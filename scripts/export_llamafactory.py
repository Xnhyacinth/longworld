#!/usr/bin/env python3
"""Export CausalTwin rows to LLaMA-Factory ShareGPT + equal-token shards.

B1–B4 are ablations. B5 is the method (four-view + query-late). B5w adds
document-level distance upsampling (EXACT-lite exposure, Route 3).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COND_VIEWS = {
    "B1": {"full"},
    "B2": {"full", "minimal"},
    "B3": {"full", "cf"},
    "B4": {"full", "minimal", "cf", "distractor_only"},
    "B5": {"full", "minimal", "cf", "distractor_only", "trajectory", "memory"},
    "B5w": {"full", "minimal", "cf", "distractor_only", "trajectory", "memory"},
}

SYSTEM = (
    "You are a careful analyst of long internal records: contracts, lab notes, "
    "git objects, CI logs, and search snapshots. Use only the provided context. "
    "If the context is insufficient, reply exactly: unanswerable"
)


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def filter_rows(
    rows: list[dict], condition: str, train_buckets: set[str]
) -> list[dict]:
    views = COND_VIEWS[condition]
    out = [
        r
        for r in rows
        if r["view"] in views
        and r.get("length_bucket", "8k") in train_buckets
        and "decoy" not in r.get("query_id", "")
    ]
    if condition in {"B1", "B2", "B3", "B4"}:
        first = [r for r in out if r["query_timing"] == "first"]
        if first:
            out = first
    return out


def to_sharegpt(row: dict) -> dict:
    return {
        "conversations": [
            {"from": "human", "value": row["context"]},
            {"from": "gpt", "value": str(row["answer"])},
        ],
        "system": SYSTEM,
        "world_id": row["world_id"],
        "query_id": row["query_id"],
        "view": row["view"],
        "query_type": row["query_type"],
        "query_timing": row["query_timing"],
        "length_bucket": row.get("length_bucket"),
        "evidence_distance": row.get("difficulty", {}).get("max_evidence_distance", 0),
    }


def token_est(row: dict) -> int:
    return int(row["difficulty"]["context_tokens"]) + max(
        1, len(str(row["answer"])) // 4
    )


def distance_repeats(row: dict) -> int:
    """EXACT-lite: upsample long-span unique evidence, cap at 3 copies."""
    if row.get("view") not in {"full", "trajectory", "cf"}:
        return 1
    d = int(row.get("difficulty", {}).get("max_evidence_distance") or 0)
    t = max(1, int(row.get("difficulty", {}).get("context_tokens") or 1))
    return max(1, min(3, 1 + int(2 * d / t)))


def write_condition(
    rows: list[dict], dest: Path, token_budget: int | None, upsample: bool
) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    used = 0
    n = 0
    by_view: Counter = Counter()
    by_len: Counter = Counter()
    by_timing: Counter = Counter()
    out_rows: list[dict] = []
    for r in rows:
        copies = distance_repeats(r) if upsample else 1
        for _ in range(copies):
            t = token_est(r)
            if token_budget is not None and used + t > token_budget and n > 0:
                break
            out_rows.append(to_sharegpt(r))
            used += t
            n += 1
            by_view[r["view"]] += 1
            by_len[r.get("length_bucket", "?")] += 1
            by_timing[r.get("query_timing", "?")] += 1
        else:
            continue
        break
    dest.write_text(json.dumps(out_rows, ensure_ascii=False) + "\n")
    return {
        "n": n,
        "tokens_est": used,
        "path": str(dest),
        "by_view": dict(by_view),
        "by_length": dict(by_len),
        "by_timing": dict(by_timing),
        "upsample": upsample,
    }


def write_dataset_info(out_dir: Path, conditions: list[str]) -> None:
    info = {}
    for c in conditions:
        info[f"causaltwin_{c.lower()}"] = {
            "file_name": f"{c}.json",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
    (out_dir / "dataset_info.json").write_text(json.dumps(info, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument(
        "--out-dir", type=Path, default=ROOT / "data" / "sft" / "llamafactory"
    )
    ap.add_argument("--token-budget", type=int, default=None)
    ap.add_argument(
        "--train-buckets",
        default="8k,32k,64k",
        help="comma-separated length buckets used in SFT",
    )
    ap.add_argument("--conditions", default="B1,B2,B3,B4,B5,B5w")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    train_buckets = {b.strip() for b in args.train_buckets.split(",") if b.strip()}
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    src = args.data / "train.jsonl"
    if not src.exists():
        raise SystemExit(f"missing {src}; run scripts/generate.py first")
    rows = list(iter_jsonl(src))
    rng = random.Random(args.seed)
    rng.shuffle(rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    metas = []
    prepared: dict[str, list[dict]] = {}
    for c in conditions:
        prepared[c] = filter_rows(rows, c, train_buckets)
        if c == "B5w":
            prepared[c] = filter_rows(rows, "B5", train_buckets)

    # Equal-token: cap to the smallest condition after a first pass without cap.
    first_pass = {}
    for c in conditions:
        upsample = c == "B5w"
        first_pass[c] = write_condition(
            prepared[c], args.out_dir / f"{c}.json", args.token_budget, upsample
        )
    if args.token_budget is None and len(first_pass) > 1:
        cap = min(m["tokens_est"] for m in first_pass.values() if m["n"])
        for c in conditions:
            upsample = c == "B5w"
            meta = write_condition(
                prepared[c], args.out_dir / f"{c}.json", cap, upsample
            )
            meta["condition"] = c
            metas.append(meta)
            (args.out_dir / f"{c}.meta.json").write_text(json.dumps(meta, indent=2))
    else:
        for c, meta in first_pass.items():
            meta["condition"] = c
            metas.append(meta)
            (args.out_dir / f"{c}.meta.json").write_text(json.dumps(meta, indent=2))

    write_dataset_info(args.out_dir, conditions)
    budgets = [m["tokens_est"] for m in metas]
    spread = 0.0
    if budgets and max(budgets):
        spread = (max(budgets) - min(budgets)) / max(budgets)
    summary = {"conditions": metas, "token_spread": round(spread, 4)}
    (args.out_dir / "export_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if spread > 0.05:
        print(f"warning: token spread {spread:.2%} exceeds 5%", file=sys.stderr)


if __name__ == "__main__":
    main()
