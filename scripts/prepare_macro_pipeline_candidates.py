#!/usr/bin/env python3
"""Prepare signed Macro candidates for the shared dense-ranking stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.macrovintage import (
    MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA,
    audit_macro_vintage_pipeline_candidate,
)
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE, candidate_sha256
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.taskreplaysidecar import (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    MAX_TASK_REPLAY_SIDECAR_BYTES,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Macro candidate input is missing or not a regular file")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
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
        raise ValueError("Macro candidate input is empty")
    return rows


def prepare(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """Verify one source sidecar, bind candidates, and sign candidate rows."""
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    source_key = attestation_key_from_env("task_replay_sidecar")
    if candidate_key is None:
        raise ValueError("candidate-row attestation key is required")
    if source_key is None:
        raise ValueError("source task-replay attestation key is required")
    input_rows = _read_jsonl(input_path)
    if any(
        row.get("schema_version") != MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA
        for row in input_rows
    ):
        raise ValueError("Macro candidate schema is invalid")
    source_bindings = [row.get("source_binding") for row in input_rows]
    tokenizer_pins = [
        (
            str(row.get("tokenizer_model_id") or ""),
            str(row.get("tokenizer_revision") or ""),
            str(row.get("tokenizer_asset_manifest_sha256") or ""),
        )
        for row in input_rows
    ]
    if (
        any(not isinstance(value, dict) for value in source_bindings)
        or any(value != source_bindings[0] for value in source_bindings[1:])
        or any(value != tokenizer_pins[0] for value in tokenizer_pins[1:])
    ):
        raise ValueError("Macro candidates do not share one source identity")
    source_binding = source_bindings[0]
    assert isinstance(source_binding, dict)
    model_id, revision, asset_digest = tokenizer_pins[0]
    if (
        resolved_tokenizer_asset_manifest_sha256(model_id, revision)
        != asset_digest
    ):
        raise ProvenanceError("Macro candidate tokenizer assets do not match")

    sidecar_path = input_path.parent / "TASK_REPLAY_SIDECAR.json"
    try:
        sidecar_bytes = _read_regular_file(
            sidecar_path, MAX_TASK_REPLAY_SIDECAR_BYTES
        )
    except OSError as error:
        raise ProvenanceError("cannot read Macro task replay sidecar") from error
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_bytes, source_attestation_key=source_key
    )
    loaded = load_task_replay_sidecar(
        input_path.parent,
        sidecar_path.name,
        sidecar_binding,
        source_attestation_key=source_key,
    )
    payload = dict(loaded.replay_payload)
    observed_commitments = payload.pop("candidate_content_commitments", None)
    fetch_receipt = payload.get("fetch_receipt")
    expected_payload = {
        "workflow_manifest_sha256": source_binding.get(
            "workflow_manifest_sha256"
        ),
        "raw_source_sha256": source_binding.get("raw_source_sha256"),
        "fetch_inventory_sha256": source_binding.get("fetch_inventory_sha256"),
        "fetch_receipt": fetch_receipt,
        "source_families": source_binding.get("source_families"),
        "authorization_record_id": source_binding.get("authorization_record_id"),
        "replay_revision": MACRO_VINTAGE_TASK_REPLAY_ADAPTER[1],
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": asset_digest,
    }
    if (
        loaded.registry_key != MACRO_VINTAGE_TASK_REPLAY_ADAPTER
        or not isinstance(fetch_receipt, dict)
        or hashlib.sha256(_canonical_bytes(fetch_receipt)).hexdigest()
        != source_binding.get("fetch_receipt_sha256")
        or payload != expected_payload
    ):
        raise ProvenanceError("Macro task replay sidecar identity is inconsistent")

    unsigned = []
    for input_row in input_rows:
        row = deepcopy(input_row)
        row["task_replay_sidecar"] = deepcopy(sidecar_binding)
        row["pipeline_capabilities"] = {
            **dict(row.get("pipeline_capabilities") or {}),
            "generic_strict_replay": True,
            "generic_promotion": True,
        }
        row["promotion_blocker_code"] = (
            "missing_candidate_dense_ranking_and_audit"
        )
        unsigned.append(row)
    commitments = sorted(
        (task_candidate_content_commitment(row) for row in unsigned),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    if observed_commitments != commitments:
        raise ProvenanceError("Macro sidecar does not bind candidate content")
    audits = [audit_macro_vintage_pipeline_candidate(row) for row in unsigned]
    if not all(audit and all(audit.values()) for audit in audits):
        raise ValueError("Macro pipeline candidate audit failed")
    candidates = [
        attach_attestation(
            row, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
        )
        for row in unsigned
    ]
    candidate_bytes = b"\n".join(_canonical_bytes(row) for row in candidates) + b"\n"
    registry_bytes = _canonical_bytes(
        {
            "schema_version": "longworld.replay-path-registry.v2",
            "episode_replay_bundles": {},
            "source_workflow_bundles": {},
            "task_replay_sidecars": {
                sidecar_binding["sha256"]: "TASK_REPLAY_SIDECAR.json"
            },
        }
    ) + b"\n"
    report: dict[str, Any] = {
        "schema_version": "longworld.macro-pipeline-candidate-manifest.v1",
        "candidate_schema_version": MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA,
        "data_stage": "candidate",
        "pipeline_stage": "dense_ranking_ready",
        "world_ids": sorted({str(row["world_id"]) for row in candidates}),
        "rows": len(candidates),
        "by_length": {
            bucket: sum(row.get("length_bucket") == bucket for row in candidates)
            for bucket in ("16k", "32k", "64k")
        },
        "context_tokens": sum(
            int(row["tokenizer_context_tokens"]) for row in candidates
        ),
        "candidate_sha256s": [candidate_sha256(row) for row in candidates],
        "serialized_candidates_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "task_replay_sidecar": sidecar_binding,
        "macro_strict_replay_green": True,
        "dense_ranking_ready": True,
        "task_promotion_adapter_available": True,
        "train_ready": False,
        "promoted": False,
    }
    _atomic_write(output_dir / "candidates.jsonl", candidate_bytes)
    _atomic_write(output_dir / "TASK_REPLAY_SIDECAR.json", sidecar_bytes)
    _atomic_write(output_dir / "REPLAY_PATH_REGISTRY.json", registry_bytes)
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(report) + b"\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.output_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
