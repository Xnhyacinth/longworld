#!/usr/bin/env python3
"""Meta-feature predictability: can answer classes be read off the ledger?

The shortcut battery's second instrument: the answer's verdict class must not
be predictable from ledger metadata (family, depth, token target band, length
band, seed band) — if a feature reliably predicts the answer, a model can
shortcut content reading entirely.

Two instruments (P72 G72-5):

* legacy buckets: per-feature class-balance and the majority-share a naive
  bucket-majority classifier would reach (the original report, kept as-is).

* classifier: a one-hot logistic regression over the five metadata features,
  trained by full-batch gradient descent in numpy (no sklearn — the venv has
  none). Reported as macro-averaged accuracy vs the class prior, per
  answer-verb family. join_unanswerable is constant-by-design (UNKNOWN for
  every row): it is EXCLUDED from the leak claim and reported separately,
  because any classifier trivially fits a constant and that fit is a
  property of the task design, not a metadata leak.

Usage:
  python scripts/measure_meta_feature_predictability.py BANK [--epochs N] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONSTANT_BY_DESIGN = ("join_unanswerable",)
FEATURES = ("family", "depth", "token_target_band", "length_band", "seed_band")


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


def row_features(row: dict) -> dict[str, str]:
    """The five metadata features of one bank row.

    Bank rows carry the raw ledger fields (target.token_target,
    length_records, world_seed); a row may instead carry the banded feature
    values directly (the CV helper's unit-test fixtures), which keeps this
    the one place the banding lives.
    """
    if "token_target_band" in row:
        return {
            name: str(row[name] if name in row else row.get(name)) for name in FEATURES
        }
    return {
        "family": row["family"],
        "depth": str(row["depth"]),
        "token_target_band": (
            "8k"
            if row["target"]["token_target"] <= 8192
            else "32k"
            if row["target"]["token_target"] <= 32768
            else "128k"
        ),
        "length_band": (
            "L<=300"
            if row["length_records"] <= 300
            else "L<=1000"
            if row["length_records"] <= 1000
            else "L>1000"
        ),
        "seed_band": str(row["world_seed"] // 1000),
    }


# --------------------------------------------------------------------------
# Tiny one-hot logistic regression (numpy, full-batch GD, no sklearn)
# --------------------------------------------------------------------------


def fit_logistic_regression(
    x: np.ndarray,
    y: np.ndarray,
    n_classes: int,
    epochs: int = 300,
    lr: float = 0.5,
    l2: float = 0.01,
) -> np.ndarray:
    """Softmax weights (D+1, C) by full-batch gradient descent, L2-regularized.

    x is (n, D) one-hot (no bias column); y is (n,) integer class labels.
    Deterministic: zero init, no sampling. The L2 term (bias excluded) keeps
    the probe honest on noisy metadata: without it the model memorizes fold
    idiosyncrasies of one-hot columns and reports memorization as leakage.
    """
    n, d = x.shape
    w = np.zeros((d + 1, n_classes))
    bias = np.ones((n, 1))
    design = np.hstack([x, bias])
    penalty = np.ones((d + 1, n_classes))
    penalty[d, :] = 0.0  # the bias column is never regularized
    for _ in range(epochs):
        scores = design @ w  # (n, C)
        scores -= scores.max(axis=1, keepdims=True)
        exp = np.exp(scores)
        probs = exp / exp.sum(axis=1, keepdims=True)
        probs[np.arange(n), y] -= 1.0
        gradient = design.T @ probs / n + l2 * penalty * w
        w -= lr * gradient
    return w


def predict(x: np.ndarray, w: np.ndarray) -> np.ndarray:
    n = x.shape[0]
    design = np.hstack([x, np.ones((n, 1))])
    scores = design @ w
    return scores.argmax(axis=1)


def macro_accuracy(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int) -> float:
    """Unweighted mean of per-class recalls (the majority prior flatters itself)."""
    recalls = []
    for cls in range(n_classes):
        mask = y_true == cls
        if mask.any():
            recalls.append(float((y_pred[mask] == cls).mean()))
    return float(np.mean(recalls)) if recalls else float("nan")


def cross_validated_macro_accuracy(
    rows: list[dict], epochs: int, folds: int = 5, seed: int = 20260919
) -> dict | None:
    """5-fold CV macro accuracy and prior, over one-hot metadata features.

    The folds are shuffled deterministically and the feature vocabulary is
    built from the full row set BEFORE splitting (the vocabulary is part of
    the measurement contract, not a leaked quantity: all values are drawn
    from the manifest's declared grid).
    """
    if len(rows) < folds * 4:
        return None
    features = [row_features(row) for row in rows]
    vocab: dict[str, dict[str, int]] = {
        name: {
            value: idx for idx, value in enumerate(sorted({f[name] for f in features}))
        }
        for name in FEATURES
    }
    # One column per (feature, value), offsets in FEATURES order.
    offsets: dict[str, int] = {}
    cursor = 0
    for name in FEATURES:
        offsets[name] = cursor
        cursor += len(vocab[name])
    x = np.zeros((len(rows), cursor), dtype=np.float64)
    for i, feat in enumerate(features):
        for name in FEATURES:
            x[i, offsets[name] + vocab[name][feat[name]]] = 1.0
    classes = sorted({row["answer"] for row in rows})
    class_index = {name: idx for idx, name in enumerate(classes)}
    y = np.array([class_index[row["answer"]] for row in rows], dtype=np.int64)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(rows))
    fold_predictions = np.zeros(len(rows), dtype=np.int64)
    for fold in range(folds):
        test = order[fold::folds]
        # train must be the order values NOT in test. Masking positionally
        # (train_mask[test] = False) keys on positions, not values, and
        # silently leaks test rows into train whenever the permutation is
        # not the identity: every fold then reports memorization as signal.
        train = order[~np.isin(order, test)]
        w = fit_logistic_regression(x[train], y[train], len(classes), epochs)
        fold_predictions[test] = predict(x[test], w)
    # The class prior as a predictor: predict the global majority class.
    majority = int(np.bincount(y).argmax())
    prior_pred = np.full(len(rows), majority, dtype=np.int64)
    return {
        "rows": len(rows),
        "classes": classes,
        "class_counts": {
            name: int(count) for name, count in zip(classes, np.bincount(y))
        },
        "macro_accuracy": round(macro_accuracy(y, fold_predictions, len(classes)), 4),
        "macro_prior": round(macro_accuracy(y, prior_pred, len(classes)), 4),
        "accuracy": round(float((fold_predictions == y).mean()), 4),
        "prior_accuracy": round(float((prior_pred == y).mean()), 4),
        "leak_above_prior": None,  # filled by the caller with the subtraction
    }


def measure(bank: Path, epochs: int = 300) -> dict:
    rows = []
    for split in ("train", "eval"):
        path = bank / f"{split}.jsonl"
        if path.exists():
            for line in path.open():
                rows.append(json.loads(line))
    report: dict = {"bank": str(bank), "rows": len(rows)}
    getters = {
        "family": lambda r: r["family"],
        "depth": lambda r: str(r["depth"]),
        "token_target_band": lambda r: row_features(r)["token_target_band"],
        "length_band": lambda r: row_features(r)["length_band"],
        "seed_band": lambda r: str(r["world_seed"] // 1000),
    }
    for name, get in getters.items():
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

    # ---- classifier (P72 G72-5): one-hot LR, macro accuracy vs prior ----
    verb_families = (
        "group_compare",
        "rule_holdout",
    )  # join_unanswerable is constant-by-design, reported separately
    classifier_report: dict[str, dict] = {}
    for fam in verb_families:
        fam_rows = [
            {"answer": answer_class(row), "row": row}
            for row in rows
            if row["family"] == fam and answer_class(row) is not None
        ]
        if not fam_rows:
            continue
        fam_rows = [entry for entry in fam_rows if entry["answer"]]
        result = cross_validated_macro_accuracy(
            [entry["row"] | {"answer": entry["answer"]} for entry in fam_rows],
            epochs,
        )
        if result is None:
            classifier_report[fam] = {"rows": len(fam_rows), "skipped": "too few rows"}
            continue
        result["leak_above_prior"] = (
            round(result["macro_accuracy"] - result["macro_prior"], 4)
            if result["macro_accuracy"] is not None
            and result["macro_prior"] is not None
            else None
        )
        classifier_report[fam] = result
    report["logistic_classifier"] = {
        "model": "one-hot softmax regression, full-batch GD (numpy), 5-fold CV",
        "features_one_hot": list(FEATURES),
        "epochs": epochs,
        "excluded_constant_by_design": list(CONSTANT_BY_DESIGN),
        "per_family": classifier_report,
        "reading": (
            "leak_above_prior > 0 means the metadata features carry the answer "
            "class beyond the class prior — a content-free channel. Macro "
            "accuracy is unweighted per-class recall so an imbalanced prior "
            "cannot hide a minority-class leak."
        ),
    }
    # The constant family, reported separately and OUT of the leak claim.
    constant = {}
    for fam in CONSTANT_BY_DESIGN:
        counter = Counter(answer_class(row) for row in rows if row["family"] == fam)
        if counter:
            constant[fam] = {
                "rows": sum(counter.values()),
                "counts": dict(counter),
                "constant_by_design": True,
                "note": (
                    "every row answers UNKNOWN by construction; any classifier "
                    "fits it perfectly and that is a property of the family, "
                    "not metadata leakage"
                ),
            }
    report["constant_by_design_families"] = constant
    report["reading"] = (
        "leak_above_baseline > 0 means ledger metadata alone beats the global "
        "class prior — the answer is partially readable without the content"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = measure(args.bank, args.epochs)
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
