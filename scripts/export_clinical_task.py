#!/usr/bin/env python3
"""Export one disabled clinical task candidate and its local audit receipt."""

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
from longworld.core.clinicalworkflow import (
    audit_clinical_trial_approval_task,
    build_clinical_trial_approval_task,
    load_clinical_workflow_manifest,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
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


def export_clinical_task_candidate(
    source_manifest_path: Path,
    output_jsonl: Path,
    audit_path: Path,
    *,
    attestation_key: bytes | None = None,
) -> None:
    """Materialize a local candidate only after every executable audit passes."""
    if output_jsonl.resolve() == audit_path.resolve():
        raise ProvenanceError("clinical candidate and audit paths must differ")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError(
            "clinical task export requires the source verification key"
        )
    source_raw = _read_regular_file(source_manifest_path, MAX_MANIFEST_BYTES)
    manifest = load_clinical_workflow_manifest(
        source_manifest_path, attestation_key=key
    )
    task = build_clinical_trial_approval_task(manifest)
    audit = audit_clinical_trial_approval_task(task, source_attestation_key=key)
    if not audit or not all(audit.values()):
        raise ProvenanceError("clinical task executable audit failed")
    candidate_bytes = _canonical_bytes(task) + b"\n"
    receipt: dict[str, Any] = {
        "schema_version": "longworld.clinical-task-audit.v2",
        "data_stage": "candidate_task_audit",
        "promotion_status": "ignored_non_world_candidate",
        "promoted": False,
        "complete_world": False,
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "generation_integration": "disabled",
        "source_attestation_verified": audit["source_attestation_verified"],
        "signed": False,
        "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "n": 1,
        "audit": audit,
    }
    _write_atomic(output_jsonl, candidate_bytes)
    _write_atomic(audit_path, _canonical_bytes(receipt) + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--audit-out", type=Path, required=True)
    args = parser.parse_args()
    export_clinical_task_candidate(
        args.source_manifest,
        args.out,
        args.audit_out,
    )


if __name__ == "__main__":
    main()
