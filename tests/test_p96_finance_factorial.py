from scripts.audit_p96_finance_factorial import visible_replay
from scripts.audit_p96_finance_interventions import intervene
from scripts.p96_finance_factorial import compile_world, execute, programs


def _world():
    years = (2021, 2022, 2023, 2024)
    values = {
        "revenue": (100, 120, 90, 150),
        "operating_income": (10, 14, 3, 30),
        "cash_from_operations": (20, 25, 10, 40),
        "cash_from_investing": (-5, -6, -4, -8),
        "cash_from_financing": (1, 2, 3, 4),
    }
    documents = [
        {"record_id": f"filing:{year}", "report_date": f"{year}-12-31"}
        for year in years
    ]
    facts = [
        {
            "record_id": doc["record_id"],
            "fact_id": f"{doc['record_id']}:{metric}",
            "role": metric,
            "value": values[metric][index],
            "unit": "USD_millions",
            "reporting_basis": "as_reported_in_filing",
            "period": {"kind": "duration", "end": doc["report_date"]},
        }
        for index, doc in enumerate(documents)
        for metric in values
    ]
    return {
        "documents": documents,
        "facts": facts,
        "world_instance_id": "fixture-world",
    }


def test_declarative_product_has_distinct_legal_selector_target_pairs():
    specs = programs()
    signatures = {
        (p["selector"]["kind"], tuple(p["selector"]["metrics"]), p["target_metric"])
        for p in specs
    }
    assert len(signatures) == len(specs) >= 80
    assert all(p["target_metric"] not in p["selector"]["metrics"] for p in specs)


def test_executor_uses_intermediate_selection_before_target_lookup():
    world = _world()
    rows = [
        {
            "year": doc["report_date"][:4],
            **{
                f["role"]: f["value"]
                for f in world["facts"]
                if f["record_id"] == doc["record_id"]
            },
        }
        for doc in world["documents"]
    ]
    program = {
        "selector": {"kind": "max_abs_delta", "metrics": ["revenue"]},
        "target_metric": "cash_from_operations",
    }
    result = execute(rows, program)
    assert result["selected_index"] == 3
    assert result["answer"] == {
        "transition": "2023–2024",
        "target_metric": "cash_from_operations",
        "later_year_value_usd_millions": 40,
    }
    changed = [{**row} for row in rows]
    changed[1]["revenue"] = 300
    shifted = execute(changed, program)
    assert shifted["selected_index"] == 2
    assert shifted["answer"] != result["answer"]


def test_world_compiler_blocks_prior_equivalents_and_caps_answers():
    world = _world()
    tasks, matrix, report = compile_world(world, max_tasks=16, answer_cap=1)
    assert len(matrix) == len(programs())
    assert 1 <= len(tasks) <= 16
    assert len({task["semantic_task_id"] for task in tasks}) == len(tasks)
    assert report["cell_statuses"]["blocked_prior_equivalent"] == 5
    assert report["cell_statuses"]["selected"] == len(tasks)
    assert all(
        task["program"]["target_metric"] not in task["program"]["selector"]["metrics"]
        for task in tasks
    )
    assert len({task["operation"] for task in tasks}) == len(tasks)


def test_ambiguous_or_wrong_scope_cells_are_rejected():
    world = _world()
    for fact in world["facts"]:
        if fact["role"] == "cash_from_investing":
            fact["unit"] = "USD_thousands"
    tasks, matrix, report = compile_world(world, max_tasks=8)
    assert tasks
    assert report["cell_statuses"]["unsupported_or_degenerate"] > 0
    assert all(
        task["program"]["target_metric"] != "cash_from_investing" for task in tasks
    )
    assert any(
        cell["reason"] == "required metric missing or not an integer" for cell in matrix
    )


def test_final_visible_cell_replay_recovers_declared_program_answer():
    world = _world()
    tasks, _, _ = compile_world(world, max_tasks=12)
    pieces, spans = [], {}
    cursor = 0
    for doc in world["documents"]:
        header = (
            f"=== Annual filing: {doc['record_id']} ===\n"
            f"Report date: {doc['report_date']}; filed: {doc['report_date']}\n"
        )
        pieces.append(header)
        cursor += len(header)
        for fact in (f for f in world["facts"] if f["record_id"] == doc["record_id"]):
            line = f"{fact['role']} | {fact['value']}\n"
            start = cursor + line.index(str(fact["value"]))
            spans[fact["fact_id"]] = {
                "record_id": fact["record_id"],
                "role": fact["role"],
                "quote": str(fact["value"]),
                "context_span": [start, start + len(str(fact["value"]))],
            }
            pieces.append(line)
            cursor += len(line)
    context = "".join(pieces)
    for task in tasks:
        answer, selected_record = visible_replay(
            context, [spans[identity] for identity in task["fact_ids"]], task["program"]
        )
        assert answer == task["answer"]
        assert selected_record == task["trace"]["selected_record_id"]
    program = {
        "selector": {"kind": "max_abs_delta", "metrics": ["revenue"]},
        "target_metric": "cash_from_operations",
    }
    baseline, selected = visible_replay(context, list(spans.values()), program)
    changed = intervene(context, list(spans.values()), program, baseline, selected)
    assert changed is not None
    assert changed["old_selected_record"] != changed["new_selected_record"]
    assert changed["old_answer"] != changed["intervened_answer"]
