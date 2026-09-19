#!/usr/bin/env python3
"""Wrong-context baseline: a foreign world's context must not yield the gold.

The shortcut-reduction battery's one instrument with no literature precedent
(faithfulness QA twins swap entities WITHIN one document; nothing probes a
fully foreign context). The check is an audit measurement, not a gate: for a
sample of tasks, solve each task's QUESTION against a DIFFERENT world of the
same family and record how often the executor still returns the stored gold.

A nonzero rate is the signal to read, not to threshold on: it means the
question alone (its constants, its phrasing) leaks the answer for some
worlds — the same channel as question-only/closed-book probes, seen from the
context side.

Usage:
  python scripts/measure_wrong_context_baseline.py BANK [--sample N] [--json]
Samples N tasks (default 200, deterministic), pairs each with the next
sampled same-family foreign world, re-solves, reports the match rate.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_capability_records import canonical, module_for


def load_bank(bank: Path) -> dict[str, list[dict]]:
    """world_id -> its tasks with question and gold, plus the stored context."""
    manifest = json.loads((bank / "manifest.json").read_text())
    worlds = []
    for receipt in manifest["shards"]:
        shard = bank / "shards" / receipt["shard_id"]
        bundle = json.loads((shard / "world.json").read_text())
        rows = [
            json.loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        if not rows:
            continue
        worlds.append(
            {
                "world_id": bundle["world_id"],
                "family": rows[0]["family"],
                "context": bundle["context"],
                "tasks": [
                    {
                        "task_id": row["example_id"].split(":")[-1],
                        "question": next(
                            t["question"]
                            for t in bundle["tasks"]
                            if t["task_id"] == row["example_id"].split(":")[-1]
                        ),
                        "gold": row["messages"][1]["content"],
                    }
                    for row in rows
                ],
            }
        )
    return worlds


def measure(bank: Path, sample: int) -> dict:
    worlds = load_bank(bank)
    by_family = defaultdict(list)
    for world in worlds:
        by_family[world["family"]].append(world)
    rng = random.Random(20260919)
    checked = matched = 0
    per_family = defaultdict(lambda: [0, 0])
    for family in sorted(by_family):
        pool = by_family[family]
        if len(pool) < 2:
            continue
        probes = [(world, task) for world in pool for task in world["tasks"]]
        rng.shuffle(probes)
        for world, task in probes[: max(1, sample // len(by_family))]:
            # The foreign world: the next same-family world in a shuffled ring.
            foreign = pool[(pool.index(world) + 1) % len(pool)]
            if foreign["world_id"] == world["world_id"]:
                continue
            module = module_for(family)
            try:
                answer = module.solve_visible(foreign["context"], task["question"])
            except (ValueError, KeyError, IndexError, TypeError):
                # The question does not even execute against the foreign world
                # (constants out of domain): no leak, not an error.
                checked += 1
                continue
            checked += 1
            per_family[family][0] += 1
            if canonical(answer) == task["gold"]:
                matched += 1
                per_family[family][1] += 1
    return {
        "bank": str(bank),
        "checked": checked,
        "gold_leaks": matched,
        "leak_rate": round(matched / checked, 4) if checked else None,
        "per_family": {
            family: {"checked": c, "gold_leaks": m}
            for family, (c, m) in sorted(per_family.items())
        },
        "reading": (
            "a leak means the question alone determines the stored gold for a "
            "foreign same-family world — the context-independent channel, "
            "complementary to the question-only probe"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = measure(args.bank, args.sample)
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
