from datetime import date

from longworld.core.render import Artifact
from longworld.core.taxonomy import (
    CompositionMethod,
    EvidenceRole,
    SourceOrigin,
    TrainingObjective,
    WorkflowKind,
    artifact_classification,
    classify_artifact,
    validate_context_classification,
)


def _artifact(aid: str, *, reveals: list[str] | None = None) -> Artifact:
    return Artifact(
        artifact_id=aid,
        doc_type="email",
        time=date(2026, 1, 1),
        project="p",
        prefix="focal",
        reveals_events=reveals or [],
        text="A dated workflow record.",
        facts=[],
    )


def test_missing_classification_is_unclassified_not_guessed() -> None:
    cls = artifact_classification(_artifact("a"))
    assert cls.source_origin == SourceOrigin.UNKNOWN
    assert cls.workflow_kind == WorkflowKind.UNCLASSIFIED
    assert cls.evidence_role == EvidenceRole.UNCLASSIFIED


def test_classification_round_trips_through_artifact_slots() -> None:
    art = _artifact("a", reveals=["e1"])
    classify_artifact(
        art,
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.REAL_SOURCE_DERIVED,
        evidence_role=EvidenceRole.CAUSAL_GOLD,
        workflow_id="rfc9110-lineage",
        provenance_id="sha256:abc",
    )
    cls = artifact_classification(art)
    assert cls.source_origin == SourceOrigin.REAL_PUBLIC
    assert cls.workflow_kind == WorkflowKind.REAL_SOURCE_DERIVED
    assert cls.evidence_role == EvidenceRole.CAUSAL_GOLD
    assert cls.workflow_id == "rfc9110-lineage"
    assert cls.provenance_id == "sha256:abc"


def test_random_concat_is_never_trainable() -> None:
    art = _artifact("a", reveals=["e1"])
    classify_artifact(
        art,
        source_origin=SourceOrigin.SYNTHETIC_WORLD,
        workflow_kind=WorkflowKind.SYNTHETIC_EXECUTABLE,
        evidence_role=EvidenceRole.CAUSAL_GOLD,
        workflow_id="w1",
    )
    errors = validate_context_classification(
        [art],
        objective=TrainingObjective.SFT,
        composition_method=CompositionMethod.RANDOM_CONCAT,
    )
    assert "random_concat_forbidden" in errors


def test_sft_requires_explicit_causal_evidence() -> None:
    background = _artifact("bg")
    classify_artifact(
        background,
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.BACKGROUND_ONLY,
        evidence_role=EvidenceRole.NATURAL_BACKGROUND,
        workflow_id="rfc9110",
    )
    errors = validate_context_classification(
        [background],
        objective=TrainingObjective.SFT,
        composition_method=CompositionMethod.SAME_CASE_DOSSIER,
    )
    assert "sft_missing_causal_evidence" in errors


def test_cpt_accepts_coherent_real_workflow_and_rejects_background_only() -> None:
    records = [_artifact("issue"), _artifact("release")]
    for art in records:
        classify_artifact(
            art,
            source_origin=SourceOrigin.REAL_PUBLIC,
            workflow_kind=WorkflowKind.REAL_SOURCE_DERIVED,
            evidence_role=EvidenceRole.CAUSAL_SUPPORTING,
            workflow_id="repo:owner/name#42",
            provenance_id=f"sha256:{art.artifact_id}",
        )
    assert not validate_context_classification(
        records,
        objective=TrainingObjective.CPT,
        composition_method=CompositionMethod.CAUSAL_TIMELINE,
    )

    classify_artifact(
        records[1],
        source_origin=SourceOrigin.REAL_PUBLIC,
        workflow_kind=WorkflowKind.BACKGROUND_ONLY,
        evidence_role=EvidenceRole.NATURAL_BACKGROUND,
        workflow_id="unrelated",
    )
    errors = validate_context_classification(
        records,
        objective=TrainingObjective.CPT,
        composition_method=CompositionMethod.CAUSAL_TIMELINE,
    )
    assert "cpt_requires_single_coherent_workflow" in errors
