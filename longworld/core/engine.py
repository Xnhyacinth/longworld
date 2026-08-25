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


def revealed_event_ids(
    artifacts: list[Any], world: SimulatedWorld | None = None
) -> list[str]:
    """Collect event ids revealed by artifacts that belong to ``world``.

    Extra-world filler reuses local names like ``focal.change_roadmap``.
    Replaying those ids on the query world would fake a shorter proof.
    """
    wid = ""
    if world is not None:
        wid = str(world.spec.get("world_id") or "")
    ids: list[str] = []
    seen: set[str] = set()
    for art in artifacts:
        if wid and not str(getattr(art, "artifact_id", "")).startswith(wid):
            continue
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
    cached = getattr(world, "_longworld_event_index", None)
    if cached is None or cached[0] is not world.events:
        cached = (world.events, {event.id: event for event in world.events})
        world._longworld_event_index = cached
    event_index = cached[1]
    events = [event_index[event_id] for event_id in allowed if event_id in event_index]
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
    enforce_preconditions: bool = False,
) -> str:
    return answer_from_events(
        world,
        spec,
        revealed_event_ids(artifacts, world),
        extra_overrides,
        skip_ids,
        enforce_preconditions,
    )
