"""Generic table admission and visible-text intervention regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis.p92_generic_table_scan import (
    answer,
    boundary_replay,
    intervals,
    parse_tables,
)
from scripts.run_p92_generic_table_scan import _prior_keys

ROOT = Path(__file__).resolve().parents[1]


def _table_text() -> str:
    return (
        "## Catalogue\n"
        "Name | Established | Region\n"
        "Alpha Park | 1980 | North\n"
        "Beta Park | 1970 | South\n"
        "Gamma Park | 1990 | East\n"
        "Delta Park | 1960 | West\n"
        "Epsilon Park | 2000 | North\n"
        "Zeta Park | 1995 | South\n"
        "Eta Park | 2010 | East\n"
        "Theta Park | 1985 | West\n"
        "\n## Other\nUnrelated text\n"
    )


def test_complete_row_universe_and_boundary_replay() -> None:
    text = _table_text()
    tables, rejected = parse_tables(text)
    assert not rejected and len(tables) == 1
    table = tables[0]
    assert len(table.rows) == 8
    assert text[table.rows[0].value_start : table.rows[0].value_end] == "1980"
    baseline = answer(table, 1980, 1990)
    assert baseline == {
        "count": 3,
        "entries": ["Alpha Park", "Gamma Park", "Theta Park"],
    }
    receipt = boundary_replay(text, table, 1980, 1990)
    assert receipt["hit_answer"]["count"] == 4
    assert receipt["changed_row"] == "Beta Park"
    assert intervals(table, 3)


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ("Beta Park | South", "row_width_mismatch"),
        ("Beta Park | unknown | South", "non_plain_year"),
        ("Alpha Park | 1970 | South", "duplicate_subject"),
    ],
)
def test_ambiguous_or_incomplete_row_rejects_whole_table(
    changed: str, reason: str
) -> None:
    text = _table_text().replace("Beta Park | 1970 | South", changed)
    tables, rejected = parse_tables(text)
    assert tables == ()
    assert any(reason in item for item in rejected)


def test_real_frozen_table_is_inferred_without_title_rules() -> None:
    pool = json.loads(
        (
            ROOT / "data/capability_records/p76_wiki_titles_v1/parks_snapshot.json"
        ).read_text()
    )
    doc = next(
        doc
        for doc in pool["documents"]
        if doc["title"] == "List of national parks of Japan"
    )
    tables, _ = parse_tables(doc["text"])
    assert len(tables) == 1
    assert tables[0].heading == "List of national parks"
    assert tables[0].year_column == "Established"
    assert len(tables[0].rows) == 35
    assert len(intervals(tables[0], 4)) == 4


@pytest.mark.parametrize(
    ("years", "expected_name", "order"),
    [
        ([1960, 1970, 1980, 1985, 1990, 1995, 2000, 2010], "Beta Park", "ascending"),
        ([2010, 2000, 1995, 1990, 1985, 1980, 1970, 1960], "Eta Park", "descending"),
    ],
)
def test_sorted_year_table_uses_order_preserving_boundary_replay(
    years: list[int], expected_name: str, order: str
) -> None:
    text = _table_text().replace("Beta Park | 1970", "Beta Park | 1981")
    lines = text.splitlines()
    for index, year in enumerate(years, start=2):
        parts = lines[index].split(" | ")
        parts[1] = str(year)
        lines[index] = " | ".join(parts)
    sorted_text = "\n".join(lines) + "\n"
    table = parse_tables(sorted_text)[0][0]
    receipt = boundary_replay(sorted_text, table, 1980, 1990)
    assert receipt["changed_row"] == expected_name
    assert receipt["original_year_order"] == order
    assert receipt["hit_answer"]["count"] == answer(table, 1980, 1990)["count"] + 1


def test_blank_line_does_not_certify_open_table_boundary() -> None:
    text = _table_text().replace("\n## Other", "\nAnother row may follow.\n## Other")
    tables, rejected = parse_tables(text)
    assert tables == ()
    assert any("ambiguous_table_end" in item for item in rejected)


def test_frozen_prior_bank_semantic_keys_cover_repeated_intervals() -> None:
    config = json.loads((ROOT / "configs/p92_generic_table_scan_v1.json").read_text())
    existing = _prior_keys(config)
    source_group = "snapshot_c363c5af8780a75d524b"
    doc_id = "doc-454979"
    assert (source_group, doc_id, "Established", 1987, 2012) in existing
    assert (source_group, doc_id, "Established", 1962, 2014) in existing
