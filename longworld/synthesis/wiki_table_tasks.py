"""Conservative two-document comparison of supported Wikipedia table years.

The oracle is the year in each named source table, not a claim about the
institution's true historical founding date. Text intervention certifies only
the named subjects' parsed founding-year cells in selected reader documents.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

from longworld.synthesis import reader_view, wiki_adapter, wiki_evidence
from longworld.synthesis.dependency_ops import ProofItem
from longworld.synthesis.shared_semantic_world import Fact, ScopeEntry, SemanticWorld
from longworld.synthesis.world_task_bank import TaskSpec

_SAFE_LABEL = re.compile(r"^[^\[\]{}|<>\n]{3,100}$")
YEAR_COLUMNS = (
    "Established",
    "Establishment",
    "Year of foundation",
    "Year offoundation",
)


def supported_year_facts(world: SemanticWorld) -> tuple[Fact, ...]:
    """Only unique-subject, clean, full-year founding-table facts."""
    candidates = [
        fact
        for fact in world.facts
        if fact.relation == "established in"
        and fact.qualifiers.get("table_column") in YEAR_COLUMNS
        and isinstance(fact.value, int)
        and not isinstance(fact.value, bool)
        and 1000 <= fact.value <= 2099
        and len({span.doc_id for span in fact.supporting_spans}) == 1
        and _SAFE_LABEL.fullmatch(world.label_of(fact.subject))
        and wiki_evidence.check_locate_fact(world, fact).supported
    ]
    counts = Counter(
        (fact.supporting_spans[0].doc_id, fact.subject) for fact in candidates
    )
    values_by_subject: dict[str, set[int]] = defaultdict(set)
    for fact in candidates:
        values_by_subject[fact.subject].add(fact.value)
    return tuple(
        sorted(
            (
                fact
                for fact in candidates
                if counts[(fact.supporting_spans[0].doc_id, fact.subject)] == 1
                and len(values_by_subject[fact.subject]) == 1
            ),
            key=lambda fact: fact.fact_id,
        )
    )


def execute_table_pair(world: SemanticWorld, program: dict[str, Any]) -> dict[str, Any]:
    if program.get("op") != "table_pair_earlier_year":
        raise ValueError("wrong table-pair operator")
    sides = program.get("sides")
    if not isinstance(sides, list) or len(sides) != 2:
        raise ValueError("table-pair requires two sides")
    facts = []
    for side in sides:
        fact = world.facts_by_id[side["fact_id"]]
        if (
            fact.supporting_spans[0].doc_id != side["doc_id"]
            or world.label_of(fact.subject) != side["label"]
            or fact.relation != "established in"
            or not isinstance(fact.value, int)
        ):
            raise ValueError("table-pair source or fact drift")
        facts.append(fact)
    if (
        facts[0].subject == facts[1].subject
        or world.label_of(facts[0].subject) == world.label_of(facts[1].subject)
        or facts[0].value == facts[1].value
    ):
        raise ValueError("table-pair must compare distinct subjects and years")
    return {
        "years": {world.label_of(fact.subject): fact.value for fact in facts},
        "earlier": world.label_of(min(facts, key=lambda fact: fact.value).subject),
    }


def build_table_pair_tasks(
    world: SemanticWorld, *, max_tasks: int = 24
) -> tuple[TaskSpec, ...]:
    """Pair disjoint facts across two source pages, using each fact once."""
    if max_tasks <= 0:
        return ()
    by_doc: dict[str, list[Fact]] = defaultdict(list)
    for fact in supported_year_facts(world):
        by_doc[fact.supporting_spans[0].doc_id].append(fact)
    ranked_docs = sorted(by_doc, key=lambda doc_id: (-len(by_doc[doc_id]), doc_id))
    if len(ranked_docs) < 2:
        return ()
    doc_a, doc_b = ranked_docs[:2]
    if len(by_doc[doc_b]) < 2:
        return ()
    key = lambda fact: hashlib.sha256(fact.fact_id.encode()).hexdigest()
    left = sorted(by_doc[doc_a], key=key)
    right = sorted(by_doc[doc_b], key=key)
    used: set[str] = set()
    tasks: list[TaskSpec] = []
    for fact_a in left:
        fact_b = next(
            (
                item
                for item in right
                if item.fact_id not in used
                and item.subject != fact_a.subject
                and world.label_of(item.subject) != world.label_of(fact_a.subject)
                and item.value != fact_a.value
            ),
            None,
        )
        if fact_b is None:
            continue
        used.add(fact_b.fact_id)
        digest = hashlib.sha256(
            f"{fact_a.fact_id}|{fact_b.fact_id}".encode()
        ).hexdigest()[:16]
        task_id = f"wiki-table-earlier-{digest}"
        label_a, label_b = (
            world.label_of(fact_a.subject),
            world.label_of(fact_b.subject),
        )
        title_a, title_b = world._docs[doc_a].title, world._docs[doc_b].title
        column_a = fact_a.qualifiers["table_column"]
        column_b = fact_b.qualifiers["table_column"]
        question = (
            f"Using the {column_a!r} year for {label_a} in {title_a} and "
            f"the {column_b!r} year for {label_b} in {title_b}, "
            "what year is listed for each, and which named entry has the earlier year? "
            "Answer with both named years and the earlier name."
        )
        program = {
            "op": "table_pair_earlier_year",
            "sides": [
                {"doc_id": doc_a, "fact_id": fact_a.fact_id, "label": label_a},
                {"doc_id": doc_b, "fact_id": fact_b.fact_id, "label": label_b},
            ],
        }
        proof = tuple(
            ProofItem(
                step=index,
                op="table_pair_earlier_year",
                out=f"year_{index}",
                kind="fact",
                ref_id=fact.fact_id,
                subject=fact.subject,
                relation=fact.relation,
                value=fact.value,
                spans=fact.supporting_spans,
                span_texts=tuple(
                    world._docs[span.doc_id].text[span.start : span.end]
                    for span in fact.supporting_spans
                ),
            )
            for index, fact in enumerate((fact_a, fact_b))
        )
        tasks.append(
            TaskSpec(
                task_id=task_id,
                family="source_compare",
                template="source_compare",
                question=question,
                program=program,
                scope=ScopeEntry(
                    task_id=task_id,
                    object_families=("untyped",),
                    relations=("established in",),
                    documents=(doc_a, doc_b),
                ),
                answer=execute_table_pair(world, program),
                answer_rendered=execute_table_pair(world, program),
                proof=proof,
                consumed_fact_ids=(fact_a.fact_id, fact_b.fact_id),
                metrics={"source_documents": 2, "consumed_facts": 2},
                nondegenerate={"distinct_years": True, "distinct_subjects": True},
                structure_signals={
                    "table_columns": (column_a, column_b),
                    "source_documents": 2,
                },
                spread=(doc_a, doc_b, "table_pair_earlier_year"),
            )
        )
        if len(tasks) >= max_tasks:
            break
    return tuple(tasks)


def with_scope_documents(task: TaskSpec, doc_ids: tuple[str, ...]) -> TaskSpec:
    if not set(task.scope.documents).issubset(doc_ids):
        raise ValueError("length view dropped a required source document")
    return replace(task, scope=replace(task.scope, documents=doc_ids))


def _table_year_cells(context: str, surfaces: tuple[str, ...]) -> list[dict[str, Any]]:
    """Replay the adapter's founding-year column parser over reader text."""
    cells: list[dict[str, Any]] = []
    header: list[str] = []
    offset = 0
    for line in context.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        parts = text.split(" | ")
        if any(column in parts for column in YEAR_COLUMNS):
            header = parts
        elif header and len(parts) == len(header) and parts[0].strip() in surfaces:
            column = next(column for column in YEAR_COLUMNS if column in header)
            index = header.index(column)
            cell = parts[index].strip()
            semantic = wiki_adapter._cell_semantics(column, cell)
            if (
                semantic is not None
                and semantic[0] == "established in"
                and cell.isdigit()
            ):
                start = (
                    offset
                    + sum(len(part) + 3 for part in parts[:index])
                    + len(parts[index])
                    - len(parts[index].lstrip())
                )
                cells.append(
                    {"year": int(cell), "start": start, "end": start + len(cell)}
                )
        offset += len(line)
    return cells


def reader_table_replay(
    world: SemanticWorld,
    task: TaskSpec,
    reader_context: str,
    *,
    allow_masked: bool = False,
) -> dict[str, Any]:
    """Read both named source-year cells from final text without consulting values."""
    rendered = reader_view.render_documents(world, task.scope.documents)
    if (not allow_masked and rendered.text != reader_context) or (
        allow_masked and len(rendered.text) != len(reader_context)
    ):
        raise ValueError("reader context drift before table replay")
    layouts = {layout.doc_id: layout for layout in rendered.layouts}
    years: dict[str, int] = {}
    for side in task.program["sides"]:
        fact = world.facts_by_id[side["fact_id"]]
        entity = world.objects[fact.subject]
        layout = layouts[side["doc_id"]]
        doc_text = reader_context[layout.text_start : layout.text_end]
        cells = _table_year_cells(doc_text, (entity.label, *entity.aliases))
        found = {cell["year"] for cell in cells}
        if len(found) != 1:
            raise ValueError("reader table parser lacks a unique source year")
        years[side["label"]] = next(iter(found))
    if len(years) != 2 or len(set(years.values())) != 2:
        raise ValueError("reader table comparison is degenerate")
    return {"years": years, "earlier": min(years, key=years.__getitem__)}


def reader_year_cell_intervention(
    world: SemanticWorld, task: TaskSpec, reader_context: str
) -> dict[str, Any]:
    """Mask every scoped parser-visible year cell for both target subjects.

    This checks table-parser necessity; it does not prove that unrestricted
    natural-language inference cannot recover a year from other prose.
    """
    if task.program.get("op") != "table_pair_earlier_year":
        raise ValueError("unsupported text intervention")
    if reader_table_replay(world, task, reader_context) != task.answer:
        raise ValueError("reader table replay disagrees with formal oracle")
    edits_by_side: list[list[tuple[int, int]]] = []
    for side in task.program["sides"]:
        fact = world.facts_by_id[side["fact_id"]]
        entity = world.objects[fact.subject]
        cells = _table_year_cells(reader_context, (entity.label, *entity.aliases))
        if not cells or any(cell["year"] != fact.value for cell in cells):
            raise ValueError(
                "target year has conflicting or missing reader table cells"
            )
        edits_by_side.append([(cell["start"], cell["end"]) for cell in cells])
    edits = [edit for side_edits in edits_by_side for edit in side_edits]
    if len(edits) != len(set(edits)):
        raise ValueError("overlapping table year evidence")

    def mask(selected: list[tuple[int, int]]) -> str:
        text = reader_context
        for start, end in sorted(selected, reverse=True):
            text = text[:start] + "?" * (end - start) + text[end:]
        return text

    for side_edits in edits_by_side:
        try:
            reader_table_replay(world, task, mask(side_edits), allow_masked=True)
        except ValueError as error:
            if "lacks a unique source year" not in str(error):
                raise
        else:
            raise ValueError("single-side year-cell deletion left full parser answer")
    masked = mask(edits)
    if any(
        _table_year_cells(
            masked,
            (
                world.objects[world.facts_by_id[side["fact_id"]].subject].label,
                *world.objects[world.facts_by_id[side["fact_id"]].subject].aliases,
            ),
        )
        for side in task.program["sides"]
    ):
        raise ValueError("year-cell deletion left a parser-visible target")
    return {
        "status": "scoped_table_parser_year_cells_removed",
        "deleted_cell_count": len(edits),
        "single_side_mask_replay": "both sides independently remove the full parser answer",
        "deleted_cell_offsets": [
            {"start": start, "end": end} for start, end in sorted(edits)
        ],
        "masked_context_sha256": hashlib.sha256(masked.encode()).hexdigest(),
        "certification_scope": "Founding-year table cells for two named subjects in selected reader documents; unrestricted prose equivalence unchecked",
    }


def validate_table_pair_task(world: SemanticWorld, task: TaskSpec) -> None:
    if task.family != "source_compare" or task.template != "source_compare":
        raise ValueError("incorrect reader renderer contract")
    if len(task.consumed_fact_ids) != 2 or len(set(task.scope.documents)) < 2:
        raise ValueError("table-pair requires two source documents and facts")
    if (
        tuple(side["fact_id"] for side in task.program["sides"])
        != task.consumed_fact_ids
    ):
        raise ValueError("table-pair proof identity drift")
    if execute_table_pair(world, task.program) != task.answer:
        raise ValueError("table-pair oracle drift")
    allowed = {fact.fact_id for fact in supported_year_facts(world)}
    if not set(task.consumed_fact_ids).issubset(allowed):
        raise ValueError("table-pair lacks supported year evidence")
    context = reader_view.render_documents(world, task.scope.documents)
    reader_year_cell_intervention(world, task, context.text)
