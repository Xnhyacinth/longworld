from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import quality_gate
from generate import _buckets_for_split, build_generation_jobs, workstreams_for_domain
from quality_gate import evaluate_quality

from longworld.core.release_profile import release_profile
from longworld.core.sampler import balanced_domain_schedule

ROOT = Path(__file__).resolve().parents[1]


def test_p4_probe_is_balanced_local_probe_with_p3_correctness_gates() -> None:
    cfg = yaml.safe_load((ROOT / "configs" / "p4_multidomain_probe.yaml").read_text())
    profile = release_profile(cfg["release_profile_id"])
    p3 = release_profile("p3-probe-12-v1")

    assert cfg["trust_scope"] == "local_probe"
    assert cfg["required_seed_start"] == 1
    assert cfg["data_stage"] == "candidate"
    assert cfg["domains"] == ["company", "researchlab", "codeforge"]
    assert cfg["domain_world_quotas"] == {
        "company": 6,
        "researchlab": 6,
        "codeforge": 6,
    }
    assert cfg["domain_n_workstreams"] == {
        "company": 36,
        "researchlab": 88,
        "codeforge": 36,
    }
    assert workstreams_for_domain(cfg, "researchlab") == 88
    assert workstreams_for_domain(cfg, "company") == 36
    assert _buckets_for_split(cfg, "train", "researchlab")["64k"] == 71_500
    assert _buckets_for_split(cfg, "train", "company")["64k"] == 58_000
    assert _buckets_for_split(cfg, "train", "codeforge")["64k"] == 44_500
    assert sum(cfg["domain_world_quotas"].values()) == cfg["n_worlds"] == 18
    assert cfg["target_promoted_worlds"] == 12
    assert cfg["strict_semantic_verification"] is True
    assert cfg["strict_cf_replay"] is True
    assert cfg["allow_legacy_sources"] is False
    assert cfg["extra_filler_worlds"] == cfg["n_anchors"] == cfg["n_source_pack"] == 0

    assert profile.environment == "probe"
    assert profile.expected_promoted_worlds == 12
    assert profile.min_domains == 3
    assert profile.promoted_domain_world_quotas == (
        ("company", 4),
        ("researchlab", 4),
        ("codeforge", 4),
    )
    assert profile.min_exact_64k_rows_by_domain == (
        ("company", 1),
        ("researchlab", 1),
        ("codeforge", 1),
    )
    assert p3.promoted_domain_world_quotas == ()
    assert profile.predecessor_profile_id is None
    assert profile.max_boilerplate == p3.max_boilerplate
    assert profile.max_pulse == p3.max_pulse
    assert profile.min_internal_growth == p3.min_internal_growth
    assert profile.max_generic_growth_share == p3.max_generic_growth_share
    assert profile.max_near_dup_sentence_ratio == p3.max_near_dup_sentence_ratio
    assert profile.dense_top_k == p3.dense_top_k


def test_p6_probe_requires_both_real_workflow_domains() -> None:
    cfg = yaml.safe_load(
        (ROOT / "configs" / "p6_source_dependent_probe.yaml").read_text()
    )
    profile = release_profile(cfg["release_profile_id"])

    assert profile.profile_id == "p6-source-dependent-probe-12-v1"
    assert profile.min_real_train_worlds == 2
    assert profile.min_source_families == 3
    assert profile.min_real_base_tasks == 5
    assert profile.min_real_source_relations == 7
    assert profile.min_real_64k_rows == 10
    assert cfg["source_workflow_seeds"] == [2]
    assert cfg["real_workflow_seeds"] == [3]


def test_balanced_domain_schedule_is_exact_and_deterministic() -> None:
    domains = ["company", "researchlab", "codeforge"]
    quotas = {"company": 3, "researchlab": 2, "codeforge": 4}

    first = balanced_domain_schedule(domains, n_worlds=9, quotas=quotas)
    second = balanced_domain_schedule(domains, n_worlds=9, quotas=quotas)

    assert first == second
    assert first == [
        "company",
        "researchlab",
        "codeforge",
        "company",
        "researchlab",
        "codeforge",
        "company",
        "codeforge",
        "codeforge",
    ]
    assert Counter(first) == quotas


def test_automatic_schedule_supports_fewer_worlds_than_domains() -> None:
    assert balanced_domain_schedule(
        ["company", "researchlab", "codeforge"], n_worlds=2
    ) == ["company", "researchlab"]


def test_generation_jobs_bind_domains_before_parallel_execution() -> None:
    cfg = yaml.safe_load((ROOT / "configs" / "p4_multidomain_probe.yaml").read_text())

    jobs, planned = build_generation_jobs(
        cfg,
        n_worlds=cfg["n_worlds"],
        seed_start=1,
        n_train_worlds=13,
    )

    assert planned == cfg["domain_world_quotas"]
    assert Counter(job[3] for job in jobs) == cfg["domain_world_quotas"]
    assert jobs[:3] == [
        (1, cfg, "train", "company"),
        (2, cfg, "train", "researchlab"),
        (3, cfg, "train", "codeforge"),
    ]
    assert sum(job[2] == "train" for job in jobs) == 13


def test_real_probe_seed_routing_fails_closed_on_seed_start_change() -> None:
    cfg = yaml.safe_load((ROOT / "configs" / "p4_multidomain_probe.yaml").read_text())

    with pytest.raises(ValueError, match="required_seed_start"):
        build_generation_jobs(
            cfg,
            n_worlds=cfg["n_worlds"],
            seed_start=2,
            n_train_worlds=13,
        )


def test_quality_gate_rejects_unbalanced_p4_promoted_worlds() -> None:
    profile_id = "p4-multidomain-probe-12-v1"
    domains = ["company"] * 5 + ["researchlab"] * 3 + ["codeforge"] * 4
    rows = [
        {
            "data_stage": "train_ready",
            "world_id": f"world-{index:02d}",
            "domain": domain,
            "motif": f"motif-{index % 5}",
            "context": f"context-{index}",
            "question": f"question-{index}",
            "answer": f"answer-{index}",
            "split": "train" if index < 10 else "eval",
            "promotion": {},
        }
        for index, domain in enumerate(domains)
    ]
    report = {
        "data_stage": "train_ready",
        "release_profile_id": profile_id,
        "release_profile_sha256": "invalid-for-this-focused-check",
        "target_promoted_worlds": 12,
        "n_worlds": 12,
        "retention": 1.0,
    }

    result = evaluate_quality(
        report,
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id=profile_id,
    )

    assert result["worlds_by_domain"] == {
        "company": 5,
        "researchlab": 3,
        "codeforge": 4,
    }
    assert (
        "release_domain_worlds={'company': 5, 'researchlab': 3, 'codeforge': 4} "
        "expected={'company': 4, 'researchlab': 4, 'codeforge': 4}" in result["errors"]
    )


def test_p4_gate_requires_exact_64k_rows_in_every_domain(monkeypatch) -> None:
    profile_id = "p4-multidomain-probe-12-v1"
    rows = [
        {
            "world_id": f"world-{domain}",
            "domain": domain,
            "motif": f"motif-{domain}",
            "context": f"context-{domain}",
            "question": "q",
            "answer": "a",
            "split": "train",
            "length_bucket": "16k" if domain == "researchlab" else "64k",
        }
        for domain in ("company", "researchlab", "codeforge")
    ]
    monkeypatch.setattr(
        quality_gate,
        "_has_exact_64k_metadata",
        lambda row, **_kwargs: row.get("length_bucket") == "64k",
    )

    result = evaluate_quality(
        {
            "release_profile_id": profile_id,
            "release_profile_sha256": "focused-check",
            "retention": 0.5,
            "n_worlds": 12,
            "target_promoted_worlds": 12,
        },
        rows,
        min_retention=0.0,
        max_retention=1.0,
        max_boilerplate=1.0,
        max_pulse=1.0,
        min_internal_growth=0,
        max_generic_growth_share=1.0,
        release_profile_id=profile_id,
    )

    assert result["exact_64k_rows_by_domain"] == {
        "company": 1,
        "researchlab": 0,
        "codeforge": 1,
    }
    assert "release_domain_exact_64k_rows:researchlab=0<1" in result["errors"]


@pytest.mark.parametrize(
    ("domains", "n_worlds", "quotas", "message"),
    [
        (["company", "researchlab"], 3, {"company": 1}, "exactly match"),
        (
            ["company", "researchlab"],
            3,
            {"company": 1, "researchlab": 1},
            "sum to n_worlds",
        ),
        (
            ["company", "researchlab"],
            3,
            {"company": 0, "researchlab": 3},
            "positive integers",
        ),
    ],
)
def test_balanced_domain_schedule_rejects_invalid_quotas(
    domains: list[str], n_worlds: int, quotas: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        balanced_domain_schedule(domains, n_worlds=n_worlds, quotas=quotas)
