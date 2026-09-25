"""Recover JOIN source pairs from pinned searches with support-first ranking.

This reuses P102 v1 search responses; only selected page previews and admitted
source freezes make new HTTP requests. Final readers still pass the independent
P102 global title/URL gate and the existing row-binding compiler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_adapter, wiki_row_binding, wiki_world_bridge
from scripts.discover_connected_wiki_pairs import (
    API,
    _anchor_rows,
    _existing_titles,
    _preview_score,
    _topic_matches_title,
)
from scripts.freeze_wiki_title_bundle import freeze_titles, resolve_titles
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p102-connected-recovery.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("recovery pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"recovery pin changed: {relative}")
    return path


def _places(titles: list[str]) -> set[str]:
    return {
        title.rsplit(" in ", 1)[1].casefold() for title in titles if " in " in title
    }


def _workspace_relative(path: Path) -> str:
    """Preserve a logical relative output path without treating it as absolute."""
    if path.is_absolute():
        return str(path.relative_to(ROOT))
    if ".." in path.parts:
        raise ValueError("recovered snapshot path escapes workspace")
    return str(path)


def _ranked_candidates(
    seed: dict, source: dict, original_dir: Path, existing: set[str], preview_limit: int
) -> tuple[list[dict], list[dict]]:
    anchor = _snapshot(ROOT, source["snapshot"])
    titles = [doc["title"] for doc in anchor["documents"]]
    places = _places(titles)
    already = {row["title"].casefold() for row in seed["previewed"]}
    counts: Counter[str] = Counter()
    entity_hits: Counter[str] = Counter()
    search_receipts = []
    for query in seed["searched_entities"]:
        path = (
            original_dir
            / "search"
            / f"{seed['seed']}_{hashlib.sha256(query['entity'].encode()).hexdigest()[:12]}.json"
        )
        if not path.is_file() or _sha(path) != query["response_sha256"]:
            raise ValueError("frozen search response changed")
        payload = json.loads(path.read_text())
        actual = [
            row["title"]
            for row in payload["query"]["search"]
            if row["title"].startswith("List of ")
        ]
        if actual != query["titles"]:
            raise ValueError("search response title inventory changed")
        search_receipts.append(
            {"entity": query["entity"], "response_sha256": query["response_sha256"]}
        )
        for title in actual:
            if (
                title.casefold() in existing
                or title.casefold() in already
                or title.casefold() in {name.casefold() for name in titles}
            ):
                continue
            counts[title] += 1
            if query["entity"] != "topic":
                entity_hits[title] += 1
    ranked = []
    for title, hits in counts.items():
        topic_match = _topic_matches_title(source["topic"], title)
        place_match = any(place in title.casefold() for place in places)
        supported = topic_match
        ranked.append(
            {
                "title": title,
                "topic_match": topic_match,
                "place_match": place_match,
                "entity_query_hits": entity_hits[title],
                "search_hits": hits,
                "status": "candidate" if supported else "topic_mismatch",
            }
        )
    ranked.sort(
        key=lambda row: (
            row["status"] != "candidate",
            -int(row["topic_match"]),
            -int(row["place_match"]),
            -row["entity_query_hits"],
            -row["search_hits"],
            row["title"],
        )
    )
    selected = [row for row in ranked if row["status"] == "candidate"][:preview_limit]
    chosen = {row["title"] for row in selected}
    for rank, row in enumerate(ranked):
        row["rank"] = rank
        if row["status"] == "candidate" and row["title"] not in chosen:
            row["status"] = "preview_budget"
    return selected, ranked


def _process_seed(job: tuple) -> dict:
    seed, source, candidate_titles, output_dir, max_freezes = job
    snapshot = _snapshot(ROOT, source["snapshot"])
    anchors = _anchor_rows(snapshot)
    fetcher = wiki_adapter.WikiHttpFetcher(API)
    report = {
        "seed": seed["seed"],
        "anchor_source": source["name"],
        "split": source["split"],
        "searched_entities": seed["searched_entities"],
        "previewed": [],
        "frozen": [],
        "rejections": [],
    }
    for title in candidate_titles:
        if len(report["frozen"]) >= max_freezes:
            report["rejections"].append({"title": title, "reason": "freeze_quota"})
            continue
        try:
            member = resolve_titles(fetcher, [title])[0]
            page = fetcher.fetch_revision(member.pageid)
            text = wiki_adapter.render_wikitext(page.title, page.wikitext).text
        except (wiki_adapter.HttpError, wiki_adapter.SnapshotError) as error:
            report["rejections"].append(
                {"title": title, "reason": f"preview:{type(error).__name__}:{error}"}
            )
            continue
        if page.title.casefold() != title.casefold():
            report["rejections"].append(
                {"title": title, "reason": "resolved_title_changed"}
            )
            continue
        best = max(
            (
                (matching, alternatives, anchor_title)
                for anchor_title, names in anchors.items()
                for matching, alternatives in [
                    _preview_score(text, {name.casefold() for name in names})
                ]
            ),
            default=(0, 0, ""),
        )
        report["previewed"].append(
            {
                "title": page.title,
                "revision": page.revid,
                "matching_rows": best[0],
                "target_rows": best[1],
            }
        )
        if best[0] < 1 or best[1] < 2:
            report["rejections"].append(
                {"title": title, "reason": "no_visible_shared_rows_or_alternatives"}
            )
            continue
        anchor_title = best[2]
        label = (
            "p102_recover_"
            + hashlib.sha256(
                f"{seed['seed']}|{anchor_title}|{title}".encode()
            ).hexdigest()[:18]
        )
        path = output_dir / "snapshots" / f"{label}.json"
        try:
            receipt = freeze_titles(fetcher, [anchor_title, title], label, path)
        except (wiki_adapter.HttpError, wiki_adapter.SnapshotError) as error:
            report["rejections"].append(
                {"title": title, "reason": f"freeze:{type(error).__name__}:{error}"}
            )
            continue
        frozen = json.loads(path.read_text())
        tasks = wiki_row_binding.build_join_tasks(
            wiki_world_bridge.snapshot_to_world(frozen), max_tasks=32
        )
        if not tasks:
            report["rejections"].append(
                {"title": title, "reason": "no_strict_join_after_freeze"}
            )
            continue
        report["frozen"].append(
            {
                "title": title,
                "path": _workspace_relative(path),
                "sha256": _sha(path),
                "snapshot_id": receipt["snapshot_id"],
                "revisions": receipt["revisions"],
                "license": receipt["license"],
                "strict_join_tasks": len(tasks),
                "task_ids": [task.task_id for task in tasks],
                "source_characters": sum(
                    len(doc["text"]) for doc in frozen["documents"]
                ),
            }
        )
    return report


def _prior(config: dict) -> tuple[dict, dict, Path, dict]:
    base_path = _pin(config["base_pool"])
    base = json.loads(base_path.read_text())
    original_path = _pin(config["original_manifest"])
    original = json.loads(original_path.read_text())
    if (
        base.get("schema") != "longworld.source-batch-pool.v2"
        or original.get("schema") != "longworld.connected-wiki-intake.v1.result"
        or original["base_pool_sha256"] != _sha(base_path)
    ):
        raise ValueError("P102 recovery inputs differ")
    original_config = json.loads(_pin(config["original_config"]).read_text())
    if original["config_sha256"] != config["original_config"][
        "sha256"
    ] or original_config["base_pool_sha256"] != _sha(base_path):
        raise ValueError("original acquisition config changed")
    return base, original, original_path.parent, original_config


def run(config_path: Path, output_dir: Path, *, workers: int = 4) -> dict:
    if output_dir.exists():
        raise ValueError("recovery output must be new")
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= workers <= 8
        or not 1 <= config.get("max_previews_per_seed", 0) <= 20
        or not 1 <= config.get("max_freezes_per_seed", 0) <= 4
    ):
        raise ValueError("invalid P102 recovery config")
    base, original, original_dir, original_config = _prior(config)
    source_by_name = {row["name"]: row for row in base["sources"]}
    existing = set(_existing_titles(base, original_config.get("prior_unified_batch")))
    existing.update(
        item["title"].casefold()
        for seed in original["seeds"]
        for item in seed["frozen"]
    )
    proposals = []
    ranking = []
    for seed in original["seeds"]:
        source = source_by_name[seed["anchor_source"]]
        selected, rows = _ranked_candidates(
            seed, source, original_dir, existing, config["max_previews_per_seed"]
        )
        for row in rows:
            ranking.append(
                {
                    "seed": seed["seed"],
                    "anchor_source": source["name"],
                    "split": source["split"],
                    "domain": source["domain"],
                    **row,
                }
            )
        for item in selected:
            proposals.append((seed, source, item))
    owners: dict[str, list[tuple]] = defaultdict(list)
    for proposal in proposals:
        owners[proposal[2]["title"].casefold()].append(proposal)
    selected_by_seed: dict[str, list[str]] = defaultdict(list)
    for title_key, rows in owners.items():
        if len({row[1]["split"] for row in rows}) > 1:
            for item in ranking:
                if (
                    item["title"].casefold() == title_key
                    and item["status"] == "candidate"
                ):
                    item["status"] = "cross_split_candidate"
            continue
        winner = min(rows, key=lambda row: (row[2]["rank"], row[0]["seed"]))
        selected_by_seed[winner[0]["seed"]].append(winner[2]["title"])
        for loser in rows:
            if loser is winner:
                continue
            for item in ranking:
                if (
                    item["seed"] == loser[0]["seed"]
                    and item["title"].casefold() == title_key
                ):
                    item["status"] = "duplicate_candidate_owned_by_other_seed"
    output_dir.mkdir(parents=True)
    (output_dir / "snapshots").mkdir()
    jobs = [
        (
            seed,
            source_by_name[seed["anchor_source"]],
            selected_by_seed.get(seed["seed"], []),
            output_dir,
            config["max_freezes_per_seed"],
        )
        for seed in original["seeds"]
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        reports = list(executor.map(_process_seed, jobs))
    sources = []
    for report in reports:
        source = source_by_name[report["anchor_source"]]
        for frozen in report["frozen"]:
            sources.append(
                {
                    "name": frozen["snapshot_id"],
                    "domain": source["domain"],
                    "topic": source["topic"],
                    "split": source["split"],
                    "snapshot": {"path": frozen["path"], "sha256": frozen["sha256"]},
                }
            )
    source_pool = {**base, "sources": sources}
    ranking_content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ranking
    )
    (output_dir / "candidate_ranking.jsonl").write_text(ranking_content)
    (output_dir / "source_pool.json").write_text(
        json.dumps(source_pool, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    acquisition = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "base_pool_sha256": config["base_pool"]["sha256"],
        "original_search_manifest_sha256": config["original_manifest"]["sha256"],
        "search_queries_reused": original["searched_entities"],
        "additional_page_previews": sum(len(report["previewed"]) for report in reports),
        "candidate_ranking_sha256": _sha(output_dir / "candidate_ranking.jsonl"),
        "seeds": reports,
        "frozen_groups": len(sources),
        "productive_groups": len(sources),
        "strict_join_tasks": sum(
            frozen["strict_join_tasks"]
            for report in reports
            for frozen in report["frozen"]
        ),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "train_ready": False,
    }
    (output_dir / "acquisition_manifest.json").write_text(
        json.dumps(acquisition, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    receipt = {
        "schema": "longworld.connected-wiki-intake.v1.source-pool-receipt",
        "acquisition_manifest_sha256": _sha(output_dir / "acquisition_manifest.json"),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "source_groups": len(sources),
        "train_groups": sum(row["split"] == "train" for row in sources),
        "eval_groups": sum(row["split"] == "eval" for row in sources),
    }
    (output_dir / "source_pool_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return acquisition


def verify(config_path: Path, output_dir: Path) -> dict:
    config = json.loads(config_path.read_text())
    _, original, original_dir, _ = _prior(config)
    for seed in original["seeds"]:
        for query in seed["searched_entities"]:
            path = (
                original_dir
                / "search"
                / f"{seed['seed']}_{hashlib.sha256(query['entity'].encode()).hexdigest()[:12]}.json"
            )
            if _sha(path) != query["response_sha256"]:
                raise ValueError("frozen search response changed")
    manifest_path = output_dir / "acquisition_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    pool_path = output_dir / "source_pool.json"
    pool = json.loads(pool_path.read_text())
    receipt = json.loads((output_dir / "source_pool_receipt.json").read_text())
    if (
        manifest.get("schema") != SCHEMA + ".result"
        or manifest["config_sha256"] != _sha(config_path)
        or manifest["original_search_manifest_sha256"]
        != config["original_manifest"]["sha256"]
        or manifest["candidate_ranking_sha256"]
        != _sha(output_dir / "candidate_ranking.jsonl")
        or manifest["source_pool_sha256"] != _sha(pool_path)
        or receipt["acquisition_manifest_sha256"] != _sha(manifest_path)
        or receipt["source_pool_sha256"] != _sha(pool_path)
        or len(pool["sources"]) != manifest["frozen_groups"]
    ):
        raise ValueError("P102 recovery output lineage differs")
    by_id = {row["name"]: row for row in pool["sources"]}
    for report in manifest["seeds"]:
        for frozen in report["frozen"]:
            source = by_id[frozen["snapshot_id"]]
            snapshot = _snapshot(ROOT, source["snapshot"])
            if (
                source["snapshot"]["sha256"] != frozen["sha256"]
                or snapshot["source"]["revisions"] != frozen["revisions"]
            ):
                raise ValueError("recovered source snapshot changed")
            tasks = wiki_row_binding.build_join_tasks(
                wiki_world_bridge.snapshot_to_world(snapshot), max_tasks=32
            )
            if [task.task_id for task in tasks] != frozen["task_ids"]:
                raise ValueError("recovered reader tasks changed")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        verify(args.config, args.output_dir)
        if args.verify_only
        else run(args.config, args.output_dir, workers=args.workers)
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "schema",
                    "search_queries_reused",
                    "additional_page_previews",
                    "frozen_groups",
                    "strict_join_tasks",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
