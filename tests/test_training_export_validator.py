from __future__ import annotations

import hashlib
import json
import stat
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

    def output_entry(path: Path) -> dict:
        raw = path.read_bytes()
        return {
            "path": f"05_training/{path.name}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    manifest = {
        "manifest_path": "05_training/training_export_manifest.json",
        "source_data_dir": "04_promoted",
        "outputs": [
            output_entry(index),
            output_entry(shard),
        ],
    }
    return manifest_path, manifest


def test_weighted_validator_uses_relocated_index_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    validated: list[tuple[Path, dict]] = []
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )
    monkeypatch.setattr(
        validate_training_export,
        "validate_deterministic_training_transform",
        lambda path, payload: validated.append((path, payload)),
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
    assert validated == [(manifest_path, manifest)]


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
        validate_training_export,
        "validate_deterministic_training_transform",
        lambda *_a, **_k: 1,
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
        validate_training_export,
        "validate_deterministic_training_transform",
        lambda *_a, **_k: 1,
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


def test_validator_fails_closed_for_an_unsupported_executable_transform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    manifest["transform_revision"] = "longworld-swift-messages-v2"
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
            "longworld-swift-messages-v2",
        ],
    )

    with pytest.raises(ValueError, match="no executable deterministic validator"):
        validate_training_export.main()


def test_verified_snapshot_is_private_read_only_and_immune_to_source_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    source_shard = manifest_path.parent / "B5w.weight3.json"
    original = source_shard.read_bytes()
    snapshot_root = tmp_path / "snapshots"
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )
    monkeypatch.setattr(
        validate_training_export,
        "validate_deterministic_training_transform",
        lambda *_a, **_k: 1,
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
            "--weighted-dataset-index",
            "B5w.datasets.yaml",
            "--snapshot-root",
            str(snapshot_root),
        ],
    )

    validate_training_export.main()
    result = json.loads(capsys.readouterr().out)
    snapshot = Path(result["snapshot_dir"])
    source_shard.write_text('[{"query_id":"attacker"}]\n')

    assert (snapshot / source_shard.name).read_bytes() == original
    assert len(snapshot.name) == 64
    assert stat.S_IMODE(snapshot_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o500
    assert stat.S_IMODE((snapshot / source_shard.name).stat().st_mode) == 0o400


def test_snapshot_rechecks_bound_bytes_after_deterministic_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    source_shard = manifest_path.parent / "B5w.weight3.json"
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )

    def replace_after_validation(*_args, **_kwargs) -> SimpleNamespace:
        source_shard.write_text('[{"query_id":"attacker"}]\n')
        return SimpleNamespace(train_rows=[{}], eval_rows=[])

    monkeypatch.setattr(
        validate_training_export,
        "validate_deterministic_training_transform",
        lambda *_a, **_k: 1,
    )
    monkeypatch.setattr(
        validate_training_export,
        "load_release_product",
        replace_after_validation,
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
            "--snapshot-root",
            str(tmp_path / "snapshots"),
        ],
    )

    with pytest.raises(ValueError, match="changed before snapshot"):
        validate_training_export.main()


def test_snapshot_streams_manifest_outputs_without_reading_whole_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    source_outputs = {
        manifest_path.parent / "B5w.datasets.yaml",
        manifest_path.parent / "B5w.weight3.json",
    }
    original_read_bytes = Path.read_bytes

    def reject_whole_file_read(path: Path) -> bytes:
        if path in source_outputs:
            raise AssertionError("snapshot source must be streamed")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_file_read)

    snapshot = validate_training_export.materialize_verified_snapshot(
        manifest_path, manifest, tmp_path / "snapshots"
    )

    assert (snapshot / "B5w.weight3.json").is_file()


def test_snapshot_preserves_nested_output_paths_and_same_basenames(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    training = release / "05_training"
    manifest_path = training / "training_export_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n")
    outputs = []
    expected: dict[Path, bytes] = {}
    for directory, raw in (("left", b"left bytes"), ("right", b"right bytes")):
        path = training / directory / "payload.json"
        path.parent.mkdir()
        path.write_bytes(raw)
        relative = path.relative_to(release).as_posix()
        outputs.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
        expected[Path(directory) / path.name] = raw
    manifest = {
        "manifest_path": manifest_path.relative_to(release).as_posix(),
        "source_data_dir": "04_promoted",
        "outputs": outputs,
    }

    snapshot = validate_training_export.materialize_verified_snapshot(
        manifest_path, manifest, tmp_path / "snapshots"
    )

    for relative, raw in expected.items():
        assert (snapshot / relative).read_bytes() == raw
        assert stat.S_IMODE((snapshot / relative.parent).stat().st_mode) == 0o500


def test_validator_cli_preserves_nested_outputs_with_same_basenames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    release = tmp_path / "release"
    manifest_path = release / "05_training" / "training_export_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n")
    outputs = []
    for directory in ("left", "right"):
        path = manifest_path.parent / directory / "payload.json"
        path.parent.mkdir()
        path.write_text(directory)
        raw = path.read_bytes()
        outputs.append(
            {
                "path": path.relative_to(release).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
    manifest = {
        "manifest_path": manifest_path.relative_to(release).as_posix(),
        "source_data_dir": "04_promoted",
        "outputs": outputs,
    }
    monkeypatch.setattr(
        validate_training_export,
        "validate_training_manifest",
        lambda *_a, **_k: manifest,
    )
    monkeypatch.setattr(
        validate_training_export,
        "validate_deterministic_training_transform",
        lambda *_a, **_k: 2,
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
            "--snapshot-root",
            str(tmp_path / "snapshots"),
        ],
    )

    validate_training_export.main()
    snapshot = Path(json.loads(capsys.readouterr().out)["snapshot_dir"])

    assert (snapshot / "left" / "payload.json").read_text() == "left"
    assert (snapshot / "right" / "payload.json").read_text() == "right"


def test_snapshot_rejects_manifest_output_outside_training_output_root(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    manifest_path = release / "05_training" / "training_export_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n")
    outside = release / "other" / "payload.json"
    outside.parent.mkdir()
    outside.write_text("outside")
    raw = outside.read_bytes()
    manifest = {
        "manifest_path": "05_training/training_export_manifest.json",
        "source_data_dir": "04_promoted",
        "outputs": [
            {
                "path": "other/payload.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        ],
    }

    with pytest.raises(ValueError, match="outside training output root"):
        validate_training_export.materialize_verified_snapshot(
            manifest_path, manifest, tmp_path / "snapshots"
        )


def test_snapshot_rejects_non_sticky_shared_writable_parent(tmp_path: Path) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    unsafe_parent = tmp_path / "unsafe-tmp"
    unsafe_parent.mkdir(mode=0o777)
    unsafe_parent.chmod(0o777)

    with pytest.raises(ValueError, match="snapshot parent is replaceable"):
        validate_training_export.materialize_verified_snapshot(
            manifest_path, manifest, unsafe_parent / "snapshot-root"
        )


def test_snapshot_rejects_foreign_owned_non_writable_ancestor() -> None:
    foreign_uid = validate_training_export.os.geteuid() + 1
    info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=foreign_uid)

    assert not validate_training_export._snapshot_ancestor_is_trusted(info)


def test_snapshot_accepts_sticky_shared_tmp_parent(tmp_path: Path) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    sticky_parent = tmp_path / "sticky-tmp"
    sticky_parent.mkdir(mode=0o777)
    sticky_parent.chmod(0o1777)

    snapshot = validate_training_export.materialize_verified_snapshot(
        manifest_path, manifest, sticky_parent / "snapshot-root"
    )

    assert snapshot.is_dir()


def test_failed_stream_validation_does_not_publish_a_partial_snapshot(
    tmp_path: Path,
) -> None:
    manifest_path, manifest = _validator_fixture(tmp_path)
    manifest["outputs"][-1]["sha256"] = "0" * 64
    snapshot_root = tmp_path / "snapshots"

    with pytest.raises(ValueError, match="changed before snapshot"):
        validate_training_export.materialize_verified_snapshot(
            manifest_path, manifest, snapshot_root
        )

    assert list(snapshot_root.iterdir()) == []
