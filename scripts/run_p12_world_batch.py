#!/usr/bin/env python3
"""Run explicit P12 world pipelines concurrently without weakening their gates.

This is a non-adversarial local engineering scheduler, not a process sandbox.
Content hashes detect drift but are not production signatures. Parent-path checks
are not dirfd confinement, and commands that deliberately detach into a new
session can escape process-group cleanup. Production execution still requires an
external sandbox and trust root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = "longworld-p12-world-batch-manifest-v1"
RECEIPT_SCHEMA = "longworld-p12-world-batch-receipt-v1"
RUN_SCHEMA = "longworld-p12-world-batch-run-v1"
RECEIPT_NAME = ".longworld_batch_receipt.json"
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_RECEIPT_BYTES = 16 * 1024 * 1024
_MAX_WORKERS = 32
_MAX_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
_TERMINATE_GRACE_SECONDS = 0.5
_KILL_WAIT_SECONDS = 5.0
_WORLD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHELL_EXECUTABLES = frozenset(
    {"ash", "bash", "csh", "dash", "fish", "ksh", "sh", "tcsh", "zsh"}
)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class BatchManifestError(ValueError):
    """Raised when a batch manifest or bound path is unsafe or malformed."""


class _StageTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class Stage:
    name: str
    argv: tuple[str, ...]
    bound_inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class World:
    world_id: str
    config: str
    source_bundle: str
    output_dir: str
    stages: tuple[Stage, ...]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchManifestError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_regular_file(path: Path, *, limit: int, label: str) -> bytes:
    try:
        expected = path.lstat()
    except OSError as error:
        raise BatchManifestError(f"{label} is missing or unsafe: {path}") from error
    if not stat.S_ISREG(expected.st_mode):
        raise BatchManifestError(f"{label} is not a regular file: {path}")
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as error:
        raise BatchManifestError(f"{label} is missing or unsafe: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or (
            expected.st_dev,
            expected.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise BatchManifestError(f"{label} is not a regular file: {path}")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, min(1024 * 1024, limit + 1 - size)):
            size += len(chunk)
            if size > limit:
                raise BatchManifestError(f"{label} exceeds the size limit: {path}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    raw = b"".join(chunks)
    if identity_before != identity_after or len(raw) != after.st_size:
        raise BatchManifestError(f"{label} changed while being read: {path}")
    return raw


def _hash_regular_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        expected = path.lstat()
    except OSError as error:
        raise BatchManifestError(f"{label} is missing or unsafe: {path}") from error
    if not stat.S_ISREG(expected.st_mode):
        raise BatchManifestError(f"{label} is not a regular file: {path}")
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as error:
        raise BatchManifestError(f"{label} is missing or unsafe: {path}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or (
            expected.st_dev,
            expected.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise BatchManifestError(f"{label} is not a regular file: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or size != after.st_size:
        raise BatchManifestError(f"{label} changed while being read: {path}")
    return {"sha256": digest.hexdigest(), "size": size}


def _workspace_root(path: Path) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BatchManifestError("workspace root is missing") from error
    if lexical != resolved or path.is_symlink():
        raise BatchManifestError("workspace root contains a symlink path")
    if not resolved.is_dir():
        raise BatchManifestError("workspace root is not a directory")
    return resolved


def _relative_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise BatchManifestError(f"{field} must be a safe relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BatchManifestError(f"{field} must be a safe relative path")
    normalized = path.as_posix()
    if normalized != value:
        raise BatchManifestError(f"{field} must be a normalized relative path")
    return normalized


def _declared_input_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise BatchManifestError(f"{field} must be a normalized path")
    path = Path(value)
    if not path.is_absolute():
        return _relative_path(value, field=field)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise BatchManifestError(f"{field} must be a normalized path")
    if path.as_posix() != value:
        raise BatchManifestError(f"{field} must be a normalized path")
    return value


def _reject_symlink_components(root: Path, relative: str, *, field: str) -> Path:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise BatchManifestError(f"{field} contains a symlink path")
        if current.exists() and current != root / relative and not current.is_dir():
            raise BatchManifestError(f"{field} contains a non-directory parent")
    return root / relative


def _bound_input(root: Path, relative: str, *, field: str) -> dict[str, Any]:
    path = _reject_symlink_components(root, relative, field=field)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BatchManifestError(f"{field} is missing: {relative}") from error
    if resolved != path or not resolved.is_file():
        raise BatchManifestError(f"{field} is not a regular file: {relative}")
    return {"path": relative, **_hash_regular_file(path, label=field)}


def _declared_bound_input(root: Path, value: str, *, field: str) -> dict[str, Any]:
    path = Path(value)
    if not path.is_absolute():
        return _bound_input(root, value, field=field)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BatchManifestError(f"{field} is missing: {value}") from error
    if resolved != path or not path.is_file() or path.is_symlink():
        raise BatchManifestError(f"{field} contains a symlink or is not a file")
    return {"path": value, **_hash_regular_file(path, label=field)}


def _parse_stage(raw: object, *, world_id: str) -> Stage:
    if not isinstance(raw, dict) or set(raw) != {
        "name",
        "argv",
        "bound_inputs",
        "outputs",
    }:
        raise BatchManifestError(f"{world_id}: stage fields are invalid")
    name = raw["name"]
    if not isinstance(name, str) or _WORLD_ID.fullmatch(name) is None:
        raise BatchManifestError(f"{world_id}: stage name is invalid")
    argv = raw["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item and "\0" not in item for item in argv)
    ):
        raise BatchManifestError(f"{world_id}/{name}: argv is invalid")
    if Path(argv[0]).name.lower() in _SHELL_EXECUTABLES:
        raise BatchManifestError(f"{world_id}/{name}: shell executables are forbidden")
    bound_inputs = raw["bound_inputs"]
    if not isinstance(bound_inputs, list) or not bound_inputs:
        raise BatchManifestError(f"{world_id}/{name}: bound_inputs are required")
    normalized_inputs = tuple(
        _declared_input_path(value, field=f"{world_id}/{name} bound input")
        for value in bound_inputs
    )
    if len(set(normalized_inputs)) != len(normalized_inputs):
        raise BatchManifestError(f"{world_id}/{name}: duplicate bound_inputs")
    outputs = raw["outputs"]
    if not isinstance(outputs, list) or not outputs:
        raise BatchManifestError(f"{world_id}/{name}: outputs are required")
    normalized_outputs = tuple(
        _relative_path(value, field=f"{world_id}/{name} output") for value in outputs
    )
    if len(set(normalized_outputs)) != len(normalized_outputs):
        raise BatchManifestError(f"{world_id}/{name}: duplicate outputs")
    if RECEIPT_NAME in normalized_outputs:
        raise BatchManifestError(f"{world_id}/{name}: receipt is a reserved output")
    return Stage(
        name=name,
        argv=tuple(argv),
        bound_inputs=normalized_inputs,
        outputs=normalized_outputs,
    )


def _parse_world(raw: object) -> World:
    expected = {"world_id", "config", "source_bundle", "output_dir", "stages"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise BatchManifestError("world fields are invalid")
    world_id = raw["world_id"]
    if not isinstance(world_id, str) or _WORLD_ID.fullmatch(world_id) is None:
        raise BatchManifestError("world_id is invalid")
    stages_raw = raw["stages"]
    if not isinstance(stages_raw, list) or not stages_raw:
        raise BatchManifestError(f"{world_id}: stages are required")
    stages = tuple(_parse_stage(stage, world_id=world_id) for stage in stages_raw)
    names = [stage.name for stage in stages]
    if len(names) != len(set(names)):
        raise BatchManifestError(f"{world_id}: duplicate stage name")
    declared_outputs = [output for stage in stages for output in stage.outputs]
    if len(declared_outputs) != len(set(declared_outputs)):
        raise BatchManifestError(f"{world_id}: duplicate stage output")
    return World(
        world_id=world_id,
        config=_relative_path(raw["config"], field=f"{world_id} config"),
        source_bundle=_relative_path(
            raw["source_bundle"], field=f"{world_id} source_bundle"
        ),
        output_dir=_relative_path(raw["output_dir"], field=f"{world_id} output_dir"),
        stages=stages,
    )


def _load_manifest(path: Path, root: Path) -> tuple[list[World], str]:
    lexical = Path(os.path.abspath(os.fspath(path)))
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BatchManifestError("batch manifest is missing") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise BatchManifestError("batch manifest is outside workspace root") from error
    if lexical != resolved or path.is_symlink():
        raise BatchManifestError("batch manifest contains a symlink path")
    raw = _read_regular_file(
        resolved, limit=_MAX_MANIFEST_BYTES, label="batch manifest"
    )
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BatchManifestError("batch manifest is not valid UTF-8 JSON") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "worlds"}
        or payload.get("schema_version") != MANIFEST_SCHEMA
    ):
        raise BatchManifestError("batch manifest schema is invalid")
    raw_worlds = payload.get("worlds")
    if not isinstance(raw_worlds, list) or not raw_worlds:
        raise BatchManifestError("batch manifest has no worlds")
    worlds = sorted(
        (_parse_world(value) for value in raw_worlds), key=lambda x: x.world_id
    )
    world_ids = [world.world_id for world in worlds]
    output_dirs = [world.output_dir for world in worlds]
    if len(world_ids) != len(set(world_ids)):
        raise BatchManifestError("duplicate world_id")
    if len(output_dirs) != len(set(output_dirs)):
        raise BatchManifestError("duplicate output_dir")
    output_parts = [(value, Path(value).parts) for value in output_dirs]
    for index, (left, left_parts) in enumerate(output_parts):
        for right, right_parts in output_parts[index + 1 :]:
            common = min(len(left_parts), len(right_parts))
            if left_parts[:common] == right_parts[:common]:
                raise BatchManifestError(
                    f"overlapping output_dir: {left!r} and {right!r}"
                )
    for world in worlds:
        _bound_input(root, world.config, field=f"{world.world_id} config")
        _bound_input(root, world.source_bundle, field=f"{world.world_id} source_bundle")
        for stage in world.stages:
            _stage_bound_inputs(root, world, stage)
        _reject_symlink_components(
            root, world.output_dir, field=f"{world.world_id} output_dir"
        )
    return worlds, _sha256_bytes(_canonical_json(payload))


def _stage_identity(stage: Stage) -> dict[str, Any]:
    argv = list(stage.argv)
    return {
        "name": stage.name,
        "argv": argv,
        "command_sha256": _sha256_bytes(_canonical_json(argv)),
        "bound_input_paths": list(stage.bound_inputs),
        "expected_outputs": list(stage.outputs),
    }


def _stage_bound_inputs(root: Path, world: World, stage: Stage) -> list[dict[str, Any]]:
    return [
        _declared_bound_input(
            root,
            relative,
            field=f"{world.world_id}/{stage.name} bound input",
        )
        for relative in stage.bound_inputs
    ]


def _world_identity(world: World) -> dict[str, Any]:
    return {
        "world_id": world.world_id,
        "config": world.config,
        "source_bundle": world.source_bundle,
        "output_dir": world.output_dir,
        "stages": [_stage_identity(stage) for stage in world.stages],
    }


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
    while _group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=_KILL_WAIT_SECONDS)


def _run_stage(stage: Stage, *, root: Path, timeout_seconds: float) -> dict[str, Any]:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            stage.argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )
        try:
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                _terminate_process_group(process)
                raise _StageTimeout(f"stage timed out: {stage.name}") from error
            if _group_exists(process.pid):
                _terminate_process_group(process)
                raise RuntimeError(f"stage left background processes: {stage.name}")
        except BaseException:
            if process.poll() is None or _group_exists(process.pid):
                _terminate_process_group(process)
            raise
        stdout.seek(0)
        stderr.seek(0)
        result = {
            **_stage_identity(stage),
            "exit_code": exit_code,
            "stdout_sha256": _sha256_bytes(stdout.read()),
            "stderr_sha256": _sha256_bytes(stderr.read()),
        }
    if exit_code != 0:
        raise RuntimeError(f"stage failed: {stage.name} (exit code {exit_code})")
    return result


def _safe_output_root(root: Path, world: World, *, must_exist: bool) -> Path:
    output = _reject_symlink_components(
        root, world.output_dir, field=f"{world.world_id} output_dir"
    )
    if not output.exists():
        if must_exist:
            raise ValueError("pipeline did not create output_dir")
        return output
    if output.is_symlink() or not output.is_dir() or output.resolve() != output:
        raise ValueError("output_dir is not a safe directory")
    return output


def _reserve_output_root(root: Path, world: World) -> Path:
    current = root
    parts = Path(world.output_dir).parts
    for part in parts[:-1]:
        current /= part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir() or current.resolve() != current:
            raise ValueError("output_dir contains an unsafe parent")
    output = current / parts[-1]
    try:
        output.mkdir()
    except FileExistsError as error:
        raise ValueError("output_dir already exists; refusing to overwrite") from error
    if output.is_symlink() or output.resolve() != output:
        raise ValueError("output_dir is not a safe directory")
    return output


def _artifact_inventory(output: Path) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for current, directory_names, file_names in os.walk(output, followlinks=False):
        current_path = Path(current)
        for name in [*directory_names, *file_names]:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"output artifact contains a symlink: {path}")
        for name in sorted(file_names):
            path = current_path / name
            relative = path.relative_to(output).as_posix()
            if relative == RECEIPT_NAME:
                continue
            inventory[relative] = _hash_regular_file(path, label="output artifact")
    return dict(sorted(inventory.items()))


def _verify_expected_outputs(output: Path, world: World) -> None:
    for stage in world.stages:
        for relative in stage.outputs:
            path = _reject_symlink_components(
                output, relative, field=f"{world.world_id}/{stage.name} output"
            )
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"missing expected output: {stage.name}/{relative}")


def _stage_output_inventory(output: Path, stage: Stage) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for relative in stage.outputs:
        path = _reject_symlink_components(
            output, relative, field=f"{stage.name} output"
        )
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing expected output: {stage.name}/{relative}")
        inventory[relative] = _hash_regular_file(path, label=f"{stage.name} output")
    return inventory


def _receipt_digest(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    return _sha256_bytes(_canonical_json(unsigned))


def _write_receipt(output: Path, payload: dict[str, Any]) -> None:
    path = output / RECEIPT_NAME
    raw = _canonical_json(payload) + b"\n"
    try:
        descriptor = os.open(path, _CREATE_FLAGS, 0o600)
    except OSError as error:
        raise ValueError("receipt already exists or is unsafe") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _read_receipt(output: Path) -> dict[str, Any]:
    path = output / RECEIPT_NAME
    raw = _read_regular_file(path, limit=_MAX_RECEIPT_BYTES, label="batch receipt")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("receipt is not valid UTF-8 JSON") from error
    expected = {
        "schema_version",
        "status",
        "trust_scope",
        "production_isolation",
        "receipt_authenticity",
        "world_id",
        "world_spec_sha256",
        "inputs",
        "commands",
        "artifacts",
        "receipt_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("receipt fields are invalid")
    if payload.get("receipt_sha256") != _receipt_digest(payload):
        raise ValueError("receipt digest mismatch")
    if (
        payload.get("trust_scope") != "local_engineering"
        or payload.get("production_isolation") is not False
        or payload.get("receipt_authenticity") != "content_hash_only"
    ):
        raise ValueError("receipt trust boundary is invalid")
    return payload


def _current_inputs(root: Path, world: World) -> dict[str, dict[str, Any]]:
    return {
        "config": _bound_input(root, world.config, field=f"{world.world_id} config"),
        "source_bundle": _bound_input(
            root, world.source_bundle, field=f"{world.world_id} source_bundle"
        ),
    }


def _resume_world(root: Path, world: World) -> dict[str, str]:
    output = _safe_output_root(root, world, must_exist=True)
    receipt = _read_receipt(output)
    identity = _world_identity(world)
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("status") != "success"
        or receipt.get("world_id") != world.world_id
        or receipt.get("world_spec_sha256") != _sha256_bytes(_canonical_json(identity))
    ):
        raise ValueError("receipt world identity mismatch")
    if receipt.get("inputs") != _current_inputs(root, world):
        raise ValueError("receipt input bindings mismatch")
    expected_commands = [_stage_identity(stage) for stage in world.stages]
    recorded_commands = receipt.get("commands")
    if not isinstance(recorded_commands, list) or len(recorded_commands) != len(
        expected_commands
    ):
        raise ValueError("receipt command bindings mismatch")
    for stage, expected, recorded in zip(
        world.stages, expected_commands, recorded_commands, strict=True
    ):
        expected_fields = set(expected) | {
            "bound_inputs",
            "output_artifacts",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
        }
        if (
            not isinstance(recorded, dict)
            or any(recorded.get(key) != value for key, value in expected.items())
            or set(recorded) != expected_fields
        ):
            raise ValueError("receipt command bindings mismatch")
        if recorded.get("bound_inputs") != _stage_bound_inputs(root, world, stage):
            raise ValueError("receipt bound input bindings mismatch")
        if recorded.get("output_artifacts") != _stage_output_inventory(output, stage):
            raise ValueError("receipt stage output bindings mismatch")
        if (
            recorded.get("exit_code") != 0
            or _SHA256.fullmatch(str(recorded.get("stdout_sha256") or "")) is None
            or _SHA256.fullmatch(str(recorded.get("stderr_sha256") or "")) is None
        ):
            raise ValueError("receipt command result is invalid")
    _verify_expected_outputs(output, world)
    if receipt.get("artifacts") != _artifact_inventory(output):
        raise ValueError("artifact inventory mismatch")
    return {
        "world_id": world.world_id,
        "status": "resumed",
        "receipt_sha256": str(receipt["receipt_sha256"]),
    }


def _execute_world(
    root: Path, world: World, *, timeout_seconds: float, resume: bool
) -> dict[str, str]:
    try:
        output = _safe_output_root(root, world, must_exist=False)
        if output.exists():
            if not resume:
                raise ValueError("output_dir already exists; refusing to overwrite")
            return _resume_world(root, world)
        inputs_before = _current_inputs(root, world)
        stage_inputs_before = {
            stage.name: _stage_bound_inputs(root, world, stage)
            for stage in world.stages
        }
        _reserve_output_root(root, world)
        output = _safe_output_root(root, world, must_exist=True)
        commands: list[dict[str, Any]] = []
        prior_outputs: dict[str, dict[str, dict[str, Any]]] = {}
        for stage in world.stages:
            if (
                _stage_bound_inputs(root, world, stage)
                != stage_inputs_before[stage.name]
            ):
                raise ValueError(f"bound inputs changed before stage: {stage.name}")
            command = _run_stage(stage, root=root, timeout_seconds=timeout_seconds)
            if (
                _stage_bound_inputs(root, world, stage)
                != stage_inputs_before[stage.name]
            ):
                raise ValueError(f"bound inputs changed during stage: {stage.name}")
            current_outputs = _stage_output_inventory(output, stage)
            for prior_name, prior_inventory in prior_outputs.items():
                prior_stage = next(
                    item for item in world.stages if item.name == prior_name
                )
                if _stage_output_inventory(output, prior_stage) != prior_inventory:
                    raise ValueError(f"stage mutated prior outputs: {stage.name}")
            command["bound_inputs"] = stage_inputs_before[stage.name]
            command["output_artifacts"] = current_outputs
            commands.append(command)
            prior_outputs[stage.name] = current_outputs
        _verify_expected_outputs(output, world)
        inputs_after = _current_inputs(root, world)
        if inputs_before != inputs_after:
            raise ValueError("bound inputs changed during execution")
        for stage in world.stages:
            if (
                _stage_bound_inputs(root, world, stage)
                != stage_inputs_before[stage.name]
            ):
                raise ValueError(f"bound inputs changed during execution: {stage.name}")
        identity = _world_identity(world)
        receipt: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA,
            "status": "success",
            "trust_scope": "local_engineering",
            "production_isolation": False,
            "receipt_authenticity": "content_hash_only",
            "world_id": world.world_id,
            "world_spec_sha256": _sha256_bytes(_canonical_json(identity)),
            "inputs": inputs_after,
            "commands": commands,
            "artifacts": _artifact_inventory(output),
        }
        receipt["receipt_sha256"] = _receipt_digest(receipt)
        _write_receipt(output, receipt)
        if _read_receipt(output) != receipt or receipt[
            "artifacts"
        ] != _artifact_inventory(output):
            (output / RECEIPT_NAME).unlink(missing_ok=True)
            raise ValueError("output artifacts changed while writing receipt")
        return {
            "world_id": world.world_id,
            "status": "success",
            "receipt_sha256": str(receipt["receipt_sha256"]),
        }
    except (
        BatchManifestError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        return {"world_id": world.world_id, "status": "failed", "error": str(error)}


def run_batch(
    manifest_path: Path,
    *,
    workspace_root: Path = ROOT,
    workers: int = 1,
    timeout_seconds: float = 3600.0,
    execute: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    """Validate and optionally execute one explicit pipeline per world."""
    if (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= _MAX_WORKERS
    ):
        raise BatchManifestError(f"workers must be between 1 and {_MAX_WORKERS}")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
    ):
        raise BatchManifestError(
            f"timeout_seconds must be in (0, {_MAX_TIMEOUT_SECONDS}]"
        )
    if resume and not execute:
        raise BatchManifestError("--resume requires --execute")
    root = _workspace_root(workspace_root)
    worlds, manifest_sha256 = _load_manifest(manifest_path, root)
    if not execute:
        return {
            "schema_version": RUN_SCHEMA,
            "status": "planned",
            "manifest_sha256": manifest_sha256,
            "trust_scope": "local_engineering",
            "production_isolation": False,
            "worlds": [
                {"world_id": world.world_id, "status": "planned"} for world in worlds
            ],
        }

    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(worlds))) as executor:
        futures = {
            executor.submit(
                _execute_world,
                root,
                world,
                timeout_seconds=float(timeout_seconds),
                resume=resume,
            ): world.world_id
            for world in worlds
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["world_id"])
    return {
        "schema_version": RUN_SCHEMA,
        "status": (
            "success"
            if all(item["status"] in {"success", "resumed"} for item in results)
            else "failed"
        ),
        "manifest_sha256": manifest_sha256,
        "trust_scope": "local_engineering",
        "production_isolation": False,
        "worlds": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        result = run_batch(
            args.manifest,
            workspace_root=args.workspace_root,
            workers=args.workers,
            timeout_seconds=args.timeout_seconds,
            execute=args.execute,
            resume=args.resume,
        )
    except BatchManifestError as error:
        raise SystemExit(f"batch manifest failed: {error}") from error
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
