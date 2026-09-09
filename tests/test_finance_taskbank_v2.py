from copy import deepcopy

import pytest

from longworld.core import finance_taskbank_v2 as v2
from tests.test_finance_taskbank import world as source_world


@pytest.fixture
def world(tmp_path, monkeypatch):
    return source_world.__wrapped__(tmp_path, monkeypatch)


def test_cross_metric_filter_consumes_selection_and_target(world):
    bank = v2.compile_taskbank(world)
    task = next(
        t
        for t in bank["tasks"]
        if t["family"] == "conditional_cross_metric_sum"
        and len(t["scope"]["filings"]) == 4
        and t["program"]["input"]["input"]["input"]["metric"] == "cash_from_investing"
        and t["program"]["input"]["input"]["relation"] == "lt_zero"
    )
    assert task["answer"]["total"] == 105
    assert len(task["answer"]["selected"]) == 3
    assert len(task["consumed_fact_ids"]) == 7
    trace = task["execution_trace"]
    assert trace[1]["output_record_ids"] == trace[2]["input_record_ids"]
    assert trace[2]["output_record_ids"] == trace[3]["input_record_ids"]
    assert v2.validate_task(world, task)["ok"]


def test_margin_comparison_is_exact_and_preserves_tied_periods(world):
    task = next(
        t
        for t in v2.compile_taskbank(world)["tasks"]
        if t["family"] == "max_margin_selected_cashflow"
        and len(t["scope"]["filings"]) == 4
    )
    assert task["answer"]["selected"] == [
        {"period_end": "2021-12-31", "value": 30},
        {"period_end": "2024-12-31", "value": 80},
    ]


def test_extreme_selection_then_cashflow_difference(world):
    task = next(
        t
        for t in v2.compile_taskbank(world)["tasks"]
        if t["family"] == "margin_extremes_cashflow_difference"
        and t["scope"]["filings"][-1]["report_date"] == "2023-12-31"
    )
    assert task["answer"]["highest_minus_lowest"] == -10
    assert task["operators"] == [
        "margin_series",
        "select_margin_extremes",
        "lookup_selected",
        "difference_extremes",
    ]


def test_v2_scope_answer_trace_and_type_are_bound(world):
    task = next(
        t
        for t in v2.compile_taskbank(world)["tasks"]
        if t["compiler_revision"] == v2.REVISION
    )
    for field, value in (
        ("question", "Guess an amount"),
        ("answer", {}),
        ("execution_trace", []),
    ):
        changed = deepcopy(task)
        changed[field] = value
        with pytest.raises(ValueError):
            v2.validate_task(world, changed)


def test_base_ids_preserved_and_new_count_conserves(world):
    original = v2.base.compile_finance_taskbank(world)
    expanded = v2.compile_taskbank(world)
    assert {t["semantic_task_id"] for t in original["tasks"]} <= {
        t["semantic_task_id"] for t in expanded["tasks"]
    }
    m = expanded["metrics"]
    assert m["dependent_attempted"] == m["dependent_accepted"] + m["dependent_rejected"]
    assert m["accepted_tasks"] == m["base_accepted_tasks"] + m["dependent_accepted"]


def test_unknown_program_is_contract_error(world):
    with pytest.raises(ValueError):
        v2.build_task(world, {"op": "invent_cause"})


def test_complete_v2_funnel_includes_dependent_programs(world):
    bank = v2.compile_taskbank(world)
    m = bank["metrics"]
    assert m["attempted_programs"] == m["accepted_tasks"] + m["rejected_tasks"]
    assert sum(m["by_fact_count"].values()) == m["accepted_tasks"]
    assert m["within_filing_tasks"] + m["cross_filing_tasks"] == m["accepted_tasks"]
