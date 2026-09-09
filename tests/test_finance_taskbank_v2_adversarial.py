"""Independent semantic checks for dependent selection; no source mutation."""

from copy import deepcopy
from fractions import Fraction

import pytest

from longworld.core import finance_taskbank_v2 as v2
from tests.test_finance_taskbank import world as world_fixture


@pytest.fixture
def world(tmp_path, monkeypatch):
    return world_fixture.__wrapped__(tmp_path, monkeypatch)


def test_every_generated_dependent_answer_matches_independent_selection(world):
    facts = {(f["record_id"], f["role"]): f for f in world["facts"]}
    docs = {d["record_id"]: d for d in world["documents"]}
    count = 0
    for task in v2.compile_taskbank(world)["tasks"]:
        if task["compiler_revision"] != v2.REVISION:
            continue
        count += 1
        program = task["program"]
        lookup = program if program["op"] == "lookup_selected" else program["input"]
        selector = lookup["input"]
        series = selector["input"]
        ids = series["record_ids"]
        if selector["op"] == "select_sign":
            values = {i: facts[i, series["metric"]]["value"] for i in ids}
            selected = [
                i
                for i in ids
                if (
                    values[i] > 0
                    if selector["relation"] == "gt_zero"
                    else values[i] < 0
                )
            ]
            roles = [series["metric"]]
        else:
            ratios = {
                i: Fraction(
                    facts[i, "operating_income"]["value"], facts[i, "revenue"]["value"]
                )
                for i in ids
            }
            highest = [i for i in ids if ratios[i] == max(ratios.values())]
            if selector["op"] == "select_margin_extremes":
                selected = [
                    i for i in ids if ratios[i] == min(ratios.values())
                ] + highest
            else:
                selected = highest
            roles = ["operating_income", "revenue"]
        amounts = [facts[i, lookup["metric"]]["value"] for i in selected]
        expected = {
            "unit": "USD_millions",
            "selected": [
                {"period_end": docs[i]["report_date"], "value": value}
                for i, value in zip(selected, amounts, strict=True)
            ],
        }
        if program["op"] == "sum_selected":
            expected["total"] = sum(amounts)
        elif program["op"] == "difference_extremes":
            expected["highest_minus_lowest"] = amounts[1] - amounts[0]
        assert task["answer"] == expected
        expected_consumed = {facts[i, role]["fact_id"] for i in ids for role in roles}
        expected_consumed |= {facts[i, lookup["metric"]]["fact_id"] for i in selected}
        assert set(task["consumed_fact_ids"]) == expected_consumed
        assert {d["record_id"] for d in task["scope"]["filings"]} == set(ids)
    assert count > 0


def test_float_collapsing_margins_are_ranked_as_exact_fractions(world):
    # Exercise the arithmetic operator directly with large integer quantities.
    # This is an operator fixture, not a claimed source-verified altered world.
    operator_world = deepcopy(dict(world))
    for f in operator_world["facts"]:
        if f["role"] == "revenue":
            f["value"] = 10**20
        if f["role"] == "operating_income":
            f["value"] = 10**20 + int(f["record_id"].split("-")[-1])
    ids = [d["record_id"] for d in world["documents"]]
    result = v2._eval(
        operator_world,
        {
            "op": "select_max_margin",
            "input": {"op": "margin_series", "record_ids": ids},
        },
    )
    assert [r["record_id"] for r in result.rows] == ["doc-2024"]


def test_duplicate_extreme_ties_rejected_but_max_ties_retained(world):
    ids = [d["record_id"] for d in world["documents"]]
    margin = {"op": "margin_series", "record_ids": ids}
    with pytest.raises(ValueError, match="ties"):
        v2._eval(world, {"op": "select_margin_extremes", "input": margin})
    result = v2._eval(world, {"op": "select_max_margin", "input": margin})
    assert [r["record_id"] for r in result.rows] == ["doc-2021", "doc-2024"]


def test_missing_fact_and_hidden_parameter_are_rejected(world):
    task = next(
        t
        for t in v2.compile_taskbank(world)["tasks"]
        if t["compiler_revision"] == v2.REVISION
    )
    program = deepcopy(task["program"])
    program["gold_total"] = task["answer"]
    with pytest.raises(ValueError, match="fields"):
        v2.build_task(world, program)
    operator_world = deepcopy(dict(world))
    operator_world["facts"] = [
        f for f in operator_world["facts"] if f["role"] != "revenue"
    ]
    with pytest.raises(ValueError, match="missing"):
        v2._eval(
            operator_world,
            {
                "op": "margin_series",
                "record_ids": [d["record_id"] for d in world["documents"]],
            },
        )
