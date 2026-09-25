"""Conservative closed-table scans inferred from frozen reader text.

The parser accepts only complete, unambiguous year tables. It neither repairs
missing cells nor silently drops a row, since either changes a complete-set
answer. A table's row universe ends at the next blank line or heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SUBJECT_COLUMNS = frozenset(
    {
        "Name",
        "University",
        "Stadium",
        "Observatory Name",
        "Building",
        "Park",
        "Site",
        "Hospital",
        "Bridge",
    }
)
YEAR_COLUMNS = frozenset({"Year", "Established", "Opened", "Built", "Completion"})
YEAR = re.compile(r"[12]\d{3}\Z")
PLAIN_NAME = re.compile(r"[^|{}\[\]<>]{2,100}\Z")


@dataclass(frozen=True)
class Row:
    name: str
    year: int
    row_start: int
    row_end: int
    value_start: int
    value_end: int


@dataclass(frozen=True)
class Table:
    heading: str
    header: str
    year_column: str
    header_start: int
    header_end: int
    table_end: int
    rows: tuple[Row, ...]


def parse_tables(text: str) -> tuple[tuple[Table, ...], tuple[str, ...]]:
    """Discover tables from headings and columns; reject any incomplete row."""
    lines = text.splitlines(keepends=True)
    starts = []
    cursor = 0
    for raw in lines:
        starts.append(cursor)
        cursor += len(raw)
    found: list[Table] = []
    rejected: list[str] = []
    heading = ""
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if line.startswith("## "):
            heading = line.removeprefix("## ")
            continue
        cells = line.split(" | ")
        if not heading or len(cells) < 2 or cells[0] not in SUBJECT_COLUMNS:
            continue
        numeric = [(i, cell) for i, cell in enumerate(cells) if cell in YEAR_COLUMNS]
        if len(numeric) != 1:
            continue
        year_index, year_column = numeric[0]
        rows: list[Row] = []
        end_index = index + 1
        failure = ""
        while end_index < len(lines):
            body = lines[end_index].rstrip("\r\n")
            if not body or body.startswith("#"):
                break
            parts = body.split(" | ")
            if len(parts) != len(cells):
                failure = "row_width_mismatch"
                break
            name, value = parts[0].strip(), parts[year_index].strip()
            if not PLAIN_NAME.fullmatch(name) or "Cite " in name:
                failure = "ambiguous_subject"
                break
            if not YEAR.fullmatch(value):
                failure = "non_plain_year"
                break
            value_start = (
                starts[end_index]
                + sum(len(part) + 3 for part in parts[:year_index])
                + len(parts[year_index])
                - len(parts[year_index].lstrip())
            )
            rows.append(
                Row(
                    name,
                    int(value),
                    starts[end_index],
                    starts[end_index] + len(body),
                    value_start,
                    value_start + len(value),
                )
            )
            end_index += 1
        if failure:
            rejected.append(f"{heading}/{year_column}:{failure}")
            continue
        next_nonblank = end_index
        while next_nonblank < len(lines) and not lines[next_nonblank].strip():
            next_nonblank += 1
        if next_nonblank < len(lines) and not lines[next_nonblank].startswith("## "):
            rejected.append(f"{heading}/{year_column}:ambiguous_table_end")
            continue
        if len(rows) < 8:
            rejected.append(f"{heading}/{year_column}:too_few_rows")
            continue
        if len({row.name for row in rows}) != len(rows):
            rejected.append(f"{heading}/{year_column}:duplicate_subject")
            continue
        found.append(
            Table(
                heading,
                line,
                year_column,
                starts[index],
                starts[index] + len(line),
                starts[end_index] if end_index < len(lines) else len(text),
                tuple(rows),
            )
        )
    return tuple(found), tuple(rejected)


def answer(table: Table, low: int, high: int) -> dict:
    if low >= high:
        raise ValueError("invalid interval")
    entries = sorted(row.name for row in table.rows if low <= row.year <= high)
    if not 2 <= len(entries) <= min(15, len(table.rows) - 2):
        raise ValueError("degenerate interval")
    return {"count": len(entries), "entries": entries}


def intervals(table: Table, limit: int) -> tuple[tuple[int, int], ...]:
    if limit < 1:
        raise ValueError("task limit must be positive")
    years = sorted({row.year for row in table.rows})
    options = []
    for low in years:
        for high in years:
            if low >= high:
                continue
            try:
                result = answer(table, low, high)
            except ValueError:
                continue
            if any(row.year < low for row in table.rows):
                options.append((result["count"], low, high))
    # Distinct answer cardinalities keep the task bank from merely paraphrasing
    # one interval. Favor wide intervals within each cardinality.
    by_count: dict[int, tuple[int, int]] = {}
    for count, low, high in sorted(
        options, key=lambda x: (x[0], -(x[2] - x[1]), x[1], x[2])
    ):
        by_count.setdefault(count, (low, high))
    counts = sorted(by_count)
    if len(counts) > limit:
        picks = (
            {round(i * (len(counts) - 1) / (limit - 1)) for i in range(limit)}
            if limit > 1
            else {len(counts) // 2}
        )
        counts = [counts[i] for i in sorted(picks)]
    return tuple(by_count[count] for count in counts)


def boundary_replay(text: str, table: Table, low: int, high: int) -> dict:
    """Promote a visible nonmatch and independently reparse changed bytes."""
    baseline = answer(table, low, high)
    years = [row.year for row in table.rows]
    if years == sorted(years) or years == sorted(years, reverse=True):
        raise ValueError(
            "year-sorted table has no order-preserving boundary intervention"
        )
    misses = [row for row in table.rows if row.year < low]
    if not misses or low < 1001:
        raise ValueError("no legal below-range row")
    target = misses[0]
    replacement = str(low)
    near = str(low - 1)

    def replay(value: str) -> dict:
        changed = text[: target.value_start] + value + text[target.value_end :]
        tables, _ = parse_tables(changed)
        matching = [
            item
            for item in tables
            if item.heading == table.heading and item.header == table.header
        ]
        if len(matching) != 1 or len(matching[0].rows) != len(table.rows):
            raise ValueError("reader table drift under intervention")
        return answer(matching[0], low, high)

    hit = replay(replacement)
    if hit["count"] != baseline["count"] + 1 or target.name not in hit["entries"]:
        raise ValueError("hit intervention failed")
    if replay(near) != baseline:
        raise ValueError("near-miss intervention failed")
    return {
        "changed_row": target.name,
        "value_start": target.value_start,
        "value_end": target.value_end,
        "old_value": text[target.value_start : target.value_end],
        "hit_value": replacement,
        "hit_answer": hit,
        "near_miss_value": near,
    }
