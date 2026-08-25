#!/usr/bin/env python3
"""Attest already-downloaded SEC filing sources; this script never fetches SEC."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.filingworkflow import build_sec_filing_manifest
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _read_regular_file,
)


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


def export_sec_workflow(
    input_path: Path,
    output_path: Path,
    *,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> None:
    """Validate, source-attest, and write one local SEC filing manifest."""
    try:
        raw = _read_regular_file(input_path, MAX_MANIFEST_BYTES)
        input_payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read SEC filing input: {exc}") from exc
    if not isinstance(input_payload, dict):
        raise ProvenanceError("SEC filing input must be an object")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("SEC filing export requires a source attestation key")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = build_sec_filing_manifest(
        input_payload, input_path.parent, generated_at=timestamp
    )
    signed = attach_attestation(manifest, key, purpose="source_manifest")
    _write_json_atomic(output_path, signed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attest local SEC JSON/text without downloading or publishing it"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export_sec_workflow(args.input, args.out)


if __name__ == "__main__":
    main()
