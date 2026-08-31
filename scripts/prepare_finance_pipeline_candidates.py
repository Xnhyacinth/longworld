#!/usr/bin/env python3
"""Prepare signed finance candidates for the shared dense-ranking stage."""

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

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.financehistory import (
    FINANCE_PIPELINE_CANDIDATE_SCHEMA,
    audit_finance_pipeline_candidate,
    build_finance_pipeline_candidate,
)
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE, candidate_sha256
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("finance candidate input is missing or not a regular file")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
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
        raise ValueError("finance candidate input is empty")
    return rows


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def prepare(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """Adapt, finance-replay, attest, and atomically write ranking candidates."""
    key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if key is None:
        raise ValueError("candidate-row attestation key is required")
    input_rows = _read_jsonl(input_path)
    unsigned = [build_finance_pipeline_candidate(row) for row in input_rows]
    for row in unsigned:
        row["tokenizer_asset_manifest_sha256"] = (
            resolved_tokenizer_asset_manifest_sha256(
                str(row["tokenizer_model_id"]), str(row["tokenizer_revision"])
            )
        )
    audits = [audit_finance_pipeline_candidate(row) for row in unsigned]
    if not all(audit and all(audit.values()) for audit in audits):
        raise ValueError("finance pipeline candidate audit failed")
    candidates = [
        attach_attestation(row, key, purpose=CANDIDATE_ATTESTATION_PURPOSE)
        for row in unsigned
    ]
    candidate_bytes = b"\n".join(_canonical_bytes(row) for row in candidates) + b"\n"
    manifest = {
        "schema_version": "longworld.finance-pipeline-candidate-manifest.v1",
        "candidate_schema_version": FINANCE_PIPELINE_CANDIDATE_SCHEMA,
        "data_stage": "candidate",
        "pipeline_stage": "dense_ranking_ready",
        "world_ids": sorted({str(row["world_id"]) for row in candidates}),
        "rows": len(candidates),
        "by_length": {
            bucket: sum(row.get("length_bucket") == bucket for row in candidates)
            for bucket in ("16k", "32k", "64k")
        },
        "context_tokens": sum(
            int(row["tokenizer_context_tokens"]) for row in candidates
        ),
        "candidate_sha256s": [candidate_sha256(row) for row in candidates],
        "serialized_candidates_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "finance_strict_replay_green": True,
        "dense_ranking_ready": True,
        "generic_strict_replay_ready": False,
        "generic_promotion_ready": False,
        "shared_interface_gap": (
            "promotion candidate verification and _reconstruct_candidate need a "
            "registered finance replay adapter plus a producer-attested finance "
            "source sidecar"
        ),
        "train_ready": False,
        "promoted": False,
    }
    _atomic_write(output_dir / "candidates.jsonl", candidate_bytes)
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(manifest) + b"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.input, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
