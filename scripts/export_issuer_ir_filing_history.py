#!/usr/bin/env python3
"""Attest one downloaded issuer-IR annual filing history; never fetch sources."""

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
from longworld.core.issuerfilingworkflow import build_issuer_ir_filing_manifest
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _read_regular_file,
)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def export_issuer_ir_filing_history(
    input_path: Path,
    output_path: Path,
    *,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> None:
    """Validate, source-attest, and atomically write one issuer-IR manifest."""
    try:
        raw = _read_regular_file(input_path, MAX_MANIFEST_BYTES)
        inventory = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read issuer IR inventory: {exc}") from exc
    if not isinstance(inventory, dict):
        raise ProvenanceError("issuer IR inventory must be an object")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("issuer IR export requires a source attestation key")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = build_issuer_ir_filing_manifest(
        inventory, input_path.parent, generated_at=timestamp
    )
    _write_json_atomic(
        output_path, attach_attestation(manifest, key, purpose="source_manifest")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export_issuer_ir_filing_history(args.input, args.out)


if __name__ == "__main__":
    main()
