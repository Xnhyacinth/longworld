#!/usr/bin/env python3
"""Build non-train-ready standard views and source-commitment rebuild input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    DENSE_RANKING_PURPOSE,
    candidate_sha256,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.taskpromotion import (
    _validate_candidate_identity,
    build_task_candidate_view_projections,
    create_task_dense_audit,
    task_sidecar_token_counter,
)
from longworld.core.taskreplaysidecar import (
    MAX_TASK_REPLAY_SIDECAR_BYTES,
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
    TASK_VIEW_DERIVATION_REVISION,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)

MANIFEST_SCHEMA = "longworld.task-view-projection-manifest.v1"
COMMITMENT_INPUT_SCHEMA = "longworld.task-view-source-commitment-input.v1"
AUDIT_MANIFEST_SCHEMA = "longworld.task-view-dense-audit-manifest.v1"
MAX_INPUT_BYTES = 512_000_000


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value).rstrip(b"\n")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = _read_regular_file(path, MAX_INPUT_BYTES).decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError, ProvenanceError) as error:
        raise ValueError("task candidate input is not a regular UTF-8 file") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{line_number}: expected an object")
        rows.append(value)
    if not rows:
        raise ValueError("task candidate input is empty")
    return rows


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _select_length_buckets(
    rows: list[dict[str, Any]], length_buckets: set[str]
) -> list[dict[str, Any]]:
    selected = [
        row for row in rows if str(row.get("length_bucket") or "") in length_buckets
    ]
    if not selected:
        raise ValueError("no task candidates match the requested length buckets")
    return selected


def _write_resumable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"existing projection output differs: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def project(
    input_path: Path,
    sidecar_path: Path,
    output_dir: Path,
    *,
    length_buckets: set[str] | None = None,
) -> dict[str, Any]:
    """Verify parent rows and emit independently signed candidate projections."""
    sidecar_path = sidecar_path.resolve()
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    source_key = attestation_key_from_env("task_replay_sidecar")
    if candidate_key is None or source_key is None:
        raise ValueError("candidate and source role keys are required")
    try:
        raw_sidecar = _read_regular_file(sidecar_path, MAX_TASK_REPLAY_SIDECAR_BYTES)
    except OSError as error:
        raise ValueError("task replay sidecar is unavailable") from error
    binding = task_replay_sidecar_binding(
        raw_sidecar, source_attestation_key=source_key
    )
    loaded = load_task_replay_sidecar(
        sidecar_path.parent,
        sidecar_path.name,
        binding,
        source_attestation_key=source_key,
    )
    token_counter = task_sidecar_token_counter(loaded)
    parents = _read_jsonl(input_path)
    if length_buckets is not None:
        parents = _select_length_buckets(parents, length_buckets)
    projected: list[dict[str, Any]] = []
    for parent in parents:
        _validate_candidate_identity(
            parent,
            loaded,
            candidate_attestation_key=candidate_key,
            source_attestation_key=source_key,
        )
        projected.extend(
            build_task_candidate_view_projections(
                parent,
                adapter_key=loaded.registry_key,
                token_counter=token_counter,
                candidate_attestation_key=candidate_key,
            )
        )
    projected.sort(
        key=lambda row: (
            str(row.get("world_id") or ""),
            str(row.get("length_bucket") or ""),
            str(row.get("view") or ""),
            candidate_sha256(row),
        )
    )
    if any(
        row.get("data_stage") != "candidate"
        or row.get("train_ready") is not False
        or row.get("production_eligible") is not False
        or row.get("promoted") is not False
        for row in projected
    ):
        raise ValueError("task view projection crossed the candidate boundary")
    source_commitments = [
        task_candidate_content_commitment(
            row, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
        )
        for row in projected
    ]
    source_commitments.sort(
        key=lambda item: (item["world_id"], item["length_bucket"], item["view"])
    )
    parent_raw = (
        _jsonl_bytes(parents)
        if length_buckets is not None
        else _read_regular_file(input_path, MAX_INPUT_BYTES)
    )
    parent_digests = sorted(candidate_sha256(parent) for parent in parents)
    parent_commitments = sorted(
        (task_candidate_content_commitment(parent) for parent in parents),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    parent_commitment_by_digest = {
        candidate_sha256(parent): task_candidate_content_commitment(parent)
        for parent in parents
    }
    derivation_receipts = [
        {
            "parent_candidate_sha256": str(
                row["task_view_projection"]["parent_candidate_sha256"]
            ),
            "parent_content_commitment": parent_commitment_by_digest[
                str(row["task_view_projection"]["parent_candidate_sha256"])
            ],
            "projection_content_commitment": task_candidate_content_commitment(
                row, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
            ),
            "projection_receipt": dict(row["task_view_projection"]),
            "projection_receipt_sha256": _canonical_sha256(row["task_view_projection"]),
        }
        for row in projected
    ]
    derivation_receipts.sort(
        key=lambda item: (
            item["projection_content_commitment"]["world_id"],
            item["projection_content_commitment"]["length_bucket"],
            item["projection_content_commitment"]["view"],
        )
    )
    replay_payload = {
        **{
            key: value
            for key, value in loaded.replay_payload.items()
            if key != "candidate_content_commitments"
        },
        "candidate_content_commitments": source_commitments,
        "parent_sidecar_raw_utf8": raw_sidecar.decode("utf-8"),
        "parent_sidecar_sha256": loaded.sidecar_sha256,
        "parent_candidates_raw_utf8": parent_raw.decode("utf-8"),
        "parent_candidates_sha256": hashlib.sha256(parent_raw).hexdigest(),
        "parent_candidate_digests": parent_digests,
        "parent_candidate_content_commitments": parent_commitments,
        "projection_derivation_revision": TASK_VIEW_DERIVATION_REVISION,
        "projection_derivation_receipts": derivation_receipts,
    }
    v3_sidecar = build_task_replay_sidecar(
        adapter_id=loaded.adapter_id,
        adapter_revision=loaded.adapter_revision,
        replay_payload=replay_payload,
        source_attestation_key=source_key,
        sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3,
    )
    v3_sidecar_bytes = _canonical_bytes(v3_sidecar)
    v3_binding = task_replay_sidecar_binding(
        v3_sidecar_bytes, source_attestation_key=source_key
    )
    rebound: list[dict[str, Any]] = []
    for candidate in projected:
        candidate = dict(candidate)
        candidate.pop("attestation", None)
        candidate["task_replay_sidecar"] = dict(v3_binding)
        candidate["promotion_blocker_code"] = (
            "missing_candidate_dense_ranking_and_audit"
        )
        candidate["pipeline_capabilities"] = {
            **dict(candidate.get("pipeline_capabilities") or {}),
            "generic_strict_replay": True,
            "generic_promotion": True,
        }
        rebound.append(
            attach_attestation(
                candidate,
                candidate_key,
                purpose=CANDIDATE_ATTESTATION_PURPOSE,
            )
        )
    projected = rebound
    commitment_rows = [
        {
            **task_candidate_content_commitment(
                row, sidecar_schema_version=TASK_REPLAY_SIDECAR_SCHEMA_V3
            ),
            "candidate_sha256": candidate_sha256(row),
        }
        for row in projected
    ]
    commitment_input = {
        "schema_version": COMMITMENT_INPUT_SCHEMA,
        "sidecar_schema_version": TASK_REPLAY_SIDECAR_SCHEMA_V3,
        "requires_source_sidecar_rebuild": False,
        "required_commitment_identity": ["world_id", "length_bucket", "view"],
        "parent_sidecar_sha256": loaded.sidecar_sha256,
        "rebuilt_sidecar_sha256": v3_binding["sha256"],
        "commitments": commitment_rows,
    }
    candidate_bytes = _jsonl_bytes(projected)
    commitment_bytes = _canonical_bytes(commitment_input)
    replay_registry_bytes = _canonical_bytes(
        {
            "schema_version": "longworld.replay-path-registry.v2",
            "episode_replay_bundles": {},
            "source_workflow_bundles": {},
            "task_replay_sidecars": {
                v3_binding["sha256"]: "TASK_REPLAY_SIDECAR_V3.json"
            },
        }
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "input_candidate_count": len(parents),
        "projection_candidate_count": len(projected),
        "views": sorted({str(row["view"]) for row in projected}),
        "train_ready": False,
        "production_eligible": False,
        "requires_source_sidecar_rebuild": False,
        "dense_audit_complete": False,
        "projection_candidates_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "source_commitment_input_sha256": hashlib.sha256(commitment_bytes).hexdigest(),
        "parent_sidecar_sha256": loaded.sidecar_sha256,
        "rebuilt_sidecar_sha256": v3_binding["sha256"],
    }
    _write_resumable(output_dir / "TASK_REPLAY_SIDECAR_V3.json", v3_sidecar_bytes)
    _write_resumable(output_dir / "REPLAY_PATH_REGISTRY.json", replay_registry_bytes)
    _write_resumable(output_dir / "candidates.jsonl", candidate_bytes)
    _write_resumable(output_dir / "SOURCE_COMMITMENT_INPUT.json", commitment_bytes)
    _write_resumable(output_dir / "MANIFEST.json", _canonical_bytes(manifest))
    return manifest


def audit_projections(output_dir: Path, ranking_path: Path) -> dict[str, Any]:
    """Replay one independently signed dense ranking for every projection."""
    output_dir = output_dir.resolve()
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    ranking_key = attestation_key_from_env(DENSE_RANKING_PURPOSE)
    audit_key = attestation_key_from_env(DENSE_AUDIT_PURPOSE)
    source_key = attestation_key_from_env("task_replay_sidecar")
    if None in {candidate_key, ranking_key, audit_key, source_key}:
        raise ValueError(
            "candidate, ranker, auditor, and source role keys are required"
        )
    sidecar_path = output_dir / "TASK_REPLAY_SIDECAR_V3.json"
    try:
        raw_sidecar = _read_regular_file(sidecar_path, MAX_TASK_REPLAY_SIDECAR_BYTES)
    except OSError as error:
        raise ValueError("projected task replay sidecar is unavailable") from error
    binding = task_replay_sidecar_binding(
        raw_sidecar, source_attestation_key=source_key
    )
    sidecar = load_task_replay_sidecar(
        output_dir,
        sidecar_path.name,
        binding,
        source_attestation_key=source_key,
    )
    if sidecar.sidecar_schema_version != TASK_REPLAY_SIDECAR_SCHEMA_V3:
        raise ValueError("projected task replay sidecar is not v3")
    candidates = _read_jsonl(output_dir / "candidates.jsonl")
    rankings = _read_jsonl(ranking_path)
    ranking_by_query: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        query_id = str(ranking.get("query_id") or "")
        if not query_id or query_id in ranking_by_query:
            raise ValueError("dense rankings have missing or duplicate query ids")
        ranking_by_query[query_id] = ranking
    candidate_queries = {str(row.get("query_id") or "") for row in candidates}
    if "" in candidate_queries or set(ranking_by_query) != candidate_queries:
        raise ValueError("dense rankings do not exactly cover projection candidates")
    audits = [
        create_task_dense_audit(
            candidate,
            ranking_by_query[str(candidate["query_id"])],
            sidecar,
            candidate_attestation_key=candidate_key,
            ranking_attestation_key=ranking_key,
            audit_attestation_key=audit_key,
            source_attestation_key=source_key,
        )
        for candidate in candidates
    ]
    audits.sort(key=lambda row: (str(row["query_id"]), str(row["candidate_sha256"])))
    audit_bytes = _jsonl_bytes(audits)
    ranking_bytes = _read_regular_file(ranking_path, MAX_INPUT_BYTES)
    manifest = {
        "schema_version": AUDIT_MANIFEST_SCHEMA,
        "audited_projection_count": len(audits),
        "candidate_sha256": sorted(str(row["candidate_sha256"]) for row in audits),
        "dense_audit_complete": True,
        "train_ready": False,
        "production_eligible": False,
        "selected": False,
        "promoted": False,
        "rankings_sha256": hashlib.sha256(ranking_bytes).hexdigest(),
        "audits_sha256": hashlib.sha256(audit_bytes).hexdigest(),
        "rebuilt_sidecar_sha256": sidecar.sidecar_sha256,
    }
    _write_resumable(output_dir / "audits.jsonl", audit_bytes)
    _write_resumable(output_dir / "AUDIT_MANIFEST.json", _canonical_bytes(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--length-bucket",
        action="append",
        choices=("16k", "32k", "64k", "128k"),
        help="project only the selected exact length bucket; repeat as needed",
    )
    parser.add_argument(
        "--rankings",
        type=Path,
        help="optional signed ranking JSONL for independent dense audits",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="audit existing projections without rebuilding them",
    )
    args = parser.parse_args()
    if args.audit_only:
        if args.rankings is None:
            raise SystemExit("--audit-only requires --rankings")
        audit_projections(args.output_dir, args.rankings)
        return 0
    project(
        args.candidates,
        args.sidecar,
        args.output_dir,
        length_buckets=set(args.length_bucket) if args.length_bucket else None,
    )
    if args.rankings is not None:
        audit_projections(args.output_dir, args.rankings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
