from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import scripts.run_p12_world_batch as batch_runner
from scripts.run_p12_world_batch import BatchManifestError, run_batch


def _write_inputs(root: Path, world_id: str) -> tuple[str, str]:
    config = root / "configs" / f"{world_id}.yaml"
    source = root / "sources" / f"{world_id}.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"world: {world_id}\n", encoding="utf-8")
    source.write_text(json.dumps({"world": world_id}), encoding="utf-8")
    return config.relative_to(root).as_posix(), source.relative_to(root).as_posix()


def _world(
    root: Path,
    world_id: str,
    *,
    exit_code: int = 0,
    delay: float = 0.0,
) -> dict[str, object]:
    config, source = _write_inputs(root, world_id)
    output = f"outputs/{world_id}"
    code = (
        "import pathlib,time,sys;"
        f"time.sleep({delay!r});"
        f"p=pathlib.Path({output!r});p.mkdir(parents=True,exist_ok=True);"
        f"(p/'result.txt').write_text({world_id!r});"
        f"sys.exit({exit_code})"
    )
    return {
        "world_id": world_id,
        "config": config,
        "source_bundle": source,
        "output_dir": output,
        "stages": [
            {
                "name": "pipeline",
                "argv": [sys.executable, "-c", code],
                "bound_inputs": [config],
                "outputs": ["result.txt"],
            }
        ],
    }


def _manifest(root: Path, worlds: list[dict[str, object]]) -> Path:
    path = root / "batch.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld-p12-world-batch-manifest-v1",
                "worlds": worlds,
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(
    root: Path,
    manifest: Path,
    *,
    workers: int,
    timeout_seconds: float = 5.0,
    resume: bool = False,
) -> dict[str, object]:
    return run_batch(
        manifest,
        workspace_root=root,
        workers=workers,
        timeout_seconds=timeout_seconds,
        execute=True,
        resume=resume,
    )


def test_serial_and_parallel_execution_have_stable_result_order(tmp_path: Path) -> None:
    serial_root = tmp_path / "serial"
    parallel_root = tmp_path / "parallel"
    serial_root.mkdir()
    parallel_root.mkdir()
    serial_manifest = _manifest(
        serial_root,
        [
            _world(serial_root, "world-b", delay=0.01),
            _world(serial_root, "world-a", delay=0.02),
        ],
    )
    parallel_manifest = _manifest(
        parallel_root,
        [
            _world(parallel_root, "world-b", delay=0.01),
            _world(parallel_root, "world-a", delay=0.02),
        ],
    )

    serial = _run(serial_root, serial_manifest, workers=1)
    parallel = _run(parallel_root, parallel_manifest, workers=2)

    assert serial == parallel
    assert [item["world_id"] for item in serial["worlds"]] == [
        "world-a",
        "world-b",
    ]


def test_failed_world_does_not_cancel_or_promote_successful_world(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        tmp_path,
        [_world(tmp_path, "bad", exit_code=7), _world(tmp_path, "good")],
    )

    result = _run(tmp_path, manifest, workers=2)

    assert result["status"] == "failed"
    assert {item["world_id"]: item["status"] for item in result["worlds"]} == {
        "bad": "failed",
        "good": "success",
    }
    assert not (tmp_path / "outputs/bad/.longworld_batch_receipt.json").exists()
    assert (tmp_path / "outputs/good/.longworld_batch_receipt.json").is_file()


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    config, source = _write_inputs(tmp_path, "timeout")
    output = "outputs/timeout"
    heartbeat = tmp_path / "heartbeat.txt"
    child_code = (
        "import pathlib,signal,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"p=pathlib.Path({str(heartbeat)!r});"
        "[(p.write_text(str(i)),time.sleep(.02)) for i in range(10000)]"
    )
    parent_code = (
        "import pathlib,subprocess,sys,time;"
        f"pathlib.Path({output!r}).mkdir(parents=True,exist_ok=True);"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "time.sleep(1000)"
    )
    manifest = _manifest(
        tmp_path,
        [
            {
                "world_id": "timeout",
                "config": config,
                "source_bundle": source,
                "output_dir": output,
                "stages": [
                    {
                        "name": "hang",
                        "argv": [sys.executable, "-c", parent_code],
                        "bound_inputs": [config],
                        "outputs": ["never.txt"],
                    }
                ],
            }
        ],
    )

    result = _run(tmp_path, manifest, workers=1, timeout_seconds=0.2)
    assert result["worlds"][0]["status"] == "failed"
    assert result["worlds"][0]["error"] == "stage timed out: hang"
    deadline = time.monotonic() + 2
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert heartbeat.exists()
    before = heartbeat.stat().st_mtime_ns
    time.sleep(0.15)
    assert heartbeat.stat().st_mtime_ns == before


@pytest.mark.parametrize("field", ["config", "source_bundle"])
def test_symlink_input_is_rejected(tmp_path: Path, field: str) -> None:
    world = _world(tmp_path, "linked")
    target = tmp_path / str(world[field])
    real = target.with_suffix(target.suffix + ".real")
    target.rename(real)
    target.symlink_to(real)
    manifest = _manifest(tmp_path, [world])

    with pytest.raises(BatchManifestError, match="symlink"):
        run_batch(manifest, workspace_root=tmp_path)


def test_output_symlink_and_escaping_paths_are_rejected(tmp_path: Path) -> None:
    world = _world(tmp_path, "linked-output")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outputs").mkdir()
    (tmp_path / str(world["output_dir"])).symlink_to(tmp_path / "outside")
    manifest = _manifest(tmp_path, [world])
    with pytest.raises(BatchManifestError, match="symlink"):
        run_batch(manifest, workspace_root=tmp_path)

    world["output_dir"] = "../outside"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "longworld-p12-world-batch-manifest-v1",
                "worlds": [world],
            }
        )
    )
    with pytest.raises(BatchManifestError, match="safe relative path"):
        run_batch(manifest, workspace_root=tmp_path)


@pytest.mark.parametrize("duplicate", ["world_id", "output_dir"])
def test_duplicate_world_or_output_is_rejected(tmp_path: Path, duplicate: str) -> None:
    first = _world(tmp_path, "first")
    second = _world(tmp_path, "second")
    second[duplicate] = first[duplicate]
    manifest = _manifest(tmp_path, [first, second])

    with pytest.raises(BatchManifestError, match="duplicate"):
        run_batch(manifest, workspace_root=tmp_path)


def test_nested_output_directories_are_rejected(tmp_path: Path) -> None:
    first = _world(tmp_path, "first")
    second = _world(tmp_path, "second")
    second["output_dir"] = f"{first['output_dir']}/nested"
    manifest = _manifest(tmp_path, [first, second])

    with pytest.raises(BatchManifestError, match="overlapping output_dir"):
        run_batch(manifest, workspace_root=tmp_path)


def test_stages_cannot_declare_the_same_output_twice(tmp_path: Path) -> None:
    world = _world(tmp_path, "duplicate-artifact")
    world["stages"].append(
        {
            "name": "second",
            "argv": [sys.executable, "-c", "raise SystemExit(0)"],
            "bound_inputs": [str(world["config"])],
            "outputs": ["result.txt"],
        }
    )
    manifest = _manifest(tmp_path, [world])

    with pytest.raises(BatchManifestError, match="duplicate stage output"):
        run_batch(manifest, workspace_root=tmp_path)


def test_resume_requires_exact_receipt_inputs_commands_and_artifacts(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, "resume")
    manifest = _manifest(tmp_path, [world])
    assert _run(tmp_path, manifest, workers=1)["status"] == "success"
    resumed = _run(tmp_path, manifest, workers=1, resume=True)
    assert resumed["status"] == "success"
    assert resumed["worlds"][0]["status"] == "resumed"

    config = tmp_path / str(world["config"])
    original_config = config.read_text(encoding="utf-8")
    config.write_text("changed: true\n", encoding="utf-8")
    rejected = _run(tmp_path, manifest, workers=1, resume=True)
    assert rejected["status"] == "failed"
    assert "input bindings mismatch" in rejected["worlds"][0]["error"]
    config.write_text(original_config, encoding="utf-8")

    world["stages"][0]["argv"].append("changed-command")
    manifest = _manifest(tmp_path, [world])
    rejected = _run(tmp_path, manifest, workers=1, resume=True)
    assert rejected["status"] == "failed"
    assert "world identity mismatch" in rejected["worlds"][0]["error"]
    world["stages"][0]["argv"].pop()
    manifest = _manifest(tmp_path, [world])

    artifact = tmp_path / "outputs/resume/result.txt"
    artifact.write_text("tampered", encoding="utf-8")
    rejected = _run(tmp_path, manifest, workers=1, resume=True)
    assert rejected["status"] == "failed"
    assert "stage output bindings mismatch" in rejected["worlds"][0]["error"]


def test_resume_rejects_a_tampered_receipt(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_world(tmp_path, "receipt")])
    assert _run(tmp_path, manifest, workers=1)["status"] == "success"
    receipt_path = tmp_path / "outputs/receipt/.longworld_batch_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["commands"][0]["command_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    rejected = _run(tmp_path, manifest, workers=1, resume=True)
    assert rejected["status"] == "failed"
    assert rejected["worlds"][0]["error"] == "receipt digest mismatch"


def test_resume_rejects_changed_bound_script_with_unchanged_argv(
    tmp_path: Path,
) -> None:
    config, source = _write_inputs(tmp_path, "script-bound")
    script = tmp_path / "tools/driver.py"
    script.parent.mkdir()
    script.write_text(
        "from pathlib import Path\n"
        "p = Path('outputs/script-bound')\n"
        "p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'result.txt').write_text('v1')\n",
        encoding="utf-8",
    )
    world = {
        "world_id": "script-bound",
        "config": config,
        "source_bundle": source,
        "output_dir": "outputs/script-bound",
        "stages": [
            {
                "name": "pipeline",
                "argv": [sys.executable, "tools/driver.py"],
                "bound_inputs": [
                    "tools/driver.py",
                    str(Path(sys.executable).resolve()),
                ],
                "outputs": ["result.txt"],
            }
        ],
    }
    manifest = _manifest(tmp_path, [world])
    assert _run(tmp_path, manifest, workers=1)["status"] == "success"

    script.write_text(script.read_text(encoding="utf-8") + "# changed\n")
    rejected = _run(tmp_path, manifest, workers=1, resume=True)

    assert rejected["status"] == "failed"
    assert "bound input bindings mismatch" in rejected["worlds"][0]["error"]


def test_later_stage_cannot_backfill_an_earlier_stage_output(tmp_path: Path) -> None:
    config, source = _write_inputs(tmp_path, "backfill")
    output = "outputs/backfill"
    world = {
        "world_id": "backfill",
        "config": config,
        "source_bundle": source,
        "output_dir": output,
        "stages": [
            {
                "name": "first",
                "argv": [sys.executable, "-c", "raise SystemExit(0)"],
                "bound_inputs": [config],
                "outputs": ["first.txt"],
            },
            {
                "name": "second",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib;"
                        f"p=pathlib.Path({output!r});"
                        "(p/'first.txt').write_text('late');"
                        "(p/'second.txt').write_text('second')"
                    ),
                ],
                "bound_inputs": [config],
                "outputs": ["second.txt"],
            },
        ],
    }
    manifest = _manifest(tmp_path, [world])

    result = _run(tmp_path, manifest, workers=1)

    assert result["status"] == "failed"
    assert result["worlds"][0]["error"] == "missing expected output: first/first.txt"
    assert not (tmp_path / output / ".longworld_batch_receipt.json").exists()


def test_later_stage_cannot_mutate_an_earlier_stage_output(tmp_path: Path) -> None:
    config, source = _write_inputs(tmp_path, "mutate-prior")
    output = "outputs/mutate-prior"
    first_code = (
        "import pathlib;"
        f"p=pathlib.Path({output!r});"
        "(p/'first.txt').write_text('original')"
    )
    second_code = (
        "import pathlib;"
        f"p=pathlib.Path({output!r});"
        "(p/'first.txt').write_text('mutated');"
        "(p/'second.txt').write_text('second')"
    )
    world = {
        "world_id": "mutate-prior",
        "config": config,
        "source_bundle": source,
        "output_dir": output,
        "stages": [
            {
                "name": "first",
                "argv": [sys.executable, "-c", first_code],
                "bound_inputs": [config],
                "outputs": ["first.txt"],
            },
            {
                "name": "second",
                "argv": [sys.executable, "-c", second_code],
                "bound_inputs": [config],
                "outputs": ["second.txt"],
            },
        ],
    }
    manifest = _manifest(tmp_path, [world])

    result = _run(tmp_path, manifest, workers=1)

    assert result["status"] == "failed"
    assert result["worlds"][0]["error"] == "stage mutated prior outputs: second"


def test_cleanup_timeout_is_isolated_as_a_world_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _world(tmp_path, "bad-cleanup")
    bad["stages"][0]["argv"] = [sys.executable, "-c", "import time;time.sleep(.5)"]
    manifest = _manifest(tmp_path, [bad, _world(tmp_path, "good-cleanup")])

    def fail_cleanup(_process: object) -> None:
        raise subprocess.TimeoutExpired("cleanup", 0.01)

    monkeypatch.setattr(batch_runner, "_terminate_process_group", fail_cleanup)
    result = _run(tmp_path, manifest, workers=2, timeout_seconds=0.15)

    assert result["status"] == "failed"
    assert {item["world_id"]: item["status"] for item in result["worlds"]} == {
        "bad-cleanup": "failed",
        "good-cleanup": "success",
    }


def test_default_mode_is_dry_run_and_does_not_create_outputs(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_world(tmp_path, "planned")])

    result = run_batch(manifest, workspace_root=tmp_path, workers=2)

    assert result["status"] == "planned"
    assert len(result["manifest_sha256"]) == 64
    assert result["production_isolation"] is False
    assert result["worlds"] == [{"status": "planned", "world_id": "planned"}]
    assert not (tmp_path / "outputs").exists()


def test_shell_executable_is_rejected(tmp_path: Path) -> None:
    world = _world(tmp_path, "shell")
    world["stages"][0]["argv"] = ["/bin/sh", "-c", "true"]
    manifest = _manifest(tmp_path, [world])

    with pytest.raises(BatchManifestError, match="shell executables"):
        run_batch(manifest, workspace_root=tmp_path)
