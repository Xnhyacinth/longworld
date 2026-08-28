from __future__ import annotations

from dataclasses import replace

import pytest

from longworld.core.release_profile import (
    RELEASE_PROFILES,
    issuable_release_profile,
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
    assert production.tokenizer_asset_manifest_sha256 is None
    assert probe.dense_top_k == production.dense_top_k == 3
    assert probe.min_promotion_retention == production.min_promotion_retention == 0.5
    assert (
        release_profile("p3-production-210-v1").predecessor_profile_id
        == "p3-production-48-v1"
    )


def test_p7_source_rich_profile_requires_every_world_to_be_real() -> None:
    profile = release_profile("p7-source-rich-probe-12-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id == "p6-source-dependent-probe-12-v1"
    assert profile.expected_promoted_worlds == 12
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (10, 2)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (10, 2)
    assert profile.min_source_families == 4
    assert profile.min_real_base_tasks == 12
    assert profile.min_real_source_relations == 12
    assert profile.min_real_64k_rows == 48
    assert profile.min_unique_real_source_workflows == 12
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {
        "company": 12,
        "researchlab": 12,
        "codeforge": 12,
    }
    assert dict(profile.min_exact_64k_rows_by_domain) == {
        "company": 12,
        "researchlab": 12,
        "codeforge": 12,
    }
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }
    assert dict(profile.promoted_domain_world_quotas) == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }


def test_current_production_profiles_require_source_rich_predecessors() -> None:
    production_48 = release_profile("p10-source-rich-production-48-v1")
    production_210 = release_profile("p10-source-rich-production-210-v1")

    assert production_48.environment == "production"
    assert production_48.predecessor_profile_id == "p7-source-rich-probe-12-v1"
    assert production_48.expected_promoted_worlds == 48
    assert dict(production_48.promoted_domain_world_quotas) == {
        "company": 16,
        "researchlab": 16,
        "codeforge": 16,
    }
    assert dict(production_48.min_real_exact_64k_worlds_by_domain) == {
        "company": 16,
        "researchlab": 16,
        "codeforge": 16,
    }
    assert production_48.min_unique_executable_proofs == 48
    assert production_48.min_unique_answer_programs == 12
    assert production_48.min_unique_semantic_base_tasks == 48
    assert production_210.predecessor_profile_id == production_48.profile_id
    assert production_210.expected_promoted_worlds == 210
    assert production_210.min_unique_executable_proofs == 210
    assert production_210.min_unique_answer_programs == 24
    assert production_210.min_unique_semantic_base_tasks == 210


def test_only_current_production_profiles_are_issuable() -> None:
    assert (
        issuable_release_profile("p10-source-rich-production-48-v1").environment
        == "production"
    )
    with pytest.raises(ValueError, match="superseded production release profile"):
        issuable_release_profile("p3-production-48-v1")


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


def test_current_profiles_bind_the_exact_tokenizer_asset_manifest(
    monkeypatch,
) -> None:
    profile_id = "p7-source-rich-probe-12-v1"
    original = release_profile_sha256(profile_id)
    profile = release_profile(profile_id)

    assert profile.tokenizer_asset_manifest_sha256 == (
        "bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb"
    )
    monkeypatch.setitem(
        RELEASE_PROFILES,
        profile_id,
        replace(profile, tokenizer_asset_manifest_sha256="c" * 64),
    )

    assert release_profile_sha256(profile_id) != original


def test_new_p7_gates_do_not_rewrite_frozen_p6_profile_digest() -> None:
    assert release_profile_sha256("p6-source-dependent-probe-12-v1") == (
        "68601a08ceabc7e6c5dec0a49a398318ebb596a1da65a3ac4bcdc35b5585b495"
    )
    assert release_profile_sha256("p3-production-48-v1") == (
        "dd64b8acf02b224fff6658008395447c1500aaa91368ac324a23d6d656ed70fe"
    )
    assert release_profile_sha256("p3-production-210-v1") == (
        "4ebb72d06a058b74d22360fc10a55b1b0621f8a37f76ab86e43e4f1d4508188f"
    )


def test_p7_sec_slice_is_an_explicit_one_world_engineering_gate() -> None:
    profile = release_profile("p7-sec-source-slice-1-v1")

    assert profile.expected_promoted_worlds == 1
    assert profile.promoted_domain_world_quotas == (("company", 1),)
    assert profile.min_real_source_relations == 1
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_real_exact_64k_rows_by_domain == (("company", 1),)
    assert profile.min_real_exact_64k_worlds_by_domain == (("company", 1),)


def test_p7_wiki_slice_is_an_explicit_one_world_engineering_gate() -> None:
    profile = release_profile("p7-wiki-source-slice-1-v1")

    assert profile.expected_promoted_worlds == 1
    assert profile.promoted_domain_world_quotas == (("researchlab", 1),)
    assert profile.min_real_source_relations == 1
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_real_exact_64k_rows_by_domain == (("researchlab", 1),)
    assert profile.min_real_exact_64k_worlds_by_domain == (("researchlab", 1),)


def test_p7_paper_slice_is_an_explicit_one_world_engineering_gate() -> None:
    profile = release_profile("p7-paper-source-slice-1-v1")

    assert profile.expected_promoted_worlds == 1
    assert profile.promoted_domain_world_quotas == (("researchlab", 1),)
    assert profile.min_real_source_relations == 1
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_real_exact_64k_rows_by_domain == (("researchlab", 1),)
    assert profile.min_real_exact_64k_worlds_by_domain == (("researchlab", 1),)


def test_p7_github_slice_is_an_explicit_one_world_engineering_gate() -> None:
    profile = release_profile("p7-github-source-slice-1-v1")

    assert profile.expected_promoted_worlds == 1
    assert profile.promoted_domain_world_quotas == (("codeforge", 1),)
    assert profile.min_real_source_relations == 1
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_real_exact_64k_rows_by_domain == (("codeforge", 1),)
    assert profile.min_real_exact_64k_worlds_by_domain == (("codeforge", 1),)
