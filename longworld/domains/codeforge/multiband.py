from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise
from typing import TYPE_CHECKING

from longworld.core.world import SimulatedWorld

if TYPE_CHECKING:
    from longworld.domains.company.queries import QuerySpec

_BAND_ORDER = {"16k": 0, "32k": 1, "64k": 2}
_RELEASE_HISTORY_QUERY_TYPES = {
    "version_selection",
    "release_supersession_trace",
}


def _authentic_relation_count(world: SimulatedWorld, event_ids: Iterable[str]) -> int:
    selected = set(event_ids)
    events = {
        event.id: event
        for event in world.events
        if event.id in selected and event.type == "repo_record"
    }
    count = 0
    for event in events.values():
        synthetic_inputs = set(event.params.get("synthetic_relation_inputs") or [])
        count += sum(
            parent_id in events and parent_id not in synthetic_inputs
            for parent_id in event.causal_inputs
        )
    return count


def bind_cumulative_release_history(
    world: SimulatedWorld, queries: list[QuerySpec]
) -> None:
    """Bind and validate nested real release-history tasks across length bands."""
    groups: dict[str, list[QuerySpec]] = {}
    for query in queries:
        if query.query_type not in _RELEASE_HISTORY_QUERY_TYPES:
            continue
        if (
            len(query.preferred_length_buckets) != 1
            or query.preferred_length_buckets[0] not in _BAND_ORDER
        ):
            raise ValueError("release-history query must bind one exact length band")
        if not query.semantic_growth_group:
            raise ValueError("release-history query has no semantic growth group")
        groups.setdefault(query.semantic_growth_group, []).append(query)

    for group_id, group in groups.items():
        group.sort(key=lambda item: _BAND_ORDER[item.preferred_length_buckets[0]])
        bands = [item.preferred_length_buckets[0] for item in group]
        if len(bands) != len(set(bands)):
            raise ValueError("release-history group repeats a length band")
        expected = ["16k", "32k", "64k"][: len(bands)]
        if bands != expected:
            raise ValueError("release-history group has a non-cumulative band sequence")

        base_task_group = f"codeforge.release_history|{group_id}"
        for query in group:
            query.base_task_group = base_task_group

        for before, after in pairwise(group):
            if not set(before.essential_event_ids) < set(after.essential_event_ids):
                raise ValueError("release-history necessary evidence does not grow")
            if not set(before.sufficient_event_ids) < set(after.sufficient_event_ids):
                raise ValueError("release-history strict support does not grow")
            if before.proof_depth >= after.proof_depth:
                raise ValueError("release-history proof depth does not grow")
            if _authentic_relation_count(
                world, before.sufficient_event_ids
            ) >= _authentic_relation_count(world, after.sufficient_event_ids):
                raise ValueError(
                    "release-history authentic source relations do not grow"
                )
