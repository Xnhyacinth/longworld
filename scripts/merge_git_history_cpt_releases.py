#!/usr/bin/env python3
"""Merge disjoint Git-history CPT shards without regenerating source history."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attestation_key_from_env,
    canonical_attested_payload,
    verify_attestation,
)
from longworld.core.provenance import _read_regular_file
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from scripts.export_cpt import _reject_reason, export_cpt_rows, iter_jsonl
from scripts.materialize_git_history_cpt import (
    RELEASE_SCHEMA,
    _atomic_write,
    _atomic_write_jsonl,
    _canonical_bytes,
    _sha256_file,
)

MAX_MANIFEST_BYTES = 64_000_000
_COMPATIBLE_FIELDS = (
    "schema_version",
    "data_stage",
    "training_objective",
    "trust",
    "production_eligible",
    "train_ready",
    "tokenizer_model_id",
    "tokenizer_revision",
    "tokenizer_asset_manifest_sha256",
    "longitudinal_gate_revision",
    "max_chunks_per_commit",
    "minimum_source_events",
)


def _select_disjoint_rows(
    rows: list[dict[str, Any]],
    *,
    target: dict[str, int],
    source_event_by_record: dict[str, str],
    source_text_by_record: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if (
        not target
        or not set(target).issubset(EXACT_TOKEN_BAND_RANGES)
        or any(value <= 0 for value in target.values())
    ):
        raise ValueError("merged Git history CPT target is invalid")
    ordered_bands = tuple(
        sorted(target, key=lambda name: EXACT_TOKEN_BAND_RANGES[name][0])
    )
    selected: list[dict[str, Any]] = []
    retained_rows: Counter[str] = Counter()
    retained_tokens: Counter[str] = Counter()
    used_records: set[str] = set()
    used_events: set[str] = set()
    used_source_texts: set[str] = set()
    duplicate_source_body_rows_skipped = 0
    base_workflows: set[str] = set()
    source_digests: set[str] = set()
    for row in rows:
        bucket = str(row.get("length_bucket") or "")
        if bucket not in target:
            raise ValueError("merged Git history CPT row has an invalid band")
        if retained_rows[bucket] >= target[bucket]:
            continue
        raw_records = row.get("workflow_records")
        if not isinstance(raw_records, list) or not raw_records:
            raise ValueError("merged Git history CPT row has no source records")
        record_ids = [str(record.get("record_id") or "") for record in raw_records]
        if "" in record_ids or len(record_ids) != len(set(record_ids)):
            raise ValueError("merged Git history CPT row has invalid source records")
        try:
            event_ids = {source_event_by_record[record_id] for record_id in record_ids}
            source_texts = {
                source_text_by_record[record_id] for record_id in record_ids
            }
        except KeyError as error:
            raise ValueError(
                "merged row references an unknown source record"
            ) from error
        if used_records.intersection(record_ids):
            raise ValueError("merged source record is reused across shards")
        if used_events.intersection(event_ids):
            raise ValueError("merged source event is reused across shards")
        if used_source_texts.intersection(source_texts):
            duplicate_source_body_rows_skipped += 1
            continue
        selected.append(row)
        retained_rows[bucket] += 1
        retained_tokens[bucket] += int(row.get("tokenizer_context_tokens") or 0)
        used_records.update(record_ids)
        used_events.update(event_ids)
        used_source_texts.update(source_texts)
        base_workflows.add(str(row.get("base_workflow_id") or ""))
        source_digests.add(str(row.get("source_export_digest") or ""))
    return selected, {
        "retained_rows": {name: retained_rows[name] for name in ordered_bands},
        "retained_context_tokens": {
            name: retained_tokens[name] for name in ordered_bands
        },
        "unique_source_records": len(used_records),
        "unique_source_events": len(used_events),
        "unique_source_windows": len(selected),
        "unique_workflows": len(base_workflows),
        "duplicate_source_body_rows_skipped": duplicate_source_body_rows_skipped,
        "used_source_digests": source_digests,
        "used_record_ids": used_records,
    }


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
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
        raise ValueError("source manifest path escapes input release directory")
    return resolved


def _validate_release_row_bindings(
    rows: list[dict[str, Any]],
    *,
    source_event_by_record: dict[str, str],
    source_binding_by_record: dict[str, tuple[str, str]],
) -> None:
    for row in rows:
        source_digest = str(row.get("source_export_digest") or "")
        for record in row["workflow_records"]:
            record_id = str(record["record_id"])
            expected = source_binding_by_record.get(record_id)
            if (
                record_id not in source_event_by_record
                or expected is None
                or expected != (str(record.get("sha256") or ""), source_digest)
            ):
                raise ValueError("input CPT row source binding is invalid")


def _load_release(
    release_dir: Path,
) -> tuple[
    dict[str, Any],
    bytes,
    list[dict[str, Any]],
    dict[str, str],
    dict[str, tuple[str, str]],
    dict[str, tuple[dict[str, Any], Path, bytes]],
]:
    release, release_raw = _read_manifest(release_dir / "MANIFEST.json")
    if release.get("schema_version") != RELEASE_SCHEMA:
        raise ValueError("unsupported Git history CPT release")
    cpt_path = release_dir / "cpt_rows.jsonl"
    train_path = release_dir / "train.jsonl"
    if _sha256_file(cpt_path) != release.get("cpt_rows_sha256"):
        raise ValueError("input CPT row hash does not match its release")
    if _sha256_file(train_path) != release.get("train_sha256"):
        raise ValueError("input training hash does not match its release")
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None:
        raise ValueError("source role key is required to merge CPT shards")
    source_event_by_record: dict[str, str] = {}
    source_binding_by_record: dict[str, tuple[str, str]] = {}
    sources: dict[str, tuple[dict[str, Any], Path, bytes]] = {}
    for summary in release.get("source_manifests") or []:
        if not isinstance(summary, dict):
            raise TypeError("input source manifest summary is invalid")
        path = _source_path(release_dir, summary.get("path"))
        source, raw = _read_manifest(path)
        if hashlib.sha256(raw).hexdigest() != summary.get("manifest_file_sha256"):
            raise ValueError("input source manifest file hash mismatch")
        payload = _canonical_bytes(source)
        if hashlib.sha256(payload).hexdigest() != summary.get(
            "manifest_payload_sha256"
        ):
            raise ValueError("input source manifest payload hash mismatch")
        if not verify_attestation(source, source_key, purpose="source_manifest"):
            raise ValueError("input source manifest attestation failed")
        provenance_id = (
            "sha256:" + hashlib.sha256(canonical_attested_payload(source)).hexdigest()
        )
        if provenance_id != summary.get("provenance_id") or provenance_id in sources:
            raise ValueError("input source manifest provenance is invalid")
        sources[provenance_id] = (dict(summary), path, raw)
        for record in source.get("record_index") or []:
            if not isinstance(record, dict):
                raise TypeError("input source record index is invalid")
            record_id = str(record.get("record_id") or "")
            event_id = str(record.get("source_event_id") or "")
            text_sha256 = str(record.get("text_sha256") or "")
            if (
                not record_id
                or not event_id
                or record_id in source_event_by_record
                or not text_sha256
            ):
                raise ValueError("input source record index is invalid or duplicated")
            source_event_by_record[record_id] = event_id
            source_binding_by_record[record_id] = (text_sha256, provenance_id)
    rows = list(iter_jsonl(cpt_path))
    for row in rows:
        reason = _reject_reason(row)
        if reason:
            raise ValueError(f"input CPT row contract failed during merge: {reason}")
    _validate_release_row_bindings(
        rows,
        source_event_by_record=source_event_by_record,
        source_binding_by_record=source_binding_by_record,
    )
    return (
        release,
        release_raw,
        rows,
        source_event_by_record,
        source_binding_by_record,
        sources,
    )


def merge_releases(
    input_dirs: list[Path], output_dir: Path, *, target: dict[str, int]
) -> dict[str, Any]:
    if len(input_dirs) < 2:
        raise ValueError("at least two Git history CPT shards are required")
    loaded = [_load_release(path) for path in input_dirs]
    first_release = loaded[0][0]
    for release, *_ in loaded[1:]:
        if any(
            release.get(field) != first_release.get(field)
            for field in _COMPATIBLE_FIELDS
        ):
            raise ValueError("Git history CPT shard contracts are incompatible")

    rows: list[dict[str, Any]] = []
    source_event_by_record: dict[str, str] = {}
    source_binding_by_record: dict[str, tuple[str, str]] = {}
    sources: dict[str, tuple[dict[str, Any], Path, bytes]] = {}
    for _, _, shard_rows, shard_events, shard_bindings, shard_sources in loaded:
        if source_event_by_record.keys() & shard_events.keys():
            raise ValueError("Git history CPT shards reuse a source record identity")
        if sources.keys() & shard_sources.keys():
            raise ValueError("Git history CPT shards reuse a source manifest")
        rows.extend(shard_rows)
        source_event_by_record.update(shard_events)
        source_binding_by_record.update(shard_bindings)
        sources.update(shard_sources)

    selected, stats = _select_disjoint_rows(
        rows,
        target=target,
        source_event_by_record=source_event_by_record,
        source_text_by_record={
            record_id: binding[0]
            for record_id, binding in source_binding_by_record.items()
        },
    )
    if stats["retained_rows"] != target:
        raise ValueError("merged Git history CPT quota is not filled")

    context_hashes: set[str] = set()
    source_text_hashes: set[str] = set()
    for row in selected:
        context_digest = hashlib.sha256(
            str(row["document_context"]).encode()
        ).hexdigest()
        if context_digest in context_hashes:
            raise ValueError("merged Git history CPT context is duplicated")
        context_hashes.add(context_digest)
        row_events: set[str] = set()
        for record in row["workflow_records"]:
            record_id = str(record["record_id"])
            expected = source_binding_by_record[record_id]
            if expected != (record.get("sha256"), row.get("source_export_digest")):
                raise ValueError("merged Git history CPT source binding is invalid")
            if expected[0] in source_text_hashes:
                raise ValueError("merged Git history CPT source body is duplicated")
            source_text_hashes.add(expected[0])
            row_events.add(source_event_by_record[record_id])
        if row.get("source_event_count") != len(row_events):
            raise ValueError("merged Git history CPT source event count is invalid")

    output_dir.mkdir(parents=True, exist_ok=True)
    used_sources = stats.pop("used_source_digests")
    stats.pop("used_record_ids")
    source_summaries: list[dict[str, Any]] = []
    for provenance_id in sorted(used_sources):
        summary, source_path, raw = sources[provenance_id]
        repository = str(summary.get("repository") or "").replace("/", "__")
        slice_name = source_path.parent.name
        destination = output_dir / "sources" / repository / slice_name / "MANIFEST.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError("merged source manifest destination collides")
        if not destination.exists():
            shutil.copyfile(source_path, destination)
        copied = dict(summary)
        copied["path"] = str(destination)
        source_summaries.append(copied)

    serialized_rows = [_canonical_bytes(row) for row in selected]
    cpt_path = output_dir / "cpt_rows.jsonl"
    cpt_rows_sha256 = _atomic_write_jsonl(cpt_path, serialized_rows)
    train_path = output_dir / "train.jsonl"
    export_report = export_cpt_rows(iter_jsonl(cpt_path), train_path)
    if export_report.get("n_exported") != len(selected):
        raise ValueError("merged Git history CPT training export is incomplete")

    pack_rejects: Counter[str] = Counter()
    cpt_rejects: Counter[str] = Counter()
    for release, *_ in loaded:
        pack_rejects.update(release.get("pack_reject_reasons") or {})
        cpt_rejects.update(release.get("cpt_reject_reasons") or {})
    for key in ("global_quota_unfilled", *(f"unfilled_{name}" for name in target)):
        pack_rejects.pop(key, None)
    manifest = {
        **{field: first_release[field] for field in _COMPATIBLE_FIELDS},
        "target_rows": target,
        "retained_rows": stats["retained_rows"],
        "retained_context_tokens": stats["retained_context_tokens"],
        "unique_workflows": stats["unique_workflows"],
        "unique_source_windows": stats["unique_source_windows"],
        "unique_source_records": stats["unique_source_records"],
        "unique_source_events": stats["unique_source_events"],
        "cross_band_source_record_overlap": 0,
        "cross_band_source_event_overlap": 0,
        "source_manifests": source_summaries,
        "pack_reject_reasons": dict(pack_rejects),
        "cpt_reject_reasons": dict(cpt_rejects),
        "merge_reject_reasons": {
            "duplicate_source_body_row": stats["duplicate_source_body_rows_skipped"]
        },
        "export_report": export_report,
        "cpt_rows_sha256": cpt_rows_sha256,
        "train_sha256": _sha256_file(train_path),
        "merge_inputs": [
            {
                "path": str(path),
                "release_manifest_sha256": hashlib.sha256(release_raw).hexdigest(),
            }
            for path, (_, release_raw, *_) in zip(input_dirs, loaded, strict=True)
        ],
    }
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(manifest) + b"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        metavar="BAND=COUNT",
        help="repeat for each exact band, for example --target 16k=100",
    )
    args = parser.parse_args()
    target: dict[str, int] = {}
    for value in args.target:
        name, separator, count = value.partition("=")
        if not separator or name in target:
            parser.error(f"invalid or repeated target: {value}")
        try:
            target[name] = int(count)
        except ValueError:
            parser.error(f"invalid target count: {value}")
    report = merge_releases(
        args.input_dir,
        args.output_dir,
        target=target,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
