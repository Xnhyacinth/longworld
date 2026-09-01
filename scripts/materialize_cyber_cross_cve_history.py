#!/usr/bin/env python3
"""Materialize exact-band cross-CVE history candidates from a signed workflow."""

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
from longworld.core.cyberworkflow import load_cyber_workflow_manifest_bytes
from longworld.core.domainhistory import (
    HistoryBand,
    build_cross_cve_remediation_history_candidates,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file

CONFIG_SCHEMA = "longworld.cyber-cross-cve-materialization.v1"
MANIFEST_SCHEMA = "longworld.cyber-cross-cve-candidate-manifest.v1"
MAX_CONFIG_BYTES = 256_000
MAX_SOURCE_BYTES = 16_000_000


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _read_json(path: Path, maximum: int) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular_file(path, maximum).decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot load {path.name}") from error
    if not isinstance(value, dict):
        raise ProvenanceError(f"{path.name} must contain an object")
    return value


def _resolve(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("cross-CVE config path is invalid")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        try:
            existing = _read_regular_file(path, max(MAX_SOURCE_BYTES, len(content)))
        except OSError as error:
            raise ProvenanceError(f"cannot verify existing {path.name}") from error
        if existing != content:
            raise ProvenanceError(f"existing {path.name} differs")
        return
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


def materialize(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = _read_json(config_path, MAX_CONFIG_BYTES)
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise ProvenanceError("unsupported cross-CVE materialization config")
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None:
        raise ProvenanceError("cross-CVE materialization requires the source key")
    source_path = _resolve(config.get("signed_manifest"))
    source_raw = _read_regular_file(source_path, MAX_SOURCE_BYTES)
    source = load_cyber_workflow_manifest_bytes(source_raw, attestation_key=source_key)
    tokenizer_config = config.get("tokenizer")
    if not isinstance(tokenizer_config, dict):
        raise ProvenanceError("cross-CVE tokenizer config is invalid")
    model_id = str(tokenizer_config.get("model_id") or "")
    revision = str(tokenizer_config.get("revision") or "")
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
        )

    def token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    raw_bands = config.get("bands")
    if not isinstance(raw_bands, list) or not raw_bands:
        raise ProvenanceError("cross-CVE bands are missing")
    bands = tuple(
        HistoryBand(
            str(value.get("name") or ""),
            int(value.get("lower_tokens") or 0),
            int(value.get("upper_tokens") or 0),
        )
        for value in raw_bands
        if isinstance(value, dict)
    )
    if len(bands) != len(raw_bands):
        raise ProvenanceError("cross-CVE bands are invalid")
    rows = build_cross_cve_remediation_history_candidates(
        source,
        world_id=str(config.get("world_id") or ""),
        source_binding={
            "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
            "fetch_inventory_sha256": source["fetch_inventory_sha256"],
            "authorization_record_id": source["authorization"]["record_id"],
            "observed_at": source["generated_at"],
        },
        bands=bands,
        token_counter=token_counter,
        tokenizer_model_id=model_id,
        tokenizer_revision=revision,
    )
    candidate_bytes = b"".join(_canonical_bytes(row) for row in rows)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "data_stage": "candidate_history_audit",
        "world_id": rows[0]["world_id"],
        "domain": "cyber",
        "attempts": len(rows),
        "accepted_candidates": len(rows),
        "retention": 1.0,
        "bands": [row["length_bucket"] for row in rows],
        "tokenizer_context_tokens": [row["tokenizer_context_tokens"] for row in rows],
        "event_counts": [row["event_count"] for row in rows],
        "proof_depths": [row["graph"]["proof_depth"] for row in rows],
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
        "source_attestation_verified": True,
        "strict_replay_verified": True,
        "task_replay_sidecar_pending": True,
        "train_ready": False,
        "production_eligible": False,
        "complete_world": False,
        "promoted": False,
    }
    _write_atomic(output_dir / "candidates.jsonl", candidate_bytes)
    _write_atomic(output_dir / "MANIFEST.json", _canonical_bytes(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.config, args.output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
