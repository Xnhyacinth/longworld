"""Behavioral checks for a strict Wikitext table grid."""

import runpy
from pathlib import Path

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/p104_wiki_table_grid.py")
)
parse_tables = MODULE["parse_tables"]


def test_preserves_empty_cells_and_expands_header_spans() -> None:
    text = """{| class="wikitable"
|-
! rowspan="2" | Name
! colspan="2" | Screens
|-
! 2D !! 3D
|-
| Cinema A || 1 ||
|-
| Cinema B || 2 || 3
|}
"""
    result = parse_tables(text)[0]
    assert result.reason is None
    assert result.grid.width == 3
    assert [cell.raw for cell in result.grid.rows[1]] == ["Name", "2D", "3D"]
    assert [cell.raw for cell in result.grid.rows[2]] == ["Cinema A", "1", ""]


def test_rejects_missing_cells_instead_of_shifting_columns() -> None:
    text = """{| class="wikitable"
! Name !! City !! Type
|-
| A || Paris || Public
|-
| B || Private
|}
"""
    result = parse_tables(text)[0]
    assert result.grid is None
    assert result.reason == "row_width_mismatch_after_spans"


def test_rejects_rowspan_past_end_and_nested_table() -> None:
    overrun = "{|\n! rowspan=3 | Name !! Type\n|-\n| A\n|}"
    assert parse_tables(overrun)[0].reason == "rowspan_past_table_end"
    nested = "{|\n! Name !! Type\n|-\n| A || {|\n! Inner\n|}\n|}"
    assert parse_tables(nested)[0].reason == "nested_table"


def test_template_pipes_do_not_create_extra_columns() -> None:
    text = "{|\n! Name !! Coordinates\n|-\n| A || {{coord|1|N|2|E}}\n|}"
    result = parse_tables(text)[0]
    assert result.reason is None
    assert result.grid.width == 2
    assert result.grid.rows[1][1].raw == "{{coord|1|N|2|E}}"
