"""External asymmetric approval for production release snapshots.

LongWorld only verifies approvals. The private key and signing operation stay in
an independent KMS or offline approval service.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

TRUST_ROOTS_PATH_ENV = "LONGWORLD_PRODUCTION_TRUST_ROOTS_PATH"
TRUST_ROOTS_DIGEST_ENV = "LONGWORLD_PRODUCTION_TRUST_ROOTS_SHA256"
APPROVAL_PATH_ENV = "LONGWORLD_PRODUCTION_APPROVAL_PATH"
APPROVAL_DIGEST_ENV = "LONGWORLD_PRODUCTION_APPROVAL_SHA256"

TRUST_ROOTS_SCHEMA = "longworld-production-trust-roots-v1"
APPROVAL_SCHEMA = "longworld-production-approval-v1"
APPROVAL_SCHEME = "ecdsa-p256-sha256-v1"

_STATEMENT_KEYS = {
    "schema_version",
    "approval_scope",
    "decision",
    "release_profile_id",
    "release_profile_sha256",
    "quality_report_sha256",
    "train_sha256",
    "eval_sha256",
    "release_selection_sha256",
    "approval_authority",
    "approval_nonce",
    "issued_at",
}
_NON_PRODUCTION_MARKERS = ("local", "probe", "test", "example", "dev")
_VERIFIED_RECEIPT_KEYS = {
    "verified",
    "scheme",
    "approval_key_id",
    "approval_envelope_sha256",
    "trust_roots_sha256",
    "approval_statement_sha256",
    "statement",
    "signature",
}


def canonical_approval_statement(statement: dict[str, Any]) -> bytes:
    """Return the exact bytes an external approval authority must sign."""
    return json.dumps(
        statement,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text
    )


def is_verified_production_approval_receipt(value: object) -> bool:
    """Validate the complete embedded-envelope shape, without trusting it."""
    if not isinstance(value, dict) or set(value) != _VERIFIED_RECEIPT_KEYS:
        return False
    key_id = str(value.get("approval_key_id") or "")
    return bool(
        value.get("verified") is True
        and value.get("scheme") == APPROVAL_SCHEME
        and key_id
        and not any(marker in key_id.lower() for marker in _NON_PRODUCTION_MARKERS)
        and all(
            _valid_sha256(value.get(field))
            for field in (
                "approval_envelope_sha256",
                "trust_roots_sha256",
                "approval_statement_sha256",
            )
        )
        and isinstance(value.get("statement"), dict)
        and isinstance(value.get("signature"), dict)
    )


def _read_pinned_json(path_value: str, digest: str, *, label: str) -> tuple[dict, str]:
    if not path_value or not _valid_sha256(digest):
        raise ValueError(f"external production approval {label} is not configured")
    path = Path(path_value)
    if not path.is_file():
        raise ValueError(f"external production approval {label} file is missing")
    raw = path.read_bytes()
    actual = _sha256(raw)
    if actual != digest:
        raise ValueError(f"{label} digest pin does not match")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"external production approval {label} is invalid JSON"
        ) from error
    if not isinstance(value, dict):
        raise TypeError(f"external production approval {label} must be an object")
    return value, actual


def _load_trust_key(
    trust_roots: dict, key_id: str, approval_authority: str
) -> ec.EllipticCurvePublicKey:
    if trust_roots.get("schema_version") != TRUST_ROOTS_SCHEMA:
        raise ValueError("production trust roots schema is invalid")
    keys = trust_roots.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ValueError("production trust roots contain no keys")
    matching = [
        item for item in keys if isinstance(item, dict) and item.get("key_id") == key_id
    ]
    if len(matching) != 1:
        raise ValueError("approval key id is not uniquely pinned")
    root = matching[0]
    authorities = root.get("approval_authorities")
    if (
        root.get("scheme") != APPROVAL_SCHEME
        or root.get("provider") not in {"aws-kms", "gcp-kms", "azure-key-vault"}
        or any(marker in key_id.lower() for marker in _NON_PRODUCTION_MARKERS)
        or not isinstance(authorities, list)
        or approval_authority not in authorities
    ):
        raise ValueError("approval key is not a production KMS identity")
    try:
        public_key = serialization.load_pem_public_key(
            str(root.get("public_key_pem") or "").encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise ValueError("approval public key is invalid") from error
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise TypeError("approval public key must be ECDSA P-256")
    public_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if not _valid_sha256(root.get("public_key_sha256")) or _sha256(
        public_der
    ) != root.get("public_key_sha256"):
        raise ValueError("approval public key digest does not match")
    return public_key


def _validate_statement(
    statement: dict[str, Any],
    *,
    release_profile_id: str,
    release_profile_sha256: str,
    source_file_sha256: dict[str, str],
    release_selection_sha256: str,
) -> None:
    if set(statement) != _STATEMENT_KEYS:
        raise ValueError("production approval statement fields are invalid")
    if (
        statement.get("schema_version") != APPROVAL_SCHEMA
        or statement.get("approval_scope") != "longworld-production-release"
        or statement.get("decision") != "approved"
        or not str(statement.get("approval_authority") or "").strip()
        or not str(statement.get("approval_nonce") or "").strip()
        or not str(statement.get("issued_at") or "").endswith("Z")
    ):
        raise ValueError("production approval decision is invalid")
    expected = {
        "release_profile_id": release_profile_id,
        "release_profile_sha256": release_profile_sha256,
        "quality_report_sha256": source_file_sha256.get("quality_report.json"),
        "train_sha256": source_file_sha256.get("train.jsonl"),
        "eval_sha256": source_file_sha256.get("eval.jsonl"),
        "release_selection_sha256": release_selection_sha256,
    }
    if any(statement.get(field) != value for field, value in expected.items()):
        raise ValueError("production approval does not bind the exact release bytes")
    if any(
        not _valid_sha256(statement.get(field))
        for field in expected
        if field != "release_profile_id"
    ):
        raise ValueError("production approval contains an invalid digest")


def _verify_approval_envelope(
    approval: dict[str, Any],
    *,
    roots: dict[str, Any],
    roots_digest: str,
    release_profile_id: str,
    release_profile_sha256: str,
    source_file_sha256: dict[str, str],
    release_selection_sha256: str,
) -> dict[str, Any]:
    if set(approval) != {"statement", "signature"}:
        raise ValueError("production approval envelope fields are invalid")
    statement = approval.get("statement")
    signature = approval.get("signature")
    if not isinstance(statement, dict) or not isinstance(signature, dict):
        raise TypeError("production approval envelope is invalid")
    if (
        set(signature) != {"scheme", "key_id", "value_base64"}
        or signature.get("scheme") != APPROVAL_SCHEME
    ):
        raise ValueError("production approval signature metadata is invalid")
    key_id = str(signature.get("key_id") or "")
    authority = str(statement.get("approval_authority") or "")
    public_key = _load_trust_key(roots, key_id, authority)
    _validate_statement(
        statement,
        release_profile_id=release_profile_id,
        release_profile_sha256=release_profile_sha256,
        source_file_sha256=source_file_sha256,
        release_selection_sha256=release_selection_sha256,
    )
    statement_bytes = canonical_approval_statement(statement)
    try:
        signature_bytes = base64.b64decode(
            str(signature.get("value_base64") or ""), validate=True
        )
        public_key.verify(
            signature_bytes,
            statement_bytes,
            ec.ECDSA(hashes.SHA256()),
        )
    except (binascii.Error, InvalidSignature, ValueError) as error:
        raise ValueError("production approval signature is invalid") from error
    envelope_sha256 = _sha256(
        json.dumps(
            approval, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )
    return {
        "verified": True,
        "scheme": APPROVAL_SCHEME,
        "approval_key_id": key_id,
        "approval_envelope_sha256": envelope_sha256,
        "trust_roots_sha256": roots_digest,
        "approval_statement_sha256": _sha256(statement_bytes),
        "statement": statement,
        "signature": signature,
    }


def verify_production_approval_from_env(
    *,
    release_profile_id: str,
    release_profile_sha256: str,
    source_file_sha256: dict[str, str],
    release_selection_sha256: str,
) -> dict[str, Any]:
    """Verify a separately pinned KMS approval and bind it downstream."""
    approval, _approval_digest = _read_pinned_json(
        os.environ.get(APPROVAL_PATH_ENV, ""),
        os.environ.get(APPROVAL_DIGEST_ENV, ""),
        label="approval",
    )
    roots, roots_digest = _read_pinned_json(
        os.environ.get(TRUST_ROOTS_PATH_ENV, ""),
        os.environ.get(TRUST_ROOTS_DIGEST_ENV, ""),
        label="trust roots",
    )
    return _verify_approval_envelope(
        approval,
        roots=roots,
        roots_digest=roots_digest,
        release_profile_id=release_profile_id,
        release_profile_sha256=release_profile_sha256,
        source_file_sha256=source_file_sha256,
        release_selection_sha256=release_selection_sha256,
    )


def verify_embedded_production_approval_from_env(
    receipt: object,
    *,
    release_profile_id: str,
    release_profile_sha256: str,
    source_file_sha256: dict[str, str],
) -> dict[str, Any]:
    """Reverify a predecessor's embedded approval against current protected roots."""
    if not is_verified_production_approval_receipt(receipt):
        raise ValueError("embedded production approval receipt is invalid")
    assert isinstance(receipt, dict)
    roots, roots_digest = _read_pinned_json(
        os.environ.get(TRUST_ROOTS_PATH_ENV, ""),
        os.environ.get(TRUST_ROOTS_DIGEST_ENV, ""),
        label="trust roots",
    )
    if receipt.get("trust_roots_sha256") != roots_digest:
        raise ValueError("embedded approval trust roots changed")
    statement = receipt["statement"]
    assert isinstance(statement, dict)
    approval = {"statement": statement, "signature": receipt["signature"]}
    verified = _verify_approval_envelope(
        approval,
        roots=roots,
        roots_digest=roots_digest,
        release_profile_id=release_profile_id,
        release_profile_sha256=release_profile_sha256,
        source_file_sha256=source_file_sha256,
        release_selection_sha256=str(statement.get("release_selection_sha256") or ""),
    )
    if verified != receipt:
        raise ValueError("embedded production approval metadata mismatch")
    return verified
