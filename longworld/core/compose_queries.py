"""Compose existing proofs into SearchArt-style width expansions.

Does not add events. Emits a small number of disjoint pairs (process-first)
plus at most one triple. Duplicate program hashes in one world are dropped.
JOIN is width, not the scale unit; first-class process queries carry novelty.
"""

from __future__ import annotations

from longworld.core.program_archive import is_novel
from longworld.core.proofprog import JOIN_SEP
from longworld.domains.company.queries import QuerySpec, instance_topology

# Process-bearing pairs first so the join cap does not fill with core×core.
_PAIRS = (
    ("current_state", "exception_scope"),
    ("current_state", "rollback_state"),
    ("current_state", "delayed_effect"),
    ("exception_scope", "multi_hop"),
    ("rollback_state", "multi_hop"),
    ("fork_join", "rollback_state"),
    ("fork_join", "delayed_effect"),
    ("delayed_effect", "hidden_bridge"),
    ("exception_scope", "delayed_effect"),
    ("contradiction", "exclusion"),
    ("fork_join", "exclusion"),
    ("current_state", "exclusion"),
    ("current_state", "hidden_bridge"),
    ("current_state", "fork_join"),
    ("version_diff", "multi_hop"),
    ("contradiction", "hidden_bridge"),
    ("fork_join", "hidden_bridge"),
    ("delayed_effect", "exclusion"),
    ("rollback_state", "hidden_bridge"),
    ("exclusion", "hidden_bridge"),
    ("current_state", "multi_hop"),
)

_TRIPLES = (
    ("current_state", "rollback_state", "multi_hop"),
    ("current_state", "fork_join", "hidden_bridge"),
    ("current_state", "multi_hop", "exception_scope"),
    ("fork_join", "delayed_effect", "hidden_bridge"),
    ("exclusion", "hidden_bridge", "fork_join"),
)

_MAX_JOINS = 3
_SKIP_JOIN_TYPES = {
    "historical_state",
    "aggregation",
    "program_join",
    "counterfactual",
    "compare_belief",
    "cross_stream",
    "source_grounded",
    "source_choice",
    "revisitation",
    "ratification",
    "docket_control",
}


def _ok_child(q: QuerySpec) -> bool:
    if "decoy" in q.query_id:
        return False
    if q.query_type in _SKIP_JOIN_TYPES:
        return False
    return not (not q.answer or q.answer == "unknown")


def _disjoint(parts: list[QuerySpec]) -> bool:
    sets = [set(q.essential_artifact_ids) for q in parts]
    for left_index, a in enumerate(sets):
        for b in sets[left_index + 1 :]:
            if a <= b or b <= a:
                return False
    union: list[str] = []
    seen: set[str] = set()
    for q in parts:
        for artifact_id in q.essential_artifact_ids:
            if artifact_id not in seen:
                seen.add(artifact_id)
                union.append(artifact_id)
    return len(union) >= 2 + len(parts)


def _join_spec(world, parts: list[QuerySpec]) -> QuerySpec | None:
    a = parts[0]
    union: list[str] = []
    evs: list[str] = []
    suf: list[str] = []
    seen_a: set[str] = set()
    seen_e: set[str] = set()
    for q in parts:
        for i in q.essential_artifact_ids:
            if i not in seen_a:
                seen_a.add(i)
                union.append(i)
        for i in q.essential_event_ids:
            if i not in seen_e:
                seen_e.add(i)
                evs.append(i)
        for i in q.sufficient_event_ids:
            if i not in suf:
                suf.append(i)
    types = sorted(q.query_type for q in parts)
    motifs = sorted((q.motif or q.query_type) for q in parts)
    ordered = sorted(parts, key=lambda q: q.query_type)
    ops = [
        {
            "op": "LOOKUP",
            "query_type": ordered[0].query_type,
            "answer_key": ordered[0].answer_key,
            "motif": ordered[0].motif,
        }
    ]
    for q in ordered[1:]:
        ops.append(
            {
                "op": "JOIN",
                "query_type": q.query_type,
                "answer_key": q.answer_key,
                "motif": q.motif,
            }
        )
    qid = a.query_id.rsplit(":", 1)[0] + ":join:" + "+".join(types)
    facts = "\n".join(f"Fact {i}: {q.question}" for i, q in enumerate(ordered, 1))
    spec = QuerySpec(
        query_id=qid,
        query_type="program_join",
        question=(
            f"{len(parts)} independent facts are required. Reply exactly as "
            f"{JOIN_SEP.join(f'<fact{i}>' for i in range(1, len(parts) + 1))} "
            f"with no extra text.\n{facts}"
        ),
        answer="",
        as_of=max((q.as_of for q in parts if q.as_of), default=a.as_of),
        answer_key=a.answer_key,
        essential_event_ids=evs,
        essential_artifact_ids=union,
        sufficient_event_ids=suf,
        cf_event_id=ordered[0].cf_event_id,
        cf_param_updates=dict(ordered[0].cf_param_updates),
        cf_answer="",
        invariance_event_id=None,
        invariance_param_updates={},
        gold_expression="JOIN(" + ",".join(q.gold_expression for q in ordered) + ")",
        proof_depth=max(q.proof_depth for q in parts) + len(parts) - 1,
        cf_op=ordered[0].cf_op,
        question_overrides=dict(ordered[0].question_overrides),
        motif="+".join(motifs),
        truth_regime=a.truth_regime,
        topology_id=instance_topology(
            f"{a.domain}.join." + "+".join(motifs),
            *[
                (q.topology_id.split(":")[-1] if q.topology_id else q.query_id)
                for q in parts
            ],
        ),
        domain=a.domain,
        program_ops=ops,
    )
    from longworld.core.engine import answer_from_events

    spec.answer = answer_from_events(world, spec, [e.id for e in world.events])
    spec.cf_answer = answer_from_events(
        world,
        spec,
        [e.id for e in world.events],
        extra_overrides={spec.cf_event_id: spec.cf_param_updates},
    )
    if (
        spec.answer
        and spec.answer != "unknown"
        and spec.answer.count(JOIN_SEP) == len(parts) - 1
        and spec.cf_answer
        and spec.cf_answer != spec.answer
    ):
        return spec
    return None


def compose_queries(world, queries: list[QuerySpec]) -> list[QuerySpec]:
    by_type = {q.query_type: q for q in queries if _ok_child(q)}
    out: list[QuerySpec] = []
    seen: set[str] = set()
    for ta, tb in _PAIRS:
        if len(out) >= _MAX_JOINS:
            break
        a, b = by_type.get(ta), by_type.get(tb)
        if a is None or b is None:
            continue
        if not _disjoint([a, b]):
            continue
        spec = _join_spec(world, [a, b])
        if spec is None:
            continue
        if not is_novel(spec, seen):
            continue
        out.append(spec)
    for types in _TRIPLES:
        if len(out) >= _MAX_JOINS:
            break
        parts = [by_type.get(t) for t in types]
        if any(p is None for p in parts):
            continue
        typed = [p for p in parts if p is not None]
        if not _disjoint(typed):
            continue
        spec = _join_spec(world, typed)
        if spec is None:
            continue
        if not is_novel(spec, seen):
            continue
        out.append(spec)
        break
    return out
