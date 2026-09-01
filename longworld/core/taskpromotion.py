"""Fail-closed dense audit and promotion for registered task replay adapters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import date
from typing import Any

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    attach_attestation,
    local_probe_diagnostic_metadata,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.domainhistory import (
    audit_kev_pipeline_candidate,
    replay_kev_pipeline_candidate,
)
from longworld.core.financehistory import (
    audit_finance_pipeline_candidate,
    replay_finance_pipeline_selection,
)
from longworld.core.macrovintage import (
    audit_macro_vintage_pipeline_candidate,
    replay_macro_vintage_pipeline_selection,
)
from longworld.core.pack import SEP
from longworld.core.promotion import (
    _APPROVED_EXACT_TOKENIZERS,
    CANDIDATE_ATTESTATION_PURPOSE,
    DENSE_AUDIT_PURPOSE,
    PROMOTION_SCHEMA,
    RELEASE_SELECTION_PURPOSE,
    RELEASE_SELECTION_SCHEMA,
    PromotionError,
    _load_replay_tokenizer_uncached,
    _resolved_local_tokenizer_revision,
    _token_counter_for,
    _validate_external_ranking,
    candidate_sha256,
    serialized_row_sha256,
    task_semantic_commitment_sha256_from_audit,
)
from longworld.core.record_contract import (
    EXACT_TOKEN_BAND_RANGES,
    STRICT_REPLAY_REVISION,
    exact_token_band_reject_reason,
    exact_token_metadata_valid,
    sft_row_errors,
)
from longworld.core.render import Artifact
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.taskproof import TaskProofError, compute_task_proof
from longworld.core.taskreplaysidecar import (
    CYBER_KEV_TASK_REPLAY_ADAPTER,
    FINANCE_TASK_REPLAY_ADAPTER,
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    TASK_REPLAY_SIDECAR_PURPOSE,
    LoadedTaskReplaySidecar,
    task_candidate_content_commitment,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from longworld.core.verify import Verification

TokenCounter = Callable[[str], int]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DENSE_TOP_K = 3
_CANDIDATE_FORBIDDEN_PROOF_FIELDS = frozenset(
    {
        "adapter_audit",
        "adapter_audit_sha256",
        "task_proof",
        "task_proof_receipt",
        "task_proof_sha256",
        "task_quality_metadata",
        "task_semantic_commitment_sha256",
        "strict_replay_prefix_answers",
        "counterfactual_replay_answer",
        "full_pool_strict_replay_sufficient",
        "task_replay_payload_sha256",
        "source_binding_sha256",
        "strict_growth_metrics",
        "verification",
        "verification_replay_sha256",
        "view_verification",
        "promotion",
    }
)


def task_sidecar_token_counter(
    sidecar: LoadedTaskReplaySidecar,
) -> TokenCounter:
    """Load the exact approved tokenizer bound by a verified task sidecar."""
    model_id = str(sidecar.replay_payload.get("tokenizer_model_id") or "")
    revision = str(sidecar.replay_payload.get("tokenizer_revision") or "")
    asset_digest = str(
        sidecar.replay_payload.get("tokenizer_asset_manifest_sha256") or ""
    )
    if (model_id, revision) not in _APPROVED_EXACT_TOKENIZERS:
        raise PromotionError("task sidecar exact tokenizer pin is not approved")
    try:
        loaded_revision = _resolved_local_tokenizer_revision(model_id, revision)
        observed_asset_digest = resolved_tokenizer_asset_manifest_sha256(
            model_id, revision
        )
        if loaded_revision != revision or observed_asset_digest != asset_digest:
            raise PromotionError("task sidecar exact tokenizer assets do not match")
        tokenizer = _load_replay_tokenizer_uncached(model_id, revision)
        loaded_asset_digest = resolved_tokenizer_asset_manifest_sha256(
            model_id, revision
        )
    except PromotionError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        raise PromotionError("task sidecar exact tokenizer cannot be loaded") from error
    if loaded_asset_digest != observed_asset_digest:
        raise PromotionError("task sidecar exact tokenizer assets do not match")
    counter = _token_counter_for(tokenizer)
    if counter is None:
        raise PromotionError("task sidecar exact tokenizer is unavailable")
    counter.offset_tokenizer = tokenizer
    return counter


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _sidecar_binding(sidecar: LoadedTaskReplaySidecar) -> dict[str, str]:
    return {
        "adapter_id": sidecar.adapter_id,
        "adapter_revision": sidecar.adapter_revision,
        "sidecar_schema_version": sidecar.sidecar_schema_version,
        "sha256": sidecar.sidecar_sha256,
    }


def _validate_loaded_sidecar(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    source_attestation_key: bytes,
) -> None:
    if hashlib.sha256(sidecar.raw_bytes).hexdigest() != sidecar.sidecar_sha256:
        raise PromotionError("task replay sidecar exact-byte binding is invalid")
    try:
        serialized = json.loads(sidecar.raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromotionError("task replay sidecar exact bytes are invalid") from error
    attestation = (
        serialized.get("attestation") if isinstance(serialized, dict) else None
    )
    if (
        not isinstance(serialized, dict)
        or not isinstance(attestation, dict)
        or attestation.get("scheme") != ATTESTATION_V2_SCHEME
        or attestation.get("role") != "source"
        or serialized != sidecar.signed_sidecar
        or serialized.get("adapter_id") != sidecar.adapter_id
        or serialized.get("adapter_revision") != sidecar.adapter_revision
        or serialized.get("schema_version") != sidecar.sidecar_schema_version
        or serialized.get("replay_payload") != sidecar.replay_payload
        or not verify_attestation(
            serialized,
            source_attestation_key,
            purpose=TASK_REPLAY_SIDECAR_PURPOSE,
        )
    ):
        raise PromotionError("task replay sidecar source attestation is invalid")
    if candidate.get("task_replay_sidecar") != _sidecar_binding(sidecar):
        raise PromotionError("candidate task replay sidecar binding is invalid")


def _validate_sidecar_payload(
    candidate: dict[str, Any], sidecar: LoadedTaskReplaySidecar
) -> None:
    payload = dict(sidecar.replay_payload)
    content_commitments = payload.pop("candidate_content_commitments", None)
    tokenizer_binding = {
        "tokenizer_model_id": candidate.get("tokenizer_model_id"),
        "tokenizer_revision": candidate.get("tokenizer_revision"),
        "tokenizer_asset_manifest_sha256": candidate.get(
            "tokenizer_asset_manifest_sha256"
        ),
    }
    if sidecar.registry_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
        if candidate.get("domain") != "cyber":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        replay_manifest = candidate.get("domain_history_replay_manifest")
        if not isinstance(source, dict) or not isinstance(replay_manifest, dict):
            raise PromotionError("candidate Cyber source binding is missing")
        expected = {
            "source_manifest_sha256": source.get("signed_manifest_sha256"),
            "source_response_sha256": source.get("retrieval_sha256"),
            "replay_manifest_sha256": replay_manifest.get("sha256"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
        if (
            replay_manifest.get("source_manifest_sha256")
            != expected["source_manifest_sha256"]
            or replay_manifest.get("source_response_sha256")
            != expected["source_response_sha256"]
            or replay_manifest.get("replay_revision") != expected["replay_revision"]
        ):
            raise PromotionError("candidate Cyber replay manifest is inconsistent")
    elif sidecar.registry_key == FINANCE_TASK_REPLAY_ADAPTER:
        if candidate.get("domain") != "finance":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        replay_contract = candidate.get("finance_replay_contract")
        if not isinstance(source, dict) or not isinstance(replay_contract, dict):
            raise PromotionError("candidate Finance source binding is missing")
        expected = {
            "signed_manifest_sha256": source.get("signed_manifest_sha256"),
            "source_family": source.get("source_family"),
            "authorization_record_id": source.get("authorization_record_id"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
        if (
            replay_contract.get("adapter_id") != sidecar.adapter_id
            or replay_contract.get("revision") != expected["replay_revision"]
        ):
            raise PromotionError("candidate Finance replay contract is inconsistent")
    elif sidecar.registry_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
        if candidate.get("domain") != "macro_economics":
            raise PromotionError("task replay adapter does not match candidate domain")
        source = candidate.get("source_binding")
        if not isinstance(source, dict):
            raise PromotionError("candidate Macro source binding is missing")
        fetch_receipt = payload.get("fetch_receipt")
        if (
            not isinstance(fetch_receipt, dict)
            or _canonical_sha256(fetch_receipt)
            != source.get("fetch_receipt_sha256")
        ):
            raise PromotionError("candidate Macro source receipt is inconsistent")
        expected = {
            "workflow_manifest_sha256": source.get("workflow_manifest_sha256"),
            "raw_source_sha256": source.get("raw_source_sha256"),
            "fetch_inventory_sha256": source.get("fetch_inventory_sha256"),
            "fetch_receipt": fetch_receipt,
            "source_families": source.get("source_families"),
            "authorization_record_id": source.get("authorization_record_id"),
            "replay_revision": candidate.get("strict_replay_revision"),
            **tokenizer_binding,
        }
    else:
        raise PromotionError("task replay adapter is not registered for promotion")
    if (
        expected != payload
        or expected.get("replay_revision") != sidecar.adapter_revision
        or _SHA256.fullmatch(str(expected.get("tokenizer_asset_manifest_sha256") or ""))
        is None
    ):
        raise PromotionError("task replay sidecar payload does not match candidate")
    commitment = task_candidate_content_commitment(candidate)
    if (
        not isinstance(content_commitments, list)
        or commitment not in content_commitments
    ):
        raise PromotionError("task replay sidecar does not bind candidate content")


def _validate_candidate_identity(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
    source_attestation_key: bytes,
) -> None:
    if not verify_attestation(
        candidate,
        candidate_attestation_key,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
    ):
        raise PromotionError("task candidate attestation is invalid")
    if _CANDIDATE_FORBIDDEN_PROOF_FIELDS.intersection(candidate):
        raise PromotionError("task candidate must not declare proof fields")
    if (
        candidate.get("data_stage") != "candidate"
        or candidate.get("training_objective") != "sft"
    ):
        raise PromotionError("task candidate lifecycle is invalid")
    _validate_loaded_sidecar(candidate, sidecar, source_attestation_key)
    _validate_sidecar_payload(candidate, sidecar)


def _adapter_audit(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    token_counter: TokenCounter | None,
) -> dict[str, bool]:
    if token_counter is None:
        raise PromotionError("task promotion requires an exact token counter")
    try:
        with sanitized_attestation_environment():
            observed_tokens = token_counter(str(candidate.get("context") or ""))
            declared_tokens = candidate.get("tokenizer_context_tokens")
            length_bucket = str(candidate.get("length_bucket") or "")
            if (
                isinstance(declared_tokens, bool)
                or not isinstance(declared_tokens, int)
                or length_bucket not in EXACT_TOKEN_BAND_RANGES
                or not exact_token_metadata_valid(
                    candidate, require_asset_manifest=True
                )
                or observed_tokens != declared_tokens
                or exact_token_band_reject_reason(length_bucket, observed_tokens)
            ):
                raise PromotionError("task candidate exact token count does not replay")
            if sidecar.registry_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
                audit = audit_kev_pipeline_candidate(
                    candidate, token_counter=token_counter
                )
            elif sidecar.registry_key == FINANCE_TASK_REPLAY_ADAPTER:
                audit = audit_finance_pipeline_candidate(candidate)
            elif sidecar.registry_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
                audit = audit_macro_vintage_pipeline_candidate(candidate)
            else:
                raise PromotionError(
                    "task replay adapter is not registered for promotion"
                )
    except PromotionError:
        raise
    except Exception as error:
        raise PromotionError("task adapter audit could not be replayed") from error
    failed = sorted(name for name, passed in audit.items() if passed is not True)
    if failed:
        raise PromotionError("task adapter audit failed: " + ",".join(failed))
    return audit


def _replay_selection(
    candidate: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    with sanitized_attestation_environment():
        if sidecar.registry_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
            return replay_kev_pipeline_candidate(
                candidate,
                counterfactual=counterfactual,
                evidence_artifact_ids=artifact_ids,
            )
        if sidecar.registry_key == FINANCE_TASK_REPLAY_ADAPTER:
            return replay_finance_pipeline_selection(
                candidate,
                artifact_ids,
                counterfactual=counterfactual,
            )
        if sidecar.registry_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
            return replay_macro_vintage_pipeline_selection(
                candidate,
                artifact_ids,
                counterfactual=counterfactual,
            )
    raise PromotionError("task replay adapter is not registered for promotion")


def _artifact_bindings(candidate: dict[str, Any]) -> dict[str, str]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise PromotionError("task candidate artifact pool is missing")
    documents = document_context.split(SEP)
    if not classifications or len(classifications) != len(documents):
        raise PromotionError("task candidate artifact pool is unbound")
    bindings: dict[str, str] = {}
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, Mapping):
            raise PromotionError("task candidate classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id or artifact_id in bindings or not document.strip():
            raise PromotionError("task candidate artifact identities are invalid")
        bindings[artifact_id] = hashlib.sha256(document.encode()).hexdigest()
    return bindings


def _relation_endpoints(relation: object) -> tuple[str, str] | None:
    if isinstance(relation, Mapping):
        parent = relation.get("parent_record_id")
        child = relation.get("child_record_id")
        if isinstance(parent, str) and parent and isinstance(child, str) and child:
            return parent, child
    if (
        isinstance(relation, Sequence)
        and not isinstance(relation, (str, bytes))
        and len(relation) >= 2
        and isinstance(relation[0], str)
        and isinstance(relation[1], str)
        and relation[0]
        and relation[1]
    ):
        return relation[0], relation[1]
    return None


def _relation_proof_depth(relations: Sequence[object]) -> int:
    adjacency: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for relation in relations:
        endpoints = _relation_endpoints(relation)
        if endpoints is None:
            raise PromotionError("task replay relation edge is malformed")
        parent, child = endpoints
        nodes.update((parent, child))
        adjacency.setdefault(parent, set()).add(child)

    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def depth(node: str) -> int:
        if node in memo:
            return memo[node]
        if node in visiting:
            raise PromotionError("task replay relation graph contains a cycle")
        visiting.add(node)
        value = max((1 + depth(child) for child in adjacency.get(node, ())), default=0)
        visiting.remove(node)
        memo[node] = value
        return value

    return max(2, max((depth(node) for node in nodes), default=0))


def _task_selection_metrics(
    candidate: dict[str, Any],
    task_proof: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    token_counter: TokenCounter,
) -> dict[str, Any]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise PromotionError("task selection quality input is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications):
        raise PromotionError("task selection quality input is unbound")
    artifacts = [
        Artifact(
            artifact_id=str(classification.get("artifact_id") or ""),
            doc_type="source_record",
            time=date(1970, 1, 1),
            project=str(candidate.get("world_id") or ""),
            prefix="",
            reveals_events=[],
            text=document,
            facts=[],
        )
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    ]
    receipt = task_proof.get("task_proof_receipt")
    if len(artifacts) != len(documents) or not isinstance(receipt, dict):
        raise PromotionError("task selection growth metadata is invalid")
    essential_ids = receipt.get("essential_artifact_ids")
    if not isinstance(essential_ids, list) or not essential_ids:
        raise PromotionError("task proof essential artifact receipt is invalid")
    artifact_index = {
        str(classification.get("artifact_id") or ""): index
        for index, classification in enumerate(classifications)
        if isinstance(classification, Mapping)
    }
    if any(value not in artifact_index for value in essential_ids):
        raise PromotionError("task proof essential artifacts are unbound")
    essential_documents = [documents[artifact_index[value]] for value in essential_ids]
    internal_tokens = token_counter(document_context)
    proof_tokens = token_counter(SEP.join(essential_documents))

    replay = _replay_selection(candidate, sidecar, list(_artifact_bindings(candidate)))
    authentic = replay.get("authentic_source_relation_edges")
    derived = replay.get("verified_derived_order_relation_edges")
    if derived is None:
        derived = replay.get("verified_derived_relation_edges")
    if not isinstance(authentic, list) or not isinstance(derived, list):
        raise PromotionError("task replay source relations are missing")
    authentic_endpoints = [_relation_endpoints(relation) for relation in authentic]
    derived_endpoints = [_relation_endpoints(relation) for relation in derived]
    if any(
        endpoints is None for endpoints in (*authentic_endpoints, *derived_endpoints)
    ):
        raise PromotionError("task replay relation edge is malformed")
    authentic_pairs = [
        endpoints for endpoints in authentic_endpoints if endpoints is not None
    ]
    relation_pairs = [
        endpoints
        for endpoints in (*authentic_endpoints, *derived_endpoints)
        if endpoints is not None
    ]
    relation_record_ids = {
        record_id for endpoints in relation_pairs for record_id in endpoints
    }
    source_records_by_artifact: Mapping[str, Any]
    if sidecar.registry_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
        candidate_source_records = candidate.get("source_record_ids_by_artifact")
        if not isinstance(candidate_source_records, Mapping):
            raise PromotionError("Cyber replay artifact source mapping is missing")
        source_records_by_artifact = candidate_source_records
    elif sidecar.registry_key in {
        FINANCE_TASK_REPLAY_ADAPTER,
        MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    }:
        if "source_record_ids_by_artifact" in candidate:
            raise PromotionError(
                "task candidate source mapping is not authoritative"
            )
        source_records_by_artifact = {}
    else:  # pragma: no cover - sidecar loading rejects this first
        raise PromotionError("task replay adapter is not registered for promotion")
    replay_relation_artifact_ids = {
        artifact_id
        for artifact_id in artifact_index
        if artifact_id in relation_record_ids
        or any(
            str(record_id) in relation_record_ids
            for record_id in source_records_by_artifact.get(artifact_id, ())
        )
    }
    replay_supporting_ids = replay_relation_artifact_ids - set(essential_ids)
    declared_supporting_ids = {
        str(classification.get("artifact_id") or "")
        for classification in classifications
        if isinstance(classification, Mapping)
        and classification.get("evidence_role") == "causal_supporting"
    }
    if declared_supporting_ids != replay_supporting_ids or any(
        value not in artifact_index for value in replay_supporting_ids
    ):
        raise PromotionError(
            "task causal supporting classification does not match replay"
        )
    causal_supporting_documents = [
        documents[artifact_index[value]] for value in sorted(replay_supporting_ids)
    ]
    causal_supporting_tokens = (
        token_counter(SEP.join(causal_supporting_documents))
        if causal_supporting_documents
        else 0
    )
    if (
        any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (internal_tokens, proof_tokens)
        )
        or not isinstance(causal_supporting_tokens, int)
        or isinstance(causal_supporting_tokens, bool)
        or causal_supporting_tokens < 0
        or proof_tokens > internal_tokens
        or causal_supporting_tokens > internal_tokens
    ):
        raise PromotionError("task replay semantic token counts are invalid")
    essential_source_units = {child for _parent, child in authentic_pairs}
    replayed_strict_support = replay.get("strict_support_event_count")
    if sidecar.registry_key == CYBER_KEV_TASK_REPLAY_ADAPTER:
        group_suffix = "kev-catalog-history"
    elif sidecar.registry_key == FINANCE_TASK_REPLAY_ADAPTER:
        group_suffix = "multi-filing-finance"
    elif sidecar.registry_key == MACRO_VINTAGE_TASK_REPLAY_ADAPTER:
        group_suffix = "macro-vintage-history"
    else:  # pragma: no cover - sidecar loading rejects this first
        raise PromotionError("task replay adapter is not registered for promotion")
    candidate_graph = candidate.get("graph")
    replay_proof_depth = replay.get("proof_depth")
    replay_hop_count = replay.get("hop_count")
    if (
        not isinstance(candidate_graph, dict)
        or isinstance(replay_proof_depth, bool)
        or not isinstance(replay_proof_depth, int)
        or replay_proof_depth < 2
        or isinstance(replay_hop_count, bool)
        or not isinstance(replay_hop_count, int)
        or replay_hop_count < 2
        or candidate_graph.get("proof_depth") != replay_proof_depth
        or candidate_graph.get("hop_count") != replay_hop_count
    ):
        raise PromotionError("task replay executable graph does not match candidate")
    if (
        isinstance(replayed_strict_support, bool)
        or not isinstance(replayed_strict_support, int)
        or replayed_strict_support < 2
    ):
        raise PromotionError("task replay strict support truth is invalid")
    return {
        "verification_replay_sha256": _canonical_sha256({"task_proof": task_proof}),
        "near_dup_sentence_ratio": round(sentence_near_dup_ratio(artifacts), 4),
        "strict_growth_metrics": {
            "semantic_growth_group_id": f"{candidate['world_id']}|{group_suffix}",
            "semantic_tokens": {
                "internal": internal_tokens,
                "event_bearing": proof_tokens,
                "proof_bearing": proof_tokens,
                "causal_supporting": causal_supporting_tokens,
                "generic_background": 0,
                "measurement_basis": (
                    "exact_pinned_tokenizer_replay_relation_path_"
                    "whole_artifact_upper_bound"
                ),
            },
            "strict_support_event_count": replayed_strict_support,
            "graph": {
                "n_essential_events": len(essential_source_units),
                "n_essential_artifacts": len(essential_ids),
                "proof_depth": replay_proof_depth,
                "hop_count": replay_hop_count,
            },
            "authentic_source_relation_edges": deepcopy(authentic),
        },
    }


def create_task_dense_audit(
    candidate: dict[str, Any],
    external_ranking: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
    ranking_attestation_key: bytes,
    audit_attestation_key: bytes,
    source_attestation_key: bytes,
    k: int = _DENSE_TOP_K,
) -> dict[str, Any]:
    """Replay a signed dense ranking through one source-attested task adapter."""
    _validate_candidate_identity(
        candidate,
        sidecar,
        candidate_attestation_key=candidate_attestation_key,
        source_attestation_key=source_attestation_key,
    )
    if k != _DENSE_TOP_K:
        raise PromotionError("task dense audit requires top-k=3")
    token_counter = task_sidecar_token_counter(sidecar)
    offset_tokenizer = getattr(token_counter, "offset_tokenizer", None)
    if offset_tokenizer is None:
        raise PromotionError("task promotion requires exact tokenizer offsets")
    adapter_audit = _adapter_audit(candidate, sidecar, token_counter)
    try:
        task_proof = compute_task_proof(
            candidate,
            token_counter=token_counter,
            offset_tokenizer=offset_tokenizer,
        )
    except TaskProofError as error:
        raise PromotionError(f"task upstream proof replay failed: {error}") from error
    selection_metrics = _task_selection_metrics(
        candidate, task_proof, sidecar, token_counter
    )
    task_quality_metadata = _task_quality_metadata(candidate)
    ranked, model = _validate_external_ranking(
        candidate, external_ranking, ranking_attestation_key
    )
    if k >= len(ranked):
        raise PromotionError("task dense top-k must be smaller than the artifact pool")
    ranked_ids = [item["artifact_id"] for item in ranked]
    prefix_answers = [
        str(_replay_selection(candidate, sidecar, ranked_ids[:prefix])["answer"])
        for prefix in range(1, k + 1)
    ]
    full_replay = _replay_selection(candidate, sidecar, ranked_ids)
    counterfactual_replay = _replay_selection(
        candidate, sidecar, ranked_ids, counterfactual=True
    )
    expected_answer = str(candidate.get("answer") or "")
    expected_cf_answer = str(candidate.get("cf_answer") or "")
    if (
        not expected_answer
        or expected_answer in prefix_answers
        or full_replay.get("answer") != expected_answer
    ):
        raise PromotionError("task dense retrieval replay gate failed")
    if (
        not expected_cf_answer
        or counterfactual_replay.get("answer") != expected_cf_answer
        or expected_cf_answer == expected_answer
    ):
        raise PromotionError("task counterfactual replay gate failed")
    payload = {
        "schema_version": PROMOTION_SCHEMA,
        "query_id": candidate["query_id"],
        "candidate_sha256": candidate_sha256(candidate),
        "ranking_sha256": serialized_row_sha256(external_ranking),
        "ranker_type": "dense_embedding",
        "model": model,
        "k": k,
        "top_k": ranked[:k],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "task_replay_adapter_revision": sidecar.adapter_revision,
        "strict_replay_answer": prefix_answers[-1],
        "strict_replay_prefix_answers": prefix_answers,
        "expected_answer": expected_answer,
        "counterfactual_replay_answer": expected_cf_answer,
        "embedding_topk_insufficient": True,
        "full_pool_strict_replay_sufficient": True,
        "task_replay_sidecar": _sidecar_binding(sidecar),
        "task_replay_payload_sha256": _canonical_sha256(sidecar.replay_payload),
        "source_binding_sha256": _canonical_sha256(candidate.get("source_binding")),
        "tokenizer_asset_manifest_sha256": candidate["tokenizer_asset_manifest_sha256"],
        "adapter_audit": adapter_audit,
        "adapter_audit_sha256": _canonical_sha256(adapter_audit),
        "task_proof": task_proof,
        "task_proof_sha256": _canonical_sha256(task_proof),
        "task_quality_metadata": task_quality_metadata,
        **selection_metrics,
    }
    payload["task_semantic_commitment_sha256"] = (
        task_semantic_commitment_sha256_from_audit(payload)
    )
    return attach_attestation(
        payload,
        audit_attestation_key,
        purpose=DENSE_AUDIT_PURPOSE,
    )


def _validate_task_audit(
    candidate: dict[str, Any],
    dense_audit: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    audit_attestation_key: bytes,
    adapter_audit: dict[str, bool],
    task_proof: dict[str, Any],
    token_counter: TokenCounter,
) -> list[str]:
    if not verify_attestation(
        dense_audit, audit_attestation_key, purpose=DENSE_AUDIT_PURPOSE
    ):
        raise PromotionError("task dense audit attestation is invalid")
    top_k = dense_audit.get("top_k")
    model = dense_audit.get("model")
    selection_metrics = _task_selection_metrics(
        candidate, task_proof, sidecar, token_counter
    )
    task_semantic_commitment = task_semantic_commitment_sha256_from_audit(dense_audit)
    task_quality_metadata = _task_quality_metadata(candidate)
    if (
        dense_audit.get("schema_version") != PROMOTION_SCHEMA
        or dense_audit.get("query_id") != candidate.get("query_id")
        or dense_audit.get("candidate_sha256") != candidate_sha256(candidate)
        or dense_audit.get("ranker_type") != "dense_embedding"
        or dense_audit.get("k") != _DENSE_TOP_K
        or not isinstance(top_k, list)
        or len(top_k) != _DENSE_TOP_K
        or not isinstance(model, dict)
        or dense_audit.get("strict_replay_revision") != STRICT_REPLAY_REVISION
        or dense_audit.get("task_replay_adapter_revision") != sidecar.adapter_revision
        or dense_audit.get("expected_answer") != candidate.get("answer")
        or dense_audit.get("counterfactual_replay_answer") != candidate.get("cf_answer")
        or dense_audit.get("embedding_topk_insufficient") is not True
        or dense_audit.get("full_pool_strict_replay_sufficient") is not True
        or dense_audit.get("task_replay_sidecar") != _sidecar_binding(sidecar)
        or dense_audit.get("task_replay_payload_sha256")
        != _canonical_sha256(sidecar.replay_payload)
        or dense_audit.get("source_binding_sha256")
        != _canonical_sha256(candidate.get("source_binding"))
        or dense_audit.get("tokenizer_asset_manifest_sha256")
        != candidate.get("tokenizer_asset_manifest_sha256")
        or dense_audit.get("adapter_audit") != adapter_audit
        or dense_audit.get("adapter_audit_sha256") != _canonical_sha256(adapter_audit)
        or dense_audit.get("task_proof") != task_proof
        or dense_audit.get("task_proof_sha256") != _canonical_sha256(task_proof)
        or dense_audit.get("task_quality_metadata") != task_quality_metadata
        or dense_audit.get("task_semantic_commitment_sha256")
        != task_semantic_commitment
        or any(
            dense_audit.get(field) != value
            for field, value in selection_metrics.items()
        )
        or _SHA256.fullmatch(str(dense_audit.get("ranking_sha256") or "")) is None
    ):
        raise PromotionError("task dense audit metadata does not match replay")
    bindings = _artifact_bindings(candidate)
    selected_ids: list[str] = []
    for rank, item in enumerate(top_k, start=1):
        if not isinstance(item, dict):
            raise PromotionError("task dense audit top-k is malformed")
        artifact_id = str(item.get("artifact_id") or "")
        chunk_count = item.get("chunk_count")
        if (
            item.get("rank") != rank
            or artifact_id in selected_ids
            or bindings.get(artifact_id) != item.get("text_sha256")
            or not isinstance(chunk_count, int)
            or isinstance(chunk_count, bool)
            or chunk_count < 1
        ):
            raise PromotionError("task dense audit top-k binding is invalid")
        selected_ids.append(artifact_id)
    return selected_ids


def _source_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    classifications = candidate.get("artifact_classification")
    if not isinstance(classifications, list) or not classifications:
        raise PromotionError("task candidate source classifications are missing")
    workflow_ids = sorted(
        {
            str(item.get("workflow_id") or "")
            for item in classifications
            if isinstance(item, dict) and item.get("workflow_id")
        }
    )
    declared_workflows = {
        str(value) for value in candidate.get("workflow_ids") or [] if value
    }
    if declared_workflows and declared_workflows != set(workflow_ids):
        raise PromotionError("task candidate workflow identity is inconsistent")
    source_families = sorted(
        {str(value) for value in candidate.get("source_family_ids") or [] if value}
    )
    if not workflow_ids or not source_families:
        raise PromotionError("task candidate real source identity is incomplete")
    authentic = candidate.get("authentic_source_relation_edges")
    derived = candidate.get("verified_derived_order_relation_edges")
    if derived is None:
        derived = candidate.get("verified_derived_relation_edges")
    if not isinstance(authentic, list) or not isinstance(derived, list):
        raise PromotionError("task candidate source relations are missing")
    relations = [*authentic, *derived]
    relation_id = _canonical_sha256(relations)[:20] if relations else ""
    authentic_relation_id = _canonical_sha256(authentic)[:20] if authentic else ""
    real_source_verified = True
    if candidate.get("domain") == "macro_economics":
        real_source_verified = bool(
            candidate.get("real_source_verified") is True
            and candidate.get("source_attestation_verified") is True
            and isinstance(candidate.get("task_replay_sidecar"), dict)
        )
        if not real_source_verified:
            raise PromotionError("Macro source receipt is not source-attested")
    return {
        "real_source_verified": real_source_verified,
        "real_source_family_ids": source_families,
        "real_source_workflow_ids": workflow_ids,
        "workflow_ids": workflow_ids,
        "source_relation_edges": relations,
        "source_relation_id": relation_id,
        "authentic_source_relation_id": authentic_relation_id,
        "context_source_relation_count": len(relations),
    }


def _task_quality_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    source = _source_metadata(candidate)
    return {
        "world_id": candidate.get("world_id"),
        "domain": candidate.get("domain"),
        "length_bucket": candidate.get("length_bucket"),
        "motif": candidate.get("motif"),
        "base_task_id": candidate.get("base_task_id"),
        "executable_proof_id": candidate.get("executable_proof_id"),
        "answer_program_id": candidate.get("answer_program_id"),
        "semantic_base_task_id": candidate.get("semantic_base_task_id"),
        "real_source_verified": source["real_source_verified"],
        "real_source_family_ids": source["real_source_family_ids"],
        "real_source_workflow_ids": source["real_source_workflow_ids"],
        "real_source_token_ratio": candidate.get("real_source_token_ratio"),
        "source_relation_edges": source["source_relation_edges"],
        "source_relation_id": source["source_relation_id"],
        "authentic_source_relation_id": source["authentic_source_relation_id"],
        "hybrid_causal_edges": [],
        "context_source_relation_count": source["context_source_relation_count"],
    }


def promote_task_candidate(
    candidate: dict[str, Any],
    dense_audit: dict[str, Any],
    sidecar: LoadedTaskReplaySidecar,
    *,
    candidate_attestation_key: bytes,
    audit_attestation_key: bytes,
    promotion_attestation_key: bytes,
    source_attestation_key: bytes,
    expected_split: str | None = None,
    release_selection_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote only a task candidate whose independent upstream gates are green."""
    _validate_candidate_identity(
        candidate,
        sidecar,
        candidate_attestation_key=candidate_attestation_key,
        source_attestation_key=source_attestation_key,
    )
    token_counter = task_sidecar_token_counter(sidecar)
    offset_tokenizer = getattr(token_counter, "offset_tokenizer", None)
    if offset_tokenizer is None:
        raise PromotionError("task promotion requires exact tokenizer offsets")
    adapter_audit = _adapter_audit(candidate, sidecar, token_counter)
    try:
        task_proof = compute_task_proof(
            candidate,
            token_counter=token_counter,
            offset_tokenizer=offset_tokenizer,
        )
        verification = Verification.model_validate(task_proof["verification"])
    except (TaskProofError, KeyError, ValueError) as error:
        raise PromotionError(f"task auditor proof replay failed: {error}") from error
    selected_ids = _validate_task_audit(
        candidate,
        dense_audit,
        sidecar,
        audit_attestation_key=audit_attestation_key,
        adapter_audit=adapter_audit,
        task_proof=task_proof,
        token_counter=token_counter,
    )
    prefix_answers = [
        str(_replay_selection(candidate, sidecar, selected_ids[:prefix])["answer"])
        for prefix in range(1, len(selected_ids) + 1)
    ]
    expected_answer = str(candidate.get("answer") or "")
    if (
        prefix_answers != dense_audit.get("strict_replay_prefix_answers")
        or prefix_answers[-1] != dense_audit.get("strict_replay_answer")
        or expected_answer in prefix_answers
    ):
        raise PromotionError("task dense audit prefix replay mismatch")
    all_ids = list(_artifact_bindings(candidate))
    full_replay = _replay_selection(candidate, sidecar, all_ids)
    counterfactual_replay = _replay_selection(
        candidate, sidecar, all_ids, counterfactual=True
    )
    if full_replay.get("answer") != expected_answer:
        raise PromotionError("task full-pool strict replay mismatch")
    if counterfactual_replay.get("answer") != candidate.get("cf_answer"):
        raise PromotionError("task counterfactual replay mismatch")

    selected_split = expected_split
    selection_digest = ""
    digest = candidate_sha256(candidate)
    task_semantic_commitment = task_semantic_commitment_sha256_from_audit(dense_audit)
    if release_selection_receipt is None:
        raise PromotionError("task promotion requires signed release selection")
    if release_selection_receipt is not None:
        if not verify_attestation(
            release_selection_receipt,
            audit_attestation_key,
            purpose=RELEASE_SELECTION_PURPOSE,
        ):
            raise PromotionError("task release world selection is invalid")
        release_selected_ids = release_selection_receipt.get(
            "selected_candidate_sha256"
        )
        split_by_world = release_selection_receipt.get("split_by_world")
        audit_sha256_by_candidate = release_selection_receipt.get(
            "audit_sha256_by_candidate"
        )
        task_semantic_commitment_by_candidate = release_selection_receipt.get(
            "task_semantic_commitment_sha256_by_candidate"
        )
        mapped_split = (
            split_by_world.get(str(candidate.get("world_id") or ""))
            if isinstance(split_by_world, dict)
            else None
        )
        if (
            release_selection_receipt.get("schema_version") != RELEASE_SELECTION_SCHEMA
            or not isinstance(release_selected_ids, list)
            or digest not in release_selected_ids
            or not isinstance(audit_sha256_by_candidate, dict)
            or audit_sha256_by_candidate.get(digest)
            != serialized_row_sha256(dense_audit)
            or not isinstance(task_semantic_commitment_by_candidate, dict)
            or task_semantic_commitment_by_candidate.get(digest)
            != task_semantic_commitment
            or mapped_split not in {"train", "eval"}
            or (expected_split is not None and expected_split != mapped_split)
            or release_selection_receipt.get("tokenizer_asset_manifest_sha256")
            != candidate.get("tokenizer_asset_manifest_sha256")
        ):
            raise PromotionError("task candidate is not bound by world selection")
        selected_split = str(mapped_split)
        selection_digest = serialized_row_sha256(release_selection_receipt)
    promoted = deepcopy(candidate)
    promoted.pop("attestation", None)
    promoted.pop("promotion_blocker_code", None)
    promoted.update(_source_metadata(candidate))
    promoted.update(deepcopy(dense_audit["task_quality_metadata"]))
    growth = dense_audit["strict_growth_metrics"]
    promoted["semantic_growth_group_id"] = growth["semantic_growth_group_id"]
    promoted["semantic_tokens"] = deepcopy(growth["semantic_tokens"])
    promoted["strict_support_event_count"] = growth["strict_support_event_count"]
    if growth["authentic_source_relation_edges"] != candidate.get(
        "authentic_source_relation_edges"
    ):
        raise PromotionError("task audited source relations do not match replay")
    candidate_graph = candidate.get("graph")
    if (
        not isinstance(candidate_graph, dict)
        or growth["graph"].get("proof_depth") != candidate_graph.get("proof_depth")
        or growth["graph"].get("hop_count") != candidate_graph.get("hop_count")
    ):
        raise PromotionError("task audited graph does not match executable replay")
    promoted["graph"] = {
        **deepcopy(candidate_graph),
        "n_essential_events": growth["graph"]["n_essential_events"],
        "n_essential_artifacts": growth["graph"]["n_essential_artifacts"],
    }
    promoted["task_proof_receipt"] = task_proof["task_proof_receipt"]
    promoted["verification"] = task_proof["verification"]
    promoted["view_verification"] = task_proof["view_verification"]
    promoted["data_stage"] = "train_ready"
    promoted["train_ready"] = True
    promoted["promotion_eligible"] = True
    promoted["promoted"] = True
    promoted.pop("complete_world", None)
    promoted["generation_integration"] = "task_replay_promotion_v1"
    capabilities = dict(promoted.get("pipeline_capabilities") or {})
    capabilities["generic_promotion"] = True
    promoted["pipeline_capabilities"] = capabilities
    if selected_split is not None:
        promoted["split"] = selected_split
        promoted["split_strategy"] = "world"
        promoted["holdout"] = {
            "strategy": "world",
            "group_id": hashlib.sha256(
                f"world|{candidate['world_id']}".encode()
            ).hexdigest()[:16],
        }
    classifications = promoted["artifact_classification"]
    promoted["source_origins"] = sorted(
        {
            str(item.get("source_origin") or "")
            for item in classifications
            if isinstance(item, dict) and item.get("source_origin")
        }
    )
    promoted["workflow_kinds"] = sorted(
        {
            str(item.get("workflow_kind") or "")
            for item in classifications
            if isinstance(item, dict) and item.get("workflow_kind")
        }
    )
    verification.production_mode = True
    verification.candidate_mode = False
    verification.embedding_topk_insufficient = True
    if not verification.all_green():
        raise PromotionError("production task verification gates are not green")
    promoted["verification"] = verification.model_dump()
    diagnostic_metadata = local_probe_diagnostic_metadata()
    if diagnostic_metadata:
        promoted.update(diagnostic_metadata)
    else:
        # ``production_eligible`` is a diagnostic-boundary field in the row
        # contract.  Production rows declare eligibility on the exact view.
        promoted.pop("production_eligible", None)
    view = dict(promoted["view_verification"])
    view["expected_answer"] = expected_answer
    view["strict_replay_answer"] = expected_answer
    view["production_eligible"] = not bool(diagnostic_metadata)
    if diagnostic_metadata:
        view["content_gate_eligible"] = True
    promoted["view_verification"] = view
    model = dense_audit["model"]
    promoted["promotion"] = {
        "schema_version": PROMOTION_SCHEMA,
        "candidate_sha256": digest,
        "dense_audit_sha256": serialized_row_sha256(dense_audit),
        "dense_model_provider": model.get("provider"),
        "dense_model_id": model.get("model_id"),
        "dense_model_revision": model.get("revision"),
        "dense_model_backend": model.get("backend"),
        "dense_score_metric": model.get("score_metric"),
        "dense_chunking": model.get("chunking"),
        "dense_ranking_sha256": dense_audit["ranking_sha256"],
        "dense_top_k": dense_audit["k"],
        "strict_replay_revision": STRICT_REPLAY_REVISION,
        "strict_replay_answer": expected_answer,
        "tokenizer_asset_manifest_sha256": candidate["tokenizer_asset_manifest_sha256"],
        "real_source_verified": _source_metadata(candidate)[
            "real_source_verified"
        ],
        "task_replay_sidecar": _sidecar_binding(sidecar),
        "task_candidate_content_commitment": task_candidate_content_commitment(
            candidate
        ),
        "task_semantic_commitment_sha256": task_semantic_commitment,
    }
    if selection_digest:
        promoted["promotion"]["release_selection_sha256"] = selection_digest
    signed = attach_attestation(
        promoted,
        promotion_attestation_key,
        purpose="sft_row",
    )
    errors = sft_row_errors(signed, attestation_key=promotion_attestation_key)
    if errors:
        raise PromotionError("promoted task row contract failed: " + ",".join(errors))
    return signed
