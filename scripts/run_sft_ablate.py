#!/usr/bin/env python3
"""Prepare equal-token SFT shards for B1–B6 and optionally train them."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "sft")
    ap.add_argument("--token-budget", type=int, default=None)
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--train", action="store_true")
    ap.add_argument(
        "--conditions",
        default="B1,B2,B3,B4,B5",
        help="comma-separated conditions",
    )
    args = ap.parse_args()
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    metas = []
    for c in conditions:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "train_sft.py"),
            "--condition",
            c,
            "--data",
            str(args.data),
            "--out-dir",
            str(args.out_dir),
            "--max-steps",
            str(args.max_steps),
            "--prepare-only",
        ]
        if args.token_budget is not None:
            cmd.extend(["--token-budget", str(args.token_budget)])
        if args.smoke:
            cmd.append("--smoke")
        subprocess.check_call(cmd)
        metas.append(json.loads((args.out_dir / f"{c}.meta.json").read_text()))
    budgets = [m["tokens_est"] for m in metas]
    mx = max(budgets) if budgets else 1
    mn = min(budgets) if budgets else 1
    # Re-cap every condition to the minimum so B1–B5 share a token budget.
    cap = mn
    if args.token_budget is None and not args.smoke and len(set(budgets)) > 1:
        for c in conditions:
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "train_sft.py"),
                "--condition",
                c,
                "--data",
                str(args.data),
                "--out-dir",
                str(args.out_dir),
                "--token-budget",
                str(cap),
                "--max-steps",
                str(args.max_steps),
                "--prepare-only",
            ]
            subprocess.check_call(cmd)
        metas = [
            json.loads((args.out_dir / f"{c}.meta.json").read_text())
            for c in conditions
        ]
        budgets = [m["tokens_est"] for m in metas]
        mx = max(budgets)
        mn = min(budgets)
    spread = (mx - mn) / mx if mx else 0
    report = {
        "conditions": {
            m["condition"]: {"tokens_est": m["tokens_est"], "n": m["n"]} for m in metas
        },
        "token_spread": spread,
        "pass_5pct": spread < 0.05,
        "fairness": "same max_steps / batch / max_length; shards capped to min tokens_est",
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "equal_token_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if args.train:
        for c in conditions:
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "train_sft.py"),
                "--condition",
                c,
                "--data",
                str(args.data),
                "--out-dir",
                str(args.out_dir),
                "--max-steps",
                str(2 if args.smoke else args.max_steps),
            ]
            if args.smoke:
                cmd.append("--smoke")
            subprocess.check_call(cmd)


if __name__ == "__main__":
    main()
