from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.run_p57_task_pipeline as pipeline
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
from longworld.core.promotion import candidate_sha256


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


def test_known_codebook_detector_does_not_treat_alternatives_as_singleton() -> None:
    question = (
        "Use this exact per-field output codebook (canonical JSON): "
        '{"status":{"code":"VALID","alternatives":["VALID","INVALID"]}}'
    )
    assert pipeline.question_only_codebook_prediction(question) is None


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


@pytest.mark.parametrize("primary_buckets", [["64k"], ["32k", "64k"]])
@pytest.mark.parametrize("leaky", [False, True])
def test_pipeline_checks_primary_projection_before_ranking(
    tmp_path: Path, monkeypatch, primary_buckets: list[str], leaky: bool
) -> None:
    parents = tmp_path / "parents"
    parents.mkdir()
    parent_rows = [{"length_bucket": band} for band in ("16k", "32k", "64k")]
    (parents / "parents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in parent_rows)
    )
    (parents / "TASK_REPLAY_SIDECAR.json").write_text("{}")
    projected = tmp_path / "projected"
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": pipeline.PIPELINE_SCHEMA,
                "ledger": str(tmp_path / "ledger.jsonl"),
                "jobs": [
                    {
                        "job_id": "primary-only",
                        "trust_file": str(tmp_path / "trust.json"),
                        "parents_dir": str(parents),
                        "projected_dir": str(projected),
                        "primary_buckets": primary_buckets,
                        "auto_promote": False,
                    }
                ],
            }
        )
    )
    ranked_buckets = []

    def subprocess_run(argv, **kwargs):
        # Simulate the external CLI boundary; the real pipeline constructs argv.
        if str(pipeline.ROOT / "scripts/project_task_candidate_views.py") in argv:
            selected = [
                argv[index + 1]
                for index, value in enumerate(argv)
                if value == "--length-bucket"
            ]
            rows = [
                {
                    **row,
                    "view": view,
                    "query_id": f"{row['length_bucket']}-{view}",
                    "question": "Compute the source-dependent value.",
                    "answer": '{"value":42}',
                }
                for row in parent_rows
                if not selected or row["length_bucket"] in selected
                for view in ("full", "cf", "ordered_artifact_view")
            ]
            if leaky:
                for row in rows:
                    row.update(
                        query_id=f"{row['length_bucket']}-{row['view']}",
                        question=(
                            "Use this exact per-field output codebook (canonical JSON): "
                            '{"status":{"code":"VALID"}}'
                        ),
                        answer=json.dumps(
                            {"status": "UNKNOWN" if row["view"] == "cf" else "VALID"}
                        ),
                    )
            (projected / "candidates.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
        elif str(pipeline.ROOT / "scripts/rank_candidates_dense.py") in argv:
            rows = map(
                json.loads, (projected / "candidates.jsonl").read_text().splitlines()
            )
            ranked_buckets.extend(row["length_bucket"] for row in rows)
            (projected / "rankings.jsonl").write_text("{}\n")
        else:
            pytest.fail(f"unexpected subprocess: {argv}")

    monkeypatch.setattr(pipeline.subprocess, "run", subprocess_run)
    result = pipeline.run_pipeline(catalog, execute=True, workers=1)
    if leaky:
        assert result["jobs"][0]["status"] == "blocked"
        assert result["jobs"][0]["route"] == "blocked_question_only_shortcut"
        assert ranked_buckets == []
    else:
        assert set(ranked_buckets) == set(primary_buckets)
        assert len(ranked_buckets) == 3 * len(primary_buckets)


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        "leak",
        "missing_candidates",
        "invalid_question",
        "wrong_band",
        "stale_audit",
        "receipt_symlink",
        "receipt_directory",
    ],
)
def test_invalid_or_leaky_candidates_cannot_reuse_green_audits(
    tmp_path: Path, resume: bool, defect: str
) -> None:
    import hashlib

    projected = tmp_path / "projected"
    projected.mkdir()
    question = (
        "Use this exact per-field output codebook (canonical JSON): "
        '{"status":{"code":"VALID","meaning":"valid status"}}. '
        "Return UNKNOWN when evidence is absent."
    )
    candidates = [
        {
            "query_id": "q-full",
            "length_bucket": "64k",
            "question": question,
            "answer": '{"status":"VALID"}',
        },
        {
            "query_id": "q-cf",
            "length_bucket": "64k",
            "question": question,
            "answer": '{"status":"UNKNOWN"}',
        },
    ]
    if defect == "invalid_question":
        candidates[0]["question"] = {"invalid": True}
    elif defect == "wrong_band":
        for row in candidates:
            row["length_bucket"] = "16k"
    elif defect == "stale_audit":
        for row in candidates:
            row["question"] = "Compute the source-dependent value."
    (projected / "candidates.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in candidates)
    )
    audits_path = projected / "audits.jsonl"
    audit_bytes = "".join(
        json.dumps(
            _audit(
                query_id=row["query_id"],
                candidate_sha256="0" * 64
                if defect == "stale_audit"
                else candidate_sha256(row),
            )
        )
        + "\n"
        for row in candidates
    ).encode()
    audits_path.write_bytes(audit_bytes)
    (projected / "FILTER_RECEIPT.json").write_text(
        json.dumps(
            {
                "schema_version": pipeline.FILTER_RECEIPT_SCHEMA,
                "job_id": "leaky",
                "input_sha256": hashlib.sha256(audit_bytes).hexdigest(),
                "route": ROUTE_STRICT_LONG_DEPENDENCY,
                "strict_eligible": True,
                "auto_promote": False,
                "production_eligible": False,
            }
        )
    )
    if defect == "missing_candidates":
        (projected / "candidates.jsonl").unlink()
    elif defect in {"receipt_symlink", "receipt_directory"}:
        receipt_path = projected / "FILTER_RECEIPT.json"
        receipt_path.unlink()
        if defect == "receipt_symlink":
            receipt_path.symlink_to(audits_path)
        else:
            receipt_path.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": pipeline.PIPELINE_SCHEMA,
                "ledger": str(tmp_path / "ledger.jsonl"),
                "jobs": [
                    {
                        "job_id": "leaky",
                        "projected_dir": str(projected),
                        "trust_file": str(tmp_path / "trust.json"),
                        "primary_buckets": ["64k"],
                        "auto_promote": False,
                    }
                ],
            }
        )
    )
    result = pipeline.run_pipeline(catalog, execute=True, resume=resume)
    assert result["jobs"][0]["status"] == ("blocked" if defect == "leak" else "failed")
    assert audits_path.read_bytes() == audit_bytes
    if defect in {"receipt_symlink", "receipt_directory"}:
        assert result["jobs"][0]["filter_receipt_error"]
        return
    receipt = json.loads((projected / "FILTER_RECEIPT.json").read_text())
    assert receipt["strict_eligible"] is False
    if defect == "leak":
        assert receipt["route"] == "blocked_question_only_shortcut"
        assert receipt["question_only_query_ids"] == ["q-full"]
    else:
        assert receipt["status"] == "failed"


def test_malformed_codebook_fails_its_job_without_stopping_other_jobs(
    tmp_path: Path,
) -> None:
    jobs = []
    for job_id, question in (
        ("bad", "Use this exact per-field output codebook (canonical JSON): {broken"),
        ("good", "Compute the source-dependent result."),
    ):
        projected = tmp_path / job_id
        projected.mkdir()
        candidate = {
            "query_id": job_id,
            "length_bucket": "64k",
            "question": question,
            "answer": '{"value":42}',
        }
        (projected / "candidates.jsonl").write_text(json.dumps(candidate) + "\n")
        (projected / "audits.jsonl").write_text(
            json.dumps(
                _audit(query_id=job_id, candidate_sha256=candidate_sha256(candidate))
            )
            + "\n"
        )
        jobs.append(
            {
                "job_id": job_id,
                "projected_dir": str(projected),
                "trust_file": str(tmp_path / "trust.json"),
                "primary_buckets": ["64k"],
                "auto_promote": False,
            }
        )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": pipeline.PIPELINE_SCHEMA,
                "ledger": str(tmp_path / "ledger.jsonl"),
                "jobs": jobs,
            }
        )
    )
    result = pipeline.run_pipeline(catalog, execute=True, workers=2)
    assert result["status"] == "failed"
    by_job = {row["job_id"]: row for row in result["jobs"]}
    assert by_job["bad"]["status"] == "failed"
    assert by_job["bad"]["production_eligible"] is False
    assert by_job["good"]["status"] == "classified"
    failed = json.loads((tmp_path / "bad/FILTER_RECEIPT.json").read_text())
    assert failed["status"] == "failed"
    assert failed["strict_eligible"] is False
