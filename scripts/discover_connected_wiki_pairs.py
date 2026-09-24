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

from longworld.synthesis import (
    wiki_adapter,
    wiki_cross_document_join,
    wiki_world_bridge,
)
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
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    by_doc: dict[str, set[str]] = {}
    for fact in world.facts:
        if (
            fact.relation != "includes facility"
            or fact.value_type != "entity"
            or fact.value not in world.objects
            or len(fact.supporting_spans) != 1
            or wiki_cross_document_join.wiki_table_lookup._inverse_selector(world, fact)
            is None
        ):
            continue
        title = world._docs[fact.supporting_spans[0].doc_id].title
        by_doc.setdefault(title, set()).add(world.label_of(fact.value))
    return {title: sorted(names) for title, names in by_doc.items()}


def _spread(names: list[str], limit: int) -> list[str]:
    if limit == 1:
        return [names[len(names) // 2]] if names else []
    if len(names) <= limit:
        return names
    return [names[index * (len(names) - 1) // (limit - 1)] for index in range(limit)]


def _preview_score(text: str, anchor_names: set[str]) -> tuple[int, int]:
    """Cheap source routing only; the frozen native compiler is authoritative."""
    header: tuple[str, ...] | None = None
    matching: set[str] = set()
    alternatives: set[str] = set()
    for line in wiki_adapter.structured_lines(text):
        if line.kind == "table_header":
            header = line.cells
            continue
        if line.kind != "table_row":
            header = None
            continue
        if header is None or len(header) != len(line.cells):
            continue
        name_index = wiki_adapter._name_column_index(header)
        if name_index is None:
            continue
        name = line.cells[name_index]
        valid_target = any(
            (semantic := wiki_adapter._cell_semantics(column, cell)) is not None
            and semantic[0] in wiki_cross_document_join.TARGET_RELATIONS
            for index, (column, cell) in enumerate(zip(header, line.cells))
            if index != name_index
        )
        if not valid_target:
            continue
        alternatives.add(name)
        if name.casefold() in anchor_names:
            matching.add(name)
    return len(matching), len(alternatives)


def _existing_titles(pool: dict[str, Any]) -> dict[str, set[str]]:
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
    return assignments


def _validate(config: dict[str, Any], pool: dict[str, Any]) -> None:
    if (
        config.get("schema") != SCHEMA
        or pool.get("schema") != "longworld.source-batch-pool.v2"
    ):
        raise ValueError("wrong connected intake or source pool schema")
    seeds = config.get("seeds")
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
    _validate(config, pool)
    existing = _existing_titles(pool)
    sources = {item["name"]: item for item in pool["sources"]}
    fetcher = fetcher or wiki_adapter.WikiHttpFetcher(API)
    output_dir.mkdir(parents=True)
    (output_dir / "snapshots").mkdir()
    (output_dir / "search").mkdir()
    seed_reports = []
    new_titles: set[str] = set()
    for seed in config["seeds"]:
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
        for anchor_title, names in sorted(rows.items()):
            if existing.get(anchor_title.casefold()) != {source["split"]}:
                report["rejections"].append(
                    {"title": anchor_title, "reason": "anchor_crosses_prior_split"}
                )
                continue
            hits: Counter[str] = Counter()
            for entity in _spread(names, seed["max_queries"]):
                query = f'intitle:"List of" "{entity}"'
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
            )[: seed["max_previews"]]:
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
                    tasks = wiki_cross_document_join.build_join_tasks(
                        world, max_tasks=32
                    )
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
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.output_dir), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
