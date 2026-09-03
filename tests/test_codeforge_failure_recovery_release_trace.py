from __future__ import annotations

import hashlib
from dataclasses import replace
from itertools import pairwise

from longworld.core.engine import answer_from_artifacts
from longworld.core.promotion import stable_answer_program_id
from longworld.core.provenance import SourceLineage
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
from longworld.core.taxonomy import SourceOrigin
from longworld.domains.codeforge.queries import build_code_queries
from longworld.domains.codeforge.render import render_code
from longworld.domains.codeforge.schema import sample_code_spec
from longworld.domains.codeforge.simulate import simulate_code

BROKEN_SHA = "a" * 40
HEAD_SHA = "1" * 40
MERGE_SHA = "2" * 40
TAG_SHA = "3" * 40


def _failure_recovery_release_workflow() -> RealWorkflow:
    records = (
        WorkflowRecord(
            f"commit:{BROKEN_SHA}",
            "commit",
            "2026-01-01T00:00:00Z",
            (
                f"commit {BROKEN_SHA}\nBreak the table decoder.\n\n"
                "diff --git a/src/table.rs b/src/table.rs\n"
                "--- a/src/table.rs\n+++ a/src/table.rs\n"
                "@@ -1 +1 @@\n-checked_decode()\n+broken_decode()\n"
            ),
            attributes={"sha": BROKEN_SHA},
        ),
        WorkflowRecord(
            "ci:18",
            "ci_run",
            "2026-01-01T12:00:00Z",
            (
                f"CI check test_folded_headers for {BROKEN_SHA}\n"
                "run_id=18 status=completed conclusion=failure"
            ),
            (f"commit:{BROKEN_SHA}",),
            {
                "head_sha": BROKEN_SHA,
                "name": "test_folded_headers",
                "run_id": "18",
                "conclusion": "failure",
            },
        ),
        WorkflowRecord(
            f"commit:{HEAD_SHA}",
            "commit",
            "2026-01-02T00:00:00Z",
            (
                f"commit {HEAD_SHA}\nRepair the table decoder.\n\n"
                "diff --git a/src/table.rs b/src/table.rs\n"
                "--- a/src/table.rs\n+++ a/src/table.rs\n"
                "@@ -1 +1 @@\n-broken_decode()\n+checked_decode()\n"
            ),
            attributes={"sha": HEAD_SHA},
        ),
        WorkflowRecord(
            "review:17",
            "review",
            "2026-01-03T00:00:00Z",
            "review_id=17 state=APPROVED\nThe recovery bounds check is correct.",
            (f"commit:{HEAD_SHA}",),
            {"state": "APPROVED"},
        ),
        WorkflowRecord(
            "ci:19",
            "ci_run",
            "2026-01-04T00:00:00Z",
            (
                f"CI check test_folded_headers for {HEAD_SHA}\n"
                "run_id=19 status=completed conclusion=success"
            ),
            (f"commit:{HEAD_SHA}",),
            {
                "head_sha": HEAD_SHA,
                "name": "test_folded_headers",
                "run_id": "19",
                "conclusion": "success",
            },
        ),
        WorkflowRecord(
            "merge:17",
            "merge",
            "2026-01-05T00:00:00Z",
            f"Merged pull request #17 from validated head commit {HEAD_SHA}.",
            (f"commit:{HEAD_SHA}", "review:17"),
            {"head_sha": HEAD_SHA, "merge_commit_sha": MERGE_SHA},
        ),
        WorkflowRecord(
            "release:v1.2.3",
            "release",
            "2026-01-06T00:00:00Z",
            "Release v1.2.3 includes pull request #17.",
            ("merge:17", "ci:19"),
            {
                "tag": "v1.2.3",
                "tag_commit_sha": TAG_SHA,
                "merge_commit_sha": MERGE_SHA,
                "ancestry_verified": True,
                "compare_status": "ahead",
                "compare_base_sha": MERGE_SHA,
                "compare_head_sha": TAG_SHA,
                "ci_evidence_scope": "observed_selected_final_pre_merge_check_runs",
            },
        ),
    )
    digest = hashlib.sha256(
        "\n".join(record.text for record in records).encode()
    ).hexdigest()
    return RealWorkflow(
        workflow_id=f"git:{digest[:16]}",
        source_kind="git_export",
        source_origin=SourceOrigin.REAL_PUBLIC,
        lineage=SourceLineage(
            provenance_id=f"sha256:{digest}",
            url="https://github.com/duckdb/duckdb",
            license="MIT",
            retrieved_at="2026-01-07T00:00:00Z",
            parser="git_workflow_json@1",
            sha256=digest,
            revision=TAG_SHA,
            source_path="/authorized/duckdb-failure-recovery.json",
        ),
        records=records,
        facts={},
    )


def test_failure_recovery_release_trace_requires_cross_commit_repair() -> None:
    spec = sample_code_spec(
        1601,
        n_parallel=0,
        real_workflow=_failure_recovery_release_workflow(),
    )
    world = simulate_code(spec)["focal"]
    query = next(
        item
        for item in build_code_queries(world)
        if item.query_type == "failure_recovery_release_trace"
        and any(
            op.get("op") == "JOIN_FAILURE_RECOVERY_RELEASE" for op in item.program_ops
        )
    )
    artifacts = render_code(world)
    selected = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert query.answer != "unknown"
    assert query.cf_answer != "unknown"
    assert query.cf_answer != query.answer
    assert f"broken={BROKEN_SHA}" in query.answer
    assert "fail=test_folded_headers:failed" in query.answer
    assert f"repair={HEAD_SHA}" in query.answer
    assert "review=approved" in query.answer
    assert "test=test_folded_headers:passed" in query.answer
    assert "test=test_folded_headers:failed" in query.cf_answer
    assert f"ancestry={MERGE_SHA}->{TAG_SHA}" in query.answer
    assert {
        event.params.get("record_kind")
        for event in world.events
        if event.id in query.essential_event_ids
    } == {
        "commit",
        "review",
        "ci_run",
        "merge",
        "release",
    }
    assert (
        answer_from_artifacts(world, query, selected, enforce_preconditions=False)
        == query.answer
    )
    for removed in selected:
        assert (
            answer_from_artifacts(
                world,
                query,
                [
                    artifact
                    for artifact in selected
                    if artifact.artifact_id != removed.artifact_id
                ],
                enforce_preconditions=False,
            )
            != query.answer
        )


def test_failure_recovery_grows_one_two_four_eight_release_cycles() -> None:
    base = _failure_recovery_release_workflow()
    workflows: list[RealWorkflow] = []
    for cycle in range(8):
        suffix = str(cycle + 1)
        records = tuple(
            replace(
                record,
                record_id=f"{record.record_id}:{suffix}",
                occurred_at=record.occurred_at.replace("2026-01", f"2026-0{cycle + 1}"),
                links=tuple(f"{link}:{suffix}" for link in record.links),
            )
            for record in base.records
        )
        digest = hashlib.sha256(
            "\n".join(record.record_id + record.text for record in records).encode()
        ).hexdigest()
        workflows.append(
            replace(
                base,
                workflow_id=f"git:{digest[:16]}",
                lineage=replace(
                    base.lineage,
                    provenance_id=f"sha256:{digest}",
                    sha256=digest,
                ),
                records=records,
            )
        )

    spec = sample_code_spec(1602, n_parallel=0, real_workflows=workflows)
    world = simulate_code(spec)["focal"]
    queries = sorted(
        (
            query
            for query in build_code_queries(world)
            if query.query_type == "failure_recovery_release_trace"
        ),
        key=lambda query: {"16k": 0, "32k": 1, "64k": 2, "128k": 3}[
            query.preferred_length_buckets[0]
        ],
    )

    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
        ["128k"],
    ]
    assert [
        sum(op.get("op") == "JOIN_FAILURE_RECOVERY_RELEASE" for op in query.program_ops)
        for query in queries
    ] == [1, 2, 4, 8]
    assert [len(query.answer.split(" | ")) for query in queries] == [1, 2, 4, 8]
    assert len({stable_answer_program_id(query) for query in queries}) == 4
    for before, after in pairwise(queries):
        assert set(before.essential_event_ids) < set(after.essential_event_ids)
        assert set(before.sufficient_event_ids) < set(after.sufficient_event_ids)
