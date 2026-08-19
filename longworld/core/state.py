from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class StateDelta:
    key: str
    old: Any
    new: Any
    event_id: str
    time: date


@dataclass
class WorldState:
    values: dict[str, Any]
    version: int = 0
    history: list[StateDelta] = field(default_factory=list)

    def copy(self) -> WorldState:
        return WorldState(
            values=dict(self.values),
            version=self.version,
            history=list(self.history),
        )

    def set(self, key: str, new: Any, event_id: str, time: date) -> StateDelta:
        old = self.values.get(key)
        self.values[key] = new
        delta = StateDelta(key=key, old=old, new=new, event_id=event_id, time=time)
        self.history.append(delta)
        self.version += 1
        return delta


def replay(
    events: list[Any], apply_fn, init_values: dict[str, Any], up_to: date | None = None
) -> WorldState:
    """Replay events in order. `apply_fn(state, event) -> None` mutates state."""
    state = WorldState(values=dict(init_values))
    for ev in events:
        if up_to is not None and ev.time > up_to:
            continue
        apply_fn(state, ev)
    return state
