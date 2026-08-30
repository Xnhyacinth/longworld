#!/usr/bin/env python3
"""Export one ignored, non-world Cyber task candidate and audit receipt."""

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

from longworld.core.attestation import attestation_key_from_env
from longworld.core.cyberworkflow import (
    MAX_CYBER_MANIFEST_BYTES,
    audit_cyber_kev_remediation_task,
    build_cyber_kev_remediation_task,
    load_cyber_workflow_manifest,
)
from longworld.core.provenance import (
    ProvenanceError,
    _read_regular_file,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _write_atomic(path: Path, content: bytes) -> None:
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


def export_cyber_task_candidate(
    source_manifest_path: Path,
    candidate_path: Path,
    audit_path: Path,
    *,
    attestation_key: bytes | None = None,
) -> None:
    """Verify the source signature and write one explicitly ignored candidate."""
    if candidate_path.resolve() == audit_path.resolve():
        raise ProvenanceError("cyber candidate and audit outputs must differ")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("cyber task export requires the source verification key")
    source_raw = _read_regular_file(source_manifest_path, MAX_CYBER_MANIFEST_BYTES)
    manifest = load_cyber_workflow_manifest(source_manifest_path, attestation_key=key)
    task = build_cyber_kev_remediation_task(manifest)
    audit = audit_cyber_kev_remediation_task(task, source_attestation_key=key)
    if not audit or not all(audit.values()):
        raise ProvenanceError("cyber task executable audit failed")
    candidate_bytes = _canonical_bytes(task) + b"\n"
    receipt: dict[str, Any] = {
        "schema_version": "longworld.cyber-task-audit.v1",
        "data_stage": "candidate_task_audit",
        "promotion_status": "ignored_non_world_candidate",
        "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
        "source_attestation_verified": audit["source_attestation_verified"],
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "n": 1,
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "signed": False,
        "audit": audit,
    }
    _write_atomic(candidate_path, candidate_bytes)
    _write_atomic(audit_path, _canonical_bytes(receipt) + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--audit-out", type=Path, required=True)
    args = parser.parse_args()
    export_cyber_task_candidate(
        args.source_manifest,
        args.candidate_jsonl,
        args.audit_out,
    )


if __name__ == "__main__":
    main()
