from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate import build_generation_jobs

from longworld.core.promotion import PromotionError, validate_predecessor_gate_receipt
from longworld.core.release_profile import release_profile

ROOT = Path(__file__).resolve().parents[1]


def _config(name: str) -> dict:
    return yaml.safe_load((ROOT / "configs" / name).read_text())


def test_p4_local_48_profile_is_balanced_probe_with_probe_predecessor() -> None:
    cfg = _config("p4_multidomain_local_48.yaml")
    profile = release_profile(cfg["release_profile_id"])

    assert cfg["trust_scope"] == "local_probe"
    assert profile.environment == "probe"
    assert profile.predecessor_profile_id == "p4-multidomain-probe-12-v1"
    assert profile.expected_promoted_worlds == cfg["target_promoted_worlds"] == 48
    assert (profile.min_train_worlds, profile.min_eval_worlds) == (40, 8)
    assert profile.promoted_domain_world_quotas == (
        ("company", 16),
        ("researchlab", 16),
        ("codeforge", 16),
    )
    assert profile.min_exact_64k_rows_by_domain == (
        ("company", 1),
        ("researchlab", 1),
        ("codeforge", 1),
    )


def test_p4_local_48_fails_closed_without_predecessor_gate_receipt() -> None:
    with pytest.raises(PromotionError, match="predecessor gate receipt"):
        validate_predecessor_gate_receipt(
            None,
            "p4-multidomain-local-48-v1",
            attestation_key=b"local-48-predecessor-test-key-32b",
            key_id="local-48-predecessor-test",
        )


def test_p4_local_48_config_scales_worlds_without_weakening_p4_probe() -> None:
    probe = _config("p4_multidomain_probe.yaml")
    cfg = _config("p4_multidomain_local_48.yaml")

    assert cfg["n_worlds"] == 72
    assert cfg["domain_world_quotas"] == {
        "company": 24,
        "researchlab": 24,
        "codeforge": 24,
    }
    for field in (
        "domains",
        "required_seed_start",
        "n_parallel",
        "n_pulses",
        "domain_n_workstreams",
        "include_program_joins",
        "exact_tokenizer",
        "length_buckets",
        "domain_length_buckets",
        "real_workflow_bundle",
        "real_workflow_seeds",
        "real_workflow_query_types",
        "real_workflow_length_buckets",
        "real_workflow_query_length_buckets",
        "real_workflow_views",
        "n_anchors",
        "n_source_pack",
        "extra_filler_worlds",
        "allow_legacy_sources",
        "strict_semantic_verification",
        "strict_cf_replay",
        "require_producer_attestation",
        "release_min_evidence_tokens",
        "min_internal_growth",
        "max_generic_growth_share",
    ):
        assert cfg[field] == probe[field]


def test_p4_local_48_generation_jobs_are_exactly_balanced() -> None:
    cfg = _config("p4_multidomain_local_48.yaml")

    jobs, planned = build_generation_jobs(
        cfg,
        n_worlds=cfg["n_worlds"],
        seed_start=cfg["required_seed_start"],
        n_train_worlds=54,
    )

    assert len(jobs) == 72
    assert planned == cfg["domain_world_quotas"]
    assert Counter(job[3] for job in jobs) == cfg["domain_world_quotas"]
    assert jobs[:3] == [
        (1, cfg, "train", "company"),
        (2, cfg, "train", "researchlab"),
        (3, cfg, "train", "codeforge"),
    ]
