"""Conservative bind-then-lookup tasks over two frozen Wiki table documents.

The first table's unique selector binds a row name; that name selects a cell
in a second table. Both operations are replayed from final reader bytes.
Interventions certify this table-parser path, not unrestricted prose necessity.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from longworld.synthesis import reader_view, wiki_evidence, wiki_table_lookup
from longworld.synthesis.dependency_ops import ProofItem
from longworld.synthesis.shared_semantic_world import Fact, ScopeEntry, SemanticWorld
from longworld.synthesis.world_task_bank import TaskSpec

TARGET_RELATIONS = frozenset(
    {"established in", "opened in", "located in", "located in region"}
)


def _target_cell(
    world: SemanticWorld, fact: Fact, name: str
) -> tuple[Any, int, int] | None:
    if len(fact.supporting_spans) != 1:
        return None
    span = fact.supporting_spans[0]
    column = fact.qualifiers.get("table_column")
    if not isinstance(column, str) or fact.relation not in TARGET_RELATIONS:
        return None
    if not wiki_evidence.check_locate_fact(world, fact).supported:
        return None
    cells = wiki_table_lookup._cells(
        world._docs[span.doc_id].text, name, column, fact.relation
    )
    if len(cells) != 1 or not wiki_table_lookup._fact_matches_cell(
        world, fact, cells[0][0]
    ):
        return None
    if (cells[0][1], cells[0][2]) != (span.start, span.end):
        return None
    return cells[0]


def build_join_tasks(
    world: SemanticWorld, *, max_tasks: int = 32
) -> tuple[TaskSpec, ...]:
    """Emit only a unique first-table bind with a discriminating second table."""
    if max_tasks < 1:
        return ()
    tasks: list[TaskSpec] = []
    seen: set[tuple[str, str, str, str]] = set()
    by_subject: dict[str, list[Fact]] = defaultdict(list)
    by_doc_relation_column: dict[tuple[str, str, str], list[Fact]] = defaultdict(list)
    for fact in world.facts:
        by_subject[fact.subject].append(fact)
        column = fact.qualifiers.get("table_column")
        if len(fact.supporting_spans) == 1 and isinstance(column, str):
            by_doc_relation_column[
                (fact.supporting_spans[0].doc_id, fact.relation, column)
            ].append(fact)
    for bind in sorted(world.facts, key=lambda fact: fact.fact_id):
        if (
            bind.relation != "includes facility"
            or bind.value_type != "entity"
            or len(bind.supporting_spans) != 1
            or bind.value not in world.objects
        ):
            continue
        selector = wiki_table_lookup._inverse_selector(world, bind)
        if selector is None:
            continue
        name_column, selector_column, selector_value = selector
        bind_span = bind.supporting_spans[0]
        name = world.label_of(bind.value)
        inverse = wiki_table_lookup._inverse_cells(
            world._docs[bind_span.doc_id].text,
            name_column,
            selector_column,
            selector_value,
        )
        if len(inverse) != 1 or inverse[0] != (
            name,
            bind_span.start,
            bind_span.end,
        ):
            continue
        for target in sorted(by_subject[bind.value], key=lambda fact: fact.fact_id):
            if target.subject != bind.value or target.relation not in TARGET_RELATIONS:
                continue
            target_span = (
                target.supporting_spans[0]
                if len(target.supporting_spans) == 1
                else None
            )
            if target_span is None or target_span.doc_id == bind_span.doc_id:
                continue
            cell = _target_cell(world, target, name)
            if cell is None:
                continue
            # A place in the first row may paraphrase a place in the second
            # row, so masking the second cell would overstate its necessity.
            if target.relation in {"located in", "located in region"} and any(
                other.subject == bind.value
                and other.relation == target.relation
                and any(
                    span.doc_id == bind_span.doc_id for span in other.supporting_spans
                )
                for other in by_subject[bind.value]
            ):
                continue
            target_column = target.qualifiers["table_column"]
            # The second table must contain alternatives under this same
            # column. Otherwise the first lookup is only cosmetic.
            alternatives = {
                other.subject
                for other in by_doc_relation_column[
                    (target_span.doc_id, target.relation, target_column)
                ]
                if other.subject != target.subject
                and other.relation == target.relation
                and other.qualifiers.get("table_column") == target_column
                and len(other.supporting_spans) == 1
                and other.supporting_spans[0].doc_id == target_span.doc_id
                and _target_cell(world, other, world.label_of(other.subject))
                is not None
            }
            if not alternatives:
                continue
            key = (
                bind_span.doc_id,
                selector_column,
                selector_value,
                target_span.doc_id,
            )
            if key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha256(
                f"{bind.fact_id}|{target.fact_id}|{selector_column}".encode()
            ).hexdigest()[:16]
            task_id = f"wiki-cross-join-{digest}"
            source_title = world._docs[bind_span.doc_id].title
            target_title = world._docs[target_span.doc_id].title
            question = (
                f"In {source_title}, which {name_column!r} entry has "
                f"{selector_value!r} in its {selector_column!r} column? "
                f"For that entry, what is listed in the {target_column!r} "
                f"column of {target_title}? Answer with the second table's value."
            )
            program = {
                "op": "cross_document_table_join",
                "bind_doc_id": bind_span.doc_id,
                "bind_fact_id": bind.fact_id,
                "name_column": name_column,
                "selector_column": selector_column,
                "selector": selector_value,
                "target_doc_id": target_span.doc_id,
                "target_fact_id": target.fact_id,
                "target_column": target_column,
                "target_relation": target.relation,
            }
            proof = tuple(
                ProofItem(
                    step=step,
                    op="cross_document_table_join",
                    out="bound_name" if step == 0 else "target_value",
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
                for step, fact in enumerate((bind, target))
            )
            task = TaskSpec(
                task_id=task_id,
                family="cross_document_join",
                template="cross_document_table_join",
                question=question,
                program=program,
                scope=ScopeEntry(
                    task_id=task_id,
                    object_families=("untyped",),
                    relations=(bind.relation, target.relation),
                    documents=(bind_span.doc_id, target_span.doc_id),
                ),
                answer=cell[0],
                answer_rendered=cell[0],
                proof=proof,
                consumed_fact_ids=(bind.fact_id, target.fact_id),
                metrics={"source_documents": 2, "consumed_facts": 2},
                nondegenerate={
                    "unique_first_selector": True,
                    "second_table_alternatives": len(alternatives),
                },
                structure_signals={"target_column": target_column},
                spread=(source_title, target_title, target.relation),
            )
            context = reader_view.render_documents(world, task.scope.documents).text
            if reader_join_replay(world, task, context) != task.answer:
                continue
            try:
                reader_join_intervention(world, task, context)
            except ValueError:
                continue
            tasks.append(task)
            if len(tasks) >= max_tasks:
                return tuple(tasks)
    return tuple(tasks)


def execute_join(world: SemanticWorld, program: dict[str, Any]) -> Any:
    """Check pinned fact identities, then execute the two-step table program."""
    if program.get("op") != "cross_document_table_join":
        raise ValueError("wrong join operator")
    bind = world.facts_by_id[program["bind_fact_id"]]
    target = world.facts_by_id[program["target_fact_id"]]
    if (
        bind.relation != "includes facility"
        or bind.value_type != "entity"
        or len(bind.supporting_spans) != 1
        or len(target.supporting_spans) != 1
        or bind.supporting_spans[0].doc_id != program["bind_doc_id"]
        or target.subject != bind.value
        or target.supporting_spans[0].doc_id != program["target_doc_id"]
        or target.relation != program["target_relation"]
        or target.qualifiers.get("table_column") != program["target_column"]
    ):
        raise ValueError("join fact/source binding drift")
    bound = wiki_table_lookup._inverse_cells(
        world._docs[program["bind_doc_id"]].text,
        program["name_column"],
        program["selector_column"],
        program["selector"],
    )
    if len(bound) != 1 or bound[0][0] != world.label_of(bind.value):
        raise ValueError("first table no longer binds one entity")
    cell = _target_cell(world, target, bound[0][0])
    if cell is None:
        raise ValueError("second table lacks pinned target cell")
    return cell[0]


def _reader_cells(
    world: SemanticWorld, task: TaskSpec, context: str, *, allow_masked: bool
) -> tuple[tuple[Any, int, int], tuple[Any, int, int]]:
    rendered = reader_view.render_documents(world, task.scope.documents)
    if (not allow_masked and rendered.text != context) or len(rendered.text) != len(
        context
    ):
        raise ValueError("join final reader context drift")
    layouts = {layout.doc_id: layout for layout in rendered.layouts}
    program = task.program
    first = layouts[program["bind_doc_id"]]
    first_text = context[first.text_start : first.text_end]
    names = wiki_table_lookup._inverse_cells(
        first_text,
        program["name_column"],
        program["selector_column"],
        program["selector"],
    )
    if len(names) != 1:
        raise ValueError("reader join lacks unique first-table binding")
    second = layouts[program["target_doc_id"]]
    second_text = context[second.text_start : second.text_end]
    cells = wiki_table_lookup._cells(
        second_text,
        names[0][0],
        program["target_column"],
        program["target_relation"],
    )
    if len(cells) != 1:
        raise ValueError("reader join lacks unique second-table cell")
    name, name_start, name_end = names[0]
    value, value_start, value_end = cells[0]
    return (
        (name, first.text_start + name_start, first.text_start + name_end),
        (value, second.text_start + value_start, second.text_start + value_end),
    )


def reader_join_replay(world: SemanticWorld, task: TaskSpec, context: str) -> Any:
    """Solve from visible tables only, without looking up hidden fact values."""
    return _reader_cells(world, task, context, allow_masked=False)[1][0]


def reader_join_intervention(
    world: SemanticWorld, task: TaskSpec, context: str
) -> dict[str, Any]:
    """Mask each consumed cell independently and replay the parser."""
    first, second = _reader_cells(world, task, context, allow_masked=False)
    if second[0] != task.answer:
        raise ValueError("join reader replay disagrees with gold")
    outcomes = []
    for step, (_value, start, end) in enumerate((first, second)):
        masked = context[:start] + "?" * (end - start) + context[end:]
        try:
            new_answer = _reader_cells(world, task, masked, allow_masked=True)[1][0]
        except ValueError:
            new_answer = None
        if new_answer == task.answer:
            raise ValueError(f"masking join step {step} left answer unchanged")
        outcomes.append(
            {
                "step": step,
                "masked_cell_span": {"start": start, "end": end},
                "masked_context_sha256": hashlib.sha256(masked.encode()).hexdigest(),
            }
        )
    return {
        "status": "both_scoped_table_cells_necessary_for_parser",
        "interventions": outcomes,
        "certification_scope": "two named table operations; equivalent prose elsewhere unchecked",
    }
