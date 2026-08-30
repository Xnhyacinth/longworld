#!/usr/bin/env python3
"""Subtract used records/events/contexts of one CPT release from another."""

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

from scripts.audit_git_history_cpt import (
    _cross_release_references,
    _load_reference_closure,
    _reference_receipt_for_path,
)
from scripts.export_cpt import export_cpt_rows, iter_jsonl
from scripts.materialize_git_history_cpt import (
    _atomic_write,
    _atomic_write_jsonl,
    _canonical_bytes,
    _sha256_file,
)
from scripts.merge_git_history_cpt_releases import _load_release


def _select_disjoint_from_reference(
    rows: list[dict[str, Any]],
    *,
    source_event_by_record: dict[str, str],
    reference_source_bodies: set[str],
    reference_source_events: set[str],
    reference_contexts: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    rejects: Counter[str] = Counter()
    for row in rows:
        records = row.get("workflow_records")
        if not isinstance(records, list) or not records:
            raise ValueError("candidate row has no source records")
        record_ids = [str(record.get("record_id") or "") for record in records]
        if "" in record_ids:
            raise ValueError("candidate row has an empty source-record identity")
        try:
            events = {source_event_by_record[record_id] for record_id in record_ids}
        except KeyError as error:
            raise ValueError(
                "candidate row references an unknown source record"
            ) from error
        bodies = {str(record.get("sha256") or "") for record in records}
        context_digest = hashlib.sha256(
            str(row.get("document_context") or "").encode()
        ).hexdigest()
        if bodies.intersection(reference_source_bodies):
            rejects["reference_source_body_overlap"] += 1
        elif events.intersection(reference_source_events):
            rejects["reference_source_event_overlap"] += 1
        elif context_digest in reference_contexts:
            rejects["reference_context_overlap"] += 1
        else:
            selected.append(row)
    return selected, dict(rejects)


def _used_sets(
    rows: list[dict[str, Any]],
    source_event_by_record: dict[str, str],
    source_binding_by_record: dict[str, tuple[str, str]],
) -> tuple[set[str], set[str], set[str]]:
    bodies: set[str] = set()
    events: set[str] = set()
    contexts: set[str] = set()
    for row in rows:
        contexts.add(hashlib.sha256(str(row["document_context"]).encode()).hexdigest())
        for record in row["workflow_records"]:
            record_id = str(record["record_id"])
            bodies.add(source_binding_by_record[record_id][0])
            events.add(source_event_by_record[record_id])
    return bodies, events, contexts


def _validate_selected_identities(
    rows: list[dict[str, Any]], source_event_by_record: dict[str, str]
) -> tuple[set[str], set[str]]:
    used_records: set[str] = set()
    used_events: set[str] = set()
    used_bodies: set[str] = set()
    used_contexts: set[str] = set()
    for row in rows:
        records = row["workflow_records"]
        row_records = {str(record["record_id"]) for record in records}
        row_bodies = {str(record["sha256"]) for record in records}
        row_events = {source_event_by_record[record_id] for record_id in row_records}
        context_digest = hashlib.sha256(
            str(row["document_context"]).encode()
        ).hexdigest()
        if (
            len(row_records) != len(records)
            or len(row_bodies) != len(records)
            or used_records.intersection(row_records)
            or used_events.intersection(row_events)
            or used_bodies.intersection(row_bodies)
            or context_digest in used_contexts
        ):
            raise ValueError("filtered CPT source identity is reused")
        used_records.update(row_records)
        used_events.update(row_events)
        used_bodies.update(row_bodies)
        used_contexts.add(context_digest)
    return used_records, used_events


def filter_release(
    reference_dirs: list[Path], candidate_dir: Path, output_dir: Path
) -> dict[str, Any]:
    if not reference_dirs or len(set(reference_dirs)) != len(reference_dirs):
        raise ValueError("reference release paths are missing or duplicated")
    candidate = _load_release(candidate_dir)
    candidate_release, _, candidate_rows, candidate_events, _, candidate_sources = (
        candidate
    )
    inherited = _cross_release_references(candidate_release)
    reference_bodies: set[str] = set()
    reference_event_ids: set[str] = set()
    reference_contexts: set[str] = set()
    direct_references = [
        *inherited,
        *(_reference_receipt_for_path(path) for path in reference_dirs),
    ]
    if len({item["release_manifest_sha256"] for item in direct_references}) != len(
        direct_references
    ):
        raise ValueError("reference release manifests are duplicated")
    reference_closure = _load_reference_closure(direct_references)
    for (
        _,
        reference_release,
        reference_rows,
        reference_events,
        bindings,
    ) in reference_closure:
        for field in (
            "tokenizer_model_id",
            "tokenizer_revision",
            "tokenizer_asset_manifest_sha256",
            "max_chunks_per_commit",
        ):
            if reference_release.get(field) != candidate_release.get(field):
                raise ValueError(
                    "reference and candidate CPT contracts are incompatible"
                )
        bodies, events, contexts = _used_sets(
            reference_rows, reference_events, bindings
        )
        reference_bodies.update(bodies)
        reference_event_ids.update(events)
        reference_contexts.update(contexts)
    selected, reject_reasons = _select_disjoint_from_reference(
        candidate_rows,
        source_event_by_record=candidate_events,
        reference_source_bodies=reference_bodies,
        reference_source_events=reference_event_ids,
        reference_contexts=reference_contexts,
    )
    if not selected:
        raise ValueError("reference subtraction removed every candidate row")

    output_dir.mkdir(parents=True, exist_ok=True)
    source_summaries: list[dict[str, Any]] = []
    for _, (summary, source_path, raw) in sorted(candidate_sources.items()):
        repository = str(summary.get("repository") or "").replace("/", "__")
        destination = (
            output_dir
            / "sources"
            / repository
            / source_path.parent.name
            / "MANIFEST.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError("filtered source manifest destination collides")
        if not destination.exists():
            shutil.copyfile(source_path, destination)
        copied = dict(summary)
        copied["path"] = str(destination)
        source_summaries.append(copied)

    serialized = [_canonical_bytes(row) for row in selected]
    cpt_path = output_dir / "cpt_rows.jsonl"
    cpt_rows_sha256 = _atomic_write_jsonl(cpt_path, serialized)
    train_path = output_dir / "train.jsonl"
    export_report = export_cpt_rows(iter_jsonl(cpt_path), train_path)
    if export_report.get("n_exported") != len(selected):
        raise ValueError("filtered CPT training export is incomplete")

    retained_rows: Counter[str] = Counter()
    retained_tokens: Counter[str] = Counter()
    used_records, used_events = _validate_selected_identities(
        selected, candidate_events
    )
    workflows: set[str] = set()
    for row in selected:
        band = str(row["length_bucket"])
        retained_rows[band] += 1
        retained_tokens[band] += int(row["tokenizer_context_tokens"])
        workflows.add(str(row["base_workflow_id"]))
    ordered_bands = tuple(candidate_release["target_rows"])
    retained_by_band = {name: retained_rows[name] for name in ordered_bands}
    manifest = {
        **candidate_release,
        "retained_rows": retained_by_band,
        "retained_context_tokens": {
            name: retained_tokens[name] for name in ordered_bands
        },
        "capacity_censored_by_band": {
            name: retained_by_band[name] == candidate_release["target_rows"][name]
            for name in ordered_bands
        },
        "unique_workflows": len(workflows),
        "unique_source_windows": len(selected),
        "unique_source_records": len(used_records),
        "unique_source_events": len(used_events),
        "source_manifests": source_summaries,
        "cross_release_references": [item[0] for item in reference_closure],
        "cross_release_reject_reasons": reject_reasons,
        "export_report": export_report,
        "cpt_rows_sha256": cpt_rows_sha256,
        "train_sha256": _sha256_file(train_path),
    }
    manifest.pop("cross_release_reference", None)
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(manifest) + b"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", action="append", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = filter_release(args.reference_dir, args.candidate_dir, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
