"""Discover new Wiki list pages through bounded category continuations.

Category membership proposes sources; only P117's frozen-text table parser and
candidate compiler may admit a task. A completed intake replays without HTTP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import HttpError, SnapshotError, WikiHttpFetcher
from scripts.p117_wiki_shape_intake import _fetch_group, _triage, dump, pin, sha
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p119-wiki-structural-discovery.v1"
TITLE = re.compile(r"^List of [A-Za-z].{5,180}$")


def validate(config: dict[str, Any]) -> None:
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("wrong P119 config schema")
    roots = config.get("roots")
    if not isinstance(roots, list) or not 1 <= len(roots) <= 12:
        raise ValueError("roots must contain 1..12 category taxonomies")
    if len({item.get("category") for item in roots if isinstance(item, dict)}) != len(
        roots
    ):
        raise ValueError("category taxonomy repeats")
    for item in roots:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("category"), str)
            or not item["category"].startswith("Category:Lists of ")
            or not isinstance(item.get("domain"), str)
            or not re.fullmatch(r"[a-z_]{3,30}", item["domain"])
            or item.get("split") not in ("train", "eval")
        ):
            raise ValueError("invalid root category/domain/split")
    for key, maximum in (
        ("workers", 4),
        ("subcategories_per_root", 12),
        ("pages_per_category", 100),
        ("max_pages_per_root", 40),
        ("max_options_per_table", 16),
    ):
        value = config.get(key)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{key} must be 1..{maximum}")
    pools = config.get("prior_pools")
    if not isinstance(pools, list) or not pools:
        raise ValueError("pinned prior pools required")
    for value in pools:
        pin(value)


def category_page(
    fetcher: WikiHttpFetcher, category: str, kind: str, limit: int
) -> tuple[list[str], list[dict[str, Any]]]:
    """Read at most two API pages, retaining raw request/response bytes."""
    if kind not in ("page", "subcat"):
        raise ValueError("invalid category member type")
    titles: list[str] = []
    raw: list[dict[str, Any]] = []
    continuation = None
    while len(titles) < limit and len(raw) < 2:
        params: dict[str, Any] = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "list": "categorymembers",
            "cmtitle": category,
            "cmtype": kind,
            "cmlimit": min(50, limit - len(titles)),
        }
        if continuation is not None:
            params["cmcontinue"] = continuation
        payload = fetcher.get_json(params)
        rows = payload.get("query", {}).get("categorymembers")
        if not isinstance(rows, list):
            raise SnapshotError("categorymembers response lacks list")
        raw.append({"request": params, "response": payload})
        for row in rows:
            title = row.get("title") if isinstance(row, dict) else None
            if isinstance(title, str) and title not in titles:
                titles.append(title)
        next_token = payload.get("continue", {}).get("cmcontinue")
        if not next_token or next_token == continuation or not rows:
            break
        continuation = next_token
    return titles[:limit], raw


def _prior(
    config: dict[str, Any],
) -> tuple[dict, set[str], set[str], set[str], set[str]]:
    pools = [json.loads(pin(value).read_text()) for value in config["prior_pools"]]
    old_titles: set[str] = set()
    old_urls: set[str] = set()
    old_pages: set[str] = set()
    old_bodies: set[str] = set()
    for pool in pools:
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError("prior pool schema differs")
        for source in pool["sources"]:
            snapshot = _snapshot(ROOT, source["snapshot"])
            for doc in snapshot["documents"]:
                old_titles.add(doc["title"].casefold())
                old_urls.add(doc["revision_url"])
                old_pages.add(doc["page_url"])
                old_bodies.add(hashlib.sha256(doc["text"].encode()).hexdigest())
    return pools[0], old_titles, old_urls, old_pages, old_bodies


def _output(path: Path) -> Path:
    path = (ROOT / path).absolute()
    if ".." in path.parts:
        raise ValueError("output path cannot traverse parents")
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) and not resolved.is_relative_to(
        (ROOT / "data").resolve()
    ):
        raise ValueError("output escapes workspace storage")
    return path


def _discovery(
    config: dict[str, Any], output: Path, *, offline: bool
) -> tuple[list[dict], list[dict]]:
    fetcher = WikiHttpFetcher(config.get("api", "https://en.wikipedia.org/w/api.php"))
    route: list[dict] = []
    selected: list[dict] = []
    for root in config["roots"]:
        root_category = root["category"]
        categories = [root_category]
        key = hashlib.sha256(root_category.encode()).hexdigest()[:12]
        category_jobs = [
            (root_category, "subcat", config["subcategories_per_root"]),
            (root_category, "page", config["pages_per_category"]),
        ]
        for index, (category, kind, limit) in enumerate(category_jobs):
            path = output / "discovery" / f"{key}-{index:02d}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                titles = _parse_raw(raw, category, kind, limit)
            elif offline:
                raise ValueError(f"missing frozen category response: {path}")
            else:
                try:
                    titles, raw = category_page(fetcher, category, kind, limit)
                except (HttpError, SnapshotError) as error:
                    route.append(
                        {
                            "root": root_category,
                            "category": category,
                            "kind": kind,
                            "status": "discovery_failed",
                            "reason": f"{type(error).__name__}:{error}",
                        }
                    )
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(dump(raw))
            route.append(
                {
                    "root": root_category,
                    "category": category,
                    "kind": kind,
                    "raw_response": {
                        "path": str(path.relative_to(ROOT)),
                        "sha256": sha(path),
                    },
                    "returned_titles": len(titles),
                    "status": "discovered",
                }
            )
            if kind == "subcat":
                categories.extend(
                    title for title in titles if title.startswith("Category:Lists of ")
                )
            else:
                selected.extend(
                    {"root": root, "category": category, "title": title}
                    for title in titles
                    if TITLE.fullmatch(title)
                )
        for offset, category in enumerate(categories[1:], 2):
            path = output / "discovery" / f"{key}-{offset:02d}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                titles = _parse_raw(raw, category, "page", config["pages_per_category"])
            elif offline:
                raise ValueError(f"missing frozen category response: {path}")
            else:
                try:
                    titles, raw = category_page(
                        fetcher, category, "page", config["pages_per_category"]
                    )
                except (HttpError, SnapshotError) as error:
                    route.append(
                        {
                            "root": root_category,
                            "category": category,
                            "kind": "page",
                            "status": "discovery_failed",
                            "reason": f"{type(error).__name__}:{error}",
                        }
                    )
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(dump(raw))
            route.append(
                {
                    "root": root_category,
                    "category": category,
                    "kind": "page",
                    "raw_response": {
                        "path": str(path.relative_to(ROOT)),
                        "sha256": sha(path),
                    },
                    "returned_titles": len(titles),
                    "status": "discovered",
                }
            )
            selected.extend(
                {"root": root, "category": category, "title": title}
                for title in titles
                if TITLE.fullmatch(title)
            )
    return route, selected


def _parse_raw(raw: list[dict], category: str, kind: str, limit: int) -> list[str]:
    titles: list[str] = []
    for entry in raw:
        request = entry["request"]
        if request["cmtitle"] != category or request["cmtype"] != kind:
            raise ValueError("frozen category response request changed")
        rows = entry["response"].get("query", {}).get("categorymembers")
        if not isinstance(rows, list):
            raise TypeError("frozen category response malformed")
        for row in rows:
            title = row.get("title") if isinstance(row, dict) else None
            if isinstance(title, str) and title not in titles:
                titles.append(title)
    return titles[:limit]


def run(
    config_path: Path,
    output: Path,
    *,
    verify_only: bool = False,
    offline_build: bool = False,
) -> dict:
    if verify_only and offline_build:
        raise ValueError("verify-only and offline-build are exclusive")
    config_path = _output(config_path)
    output = _output(output)
    config = json.loads(config_path.read_text())
    validate(config)
    manifest_path = output / "manifest.json"
    if verify_only and not manifest_path.exists():
        raise ValueError("completed P119 manifest required for replay")
    if not verify_only and manifest_path.exists():
        raise ValueError("completed P119 intake cannot be overwritten")
    base_pool, prior_titles, prior_urls, prior_pages, prior_bodies = _prior(config)
    output.mkdir(parents=True, exist_ok=True)
    route, discovered = _discovery(config, output, offline=verify_only or offline_build)
    if any(row["status"] == "discovery_failed" for row in route):
        raise ValueError(
            "category discovery incomplete; retry the same output directory"
        )
    used = set(prior_titles)
    selected: list[dict] = []
    skipped = Counter()
    for root in config["roots"]:
        candidates = [row for row in discovered if row["root"] == root]
        for row in candidates:
            title = row["title"]
            if title.casefold() in used:
                skipped["prior_or_duplicate_title"] += 1
                continue
            if (
                sum(item["root"] == root for item in selected)
                >= config["max_pages_per_root"]
            ):
                skipped["root_page_cap"] += 1
                continue
            used.add(title.casefold())
            selected.append(row)
    jobs = []
    for row in selected:
        digest = hashlib.sha256(row["title"].casefold().encode()).hexdigest()[:16]
        name = f"p119_wiki_{digest}"
        relative = f"{output.relative_to(ROOT)}/snapshots/{name}.json"
        jobs.append(
            {
                "source_group": name,
                "snapshot_path": relative,
                "titles": [row["title"]],
                "api": config.get("api", "https://en.wikipedia.org/w/api.php"),
            }
        )
    if not verify_only and not offline_build:
        pending = [job for job in jobs if not (ROOT / job["snapshot_path"]).exists()]
        with ProcessPoolExecutor(max_workers=1) as executor:
            frozen = list(executor.map(_fetch_group, pending))
        (output / "freeze_ledger.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in frozen
            )
        )
    snapshots = []
    source_ledger = []
    seen_titles = set(prior_titles)
    seen_urls = set(prior_urls)
    seen_pages = set(prior_pages)
    seen_bodies = set(prior_bodies)
    for row, job in zip(selected, jobs, strict=True):
        path = ROOT / job["snapshot_path"]
        if not path.exists():
            source_ledger.append(
                {
                    "requested_title": row["title"],
                    "source_group": job["source_group"],
                    "status": "unfrozen",
                    "reason": "freeze_missing",
                }
            )
            continue
        source = {
            "name": job["source_group"],
            "domain": row["root"]["domain"],
            "topic": re.sub(
                r"[^a-z0-9]+", "_", row["category"].removeprefix("Category:").lower()
            ).strip("_"),
            "split": row["root"]["split"],
            "snapshot": {"path": job["snapshot_path"], "sha256": sha(path)},
        }
        snapshot = _snapshot(ROOT, source["snapshot"])
        if len(snapshot["documents"]) != 1:
            raise ValueError("single-page frozen group changed")
        doc = snapshot["documents"][0]
        body = hashlib.sha256(doc["text"].encode()).hexdigest()
        if doc["title"].casefold() != row["title"].casefold():
            reason = "canonical_title_redirect"
        elif doc["title"].casefold() in seen_titles:
            reason = "prior_or_duplicate_canonical_title"
        elif doc["page_url"] in seen_pages:
            reason = "prior_or_duplicate_page_url"
        elif doc["revision_url"] in seen_urls:
            reason = "prior_or_duplicate_revision_url"
        elif body in seen_bodies:
            reason = "prior_or_duplicate_body_sha256"
        else:
            reason = None
            seen_titles.add(doc["title"].casefold())
            seen_pages.add(doc["page_url"])
            seen_urls.add(doc["revision_url"])
            seen_bodies.add(body)
            snapshots.append(source)
        source_ledger.append(
            {
                "requested_title": row["title"],
                "source_group": job["source_group"],
                "canonical_title": doc["title"],
                "page_url": doc["page_url"],
                "revision_url": doc["revision_url"],
                "body_sha256": body,
                "snapshot_sha256": source["snapshot"]["sha256"],
                "status": "admitted_source" if reason is None else "excluded",
                "reason": reason,
            }
        )
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        scans = list(
            executor.map(
                _triage,
                snapshots,
                [config["max_options_per_table"]] * len(snapshots),
                [True] * len(snapshots),
            )
        )
    ledger = [row for _, rows in scans for row in rows]
    pool = {**base_pool, "sources": snapshots}
    files = {
        "source_pool.json": dump(pool),
        "source_ledger.jsonl": "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in source_ledger
        ).encode(),
        "shape_ledger.jsonl": "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger
        ).encode(),
    }
    p117_code = ROOT / "scripts/p117_wiki_shape_intake.py"
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": sha(config_path),
        "p119_code_sha256": sha(Path(__file__)),
        "code_sha256": sha(p117_code),
        "input_pools": config["prior_pools"],
        "discovery": route,
        "selected_titles": [
            {
                "title": row["title"],
                "category": row["category"],
                "domain": row["root"]["domain"],
                "split": row["root"]["split"],
            }
            for row in selected
        ],
        "skipped_discovery": dict(skipped),
        "gross_discovered_title_rows": len(discovered),
        "selected_novel_titles": len(selected),
        "frozen_source_observations": sum(
            row["status"] != "unfrozen" for row in source_ledger
        ),
        "source_exclusions": dict(
            Counter(
                row["reason"]
                for row in source_ledger
                if row["status"] != "admitted_source"
            )
        ),
        "new_frozen_groups": len(snapshots),
        "all_frozen_groups": len(snapshots),
        "gross_frozen_groups": len(snapshots),
        "gross_frozen_pages": len(ledger),
        "legal_l2_year_cells": sum(row["year_cells"] for row in ledger),
        "legal_l2_categorical_cells": sum(row["categorical_cells"] for row in ledger),
        "positive_groups": len(
            {
                row["source_group"]
                for row in ledger
                if row["status"] == "legal_table_cell"
            }
        ),
        "domain_counts": dict(Counter(source["domain"] for source in snapshots)),
        "topic_counts": dict(Counter(source["topic"] for source in snapshots)),
        "source_pool_sha256": hashlib.sha256(files["source_pool.json"]).hexdigest(),
        "source_ledger_sha256": hashlib.sha256(
            files["source_ledger.jsonl"]
        ).hexdigest(),
        "shape_ledger_sha256": hashlib.sha256(files["shape_ledger.jsonl"]).hexdigest(),
        "train_ready": False,
    }
    files["manifest.json"] = dump(result)
    if verify_only:
        stored = json.loads(manifest_path.read_text())
        if stored != result:
            raise ValueError("P119 manifest replay differs")
        for name, content in files.items():
            if (output / name).read_bytes() != content:
                raise ValueError(f"P119 replay differs: {name}")
    else:
        for name, content in files.items():
            (output / name).write_bytes(content)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--offline-build", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.config,
                args.output,
                verify_only=args.verify_only,
                offline_build=args.offline_build,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
