#!/usr/bin/env python3
"""Measure the realized verdict distribution of a record-world bank.

The collapse gates measure answer-SHAPE uniqueness but nothing measures
verdict guessability: a group_compare bank whose verdicts are 90% GT
passes every current gate. This closes that blind spot (Oolong's
analytic-baseline idea; our generator samples the verdict uniformly then
builds it, and validate re-executes gold, but the REALIZED marginal was
never reported). Outputs the verdict histogram, the majority-verdict
baseline (guess-the-plurality accuracy), and the uniform baseline.

Usage:
  python scripts/measure_verdict_marginal.py BANK/train.jsonl [--json]
  Extracts verdicts from group_compare answers ({"verdict": ...} key).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def extract_verdict(answer_text: str) -> str | None:
    try:
        answer = json.loads(answer_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(answer, dict) and "verdict" in answer:
        verdict = answer["verdict"]
        return verdict if isinstance(verdict, str) else None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("train", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    verdicts: list[str] = []
    rows = 0
    with args.train.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows += 1
            row = json.loads(line)
            answer = next(
                (m["content"] for m in row["messages"] if m["role"] == "assistant"),
                "",
            )
            verdict = extract_verdict(answer)
            if verdict is not None:
                verdicts.append(verdict)

    if not verdicts:
        print(json.dumps({"rows": rows, "verdicts": 0}, indent=2 if args.json else None))
        return 0

    counts = Counter(verdicts)
    n = len(verdicts)
    majority = counts.most_common(1)[0]
    uniform = 1.0 / len(counts)
    report = {
        "rows": rows,
        "verdict_rows": n,
        "verdict_histogram": dict(sorted(counts.items())),
        "majority_verdict_baseline": round(majority[1] / n, 4),
        "uniform_baseline": round(uniform, 4),
        "verdict_margin_over_uniform": round(
            majority[1] / n - uniform, 4
        ),
        "warning": (
            "majority-verdict baseline exceeds 0.45: verdicts are guessable"
            if majority[1] / n > 0.45
            else "verdicts not guessable by plurality"
        ),
    }
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
