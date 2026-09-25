import hashlib

import pytest

from scripts.audit_p95_report_finance_shared import replay_answer
from scripts.audit_p95_report_selector_interventions import _replacement_quotes
from scripts.p95_report_finance_shared import compile_tasks
from scripts.p95_report_finance_to_unified import _normalize_pair


def _world(*, cash=(1, 3, 20, 24), revenue=(100, 200, 300, 400)):
    documents = [
        {
            "record_id": f"filing:{year}",
            "report_date": f"{year}-12-31",
        }
        for year in range(2021, 2025)
    ]
    facts = []
    for index, (doc, amount, operating_cash) in enumerate(
        zip(documents, revenue, cash, strict=True)
    ):
        for role, value in (
            ("revenue", amount),
            ("operating_income", (10, 40, 45, 120)[index]),
            ("cash_from_operations", operating_cash),
        ):
            facts.append(
                {
                    "fact_id": f"{doc['record_id']}:{role}",
                    "record_id": doc["record_id"],
                    "role": role,
                    "value": value,
                    "unit": "USD_millions",
                    "reporting_basis": "as_reported_in_filing",
                    "period": {"kind": "duration", "end": doc["report_date"]},
                }
            )
    return {"documents": documents, "facts": facts, "world_instance_id": "fake-world"}


def test_four_data_dependent_operations_select_distinct_programs():
    tasks, rejects = compile_tasks(_world())
    assert not rejects
    assert [task["operation"] for task in tasks] == [
        "middle_margin_then_cash",
        "largest_cash_jump_then_revenue",
        "max_revenue_growth_then_cash",
        "largest_margin_swing_then_cash",
    ]
    assert tasks[0]["answer"] == {
        "fiscal_year": "2023",
        "operating_cash_flow_usd_millions": 20,
    }
    assert tasks[1]["answer"] == {
        "transition": "2022–2023",
        "later_year_revenue_usd_millions": 300,
    }
    assert len(tasks[0]["fact_ids"]) == 9
    assert len(tasks[1]["fact_ids"]) == 5
    assert tasks[2]["answer"] == {
        "transition": "2021–2022",
        "later_year_operating_cash_flow_usd_millions": 3,
    }
    assert tasks[3]["answer"] == {
        "transition": "2023–2024",
        "later_year_operating_cash_flow_usd_millions": 24,
    }


def test_ambiguous_max_jump_rejects_only_that_operation():
    tasks, rejects = compile_tasks(_world(cash=(1, 3, 20, 41)))
    assert len(tasks) == 4
    tasks, rejects = compile_tasks(_world(cash=(1, 3, 20, 37)))
    assert len(tasks) == 3
    assert rejects == [
        {"operation": "largest_cash_jump_then_revenue", "reason": "cash_jump_tie"}
    ]


def test_missing_or_wrong_scoped_metric_rejects_world():
    world = _world()
    world["facts"][0]["unit"] = "USD_thousands"
    with pytest.raises(ValueError, match="missing compatible annual metric"):
        compile_tasks(world)
    world = _world()
    world["facts"].append(dict(world["facts"][0]))
    with pytest.raises(ValueError, match="ambiguous repeated"):
        compile_tasks(world)


def test_nonpositive_revenue_does_not_crash_ratio_path():
    tasks, rejects = compile_tasks(_world(revenue=(0, 200, 300, 400)))
    assert [task["operation"] for task in tasks] == ["largest_cash_jump_then_revenue"]
    assert rejects == [
        {"operation": operation, "reason": "nonpositive_revenue"}
        for operation in (
            "middle_margin_then_cash",
            "max_revenue_growth_then_cash",
            "largest_margin_swing_then_cash",
        )
    ]


def test_answer_replays_from_visible_cells_without_hidden_values():
    world = _world()
    tasks, _ = compile_tasks(world)
    parts, spans = [], {}
    cursor = 0
    for doc in world["documents"]:
        header = (
            f"=== Annual filing: {doc['record_id']} ===\n"
            f"Report date: {doc['report_date']}; filed: {doc['report_date']}\n"
        )
        parts.append(header)
        cursor += len(header)
        for fact in (f for f in world["facts"] if f["record_id"] == doc["record_id"]):
            raw = f"{fact['role']} | {fact['value']}\n"
            start = cursor + raw.index(str(fact["value"]))
            spans[fact["fact_id"]] = {
                "record_id": fact["record_id"],
                "role": fact["role"],
                "quote": str(fact["value"]),
                "context_span": [start, start + len(str(fact["value"]))],
            }
            parts.append(raw)
            cursor += len(raw)
    context = "".join(parts)
    for task in tasks:
        visible = [spans[identity] for identity in task["fact_ids"]]
        assert replay_answer(context, visible, task["operation"]) == task["answer"]
    median = tasks[0]
    evidence = [spans[identity] for identity in median["fact_ids"]]
    changed = next(
        item
        for item in evidence
        if item["record_id"] == "filing:2022" and item["role"] == "revenue"
    )
    start, end = changed["context_span"]
    altered = context[:start] + "900" + context[end:]
    with pytest.raises(ValueError, match="selected target not in bounded evidence"):
        replay_answer(altered, evidence, median["operation"])


def test_unified_adapter_accepts_only_reader_messages(tmp_path):
    receipt = tmp_path / "manifest.json"
    receipt.write_text("{}\n")
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    context = "Annual filing A: revenue 42."
    answer = '{"value":42}'
    reader = {
        "sample_id": "sample-one",
        "messages": [
            {"role": "user", "content": context + "\n\nQuestion: What value?"},
            {"role": "assistant", "content": answer},
        ],
    }
    index = {
        "sample_id": "sample-one",
        "semantic_task_id": "task-one",
        "source_group": "issuer-one",
        "source_kind": "real_finance",
        "domain": "finance",
        "topic": "Issuer One",
        "operation": "middle_margin_then_cash",
        "split": "train",
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "full_chat_tokens": 10,
        "input_tokens": 7,
        "supervised_tokens": 3,
        "length_bin": "lt32k",
    }
    normalized = _normalize_pair(index, reader, receipt, receipt_sha)
    assert normalized.task_key == '["real_finance","issuer-one","task-one"]'
    assert normalized.supervised_tokens == 3
    with pytest.raises(ValueError, match="hidden metadata"):
        _normalize_pair(index, {**reader, "trace": {}}, receipt, receipt_sha)


def test_selector_intervention_can_use_equal_width_parenthesized_loss():
    original = "2,334"
    options = list(_replacement_quotes(original, allow_signed=True))
    assert "(999)" in options
    assert all(len(value) == len(original) for value in options)
