"""Artifact predecessor graph from event causal_inputs.

Used by the packer as task-bank support, not as a second event script.
"""

from __future__ import annotations

from longworld.core.render import Artifact
from longworld.core.world import SimulatedWorld


def artifact_index(artifacts: list[Artifact]) -> dict[str, Artifact]:
    return {a.artifact_id: a for a in artifacts}


def event_to_artifacts(artifacts: list[Artifact]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in artifacts:
        for eid in a.reveals_events:
            out.setdefault(eid, []).append(a.artifact_id)
    return out


def predecessor_ids(
    world: SimulatedWorld,
    artifacts: list[Artifact],
    essential_ids: list[str],
) -> set[str]:
    """Artifacts that reveal causal parents of essential events."""
    by_event = event_to_artifacts(artifacts)
    events = {e.id: e for e in world.events}
    ess_events: set[str] = set()
    index = artifact_index(artifacts)
    for aid in essential_ids:
        art = index.get(aid)
        if art is None:
            continue
        ess_events.update(art.reveals_events)
    parents: set[str] = set()
    for eid in ess_events:
        ev = events.get(eid)
        if ev is None:
            continue
        for p in ev.causal_inputs:
            parents.update(by_event.get(p) or [])
    parents -= set(essential_ids)
    return parents


def attach_dag_slots(
    world: SimulatedWorld, artifacts: list[Artifact]
) -> list[Artifact]:
    by_event = event_to_artifacts(artifacts)
    events = {e.id: e for e in world.events}
    for a in artifacts:
        preds: list[str] = []
        seen: set[str] = set()
        for eid in a.reveals_events:
            ev = events.get(eid)
            if ev is None:
                continue
            for p in ev.causal_inputs:
                for aid in by_event.get(p) or []:
                    if aid not in seen and aid != a.artifact_id:
                        seen.add(aid)
                        preds.append(aid)
        plan = dict((a.slots or {}).get("content_plan") or {})
        plan["predecessors"] = preds
        a.slots = {**(a.slots or {}), "content_plan": plan}
    return artifacts
