"""Executable upstream proof gates for source-bound task replay candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from itertools import pairwise
from typing import Any

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.domainhistory import (
    audit_cross_cve_pipeline_candidate,
    audit_kev_pipeline_candidate,
    replay_cross_cve_pipeline_candidate,
    replay_cross_cve_pipeline_raw_slice,
    replay_kev_pipeline_candidate,
    replay_kev_pipeline_raw_slice,
)
from longworld.core.financehistory import (
    audit_finance_pipeline_candidate,
    replay_finance_pipeline_raw_slice,
    replay_finance_pipeline_selection,
)
from longworld.core.macrovintage import (
    MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA,
    audit_macro_vintage_pipeline_candidate,
    replay_macro_vintage_pipeline_raw_slice,
    replay_macro_vintage_pipeline_selection,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.standardsworkflow import (
    render_ietf_cross_spec_prompt,
    replay_ietf_cross_spec_requirement_task,
)
from longworld.core.taskreplaysidecar import (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER,
    IETF_OAUTH_TASK_REPLAY_ADAPTER,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    SOURCE_TOKEN_MEASUREMENT_BASIS,
    SOURCE_TOKEN_MEASUREMENT_RECEIPT_SCHEMA,
    TASK_REPLAY_ADAPTER_REGISTRY,
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
    TASK_VIEW_DERIVATION_REVISION,
    TaskReplayRegistryKey,
)
from longworld.core.verify import Verification

TASK_PROOF_RECEIPT_SCHEMA = "longworld.task-proof-receipt.v5"
TokenCounter = Callable[[str], int]
_WINDOW_BANDS = {"4k": 4_096, "8k": 8_192, "16k": 16_384}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_LEXEME = re.compile(r"[^\W_]+", re.UNICODE)


class TaskProofError(ValueError):
    """A task candidate lacks one or more independently replayed proof gates."""


def relation_endpoints(relation: object) -> tuple[str, str] | None:
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
        and relation[0]
        and isinstance(relation[1], str)
        and relation[1]
    ):
        return relation[0], relation[1]
    return None


def relation_proof_depth(relations: Sequence[object]) -> int:
    adjacency: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for relation in relations:
        endpoints = relation_endpoints(relation)
        if endpoints is None:
            raise TaskProofError("task replay relation edge is malformed")
        parent, child = endpoints
        nodes.update((parent, child))
        adjacency.setdefault(parent, set()).add(child)

    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def depth(node: str) -> int:
        if node in memo:
            return memo[node]
        if node in visiting:
            raise TaskProofError("task replay relation graph contains a cycle")
        visiting.add(node)
        value = max((1 + depth(child) for child in adjacency.get(node, ())), default=0)
        visiting.remove(node)
        memo[node] = value
        return value

    return max(2, max((depth(node) for node in nodes), default=0))


def replay_ietf_cross_spec_candidate(
    candidate: dict[str, Any],
    evidence_artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay the OAuth requirement task from selected source-record artifacts."""
    task = candidate.get("ietf_requirement_task")
    source_records = candidate.get("source_record_ids_by_artifact")
    classifications = candidate.get("artifact_classification")
    if (
        not isinstance(task, dict)
        or not isinstance(source_records, dict)
        or not isinstance(classifications, list)
    ):
        raise TaskProofError("IETF task replay contract is missing")
    if candidate.get("question") != task.get("question"):
        raise TaskProofError("candidate question is not bound to the IETF task")
    classification_by_artifact = {
        str(item.get("artifact_id") or ""): item
        for item in classifications
        if isinstance(item, dict)
    }
    if len(classification_by_artifact) != len(classifications):
        raise TaskProofError("IETF task replay artifact spans are invalid")
    document_context = candidate.get("document_context")
    documents = document_context.split(SEP) if isinstance(document_context, str) else []
    if len(documents) != len(classifications):
        raise TaskProofError("IETF task replay artifact bytes are missing")
    document_by_artifact = {
        str(classification.get("artifact_id") or ""): document
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    }
    if len(document_by_artifact) != len(documents):
        raise TaskProofError("IETF task replay artifact bytes are invalid")
    selected_artifacts = list(evidence_artifact_ids)
    if len(selected_artifacts) != len(set(selected_artifacts)) or any(
        artifact_id not in source_records for artifact_id in selected_artifacts
    ):
        raise TaskProofError("IETF task replay artifact selection is invalid")
    manifest_records = {
        str(record.get("record_id") or ""): record
        for record in (task.get("source_manifest") or {}).get("records") or []
        if isinstance(record, dict)
    }
    selected_spans: dict[str, list[tuple[int, int]]] = {}
    for artifact_id in selected_artifacts:
        record_ids = source_records[artifact_id]
        classification = classification_by_artifact.get(artifact_id)
        if (
            not isinstance(record_ids, list)
            or len(record_ids) != 1
            or not isinstance(record_ids[0], str)
            or not isinstance(classification, dict)
            or classification.get("source_record_id") != record_ids[0]
        ):
            raise TaskProofError("IETF task replay source mapping is invalid")
        record_id = record_ids[0]
        record = manifest_records.get(record_id)
        start = classification.get("source_char_start")
        end = classification.get("source_char_end")
        if (
            not isinstance(record, dict)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not 0 <= start < end <= len(str(record.get("text") or ""))
        ):
            raise TaskProofError("IETF task replay artifact spans are invalid")
        source_document = str(record["text"])[start:end]
        document = document_by_artifact[artifact_id]
        if classification.get("source_origin") == "synthetic_counterfactual":
            twin = candidate.get("counterfactual_twin")
            evidence = next(
                (
                    item
                    for item in task.get("evidence_items") or []
                    if isinstance(item, dict)
                    and item.get("evidence_id")
                    == (twin.get("evidence_id") if isinstance(twin, dict) else None)
                ),
                None,
            )
            evidence_start = (
                evidence.get("char_start") if isinstance(evidence, dict) else None
            )
            evidence_end = (
                evidence.get("char_end") if isinstance(evidence, dict) else None
            )
            parent_value = (
                evidence.get("evidence_quote") if isinstance(evidence, dict) else None
            )
            if (
                not isinstance(twin, dict)
                or twin.get("provenance_operation") != "exclude_exact_source_span"
                or not isinstance(evidence, dict)
                or evidence.get("record_id") != record_id
                or not isinstance(evidence_start, int)
                or not isinstance(evidence_end, int)
                or not isinstance(parent_value, str)
                or not start <= evidence_start < evidence_end <= end
            ):
                raise TaskProofError("IETF counterfactual artifact bytes are invalid")
            local_start = evidence_start - start
            local_end = evidence_end - start
            expected = (
                source_document[:local_start]
                + " " * len(parent_value)
                + source_document[local_end:]
            )
            document_sha256 = hashlib.sha256(document.encode()).hexdigest()
            if (
                source_document[local_start:local_end] != parent_value
                or document != expected
                or classification.get("counterfactual_parent_text_sha256")
                != hashlib.sha256(source_document.encode()).hexdigest()
                or classification.get("counterfactual_child_text_sha256")
                != document_sha256
                or classification.get("counterfactual_operation_source_char_start")
                != evidence_start
                or classification.get("counterfactual_operation_source_char_end")
                != evidence_end
                or classification.get("counterfactual_operation_local_char_start")
                != local_start
                or classification.get("counterfactual_operation_local_char_end")
                != local_end
                or classification.get("provenance_id")
                != "counterfactual-projection-sha256:" + document_sha256
            ):
                raise TaskProofError("IETF counterfactual artifact bytes are invalid")
        elif document != source_document:
            raise TaskProofError("IETF task replay artifact source bytes are invalid")
        selected_spans.setdefault(record_id, []).append((start, end))
    selected_records = set(selected_spans)
    excluded_evidence_id = ""
    if counterfactual:
        twin = candidate.get("counterfactual_twin")
        excluded_evidence_id = (
            str(twin.get("evidence_id") or "") if isinstance(twin, dict) else ""
        )
        if excluded_evidence_id not in {
            str(item.get("evidence_id") or "")
            for item in task.get("evidence_items") or []
            if isinstance(item, dict)
        }:
            raise TaskProofError("IETF counterfactual evidence binding is invalid")
    evidence_ids = [
        item["evidence_id"]
        for item in task.get("evidence_items") or []
        if any(
            start <= item.get("char_start") and item.get("char_end") <= end
            for start, end in selected_spans.get(str(item.get("record_id") or ""), [])
        )
        and item.get("evidence_id") != excluded_evidence_id
    ]
    relations = [
        relation
        for relation in (task.get("source_manifest") or {}).get("relations") or []
        if relation.get("source_record_id") in selected_records
        and relation.get("target_record_id") in selected_records
    ]
    relation_ids = [str(relation["relation_id"]) for relation in relations]
    record_dates = {
        str(record.get("record_id") or ""): str(record.get("occurred_at") or "")
        for record in (task.get("source_manifest") or {}).get("records") or []
        if isinstance(record, dict)
    }
    allowed_relation_kinds = {
        "published_as",
        "updates",
        "normative_reference",
        "informative_reference",
    }
    derived_order_edges: list[dict[str, str]] = []
    for relation in relations:
        source_id = str(relation["source_record_id"])
        target_id = str(relation["target_record_id"])
        source_date = record_dates.get(source_id, "")
        target_date = record_dates.get(target_id, "")
        kind = str(relation.get("kind") or "")
        if (
            kind not in allowed_relation_kinds
            or not source_date
            or not target_date
            or source_date == target_date
        ):
            continue
        parent_id, child_id, parent_date, child_date = (
            (source_id, target_id, source_date, target_date)
            if source_date < target_date
            else (target_id, source_id, target_date, source_date)
        )
        derived_order_edges.append(
            {
                "parent_record_id": parent_id,
                "child_record_id": child_id,
                "relation_provenance": "authenticated_relation_timestamp_order",
                "source_relation_id": str(relation["relation_id"]),
                "source_relation_kind": kind,
                "parent_occurred_at": parent_date,
                "child_occurred_at": child_date,
            }
        )
    answer = replay_ietf_cross_spec_requirement_task(
        task,
        evidence_ids=evidence_ids,
        relation_ids=[
            relation_id
            for relation_id in task.get("essential_relation_ids") or []
            if relation_id in relation_ids
        ],
    )
    proof_depth = relation_proof_depth(
        [
            {
                "parent_record_id": relation["source_record_id"],
                "child_record_id": relation["target_record_id"],
            }
            for relation in relations
        ]
    )
    return {
        "answer": json.dumps(answer, sort_keys=True, separators=(",", ":")),
        "source_record_ids": sorted(selected_records),
        "source_relation_ids": relation_ids,
        "authentic_source_relation_edges": [
            {
                "parent_record_id": relation["source_record_id"],
                "child_record_id": relation["target_record_id"],
                "relation_provenance": relation["kind"],
            }
            for relation in relations
        ],
        "verified_derived_order_relation_edges": derived_order_edges,
        "event_count": len(evidence_ids),
        "strict_support_event_count": len(
            [value for value in answer.values() if value != "UNKNOWN"]
        ),
        "proof_depth": proof_depth,
        "hop_count": proof_depth,
    }


def replay_ietf_cross_spec_raw_slice(
    candidate: dict[str, Any],
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
) -> dict[str, Any]:
    """Replay only complete IETF artifacts inside an exact raw context slice."""
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise TaskProofError("IETF raw replay artifact pool is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications) or not documents:
        raise TaskProofError("IETF raw replay artifact pool is unbound")
    artifact_by_document: dict[str, str] = {}
    for classification, document in zip(classifications, documents, strict=True):
        artifact_id = (
            str(classification.get("artifact_id") or "")
            if isinstance(classification, dict)
            else ""
        )
        if not artifact_id or not document or document in artifact_by_document:
            raise TaskProofError("IETF raw replay artifacts are invalid")
        artifact_by_document[document] = artifact_id
    parts = raw_document_context.split(SEP)
    if not left_framed and parts:
        parts = parts[1:]
    if not right_framed and parts:
        parts = parts[:-1]
    selected = [
        artifact_by_document[part] for part in parts if part in artifact_by_document
    ]
    materialized = any(
        isinstance(classification, dict)
        and classification.get("source_origin") == "synthetic_counterfactual"
        for classification in classifications
    )
    return replay_ietf_cross_spec_candidate(
        candidate, selected, counterfactual=materialized
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _adapter_key(candidate: dict[str, Any]) -> TaskReplayRegistryKey:
    binding = candidate.get("task_replay_sidecar")
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        }
        or _SHA256.fullmatch(str(binding.get("sha256") or "")) is None
    ):
        raise TaskProofError("candidate task replay sidecar binding is invalid")
    key = (
        str(binding.get("adapter_id") or ""),
        str(binding.get("adapter_revision") or ""),
        str(binding.get("sidecar_schema_version") or ""),
    )
    if key not in TASK_REPLAY_ADAPTER_REGISTRY:
        raise TaskProofError("candidate task replay adapter is not registered")
    family = key[:2]
    standard_projection = key[2] in {
        TASK_REPLAY_SIDECAR_SCHEMA_V2,
        TASK_REPLAY_SIDECAR_SCHEMA_V3,
    }
    if (
        family == CYBER_KEV_TASK_REPLAY_ADAPTER[:2]
        or family == (CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[:2])
    ):
        expected_domain = "cyber"
        expected_view = "ordered_artifact_view"
        expected_composition = "causal_timeline"
    elif family == FINANCE_TASK_REPLAY_ADAPTER[:2]:
        expected_domain = "finance"
        expected_view = "full"
        expected_composition = "same_case_dossier"
    elif family == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
        expected_domain = "macro_economics"
        expected_view = "ordered_release_timeline"
        expected_composition = "as_of_revision_workflow"
    elif family == IETF_OAUTH_TASK_REPLAY_ADAPTER[:2]:
        expected_domain = "standards"
        expected_view = "full"
        expected_composition = "same_case_dossier"
    else:  # pragma: no cover - the closed registry is checked first
        raise TaskProofError("candidate task replay adapter is unsupported")
    if candidate.get("domain") != expected_domain:
        raise TaskProofError("candidate task replay adapter and domain do not match")
    view = str(candidate.get("view") or "")
    if standard_projection:
        expected_compositions = {
            "full": "same_case_dossier",
            "cf": "counterfactual_twin",
            "ordered_artifact_view": "causal_timeline",
        }
        view_valid = candidate.get("composition_method") == expected_compositions.get(
            view
        )
    else:
        view_valid = (
            view == expected_view
            and candidate.get("composition_method") == expected_composition
        )
    if (
        candidate.get("data_stage") != "candidate"
        or candidate.get("training_objective") != "sft"
        or not view_valid
        or not str(candidate.get("question") or "").strip()
        or not str(candidate.get("answer") or "").strip()
        or not str(candidate.get("cf_answer") or "").strip()
    ):
        raise TaskProofError("candidate task lifecycle or view contract is invalid")
    if candidate.get("strict_replay_revision") != key[1]:
        raise TaskProofError("candidate task replay revision does not match sidecar")
    if family == CYBER_KEV_TASK_REPLAY_ADAPTER[:2]:
        replay_contract = candidate.get("domain_history_replay_manifest")
        replay_identity_valid = bool(
            isinstance(replay_contract, dict)
            and replay_contract.get("replay_revision") == key[1]
        )
    elif family == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[:2]:
        replay_contract = candidate.get("cross_cve_replay_contract")
        replay_identity_valid = bool(
            isinstance(replay_contract, dict)
            and replay_contract.get("adapter_id") == key[0]
            and replay_contract.get("revision") == key[1]
        )
    elif family == FINANCE_TASK_REPLAY_ADAPTER[:2]:
        replay_contract = candidate.get("finance_replay_contract")
        replay_identity_valid = bool(
            isinstance(replay_contract, dict)
            and replay_contract.get("adapter_id") == key[0]
            and replay_contract.get("revision") == key[1]
        )
    elif family == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
        replay_identity_valid = (
            candidate.get("schema_version") == MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA
        )
    else:
        task = candidate.get("ietf_requirement_task")
        ietf_task_programs = {
            "longworld.ietf-cross-spec-requirement-task.v1": (
                "ietf.oauth_effective_requirement.v1"
            ),
            "longworld.ietf-cross-spec-growth-requirement-task.v1": (
                "ietf.oauth_effective_requirement.v2"
            ),
        }
        replay_identity_valid = bool(
            isinstance(task, dict)
            and task.get("answer_program_id")
            == ietf_task_programs.get(str(task.get("schema_version") or ""))
            and candidate.get("answer_program_id") == task.get("answer_program_id")
        )
    if not replay_identity_valid:
        raise TaskProofError("candidate adapter replay contract is inconsistent")
    if (
        not str(candidate.get("tokenizer_model_id") or "").strip()
        or _COMMIT_SHA.fullmatch(str(candidate.get("tokenizer_revision") or "")) is None
        or _SHA256.fullmatch(
            str(candidate.get("tokenizer_asset_manifest_sha256") or "")
        )
        is None
    ):
        raise TaskProofError("candidate exact tokenizer identity is incomplete")
    return key


def audit_task_view_projection(candidate: dict[str, Any]) -> dict[str, bool]:
    """Validate projected bytes, parent provenance, and source chronology."""
    projection = candidate.get("task_view_projection")
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if (
        not isinstance(projection, dict)
        or projection.get("schema_version") != "longworld.task-view-projection.v1"
        or projection.get("derivation_revision") != TASK_VIEW_DERIVATION_REVISION
        or projection.get("view") != candidate.get("view")
        or projection.get("dossier_id") != candidate.get("dossier_id")
        or not isinstance(classifications, list)
        or not isinstance(document_context, str)
    ):
        raise TaskProofError("task view projection contract is invalid")
    documents = document_context.split(SEP)
    bindings = projection.get("artifact_bindings")
    if len(documents) != len(classifications) or not isinstance(bindings, list):
        raise TaskProofError("task view projection artifact bindings are invalid")
    expected_bindings = [
        {
            "artifact_id": str(classification.get("artifact_id") or ""),
            "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
        }
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    ]
    chronology = projection.get("chronology")
    chronology_valid = chronology == []
    if candidate.get("view") == "ordered_artifact_view":
        try:
            expected_chronology = _projection_chronology(
                candidate, classifications, documents
            )
        except (KeyError, TypeError, ValueError, TaskProofError):
            expected_chronology = None
        chronology_valid = bool(
            isinstance(chronology, list)
            and len(chronology) == len(classifications)
            and chronology == expected_chronology
            and [
                str(classification.get("artifact_id") or "")
                for classification in classifications
                if isinstance(classification, dict)
            ]
            == [item["artifact_id"] for item in chronology]
        )
    parent_binding_valid = _counterfactual_parent_binding_valid(
        candidate, classifications, documents, projection
    )
    source_token_measurement_valid = _source_token_measurement_valid(
        candidate, projection
    )
    question = candidate.get("question")
    query_timing = candidate.get("query_timing")
    renderer = (
        render_ietf_cross_spec_prompt
        if isinstance(candidate.get("ietf_requirement_task"), dict)
        else wrap_prompt
    )
    serialized_context_valid = bool(
        isinstance(question, str)
        and query_timing in {"first", "late"}
        and candidate.get("context")
        == renderer(question, document_context, str(query_timing))
    )
    checks = {
        "projection_document_digest_valid": projection.get("document_context_sha256")
        == hashlib.sha256(document_context.encode()).hexdigest(),
        "projection_artifact_bindings_valid": bindings == expected_bindings,
        "projection_chronology_valid": chronology_valid,
        "counterfactual_parent_binding_valid": parent_binding_valid,
        "source_token_measurement_valid": source_token_measurement_valid,
        "serialized_context_valid": serialized_context_valid,
        "projection_candidate_only": all(
            candidate.get(field) is False
            for field in ("train_ready", "production_eligible", "promoted")
        ),
    }
    failed = sorted(name for name, value in checks.items() if value is not True)
    if failed:
        raise TaskProofError("task view projection failed: " + ",".join(failed))
    return checks


def _source_token_measurement_valid(
    candidate: dict[str, Any], projection: dict[str, Any]
) -> bool:
    measurement = projection.get("source_token_measurement_receipt")
    contributions = (
        measurement.get("parent_artifact_token_contributions")
        if isinstance(measurement, dict)
        else None
    )
    parent_bindings = projection.get("parent_artifact_bindings")
    if (
        not isinstance(measurement, dict)
        or measurement.get("schema_version") != SOURCE_TOKEN_MEASUREMENT_RECEIPT_SCHEMA
        or measurement.get("measurement_basis") != SOURCE_TOKEN_MEASUREMENT_BASIS
        or measurement.get("tokenizer_model_id") != candidate.get("tokenizer_model_id")
        or measurement.get("tokenizer_revision") != candidate.get("tokenizer_revision")
        or measurement.get("tokenizer_asset_manifest_sha256")
        != candidate.get("tokenizer_asset_manifest_sha256")
        or not isinstance(contributions, list)
        or not contributions
        or not isinstance(parent_bindings, list)
    ):
        return False
    parent_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in parent_bindings
        if isinstance(item, dict)
    }
    contribution_ids = [
        str(item.get("artifact_id") or "")
        for item in contributions
        if isinstance(item, dict)
    ]
    if (
        len(contribution_ids) != len(contributions)
        or len(set(contribution_ids)) != len(contribution_ids)
        or set(contribution_ids) != set(parent_by_id)
        or any(
            not isinstance(item, dict)
            or item.get("parent_text_sha256")
            != parent_by_id.get(str(item.get("artifact_id") or ""), {}).get(
                "text_sha256"
            )
            or isinstance(item.get("token_contribution"), bool)
            or not isinstance(item.get("token_contribution"), int)
            or item["token_contribution"] < 0
            or not isinstance(item.get("retained_as_real"), bool)
            for item in contributions
        )
    ):
        return False
    parent_tokens = measurement.get("parent_document_context_tokens")
    projected_tokens = measurement.get("projected_document_context_tokens")
    retained_tokens = measurement.get("retained_parent_document_tokens")
    parent_ratio = measurement.get("parent_real_source_token_ratio")
    measured_ratio = measurement.get("real_source_token_ratio")
    final_prompt_tokens = measurement.get("final_prompt_tokens")
    without_real_prompt_tokens = measurement.get("without_real_prompt_tokens")
    source_tokens = measurement.get("real_source_marginal_tokens")
    expected_parent_tokens = sum(item["token_contribution"] for item in contributions)
    expected_retained_tokens = sum(
        item["token_contribution"] for item in contributions if item["retained_as_real"]
    )
    if (
        isinstance(parent_tokens, bool)
        or not isinstance(parent_tokens, int)
        or parent_tokens < 1
        or isinstance(projected_tokens, bool)
        or not isinstance(projected_tokens, int)
        or projected_tokens < 1
        or isinstance(retained_tokens, bool)
        or not isinstance(retained_tokens, int)
        or retained_tokens < 0
        or expected_parent_tokens != parent_tokens
        or expected_retained_tokens != retained_tokens
        or isinstance(parent_ratio, bool)
        or not isinstance(parent_ratio, (int, float))
        or isinstance(measured_ratio, bool)
        or not isinstance(measured_ratio, (int, float))
        or not 0.0 < float(parent_ratio) <= 1.0
        or isinstance(final_prompt_tokens, bool)
        or not isinstance(final_prompt_tokens, int)
        or final_prompt_tokens < 1
        or final_prompt_tokens != candidate.get("tokenizer_context_tokens")
        or isinstance(without_real_prompt_tokens, bool)
        or not isinstance(without_real_prompt_tokens, int)
        or not 0 <= without_real_prompt_tokens < final_prompt_tokens
        or isinstance(source_tokens, bool)
        or not isinstance(source_tokens, int)
        or source_tokens != final_prompt_tokens - without_real_prompt_tokens
        or candidate.get("real_source_token_ratio") != measured_ratio
    ):
        return False
    expected_ratio = source_tokens / final_prompt_tokens
    return bool(
        measured_ratio == expected_ratio
        and (
            retained_tokens < parent_tokens
            if candidate.get("view") == "cf"
            else retained_tokens == parent_tokens
        )
    )


def _projection_chronology(
    candidate: dict[str, Any],
    classifications: list[Any],
    documents: list[str],
) -> list[dict[str, str]]:
    domain = str(candidate.get("domain") or "")
    if domain == "standards":
        task = candidate.get("ietf_requirement_task")
        manifest = task.get("source_manifest") if isinstance(task, dict) else None
        records = manifest.get("records") if isinstance(manifest, dict) else None
        source_records = candidate.get("source_record_ids_by_artifact")
        if not isinstance(records, list) or not isinstance(source_records, dict):
            raise TaskProofError("IETF chronology source binding is missing")
        occurred_at_by_record = {
            str(record.get("record_id") or ""): str(record.get("occurred_at") or "")
            for record in records
            if isinstance(record, dict)
        }
        ordered: list[tuple[str, str, int, int, str, dict[str, str]]] = []
        for classification in classifications:
            if not isinstance(classification, dict):
                raise TaskProofError("IETF chronology classification is malformed")
            artifact_id = str(classification.get("artifact_id") or "")
            record_id = str(classification.get("source_record_id") or "")
            char_start = classification.get("source_char_start")
            char_end = classification.get("source_char_end")
            occurred_at = occurred_at_by_record.get(record_id, "")
            if (
                not artifact_id
                or source_records.get(artifact_id) != [record_id]
                or not occurred_at
                or isinstance(char_start, bool)
                or not isinstance(char_start, int)
                or isinstance(char_end, bool)
                or not isinstance(char_end, int)
                or char_start < 0
                or char_end <= char_start
            ):
                raise TaskProofError("IETF chronology source span is invalid")
            ordered.append(
                (
                    occurred_at,
                    record_id,
                    char_start,
                    char_end,
                    artifact_id,
                    {
                        "artifact_id": artifact_id,
                        "order_key": f"{occurred_at}|{artifact_id}",
                    },
                )
            )
        ordered.sort(key=lambda item: item[:-1])
        return [item[-1] for item in ordered]
    parsed: list[tuple[dict[str, Any], str, dict[str, Any] | list[dict[str, Any]]]] = []
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, dict):
            raise TaskProofError("task chronology classification is malformed")
        if domain == "cyber":
            records = [json.loads(line) for line in document.splitlines()]
            parsed.append((classification, document, records))
        else:
            record = json.loads(document)
            if not isinstance(record, dict):
                raise TaskProofError("task chronology record is malformed")
            parsed.append((classification, document, record))
    filings: dict[str, str] = {}
    if domain == "finance":
        filings = {
            str(record.get("source_record_id") or ""): str(
                record.get("filing_date") or ""
            )
            for _classification, _document, record in parsed
            if isinstance(record, dict) and record.get("record_type") == "filing"
        }
    chronology: list[dict[str, str]] = []
    for classification, _document, value in parsed:
        artifact_id = str(classification.get("artifact_id") or "")
        if domain == "finance" and isinstance(value, dict):
            record_type = str(value.get("record_type") or "")
            if record_type == "financial_source_row":
                occurred_at, kind = str(value.get("report_date") or ""), "0"
            elif record_type == "filing":
                occurred_at, kind = str(value.get("filing_date") or ""), "1"
            elif record_type == "filing_relation":
                occurred_at, kind = (
                    filings.get(str(value.get("source_record_id") or ""), ""),
                    "2",
                )
            elif record_type == "table_branch_relation":
                occurred_at, kind = (
                    filings.get(str(value.get("source_record_id") or ""), ""),
                    "3",
                )
            elif record_type == "year_join_relation":
                occurred_at, kind = (
                    filings.get(str(value.get("source_record_id") or ""), ""),
                    "4",
                )
            else:
                raise TaskProofError("Finance chronology record type is unsupported")
            order_key = f"{occurred_at}|{kind}|{artifact_id}"
        elif domain == "macro_economics" and isinstance(value, dict):
            payload = value.get("source_payload")
            if not isinstance(payload, dict):
                raise TaskProofError("Macro chronology payload is malformed")
            if value.get("record_type") == "macro_vintage_observation":
                occurred_at, kind = str(payload.get("vintage_date") or ""), "0"
            elif value.get("record_type") == "macro_vintage_relation":
                occurred_at, kind = str(payload.get("source_vintage_date") or ""), "1"
            else:
                raise TaskProofError("Macro chronology record type is unsupported")
            order_key = f"{occurred_at}|{kind}|{artifact_id}"
        elif domain == "cyber" and isinstance(value, list):
            if (
                value
                and isinstance(value[0], dict)
                and value[0].get("record_type")
                in {"cyber_source_record", "cyber_source_relation"}
            ):
                cve_dates: dict[str, str] = {}
                for _classification, _document, records in parsed:
                    if not isinstance(records, list):
                        continue
                    for record in records:
                        if (
                            not isinstance(record, dict)
                            or record.get("kind") != "cisa_kev_entry"
                        ):
                            continue
                        try:
                            payload = json.loads(str(record.get("text") or ""))
                        except json.JSONDecodeError as error:
                            raise TaskProofError(
                                "cross-CVE chronology payload is malformed"
                            ) from error
                        if not isinstance(payload, dict):
                            raise TaskProofError(
                                "cross-CVE chronology payload is malformed"
                            )
                        cve_id = str(record.get("cve_id") or "")
                        date_added = str(payload.get("dateAdded") or "")
                        if cve_id and date_added:
                            cve_dates[cve_id] = date_added
                dates = []
                cve_ids = []
                for record in value:
                    if not isinstance(record, dict):
                        raise TaskProofError("cross-CVE chronology record is malformed")
                    cve_id = str(
                        record.get("cve_id")
                        or str(record.get("relation_id") or "").removeprefix(
                            "cyber:listed-in-kev:"
                        )
                    )
                    dates.append(cve_dates.get(cve_id, ""))
                    cve_ids.append(cve_id)
                start = min(dates) if dates and all(dates) else ""
                cve_key = min(cve_ids) if cve_ids and all(cve_ids) else ""
                order_key = f"{start}|{cve_key}|{artifact_id}"
            else:
                dates = [
                    str((record.get("source_payload") or {}).get("dateAdded") or "")
                    for record in value
                    if isinstance(record, dict)
                    and isinstance(record.get("source_payload"), dict)
                ]
                if dates != sorted(dates):
                    raise TaskProofError("Cyber chronology shard is not ordered")
                start = dates[0] if dates else "0000-00-00"
                end = dates[-1] if dates else start
                order_key = f"{start}|{end}|{artifact_id}"
        else:
            raise TaskProofError("task chronology domain is unsupported")
        if not artifact_id or not order_key.split("|", 1)[0]:
            raise TaskProofError("task chronology identity is incomplete")
        chronology.append({"artifact_id": artifact_id, "order_key": order_key})
    return sorted(chronology, key=lambda item: item["order_key"])


def _counterfactual_parent_binding_valid(
    candidate: dict[str, Any],
    classifications: list[Any],
    documents: list[str],
    projection: dict[str, Any],
) -> bool:
    synthetic = [
        item
        for item in classifications
        if isinstance(item, dict)
        and item.get("source_origin") == "synthetic_counterfactual"
    ]
    if candidate.get("view") == "cf":
        if len(synthetic) != 1:
            return False
    elif synthetic:
        return False
    parent_bindings = projection.get("parent_artifact_bindings")
    parent_sidecar = projection.get("parent_task_replay_sidecar")
    if (
        not isinstance(parent_bindings, list)
        or len(parent_bindings) != len(classifications)
        or not isinstance(parent_sidecar, dict)
        or projection.get("parent_source_binding_sha256")
        != _canonical_sha256(candidate.get("source_binding"))
    ):
        return False
    parent_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in parent_bindings
        if isinstance(item, dict)
    }
    if len(parent_by_id) != len(parent_bindings):
        return False
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, dict):
            return False
        parent = parent_by_id.get(str(classification.get("artifact_id") or ""))
        if not isinstance(parent, dict) or parent.get("source_origin") not in {
            "real_public",
            "real_private_export",
            "real_derived",
        }:
            return False
        current_sha = hashlib.sha256(document.encode()).hexdigest()
        if classification.get("source_origin") == "synthetic_counterfactual":
            if (
                classification.get("counterfactual_parent_source_origin")
                != parent.get("source_origin")
                or classification.get("counterfactual_parent_provenance_id")
                != parent.get("provenance_id")
                or classification.get("counterfactual_parent_text_sha256")
                != parent.get("text_sha256")
                or classification.get("counterfactual_parent_source_binding_sha256")
                != projection.get("parent_source_binding_sha256")
                or classification.get("counterfactual_parent_sidecar_sha256")
                != parent_sidecar.get("sha256")
                or classification.get("provenance_id")
                != "counterfactual-projection-sha256:" + current_sha
                or current_sha == parent.get("text_sha256")
            ):
                return False
        elif (
            classification.get("source_origin") != parent.get("source_origin")
            or classification.get("provenance_id") != parent.get("provenance_id")
            or current_sha != parent.get("text_sha256")
        ):
            return False
    return True


def canonicalize_kev_projection_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Restore source chronology for KEV execution without changing view bytes."""
    if candidate.get("view") == "ordered_artifact_view":
        return candidate
    document_context = candidate.get("document_context")
    classifications = candidate.get("artifact_classification")
    if not isinstance(document_context, str) or not isinstance(classifications, list):
        return candidate
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        return candidate

    def order_key(item: tuple[dict[str, Any], str]) -> tuple[str, str]:
        classification, document = item
        keys: list[str] = []
        for line in document.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return ("9", str(classification.get("artifact_id") or ""))
            if record.get("record_type") == "catalog_snapshot":
                keys.append("0")
            else:
                payload = record.get("source_payload")
                if not isinstance(payload, dict):
                    return ("9", str(classification.get("artifact_id") or ""))
                keys.append(
                    "1|"
                    + str(payload.get("dateAdded") or "")
                    + "|"
                    + str(payload.get("cveID") or "")
                )
        return (min(keys, default="9"), str(classification.get("artifact_id") or ""))

    paired = [
        (classification, document)
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    ]
    if len(paired) != len(documents):
        return candidate
    ordered = sorted(paired, key=order_key)
    replay_candidate = dict(candidate)
    replay_candidate["artifact_classification"] = [item[0] for item in ordered]
    replay_candidate["document_context"] = SEP.join(item[1] for item in ordered)
    return replay_candidate


def canonicalize_cross_cve_projection_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Restore date-then-CVE source order without changing view bytes."""
    if candidate.get("view") == "ordered_artifact_view":
        return candidate
    document_context = candidate.get("document_context")
    classifications = candidate.get("artifact_classification")
    if not isinstance(document_context, str) or not isinstance(classifications, list):
        return candidate
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        return candidate
    paired = [
        (classification, document)
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    ]
    if len(paired) != len(documents):
        return candidate
    cve_dates: dict[str, str] = {}
    parsed_records: list[list[dict[str, Any]]] = []
    for _classification, document in paired:
        records: list[dict[str, Any]] = []
        for line in document.splitlines():
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                parsed_records.append([])
                records = []
                break
            if not isinstance(record, dict):
                parsed_records.append([])
                records = []
                break
            records.append(record)
            if record.get("kind") == "cisa_kev_entry":
                try:
                    payload = json.loads(str(record.get("text") or ""))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    cve_id = str(record.get("cve_id") or "")
                    date_added = str(payload.get("dateAdded") or "")
                    if cve_id and date_added:
                        cve_dates[cve_id] = date_added
        parsed_records.append(records)

    def order_key(item: tuple[int, tuple[dict[str, Any], str]]) -> tuple[str, str, str]:
        index, (classification, _document) = item
        records = parsed_records[index]
        cve_ids = []
        for record in records:
            cve_ids.append(
                str(
                    record.get("cve_id")
                    or str(record.get("relation_id") or "").removeprefix(
                        "cyber:listed-in-kev:"
                    )
                )
            )
        date_added = min(
            (cve_dates.get(cve_id, "9999-99-99") for cve_id in cve_ids),
            default="9999-99-99",
        )
        return (
            date_added,
            min(cve_ids, default=""),
            str(classification.get("artifact_id") or ""),
        )

    ordered = [
        pair
        for _key, pair in sorted(
            enumerate(paired),
            key=lambda item: order_key((item[0], item[1])),
        )
    ]
    replay_candidate = dict(candidate)
    replay_candidate["artifact_classification"] = [item[0] for item in ordered]
    replay_candidate["document_context"] = SEP.join(item[1] for item in ordered)
    return replay_candidate


def normalize_projection_candidate_for_adapter(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Present adapter-native provenance while retaining projection metadata."""
    if candidate.get("domain") != "macro_economics":
        return candidate
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        return candidate
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        return candidate
    normalized = deepcopy(classifications)
    changed = False
    for classification, document in zip(normalized, documents, strict=True):
        if (
            isinstance(classification, dict)
            and classification.get("source_origin") == "synthetic_counterfactual"
        ):
            classification["provenance_id"] = (
                "sha256:" + hashlib.sha256(document.encode()).hexdigest()
            )
            changed = True
    if not changed:
        return candidate
    output = dict(candidate)
    output["artifact_classification"] = normalized
    return output


def _artifact_pool(
    candidate: dict[str, Any],
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    document_context = candidate.get("document_context")
    classifications = candidate.get("artifact_classification")
    if (
        not isinstance(document_context, str)
        or not document_context.strip()
        or not isinstance(classifications, list)
        or not classifications
    ):
        raise TaskProofError("candidate document body or classification is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        raise TaskProofError("candidate document body and classification are unbound")
    artifact_ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, dict):
            raise TaskProofError("candidate artifact classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        if (
            not artifact_id
            or artifact_id in artifact_ids
            or not document.strip()
            or not str(classification.get("workflow_id") or "")
            or classification.get("source_origin")
            not in {
                "real_public",
                "real_private_export",
                "real_derived",
                "synthetic_counterfactual",
            }
            or classification.get("workflow_kind")
            not in {"real_source_derived", "hybrid_causal"}
            or classification.get("evidence_role")
            not in {
                "causal_gold",
                "causal_supporting",
                "natural_background",
                "structural_hard_negative",
            }
            or not str(classification.get("provenance_id") or "")
        ):
            raise TaskProofError("candidate artifact classification is invalid")
        artifact_ids.append(artifact_id)
        normalized.append(classification)
    return artifact_ids, documents, normalized


def _adapter_audit(
    candidate: dict[str, Any],
    adapter_key: TaskReplayRegistryKey,
    token_counter: TokenCounter,
) -> dict[str, bool]:
    try:
        if adapter_key[2] in {
            TASK_REPLAY_SIDECAR_SCHEMA_V2,
            TASK_REPLAY_SIDECAR_SCHEMA_V3,
        }:
            audit = audit_task_view_projection(candidate)
        elif adapter_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
            audit = audit_kev_pipeline_candidate(candidate, token_counter=token_counter)
        elif adapter_key == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER:
            audit = audit_cross_cve_pipeline_candidate(
                candidate, token_counter=token_counter
            )
        elif adapter_key == FINANCE_TASK_REPLAY_ADAPTER:
            audit = audit_finance_pipeline_candidate(candidate)
        elif adapter_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
            audit = audit_macro_vintage_pipeline_candidate(candidate)
        else:  # pragma: no cover - the closed registry is checked first
            raise TaskProofError("task replay adapter is unsupported")
    except TaskProofError:
        raise
    except Exception as error:
        raise TaskProofError("task adapter audit could not inspect the body") from error
    failed = sorted(name for name, passed in audit.items() if passed is not True)
    if failed:
        raise TaskProofError("task adapter audit failed: " + ",".join(failed))
    return audit


def _replay(
    candidate: dict[str, Any],
    adapter_key: TaskReplayRegistryKey,
    artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    if adapter_key[:2] == CYBER_KEV_TASK_REPLAY_ADAPTER[:2]:
        return replay_kev_pipeline_candidate(
            canonicalize_kev_projection_candidate(candidate),
            evidence_artifact_ids=artifact_ids,
            counterfactual=counterfactual,
        )
    if adapter_key[:2] == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[:2]:
        return replay_cross_cve_pipeline_candidate(
            canonicalize_cross_cve_projection_candidate(candidate),
            evidence_artifact_ids=artifact_ids,
            counterfactual=counterfactual,
        )
    if adapter_key[:2] == FINANCE_TASK_REPLAY_ADAPTER[:2]:
        if adapter_key[2] in {
            TASK_REPLAY_SIDECAR_SCHEMA_V2,
            TASK_REPLAY_SIDECAR_SCHEMA_V3,
        }:
            projection = candidate.get("task_view_projection")
            header = (
                projection.get("adapter_context_header")
                if isinstance(projection, dict)
                else None
            )
            if not isinstance(header, str) or not header:
                return {"answer": "unknown"}
            candidate = dict(candidate)
            candidate["context"] = "\n".join(
                [header, *str(candidate.get("document_context") or "").split(SEP)]
            )
        return replay_finance_pipeline_selection(
            candidate,
            artifact_ids,
            counterfactual=counterfactual,
        )
    if adapter_key[:2] == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
        return replay_macro_vintage_pipeline_selection(
            normalize_projection_candidate_for_adapter(candidate),
            artifact_ids,
            counterfactual=counterfactual,
        )
    if adapter_key[:2] == IETF_OAUTH_TASK_REPLAY_ADAPTER[:2]:
        materialized = any(
            isinstance(classification, dict)
            and classification.get("source_origin") == "synthetic_counterfactual"
            for classification in candidate.get("artifact_classification") or []
        )
        return replay_ietf_cross_spec_candidate(
            candidate,
            artifact_ids,
            counterfactual=counterfactual != materialized,
        )
    raise TaskProofError("task replay adapter is unsupported")


def _replay_raw_slice(
    candidate: dict[str, Any],
    adapter_key: TaskReplayRegistryKey,
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
) -> dict[str, Any]:
    if adapter_key[:2] == CYBER_KEV_TASK_REPLAY_ADAPTER[:2]:
        return replay_kev_pipeline_raw_slice(
            candidate,
            raw_document_context,
            left_framed=left_framed,
            right_framed=right_framed,
        )
    if adapter_key[:2] == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[:2]:
        return replay_cross_cve_pipeline_raw_slice(
            candidate,
            raw_document_context,
            left_framed=left_framed,
            right_framed=right_framed,
        )
    if adapter_key[:2] == FINANCE_TASK_REPLAY_ADAPTER[:2]:
        if adapter_key[2] in {
            TASK_REPLAY_SIDECAR_SCHEMA_V2,
            TASK_REPLAY_SIDECAR_SCHEMA_V3,
        }:
            projection = candidate.get("task_view_projection")
            header = (
                projection.get("adapter_context_header")
                if isinstance(projection, dict)
                else None
            )
            if not isinstance(header, str) or not header:
                return {"answer": "unknown"}
            candidate = dict(candidate)
            candidate["context"] = "\n".join(
                [header, *str(candidate.get("document_context") or "").split(SEP)]
            )
        return replay_finance_pipeline_raw_slice(
            candidate,
            raw_document_context,
            left_framed=left_framed,
            right_framed=right_framed,
        )
    if adapter_key[:2] == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
        return replay_macro_vintage_pipeline_raw_slice(
            candidate,
            raw_document_context,
            left_framed=left_framed,
            right_framed=right_framed,
        )
    if adapter_key[:2] == IETF_OAUTH_TASK_REPLAY_ADAPTER[:2]:
        return replay_ietf_cross_spec_raw_slice(
            candidate,
            raw_document_context,
            left_framed=left_framed,
            right_framed=right_framed,
        )
    raise TaskProofError("task raw-slice replay adapter is unsupported")


def _lexemes(text: str) -> list[str]:
    return [value.casefold() for value in _LEXEME.findall(text)]


def _bm25_ranking(question: str, documents: list[str]) -> list[tuple[int, float]]:
    query = Counter(_lexemes(question))
    terms = [Counter(_lexemes(document)) for document in documents]
    lengths = [sum(values.values()) for values in terms]
    if not query or not terms or any(length < 1 for length in lengths):
        raise TaskProofError("BM25 tokenization produced an empty query or document")
    document_count = len(documents)
    average_length = sum(lengths) / document_count
    document_frequency = {
        term: sum(term in values for values in terms) for term in query
    }
    scores: list[tuple[int, float]] = []
    for index, (values, length) in enumerate(zip(terms, lengths, strict=True)):
        score = 0.0
        for term, query_frequency in query.items():
            frequency = values.get(term, 0)
            if not frequency:
                continue
            frequency_in_documents = document_frequency[term]
            inverse_frequency = math.log(
                1
                + (document_count - frequency_in_documents + 0.5)
                / (frequency_in_documents + 0.5)
            )
            denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * length / average_length)
            score += (
                query_frequency
                * inverse_frequency
                * (frequency * (1.2 + 1) / denominator)
            )
        scores.append((index, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def _tfidf_ranking(question: str, documents: list[str]) -> list[tuple[int, float]]:
    query_counts = Counter(_lexemes(question))
    document_counts = [Counter(_lexemes(document)) for document in documents]
    if not query_counts or any(not values for values in document_counts):
        raise TaskProofError("TF-IDF tokenization produced an empty query or document")
    document_count = len(documents)
    vocabulary = set(query_counts)
    document_frequency = {
        term: sum(term in values for values in document_counts) for term in vocabulary
    }
    inverse_frequency = {
        term: math.log((1 + document_count) / (1 + document_frequency[term])) + 1
        for term in vocabulary
    }
    query_vector = {
        term: frequency * inverse_frequency[term]
        for term, frequency in query_counts.items()
    }
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    if not query_norm:
        raise TaskProofError("TF-IDF query vector is empty")
    scores: list[tuple[int, float]] = []
    for index, counts in enumerate(document_counts):
        vector = {
            term: counts.get(term, 0) * inverse_frequency[term] for term in vocabulary
        }
        norm = math.sqrt(sum(value * value for value in vector.values()))
        score = (
            sum(query_vector[term] * vector[term] for term in vocabulary)
            / (query_norm * norm)
            if norm
            else 0.0
        )
        scores.append((index, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def _retrieval_proof(
    *,
    name: str,
    ranking: list[tuple[int, float]],
    artifact_ids: list[str],
    replay_answer: Callable[[Sequence[str]], str],
    expected_answer: str,
) -> dict[str, Any]:
    if len(ranking) < 3:
        raise TaskProofError(f"{name} requires at least three ranked artifacts")
    selected = [artifact_ids[index] for index, _score in ranking[:3]]
    prefix_answers = [
        replay_answer(selected[:prefix]) for prefix in range(1, len(selected) + 1)
    ]
    if expected_answer in prefix_answers:
        solved_at = prefix_answers.index(expected_answer) + 1
        raise TaskProofError(f"{name} top-{solved_at} retrieves the gold answer")
    return {
        "top3": [
            {"artifact_id": artifact_ids[index], "score": format(score, ".17g")}
            for index, score in ranking[:3]
        ],
        "prefix_answer_sha256": [
            hashlib.sha256(answer.encode()).hexdigest() for answer in prefix_answers
        ],
        "top1_insufficient": prefix_answers[0] != expected_answer,
        "all_prefixes_insufficient": True,
    }


def _artifact_token_spans(
    documents: list[str], token_counter: TokenCounter
) -> tuple[list[dict[str, int]], int]:
    prefix_tokens = [0]
    for stop in range(1, len(documents) + 1):
        prefix_tokens.append(token_counter(SEP.join(documents[:stop])))
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in prefix_tokens[1:]
    ) or any(left >= right for left, right in pairwise(prefix_tokens)):
        raise TaskProofError("exact token counter produced invalid artifact spans")
    return (
        [
            {"start_token": prefix_tokens[index], "end_token": prefix_tokens[index + 1]}
            for index in range(len(documents))
        ],
        prefix_tokens[-1],
    )


def _contiguous_window_proof(
    *,
    artifact_ids: list[str],
    documents: list[str],
    spans: list[dict[str, int]],
    full_tokens: int,
    token_counter: TokenCounter,
    replay_answer: Callable[[Sequence[str]], str],
    expected_answer: str,
) -> tuple[dict[str, Any], bool]:
    if len(documents) != len(artifact_ids):
        raise TaskProofError("contiguous window documents are unbound")
    token_cache: dict[tuple[int, int], int] = {}

    def exact_window_tokens(start: int, stop: int) -> int:
        key = (start, stop)
        if key not in token_cache:
            token_cache[key] = token_counter(SEP.join(documents[start:stop]))
        value = token_cache[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise TaskProofError("exact token counter produced an invalid window")
        return value

    output: dict[str, Any] = {}
    has_strict_evidence = False
    for label, limit in _WINDOW_BANDS.items():
        if limit >= full_tokens:
            output[label] = {
                "window_tokens": limit,
                "status": "full_control",
                "strict_window_count": 0,
                "enumeration_sha256": None,
            }
            continue
        enumerated: list[dict[str, Any]] = []
        for start in range(len(artifact_ids)):
            for stop in range(start + 1, len(artifact_ids) + 1):
                if start == 0 and stop == len(artifact_ids):
                    continue
                span_tokens = exact_window_tokens(start, stop)
                if span_tokens > limit:
                    continue
                selected = artifact_ids[start:stop]
                answer = replay_answer(selected)
                enumerated.append(
                    {
                        "start": start,
                        "stop": stop,
                        "span_tokens": span_tokens,
                        "artifact_ids": selected,
                        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                    }
                )
                if answer == expected_answer:
                    raise TaskProofError(
                        f"contiguous window {label} retrieves the gold answer: "
                        f"{start}:{stop}"
                    )
        if not enumerated:
            raise TaskProofError(f"contiguous window {label} has no actual evidence")
        has_strict_evidence = True
        output[label] = {
            "window_tokens": limit,
            "status": "insufficient",
            "strict_window_count": len(enumerated),
            "max_artifact_count": max(len(item["artifact_ids"]) for item in enumerated),
            "max_span_tokens": max(item["span_tokens"] for item in enumerated),
            "enumeration_sha256": _canonical_sha256(enumerated),
        }
    return output, has_strict_evidence


def _canonical_record_char_spans(document_context: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for framed_line in document_context.splitlines(keepends=True):
        line = framed_line.removesuffix("\n")
        if line and line != SEP.strip():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise TaskProofError(
                    "raw replay body contains an invalid framed record"
                ) from error
            canonical = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if not isinstance(record, dict) or canonical != line:
                raise TaskProofError("raw replay body record is not canonical")
            spans.append((cursor, cursor + len(line)))
        cursor += len(framed_line)
    if cursor != len(document_context) or not spans:
        raise TaskProofError("raw replay body has no complete framed records")
    return spans


def _window_state_starts(
    offsets: Sequence[tuple[int, int]],
    spans: Sequence[tuple[int, int]],
    limit: int,
    *,
    complete: bool,
) -> list[int]:
    """Return one start per interval where the visible record set is unchanged."""
    if limit < 1 or limit > len(offsets):
        raise TaskProofError("raw replay window limit is invalid")
    starts = [start for start, _end in offsets]
    ends = [end for _start, end in offsets]
    max_start = len(offsets) - limit
    changes = {0, max_start}
    for char_start, char_end in spans:
        if char_start < 0 or char_end <= char_start:
            raise TaskProofError("raw replay record span is invalid")
        if complete:
            first_end = bisect_left(ends, char_end)
            last_start = bisect_right(starts, char_start) - 1
        else:
            first_end = bisect_right(ends, char_start)
            last_start = bisect_left(starts, char_end) - 1
        if first_end == len(offsets) or last_start < 0:
            continue
        visible_from = max(0, first_end - limit + 1)
        visible_through = min(max_start, last_start)
        if visible_from > visible_through:
            continue
        changes.add(visible_from)
        if visible_through < max_start:
            changes.add(visible_through + 1)
    return sorted(changes)


def _semantic_window_starts(
    offsets: Sequence[tuple[int, int]],
    record_spans: Sequence[tuple[int, int]],
    limit: int,
) -> list[int]:
    """Return exhaustive representatives for complete-record raw replay states."""
    return _window_state_starts(offsets, record_spans, limit, complete=True)


def _raw_token_window_proof(
    *,
    artifact_ids: list[str],
    documents: list[str],
    offset_tokenizer: Any,
    replay_raw_answer: Callable[[str, bool, bool], str],
    replay_artifact_answer: Callable[[Sequence[str]], str],
    expected_answer: str,
    expected_total_tokens: int,
    records_are_artifacts: bool = False,
) -> tuple[dict[str, Any], bool, list[dict[str, int]]]:
    """Replay exact raw slices and separately run a non-certifying upper bound."""
    if len(documents) != len(artifact_ids) or not callable(offset_tokenizer):
        raise TaskProofError("raw token window inputs are unbound")
    document_context = SEP.join(documents)
    try:
        encoded = offset_tokenizer(
            document_context,
            add_special_tokens=False,
            return_offsets_mapping=True,
        )
        token_ids = list(encoded["input_ids"])
        raw_offsets = list(encoded["offset_mapping"])
        if any(
            not isinstance(item, (list, tuple)) or len(item) != 2
            for item in raw_offsets
        ):
            raise TaskProofError("exact tokenizer produced malformed raw offsets")
        offsets = [(item[0], item[1]) for item in raw_offsets]
    except (KeyError, TypeError, ValueError, NotImplementedError) as error:
        raise TaskProofError("exact tokenizer has no raw offset mapping") from error
    if (
        not token_ids
        or len(token_ids) != len(offsets)
        or any(
            not isinstance(start, int)
            or not isinstance(end, int)
            or isinstance(start, bool)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(document_context)
            for start, end in offsets
        )
        or any(
            left_start > right_start or left_end > right_end
            for (left_start, left_end), (right_start, right_end) in pairwise(offsets)
        )
    ):
        raise TaskProofError("exact tokenizer produced invalid raw offsets")
    token_starts = [start for start, _end in offsets]
    token_ends = [end for _start, end in offsets]
    artifact_char_spans: list[tuple[int, int]] = []
    artifact_spans: list[tuple[int, int]] = []
    cursor = 0
    for index, document in enumerate(documents):
        if index:
            cursor += len(SEP)
        char_start = cursor
        cursor += len(document)
        artifact_char_spans.append((char_start, cursor))
        start = bisect_right(token_ends, char_start)
        stop = bisect_left(token_starts, cursor)
        if start >= stop:
            raise TaskProofError("raw token window artifact has no token span")
        artifact_spans.append((start, stop))
    record_char_spans = (
        artifact_char_spans
        if records_are_artifacts
        else _canonical_record_char_spans(document_context)
    )

    total_tokens = len(token_ids)
    if total_tokens != expected_total_tokens:
        raise TaskProofError("raw offset tokenizer count does not match exact counter")
    output: dict[str, Any] = {}
    has_strict_evidence = False
    for label, limit in _WINDOW_BANDS.items():
        if limit >= total_tokens:
            output[label] = {
                "window_tokens": limit,
                "status": "full_control",
                "strict_window_count": 0,
                "exact_raw_executable": {
                    "status": "full_control",
                    "exhaustive_start_count": 0,
                    "semantic_state_count": 0,
                    "enumeration_sha256": None,
                },
                "conservative_intersection_upper_bound": {
                    "status": "full_control",
                    "can_establish_pass": False,
                    "monotonicity_proven": False,
                    "semantic_state_count": 0,
                    "enumeration_sha256": None,
                },
                "enumeration_sha256": None,
            }
            continue
        max_start = total_tokens - limit
        exact_enumerated: list[dict[str, Any]] = []
        seen_record_states: set[tuple[int, ...]] = set()
        for start in _semantic_window_starts(offsets, record_char_spans, limit):
            stop = start + limit
            char_start = offsets[start][0]
            char_end = offsets[stop - 1][1]
            record_state = tuple(
                index
                for index, (record_start, record_end) in enumerate(record_char_spans)
                if char_start <= record_start and record_end <= char_end
            )
            if record_state in seen_record_states:
                continue
            seen_record_states.add(record_state)
            raw_slice = document_context[char_start:char_end]
            answer = replay_raw_answer(
                raw_slice,
                char_start == 0 or document_context[char_start - 1] == "\n",
                char_end == len(document_context)
                or document_context[char_end : char_end + 1] == "\n",
            )
            exact_enumerated.append(
                {
                    "start_token": start,
                    "end_token": stop,
                    "start_char": char_start,
                    "end_char": char_end,
                    "raw_slice_sha256": hashlib.sha256(raw_slice.encode()).hexdigest(),
                    "visible_record_count": len(record_state),
                    "record_state_sha256": _canonical_sha256(record_state),
                    "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                }
            )
            if answer == expected_answer:
                raise TaskProofError(
                    f"exact raw token window {label} retrieves the gold answer: "
                    f"{start}:{stop}"
                )

        upper_enumerated: list[dict[str, Any]] = []
        seen_selections: set[tuple[str, ...]] = set()
        for start in _window_state_starts(
            offsets, artifact_char_spans, limit, complete=False
        ):
            stop = start + limit
            char_start = offsets[start][0]
            char_end = offsets[stop - 1][1]
            selected = tuple(
                artifact_id
                for artifact_id, (artifact_start, artifact_end) in zip(
                    artifact_ids, artifact_char_spans, strict=True
                )
                if artifact_start < char_end and artifact_end > char_start
            )
            if not selected or selected in seen_selections:
                continue
            seen_selections.add(selected)
            answer = replay_artifact_answer(selected)
            upper_enumerated.append(
                {
                    "start_token": start,
                    "end_token": stop,
                    "artifact_ids": list(selected),
                    "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                }
            )
            if answer == expected_answer:
                raise TaskProofError(
                    f"raw token window {label} intersecting-artifact upper bound "
                    f"retrieves the gold answer: {start}:{stop}"
                )
        if not exact_enumerated or not upper_enumerated:
            raise TaskProofError(f"raw token window {label} has no executable evidence")
        has_strict_evidence = True
        output[label] = {
            "window_tokens": limit,
            "status": "executable_insufficient",
            "strict_window_count": len(exact_enumerated),
            "exact_raw_executable": {
                "status": "insufficient",
                "exhaustive_start_count": max_start + 1,
                "semantic_state_count": len(exact_enumerated),
                "state_partition": "complete_canonical_record_visibility_v1",
                "enumeration_sha256": _canonical_sha256(exact_enumerated),
            },
            "conservative_intersection_upper_bound": {
                "status": "gold_absent_non_certifying",
                "can_establish_pass": False,
                "monotonicity_proven": False,
                "semantic_state_count": len(upper_enumerated),
                "max_artifact_count": max(
                    len(item["artifact_ids"]) for item in upper_enumerated
                ),
                "enumeration_sha256": _canonical_sha256(upper_enumerated),
            },
            "enumeration_sha256": _canonical_sha256(exact_enumerated),
        }
    return (
        output,
        has_strict_evidence,
        [{"start_token": start, "end_token": stop} for start, stop in artifact_spans],
    )


def _complexity_valid(candidate: dict[str, Any], essential_ids: list[str]) -> bool:
    graph = candidate.get("graph")
    return bool(
        len(essential_ids) >= 2
        and isinstance(candidate.get("event_count"), int)
        and not isinstance(candidate.get("event_count"), bool)
        and int(candidate["event_count"]) >= 4
        and isinstance(candidate.get("strict_support_event_count"), int)
        and not isinstance(candidate.get("strict_support_event_count"), bool)
        and int(candidate["strict_support_event_count"]) >= 2
        and isinstance(graph, dict)
        and isinstance(graph.get("proof_depth"), int)
        and not isinstance(graph.get("proof_depth"), bool)
        and int(graph["proof_depth"]) >= 2
        and isinstance(graph.get("hop_count"), int)
        and not isinstance(graph.get("hop_count"), bool)
        and int(graph["hop_count"]) >= 2
    )


def _proof_input_sha256(candidate: dict[str, Any]) -> str:
    return _canonical_sha256(
        {
            key: value
            for key, value in candidate.items()
            if key
            not in {
                "attestation",
                "verification",
                "view_verification",
                "task_proof_receipt",
            }
        }
    )


def compute_task_proof(
    candidate: dict[str, Any],
    *,
    token_counter: TokenCounter,
    offset_tokenizer: Any | None = None,
) -> dict[str, Any]:
    """Compute proof fields for later embedding in a separately signed candidate."""
    if not callable(token_counter):
        raise TaskProofError("an exact token counter is required")
    before = _canonical_sha256(candidate)
    with sanitized_attestation_environment():
        adapter_key = _adapter_key(candidate)
        artifact_ids, documents, classifications = _artifact_pool(candidate)
        adapter_audit = _adapter_audit(candidate, adapter_key, token_counter)
        context = candidate.get("context")
        if not isinstance(context, str) or not context.strip():
            raise TaskProofError("candidate serialized context is missing")
        observed_context_tokens = token_counter(context)
        if (
            not isinstance(observed_context_tokens, int)
            or isinstance(observed_context_tokens, bool)
            or observed_context_tokens < 1
            or candidate.get("tokenizer_context_tokens") != observed_context_tokens
            or candidate.get("actual_context_tokens") != observed_context_tokens
        ):
            raise TaskProofError("candidate exact token count does not recompute")
        spans, document_context_tokens = _artifact_token_spans(documents, token_counter)
        if (
            adapter_key[:2] == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]
            and candidate.get("tokenizer_document_context_tokens")
            != document_context_tokens
        ):
            raise TaskProofError(
                "candidate exact document-context token count does not recompute"
            )

        essential = candidate.get("essential_artifact_ids")
        if (
            not isinstance(essential, list)
            or not essential
            or len(essential) != len(set(essential))
            or any(not isinstance(value, str) for value in essential)
            or not set(essential) <= set(artifact_ids)
        ):
            raise TaskProofError("candidate essential artifact set is invalid")
        essential_ids = [str(value) for value in essential]
        if adapter_key[:2] == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
            essential_positions = [artifact_ids.index(value) for value in essential_ids]
            essential_span_tokens = token_counter(
                SEP.join(
                    documents[min(essential_positions) : max(essential_positions) + 1]
                )
            )
            band_lower_tokens = candidate.get("band_lower_tokens")
            band_upper_tokens = candidate.get("band_upper_tokens")
            expected_band = EXACT_TOKEN_BAND_RANGES.get(
                str(candidate.get("length_bucket") or "")
            )
            if (
                not isinstance(band_lower_tokens, int)
                or isinstance(band_lower_tokens, bool)
                or expected_band is None
                or (band_lower_tokens, band_upper_tokens) != expected_band
                or candidate.get("minimum_essential_span_tokens")
                != max(
                    8_193,
                    16_385 if band_lower_tokens > 16_384 else 0,
                    band_lower_tokens // 2 + 1,
                )
                or candidate.get("tokenizer_essential_span_tokens")
                != essential_span_tokens
            ):
                raise TaskProofError(
                    "candidate exact essential-span token count does not recompute"
                )
        essential_classes = {str(item["artifact_id"]): item for item in classifications}
        classification_valid = all(
            essential_classes[value].get("evidence_role") == "causal_gold"
            for value in essential_ids
        )
        if not classification_valid:
            raise TaskProofError(
                "candidate essential classification is not causal gold"
            )

        replay_cache: dict[tuple[bool, tuple[str, ...]], dict[str, Any]] = {}

        def replay(
            selected: Sequence[str], *, counterfactual: bool = False
        ) -> dict[str, Any]:
            cache_key = (counterfactual, tuple(selected))
            if cache_key not in replay_cache:
                replay_cache[cache_key] = _replay(
                    candidate,
                    adapter_key,
                    selected,
                    counterfactual=counterfactual,
                )
            return replay_cache[cache_key]

        def answer(selected: Sequence[str]) -> str:
            return str(replay(selected).get("answer") or "")

        def raw_answer(raw_slice: str, left_framed: bool, right_framed: bool) -> str:
            return str(
                _replay_raw_slice(
                    candidate,
                    adapter_key,
                    raw_slice,
                    left_framed=left_framed,
                    right_framed=right_framed,
                ).get("answer")
                or ""
            )

        expected_answer = str(candidate.get("answer") or "")
        expected_cf_answer = str(candidate.get("cf_answer") or "")
        full = replay(artifact_ids)
        minimal = replay(essential_ids)
        full_cf = replay(artifact_ids, counterfactual=True)
        minimal_cf = replay(essential_ids, counterfactual=True)
        empty_answer = answer([])
        remove_one = [
            {
                "removed_artifact_id": removed,
                "answer": answer(
                    [value for value in essential_ids if value != removed]
                ),
            }
            for removed in essential_ids
        ]
        singles = [
            {"artifact_id": artifact_id, "answer": answer([artifact_id])}
            for artifact_id in essential_ids
        ]

        relation_ids = candidate.get("source_relation_ids")
        replay_authentic_edges = full.get("authentic_source_relation_edges")
        replay_derived_edges = full.get("verified_derived_order_relation_edges")
        candidate_derived_edges = candidate.get("verified_derived_order_relation_edges")
        if replay_derived_edges is None:
            replay_derived_edges = full.get("verified_derived_relation_edges")
            candidate_derived_edges = candidate.get("verified_derived_relation_edges")
        source_relations_valid = bool(
            isinstance(relation_ids, list)
            and relation_ids
            and relation_ids == full.get("source_relation_ids")
            and candidate.get("source_record_ids") == full.get("source_record_ids")
            and isinstance(replay_authentic_edges, list)
            and replay_authentic_edges
            and candidate.get("authentic_source_relation_edges")
            == replay_authentic_edges
            and isinstance(replay_derived_edges, list)
            and replay_derived_edges
            and candidate_derived_edges == replay_derived_edges
        )
        answer_surface_free = bool(
            expected_answer
            and expected_answer not in str(candidate.get("document_context") or "")
            and expected_answer not in str(candidate.get("question") or "")
        )
        body_and_source_valid = bool(adapter_audit) and all(adapter_audit.values())
        complexity_valid = _complexity_valid(candidate, essential_ids)
        if not source_relations_valid:
            raise TaskProofError("task source relation replay is incomplete")
        if not answer_surface_free:
            raise TaskProofError(
                "task answer is exposed on the question or body surface"
            )
        if not complexity_valid:
            raise TaskProofError("task proof complexity is insufficient")

        windows, has_strict_window_evidence = _contiguous_window_proof(
            artifact_ids=artifact_ids,
            documents=documents,
            spans=spans,
            full_tokens=document_context_tokens,
            token_counter=token_counter,
            replay_answer=answer,
            expected_answer=expected_answer,
        )
        if not has_strict_window_evidence:
            raise TaskProofError("contiguous windows have no strict sub-full evidence")
        if offset_tokenizer is None:
            raise TaskProofError("raw token window proof requires an exact tokenizer")
        raw_windows, has_raw_window_evidence, raw_spans = _raw_token_window_proof(
            artifact_ids=artifact_ids,
            documents=documents,
            offset_tokenizer=offset_tokenizer,
            replay_raw_answer=raw_answer,
            replay_artifact_answer=answer,
            expected_answer=expected_answer,
            expected_total_tokens=document_context_tokens,
            records_are_artifacts=(
                adapter_key[:2] == IETF_OAUTH_TASK_REPLAY_ADAPTER[:2]
            ),
        )
        if not has_raw_window_evidence:
            raise TaskProofError("raw token windows have no strict sub-full evidence")
        bm25 = _retrieval_proof(
            name="BM25",
            ranking=_bm25_ranking(str(candidate.get("question") or ""), documents),
            artifact_ids=artifact_ids,
            replay_answer=answer,
            expected_answer=expected_answer,
        )
        lexical = _retrieval_proof(
            name="lexical TF-IDF",
            ranking=_tfidf_ranking(str(candidate.get("question") or ""), documents),
            artifact_ids=artifact_ids,
            replay_answer=answer,
            expected_answer=expected_answer,
        )

        checks = {
            "full_replay_sufficient": full.get("answer") == expected_answer,
            "minimal_replay_sufficient": minimal.get("answer") == expected_answer,
            "full_counterfactual_sufficient": full_cf.get("answer")
            == expected_cf_answer,
            "minimal_counterfactual_sufficient": minimal_cf.get("answer")
            == expected_cf_answer,
            "counterfactual_changes_answer": expected_cf_answer != expected_answer,
            "remove_one_fails": bool(remove_one)
            and all(item["answer"] != expected_answer for item in remove_one),
            "single_essential_insufficient": bool(singles)
            and all(item["answer"] != expected_answer for item in singles),
            "empty_selection_insufficient": empty_answer != expected_answer,
            "artifact_aligned_windows_insufficient": has_strict_window_evidence
            and all(
                item["status"] in {"insufficient", "full_control"}
                for item in windows.values()
            ),
            "raw_token_executable_windows_insufficient": has_raw_window_evidence
            and all(
                item["status"] in {"executable_insufficient", "full_control"}
                for item in raw_windows.values()
            ),
            "bm25_top1_insufficient": bm25["top1_insufficient"] is True,
            "bm25_top3_prefixes_insufficient": bm25["all_prefixes_insufficient"]
            is True,
            "lexical_tfidf_top3_prefixes_insufficient": lexical[
                "all_prefixes_insufficient"
            ]
            is True,
            "body_and_classification_valid": body_and_source_valid
            and classification_valid,
            "answer_surface_free": answer_surface_free,
            "source_relations_replayed": source_relations_valid,
            "minimum_complexity_met": complexity_valid,
            "exact_token_count_recomputed": True,
        }
        checks["no_shortcut"] = all(
            checks[field]
            for field in (
                "artifact_aligned_windows_insufficient",
                "raw_token_executable_windows_insufficient",
                "bm25_top1_insufficient",
                "bm25_top3_prefixes_insufficient",
                "lexical_tfidf_top3_prefixes_insufficient",
                "single_essential_insufficient",
                "empty_selection_insufficient",
                "answer_surface_free",
            )
        )
        failed = sorted(name for name, passed in checks.items() if passed is not True)
        if failed:
            raise TaskProofError("task proof gates failed: " + ",".join(failed))

        verification = Verification(
            production_mode=False,
            candidate_mode=True,
            full_sufficient=checks["full_replay_sufficient"],
            minimal_sufficient=checks["minimal_replay_sufficient"],
            semantic_sufficient=checks["body_and_classification_valid"]
            and checks["source_relations_replayed"],
            strict_executable_sufficient=checks["full_replay_sufficient"],
            remove_one_fails=checks["remove_one_fails"],
            counterfactual_changes_answer=checks["counterfactual_changes_answer"],
            counterfactual_replay_sufficient=checks["full_counterfactual_sufficient"]
            and checks["minimal_counterfactual_sufficient"],
            local_window_insufficient=checks[
                "raw_token_executable_windows_insufficient"
            ],
            contiguous_windows_insufficient=checks[
                "artifact_aligned_windows_insufficient"
            ],
            artifact_aligned_windows_insufficient=checks[
                "artifact_aligned_windows_insufficient"
            ],
            closed_book_unsolved=checks["empty_selection_insufficient"],
            distractor_invariance_gold=checks["minimal_replay_sufficient"],
            surface_match=checks["full_replay_sufficient"],
            schema_ok=checks["body_and_classification_valid"],
            no_shortcut=checks["no_shortcut"],
            min_complexity=checks["minimum_complexity_met"],
            bm25_top1_insufficient=checks["bm25_top1_insufficient"],
            bm25_topk_insufficient=checks["bm25_top3_prefixes_insufficient"],
            lexical_tfidf_topk_insufficient=checks[
                "lexical_tfidf_top3_prefixes_insufficient"
            ],
            embedding_topk_insufficient=False,
            question_only_unsolved=checks["empty_selection_insufficient"]
            and checks["answer_surface_free"],
            essential_single_doc_insufficient=checks["single_essential_insufficient"],
            essential_surface_gold_free=checks["answer_surface_free"],
            essential_text_grounded=checks["body_and_classification_valid"],
        )
        artifact_bindings = [
            {
                "artifact_id": artifact_id,
                "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                **span,
            }
            for artifact_id, document, span in zip(
                artifact_ids, documents, raw_spans, strict=True
            )
        ]
        receipt: dict[str, Any] = {
            "schema_version": TASK_PROOF_RECEIPT_SCHEMA,
            "proof_input_sha256": _proof_input_sha256(candidate),
            "adapter_id": adapter_key[0],
            "adapter_revision": adapter_key[1],
            "sidecar_schema_version": adapter_key[2],
            "task_replay_sidecar_sha256": candidate["task_replay_sidecar"]["sha256"],
            "tokenizer": {
                "model_id": candidate.get("tokenizer_model_id"),
                "revision": candidate.get("tokenizer_revision"),
                "asset_manifest_sha256": candidate.get(
                    "tokenizer_asset_manifest_sha256"
                ),
                "serialized_context_tokens": observed_context_tokens,
                "document_context_tokens": document_context_tokens,
            },
            "artifact_bindings": artifact_bindings,
            "essential_artifact_ids": essential_ids,
            "replay": {
                "full_answer": expected_answer,
                "minimal_answer": str(minimal.get("answer") or ""),
                "counterfactual_answer": expected_cf_answer,
                "empty_answer": empty_answer,
                "remove_one": remove_one,
                "single_essential": singles,
            },
            "window_scope": (
                "exact_raw_slice_replay_with_separate_intersection_upper_bound"
            ),
            "artifact_aligned_windows": windows,
            "raw_token_offset_windows": raw_windows,
            "retrieval": {"bm25": bm25, "lexical_tfidf": lexical},
            "adapter_audit": adapter_audit,
            "checks": checks,
        }
        receipt["receipt_sha256"] = _canonical_sha256(receipt)
        result = {
            "verification": verification.model_dump(),
            "view_verification": {
                "expected_answer": expected_answer,
                "strict_replay_answer": expected_answer,
                "essential_present": True,
                "semantic_text_grounded": verification.essential_text_grounded,
                "classification_ok": classification_valid,
                "global_proof_green": verification.all_green(),
                "production_eligible": False,
            },
            "task_proof_receipt": receipt,
        }
    if _canonical_sha256(candidate) != before:
        raise TaskProofError("task proof computation mutated the candidate")
    return result
