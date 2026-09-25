"""Text-level rule/state interventions for the RFC hybrid pilot."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.run_p87_hybrid_rfc_pilot import (
    OPERATIONS,
    _alter_rule,
    _filler,
    _id_only_projection,
    _pin,
    _rules,
    _solve_visible,
    _world,
)


def test_real_rule_and_simulated_record_are_both_visible_and_necessary() -> None:
    config = json.loads(Path("configs/p87_hybrid_rfc9114_pilot_v6.json").read_text())
    rules = _rules(_pin(config["rules_source"], config["rules_sha256"]))
    filler = _filler(_pin(config["filler_source"], config["filler_sha256"]), 60000)
    context, specs = _world(config, rules, filler, 11)
    assert "SIMULATED connection inspection records" in context
    for spec in specs:
        operation = spec["operation"]
        answer = _solve_visible(operation, context)
        assert answer
        rule_removed = context.replace(rules[operation], "", 1)
        assert _solve_visible(operation, rule_removed) is None
        rule_altered = context.replace(
            rules[operation], _alter_rule(operation, rules[operation]), 1
        )
        assert _solve_visible(operation, rule_altered) != answer
        positive = answer[0]
        start, end = spec["record_spans"][positive]
        record_removed = context.replace(context[start:end], "", 1)
        assert _solve_visible(operation, record_removed) != answer
        assert context.count(rules[operation]) == 1
    assert {spec["operation"] for spec in specs} == set(OPERATIONS)


def test_same_opaque_ids_require_different_answers_in_paired_worlds() -> None:
    config = json.loads(Path("configs/p87_hybrid_rfc9114_pilot_v6.json").read_text())
    rules = _rules(_pin(config["rules_source"], config["rules_sha256"]))
    filler = _filler(_pin(config["filler_source"], config["filler_sha256"]), 60000)
    first, first_specs = _world(config, rules, filler, 11)
    second, second_specs = _world(config, rules, filler, 29)
    first_ids = [row["id"] for row in first_specs[0]["records"]]
    second_ids = [row["id"] for row in second_specs[0]["records"]]
    assert first_ids == second_ids
    assert all(re.fullmatch(r"cx-[0-9a-f]{12}", identifier) for identifier in first_ids)
    assert _id_only_projection(first) == _id_only_projection(second)
    for operation in OPERATIONS:
        # An ID-only baseline memorizing all labels from one world necessarily
        # fails on its paired world with identical IDs, order and question.
        id_only_prediction = _solve_visible(operation, first)
        assert id_only_prediction != _solve_visible(operation, second)
