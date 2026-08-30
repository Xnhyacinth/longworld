#!/usr/bin/env python3
"""Independently replay a Git-history CPT release and sign its audit report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    canonical_attested_payload,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.provenance import _read_regular_file
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.export_cpt import _reject_reason, iter_jsonl

MAX_MANIFEST_BYTES = 64_000_000
AUDIT_SCHEMA = "longworld.git-history-cpt-audit.v1"


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
) -> None:
    invalid = (
        set(observed) != set(target)
        or any(observed[name] > target[name] for name in target)
        or (require_full_target and observed != target)
    )
    if invalid:
        raise ValueError("release row counts do not match audited rows")


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
    require_full_target = release.get("require_full_target", True)
    if not isinstance(require_full_target, bool):
        raise TypeError("Git history CPT target policy is invalid")
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
    cpt_path = release_dir / "cpt_rows.jsonl"
    train_path = release_dir / "train.jsonl"
    if _sha256_file(cpt_path) != release.get("cpt_rows_sha256"):
        raise ValueError("CPT row file hash does not match release manifest")
    if _sha256_file(train_path) != release.get("train_sha256"):
        raise ValueError("CPT train file hash does not match release manifest")

    source_key = attestation_key_from_env("source_manifest")
    if source_key is None:
        raise ValueError("source audit key is unavailable")
    source_records: dict[str, tuple[str, str, str]] = {}
    base_workflows: set[str] = set()
    verified_source_manifests = 0
    for summary in release.get("source_manifests") or []:
        if not isinstance(summary, dict):
            raise TypeError("source manifest summary is invalid")
        path = _source_path(release_dir, summary.get("path"))
        source, raw = _read_object(path)
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
        parser = source.get("parser")
        if longitudinal and (
            not isinstance(parser, dict)
            or parser.get("revision") != "v3"
            or parser.get("max_chunks_per_commit")
            != release.get("max_chunks_per_commit")
            or parser.get("oversized_commit_policy")
            != "real_prefix_chunks_with_audited_omission"
        ):
            raise ValueError("longitudinal source parser contract is invalid")
        for record in source.get("record_index") or []:
            if not isinstance(record, dict):
                raise TypeError("source record index is invalid")
            record_id = str(record.get("record_id") or "")
            text_sha256 = str(record.get("text_sha256") or "")
            source_event_id = str(record.get("source_event_id") or "")
            if not record_id or record_id in source_records:
                raise ValueError("source record index is duplicated")
            if longitudinal and not source_event_id:
                raise ValueError("longitudinal source event identity is missing")
            source_records[record_id] = (
                text_sha256,
                provenance_id,
                source_event_id or record_id,
            )
        verified_source_manifests += 1

    model_id = str(release.get("tokenizer_model_id") or "")
    revision = str(release.get("tokenizer_revision") or "")
    asset_digest = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    if asset_digest != release.get("tokenizer_asset_manifest_sha256"):
        raise ValueError("tokenizer asset digest does not match release")
    tokenizer = _load_tokenizer(model_id, revision)
    band_counts: Counter[str] = Counter()
    band_tokens: Counter[str] = Counter()
    used_source_records: set[str] = set()
    used_source_record_texts: set[str] = set()
    used_source_events: set[str] = set()
    band_source_events: dict[str, set[str]] = {name: set() for name in band_names}
    minimum_observed_source_events: dict[str, int | None] = {
        name: None for name in band_names
    }
    contexts: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(cpt_path):
        reason = _reject_reason(row)
        if reason:
            raise ValueError(f"CPT row contract failed during audit: {reason}")
        text = str(row["document_context"])
        with sanitized_attestation_environment():
            exact_tokens = len(tokenizer.encode(text, add_special_tokens=False))
        if exact_tokens != row.get("tokenizer_context_tokens"):
            raise ValueError("CPT row exact token count mismatch")
        bucket = str(row.get("length_bucket") or "")
        if bucket not in band_source_events:
            raise ValueError("CPT row uses an undeclared release band")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in contexts:
            raise ValueError("CPT context is duplicated")
        record_ids: list[str] = []
        row_source_events: set[str] = set()
        occurred_at: list[datetime] = []
        for record in row["workflow_records"]:
            record_id = str(record["record_id"])
            expected_source_record = source_records.get(record_id)
            if expected_source_record is None:
                raise ValueError("CPT row references an unknown source record")
            if expected_source_record[:2] != (
                record["sha256"],
                row["source_export_digest"],
            ):
                raise ValueError("CPT row source record binding mismatch")
            if record_id in used_source_records:
                raise ValueError("CPT source record is reused across windows")
            if expected_source_record[0] in used_source_record_texts:
                raise ValueError("CPT source record text is reused across windows")
            used_source_records.add(record_id)
            used_source_record_texts.add(expected_source_record[0])
            record_ids.append(record_id)
            row_source_events.add(expected_source_record[2])
            occurred_at.append(
                datetime.fromisoformat(
                    str(record["occurred_at"]).replace("Z", "+00:00")
                )
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
    reference = release.get("cross_release_reference")
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {
            "path",
            "release_manifest_sha256",
        }:
            raise ValueError("cross-release reference contract is invalid")
        from scripts.merge_git_history_cpt_releases import _load_release

        reference_dir = _release_path(reference["path"])
        (
            _,
            reference_raw,
            reference_rows,
            reference_events,
            _,
            _,
        ) = _load_release(reference_dir)
        if hashlib.sha256(reference_raw).hexdigest() != reference.get(
            "release_manifest_sha256"
        ):
            raise ValueError("cross-release reference manifest hash mismatch")
        reference_bodies: set[str] = set()
        reference_event_ids: set[str] = set()
        reference_contexts: set[str] = set()
        for reference_row in reference_rows:
            reference_contexts.add(
                hashlib.sha256(
                    str(reference_row["document_context"]).encode()
                ).hexdigest()
            )
            for record in reference_row["workflow_records"]:
                record_id = str(record["record_id"])
                reference_bodies.add(str(record["sha256"]))
                reference_event_ids.add(reference_events[record_id])
        if (
            used_source_record_texts.intersection(reference_bodies)
            or used_source_events.intersection(reference_event_ids)
            or set(contexts).intersection(reference_contexts)
        ):
            raise ValueError("cross-release source identity overlap remains")
        reference_report = {
            "cross_release_reference_replayed": True,
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
