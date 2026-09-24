"""P71 adapter preserves native plan, split, and solver receipts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis import capability_native_adapter as adapter

CONFIG = {
    "schema_version": "longworld.record-world-config.v1",
    "world_seed_base": 990000,
    "families": ["alias_locate"],
    "depths": [1],
    "lengths": [200],
    "consumed_by_depth": {"1": 20},
    "variants_per_world": 1,
    "worlds_per_cell": 1,
    "token_targets_by_depth": {"1": [8192]},
    "workers": 1,
}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value) + "\n")


def _completed_bank(root: Path, config: dict = CONFIG) -> None:
    root.mkdir()
    sample = {
        "example_id": "world-a:q0",
        "world_id": "world-a",
        "split": "train",
        "messages": [
            {"role": "user", "content": "context"},
            {"role": "assistant", "content": "1"},
        ],
    }
    index = {
        "example_id": "world-a:q0",
        "world_id": "world-a",
        "group_id": "world-a",
        "split": "train",
        "admission_status": "completed",
        "full_message_tokens": 8000,
    }
    (root / "train.jsonl").write_text(json.dumps(sample) + "\n")
    (root / "eval.jsonl").write_text("")
    (root / "sample_index.jsonl").write_text(json.dumps(index) + "\n")
    _write_json(root / "state.json", {"config": config, "code_sha256": {}})
    _write_json(
        root / "verification.json",
        {"solver_recheck": {"passed": True, "checked": 1, "mismatches": 0}},
    )
    files = {
        name: adapter._sha256(root / name)
        for name in ("train.jsonl", "eval.jsonl", "sample_index.jsonl")
    }
    _write_json(
        root / "manifest.json",
        {
            "schema_version": adapter.native.SCHEMA,
            "status": "local_symbolic_record_worlds_complete",
            "rows": 1,
            "semantic_tasks": 1,
            "split_rows": {"train": 1, "eval": 0},
            "completed_shards": 0,
            "shards": [],
            "families": ["alias_locate"],
            "files": files,
        },
    )


def test_probe_expands_native_family_length_split_cells():
    result = adapter.probe(CONFIG)
    assert result["source_kind"] == "controlled_simulation"
    assert result["planned_shards"] == 1
    assert result["families"] == ["alias_locate"]
    assert result["token_targets"] == [8192]
    assert result["cells"] == [
        {"family": "alias_locate", "depth": 1, "token_target": 8192, "shards": 1}
    ]


def test_probe_rejects_duplicate_family_and_unbounded_plan():
    with pytest.raises(ValueError, match="unique"):
        adapter.probe({**CONFIG, "families": ["alias_locate", "alias_locate"]})
    with pytest.raises(ValueError, match="1..1"):
        adapter.probe({**CONFIG, "worlds_per_cell": 2}, max_shards=1)


def test_unsupported_family_depth_is_reported_and_rejected_before_fresh_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = {
        **CONFIG,
        "depths": [3],
        "consumed_by_depth": {"3": 200},
        "token_targets_by_depth": {"3": [98304]},
    }
    planned = adapter.probe(config)
    assert planned["unsupported_depth_shards"] == 1
    assert planned["unsupported_depth_cells"] == [
        {"family": "alias_locate", "depth": 3, "token_target": 98304, "shards": 1}
    ]
    config_path = tmp_path / "config.json"
    _write_json(config_path, config)
    monkeypatch.setattr(
        adapter.native,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unsupported job should not run"),
    )
    with pytest.raises(ValueError, match="unsupported family/depth"):
        adapter.run_native(config_path, tmp_path / "fresh")


def test_native_run_returns_verified_paths(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    _write_json(config_path, CONFIG)
    output = tmp_path / "candidate"

    def fake_run(received_config: Path, received_output: Path, resume: bool):
        assert received_config == config_path
        assert received_output == output
        assert resume is False
        _completed_bank(output)

    monkeypatch.setattr(adapter.native, "run", fake_run)
    result = adapter.run_native(config_path, output)
    assert result["status"] == "verified_native_candidate"
    assert result["rows"] == result["semantic_tasks"] == 1
    assert result["split_rows"] == {"train": 1, "eval": 0}
    assert Path(result["paths"]["sample_index"]).exists()
    assert result["train_ready"] is False
    assert adapter.run_native(config_path, output, resume=True) == result


def test_native_output_rejects_changed_hash_and_cross_split(tmp_path: Path):
    output = tmp_path / "candidate"
    _completed_bank(output)
    (output / "train.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="hash"):
        adapter.verify_output(output)

    _completed_bank(output := tmp_path / "candidate2")
    index_path = output / "sample_index.jsonl"
    index = json.loads(index_path.read_text())
    index["split"] = "eval"
    index_path.write_text(json.dumps(index) + "\n")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["sample_index.jsonl"] = adapter._sha256(index_path)
    _write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="index invalid"):
        adapter.verify_output(output)


def test_native_output_rejects_solver_mismatch(tmp_path: Path):
    output = tmp_path / "candidate"
    _completed_bank(output)
    _write_json(
        output / "verification.json",
        {"solver_recheck": {"passed": False, "checked": 1, "mismatches": 1}},
    )
    with pytest.raises(ValueError, match="solver"):
        adapter.verify_output(output)
