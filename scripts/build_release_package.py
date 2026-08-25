#!/usr/bin/env python3
"""Build an isolated, relocatable staging directory for a private HF dataset."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.promotion import RELEASE_GATE_PURPOSE
from longworld.core.release_inventory import (
    COMMITTED_NAME,
    PROMOTED_FILES,
    RELEASE_INVENTORY_NAME,
    RELEASE_INVENTORY_PURPOSE,
    RELEASE_INVENTORY_SCHEMA,
    RELEASE_TRUST_MODES,
    TRAINING_OUTPUT_ALLOWLIST,
    create_release_inventory,
    validate_release_inventory,
)
from longworld.core.training_manifest import (
    file_sha256,
    resolve_training_manifest_path,
    training_manifest_release_root,
    validate_training_manifest,
)


def _source_file(root: Path, path: Path, *, field: str) -> Path:
    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{field} is outside source release root") from error
    lowered_parts = [part.lower() for part in relative.parts]
    if any(
        part == "keys" or part.startswith((".env", "candidates", "rejects"))
        for part in lowered_parts
    ):
        raise ValueError(f"{field} is a forbidden private-workflow path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{field} contains a symlink path")
    if not path.is_file():
        raise ValueError(f"{field} is missing or not a regular file")
    return path


def _copy_bound_file(source_root: Path, stage_root: Path, source: Path) -> Path:
    source = _source_file(source_root, source, field="release payload")
    relative = source.relative_to(source_root)
    destination = stage_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _write_commit_marker(stage_root: Path) -> None:
    inventory_path = stage_root / RELEASE_INVENTORY_NAME
    marker = {
        "schema_version": RELEASE_INVENTORY_SCHEMA,
        "inventory_sha256": file_sha256(inventory_path),
    }
    marker_path = stage_root / COMMITTED_NAME
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{COMMITTED_NAME}.", suffix=".tmp", dir=stage_root
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(marker, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, marker_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_release_package(
    *,
    source_release_root: Path,
    destination: Path,
    promoted_dir: Path,
    training_manifest_path: Path,
    data_card_path: Path,
    release_profile_id: str,
    transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
    trust_mode: str = "local_engineering",
) -> dict:
    """Copy only signed/allowlisted artifacts, then atomically commit the package."""
    source_root = source_release_root.absolute()
    destination = destination.absolute()
    if source_root.is_symlink():
        raise ValueError("source release root cannot be a symlink")
    if not source_root.is_dir():
        raise ValueError("source release root is missing")
    if destination.exists() or destination.is_symlink():
        raise ValueError("release package destination already exists")
    promoted_dir = promoted_dir.absolute()
    if not promoted_dir.is_dir() or promoted_dir.is_symlink():
        raise ValueError("promoted directory is missing or a symlink")
    manifest_path = _source_file(
        source_root, training_manifest_path, field="training manifest"
    )
    manifest = validate_training_manifest(
        manifest_path,
        expected_release_profile_id=release_profile_id,
        expected_transform_revision=transform_revision,
        attestation_key=training_attestation_key,
    )
    if training_manifest_release_root(manifest_path, manifest) != source_root:
        raise ValueError("training manifest is bound to another release root")
    manifest_source = resolve_training_manifest_path(
        manifest_path, manifest, manifest["source_data_dir"]
    )
    if manifest_source != promoted_dir:
        raise ValueError("training manifest does not bind the promoted directory")
    output_paths = [
        resolve_training_manifest_path(manifest_path, manifest, entry["path"])
        for entry in manifest["outputs"]
    ]
    rejected_outputs = sorted(
        path.name for path in output_paths if path.name not in TRAINING_OUTPUT_ALLOWLIST
    )
    if rejected_outputs:
        raise ValueError(f"training outputs are not allowlisted: {rejected_outputs}")
    card = _source_file(source_root, data_card_path, field="data card")
    if card.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("data card must be a Markdown file")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        shutil.copyfile(card, temporary / "README.md")
        for name in (*PROMOTED_FILES, "release_gate_pass.json"):
            _copy_bound_file(source_root, temporary, promoted_dir / name)
        staged_manifest = _copy_bound_file(source_root, temporary, manifest_path)
        for output in output_paths:
            _copy_bound_file(source_root, temporary, output)
        inventory = create_release_inventory(
            temporary,
            manifest_path=staged_manifest,
            release_profile_id=release_profile_id,
            transform_revision=transform_revision,
            training_attestation_key=training_attestation_key,
            gate_attestation_key=gate_attestation_key,
            inventory_attestation_key=inventory_attestation_key,
            trust_mode=trust_mode,
        )
        _write_commit_marker(temporary)
        _fsync_directory(temporary)
        validate_release_inventory(
            temporary,
            expected_release_profile_id=release_profile_id,
            expected_transform_revision=transform_revision,
            training_attestation_key=training_attestation_key,
            gate_attestation_key=gate_attestation_key,
            inventory_attestation_key=inventory_attestation_key,
            expected_trust_mode=trust_mode,
        )
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        return inventory
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--promoted-dir", type=Path, required=True)
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--data-card", type=Path, required=True)
    parser.add_argument("--release-profile", required=True)
    parser.add_argument("--expected-transform-revision", required=True)
    parser.add_argument(
        "--trust-mode", choices=RELEASE_TRUST_MODES, default="local_engineering"
    )
    args = parser.parse_args()
    inventory = build_release_package(
        source_release_root=args.release_root,
        destination=args.destination,
        promoted_dir=args.promoted_dir,
        training_manifest_path=args.training_manifest,
        data_card_path=args.data_card,
        release_profile_id=args.release_profile,
        transform_revision=args.expected_transform_revision,
        training_attestation_key=attestation_key_from_env("training_export_manifest"),
        gate_attestation_key=attestation_key_from_env(RELEASE_GATE_PURPOSE),
        inventory_attestation_key=attestation_key_from_env(RELEASE_INVENTORY_PURPOSE),
        trust_mode=args.trust_mode,
    )
    print(json.dumps(inventory, indent=2))


if __name__ == "__main__":
    main()
