"""P74 dependency operators: bound-variable programs over a SemanticWorld.

The T2 half of the P74 charter (.hl/design/p74_real_shared_worlds.md §5).
Programs are LET-chains over the seven charter operators — bind,
follow_relation, resolve_version, apply_rule, join_on_bound_result, group,
aggregate — where each step's query key, rule or candidate set may be a
variable BOUND by a preceding step. Two compile-time gates keep that honest:

- the dependency invariant: every step after the first must consume at least
  one upstream binding, so no step is a free-floating constant filter;
- the non-foldability gate: at least one step must take a *key* parameter (its
  query key, rule or version selector) from a COMPUTED upstream binding — one
  produced by state resolution, rule application, or another consuming step.
  A program whose every key is a literal folds to a flat AND of constants and
  is rejected even when it passes the invariant.

Execution produces an answer plus a PROOF: the ordered (step, consumed facts,
supporting spans) lineage. Spans are the rendered-text evidence the charter
§6 binds facts to — never generator internals, never hidden state. Interventions
mutate one fact's value in the frozen documents (text edits with span-offset
repair) and re-execute: an upstream mutation must change the answer, an
unrelated one must not.

Four quantities are reported separately, per charter §5: program_length,
constant_params, dependency_depth (longest def-use chain) and
state_transition_depth (state-resolving steps). No claim about algorithmic
time lower bounds is made or implied.

Simulated data only; CPU-tiny; standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from longworld.synthesis.shared_semantic_world import (
    Document,
    Entity,
    EntityMention,
    Fact,
    ScopeEntry,
    SemanticWorld,
    SpanRef,
)

OPS = (
    "bind",
    "follow_relation",
    "resolve_version",
    "apply_rule",
    "join_on_bound_result",
    "group",
    "aggregate",
)

# Which parameters are query KEYS (they select what is looked up), which are
# DATA (candidate sets / payloads), and which are literal META (how, at, ...).
# The non-foldability gate reads KEYS only: a data-position binding alone is a
# renamed constant set, not a dependency.
PARAM_ROLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bind": (("family", "label", "entity_id", "subject", "relation", "version"), ()),
    "follow_relation": (("subject", "relation"), ()),
    "resolve_version": (("subject", "relation", "at"), ()),
    "apply_rule": (("rule",), ("over", "at")),
    "join_on_bound_result": (("left", "relation", "status_relation"), ("at",)),
    "group": (("key",), ("items",)),
    "aggregate": (("how", "field", "compare"), ("groups",)),
}
_ALLOWED = {
    "bind": {
        "op",
        "out",
        "family",
        "label",
        "entity_id",
        "subject",
        "relation",
        "version",
    },
    "follow_relation": {"op", "out", "subject", "relation"},
    "resolve_version": {"op", "out", "subject", "relation", "at"},
    "apply_rule": {"op", "out", "rule", "over", "at"},
    "join_on_bound_result": {
        "op",
        "out",
        "left",
        "relation",
        "status_relation",
        "at",
        "require_active",
    },
    "group": {"op", "out", "items", "key"},
    "aggregate": {"op", "out", "groups", "how", "field", "compare"},
}
# Ops whose execution resolves world STATE, not just lookups: they feed the
# state_transition_depth metric and make a binding "computed".
_STATE_OPS = ("resolve_version",)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("$")


def _literal_params(step: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        (name, value)
        for name, value in step.items()
        if name not in ("op", "out") and value is not None and not _is_ref(value)
    ]


def _ref_params(step: dict[str, Any]) -> list[str]:
    return [name for name, value in step.items() if _is_ref(value)]


def _validate_shape(program: dict[str, Any]) -> list[dict[str, Any]]:
    _require(
        isinstance(program, dict) and isinstance(program.get("steps"), list),
        "program must be an object with a steps list",
    )
    steps = program["steps"]
    _require(2 <= len(steps) <= 12, "a chain needs 2 to 12 steps")
    defined: set[str] = set()
    for index, step in enumerate(steps):
        _require(
            isinstance(step, dict) and step.get("op") in OPS,
            f"step {index} has an unknown op",
        )
        unknown = set(step) - _ALLOWED[step["op"]]
        _require(
            not unknown,
            f"step {index} ({step['op']}) has unknown keys {sorted(unknown)}",
        )
        out = step.get("out")
        _require(
            isinstance(out, str) and out and not out.startswith("$"),
            f"step {index} needs a plain out name",
        )
        _require(out not in defined, f"step {index} rebinds ${out}")
        for name in _ref_params(step):
            _require(name != "out", f"step {index} cannot bind $ as a name")
            _require(
                step[name][1:] in defined,
                f"step {index} consumes ${step[name][1:]} before it is bound",
            )
        defined.add(out)
    _require(
        program.get("return") in defined,
        f"program return {program.get('return')!r} is not a bound variable",
    )
    return steps


def _computed_bindings(steps: list[dict[str, Any]]) -> dict[str, bool]:
    """Which variables carry a COMPUTED value, not a renamed constant.

    A binding is computed when its step resolves state (resolve_version, or a
    join that checks a status timeline) or itself consumes a computed binding.
    Bindings from literal-only steps are renamed constants.
    """
    computed: dict[str, bool] = {}
    for step in steps:
        resolves_state = step["op"] in _STATE_OPS or (
            step["op"] == "join_on_bound_result" and step.get("status_relation")
        )
        consumed_computed = any(
            _is_ref(step[name]) and computed.get(step[name][1:]) for name in step
        )
        computed[step["out"]] = resolves_state or consumed_computed
    return computed


def _dependency_depths(steps: list[dict[str, Any]]) -> dict[str, int]:
    depths: dict[str, int] = {}
    for step in steps:
        consumed = [depths[step[name][1:]] for name in step if _is_ref(step[name])]
        depths[step["out"]] = 1 + max(consumed, default=0)
    return depths


def compile_program(
    program: dict[str, Any], require_fold_gate: bool = True
) -> dict[str, Any]:
    """Compile-time constant-dependency analysis; rejects foldable programs.

    Returns the four charter §5 quantities separately. Raises ValueError when
    the dependency invariant fails (always) or the non-foldability gate fails
    (only with require_fold_gate=True: the H-chain contract; a plain locate
    task legitimately compiles as a constant program, so its execution path
    passes require_fold_gate=False).
    """
    steps = _validate_shape(program)
    for index, step in enumerate(steps[1:], start=1):
        _require(
            _ref_params(step),
            f"step {index} ({step['op']}) consumes no upstream binding: every "
            "step after the first must take a key, rule or candidate set from "
            "a preceding step",
        )
    computed = _computed_bindings(steps)
    key_bindings = []
    for index, step in enumerate(steps):
        keys, _ = PARAM_ROLES[step["op"]]
        for name in keys:
            if _is_ref(step.get(name)) and computed.get(step[name][1:]):
                key_bindings.append(
                    {"step": index, "op": step["op"], "param": name, "var": step[name]}
                )
    if require_fold_gate:
        _require(
            key_bindings,
            "program folds to a flat AND of constants: no step takes a query key, "
            "rule or version selector from a computed upstream binding",
        )
    depths = _dependency_depths(steps)
    return {
        "program_length": len(steps),
        "constant_params": sum(len(_literal_params(step)) for step in steps),
        "dependency_depth": max(depths.values()),
        "state_transition_depth": sum(
            1
            for step in steps
            if step["op"] in _STATE_OPS
            or (step["op"] == "join_on_bound_result" and step.get("status_relation"))
        ),
        "key_bindings": key_bindings,
        "non_foldable": bool(key_bindings),
    }


@dataclass(frozen=True)
class ProofItem:
    """One lineage entry: what a step consumed, and where the text says it."""

    step: int
    op: str
    out: str
    kind: str  # "fact" or "entity"
    ref_id: str
    subject: str
    relation: str | None
    value: Any
    spans: tuple[SpanRef, ...]
    span_texts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "op": self.op,
            "out": self.out,
            "kind": self.kind,
            "ref_id": self.ref_id,
            "subject": self.subject,
            "relation": self.relation,
            "value": self.value,
            "spans": [span.to_dict() for span in self.spans],
            "span_texts": list(self.span_texts),
        }


@dataclass(frozen=True)
class ExecutionResult:
    answer: Any
    proof: tuple[ProofItem, ...]
    consumed_fact_ids: tuple[str, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "proof": [item.to_dict() for item in self.proof],
            "consumed_fact_ids": list(self.consumed_fact_ids),
            "metrics": self.metrics,
        }


def _span_texts(world: SemanticWorld, spans: tuple[SpanRef, ...]) -> tuple[str, ...]:
    docs = {doc.doc_id: doc for doc in world.documents}
    return tuple(docs[span.doc_id].text[span.start : span.end] for span in spans)


def _fact_item(
    world: SemanticWorld, step_index: int, step: dict[str, Any], fact: Fact
) -> ProofItem:
    return ProofItem(
        step=step_index,
        op=step["op"],
        out=step["out"],
        kind="fact",
        ref_id=fact.fact_id,
        subject=fact.subject,
        relation=fact.relation,
        value=fact.value,
        spans=fact.supporting_spans,
        span_texts=_span_texts(world, fact.supporting_spans),
    )


def _entity_item(
    world: SemanticWorld, step_index: int, step: dict[str, Any], entity: Entity
) -> ProofItem:
    spans = (
        tuple(
            SpanRef(doc_id=entity.doc_id, start=m.start, end=m.end)
            for m in entity.mentions
        )
        if entity.doc_id is not None
        else ()
    )
    return ProofItem(
        step=step_index,
        op=step["op"],
        out=step["out"],
        kind="entity",
        ref_id=entity.entity_id,
        subject=entity.entity_id,
        relation=None,
        value=entity.label,
        spans=spans,
        span_texts=_span_texts(world, spans),
    )


def _parse_rule(rule: str) -> tuple[str, str, Any]:
    parts = rule.split()
    _require(len(parts) == 3, f"rule {rule!r} is not 'field op threshold'")
    field, op, threshold = parts
    _require(
        op in ("<=", ">=", "==", "!=", "<", ">"),
        f"rule {rule!r} has an unsupported comparison",
    )
    number = float(threshold)
    return field, op, int(number) if number.is_integer() else number


def _holds(value: Any, op: str, threshold: Any) -> bool:
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "==":
        return value == threshold
    if op == "!=":
        return value != threshold
    if op == "<":
        return value < threshold
    return value > threshold


def execute(
    world: SemanticWorld, program: dict[str, Any], require_fold_gate: bool = True
) -> ExecutionResult:
    """Run one compiled program; the answer plus its span-lineage proof."""
    metrics = compile_program(program, require_fold_gate=require_fold_gate)
    env: dict[str, Any] = {}
    proof: list[ProofItem] = []
    steps = program["steps"]

    def bound(step: dict[str, Any], name: str) -> Any:
        value = step[name]
        return env[value[1:]] if _is_ref(value) else value

    for index, step in enumerate(steps):
        op = step["op"]
        if op == "bind":
            if step.get("entity_id") or step.get("label"):
                entity = world.bind_entity(
                    family=step.get("family"),
                    label=step.get("label"),
                    entity_id=step.get("entity_id"),
                )
                proof.append(_entity_item(world, index, step, entity))
                env[step["out"]] = entity.entity_id
            else:
                fact = world.lookup_fact(
                    subject=bound(step, "subject"),
                    relation=bound(step, "relation"),
                    version=bound(step, "version") if step.get("version") else None,
                )
                proof.append(_fact_item(world, index, step, fact))
                env[step["out"]] = fact.value
        elif op == "follow_relation":
            subject = bound(step, "subject")
            facts = world.follow(subject, bound(step, "relation"))
            _require(bool(facts), f"follow_relation found no edges from {subject}")
            proof.extend(_fact_item(world, index, step, fact) for fact in facts)
            env[step["out"]] = tuple(fact.value for fact in facts)
        elif op == "resolve_version":
            subject = bound(step, "subject")
            entry = world.resolve_at(
                subject, bound(step, "relation"), bound(step, "at")
            )
            # The superseded entries are why the winner wins: their facts stay
            # in the proof so a revoked_at mutation shows up as a lineage hit.
            for fact in world.facts:
                if fact.subject == subject and fact.relation == step["relation"]:
                    proof.append(_fact_item(world, index, step, fact))
            env[step["out"]] = entry.value
        elif op == "apply_rule":
            rule = bound(step, "rule")
            _require(isinstance(rule, str), "apply_rule needs a rule string")
            candidates = bound(step, "over")
            _require(
                isinstance(candidates, tuple),
                "apply_rule needs a bound candidate set (a tuple of object ids)",
            )
            field, comparison, threshold = _parse_rule(rule)
            kept = []
            for candidate in candidates:
                if step.get("at"):
                    entry = world.resolve_at(candidate, field, bound(step, "at"))
                    fact = world.facts_by_id[entry.fact_id]
                else:
                    fact = world.lookup_fact(candidate, field)
                proof.append(_fact_item(world, index, step, fact))
                if _holds(fact.value, comparison, threshold):
                    kept.append(candidate)
            env[step["out"]] = tuple(kept)
        elif op == "join_on_bound_result":
            left = bound(step, "left")
            _require(
                isinstance(left, tuple),
                "join_on_bound_result needs a bound left set",
            )
            joined = []
            for obj in left:
                for fact in world.follow(obj, step["relation"]):
                    proof.append(_fact_item(world, index, step, fact))
                    target = fact.value
                    if step.get("status_relation"):
                        status = world.status_at(
                            target, step["status_relation"], bound(step, "at")
                        )
                        if status is None:
                            # A timeline that has nothing active at `at` is a
                            # legal state: the pair drops out of the join.
                            continue
                        winner = world.facts_by_id[status.fact_id]
                        proof.append(_fact_item(world, index, step, winner))
                        if step.get("require_active") and status.value != "active":
                            continue
                    joined.append(target)
            _require(bool(joined), "join_on_bound_result matched nothing")
            env[step["out"]] = tuple(joined)
        elif op == "group":
            items = bound(step, "items")
            _require(isinstance(items, tuple), "group needs a bound item set")
            key = step["key"]
            buckets: dict[str, list[str]] = {}
            for item in items:
                fact = world.lookup_fact(item, key)
                proof.append(_fact_item(world, index, step, fact))
                label = (
                    world.label_of(fact.value)
                    if fact.value_type == "entity"
                    else str(fact.value)
                )
                buckets.setdefault(label, []).append(item)
            env[step["out"]] = {
                label: tuple(buckets[label]) for label in sorted(buckets)
            }
        else:  # aggregate
            groups = bound(step, "groups")
            _require(
                isinstance(groups, dict), "aggregate needs bound groups (label -> ids)"
            )
            how = step["how"]
            field = step.get("field")
            values: dict[str, Any] = {}
            members_by_group: dict[str, list[str]] = {}
            for label, members in groups.items():
                collected = []
                for member in members:
                    fact = world.lookup_fact(member, field)
                    proof.append(_fact_item(world, index, step, fact))
                    _require(
                        isinstance(fact.value, (int, float)),
                        f"aggregate field {field} is not numeric",
                    )
                    collected.append(fact.value)
                _require(bool(collected), f"aggregate group {label} is empty")
                if how == "sum":
                    values[label] = sum(collected)
                elif how == "count":
                    values[label] = len(collected)
                elif how == "max":
                    values[label] = max(collected)
                elif how == "min":
                    values[label] = min(collected)
                else:
                    raise ValueError(f"unsupported aggregate {how}")
                members_by_group[label] = list(members)
            verdict = None
            if step.get("compare"):
                _require(
                    len(values) == 2,
                    "compare needs exactly two groups",
                )
                left_label, right_label = sorted(values)
                verdict = (
                    "GT"
                    if values[left_label] > values[right_label]
                    else "LT"
                    if values[left_label] < values[right_label]
                    else "EQ"
                )
            env[step["out"]] = {
                "how": how,
                "field": field,
                "groups": values,
                "items": members_by_group,
                "verdict": verdict,
            }
    consumed = tuple(sorted({item.ref_id for item in proof if item.kind == "fact"}))
    return ExecutionResult(
        answer=env[program["return"]],
        proof=tuple(proof),
        consumed_fact_ids=consumed,
        metrics=metrics,
    )


# --- instruction rendering (the task layer's visible question) ---


def describe_program(program: dict[str, Any]) -> str:
    steps = _validate_shape(program)
    parts = []
    for step in steps:
        op = step["op"]
        out = step["out"]
        if op == "bind":
            if step.get("label"):
                parts.append(
                    f"LET {out} = the {step.get('family')} object labeled {step['label']!r}"
                )
            else:
                version = (
                    f" at version {step['version']}" if step.get("version") else ""
                )
                parts.append(
                    f"LET {out} = the {step['relation']} of {step['subject']}{version}"
                )
        elif op == "follow_relation":
            parts.append(
                f"LET {out} = the objects reachable by {step['relation']} from {step['subject']}"
            )
        elif op == "resolve_version":
            parts.append(
                f"LET {out} = the value of {step['relation']} for {step['subject']} "
                f"effective as of {step['at']}"
            )
        elif op == "apply_rule":
            asof = f" as of {step['at']}" if step.get("at") else ""
            parts.append(
                f"LET {out} = the members of {step['over']} satisfying the rule {step['rule']}{asof}"
            )
        elif op == "join_on_bound_result":
            status = ""
            if step.get("status_relation"):
                active = "active" if step.get("require_active", True) else "present"
                status = (
                    f", keeping only those whose {step['status_relation']} is "
                    f"{active} at {step.get('at')}"
                )
            parts.append(
                f"LET {out} = the objects joined to {step['left']} by {step['relation']}{status}"
            )
        elif op == "group":
            parts.append(f"LET {out} = {step['items']} grouped by {step['key']}")
        else:
            field = f" of {step['field']}" if step.get("field") else ""
            compare = " and compare the two groups" if step.get("compare") else ""
            parts.append(
                f"LET {out} = the {step['how']}{field} within each group of {step['groups']}{compare}"
            )
    return "; ".join(parts) + f". RETURN {program['return']}."


def render_instruction(
    task_id: str, program: dict[str, Any], scope: ScopeEntry, goal: str
) -> str:
    return (
        f"Task {task_id}: {goal} Program: {describe_program(program)} "
        f"Scope: object families {sorted(scope.object_families)}; relations "
        f"{sorted(scope.relations)}; documents {sorted(scope.documents)}. "
        "Answer only from the documents in scope."
    )


def make_task(
    world: SemanticWorld,
    task_id: str,
    capability: str,
    program: dict[str, Any],
    scope: ScopeEntry,
    goal: str,
) -> dict[str, Any]:
    """One task row over the shared world: program, scope, executed gold, proof."""
    result = execute(world, program)
    return {
        "task_id": task_id,
        "capability": capability,
        "goal": goal,
        "question": program,
        "instruction": render_instruction(task_id, program, scope, goal),
        "scope": scope.to_dict(),
        "answer": result.answer,
        "proof": [item.to_dict() for item in result.proof],
        "consumed_fact_ids": list(result.consumed_fact_ids),
        "metrics": result.metrics,
        "honesty": {
            "source_kind": "simulated",
            "span_lineage": True,
            "alternative_proofs_checked": False,
            "long_span_requirement_verified": False,
        },
    }


# --- interventions: mutate one fact in the frozen documents, re-execute ---


@dataclass(frozen=True)
class _TextEdit:
    doc_id: str
    start: int
    old_end: int
    new_text: str


def _edits_for(
    world: SemanticWorld, fact: Fact, old_text: str, new_text: str
) -> list[_TextEdit]:
    edits = []
    for span in fact.supporting_spans:
        doc = next(d for d in world.documents if d.doc_id == span.doc_id)
        offset = doc.text[span.start : span.end].find(old_text)
        _require(
            offset >= 0,
            f"fact {fact.fact_id} span in {span.doc_id} does not carry {old_text!r}",
        )
        edits.append(
            _TextEdit(
                span.doc_id,
                span.start + offset,
                span.start + offset + len(old_text),
                new_text,
            )
        )
    return edits


def _map_position(offset: int, edits: list[_TextEdit]) -> int:
    """One original-text position to its position in the edited text.

    Positions inside an edit region map to its end (edits replace the fact's
    own evidence, so a foreign span pointing INTO the old evidence still
    points at the region as a whole); positions after all edits shift by the
    accumulated length delta. One rule, no double counting.
    """
    for edit in edits:
        if offset >= edit.old_end:
            offset += len(edit.new_text) - (edit.old_end - edit.start)
        elif offset > edit.start:
            return edit.start + len(edit.new_text)
    return offset


def _remap_spans(
    spans: tuple[SpanRef, ...], edits: list[_TextEdit]
) -> tuple[SpanRef, ...]:
    remapped = []
    for span in spans:
        doc_edits = sorted(
            (edit for edit in edits if edit.doc_id == span.doc_id),
            key=lambda edit: edit.start,
        )
        remapped.append(
            SpanRef(
                span.doc_id,
                _map_position(span.start, doc_edits),
                _map_position(span.end, doc_edits),
            )
        )
    return tuple(remapped)


def _mutated_world(
    world: SemanticWorld, fact: Fact, new_value: Any, old_text: str, new_text: str
) -> SemanticWorld:
    edits = _edits_for(world, fact, old_text, new_text)
    documents = []
    for doc in world.documents:
        doc_edits = sorted(
            (edit for edit in edits if edit.doc_id == doc.doc_id),
            key=lambda edit: edit.start,
        )
        if not doc_edits:
            documents.append(doc)
            continue
        text = doc.text
        for edit in reversed(doc_edits):
            text = text[: edit.start] + edit.new_text + text[edit.old_end :]
        documents.append(Document(doc.doc_id, doc.title, text, doc.sections))
    facts = []
    for other in world.facts:
        values: dict[str, Any] = {
            "fact_id": other.fact_id,
            "subject": other.subject,
            "relation": other.relation,
            "value": other.value,
            "value_type": other.value_type,
            "unit": other.unit,
            "time": other.time,
            "version": other.version,
            "qualifiers": dict(other.qualifiers),
            "supporting_spans": _remap_spans(other.supporting_spans, edits),
            "alternative_spans": _remap_spans(other.alternative_spans, edits),
            "source_hash": other.source_hash,
        }
        if other.fact_id == fact.fact_id:
            values["value"] = new_value
        facts.append(Fact(**values))
    entities = []
    for entity in world.entities:
        mentions = ()
        if entity.doc_id is not None:
            doc_edits = sorted(
                (e for e in edits if e.doc_id == entity.doc_id),
                key=lambda e: e.start,
            )
            mentions = tuple(
                EntityMention(
                    _map_position(mention.start, doc_edits),
                    _map_position(mention.end, doc_edits),
                )
                for mention in entity.mentions
            )
        entities.append(
            Entity(
                entity.entity_id,
                entity.label,
                entity.aliases,
                entity.external_qid,
                entity.doc_id,
                mentions,
                entity.entity_type,
            )
        )
    return SemanticWorld(tuple(documents), tuple(entities), tuple(facts))


def mutate_fact(world: SemanticWorld, fact_id: str, new_value: Any) -> SemanticWorld:
    """Replace one scalar/string/rule fact's value, editing the frozen text.

    The counterfactual is a simulated copy (charter §9): the document text is
    edited at the fact's supporting spans, every other span and mention in the
    affected documents is re-offset, and the mutated world re-validates its
    fact->span integrity on construction.
    """
    fact = world.facts_by_id.get(fact_id)
    _require(fact is not None, f"unknown fact {fact_id}")
    fact = world.facts_by_id[fact_id]
    _require(
        fact.value_type != "entity",
        "entity-valued facts are relations; use mutate_relation",
    )
    old_text, new_text = str(fact.value), str(new_value)
    _require(old_text != new_text, "mutation must change the value")
    return _mutated_world(world, fact, new_value, old_text, new_text)


def mutate_relation(
    world: SemanticWorld, fact_id: str, new_object: str
) -> SemanticWorld:
    """Repoint one entity-valued fact at a different object, editing the text."""
    fact = world.facts_by_id.get(fact_id)
    _require(fact is not None, f"unknown fact {fact_id}")
    fact = world.facts_by_id[fact_id]
    _require(
        fact.value_type == "entity" and fact.value != new_object,
        "mutate_relation needs an entity-valued fact and a different target",
    )
    _require(new_object in world.objects, f"unknown entity {new_object}")
    return _mutated_world(
        world,
        fact,
        new_object,
        world.label_of(fact.value),
        world.label_of(new_object),
    )


def check_intervention(
    world: SemanticWorld,
    program: dict[str, Any],
    fact_id: str,
    new_value: Any,
    relation: bool = False,
) -> dict[str, Any]:
    """Dependence certificate v0: mutate one fact, re-execute, compare."""
    base = execute(world, program)
    mutated = (
        mutate_relation(world, fact_id, new_value)
        if relation
        else mutate_fact(world, fact_id, new_value)
    )
    after = execute(mutated, program)
    return {
        "fact_id": fact_id,
        "mutation": {
            "new_value": new_value,
            "kind": "relation" if relation else "fact",
        },
        "base_answer": base.answer,
        "mutated_answer": after.answer,
        "answer_changed": base.answer != after.answer,
        "fact_in_proof": fact_id in set(base.consumed_fact_ids),
    }
