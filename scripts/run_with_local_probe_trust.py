#!/usr/bin/env python3
"""Run one argv command with isolated local-probe role credentials."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ATTESTATION_ROLES,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attestation_environment_names,
)
from scripts.init_local_probe_trust import (
    TRUST_SCHEMA,
    ProbeTrustError,
    _absolute,
    _has_posix_acl,
    _open_private_root,
    _outside_repository,
)

_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_MAX_TRUST_BYTES = 64 * 1024
_OUTPUT_DRAIN_TIMEOUT_SECONDS = 30.0
_PROCESS_POLL_INTERVAL_SECONDS = 0.1
_PROCESS_TERMINATE_GRACE_SECONDS = 1.0
_PROCESS_KILL_TIMEOUT_SECONDS = 5.0
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{43,256}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{16,160}\Z")
_REJECTED_KEY_MARKERS = (
    "changeme",
    "example",
    "placeholder",
    "test-key",
    "use-a-secret",
)
_FIXED_CHILD_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}
_PASSTHROUGH_ALLOWLIST = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "GH_CONFIG_DIR",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "LONGWORLD_GH_BINARY",
        "LONGWORLD_GH_BINARY_SHA256",
        "LONGWORLD_PUBLIC_POLICY_SHA256",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "TOKENIZERS_PARALLELISM",
        "TRANSFORMERS_OFFLINE",
    }
)
_DANGEROUS_ENV_NAMES = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "GCONV_PATH",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "SSLKEYLOGFILE",
    }
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProbeTrustError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _strict_payload(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeDecodeError as error:
        raise ProbeTrustError("trust file must be UTF-8 JSON") from error
    except json.JSONDecodeError as error:
        raise ProbeTrustError("trust file is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ProbeTrustError("trust file must contain a JSON object")
    expected_top = {"schema_version", "environment", "probe_id", "roles"}
    if set(payload) != expected_top:
        raise ProbeTrustError("trust file has missing or unexpected fields")
    if payload["schema_version"] != TRUST_SCHEMA:
        raise ProbeTrustError("unsupported trust schema")
    if payload["environment"] != "probe":
        raise ProbeTrustError("local trust environment must be probe")
    probe_id = payload["probe_id"]
    if (
        not isinstance(probe_id, str)
        or not probe_id.startswith("local-probe-")
        or not _ID_RE.fullmatch(probe_id)
    ):
        raise ProbeTrustError("invalid probe ID")
    roles = payload["roles"]
    if not isinstance(roles, dict) or set(roles) != set(ATTESTATION_ROLES):
        raise ProbeTrustError(
            "trust file must contain exactly the six attestation roles"
        )
    keys: list[str] = []
    key_ids: list[str] = []
    for role in ATTESTATION_ROLES:
        identity = roles[role]
        if not isinstance(identity, dict) or set(identity) != {"key", "key_id"}:
            raise ProbeTrustError(f"invalid {role} role identity fields")
        key = identity["key"]
        key_id = identity["key_id"]
        if (
            not isinstance(key, str)
            or len(key.encode("utf-8")) < 32
            or not _KEY_RE.fullmatch(key)
            or any(marker in key.lower() for marker in _REJECTED_KEY_MARKERS)
        ):
            raise ProbeTrustError(f"invalid key for role {role}")
        if (
            not isinstance(key_id, str)
            or not key_id.startswith("probe-")
            or not _ID_RE.fullmatch(key_id)
        ):
            raise ProbeTrustError(f"invalid key ID for role {role}")
        keys.append(key)
        key_ids.append(key_id)
    if len(set(keys)) != len(keys):
        raise ProbeTrustError("role keys must be unique")
    if len(set(key_ids)) != len(key_ids):
        raise ProbeTrustError("role key IDs must be unique")
    if probe_id in key_ids:
        raise ProbeTrustError("probe ID and role key IDs must be unique")
    return payload


def load_local_probe_trust(trust_file: Path) -> dict[str, Any]:
    """Read and validate a private local-probe trust file without following links."""
    path = _absolute(trust_file)
    _outside_repository(path)
    parent_fd, root_fd = _open_private_root(path.parent, create=False)
    try:
        try:
            metadata = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            raise ProbeTrustError("trust file is missing") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ProbeTrustError("trust file must not be a symlink")
        try:
            descriptor = os.open(path.name, _READ_FLAGS, dir_fd=root_fd)
        except OSError as error:
            raise ProbeTrustError("trust file cannot be opened safely") from error
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ProbeTrustError("trust file is not a regular file")
            if before.st_uid != os.geteuid():
                raise ProbeTrustError("trust file must be owned by the current user")
            if stat.S_IMODE(before.st_mode) != 0o600:
                raise ProbeTrustError("trust file must have mode 0600")
            if before.st_nlink != 1:
                raise ProbeTrustError("trust file must not have hard links")
            if _has_posix_acl(descriptor):
                raise ProbeTrustError("trust file must not have a POSIX ACL")
            if before.st_size > _MAX_TRUST_BYTES:
                raise ProbeTrustError("trust file exceeds the size limit")
            chunks: list[bytes] = []
            remaining = _MAX_TRUST_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            if len(raw) > _MAX_TRUST_BYTES:
                raise ProbeTrustError("trust file exceeds the size limit")
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ProbeTrustError("trust file changed while it was being read")
        finally:
            os.close(descriptor)
    finally:
        os.close(root_fd)
        os.close(parent_fd)
    return _strict_payload(raw)


def _selected_roles(
    roles: tuple[str, ...], *, allow_combined_roles: bool
) -> tuple[str, ...]:
    if not roles:
        raise ProbeTrustError("at least one attestation role is required")
    if len(set(roles)) != len(roles) or any(
        role not in ATTESTATION_ROLES for role in roles
    ):
        raise ProbeTrustError("attestation roles are invalid or duplicated")
    if len(roles) > 1 and not allow_combined_roles:
        raise ProbeTrustError(
            "combined attestation roles require explicit diagnostic opt-in"
        )
    return roles


def _passthrough_environment(names: tuple[str, ...]) -> dict[str, str]:
    attestation_names = set(attestation_environment_names())
    passed: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ProbeTrustError("passthrough environment name is invalid")
        if name in attestation_names or name == ATTESTATION_ENVIRONMENT_ENV:
            raise ProbeTrustError("attestation environment cannot be passed through")
        if name in _DANGEROUS_ENV_NAMES or name.startswith(("LD_", "DYLD_")):
            raise ProbeTrustError("dangerous execution environment cannot be passed")
        if name not in _PASSTHROUGH_ALLOWLIST:
            raise ProbeTrustError("environment passthrough is not allowlisted")
        if name in passed:
            raise ProbeTrustError("environment passthrough names must be unique")
        if name not in os.environ:
            raise ProbeTrustError(f"requested environment variable is missing: {name}")
        value = os.environ[name]
        if "\0" in value:
            raise ProbeTrustError("passthrough environment value contains NUL")
        passed[name] = value
    return passed


def _child_environment(
    payload: dict[str, Any],
    *,
    roles: tuple[str, ...],
    pass_env: tuple[str, ...],
    allow_combined_roles: bool,
) -> tuple[dict[str, str], tuple[bytes, ...]]:
    selected = _selected_roles(roles, allow_combined_roles=allow_combined_roles)
    passed = _passthrough_environment(pass_env)
    environment = dict(_FIXED_CHILD_ENV)
    environment.update(passed)
    environment[ATTESTATION_ENVIRONMENT_ENV] = "probe"
    identities = payload["roles"]
    for role in selected:
        environment[ROLE_KEY_ENVS[role]] = identities[role]["key"]
        environment[ROLE_KEY_ID_ENVS[role]] = identities[role]["key_id"]
    if len(selected) > 1:
        environment[LOCAL_PROBE_COMBINED_ROLES_ENV] = LOCAL_PROBE_TRUST_ISOLATION_VALUE
    secrets_to_remove = tuple(
        value.encode()
        for value in (
            *(identities[role]["key"] for role in selected),
            *(value for value in passed.values() if len(value.encode()) >= 8),
        )
    )
    return environment, secrets_to_remove


def _redact(raw: bytes, secrets_to_remove: tuple[bytes, ...]) -> bytes:
    redacted = raw
    for secret in sorted(set(secrets_to_remove), key=len, reverse=True):
        redacted = redacted.replace(secret, b"[REDACTED]")
    return redacted


def _stream_redacted(
    source: Any, destination: Any, secrets_to_remove: tuple[bytes, ...]
) -> None:
    overlap = max((len(secret) for secret in secrets_to_remove), default=1) - 1
    pending = b""
    while chunk := source.read(64 * 1024):
        pending += chunk
        if len(pending) <= overlap:
            continue
        boundary = len(pending) - overlap
        emit_end = boundary
        for secret in secrets_to_remove:
            start = pending.rfind(secret, 0, boundary + len(secret))
            if 0 <= start < boundary < start + len(secret):
                emit_end = min(emit_end, start)
        destination.write(_redact(pending[:emit_end], secrets_to_remove))
        destination.flush()
        pending = pending[emit_end:]
    destination.write(_redact(pending, secrets_to_remove))
    destination.flush()


def _signal_process_group(process: subprocess.Popen[bytes], signum: int) -> bool:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return False
    except OSError as error:
        raise ProbeTrustError("probe process group could not be signalled") from error
    return True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> int:
    term_sent = _signal_process_group(process, signal.SIGTERM)
    if term_sent:
        time.sleep(_PROCESS_TERMINATE_GRACE_SECONDS)
    _signal_process_group(process, signal.SIGKILL)
    try:
        return process.wait(timeout=_PROCESS_KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ProbeTrustError("probe process group could not be terminated") from error


def _join_workers(workers: tuple[threading.Thread, ...], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    for worker in workers:
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
    return not any(worker.is_alive() for worker in workers)


def run_with_trust(
    trust_file: Path,
    command: list[str],
    *,
    roles: tuple[str, ...],
    pass_env: tuple[str, ...] = (),
    allow_combined_roles: bool = False,
) -> int:
    """Execute argv directly and redact any role key echoed by the child."""
    if not command or any(
        not isinstance(part, str) or "\0" in part for part in command
    ):
        raise ProbeTrustError("command must be a non-empty NUL-free argv list")
    payload = load_local_probe_trust(trust_file)
    environment, secrets_to_remove = _child_environment(
        payload,
        roles=roles,
        pass_env=pass_env,
        allow_combined_roles=allow_combined_roles,
    )
    try:
        process = subprocess.Popen(
            command,
            env=environment,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError as error:
        raise ProbeTrustError("probe command could not be executed") from error
    try:
        assert process.stdout is not None and process.stderr is not None
        worker_errors: list[Exception] = []
        relay_failed = threading.Event()

        def relay(source: Any, destination: Any) -> None:
            try:
                _stream_redacted(source, destination, secrets_to_remove)
            except Exception as error:  # noqa: BLE001 - thread boundary fails closed
                worker_errors.append(error)
                relay_failed.set()

        workers = (
            threading.Thread(
                target=relay,
                args=(process.stdout, sys.stdout.buffer),
                daemon=True,
            ),
            threading.Thread(
                target=relay,
                args=(process.stderr, sys.stderr.buffer),
                daemon=True,
            ),
        )
        for worker in workers:
            worker.start()
        terminated_for_relay_failure = False
        while True:
            try:
                returncode = process.wait(timeout=_PROCESS_POLL_INTERVAL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if relay_failed.is_set():
                    returncode = _terminate_process_group(process)
                    terminated_for_relay_failure = True
                    break

        drained = _join_workers(workers, _OUTPUT_DRAIN_TIMEOUT_SECONDS)
        drain_timed_out = not drained
        if drain_timed_out:
            _terminate_process_group(process)
            drained = _join_workers(workers, _PROCESS_KILL_TIMEOUT_SECONDS)
        if not drained:
            raise ProbeTrustError("probe output relay did not terminate")
        if drain_timed_out:
            process.stdout.close()
            process.stderr.close()
            raise ProbeTrustError("probe output relay did not terminate")
        if worker_errors:
            if not terminated_for_relay_failure:
                _terminate_process_group(process)
            process.stdout.close()
            process.stderr.close()
            raise ProbeTrustError("probe output relay failed") from worker_errors[0]
        process.stdout.close()
        process.stderr.close()
        return returncode
    except ProbeTrustError:
        raise
    except BaseException:
        try:
            _terminate_process_group(process)
        except ProbeTrustError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one argv command with local-probe role credentials."
    )
    parser.add_argument("--trust-file", type=Path, required=True)
    parser.add_argument(
        "--role", choices=ATTESTATION_ROLES, action="append", required=True
    )
    parser.add_argument("--pass-env", action="append", default=[])
    parser.add_argument("--allow-combined-roles", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        return run_with_trust(
            args.trust_file,
            command,
            roles=tuple(args.role),
            pass_env=tuple(args.pass_env),
            allow_combined_roles=args.allow_combined_roles,
        )
    except ProbeTrustError as error:
        parser.error(str(error))
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
