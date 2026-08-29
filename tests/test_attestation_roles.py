from __future__ import annotations

import os

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
    attestation_key_from_env,
    production_attestation_errors,
    verify_attestation,
    verify_attestation_identity,
)


def _configure_role(monkeypatch: pytest.MonkeyPatch, role: str, marker: str) -> bytes:
    key = f"longworld-{role}-{marker}-key-material-32-bytes".encode()
    monkeypatch.setenv(ROLE_KEY_ENVS[role], key.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS[role], f"prod-{role}-{marker}")
    return key


def test_production_role_signature_binds_environment_role_and_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    candidate_key = _configure_role(monkeypatch, "candidate", "v1")
    report_key = _configure_role(monkeypatch, "report", "v1")

    signed = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")

    assert signed["attestation"] == {
        "scheme": "hmac-sha256-v2",
        "purpose": "candidate_row",
        "role": "candidate",
        "key_id": "prod-candidate-v1",
        "environment": "production",
        "digest": signed["attestation"]["digest"],
    }
    assert verify_attestation(signed, candidate_key, purpose="candidate_row")
    assert not verify_attestation(signed, report_key, purpose="candidate_row")


def test_production_release_inventory_uses_the_report_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    report_key = _configure_role(monkeypatch, "report", "inventory-v1")

    signed = attach_attestation(
        {"production_eligible": True}, report_key, purpose="release_inventory"
    )

    assert signed["attestation"]["scheme"] == "hmac-sha256-v2"
    assert signed["attestation"]["role"] == "report"
    assert signed["attestation"]["environment"] == "production"
    assert verify_attestation(signed, report_key, purpose="release_inventory")


def test_production_never_falls_back_to_the_legacy_shared_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    monkeypatch.setenv(ATTESTATION_ENV, "legacy-shared-key-material-at-least-32-bytes")

    assert attestation_key_from_env("dense_ranking") is None


def test_verification_rejects_a_retired_key_id_even_when_key_bytes_are_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    candidate_key = _configure_role(monkeypatch, "candidate", "v1")
    signed = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")

    monkeypatch.setenv(ROLE_KEY_ID_ENVS["candidate"], "prod-candidate-v2")

    assert not verify_attestation(signed, candidate_key, purpose="candidate_row")


def test_production_rejects_probe_and_legacy_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_key = _configure_role(monkeypatch, "candidate", "v1")
    legacy = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    probe = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")

    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")

    assert not verify_attestation(legacy, candidate_key, purpose="candidate_row")
    assert not verify_attestation(probe, candidate_key, purpose="candidate_row")


def test_explicit_identity_verifier_accepts_only_the_pinned_cross_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    auditor_key = _configure_role(monkeypatch, "auditor", "gate-v1")
    key_id = os.environ[ROLE_KEY_ID_ENVS["auditor"]]
    signed = attach_attestation({"ok": True}, auditor_key, purpose="release_gate_pass")
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")

    assert not verify_attestation(signed, auditor_key, purpose="release_gate_pass")
    assert verify_attestation_identity(
        signed,
        auditor_key,
        purpose="release_gate_pass",
        role="auditor",
        key_id=key_id,
        environment="probe",
    )
    assert not verify_attestation_identity(
        signed,
        auditor_key,
        purpose="release_gate_pass",
        role="auditor",
        key_id="wrong-key-id",
        environment="probe",
    )


def test_probe_rejects_legacy_signatures_without_role_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_key = _configure_role(monkeypatch, "candidate", "v1")
    legacy = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")

    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")

    assert not verify_attestation(legacy, candidate_key, purpose="candidate_row")


def test_v2_verification_requires_an_active_role_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    candidate_key = _configure_role(monkeypatch, "candidate", "v1")
    signed = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")

    monkeypatch.delenv(ATTESTATION_ENVIRONMENT_ENV)
    monkeypatch.delenv(ROLE_KEY_ID_ENVS["candidate"])

    assert not verify_attestation(signed, candidate_key, purpose="candidate_row")


def test_probe_artifact_semantics_use_the_source_role_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    source_key = _configure_role(monkeypatch, "source", "semantic-v1")
    wrong_key = b"longworld-wrong-source-key-material-32-bytes"

    signed = attach_attestation(
        {"artifact_id": "a1", "text": "grounded workflow evidence"},
        source_key,
        purpose="artifact_semantics",
    )

    assert signed["attestation"]["scheme"] == "hmac-sha256-v2"
    assert signed["attestation"]["role"] == "source"
    assert verify_attestation(signed, source_key, purpose="artifact_semantics")
    assert not verify_attestation(signed, wrong_key, purpose="artifact_semantics")
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "prod-source-semantic-v2")
    assert not verify_attestation(signed, source_key, purpose="artifact_semantics")
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "prod-source-semantic-v1")
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    assert not verify_attestation(signed, source_key, purpose="artifact_semantics")


def test_combined_local_probe_isolation_marker_is_signed_and_cannot_be_forged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(
        "LONGWORLD_LOCAL_PROBE_COMBINED_ROLES", LOCAL_PROBE_TRUST_ISOLATION_VALUE
    )
    candidate_key = _configure_role(monkeypatch, "candidate", "combined-v1")

    signed = attach_attestation({"row": 1}, candidate_key, purpose="candidate_row")

    assert signed[LOCAL_PROBE_TRUST_ISOLATION_FIELD] == (
        LOCAL_PROBE_TRUST_ISOLATION_VALUE
    )
    assert verify_attestation(signed, candidate_key, purpose="candidate_row")
    signed[LOCAL_PROBE_TRUST_ISOLATION_FIELD] = "independent"
    assert not verify_attestation(signed, candidate_key, purpose="candidate_row")


def test_local_probe_isolation_marker_cannot_be_claimed_without_combined_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    candidate_key = _configure_role(monkeypatch, "candidate", "single-v1")

    with pytest.raises(ValueError, match="local probe trust isolation"):
        attach_attestation(
            {
                "row": 1,
                LOCAL_PROBE_TRUST_ISOLATION_FIELD: LOCAL_PROBE_TRUST_ISOLATION_VALUE,
            },
            candidate_key,
            purpose="candidate_row",
        )


def test_production_readiness_rejects_missing_duplicate_or_probe_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    for role in ROLE_KEY_ENVS:
        _configure_role(monkeypatch, role, "v1")
    assert production_attestation_errors() == []

    monkeypatch.setenv(ROLE_KEY_ENVS["report"], os.environ[ROLE_KEY_ENVS["source"]])
    assert "role keys must be distinct" in production_attestation_errors()

    _configure_role(monkeypatch, "report", "v2")
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["report"], "probe-report-v2")
    assert "production key ids cannot be probe/dev/test identities" in (
        production_attestation_errors()
    )
