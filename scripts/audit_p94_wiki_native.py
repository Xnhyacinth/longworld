"""Replay P94 Wiki native answers and the exact final assistant loss mask."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts.audit_unified_reader_mask import audit_reader

SCHEMA = "longworld.p94-wiki-native-mask-audit.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(native_dir: Path, kind: str) -> dict:
    if kind not in {"pair", "generic_year", "source_pool"}:
        raise ValueError("unknown native Wiki kind")
    manifest_path = native_dir / "manifest.json"
    native_manifest = json.loads(manifest_path.read_text())
    index_rows = _rows(native_dir / "sample_index.jsonl")
    if len({row["example_id"] for row in index_rows}) != len(index_rows):
        raise ValueError("native index example IDs repeat")
    audit_rows = _rows(native_dir / "audit.jsonl")
    by_audit = {row["example_id"]: row for row in audit_rows}
    if len(by_audit) != len(audit_rows):
        raise ValueError("native audit IDs repeat")
    reader_files = {
        split: _rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
    }
    readers = {row["example_id"]: row for rows in reader_files.values() for row in rows}
    physical_split = {
        row["example_id"]: split for split, rows in reader_files.items() for row in rows
    }
    if len(readers) != sum(len(rows) for rows in reader_files.values()):
        raise ValueError("native reader IDs repeat")
    if {row["example_id"] for row in index_rows} != set(readers) or set(readers) != set(
        by_audit
    ):
        raise ValueError("native reader/index/audit inventories differ")
    tokenizer = get_tokenizer()
    checks = []
    for index in index_rows:
        sample_id = index["example_id"]
        reader, row_audit = readers[sample_id], by_audit[sample_id]
        if physical_split[sample_id] != index["split"]:
            raise ValueError("native reader physical split differs from index")
        row_kind = (
            "pair"
            if kind == "source_pool"
            and row_audit.get("task_type") == "table_pair_earlier_year"
            else kind
        )
        expected = (
            row_audit["oracle_replay"]["supervised_answer"]
            if row_kind == "pair"
            else row_audit["answer"]
            if row_kind == "generic_year"
            else row_audit["value_blind_reader_parser_answer"]
        )
        if row_kind == "pair" and (
            row_audit["oracle_replay"]["program_answer"] != expected
            or row_audit["oracle_replay"]["value_blind_reader_parser_answer"]
            != expected
            or row_audit["reader_text_intervention"]["status"]
            != "scoped_table_parser_year_cells_removed"
            or row_audit["reader_text_intervention"]["deleted_cell_count"] != 2
        ):
            raise ValueError("pair answer or reader intervention differs")
        if kind == "generic_year" and not row_audit["intervention"].get("hit_answer"):
            raise ValueError("generic table intervention missing")
        if kind == "source_pool":
            if row_kind == "pair":
                pass  # The two-cell deletion and both oracle replays were checked above.
            elif row_audit["task_type"] == "table_cell_lookup":
                if (
                    row_audit["reader_text_intervention"]["status"]
                    != "scoped_named_table_cell_removed"
                ):
                    raise ValueError("lookup reader deletion is not certified")
            elif row_audit["task_type"] == "dense_table_interval_scan":
                intervention = row_audit["insertion_intervention"]
                if (
                    intervention["status"] != "scoped_table_insertion_replay"
                    or intervention["near_miss_answer"] != expected
                    or intervention["hit_answer"]["count"] != expected["count"] + 1
                ):
                    raise ValueError("scan reader insertion is not certified")
            else:
                raise ValueError("unexpected source-pool task type")
        content = reader["messages"][1]["content"]
        try:
            actual = json.loads(content)
        except json.JSONDecodeError:
            actual = content
        if actual != expected:
            raise ValueError("assistant answer differs from native oracle")
        if (
            index.get("token_measurement", "pinned-chat-template")
            != "pinned-chat-template"
        ):
            raise ValueError("native tokenizer profile differs")
        normalized = {
            **index,
            "sample_id": sample_id,
            "operation": index.get("operation", index.get("task_type")),
            "answer_sha256": _answer_hash(reader["messages"][1]["content"]),
            "tokenizer_profile": "pinned-chat-template",
        }
        checked = audit_reader(
            {"sample_id": sample_id, "messages": reader["messages"]},
            normalized,
            tokenizer,
            131072,
        )
        checks.append(checked)
    if len(checks) != native_manifest["candidate_views"]:
        raise ValueError("native candidate count differs from replay")
    receipt = {
        "schema_version": SCHEMA,
        "source_manifest_sha256": _sha(manifest_path),
        "source_index_sha256": _sha(native_dir / "sample_index.jsonl"),
        "source_audit_sha256": _sha(native_dir / "audit.jsonl"),
        "checked_rows": len(checks),
        "splits": dict(sorted(Counter(row["split"] for row in checks).items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in checks),
        "supervised_tokens": sum(row["supervised_tokens"] for row in checks),
        "tokenizer": {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
        "reader_sha256": {row["sample_id"]: row["reader_sha256"] for row in checks},
        "scope": "native_oracle_and_scoped_intervention_plus_training_assistant_mask",
        "train_ready": False,
    }
    path = native_dir / "mask_audit.json"
    if path.exists():
        if json.loads(path.read_text()) != receipt:
            raise ValueError("existing native mask audit drift")
    else:
        path.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument(
        "--kind", choices=("pair", "generic_year", "source_pool"), required=True
    )
    args = parser.parse_args()
    print(
        json.dumps(
            audit(args.native_dir, args.kind), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
