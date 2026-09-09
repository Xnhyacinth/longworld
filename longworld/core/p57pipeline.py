"""P57 independent-task pipeline: natural-length routing without padding or auto-promotion."""

from __future__ import annotations

import json
from typing import Any

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES

PIPELINE_SCHEMA = "longworld.p57-task-pipeline.v1"
FILTER_RECEIPT_SCHEMA = "longworld.p57-task-filter-receipt.v1"
LEDGER_SCHEMA = "longworld.p57-task-pipeline-ledger.v1"

ROUTE_REJECT_FALSE_LABEL = "reject_false_label"
ROUTE_RETRIEVAL_SHORT_WINDOW = "retrieval_short_window"
ROUTE_INTEGRATION_RAG_SOLVABLE = "integration_rag_solvable"
ROUTE_STRICT_LONG_DEPENDENCY = "strict_long_dependency"
ROUTE_BLOCKED_INSUFFICIENT_UNIQUE = "blocked_insufficient_unique_tokens"
ROUTE_BLOCKED_INSUFFICIENT_PROOF = "blocked_insufficient_proof"
ROUTE_BLOCKED_PARENT_EXPLOSION = "blocked_parent_artifact_explosion"
ROUTE_BLOCKED_QUESTION_ONLY = "blocked_question_only_shortcut"
QUESTION_ONLY_CHECK_REVISION = "longworld.question-only-codebook.v1"
MAX_PARENT_ARTIFACTS = 80

_BAND_ORDER = ("16k", "32k", "64k", "128k")


def question_only_codebook_prediction(question: str) -> dict[str, str] | None:
    """Predict from the public singleton codebook, without context or gold."""
    marker = "Use this exact per-field output codebook (canonical JSON): "
    if marker not in question:
        return None
    codebook, _ = json.JSONDecoder().raw_decode(question.split(marker, 1)[1])
    if not isinstance(codebook, dict) or not codebook:
        return None
    if any(
        not isinstance(value, dict)
        or not isinstance(value.get("code"), str)
        or not set(value).issubset({"code", "meaning"})
        for value in codebook.values()
    ):
        return None
    return {key: value["code"] for key, value in codebook.items()}


def feasible_exact_buckets(unique_tokens: int) -> tuple[str, ...]:
    if isinstance(unique_tokens, bool) or unique_tokens < 0:
        raise ValueError("unique_tokens must be a non-negative integer")
    return tuple(
        bucket
        for bucket in _BAND_ORDER
        if unique_tokens >= EXACT_TOKEN_BAND_RANGES[bucket][0]
    )


def refuse_padded_bucket(unique_tokens: int, requested_bucket: str) -> str | None:
    if requested_bucket not in EXACT_TOKEN_BAND_RANGES:
        return f"unknown_bucket:{requested_bucket}"
    minimum, _maximum = EXACT_TOKEN_BAND_RANGES[requested_bucket]
    if unique_tokens < minimum:
        return (
            "pad_forbidden:"
            f"{requested_bucket}:unique={unique_tokens}<min={minimum}"
        )
    return None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def audit_gate_flag(row: dict[str, Any], name: str) -> Any:
    """Read a dense-audit gate from the row or nested task_proof blobs."""
    if name in row:
        return row.get(name)
    proof = _mapping(row.get("task_proof"))
    if name == "global_proof_green":
        for blob in (row.get("view_verification"), proof.get("view_verification")):
            mapping = _mapping(blob)
            if name in mapping:
                return mapping.get(name)
    if name == "contiguous_windows_insufficient":
        for blob in (row.get("verification"), proof.get("verification")):
            mapping = _mapping(blob)
            if name in mapping:
                return mapping.get(name)
    return None


def classify_audit_row(row: dict[str, Any]) -> str:
    if audit_gate_flag(row, "global_proof_green") is not True:
        return ROUTE_REJECT_FALSE_LABEL
    if audit_gate_flag(row, "full_pool_strict_replay_sufficient") is not True:
        return ROUTE_REJECT_FALSE_LABEL
    if audit_gate_flag(row, "contiguous_windows_insufficient") is not True:
        return ROUTE_RETRIEVAL_SHORT_WINDOW
    if audit_gate_flag(row, "embedding_topk_insufficient") is not True:
        return ROUTE_INTEGRATION_RAG_SOLVABLE
    return ROUTE_STRICT_LONG_DEPENDENCY


def classify_job_audits(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("dense audits are empty")
    routes = [classify_audit_row(row) for row in rows]
    unique_routes = tuple(sorted(set(routes)))
    if ROUTE_REJECT_FALSE_LABEL in unique_routes:
        route = ROUTE_REJECT_FALSE_LABEL
    elif len(unique_routes) == 1:
        route = unique_routes[0]
    else:
        route = unique_routes[0]
        if ROUTE_RETRIEVAL_SHORT_WINDOW in unique_routes:
            route = ROUTE_RETRIEVAL_SHORT_WINDOW
        elif ROUTE_INTEGRATION_RAG_SOLVABLE in unique_routes:
            route = ROUTE_INTEGRATION_RAG_SOLVABLE
    return {
        "schema_version": FILTER_RECEIPT_SCHEMA,
        "n_audits": len(rows),
        "route": route,
        "routes": routes,
        "strict_eligible": route == ROUTE_STRICT_LONG_DEPENDENCY,
        "auto_promote": False,
        "production_eligible": False,
        "query_ids": [str(row.get("query_id") or "") for row in rows],
    }


def job_pad_errors(job: dict[str, Any]) -> list[str]:
    unique = job.get("unique_tokens")
    buckets = job.get("primary_buckets") or ()
    if unique is None:
        return []
    if isinstance(unique, bool) or not isinstance(unique, int):
        return ["unique_tokens_invalid"]
    errors: list[str] = []
    for bucket in buckets:
        refused = refuse_padded_bucket(unique, str(bucket))
        if refused:
            errors.append(refused)
    return errors


def parent_artifact_explosion_error(
    n_artifacts: int, *, cap: int = MAX_PARENT_ARTIFACTS
) -> str | None:
    if isinstance(n_artifacts, bool) or n_artifacts < 0:
        raise ValueError("n_artifacts must be a non-negative integer")
    if n_artifacts > cap:
        return f"parent_artifact_explosion:{n_artifacts}>{cap}"
    return None
