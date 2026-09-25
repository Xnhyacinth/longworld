"""Map exact-revision Wikitext cells into a versioned reader table view."""

from __future__ import annotations

from dataclasses import dataclass

from longworld.synthesis.wiki_adapter import _clean_wikitext_inline, render_wikitext
from scripts.p104_wiki_table_grid import (
    Cell,
    Grid,
    _expand,
    _parse_cell,
    _split_cells,
    _top_level_attr_pipe,
    parse_tables,
)

EMPTY = "∅"


@dataclass(frozen=True)
class VisibleCell:
    row: int
    column: int
    value: str
    raw_start: int
    raw_end: int
    reader_start: int
    reader_end: int


@dataclass(frozen=True)
class ReaderTable:
    text: str
    cells: tuple[tuple[VisibleCell, ...], ...]
    grid: Grid
    source_start_line: int
    source_end_line: int


def _explicit_grid_with_spans(
    wikitext: str, start_line: int, end_line: int
) -> tuple[Grid, dict[int, tuple[int, int]]]:
    """Reparse one supported table, assigning exact source spans to cells."""
    lines = wikitext.splitlines(keepends=True)
    starts = []
    cursor = 0
    for raw in lines:
        starts.append(cursor)
        cursor += len(raw)
    rows: list[list[Cell]] = []
    current: list[Cell] = []
    spans: dict[int, tuple[int, int]] = {}
    if not lines[start_line - 1].lstrip().startswith("{|"):
        raise ValueError("raw table start moved")
    if not lines[end_line - 1].lstrip().startswith("|}"):
        raise ValueError("raw table end moved")
    for line_index in range(start_line, end_line - 1):
        original = lines[line_index].rstrip("\r\n")
        leading = len(original) - len(original.lstrip())
        line = original[leading:]
        if line.startswith("|-"):
            if current:
                rows.append(current)
                current = []
            continue
        if line.startswith("|+"):
            continue
        if line.startswith(("!", "|")):
            header = line.startswith("!")
            body = line[1:]
            parts = _split_cells(body, header)
            marker = "!!" if header else "||"
            if marker.join(parts) != body:
                raise ValueError("raw cell delimiter replay failed")
            relative = 0
            for part in parts:
                cell = _parse_cell(part, header)
                pipe = _top_level_attr_pipe(part.strip())
                stripped = part.strip()
                content = stripped[pipe + 1 :] if pipe is not None else stripped
                content_start = len(part) - len(part.lstrip())
                if pipe is not None:
                    content_start += pipe + 1
                content_start += len(content) - len(content.lstrip())
                absolute = starts[line_index] + leading + 1 + relative + content_start
                if wikitext[absolute : absolute + len(cell.raw)] != cell.raw:
                    raise ValueError("raw cell source span differs")
                spans[id(cell)] = (absolute, absolute + len(cell.raw))
                current.append(cell)
                relative += len(part) + 2
            continue
        if line.strip():
            raise ValueError("multiline or unattached cell not supported by P105")
    if current:
        rows.append(current)
    grid = _expand(rows, start_line, end_line)
    original_grid = next(
        (
            item.grid
            for item in parse_tables(wikitext)
            if item.source_start_line == start_line and item.source_end_line == end_line
        ),
        None,
    )
    if original_grid is None or (
        [[cell.raw for cell in row] for row in grid.rows]
        != [[cell.raw for cell in row] for row in original_grid.rows]
    ):
        raise ValueError("P104/P105 grid disagreement")
    return grid, spans


def render_table(wikitext: str, start_line: int, end_line: int) -> ReaderTable:
    grid, spans = _explicit_grid_with_spans(wikitext, start_line, end_line)
    if (
        len(grid.rows) < 9
        or grid.width < 3
        or not all(cell.header for cell in grid.rows[0])
    ):
        raise ValueError("table lacks a complete explicit header or enough rows")
    if any(cell.header for cell in grid.rows[1]):
        raise ValueError("multi-level header requires a separate adapter")
    if any(cell.rowspan != 1 or cell.colspan != 1 for row in grid.rows for cell in row):
        raise ValueError("spanned cell requires a span-aware complete-set task")
    rendered_lines = []
    visible_rows = []
    cursor = 0
    for row_number, row in enumerate(grid.rows):
        visible = []
        parts = []
        for column, cell in enumerate(row):
            value = _clean_wikitext_inline(cell.raw).strip() or EMPTY
            if " | " in value or "\n" in value or "\r" in value:
                raise ValueError("rendered cell contains a table delimiter/newline")
            if row_number == 0 and value == EMPTY:
                raise ValueError("unlabeled header column")
            if id(cell) not in spans:
                raise ValueError("raw cell origin missing")
            start = cursor + sum(len(part) + 3 for part in parts)
            end = start + len(value)
            raw_start, raw_end = spans[id(cell)]
            visible.append(
                VisibleCell(row_number, column, value, raw_start, raw_end, start, end)
            )
            parts.append(value)
        rendered_lines.append(" | ".join(parts))
        visible_rows.append(tuple(visible))
        cursor += len(rendered_lines[-1]) + 1
    text = "\n".join(rendered_lines)
    if any(
        text[cell.reader_start : cell.reader_end] != cell.value
        for row in visible_rows
        for cell in row
    ):
        raise ValueError("reader cell position drift")
    return ReaderTable(text, tuple(visible_rows), grid, start_line, end_line)


def replace_in_document(
    *, title: str, wikitext: str, frozen_reader_text: str, table: ReaderTable
) -> tuple[str, int, int]:
    """Replace the one old-renderer table slice; retain all other reader bytes."""
    if render_wikitext(title, wikitext).text != frozen_reader_text:
        raise ValueError("exact revision differs from frozen reader document")
    lines = wikitext.splitlines()
    raw_table = "\n".join(lines[table.source_start_line - 1 : table.source_end_line])
    old_slice = render_wikitext(title, raw_table).text.split("\n", 1)[-1].strip()
    if not old_slice or frozen_reader_text.count(old_slice) != 1:
        raise ValueError("old rendered table is not unique in reader document")
    start = frozen_reader_text.index(old_slice)
    end = start + len(old_slice)
    return (
        frozen_reader_text[:start] + table.text + frozen_reader_text[end:],
        start,
        start + len(table.text),
    )
