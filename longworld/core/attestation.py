"""HMAC attestations for production manifests and serialized training rows."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

ATTESTATION_ENV = "LONGWORLD_ATTESTATION_KEY"
ATTESTATION_SCHEME = "hmac-sha256"
ATTESTATION_V2_SCHEME = "hmac-sha256-v2"
ATTESTATION_ENVIRONMENT_ENV = "LONGWORLD_ATTESTATION_ENVIRONMENT"
LOCAL_PROBE_COMBINED_ROLES_ENV = "LONGWORLD_LOCAL_PROBE_COMBINED_ROLES"
LOCAL_PROBE_TRUST_ISOLATION_FIELD = "local_probe_trust_isolation"
LOCAL_PROBE_TRUST_ISOLATION_VALUE = "non_independent_local_diagnostic"
ATTESTATION_ROLES = (
    "source",
    "candidate",
    "ranker",
    "auditor",
    "promotion",
    "report",
)
ROLE_KEY_ENVS = {
    role: f"LONGWORLD_{role.upper()}_ATTESTATION_KEY" for role in ATTESTATION_ROLES
}
ROLE_KEY_ID_ENVS = {
    role: f"LONGWORLD_{role.upper()}_ATTESTATION_KEY_ID" for role in ATTESTATION_ROLES
}
PREDECESSOR_GATE_KEY_ENV = "LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY"
PREDECESSOR_GATE_KEY_ID_ENV = "LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY_ID"
PURPOSE_ROLES = {
    "source_manifest": "source",
    "artifact_semantics": "source",
    "git_workflow": "source",
    "episode_replay_bundle": "source",
    "source_workflow_bundle": "source",
    "source_workflow_component": "source",
    "candidate_row": "candidate",
    "dense_ranking": "ranker",
    "dense_retrieval_audit": "auditor",
    "release_world_selection": "auditor",
    "release_gate_pass": "auditor",
    "sft_row": "promotion",
    "cpt_row": "promotion",
    "quality_report": "report",
    "training_export_manifest": "report",
    "release_inventory": "report",
}
_REJECTED_KEYS = {b"use-a-secret-of-at-least-32-bytes"}
_NON_PRODUCTION_KEY_ID_PREFIXES = ("probe", "dev", "test", "local", "example")


def local_probe_diagnostic_metadata() -> dict[str, Any]:
    """Return signed trust labels for an explicitly combined local probe."""
    if (
        os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower() == "probe"
        and os.environ.get(LOCAL_PROBE_COMBINED_ROLES_ENV, "").strip()
        == LOCAL_PROBE_TRUST_ISOLATION_VALUE
    ):
        return {
            "trust_scope": "local_probe",
            "diagnostic_only": True,
            "content_gate_eligible": True,
            "trust_valid_for_production": False,
            "production_eligible": False,
        }
    return {}


def attestation_environment_names() -> tuple[str, ...]:
    """Return every producer credential/environment variable."""
    return (
        ATTESTATION_ENV,
        ATTESTATION_ENVIRONMENT_ENV,
        *ROLE_KEY_ENVS.values(),
        *ROLE_KEY_ID_ENVS.values(),
        PREDECESSOR_GATE_KEY_ENV,
        PREDECESSOR_GATE_KEY_ID_ENV,
    )


@contextmanager
def sanitized_attestation_environment() -> Iterator[None]:
    """Hide producer credentials while third-party model code is executing."""
    saved = {
        name: os.environ.pop(name)
        for name in attestation_environment_names()
        if name in os.environ
    }
    try:
        yield
    finally:
        os.environ.update(saved)


def _valid_key(key: bytes | None) -> bool:
    return key is not None and len(key) >= 32 and key not in _REJECTED_KEYS


def attestation_key_from_env(purpose: str | None = None) -> bytes | None:
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    role = PURPOSE_ROLES.get(purpose or "")
    if role is not None:
        value = os.environ.get(ROLE_KEY_ENVS[role], "").encode()
        if _valid_key(value):
            return value
    if environment == "production":
        return None
    value = os.environ.get(ATTESTATION_ENV, "").encode()
    return value if _valid_key(value) else None


def canonical_attested_payload(value: dict[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "attestation"}
    return json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _message(
    value: dict[str, Any],
    purpose: str,
    *,
    role: str = "",
    key_id: str = "",
    environment: str = "",
) -> bytes:
    prefix = purpose.encode() + b"\0"
    if role or key_id or environment:
        prefix += (
            role.encode()
            + b"\0"
            + key_id.encode()
            + b"\0"
            + environment.encode()
            + b"\0"
        )
    return prefix + canonical_attested_payload(value)


def _role_identity_for_key(key: bytes, purpose: str) -> tuple[str, str, str] | None:
    role = PURPOSE_ROLES.get(purpose)
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    if role is None or environment not in {"probe", "production"}:
        return None
    configured = os.environ.get(ROLE_KEY_ENVS[role], "").encode()
    key_id = os.environ.get(ROLE_KEY_ID_ENVS[role], "").strip()
    if not _valid_key(configured) or configured != key or not key_id:
        if environment == "production":
            raise ValueError(f"production {role} attestation identity is incomplete")
        return None
    return role, key_id, environment


def attach_attestation(
    value: dict[str, Any], key: bytes, *, purpose: str = "generic"
) -> dict[str, Any]:
    if not _valid_key(key):
        raise ValueError("attestation key must contain at least 32 bytes")
    signed = dict(value)
    combined_mode = os.environ.get(LOCAL_PROBE_COMBINED_ROLES_ENV, "").strip()
    declared_isolation = signed.get(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
    if combined_mode == LOCAL_PROBE_TRUST_ISOLATION_VALUE:
        if os.environ.get(
            ATTESTATION_ENVIRONMENT_ENV, ""
        ).strip().lower() != "probe" or declared_isolation not in {
            None,
            LOCAL_PROBE_TRUST_ISOLATION_VALUE,
        }:
            raise ValueError("local probe trust isolation marker is invalid")
        signed[LOCAL_PROBE_TRUST_ISOLATION_FIELD] = LOCAL_PROBE_TRUST_ISOLATION_VALUE
    elif declared_isolation is not None:
        raise ValueError("local probe trust isolation marker is not active")
    identity = _role_identity_for_key(key, purpose)
    if identity is None:
        signed["attestation"] = {
            "scheme": ATTESTATION_SCHEME,
            "purpose": purpose,
            "digest": hmac.new(
                key, _message(signed, purpose), hashlib.sha256
            ).hexdigest(),
        }
    else:
        role, key_id, environment = identity
        signed["attestation"] = {
            "scheme": ATTESTATION_V2_SCHEME,
            "purpose": purpose,
            "role": role,
            "key_id": key_id,
            "environment": environment,
            "digest": hmac.new(
                key,
                _message(
                    signed,
                    purpose,
                    role=role,
                    key_id=key_id,
                    environment=environment,
                ),
                hashlib.sha256,
            ).hexdigest(),
        }
    return signed


def verify_attestation(
    value: dict[str, Any], key: bytes | None, *, purpose: str = "generic"
) -> bool:
    if not _valid_key(key):
        return False
    assert key is not None
    raw = value.get("attestation")
    if not isinstance(raw, dict) or raw.get("purpose") != purpose:
        return False
    digest = str(raw.get("digest") or "")
    scheme = raw.get("scheme")
    current_environment = (
        os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    )
    if scheme == ATTESTATION_SCHEME:
        if current_environment in {"probe", "production"}:
            return False
        message = _message(value, purpose)
    elif scheme == ATTESTATION_V2_SCHEME:
        role = str(raw.get("role") or "")
        key_id = str(raw.get("key_id") or "")
        environment = str(raw.get("environment") or "")
        if (
            role != PURPOSE_ROLES.get(purpose)
            or not key_id
            or environment not in {"probe", "production"}
            or current_environment not in {"probe", "production"}
        ):
            return False
        configured = os.environ.get(ROLE_KEY_ENVS[role], "").encode()
        configured_key_id = os.environ.get(ROLE_KEY_ID_ENVS[role], "").strip()
        if (
            not _valid_key(configured)
            or key != configured
            or key_id != configured_key_id
            or environment != current_environment
        ):
            return False
        message = _message(
            value,
            purpose,
            role=role,
            key_id=key_id,
            environment=environment,
        )
    else:
        return False
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def verify_attestation_identity(
    value: dict[str, Any],
    key: bytes | None,
    *,
    purpose: str,
    role: str,
    key_id: str,
    environment: str,
) -> bool:
    """Verify a pinned v2 producer identity independently of the current process."""
    if (
        not _valid_key(key)
        or role != PURPOSE_ROLES.get(purpose)
        or not key_id
        or environment not in {"probe", "production"}
    ):
        return False
    assert key is not None
    raw = value.get("attestation")
    if not isinstance(raw, dict) or raw != {
        "scheme": ATTESTATION_V2_SCHEME,
        "purpose": purpose,
        "role": role,
        "key_id": key_id,
        "environment": environment,
        "digest": raw.get("digest"),
    }:
        return False
    expected = hmac.new(
        key,
        _message(
            value,
            purpose,
            role=role,
            key_id=key_id,
            environment=environment,
        ),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(str(raw.get("digest") or ""), expected)


def production_attestation_errors() -> list[str]:
    """Return fail-closed role/key identity errors for production release mode."""
    errors: list[str] = []
    if os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower() != (
        "production"
    ):
        errors.append("attestation environment must be production")
    if os.environ.get(LOCAL_PROBE_COMBINED_ROLES_ENV):
        errors.append("combined local-probe roles cannot be used in production")
    keys: list[bytes] = []
    key_ids: list[str] = []
    for role in ATTESTATION_ROLES:
        key = os.environ.get(ROLE_KEY_ENVS[role], "").encode()
        key_id = os.environ.get(ROLE_KEY_ID_ENVS[role], "").strip()
        if not _valid_key(key) or not key_id:
            errors.append(f"production {role} key/key_id is missing")
            continue
        keys.append(key)
        key_ids.append(key_id)
    if len(keys) == len(ATTESTATION_ROLES) and len(set(keys)) != len(keys):
        errors.append("role keys must be distinct")
    if len(key_ids) == len(ATTESTATION_ROLES) and len(set(key_ids)) != len(key_ids):
        errors.append("role key ids must be distinct")
    if any(
        key_id.lower().startswith(_NON_PRODUCTION_KEY_ID_PREFIXES) for key_id in key_ids
    ):
        errors.append("production key ids cannot be probe/dev/test identities")
    return errors
