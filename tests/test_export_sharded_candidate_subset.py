"""Real P94 L2 subset keeps source, task and final-mask boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.export_sharded_candidate_subset import export, verify

ROOT = Path(__file__).resolve().parents[1]


def test_subset_rejects_unhashable_operation_config(tmp_path: Path) -> None:
    config = json.loads((ROOT / "configs/p94_real_l2_subset_v1.json").read_text())
    config["lane_operations"] = {"wiki_p94_structural_pool": [{"bad": "op"}]}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="lane/operation"):
        export(path, tmp_path / "out")


def test_real_l2_subset_materializes_two_worlds_without_audit_leakage(
    tmp_path: Path,
) -> None:
    index = ROOT / "data/candidates/p94_candidate_refs_v5/manifest.json"
    if not index.exists():
        pytest.skip("frozen P94 candidate index is not mounted")
    output = tmp_path / "subset"
    config = ROOT / "configs/p94_real_l2_subset_v1.json"
    result = export(config, output)
    assert result["candidate_views"] == result["mask_audited_views"] == 67
    assert result["independent_semantic_tasks"] == 19
    assert result["splits"] == {"train": 48, "eval": 19}
    assert result["source_groups"] == 2
    assert result["train_ready"] is False
    for split in ("train", "eval"):
        for line in (output / f"{split}.jsonl").read_text().splitlines():
            row = json.loads(line)
            assert set(row) == {"sample_id", "messages"}
            assert [message["role"] for message in row["messages"]] == [
                "user",
                "assistant",
            ]
    worlds = json.loads((output / "source_manifest.json").read_text())
    assert {world["split"] for world in worlds} == {"train", "eval"}
    assert all(world["documents"] and world["snapshot"]["sha256"] for world in worlds)
    with pytest.raises(ValueError, match="subset output changed"):
        (output / "train.jsonl").write_text("changed\n")
        verify(config, output)
