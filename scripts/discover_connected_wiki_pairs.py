"""Discover complementary Wiki tables from an existing, pinned source pool.

An anchor table supplies a unique row selector. Searches use its *actual row
names* to find a second list, rather than adding hand-written per-world tasks.
Only a frozen two-page snapshot that passes the native join compiler counts as
a strict join source. Existing pages may serve as same-split anchors, but new
pages must be absent from the source pool and prior source manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_adapter, wiki_row_binding, wiki_world_bridge
from scripts.freeze_wiki_title_bundle import freeze_titles, resolve_titles
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.connected-wiki-intake.v1"
API = "https://en.wikipedia.org/w/api.php"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _anchor_rows(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    return {
        doc["title"]: sorted({row.name.value for row in rows})
        for doc in snapshot["documents"]
        if (rows := wiki_row_binding._rows(doc["text"]))
    }


def _spread(names: list[str], limit: int) -> list[str]:
    if limit == 1:
        return [names[len(names) // 2]] if names else []
    if len(names) <= limit:
        return names
    return [names[index * (len(names) - 1) // (limit - 1)] for index in range(limit)]


def _preview_score(text: str, anchor_names: set[str]) -> tuple[int, int]:
    """Cheap source routing only; the frozen native compiler is authoritative."""
    rows = wiki_row_binding._rows(text)
    useful = {
        row.name.value
        for row in rows
        if any(
            cell != row.name
            and not wiki_row_binding._BAD_COLUMN.search(column)
            and wiki_row_binding._value(cell.value) is not None
            for column, cell in row.columns
        )
    }
    return len({name for name in useful if name.casefold() in anchor_names}), len(
        useful
    )


def _auto_seeds(config: dict[str, Any], pool: dict[str, Any]) -> list[dict[str, Any]]:
    auto = config.get("auto_sources")
    if auto is None:
        return config.get("seeds", [])
    domains = auto["domains"]
    selected = []
    for domain in domains:
        candidates = []
        for source in pool["sources"]:
            if source["domain"] != domain:
                continue
            rows = _anchor_rows(_snapshot(ROOT, source["snapshot"]))
            candidates.append((sum(map(len, rows.values())), source["name"]))
        if candidates:
            count, name = max(candidates)
            if count:
                selected.append(name)
        if len(selected) >= auto["max_sources"]:
            break
    return [
        {
            "name": "auto_" + hashlib.sha256(name.encode()).hexdigest()[:12],
            "anchor_source": name,
            **{
                key: auto[key]
                for key in (
                    "max_queries",
                    "results_per_query",
                    "max_previews",
                    "max_freezes",
                )
            },
        }
        for name in selected
    ]


def _query_specs(topic: str, names: list[str], limit: int) -> list[tuple[str, str]]:
    words = [
        word
        for word in topic.split("_")
        if word not in {"lists", "expansion", "broad"} and not word.isdigit()
    ]
    queries = [("topic", f'intitle:"List of" {" ".join(words)}')]
    queries.extend(
        (name, f'intitle:"List of" "{name}"')
        for name in _spread(names, max(1, limit - 1))
    )
    return queries[:limit]


def _prior_unified_titles(pin: dict[str, str]) -> dict[str, set[str]]:
    """Read every pinned Wiki lane in a prior unified bank for split safety."""
    batch = Path(pin["path"])
    if not batch.is_absolute():
        batch = ROOT / batch
    manifest_path = batch / "manifest.json"
    if _sha(manifest_path) != pin["manifest_sha256"]:
        raise ValueError("prior unified batch manifest pin mismatch")
    manifest = json.loads(manifest_path.read_text())
    plan_path = batch / "plan.json"
    if manifest.get("plan_sha256") != _sha(plan_path):
        raise ValueError("prior unified batch plan changed")
    assignments: dict[str, set[str]] = {}
    for entry in json.loads(plan_path.read_text())["sources"]:
        kind = entry["kind"]
        if not kind.startswith("wiki_"):
            continue
        config_path = Path(entry["config"])
        if not config_path.is_absolute():
            config_path = ROOT / config_path
        if _sha(config_path) != entry["config_sha256"]:
            raise ValueError("prior unified Wiki lane config changed")
        if kind in {"wiki_source_pool", "wiki_row_join_probe"}:
            sources = json.loads(config_path.read_text())["sources"]
        elif kind == "wiki_candidate_delta":
            native = json.loads(config_path.read_text())["native_pool"]
            native_path = Path(native)
            if not native_path.is_absolute():
                native_path = ROOT / native_path
            sources = json.loads((native_path / "job_config.json").read_text())["jobs"]
        else:
            raise ValueError(f"unsupported prior unified Wiki lane: {kind}")
        for source in sources:
            snapshot = _snapshot(ROOT, source["snapshot"])
            for doc in snapshot["documents"]:
                assignments.setdefault(doc["title"].casefold(), set()).add(
                    source["split"]
                )
    return assignments


def _existing_titles(
    pool: dict[str, Any], prior_unified: dict[str, str] | None = None
) -> dict[str, set[str]]:
    assignments: dict[str, set[str]] = {}
    for source in pool["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        for doc in snapshot["documents"]:
            key = doc["title"].casefold()
            assignments.setdefault(key, set()).add(source["split"])
    manifest_pin = pool["prior_source_manifest"]
    manifest_path = Path(manifest_pin["path"])
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    if _sha(manifest_path) != manifest_pin["sha256"]:
        raise ValueError("prior source manifest pin mismatch")
    for line in manifest_path.read_text().splitlines():
        row = json.loads(line)
        for title in row["revisions"]:
            key = title.casefold()
            assignments.setdefault(key, set()).add(row["split"])
    if prior_unified is not None:
        for title, splits in _prior_unified_titles(prior_unified).items():
            assignments.setdefault(title, set()).update(splits)
    return assignments


def _validate(config: dict[str, Any], pool: dict[str, Any]) -> None:
    if (
        config.get("schema") != SCHEMA
        or pool.get("schema") != "longworld.source-batch-pool.v2"
    ):
        raise ValueError("wrong connected intake or source pool schema")
    prior = config.get("prior_unified_batch")
    if prior is not None and (
        not isinstance(prior, dict)
        or set(prior) != {"path", "manifest_sha256"}
        or any(not isinstance(value, str) or not value for value in prior.values())
    ):
        raise ValueError("invalid prior unified batch pin")
    auto = config.get("auto_sources")
    if auto is not None:
        if (
            not isinstance(auto, dict)
            or not isinstance(auto.get("domains"), list)
            or not 1 <= len(auto["domains"]) <= 20
            or len(auto["domains"]) != len(set(auto["domains"]))
        ):
            raise ValueError("auto_sources domains must be 1..20 unique entries")
        if (
            type(auto.get("max_sources")) is not int
            or not 1 <= auto["max_sources"] <= 20
        ):
            raise ValueError("auto_sources max_sources must be 1..20")
        known = {source["domain"] for source in pool["sources"]}
        if any(domain not in known for domain in auto["domains"]):
            raise ValueError("auto_sources contains unknown domain")
    seeds = _auto_seeds(config, pool)
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 20:
        raise ValueError("seeds must have 1..20 entries")
    sources = {item["name"]: item for item in pool["sources"]}
    seen = set()
    for seed in seeds:
        if not isinstance(seed, dict) or not re.fullmatch(
            r"[a-z][a-z0-9_]{2,30}", seed.get("name", "")
        ):
            raise ValueError("invalid seed name")
        if seed["name"] in seen or seed.get("anchor_source") not in sources:
            raise ValueError("duplicate seed or unknown anchor source")
        seen.add(seed["name"])
        for key, maximum in (
            ("max_queries", 30),
            ("results_per_query", 20),
            ("max_previews", 30),
            ("max_freezes", 8),
        ):
            value = seed.get(key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be within 1..{maximum}")


def run(
    config_path: Path,
    output_dir: Path,
    *,
    fetcher: wiki_adapter.WikiHttpFetcher | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    pool_path = Path(config["base_pool"])
    if not pool_path.is_absolute():
        pool_path = ROOT / pool_path
    pool = json.loads(pool_path.read_text())
    if config.get("base_pool_sha256") not in (None, _sha(pool_path)):
        raise ValueError("base source pool pin mismatch")
    _validate(config, pool)
    existing = _existing_titles(pool, config.get("prior_unified_batch"))
    sources = {item["name"]: item for item in pool["sources"]}
    fetcher = fetcher or wiki_adapter.WikiHttpFetcher(API)
    output_dir.mkdir(parents=True)
    (output_dir / "snapshots").mkdir()
    (output_dir / "search").mkdir()
    seed_reports = []
    new_titles: set[str] = set()
    for seed in _auto_seeds(config, pool):
        source = sources[seed["anchor_source"]]
        anchor_snapshot = _snapshot(ROOT, source["snapshot"])
        rows = _anchor_rows(anchor_snapshot)
        report: dict[str, Any] = {
            "seed": seed["name"],
            "anchor_source": source["name"],
            "split": source["split"],
            "selectable_anchor_docs": len(rows),
            "searched_entities": [],
            "previewed": [],
            "frozen": [],
            "rejections": [],
        }
        seen_previews: set[str] = set()
        for anchor_title, names in sorted(rows.items()):
            if (
                len(report["searched_entities"]) >= seed["max_queries"]
                or len(report["previewed"]) >= seed["max_previews"]
                or len(report["frozen"]) >= seed["max_freezes"]
            ):
                break
            if existing.get(anchor_title.casefold()) != {source["split"]}:
                report["rejections"].append(
                    {"title": anchor_title, "reason": "anchor_crosses_prior_split"}
                )
                continue
            hits: Counter[str] = Counter()
            remaining_queries = seed["max_queries"] - len(report["searched_entities"])
            for entity, query in _query_specs(
                source["topic"], names, remaining_queries
            ):
                try:
                    payload = fetcher.get_json(
                        {
                            "action": "query",
                            "format": "json",
                            "formatversion": 2,
                            "list": "search",
                            "srsearch": query,
                            "srnamespace": 0,
                            "srlimit": seed["results_per_query"],
                        }
                    )
                    titles = [
                        item["title"]
                        for item in payload["query"]["search"]
                        if item["title"].startswith("List of ")
                    ]
                except (wiki_adapter.HttpError, KeyError, TypeError) as error:
                    report["rejections"].append(
                        {
                            "entity": entity,
                            "reason": f"search:{type(error).__name__}:{error}",
                        }
                    )
                    continue
                path = (
                    output_dir
                    / "search"
                    / f"{seed['name']}_{hashlib.sha256(entity.encode()).hexdigest()[:12]}.json"
                )
                _write(path, payload)
                report["searched_entities"].append(
                    {"entity": entity, "titles": titles, "response_sha256": _sha(path)}
                )
                for title in titles:
                    if (
                        title.casefold() != anchor_title.casefold()
                        and title.casefold() not in existing
                        and title.casefold() not in new_titles
                    ):
                        hits[title] += 1
            for title, hits_count in sorted(
                hits.items(), key=lambda item: (-item[1], item[0])
            ):
                if len(report["previewed"]) >= seed["max_previews"]:
                    break
                if title.casefold() in seen_previews:
                    continue
                seen_previews.add(title.casefold())
                try:
                    member = resolve_titles(fetcher, [title])[0]
                    page = fetcher.fetch_revision(member.pageid)
                    text = wiki_adapter.render_wikitext(page.title, page.wikitext).text
                    matches, alternatives = _preview_score(
                        text, {name.casefold() for name in names}
                    )
                    report["previewed"].append(
                        {
                            "title": page.title,
                            "search_hits": hits_count,
                            "matching_rows": matches,
                            "target_rows": alternatives,
                            "revision": page.revid,
                        }
                    )
                    if not matches or alternatives < 2:
                        continue
                    label = (
                        f"{seed['name']}_"
                        f"{hashlib.sha256(page.title.encode()).hexdigest()[:12]}"
                    )
                    path = output_dir / "snapshots" / f"{label}.json"
                    receipt = freeze_titles(
                        fetcher, [anchor_title, page.title], label, path
                    )
                    snapshot = json.loads(path.read_text())
                    world = wiki_world_bridge.snapshot_to_world(snapshot)
                    tasks = wiki_row_binding.build_join_tasks(world, max_tasks=32)
                    if not tasks:
                        report["rejections"].append(
                            {"title": page.title, "reason": "no_strict_join_tasks"}
                        )
                        continue
                    report["frozen"].append(
                        {
                            "title": page.title,
                            "path": str(path),
                            "sha256": _sha(path),
                            "snapshot_id": receipt["snapshot_id"],
                            "revisions": receipt["revisions"],
                            "license": receipt["license"],
                            "strict_join_tasks": len(tasks),
                            "task_ids": [task.task_id for task in tasks],
                            "source_characters": sum(
                                len(doc["text"]) for doc in snapshot["documents"]
                            ),
                        }
                    )
                    new_titles.add(page.title.casefold())
                except (
                    wiki_adapter.HttpError,
                    wiki_adapter.SnapshotError,
                    OSError,
                    ValueError,
                    KeyError,
                ) as error:
                    report["rejections"].append(
                        {
                            "title": title,
                            "reason": f"freeze_or_probe:{type(error).__name__}:{error}",
                        }
                    )
                if len(report["frozen"]) >= seed["max_freezes"]:
                    break
        seed_reports.append(report)
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "base_pool_sha256": _sha(pool_path),
        "seeds": seed_reports,
        "preexisting_cross_split_titles": sum(
            len(splits) > 1 for splits in existing.values()
        ),
        "searched_entities": sum(len(r["searched_entities"]) for r in seed_reports),
        "previewed_pages": sum(len(r["previewed"]) for r in seed_reports),
        "frozen_groups": sum(len(r["frozen"]) for r in seed_reports),
        "productive_groups": sum(
            sum(item["strict_join_tasks"] > 0 for item in r["frozen"])
            for r in seed_reports
        ),
        "strict_join_tasks": sum(
            sum(item["strict_join_tasks"] for item in r["frozen"]) for r in seed_reports
        ),
        "train_ready": False,
    }
    _write(output_dir / "acquisition_manifest.json", result)
    materialize_source_pool(config_path, output_dir)
    return result


def materialize_source_pool(config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Turn accepted, revision-pinned pairs into the ordinary source-pool input."""
    config = json.loads(config_path.read_text())
    base_path = Path(config["base_pool"])
    if not base_path.is_absolute():
        base_path = ROOT / base_path
    base = json.loads(base_path.read_text())
    manifest = json.loads((output_dir / "acquisition_manifest.json").read_text())
    if manifest["config_sha256"] != _sha(config_path) or manifest[
        "base_pool_sha256"
    ] != _sha(base_path):
        raise ValueError("connected source acquisition pin drift")
    existing = _existing_titles(base, config.get("prior_unified_batch"))
    source_by_name = {source["name"]: source for source in base["sources"]}
    sources = []
    for report in manifest["seeds"]:
        anchor = source_by_name[report["anchor_source"]]
        if report["split"] != anchor["split"]:
            raise ValueError("connected source split drift")
        for frozen in report["frozen"]:
            path = Path(frozen["path"])
            if not path.is_absolute():
                path = ROOT / path
            if _sha(path) != frozen["sha256"]:
                raise ValueError("connected source snapshot hash drift")
            snapshot = _snapshot(ROOT, {"path": str(path), "sha256": frozen["sha256"]})
            if (
                snapshot["snapshot_id"] != frozen["snapshot_id"]
                or snapshot["source"]["revisions"] != frozen["revisions"]
                or frozen["strict_join_tasks"] < 1
            ):
                raise ValueError("connected source revision or productivity drift")
            tasks = wiki_row_binding.build_join_tasks(
                wiki_world_bridge.snapshot_to_world(snapshot), max_tasks=32
            )
            if (
                len(tasks) != frozen["strict_join_tasks"]
                or [task.task_id for task in tasks] != frozen["task_ids"]
            ):
                raise ValueError("connected source task replay drift")
            if any(
                existing.get(title.casefold(), {anchor["split"]}) != {anchor["split"]}
                for title in frozen["revisions"]
            ):
                raise ValueError("connected source crosses prior split")
            sources.append(
                {
                    "name": frozen["snapshot_id"],
                    "domain": anchor["domain"],
                    "topic": anchor["topic"],
                    "split": anchor["split"],
                    "snapshot": {
                        "path": (
                            str(path.relative_to(ROOT))
                            if path.is_relative_to(ROOT)
                            else str(path)
                        ),
                        "sha256": frozen["sha256"],
                    },
                }
            )
    source_pool = {
        "schema": "longworld.source-batch-pool.v2",
        "prior_source_manifest": base["prior_source_manifest"],
        "requested_recipes": base["requested_recipes"],
        "max_tasks_by_recipe": base["max_tasks_by_recipe"],
        "sources": sources,
    }
    _write(output_dir / "source_pool.json", source_pool)
    receipt = {
        "schema": SCHEMA + ".source-pool-receipt",
        "acquisition_manifest_sha256": _sha(output_dir / "acquisition_manifest.json"),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "source_groups": len(sources),
        "train_groups": sum(source["split"] == "train" for source in sources),
        "eval_groups": sum(source["split"] == "eval" for source in sources),
    }
    _write(output_dir / "source_pool_receipt.json", receipt)
    return receipt


def refilter_frozen_acquisition(
    config_path: Path, original_dir: Path, output_dir: Path
) -> dict[str, Any]:
    """Reapply the current JOIN gate to immutable already-fetched snapshots."""
    if output_dir.exists():
        raise ValueError("refilter output directory must be new")
    original = json.loads((original_dir / "acquisition_manifest.json").read_text())
    receipt = json.loads((original_dir / "source_pool_receipt.json").read_text())
    if (
        original.get("config_sha256") != _sha(config_path)
        or receipt.get("acquisition_manifest_sha256")
        != _sha(original_dir / "acquisition_manifest.json")
        or receipt.get("source_pool_sha256") != _sha(original_dir / "source_pool.json")
    ):
        raise ValueError("original acquisition lineage changed")
    for report in original["seeds"]:
        accepted = []
        for frozen in report["frozen"]:
            path = Path(frozen["path"])
            if not path.is_absolute():
                path = ROOT / path
            if _sha(path) != frozen["sha256"]:
                raise ValueError("frozen source snapshot changed")
            snapshot = _snapshot(ROOT, {"path": str(path), "sha256": frozen["sha256"]})
            tasks = wiki_row_binding.build_join_tasks(
                wiki_world_bridge.snapshot_to_world(snapshot), max_tasks=32
            )
            if tasks:
                accepted.append(
                    {
                        **frozen,
                        "strict_join_tasks": len(tasks),
                        "task_ids": [task.task_id for task in tasks],
                    }
                )
            else:
                report["rejections"].append(
                    {"title": frozen["title"], "reason": "no_tasks_after_refilter"}
                )
        report["frozen"] = accepted
    original["refiltered_from_manifest_sha256"] = _sha(
        original_dir / "acquisition_manifest.json"
    )
    original["row_binding_source_sha256"] = _sha(
        ROOT / "longworld/synthesis/wiki_row_binding.py"
    )
    original["frozen_groups"] = sum(
        len(report["frozen"]) for report in original["seeds"]
    )
    original["productive_groups"] = original["frozen_groups"]
    original["strict_join_tasks"] = sum(
        frozen["strict_join_tasks"]
        for report in original["seeds"]
        for frozen in report["frozen"]
    )
    output_dir.mkdir(parents=True)
    _write(output_dir / "acquisition_manifest.json", original)
    materialize_source_pool(config_path, output_dir)
    return original


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--refilter-from", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            (
                refilter_frozen_acquisition(
                    args.config, args.refilter_from, args.output_dir
                )
                if args.refilter_from is not None
                else run(args.config, args.output_dir)
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
