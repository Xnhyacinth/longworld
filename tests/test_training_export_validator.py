from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_training_export


def _validator_fixture(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "relocated-release"
    training = root / "05_training"
    training.mkdir(parents=True)
    manifest_path = training / "training_export_manifest.json"
    manifest_path.write_text("{}\n")
    shard = training / "B5w.weight3.json"
    shard.write_text('[{"query_id":"q1","sample_weight":3}]\n')
    index = training / "B5w.datasets.yaml"
    index.write_text(
        yaml.safe_dump(
            {
                "b5w_w3": {
                    "path": shard.name,
                    "source": "local",
                    "converter": "sharegpt",
                    "weight": 3.0,
                }
            }
        )
    )
    manifest = {
        "manifest_path": "05_training/training_export_manifest.json",
        "source_data_dir": "04_promoted",
        "outputs": [
            {"path": "05_training/B5w.datasets.yaml"},
            {"path": "05_training/B5w.weight3.json"},
        ],
    }
    return manifest_path, manifest


def test_weighted_validator_uses_relocated_index_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )
    monkeypatch.setattr(
        validate_training_export,
        "load_release_product",
        lambda *_a, **_k: SimpleNamespace(train_rows=[{}], eval_rows=[]),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_training_export.py",
            "--manifest",
            str(manifest_path),
            "--release-profile",
            "p3-probe-12-v1",
            "--expected-transform-revision",
            "sharegpt-v-test",
            "--required-output",
            "B5w.weight3.json",
            "--expected-output-path",
            str(manifest_path.parent / "B5w.weight3.json"),
            "--weighted-dataset-index",
            "B5w.datasets.yaml",
        ],
    )

    validate_training_export.main()

    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_weighted_validator_rejects_absolute_sampler_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    index_path = manifest_path.parent / "B5w.datasets.yaml"
    index = yaml.safe_load(index_path.read_text())
    index["b5w_w3"]["path"] = str(
        (manifest_path.parent / "B5w.weight3.json").absolute()
    )
    index_path.write_text(yaml.safe_dump(index))
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_training_export.py",
            "--manifest",
            str(manifest_path),
            "--release-profile",
            "p3-probe-12-v1",
            "--expected-transform-revision",
            "sharegpt-v-test",
            "--weighted-dataset-index",
            "B5w.datasets.yaml",
        ],
    )

    with pytest.raises(SystemExit, match="index-relative"):
        validate_training_export.main()


def test_weighted_validator_rejects_logical_duplicates_across_weight_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    training = manifest_path.parent
    first = training / "B5w.weight1.json"
    first.write_text('[{"query_id":"q1","sample_weight":1}]\n')
    second = training / "B5w.weight3.json"
    second.write_text('[{"query_id":"q1","sample_weight":3}]\n')
    (training / "B5w.datasets.yaml").write_text(
        yaml.safe_dump(
            {
                "b5w_w1": {
                    "path": first.name,
                    "source": "local",
                    "converter": "sharegpt",
                    "weight": 1.0,
                },
                "b5w_w3": {
                    "path": second.name,
                    "source": "local",
                    "converter": "sharegpt",
                    "weight": 3.0,
                },
            }
        )
    )
    manifest["outputs"] = [
        {"path": f"05_training/{path.name}"}
        for path in (training / "B5w.datasets.yaml", first, second)
    ]
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_training_export.py",
            "--manifest",
            str(manifest_path),
            "--release-profile",
            "p3-probe-12-v1",
            "--expected-transform-revision",
            "sharegpt-v-test",
            "--weighted-dataset-index",
            "B5w.datasets.yaml",
        ],
    )

    with pytest.raises(SystemExit, match="logical row"):
        validate_training_export.main()
