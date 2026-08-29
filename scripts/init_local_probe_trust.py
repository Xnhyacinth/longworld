#!/usr/bin/env python3
"""Create an isolated local-probe trust file outside the repository."""

from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import ATTESTATION_ROLES

TRUST_SCHEMA = "longworld-local-probe-trust-v1"
TRUST_FILENAME = "local_probe_trust.json"
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_POSIX_ACL_XATTRS = {"system.posix_acl_access", "system.posix_acl_default"}


class ProbeTrustError(ValueError):
    """Raised when local-probe credentials cannot be handled safely."""


def _absolute(path: Path) -> Path:
    if not path.is_absolute():
        raise ProbeTrustError("trust root must be an absolute path")
    return Path(os.path.abspath(os.fspath(path)))


def _outside_repository(path: Path) -> None:
    if path == ROOT or ROOT in path.parents:
        raise ProbeTrustError("trust root must be outside the repository")


def _has_posix_acl(descriptor: int) -> bool:
    try:
        names = os.listxattr(descriptor)
    except OSError as error:
        if error.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            return False
        raise ProbeTrustError("cannot inspect trust path ACLs") from error
    return bool(_POSIX_ACL_XATTRS.intersection(names))


def _validate_private_directory(descriptor: int, *, label: str) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProbeTrustError(f"{label} is not a directory")
    if metadata.st_uid != os.geteuid():
        raise ProbeTrustError(f"{label} must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ProbeTrustError(f"{label} must have mode 0700")
    if _has_posix_acl(descriptor):
        raise ProbeTrustError(f"{label} must not have a POSIX ACL")


def _open_absolute_directory(path: Path) -> int:
    if not getattr(os, "O_NOFOLLOW", 0):
        raise ProbeTrustError("this platform cannot reject symlink paths")
    current = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ProbeTrustError("trust path contains a symlink") from error
                raise ProbeTrustError(
                    "trust parent directory is unavailable"
                ) from error
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _open_private_root(path: Path, *, create: bool) -> tuple[int, int]:
    parent_fd = _open_absolute_directory(path.parent)
    try:
        _validate_private_directory(parent_fd, label="trust parent")
        try:
            metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            if not create:
                raise ProbeTrustError("trust root is missing") from error
            os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        else:
            if stat.S_ISLNK(metadata.st_mode):
                raise ProbeTrustError("trust root must not be a symlink")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ProbeTrustError("trust root is not a directory")
        try:
            root_fd = os.open(path.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise ProbeTrustError("trust root must not be a symlink") from error
        try:
            _validate_private_directory(root_fd, label="trust root")
        except BaseException:
            os.close(root_fd)
            raise
        return parent_fd, root_fd
    except BaseException:
        os.close(parent_fd)
        raise


def _payload() -> dict[str, object]:
    probe_id = f"local-probe-{secrets.token_hex(16)}"
    keys: set[str] = set()
    key_ids: set[str] = set()
    roles: dict[str, dict[str, str]] = {}
    for role in ATTESTATION_ROLES:
        key = secrets.token_hex(32)
        while key in keys:
            key = secrets.token_hex(32)
        key_id = f"probe-{role}-{secrets.token_hex(16)}"
        while key_id in key_ids or key_id == probe_id:
            key_id = f"probe-{role}-{secrets.token_hex(16)}"
        keys.add(key)
        key_ids.add(key_id)
        roles[role] = {"key_id": key_id, "key": key}
    return {
        "schema_version": TRUST_SCHEMA,
        "environment": "probe",
        "probe_id": probe_id,
        "roles": roles,
    }


def _exclusive_atomic_write(root_fd: int, filename: str, payload: bytes) -> None:
    temporary_name = f".{filename}.{secrets.token_hex(8)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(temporary_name, _WRITE_FLAGS, 0o600, dir_fd=root_fd)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                temporary_name,
                filename,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ProbeTrustError(
                f"{filename} already exists; refusing to overwrite"
            ) from error
        os.fsync(root_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=root_fd)
            os.fsync(root_fd)
        except FileNotFoundError:
            pass


def initialize_local_probe_trust(trust_root: Path) -> Path:
    """Create one role-separated local-probe trust file without overwriting."""
    root = _absolute(trust_root)
    _outside_repository(root)
    parent_fd, root_fd = _open_private_root(root, create=True)
    try:
        encoded = (
            json.dumps(_payload(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode()
        _exclusive_atomic_write(root_fd, TRUST_FILENAME, encoded)
    finally:
        os.close(root_fd)
        os.close(parent_fd)
    return root / TRUST_FILENAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create an isolated role-separated local-probe trust root."
    )
    parser.add_argument("--trust-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        trust_file = initialize_local_probe_trust(args.trust_root)
    except ProbeTrustError as error:
        parser.error(str(error))
    print(trust_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
