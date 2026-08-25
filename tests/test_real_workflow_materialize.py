from __future__ import annotations

import hashlib

from longworld.core.provenance import SourceLineage
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
from longworld.core.sampler import materialize
from longworld.core.taxonomy import SourceOrigin


def test_materialize_passes_real_episode_bodies_into_codeforge() -> None:
    text = "Release tag v1.2.3 published after the linked gates completed."
    digest = hashlib.sha256(text.encode()).hexdigest()
    workflow = RealWorkflow(
        workflow_id=f"git:{digest[:16]}",
        source_kind="git_export",
        source_origin=SourceOrigin.REAL_PUBLIC,
        lineage=SourceLineage(
            provenance_id=f"sha256:{digest}",
            url="https://github.com/example/project",
            license="Apache-2.0",
            retrieved_at="2026-08-20T12:00:00Z",
            parser="git_workflow_json@1",
            sha256=digest,
            revision="abc123",
            source_path="workflow.json",
        ),
        records=(
            WorkflowRecord(
                "release:v1.2.3",
                "release",
                "2026-08-20T12:00:00Z",
                text,
                attributes={"tag": "v1.2.3"},
            ),
        ),
        facts={},
    )

    materialized = materialize(
        2,
        n_parallel=0,
        domain="codeforge",
        real_workflows=[workflow],
    )

    assert materialized.spec["real_workflow_ids"] == [workflow.workflow_id]
    assert any(text in artifact.text for artifact in materialized.artifacts["focal"])


def test_release_cycle_metric_deduplicates_same_repo_release_across_pr_episodes() -> (
    None
):
    workflows: list[RealWorkflow] = []
    for index in range(2):
        text = f"Release tag v1.2.3 published after pull request {index}."
        digest = hashlib.sha256(text.encode()).hexdigest()
        workflows.append(
            RealWorkflow(
                workflow_id=f"git:{digest[:16]}",
                source_kind="git_export",
                source_origin=SourceOrigin.REAL_PUBLIC,
                lineage=SourceLineage(
                    provenance_id=f"sha256:{digest}",
                    url="https://github.com/example/project",
                    license="Apache-2.0",
                    retrieved_at="2026-08-20T12:00:00Z",
                    parser="git_workflow_json@1",
                    sha256=digest,
                    revision=f"abc12{index}",
                    source_path=f"workflow-{index}.json",
                ),
                records=(
                    WorkflowRecord(
                        f"release:v1.2.3:{index}",
                        "release",
                        f"2026-08-2{index}T12:00:00Z",
                        text,
                        attributes={"tag": "v1.2.3"},
                    ),
                ),
                facts={},
            )
        )

    materialized = materialize(
        2,
        n_parallel=0,
        domain="codeforge",
        real_workflows=workflows,
    )

    assert materialized.spec["real_release_cycles"] == 1

    releases = [
        event
        for event in materialized.worlds["focal"].events
        if event.type == "repo_record" and event.params.get("record_kind") == "release"
    ]
    assert len(releases) == 2
    assert releases[0].id not in releases[1].required_inputs
