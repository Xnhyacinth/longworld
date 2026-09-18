#!/usr/bin/env python3
"""Collapse-predictor metrics for a LongWorld SFT corpus (P67 design §4, E2).

Computes, from the training JSONL alone — before any training run:
  - rows, distinct answer shapes (quoted strings and numbers masked),
    shape uniqueness, top-3 shape coverage
  - distinct instruction ("Task:" / first user line) count
  - distinct source documents (group_id, if an index is supplied)
  - supervised-token and context-token counts -> supervised fraction
  - per-document exposure implied by a given step budget
  - answer-scaffolding concentration: positions >=90% identical within a
    task's answers (the fraction of the gold an LM can emit for free)

Usage:
  python scripts/measure_sft_collapse_predictors.py \
    --train finance_train.jsonl codeforge_train.jsonl \
    [--index sample_index.jsonl] [--steps 680] [--gbs 16]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mask_shape(text: str) -> str:
    text = re.sub(r'"[^"]*"', '"S"', text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "N", text)
    return re.sub(r"\s+", " ", text).strip()


def instruction_line(user_text: str) -> str:
    m = re.search(r"^Task: .*$", user_text, re.MULTILINE)
    if m:
        return m.group(0)
    return user_text.strip().splitlines()[-1] if user_text.strip() else ""


def scaffolding_fraction(answers: list[str]) -> tuple[float, int]:
    """Share of answer positions that are >=90% identical across answers."""
    if len(answers) < 3:
        return 0.0, 0
    by_pos: dict[int, Counter[str]] = defaultdict(Counter)
    max_len = max(len(a) for a in answers)
    for a in answers:
        for i, ch in enumerate(a):
            by_pos[i][ch] += 1
    n = len(answers)
    fixed = sum(1 for i in range(max_len) if by_pos[i].most_common(1)[0][1] / n >= 0.9)
    return fixed / max_len if max_len else 0.0, fixed


def entropy_bits(counter: Counter) -> float:
    n = sum(counter.values())
    return -sum((c / n) * math.log2(c / n) for c in counter.values() if c)


def conditional_entropy_bits(by_x: dict[str, Counter]) -> float:
    n = sum(sum(c.values()) for c in by_x.values())
    total = 0.0
    for c in by_x.values():
        nx = sum(c.values())
        for v in c.values():
            total -= (v / n) * math.log2(v / nx)
    return total


def template_collapse_profile(
    shapes: list[str], instructions: list[str]
) -> dict:
    """RAGEN-2-style decomposition (arXiv 2604.06268): how much of the answer-
    shape entropy is explained by the instruction template alone?

    Template collapse = high I(shape;instruction) combined with a low number
    of distinct shapes per instruction. Measured on P64 finance it is 69%
    with 17 instructions serving 76 rows each; on ACC it is 80% but 6,396
    instructions serving 1.7 rows each -- the same share with a thousandfold
    different instruction count. Both numbers are needed to tell them apart.
    """
    h_shape = entropy_bits(Counter(shapes))
    by_instr: dict[str, Counter] = defaultdict(Counter)
    for i, s in zip(instructions, shapes):
        by_instr[i][s] += 1
    h_shape_given_instr = conditional_entropy_bits(by_instr)
    mutual = max(0.0, h_shape - h_shape_given_instr)
    import statistics

    rows_per_instr = [sum(c.values()) for c in by_instr.values()]
    shapes_per_row = [len(c) / sum(c.values()) for c in by_instr.values()]
    top_instr = Counter(instructions).most_common(1)[0]
    return {
        "shape_entropy_bits": round(h_shape, 2),
        "instruction_explained_fraction": round(mutual / h_shape, 3) if h_shape else 0.0,
        "distinct_instructions": len(by_instr),
        "mean_rows_per_instruction": round(statistics.mean(rows_per_instr), 1)
        if rows_per_instr
        else 0.0,
        "mean_shapes_per_row_within_instruction": round(
            statistics.mean(shapes_per_row), 3
        )
        if shapes_per_row
        else 0.0,
        "top_instruction_share": round(top_instr[1] / len(instructions), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", nargs="+", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--steps", type=int, default=680)
    parser.add_argument("--gbs", type=int, default=16)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="apply collapse gates and exit non-zero on violation",
    )
    args = parser.parse_args()

    shapes: Counter[str] = Counter()
    instructions: Counter[str] = Counter()
    ans_chars: list[int] = []
    sup_tokens = 0
    rows = 0
    answers_by_task: dict[str, list[str]] = defaultdict(list)
    tokens_by_row: list[int] = []

    for path in args.train:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                msgs = d["messages"]
                assistant = next(m for m in msgs if m["role"] == "assistant")["content"]
                user = next(m for m in msgs if m["role"] == "user")["content"]
                shapes[mask_shape(assistant)] += 1
                instructions[instruction_line(user)] += 1
                ans_chars.append(len(assistant))
                sup_tokens += max(1, round(len(assistant) / 3.8))
                rows += 1
                tokens_by_row.append(sum(len(m["content"]) for m in msgs) // 4)
                task = instruction_line(user)[:40]
                answers_by_task[task].append(assistant)

    group_ids: set[str] = set()
    ctx_tokens_index = 0
    if args.index and args.index.exists():
        for line in args.index.open():
            d = json.loads(line)
            if d.get("split") == "train":
                group_ids.add(str(d.get("group_id")))
                ctx_tokens_index += int(d.get("full_message_tokens", 0))
    ctx_tokens = ctx_tokens_index or sum(tokens_by_row)

    top3 = sum(c for _, c in shapes.most_common(3))
    samples_seen = args.steps * args.gbs
    epochs = samples_seen / rows if rows else 0.0
    docs = len(group_ids) if group_ids else None
    exposure = (epochs * rows / docs) if docs else None
    sup_frac = sup_tokens / ctx_tokens if ctx_tokens else 0.0
    ans_chars.sort()
    scaffolds = {
        task: scaffolding_fraction(answers)
        for task, answers in answers_by_task.items()
        if len(answers) >= 10
    }
    worst_scaffold = max(scaffolds.items(), key=lambda kv: kv[1][0]) if scaffolds else None

    instr_list: list[str] = []
    shape_list: list[str] = []
    for path in args.train:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                msgs = d["messages"]
                assistant = next(m for m in msgs if m["role"] == "assistant")["content"]
                user = next(m for m in msgs if m["role"] == "user")["content"]
                shape_list.append(mask_shape(assistant))
                instr_list.append(instruction_line(user))

    report = {
        "rows": rows,
        "template_collapse": template_collapse_profile(shape_list, instr_list),
        "distinct_answer_shapes": len(shapes),
        "shape_uniqueness": round(len(shapes) / rows, 4) if rows else 0.0,
        "top3_shape_coverage": round(top3 / rows, 4) if rows else 0.0,
        "distinct_instructions": len(instructions),
        "answer_chars_p50": ans_chars[len(ans_chars) // 2] if ans_chars else 0,
        "context_tokens": ctx_tokens,
        "supervised_tokens_est": sup_tokens,
        "supervised_fraction": round(sup_frac, 6),
        "steps_x_gbs_samples": samples_seen,
        "effective_epochs_at_budget": round(epochs, 2),
        "distinct_source_documents": docs,
        "per_document_exposure_at_budget": round(exposure, 1) if exposure else None,
        "worst_scaffold_task": {
            "task": worst_scaffold[0],
            "fixed_position_fraction": round(worst_scaffold[1][0], 3),
        }
        if worst_scaffold
        else None,
    }

    # Collapse gates. Thresholds derive from the three measured runs, not
    # from a published number (none exists -- see the P68 design doc):
    # ACC 1.1 shape-exposure / 94% shape uniqueness / 3.2 rows-per-document
    # survived; LongTrace 5.6 / 70% / -- survived with partial damage;
    # P64 68.4 / 8.1% / 89 rows-per-doc collapsed. Floors sit a factor of
    # ~2 above the observed success and ~2-5x below the observed failure.
    violations: list[str] = []
    shape_uniqueness = report["shape_uniqueness"]
    if shape_uniqueness < 0.10:
        violations.append(
            f"shape_uniqueness {shape_uniqueness} < 0.10 floor "
            "(P64 measured 0.081; ACC 0.940)"
        )
    if report.get("per_document_exposure_at_budget") and report[
        "per_document_exposure_at_budget"
    ] > 50:
        violations.append(
            f"per-document exposure {report['per_document_exposure_at_budget']} > 50 "
            "(ACC measured 3.2 rows/doc; P64 measured 89)"
        )
    top_instr_share = report.get("template_collapse", {}).get(
        "top_instruction_share"
    )
    if top_instr_share and top_instr_share > 0.10:
        violations.append(
            f"top_instruction_share {top_instr_share} > 0.10 "
            "(P64 finance: 17 instructions, one serves 25% of rows)"
        )
    # Scaffold concentration is REPORT-ONLY: ACC's worst task measures 0.704
    # and P64's 0.837 -- a real but thin margin, not a gate. Use it as a
    # signal to inspect a family, not to block a corpus.
    shape_exposure = (
        report["effective_epochs_at_budget"] * rows / len(shapes)
        if shapes
        else 0.0
    )
    if shape_exposure > 10:
        violations.append(
            f"shape-exposure {shape_exposure:.1f} > 10 "
            "(ACC 1.1 / LongTrace 5.6 / P64 68.4; failure boundary ~19.6)"
        )
    report["gate_violations"] = violations
    report["gate_passed"] = not violations

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for k, v in report.items():
            if k == "gate_violations" and v:
                print("  GATE VIOLATIONS:")
                for item in v:
                    print(f"    - {item}")
            elif k == "gate_violations":
                print("  gate_violations                   []")
            else:
                print(f"  {k:38s} {v}")
    if args.gate and violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
