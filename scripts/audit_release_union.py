#!/usr/bin/env python3
"""Audit and attest identity and byte bindings across promoted releases.

This creates a local-engineering inventory, not a production package or an
independent production approval.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ATTESTATION_V2_SCHEME,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attestation_key_from_env,
    canonical_attested_payload,
    verify_attestation,
)
from longworld.core.promotion import (
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_REVISION,
    RELEASE_GATE_SCHEMA,
    promoted_row_set_sha256,
)
from longworld.core.record_contract import (
    exact_token_band_reject_reason,
    exact_token_metadata_valid,
    replay_bundle_binding_valid,
)
from longworld.core.release_profile import (
    ReleaseProfile,
    release_profile,
    release_profile_sha256,
)

LOCAL_RELEASE_INVENTORY_SCHEMA = "longworld-local-release-inventory-v2"
LOCAL_RELEASE_INVENTORY_PURPOSE = "local_release_inventory"
RECEIPT_NAMES = ("release_gate_pass.json", "release_gate_receipt.json")
SOURCE_FILES = ("quality_report.json", "train.jsonl", "eval.jsonl")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_OUTPUT_FILE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_BINDING_DIGEST_FIELDS = ("sha256", "binding_digest")


class ReleaseUnionError(ValueError):
    """Raised when one release or the combined release set is invalid."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseUnionError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _json_object(raw: bytes, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseUnionError(f"{path}: invalid JSON") from error
    if not isinstance(value, dict):
        raise ReleaseUnionError(f"{path}: expected a JSON object")
    return value


def _jsonl(raw: bytes, path: Path) -> list[dict[str, Any]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseUnionError(f"{path}: release rows are not UTF-8") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, object_pairs_hook=_unique_json_object)
        except json.JSONDecodeError as error:
            raise ReleaseUnionError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(value, dict):
            raise ReleaseUnionError(f"{path}:{line_number}: expected a JSON object")
        rows.append(value)
    return rows


def _integer_field(payload: dict[str, Any], field: str, path: Path) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReleaseUnionError(f"{path}: invalid {field}")
    return value


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validated_root(root: Path) -> Path:
    lexical = _lexical_absolute(root)
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ReleaseUnionError("inventory root is missing") from error
    if lexical != resolved:
        raise ReleaseUnionError("inventory root contains a symlink path")
    if not getattr(os, "O_NOFOLLOW", 0):
        raise ReleaseUnionError(
            "this platform cannot enforce symlink-safe inventory reads"
        )
    try:
        descriptor = os.open(resolved, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ReleaseUnionError("inventory root is not a safe directory") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ReleaseUnionError("inventory root is not a directory")
    finally:
        os.close(descriptor)
    return resolved


def _relative(root: Path, path: Path, *, field: str) -> Path:
    lexical = _lexical_absolute(path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReleaseUnionError(f"{field} is missing") from error
    if lexical != resolved:
        raise ReleaseUnionError(f"{field} contains a symlink path")
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ReleaseUnionError(f"{field} is outside inventory root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ReleaseUnionError(f"{field} is not inventory-root relative")
    return relative


def _open_directory_chain(root: Path, parts: tuple[str, ...], *, field: str) -> int:
    try:
        current = os.open(root, _DIRECTORY_FLAGS)
        for part in parts:
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            finally:
                os.close(current)
            current = child
    except OSError as error:
        raise ReleaseUnionError(f"{field} contains an unsafe directory path") from error
    try:
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise ReleaseUnionError(f"{field} contains a non-directory path")
    except BaseException:
        os.close(current)
        raise
    return current


def _validate_directory(root: Path, path: Path, *, field: str) -> str:
    relative = _relative(root, path, field=field)
    descriptor = _open_directory_chain(root, relative.parts, field=field)
    os.close(descriptor)
    return relative.as_posix()


def _read_bound_file(
    root: Path, path: Path, *, role: str
) -> tuple[bytes, dict[str, Any]]:
    relative = _relative(root, path, field=role)
    parent = _open_directory_chain(root, relative.parts[:-1], field=role)
    try:
        try:
            descriptor = os.open(relative.name, _FILE_FLAGS, dir_fd=parent)
        except OSError as error:
            raise ReleaseUnionError(f"{path}: {role} is missing or unsafe") from error
    finally:
        os.close(parent)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseUnionError(f"{path}: {role} is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ReleaseUnionError(f"{path}: cannot read bound release file") from error
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity or len(raw) != after.st_size:
        raise ReleaseUnionError(f"{path}: bound release file changed while reading")
    return raw, {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "role": role,
    }


def _receipt_path(release: Path) -> Path:
    matches: list[Path] = []
    for name in RECEIPT_NAMES:
        candidate = release / name
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ReleaseUnionError(
                f"{release}: cannot inspect release gate receipt"
            ) from error
        matches.append(candidate)
    if len(matches) != 1:
        raise ReleaseUnionError(f"{release}: expected exactly one release gate receipt")
    return matches[0]


def _profile_sha256(profile_id: object, *, path: Path) -> tuple[str, str]:
    if not isinstance(profile_id, str) or not profile_id:
        raise ReleaseUnionError(f"{path}: release profile identity is missing")
    try:
        return profile_id, release_profile_sha256(profile_id)
    except ValueError as error:
        raise ReleaseUnionError(
            f"{path}: release profile identity is unknown"
        ) from error


def _row_metrics(
    row: dict[str, Any],
    *,
    release: Path,
    row_number: int,
    source_profile: ReleaseProfile,
) -> tuple[int, str, str]:
    tokens = row.get("actual_context_tokens")
    difficulty = row.get("difficulty")
    difficulty_tokens = (
        difficulty.get("context_tokens") if isinstance(difficulty, dict) else None
    )
    if (
        not isinstance(tokens, int)
        or isinstance(tokens, bool)
        or tokens <= 0
        or difficulty_tokens != tokens
    ):
        raise ReleaseUnionError(
            f"{release}: row {row_number} has invalid exact context tokens"
        )
    bucket = row.get("length_bucket")
    domain = row.get("domain")
    if not isinstance(bucket, str) or not bucket:
        raise ReleaseUnionError(
            f"{release}: row {row_number} has missing length_bucket"
        )
    if not isinstance(domain, str) or not domain:
        raise ReleaseUnionError(f"{release}: row {row_number} has missing domain")
    if bucket not in source_profile.training_length_buckets:
        raise ReleaseUnionError(
            f"{release}: row {row_number} length bucket is not admitted by the "
            "source release profile"
        )
    band_error = exact_token_band_reject_reason(bucket, tokens)
    if band_error is not None:
        raise ReleaseUnionError(f"{release}: row {row_number} has {band_error}")
    promotion = row.get("promotion")
    if (
        row.get("tokenizer_context_tokens") != tokens
        or not exact_token_metadata_valid(row, require_asset_manifest=True)
        or row.get("tokenizer_model_id") != source_profile.tokenizer_model_id
        or row.get("tokenizer_revision") != source_profile.tokenizer_revision
        or row.get("tokenizer_asset_manifest_sha256")
        != source_profile.tokenizer_asset_manifest_sha256
        or not isinstance(promotion, dict)
        or promotion.get("tokenizer_asset_manifest_sha256")
        != row.get("tokenizer_asset_manifest_sha256")
    ):
        raise ReleaseUnionError(
            f"{release}: row {row_number} has exact tokenizer metadata that does "
            "not match the source release profile"
        )
    return tokens, bucket, domain


def _canonical_content_hash(
    row: dict[str, Any], *, release: Path, row_number: int
) -> str:
    context = row.get("context")
    if not isinstance(context, str) or "answer" not in row:
        raise ReleaseUnionError(
            f"{release}: row {row_number} cannot derive canonical content_hash"
        )
    payload = json.dumps(
        {"context": context, "answer": str(row["answer"])}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _string_identities(
    row: dict[str, Any], field: str, *, release: Path, row_number: int
) -> list[tuple[str, str]]:
    value = row.get(field)
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ReleaseUnionError(
            f"{release}: row {row_number} has invalid {field} source identity"
        )
    return [(field, item) for item in sorted(set(value))]


def _binding_identities(
    row: dict[str, Any], field: str, *, release: Path, row_number: int
) -> list[tuple[str, str]]:
    direct = row.get(field)
    promotion = row.get("promotion")
    promoted = promotion.get(field) if isinstance(promotion, dict) else None
    bindings = [value for value in (direct, promoted) if value is not None]
    if not bindings:
        return []
    if any(not isinstance(value, dict) for value in bindings):
        raise ReleaseUnionError(
            f"{release}: row {row_number} has invalid {field} source binding"
        )
    normalized: list[dict[str, str]] = []
    for binding in bindings:
        assert isinstance(binding, dict)
        digests = {
            digest_field: str(binding.get(digest_field) or "").strip()
            for digest_field in _BINDING_DIGEST_FIELDS
            if str(binding.get(digest_field) or "").strip()
        }
        if not digests:
            raise ReleaseUnionError(
                f"{release}: row {row_number} has incomplete {field} source binding"
            )
        normalized.append(digests)
    if len(normalized) == 2 and normalized[0] != normalized[1]:
        raise ReleaseUnionError(
            f"{release}: row {row_number} has inconsistent {field} source binding"
        )
    return [
        (f"{field}.{digest_field}", digest)
        for digest_field, digest in sorted(normalized[0].items())
    ]


def _row_source_identities(
    row: dict[str, Any], *, release: Path, row_number: int
) -> list[tuple[str, str]]:
    identities: list[tuple[str, str]] = []
    for field in ("workflow_ids", "real_source_workflow_ids"):
        identities.extend(
            _string_identities(row, field, release=release, row_number=row_number)
        )
    for field in ("episode_replay_bundle", "source_workflow_bundle"):
        identities.extend(
            _binding_identities(row, field, release=release, row_number=row_number)
        )
    return identities


def _real_source_identity_valid(row: dict[str, Any]) -> bool:
    promotion = row.get("promotion")
    real_workflows = row.get("real_source_workflow_ids")
    return bool(
        row.get("real_source_verified") is True
        and isinstance(promotion, dict)
        and promotion.get("real_source_verified") is True
        and isinstance(real_workflows, list)
        and real_workflows
        and all(isinstance(item, str) and item.strip() for item in real_workflows)
        and replay_bundle_binding_valid(row)
    )


def _local_inventory_message(
    value: dict[str, Any], *, key_id: str, environment: str
) -> bytes:
    return (
        LOCAL_RELEASE_INVENTORY_PURPOSE.encode()
        + b"\0report\0"
        + key_id.encode()
        + b"\0"
        + environment.encode()
        + b"\0"
        + canonical_attested_payload(value)
    )


def _local_inventory_identity(key: bytes | None) -> tuple[str, str] | None:
    if key is None or attestation_key_from_env("release_inventory") != key:
        return None
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    configured = os.environ.get(ROLE_KEY_ENVS["report"], "").encode()
    key_id = os.environ.get(ROLE_KEY_ID_ENVS["report"], "").strip()
    if environment != "probe" or configured != key or not key_id:
        return None
    return key_id, environment


def _attach_local_release_inventory_attestation(
    payload: dict[str, Any], key: bytes | None
) -> dict[str, Any]:
    identity = _local_inventory_identity(key)
    if identity is None:
        raise ReleaseUnionError(
            "local release inventory report attestation identity is incomplete"
        )
    assert key is not None
    key_id, environment = identity
    inventory = dict(payload)
    inventory["attestation"] = {
        "scheme": ATTESTATION_V2_SCHEME,
        "purpose": LOCAL_RELEASE_INVENTORY_PURPOSE,
        "role": "report",
        "key_id": key_id,
        "environment": environment,
        "digest": hmac.new(
            key,
            _local_inventory_message(inventory, key_id=key_id, environment=environment),
            hashlib.sha256,
        ).hexdigest(),
    }
    return inventory


def verify_local_release_inventory_attestation(
    inventory: dict[str, Any], key: bytes | None
) -> bool:
    """Verify the probe-only signature without accepting production inventory."""
    identity = _local_inventory_identity(key)
    if identity is None:
        return False
    assert key is not None
    key_id, environment = identity
    attestation = inventory.get("attestation")
    if not isinstance(attestation, dict) or attestation != {
        "scheme": ATTESTATION_V2_SCHEME,
        "purpose": LOCAL_RELEASE_INVENTORY_PURPOSE,
        "role": "report",
        "key_id": key_id,
        "environment": environment,
        "digest": attestation.get("digest"),
    }:
        return False
    expected = hmac.new(
        key,
        _local_inventory_message(inventory, key_id=key_id, environment=environment),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(str(attestation.get("digest") or ""), expected)


def _local_inventory_key_from_env() -> bytes | None:
    return attestation_key_from_env("release_inventory")


def _audit_release(
    release: Path,
    *,
    root: Path,
    gate_attestation_key: bytes | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    release_relative = _validate_directory(
        root, release, field="promoted release directory"
    )
    receipt_path = _receipt_path(release)
    receipt_raw, receipt_entry = _read_bound_file(
        root, receipt_path, role="release_gate_pass"
    )
    receipt = _json_object(receipt_raw, receipt_path)
    if not verify_attestation(
        receipt, gate_attestation_key, purpose=RELEASE_GATE_PURPOSE
    ):
        raise ReleaseUnionError(f"{receipt_path}: release gate attestation is invalid")
    profile_id, profile_digest = _profile_sha256(
        receipt.get("release_profile_id"), path=receipt_path
    )
    source_profile = release_profile(profile_id)
    if (
        receipt.get("schema_version") != RELEASE_GATE_SCHEMA
        or receipt.get("gate_revision") != RELEASE_GATE_REVISION
        or receipt.get("release_profile_sha256") != profile_digest
        or receipt.get("ok") is not True
        or receipt.get("errors") != []
        or receipt.get("tokenizer_model_id") != source_profile.tokenizer_model_id
        or receipt.get("tokenizer_revision") != source_profile.tokenizer_revision
        or receipt.get("tokenizer_asset_manifest_sha256")
        != source_profile.tokenizer_asset_manifest_sha256
    ):
        raise ReleaseUnionError(f"{receipt_path}: release profile or gate is invalid")

    source_hashes = receipt.get("source_file_sha256")
    if not isinstance(source_hashes, dict) or set(source_hashes) != set(SOURCE_FILES):
        raise ReleaseUnionError(f"{receipt_path}: source_file_sha256 is incomplete")
    source_entries: list[dict[str, Any]] = []
    source_raw: dict[str, bytes] = {}
    for filename in SOURCE_FILES:
        source_path = release / filename
        raw, entry = _read_bound_file(root, source_path, role="promoted_data")
        expected = source_hashes.get(filename)
        if not isinstance(expected, str) or entry["sha256"] != expected:
            raise ReleaseUnionError(f"{source_path}: source_file_sha256 mismatch")
        source_entries.append(entry)
        source_raw[filename] = raw
    if receipt.get("quality_report_sha256") != source_hashes["quality_report.json"]:
        raise ReleaseUnionError(f"{receipt_path}: quality report binding is invalid")

    rows = [
        *_jsonl(source_raw["train.jsonl"], release / "train.jsonl"),
        *_jsonl(source_raw["eval.jsonl"], release / "eval.jsonl"),
    ]
    if not rows:
        raise ReleaseUnionError(f"{release}: promoted release contains no rows")
    worlds: set[str] = set()
    tokens = 0
    for row_number, row in enumerate(rows, start=1):
        for field in ("world_id", "content_hash"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ReleaseUnionError(
                    f"{release}: row {row_number} has missing {field}"
                )
        worlds.add(str(row["world_id"]))
        if row["content_hash"] != _canonical_content_hash(
            row, release=release, row_number=row_number
        ):
            raise ReleaseUnionError(
                f"{release}: row {row_number} does not match canonical content_hash"
            )
        row_tokens, _bucket, _domain = _row_metrics(
            row,
            release=release,
            row_number=row_number,
            source_profile=source_profile,
        )
        tokens += row_tokens

    report_path = release / "quality_report.json"
    report = _json_object(source_raw["quality_report.json"], report_path)
    expected_rows = len(rows)
    expected_worlds = len(worlds)
    row_set_digest = promoted_row_set_sha256(rows)
    for payload, path in ((receipt, receipt_path), (report, report_path)):
        if _integer_field(payload, "n_rows", path) != expected_rows:
            raise ReleaseUnionError(f"{path}: n_rows does not match release rows")
        if _integer_field(payload, "n_worlds", path) != expected_worlds:
            raise ReleaseUnionError(f"{path}: n_worlds does not match release rows")
    if (
        report.get("data_stage") != "train_ready"
        or report.get("release_profile_id") != profile_id
        or report.get("release_profile_sha256") != profile_digest
        or report.get("promoted_row_set_sha256") != row_set_digest
        or report.get("tokenizer_model_id") != source_profile.tokenizer_model_id
        or report.get("tokenizer_revision") != source_profile.tokenizer_revision
        or report.get("tokenizer_asset_manifest_sha256")
        != source_profile.tokenizer_asset_manifest_sha256
    ):
        raise ReleaseUnionError(f"{report_path}: promoted row-set binding is invalid")

    return (
        {
            "release": release_relative,
            "release_profile_id": profile_id,
            "release_profile_sha256": profile_digest,
            "receipt": receipt_entry,
            "files": source_entries,
            "n_rows": expected_rows,
            "n_worlds": expected_worlds,
            "n_reported_context_tokens": tokens,
            "promoted_row_set_sha256": row_set_digest,
        },
        rows,
    )


def audit_release_union(
    release_dirs: list[Path],
    *,
    root: Path,
    target_release_profile_id: str,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
) -> dict[str, Any]:
    """Validate releases and attest their deterministic local union inventory."""
    if not release_dirs:
        raise ReleaseUnionError("at least one promoted release directory is required")
    root = _validated_root(root)
    try:
        target = release_profile(target_release_profile_id)
        target_digest = release_profile_sha256(target_release_profile_id)
    except ValueError as error:
        raise ReleaseUnionError("target release profile is unknown") from error
    if target.environment != "probe":
        raise ReleaseUnionError("local union target profile must be a probe profile")

    ordered_releases = sorted(
        (
            _relative(root, path, field="promoted release directory"),
            path.absolute(),
        )
        for path in release_dirs
    )
    releases: list[dict[str, Any]] = []
    world_owners: dict[str, Path] = {}
    content_owners: dict[str, Path] = {}
    source_identity_owners: dict[tuple[str, str], str] = {}
    world_domains: dict[str, str] = {}
    domain_worlds: defaultdict[str, set[str]] = defaultdict(set)
    length_buckets: Counter[str] = Counter()
    domain_rows: Counter[str] = Counter()
    all_rows: list[dict[str, Any]] = []
    exact_tokens = 0
    for release_relative, release_path in ordered_releases:
        summary, rows = _audit_release(
            release_path,
            root=root,
            gate_attestation_key=gate_attestation_key,
        )
        releases.append(summary)
        all_rows.extend(rows)
        source_profile = release_profile(str(summary["release_profile_id"]))
        for row_number, row in enumerate(rows, start=1):
            world_id = str(row["world_id"])
            world_owner = world_owners.setdefault(world_id, release_relative)
            if world_owner != release_relative:
                raise ReleaseUnionError(
                    f"world_id {world_id!r} appears in multiple releases: "
                    f"{world_owner} and {release_relative}"
                )
            content_hash = str(row["content_hash"])
            content_owner = content_owners.get(content_hash)
            if content_owner is not None:
                location = (
                    "multiple releases"
                    if content_owner != release_relative
                    else "one release"
                )
                raise ReleaseUnionError(
                    f"content_hash {content_hash!r} is duplicated in {location}: "
                    f"{content_owner} and {release_relative}"
                )
            content_owners[content_hash] = release_relative
            source_identities = _row_source_identities(
                row, release=release_path, row_number=row_number
            )
            if (
                target.min_unique_real_source_workflows
                and not _real_source_identity_valid(row)
            ):
                raise ReleaseUnionError(
                    f"{release_path}: row {row_number} has missing real source identity"
                )
            for source_identity in source_identities:
                source_owner = source_identity_owners.setdefault(
                    source_identity, world_id
                )
                if source_owner != world_id:
                    field, identity = source_identity
                    raise ReleaseUnionError(
                        f"source identity {field}={identity!r} appears in multiple "
                        f"world_ids: {source_owner!r} and {world_id!r}"
                    )
            tokens, bucket, domain = _row_metrics(
                row,
                release=release_path,
                row_number=row_number,
                source_profile=source_profile,
            )
            existing_domain = world_domains.setdefault(world_id, domain)
            if existing_domain != domain:
                raise ReleaseUnionError(
                    f"world_id {world_id!r} has inconsistent domains"
                )
            exact_tokens += tokens
            length_buckets[bucket] += 1
            domain_rows[domain] += 1
            domain_worlds[domain].add(world_id)

    payload = {
        "schema_version": LOCAL_RELEASE_INVENTORY_SCHEMA,
        "inventory_integrity_ok": True,
        "target_gate_evaluated": False,
        "target_gate_passed": False,
        "scope": "local_release_identity_and_byte_inventory",
        "trust_mode": "local_engineering",
        "production_eligible": False,
        "gate_revision": RELEASE_GATE_REVISION,
        "target_release_profile_id": target_release_profile_id,
        "target_release_profile_sha256": target_digest,
        "target_expected_worlds": target.expected_promoted_worlds,
        "n_releases": len(releases),
        "n_rows": len(all_rows),
        "n_worlds": len(world_owners),
        "n_content_hashes": len(content_owners),
        "n_reported_context_tokens": exact_tokens,
        "length_bucket_counts": dict(sorted(length_buckets.items())),
        "domain_row_counts": dict(sorted(domain_rows.items())),
        "domain_world_counts": {
            domain: len(worlds) for domain, worlds in sorted(domain_worlds.items())
        },
        "promoted_row_set_sha256": promoted_row_set_sha256(all_rows),
        "releases": releases,
    }
    inventory = _attach_local_release_inventory_attestation(
        payload, inventory_attestation_key
    )
    inventory_attestation = inventory.get("attestation")
    if (
        not isinstance(inventory_attestation, dict)
        or inventory_attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or inventory_attestation.get("role") != "report"
        or inventory_attestation.get("environment") != "probe"
        or not verify_local_release_inventory_attestation(
            inventory, inventory_attestation_key
        )
    ):
        raise ReleaseUnionError("release inventory report attestation is invalid")
    return inventory


def _write_inventory_output(root: Path, path: Path, raw: bytes) -> None:
    root = _validated_root(root)
    output = _lexical_absolute(path)
    try:
        output.relative_to(root)
    except ValueError as error:
        raise ReleaseUnionError("inventory output is outside inventory root") from error
    parent_relative = _relative(root, output.parent, field="inventory output directory")
    parent = _open_directory_chain(
        root, parent_relative.parts, field="inventory output directory"
    )
    temporary_name = f".{output.name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                temporary_name, _OUTPUT_FILE_FLAGS, 0o600, dir_fd=parent
            )
        except OSError as error:
            raise ReleaseUnionError(
                "inventory temporary output cannot be created safely"
            ) from error
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ReleaseUnionError("inventory output write was incomplete")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(
                temporary_name,
                output.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ReleaseUnionError(
                "inventory output already exists or is unsafe"
            ) from error
        os.fsync(parent)
    except OSError as error:
        raise ReleaseUnionError("inventory output cannot be written safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent)
            os.fsync(parent)
        except FileNotFoundError:
            pass
        finally:
            os.close(parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--target-release-profile", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("release_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = audit_release_union(
            args.release_dirs,
            root=args.root,
            target_release_profile_id=args.target_release_profile,
            gate_attestation_key=attestation_key_from_env(RELEASE_GATE_PURPOSE),
            inventory_attestation_key=_local_inventory_key_from_env(),
        )
    except ReleaseUnionError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    serialized = json.dumps(summary, sort_keys=True)
    try:
        if args.output is not None:
            _write_inventory_output(
                args.root, args.output, (serialized + "\n").encode("utf-8")
            )
    except ReleaseUnionError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
