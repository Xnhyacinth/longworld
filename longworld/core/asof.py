"""Temporal cut for core vs process-extension queries."""

from __future__ import annotations

from datetime import date, timedelta

from longworld.core.process import EXTENSION_TYPES
from longworld.core.world import Event, SimulatedWorld


def core_as_of(world: SimulatedWorld) -> date:
    """Latest core timestamp strictly before rollback / invalidate.

    Carve-out is an extension but must not clip later core finance / legal
    writes. Rollback and invalidate overwrite HEAD / score, so core queries
    (current_state, multi_hop) stop before those events. Later non-extension
    events (ack / reopen / ratify) must not pull the cut past rollback.
    """
    cut_types = frozenset({"rollback_amendment", "rollback_hotfix", "invalidate_run"})
    cuts = [e.time for e in world.events if e.type in cut_types]
    times = [e.time for e in world.events if e.type not in EXTENSION_TYPES]
    if cuts:
        bound = min(cuts)
        times = [t for t in times if t < bound]
    if times:
        return max(times)
    if cuts:
        return min(cuts) - timedelta(days=1)
    return max(e.time for e in world.events)


def world_as_of(world: SimulatedWorld) -> date:
    return max(e.time for e in world.events)


def day_before(when: date) -> date:
    return when - timedelta(days=1)


def find_event(world: SimulatedWorld, suffix: str) -> Event | None:
    eid = f"{world.spec['prefix']}.{suffix}"
    for e in world.events:
        if e.id == eid:
            return e
    return None
