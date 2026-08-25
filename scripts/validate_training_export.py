#!/usr/bin/env python3
"""Validate a signed training transform and its complete source release."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.training_manifest import (
    resolve_training_manifest_path,
    validate_training_manifest,
)
from scripts.quality_gate import load_release_product


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-profile", required=True)
    parser.add_argument("--expected-transform-revision", required=True)
    parser.add_argument("--required-output", action="append", default=[])
    parser.add_argument("--expected-output-path", type=Path)
    parser.add_argument("--dataset-info-key")
    parser.add_argument("--dataset-file")
    parser.add_argument("--weighted-dataset-index")
    args = parser.parse_args()
    manifest = validate_training_manifest(
        args.manifest,
        expected_release_profile_id=args.release_profile,
        expected_transform_revision=args.expected_transform_revision,
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
    outputs = {str(entry["path"]) for entry in manifest["outputs"]}
    by_name: dict[str, str] = {}
    for output in outputs:
        name = Path(output).name
        if name in by_name:
            raise SystemExit(f"training manifest has ambiguous output name {name}")
        by_name[name] = output

    def bound_output(requested: str) -> str | None:
        return requested if requested in outputs else by_name.get(requested)

    missing_outputs = sorted(
        requested
        for requested in set(args.required_output)
        if bound_output(requested) is None
    )
    if missing_outputs:
        raise SystemExit(f"training manifest does not bind {missing_outputs}")
    if args.expected_output_path is not None:
        expected = args.expected_output_path.resolve()
        bound_paths = {
            resolve_training_manifest_path(args.manifest, manifest, output).absolute()
            for output in outputs
        }
        if expected not in bound_paths:
            raise SystemExit(f"training manifest does not bind actual path {expected}")
    if args.dataset_info_key or args.dataset_file:
        if not args.dataset_info_key or not args.dataset_file:
            raise SystemExit("dataset info key and file must be provided together")
        dataset_info_output = bound_output("dataset_info.json")
        if dataset_info_output is None:
            raise SystemExit("training manifest does not bind dataset_info.json")
        dataset_info = json.loads(
            resolve_training_manifest_path(
                args.manifest, manifest, dataset_info_output
            ).read_text(encoding="utf-8")
        )
        entry = dataset_info.get(args.dataset_info_key)
        if not isinstance(entry, dict) or entry.get("file_name") != args.dataset_file:
            raise SystemExit(
                "dataset_info.json does not bind the expected dataset file"
            )
    if args.weighted_dataset_index:
        index_output = bound_output(args.weighted_dataset_index)
        if index_output is None:
            raise SystemExit("training manifest does not bind weighted dataset index")
        index_path = resolve_training_manifest_path(
            args.manifest, manifest, index_output
        )
        index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or not index:
            raise SystemExit("weighted dataset index is empty or malformed")
        bound_paths = {
            resolve_training_manifest_path(args.manifest, manifest, output)
            for output in outputs
        }
        b5w_payloads = {
            path
            for path in bound_paths
            if path.name == "B5w.json"
            or (path.name.startswith("B5w.weight") and path.name.endswith(".json"))
        }
        referenced_payloads: set[Path] = set()
        seen: set[str] = set()
        logical_seen: set[str] = set()
        has_upsampling = False
        for entry in index.values():
            if not isinstance(entry, dict):
                raise SystemExit("weighted dataset entry is malformed")
            weight = entry.get("weight")
            raw_path = str(entry.get("path") or "")
            path = Path(raw_path)
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(float(weight))
                or not float(weight).is_integer()
                or not 1 <= int(weight) <= 3
                or entry.get("source") != "local"
                or entry.get("converter") != "sharegpt"
            ):
                raise SystemExit("weighted dataset entry has invalid sampler policy")
            normalized_weight = int(weight)
            has_upsampling = has_upsampling or normalized_weight > 1
            if (
                not raw_path
                or path.is_absolute()
                or "\\" in raw_path
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise SystemExit("weighted dataset path must be index-relative")
            resolved = index_path.parent.joinpath(path).absolute()
            if resolved not in bound_paths or resolved.is_symlink():
                raise SystemExit(
                    f"weighted dataset shard is not manifest-bound: {path}"
                )
            if resolved in referenced_payloads:
                raise SystemExit("weighted dataset index repeats a shard")
            if path.name != "B5w.json" and path.name != (
                f"B5w.weight{normalized_weight}.json"
            ):
                raise SystemExit("weighted dataset shard name does not match weight")
            referenced_payloads.add(resolved)
            rows = json.loads(resolved.read_text(encoding="utf-8"))
            if not isinstance(rows, list) or not rows:
                raise SystemExit("weighted dataset shard is malformed")
            for row in rows:
                if not isinstance(row, dict):
                    raise SystemExit("weighted dataset row is malformed")
                if row.get("sample_weight") != normalized_weight:
                    raise SystemExit("weighted dataset row weight does not match shard")
                digest = json.dumps(row, sort_keys=True, separators=(",", ":"))
                if digest in seen:
                    raise SystemExit("weighted dataset shards duplicate a JSON row")
                seen.add(digest)
                logical_row = dict(row)
                logical_row.pop("sample_weight", None)
                logical_digest = json.dumps(
                    logical_row, sort_keys=True, separators=(",", ":")
                )
                if logical_digest in logical_seen:
                    raise SystemExit("weighted dataset shards duplicate a logical row")
                logical_seen.add(logical_digest)
        if referenced_payloads != b5w_payloads:
            raise SystemExit(
                "weighted dataset index must cover every manifest-bound B5w payload"
            )
        if not has_upsampling:
            raise SystemExit("weighted dataset index has no weight greater than one")
    product = load_release_product(
        resolve_training_manifest_path(
            args.manifest, manifest, manifest["source_data_dir"]
        ),
        args.release_profile,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "release_profile_id": args.release_profile,
                "source_rows": len(product.train_rows) + len(product.eval_rows),
                "outputs": len(outputs),
            }
        )
    )


if __name__ == "__main__":
    main()
