"""Fail-closed dense-retrieval audit and SFT candidate promotion."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.attestation import (
    attach_attestation,
    canonical_attested_payload,
    verify_attestation,
    verify_attestation_identity,
)
from longworld.core.engine import answer_from_artifacts
from longworld.core.graph import graph_stats
from longworld.core.pack import (
    SEP,
    compute_view_metrics,
    dependency_class_for_view,
    dependency_evidence_ids,
    estimate_tokens,
    join_artifacts,
    wrap_prompt,
)
from longworld.core.production_trust import (
    verify_embedded_production_approval_from_env,
)
from longworld.core.realworkflow import (
    EPISODE_REPLAY_BUNDLE_SCHEMA,
    load_episode_replay_bundle,
)
from longworld.core.record_contract import sft_row_errors
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
    SOURCE_WORKFLOW_ADAPTER_REVISION,
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
    load_source_workflow_bundle,
)
from longworld.core.taxonomy import artifact_classification
from longworld.core.topology import canonical_topology, topology_family
from longworld.core.verify import (
    Verification,
    counterfactual_shortcuts_insufficient,
    query_adjacent_window_artifact_ids,
    verify_question,
)
from longworld.core.views import render_cf_view
from longworld.domains.company.queries import QuerySpec

DENSE_AUDIT_PURPOSE = "dense_retrieval_audit"
DENSE_RANKING_PURPOSE = "dense_ranking"
CANDIDATE_ATTESTATION_PURPOSE = "candidate_row"
DENSE_RANKING_SCHEMA = "dense-ranking-v2"
PROMOTION_SCHEMA = "train-ready-promotion-v1"
STRICT_REPLAY_REVISION = "longworld-strict-replay-v3"
REAL_REPLAY_BUNDLE_PURPOSE = "episode_replay_bundle"
QUALITY_REPORT_PURPOSE = "quality_report"
QUALITY_REPORT_BINDING_REVISION = "longworld-quality-binding-v2"
RELEASE_SELECTION_SCHEMA = "longworld-release-world-selection-v1"
RELEASE_SELECTION_PURPOSE = "release_world_selection"
RELEASE_GATE_SCHEMA = "longworld-release-gate-pass-v1"
RELEASE_GATE_PURPOSE = "release_gate_pass"
RELEASE_GATE_REVISION = "longworld-quality-gate-v2"

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


def _canonical_sha256(value: dict[str, Any]) -> str:
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
        and candidate.get("source_relation_edges")
        and any(
            isinstance(candidate.get(field), dict)
            for field in ("episode_replay_bundle", "source_workflow_bundle")
        )
        and isinstance(classifications, list)
        and any(
            isinstance(item, dict)
            and item.get("source_origin")
            in {"real_public", "real_private_export", "real_derived"}
            and item.get("workflow_kind") == "hybrid_causal"
            for item in classifications
        )
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
    if (
        receipt.get("schema_version") != RELEASE_GATE_SCHEMA
        or receipt.get("gate_revision") != RELEASE_GATE_REVISION
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
    if profile.split_strategy != "world_atomic_hash_v1":
        raise PromotionError("release profile split strategy is unsupported")
    if (
        profile.min_real_train_worlds > profile.min_train_worlds
        or profile.min_real_eval_worlds > profile.min_eval_worlds
    ):
        raise PromotionError("release profile real-world quotas exceed split quotas")
    by_world: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    candidate_digests: list[str] = []
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
    eligible_worlds = [
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
    target = profile.expected_promoted_worlds
    if len(eligible_worlds) < target:
        raise PromotionError(
            f"insufficient fully audited worlds: {len(eligible_worlds)}<{target}"
        )
    real_eligible_worlds = [
        world_id
        for world_id in eligible_worlds
        if any(
            _candidate_has_verified_real_source(candidate)
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
    required_real_worlds = sorted(real_eligible_worlds, key=select_key)[
        :min_real_worlds
    ]
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
        required_real_set = set(required_real_worlds)
        for domain in domain_quotas:
            domain_real_worlds = sorted(
                (
                    world_id
                    for world_id in real_eligible_worlds
                    if domains_by_world[world_id] == domain
                ),
                key=select_key,
            )
            if domain_real_worlds and required_real_set.isdisjoint(domain_real_worlds):
                required_real_worlds.append(domain_real_worlds[0])
                required_real_set.add(domain_real_worlds[0])
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
    real_eval_worlds = set(
        sorted(
            selected_real_worlds,
            key=lambda world_id: _sha256_text(f"{profile_digest}|real-eval|{world_id}"),
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
    selected = [
        candidate
        for world_id in selected_worlds
        for _digest, candidate in by_world[world_id]
    ]
    selected_ids = sorted(candidate_sha256(candidate) for candidate in selected)
    receipt = attach_attestation(
        {
            "schema_version": RELEASE_SELECTION_SCHEMA,
            "release_profile_id": release_profile_id,
            "release_profile_sha256": profile_digest,
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
            "n_candidate_worlds": len(by_world),
            "n_eligible_worlds": len(eligible_worlds),
            "n_selected_worlds": len(selected_worlds),
            "split_strategy": profile.split_strategy,
            "promoted_domain_world_quotas": domain_quotas,
            "selected_worlds_by_domain": selected_worlds_by_domain,
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
    for candidate in candidates:
        if (
            not verify_attestation(
                candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
            or candidate.get("schema_version") != candidate_report.get("schema_version")
            or candidate.get("data_product") != candidate_report.get("data_product")
            or candidate.get("data_stage") != "candidate"
        ):
            raise PromotionError("candidate product identity is inconsistent")
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
    profile = release_profile(release_profile_id)
    domain_quotas = dict(profile.promoted_domain_world_quotas)
    selection_digest = ""
    selected_worlds_by_domain: dict[str, list[str]] = {}
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
        selected_real_worlds = {
            str(candidate.get("world_id") or "")
            for candidate in selected_candidates
            if _candidate_has_verified_real_source(candidate)
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
    candidate_by_digest = {
        candidate_sha256(candidate): candidate for candidate in candidates
    }
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
    if any(
        row.get("schema_version") != candidate_report.get("schema_version")
        or row.get("data_product") != candidate_report.get("data_product")
        for row in rows
    ):
        raise PromotionError("promoted product identity is inconsistent")
    if any(row.get("split") not in {"train", "eval"} for row in rows):
        raise PromotionError("promoted rows have an invalid split")
    payload = {
        "schema_version": str(candidate_report.get("schema_version") or ""),
        "data_product": str(candidate_report.get("data_product") or ""),
        "data_stage": "train_ready",
        "release_profile_id": release_profile_id,
        "release_profile_sha256": profile_digest,
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
            or source_binding["adapter_revision"] != SOURCE_WORKFLOW_ADAPTER_REVISION
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
            materialized = materialize(
                seed,
                n_parallel=0,
                n_pulses=0,
                domain=domain,
                n_workstreams=n_workstreams,
                source_workflows=domain_workflows,
                include_program_joins=include_program_joins,
            )
    else:
        materialized = materialize(
            seed,
            n_parallel=0,
            n_pulses=0,
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


def _independent_verification_replay(
    candidate: dict[str, Any],
    world: Any,
    spec: QuerySpec,
    artifacts: list[Artifact],
    counterpart: list[Artifact],
) -> tuple[Verification, dict[str, Any]]:
    factual, counterfactual = _parallel_dossiers(candidate, artifacts, counterpart)
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
    )
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
        world, spec, counterfactual, retrieval_top_k=3
    )
    notes["counterfactual_shortcuts"] = cf_shortcut_notes
    if candidate.get("view") == "cf" and not cf_shortcuts_green:
        raise PromotionError("counterfactual dossier has a short-context shortcut")
    return verification, notes


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
    program_id = _sha256_text(
        json.dumps(
            {
                "query_type": spec.query_type,
                "program_ops": list(spec.program_ops or []),
                "proof_depth": int(spec.proof_depth or 0),
                "cf_op": spec.cf_op,
            },
            sort_keys=True,
        )
    )[:20]
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
        "base_task_id": _sha256_text(f"{world.world_id}|{spec.query_id}")[:20],
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
        "graph": graph_stats(world, spec),
        "hop_count": int(spec.proof_depth or 0),
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
        "dossier_id": "dossier identity",
        "semantic_growth_group_id": "semantic growth group",
        "strict_support_event_count": "strict support count",
        "cf_answer": "counterfactual answer",
        "cf_op": "counterfactual operator",
        "proof_graph": "proof graph",
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
    edges: list[dict[str, str]] = []
    for child in events.values():
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
                    "parent_source_url": str(parent.params["source_url"]),
                    "child_source_url": str(child.params["source_url"]),
                }
            )
    for relation in all_events.values():
        if relation.type != "arxiv_revision_relation":
            continue
        edges.append(
            {
                "parent_record_id": str(relation.params.get("target_record_id") or ""),
                "child_record_id": str(relation.params.get("source_record_id") or ""),
                "relation": str(relation.params.get("relation_kind") or ""),
                "parent_source_url": str(
                    relation.params.get("target_source_url") or ""
                ),
                "child_source_url": str(relation.params.get("source_url") or ""),
            }
        )
    proof_artifact_ids = set(spec.essential_artifact_ids) | {
        artifact.artifact_id
        for artifact in artifacts
        if sufficient_event_ids.intersection(artifact.reveals_events)
    }
    real_origins = {"real_public", "real_private_export", "real_derived"}
    real_families: set[str] = set()
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
    relation_id = (
        _sha256_text(json.dumps(edges, sort_keys=True, ensure_ascii=False))[:20]
        if edges
        else ""
    )
    return {
        "real_source_verified": bool(edges and real_families),
        "real_source_family_ids": sorted(real_families),
        "source_relation_edges": edges,
        "source_relation_id": relation_id,
        "base_task_id": _sha256_text(f"{world.world_id}|{spec.query_id}")[:20],
    }


def _replayed_quality_metrics(
    candidate: dict[str, Any],
    world: Any,
    spec: QuerySpec,
    artifacts: list[Artifact],
) -> dict[str, Any]:
    """Recompute serialized-view quality fields without trusting candidate values."""
    document_context = join_artifacts(artifacts)
    context = wrap_prompt(
        spec.question, document_context, str(candidate["query_timing"])
    )
    essential_ids = set(spec.essential_artifact_ids)
    metrics = compute_view_metrics(
        artifacts,
        dependency_evidence_ids(artifacts, spec),
        query_timing=str(candidate["query_timing"]),
        context=context,
    )
    if (
        candidate.get("length_bucket") != metrics.length_bucket
        or candidate.get("position_bucket") != metrics.position_bucket
    ):
        raise PromotionError(
            "candidate view length or position metadata is not truthful"
        )

    semantic_tokens = {
        "event_bearing": 0,
        "internal": 0,
        "generic_background": 0,
        "proof_bearing": 0,
        "causal_supporting": 0,
    }
    real_source_tokens = 0
    real_origins = {"real_public", "real_private_export", "real_derived"}
    bound_workflow_ids = {world.world_id} | {
        str(event.params["workflow_id"])
        for event in world.events
        if event.params.get("workflow_id")
    }
    for artifact in artifacts:
        tokens = estimate_tokens(artifact.text)
        classification = artifact_classification(artifact)
        same_workflow = classification.workflow_id in bound_workflow_ids or (
            classification.workflow_kind.value == "unclassified"
            and artifact.artifact_id.startswith(world.world_id)
        )
        if same_workflow and artifact.reveals_events:
            semantic_tokens["event_bearing"] += tokens
        elif same_workflow and artifact.artifact_id in essential_ids:
            semantic_tokens["internal"] += tokens
        else:
            semantic_tokens["generic_background"] += tokens
        if artifact.artifact_id in essential_ids:
            semantic_tokens["proof_bearing"] += tokens
        elif set(spec.sufficient_event_ids).intersection(artifact.reveals_events):
            semantic_tokens["causal_supporting"] += tokens
        if classification.source_origin.value in real_origins:
            real_source_tokens += tokens

    difficulty = dict(candidate.get("difficulty") or {})
    difficulty.update(
        {
            "context_tokens": metrics.context_tokens,
            "max_evidence_distance": metrics.max_evidence_distance,
            "proof_depth": int(spec.proof_depth or 0),
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
    return {
        "context": context,
        "document_context": document_context,
        "difficulty": difficulty,
        "length_bucket": metrics.length_bucket,
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
        "real_source_token_ratio": round(
            real_source_tokens / max(1, metrics.context_tokens), 4
        ),
    }


def _validate_candidate_gates(
    candidate: dict[str, Any], attestation_key: bytes
) -> Verification:
    if not verify_attestation(
        candidate, attestation_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
    ):
        raise PromotionError("candidate has no valid producer attestation")
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
    replayed_verification, replayed_notes = _independent_verification_replay(
        candidate, world, spec, artifacts, counterpart
    )
    replayed_quality = _replayed_quality_metrics(candidate, world, spec, artifacts)
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
    }
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
    verification, replayed_notes = _independent_verification_replay(
        candidate, world, spec, artifacts, counterpart
    )
    verification_replay_sha256 = _canonical_sha256(
        {"verification": verification.model_dump(), "notes": replayed_notes}
    )
    if dense_audit.get("verification_replay_sha256") != verification_replay_sha256:
        raise PromotionError("dense audit verification replay binding mismatch")
    replayed_quality = _replayed_quality_metrics(candidate, world, spec, artifacts)
    if (
        _audited_near_dup_sentence_ratio(dense_audit)
        != replayed_quality["near_dup_sentence_ratio"]
    ):
        raise PromotionError("dense audit near-duplicate replay binding mismatch")
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
    replayed_task = _replayed_task_metadata(candidate, world, spec, artifacts)
    promoted.update(replayed_task)
    replayed_source = _replayed_source_metadata(world, spec, artifacts)
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
    promoted["verification"] = verification.model_dump()
    view = dict(promoted["view_verification"])
    view.update(
        {
            "strict_replay_answer": strict_answer,
            "expected_answer": expected_answer,
            "global_proof_green": True,
            "production_eligible": True,
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
    if selection_digest:
        promoted["promotion"]["release_selection_sha256"] = selection_digest
    promoted["promotion"].update(bundle_bindings)
    signed = attach_attestation(promoted, promotion_key, purpose="sft_row")
    errors = sft_row_errors(signed, attestation_key=promotion_key)
    if errors:
        raise PromotionError("promoted row contract failed: " + ",".join(errors))
    return signed
