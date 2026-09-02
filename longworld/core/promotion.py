"""Fail-closed dense-retrieval audit and SFT candidate promotion."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import replace
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from longworld.core.attestation import (
    attach_attestation,
    canonical_attested_payload,
    local_probe_diagnostic_metadata,
    sanitized_attestation_environment,
    verify_attestation,
    verify_attestation_identity,
)
from longworld.core.engine import answer_from_artifacts
from longworld.core.filingworkflow import (
    SEC_HYBRID_CHILD_EVENT_TYPES,
    selected_sec_source_relation_edges,
)
from longworld.core.graph import graph_stats
from longworld.core.issuerfilingworkflow import (
    ISSUER_IR_HYBRID_CHILD_EVENT_TYPES,
    selected_issuer_ir_source_relation_edges,
)
from longworld.core.issuerpdfworkflow import (
    selected_issuer_official_pdf_relation_edges,
)
from longworld.core.pack import (
    SEP,
    compute_view_metrics,
    dependency_class_for_view,
    dependency_evidence_ids,
    estimate_tokens,
    join_artifacts,
    prompt_document_prefix,
    prompt_query_boundary,
    real_source_marginal_token_metrics,
    wrap_prompt,
)
from longworld.core.production_trust import (
    verify_embedded_production_approval_from_env,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.realworkflow import (
    EPISODE_REPLAY_BUNDLE_SCHEMA,
    load_episode_replay_bundle,
)
from longworld.core.record_contract import (
    EXACT_TOKEN_BAND_RANGES,
    STRICT_REPLAY_REVISION,
    exact_token_band_reject_reason,
    sft_row_errors,
)
from longworld.core.release_profile import (
    release_profile,
    release_profile_sha256,
)
from longworld.core.render import Artifact
from longworld.core.sampler import materialize
from longworld.core.semantic import (
    boilerplate_char_fraction,
    pulse_doc_ratio,
    sentence_near_dup_ratio,
)
from longworld.core.sourcebundle import (
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
    load_source_workflow_bundle,
)
from longworld.core.sourceworkflow import SOURCE_WORKFLOW_ADAPTER_REVISIONS
from longworld.core.taskproof import TASK_PROOF_RECEIPT_SCHEMA
from longworld.core.taskreplaysidecar import (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    TASK_REPLAY_ADAPTER_REGISTRY,
    TASK_REPLAY_SIDECAR_SCHEMA_V2,
    TASK_REPLAY_SIDECAR_SCHEMA_V3,
    TASK_VIEW_DERIVATION_REVISION,
    task_candidate_content_commitment,
)
from longworld.core.taxonomy import artifact_classification
from longworld.core.tokenizer_assets import (
    TokenizerAssetError,
    resolved_tokenizer_asset_manifest_sha256,
)
from longworld.core.topology import canonical_topology, topology_family
from longworld.core.verify import (
    Verification,
    counterfactual_shortcuts_insufficient,
    query_adjacent_window_artifact_ids,
    verify_question,
)
from longworld.core.views import render_cf_view
from longworld.core.wikiparse import WIKI_HYBRID_CHILD_EVENT_TYPES
from longworld.domains.codeforge.multiband import _has_authentic_source_relation
from longworld.domains.company.queries import QuerySpec
from longworld.domains.researchlab.simulate import (
    selected_wiki_source_relation_edges,
    valid_arxiv_revision_relation_event,
)

DENSE_AUDIT_PURPOSE = "dense_retrieval_audit"
DENSE_RANKING_PURPOSE = "dense_ranking"
CANDIDATE_ATTESTATION_PURPOSE = "candidate_row"
DENSE_RANKING_SCHEMA = "dense-ranking-v2"
PROMOTION_SCHEMA = "train-ready-promotion-v2"
REAL_REPLAY_BUNDLE_PURPOSE = "episode_replay_bundle"
QUALITY_REPORT_PURPOSE = "quality_report"
QUALITY_REPORT_BINDING_REVISION = "longworld-quality-binding-v4"
CANDIDATE_UNION_REPORT_SCHEMA = "longworld-release-candidate-union-v1"
TRAIN_READY_UNION_REPORT_SCHEMA = "longworld-release-train-ready-report-v1"
RELEASE_UNION_DATA_PRODUCT = "worldlong_release_union_v1"
RELEASE_SELECTION_SCHEMA = "longworld-release-world-selection-v2"
RELEASE_SELECTION_PURPOSE = "release_world_selection"
RELEASE_GATE_SCHEMA = "longworld-release-gate-pass-v1"
RELEASE_GATE_PURPOSE = "release_gate_pass"
RELEASE_GATE_REVISION = "longworld-quality-gate-v6"
TASK_SEMANTIC_COMMITMENT_SCHEMA = "longworld.task-semantic-commitment.v2"
LEGACY_RELEASE_GATE_REVISION = "longworld-quality-gate-v5"
_LEGACY_RELEASE_GATE_PROFILE_IDS = frozenset(
    {
        "p3-probe-12-v1",
        "p4-multidomain-probe-12-v1",
        "p6-source-dependent-probe-12-v1",
        "p7-source-rich-probe-12-v1",
        "p7-sec-source-slice-1-v1",
        "p7-wiki-source-slice-1-v1",
        "p7-paper-source-slice-1-v1",
        "p7-github-source-slice-1-v1",
        "p4-multidomain-local-48-v1",
        "p3-production-48-v1",
        "p3-production-210-v1",
    }
)

_HEX_REVISION = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_APPROVED_DENSE_MODELS = {
    (
        "huggingface",
        "sentence-transformers/all-MiniLM-L6-v2",
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "sentence-transformers-6.0.0",
        "dot_product",
    )
}


def release_gate_revision_supported(profile_id: str, revision: object) -> bool:
    """Accept v5 only for explicitly registered historical profiles."""
    return revision == RELEASE_GATE_REVISION or (
        revision == LEGACY_RELEASE_GATE_REVISION
        and profile_id in _LEGACY_RELEASE_GATE_PROFILE_IDS
    )


_APPROVED_EXACT_TOKENIZERS = {
    (
        "Qwen/Qwen3.5-4B",
        "a7b0d22b993d71000cf2eadfb37222a67cee521e",
    )
}


@lru_cache(maxsize=8)
def _materialize_synthetic_replay(
    seed: int, domain: str, n_workstreams: int, include_program_joins: bool
):
    """Cache a synthetic baseline; callers must copy before replay."""
    return materialize(
        seed,
        n_parallel=0,
        n_pulses=0,
        domain=domain,
        n_workstreams=n_workstreams,
        include_program_joins=include_program_joins,
    )


def _synthetic_replay_materialization(
    seed: int, domain: str, n_workstreams: int, include_program_joins: bool
):
    return copy.deepcopy(
        _materialize_synthetic_replay(
            seed, domain, n_workstreams, include_program_joins
        )
    )


_REAL_REPLAY_MATERIALIZATION_CACHE_MAX = 4
_REAL_REPLAY_MATERIALIZATION_CACHE: OrderedDict[tuple[Any, ...], Any] = OrderedDict()


def _real_replay_materialization(
    cache_identity: tuple[str, ...],
    *,
    seed: int,
    domain: str,
    n_workstreams: int,
    include_program_joins: bool,
    real_workflows: list[Any] | None = None,
    source_workflows: list[Any] | None = None,
):
    """Cache only a verified bundle's immutable materialization baseline."""
    if (real_workflows is None) == (source_workflows is None):
        raise PromotionError("real replay requires exactly one workflow source")
    key = (
        *cache_identity,
        seed,
        domain,
        n_workstreams,
        include_program_joins,
    )
    materialized = _REAL_REPLAY_MATERIALIZATION_CACHE.get(key)
    if materialized is None:
        materialized = materialize(
            seed,
            n_parallel=0,
            n_pulses=0,
            domain=domain,
            n_workstreams=n_workstreams,
            real_workflows=real_workflows,
            source_workflows=source_workflows,
            include_program_joins=include_program_joins,
        )
        _REAL_REPLAY_MATERIALIZATION_CACHE[key] = materialized
        while (
            len(_REAL_REPLAY_MATERIALIZATION_CACHE)
            > _REAL_REPLAY_MATERIALIZATION_CACHE_MAX
        ):
            _REAL_REPLAY_MATERIALIZATION_CACHE.popitem(last=False)
    else:
        _REAL_REPLAY_MATERIALIZATION_CACHE.move_to_end(key)
    return copy.deepcopy(materialized)


_WORKSTREAM_ARTIFACT = re.compile(
    r"\.workflow_(\d+)_(?:request|review|ci|license|merge)$"
)
_COMPOSITION_BY_VIEW = {
    "full": "same_case_dossier",
    "minimal": "same_case_dossier",
    "ordered_artifact_view": "causal_timeline",
    "cf": "counterfactual_twin",
}


class PromotionError(ValueError):
    """A candidate or audit cannot be promoted without weakening a gate."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def stable_dossier_id(
    world_id: str,
    query_id: str,
    query_timing: str,
    artifact_ids: list[str],
) -> str:
    """Identify one factual packed dossier independently of rendered view metrics."""
    return _sha256_text(
        json.dumps(
            {
                "world_id": world_id,
                "query_id": query_id,
                "query_timing": query_timing,
                "artifact_ids": sorted(artifact_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )[:20]


def stable_semantic_base_task_id(spec: QuerySpec) -> str:
    """Identify a source-independent task template and operator sequence."""
    return _sha256_text(
        json.dumps(
            {
                "domain": spec.domain,
                "query_type": spec.query_type,
                "motif": spec.motif,
                "operator_sequence": [
                    str(operation.get("op") or "")
                    for operation in (spec.program_ops or [])
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )[:20]


_ANSWER_PROGRAM_INSTANCE_KEYS = frozenset(
    {
        "answer_key",
        "event_id",
        "event_ids",
        "provenance_id",
        "query_id",
        "record_id",
        "record_key",
        "source_id",
        "source_key",
        "topology_id",
        "workflow_id",
        "workflow_ids",
        "world_id",
    }
)
_ANSWER_PROGRAM_SCALE_KEYS = frozenset(
    {"count", "cycle_count", "lanes", "streams", "tier", "workstreams"}
)


def _stable_answer_program_operand(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_answer_program_operand(value[key])
            for key in sorted(value)
            if key not in _ANSWER_PROGRAM_INSTANCE_KEYS
            and key not in _ANSWER_PROGRAM_SCALE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_answer_program_operand(item) for item in value]
    if isinstance(value, set):
        return sorted(_stable_answer_program_operand(item) for item in value)
    return value


def stable_answer_program_id(spec: QuerySpec) -> str:
    """Identify one executable operator program independently of proof length."""
    program_ops = list(spec.program_ops or [])
    semantic_program = (
        _stable_answer_program_operand(program_ops)
        if program_ops
        else [
            {
                "op": "GOLD_EXPRESSION",
                "expression": " ".join(spec.gold_expression.split()),
            }
        ]
    )
    return _sha256_text(
        json.dumps(
            {
                "domain": spec.domain,
                "query_type": spec.query_type,
                "motif": spec.motif,
                "program_ops": semantic_program,
                "cf_op": spec.cf_op,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )[:20]


def serialized_row_sha256(row: dict[str, Any]) -> str:
    """Digest every serialized row field, including its attestation."""
    return _canonical_sha256(row)


def row_digest_set_sha256(row_digests: list[str]) -> str:
    """Bind an unordered set of fixed-width serialized-row digests."""
    if not row_digests:
        raise PromotionError("row digest set is empty")
    if len(set(row_digests)) != len(row_digests) or any(
        _SHA256.fullmatch(digest) is None for digest in row_digests
    ):
        raise PromotionError("row digest set is malformed or duplicated")
    return hashlib.sha256("\n".join(sorted(row_digests)).encode()).hexdigest()


def _candidate_identity_bindings(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, bool, str | None], list[str]] = defaultdict(list)
    for candidate in candidates:
        schema_version = candidate.get("schema_version")
        data_product_present = "data_product" in candidate
        data_product = candidate.get("data_product")
        if not isinstance(schema_version, str) or not schema_version:
            raise PromotionError("candidate schema_version is invalid")
        if data_product_present and not isinstance(data_product, str):
            raise PromotionError("candidate data_product is invalid")
        grouped[(schema_version, data_product_present, data_product)].append(
            serialized_row_sha256(candidate)
        )
    return [
        {
            "schema_version": schema_version,
            "data_product_present": data_product_present,
            "data_product": data_product,
            "n_rows": len(row_digests),
            "candidate_row_set_sha256": row_digest_set_sha256(row_digests),
        }
        for (
            schema_version,
            data_product_present,
            data_product,
        ), row_digests in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
        )
    ]


def create_candidate_union_report(
    candidates: list[dict[str, Any]],
    release_selection_receipt: dict[str, Any],
    *,
    report_attestation_key: bytes | None,
    candidate_attestation_key: bytes | None,
    selection_attestation_key: bytes | None,
) -> dict[str, Any]:
    """Bind heterogeneous signed candidates under one selected release identity."""
    if (
        report_attestation_key is None
        or candidate_attestation_key is None
        or selection_attestation_key is None
    ):
        raise PromotionError("candidate union attestation keys are incomplete")
    if not candidates:
        raise PromotionError("candidate union is empty")
    if any(
        candidate.get("data_stage") != "candidate"
        or not verify_attestation(
            candidate,
            candidate_attestation_key,
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        )
        for candidate in candidates
    ):
        raise PromotionError("candidate union contains an invalid candidate")
    candidate_digests = [serialized_row_sha256(row) for row in candidates]
    candidate_ids = [candidate_sha256(row) for row in candidates]
    if len(set(candidate_digests)) != len(candidates) or len(set(candidate_ids)) != len(
        candidates
    ):
        raise PromotionError("candidate union contains duplicate candidates")
    if not verify_attestation(
        release_selection_receipt,
        selection_attestation_key,
        purpose=RELEASE_SELECTION_PURPOSE,
    ):
        raise PromotionError("candidate union release selection is invalid")
    release_profile_id = str(release_selection_receipt.get("release_profile_id") or "")
    profile = release_profile(release_profile_id)
    profile_digest = release_profile_sha256(release_profile_id)
    selected_ids = release_selection_receipt.get("selected_candidate_sha256")
    if (
        release_selection_receipt.get("schema_version") != RELEASE_SELECTION_SCHEMA
        or release_selection_receipt.get("release_profile_sha256") != profile_digest
        or release_selection_receipt.get("candidate_row_set_sha256")
        != row_digest_set_sha256(candidate_digests)
        or not isinstance(selected_ids, list)
        or not set(selected_ids).issubset(candidate_ids)
        or release_selection_receipt.get("n_selected_worlds")
        != profile.expected_promoted_worlds
    ):
        raise PromotionError("candidate union release selection binding is invalid")
    identity_bindings = _candidate_identity_bindings(candidates)
    worlds = {str(candidate.get("world_id") or "") for candidate in candidates}
    worlds.discard("")
    if not worlds:
        raise PromotionError("candidate union has no world identity")
    payload: dict[str, Any] = {
        "schema_version": CANDIDATE_UNION_REPORT_SCHEMA,
        "data_product": RELEASE_UNION_DATA_PRODUCT,
        "data_stage": "candidate",
        "release_profile_id": release_profile_id,
        "release_profile_sha256": profile_digest,
        "release_selection_sha256": serialized_row_sha256(release_selection_receipt),
        "n_worlds": len(worlds),
        "n_rows": len(candidates),
        "target_promoted_worlds": profile.expected_promoted_worlds,
        "candidate_row_set_sha256": row_digest_set_sha256(candidate_digests),
        "candidate_identity_bindings": identity_bindings,
        "candidate_identity_bindings_sha256": _canonical_sha256(identity_bindings),
        "retention": 1.0,
        "n_clones": 0,
    }
    payload.update(local_probe_diagnostic_metadata())
    return attach_attestation(
        payload, report_attestation_key, purpose=QUALITY_REPORT_PURPOSE
    )


def candidate_sha256(candidate: dict[str, Any]) -> str:
    """Bind every serialized candidate field except an existing attestation."""
    return hashlib.sha256(canonical_attested_payload(candidate)).hexdigest()


def promoted_row_set_sha256(rows: list[dict[str, Any]]) -> str:
    """Bind an unordered promoted row set, including every row attestation."""
    return row_digest_set_sha256([serialized_row_sha256(row) for row in rows])


def promoted_split_row_set_sha256(rows: list[dict[str, Any]], split: str) -> str:
    """Bind one physical split, including the valid empty-set case."""
    digests = sorted(
        serialized_row_sha256(row) for row in rows if row.get("split") == split
    )
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()


def _candidate_has_verified_real_source(candidate: dict[str, Any]) -> bool:
    classifications = candidate.get("artifact_classification")
    return bool(
        candidate.get("source_family_ids")
        and any(
            isinstance(candidate.get(field), dict)
            for field in (
                "episode_replay_bundle",
                "source_workflow_bundle",
                "task_replay_sidecar",
            )
        )
        and isinstance(classifications, list)
        and any(
            isinstance(item, dict)
            and item.get("source_origin")
            in {"real_public", "real_private_export", "real_derived"}
            and item.get("workflow_kind") in {"hybrid_causal", "real_source_derived"}
            for item in classifications
        )
        and _counterfactual_parent_source_binding_valid(candidate)
    )


def _candidate_has_source_bound_proof(candidate: dict[str, Any]) -> bool:
    classifications = candidate.get("artifact_classification")
    return bool(
        candidate.get("source_family_ids")
        and any(
            isinstance(candidate.get(field), dict)
            for field in (
                "episode_replay_bundle",
                "source_workflow_bundle",
                "task_replay_sidecar",
            )
        )
        and isinstance(classifications, list)
        and any(
            isinstance(item, dict)
            and item.get("source_origin")
            in {"real_public", "real_private_export", "real_derived"}
            and item.get("workflow_kind") in {"hybrid_causal", "real_source_derived"}
            and item.get("evidence_role") in {"causal_gold", "causal_supporting"}
            for item in classifications
        )
        and _counterfactual_parent_source_binding_valid(candidate)
    )


def _counterfactual_parent_source_binding_valid(candidate: dict[str, Any]) -> bool:
    classifications = candidate.get("artifact_classification")
    if not isinstance(classifications, list):
        return False
    synthetic = [
        item
        for item in classifications
        if isinstance(item, dict)
        and item.get("source_origin") == "synthetic_counterfactual"
    ]
    if not synthetic:
        return True
    projection = candidate.get("task_view_projection")
    if (
        candidate.get("view") != "cf"
        or len(synthetic) != 1
        or not isinstance(projection, dict)
        or projection.get("view") != "cf"
        or not isinstance(projection.get("parent_task_replay_sidecar"), dict)
        or _SHA256.fullmatch(
            str(projection["parent_task_replay_sidecar"].get("sha256") or "")
        )
        is None
        or _SHA256.fullmatch(str(projection.get("parent_source_binding_sha256") or ""))
        is None
    ):
        return False
    parent_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in projection.get("parent_artifact_bindings") or []
        if isinstance(item, dict)
    }
    changed = synthetic[0]
    parent = parent_by_id.get(str(changed.get("artifact_id") or ""))
    return bool(
        isinstance(parent, dict)
        and parent.get("source_origin")
        in {"real_public", "real_private_export", "real_derived"}
        and changed.get("counterfactual_parent_source_origin")
        == parent.get("source_origin")
        and changed.get("counterfactual_parent_provenance_id")
        == parent.get("provenance_id")
        and changed.get("counterfactual_parent_text_sha256")
        == parent.get("text_sha256")
        and changed.get("counterfactual_parent_source_binding_sha256")
        == projection.get("parent_source_binding_sha256")
        and changed.get("counterfactual_parent_sidecar_sha256")
        == projection["parent_task_replay_sidecar"].get("sha256")
    )


def _candidate_world_domains(candidates: list[dict[str, Any]]) -> dict[str, str]:
    domains_by_world: dict[str, str] = {}
    for candidate in candidates:
        world_id = str(candidate.get("world_id") or "")
        domain = str(candidate.get("domain") or "")
        if not world_id or not domain:
            raise PromotionError("domain-quota candidate identity is incomplete")
        previous = domains_by_world.setdefault(world_id, domain)
        if previous != domain:
            raise PromotionError(f"world {world_id} spans multiple domains")
    return domains_by_world


def _audited_near_dup_sentence_ratio(audit: dict[str, Any]) -> float | None:
    if "near_dup_sentence_ratio" not in audit:
        return None
    try:
        ratio = float(audit["near_dup_sentence_ratio"])
    except (TypeError, ValueError):
        return None
    return ratio if math.isfinite(ratio) and 0.0 <= ratio <= 1.0 else None


def _missing_required_exact_length_buckets_by_world(
    rows: list[dict[str, Any]], profile: Any
) -> dict[str, tuple[str, ...]]:
    required = profile.required_exact_length_buckets
    if not required:
        return {}
    if len(set(required)) != len(required) or any(
        bucket not in EXACT_TOKEN_BAND_RANGES for bucket in required
    ):
        raise PromotionError(
            "release profile required exact length buckets are invalid"
        )
    observed: dict[str, set[str]] = {}
    for row in rows:
        world_id = str(row.get("world_id") or "")
        bucket = str(row.get("length_bucket") or "")
        tokens = row.get("tokenizer_context_tokens")
        if (
            not world_id
            or bucket not in required
            or not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or exact_token_band_reject_reason(bucket, tokens) is not None
            or row.get("tokenizer_model_id") != profile.tokenizer_model_id
            or row.get("tokenizer_revision") != profile.tokenizer_revision
            or (
                profile.tokenizer_asset_manifest_sha256
                and row.get("tokenizer_asset_manifest_sha256")
                != profile.tokenizer_asset_manifest_sha256
            )
        ):
            continue
        observed.setdefault(world_id, set()).add(bucket)
    worlds = {str(row.get("world_id") or "") for row in rows}
    worlds.discard("")
    return {
        world_id: tuple(
            bucket for bucket in required if bucket not in observed.get(world_id, set())
        )
        for world_id in sorted(worlds)
        if not set(required).issubset(observed.get(world_id, set()))
    }


def _missing_required_view_coverage_by_world(
    rows: list[dict[str, Any]], profile: Any
) -> dict[str, tuple[str, ...]]:
    required_views = tuple(profile.required_view_timings)
    required_buckets = tuple(profile.required_exact_length_buckets)
    if not required_views:
        return {}
    if (
        not required_buckets
        or len(set(required_views)) != len(required_views)
        or any(
            view not in _COMPOSITION_BY_VIEW or timing not in {"first", "late"}
            for view, timing in required_views
        )
    ):
        raise PromotionError("release profile required view coverage is invalid")
    required = {
        (bucket, view, timing)
        for bucket in required_buckets
        for view, timing in required_views
    }
    observed: defaultdict[str, set[tuple[str, str, str]]] = defaultdict(set)
    worlds: set[str] = set()
    for row in rows:
        world_id = str(row.get("world_id") or "")
        if not world_id:
            continue
        worlds.add(world_id)
        bucket = str(row.get("length_bucket") or "")
        tokens = row.get("tokenizer_context_tokens")
        key = (
            bucket,
            str(row.get("view") or ""),
            str(row.get("query_timing") or ""),
        )
        if (
            key not in required
            or not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or exact_token_band_reject_reason(bucket, tokens) is not None
            or row.get("tokenizer_model_id") != profile.tokenizer_model_id
            or row.get("tokenizer_revision") != profile.tokenizer_revision
            or (
                profile.tokenizer_asset_manifest_sha256
                and row.get("tokenizer_asset_manifest_sha256")
                != profile.tokenizer_asset_manifest_sha256
            )
        ):
            continue
        observed[world_id].add(key)
    return {
        world_id: tuple(
            f"{bucket}:{view}/{timing}"
            for bucket, view, timing in sorted(required - observed[world_id])
        )
        for world_id in sorted(worlds)
        if not required.issubset(observed[world_id])
    }


def _immutable_world_lineage_violations_by_world(
    rows: list[dict[str, Any]], profile: Any
) -> dict[str, tuple[str, ...]]:
    """Reject mixed-source or duplicate cells in fixed multi-view profiles."""
    required_views = tuple(profile.required_view_timings)
    required_buckets = tuple(profile.required_exact_length_buckets)
    if not required_views:
        return {}
    required_cells = {
        (bucket, view, timing)
        for bucket in required_buckets
        for view, timing in required_views
    }
    by_world: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        world_id = str(row.get("world_id") or "")
        if world_id:
            by_world[world_id].append(row)

    def source_lineage(row: dict[str, Any]) -> str:
        for field in (
            "source_binding",
            "source_workflow_bundle",
            "episode_replay_bundle",
        ):
            value = row.get(field)
            if isinstance(value, dict):
                return _canonical_sha256({"binding_type": field, "binding": value})
        return ""

    violations: dict[str, tuple[str, ...]] = {}
    for world_id, world_rows in sorted(by_world.items()):
        reasons: list[str] = []
        cell_counts = Counter(
            (
                str(row.get("length_bucket") or ""),
                str(row.get("view") or ""),
                str(row.get("query_timing") or ""),
            )
            for row in world_rows
        )
        duplicated = sorted(
            cell
            for cell, count in cell_counts.items()
            if cell in required_cells and count != 1
        )
        if duplicated:
            reasons.append(
                "duplicate_cells="
                + "+".join(
                    f"{bucket}:{view}/{timing}" for bucket, view, timing in duplicated
                )
            )
        for field in ("base_task_id",):
            values = {str(row.get(field) or "") for row in world_rows if row.get(field)}
            if len(values) > 1:
                reasons.append(f"mixed_{field}")
        for field in ("source_family_ids", "workflow_ids"):
            values = {
                _canonical_sha256(sorted(str(value) for value in row.get(field) or []))
                for row in world_rows
            }
            if len(values) > 1:
                reasons.append(f"mixed_{field}")
        source_lineages = {source_lineage(row) for row in world_rows}
        if "" in source_lineages or len(source_lineages) > 1:
            reasons.append("mixed_source_binding")
        if reasons:
            violations[world_id] = tuple(reasons)
    return violations


def _worlds_with_unbound_rows(rows: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    unbound: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows:
        if _candidate_has_source_bound_proof(row):
            continue
        world_id = str(row.get("world_id") or "")
        unbound[world_id].append(str(row.get("query_id") or "?"))
    return {
        world_id: tuple(sorted(query_ids))
        for world_id, query_ids in sorted(unbound.items())
    }


def _cumulative_history_violations_by_world(
    rows: list[dict[str, Any]], profile: Any
) -> dict[str, tuple[str, ...]]:
    required = tuple(profile.required_exact_length_buckets)
    if len(required) < 2:
        return {}
    groups: defaultdict[tuple[str, str, str, str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    violations: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        group_id = str(row.get("semantic_growth_group_id") or "")
        bucket = str(row.get("length_bucket") or "")
        if bucket not in required or not _candidate_has_source_bound_proof(row):
            continue
        world_id = str(row.get("world_id") or "")
        if not group_id:
            violations[world_id].add("missing_semantic_growth_group")
            continue
        groups[
            (
                world_id,
                group_id,
                str(row.get("query_timing") or ""),
                str(row.get("view") or ""),
                str(row.get("split") or ""),
            )
        ].append(row)

    for (world_id, group_id, _timing, view, _split), group in groups.items():
        rows_by_bucket: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in group:
            rows_by_bucket[str(row["length_bucket"])].append(row)
        missing = [bucket for bucket in required if not rows_by_bucket[bucket]]
        if missing:
            violations[world_id].add(
                f"{group_id}:{view}:missing_buckets={'+'.join(missing)}"
            )
            continue

        def metrics(row: dict[str, Any]) -> tuple[int, int, int, int, int, int] | None:
            semantic = row.get("semantic_tokens")
            graph = row.get("graph")
            relations = row.get("authentic_source_relation_edges")
            if (
                not isinstance(semantic, dict)
                or not isinstance(graph, dict)
                or not isinstance(relations, list)
            ):
                return None
            raw_values = (
                semantic.get("event_bearing"),
                semantic.get("internal"),
                row.get("strict_support_event_count"),
                graph.get("n_essential_events"),
                graph.get("proof_depth"),
            )
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in raw_values
            ):
                return None
            event_bearing, internal, support, essential, proof_depth = raw_values
            if cast(int, event_bearing) > cast(int, internal):
                return None
            return (
                cast(int, event_bearing),
                cast(int, internal),
                cast(int, support),
                cast(int, essential),
                len(relations),
                cast(int, proof_depth),
            )

        def relation_ids(row: dict[str, Any]) -> set[str] | None:
            relations = row.get("authentic_source_relation_edges")
            if not isinstance(relations, list) or any(
                not _strict_source_relation_valid(relation) for relation in relations
            ):
                return None
            return {_canonical_sha256(relation) for relation in relations}

        metric_names = (
            "event_bearing",
            "internal",
            "strict_support",
            "essential_events",
            "authentic_relations",
            "proof_depth",
        )
        for before_bucket, after_bucket in pairwise(required):
            before_metrics = [metrics(row) for row in rows_by_bucket[before_bucket]]
            after_metrics = [metrics(row) for row in rows_by_bucket[after_bucket]]
            label = f"{group_id}:{view}:{before_bucket}->{after_bucket}"
            if any(value is None for value in (*before_metrics, *after_metrics)):
                violations[world_id].add(f"{label}:invalid_growth_metrics")
                continue
            for index, metric_name in enumerate(metric_names):
                before_max = max(value[index] for value in before_metrics if value)
                after_min = min(value[index] for value in after_metrics if value)
                if after_min <= before_max:
                    violations[world_id].add(
                        f"{label}:{metric_name}_not_growing={before_max}->{after_min}"
                    )
            for before in rows_by_bucket[before_bucket]:
                before_relations = relation_ids(before)
                for after in rows_by_bucket[after_bucket]:
                    after_relations = relation_ids(after)
                    if (
                        before_relations is None
                        or after_relations is None
                        or not before_relations < after_relations
                    ):
                        violations[world_id].add(
                            f"{label}:authentic_relation_history_not_nested"
                        )

    return {
        world_id: tuple(sorted(items))
        for world_id, items in sorted(violations.items())
        if items
    }


def candidate_structural_preflight(
    candidates: list[dict[str, Any]],
    release_profile_id: str,
    *,
    candidate_attestation_key: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reject whole worlds that cannot satisfy the profile's exact token bands."""
    profile = release_profile(release_profile_id)
    seen: set[str] = set()
    for candidate in candidates:
        if not verify_attestation(
            candidate,
            candidate_attestation_key,
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        ):
            raise PromotionError(
                "candidate structural preflight attestation is invalid"
            )
        if not str(candidate.get("world_id") or ""):
            raise PromotionError("candidate structural preflight world id is missing")
        digest = candidate_sha256(candidate)
        if digest in seen:
            raise PromotionError("candidate structural preflight input is duplicated")
        seen.add(digest)

    task_candidate_digests: set[str] = set()
    task_content_identities: set[tuple[str, ...]] = set()
    task_training_content_digests: set[str] = set()
    task_answers_by_prompt: dict[str, str] = {}
    for candidate in candidates:
        binding = candidate.get("task_replay_sidecar")
        if binding is None:
            continue
        try:
            identity = _task_candidate_content_identity(candidate)
        except PromotionError as error:
            raise PromotionError(
                "candidate structural preflight task replay sidecar is invalid"
            ) from error
        assert identity is not None
        if identity in task_content_identities:
            raise PromotionError(
                "candidate structural preflight task content identity is duplicated"
            )
        task_content_identities.add(identity)
        training_digest, prompt_digest, answer = _task_training_content_identity(
            candidate
        )
        if training_digest in task_training_content_digests:
            raise PromotionError(
                "candidate structural preflight task training content is duplicated"
            )
        task_training_content_digests.add(training_digest)
        previous_answer = task_answers_by_prompt.setdefault(prompt_digest, answer)
        if previous_answer != answer:
            raise PromotionError(
                "candidate structural preflight task prompt has conflicting answers"
            )
        task_candidate_digests.add(candidate_sha256(candidate))

    missing_by_world = _missing_required_exact_length_buckets_by_world(
        candidates, profile
    )
    # Task-adapter growth is authoritative only after signed strict replay.
    # It is checked again in ``select_release_worlds`` with auditor metrics.
    cumulative_violations = _cumulative_history_violations_by_world(
        [
            candidate
            for candidate in candidates
            if candidate_sha256(candidate) not in task_candidate_digests
        ],
        profile,
    )
    rejected_worlds = set(missing_by_world) | set(cumulative_violations)
    profile_digest = release_profile_sha256(release_profile_id)
    accepted = [
        candidate
        for candidate in candidates
        if str(candidate["world_id"]) not in rejected_worlds
    ]
    rejects = []
    for candidate in candidates:
        world_id = str(candidate["world_id"])
        if world_id not in rejected_worlds:
            continue
        missing = missing_by_world.get(world_id, ())
        cumulative = cumulative_violations.get(world_id, ())
        reject: dict[str, Any] = {
            "schema_version": "candidate-structural-reject-v1",
            "candidate_sha256": candidate_sha256(candidate),
            "world_id": world_id,
            "query_id": str(candidate.get("query_id") or ""),
            "release_profile_id": release_profile_id,
            "release_profile_sha256": profile_digest,
        }
        if missing:
            reject["missing_exact_length_buckets"] = missing
            reject["reason"] = "missing required exact length buckets: " + "+".join(
                missing
            )
        else:
            reject["cumulative_history_violations"] = cumulative
            reject["reason"] = "invalid cumulative source history: " + ";".join(
                cumulative
            )
        rejects.append(reject)
    return accepted, rejects


def _task_sidecar_matches_candidate(
    candidate: dict[str, Any], binding: dict[str, Any]
) -> bool:
    key = (
        str(binding.get("adapter_id") or ""),
        str(binding.get("adapter_revision") or ""),
        str(binding.get("sidecar_schema_version") or ""),
    )
    if key[2] == TASK_REPLAY_SIDECAR_SCHEMA_V3:
        projection = candidate.get("task_view_projection")
        view = str(candidate.get("view") or "")
        expected_domains = {
            "cyber.kev_history.v1": "cyber",
            "finance.multi_filing.v1": "finance",
            "macro.gdp_vintage_reconstruction.v1": "macro_economics",
        }
        return bool(
            key[0] in expected_domains
            and candidate.get("domain") == expected_domains[key[0]]
            and view in {"full", "cf", "ordered_artifact_view"}
            and candidate.get("composition_method") == _COMPOSITION_BY_VIEW[view]
            and candidate.get("strict_replay_revision") == key[1]
            and isinstance(projection, dict)
            and projection.get("schema_version") == "longworld.task-view-projection.v1"
            and projection.get("derivation_revision") == TASK_VIEW_DERIVATION_REVISION
            and projection.get("view") == view
            and projection.get("dossier_id") == candidate.get("dossier_id")
        )
    if candidate.get("task_view_projection") is not None:
        return False
    if key[0] == "cyber.kev_history.v1":
        replay = candidate.get("domain_history_replay_manifest")
        return bool(
            candidate.get("domain") == "cyber"
            and candidate.get("view") == "ordered_artifact_view"
            and candidate.get("composition_method") == "causal_timeline"
            and isinstance(replay, dict)
            and replay.get("replay_revision") == key[1]
            and candidate.get("strict_replay_revision") == key[1]
        )
    if key[0] == "finance.multi_filing.v1":
        replay = candidate.get("finance_replay_contract")
        return bool(
            candidate.get("domain") == "finance"
            and candidate.get("view") == "full"
            and candidate.get("composition_method") == "same_case_dossier"
            and isinstance(replay, dict)
            and replay.get("adapter_id") == key[0]
            and replay.get("revision") == key[1]
            and candidate.get("strict_replay_revision") == key[1]
        )
    if key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
        return bool(
            candidate.get("domain") == "macro_economics"
            and candidate.get("view") == "ordered_release_timeline"
            and candidate.get("composition_method") == "as_of_revision_workflow"
            and candidate.get("schema_version")
            == "longworld.macro-vintage-pipeline-candidate.v1"
            and candidate.get("strict_replay_revision") == key[1]
        )
    return False


def _task_candidate_content_identity(
    candidate: dict[str, Any],
) -> tuple[str, ...] | None:
    binding = candidate.get("task_replay_sidecar")
    if binding is None:
        return None
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        }
        or (
            str(binding.get("adapter_id") or ""),
            str(binding.get("adapter_revision") or ""),
            str(binding.get("sidecar_schema_version") or ""),
        )
        not in TASK_REPLAY_ADAPTER_REGISTRY
        or _SHA256.fullmatch(str(binding.get("sha256") or "")) is None
        or not _task_sidecar_matches_candidate(candidate, binding)
    ):
        raise PromotionError("task replay sidecar binding is invalid")
    try:
        sidecar_schema_version = str(binding["sidecar_schema_version"])
        commitment = task_candidate_content_commitment(
            candidate, sidecar_schema_version=sidecar_schema_version
        )
    except ProvenanceError as error:
        raise PromotionError("task candidate content identity is invalid") from error
    if sidecar_schema_version in {
        TASK_REPLAY_SIDECAR_SCHEMA_V2,
        TASK_REPLAY_SIDECAR_SCHEMA_V3,
    }:
        return (
            str(binding["sha256"]),
            commitment["world_id"],
            commitment["length_bucket"],
            commitment["view"],
            commitment["content_sha256"],
        )
    return (
        str(binding["sha256"]),
        commitment["world_id"],
        commitment["length_bucket"],
        commitment["content_sha256"],
    )


def _task_training_content_identity(
    candidate: dict[str, Any],
) -> tuple[str, str, str]:
    context = candidate.get("context")
    if not isinstance(context, str) or not context:
        raise PromotionError("task candidate training context is invalid")
    answer = str(candidate.get("answer") or "")
    if not answer:
        raise PromotionError("task candidate training answer is invalid")
    return (
        _canonical_sha256({"context": context, "answer": answer}),
        hashlib.sha256(context.encode("utf-8")).hexdigest(),
        answer,
    )


def validate_task_candidate_content_uniqueness(
    candidates: list[dict[str, Any]], *, label: str
) -> None:
    """Reject task-row metadata clones and conflicting exported prompts."""
    source_identities: set[tuple[str, ...]] = set()
    training_digests: set[str] = set()
    answers_by_prompt: dict[str, str] = {}
    for candidate in candidates:
        identity = _task_candidate_content_identity(candidate)
        if identity is None:
            continue
        if identity in source_identities:
            raise PromotionError(f"{label} task content identity is duplicated")
        source_identities.add(identity)
        training_digest, prompt_digest, answer = _task_training_content_identity(
            candidate
        )
        if training_digest in training_digests:
            raise PromotionError(f"{label} task training content is duplicated")
        training_digests.add(training_digest)
        previous_answer = answers_by_prompt.setdefault(prompt_digest, answer)
        if previous_answer != answer:
            raise PromotionError(f"{label} task prompt has conflicting answers")


def _selection_audit_matches_candidate(
    audit: dict[str, Any], candidate: dict[str, Any], dense_top_k: int
) -> bool:
    model = audit.get("model")
    top_k = audit.get("top_k")
    return bool(
        audit.get("schema_version") == PROMOTION_SCHEMA
        and audit.get("query_id") == candidate.get("query_id")
        and audit.get("candidate_sha256") == candidate_sha256(candidate)
        and audit.get("ranker_type") == "dense_embedding"
        and audit.get("k") == dense_top_k
        and isinstance(top_k, list)
        and len(top_k) == dense_top_k
        and audit.get("strict_replay_revision") == STRICT_REPLAY_REVISION
        and audit.get("expected_answer") == candidate.get("answer")
        and audit.get("embedding_topk_insufficient") is True
        and _audited_near_dup_sentence_ratio(audit) is not None
        and _SHA256.fullmatch(str(audit.get("ranking_sha256") or "")) is not None
        and _SHA256.fullmatch(str(audit.get("verification_replay_sha256") or ""))
        is not None
        and _strict_growth_metrics_are_valid(audit.get("strict_growth_metrics"))
        and _selection_task_proof_is_closed(audit, candidate)
        and _selection_task_semantic_commitment_is_closed(audit, candidate)
        and (
            str(candidate.get("length_bucket") or "") not in EXACT_TOKEN_BAND_RANGES
            or (
                _SHA256.fullmatch(
                    str(candidate.get("tokenizer_asset_manifest_sha256") or "")
                )
                is not None
                and audit.get("tokenizer_asset_manifest_sha256")
                == candidate.get("tokenizer_asset_manifest_sha256")
            )
        )
        and isinstance(model, dict)
        and tuple(
            str(model.get(field) or "")
            for field in (
                "provider",
                "model_id",
                "revision",
                "backend",
                "score_metric",
            )
        )
        in _APPROVED_DENSE_MODELS
        and model.get("chunking")
        == {
            "strategy": "tokenizer_token_windows",
            "max_tokens": 192,
            "overlap_tokens": 32,
            "aggregation": "max_similarity",
        }
    )


def _selection_task_proof_is_closed(
    audit: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    if not isinstance(candidate.get("task_replay_sidecar"), dict):
        return True
    task_proof = audit.get("task_proof")
    if not isinstance(task_proof, dict):
        return False
    try:
        verification = Verification.model_validate(task_proof.get("verification"))
    except ValueError:
        return False
    return verification.all_green()


def _strict_growth_metrics_are_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "semantic_growth_group_id",
        "semantic_tokens",
        "strict_support_event_count",
        "graph",
        "authentic_source_relation_edges",
    }:
        return False
    semantic = value.get("semantic_tokens")
    graph = value.get("graph")
    relations = value.get("authentic_source_relation_edges")
    integers = (
        value.get("strict_support_event_count"),
        semantic.get("event_bearing") if isinstance(semantic, dict) else None,
        semantic.get("internal") if isinstance(semantic, dict) else None,
        graph.get("n_essential_events") if isinstance(graph, dict) else None,
        graph.get("proof_depth") if isinstance(graph, dict) else None,
    )
    return bool(
        isinstance(value.get("semantic_growth_group_id"), str)
        and isinstance(semantic, dict)
        and isinstance(graph, dict)
        and isinstance(relations, list)
        and all(_strict_source_relation_valid(item) for item in relations)
        and all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in integers
        )
        and semantic["event_bearing"] <= semantic["internal"]
    )


_TASK_SEMANTIC_TOKEN_FIELDS = frozenset(
    {
        "internal",
        "event_bearing",
        "proof_bearing",
        "causal_supporting",
        "generic_background",
        "measurement_basis",
    }
)
_TASK_SEMANTIC_GRAPH_FIELDS = frozenset(
    {
        "n_essential_events",
        "n_essential_artifacts",
        "proof_depth",
        "hop_count",
    }
)
_TASK_SEMANTIC_VIEW_FIELDS = (
    "expected_answer",
    "strict_replay_answer",
    "essential_present",
    "semantic_text_grounded",
    "classification_ok",
    "global_proof_green",
)
_TASK_QUALITY_METADATA_FIELDS = (
    "world_id",
    "domain",
    "length_bucket",
    "motif",
    "base_task_id",
    "executable_proof_id",
    "answer_program_id",
    "semantic_base_task_id",
    "real_source_verified",
    "real_source_family_ids",
    "real_source_workflow_ids",
    "real_source_token_ratio",
    "source_relation_edges",
    "source_relation_id",
    "authentic_source_relation_id",
    "hybrid_causal_edges",
    "context_source_relation_count",
)
_TASK_QUALITY_OPTIONAL_ID_FIELDS = frozenset(
    {
        "motif",
        "base_task_id",
        "executable_proof_id",
        "answer_program_id",
        "semantic_base_task_id",
    }
)
_TASK_QUALITY_REQUIRED_ID_FIELDS = frozenset(
    {
        "world_id",
        "domain",
        "length_bucket",
    }
)


def _task_semantic_growth_commitment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "semantic_growth_group_id",
        "semantic_tokens",
        "strict_support_event_count",
        "graph",
        "authentic_source_relation_edges",
    }:
        raise PromotionError("task semantic growth commitment is invalid")
    semantic = value.get("semantic_tokens")
    graph = value.get("graph")
    if (
        not isinstance(semantic, dict)
        or set(semantic) != _TASK_SEMANTIC_TOKEN_FIELDS
        or not isinstance(graph, dict)
        or set(graph) != _TASK_SEMANTIC_GRAPH_FIELDS
        or any(
            isinstance(semantic.get(field), bool)
            or not isinstance(semantic.get(field), int)
            or semantic[field] < 0
            for field in _TASK_SEMANTIC_TOKEN_FIELDS - {"measurement_basis"}
        )
        or not isinstance(semantic.get("measurement_basis"), str)
        or not semantic["measurement_basis"]
        or any(
            isinstance(graph.get(field), bool)
            or not isinstance(graph.get(field), int)
            or graph[field] < 0
            for field in _TASK_SEMANTIC_GRAPH_FIELDS
        )
        or not _strict_growth_metrics_are_valid(value)
    ):
        raise PromotionError("task semantic growth commitment is invalid")
    return {
        "semantic_growth_group_id": value["semantic_growth_group_id"],
        "semantic_tokens": dict(semantic),
        "strict_support_event_count": value["strict_support_event_count"],
        "graph": dict(graph),
        "authentic_source_relation_edges": copy.deepcopy(
            value["authentic_source_relation_edges"]
        ),
    }


def _task_semantic_proof_commitment(
    proof: Any, *, normalize_audit_verification: bool
) -> dict[str, Any]:
    if not isinstance(proof, dict):
        raise PromotionError("task semantic proof commitment is invalid")
    receipt = proof.get("task_proof_receipt")
    raw_verification = proof.get("verification")
    view = proof.get("view_verification")
    allowed_view_fields = {
        *_TASK_SEMANTIC_VIEW_FIELDS,
        "production_eligible",
        "content_gate_eligible",
    }
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != TASK_PROOF_RECEIPT_SCHEMA
    ):
        raise PromotionError("task proof receipt schema is not current")
    if (
        not isinstance(raw_verification, dict)
        or set(raw_verification) != set(Verification.model_fields)
        or not isinstance(view, dict)
        or not set(_TASK_SEMANTIC_VIEW_FIELDS) <= set(view)
        or any(key not in allowed_view_fields for key in view)
        or any(
            not isinstance(view[field], str) or not view[field]
            for field in _TASK_SEMANTIC_VIEW_FIELDS[:2]
        )
        or any(
            not isinstance(view[field], bool)
            for field in _TASK_SEMANTIC_VIEW_FIELDS[2:]
        )
    ):
        raise PromotionError("task semantic proof commitment is invalid")
    try:
        verification = Verification.model_validate(raw_verification)
    except ValueError as error:
        raise PromotionError("task semantic proof commitment is invalid") from error
    if normalize_audit_verification:
        verification.production_mode = True
        verification.candidate_mode = False
        verification.embedding_topk_insufficient = True
    return {
        "task_proof_receipt": copy.deepcopy(receipt),
        "verification": verification.model_dump(),
        "view_verification": {
            field: copy.deepcopy(view[field]) for field in _TASK_SEMANTIC_VIEW_FIELDS
        },
    }


def _task_quality_metadata_commitment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_TASK_QUALITY_METADATA_FIELDS):
        raise PromotionError("task quality metadata commitment is invalid")
    if any(
        item is not None and (not isinstance(item, str) or not item)
        for item in (value.get(field) for field in _TASK_QUALITY_OPTIONAL_ID_FIELDS)
    ) or any(
        not isinstance(value.get(field), str) or not value[field]
        for field in _TASK_QUALITY_REQUIRED_ID_FIELDS
    ):
        raise PromotionError("task quality metadata commitment is invalid")
    families = value.get("real_source_family_ids")
    workflows = value.get("real_source_workflow_ids")
    source_relations = value.get("source_relation_edges")
    hybrid_relations = value.get("hybrid_causal_edges")
    ratio = value.get("real_source_token_ratio")
    relation_count = value.get("context_source_relation_count")
    if (
        value.get("real_source_verified") is not True
        or not isinstance(families, list)
        or not families
        or any(not isinstance(item, str) or not item for item in families)
        or families != sorted(set(families))
        or not isinstance(workflows, list)
        or not workflows
        or any(not isinstance(item, str) or not item for item in workflows)
        or workflows != sorted(set(workflows))
        or not isinstance(ratio, (int, float))
        or isinstance(ratio, bool)
        or not math.isfinite(float(ratio))
        or not 0.0 <= float(ratio) <= 1.0
        or not isinstance(source_relations, list)
        or not source_relations
        or any(not _strict_source_relation_valid(item) for item in source_relations)
        or not isinstance(hybrid_relations, list)
        or any(not _strict_source_relation_valid(item) for item in hybrid_relations)
        or not isinstance(relation_count, int)
        or isinstance(relation_count, bool)
        or relation_count != len(source_relations)
        or value.get("source_relation_id") != _canonical_sha256(source_relations)[:20]
        or not isinstance(value.get("authentic_source_relation_id"), str)
        or not value["authentic_source_relation_id"]
    ):
        raise PromotionError("task quality metadata commitment is invalid")
    return {
        field: (
            float(value[field])
            if field == "real_source_token_ratio"
            else copy.deepcopy(value[field])
        )
        for field in _TASK_QUALITY_METADATA_FIELDS
    }


def task_semantic_commitment_sha256_from_audit(audit: dict[str, Any]) -> str:
    """Commit to auditor-owned task semantics in their promoted representation."""
    payload = {
        "schema_version": TASK_SEMANTIC_COMMITMENT_SCHEMA,
        "strict_growth_metrics": _task_semantic_growth_commitment(
            audit.get("strict_growth_metrics")
        ),
        "proof": _task_semantic_proof_commitment(
            audit.get("task_proof"), normalize_audit_verification=True
        ),
        "quality_metadata": _task_quality_metadata_commitment(
            audit.get("task_quality_metadata")
        ),
    }
    return _canonical_sha256(payload)


def task_semantic_commitment_sha256_from_row(row: dict[str, Any]) -> str:
    """Recompute the auditor-owned task semantic commitment from an actual row."""
    growth = {
        "semantic_growth_group_id": row.get("semantic_growth_group_id"),
        "semantic_tokens": row.get("semantic_tokens"),
        "strict_support_event_count": row.get("strict_support_event_count"),
        "graph": row.get("graph"),
        "authentic_source_relation_edges": row.get("authentic_source_relation_edges"),
    }
    proof = {
        "task_proof_receipt": row.get("task_proof_receipt"),
        "verification": row.get("verification"),
        "view_verification": row.get("view_verification"),
    }
    quality_metadata = {
        field: row.get(field) for field in _TASK_QUALITY_METADATA_FIELDS
    }
    payload = {
        "schema_version": TASK_SEMANTIC_COMMITMENT_SCHEMA,
        "strict_growth_metrics": _task_semantic_growth_commitment(growth),
        "proof": _task_semantic_proof_commitment(
            proof, normalize_audit_verification=False
        ),
        "quality_metadata": _task_quality_metadata_commitment(quality_metadata),
    }
    return _canonical_sha256(payload)


def _selection_task_semantic_commitment_is_closed(
    audit: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    if not isinstance(candidate.get("task_replay_sidecar"), dict):
        return True
    try:
        expected = task_semantic_commitment_sha256_from_audit(audit)
    except PromotionError:
        return False
    return audit.get("task_semantic_commitment_sha256") == expected


def _strict_source_relation_valid(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    return bool(
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and all(isinstance(item, str) and item for item in value[:2])
    )


def _strict_growth_metrics(
    replayed_quality: dict[str, Any],
    replayed_task: dict[str, Any],
    replayed_source: dict[str, Any],
) -> dict[str, Any]:
    graph = replayed_task["graph"]
    return {
        "semantic_growth_group_id": replayed_task["semantic_growth_group_id"],
        "semantic_tokens": dict(replayed_quality["semantic_tokens"]),
        "strict_support_event_count": replayed_task["strict_support_event_count"],
        "graph": {
            "n_essential_events": graph["n_essential_events"],
            "proof_depth": graph["proof_depth"],
        },
        "authentic_source_relation_edges": list(
            replayed_source["authentic_source_relation_edges"]
        ),
    }


def _candidate_with_strict_growth(
    candidate: dict[str, Any], audit: dict[str, Any]
) -> dict[str, Any]:
    metrics = audit["strict_growth_metrics"]
    return {
        **candidate,
        "semantic_growth_group_id": metrics["semantic_growth_group_id"],
        "semantic_tokens": metrics["semantic_tokens"],
        "strict_support_event_count": metrics["strict_support_event_count"],
        "graph": metrics["graph"],
        "authentic_source_relation_edges": metrics["authentic_source_relation_edges"],
    }


def validate_predecessor_gate_receipt(
    receipt: dict[str, Any] | None,
    release_profile_id: str,
    *,
    attestation_key: bytes | None,
    key_id: str,
) -> tuple[str, str]:
    """Validate the independently signed, post-quality-gate predecessor proof."""
    profile = release_profile(release_profile_id)
    predecessor_id = profile.predecessor_profile_id
    if predecessor_id is None:
        if receipt is not None:
            raise PromotionError("probe release must not carry a predecessor gate")
        return "", ""
    predecessor = release_profile(predecessor_id)
    if receipt is None or not verify_attestation_identity(
        receipt,
        attestation_key,
        purpose=RELEASE_GATE_PURPOSE,
        role="auditor",
        key_id=key_id,
        environment=predecessor.environment,
    ):
        raise PromotionError("predecessor gate receipt attestation is invalid")
    source_hashes = receipt.get("source_file_sha256")
    report_digest = str(receipt.get("quality_report_sha256") or "")
    revision_supported = (
        receipt.get("gate_revision") == RELEASE_GATE_REVISION
        if release_profile_id.startswith("p10-")
        else release_gate_revision_supported(
            predecessor_id, receipt.get("gate_revision")
        )
    )
    if (
        receipt.get("schema_version") != RELEASE_GATE_SCHEMA
        or not revision_supported
        or receipt.get("release_profile_id") != predecessor_id
        or receipt.get("release_profile_sha256")
        != release_profile_sha256(predecessor_id)
        or receipt.get("predecessor_profile_id") != predecessor.predecessor_profile_id
        or receipt.get("ok") is not True
        or receipt.get("errors") != []
        or receipt.get("n_worlds") != predecessor.expected_promoted_worlds
        or not isinstance(receipt.get("n_rows"), int)
        or int(receipt["n_rows"]) <= 0
        or not isinstance(source_hashes, dict)
        or set(source_hashes) != {"quality_report.json", "train.jsonl", "eval.jsonl"}
        or any(
            _SHA256.fullmatch(str(value or "")) is None
            for value in source_hashes.values()
        )
        or _SHA256.fullmatch(report_digest) is None
        or report_digest != source_hashes.get("quality_report.json")
        or _SHA256.fullmatch(str(receipt.get("metrics_sha256") or "")) is None
    ):
        raise PromotionError("predecessor gate receipt contract is invalid")
    if predecessor.environment == "production":
        assert isinstance(source_hashes, dict)
        try:
            verify_embedded_production_approval_from_env(
                receipt.get("production_approval"),
                release_profile_id=predecessor_id,
                release_profile_sha256=release_profile_sha256(predecessor_id),
                source_file_sha256=source_hashes,
            )
        except (TypeError, ValueError) as error:
            raise PromotionError(
                "predecessor production approval is invalid"
            ) from error
    return serialized_row_sha256(receipt), report_digest


def select_release_worlds(
    candidates: list[dict[str, Any]],
    audits: list[dict[str, Any]],
    release_profile_id: str,
    *,
    candidate_attestation_key: bytes,
    audit_attestation_key: bytes,
    predecessor_gate_receipt: dict[str, Any] | None = None,
    predecessor_gate_attestation_key: bytes | None = None,
    predecessor_gate_key_id: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select complete audited worlds and sign an exact deterministic split map."""
    profile = release_profile(release_profile_id)
    profile_digest = release_profile_sha256(release_profile_id)
    predecessor_gate_digest, predecessor_report_digest = (
        validate_predecessor_gate_receipt(
            predecessor_gate_receipt,
            release_profile_id,
            attestation_key=predecessor_gate_attestation_key,
            key_id=predecessor_gate_key_id,
        )
    )
    if profile.min_train_worlds + profile.min_eval_worlds != (
        profile.expected_promoted_worlds
    ):
        raise PromotionError("release profile split quotas do not cover its target")
    if profile.split_strategy not in {
        "world_atomic_hash_v1",
        "world_atomic_domain_stratified_hash_v1",
    }:
        raise PromotionError("release profile split strategy is unsupported")
    if (
        profile.min_real_train_worlds > profile.min_train_worlds
        or profile.min_real_eval_worlds > profile.min_eval_worlds
    ):
        raise PromotionError("release profile real-world quotas exceed split quotas")
    by_world: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    candidate_digests: list[str] = []
    task_content_identities: set[tuple[str, ...]] = set()
    task_training_content_digests: set[str] = set()
    task_answers_by_prompt: dict[str, str] = {}
    for candidate in candidates:
        if not verify_attestation(
            candidate,
            candidate_attestation_key,
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
        ):
            raise PromotionError("world selection candidate attestation is invalid")
        world_id = str(candidate.get("world_id") or "")
        if not world_id:
            raise PromotionError("world selection candidate has no world id")
        task_content_identity = _task_candidate_content_identity(candidate)
        if task_content_identity is not None:
            if task_content_identity in task_content_identities:
                raise PromotionError(
                    "world selection task content identity is duplicated"
                )
            task_content_identities.add(task_content_identity)
            training_digest, prompt_digest, answer = _task_training_content_identity(
                candidate
            )
            if training_digest in task_training_content_digests:
                raise PromotionError(
                    "world selection task training content is duplicated"
                )
            task_training_content_digests.add(training_digest)
            previous_answer = task_answers_by_prompt.setdefault(prompt_digest, answer)
            if previous_answer != answer:
                raise PromotionError(
                    "world selection task prompt has conflicting answers"
                )
        if str(candidate.get("length_bucket") or "") in EXACT_TOKEN_BAND_RANGES:
            expected_asset_digest = profile.tokenizer_asset_manifest_sha256
            if expected_asset_digest and (
                candidate.get("tokenizer_model_id") != profile.tokenizer_model_id
                or candidate.get("tokenizer_revision") != profile.tokenizer_revision
                or candidate.get("tokenizer_asset_manifest_sha256")
                != expected_asset_digest
            ):
                raise PromotionError(
                    "candidate tokenizer assets do not match release profile"
                )
        digest = candidate_sha256(candidate)
        candidate_digests.append(serialized_row_sha256(candidate))
        by_world.setdefault(world_id, []).append((digest, candidate))
    if len(set(candidate_digests)) != len(candidate_digests):
        raise PromotionError("world selection candidates are duplicated")
    candidates_by_id = {
        digest: candidate
        for world_candidates in by_world.values()
        for digest, candidate in world_candidates
    }

    audits_by_candidate: dict[str, dict[str, Any]] = {}
    for audit in audits:
        if not verify_attestation(
            audit, audit_attestation_key, purpose=DENSE_AUDIT_PURPOSE
        ):
            raise PromotionError("world selection audit attestation is invalid")
        digest = str(audit.get("candidate_sha256") or "")
        if not digest or digest in audits_by_candidate:
            raise PromotionError("world selection audits are malformed or duplicated")
        matched_candidate = candidates_by_id.get(digest)
        if matched_candidate is not None and not _selection_audit_matches_candidate(
            audit, matched_candidate, profile.dense_top_k
        ):
            raise PromotionError("world selection audit contract is invalid")
        audits_by_candidate[digest] = audit
    all_candidate_ids = {
        digest for world in by_world.values() for digest, _candidate in world
    }
    if not set(audits_by_candidate).issubset(all_candidate_ids):
        raise PromotionError("world selection audit does not belong to candidate set")
    fully_audited_worlds = [
        world_id
        for world_id, world_candidates in by_world.items()
        if all(
            digest in audits_by_candidate
            and (
                audited_ratio := _audited_near_dup_sentence_ratio(
                    audits_by_candidate[digest]
                )
            )
            is not None
            and audited_ratio <= profile.max_near_dup_sentence_ratio
            for digest, _candidate in world_candidates
        )
    ]
    missing_required_buckets = _missing_required_exact_length_buckets_by_world(
        [
            candidate
            for world_id in fully_audited_worlds
            for _digest, candidate in by_world[world_id]
        ],
        profile,
    )
    missing_required_views = _missing_required_view_coverage_by_world(
        [
            candidate
            for world_id in fully_audited_worlds
            if world_id not in missing_required_buckets
            for _digest, candidate in by_world[world_id]
        ],
        profile,
    )
    lineage_violations = _immutable_world_lineage_violations_by_world(
        [
            candidate
            for world_id in fully_audited_worlds
            if world_id not in missing_required_buckets
            and world_id not in missing_required_views
            for _digest, candidate in by_world[world_id]
        ],
        profile,
    )
    unbound_rows_by_world = (
        _worlds_with_unbound_rows(
            [
                candidate
                for world_id in fully_audited_worlds
                if world_id not in missing_required_buckets
                and world_id not in missing_required_views
                for _digest, candidate in by_world[world_id]
            ]
        )
        if profile.require_all_rows_source_bound
        else {}
    )
    strict_growth_violations = _cumulative_history_violations_by_world(
        [
            _candidate_with_strict_growth(candidate, audits_by_candidate[digest])
            for world_id in fully_audited_worlds
            if world_id not in missing_required_buckets
            and world_id not in missing_required_views
            and world_id not in lineage_violations
            and world_id not in unbound_rows_by_world
            for digest, candidate in by_world[world_id]
        ],
        profile,
    )
    fully_audited_worlds = [
        world_id
        for world_id in fully_audited_worlds
        if world_id not in strict_growth_violations
    ]
    eligible_worlds = [
        world_id
        for world_id in fully_audited_worlds
        if world_id not in missing_required_buckets
        and world_id not in missing_required_views
        and world_id not in lineage_violations
        and world_id not in unbound_rows_by_world
    ]
    target = profile.expected_promoted_worlds
    if len(eligible_worlds) < target:
        if strict_growth_violations:
            details = ",".join(
                f"{world_id}={';'.join(violations)}"
                for world_id, violations in strict_growth_violations.items()
            )
            raise PromotionError(
                "insufficient worlds after strict replay cumulative history: " + details
            )
        if missing_required_buckets:
            details = ",".join(
                f"{world_id}={'+'.join(missing)}"
                for world_id, missing in missing_required_buckets.items()
            )
            raise PromotionError(
                "insufficient fully audited worlds with required exact length buckets: "
                + details
            )
        if missing_required_views:
            details = ",".join(
                f"{world_id}={'+'.join(missing)}"
                for world_id, missing in missing_required_views.items()
            )
            raise PromotionError(
                "insufficient fully audited worlds with required view coverage: "
                + details
            )
        if unbound_rows_by_world:
            details = ",".join(
                f"{world_id}={len(query_ids)}"
                for world_id, query_ids in unbound_rows_by_world.items()
            )
            raise PromotionError(
                "release profile requires all rows to be source-bound: " + details
            )
        if lineage_violations:
            details = ",".join(
                f"{world_id}={'+'.join(reasons)}"
                for world_id, reasons in lineage_violations.items()
            )
            raise PromotionError(
                "insufficient worlds with immutable world lineage: " + details
            )
        raise PromotionError(
            f"insufficient fully audited worlds: {len(eligible_worlds)}<{target}"
        )
    real_eligible_worlds = [
        world_id
        for world_id in eligible_worlds
        if any(
            _candidate_has_source_bound_proof(candidate)
            for _digest, candidate in by_world[world_id]
        )
    ]
    real_exact_64k_eligible_worlds = [
        world_id
        for world_id in real_eligible_worlds
        if any(
            _candidate_has_source_bound_proof(candidate)
            and candidate.get("length_bucket") == "64k"
            and isinstance(candidate.get("tokenizer_context_tokens"), int)
            and not isinstance(candidate.get("tokenizer_context_tokens"), bool)
            and exact_token_band_reject_reason(
                "64k", int(candidate["tokenizer_context_tokens"])
            )
            is None
            and candidate.get("tokenizer_model_id") == profile.tokenizer_model_id
            and candidate.get("tokenizer_revision") == profile.tokenizer_revision
            for _digest, candidate in by_world[world_id]
        )
    ]
    min_real_worlds = profile.min_real_train_worlds + profile.min_real_eval_worlds
    if len(real_eligible_worlds) < min_real_worlds:
        raise PromotionError(
            "insufficient fully audited real workflow worlds: "
            f"{len(real_eligible_worlds)}<{min_real_worlds}"
        )
    select_key = lambda world_id: _sha256_text(f"{profile_digest}|select|{world_id}")
    required_real_worlds: list[str] = []
    domain_quotas = dict(profile.promoted_domain_world_quotas)
    selected_worlds_by_domain: dict[str, list[str]] = {}
    if domain_quotas:
        if (
            len(domain_quotas) != len(profile.promoted_domain_world_quotas)
            or any(not domain or quota < 1 for domain, quota in domain_quotas.items())
            or sum(domain_quotas.values()) != target
            or len(domain_quotas) < profile.min_domains
        ):
            raise PromotionError("release profile domain quotas are invalid")
        domains_by_world = _candidate_world_domains(
            [
                candidate
                for world_candidates in by_world.values()
                for _digest, candidate in world_candidates
            ]
        )
        required_real_minima = dict(profile.min_real_exact_64k_worlds_by_domain)
        if len(required_real_minima) != len(
            profile.min_real_exact_64k_worlds_by_domain
        ) or any(
            domain not in domain_quotas or minimum < 0
            for domain, minimum in required_real_minima.items()
        ):
            raise PromotionError("release profile real domain quotas are invalid")
        required_real_set = set(required_real_worlds)
        for domain, domain_quota in domain_quotas.items():
            domain_real_worlds = sorted(
                (
                    world_id
                    for world_id in real_eligible_worlds
                    if domains_by_world[world_id] == domain
                ),
                key=select_key,
            )
            exact_minimum = required_real_minima.get(domain)
            minimum = (
                exact_minimum
                if exact_minimum is not None
                else 1
                if domain_real_worlds
                else 0
            )
            domain_required_real_worlds = (
                sorted(
                    (
                        world_id
                        for world_id in real_exact_64k_eligible_worlds
                        if domains_by_world[world_id] == domain
                    ),
                    key=select_key,
                )
                if exact_minimum is not None and exact_minimum > 0
                else domain_real_worlds
            )
            if minimum > domain_quota:
                raise PromotionError(
                    f"real workflow requirement exceeds {domain} world quota"
                )
            if len(domain_required_real_worlds) < minimum:
                raise PromotionError(
                    "insufficient fully audited real exact-64K domain worlds: "
                    f"{domain} {len(domain_required_real_worlds)}<{minimum}"
                )
            required_real_worlds.extend(domain_required_real_worlds[:minimum])
            required_real_set.update(domain_required_real_worlds[:minimum])
        remaining_real = max(0, min_real_worlds - len(required_real_worlds))
        if remaining_real:
            required_real_by_domain = Counter(
                domains_by_world[world_id] for world_id in required_real_worlds
            )
            available_real: list[str] = []
            for world_id in sorted(real_eligible_worlds, key=select_key):
                domain = domains_by_world[world_id]
                if (
                    world_id in required_real_set
                    or domain not in domain_quotas
                    or required_real_by_domain[domain] >= domain_quotas[domain]
                ):
                    continue
                available_real.append(world_id)
                required_real_by_domain[domain] += 1
                if len(available_real) == remaining_real:
                    break
            if len(available_real) < remaining_real:
                raise PromotionError(
                    "real workflow requirements cannot fit domain world quotas"
                )
            for world_id in available_real:
                required_real_worlds.append(world_id)
                required_real_set.add(world_id)
        required_real_by_domain = Counter(
            domains_by_world[world_id] for world_id in required_real_worlds
        )
        if any(
            domains_by_world[world_id] not in domain_quotas
            for world_id in required_real_worlds
        ):
            raise PromotionError("required real workflow domain has no world quota")
        selected_worlds = []
        for domain, quota in domain_quotas.items():
            domain_eligible = sorted(
                (
                    world_id
                    for world_id in eligible_worlds
                    if domains_by_world[world_id] == domain
                ),
                key=select_key,
            )
            if len(domain_eligible) < quota:
                raise PromotionError(
                    "insufficient fully audited domain worlds: "
                    f"{domain} {len(domain_eligible)}<{quota}"
                )
            required = [
                world_id
                for world_id in required_real_worlds
                if domains_by_world[world_id] == domain
            ]
            if required_real_by_domain[domain] > quota:
                raise PromotionError(
                    f"real workflow requirement exceeds {domain} world quota"
                )
            selected_for_domain = (
                required
                + [
                    world_id
                    for world_id in domain_eligible
                    if world_id not in required_real_worlds
                ][: quota - len(required)]
            )
            selected_worlds_by_domain[domain] = sorted(selected_for_domain)
            selected_worlds.extend(selected_for_domain)
    else:
        required_real_worlds = sorted(real_eligible_worlds, key=select_key)[
            :min_real_worlds
        ]
        selected_worlds = (
            required_real_worlds
            + [
                world_id
                for world_id in sorted(eligible_worlds, key=select_key)
                if world_id not in required_real_worlds
            ][: target - len(required_real_worlds)]
        )
    selected_real_worlds = {
        world_id for world_id in selected_worlds if world_id in real_eligible_worlds
    }
    if profile.split_strategy == "world_atomic_domain_stratified_hash_v1":
        train_minima = dict(profile.min_train_worlds_by_domain)
        if (
            not profile.require_all_rows_source_bound
            or profile.min_real_train_worlds != profile.min_train_worlds
            or profile.min_real_eval_worlds != profile.min_eval_worlds
            or set(train_minima) != set(domain_quotas)
            or len(train_minima) != len(profile.min_train_worlds_by_domain)
            or any(
                minimum < 1 or minimum >= domain_quotas[domain]
                for domain, minimum in train_minima.items()
            )
            or profile.min_eval_domains < 1
            or profile.min_eval_domains > profile.min_eval_worlds
        ):
            raise PromotionError("release profile domain split quotas are invalid")
        eval_domains = sorted(
            domain_quotas,
            key=lambda domain: _sha256_text(f"{profile_digest}|eval-domain|{domain}"),
        )[: profile.min_eval_domains]
        eval_worlds = {
            min(
                selected_worlds_by_domain[domain],
                key=lambda world_id: _sha256_text(f"{profile_digest}|eval|{world_id}"),
            )
            for domain in eval_domains
        }
        remaining_eval_slots = profile.min_eval_worlds - len(eval_worlds)
        if remaining_eval_slots:
            eval_counts = Counter(
                domains_by_world[world_id] for world_id in eval_worlds
            )
            remaining = [
                world_id
                for world_id in sorted(
                    set(selected_worlds) - eval_worlds,
                    key=lambda item: _sha256_text(f"{profile_digest}|eval|{item}"),
                )
                if domain_quotas[domains_by_world[world_id]]
                - eval_counts[domains_by_world[world_id]]
                > train_minima[domains_by_world[world_id]]
            ]
            eval_worlds.update(remaining[:remaining_eval_slots])
    else:
        real_eval_worlds = set(
            sorted(
                selected_real_worlds,
                key=lambda world_id: _sha256_text(
                    f"{profile_digest}|real-eval|{world_id}"
                ),
            )[: profile.min_real_eval_worlds]
        )
        protected_real_train_worlds = set(
            sorted(
                selected_real_worlds - real_eval_worlds,
                key=lambda world_id: _sha256_text(
                    f"{profile_digest}|real-train|{world_id}"
                ),
            )[: profile.min_real_train_worlds]
        )
        eval_worlds = real_eval_worlds | set(
            sorted(
                set(selected_worlds) - real_eval_worlds - protected_real_train_worlds,
                key=lambda world_id: _sha256_text(f"{profile_digest}|eval|{world_id}"),
            )[: profile.min_eval_worlds - len(real_eval_worlds)]
        )
    if len(eval_worlds) != profile.min_eval_worlds:
        raise PromotionError("release profile eval split cannot satisfy real quotas")
    split_by_world = {
        world_id: "eval" if world_id in eval_worlds else "train"
        for world_id in selected_worlds
    }
    if profile.min_train_worlds_by_domain and (
        any(
            sum(
                split_by_world[world_id] == "train"
                for world_id in selected_worlds_by_domain.get(domain, [])
            )
            < minimum
            for domain, minimum in profile.min_train_worlds_by_domain
        )
        or len({domains_by_world[world_id] for world_id in eval_worlds})
        < profile.min_eval_domains
    ):
        raise PromotionError("release profile domain split cannot satisfy quotas")
    split_worlds_by_domain = (
        {
            split: {
                domain: sorted(
                    world_id
                    for world_id in world_ids
                    if split_by_world[world_id] == split
                )
                for domain, world_ids in selected_worlds_by_domain.items()
            }
            for split in ("train", "eval")
        }
        if domain_quotas
        else {}
    )
    selected = [
        candidate
        for world_id in selected_worlds
        for _digest, candidate in by_world[world_id]
    ]
    selected_ids = sorted(candidate_sha256(candidate) for candidate in selected)
    task_semantic_commitments = {
        digest: str(audits_by_candidate[digest]["task_semantic_commitment_sha256"])
        for digest in selected_ids
        if isinstance(candidates_by_id[digest].get("task_replay_sidecar"), dict)
    }
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "release_profile_id": release_profile_id,
            "release_profile_sha256": profile_digest,
            "tokenizer_model_id": profile.tokenizer_model_id,
            "tokenizer_revision": profile.tokenizer_revision,
            "tokenizer_asset_manifest_sha256": (
                profile.tokenizer_asset_manifest_sha256
            ),
            "predecessor_profile_id": profile.predecessor_profile_id,
            "predecessor_report_sha256": predecessor_report_digest or None,
            "predecessor_gate_receipt_sha256": predecessor_gate_digest or None,
            "candidate_row_set_sha256": row_digest_set_sha256(candidate_digests),
            "audit_row_set_sha256": row_digest_set_sha256(
                [serialized_row_sha256(audit) for audit in audits]
            ),
            "audit_sha256_by_candidate": {
                digest: serialized_row_sha256(audit)
                for digest, audit in sorted(audits_by_candidate.items())
            },
            "task_semantic_commitment_sha256_by_candidate": (task_semantic_commitments),
            "n_candidate_worlds": len(by_world),
            "n_eligible_worlds": len(eligible_worlds),
            "n_selected_worlds": len(selected_worlds),
            "split_strategy": profile.split_strategy,
            "promoted_domain_world_quotas": domain_quotas,
            "selected_worlds_by_domain": selected_worlds_by_domain,
            "worlds_by_domain": selected_worlds_by_domain,
            "split_worlds_by_domain": split_worlds_by_domain,
            "selected_candidate_sha256": selected_ids,
            "split_by_world": split_by_world,
            "n_real_eligible_worlds": len(real_eligible_worlds),
            "real_worlds_by_split": {
                split: sorted(
                    world_id
                    for world_id in selected_real_worlds
                    if split_by_world[world_id] == split
                )
                for split in ("train", "eval")
            },
        },
        audit_attestation_key,
        purpose=RELEASE_SELECTION_PURPOSE,
    )
    return selected, receipt


def create_train_ready_report(
    candidate_report: dict[str, Any],
    candidates: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    attestation_key: bytes | None = None,
    *,
    report_attestation_key: bytes | None = None,
    candidate_attestation_key: bytes | None = None,
    promotion_attestation_key: bytes | None = None,
    selection_attestation_key: bytes | None = None,
    release_selection_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sign the exact post-promotion row set and its release-scale counts."""
    report_key = report_attestation_key or attestation_key
    candidate_key = candidate_attestation_key or attestation_key
    promotion_key = promotion_attestation_key or attestation_key
    if report_key is None or candidate_key is None or promotion_key is None:
        raise PromotionError("train-ready report attestation keys are incomplete")
    if not verify_attestation(
        candidate_report, report_key, purpose=QUALITY_REPORT_PURPOSE
    ):
        raise PromotionError("candidate quality report attestation is invalid")
    if candidate_report.get("data_stage") != "candidate":
        raise PromotionError("quality report is not a candidate report")
    release_profile_id = str(candidate_report.get("release_profile_id") or "")
    if not release_profile_id:
        raise PromotionError("candidate quality report has no release profile")
    profile_digest = release_profile_sha256(release_profile_id)
    if candidate_report.get("release_profile_sha256") != profile_digest:
        raise PromotionError("candidate quality report release profile changed")
    candidate_digests = [serialized_row_sha256(row) for row in candidates]
    if int(candidate_report.get("n_rows") or 0) != len(
        candidates
    ) or candidate_report.get("candidate_row_set_sha256") != row_digest_set_sha256(
        candidate_digests
    ):
        raise PromotionError("candidate quality report row binding is invalid")
    is_candidate_union = (
        candidate_report.get("schema_version") == CANDIDATE_UNION_REPORT_SCHEMA
    )
    if is_candidate_union:
        identity_bindings = _candidate_identity_bindings(candidates)
        candidate_worlds = {
            str(candidate.get("world_id") or "") for candidate in candidates
        }
        candidate_worlds.discard("")
        if (
            candidate_report.get("data_product") != RELEASE_UNION_DATA_PRODUCT
            or candidate_report.get("candidate_identity_bindings") != identity_bindings
            or candidate_report.get("candidate_identity_bindings_sha256")
            != _canonical_sha256(identity_bindings)
            or int(candidate_report.get("n_worlds") or 0) != len(candidate_worlds)
            or int(candidate_report.get("target_promoted_worlds") or 0)
            != release_profile(release_profile_id).expected_promoted_worlds
            or release_selection_receipt is None
            or candidate_report.get("release_selection_sha256")
            != serialized_row_sha256(release_selection_receipt)
        ):
            raise PromotionError("candidate union identity binding is invalid")
    for candidate in candidates:
        if (
            not verify_attestation(
                candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
            or (
                not is_candidate_union
                and (
                    candidate.get("schema_version")
                    != candidate_report.get("schema_version")
                    or candidate.get("data_product")
                    != candidate_report.get("data_product")
                )
            )
            or candidate.get("data_stage") != "candidate"
        ):
            raise PromotionError("candidate product identity is inconsistent")
    task_content_by_candidate_id: dict[str, tuple[str, ...]] = {}
    task_training_by_candidate_id: dict[str, tuple[str, str, str]] = {}
    seen_task_content_identities: set[tuple[str, ...]] = set()
    seen_task_training_content_digests: set[str] = set()
    task_answers_by_prompt: dict[str, str] = {}
    for candidate in candidates:
        identity = _task_candidate_content_identity(candidate)
        if identity is None:
            continue
        if identity in seen_task_content_identities:
            raise PromotionError("train-ready task content identity is duplicated")
        seen_task_content_identities.add(identity)
        task_training_identity = _task_training_content_identity(candidate)
        training_digest, prompt_digest, answer = task_training_identity
        if training_digest in seen_task_training_content_digests:
            raise PromotionError("train-ready task training content is duplicated")
        seen_task_training_content_digests.add(training_digest)
        previous_answer = task_answers_by_prompt.setdefault(prompt_digest, answer)
        if previous_answer != answer:
            raise PromotionError("train-ready task prompt has conflicting answers")
        digest = candidate_sha256(candidate)
        task_content_by_candidate_id[digest] = identity
        task_training_by_candidate_id[digest] = task_training_identity
    invalid = [
        row for row in rows if sft_row_errors(row, attestation_key=promotion_key)
    ]
    if invalid:
        raise PromotionError(f"train-ready report has {len(invalid)} invalid rows")
    worlds = {str(row.get("world_id") or "") for row in rows}
    worlds.discard("")
    target = int(candidate_report.get("target_promoted_worlds") or 0)
    if target < 1 or len(worlds) != target:
        raise PromotionError(
            f"promoted world target mismatch: observed={len(worlds)} target={target}"
        )
    candidate_ids = {candidate_sha256(candidate) for candidate in candidates}
    promoted_candidate_ids = [
        str((row.get("promotion") or {}).get("candidate_sha256") or "") for row in rows
    ]
    if len(set(promoted_candidate_ids)) != len(promoted_candidate_ids) or not set(
        promoted_candidate_ids
    ).issubset(candidate_ids):
        raise PromotionError("promoted rows do not belong to the candidate report")
    candidate_by_digest = {
        candidate_sha256(candidate): candidate for candidate in candidates
    }
    promoted_task_training_digests: set[str] = set()
    promoted_task_answers_by_prompt: dict[str, str] = {}
    for row, promoted_candidate_id in zip(rows, promoted_candidate_ids, strict=True):
        identity = task_content_by_candidate_id.get(promoted_candidate_id)
        if identity is None:
            continue
        row_training_identity = _task_training_content_identity(row)
        if (
            row_training_identity
            != task_training_by_candidate_id[promoted_candidate_id]
        ):
            raise PromotionError(
                "promoted task training content differs from candidate"
            )
        training_digest, prompt_digest, answer = row_training_identity
        if training_digest in promoted_task_training_digests:
            raise PromotionError("promoted task training content is duplicated")
        promoted_task_training_digests.add(training_digest)
        previous_answer = promoted_task_answers_by_prompt.setdefault(
            prompt_digest, answer
        )
        if previous_answer != answer:
            raise PromotionError("promoted task prompt has conflicting answers")
        promotion = row.get("promotion") or {}
        expected_content_commitment = {
            "world_id": identity[1],
            "length_bucket": identity[2],
            "content_sha256": identity[-1],
        }
        if len(identity) == 5:
            expected_content_commitment["view"] = identity[3]
        if (
            promotion.get("task_candidate_content_commitment")
            != expected_content_commitment
            or (promotion.get("task_replay_sidecar") or {}).get("sha256") != identity[0]
        ):
            raise PromotionError("promoted task content commitment is invalid")
    task_selection_digest = ""
    if task_content_by_candidate_id:
        selection_key = selection_attestation_key or attestation_key
        if (
            release_selection_receipt is None
            or selection_key is None
            or not verify_attestation(
                release_selection_receipt,
                selection_key,
                purpose=RELEASE_SELECTION_PURPOSE,
            )
        ):
            raise PromotionError(
                "task train-ready report requires a valid release selection"
            )
        selected_ids = release_selection_receipt.get("selected_candidate_sha256")
        split_by_world = release_selection_receipt.get("split_by_world")
        audit_sha256_by_candidate = release_selection_receipt.get(
            "audit_sha256_by_candidate"
        )
        task_commitments = release_selection_receipt.get(
            "task_semantic_commitment_sha256_by_candidate"
        )
        promoted_task_ids = {
            candidate_id
            for candidate_id in promoted_candidate_ids
            if candidate_id in task_content_by_candidate_id
        }
        if (
            release_selection_receipt.get("schema_version") != RELEASE_SELECTION_SCHEMA
            or release_selection_receipt.get("release_profile_id") != release_profile_id
            or release_selection_receipt.get("release_profile_sha256") != profile_digest
            or release_selection_receipt.get("candidate_row_set_sha256")
            != row_digest_set_sha256(candidate_digests)
            or selected_ids != sorted(promoted_candidate_ids)
            or not isinstance(split_by_world, dict)
            or not isinstance(audit_sha256_by_candidate, dict)
            or not isinstance(task_commitments, dict)
            or set(task_commitments) != promoted_task_ids
            or any(
                split_by_world.get(str(row.get("world_id") or "")) != row.get("split")
                for row in rows
            )
        ):
            raise PromotionError("task release selection binding is invalid")
        task_selection_digest = serialized_row_sha256(release_selection_receipt)
        for row, promoted_candidate_id in zip(
            rows, promoted_candidate_ids, strict=True
        ):
            if promoted_candidate_id not in promoted_task_ids:
                continue
            promotion = row.get("promotion") or {}
            try:
                actual_commitment = task_semantic_commitment_sha256_from_row(row)
            except PromotionError as error:
                raise PromotionError(
                    "promoted task semantic commitment is invalid"
                ) from error
            expected_commitment = task_commitments[promoted_candidate_id]
            if (
                _SHA256.fullmatch(str(expected_commitment or "")) is None
                or actual_commitment != expected_commitment
                or promotion.get("task_semantic_commitment_sha256")
                != expected_commitment
                or audit_sha256_by_candidate.get(promoted_candidate_id)
                != promotion.get("dense_audit_sha256")
                or promotion.get("release_selection_sha256") != task_selection_digest
            ):
                raise PromotionError(
                    "promoted task semantics differ from release selection"
                )
    identity_fields = (
        "schema_version",
        "data_product",
        "world_id",
        "query_id",
        "domain",
        "length_bucket",
        "view",
        "content_hash",
    )
    if any(
        any(
            (field in row) != (field in candidate_by_digest[candidate_id])
            or row.get(field) != candidate_by_digest[candidate_id].get(field)
            for field in identity_fields
        )
        for row, candidate_id in zip(rows, promoted_candidate_ids, strict=True)
    ):
        raise PromotionError("promoted product identity differs from candidate")
    profile = release_profile(release_profile_id)
    missing_required_buckets = _missing_required_exact_length_buckets_by_world(
        rows, profile
    )
    if missing_required_buckets:
        details = ",".join(
            f"{world_id}={'+'.join(missing)}"
            for world_id, missing in missing_required_buckets.items()
        )
        raise PromotionError(
            "promoted rows are missing required exact length buckets: " + details
        )
    missing_required_views = _missing_required_view_coverage_by_world(rows, profile)
    if missing_required_views:
        details = ",".join(
            f"{world_id}={'+'.join(missing)}"
            for world_id, missing in missing_required_views.items()
        )
        raise PromotionError(
            "promoted rows are missing required view coverage: " + details
        )
    lineage_violations = _immutable_world_lineage_violations_by_world(rows, profile)
    if lineage_violations:
        details = ",".join(
            f"{world_id}={'+'.join(reasons)}"
            for world_id, reasons in lineage_violations.items()
        )
        raise PromotionError(
            "promoted rows violate immutable world lineage: " + details
        )
    if profile.require_all_rows_source_bound:
        unbound_rows_by_world = _worlds_with_unbound_rows(rows)
        if unbound_rows_by_world:
            details = ",".join(
                f"{world_id}={len(query_ids)}"
                for world_id, query_ids in unbound_rows_by_world.items()
            )
            raise PromotionError(
                "release profile requires all rows to be source-bound: " + details
            )
    strict_growth_violations = _cumulative_history_violations_by_world(rows, profile)
    if strict_growth_violations:
        details = ";".join(
            f"{world_id}={','.join(violations)}"
            for world_id, violations in strict_growth_violations.items()
        )
        raise PromotionError(
            "promoted rows fail strict cumulative source history: " + details
        )
    for row in rows:
        if str(row.get("length_bucket") or "") not in EXACT_TOKEN_BAND_RANGES:
            continue
        promotion = row.get("promotion") or {}
        if profile.tokenizer_asset_manifest_sha256 and (
            row.get("tokenizer_model_id") != profile.tokenizer_model_id
            or row.get("tokenizer_revision") != profile.tokenizer_revision
            or row.get("tokenizer_asset_manifest_sha256")
            != profile.tokenizer_asset_manifest_sha256
            or promotion.get("tokenizer_asset_manifest_sha256")
            != profile.tokenizer_asset_manifest_sha256
        ):
            raise PromotionError(
                "train-ready row tokenizer assets do not match release profile"
            )
    domain_quotas = dict(profile.promoted_domain_world_quotas)
    selection_digest = task_selection_digest
    selected_worlds_by_domain: dict[str, list[str]] = {}
    split_worlds_by_domain: dict[str, dict[str, list[str]]] = {}
    expected_real_worlds_by_split: dict[str, list[str]] = {
        "train": [],
        "eval": [],
    }
    if target == profile.expected_promoted_worlds:
        selection_key = selection_attestation_key or attestation_key
        if (
            release_selection_receipt is None
            or selection_key is None
            or not verify_attestation(
                release_selection_receipt,
                selection_key,
                purpose=RELEASE_SELECTION_PURPOSE,
            )
        ):
            raise PromotionError("release world selection receipt is invalid")
        if (
            release_selection_receipt.get("schema_version") != RELEASE_SELECTION_SCHEMA
            or release_selection_receipt.get("release_profile_id") != release_profile_id
            or release_selection_receipt.get("release_profile_sha256") != profile_digest
            or release_selection_receipt.get("predecessor_profile_id")
            != profile.predecessor_profile_id
            or (
                profile.predecessor_profile_id is not None
                and _SHA256.fullmatch(
                    str(
                        release_selection_receipt.get("predecessor_report_sha256") or ""
                    )
                )
                is None
            )
            or (
                profile.predecessor_profile_id is not None
                and _SHA256.fullmatch(
                    str(
                        release_selection_receipt.get("predecessor_gate_receipt_sha256")
                        or ""
                    )
                )
                is None
            )
            or release_selection_receipt.get("split_strategy") != profile.split_strategy
            or (
                bool(profile.tokenizer_asset_manifest_sha256)
                and (
                    release_selection_receipt.get("tokenizer_model_id")
                    != profile.tokenizer_model_id
                    or release_selection_receipt.get("tokenizer_revision")
                    != profile.tokenizer_revision
                    or release_selection_receipt.get("tokenizer_asset_manifest_sha256")
                    != profile.tokenizer_asset_manifest_sha256
                )
            )
            or release_selection_receipt.get("candidate_row_set_sha256")
            != row_digest_set_sha256(candidate_digests)
            or release_selection_receipt.get("selected_candidate_sha256")
            != sorted(promoted_candidate_ids)
            or release_selection_receipt.get("n_selected_worlds") != target
            or (
                bool(domain_quotas)
                and release_selection_receipt.get("promoted_domain_world_quotas")
                != domain_quotas
            )
        ):
            raise PromotionError("release world selection binding is invalid")
        split_by_world = release_selection_receipt.get("split_by_world")
        audit_sha256_by_candidate = release_selection_receipt.get(
            "audit_sha256_by_candidate"
        )
        if (
            not isinstance(split_by_world, dict)
            or not isinstance(audit_sha256_by_candidate, dict)
            or set(split_by_world) != worlds
            or list(split_by_world.values()).count("train") != profile.min_train_worlds
            or list(split_by_world.values()).count("eval") != profile.min_eval_worlds
            or any(
                split_by_world.get(str(row.get("world_id") or "")) != row.get("split")
                for row in rows
            )
        ):
            raise PromotionError("promoted split differs from release selection")
        promoted_id_set = set(promoted_candidate_ids)
        selected_candidates = [
            candidate
            for candidate in candidates
            if candidate_sha256(candidate) in promoted_id_set
        ]
        if domain_quotas:
            selected_domains = _candidate_world_domains(selected_candidates)
            selected_worlds_by_domain = {
                domain: sorted(
                    world_id
                    for world_id, selected_domain in selected_domains.items()
                    if selected_domain == domain
                )
                for domain in domain_quotas
            }
            if {
                domain: len(world_ids)
                for domain, world_ids in selected_worlds_by_domain.items()
            } != domain_quotas or release_selection_receipt.get(
                "selected_worlds_by_domain"
            ) != selected_worlds_by_domain:
                raise PromotionError("release domain world quota is invalid")
            split_worlds_by_domain = {
                split: {
                    domain: sorted(
                        world_id
                        for world_id in world_ids
                        if split_by_world.get(world_id) == split
                    )
                    for domain, world_ids in selected_worlds_by_domain.items()
                }
                for split in ("train", "eval")
            }
            if profile.min_train_worlds_by_domain and (
                release_selection_receipt.get("worlds_by_domain")
                != selected_worlds_by_domain
                or release_selection_receipt.get("split_worlds_by_domain")
                != split_worlds_by_domain
            ):
                raise PromotionError("release signed domain split map is invalid")
            if profile.min_train_worlds_by_domain and (
                any(
                    len(split_worlds_by_domain["train"].get(domain, [])) < minimum
                    for domain, minimum in profile.min_train_worlds_by_domain
                )
                or len(
                    {
                        domain
                        for domain, world_ids in split_worlds_by_domain["eval"].items()
                        if world_ids
                    }
                )
                < profile.min_eval_domains
            ):
                raise PromotionError("release domain split quota is invalid")
        selected_real_worlds = {
            str(candidate.get("world_id") or "")
            for candidate in selected_candidates
            if _candidate_has_source_bound_proof(candidate)
        }
        expected_real_worlds_by_split = {
            split: sorted(
                world_id
                for world_id in selected_real_worlds
                if split_by_world.get(world_id) == split
            )
            for split in ("train", "eval")
        }
        if (
            release_selection_receipt.get("real_worlds_by_split")
            != expected_real_worlds_by_split
            or len(expected_real_worlds_by_split["train"])
            < profile.min_real_train_worlds
            or len(expected_real_worlds_by_split["eval"]) < profile.min_real_eval_worlds
        ):
            raise PromotionError("release real workflow split quota is invalid")
        selection_digest = serialized_row_sha256(release_selection_receipt)
        if any(
            (row.get("promotion") or {}).get("release_selection_sha256")
            != selection_digest
            for row in rows
        ):
            raise PromotionError("promoted rows do not bind release selection")
        if any(
            audit_sha256_by_candidate.get(candidate_digest)
            != (row.get("promotion") or {}).get("dense_audit_sha256")
            for candidate_digest, row in zip(promoted_candidate_ids, rows)
        ):
            raise PromotionError("promoted rows differ from selected dense audits")
    dossier_candidates: dict[str, dict[str, str]] = {}
    for digest, candidate in candidate_by_digest.items():
        view = str(candidate.get("view") or "")
        if view in {"full", "cf"}:
            dossier = str(candidate.get("dossier_id") or "")
            if not dossier:
                raise PromotionError("counterfactual dossier identity is missing")
            twins = dossier_candidates.setdefault(dossier, {})
            if view in twins:
                raise PromotionError("counterfactual dossier view is duplicated")
            twins[view] = digest
    promoted_ids = set(promoted_candidate_ids)
    for dossier, twins in dossier_candidates.items():
        if set(twins) != {"full", "cf"}:
            continue
        selected = {view for view, digest in twins.items() if digest in promoted_ids}
        if selected and selected != {"full", "cf"}:
            raise PromotionError(
                f"counterfactual dossier promotion is asymmetric: {dossier}"
            )
    if not is_candidate_union and any(
        row.get("schema_version") != candidate_report.get("schema_version")
        or row.get("data_product") != candidate_report.get("data_product")
        for row in rows
    ):
        raise PromotionError("promoted product identity is inconsistent")
    if any(row.get("split") not in {"train", "eval"} for row in rows):
        raise PromotionError("promoted rows have an invalid split")
    payload = {
        "schema_version": (
            TRAIN_READY_UNION_REPORT_SCHEMA
            if is_candidate_union
            else str(candidate_report.get("schema_version") or "")
        ),
        "data_product": (
            RELEASE_UNION_DATA_PRODUCT
            if is_candidate_union
            else str(candidate_report.get("data_product") or "")
        ),
        "data_stage": "train_ready",
        "release_profile_id": release_profile_id,
        "release_profile_sha256": profile_digest,
        "tokenizer_model_id": profile.tokenizer_model_id,
        "tokenizer_revision": profile.tokenizer_revision,
        "tokenizer_asset_manifest_sha256": (profile.tokenizer_asset_manifest_sha256),
        "report_binding_revision": QUALITY_REPORT_BINDING_REVISION,
        "candidate_quality_report_sha256": _canonical_sha256(candidate_report),
        "release_selection_sha256": selection_digest or None,
        "predecessor_profile_id": profile.predecessor_profile_id,
        "predecessor_report_sha256": (
            release_selection_receipt.get("predecessor_report_sha256")
            if release_selection_receipt is not None
            else None
        ),
        "predecessor_gate_receipt_sha256": (
            release_selection_receipt.get("predecessor_gate_receipt_sha256")
            if release_selection_receipt is not None
            else None
        ),
        "candidate_row_set_sha256": str(
            candidate_report.get("candidate_row_set_sha256") or ""
        ),
        "n_candidate_worlds": int(candidate_report.get("n_worlds") or 0),
        "n_candidate_rows": int(candidate_report.get("n_rows") or 0),
        "target_promoted_worlds": target,
        "n_worlds": len(worlds),
        "n_world_ids": len(worlds),
        "n_rows": len(rows),
        "n_candidates": len(candidates),
        "n_promoted": len(rows),
        "promoted_row_set_sha256": promoted_row_set_sha256(rows),
        "promoted_split_row_set_sha256": {
            split: promoted_split_row_set_sha256(rows, split)
            for split in ("train", "eval")
        },
        "promoted_domain_world_quotas": domain_quotas,
        "worlds_by_domain": {
            domain: len(world_ids)
            for domain, world_ids in selected_worlds_by_domain.items()
        }
        if domain_quotas
        else {},
        "split_worlds_by_domain": split_worlds_by_domain,
        "candidate_generation_retention": float(candidate_report.get("retention") or 0),
        "promotion_retention": len(rows) / max(1, len(candidates)),
        "retention": len(rows) / max(1, len(candidates)),
        "n_clones": int(candidate_report.get("n_clones") or 0),
        "by_split": dict(Counter(str(row.get("split") or "?") for row in rows)),
        "real_worlds_by_split": {
            split: len(world_ids)
            for split, world_ids in expected_real_worlds_by_split.items()
        }
        if selection_digest
        else None,
        "by_length": dict(
            Counter(str(row.get("length_bucket") or "?") for row in rows)
        ),
        "by_domain": dict(Counter(str(row.get("domain") or "?") for row in rows)),
        "by_motif": dict(Counter(str(row.get("motif") or "?") for row in rows)),
    }
    payload.update(local_probe_diagnostic_metadata())
    return attach_attestation(payload, report_key, purpose=QUALITY_REPORT_PURPOSE)


def _artifact_bindings(candidate: dict[str, Any]) -> list[dict[str, str]]:
    classifications = candidate.get("artifact_classification")
    if not isinstance(classifications, list) or not classifications:
        raise PromotionError("candidate has no artifact classification")
    context = candidate.get("document_context")
    if not isinstance(context, str) or not context.strip():
        raise PromotionError("candidate has no document context")
    documents = context.split(SEP)
    if len(documents) != len(classifications):
        raise PromotionError("artifact classification and document count differ")

    bindings: list[dict[str, str]] = []
    seen: set[str] = set()
    for item, text in zip(classifications, documents):
        if not isinstance(item, dict):
            raise PromotionError("candidate artifact classification is malformed")
        artifact_id = str(item.get("artifact_id") or "")
        if not artifact_id or artifact_id in seen:
            raise PromotionError("candidate artifact ids are missing or duplicated")
        seen.add(artifact_id)
        bindings.append(
            {
                "artifact_id": artifact_id,
                "text": text,
                "text_sha256": _sha256_text(text),
            }
        )
    return bindings


def _n_workstreams(bindings: list[dict[str, str]]) -> int:
    indices = [
        int(match.group(1))
        for binding in bindings
        if (match := _WORKSTREAM_ARTIFACT.search(binding["artifact_id"]))
    ]
    return max(indices, default=-1) + 1


def _candidate_n_workstreams(
    candidate: dict[str, Any], bindings: list[dict[str, str]]
) -> int:
    """Use the signed generation parameter; retain legacy CodeForge inference."""
    raw = candidate.get("n_workstreams")
    if raw is None:
        return _n_workstreams(bindings)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 512:
        raise PromotionError("candidate n_workstreams is invalid")
    return raw


def _candidate_include_program_joins(candidate: dict[str, Any]) -> bool:
    """Replay the signed query-composition setting; old rows used joins."""
    raw = candidate.get("include_program_joins", True)
    if not isinstance(raw, bool):
        raise PromotionError("candidate include_program_joins is invalid")
    return raw


def _reconstruct_candidate(
    candidate: dict[str, Any],
    episode_bundle_path: Path | None = None,
    episode_attestation_key: bytes | None = None,
    source_bundle_path: Path | None = None,
    source_attestation_key: bytes | None = None,
) -> tuple[
    Any,
    QuerySpec,
    list[Artifact],
    list[Artifact],
    dict[str, dict[str, str]],
]:
    if candidate.get("data_stage") != "candidate":
        raise PromotionError("only candidate rows can be promoted")
    if candidate.get("training_objective") != "sft":
        raise PromotionError("only SFT candidates can be promoted")
    if candidate.get("view") not in {"full", "minimal", "ordered_artifact_view", "cf"}:
        raise PromotionError("candidate view is not trainable")
    bindings = _artifact_bindings(candidate)
    classifications = candidate["artifact_classification"]
    assert isinstance(classifications, list)
    has_real_or_hybrid = any(
        not isinstance(item, dict)
        or item.get("source_origin") != "synthetic_world"
        or item.get("workflow_kind") != "synthetic_executable"
        for item in classifications
    )
    raw_bundle_binding = candidate.get("episode_replay_bundle")
    raw_source_binding = candidate.get("source_workflow_bundle")
    if raw_bundle_binding is not None and raw_source_binding is not None:
        raise PromotionError("candidate cannot bind two replay bundle types")
    requires_bundle = (
        has_real_or_hybrid
        or raw_bundle_binding is not None
        or raw_source_binding is not None
    )
    bundle_bindings: dict[str, dict[str, str]] = {}
    real_workflows = None
    source_workflows = None
    if raw_bundle_binding is not None:
        if episode_bundle_path is None or not isinstance(raw_bundle_binding, dict):
            raise PromotionError(
                "real/hybrid candidate requires a producer-attested "
                f"{REAL_REPLAY_BUNDLE_PURPOSE} sidecar"
            )
        bundle_binding = {
            field: str(raw_bundle_binding.get(field) or "")
            for field in ("schema_version", "sha256", "composition")
        }
        if (
            bundle_binding["schema_version"] != EPISODE_REPLAY_BUNDLE_SCHEMA
            or bundle_binding["composition"] != "chronological_causal_union"
            or _SHA256.fullmatch(bundle_binding["sha256"]) is None
        ):
            raise PromotionError("candidate episode replay bundle binding is malformed")
        try:
            real_workflows = load_episode_replay_bundle(
                episode_bundle_path,
                attestation_key=episode_attestation_key,
                expected_sha256=bundle_binding["sha256"],
            )
        except (OSError, ValueError) as error:
            raise PromotionError(
                f"episode replay bundle validation failed: {error}"
            ) from error
        bundle_bindings["episode_replay_bundle"] = bundle_binding
    elif raw_source_binding is not None:
        if source_bundle_path is None or not isinstance(raw_source_binding, dict):
            raise PromotionError(
                "real/hybrid candidate requires a producer-attested "
                "source_workflow_bundle sidecar"
            )
        source_binding = {
            field: str(raw_source_binding.get(field) or "")
            for field in (
                "schema_version",
                "adapter_revision",
                "sha256",
                "binding_digest",
            )
        }
        if (
            source_binding["schema_version"] != SOURCE_WORKFLOW_BUNDLE_SCHEMA
            or source_binding["adapter_revision"]
            not in SOURCE_WORKFLOW_ADAPTER_REVISIONS
            or _SHA256.fullmatch(source_binding["sha256"]) is None
            or _SHA256.fullmatch(source_binding["binding_digest"]) is None
        ):
            raise PromotionError(
                "candidate source workflow bundle binding is malformed"
            )
        try:
            loaded = load_source_workflow_bundle(
                source_bundle_path, attestation_key=source_attestation_key
            )
        except (OSError, ValueError) as error:
            raise PromotionError(
                f"source workflow bundle validation failed: {error}"
            ) from error
        if (
            loaded.bundle_sha256 != source_binding["sha256"]
            or loaded.binding_digest != source_binding["binding_digest"]
            or loaded.adapter_revision != source_binding["adapter_revision"]
        ):
            raise PromotionError(
                "source workflow bundle digest does not match candidate"
            )
        source_workflows = list(loaded.workflows)
        bundle_bindings["source_workflow_bundle"] = source_binding
    elif requires_bundle:
        raise PromotionError(
            "real/hybrid candidate requires episode_replay_bundle or "
            "source_workflow_bundle binding"
        )
    try:
        seed = int(candidate["seed"])
    except (KeyError, TypeError, ValueError) as error:
        raise PromotionError("candidate seed is missing or invalid") from error
    domain = str(candidate.get("domain") or "")
    if domain not in {"company", "researchlab", "codeforge"}:
        raise PromotionError("candidate domain is unsupported")
    n_workstreams = _candidate_n_workstreams(candidate, bindings)
    include_program_joins = _candidate_include_program_joins(candidate)
    if real_workflows is None:
        if source_workflows is None:
            materialized = _synthetic_replay_materialization(
                seed, domain, n_workstreams, include_program_joins
            )
        else:
            domain_workflows = [
                workflow
                for workflow in source_workflows
                if workflow.target_domain == domain
            ]
            if not domain_workflows:
                raise PromotionError(
                    "source workflow bundle has no workflow for candidate domain"
                )
            materialized = _real_replay_materialization(
                (
                    "source_workflow_bundle",
                    loaded.bundle_sha256,
                    loaded.binding_digest,
                    loaded.adapter_revision,
                ),
                seed=seed,
                domain=domain,
                n_workstreams=n_workstreams,
                source_workflows=domain_workflows,
                include_program_joins=include_program_joins,
            )
    else:
        materialized = _real_replay_materialization(
            ("episode_replay_bundle", bundle_binding["sha256"]),
            seed=seed,
            domain=domain,
            n_workstreams=n_workstreams,
            real_workflows=real_workflows,
            include_program_joins=include_program_joins,
        )
    world = materialized.worlds["focal"]
    if world.world_id != candidate.get("world_id"):
        raise PromotionError("reconstructed world id does not match candidate")
    row_query_id = str(candidate.get("query_id") or "")
    query_type = str(candidate.get("query_type") or "")
    matches = [
        query
        for query in materialized.queries
        if query.query_type == query_type
        and row_query_id.startswith(f"{query.query_id}:")
    ]
    if len(matches) != 1:
        raise PromotionError("candidate query cannot be reconstructed uniquely")
    spec = matches[0]
    if candidate.get("motif") != spec.motif:
        raise PromotionError("candidate motif does not match replay specification")
    if candidate.get("question") != spec.question:
        raise PromotionError("candidate question does not match replay specification")
    expected_answer = spec.cf_answer if candidate.get("view") == "cf" else spec.answer
    if candidate.get("answer") != expected_answer:
        raise PromotionError("candidate answer does not match replay specification")
    if candidate.get("essential_artifact_ids") != spec.essential_artifact_ids:
        raise PromotionError(
            "candidate essential artifacts do not match replay specification"
        )
    timing = str(candidate.get("query_timing") or "")
    if timing not in {"first", "late"}:
        raise PromotionError("candidate query timing is invalid")
    expected_query_id = (
        f"{spec.query_id}:{timing}:{candidate.get('position_bucket')}:"
        f"{candidate.get('length_bucket')}"
    )
    if row_query_id != expected_query_id:
        raise PromotionError(
            "candidate query id does not match serialized view metadata"
        )
    if candidate.get("context") != wrap_prompt(
        spec.question, candidate["document_context"], timing
    ):
        raise PromotionError(
            "candidate prompt does not match question and document context"
        )

    factual_artifact_index = {
        artifact.artifact_id: artifact
        for artifacts in materialized.artifacts.values()
        for artifact in artifacts
    }
    artifact_index = dict(factual_artifact_index)
    cf_artifact_index: dict[str, Artifact] = {}
    if candidate.get("view") == "cf":
        _, cf_artifacts = render_cf_view(world, spec)
        cf_artifact_index = {
            artifact.artifact_id: artifact for artifact in cf_artifacts
        }
        artifact_index.update(cf_artifact_index)
    else:
        _, cf_artifacts = render_cf_view(world, spec)
        cf_artifact_index = {
            artifact.artifact_id: artifact for artifact in cf_artifacts
        }
    reconstructed: list[Artifact] = []
    serialized_classes = {
        str(item.get("artifact_id") or ""): item
        for item in classifications
        if isinstance(item, dict)
    }
    for binding in bindings:
        artifact = artifact_index.get(binding["artifact_id"])
        if artifact is None:
            raise PromotionError(
                f"candidate artifact cannot be reconstructed: {binding['artifact_id']}"
            )
        if artifact.text != binding["text"]:
            raise PromotionError(
                f"candidate artifact text does not match replay: {binding['artifact_id']}"
            )
        replayed = replace(artifact, text=binding["text"])
        actual = artifact_classification(replayed)
        if (
            actual.workflow_kind.value == "unclassified"
            and replayed.artifact_id.startswith(world.world_id)
        ):
            actual_source_origin = "synthetic_world"
            actual_workflow_kind = "synthetic_executable"
            actual_workflow_id = world.world_id
            actual_provenance_id = (
                "synthetic-sha256:"
                + hashlib.sha256(replayed.text.encode("utf-8")).hexdigest()
            )
        else:
            actual_source_origin = actual.source_origin.value
            actual_workflow_kind = actual.workflow_kind.value
            actual_workflow_id = actual.workflow_id
            actual_provenance_id = actual.provenance_id
        serialized = serialized_classes.get(binding["artifact_id"], {})
        if any(
            (
                serialized.get("source_origin") != actual_source_origin,
                serialized.get("workflow_kind") != actual_workflow_kind,
                serialized.get("workflow_id") != actual_workflow_id,
                serialized.get("provenance_id") != actual_provenance_id,
            )
        ):
            raise PromotionError(
                "candidate artifact classification does not match replay: "
                f"{binding['artifact_id']}"
            )
        expected_evidence_role = ""
        if replayed.artifact_id in spec.essential_artifact_ids:
            expected_evidence_role = "causal_gold"
        elif set(spec.sufficient_event_ids).intersection(replayed.reveals_events):
            expected_evidence_role = "causal_supporting"
        if (
            expected_evidence_role
            and serialized.get("evidence_role") != expected_evidence_role
        ):
            raise PromotionError(
                "candidate artifact evidence role does not match replay: "
                f"{binding['artifact_id']}"
            )
        if not expected_evidence_role and serialized.get("evidence_role") in {
            "causal_gold",
            "causal_supporting",
        }:
            raise PromotionError(
                "candidate artifact evidence role claims non-replayed proof: "
                f"{binding['artifact_id']}"
            )
        reconstructed.append(replayed)
    if candidate.get("view") == "ordered_artifact_view" and reconstructed != sorted(
        reconstructed, key=lambda artifact: (artifact.time, artifact.artifact_id)
    ):
        raise PromotionError("ordered artifact view is not chronological")
    _validate_replayed_task_metadata(
        candidate, _replayed_task_metadata(candidate, world, spec, reconstructed)
    )
    _validate_replayed_source_metadata(
        candidate, _replayed_source_metadata(world, spec, reconstructed)
    )
    counterpart_index = (
        factual_artifact_index if candidate.get("view") == "cf" else cf_artifact_index
    )
    counterpart = [
        counterpart_index.get(artifact.artifact_id.split("#", 1)[0], artifact)
        for artifact in reconstructed
    ]
    return world, spec, reconstructed, counterpart, bundle_bindings


def _strict_answer(
    candidate: dict[str, Any], world: Any, spec: QuerySpec, artifacts: list[Artifact]
) -> str:
    overrides = (
        {spec.cf_event_id: spec.cf_param_updates}
        if candidate.get("view") == "cf"
        else None
    )
    return answer_from_artifacts(
        world,
        spec,
        artifacts,
        extra_overrides=overrides,
        enforce_preconditions=True,
    )


def _parallel_dossiers(
    candidate: dict[str, Any],
    artifacts: list[Artifact],
    counterpart: list[Artifact],
) -> tuple[list[Artifact], list[Artifact]]:
    """Build factual/CF twins with the exact serialized artifact order and scope."""
    if candidate.get("view") == "cf":
        factual, counterfactual = counterpart, artifacts
    else:
        factual, counterfactual = artifacts, counterpart
    if [artifact.artifact_id for artifact in factual] != [
        artifact.artifact_id for artifact in counterfactual
    ]:
        raise PromotionError("factual and counterfactual dossier ids do not match")
    return factual, counterfactual


def _load_replay_tokenizer_uncached(model_id: str, revision: str):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
        )


@lru_cache(maxsize=2)
def _load_replay_tokenizer(model_id: str, revision: str):
    return _load_replay_tokenizer_uncached(model_id, revision)


@lru_cache(maxsize=2)
def _resolved_local_tokenizer_revision(model_id: str, revision: str) -> str:
    """Resolve the immutable snapshot backing the locally loaded tokenizer."""
    with sanitized_attestation_environment():
        from transformers.utils.hub import cached_file

        resolved = cached_file(
            model_id,
            "tokenizer_config.json",
            revision=revision,
            local_files_only=True,
        )
    if not resolved:
        raise PromotionError("candidate exact tokenizer cannot be resolved")
    parts = Path(resolved).parts
    try:
        snapshot_index = parts.index("snapshots")
        resolved_revision = parts[snapshot_index + 1]
    except (ValueError, IndexError) as error:
        raise PromotionError(
            "candidate exact tokenizer is not loaded from a pinned snapshot"
        ) from error
    if not _HEX_REVISION.fullmatch(resolved_revision):
        raise PromotionError("loaded exact tokenizer revision is malformed")
    return resolved_revision.lower()


def _token_counter_for(tokenizer: Any | None) -> Callable[[str], int] | None:
    if tokenizer is None:
        return None

    def count(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    return count


def _independent_verification_replay(
    candidate: dict[str, Any],
    world: Any,
    spec: QuerySpec,
    artifacts: list[Artifact],
    counterpart: list[Artifact],
) -> tuple[Verification, dict[str, Any], Any | None]:
    factual, counterfactual = _parallel_dossiers(candidate, artifacts, counterpart)
    tokenizer = None
    tokenizer_model_id = str(candidate.get("tokenizer_model_id") or "")
    tokenizer_revision = str(candidate.get("tokenizer_revision") or "")
    exact_metadata: dict[str, Any] | None = None
    if str(candidate.get("length_bucket") or "") in EXACT_TOKEN_BAND_RANGES:
        if (tokenizer_model_id, tokenizer_revision) not in _APPROVED_EXACT_TOKENIZERS:
            raise PromotionError("candidate exact tokenizer pin is not approved")
        declared_asset_digest = str(
            candidate.get("tokenizer_asset_manifest_sha256") or ""
        )
        if _SHA256.fullmatch(declared_asset_digest) is None:
            raise PromotionError(
                "candidate exact tokenizer asset manifest digest is missing"
            )
        try:
            loaded_revision = _resolved_local_tokenizer_revision(
                tokenizer_model_id, tokenizer_revision
            )
            loaded_asset_digest = resolved_tokenizer_asset_manifest_sha256(
                tokenizer_model_id, tokenizer_revision
            )
        except (
            ImportError,
            OSError,
            RuntimeError,
            TokenizerAssetError,
            ValueError,
        ) as error:
            raise PromotionError(
                "candidate exact tokenizer cannot be loaded"
            ) from error
        if loaded_revision != tokenizer_revision:
            raise PromotionError("loaded exact tokenizer revision does not match pin")
        if loaded_asset_digest != declared_asset_digest:
            raise PromotionError(
                "loaded exact tokenizer asset manifest does not match candidate"
            )
        try:
            tokenizer = _load_replay_tokenizer(tokenizer_model_id, tokenizer_revision)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            raise PromotionError(
                "candidate exact tokenizer cannot be loaded"
            ) from error
        if (
            resolved_tokenizer_asset_manifest_sha256(
                tokenizer_model_id, tokenizer_revision
            )
            != loaded_asset_digest
        ):
            raise PromotionError("exact tokenizer assets changed while loading")
        reconstructed_context = wrap_prompt(
            spec.question,
            join_artifacts(artifacts),
            str(candidate["query_timing"]),
        )
        with sanitized_attestation_environment():
            replayed_tokens = len(
                tokenizer.encode(reconstructed_context, add_special_tokens=False)
            )
        try:
            declared_tokens = int(candidate.get("tokenizer_context_tokens") or 0)
        except (TypeError, ValueError) as error:
            raise PromotionError("candidate exact token count is invalid") from error
        if replayed_tokens != declared_tokens:
            raise PromotionError("candidate exact token count does not replay")
        exact_reject = exact_token_band_reject_reason(
            str(candidate["length_bucket"]), replayed_tokens
        )
        if exact_reject:
            raise PromotionError(exact_reject)
        exact_metadata = {
            "tokenizer_context_tokens": replayed_tokens,
            "tokenizer_model_id": tokenizer_model_id,
            "tokenizer_revision": tokenizer_revision,
            "tokenizer_asset_manifest_sha256": loaded_asset_digest,
        }
    verification, notes = verify_question(
        world,
        spec,
        factual,
        cf_artifacts=counterfactual,
        window_ids=query_adjacent_window_artifact_ids(
            factual,
            str(candidate["query_timing"]),
            set(spec.essential_artifact_ids),
        ),
        retrieval_top_k=3,
        verification_mode="candidate",
        raw_window_tokenizer=tokenizer,
    )
    if exact_metadata is not None:
        notes["exact_token_replay"] = exact_metadata
    if not verification.all_green():
        failed = [
            name
            for name, value in verification.model_dump().items()
            if isinstance(value, bool)
            and name not in {"production_mode", "embedding_topk_insufficient"}
            and not value
        ]
        raise PromotionError(
            "independent verification replay failed: " + ",".join(failed)
        )
    cf_shortcuts_green, cf_shortcut_notes = counterfactual_shortcuts_insufficient(
        world,
        spec,
        counterfactual,
        retrieval_top_k=3,
        raw_window_tokenizer=tokenizer,
    )
    notes["counterfactual_shortcuts"] = cf_shortcut_notes
    if candidate.get("view") == "cf" and not cf_shortcuts_green:
        raise PromotionError("counterfactual dossier has a short-context shortcut")
    return verification, notes, tokenizer


def _source_family_from_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if not parsed.hostname or len(path_parts) < 2:
        return ""
    return (
        f"{parsed.hostname.lower()}/"
        f"{'/'.join(path_parts[:2]).removesuffix('.git').lower()}"
    )


def _replayed_source_family_ids(artifacts: list[Artifact]) -> list[str]:
    families: set[str] = set()
    for artifact in artifacts:
        slots = artifact.slots or {}
        family = str(slots.get("source_family") or "").strip().lower()
        stem = str(slots.get("source_stem") or "").strip().lower()
        if not family:
            family = _source_family_from_url(str(slots.get("source_url") or ""))
        if not family and stem:
            family = stem.split("-", 1)[0].rstrip("0123456789") or stem
        if family:
            families.add(family)
    return sorted(families)


def _replayed_task_metadata(
    candidate: dict[str, Any], world: Any, spec: QuerySpec, artifacts: list[Artifact]
) -> dict[str, Any]:
    replayed_graph = graph_stats(world, spec)
    proof_id = _sha256_text(
        json.dumps(
            {
                "necessary": sorted(spec.essential_event_ids),
                "sufficient": sorted(spec.sufficient_event_ids),
                "expression": spec.gold_expression,
            },
            sort_keys=True,
        )
    )[:20]
    program_id = stable_answer_program_id(spec)
    dossier = stable_dossier_id(
        world.world_id,
        spec.query_id,
        str(candidate["query_timing"]),
        [artifact.artifact_id for artifact in artifacts],
    )
    semantic_growth_group_id = _sha256_text(
        f"{world.world_id}|{spec.semantic_growth_group or spec.query_id}"
    )[:20]
    visible_events = {
        event_id for artifact in artifacts for event_id in artifact.reveals_events
    }
    return {
        "source_family_ids": _replayed_source_family_ids(artifacts),
        "canonical_topology": canonical_topology(spec),
        "topology_family": topology_family(spec),
        "executable_proof_id": proof_id,
        "answer_program_id": program_id,
        "base_task_id": _sha256_text(
            f"{world.world_id}|{spec.base_task_group or spec.query_id}"
        )[:20],
        "semantic_base_task_id": stable_semantic_base_task_id(spec),
        "dossier_id": dossier,
        "semantic_growth_group_id": semantic_growth_group_id,
        "strict_support_event_count": len(
            visible_events.intersection(spec.sufficient_event_ids)
        ),
        "cf_answer": spec.cf_answer,
        "cf_op": spec.cf_op,
        "proof_graph": {
            "necessary_nodes": list(spec.essential_event_ids),
            "sufficient_set": list(spec.sufficient_event_ids),
            "answer_expression": spec.gold_expression,
            "cf_event_id": spec.cf_event_id,
            "cf_op": spec.cf_op,
        },
        "graph": replayed_graph,
        "hop_count": int(replayed_graph["hop_count"]),
        "searchart_width": len(spec.essential_artifact_ids),
        "program_ops": list(spec.program_ops or []),
        "window_artifact_ids": query_adjacent_window_artifact_ids(
            artifacts,
            str(candidate["query_timing"]),
            set(spec.essential_artifact_ids),
        ),
    }


def _validate_replayed_task_metadata(
    candidate: dict[str, Any], expected: dict[str, Any]
) -> None:
    labels = {
        "source_family_ids": "source family",
        "canonical_topology": "canonical topology",
        "topology_family": "topology family",
        "executable_proof_id": "executable proof",
        "answer_program_id": "answer program",
        "base_task_id": "base task",
        "semantic_base_task_id": "semantic base task",
        "dossier_id": "dossier identity",
        "semantic_growth_group_id": "semantic growth group",
        "strict_support_event_count": "strict support count",
        "cf_answer": "counterfactual answer",
        "cf_op": "counterfactual operator",
        "proof_graph": "proof graph",
        "graph": "graph statistics",
        "hop_count": "graph hop count",
    }
    for field, label in labels.items():
        if field == "dossier_id" and candidate.get("view") in {"full", "cf"}:
            if candidate.get(field) != expected[field]:
                raise PromotionError(
                    "candidate dossier identity does not match replay specification"
                )
        elif field in candidate and candidate[field] != expected[field]:
            raise PromotionError(
                f"candidate {label} does not match replay specification"
            )

    window_ids = candidate.get("window_artifact_ids")
    if window_ids is not None:
        artifact_ids = {
            str(item.get("artifact_id") or "")
            for item in candidate.get("artifact_classification") or []
            if isinstance(item, dict)
        }
        if (
            not isinstance(window_ids, list)
            or len(window_ids) != len(set(window_ids))
            or any(
                not isinstance(item, str) or item not in artifact_ids
                for item in window_ids
            )
        ):
            raise PromotionError(
                "candidate window artifact ids do not match replayed artifacts"
            )

    split_strategy = candidate.get("split_strategy")
    holdout = candidate.get("holdout")
    if split_strategy is None and holdout is None:
        return
    if (
        split_strategy
        not in {
            "world",
            "topology",
            "source_family",
            "domain_composition",
        }
        or not isinstance(holdout, dict)
        or holdout.get("strategy") != split_strategy
    ):
        raise PromotionError("candidate split strategy and holdout are inconsistent")
    if split_strategy == "world":
        group_key = str(candidate.get("world_id") or "")
    elif split_strategy == "topology":
        group_key = str(expected["canonical_topology"])
    elif split_strategy == "source_family":
        group_key = "+".join(expected["source_family_ids"]) or "no_source_family"
    else:
        group_key = f"{candidate.get('domain')}|{candidate.get('motif') or 'single'}"
    expected_group = _sha256_text(f"{split_strategy}|{group_key}")[:16]
    if holdout.get("group_id") != expected_group:
        raise PromotionError("candidate holdout does not match replay specification")


def _validate_replayed_source_metadata(
    candidate: dict[str, Any], expected: dict[str, Any]
) -> None:
    labels = {
        "real_source_verified": "real source verification",
        "real_source_family_ids": "real source families",
        "real_source_workflow_ids": "real source workflows",
        "source_relation_edges": "source relation edges",
        "source_relation_id": "source relation identity",
        "authentic_source_relation_edges": "authentic source relation edges",
        "authentic_source_relation_id": "authentic source relation identity",
        "hybrid_causal_edges": "hybrid causal edges",
        "context_source_relation_count": "source relation count",
    }
    for field, label in labels.items():
        if field in candidate and candidate[field] != expected[field]:
            raise PromotionError(
                f"candidate {label} does not match replay specification"
            )


def _replayed_source_metadata(
    world: Any, spec: QuerySpec, artifacts: list[Artifact]
) -> dict[str, Any]:
    sufficient_event_ids = set(spec.sufficient_event_ids)
    sufficient_event_ids &= {
        event_id for artifact in artifacts for event_id in artifact.reveals_events
    }
    all_events = {
        event.id: event for event in world.events if event.id in sufficient_event_ids
    }
    events = {
        event_id: event
        for event_id, event in all_events.items()
        if event.type == "repo_record" and event.params.get("source_url")
    }
    real_origins = {"real_public", "real_private_export", "real_derived"}
    endpoint_is_real: dict[str, bool] = {}
    for event_id in events:
        visible = [
            artifact for artifact in artifacts if event_id in artifact.reveals_events
        ]
        endpoint_is_real[event_id] = bool(visible) and all(
            artifact_classification(artifact).source_origin.value in real_origins
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
                        or not _has_authentic_source_relation(parent, child, parent_id)
                        else "authentic_source"
                    ),
                    "parent_source_url": str(parent.params["source_url"]),
                    "child_source_url": str(child.params["source_url"]),
                }
            )

    for relation in all_events.values():
        if not valid_arxiv_revision_relation_event(relation, all_events):
            continue
        source = all_events.get(
            str(relation.params.get("source_record_event_id") or "")
        )
        target = all_events.get(
            str(relation.params.get("target_record_event_id") or "")
        )
        if source is None or target is None:
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
    edges.extend(selected_sec_source_relation_edges(world, spec, artifacts))
    edges.extend(selected_wiki_source_relation_edges(world, spec, artifacts))
    edges.extend(selected_issuer_ir_source_relation_edges(world, spec, artifacts))
    edges.extend(selected_issuer_official_pdf_relation_edges(world, spec, artifacts))
    for child in all_events.values():
        if child.type not in (
            SEC_HYBRID_CHILD_EVENT_TYPES
            | WIKI_HYBRID_CHILD_EVENT_TYPES
            | ISSUER_IR_HYBRID_CHILD_EVENT_TYPES
        ):
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
    proof_artifact_ids = set(spec.essential_artifact_ids) | {
        artifact.artifact_id
        for artifact in artifacts
        if sufficient_event_ids.intersection(artifact.reveals_events)
    }
    real_origins = {"real_public", "real_private_export", "real_derived"}
    real_families: set[str] = set()
    real_workflow_ids: set[str] = set()
    for artifact in artifacts:
        classification = artifact_classification(artifact)
        slots = artifact.slots or {}
        family = str(slots.get("source_family") or "").strip().lower()
        if not family:
            family = _source_family_from_url(str(slots.get("source_url") or ""))
        if (
            artifact.artifact_id in proof_artifact_ids
            and classification.source_origin.value in real_origins
            and classification.workflow_kind.value
            in {"hybrid_causal", "real_source_derived"}
            and family
        ):
            real_families.add(family)
            workflow_id = str(slots.get("source_workflow_id") or "").strip()
            if workflow_id:
                real_workflow_ids.add(workflow_id)
    relation_id = (
        _sha256_text(json.dumps(edges, sort_keys=True, ensure_ascii=False))[:20]
        if edges
        else ""
    )
    authentic_edges = [
        edge for edge in edges if edge["relation_provenance"] == "authentic_source"
    ]
    hybrid_edges = [
        edge for edge in edges if edge["relation_provenance"] == "synthetic_executable"
    ]
    authentic_relation_id = (
        _sha256_text(json.dumps(authentic_edges, sort_keys=True, ensure_ascii=False))[
            :20
        ]
        if authentic_edges
        else ""
    )
    return {
        "real_source_verified": bool(real_families and real_workflow_ids),
        "real_source_family_ids": sorted(real_families),
        "real_source_workflow_ids": sorted(real_workflow_ids),
        "source_relation_edges": edges,
        "source_relation_id": relation_id,
        "authentic_source_relation_edges": authentic_edges,
        "authentic_source_relation_id": authentic_relation_id,
        "hybrid_causal_edges": hybrid_edges,
        "context_source_relation_count": len(edges),
        "base_task_id": _sha256_text(
            f"{world.world_id}|{spec.base_task_group or spec.query_id}"
        )[:20],
        "semantic_base_task_id": stable_semantic_base_task_id(spec),
    }


def _replayed_quality_metrics(
    candidate: dict[str, Any],
    world: Any,
    spec: QuerySpec,
    artifacts: list[Artifact],
    verification_notes: dict[str, Any] | None = None,
    *,
    token_counter: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """Recompute serialized-view quality fields without trusting candidate values."""
    document_context = join_artifacts(artifacts)
    context = wrap_prompt(
        spec.question, document_context, str(candidate["query_timing"])
    )
    essential_ids = set(spec.essential_artifact_ids)
    requested_bucket = str(candidate.get("length_bucket") or "")
    exact_token_replay = (verification_notes or {}).get("exact_token_replay")
    if requested_bucket in EXACT_TOKEN_BAND_RANGES and token_counter is None:
        raise PromotionError("exact view metrics require the pinned tokenizer")
    metrics = compute_view_metrics(
        artifacts,
        dependency_evidence_ids(artifacts, spec),
        query_timing=str(candidate["query_timing"]),
        context=context,
        token_counter=token_counter,
        token_prefix=(
            prompt_document_prefix(spec.question, str(candidate["query_timing"]))
            if token_counter is not None
            else ""
        ),
        query_boundary_tokens=(
            prompt_query_boundary(
                spec.question,
                document_context,
                str(candidate["query_timing"]),
                token_counter,
            )
            if token_counter is not None
            else None
        ),
    )
    if requested_bucket in EXACT_TOKEN_BAND_RANGES:
        exact_tokens = (
            exact_token_replay.get("tokenizer_context_tokens")
            if isinstance(exact_token_replay, dict)
            else None
        )
        if (
            not isinstance(exact_tokens, int)
            or exact_token_band_reject_reason(requested_bucket, exact_tokens)
            or exact_tokens != metrics.context_tokens
        ):
            raise PromotionError("candidate exact length metadata is not truthful")
    elif requested_bucket != metrics.length_bucket:
        raise PromotionError("candidate view length metadata is not truthful")
    if candidate.get("position_bucket") != metrics.position_bucket:
        raise PromotionError("candidate view position metadata is not truthful")

    # ``internal`` is the inclusive workflow-owned total. ``event_bearing`` is
    # its event-revealing subset, so both can grow without background masking.
    semantic_tokens = {
        "event_bearing": 0,
        "internal": 0,
        "generic_background": 0,
        "proof_bearing": 0,
        "causal_supporting": 0,
    }
    bound_workflow_ids = {world.world_id} | {
        str(event.params["workflow_id"])
        for event in world.events
        if event.params.get("workflow_id")
    }
    for artifact in artifacts:
        tokens = (token_counter or estimate_tokens)(artifact.text)
        classification = artifact_classification(artifact)
        same_workflow = classification.workflow_id in bound_workflow_ids or (
            classification.workflow_kind.value == "unclassified"
            and artifact.artifact_id.startswith(world.world_id)
        )
        if same_workflow:
            semantic_tokens["internal"] += tokens
            if artifact.reveals_events:
                semantic_tokens["event_bearing"] += tokens
        else:
            semantic_tokens["generic_background"] += tokens
        if artifact.artifact_id in essential_ids:
            semantic_tokens["proof_bearing"] += tokens
        elif set(spec.sufficient_event_ids).intersection(artifact.reveals_events):
            semantic_tokens["causal_supporting"] += tokens

    try:
        _, source_metric_tokens, source_ratio = real_source_marginal_token_metrics(
            artifacts,
            question=spec.question,
            timing=str(candidate["query_timing"]),
            token_counter=token_counter or estimate_tokens,
        )
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc
    if source_metric_tokens != metrics.context_tokens:
        raise PromotionError("real source metric does not bind the rendered context")

    replayed_graph = graph_stats(world, spec)
    difficulty = dict(candidate.get("difficulty") or {})
    difficulty.update(
        {
            "context_tokens": metrics.context_tokens,
            "max_evidence_distance": metrics.max_evidence_distance,
            "proof_depth": int(replayed_graph["proof_depth"]),
            "state_updates": len(world.state.history),
            "query_delay": 1 if candidate.get("query_timing") == "late" else 0,
            "visibility_gap": len(
                {
                    artifact.doc_type
                    for artifact in artifacts
                    if artifact.artifact_id in essential_ids
                }
            ),
        }
    )
    unique_ids = {artifact.artifact_id for artifact in artifacts}
    visible_event_ids = {
        event_id for artifact in artifacts for event_id in artifact.reveals_events
    }
    visible_real_events = {
        event.id: event for event in world.events if event.id in visible_event_ids
    }
    context_source_relation_count = sum(
        parent_id in visible_real_events
        for event in visible_real_events.values()
        if event.type == "repo_record"
        for parent_id in event.causal_inputs
    )
    context_source_relation_count += sum(
        event.type == "arxiv_revision_relation"
        and all(parent_id in visible_real_events for parent_id in event.required_inputs)
        for event in visible_real_events.values()
    )
    raw_fact_windows = (verification_notes or {}).get("raw_token_fact_windows")
    tokenizer_evidence_span_tokens = (
        int(raw_fact_windows["tokenizer_evidence_span_tokens"])
        if isinstance(raw_fact_windows, dict)
        and isinstance(raw_fact_windows.get("tokenizer_evidence_span_tokens"), int)
        else None
    )
    replayed = {
        "context": context,
        "document_context": document_context,
        "difficulty": difficulty,
        "length_bucket": requested_bucket,
        "position_bucket": metrics.position_bucket,
        "actual_context_tokens": metrics.context_tokens,
        "natural_tokens": metrics.context_tokens,
        "evidence_distance": metrics.max_evidence_distance,
        "evidence_span_tokens": metrics.evidence_span_tokens,
        "query_evidence_distance": metrics.query_evidence_distance,
        "evidence_count": metrics.evidence_count,
        "dependency_class": dependency_class_for_view(
            spec, metrics, str(candidate["view"])
        ),
        "n_unique_docs": len(unique_ids),
        "n_clones": len(artifacts) - len(unique_ids),
        "boilerplate_token_ratio": round(
            boilerplate_char_fraction(document_context), 4
        ),
        "pulse_doc_ratio": round(pulse_doc_ratio(artifacts), 4),
        "near_dup_sentence_ratio": round(sentence_near_dup_ratio(artifacts), 4),
        "semantic_tokens": semantic_tokens,
        "context_source_relation_count": context_source_relation_count,
        "real_source_token_ratio": round(source_ratio, 4),
    }
    if isinstance(exact_token_replay, dict):
        replayed.update(exact_token_replay)
    if tokenizer_evidence_span_tokens is not None:
        replayed["tokenizer_evidence_span_tokens"] = tokenizer_evidence_span_tokens
    return replayed


def _validate_candidate_gates(
    candidate: dict[str, Any], attestation_key: bytes
) -> Verification:
    if not verify_attestation(
        candidate, attestation_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
    ):
        raise PromotionError("candidate has no valid producer attestation")
    length_bucket = str(candidate.get("length_bucket") or "")
    if length_bucket in EXACT_TOKEN_BAND_RANGES:
        tokens = candidate.get("tokenizer_context_tokens")
        if isinstance(tokens, int) and not isinstance(tokens, bool):
            reject_reason = exact_token_band_reject_reason(length_bucket, tokens)
            if reject_reason:
                raise PromotionError(reject_reason)
    expected_composition = _COMPOSITION_BY_VIEW.get(str(candidate.get("view") or ""))
    if candidate.get("composition_method") != expected_composition:
        raise PromotionError("candidate view and composition do not match")
    try:
        verification = Verification.model_validate(candidate.get("verification"))
    except Exception as error:
        raise PromotionError("candidate verification is malformed") from error
    if (
        verification.production_mode
        or not verification.candidate_mode
        or not verification.all_green()
    ):
        raise PromotionError("candidate verification gates are not green")
    if not verification.surface_match:
        raise PromotionError("candidate counterfactual surface gate is not green")
    view = candidate.get("view_verification")
    if not isinstance(view, dict):
        raise PromotionError("candidate view verification is missing")
    required = (
        "production_eligible",
        "essential_present",
        "semantic_text_grounded",
        "classification_ok",
        "global_proof_green",
    )
    if any(view.get(field) is not True for field in required):
        raise PromotionError("candidate view verification gates are not green")
    answer = str(candidate.get("answer") or "")
    if (
        not answer
        or view.get("expected_answer") != answer
        or view.get("strict_replay_answer") != answer
    ):
        raise PromotionError("candidate view answer does not match replay metadata")
    return verification


def _validate_external_ranking(
    candidate: dict[str, Any], ranking: dict[str, Any], attestation_key: bytes
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not verify_attestation(ranking, attestation_key, purpose=DENSE_RANKING_PURPOSE):
        raise PromotionError("dense ranking attestation is invalid")
    if ranking.get("schema_version") != DENSE_RANKING_SCHEMA:
        raise PromotionError("unsupported dense ranking schema")
    if ranking.get("ranker_type") != "dense_embedding":
        raise PromotionError("ranker_type must be dense_embedding")
    if ranking.get("query_id") != candidate.get("query_id"):
        raise PromotionError("ranking query id does not match candidate")
    if ranking.get("candidate_sha256") != candidate_sha256(candidate):
        raise PromotionError("ranking candidate digest does not match candidate")
    if ranking.get("query_sha256") != _sha256_text(
        str(candidate.get("question") or "")
    ):
        raise PromotionError("ranking query digest does not match candidate")

    raw_model = ranking.get("model")
    if not isinstance(raw_model, dict):
        raise PromotionError("dense model identity is missing")
    model: dict[str, Any] = {
        field: str(raw_model.get(field) or "")
        for field in (
            "provider",
            "model_id",
            "revision",
            "backend",
            "score_metric",
        )
    }
    if any(not model[field] for field in model):
        raise PromotionError("dense model identity is incomplete")
    if not _HEX_REVISION.fullmatch(model["revision"]):
        raise PromotionError("dense ranking requires a pinned model revision")
    if model["score_metric"] not in {"cosine_similarity", "dot_product"}:
        raise PromotionError("dense ranking score metric is unsupported")
    if tuple(model.values()) not in _APPROVED_DENSE_MODELS:
        raise PromotionError("dense ranking model tuple is not approved")
    chunking = raw_model.get("chunking")
    expected_chunking = {
        "strategy": "tokenizer_token_windows",
        "max_tokens": 192,
        "overlap_tokens": 32,
        "aggregation": "max_similarity",
    }
    if chunking != expected_chunking:
        raise PromotionError("dense ranking chunking policy is unsupported")
    model["chunking"] = expected_chunking

    bindings = _artifact_bindings(candidate)
    expected = {item["artifact_id"]: item for item in bindings}
    raw_artifacts = ranking.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(expected):
        raise PromotionError("dense ranking must bind the complete artifact pool")
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for expected_rank, item in enumerate(raw_artifacts, start=1):
        if not isinstance(item, dict) or item.get("rank") != expected_rank:
            raise PromotionError("dense ranking ranks must be contiguous and ordered")
        artifact_id = str(item.get("artifact_id") or "")
        if artifact_id in seen or artifact_id not in expected:
            raise PromotionError("dense ranking artifact ids do not match candidate")
        seen.add(artifact_id)
        if item.get("text_sha256") != expected[artifact_id]["text_sha256"]:
            raise PromotionError("dense ranking artifact text digest does not match")
        try:
            score = float(item["score"])
        except (KeyError, TypeError, ValueError) as error:
            raise PromotionError("dense ranking score is invalid") from error
        if not math.isfinite(score):
            raise PromotionError("dense ranking score is invalid")
        chunk_count = item.get("chunk_count")
        if not isinstance(chunk_count, int) or chunk_count < 1:
            raise PromotionError("dense ranking artifact chunk count is invalid")
        ranked.append(
            {
                "rank": expected_rank,
                "artifact_id": artifact_id,
                "text_sha256": str(item["text_sha256"]),
                "score": score,
                "chunk_count": chunk_count,
            }
        )
    if seen != set(expected):
        raise PromotionError("dense ranking artifact pool is incomplete")
    if any(
        ranked[index]["score"] < ranked[index + 1]["score"]
        for index in range(len(ranked) - 1)
    ):
        raise PromotionError("dense ranking scores are not in descending order")
    return ranked, model


def create_dense_audit(
    candidate: dict[str, Any],
    external_ranking: dict[str, Any],
    attestation_key: bytes | None = None,
    *,
    k: int = 3,
    episode_bundle_path: Path | None = None,
    source_bundle_path: Path | None = None,
    candidate_attestation_key: bytes | None = None,
    ranking_attestation_key: bytes | None = None,
    audit_attestation_key: bytes | None = None,
    episode_attestation_key: bytes | None = None,
    source_attestation_key: bytes | None = None,
) -> dict[str, Any]:
    """Validate an external dense ranking and attest its strict top-k replay."""
    candidate_key = candidate_attestation_key or attestation_key
    ranking_key = ranking_attestation_key or attestation_key
    audit_key = audit_attestation_key or attestation_key
    if candidate_key is None or ranking_key is None or audit_key is None:
        raise PromotionError("dense audit attestation keys are incomplete")
    _validate_candidate_gates(candidate, candidate_key)
    ranked, model = _validate_external_ranking(candidate, external_ranking, ranking_key)
    if k < 1 or k > len(ranked):
        raise PromotionError("dense top-k is outside the ranked artifact pool")
    world, spec, artifacts, counterpart, bundle_bindings = _reconstruct_candidate(
        candidate,
        episode_bundle_path,
        episode_attestation_key,
        source_bundle_path,
        source_attestation_key,
    )
    replayed_verification, replayed_notes, replay_tokenizer = (
        _independent_verification_replay(candidate, world, spec, artifacts, counterpart)
    )
    replayed_quality = _replayed_quality_metrics(
        candidate,
        world,
        spec,
        artifacts,
        replayed_notes,
        token_counter=_token_counter_for(replay_tokenizer),
    )
    replayed_task = _replayed_task_metadata(candidate, world, spec, artifacts)
    replayed_source = _replayed_source_metadata(world, spec, artifacts)
    verification_replay_sha256 = _canonical_sha256(
        {
            "verification": replayed_verification.model_dump(),
            "notes": replayed_notes,
        }
    )
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    top_k = ranked[:k]
    selected = [by_id[item["artifact_id"]] for item in top_k]
    prefix_answers = [
        _strict_answer(candidate, world, spec, selected[:prefix])
        for prefix in range(1, len(selected) + 1)
    ]
    dense_answer = prefix_answers[-1]
    expected_answer = str(candidate.get("answer") or "")
    if expected_answer in prefix_answers:
        raise PromotionError("dense top-k solves the candidate")
    payload: dict[str, Any] = {
        "schema_version": PROMOTION_SCHEMA,
        "query_id": candidate["query_id"],
        "candidate_sha256": candidate_sha256(candidate),
        "ranking_sha256": _canonical_sha256(external_ranking),
        "ranker_type": "dense_embedding",
        "model": model,
        "k": k,
        "top_k": top_k,
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "strict_replay_answer": dense_answer,
        "strict_replay_prefix_answers": prefix_answers,
        "expected_answer": expected_answer,
        "embedding_topk_insufficient": True,
        "verification_replay_sha256": verification_replay_sha256,
        "near_dup_sentence_ratio": replayed_quality["near_dup_sentence_ratio"],
        "strict_growth_metrics": _strict_growth_metrics(
            replayed_quality, replayed_task, replayed_source
        ),
    }
    exact_replay = replayed_notes.get("exact_token_replay")
    if isinstance(exact_replay, dict):
        payload["tokenizer_asset_manifest_sha256"] = exact_replay[
            "tokenizer_asset_manifest_sha256"
        ]
    payload.update(bundle_bindings)
    return attach_attestation(payload, audit_key, purpose=DENSE_AUDIT_PURPOSE)


def promote_candidate(
    candidate: dict[str, Any],
    dense_audit: dict[str, Any],
    attestation_key: bytes | None = None,
    *,
    episode_bundle_path: Path | None = None,
    source_bundle_path: Path | None = None,
    candidate_attestation_key: bytes | None = None,
    audit_attestation_key: bytes | None = None,
    promotion_attestation_key: bytes | None = None,
    episode_attestation_key: bytes | None = None,
    source_attestation_key: bytes | None = None,
    expected_split: str | None = None,
    release_selection_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strictly replay an audited candidate and sign a train-ready SFT row."""
    candidate_key = candidate_attestation_key or attestation_key
    audit_key = audit_attestation_key or attestation_key
    promotion_key = promotion_attestation_key or attestation_key
    if candidate_key is None or audit_key is None or promotion_key is None:
        raise PromotionError("promotion attestation keys are incomplete")
    _validate_candidate_gates(candidate, candidate_key)
    if not verify_attestation(dense_audit, audit_key, purpose=DENSE_AUDIT_PURPOSE):
        raise PromotionError("dense audit has no valid producer attestation")
    digest = candidate_sha256(candidate)
    if dense_audit.get("candidate_sha256") != digest:
        raise PromotionError("dense audit candidate digest does not match candidate")
    if (
        dense_audit.get("schema_version") != PROMOTION_SCHEMA
        or dense_audit.get("query_id") != candidate.get("query_id")
        or dense_audit.get("ranker_type") != "dense_embedding"
        or dense_audit.get("embedding_topk_insufficient") is not True
        or dense_audit.get("strict_replay_revision") != STRICT_REPLAY_REVISION
        or dense_audit.get("expected_answer") != candidate.get("answer")
    ):
        raise PromotionError("dense audit metadata does not match candidate")
    model = dense_audit.get("model")
    if (
        not isinstance(model, dict)
        or any(
            not str(model.get(field) or "")
            for field in ("provider", "model_id", "revision", "backend", "score_metric")
        )
        or not _HEX_REVISION.fullmatch(str(model.get("revision") or ""))
    ):
        raise PromotionError("dense audit model identity is malformed")

    world, spec, artifacts, counterpart, bundle_bindings = _reconstruct_candidate(
        candidate,
        episode_bundle_path,
        episode_attestation_key,
        source_bundle_path,
        source_attestation_key,
    )
    verification, replayed_notes, replay_tokenizer = _independent_verification_replay(
        candidate, world, spec, artifacts, counterpart
    )
    verification_replay_sha256 = _canonical_sha256(
        {"verification": verification.model_dump(), "notes": replayed_notes}
    )
    if dense_audit.get("verification_replay_sha256") != verification_replay_sha256:
        raise PromotionError("dense audit verification replay binding mismatch")
    exact_replay = replayed_notes.get("exact_token_replay")
    if isinstance(exact_replay, dict) and (
        dense_audit.get("tokenizer_asset_manifest_sha256")
        != exact_replay.get("tokenizer_asset_manifest_sha256")
        or candidate.get("tokenizer_asset_manifest_sha256")
        != exact_replay.get("tokenizer_asset_manifest_sha256")
    ):
        raise PromotionError("dense audit tokenizer asset binding mismatch")
    replayed_quality = _replayed_quality_metrics(
        candidate,
        world,
        spec,
        artifacts,
        replayed_notes,
        token_counter=_token_counter_for(replay_tokenizer),
    )
    replayed_task = _replayed_task_metadata(candidate, world, spec, artifacts)
    replayed_source = _replayed_source_metadata(world, spec, artifacts)
    if (
        _audited_near_dup_sentence_ratio(dense_audit)
        != replayed_quality["near_dup_sentence_ratio"]
    ):
        raise PromotionError("dense audit near-duplicate replay binding mismatch")
    if dense_audit.get("strict_growth_metrics") != _strict_growth_metrics(
        replayed_quality, replayed_task, replayed_source
    ):
        raise PromotionError("dense audit strict-growth replay binding mismatch")
    selected_split = expected_split
    selection_digest = ""
    if release_selection_receipt is not None:
        if not verify_attestation(
            release_selection_receipt,
            audit_key,
            purpose=RELEASE_SELECTION_PURPOSE,
        ):
            raise PromotionError("release world selection receipt is invalid")
        selected_ids = release_selection_receipt.get("selected_candidate_sha256")
        split_by_world = release_selection_receipt.get("split_by_world")
        audit_sha256_by_candidate = release_selection_receipt.get(
            "audit_sha256_by_candidate"
        )
        mapped_split = (
            split_by_world.get(str(candidate.get("world_id") or ""))
            if isinstance(split_by_world, dict)
            else None
        )
        if (
            release_selection_receipt.get("schema_version") != RELEASE_SELECTION_SCHEMA
            or not isinstance(selected_ids, list)
            or not isinstance(audit_sha256_by_candidate, dict)
            or digest not in selected_ids
            or audit_sha256_by_candidate.get(digest)
            != serialized_row_sha256(dense_audit)
            or mapped_split not in {"train", "eval"}
            or (expected_split is not None and expected_split != mapped_split)
            or (
                isinstance(exact_replay, dict)
                and release_selection_receipt.get("tokenizer_asset_manifest_sha256")
                != exact_replay.get("tokenizer_asset_manifest_sha256")
            )
        ):
            raise PromotionError("candidate is not bound by release world selection")
        selected_split = str(mapped_split)
        selection_digest = serialized_row_sha256(release_selection_receipt)
    elif selected_split is not None:
        if selected_split not in {"train", "eval"}:
            raise PromotionError("trusted split policy is invalid")
        if candidate.get("split") != selected_split:
            raise PromotionError("candidate split does not match trusted split policy")
    for field in ("episode_replay_bundle", "source_workflow_bundle"):
        if dense_audit.get(field) != bundle_bindings.get(field):
            raise PromotionError(f"dense audit {field} binding mismatch")
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    raw_top_k = dense_audit.get("top_k")
    if (
        not isinstance(raw_top_k, list)
        or not raw_top_k
        or dense_audit.get("k") != len(raw_top_k)
    ):
        raise PromotionError("dense audit top-k is missing")
    bindings = {item["artifact_id"]: item for item in _artifact_bindings(candidate)}
    top_k_ids: list[str] = []
    for expected_rank, item in enumerate(raw_top_k, start=1):
        if not isinstance(item, dict) or item.get("rank") != expected_rank:
            raise PromotionError("dense audit top-k ranks are malformed")
        artifact_id = str(item.get("artifact_id") or "")
        binding = bindings.get(artifact_id)
        if (
            binding is None
            or artifact_id in top_k_ids
            or item.get("text_sha256") != binding["text_sha256"]
        ):
            raise PromotionError("dense audit top-k binding is malformed")
        top_k_ids.append(artifact_id)
    try:
        selected = [by_id[artifact_id] for artifact_id in top_k_ids]
    except (KeyError, TypeError) as error:
        raise PromotionError("dense audit top-k cannot be replayed") from error
    dense_answer = _strict_answer(candidate, world, spec, selected)
    if dense_answer != dense_audit.get(
        "strict_replay_answer"
    ) or dense_answer == candidate.get("answer"):
        raise PromotionError("dense audit strict replay mismatch")

    strict_answer = _strict_answer(candidate, world, spec, artifacts)
    expected_answer = str(candidate.get("answer") or "")
    if strict_answer != expected_answer:
        raise PromotionError("strict replay answer mismatch")

    verification.production_mode = True
    verification.candidate_mode = False
    verification.embedding_topk_insufficient = True
    if not verification.all_green():
        raise PromotionError("production verification gates are not green")
    promoted = {key: value for key, value in candidate.items() if key != "attestation"}
    promoted.update(replayed_quality)
    promoted.update(replayed_task)
    promoted.update(replayed_source)
    if selected_split is not None:
        promoted["split"] = selected_split
        promoted["split_strategy"] = "world"
        promoted["holdout"] = {
            "strategy": "world",
            "group_id": _sha256_text(f"world|{world.world_id}")[:16],
        }
    promoted["source_origins"] = sorted(
        {
            str(item.get("source_origin") or "")
            for item in promoted.get("artifact_classification") or []
            if isinstance(item, dict) and item.get("source_origin")
        }
    )
    promoted["workflow_kinds"] = sorted(
        {
            str(item.get("workflow_kind") or "")
            for item in promoted.get("artifact_classification") or []
            if isinstance(item, dict) and item.get("workflow_kind")
        }
    )
    promoted["data_stage"] = "train_ready"
    diagnostic_metadata = local_probe_diagnostic_metadata()
    promoted.update(diagnostic_metadata)
    promoted["verification"] = verification.model_dump()
    view = dict(promoted["view_verification"])
    view.update(
        {
            "strict_replay_answer": strict_answer,
            "expected_answer": expected_answer,
            "global_proof_green": True,
            "content_gate_eligible": True,
            "production_eligible": not bool(diagnostic_metadata),
        }
    )
    promoted["view_verification"] = view
    promoted["promotion"] = {
        "schema_version": PROMOTION_SCHEMA,
        "candidate_sha256": digest,
        "dense_audit_sha256": serialized_row_sha256(dense_audit),
        "dense_model_provider": model["provider"],
        "dense_model_id": model["model_id"],
        "dense_model_revision": model["revision"],
        "dense_model_backend": model["backend"],
        "dense_score_metric": model["score_metric"],
        "dense_chunking": model["chunking"],
        "dense_ranking_sha256": dense_audit["ranking_sha256"],
        "dense_top_k": dense_audit["k"],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "strict_replay_answer": strict_answer,
        "real_source_verified": replayed_source["real_source_verified"],
    }
    if isinstance(exact_replay, dict):
        promoted["promotion"]["tokenizer_asset_manifest_sha256"] = exact_replay[
            "tokenizer_asset_manifest_sha256"
        ]
    if selection_digest:
        promoted["promotion"]["release_selection_sha256"] = selection_digest
    promoted["promotion"].update(bundle_bindings)
    signed = attach_attestation(promoted, promotion_key, purpose="sft_row")
    errors = sft_row_errors(signed, attestation_key=promotion_key)
    if errors:
        raise PromotionError("promoted row contract failed: " + ",".join(errors))
    return signed
