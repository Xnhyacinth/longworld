"""Tests for the P73 v2 multi-band shared-world bank generator.

Tiny end-to-end generation (2 worlds per band into a tmp dir) plus
config-level assertions: band table shape, infeasible 8K cells, seed layout
collision-freedom against v1, and the manifest/shard/row contracts the gates
consume (band field everywhere, world-disjoint split).
"""

from __future__ import annotations

import json

import pytest

from scripts.generate_p73_pilot import CONFIG as V1_CONFIG
from scripts.generate_p73_pilot_v2 import (
    BANDS,
    CONFIG,
    INFEASIBLE_CELLS,
    SEED_BASE,
    SEED_CLEARANCE,
    band_seed,
    build_bank,
)


def test_band_table_shape():
    assert [b["band"] for b in BANDS] == ["8K", "32K", "64K"]
    by_band = {b["band"]: b for b in BANDS}
    assert by_band["8K"]["status"] == "infeasible"
    assert by_band["8K"]["length_records"] == [None, None]
    for band in ("32K", "64K"):
        entry = by_band[band]
        assert entry["status"] == "feasible"
        assert all(isinstance(L, int) for L in entry["length_records"])
        # calibration stayed capacity-driven: bigger band, more records
        assert all(L > 0 for L in entry["length_records"])
    # groups and families unchanged from v1
    assert CONFIG["groups"] == V1_CONFIG["groups"]
    assert set(CONFIG) == {"groups", "consumed_per_family", "depth"}
    assert CONFIG["consumed_per_family"] == V1_CONFIG["consumed_per_family"]
    assert CONFIG["depth"] == V1_CONFIG["depth"]


def test_eight_k_infeasibility_is_recorded_not_forced():
    assert {cell["band"] for cell in INFEASIBLE_CELLS} == {"8K"}
    assert {cell["group_index"] for cell in INFEASIBLE_CELLS} == {0, 1}
    for cell in INFEASIBLE_CELLS:
        assert cell["band_ceiling_tokens"] == 10240
        assert cell["reason"]


def test_seed_layout_is_collision_free_against_v1():
    v1_seeds = set()
    for index in range(len(V1_CONFIG["groups"])):
        for w in range(21):  # v1 scale: 42 worlds, 21 per group
            v1_seeds.add(861000 + index * 1000 + w)
    v2_seeds = set()
    for band in BANDS:
        if band["status"] != "feasible":
            continue
        for group_index in range(len(CONFIG["groups"])):
            for w in range(14):
                v2_seeds.add(band_seed(band, group_index, w))
    assert not (v1_seeds & v2_seeds)
    # every v2 seed stays in the wave's 862xxx base
    assert all(SEED_BASE <= s < SEED_BASE + 1000 for s in v2_seeds)
    # and the bands do not collide with each other: 2 feasible bands x 2
    # groups x 14 worlds
    assert len(v2_seeds) == 56


@pytest.fixture(scope="module")
def tiny_bank(tmp_path_factory):
    out = tmp_path_factory.mktemp("p73_v2")
    manifest = build_bank(out, 2)
    return out, manifest


def test_tiny_generation_manifest(tiny_bank):
    out, manifest = tiny_bank
    assert manifest["schema_version"] == "longworld.p73-shared-bank.v2"
    # 2 worlds per band, one per family group: 8K skipped as infeasible
    assert manifest["worlds"] == 4
    assert set(manifest["bands"]["per_band"]) == {"32K", "64K"}
    assert manifest["bands"]["per_band"]["32K"]["worlds"] == 2
    assert manifest["bands"]["per_band"]["64K"]["worlds"] == 2
    # every family keeps its rows (12 records-side, 16 F-side per world)
    assert set(manifest["families"]) == set(
        V1_CONFIG["groups"][0] + V1_CONFIG["groups"][1]
    )
    assert manifest["honesty"]["source_kind"] == "simulated"
    assert manifest["honesty"]["production_eligible"] is False
    # infeasible cells carried into the manifest
    assert manifest["bands"]["infeasible_cells"] == INFEASIBLE_CELLS
    # calibration table landed
    assert manifest["calibration"]["tokenizer"]["model_id"] == "Qwen/Qwen3.5-4B"
    chosen = [c for c in manifest["calibration"]["final_candidates"] if c["chosen"]]
    assert {c["band"] for c in chosen} == {"32K", "64K"}


def test_rows_and_shards_carry_band(tiny_bank):
    out, manifest = tiny_bank
    rows = []
    for name in ("train.jsonl", "eval.jsonl"):
        with (out / name).open() as fh:
            for line in fh:
                rows.append(json.loads(line))
    assert len(rows) == manifest["rows"] == 56
    world_bands = {}
    shards = sorted((out / "shards").glob("*/world.json"))
    assert len(shards) == 4
    for shard in shards:
        world = json.loads(shard.read_text())
        assert world["band"] in ("32K", "64K")
        assert world["band_target_full_chat_tokens"] in (32768, 65536)
        assert "requested_length_records" in world
        # the band's configured L matches the group the shard serves
        group_index = int(shard.parent.name.split("-")[2])
        band_cfg = next(b for b in BANDS if b["band"] == world["band"])
        assert (
            world["requested_length_records"] == band_cfg["length_records"][group_index]
        )
        # shard tasks keep the solve_context the audits re-solve on
        assert all("solve_context" in t for t in world["tasks"])
        world_bands[world["world_id"]] = world["band"]
    for row in rows:
        assert row["band"] == world_bands[row["world_id"]]
        assert row["band"] in ("32K", "64K")


def test_split_is_world_disjoint(tiny_bank):
    out, manifest = tiny_bank
    split_worlds = {}
    for name in ("train.jsonl", "eval.jsonl"):
        with (out / name).open() as fh:
            for line in fh:
                row = json.loads(line)
                split_worlds.setdefault(row["world_id"], set()).add(name)
    assert all(len(splits) == 1 for splits in split_worlds.values())
    # group_id == world_id everywhere
    with (out / "train.jsonl").open() as fh:
        for line in fh:
            row = json.loads(line)
            assert row["group_id"] == row["world_id"]
