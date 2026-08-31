"""Fail-closed validation for serialized training records."""

from __future__ import annotations

import re
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.realworkflow import EPISODE_REPLAY_BUNDLE_SCHEMA
from longworld.core.sourcebundle import (
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
)
from longworld.core.sourceworkflow import SOURCE_WORKFLOW_ADAPTER_REVISIONS
from longworld.core.taskreplaysidecar import TASK_REPLAY_ADAPTER_REGISTRY

_SFT_COMPOSITIONS = {
    "causal_timeline",
    "provenance_graph",
    "same_case_dossier",
    "counterfactual_twin",
}
_COMPOSITION_BY_VIEW = {
    "full": "same_case_dossier",
    "minimal": "same_case_dossier",
    "ordered_artifact_view": "causal_timeline",
    "cf": "counterfactual_twin",
}
_WORKFLOW_KINDS = {
    "synthetic_executable",
    "real_source_derived",
    "hybrid_causal",
}
_EVIDENCE_ROLES = {
    "causal_gold",
    "causal_supporting",
    "structural_hard_negative",
    "natural_background",
}
_SOURCE_ORIGINS = {
    "synthetic_world",
    "real_public",
    "real_private_export",
    "real_derived",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
EXACT_TOKEN_BAND_RANGES: dict[str, tuple[int, int]] = {
    "16k": (16_000, 16_384),
    "32k": (32_000, 32_768),
    "64k": (64_000, 65_536),
    "128k": (128_000, 131_072),
    "256k": (256_000, 262_144),
}
_STRICT_EXACT_BUCKETS = frozenset(EXACT_TOKEN_BAND_RANGES)
STRICT_REPLAY_REVISION = "longworld-strict-replay-v5"
_DENSE_PROMOTION_CONTRACT = {
    "dense_model_provider": "huggingface",
    "dense_model_id": "sentence-transformers/all-MiniLM-L6-v2",
    "dense_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    "dense_model_backend": "sentence-transformers-6.0.0",
    "dense_score_metric": "dot_product",
}
_DENSE_CHUNKING_CONTRACT = {
    "strategy": "tokenizer_token_windows",
    "max_tokens": 192,
    "overlap_tokens": 32,
    "aggregation": "max_similarity",
}


def exact_token_band_reject_reason(length_bucket: str, tokens: int) -> str | None:
    """Return a stable reject reason when an exact-token band is mislabeled."""
    bounds = EXACT_TOKEN_BAND_RANGES.get(length_bucket)
    if bounds is None:
        return None
    lower, upper = bounds
    if not lower <= tokens <= upper:
        return f"exact_{length_bucket}_out_of_range:{tokens}"
    return None


def exact_token_metadata_valid(
    row: dict[str, Any], *, require_asset_manifest: bool
) -> bool:
    """Validate immutable exact-token identity fields without loading the asset."""
    length_bucket = str(row.get("length_bucket") or "")
    tokens = row.get("tokenizer_context_tokens")
    revision = str(row.get("tokenizer_revision") or "")
    asset_digest = str(row.get("tokenizer_asset_manifest_sha256") or "")
    return bool(
        length_bucket in EXACT_TOKEN_BAND_RANGES
        and isinstance(tokens, int)
        and not isinstance(tokens, bool)
        and exact_token_band_reject_reason(length_bucket, tokens) is None
        and str(row.get("tokenizer_model_id") or "")
        and _COMMIT_SHA.fullmatch(revision) is not None
        and (not require_asset_manifest or _SHA256.fullmatch(asset_digest) is not None)
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def replay_bundle_binding_valid(row: dict[str, Any]) -> bool:
    """Require one exact replay sidecar binding at both release layers."""
    promotion = _mapping(row.get("promotion"))
    present = [
        field
        for field in (
            "episode_replay_bundle",
            "source_workflow_bundle",
            "task_replay_sidecar",
        )
        if isinstance(row.get(field), dict) or isinstance(promotion.get(field), dict)
    ]
    if len(present) != 1:
        return False
    field = present[0]
    top_level = row.get(field)
    nested = promotion.get(field)
    if not isinstance(top_level, dict) or top_level != nested:
        return False
    if field == "episode_replay_bundle":
        return set(top_level) == {"schema_version", "sha256", "composition"} and (
            top_level.get("schema_version") == EPISODE_REPLAY_BUNDLE_SCHEMA
            and top_level.get("composition") == "chronological_causal_union"
            and _SHA256.fullmatch(str(top_level.get("sha256") or "")) is not None
        )
    if field == "task_replay_sidecar":
        return set(top_level) == {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        } and (
            (
                str(top_level.get("adapter_id") or ""),
                str(top_level.get("adapter_revision") or ""),
                str(top_level.get("sidecar_schema_version") or ""),
            )
            in TASK_REPLAY_ADAPTER_REGISTRY
            and _SHA256.fullmatch(str(top_level.get("sha256") or "")) is not None
        )
    return set(top_level) == {
        "schema_version",
        "adapter_revision",
        "sha256",
        "binding_digest",
    } and (
        top_level.get("schema_version") == SOURCE_WORKFLOW_BUNDLE_SCHEMA
        and top_level.get("adapter_revision") in SOURCE_WORKFLOW_ADAPTER_REVISIONS
        and _SHA256.fullmatch(str(top_level.get("sha256") or "")) is not None
        and _SHA256.fullmatch(str(top_level.get("binding_digest") or "")) is not None
    )


def sft_row_errors(
    row: dict[str, Any], *, attestation_key: bytes | None = None
) -> list[str]:
    """Return stable reasons why a serialized row is unsafe for SFT."""
    errors: list[str] = []
    key = (
        attestation_key
        if attestation_key is not None
        else attestation_key_from_env("sft_row")
    )
    attestation = _mapping(row.get("attestation"))
    role_attestation_valid = not (
        set(attestation)
        != {"scheme", "purpose", "role", "key_id", "environment", "digest"}
        or attestation.get("purpose") != "sft_row"
        or attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or attestation.get("role") != "promotion"
        or attestation.get("environment") not in {"probe", "production"}
    )
    if not role_attestation_valid or not verify_attestation(
        row, key, purpose="sft_row"
    ):
        errors.append("invalid_or_missing_attestation")
    if row.get("training_objective") != "sft":
        errors.append("not_sft_objective")
    if row.get("data_stage") != "train_ready":
        errors.append("not_train_ready")
    diagnostic_expected = {
        "trust_scope": "local_probe",
        "diagnostic_only": True,
        "content_gate_eligible": True,
        "trust_valid_for_production": False,
        "production_eligible": False,
    }
    diagnostic_declared = (
        row.get(LOCAL_PROBE_TRUST_ISOLATION_FIELD) is not None
        or any(field in row for field in diagnostic_expected)
        or attestation.get("environment") == "probe"
    )
    local_probe_diagnostic = (
        attestation.get("environment") == "probe"
        and row.get(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
        in {None, LOCAL_PROBE_TRUST_ISOLATION_VALUE}
        and all(row.get(field) == value for field, value in diagnostic_expected.items())
    )
    if diagnostic_declared and not local_probe_diagnostic:
        errors.append("invalid_local_probe_diagnostic_boundary")
    if row.get("composition_method") not in _SFT_COMPOSITIONS:
        errors.append("invalid_composition_method")
    if row.get("composition_method") != _COMPOSITION_BY_VIEW.get(
        str(row.get("view") or "")
    ):
        errors.append("view_composition_mismatch")
    if not str(row.get("context") or "").strip():
        errors.append("empty_context")
    if not str(row.get("answer") or "").strip():
        errors.append("empty_answer")

    verification = _mapping(row.get("verification"))
    if not verification.get("production_mode"):
        errors.append("not_production_verified")
    if not verification.get("semantic_sufficient"):
        errors.append("semantic_proof_failed")
    if not verification.get("strict_executable_sufficient"):
        errors.append("strict_executable_proof_failed")
    if verification.get("embedding_topk_insufficient") is not True:
        errors.append("dense_retrieval_gate_failed")
    promotion = _mapping(row.get("promotion"))
    length_bucket = str(row.get("length_bucket") or "")
    exact_asset_digest = str(row.get("tokenizer_asset_manifest_sha256") or "")
    has_exact_asset_binding = (
        row.get("tokenizer_asset_manifest_sha256") is not None
        or promotion.get("tokenizer_asset_manifest_sha256") is not None
    )
    strict_exact_asset_valid = not (
        length_bucket in _STRICT_EXACT_BUCKETS and has_exact_asset_binding
    ) or (
        _SHA256.fullmatch(exact_asset_digest) is not None
        and promotion.get("tokenizer_asset_manifest_sha256") == exact_asset_digest
    )
    task_exact_binding = isinstance(row.get("task_replay_sidecar"), dict)
    if (length_bucket == "128k" or task_exact_binding) and (
        not exact_token_metadata_valid(row, require_asset_manifest=True)
        or promotion.get("tokenizer_asset_manifest_sha256") != exact_asset_digest
    ):
        errors.append("invalid_exact_token_binding")
    promotion_valid = (
        promotion.get("schema_version") == "train-ready-promotion-v2"
        and all(
            _SHA256.fullmatch(str(promotion.get(field) or "")) is not None
            for field in (
                "candidate_sha256",
                "dense_audit_sha256",
                "dense_ranking_sha256",
            )
        )
        and bool(str(promotion.get("dense_model_provider") or ""))
        and bool(str(promotion.get("dense_model_id") or ""))
        and _COMMIT_SHA.fullmatch(str(promotion.get("dense_model_revision") or ""))
        is not None
        and all(
            promotion.get(field) == expected
            for field, expected in _DENSE_PROMOTION_CONTRACT.items()
        )
        and promotion.get("dense_chunking") == _DENSE_CHUNKING_CONTRACT
        and isinstance(promotion.get("dense_top_k"), int)
        and int(promotion["dense_top_k"]) == 3
        and promotion.get("strict_replay_revision") == STRICT_REPLAY_REVISION
        and str(promotion.get("strict_replay_answer") or "")
        == str(row.get("answer") or "")
        and strict_exact_asset_valid
    )
    if not promotion_valid:
        errors.append("missing_or_invalid_promotion")
    view_verification = _mapping(row.get("view_verification"))
    if local_probe_diagnostic:
        if (
            view_verification.get("content_gate_eligible") is not True
            or view_verification.get("production_eligible") is not False
        ):
            errors.append("invalid_local_probe_diagnostic_boundary")
    elif not view_verification.get("production_eligible"):
        errors.append("view_not_production_eligible")
    if any(
        not view_verification.get(field)
        for field in (
            "essential_present",
            "semantic_text_grounded",
            "classification_ok",
            "global_proof_green",
        )
    ):
        errors.append("view_proof_incomplete")
    answer = str(row.get("answer") or "")
    if (
        str(view_verification.get("expected_answer") or "") != answer
        or str(view_verification.get("strict_replay_answer") or "") != answer
    ):
        errors.append("view_answer_replay_mismatch")

    classes = row.get("artifact_classification")
    workflow_ids = {str(value) for value in row.get("workflow_ids") or [] if value}
    if not isinstance(classes, list) or not classes:
        errors.append("missing_artifact_classification")
        return errors
    class_workflows: set[str] = set()
    class_origins: set[str] = set()
    class_workflow_kinds: set[str] = set()
    real_causal_class = False
    has_gold = False
    invalid_class = False
    for item in classes:
        if not isinstance(item, dict):
            invalid_class = True
            continue
        workflow_id = str(item.get("workflow_id") or "")
        if workflow_id:
            class_workflows.add(workflow_id)
        source_origin = str(item.get("source_origin") or "")
        workflow_kind = str(item.get("workflow_kind") or "")
        evidence_role = str(item.get("evidence_role") or "")
        if source_origin:
            class_origins.add(source_origin)
        if workflow_kind:
            class_workflow_kinds.add(workflow_kind)
        real_causal_class = real_causal_class or (
            source_origin in {"real_public", "real_private_export", "real_derived"}
            and workflow_kind in {"hybrid_causal", "real_source_derived"}
            and evidence_role in {"causal_gold", "causal_supporting"}
        )
        has_gold = has_gold or item.get("evidence_role") == "causal_gold"
        invalid_class = invalid_class or (
            not str(item.get("artifact_id") or "")
            or not workflow_id
            or item.get("workflow_kind") not in _WORKFLOW_KINDS
            or item.get("evidence_role") not in _EVIDENCE_ROLES
            or item.get("source_origin") not in _SOURCE_ORIGINS
            or not str(item.get("provenance_id") or "")
        )
    if invalid_class:
        errors.append("invalid_artifact_classification")
    if len(workflow_ids) != 1 or class_workflows != workflow_ids:
        errors.append("incoherent_workflow")
    if not has_gold:
        errors.append("sft_missing_causal_evidence")
    if "source_origins" in row and set(row.get("source_origins") or []) != (
        class_origins
    ):
        errors.append("source_origin_summary_mismatch")
    if "workflow_kinds" in row and set(row.get("workflow_kinds") or []) != (
        class_workflow_kinds
    ):
        errors.append("workflow_kind_summary_mismatch")
    real_source_verified = row.get("real_source_verified") is True
    if real_source_verified:
        relation_id_present = bool(row.get("source_relation_id"))
        relation_edges_present = bool(row.get("source_relation_edges"))
        if (
            not real_causal_class
            or not row.get("real_source_family_ids")
            or not row.get("real_source_workflow_ids")
            or relation_id_present != relation_edges_present
            or not replay_bundle_binding_valid(row)
            or promotion.get("real_source_verified") is not True
        ):
            errors.append("invalid_replayed_real_source_identity")
    elif any(
        (
            row.get("real_source_family_ids"),
            row.get("real_source_workflow_ids"),
            promotion.get("real_source_verified") is True,
        )
    ):
        errors.append("invalid_replayed_real_source_identity")
    return errors
