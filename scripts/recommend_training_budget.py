#!/usr/bin/env python3
"""Derive a training budget recommendation from a bank's row count.

The P64 collapse came with a fixed 680x16 budget over a 1,953-row corpus
(5.57 epochs, 989 exposures per document). This helper makes the budget a
function of the corpus so that mistake cannot recur silently: it reports
the step count at the 1.0-epoch and 1.5-epoch red line for a given GBS,
plus the shape-exposure the gate will compute at those budgets.

Usage:
  python scripts/recommend_training_budget.py BANK [--gbs 16] [--json]
  where BANK is a directory with train.jsonl (reads the row count).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mask_shape(text: str) -> str:
    text = re.sub(r'"[^"]*"', '"S"', text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "N", text)
    return re.sub(r"\s+", " ", text).strip()


def derive_budget_report(train_paths: list[Path], gbs: int) -> dict:
    """The budget report over explicit train files; the CLI and the runner
    manifest share this one derivation so the numbers cannot drift apart."""
    train_files = [p for p in train_paths if p.is_file()]
    if not train_files:
        raise FileNotFoundError(f"no train files among {[str(p) for p in train_paths]}")
    rows = 0
    shapes: set[str] = set()
    for train in train_files:
        with train.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                rows += 1
                answer = next(
                    (m["content"] for m in d["messages"] if m["role"] == "assistant"),
                    "",
                )
                shapes.add(mask_shape(answer))
    return {
        "train_files": [f.name for f in train_files],
        "train_rows": rows,
        "distinct_answer_shapes": len(shapes),
        "gbs": gbs,
        "budget_recommendation": {
            "steps_at_1_epoch": max(1, round(rows / gbs)),
            "steps_at_1_5_epoch": max(1, round(rows * 1.5 / gbs)),
            "shape_exposure_at_1_epoch": round(rows / max(1, len(shapes)), 2),
            "shape_exposure_at_1_5_epoch": round(1.5 * rows / max(1, len(shapes)), 2),
        },
        "warning": (
            "shape-exposure at the 1.5-epoch red line exceeds 10"
            if 1.5 * rows / max(1, len(shapes)) > 10
            else "within the gate's shape-exposure band at 1.5 epochs"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--gbs", type=int, default=16)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    train_files = [args.bank / "train.jsonl"] + sorted(args.bank.glob("*_train.jsonl"))
    try:
        report = derive_budget_report(train_files, args.gbs)
    except FileNotFoundError as error:
        print(f"missing train.jsonl under {args.bank} ({error})", file=sys.stderr)
        return 2
    report = {"bank": str(args.bank), **report}
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
