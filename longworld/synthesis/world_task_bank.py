"""P74 W2-B: a structure-driven task bank over any SemanticWorld.

Charter (.hl/design/p74_real_shared_worlds.md) §16 W2-B row; §2/§3/§11
milestone-1 acceptance: a NEW same-kind source must yield scoped, correct,
text-supported tasks with different operations WITHOUT any per-topic code.

The bank inspects only WORLD STRUCTURE — entity families, relation kinds
(entity-valued vs attribute facts, attribute value types), timeline
supersession/revocation, hop reachability — and emits executable TaskSpecs
for four capability families:

- locate: bind one object by family+label, then follow one relation (the
  answer is the reached object ids) or read one attribute (the value).
- aggregate: bind, follow one relation, group the reached set by an attribute
  of the reached family, then count / sum / max / min per group. Non-degenerate
  filter: at least two groups.
- multi_hop: a chain whose second hop is KEYED on a binding computed by state
  resolution (charter §5: bind -> resolve versioned relation -> rule or
  follow from the RESOLVED value -> join on the bound result -> group ->
  aggregate). Every multi_hop program must pass the dependency_ops
  non-foldability gate (execute refuses it otherwise).
- as_of_state: resolve a timeline as of a date. Emitted only when the world
  has versioned facts (supersession or revocation); otherwise the family is
  skipped with a recorded reason.

Task selection is keyed ONLY on structure. No topic string, family name or
relation name is hardcoded: the demo observatory world and a differently
shaped team world go through exactly the same code paths. Question phrasing
is ONE countable template per family (charter §1: the variety claim is
OPERATIONAL — different programs, answers and consumed facts — not phrasal).

Every TaskSpec executes through dependency_ops.execute (answer + span proof +
consumed facts). The bank also emits fact-reuse statistics (charter §3.1:
which fact_ids appear in >=2 tasks' proofs) and records honestly: family
skips (missing structure), candidate rejections (degenerate or failing
execution), and the structure signals each task was keyed on.

Deterministic: sorted iteration everywhere, no RNG, standard library only.
This module adds no new dependencies and modifies no existing file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from longworld.synthesis import dependency_ops as ops
from longworld.synthesis.shared_semantic_world import (
    Fact,
    ScopeEntry,
    SemanticWorld,
)

CAPABILITY_FAMILIES = ("locate", "aggregate", "multi_hop", "as_of_state")
DEFAULT_BUDGET = {
    "locate": 6,
    "aggregate": 6,
    "multi_hop": 6,
    "as_of_state": 6,
}
_AGG_HOWS = ("count", "sum", "max", "min")
REJECTED_LOG_CAP = 40

# One template id per family (charter §1: countable phrasing). The rendered
# question may only name labels/relations/families/dates from the world.
TEMPLATES = {
    "locate_follow": (
        "In the documents in scope, bind the {family} labeled {label!r}, then "
        "list the objects related to it by {relation}. Answer with the object "
        "labels."
    ),
    "locate_attribute": (
        "In the documents in scope, bind the {family} labeled {label!r}, then "
        "report its {relation} value."
    ),
    "aggregate_group": (
        "In the documents in scope, bind the {family} labeled {label!r}, take "
        "the objects related to it by {relation}, group them by their {key} "
        "value, and give the {how}{field_phrase} of each group."
    ),
    "multi_hop_chain": (
        "Solve this step by step, using only the documents in scope. {chain}"
    ),
    "as_of_resolve": (
        "In the documents in scope, bind the {family} labeled {label!r} and "
        "report the value of its {relation} effective as of {at}."
    ),
}


@dataclass(frozen=True)
class TaskSpec:
    """One generated task: program + executed gold + proof + scope."""

    task_id: str
    family: str  # one of CAPABILITY_FAMILIES
    template: str  # question template id (phrasing stays countable)
    question: str
    program: dict[str, Any]  # executable by dependency_ops.execute
    scope: ScopeEntry
    answer: Any  # the program's return value
    answer_rendered: Any  # entity ids replaced by labels
    proof: tuple[ops.ProofItem, ...]
    consumed_fact_ids: tuple[str, ...]
    metrics: dict[str, Any]
    nondegenerate: dict[str, Any]  # why this task passed the filter
    structure_signals: dict[str, Any]  # structure keys that selected it
    spread: tuple[str, ...]  # round-robin key: distinct operations per family

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(
            {
                "task_id": self.task_id,
                "family": self.family,
                "template": self.template,
                "question": self.question,
                "program": self.program,
                "scope": self.scope.to_dict(),
                "answer": self.answer,
                "answer_rendered": self.answer_rendered,
                "proof": [item.to_dict() for item in self.proof],
                "consumed_fact_ids": list(self.consumed_fact_ids),
                "metrics": self.metrics,
                "nondegenerate": self.nondegenerate,
                "structure_signals": self.structure_signals,
                "spread": list(self.spread),
            }
        )


@dataclass(frozen=True)
class SkippedFamily:
    family: str
    reason: str  # names the structure that is missing


@dataclass(frozen=True)
class RejectedCandidate:
    family: str
    detail: str  # which candidate (subject/relation/date/...)
    reason: str  # why it was dropped: degenerate answer or failed execution


@dataclass(frozen=True)
class TaskBank:
    tasks: tuple[TaskSpec, ...]
    skipped: tuple[SkippedFamily, ...]
    rejected: tuple[RejectedCandidate, ...]
    reuse: dict[str, int]  # fact_id -> number of tasks whose proof consumes it
    reuse_summary: dict[str, Any]
    structure: dict[str, Any]  # the structure summary the bank keyed on
    budget: dict[str, Any]
    as_of: str | None

    def by_family(self, family: str) -> tuple[TaskSpec, ...]:
        return tuple(t for t in self.tasks if t.family == family)

    def counts(self) -> dict[str, int]:
        return {family: len(self.by_family(family)) for family in CAPABILITY_FAMILIES}

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(
            {
                "counts": self.counts(),
                "tasks": [task.to_dict() for task in self.tasks],
                "skipped": [
                    {"family": s.family, "reason": s.reason} for s in self.skipped
                ],
                "rejected": [
                    {
                        "family": r.family,
                        "detail": r.detail,
                        "reason": r.reason,
                    }
                    for r in self.rejected
                ],
                "reuse": self.reuse,
                "reuse_summary": self.reuse_summary,
                "structure": self.structure,
                "budget": self.budget,
                "as_of": self.as_of,
            }
        )


def _jsonable(value: Any) -> Any:
    """Tuples to lists, recursively, so to_dict() rows dump as JSON."""
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


# --- structure analysis: everything below reads the world's SHAPE only ---


@dataclass(frozen=True)
class RelationShape:
    """One entity-valued relation as observed in the fact set."""

    relation: str
    from_family: str | None  # subject family (None when mixed/absent)
    to_family: str | None  # value-entity family
    subjects: tuple[str, ...]  # distinct subjects, sorted
    fanout_min: int
    fanout_max: int


@dataclass(frozen=True)
class AttributeShape:
    """One attribute (non-entity) relation as observed in the fact set."""

    relation: str
    family: str | None
    value_type: str  # "string" | "number" | "rule"
    distinct_values: int
    card_min: int
    card_max: int


@dataclass(frozen=True)
class WorldStructure:
    """The whole structure summary the bank keys on, built once per world."""

    families: tuple[str, ...]  # entity types seen on entities (sorted)
    families_by_label: dict[str, tuple[str, ...]]  # family -> sorted object ids
    entity_relations: tuple[RelationShape, ...]  # typed relation edges
    attributes: tuple[AttributeShape, ...]  # non-entity facts
    relation_to_attribute: dict[str, tuple[str, ...]]  # to-family -> attribute names
    versioned_subjects: tuple[str, ...]  # ids with supersession or revocation
    versioned_relations: tuple[str, ...]  # relations with supersession or revocation
    dates: tuple[str, ...]  # distinct fact times, sorted (ISO-ish strings)
    doc_ids: tuple[str, ...]
    all_relations: tuple[str, ...]


def _family_of(world: SemanticWorld, entity_id: str) -> str | None:
    entity = world.objects.get(entity_id)
    return entity.entity_type if entity else None


def analyze_structure(world: SemanticWorld) -> WorldStructure:
    """Read families, relation kinds, attribute types, timeline shape, dates."""
    families: set[str] = set()
    families_by_label: dict[str, list[str]] = {}
    for entity in world.entities:
        family = entity.entity_type
        if family is None:
            continue  # untyped objects cannot be bound by family+label
        families.add(family)
        families_by_label.setdefault(family, []).append(entity.entity_id)

    entity_edges: dict[str, list[Fact]] = {}
    for fact in world.facts:
        if fact.value_type == "entity":
            entity_edges.setdefault(fact.relation, []).append(fact)
    relation_shapes = []
    for relation in sorted(entity_edges):
        facts = entity_edges[relation]
        from_families = {_family_of(world, f.subject) for f in facts}
        to_families = {_family_of(world, f.value) for f in facts}
        fanouts: dict[str, int] = {}
        for f in facts:
            fanouts[f.subject] = fanouts.get(f.subject, 0) + 1
        relation_shapes.append(
            RelationShape(
                relation=relation,
                from_family=next(iter(from_families))
                if len(from_families) == 1
                else None,
                to_family=next(iter(to_families)) if len(to_families) == 1 else None,
                subjects=tuple(sorted(fanouts)),
                fanout_min=min(fanouts.values()),
                fanout_max=max(fanouts.values()),
            )
        )

    attribute_facts: dict[str, list[Fact]] = {}
    for fact in world.facts:
        if fact.value_type != "entity":
            attribute_facts.setdefault(fact.relation, []).append(fact)
    attribute_shapes = []
    for relation in sorted(attribute_facts):
        facts = attribute_facts[relation]
        families_seen = {_family_of(world, f.subject) for f in facts}
        card: dict[str, int] = {}
        values: set[Any] = set()
        for f in facts:
            card[f.subject] = card.get(f.subject, 0) + 1
            values.add(str(f.value))
        attribute_shapes.append(
            AttributeShape(
                relation=relation,
                family=next(iter(families_seen)) if len(families_seen) == 1 else None,
                value_type=facts[0].value_type,
                distinct_values=len(values),
                card_min=min(card.values()),
                card_max=max(card.values()),
            )
        )

    relation_to_attribute: dict[str, list[str]] = {family: [] for family in families}
    for shape in relation_shapes:
        if shape.to_family is None:
            continue
        for attribute in attribute_shapes:
            if attribute.family == shape.to_family:
                relation_to_attribute[shape.to_family].append(attribute.relation)
    relation_to_attribute = {
        family: tuple(sorted(set(attrs)))
        for family, attrs in relation_to_attribute.items()
    }

    versioned_pairs: set[tuple[str, str]] = set()
    versioned_rels: set[str] = set()
    for (subject, relation), entries in world.timeline.items():
        has_supersession = len({e.value for e in entries}) > 1 or len(entries) > 1
        has_revocation = any(e.revoked_at is not None for e in entries)
        if has_supersession or has_revocation:
            versioned_pairs.add((subject, relation))
            versioned_rels.add(relation)
    timeline_subjects = {subject for subject, _ in versioned_pairs}

    dates = sorted({fact.time for fact in world.facts if fact.time})

    return WorldStructure(
        families=tuple(sorted(families)),
        families_by_label={
            family: tuple(sorted(ids)) for family, ids in families_by_label.items()
        },
        entity_relations=tuple(relation_shapes),
        attributes=tuple(attribute_shapes),
        relation_to_attribute=relation_to_attribute,
        versioned_subjects=tuple(sorted(timeline_subjects)),
        versioned_relations=tuple(sorted(versioned_rels)),
        dates=tuple(dates),
        doc_ids=tuple(sorted(doc.doc_id for doc in world.documents)),
        all_relations=tuple(sorted({fact.relation for fact in world.facts})),
    )


# --- candidate enumeration: all pairs derived from structure, sorted ---


def _bindable_objects(
    world: SemanticWorld, structure: WorldStructure, family: str
) -> tuple[str, ...]:
    """Family members whose family+label bind is unambiguous in this world."""
    out = []
    for entity_id in structure.families_by_label.get(family, ()):
        entity = world.objects[entity_id]
        same_label = [
            other
            for other in structure.families_by_label.get(family, ())
            if world.objects[other].label == entity.label
        ]
        if len(same_label) == 1:
            out.append(entity_id)
    return tuple(out)


def _scope_for(
    structure: WorldStructure, families: set[str], relations: set[str]
) -> ScopeEntry:
    """One scope entry naming exactly the families/relations/docs a task reads."""
    return ScopeEntry(
        task_id="pending",  # replaced per task
        object_families=tuple(sorted(families)),
        relations=tuple(sorted(relations)),
        documents=structure.doc_ids,
    )


def _relations_from(
    structure: WorldStructure, family: str, fanout_min: int = 1
) -> tuple[RelationShape, ...]:
    """Typed relations whose subjects live in `family` with enough edges."""
    return tuple(
        shape
        for shape in structure.entity_relations
        if shape.from_family == family and shape.fanout_min >= fanout_min
    )


def _attributes_of(
    structure: WorldStructure, family: str
) -> tuple[AttributeShape, ...]:
    return tuple(shape for shape in structure.attributes if shape.family == family)


def _versioned_timelines_of(
    world: SemanticWorld,
) -> tuple[tuple[str, str, tuple[Any, ...]], ...]:
    """(subject, relation, entries) for timelines that supersede or revoke."""
    out = []
    for (subject, relation), entries in sorted(world.timeline.items()):
        revoked = any(e.revoked_at is not None for e in entries)
        # supersession needs values that REPLACE one another: distinct
        # effective_from dates (or a revocation). Several entries with the
        # SAME effective date and no revocation are fan-out edges (one
        # member works on several tasks), not a versioned state.
        distinct_dates = {e.effective_from for e in entries}
        superseded = len({e.value for e in entries}) > 1 and (
            len(distinct_dates) > 1 or revoked
        )
        single_superseded = len({e.value for e in entries}) == 1 and len(entries) > 1
        if revoked or superseded or single_superseded:
            out.append((subject, relation, entries))
    return tuple(out)


def _as_of(world: SemanticWorld, structure: WorldStructure) -> str | None:
    """A late date covering every timeline entry (after the last revocation)."""
    timelines = _versioned_timelines_of(world)
    if not timelines:
        return None
    dates = set(structure.dates)
    for _subject, _relation, entries in timelines:
        for entry in entries:
            if entry.effective_from is not None:
                dates.add(entry.effective_from)
            if entry.revoked_at is not None:
                dates.add(entry.revoked_at)
    if not dates:
        return None
    return max(dates)


@dataclass(frozen=True)
class _Candidate:
    """One enumerated candidate, before execution and selection."""

    family: str
    template: str
    spread: tuple[str, ...]  # round-robin key: spread across operations
    detail: str  # which world structure this candidate came from
    question: str
    program: dict[str, Any]
    scope_families: frozenset[str]
    scope_relations: frozenset[str]
    signals: dict[str, Any]


def _scope_parts(
    program: dict[str, Any], structure: WorldStructure
) -> tuple[frozenset[str], frozenset[str]]:
    """Families and relations a program touches, from its steps + shapes."""
    families: set[str] = set()
    relations: set[str] = set()
    for step in program["steps"]:
        if step.get("family"):
            families.add(step["family"])
        for name in ("relation", "status_relation", "key", "field"):
            if step.get(name):
                relations.add(step[name])
    for shape in structure.entity_relations:
        if shape.relation in relations and shape.to_family:
            families.add(shape.to_family)
    for shape in structure.attributes:
        if shape.relation in relations and shape.family:
            families.add(shape.family)
    return frozenset(families), frozenset(relations)


def _render_answer(world: SemanticWorld, value: Any) -> Any:
    """Entity ids become labels; everything else passes through."""
    if isinstance(value, tuple):
        if value and all(isinstance(v, str) and v in world.objects for v in value):
            return [world.label_of(v) for v in value]
        return [_render_answer(world, v) for v in value]
    if isinstance(value, list):
        return [_render_answer(world, v) for v in value]
    if isinstance(value, dict):
        return {key: _render_answer(world, v) for key, v in value.items()}
    if isinstance(value, str) and value in world.objects:
        return world.label_of(value)
    return value


def _field_phrase(how: str, field: str) -> str:
    return "" if how == "count" else f" of {field}"


# --- family: locate (bind by family+label, then one relation hop or read) ---


def _locate_candidates(
    world: SemanticWorld, structure: WorldStructure, limit: int
) -> tuple[list[_Candidate], list[RejectedCandidate]]:
    accepted: list[_Candidate] = []
    rejected: list[RejectedCandidate] = []
    counted = 0
    for family in structure.families:
        for entity_id in _bindable_objects(world, structure, family):
            label = world.label_of(entity_id)
            # entity-valued relation hops
            for shape in _relations_from(structure, family):
                counted += 1
                if counted > limit:
                    break
                program = {
                    "steps": [
                        {"op": "bind", "out": "v0", "family": family, "label": label},
                        {
                            "op": "follow_relation",
                            "out": "v1",
                            "subject": "$v0",
                            "relation": shape.relation,
                        },
                    ],
                    "return": "v1",
                }
                question = TEMPLATES["locate_follow"].format(
                    family=family, label=label, relation=shape.relation
                )
                fams, rels = _scope_parts(program, structure)
                accepted.append(
                    _Candidate(
                        family="locate",
                        template="locate_follow",
                        spread=("follow", shape.relation),
                        detail=f"{entity_id} -{shape.relation}->",
                        question=question,
                        program=program,
                        scope_families=fams,
                        scope_relations=rels,
                        signals={
                            "kind": "entity_relation",
                            "family": family,
                            "entity": entity_id,
                            "relation": shape.relation,
                            "to_family": shape.to_family,
                        },
                    )
                )
            if counted > limit:
                break
            # single-valued attribute reads
            for shape in _attributes_of(structure, family):
                counted += 1
                if counted > limit:
                    break
                program = {
                    "steps": [
                        {"op": "bind", "out": "v0", "family": family, "label": label},
                        {
                            "op": "bind",
                            "out": "v1",
                            "subject": "$v0",
                            "relation": shape.relation,
                        },
                    ],
                    "return": "v1",
                }
                question = TEMPLATES["locate_attribute"].format(
                    family=family, label=label, relation=shape.relation
                )
                fams, rels = _scope_parts(program, structure)
                accepted.append(
                    _Candidate(
                        family="locate",
                        template="locate_attribute",
                        spread=("attribute", shape.relation),
                        detail=f"{entity_id}.{shape.relation}",
                        question=question,
                        program=program,
                        scope_families=fams,
                        scope_relations=rels,
                        signals={
                            "kind": "attribute",
                            "family": family,
                            "entity": entity_id,
                            "relation": shape.relation,
                            "value_type": shape.value_type,
                        },
                    )
                )
            if counted > limit:
                break
    return accepted, rejected


# --- family: aggregate (bind, follow, group by attribute, aggregate) ---


def _aggregate_candidates(
    world: SemanticWorld, structure: WorldStructure, limit: int
) -> tuple[list[_Candidate], list[RejectedCandidate]]:
    accepted: list[_Candidate] = []
    rejected: list[RejectedCandidate] = []
    counted = 0
    for family in structure.families:
        for entity_id in _bindable_objects(world, structure, family):
            label = world.label_of(entity_id)
            for shape in _relations_from(structure, family):
                to_family = shape.to_family
                if to_family is None:
                    continue
                keys = [
                    attr.relation
                    for attr in _attributes_of(structure, to_family)
                    if attr.distinct_values >= 2  # the key can actually split
                ]
                numbers = [
                    attr.relation
                    for attr in _attributes_of(structure, to_family)
                    if attr.value_type == "number"
                ]
                if not keys or not numbers:
                    continue  # no split key or no numeric aggregate field here
                for key in keys:
                    for number in numbers:
                        for how in _AGG_HOWS:
                            counted += 1
                            if counted > limit:
                                break
                            program = {
                                "steps": [
                                    {
                                        "op": "bind",
                                        "out": "v0",
                                        "family": family,
                                        "label": label,
                                    },
                                    {
                                        "op": "follow_relation",
                                        "out": "v1",
                                        "subject": "$v0",
                                        "relation": shape.relation,
                                    },
                                    {
                                        "op": "group",
                                        "out": "v2",
                                        "items": "$v1",
                                        "key": key,
                                    },
                                    {
                                        "op": "aggregate",
                                        "out": "v3",
                                        "groups": "$v2",
                                        "how": how,
                                        "field": number,
                                    },
                                ],
                                "return": "v3",
                            }
                            question = TEMPLATES["aggregate_group"].format(
                                family=family,
                                label=label,
                                relation=shape.relation,
                                key=key,
                                how=how,
                                field_phrase=_field_phrase(how, number),
                            )
                            fams, rels = _scope_parts(program, structure)
                            accepted.append(
                                _Candidate(
                                    family="aggregate",
                                    template="aggregate_group",
                                    spread=(shape.relation, how),
                                    detail=(
                                        f"{entity_id} -{shape.relation}-> "
                                        f"{to_family} group:{key} {how}:{number}"
                                    ),
                                    question=question,
                                    program=program,
                                    scope_families=fams,
                                    scope_relations=rels,
                                    signals={
                                        "family": family,
                                        "entity": entity_id,
                                        "relation": shape.relation,
                                        "to_family": to_family,
                                        "group_key": key,
                                        "how": how,
                                        "field": number,
                                    },
                                )
                            )
                        if counted > limit:
                            break
                    if counted > limit:
                        break
                if counted > limit:
                    break
            if counted > limit:
                break
    return accepted, rejected


# --- family: multi_hop (chains whose next hop is keyed on a bound result) ---


def _multi_hop_candidates(
    world: SemanticWorld, structure: WorldStructure, limit: int
) -> tuple[list[_Candidate], list[RejectedCandidate]]:
    """Enumerate chain SHAPES from structure; execution validates each.

    The shapes (all start bind -> resolve_version over a versioned relation,
    so the second step's subject key is a computed binding, and every program
    is executed with the dependency_ops non-foldability gate ON):

    A  bind -> resolve(R1) [entity] -> follow(R2 from resolved) ->
       join(R3) [status?] -> group -> aggregate
    B  bind -> resolve(R1) [entity] -> resolve(R2 on resolved) [rule] ->
       follow(R3 from bound head) -> apply_rule($rule) -> join(R4) [status?]
       -> group -> aggregate
    C  bind -> resolve(R1) [entity] -> resolve(R2 on resolved) [entity] ->
       follow(R3 from resolved) -> join(R4) [status?] -> group -> aggregate

    Only world STRUCTURE picks R1..R4 (versioned relation existence, relation
    families, attribute split keys and numeric fields); which values the chain
    actually visits is decided by execution, not by this code.
    """
    accepted: list[_Candidate] = []
    rejected: list[RejectedCandidate] = []
    counted = 0
    as_of = _as_of(world, structure)
    if as_of is None:
        return accepted, rejected
    for subject, relation, _entries in _versioned_timelines_of(world):
        subject_family = _family_of(world, subject)
        if subject_family is None:
            continue
        if subject not in _bindable_objects(world, structure, subject_family):
            continue
        label = world.label_of(subject)
        resolved = _peek_resolved(world, subject, relation, as_of)
        if resolved is None:
            continue
        resolved_id, resolved_family = resolved
        if resolved_family is None:
            continue  # resolved value is not an object; no follow from it
        for shape_name, steps, spread, signals in _chain_variants(
            world,
            structure,
            subject_family,
            label,
            relation,
            resolved_id,
            resolved_family,
            as_of,
        ):
            counted += 1
            if counted > limit:
                break
            program = {"steps": steps, "return": steps[-1]["out"]}
            chain = ops.describe_program(program)
            question = TEMPLATES["multi_hop_chain"].format(chain=chain)
            fams, rels = _scope_parts(program, structure)
            accepted.append(
                _Candidate(
                    family="multi_hop",
                    template="multi_hop_chain",
                    spread=spread,
                    detail=f"{shape_name}: {subject} -{relation}(resolve)-> ...",
                    question=question,
                    program=program,
                    scope_families=fams,
                    scope_relations=rels,
                    signals={"shape": shape_name, "head": subject, **signals},
                )
            )
    return accepted, rejected


def _peek_resolved(
    world: SemanticWorld, subject: str, relation: str, at: str
) -> tuple[str, str | None] | None:
    """Build-time peek at the as-of value; None when it does not resolve."""
    try:
        entry = world.resolve_at(subject, relation, at)
    except ValueError:
        return None
    return entry.value, _family_of(world, entry.value)


def _numeric_fields(structure: WorldStructure, family: str | None) -> tuple[str, ...]:
    if family is None:
        return ()
    return tuple(
        shape.relation
        for shape in _attributes_of(structure, family)
        if shape.value_type == "number"
    )


def _split_keys(structure: WorldStructure, family: str | None) -> tuple[str, ...]:
    if family is None:
        return ()
    return tuple(
        shape.relation
        for shape in _attributes_of(structure, family)
        if shape.distinct_values >= 2
    )


def _status_relations(
    world: SemanticWorld, structure: WorldStructure, family: str | None
) -> tuple[str, ...]:
    """Attributes of `family` whose timelines show STATE, not plain data.

    The world timeline holds every fact, so an attribute being IN it is not
    evidence of state. State evidence is supersession (values replacing one
    another, per _versioned_timelines_of) or revocation on at least one
    member of the family. Attributes that merely vary across members are
    group keys, not status relations.
    """
    if family is None:
        return ()
    versioned = {
        (subject, relation)
        for subject, relation, _entries in _versioned_timelines_of(world)
    }
    members = structure.families_by_label.get(family, ())
    out = []
    for shape in _attributes_of(structure, family):
        if any((member, shape.relation) in versioned for member in members):
            out.append(shape.relation)
    return tuple(sorted(out))


def _tail_variants(
    world: SemanticWorld,
    structure: WorldStructure,
    items_ref: str,
    join_shape: RelationShape,
    as_of: str,
) -> list[tuple[list[dict[str, Any]], tuple[str, ...]]]:
    """join -> group -> aggregate tails for one join edge.

    Structure picks everything: the split keys and numeric fields of the join
    target family, the aggregate ops, and which timeline attributes of the
    target may act as the join's status check (with and without a status
    check). Status variants whose values never match "active" drop out at
    execution and are recorded as rejections — no topic knowledge filters
    them here.
    """
    target = join_shape.to_family
    keys = _split_keys(structure, target)
    fields = _numeric_fields(structure, target)
    if not keys or not fields:
        return []
    statuses = [None, *_status_relations(world, structure, target)]
    out: list[tuple[list[dict[str, Any]], tuple[str, ...]]] = []
    for key in keys[:2]:
        for field in fields[:2]:
            for how in _AGG_HOWS:
                for status in statuses:
                    join_step: dict[str, Any] = {
                        "op": "join_on_bound_result",
                        "out": "vJ",
                        "left": items_ref,
                        "relation": join_shape.relation,
                    }
                    if status is not None:
                        join_step["status_relation"] = status
                        join_step["at"] = as_of
                        join_step["require_active"] = True
                    steps = [
                        join_step,
                        {"op": "group", "out": "vG", "items": "$vJ", "key": key},
                        {
                            "op": "aggregate",
                            "out": "vA",
                            "groups": "$vG",
                            "how": how,
                            "field": field,
                        },
                    ]
                    spread = (join_shape.relation, key, how, status or "no-status")
                    out.append((steps, spread))
    return out


def _chain_variants(
    world: SemanticWorld,
    structure: WorldStructure,
    head_family: str,
    head_label: str,
    relation: str,
    resolved_id: str,
    resolved_family: str,
    as_of: str,
) -> list[tuple[str, list[dict[str, Any]], tuple[str, ...], dict[str, Any]]]:
    """(shape name, program steps, spread, signals) for one resolved head.

    Steps bind v0 (the head object) and resolve v1 (the versioned relation);
    variants then key their next hop on $v1 or on a second resolution from
    $v1. Every variant therefore has a COMPUTED key binding and must pass the
    dependency_ops non-foldability gate at execution time.
    """
    bind = {"op": "bind", "out": "v0", "family": head_family, "label": head_label}
    resolve1 = {
        "op": "resolve_version",
        "out": "v1",
        "subject": "$v0",
        "relation": relation,
        "at": as_of,
    }
    variants: list[
        tuple[str, list[dict[str, Any]], tuple[str, ...], dict[str, Any]]
    ] = []

    # shape A: follow directly from the resolved object
    for second in structure.entity_relations:
        if second.from_family != resolved_family:
            continue
        follow = {
            "op": "follow_relation",
            "out": "v2",
            "subject": "$v1",
            "relation": second.relation,
        }
        for join_shape in structure.entity_relations:
            if join_shape.from_family != second.to_family:
                continue
            for tail, tail_spread in _tail_variants(
                world, structure, "$v2", join_shape, as_of
            ):
                steps = [bind, resolve1, follow, *tail]
                variants.append(
                    (
                        "A",
                        steps,
                        (relation, second.relation, *tail_spread),
                        {
                            "resolved_to_family": resolved_family,
                            "hop2": second.relation,
                            "join": join_shape.relation,
                        },
                    )
                )

    # second resolution from the resolved object (rule/scalar or entity)
    second_relations = sorted(
        rel for (subject, rel) in world.timeline if subject == resolved_id
    )
    for second_relation in second_relations:
        peek = _peek_resolved(world, resolved_id, second_relation, as_of)
        if peek is None:
            continue
        second_value, second_family = peek
        resolve2 = {
            "op": "resolve_version",
            "out": "v2",
            "subject": "$v1",
            "relation": second_relation,
            "at": as_of,
        }
        if second_family is None:
            # shape B: the second resolution yields a rule (or scalar) that
            # filters a candidate set bound from the head object
            for follow_shape in structure.entity_relations:
                if follow_shape.from_family not in (head_family, resolved_family):
                    continue
                if not _numeric_fields(structure, follow_shape.to_family):
                    continue  # the rule needs a comparable field downstream
                follow = {
                    "op": "follow_relation",
                    "out": "v3",
                    "subject": (
                        "$v0" if follow_shape.from_family == head_family else "$v1"
                    ),
                    "relation": follow_shape.relation,
                }
                apply_rule = {
                    "op": "apply_rule",
                    "out": "v4",
                    "rule": "$v2",
                    "over": "$v3",
                }
                for join_shape in structure.entity_relations:
                    if join_shape.from_family != follow_shape.to_family:
                        continue
                    for tail, tail_spread in _tail_variants(
                        world, structure, "$v4", join_shape, as_of
                    ):
                        steps = [bind, resolve1, resolve2, follow, apply_rule, *tail]
                        variants.append(
                            (
                                "B",
                                steps,
                                (
                                    relation,
                                    second_relation,
                                    follow_shape.relation,
                                    *tail_spread,
                                ),
                                {
                                    "resolved_to_family": resolved_family,
                                    "resolved2": second_value,
                                    "filter": follow_shape.relation,
                                    "join": join_shape.relation,
                                },
                            )
                        )
        else:
            # shape C: the second resolution yields an object; follow from it
            for follow_shape in structure.entity_relations:
                if follow_shape.from_family != second_family:
                    continue
                follow = {
                    "op": "follow_relation",
                    "out": "v3",
                    "subject": "$v2",
                    "relation": follow_shape.relation,
                }
                for join_shape in structure.entity_relations:
                    if join_shape.from_family != follow_shape.to_family:
                        continue
                    for tail, tail_spread in _tail_variants(
                        world, structure, "$v3", join_shape, as_of
                    ):
                        steps = [bind, resolve1, resolve2, follow, *tail]
                        variants.append(
                            (
                                "C",
                                steps,
                                (
                                    relation,
                                    second_relation,
                                    follow_shape.relation,
                                    *tail_spread,
                                ),
                                {
                                    "resolved_to_family": resolved_family,
                                    "resolved2": second_value,
                                    "hop2": follow_shape.relation,
                                    "join": join_shape.relation,
                                },
                            )
                        )
    return variants


# --- family: as_of_state (timeline resolution, only with versioned facts) ---


def _as_of_state_candidates(
    world: SemanticWorld, structure: WorldStructure, limit: int
) -> tuple[list[_Candidate], list[RejectedCandidate]]:
    accepted: list[_Candidate] = []
    rejected: list[RejectedCandidate] = []
    counted = 0
    timelines = _versioned_timelines_of(world)
    for subject, relation, _entries in timelines:
        subject_family = _family_of(world, subject)
        if subject_family is None:
            continue
        if subject not in _bindable_objects(world, structure, subject_family):
            continue
        # resolve_version needs a single winner per date; that is decided
        # per (subject, relation, at) by the world's own resolver, not here.
        # Dates where resolution fails (nothing effective, or a tie) are
        # pre-validated below and simply do not become tasks.
        # informative dates for THIS timeline: each point where the active
        # value can change (entries + revocations + horizon), filtered to
        # those where resolution actually succeeds
        points = _as_of_choices(world, structure)
        working = []
        for at in points:
            try:
                if world.status_at(subject, relation, at) is not None:
                    working.append(at)
            except ValueError:
                continue
        if not working:
            continue
        # answer-flip dates first: walk the sorted points, keep the first date
        # of each distinct resolved value (a supersession boundary), then any
        # remaining working dates. A date whose RESOLVED VALUE equals the date
        # itself (year-valued timelines like wiki "established in") makes the
        # answer literally appear in the question — reject it; if every flip
        # date is such a self-answering date the timeline yields no task.
        flips: dict[Any, str] = {}
        rest: list[str] = []
        for at in working:
            value = world.status_at(subject, relation, at).value
            if str(value) == str(at):
                continue
            if value not in flips:
                flips[value] = at
            else:
                rest.append(at)
        rest = [
            at
            for at in rest
            if str(world.status_at(subject, relation, at).value) != str(at)
        ]
        ordered = [*flips.values(), *rest]
        per_timeline = max(2, limit // max(1, len(timelines)))
        for at in ordered[:per_timeline]:
            counted += 1
            label = world.label_of(subject)
            program = {
                "steps": [
                    {
                        "op": "bind",
                        "out": "v0",
                        "family": subject_family,
                        "label": label,
                    },
                    {
                        "op": "resolve_version",
                        "out": "v1",
                        "subject": "$v0",
                        "relation": relation,
                        "at": at,
                    },
                ],
                "return": "v1",
            }
            question = TEMPLATES["as_of_resolve"].format(
                family=subject_family, label=label, relation=relation, at=at
            )
            fams, rels = _scope_parts(program, structure)
            accepted.append(
                _Candidate(
                    family="as_of_state",
                    template="as_of_resolve",
                    spread=(relation, at),
                    detail=f"{subject}.{relation} @ {at}",
                    question=question,
                    program=program,
                    scope_families=fams,
                    scope_relations=rels,
                    signals={
                        "subject": subject,
                        "relation": relation,
                        "at": at,
                    },
                )
            )
        if counted > limit:
            break
    return accepted, rejected


def _as_of_choices(world: SemanticWorld, structure: WorldStructure) -> tuple[str, ...]:
    """Informative dates: entry points, revocation points, all fact dates."""
    points: set[str] = set(structure.dates)
    for _subject, _relation, entries in _versioned_timelines_of(world):
        for entry in entries:
            if entry.effective_from is not None:
                points.add(entry.effective_from)
            if entry.revoked_at is not None:
                points.add(entry.revoked_at)
    horizon = _as_of(world, structure)
    if horizon is not None:
        points.add(horizon)
    return tuple(sorted(points))


# --- execution, non-degeneracy, selection, and the public API ---


def _nondegenerate_check(
    world: SemanticWorld,
    family: str,
    result: ops.ExecutionResult,
    program: dict[str, Any] | None = None,
) -> str | None:
    """Reason the answer is degenerate, or None if it is a real task.

    locate: non-empty and not every object of the family (a trivial answer
    would be the whole family or nothing).
    aggregate: at least two groups; count per group >= 1 is inherent; sum
    needs the values to differ (identical sums across groups are still
    informative for count/max/min, but a single group is not).
    multi_hop: at least two groups AND >=2 consumed facts on the joining hop
    (a one-fact join is a locate in disguise).
    as_of_state: the resolved value must differ from the queried date (a
    year-valued timeline asked as-of its own year prints the answer in the
    question).
    """
    answer = result.answer
    if family == "locate":
        if isinstance(answer, tuple):
            return None if answer else "answer empty"
        if answer is None or answer == "":
            return "answer empty"
        return None
    if family == "as_of_state":
        # A timeline whose value equals the queried date ("established in"
        # 1976 asked as-of 1976) prints its own answer in the question; the
        # candidate filter above rejects those, and this guard keeps any
        # future emitter honest.
        at = next(
            (
                step.get("at")
                for step in (program or {}).get("steps", [])
                if step.get("op") == "resolve_version"
            ),
            None,
        )
        if at is not None and str(answer) == str(at):
            return "answer equals the queried date (self-answering timeline)"
        return None
    # aggregate / multi_hop
    if not isinstance(answer, dict) or "groups" not in answer:
        return "answer has no groups"
    groups = answer["groups"]
    if len(groups) < 2:
        return f"only {len(groups)} group(s)"
    if family == "multi_hop":
        if len(result.consumed_fact_ids) < 4:
            return (
                f"chain consumed only {len(result.consumed_fact_ids)} facts "
                "(a locate in disguise)"
            )
        if not result.metrics.get("non_foldable"):
            return "fold gate did not pass"
    return None


def _run_candidate(
    world: SemanticWorld,
    structure: WorldStructure,
    family: str,
    candidate: _Candidate,
    require_fold_gate: bool,
) -> tuple[TaskSpec | None, RejectedCandidate | None]:
    """Execute one candidate; non-degenerate and runnable, or rejected."""
    try:
        result = ops.execute(
            world, candidate.program, require_fold_gate=require_fold_gate
        )
    except ValueError as error:
        return None, RejectedCandidate(
            family=family,
            detail=candidate.detail,
            reason=f"execution failed: {error}",
        )
    degenerate = _nondegenerate_check(world, family, result, candidate.program)
    if degenerate is not None:
        return None, RejectedCandidate(
            family=family,
            detail=candidate.detail,
            reason=f"degenerate: {degenerate}",
        )
    scope = _scope_for(
        structure,
        set(candidate.scope_families),
        set(candidate.scope_relations),
    )
    return _finalize(world, family, candidate, result, scope), None


def _finalize(
    world: SemanticWorld,
    family: str,
    candidate: _Candidate,
    result: ops.ExecutionResult,
    scope: ScopeEntry,
) -> TaskSpec:
    return TaskSpec(
        task_id="pending",
        family=family,
        template=candidate.template,
        question=candidate.question,
        program=candidate.program,
        scope=scope,
        answer=result.answer,
        answer_rendered=_render_answer(world, result.answer),
        proof=result.proof,
        consumed_fact_ids=result.consumed_fact_ids,
        metrics=result.metrics,
        nondegenerate={
            "check": "passed",
            "groups": (
                len(result.answer["groups"])
                if isinstance(result.answer, dict) and "groups" in result.answer
                else None
            ),
        },
        structure_signals=candidate.signals,
        spread=candidate.spread,
    )


def _select(
    executed: list[TaskSpec],
    limit: int,
) -> tuple[TaskSpec, ...]:
    """Pick up to `limit` tasks spread across distinct spread-keys.

    Round-robin over the sorted distinct first elements of each candidate's
    spread key so consecutive tasks differ in the operation they exercise.
    """
    if limit <= 0:
        return ()
    by_key: dict[str, list[TaskSpec]] = {}
    for task in executed:
        key = "|".join(task.spread) or task.task_id
        by_key.setdefault(key, []).append(task)
    buckets = [by_key[key] for key in sorted(by_key)]
    chosen: list[TaskSpec] = []
    cursor = 0
    while len(chosen) < limit and any(buckets):
        bucket = buckets[cursor % len(buckets)]
        if bucket:
            chosen.append(bucket.pop(0))
        if all(not bucket for bucket in buckets):
            break
        cursor += 1
    return tuple(chosen)


def _assign_ids(tasks: tuple[TaskSpec, ...], prefix: str) -> tuple[TaskSpec, ...]:
    out = []
    counter = {}
    for task in tasks:
        counter[task.family] = counter.get(task.family, 0) + 1
        task_id = f"{prefix}-{task.family}-{counter[task.family]:03d}"
        scope = ScopeEntry(
            task_id=task_id,
            object_families=task.scope.object_families,
            relations=task.scope.relations,
            documents=task.scope.documents,
            time_range=task.scope.time_range,
        )
        out.append(
            TaskSpec(
                task_id=task_id,
                family=task.family,
                template=task.template,
                question=task.question,
                program=task.program,
                scope=scope,
                answer=task.answer,
                answer_rendered=task.answer_rendered,
                proof=task.proof,
                consumed_fact_ids=task.consumed_fact_ids,
                metrics=task.metrics,
                nondegenerate=task.nondegenerate,
                structure_signals=task.structure_signals,
                spread=task.spread,
            )
        )
    return tuple(out)


def _reuse_stats(tasks: tuple[TaskSpec, ...]) -> tuple[dict[str, int], dict[str, Any]]:
    counts: dict[str, int] = {}
    for task in tasks:
        for fact_id in task.consumed_fact_ids:
            counts[fact_id] = counts.get(fact_id, 0) + 1
    reused = {fact_id: n for fact_id, n in counts.items() if n >= 2}
    shared_tasks = {
        fact_id: [
            task.task_id for task in tasks if fact_id in set(task.consumed_fact_ids)
        ]
        for fact_id in sorted(reused)
    }
    summary = {
        "facts_consumed_total": sum(counts.values()),
        "distinct_facts_consumed": len(counts),
        "facts_reused_ge2": len(reused),
        "shared_fact_ids": sorted(reused),
        "shared_facts_tasks": shared_tasks,
    }
    return reused, summary


def generate_tasks(
    world: SemanticWorld, budget: dict[str, int] | None = None
) -> list[TaskSpec]:
    """Structure-driven task generation over one SemanticWorld.

    The number per family comes from `budget` (keys: locate, aggregate,
    multi_hop, as_of_state; DEFAULT_BUDGET when omitted). See build_task_bank
    for the full bank with skips, rejections and reuse stats.
    """
    return list(build_task_bank(world, budget).tasks)


def build_task_bank(
    world: SemanticWorld, budget: dict[str, int] | None = None
) -> TaskBank:
    """The full bank: tasks, skipped families, rejections, reuse stats.

    Family skip rules (recorded, not silent):
    - locate: a family with no bindable object and no relation or attribute.
    - aggregate: no (relation, group-key, numeric-field) triple exists.
    - multi_hop: no versioned timeline whose resolved value starts a chain.
    - as_of_state: the world has no supersession or revocation at all.
    """
    # A partial budget zeroes the un-named families (an explicit request),
    # while NO budget at all uses DEFAULT_BUDGET.
    if budget is None:
        effective = dict(DEFAULT_BUDGET)
    else:
        effective = {key: int(budget.get(key, 0)) for key in CAPABILITY_FAMILIES}

    structure = analyze_structure(world)
    skipped: list[SkippedFamily] = []
    rejected: list[RejectedCandidate] = []
    executed: list[TaskSpec] = []
    as_of = _as_of(world, structure)

    # locate
    locate_candidates, _ = _locate_candidates(
        world, structure, 4 * effective["locate"] + 4
    )
    if not locate_candidates:
        skipped.append(
            SkippedFamily(
                "locate",
                "no bindable object with an entity relation or attribute",
            )
        )
    for candidate in locate_candidates:
        task, rej = _run_candidate(
            world, structure, "locate", candidate, require_fold_gate=False
        )
        if rej is not None:
            rejected.append(rej)
        elif task is not None:
            executed.append(task)
    locate_tasks = _select(
        [t for t in executed if t.family == "locate"], effective["locate"]
    )

    # aggregate
    executed = []
    aggregate_candidates, _ = _aggregate_candidates(
        world, structure, 4 * effective["aggregate"] + 4
    )
    if not aggregate_candidates:
        skipped.append(
            SkippedFamily(
                "aggregate",
                "no (relation, group key, numeric field) triple in structure",
            )
        )
    for candidate in aggregate_candidates:
        task, rej = _run_candidate(
            world, structure, "aggregate", candidate, require_fold_gate=False
        )
        if rej is not None:
            rejected.append(rej)
        elif task is not None:
            executed.append(task)
    aggregate_tasks = _select(executed, effective["aggregate"])

    # multi_hop
    executed = []
    multi_candidates, _ = _multi_hop_candidates(
        world, structure, 4 * effective["multi_hop"] + 4
    )
    if not multi_candidates:
        skipped.append(
            SkippedFamily(
                "multi_hop",
                "no versioned timeline resolving to an entity that starts a chain",
            )
        )
    for candidate in multi_candidates:
        task, rej = _run_candidate(
            world, structure, "multi_hop", candidate, require_fold_gate=True
        )
        if rej is not None:
            rejected.append(rej)
        elif task is not None:
            executed.append(task)
    multi_tasks = _select(executed, effective["multi_hop"])

    # as_of_state
    executed = []
    if not _versioned_timelines_of(world):
        skipped.append(
            SkippedFamily(
                "as_of_state", "no versioned facts (supersession or revocation)"
            )
        )
    else:
        asof_candidates, _ = _as_of_state_candidates(
            world, structure, 4 * effective["as_of_state"] + 4
        )
        for candidate in asof_candidates:
            task, rej = _run_candidate(
                world, structure, "as_of_state", candidate, require_fold_gate=False
            )
            if rej is not None:
                rejected.append(rej)
            elif task is not None:
                executed.append(task)
    asof_tasks = _select(executed, effective["as_of_state"])

    all_tasks = _assign_ids(
        locate_tasks + aggregate_tasks + multi_tasks + asof_tasks, "TB"
    )
    reuse, summary = _reuse_stats(all_tasks)
    rejected = rejected[:REJECTED_LOG_CAP]
    return TaskBank(
        tasks=all_tasks,
        skipped=tuple(skipped),
        rejected=tuple(rejected),
        reuse=reuse,
        reuse_summary=summary,
        structure={
            "families": list(structure.families),
            "family_sizes": {
                family: len(structure.families_by_label.get(family, ()))
                for family in structure.families
            },
            "entity_relations": {
                shape.relation: {
                    "from": shape.from_family,
                    "to": shape.to_family,
                    "fanout": [shape.fanout_min, shape.fanout_max],
                }
                for shape in structure.entity_relations
            },
            "attributes": {
                shape.relation: {
                    "family": shape.family,
                    "value_type": shape.value_type,
                    "distinct_values": shape.distinct_values,
                }
                for shape in structure.attributes
            },
            "versioned_relations": list(structure.versioned_relations),
            "dates": list(structure.dates),
        },
        budget=effective,
        as_of=as_of,
    )


def export_rows(bank: TaskBank) -> list[dict[str, Any]]:
    """Task rows for the batch chain: JSON-ready, one dict per task.

    The question is rendered from the family's single generic template
    (charter §1: the bank's variety claim is OPERATIONAL — distinct
    programs, answers, consumed facts — not phrasal).
    """
    return [task.to_dict() for task in bank.tasks]
