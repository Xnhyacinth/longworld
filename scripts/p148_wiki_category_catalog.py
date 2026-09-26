"""Route official MediaWiki list categories into frozen P119/P122/P126 batches.

Category names are scheduling labels, never new semantic domains or task gold.
The catalog and every source request are cached for exact offline replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import HttpError, SnapshotError, WikiHttpFetcher
from scripts import p119_wiki_structural_discovery as discovery
from scripts import p122_wiki_html_grid as html_grid
from scripts import p126_wiki_html_table_tasks as table_tasks
from scripts.freeze_wiki_title_bundle import freeze_titles

SCHEMA = "longworld.p148-wiki-category-catalog.v1"
ROOT_PATTERN = re.compile(r"^Category:Lists of [A-Za-z].{2,160}$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file(value: dict[str, str]) -> Path:
    if set(value) != {"path", "sha256"}:
        raise ValueError("source pin needs path and SHA")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin must be workspace relative")
    path = ROOT / relative
    if _sha(path) != value["sha256"]:
        raise ValueError(f"source pin differs: {relative}")
    return path


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("api") != "https://en.wikipedia.org/w/api.php"
        or config.get("index_category") != "Category:Lists"
        or config.get("index_subcategory_limit") != 50
        or not 1 <= config.get("branch_subcategory_limit", 0) <= 100
        or not 1 <= config.get("minimum_direct_pages", 0) <= 50
        or not 8 <= config.get("scheduled_roots", 0) <= 12
        or not 1 <= config.get("pages_per_root", 0) <= 2
        or not 1 <= config.get("p119_subcategories_per_root", 0) <= 6
        or not 1 <= config.get("p119_pages_per_category", 0) <= 35
        or not 0 < config.get("requests_per_second", 0) <= 0.5
        or not 0 <= config.get("max_429_retries", -1) <= 2
        or not isinstance(config.get("prior_pools"), list)
        or len(config["prior_pools"]) != 4
    ):
        raise ValueError("invalid P148 bounded catalog config")
    _file(config["prior_roots_config"])
    for pin in config["prior_pools"]:
        _file(pin)
    return config


class PoliteFetcher(WikiHttpFetcher):
    """Global 0.5 req/s limit and bounded 429 retry for all source requests."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config["api"])
        self.interval = 1 / config["requests_per_second"]
        self.retries = config["max_429_retries"]
        self.last_attempt: float | None = None
        self.attempts = 0

    def get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        for retry in range(self.retries + 1):
            if self.last_attempt is not None:
                time.sleep(
                    max(0, self.interval - (time.monotonic() - self.last_attempt))
                )
            self.last_attempt = time.monotonic()
            self.attempts += 1
            try:
                return super().get_json(params)
            except HttpError as error:
                cause = error.__cause__
                limited = getattr(cause, "code", None) == 429 or any(
                    token in str(error).lower()
                    for token in ("429", "ratelimit", "maxlag")
                )
                if not limited or retry == self.retries:
                    raise
                time.sleep(self.interval * (retry + 1))
        raise AssertionError("bounded retry loop exhausted")


def _raw_category(
    path: Path,
    fetcher: PoliteFetcher | None,
    category: str,
    kind: str,
    limit: int,
) -> tuple[list[str], str]:
    if path.exists():
        raw = json.loads(path.read_text())
        titles = discovery._parse_raw(raw, category, kind, limit)
    elif fetcher is not None:
        titles, raw = discovery.category_page(fetcher, category, kind, limit)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(raw) + "\n")
    else:
        raise ValueError(f"missing frozen MediaWiki category response: {path}")
    return titles, _sha(path)


def _raw_info(
    path: Path, fetcher: PoliteFetcher | None, titles: list[str]
) -> tuple[dict[str, int], str]:
    request = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "prop": "categoryinfo",
        "titles": "|".join(titles),
    }
    if path.exists():
        raw = json.loads(path.read_text())
    elif fetcher is not None:
        raw = {"request": request, "response": fetcher.get_json(request)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(raw) + "\n")
    else:
        raise ValueError(f"missing frozen MediaWiki category info: {path}")
    if raw.get("request") != request:
        raise ValueError("frozen category-info request changed")
    pages = raw.get("response", {}).get("query", {}).get("pages")
    if not isinstance(pages, list):
        raise TypeError("category-info response lacks pages")
    counts = {}
    for item in pages:
        title = item.get("title") if isinstance(item, dict) else None
        info = item.get("categoryinfo", {}) if isinstance(item, dict) else {}
        if title in titles and type(info.get("pages")) is int:
            counts[title] = info["pages"]
    return counts, _sha(path)


def schedule(
    candidates: dict[str, tuple[str, int]], prior: set[str], count: int
) -> tuple[list[dict[str, str]], Counter[str]]:
    """Pick one high-page-count category per discovered parent before repeats."""
    exclusions: Counter[str] = Counter()
    by_parent: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for title, (parent, pages) in candidates.items():
        if not ROOT_PATTERN.fullmatch(title) or title.casefold().endswith(" lists"):
            exclusions["not_direct_list_root"] += 1
        elif title.casefold() in prior:
            exclusions["prior_root"] += 1
        else:
            by_parent[parent].append((title, pages))
    for rows in by_parent.values():
        rows.sort(key=lambda item: (-item[1], item[0]))
    parents = sorted(
        by_parent, key=lambda value: hashlib.sha256(value.encode()).hexdigest()
    )
    chosen = []
    while len(chosen) < count and any(by_parent.values()):
        for parent in parents:
            if by_parent[parent]:
                title, pages = by_parent[parent].pop(0)
                chosen.append((title, parent, pages))
                if len(chosen) == count:
                    break
    if len(chosen) < count:
        raise ValueError("official category index lacks enough legal roots")
    result = [
        {
            "category": title,
            "parent": parent,
            "direct_pages": pages,
            "domain": "wiki_list_index",
            "split": "eval" if rank % 4 == 3 else "train",
        }
        for rank, (title, parent, pages) in enumerate(chosen)
    ]
    return result, exclusions


def _catalog(config_path: Path, output: Path, *, fetch: bool) -> dict[str, Any]:
    config = _config(config_path)
    fetcher = PoliteFetcher(config) if fetch else None
    raw_dir = output / "catalog" / "raw"
    receipts: dict[str, str] = {}
    top_path = raw_dir / "index.json"
    branches, receipts[str(top_path.relative_to(output))] = _raw_category(
        top_path,
        fetcher,
        config["index_category"],
        "subcat",
        config["index_subcategory_limit"],
    )
    found: dict[str, str] = {}
    for branch in branches:
        if not branch.startswith("Category:"):
            continue
        if ROOT_PATTERN.fullmatch(branch):
            found.setdefault(branch, config["index_category"])
        key = hashlib.sha256(branch.encode()).hexdigest()[:16]
        path = raw_dir / f"branch-{key}.json"
        children, receipts[str(path.relative_to(output))] = _raw_category(
            path, fetcher, branch, "subcat", config["branch_subcategory_limit"]
        )
        for child in children:
            if ROOT_PATTERN.fullmatch(child):
                found.setdefault(child, branch)
    info = {}
    titles = sorted(found)
    for offset in range(0, len(titles), 50):
        group = titles[offset : offset + 50]
        path = raw_dir / f"info-{offset // 50:03d}.json"
        values, receipts[str(path.relative_to(output))] = _raw_info(
            path, fetcher, group
        )
        info.update(values)
    eligible = {
        title: (parent, info[title])
        for title, parent in found.items()
        if info.get(title, 0) >= config["minimum_direct_pages"]
    }
    prior_config = json.loads(_file(config["prior_roots_config"]).read_text())
    prior = {row["category"].casefold() for row in prior_config["roots"]}
    roots, rejected = schedule(eligible, prior, config["scheduled_roots"])
    p119_config = {
        "schema": discovery.SCHEMA + ".config",
        "api": config["api"],
        "workers": 2,
        "subcategories_per_root": config["p119_subcategories_per_root"],
        "pages_per_category": config["p119_pages_per_category"],
        "max_pages_per_root": config["pages_per_root"],
        "max_options_per_table": 4,
        "prior_pools": config["prior_pools"],
        "roots": [
            {key: row[key] for key in ("category", "domain", "split")} for row in roots
        ],
    }
    discovery.validate(p119_config)
    output.mkdir(parents=True, exist_ok=True)
    config_bytes = (_dump(p119_config) + "\n").encode()
    generated_path = output / "p119_config.json"
    if generated_path.exists() and generated_path.read_bytes() != config_bytes:
        raise ValueError("frozen P119 scheduled config changed")
    if not generated_path.exists():
        generated_path.write_bytes(config_bytes)
    result = {
        "schema": SCHEMA + ".catalog",
        "config_sha256": _sha(config_path),
        "compiler_sha256": _sha(Path(__file__)),
        "index_category": config["index_category"],
        "index_branches": len(branches),
        "gross_list_root_candidates": len(found),
        "minimum_direct_pages": config["minimum_direct_pages"],
        "eligible_roots": len(eligible),
        "scheduled_roots": roots,
        "schedule_exclusions": dict(sorted(rejected.items())),
        "prior_roots_config_sha256": config["prior_roots_config"]["sha256"],
        "p119_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "raw_response_sha256": dict(sorted(receipts.items())),
        "network_attempts_this_run": fetcher.attempts if fetcher else 0,
        "train_ready": False,
    }
    path = output / "catalog_manifest.json"
    if path.exists():
        previous = json.loads(path.read_text())
        comparable = {
            key: value
            for key, value in result.items()
            if key != "network_attempts_this_run"
        }
        prior_comparable = {
            key: value
            for key, value in previous.items()
            if key != "network_attempts_this_run"
        }
        if comparable != prior_comparable:
            raise ValueError("frozen category catalog differs")
        return previous
    if not fetch:
        raise ValueError("completed category catalog absent")
    path.write_text(_dump(result) + "\n")
    return result


def _selected(config: dict[str, Any], output: Path) -> list[dict[str, Any]]:
    p119_config = json.loads((output / "p119_config.json").read_text())
    _, discovered = discovery._discovery(p119_config, output / "p119", offline=True)
    _, prior_titles, _, _, _ = discovery._prior(p119_config)
    used = set(prior_titles)
    selected = []
    for root in p119_config["roots"]:
        per_root = 0
        for row in discovered:
            if row["root"] != root or row["title"].casefold() in used:
                continue
            if per_root >= config["pages_per_root"]:
                break
            used.add(row["title"].casefold())
            selected.append(row)
            per_root += 1
    return selected


def acquire(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = _config(config_path)
    _catalog(config_path, output, fetch=False)
    p119_dir = output / "p119"
    p119_config_path = output / "p119_config.json"
    p119_config = json.loads(p119_config_path.read_text())
    fetcher = None if verify_only else PoliteFetcher(config)
    for root in p119_config["roots"]:
        category = root["category"]
        key = hashlib.sha256(category.encode()).hexdigest()[:12]
        subcat, _ = _raw_category(
            p119_dir / "discovery" / f"{key}-00.json",
            fetcher,
            category,
            "subcat",
            config["p119_subcategories_per_root"],
        )
        _raw_category(
            p119_dir / "discovery" / f"{key}-01.json",
            fetcher,
            category,
            "page",
            config["p119_pages_per_category"],
        )
        for offset, child in enumerate(
            (value for value in subcat if value.startswith("Category:Lists of ")), 2
        ):
            _raw_category(
                p119_dir / "discovery" / f"{key}-{offset:02d}.json",
                fetcher,
                child,
                "page",
                config["p119_pages_per_category"],
            )
    selected = _selected(config, output)
    freeze_rows = []
    for row in selected:
        title = row["title"]
        name = "p119_wiki_" + hashlib.sha256(title.casefold().encode()).hexdigest()[:16]
        snapshot = p119_dir / "snapshots" / f"{name}.json"
        if snapshot.exists():
            status, reason = "cached", None
        elif verify_only:
            status, reason = "unfrozen", "snapshot_missing"
        else:
            try:
                freeze_titles(fetcher, [title], name, snapshot)
            except (HttpError, SnapshotError, OSError, ValueError) as error:
                status, reason = "freeze_failed", f"{type(error).__name__}:{error}"
            else:
                status, reason = "frozen", None
        freeze_rows.append(
            {
                "title": title,
                "root": row["root"]["category"],
                "source_group": name,
                "status": status,
                "reason": reason,
                "snapshot_sha256": _sha(snapshot) if snapshot.exists() else None,
                "freeze_log_sha256": _sha(snapshot.with_suffix(".freeze-log.json"))
                if snapshot.with_suffix(".freeze-log.json").exists()
                else None,
            }
        )
    ledger_path = output / "fetch_ledger.jsonl"
    if not verify_only:
        ledger_path.write_text("".join(_dump(row) + "\n" for row in freeze_rows))
    elif not ledger_path.exists():
        raise ValueError("frozen fetch ledger missing")
    result = discovery.run(
        p119_config_path,
        p119_dir,
        verify_only=verify_only,
        offline_build=not verify_only,
    )
    return {
        "selected_titles": len(selected),
        "frozen_source_groups": result["new_frozen_groups"],
        "p119_manifest_sha256": _sha(p119_dir / "manifest.json"),
        "http_attempts_this_run": fetcher.attempts if fetcher else 0,
        "fetch_statuses": dict(
            sorted(Counter(row["status"] for row in freeze_rows).items())
        ),
    }


def compile_tasks(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = _config(config_path)
    p119_dir = output / "p119"
    p119_manifest = json.loads((p119_dir / "manifest.json").read_text())
    pool = json.loads((p119_dir / "source_pool.json").read_text())
    if not pool["sources"]:
        raise ValueError("no frozen Wiki source groups for HTML task recipe")
    titles = []
    for source in pool["sources"]:
        snapshot = json.loads(_file(source["snapshot"]).read_text())
        titles.append(snapshot["documents"][0]["title"])
    if len(titles) > 24 or len(set(titles)) != len(titles):
        raise ValueError("source title count/identity exceeds P122 recipe")
    source_config = {
        "schema": html_grid.SCHEMA + ".config",
        "api": config["api"],
        "requests_per_second": config["requests_per_second"],
        "max_html_bytes_per_page": 2_000_000,
        "source_manifest": {
            "path": str((p119_dir / "manifest.json").relative_to(ROOT)),
            "sha256": _sha(p119_dir / "manifest.json"),
        },
        "source_pool": {
            "path": str((p119_dir / "source_pool.json").relative_to(ROOT)),
            "sha256": _sha(p119_dir / "source_pool.json"),
        },
        "source_ledger": {
            "path": str((p119_dir / "source_ledger.jsonl").relative_to(ROOT)),
            "sha256": _sha(p119_dir / "source_ledger.jsonl"),
        },
        "titles": titles,
    }
    if source_config["source_pool"]["sha256"] != p119_manifest["source_pool_sha256"]:
        raise ValueError("P119 source pool pin differs")
    source_config_path = output / "p122_source_config.json"
    source_bytes = (_dump(source_config) + "\n").encode()
    if source_config_path.exists() and source_config_path.read_bytes() != source_bytes:
        raise ValueError("P122 source config drift")
    if not source_config_path.exists():
        source_config_path.write_bytes(source_bytes)
    source_dir, grid_dir, task_dir = (
        output / "p122_source",
        output / "p122_grid",
        output / "p126_tasks",
    )
    source = html_grid.freeze(source_config_path, source_dir, verify_only=verify_only)
    grid = html_grid.compile_grids(source_dir, grid_dir, verify_only=verify_only)
    task_config = {
        "schema": table_tasks.SCHEMA + ".config",
        "source_manifest": {
            "path": str((source_dir / "source_manifest.json").relative_to(ROOT)),
            "sha256": _sha(source_dir / "source_manifest.json"),
        },
        "source_pool": source_config["source_pool"],
        "grid_manifest": {
            "path": str((grid_dir / "manifest.json").relative_to(ROOT)),
            "sha256": _sha(grid_dir / "manifest.json"),
        },
        "max_tasks_per_table": 2,
        "max_tasks_per_page": 3,
        "max_seq_len": 131072,
    }
    task_config_path = output / "p126_task_config.json"
    task_bytes = (_dump(task_config) + "\n").encode()
    if task_config_path.exists() and task_config_path.read_bytes() != task_bytes:
        raise ValueError("P126 task config drift")
    if not task_config_path.exists():
        task_config_path.write_bytes(task_bytes)
    tasks = table_tasks.compile(task_config_path, task_dir, verify_only=verify_only)
    result = {
        "schema": SCHEMA + ".result",
        "train_ready": False,
        "config_sha256": _sha(config_path),
        "compiler_sha256": _sha(Path(__file__)),
        "catalog_manifest_sha256": _sha(output / "catalog_manifest.json"),
        "p119_manifest_sha256": _sha(p119_dir / "manifest.json"),
        "p122_source_manifest_sha256": _sha(source_dir / "source_manifest.json"),
        "p122_grid_manifest_sha256": _sha(grid_dir / "manifest.json"),
        "p126_task_manifest_sha256": _sha(task_dir / "manifest.json"),
        "gross_selected_roots": len(
            json.loads((output / "catalog_manifest.json").read_text())[
                "scheduled_roots"
            ]
        ),
        "frozen_source_groups": len(pool["sources"]),
        "gross_wikitables": grid["gross_wikitables"],
        "valid_grids": grid["grid_valid_tables"],
        "candidate_views": tasks["candidate_views"],
        "task_source_groups": tasks["source_pages_with_tasks"],
        "task_length_bins": tasks["length_bins"],
        "task_rejections": tasks["rejection_reasons"],
        "claim_limit": "official category labels route real pages; only P126 HTML table tasks admitted; no long-dependency or new-domain claim",
    }
    manifest_path = output / "manifest.json"
    if verify_only:
        if json.loads(manifest_path.read_text()) != result:
            raise ValueError("P148 compiled manifest byte replay differs")
    else:
        if manifest_path.exists():
            raise ValueError("P148 compiled manifest already exists")
        manifest_path.write_text(_dump(result) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/p148_wiki_category_catalog_v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/candidates/p148_wiki_category_catalog_v1",
    )
    parser.add_argument(
        "--phase", choices=("catalog", "acquire", "compile", "verify"), required=True
    )
    args = parser.parse_args()
    output = args.output.absolute()
    if not output.resolve().is_relative_to((ROOT / "data" / "candidates").resolve()):
        raise ValueError("P148 output must be under workspace candidate storage")
    if args.phase == "catalog":
        result = _catalog(args.config, output, fetch=True)
    elif args.phase == "acquire":
        result = acquire(args.config, output)
    elif args.phase == "compile":
        result = compile_tasks(args.config, output)
    else:
        _catalog(args.config, output, fetch=False)
        acquire(args.config, output, verify_only=True)
        result = compile_tasks(args.config, output, verify_only=True)
    print(_dump(result))


if __name__ == "__main__":
    main()
