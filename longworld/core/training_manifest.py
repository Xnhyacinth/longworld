"""Signed binding from a validated release product to transformed training files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.release_profile import release_profile_sha256

TRAINING_MANIFEST_SCHEMA = "longworld-training-export-v2"
TRAINING_MANIFEST_PURPOSE = "training_export_manifest"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _safe_relative_path(value: object, *, field: str) -> Path:
    raw = str(value or "")
    path = Path(raw)
    if (
        not raw
        or path.is_absolute()
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field} must be a normalized release-root relative path")
    return path


def _reject_symlink_path(path: Path, root: Path, *, field: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{field} is outside release root") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{field} contains a symlink path")


def training_manifest_release_root(
    manifest_path: Path, manifest: dict[str, Any]
) -> Path:
    """Derive the relocatable release root from the manifest's own relative path."""
    relative = _safe_relative_path(
        manifest.get("manifest_path"), field="training manifest path"
    )
    actual = manifest_path.absolute()
    root = actual
    for _part in relative.parts:
        root = root.parent
    expected = root.joinpath(relative)
    if expected != actual:
        raise ValueError("training manifest is not at its bound release path")
    _reject_symlink_path(actual, root, field="training manifest path")
    return root


def resolve_training_manifest_path(
    manifest_path: Path, manifest: dict[str, Any], relative_path: object
) -> Path:
    """Resolve one manifest-bound release path without permitting traversal."""
    root = training_manifest_release_root(manifest_path, manifest)
    relative = _safe_relative_path(relative_path, field="training manifest artifact")
    path = root.joinpath(relative)
    _reject_symlink_path(path, root, field="training manifest artifact")
    return path


def create_training_manifest(
    manifest_path: Path,
    *,
    source_data_dir: Path,
    release_profile_id: str,
    transform_revision: str,
    output_paths: list[Path],
    source_file_sha256: dict[str, str],
    attestation_key: bytes | None,
    upstream_manifest_path: Path | None = None,
    release_root: Path | None = None,
) -> dict[str, Any]:
    """Sign the source quality report and exact bytes of every derived output."""
    if attestation_key is None:
        raise ValueError("report attestation key is required")
    if not release_profile_id or not transform_revision:
        raise ValueError("release profile and transform revision are required")
    manifest_path = manifest_path.absolute()
    source_data_dir = source_data_dir.absolute()
    output_paths = [path.absolute() for path in output_paths]
    if release_root is None:
        release_root = Path(
            os.path.commonpath([manifest_path, source_data_dir, *output_paths])
        )
    release_root = release_root.absolute()
    if release_root.is_symlink():
        raise ValueError("release root cannot be a symlink")
    try:
        manifest_relative = manifest_path.relative_to(release_root).as_posix()
        source_relative = source_data_dir.relative_to(release_root).as_posix()
    except ValueError as error:
        raise ValueError(
            "training manifest inputs must be inside release root"
        ) from error
    _safe_relative_path(manifest_relative, field="training manifest path")
    _safe_relative_path(source_relative, field="source data directory")
    _reject_symlink_path(manifest_path, release_root, field="training manifest path")
    _reject_symlink_path(source_data_dir, release_root, field="source data directory")
    required_source_files = ("quality_report.json", "train.jsonl", "eval.jsonl")
    if set(source_file_sha256) != set(required_source_files):
        raise ValueError("training manifest source file binding is incomplete")
    for name in required_source_files:
        path = source_data_dir / name
        if not path.is_file() or file_sha256(path) != source_file_sha256[name]:
            raise ValueError(f"validated source release changed: {name}")
    output_root = manifest_path.parent
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in output_paths:
        path = raw_path.absolute()
        _reject_symlink_path(path, release_root, field="training output")
        if not path.is_file() or not path.is_relative_to(output_root):
            raise ValueError(
                f"training output is missing or outside export root: {path}"
            )
        relative = path.relative_to(release_root).as_posix()
        if relative in seen or path == manifest_path:
            raise ValueError(f"duplicate or recursive training output: {relative}")
        seen.add(relative)
        entries.append(
            {
                "path": relative,
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    if not entries:
        raise ValueError("training manifest has no outputs")
    payload = {
        "schema_version": TRAINING_MANIFEST_SCHEMA,
        "release_profile_id": release_profile_id,
        "release_profile_sha256": release_profile_sha256(release_profile_id),
        "transform_revision": transform_revision,
        "manifest_path": manifest_relative,
        "source_data_dir": source_relative,
        "source_file_sha256": dict(sorted(source_file_sha256.items())),
        "outputs": sorted(entries, key=lambda item: item["path"]),
    }
    if upstream_manifest_path is not None:
        upstream_manifest_path = upstream_manifest_path.absolute()
        if not upstream_manifest_path.is_file():
            raise ValueError("upstream training manifest is missing")
        _reject_symlink_path(
            upstream_manifest_path, release_root, field="upstream training manifest"
        )
        payload["upstream_manifest"] = {
            "path": upstream_manifest_path.relative_to(release_root).as_posix(),
            "sha256": file_sha256(upstream_manifest_path),
        }
    manifest = attach_attestation(
        payload, attestation_key, purpose=TRAINING_MANIFEST_PURPOSE
    )
    _write_json_atomic(manifest_path, manifest)
    return manifest


def validate_training_manifest(
    manifest_path: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    attestation_key: bytes | None,
) -> dict[str, Any]:
    """Fail closed if the source report or any transformed output changed."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid training manifest: {manifest_path}") from error
    if not isinstance(manifest, dict) or not verify_attestation(
        manifest, attestation_key, purpose=TRAINING_MANIFEST_PURPOSE
    ):
        raise ValueError("training manifest attestation is invalid")
    if (
        manifest.get("schema_version") != TRAINING_MANIFEST_SCHEMA
        or manifest.get("release_profile_id") != expected_release_profile_id
        or manifest.get("release_profile_sha256")
        != release_profile_sha256(expected_release_profile_id)
        or manifest.get("transform_revision") != expected_transform_revision
    ):
        raise ValueError("training manifest release identity is invalid")
    release_root = training_manifest_release_root(manifest_path, manifest)
    upstream = manifest.get("upstream_manifest")
    if upstream is not None:
        if not isinstance(upstream, dict):
            raise TypeError("upstream training manifest binding is malformed")
        upstream_path = resolve_training_manifest_path(
            manifest_path, manifest, upstream.get("path")
        )
        if not upstream_path.is_file() or file_sha256(upstream_path) != upstream.get(
            "sha256"
        ):
            raise ValueError("upstream training manifest digest changed")
    source_dir = resolve_training_manifest_path(
        manifest_path, manifest, manifest.get("source_data_dir")
    )
    source_digests = manifest.get("source_file_sha256")
    if not isinstance(source_digests, dict) or set(source_digests) != {
        "quality_report.json",
        "train.jsonl",
        "eval.jsonl",
    }:
        raise ValueError("source release digests are missing")
    for name, expected_digest in source_digests.items():
        source_path = source_dir / name
        if not source_path.is_file() or file_sha256(source_path) != expected_digest:
            raise ValueError(f"source release digest changed: {name}")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("training manifest outputs are missing")
    seen: set[str] = set()
    for entry in outputs:
        if not isinstance(entry, dict):
            raise TypeError("training manifest output entry is malformed")
        relative = str(entry.get("path") or "")
        path = resolve_training_manifest_path(manifest_path, manifest, relative)
        if (
            not relative
            or relative in seen
            or not path.is_relative_to(release_root)
            or not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            raise ValueError(f"training output digest changed: {relative or '?'}")
        seen.add(relative)
    return manifest
