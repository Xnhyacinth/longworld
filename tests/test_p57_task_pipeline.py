from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.p57pipeline import (
    ROUTE_INTEGRATION_RAG_SOLVABLE,
    ROUTE_REJECT_FALSE_LABEL,
    ROUTE_RETRIEVAL_SHORT_WINDOW,
    ROUTE_STRICT_LONG_DEPENDENCY,
    classify_audit_row,
    classify_job_audits,
    feasible_exact_buckets,
    job_pad_errors,
    parent_artifact_explosion_error,
    refuse_padded_bucket,
)
import scripts.run_p57_task_pipeline as pipeline


def _audit(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "query_id": "world:128k:standard:full",
        "global_proof_green": True,
        "full_pool_strict_replay_sufficient": True,
        "contiguous_windows_insufficient": True,
        "embedding_topk_insufficient": True,
    }
    row.update(overrides)
    return row


def test_feasible_buckets_do_not_pad_http2_or_ssh_or_dnssec() -> None:
    assert feasible_exact_buckets(103_810) == ("16k", "32k", "64k")
    assert feasible_exact_buckets(52_837) == ("16k", "32k")
    assert feasible_exact_buckets(126_639) == ("16k", "32k", "64k")
    assert feasible_exact_buckets(82_082) == ("16k", "32k", "64k")
    assert "128k" not in feasible_exact_buckets(126_639)
    assert refuse_padded_bucket(103_810, "128k") is not None
    assert refuse_padded_bucket(52_837, "64k") is not None
    assert refuse_padded_bucket(128_841, "128k") is None


def test_window_class_routes_match_failed_candidate_policy() -> None:
    assert classify_audit_row(_audit(global_proof_green=False)) == (
        ROUTE_REJECT_FALSE_LABEL
    )
    assert classify_audit_row(_audit(contiguous_windows_insufficient=False)) == (
        ROUTE_RETRIEVAL_SHORT_WINDOW
    )
    assert classify_audit_row(_audit(embedding_topk_insufficient=False)) == (
        ROUTE_INTEGRATION_RAG_SOLVABLE
    )
    assert classify_audit_row(_audit()) == ROUTE_STRICT_LONG_DEPENDENCY


def test_classify_reads_nested_task_proof_gates() -> None:
    row = {
        "query_id": "ietf-http-semantics-succession-v1:128k:standard:full",
        "embedding_topk_insufficient": True,
        "full_pool_strict_replay_sufficient": True,
        "task_proof": {
            "verification": {"contiguous_windows_insufficient": True},
            "view_verification": {"global_proof_green": True},
        },
    }
    assert classify_audit_row(row) == ROUTE_STRICT_LONG_DEPENDENCY
    retrieval = {
        **row,
        "query_id": "amazon:16k:full",
        "task_proof": {
            "verification": {"contiguous_windows_insufficient": False},
            "view_verification": {"global_proof_green": True},
        },
    }
    mixed = classify_job_audits([row, retrieval])
    assert mixed["route"] == ROUTE_RETRIEVAL_SHORT_WINDOW
    assert mixed["strict_eligible"] is False
    assert mixed["auto_promote"] is False


def test_mixed_short_window_does_not_enter_strict() -> None:
    receipt = classify_job_audits(
        [
            _audit(query_id="a", contiguous_windows_insufficient=False),
            _audit(query_id="b"),
        ]
    )
    assert receipt["route"] == ROUTE_RETRIEVAL_SHORT_WINDOW
    assert receipt["strict_eligible"] is False
    assert receipt["auto_promote"] is False
    assert receipt["production_eligible"] is False


def test_job_catalog_refuses_128k_pad_and_auto_promote(tmp_path: Path) -> None:
    catalog = {
        "schema_version": "longworld.p57-task-pipeline.v1",
        "jobs": [
            {
                "job_id": "http2",
                "unique_tokens": 103810,
                "primary_buckets": ["128k"],
                "auto_promote": False,
                "trust_file": "trust.json",
                "projected_dir": "out",
            }
        ],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    loaded = pipeline.load_catalog(path)
    errors = job_pad_errors(loaded["jobs"][0])
    assert any("pad_forbidden:128k" in item for item in errors)


def test_catalog_rejects_auto_promote_flag(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.p57-task-pipeline.v1",
                "jobs": [
                    {
                        "job_id": "bad",
                        "primary_buckets": ["64k"],
                        "auto_promote": True,
                        "trust_file": "trust.json",
                        "projected_dir": "out",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(pipeline.PipelineCatalogError, match="auto_promote"):
        pipeline.load_catalog(path)


def test_resume_skips_jobs_with_matching_filter_receipt(tmp_path: Path) -> None:
    projected = tmp_path / "projected"
    projected.mkdir()
    receipt = {
        "schema_version": "longworld.p57-task-filter-receipt.v1",
        "job_id": "tls",
        "route": ROUTE_STRICT_LONG_DEPENDENCY,
        "input_sha256": "abc",
        "auto_promote": False,
        "production_eligible": False,
    }
    (projected / "FILTER_RECEIPT.json").write_text(
        json.dumps(receipt, sort_keys=True), encoding="utf-8"
    )
    job = {
        "job_id": "tls",
        "projected_dir": str(projected),
        "primary_buckets": ["64k"],
        "auto_promote": False,
        "trust_file": str(tmp_path / "trust.json"),
    }
    assert pipeline.filter_receipt_complete(job, expected_input_sha256="abc") is True
    assert pipeline.filter_receipt_complete(job, expected_input_sha256="def") is False


def test_parent_artifact_explosion_blocks_ssh_style_tiny_chunks() -> None:
    assert parent_artifact_explosion_error(20) is None
    assert parent_artifact_explosion_error(80) is None
    error = parent_artifact_explosion_error(299)
    assert error is not None
    assert error.startswith("parent_artifact_explosion:299>")


def test_log_blocker_treats_selection_invalid_as_structural(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "ssh.log"
    log_path.write_text(
        "TaskProofError: IETF task replay artifact selection is invalid\n",
        encoding="utf-8",
    )
    assert (
        pipeline._log_blocker(log_path)
        == pipeline.ROUTE_BLOCKED_INSUFFICIENT_PROOF
    )
