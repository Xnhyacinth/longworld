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
    verify_attestation,
)
from longworld.core.provenance import ProvenanceError

TASK_REPLAY_SIDECAR_SCHEMA = "longworld.task-replay-sidecar.v1"
TASK_REPLAY_SIDECAR_PURPOSE = "task_replay_sidecar"
MAX_TASK_REPLAY_SIDECAR_BYTES = 16_000_000

CYBER_KEV_TASK_REPLAY_ADAPTER = (
    "cyber.kev_history.v1",
    "longworld.kev-catalog-history-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
)
FINANCE_TASK_REPLAY_ADAPTER = (
    "finance.multi_filing.v1",
    "longworld.financial-history-replay.v1",
    TASK_REPLAY_SIDECAR_SCHEMA,
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

    @property
    def registry_key(self) -> TaskReplayRegistryKey:
        return (
            self.adapter_id,
            self.adapter_revision,
            self.sidecar_schema_version,
        )


def _contract(key: TaskReplayRegistryKey) -> TaskReplayAdapterContract:
    payload_fields = (
        frozenset(
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
        if key == CYBER_KEV_TASK_REPLAY_ADAPTER
        else frozenset(
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
    )
    return TaskReplayAdapterContract(*key, replay_payload_fields=payload_fields)


TASK_REPLAY_ADAPTER_REGISTRY: Mapping[
    TaskReplayRegistryKey, TaskReplayAdapterContract
] = MappingProxyType(
    {
        key: _contract(key)
        for key in (
            CYBER_KEV_TASK_REPLAY_ADAPTER,
            FINANCE_TASK_REPLAY_ADAPTER,
        )
    }
)


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
    }
    if any(
        field in replay_payload and not _is_sha256(replay_payload[field])
        for field in sha_fields
    ):
        raise ProvenanceError("task replay sidecar payload digest is invalid")
    commitments = replay_payload.get("candidate_content_commitments")
    if (
        not isinstance(commitments, list)
        or not commitments
        or any(
            not isinstance(item, dict)
            or set(item) != _CONTENT_COMMITMENT_FIELDS
            or not str(item.get("world_id") or "").strip()
            or not str(item.get("length_bucket") or "").strip()
            or not _is_sha256(item.get("content_sha256"))
            for item in commitments
        )
        or commitments
        != sorted(
            commitments,
            key=lambda item: (str(item["world_id"]), str(item["length_bucket"])),
        )
        or len(
            {
                (str(item["world_id"]), str(item["length_bucket"]))
                for item in commitments
            }
        )
        != len(commitments)
    ):
        raise ProvenanceError("task replay sidecar content commitments are invalid")
    tokenizer_revision = replay_payload.get("tokenizer_revision")
    if (
        not isinstance(tokenizer_revision, str)
        or len(tokenizer_revision) != 40
        or any(character not in "0123456789abcdef" for character in tokenizer_revision)
        or replay_payload.get("replay_revision") != contract.adapter_revision
        or any(
            not isinstance(replay_payload.get(field), str)
            or not str(replay_payload[field]).strip()
            for field in (
                "tokenizer_model_id",
                *(
                    ("source_family", "authorization_record_id")
                    if contract.registry_key == FINANCE_TASK_REPLAY_ADAPTER
                    else ()
                ),
            )
        )
    ):
        raise ProvenanceError("task replay sidecar payload identity is invalid")
    return replay_payload


def task_candidate_content_commitment(candidate: Mapping[str, Any]) -> dict[str, str]:
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
    return {
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


def build_task_replay_sidecar(
    *,
    adapter_id: str,
    adapter_revision: str,
    replay_payload: Mapping[str, Any],
    source_attestation_key: bytes,
) -> dict[str, Any]:
    """Build one source-role-attested sidecar for a registered static adapter."""
    key = (adapter_id, adapter_revision, TASK_REPLAY_SIDECAR_SCHEMA)
    contract = TASK_REPLAY_ADAPTER_REGISTRY.get(key)
    if contract is None:
        raise ProvenanceError("task replay adapter contract is not registered")
    validated_payload = _verify_replay_payload(dict(replay_payload), contract)
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
    return LoadedTaskReplaySidecar(
        adapter_id=contract.adapter_id,
        adapter_revision=contract.adapter_revision,
        sidecar_schema_version=contract.sidecar_schema_version,
        relative_path=relative_path,
        sidecar_sha256=observed_sha256,
        raw_bytes=raw,
        signed_sidecar=payload,
        replay_payload=replay_payload,
    )
