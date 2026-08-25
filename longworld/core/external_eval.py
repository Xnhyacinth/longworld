"""Auditable execution receipts for external long-context benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

EXTERNAL_BENCHMARKS = {
    "ruler": "https://github.com/NVIDIA/RULER",
    "longbench_v2": "https://github.com/THUDM/LongBench",
    "mrcr": "https://huggingface.co/datasets/openai/mrcr",
    "graphwalks": "https://huggingface.co/datasets/openai/graphwalks",
    "helmet": "https://github.com/princeton-nlp/HELMET",
}
LONGWORLD_RUNNER_SOURCE = "https://github.com/Xnhyacinth/longworld"
EXTERNAL_RUNNER_SOURCES = {
    "ruler": EXTERNAL_BENCHMARKS["ruler"],
    "longbench_v2": EXTERNAL_BENCHMARKS["longbench_v2"],
    "mrcr": LONGWORLD_RUNNER_SOURCE,
    "graphwalks": LONGWORLD_RUNNER_SOURCE,
    "helmet": EXTERNAL_BENCHMARKS["helmet"],
}
EXTERNAL_EVAL_SCHEMA = "longworld-external-eval-suite-v1"
_REVISION = re.compile(r"[0-9a-f]{40,64}")
_MAX_RESULT_BYTES = 256_000_000
_SAFE_ENVIRONMENT_NAMES = (
    "PATH",
    "LD_LIBRARY_PATH",
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_output_path(root: Path, relative: object) -> Path:
    text = str(relative or "")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"external benchmark output path is unsafe: {text!r}")
    resolved = (root / path).resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise ValueError(f"external benchmark output path escapes root: {text!r}")
    return resolved


def _normalized_git_url(value: str) -> str:
    return value.removesuffix(".git").rstrip("/")


def _git_output(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _runner_revision(path: Path, expected_origin: str) -> str:
    if not path.is_dir():
        raise ValueError(f"external benchmark runner directory is missing: {path}")
    try:
        revision = _git_output(path, "rev-parse", "HEAD")
        origin = _git_output(path, "remote", "get-url", "origin")
        dirty = _git_output(path, "status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(
            f"external benchmark runner is not a git checkout: {path}"
        ) from error
    if _normalized_git_url(origin) != _normalized_git_url(expected_origin):
        raise ValueError("external benchmark runner origin does not match its pin")
    if dirty:
        raise ValueError("external benchmark runner checkout must be clean")
    return revision


def _safe_environment(temporary_home: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in _SAFE_ENVIRONMENT_NAMES
        if os.environ.get(name)
    }
    environment.update(
        {
            "HOME": str(temporary_home),
            "XDG_CACHE_HOME": str(temporary_home / "cache"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
        }
    )
    return environment


def _hash_file(path: Path, *, maximum_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(
            f"external benchmark output cannot be opened safely: {path}"
        ) from error
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError(
                f"external benchmark output must be a regular file: {path}"
            )
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError(f"external benchmark output is too large: {path}")
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_output(path: Path, root: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"external benchmark output must be a regular file: {path}")
    if root.resolve() not in path.resolve().parents:
        raise ValueError(f"external benchmark output escapes root: {path}")
    path.unlink()


def _verified_output_digest(path: Path, root: Path) -> str:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"external benchmark omitted output: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"external benchmark output must be a regular file: {path}")
    if root.resolve() not in path.resolve().parents:
        raise ValueError(f"external benchmark output escapes root: {path}")
    return _hash_file(path, maximum_bytes=_MAX_RESULT_BYTES)


def _validate_entry(entry: dict[str, Any]) -> tuple[str, Path, list[str], list[str]]:
    name = str(entry.get("name") or "")
    if name not in EXTERNAL_BENCHMARKS:
        raise ValueError(f"unsupported external benchmark: {name!r}")
    if entry.get("source_ref") != EXTERNAL_BENCHMARKS[name]:
        raise ValueError(f"external benchmark source is not pinned for {name}")
    source_revision = str(entry.get("source_revision") or "")
    runner_revision = str(entry.get("runner_revision") or "")
    runner_source_ref = str(entry.get("runner_source_ref") or "")
    if (
        _REVISION.fullmatch(source_revision) is None
        or _REVISION.fullmatch(runner_revision) is None
    ):
        raise ValueError(f"external benchmark revision is invalid for {name}")
    if runner_source_ref != EXTERNAL_RUNNER_SOURCES[name]:
        raise ValueError(f"external benchmark runner source is not pinned for {name}")
    runner_dir = Path(str(entry.get("runner_dir") or ""))
    if _runner_revision(runner_dir, runner_source_ref) != runner_revision:
        raise ValueError(f"external benchmark runner revision mismatch for {name}")
    argv = entry.get("argv")
    outputs = entry.get("output_paths")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise ValueError(f"external benchmark argv is invalid for {name}")
    if (
        not isinstance(outputs, list)
        or not outputs
        or not all(isinstance(item, str) and item for item in outputs)
    ):
        raise ValueError(f"external benchmark outputs are invalid for {name}")
    return name, runner_dir, argv, outputs


def execute_external_eval_suite(
    suite: dict[str, Any],
    *,
    model_id: str,
    model_revision: str,
    output_dir: Path,
    execute: bool,
    require_complete: bool = True,
    timeout_seconds: int = 86_400,
) -> dict[str, Any]:
    """Preflight or execute fixed argv commands and hash every result artifact."""
    if suite.get("schema_version") != EXTERNAL_EVAL_SCHEMA:
        raise ValueError("external benchmark suite schema is invalid")
    if not model_id or _REVISION.fullmatch(model_revision) is None:
        raise ValueError("external benchmark model identity is not pinned")
    entries = suite.get("benchmarks")
    if not isinstance(entries, list) or not entries:
        raise ValueError("external benchmark suite is empty")
    names = [
        str(entry.get("name") or "") for entry in entries if isinstance(entry, dict)
    ]
    if len(names) != len(entries) or len(names) != len(set(names)):
        raise ValueError("external benchmark names must be unique objects")
    if require_complete and set(names) != set(EXTERNAL_BENCHMARKS):
        raise ValueError("external benchmark suite is incomplete")

    results: list[dict[str, Any]] = []
    for raw_entry in entries:
        assert isinstance(raw_entry, dict)
        name, runner_dir, raw_argv, output_paths = _validate_entry(raw_entry)
        benchmark_dir = output_dir / name
        if benchmark_dir.is_symlink():
            raise ValueError(
                f"external benchmark directory must not be a symlink: {benchmark_dir}"
            )
        checked_outputs = [
            _safe_output_path(benchmark_dir, relative) for relative in output_paths
        ]
        command = [
            value.replace("{model}", model_id)
            .replace("{model_revision}", model_revision)
            .replace("{output_dir}", str(benchmark_dir.resolve()))
            for value in raw_argv
        ]
        base = {
            "name": name,
            "source_ref": raw_entry["source_ref"],
            "source_revision": raw_entry["source_revision"],
            "runner_revision": raw_entry["runner_revision"],
            "runner_source_ref": raw_entry["runner_source_ref"],
            "command_sha256": _sha256(_canonical_json(command)),
        }
        if not execute or raw_entry.get("enabled") is not True:
            results.append({**base, "status": "planned"})
            continue

        benchmark_dir.mkdir(parents=True, exist_ok=True)
        if benchmark_dir.is_symlink() or not benchmark_dir.is_dir():
            raise ValueError(
                f"external benchmark directory must not be a symlink: {benchmark_dir}"
            )
        benchmark_root = benchmark_dir.resolve()
        for path in checked_outputs:
            _prepare_output(path, benchmark_dir)
        started = time.monotonic()
        with (
            tempfile.TemporaryDirectory(prefix="longworld-external-eval-") as home,
            tempfile.TemporaryFile() as stdout,
            tempfile.TemporaryFile() as stderr,
        ):
            completed = subprocess.run(
                command,
                cwd=runner_dir,
                env=_safe_environment(Path(home)),
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=timeout_seconds,
            )
            stdout.seek(0)
            stderr.seek(0)
            stdout_sha256 = _sha256(stdout.read())
            stderr_sha256 = _sha256(stderr.read())
        elapsed = time.monotonic() - started
        if completed.returncode != 0:
            raise RuntimeError(
                f"external benchmark {name} failed with exit code {completed.returncode}"
            )
        if (
            _runner_revision(runner_dir, raw_entry["runner_source_ref"])
            != raw_entry["runner_revision"]
        ):
            raise ValueError(f"external benchmark runner changed during {name}")
        if benchmark_dir.is_symlink() or benchmark_dir.resolve() != benchmark_root:
            raise ValueError(f"external benchmark directory changed during {name}")
        results.append(
            {
                **base,
                "status": "completed",
                "exit_code": completed.returncode,
                "wall_time_seconds": round(elapsed, 6),
                "stdout_sha256": stdout_sha256,
                "stderr_sha256": stderr_sha256,
                "output_sha256": {
                    relative: _verified_output_digest(path, benchmark_dir)
                    for relative, path in zip(
                        output_paths, checked_outputs, strict=True
                    )
                },
            }
        )

    statuses = {item["status"] for item in results}
    status = "completed" if statuses == {"completed"} else "planned"
    return {
        "schema_version": "longworld-external-eval-receipt-v1",
        "status": status,
        "model_id": model_id,
        "model_revision": model_revision,
        "suite_sha256": _sha256(_canonical_json(suite)),
        "benchmarks": results,
    }
