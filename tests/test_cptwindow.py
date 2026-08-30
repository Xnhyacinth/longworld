from __future__ import annotations

import hashlib

from longworld.core.cptwindow import (
    CPTBand,
    CPTWindowRequest,
    pack_disjoint_workflow_windows,
)
from longworld.core.provenance import SourceLineage
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
from longworld.core.taxonomy import SourceOrigin


def _tokens(text: str) -> int:
    return len(text.split())


def _workflow(*, disconnected_at: int | None = None) -> RealWorkflow:
    source = b"source manifest"
    digest = hashlib.sha256(source).hexdigest()
    records: list[WorkflowRecord] = []
    for index in range(12):
        links = () if index == 0 or index == disconnected_at else (f"r{index - 1}",)
        records.append(
            WorkflowRecord(
                record_id=f"r{index}",
                kind="commit",
                occurred_at=f"2026-01-{index + 1:02d}T00:00:00Z",
                text=" ".join(f"t{index}_{token}" for token in range(10)),
                links=links,
                source_pointer=f"https://example.com/commit/{index}",
            )
        )
    return RealWorkflow(
        workflow_id="git-history:example/repo@head",
        source_kind="git_history",
        source_origin=SourceOrigin.REAL_PUBLIC,
        lineage=SourceLineage(
            provenance_id=f"sha256:{digest}",
            url="https://github.com/example/repo",
            license="MIT",
            retrieved_at="2026-02-01T00:00:00Z",
            parser="git_history@1",
            sha256=digest,
            revision="a" * 40,
            source_path="SOURCE_MANIFEST.json",
        ),
        records=tuple(records),
        facts={},
    )


def test_bulk_windows_are_exact_connected_and_disjoint_across_bands() -> None:
    result = pack_disjoint_workflow_windows(
        _workflow(),
        requests=(
            CPTWindowRequest(CPTBand("128k", 38, 50), count=1),
            CPTWindowRequest(CPTBand("64k", 28, 40), count=2),
        ),
        token_counter=_tokens,
    )

    assert [window.band.name for window in result.windows] == [
        "128k",
        "64k",
        "64k",
    ]
    used = [record_id for window in result.windows for record_id in window.record_ids]
    assert len(used) == len(set(used))
    for window in result.windows:
        assert (
            window.band.lower_tokens
            <= window.context_tokens
            <= (window.band.upper_tokens)
        )
        records = window.workflow.records
        assert records[0].links == ()
        seen = {records[0].record_id}
        for record in records[1:]:
            assert set(record.links) & seen
            seen.add(record.record_id)
        assert window.workflow.workflow_id != "git-history:example/repo@head"
    assert result.reject_reasons == {}


def test_bulk_windows_do_not_bridge_disconnected_source_components() -> None:
    result = pack_disjoint_workflow_windows(
        _workflow(disconnected_at=3),
        requests=(CPTWindowRequest(CPTBand("64k", 38, 50), count=1),),
        token_counter=_tokens,
    )

    assert len(result.windows) == 1
    assert result.windows[0].record_ids == ("r3", "r4", "r5", "r6")
    assert result.reject_reasons == {"short_disconnected_component": 3}


def test_bulk_windows_report_unfilled_quota_and_short_tail() -> None:
    result = pack_disjoint_workflow_windows(
        _workflow(),
        requests=(CPTWindowRequest(CPTBand("128k", 48, 60), count=3),),
        token_counter=_tokens,
    )

    assert len(result.windows) == 2
    assert result.reject_reasons == {"short_tail_records": 2, "unfilled_128k": 1}


def test_bulk_windows_reject_a_record_coarser_than_band_slack() -> None:
    workflow = _workflow()
    records = list(workflow.records)
    records[0] = WorkflowRecord(
        record_id="r0",
        kind="commit",
        occurred_at="2026-01-01T00:00:00Z",
        text=" ".join(f"large_{index}" for index in range(20)),
        links=(),
        source_pointer="https://example.com/commit/0",
    )
    coarse = RealWorkflow(
        workflow_id=workflow.workflow_id,
        source_kind=workflow.source_kind,
        source_origin=workflow.source_origin,
        lineage=workflow.lineage,
        records=tuple(records),
        facts={},
    )

    result = pack_disjoint_workflow_windows(
        coarse,
        requests=(CPTWindowRequest(CPTBand("64k", 28, 40), count=1),),
        token_counter=_tokens,
    )

    assert result.reject_reasons["record_exceeds_band_slack"] == 1
    assert "r0" not in result.windows[0].record_ids


def test_bulk_windows_only_retokenize_near_exact_band_boundary() -> None:
    calls = 0

    def counted_tokens(text: str) -> int:
        nonlocal calls
        calls += 1
        return _tokens(text)

    result = pack_disjoint_workflow_windows(
        _workflow(),
        requests=(
            CPTWindowRequest(CPTBand("128k", 38, 50), count=1),
            CPTWindowRequest(CPTBand("64k", 28, 40), count=2),
        ),
        token_counter=counted_tokens,
    )

    assert len(result.windows) == 3
    assert calls <= 18
