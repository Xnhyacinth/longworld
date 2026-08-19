#!/usr/bin/env python3
"""Fail the generation run if clones, facts dumps, or empty long buckets leak in."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument("--min-retention", type=float, default=0.15)
    ap.add_argument("--max-retention", type=float, default=0.55)
    args = ap.parse_args()
    report = json.loads((args.data / "quality_report.json").read_text())
    errors: list[str] = []
    if len(report.get("by_domain") or {}) < 2 and int(report.get("n_worlds") or 0) >= 8:
        errors.append("need >=2 domains")
    if len(report.get("by_motif") or {}) < 5 and int(report.get("n_worlds") or 0) >= 8:
        errors.append("need >=5 motifs")
    if int(report.get("n_clones") or 0) > 0:
        errors.append(f"clones={report['n_clones']}")
    ret = float(report.get("retention") or 0)
    if ret < args.min_retention or ret > args.max_retention:
        errors.append(
            f"retention={ret} not in [{args.min_retention},{args.max_retention}]"
        )
    rows = []
    for name in ("train.jsonl", "eval.jsonl"):
        p = args.data / name
        if p.exists():
            rows.extend(iter_jsonl(p))
    facts = 0
    pads = 0
    by_len = Counter()
    dist_64 = []
    for r in rows:
        ctx = r.get("context") or ""
        if (
            "recorded facts" in ctx.lower()
            or "line items (authoritative)" in ctx.lower()
        ):
            facts += 1
        if "#pad" in ctx:
            pads += 1
        by_len[r.get("length_bucket", "?")] += 1
        if r.get("length_bucket") == "64k" and r.get("view") == "full":
            dist_64.append(int(r["difficulty"]["max_evidence_distance"]))
    if facts:
        errors.append(f"facts_blocks={facts}")
    if pads:
        errors.append(f"pad_clones_in_text={pads}")
    if "64k" not in by_len:
        errors.append("missing 64k bucket")
    if dist_64 and sum(dist_64) / len(dist_64) < 8000:
        errors.append(
            f"mean 64k evidence distance {sum(dist_64) / len(dist_64):.0f} < 8000"
        )
    out = {
        "ok": not errors,
        "errors": errors,
        "by_length": dict(by_len),
        "n_rows": len(rows),
        "retention": ret,
        "mean_64k_distance": (sum(dist_64) / len(dist_64)) if dist_64 else 0,
    }
    print(json.dumps(out, indent=2))
    if errors:
        raise SystemExit("quality gate failed: " + "; ".join(errors))


if __name__ == "__main__":
    main()
