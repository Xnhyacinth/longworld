#!/usr/bin/env python3
"""Materialize exact, disjoint 64K/128K CPT rows from public Git histories."""

from __future__ import annotations

import argparse
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
from longworld.core.githistory import GitHistoryExtraction, extract_first_parent_history
from longworld.core.provenance import SourceLineage
from longworld.core.publicscan import PUBLIC_SCANNER, PUBLIC_SCANNER_REVISION
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
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
    return repositories, hashlib.sha256(raw).hexdigest()


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
) -> dict[str, Any]:
    """Bind a local object ID to a currently public, license-matched GitHub repo."""
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
    remote_license = (
        license_payload.get("license") if isinstance(license_payload, dict) else None
    )
    if (
        not isinstance(remote_license, dict)
        or remote_license.get("spdx_id") != license_id
    ):
        raise ValueError("Git history repository license does not match allowlist")
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
    max_commits: int,
    skip_commits: int,
    max_record_tokens: int,
    max_chunks_per_commit: int,
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
    unsigned = {
        "schema_version": SOURCE_SCHEMA,
        "source_origin": "real_public",
        "repository_url": f"https://github.com/{repository}",
        "revision": extraction.head_revision,
        "license": str(policy.get("license") or ""),
        "exported_at": exported_at,
        "authorization": authorization,
        "public_policy": {
            "record_id": str(authorization.get("record_id") or ""),
            "sha256": policy_sha256,
        },
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
            "revision": "v3",
            "first_parent": True,
            "source_text_deduplication": "global_sha256_fail_closed",
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
        "record_index": [
            {
                "record_id": record.record_id,
                "occurred_at": record.occurred_at,
                "source_pointer": record.source_pointer,
                "text_sha256": hashlib.sha256(record.text.encode()).hexdigest(),
                "source_event_id": str(record.attributes.get("sha") or ""),
                "predecessor_ids": list(record.links),
            }
            for record in extraction.records
        ],
    }
    return attach_attestation(unsigned, key, purpose="source_manifest")


def _requests(
    bands: dict[str, CPTBand],
    target: dict[str, int],
    multiplier: int,
    minimum_source_events: dict[str, int],
):
    requests: list[CPTWindowRequest] = []
    maximum = max(target.values()) * multiplier
    for index in range(maximum):
        for name in ("128k", "64k"):
            if index < target[name] * multiplier:
                requests.append(
                    CPTWindowRequest(
                        bands[name],
                        count=1,
                        min_source_events=minimum_source_events[name],
                    )
                )
    return tuple(requests)


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

    def token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    raw_bands = config.get("bands")
    if not isinstance(raw_bands, dict) or set(raw_bands) != {"64k", "128k"}:
        raise ValueError("Git history CPT requires exact 64k and 128k bands")
    bands = {
        name: CPTBand(
            name,
            int(value.get("lower_tokens") or 0),
            int(value.get("upper_tokens") or 0),
        )
        for name, value in raw_bands.items()
        if isinstance(value, dict)
    }
    if set(bands) != set(raw_bands):
        raise ValueError("Git history CPT band config is invalid")
    raw_target = config.get("target_rows")
    if not isinstance(raw_target, dict):
        raise TypeError("Git history CPT target rows are missing")
    target = {name: int(raw_target.get(name) or 0) for name in bands}
    if any(count <= 0 for count in target.values()):
        raise ValueError("Git history CPT target rows must be positive")
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
    max_chunks_per_commit = int(config.get("max_chunks_per_commit") or 0)
    if max_chunks_per_commit < 0 or (longitudinal and max_chunks_per_commit <= 0):
        raise ValueError("longitudinal CPT requires a source-event chunk cap")
    source_client = _file_receipt(Path("/usr/bin/gh"))
    history_client = _file_receipt(Path("/usr/bin/git"))
    tokenizer_asset_sha256 = resolved_tokenizer_asset_manifest_sha256(
        model_id, revision
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
            return int(_git(checkout, "rev-list", "--first-parent", "--count", "HEAD"))

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
        extraction = extract_first_parent_history(
            checkout,
            repository=repository,
            tokenizer=tokenizer,
            max_commits=int(source.get("max_commits") or 0),
            skip_commits=int(source.get("skip_commits") or 0),
            max_record_tokens=max_record_tokens,
            max_chunks_per_commit=max_chunks_per_commit,
        )
        extraction = _deduplicate_extraction(extraction, seen_source_text_sha256)
        license_id = str(policy.get("license") or "")
        remote_key = (repository, extraction.head_revision, license_id)
        remote_identity = remote_identities.get(remote_key)
        if remote_identity is None:
            remote_identity = validate_remote_identity(
                repository,
                extraction.head_revision,
                license_id,
            )
            remote_identities[remote_key] = remote_identity
        source_manifest = _source_manifest(
            repository=repository,
            policy=policy,
            policy_sha256=policy_sha256,
            extraction=extraction,
            exported_at=exported_at,
            source_client=source_client,
            history_client=history_client,
            remote_identity=remote_identity,
            max_commits=int(source.get("max_commits") or 0),
            skip_commits=int(source.get("skip_commits") or 0),
            max_record_tokens=max_record_tokens,
            max_chunks_per_commit=max_chunks_per_commit,
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
            parser="git_first_parent_patch@3",
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
                occurred_at = [
                    datetime.fromisoformat(record.occurred_at.replace("Z", "+00:00"))
                    for record in window.workflow.records
                ]
                unsigned.update(
                    {
                        "longitudinal_gate_revision": "git-distinct-commit-v1",
                        "minimum_source_event_count": minimum_source_events[
                            window.band.name
                        ],
                        "source_elapsed_seconds": int(
                            (max(occurred_at) - min(occurred_at)).total_seconds()
                        ),
                        "source_event_count": window.source_event_count,
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
            }
        )

    serialized_rows = [*accepted_by_band["64k"], *accepted_by_band["128k"]]
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
        "target_rows": target,
        "retained_rows": {
            name: len(accepted_by_band[name]) for name in ("64k", "128k")
        },
        "retained_context_tokens": {
            name: accepted_tokens[name] for name in ("64k", "128k")
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
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(manifest) + b"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = materialize(args.config, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["retained_rows"] != report["target_rows"]:
        raise SystemExit("Git history CPT quota was not filled")


if __name__ == "__main__":
    main()
