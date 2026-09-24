"""Value-blind two-table joins over frozen Wiki reader documents.

This deliberately small compiler accepts only exact table rows, unique first
selectors, a distinct second-document attribute, and visible alternatives.
It reads rendered source tables; it does not infer facts from page titles or
from a hidden graph. The returned context contains no audit index.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from longworld.synthesis import wiki_adapter
from longworld.synthesis.shared_semantic_world import SemanticWorld

_CONVERT = re.compile(r"(?i:convert)\|(-?\d+(?:\.\d+)?)\|([A-Za-z]{1,8})\|[^\n]*\Z")
_CENTER_YEAR = re.compile(r"center\|(?:1=)?([12]\d{3})\Z", re.IGNORECASE)
_UNSAFE = re.compile(r"[\[\]{}|?]|https?://")
_BAD_COLUMN = re.compile(
    r"photo|image|notes?|remarks?|refs?|references?|url|coordinates?|diagram",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Cell:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Row:
    name: Cell
    columns: tuple[tuple[str, Cell], ...]

    def cell(self, column: str) -> Cell | None:
        matches = [cell for header, cell in self.columns if header == column]
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class JoinTask:
    task_id: str
    question: str
    answer: str
    first_title: str
    second_title: str
    name_column: str
    selector_column: str
    selector: str
    target_column: str
    first_span: tuple[int, int]
    bound_name_span: tuple[int, int]
    target_span: tuple[int, int]
    first_text_span: tuple[int, int]
    second_text_span: tuple[int, int]
    context: str
    alternatives: int


def _value(raw: str) -> str | None:
    raw = raw.strip()
    match = _CONVERT.fullmatch(raw)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    match = _CENTER_YEAR.fullmatch(raw)
    if match:
        return match.group(1)
    if not 2 <= len(raw) <= 100 or _UNSAFE.search(raw):
        return None
    return raw


def _cell(line: wiki_adapter.StructuredLine, index: int) -> Cell:
    parts = line.text.split(" | ")
    start = line.start + sum(len(part) + 3 for part in parts[:index])
    start += len(parts[index]) - len(parts[index].lstrip())
    raw = line.cells[index]
    return Cell(raw, start, start + len(raw))


def _rows(text: str) -> tuple[Row, ...]:
    header: tuple[str, ...] | None = None
    rows = []
    for line in wiki_adapter.structured_lines(text):
        if line.kind == "table_header":
            header = line.cells
            continue
        if line.kind != "table_row":
            header = None
            continue
        if header is None or len(header) != len(line.cells):
            continue
        name_index = wiki_adapter._name_column_index(header)
        if (
            name_index is None
            or header.count(header[name_index]) != 1
            or _value(line.cells[name_index]) is None
        ):
            continue
        rows.append(
            Row(
                name=_cell(line, name_index),
                columns=tuple(
                    (column, _cell(line, index)) for index, column in enumerate(header)
                ),
            )
        )
    return tuple(rows)


def _context(
    first_title: str, first_text: str, second_title: str, second_text: str
) -> tuple[str, tuple[int, int], tuple[int, int]]:
    prefix1 = f"=== DOCUMENT: {first_title} ===\n"
    between = f"\n\n=== DOCUMENT: {second_title} ===\n"
    first_start = len(prefix1)
    first_end = first_start + len(first_text)
    second_start = first_end + len(between)
    second_end = second_start + len(second_text)
    return (
        prefix1 + first_text + between + second_text,
        (first_start, first_end),
        (second_start, second_end),
    )


def _visible_rows(
    task: JoinTask, context: str
) -> tuple[tuple[Row, ...], tuple[Row, ...]]:
    if len(context) != len(task.context):
        raise ValueError("reader context length drift")
    first_start, first_end = task.first_text_span
    second_start, second_end = task.second_text_span
    if (
        context[:first_start] != task.context[:first_start]
        or context[first_end:second_start] != task.context[first_end:second_start]
        or context[second_end:] != task.context[second_end:]
    ):
        raise ValueError("reader document boundary drift")
    return _rows(context[first_start:first_end]), _rows(
        context[second_start:second_end]
    )


def _replay_cells(task: JoinTask, context: str) -> tuple[Cell, Cell, Cell]:
    first_rows, second_rows = _visible_rows(task, context)
    matching = [
        row
        for row in first_rows
        if row.cell(task.name_column) == row.name
        and (cell := row.cell(task.selector_column)) is not None
        and _value(cell.value) == task.selector
    ]
    if len(matching) != 1:
        raise ValueError("first reader table does not bind one row")
    bound = matching[0]
    target = [
        row.cell(task.target_column)
        for row in second_rows
        if row.name.value == bound.name.value
        and row.cell(task.target_column) is not None
    ]
    if len(target) != 1 or _value(target[0].value) is None:
        raise ValueError("second reader table lacks one target cell")
    assert bound.cell(task.selector_column) is not None
    return bound.cell(task.selector_column), bound.name, target[0]


def reader_replay(task: JoinTask, context: str) -> str:
    """Resolve the answer from visible table text and question parameters only."""
    value = _value(_replay_cells(task, context)[2].value)
    assert value is not None
    return value


def reader_interventions(task: JoinTask) -> tuple[dict[str, object], ...]:
    """Independently mask the binding selector and target cell in final bytes."""
    outcomes = []
    for label, (start, end) in (
        ("selector", task.first_span),
        ("target", task.target_span),
    ):
        masked = task.context[:start] + "?" * (end - start) + task.context[end:]
        try:
            answer = reader_replay(task, masked)
        except ValueError:
            answer = None
        if answer == task.answer:
            raise ValueError(f"masking {label} did not change reader answer")
        outcomes.append(
            {
                "cell": label,
                "start": start,
                "end": end,
                "masked_sha256": hashlib.sha256(masked.encode()).hexdigest(),
            }
        )
    return tuple(outcomes)


def _source_contains(value: str, source: str) -> bool:
    """Reject easy answers repeated anywhere in the first visible document."""
    number = re.fullmatch(r"(-?\d+(?:\.\d+)?) [A-Za-z]{1,8}", value)
    if number:
        return (
            re.search(rf"(?<![\d.]){re.escape(number.group(1))}(?![\d.])", source)
            is not None
        )
    folded_value = _fold(value)
    return bool(folded_value) and f" {folded_value} " in f" {_fold(source)} "


def _fold(value: str) -> str:
    """Compare answer surfaces across Unicode, case, and punctuation variants."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(char if char.isalnum() else " " for char in normalized).split()
    )


def _row_partly_supports(answer: str, row: Row) -> bool:
    """Reject a target that merely elaborates a visible first-row field."""
    folded_answer = f" {_fold(answer)} "
    for column, cell in row.columns:
        if cell == row.name or _BAD_COLUMN.search(column):
            continue
        value = _value(cell.value)
        if value is None:
            continue
        folded_value = _fold(value)
        if (
            len(folded_value) >= 5
            and any(char.isalpha() for char in folded_value)
            and f" {folded_value} " in folded_answer
        ):
            return True
    return False


def build_join_tasks(
    world: SemanticWorld, *, max_tasks: int = 32
) -> tuple[JoinTask, ...]:
    """Compile and replay conservative joins between different source pages."""
    if max_tasks < 1:
        return ()
    documents = [doc for doc in world.documents if doc.text]
    rows_by_doc = {doc.doc_id: _rows(doc.text) for doc in documents}
    tasks = []
    seen = set()
    for first in documents:
        first_rows = rows_by_doc[first.doc_id]
        for second in documents:
            if first.doc_id == second.doc_id:
                continue
            second_rows = rows_by_doc[second.doc_id]
            second_by_name: dict[str, list[Row]] = {}
            for row in second_rows:
                second_by_name.setdefault(row.name.value, []).append(row)
            context, first_span, second_span = _context(
                first.title, first.text, second.title, second.text
            )
            for first_row in first_rows:
                bound_name = first_row.name.value
                target_rows = second_by_name.get(bound_name, [])
                if len(target_rows) != 1:
                    continue
                target_row = target_rows[0]
                name_column = next(
                    (
                        header
                        for header, cell in first_row.columns
                        if cell == first_row.name
                    ),
                    None,
                )
                if name_column is None:
                    continue
                for selector_column, selector_cell in first_row.columns:
                    selector = _value(selector_cell.value)
                    if (
                        selector_column == name_column
                        or _BAD_COLUMN.search(selector_column)
                        or _UNSAFE.search(selector_column)
                        or selector is None
                        or selector.casefold() in bound_name.casefold()
                    ):
                        continue
                    selector_matches = [
                        row
                        for row in first_rows
                        if row.cell(name_column) == row.name
                        and (cell := row.cell(selector_column)) is not None
                        and _value(cell.value) == selector
                    ]
                    if len(selector_matches) != 1:
                        continue
                    for target_column, target_cell in target_row.columns:
                        answer = _value(target_cell.value)
                        if (
                            target_column == name_column
                            or target_column == selector_column
                            or _BAD_COLUMN.search(target_column)
                            or _UNSAFE.search(target_column)
                            or answer is None
                            or _fold(selector) == _fold(answer)
                            or _row_partly_supports(answer, first_row)
                            or _source_contains(answer, first.text)
                        ):
                            continue
                        target_matches = [
                            row
                            for row in second_rows
                            if row.name.value == bound_name
                            and row.cell(target_column) is not None
                        ]
                        if len(target_matches) != 1:
                            continue
                        alternatives = sum(
                            row.name.value != bound_name
                            and (cell := row.cell(target_column)) is not None
                            and _value(cell.value) is not None
                            for row in second_rows
                        )
                        if alternatives < 2:
                            continue
                        key = (
                            first.doc_id,
                            second.doc_id,
                            bound_name,
                            target_column,
                        )
                        if key in seen:
                            continue
                        digest = hashlib.sha256("|".join(key).encode()).hexdigest()[:16]
                        task = JoinTask(
                            task_id=f"wiki-row-join-{digest}",
                            question=(
                                f"In {first.title}, which {name_column!r} entry has {selector!r} in its "
                                f"{selector_column!r} column? For that entry, what does the "
                                f"{target_column!r} column of {second.title} report?"
                            ),
                            answer=answer,
                            first_title=first.title,
                            second_title=second.title,
                            name_column=name_column,
                            selector_column=selector_column,
                            selector=selector,
                            target_column=target_column,
                            first_span=(
                                first_span[0] + selector_cell.start,
                                first_span[0] + selector_cell.end,
                            ),
                            bound_name_span=(
                                first_span[0] + first_row.name.start,
                                first_span[0] + first_row.name.end,
                            ),
                            target_span=(
                                second_span[0] + target_cell.start,
                                second_span[0] + target_cell.end,
                            ),
                            first_text_span=first_span,
                            second_text_span=second_span,
                            context=context,
                            alternatives=alternatives,
                        )
                        if _source_contains(answer, task.question):
                            continue
                        # Reject a repeated answer surface anywhere else in
                        # the final two-document reader context. This is a
                        # conservative exact/number-string duplicate check,
                        # not a proof against paraphrases or prior knowledge.
                        masked_target = (
                            context[: task.target_span[0]]
                            + "?" * (task.target_span[1] - task.target_span[0])
                            + context[task.target_span[1] :]
                        )
                        if _source_contains(answer, masked_target):
                            continue
                        try:
                            if reader_replay(task, context) != answer:
                                continue
                            reader_interventions(task)
                        except ValueError:
                            continue
                        seen.add(key)
                        tasks.append(task)
                        if len(tasks) >= max_tasks:
                            return tuple(tasks)
    return tuple(tasks)
