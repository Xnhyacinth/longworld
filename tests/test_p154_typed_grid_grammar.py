from scripts import p154_typed_grid_grammar as task


def _reader() -> tuple[str, dict[tuple[int, int], tuple[int, int]]]:
    rows = ["100", "120", "130", "140", "150", "160", "170", "180", "190", "200"]
    context = "Wikipedia article: Example\nTABLE_START\nCapacity\n"
    spans = {}
    for index, value in enumerate(rows):
        spans[index, 0] = (len(context), len(context) + len(value))
        context += value + "\n"
    return context + "TABLE_END", spans


def test_typed_header_and_strict_numeric_parse() -> None:
    assert task.column_type(["Capacity (MW)"]) == "capacity_integer"
    assert task.column_type(["Built"]) == "calendar_year"
    assert task.column_type(["Rank"]) is None
    assert task.parse_number("10,200", "capacity_integer") == 10200
    assert task.parse_number("10,20", "capacity_integer") is None
    assert task.parse_number("2025", "calendar_year") == 2025
    assert task.parse_number("2025 [1]", "calendar_year") is None


def test_interval_count_and_sum_respond_to_reader_edits() -> None:
    context, spans = _reader()
    common = {"column": 0, "kind": "capacity_integer", "low": 130, "high": 170}
    assert task.evaluate(context, **common, operation="interval_count") == {
        "count": 5,
        "eligible_rows": 10,
    }
    assert task.evaluate(context, **common, operation="interval_sum") == {
        "sum": 750,
        "count": 5,
    }
    for operation in ("interval_count", "interval_sum"):
        result = task.interventions(context, spans, {**common, "operation": operation})
        assert result["incoming_answer"]["count"] == 6
        assert result["removal_answer"]["count"] == 4
        assert result["control_answer"] == result["original"]
    summation = task.interventions(context, spans, {**common, "operation": "interval_sum"})
    assert summation["within_interval_answer"]["count"] == 5
    assert summation["within_interval_answer"]["sum"] != 750


def test_nontrivial_intervals_are_parameterized() -> None:
    values = list(range(1, 21))
    options = task.intervals(values, 3)
    assert len(options) >= 2
    assert all(low < high for low, high in options)
    assert all(3 <= sum(low <= x <= high for x in values) <= 17 for low, high in options)


def test_ambiguous_visible_numeric_source_rejects_whole_column() -> None:
    html = "<span aria-hidden='true'>100</span>" + "200" * 9
    origins = {}
    offset = 0
    for index in range(10):
        fragment = "<span aria-hidden='true'>100</span>" if index == 0 else "200"
        origins[str(index)] = {
            "text": "100" if index == 0 else "200",
            "html_span": [offset, offset + len(fragment)],
            "rowspan": 1,
            "colspan": 1,
            "footnote_refs": [],
            "citation_markers": [],
        }
        offset += len(fragment)
    entry = {
        "grid": {
            "rows": [[str(index)] for index in range(10)],
            "header_rows": 0,
            "header_paths": [["Capacity"]],
            "origins": origins,
        }
    }
    options, reasons = task.choices(
        entry,
        html,
        {
            "minimum_numeric_rows": 8,
            "minimum_hits": 3,
            "intervals_per_column": 2,
            "operators_by_type": {
                "calendar_year": ["interval_count"],
                "capacity_integer": ["interval_count", "interval_sum"],
            },
        },
    )
    assert not options
    assert reasons["ambiguous_visible_numeric_support"] == 1
