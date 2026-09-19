#!/usr/bin/env python3
"""Meta-feature predictability: can answer classes be read off the ledger?

The shortcut battery's second instrument: the answer's verdict class must not
be predictable from ledger metadata (renderer, family, record count, seed
band, token target) — if a feature reliably predicts the answer, a model can
shortcut content reading entirely. Reports per-feature class-balance and the
majority-share a naive classifier would reach, per answer-verb family where
applicable. An audit measurement, not a gate.

Usage:
  python scripts/measure_meta_feature_predictability.py BANK [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def answer_class(row: dict) -> str | None:
    """A coarse answer verdict class where one exists (GT/LT/EQ, labels...)."""
    answer = row["messages"][1]["content"]
    if row["family"] == "group_compare":
        for verdict in ("GT", "LT", "EQ"):
            if f'"{verdict}"' in answer:
                return verdict
    if row["family"] == "rule_holdout":
        start = answer.find('"label_')
        if start >= 0:
            return answer[start + 1 : answer.find('"', start + 1)]
    if row["family"] == "join_unanswerable":
        return "UNKNOWN" if "UNKNOWN" in answer else "determined"
    return None


def majority_share(pairs: list[tuple[str, str]]) -> float | None:
    """Share of the majority answer class within one meta-feature bucket.

    total counts ROWS (sum of bucket sizes), not distinct classes —
    len(counter) counts distinct answer classes per bucket and would
    divide by the wrong denominator when classes repeat.
    """
    buckets = defaultdict(Counter)
    for feature, answer in pairs:
        buckets[feature][answer] += 1
    total = sum(sum(counter.values()) for counter in buckets.values())
    if not total:
        return None
    correct = sum(counter.most_common(1)[0][1] for counter in buckets.values())
    return correct / total


def entropy_bits(counter: Counter) -> float:
    total = sum(counter.values())
    return -sum((n / total) * math.log2(n / total) for n in counter.values() if n)


def measure(bank: Path) -> dict:
    rows = []
    for split in ("train", "eval"):
        path = bank / f"{split}.jsonl"
        if path.exists():
            for line in path.open():
                rows.append(json.loads(line))
    report: dict = {"bank": str(bank), "rows": len(rows)}
    features = {
        "family": lambda r: r["family"],
        "depth": lambda r: str(r["depth"]),
        "token_target_band": lambda r: (
            "8k"
            if r["target"]["token_target"] <= 8192
            else "32k"
            if r["target"]["token_target"] <= 32768
            else "128k"
        ),
        "length_band": lambda r: (
            "L<=300"
            if r["length_records"] <= 300
            else "L<=1000"
            if r["length_records"] <= 1000
            else "L>1000"
        ),
        "seed_band": lambda r: str(r["world_seed"] // 1000),
    }
    for name, get in features.items():
        pairs = [(get(row), answer_class(row)) for row in rows]
        pairs = [(feature, answer) for feature, answer in pairs if answer is not None]
        share = majority_share(pairs)
        baseline = (
            max(Counter(answer for _, answer in pairs).values()) / len(pairs)
            if pairs
            else None
        )
        report[name] = {
            "answer_rows": len(pairs),
            "majority_share_within_buckets": round(share, 4) if share else None,
            "global_majority_baseline": round(baseline, 4) if baseline else None,
            "leak_above_baseline": round(share - baseline, 4)
            if share and baseline
            else None,
        }
    # family-level verdict balance (a wildly imbalanced class IS a meta-leak of family)
    balance = {}
    for fam in sorted({row["family"] for row in rows}):
        counter = Counter(
            answer_class(row)
            for row in rows
            if row["family"] == fam and answer_class(row)
        )
        if counter:
            balance[fam] = {
                "counts": dict(counter),
                "entropy_bits": round(entropy_bits(counter), 3),
            }
    report["verdict_balance_by_family"] = balance
    report["reading"] = (
        "leak_above_baseline > 0 means ledger metadata alone beats the global "
        "class prior — the answer is partially readable without the content"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = measure(args.bank)
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
