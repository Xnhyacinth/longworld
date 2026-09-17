import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_source_taskbank_pipeline as pipeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "catalog_name",
    (
        "p65_source_pipeline_wave1.json",
        "p65_source_pipeline_wave2.json",
        "p66_source_pipeline_wave1.json",
    ),
)
def test_repository_catalog_pins_are_relative_and_current(catalog_name):
    catalog = json.loads((ROOT / "configs" / catalog_name).read_text())
    for job in catalog["jobs"]:
        names = [job["script"], job["config"], *job.get("input_files", [])]
        if job.get("trust_file"):
            names.append("scripts/run_with_local_probe_trust.py")
        assert set(job["input_sha256"]) == set(names)
        assert job["input_sha256"] == {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in names
        }


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
            name: hashlib.sha256(p.read_bytes()).hexdigest()
            for name, p in (("scripts/native.py", script), ("config.json", config))
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
    assert first["jobs"][0]["source_authority"] == "official_hash_pinned"
    assert (
        first["orchestrator_sha256"]
        == hashlib.sha256(Path(pipeline.__file__).read_bytes()).hexdigest()
    )
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


def trust_job(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    wrapper = tmp_path / "scripts/run_with_local_probe_trust.py"
    wrapper.write_text("# Reviewed trust wrapper fixture.\n")
    monkeypatch.setattr(pipeline, "TRUST_WRAPPER", wrapper)
    script = tmp_path / "scripts/native.py"
    script.write_text("# Native fixture stage.\n")
    config = tmp_path / "config.json"
    config.write_text("{}\n")
    identity = {
        "schema_version": "longworld-local-probe-trust-v1",
        "environment": "probe",
        "probe_id": "local-probe-fixture",
        "role": "source",
        "key_id": "probe-source-fixture",
    }
    monkeypatch.setattr(
        pipeline, "local_probe_trust_identity", lambda path, role: identity
    )
    paths = (script, config, wrapper)
    spec = {
        "job_id": "trusted-native",
        "script": "scripts/native.py",
        "config": "config.json",
        "output": "out",
        "trust_file": "/private/local_probe_trust.json",
        "trust_identity": identity,
        "receipt_schema": "native-proof-v1",
        "input_sha256": {
            name: hashlib.sha256(p.read_bytes()).hexdigest()
            for name, p in zip(
                (
                    "scripts/native.py",
                    "config.json",
                    "scripts/run_with_local_probe_trust.py",
                ),
                paths,
            )
        },
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": pipeline.SCHEMA, "jobs": [spec]}))
    return catalog, wrapper, identity


def test_trust_wrapper_mutation_blocks_success(tmp_path, monkeypatch):
    catalog, wrapper, _ = trust_job(tmp_path, monkeypatch)

    def run(argv, **kwargs):
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(exist_ok=True)
        (output / "BUILD_RECEIPT.json").write_text(
            '{"schema_version":"native-proof-v1"}'
        )
        wrapper.write_text("# Mutated trust wrapper fixture.\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    result = pipeline.run_pipeline(catalog, tmp_path / "logs", 1)
    assert result["jobs"][0]["status"] == "failed"
    assert "pipeline input changed" in result["jobs"][0]["error"]


def test_receipt_records_public_trust_identity_and_wrapper_pin(tmp_path, monkeypatch):
    catalog, wrapper, identity = trust_job(tmp_path, monkeypatch)

    def run(argv, **kwargs):
        assert argv[1] == str(wrapper)
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(exist_ok=True)
        (output / "BUILD_RECEIPT.json").write_text(
            '{"schema_version":"native-proof-v1"}'
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    result = pipeline.run_pipeline(catalog, tmp_path / "logs", 1)
    recorded = result["jobs"][0]
    assert recorded["status"] == "native_replay_verified"
    assert recorded["trust_identity"] == identity
    assert "scripts/run_with_local_probe_trust.py" in recorded["input_sha256"]


def test_trust_identity_mismatch_is_rejected(tmp_path, monkeypatch):
    catalog, _, identity = trust_job(tmp_path, monkeypatch)
    payload = json.loads(catalog.read_text())
    payload["jobs"][0]["trust_identity"] = {**identity, "key_id": "probe-other"}
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="trust identity"):
        pipeline.run_pipeline(catalog, tmp_path / "logs", 1)


def test_local_hash_pinned_job_runs_without_source_credentials(
    job, tmp_path, monkeypatch
):
    catalog, _ = job
    payload = json.loads(catalog.read_text())
    payload["jobs"][0]["source_authority"] = "local_hash_pinned"
    catalog.write_text(json.dumps(payload))
    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY", "must-not-pass")

    def run(argv, **kwargs):
        assert "LONGWORLD_SOURCE_ATTESTATION_KEY" not in kwargs["env"]
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(exist_ok=True)
        (output / "BUILD_RECEIPT.json").write_text(
            '{"schema_version":"native-proof-v1"}'
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    result = pipeline.run_pipeline(catalog, tmp_path / "logs", 1)
    assert result["jobs"][0]["status"] == "native_replay_verified"
    assert result["jobs"][0]["source_authority"] == "local_hash_pinned"
