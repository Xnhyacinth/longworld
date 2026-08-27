#!/usr/bin/env python3
"""Attest local Wikipedia/Wikidata workflow sources without fetching them."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from longworld.core.realworkflow import RealWorkflow

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.documentworkflow import (
    build_wikipedia_workflow_manifest,
    load_wikipedia_real_workflow_episode,
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


def export_wikipedia_workflow(
    input_path: Path,
    output_path: Path,
    *,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> RealWorkflow | None:
    """Validate, source-attest, and write one Wikimedia workflow inventory."""
    try:
        raw = _read_regular_file(input_path, MAX_MANIFEST_BYTES)
        input_payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read Wikipedia workflow input: {exc}") from exc
    if not isinstance(input_payload, dict):
        raise ProvenanceError("Wikipedia workflow input must be an object")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError(
            "Wikipedia workflow export requires a source attestation key"
        )
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = build_wikipedia_workflow_manifest(
        input_payload, input_path.parent, generated_at=timestamp
    )
    signed = attach_attestation(manifest, key, purpose="source_manifest")
    if input_payload.get("source_status") == "public_api_export":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".stage",
            delete=False,
        ) as handle:
            staged_path = Path(handle.name)
        try:
            _write_json_atomic(staged_path, signed)
            load_wikipedia_real_workflow_episode(staged_path, attestation_key=key)
            os.replace(staged_path, output_path)
            return load_wikipedia_real_workflow_episode(
                output_path, attestation_key=key
            )
        finally:
            staged_path.unlink(missing_ok=True)
    _write_json_atomic(output_path, signed)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attest local Wikipedia/Wikidata sources without downloading them"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export_wikipedia_workflow(args.input, args.out)


if __name__ == "__main__":
    main()
