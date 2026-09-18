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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", nargs="+", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--steps", type=int, default=680)
    parser.add_argument("--gbs", type=int, default=16)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
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

    report = {
        "rows": rows,
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

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for k, v in report.items():
            print(f"  {k:38s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
