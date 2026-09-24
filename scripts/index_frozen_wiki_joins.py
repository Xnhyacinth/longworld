"""Find source-supported Wiki JOIN pairs through exact typed-row postings.

This is a bounded, offline source planner. It indexes rendered row names once,
probes only same-split and same-domain/topic page pairs with the existing JOIN
compiler, and freezes accepted two-page reader snapshots. No label is invented:
domain and topic come from pinned source pools. Prior task IDs are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_adapter, wiki_row_binding
from longworld.synthesis.shared_semantic_world import Document, SemanticWorld
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.frozen-wiki-row-index.v1"


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _output_pin(path: Path) -> str:
    """Keep the logical workspace path; data/ may be a mounted symlink."""
    logical = path if path.is_absolute() else ROOT / path
    relative = logical.relative_to(ROOT)
    if ".." in relative.parts:
        raise ValueError("output path must stay inside the workspace")
    return str(relative)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
    )


def _topic(topic: str) -> str:
    return "_".join(
        part for part in topic.split("_") if part not in {"lists", "expansion", "broad"}
    )


def _load(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], set[str], dict[str, set[str]], dict[str, Any]]:
    pins = config.get("source_pools")
    prior = config.get("prior_task_indexes")
    if (
        config.get("schema") != SCHEMA
        or not isinstance(pins, list)
        or not pins
        or not isinstance(prior, list)
        or not prior
    ):
        raise ValueError("invalid frozen Wiki index config")
    title_splits: dict[str, set[str]] = defaultdict(set)
    versions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    source_pool = None
    source_groups = 0
    for pin in pins:
        path = _path(pin["path"])
        if _sha(path) != pin["sha256"]:
            raise ValueError(f"source pool pin mismatch: {path}")
        pool = json.loads(path.read_text())
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError("wrong source pool schema")
        if source_pool is None:
            source_pool = pool
        manifest_pin = pool["prior_source_manifest"]
        manifest_path = _path(manifest_pin["path"])
        if _sha(manifest_path) != manifest_pin["sha256"]:
            raise ValueError("prior source manifest pin mismatch")
        for line in manifest_path.read_text().splitlines():
            row = json.loads(line)
            for title in row["revisions"]:
                title_splits[title.casefold()].add(row["split"])
        for source in pool["sources"]:
            source_groups += 1
            snapshot = _snapshot(ROOT, source["snapshot"])
            wiki_adapter.validate_snapshot(snapshot)
            for doc in snapshot["documents"]:
                title = doc["title"].casefold()
                title_splits[title].add(source["split"])
                versions[(title, source["split"])].append(
                    {
                        "source": source,
                        "snapshot": snapshot,
                        "doc": doc,
                        "snapshot_sha256": source["snapshot"]["sha256"],
                    }
                )
    task_ids = set()
    for pin in prior:
        path = _path(pin["path"])
        if _sha(path) != pin["sha256"]:
            raise ValueError(f"prior task index pin mismatch: {path}")
        for line in path.read_text().splitlines():
            task_ids.add(json.loads(line)["semantic_task_id"])
    chosen = []
    version_rejections = Counter()
    for (title, split), entries in sorted(versions.items()):
        if title_splits[title] != {split}:
            version_rejections["cross_split_title"] += len(entries)
            continue
        by_revision: dict[int, set[str]] = defaultdict(set)
        for entry in entries:
            revision = entry["snapshot"]["source"]["revisions"][entry["doc"]["title"]]
            by_revision[revision].add(
                hashlib.sha256(entry["doc"]["text"].encode()).hexdigest()
            )
        if any(len(hashes) > 1 for hashes in by_revision.values()):
            version_rejections["same_revision_text_conflict"] += len(entries)
            continue
        entries.sort(
            key=lambda item: (
                -item["snapshot"]["source"]["revisions"][item["doc"]["title"]],
                item["snapshot_sha256"],
                item["source"]["name"],
            )
        )
        chosen.append(entries[0])
        version_rejections["superseded_same_split_versions"] += len(entries) - 1
    assert source_pool is not None
    return (
        chosen,
        task_ids,
        title_splits,
        {
            "source_groups_scanned": source_groups,
            "unique_titles": len(versions),
            "version_rejections": dict(sorted(version_rejections.items())),
            "source_pool": source_pool,
        },
    )


def _pair_snapshot(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    entries = sorted((first, second), key=lambda item: item["doc"]["doc_id"])
    docs = [entry["doc"] for entry in entries]
    revisions = {
        entry["doc"]["title"]: entry["snapshot"]["source"]["revisions"][
            entry["doc"]["title"]
        ]
        for entry in entries
    }
    licenses = [entry["snapshot"]["source"]["license"] for entry in entries]
    if licenses[0] != licenses[1]:
        raise ValueError("source license mismatch")
    source = {
        **first["snapshot"]["source"],
        "kind": wiki_adapter.TITLE_BUNDLE_KIND,
        "collection_kind": "indexed_row_pair",
        "collection_label": "Indexed frozen Wiki row pair",
        "category": None,
        "revisions": revisions,
        "composition": [
            {
                "title": entry["doc"]["title"],
                "source_snapshot_id": entry["snapshot"]["snapshot_id"],
                "source_snapshot_sha256": entry["snapshot_sha256"],
                "document_sha256": hashlib.sha256(
                    entry["doc"]["text"].encode()
                ).hexdigest(),
            }
            for entry in entries
        ],
        "structured_task_only": True,
    }
    snapshot = {
        "schema_version": wiki_adapter.SOURCE_SNAPSHOT_SCHEMA,
        "snapshot_id": wiki_adapter._snapshot_id(
            "Indexed frozen Wiki row pair", source, docs, []
        ),
        "frozen_at": max(entry["snapshot"]["frozen_at"] for entry in entries),
        "source": source,
        "documents": docs,
        "entities": [],
        "facts": [],
        "relations": [],
        "ungrounded_quarantine": [],
    }
    wiki_adapter.validate_snapshot(snapshot)
    return snapshot


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    entries, prior_tasks, title_splits, info = _load(config)
    if type(config.get("max_pairs")) is not int or not 1 <= config["max_pairs"] <= 1000:
        raise ValueError("max_pairs must be 1..1000")
    if (
        type(config.get("max_tasks_per_pair")) is not int
        or not 1 <= config["max_tasks_per_pair"] <= 1000
    ):
        raise ValueError("max_tasks_per_pair must be 1..1000")
    by_title = {entry["doc"]["title"].casefold(): entry for entry in entries}
    postings: dict[tuple[str, str], set[str]] = defaultdict(set)
    for title, entry in by_title.items():
        rows = wiki_row_binding._rows(entry["doc"]["text"])
        entry["rows"] = rows
        for name in {row.name.value for row in rows}:
            postings[(entry["source"]["split"], name)].add(title)
    shared: Counter[tuple[str, str]] = Counter()
    for titles in postings.values():
        ordered = sorted(titles)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                shared[(left, right)] += 1
    if len(shared) > config["max_pairs"]:
        raise ValueError("row-overlap pair count exceeds max_pairs; shard source pools")
    output_dir.mkdir(parents=True)
    (output_dir / "snapshots").mkdir()
    audits = []
    sources = []
    reasons = Counter()
    accepted_tasks = Counter()
    for (left, right), overlap in sorted(
        shared.items(), key=lambda item: (-item[1], item[0])
    ):
        first, second = by_title[left], by_title[right]
        a, b = first["source"], second["source"]
        audit: dict[str, Any] = {
            "first_title": first["doc"]["title"],
            "second_title": second["doc"]["title"],
            "split": a["split"],
            "first_domain": a["domain"],
            "second_domain": b["domain"],
            "first_topic": a["topic"],
            "second_topic": b["topic"],
            "shared_row_names": overlap,
            "first_source_snapshot_sha256": first["snapshot_sha256"],
            "second_source_snapshot_sha256": second["snapshot_sha256"],
        }
        if a["domain"] != b["domain"] or _topic(a["topic"]) != _topic(b["topic"]):
            audit["status"] = "domain_or_topic_mismatch"
        else:
            world = SemanticWorld(
                tuple(
                    Document(
                        entry["doc"]["doc_id"],
                        entry["doc"]["title"],
                        entry["doc"]["text"],
                    )
                    for entry in (first, second)
                ),
                (),
                (),
            )
            tasks = wiki_row_binding.build_join_tasks(
                world, max_tasks=config["max_tasks_per_pair"]
            )
            new = [task for task in tasks if task.task_id not in prior_tasks]
            audit["compiled_tasks"] = len(tasks)
            audit["novel_tasks"] = len(new)
            if not tasks:
                audit["status"] = "native_join_zero"
            elif not new:
                audit["status"] = "prior_task_duplicate"
            elif len(new) != len(tasks):
                audit["status"] = "mixed_prior_and_novel_tasks"
            else:
                snapshot = _pair_snapshot(first, second)
                path = output_dir / "snapshots" / f"{snapshot['snapshot_id']}.json"
                _write(path, snapshot)
                digest = _sha(path)
                source = {
                    "name": snapshot["snapshot_id"],
                    "domain": a["domain"],
                    "topic": a["topic"],
                    "split": a["split"],
                    "snapshot": {
                        "path": _output_pin(path),
                        "sha256": digest,
                    },
                }
                sources.append(source)
                accepted_tasks[a["split"]] += len(tasks)
                audit.update(
                    {
                        "status": "accepted",
                        "task_ids": [task.task_id for task in tasks],
                        "snapshot_sha256": digest,
                        "snapshot_id": snapshot["snapshot_id"],
                    }
                )
        reasons[audit["status"]] += 1
        audits.append(audit)
    base = info.pop("source_pool")
    pool = {
        "schema": "longworld.source-batch-pool.v2",
        "prior_source_manifest": base["prior_source_manifest"],
        "requested_recipes": base["requested_recipes"],
        "max_tasks_by_recipe": base["max_tasks_by_recipe"],
        "sources": sources,
    }
    _write(output_dir / "source_pool.json", pool)
    _jsonl(output_dir / "pair_audit.jsonl", audits)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        **info,
        "distinct_row_names": len({name for _, name in postings}),
        "cross_split_titles_excluded": sum(
            len(splits) > 1 for splits in title_splits.values()
        ),
        "documents_with_typed_rows": sum(bool(entry["rows"]) for entry in entries),
        "candidate_pairs": len(shared),
        "accepted_pairs": len(sources),
        "accepted_tasks_by_split": dict(sorted(accepted_tasks.items())),
        "pair_statuses": dict(sorted(reasons.items())),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "pair_audit_sha256": _sha(output_dir / "pair_audit.jsonl"),
        "prior_task_ids": len(prior_tasks),
        "train_ready": False,
        "claim_limit": "exact row-name inverted index and bounded native two-table JOIN; no semantic paraphrase or global-proof guarantee",
    }
    _write(output_dir / "index_manifest.json", manifest)
    return manifest


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
