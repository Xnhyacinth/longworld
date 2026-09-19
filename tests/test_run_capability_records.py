"""Runner-level contracts: structure split, scheduling decoupling, index,
resume adoption, budget block, solver verification receipt."""

from pathlib import Path

import pytest

from scripts.run_capability_records import (
    _task_program_signature,
    make_plan,
    seed_for_rule_family,
    split_for_job,
)

ROOT = Path(__file__).resolve().parents[1]

BASE_CONFIG = {
    "schema_version": "longworld.record-world-config.v1",
    "world_seed_base": 900000,
    "families": ["rule_holdout"],
    "depths": [1, 2],
    "lengths": [200, 800],
    "consumed_by_depth": {"1": 20, "2": 60},
    "variants_per_world": 2,
    "worlds_per_cell": 6,
    "token_targets_by_depth": {"1": [8192, 32768], "2": [32768, 131072]},
    "workers": 2,
    "trained_rule_family": "threshold_class",
}


def test_plan_requires_trained_rule_family_pin():
    config = dict(BASE_CONFIG)
    del config["trained_rule_family"]
    with pytest.raises(ValueError, match="trained_rule_family"):
        make_plan(config)


def test_structure_split_isolated_per_side():
    plan = make_plan(BASE_CONFIG)
    assert plan
    for job in plan:
        expected = "train" if job["rule_family"] == "threshold_class" else "eval"
        assert job["split"] == expected


def test_rule_family_decoupled_from_seed_parity():
    """Both structures must appear regardless of the seed stream's parity."""
    plan = make_plan(BASE_CONFIG)
    families = {job["rule_family"] for job in plan}
    assert families == {"threshold_class", "parity_vote"}
    # And in every (depth, target) cell the structures interleave by cell parity,
    # so no (structure, target) bucket is starved by scheduling accident.
    cells = {}
    for job in plan:
        cells.setdefault((job["depth"], job["token_target"]), []).append(
            job["rule_family"]
        )
    for cell, structures in cells.items():
        assert set(structures) == {"threshold_class", "parity_vote"}, cell


def test_seed_nudge_yields_planned_structure():
    for rule_family in ("threshold_class", "parity_vote"):
        for seed in range(900000, 900020):
            nudged = seed_for_rule_family(seed, rule_family)
            # _rule_world reads RULE_FAMILIES[seed % 2]
            from longworld.synthesis.capability_families import RULE_FAMILIES

            assert RULE_FAMILIES[nudged % len(RULE_FAMILIES)] == rule_family
            # The nudge is minimal: 0 or 1.
            assert nudged - seed in (0, 1)


def test_non_rule_families_keep_seed_split():
    plan = make_plan(
        {
            **BASE_CONFIG,
            "families": ["filter_aggregate"],
            "depths": [1],
            "consumed_by_depth": {"1": 20},
            "token_targets_by_depth": {"1": [8192]},
        }
    )
    assert {job["rule_family"] for job in plan} == {None}
    for job in plan:
        assert job["split"] == ("eval" if job["seed"] % 5 == 0 else "train")


def test_program_signature_masks_values():
    row = {"messages": [{}, {"content": '{"ids":["r1","r2"],"count":2}'}]}
    signature = _task_program_signature(row)
    # Quoted strings (keys and values) fold to S, digits to N: the signature
    # is structure-only, so renderer/CF variants of one task share it.
    assert signature == '{"S":["S","S"],"S":N}'
    assert "r1" not in signature


def test_receipt_split_is_bank_level_not_world_local():
    """The bank decides; world-local holdout blocks stay self-relative.

    Verified on the shipped v2 bank: the local blocks claim trained structures
    on both sides, while the export split for v3+ comes from the config.
    """
    config = {**BASE_CONFIG, "trained_rule_family": "parity_vote"}
    job = {"family": "rule_holdout", "rule_family": "threshold_class", "seed": 900001}
    assert split_for_job(config, job) == "eval"
    config2 = {**BASE_CONFIG, "trained_rule_family": "threshold_class"}
    assert split_for_job(config2, job) == "train"
