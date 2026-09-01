#!/usr/bin/env python3
"""Generate CausalCore / WorldLong JSONL from mixed executable worlds.

Length is a max cap. Packing never fills with weekly pulses or unique_prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.anchors import public_anchor_artifacts
from longworld.core.attestation import (
    ATTESTATION_ENV,
    attach_attestation,
    attestation_key_from_env,
    sanitized_attestation_environment,
)
from longworld.core.causal import build_causal_graph
from longworld.core.filingworkflow import (
    SEC_HYBRID_CHILD_EVENT_TYPES,
    selected_sec_source_relation_edges,
)
from longworld.core.graph import (
    graph_stats,
    random_walk_event_ids,
    typed_walk_event_ids,
)
from longworld.core.issuerfilingworkflow import (
    ISSUER_IR_HYBRID_CHILD_EVENT_TYPES,
    selected_issuer_ir_source_relation_edges,
)
from longworld.core.pack import (
    PackedContext,
    compute_view_metrics,
    dependency_class_for_view,
    dependency_evidence_ids,
    estimate_tokens,
    join_artifacts,
    local_span_too_short,
    pack_view,
    prompt_document_prefix,
    prompt_query_boundary,
    real_source_marginal_token_metrics,
    wrap_prompt,
)
from longworld.core.promotion import (
    EXACT_TOKEN_BAND_RANGES,
    exact_token_band_reject_reason,
    row_digest_set_sha256,
    serialized_row_sha256,
    stable_answer_program_id,
    stable_dossier_id,
    stable_semantic_base_task_id,
)
from longworld.core.realworkflow import (
    EPISODE_REPLAY_BUNDLE_SCHEMA,
    load_episode_replay_bundle,
    read_episode_replay_bundle_bytes,
)
from longworld.core.release_profile import release_profile, release_profile_sha256
from longworld.core.render import Artifact
from longworld.core.sampler import balanced_domain_schedule, materialize
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.sourcebundle import (
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
    load_source_workflow_bundle,
)
from longworld.core.sourcepack import source_pack_artifacts
from longworld.core.taxonomy import (
    CompositionMethod,
    EvidenceRole,
    SourceOrigin,
    TrainingObjective,
    WorkflowKind,
    artifact_classification,
    classify_artifact,
    validate_context_classification,
)
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)
from longworld.core.topology import (
    canonical_topology,
    effective_number,
    max_family_share,
    topology_family,
)
from longworld.core.verify import (
    Difficulty,
    ProofGraph,
    SampleRecord,
    Verification,
    counterfactual_shortcuts_insufficient,
    query_adjacent_window_artifact_ids,
    verify_packed_question,
    verify_question,
    verify_rendered_view,
)
from longworld.core.views import memory_card, render_cf_view, split_views, view_answer
from longworld.core.wikiparse import WIKI_HYBRID_CHILD_EVENT_TYPES
from longworld.core.world import SimulatedWorld
from longworld.domains.researchlab.simulate import (
    selected_wiki_source_relation_edges,
    valid_arxiv_revision_relation_event,
)

HYBRID_CHILD_EVENT_TYPES = (
    SEC_HYBRID_CHILD_EVENT_TYPES
    | WIKI_HYBRID_CHILD_EVENT_TYPES
    | ISSUER_IR_HYBRID_CHILD_EVENT_TYPES
)


def load_cfg(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def enforce_source_requirements(cfg: dict) -> None:
    """Stop train-ready promotion when its verified source pack is unavailable."""
    if (
        not cfg.get("require_verified_sources")
        or str(cfg.get("data_stage") or "train_ready") != "train_ready"
    ):
        return
    verified = source_pack_artifacts(
        "source-check",
        date(2026, 1, 1),
        n=1,
        allow_legacy=False,
    )
    if not verified:
        raise ValueError(
            "verified source pack required; fetch a provenance-v2 pack before P3"
        )


def stable_digest(value: str, *, size: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts)
    return int(stable_digest(payload, size=16), 16)


def workstreams_for_domain(cfg: dict, domain: str) -> int:
    """Resolve native workflow depth without forcing every domain to one size."""
    configured = cfg.get("domain_n_workstreams")
    if configured is None:
        return int(cfg.get("n_workstreams", 0))
    if not isinstance(configured, dict):
        raise TypeError("domain_n_workstreams must be a mapping")
    domains = {str(item) for item in cfg.get("domains") or ["company"]}
    if set(configured) != domains:
        raise ValueError("domain_n_workstreams keys must match configured domains")
    value = configured[domain]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("domain_n_workstreams values must be non-negative integers")
    return value


def _views_for_band(
    view_contexts: dict,
    *,
    base_task_id: str,
    timing: str,
    published_minimal_courses: set[tuple[str, str]],
) -> dict:
    """Publish one minimal curriculum view per base task and query timing."""
    minimal_key = (base_task_id, timing)
    return {
        name: value
        for name, value in view_contexts.items()
        if name != "minimal" or minimal_key not in published_minimal_courses
    }


def _strict_workflow_artifacts(
    artifacts: list[Artifact], *, allow_legacy_sources: bool
) -> list[Artifact]:
    """Exclude unverified source-pack text from strict executable dossiers."""
    if allow_legacy_sources:
        return artifacts
    return [
        artifact
        for artifact in artifacts
        if artifact.doc_type != "source_pack"
        or bool((artifact.slots or {}).get("provenance_verified"))
    ]


def holdout_assignment(
    strategy: str, key: str, *, train_ratio: float, seed: int
) -> tuple[str, str]:
    """Return a reproducible split and opaque holdout-group id."""

    group_id = stable_digest(f"{strategy}|{key}", size=16)
    draw = int(stable_digest(f"{seed}|{strategy}|{key}", size=16), 16) / float(16**16)
    return ("train" if draw < train_ratio else "eval", group_id)


def _source_families(artifacts: list) -> list[str]:
    families: set[str] = set()
    for artifact in artifacts:
        slots = artifact.slots or {}
        family = str(slots.get("source_family") or "").strip().lower()
        stem = str(slots.get("source_stem") or "").strip().lower()
        source_url = str(slots.get("source_url") or "").strip()
        if not family and source_url:
            parsed = urlparse(source_url)
            path_parts = [part for part in parsed.path.split("/") if part]
            if parsed.hostname and len(path_parts) >= 2:
                family = (
                    f"{parsed.hostname.lower()}/"
                    f"{'/'.join(path_parts[:2]).removesuffix('.git').lower()}"
                )
        if not family and stem:
            family = stem.split("-", 1)[0].rstrip("0123456789") or stem
        if family:
            families.add(family)
    return sorted(families)


def _split_for_spec(
    cfg: dict,
    default_split: str,
    *,
    seed: int,
    world_id: str,
    domain: str,
    spec,
    artifacts: list,
) -> tuple[str, dict[str, str]]:
    strategy = str(cfg.get("split_strategy") or "world")
    if strategy == "world":
        return default_split, {
            "strategy": strategy,
            "group_id": stable_digest(f"world|{world_id}", size=16),
        }
    if strategy == "topology":
        key = canonical_topology(spec)
    elif strategy == "source_family":
        key = "+".join(_source_families(artifacts)) or "no_source_family"
    elif strategy == "domain_composition":
        key = f"{domain}|{spec.motif or 'single'}"
    else:
        raise ValueError(f"unsupported split_strategy={strategy!r}")
    assigned, group_id = holdout_assignment(
        strategy,
        key,
        train_ratio=float(cfg.get("train_ratio", 0.8)),
        seed=int(cfg.get("split_seed", seed)),
    )
    return assigned, {"strategy": strategy, "group_id": group_id}


def _semantic_tokens(artifacts: list, *, workflow_id: str) -> dict[str, int]:
    """Count workflow-owned internal tokens; event-bearing is an inclusive subset."""
    event_bearing = 0
    internal = 0
    generic = 0
    for artifact in artifacts:
        tokens = estimate_tokens(artifact.text)
        classification = artifact_classification(artifact)
        same_workflow = classification.workflow_id == workflow_id
        if same_workflow:
            internal += tokens
            if artifact.reveals_events:
                event_bearing += tokens
        else:
            generic += tokens
    return {
        "event_bearing": event_bearing,
        "internal": internal,
        "generic_background": generic,
    }


def _real_source_tokens(artifacts: list[Artifact]) -> int:
    real_origins = {
        SourceOrigin.REAL_PUBLIC,
        SourceOrigin.REAL_PRIVATE_EXPORT,
        SourceOrigin.REAL_DERIVED,
    }
    return sum(
        estimate_tokens(artifact.text)
        for artifact in artifacts
        if artifact_classification(artifact).source_origin in real_origins
    )


def _classified_view_artifacts(
    artifacts: list,
    roles: dict[str, str],
    *,
    world_id: str,
) -> list:
    """Return per-view classifications without mutating shared artifacts."""
    classified = []
    for artifact in artifacts:
        clone = replace(artifact, slots=dict(artifact.slots or {}))
        current = artifact_classification(clone)
        if current.workflow_kind == WorkflowKind.UNCLASSIFIED:
            if not str(clone.artifact_id).startswith(world_id):
                classified.append(clone)
                continue
            current = type(current)(
                source_origin=SourceOrigin.SYNTHETIC_WORLD,
                workflow_kind=WorkflowKind.SYNTHETIC_EXECUTABLE,
                evidence_role=EvidenceRole.CAUSAL_SUPPORTING,
                workflow_id=world_id,
                provenance_id=(
                    "synthetic-sha256:"
                    + hashlib.sha256(clone.text.encode("utf-8")).hexdigest()
                ),
            )
        role_value = roles.get(clone.artifact_id, current.evidence_role.value)
        role = (
            EvidenceRole(role_value)
            if role_value in EvidenceRole._value2member_map_
            else EvidenceRole.UNCLASSIFIED
        )
        classify_artifact(
            clone,
            source_origin=current.source_origin,
            workflow_kind=current.workflow_kind,
            evidence_role=role,
            workflow_id=current.workflow_id,
            provenance_id=current.provenance_id,
        )
        classified.append(clone)
    return classified


def flatten_parallel(mat) -> list[Artifact]:
    arts: list[Artifact] = []
    for k, v in mat.artifacts.items():
        if k == "focal":
            continue
        arts.extend(v)
    return arts


def extra_filler(
    seed: int, n: int, n_parallel: int, n_pulses: int, domain: str, domains: list[str]
) -> list[Artifact]:
    filler: list[Artifact] = []
    pool = list(domains) or [domain]
    off = pool.index(domain) if domain in pool else 0
    for i in range(n):
        d = pool[(off + i + 1) % len(pool)]
        extra = materialize(
            seed * 1_000_003 + 17 + i,
            n_parallel=n_parallel,
            n_pulses=n_pulses,
            domain=d,
        )
        for arts in extra.artifacts.values():
            filler.extend(a for a in arts if a.doc_type != "source_pack")
    return filler


def _trainable(spec, *, production: bool = False) -> bool:
    if "decoy" in spec.query_id:
        return False
    if (
        production
        and getattr(spec, "domain", "") == "company"
        and spec.query_type == "version_diff"
    ):
        # Two-document version comparison is a short curriculum task, not a
        # strict long-context candidate; dense top-3 legitimately solves it.
        return False
    if production and spec.query_type in {"source_grounded", "source_choice"}:
        # These legacy labels are filename stems, not facts parsed from source text.
        return False
    return spec.query_type not in {"historical_state", "aggregation"}


def _buckets_for_split(
    cfg: dict, split: str, domain: str | None = None
) -> dict[str, int]:
    buckets = dict(cfg.get("length_buckets", {"8k": 8000}))
    if split != "train":
        extra = dict(cfg.get("eval_length_buckets") or {})
        buckets.update(extra)
    domain_buckets = cfg.get("domain_length_buckets")
    if domain is not None and isinstance(domain_buckets, dict):
        override = domain_buckets.get(domain)
        if override is not None:
            if not isinstance(override, dict):
                raise ValueError("domain length bucket overrides must be mappings")
            unknown = set(override) - set(buckets)
            if unknown:
                raise ValueError(f"unknown domain length buckets: {sorted(unknown)}")
            buckets.update({str(name): int(value) for name, value in override.items()})
    return buckets


def band_growth_reject_reason(
    previous_tokens: int,
    requested_tokens: int,
    packed_tokens: int,
    *,
    min_internal_growth: int = 0,
) -> str | None:
    """Reject a band that cannot be emitted before running expensive gates."""
    if previous_tokens >= 0 and (
        packed_tokens <= int(previous_tokens * 1.08)
        or packed_tokens - previous_tokens < min_internal_growth
    ):
        return "no_semantic_growth"
    if (
        previous_tokens >= 0
        and requested_tokens >= 24000
        and packed_tokens < int(0.50 * requested_tokens)
    ):
        return "long_cap_underfilled"
    return None


def exact_64k_reject_reason(tokens: int) -> str | None:
    return exact_token_band_reject_reason("64k", tokens)


def exact_token_metadata_for_band(
    prompt: str,
    length_bucket: str,
    *,
    model_id: str,
    revision: str,
    asset_manifest_sha256: str,
    tokenizer: object,
    cache: dict[str, int],
) -> tuple[dict[str, object], str | None]:
    """Recount a strict long view and bind it to one pinned tokenizer."""
    if length_bucket not in EXACT_TOKEN_BAND_RANGES:
        return {}, None
    context_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if context_digest not in cache:
        cache[context_digest] = tokenizer_token_count(prompt, tokenizer)
    tokens = cache[context_digest]
    return (
        {
            "tokenizer_context_tokens": tokens,
            "tokenizer_model_id": model_id,
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": asset_manifest_sha256,
        },
        exact_token_band_reject_reason(length_bucket, tokens),
    )


def _exact_metric_token_counter(
    exact_settings: dict[str, Any],
    length_bucket: str,
    counter: Callable[[str], int],
) -> Callable[[str], int] | None:
    if exact_settings and length_bucket in EXACT_TOKEN_BAND_RANGES:
        return counter
    return None


def exact_context_pack_target(
    question: str,
    timing: str,
    length_bucket: str,
    tokenizer: object,
) -> int:
    """Reserve prompt-wrapper tokens while packing an exact-token document view."""
    bounds = EXACT_TOKEN_BAND_RANGES.get(length_bucket)
    if bounds is None:
        raise ValueError("exact pack admission requires a strict token band")
    wrapper_tokens = tokenizer_token_count(wrap_prompt(question, "", timing), tokenizer)
    target = bounds[1] - wrapper_tokens
    if target < 1:
        raise ValueError("exact prompt wrapper exhausts the token band")
    return target


def retune_pack_target_for_exact_64k(
    current_target: int,
    observed_wraps: tuple[int, ...],
    *,
    lo: int = 64_000,
    hi: int = 65_536,
    bucket: int = 65_536,
) -> int:
    """Scale a pack cap so observed tokenizer wraps share the exact-64K window.

    The packer fills to the cap, so shrinking leftover text does not lower wrap
    when other documents still fill the remaining budget. Observed wraps at the
    current target are treated as proportional to that target.
    """
    if current_target <= 0:
        raise ValueError("pack target must be positive")
    if not observed_wraps:
        raise ValueError("observed wraps are required")
    if any(wrap <= 0 for wrap in observed_wraps):
        raise ValueError("observed wrap must be positive")
    if all(lo <= wrap <= hi for wrap in observed_wraps):
        return current_target
    lower = max(lo * current_target / wrap for wrap in observed_wraps)
    upper = min(hi * current_target / wrap for wrap in observed_wraps)
    if lower > upper:
        raise ValueError("observed wraps cannot share one pack target in exact-64K")
    target = (math.ceil(lower) + math.floor(upper)) // 2
    if target > bucket:
        raise ValueError("source body cannot fill exact-64K inside the bucket cap")
    if target < 1:
        raise ValueError("retuned pack target must be positive")
    return target


def strict_view_evidence_reject_reason(
    metrics: object, *, minimum_tokens: int
) -> str | None:
    if (
        minimum_tokens > 0
        and int(getattr(metrics, "evidence_count", 0)) >= 2
        and int(getattr(metrics, "max_evidence_distance", 0)) < minimum_tokens
    ):
        return (
            "view_distance_shortfall:"
            f"{int(getattr(metrics, 'max_evidence_distance', 0))}<{minimum_tokens}"
        )
    return None


def prepack_verification_green(verification: Verification) -> bool:
    """Check proof semantics before a concrete dossier layout exists."""
    return all(
        (
            verification.full_sufficient,
            verification.minimal_sufficient,
            verification.semantic_sufficient,
            verification.strict_executable_sufficient,
            verification.remove_one_fails,
            verification.counterfactual_changes_answer,
            verification.counterfactual_replay_sufficient,
            verification.closed_book_unsolved,
            verification.distractor_invariance_gold,
            verification.surface_match,
            verification.schema_ok,
            verification.no_shortcut,
            verification.min_complexity,
            verification.essential_single_doc_insufficient,
            verification.essential_surface_gold_free,
            verification.essential_text_grounded,
        )
    )


def composition_for_view(view_name: str) -> CompositionMethod:
    if view_name == "cf":
        return CompositionMethod.COUNTERFACTUAL_TWIN
    if view_name == "ordered_artifact_view":
        return CompositionMethod.CAUSAL_TIMELINE
    return CompositionMethod.SAME_CASE_DOSSIER


def distinct_strict_view_contexts(
    view_contexts: dict[str, tuple[str, list[Artifact]]],
) -> dict[str, tuple[str, list[Artifact]]]:
    """Do not publish an ordered view when it is byte-identical to full."""
    distinct = dict(view_contexts)
    full = distinct.get("full")
    ordered = distinct.get("ordered_artifact_view")
    if (
        full is not None
        and ordered is not None
        and (
            full[0] == ordered[0]
            and [artifact.artifact_id for artifact in full[1]]
            == [artifact.artifact_id for artifact in ordered[1]]
        )
    ):
        distinct.pop("ordered_artifact_view")
    return distinct


def counterfactual_text_reject_reason(
    view_contexts: dict[str, tuple[str, list[Artifact]]],
    *,
    factual_answer: str,
    counterfactual_answer: str,
    cf_event_id: str,
) -> str | None:
    """Require a changed target to be attributable to visible CF text."""
    if factual_answer == counterfactual_answer:
        return None
    factual = view_contexts.get("full")
    counterfactual = view_contexts.get("cf")
    if factual is None or counterfactual is None:
        return None
    if factual[0] == counterfactual[0]:
        return "counterfactual_text_unchanged"
    factual_targets = {
        artifact.artifact_id.split("#", 1)[0]: artifact.text
        for artifact in factual[1]
        if cf_event_id in artifact.reveals_events
    }
    if not factual_targets:
        return "counterfactual_event_not_visible"
    counterfactual_text = {
        artifact.artifact_id.split("#", 1)[0]: artifact.text
        for artifact in counterfactual[1]
        if cf_event_id in artifact.reveals_events
    }
    if not any(
        artifact_id in counterfactual_text and counterfactual_text[artifact_id] != text
        for artifact_id, text in factual_targets.items()
    ):
        return "counterfactual_event_text_unchanged"
    return None


def prune_short_ordered_view(
    view_contexts: dict[str, tuple[str, list[Artifact]]],
    spec: Any,
    *,
    query_timing: str,
    minimum_tokens: int,
    token_counter: Callable[[str], int] | None = None,
) -> tuple[dict[str, tuple[str, list[Artifact]]], str | None]:
    """Keep factual/CF twins while omitting a derived timeline that is local."""
    kept = dict(view_contexts)
    ordered = kept.get("ordered_artifact_view")
    if ordered is None:
        return kept, None
    context, artifacts = ordered
    metrics = compute_view_metrics(
        artifacts,
        dependency_evidence_ids(artifacts, spec),
        query_timing=query_timing,
        context=(
            wrap_prompt(str(spec.question), context, query_timing)
            if token_counter is not None
            else context
        ),
        token_counter=token_counter,
        token_prefix=(
            prompt_document_prefix(str(spec.question), query_timing)
            if token_counter is not None
            else ""
        ),
        query_boundary_tokens=(
            prompt_query_boundary(
                str(spec.question), context, query_timing, token_counter
            )
            if token_counter is not None
            else None
        ),
    )
    reason = strict_view_evidence_reject_reason(metrics, minimum_tokens=minimum_tokens)
    if reason:
        kept.pop("ordered_artifact_view")
    return kept, reason


def uses_legacy_exit_bars(data_stage: str) -> bool:
    return data_stage == "diagnostic"


def real_workflow_bundle_for_seed(cfg: dict, *, seed: int, domain: str) -> Path | None:
    configured = str(cfg.get("real_workflow_bundle") or "").strip()
    if not configured or domain != "codeforge":
        return None
    seeds = cfg.get("real_workflow_seeds")
    if not isinstance(seeds, list) or not all(isinstance(item, int) for item in seeds):
        raise ValueError("real_workflow_seeds must explicitly list integer seeds")
    if seed not in seeds:
        return None
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def episode_replay_binding(
    path: Path, *, _verified_raw: bytes | None = None
) -> dict[str, str]:
    raw = (
        _verified_raw
        if _verified_raw is not None
        else read_episode_replay_bundle_bytes(path)
    )
    return {
        "schema_version": EPISODE_REPLAY_BUNDLE_SCHEMA,
        "composition": "chronological_causal_union",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def source_workflow_bundle_for_seed(
    cfg: dict, *, seed: int, domain: str
) -> Path | None:
    configured = str(cfg.get("source_workflow_bundle") or "").strip()
    if not configured:
        return None
    seeds = cfg.get("source_workflow_seeds")
    if not isinstance(seeds, list) or not all(isinstance(item, int) for item in seeds):
        raise ValueError("source_workflow_seeds must explicitly list integer seeds")
    if seed not in seeds:
        return None
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def tokenizer_token_count(text: str, tokenizer) -> int:
    with sanitized_attestation_environment():
        return len(tokenizer.encode(text, add_special_tokens=False))


@lru_cache(maxsize=2)
def _load_exact_tokenizer(model_id: str, revision: str, local_files_only: bool):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=local_files_only,
        )


def filter_real_workflow_queries(queries: list, allowed_types: list[str]) -> list:
    if not allowed_types or not all(isinstance(item, str) for item in allowed_types):
        raise ValueError("real_workflow_query_types must list task-family names")
    allowed = set(allowed_types)
    return [query for query in queries if query.query_type in allowed]


def real_workflow_buckets_for_query(
    buckets: dict[str, int],
    query_type: str,
    routing: dict,
    preferred: list[str] | None = None,
) -> dict[str, int]:
    if not routing:
        return dict(buckets)
    allowed = routing.get(query_type)
    if (
        not isinstance(allowed, list)
        or not allowed
        or not all(isinstance(item, str) for item in allowed)
    ):
        raise ValueError(f"real workflow length routing is missing for {query_type}")
    selected = {name: target for name, target in buckets.items() if name in allowed}
    if preferred:
        selected = {
            name: target for name, target in selected.items() if name in preferred
        }
    return selected


def validate_declared_source_workflow_query_buckets(
    queries: list,
    *,
    source_query_types: list[str],
    routing: dict,
    active_buckets: list[str] | None = None,
) -> None:
    """Fail closed when a declared source-workflow band has no query."""

    if not source_query_types or not routing:
        return
    for query_type in source_query_types:
        declared = routing.get(query_type)
        if (
            not isinstance(declared, list)
            or not declared
            or not all(isinstance(item, str) for item in declared)
        ):
            raise ValueError(
                f"real workflow length routing is missing for {query_type}"
            )
        materialized = {
            bucket
            for query in queries
            if query.query_type == query_type
            for bucket in query.preferred_length_buckets
        }
        missing = [bucket for bucket in declared if bucket not in materialized]
        if missing:
            raise ValueError(
                "declared real workflow query bucket is not materialized for "
                f"{query_type}: {', '.join(missing)}"
            )
        if active_buckets is not None:
            active = set(active_buckets)
            inactive = [bucket for bucket in declared if bucket not in active]
            if inactive:
                raise ValueError(
                    "declared real workflow query routing for "
                    f"{query_type} has inactive buckets: {', '.join(inactive)}"
                )


def apply_source_workflow_bucket_targets(
    buckets: dict[str, int], cfg: dict
) -> dict[str, int]:
    """Apply explicit caps for authentic bodies whose token density differs."""
    raw = cfg.get("source_workflow_bucket_targets") or {}
    if not isinstance(raw, dict):
        raise TypeError("source_workflow_bucket_targets must be a mapping")
    result = dict(buckets)
    for name, target in raw.items():
        if (
            name not in result
            or isinstance(target, bool)
            or not isinstance(target, int)
        ):
            raise ValueError("source workflow bucket target is invalid")
        if target <= 0 or target > result[name]:
            raise ValueError("source workflow bucket target must tighten its bucket")
        result[name] = target
    return result


def real_workflow_artifacts_for_query(
    artifacts: list[Artifact], spec: object
) -> list[Artifact]:
    """Restrict a real task dossier to repositories containing its proof."""
    essential_ids = set(getattr(spec, "essential_artifact_ids", []))
    source_urls = {
        str((artifact.slots or {}).get("source_url") or "")
        for artifact in artifacts
        if artifact.artifact_id in essential_ids
        and (artifact.slots or {}).get("real_workflow_record") is True
    }
    source_urls.discard("")
    if not source_urls:
        raise ValueError("real workflow proof has no source repository binding")
    bound_workflow_ids = (
        {
            str((artifact.slots or {}).get("source_workflow_id") or "")
            for artifact in artifacts
            if (
                artifact.artifact_id in essential_ids
                or set(getattr(spec, "sufficient_event_ids", [])).intersection(
                    artifact.reveals_events
                )
            )
            and (artifact.slots or {}).get("real_workflow_record") is True
        }
        if getattr(spec, "query_type", "") == "ci_regression_origin"
        else set()
    )
    bound_workflow_ids.discard("")
    selected = [
        artifact
        for artifact in artifacts
        if (
            (artifact.slots or {}).get("real_workflow_record") is True
            and str((artifact.slots or {}).get("source_url") or "") in source_urls
            and (
                not bound_workflow_ids
                or str((artifact.slots or {}).get("source_workflow_id") or "")
                in bound_workflow_ids
            )
        )
        or (
            artifact.artifact_id in essential_ids
            and (artifact.slots or {}).get("real_workflow_record") is not True
        )
    ]
    sufficient_event_ids = set(getattr(spec, "sufficient_event_ids", []))
    protected_ids = essential_ids | {
        artifact.artifact_id
        for artifact in selected
        if sufficient_event_ids.intersection(artifact.reveals_events)
    }
    by_body: dict[str, Artifact] = {}
    for artifact in selected:
        params = dict((artifact.slots or {}).get("params") or {})
        body_sha256 = str(params.get("body_sha256") or "")
        body_identity = (
            body_sha256 or hashlib.sha256(artifact.text.encode()).hexdigest()
        )
        current = by_body.get(body_identity)
        if current is None or (
            artifact.artifact_id in protected_ids
            and current.artifact_id not in protected_ids
        ):
            by_body[body_identity] = artifact
        elif (
            current is not None
            and artifact.artifact_id in protected_ids
            and current.artifact_id in protected_ids
        ):
            raise ValueError(
                "strict real workflow proof contains duplicate source bodies"
            )
    selected = sorted(by_body.values(), key=lambda item: (item.time, item.artifact_id))
    selected_ids = {artifact.artifact_id for artifact in selected}
    if not essential_ids.issubset(selected_ids):
        raise ValueError("real workflow proof crosses an unbound repository episode")
    return selected


def source_workflow_artifacts_for_query(
    artifacts: list[Artifact], spec: object
) -> list[Artifact]:
    """Restrict source-grounded workflow artifacts to the query checkpoint.

    Unbound RFC leftover files that ``bind_source_packs`` attaches to every
    focal world must not consume authentic wiki/SEC pack budget. Length is a
    cap, so those files make leftover retune a no-op and discrete-skip the
    exact-64K window.
    """
    as_of = getattr(spec, "as_of", None)
    selected = [
        artifact
        for artifact in artifacts
        if (as_of is None or artifact.time <= as_of)
        and artifact.doc_type != "source_pack"
    ]
    essential_ids = set(getattr(spec, "essential_artifact_ids", []))
    source_workflow_ids = {
        str((artifact.slots or {}).get("source_workflow_id") or "")
        for artifact in selected
        if artifact.artifact_id in essential_ids
    }
    source_workflow_ids.discard("")
    if source_workflow_ids:
        if len(source_workflow_ids) != 1:
            raise ValueError("source-grounded proof must bind one source workflow")
        source_workflow_id = next(iter(source_workflow_ids))
        selected = [
            artifact
            for artifact in selected
            if str((artifact.slots or {}).get("source_workflow_id") or "")
            == source_workflow_id
            or artifact_classification(artifact).evidence_role
            == EvidenceRole.STRUCTURAL_HARD_NEGATIVE
        ]
    if getattr(spec, "query_type", "") == "sec_financial_reconstruction":
        if len(source_workflow_ids) != 1:
            raise ValueError("SEC financial proof must bind one source workflow")
        source_workflow_id = next(iter(source_workflow_ids))
        sec_event_types = {
            "sec_filing",
            "sec_source_section",
            "sec_prior_annual_filing_relation",
            "sec_financial_answer",
        }
        selected = [
            artifact
            for artifact in selected
            if (
                str((artifact.slots or {}).get("source_workflow_id") or "")
                == source_workflow_id
                and str((artifact.slots or {}).get("event_type") or artifact.doc_type)
                in sec_event_types
            )
            or artifact_classification(artifact).evidence_role
            == EvidenceRole.STRUCTURAL_HARD_NEGATIVE
        ]
    selected_ids = {artifact.artifact_id for artifact in selected}
    if not essential_ids.issubset(selected_ids):
        raise ValueError("source workflow proof references a future artifact")
    return selected


def real_workflow_corridor_ids(artifacts: list[Artifact], spec: object) -> set[str]:
    """Return authentic records between the real CI failure and recovery proof."""
    if getattr(spec, "query_type", "") != "ci_regression_origin":
        return set()
    essential_ids = set(getattr(spec, "essential_artifact_ids", []))
    sufficient_event_ids = set(getattr(spec, "sufficient_event_ids", []))
    proof_records = [
        artifact
        for artifact in artifacts
        if (
            artifact.artifact_id in essential_ids
            or sufficient_event_ids.intersection(artifact.reveals_events)
        )
        and (artifact.slots or {}).get("real_workflow_record") is True
    ]
    source_urls = {
        str((artifact.slots or {}).get("source_url") or "")
        for artifact in proof_records
    }
    workflow_ids = {
        str((artifact.slots or {}).get("source_workflow_id") or "")
        for artifact in proof_records
    }
    source_urls.discard("")
    workflow_ids.discard("")
    if len(source_urls) != 1 or not workflow_ids or not proof_records:
        raise ValueError("real CI proof does not bind one authentic source repository")
    source_url = next(iter(source_urls))
    if not source_url:
        raise ValueError("real CI source corridor binding is incomplete")

    def source_order(artifact: Artifact) -> int:
        params = dict((artifact.slots or {}).get("params") or {})
        return int(params.get("source_order") or 0)

    lower = min(source_order(artifact) for artifact in proof_records)
    upper = max(source_order(artifact) for artifact in proof_records)
    corridor = {
        artifact.artifact_id
        for artifact in artifacts
        if (artifact.slots or {}).get("real_workflow_record") is True
        and str((artifact.slots or {}).get("source_url") or "") == source_url
        and str((artifact.slots or {}).get("source_workflow_id") or "") in workflow_ids
        and lower <= source_order(artifact) <= upper
    }
    if not essential_ids.issubset(corridor):
        raise ValueError("real CI source corridor omits an essential record")
    return corridor


def real_source_relation_edges(
    world: SimulatedWorld, spec: object, artifacts: list[Artifact] | None = None
) -> list[dict[str, str]]:
    """Return explicit record-to-record edges used by a real answer program."""
    event_ids = set(getattr(spec, "sufficient_event_ids", []))
    if artifacts is not None:
        event_ids &= {
            event_id for artifact in artifacts for event_id in artifact.reveals_events
        }
    all_events = {
        event.id: event
        for event in getattr(world, "events", [])
        if event.id in event_ids
    }
    events = {
        event_id: event
        for event_id, event in all_events.items()
        if event.type == "repo_record" and event.params.get("source_url")
    }
    real_origins = {
        SourceOrigin.REAL_PUBLIC,
        SourceOrigin.REAL_PRIVATE_EXPORT,
        SourceOrigin.REAL_DERIVED,
    }
    endpoint_is_real: dict[str, bool] = {}
    if artifacts is not None:
        for event_id in events:
            visible = [
                artifact
                for artifact in artifacts
                if event_id in artifact.reveals_events
            ]
            endpoint_is_real[event_id] = bool(visible) and all(
                artifact_classification(artifact).source_origin in real_origins
                for artifact in visible
            )
    edges: list[dict[str, str]] = []
    for child in events.values():
        synthetic_inputs = set(child.params.get("synthetic_relation_inputs") or [])
        for parent_id in child.causal_inputs:
            parent = events.get(parent_id)
            if parent is None:
                continue
            edges.append(
                {
                    "parent_record_id": str(parent.params.get("record_id") or ""),
                    "child_record_id": str(child.params.get("record_id") or ""),
                    "relation": str(
                        child.relation_kinds.get(parent_id) or "causal_input"
                    ),
                    "relation_provenance": (
                        "synthetic_executable"
                        if parent_id in synthetic_inputs
                        or endpoint_is_real.get(parent_id, True) is not True
                        or endpoint_is_real.get(child.id, True) is not True
                        else "authentic_source"
                    ),
                    "parent_source_url": str(parent.params["source_url"]),
                    "child_source_url": str(child.params["source_url"]),
                }
            )
    for relation in all_events.values():
        if not valid_arxiv_revision_relation_event(relation, all_events):
            continue
        edges.append(
            {
                "parent_record_id": str(relation.params.get("target_record_id") or ""),
                "child_record_id": str(relation.params.get("source_record_id") or ""),
                "relation": str(relation.params.get("relation_kind") or ""),
                "relation_provenance": "authentic_source",
                "parent_source_url": str(
                    relation.params.get("target_source_url") or ""
                ),
                "child_source_url": str(relation.params.get("source_url") or ""),
            }
        )
    edges.extend(selected_sec_source_relation_edges(world, spec, artifacts or []))
    edges.extend(selected_wiki_source_relation_edges(world, spec, artifacts or []))
    edges.extend(selected_issuer_ir_source_relation_edges(world, spec, artifacts or []))
    for child in all_events.values():
        if child.type not in HYBRID_CHILD_EVENT_TYPES:
            continue
        for parent_id in child.causal_inputs:
            parent = all_events.get(parent_id)
            if parent is None:
                continue
            edges.append(
                {
                    "parent_record_id": parent.id,
                    "child_record_id": child.id,
                    "source_record_id": str(child.params.get("record_id") or ""),
                    "relation": str(
                        child.relation_kinds.get(parent_id) or "causal_input"
                    ),
                    "relation_provenance": "synthetic_executable",
                    "parent_source_url": str(parent.params.get("source_url") or ""),
                    "child_source_url": str(child.params.get("source_url") or ""),
                }
            )
    return edges


def context_source_relation_count(
    world: SimulatedWorld, artifacts: list[Artifact], *, spec: object | None = None
) -> int:
    """Count replayable real-event edges whose endpoints occur in this exact view."""
    visible_event_ids = {
        event_id for artifact in artifacts for event_id in artifact.reveals_events
    }
    if spec is not None:
        visible_event_ids &= set(getattr(spec, "sufficient_event_ids", []))
    events = {
        event.id: event
        for event in getattr(world, "events", [])
        if event.id in visible_event_ids
    }
    repo_edges = sum(
        parent_id in events
        for event in events.values()
        if event.type == "repo_record"
        for parent_id in event.causal_inputs
    )
    source_relations = sum(
        event.type == "arxiv_revision_relation"
        and all(parent_id in events for parent_id in event.required_inputs)
        for event in events.values()
    )
    sec_source_relations = len(
        selected_sec_source_relation_edges(world, spec, artifacts)
    )
    wiki_source_relations = len(
        selected_wiki_source_relation_edges(world, spec, artifacts)
    )
    issuer_source_relations = len(
        selected_issuer_ir_source_relation_edges(world, spec, artifacts)
    )
    sec_edges = sum(
        parent_id in events
        for event in events.values()
        if event.type in HYBRID_CHILD_EVENT_TYPES
        for parent_id in event.causal_inputs
    )
    return (
        repo_edges
        + source_relations
        + sec_source_relations
        + wiki_source_relations
        + issuer_source_relations
        + sec_edges
    )


def emit_records(
    seed: int, cfg: dict, split: str, domain_override: str | None = None
) -> tuple[list[dict], list[dict], dict]:
    n_parallel = int(cfg.get("n_parallel", 2))
    n_pulses = int(cfg.get("n_pulses", 0))
    domains = list(cfg.get("domains") or ["company"])
    domain = domain_override or domains[seed % len(domains)]
    if domain not in domains:
        raise ValueError(f"domain override {domain!r} is not configured")
    n_workstreams = workstreams_for_domain(cfg, domain)
    strict_generation = bool(cfg.get("strict_semantic_verification"))
    data_stage = str(
        cfg.get("data_stage") or ("train_ready" if strict_generation else "diagnostic")
    )
    if data_stage not in {"candidate", "train_ready", "diagnostic"}:
        raise ValueError(f"unsupported data_stage={data_stage!r}")
    train_ready = data_stage == "train_ready"
    attestation_key = (
        attestation_key_from_env("candidate_row") if strict_generation else None
    )
    if strict_generation and attestation_key is None:
        raise ValueError(f"strict generation requires a 32-byte {ATTESTATION_ENV}")
    bundle_path = real_workflow_bundle_for_seed(cfg, seed=seed, domain=domain)
    if bundle_path is not None:
        bundle_raw = read_episode_replay_bundle_bytes(bundle_path)
        replay_binding = episode_replay_binding(bundle_path, _verified_raw=bundle_raw)
        real_workflows = load_episode_replay_bundle(
            bundle_path, expected_sha256=replay_binding["sha256"]
        )
    else:
        replay_binding = None
        real_workflows = None
    source_bundle_path = source_workflow_bundle_for_seed(cfg, seed=seed, domain=domain)
    if source_bundle_path is not None:
        loaded_source_bundle = load_source_workflow_bundle(source_bundle_path)
        source_workflows = [
            workflow
            for workflow in loaded_source_bundle.workflows
            if workflow.target_domain == domain
        ]
        if not source_workflows:
            raise ValueError("source workflow bundle has no workflow for domain")
        source_replay_binding = {
            "schema_version": SOURCE_WORKFLOW_BUNDLE_SCHEMA,
            "adapter_revision": loaded_source_bundle.adapter_revision,
            "sha256": loaded_source_bundle.bundle_sha256,
            "binding_digest": loaded_source_bundle.binding_digest,
        }
    else:
        source_workflows = None
        source_replay_binding = None
    if real_workflows is not None and source_workflows is not None:
        raise ValueError("one candidate world cannot bind two replay bundle types")
    has_real_workflows = real_workflows is not None or source_workflows is not None
    mat = materialize(
        seed,
        n_parallel=n_parallel,
        n_pulses=n_pulses,
        domain=domain,
        n_workstreams=n_workstreams,
        real_workflows=real_workflows,
        source_workflows=source_workflows,
        include_program_joins=bool(cfg.get("include_program_joins", True)),
    )
    if real_workflows is not None:
        mat.queries = filter_real_workflow_queries(
            mat.queries, list(cfg.get("real_workflow_query_types") or [])
        )
    elif source_workflows is not None:
        source_query_types = list(cfg.get("source_workflow_query_types") or [])
        mat.queries = filter_real_workflow_queries(mat.queries, source_query_types)
        configured_buckets = set(_buckets_for_split(cfg, split, domain))
        configured_real_buckets = cfg.get("real_workflow_length_buckets")
        active_buckets = (
            [
                bucket
                for bucket in configured_real_buckets
                if isinstance(bucket, str) and bucket in configured_buckets
            ]
            if isinstance(configured_real_buckets, list)
            else []
        )
        validate_declared_source_workflow_query_buckets(
            mat.queries,
            source_query_types=source_query_types,
            routing=dict(cfg.get("real_workflow_query_length_buckets") or {}),
            active_buckets=active_buckets,
        )
    exact_settings = dict(cfg.get("exact_tokenizer") or {})
    exact_model_id = str(exact_settings.get("model_id") or "")
    exact_revision = str(exact_settings.get("revision") or "")
    if exact_settings and (
        not exact_model_id
        or len(exact_revision) != 40
        or any(character not in "0123456789abcdef" for character in exact_revision)
    ):
        raise ValueError("exact_tokenizer requires a model id and 40-char revision")
    exact_tokenizer = None
    exact_asset_manifest_sha256 = ""
    if exact_settings:
        exact_asset_manifest_sha256 = resolved_tokenizer_asset_manifest_sha256(
            exact_model_id, exact_revision
        )
    if strict_generation and exact_settings:
        exact_tokenizer = _load_exact_tokenizer(
            exact_model_id,
            exact_revision,
            bool(exact_settings.get("local_files_only", True)),
        )
        if (
            resolved_tokenizer_asset_manifest_sha256(exact_model_id, exact_revision)
            != exact_asset_manifest_sha256
        ):
            raise ValueError("exact tokenizer assets changed while loading")
    exact_token_cache: dict[str, int] = {}

    def count_packed_tokens(text: str) -> int:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = exact_token_cache.get(digest)
        if cached is None:
            if exact_tokenizer is None:
                raise ValueError("exact pack tokenizer is unavailable")
            cached = tokenizer_token_count(text, exact_tokenizer)
            exact_token_cache[digest] = cached
        return cached

    stats: dict[str, Any] = {
        "attempts": 0,
        "kept_slots": 0,
        "n_clones": 0,
        "domain": domain,
    }
    rejects: list[dict] = []
    if not mat.scan_ok:
        return (
            [],
            [
                {
                    "seed": seed,
                    "world_id": mat.spec["world_id"],
                    "reason": "consistency_scan",
                    "issues": [i.detail for i in mat.scan_issues[:8]],
                }
            ],
            stats,
        )

    stats["world_id"] = mat.spec["world_id"]
    focal_w = mat.worlds["focal"]
    focal_arts = mat.artifacts["focal"]
    if strict_generation:
        focal_arts = _strict_workflow_artifacts(
            focal_arts,
            allow_legacy_sources=bool(cfg.get("allow_legacy_sources", False)),
        )
    parallel_arts = flatten_parallel(mat)
    if strict_generation:
        # Unlinked worlds are not a valid way to create long context.
        parallel_arts = []
    if cfg.get("require_verified_sources"):
        focal_arts = [
            artifact
            for artifact in focal_arts
            if artifact.doc_type != "source_pack"
            or bool((artifact.slots or {}).get("provenance_verified"))
        ]
        parallel_arts = [
            artifact
            for artifact in parallel_arts
            if artifact.doc_type != "source_pack"
            or bool((artifact.slots or {}).get("provenance_verified"))
        ]
    filler = (
        []
        if strict_generation
        else extra_filler(
            seed,
            int(cfg.get("extra_filler_worlds", 8)),
            n_parallel=max(1, n_parallel),
            n_pulses=n_pulses,
            domain=domain,
            domains=domains,
        )
    )
    from datetime import date as _date

    start = _date.fromisoformat(str(mat.spec.get("start") or "2026-01-08"))
    anchors = public_anchor_artifacts(
        mat.spec["world_id"], start, n=int(cfg.get("n_anchors", 12))
    )
    have_stems = {
        str((a.slots or {}).get("source_stem") or "")
        for a in focal_arts
        if a.doc_type == "source_pack"
    }
    sources = [
        s
        for s in source_pack_artifacts(
            mat.spec["world_id"],
            start,
            n=int(cfg.get("n_source_pack", 24)),
            deny_substrings=[
                str(s.answer)
                for s in mat.queries
                if s.answer
                and s.answer not in {"unknown", ""}
                and len(str(s.answer)) >= 6
                and s.query_type not in {"source_grounded", "source_choice"}
            ],
            allow_legacy=bool(cfg.get("allow_legacy_sources", True)),
        )
        if str((s.slots or {}).get("source_stem") or "") not in have_stems
    ]
    if strict_generation:
        # Unbound public documents are not causal workflow history.
        sources = []
    unique_pool = filler + parallel_arts + anchors + sources
    from longworld.core.artifact_dag import predecessor_ids

    bank_ids = {
        i
        for spec in mat.queries
        if _trainable(spec, production=strict_generation)
        for i in spec.essential_artifact_ids
    }
    for spec in mat.queries:
        if _trainable(spec, production=strict_generation):
            bank_ids |= predecessor_ids(
                focal_w, focal_arts, spec.essential_artifact_ids
            )
    bank_ids |= {
        a.artifact_id for a in focal_arts if str(a.artifact_id).endswith("latent_decoy")
    }
    graph = build_causal_graph(focal_w)

    kept: list[dict] = []
    timings = list(cfg.get("query_timings", ["first", "late"]))
    positions = list(cfg.get("position_buckets", ["front", "middle", "back"]))
    buckets = _buckets_for_split(cfg, split, domain)
    if has_real_workflows:
        allowed_buckets = set(cfg.get("real_workflow_length_buckets") or [])
        if not allowed_buckets:
            raise ValueError("real_workflow_length_buckets must be explicit")
        buckets = {
            name: target for name, target in buckets.items() if name in allowed_buckets
        }
        if not buckets:
            raise ValueError("real workflow length selection is empty")
    if source_workflows is not None:
        buckets = apply_source_workflow_bucket_targets(buckets, cfg)
    frac_map = dict(cfg.get("min_evidence_frac") or {})
    distance_config = (
        cfg.get("release_min_evidence_tokens")
        if train_ready or strict_generation
        else cfg.get("min_evidence_tokens") or {}
    )
    tok_map = dict(distance_config or {})
    surface_min = float(cfg.get("surface_min", 0.82))
    verification_mode = (
        "production" if train_ready else "candidate" if strict_generation else "legacy"
    )
    keep_one_pos = bool(cfg.get("keep_one_position", True))
    seen_conversations: set[str] = set()
    published_minimal_courses: set[tuple[str, str]] = set()

    for spec in mat.queries:
        if not _trainable(spec, production=strict_generation):
            continue
        _, cf_arts = render_cf_view(focal_w, spec)
        if strict_generation:
            cf_arts = _strict_workflow_artifacts(
                cf_arts,
                allow_legacy_sources=bool(cfg.get("allow_legacy_sources", False)),
            )
        if cfg.get("require_verified_sources"):
            cf_arts = [
                artifact
                for artifact in cf_arts
                if artifact.doc_type != "source_pack"
                or bool((artifact.slots or {}).get("provenance_verified"))
            ]
        query_focal_arts = focal_arts
        query_parallel_arts = parallel_arts
        query_unique_pool = unique_pool
        if real_workflows is not None:
            query_focal_arts = real_workflow_artifacts_for_query(focal_arts, spec)
            cf_arts = real_workflow_artifacts_for_query(cf_arts, spec)
            query_parallel_arts = []
            query_unique_pool = []
        elif source_workflows is not None:
            query_focal_arts = source_workflow_artifacts_for_query(focal_arts, spec)
            cf_arts = source_workflow_artifacts_for_query(cf_arts, spec)
            query_parallel_arts = [
                artifact
                for artifact in parallel_arts
                if spec.as_of is None or artifact.time <= spec.as_of
            ]
            query_unique_pool = []
        corridor_ids = real_workflow_corridor_ids(query_focal_arts, spec)
        views = split_views(query_focal_arts, query_parallel_arts, spec, cf_arts)
        ver, notes = verify_question(
            focal_w,
            spec,
            query_focal_arts + query_parallel_arts,
            cf_artifacts=cf_arts + query_parallel_arts,
            surface_min=surface_min,
            verification_mode=verification_mode,
            require_raw_token_windows=False,
        )
        stats["attempts"] += 1
        if not prepack_verification_green(ver):
            rejects.append(
                {
                    "seed": seed,
                    "world_id": mat.spec["world_id"],
                    "query_id": spec.query_id,
                    "reason": "prepack_gate",
                    "notes": {
                        k: notes.get(k)
                        for k in (
                            "full_ans",
                            "min_ans",
                            "cf_ans",
                            "closed_ans",
                            "surface_ratio",
                            "shortcut",
                            "visibility_gap",
                        )
                    },
                    "gates": ver.model_dump(),
                }
            )
            continue

        walk_ids = random_walk_event_ids(
            graph,
            starts=list(spec.essential_event_ids),
            n_walks=12,
            walk_len=8,
            forbid=set(spec.essential_event_ids),
            rng=random.Random(stable_seed(seed, spec.query_id, "random_walk")),
        )
        typed = typed_walk_event_ids(
            graph,
            starts=list(spec.essential_event_ids),
            allowed_kinds={
                "causes",
                "enables",
                "derived_from",
                "supersedes",
                "contradicts",
            },
            n_walks=8,
            walk_len=6,
            forbid=set(spec.essential_event_ids),
            rng=random.Random(stable_seed(seed, spec.query_id, "typed_walk")),
        )
        walk_ids = list(dict.fromkeys(walk_ids + typed))
        gstat = graph_stats(focal_w, spec)
        rng = random.Random(stable_seed(seed, spec.query_id, "pack"))
        min_semantic = int(cfg.get("min_semantic_tokens", 800))
        query_buckets = buckets
        if has_real_workflows:
            query_buckets = real_workflow_buckets_for_query(
                buckets,
                spec.query_type,
                dict(cfg.get("real_workflow_query_length_buckets") or {}),
                list(spec.preferred_length_buckets),
            )
            if not query_buckets:
                continue
        for timing in timings:
            prev_tokens = -1
            for bname, target in sorted(
                query_buckets.items(), key=lambda kv: int(kv[1])
            ):
                exact_pack_admission = bool(cfg.get("exact_pack_admission", False))
                if exact_pack_admission:
                    if exact_tokenizer is None:
                        raise ValueError(
                            "exact_pack_admission requires the pinned exact tokenizer"
                        )
                    pack_target = exact_context_pack_target(
                        spec.question, timing, bname, exact_tokenizer
                    )
                    pack_token_counter = count_packed_tokens
                else:
                    pack_target = int(target)
                    pack_token_counter = None
                min_frac = float(frac_map.get(bname, 0.0))
                min_dist_tok = int(tok_map.get(bname, 0))
                candidates: list[tuple[int, PackedContext, Verification]] = []
                for pos in positions:
                    stats["attempts"] += 1
                    prefer = set(bank_ids)
                    if spec.query_type in {
                        "revisitation",
                        "ratification",
                        "docket_control",
                    }:
                        prefer = set(spec.essential_artifact_ids)
                        prefer |= {
                            a.artifact_id
                            for a in focal_arts
                            if str(a.artifact_id).endswith("latent_decoy")
                        }
                    elif spec.query_type == "source_choice":
                        prefer = set(spec.essential_artifact_ids)
                    prefer |= {
                        artifact.artifact_id
                        for artifact in query_focal_arts
                        if "wiki_section_appendix_rest" in artifact.artifact_id
                        and artifact.artifact_id not in spec.essential_artifact_ids
                    }
                    packed = pack_view(
                        views["full"],
                        spec,
                        query_unique_pool,
                        query_timing=timing,
                        position_bucket=pos,
                        length_bucket=bname,
                        target_tokens=pack_target,
                        rng=rng,
                        min_distance_frac=min_frac,
                        min_distance_tokens=min_dist_tok,
                        walk_ids=walk_ids,
                        min_semantic_tokens=min_semantic,
                        prefer_ids=prefer,
                        ordered_corridor_ids=corridor_ids,
                        token_counter=pack_token_counter,
                    )
                    if packed.n_clones:
                        stats["n_clones"] += packed.n_clones
                    if not packed.ok:
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": packed.reject_reason or "pack_fail",
                                "timing": timing,
                                "position": pos,
                                "bucket": bname,
                            }
                        )
                        continue
                    span_why = None
                    if min_dist_tok > 0:
                        span_why = local_span_too_short(
                            packed.artifacts,
                            dependency_evidence_ids(packed.artifacts, spec),
                            target_tokens=pack_target,
                            token_counter=(
                                count_packed_tokens
                                if exact_settings and bname in EXACT_TOKEN_BAND_RANGES
                                else None
                            ),
                        )
                    if span_why:
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": span_why,
                                "timing": timing,
                                "position": pos,
                                "bucket": bname,
                                "evidence_distance": packed.max_evidence_distance,
                            }
                        )
                        continue
                    if any("#pad" in a.artifact_id for a in packed.artifacts):
                        stats["n_clones"] += 1
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": "clone_forbidden",
                                "bucket": bname,
                            }
                        )
                        continue
                    growth_reason = band_growth_reject_reason(
                        prev_tokens,
                        pack_target,
                        packed.tokens,
                        min_internal_growth=int(cfg.get("min_internal_growth", 0)),
                    )
                    if growth_reason:
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": growth_reason,
                                "timing": timing,
                                "position": pos,
                                "bucket": bname,
                                "actual_tokens": packed.tokens,
                                "previous_tokens": prev_tokens,
                            }
                        )
                        continue
                    ver2, notes2 = verify_packed_question(
                        focal_w,
                        spec,
                        packed.artifacts,
                        base_verification=ver,
                        base_notes=notes,
                        cf_artifacts=cf_arts,
                        window_ids=packed.window_ids,
                        verification_mode=verification_mode,
                        raw_window_tokenizer=exact_tokenizer,
                    )
                    if not ver2.all_green():
                        rejects.append(
                            {
                                "seed": seed,
                                "query_id": spec.query_id,
                                "reason": "pack_gate",
                                "timing": timing,
                                "position": pos,
                                "bucket": bname,
                                "window_ans": notes2.get("window_ans"),
                                "bm25_top1_id": notes2.get("bm25_top1_id"),
                                "contiguous_windows": notes2.get("contiguous_windows"),
                                "raw_token_fact_windows": notes2.get(
                                    "raw_token_fact_windows"
                                ),
                                "embedding_topk": notes2.get("embedding_topk"),
                                "gates": ver2.model_dump(),
                            }
                        )
                        continue
                    candidates.append((packed.max_evidence_distance, packed, ver2))
                if not candidates:
                    continue
                if keep_one_pos:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    candidates = candidates[:1]
                band_emitted = False
                for dist, packed, ver2 in candidates:
                    proof = ProofGraph(
                        necessary_nodes=list(spec.essential_event_ids),
                        sufficient_set=list(spec.sufficient_event_ids),
                        answer_expression=spec.gold_expression,
                        cf_event_id=spec.cf_event_id,
                        cf_op=spec.cf_op,
                    )
                    vis_gap = int(notes.get("visibility_gap") or 0)
                    ess = set(spec.essential_artifact_ids)
                    cf_map = {a.artifact_id: a for a in views["cf"]}
                    packed_cf = [
                        cf_map.get(a.artifact_id.split("#")[0], a)
                        for a in packed.artifacts
                    ]
                    distractor_artifacts = [
                        a for a in packed.artifacts if a.artifact_id not in ess
                    ]
                    ordered_artifacts = sorted(
                        packed.artifacts,
                        key=lambda a: (a.time, a.artifact_id),
                    )
                    view_contexts = {
                        "full": (packed.text, packed.artifacts),
                        "cf": (join_artifacts(packed_cf), packed_cf),
                        "minimal": (
                            join_artifacts(views["minimal"]),
                            views["minimal"],
                        ),
                        "distractor_only": (
                            join_artifacts(distractor_artifacts),
                            distractor_artifacts,
                        ),
                        "ordered_artifact_view": (
                            join_artifacts(ordered_artifacts),
                            ordered_artifacts,
                        ),
                        "memory": (memory_card(focal_w, spec), []),
                    }
                    if strict_generation:
                        raw_allowed_views = (
                            cfg.get("real_workflow_views")
                            if has_real_workflows
                            else ["full", "cf", "ordered_artifact_view"]
                        )
                        if not isinstance(raw_allowed_views, list):
                            raise ValueError("strict view selection must be a list")
                        allowed_views = {str(view) for view in raw_allowed_views}
                        if not allowed_views:
                            raise ValueError("strict view selection is empty")
                        view_contexts = {
                            name: value
                            for name, value in view_contexts.items()
                            if name in allowed_views
                        }
                        cf_text_reason = counterfactual_text_reject_reason(
                            view_contexts,
                            factual_answer=str(spec.answer),
                            counterfactual_answer=str(spec.cf_answer),
                            cf_event_id=str(spec.cf_event_id),
                        )
                        if cf_text_reason:
                            rejects.append(
                                {
                                    "seed": seed,
                                    "query_id": spec.query_id,
                                    "reason": cf_text_reason,
                                    "timing": timing,
                                    "bucket": bname,
                                }
                            )
                            continue
                        view_contexts = distinct_strict_view_contexts(view_contexts)
                        view_contexts, ordered_view_reason = prune_short_ordered_view(
                            view_contexts,
                            spec,
                            query_timing=timing,
                            minimum_tokens=min_dist_tok,
                            token_counter=_exact_metric_token_counter(
                                exact_settings, bname, count_packed_tokens
                            ),
                        )
                        if ordered_view_reason:
                            rejects.append(
                                {
                                    "seed": seed,
                                    "query_id": spec.query_id,
                                    "reason": ordered_view_reason,
                                    "view": "ordered_artifact_view",
                                    "timing": timing,
                                    "bucket": bname,
                                    "disposition": "optional_view_omitted",
                                }
                            )
                    canon = canonical_topology(spec)
                    near_dup = sentence_near_dup_ratio(packed.artifacts)
                    record_split, holdout = _split_for_spec(
                        cfg,
                        split,
                        seed=seed,
                        world_id=focal_w.world_id,
                        domain=spec.domain or domain,
                        spec=spec,
                        artifacts=views["minimal"],
                    )
                    base_task_id = stable_digest(
                        f"{focal_w.world_id}|{spec.base_task_group or spec.query_id}",
                        size=20,
                    )
                    semantic_base_task_id = stable_semantic_base_task_id(spec)
                    dossier = stable_dossier_id(
                        focal_w.world_id,
                        spec.query_id,
                        timing,
                        [artifact.artifact_id for artifact in packed.artifacts],
                    )
                    semantic_growth_group_id = stable_digest(
                        f"{focal_w.world_id}|"
                        f"{spec.semantic_growth_group or spec.query_id}",
                        size=20,
                    )
                    minimal_course_key = (base_task_id, timing)
                    view_contexts = _views_for_band(
                        view_contexts,
                        base_task_id=base_task_id,
                        timing=timing,
                        published_minimal_courses=published_minimal_courses,
                    )
                    proof_signature = stable_digest(
                        json.dumps(
                            {
                                "necessary": sorted(spec.essential_event_ids),
                                "sufficient": sorted(spec.sufficient_event_ids),
                                "expression": spec.gold_expression,
                            },
                            sort_keys=True,
                        ),
                        size=20,
                    )
                    answer_program_id = stable_answer_program_id(spec)
                    source_families = _source_families(views["minimal"])
                    source_provenance_ids = sorted(
                        {
                            classification.provenance_id
                            for classification in (
                                artifact_classification(artifact)
                                for artifact in views["minimal"]
                            )
                            if classification.provenance_id
                        }
                    )
                    relation_edges: list[dict[str, str]] = []
                    source_relation_id = (
                        stable_digest(
                            json.dumps(relation_edges, sort_keys=True), size=20
                        )
                        if relation_edges
                        else ""
                        if has_real_workflows
                        else (
                            stable_digest(
                                json.dumps(
                                    {
                                        "query_type": spec.query_type,
                                        "provenance_ids": source_provenance_ids,
                                        "program_ops": list(spec.program_ops or []),
                                    },
                                    sort_keys=True,
                                ),
                                size=20,
                            )
                            if source_provenance_ids
                            else stable_digest(
                                f"diagnostic|{spec.query_type}|{'+'.join(source_families)}",
                                size=20,
                            )
                            if source_families
                            else ""
                        )
                    )
                    slot_records: list[dict] = []
                    slot_hashes: set[str] = set()
                    slot_failed = False
                    for view_name, (context, view_artifacts) in view_contexts.items():
                        prompt = wrap_prompt(spec.question, context, timing)
                        exact_metric_counter = _exact_metric_token_counter(
                            exact_settings, bname, count_packed_tokens
                        )
                        metrics = compute_view_metrics(
                            view_artifacts,
                            dependency_evidence_ids(view_artifacts, spec),
                            query_timing=timing,
                            context=prompt if exact_metric_counter else context,
                            token_counter=exact_metric_counter,
                            token_prefix=(
                                prompt_document_prefix(spec.question, timing)
                                if exact_metric_counter
                                else ""
                            ),
                            query_boundary_tokens=(
                                prompt_query_boundary(
                                    spec.question,
                                    context,
                                    timing,
                                    exact_metric_counter,
                                )
                                if exact_metric_counter
                                else None
                            ),
                        )
                        answer = view_answer(spec, view_name)
                        view_roles = {
                            artifact.artifact_id: packed.roles.get(
                                artifact.artifact_id, "unclassified"
                            )
                            for artifact in view_artifacts
                        }
                        classified_artifacts = _classified_view_artifacts(
                            view_artifacts,
                            view_roles,
                            world_id=focal_w.world_id,
                        )
                        composition = composition_for_view(view_name)
                        objective = (
                            TrainingObjective.DIAGNOSTIC
                            if view_name in {"memory", "distractor_only"}
                            else TrainingObjective.SFT
                        )
                        classification_errors = validate_context_classification(
                            classified_artifacts,
                            objective=objective,
                            composition_method=composition,
                        )
                        workflow_ids_for_view = {
                            artifact_classification(artifact).workflow_id
                            for artifact in classified_artifacts
                            if artifact_classification(artifact).workflow_id
                        }
                        if strict_generation and (
                            classification_errors or len(workflow_ids_for_view) != 1
                        ):
                            rejects.append(
                                {
                                    "seed": seed,
                                    "query_id": spec.query_id,
                                    "reason": "classification_gate",
                                    "view": view_name,
                                    "errors": classification_errors,
                                    "workflow_ids": sorted(workflow_ids_for_view),
                                }
                            )
                            slot_failed = True
                            break
                        view_distance_reason = strict_view_evidence_reject_reason(
                            metrics, minimum_tokens=min_dist_tok
                        )
                        if strict_generation and view_distance_reason:
                            rejects.append(
                                {
                                    "seed": seed,
                                    "query_id": spec.query_id,
                                    "reason": view_distance_reason,
                                    "view": view_name,
                                    "timing": timing,
                                    "bucket": bname,
                                }
                            )
                            slot_failed = True
                            break
                        exact_verification = ver2
                        exact_notes: dict = {}
                        if strict_generation:
                            if exact_settings and exact_tokenizer is None:
                                exact_tokenizer = _load_exact_tokenizer(
                                    exact_model_id,
                                    exact_revision,
                                    bool(exact_settings.get("local_files_only", True)),
                                )
                            factual_map = {
                                artifact.artifact_id: artifact
                                for artifact in packed.artifacts
                            }
                            if view_name == "cf":
                                factual_view = [
                                    factual_map.get(artifact.artifact_id, artifact)
                                    for artifact in view_artifacts
                                ]
                                cf_view = view_artifacts
                            else:
                                factual_view = view_artifacts
                                cf_view = [
                                    cf_map.get(artifact.artifact_id, artifact)
                                    for artifact in view_artifacts
                                ]
                            factual_classified = _classified_view_artifacts(
                                factual_view,
                                view_roles,
                                world_id=focal_w.world_id,
                            )
                            cf_classified = _classified_view_artifacts(
                                cf_view,
                                view_roles,
                                world_id=focal_w.world_id,
                            )
                            exact_verification, exact_notes = verify_question(
                                focal_w,
                                spec,
                                factual_classified,
                                cf_artifacts=cf_classified,
                                window_ids=query_adjacent_window_artifact_ids(
                                    factual_classified, timing, ess
                                ),
                                surface_min=surface_min,
                                retrieval_top_k=3,
                                verification_mode=verification_mode,
                                raw_window_tokenizer=exact_tokenizer,
                            )
                            if not exact_verification.all_green():
                                rejects.append(
                                    {
                                        "seed": seed,
                                        "query_id": spec.query_id,
                                        "reason": "derived_view_gate",
                                        "view": view_name,
                                        "timing": timing,
                                        "bucket": bname,
                                        "gates": exact_verification.model_dump(),
                                        "contiguous_windows": exact_notes.get(
                                            "contiguous_windows"
                                        ),
                                        "raw_token_fact_windows": exact_notes.get(
                                            "raw_token_fact_windows"
                                        ),
                                    }
                                )
                                slot_failed = True
                                break
                            if view_name == "cf":
                                cf_shortcuts_green, cf_shortcut_notes = (
                                    counterfactual_shortcuts_insufficient(
                                        focal_w,
                                        spec,
                                        cf_classified,
                                        retrieval_top_k=3,
                                        raw_window_tokenizer=exact_tokenizer,
                                    )
                                )
                                if not cf_shortcuts_green:
                                    rejects.append(
                                        {
                                            "seed": seed,
                                            "query_id": spec.query_id,
                                            "reason": "counterfactual_view_shortcut",
                                            "view": view_name,
                                            "timing": timing,
                                            "bucket": bname,
                                            "counterfactual_shortcuts": cf_shortcut_notes,
                                        }
                                    )
                                    slot_failed = True
                                    break
                        view_verification = verify_rendered_view(
                            focal_w,
                            spec,
                            classified_artifacts,
                            view_name=view_name,
                            expected_answer=answer,
                            base_verification=exact_verification,
                            classification_ok=(
                                not classification_errors
                                and len(workflow_ids_for_view) == 1
                            ),
                        )
                        if (
                            strict_generation
                            and not view_verification.production_eligible
                        ):
                            rejects.append(
                                {
                                    "seed": seed,
                                    "query_id": spec.query_id,
                                    "reason": "view_gate",
                                    "view": view_name,
                                    "gates": view_verification.model_dump(),
                                }
                            )
                            slot_failed = True
                            break
                        content_hash = stable_digest(
                            json.dumps(
                                {
                                    "context": prompt,
                                    "answer": str(answer),
                                },
                                sort_keys=True,
                            )
                        )
                        if (
                            content_hash in seen_conversations
                            or content_hash in slot_hashes
                        ):
                            if strict_generation:
                                slot_failed = True
                                break
                            continue
                        slot_hashes.add(content_hash)
                        slot_qid = (
                            f"{spec.query_id}:{timing}:{metrics.position_bucket}:"
                            f"{bname}"
                        )
                        rec = SampleRecord(
                            world_id=focal_w.world_id,
                            seed=seed,
                            schema_version=str(
                                cfg.get("schema_version")
                                or mat.spec.get("schema_version")
                                or "p1.2"
                            ),
                            query_id=slot_qid,
                            query_type=spec.query_type,
                            query_timing=timing,
                            question=spec.question,
                            answer=answer,
                            cf_answer=spec.cf_answer,
                            view=view_name,
                            context=prompt,
                            proof_graph=proof,
                            difficulty=Difficulty(
                                context_tokens=metrics.context_tokens,
                                max_evidence_distance=metrics.max_evidence_distance,
                                proof_depth=int(gstat["proof_depth"]),
                                state_updates=len(focal_w.state.history),
                                query_delay=1 if timing == "late" else 0,
                                distractor_similarity=(1.0 if parallel_arts else 0.0),
                                visibility_gap=vis_gap,
                            ),
                            verification=exact_verification,
                            essential_artifact_ids=list(spec.essential_artifact_ids),
                            window_artifact_ids=packed.window_ids,
                            position_bucket=metrics.position_bucket,
                            length_bucket=bname,
                            split=record_split,
                            cf_op=spec.cf_op,
                        )
                        dumped = rec.model_dump()
                        dumped["view_verification"] = view_verification.model_dump()
                        dumped["document_context"] = context
                        dumped["graph"] = gstat
                        dumped["n_unique_docs"] = packed.n_unique
                        dumped["n_clones"] = packed.n_clones
                        dumped["motif"] = spec.motif
                        dumped["topology_id"] = spec.topology_id
                        dumped["topology_family"] = topology_family(spec)
                        dumped["canonical_topology"] = canon
                        dumped["domain"] = spec.domain or domain
                        dumped["n_workstreams"] = n_workstreams
                        dumped["include_program_joins"] = bool(
                            cfg.get("include_program_joins", True)
                        )
                        dumped["truth_regime"] = spec.truth_regime
                        dumped["artifact_roles"] = view_roles
                        dumped["boilerplate_token_ratio"] = (
                            packed.boilerplate_token_ratio
                        )
                        dumped["pulse_doc_ratio"] = packed.pulse_doc_ratio
                        dumped["hard_negative_doc_ratio"] = (
                            packed.hard_negative_doc_ratio
                        )
                        dumped["near_dup_sentence_ratio"] = round(near_dup, 4)
                        dumped["natural_tokens"] = metrics.context_tokens
                        dumped["actual_context_tokens"] = metrics.context_tokens
                        if exact_settings and bname in EXACT_TOKEN_BAND_RANGES:
                            if exact_tokenizer is None:
                                exact_tokenizer = _load_exact_tokenizer(
                                    exact_model_id,
                                    exact_revision,
                                    bool(exact_settings.get("local_files_only", True)),
                                )
                            exact_metadata, exact_length_reason = (
                                exact_token_metadata_for_band(
                                    prompt,
                                    bname,
                                    model_id=exact_model_id,
                                    revision=exact_revision,
                                    asset_manifest_sha256=(exact_asset_manifest_sha256),
                                    tokenizer=exact_tokenizer,
                                    cache=exact_token_cache,
                                )
                            )
                            dumped.update(exact_metadata)
                            if strict_generation and exact_length_reason:
                                rejects.append(
                                    {
                                        "seed": seed,
                                        "query_id": spec.query_id,
                                        "reason": exact_length_reason,
                                        "view": view_name,
                                        "timing": timing,
                                        "bucket": bname,
                                    }
                                )
                                slot_failed = True
                                break
                        dumped["requested_max_tokens"] = packed.requested_max_tokens
                        if classified_artifacts:
                            _, source_metric_tokens, source_ratio = (
                                real_source_marginal_token_metrics(
                                    classified_artifacts,
                                    question=spec.question,
                                    timing=timing,
                                    token_counter=(
                                        exact_metric_counter or estimate_tokens
                                    ),
                                    include_prompt=exact_metric_counter is not None,
                                )
                            )
                        else:
                            source_metric_tokens = metrics.context_tokens
                            source_ratio = 0.0
                        if source_metric_tokens != metrics.context_tokens:
                            raise ValueError(
                                "real source metric does not bind the rendered context"
                            )
                        dumped["real_source_token_ratio"] = round(source_ratio, 4)
                        dumped["cross_workstream"] = spec.motif in {
                            "cross_workstream",
                            "cross_stream_release_gate",
                        }
                        dumped["source_grounded"] = spec.query_type == "source_grounded"
                        dumped["source_choice"] = spec.query_type == "source_choice"
                        dumped["revisitation"] = spec.query_type == "revisitation"
                        dumped["ratification"] = spec.query_type == "ratification"
                        dumped["docket_control"] = spec.query_type == "docket_control"
                        dumped["dependency_class"] = dependency_class_for_view(
                            spec, metrics, view_name
                        )
                        dumped["evidence_distance"] = metrics.max_evidence_distance
                        raw_fact_windows = exact_notes.get("raw_token_fact_windows")
                        dumped["evidence_span_tokens"] = metrics.evidence_span_tokens
                        if isinstance(raw_fact_windows, dict) and isinstance(
                            raw_fact_windows.get("tokenizer_evidence_span_tokens"), int
                        ):
                            dumped["tokenizer_evidence_span_tokens"] = int(
                                raw_fact_windows["tokenizer_evidence_span_tokens"]
                            )
                        dumped["query_evidence_distance"] = (
                            metrics.query_evidence_distance
                        )
                        dumped["evidence_count"] = metrics.evidence_count
                        dumped["data_product"] = str(
                            cfg.get("data_product") or "causalcore_v0"
                        )
                        dumped["data_stage"] = data_stage
                        dumped["hop_count"] = int(gstat["hop_count"])
                        dumped["searchart_width"] = len(spec.essential_artifact_ids)
                        dumped["oracle_long_gain"] = int(
                            bool(
                                exact_verification.full_sufficient
                                and exact_verification.local_window_insufficient
                            )
                        )
                        dumped["program_ops"] = list(spec.program_ops or [])
                        dumped["base_task_id"] = base_task_id
                        dumped["semantic_base_task_id"] = semantic_base_task_id
                        dumped["dossier_id"] = dossier
                        dumped["semantic_growth_group_id"] = semantic_growth_group_id
                        dumped["executable_proof_id"] = proof_signature
                        dumped["source_relation_id"] = source_relation_id
                        dumped["source_relation_edges"] = relation_edges
                        dumped["source_family_ids"] = source_families
                        dumped["answer_program_id"] = answer_program_id
                        dumped["content_hash"] = content_hash
                        dumped["holdout"] = holdout
                        dumped["split_strategy"] = holdout["strategy"]
                        dumped["semantic_tokens"] = _semantic_tokens(
                            classified_artifacts,
                            workflow_id=focal_w.world_id,
                        )
                        dumped["context_source_relation_count"] = (
                            context_source_relation_count(
                                focal_w, classified_artifacts, spec=spec
                            )
                            if has_real_workflows
                            else 0
                        )
                        visible_events = {
                            event_id
                            for artifact in classified_artifacts
                            for event_id in artifact.reveals_events
                        }
                        row_relation_edges = (
                            real_source_relation_edges(
                                focal_w, spec, classified_artifacts
                            )
                            if has_real_workflows
                            else relation_edges
                        )
                        row_source_relation_id = (
                            stable_digest(
                                json.dumps(row_relation_edges, sort_keys=True),
                                size=20,
                            )
                            if row_relation_edges
                            else source_relation_id
                        )
                        dumped["source_relation_id"] = row_source_relation_id
                        dumped["source_relation_edges"] = row_relation_edges
                        authentic_relation_edges = [
                            edge
                            for edge in row_relation_edges
                            if edge.get("relation_provenance") == "authentic_source"
                        ]
                        dumped["authentic_source_relation_edges"] = (
                            authentic_relation_edges
                        )
                        dumped["authentic_source_relation_id"] = (
                            stable_digest(
                                json.dumps(authentic_relation_edges, sort_keys=True),
                                size=20,
                            )
                            if authentic_relation_edges
                            else ""
                        )
                        dumped["hybrid_causal_edges"] = [
                            edge
                            for edge in row_relation_edges
                            if edge.get("relation_provenance") == "synthetic_executable"
                        ]
                        dumped["strict_support_event_count"] = len(
                            visible_events.intersection(spec.sufficient_event_ids)
                        )
                        classifications = [
                            artifact_classification(artifact)
                            for artifact in classified_artifacts
                        ]
                        dumped["source_origins"] = sorted(
                            {c.source_origin.value for c in classifications}
                        )
                        dumped["workflow_kinds"] = sorted(
                            {c.workflow_kind.value for c in classifications}
                        )
                        dumped["workflow_ids"] = sorted(
                            {c.workflow_id for c in classifications if c.workflow_id}
                        )
                        dumped["artifact_classification"] = [
                            {
                                "artifact_id": artifact.artifact_id,
                                "source_origin": classification.source_origin.value,
                                "workflow_kind": classification.workflow_kind.value,
                                "evidence_role": packed.roles.get(
                                    artifact.artifact_id,
                                    classification.evidence_role.value,
                                ),
                                "workflow_id": classification.workflow_id,
                                "provenance_id": classification.provenance_id,
                            }
                            for artifact, classification in zip(
                                classified_artifacts, classifications
                            )
                        ]
                        if replay_binding is not None:
                            dumped["episode_replay_bundle"] = dict(replay_binding)
                        if source_replay_binding is not None:
                            dumped["source_workflow_bundle"] = dict(
                                source_replay_binding
                            )
                        dynamic_roles = {
                            packed.roles.get(artifact.artifact_id, "unclassified")
                            for artifact in view_artifacts
                        }
                        dumped["evidence_roles"] = sorted(
                            {
                                EvidenceRole(role).value
                                if role in EvidenceRole._value2member_map_
                                else EvidenceRole.UNCLASSIFIED.value
                                for role in dynamic_roles
                            }
                        )
                        dumped["composition_method"] = composition.value
                        dumped["training_objective"] = objective.value
                        dumped["process"] = dict(
                            (mat.spec.get("focal") or {}).get("process") or {}
                        )
                        dumped["register"] = str(
                            (mat.spec.get("focal") or {}).get("register") or ""
                        )
                        style = ""
                        for art in packed.artifacts:
                            if art.doc_type == "source_pack":
                                continue
                            if ess and art.artifact_id not in ess:
                                continue
                            plan = dict((art.slots or {}).get("content_plan") or {})
                            style = str(plan.get("style_cluster") or "")
                            if style:
                                break
                        dumped["style_cluster"] = style or (
                            f"{dumped['register']}:mixed" if dumped["register"] else ""
                        )
                        dumped["program_join"] = spec.query_type == "program_join"
                        dumped["compositional_dependency"] = bool(
                            "+" in str(spec.motif or "")
                            and spec.query_type != "program_join"
                        )
                        dumped["motif_composition"] = dumped["compositional_dependency"]
                        if strict_generation:
                            assert attestation_key is not None
                            dumped = attach_attestation(
                                dumped,
                                attestation_key,
                                purpose=("sft_row" if train_ready else "candidate_row"),
                            )
                        slot_records.append(dumped)
                    if strict_generation and (
                        slot_failed or len(slot_records) != len(view_contexts)
                    ):
                        continue
                    seen_conversations.update(slot_hashes)
                    kept.extend(slot_records)
                    if any(record.get("view") == "minimal" for record in slot_records):
                        published_minimal_courses.add(minimal_course_key)
                    stats["kept_slots"] += 1
                    prev_tokens = packed.tokens
                    band_emitted = True
                if not band_emitted:
                    continue
    return kept, rejects, stats


def _job(payload: tuple[int, dict, str, str]) -> tuple[list[dict], list[dict], dict]:
    seed, cfg, split, domain = payload
    return emit_records(seed, cfg, split, domain)


def build_generation_jobs(
    cfg: dict,
    *,
    n_worlds: int,
    seed_start: int,
    n_train_worlds: int,
) -> tuple[list[tuple[int, dict, str, str]], dict[str, int]]:
    required_seed_start = cfg.get("required_seed_start")
    if required_seed_start is not None and seed_start != int(required_seed_start):
        raise ValueError(
            f"required_seed_start={required_seed_start} but received {seed_start}"
        )
    domains = [str(domain) for domain in cfg.get("domains") or ["company"]]
    raw_quotas = cfg.get("domain_world_quotas")
    quotas = (
        {str(domain): value for domain, value in raw_quotas.items()}
        if isinstance(raw_quotas, dict)
        else None
    )
    schedule = balanced_domain_schedule(domains, n_worlds=n_worlds, quotas=quotas)
    jobs = [
        (
            seed_start + index,
            cfg,
            "train" if index < n_train_worlds else "eval",
            domain,
        )
        for index, domain in enumerate(schedule)
    ]
    return jobs, dict(Counter(schedule))


def resolve_world_targets(
    cfg: dict,
    n_worlds_override: int | None,
    promoted_worlds_override: int | None,
) -> tuple[int, int]:
    configured_worlds = int(cfg.get("n_worlds", 50))
    if (
        n_worlds_override is not None
        and n_worlds_override != configured_worlds
        and promoted_worlds_override is None
        and cfg.get("target_promoted_worlds") is not None
    ):
        raise ValueError("overriding --n-worlds requires --target-promoted-worlds")
    n_worlds = n_worlds_override or configured_worlds
    target = int(
        promoted_worlds_override or cfg.get("target_promoted_worlds") or n_worlds
    )
    if n_worlds < 1 or target < 1:
        raise ValueError("world budgets must be positive")
    if target > n_worlds:
        raise ValueError("target promoted worlds cannot exceed candidate worlds")
    return n_worlds, target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "configs" / "p0.yaml")
    ap.add_argument("--n-worlds", type=int, default=None)
    ap.add_argument("--target-promoted-worlds", type=int, default=None)
    ap.add_argument("--seed-start", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "p0")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    enforce_source_requirements(cfg)
    n_worlds, target_promoted_worlds = resolve_world_targets(
        cfg, args.n_worlds, args.target_promoted_worlds
    )
    release_profile_id = str(cfg.get("release_profile_id") or "")
    if cfg.get("strict_semantic_verification"):
        if not release_profile_id:
            raise ValueError("strict generation requires a release_profile_id")
        profile = release_profile(release_profile_id)
        if target_promoted_worlds != profile.expected_promoted_worlds:
            raise ValueError(
                "release profile promoted-world target mismatch: "
                f"{target_promoted_worlds}!={profile.expected_promoted_worlds}"
            )
    train_ratio = float(cfg.get("train_ratio", 0.8))

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    train_f = (out / "train.jsonl").open("w")
    eval_f = (out / "eval.jsonl").open("w")
    rej_f = (out / "reject_log.jsonl").open("w")

    if n_worlds <= 1:
        n_train_w = n_worlds
    else:
        n_train_w = min(n_worlds - 1, max(1, int(n_worlds * train_ratio)))

    jobs, planned_domain_worlds = build_generation_jobs(
        cfg,
        n_worlds=n_worlds,
        seed_start=args.seed_start,
        n_train_worlds=n_train_w,
    )

    n_kept = 0
    n_q = 0
    n_rej = 0
    n_att = 0
    n_kept_slots = 0
    n_clones = 0
    type_c: Counter = Counter()
    view_c: Counter = Counter()
    len_c: Counter = Counter()
    motif_c: Counter = Counter()
    domain_c: Counter = Counter()
    attempted_domain_world_c: Counter = Counter()
    emitting_domain_worlds: dict[str, set[str]] = {
        domain: set() for domain in planned_domain_worlds
    }
    topo_c: Counter = Counter()
    family_c: Counter = Counter()
    canon_c: Counter = Counter()
    rej_c: Counter = Counter()
    split_q: dict[str, set[str]] = {"train": set(), "eval": set()}
    split_base: dict[str, set[str]] = {"train": set(), "eval": set()}
    world_ids: set[str] = set()
    answers: Counter = Counter()
    boiler_acc: list[float] = []
    pulse_acc: list[float] = []
    hn_acc: list[float] = []
    src_acc: list[float] = []
    n_comp = 0
    n_src_q = 0
    n_choice_q = 0
    n_xs = 0
    n_rev_q = 0
    n_rat_q = 0
    n_dock_q = 0
    dep_c: Counter = Counter()
    dist_acc: list[float] = []
    register_c: Counter = Counter()
    style_c: Counter = Counter()
    dup_acc: list[float] = []
    base_task_ids: set[str] = set()
    proof_ids: set[str] = set()
    source_relation_ids: set[str] = set()
    answer_program_ids: set[str] = set()
    content_hashes: set[str] = set()

    def consume(kept: list[dict], rejects: list[dict], st: dict) -> None:
        nonlocal \
            n_kept, \
            n_q, \
            n_rej, \
            n_att, \
            n_kept_slots, \
            n_clones, \
            n_comp, \
            n_src_q, \
            n_choice_q, \
            n_xs, \
            n_rev_q, \
            n_rat_q, \
            n_dock_q
        n_att += int(st.get("attempts") or 0)
        n_kept_slots += int(st.get("kept_slots") or 0)
        n_clones += int(st.get("n_clones") or 0)
        attempted_domain = str(st.get("domain") or "?")
        attempted_domain_world_c[attempted_domain] += 1
        for r in rejects:
            rej_f.write(json.dumps(r, default=str) + "\n")
            n_rej += 1
            key = str(r.get("reason", "?"))
            if ":" in key:
                key = key.split(":", 1)[0]
            rej_c[key] += 1
        seen_q: set[str] = set()
        for rec in kept:
            dest = train_f if rec["split"] == "train" else eval_f
            dest.write(json.dumps(rec) + "\n")
            n_kept += 1
            type_c[rec["query_type"]] += 1
            view_c[rec["view"]] += 1
            len_c[rec["length_bucket"]] += 1
            motif_c[rec.get("motif") or "?"] += 1
            domain_c[rec.get("domain") or "?"] += 1
            emitting_domain_worlds.setdefault(str(rec.get("domain") or "?"), set()).add(
                str(rec["world_id"])
            )
            topo_c[rec.get("topology_id") or "?"] += 1
            family_c[rec.get("topology_family") or "?"] += 1
            world_ids.add(rec["world_id"])
            if rec.get("base_task_id"):
                base_task_id = str(rec["base_task_id"])
                base_task_ids.add(base_task_id)
                split_base[rec["split"]].add(base_task_id)
            if rec.get("executable_proof_id"):
                proof_ids.add(str(rec["executable_proof_id"]))
            if rec.get("source_relation_id"):
                source_relation_ids.add(str(rec["source_relation_id"]))
            if rec.get("answer_program_id"):
                answer_program_ids.add(str(rec["answer_program_id"]))
            if rec.get("content_hash"):
                content_hashes.add(str(rec["content_hash"]))
            if rec["view"] == "full":
                seen_q.add(rec["query_id"])
                split_q[rec["split"]].add(rec["query_id"])
                answers[str(rec["answer"])] += 1
                canon_c[rec.get("canonical_topology") or "?"] += 1
                boiler_acc.append(float(rec.get("boilerplate_token_ratio") or 0))
                pulse_acc.append(float(rec.get("pulse_doc_ratio") or 0))
                hn_acc.append(float(rec.get("hard_negative_doc_ratio") or 0))
                src_acc.append(float(rec.get("real_source_token_ratio") or 0))
                if rec.get("source_grounded"):
                    n_src_q += 1
                if rec.get("source_choice"):
                    n_choice_q += 1
                if rec.get("revisitation"):
                    n_rev_q += 1
                if rec.get("ratification"):
                    n_rat_q += 1
                if rec.get("docket_control"):
                    n_dock_q += 1
                dep_c[str(rec.get("dependency_class") or "local_or_mixed")] += 1
                dist_acc.append(float(rec.get("evidence_distance") or 0))
                register_c[str(rec.get("register") or "?")] += 1
                style_c[str(rec.get("style_cluster") or "?")] += 1
                dup_acc.append(float(rec.get("near_dup_sentence_ratio") or 0))
                if rec.get("cross_workstream"):
                    n_xs += 1
                if rec.get("motif_composition"):
                    n_comp += 1
        n_q += len(seen_q)

    if args.workers <= 1:
        for i, job in enumerate(jobs):
            consume(*_job(job))
            if (i + 1) % 5 == 0 or (i + 1) == n_worlds:
                print(
                    f"worlds={i + 1}/{n_worlds} rows={n_kept} questions={n_q} rejects={n_rej}",
                    flush=True,
                )
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for done, (kept, rejects, st) in enumerate(ex.map(_job, jobs), start=1):
                consume(kept, rejects, st)
                if done % 5 == 0 or done == n_worlds:
                    print(
                        f"worlds={done}/{n_worlds} rows={n_kept} questions={n_q} rejects={n_rej}",
                        flush=True,
                    )

    train_f.close()
    eval_f.close()
    rej_f.close()

    candidate_row_digests: list[str] = []
    for name in ("train.jsonl", "eval.jsonl"):
        with (out / name).open() as handle:
            candidate_row_digests.extend(
                serialized_row_sha256(json.loads(line))
                for line in handle
                if line.strip()
            )
    candidate_row_set_sha256 = (
        row_digest_set_sha256(candidate_row_digests) if candidate_row_digests else None
    )
    retention = (n_kept_slots / n_att) if n_att else 0.0
    canonical_neff = round(effective_number(canon_c), 3)
    join_row_share = round((int(type_c.get("program_join") or 0) / max(1, n_kept)), 4)
    report = {
        "schema_version": str(cfg.get("schema_version") or "p1.2"),
        "data_product": str(cfg.get("data_product") or "causalcore_v0"),
        "data_stage": str(cfg.get("data_stage") or "diagnostic"),
        "trust_scope": str(cfg.get("trust_scope") or "unspecified"),
        "release_profile_id": release_profile_id or None,
        "release_profile_sha256": (
            release_profile_sha256(release_profile_id) if release_profile_id else None
        ),
        "n_worlds": n_worlds,
        "target_promoted_worlds": target_promoted_worlds,
        "n_world_ids": len(world_ids),
        "n_rows": n_kept,
        "candidate_row_set_sha256": candidate_row_set_sha256,
        "n_questions": len(base_task_ids),
        "n_question_slots": n_q,
        "n_rejects": n_rej,
        "n_attempts": n_att,
        "n_kept_slots": n_kept_slots,
        "retention": round(retention, 4),
        "n_clones": n_clones,
        "unique_full_answers": len(answers),
        "n_unique_base_tasks": len(base_task_ids),
        "n_unique_executable_proofs": len(proof_ids),
        "n_unique_source_relations": len(source_relation_ids),
        "n_unique_answer_programs": len(answer_program_ids),
        "n_unique_content_hashes": len(content_hashes),
        "n_exact_duplicate_rows": max(0, n_kept - len(content_hashes)),
        "n_topologies": len(topo_c),
        "n_topology_families": len(family_c),
        "n_canonical_topologies": len(canon_c),
        "canonical_neff": canonical_neff,
        "max_canonical_share": round(max_family_share(canon_c), 4),
        "mean_boilerplate_token_ratio": round(
            (sum(boiler_acc) / len(boiler_acc)) if boiler_acc else 0.0, 4
        ),
        "mean_pulse_doc_ratio": round(
            (sum(pulse_acc) / len(pulse_acc)) if pulse_acc else 0.0, 4
        ),
        "mean_hard_negative_doc_ratio": round(
            (sum(hn_acc) / len(hn_acc)) if hn_acc else 0.0, 4
        ),
        "mean_real_source_token_ratio": round(
            (sum(src_acc) / len(src_acc)) if src_acc else 0.0, 4
        ),
        "n_source_grounded_full": n_src_q,
        "n_source_choice_full": n_choice_q,
        "n_revisitation_full": n_rev_q,
        "n_ratification_full": n_rat_q,
        "n_docket_control_full": n_dock_q,
        "n_deep_dependency_full": int(dep_c.get("deep_dependency") or 0),
        "n_long_range_retrieval_full": int(dep_c.get("long_range_retrieval") or 0),
        "by_dependency_class": dict(dep_c),
        "mean_evidence_distance": round(
            (sum(dist_acc) / len(dist_acc)) if dist_acc else 0.0, 1
        ),
        "n_registers": len(register_c),
        "register_neff": round(effective_number(register_c), 3),
        "by_register": dict(register_c),
        "n_style_clusters": len(style_c),
        "style_cluster_neff": round(effective_number(style_c), 3),
        "by_style_cluster": dict(style_c),
        "mean_near_dup_sentence_ratio": round(
            (sum(dup_acc) / len(dup_acc)) if dup_acc else 0.0, 4
        ),
        "n_cross_workstream_full": n_xs,
        "motif_composition_rate": round((n_comp / max(1, len(boiler_acc))), 4),
        "join_row_share": join_row_share,
        "n_motifs": len(motif_c),
        "n_domains": len(domain_c),
        "planned_worlds_by_domain": planned_domain_worlds,
        "attempted_worlds_by_domain": dict(attempted_domain_world_c),
        "emitting_worlds_by_domain": {
            domain: len(world_ids)
            for domain, world_ids in emitting_domain_worlds.items()
        },
        "train_questions": len(split_base["train"]),
        "eval_questions": len(split_base["eval"]),
        "train_question_slots": len(split_q["train"]),
        "eval_question_slots": len(split_q["eval"]),
        "by_query_type": dict(type_c),
        "by_view": dict(view_c),
        "by_length": dict(len_c),
        "by_motif": dict(motif_c),
        "by_domain": dict(domain_c),
        "by_topology_family": dict(family_c),
        "by_canonical_topology": dict(canon_c),
        "reject_reasons": dict(rej_c),
        "gold_cfr": 1.0 if n_kept else 0.0,
    }
    if cfg.get("strict_semantic_verification"):
        report_key = attestation_key_from_env("quality_report")
        if report_key is None:
            raise ValueError(f"strict generation requires a 32-byte {ATTESTATION_ENV}")
        report = attach_attestation(report, report_key, purpose="quality_report")
    (out / "quality_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if n_clones:
        raise SystemExit(f"clone packing leaked {n_clones} documents")
    if uses_legacy_exit_bars(str(report.get("data_stage") or "diagnostic")):
        if n_worlds >= 50 and n_q < 200:
            raise SystemExit(f"P0 exit bar missed: only {n_q} questions (need >=200)")
        if n_worlds >= 8 and n_q < 40:
            raise SystemExit(f"P0 week-1 bar missed: only {n_q} questions (need >=40)")
        if n_worlds >= 12 and canonical_neff < 24:
            raise SystemExit(
                "v2 probe bar missed: "
                f"canonical_neff={report.get('canonical_neff')} (need >=24)"
            )
    if n_worlds >= 12 and join_row_share > 0.40:
        raise SystemExit(
            f"JOIN flood: join_row_share={report.get('join_row_share')} (need <=0.40)"
        )


if __name__ == "__main__":
    main()
