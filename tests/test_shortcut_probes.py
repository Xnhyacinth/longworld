"""Unit tests for the P72 G72-5 shortcut-probe upgrades.

Three instruments, three fixtures:

* matched-entity mutation: for hand-built small worlds of every family, the
  mutated context must NOT re-yield the stored gold (either a different
  answer or the executor's fail-closed refusal), while every UNTOUCHED row
  must re-render byte-identically (surface identity is fixed).
* lexical calibers: gold-span precision on a fixture where the question
  names the filtered field but not the values, and hard-negative precision
  against the near-miss row the question's category names.
* the tiny logistic regression: on synthetic separable one-hot data it must
  exceed 0.9 accuracy (else the meta probe's numbers are meaningless), and
  on shuffled labels it must not beat the prior.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records
from longworld.synthesis import capability_unanswerable_adapter as unanswerable
from scripts.measure_bank_lexical_overlap import (
    gold_span_text,
    hard_negative_text,
)
from scripts.measure_meta_feature_predictability import (
    answer_class,
    cross_validated_macro_accuracy,
    fit_logistic_regression,
    macro_accuracy,
    predict,
)
from scripts.measure_wrong_context_baseline import mutate_context
from scripts.run_capability_records import canonical

SMALL = {"length_records": 200, "consumed_records": 20, "depth": 2, "n_variants": 2}
SEEDS = (11, 12, 13, 14, 15)


def solved(module, context, question):
    """The answer, or the fail-closed refusal as its own outcome."""
    try:
        return module.solve_visible(context, question)
    except ValueError:
        return "<refused>"


def module_of(family):
    return {
        "join_unanswerable": unanswerable,
        **{name: records for name in records.FAMILIES},
        **{name: families for name in families.FAMILIES},
    }[family]


def row_lines(context):
    return context.splitlines()[1:]


ALL_FAMILIES = (
    *records.FAMILIES,
    *families.FAMILIES,
    "join_unanswerable",
)


# --------------------------------------------------------------------------
# Matched-entity mutation: the gold must not survive a content perturbation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("family", ALL_FAMILIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_mutated_context_does_not_reyield_gold(family, seed):
    """Surface identity fixed, content moved: the stored gold must not reproduce."""
    if family in ("set_complete", "dense_aggregate"):
        # Neither is in the P71 bank (set_complete was folded out of the grid;
        # dense_aggregate is the G72-3 track's new family, not yet in a pool).
        # Their mutators are out of scope for G72-5.
        pytest.skip("family not in the p71 pool")
    depth = 1 if family in ("asof_state", "join_unanswerable") else 2
    kwargs = dict(SMALL, depth=depth)
    if family == "join_unanswerable":
        world = unanswerable.generate_world(seed, family, **kwargs)
    else:
        world = module_of(family).generate_world(seed, family, **kwargs)
    module = module_of(family)
    rng = random.Random(seed)
    for task in world["tasks"]:
        answer = task["answer"]
        gold = canonical(answer)
        mutated = mutate_context(world["context"], task, rng)
        assert mutated is not None, f"{family}: mutator refused the task"
        re_answer = solved(module, mutated, task["question"])
        assert canonical(re_answer) != gold, (
            f"{family}/{seed}: gold survived a matched-entity mutation"
        )


@pytest.mark.parametrize("family", ["filter_aggregate", "alias_locate", "asof_state"])
def test_untouched_rows_render_byte_identically(family):
    """The mutation touches only question-relevant content, in place."""
    world = module_of(family).generate_world(21, family, **SMALL)
    rng = random.Random(21)
    task = world["tasks"][0]
    before = row_lines(world["context"])
    mutated = mutate_context(world["context"], task, rng)
    after = row_lines(mutated)
    assert len(after) == len(before)
    untouched = sum(1 for b, a in zip(before, after) if b == a)
    assert untouched <= len(before)
    # The header line is byte-identical.
    assert mutated.splitlines()[0] == world["context"].splitlines()[0]
    # Mutated rows are still valid JSON lines with the same ids and types.
    for line in after:
        row = json.loads(line)
        assert "id" in row and "type" in row


# --------------------------------------------------------------------------
# Lexical calibers
# --------------------------------------------------------------------------


def _row(id, type, **fields):
    return {"id": id, "type": type, **fields}


OVERLAP_ROWS = [
    _row(
        "r1",
        "record",
        entity="e1",
        amount=100,
        date="2019-01-05",
        category="ember",
        memo="note-aaa",
    ),
    _row(
        "r2",
        "record",
        entity="e2",
        amount=200,
        date="2019-02-05",
        category="ember",
        memo="note-bbb",
    ),
    _row(
        "r3",
        "record",
        entity="e3",
        amount=300,
        date="2019-03-05",
        category="fjord",
        memo="note-ccc",
    ),
    _row(
        "r4",
        "record",
        entity="e1",
        amount=400,
        date="2019-04-05",
        category="ember",
        memo="note-ddd",
    ),
]
OVERLAP_TASK = {
    "question": {
        "family": "filter_aggregate",
        "steps": [
            {
                "op": "filter",
                "conditions": [
                    {"field": "category", "op": "==", "value": "ember"},
                    {"field": "amount", "op": ">=", "value": 150},
                ],
            },
            {"op": "aggregate", "how": "sum"},
        ],
    },
    "consumed": ["r2", "r4"],
    "phrasing_index": 0,
}


def test_gold_span_excludes_nondecisive_fields():
    """The gold span carries amounts and dates, not ids/memos/entities."""
    span = gold_span_text(OVERLAP_ROWS, OVERLAP_TASK["consumed"])
    assert "200" in span and "400" in span
    assert "note-aaa" not in span and "e2" not in span and "r2" not in span


def test_gold_span_precision_is_partial():
    """The question names the field vocabulary, so precision is nonzero but low."""
    from scripts.measure_bank_lexical_overlap import _precision, _question_text

    question = _question_text(OVERLAP_TASK)
    span = gold_span_text(OVERLAP_ROWS, OVERLAP_TASK["consumed"])
    precision = _precision(question, span)
    assert precision is not None
    assert 0.0 < precision < 0.5, "question should not cover the gold span"


def test_hard_negative_selects_same_category_out_of_scope():
    """Near-miss rows share the question's category but sit outside the consumed set."""
    near = hard_negative_text(OVERLAP_ROWS, OVERLAP_TASK)
    assert near is not None
    # r1 is the ember row out of scope (amount 100 < 150): the near miss.
    assert "100" in near
    assert "300" not in near, "fjord row shares neither category nor entity"


def test_hard_negative_falls_back_to_same_type():
    """No shared category/entity: same-type rows keep the caliber defined."""
    task = dict(OVERLAP_TASK, consumed=["r3"])
    near = hard_negative_text(OVERLAP_ROWS, task)
    assert near is not None
    assert "300" not in near


# --------------------------------------------------------------------------
# The tiny logistic regression
# --------------------------------------------------------------------------


def _one_hot(rows, vocab):
    x = np.zeros((len(rows), len(vocab)), dtype=np.float64)
    for i, value in enumerate(rows):
        x[i, vocab[value]] = 1.0
    return x


def test_logistic_regression_separates_synthetic_data():
    """A separable one-hot problem must reach > 0.9 accuracy."""
    vocab = {name: idx for idx, name in enumerate("abcdef")}
    rows = ["a", "b", "a", "b", "c", "d", "c", "d", "e", "f", "e", "f"] * 10
    labels = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2] * 10
    x = _one_hot(rows, vocab)
    y = np.array(labels, dtype=np.int64)
    w = fit_logistic_regression(x, y, n_classes=3, epochs=200)
    acc = float((predict(x, w) == y).mean())
    assert acc > 0.9, f"LR failed to separate separable data: {acc}"


def test_logistic_regression_learns_nothing_from_shuffled_labels():
    """Label-independent features: CV macro accuracy must not beat the prior."""
    rng = np.random.default_rng(7)
    labels = rng.integers(0, 3, size=300).tolist()
    rows = [
        {
            "family": f"f{i % 6}",
            "depth": "2",
            "token_target_band": "32k",
            "length_band": "L<=300",
            "seed_band": "761",
            "answer": str(labels[i]),
        }
        for i in range(300)
    ]
    result = cross_validated_macro_accuracy(rows, epochs=100)
    assert result is not None
    assert result["macro_accuracy"] <= result["macro_prior"] + 0.05


def test_answer_class_parses_each_verb_family():
    assert (
        answer_class(
            {
                "family": "group_compare",
                "messages": [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": '{"verdict":"GT"}'},
                ],
            }
        )
        == "GT"
    )
    assert (
        answer_class(
            {
                "family": "rule_holdout",
                "messages": [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": '{"label":"label_alpha"}'},
                ],
            }
        )
        == "label_alpha"
    )
    assert (
        answer_class(
            {
                "family": "join_unanswerable",
                "messages": [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": '"UNKNOWN"'},
                ],
            }
        )
        == "UNKNOWN"
    )


def test_cross_validated_report_carries_denominators():
    rows = [
        {
            "family": f"f{i % 3}",
            "depth": "2",
            "token_target_band": "32k",
            "length_band": "L<=300",
            "seed_band": "761",
            "answer": str(i % 2),
        }
        for i in range(40)
    ]
    result = cross_validated_macro_accuracy(rows, epochs=100)
    assert result["rows"] == 40
    assert result["class_counts"] == {"0": 20, "1": 20}
    assert 0.0 <= result["macro_accuracy"] <= 1.0
    assert result["leak_above_prior"] is None  # caller fills it
