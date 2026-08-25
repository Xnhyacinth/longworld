from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from longworld.core.production_trust import (
    APPROVAL_DIGEST_ENV,
    APPROVAL_PATH_ENV,
    TRUST_ROOTS_DIGEST_ENV,
    TRUST_ROOTS_PATH_ENV,
    canonical_approval_statement,
    is_verified_production_approval_receipt,
    verify_embedded_production_approval_from_env,
    verify_production_approval_from_env,
)


def _write_approval_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    key_id = (
        "aws-kms://arn:aws:kms:us-east-1:123456789012:"
        "key/11111111-2222-3333-4444-555555555555"
    )
    statement = {
        "schema_version": "longworld-production-approval-v1",
        "approval_scope": "longworld-production-release",
        "decision": "approved",
        "release_profile_id": "p3-production-48-v1",
        "release_profile_sha256": "a" * 64,
        "quality_report_sha256": "b" * 64,
        "train_sha256": "c" * 64,
        "eval_sha256": "d" * 64,
        "release_selection_sha256": "e" * 64,
        "approval_authority": "release-governance",
        "approval_nonce": "approval-2026-08-25-001",
        "issued_at": "2026-08-25T12:00:00Z",
    }
    signature = private_key.sign(
        canonical_approval_statement(statement), ec.ECDSA(hashes.SHA256())
    )
    approval = {
        "statement": statement,
        "signature": {
            "scheme": "ecdsa-p256-sha256-v1",
            "key_id": key_id,
            "value_base64": base64.b64encode(signature).decode(),
        },
    }
    trust_roots = {
        "schema_version": "longworld-production-trust-roots-v1",
        "keys": [
            {
                "key_id": key_id,
                "scheme": "ecdsa-p256-sha256-v1",
                "public_key_pem": public_pem,
                "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
                "provider": "aws-kms",
                "approval_authorities": ["release-governance"],
            }
        ],
    }
    approval_path = tmp_path / "approval.json"
    roots_path = tmp_path / "trust-roots.json"
    approval_path.write_text(json.dumps(approval, sort_keys=True))
    roots_path.write_text(json.dumps(trust_roots, sort_keys=True))
    monkeypatch.setenv(APPROVAL_PATH_ENV, str(approval_path))
    monkeypatch.setenv(TRUST_ROOTS_PATH_ENV, str(roots_path))
    monkeypatch.setenv(
        APPROVAL_DIGEST_ENV, hashlib.sha256(approval_path.read_bytes()).hexdigest()
    )
    monkeypatch.setenv(
        TRUST_ROOTS_DIGEST_ENV, hashlib.sha256(roots_path.read_bytes()).hexdigest()
    )
    return statement


def test_external_kms_approval_binds_exact_release_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statement = _write_approval_fixture(tmp_path, monkeypatch)

    receipt = verify_production_approval_from_env(
        release_profile_id=statement["release_profile_id"],
        release_profile_sha256=statement["release_profile_sha256"],
        source_file_sha256={
            "quality_report.json": statement["quality_report_sha256"],
            "train.jsonl": statement["train_sha256"],
            "eval.jsonl": statement["eval_sha256"],
        },
        release_selection_sha256=statement["release_selection_sha256"],
    )

    assert receipt["verified"] is True
    assert receipt["approval_key_id"].startswith("aws-kms://")
    assert len(receipt["approval_envelope_sha256"]) == 64
    assert is_verified_production_approval_receipt(receipt)
    assert not is_verified_production_approval_receipt(
        {**receipt, "approval_key_id": "local-self-signed"}
    )
    assert (
        verify_embedded_production_approval_from_env(
            receipt,
            release_profile_id=statement["release_profile_id"],
            release_profile_sha256=statement["release_profile_sha256"],
            source_file_sha256={
                "quality_report.json": statement["quality_report_sha256"],
                "train.jsonl": statement["train_sha256"],
                "eval.jsonl": statement["eval_sha256"],
            },
        )
        == receipt
    )


def test_embedded_production_approval_rejects_shape_only_forgery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statement = _write_approval_fixture(tmp_path, monkeypatch)
    receipt = verify_production_approval_from_env(
        release_profile_id=statement["release_profile_id"],
        release_profile_sha256=statement["release_profile_sha256"],
        source_file_sha256={
            "quality_report.json": statement["quality_report_sha256"],
            "train.jsonl": statement["train_sha256"],
            "eval.jsonl": statement["eval_sha256"],
        },
        release_selection_sha256=statement["release_selection_sha256"],
    )
    forged = {
        **receipt,
        "signature": {**receipt["signature"], "value_base64": "Zm9yZ2Vk"},
    }

    with pytest.raises(ValueError, match="signature|metadata"):
        verify_embedded_production_approval_from_env(
            forged,
            release_profile_id=statement["release_profile_id"],
            release_profile_sha256=statement["release_profile_sha256"],
            source_file_sha256={
                "quality_report.json": statement["quality_report_sha256"],
                "train.jsonl": statement["train_sha256"],
                "eval.jsonl": statement["eval_sha256"],
            },
        )


def test_external_approval_rejects_changed_release_or_unpinned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statement = _write_approval_fixture(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="release bytes"):
        verify_production_approval_from_env(
            release_profile_id=statement["release_profile_id"],
            release_profile_sha256=statement["release_profile_sha256"],
            source_file_sha256={
                "quality_report.json": "0" * 64,
                "train.jsonl": statement["train_sha256"],
                "eval.jsonl": statement["eval_sha256"],
            },
            release_selection_sha256=statement["release_selection_sha256"],
        )

    monkeypatch.setenv(APPROVAL_DIGEST_ENV, "0" * 64)
    with pytest.raises(ValueError, match="approval digest pin"):
        verify_production_approval_from_env(
            release_profile_id=statement["release_profile_id"],
            release_profile_sha256=statement["release_profile_sha256"],
            source_file_sha256={
                "quality_report.json": statement["quality_report_sha256"],
                "train.jsonl": statement["train_sha256"],
                "eval.jsonl": statement["eval_sha256"],
            },
            release_selection_sha256=statement["release_selection_sha256"],
        )


def test_production_approval_fails_closed_without_external_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        APPROVAL_PATH_ENV,
        APPROVAL_DIGEST_ENV,
        TRUST_ROOTS_PATH_ENV,
        TRUST_ROOTS_DIGEST_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="external production approval"):
        verify_production_approval_from_env(
            release_profile_id="p3-production-48-v1",
            release_profile_sha256="a" * 64,
            source_file_sha256={
                "quality_report.json": "b" * 64,
                "train.jsonl": "c" * 64,
                "eval.jsonl": "d" * 64,
            },
            release_selection_sha256="e" * 64,
        )
