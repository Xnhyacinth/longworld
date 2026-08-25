from __future__ import annotations

from dataclasses import replace

from longworld.core.release_profile import (
    RELEASE_PROFILES,
    release_profile,
    release_profile_sha256,
)


def test_release_profiles_fix_scale_and_real_data_growth() -> None:
    probe = release_profile("p3-probe-12-v1")
    production = release_profile("p3-production-48-v1")

    assert probe.environment == "probe"
    assert probe.predecessor_profile_id is None
    assert probe.expected_promoted_worlds == 12
    assert (probe.min_train_worlds, probe.min_eval_worlds) == (10, 2)
    assert (probe.min_real_train_worlds, probe.min_real_eval_worlds) == (1, 0)
    assert probe.min_domains == 1
    assert probe.split_strategy == "world_atomic_hash_v1"
    assert probe.training_conditions == ("B1", "B3", "B5", "B5w")
    assert probe.training_length_buckets == ("16k", "32k", "64k")
    assert probe.training_export_seed == 0
    assert probe.max_training_token_spread == 0.05
    assert probe.llamafactory_transform_revision == (
        "longworld-llamafactory-sharegpt-v4"
    )
    assert probe.swift_transform_revision == "longworld-swift-messages-v2"
    assert production.environment == "production"
    assert production.predecessor_profile_id == "p3-probe-12-v1"
    assert production.expected_promoted_worlds == 48
    assert (production.min_train_worlds, production.min_eval_worlds) == (40, 8)
    assert production.min_source_families >= 6
    assert production.min_domains >= 2
    assert production.min_real_base_tasks >= 12
    assert production.min_real_64k_rows >= 12
    assert production.tokenizer_model_id == "Qwen/Qwen3.5-4B"
    assert len(production.tokenizer_revision) == 40
    assert probe.dense_top_k == production.dense_top_k == 3
    assert probe.min_promotion_retention == production.min_promotion_retention == 0.5
    assert (
        release_profile("p3-production-210-v1").predecessor_profile_id
        == "p3-production-48-v1"
    )


def test_release_profile_digest_binds_every_gate_value(monkeypatch) -> None:
    profile_id = "p3-probe-12-v1"
    original = release_profile_sha256(profile_id)
    monkeypatch.setitem(
        RELEASE_PROFILES,
        profile_id,
        replace(release_profile(profile_id), min_real_64k_rows=7),
    )

    assert len(original) == 64
    assert release_profile_sha256(profile_id) != original
