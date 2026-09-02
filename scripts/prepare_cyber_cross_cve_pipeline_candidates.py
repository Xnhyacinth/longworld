#!/usr/bin/env python3
"""Adapt signed cross-CVE histories into sidecar-bound pipeline candidates."""

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

from longworld.core.attestation import (
    attestation_key_from_env,
    sanitized_attestation_environment,
)
from longworld.core.domainhistory import (
    audit_cross_cve_pipeline_candidate,
    build_cross_cve_pipeline_candidate,
)
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.taskreplaysidecar import (
    CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER,
    MAX_TASK_REPLAY_SIDECAR_BYTES,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

MAX_CANDIDATE_BYTES = 32_000_000


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    raw = _read_regular_file(path, MAX_CANDIDATE_BYTES)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
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
        raise ValueError("cross-CVE history input is empty")
    return rows


def _load_tokenizer(model_id: str, revision: str):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
        )


def prepare(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """Build sidecar-bound pipeline candidates from audited history rows."""
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    source_key = attestation_key_from_env("task_replay_sidecar")
    if candidate_key is None or source_key is None:
        raise ValueError("candidate and source role keys are required")
    rows = _read_jsonl(input_path)
    source_bindings = [row.get("source_binding") for row in rows]
    tokenizer_pins = [
        (
            str(row.get("tokenizer_model_id") or ""),
            str(row.get("tokenizer_revision") or ""),
        )
        for row in rows
    ]
    if (
        any(not isinstance(value, dict) for value in source_bindings)
        or any(value != source_bindings[0] for value in source_bindings[1:])
        or any(value != tokenizer_pins[0] for value in tokenizer_pins[1:])
    ):
        raise ValueError("cross-CVE history rows do not share one source identity")
    source_binding = source_bindings[0]
    assert isinstance(source_binding, dict)
    model_id, revision = tokenizer_pins[0]
    tokenizer = _load_tokenizer(model_id, revision)

    def token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    asset_digest = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    provisional = [
        build_cross_cve_pipeline_candidate(
            row,
            token_counter=token_counter,
            tokenizer_asset_manifest_sha256=asset_digest,
            candidate_attestation_key=candidate_key,
        )
        for row in rows
    ]
    commitments = sorted(
        (task_candidate_content_commitment(row) for row in provisional),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    sidecar = build_task_replay_sidecar(
        adapter_id=CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[0],
        adapter_revision=CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[1],
        replay_payload={
            "source_manifest_sha256": str(
                source_binding.get("source_manifest_sha256") or ""
            ),
            "fetch_inventory_sha256": str(
                source_binding.get("fetch_inventory_sha256") or ""
            ),
            "authorization_record_id": str(
                source_binding.get("authorization_record_id") or ""
            ),
            "replay_revision": CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[1],
            "tokenizer_model_id": model_id,
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": asset_digest,
            "candidate_content_commitments": commitments,
        },
        source_attestation_key=source_key,
    )
    sidecar_bytes = _canonical_bytes(sidecar) + b"\n"
    if len(sidecar_bytes) > MAX_TASK_REPLAY_SIDECAR_BYTES:
        raise ProvenanceError("cross-CVE task replay sidecar exceeds size limit")
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_bytes, source_attestation_key=source_key
    )
    pipeline_rows = [
        build_cross_cve_pipeline_candidate(
            row,
            token_counter=token_counter,
            tokenizer_asset_manifest_sha256=asset_digest,
            candidate_attestation_key=candidate_key,
            task_replay_sidecar_binding=sidecar_binding,
        )
        for row in rows
    ]
    rebound = sorted(
        (task_candidate_content_commitment(row) for row in pipeline_rows),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    if rebound != commitments:
        raise ProvenanceError("cross-CVE sidecar content commitment mismatch")
    audits = [
        audit_cross_cve_pipeline_candidate(row, token_counter=token_counter)
        for row in pipeline_rows
    ]
    if not all(audit and all(audit.values()) for audit in audits):
        raise ProvenanceError("cross-CVE pipeline candidate export audit failed")
    candidate_bytes = b"\n".join(_canonical_bytes(row) for row in pipeline_rows) + b"\n"
    registry_bytes = (
        _canonical_bytes(
            {
                "schema_version": "longworld.replay-path-registry.v2",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {},
                "task_replay_sidecars": {
                    sidecar_binding["sha256"]: "TASK_REPLAY_SIDECAR.json"
                },
            }
        )
        + b"\n"
    )
    manifest = {
        "schema_version": "longworld.cyber-cross-cve-pipeline-manifest.v1",
        "data_stage": "candidate_pipeline_audit",
        "world_id": pipeline_rows[0]["world_id"],
        "domain": "cyber",
        "adapter_id": CYBER_CROSS_CVE_TASK_REPLAY_ADAPTER[0],
        "attempts": len(pipeline_rows),
        "accepted_candidates": len(pipeline_rows),
        "retention": 1.0,
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "source_manifest_sha256": source_binding.get("source_manifest_sha256"),
        "task_replay_sidecar": sidecar_binding,
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": asset_digest,
        "task_replay_sidecar_pending": False,
        "train_ready": False,
        "production_eligible": False,
        "complete_world": False,
        "promoted": False,
        "rows": [
            {
                "length_bucket": row["length_bucket"],
                "context_tokens": row["tokenizer_context_tokens"],
                "artifact_count": len(row["essential_artifact_ids"]),
                "event_count": row["event_count"],
                "proof_depth": row["graph"]["proof_depth"],
            }
            for row in pipeline_rows
        ],
    }
    _atomic_write(output_dir / "candidates.jsonl", candidate_bytes)
    _atomic_write(output_dir / "TASK_REPLAY_SIDECAR.json", sidecar_bytes)
    _atomic_write(output_dir / "REPLAY_PATH_REGISTRY.json", registry_bytes)
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(manifest) + b"\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
