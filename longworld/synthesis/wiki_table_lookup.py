"""Source-backed table-cell lookup with a value-blind final-reader replay."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from longworld.synthesis import reader_view, wiki_adapter, wiki_evidence
from longworld.synthesis.dependency_ops import ProofItem
from longworld.synthesis.shared_semantic_world import ScopeEntry, SemanticWorld
from longworld.synthesis.world_task_bank import TaskSpec

YEAR = re.compile(r"[12][0-9]{3}\Z")
SAFE = re.compile(r"[\[\]{}|?]|https?://")
SELECTOR_EXCLUDE = re.compile(
    r"\b(?:photo|image|notes?|remarks?|summary|description|references?|refs?|"
    r"coordinates?|latitude|longitude|url)\b",
    re.IGNORECASE,
)


def _cell_offset(line: wiki_adapter.StructuredLine, index: int) -> tuple[int, int]:
    parts = line.text.split(" | ")
    start = line.start + sum(len(part) + 3 for part in parts[:index])
    start += len(parts[index]) - len(parts[index].lstrip())
    return start, start + len(line.cells[index])


def _inverse_cells(
    text: str, name_column: str, selector_column: str, selector: str
) -> list[tuple[str, int, int]]:
    """Read names in rows identified by one other, explicitly headed cell."""
    header: tuple[str, ...] | None = None
    found: list[tuple[str, int, int]] = []
    for line in wiki_adapter.structured_lines(text):
        if line.kind == "table_header":
            header = line.cells
            continue
        if line.kind != "table_row":
            header = None
            continue
        if (
            header is None
            or len(header) != len(line.cells)
            or header.count(name_column) != 1
            or header.count(selector_column) != 1
            or name_column == selector_column
        ):
            continue
        name_index = header.index(name_column)
        selector_index = header.index(selector_column)
        if (
            wiki_adapter._name_column_index(header) != name_index
            or line.cells[selector_index] != selector
        ):
            continue
        name = line.cells[name_index]
        if (
            not name
            or len(name) > 200
            or SAFE.search(name)
            or re.search(r"[A-Za-z]{3}", name) is None
        ):
            continue
        start, end = _cell_offset(line, name_index)
        found.append((name, start, end))
    return found


def _cells(
    text: str, surface: str, column: str, relation: str
) -> list[tuple[Any, int, int]]:
    header: tuple[str, ...] | None = None
    found: list[tuple[Any, int, int]] = []
    for line in wiki_adapter.structured_lines(text):
        if line.kind == "table_header":
            header = line.cells
            continue
        if line.kind != "table_row":
            header = None
            continue
        subject_index = wiki_adapter._name_column_index(header or ())
        if subject_index is None:
            subject_index = 0
        if (
            header is None
            or len(line.cells) != len(header)
            or line.cells[subject_index] != surface
            or header.count(column) != 1
        ):
            continue
        index = header.index(column)
        cell = line.cells[index]
        if SAFE.search(cell) or len(cell) > 200:
            continue
        semantics = wiki_adapter._cell_semantics(column, cell)
        if semantics is None or semantics[0] != relation:
            continue
        value: Any = semantics[1]
        if relation in {"established in", "opened in"}:
            if not YEAR.fullmatch(value):
                continue
            value = int(value)
        parts = line.text.split(" | ")
        start = line.start + sum(len(part) + 3 for part in parts[:index])
        start += len(parts[index]) - len(parts[index].lstrip())
        found.append((value, start, start + len(cell)))
    return found


def _fact_matches_cell(world: SemanticWorld, fact: Any, value: Any) -> bool:
    if fact.value_type == "entity":
        entity = world.objects[fact.value]
        return value in (entity.label, *entity.aliases)
    return value == fact.value


def _inverse_selector(world: SemanticWorld, fact: Any) -> tuple[str, str, str] | None:
    """Choose a unique visible row key for a list-membership fact."""
    if (
        fact.relation != "includes facility"
        or len(fact.supporting_spans) != 1
        or not isinstance(fact.qualifiers.get("table_column"), str)
    ):
        return None
    span = fact.supporting_spans[0]
    doc = world._docs[span.doc_id]
    if world.objects[fact.subject].label != doc.title:
        return None
    header: tuple[str, ...] | None = None
    choices: list[tuple[int, str, str, str]] = []
    for line in wiki_adapter.structured_lines(doc.text):
        if line.kind == "table_header":
            header = line.cells
            continue
        if line.kind != "table_row":
            header = None
            continue
        if header is None or len(header) != len(line.cells):
            continue
        name_column = fact.qualifiers["table_column"]
        if header.count(name_column) != 1:
            continue
        name_index = header.index(name_column)
        if wiki_adapter._name_column_index(header) != name_index:
            continue
        start, end = _cell_offset(line, name_index)
        name = line.cells[name_index]
        if (
            (span.start, span.end) != (start, end)
            or not _fact_matches_cell(world, fact, name)
            or SAFE.search(name)
        ):
            continue
        for index, (column, selector) in enumerate(zip(header, line.cells)):
            if (
                index == name_index
                or header.count(column) != 1
                or SELECTOR_EXCLUDE.search(column)
                or SAFE.search(column)
                or not 3 <= len(selector) <= 80
                or SAFE.search(selector)
                or selector.casefold() in name.casefold()
            ):
                continue
            cells = _inverse_cells(doc.text, name_column, column, selector)
            if len(cells) == 1 and cells[0] == (name, start, end):
                choices.append((len(selector), name_column, column, selector))
    if not choices:
        return None
    _, name_column, column, selector = min(choices)
    return name_column, column, selector


def execute_lookup(world: SemanticWorld, program: dict[str, Any]) -> Any:
    """Native oracle checks the fact identity against the visible table cell."""
    if program.get("op") != "table_cell_lookup":
        raise ValueError("wrong table lookup operator")
    fact = world.facts_by_id[program["fact_id"]]
    if "selector_column" in program:
        if (
            fact.relation != "includes facility"
            or fact.qualifiers.get("table_column") != program["column"]
            or len(fact.supporting_spans) != 1
            or fact.supporting_spans[0].doc_id != program["doc_id"]
            or _inverse_selector(world, fact)
            != (
                program["column"],
                program["selector_column"],
                program["selector"],
            )
        ):
            raise ValueError("inverse table lookup fact/source binding drift")
        cells = _inverse_cells(
            world._docs[program["doc_id"]].text,
            program["column"],
            program["selector_column"],
            program["selector"],
        )
        if len(cells) != 1 or not _fact_matches_cell(world, fact, cells[0][0]):
            raise ValueError("inverse table source fact disagrees with visible cell")
        return cells[0][0]
    if (
        fact.relation != program["relation"]
        or fact.qualifiers.get("table_column") != program["column"]
        or len(fact.supporting_spans) != 1
        or fact.supporting_spans[0].doc_id != program["doc_id"]
        or not wiki_evidence.check_locate_fact(world, fact).supported
    ):
        raise ValueError("table lookup fact/source binding drift")
    cells = _cells(
        world._docs[program["doc_id"]].text,
        program["surface"],
        program["column"],
        program["relation"],
    )
    if len(cells) != 1 or not _fact_matches_cell(world, fact, cells[0][0]):
        raise ValueError("table lookup source fact disagrees with visible cell")
    return cells[0][0]


def build_lookup_tasks(
    world: SemanticWorld, *, max_tasks: int = 32
) -> tuple[TaskSpec, ...]:
    """Select one unambiguous named cell per checked source fact and column."""
    if max_tasks < 1:
        return ()
    buckets: dict[tuple[str, str, str], list[TaskSpec]] = defaultdict(list)
    for fact in sorted(world.facts, key=lambda item: item.fact_id):
        column = fact.qualifiers.get("table_column")
        inverse = fact.relation == "includes facility"
        if (
            not isinstance(column, str)
            or (
                not inverse
                and fact.relation
                not in {
                    "established in",
                    "opened in",
                    "located in",
                    "located in region",
                }
            )
            or len(fact.supporting_spans) != 1
        ):
            continue
        span = fact.supporting_spans[0]
        doc = world._docs[span.doc_id]
        if inverse:
            selector = _inverse_selector(world, fact)
            if selector is None:
                continue
            _, selector_column, selector_value = selector
            cells = _inverse_cells(doc.text, column, selector_column, selector_value)
            if len(cells) != 1 or not _fact_matches_cell(world, fact, cells[0][0]):
                continue
            surface = cells[0][0]
        else:
            check = wiki_evidence.check_locate_fact(world, fact)
            if not check.supported or not check.row:
                continue
            header = check.header.split(" | ") if check.header else []
            row_cells = check.row.split(" | ")
            subject_index = wiki_adapter._name_column_index(tuple(header))
            if subject_index is None:
                subject_index = 0
            if subject_index >= len(row_cells):
                continue
            surface = row_cells[subject_index].strip()
            if not surface or SAFE.search(surface):
                continue
            cells = _cells(doc.text, surface, column, fact.relation)
            if len(cells) != 1 or not _fact_matches_cell(world, fact, cells[0][0]):
                continue
        digest = hashlib.sha256(
            f"{doc.doc_id}|{fact.fact_id}|{column}".encode()
        ).hexdigest()[:16]
        task_id = f"wiki-table-lookup-{digest}"
        if inverse:
            question = (
                f"In {doc.title}, which {column!r} entry has "
                f"{selector_value!r} in the {selector_column!r} column?"
            )
        else:
            question = (
                f"In the {column!r} column of {doc.title}, what value is listed "
                f"for {surface}?"
            )
        program = {
            "op": "table_cell_lookup",
            "fact_id": fact.fact_id,
            "relation": fact.relation,
            "doc_id": doc.doc_id,
            "surface": surface,
            "column": column,
        }
        if inverse:
            program["selector_column"] = selector_column
            program["selector"] = selector_value
        answer = execute_lookup(world, program)
        proof = (
            ProofItem(
                step=0,
                op="table_cell_lookup",
                out="value",
                kind="fact",
                ref_id=fact.fact_id,
                subject=fact.subject,
                relation=fact.relation,
                value=fact.value,
                spans=fact.supporting_spans,
                span_texts=(doc.text[span.start : span.end],),
            ),
        )
        task = TaskSpec(
            task_id=task_id,
            family="table_lookup",
            template="table_lookup",
            question=question,
            program=program,
            scope=ScopeEntry(
                task_id=task_id,
                object_families=("untyped",),
                relations=(fact.relation,),
                documents=tuple(item.doc_id for item in world.documents if item.text),
            ),
            answer=answer,
            answer_rendered=answer,
            proof=proof,
            consumed_fact_ids=(fact.fact_id,),
            metrics={"consumed_facts": 1, "source_documents": 1},
            nondegenerate={"unique_subject_column_cell": True},
            structure_signals={
                "lookup_doc_id": doc.doc_id,
                "lookup_surface": surface,
                "lookup_column": column,
                **(
                    {
                        "lookup_selector_column": selector_column,
                        "lookup_selector": selector_value,
                    }
                    if inverse
                    else {}
                ),
            },
            spread=(doc.doc_id, fact.relation, column),
        )
        buckets[(doc.doc_id, fact.relation, column)].append(task)
    selected: list[TaskSpec] = []
    keys = sorted(buckets)
    while len(selected) < max_tasks and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < max_tasks:
                selected.append(buckets[key].pop(0))
    return tuple(selected)


def reader_lookup(
    world: SemanticWorld, task: TaskSpec, context: str, *, allow_masked: bool = False
) -> tuple[Any, tuple[int, int]]:
    """Read the named cell from final visible bytes without using fact values."""
    rendered = reader_view.render_documents(world, task.scope.documents)
    if (not allow_masked and rendered.text != context) or (
        allow_masked and len(rendered.text) != len(context)
    ):
        raise ValueError("table lookup final reader context drift")
    signals = task.structure_signals
    layout = next(
        item for item in rendered.layouts if item.doc_id == signals["lookup_doc_id"]
    )
    doc_text = context[layout.text_start : layout.text_end]
    if "lookup_selector_column" in signals:
        cells = _inverse_cells(
            doc_text,
            signals["lookup_column"],
            signals["lookup_selector_column"],
            signals["lookup_selector"],
        )
    else:
        cells = _cells(
            doc_text,
            signals["lookup_surface"],
            signals["lookup_column"],
            task.program["relation"],
        )
    if len(cells) != 1:
        raise ValueError("reader table lookup lacks one unambiguous cell")
    value, start, end = cells[0]
    return value, (layout.text_start + start, layout.text_start + end)


def reader_cell_intervention(
    world: SemanticWorld, task: TaskSpec, context: str
) -> dict[str, Any]:
    value, (start, end) = reader_lookup(world, task, context)
    if value != task.answer_rendered:
        raise ValueError("reader table lookup disagrees with program answer")
    masked = context[:start] + "?" * (end - start) + context[end:]
    fact = world.facts_by_id[task.consumed_fact_ids[0]]
    entity = world.objects[fact.subject]
    surfaces = (task.structure_signals["lookup_surface"], entity.label, *entity.aliases)
    value_pattern = re.compile(
        r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)", re.IGNORECASE
    )
    other_answer_lines = [
        line for line in masked.splitlines() if value_pattern.search(line)
    ]
    if any(
        any(surface.casefold() in line.casefold() for surface in surfaces)
        for line in other_answer_lines
    ):
        raise ValueError("equivalent_subject_answer_elsewhere_in_reader_text")
    try:
        reader_lookup(world, task, masked, allow_masked=True)
    except ValueError as error:
        if "lacks one unambiguous cell" not in str(error):
            raise
    else:
        raise ValueError("table cell deletion left reader parser answer")
    return {
        "status": "scoped_named_table_cell_removed",
        "cell_span": {"start": start, "end": end},
        "masked_context_sha256": hashlib.sha256(masked.encode()).hexdigest(),
        "other_answer_line_count": len(other_answer_lines),
        "certification_scope": "one named table row and column; equivalent prose elsewhere unchecked",
    }
