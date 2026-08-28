from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from longworld.core.attestation import ATTESTATION_ENV, attestation_key_from_env
from longworld.core.release_profile import release_profile_sha256
from longworld.core.training_manifest import (
    create_training_manifest,
    file_sha256,
    resolve_training_manifest_path,
    validate_training_manifest,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import quality_gate
from quality_gate import (
    load_and_create_release_gate_receipt,
    validate_gate_receipt_output,
)

KEY = b"longworld-training-manifest-test-key-32-bytes"


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, KEY.decode())


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _source_digests(source: Path) -> dict[str, str]:
    return {
        name: file_sha256(source / name)
        for name in ("quality_report.json", "train.jsonl", "eval.jsonl")
    }


def test_inrepo_single_gpu_trainer_is_explicitly_unsigned_diagnostic_only() -> None:
    source = (SCRIPTS / "train_sft.py").read_text(encoding="utf-8")

    assert 'ap.add_argument("--diagnostic-only", action="store_true")' in source
    assert "--diagnostic-only is required" in source
    assert "create_training_manifest" not in source
    assert "validate_training_manifest" not in source


def test_loaded_release_metrics_feed_the_gate_receipt_world_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "data_stage": "train_ready",
                "release_profile_id": "p3-probe-12-v1",
                "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            }
        )
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", KEY.decode())
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", "probe-gate-v1")

    product, receipt = load_and_create_release_gate_receipt(
        tmp_path,
        "p3-probe-12-v1",
        attestation_key=KEY,
    )

    assert receipt["n_worlds"] == 12
    assert receipt["ok"] is True
    assert receipt["errors"] == []
    assert receipt["source_file_sha256"] == product.source_file_sha256
    assert receipt["tokenizer_model_id"] == "Qwen/Qwen3.5-4B"
    assert len(receipt["tokenizer_revision"]) == 40
    assert receipt["tokenizer_asset_manifest_sha256"] is None


def test_gate_receipt_rejects_non_green_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text("{}")
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "errors": ["semantic growth failed"],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )

    with pytest.raises(ValueError, match="failed quality gate"):
        load_and_create_release_gate_receipt(
            tmp_path,
            "p3-probe-12-v1",
            attestation_key=KEY,
        )


def test_gate_receipt_rejects_an_incomplete_auditor_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.setenv("LONGWORLD_AUDITOR_ATTESTATION_KEY", KEY.decode())
    monkeypatch.delenv("LONGWORLD_AUDITOR_ATTESTATION_KEY_ID", raising=False)
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "data_stage": "train_ready",
                "release_profile_id": "p3-probe-12-v1",
                "release_profile_sha256": release_profile_sha256("p3-probe-12-v1"),
            }
        )
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {
            "errors": [],
            "n_rows": 2,
            "n_worlds_observed": 12,
        },
    )

    with pytest.raises(ValueError, match="complete auditor identity"):
        load_and_create_release_gate_receipt(
            tmp_path,
            "p3-probe-12-v1",
            attestation_key=KEY,
        )


@pytest.mark.parametrize(
    "source_name", ("quality_report.json", "train.jsonl", "eval.jsonl")
)
def test_gate_receipt_cannot_overwrite_a_bound_release_source(
    tmp_path: Path, source_name: str
) -> None:
    with pytest.raises(ValueError, match="cannot overwrite"):
        validate_gate_receipt_output(tmp_path, tmp_path / source_name)


def test_release_loader_always_rechecks_the_complete_train_eval_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = {"release_profile_id": "p3-probe-12-v1"}
    (tmp_path / "quality_report.json").write_text(json.dumps(report))
    train = [{"query_id": "train-1", "split": "train"}]
    evaluation = [{"query_id": "eval-1", "split": "eval"}]
    _write_jsonl(tmp_path / "train.jsonl", train)
    _write_jsonl(tmp_path / "eval.jsonl", evaluation)
    observed: dict[str, object] = {}

    def fake_evaluate(candidate_report, rows, **kwargs):
        observed.update(report=candidate_report, rows=rows, kwargs=kwargs)
        return {"ok": True, "errors": []}

    monkeypatch.setattr(quality_gate, "evaluate_quality", fake_evaluate)

    loaded = quality_gate.load_release_product(tmp_path, "p3-probe-12-v1")

    assert loaded.train_rows == train
    assert loaded.eval_rows == evaluation
    assert observed["rows"] == train + evaluation
    assert observed["kwargs"]["release_profile_id"] == "p3-probe-12-v1"


def test_release_loader_fails_closed_on_any_recomputed_gate_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps({"release_profile_id": "p3-probe-12-v1"})
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"query_id": "train-1", "split": "train"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"query_id": "eval-1", "split": "eval"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {"ok": False, "errors": ["tampered"]},
    )

    with pytest.raises(ValueError, match="tampered"):
        quality_gate.load_release_product(tmp_path, "p3-probe-12-v1")


def test_release_loader_rejects_rows_stored_in_the_wrong_split_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality_report.json").write_text(
        json.dumps({"release_profile_id": "p3-probe-12-v1"})
    )
    _write_jsonl(tmp_path / "train.jsonl", [{"query_id": "eval-1", "split": "eval"}])
    _write_jsonl(tmp_path / "eval.jsonl", [{"query_id": "train-1", "split": "train"}])
    monkeypatch.setattr(
        quality_gate,
        "evaluate_quality",
        lambda *_args, **_kwargs: {"ok": True, "errors": []},
    )

    with pytest.raises(ValueError, match="split file mismatch"):
        quality_gate.load_release_product(tmp_path, "p3-probe-12-v1")


def test_training_manifest_binds_source_report_and_every_output(tmp_path: Path) -> None:
    source = tmp_path / "release"
    output = tmp_path / "export"
    source.mkdir()
    output.mkdir()
    (source / "quality_report.json").write_text('{"signed":"report"}\n')
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    shard = output / "B5.json"
    shard.write_text('[{"conversations":[]}]\n')
    dataset_info = output / "dataset_info.json"
    dataset_info.write_text('{"causaltwin_b5":{}}\n')
    manifest_path = output / "training_export_manifest.json"

    manifest = create_training_manifest(
        manifest_path,
        source_data_dir=source,
        release_profile_id="p3-probe-12-v1",
        transform_revision="sharegpt-v-test",
        output_paths=[shard, dataset_info],
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    validated = validate_training_manifest(
        manifest_path,
        expected_release_profile_id="p3-probe-12-v1",
        expected_transform_revision="sharegpt-v-test",
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
    assert validated == manifest
    assert manifest["release_profile_sha256"] == release_profile_sha256(
        "p3-probe-12-v1"
    )

    shard.write_text('[{"conversations":[{"tampered":true}]}]\n')
    with pytest.raises(ValueError, match="output digest"):
        validate_training_manifest(
            manifest_path,
            expected_release_profile_id="p3-probe-12-v1",
            expected_transform_revision="sharegpt-v-test",
            attestation_key=attestation_key_from_env("training_export_manifest"),
        )


def test_validator_rejects_a_valid_manifest_for_a_different_output_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "release"
    signed_output = tmp_path / "signed"
    actual_output = tmp_path / "actual"
    source.mkdir()
    signed_output.mkdir()
    actual_output.mkdir()
    (source / "quality_report.json").write_text('{"signed":"report"}\n')
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    signed_shard = signed_output / "B5.json"
    signed_shard.write_text("[]\n")
    manifest_path = signed_output / "training_export_manifest.json"
    create_training_manifest(
        manifest_path,
        source_data_dir=source,
        release_profile_id="p3-probe-12-v1",
        transform_revision="sharegpt-v-test",
        output_paths=[signed_shard],
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    manifest = validate_training_manifest(
        manifest_path,
        expected_release_profile_id="p3-probe-12-v1",
        expected_transform_revision="sharegpt-v-test",
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
    bound = resolve_training_manifest_path(
        manifest_path, manifest, manifest["outputs"][0]["path"]
    )

    assert bound != (actual_output / "B5.json").resolve()


def test_training_manifest_validates_after_release_directory_relocation(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release-v1"
    source = release_root / "04_promoted"
    output = release_root / "05_training"
    source.mkdir(parents=True)
    output.mkdir()
    (source / "quality_report.json").write_text('{"signed":"report"}\n')
    (source / "train.jsonl").write_text('{"split":"train"}\n')
    (source / "eval.jsonl").write_text('{"split":"eval"}\n')
    shard = output / "B5.json"
    shard.write_text("[]\n")
    manifest_path = output / "training_export_manifest.json"

    manifest = create_training_manifest(
        manifest_path,
        release_root=release_root,
        source_data_dir=source,
        release_profile_id="p3-probe-12-v1",
        transform_revision="sharegpt-v-test",
        output_paths=[shard],
        source_file_sha256=_source_digests(source),
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )

    assert manifest["manifest_path"] == "05_training/training_export_manifest.json"
    assert manifest["source_data_dir"] == "04_promoted"
    assert manifest["outputs"][0]["path"] == "05_training/B5.json"
    assert not Path(manifest["source_data_dir"]).is_absolute()

    relocated = tmp_path / "relocated"
    shutil.move(release_root, relocated)
    validate_training_manifest(
        relocated / "05_training" / "training_export_manifest.json",
        expected_release_profile_id="p3-probe-12-v1",
        expected_transform_revision="sharegpt-v-test",
        attestation_key=attestation_key_from_env("training_export_manifest"),
    )
