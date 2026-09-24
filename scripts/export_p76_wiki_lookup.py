"""Compile structurally supported Wiki table lookups into reader candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import (
    length_controller,
    reader_view,
    wiki_table_lookup,
    wiki_world_bridge,
)

SCHEMA = "longworld.p76-wiki-table-lookup.v1"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write(path: Path, rows: list[dict[str, Any]]) -> str:
    data = "".join(_dump(row) + "\n" for row in rows).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _bin(tokens: int) -> str:
    for cap, name in (
        (32768, "lt32k"),
        (65536, "32k"),
        (131072, "64k"),
        (262144, "128k"),
    ):
        if tokens < cap:
            return name
    return "ge256k"


def export(
    snapshot_path: Path,
    output_dir: Path,
    *,
    domain: str,
    topic: str,
    split: str,
    max_tasks: int = 32,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory already exists")
    if split not in {"train", "eval"} or not domain or not topic:
        raise ValueError("split/domain/topic required")
    raw = snapshot_path.read_bytes()
    snapshot = json.loads(raw)
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    typed, _ = wiki_world_bridge.structurally_typed_world(world)
    tasks = wiki_table_lookup.build_lookup_tasks(typed, max_tasks=max_tasks)
    if not tasks:
        raise ValueError("source has no supported table lookup tasks")
    tokenizer = length_controller.get_tokenizer()
    rows: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    group = snapshot["snapshot_id"]
    for task in tasks:
        try:
            row, index, audit = reader_view.compile_task(
                typed,
                task,
                source_group=group,
                tokenizer=tokenizer,
                max_full_tokens=262144,
            )
            context = reader_view.render_documents(typed, task.scope.documents)
            value, _ = wiki_table_lookup.reader_lookup(typed, task, context.text)
            if value != task.answer_rendered:
                raise ValueError("final reader and native oracle disagree")
            intervention = wiki_table_lookup.reader_cell_intervention(
                typed, task, context.text
            )
            if (
                index.get("evidence_token_mapping") != "exact_prompt_offsets"
                or not index.get("fact_value_token_spans")
                or index["input_tokens"] + index["supervised_tokens"]
                != index["full_chat_tokens"]
            ):
                raise ValueError("final token or evidence mapping failed")
        except ValueError as error:
            rejects.append({"task_id": task.task_id, "reason": str(error)})
            continue
        index.update(
            {
                "source_group": group,
                "world_id": group,
                "task_id": task.task_id,
                "task_type": "table_cell_lookup",
                "domain": domain,
                "topic": topic,
                "source_kind": "real_wiki",
                "split": split,
                "length_bin": _bin(index["full_chat_tokens"]),
                "evidence_status": "table_subject_header_value_structurally_checked",
                "dependency_status": intervention["status"],
                "quality_status": "research_candidate",
            }
        )
        audit.update(
            {
                "source_group": group,
                "task_type": "table_cell_lookup",
                "value_blind_reader_parser_answer": value,
                "reader_text_intervention": intervention,
            }
        )
        rows.append(row)
        indexes.append(index)
        audits.append(audit)
    output_dir.mkdir(parents=True)
    hashes = {
        "train.jsonl": _write(
            output_dir / "train.jsonl", rows if split == "train" else []
        ),
        "eval.jsonl": _write(
            output_dir / "eval.jsonl", rows if split == "eval" else []
        ),
        "sample_index.jsonl": _write(output_dir / "sample_index.jsonl", indexes),
        "audit.jsonl": _write(output_dir / "audit.jsonl", audits),
        "rejects.jsonl": _write(output_dir / "rejects.jsonl", rejects),
        "source_manifest.jsonl": _write(
            output_dir / "source_manifest.jsonl",
            [
                {
                    "source_group": group,
                    "snapshot_path": str(snapshot_path),
                    "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
                    "license": snapshot["source"].get("license"),
                    "revisions": snapshot["source"]["revisions"],
                }
            ],
        ),
    }
    manifest = {
        "schema": SCHEMA,
        "source_group": group,
        "split": split,
        "domain": domain,
        "topic": topic,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_rows": len(rows),
        "independent_tasks": len({index["task_id"] for index in indexes}),
        "rejected_rows": len(rejects),
        "quality_status": "research_candidate" if rows else "source_rejected",
        "train_ready": False,
        "training_release_eligible": False,
        "dependency_scope": "one named table cell deletion; unrestricted prose equivalence and independent model reader unchecked",
        "files_sha256": hashes,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--split", choices=("train", "eval"), required=True)
    parser.add_argument("--max-tasks", type=int, default=32)
    args = parser.parse_args()
    print(
        _dump(
            export(
                args.snapshot,
                args.output_dir,
                domain=args.domain,
                topic=args.topic,
                split=args.split,
                max_tasks=args.max_tasks,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
