from __future__ import annotations

import hashlib

import pytest

from longworld.core.provenance import SourceLineage
from longworld.core.realworkflow import RealWorkflow, WorkflowRecord
from longworld.core.taxonomy import SourceOrigin
from longworld.domains.codeforge.queries import build_code_queries
from longworld.domains.codeforge.schema import sample_code_spec
from longworld.domains.codeforge.simulate import _release_version, simulate_code


def _release_cycle(
    version: str,
    month: int,
    *,
    tag_family: str = "crates",
    tag: str | None = None,
) -> RealWorkflow:
    tag = tag or f"{tag_family}_v{version}"
    commit = f"sha-{version}"
    records = (
        WorkflowRecord(
            f"commit:{commit}",
            "commit",
            f"2026-{month:02d}-01T00:00:00Z",
            f"Commit {commit} selects oxc version {version}.",
            attributes={"commit": commit, "package": "oxc", "version": version},
        ),
        WorkflowRecord(
            f"ci:{version}",
            "ci_run",
            f"2026-{month:02d}-02T00:00:00Z",
            f"CI for {commit} passed.",
            (f"commit:{commit}",),
            {"run": version, "result": "passed"},
        ),
        WorkflowRecord(
            f"release:{tag}",
            "release",
            f"2026-{month:02d}-03T00:00:00Z",
            f"Release {tag} published.",
            (f"ci:{version}",),
            {"tag": tag},
        ),
    )
    digest = hashlib.sha256("\n".join(row.text for row in records).encode()).hexdigest()
    return RealWorkflow(
        workflow_id=f"git:{digest[:16]}",
        source_kind="git_export",
        source_origin=SourceOrigin.REAL_PUBLIC,
        lineage=SourceLineage(
            provenance_id=f"sha256:{digest}",
            url="https://github.com/oxc-project/oxc",
            license="MIT",
            retrieved_at="2026-08-29T00:00:00Z",
            parser="git_workflow_json@1",
            sha256=digest,
            revision=commit,
        ),
        records=records,
        facts={},
    )


def test_prefixed_semver_release_tags_form_real_multicycle_trace() -> None:
    workflows = [
        _release_cycle("0.144.0", 6),
        _release_cycle("0.145.0", 7),
        _release_cycle("0.146.0", 8),
    ]
    spec = sample_code_spec(124, n_parallel=0, real_workflows=workflows)
    queries = build_code_queries(simulate_code(spec)["focal"])

    traces = [
        query for query in queries if query.query_type == "release_supersession_trace"
    ]

    assert [query.preferred_length_buckets for query in traces] == [["32k"], ["64k"]]
    assert traces[-1].answer == (
        "oxc@0.144.0 -> crates_v0.144.0 | "
        "oxc@0.145.0 -> crates_v0.145.0 | "
        "oxc@0.146.0 -> crates_v0.146.0"
    )


@pytest.mark.parametrize("tag", ["v1.2.3", "1.2.3"])
def test_standard_semver_release_tags_remain_supported(tag: str) -> None:
    spec = sample_code_spec(
        124,
        n_parallel=0,
        real_workflow=_release_cycle("1.2.3", 6, tag=tag),
    )
    release = next(
        event
        for event in simulate_code(spec)["focal"].events
        if event.type == "repo_record" and event.params.get("record_kind") == "release"
    )

    assert _release_version(release) == (1, 2, 3)


def test_distinct_tag_families_do_not_share_a_supersession_chain() -> None:
    spec = sample_code_spec(
        124,
        n_parallel=0,
        real_workflows=[
            _release_cycle("0.144.0", 6),
            _release_cycle("1.0.0", 7, tag_family="napi"),
            _release_cycle("0.145.0", 8),
        ],
    )
    world = simulate_code(spec)["focal"]
    releases = {
        str(event.params.get("tag")): event
        for event in world.events
        if event.type == "repo_record" and event.params.get("record_kind") == "release"
    }
    crates_first = releases["crates_v0.144.0"]
    napi = releases["napi_v1.0.0"]
    crates_second = releases["crates_v0.145.0"]

    assert crates_first.id in crates_second.causal_inputs
    assert crates_first.id not in napi.causal_inputs
    assert napi.id not in crates_second.causal_inputs


@pytest.mark.parametrize(
    "tag",
    ["release-2026", "build-2025-1.2.3", "v1.2"],
)
def test_non_semver_release_tags_are_rejected(tag: str) -> None:
    spec = sample_code_spec(
        124,
        n_parallel=0,
        real_workflow=_release_cycle("1.2.3", 6, tag=tag),
    )
    release = next(
        event
        for event in simulate_code(spec)["focal"].events
        if event.type == "repo_record" and event.params.get("record_kind") == "release"
    )

    assert _release_version(release) == ()
