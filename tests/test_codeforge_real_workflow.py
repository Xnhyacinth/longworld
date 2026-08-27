from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from longworld.core.engine import answer_from_artifacts
from longworld.core.pack import estimate_tokens, join_artifacts
from longworld.core.provenance import SourceLineage
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
from longworld.core.taxonomy import (
    SourceOrigin,
    WorkflowKind,
    artifact_classification,
)
from longworld.core.views import render_cf_view
from longworld.domains.codeforge.queries import build_code_queries
from longworld.domains.codeforge.render import render_code
from longworld.domains.codeforge.schema import sample_code_spec
from longworld.domains.codeforge.simulate import simulate_code

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate import real_workflow_artifacts_for_query


def _workflow(
    records: list[WorkflowRecord],
    *,
    source_url: str = "https://github.com/example/parser",
) -> RealWorkflow:
    digest = hashlib.sha256("\n".join(r.text for r in records).encode()).hexdigest()
    return RealWorkflow(
        workflow_id=f"git:{digest[:16]}",
        source_kind="git_export",
        source_origin=SourceOrigin.REAL_PRIVATE_EXPORT,
        lineage=SourceLineage(
            provenance_id=f"sha256:{digest}",
            url=source_url,
            license="Apache-2.0",
            retrieved_at="2026-08-20T12:00:00Z",
            parser="git_workflow_json@1",
            sha256=digest,
            revision="release-head",
            source_path="/authorized/parser-workflow.json",
        ),
        records=tuple(records),
        facts={},
    )


def _repo_episode() -> RealWorkflow:
    return _workflow(
        [
            WorkflowRecord(
                "issue:17",
                "issue",
                "2025-01-02T09:00:00Z",
                "Issue 17 reports folded-header corruption in parser-core.",
            ),
            WorkflowRecord(
                "commit:deadbee",
                "commit",
                "2025-01-03T10:00:00Z",
                "Commit deadbee introduced the parser-core regression.",
                ("issue:17",),
                {"commit": "deadbee"},
            ),
            WorkflowRecord(
                "ci:failed",
                "ci_run",
                "2025-01-03T10:30:00Z",
                "CI run ci-failed reports failed test_folded_headers.",
                ("commit:deadbee",),
                {"run": "ci-failed", "result": "failed", "test": "test_folded_headers"},
            ),
            WorkflowRecord(
                "pr:18",
                "pull_request",
                "2025-01-05T11:00:00Z",
                "Pull request 18 proposes parser-core version 2.4.1 as the recovery candidate.",
                ("issue:17",),
                {"package": "parser-core", "version": "2.4.1"},
            ),
            WorkflowRecord(
                "review:18",
                "review",
                "2025-01-06T08:00:00Z",
                "Review 18 approved the recovery candidate after API inspection.",
                ("pr:18",),
                {"result": "approved"},
            ),
            WorkflowRecord(
                "commit:cafe123",
                "commit",
                "2025-01-07T12:00:00Z",
                "Commit cafe123 packages parser-core version 2.4.1 for validation.",
                ("review:18",),
                {"commit": "cafe123", "package": "parser-core", "version": "2.4.1"},
            ),
            WorkflowRecord(
                "ci:passed",
                "ci_run",
                "2025-01-08T15:00:00Z",
                "CI run ci-passed reports passed test_folded_headers across the recovery matrix.",
                ("commit:cafe123",),
                {
                    "run": "ci-passed",
                    "result": "passed",
                    "test": "test_folded_headers",
                },
            ),
            WorkflowRecord(
                "license:18",
                "license",
                "2025-01-09T09:00:00Z",
                "License review finds Apache-2.0 compatible for redistribution.",
                ("commit:cafe123",),
                {"license": "Apache-2.0", "compatible": True},
            ),
            WorkflowRecord(
                "release:v2.4.1",
                "release",
                "2025-01-10T17:00:00Z",
                "Release tag v2.4.1 published after the linked gates completed.",
                ("ci:passed", "license:18"),
                {"tag": "v2.4.1"},
            ),
        ]
    )


def _materialize_real(workflow: RealWorkflow):
    spec = sample_code_spec(73, n_parallel=0, real_workflow=workflow)
    world = simulate_code(spec)["focal"]
    artifacts = render_code(world)
    queries = build_code_queries(world)
    return spec, world, artifacts, queries


def test_multi_repo_version_selection_targets_the_latest_release_by_time() -> None:
    earlier = _workflow(
        [
            WorkflowRecord(
                "pr:old",
                "pull_request",
                "2025-01-01T00:00:00Z",
                "Pull request old selects old-core version 1.0.0.",
                attributes={"package": "old-core", "version": "1.0.0"},
            ),
            WorkflowRecord(
                "ci:old",
                "ci_run",
                "2025-01-02T00:00:00Z",
                "CI run old passed.",
                ("pr:old",),
                {"run": "old", "result": "passed"},
            ),
            WorkflowRecord(
                "license:old",
                "license",
                "2025-01-02T01:00:00Z",
                "License review old is compatible.",
                ("pr:old",),
                {"license": "Apache-2.0", "compatible": True},
            ),
            WorkflowRecord(
                "release:v1.0.0",
                "release",
                "2025-01-03T00:00:00Z",
                "Release tag v1.0.0 was published.",
                ("ci:old", "license:old"),
                {"tag": "v1.0.0"},
            ),
        ],
        source_url="https://github.com/example/old-core",
    )
    latest = _workflow(
        [
            WorkflowRecord(
                "pr:new",
                "pull_request",
                "2025-02-01T00:00:00Z",
                "Pull request new selects new-core version 2.0.0.",
                attributes={"package": "new-core", "version": "2.0.0"},
            ),
            WorkflowRecord(
                "ci:new",
                "ci_run",
                "2025-02-02T00:00:00Z",
                "CI run new passed.",
                ("pr:new",),
                {"run": "new", "result": "passed"},
            ),
            WorkflowRecord(
                "release:v2.0.0",
                "release",
                "2025-02-03T00:00:00Z",
                "Release tag v2.0.0 was published.",
                ("ci:new",),
                {"tag": "v2.0.0"},
            ),
        ],
        source_url="https://github.com/example/new-core",
    )
    spec = sample_code_spec(73, n_parallel=0, real_workflows=[earlier, latest])
    world = simulate_code(spec)["focal"]
    query = next(
        item
        for item in build_code_queries(world)
        if item.query_type == "version_selection"
    )

    assert query.query_id.endswith("release:v2.0.0")
    assert "github.com/example/new-core" in query.question
    assert query.answer == "new-core@2.0.0 -> v2.0.0"


def test_cross_repo_integration_combines_real_release_and_merge_evidence() -> None:
    dependency = _workflow(
        [
            WorkflowRecord(
                "license:client",
                "license",
                "2025-01-02T00:00:00Z",
                "Apache License 2.0 applies to the client repository.",
                attributes={"license": "Apache-2.0"},
            ),
            WorkflowRecord(
                "pull:client",
                "pull_request",
                "2025-01-03T00:00:00Z",
                "The dependency graph is compatible with Apache-2.0.",
                ("license:client",),
            ),
            WorkflowRecord(
                "review:client",
                "review",
                "2025-01-04T00:00:00Z",
                "Review state: APPROVED",
                ("pull:client",),
                {"result": "approved"},
            ),
            WorkflowRecord(
                "commit:abc1234",
                "commit",
                "2025-01-05T00:00:00Z",
                "Commit abc1234 implements the client integration.",
                ("review:client",),
                {"commit": "abc1234"},
            ),
            WorkflowRecord(
                "merge:client",
                "merge",
                "2025-01-06T00:00:00Z",
                "Merged client integration at commit abc1234.",
                (
                    "pull:client",
                    "review:client",
                    "commit:abc1234",
                    "license:client",
                ),
                {"commit": "abc1234"},
            ),
        ],
        source_url="https://github.com/example/client",
    )
    spec = sample_code_spec(
        73,
        n_parallel=0,
        real_workflows=[_repo_episode(), dependency],
    )
    world = simulate_code(spec)["focal"]
    artifacts = render_code(world)
    query = next(
        item
        for item in build_code_queries(world)
        if item.query_type == "cross_repo_release_dependency"
    )
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in set(query.essential_artifact_ids)
    ]

    assert len(essential) >= 4
    assert query.answer == "v2.4.1 :: abc1234 :: Apache-2.0"
    assert query.cf_answer != query.answer
    assert query.preferred_length_buckets == ["32k"]
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=False)
        == query.answer
    )
    assert all(
        answer_from_artifacts(
            world,
            query,
            [item for item in essential if item is not artifact],
            enforce_preconditions=False,
        )
        != query.answer
        for artifact in essential
    )


def test_real_repo_episode_binds_body_facts_into_state_answers_and_lineage() -> None:
    workflow = _repo_episode()
    spec, world, artifacts, queries = _materialize_real(workflow)

    assert spec["real_workflow_id"] == workflow.workflow_id
    assert world.state.values["repo:commit:cafe123:version"] == "2.4.1"
    assert world.state.values["real:release:version"] == "2.4.1"
    assert world.state.values["real:release:license"] == "Apache-2.0"
    assert {query.query_type for query in queries} >= {
        "version_selection",
        "ci_regression_origin",
        "license_compatibility",
    }

    real_artifacts = [a for a in artifacts if a.slots.get("real_workflow_record")]
    assert len(real_artifacts) == len(workflow.records)
    assert all(
        record.text in artifact.text
        for record, artifact in zip(workflow.records, real_artifacts)
    )
    assert all(
        artifact_classification(artifact).source_origin
        == SourceOrigin.REAL_PRIVATE_EXPORT
        for artifact in real_artifacts
    )
    assert all(
        artifact_classification(artifact).workflow_kind == WorkflowKind.HYBRID_CAUSAL
        for artifact in real_artifacts
    )
    assert {
        artifact_classification(artifact).workflow_id for artifact in real_artifacts
    } == {world.spec["world_id"]}
    assert {artifact.slots["source_workflow_id"] for artifact in real_artifacts} == {
        workflow.workflow_id
    }
    assert {
        artifact_classification(artifact).provenance_id for artifact in real_artifacts
    } == {workflow.lineage.provenance_id}


def test_release_cycles_create_band_specific_executable_proofs() -> None:
    records = list(_repo_episode().records)
    records.extend(
        [
            WorkflowRecord(
                "commit:feed456",
                "commit",
                "2025-02-01T10:00:00Z",
                "Commit feed456 selects parser-core version 2.5.0.",
                ("release:v2.4.1",),
                attributes={
                    "commit": "feed456",
                    "package": "parser-core",
                    "version": "2.5.0",
                },
            ),
            WorkflowRecord(
                "ci:2.5.0",
                "ci_run",
                "2025-02-02T10:00:00Z",
                "CI run 250 for commit feed456 passed.",
                ("commit:feed456",),
                {"run": "250", "result": "passed"},
            ),
            WorkflowRecord(
                "release:v2.5.0",
                "release",
                "2025-02-03T10:00:00Z",
                "Release tag v2.5.0 published from the linked validation.",
                ("ci:2.5.0",),
                {"tag": "v2.5.0"},
            ),
            WorkflowRecord(
                "commit:f00baa7",
                "commit",
                "2025-03-01T10:00:00Z",
                "Commit f00baa7 selects parser-core version 2.6.0.",
                ("release:v2.5.0",),
                {
                    "commit": "f00baa7",
                    "package": "parser-core",
                    "version": "2.6.0",
                },
            ),
            WorkflowRecord(
                "ci:2.6.0",
                "ci_run",
                "2025-03-02T10:00:00Z",
                "CI run 260 for commit f00baa7 passed.",
                ("commit:f00baa7",),
                {"run": "260", "result": "passed"},
            ),
            WorkflowRecord(
                "release:v2.6.0",
                "release",
                "2025-03-03T10:00:00Z",
                "Release tag v2.6.0 published from the linked validation.",
                ("ci:2.6.0",),
                {"tag": "v2.6.0"},
            ),
        ]
    )
    _, world, _, queries = _materialize_real(_workflow(records))
    version_queries = [
        query for query in queries if query.query_type == "version_selection"
    ]
    [supersession_trace] = [
        query for query in queries if query.query_type == "release_supersession_trace"
    ]

    assert len(version_queries) == 2
    short, long = version_queries
    assert short.preferred_length_buckets == ["16k", "32k"]
    assert long.preferred_length_buckets == ["64k"]
    assert short.answer == "parser-core@2.5.0 -> v2.5.0"
    assert long.answer == "parser-core@2.6.0 -> v2.6.0"
    assert short.semantic_growth_group == long.semantic_growth_group
    assert len(long.sufficient_event_ids) > len(short.sufficient_event_ids)
    assert long.proof_depth > short.proof_depth
    assert supersession_trace.answer == (
        "parser-core@2.5.0 -> v2.5.0 | parser-core@2.6.0 -> v2.6.0"
    )
    assert supersession_trace.cf_answer == (
        "parser-core@2.5.0 -> v2.5.0 | BLOCKED-v2.6.0"
    )
    assert supersession_trace.motif == "release_supersession_trace"
    assert supersession_trace.preferred_length_buckets == ["64k"]
    assert supersession_trace.program_ops != long.program_ops
    trace_essential = [
        artifact
        for artifact in render_code(world)
        if artifact.artifact_id in set(supersession_trace.essential_artifact_ids)
    ]
    trace_strict = [
        artifact
        for artifact in render_code(world)
        if set(artifact.reveals_events).intersection(
            supersession_trace.sufficient_event_ids
        )
    ]
    assert (
        answer_from_artifacts(
            world, supersession_trace, trace_strict, enforce_preconditions=True
        )
        == supersession_trace.answer
    )
    assert all(
        answer_from_artifacts(
            world,
            supersession_trace,
            [artifact for artifact in trace_essential if artifact is not dropped],
            enforce_preconditions=False,
        )
        != supersession_trace.answer
        for dropped in trace_essential
    )
    _, trace_cf_artifacts = render_cf_view(world, supersession_trace)
    trace_cf_strict = [
        artifact
        for artifact in trace_cf_artifacts
        if set(artifact.reveals_events).intersection(
            supersession_trace.sufficient_event_ids
        )
    ]
    assert (
        answer_from_artifacts(
            world,
            supersession_trace,
            trace_cf_strict,
            extra_overrides={
                supersession_trace.cf_event_id: supersession_trace.cf_param_updates
            },
            enforce_preconditions=True,
        )
        == supersession_trace.cf_answer
    )
    assert (
        answer_from_artifacts(
            world,
            long,
            [
                artifact
                for artifact in render_code(world)
                if set(artifact.reveals_events).intersection(long.sufficient_event_ids)
            ],
            enforce_preconditions=True,
        )
        == long.answer
    )


def test_repeated_license_snapshots_are_canonicalized_before_strict_replay() -> None:
    def episode(version: str, month: int) -> RealWorkflow:
        return _workflow(
            [
                WorkflowRecord(
                    "LICENSE",
                    "license",
                    f"2025-{month:02d}-01T00:00:00Z",
                    "Apache License, Version 2.0",
                    attributes={"license": "Apache-2.0", "compatible": True},
                ),
                WorkflowRecord(
                    f"commit:{version}",
                    "commit",
                    f"2025-{month:02d}-02T00:00:00Z",
                    f"Commit c{month} selects parser-core version {version}.",
                    ("LICENSE",),
                    {
                        "commit": f"c{month}",
                        "package": "parser-core",
                        "version": version,
                    },
                ),
                WorkflowRecord(
                    f"ci:{version}",
                    "ci_run",
                    f"2025-{month:02d}-03T00:00:00Z",
                    f"CI run {month} for commit c{month} passed.",
                    (f"commit:{version}",),
                    {"run": str(month), "result": "passed"},
                ),
                WorkflowRecord(
                    f"release:v{version}",
                    "release",
                    f"2025-{month:02d}-04T00:00:00Z",
                    f"Release tag v{version} published.",
                    (f"ci:{version}",),
                    {"tag": f"v{version}"},
                ),
            ]
        )

    spec = sample_code_spec(
        73,
        n_parallel=0,
        real_workflows=[episode("2.4.1", 1), episode("2.5.0", 2)],
    )
    world = simulate_code(spec)["focal"]
    artifacts = render_code(world)
    query = [
        item
        for item in build_code_queries(world)
        if item.query_type == "version_selection"
    ][-1]

    selected = real_workflow_artifacts_for_query(artifacts, query)
    license_artifacts = [
        artifact
        for artifact in selected
        if (artifact.slots or {}).get("source_record_id") == "LICENSE"
    ]
    strict = [
        artifact
        for artifact in selected
        if set(artifact.reveals_events).intersection(query.sufficient_event_ids)
    ]

    assert len(license_artifacts) == 1
    assert (
        answer_from_artifacts(world, query, strict, enforce_preconditions=True)
        == query.answer
    )


def test_duplicate_release_tags_do_not_fake_a_longer_release_cycle() -> None:
    def episode(suffix: str, month: int) -> RealWorkflow:
        return _workflow(
            [
                WorkflowRecord(
                    f"commit:{suffix}",
                    "commit",
                    f"2025-{month:02d}-01T00:00:00Z",
                    f"Commit {suffix} selects parser-core version 2.4.1.",
                    attributes={
                        "commit": suffix,
                        "package": "parser-core",
                        "version": "2.4.1",
                    },
                ),
                WorkflowRecord(
                    f"ci:{suffix}",
                    "ci_run",
                    f"2025-{month:02d}-02T00:00:00Z",
                    f"CI run {suffix} passed.",
                    (f"commit:{suffix}",),
                    {"run": suffix, "result": "passed"},
                ),
                WorkflowRecord(
                    "release:v2.4.1",
                    "release",
                    f"2025-{month:02d}-03T00:00:00Z",
                    "Release tag v2.4.1 published.",
                    (f"ci:{suffix}",),
                    {"tag": "v2.4.1"},
                ),
            ]
        )

    spec = sample_code_spec(
        73,
        n_parallel=0,
        real_workflows=[episode("aaa1111", 1), episode("bbb2222", 2)],
    )
    queries = [
        query
        for query in build_code_queries(simulate_code(spec)["focal"])
        if query.query_type == "version_selection"
    ]

    assert len(queries) == 1
    assert queries[0].preferred_length_buckets == ["16k"]


def test_duplicate_release_tag_uses_the_richest_executable_episode() -> None:
    sparse = _workflow(
        [
            WorkflowRecord(
                "commit:sparse",
                "commit",
                "2025-01-01T00:00:00Z",
                "Commit sparse selects parser-core version 2.4.1.",
                attributes={
                    "commit": "sparse",
                    "package": "parser-core",
                    "version": "2.4.1",
                },
            ),
            WorkflowRecord(
                "ci:sparse",
                "ci_run",
                "2025-01-02T00:00:00Z",
                "CI sparse passed.",
                ("commit:sparse",),
                {"run": "sparse", "result": "passed"},
            ),
            WorkflowRecord(
                "release:v2.4.1",
                "release",
                "2025-01-03T00:00:00Z",
                "Release v2.4.1 published.",
                ("ci:sparse",),
                {"tag": "v2.4.1"},
            ),
        ]
    )
    rich = _workflow(
        [
            WorkflowRecord(
                "issue:rich",
                "issue",
                "2025-02-01T00:00:00Z",
                "Issue rich records the recovery requirement.",
            ),
            WorkflowRecord(
                "commit:rich",
                "commit",
                "2025-02-02T00:00:00Z",
                "Commit rich selects parser-core version 2.4.1.",
                ("issue:rich",),
                {
                    "commit": "rich",
                    "package": "parser-core",
                    "version": "2.4.1",
                },
            ),
            WorkflowRecord(
                "ci:rich",
                "ci_run",
                "2025-02-03T00:00:00Z",
                "CI rich passed.",
                ("commit:rich",),
                {"run": "rich", "result": "passed"},
            ),
            WorkflowRecord(
                "release:v2.4.1",
                "release",
                "2025-02-04T00:00:00Z",
                "Release v2.4.1 published from the richer episode.",
                ("ci:rich",),
                {"tag": "v2.4.1"},
            ),
        ]
    )
    spec = sample_code_spec(73, n_parallel=0, real_workflows=[sparse, rich])
    world = simulate_code(spec)["focal"]
    query = next(
        query
        for query in build_code_queries(world)
        if query.query_type == "version_selection"
    )
    essential_records = {
        str(event.params.get("record_id") or "")
        for event in world.events
        if event.id in set(query.essential_event_ids)
    }

    assert "commit:rich" in essential_records
    assert "commit:sparse" not in essential_records


def test_version_selection_falls_back_from_invalid_richest_prior_release() -> None:
    def release_episode(
        version: str, month: int, *, passed: bool, rich: bool = False
    ) -> RealWorkflow:
        records = []
        parent = ""
        for index in range(4 if rich else 1):
            record_id = f"commit:{version}:{index}"
            selection_attributes = {
                "commit": f"sha-{version}-{index}",
                "package": "parser-core",
                "version": version,
            }
            records.append(
                WorkflowRecord(
                    record_id,
                    "commit",
                    f"2025-{month:02d}-01T0{index}:00:00Z",
                    f"Commit {record_id} selects parser-core version {version}.",
                    (parent,) if parent else (),
                    selection_attributes,
                )
            )
            parent = record_id
        ci_id = f"ci:{version}"
        records.append(
            WorkflowRecord(
                ci_id,
                "ci_run",
                f"2025-{month:02d}-02T00:00:00Z",
                f"CI {version} {'passed' if passed else 'failed'}.",
                (parent,),
                {
                    "run": version,
                    "result": "passed" if passed else "failed",
                },
            )
        )
        records.append(
            WorkflowRecord(
                f"release:v{version}",
                "release",
                f"2025-{month:02d}-03T00:00:00Z",
                f"Release v{version} published.",
                (ci_id,),
                {"tag": f"v{version}"},
            )
        )
        return _workflow(records)

    spec = sample_code_spec(
        73,
        n_parallel=0,
        real_workflows=[
            release_episode("1.0.0", 1, passed=True),
            release_episode("2.0.0", 2, passed=False, rich=True),
            release_episode("3.0.0", 3, passed=True),
        ],
    )
    queries = [
        query
        for query in build_code_queries(simulate_code(spec)["focal"])
        if query.query_type == "version_selection"
    ]

    assert [query.answer for query in queries] == [
        "parser-core@1.0.0 -> v1.0.0",
        "parser-core@3.0.0 -> v3.0.0",
    ]
    assert [query.cf_answer for query in queries] == [
        "BLOCKED-v1.0.0",
        "BLOCKED-v3.0.0",
    ]


def test_version_selection_ignores_skipped_ci_when_a_gate_passed() -> None:
    def release_episode(version: str, month: int) -> RealWorkflow:
        return _workflow(
            [
                WorkflowRecord(
                    f"commit:{version}",
                    "commit",
                    f"2025-{month:02d}-01T00:00:00Z",
                    f"Commit sha-{version} selects parser-core version {version}.",
                    (),
                    {
                        "commit": f"sha-{version}",
                        "package": "parser-core",
                        "version": version,
                    },
                ),
                WorkflowRecord(
                    f"ci:{version}:skipped",
                    "ci_run",
                    f"2025-{month:02d}-02T00:00:00Z",
                    f"CI check skip-matrix for sha-{version} conclusion=skipped",
                    (f"commit:{version}",),
                    {"run": f"skip-{version}", "conclusion": "skipped"},
                ),
                WorkflowRecord(
                    f"ci:{version}",
                    "ci_run",
                    f"2025-{month:02d}-02T00:01:00Z",
                    f"CI check linux for sha-{version} conclusion=success",
                    (f"commit:{version}",),
                    {"run": version, "result": "passed"},
                ),
                WorkflowRecord(
                    f"release:v{version}",
                    "release",
                    f"2025-{month:02d}-03T00:00:00Z",
                    f"Release v{version} published.",
                    (f"ci:{version}", f"ci:{version}:skipped"),
                    {"tag": f"v{version}"},
                ),
            ]
        )

    queries = [
        query
        for query in build_code_queries(
            simulate_code(
                sample_code_spec(
                    73,
                    n_parallel=0,
                    real_workflows=[
                        release_episode("1.0.0", 1),
                        release_episode("2.0.0", 2),
                    ],
                )
            )["focal"]
        )
        if query.query_type == "version_selection"
    ]

    assert [query.preferred_length_buckets for query in queries] == [
        ["16k", "32k"],
        ["64k"],
    ]
    assert [query.answer for query in queries] == [
        "parser-core@1.0.0 -> v1.0.0",
        "parser-core@2.0.0 -> v2.0.0",
    ]
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k", "32k"],
        ["64k"],
    ]


def test_real_repo_task_families_have_strict_cross_record_proofs_and_cf_twins() -> None:
    _, world, artifacts, queries = _materialize_real(_repo_episode())
    by_type = {query.query_type: query for query in queries}
    expected = {
        "version_selection": "parser-core@2.4.1 -> v2.4.1",
        "ci_regression_origin": "deadbee :: test_folded_headers",
        "license_compatibility": "parser-core@2.4.1 :: Apache-2.0 :: compatible",
    }

    for query_type, answer in expected.items():
        query = by_type[query_type]
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in set(query.essential_artifact_ids)
        ]
        assert query.answer == answer
        assert len(essential) >= 2
        assert (
            answer_from_artifacts(world, query, essential, enforce_preconditions=False)
            == answer
        )
        strict = [
            artifact
            for artifact in artifacts
            if set(artifact.reveals_events).intersection(query.sufficient_event_ids)
        ]
        assert (
            answer_from_artifacts(world, query, strict, enforce_preconditions=True)
            == answer
        )
        assert all(
            answer_from_artifacts(world, query, [artifact], enforce_preconditions=True)
            != answer
            for artifact in essential
        )
        assert all(
            answer.lower() not in artifact.text.lower() for artifact in essential
        )
        for dropped in essential:
            assert (
                answer_from_artifacts(
                    world,
                    query,
                    [artifact for artifact in essential if artifact is not dropped],
                    enforce_preconditions=False,
                )
                != answer
            )

        _, cf_artifacts = render_cf_view(world, query)
        filtered_factual = real_workflow_artifacts_for_query(artifacts, query)
        filtered_cf = real_workflow_artifacts_for_query(cf_artifacts, query)
        assert [artifact.artifact_id for artifact in filtered_factual] == [
            artifact.artifact_id for artifact in filtered_cf
        ]
        cf_strict = [
            artifact
            for artifact in cf_artifacts
            if set(artifact.reveals_events).intersection(query.sufficient_event_ids)
        ]
        assert query.cf_answer not in {"", "unknown", answer}
        assert (
            answer_from_artifacts(
                world,
                query,
                cf_strict,
                extra_overrides={query.cf_event_id: query.cf_param_updates},
                enforce_preconditions=True,
            )
            == query.cf_answer
        )
        if query_type == "ci_regression_origin":
            assert query.cf_op == "origin_commit"
            assert set(query.cf_param_updates) == {"commit"}
            assert "NO-REGRESSION" not in query.cf_answer
            cf_essential = [
                artifact
                for artifact in cf_artifacts
                if artifact.artifact_id in set(query.essential_artifact_ids)
            ]
            assert all(
                answer_from_artifacts(
                    world,
                    query,
                    [artifact],
                    extra_overrides={query.cf_event_id: query.cf_param_updates},
                    enforce_preconditions=True,
                )
                != query.cf_answer
                for artifact in cf_essential
            )


def test_real_ci_selects_the_longest_recovery_chain() -> None:
    records = [
        WorkflowRecord(
            "commit:near",
            "commit",
            "2025-01-01T08:00:00Z",
            "Commit near111 introduced the near regression.",
            (),
            {"commit": "near111"},
        ),
        WorkflowRecord(
            "ci:near-fail",
            "ci_run",
            "2025-01-01T09:00:00Z",
            "CI check near-check for commit near111; conclusion=failure.",
            ("commit:near",),
            {"head_sha": "near111", "name": "near-check", "conclusion": "failure"},
        ),
        WorkflowRecord(
            "ci:near-pass",
            "ci_run",
            "2025-01-01T10:00:00Z",
            "CI check near-check for commit near111; conclusion=success.",
            ("commit:near",),
            {"head_sha": "near111", "name": "near-check", "conclusion": "success"},
        ),
        WorkflowRecord(
            "commit:far",
            "commit",
            "2025-01-02T08:00:00Z",
            "Commit far222 introduced the far regression after a detailed migration "
            "and compatibility review across every supported runtime.",
            (),
            {"commit": "far222"},
        ),
        WorkflowRecord(
            "ci:far-fail",
            "ci_run",
            "2025-01-02T09:00:00Z",
            "CI check far-check for commit far222; conclusion=failure after the "
            "complete cross-platform compatibility and migration matrix.",
            ("commit:far",),
            {"head_sha": "far222", "name": "far-check", "conclusion": "failure"},
        ),
        *[
            WorkflowRecord(
                f"issue:{index}",
                "issue",
                f"2025-01-{3 + index:02d}T09:00:00Z",
                f"Issue {index} tracks an unrelated maintenance item.",
                (),
                {},
            )
            for index in range(12)
        ],
        WorkflowRecord(
            "ci:far-pass",
            "ci_run",
            "2025-01-20T10:00:00Z",
            "CI check far-check for commit far222; conclusion=success after the "
            "complete cross-platform compatibility and migration matrix.",
            ("commit:far",),
            {"head_sha": "far222", "name": "far-check", "conclusion": "success"},
        ),
    ]

    _, _, _, queries = _materialize_real(_workflow(records))
    query = next(item for item in queries if item.query_type == "ci_regression_origin")

    assert "ci:far-fail" in query.query_id
    assert query.answer == "far222 :: far-check"


def test_real_workflow_ignores_attribute_values_absent_from_source_body() -> None:
    records = list(_repo_episode().records)
    recovery = records[5]
    records[5] = WorkflowRecord(
        recovery.record_id,
        recovery.kind,
        recovery.occurred_at,
        recovery.text,
        recovery.links,
        {**recovery.attributes, "version": "9.9.9"},
        recovery.source_pointer,
    )
    _, world, _, queries = _materialize_real(_workflow(records))

    assert world.state.values["repo:commit:cafe123:version"] == "2.4.1"
    version_query = next(q for q in queries if q.query_type == "version_selection")
    assert "9.9.9" not in version_query.answer


def test_real_ci_uses_the_body_visible_check_name_as_regression_origin() -> None:
    records = list(_repo_episode().records)
    records[2] = WorkflowRecord(
        "ci:failed",
        "ci_run",
        "2025-01-03T10:30:00Z",
        "CI check parser-tests for commit deadbee; status=completed conclusion=failure.",
        ("commit:deadbee",),
        {
            "head_sha": "deadbee",
            "name": "parser-tests",
            "conclusion": "failure",
        },
    )
    records[6] = WorkflowRecord(
        "ci:passed",
        "ci_run",
        "2025-01-08T15:00:00Z",
        "CI check parser-tests for commit cafe123; status=completed conclusion=success.",
        ("commit:cafe123",),
        {
            "head_sha": "cafe123",
            "name": "parser-tests",
            "conclusion": "success",
        },
    )
    _, _, _, queries = _materialize_real(_workflow(records))

    query = next(q for q in queries if q.query_type == "ci_regression_origin")
    assert query.answer == "deadbee :: parser-tests"


def test_real_ci_counterfactual_commit_preserves_sha_shape() -> None:
    original = "0123456789abcdef0123456789abcdef01234567"
    records = list(_repo_episode().records)
    records[1] = WorkflowRecord(
        "commit:origin",
        "commit",
        "2025-01-03T10:00:00Z",
        f"Commit {original} introduced the parser-core regression.",
        ("issue:17",),
        {"commit": original},
    )
    records[2] = WorkflowRecord(
        "ci:failed",
        "ci_run",
        "2025-01-03T10:30:00Z",
        f"CI run ci-failed for commit {original} reports failed test_folded_headers.",
        ("commit:origin",),
        {
            "run": "ci-failed",
            "result": "failed",
            "test": "test_folded_headers",
            "head_sha": original,
        },
    )
    _, _, _, queries = _materialize_real(_workflow(records))
    query = next(item for item in queries if item.query_type == "ci_regression_origin")
    changed = str(query.cf_param_updates["commit"])

    assert len(changed) == len(original)
    assert all(character in "0123456789abcdef" for character in changed)
    assert changed != original
    assert query.cf_answer.endswith(" :: test_folded_headers")
    assert query.cf_answer.startswith(changed)


def test_same_day_success_before_failure_is_not_a_recovery() -> None:
    workflow = _workflow(
        [
            WorkflowRecord(
                "commit:deadbee",
                "commit",
                "2025-01-03T08:00:00Z",
                "Commit deadbee introduced the parser regression.",
                (),
                {"commit": "deadbee"},
            ),
            WorkflowRecord(
                "ci:passed-first",
                "ci_run",
                "2025-01-03T09:00:00Z",
                "CI check parser-tests for commit deadbee; conclusion=success.",
                ("commit:deadbee",),
                {
                    "head_sha": "deadbee",
                    "name": "parser-tests",
                    "conclusion": "success",
                },
            ),
            WorkflowRecord(
                "ci:failed-later",
                "ci_run",
                "2025-01-03T10:00:00Z",
                "CI check parser-tests for commit deadbee; conclusion=failure.",
                ("commit:deadbee",),
                {
                    "head_sha": "deadbee",
                    "name": "parser-tests",
                    "conclusion": "failure",
                },
            ),
        ]
    )

    _, _, _, queries = _materialize_real(workflow)

    assert not any(query.query_type == "ci_regression_origin" for query in queries)


def test_real_license_decision_joins_pr_body_commit_and_repo_license() -> None:
    workflow = _workflow(
        [
            WorkflowRecord(
                "license:repo",
                "license",
                "2025-01-01T00:00:00Z",
                "Apache License Version 2.0",
                (),
                {"spdx_id": "Apache-2.0"},
            ),
            WorkflowRecord(
                "pull_request:419",
                "pull_request",
                "2025-01-02T00:00:00Z",
                "The dependency graph is fully compatible with Apache-2.0.",
                ("license:repo",),
            ),
            WorkflowRecord(
                "commit:43660a9",
                "commit",
                "2025-01-03T00:00:00Z",
                "Commit 43660a9 standardizes the distribution metadata.",
                ("pull_request:419",),
                {"sha": "43660a9"},
            ),
            WorkflowRecord(
                "review:419",
                "review",
                "2025-01-03T12:00:00Z",
                "Review state: APPROVED\nThe license decision is supported by the scan.",
                ("pull_request:419", "commit:43660a9"),
                {"state": "APPROVED"},
            ),
            WorkflowRecord(
                "merge:419",
                "merge",
                "2025-01-04T00:00:00Z",
                "Merged pull request 419 from validated head commit 43660a9.",
                ("pull_request:419", "commit:43660a9", "review:419"),
                {"commit": "43660a9"},
            ),
        ]
    )
    _, world, artifacts, queries = _materialize_real(workflow)

    query = next(q for q in queries if q.query_type == "license_compatibility")
    assert query.answer == "43660a9 :: Apache-2.0 :: compatible"
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in set(query.essential_artifact_ids)
    ]
    assert len(essential) >= 3
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=False)
        == query.answer
    )
    assert all(
        answer_from_artifacts(world, query, [artifact], enforce_preconditions=True)
        != query.answer
        for artifact in essential
    )


def test_unique_real_records_create_64k_event_bearing_multi_cycle_history() -> None:
    records: list[WorkflowRecord] = []
    previous = ""
    for index in range(72):
        record_id = f"commit:{index:03d}feed"
        unique_body = " ".join(
            f"cycle{index:03d}-change{part:04d}" for part in range(900)
        )
        text = (
            f"Commit {index:03d}feed carries parser-core version 3.{index}.0. "
            f"{unique_body}"
        )
        records.append(
            WorkflowRecord(
                record_id,
                "commit",
                f"2025-02-{1 + index // 24:02d}T{index % 24:02d}:00:00Z",
                text,
                (previous,) if previous else (),
                {
                    "commit": f"{index:03d}feed",
                    "package": "parser-core",
                    "version": f"3.{index}.0",
                },
            )
        )
        previous = record_id
    records.extend(
        [
            WorkflowRecord(
                "ci:long-pass",
                "ci_run",
                "2025-02-04T00:00:00Z",
                "CI run ci-long-pass reports passed after the complete history replay.",
                (previous,),
                {"run": "ci-long-pass", "result": "passed"},
            ),
            WorkflowRecord(
                "release:v3.71.0",
                "release",
                "2025-02-04T01:00:00Z",
                "Release tag v3.71.0 published from the linked validation.",
                ("ci:long-pass",),
                {"tag": "v3.71.0"},
            ),
        ]
    )
    _, world, artifacts, queries = _materialize_real(_workflow(records))
    query = next(q for q in queries if q.query_type == "version_selection")
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in set(query.essential_artifact_ids)
    ]

    real_history = [
        artifact for artifact in artifacts if artifact.slots.get("real_workflow_record")
    ]
    assert len(essential) == 3
    assert len({artifact.text for artifact in real_history}) == len(real_history)
    assert estimate_tokens(join_artifacts(real_history)) >= 64_000
    assert len(world.state.history) >= len(records) * 3
    assert world.state.values["real:release_cycle"] == 1
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=False)
        == query.answer
    )
