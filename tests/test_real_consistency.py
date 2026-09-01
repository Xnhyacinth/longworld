from __future__ import annotations

from datetime import date

from longworld.core.consist import artifact_text_issues, consistency_scan
from longworld.core.render import Artifact
from longworld.core.state import WorldState
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)
from longworld.core.world import Event, SimulatedWorld


def _world() -> SimulatedWorld:
    event = Event(
        id="w.real_ci",
        type="repo_record",
        time=date(2026, 8, 20),
        params={},
        visibility=["w.real_ci"],
    )
    return SimulatedWorld(
        world_id="w",
        seed=1,
        schema_version="test",
        spec={"world_id": "w"},
        state=WorldState(values={}),
        events=[event],
        init_values={},
    )


def _artifact() -> Artifact:
    return Artifact(
        artifact_id="w.real_ci",
        doc_type="log",
        time=date(2026, 8, 20),
        project="repo",
        prefix="focal",
        reveals_events=["w.real_ci"],
        text="CI payload\nstatus=COMPLETED\nconclusion=SUCCESS",
        facts=[],
        slots={"ground_values": ["completed", "success"]},
        is_focal=True,
    )


def test_real_workflow_body_allows_native_kv_and_casefolded_ground_values() -> None:
    artifact = _artifact()
    classify_artifact(
        artifact,
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.HYBRID_CAUSAL,
        evidence_role=EvidenceRole.CAUSAL_SUPPORTING,
        workflow_id="w",
        provenance_id="sha256:" + "a" * 64,
    )

    assert consistency_scan(_world(), [artifact]).ok
    assert artifact_text_issues(_world(), [artifact]) == []


def test_relationship_only_body_still_requires_an_event_type_anchor() -> None:
    artifact = _artifact()
    artifact.slots["ground_values"] = []

    issues = artifact_text_issues(_world(), [artifact])

    assert any(issue.kind == "event_unanchored" for issue in issues)


def test_synthetic_body_still_rejects_kv_shortcuts() -> None:
    result = consistency_scan(_world(), [_artifact()])

    assert not result.ok
    assert any(issue.kind == "shortcut_kv" for issue in result.issues)
