"""Export source-backed complete-table scan candidates from frozen Wiki pages.

The output is a local research candidate. A source whose visible candidate
universe fails exact fact reconciliation is rejected before task compilation.
"""

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
    wiki_table_scan,
    wiki_world_bridge,
)

SCHEMA = "longworld.p76-wiki-table-scan.v2"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, rows: list[dict[str, Any]]) -> str:
    data = "".join(_dump(row) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


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
    table_title: str,
    domain: str,
    topic: str,
    split: str,
    max_tasks: int = 8,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    if split not in {"train", "eval"} or not all((table_title, domain, topic)):
        raise ValueError("split, table title, domain, and topic are required")
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot = json.loads(snapshot_bytes)
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    target = [doc for doc in world.documents if doc.title == table_title]
    if len(target) != 1:
        raise ValueError("named table page must occur once in source group")
    group = snapshot["snapshot_id"]
    index_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    parsed: wiki_table_scan.TableParse | None = None
    try:
        parsed, _facts = wiki_table_scan.closed_universe(world, target[0].doc_id)
        tasks = wiki_table_scan.build_scan_tasks(
            world, target[0].doc_id, max_tasks=max_tasks
        )
        if not tasks:
            raise ValueError("no nontrivial distinct interval tasks")
    except ValueError as error:
        rejects.append(
            {
                "source_group": group,
                "table_title": table_title,
                "reason": f"closed_universe_rejected:{error}",
            }
        )
        tasks = ()
    if tasks:
        tokenizer = length_controller.get_tokenizer()
        for task in tasks:
            try:
                row, index, audit = reader_view.compile_task(
                    world,
                    task,
                    source_group=group,
                    tokenizer=tokenizer,
                    max_full_tokens=262144,
                )
                context = reader_view.render_documents(world, task.scope.documents)
                replayed, final_parse, offset = wiki_table_scan.reader_interval_replay(
                    world, task, context.text
                )
                if replayed != task.answer or len(final_parse.eligible) != len(
                    parsed.eligible
                ):
                    raise ValueError(
                        "final-reader full-table replay disagrees with oracle"
                    )
                intervention = wiki_table_scan.insertion_intervention(
                    world, task, context.text
                )
                if (
                    index["input_tokens"] + index["supervised_tokens"]
                    != index["full_chat_tokens"]
                    or index.get("evidence_token_mapping") != "exact_prompt_offsets"
                    or len(index.get("fact_value_token_spans", []))
                    != len(parsed.eligible)
                    or any(
                        span["end"] > index["input_tokens"]
                        for span in index["fact_value_token_spans"]
                    )
                ):
                    raise ValueError(
                        "final chat mask or candidate evidence mapping failed"
                    )
            except ValueError as error:
                rejects.append(
                    {
                        "source_group": group,
                        "task_id": task.task_id,
                        "reason": str(error),
                    }
                )
                continue
            index.update(
                {
                    "source_group": group,
                    "world_id": group,
                    "task_id": task.task_id,
                    "task_type": "dense_table_interval_scan",
                    "domain": domain,
                    "topic": topic,
                    "source_kind": "real_wiki",
                    "split": split,
                    "length_bin": _bin(index["full_chat_tokens"]),
                    "evidence_status": "complete_plain_year_table_rows_structurally_reconciled",
                    "dependency_status": intervention["status"],
                    "quality_status": "research_candidate",
                }
            )
            audit.update(
                {
                    "source_group": group,
                    "task_type": "dense_table_interval_scan",
                    "candidate_universe": {
                        "eligible_rows": [
                            candidate.to_dict(
                                offset,
                                selected=task.program["low"]
                                <= candidate.year
                                <= task.program["high"],
                            )
                            for candidate in final_parse.eligible
                        ],
                        "format_excluded_rows": [
                            candidate.to_dict(offset)
                            for candidate in final_parse.format_excluded
                        ],
                        "malformed_row_count": final_parse.malformed_row_count,
                        "source_fact_ids": list(task.consumed_fact_ids),
                    },
                    "value_blind_reader_parser_answer": replayed,
                    "insertion_intervention": intervention,
                    "formal_fact_necessity": "not_claimed_for_all_excluded_candidates",
                }
            )
            rows.append(row)
            index_rows.append(index)
            audits.append(audit)
    output_dir.mkdir(parents=True)
    source_rows = [
        {
            "source_group": group,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": _sha(snapshot_bytes),
            "license": snapshot["source"].get("license"),
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
    hashes = {
        "train.jsonl": _write(
            output_dir / "train.jsonl", rows if split == "train" else []
        ),
        "eval.jsonl": _write(
            output_dir / "eval.jsonl", rows if split == "eval" else []
        ),
        "sample_index.jsonl": _write(output_dir / "sample_index.jsonl", index_rows),
        "audit.jsonl": _write(output_dir / "audit.jsonl", audits),
        "rejects.jsonl": _write(output_dir / "rejects.jsonl", rejects),
        "source_manifest.jsonl": _write(
            output_dir / "source_manifest.jsonl", source_rows
        ),
    }
    manifest = {
        "schema": SCHEMA,
        "source_group": group,
        "split": split,
        "table_title": table_title,
        "domain": domain,
        "topic": topic,
        "snapshot_sha256": _sha(snapshot_bytes),
        "candidate_universe_rows": len(parsed.eligible) if parsed else 0,
        "format_excluded_rows": len(parsed.format_excluded) if parsed else 0,
        "independent_tasks": len({row["task_id"] for row in index_rows}),
        "candidate_rows": len(rows),
        "answer_cardinalities": sorted(
            {json.loads(row["messages"][1]["content"])["count"] for row in rows}
        ),
        "final_token_range": (
            [
                min(row["full_chat_tokens"] for row in index_rows),
                max(row["full_chat_tokens"] for row in index_rows),
            ]
            if index_rows
            else None
        ),
        "rejected_rows": len(rejects),
        "quality_status": "research_candidate" if rows else "source_rejected",
        "train_ready": False,
        "training_release_eligible": False,
        "dependency_scope": "complete named-table plain-year scan and insertion replay; unrestricted prose equivalence and independent model reader unchecked",
        "files_sha256": hashes,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--table-title", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--split", choices=("train", "eval"), required=True)
    parser.add_argument("--max-tasks", type=int, default=8)
    args = parser.parse_args()
    result = export(
        args.snapshot,
        args.output_dir,
        table_title=args.table_title,
        domain=args.domain,
        topic=args.topic,
        split=args.split,
        max_tasks=args.max_tasks,
    )
    print(
        _dump(
            {
                key: result[key]
                for key in (
                    "source_group",
                    "independent_tasks",
                    "candidate_rows",
                    "candidate_universe_rows",
                    "final_token_range",
                    "rejected_rows",
                    "train_ready",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
