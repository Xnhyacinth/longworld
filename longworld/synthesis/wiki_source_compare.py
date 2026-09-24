"""Source-scoped comparison tasks from conflicting supported Wiki rows.

The target is what each frozen document says, not which real-world value is
correct. A pair is admitted only when each document supplies exactly one
structurally checked fact for the same subject and relation.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations
from typing import Any

from longworld.synthesis.dependency_ops import ProofItem
from longworld.synthesis.shared_semantic_world import Fact, ScopeEntry, SemanticWorld
from longworld.synthesis.wiki_evidence import check_locate_fact
from longworld.synthesis.world_task_bank import TaskSpec

COMPARABLE_RELATIONS = frozenset(("established in", "located in"))


def _source_doc(fact: Fact) -> str | None:
    ids = {span.doc_id for span in fact.supporting_spans}
    return next(iter(ids)) if len(ids) == 1 else None


def execute_source_compare(
    world: SemanticWorld, program: dict[str, Any]
) -> dict[str, Any]:
    """Execute the finite source-scoped oracle against the current fact set."""
    if program.get("op") != "source_compare" or len(program.get("sides", [])) != 2:
        raise ValueError("invalid source comparison program")
    output = {}
    for side in program["sides"]:
        fact = world.facts_by_id[side["fact_id"]]
        doc_id = _source_doc(fact)
        if (
            fact.subject != program["subject"]
            or fact.relation != program["relation"]
            or doc_id != side["doc_id"]
        ):
            raise ValueError("source comparison fact/scope drift")
        title = world._docs[doc_id].title
        if title in output:
            raise ValueError("source comparison titles are not unique")
        output[title] = fact.value
    return output


def build_source_compare_tasks(
    world: SemanticWorld, *, max_tasks: int = 4
) -> tuple[TaskSpec, ...]:
    """Enumerate different source reports over one subject/relation pair."""
    if max_tasks <= 0:
        return ()
    grouped: dict[tuple[str, str, str], list[Fact]] = defaultdict(list)
    for fact in world.facts:
        if fact.relation not in COMPARABLE_RELATIONS or fact.value_type == "entity":
            continue
        doc_id = _source_doc(fact)
        if doc_id is None or not check_locate_fact(world, fact).supported:
            continue
        grouped[(fact.subject, fact.relation, doc_id)].append(fact)

    pairs = []
    by_relation: dict[tuple[str, str], list[tuple[str, Fact]]] = defaultdict(list)
    for (subject, relation, doc_id), facts in grouped.items():
        if len(facts) == 1:
            by_relation[(subject, relation)].append((doc_id, facts[0]))
    for (subject, relation), items in sorted(by_relation.items()):
        for (doc_a, fact_a), (doc_b, fact_b) in combinations(sorted(items), 2):
            if fact_a.value == fact_b.value:
                continue
            column = fact_a.qualifiers.get("table_column")
            if not column or column != fact_b.qualifiers.get("table_column"):
                continue
            if world._docs[doc_a].title == world._docs[doc_b].title:
                continue
            pairs.append((subject, relation, doc_a, fact_a, doc_b, fact_b))

    tasks = []
    for subject, relation, doc_a, fact_a, doc_b, fact_b in pairs[:max_tasks]:
        digest = hashlib.sha256(
            f"{subject}|{relation}|{doc_a}|{doc_b}".encode()
        ).hexdigest()[:12]
        task_id = f"wiki-source-compare-{digest}"
        title_a = world._docs[doc_a].title
        title_b = world._docs[doc_b].title
        label = world.label_of(subject)
        column = fact_a.qualifiers["table_column"]
        question = (
            f"For {label}, what does the {column!r} column report in "
            f"{title_a} and in {title_b}, respectively?"
        )
        program = {
            "op": "source_compare",
            "subject": subject,
            "relation": relation,
            "sides": [
                {"doc_id": doc_a, "fact_id": fact_a.fact_id},
                {"doc_id": doc_b, "fact_id": fact_b.fact_id},
            ],
        }
        answer = execute_source_compare(world, program)
        proof = tuple(
            ProofItem(
                step=index,
                op="source_compare",
                out=f"source_{index}",
                kind="fact",
                ref_id=fact.fact_id,
                subject=subject,
                relation=relation,
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
                    object_families=(world.objects[subject].entity_type or "untyped",),
                    relations=(relation,),
                    documents=(doc_a, doc_b),
                ),
                answer=answer,
                answer_rendered=answer,
                proof=proof,
                consumed_fact_ids=(fact_a.fact_id, fact_b.fact_id),
                metrics={"source_documents": 2, "consumed_facts": 2},
                nondegenerate={"distinct_source_values": True},
                structure_signals={"relation": relation, "source_documents": 2},
                spread=(relation, doc_a, doc_b),
            )
        )
    return tuple(tasks)


def validate_source_compare_task(world: SemanticWorld, task: TaskSpec) -> None:
    if task.family != "source_compare" or task.template != "source_compare":
        raise ValueError("unsupported source comparison task")
    sides = task.program.get("sides", [])
    if len(sides) != 2 or len(task.scope.documents) != 2:
        raise ValueError("source comparison needs two documents")
    if tuple(side["doc_id"] for side in sides) != task.scope.documents:
        raise ValueError("source comparison scope drift")
    if tuple(side["fact_id"] for side in sides) != task.consumed_fact_ids:
        raise ValueError("source comparison lineage drift")
    if task.answer != execute_source_compare(world, task.program):
        raise ValueError("source comparison answer drift")
    facts = [world.facts_by_id[side["fact_id"]] for side in sides]
    if not facts[0].qualifiers.get("table_column") or facts[0].qualifiers[
        "table_column"
    ] != facts[1].qualifiers.get("table_column"):
        raise ValueError("source comparison column scope is ambiguous")
    if facts[0].value == facts[1].value:
        raise ValueError("source comparison values do not differ")
    if not all(check_locate_fact(world, fact).supported for fact in facts):
        raise ValueError("source comparison relation evidence failed")
