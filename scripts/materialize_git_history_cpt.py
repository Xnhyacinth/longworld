#!/usr/bin/env python3
"""Materialize exact, disjoint CPT rows from public Git histories."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
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
    local_probe_diagnostic_metadata,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.cptwindow import (
    CPTBand,
    CPTWindowRequest,
    pack_disjoint_workflow_windows,
)
from longworld.core.githistory import (
    LICENSE_BINDING_POLICY_SCHEMA,
    TOKEN_COUNT_CACHE_REVISION,
    DeterministicTokenCountCache,
    GitHistoryExtraction,
    bind_git_license_history,
    extract_first_parent_history,
    git_truncation_quality,
)
from longworld.core.provenance import SourceLineage
from longworld.core.publicscan import PUBLIC_SCANNER, PUBLIC_SCANNER_REVISION
from longworld.core.realworkflow import (
    PUBLIC_POLICY_SHA256_ENV,
    RealWorkflow,
    WorkflowRecord,
    _validate_public_export_governance,
    approved_public_policy_digests,
)
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.taxonomy import SourceOrigin
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.export_cpt import (
    _reject_reason,
    cpt_row_from_workflow,
    export_cpt_rows,
    iter_jsonl,
)
from scripts.export_github_workflow import CANONICAL_ALLOWLIST

CONFIG_SCHEMA = "longworld.git-history-cpt-materialization.v1"
SOURCE_SCHEMA = "longworld.git-history-source-manifest.v1"
RELEASE_SCHEMA = "longworld.git-history-cpt-release.v1"
MAX_CONFIG_BYTES = 256_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OBJECT_ID = re.compile(r"[0-9a-f]{40,64}\Z")
MAX_SOURCE_SLICES = 1_000


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _atomic_write_jsonl(path: Path, rows: list[bytes]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            for row in rows:
                line = row + b"\n"
                handle.write(line)
                digest.update(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1_048_576):
            digest.update(chunk)
    return digest.hexdigest()


def _read_config(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("Git history CPT config is too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("unsupported Git history CPT config")
    return value


def _file_receipt(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"source client is unavailable: {resolved}")
    return {
        "path": str(resolved),
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }


def _load_allowlist() -> tuple[dict[str, Any], str]:
    raw = CANONICAL_ALLOWLIST.read_bytes()
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "longworld.repo-allowlist.v1"
    ):
        raise ValueError("canonical repository allowlist is invalid")
    repositories = payload.get("repositories")
    if not isinstance(repositories, dict):
        raise TypeError("canonical repository allowlist has no repositories")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        approved = approved_public_policy_digests()
    except ValueError as error:
        raise ValueError("canonical repository allowlist pin is invalid") from error
    environment = os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
    if approved and digest not in approved:
        raise ValueError("canonical repository allowlist is not independently pinned")
    if environment in {"probe", "production"} and not approved:
        raise ValueError(f"{PUBLIC_POLICY_SHA256_ENV} is required in release mode")
    return repositories, digest


def _repository_policy_sha256(repository: str, policy: dict[str, Any]) -> str:
    authorization = policy.get("authorization")
    if not isinstance(authorization, dict):
        raise TypeError("repository authorization is missing")
    normalized_authorization = dict(authorization)
    reviewed_at = normalized_authorization.get("reviewed_at")
    if isinstance(reviewed_at, datetime):
        if reviewed_at.tzinfo is None:
            raise ValueError("repository authorization timestamp lacks timezone")
        normalized_authorization["reviewed_at"] = reviewed_at.isoformat().replace(
            "+00:00", "Z"
        )
    receipt = {
        "repository": repository,
        "visibility": policy.get("visibility"),
        "license": policy.get("license"),
        "authorization": normalized_authorization,
        "allowed_record_kinds": policy.get("allowed_record_kinds"),
        "license_binding": policy.get("license_binding"),
    }
    return hashlib.sha256(_canonical_bytes(receipt)).hexdigest()


def _git(checkout: Path, *args: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(checkout), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    return completed.stdout.strip()


def _gh_fetch(endpoint: str) -> Any:
    child_env = {
        name: os.environ[name]
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR")
        if os.environ.get(name)
    }
    child_env.update(
        {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
    )
    completed = subprocess.run(
        ["/usr/bin/gh", "api", "--hostname", "github.com", endpoint],
        check=True,
        capture_output=True,
        text=True,
        env=child_env,
    )
    return json.loads(completed.stdout)


def validate_remote_identity(
    repository: str,
    head_revision: str,
    license_id: str,
    fetch_json=_gh_fetch,
    *,
    license_binding_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a local object ID and exact license bytes to a public GitHub repo."""
    repo = fetch_json(f"repos/{repository}")
    commit = fetch_json(f"repos/{repository}/commits/{head_revision}")
    license_payload = fetch_json(f"repos/{repository}/license?ref={head_revision}")
    expected_url = f"https://github.com/{repository}"
    if (
        not isinstance(repo, dict)
        or repo.get("full_name") != repository
        or repo.get("private") is not False
        or repo.get("visibility") != "public"
        or repo.get("html_url") != expected_url
    ):
        raise ValueError("Git history source is not the expected public repository")
    if (
        not isinstance(commit, dict)
        or commit.get("sha") != head_revision
        or commit.get("html_url") != f"{expected_url}/commit/{head_revision}"
    ):
        raise ValueError("Git history HEAD is not present in the public repository")
    repository_license = repo.get("license")
    if (
        not isinstance(repository_license, dict)
        or repository_license.get("spdx_id") != license_id
    ):
        raise ValueError("Git history repository license does not match allowlist")
    license_classifier = (
        license_payload.get("license") if isinstance(license_payload, dict) else None
    )
    if (
        not isinstance(license_payload, dict)
        or not isinstance(license_classifier, dict)
        or license_classifier.get("spdx_id") not in {license_id, "NOASSERTION"}
    ):
        raise ValueError(
            "Git history revision license classifier conflicts with allowlist"
        )
    license_path = license_payload.get("path")
    license_blob_sha = license_payload.get("sha")
    license_size = license_payload.get("size")
    license_content = license_payload.get("content")
    license_html_url = license_payload.get("html_url")
    license_download_url = license_payload.get("download_url")
    if (
        not isinstance(license_path, str)
        or not license_path
        or not isinstance(license_blob_sha, str)
        or _GIT_OBJECT_ID.fullmatch(license_blob_sha) is None
        or isinstance(license_size, bool)
        or not isinstance(license_size, int)
        or license_size <= 0
        or license_payload.get("encoding") != "base64"
        or not isinstance(license_content, str)
        or not isinstance(license_html_url, str)
        or license_html_url != f"{expected_url}/blob/{head_revision}/{license_path}"
        or not isinstance(license_download_url, str)
        or license_download_url
        != (
            "https://raw.githubusercontent.com/"
            f"{repository}/{head_revision}/{license_path}"
        )
    ):
        raise ValueError("Git history revision license file identity is invalid")
    try:
        license_bytes = base64.b64decode(
            "".join(license_content.split()), validate=True
        )
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("Git history revision license content is invalid") from error
    if len(license_bytes) != license_size:
        raise ValueError("Git history revision license size does not match content")
    if (
        license_classifier["spdx_id"] == "NOASSERTION"
        and license_binding_policy is None
    ):
        raise ValueError("Git history NOASSERTION license is not independently pinned")
    if license_binding_policy is not None:
        raw_approved = license_binding_policy.get("approved_blobs")
        if (
            license_binding_policy.get("schema_version")
            != LICENSE_BINDING_POLICY_SCHEMA
            or license_binding_policy.get("approved_path") != license_path
            or not isinstance(raw_approved, list)
            or not any(
                isinstance(item, dict)
                and item.get("git_blob_sha") == license_blob_sha
                and item.get("sha256") == hashlib.sha256(license_bytes).hexdigest()
                and item.get("size") == license_size
                for item in raw_approved
            )
        ):
            raise ValueError("Git history revision license is not independently pinned")
    return {
        "repository_response_sha256": hashlib.sha256(
            _canonical_bytes(repo)
        ).hexdigest(),
        "commit_response_sha256": hashlib.sha256(_canonical_bytes(commit)).hexdigest(),
        "license_response_sha256": hashlib.sha256(
            _canonical_bytes(license_payload)
        ).hexdigest(),
        "head_revision": head_revision,
        "repository_url": expected_url,
        "license": license_id,
        "repository_license_spdx_id": str(repository_license["spdx_id"]),
        "license_file_classifier_spdx_id": str(license_classifier["spdx_id"]),
        "license_file_revision": head_revision,
        "license_file_path": license_path,
        "license_file_git_blob_sha": license_blob_sha,
        "license_file_size": license_size,
        "license_file_sha256": hashlib.sha256(license_bytes).hexdigest(),
        "license_file_html_url": license_html_url,
        "license_file_download_url": license_download_url,
    }


def _validate_checkout(checkout: Path, repository: str) -> None:
    expected = f"https://github.com/{repository}"
    remote = _git(checkout, "remote", "get-url", "origin").removesuffix(".git")
    if remote != expected:
        raise ValueError("Git history checkout remote does not match config")
    _git(checkout, "fsck", "--connectivity-only", "--strict")


def _load_tokenizer(model_id: str, revision: str):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
            use_fast=True,
        )


def _source_manifest(
    *,
    repository: str,
    policy: dict[str, Any],
    policy_sha256: str,
    extraction: Any,
    exported_at: str,
    source_client: dict[str, str],
    history_client: dict[str, str],
    remote_identity: dict[str, Any],
    license_binding: dict[str, Any],
    root_revision: str,
    max_commits: int,
    skip_commits: int,
    max_record_tokens: int,
    max_chunks_per_commit: int,
    maximum_truncated_commit_ratio_ppm: int | None,
) -> dict[str, Any]:
    key = attestation_key_from_env("source_manifest")
    if key is None:
        raise ValueError("source role key is required")
    authorization = policy.get("authorization")
    if not isinstance(authorization, dict):
        raise TypeError("repository authorization is missing")
    authorization = dict(authorization)
    reviewed_at = authorization.get("reviewed_at")
    if isinstance(reviewed_at, datetime):
        if reviewed_at.tzinfo is None:
            raise ValueError("repository authorization timestamp lacks timezone")
        authorization["reviewed_at"] = reviewed_at.isoformat().replace("+00:00", "Z")
    truncation_quality = git_truncation_quality(
        extraction.commit_count,
        extraction.reject_reasons,
        maximum_truncated_commit_ratio_ppm=maximum_truncated_commit_ratio_ppm,
    )
    truncated_commit_index = [
        {
            "source_event_id": item.sha,
            "commit_chunk_count_total": item.total_chunk_count,
            "commit_chunk_count_emitted": item.emitted_chunk_count,
        }
        for item in extraction.truncated_commits
    ]
    if (
        len(truncated_commit_index) != truncation_quality["truncated_commit_count"]
        or sum(
            item["commit_chunk_count_total"] - item["commit_chunk_count_emitted"]
            for item in truncated_commit_index
        )
        != truncation_quality["omitted_chunk_count"]
    ):
        raise ValueError("Git history truncation event index is inconsistent")
    record_index: list[dict[str, Any]] = []
    for record in extraction.records:
        attributes = record.attributes
        chunk_index = attributes.get("chunk_index")
        total_chunks = attributes.get("commit_chunk_count_total")
        emitted_chunks = attributes.get("commit_chunk_count_emitted")
        was_truncated = attributes.get("commit_was_truncated")
        if (
            isinstance(chunk_index, bool)
            or not isinstance(chunk_index, int)
            or chunk_index < 0
            or isinstance(total_chunks, bool)
            or not isinstance(total_chunks, int)
            or total_chunks <= 0
            or isinstance(emitted_chunks, bool)
            or not isinstance(emitted_chunks, int)
            or not 0 < emitted_chunks <= total_chunks
            or not isinstance(was_truncated, bool)
            or was_truncated != (emitted_chunks < total_chunks)
        ):
            raise ValueError("Git history record truncation metadata is invalid")
        record_index.append(
            {
                "record_id": record.record_id,
                "occurred_at": record.occurred_at,
                "source_pointer": record.source_pointer,
                "text_sha256": hashlib.sha256(record.text.encode()).hexdigest(),
                "source_event_id": str(attributes.get("sha") or ""),
                "predecessor_ids": list(record.links),
                "chunk_index": chunk_index,
                "commit_chunk_count_total": total_chunks,
                "commit_chunk_count_emitted": emitted_chunks,
                "commit_was_truncated": was_truncated,
            }
        )
    public_policy = {
        "record_id": str(authorization.get("record_id") or ""),
        "sha256": policy_sha256,
        "repository_policy_sha256": _repository_policy_sha256(repository, policy),
        "license_binding_policy_sha256": str(
            license_binding.get("policy_sha256") or ""
        ),
    }
    unsigned = {
        "schema_version": SOURCE_SCHEMA,
        "source_origin": "real_public",
        "repository_url": f"https://github.com/{repository}",
        "revision": extraction.head_revision,
        "license": str(policy.get("license") or ""),
        "exported_at": exported_at,
        "authorization": authorization,
        "public_policy": public_policy,
        "source_client": source_client,
        "history_client": history_client,
        "remote_identity": remote_identity,
        "privacy_review": {
            "emails": "redacted",
            "secrets": "fail_closed",
            "scanner": PUBLIC_SCANNER,
            "scanner_revision": PUBLIC_SCANNER_REVISION,
        },
        "parser": {
            "name": "git_first_parent_patch",
            "revision": "v5",
            "first_parent": True,
            "source_text_deduplication": "global_sha256_fail_closed",
            "root_revision": root_revision,
            "max_commits": max_commits,
            "skip_commits": skip_commits,
            "max_record_tokens": max_record_tokens,
            "max_chunks_per_commit": max_chunks_per_commit,
            "oversized_commit_policy": "real_prefix_chunks_with_audited_omission",
            "renames": "disabled_for_determinism",
        },
        "observed_commit_count": extraction.commit_count,
        "accepted_record_count": len(extraction.records),
        "reject_reasons": extraction.reject_reasons,
        "truncation_quality": truncation_quality,
        "truncated_commit_index": truncated_commit_index,
        "record_index": record_index,
        "license_binding": license_binding,
    }
    _validate_public_export_governance(unsigned)
    return attach_attestation(unsigned, key, purpose="source_manifest")


def _requests(
    bands: dict[str, CPTBand],
    target: dict[str, int],
    multiplier: int,
    minimum_source_events: dict[str, int],
):
    requests: list[CPTWindowRequest] = []
    maximum = max(target.values()) * multiplier
    ordered_names = sorted(
        bands, key=lambda name: bands[name].lower_tokens, reverse=True
    )
    for index in range(maximum):
        for name in ordered_names:
            if index < target[name] * multiplier:
                requests.append(
                    CPTWindowRequest(
                        bands[name],
                        count=1,
                        min_source_events=minimum_source_events[name],
                    )
                )
    return tuple(requests)


def _configured_bands(raw_bands: object) -> dict[str, CPTBand]:
    if not isinstance(raw_bands, dict) or not raw_bands:
        raise ValueError("Git history CPT requires at least one exact token band")
    if not set(raw_bands).issubset(EXACT_TOKEN_BAND_RANGES):
        raise ValueError("Git history CPT band is not registered")
    bands: dict[str, CPTBand] = {}
    for name, value in raw_bands.items():
        if not isinstance(value, dict):
            raise TypeError("Git history CPT band config is invalid")
        bounds = (
            int(value.get("lower_tokens") or 0),
            int(value.get("upper_tokens") or 0),
        )
        if bounds != EXACT_TOKEN_BAND_RANGES[name]:
            raise ValueError(f"Git history CPT band bounds do not match {name}")
        bands[name] = CPTBand(name, *bounds)
    return bands


def _configured_minimum_source_elapsed_seconds(
    raw: object,
    bands: dict[str, CPTBand],
    *,
    longitudinal: bool,
) -> dict[str, int] | None:
    if raw is None:
        return None
    if not longitudinal:
        raise ValueError("source elapsed-time gate requires longitudinal metadata")
    if not isinstance(raw, dict) or set(raw) != set(bands):
        raise ValueError("Git history CPT source elapsed minimums are invalid")
    configured: dict[str, int] = {}
    for name in bands:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("Git history CPT source elapsed minimums are invalid")
        configured[name] = value
    return configured


def _configured_truncation_ratio(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 1_000_000:
        raise ValueError("Git history CPT truncation ratio maximum is invalid")
    return raw


def _source_slices(
    sources: list[dict[str, Any]],
    *,
    slice_commits: int,
    commit_count: Callable[[dict[str, Any]], int],
) -> list[dict[str, Any]]:
    """Expand whole repositories into deterministic round-robin history slices."""
    if slice_commits <= 0 or slice_commits > 10_000:
        raise ValueError("Git history source slice size is invalid")
    if any("max_commits" in source or "skip_commits" in source for source in sources):
        raise ValueError("automatic source slicing cannot mix explicit commit ranges")
    totals = [commit_count(source) for source in sources]
    if any(total <= 0 or total > 100_000 for total in totals):
        raise ValueError("Git history first-parent commit count is invalid")
    expanded: list[dict[str, Any]] = []
    for skip_commits in range(0, max(totals), slice_commits):
        for source, total in zip(sources, totals, strict=True):
            if skip_commits >= total:
                continue
            expanded.append(
                {
                    **source,
                    "max_commits": min(slice_commits, total - skip_commits),
                    "skip_commits": skip_commits,
                }
            )
            if len(expanded) > MAX_SOURCE_SLICES:
                raise ValueError("Git history config expands to too many source slices")
    return expanded


def _deduplicate_extraction(
    extraction: GitHistoryExtraction,
    seen_text_sha256: set[str],
) -> GitHistoryExtraction:
    """Drop repeated source bodies and break, rather than invent, their edges."""
    records: list[WorkflowRecord] = []
    rejects: Counter[str] = Counter(extraction.reject_reasons)
    previous_record_id = ""
    for record in extraction.records:
        if not record.links:
            previous_record_id = ""
        digest = hashlib.sha256(record.text.encode()).hexdigest()
        if digest in seen_text_sha256:
            rejects["duplicate_source_record_text"] += 1
            previous_record_id = ""
            continue
        seen_text_sha256.add(digest)
        records.append(
            WorkflowRecord(
                record_id=record.record_id,
                kind=record.kind,
                occurred_at=record.occurred_at,
                text=record.text,
                links=(previous_record_id,) if previous_record_id else (),
                attributes=dict(record.attributes),
                source_pointer=record.source_pointer,
            )
        )
        previous_record_id = record.record_id
    return GitHistoryExtraction(
        records=tuple(records),
        commit_count=extraction.commit_count,
        head_revision=extraction.head_revision,
        reject_reasons=dict(rejects),
        truncated_commits=extraction.truncated_commits,
        commit_revisions=extraction.commit_revisions,
    )


def materialize(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = _read_config(config_path)
    repositories, policy_sha256 = _load_allowlist()
    tokenizer_config = config.get("tokenizer")
    if not isinstance(tokenizer_config, dict):
        raise TypeError("Git history CPT tokenizer config is missing")
    model_id = str(tokenizer_config.get("model_id") or "")
    revision = str(tokenizer_config.get("revision") or "")
    tokenizer = _load_tokenizer(model_id, revision)
    tokenizer.model_max_length = max(int(tokenizer.model_max_length), 1_000_000_000)

    def exact_token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    bands = _configured_bands(config.get("bands"))
    raw_target = config.get("target_rows")
    if not isinstance(raw_target, dict):
        raise TypeError("Git history CPT target rows are missing")
    target = {name: int(raw_target.get(name) or 0) for name in bands}
    if any(count <= 0 for count in target.values()):
        raise ValueError("Git history CPT target rows must be positive")
    require_full_target = config.get("require_full_target", True)
    if not isinstance(require_full_target, bool):
        raise TypeError("Git history CPT target policy is invalid")
    multiplier = int(config.get("attempt_multiplier") or 1)
    if multiplier <= 0 or multiplier > 4:
        raise ValueError("Git history CPT attempt multiplier is invalid")
    max_record_tokens = int(config.get("max_record_tokens") or 0)
    if max_record_tokens <= 0:
        raise ValueError("Git history CPT record token limit is invalid")
    raw_minimum_source_events = config.get("minimum_source_events")
    longitudinal = raw_minimum_source_events is not None
    if longitudinal:
        if not isinstance(raw_minimum_source_events, dict) or set(
            raw_minimum_source_events
        ) != set(bands):
            raise ValueError("Git history CPT source-event minimums are invalid")
        minimum_source_events = {
            name: int(raw_minimum_source_events.get(name) or 0) for name in bands
        }
        if any(value <= 1 for value in minimum_source_events.values()):
            raise ValueError("longitudinal CPT requires multiple source events")
    else:
        minimum_source_events = {name: 1 for name in bands}
    minimum_source_elapsed_seconds = _configured_minimum_source_elapsed_seconds(
        config.get("minimum_source_elapsed_seconds"),
        bands,
        longitudinal=longitudinal,
    )
    maximum_truncated_commit_ratio_ppm = _configured_truncation_ratio(
        config.get("maximum_truncated_commit_ratio_ppm")
    )
    max_chunks_per_commit = int(config.get("max_chunks_per_commit") or 0)
    if max_chunks_per_commit < 0 or (longitudinal and max_chunks_per_commit <= 0):
        raise ValueError("longitudinal CPT requires a source-event chunk cap")
    source_client = _file_receipt(Path("/usr/bin/gh"))
    history_client = _file_receipt(Path("/usr/bin/git"))
    tokenizer_asset_sha256 = resolved_tokenizer_asset_manifest_sha256(
        model_id, revision
    )
    token_counter = DeterministicTokenCountCache(
        tokenizer_asset_sha256,
        exact_token_counter,
    )
    exported_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_manifests: list[dict[str, Any]] = []
    accepted_by_band: dict[str, list[bytes]] = {name: [] for name in bands}
    accepted_tokens: Counter[str] = Counter()
    pack_rejects: Counter[str] = Counter()
    cpt_rejects: Counter[str] = Counter()
    used_record_ids: set[str] = set()
    used_source_event_ids: set[str] = set()
    seen_source_text_sha256: set[str] = set()
    validated_checkouts: set[tuple[str, Path]] = set()
    remote_identities: dict[tuple[str, str, str], dict[str, Any]] = {}

    raw_sources = config.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("Git history CPT config has no sources")
    if not all(isinstance(source, dict) for source in raw_sources):
        raise TypeError("Git history CPT source config is invalid")
    slice_commits = int(config.get("source_slice_commits") or 0)
    if slice_commits:

        def first_parent_commit_count(source: dict[str, Any]) -> int:
            repository = str(source.get("repository") or "")
            checkout = Path(str(source.get("checkout") or ""))
            if not checkout.is_absolute():
                checkout = ROOT / checkout
            checkout_key = (repository, checkout.resolve())
            if checkout_key not in validated_checkouts:
                _validate_checkout(checkout, repository)
                validated_checkouts.add(checkout_key)
            root_revision = _git(checkout, "rev-parse", "HEAD")
            if _GIT_OBJECT_ID.fullmatch(root_revision) is None:
                raise ValueError("Git history source root revision is invalid")
            source["root_revision"] = root_revision
            return int(
                _git(
                    checkout,
                    "rev-list",
                    "--first-parent",
                    "--count",
                    root_revision,
                )
            )

        raw_sources = _source_slices(
            raw_sources,
            slice_commits=slice_commits,
            commit_count=first_parent_commit_count,
        )
    for source in raw_sources:
        if all(len(accepted_by_band[name]) >= target[name] for name in bands):
            break
        repository = str(source.get("repository") or "")
        checkout_value = str(source.get("checkout") or "")
        checkout = Path(checkout_value)
        if not checkout.is_absolute():
            checkout = ROOT / checkout
        policy = repositories.get(repository)
        if not isinstance(policy, dict) or policy.get("visibility") != "public":
            raise ValueError(f"repository is not public and allowlisted: {repository}")
        if "commit" not in set(policy.get("allowed_record_kinds") or []):
            raise ValueError(f"repository does not allow commit records: {repository}")
        checkout_key = (repository, checkout.resolve())
        if checkout_key not in validated_checkouts:
            _validate_checkout(checkout, repository)
            validated_checkouts.add(checkout_key)
        root_revision = str(source.get("root_revision") or "")
        if not root_revision:
            root_revision = _git(checkout, "rev-parse", "HEAD")
        if _GIT_OBJECT_ID.fullmatch(root_revision) is None:
            raise ValueError("Git history source root revision is invalid")
        extraction = extract_first_parent_history(
            checkout,
            repository=repository,
            tokenizer=tokenizer,
            max_commits=int(source.get("max_commits") or 0),
            skip_commits=int(source.get("skip_commits") or 0),
            root_revision=root_revision,
            max_record_tokens=max_record_tokens,
            max_chunks_per_commit=max_chunks_per_commit,
        )
        extraction = _deduplicate_extraction(extraction, seen_source_text_sha256)
        license_id = str(policy.get("license") or "")
        raw_license_binding_policy = policy.get("license_binding")
        if raw_license_binding_policy is None:
            raise ValueError(
                "repository has no license-binding v2 policy for materialization"
            )
        if not isinstance(raw_license_binding_policy, dict):
            raise TypeError("repository license-binding v2 policy is invalid")
        license_binding = bind_git_license_history(
            checkout,
            extraction.commit_revisions,
            raw_license_binding_policy,
        )
        remote_key = (repository, extraction.head_revision, license_id)
        remote_identity = remote_identities.get(remote_key)
        if remote_identity is None:
            remote_identity = validate_remote_identity(
                repository,
                extraction.head_revision,
                license_id,
                license_binding_policy=raw_license_binding_policy,
            )
            remote_identities[remote_key] = remote_identity
        last_blob = str(license_binding["commit_bindings"][-1]["git_blob_sha"])
        observed = next(
            item
            for item in license_binding["observed_license_blobs"]
            if item["git_blob_sha"] == last_blob
        )
        if (
            remote_identity.get("license_file_revision") != extraction.head_revision
            or remote_identity.get("license_file_path")
            != license_binding["policy"]["approved_path"]
            or remote_identity.get("license_file_git_blob_sha") != last_blob
            or remote_identity.get("license_file_sha256") != observed["sha256"]
            or remote_identity.get("license_file_size") != observed["size"]
        ):
            raise ValueError(
                "Git history remote license tip does not match local history"
            )
        source_manifest = _source_manifest(
            repository=repository,
            policy=policy,
            policy_sha256=policy_sha256,
            extraction=extraction,
            exported_at=exported_at,
            source_client=source_client,
            history_client=history_client,
            remote_identity=remote_identity,
            license_binding=license_binding,
            root_revision=root_revision,
            max_commits=int(source.get("max_commits") or 0),
            skip_commits=int(source.get("skip_commits") or 0),
            max_record_tokens=max_record_tokens,
            max_chunks_per_commit=max_chunks_per_commit,
            maximum_truncated_commit_ratio_ppm=(maximum_truncated_commit_ratio_ppm),
        )
        source_key = attestation_key_from_env("source_manifest")
        if not verify_attestation(
            source_manifest, source_key, purpose="source_manifest"
        ):
            raise ValueError("generated Git history source manifest did not verify")
        source_digest = hashlib.sha256(
            canonical_attested_payload(source_manifest)
        ).hexdigest()
        slice_name = (
            f"skip-{int(source.get('skip_commits') or 0):06d}-"
            f"count-{int(source.get('max_commits') or 0):06d}"
        )
        manifest_path = (
            output_dir
            / "sources"
            / repository.replace("/", "__")
            / slice_name
            / "MANIFEST.json"
        )
        source_manifest_bytes = _canonical_bytes(source_manifest)
        _atomic_write(manifest_path, source_manifest_bytes + b"\n")
        lineage = SourceLineage(
            provenance_id=f"sha256:{source_digest}",
            url=f"https://github.com/{repository}",
            license=str(policy.get("license") or ""),
            retrieved_at=exported_at,
            parser="git_first_parent_patch@5",
            sha256=source_digest,
            revision=extraction.head_revision,
            source_path=str(manifest_path),
        )
        workflow = RealWorkflow(
            workflow_id=f"git-history:{repository}@{extraction.head_revision}",
            source_kind="git_history",
            source_origin=SourceOrigin.REAL_PUBLIC,
            lineage=lineage,
            records=extraction.records,
            facts={},
        )
        remaining = {name: target[name] - len(accepted_by_band[name]) for name in bands}
        packing = pack_disjoint_workflow_windows(
            workflow,
            requests=_requests(bands, remaining, multiplier, minimum_source_events),
            token_counter=token_counter,
            source_event_id=lambda record: str(record.attributes.get("sha") or ""),
        )
        pack_rejects.update(packing.reject_reasons)
        for window in packing.windows:
            if len(accepted_by_band[window.band.name]) >= target[window.band.name]:
                continue
            if used_record_ids.intersection(window.record_ids):
                raise ValueError("Git history CPT source record was reused")
            if used_source_event_ids.intersection(window.source_event_ids):
                raise ValueError("Git history CPT source event was reused")
            occurred_at = [
                datetime.fromisoformat(record.occurred_at.replace("Z", "+00:00"))
                for record in window.workflow.records
            ]
            elapsed_seconds = int((max(occurred_at) - min(occurred_at)).total_seconds())
            if (
                minimum_source_elapsed_seconds is not None
                and elapsed_seconds < minimum_source_elapsed_seconds[window.band.name]
            ):
                pack_rejects["source_elapsed_below_minimum"] += 1
                continue
            row = cpt_row_from_workflow(window.workflow)
            unsigned = {
                key: value for key, value in row.items() if key != "attestation"
            }
            unsigned.update(
                {
                    "length_bucket": window.band.name,
                    "tokenizer_context_tokens": window.context_tokens,
                    "tokenizer_model_id": model_id,
                    "tokenizer_revision": revision,
                    "tokenizer_asset_manifest_sha256": tokenizer_asset_sha256,
                    "source_record_count": len(window.record_ids),
                    "source_start_index": window.source_start_index,
                    "source_end_index": window.source_end_index,
                    "cross_band_source_overlap": 0,
                    "base_workflow_id": workflow.workflow_id,
                }
            )
            if longitudinal:
                unsigned.update(
                    {
                        "longitudinal_gate_revision": "git-distinct-commit-v1",
                        "minimum_source_event_count": minimum_source_events[
                            window.band.name
                        ],
                        "source_elapsed_seconds": elapsed_seconds,
                        "source_event_count": window.source_event_count,
                    }
                )
            if minimum_source_elapsed_seconds is not None:
                unsigned.update(
                    {
                        "source_span_gate_revision": (
                            "git-observed-committer-timestamp-span-v1"
                        ),
                        "minimum_source_elapsed_seconds": (
                            minimum_source_elapsed_seconds[window.band.name]
                        ),
                    }
                )
            promotion_key = attestation_key_from_env("cpt_row")
            if promotion_key is None:
                raise ValueError("promotion role key is required")
            row = attach_attestation(unsigned, promotion_key, purpose="cpt_row")
            reason = _reject_reason(row)
            if reason:
                cpt_rejects[reason] += 1
                continue
            accepted_by_band[window.band.name].append(_canonical_bytes(row))
            accepted_tokens[window.band.name] += window.context_tokens
            used_record_ids.update(window.record_ids)
            used_source_event_ids.update(window.source_event_ids)
        source_manifests.append(
            {
                "repository": repository,
                "path": str(manifest_path),
                "manifest_payload_sha256": hashlib.sha256(
                    source_manifest_bytes
                ).hexdigest(),
                "manifest_file_sha256": hashlib.sha256(
                    source_manifest_bytes + b"\n"
                ).hexdigest(),
                "provenance_id": lineage.provenance_id,
                "commit_count": extraction.commit_count,
                "skip_commits": int(source.get("skip_commits") or 0),
                "record_count": len(extraction.records),
                "reject_reasons": extraction.reject_reasons,
                "truncation_quality": source_manifest["truncation_quality"],
            }
        )

    ordered_band_names = sorted(bands, key=lambda name: bands[name].lower_tokens)
    serialized_rows = [
        row for name in ordered_band_names for row in accepted_by_band[name]
    ]
    if any(len(accepted_by_band[name]) < target[name] for name in bands):
        pack_rejects["global_quota_unfilled"] += sum(
            max(0, target[name] - len(accepted_by_band[name])) for name in bands
        )
    cpt_path = output_dir / "cpt_rows.jsonl"
    cpt_rows_sha256 = _atomic_write_jsonl(cpt_path, serialized_rows)
    export_path = output_dir / "train.jsonl"
    export_report = export_cpt_rows(iter_jsonl(cpt_path), export_path)
    manifest = {
        "schema_version": RELEASE_SCHEMA,
        "data_stage": "local_probe_candidate",
        "training_objective": "cpt",
        "trust": local_probe_diagnostic_metadata(),
        "production_eligible": False,
        "train_ready": False,
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": tokenizer_asset_sha256,
        "token_count_cache_revision": TOKEN_COUNT_CACHE_REVISION,
        "token_count_cache": token_counter.stats(),
        "target_rows": target,
        "selection_mode": "quota" if require_full_target else "capacity_scan",
        "require_full_target": require_full_target,
        "retained_rows": {
            name: len(accepted_by_band[name]) for name in ordered_band_names
        },
        "retained_context_tokens": {
            name: accepted_tokens[name] for name in ordered_band_names
        },
        "capacity_censored_by_band": {
            name: len(accepted_by_band[name]) == target[name]
            for name in ordered_band_names
        },
        "unique_workflows": len({str(item["repository"]) for item in source_manifests}),
        "unique_source_windows": len(serialized_rows),
        "unique_source_records": len(used_record_ids),
        "cross_band_source_record_overlap": 0,
        "source_manifests": source_manifests,
        "pack_reject_reasons": dict(pack_rejects),
        "cpt_reject_reasons": dict(cpt_rejects),
        "export_report": export_report,
        "cpt_rows_sha256": cpt_rows_sha256,
        "train_sha256": _sha256_file(export_path),
    }
    if longitudinal:
        manifest.update(
            {
                "longitudinal_gate_revision": "git-distinct-commit-v1",
                "max_chunks_per_commit": max_chunks_per_commit,
                "minimum_source_events": minimum_source_events,
                "unique_source_events": len(used_source_event_ids),
                "cross_band_source_event_overlap": 0,
            }
        )
    if minimum_source_elapsed_seconds is not None:
        manifest.update(
            {
                "source_span_gate_revision": (
                    "git-observed-committer-timestamp-span-v1"
                ),
                "minimum_source_elapsed_seconds": minimum_source_elapsed_seconds,
            }
        )
    if maximum_truncated_commit_ratio_ppm is not None:
        manifest.update(
            {
                "truncation_quality_gate_revision": (
                    "git-observed-prefix-truncation-v1"
                ),
                "maximum_truncated_commit_ratio_ppm": (
                    maximum_truncated_commit_ratio_ppm
                ),
            }
        )
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(manifest) + b"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = materialize(args.config, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["require_full_target"] and (
        report["retained_rows"] != report["target_rows"]
    ):
        raise SystemExit("Git history CPT quota was not filled")


if __name__ == "__main__":
    main()
