import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_source_taskbank_pipeline as pipeline


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts/native.py"
    script.write_text("# Native fixture stage.\n")
    config = tmp_path / "config.json"
    config.write_text("{}\n")
    spec = {
        "job_id": "native",
        "script": "scripts/native.py",
        "config": "config.json",
        "output": "out",
        "source_authority": "official_hash_pinned",
        "receipt_schema": "native-proof-v1",
        "input_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (script, config)
        },
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": pipeline.SCHEMA, "jobs": [spec]}))
    return catalog, spec


def test_build_then_replay_and_resume_do_not_infer_quality(job, tmp_path, monkeypatch):
    catalog, _ = job
    stages = []
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY", "test-source-secret")

    def run(argv, **kwargs):
        if "LONGWORLD_SOURCE_ATTESTATION_KEY" in kwargs["env"]:
            raise AssertionError("credentials reached unsigned native verifier")
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(exist_ok=True)
        (output / "BUILD_RECEIPT.json").write_text(
            json.dumps({"schema_version": "native-proof-v1", "strict_claim": True})
        )
        stages.append("--validate" in argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    first = pipeline.run_pipeline(catalog, tmp_path / "logs1", 1)
    assert stages == [False, True]
    assert first["jobs"][0]["strict_eligibility_inferred"] is False
    pipeline.run_pipeline(catalog, tmp_path / "logs2", 1)
    assert stages == [False, True, True]


def test_changed_input_blocks_successful_child(job, tmp_path, monkeypatch):
    catalog, _ = job

    def run(argv, **kwargs):
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        (out / "BUILD_RECEIPT.json").write_text('{"schema_version":"native-proof-v1"}')
        (tmp_path / "config.json").write_text('{"changed":true}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    result = pipeline.run_pipeline(catalog, tmp_path / "logs", 1)
    assert result["jobs"][0]["status"] == "failed"


def test_incomplete_output_is_not_overwritten(job, tmp_path, monkeypatch):
    catalog, _ = job
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "partial").write_text("keep")

    def run(*args, **kwargs):
        raise AssertionError("incomplete output must not be regenerated in place")

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    result = pipeline.run_pipeline(catalog, tmp_path / "logs", 1)
    assert result["jobs"][0]["status"] == "held_incomplete_output"
    assert (tmp_path / "out" / "partial").read_text() == "keep"


def test_duplicate_output_jobs_rejected(job, tmp_path):
    catalog, spec = job
    catalog.write_text(
        json.dumps(
            {
                "schema_version": pipeline.SCHEMA,
                "jobs": [spec, {**spec, "job_id": "other"}],
            }
        )
    )
    with pytest.raises(ValueError, match="share output"):
        pipeline.run_pipeline(catalog, tmp_path / "logs", 2)


def test_receipt_symlink_rejected(job, tmp_path, monkeypatch):
    catalog, _ = job
    out = tmp_path / "out"
    out.mkdir()
    external = tmp_path / "external.json"
    external.write_text('{"schema_version":"native-proof-v1"}')
    (out / "BUILD_RECEIPT.json").symlink_to(external)
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    result = pipeline.run_pipeline(catalog, tmp_path / "logs", 1)
    assert result["jobs"][0]["status"] == "failed"
