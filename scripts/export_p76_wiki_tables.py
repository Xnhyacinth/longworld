"""Compile frozen Wiki table-pair tasks into exact-length reader candidates.

Whole frozen source pages stay intact. If a snapshot lacks enough pages for
32K, the exporter preserves its measured native length rather than padding it.
The resulting train/eval filenames denote split candidates, not training approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import (
    length_controller,
    reader_view,
    wiki_table_tasks,
    wiki_world_bridge,
)
from longworld.synthesis.shared_semantic_world import SemanticWorld

DEFAULT_SNAPSHOT = (
    ROOT
    / "data/capability_records/p74_wiki_snapshot_v1/astronomical_observatories_snapshot.json"
)
DEFAULT_OUTPUT = ROOT / "data/candidates/p76_wiki_table_pairs_v5_astronomy"
SCHEMA = "longworld.p76-wiki-table-pairs.v5"
TOKEN_BINS = {"32k": (32768, 65536), "64k": (65536, 131072)}
EXCLUDED_TITLES = frozenset({"List of observatory codes"})


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, rows: list[dict[str, Any]]) -> str:
    data = "".join(_dump(row) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


def _positioned_world(world: SemanticWorld, required: tuple[str, str]) -> SemanticWorld:
    """Keep source bytes whole while placing required pages at opposite ends."""
    first, last = required
    documents = tuple(
        sorted(
            world.documents,
            key=lambda doc: (
                0 if doc.doc_id == first else 2 if doc.doc_id == last else 1,
            ),
        )
    )
    return SemanticWorld(documents, world.entities, world.facts)


def _selected_docs(
    world: Any, task: Any, tokenizer: Any
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Choose nested, whole-page scopes with a margin from both bin edges."""
    required = set(task.scope.documents)
    optional = [
        doc
        for doc in world.documents
        if doc.doc_id not in required and doc.text and doc.title not in EXCLUDED_TITLES
    ]
    # Prefer substantial related pages; the 64K variant adds smaller complete
    # pages only after the main astronomical overview and timeline material.
    optional.sort(key=lambda doc: (-len(doc.text), doc.doc_id))
    chosen = set(required)
    scopes: dict[str, tuple[str, ...]] = {}
    unsupported: dict[str, str] = {}
    for target, (low, high) in TOKEN_BINS.items():
        while True:
            doc_ids = tuple(
                doc.doc_id for doc in world.documents if doc.doc_id in chosen
            )
            view = wiki_table_tasks.with_scope_documents(task, doc_ids)
            # Only measure the actual final chat template; never estimate by
            # character count or target length. This inexpensive probe is
            # compiled without audit output publication.
            try:
                _row, index, _audit = reader_view.compile_task(
                    world,
                    view,
                    source_group=f"scope_probe:{target}",
                    tokenizer=tokenizer,
                )
            except ValueError as error:
                raise ValueError(f"scope probe failed: {error}") from error
            tokens = index["full_chat_tokens"]
            if low + 256 <= tokens < high - 256:
                scopes[target] = doc_ids
                break
            if tokens >= high - 256:
                if target == "64k" and scopes:
                    unsupported[target] = (
                        f"whole source page would exceed 64K upper bound: {tokens}"
                    )
                    break
                raise ValueError(
                    f"no whole-page {target} scope below upper bound: {tokens}"
                )
            if not optional:
                if target == "64k" and scopes:
                    unsupported[target] = (
                        f"complete source pages reach only {tokens} tokens"
                    )
                    break
                raise ValueError(
                    f"source lacks enough whole-page material for {target}: {tokens}"
                )
            chosen.add(optional.pop(0).doc_id)
    return scopes, unsupported


def export(
    snapshot_path: Path,
    output_dir: Path,
    *,
    max_tasks: int = 24,
    split: str = "train",
    domain: str,
    topic: str,
) -> dict[str, Any]:
    if split not in {"train", "eval"}:
        raise ValueError("split must be train or eval")
    if not domain.strip() or not topic.strip():
        raise ValueError("domain and topic must be explicitly nonempty")
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot = json.loads(snapshot_bytes)
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    tasks = wiki_table_tasks.build_table_pair_tasks(world, max_tasks=max_tasks)
    if not tasks:
        raise ValueError("snapshot has no supported cross-document table-year pairs")
    positioned = _positioned_world(world, tasks[0].scope.documents)
    tokenizer = length_controller.get_tokenizer()
    try:
        scopes, unsupported_lengths = _selected_docs(positioned, tasks[0], tokenizer)
        unsupported_lengths = {
            **unsupported_lengths,
            "128k": "available complete source pages do not produce a suitable 128K view under the source composition rules",
        }
    except ValueError as error:
        if "source lacks enough whole-page material for 32k" not in str(error):
            raise
        native = tuple(doc.doc_id for doc in positioned.documents if doc.text)
        scopes = {"native": native}
        unsupported_lengths = {
            "32k": "native frozen pages do not reach 32K final tokens",
            "64k": "native frozen pages do not reach 64K final tokens",
            "128k": "native frozen pages do not reach 128K final tokens",
        }
    group = snapshot["snapshot_id"]
    train: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for task in tasks:
        for target, doc_ids in scopes.items():
            low, high = TOKEN_BINS.get(target, (0, 32768))
            try:
                scoped = wiki_table_tasks.with_scope_documents(task, doc_ids)
                wiki_table_tasks.validate_table_pair_task(positioned, scoped)
                # The salt distinguishes length views while logical source_group
                # and task_id stay identical for semantic-task deduplication.
                row, index, audit = reader_view.compile_task(
                    positioned,
                    scoped,
                    source_group=f"{group}:{target}",
                    tokenizer=tokenizer,
                    max_full_tokens=high,
                )
                tokens = index["full_chat_tokens"]
                if not low <= tokens < high:
                    raise ValueError(f"exact_length_bin_miss:{target}:{tokens}")
                context = reader_view.render_documents(
                    positioned, scoped.scope.documents
                )
                parsed_answer = wiki_table_tasks.reader_table_replay(
                    positioned, scoped, context.text
                )
                if parsed_answer != task.answer:
                    raise ValueError("gold-blind reader parser disagrees with oracle")
                intervention = wiki_table_tasks.reader_year_cell_intervention(
                    positioned, scoped, context.text
                )
            except ValueError as error:
                rejects.append(
                    {
                        "task_id": task.task_id,
                        "length_bin": target,
                        "reason": str(error),
                    }
                )
                continue
            index.update(
                {
                    "source_group": group,
                    "world_id": group,
                    "task_id": task.task_id,
                    "task_type": "table_pair_earlier_year",
                    "domain": domain,
                    "topic": topic,
                    "source_kind": "real_wiki",
                    "length_bin": target,
                    "split": split,
                    "evidence_status": "two_table_rows_subject_header_value_checked",
                    "dependency_status": intervention["status"],
                    "quality_status": "research_candidate",
                }
            )
            audit.update(
                {
                    "source_group": group,
                    "task_type": "table_pair_earlier_year",
                    "reader_text_intervention": intervention,
                    "oracle_replay": {
                        "program_answer": wiki_table_tasks.execute_table_pair(
                            positioned, task.program
                        ),
                        "value_blind_reader_parser_answer": parsed_answer,
                        "supervised_answer": json.loads(row["messages"][1]["content"]),
                    },
                }
            )
            train.append(row)
            index_rows.append(index)
            audits.append(audit)
    output_dir.mkdir(parents=True)
    source_manifest = [
        {
            "source_group": group,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": _sha(snapshot_bytes),
            "license": snapshot["source"]["license"],
            "documents": [
                {
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "page_url": doc.get("page_url"),
                    "revision_url": doc.get("revision_url"),
                }
                for doc in snapshot["documents"]
            ],
        }
    ]
    digests = {
        "train.jsonl": _write(
            output_dir / "train.jsonl", train if split == "train" else []
        ),
        "eval.jsonl": _write(
            output_dir / "eval.jsonl", train if split == "eval" else []
        ),
        "sample_index.jsonl": _write(output_dir / "sample_index.jsonl", index_rows),
        "audit.jsonl": _write(output_dir / "audit.jsonl", audits),
        "rejects.jsonl": _write(output_dir / "rejects.jsonl", rejects),
        "source_manifest.jsonl": _write(
            output_dir / "source_manifest.jsonl", source_manifest
        ),
    }
    manifest = {
        "schema": SCHEMA,
        "quality_status": "research_candidate",
        "train_ready": False,
        "training_release_eligible": False,
        "split": split,
        "split_policy": "whole source group assigned to one split",
        "source_group": group,
        "domain": domain,
        "topic": topic,
        "snapshot_sha256": _sha(snapshot_bytes),
        "independent_tasks": len({row["task_id"] for row in index_rows}),
        "candidate_rows": len(train),
        "candidate_rows_by_length": dict(
            sorted(Counter(row["length_bin"] for row in index_rows).items())
        ),
        "rejected_rows": len(rejects),
        "required_documents": list(tasks[0].scope.documents),
        "scope_documents": {name: list(ids) for name, ids in scopes.items()},
        "length_method": "complete frozen pages, required pages at opposite ends, exact pinned final chat tokens",
        "not_supported_lengths": unsupported_lengths,
        "dependency_certification": "value-blind final-reader table parser replay plus scoped two-subject year-cell deletion; unrestricted prose equivalence and independent model reader unchecked",
        "files_sha256": digests,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-tasks", type=int, default=24)
    parser.add_argument("--split", choices=("train", "eval"), default="train")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--topic", required=True)
    args = parser.parse_args()
    manifest = export(
        args.snapshot,
        args.output_dir,
        max_tasks=args.max_tasks,
        split=args.split,
        domain=args.domain,
        topic=args.topic,
    )
    print(
        _dump(
            {
                key: manifest[key]
                for key in (
                    "source_group",
                    "independent_tasks",
                    "candidate_rows",
                    "candidate_rows_by_length",
                    "rejected_rows",
                    "train_ready",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
