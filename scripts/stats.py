#!/usr/bin/env python3
"""Difficulty histograms and diversity report over generated JSONL."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    args = ap.parse_args()
    files = [args.data / "train.jsonl", args.data / "eval.jsonl"]
    rows = []
    for p in files:
        if p.exists():
            rows.extend(load_jsonl(p))
    if not rows:
        raise SystemExit(f"no jsonl in {args.data}")

    def count(key):
        return Counter(r[key] for r in rows)

    dist_tokens = Counter()
    for r in rows:
        t = r["difficulty"]["context_tokens"]
        if t < 4000:
            dist_tokens["<4k"] += 1
        elif t < 12000:
            dist_tokens["8k-ish"] += 1
        elif t < 40000:
            dist_tokens["32k-ish"] += 1
        elif t < 80000:
            dist_tokens["64k-ish"] += 1
        else:
            dist_tokens["128k+"] += 1

    questions = {
        (r["world_id"], r["query_id"], r["query_timing"], r["position_bucket"])
        for r in rows
    }
    report = {
        "n_rows": len(rows),
        "n_question_slots": len(questions),
        "n_worlds": len({r["world_id"] for r in rows}),
        "by_split": count("split"),
        "by_view": count("view"),
        "by_query_type": count("query_type"),
        "by_timing": count("query_timing"),
        "by_position": count("position_bucket"),
        "by_length": count("length_bucket"),
        "by_cf_op": count("cf_op"),
        "token_buckets": dict(dist_tokens),
        "mean_proof_depth": sum(r["difficulty"]["proof_depth"] for r in rows)
        / len(rows),
        "mean_evidence_distance": sum(
            r["difficulty"]["max_evidence_distance"] for r in rows
        )
        / len(rows),
        "n_unique_full_answers": len(
            {r["answer"] for r in rows if r.get("view") == "full"}
        ),
        "n_clone_rows": sum(1 for r in rows if r.get("n_clones", 0)),
        "by_domain": dict(Counter(r.get("domain") or "?" for r in rows)),
        "by_motif": dict(Counter(r.get("motif") or "?" for r in rows)),
        "n_topology_ids": len({r.get("topology_id") for r in rows}),
        "n_topology_families": len({r.get("topology_family") for r in rows}),
        "by_length_distance": {
            b: round(
                sum(
                    r["difficulty"]["max_evidence_distance"]
                    for r in rows
                    if r.get("length_bucket") == b
                )
                / max(1, sum(1 for r in rows if r.get("length_bucket") == b)),
                1,
            )
            for b in sorted({r.get("length_bucket") for r in rows})
        },
    }
    out = args.data / "stats.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
