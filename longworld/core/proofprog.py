"""Proof-program atoms and JOIN evaluation.

Inspired by SearchArt's depth×width subgraph sampling, but compiled against
an executable world rather than a web evidence graph. Atoms are evaluated
by the domain `eval_answer` on a replayed state — no LLM judge.
"""

from __future__ import annotations

from typing import Any

from longworld.domains.company.queries import QuerySpec

JOIN_SEP = " || "

# SearchArt-style operators we can actually execute today.
ATOMS = (
    "LOOKUP",
    "JOIN",
    "COMPARE",
    "RESOLVE_VERSION",
    "APPLY_EXCEPTION",
    "FOLLOW",
)


def eval_program(world, spec: QuerySpec, values: dict[str, Any]) -> str:
    """Evaluate a `program_join` spec as LOOKUP steps joined by JOIN_SEP."""
    if spec.query_type != "program_join" or not spec.program_ops:
        return "unknown"
    name = str(world.spec.get("domain") or "company")
    if name == "researchlab":
        from longworld.domains.researchlab.queries import eval_answer as fn
    elif name == "codeforge":
        from longworld.domains.codeforge.queries import eval_answer as fn
    else:
        from longworld.domains.company.queries import eval_answer as fn
    parts: list[str] = []
    for step in spec.program_ops:
        sub = QuerySpec(
            query_id=f"{spec.query_id}:{step.get('op', 'LOOKUP')}",
            query_type=str(step.get("query_type") or "current_state"),
            question="",
            answer="",
            as_of=spec.as_of,
            answer_key=str(step.get("answer_key") or spec.answer_key),
            essential_event_ids=[],
            essential_artifact_ids=[],
            sufficient_event_ids=[],
            cf_event_id=spec.cf_event_id,
            cf_param_updates={},
            cf_answer="",
            invariance_event_id=None,
        )
        part = fn(world, sub, values)
        if not part or part == "unknown":
            return "unknown"
        parts.append(part)
    return JOIN_SEP.join(parts)
