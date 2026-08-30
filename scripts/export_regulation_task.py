#!/usr/bin/env python3
"""Export one audited, non-promoted regulation task candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _read_regular_file,
)
from longworld.core.regulationworkflow import (
    audit_regulation_rulemaking_task,
    build_regulation_rulemaking_task,
    load_regulation_workflow_manifest,
)


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def export_regulation_task_candidate(
    source_manifest_path: Path,
    candidate_path: Path,
    audit_path: Path,
    *,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> None:
    """Write one candidate JSONL row plus a fail-closed audit receipt."""
    if candidate_path.resolve() == audit_path.resolve():
        raise ProvenanceError("regulation candidate and audit outputs must differ")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError(
            "regulation task export requires a source attestation key"
        )
    raw = _read_regular_file(source_manifest_path, MAX_MANIFEST_BYTES)
    manifest = load_regulation_workflow_manifest(
        source_manifest_path, attestation_key=key
    )
    task = build_regulation_rulemaking_task(manifest)
    gates = audit_regulation_rulemaking_task(task, source_attestation_key=key)
    if not gates or not all(gates.values()):
        raise ProvenanceError("regulation task candidate failed executable audit")
    candidate_bytes = (
        json.dumps(task, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    receipt: dict[str, Any] = {
        "schema_version": "longworld.regulation-task-audit.v2",
        "data_stage": "candidate_task_audit",
        "promotion_status": "ignored_non_world_candidate",
        "generated_at": timestamp,
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_attestation_verified": gates["source_attestation_verified"],
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "n": 1,
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "signed": False,
        "audit": gates,
    }
    audit_bytes = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    _atomic_write(candidate_path, candidate_bytes)
    _atomic_write(audit_path, audit_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--audit-out", type=Path, required=True)
    args = parser.parse_args()
    export_regulation_task_candidate(
        args.source_manifest,
        args.candidate_jsonl,
        args.audit_out,
    )


if __name__ == "__main__":
    main()
