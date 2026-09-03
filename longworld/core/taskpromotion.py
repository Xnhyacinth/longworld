"""Fail-closed dense audit and promotion for registered task replay adapters."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import date
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    attach_attestation,
    local_probe_diagnostic_metadata,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.domainhistory import (
    audit_cross_cve_pipeline_candidate,
    audit_kev_pipeline_candidate,
    replay_cross_cve_pipeline_candidate,
    replay_kev_pipeline_candidate,
)
from longworld.core.financehistory import (
    audit_finance_pipeline_candidate,
    replay_finance_pipeline_selection,
)
from longworld.core.macrovintage import (
    audit_macro_vintage_pipeline_candidate,
    replay_macro_vintage_pipeline_selection,
)
from longworld.core.pack import SEP, prompt_document_prefix, wrap_prompt
from longworld.core.promotion import (
    _APPROVED_EXACT_TOKENIZERS,
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    PROMOTION_SCHEMA,
    RELEASE_SELECTION_PURPOSE,
    RELEASE_SELECTION_SCHEMA,
    PromotionError,
    _load_replay_tokenizer_uncached,
    _resolved_local_tokenizer_revision,
    _token_counter_for,
    _validate_external_ranking,
    candidate_sha256,
    serialized_row_sha256,
    task_semantic_commitment_sha256_from_audit,
)
from longworld.core.record_contract import (
    EXACT_TOKEN_BAND_RANGES,
    STRICT_REPLAY_REVISION,
    exact_token_band_reject_reason,
    exact_token_metadata_valid,
    sft_row_errors,
)
from longworld.core.render import Artifact
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.taskproof import (
    TaskProofError,
    audit_task_view_projection,
    canonicalize_cross_cve_projection_candidate,
    canonicalize_kev_projection_candidate,
    compute_task_proof,
    normalize_projection_candidate_for_adapter,
)
from longworld.core.taskreplaysidecar import (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    SOURCE_TOKEN_MEASUREMENT_BASIS,
    SOURCE_TOKEN_MEASUREMENT_RECEIPT_SCHEMA,
    TASK_REPLAY_SIDECAR_PURPOSE,
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
    TASK_VIEW_DERIVATION_REVISION,
    LoadedTaskReplaySidecar,
    task_candidate_content_commitment,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from longworld.core.verify import Verification

TokenCounter = Callable[[str], int]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DENSE_TOP_K = 3
_CANDIDATE_FORBIDDEN_PROOF_FIELDS = frozenset(
    {
        "adapter_audit",
        "adapter_audit_sha256",
        "task_proof",
        "task_proof_receipt",
        "task_proof_sha256",
        "task_quality_metadata",
        "task_semantic_commitment_sha256",
        "strict_replay_prefix_answers",
        "counterfactual_replay_answer",
        "full_pool_strict_replay_sufficient",
        "task_replay_payload_sha256",
        "source_binding_sha256",
        "strict_growth_metrics",
        "verification",
        "verification_replay_sha256",
        "view_verification",
        "promotion",
    }
)


def task_sidecar_token_counter(
    sidecar: LoadedTaskReplaySidecar,
) -> TokenCounter:
    """Load the exact approved tokenizer bound by a verified task sidecar."""
    model_id = str(sidecar.replay_payload.get("tokenizer_model_id") or "")
    revision = str(sidecar.replay_payload.get("tokenizer_revision") or "")
    asset_digest = str(
        sidecar.replay_payload.get("tokenizer_asset_manifest_sha256") or ""
    )
    if (model_id, revision) not in _APPROVED_EXACT_TOKENIZERS:
        raise PromotionError("task sidecar exact tokenizer pin is not approved")
    try:
        loaded_revision = _resolved_local_tokenizer_revision(model_id, revision)
        observed_asset_digest = resolved_tokenizer_asset_manifest_sha256(
            model_id, revision
        )
        if loaded_revision != revision or observed_asset_digest != asset_digest:
            raise PromotionError("task sidecar exact tokenizer assets do not match")
        tokenizer = _load_replay_tokenizer_uncached(model_id, revision)
        loaded_asset_digest = resolved_tokenizer_asset_manifest_sha256(
            model_id, revision
        )
    except PromotionError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        raise PromotionError("task sidecar exact tokenizer cannot be loaded") from error
    if loaded_asset_digest != observed_asset_digest:
        raise PromotionError("task sidecar exact tokenizer assets do not match")
    counter = _token_counter_for(tokenizer)
    if counter is None:
        raise PromotionError("task sidecar exact tokenizer is unavailable")
    counter.offset_tokenizer = tokenizer
    return counter


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _sidecar_binding(sidecar: LoadedTaskReplaySidecar) -> dict[str, str]:
    return {
        "adapter_id": sidecar.adapter_id,
        "adapter_revision": sidecar.adapter_revision,
        "sidecar_schema_version": sidecar.sidecar_schema_version,
        "sha256": sidecar.sidecar_sha256,
    }


def _sidecar_uses(
    sidecar: LoadedTaskReplaySidecar, adapter: tuple[str, str, str]
) -> bool:
    return sidecar.registry_key[:2] == adapter[:2]


def _validate_loaded_sidecar(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    source_attestation_key: bytes,
) -> None:
    if hashlib.sha256(sidecar.raw_bytes).hexdigest() != sidecar.sidecar_sha256:
        raise PromotionError("task replay sidecar exact-byte binding is invalid")
    try:
        serialized = json.loads(sidecar.raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromotionError("task replay sidecar exact bytes are invalid") from error
    attestation = (
        serialized.get("attestation") if isinstance(serialized, dict) else None
    )
    if (
        not isinstance(serialized, dict)
        or not isinstance(attestation, dict)
        or attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or attestation.get("role") != "source"
        or serialized != sidecar.signed_sidecar
        or serialized.get("adapter_id") != sidecar.adapter_id
        or serialized.get("adapter_revision") != sidecar.adapter_revision
        or serialized.get("schema_version") != sidecar.sidecar_schema_version
        or serialized.get("replay_payload") != sidecar.replay_payload
        or not verify_attestation(
            serialized,
            source_attestation_key,
            purpose=TASK_REPLAY_SIDECAR_PURPOSE,
        )
    ):
        raise PromotionError("task replay sidecar source attestation is invalid")
    if candidate.get("task_replay_sidecar") != _sidecar_binding(sidecar):
        raise PromotionError("candidate task replay sidecar binding is invalid")


def _validate_sidecar_payload(
    candidate: dict[str, Any], sidecar: LoadedTaskReplaySidecar
) -> None:
    payload = dict(sidecar.replay_payload)
    content_commitments = payload.pop("candidate_content_commitments", None)
    if sidecar.sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        for field in (
            "parent_sidecar_raw_utf8",
            "parent_sidecar_sha256",
            "parent_candidates_raw_utf8",
            "parent_candidates_sha256",
            "parent_candidate_digests",
            "parent_candidate_content_commitments",
            "projection_derivation_revision",
            "projection_derivation_receipts",
        ):
            payload.pop(field, None)
    tokenizer_binding = {
        "tokenizer_model_id": candidate.get("tokenizer_model_id"),
        "tokenizer_revision": candidate.get("tokenizer_revision"),
        "tokenizer_asset_manifest_sha256": candidate.get(
            "tokenizer_asset_manifest_sha256"
        ),
    }
    if _sidecar_uses(sidecar, CYBER_KEV_TASK_REPLAY_ADAPTER):
        if candidate.get("domain") != "cyber":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        replay_manifest = candidate.get("domain_history_replay_manifest")
        if not isinstance(source, dict) or not isinstance(replay_manifest, dict):
            raise PromotionError("candidate Cyber source binding is missing")
        expected = {
            "source_manifest_sha256": source.get("signed_manifest_sha256"),
            "source_response_sha256": source.get("retrieval_sha256"),
            "replay_manifest_sha256": replay_manifest.get("sha256"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
        if (
            replay_manifest.get("source_manifest_sha256")
            != expected["source_manifest_sha256"]
            or replay_manifest.get("source_response_sha256")
            != expected["source_response_sha256"]
            or replay_manifest.get("replay_revision") != expected["replay_revision"]
        ):
            raise PromotionError("candidate Cyber replay manifest is inconsistent")
    elif _sidecar_uses(sidecar, CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER):
        if candidate.get("domain") != "cyber":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        replay_contract = candidate.get("cross_cve_replay_contract")
        if not isinstance(source, dict) or not isinstance(replay_contract, dict):
            raise PromotionError("candidate cross-CVE source binding is missing")
        expected = {
            "source_manifest_sha256": source.get("source_manifest_sha256"),
            "fetch_inventory_sha256": source.get("fetch_inventory_sha256"),
            "authorization_record_id": source.get("authorization_record_id"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
        if (
            replay_contract.get("adapter_id") != sidecar.adapter_id
            or replay_contract.get("revision") != expected["replay_revision"]
        ):
            raise PromotionError("candidate cross-CVE replay contract is inconsistent")
    elif _sidecar_uses(sidecar, FINANCE_TASK_REPLAY_ADAPTER):
        if candidate.get("domain") != "finance":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        replay_contract = candidate.get("finance_replay_contract")
        if not isinstance(source, dict) or not isinstance(replay_contract, dict):
            raise PromotionError("candidate Finance source binding is missing")
        expected = {
            "signed_manifest_sha256": source.get("signed_manifest_sha256"),
            "source_family": source.get("source_family"),
            "authorization_record_id": source.get("authorization_record_id"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
        if (
            replay_contract.get("adapter_id") != sidecar.adapter_id
            or replay_contract.get("revision") != expected["replay_revision"]
        ):
            raise PromotionError("candidate Finance replay contract is inconsistent")
    elif _sidecar_uses(sidecar, MACRO_VINTAGE_TASK_REPLAY_ADAPTER):
        if candidate.get("domain") != "macro_economics":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        if not isinstance(source, dict):
            raise PromotionError("candidate Macro source binding is missing")
        fetch_receipt = payload.get("fetch_receipt")
        if not isinstance(fetch_receipt, dict) or _canonical_sha256(
            fetch_receipt
        ) != source.get("fetch_receipt_sha256"):
            raise PromotionError("candidate Macro source receipt is inconsistent")
        expected = {
            "workflow_manifest_sha256": source.get("workflow_manifest_sha256"),
            "raw_source_sha256": source.get("raw_source_sha256"),
            "fetch_inventory_sha256": source.get("fetch_inventory_sha256"),
            "fetch_receipt": fetch_receipt,
            "source_families": source.get("source_families"),
            "authorization_record_id": source.get("authorization_record_id"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
    else:
        raise PromotionError("task replay adapter is not registered for promotion")
    if (
        expected != payload
        or expected.get("replay_revision") != sidecar.adapter_revision
        or _SHA256.fullmatch(str(expected.get("tokenizer_asset_manifest_sha256") or ""))
        is None
    ):
        raise PromotionError("task replay sidecar payload does not match candidate")
    commitment = task_candidate_content_commitment(
        candidate, sidecar_schema_version=sidecar.sidecar_schema_version
    )
    if (
        not isinstance(content_commitments, list)
        or commitment not in content_commitments
    ):
        raise PromotionError("task replay sidecar does not bind candidate content")


def _validate_candidate_identity(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
    source_attestation_key: bytes,
) -> None:
    if not verify_attestation(
        candidate,
        candidate_attestation_key,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    ):
        raise PromotionError("task candidate attestation is invalid")
    if _CANDIDATE_FORBIDDEN_PROOF_FIELDS.intersection(candidate):
        raise PromotionError("task candidate must not declare proof fields")
    if (
        candidate.get("data_stage") != "candidate"
        or candidate.get("training_objective") != "sft"
    ):
        raise PromotionError("task candidate lifecycle is invalid")
    _validate_loaded_sidecar(candidate, sidecar, source_attestation_key)
    _validate_sidecar_payload(candidate, sidecar)
    if sidecar.sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        _validate_v3_projection_derivation(
            candidate,
            sidecar,
            candidate_attestation_key=candidate_attestation_key,
        )


def _validate_v3_projection_derivation(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
) -> None:
    payload = sidecar.replay_payload
    parent_sha = str(payload.get("parent_sidecar_sha256") or "")
    parents_by_digest: dict[str, dict[str, Any]] = {}
    for parent in sidecar.parent_candidates:
        if (
            not verify_attestation(
                parent,
                candidate_attestation_key,
                purpose=CANDIDATE_ATTESTATION_PURPOSE,
            )
            or (parent.get("task_replay_sidecar") or {}).get("sha256") != parent_sha
            or (parent.get("task_replay_sidecar") or {}).get("sidecar_schema_version")
            != "longworld.task-replay-sidecar.v1"
        ):
            raise PromotionError("task projection parent candidate is not verified")
        parents_by_digest[candidate_sha256(parent)] = parent
    projection = candidate.get("task_view_projection")
    projection_commitment = task_candidate_content_commitment(
        candidate, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
    )
    receipts = payload.get("projection_derivation_receipts")
    matches = [
        receipt
        for receipt in receipts or []
        if isinstance(receipt, dict)
        and receipt.get("projection_content_commitment") == projection_commitment
    ]
    if len(matches) != 1 or not isinstance(projection, dict):
        raise PromotionError("task projection derivation receipt is missing")
    receipt = matches[0]
    parent_digest = str(receipt.get("parent_candidate_sha256") or "")
    parent = parents_by_digest.get(parent_digest)
    parent_documents = str((parent or {}).get("document_context") or "").split(SEP)
    parent_classifications = (parent or {}).get("artifact_classification")
    parent_artifacts: dict[str, dict[str, str]] = {}
    if isinstance(parent_classifications, list) and len(parent_documents) == len(
        parent_classifications
    ):
        parent_artifacts = {
            str(classification.get("artifact_id") or ""): {
                "artifact_id": str(classification.get("artifact_id") or ""),
                "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                "provenance_id": str(classification.get("provenance_id") or ""),
                "source_origin": str(classification.get("source_origin") or ""),
            }
            for classification, document in zip(
                parent_classifications, parent_documents, strict=True
            )
            if isinstance(classification, dict)
        }
    projected_classifications = candidate.get("artifact_classification")
    expected_parent_bindings = (
        [
            parent_artifacts[str(classification.get("artifact_id") or "")]
            for classification in projected_classifications
        ]
        if isinstance(projected_classifications, list)
        and all(
            isinstance(classification, dict)
            and str(classification.get("artifact_id") or "") in parent_artifacts
            for classification in projected_classifications
        )
        else None
    )
    measurement = (
        projection.get("source_token_measurement_receipt")
        if isinstance(projection, dict)
        else None
    )
    contributions = (
        measurement.get("parent_artifact_token_contributions")
        if isinstance(measurement, dict)
        else None
    )
    parent_ratio = (parent or {}).get("real_source_token_ratio")
    measured_ratio = (
        measurement.get("real_source_token_ratio")
        if isinstance(measurement, dict)
        else None
    )
    parent_tokens = (
        measurement.get("parent_document_context_tokens")
        if isinstance(measurement, dict)
        else None
    )
    retained_tokens = (
        measurement.get("retained_parent_document_tokens")
        if isinstance(measurement, dict)
        else None
    )
    final_prompt_tokens = (
        measurement.get("final_prompt_tokens")
        if isinstance(measurement, dict)
        else None
    )
    without_real_prompt_tokens = (
        measurement.get("without_real_prompt_tokens")
        if isinstance(measurement, dict)
        else None
    )
    source_tokens = (
        measurement.get("real_source_marginal_tokens")
        if isinstance(measurement, dict)
        else None
    )
    measurement_valid = bool(
        isinstance(measurement, dict)
        and measurement.get("schema_version") == SOURCE_TOKEN_MEASUREMENT_RECEIPT_SCHEMA
        and measurement.get("measurement_basis") == SOURCE_TOKEN_MEASUREMENT_BASIS
        and measurement.get("parent_real_source_token_ratio") == parent_ratio
        and measured_ratio == candidate.get("real_source_token_ratio")
        and isinstance(parent_ratio, (int, float))
        and not isinstance(parent_ratio, bool)
        and isinstance(measured_ratio, (int, float))
        and not isinstance(measured_ratio, bool)
        and 0.0 < float(measured_ratio) <= 1.0
        and 0.0 < float(parent_ratio) <= 1.0
        and isinstance(parent_tokens, int)
        and not isinstance(parent_tokens, bool)
        and parent_tokens > 0
        and isinstance(retained_tokens, int)
        and not isinstance(retained_tokens, bool)
        and 0 <= retained_tokens <= parent_tokens
        and isinstance(final_prompt_tokens, int)
        and not isinstance(final_prompt_tokens, bool)
        and final_prompt_tokens == candidate.get("tokenizer_context_tokens")
        and isinstance(without_real_prompt_tokens, int)
        and not isinstance(without_real_prompt_tokens, bool)
        and 0 <= without_real_prompt_tokens < final_prompt_tokens
        and isinstance(source_tokens, int)
        and not isinstance(source_tokens, bool)
        and source_tokens == final_prompt_tokens - without_real_prompt_tokens
        and measured_ratio == source_tokens / final_prompt_tokens
        and isinstance(contributions, list)
        and contributions
        and all(
            isinstance(item, dict)
            and isinstance(item.get("token_contribution"), int)
            and not isinstance(item.get("token_contribution"), bool)
            and item["token_contribution"] >= 0
            for item in contributions
        )
        and sum(item["token_contribution"] for item in contributions) == parent_tokens
        and (
            retained_tokens < parent_tokens
            if candidate.get("view") == "cf"
            else retained_tokens == parent_tokens
        )
    )
    if (
        parent is None
        or receipt.get("projection_receipt") != projection
        or receipt.get("projection_receipt_sha256") != _canonical_sha256(projection)
        or receipt.get("parent_content_commitment")
        != task_candidate_content_commitment(parent)
        or projection.get("parent_candidate_sha256") != parent_digest
        or projection.get("parent_artifact_bindings") != expected_parent_bindings
        or projection.get("parent_source_binding_sha256")
        != _canonical_sha256(parent.get("source_binding"))
        or projection.get("parent_task_replay_sidecar")
        != parent.get("task_replay_sidecar")
        or not measurement_valid
    ):
        raise PromotionError("task projection derivation receipt is invalid")


def _adapter_audit(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    token_counter: TokenCounter | None,
) -> dict[str, bool]:
    if token_counter is None:
        raise PromotionError("task promotion requires an exact token counter")
    try:
        with sanitized_attestation_environment():
            observed_tokens = token_counter(str(candidate.get("context") or ""))
            declared_tokens = candidate.get("tokenizer_context_tokens")
            length_bucket = str(candidate.get("length_bucket") or "")
            if (
                isinstance(declared_tokens, bool)
                or not isinstance(declared_tokens, int)
                or length_bucket not in EXACT_TOKEN_BAND_RANGES
                or not exact_token_metadata_valid(
                    candidate, require_asset_manifest=True
                )
                or observed_tokens != declared_tokens
                or exact_token_band_reject_reason(length_bucket, observed_tokens)
            ):
                raise PromotionError("task candidate exact token count does not replay")
            if sidecar.sidecar_schema_version in {
                "longworld.task-replay-sidecar.v2",
                "longworld.task-replay-sidecar.v3",
            }:
                audit = audit_task_view_projection(candidate)
            elif _sidecar_uses(sidecar, CYBER_KEV_TASK_REPLAY_ADAPTER):
                audit = audit_kev_pipeline_candidate(
                    candidate, token_counter=token_counter
                )
            elif _sidecar_uses(sidecar, CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER):
                audit = audit_cross_cve_pipeline_candidate(
                    candidate, token_counter=token_counter
                )
            elif _sidecar_uses(sidecar, FINANCE_TASK_REPLAY_ADAPTER):
                audit = audit_finance_pipeline_candidate(candidate)
            elif _sidecar_uses(sidecar, MACRO_VINTAGE_TASK_REPLAY_ADAPTER):
                audit = audit_macro_vintage_pipeline_candidate(candidate)
            else:
                raise PromotionError(
                    "task replay adapter is not registered for promotion"
                )
    except PromotionError:
        raise
    except Exception as error:
        raise PromotionError("task adapter audit could not be replayed") from error
    failed = sorted(name for name, passed in audit.items() if passed is not True)
    if failed:
        raise PromotionError("task adapter audit failed: " + ",".join(failed))
    return audit


def _replay_selection(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    with sanitized_attestation_environment():
        if _sidecar_uses(sidecar, CYBER_KEV_TASK_REPLAY_ADAPTER):
            return replay_kev_pipeline_candidate(
                canonicalize_kev_projection_candidate(candidate),
                counterfactual=counterfactual,
                evidence_artifact_ids=artifact_ids,
            )
        if _sidecar_uses(sidecar, CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER):
            return replay_cross_cve_pipeline_candidate(
                canonicalize_cross_cve_projection_candidate(candidate),
                counterfactual=counterfactual,
                evidence_artifact_ids=artifact_ids,
            )
        if _sidecar_uses(sidecar, FINANCE_TASK_REPLAY_ADAPTER):
            if sidecar.sidecar_schema_version in {
                "longworld.task-replay-sidecar.v2",
                "longworld.task-replay-sidecar.v3",
            }:
                projection = candidate.get("task_view_projection")
                header = (
                    projection.get("adapter_context_header")
                    if isinstance(projection, Mapping)
                    else None
                )
                if not isinstance(header, str) or not header:
                    return {"answer": "unknown"}
                candidate = dict(candidate)
                candidate["context"] = "\n".join(
                    [
                        header,
                        *str(candidate.get("document_context") or "").split(SEP),
                    ]
                )
            return replay_finance_pipeline_selection(
                candidate,
                artifact_ids,
                counterfactual=counterfactual,
            )
        if _sidecar_uses(sidecar, MACRO_VINTAGE_TASK_REPLAY_ADAPTER):
            return replay_macro_vintage_pipeline_selection(
                normalize_projection_candidate_for_adapter(candidate),
                artifact_ids,
                counterfactual=counterfactual,
            )
    raise PromotionError("task replay adapter is not registered for promotion")


def _artifact_bindings(candidate: dict[str, Any]) -> dict[str, str]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise PromotionError("task candidate artifact pool is missing")
    documents = document_context.split(SEP)
    if not classifications or len(classifications) != len(documents):
        raise PromotionError("task candidate artifact pool is unbound")
    bindings: dict[str, str] = {}
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, Mapping):
            raise PromotionError("task candidate classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id or artifact_id in bindings or not document.strip():
            raise PromotionError("task candidate artifact identities are invalid")
        bindings[artifact_id] = hashlib.sha256(document.encode()).hexdigest()
    return bindings


def _relation_endpoints(relation: object) -> tuple[str, str] | None:
    if isinstance(relation, Mapping):
        parent = relation.get("parent_record_id")
        child = relation.get("child_record_id")
        if isinstance(parent, str) and parent and isinstance(child, str) and child:
            return parent, child
    if (
        isinstance(relation, Sequence)
        and not isinstance(relation, (str, bytes))
        and len(relation) >= 2
        and isinstance(relation[0], str)
        and isinstance(relation[1], str)
        and relation[0]
        and relation[1]
    ):
        return relation[0], relation[1]
    return None


def _relation_proof_depth(relations: Sequence[object]) -> int:
    adjacency: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for relation in relations:
        endpoints = _relation_endpoints(relation)
        if endpoints is None:
            raise PromotionError("task replay relation edge is malformed")
        parent, child = endpoints
        nodes.update((parent, child))
        adjacency.setdefault(parent, set()).add(child)

    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def depth(node: str) -> int:
        if node in memo:
            return memo[node]
        if node in visiting:
            raise PromotionError("task replay relation graph contains a cycle")
        visiting.add(node)
        value = max((1 + depth(child) for child in adjacency.get(node, ())), default=0)
        visiting.remove(node)
        memo[node] = value
        return value

    return max(2, max((depth(node) for node in nodes), default=0))


def _task_selection_metrics(
    candidate: dict[str, Any],
    task_proof: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    token_counter: TokenCounter,
) -> dict[str, Any]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise PromotionError("task selection quality input is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        raise PromotionError("task selection quality input is unbound")
    artifacts = [
        Artifact(
            artifact_id=str(classification.get("artifact_id") or ""),
            doc_type="source_record",
            time=date(1970, 1, 1),
            project=str(candidate.get("world_id") or ""),
            prefix="",
            reveals_events=[],
            text=document,
            facts=[],
        )
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    ]
    receipt = task_proof.get("task_proof_receipt")
    if len(artifacts) != len(documents) or not isinstance(receipt, dict):
        raise PromotionError("task selection growth metadata is invalid")
    essential_ids = receipt.get("essential_artifact_ids")
    if not isinstance(essential_ids, list) or not essential_ids:
        raise PromotionError("task proof essential artifact receipt is invalid")
    artifact_index = {
        str(classification.get("artifact_id") or ""): index
        for index, classification in enumerate(classifications)
        if isinstance(classification, Mapping)
    }
    if any(value not in artifact_index for value in essential_ids):
        raise PromotionError("task proof essential artifacts are unbound")
    essential_documents = [documents[artifact_index[value]] for value in essential_ids]
    internal_tokens = token_counter(document_context)
    proof_tokens = token_counter(SEP.join(essential_documents))

    replay = _replay_selection(candidate, sidecar, list(_artifact_bindings(candidate)))
    authentic = replay.get("authentic_source_relation_edges")
    derived = replay.get("verified_derived_order_relation_edges")
    if derived is None:
        derived = replay.get("verified_derived_relation_edges")
    if not isinstance(authentic, list) or not isinstance(derived, list):
        raise PromotionError("task replay source relations are missing")
    authentic_endpoints = [_relation_endpoints(relation) for relation in authentic]
    derived_endpoints = [_relation_endpoints(relation) for relation in derived]
    if any(
        endpoints is None for endpoints in (*authentic_endpoints, *derived_endpoints)
    ):
        raise PromotionError("task replay relation edge is malformed")
    authentic_pairs = [
        endpoints for endpoints in authentic_endpoints if endpoints is not None
    ]
    relation_pairs = [
        endpoints
        for endpoints in (*authentic_endpoints, *derived_endpoints)
        if endpoints is not None
    ]
    relation_record_ids = {
        record_id for endpoints in relation_pairs for record_id in endpoints
    }
    source_records_by_artifact: Mapping[str, Any]
    if _sidecar_uses(sidecar, CYBER_KEV_TASK_REPLAY_ADAPTER) or _sidecar_uses(
        sidecar, CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER
    ):
        candidate_source_records = candidate.get("source_record_ids_by_artifact")
        if not isinstance(candidate_source_records, Mapping):
            raise PromotionError("Cyber replay artifact source mapping is missing")
        source_records_by_artifact = candidate_source_records
    elif _sidecar_uses(sidecar, FINANCE_TASK_REPLAY_ADAPTER) or _sidecar_uses(
        sidecar, MACRO_VINTAGE_TASK_REPLAY_ADAPTER
    ):
        if "source_record_ids_by_artifact" in candidate:
            raise PromotionError("task candidate source mapping is not authoritative")
        source_records_by_artifact = {}
    else:  # pragma: no cover - sidecar loading rejects this first
        raise PromotionError("task replay adapter is not registered for promotion")
    replay_relation_artifact_ids = {
        artifact_id
        for artifact_id in artifact_index
        if artifact_id in relation_record_ids
        or any(
            str(record_id) in relation_record_ids
            for record_id in source_records_by_artifact.get(artifact_id, ())
        )
    }
    replay_supporting_ids = replay_relation_artifact_ids - set(essential_ids)
    declared_supporting_ids = {
        str(classification.get("artifact_id") or "")
        for classification in classifications
        if isinstance(classification, Mapping)
        and classification.get("evidence_role") == "causal_supporting"
    }
    if declared_supporting_ids != replay_supporting_ids or any(
        value not in artifact_index for value in replay_supporting_ids
    ):
        raise PromotionError(
            "task causal supporting classification does not match replay"
        )
    causal_supporting_documents = [
        documents[artifact_index[value]] for value in sorted(replay_supporting_ids)
    ]
    causal_supporting_tokens = (
        token_counter(SEP.join(causal_supporting_documents))
        if causal_supporting_documents
        else 0
    )
    if (
        any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (internal_tokens, proof_tokens)
        )
        or not isinstance(causal_supporting_tokens, int)
        or isinstance(causal_supporting_tokens, bool)
        or causal_supporting_tokens < 0
        or proof_tokens > internal_tokens
        or causal_supporting_tokens > internal_tokens
    ):
        raise PromotionError("task replay semantic token counts are invalid")
    essential_source_units = {child for _parent, child in authentic_pairs}
    replayed_strict_support = replay.get("strict_support_event_count")
    if _sidecar_uses(sidecar, CYBER_KEV_TASK_REPLAY_ADAPTER):
        group_suffix = "kev-catalog-history"
    elif _sidecar_uses(sidecar, CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER):
        group_suffix = "cross-cve-remediation"
    elif _sidecar_uses(sidecar, FINANCE_TASK_REPLAY_ADAPTER):
        group_suffix = "multi-filing-finance"
    elif _sidecar_uses(sidecar, MACRO_VINTAGE_TASK_REPLAY_ADAPTER):
        group_suffix = "macro-vintage-history"
    else:  # pragma: no cover - sidecar loading rejects this first
        raise PromotionError("task replay adapter is not registered for promotion")
    candidate_graph = candidate.get("graph")
    replay_proof_depth = replay.get("proof_depth")
    replay_hop_count = replay.get("hop_count")
    if (
        not isinstance(candidate_graph, dict)
        or isinstance(replay_proof_depth, bool)
        or not isinstance(replay_proof_depth, int)
        or replay_proof_depth < 2
        or isinstance(replay_hop_count, bool)
        or not isinstance(replay_hop_count, int)
        or replay_hop_count < 2
        or candidate_graph.get("proof_depth") != replay_proof_depth
        or candidate_graph.get("hop_count") != replay_hop_count
    ):
        raise PromotionError("task replay executable graph does not match candidate")
    if (
        isinstance(replayed_strict_support, bool)
        or not isinstance(replayed_strict_support, int)
        or replayed_strict_support < 2
    ):
        raise PromotionError("task replay strict support truth is invalid")
    return {
        "verification_replay_sha256": _canonical_sha256({"task_proof": task_proof}),
        "near_dup_sentence_ratio": round(sentence_near_dup_ratio(artifacts), 4),
        "strict_growth_metrics": {
            "semantic_growth_group_id": f"{candidate['world_id']}|{group_suffix}",
            "semantic_tokens": {
                "internal": internal_tokens,
                "event_bearing": proof_tokens,
                "proof_bearing": proof_tokens,
                "causal_supporting": causal_supporting_tokens,
                "generic_background": 0,
                "measurement_basis": (
                    "exact_pinned_tokenizer_replay_relation_path_"
                    "whole_artifact_upper_bound"
                ),
            },
            "strict_support_event_count": replayed_strict_support,
            "graph": {
                "n_essential_events": len(essential_source_units),
                "n_essential_artifacts": len(essential_ids),
                "proof_depth": replay_proof_depth,
                "hop_count": replay_hop_count,
            },
            "authentic_source_relation_edges": deepcopy(authentic),
        },
    }


def create_task_dense_audit(
    candidate: dict[str, Any],
    external_ranking: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
    ranking_attestation_key: bytes,
    audit_attestation_key: bytes,
    source_attestation_key: bytes,
    k: int = _DENSE_TOP_K,
) -> dict[str, Any]:
    """Replay a signed dense ranking through one source-attested task adapter."""
    _validate_candidate_identity(
        candidate,
        sidecar,
        candidate_attestation_key=candidate_attestation_key,
        source_attestation_key=source_attestation_key,
    )
    if k != _DENSE_TOP_K:
        raise PromotionError("task dense audit requires top-k=3")
    token_counter = task_sidecar_token_counter(sidecar)
    offset_tokenizer = getattr(token_counter, "offset_tokenizer", None)
    if offset_tokenizer is None:
        raise PromotionError("task promotion requires exact tokenizer offsets")
    adapter_audit = _adapter_audit(candidate, sidecar, token_counter)
    try:
        task_proof = compute_task_proof(
            candidate,
            token_counter=token_counter,
            offset_tokenizer=offset_tokenizer,
        )
    except TaskProofError as error:
        raise PromotionError(f"task upstream proof replay failed: {error}") from error
    selection_metrics = _task_selection_metrics(
        candidate, task_proof, sidecar, token_counter
    )
    task_quality_metadata = _task_quality_metadata(candidate, sidecar, token_counter)
    ranked, model = _validate_external_ranking(
        candidate, external_ranking, ranking_attestation_key
    )
    if k >= len(ranked):
        raise PromotionError("task dense top-k must be smaller than the artifact pool")
    ranked_ids = [item["artifact_id"] for item in ranked]
    prefix_answers = [
        str(_replay_selection(candidate, sidecar, ranked_ids[:prefix])["answer"])
        for prefix in range(1, k + 1)
    ]
    full_replay = _replay_selection(candidate, sidecar, ranked_ids)
    counterfactual_replay = _replay_selection(
        candidate, sidecar, ranked_ids, counterfactual=True
    )
    expected_answer = str(candidate.get("answer") or "")
    expected_cf_answer = str(candidate.get("cf_answer") or "")
    if (
        not expected_answer
        or expected_answer in prefix_answers
        or full_replay.get("answer") != expected_answer
    ):
        raise PromotionError("task dense retrieval replay gate failed")
    if (
        not expected_cf_answer
        or counterfactual_replay.get("answer") != expected_cf_answer
        or expected_cf_answer == expected_answer
    ):
        raise PromotionError("task counterfactual replay gate failed")
    payload = {
        "schema_version": PROMOTION_SCHEMA,
        "query_id": candidate["query_id"],
        "candidate_sha256": candidate_sha256(candidate),
        "ranking_sha256": serialized_row_sha256(external_ranking),
        "ranker_type": "dense_embedding",
        "model": model,
        "k": k,
        "top_k": ranked[:k],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "task_replay_adapter_revision": sidecar.adapter_revision,
        "strict_replay_answer": prefix_answers[-1],
        "strict_replay_prefix_answers": prefix_answers,
        "expected_answer": expected_answer,
        "counterfactual_replay_answer": expected_cf_answer,
        "embedding_topk_insufficient": True,
        "full_pool_strict_replay_sufficient": True,
        "task_replay_sidecar": _sidecar_binding(sidecar),
        "task_replay_payload_sha256": _canonical_sha256(sidecar.replay_payload),
        "source_binding_sha256": _canonical_sha256(candidate.get("source_binding")),
        "tokenizer_asset_manifest_sha256": candidate["tokenizer_asset_manifest_sha256"],
        "adapter_audit": adapter_audit,
        "adapter_audit_sha256": _canonical_sha256(adapter_audit),
        "task_proof": task_proof,
        "task_proof_sha256": _canonical_sha256(task_proof),
        "task_quality_metadata": task_quality_metadata,
        **selection_metrics,
    }
    payload["task_semantic_commitment_sha256"] = (
        task_semantic_commitment_sha256_from_audit(payload)
    )
    return attach_attestation(
        payload,
        audit_attestation_key,
        purpose=DENSE_AUDIT_PURPOSE,
    )


def _validate_task_audit(
    candidate: dict[str, Any],
    dense_audit: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    audit_attestation_key: bytes,
    adapter_audit: dict[str, bool],
    task_proof: dict[str, Any],
    token_counter: TokenCounter,
) -> list[str]:
    if not verify_attestation(
        dense_audit, audit_attestation_key, purpose=DENSE_AUDIT_PURPOSE
    ):
        raise PromotionError("task dense audit attestation is invalid")
    top_k = dense_audit.get("top_k")
    model = dense_audit.get("model")
    selection_metrics = _task_selection_metrics(
        candidate, task_proof, sidecar, token_counter
    )
    task_semantic_commitment = task_semantic_commitment_sha256_from_audit(dense_audit)
    task_quality_metadata = _task_quality_metadata(candidate, sidecar, token_counter)
    if (
        dense_audit.get("schema_version") != PROMOTION_SCHEMA
        or dense_audit.get("query_id") != candidate.get("query_id")
        or dense_audit.get("candidate_sha256") != candidate_sha256(candidate)
        or dense_audit.get("ranker_type") != "dense_embedding"
        or dense_audit.get("k") != _DENSE_TOP_K
        or not isinstance(top_k, list)
        or len(top_k) != _DENSE_TOP_K
        or not isinstance(model, dict)
        or dense_audit.get("strict_replay_revision") != STRICT_REPLAY_REVISION
        or dense_audit.get("task_replay_adapter_revision") != sidecar.adapter_revision
        or dense_audit.get("expected_answer") != candidate.get("answer")
        or dense_audit.get("counterfactual_replay_answer") != candidate.get("cf_answer")
        or dense_audit.get("embedding_topk_insufficient") is not True
        or dense_audit.get("full_pool_strict_replay_sufficient") is not True
        or dense_audit.get("task_replay_sidecar") != _sidecar_binding(sidecar)
        or dense_audit.get("task_replay_payload_sha256")
        != _canonical_sha256(sidecar.replay_payload)
        or dense_audit.get("source_binding_sha256")
        != _canonical_sha256(candidate.get("source_binding"))
        or dense_audit.get("tokenizer_asset_manifest_sha256")
        != candidate.get("tokenizer_asset_manifest_sha256")
        or dense_audit.get("adapter_audit") != adapter_audit
        or dense_audit.get("adapter_audit_sha256") != _canonical_sha256(adapter_audit)
        or dense_audit.get("task_proof") != task_proof
        or dense_audit.get("task_proof_sha256") != _canonical_sha256(task_proof)
        or dense_audit.get("task_quality_metadata") != task_quality_metadata
        or dense_audit.get("task_semantic_commitment_sha256")
        != task_semantic_commitment
        or any(
            dense_audit.get(field) != value
            for field, value in selection_metrics.items()
        )
        or _SHA256.fullmatch(str(dense_audit.get("ranking_sha256") or "")) is None
    ):
        raise PromotionError("task dense audit metadata does not match replay")
    bindings = _artifact_bindings(candidate)
    selected_ids: list[str] = []
    for rank, item in enumerate(top_k, start=1):
        if not isinstance(item, dict):
            raise PromotionError("task dense audit top-k is malformed")
        artifact_id = str(item.get("artifact_id") or "")
        chunk_count = item.get("chunk_count")
        if (
            item.get("rank") != rank
            or artifact_id in selected_ids
            or bindings.get(artifact_id) != item.get("text_sha256")
            or not isinstance(chunk_count, int)
            or isinstance(chunk_count, bool)
            or chunk_count < 1
        ):
            raise PromotionError("task dense audit top-k binding is invalid")
        selected_ids.append(artifact_id)
    return selected_ids


def _source_metadata(
    candidate: dict[str, Any], sidecar: LoadedTaskReplaySidecar | None = None
) -> dict[str, Any]:
    classifications = candidate.get("artifact_classification")
    if not isinstance(classifications, list) or not classifications:
        raise PromotionError("task candidate source classifications are missing")
    workflow_ids = sorted(
        {
            str(item.get("workflow_id") or "")
            for item in classifications
            if isinstance(item, dict) and item.get("workflow_id")
        }
    )
    declared_workflows = {
        str(value) for value in candidate.get("workflow_ids") or [] if value
    }
    if declared_workflows and declared_workflows != set(workflow_ids):
        raise PromotionError("task candidate workflow identity is inconsistent")
    if sidecar is not None:
        workflow_ids = [
            "task-source:"
            + _canonical_sha256(
                {
                    "adapter_id": sidecar.adapter_id,
                    "adapter_revision": sidecar.adapter_revision,
                    "source_binding": candidate.get("source_binding"),
                }
            )[:20]
        ]
    source_families = sorted(
        {str(value) for value in candidate.get("source_family_ids") or [] if value}
    )
    if not workflow_ids or not source_families:
        raise PromotionError("task candidate real source identity is incomplete")
    authentic = candidate.get("authentic_source_relation_edges")
    derived = candidate.get("verified_derived_order_relation_edges")
    if derived is None:
        derived = candidate.get("verified_derived_relation_edges")
    if not isinstance(authentic, list) or not isinstance(derived, list):
        raise PromotionError("task candidate source relations are missing")
    relations = [*authentic, *derived]
    relation_id = _canonical_sha256(relations)[:20] if relations else ""
    authentic_relation_id = _canonical_sha256(authentic)[:20] if authentic else ""
    real_source_verified = True
    if candidate.get("domain") == "macro_economics":
        real_source_verified = bool(
            candidate.get("real_source_verified") is True
            and candidate.get("source_attestation_verified") is True
            and isinstance(candidate.get("task_replay_sidecar"), dict)
        )
        if not real_source_verified:
            raise PromotionError("Macro source receipt is not source-attested")
    return {
        "real_source_verified": real_source_verified,
        "real_source_family_ids": source_families,
        "real_source_workflow_ids": workflow_ids,
        "workflow_ids": workflow_ids,
        "source_relation_edges": relations,
        "source_relation_id": relation_id,
        "authentic_source_relation_id": authentic_relation_id,
        "context_source_relation_count": len(relations),
    }


def _task_quality_metadata(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    token_counter: TokenCounter,
) -> dict[str, Any]:
    source = _source_metadata(candidate, sidecar)
    identifiers = _canonical_task_identifiers(candidate, sidecar.registry_key)
    if candidate.get("task_view_projection") is not None and any(
        candidate.get(field) != value for field, value in identifiers.items()
    ):
        raise PromotionError("task candidate semantic identifiers are not canonical")
    real_source_token_ratio = candidate.get("real_source_token_ratio")
    projection = candidate.get("task_view_projection")
    if (
        isinstance(projection, Mapping)
        and sidecar.sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA_V3
    ):
        parent_digest = str(projection.get("parent_candidate_sha256") or "")
        parent_matches = [
            parent
            for parent in sidecar.parent_candidates
            if candidate_sha256(parent) == parent_digest
        ]
        if len(parent_matches) != 1:
            raise PromotionError("task source-token measurement parent is unbound")
        expected_measurement = _source_token_measurement_receipt(
            parent_matches[0], candidate, token_counter
        )
        if (
            projection.get("source_token_measurement_receipt") != expected_measurement
            or real_source_token_ratio
            != expected_measurement["real_source_token_ratio"]
        ):
            raise PromotionError("task source-token measurement receipt is invalid")
        real_source_token_ratio = expected_measurement["real_source_token_ratio"]
    elif candidate.get("view") == "cf":
        documents = str(candidate.get("document_context") or "").split(SEP)
        classifications = candidate.get("artifact_classification")
        if not isinstance(classifications, list) or len(documents) != len(
            classifications
        ):
            raise PromotionError("task CF source-token accounting is unbound")
        real_documents = [
            document
            for classification, document in zip(classifications, documents, strict=True)
            if isinstance(classification, dict)
            and classification.get("source_origin") in _REAL_SOURCE_ORIGINS
        ]
        denominator = token_counter(str(candidate.get("document_context") or ""))
        numerator = token_counter(SEP.join(real_documents)) if real_documents else 0
        if denominator < 1 or not 0 <= numerator < denominator:
            raise PromotionError("task CF source-token accounting is invalid")
        real_source_token_ratio = numerator / denominator
    return {
        "world_id": candidate.get("world_id"),
        "domain": candidate.get("domain"),
        "length_bucket": candidate.get("length_bucket"),
        **identifiers,
        "real_source_verified": source["real_source_verified"],
        "real_source_family_ids": source["real_source_family_ids"],
        "real_source_workflow_ids": source["real_source_workflow_ids"],
        "real_source_token_ratio": real_source_token_ratio,
        "source_relation_edges": source["source_relation_edges"],
        "source_relation_id": source["source_relation_id"],
        "authentic_source_relation_id": source["authentic_source_relation_id"],
        "hybrid_causal_edges": [],
        "context_source_relation_count": source["context_source_relation_count"],
    }


def promote_task_candidate(
    candidate: dict[str, Any],
    dense_audit: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
    audit_attestation_key: bytes,
    promotion_attestation_key: bytes,
    source_attestation_key: bytes,
    expected_split: str | None = None,
    release_selection_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote only a task candidate whose independent upstream gates are green."""
    if (
        candidate.get("task_view_projection") is not None
        and sidecar.sidecar_schema_version != TASK_REPLAY_SIDECAR_SCHEMA_V3
    ):
        raise PromotionError("task view production promotion requires v3 sidecar")
    _validate_candidate_identity(
        candidate,
        sidecar,
        candidate_attestation_key=candidate_attestation_key,
        source_attestation_key=source_attestation_key,
    )
    token_counter = task_sidecar_token_counter(sidecar)
    offset_tokenizer = getattr(token_counter, "offset_tokenizer", None)
    if offset_tokenizer is None:
        raise PromotionError("task promotion requires exact tokenizer offsets")
    adapter_audit = _adapter_audit(candidate, sidecar, token_counter)
    try:
        task_proof = compute_task_proof(
            candidate,
            token_counter=token_counter,
            offset_tokenizer=offset_tokenizer,
        )
        verification = Verification.model_validate(task_proof["verification"])
    except (TaskProofError, KeyError, ValueError) as error:
        raise PromotionError(f"task auditor proof replay failed: {error}") from error
    selected_ids = _validate_task_audit(
        candidate,
        dense_audit,
        sidecar,
        audit_attestation_key=audit_attestation_key,
        adapter_audit=adapter_audit,
        task_proof=task_proof,
        token_counter=token_counter,
    )
    prefix_answers = [
        str(_replay_selection(candidate, sidecar, selected_ids[:prefix])["answer"])
        for prefix in range(1, len(selected_ids) + 1)
    ]
    expected_answer = str(candidate.get("answer") or "")
    if (
        prefix_answers != dense_audit.get("strict_replay_prefix_answers")
        or prefix_answers[-1] != dense_audit.get("strict_replay_answer")
        or expected_answer in prefix_answers
    ):
        raise PromotionError("task dense audit prefix replay mismatch")
    all_ids = list(_artifact_bindings(candidate))
    full_replay = _replay_selection(candidate, sidecar, all_ids)
    counterfactual_replay = _replay_selection(
        candidate, sidecar, all_ids, counterfactual=True
    )
    if full_replay.get("answer") != expected_answer:
        raise PromotionError("task full-pool strict replay mismatch")
    if counterfactual_replay.get("answer") != candidate.get("cf_answer"):
        raise PromotionError("task counterfactual replay mismatch")

    selected_split = expected_split
    selection_digest = ""
    digest = candidate_sha256(candidate)
    task_semantic_commitment = task_semantic_commitment_sha256_from_audit(dense_audit)
    if release_selection_receipt is None:
        raise PromotionError("task promotion requires signed release selection")
    if release_selection_receipt is not None:
        if not verify_attestation(
            release_selection_receipt,
            audit_attestation_key,
            purpose=RELEASE_SELECTION_PURPOSE,
        ):
            raise PromotionError("task release world selection is invalid")
        release_selected_ids = release_selection_receipt.get(
            "selected_candidate_sha256"
        )
        split_by_world = release_selection_receipt.get("split_by_world")
        audit_sha256_by_candidate = release_selection_receipt.get(
            "audit_sha256_by_candidate"
        )
        task_semantic_commitment_by_candidate = release_selection_receipt.get(
            "task_semantic_commitment_sha256_by_candidate"
        )
        mapped_split = (
            split_by_world.get(str(candidate.get("world_id") or ""))
            if isinstance(split_by_world, dict)
            else None
        )
        if (
            release_selection_receipt.get("schema_version") != RELEASE_SELECTION_SCHEMA
            or not isinstance(release_selected_ids, list)
            or digest not in release_selected_ids
            or not isinstance(audit_sha256_by_candidate, dict)
            or audit_sha256_by_candidate.get(digest)
            != serialized_row_sha256(dense_audit)
            or not isinstance(task_semantic_commitment_by_candidate, dict)
            or task_semantic_commitment_by_candidate.get(digest)
            != task_semantic_commitment
            or mapped_split not in {"train", "eval"}
            or (expected_split is not None and expected_split != mapped_split)
            or release_selection_receipt.get("tokenizer_asset_manifest_sha256")
            != candidate.get("tokenizer_asset_manifest_sha256")
        ):
            raise PromotionError("task candidate is not bound by world selection")
        selected_split = str(mapped_split)
        selection_digest = serialized_row_sha256(release_selection_receipt)
    promoted = deepcopy(candidate)
    promoted.pop("attestation", None)
    promoted.pop("promotion_blocker_code", None)
    promoted.update(_source_metadata(candidate, sidecar))
    [canonical_workflow_id] = promoted["workflow_ids"]
    for classification in promoted.get("artifact_classification") or []:
        if isinstance(classification, dict):
            classification["workflow_id"] = canonical_workflow_id
    promoted.update(deepcopy(dense_audit["task_quality_metadata"]))
    growth = dense_audit["strict_growth_metrics"]
    promoted["semantic_growth_group_id"] = growth["semantic_growth_group_id"]
    promoted["semantic_tokens"] = deepcopy(growth["semantic_tokens"])
    promoted["strict_support_event_count"] = growth["strict_support_event_count"]
    if growth["authentic_source_relation_edges"] != candidate.get(
        "authentic_source_relation_edges"
    ):
        raise PromotionError("task audited source relations do not match replay")
    candidate_graph = candidate.get("graph")
    if (
        not isinstance(candidate_graph, dict)
        or growth["graph"].get("proof_depth") != candidate_graph.get("proof_depth")
        or growth["graph"].get("hop_count") != candidate_graph.get("hop_count")
    ):
        raise PromotionError("task audited graph does not match executable replay")
    promoted["graph"] = {
        **deepcopy(candidate_graph),
        "n_essential_events": growth["graph"]["n_essential_events"],
        "n_essential_artifacts": growth["graph"]["n_essential_artifacts"],
    }
    promoted["hop_count"] = int(candidate_graph["hop_count"])
    promoted["task_proof_receipt"] = task_proof["task_proof_receipt"]
    promoted["verification"] = task_proof["verification"]
    promoted["view_verification"] = task_proof["view_verification"]
    promoted["data_stage"] = "train_ready"
    promoted["train_ready"] = True
    promoted["promotion_eligible"] = True
    promoted["promoted"] = True
    promoted.pop("complete_world", None)
    promoted["generation_integration"] = "task_replay_promotion_v1"
    capabilities = dict(promoted.get("pipeline_capabilities") or {})
    capabilities["generic_promotion"] = True
    promoted["pipeline_capabilities"] = capabilities
    if selected_split is not None:
        promoted["split"] = selected_split
        promoted["split_strategy"] = "world"
        promoted["holdout"] = {
            "strategy": "world",
            "group_id": hashlib.sha256(
                f"world|{candidate['world_id']}".encode()
            ).hexdigest()[:16],
        }
    classifications = promoted["artifact_classification"]
    promoted["source_origins"] = sorted(
        {
            str(item.get("source_origin") or "")
            for item in classifications
            if isinstance(item, dict) and item.get("source_origin")
        }
    )
    promoted["workflow_kinds"] = sorted(
        {
            str(item.get("workflow_kind") or "")
            for item in classifications
            if isinstance(item, dict) and item.get("workflow_kind")
        }
    )
    verification.production_mode = True
    verification.candidate_mode = False
    verification.embedding_topk_insufficient = True
    if not verification.all_green():
        raise PromotionError("production task verification gates are not green")
    promoted["verification"] = verification.model_dump()
    diagnostic_metadata = local_probe_diagnostic_metadata()
    if diagnostic_metadata:
        promoted.update(diagnostic_metadata)
    else:
        # ``production_eligible`` is a diagnostic-boundary field in the row
        # contract.  Production rows declare eligibility on the exact view.
        promoted.pop("production_eligible", None)
    view = dict(promoted["view_verification"])
    view["expected_answer"] = expected_answer
    view["strict_replay_answer"] = expected_answer
    view["production_eligible"] = not bool(diagnostic_metadata)
    if diagnostic_metadata:
        view["content_gate_eligible"] = True
    promoted["view_verification"] = view
    model = dense_audit["model"]
    promoted["promotion"] = {
        "schema_version": PROMOTION_SCHEMA,
        "candidate_sha256": digest,
        "dense_audit_sha256": serialized_row_sha256(dense_audit),
        "dense_model_provider": model.get("provider"),
        "dense_model_id": model.get("model_id"),
        "dense_model_revision": model.get("revision"),
        "dense_model_backend": model.get("backend"),
        "dense_score_metric": model.get("score_metric"),
        "dense_chunking": model.get("chunking"),
        "dense_ranking_sha256": dense_audit["ranking_sha256"],
        "dense_top_k": dense_audit["k"],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "strict_replay_answer": expected_answer,
        "tokenizer_asset_manifest_sha256": candidate["tokenizer_asset_manifest_sha256"],
        "real_source_verified": _source_metadata(candidate, sidecar)[
            "real_source_verified"
        ],
        "task_replay_sidecar": _sidecar_binding(sidecar),
        "task_candidate_content_commitment": task_candidate_content_commitment(
            candidate, sidecar_schema_version=sidecar.sidecar_schema_version
        ),
        "task_semantic_commitment_sha256": task_semantic_commitment,
    }
    if selection_digest:
        promoted["promotion"]["release_selection_sha256"] = selection_digest
    signed = attach_attestation(
        promoted,
        promotion_attestation_key,
        purpose="sft_row",
    )
    errors = sft_row_errors(signed, attestation_key=promotion_attestation_key)
    if errors:
        raise PromotionError("promoted task row contract failed: " + ",".join(errors))
    return signed


TASK_VIEW_PROJECTION_SCHEMA = "longworld.task-view-projection.v1"
_STANDARD_TASK_VIEWS = ("full", "cf", "ordered_artifact_view")
_STANDARD_VIEW_COMPOSITIONS = {
    "full": "same_case_dossier",
    "cf": "counterfactual_twin",
    "ordered_artifact_view": "causal_timeline",
}
_REAL_SOURCE_ORIGINS = frozenset({"real_public", "real_private_export", "real_derived"})


def _normalized_relation_topology(candidate: Mapping[str, Any]) -> dict[str, Any]:
    edges: list[tuple[str, str, str, str]] = []
    relation_fields = (
        ("authentic", candidate.get("authentic_source_relation_edges")),
        (
            "derived",
            candidate.get("verified_derived_order_relation_edges")
            or candidate.get("verified_derived_relation_edges"),
        ),
    )
    for provenance, relations in relation_fields:
        if not isinstance(relations, list):
            continue
        for relation in relations:
            if isinstance(relation, list) and len(relation) >= 2:
                parent, child = str(relation[0]), str(relation[1])
                operator = (
                    str(relation[2]).split(":", 1)[0] if len(relation) > 2 else ""
                )
            elif isinstance(relation, dict):
                parent = str(
                    relation.get("parent_record_id")
                    or relation.get("source_record_id")
                    or ""
                )
                child = str(
                    relation.get("child_record_id")
                    or relation.get("target_record_id")
                    or ""
                )
                operator = str(
                    relation.get("relation_provenance")
                    or relation.get("relation_type")
                    or relation.get("operator")
                    or relation.get("type")
                    or ""
                )
            else:
                continue
            if parent and child:
                edges.append((parent, child, provenance, operator))
    indegree: Counter[str] = Counter(child for _parent, child, _kind, _op in edges)
    outdegree: Counter[str] = Counter(parent for parent, _child, _kind, _op in edges)
    nodes = set(indegree) | set(outdegree)
    return {
        "edge_count": len(edges),
        "node_degree_multiset": sorted(
            (indegree[node], outdegree[node]) for node in nodes
        ),
        "typed_edge_degree_multiset": sorted(
            (
                provenance,
                operator,
                indegree[parent],
                outdegree[parent],
                indegree[child],
                outdegree[child],
            )
            for parent, child, provenance, operator in edges
        ),
    }


def _canonical_task_identifiers(
    candidate: Mapping[str, Any], adapter_key: tuple[str, str, str]
) -> dict[str, str]:
    family = adapter_key[:2]
    if family == FINANCE_TASK_REPLAY_ADAPTER[:2]:
        finance_task = candidate.get("finance_task")
        if not isinstance(finance_task, Mapping):
            raise PromotionError("finance answer program is unsupported")
        answer_program_id = str(finance_task.get("answer_program_id") or "")
        finance_programs = {
            "finance.multi_filing_reconstruction.v1": (
                "multi_filing_trajectory+certification+cross_statement_reconciliation",
                "multi_filing_financial_reconstruction",
                (
                    "source_span_parse",
                    "cross_filing_trajectory",
                    "balance_sheet_certification",
                    "cashflow_reconciliation",
                    "operating_margin_reconciliation",
                ),
                (
                    "select_filing_chain",
                    "reconcile_financial_facts",
                    "verify_balance_cashflow_margin",
                ),
            ),
            "finance.multi_filing_asset_trajectory.v1": (
                "multi_filing_asset_and_operating_cash_trajectory+balance_sheet_certification",
                "multi_filing_asset_trajectory",
                (
                    "source_span_parse",
                    "cross_filing_revenue_trajectory",
                    "cross_filing_asset_trajectory",
                    "cross_filing_operating_cash_trajectory",
                    "balance_sheet_certification",
                ),
                (
                    "select_filing_chain",
                    "reconcile_revenue_assets_and_operating_cash",
                    "verify_balance_sheet",
                ),
            ),
        }
        program = finance_programs.get(answer_program_id)
        if (
            program is None
            or candidate.get("answer_program_id") != answer_program_id
            or finance_task.get("query_type") != program[1]
            or finance_task.get("answer_program_operations") != list(program[2])
        ):
            raise PromotionError("finance answer program is unsupported")
        motif = program[0]
        program_ops = program[3]
    elif family == CYBER_KEV_TASK_REPLAY_ADAPTER[:2]:
        motif = "chronology+annual_aggregation+remediation_window"
        answer_program_id = "cyber.kev_catalog_chronology_audit.v1"
        program_ops = (
            "sort_date_added_then_cve",
            "annual_checkpoints",
            "maximum_remediation_window",
        )
    elif family == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[:2]:
        motif = "nvd_kev_join+vendor_year_aggregation+remediation_window"
        answer_program_id = "cyber.cross_cve_remediation_reconstruction.v1"
        program_ops = (
            "JOIN_NVD_CISA_BY_CVE",
            "ORDER_DATE_THEN_CVE",
            "GROUP_VENDOR_AND_YEAR",
            "COUNT_KNOWN_RANSOMWARE",
            "SELECT_MAX_REMEDIATION_WINDOW",
        )
    elif family == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
        motif = "revision_path+unchanged_supersession+as_of_reconstruction"
        answer_program_id = "macro.as_of_revision_path.v2"
        program_ops = (
            "select_as_of_vintages",
            "trace_changed_and_unchanged_revisions",
            "compute_cumulative_revision_delta",
        )
    else:
        raise PromotionError("task semantic identifier adapter is unsupported")
    semantic_base_task_id = _canonical_sha256(
        {
            "adapter_id": family[0],
            "adapter_revision": family[1],
            "answer_program_id": answer_program_id,
            "program_ops": program_ops,
            "counterfactual_operation": (
                candidate.get("counterfactual_twin") or {}
            ).get("provenance_operation"),
        }
    )[:20]
    graph = candidate.get("graph") if isinstance(candidate.get("graph"), dict) else {}
    executable_proof_id = _canonical_sha256(
        {
            "adapter_id": family[0],
            "answer_program_id": answer_program_id,
            "program_ops": program_ops,
            "essential_artifact_count": len(
                candidate.get("essential_artifact_ids") or []
            ),
            "relation_topology": _normalized_relation_topology(candidate),
            "proof_depth": graph.get("proof_depth"),
            "hop_count": graph.get("hop_count"),
            "counterfactual_operation": (
                candidate.get("counterfactual_twin") or {}
            ).get("provenance_operation"),
        }
    )[:20]
    return {
        "motif": motif,
        "base_task_id": _canonical_sha256(
            {
                "world_id": candidate.get("world_id"),
                "semantic_base_task_id": semantic_base_task_id,
            }
        )[:20],
        "executable_proof_id": executable_proof_id,
        "answer_program_id": answer_program_id,
        "semantic_base_task_id": semantic_base_task_id,
    }


def _counterfactual_classification(
    candidate: Mapping[str, Any],
    classification: Mapping[str, Any],
    *,
    parent_document: str,
    projected_document: str,
) -> dict[str, Any]:
    parent_provenance = str(classification.get("provenance_id") or "")
    parent_origin = str(classification.get("source_origin") or "")
    source_binding = candidate.get("source_binding")
    parent_sidecar = candidate.get("task_replay_sidecar")
    if (
        parent_origin not in _REAL_SOURCE_ORIGINS
        or not parent_provenance
        or not isinstance(source_binding, Mapping)
        or not isinstance(parent_sidecar, Mapping)
        or not str(parent_sidecar.get("sha256") or "")
    ):
        raise PromotionError("task counterfactual parent source binding is invalid")
    output = deepcopy(dict(classification))
    output.update(
        {
            "source_origin": "synthetic_counterfactual",
            "workflow_kind": "hybrid_causal",
            "counterfactual_parent_source_origin": parent_origin,
            "counterfactual_parent_provenance_id": parent_provenance,
            "counterfactual_parent_text_sha256": hashlib.sha256(
                parent_document.encode()
            ).hexdigest(),
            "counterfactual_parent_source_binding_sha256": _canonical_sha256(
                source_binding
            ),
            "counterfactual_parent_sidecar_sha256": str(parent_sidecar["sha256"]),
            "provenance_id": "counterfactual-projection-sha256:"
            + hashlib.sha256(projected_document.encode()).hexdigest(),
        }
    )
    return output


def _task_view_artifacts(
    candidate: Mapping[str, Any],
) -> list[tuple[dict[str, Any], str]]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise PromotionError("task view projection artifact pool is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications) or not documents:
        raise PromotionError("task view projection artifact pool is unbound")
    output: list[tuple[dict[str, Any], str]] = []
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, dict) or not document.strip():
            raise PromotionError("task view projection artifact is malformed")
        output.append((deepcopy(classification), document))
    return output


def _source_token_measurement_receipt(
    parent: Mapping[str, Any],
    projected: Mapping[str, Any],
    token_counter: TokenCounter,
) -> dict[str, Any]:
    parent_ratio = parent.get("real_source_token_ratio")
    if (
        isinstance(parent_ratio, bool)
        or not isinstance(parent_ratio, (int, float))
        or not 0.0 < float(parent_ratio) <= 1.0
        or any(
            not str(parent.get(field) or "")
            or projected.get(field) != parent.get(field)
            for field in (
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
            )
        )
    ):
        raise PromotionError("task parent source-token measurement is invalid")
    parent_artifacts = _task_view_artifacts(parent)
    projected_artifacts = _task_view_artifacts(projected)
    projected_by_id = {
        str(classification.get("artifact_id") or ""): (classification, document)
        for classification, document in projected_artifacts
    }
    if len(projected_by_id) != len(projected_artifacts) or set(projected_by_id) != {
        str(classification.get("artifact_id") or "")
        for classification, _document in parent_artifacts
    }:
        raise PromotionError("task projected source-token artifacts are unbound")
    contributions: list[dict[str, Any]] = []
    previous_tokens = 0
    retained_tokens = 0
    parent_documents: list[str] = []
    for parent_classification, parent_document in parent_artifacts:
        artifact_id = str(parent_classification.get("artifact_id") or "")
        projected_classification, projected_document = projected_by_id[artifact_id]
        parent_documents.append(parent_document)
        cumulative_tokens = token_counter(SEP.join(parent_documents))
        contribution = cumulative_tokens - previous_tokens
        previous_tokens = cumulative_tokens
        retained_as_real = bool(
            projected_classification.get("source_origin") in _REAL_SOURCE_ORIGINS
            and projected_classification.get("source_origin")
            == parent_classification.get("source_origin")
            and projected_classification.get("provenance_id")
            == parent_classification.get("provenance_id")
            and projected_document == parent_document
        )
        if (
            isinstance(cumulative_tokens, bool)
            or not isinstance(cumulative_tokens, int)
            or contribution < 0
        ):
            raise PromotionError("task parent source-token contributions are invalid")
        if retained_as_real:
            retained_tokens += contribution
        contributions.append(
            {
                "artifact_id": artifact_id,
                "parent_text_sha256": hashlib.sha256(
                    parent_document.encode()
                ).hexdigest(),
                "token_contribution": contribution,
                "retained_as_real": retained_as_real,
            }
        )
    parent_tokens = previous_tokens
    view = projected.get("view")
    retained_range_valid = (
        0 < retained_tokens < parent_tokens
        if view == "cf"
        else retained_tokens == parent_tokens
    )
    if parent_tokens < 1 or not retained_range_valid:
        raise PromotionError("task parent source-token measurement is invalid")
    projected_context = str(projected.get("document_context") or "")
    question = str(projected.get("question") or "")
    timing = str(projected.get("query_timing") or "")
    rendered_prompt = wrap_prompt(question, projected_context, timing)
    if rendered_prompt != projected.get("context"):
        raise PromotionError("task projected prompt is not canonically rendered")
    without_real_context = SEP.join(
        document
        for classification, document in projected_artifacts
        if classification.get("source_origin") not in _REAL_SOURCE_ORIGINS
    )
    without_real_prompt = wrap_prompt(question, without_real_context, timing)
    final_prompt_tokens = token_counter(rendered_prompt)
    without_real_prompt_tokens = token_counter(without_real_prompt)
    source_tokens = final_prompt_tokens - without_real_prompt_tokens
    if final_prompt_tokens < 1 or not 0 < source_tokens <= final_prompt_tokens:
        raise PromotionError("task final prompt source-token marginal is invalid")
    measured_ratio = source_tokens / final_prompt_tokens
    return {
        "schema_version": SOURCE_TOKEN_MEASUREMENT_RECEIPT_SCHEMA,
        "measurement_basis": SOURCE_TOKEN_MEASUREMENT_BASIS,
        "tokenizer_model_id": parent.get("tokenizer_model_id"),
        "tokenizer_revision": parent.get("tokenizer_revision"),
        "tokenizer_asset_manifest_sha256": parent.get(
            "tokenizer_asset_manifest_sha256"
        ),
        "parent_real_source_token_ratio": parent_ratio,
        "parent_document_context_tokens": parent_tokens,
        "projected_document_context_tokens": token_counter(projected_context),
        "retained_parent_document_tokens": retained_tokens,
        "parent_artifact_token_contributions": contributions,
        "final_prompt_tokens": final_prompt_tokens,
        "without_real_prompt_tokens": without_real_prompt_tokens,
        "real_source_marginal_tokens": source_tokens,
        "real_source_token_ratio": measured_ratio,
    }


def _finance_counterfactual_artifacts(
    candidate: Mapping[str, Any],
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    twin = candidate.get("counterfactual_twin")
    if (
        not isinstance(twin, Mapping)
        or twin.get("provenance_operation") != "replace_exact_span"
        or twin.get("source_origin") != "synthetic_counterfactual"
    ):
        raise PromotionError("Finance task counterfactual binding is invalid")
    changed = 0
    output: list[tuple[dict[str, Any], str]] = []
    for classification, document in artifacts:
        parent_document = document
        try:
            record = json.loads(document)
        except json.JSONDecodeError as error:
            raise PromotionError("Finance task artifact is not JSON") from error
        if not isinstance(record, dict):
            raise PromotionError("Finance task artifact is malformed")
        if record.get("source_record_id") == twin.get("record_id"):
            facts = record.get("facts")
            matches = [
                value
                for value in facts or []
                if isinstance(value, dict) and value.get("role") == twin.get("role")
            ]
            if len(matches) != 1:
                raise PromotionError("Finance task counterfactual fact is ambiguous")
            fact = matches[0]
            start = fact.get("relative_start")
            parent = twin.get("parent_value")
            replacement = twin.get("value")
            source_text = record.get("source_text")
            if (
                not isinstance(start, int)
                or not isinstance(parent, str)
                or not isinstance(replacement, str)
                or not isinstance(source_text, str)
                or len(parent) != len(replacement)
                or source_text[start : start + len(parent)] != parent
                or parent == replacement
            ):
                raise PromotionError("Finance task counterfactual span is invalid")
            record["source_text"] = (
                source_text[:start] + replacement + source_text[start + len(parent) :]
            )
            record["source_text_sha256"] = hashlib.sha256(
                record["source_text"].encode()
            ).hexdigest()
            fact["evidence_quote"] = replacement
            document = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            classification = _counterfactual_classification(
                candidate,
                classification,
                parent_document=parent_document,
                projected_document=document,
            )
            changed += 1
        output.append((classification, document))
    if changed != 1:
        raise PromotionError("Finance task counterfactual target is not unique")
    return output


def _finance_chronology(
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[str, dict[str, Any], str]]:
    parsed: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    filings: dict[str, str] = {}
    for classification, document in artifacts:
        try:
            record = json.loads(document)
        except json.JSONDecodeError as error:
            raise PromotionError(
                "Finance task chronology artifact is not JSON"
            ) from error
        if not isinstance(record, dict):
            raise PromotionError("Finance task chronology artifact is malformed")
        if record.get("record_type") == "filing":
            filings[str(record.get("source_record_id") or "")] = str(
                record.get("filing_date") or ""
            )
        parsed.append((classification, record, document))
    ordered: list[tuple[str, dict[str, Any], str]] = []
    for classification, record, document in parsed:
        record_type = str(record.get("record_type") or "")
        if record_type == "financial_source_row":
            occurred_at = str(record.get("report_date") or "")
            kind_order = "0"
        elif record_type == "filing":
            occurred_at = str(record.get("filing_date") or "")
            kind_order = "1"
        elif record_type == "filing_relation":
            occurred_at = filings.get(str(record.get("source_record_id") or ""), "")
            kind_order = "2"
        else:
            raise PromotionError("Finance task chronology record type is unsupported")
        artifact_id = str(classification.get("artifact_id") or "")
        if not occurred_at or not artifact_id:
            raise PromotionError("Finance task chronology identity is incomplete")
        ordered.append(
            (f"{occurred_at}|{kind_order}|{artifact_id}", classification, document)
        )
    return sorted(ordered, key=lambda value: value[0])


def _cyber_counterfactual_artifacts(
    candidate: Mapping[str, Any],
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    twin = candidate.get("counterfactual_twin")
    if (
        not isinstance(twin, Mapping)
        or twin.get("provenance_operation") != "replace_due_date"
        or twin.get("source_origin") != "synthetic_counterfactual"
    ):
        raise PromotionError("Cyber task counterfactual binding is invalid")
    changed = 0
    output: list[tuple[dict[str, Any], str]] = []
    for classification, document in artifacts:
        records: list[dict[str, Any]] = []
        for line in document.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise PromotionError("Cyber task artifact is not JSONL") from error
            if not isinstance(record, dict):
                raise PromotionError("Cyber task artifact is malformed")
            payload = record.get("source_payload")
            if record.get("source_record_id") == twin.get("record_id"):
                if (
                    not isinstance(payload, dict)
                    or payload.get("dueDate") != twin.get("parent_value")
                    or not isinstance(twin.get("value"), str)
                    or twin.get("value") == twin.get("parent_value")
                ):
                    raise PromotionError("Cyber task counterfactual fact is invalid")
                payload["dueDate"] = twin["value"]
                changed += 1
            records.append(record)
        projected = "\n".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for record in records
        )
        if projected != document:
            classification = _counterfactual_classification(
                candidate,
                classification,
                parent_document=document,
                projected_document=projected,
            )
        output.append((classification, projected))
    if changed != 1:
        raise PromotionError("Cyber task counterfactual target is not unique")
    return output


def _cyber_chronology(
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[str, dict[str, Any], str]]:
    output: list[tuple[str, dict[str, Any], str]] = []
    previous_end = ""
    for classification, document in artifacts:
        dates: list[str] = []
        for line in document.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise PromotionError(
                    "Cyber task chronology artifact is not JSONL"
                ) from error
            if not isinstance(record, dict):
                raise PromotionError("Cyber task chronology artifact is malformed")
            payload = record.get("source_payload")
            if isinstance(payload, Mapping):
                date_added = str(payload.get("dateAdded") or "")
                if not date_added:
                    raise PromotionError("Cyber task chronology date is missing")
                dates.append(date_added)
        if dates != sorted(dates):
            raise PromotionError("Cyber task artifact records are not chronological")
        start = dates[0] if dates else "0000-00-00"
        end = dates[-1] if dates else start
        if previous_end and start < previous_end:
            raise PromotionError("Cyber task artifact shards overlap chronology")
        previous_end = end
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id:
            raise PromotionError("Cyber task chronology identity is incomplete")
        output.append((f"{start}|{end}|{artifact_id}", classification, document))
    return sorted(output, key=lambda value: value[0])


def _cross_cve_counterfactual_artifacts(
    candidate: Mapping[str, Any],
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    twin = candidate.get("counterfactual_twin")
    if (
        not isinstance(twin, Mapping)
        or twin.get("provenance_operation") != "replace_due_date"
        or twin.get("source_origin") != "synthetic_counterfactual"
    ):
        raise PromotionError("cross-CVE task counterfactual binding is invalid")
    changed = 0
    output: list[tuple[dict[str, Any], str]] = []
    for classification, document in artifacts:
        records: list[dict[str, Any]] = []
        for line in document.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise PromotionError("cross-CVE task artifact is not JSONL") from error
            if not isinstance(record, dict):
                raise PromotionError("cross-CVE task artifact is malformed")
            if (
                record.get("record_id") == twin.get("record_id")
                and record.get("kind") == "cisa_kev_entry"
            ):
                try:
                    payload = json.loads(str(record.get("text") or ""))
                except json.JSONDecodeError as error:
                    raise PromotionError(
                        "cross-CVE task counterfactual payload is invalid"
                    ) from error
                if (
                    not isinstance(payload, dict)
                    or payload.get("dueDate") != twin.get("parent_value")
                    or not isinstance(twin.get("value"), str)
                    or twin.get("value") == twin.get("parent_value")
                ):
                    raise PromotionError(
                        "cross-CVE task counterfactual fact is invalid"
                    )
                payload["dueDate"] = twin["value"]
                record = dict(record)
                record["text"] = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                record["text_sha256"] = hashlib.sha256(
                    record["text"].encode()
                ).hexdigest()
                changed += 1
            records.append(record)
        projected = "\n".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for record in records
        )
        if projected != document:
            classification = _counterfactual_classification(
                candidate,
                classification,
                parent_document=document,
                projected_document=projected,
            )
        output.append((classification, projected))
    if changed != 1:
        raise PromotionError("cross-CVE task counterfactual target is not unique")
    return output


def _cross_cve_chronology(
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[str, dict[str, Any], str]]:
    cve_dates: dict[str, str] = {}
    parsed: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    for classification, document in artifacts:
        records: list[dict[str, Any]] = []
        for line in document.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise PromotionError(
                    "cross-CVE chronology artifact is not JSONL"
                ) from error
            if not isinstance(record, dict):
                raise PromotionError("cross-CVE chronology artifact is malformed")
            records.append(record)
            if record.get("kind") == "cisa_kev_entry":
                try:
                    payload = json.loads(str(record.get("text") or ""))
                except json.JSONDecodeError as error:
                    raise PromotionError(
                        "cross-CVE chronology payload is malformed"
                    ) from error
                date_added = (
                    str(payload.get("dateAdded") or "")
                    if isinstance(payload, dict)
                    else ""
                )
                cve_id = str(record.get("cve_id") or "")
                if not date_added or not cve_id:
                    raise PromotionError("cross-CVE chronology date is missing")
                cve_dates[cve_id] = date_added
        parsed.append((classification, document, records))
    output: list[tuple[str, dict[str, Any], str]] = []
    for classification, document, records in parsed:
        dates: list[str] = []
        cve_ids: list[str] = []
        for record in records:
            cve_id = str(
                record.get("cve_id")
                or str(record.get("relation_id") or "").removeprefix(
                    "cyber:listed-in-kev:"
                )
            )
            date_added = cve_dates.get(cve_id, "")
            if not cve_id or not date_added:
                raise PromotionError("cross-CVE chronology identity is incomplete")
            dates.append(date_added)
            cve_ids.append(cve_id)
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id:
            raise PromotionError("cross-CVE chronology identity is incomplete")
        start = min(dates)
        output.append(
            (f"{start}|{min(cve_ids)}|{artifact_id}", classification, document)
        )
    return sorted(output, key=lambda value: value[0])


def _macro_counterfactual_artifacts(
    candidate: Mapping[str, Any],
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    twin = candidate.get("counterfactual_twin")
    if (
        not isinstance(twin, Mapping)
        or twin.get("provenance_operation") != "replace_exact_fact"
        or twin.get("source_origin") != "synthetic_counterfactual"
    ):
        raise PromotionError("Macro task counterfactual binding is invalid")
    changed = 0
    output: list[tuple[dict[str, Any], str]] = []
    for classification, document in artifacts:
        try:
            record = json.loads(document)
        except json.JSONDecodeError as error:
            raise PromotionError("Macro task artifact is not JSON") from error
        if not isinstance(record, dict):
            raise PromotionError("Macro task artifact is malformed")
        payload = record.get("source_payload")
        if (
            record.get("record_type") == "macro_vintage_observation"
            and isinstance(payload, dict)
            and payload.get("observation_id") == twin.get("record_id")
        ):
            if (
                payload.get("value") != twin.get("parent_value")
                or not isinstance(twin.get("value"), str)
                or twin.get("value") == twin.get("parent_value")
            ):
                raise PromotionError("Macro task counterfactual fact is invalid")
            payload["value"] = twin["value"]
            changed += 1
        projected = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if projected != document:
            classification = _counterfactual_classification(
                candidate,
                classification,
                parent_document=document,
                projected_document=projected,
            )
        output.append((classification, projected))
    if changed != 1:
        raise PromotionError("Macro task counterfactual target is not unique")
    return output


def _macro_chronology(
    artifacts: Sequence[tuple[dict[str, Any], str]],
) -> list[tuple[str, dict[str, Any], str]]:
    output: list[tuple[str, dict[str, Any], str]] = []
    for classification, document in artifacts:
        try:
            record = json.loads(document)
        except json.JSONDecodeError as error:
            raise PromotionError(
                "Macro task chronology artifact is not JSON"
            ) from error
        if not isinstance(record, dict):
            raise PromotionError("Macro task chronology artifact is malformed")
        payload = record.get("source_payload")
        if not isinstance(payload, Mapping):
            raise PromotionError("Macro task chronology payload is malformed")
        if record.get("record_type") == "macro_vintage_observation":
            occurred_at = str(payload.get("vintage_date") or "")
            kind_order = "0"
        elif record.get("record_type") == "macro_vintage_relation":
            occurred_at = str(payload.get("source_vintage_date") or "")
            kind_order = "1"
        else:
            raise PromotionError("Macro task chronology record type is unsupported")
        artifact_id = str(classification.get("artifact_id") or "")
        if not occurred_at or not artifact_id:
            raise PromotionError("Macro task chronology identity is incomplete")
        output.append(
            (f"{occurred_at}|{kind_order}|{artifact_id}", classification, document)
        )
    return sorted(output, key=lambda value: value[0])


def _task_view_difficulty(
    *,
    question: str,
    artifacts: Sequence[tuple[dict[str, Any], str]],
    context: str,
    token_counter: TokenCounter,
    proof_depth: int,
) -> dict[str, int]:
    essential_positions = [
        index
        for index, (classification, _document) in enumerate(artifacts)
        if classification.get("evidence_role") == "causal_gold"
    ]
    if not essential_positions:
        raise PromotionError("task view has no causal gold artifacts")
    prefix = prompt_document_prefix(question, "first")
    first = min(essential_positions)
    last = max(essential_positions)
    start_text = prefix + (SEP.join(value[1] for value in artifacts[:first]))
    if first:
        start_text += SEP
    end_text = prefix + SEP.join(value[1] for value in artifacts[: last + 1])
    query_boundary = token_counter(f"Question:\n{question}")
    start = token_counter(start_text)
    end = token_counter(end_text)
    context_tokens = token_counter(context)
    return {
        "context_tokens": context_tokens,
        "max_evidence_distance": max(end - start, end - query_boundary),
        "proof_depth": proof_depth,
        "state_updates": len(artifacts),
    }


def _dossier_spread(
    chronology: Sequence[tuple[str, dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    spread: list[tuple[dict[str, Any], str]] = []
    left = 0
    right = len(chronology) - 1
    while left <= right:
        _key, classification, document = chronology[left]
        spread.append((classification, document))
        left += 1
        if left <= right:
            _key, classification, document = chronology[right]
            spread.append((classification, document))
            right -= 1
    if len(spread) != len(chronology) or len(
        {str(value[0].get("artifact_id") or "") for value in spread}
    ) != len(spread):
        raise PromotionError("task dossier spread is invalid")
    return spread


def _task_view_replay(
    candidate: dict[str, Any],
    adapter_key: tuple[str, str, str],
    artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    with sanitized_attestation_environment():
        if adapter_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
            return replay_kev_pipeline_candidate(
                canonicalize_kev_projection_candidate(candidate),
                evidence_artifact_ids=artifact_ids,
                counterfactual=counterfactual,
            )
        if adapter_key == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER:
            return replay_cross_cve_pipeline_candidate(
                canonicalize_cross_cve_projection_candidate(candidate),
                evidence_artifact_ids=artifact_ids,
                counterfactual=counterfactual,
            )
        if adapter_key == FINANCE_TASK_REPLAY_ADAPTER:
            return replay_finance_pipeline_selection(
                candidate, artifact_ids, counterfactual=counterfactual
            )
        if adapter_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
            return replay_macro_vintage_pipeline_selection(
                normalize_projection_candidate_for_adapter(candidate),
                artifact_ids,
                counterfactual=counterfactual,
            )
    raise PromotionError("standard task views are not implemented for adapter")


def build_task_candidate_view_projections(
    candidate: dict[str, Any],
    *,
    adapter_key: tuple[str, str, str],
    token_counter: TokenCounter,
    candidate_attestation_key: bytes,
) -> list[dict[str, Any]]:
    """Build independently attested standard-view candidates.

    These rows intentionally remain non-train-ready until their individual
    content commitments, audits, and candidate digests are selected upstream.
    """
    if not callable(token_counter):
        raise PromotionError("task view projection requires an exact token counter")
    if adapter_key not in {
        CYBER_KEV_TASK_REPLAY_ADAPTER,
        CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
        FINANCE_TASK_REPLAY_ADAPTER,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    }:
        raise PromotionError("standard task views are not implemented for adapter")
    artifacts = _task_view_artifacts(candidate)
    all_ids = [str(value[0].get("artifact_id") or "") for value in artifacts]
    factual = _task_view_replay(candidate, adapter_key, all_ids)
    counterfactual = _task_view_replay(
        candidate, adapter_key, all_ids, counterfactual=True
    )
    if factual.get("answer") != candidate.get("answer") or counterfactual.get(
        "answer"
    ) != candidate.get("cf_answer"):
        raise PromotionError("task view source replay does not match answers")
    if adapter_key == FINANCE_TASK_REPLAY_ADAPTER:
        cf_artifacts = _finance_counterfactual_artifacts(candidate, artifacts)
        chronology = _finance_chronology(artifacts)
        raw_header = str(candidate.get("context") or "").splitlines()[0]
        cf_candidate = deepcopy(candidate)
        cf_candidate["context"] = "\n".join(
            [raw_header, *(document for _classification, document in cf_artifacts)]
        )
    elif adapter_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
        cf_artifacts = _cyber_counterfactual_artifacts(candidate, artifacts)
        chronology = _cyber_chronology(artifacts)
        cf_candidate = deepcopy(candidate)
        cf_candidate["document_context"] = SEP.join(
            document for _classification, document in cf_artifacts
        )
        cf_candidate["artifact_classification"] = [
            deepcopy(classification) for classification, _document in cf_artifacts
        ]
    elif adapter_key == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER:
        cf_artifacts = _cross_cve_counterfactual_artifacts(candidate, artifacts)
        chronology = _cross_cve_chronology(artifacts)
        cf_candidate = deepcopy(candidate)
        cf_candidate["document_context"] = SEP.join(
            document for _classification, document in cf_artifacts
        )
        cf_candidate["artifact_classification"] = [
            deepcopy(classification) for classification, _document in cf_artifacts
        ]
    elif adapter_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
        cf_artifacts = _macro_counterfactual_artifacts(candidate, artifacts)
        chronology = _macro_chronology(artifacts)
        cf_candidate = deepcopy(candidate)
        cf_candidate["document_context"] = SEP.join(
            document for _classification, document in cf_artifacts
        )
        cf_candidate["artifact_classification"] = [
            deepcopy(classification) for classification, _document in cf_artifacts
        ]
    else:
        raise PromotionError("standard task views are not implemented for adapter")
    materialized_cf = _task_view_replay(cf_candidate, adapter_key, all_ids)
    if materialized_cf.get("answer") != candidate.get("cf_answer"):
        raise PromotionError("materialized task counterfactual replay does not match")
    ordered_artifacts = [
        (classification, document) for _key, classification, document in chronology
    ]
    full_artifacts = _dossier_spread(chronology)
    cf_by_id = {
        str(classification.get("artifact_id") or ""): (classification, document)
        for classification, document in cf_artifacts
    }
    cf_artifacts = [
        cf_by_id[str(classification.get("artifact_id") or "")]
        for classification, _document in full_artifacts
    ]
    if [value[0]["artifact_id"] for value in full_artifacts] == [
        value[0]["artifact_id"] for value in ordered_artifacts
    ]:
        raise PromotionError("task full and ordered artifact views are not distinct")
    by_view = {
        "full": full_artifacts,
        "cf": cf_artifacts,
        "ordered_artifact_view": ordered_artifacts,
    }
    dossier_id = (
        str(candidate.get("dossier_id") or "")
        or _canonical_sha256(
            {
                "candidate_sha256": candidate_sha256(candidate),
                "world_id": candidate.get("world_id"),
                "answer_program_id": candidate.get("answer_program_id"),
            }
        )[:20]
    )
    output: list[dict[str, Any]] = []
    for view in _STANDARD_TASK_VIEWS:
        view_artifacts = by_view[view]
        document_context = SEP.join(
            document for _classification, document in view_artifacts
        )
        question = str(candidate["question"])
        context = wrap_prompt(question, document_context, "first")
        context_tokens = token_counter(context)
        reject_reason = exact_token_band_reject_reason(
            str(candidate.get("length_bucket") or ""), context_tokens
        )
        if reject_reason:
            raise PromotionError("task view exact token band failed: " + reject_reason)
        unsigned = deepcopy(candidate)
        unsigned.pop("attestation", None)
        for field in _CANDIDATE_FORBIDDEN_PROOF_FIELDS:
            unsigned.pop(field, None)
        unsigned["view"] = view
        unsigned["composition_method"] = _STANDARD_VIEW_COMPOSITIONS[view]
        unsigned["dossier_id"] = dossier_id
        unsigned["query_id"] = f"{candidate['query_id']}:standard:{view}"
        unsigned["query_timing"] = "first"
        unsigned["question"] = question
        unsigned["answer"] = (
            candidate["cf_answer"] if view == "cf" else candidate["answer"]
        )
        unsigned["cf_answer"] = candidate["cf_answer"]
        if view == "cf":
            twin = deepcopy(candidate.get("counterfactual_twin"))
            if not isinstance(twin, dict):
                raise PromotionError("task counterfactual twin is missing")
            twin["parent_value"], twin["value"] = (
                twin.get("value"),
                twin.get("parent_value"),
            )
            unsigned["counterfactual_twin"] = twin
            unsigned["cf_answer"] = candidate["answer"]
        unsigned["document_context"] = document_context
        unsigned["context"] = context
        unsigned["artifact_classification"] = [
            deepcopy(classification) for classification, _document in view_artifacts
        ]
        source_token_measurement_receipt = _source_token_measurement_receipt(
            candidate, unsigned, token_counter
        )
        unsigned["real_source_token_ratio"] = source_token_measurement_receipt[
            "real_source_token_ratio"
        ]
        replay_candidate = deepcopy(unsigned)
        if adapter_key[:2] == FINANCE_TASK_REPLAY_ADAPTER[:2]:
            replay_candidate["context"] = "\n".join(
                [
                    str(candidate.get("context") or "").splitlines()[0],
                    *document_context.split(SEP),
                ]
            )
        view_ids = [
            str(classification.get("artifact_id") or "")
            for classification, _document in view_artifacts
        ]
        view_replay = _task_view_replay(replay_candidate, adapter_key, view_ids)
        if view_replay.get("answer") != unsigned["answer"]:
            raise PromotionError("task view factual replay does not match answer")
        for field in (
            "source_record_ids",
            "source_relation_ids",
            "authentic_source_relation_edges",
            "verified_derived_order_relation_edges",
            "verified_derived_relation_edges",
            "event_count",
            "strict_support_event_count",
        ):
            if field in view_replay:
                unsigned[field] = deepcopy(view_replay[field])
        replay_graph = dict(unsigned.get("graph") or {})
        replay_graph["proof_depth"] = view_replay.get("proof_depth")
        replay_graph["hop_count"] = view_replay.get("hop_count")
        unsigned["graph"] = replay_graph
        unsigned.update(_canonical_task_identifiers(unsigned, adapter_key))
        unsigned["tokenizer_context_tokens"] = context_tokens
        unsigned["actual_context_tokens"] = context_tokens
        if "tokenizer_document_context_tokens" in unsigned:
            unsigned["tokenizer_document_context_tokens"] = token_counter(
                document_context
            )
        if "tokenizer_essential_span_tokens" in unsigned:
            essential_ids = set(unsigned.get("essential_artifact_ids") or [])
            essential_positions = [
                index
                for index, (classification, _document) in enumerate(view_artifacts)
                if classification.get("artifact_id") in essential_ids
            ]
            if not essential_positions:
                raise PromotionError("task view essential artifact span is missing")
            unsigned["tokenizer_essential_span_tokens"] = token_counter(
                SEP.join(
                    document
                    for _classification, document in view_artifacts[
                        min(essential_positions) : max(essential_positions) + 1
                    ]
                )
            )
        graph = unsigned.get("graph")
        proof_depth = (
            int(graph.get("proof_depth") or 0) if isinstance(graph, Mapping) else 0
        )
        difficulty = _task_view_difficulty(
            question=question,
            artifacts=view_artifacts,
            context=context,
            token_counter=token_counter,
            proof_depth=proof_depth,
        )
        unsigned["difficulty"] = difficulty
        unsigned["evidence_distance"] = difficulty["max_evidence_distance"]
        unsigned["dependency_class"] = (
            "deep_dependency"
            if proof_depth >= 3
            else "long_range_retrieval"
            if difficulty["max_evidence_distance"] >= 8_000
            else "local_or_mixed"
        )
        chronology_receipt = (
            [
                {
                    "artifact_id": str(classification.get("artifact_id") or ""),
                    "order_key": key,
                }
                for key, classification, _document in chronology
            ]
            if view == "ordered_artifact_view"
            else []
        )
        parent_view_artifacts = (
            ordered_artifacts if view == "ordered_artifact_view" else full_artifacts
        )
        projection = {
            "schema_version": TASK_VIEW_PROJECTION_SCHEMA,
            "derivation_revision": TASK_VIEW_DERIVATION_REVISION,
            "parent_candidate_sha256": candidate_sha256(candidate),
            "view": view,
            "dossier_id": dossier_id,
            "document_context_sha256": hashlib.sha256(
                document_context.encode()
            ).hexdigest(),
            "artifact_bindings": [
                {
                    "artifact_id": str(classification.get("artifact_id") or ""),
                    "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                }
                for classification, document in view_artifacts
            ],
            "parent_artifact_bindings": [
                {
                    "artifact_id": str(classification.get("artifact_id") or ""),
                    "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                    "provenance_id": str(classification.get("provenance_id") or ""),
                    "source_origin": str(classification.get("source_origin") or ""),
                }
                for classification, document in parent_view_artifacts
            ],
            "parent_source_binding_sha256": _canonical_sha256(
                candidate.get("source_binding")
            ),
            "parent_task_replay_sidecar": deepcopy(
                candidate.get("task_replay_sidecar")
            ),
            "source_token_measurement_receipt": source_token_measurement_receipt,
            "chronology": chronology_receipt,
            "adapter_context_header": (
                str(candidate.get("context") or "").splitlines()[0]
                if adapter_key[:2] == FINANCE_TASK_REPLAY_ADAPTER[:2]
                else None
            ),
            "adapter_replay_answer_sha256": hashlib.sha256(
                str(
                    counterfactual.get("answer")
                    if view == "cf"
                    else factual.get("answer")
                ).encode()
            ).hexdigest(),
            "materialized_replay_answer_sha256": hashlib.sha256(
                str(
                    materialized_cf.get("answer")
                    if view == "cf"
                    else factual.get("answer")
                ).encode()
            ).hexdigest(),
        }
        unsigned["task_view_projection"] = projection
        unsigned["data_stage"] = "candidate"
        unsigned["train_ready"] = False
        unsigned["production_eligible"] = False
        unsigned["promotion_eligible"] = False
        unsigned["complete_world"] = False
        unsigned["promoted"] = False
        unsigned["generation_integration"] = "task_view_candidate_projection_v1"
        unsigned["promotion_blocker_code"] = "task_view_source_commitment_pending"
        capabilities = dict(unsigned.get("pipeline_capabilities") or {})
        capabilities["generic_promotion"] = False
        unsigned["pipeline_capabilities"] = capabilities
        signed = attach_attestation(
            unsigned,
            candidate_attestation_key,
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        )
        output.append(signed)
    return output
