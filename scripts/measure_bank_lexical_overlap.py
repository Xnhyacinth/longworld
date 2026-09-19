#!/usr/bin/env python3
"""Bank-level lexical overlap: NoLiMa's low-overlap discipline, measured.

The renderer's lexical_overlap (question-to-evidence unigram precision,
stopworded, dates/ids kept whole) existed only in tests; P68 §4.3 asked for
it as an audit from the first place ("从宣称变测量" — NoLiMa measures
R-1 0.069 where NIAH-style tasks measure 0.905). This walks a bank's shards,
computes the per-task overlap of the question against its consumed rows'
rendered jsonl lines, and reports the distribution per family and terminal.

Usage:
  python scripts/measure_bank_lexical_overlap.py BANK [--sample N] [--json]
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

from longworld.synthesis.capability_renderers import lexical_overlap


def measure(bank: Path, sample: int) -> dict:
    manifest = json.loads((bank / "manifest.json").read_text())
    rng = random.Random(20260919)
    shards = list(manifest["shards"])
    rng.shuffle(shards)
    by_family: dict[str, list[float]] = defaultdict(list)
    checked = 0
    for receipt in shards:
        shard = bank / "shards" / receipt["shard_id"]
        bundle = json.loads((shard / "world.json").read_text())
        rows = [
            json.loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        for row in rows:
            if checked >= sample:
                break
            # lexical_overlap(world, question_dict, fmt): the A5-extended
            # signature takes the stored world and the task's question (which
            # carries `consumed`), and renders evidence in the given format.
            task_id = row["example_id"].split(":")[-1]
            task = next(t for t in bundle["tasks"] if t["task_id"] == task_id)
            overlap = lexical_overlap(bundle["context"], task["question"], "jsonl")
            by_family[row["family"]].append(overlap)
            checked += 1
        if checked >= sample:
            break

    def stats(values: list[float]) -> dict:
        values = sorted(values)
        return {
            "n": len(values),
            "p50": round(values[len(values) // 2], 3),
            "max": round(values[-1], 3),
            "share_over_0.2": round(sum(v > 0.2 for v in values) / len(values), 3),
        }

    return {
        "bank": str(bank),
        "sampled": checked,
        "per_family": {fam: stats(vals) for fam, vals in sorted(by_family.items())},
        "anchor": "NoLiMa measures R-1 0.069 vs NIAH 0.905; lower is harder",
        "note": "alias locate_empty tasks consume one short declaration row, so their evidence denominator is small — high overlap there is a terminal property, reported separately by the renderer tests",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = measure(args.bank, args.sample)
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
