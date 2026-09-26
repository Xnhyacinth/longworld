import pytest

from scripts.p112_report_route import (
    _selector_flip,
    _target_flip,
    execute_visible,
    shortcut_reason,
)


def _fixture():
    years = (2021, 2022, 2023, 2024)
    revenue = (100, 120, 180, 140)
    cash = (10, 20, 35, 50)
    parts = []
    cells = {}
    cursor = 0
    for year, r, c in zip(years, revenue, cash, strict=True):
        record = f"filing:{year}"
        for text in (
            f"=== Annual filing: {record} ===\nReport date: {year}-12-31\n",
            "USD millions\n",
        ):
            parts.append(text)
            cursor += len(text)
        for role, value in (("revenue", r), ("cash_from_operations", c)):
            prefix = role + "\t"
            quote = str(value)
            parts.extend((prefix, quote, "\n"))
            cursor += len(prefix)
            cells[record, role] = {
                "record_id": record,
                "role": role,
                "quote": quote,
                "context_span": [cursor, cursor + len(quote)],
            }
            cursor += len(quote) + 1
    return "".join(parts), cells


def test_selected_period_controls_distinct_reference_year_delta():
    context, cells = _fixture()
    program = {
        "selector_kind": "max_value",
        "selector_metric": "revenue",
        "target_metric": "cash_from_operations",
        "baseline_endpoint": "earliest",
    }
    answer, lineage, selected = execute_visible(context, cells, program)
    assert answer["selected_fiscal_year"] == "2023"
    assert answer["change_from_reference_year_usd_millions"] == 25
    assert answer["reference_fiscal_year"] == "2021"
    assert selected == "filing:2023"
    assert lineage[-2:] == [
        ("filing:2021", "cash_from_operations"),
        ("filing:2023", "cash_from_operations"),
    ]
    assert shortcut_reason(context, cells, lineage, answer) is None
    assert _selector_flip(context, cells, program, answer, selected) is not None
    assert _target_flip(context, cells, program, answer, lineage) is not None
    with pytest.raises(KeyError):
        execute_visible(
            context,
            {key: value for key, value in cells.items() if key != lineage[-2]},
            program,
        )


def test_invalid_or_unsupported_source_shape_is_rejected():
    context, cells = _fixture()
    program = {
        "selector_kind": "max_value",
        "selector_metric": "revenue",
        "target_metric": "cash_from_operations",
        "baseline_endpoint": "earliest",
    }
    changed = {**cells}
    changed["filing:2024", "revenue"] = cells["filing:2023", "revenue"]
    with pytest.raises(ValueError, match="selector_tie"):
        execute_visible(context, changed, program)
    with pytest.raises(ValueError, match="invalid source-operation pairing"):
        execute_visible(context, cells, {**program, "target_metric": "revenue"})


def test_comparative_row_shortcut_is_rejected():
    context, cells = _fixture()
    program = {
        "selector_kind": "max_value",
        "selector_metric": "revenue",
        "target_metric": "cash_from_operations",
        "baseline_endpoint": "earliest",
    }
    answer, lineage, _ = execute_visible(context, cells, program)
    end = cells["filing:2023", "cash_from_operations"]["context_span"][1]
    shortcut_context = context[:end] + "\t10" + context[end:]
    assert (
        shortcut_reason(shortcut_context, cells, lineage, answer)
        == "one_report_contains_both_target_numbers"
    )


def test_latest_endpoint_pairs_with_earliest_selected_year():
    context, cells = _fixture()
    program = {
        "selector_kind": "min_value",
        "selector_metric": "revenue",
        "target_metric": "cash_from_operations",
        "baseline_endpoint": "latest",
    }
    answer, lineage, selected = execute_visible(context, cells, program)
    assert selected == "filing:2021"
    assert answer == {
        "selected_fiscal_year": "2021",
        "reference_fiscal_year": "2024",
        "target_metric": "cash_from_operations",
        "change_from_reference_year_usd_millions": -40,
    }
    assert lineage[-2:] == [
        ("filing:2024", "cash_from_operations"),
        ("filing:2021", "cash_from_operations"),
    ]
    assert shortcut_reason(context, cells, lineage, answer) is None
