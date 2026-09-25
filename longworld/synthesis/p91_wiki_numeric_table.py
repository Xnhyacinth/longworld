"""Conservative numeric scans of a closed, visible Wikipedia table.

This compiler reads the final rendered document, not the lossy fact graph.
Its deliberately narrow table contract is checked again on reader replay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = "## Current tallest buildings"
HEADER = "Rank | Name | Image | Location | Height | Floors | Year"
NEXT_HEADING = "## Under construction"
CONVERT = re.compile(r"convert\|(\d+(?:\.\d+)?)\|m\|ft\|abbr=on\Z")
PLAIN = re.compile(r"(\d+(?:\.\d+)?) m \(\d+(?:,\d+)? ft\)\Z")
RANK = re.compile(r"(?:center\|)?\d+\Z")
NAME = re.compile(r"[^|{}\[\]<>]{2,80}\Z")


@dataclass(frozen=True)
class NumericRow:
    name: str
    meters: float
    name_start: int
    name_end: int
    value_start: int
    value_end: int
    row_start: int
    row_end: int
    plain_name: bool


@dataclass(frozen=True)
class NumericTable:
    header_start: int
    header_end: int
    rows: tuple[NumericRow, ...]
    section_end: int


def parse_table(
    text: str,
    *,
    heading: str = HEADING,
    header: str = HEADER,
    next_heading: str = NEXT_HEADING,
) -> NumericTable:
    """Reject an incomplete or ambiguous table instead of dropping rows."""
    if text.count(heading) != 1:
        raise ValueError("current-buildings section is absent or ambiguous")
    section_start = text.index(heading)
    section_end = text.find(next_heading, section_start)
    if section_end < 0:
        raise ValueError("current-buildings table has no closing heading")
    section = text[section_start:section_end]
    header_marker = header + "\n"
    if section.count(header_marker) != 1:
        raise ValueError("height table header is absent or ambiguous")
    header_start = section_start + section.index(header_marker)
    header_end = header_start + len(header)
    body_start = header_start + len(header_marker)
    rows = []
    offset = body_start
    for raw in text[body_start:section_end].splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        if not line:
            offset += len(raw)
            continue
        cells = line.split(" | ")
        if len(cells) < 5:
            raise ValueError(f"malformed height row at {offset}")
        # The last four cells are Location, Height, Floors, Year. Thus Height
        # is the third cell from the end. Wiki rank
        # and image cells may be absent, and citations can add prefix cells.
        height_index = len(cells) - 3
        height = cells[height_index]
        match = CONVERT.fullmatch(height) or PLAIN.fullmatch(height)
        if match is None:
            raise ValueError(f"unnormalized height row at {offset}")
        meters = float(match.group(1))
        if not 0 < meters < 1000 or not re.fullmatch(r"[12]\d{3}", cells[-1]):
            raise ValueError(f"invalid height or year at {offset}")
        name_index = 1 if RANK.fullmatch(cells[0]) else 0
        name = cells[name_index]
        name_start = offset + sum(len(cell) + 3 for cell in cells[:name_index])
        value_start = offset + sum(len(cell) + 3 for cell in cells[:height_index])
        rows.append(
            NumericRow(
                name,
                meters,
                name_start,
                name_start + len(name),
                value_start,
                value_start + len(height),
                offset,
                offset + len(line),
                NAME.fullmatch(name) is not None and "Cite " not in name,
            )
        )
        offset += len(raw)
    if len(rows) < 8 or len({row.name for row in rows}) != len(rows):
        raise ValueError("too few or duplicate table rows")
    if any(text[row.value_start : row.value_end] == "" for row in rows):
        raise ValueError("empty height span")
    return NumericTable(header_start, header_end, tuple(rows), section_end)


def interval_answer(table: NumericTable, low: int, high: int) -> dict:
    if low >= high:
        raise ValueError("invalid interval")
    selected = [row.name for row in table.rows if low <= row.meters <= high]
    if any(not row.plain_name and low <= row.meters <= high for row in table.rows):
        raise ValueError("selected table row has no clean name")
    entries = sorted(selected)
    if not 2 <= len(entries) <= min(15, len(table.rows) - 2):
        raise ValueError("degenerate interval")
    return {"count": len(entries), "entries": entries}


def intervention(
    table: NumericTable, text: str, low: int, high: int, **table_shape: str
) -> dict:
    """Change one nonmatch to a hit in visible text and replay the parser."""
    baseline = interval_answer(table, low, high)
    misses = [row for row in table.rows if row.plain_name and row.meters < low]
    if not misses:
        raise ValueError("no legal nonmatching row for intervention")
    target = misses[0]
    old = text[target.value_start : target.value_end]
    changed = f"convert|{(low + high) // 2}|m|ft|abbr=on"
    if not CONVERT.fullmatch(old):
        raise ValueError("intervention target is not a metric template")
    modified = text[: target.value_start] + changed + text[target.value_end :]
    replayed = interval_answer(parse_table(modified, **table_shape), low, high)
    if (
        replayed["count"] != baseline["count"] + 1
        or target.name not in replayed["entries"]
    ):
        raise ValueError("numeric intervention did not change answer")
    # A same-format value just below the lower boundary must remain a miss.
    near = f"convert|{low - 1}|m|ft|abbr=on"
    near_text = text[: target.value_start] + near + text[target.value_end :]
    if interval_answer(parse_table(near_text, **table_shape), low, high) != baseline:
        raise ValueError("numeric near miss changed answer")
    return {
        "changed_row": target.name,
        "value_start": target.value_start,
        "value_end": target.value_end,
        "old_value": old,
        "hit_value": changed,
        "hit_answer": replayed,
        "near_miss_value": near,
    }
