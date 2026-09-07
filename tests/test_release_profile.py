from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from longworld.core.release_profile import (
    CURRENT_RELEASE_GATE_ONLY_PROFILE_IDS,
    LENGTH_VIEW_PAIR_PROFILE_IDS,
    RELATION_PROVENANCE_SPLIT_PROFILE_IDS,
    RELEASE_PROFILES,
    SUBSTANTIAL_REAL_PROOF_GROWTH_PROFILE_IDS,
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


def test_p12_current_gate_probe_is_a_new_strict_root() -> None:
    profile = release_profile("p12-current-source-probe-12-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 12
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (10, 2)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (10, 2)
    assert profile.min_source_families == 4
    assert profile.min_unique_real_source_workflows == 12
    assert profile.min_unique_executable_proofs == 12
    assert profile.min_unique_answer_programs == 8
    assert profile.min_unique_semantic_base_tasks == 8
    assert profile.min_motifs == 8
    assert dict(profile.promoted_domain_world_quotas) == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {
        "company": 4,
        "researchlab": 4,
        "codeforge": 4,
    }


def test_p12_current_v2_adds_per_world_bands_without_rewriting_v1() -> None:
    historical = release_profile("p12-current-source-probe-12-v1")
    profile = release_profile("p12-current-source-probe-12-v2")

    assert historical.predecessor_profile_id is None
    assert profile.predecessor_profile_id is None
    assert historical.required_exact_length_buckets == ()
    assert profile.required_exact_length_buckets == ("16k", "32k", "64k")
    historical_contract = asdict(historical)
    current_contract = asdict(profile)
    for field in ("profile_id", "required_exact_length_buckets"):
        historical_contract.pop(field)
        current_contract.pop(field)
    assert current_contract == historical_contract


def test_p13_authentic_six_domain_profile_is_an_immutable_probe_root() -> None:
    profile = release_profile("p13-authentic-six-domain-probe-12-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 12
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (10, 2)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (10, 2)
    assert profile.split_strategy == "world_atomic_domain_stratified_hash_v1"
    assert profile.min_domains == 6
    assert dict(profile.promoted_domain_world_quotas) == {
        "company": 2,
        "researchlab": 2,
        "codeforge": 2,
        "finance": 2,
        "cyber": 2,
        "macro_economics": 2,
    }
    assert dict(profile.min_train_worlds_by_domain) == {
        domain: 1 for domain, _quota in profile.promoted_domain_world_quotas
    }
    assert profile.min_eval_domains == 2
    assert profile.require_all_rows_source_bound is True
    assert profile.required_exact_length_buckets == ("16k", "32k", "64k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert profile.min_real_64k_rows == 36
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {
        domain: 6 for domain, _quota in profile.promoted_domain_world_quotas
    }
    assert dict(profile.min_exact_64k_rows_by_domain) == {
        domain: 6 for domain, _quota in profile.promoted_domain_world_quotas
    }
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {
        domain: 2 for domain, _quota in profile.promoted_domain_world_quotas
    }
    assert profile.min_unique_real_source_workflows == 12
    assert profile.min_unique_executable_proofs == 12
    assert profile.min_unique_answer_programs == 12
    assert profile.min_unique_semantic_base_tasks == 12
    assert profile.min_motifs == 11
    assert release_profile_sha256(profile.profile_id) == (
        "a0c2148ab2524d8a5130bef85b7e519db76244ccd8453b0f917dc3a8d79ae030"
    )


def test_p15_128k_extension_profile_accepts_two_honest_partial_band_worlds() -> None:
    profile = release_profile("p15-authentic-128k-extension-probe-2-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 2
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (2, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (2, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("64k", "128k")
    assert profile.required_exact_length_buckets == ("64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {
        "codeforge": 1,
        "cyber": 1,
    }
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {
        "codeforge": 3,
        "cyber": 3,
    }
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {
        "codeforge": 1,
        "cyber": 1,
    }
    assert profile.require_all_rows_source_bound is True


def test_p16_macro_bea_128k_extension_is_a_one_world_four_band_gate() -> None:
    profile = release_profile("p16-macro-bea-128k-extension-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("16k", "32k", "64k", "128k")
    assert profile.required_exact_length_buckets == ("16k", "32k", "64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"macro_economics": 1}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"macro_economics": 3}
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {"macro_economics": 1}
    assert profile.require_all_rows_source_bound is True
    assert profile.min_real_64k_rows == 3
    assert profile.min_motifs == 1


def test_p17_finance_128k_extension_is_a_one_world_four_band_gate() -> None:
    profile = release_profile("p17-finance-128k-extension-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("16k", "32k", "64k", "128k")
    assert profile.required_exact_length_buckets == ("16k", "32k", "64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"finance": 1}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"finance": 3}
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {"finance": 1}
    assert profile.require_all_rows_source_bound is True
    assert profile.min_real_64k_rows == 3
    assert profile.min_motifs == 1


def test_p17_codeforge_128k_extension_requires_two_real_proofs() -> None:
    profile = release_profile("p17-codeforge-128k-extension-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("64k", "128k")
    assert profile.required_exact_length_buckets == ("64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"codeforge": 1}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"codeforge": 3}
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {"codeforge": 1}
    assert profile.min_unique_executable_proofs == 2
    assert profile.min_unique_answer_programs == 2
    assert profile.require_all_rows_source_bound is True


def test_p38_ietf_64k_extension_is_a_one_world_standards_gate() -> None:
    profile = release_profile("p38-ietf-oauth-64k-extension-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("64k",)
    assert profile.required_exact_length_buckets == ("64k",)
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"standards": 1}
    assert dict(profile.min_exact_64k_rows_by_domain) == {"standards": 3}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"standards": 3}
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {"standards": 1}
    assert dict(profile.min_train_worlds_by_domain) == {"standards": 1}
    assert profile.min_source_families == 1
    assert profile.min_real_base_tasks == 1
    assert profile.min_real_source_relations == 1
    assert profile.min_real_64k_rows == 3
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_motifs == 1
    assert profile.min_unique_executable_proofs == 1
    assert profile.min_unique_answer_programs == 1
    assert profile.min_unique_semantic_base_tasks == 1
    assert profile.require_all_rows_source_bound is True
    assert profile.profile_id in RELATION_PROVENANCE_SPLIT_PROFILE_IDS
    assert profile.profile_id in SUBSTANTIAL_REAL_PROOF_GROWTH_PROFILE_IDS
    assert profile.profile_id in CURRENT_RELEASE_GATE_ONLY_PROFILE_IDS


def test_p43_ietf_lower_band_extension_is_a_one_world_standards_gate() -> None:
    profile = release_profile("p43-ietf-oauth-16k-64k-extension-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("16k", "64k")
    assert profile.required_exact_length_buckets == ("16k", "64k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"standards": 1}
    assert dict(profile.min_exact_64k_rows_by_domain) == {"standards": 3}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"standards": 3}
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {"standards": 1}
    assert profile.require_all_rows_source_bound is True
    assert profile.profile_id in RELATION_PROVENANCE_SPLIT_PROFILE_IDS
    assert profile.profile_id in SUBSTANTIAL_REAL_PROOF_GROWTH_PROFILE_IDS
    assert profile.profile_id in CURRENT_RELEASE_GATE_ONLY_PROFILE_IDS


def test_p57_ietf_tls13_64k_128k_extension_is_a_one_world_standards_gate() -> None:
    profile = release_profile("p57-ietf-tls13-64k-128k-extension-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("64k", "128k")
    assert profile.required_exact_length_buckets == ("64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"standards": 1}
    assert dict(profile.min_exact_64k_rows_by_domain) == {"standards": 3}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"standards": 3}
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {"standards": 1}
    assert dict(profile.min_train_worlds_by_domain) == {"standards": 1}
    assert profile.min_unique_executable_proofs == 1
    assert profile.min_unique_answer_programs == 1
    assert profile.require_all_rows_source_bound is True
    assert profile.profile_id in RELATION_PROVENANCE_SPLIT_PROFILE_IDS
    assert profile.profile_id in SUBSTANTIAL_REAL_PROOF_GROWTH_PROFILE_IDS
    assert profile.profile_id in CURRENT_RELEASE_GATE_ONLY_PROFILE_IDS
    assert profile.profile_id in LENGTH_VIEW_PAIR_PROFILE_IDS


def test_p40_ietf_semantic_growth_requires_nested_32k_64k_and_128k() -> None:
    profile = release_profile("p40-ietf-oauth-semantic-growth-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.expected_promoted_worlds == 1
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("32k", "64k", "128k")
    assert profile.required_exact_length_buckets == ("32k", "64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"standards": 1}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {"standards": 3}
    assert profile.min_unique_executable_proofs == 3
    assert profile.require_all_rows_source_bound is True
    assert profile.profile_id in RELATION_PROVENANCE_SPLIT_PROFILE_IDS
    assert profile.profile_id in SUBSTANTIAL_REAL_PROOF_GROWTH_PROFILE_IDS
    assert profile.profile_id in CURRENT_RELEASE_GATE_ONLY_PROFILE_IDS


def test_p52_govinfo_disposition_requires_nested_32k_64k_and_128k() -> None:
    profile = release_profile("p52-govinfo-bill-disposition-probe-1-v1")

    assert profile.environment == "probe"
    assert profile.predecessor_profile_id is None
    assert profile.expected_promoted_worlds == 1
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (1, 0)
    assert (profile.min_real_train_worlds, profile.min_real_eval_worlds) == (1, 0)
    assert profile.training_conditions == ("B5",)
    assert profile.training_length_buckets == ("32k", "64k", "128k")
    assert profile.required_exact_length_buckets == ("32k", "64k", "128k")
    assert profile.required_view_timings == (
        ("full", "first"),
        ("cf", "first"),
        ("ordered_artifact_view", "first"),
    )
    assert dict(profile.promoted_domain_world_quotas) == {"government_legislation": 1}
    assert dict(profile.min_exact_64k_rows_by_domain) == {"government_legislation": 3}
    assert dict(profile.min_real_exact_64k_rows_by_domain) == {
        "government_legislation": 3
    }
    assert dict(profile.min_real_exact_64k_worlds_by_domain) == {
        "government_legislation": 1
    }
    assert profile.min_source_families == 2
    assert profile.min_real_source_relations == 6
    assert profile.min_unique_executable_proofs == 3
    assert profile.min_unique_answer_programs == 1
    assert profile.min_unique_semantic_base_tasks == 1
    assert profile.require_all_rows_source_bound is True
    assert profile.profile_id in RELATION_PROVENANCE_SPLIT_PROFILE_IDS
    assert profile.profile_id in SUBSTANTIAL_REAL_PROOF_GROWTH_PROFILE_IDS
    assert profile.profile_id in CURRENT_RELEASE_GATE_ONLY_PROFILE_IDS


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


def test_no_production_profile_is_issuable_before_the_current_probe_passes() -> None:
    assert issuable_release_profile("p12-current-source-probe-12-v1").environment == (
        "probe"
    )
    production_profile_ids = {
        profile_id
        for profile_id, profile in RELEASE_PROFILES.items()
        if profile.environment == "production"
    }
    assert production_profile_ids
    for profile_id in production_profile_ids:
        with pytest.raises(ValueError, match="superseded production release profile"):
            issuable_release_profile(profile_id)


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
    assert release_profile_sha256("p7-source-rich-probe-12-v1") == (
        "846b09bfc977672ef6f5985fd807ea7423ac1a279a24790080d0267c8ffa5a31"
    )
    assert release_profile_sha256("p10-source-rich-production-48-v1") == (
        "de7da44b3710102fb215c26ed9cdb08bfe317f8b3e9c125208c3ea02e3ad877a"
    )


def test_p7_sec_slice_is_an_explicit_one_world_engineering_gate() -> None:
    profile = release_profile("p7-sec-source-slice-1-v1")

    assert profile.expected_promoted_worlds == 1
    assert profile.promoted_domain_world_quotas == (("company", 1),)
    assert profile.min_real_source_relations == 1
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_real_exact_64k_rows_by_domain == (("company", 1),)
    assert profile.min_real_exact_64k_worlds_by_domain == (("company", 1),)


def test_p12_sec_slice_adds_128k_without_changing_historical_training_buckets() -> None:
    historical = release_profile("p7-sec-source-slice-1-v1")
    profile = release_profile("p12-sec-source-slice-1-v1")

    assert all(
        candidate.training_length_buckets == ("16k", "32k", "64k")
        for profile_id, candidate in RELEASE_PROFILES.items()
        if profile_id
        not in {
            profile.profile_id,
            "p15-authentic-128k-extension-probe-2-v1",
            "p16-macro-bea-128k-extension-probe-1-v1",
            "p17-finance-128k-extension-probe-1-v1",
            "p17-codeforge-128k-extension-probe-1-v1",
            "p38-ietf-oauth-64k-extension-probe-1-v1",
            "p43-ietf-oauth-16k-64k-extension-probe-1-v1",
            "p40-ietf-oauth-semantic-growth-probe-1-v1",
            "p52-govinfo-bill-disposition-probe-1-v1",
            "p57-ietf-tls13-64k-128k-extension-probe-1-v1",
        }
    )
    assert historical.training_length_buckets == ("16k", "32k", "64k")
    assert profile.training_length_buckets == ("16k", "32k", "64k", "128k")
    assert historical.required_exact_length_buckets == ()
    assert profile.required_exact_length_buckets == (
        "16k",
        "32k",
        "64k",
        "128k",
    )
    historical_contract = asdict(historical)
    p12_contract = asdict(profile)
    for field in (
        "profile_id",
        "training_length_buckets",
        "required_exact_length_buckets",
    ):
        historical_contract.pop(field)
        p12_contract.pop(field)
    assert p12_contract == historical_contract


def test_p7_wiki_slice_is_an_explicit_one_world_engineering_gate() -> None:
    profile = release_profile("p7-wiki-source-slice-1-v1")

    assert profile.expected_promoted_worlds == 1
    assert profile.promoted_domain_world_quotas == (("researchlab", 1),)
    assert profile.min_real_source_relations == 1
    assert profile.min_unique_real_source_workflows == 1
    assert profile.min_real_exact_64k_rows_by_domain == (("researchlab", 1),)
    assert profile.min_real_exact_64k_worlds_by_domain == (("researchlab", 1),)


def test_p12_wiki_slice_adds_per_world_bands_without_rewriting_p7() -> None:
    historical = release_profile("p7-wiki-source-slice-1-v1")
    profile = release_profile("p12-wiki-source-slice-1-v1")

    assert historical.required_exact_length_buckets == ()
    assert profile.required_exact_length_buckets == ("16k", "32k", "64k")
    historical_contract = asdict(historical)
    current_contract = asdict(profile)
    for field in ("profile_id", "required_exact_length_buckets"):
        historical_contract.pop(field)
        current_contract.pop(field)
    assert current_contract == historical_contract


def test_historical_p7_and_p12_current_profiles_remain_immutable() -> None:
    expected_digests = {
        "p7-source-rich-probe-12-v1": (
            "846b09bfc977672ef6f5985fd807ea7423ac1a279a24790080d0267c8ffa5a31"
        ),
        "p7-sec-source-slice-1-v1": (
            "a2b08e46b0fe9cfed32ca15c2c01b1074bcac9876432dc42fd803d26d1dbce84"
        ),
        "p7-wiki-source-slice-1-v1": (
            "b67c47e4903d3535e5b556ee7326069ce429d882ffe7e73efac62ba17b7eee1d"
        ),
        "p7-paper-source-slice-1-v1": (
            "0e42fc5c44f85bf537fe45439d06cf7c958ff7d9b39f04e86150ca1d7849c2ab"
        ),
        "p7-github-source-slice-1-v1": (
            "9118b164b7be4fc8a8d4f97ac065df2e7d2170b3a312b59bfae9c9fe0e53719c"
        ),
        "p12-current-source-probe-12-v1": (
            "14a8ee853f6f82078618c101a3277a7304576f23c451f8a646b8ad56757408ed"
        ),
    }

    assert {
        profile_id: release_profile_sha256(profile_id)
        for profile_id in expected_digests
    } == expected_digests
    assert all(
        release_profile(profile_id).required_exact_length_buckets == ()
        for profile_id in expected_digests
    )


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
