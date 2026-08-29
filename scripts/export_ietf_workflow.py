#!/usr/bin/env python3
"""Validate and attest a disabled IETF standards source inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _read_regular_file,
)
from longworld.core.standardsworkflow import build_ietf_workflow_from_fetch_inventory


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def export_ietf_workflow(
    fetch_inventory_path: Path,
    output_path: Path,
    *,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> None:
    try:
        raw = _read_regular_file(fetch_inventory_path, MAX_MANIFEST_BYTES)
        payload = json.loads(raw.decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read IETF fetch inventory: {error}") from error
    if not isinstance(payload, dict):
        raise ProvenanceError("IETF fetch inventory must be an object")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("IETF workflow export requires a source attestation key")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = build_ietf_workflow_from_fetch_inventory(
        payload,
        fetch_inventory_path.parent,
        generated_at=timestamp,
        fetch_inventory_sha256=hashlib.sha256(raw).hexdigest(),
    )
    _write_json_atomic(
        output_path, attach_attestation(manifest, key, purpose="source_manifest")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export_ietf_workflow(args.fetch_inventory, args.out)


if __name__ == "__main__":
    main()
