"""Explicit provenance and workflow classes for trainable long-context data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from longworld.core.render import Artifact


class SourceOrigin(str, Enum):
    UNKNOWN = "unknown"
    SYNTHETIC_WORLD = "synthetic_world"
    REAL_PUBLIC = "real_public"
    REAL_PRIVATE_EXPORT = "real_private_export"
    REAL_DERIVED = "real_derived"


class WorkflowKind(str, Enum):
    UNCLASSIFIED = "unclassified"
    SYNTHETIC_EXECUTABLE = "synthetic_executable"
    REAL_SOURCE_DERIVED = "real_source_derived"
    HYBRID_CAUSAL = "hybrid_causal"
    BACKGROUND_ONLY = "background_only"


class EvidenceRole(str, Enum):
    UNCLASSIFIED = "unclassified"
    CAUSAL_GOLD = "causal_gold"
    CAUSAL_SUPPORTING = "causal_supporting"
    STRUCTURAL_HARD_NEGATIVE = "structural_hard_negative"
    NATURAL_BACKGROUND = "natural_background"


class CompositionMethod(str, Enum):
    CAUSAL_TIMELINE = "causal_timeline"
    PROVENANCE_GRAPH = "provenance_graph"
    SAME_CASE_DOSSIER = "same_case_dossier"
    COUNTERFACTUAL_TWIN = "counterfactual_twin"
    RANDOM_CONCAT = "random_concat"


class TrainingObjective(str, Enum):
    SFT = "sft"
    CPT = "cpt"
    DIAGNOSTIC = "diagnostic"
    REJECT = "reject"


@dataclass(frozen=True)
class ArtifactClassification:
    source_origin: SourceOrigin = SourceOrigin.UNKNOWN
    workflow_kind: WorkflowKind = WorkflowKind.UNCLASSIFIED
    evidence_role: EvidenceRole = EvidenceRole.UNCLASSIFIED
    workflow_id: str = ""
    provenance_id: str = ""


def classify_artifact(
    artifact: Artifact,
    *,
    source_origin: SourceOrigin,
    workflow_kind: WorkflowKind,
    evidence_role: EvidenceRole,
    workflow_id: str,
    provenance_id: str = "",
) -> Artifact:
    """Attach an explicit classification; never infer trainability from doc type."""
    artifact.slots = {
        **(artifact.slots or {}),
        "classification": {
            "source_origin": source_origin.value,
            "workflow_kind": workflow_kind.value,
            "evidence_role": evidence_role.value,
            "workflow_id": workflow_id,
            "provenance_id": provenance_id,
        },
    }
    return artifact


def artifact_classification(artifact: Artifact) -> ArtifactClassification:
    raw: dict[str, Any] = dict((artifact.slots or {}).get("classification") or {})
    try:
        source_origin = SourceOrigin(raw.get("source_origin", SourceOrigin.UNKNOWN))
        workflow_kind = WorkflowKind(
            raw.get("workflow_kind", WorkflowKind.UNCLASSIFIED)
        )
        evidence_role = EvidenceRole(
            raw.get("evidence_role", EvidenceRole.UNCLASSIFIED)
        )
    except ValueError:
        return ArtifactClassification()
    return ArtifactClassification(
        source_origin=source_origin,
        workflow_kind=workflow_kind,
        evidence_role=evidence_role,
        workflow_id=str(raw.get("workflow_id") or ""),
        provenance_id=str(raw.get("provenance_id") or ""),
    )


def validate_context_classification(
    artifacts: list[Artifact],
    *,
    objective: TrainingObjective,
    composition_method: CompositionMethod,
) -> list[str]:
    """Return stable rejection reasons for SFT/CPT context classification."""
    errors: list[str] = []
    classes = [artifact_classification(a) for a in artifacts]
    if composition_method == CompositionMethod.RANDOM_CONCAT:
        errors.append("random_concat_forbidden")
    if objective in {TrainingObjective.SFT, TrainingObjective.CPT} and any(
        c.workflow_kind == WorkflowKind.UNCLASSIFIED
        or c.evidence_role == EvidenceRole.UNCLASSIFIED
        or not c.workflow_id
        for c in classes
    ):
        errors.append("trainable_context_has_unclassified_artifact")
    if objective == TrainingObjective.SFT and not any(
        c.evidence_role == EvidenceRole.CAUSAL_GOLD for c in classes
    ):
        errors.append("sft_missing_causal_evidence")
    if objective == TrainingObjective.CPT:
        workflow_ids = {c.workflow_id for c in classes if c.workflow_id}
        coherent = {
            c.workflow_id
            for c in classes
            if c.workflow_kind
            in {
                WorkflowKind.REAL_SOURCE_DERIVED,
                WorkflowKind.SYNTHETIC_EXECUTABLE,
                WorkflowKind.HYBRID_CAUSAL,
            }
            and c.evidence_role
            in {EvidenceRole.CAUSAL_GOLD, EvidenceRole.CAUSAL_SUPPORTING}
        }
        if (
            len(artifacts) < 2
            or len(coherent) != 1
            or len(workflow_ids) != 1
            or any(c.workflow_kind == WorkflowKind.BACKGROUND_ONLY for c in classes)
        ):
            errors.append("cpt_requires_single_coherent_workflow")
    return errors
