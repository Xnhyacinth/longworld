#!/usr/bin/env python3
"""Validate a signed training transform and its complete source release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.training_manifest import (
    resolve_training_manifest_path,
    validate_deterministic_training_transform,
    validate_training_manifest,
)
from scripts.quality_gate import load_release_product

_COPY_CHUNK_BYTES = 1024 * 1024


def _snapshot_outputs(
    manifest_path: Path, manifest: dict
) -> dict[Path, tuple[Path, int, str]]:
    manifest_relative = Path(str(manifest.get("manifest_path") or ""))
    if (
        manifest_relative.is_absolute()
        or "\\" in str(manifest_relative)
        or any(part in {"", ".", ".."} for part in manifest_relative.parts)
    ):
        raise ValueError("training manifest path is invalid for snapshot")
    output_root = manifest_relative.parent
    outputs: dict[Path, tuple[Path, int, str]] = {}
    for entry in manifest["outputs"]:
        raw_relative = str(entry["path"])
        relative = Path(raw_relative)
        if (
            relative.is_absolute()
            or "\\" in raw_relative
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("training output path is invalid for snapshot")
        try:
            snapshot_relative = relative.relative_to(output_root)
        except ValueError as error:
            raise ValueError(
                "training output is outside training output root"
            ) from error
        if not snapshot_relative.parts or snapshot_relative in outputs:
            raise ValueError("training snapshot output path is duplicated")
        expected_bytes = entry.get("bytes")
        expected_sha256 = str(entry.get("sha256") or "")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or len(expected_sha256) != 64
        ):
            raise ValueError("training snapshot output binding is malformed")
        outputs[snapshot_relative] = (
            resolve_training_manifest_path(manifest_path, manifest, raw_relative),
            expected_bytes,
            expected_sha256,
        )
    if not outputs:
        raise ValueError("training snapshot has no outputs")
    return outputs


def _stream_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validate_existing_snapshot(
    path: Path, outputs: dict[Path, tuple[Path, int, str]]
) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("validated training snapshot path is invalid")
    if stat.S_IMODE(path.stat().st_mode) != 0o500:
        raise ValueError("validated training snapshot directory is not read-only")
    observed: set[Path] = set()
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if item.is_symlink():
            raise ValueError("validated training snapshot contains a symlink")
        if item.is_dir():
            if stat.S_IMODE(item.stat().st_mode) != 0o500:
                raise ValueError(
                    "validated training snapshot directory is not read-only"
                )
            continue
        if not item.is_file() or stat.S_IMODE(item.stat().st_mode) != 0o400:
            raise ValueError("validated training snapshot file is not read-only")
        binding = outputs.get(relative)
        if binding is None:
            raise ValueError("validated training snapshot contents are unexpected")
        _source, expected_bytes, expected_sha256 = binding
        if _stream_digest(item) != (expected_bytes, expected_sha256):
            raise ValueError("validated training snapshot bytes changed")
        observed.add(relative)
    if observed != set(outputs):
        raise ValueError("validated training snapshot contents are incomplete")


def _stream_copy_verified(
    source: Path,
    destination: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(_COPY_CHUNK_BYTES):
            writer.write(chunk)
            size += len(chunk)
            digest.update(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if size != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ValueError(f"training output changed before snapshot: {source}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_staging_snapshot(path: Path) -> None:
    if not path.exists():
        return
    os.chmod(path, 0o700)
    for directory, _subdirectories, files in os.walk(path):
        os.chmod(directory, 0o700)
        for name in files:
            item = Path(directory) / name
            if not item.is_symlink():
                os.chmod(item, 0o600)
    shutil.rmtree(path)


def _snapshot_ancestor_is_trusted(info: os.stat_result) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    return info.st_uid in {0, os.geteuid()} and not (
        mode & 0o022 and not mode & stat.S_ISVTX
    )


def _validate_snapshot_parent(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or not ancestor.is_dir():
            raise ValueError("training snapshot parent path is unsafe")
        if not _snapshot_ancestor_is_trusted(ancestor.stat()):
            raise ValueError("training snapshot parent is replaceable by another user")


def materialize_verified_snapshot(
    manifest_path: Path, manifest: dict, snapshot_root: Path
) -> Path:
    """Copy reverified output bytes into a private, read-only content address."""
    snapshot_root = snapshot_root.absolute()
    _validate_snapshot_parent(snapshot_root.parent)
    if snapshot_root.is_symlink():
        raise ValueError("training snapshot root contains a symlink")
    snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_stat = snapshot_root.stat()
    if root_stat.st_uid != os.geteuid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise ValueError("training snapshot root must be owned and private")
    outputs = _snapshot_outputs(manifest_path, manifest)
    snapshot_id = hashlib.sha256(
        json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    destination = snapshot_root / snapshot_id
    if destination.exists():
        _validate_existing_snapshot(destination, outputs)
        return destination

    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=snapshot_root))
    try:
        for relative, (source, expected_bytes, expected_sha256) in outputs.items():
            output = staging / relative
            _stream_copy_verified(
                source,
                output,
                expected_bytes=expected_bytes,
                expected_sha256=expected_sha256,
            )
        for relative in outputs:
            output = staging / relative
            os.chmod(output, 0o400)
        directories = sorted(
            (item for item in staging.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            os.chmod(directory, 0o500)
        os.chmod(staging, 0o500)
        _fsync_directory(staging)
        try:
            staging.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            _remove_staging_snapshot(staging)
            _validate_existing_snapshot(destination, outputs)
        _fsync_directory(snapshot_root)
        return destination
    except BaseException:
        _remove_staging_snapshot(staging)
        raise


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
    parser.add_argument("--snapshot-root", type=Path)
    args = parser.parse_args()
    manifest = validate_training_manifest(
        args.manifest,
        expected_release_profile_id=args.release_profile,
        expected_transform_revision=args.expected_transform_revision,
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
    validate_deterministic_training_transform(args.manifest, manifest)
    outputs = {str(entry["path"]) for entry in manifest["outputs"]}
    by_name: dict[str, list[str]] = {}
    for output in outputs:
        by_name.setdefault(Path(output).name, []).append(output)

    def bound_output(requested: str) -> str | None:
        if requested in outputs:
            return requested
        matches = by_name.get(requested, [])
        if len(matches) > 1:
            raise SystemExit(f"training manifest has ambiguous output name {requested}")
        return matches[0] if matches else None

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
    snapshot = (
        materialize_verified_snapshot(args.manifest, manifest, args.snapshot_root)
        if args.snapshot_root is not None
        else None
    )
    print(
        json.dumps(
            {
                "ok": True,
                "release_profile_id": args.release_profile,
                "source_rows": len(product.train_rows) + len(product.eval_rows),
                "outputs": len(outputs),
                "snapshot_dir": str(snapshot) if snapshot is not None else None,
            }
        )
    )


if __name__ == "__main__":
    main()
