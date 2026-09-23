#!/usr/bin/env python3
"""Generate the P73 shared-world pilot bank.

One world hosts MULTIPLE task families on one shared context (the original
project goal), covering L1-L4 without family-specific worlds. Uses the frozen
spine generators per family and merges rows into one context; answers are
re-solved on the shared rows. The output bank is a candidate pool for the
C (normal) vs D (witness-rich) arm split of the P73 experiment design.

Usage:
  python scripts/generate_p73_pilot.py --out data/capability_records/p73_shared_v1 [--worlds N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import capability_shared_world as sw  # noqa: E402

CONFIG = {
    "groups": [
        ("filter_aggregate", "group_compare", "join_lookup"),
        ("alias_locate", "asof_state", "rule_holdout", "set_complete"),
    ],
    "length_records": 800,
    "consumed_per_family": 20,
    "depth": 2,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worlds", type=int, default=42)
    args = parser.parse_args()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_out = []
    contexts_out = []
    worlds_out = []
    fam_counts: dict[str, int] = {}
    n_worlds = 0
    per_group = max(1, args.worlds // len(CONFIG["groups"]))
    for index, group in enumerate(CONFIG["groups"]):
        if not group:
            continue
        for w in range(per_group):
            seed = 861000 + index * 1000 + w
            try:
                world = sw.build_shared_world(
                    seed,
                    group,
                    CONFIG["length_records"],
                    CONFIG["consumed_per_family"],
                    CONFIG["depth"],
                )
            except ValueError as exc:
                print(f"skip seed {seed}: {exc}", file=sys.stderr)
                continue
            n_worlds += 1
            worlds_out.append(world)
            contexts_out.append(
                {"world_id": world["world_id"], "context": world["context"]}
            )
            for task in world["tasks"]:
                fam_counts[task["family"]] = fam_counts.get(task["family"], 0) + 1
                rows_out.append(
                    {
                        "schema_version": world["schema_version"],
                        "example_id": f"{world['world_id']}:{task['task_id']}",
                        "semantic_task_id": f"{world['world_id']}:{task['task_id']}",
                        "world_id": world["world_id"],
                        "group_id": world["world_id"],
                        "family": task["family"],
                        "capability_level": task.get("capability_level"),
                        "context_sha256": world["context_sha256"],
                        "context": world["context"],
                        "question": task["question"],
                        "instruction": task["instruction"],
                        "answer": task["answer"],
                        "consumed": task["consumed"],
                        "consumed_count": task["consumed_count"],
                        "shared_with": sorted(
                            set(
                                t["family"]
                                for t in world["tasks"]
                                if t["family"] != task["family"]
                            )
                        ),
                        "honesty": world["honesty"],
                    }
                )
    # Split by WORLD, not by row: one world lives in exactly one split (the
    # runner's split_for_seed convention, seed % 5). A row-level 80% cut lets
    # one world straddle train and eval — exposure leakage under the
    # group_id=world accounting the design's exposure gate uses. The modulo
    # rule also spreads eval worlds over both family groups, so every family
    # keeps eval rows.
    eval_worlds = {w["world_id"] for w in worlds_out if w["seed"] % 5 == 0}
    train = [r for r in rows_out if r["world_id"] not in eval_worlds]
    eval_rows = [r for r in rows_out if r["world_id"] in eval_worlds]
    for name, rows in (("train.jsonl", train), ("eval.jsonl", eval_rows)):
        with (out_dir / name).open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    # Per-family row view: the F-side family solvers need their own rows; store
    # the family-scoped rows for the audit (shared worlds are also written to
    # shards/<world>/world.json so the standard audit path works).
    for index, group in enumerate(CONFIG["groups"]):
        if not group:
            continue
        for w in range(per_group):
            seed = 861000 + index * 1000 + w
            shard_dir = out_dir / "shards" / f"shared-{index}-{w}"
            shard_dir.mkdir(parents=True, exist_ok=True)
            world2 = sw.build_shared_world(
                seed,
                group,
                CONFIG["length_records"],
                CONFIG["consumed_per_family"],
                CONFIG["depth"],
            )
            (shard_dir / "world.json").write_text(
                json.dumps(world2, ensure_ascii=False) + "\n"
            )
    manifest = {
        "worlds": n_worlds,
        "rows": len(rows_out),
        "families": dict(sorted(fam_counts.items())),
        "split_rows": {
            split: len(rows) for split, rows in (("train", train), ("eval", eval_rows))
        },
        "split_basis": (
            "world_seed_mod_5: seed % 5 == 0 -> eval; one world lives in exactly "
            "one split, so group_id=world exposure accounting stays leak-free"
        ),
        "shared_context": "one context per world, multiple families (see shared_with)",
        "honesty": {
            "source_kind": "simulated",
            "strict_long_dependency_verified": False,
            "model_utility_measured": False,
            "production_eligible": False,
        },
    }
    with (out_dir / "contexts.jsonl").open("w") as fh:
        for c in contexts_out:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"worlds={n_worlds} rows={len(rows_out)}")
    print(json.dumps(fam_counts, indent=0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
