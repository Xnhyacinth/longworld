#!/usr/bin/env python3
"""Attest local OpenReview/arXiv workflow sources without fetching them."""

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
from longworld.core.documentworkflow import (
    PAPER_FETCH_INVENTORY_SCHEMA,
    build_paper_workflow_from_fetch_inventory,
    build_paper_workflow_manifest,
)
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


def export_paper_workflow(
    input_path: Path | None,
    output_path: Path,
    *,
    fetch_inventory_path: Path | None = None,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> None:
    """Validate, source-attest, and write one scholarly workflow inventory."""
    if (input_path is None) == (fetch_inventory_path is None):
        raise ProvenanceError("provide exactly one paper workflow input mode")
    selected_path = fetch_inventory_path or input_path
    assert selected_path is not None
    try:
        raw = _read_regular_file(selected_path, MAX_MANIFEST_BYTES)
        input_payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read paper workflow input: {exc}") from exc
    if not isinstance(input_payload, dict):
        raise ProvenanceError("paper workflow input must be an object")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("paper workflow export requires a source attestation key")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    if fetch_inventory_path is not None:
        if input_payload.get("schema_version") != PAPER_FETCH_INVENTORY_SCHEMA:
            raise ProvenanceError("unsupported paper fetch inventory schema")
        manifest = build_paper_workflow_from_fetch_inventory(
            input_payload,
            selected_path.parent,
            generated_at=timestamp,
            fetch_inventory_sha256=hashlib.sha256(raw).hexdigest(),
        )
    else:
        manifest = build_paper_workflow_manifest(
            input_payload, selected_path.parent, generated_at=timestamp
        )
    signed = attach_attestation(manifest, key, purpose="source_manifest")
    _write_json_atomic(output_path, signed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attest local OpenReview/arXiv source files without downloading them"
    )
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", type=Path)
    inputs.add_argument("--fetch-inventory", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export_paper_workflow(
        args.input,
        args.out,
        fetch_inventory_path=args.fetch_inventory,
    )


if __name__ == "__main__":
    main()
