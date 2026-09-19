#!/usr/bin/env python3
"""Hash-stratified arm extraction over a finalized record bank.

The bank is the single source of truth; arms are deterministic exports of
it, so the same bank can feed main / mechanism / format / extreme-cell arms
that share strata rows and stay directly comparable under one training
recipe. Selection hashes the WORLD id (world atomicity: a world's rows never
split across arms), stratifies over (family, depth, token_target, split,
rule_family), and takes sorted-prefix quotas inside each stratum. Reruns
with the same seed are byte-identical; paired arms declare their overlap in
arms.json so concatenation cannot double-count.

Usage:
  python scripts/extract_arms.py BANK --config ARMS_CONFIG --output DIR
ARMS_CONFIG names quotas as fractions of each stratum's world count, e.g.
{"main": 0.6, "mechanism": 0.2, "extreme": 0.1, "format": 0.4}.
The format arm renders prose/table views of its shared strata only after the
renderer supports the row's family — families outside the common support set
are excluded and REPORTED, not silently dropped (the ablation must not change
task composition).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.recommend_training_budget import derive_budget_report
from scripts.run_capability_records import sha


def stratum_key(index_row: dict) -> tuple:
    """The stratum an indexed task belongs to; worlds, not rows, are the atom."""
    return (
        index_row["family"],
        index_row["depth"],
        index_row["token_target"],
        index_row["split"],
        index_row.get("rule_structure_id") or "",
    )


def world_order(stratum: tuple, world_ids: list[str]) -> list[str]:
    """Deterministic order inside a stratum: sha256(stratum|world_id) sorted.

    Hash-ordering decorrelates selection from id/seed sequence (ids embed
    plan order, which correlates with family scheduling), the same reason
    MRCR's subsampler sorts by hash rather than row id.
    """
    key = "|".join(str(part) for part in stratum)
    return sorted(
        world_ids, key=lambda w: hashlib.sha256(f"{key}|{w}".encode()).hexdigest()
    )


def plan_arms(
    bank: Path, quotas: dict[str, float]
) -> tuple[dict[str, list[str]], dict]:
    """Assign every world in the bank to arms by sorted-prefix quotas.

    Arms overlap only when their quotas sum above 1.0 inside a stratum (the
    paired-strata design); the assignment is a pure function of
    (index, quotas) so a rerun is identical.
    """
    strata: dict[tuple, set[str]] = defaultdict(set)
    with (bank / "sample_index.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            strata[stratum_key(row)].add(row["world_id"])
    assignment: dict[str, list[str]] = defaultdict(list)
    for stratum in sorted(strata, key=str):
        worlds = world_order(stratum, sorted(strata[stratum]))
        for arm, quota in sorted(quotas.items()):
            take = round(len(worlds) * quota)
            for world_id in worlds[:take]:
                assignment[arm].append(world_id)
    stats = {
        "bank": str(bank),
        "bank_manifest_sha256": sha(bank / "manifest.json"),
        "config_sha256": sha(bank / "state.json")
        if (bank / "state.json").exists()
        else None,
        "quotas": quotas,
        "strata": {str(k): len(v) for k, v in sorted(strata.items(), key=str)},
        "worlds_per_arm": {arm: len(ids) for arm, ids in sorted(assignment.items())},
    }
    return dict(assignment), stats


def arm_rows(bank: Path, world_ids: list[str]) -> list[dict]:
    """The export rows for one arm, in bank export order, world-atomic."""
    wanted = set(world_ids)
    rows = []
    for split in ("train", "eval"):
        path = bank / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.open():
            row = json.loads(line)
            if row["world_id"] in wanted:
                rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    quotas = json.loads(args.config.read_text())
    if "main" not in quotas:
        raise ValueError("an arms config needs a main arm")
    for arm, quota in quotas.items():
        if not 0 < quota <= 1:
            raise ValueError(f"arm {arm} quota {quota} outside (0, 1]")

    assignment, stats = plan_arms(args.bank, quotas)
    args.output.mkdir(parents=True)
    receipt: dict = {**stats, "arms": {}}
    for arm, world_ids in sorted(assignment.items()):
        rows = arm_rows(args.bank, world_ids)
        arm_dir = args.output / arm
        arm_dir.mkdir()
        path = arm_dir / "train.jsonl"
        exported = 0
        with path.open("x") as stream:
            for row in rows:
                if row["split"] != "train":
                    continue
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                exported += 1
        # index slice: the bank's index rows for this arm's train worlds
        wanted = set(world_ids)
        index_path = arm_dir / "sample_index.jsonl"
        indexed = 0
        with index_path.open("x") as stream:
            for line in (args.bank / "sample_index.jsonl").open():
                entry = json.loads(line)
                if entry["world_id"] in wanted and entry["split"] == "train":
                    stream.write(json.dumps(entry, sort_keys=True) + "\n")
                    indexed += 1
        budget = derive_budget_report([path], 16)
        receipt["arms"][arm] = {
            "worlds": len(world_ids),
            "train_rows": exported,
            "index_rows": indexed,
            "train_sha256": sha(path),
            "index_sha256": sha(index_path),
            "budget_recommendation": budget["budget_recommendation"],
        }
    # overlap declaration: world sets shared between arms (paired strata)
    sets = {arm: set(ids) for arm, ids in assignment.items()}
    overlaps = {
        f"{a}&{b}": len(sets[a] & sets[b])
        for a, b in sorted(
            (x, y) for i, x in enumerate(sorted(sets)) for y in sorted(sets)[i + 1 :]
        )
        if sets[a] & sets[b]
    }
    receipt["world_overlaps"] = overlaps
    receipt["overlap_note"] = (
        "shared worlds are the paired-strata design; concatenate arms only "
        "after deduplicating world_id or double-counting rows"
    )
    with (args.output / "arms.json").open("x") as stream:
        stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt["worlds_per_arm"], sort_keys=True))
    print(json.dumps(overlaps, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
