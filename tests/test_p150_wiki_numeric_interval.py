"""Numeric table tasks exclude qualified cells and require causal row edits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p126_wiki_html_table_tasks as tables
from scripts import p150_wiki_numeric_interval as numeric


def test_plain_numeric_and_calendar_year_contract() -> None:
    assert numeric._parse_number("12", "integer_count") == 12
    assert numeric._parse_number("1914", "calendar_year") == 1914
    for value in ("", "2–3", "4 (including perpetrator)", "2 [ref]", "about 2"):
        assert numeric._parse_number(value, "integer_count") is None
    assert numeric._parse_number("14", "calendar_year") is None


def test_reader_hit_removal_and_near_miss_control() -> None:
    values = ["0", "1", "2", "2", "3", "3", "4", "4", "", "about 3", "5", "6"]
    text = "Wikipedia article: Example\nTABLE_START\nDead\n"
    spans = {}
    for index, value in enumerate(values):
        spans[index, 0] = (len(text), len(text) + len(value))
        text += value + "\n"
    text += "TABLE_END"
    assert numeric.answer(text, 0, 2, 3, "integer_count") == {
        "count": 4,
        "eligible_rows": 10,
    }
    changed = numeric.interventions(
        text, spans, {"column": 0, "low": 2, "high": 3, "unit": "integer_count"}
    )
    assert changed["hit_edit_answer"] == {"count": 5, "eligible_rows": 10}
    assert changed["control_answer"] == {"count": 4, "eligible_rows": 10}
    assert changed["removal_answer"] == {"count": 3, "eligible_rows": 10}


def test_frozen_html_grid_yields_supported_numeric_options() -> None:
    root = Path("data/candidates/p148_wiki_category_catalog_v1")
    path = root / "p122_grid/valid_grids.jsonl"
    if not path.exists():
        pytest.skip("P148 source grid not present")
    config = json.loads(Path("configs/p150_wiki_numeric_interval_v1.json").read_text())
    entries = [json.loads(raw) for raw in path.read_text().splitlines()]
    first = entries[0]
    assert first["title"] == "List of attacks related to post-secondary schools"
    source = json.loads((root / "p122_source/source_manifest.json").read_text())
    page = next(row for row in source["records"] if row["title"] == first["title"])
    html = tables.pin(
        {"path": page["html_path"], "sha256": page["html_sha256"]}
    ).read_text()
    choices, _ = numeric.options(first, html, config)
    assert {"dead", "injured"}.issubset(
        {" ".join(choice["header"]).casefold() for choice in choices}
    )
    context, _ = tables.render_context(first)
    for choice in choices:
        assert numeric.answer(
            context,
            choice["column"],
            choice["low"],
            choice["high"],
            choice["unit"],
        ) == {"count": choice["hits"], "eligible_rows": choice["eligible_rows"]}
