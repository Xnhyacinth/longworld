from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from itertools import pairwise

import pytest

from longworld.core.engine import answer_from_artifacts, semantic_answer_from_artifacts
from longworld.core.promotion import stable_answer_program_id
from longworld.core.sampler import materialize
from longworld.core.views import render_cf_view
from longworld.domains.codeforge.multiband import (
    bind_cumulative_patch_review_test_history,
)
from longworld.domains.codeforge.queries import build_code_queries, eval_answer
from tests.test_codeforge_patch_review_test_ancestry import (
    HEAD_SHA,
    MERGE_SHA,
    TAG_SHA,
    _patch_review_test_release_workflow,
)

QUERY = "patch_files_review_test_release_v2"


def _workflow_with_patch(patch: str):
    base = _patch_review_test_release_workflow()
    records = tuple(
        replace(record, text=f"commit {HEAD_SHA}\nFix the decoder.\n\n{patch}")
        if record.kind == "commit"
        else record
        for record in base.records
    )
    digest = hashlib.sha256("\n".join(r.text for r in records).encode()).hexdigest()
    return replace(
        base,
        workflow_id=f"git:{digest[:16]}",
        lineage=replace(base.lineage, provenance_id=f"sha256:{digest}", sha256=digest),
        records=records,
    )


def _materialized(workflows):
    return materialize(
        601,
        domain="codeforge",
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        real_workflows=workflows,
        include_program_joins=False,
    )


def test_readable_patch_query_joins_visible_paths_and_requires_every_source() -> None:
    mat = _materialized([_patch_review_test_release_workflow()])
    world = mat.worlds["focal"]
    query = next(q for q in mat.queries if q.query_type == QUERY)
    legacy = next(
        q for q in mat.queries if q.query_type == "patch_review_test_ancestry"
    )
    selected = [
        a
        for a in mat.artifacts["focal"]
        if set(a.reveals_events) & set(query.sufficient_event_ids)
    ]
    expected = (
        'tag=v1.2.3;files=["src/table.rs"];review=approved;'
        "test=Test table decoder:passed"
    )
    assert query.answer == expected
    assert "SHA256" not in query.question
    assert "patch=" not in query.answer
    assert query.cf_answer == expected.replace("decoder:passed", "decoder:failed")
    assert stable_answer_program_id(query) != stable_answer_program_id(legacy)
    assert query.base_task_group != legacy.base_task_group
    assert f"ancestry={MERGE_SHA}->{TAG_SHA}" in legacy.answer
    assert (
        answer_from_artifacts(world, query, [], enforce_preconditions=True) != expected
    )
    assert (
        semantic_answer_from_artifacts(
            world, query, selected, enforce_preconditions=True
        )
        == expected
    )
    for event_id in query.essential_event_ids:
        assert (
            answer_from_artifacts(
                world, query, selected, skip_ids={event_id}, enforce_preconditions=True
            )
            != expected
        )
    patch = next(a for a in selected if "diff --git" in a.text)
    tampered = [
        replace(a, text=a.text.replace("src/table.rs", "src/forged.rs"))
        if a.artifact_id == patch.artifact_id
        else a
        for a in selected
    ]
    assert (
        semantic_answer_from_artifacts(
            world, query, tampered, enforce_preconditions=True
        )
        != expected
    )
    cf_world, cf_artifacts = render_cf_view(world, query)
    factual_ci = next(a for a in selected if query.cf_event_id in a.reveals_events)
    cf_ci = next(a for a in cf_artifacts if query.cf_event_id in a.reveals_events)
    assert "conclusion=success" in factual_ci.text
    assert "conclusion=failed" in cf_ci.text
    assert (
        answer_from_artifacts(cf_world, query, cf_artifacts, enforce_preconditions=True)
        == query.cf_answer
    )


def test_readable_patch_query_does_not_require_unrendered_identifiers() -> None:
    mat = _materialized([_patch_review_test_release_workflow()])
    query = next(q for q in mat.queries if q.query_type == QUERY)
    visible = "\n".join(a.text for a in mat.artifacts["focal"])
    assert all(
        identifier in visible
        for identifier in re.findall(r"\b[0-9a-f]{40}\b", query.answer)
    )
    without_admission_metadata = {
        key: value
        for key, value in mat.worlds["focal"].state.values.items()
        if ":ancestry:" not in key
    }
    assert (
        eval_answer(mat.worlds["focal"], query, without_admission_metadata)
        == query.answer
    )


@pytest.mark.parametrize(
    ("patch", "expected_files"),
    [
        (
            (
                "diff -- pkg/z.go\n@@ -1 +1 @@\n-old\n+new\n"
                "diff -- pkg/a.go\n@@ -1 +1 @@\n-old\n+diff -- forged.go\n"
            ),
            '["pkg/a.go","pkg/z.go"]',
        ),
        (
            (
                'diff --git "a/src/old file.rs" "b/src/new file.rs"\n'
                "similarity index 100%\nrename from src/old file.rs\n"
                "rename to src/new file.rs\n"
            ),
            '["src/new file.rs","src/old file.rs"]',
        ),
    ],
)
def test_readable_patch_paths_come_from_diff_headers(patch, expected_files) -> None:
    mat = _materialized([_workflow_with_patch(patch)])
    query = next(q for q in mat.queries if q.query_type == QUERY)
    assert f"files={expected_files};" in query.answer
    assert "forged.go" not in query.answer


def test_missing_or_unsupported_headers_do_not_relabel_legacy_hash_task() -> None:
    for patch in (
        "2 files changed (GitHub commit API summary):\nfile metadata only\n",
        'diff --git "a/src/escaped\\tfile.rs" "b/src/escaped\\tfile.rs"\n@@ -1 +1 @@\n-a\n+b\n',
        (
            "diff -- src/known.rs\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --cc src/merged.rs\n@@@ -1,1 -1,1 +1,1 @@@\n++merged\n"
        ),
        (
            "diff -- src/known.rs\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --combined src/merged.rs\n@@@ -1,1 -1,1 +1,1 @@@\n++merged\n"
        ),
        "diff -- src/known.rs\n@@ -1 +1 @@\n-a\n+b\ndiff --git\n",
    ):
        mat = _materialized([_workflow_with_patch(patch)])
        assert not any(q.query_type == QUERY for q in mat.queries)
        legacy = next(
            q for q in mat.queries if q.query_type == "patch_review_test_ancestry"
        )
        assert f"patch={hashlib.sha256(patch.encode()).hexdigest()}" in legacy.answer


def test_readable_patch_answer_changes_with_the_actual_patch_path() -> None:
    answers = []
    for path in ("src/first.rs", "src/second.rs"):
        mat = _materialized(
            [_workflow_with_patch(f"diff -- {path}\n@@ -1 +1 @@\n-a\n+b\n")]
        )
        answers.append(next(q.answer for q in mat.queries if q.query_type == QUERY))
    assert answers[0] != answers[1]
    assert 'files=["src/first.rs"]' in answers[0]
    assert 'files=["src/second.rs"]' in answers[1]


def test_readable_patch_cycles_have_independent_growing_program_identity() -> None:
    base = _patch_review_test_release_workflow()
    workflows = []
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
            "\n".join(r.record_id + r.text for r in records).encode()
        ).hexdigest()
        workflows.append(
            replace(
                base,
                workflow_id=f"git:{digest[:16]}",
                lineage=replace(
                    base.lineage, provenance_id=f"sha256:{digest}", sha256=digest
                ),
                records=records,
            )
        )
    mat = _materialized(workflows)
    queries = [q for q in mat.queries if q.query_type == QUERY]
    assert [q.preferred_length_buckets for q in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
        ["128k"],
    ]
    assert [len(q.program_ops) for q in queries] == [1, 2, 4, 8]
    assert len({stable_answer_program_id(q) for q in queries}) == 4
    assert len({q.base_task_group for q in queries}) == 1
    for before, after in pairwise(queries):
        assert set(before.essential_event_ids) < set(after.essential_event_ids)
        assert set(before.sufficient_event_ids) < set(after.sufficient_event_ids)
    query = next(
        q for q in build_code_queries(mat.worlds["focal"]) if q.query_type == QUERY
    )
    bad = replace(
        query,
        program_ops=[{**query.program_ops[0], "op": "JOIN_PATCH_REVIEW_TEST_ANCESTRY"}],
    )
    with pytest.raises(ValueError, match="wrong cycle count"):
        bind_cumulative_patch_review_test_history(mat.worlds["focal"], [bad])
