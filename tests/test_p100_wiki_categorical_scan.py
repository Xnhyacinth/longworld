"""Behavioral checks for the complete categorical table reader path."""

from scripts.p100_wiki_categorical_scan import (
    _intervene,
    answer,
    options,
    parse_tables,
)

ROWS = (
    "Aster | North | Public\n"
    "Birch | North | Public\n"
    "Cedar | South | Private\n"
    "Dogwood | South | Private\n"
    "Elm | East | Public\n"
    "Fir | East | Public\n"
    "Ginkgo | West | Private\n"
    "Holly | West | Community\n"
)
TEXT = "## Institutions\nName | Region | Type\n" + ROWS + "\n"


def test_complete_set_replays_after_visible_cell_edit() -> None:
    tables, rejected = parse_tables(TEXT)
    assert not rejected
    assert len(tables) == 1
    table = tables[0]
    assert (2, "Public") in options(table, max_tasks=8)
    baseline = answer(table, 2, "Public")
    assert baseline == {
        "count": 4,
        "entries": ["Aster", "Birch", "Elm", "Fir"],
    }
    context = "=== DOCUMENT ===\n" + TEXT + "\nQUESTION\n"
    edit = _intervene(context, len("=== DOCUMENT ===\n"), TEXT, table, 2, "Public")
    assert edit["hit_answer"]["count"] == 5
    assert edit["control_answer"] == baseline
    assert edit["old_value"] != edit["hit_value"]


def test_incomplete_or_repeated_visible_tables_are_not_eligible() -> None:
    malformed = TEXT.replace("Holly | West | Community", "Holly | West")
    assert not parse_tables(malformed)[0]
    assert any(
        row["reason"] == "row_width_mismatch" for row in parse_tables(malformed)[1]
    )
    repeated = TEXT + "## Institutions\nName | Region | Type\n" + ROWS + "\n"
    assert not parse_tables(repeated)[0]
    assert all(
        row["reason"] == "ambiguous_visible_key" for row in parse_tables(repeated)[1]
    )


def test_nonplain_category_column_is_excluded_without_dropping_rows() -> None:
    altered = TEXT.replace("Aster | North | Public", "Aster | North | {Public}")
    table = parse_tables(altered)[0][0]
    assert all(column != 2 for column, _value in options(table, max_tasks=8))


def test_shifted_numeric_column_is_not_mislabelled_categorical() -> None:
    numeric = (
        "## Estates\nName | No Units | Type\n"
        + "".join(f"Estate {i} | {i % 3 + 1} | Public\n" for i in range(8))
        + "\n"
    )
    table = parse_tables(numeric)[0][0]
    assert all(column != 1 for column, _value in options(table, max_tasks=8))
