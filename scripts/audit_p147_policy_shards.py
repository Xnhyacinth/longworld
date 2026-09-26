"""Cross-audit P142 and P147 policy shards without joining them to reader QA."""

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

from scripts.audit_p142_two_step_agentic import audit as audit_one

SCHEMA = "longworld.p147-policy-cross-audit.v1"
DEFAULT_SHARDS = (
    ROOT / "data/candidates/p142_two_step_agentic_pilot_v1",
    ROOT / "data/candidates/p147_two_step_p144_batch_000_v1",
    ROOT / "data/candidates/p147_two_step_p144_batch_001_v1",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit(shards: tuple[Path, ...]) -> dict[str, Any]:
    if len(shards) != 3 or len(set(shards)) != 3:
        raise ValueError("P147 cross audit needs three distinct policy shards")
    summaries = {}
    source_worlds: set[str] = set()
    source_tasks: set[tuple[str, str]] = set()
    traces: set[str] = set()
    source_splits: dict[str, set[str]] = defaultdict(set)
    coverage = Counter()
    length_bins = Counter()
    rejection_reasons = Counter()
    full_chat = supervised = 0
    for shard in shards:
        manifest = json.loads((shard / "manifest.json").read_text())
        independent = audit_one(shard)
        name = shard.name
        if (
            manifest["traces"] != independent["traces"]
            or manifest["stage_rows"] != independent["stage_rows"]
        ):
            raise ValueError("independent shard audit count differs")
        proofs = _rows(shard / "trajectory_proofs.jsonl")
        index = _rows(shard / "sample_index.jsonl")
        stage_one = {row["trace_id"]: row for row in index if row["stage"] == 1}
        if len(stage_one) != len(proofs):
            raise ValueError("duplicate stage-one trace in shard")
        shard_worlds = {row["world_id"] for row in proofs}
        shard_tasks = {(row["world_id"], row["source_task_id"]) for row in proofs}
        shard_traces = {row["trace_id"] for row in proofs}
        if (
            source_worlds & shard_worlds
            or source_tasks & shard_tasks
            or traces & shard_traces
            or len(shard_traces) != len(proofs)
        ):
            raise ValueError("P142/P147 policy world, task, or trace overlap")
        source_worlds.update(shard_worlds)
        source_tasks.update(shard_tasks)
        traces.update(shard_traces)
        for proof in proofs:
            first = stage_one[proof["trace_id"]]
            source_splits[proof["world_id"]].add(first["split"])
            coverage[(proof["mechanism"], first["length_records"], first["split"])] += 1
            tokens = first["full_chat_tokens"]
            length_bins[
                "<32K"
                if tokens < 32768
                else "32-64K"
                if tokens < 65536
                else "64-128K"
                if tokens < 131072
                else "128-256K"
            ] += 1
        ledger = _rows(shard / "attempt_ledger.jsonl")
        rejection_reasons.update(
            row["reason"] for row in ledger if row["status"] == "rejected"
        )
        full_chat += manifest["full_chat_tokens"]
        supervised += manifest["supervised_tokens"]
        summaries[name] = {
            "manifest_sha256": _sha(shard / "manifest.json"),
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "source_worlds_attempted": manifest["source_worlds_attempted"],
            "source_worlds_accepted": independent["source_worlds"],
            "traces": independent["traces"],
            "stage_rows": independent["stage_rows"],
            "first_actions": independent["first_actions"],
            "full_chat_tokens": manifest["full_chat_tokens"],
            "supervised_tokens": manifest["supervised_tokens"],
            "minimum_event_to_first_query_tokens": manifest[
                "minimum_event_to_first_query_tokens"
            ],
            "rejections": manifest["rejections"],
            "independent_audit_status": independent["status"],
        }
    if any(len(splits) != 1 for splits in source_splits.values()):
        raise ValueError("policy source world crosses train/eval across shards")
    return {
        "schema_version": SCHEMA,
        "status": "three_policy_shards_independently_replayed_and_disjoint",
        "train_ready": False,
        "training_contract": "two_step_simulated_policy_candidate_only",
        "shards": summaries,
        "independent_source_worlds": len(source_worlds),
        "independent_source_tasks": len(source_tasks),
        "traces": len(traces),
        "stage_rows": 2 * len(traces),
        "source_world_overlap": 0,
        "source_task_overlap": 0,
        "trace_overlap": 0,
        "source_split_overlap": 0,
        "coverage": {str(key): value for key, value in sorted(coverage.items())},
        "first_stage_length_bins": dict(sorted(length_bins.items())),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "full_chat_tokens": full_chat,
        "supervised_tokens": supervised,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", nargs=3, type=Path, default=DEFAULT_SHARDS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(tuple(args.shards))
    content = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(content, end="")


if __name__ == "__main__":
    main()
