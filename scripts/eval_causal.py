#!/usr/bin/env python3
"""Causal diagnostic eval.

Gold engine metrics are computed by replay (CFR_gold = 1.0 by construction).
Optional --model-dir runs the trained (or base) model on a held-out 8k slice.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.engine import answer_from_artifacts
from longworld.core.sampler import materialize


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def exact(pred: str, gold: str) -> bool:
    p = pred.strip().splitlines()[0].strip() if pred.strip() else ""
    g = gold.strip()
    return p == g or p.endswith(g) or g in p


def engine_oracle_metrics(eval_path: Path) -> dict:
    rows = [r for r in iter_jsonl(eval_path) if r["view"] == "full"]
    worlds = sorted({r["seed"] for r in rows})
    stats = defaultdict(int)
    n = 0
    for seed in worlds:
        mat = materialize(seed, n_parallel=2)
        for spec in mat.queries:
            if "decoy" in spec.query_id:
                continue
            n += 1
            gold = spec.answer
            cf = spec.cf_answer
            stats["cfr_gold"] += int(cf != gold)
            focal = mat.artifacts["focal"]
            stats["acc_full_gold"] += int(
                answer_from_artifacts(mat.worlds["focal"], spec, focal) == gold
            )
            ess = [a for a in focal if a.artifact_id in spec.essential_artifact_ids]
            stats["mes_gold"] += int(
                answer_from_artifacts(mat.worlds["focal"], spec, ess) == gold
            )
            closed = answer_from_artifacts(mat.worlds["focal"], spec, [])
            stats["closed_book_acc_gold"] += int(closed == gold)
            non = [
                a for a in focal if a.artifact_id not in spec.essential_artifact_ids
            ][-2:]
            stats["local_window_acc_gold"] += int(
                answer_from_artifacts(mat.worlds["focal"], spec, non) == gold
            )
            dist = [
                a for a in focal if a.artifact_id not in spec.essential_artifact_ids
            ]
            stats["distractor_only_unanswerable_gold"] += int(
                answer_from_artifacts(mat.worlds["focal"], spec, dist) != gold
            )
    denom = max(n, 1)
    return {
        "n_questions": n,
        "n_worlds": len(worlds),
        "Acc_full_gold": stats["acc_full_gold"] / denom,
        "MES_gold": stats["mes_gold"] / denom,
        "CFR_gold": stats["cfr_gold"] / denom,
        "closed_book_acc_gold": stats["closed_book_acc_gold"] / denom,
        "local_window_acc_gold": stats["local_window_acc_gold"] / denom,
        "distractor_invariance_proxy_gold": stats["distractor_only_unanswerable_gold"]
        / denom,
    }


def load_model(model_dir: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()
    return tok, model


def generate(tok, model, prompt: str, max_new: int = 48) -> str:
    import torch

    ids = tok(prompt, return_tensors="pt", truncation=True, max_length=7800)
    ids = {k: v.to(model.device) for k, v in ids.items()}
    with torch.no_grad():
        out = model.generate(
            **ids,
            max_new_tokens=max_new,
            do_sample=False,
            pad_token_id=tok.pad_token_id,
        )
    text = tok.decode(out[0][ids["input_ids"].shape[1] :], skip_special_tokens=True)
    return text.strip()


def eval_model(eval_path: Path, model_dir: str, max_examples: int) -> dict:
    tok, model = load_model(model_dir)
    grouped: dict[tuple, dict] = {}
    for r in iter_jsonl(eval_path):
        if r.get("length_bucket") != "8k":
            continue
        if "decoy" in r.get("query_id", ""):
            continue
        if r.get("position_bucket") != "middle":
            continue
        key = (r["world_id"], r["query_type"], r["query_timing"])
        grouped.setdefault(key, {})[r["view"]] = r
    # Prefer query-first for the main table; keep some late.
    keys = [
        k for k in grouped if grouped[k].get("full", {}).get("query_timing") == "first"
    ]
    keys = keys[:max_examples]
    hits = defaultdict(int)
    n = 0
    by_type = defaultdict(lambda: defaultdict(int))
    for key in keys:
        views = grouped[key]
        if "full" not in views or "cf" not in views:
            continue
        n += 1
        qtype = key[1]
        full = views["full"]
        pred_full = generate(tok, model, full["context"])
        ok_full = exact(pred_full, full["answer"])
        hits["acc_full"] += int(ok_full)
        by_type[qtype]["n"] += 1
        by_type[qtype]["acc_full"] += int(ok_full)
        if "minimal" in views:
            pred_min = generate(tok, model, views["minimal"]["context"])
            hits["mes"] += int(exact(pred_min, views["minimal"]["answer"]))
        pred_cf = generate(tok, model, views["cf"]["context"])
        ok_cf = (
            exact(pred_cf, views["cf"]["answer"]) and pred_cf.strip() != full["answer"]
        )
        hits["cfr"] += int(ok_cf)
        by_type[qtype]["cfr"] += int(ok_cf)
        if "distractor_only" in views:
            pred_d = generate(tok, model, views["distractor_only"]["context"])
            hits["refuse"] += int("unanswerable" in pred_d.strip().lower())
        pred_cb = generate(tok, model, full["question"])
        hits["closed"] += int(exact(pred_cb, full["answer"]))
    denom = max(n, 1)
    return {
        "n": n,
        "Acc_full": hits["acc_full"] / denom,
        "MES": hits["mes"] / denom,
        "CFR": hits["cfr"] / denom,
        "distractor_refuse": hits["refuse"] / denom,
        "closed_book_acc": hits["closed"] / denom,
        "by_type": {k: dict(v) for k, v in by_type.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "p0")
    ap.add_argument("--model-dir", type=str, default=None)
    ap.add_argument("--max-examples", type=int, default=24)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    report = {"engine": engine_oracle_metrics(args.data / "eval.jsonl")}
    if args.model_dir:
        report["model"] = eval_model(
            args.data / "eval.jsonl", args.model_dir, args.max_examples
        )
    out = args.out or (args.data / "eval_causal.json")
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
