from __future__ import annotations

from typing import Any

from longworld.core.domain import eval_answer, handlers
from longworld.core.world import SimulatedWorld, WorldSimulator
from longworld.domains.company.queries import QuerySpec, _merge_overrides


def simulator(world: SimulatedWorld) -> WorldSimulator:
    check_preconditions, apply_event = handlers(world)
    return WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )


def revealed_event_ids(artifacts: list[Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for art in artifacts:
        for eid in art.reveals_events:
            if eid not in seen:
                seen.add(eid)
                ids.append(eid)
    return ids


def answer_from_events(
    world: SimulatedWorld,
    spec: QuerySpec,
    event_ids: list[str] | set[str],
    extra_overrides: dict[str, dict[str, Any]] | None = None,
    skip_ids: set[str] | None = None,
    enforce_preconditions: bool = False,
) -> str:
    allowed = set(event_ids)
    events = [e for e in world.events if e.id in allowed]
    st = simulator(world).replay_events(
        events,
        up_to=spec.as_of,
        skip_ids=skip_ids,
        param_overrides=_merge_overrides(spec, extra_overrides),
        enforce_preconditions=enforce_preconditions,
    )
    return eval_answer(world, spec, st.values)


def answer_from_artifacts(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Any],
    extra_overrides: dict[str, dict[str, Any]] | None = None,
    skip_ids: set[str] | None = None,
) -> str:
    return answer_from_events(
        world, spec, revealed_event_ids(artifacts), extra_overrides, skip_ids
    )
