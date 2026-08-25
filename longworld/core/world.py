from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from longworld.core.state import WorldState, replay


@dataclass
class Event:
    id: str
    type: str
    time: date
    params: dict[str, Any]
    visibility: list[str]
    preconditions: list[str] = field(default_factory=list)
    causal_inputs: list[str] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    relation_kinds: dict[str, str] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class SimulatedWorld:
    world_id: str
    seed: int
    schema_version: str
    spec: dict[str, Any]
    state: WorldState
    events: list[Event]
    init_values: dict[str, Any]


class WorldSimulator:
    def __init__(
        self,
        spec: dict[str, Any],
        init_values: dict[str, Any],
        check_preconditions: Callable[[WorldState, Event], tuple[bool, str | None]],
        apply_event: Callable[[WorldState, Event], None],
    ) -> None:
        self.spec = spec
        self.init_values = dict(init_values)
        self.check_preconditions = check_preconditions
        self.apply_event = apply_event

    def run(self, events: list[Event]) -> SimulatedWorld:
        state = WorldState(values=dict(self.init_values))
        applied: list[Event] = []
        applied_ids: set[str] = set()
        for ev in events:
            if any(parent not in applied_ids for parent in ev.required_inputs):
                ev.skipped = True
                ev.skip_reason = "required_input_missing"
                applied.append(ev)
                continue
            ok, reason = self.check_preconditions(state, ev)
            if not ok:
                ev.skipped = True
                ev.skip_reason = reason
                applied.append(ev)
                continue
            self.apply_event(state, ev)
            applied_ids.add(ev.id)
            applied.append(ev)
        return SimulatedWorld(
            world_id=self.spec["world_id"],
            seed=int(self.spec["seed"]),
            schema_version=self.spec.get("schema_version", "p0.1"),
            spec=self.spec,
            state=state,
            events=applied,
            init_values=self.init_values,
        )

    def replay_events(
        self,
        events: list[Event],
        up_to: date | None = None,
        skip_ids: set[str] | None = None,
        param_overrides: dict[str, dict[str, Any]] | None = None,
        enforce_preconditions: bool = True,
    ) -> WorldState:
        """Replay a (possibly filtered/overridden) event list from init state."""
        skip_ids = skip_ids or set()
        param_overrides = param_overrides or {}
        applied_ids: set[str] = set()

        def apply_fn(state: WorldState, ev: Event) -> None:
            if ev.id in skip_ids:
                return
            patched = ev
            if ev.id in param_overrides:
                patched = Event(
                    id=ev.id,
                    type=ev.type,
                    time=ev.time,
                    params={**ev.params, **param_overrides[ev.id]},
                    visibility=list(ev.visibility),
                    preconditions=list(ev.preconditions),
                    causal_inputs=list(ev.causal_inputs),
                    required_inputs=list(ev.required_inputs),
                    relation_kinds=dict(ev.relation_kinds),
                )
            if enforce_preconditions:
                if any(parent not in applied_ids for parent in patched.required_inputs):
                    return
                ok, _ = self.check_preconditions(state, patched)
                if not ok:
                    return
            self.apply_event(state, patched)
            applied_ids.add(patched.id)

        ordered = sorted(events, key=lambda e: (e.time, e.id))
        return replay(ordered, apply_fn, self.init_values, up_to=up_to)
