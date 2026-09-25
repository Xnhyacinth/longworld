"""Closed year-table projection with optional columns after the year cell.

Every row in a visible contiguous table must have an unambiguous name and
plain year. Only trailing, unqueried cells may be absent. We never infer a
missing row, a date from prose, or a table end from a malformed record.
"""

from __future__ import annotations

from collections import Counter

from longworld.synthesis.p92_generic_table_scan import (
    PLAIN_NAME,
    SUBJECT_COLUMNS,
    YEAR,
    YEAR_COLUMNS,
    Row,
    Table,
    answer,
)


def parse_tables(text: str) -> tuple[tuple[Table, ...], tuple[str, ...]]:
    lines = text.splitlines(keepends=True)
    starts = []
    cursor = 0
    for raw in lines:
        starts.append(cursor)
        cursor += len(raw)
    found = []
    rejected = []
    visible_keys: Counter[tuple[str, str]] = Counter()
    heading = ""
    for index, raw in enumerate(lines):
        header = raw.rstrip("\r\n")
        if header.startswith("## "):
            heading = header[3:]
            continue
        columns = header.split(" | ")
        if not heading or len(columns) < 2 or columns[0] not in SUBJECT_COLUMNS:
            continue
        numeric = [
            (i, column) for i, column in enumerate(columns) if column in YEAR_COLUMNS
        ]
        if len(numeric) != 1:
            continue
        visible_keys[heading, header] += 1
        year_index, year_column = numeric[0]
        rows = []
        widths = []
        end_index = index + 1
        failure = ""
        while end_index < len(lines):
            body = lines[end_index].rstrip("\r\n")
            if not body or body.startswith("#"):
                break
            parts = body.split(" | ")
            if not year_index < len(parts) <= len(columns):
                failure = "required_cell_or_row_width"
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
            widths.append(len(parts))
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
        if all(width == len(columns) for width in widths):
            # The existing strict recipe already owns uniform-width tables.
            continue
        found.append(
            Table(
                heading,
                header,
                year_column,
                starts[index],
                starts[index] + len(header),
                starts[end_index] if end_index < len(lines) else len(text),
                tuple(rows),
            )
        )
    unique = []
    for table in found:
        if visible_keys[table.heading, table.header] != 1:
            rejected.append(
                f"{table.heading}/{table.year_column}:ambiguous_visible_table_key"
            )
        else:
            unique.append(table)
    return tuple(unique), tuple(rejected)


def boundary_replay(text: str, table: Table, low: int, high: int) -> dict:
    """Change a visible nonmatch and reparse both changed final source texts."""
    baseline = answer(table, low, high)
    years = [row.year for row in table.rows]
    ascending = years == sorted(years)
    descending = years == sorted(years, reverse=True)

    def preserves_order(position: int, value: int) -> bool:
        if ascending and (
            (position > 0 and years[position - 1] > value)
            or (position + 1 < len(years) and value > years[position + 1])
        ):
            return False
        return not descending or not (
            (position > 0 and years[position - 1] < value)
            or (position + 1 < len(years) and value < years[position + 1])
        )

    if low < 1001:
        raise ValueError("no legal below-range year")
    misses = [
        (position, row)
        for position, row in enumerate(table.rows)
        if row.year < low
        and preserves_order(position, low)
        and preserves_order(position, low - 1)
    ]
    if not misses:
        raise ValueError("no legal below-range row")
    position, target = misses[-1] if ascending else misses[0]

    def replay(value: str) -> dict:
        changed = text[: target.value_start] + value + text[target.value_end :]
        tables, _ = parse_tables(changed)
        matching = [
            candidate
            for candidate in tables
            if candidate.header_start == table.header_start
            and candidate.heading == table.heading
            and candidate.header == table.header
        ]
        if len(matching) != 1 or len(matching[0].rows) != len(table.rows):
            raise ValueError("reader table drift under intervention")
        return answer(matching[0], low, high)

    hit_value, near_value = str(low), str(low - 1)
    hit = replay(hit_value)
    if hit["count"] != baseline["count"] + 1 or target.name not in hit["entries"]:
        raise ValueError("hit intervention failed")
    if replay(near_value) != baseline:
        raise ValueError("near-miss intervention failed")
    return {
        "changed_row": target.name,
        "value_start": target.value_start,
        "value_end": target.value_end,
        "old_value": text[target.value_start : target.value_end],
        "hit_value": hit_value,
        "hit_answer": hit,
        "near_miss_value": near_value,
        "original_year_order": (
            "ascending" if ascending else "descending" if descending else "unsorted"
        ),
        "changed_row_position": position,
    }
