from __future__ import annotations

from longworld.core.engine import answer_from_events
from longworld.core.sampler import materialize


class _CountingEvents(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


def test_repeated_replay_reuses_world_event_index() -> None:
    materialized = materialize(1, n_parallel=0, domain="company")
    world = materialized.worlds["focal"]
    spec = next(query for query in materialized.queries if query.answer)
    world.events = _CountingEvents(world.events)
    if hasattr(world, "_longworld_event_index"):
        delattr(world, "_longworld_event_index")

    answer_from_events(world, spec, spec.sufficient_event_ids)
    first_iterations = world.events.iterations
    answer_from_events(world, spec, spec.sufficient_event_ids)

    assert first_iterations > 0
    assert world.events.iterations == first_iterations
