"""Regression checks for complete projected table universes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis.p95_semiclosed_table import boundary_replay, parse_tables


def _table() -> str:
    return (
        "## Parks\n"
        "Park | District | Established | Notes\n"
        "Alpha | North | 1960 | old\n"
        "Beta | South | 1970\n"
        "Gamma | East | 1980 | new\n"
        "Delta | West | 1990\n"
        "Epsilon | North | 2000 | old\n"
        "Zeta | South | 2010\n"
        "Eta | East | 2020 | new\n"
        "Theta | West | 2030\n"
        "\n## References\nSource\n"
    )


def test_optional_tail_columns_keep_every_name_and_year() -> None:
    text = _table()
    tables, rejected = parse_tables(text)
    assert not rejected and len(tables) == 1
    table = tables[0]
    assert [row.name for row in table.rows] == [
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
        "Epsilon",
        "Zeta",
        "Eta",
        "Theta",
    ]
    assert [row.year for row in table.rows] == list(range(1960, 2040, 10))
    assert all(
        text[row.value_start : row.value_end] == str(row.year) for row in table.rows
    )
    intervention = boundary_replay(text, table, 1980, 2000)
    assert intervention["hit_answer"]["entries"] == [
        "Beta",
        "Delta",
        "Epsilon",
        "Gamma",
    ]


@pytest.mark.parametrize(
    ("changed", "replacement", "reason"),
    [
        ("Beta | South | 1970", "Beta | South", "required_cell_or_row_width"),
        ("Beta | South | 1970", "Beta | South | c. 1970", "non_plain_year"),
        ("Beta | South | 1970", "Alpha | South | 1970", "duplicate_subject"),
        ("\n## References", "\nUnmarked prose\n## References", "ambiguous_table_end"),
    ],
)
def test_missing_required_cells_or_uncertain_boundary_reject_whole_table(
    changed: str, replacement: str, reason: str
) -> None:
    tables, rejected = parse_tables(_table().replace(changed, replacement))
    assert not tables
    assert any(reason in item for item in rejected)


def test_uniform_width_table_remains_owned_by_strict_recipe() -> None:
    text = _table().replace("Beta | South | 1970\n", "Beta | South | 1970 | x\n")
    text = text.replace("Delta | West | 1990\n", "Delta | West | 1990 | x\n")
    text = text.replace("Zeta | South | 2010\n", "Zeta | South | 2010 | x\n")
    text = text.replace("Theta | West | 2030\n", "Theta | West | 2030 | x\n")
    assert parse_tables(text)[0] == ()


def test_duplicate_reader_visible_table_keys_reject_all_candidates() -> None:
    first = _table().split("\n## References", 1)[0]
    second = _table().split("\n", 1)[1]
    tables, rejected = parse_tables(first + "\n## Parks\n" + second)
    assert tables == ()
    assert rejected.count("Parks/Established:ambiguous_visible_table_key") == 2


def test_malformed_duplicate_header_still_makes_good_table_ambiguous() -> None:
    first = _table().split("\n## References", 1)[0]
    second = (
        _table()
        .split("\n", 1)[1]
        .replace("Beta | South | 1970", "Beta | South | circa 1970")
    )
    tables, rejected = parse_tables(first + "\n## Parks\n" + second)
    assert tables == ()
    assert "Parks/Established:non_plain_year" in rejected
    assert "Parks/Established:ambiguous_visible_table_key" in rejected


def test_frozen_recovered_tables_have_complete_visible_row_universes() -> None:
    root = Path(__file__).resolve().parents[1]
    pool = json.loads(
        (
            root
            / "data/capability_records/p94_wiki_structural_merge_v2/source_pool.json"
        ).read_text()
    )
    observed = {}
    for source in pool["sources"]:
        if source["name"] not in {"p93_national_parks_01_01", "p93_schools_01_02"}:
            continue
        snapshot = json.loads((root / source["snapshot"]["path"]).read_text())
        for doc in snapshot["documents"]:
            for table in parse_tables(doc["text"])[0]:
                observed[(doc["title"], table.heading, table.header)] = len(table.rows)
    assert {
        (title, heading): count for (title, heading, _header), count in observed.items()
    } == {
        ("List of national parks of Nigeria", "Parks"): 9,
        ("List of schools in the Australian Capital Territory", "Public schools"): 52,
        (
            "List of schools in the Australian Capital Territory",
            "Non-Government schools",
        ): 9,
    }
