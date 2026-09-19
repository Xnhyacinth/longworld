#!/usr/bin/env python3
"""Master table for the aligned eval wave (2026-09-17).

Merges the 0912 baseline runs (acc_ckpt680 / acc_base_ckpt680 /
longtrace_base_ckpt680, verified complete and same-protocol) with the 0917
P64 runs into one comparison table:

  MRCR-2 / MRCR-4   sequence_matcher_mean of mrcr_2needle / mrcr_4needle
  GW parents/BFS P  precision of graphwalks_parents / graphwalks_bfs
  IFEval            prompt_level_strict_acc,none
  GPQA              exact_match,flexible-extract (cot_zeroshot, no-think greedy)
  MMLU-Pro          mean over 14 subjects weighted by n-samples

Everything is read from the launcher-produced summaries; nothing is
recomputed from raw samples. Missing runs leave a dash.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MRCR_BASELINE = Path("/volume/pt-dev/qjiu/longworld/data/hf/LongWorld-Training-State/evaluations/runs/mrcr_graphwalks_20260912")
MRCR_BASEMODEL = Path("/volume/pt-dev/qjiu/longworld/data/hf/LongWorld-Training-State/evaluations/runs/mrcr_graphwalks_20260901")
DOWNSTREAM_BASELINE = Path("/volume/pt-dev/qjiu/longworld/data/hf/LongWorld-Training-State/evaluations/runs/downstream_same_protocol_20260912")
MRCR_CURRENT = Path("/volume/pt-dev/qjiu/longworld-worlds/data/evals/mrcr_graphwalks_20260917")
DOWNSTREAM_CURRENT = Path("/volume/pt-dev/qjiu/longworld-worlds/data/evals/downstream_same_protocol_20260917")

MODELS = [
    "b0_qwen35_4b_base",
    "acc_base_ckpt680",
    "longtrace_base_ckpt680",
    "p64_base_ckpt680",
    "acc_ckpt680",
]
MODEL_SOURCE = {
    "b0_qwen35_4b_base": "0917 (MRCR 0901)",
    "acc_ckpt680": "0912",
    "acc_base_ckpt680": "0912",
    "longtrace_base_ckpt680": "0912",
    "p64_base_ckpt680": "0917",
}


def mrcr_metrics(root: Path) -> dict[str, dict]:
    """model -> {shard_task: metric fields} from per-shard summary.json."""
    out: dict[str, dict] = {}
    for path in sorted(root.glob("*/*/summary.json")):
        data = json.loads(path.read_text())
        out.setdefault(path.parts[-3], {})[data["task"]] = data
    return out


def downstream_metrics(root: Path) -> dict[str, dict]:
    """model -> {task: metric dict} from lm-eval results_*.json."""
    out: dict[str, dict] = {}
    for path in sorted(root.glob("*/*/results/*/results_*.json")):
        data = json.loads(path.read_text())
        model = path.parts[-5]
        tasks = out.setdefault(model, {})
        for task, metrics in data.get("results", {}).items():
            merged = dict(tasks.get(task, {}))
            for k, v in metrics.items():
                if isinstance(v, (int, float)):
                    merged[k] = v
            # n-samples for MMLU-Pro weighting
            ns = data.get("n-samples", {}).get(task, {})
            if ns:
                merged["_n"] = ns.get("effective", ns.get("original"))
            tasks[task] = merged
    return out


def f(v, nd=4):
    return "—" if v is None else f"{v:.{nd}f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mrcr-baseline", type=Path, default=MRCR_BASELINE)
    ap.add_argument("--mrcr-basemodel", type=Path, default=MRCR_BASEMODEL)
    ap.add_argument("--downstream-baseline", type=Path, default=DOWNSTREAM_BASELINE)
    ap.add_argument("--mrcr-current", type=Path, default=MRCR_CURRENT)
    ap.add_argument("--downstream-current", type=Path, default=DOWNSTREAM_CURRENT)
    ap.add_argument("--out", type=Path, default=Path("/volume/pt-dev/qjiu/longworld-worlds/data/evals/aligned_eval_master_table.md"))
    args = ap.parse_args()

    mrcr = {}
    down = {}
    # Merge order: 0901 (oldest, contributes ONLY the untrained-Base rows,
    # which no other wave has) -> 0912 baselines -> 0917 current. setdefault at
    # shard level, so a 0917 re-run of b0_qwen35_4b_base (same-machine anchor)
    # supersedes the 0901 numbers, and 0912 never gets overwritten by 0901.
    for root in (args.mrcr_basemodel, args.mrcr_baseline, args.mrcr_current):
        if not root.exists():
            continue
        is_basemodel_wave = root == args.mrcr_basemodel
        for model, shards in mrcr_metrics(root).items():
            if is_basemodel_wave and model != "b0_qwen35_4b_base":
                continue
            for shard, data in shards.items():
                mrcr.setdefault(model, {}).setdefault(shard, data)
    for tag, root in (("0912", args.downstream_baseline), ("0917", args.downstream_current)):
        if root.exists():
            for model, tasks in downstream_metrics(root).items():
                down.setdefault(model, {}).update(tasks)

    rows = []
    for model in MODELS:
        m = mrcr.get(model, {})
        d = down.get(model, {})
        m2 = m.get("mrcr_2needle", {}).get("sequence_matcher_mean")
        m4 = m.get("mrcr_4needle", {}).get("sequence_matcher_mean")
        gp = m.get("graphwalks_parents", {}).get("precision")
        gb = m.get("graphwalks_bfs", {}).get("precision")
        ifeval = d.get("ifeval", {}).get("prompt_level_strict_acc,none")
        gpqa = d.get("gpqa_diamond_cot_zeroshot", {}).get("exact_match,flexible-extract")
        # MMLU-Pro: mean over subjects weighted by effective n
        num = den = 0.0
        for task, td in d.items():
            if task.startswith("mmlu_pro_") and "exact_match,custom-extract" in td:
                n = td.get("_n") or 1
                num += td["exact_match,custom-extract"] * n
                den += n
        mmlu = num / den if den else None
        rows.append({
            "model": model, "source": MODEL_SOURCE.get(model, "?"),
            "mrcr_2": m2, "mrcr_4": m4,
            "mrcr_overall": (m2 + m4) / 2 if m2 is not None and m4 is not None else None,
            "gw_parents_p": gp, "gw_bfs_p": gb,
            "ifeval_strict": ifeval, "gpqa_flex": gpqa, "mmlu_pro_w": mmlu,
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Aligned eval master table (2026-09-17 wave)",
        "",
        "Protocol: greedy T=0, enable_thinking=false, seed 42, apply_chat_template, no --limit;",
        "vLLM 0.18.0 OpenAI server (131072 ctx MRCR/GW, 16384 downstream); lm-eval upstream v0.4.12.",
        "Baselines from the 2026-09-12 runs (verified same-protocol; not re-run). P64 from the 2026-09-17 runs.",
        "",
        "| model | run | MRCR-2 | MRCR-4 | MRCR avg | GW parents P | GW BFS P | IFEval strict | GPQA flex | MMLU-Pro w |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['source']} | {f(r['mrcr_2'])} | {f(r['mrcr_4'])} | {f(r['mrcr_overall'])} | "
            f"{f(r['gw_parents_p'])} | {f(r['gw_bfs_p'])} | {f(r['ifeval_strict'])} | {f(r['gpqa_flex'])} | {f(r['mmlu_pro_w'])} |"
        )
    lines += [
        "",
        "Notes: MRCR = difflib.SequenceMatcher after hash prefix; GW P = precision (ACC Table 2 headline);",
        "IFEval = prompt_level_strict_acc (cap 1280); GPQA = flexible-extract of cot_zeroshot no-think;",
        "MMLU-Pro = exact_match custom-extract, weight_by_size over 14 subjects.",
    ]
    args.out.write_text("\n".join(lines) + "\n")
    json_out = args.out.with_suffix(".json")
    json_out.write_text(json.dumps({"rows": rows}, indent=2) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {args.out} and {json_out}")


if __name__ == "__main__":
    main()
