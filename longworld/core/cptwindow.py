"""Pack verified workflow records into exact, non-overlapping CPT windows."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from longworld.core.realworkflow import RealWorkflow, WorkflowRecord


@dataclass(frozen=True)
class CPTBand:
    name: str
    lower_tokens: int
    upper_tokens: int

    def __post_init__(self) -> None:
        if (
            not self.name
            or self.lower_tokens <= 0
            or self.upper_tokens < self.lower_tokens
        ):
            raise ValueError("CPT token band is invalid")


@dataclass(frozen=True)
class CPTWindowRequest:
    band: CPTBand
    count: int
    min_source_events: int = 1

    def __post_init__(self) -> None:
        if self.count <= 0 or self.min_source_events <= 0:
            raise ValueError(
                "CPT window count and source-event minimum must be positive"
            )


@dataclass(frozen=True)
class PackedCPTWindow:
    band: CPTBand
    workflow: RealWorkflow
    context_tokens: int
    record_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    source_event_count: int
    source_start_index: int
    source_end_index: int


@dataclass(frozen=True)
class CPTWindowPackingResult:
    windows: tuple[PackedCPTWindow, ...]
    next_record_index: int
    reject_reasons: dict[str, int]


def _context(records: Sequence[WorkflowRecord]) -> str:
    return "\n\n".join(record.text for record in records)


def _derived_workflow(
    source: RealWorkflow,
    records: Sequence[WorkflowRecord],
    band: CPTBand,
    context: str,
) -> RealWorkflow:
    selected_ids = {record.record_id for record in records}
    normalized: list[WorkflowRecord] = []
    for index, record in enumerate(records):
        links = (
            ()
            if index == 0
            else tuple(link for link in record.links if link in selected_ids)
        )
        normalized.append(
            WorkflowRecord(
                record_id=record.record_id,
                kind=record.kind,
                occurred_at=record.occurred_at,
                text=record.text,
                links=links,
                attributes=dict(record.attributes),
                source_pointer=record.source_pointer,
            )
        )
    identity = hashlib.sha256(
        (
            f"{source.workflow_id}\0{band.name}\0"
            + "\0".join(record.record_id for record in records)
            + "\0"
            + hashlib.sha256(context.encode()).hexdigest()
        ).encode()
    ).hexdigest()[:24]
    return RealWorkflow(
        workflow_id=f"{source.workflow_id}:cpt:{band.name}:{identity}",
        source_kind=source.source_kind,
        source_origin=source.source_origin,
        lineage=source.lineage,
        records=tuple(normalized),
        facts={},
    )


def pack_disjoint_workflow_windows(
    workflow: RealWorkflow,
    *,
    requests: Sequence[CPTWindowRequest],
    token_counter: Callable[[str], int],
    source_event_id: Callable[[WorkflowRecord], str] | None = None,
) -> CPTWindowPackingResult:
    """Consume source records once while satisfying exact token-band requests.

    Records are never reordered or reused. A new window may start at any record,
    but later records must link to an earlier record in that same window. This
    prevents the packer from manufacturing a connection across source components.
    """
    records = workflow.records
    cursor = 0
    windows: list[PackedCPTWindow] = []
    rejects: Counter[str] = Counter()
    token_cache: dict[str, int] = {}
    separator_tokens = token_counter("\n\n")

    for request in requests:
        produced = 0
        while produced < request.count and cursor < len(records):
            selected: list[WorkflowRecord] = []
            selected_ids: set[str] = set()
            selected_event_ids: set[str] = set()
            estimated_tokens = 0
            start_index = cursor
            while cursor < len(records):
                record = records[cursor]
                record_tokens = token_cache.get(record.record_id)
                if record_tokens is None:
                    record_tokens = token_counter(record.text)
                    token_cache[record.record_id] = record_tokens
                if record_tokens > (
                    request.band.upper_tokens - request.band.lower_tokens
                ):
                    if selected:
                        rejects["short_disconnected_component"] += len(selected)
                        selected = []
                        selected_ids = set()
                        selected_event_ids = set()
                        estimated_tokens = 0
                    rejects["record_exceeds_band_slack"] += 1
                    cursor += 1
                    start_index = cursor
                    continue
                if selected and not selected_ids.intersection(record.links):
                    rejects["short_disconnected_component"] += len(selected)
                    selected = []
                    selected_ids = set()
                    selected_event_ids = set()
                    estimated_tokens = 0
                    start_index = cursor
                event_id = (
                    source_event_id(record)
                    if source_event_id is not None
                    else record.record_id
                )
                if not event_id:
                    raise ValueError("CPT source event identity is empty")
                if selected:
                    estimated_tokens += separator_tokens
                selected.append(record)
                selected_ids.add(record.record_id)
                selected_event_ids.add(event_id)
                estimated_tokens += record_tokens
                cursor += 1
                exact_margin = min(
                    request.band.upper_tokens - request.band.lower_tokens,
                    max(32, len(selected) * 2),
                )
                if estimated_tokens < request.band.lower_tokens - exact_margin:
                    continue
                if len(selected_event_ids) < request.min_source_events:
                    continue
                context = _context(selected)
                context_tokens = token_counter(context)
                if context_tokens < request.band.lower_tokens:
                    continue
                if context_tokens > request.band.upper_tokens:
                    rejects["window_exceeds_upper_bound"] += len(selected)
                    selected = []
                    selected_ids = set()
                    selected_event_ids = set()
                    estimated_tokens = 0
                    start_index = cursor
                    continue
                packed_workflow = _derived_workflow(
                    workflow, selected, request.band, context
                )
                ordered_event_ids = tuple(
                    dict.fromkeys(
                        source_event_id(record)
                        if source_event_id is not None
                        else record.record_id
                        for record in selected
                    )
                )
                windows.append(
                    PackedCPTWindow(
                        band=request.band,
                        workflow=packed_workflow,
                        context_tokens=context_tokens,
                        record_ids=tuple(record.record_id for record in selected),
                        source_event_ids=ordered_event_ids,
                        source_event_count=len(ordered_event_ids),
                        source_start_index=start_index,
                        source_end_index=cursor,
                    )
                )
                if source_event_id is not None:
                    last_event_id = ordered_event_ids[-1]
                    while cursor < len(records):
                        next_event_id = source_event_id(records[cursor])
                        if not next_event_id:
                            raise ValueError("CPT source event identity is empty")
                        if next_event_id != last_event_id:
                            break
                        rejects["source_event_tail_records_discarded"] += 1
                        cursor += 1
                produced += 1
                break
            else:
                if selected:
                    rejects["short_tail_records"] += len(selected)
                break
        if produced < request.count:
            rejects[f"unfilled_{request.band.name}"] += request.count - produced

    return CPTWindowPackingResult(
        windows=tuple(windows),
        next_record_index=cursor,
        reject_reasons=dict(rejects),
    )
