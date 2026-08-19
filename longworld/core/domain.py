"""Dispatch simulator / eval / render by world.spec['domain']."""

from __future__ import annotations

from typing import Any

from longworld.core.world import SimulatedWorld

DOMAINS = ("company", "researchlab", "codeforge")


def domain_name(world_or_spec: Any) -> str:
    if isinstance(world_or_spec, SimulatedWorld):
        return str(world_or_spec.spec.get("domain") or "company")
    if isinstance(world_or_spec, dict):
        return str(world_or_spec.get("domain") or "company")
    return "company"


def handlers(world: SimulatedWorld):
    name = domain_name(world)
    if name == "researchlab":
        from longworld.domains.researchlab.events import (
            apply_event,
            check_preconditions,
        )
    elif name == "codeforge":
        from longworld.domains.codeforge.events import apply_event, check_preconditions
    else:
        from longworld.domains.company.events import apply_event, check_preconditions
    return check_preconditions, apply_event


def eval_answer(world: SimulatedWorld, spec, values: dict[str, Any]) -> str:
    name = domain_name(world)
    if name == "researchlab":
        from longworld.domains.researchlab.queries import eval_answer as fn
    elif name == "codeforge":
        from longworld.domains.codeforge.queries import eval_answer as fn
    else:
        from longworld.domains.company.queries import eval_answer as fn
    return fn(world, spec, values)
