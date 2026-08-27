"""Immutable release gates for probe and production LongWorld products."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReleaseProfile:
    profile_id: str
    environment: str
    predecessor_profile_id: str | None
    expected_promoted_worlds: int
    min_train_worlds: int
    min_eval_worlds: int
    min_real_train_worlds: int
    min_real_eval_worlds: int
    split_strategy: str
    training_conditions: tuple[str, ...]
    training_length_buckets: tuple[str, ...]
    training_export_seed: int
    max_training_token_spread: float
    llamafactory_transform_revision: str
    swift_transform_revision: str
    min_retention: float
    max_retention: float
    min_promotion_retention: float
    max_boilerplate: float
    max_pulse: float
    min_internal_growth: int
    max_generic_growth_share: float
    max_near_dup_sentence_ratio: float
    min_domains: int
    promoted_domain_world_quotas: tuple[tuple[str, int], ...]
    min_exact_64k_rows_by_domain: tuple[tuple[str, int], ...]
    min_motifs: int
    min_source_families: int
    min_real_base_tasks: int
    min_real_source_relations: int
    min_real_64k_rows: int
    min_unique_real_source_workflows: int
    min_real_exact_64k_rows_by_domain: tuple[tuple[str, int], ...]
    min_real_exact_64k_worlds_by_domain: tuple[tuple[str, int], ...]
    tokenizer_model_id: str
    tokenizer_revision: str
    dense_top_k: int


def _profile(
    profile_id: str,
    environment: str,
    expected_promoted_worlds: int,
    min_source_families: int,
    min_real_base_tasks: int,
    min_real_source_relations: int,
    min_real_64k_rows: int,
    min_train_worlds: int,
    min_eval_worlds: int,
    min_real_train_worlds: int,
    min_real_eval_worlds: int,
    predecessor_profile_id: str | None,
    min_domains: int,
    promoted_domain_world_quotas: tuple[tuple[str, int], ...] = (),
    min_exact_64k_rows_by_domain: tuple[tuple[str, int], ...] = (),
    min_unique_real_source_workflows: int = 0,
    min_real_exact_64k_rows_by_domain: tuple[tuple[str, int], ...] = (),
    min_real_exact_64k_worlds_by_domain: tuple[tuple[str, int], ...] = (),
    min_motifs: int = 5,
) -> ReleaseProfile:
    return ReleaseProfile(
        profile_id=profile_id,
        environment=environment,
        predecessor_profile_id=predecessor_profile_id,
        expected_promoted_worlds=expected_promoted_worlds,
        min_train_worlds=min_train_worlds,
        min_eval_worlds=min_eval_worlds,
        min_real_train_worlds=min_real_train_worlds,
        min_real_eval_worlds=min_real_eval_worlds,
        split_strategy="world_atomic_hash_v1",
        training_conditions=("B1", "B3", "B5", "B5w"),
        training_length_buckets=("16k", "32k", "64k"),
        training_export_seed=0,
        max_training_token_spread=0.05,
        llamafactory_transform_revision="longworld-llamafactory-sharegpt-v4",
        swift_transform_revision="longworld-swift-messages-v2",
        min_retention=0.0001,
        max_retention=1.0,
        min_promotion_retention=0.5,
        max_boilerplate=0.15,
        max_pulse=0.0,
        min_internal_growth=4096,
        max_generic_growth_share=0.20,
        max_near_dup_sentence_ratio=0.25,
        min_domains=min_domains,
        promoted_domain_world_quotas=promoted_domain_world_quotas,
        min_exact_64k_rows_by_domain=min_exact_64k_rows_by_domain,
        min_motifs=min_motifs,
        min_source_families=min_source_families,
        min_real_base_tasks=min_real_base_tasks,
        min_real_source_relations=min_real_source_relations,
        min_real_64k_rows=min_real_64k_rows,
        min_unique_real_source_workflows=min_unique_real_source_workflows,
        min_real_exact_64k_rows_by_domain=min_real_exact_64k_rows_by_domain,
        min_real_exact_64k_worlds_by_domain=min_real_exact_64k_worlds_by_domain,
        tokenizer_model_id="Qwen/Qwen3.5-4B",
        tokenizer_revision="a7b0d22b993d71000cf2eadfb37222a67cee521e",
        dense_top_k=3,
    )


RELEASE_PROFILES = {
    "p3-probe-12-v1": _profile(
        profile_id="p3-probe-12-v1",
        environment="probe",
        expected_promoted_worlds=12,
        min_source_families=1,
        min_real_base_tasks=3,
        min_real_source_relations=3,
        min_real_64k_rows=6,
        min_train_worlds=10,
        min_eval_worlds=2,
        min_real_train_worlds=1,
        min_real_eval_worlds=0,
        predecessor_profile_id=None,
        min_domains=1,
    ),
    "p4-multidomain-probe-12-v1": _profile(
        profile_id="p4-multidomain-probe-12-v1",
        environment="probe",
        expected_promoted_worlds=12,
        min_source_families=1,
        min_real_base_tasks=3,
        min_real_source_relations=3,
        min_real_64k_rows=6,
        min_train_worlds=10,
        min_eval_worlds=2,
        min_real_train_worlds=1,
        min_real_eval_worlds=0,
        predecessor_profile_id=None,
        min_domains=3,
        promoted_domain_world_quotas=(
            ("company", 4),
            ("researchlab", 4),
            ("codeforge", 4),
        ),
        min_exact_64k_rows_by_domain=(
            ("company", 1),
            ("researchlab", 1),
            ("codeforge", 1),
        ),
    ),
    "p6-source-dependent-probe-12-v1": _profile(
        profile_id="p6-source-dependent-probe-12-v1",
        environment="probe",
        expected_promoted_worlds=12,
        min_source_families=3,
        min_real_base_tasks=5,
        min_real_source_relations=7,
        min_real_64k_rows=10,
        min_train_worlds=10,
        min_eval_worlds=2,
        min_real_train_worlds=2,
        min_real_eval_worlds=0,
        predecessor_profile_id=None,
        min_domains=3,
        promoted_domain_world_quotas=(
            ("company", 4),
            ("researchlab", 4),
            ("codeforge", 4),
        ),
        min_exact_64k_rows_by_domain=(
            ("company", 1),
            ("researchlab", 1),
            ("codeforge", 1),
        ),
    ),
    "p7-source-rich-probe-12-v1": _profile(
        profile_id="p7-source-rich-probe-12-v1",
        environment="probe",
        expected_promoted_worlds=12,
        min_source_families=4,
        min_real_base_tasks=12,
        min_real_source_relations=12,
        min_real_64k_rows=48,
        min_train_worlds=10,
        min_eval_worlds=2,
        min_real_train_worlds=10,
        min_real_eval_worlds=2,
        predecessor_profile_id="p6-source-dependent-probe-12-v1",
        min_domains=3,
        promoted_domain_world_quotas=(
            ("company", 4),
            ("researchlab", 4),
            ("codeforge", 4),
        ),
        min_exact_64k_rows_by_domain=(
            ("company", 12),
            ("researchlab", 12),
            ("codeforge", 12),
        ),
        min_unique_real_source_workflows=12,
        min_real_exact_64k_rows_by_domain=(
            ("company", 12),
            ("researchlab", 12),
            ("codeforge", 12),
        ),
        min_real_exact_64k_worlds_by_domain=(
            ("company", 4),
            ("researchlab", 4),
            ("codeforge", 4),
        ),
    ),
    "p7-sec-source-slice-1-v1": _profile(
        profile_id="p7-sec-source-slice-1-v1",
        environment="probe",
        expected_promoted_worlds=1,
        min_source_families=1,
        min_real_base_tasks=1,
        min_real_source_relations=0,
        min_real_64k_rows=1,
        min_train_worlds=1,
        min_eval_worlds=0,
        min_real_train_worlds=1,
        min_real_eval_worlds=0,
        predecessor_profile_id=None,
        min_domains=1,
        promoted_domain_world_quotas=(("company", 1),),
        min_exact_64k_rows_by_domain=(("company", 1),),
        min_unique_real_source_workflows=1,
        min_real_exact_64k_rows_by_domain=(("company", 1),),
        min_real_exact_64k_worlds_by_domain=(("company", 1),),
        min_motifs=1,
    ),
    "p7-wiki-source-slice-1-v1": _profile(
        profile_id="p7-wiki-source-slice-1-v1",
        environment="probe",
        expected_promoted_worlds=1,
        min_source_families=1,
        min_real_base_tasks=1,
        min_real_source_relations=0,
        min_real_64k_rows=1,
        min_train_worlds=1,
        min_eval_worlds=0,
        min_real_train_worlds=1,
        min_real_eval_worlds=0,
        predecessor_profile_id=None,
        min_domains=1,
        promoted_domain_world_quotas=(("researchlab", 1),),
        min_exact_64k_rows_by_domain=(("researchlab", 1),),
        min_unique_real_source_workflows=1,
        min_real_exact_64k_rows_by_domain=(("researchlab", 1),),
        min_real_exact_64k_worlds_by_domain=(("researchlab", 1),),
        min_motifs=1,
    ),
    "p7-github-source-slice-1-v1": _profile(
        profile_id="p7-github-source-slice-1-v1",
        environment="probe",
        expected_promoted_worlds=1,
        min_source_families=1,
        min_real_base_tasks=1,
        min_real_source_relations=0,
        min_real_64k_rows=1,
        min_train_worlds=1,
        min_eval_worlds=0,
        min_real_train_worlds=1,
        min_real_eval_worlds=0,
        predecessor_profile_id=None,
        min_domains=1,
        promoted_domain_world_quotas=(("codeforge", 1),),
        min_exact_64k_rows_by_domain=(("codeforge", 1),),
        min_unique_real_source_workflows=1,
        min_real_exact_64k_rows_by_domain=(("codeforge", 1),),
        min_real_exact_64k_worlds_by_domain=(("codeforge", 1),),
        min_motifs=1,
    ),
    "p4-multidomain-local-48-v1": _profile(
        profile_id="p4-multidomain-local-48-v1",
        environment="probe",
        expected_promoted_worlds=48,
        min_source_families=1,
        min_real_base_tasks=3,
        min_real_source_relations=3,
        min_real_64k_rows=6,
        min_train_worlds=40,
        min_eval_worlds=8,
        min_real_train_worlds=1,
        min_real_eval_worlds=0,
        predecessor_profile_id="p4-multidomain-probe-12-v1",
        min_domains=3,
        promoted_domain_world_quotas=(
            ("company", 16),
            ("researchlab", 16),
            ("codeforge", 16),
        ),
        min_exact_64k_rows_by_domain=(
            ("company", 1),
            ("researchlab", 1),
            ("codeforge", 1),
        ),
    ),
    "p3-production-48-v1": _profile(
        profile_id="p3-production-48-v1",
        environment="production",
        expected_promoted_worlds=48,
        min_source_families=6,
        min_real_base_tasks=12,
        min_real_source_relations=12,
        min_real_64k_rows=12,
        min_train_worlds=40,
        min_eval_worlds=8,
        min_real_train_worlds=4,
        min_real_eval_worlds=2,
        predecessor_profile_id="p3-probe-12-v1",
        min_domains=2,
    ),
    "p3-production-210-v1": _profile(
        profile_id="p3-production-210-v1",
        environment="production",
        expected_promoted_worlds=210,
        min_source_families=12,
        min_real_base_tasks=36,
        min_real_source_relations=36,
        min_real_64k_rows=24,
        min_train_worlds=178,
        min_eval_worlds=32,
        min_real_train_worlds=8,
        min_real_eval_worlds=4,
        predecessor_profile_id="p3-production-48-v1",
        min_domains=2,
    ),
}


def release_profile(profile_id: str) -> ReleaseProfile:
    try:
        return RELEASE_PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f"unknown release profile: {profile_id}") from error


def release_profile_sha256(profile_id: str) -> str:
    """Bind an ID to the exact immutable gate values active for a release."""
    profile = asdict(release_profile(profile_id))
    if profile["min_unique_real_source_workflows"] == 0:
        profile.pop("min_unique_real_source_workflows")
    if not profile["min_real_exact_64k_rows_by_domain"]:
        profile.pop("min_real_exact_64k_rows_by_domain")
    if not profile["min_real_exact_64k_worlds_by_domain"]:
        profile.pop("min_real_exact_64k_worlds_by_domain")
    payload = json.dumps(
        profile,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
