"""Signed, content-addressed cache contracts for verified Git workflows."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    attach_attestation,
    verify_attestation,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES

REMOTE_IDENTITY_RECEIPT_SCHEMA = "longworld.git-remote-identity-receipt.v1"
REMOTE_IDENTITY_ATTESTATION_PURPOSE = "git_remote_identity_receipt"
REMOTE_IDENTITY_VALIDITY_SECONDS = 86_400
PACKING_PLAN_SCHEMA = "longworld.git-packing-plan.v1"
PACKING_PLAN_ATTESTATION_PURPOSE = "git_packing_plan"
REFERENCE_INDEX_SCHEMA = "longworld.git-reference-index.v1"
REFERENCE_INDEX_ATTESTATION_PURPOSE = "git_reference_index"
MAX_REMOTE_IDENTITY_RECEIPT_BYTES = 2_000_000
MAX_PACKING_PLAN_BYTES = 128_000_000
MAX_REFERENCE_INDEX_BYTES = 512_000_000

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_GIT_OBJECT_ID = re.compile(r"[0-9a-f]{40,64}\Z")
_GIT_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\Z")


class GitCacheError(ValueError):
    """A Git cache artifact is malformed, stale, or unauthenticated."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _strict_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise GitCacheError("Git cache JSON contains a duplicate key")
            value[key] = item
        return value

    def reject_nonfinite(value: str) -> None:
        raise GitCacheError(f"Git cache JSON contains non-finite value {value}")

    try:
        parsed = json.loads(
            _read_regular_file(path, max_bytes).decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except (
        OSError,
        ProvenanceError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        GitCacheError,
    ) as error:
        raise GitCacheError(f"Git cache artifact is unreadable: {path.name}") from error
    if not isinstance(parsed, dict):
        raise GitCacheError("Git cache artifact must be a JSON object")
    return parsed


def _canonical_timestamp(value: datetime, *, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise GitCacheError(f"{field} must be an aware UTC timestamp")
    return value.isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise GitCacheError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise GitCacheError(f"{field} is invalid") from error
    if (
        parsed.utcoffset() != timedelta(0)
        or _canonical_timestamp(parsed, field=field) != value
    ):
        raise GitCacheError(f"{field} must be a canonical UTC timestamp")
    return parsed


def _validate_closed_signed_object(
    value: object, *, unsigned_fields: set[str]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitCacheError("Git cache artifact must be an object")
    fields = set(value)
    expected = {*unsigned_fields, "attestation"}
    if fields == expected:
        return value
    if fields == expected | {LOCAL_PROBE_TRUST_ISOLATION_FIELD} and (
        value.get(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
        == LOCAL_PROBE_TRUST_ISOLATION_VALUE
    ):
        return value
    raise GitCacheError("Git cache artifact has unexpected fields")


def _verify_role_attestation(
    value: dict[str, Any],
    key: bytes | None,
    *,
    purpose: str,
    role: str,
) -> None:
    attestation = value.get("attestation")
    if (
        not isinstance(attestation, dict)
        or set(attestation)
        != {
            "scheme",
            "purpose",
            "role",
            "key_id",
            "environment",
            "digest",
        }
        or attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or attestation.get("purpose") != purpose
        or attestation.get("role") != role
        or not isinstance(attestation.get("key_id"), str)
        or not attestation["key_id"]
        or attestation.get("environment") not in {"probe", "production"}
        or not isinstance(attestation.get("digest"), str)
        or _SHA256.fullmatch(attestation["digest"]) is None
        or not verify_attestation(value, key, purpose=purpose)
    ):
        raise GitCacheError(f"Git cache artifact lacks a valid {role}-role signature")


def _validated_remote_request(value: object) -> dict[str, Any]:
    fields = {
        "repository",
        "head_revision",
        "license",
        "license_binding_policy_sha256",
        "source_client",
        "request_operations",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise GitCacheError("remote identity request is invalid")
    repository = value.get("repository")
    head_revision = value.get("head_revision")
    license_id = value.get("license")
    policy_digest = value.get("license_binding_policy_sha256")
    source_client = value.get("source_client")
    request_operations = value.get("request_operations")
    expected_operations = [
        {
            "method": "GET",
            "hostname": "github.com",
            "endpoint": f"repos/{repository}",
        },
        {
            "method": "GET",
            "hostname": "github.com",
            "endpoint": f"repos/{repository}/commits/{head_revision}",
        },
        {
            "method": "GET",
            "hostname": "github.com",
            "endpoint": f"repos/{repository}/license?ref={head_revision}",
        },
    ]
    if (
        not isinstance(repository, str)
        or _REPOSITORY.fullmatch(repository) is None
        or not isinstance(head_revision, str)
        or _GIT_OBJECT_ID.fullmatch(head_revision) is None
        or not isinstance(license_id, str)
        or not license_id
        or len(license_id) > 128
        or not isinstance(policy_digest, str)
        or _SHA256.fullmatch(policy_digest) is None
        or not isinstance(source_client, dict)
        or set(source_client) != {"path", "sha256"}
        or not isinstance(source_client.get("path"), str)
        or not Path(source_client["path"]).is_absolute()
        or not isinstance(source_client.get("sha256"), str)
        or _SHA256.fullmatch(source_client["sha256"]) is None
        or request_operations != expected_operations
    ):
        raise GitCacheError("remote identity request is invalid")
    return {
        "repository": repository,
        "head_revision": head_revision,
        "license": license_id,
        "license_binding_policy_sha256": policy_digest,
        "source_client": dict(source_client),
        "request_operations": [dict(operation) for operation in expected_operations],
    }


def _validated_remote_identity(
    value: object, *, request_identity: Mapping[str, Any]
) -> dict[str, Any]:
    fields = {
        "repository_response_sha256",
        "commit_response_sha256",
        "license_response_sha256",
        "head_revision",
        "repository_url",
        "license",
        "repository_license_spdx_id",
        "license_file_classifier_spdx_id",
        "license_file_revision",
        "license_file_path",
        "license_file_git_blob_sha",
        "license_file_size",
        "license_file_sha256",
        "license_file_html_url",
        "license_file_download_url",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise GitCacheError("remote identity is invalid")
    repository = str(request_identity["repository"])
    revision = str(request_identity["head_revision"])
    license_id = str(request_identity["license"])
    repository_url = f"https://github.com/{repository}"
    license_path = value.get("license_file_path")
    size = value.get("license_file_size")
    if (
        any(
            not isinstance(value.get(field), str)
            or _SHA256.fullmatch(value[field]) is None
            for field in (
                "repository_response_sha256",
                "commit_response_sha256",
                "license_response_sha256",
                "license_file_sha256",
            )
        )
        or value.get("head_revision") != revision
        or value.get("license_file_revision") != revision
        or value.get("repository_url") != repository_url
        or value.get("license") != license_id
        or value.get("repository_license_spdx_id") != license_id
        or value.get("license_file_classifier_spdx_id")
        not in {license_id, "NOASSERTION"}
        or not isinstance(license_path, str)
        or _GIT_PATH.fullmatch(license_path) is None
        or any(component in {".", ".."} for component in license_path.split("/"))
        or not isinstance(value.get("license_file_git_blob_sha"), str)
        or _GIT_OBJECT_ID.fullmatch(value["license_file_git_blob_sha"]) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not 0 < size <= 16_000_000
        or value.get("license_file_html_url")
        != f"{repository_url}/blob/{revision}/{license_path}"
        or value.get("license_file_download_url")
        != (f"https://raw.githubusercontent.com/{repository}/{revision}/{license_path}")
    ):
        raise GitCacheError("remote identity does not match its request")
    return dict(value)


def sign_remote_identity_receipt(
    *,
    request_identity: Mapping[str, Any],
    remote_identity: Mapping[str, Any],
    checked_at: datetime,
    key: bytes,
) -> dict[str, Any]:
    """Create a source-role receipt with a fixed one-day validity interval."""
    request = _validated_remote_request(dict(request_identity))
    identity = _validated_remote_identity(
        dict(remote_identity), request_identity=request
    )
    checked = _canonical_timestamp(checked_at, field="checked_at")
    expires = _canonical_timestamp(
        checked_at + timedelta(seconds=REMOTE_IDENTITY_VALIDITY_SECONDS),
        field="expires_at",
    )
    signed = attach_attestation(
        {
            "schema_version": REMOTE_IDENTITY_RECEIPT_SCHEMA,
            "checked_at": checked,
            "expires_at": expires,
            "request_identity": request,
            "remote_identity": identity,
        },
        key,
        purpose=REMOTE_IDENTITY_ATTESTATION_PURPOSE,
    )
    _verify_role_attestation(
        signed,
        key,
        purpose=REMOTE_IDENTITY_ATTESTATION_PURPOSE,
        role="source",
    )
    return signed


def verify_remote_identity_receipt(
    receipt: object,
    *,
    expected_request_identity: Mapping[str, Any],
    exported_at: str,
    key: bytes | None,
) -> dict[str, Any]:
    """Verify receipt identity, source-role signature, and export-time freshness."""
    value = _validate_closed_signed_object(
        receipt,
        unsigned_fields={
            "schema_version",
            "checked_at",
            "expires_at",
            "request_identity",
            "remote_identity",
        },
    )
    if value.get("schema_version") != REMOTE_IDENTITY_RECEIPT_SCHEMA:
        raise GitCacheError("remote identity receipt schema is unsupported")
    _verify_role_attestation(
        value,
        key,
        purpose=REMOTE_IDENTITY_ATTESTATION_PURPOSE,
        role="source",
    )
    expected = _validated_remote_request(dict(expected_request_identity))
    request = _validated_remote_request(value.get("request_identity"))
    if request != expected:
        raise GitCacheError("remote identity receipt request does not match")
    checked_at = _parse_utc_timestamp(value.get("checked_at"), field="checked_at")
    expires_at = _parse_utc_timestamp(value.get("expires_at"), field="expires_at")
    if expires_at - checked_at != timedelta(seconds=REMOTE_IDENTITY_VALIDITY_SECONDS):
        raise GitCacheError("remote identity receipt validity interval is invalid")
    export_time = _parse_utc_timestamp(exported_at, field="exported_at")
    if not checked_at <= export_time <= expires_at:
        raise GitCacheError("remote identity receipt is stale for this export")
    identity = _validated_remote_identity(
        value.get("remote_identity"), request_identity=request
    )
    return {
        "receipt_sha256": hashlib.sha256(_canonical_bytes(value)).hexdigest(),
        "checked_at": value["checked_at"],
        "expires_at": value["expires_at"],
        "request_identity": request,
        "remote_identity": identity,
    }


def read_remote_identity_receipt(
    path: Path,
    *,
    expected_request_identity: Mapping[str, Any],
    exported_at: str,
    key: bytes | None,
) -> dict[str, Any]:
    """Securely read and verify a source-role remote identity receipt."""
    return verify_remote_identity_receipt(
        _strict_json_object(path, max_bytes=MAX_REMOTE_IDENTITY_RECEIPT_BYTES),
        expected_request_identity=expected_request_identity,
        exported_at=exported_at,
        key=key,
    )


def _validated_packing_identity(value: object) -> dict[str, Any]:
    fields = {
        "repository",
        "root_revision",
        "source_record_count",
        "source_records_sha256",
        "public_policy_sha256",
        "history_client_sha256",
        "remote_identity_receipt_sha256",
        "requests_sha256",
        "tokenizer_asset_manifest_sha256",
        "packer_revision",
        "source_event_selector_revision",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise GitCacheError("packing plan identity is invalid")
    record_count = value.get("source_record_count")
    if (
        not isinstance(value.get("repository"), str)
        or _REPOSITORY.fullmatch(value["repository"]) is None
        or not isinstance(value.get("root_revision"), str)
        or _GIT_OBJECT_ID.fullmatch(value["root_revision"]) is None
        or isinstance(record_count, bool)
        or not isinstance(record_count, int)
        or not 0 < record_count <= 10_000_000
        or any(
            not isinstance(value.get(field), str)
            or _SHA256.fullmatch(value[field]) is None
            for field in (
                "source_records_sha256",
                "public_policy_sha256",
                "history_client_sha256",
                "remote_identity_receipt_sha256",
                "requests_sha256",
                "tokenizer_asset_manifest_sha256",
            )
        )
        or any(
            not isinstance(value.get(field), str)
            or _REVISION.fullmatch(value[field]) is None
            for field in ("packer_revision", "source_event_selector_revision")
        )
    ):
        raise GitCacheError("packing plan identity is invalid")
    return dict(value)


def packing_plan_cache_key(identity: Mapping[str, Any]) -> str:
    """Return the content address for an exact packing-plan identity."""
    normalized = _validated_packing_identity(dict(identity))
    return hashlib.sha256(_canonical_bytes(normalized)).hexdigest()


def _validated_packing_windows(
    value: object, *, source_record_count: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > source_record_count:
        raise GitCacheError("packing plan windows are invalid")
    fields = {
        "band",
        "source_start_index",
        "source_end_index",
        "context_tokens",
        "context_sha256",
        "source_event_ids",
    }
    windows: list[dict[str, Any]] = []
    previous_end = 0
    for item in value:
        if not isinstance(item, dict) or set(item) != fields:
            raise GitCacheError("packing plan window is invalid")
        band = item.get("band")
        start = item.get("source_start_index")
        end = item.get("source_end_index")
        tokens = item.get("context_tokens")
        context_sha256 = item.get("context_sha256")
        event_ids = item.get("source_event_ids")
        bounds = EXACT_TOKEN_BAND_RANGES.get(str(band))
        if (
            bounds is None
            or isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < previous_end
            or not start < end <= source_record_count
            or isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or not bounds[0] <= tokens <= bounds[1]
            or not isinstance(context_sha256, str)
            or _SHA256.fullmatch(context_sha256) is None
            or not isinstance(event_ids, list)
            or not event_ids
            or len(event_ids) != len(set(event_ids))
            or any(
                not isinstance(event_id, str)
                or not event_id
                or len(event_id.encode()) > 512
                for event_id in event_ids
            )
        ):
            raise GitCacheError("packing plan window is invalid")
        windows.append(
            {
                "band": band,
                "source_start_index": start,
                "source_end_index": end,
                "context_tokens": tokens,
                "context_sha256": context_sha256,
                "source_event_ids": list(event_ids),
            }
        )
        previous_end = end
    return windows


def _validated_reject_reasons(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > 10_000:
        raise GitCacheError("packing plan reject counters are invalid")
    if any(
        not isinstance(reason, str)
        or not reason
        or len(reason) > 256
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        for reason, count in value.items()
    ):
        raise GitCacheError("packing plan reject counters are invalid")
    return dict(value)


def sign_packing_plan(
    *,
    identity: Mapping[str, Any],
    windows: list[dict[str, Any]],
    next_record_index: int,
    reject_reasons: Mapping[str, int],
    key: bytes,
) -> dict[str, Any]:
    """Sign a deterministic window plan, never a final quality decision."""
    normalized_identity = _validated_packing_identity(dict(identity))
    record_count = int(normalized_identity["source_record_count"])
    normalized_windows = _validated_packing_windows(
        windows, source_record_count=record_count
    )
    minimum_cursor = (
        int(normalized_windows[-1]["source_end_index"]) if normalized_windows else 0
    )
    if (
        isinstance(next_record_index, bool)
        or not isinstance(next_record_index, int)
        or not minimum_cursor <= next_record_index <= record_count
    ):
        raise GitCacheError("packing plan cursor is invalid")
    signed = attach_attestation(
        {
            "schema_version": PACKING_PLAN_SCHEMA,
            "identity": normalized_identity,
            "identity_sha256": packing_plan_cache_key(normalized_identity),
            "windows": normalized_windows,
            "next_record_index": next_record_index,
            "reject_reasons": _validated_reject_reasons(dict(reject_reasons)),
        },
        key,
        purpose=PACKING_PLAN_ATTESTATION_PURPOSE,
    )
    _verify_role_attestation(
        signed,
        key,
        purpose=PACKING_PLAN_ATTESTATION_PURPOSE,
        role="promotion",
    )
    return signed


def verify_packing_plan(
    plan: object,
    *,
    expected_identity: Mapping[str, Any],
    key: bytes | None,
) -> dict[str, Any]:
    """Verify a cached packing plan and return only its non-final plan payload."""
    value = _validate_closed_signed_object(
        plan,
        unsigned_fields={
            "schema_version",
            "identity",
            "identity_sha256",
            "windows",
            "next_record_index",
            "reject_reasons",
        },
    )
    if value.get("schema_version") != PACKING_PLAN_SCHEMA:
        raise GitCacheError("packing plan schema is unsupported")
    _verify_role_attestation(
        value,
        key,
        purpose=PACKING_PLAN_ATTESTATION_PURPOSE,
        role="promotion",
    )
    expected = _validated_packing_identity(dict(expected_identity))
    identity = _validated_packing_identity(value.get("identity"))
    identity_sha256 = packing_plan_cache_key(identity)
    if identity != expected or value.get("identity_sha256") != identity_sha256:
        raise GitCacheError("packing plan identity does not match")
    record_count = int(identity["source_record_count"])
    windows = _validated_packing_windows(
        value.get("windows"), source_record_count=record_count
    )
    next_record_index = value.get("next_record_index")
    minimum_cursor = int(windows[-1]["source_end_index"]) if windows else 0
    if (
        isinstance(next_record_index, bool)
        or not isinstance(next_record_index, int)
        or not minimum_cursor <= next_record_index <= record_count
    ):
        raise GitCacheError("packing plan cursor is invalid")
    return {
        "windows": windows,
        "next_record_index": next_record_index,
        "reject_reasons": _validated_reject_reasons(value.get("reject_reasons")),
    }


def read_packing_plan(
    path: Path,
    *,
    expected_identity: Mapping[str, Any],
    key: bytes | None,
) -> dict[str, Any]:
    """Securely read and verify a promotion-role packing plan."""
    return verify_packing_plan(
        _strict_json_object(path, max_bytes=MAX_PACKING_PLAN_BYTES),
        expected_identity=expected_identity,
        key=key,
    )


def _sorted_unique_digests(
    value: object, *, field: str, allow_empty: bool
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or len(value) > 5_000_000
        or any(
            not isinstance(item, str) or _SHA256.fullmatch(item) is None
            for item in value
        )
    ):
        raise GitCacheError(f"reference index {field} is invalid")
    normalized = sorted(set(value))
    if len(normalized) != len(value):
        raise GitCacheError(f"reference index {field} is duplicated")
    return normalized


def _sorted_unique_events(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 5_000_000
        or any(
            not isinstance(item, str) or _GIT_OBJECT_ID.fullmatch(item) is None
            for item in value
        )
    ):
        raise GitCacheError("reference index source events are invalid")
    normalized = sorted(set(value))
    if len(normalized) != len(value):
        raise GitCacheError("reference index source events are duplicated")
    return normalized


def _reference_digest(value: list[str]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _reference_payload(
    *,
    release_manifest_sha256: object,
    closure_release_manifest_sha256: object,
    cpt_rows_sha256: object,
    cpt_row_count: object,
    source_body_sha256: object,
    source_event_ids: object,
    context_sha256: object,
) -> dict[str, Any]:
    if (
        not isinstance(release_manifest_sha256, str)
        or _SHA256.fullmatch(release_manifest_sha256) is None
    ):
        raise GitCacheError("reference index release manifest is invalid")
    closure = _sorted_unique_digests(
        closure_release_manifest_sha256,
        field="release closure",
        allow_empty=True,
    )
    if release_manifest_sha256 in closure:
        raise GitCacheError("reference index release appears in its own closure")
    if (
        not isinstance(cpt_rows_sha256, str)
        or _SHA256.fullmatch(cpt_rows_sha256) is None
        or isinstance(cpt_row_count, bool)
        or not isinstance(cpt_row_count, int)
        or not 0 < cpt_row_count <= 10_000_000
    ):
        raise GitCacheError("reference index CPT row set is invalid")
    bodies = _sorted_unique_digests(
        source_body_sha256, field="source bodies", allow_empty=False
    )
    events = _sorted_unique_events(source_event_ids)
    contexts = _sorted_unique_digests(
        context_sha256, field="contexts", allow_empty=False
    )
    if len(contexts) != cpt_row_count:
        raise GitCacheError("reference index CPT row count does not match contexts")
    digests = {
        "closure_sha256": _reference_digest(closure),
        "source_bodies_sha256": _reference_digest(bodies),
        "source_events_sha256": _reference_digest(events),
        "contexts_sha256": _reference_digest(contexts),
    }
    counts = {
        "closure_releases": len(closure),
        "cpt_rows": cpt_row_count,
        "source_bodies": len(bodies),
        "source_events": len(events),
        "contexts": len(contexts),
    }
    audited_identity = {
        "release_manifest_sha256": release_manifest_sha256,
        "closure_release_manifest_sha256": closure,
        "cpt_rows_sha256": cpt_rows_sha256,
        "cpt_row_count": cpt_row_count,
        "source_bodies_sha256": digests["source_bodies_sha256"],
        "source_events_sha256": digests["source_events_sha256"],
        "contexts_sha256": digests["contexts_sha256"],
        "source_body_count": counts["source_bodies"],
        "source_event_count": counts["source_events"],
        "context_count": counts["contexts"],
    }
    return {
        "schema_version": REFERENCE_INDEX_SCHEMA,
        "audited_identity": audited_identity,
        "source_body_sha256": bodies,
        "source_event_ids": events,
        "context_sha256": contexts,
        "digests": digests,
        "counts": counts,
    }


def _validated_reference_audited_identity(value: object) -> dict[str, Any]:
    fields = {
        "release_manifest_sha256",
        "closure_release_manifest_sha256",
        "cpt_rows_sha256",
        "cpt_row_count",
        "source_bodies_sha256",
        "source_events_sha256",
        "contexts_sha256",
        "source_body_count",
        "source_event_count",
        "context_count",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise GitCacheError("reference audited identity is invalid")
    closure = _sorted_unique_digests(
        value.get("closure_release_manifest_sha256"),
        field="audited release closure",
        allow_empty=True,
    )
    cpt_row_count = value.get("cpt_row_count")
    counts = [
        cpt_row_count,
        value.get("source_body_count"),
        value.get("source_event_count"),
        value.get("context_count"),
    ]
    if (
        any(
            not isinstance(value.get(field), str)
            or _SHA256.fullmatch(value[field]) is None
            for field in (
                "release_manifest_sha256",
                "cpt_rows_sha256",
                "source_bodies_sha256",
                "source_events_sha256",
                "contexts_sha256",
            )
        )
        or value.get("release_manifest_sha256") in closure
        or any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 < count <= 10_000_000
            for count in counts
        )
        or value.get("context_count") != cpt_row_count
    ):
        raise GitCacheError("reference audited identity is invalid")
    normalized = dict(value)
    normalized["closure_release_manifest_sha256"] = closure
    return normalized


def reference_audited_identity(
    *,
    release_manifest_sha256: str,
    closure_release_manifest_sha256: list[str],
    cpt_rows_sha256: str,
    cpt_row_count: int,
    source_body_sha256: list[str],
    source_event_ids: list[str],
    context_sha256: list[str],
) -> dict[str, Any]:
    """Build the exact identity that an independent release audit must supply."""
    payload = _reference_payload(
        release_manifest_sha256=release_manifest_sha256,
        closure_release_manifest_sha256=closure_release_manifest_sha256,
        cpt_rows_sha256=cpt_rows_sha256,
        cpt_row_count=cpt_row_count,
        source_body_sha256=source_body_sha256,
        source_event_ids=source_event_ids,
        context_sha256=context_sha256,
    )
    return dict(payload["audited_identity"])


def _validated_reference_payload(value: object) -> dict[str, Any]:
    fields = {
        "schema_version",
        "audited_identity",
        "source_body_sha256",
        "source_event_ids",
        "context_sha256",
        "digests",
        "counts",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise GitCacheError("reference index payload is invalid")
    if value.get("schema_version") != REFERENCE_INDEX_SCHEMA:
        raise GitCacheError("reference index schema is unsupported")
    audited_identity = _validated_reference_audited_identity(
        value.get("audited_identity")
    )
    expected = _reference_payload(
        release_manifest_sha256=audited_identity["release_manifest_sha256"],
        closure_release_manifest_sha256=audited_identity[
            "closure_release_manifest_sha256"
        ],
        cpt_rows_sha256=audited_identity["cpt_rows_sha256"],
        cpt_row_count=audited_identity["cpt_row_count"],
        source_body_sha256=value.get("source_body_sha256"),
        source_event_ids=value.get("source_event_ids"),
        context_sha256=value.get("context_sha256"),
    )
    if value != expected:
        raise GitCacheError("reference index digests or counts do not replay")
    return expected


def reference_index_cache_key(index: Mapping[str, Any]) -> str:
    """Return the content address of a validated reference-index payload."""
    payload_fields = {
        "schema_version",
        "audited_identity",
        "source_body_sha256",
        "source_event_ids",
        "context_sha256",
        "digests",
        "counts",
    }
    payload = _validated_reference_payload(
        {field: index.get(field) for field in payload_fields}
    )
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def sign_reference_index(
    *,
    release_manifest_sha256: str,
    closure_release_manifest_sha256: list[str],
    cpt_rows_sha256: str,
    cpt_row_count: int,
    source_body_sha256: list[str],
    source_event_ids: list[str],
    context_sha256: list[str],
    key: bytes,
) -> dict[str, Any]:
    """Sign an audited release's immutable cross-release identity index."""
    payload = _reference_payload(
        release_manifest_sha256=release_manifest_sha256,
        closure_release_manifest_sha256=closure_release_manifest_sha256,
        cpt_rows_sha256=cpt_rows_sha256,
        cpt_row_count=cpt_row_count,
        source_body_sha256=source_body_sha256,
        source_event_ids=source_event_ids,
        context_sha256=context_sha256,
    )
    signed = attach_attestation(
        {**payload, "index_sha256": reference_index_cache_key(payload)},
        key,
        purpose=REFERENCE_INDEX_ATTESTATION_PURPOSE,
    )
    _verify_role_attestation(
        signed,
        key,
        purpose=REFERENCE_INDEX_ATTESTATION_PURPOSE,
        role="report",
    )
    return signed


def verify_reference_index(
    index: object,
    *,
    expected_audited_identity: Mapping[str, Any],
    key: bytes | None,
) -> dict[str, Any]:
    """Verify a report-role index and return its exact overlap identity sets."""
    value = _validate_closed_signed_object(
        index,
        unsigned_fields={
            "schema_version",
            "audited_identity",
            "source_body_sha256",
            "source_event_ids",
            "context_sha256",
            "digests",
            "counts",
            "index_sha256",
        },
    )
    _verify_role_attestation(
        value,
        key,
        purpose=REFERENCE_INDEX_ATTESTATION_PURPOSE,
        role="report",
    )
    payload_fields = {
        "schema_version",
        "audited_identity",
        "source_body_sha256",
        "source_event_ids",
        "context_sha256",
        "digests",
        "counts",
    }
    payload = _validated_reference_payload(
        {field: value.get(field) for field in payload_fields}
    )
    expected = _validated_reference_audited_identity(dict(expected_audited_identity))
    if payload["audited_identity"] != expected or value.get(
        "index_sha256"
    ) != reference_index_cache_key(payload):
        raise GitCacheError("reference index identity does not match")
    return {
        "audited_identity": payload["audited_identity"],
        "source_body_sha256": payload["source_body_sha256"],
        "source_event_ids": payload["source_event_ids"],
        "context_sha256": payload["context_sha256"],
        "digests": payload["digests"],
        "counts": payload["counts"],
    }


def read_reference_index(
    path: Path,
    *,
    expected_audited_identity: Mapping[str, Any],
    key: bytes | None,
) -> dict[str, Any]:
    """Securely read and verify a report-role reference index."""
    return verify_reference_index(
        _strict_json_object(path, max_bytes=MAX_REFERENCE_INDEX_BYTES),
        expected_audited_identity=expected_audited_identity,
        key=key,
    )
