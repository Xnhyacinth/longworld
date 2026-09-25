"""Source-to-reader cell alignment for an exact Wikitext table revision."""

from longworld.synthesis.wiki_adapter import render_wikitext
from scripts.p104_wiki_table_grid import parse_tables
from scripts.p105_wiki_reader_cells import render_table, replace_in_document


def test_raw_cells_map_to_reader_spans() -> None:
    body = """== Institutions ==
{| class="wikitable"
! Name !! Type !! Region
|-
| A || Public || North
|-
| B || Public || South
|-
| C || Private || East
|-
| D || Private || East
|-
| E || Public || West
|-
| F || Public || West
|-
| G || Private || North
|-
| H || Community || South
|}
"""
    original = render_wikitext("Example", body).text
    raw_table = parse_tables(body)[0]
    table = render_table(body, raw_table.source_start_line, raw_table.source_end_line)
    reader, table_start, table_end = replace_in_document(
        title="Example", wikitext=body, frozen_reader_text=original, table=table
    )
    assert reader[table_start:table_end] == table.text
    assert table.cells[2][1].value == "Public"
    assert table.cells[2][1].raw_start != table.cells[1][1].raw_start
    for row in table.cells:
        for cell in row:
            assert body[cell.raw_start : cell.raw_end].strip()
            assert table.text[cell.reader_start : cell.reader_end] == cell.value


def test_spans_require_a_separate_task_semantics_proof() -> None:
    rows = "\n".join(f"| Entry {i} || Public || North\n|-" for i in range(8))
    source = "== Items ==\n{|\n! Name !! Type !! Region\n|-\n" + rows + "\n|}"
    source = source.replace(
        "| Entry 0 || Public || North", "| Entry 0 || rowspan=2 | Public || North"
    ).replace("| Entry 1 || Public || North", "| Entry 1 || North")
    raw_table = parse_tables(source)[0]
    assert raw_table.grid is not None
    try:
        render_table(source, raw_table.source_start_line, raw_table.source_end_line)
    except ValueError as error:
        assert "span-aware" in str(error)
    else:
        raise AssertionError("rowspan table was admitted")


def test_reader_replacement_rejects_source_revision_drift() -> None:
    rows = "\n".join(f"| Entry {i} || Public || North\n|-" for i in range(8))
    source = "== Items ==\n{|\n! Name !! Type !! Region\n|-\n" + rows + "\n|}"
    raw_table = parse_tables(source)[0]
    table = render_table(source, raw_table.source_start_line, raw_table.source_end_line)
    old = render_wikitext("Example", source).text
    try:
        replace_in_document(
            title="Example",
            wikitext=source.replace("Entry 0", "Entry Zero"),
            frozen_reader_text=old,
            table=table,
        )
    except ValueError as error:
        assert "exact revision differs" in str(error)
    else:
        raise AssertionError("source drift was accepted")
