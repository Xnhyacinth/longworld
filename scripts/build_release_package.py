#!/usr/bin/env python3
"""Build local packages or prepare/finalize independently approved production ones."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.production_trust import (
    require_independent_package_approval,
    verify_production_package_approval_from_env,
)
from longworld.core.promotion import RELEASE_GATE_PURPOSE
from longworld.core.release_inventory import (
    COMMITTED_NAME,
    PROMOTED_FILES,
    RELEASE_INVENTORY_NAME,
    RELEASE_INVENTORY_PURPOSE,
    RELEASE_TRUST_MODES,
    TRAINING_OUTPUT_ALLOWLIST,
    create_release_inventory,
    production_package_ready_profile,
    release_commit_marker_bytes,
    validate_production_release_preflight,
    validate_release_inventory,
    validate_staged_release_inventory,
    validate_staged_release_inventory_with_gate,
)
from longworld.core.release_profile import (
    issuable_release_profile,
    release_profile_sha256,
)
from longworld.core.training_manifest import (
    file_sha256,
    resolve_training_manifest_path,
    training_manifest_release_root,
    validate_deterministic_training_transform,
    validate_training_manifest,
)

_EMAIL = re.compile(rb"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])")
_SECRET_PATTERNS = (
    re.compile(rb"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
        rb"[A-Za-z0-9_-]{10,}\b"
    ),
    re.compile(rb"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bnpm_[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"\b(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*"
        rb"[\"']?[A-Za-z0-9._~+/=-]{16,}",
        re.IGNORECASE,
    ),
)
_NON_RELEASE_STAGE = re.compile(
    rb'["\']data_stage["\']\s*:\s*["\']'
    rb"(?:candidate|source_inventory|external_candidate|diagnostic)[\"']",
    re.IGNORECASE,
)
_SCAN_CHUNK_BYTES = 1024 * 1024
_SCAN_OVERLAP_BYTES = 512
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


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


def _commit_marker_bytes(stage_root: Path) -> bytes:
    return release_commit_marker_bytes(stage_root / RELEASE_INVENTORY_NAME)


def _write_commit_marker(stage_root: Path, marker_bytes: bytes) -> None:
    marker_path = stage_root / COMMITTED_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(marker_path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(marker_bytes)
        handle.flush()
        os.fsync(handle.fileno())


def _scan_release_payloads(stage_root: Path) -> None:
    """Reject credential, email PII, or non-release-stage bytes before commit."""
    for path in sorted(stage_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("release payload scan encountered a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(stage_root)
        if any(
            part.lower().startswith(("candidate", "reject")) for part in relative.parts
        ):
            raise ValueError("release payload contains candidate/reject material")
        overlap = b""
        with path.open("rb") as handle:
            while chunk := handle.read(_SCAN_CHUNK_BYTES):
                scanned = overlap + chunk
                if _EMAIL.search(scanned):
                    raise ValueError(
                        f"release payload contains unredacted email PII: {relative}"
                    )
                if any(pattern.search(scanned) for pattern in _SECRET_PATTERNS):
                    raise ValueError(
                        f"release payload contains a credential-shaped secret: {relative}"
                    )
                if _NON_RELEASE_STAGE.search(scanned):
                    raise ValueError(
                        f"release payload contains a non-release data stage: {relative}"
                    )
                overlap = scanned[-_SCAN_OVERLAP_BYTES:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_path_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish one same-filesystem path without replacing a peer."""
    if sys.platform != "linux":
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace package publication requires Linux renameat2",
            os.fspath(destination),
        )
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise OSError(
            errno.ENOSYS,
            "Linux libc does not expose renameat2",
            os.fspath(destination),
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(
            error_number, os.strerror(error_number), os.fspath(destination)
        )
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _directory_identity(path: Path, *, label: str) -> tuple[int, int]:
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink")
    if not path.is_dir():
        raise ValueError(f"{label} is missing or not a directory")
    status = path.stat(follow_symlinks=False)
    return status.st_dev, status.st_ino


def _require_directory_identity(
    path: Path, expected: tuple[int, int], *, label: str
) -> None:
    try:
        actual = _directory_identity(path, label=label)
    except ValueError as error:
        raise ValueError(f"{label} identity changed") from error
    if actual != expected:
        raise ValueError(f"{label} identity changed")


def _install_file_no_replace(source: Path, destination: Path) -> tuple[int, int]:
    """Publish a complete temp file after atomically claiming its destination."""
    source_status = source.stat(follow_symlinks=False)
    source_identity = (source_status.st_dev, source_status.st_ino)
    _publish_path_no_replace(source, destination)
    installed_status = destination.stat(follow_symlinks=False)
    if (installed_status.st_dev, installed_status.st_ino) != source_identity:
        raise ValueError("production package approval request identity changed")
    return source_identity


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
    if trust_mode == "production":
        raise ValueError(
            "production packages require prepare_release_package followed by "
            "independent approval and finalize_release_package"
        )
    temporary, inventory, _staged_manifest, marker_bytes = _stage_release_package(
        source_release_root=source_release_root,
        destination=destination,
        promoted_dir=promoted_dir,
        training_manifest_path=training_manifest_path,
        data_card_path=data_card_path,
        release_profile_id=release_profile_id,
        transform_revision=transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        trust_mode=trust_mode,
    )
    _write_commit_marker(temporary, marker_bytes)
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
    _publish_path_no_replace(temporary, destination.absolute())
    _fsync_directory(destination.absolute().parent)
    return inventory


def _stage_release_package(
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
    trust_mode: str,
) -> tuple[Path, dict, Path, bytes]:
    """Create a validated inventory in a private temporary directory."""
    if trust_mode == "production":
        issuable_release_profile(release_profile_id)
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
    # Keep this mode-0700 tree on failure. Deleting a pathname after an
    # identity check is racy; a retained private orphan is fail-closed.
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
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
    validate_deterministic_training_transform(
        staged_manifest,
        validate_training_manifest(
            staged_manifest,
            expected_release_profile_id=release_profile_id,
            expected_transform_revision=transform_revision,
            attestation_key=training_attestation_key,
        ),
        required=trust_mode == "production",
        require_production_trust=trust_mode == "production",
    )
    marker_bytes = _commit_marker_bytes(temporary)
    _scan_release_payloads(temporary)
    validate_staged_release_inventory(
        temporary,
        expected_release_profile_id=release_profile_id,
        expected_transform_revision=transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        expected_trust_mode=trust_mode,
    )
    return temporary, inventory, staged_manifest, marker_bytes


def prepare_release_package(
    *,
    source_release_root: Path,
    destination: Path,
    promoted_dir: Path,
    training_manifest_path: Path,
    data_card_path: Path,
    approval_request_path: Path,
    release_profile_id: str,
    transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
) -> dict:
    """Stage a production package and emit unsigned bindings for external approval."""
    destination = destination.absolute()
    request_path = approval_request_path.absolute()
    try:
        request_path.relative_to(destination)
    except ValueError:
        pass
    else:
        raise ValueError(
            "production package approval request must be outside the staged package"
        )
    if request_path.exists() or request_path.is_symlink():
        raise ValueError("production package approval request already exists")
    temporary, inventory, staged_manifest, marker_bytes = _stage_release_package(
        source_release_root=source_release_root,
        destination=destination,
        promoted_dir=promoted_dir,
        training_manifest_path=training_manifest_path,
        data_card_path=data_card_path,
        release_profile_id=release_profile_id,
        transform_revision=transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        trust_mode="production",
    )
    request = {
        "schema_version": "longworld-production-package-approval-request-v1",
        "approval_scope": "longworld-production-package",
        "release_profile_id": release_profile_id,
        "release_profile_sha256": release_profile_sha256(release_profile_id),
        "release_inventory_sha256": file_sha256(temporary / RELEASE_INVENTORY_NAME),
        "committed_sha256": hashlib.sha256(marker_bytes).hexdigest(),
        "training_manifest_sha256": file_sha256(staged_manifest),
    }
    request_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_request_name = tempfile.mkstemp(
        prefix=f".{request_path.name}.", suffix=".tmp", dir=request_path.parent
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(request, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    # Once either pathname is published, failures retain it for audit instead
    # of racing another owner with stat-then-rmtree/unlink cleanup.
    _publish_path_no_replace(temporary, destination)
    _install_file_no_replace(Path(temporary_request_name), request_path)
    _fsync_directory(destination.parent)
    if request_path.parent != destination.parent:
        _fsync_directory(request_path.parent)
    return inventory


def finalize_release_package(
    *,
    staged_package: Path,
    release_profile_id: str,
    transform_revision: str,
    training_attestation_key: bytes | None,
    gate_attestation_key: bytes | None,
    inventory_attestation_key: bytes | None,
) -> dict:
    """Verify independent approval, then atomically commit a production package."""
    production_package_ready_profile(release_profile_id)
    stage_root = staged_package.absolute()
    stage_identity = _directory_identity(stage_root, label="staged package root")
    inventory, gate = validate_staged_release_inventory_with_gate(
        stage_root,
        expected_release_profile_id=release_profile_id,
        expected_transform_revision=transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
        expected_trust_mode="production",
    )
    _require_directory_identity(stage_root, stage_identity, label="staged package root")
    inventory_path = stage_root / RELEASE_INVENTORY_NAME
    manifest_binding = inventory.get("training_manifest")
    if not isinstance(manifest_binding, dict):
        raise TypeError("release inventory training manifest binding is missing")
    manifest_path = stage_root / str(manifest_binding.get("path") or "")
    marker_bytes = _commit_marker_bytes(stage_root)
    approval = verify_production_package_approval_from_env(
        release_profile_id=release_profile_id,
        release_profile_sha256=release_profile_sha256(release_profile_id),
        release_inventory_sha256=file_sha256(inventory_path),
        committed_sha256=hashlib.sha256(marker_bytes).hexdigest(),
        training_manifest_sha256=file_sha256(manifest_path),
    )
    release_approval = gate.get("production_approval")
    if not isinstance(release_approval, dict):
        raise TypeError("release gate production approval is missing")
    require_independent_package_approval(release_approval, approval)
    _require_directory_identity(stage_root, stage_identity, label="staged package root")
    marker = json.loads(marker_bytes)
    marker["production_approval"] = approval
    committed_bytes = (
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    _require_directory_identity(stage_root, stage_identity, label="staged package root")
    _write_commit_marker(stage_root, committed_bytes)
    _require_directory_identity(stage_root, stage_identity, label="staged package root")
    _fsync_directory(stage_root)
    _fsync_directory(stage_root.parent)
    return validate_production_release_preflight(
        stage_root,
        expected_release_profile_id=release_profile_id,
        expected_transform_revision=transform_revision,
        training_attestation_key=training_attestation_key,
        gate_attestation_key=gate_attestation_key,
        inventory_attestation_key=inventory_attestation_key,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("build", "prepare", "finalize", "preflight"),
        default="build",
    )
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--promoted-dir", type=Path)
    parser.add_argument("--training-manifest", type=Path)
    parser.add_argument("--data-card", type=Path)
    parser.add_argument("--approval-request", type=Path)
    parser.add_argument("--release-profile", required=True)
    parser.add_argument("--expected-transform-revision", required=True)
    parser.add_argument(
        "--trust-mode", choices=RELEASE_TRUST_MODES, default="local_engineering"
    )
    args = parser.parse_args()
    training_key = attestation_key_from_env("training_export_manifest")
    gate_key = attestation_key_from_env(RELEASE_GATE_PURPOSE)
    inventory_key = attestation_key_from_env(RELEASE_INVENTORY_PURPOSE)
    if args.phase in {"finalize", "preflight"}:
        if args.phase == "finalize":
            inventory = finalize_release_package(
                staged_package=args.destination,
                release_profile_id=args.release_profile,
                transform_revision=args.expected_transform_revision,
                training_attestation_key=training_key,
                gate_attestation_key=gate_key,
                inventory_attestation_key=inventory_key,
            )
        else:
            inventory = validate_production_release_preflight(
                args.destination,
                expected_release_profile_id=args.release_profile,
                expected_transform_revision=args.expected_transform_revision,
                training_attestation_key=training_key,
                gate_attestation_key=gate_key,
                inventory_attestation_key=inventory_key,
            )
    else:
        missing = [
            flag
            for flag, value in (
                ("--release-root", args.release_root),
                ("--promoted-dir", args.promoted_dir),
                ("--training-manifest", args.training_manifest),
                ("--data-card", args.data_card),
            )
            if value is None
        ]
        if missing:
            parser.error(f"{args.phase} requires {', '.join(missing)}")
        assert args.release_root is not None
        assert args.promoted_dir is not None
        assert args.training_manifest is not None
        assert args.data_card is not None
        if args.phase == "prepare":
            if args.approval_request is None:
                parser.error("prepare requires --approval-request")
            inventory = prepare_release_package(
                source_release_root=args.release_root,
                destination=args.destination,
                promoted_dir=args.promoted_dir,
                training_manifest_path=args.training_manifest,
                data_card_path=args.data_card,
                approval_request_path=args.approval_request,
                release_profile_id=args.release_profile,
                transform_revision=args.expected_transform_revision,
                training_attestation_key=training_key,
                gate_attestation_key=gate_key,
                inventory_attestation_key=inventory_key,
            )
        else:
            inventory = build_release_package(
                source_release_root=args.release_root,
                destination=args.destination,
                promoted_dir=args.promoted_dir,
                training_manifest_path=args.training_manifest,
                data_card_path=args.data_card,
                release_profile_id=args.release_profile,
                transform_revision=args.expected_transform_revision,
                training_attestation_key=training_key,
                gate_attestation_key=gate_key,
                inventory_attestation_key=inventory_key,
                trust_mode=args.trust_mode,
            )
    print(json.dumps(inventory, indent=2))


if __name__ == "__main__":
    main()
