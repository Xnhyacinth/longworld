"""Real P94 L2 subset keeps source, task and final-mask boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import export_sharded_candidate_subset as subset
from scripts.export_sharded_candidate_subset import export, verify

ROOT = Path(__file__).resolve().parents[1]


def test_source_worlds_accept_two_pinned_pools_without_repeated_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subset, "ROOT", tmp_path)
    pools = []
    for group, split in (("g1", "train"), ("g2", "eval")):
        snapshot = {
            "snapshot_id": group,
            "source": {"license": "CC BY-SA", "revisions": {group: 7}},
            "documents": [
                {
                    "title": group,
                    "page_url": f"https://example.test/{group}",
                    "revision_url": f"https://example.test/{group}?oldid=7",
                }
            ],
        }
        snapshot_path = tmp_path / f"{group}.json"
        snapshot_path.write_text(json.dumps(snapshot))
        pool_path = tmp_path / f"{group}_pool.json"
        pool_path.write_text(
            json.dumps(
                {
                    "schema": "longworld.source-batch-pool.v2",
                    "sources": [
                        {
                            "split": split,
                            "domain": "test",
                            "topic": group,
                            "snapshot": {
                                "path": snapshot_path.name,
                                "sha256": hashlib.sha256(
                                    snapshot_path.read_bytes()
                                ).hexdigest(),
                            },
                        }
                    ],
                }
            )
        )
        pools.append(pool_path)
    worlds = subset._source_worlds(pools, {"g1": "train", "g2": "eval"})
    assert {(world["source_group"], world["split"]) for world in worlds} == {
        ("g1", "train"),
        ("g2", "eval"),
    }
    with pytest.raises(ValueError, match="world repeated"):
        subset._source_worlds([pools[0], pools[0]], {"g1": "train"})
    second = json.loads((tmp_path / "g2.json").read_text())
    second["documents"][0]["title"] = "g1"
    second["source"]["revisions"] = {"g1": 7}
    (tmp_path / "g2.json").write_text(json.dumps(second))
    second_pool = json.loads(pools[1].read_text())
    second_pool["sources"][0]["snapshot"]["sha256"] = hashlib.sha256(
        (tmp_path / "g2.json").read_bytes()
    ).hexdigest()
    pools[1].write_text(json.dumps(second_pool))
    with pytest.raises(ValueError, match="page crosses train/eval"):
        subset._source_worlds(pools, {"g1": "train", "g2": "eval"})


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
