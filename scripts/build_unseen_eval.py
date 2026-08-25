#!/usr/bin/env python3
"""Materialize leakage-safe LongWorld train/eval complements by unseen axis."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.unseen import (
    UNSEEN_AXES,
    UNSEEN_TRUST_MODES,
    build_unseen_splits,
    load_unseen_split_manifest,
)


def _read_jsonl(paths: list[Path]) -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    source_file_sha256: dict[str, str] = {}
    for path in paths:
        if path.name in source_file_sha256:
            raise ValueError(f"duplicate unseen input basename: {path.name}")
        raw = path.read_bytes()
        source_file_sha256[path.name] = hashlib.sha256(raw).hexdigest()
        rows.extend(
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        )
    return rows, source_file_sha256


def _read_release_manifest(path: Path | None) -> dict | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("release manifest must be an object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--eval-ratio", type=float, default=0.2)
    parser.add_argument("--split-seed", type=int, default=0)
    parser.add_argument("--axes", nargs="+", choices=UNSEEN_AXES, default=UNSEEN_AXES)
    parser.add_argument("--trust-mode", choices=UNSEEN_TRUST_MODES, required=True)
    parser.add_argument("--release-manifest", type=Path)
    args = parser.parse_args()

    if args.trust_mode == "production" and args.release_manifest is None:
        parser.error("production trust mode requires --release-manifest")
    if args.trust_mode == "local_engineering" and args.release_manifest is not None:
        parser.error("local engineering mode cannot carry --release-manifest")

    rows, source_file_sha256 = _read_jsonl(args.input)
    release_manifest = _read_release_manifest(args.release_manifest)
    production = args.trust_mode == "production"
    result = build_unseen_splits(
        rows,
        args.output_dir,
        axes=args.axes,
        eval_ratio=args.eval_ratio,
        split_seed=args.split_seed,
        trust_mode=args.trust_mode,
        source_file_sha256=source_file_sha256 if production else None,
        release_manifest=release_manifest,
        promotion_attestation_key=(
            attestation_key_from_env("sft_row") if production else None
        ),
        release_attestation_key=(
            attestation_key_from_env("release_gate_pass") if production else None
        ),
        manifest_attestation_key=(
            attestation_key_from_env("training_export_manifest") if production else None
        ),
    )
    load_unseen_split_manifest(
        args.output_dir / "manifest.json",
        trust_mode=args.trust_mode,
        release_manifest=release_manifest,
        release_attestation_key=(
            attestation_key_from_env("release_gate_pass") if production else None
        ),
        manifest_attestation_key=(
            attestation_key_from_env("training_export_manifest") if production else None
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
