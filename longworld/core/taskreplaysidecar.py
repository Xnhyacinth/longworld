"""Exact-byte, source-attested loading for registered task replay sidecars."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    PURPOSE_ROLES,
    attach_attestation,
    attestation_key_from_env,
    canonical_attested_payload,
    verify_attestation,
)
from longworld.core.provenance import ProvenanceError

TASK_REPLAY_SIDECAR_SCHEMA = "longworld.task-replay-sidecar.v1"
TASK_REPLAY_SIDECAR_SCHEMA_V2 = "longworld.task-replay-sidecar.v2"
TASK_REPLAY_SIDECAR_SCHEMA_V3 = "longworld.task-replay-sidecar.v3"
TASK_REPLAY_SIDECAR_PURPOSE = "task_replay_sidecar"
MAX_TASK_REPLAY_SIDECAR_BYTES = 16_000_000

CYBER_KEV_TASK_REPLAY_ADAPTER = (
    "cyber.kev_history.v1",
    "longworld.kev-catalog-history-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER = (
    "cyber.cross_cve_remediation.v1",
    "longworld.cyber-cross-cve-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
FINANCE_TASK_REPLAY_ADAPTER = (
    "finance.multi_filing.v1",
    "longworld.financial-history-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
MACRO_VINTAGE_TASK_REPLAY_ADAPTER = (
    "macro.gdp_vintage_reconstruction.v1",
    "longworld.macro-vintage-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
IETF_OAUTH_TASK_REPLAY_ADAPTER = (
    "standards.ietf_oauth_requirement.v1",
    "longworld.ietf-oauth-cross-spec-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER = (
    "researchlab.elife_review_revision.v1",
    "longworld.elife-review-revision-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
IETF_OAUTH_TASK_REPLAY_ADAPTER_V3 = (
    IETF_OAUTH_TASK_REPLAY_ADAPTER[0],
    IETF_OAUTH_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
)
CYBER_KEV_TASK_REPLAY_ADAPTER_V2 = (
    CYBER_KEV_TASK_REPLAY_ADAPTER[0],
    CYBER_KEV_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
)
CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V2 = (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[0],
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
)
FINANCE_TASK_REPLAY_ADAPTER_V2 = (
    FINANCE_TASK_REPLAY_ADAPTER[0],
    FINANCE_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
)
MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V2 = (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0],
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
)
CYBER_KEV_TASK_REPLAY_ADAPTER_V3 = (
    CYBER_KEV_TASK_REPLAY_ADAPTER[0],
    CYBER_KEV_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
)
CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V3 = (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[0],
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
)
FINANCE_TASK_REPLAY_ADAPTER_V3 = (
    FINANCE_TASK_REPLAY_ADAPTER[0],
    FINANCE_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
)
MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V3 = (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0],
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER[1],
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
)

TaskReplayRegistryKey = tuple[str, str, str]
_SHA256_LENGTH = 64
_BINDING_FIELDS = {
    "adapter_id",
    "adapter_revision",
    "sidecar_schema_version",
    "sha256",
}
_SIDECAR_FIELDS = {
    "schema_version",
    "data_stage",
    "adapter_id",
    "adapter_revision",
    "replay_payload",
    "train_ready",
    "production_eligible",
    "attestation",
}
_ATTESTATION_FIELDS = {
    "scheme",
    "purpose",
    "role",
    "key_id",
    "environment",
    "digest",
}
_CONTENT_COMMITMENT_FIELDS = {"world_id", "length_bucket", "content_sha256"}
_CONTENT_COMMITMENT_FIELDS_V2 = {
    "world_id",
    "length_bucket",
    "view",
    "content_sha256",
}
_CONTENT_COMMITMENT_EXCLUDED_FIELDS = {
    "attestation",
    "complete_world",
    "generation_integration",
    "pipeline_capabilities",
    "promoted",
    "promotion",
    "promotion_blocker_code",
    "promotion_eligible",
    "production_eligible",
    "task_proof_receipt",
    "task_replay_sidecar",
    "train_ready",
    "verification",
    "view_verification",
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
}
TASK_VIEW_DERIVATION_REVISION = "longworld.task-view-derivation.v4"
SOURCE_TOKEN_MEASUREMENT_RECEIPT_SCHEMA = (
    "longworld.source-token-measurement-receipt.v2"
)
SOURCE_TOKEN_MEASUREMENT_BASIS = "final_prompt_real_source_marginal"
_V3_DERIVATION_FIELDS = {
    "parent_candidate_sha256",
    "parent_content_commitment",
    "projection_content_commitment",
    "projection_receipt",
    "projection_receipt_sha256",
}


@dataclass(frozen=True)
class TaskReplayAdapterContract:
    adapter_id: str
    adapter_revision: str
    sidecar_schema_version: str
    replay_payload_fields: frozenset[str]
    max_bytes: int = MAX_TASK_REPLAY_SIDECAR_BYTES

    @property
    def registry_key(self) -> TaskReplayRegistryKey:
        return (
            self.adapter_id,
            self.adapter_revision,
            self.sidecar_schema_version,
        )


@dataclass(frozen=True)
class LoadedTaskReplaySidecar:
    adapter_id: str
    adapter_revision: str
    sidecar_schema_version: str
    relative_path: str
    sidecar_sha256: str
    raw_bytes: bytes
    signed_sidecar: dict[str, Any]
    replay_payload: dict[str, Any]
    parent_candidates: tuple[dict[str, Any], ...] = ()

    @property
    def registry_key(self) -> TaskReplayRegistryKey:
        return (
            self.adapter_id,
            self.adapter_revision,
            self.sidecar_schema_version,
        )


def _contract(key: TaskReplayRegistryKey) -> TaskReplayAdapterContract:
    family = key[:2]
    if key[2] not in {
        TASK_REPLAY_SIDECAR_SCHEMA,
        TASK_REPLAY_SIDECAR_SCHEMA_V2,
        TASK_REPLAY_SIDECAR_SCHEMA_V3,
    }:
        raise ProvenanceError("task replay adapter contract is not registered")
    if family == CYBER_KEV_TASK_REPLAY_ADAPTER[:2]:
        payload_fields = frozenset(
            {
                "source_manifest_sha256",
                "source_response_sha256",
                "replay_manifest_sha256",
                "replay_revision",
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
                "candidate_content_commitments",
            }
        )
    elif family == CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[:2]:
        payload_fields = frozenset(
            {
                "source_manifest_sha256",
                "fetch_inventory_sha256",
                "authorization_record_id",
                "replay_revision",
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
                "candidate_content_commitments",
            }
        )
    elif family == FINANCE_TASK_REPLAY_ADAPTER[:2]:
        payload_fields = frozenset(
            {
                "signed_manifest_sha256",
                "source_family",
                "authorization_record_id",
                "replay_revision",
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
                "candidate_content_commitments",
            }
        )
    elif family == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[:2]:
        payload_fields = frozenset(
            {
                "workflow_manifest_sha256",
                "raw_source_sha256",
                "fetch_inventory_sha256",
                "fetch_receipt",
                "source_families",
                "authorization_record_id",
                "replay_revision",
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
                "candidate_content_commitments",
            }
        )
    elif family == IETF_OAUTH_TASK_REPLAY_ADAPTER[:2]:
        payload_fields = frozenset(
            {
                "source_manifest_sha256",
                "fetch_inventory_sha256",
                "authorization_record_id",
                "ietf_requirement_task",
                "task_sha256",
                "replay_revision",
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
                "candidate_content_commitments",
            }
        )
    elif family == ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER[:2]:
        payload_fields = frozenset(
            {
                "source_inventory_sha256",
                "authorization_record_id",
                "source_record_bindings",
                "email_redaction_receipt",
                "relation_kinds",
                "elife_review_revision_task",
                "task_sha256",
                "replay_revision",
                "tokenizer_model_id",
                "tokenizer_revision",
                "tokenizer_asset_manifest_sha256",
                "candidate_content_commitments",
            }
        )
    else:
        raise ProvenanceError("task replay adapter contract is not registered")
    if key[2] == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        payload_fields = payload_fields | frozenset(
            {
                "parent_sidecar_raw_utf8",
                "parent_sidecar_sha256",
                "parent_candidates_raw_utf8",
                "parent_candidates_sha256",
                "parent_candidate_digests",
                "parent_candidate_content_commitments",
                "projection_derivation_revision",
                "projection_derivation_receipts",
            }
        )
    return TaskReplayAdapterContract(*key, replay_payload_fields=payload_fields)


TASK_REPLAY_ADAPTER_REGISTRY: Mapping[
    TaskReplayRegistryKey, TaskReplayAdapterContract
] = MappingProxyType(
    {
        key: _contract(key)
        for key in (
            CYBER_KEV_TASK_REPLAY_ADAPTER,
            CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
            FINANCE_TASK_REPLAY_ADAPTER,
            MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
            IETF_OAUTH_TASK_REPLAY_ADAPTER,
            ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER,
            CYBER_KEV_TASK_REPLAY_ADAPTER_V2,
            CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V2,
            FINANCE_TASK_REPLAY_ADAPTER_V2,
            MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V2,
            CYBER_KEV_TASK_REPLAY_ADAPTER_V3,
            CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER_V3,
            FINANCE_TASK_REPLAY_ADAPTER_V3,
            MACRO_VINTAGE_TASK_REPLAY_ADAPTER_V3,
            IETF_OAUTH_TASK_REPLAY_ADAPTER_V3,
        )
    }
)


def _verify_elife_replay_payload(replay_payload: dict[str, Any]) -> None:
    bindings = replay_payload.get("source_record_bindings")
    if (
        not isinstance(bindings, list)
        or len(bindings) != 2
        or any(
            not isinstance(item, dict)
            or set(item)
            != {
                "record_id",
                "raw_source_sha256",
                "redacted_text_sha256",
                "email_redaction_count",
            }
            or not _is_sha256(item.get("raw_source_sha256"))
            or not _is_sha256(item.get("redacted_text_sha256"))
            or item.get("raw_source_sha256") == item.get("redacted_text_sha256")
            or isinstance(item.get("email_redaction_count"), bool)
            or not isinstance(item.get("email_redaction_count"), int)
            or item["email_redaction_count"] < 1
            for item in bindings
        )
        or [item["record_id"] for item in bindings]
        != ["elife:94586:v1", "elife:94586:v2"]
    ):
        raise ProvenanceError("eLife task replay source bindings are invalid")
    receipt = replay_payload.get("email_redaction_receipt")
    if (
        not isinstance(receipt, dict)
        or receipt
        != {
            "replacement": "[redacted-email]",
            "total": sum(item["email_redaction_count"] for item in bindings),
        }
        or receipt["total"] != 6
    ):
        raise ProvenanceError("eLife task replay redaction receipt is invalid")
    if replay_payload.get("relation_kinds") != [
        "implements_revision_delta",
        "requests_revision",
        "responds_to_review",
        "revision_of",
    ]:
        raise ProvenanceError("eLife task replay relation kinds are invalid")
    task = replay_payload.get("elife_review_revision_task")
    if (
        not isinstance(task, dict)
        or set(task)
        != {
            "program_id",
            "answer",
            "essential_evidence_ids",
            "witness",
            "model_written_gold",
        }
        or task.get("program_id")
        != "researchlab.review_response_revision_claim_disposition.v1"
        or task.get("answer") != "VERIFIED_IMPLEMENTED"
        or task.get("essential_evidence_ids")
        != [
            "appendix_APP9",
            "appendix_table_tbl3",
            "body_figure_fig5",
            "controlling_review",
            "direct_author_response",
        ]
        or task.get("model_written_gold") is not False
        or replay_payload.get("task_sha256")
        != hashlib.sha256(
            json.dumps(
                task, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    ):
        raise ProvenanceError("eLife task replay task binding is invalid")
    witness = task.get("witness")
    evidence = witness.get("evidence") if isinstance(witness, dict) else None
    expected_evidence_ids = {
        "appendix_APP9",
        "appendix_table_tbl3",
        "body_figure_fig5",
        "controlling_review",
        "direct_author_response",
        "v1_version_doi",
        "v2_version_doi",
    }
    bindings_by_id = {item["record_id"]: item for item in bindings}
    if not isinstance(evidence, dict) or set(evidence) != expected_evidence_ids:
        raise ProvenanceError("eLife task replay evidence set is invalid")
    for evidence_id, item in evidence.items():
        if not isinstance(item, dict) or set(item) != {
            "evidence_id",
            "record_id",
            "evidence_quote",
            "evidence_char_start",
            "evidence_char_end",
            "source_sha256",
            "text_sha256",
        }:
            raise ProvenanceError("eLife task replay evidence binding is invalid")
        record = bindings_by_id.get(str(item.get("record_id") or ""))
        quote = item.get("evidence_quote")
        start = item.get("evidence_char_start")
        end = item.get("evidence_char_end")
        if (
            item.get("evidence_id") != evidence_id
            or record is None
            or not isinstance(quote, str)
            or not quote
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end != start + len(quote)
            or item.get("source_sha256") != record["raw_source_sha256"]
            or item.get("text_sha256") != record["redacted_text_sha256"]
        ):
            raise ProvenanceError("eLife task replay evidence binding is invalid")


def _verify_replay_payload(
    replay_payload: object, contract: TaskReplayAdapterContract
) -> dict[str, Any]:
    if not isinstance(replay_payload, dict) or set(replay_payload) != set(
        contract.replay_payload_fields
    ):
        raise ProvenanceError("task replay sidecar payload fields are invalid")
    sha_fields = {
        "tokenizer_asset_manifest_sha256",
        "source_manifest_sha256",
        "source_response_sha256",
        "replay_manifest_sha256",
        "signed_manifest_sha256",
        "workflow_manifest_sha256",
        "raw_source_sha256",
        "fetch_inventory_sha256",
        "task_sha256",
        "source_inventory_sha256",
    }
    if any(
        field in replay_payload and not _is_sha256(replay_payload[field])
        for field in sha_fields
    ):
        raise ProvenanceError("task replay sidecar payload digest is invalid")
    commitments = replay_payload.get("candidate_content_commitments")
    commitment_fields = (
        _CONTENT_COMMITMENT_FIELDS_V2
        if contract.sidecar_schema_version
        in {TASK_REPLAY_SIDECAR_SCHEMA_V2, TASK_REPLAY_SIDECAR_SCHEMA_V3}
        else _CONTENT_COMMITMENT_FIELDS
    )
    identity_fields = (
        ("world_id", "length_bucket", "view")
        if contract.sidecar_schema_version
        in {TASK_REPLAY_SIDECAR_SCHEMA_V2, TASK_REPLAY_SIDECAR_SCHEMA_V3}
        else ("world_id", "length_bucket")
    )
    if (
        not isinstance(commitments, list)
        or not commitments
        or any(
            not isinstance(item, dict)
            or set(item) != commitment_fields
            or not str(item.get("world_id") or "").strip()
            or not str(item.get("length_bucket") or "").strip()
            or (
                contract.sidecar_schema_version
                in {TASK_REPLAY_SIDECAR_SCHEMA_V2, TASK_REPLAY_SIDECAR_SCHEMA_V3}
                and not str(item.get("view") or "").strip()
            )
            or not _is_sha256(item.get("content_sha256"))
            for item in commitments
        )
        or commitments
        != sorted(
            commitments,
            key=lambda item: tuple(str(item[field]) for field in identity_fields),
        )
        or len(
            {
                tuple(str(item[field]) for field in identity_fields)
                for item in commitments
            }
        )
        != len(commitments)
    ):
        raise ProvenanceError("task replay sidecar content commitments are invalid")
    if contract.sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        _verify_v3_derivation_payload(replay_payload, contract)
    if contract.adapter_id == ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER[0]:
        _verify_elife_replay_payload(replay_payload)
    tokenizer_revision = replay_payload.get("tokenizer_revision")
    source_families = replay_payload.get("source_families")
    fetch_receipt = replay_payload.get("fetch_receipt")
    ietf_task = replay_payload.get("ietf_requirement_task")
    retrieval = (
        fetch_receipt.get("retrieval") if isinstance(fetch_receipt, dict) else None
    )
    if (
        not isinstance(tokenizer_revision, str)
        or len(tokenizer_revision) != 40
        or any(character not in "0123456789abcdef" for character in tokenizer_revision)
        or replay_payload.get("replay_revision") != contract.adapter_revision
        or (
            contract.adapter_id == IETF_OAUTH_TASK_REPLAY_ADAPTER[0]
            and (
                not isinstance(ietf_task, dict)
                or ietf_task.get("schema_version")
                != "longworld.ietf-cross-spec-requirement-task.v1"
                or replay_payload.get("task_sha256")
                != hashlib.sha256(
                    json.dumps(
                        ietf_task,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                or replay_payload.get("source_manifest_sha256")
                != ietf_task.get("source_manifest_sha256")
                or replay_payload.get("fetch_inventory_sha256")
                != (ietf_task.get("source_manifest") or {}).get(
                    "fetch_inventory_sha256"
                )
                or replay_payload.get("authorization_record_id")
                != (
                    (ietf_task.get("source_manifest") or {}).get("authorization") or {}
                ).get("record_id")
            )
        )
        or (
            contract.adapter_id == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0]
            and (
                not isinstance(source_families, list)
                or not source_families
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in source_families
                )
                or source_families != sorted(set(source_families))
                or not isinstance(fetch_receipt, dict)
                or set(fetch_receipt) != {"started_at", "completed_at", "retrieval"}
                or not str(fetch_receipt.get("started_at") or "")
                or not str(fetch_receipt.get("completed_at") or "")
                or not isinstance(retrieval, dict)
                or retrieval.get("status") != 200
                or retrieval.get("sha256") != replay_payload.get("raw_source_sha256")
                or not str(retrieval.get("requested_url") or "")
                or not str(retrieval.get("final_url") or "")
                or not isinstance(retrieval.get("raw_bytes"), int)
                or isinstance(retrieval.get("raw_bytes"), bool)
                or int(retrieval["raw_bytes"]) < 1
            )
        )
        or any(
            not isinstance(replay_payload.get(field), str)
            or not str(replay_payload[field]).strip()
            for field in (
                "tokenizer_model_id",
                *(
                    ("source_family", "authorization_record_id")
                    if contract.adapter_id == FINANCE_TASK_REPLAY_ADAPTER[0]
                    else ()
                ),
                *(
                    ("authorization_record_id",)
                    if contract.adapter_id == MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0]
                    else ()
                ),
                *(
                    ("authorization_record_id",)
                    if contract.adapter_id == IETF_OAUTH_TASK_REPLAY_ADAPTER[0]
                    else ()
                ),
                *(
                    ("authorization_record_id",)
                    if contract.adapter_id
                    == ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER[0]
                    else ()
                ),
            )
        )
    ):
        raise ProvenanceError("task replay sidecar payload identity is invalid")
    return replay_payload


def _parse_parent_candidates(raw_utf8: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        lines = raw_utf8.splitlines()
        for line in lines:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError
            rows.append(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ProvenanceError("task replay parent candidates are invalid") from error
    if not rows:
        raise ProvenanceError("task replay parent candidates are empty")
    return tuple(rows)


def _verify_v3_derivation_payload(
    replay_payload: dict[str, Any], contract: TaskReplayAdapterContract
) -> tuple[dict[str, Any], ...]:
    parent_sidecar_raw = replay_payload.get("parent_sidecar_raw_utf8")
    parent_candidates_raw = replay_payload.get("parent_candidates_raw_utf8")
    if not isinstance(parent_sidecar_raw, str) or not isinstance(
        parent_candidates_raw, str
    ):
        raise ProvenanceError("task replay parent exact bytes are invalid")
    parent_sidecar_bytes = parent_sidecar_raw.encode("utf-8")
    parent_candidate_bytes = parent_candidates_raw.encode("utf-8")
    if (
        replay_payload.get("parent_sidecar_sha256")
        != hashlib.sha256(parent_sidecar_bytes).hexdigest()
        or replay_payload.get("parent_candidates_sha256")
        != hashlib.sha256(parent_candidate_bytes).hexdigest()
    ):
        raise ProvenanceError("task replay parent exact-byte digest mismatch")
    parent_sidecar = _json_object(parent_sidecar_bytes)
    if (
        parent_sidecar.get("schema_version") != TASK_REPLAY_SIDECAR_SCHEMA
        or parent_sidecar.get("adapter_id") != contract.adapter_id
        or parent_sidecar.get("adapter_revision") != contract.adapter_revision
    ):
        raise ProvenanceError("task replay parent sidecar identity is invalid")
    parents = _parse_parent_candidates(parent_candidates_raw)
    observed_digests = sorted(
        hashlib.sha256(canonical_attested_payload(row)).hexdigest() for row in parents
    )
    parent_commitment_by_digest = {
        hashlib.sha256(canonical_attested_payload(row)).hexdigest(): (
            task_candidate_content_commitment(row)
        )
        for row in parents
    }
    observed_commitments = sorted(
        parent_commitment_by_digest.values(),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    if (
        replay_payload.get("parent_candidate_digests") != observed_digests
        or replay_payload.get("parent_candidate_content_commitments")
        != observed_commitments
        or not isinstance(
            (parent_sidecar.get("replay_payload") or {}).get(
                "candidate_content_commitments"
            ),
            list,
        )
        or any(
            commitment
            not in parent_sidecar["replay_payload"]["candidate_content_commitments"]
            for commitment in observed_commitments
        )
        or replay_payload.get("projection_derivation_revision")
        != TASK_VIEW_DERIVATION_REVISION
    ):
        raise ProvenanceError("task replay parent candidate binding is invalid")
    receipts = replay_payload.get("projection_derivation_receipts")
    commitments = replay_payload.get("candidate_content_commitments")
    if not isinstance(receipts, list) or len(receipts) != len(commitments):
        raise ProvenanceError("task replay projection derivation receipts are invalid")
    parent_by_digest = parent_commitment_by_digest
    projection_commitments: list[dict[str, str]] = []
    identities: list[tuple[str, str, str]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != _V3_DERIVATION_FIELDS:
            raise ProvenanceError(
                "task replay projection derivation receipts are invalid"
            )
        parent_digest = str(receipt.get("parent_candidate_sha256") or "")
        projection_receipt = receipt.get("projection_receipt")
        projection_commitment = receipt.get("projection_content_commitment")
        if (
            parent_digest not in parent_by_digest
            or receipt.get("parent_content_commitment")
            != parent_by_digest[parent_digest]
            or not isinstance(projection_receipt, dict)
            or receipt.get("projection_receipt_sha256")
            != hashlib.sha256(
                json.dumps(
                    projection_receipt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            or not isinstance(projection_commitment, dict)
            or projection_receipt.get("parent_candidate_sha256") != parent_digest
            or projection_receipt.get("derivation_revision")
            != TASK_VIEW_DERIVATION_REVISION
            or projection_receipt.get("view") != projection_commitment.get("view")
        ):
            raise ProvenanceError(
                "task replay projection derivation receipt binding is invalid"
            )
        projection_commitments.append(projection_commitment)
        identities.append(
            (
                str(projection_commitment.get("world_id") or ""),
                str(projection_commitment.get("length_bucket") or ""),
                str(projection_commitment.get("view") or ""),
            )
        )
    if identities != sorted(identities) or projection_commitments != commitments:
        raise ProvenanceError("task replay projection derivation coverage is invalid")
    return parents


def task_candidate_content_commitment(
    candidate: Mapping[str, Any],
    *,
    sidecar_schema_version: str = TASK_REPLAY_SIDECAR_SCHEMA,
) -> dict[str, str]:
    """Commit to all source-sensitive candidate fields before trust metadata."""
    world_id = str(candidate.get("world_id") or "").strip()
    length_bucket = str(candidate.get("length_bucket") or "").strip()
    if not world_id or not length_bucket:
        raise ProvenanceError("task candidate content identity is incomplete")
    content = {
        key: value
        for key, value in candidate.items()
        if key not in _CONTENT_COMMITMENT_EXCLUDED_FIELDS
    }
    commitment = {
        "world_id": world_id,
        "length_bucket": length_bucket,
        "content_sha256": hashlib.sha256(
            json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }
    if sidecar_schema_version in {
        TASK_REPLAY_SIDECAR_SCHEMA_V2,
        TASK_REPLAY_SIDECAR_SCHEMA_V3,
    }:
        view = str(candidate.get("view") or "").strip()
        if not view:
            raise ProvenanceError("task candidate view identity is incomplete")
        commitment["view"] = view
    elif sidecar_schema_version != TASK_REPLAY_SIDECAR_SCHEMA:
        raise ProvenanceError("task replay sidecar schema is not registered")
    return commitment


def build_task_replay_sidecar(
    *,
    adapter_id: str,
    adapter_revision: str,
    replay_payload: Mapping[str, Any],
    source_attestation_key: bytes,
    sidecar_schema_version: str = TASK_REPLAY_SIDECAR_SCHEMA,
) -> dict[str, Any]:
    """Build one source-role-attested sidecar for a registered static adapter."""
    key = (adapter_id, adapter_revision, sidecar_schema_version)
    contract = TASK_REPLAY_ADAPTER_REGISTRY.get(key)
    if contract is None:
        raise ProvenanceError("task replay adapter contract is not registered")
    validated_payload = _verify_replay_payload(dict(replay_payload), contract)
    if sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        parent_raw = str(validated_payload["parent_sidecar_raw_utf8"]).encode()
        parent_binding = task_replay_sidecar_binding(
            parent_raw, source_attestation_key=source_attestation_key
        )
        if (
            parent_binding["sidecar_schema_version"] != TASK_REPLAY_SIDECAR_SCHEMA
            or parent_binding["sha256"] != validated_payload["parent_sidecar_sha256"]
        ):
            raise ProvenanceError("task replay parent sidecar is not verified v1")
    payload = {
        "schema_version": contract.sidecar_schema_version,
        "data_stage": "source_replay_sidecar",
        "adapter_id": contract.adapter_id,
        "adapter_revision": contract.adapter_revision,
        "replay_payload": dict(validated_payload),
        "train_ready": False,
        "production_eligible": False,
    }
    try:
        signed = attach_attestation(
            payload,
            source_attestation_key,
            purpose=TASK_REPLAY_SIDECAR_PURPOSE,
        )
    except ValueError as error:
        raise ProvenanceError(
            "task replay sidecar source-role identity is incomplete"
        ) from error
    attestation = signed.get("attestation")
    if (
        not isinstance(attestation, dict)
        or attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or attestation.get("role") != "source"
    ):
        raise ProvenanceError("task replay sidecar source-role identity is incomplete")
    return signed


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProvenanceError("task replay sidecar path is unsafe")
    if "\\" in value or ":" in value or "\x00" in value or glob.has_magic(value):
        raise ProvenanceError("task replay sidecar path is unsafe")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix.lower() != ".json"
    ):
        raise ProvenanceError("task replay sidecar path is unsafe")
    return value


def _read_sidecar_at(root: Path, relative: str, max_bytes: int) -> bytes:
    """Read through directory fds so no path component can be swapped to a link."""
    absolute_root = Path(os.path.abspath(root))
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute_root.anchor, directory_flags)
    try:
        for part in (*absolute_root.parts[1:], *PurePosixPath(relative).parts[:-1]):
            next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(
            PurePosixPath(relative).parts[-1], file_flags, dir_fd=descriptor
        )
        try:
            info = os.fstat(file_descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ProvenanceError("task replay sidecar is not a regular file")
            if info.st_size > max_bytes:
                raise ProvenanceError("task replay sidecar exceeds size limit")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(file_descriptor, min(1_048_576, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > max_bytes:
                raise ProvenanceError("task replay sidecar exceeds size limit")
            return raw
        finally:
            os.close(file_descriptor)
    except OSError as error:
        raise ProvenanceError(
            "task replay sidecar path is unavailable or unsafe"
        ) from error
    finally:
        os.close(descriptor)


def _binding_contract(
    binding: Mapping[str, Any],
) -> tuple[TaskReplayAdapterContract, str]:
    if set(binding) != _BINDING_FIELDS:
        raise ProvenanceError("task replay sidecar binding fields are invalid")
    adapter_id = binding.get("adapter_id")
    adapter_revision = binding.get("adapter_revision")
    sidecar_schema_version = binding.get("sidecar_schema_version")
    if not all(
        isinstance(value, str) and value and value == value.strip()
        for value in (adapter_id, adapter_revision, sidecar_schema_version)
    ):
        raise ProvenanceError("task replay sidecar binding identity is invalid")
    assert isinstance(adapter_id, str)
    assert isinstance(adapter_revision, str)
    assert isinstance(sidecar_schema_version, str)
    key = (adapter_id, adapter_revision, sidecar_schema_version)
    contract = TASK_REPLAY_ADAPTER_REGISTRY.get(key)
    if contract is None:
        raise ProvenanceError("task replay adapter contract is not registered")
    digest = binding.get("sha256")
    if not _is_sha256(digest):
        raise ProvenanceError("task replay sidecar binding digest is invalid")
    assert isinstance(digest, str)
    return contract, digest


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("task replay sidecar is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ProvenanceError("task replay sidecar must be a JSON object")
    return payload


def _verify_source_attestation(
    payload: dict[str, Any], source_attestation_key: bytes | None
) -> None:
    if PURPOSE_ROLES.get(TASK_REPLAY_SIDECAR_PURPOSE) != "source":
        raise ProvenanceError("task replay sidecar purpose is not source-role bound")
    raw_attestation = payload.get("attestation")
    if (
        not isinstance(raw_attestation, Mapping)
        or set(raw_attestation) != _ATTESTATION_FIELDS
        or raw_attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or raw_attestation.get("purpose") != TASK_REPLAY_SIDECAR_PURPOSE
        or raw_attestation.get("role") != "source"
        or not verify_attestation(
            payload,
            source_attestation_key,
            purpose=TASK_REPLAY_SIDECAR_PURPOSE,
        )
    ):
        raise ProvenanceError("task replay sidecar source-role attestation is invalid")


def _verify_sidecar_contract(
    payload: dict[str, Any], contract: TaskReplayAdapterContract
) -> dict[str, Any]:
    expected_fields = set(_SIDECAR_FIELDS)
    if LOCAL_PROBE_TRUST_ISOLATION_FIELD in payload:
        expected_fields.add(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
        if (
            payload.get(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
            != LOCAL_PROBE_TRUST_ISOLATION_VALUE
        ):
            raise ProvenanceError("task replay sidecar trust isolation is invalid")
    replay_payload = payload.get("replay_payload")
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != contract.sidecar_schema_version
        or payload.get("data_stage") != "source_replay_sidecar"
        or payload.get("adapter_id") != contract.adapter_id
        or payload.get("adapter_revision") != contract.adapter_revision
        or payload.get("train_ready") is not False
        or payload.get("production_eligible") is not False
    ):
        raise ProvenanceError("task replay sidecar identity or fields are invalid")
    return _verify_replay_payload(replay_payload, contract)


def task_replay_sidecar_binding(
    raw: bytes, *, source_attestation_key: bytes | None = None
) -> dict[str, str]:
    """Verify serialized source bytes and return the portable candidate binding."""
    if len(raw) > MAX_TASK_REPLAY_SIDECAR_BYTES:
        raise ProvenanceError("task replay sidecar exceeds size limit")
    payload = _json_object(raw)
    adapter_id = str(payload.get("adapter_id") or "")
    adapter_revision = str(payload.get("adapter_revision") or "")
    schema_version = str(payload.get("schema_version") or "")
    contract = TASK_REPLAY_ADAPTER_REGISTRY.get(
        (adapter_id, adapter_revision, schema_version)
    )
    if contract is None:
        raise ProvenanceError("task replay adapter contract is not registered")
    key = (
        source_attestation_key
        if source_attestation_key is not None
        else attestation_key_from_env(TASK_REPLAY_SIDECAR_PURPOSE)
    )
    _verify_source_attestation(payload, key)
    _verify_sidecar_contract(payload, contract)
    return {
        "adapter_id": contract.adapter_id,
        "adapter_revision": contract.adapter_revision,
        "sidecar_schema_version": contract.sidecar_schema_version,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def load_task_replay_sidecar(
    root: Path,
    relative_path: str,
    binding: Mapping[str, Any],
    *,
    source_attestation_key: bytes | None = None,
) -> LoadedTaskReplaySidecar:
    """Load one exact, registered sidecar without importing executable adapters."""
    contract, declared_sha256 = _binding_contract(binding)
    relative_path = _safe_relative_path(relative_path)
    try:
        raw = _read_sidecar_at(root, relative_path, contract.max_bytes)
    except ProvenanceError as error:
        raise ProvenanceError(
            f"cannot read task replay sidecar: {relative_path}"
        ) from error
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if observed_sha256 != declared_sha256:
        raise ProvenanceError("task replay sidecar exact-byte digest mismatch")
    payload = _json_object(raw)
    key = (
        source_attestation_key
        if source_attestation_key is not None
        else attestation_key_from_env(TASK_REPLAY_SIDECAR_PURPOSE)
    )
    _verify_source_attestation(payload, key)
    replay_payload = _verify_sidecar_contract(payload, contract)
    parent_candidates: tuple[dict[str, Any], ...] = ()
    if contract.sidecar_schema_version == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        parent_raw = str(replay_payload["parent_sidecar_raw_utf8"]).encode()
        parent_binding = task_replay_sidecar_binding(
            parent_raw, source_attestation_key=key
        )
        if (
            parent_binding["sidecar_schema_version"] != TASK_REPLAY_SIDECAR_SCHEMA
            or parent_binding["sha256"] != replay_payload["parent_sidecar_sha256"]
        ):
            raise ProvenanceError("task replay parent sidecar is not verified v1")
        parent_candidates = _verify_v3_derivation_payload(replay_payload, contract)
    return LoadedTaskReplaySidecar(
        adapter_id=contract.adapter_id,
        adapter_revision=contract.adapter_revision,
        sidecar_schema_version=contract.sidecar_schema_version,
        relative_path=relative_path,
        sidecar_sha256=observed_sha256,
        raw_bytes=raw,
        signed_sidecar=payload,
        replay_payload=replay_payload,
        parent_candidates=parent_candidates,
    )
