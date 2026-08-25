from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from longworld.core.external_eval import (
    EXTERNAL_BENCHMARKS,
    EXTERNAL_RUNNER_SOURCES,
    execute_external_eval_suite,
)


def _git_runner(tmp_path: Path, source_ref: str) -> tuple[Path, str]:
    runner = tmp_path / "runner"
    runner.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(runner)], check=True)
    subprocess.run(
        ["git", "-C", str(runner), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(runner), "config", "user.name", "LongWorld Test"], check=True
    )
    (runner / "README.md").write_text("fixture")
    subprocess.run(["git", "-C", str(runner), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(runner), "commit", "-q", "-m", "fixture"], check=True
    )
    subprocess.run(
        ["git", "-C", str(runner), "remote", "add", "origin", source_ref],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(runner), "rev-parse", "HEAD"], text=True
    ).strip()
    return runner, revision


def _entry(name: str, runner: Path, revision: str, *, enabled: bool) -> dict:
    return {
        "name": name,
        "source_ref": EXTERNAL_BENCHMARKS[name],
        "source_revision": revision,
        "runner_source_ref": EXTERNAL_RUNNER_SOURCES[name],
        "runner_dir": str(runner),
        "runner_revision": revision,
        "enabled": enabled,
        "argv": [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "Path(r'{output_dir}/score.json').write_text('score')"
            ),
        ],
        "output_paths": ["score.json"],
    }


def test_external_eval_preflight_lists_all_required_benchmarks(tmp_path: Path) -> None:
    runners = {
        name: _git_runner(tmp_path / name, EXTERNAL_RUNNER_SOURCES[name])
        for name in EXTERNAL_BENCHMARKS
    }
    suite = {
        "schema_version": "longworld-external-eval-suite-v1",
        "benchmarks": [
            _entry(name, *runners[name], enabled=False) for name in EXTERNAL_BENCHMARKS
        ],
    }

    receipt = execute_external_eval_suite(
        suite,
        model_id="Qwen/Qwen3.5-4B",
        model_revision="a" * 40,
        output_dir=tmp_path / "out",
        execute=False,
        require_complete=True,
    )

    assert receipt["status"] == "planned"
    assert {item["name"] for item in receipt["benchmarks"]} == set(EXTERNAL_BENCHMARKS)
    assert all(item["status"] == "planned" for item in receipt["benchmarks"])


def test_external_eval_execution_hashes_outputs_and_pins_runner(tmp_path: Path) -> None:
    runner, revision = _git_runner(tmp_path, EXTERNAL_BENCHMARKS["ruler"])
    suite = {
        "schema_version": "longworld-external-eval-suite-v1",
        "benchmarks": [_entry("ruler", runner, revision, enabled=True)],
    }

    receipt = execute_external_eval_suite(
        suite,
        model_id="local-model",
        model_revision="b" * 40,
        output_dir=tmp_path / "out",
        execute=True,
        require_complete=False,
    )

    result = receipt["benchmarks"][0]
    assert receipt["status"] == "completed"
    assert result["status"] == "completed"
    assert len(result["stdout_sha256"]) == 64
    assert len(result["output_sha256"]["score.json"]) == 64


def test_external_eval_rejects_unpinned_runner_and_path_escape(tmp_path: Path) -> None:
    runner, revision = _git_runner(tmp_path, EXTERNAL_BENCHMARKS["ruler"])
    entry = _entry("ruler", runner, "0" * 40, enabled=True)
    suite = {
        "schema_version": "longworld-external-eval-suite-v1",
        "benchmarks": [entry],
    }
    with pytest.raises(ValueError, match="runner revision"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=tmp_path / "out",
            execute=True,
            require_complete=False,
        )

    entry["runner_revision"] = revision
    entry["output_paths"] = ["../escape.json"]
    with pytest.raises(ValueError, match="output path"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=tmp_path / "out",
            execute=True,
            require_complete=False,
        )


def test_external_eval_rejects_dirty_or_wrong_origin_runner(tmp_path: Path) -> None:
    runner, revision = _git_runner(tmp_path, EXTERNAL_BENCHMARKS["ruler"])
    suite = {
        "schema_version": "longworld-external-eval-suite-v1",
        "benchmarks": [_entry("ruler", runner, revision, enabled=True)],
    }
    (runner / "README.md").write_text("modified")
    with pytest.raises(ValueError, match="clean"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=tmp_path / "out",
            execute=True,
            require_complete=False,
        )

    subprocess.run(["git", "-C", str(runner), "restore", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(runner),
            "remote",
            "set-url",
            "origin",
            "https://evil.invalid/runner",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="origin"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=tmp_path / "out",
            execute=True,
            require_complete=False,
        )


def test_external_eval_does_not_inherit_secrets_or_accept_symlink_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, revision = _git_runner(tmp_path, EXTERNAL_BENCHMARKS["ruler"])
    entry = _entry("ruler", runner, revision, enabled=True)
    entry["argv"] = [
        sys.executable,
        "-c",
        (
            "import os; from pathlib import Path; "
            "Path(r'{output_dir}/score.json').write_text(os.getenv('LONGWORLD_SENTINEL', 'absent'))"
        ),
    ]
    suite = {
        "schema_version": "longworld-external-eval-suite-v1",
        "benchmarks": [entry],
    }
    monkeypatch.setenv("LONGWORLD_SENTINEL", "secret")
    execute_external_eval_suite(
        suite,
        model_id="local-model",
        model_revision="b" * 40,
        output_dir=tmp_path / "out",
        execute=True,
        require_complete=False,
    )
    assert (tmp_path / "out" / "ruler" / "score.json").read_text() == "absent"

    escape = tmp_path / "outside.json"
    benchmark_dir = tmp_path / "symlink-out" / "ruler"
    benchmark_dir.mkdir(parents=True)
    (benchmark_dir / "score.json").symlink_to(escape)
    with pytest.raises(ValueError, match="regular file|escapes root"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=tmp_path / "symlink-out",
            execute=True,
            require_complete=False,
        )

    generated_root = tmp_path / "generated-symlink-out"
    escape.write_text("outside")
    entry["argv"] = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"Path(r'{{output_dir}}/score.json').symlink_to(Path(r'{escape}'))"
        ),
    ]
    with pytest.raises(ValueError, match="regular file|escapes root|safely"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=generated_root,
            execute=True,
            require_complete=False,
        )


def test_external_eval_rejects_symlink_benchmark_directory(tmp_path: Path) -> None:
    runner, revision = _git_runner(tmp_path, EXTERNAL_BENCHMARKS["ruler"])
    suite = {
        "schema_version": "longworld-external-eval-suite-v1",
        "benchmarks": [_entry("ruler", runner, revision, enabled=True)],
    }
    output_dir = tmp_path / "out"
    outside = tmp_path / "outside"
    output_dir.mkdir()
    outside.mkdir()
    (output_dir / "ruler").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="directory.*symlink|symlink.*directory"):
        execute_external_eval_suite(
            suite,
            model_id="local-model",
            model_revision="b" * 40,
            output_dir=output_dir,
            execute=True,
            require_complete=False,
        )
