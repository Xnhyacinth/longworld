#!/usr/bin/env python3
"""Backfill a sample_index.jsonl onto an existing, finalized record bank.

The runner writes the index at finalize time (P71); banks generated before
that (p69_validation_wave_v1, p70_superset_v1/v2) stay byte-identical and get
their index here instead — the same row shape the runner emits, derived from
the same stored shards. rule_structure_id comes from each shard's own
world.json (the plan's rule block), never re-derived from the seed: v2's
structure was seed % 2, but that coupling is a v2 property, not a law.

Usage:
  python scripts/build_record_bank_index.py BANK [--gbs 16]
writes BANK/sample_index.jsonl and prints the budget report for the bank.
The bank's manifest.json and state.json are never modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.recommend_training_budget import derive_budget_report
from scripts.run_capability_records import (
    _task_program_signature,
    canonical,
    sha,
)


def load_receipts(bank: Path) -> list[dict]:
    """Receipts from the manifest, in the order the export used (shard_id)."""
    manifest = json.loads((bank / "manifest.json").read_text())
    receipts = manifest["shards"]
    for receipt in receipts:
        shard = bank / "shards" / receipt["shard_id"]
        source = shard / "rows.jsonl"
        if sha(source) != receipt["files"]["rows.jsonl"]:
            raise RuntimeError(f"shard {receipt['shard_id']} changed under its receipt")
    return receipts


def rule_families_from_worlds(
    bank: Path, receipts: list[dict]
) -> dict[int, str | None]:
    """world_seed -> rule structure, read from each shard's own world.json."""
    mapping: dict[int, str | None] = {}
    for receipt in receipts:
        shard = bank / "shards" / receipt["shard_id"]
        bundle = json.loads((shard / "world.json").read_text())
        rule = bundle.get("rule") or {}
        mapping[receipt["world_seed"]] = rule.get("rule_family")
    return mapping


def build_index(bank: Path) -> dict:
    receipts = load_receipts(bank)
    rule_families = rule_families_from_worlds(bank, receipts)
    path = bank / "sample_index.jsonl"
    if path.exists():
        raise ValueError(f"{path} already exists; banks are write-once")
    rows = 0
    splits = {"train": 0, "eval": 0}
    groups = set()
    with path.open("x") as stream:
        for receipt in receipts:
            shard = bank / "shards" / receipt["shard_id"]
            for line in (shard / "rows.jsonl").read_text().splitlines():
                row = json.loads(line)
                index_row = {
                    "example_id": row["example_id"],
                    "semantic_task_id": row["example_id"],
                    "world_id": row["world_id"],
                    "group_id": row["world_id"],
                    "family": row["family"],
                    "rule_structure_id": rule_families.get(row["world_seed"]),
                    "program_signature": _task_program_signature(row),
                    "language": "en",
                    "renderer": "jsonl",
                    "length_records": row["length_records"],
                    "depth": row["depth"],
                    "consumed_records": row["consumed_records"],
                    "token_target": row["target"]["token_target"],
                    "input_tokens": row["input_tokens"],
                    "supervised_tokens": row["supervised_tokens"],
                    "full_message_tokens": row["full_chat_tokens"],
                    "output_file": f"{row['split']}.jsonl",
                    "row_index": rows,
                    "split": row["split"],
                    "admission_status": "completed",
                }
                stream.write(canonical(index_row) + "\n")
                rows += 1
                splits[row["split"]] += 1
                groups.add(row["world_id"])
    return {
        "bank": str(bank),
        "index": "sample_index.jsonl",
        "rows": rows,
        "split_rows": splits,
        "distinct_group_ids": len(groups),
        "group_semantics": (
            "group_id = world_id: the simulated world is the document analog; "
            "exposure measures world atomicity, not source breadth"
        ),
        "index_sha256": sha(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--gbs", type=int, default=16)
    args = parser.parse_args()
    report = build_index(args.bank)
    report["budget_report"] = derive_budget_report(
        [args.bank / "train.jsonl"], args.gbs
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
