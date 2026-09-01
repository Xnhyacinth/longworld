"""Relocatable, fail-closed inventory for a committed private dataset release."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    attach_attestation,
    verify_attestation,
)
from longworld.core.production_trust import (
    require_independent_package_approval,
    verify_embedded_production_approval_from_env,
    verify_embedded_production_package_approval_from_env,
)
from longworld.core.promotion import (
    RELEASE_GATE_PURPOSE,
    RELEASE_GATE_SCHEMA,
    release_gate_revision_supported,
)
from longworld.core.release_profile import (
    issuable_release_profile,
    release_profile,
    release_profile_sha256,
)
from longworld.core.training_manifest import (
    file_sha256,
    resolve_training_manifest_path,
    training_manifest_release_root,
    validate_training_manifest,
)

RELEASE_INVENTORY_SCHEMA = "longworld-private-dataset-release-v1"
PRODUCTION_RELEASE_PREFLIGHT_SCHEMA = "longworld-production-release-preflight-v1"
RELEASE_INVENTORY_NAME = "release_inventory.json"
PRODUCTION_PACKAGE_READY_PROFILE_IDS: frozenset[str] = frozenset()
COMMITTED_NAME = "COMMITTED"
RELEASE_INVENTORY_PURPOSE = "release_inventory"
RELEASE_TRUST_MODES = ("local_engineering", "production")
PROMOTED_FILES = ("quality_report.json", "train.jsonl", "eval.jsonl")
TRAINING_OUTPUT_ALLOWLIST = frozenset(
    {
        "B1.json",
        "B1.meta.json",
        "B3.json",
        "B3.meta.json",
        "B5.json",
        "B5.meta.json",
        "B5w.json",
        "B5w.meta.json",
        "B5w.datasets.yaml",
        "B5w.weight1.json",
        "B5w.weight2.json",
        "B5w.weight3.json",
        "dataset_info.json",
        "export_summary.json",
    }
)


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def release_commit_marker_bytes(inventory_path: Path) -> bytes:
    """Return the immutable marker bytes bound by package approval."""
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    marker = {
        "schema_version": RELEASE_INVENTORY_SCHEMA,
        "inventory_sha256": file_sha256(inventory_path),
    }
    if isinstance(inventory, dict) and inventory.get("trust_mode") == "production":
        marker["production_eligible"] = True
    return (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _relative(root: Path, path: Path, *, field: str) -> str:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise ValueError(f"{field} is outside release root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{field} is not release-root relative")
    return relative.as_posix()


def _expected_directories(paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for value in paths:
        parent = Path(value).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _package_member_paths(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for path in root.rglob("*"):
        status = path.stat(follow_symlinks=False)
        relative = path.relative_to(root).as_posix()
        if stat.S_ISREG(status.st_mode):
            if status.st_nlink != 1:
                raise ValueError(f"package member has multiple hard links: {relative}")
            files.add(relative)
        elif stat.S_ISDIR(status.st_mode):
            directories.add(relative)
        else:
            raise ValueError(f"non-regular package member: {relative}")
    return files, directories


def _regular_file(path: Path, *, field: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{field} contains a symlink path")
    try:
        status = path.stat(follow_symlinks=False)
    except FileNotFoundError as error:
        raise ValueError(f"{field} is missing or not a regular file") from error
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{field} is missing or not a regular file")
    if status.st_nlink != 1:
        raise ValueError(f"{field} has multiple hard links")


def _release_member_snapshot(root: Path) -> dict[str, tuple[Any, ...]]:
    """Bind child identities and bytes across one public eligibility check."""
    if root.is_symlink():
        raise ValueError("release package root cannot be a symlink")
    root_status = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(root_status.st_mode):
        raise ValueError("release package root is missing or not a directory")
    snapshot: dict[str, tuple[Any, ...]] = {
        ".": (
            root_status.st_dev,
            root_status.st_ino,
            root_status.st_mode,
            root_status.st_nlink,
            root_status.st_size,
            root_status.st_mtime_ns,
            root_status.st_ctime_ns,
            "",
        )
    }
    member_paths = sorted(root.rglob("*"))
    member_names = tuple(path.relative_to(root).as_posix() for path in member_paths)
    for path in member_paths:
        before = path.stat(follow_symlinks=False)
        if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
            raise ValueError(
                f"non-regular package member: {path.relative_to(root).as_posix()}"
            )
        if stat.S_ISREG(before.st_mode) and before.st_nlink != 1:
            raise ValueError(
                f"package member has multiple hard links: "
                f"{path.relative_to(root).as_posix()}"
            )
        digest = ""
        if stat.S_ISREG(before.st_mode):
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            after = path.stat(follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("release package members changed during validation")
            before = after
        snapshot[path.relative_to(root).as_posix()] = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            digest,
        )
    final_paths = sorted(root.rglob("*"))
    final_names = tuple(path.relative_to(root).as_posix() for path in final_paths)
    if final_names != member_names:
        raise ValueError("release package members changed during validation")
    for path in final_paths:
        try:
            final = path.stat(follow_symlinks=False)
        except FileNotFoundError as error:
            raise ValueError(
                "release package members changed during validation"
            ) from error
        expected = snapshot[path.relative_to(root).as_posix()]
        if (
            final.st_dev,
            final.st_ino,
            final.st_mode,
            final.st_nlink,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        ) != expected[:7]:
            raise ValueError("release package members changed during validation")
    final_root = root.stat(follow_symlinks=False)
    if (
        final_root.st_dev,
        final_root.st_ino,
        final_root.st_mode,
        final_root.st_nlink,
        final_root.st_size,
        final_root.st_mtime_ns,
        final_root.st_ctime_ns,
    ) != snapshot["."][:7]:
        raise ValueError("release package members changed during validation")
    return snapshot


def _entry(root: Path, path: Path, *, role: str) -> dict[str, Any]:
    _regular_file(path, field=role)
    return {
        "path": _relative(root, path, field=role),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "role": role,
    }


def _load_gate_receipt(
    path: Path,
    *,
    release_profile_id: str,
    source_digests: dict[str, str],
    attestation_key: bytes | None,
    trust_mode: str,
) -> dict[str, Any]:
    _regular_file(path, field="release gate receipt")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("release gate receipt is malformed") from error
    if (
        not isinstance(receipt, dict)
        or not verify_attestation(
            receipt, attestation_key, purpose=RELEASE_GATE_PURPOSE
        )
        or receipt.get("schema_version") != RELEASE_GATE_SCHEMA
        or not release_gate_revision_supported(
            release_profile_id, receipt.get("gate_revision")
        )
        or receipt.get("release_profile_id") != release_profile_id
        or receipt.get("release_profile_sha256")
        != release_profile_sha256(release_profile_id)
        or receipt.get("source_file_sha256") != source_digests
        or receipt.get("ok") is not True
        or receipt.get("errors") != []
    ):
        raise ValueError("release gate receipt does not bind a passing source release")
    if trust_mode == "production":
        try:
            verify_embedded_production_approval_from_env(
                receipt.get("production_approval"),
                release_profile_id=release_profile_id,
                release_profile_sha256=release_profile_sha256(release_profile_id),
                source_file_sha256=source_digests,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("release gate production approval is invalid") from error
    return receipt


def _index_relative_file(index_path: Path, value: object, *, field: str) -> Path:
    raw = str(value or "")
    relative = Path(raw)
    if (
        not raw
        or relative.is_absolute()
        or "\\" in raw
        or len(relative.parts) != 1
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{field} must be an index-relative filename")
    return index_path.parent / relative


def _validate_training_references(
    manifest_path: Path, manifest: dict[str, Any]
) -> None:
    outputs = {
        resolve_training_manifest_path(manifest_path, manifest, entry["path"])
        for entry in manifest["outputs"]
    }
    by_name = {path.name: path for path in outputs}
    if len(by_name) != len(outputs):
        raise ValueError("training manifest contains ambiguous output names")
    b5w_payloads = {
        path
        for name, path in by_name.items()
        if name == "B5w.json"
        or name in {f"B5w.weight{weight}.json" for weight in range(1, 4)}
    }
    weighted_index = by_name.get("B5w.datasets.yaml")
    if weighted_index is None:
        if b5w_payloads:
            raise ValueError(
                "weighted dataset index does not exactly cover B5w payloads"
            )
    else:
        index = yaml.safe_load(weighted_index.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or not index:
            raise ValueError("weighted dataset index is empty or malformed")
        referenced_payloads: set[Path] = set()
        for entry in index.values():
            if not isinstance(entry, dict):
                raise TypeError("weighted dataset index entry is malformed")
            if entry.get("source") != "local":
                raise ValueError("weighted dataset source must be local")
            if entry.get("converter") != "sharegpt":
                raise ValueError("weighted dataset converter must be sharegpt")
            weight = entry.get("weight")
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(weight)
                or weight not in {1, 2, 3}
            ):
                raise ValueError(
                    "weighted dataset weight must be a finite number from 1 to 3"
                )
            referenced = _index_relative_file(
                weighted_index, entry.get("path"), field="weighted dataset path"
            )
            if referenced not in outputs or referenced.is_symlink():
                raise ValueError("weighted dataset path is not manifest-bound")
            if referenced in referenced_payloads:
                raise ValueError("duplicate weighted dataset path")
            referenced_payloads.add(referenced)
            for shard_weight in range(1, 4):
                if referenced.name == f"B5w.weight{shard_weight}.json":
                    if weight != shard_weight:
                        raise ValueError(
                            "weighted dataset shard name does not match weight"
                        )
                    break
        if referenced_payloads != b5w_payloads:
            raise ValueError(
                "weighted dataset index does not exactly cover B5w payloads"
            )
    dataset_info_path = by_name.get("dataset_info.json")
    if dataset_info_path is not None:
        try:
            dataset_info = json.loads(dataset_info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("dataset_info.json is malformed") from error
        if not isinstance(dataset_info, dict):
            raise TypeError("dataset_info.json must contain an object")
        for entry in dataset_info.values():
            if not isinstance(entry, dict):
                raise TypeError("dataset_info.json entry is malformed")
            referenced = _index_relative_file(
                dataset_info_path,
                entry.get("file_name"),
                field="dataset_info file_name",
            )
            if referenced not in outputs or referenced.is_symlink():
                raise ValueError("dataset_info file is not manifest-bound")


def _bound_files(
    root: Path,
    *,
    manifest_path: Path,
    release_profile_id: str,
    transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    trust_mode: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    manifest = validate_training_manifest(
        manifest_path,
        expected_release_profile_id=release_profile_id,
        expected_transform_revision=transform_revision,
        attestation_key=training_attestation_key,
    )
    _validate_training_references(manifest_path, manifest)
    if training_manifest_release_root(manifest_path, manifest) != root.absolute():
        raise ValueError("training manifest release root does not match package root")
    source_dir = resolve_training_manifest_path(
        manifest_path, manifest, manifest["source_data_dir"]
    )
    source_digests = manifest["source_file_sha256"]
    gate_path = source_dir / "release_gate_pass.json"
    gate_receipt = _load_gate_receipt(
        gate_path,
        release_profile_id=release_profile_id,
        source_digests=source_digests,
        attestation_key=gate_attestation_key,
        trust_mode=trust_mode,
    )
    entries = [
        _entry(root, root / "README.md", role="data_card"),
        *(
            _entry(root, source_dir / name, role="promoted_data")
            for name in PROMOTED_FILES
        ),
        _entry(root, gate_path, role="release_gate_pass"),
        _entry(root, manifest_path, role="training_manifest"),
    ]
    for output in manifest["outputs"]:
        path = resolve_training_manifest_path(manifest_path, manifest, output["path"])
        if (
            path.parent != manifest_path.parent
            or path.name not in TRAINING_OUTPUT_ALLOWLIST
        ):
            raise ValueError(f"training output is not allowlisted: {path.name}")
        entries.append(_entry(root, path, role="training_output"))
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("release inventory contains duplicate paths")
    return manifest, sorted(entries, key=lambda entry: entry["path"]), gate_receipt


def _inventory_payload(
    *,
    release_profile_id: str,
    transform_revision: str,
    trust_mode: str,
    manifest: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": RELEASE_INVENTORY_SCHEMA,
        "release_profile_id": release_profile_id,
        "release_profile_sha256": release_profile_sha256(release_profile_id),
        "transform_revision": transform_revision,
        "trust_mode": trust_mode,
        "production_eligible": False,
        "release_gate_pass": next(
            entry for entry in entries if entry["role"] == "release_gate_pass"
        ),
        "training_manifest": next(
            entry for entry in entries if entry["role"] == "training_manifest"
        ),
        "training_manifest_attestation": manifest["attestation"],
        "files": entries,
    }


def _attest_inventory(
    payload: dict[str, Any], *, trust_mode: str, attestation_key: bytes | None
) -> dict[str, Any]:
    if trust_mode not in RELEASE_TRUST_MODES:
        raise ValueError(f"unsupported release trust mode: {trust_mode}")
    if attestation_key is None:
        raise ValueError("release inventory report attestation key is required")
    inventory = attach_attestation(
        payload, attestation_key, purpose=RELEASE_INVENTORY_PURPOSE
    )
    attestation = inventory.get("attestation")
    if not isinstance(attestation, dict) or not verify_attestation(
        inventory, attestation_key, purpose=RELEASE_INVENTORY_PURPOSE
    ):
        raise ValueError("release inventory attestation is invalid")
    if trust_mode == "production":
        if (
            attestation.get("scheme") != ATTESTATION_V2_SCHEME
            or attestation.get("role") != "report"
            or attestation.get("environment") != "production"
        ):
            raise ValueError("production release inventory requires report identity")
    elif attestation.get("environment") == "production":
        raise ValueError("local engineering inventory cannot use production identity")
    return inventory


def create_release_inventory(
    release_root: Path,
    *,
    manifest_path: Path,
    release_profile_id: str,
    transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
    trust_mode: str = "local_engineering",
) -> dict[str, Any]:
    """Create the inventory only after all allowlisted payload files exist."""
    if trust_mode not in RELEASE_TRUST_MODES:
        raise ValueError(f"unsupported release trust mode: {trust_mode}")
    if (
        trust_mode == "production"
        and release_profile(release_profile_id).environment != "production"
    ):
        raise ValueError("production inventory requires a production release profile")
    root = release_root.absolute()
    manifest, entries, _gate_receipt = _bound_files(
        root,
        manifest_path=manifest_path.absolute(),
        release_profile_id=release_profile_id,
        transform_revision=transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        trust_mode=trust_mode,
    )
    if trust_mode == "production":
        production_package_ready_profile(release_profile_id)
    expected_files = {entry["path"] for entry in entries}
    actual_files, actual_directories = _package_member_paths(root)
    if actual_files != expected_files:
        raise ValueError(
            f"unexpected release package files: {sorted(actual_files - expected_files)}"
        )
    expected_directories = _expected_directories(expected_files)
    if actual_directories != expected_directories:
        raise ValueError(
            "unexpected release package directories: "
            f"{sorted(actual_directories - expected_directories)}"
        )
    payload = _inventory_payload(
        release_profile_id=release_profile_id,
        transform_revision=transform_revision,
        trust_mode=trust_mode,
        manifest=manifest,
        entries=entries,
    )
    inventory = _attest_inventory(
        payload,
        trust_mode=trust_mode,
        attestation_key=inventory_attestation_key,
    )
    _write_json_exclusive(root / RELEASE_INVENTORY_NAME, inventory)
    return inventory


def _validate_release_inventory_contents(
    release_root: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
    expected_trust_mode: str,
    committed: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if expected_trust_mode not in RELEASE_TRUST_MODES:
        raise ValueError(f"unsupported release trust mode: {expected_trust_mode}")
    if (
        expected_trust_mode == "production"
        and release_profile(expected_release_profile_id).environment != "production"
    ):
        raise ValueError("production inventory requires a production release profile")
    root = release_root.absolute()
    if root.is_symlink():
        raise ValueError("release package root cannot be a symlink")
    inventory_path = root / RELEASE_INVENTORY_NAME
    marker_path = root / COMMITTED_NAME
    _regular_file(inventory_path, field="release inventory")
    if committed:
        _regular_file(marker_path, field="commit marker")
    elif marker_path.exists() or marker_path.is_symlink():
        raise ValueError("staged release package is already committed")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        marker = (
            json.loads(marker_path.read_text(encoding="utf-8")) if committed else None
        )
    except json.JSONDecodeError as error:
        raise ValueError("release inventory or commit marker is malformed") from error
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != RELEASE_INVENTORY_SCHEMA
        or inventory.get("release_profile_id") != expected_release_profile_id
        or inventory.get("release_profile_sha256")
        != release_profile_sha256(expected_release_profile_id)
        or inventory.get("transform_revision") != expected_transform_revision
        or inventory.get("trust_mode") != expected_trust_mode
        or inventory.get("production_eligible") is not False
        or not verify_attestation(
            inventory,
            inventory_attestation_key,
            purpose=RELEASE_INVENTORY_PURPOSE,
        )
    ):
        raise ValueError("release inventory identity or commit marker is invalid")
    manifest_binding = inventory.get("training_manifest")
    if not isinstance(manifest_binding, dict):
        raise TypeError("release inventory training manifest binding is missing")
    manifest_path = root / str(manifest_binding.get("path") or "")
    manifest, expected_entries, gate_receipt = _bound_files(
        root,
        manifest_path=manifest_path,
        release_profile_id=expected_release_profile_id,
        transform_revision=expected_transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        trust_mode=expected_trust_mode,
    )
    expected_payload = _inventory_payload(
        release_profile_id=expected_release_profile_id,
        transform_revision=expected_transform_revision,
        trust_mode=expected_trust_mode,
        manifest=manifest,
        entries=expected_entries,
    )
    expected_inventory = _attest_inventory(
        expected_payload,
        trust_mode=expected_trust_mode,
        attestation_key=inventory_attestation_key,
    )
    if inventory != expected_inventory:
        raise ValueError("release inventory file bindings changed")
    if committed:
        expected_marker = json.loads(release_commit_marker_bytes(inventory_path))
        if not isinstance(marker, dict):
            raise ValueError("release inventory identity or commit marker is invalid")
        if expected_trust_mode == "production":
            if set(marker) != {*expected_marker, "production_approval"} or any(
                marker.get(field) != value for field, value in expected_marker.items()
            ):
                raise ValueError(
                    "release inventory identity or commit marker is invalid"
                )
            package_approval = verify_embedded_production_package_approval_from_env(
                marker.get("production_approval"),
                release_profile_id=expected_release_profile_id,
                release_profile_sha256=release_profile_sha256(
                    expected_release_profile_id
                ),
                release_inventory_sha256=file_sha256(inventory_path),
                committed_sha256=hashlib.sha256(
                    release_commit_marker_bytes(inventory_path)
                ).hexdigest(),
                training_manifest_sha256=file_sha256(manifest_path),
            )
            require_independent_package_approval(
                gate_receipt.get("production_approval"), package_approval
            )
        elif marker != expected_marker:
            raise ValueError("release inventory identity or commit marker is invalid")
    expected_files = {
        *(entry["path"] for entry in expected_entries),
        RELEASE_INVENTORY_NAME,
    }
    if committed:
        expected_files.add(COMMITTED_NAME)
    actual_files, actual_directories = _package_member_paths(root)
    if actual_files != expected_files:
        raise ValueError(
            f"unexpected release package files: {sorted(actual_files - expected_files)}"
        )
    expected_directories = _expected_directories(expected_files)
    if actual_directories != expected_directories:
        raise ValueError(
            "unexpected release package directories: "
            f"{sorted(actual_directories - expected_directories)}"
        )
    return inventory, gate_receipt


def production_package_ready_profile(release_profile_id: str) -> None:
    """Require a currently issuable profile with a completed package contract."""
    profile = issuable_release_profile(release_profile_id)
    if profile.environment != "production":
        raise ValueError("production inventory requires a production release profile")
    if release_profile_id not in PRODUCTION_PACKAGE_READY_PROFILE_IDS:
        raise ValueError(
            "production packaging contract is not ready for source sidecars and "
            "runtime dependency locks"
        )


def validate_staged_release_inventory(
    release_root: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
    expected_trust_mode: str = "production",
) -> dict[str, Any]:
    """Validate an uncommitted package before independent approval."""
    inventory, _gate_receipt = _validate_release_inventory_contents(
        release_root,
        expected_release_profile_id=expected_release_profile_id,
        expected_transform_revision=expected_transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        expected_trust_mode=expected_trust_mode,
        committed=False,
    )
    return inventory


def validate_staged_release_inventory_with_gate(
    release_root: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
    expected_trust_mode: str = "production",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a staged package and return its already-verified gate snapshot."""
    return _validate_release_inventory_contents(
        release_root,
        expected_release_profile_id=expected_release_profile_id,
        expected_transform_revision=expected_transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        expected_trust_mode=expected_trust_mode,
        committed=False,
    )


def validate_release_inventory(
    release_root: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
    expected_trust_mode: str = "local_engineering",
) -> dict[str, Any]:
    """Validate a committed package, including the absence of unlisted files."""
    inventory, _gate_receipt = _validate_release_inventory_contents(
        release_root,
        expected_release_profile_id=expected_release_profile_id,
        expected_transform_revision=expected_transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        expected_trust_mode=expected_trust_mode,
        committed=True,
    )
    return inventory


def validate_production_release_preflight(
    release_root: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
) -> dict[str, Any]:
    """Return effective eligibility only after the committed package verifies."""
    production_package_ready_profile(expected_release_profile_id)
    root = release_root.absolute()
    initial_members = _release_member_snapshot(root)
    inventory = validate_release_inventory(
        root,
        expected_release_profile_id=expected_release_profile_id,
        expected_transform_revision=expected_transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        expected_trust_mode="production",
    )
    preflight = {
        "schema_version": PRODUCTION_RELEASE_PREFLIGHT_SCHEMA,
        "release_profile_id": expected_release_profile_id,
        "release_inventory_sha256": file_sha256(root / RELEASE_INVENTORY_NAME),
        "commit_marker_sha256": file_sha256(root / COMMITTED_NAME),
        "production_eligible": True,
        "inventory": inventory,
    }
    if _release_member_snapshot(root) != initial_members:
        raise ValueError("release package members changed during validation")
    return preflight
