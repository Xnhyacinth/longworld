#!/usr/bin/env python3
"""Independently replay a Git-history CPT release and sign its audit report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    attach_attestation,
    attestation_key_from_env,
    canonical_attested_payload,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.githistory import (
    LICENSE_BINDING_POLICY_SCHEMA,
    LICENSE_BINDING_RECEIPT_SCHEMA,
    TOKEN_COUNT_CACHE_REVISION,
    DeterministicTokenCountCache,
    git_history_scan_coverage,
    git_object_path_proof_history_anchor,
    git_truncation_quality,
    validate_git_object_proof_public_export_governance,
    verify_git_object_path_absence_proof,
    verify_git_object_path_proof,
)
from longworld.core.provenance import _read_regular_file
from longworld.core.realworkflow import (
    PUBLIC_POLICY_SHA256_ENV,
    _validate_public_export_governance,
    approved_public_policy_digests,
)
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.export_cpt import _reject_reason, iter_jsonl
from scripts.export_github_workflow import CANONICAL_ALLOWLIST
from scripts.merge_git_history_cpt_releases import _load_release

MAX_MANIFEST_BYTES = 64_000_000
AUDIT_SCHEMA = "longworld.git-history-cpt-audit.v1"
SOURCE_SCHEMA = "longworld.git-history-source-manifest.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_GIT_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\Z")


@dataclass(frozen=True)
class SourceRecordBinding:
    text_sha256: str
    provenance_id: str
    source_event_id: str
    occurred_at: str
    source_pointer: str
    predecessor_ids: tuple[str, ...]
    ordinal: int
    manifest_record_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1_048_576):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_file(path, MAX_MANIFEST_BYTES)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"JSON manifest is not an object: {path}")
    return value, raw


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _canonical_license_policy(source: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw = CANONICAL_ALLOWLIST.read_bytes()
    current_digest = hashlib.sha256(raw).hexdigest()
    try:
        approved = approved_public_policy_digests()
    except ValueError as error:
        raise ValueError("current canonical allowlist pin is invalid") from error
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    if approved and current_digest not in approved:
        raise ValueError("current canonical allowlist is not independently approved")
    if environment in {"probe", "production"} and not approved:
        raise ValueError(f"{PUBLIC_POLICY_SHA256_ENV} is required in release mode")
    allowlist = yaml.safe_load(raw)
    repository = str(source.get("repository_url") or "").removeprefix(
        "https://github.com/"
    )
    repositories = (
        allowlist.get("repositories") if isinstance(allowlist, dict) else None
    )
    policy = repositories.get(repository) if isinstance(repositories, dict) else None
    license_binding = (
        policy.get("license_binding") if isinstance(policy, dict) else None
    )
    if (
        not isinstance(allowlist, dict)
        or allowlist.get("schema_version") != "longworld.repo-allowlist.v1"
        or not isinstance(policy, dict)
        or policy.get("visibility") != "public"
        or policy.get("license") != source.get("license")
        or "commit" not in set(policy.get("allowed_record_kinds") or [])
        or not isinstance(license_binding, dict)
    ):
        raise ValueError("source license is not pinned by canonical allowlist")
    authorization = policy.get("authorization")
    if not isinstance(authorization, dict):
        raise TypeError("source authorization is not pinned by canonical allowlist")
    normalized_authorization = dict(authorization)
    reviewed_at = normalized_authorization.get("reviewed_at")
    if isinstance(reviewed_at, datetime):
        if reviewed_at.tzinfo is None:
            raise ValueError("canonical authorization timestamp lacks timezone")
        normalized_authorization["reviewed_at"] = reviewed_at.isoformat().replace(
            "+00:00", "Z"
        )
    if source.get("authorization") != normalized_authorization:
        raise ValueError("source authorization is not pinned by canonical allowlist")
    repository_policy = {
        "repository": repository,
        "visibility": policy.get("visibility"),
        "license": policy.get("license"),
        "authorization": normalized_authorization,
        "allowed_record_kinds": policy.get("allowed_record_kinds"),
        "license_binding": license_binding,
    }
    return license_binding, hashlib.sha256(
        _canonical_bytes(repository_policy)
    ).hexdigest()


def _validate_source_manifest_schema(source: dict[str, Any]) -> None:
    if source.get("schema_version") != SOURCE_SCHEMA:
        raise ValueError("unsupported Git history source manifest schema")


def _validate_source_export_governance(source: dict[str, Any]) -> None:
    binding = source.get("license_binding")
    binding_schema = (
        binding.get("schema_version") if isinstance(binding, dict) else None
    )
    if binding_schema == LICENSE_BINDING_RECEIPT_SCHEMA:
        validate_git_object_proof_public_export_governance(source)
        return
    _validate_public_export_governance(source)


def _validate_license_binding_receipt(
    source: dict[str, Any],
    *,
    canonical_policy: dict[str, Any] | None = None,
    canonical_repository_policy_sha256: str | None = None,
) -> str:
    """Replay object-backed license history or identify an explicit legacy source."""
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    remote = source.get("remote_identity")
    parser = source.get("parser")
    if not isinstance(remote, dict) or not isinstance(parser, dict):
        raise TypeError("source license identity is invalid")
    binding = source.get("license_binding")
    if binding is None:
        if environment == "production":
            raise ValueError("production audit requires object-backed license binding")
        if parser.get("revision") in {"v5", "v6"}:
            raise ValueError("modern Git parser requires a license receipt")
        if parser.get("revision") not in {"v1", "v2", "v3", "v4"}:
            raise ValueError("Git legacy parser revision is unsupported")
        if remote.get("license_file_classifier_spdx_id") == "NOASSERTION":
            raise ValueError("legacy Git license binding cannot accept NOASSERTION")
        return "legacy-remote-head-v1"
    binding_schema = (
        binding.get("schema_version") if isinstance(binding, dict) else None
    )
    object_backed = binding_schema == LICENSE_BINDING_RECEIPT_SCHEMA
    objectless_v2 = binding_schema == "longworld.git-license-binding-receipt.v2"
    if not object_backed and not objectless_v2:
        raise ValueError("Git license-binding receipt revision is invalid")
    if objectless_v2 and environment == "production":
        raise ValueError("production audit requires object-backed license binding")
    expected_parser_revision = "v6" if object_backed else "v5"
    if parser.get("revision") != expected_parser_revision:
        raise ValueError("Git license binding does not match parser revision")
    if canonical_policy is None and canonical_repository_policy_sha256 is None:
        canonical_policy, canonical_repository_policy_sha256 = (
            _canonical_license_policy(source)
        )
    elif canonical_policy is None or canonical_repository_policy_sha256 is None:
        raise ValueError("canonical allowlist policy binding is incomplete")
    expected_binding_fields = {
        "schema_version",
        "policy",
        "policy_sha256",
        "selected_commit_count",
        "selected_first_revision",
        "selected_last_revision",
        "commit_bindings",
        "commit_bindings_sha256",
        "observed_license_blobs",
        "transition_count",
    }
    if object_backed:
        expected_binding_fields.add("object_path_proof")
    if not isinstance(binding, dict) or set(binding) != expected_binding_fields:
        raise ValueError("Git object-backed license receipt shape is invalid")
    policy = binding.get("policy")
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "approved_path",
        "approved_blobs",
    }:
        raise ValueError("Git license-binding policy shape is invalid")
    policy_digest = hashlib.sha256(_canonical_bytes(policy)).hexdigest()
    public_policy = source.get("public_policy")
    try:
        approved_public_policies = approved_public_policy_digests()
    except ValueError as error:
        raise ValueError("public policy approval pin is invalid") from error
    if (
        policy.get("schema_version") != LICENSE_BINDING_POLICY_SCHEMA
        or policy != canonical_policy
        or binding.get("policy_sha256") != policy_digest
        or not isinstance(public_policy, dict)
        or not isinstance(public_policy.get("sha256"), str)
        or _SHA256.fullmatch(public_policy["sha256"]) is None
        or public_policy.get("repository_policy_sha256")
        != canonical_repository_policy_sha256
        or public_policy.get("license_binding_policy_sha256") != policy_digest
    ):
        raise ValueError("Git license policy binding is not in canonical allowlist")
    if approved_public_policies and public_policy["sha256"] not in (
        approved_public_policies
    ):
        raise ValueError("Git source policy is not independently approved")
    if environment in {"probe", "production"} and not approved_public_policies:
        raise ValueError(f"{PUBLIC_POLICY_SHA256_ENV} is required in release mode")
    approved_path = policy.get("approved_path")
    raw_approved = policy.get("approved_blobs")
    if (
        not isinstance(approved_path, str)
        or _GIT_PATH.fullmatch(approved_path) is None
        or not isinstance(raw_approved, list)
        or not raw_approved
    ):
        raise ValueError("Git license-binding policy content is invalid")

    def validated_blobs(raw: object, *, label: str) -> dict[str, tuple[str, int]]:
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"Git {label} license blobs are invalid")
        result: dict[str, tuple[str, int]] = {}
        for item in raw:
            if not isinstance(item, dict) or set(item) != {
                "git_blob_sha",
                "sha256",
                "size",
            }:
                raise ValueError(f"Git {label} license blob is invalid")
            git_blob_sha = item.get("git_blob_sha")
            sha256 = item.get("sha256")
            size = item.get("size")
            if (
                not isinstance(git_blob_sha, str)
                or _GIT_SHA.fullmatch(git_blob_sha) is None
                or git_blob_sha in result
                or not isinstance(sha256, str)
                or _SHA256.fullmatch(sha256) is None
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size <= 0
            ):
                raise ValueError(f"Git {label} license blob is invalid")
            result[git_blob_sha] = (sha256, size)
        return result

    approved = validated_blobs(raw_approved, label="approved")
    observed = validated_blobs(binding.get("observed_license_blobs"), label="observed")
    if any(
        approved.get(git_blob_sha) != identity
        for git_blob_sha, identity in observed.items()
    ):
        raise ValueError("Git observed license blob is not approved")
    commit_bindings = binding.get("commit_bindings")
    selected_count = binding.get("selected_commit_count")
    if (
        not isinstance(commit_bindings, list)
        or not commit_bindings
        or isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or selected_count != len(commit_bindings)
        or selected_count != source.get("observed_commit_count")
    ):
        raise ValueError("Git license commit binding count is invalid")
    if (
        binding.get("commit_bindings_sha256")
        != hashlib.sha256(_canonical_bytes(commit_bindings)).hexdigest()
    ):
        raise ValueError("Git license commit binding digest is invalid")
    revisions: list[str] = []
    blob_sequence: list[str] = []
    for item in commit_bindings:
        if not isinstance(item, dict) or set(item) != {
            "revision",
            "git_blob_sha",
        }:
            raise ValueError("Git license commit binding is invalid")
        revision = item.get("revision")
        git_blob_sha = item.get("git_blob_sha")
        if (
            not isinstance(revision, str)
            or _GIT_SHA.fullmatch(revision) is None
            or revision in revisions
            or not isinstance(git_blob_sha, str)
            or git_blob_sha not in observed
        ):
            raise ValueError("Git license commit binding is invalid")
        revisions.append(revision)
        blob_sequence.append(git_blob_sha)
    if (
        binding.get("selected_first_revision") != revisions[0]
        or binding.get("selected_last_revision") != revisions[-1]
        or source.get("revision") != revisions[-1]
        or set(observed) != set(blob_sequence)
        or binding.get("transition_count")
        != sum(left != right for left, right in pairwise(blob_sequence))
    ):
        raise ValueError("Git license history binding is inconsistent")
    record_events = {
        str(item.get("source_event_id") or "")
        for item in source.get("record_index") or []
        if isinstance(item, dict)
    }
    if not record_events.issubset(revisions):
        raise ValueError("Git source records escape license-bound history")
    if object_backed:
        verify_git_object_path_proof(
            binding.get("object_path_proof"),
            expected_path=approved_path,
            expected_commit_bindings=commit_bindings,
            expected_license_blobs=binding.get("observed_license_blobs"),
        )
        proof = binding.get("object_path_proof")
        if not isinstance(proof, dict) or source.get(
            "git_object_proof_privacy_review"
        ) != proof.get("public_metadata_review"):
            raise ValueError("Git object proof privacy review is unbound")
    expected_remote_fields = {
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
    last_blob = blob_sequence[-1]
    last_sha256, last_size = observed[last_blob]
    repository_url = source.get("repository_url")
    source_license = source.get("license")
    if (
        set(remote) != expected_remote_fields
        or any(
            not isinstance(remote.get(field), str)
            or _SHA256.fullmatch(str(remote.get(field))) is None
            for field in (
                "repository_response_sha256",
                "commit_response_sha256",
                "license_response_sha256",
            )
        )
        or remote.get("head_revision") != revisions[-1]
        or remote.get("license_file_revision") != revisions[-1]
        or remote.get("repository_url") != repository_url
        or remote.get("license") != source_license
        or remote.get("repository_license_spdx_id") != source_license
        or remote.get("license_file_classifier_spdx_id")
        not in {source_license, "NOASSERTION"}
        or remote.get("license_file_path") != approved_path
        or remote.get("license_file_git_blob_sha") != last_blob
        or remote.get("license_file_sha256") != last_sha256
        or remote.get("license_file_size") != last_size
        or remote.get("license_file_html_url")
        != f"{repository_url}/blob/{revisions[-1]}/{approved_path}"
        or remote.get("license_file_download_url")
        != (
            str(repository_url).replace(
                "https://github.com/", "https://raw.githubusercontent.com/"
            )
            + f"/{revisions[-1]}/{approved_path}"
        )
    ):
        raise ValueError("Git remote license tip is not bound to selected history")
    return (
        "git-license-binding-v3"
        if object_backed
        else "git-license-binding-v2-objectless"
    )


def _source_path(release_dir: Path, value: object) -> Path:
    path = Path(str(value or ""))
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    release = release_dir.resolve()
    if release != resolved and release not in resolved.parents:
        raise ValueError("source manifest path escapes release directory")
    return resolved


def _release_path(value: object) -> Path:
    path = Path(str(value or ""))
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        raise ValueError("reference release path escapes the repository")
    return resolved


def _load_tokenizer(model_id: str, revision: str):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
            use_fast=True,
        )
    tokenizer.model_max_length = max(int(tokenizer.model_max_length), 1_000_000_000)
    return tokenizer


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary_name = output.name
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _validate_retained_count_contract(
    observed: dict[str, int],
    *,
    target: dict[str, int],
    require_full_target: bool,
    require_nonempty: bool = False,
) -> None:
    invalid = (
        set(observed) != set(target)
        or any(observed[name] > target[name] for name in target)
        or (require_full_target and observed != target)
        or (require_nonempty and sum(observed.values()) == 0)
    )
    if invalid:
        raise ValueError("release row counts do not match audited rows")


def _validate_complete_scan_contract(
    source_manifests: object,
    scan_coverage: object,
    *,
    required: bool,
) -> None:
    if not required:
        return
    if (
        not isinstance(source_manifests, list)
        or not source_manifests
        or not isinstance(scan_coverage, list)
        or not scan_coverage
        or any(
            not isinstance(item, dict) or item.get("source_scan_complete") is not True
            for item in scan_coverage
        )
    ):
        raise ValueError("required complete source scan is incomplete")


def _validate_source_truncation_contract(
    source: dict[str, Any], maximum_truncated_commit_ratio_ppm: int | None
) -> None:
    observed_commit_count = source.get("observed_commit_count")
    if (
        isinstance(observed_commit_count, bool)
        or not isinstance(observed_commit_count, int)
        or observed_commit_count < 0
    ):
        raise ValueError("source observed commit count is invalid")
    reject_reasons = source.get("reject_reasons")
    if not isinstance(reject_reasons, dict) or any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for key, value in reject_reasons.items()
    ):
        raise ValueError("source truncation counters are invalid")
    expected = git_truncation_quality(
        observed_commit_count,
        reject_reasons,
        maximum_truncated_commit_ratio_ppm=maximum_truncated_commit_ratio_ppm,
    )
    if source.get("truncation_quality") != expected:
        raise ValueError("source truncation quality does not replay")
    raw_index = source.get("truncated_commit_index")
    if not isinstance(raw_index, list):
        raise TypeError("source truncation event index is invalid")
    seen_events: set[str] = set()
    truncated_event_shape: dict[str, tuple[int, int]] = {}
    omitted_chunks = 0
    for item in raw_index:
        if not isinstance(item, dict) or set(item) != {
            "source_event_id",
            "commit_chunk_count_total",
            "commit_chunk_count_emitted",
        }:
            raise ValueError("source truncation event index is invalid")
        event_id = item["source_event_id"]
        total = item["commit_chunk_count_total"]
        emitted = item["commit_chunk_count_emitted"]
        if (
            not isinstance(event_id, str)
            or not event_id
            or event_id in seen_events
            or isinstance(total, bool)
            or not isinstance(total, int)
            or isinstance(emitted, bool)
            or not isinstance(emitted, int)
            or not 0 < emitted < total
        ):
            raise ValueError("source truncation event index is invalid")
        seen_events.add(event_id)
        truncated_event_shape[event_id] = (total, emitted)
        omitted_chunks += total - emitted
    if (
        len(seen_events) != expected["truncated_commit_count"]
        or omitted_chunks != expected["omitted_chunk_count"]
    ):
        raise ValueError("source truncation event index does not replay")
    truncated_record_events: set[str] = set()
    for record in source.get("record_index") or []:
        if not isinstance(record, dict):
            raise TypeError("source truncation record metadata is invalid")
        chunk_index = record.get("chunk_index")
        total = record.get("commit_chunk_count_total")
        emitted = record.get("commit_chunk_count_emitted")
        was_truncated = record.get("commit_was_truncated")
        if (
            isinstance(chunk_index, bool)
            or not isinstance(chunk_index, int)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or isinstance(emitted, bool)
            or not isinstance(emitted, int)
            or not isinstance(was_truncated, bool)
            or not 0 <= chunk_index < emitted <= total
            or was_truncated != (emitted < total)
        ):
            raise ValueError("source truncation record metadata is invalid")
        event_id = str(record.get("source_event_id") or "")
        event_is_indexed = event_id in truncated_event_shape
        if event_is_indexed != was_truncated or (
            was_truncated and truncated_event_shape[event_id] != (total, emitted)
        ):
            raise ValueError("source truncation record is not indexed")
        if was_truncated:
            truncated_record_events.add(event_id)
    if truncated_record_events != seen_events:
        raise ValueError("source truncation event has no emitted record")


def _validate_source_summary_binding(
    summary: dict[str, Any], source: dict[str, Any]
) -> None:
    parser = source.get("parser")
    expected = {
        "repository": str(source.get("repository_url") or "").removeprefix(
            "https://github.com/"
        ),
        "commit_count": source.get("observed_commit_count"),
        "skip_commits": parser.get("skip_commits")
        if isinstance(parser, dict)
        else None,
        "record_count": source.get("accepted_record_count"),
        "reject_reasons": source.get("reject_reasons"),
        "truncation_quality": source.get("truncation_quality"),
        "history_coverage": source.get("history_coverage"),
        "history_slice_anchor": source.get("history_slice_anchor"),
    }
    if any(summary.get(field) != value for field, value in expected.items()):
        raise ValueError("source manifest summary is not bound to signed source")


def _validate_history_coverage(source: dict[str, Any]) -> bool:
    coverage = source.get("history_coverage")
    parser = source.get("parser")
    if coverage is None:
        if os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower() == (
            "production"
        ):
            raise ValueError("production audit requires Git history coverage")
        return False
    if not isinstance(coverage, dict) or not isinstance(parser, dict):
        raise TypeError("Git history coverage is missing")
    common_fields = {
        "schema_version",
        "scope",
        "root_revision",
        "total_first_parent_commits",
        "included_commit_count",
        "excluded_commit_count",
    }
    scope = coverage.get("scope")
    expected_fields = set(common_fields)
    if scope == "approved_license_path_contiguous_suffix":
        expected_fields.update(
            {
                "oldest_included_revision",
                "first_excluded_revision",
                "approved_path",
                "exclusion_reason",
            }
        )
    if (
        set(coverage) != expected_fields
        or coverage.get("schema_version")
        != "longworld.git-history-coverage-boundary.v1"
        or scope
        not in {
            "complete_first_parent_history",
            "approved_license_path_contiguous_suffix",
        }
        or coverage.get("root_revision") != parser.get("root_revision")
    ):
        raise ValueError("Git history coverage contract is invalid")
    total = coverage.get("total_first_parent_commits")
    included = coverage.get("included_commit_count")
    excluded = coverage.get("excluded_commit_count")
    skip = parser.get("skip_commits")
    observed = source.get("observed_commit_count")
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (total, included, excluded, skip, observed)
        )
        or total <= 0
        or included <= 0
        or included + excluded != total
        or skip + observed > included
        or (scope == "complete_first_parent_history" and excluded != 0)
    ):
        raise ValueError("Git history coverage counts are invalid")
    if scope == "approved_license_path_contiguous_suffix":
        binding = source.get("license_binding")
        policy = binding.get("policy") if isinstance(binding, dict) else None
        for field in ("oldest_included_revision", "first_excluded_revision"):
            value = coverage.get(field)
            if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
                raise ValueError("Git history coverage boundary is invalid")
        if (
            excluded <= 0
            or coverage.get("exclusion_reason") != "approved_license_path_absent"
            or not isinstance(policy, dict)
            or coverage.get("approved_path") != policy.get("approved_path")
        ):
            raise ValueError("Git history coverage boundary is invalid")
        verify_git_object_path_absence_proof(
            source.get("history_coverage_absence_proof"),
            expected_commit_revision=str(coverage["first_excluded_revision"]),
            expected_path=str(coverage["approved_path"]),
        )
    elif source.get("history_coverage_absence_proof") is not None:
        raise ValueError("complete Git history has an unexpected absence proof")
    binding = source.get("license_binding")
    proof = binding.get("object_path_proof") if isinstance(binding, dict) else None
    anchor = git_object_path_proof_history_anchor(proof)
    if source.get("history_slice_anchor") != anchor:
        raise ValueError("Git history slice anchor does not replay")
    return True


def _validate_source_record_binding(
    expected: SourceRecordBinding,
    record: dict[str, Any],
    row_source_digest: object,
) -> None:
    if (
        expected.text_sha256 != str(record.get("sha256") or "")
        or expected.provenance_id != str(row_source_digest or "")
        or expected.occurred_at != str(record.get("occurred_at") or "")
        or expected.source_pointer != str(record.get("source_pointer") or "")
    ):
        raise ValueError("CPT row source record binding mismatch")


def _validate_source_window_binding(
    expected_records: list[SourceRecordBinding],
    row_records: list[dict[str, Any]],
    row_source_digest: object,
    *,
    source_start_index: object,
    source_end_index: object,
) -> None:
    if (
        not expected_records
        or len(expected_records) != len(row_records)
        or isinstance(source_start_index, bool)
        or not isinstance(source_start_index, int)
        or isinstance(source_end_index, bool)
        or not isinstance(source_end_index, int)
        or source_start_index < 0
        or source_end_index <= source_start_index
        or source_end_index - source_start_index != len(row_records)
        or [record.ordinal for record in expected_records]
        != list(range(source_start_index, source_end_index))
        or any(
            record.manifest_record_count != expected_records[0].manifest_record_count
            or source_end_index > record.manifest_record_count
            for record in expected_records
        )
    ):
        raise ValueError("CPT row source window is not contiguous")
    selected_ids = {str(record.get("record_id") or "") for record in row_records}
    for index, (expected, record) in enumerate(
        zip(expected_records, row_records, strict=True)
    ):
        _validate_source_record_binding(expected, record, row_source_digest)
        expected_predecessors = (
            ()
            if index == 0
            else tuple(
                predecessor
                for predecessor in expected.predecessor_ids
                if predecessor in selected_ids
            )
        )
        raw_predecessors = record.get("predecessor_ids")
        if not isinstance(raw_predecessors, list) or tuple(raw_predecessors) != (
            expected_predecessors
        ):
            raise ValueError("CPT row source record binding mismatch")


def _cross_release_references(release: dict[str, Any]) -> list[dict[str, str]]:
    legacy = release.get("cross_release_reference")
    multiple = release.get("cross_release_references")
    if legacy is not None and multiple is not None:
        raise ValueError("cross-release reference contract is ambiguous")
    raw = [legacy] if legacy is not None else multiple
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise ValueError("cross-release reference contract is invalid")
    references: list[dict[str, str]] = []
    for reference in raw:
        if not isinstance(reference, dict) or set(reference) != {
            "path",
            "release_manifest_sha256",
        }:
            raise ValueError("cross-release reference contract is invalid")
        references.append(
            {
                "path": str(reference["path"]),
                "release_manifest_sha256": str(reference["release_manifest_sha256"]),
            }
        )
    if len({item["release_manifest_sha256"] for item in references}) != len(references):
        raise ValueError("cross-release reference contract is duplicated")
    return references


def _reference_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _reference_receipt_for_path(value: object) -> dict[str, str]:
    path = _release_path(value)
    _, raw, *_ = _load_release(path)
    return {
        "path": _reference_path_text(path),
        "release_manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _load_reference_closure(
    references: list[dict[str, str]],
) -> list[
    tuple[
        dict[str, str],
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, str],
        dict[str, tuple[str, str]],
    ]
]:
    loaded: dict[
        str,
        tuple[
            dict[str, str],
            dict[str, Any],
            list[dict[str, Any]],
            dict[str, str],
            dict[str, tuple[str, str]],
        ],
    ] = {}
    active: set[str] = set()

    def visit(reference: dict[str, str]) -> None:
        reference_dir = _release_path(reference["path"])
        release, raw, rows, events, bindings, _ = _load_release(reference_dir)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != reference["release_manifest_sha256"]:
            raise ValueError("cross-release reference manifest hash mismatch")
        if digest in active:
            raise ValueError("cross-release reference cycle is invalid")
        canonical_path = _reference_path_text(reference_dir)
        if digest in loaded:
            if loaded[digest][0]["path"] != canonical_path:
                raise ValueError("cross-release reference manifest has path aliases")
            return
        active.add(digest)
        for parent in _cross_release_references(release):
            visit(parent)
        active.remove(digest)
        receipt = {
            "path": canonical_path,
            "release_manifest_sha256": digest,
        }
        loaded[digest] = (receipt, release, rows, events, bindings)

    for reference in references:
        visit(reference)
    return [loaded[digest] for digest in sorted(loaded)]


def audit_release(release_dir: Path) -> dict[str, Any]:
    manifest_path = release_dir / "MANIFEST.json"
    release, release_raw = _read_object(manifest_path)
    if release.get("schema_version") != "longworld.git-history-cpt-release.v1":
        raise ValueError("unsupported Git history CPT release")
    raw_target_rows = release.get("target_rows")
    if (
        not isinstance(raw_target_rows, dict)
        or not raw_target_rows
        or not set(raw_target_rows).issubset(EXACT_TOKEN_BAND_RANGES)
        or any(
            not isinstance(value, int) or value <= 0
            for value in raw_target_rows.values()
        )
    ):
        raise ValueError("Git history CPT release bands are invalid")
    band_names = tuple(
        sorted(raw_target_rows, key=lambda name: EXACT_TOKEN_BAND_RANGES[name][0])
    )
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    require_full_target = release.get("require_full_target", True)
    if not isinstance(require_full_target, bool):
        raise TypeError("Git history CPT target policy is invalid")
    require_complete_source_scan = release.get("require_complete_source_scan", False)
    if not isinstance(require_complete_source_scan, bool):
        raise TypeError("Git history complete source scan policy is invalid")
    if environment == "production" and require_complete_source_scan is not True:
        raise ValueError("production audit requires a complete source scan")
    raw_minimum_source_events = release.get("minimum_source_events")
    longitudinal = raw_minimum_source_events is not None
    if longitudinal:
        if (
            not isinstance(raw_minimum_source_events, dict)
            or set(raw_minimum_source_events) != set(band_names)
            or release.get("longitudinal_gate_revision") != "git-distinct-commit-v1"
            or not isinstance(release.get("max_chunks_per_commit"), int)
            or release["max_chunks_per_commit"] <= 0
        ):
            raise ValueError("longitudinal release contract is invalid")
        minimum_source_events = {
            name: int(raw_minimum_source_events.get(name) or 0) for name in band_names
        }
        if any(value <= 1 for value in minimum_source_events.values()):
            raise ValueError("longitudinal source-event minimum is invalid")
    else:
        minimum_source_events = {name: 1 for name in band_names}
    raw_minimum_source_elapsed_seconds = release.get("minimum_source_elapsed_seconds")
    span_gated = raw_minimum_source_elapsed_seconds is not None
    if span_gated:
        if (
            not longitudinal
            or not isinstance(raw_minimum_source_elapsed_seconds, dict)
            or set(raw_minimum_source_elapsed_seconds) != set(band_names)
            or release.get("source_span_gate_revision")
            != "git-observed-committer-timestamp-span-v1"
        ):
            raise ValueError("source timestamp-span release contract is invalid")
        minimum_source_elapsed_seconds = {}
        for name in band_names:
            value = raw_minimum_source_elapsed_seconds.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("source timestamp-span minimum is invalid")
            minimum_source_elapsed_seconds[name] = value
    else:
        if release.get("source_span_gate_revision") is not None:
            raise ValueError("source timestamp-span release contract is incomplete")
        minimum_source_elapsed_seconds = {}
    raw_maximum_truncation_ratio = release.get("maximum_truncated_commit_ratio_ppm")
    truncation_gated = raw_maximum_truncation_ratio is not None
    if truncation_gated:
        if (
            isinstance(raw_maximum_truncation_ratio, bool)
            or not isinstance(raw_maximum_truncation_ratio, int)
            or not 0 <= raw_maximum_truncation_ratio <= 1_000_000
            or release.get("truncation_quality_gate_revision")
            != "git-observed-prefix-truncation-v1"
        ):
            raise ValueError("source truncation release contract is invalid")
        maximum_truncated_commit_ratio_ppm = raw_maximum_truncation_ratio
    else:
        if release.get("truncation_quality_gate_revision") is not None:
            raise ValueError("source truncation release contract is incomplete")
        maximum_truncated_commit_ratio_ppm = None
    cpt_path = release_dir / "cpt_rows.jsonl"
    train_path = release_dir / "train.jsonl"
    if _sha256_file(cpt_path) != release.get("cpt_rows_sha256"):
        raise ValueError("CPT row file hash does not match release manifest")
    if _sha256_file(train_path) != release.get("train_sha256"):
        raise ValueError("CPT train file hash does not match release manifest")

    source_key = attestation_key_from_env("source_manifest")
    if source_key is None:
        raise ValueError("source audit key is unavailable")
    source_records: dict[str, SourceRecordBinding] = {}
    base_workflows: set[str] = set()
    verified_source_manifests = 0
    history_coverage_manifests = 0
    license_binding_revisions: Counter[str] = Counter()
    source_manifests = release.get("source_manifests")
    if not isinstance(source_manifests, list):
        raise TypeError("release source manifests are invalid")
    for summary in source_manifests:
        if not isinstance(summary, dict):
            raise TypeError("source manifest summary is invalid")
        path = _source_path(release_dir, summary.get("path"))
        source, raw = _read_object(path)
        _validate_source_manifest_schema(source)
        if hashlib.sha256(raw).hexdigest() != summary.get("manifest_file_sha256"):
            raise ValueError("source manifest file hash mismatch")
        payload = json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if hashlib.sha256(payload).hexdigest() != summary.get(
            "manifest_payload_sha256"
        ):
            raise ValueError("source manifest payload hash mismatch")
        if not verify_attestation(source, source_key, purpose="source_manifest"):
            raise ValueError("source manifest attestation failed")
        _validate_source_export_governance(source)
        provenance_id = (
            "sha256:" + hashlib.sha256(canonical_attested_payload(source)).hexdigest()
        )
        if provenance_id != summary.get("provenance_id"):
            raise ValueError("source manifest provenance mismatch")
        remote = source.get("remote_identity")
        if (
            not isinstance(remote, dict)
            or remote.get("head_revision") != source.get("revision")
            or remote.get("repository_url") != source.get("repository_url")
            or remote.get("license") != source.get("license")
        ):
            raise ValueError("source remote identity is unbound")
        license_binding_revisions[_validate_license_binding_receipt(source)] += 1
        history_coverage_manifests += int(_validate_history_coverage(source))
        parser = source.get("parser")
        if longitudinal and (
            not isinstance(parser, dict)
            or parser.get("revision") not in {"v3", "v4", "v5", "v6"}
            or parser.get("max_chunks_per_commit")
            != release.get("max_chunks_per_commit")
            or parser.get("oversized_commit_policy")
            != "real_prefix_chunks_with_audited_omission"
        ):
            raise ValueError("longitudinal source parser contract is invalid")
        if isinstance(parser, dict) and parser.get("revision") in {"v4", "v5", "v6"}:
            _validate_source_truncation_contract(
                source, maximum_truncated_commit_ratio_ppm
            )
        elif truncation_gated:
            raise ValueError("truncation-gated source parser is not replayable")
        _validate_source_summary_binding(summary, source)
        source_record_index = source.get("record_index")
        if not isinstance(source_record_index, list) or not source_record_index:
            raise TypeError("source record index is invalid")
        manifest_record_count = len(source_record_index)
        for ordinal, record in enumerate(source_record_index):
            if not isinstance(record, dict):
                raise TypeError("source record index is invalid")
            record_id = str(record.get("record_id") or "")
            text_sha256 = str(record.get("text_sha256") or "")
            source_event_id = str(record.get("source_event_id") or "")
            source_occurred_at = str(record.get("occurred_at") or "")
            if not record_id or record_id in source_records:
                raise ValueError("source record index is duplicated")
            if longitudinal and not source_event_id:
                raise ValueError("longitudinal source event identity is missing")
            try:
                source_timestamp = datetime.fromisoformat(
                    source_occurred_at.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise ValueError("source record timestamp is invalid") from error
            if source_timestamp.tzinfo is None:
                raise ValueError("source record timestamp lacks a timezone")
            source_pointer = str(record.get("source_pointer") or "")
            predecessor_ids = record.get("predecessor_ids")
            if (
                not source_pointer
                or not isinstance(predecessor_ids, list)
                or any(
                    not isinstance(value, str) or not value for value in predecessor_ids
                )
            ):
                raise ValueError("source record lineage is invalid")
            source_records[record_id] = SourceRecordBinding(
                text_sha256=text_sha256,
                provenance_id=provenance_id,
                source_event_id=source_event_id or record_id,
                occurred_at=source_occurred_at,
                source_pointer=source_pointer,
                predecessor_ids=tuple(predecessor_ids),
                ordinal=ordinal,
                manifest_record_count=manifest_record_count,
            )
        verified_source_manifests += 1

    release_scan_coverage = release.get("source_scan_coverage")
    if release_scan_coverage is None:
        if environment == "production":
            raise ValueError("production audit requires source scan coverage")
    elif release_scan_coverage != git_history_scan_coverage(source_manifests):
        raise ValueError("release source scan coverage does not replay")
    _validate_complete_scan_contract(
        source_manifests,
        release_scan_coverage,
        required=require_complete_source_scan,
    )

    model_id = str(release.get("tokenizer_model_id") or "")
    revision = str(release.get("tokenizer_revision") or "")
    asset_digest = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    if asset_digest != release.get("tokenizer_asset_manifest_sha256"):
        raise ValueError("tokenizer asset digest does not match release")
    tokenizer = _load_tokenizer(model_id, revision)

    def exact_token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    if release.get("token_count_cache_revision") not in {
        None,
        TOKEN_COUNT_CACHE_REVISION,
    }:
        raise ValueError("token count cache revision is invalid")
    token_counter = DeterministicTokenCountCache(asset_digest, exact_token_counter)
    band_counts: Counter[str] = Counter()
    band_tokens: Counter[str] = Counter()
    used_source_records: set[str] = set()
    used_source_record_texts: set[str] = set()
    used_source_events: set[str] = set()
    band_source_events: dict[str, set[str]] = {name: set() for name in band_names}
    minimum_observed_source_events: dict[str, int | None] = {
        name: None for name in band_names
    }
    minimum_observed_source_elapsed_seconds: dict[str, int | None] = {
        name: None for name in band_names
    }
    contexts: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(cpt_path):
        reason = _reject_reason(row)
        if reason:
            raise ValueError(f"CPT row contract failed during audit: {reason}")
        text = str(row["document_context"])
        exact_tokens = token_counter(text)
        if exact_tokens != row.get("tokenizer_context_tokens"):
            raise ValueError("CPT row exact token count mismatch")
        bucket = str(row.get("length_bucket") or "")
        if bucket not in band_source_events:
            raise ValueError("CPT row uses an undeclared release band")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in contexts:
            raise ValueError("CPT context is duplicated")
        record_ids: list[str] = []
        expected_source_records: list[SourceRecordBinding] = []
        row_source_events: set[str] = set()
        occurred_at: list[datetime] = []
        for record in row["workflow_records"]:
            record_id = str(record["record_id"])
            expected_source_record = source_records.get(record_id)
            if expected_source_record is None:
                raise ValueError("CPT row references an unknown source record")
            if record_id in used_source_records:
                raise ValueError("CPT source record is reused across windows")
            if expected_source_record.text_sha256 in used_source_record_texts:
                raise ValueError("CPT source record text is reused across windows")
            used_source_records.add(record_id)
            used_source_record_texts.add(expected_source_record.text_sha256)
            record_ids.append(record_id)
            expected_source_records.append(expected_source_record)
            row_source_events.add(expected_source_record.source_event_id)
            occurred_at.append(
                datetime.fromisoformat(
                    str(record["occurred_at"]).replace("Z", "+00:00")
                )
            )
        _validate_source_window_binding(
            expected_source_records,
            row["workflow_records"],
            row.get("source_export_digest"),
            source_start_index=row.get("source_start_index"),
            source_end_index=row.get("source_end_index"),
        )
        if longitudinal:
            expected_minimum = minimum_source_events.get(bucket)
            elapsed_seconds = int((max(occurred_at) - min(occurred_at)).total_seconds())
            if (
                expected_minimum is None
                or row.get("longitudinal_gate_revision") != "git-distinct-commit-v1"
                or row.get("minimum_source_event_count") != expected_minimum
                or row.get("source_event_count") != len(row_source_events)
                or len(row_source_events) < expected_minimum
                or row.get("source_elapsed_seconds") != elapsed_seconds
                or used_source_events.intersection(row_source_events)
            ):
                raise ValueError("longitudinal CPT row contract failed")
            used_source_events.update(row_source_events)
            band_source_events[bucket].update(row_source_events)
            observed_minimum = minimum_observed_source_events[bucket]
            minimum_observed_source_events[bucket] = (
                len(row_source_events)
                if observed_minimum is None
                else min(observed_minimum, len(row_source_events))
            )
            observed_elapsed = minimum_observed_source_elapsed_seconds[bucket]
            minimum_observed_source_elapsed_seconds[bucket] = (
                elapsed_seconds
                if observed_elapsed is None
                else min(observed_elapsed, elapsed_seconds)
            )
        row_span_revision = row.get("source_span_gate_revision")
        row_minimum_elapsed = row.get("minimum_source_elapsed_seconds")
        if span_gated:
            expected_elapsed = minimum_source_elapsed_seconds.get(bucket)
            if (
                expected_elapsed is None
                or row_span_revision != "git-observed-committer-timestamp-span-v1"
                or row_minimum_elapsed != expected_elapsed
                or elapsed_seconds < expected_elapsed
            ):
                raise ValueError("source timestamp-span CPT row contract failed")
        elif row_span_revision is not None or row_minimum_elapsed is not None:
            raise ValueError("source timestamp-span CPT row is undeclared")
        base_workflow = str(row.get("base_workflow_id") or "")
        base_workflows.add(base_workflow)
        band_counts[bucket] += 1
        band_tokens[bucket] += exact_tokens
        contexts[digest] = {
            "workflow_id": row["workflow_ids"][0],
            "base_workflow_id": base_workflow,
            "composition_method": row["composition_method"],
            "length_bucket": bucket,
            "tokenizer_context_tokens": exact_tokens,
            "tokenizer_model_id": model_id,
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": asset_digest,
            "source_record_count": len(record_ids),
            "source_export_digest": row["source_export_digest"],
        }
        if longitudinal:
            contexts[digest].update(
                {
                    field: row[field]
                    for field in (
                        "longitudinal_gate_revision",
                        "minimum_source_event_count",
                        "source_elapsed_seconds",
                        "source_event_count",
                    )
                }
            )

    seen_train: set[str] = set()
    for row in iter_jsonl(train_path):
        text = str(row.get("text") or "")
        digest = hashlib.sha256(text.encode()).hexdigest()
        expected_metadata = contexts.get(digest)
        if (
            expected_metadata is None
            or row.get("metadata") != expected_metadata
            or digest in seen_train
        ):
            raise ValueError("training export does not match signed CPT rows")
        seen_train.add(digest)
    if seen_train != set(contexts):
        raise ValueError("training export coverage is incomplete")

    observed_counts = {name: band_counts[name] for name in band_names}
    observed_tokens = {name: band_tokens[name] for name in band_names}
    if observed_counts != release.get("retained_rows"):
        raise ValueError("release row counts do not match audited rows")
    _validate_retained_count_contract(
        observed_counts,
        target=raw_target_rows,
        require_full_target=require_full_target,
        require_nonempty=environment == "production",
    )
    if observed_tokens != release.get("retained_context_tokens"):
        raise ValueError("release token counts do not match audited rows")
    if len(used_source_records) != release.get("unique_source_records"):
        raise ValueError("release source-record count does not match audit")
    if len(contexts) != release.get("unique_source_windows"):
        raise ValueError("release source-window count does not match audit")
    if len(base_workflows) != release.get("unique_workflows"):
        raise ValueError("release workflow count does not match audit")
    if longitudinal and (
        len(used_source_events) != release.get("unique_source_events")
        or release.get("cross_band_source_event_overlap") != 0
    ):
        raise ValueError("release source-event accounting does not match audit")

    reference_report: dict[str, Any] = {}
    references = _cross_release_references(release)
    if references:
        reference_bodies: set[str] = set()
        reference_event_ids: set[str] = set()
        reference_contexts: set[str] = set()
        reference_closure = _load_reference_closure(references)
        for (
            _,
            _,
            reference_rows,
            reference_events,
            reference_bindings,
        ) in reference_closure:
            for reference_row in reference_rows:
                reference_contexts.add(
                    hashlib.sha256(
                        str(reference_row["document_context"]).encode()
                    ).hexdigest()
                )
                for record in reference_row["workflow_records"]:
                    record_id = str(record["record_id"])
                    reference_bodies.add(reference_bindings[record_id][0])
                    reference_event_ids.add(reference_events[record_id])
        if (
            used_source_record_texts.intersection(reference_bodies)
            or used_source_events.intersection(reference_event_ids)
            or set(contexts).intersection(reference_contexts)
        ):
            raise ValueError("cross-release source identity overlap remains")
        reference_report = {
            "cross_release_reference_replayed": True,
            "cross_release_reference_count": len(reference_closure),
            "cross_release_source_body_overlap": 0,
            "cross_release_source_event_overlap": 0,
            "cross_release_context_overlap": 0,
        }

    report_key = attestation_key_from_env("quality_report")
    if report_key is None:
        raise ValueError("report audit key is unavailable")
    report = {
        "schema_version": AUDIT_SCHEMA,
        "audit_passed": True,
        "release_manifest_sha256": hashlib.sha256(release_raw).hexdigest(),
        "cpt_rows_sha256": release["cpt_rows_sha256"],
        "train_sha256": release["train_sha256"],
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": asset_digest,
        "token_count_cache_revision": TOKEN_COUNT_CACHE_REVISION,
        "token_count_cache": token_counter.stats(),
        "retained_rows": observed_counts,
        "require_full_target": require_full_target,
        "capacity_censored_by_band": {
            name: observed_counts[name] == raw_target_rows[name] for name in band_names
        },
        "retained_context_tokens": observed_tokens,
        "unique_source_workflows": len(base_workflows),
        "unique_source_windows": len(contexts),
        "unique_source_records": len(used_source_records),
        "cross_band_source_record_overlap": 0,
        "exact_duplicate_contexts": 0,
        "exact_duplicate_source_record_texts": 0,
        "verified_source_manifests": verified_source_manifests,
        "history_coverage_source_manifests": history_coverage_manifests,
        "source_scan_coverage": release_scan_coverage,
        "license_binding_revisions": dict(sorted(license_binding_revisions.items())),
        "object_backed_license_source_manifests": license_binding_revisions[
            "git-license-binding-v3"
        ],
        "objectless_v2_license_source_manifests": license_binding_revisions[
            "git-license-binding-v2-objectless"
        ],
        "legacy_license_binding_source_manifests": license_binding_revisions[
            "legacy-remote-head-v1"
        ],
        "cpt_contract_replayed": True,
        "exact_token_counts_recomputed": True,
        "training_export_reconstructed": True,
        **reference_report,
    }
    if longitudinal:
        report.update(
            {
                "cross_band_source_event_overlap": 0,
                "longitudinal_gate_revision": "git-distinct-commit-v1",
                "minimum_observed_source_events": {
                    name: int(minimum_observed_source_events[name] or 0)
                    for name in band_names
                },
                "minimum_source_events": minimum_source_events,
                "source_event_contract_replayed": True,
                "unique_source_events": len(used_source_events),
            }
        )
    if span_gated:
        report.update(
            {
                "source_span_gate_revision": (
                    "git-observed-committer-timestamp-span-v1"
                ),
                "minimum_source_elapsed_seconds": minimum_source_elapsed_seconds,
                "minimum_observed_source_elapsed_seconds": {
                    name: int(minimum_observed_source_elapsed_seconds[name] or 0)
                    for name in band_names
                },
                "source_timestamp_span_contract_replayed": True,
            }
        )
    if truncation_gated:
        report.update(
            {
                "truncation_quality_gate_revision": (
                    "git-observed-prefix-truncation-v1"
                ),
                "maximum_truncated_commit_ratio_ppm": (
                    maximum_truncated_commit_ratio_ppm
                ),
                "source_truncation_contract_replayed": True,
            }
        )
    return attach_attestation(report, report_key, purpose="quality_report")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args()
    report = audit_release(args.release_dir)
    payload = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    _atomic_write(args.release_dir / "AUDIT.json", payload + b"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
