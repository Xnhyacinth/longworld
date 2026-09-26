"""P117 structure-first routing checks."""

import pytest

from scripts.p117_wiki_shape_intake import (
    entity_header,
    entity_tables,
    legal_shape,
    stem,
)


def test_seed_stem_requires_list_and_keeps_generic_subject() -> None:
    assert stem("List of medical colleges in India") == "List of medical colleges"
    assert stem("List of railway stations in Turin") == "List of railway stations"
    assert stem("Medical colleges in India") is None
    assert stem("List of cats") is None


def test_structural_gate_rejects_incomplete_table_even_with_right_title() -> None:
    good = (
        "## Institutions\nName | Region | Type\n"
        + "".join(
            f"College {i} | Region {i % 3} | {'Public' if i % 2 else 'Private'}\n"
            for i in range(8)
        )
        + "\n"
    )
    year, category, _ = legal_shape(good, 4)
    assert year == 0 and category > 0
    malformed = good.replace("College 7 | Region 1 | Public", "College 7 | Public")
    year, category, rejected = legal_shape(malformed, 4)
    assert year == category == 0
    assert any("row_width_mismatch" in item for item in rejected)


def test_pin_validation_rejects_unpinned_path() -> None:
    from scripts.p117_wiki_shape_intake import pin

    with pytest.raises(ValueError, match="requires path"):
        pin({"path": "data/source.json"})


def test_generic_entity_column_handles_station_without_accepting_rank() -> None:
    assert entity_header("Station")
    assert entity_header("Station name")
    assert entity_header("University")
    assert not entity_header("Rank")
    assert not entity_header("Year")
    assert not entity_header("Unclear{entity}")
    text = (
        "## Stations\nStation name | Province | Category\n"
        + "".join(f"Station {i} | North | {'Gold' if i % 2 else 'Silver'}\n" for i in range(8))
        + "\n"
    )
    tables, rejected = entity_tables(text)
    assert not rejected and len(tables) == 1
    assert tables[0].header == "Station name | Province | Category"
    assert legal_shape(text, 4, generic=True)[1] > 0
    rank_table = text.replace("Station name |", "Rank |")
    assert not entity_tables(rank_table)[0]


def test_repeated_header_inside_one_section_rejects_whole_table() -> None:
    rows = "".join(
        f"Stop {i} | North | {'Gold' if i % 2 else 'Silver'}\n"
        for i in range(8)
    )
    text = (
        "## List\nStation | Province | Category\n"
        + rows
        + "Station | Province | Category\n"
        + rows.replace("Stop", "Other Stop")
        + "\n"
    )
    tables, rejected = entity_tables(text)
    assert not tables
    assert any(row["reason"] == "repeated_header_within_section" for row in rejected)
