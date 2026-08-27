from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from longworld.core.engine import answer_from_artifacts
from longworld.core.graph import graph_stats
from longworld.core.pack import estimate_tokens
from longworld.core.promotion import _replayed_source_metadata
from longworld.core.sampler import materialize
from longworld.core.sourceworkflow import SourceRecord, SourceWorkflow
from longworld.core.taxonomy import SourceOrigin, artifact_classification
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.core.wikiparse import (
    WIKI_COMMEMORATION_QUOTE,
    WIKI_EINSTEIN_LATE_QUOTE,
    WIKI_EINSTEIN_MID_QUOTE,
    WIKI_ELIZABETH_LATE_QUOTE,
    WIKI_ELIZABETH_MID_QUOTE,
    WIKI_JEFFERSON_LATE_QUOTE,
    WIKI_JEFFERSON_MID_QUOTE,
    WIKI_MLK_LATE_QUOTE,
    WIKI_MLK_MID_QUOTE,
    WIKI_NEWTON_LATE_QUOTE,
    WIKI_NEWTON_MID_QUOTE,
    WIKI_OBAMA_LATE_QUOTE,
    WIKI_OBAMA_MID_QUOTE,
    WIKI_POPULAR_CULTURE_QUOTE,
    WIKI_THATCHER_LATE_QUOTE,
    WIKI_THATCHER_MID_QUOTE,
    WIKI_TURING_LATE_QUOTE,
    WIKI_TURING_MID_QUOTE,
)
from scripts.generate import context_source_relation_count, real_source_relation_edges

SOURCE_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p5_ada_smoke"
)
TURING_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_source_rich_v1"
)
EINSTEIN_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_einstein_v1"
)
THATCHER_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_thatcher_v1"
)
NEWTON_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_newton_v1"
)
OBAMA_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_obama_v1"
)
ELIZABETH_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_elizabeth_ii_v1"
)
MLK_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_mlk_v1"
)
JEFFERSON_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_jefferson_v1"
)


def _record(
    *,
    record_id: str,
    kind: str,
    occurred_at: str,
    source_file: str,
    source_url: str,
    retrieval_url: str,
    attributes: tuple[tuple[str, str], ...],
    directory: Path = SOURCE_DIRECTORY,
) -> SourceRecord:
    text = (directory / source_file).read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    return SourceRecord(
        record_id=record_id,
        kind=kind,
        occurred_at=occurred_at,
        text=text,
        source_url=source_url,
        retrieval_url=retrieval_url,
        source_family=kind,
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=(),
        attributes=attributes,
    )


def _workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-974-r1360639004",
        kind="wikipedia_revision",
        occurred_at="2026-06-22T18:42:17Z",
        source_file="wikipedia-974-r1360639004.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1360639004",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1360639004"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1360639004"),
            ("page_id", "974"),
            ("title", "Ada Lovelace"),
            ("parent_revision_id", "1359380954"),
        ),
    )
    later = _record(
        record_id="page-974-r1370153024",
        kind="wikipedia_revision",
        occurred_at="2026-08-19T11:48:44Z",
        source_file="wikipedia-974-r1370153024.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1370153024",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1370153024"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1370153024"),
            ("page_id", "974"),
            ("title", "Ada Lovelace"),
            ("parent_revision_id", "1360639004"),
        ),
    )
    entity = _record(
        record_id="entity-Q7259-r2531694935",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-15T15:14:23Z",
        source_file="wikidata-Q7259-r2531694935.json",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7259"
        ),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7259"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2531694935"),
            ("entity_id", "Q7259"),
        ),
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:0123456789abcdef01234567",
        component_digest="0" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def ada_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_wiki_queries_stage_real_body_programs(ada_world) -> None:
    world, artifacts, queries = ada_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert [graph_stats(world, query)["proof_depth"] for query in queries] == [4, 5, 6]
    assert [len(query.essential_event_ids) for query in queries] == [4, 5, 7]
    copy_ids = {
        event.id
        for event in world.events
        if event.type == "wiki_claim_answer" and event.params.get("compose") == "copy"
    }
    compute_16k = next(
        event.id
        for event in world.events
        if event.type == "wiki_claim_answer"
        and event.params.get("control_tier") == "16k"
        and event.params.get("compose") != "copy"
    )
    assert len(copy_ids & set(queries[0].essential_event_ids)) == 2
    assert compute_16k in queries[0].essential_event_ids
    assert compute_16k in queries[1].essential_event_ids
    assert not (copy_ids & set(queries[1].essential_event_ids))
    assert not (copy_ids & set(queries[2].essential_event_ids))
    assert len({query.base_task_group for query in queries}) == 1
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1815-12-10"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_COMMEMORATION_QUOTE}" in answers[1]
    assert "ENTITY:Q7259" in answers[1]
    assert f"POP:{WIKI_POPULAR_CULTURE_QUOTE}" in answers[2]
    assert all("Workflow:" not in query.question for query in queries)
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    for query, expected_tokens in zip(queries, (10_000, 14_000, 20_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        assert all(query.answer not in item.text for item in essential)
        assert any(
            artifact_classification(item).source_origin is SourceOrigin.REAL_DERIVED
            for item in essential
        )


def test_wiki_hybrid_edges_count_compute_and_copy_rungs(ada_world) -> None:
    world, artifacts, queries = ada_world
    relation_counts = []
    hybrid_counts = []
    authentic_counts = []
    for query in queries:
        generated_edges = real_source_relation_edges(world, query, artifacts)
        metadata = _replayed_source_metadata(world, query, artifacts)
        relation_counts.append(
            context_source_relation_count(world, artifacts, spec=query)
        )
        hybrid_counts.append(len(metadata["hybrid_causal_edges"]))
        authentic_counts.append(len(metadata["authentic_source_relation_edges"]))
        assert generated_edges == metadata["hybrid_causal_edges"]
        assert generated_edges
        assert all(
            edge["relation_provenance"] == "synthetic_executable"
            for edge in generated_edges
        )
        assert all(
            edge["parent_record_id"] != edge["child_record_id"]
            for edge in generated_edges
        )
    assert relation_counts == [3, 6, 8]
    assert hybrid_counts == [3, 6, 8]
    assert authentic_counts == [0, 0, 0]


def test_wiki_cf_remove_one_and_surface_gates(ada_world) -> None:
    world, artifacts, queries = ada_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1816-12-10")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{birth date|df=y|1815|12|10}}",
            "{{birth date|df=y|0000|12|10}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _turing_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-1208-r1368351634",
        kind="wikipedia_revision",
        occurred_at="2026-08-08T15:22:07Z",
        source_file="wikipedia-1208-r1368351634.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1368351634",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1368351634"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1368351634"),
            ("page_id", "1208"),
            ("title", "Alan Turing"),
            ("parent_revision_id", "1367960944"),
        ),
        directory=TURING_DIRECTORY,
    )
    later = _record(
        record_id="page-1208-r1369298323",
        kind="wikipedia_revision",
        occurred_at="2026-08-14T03:52:03Z",
        source_file="wikipedia-1208-r1369298323.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1369298323",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1369298323"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1369298323"),
            ("page_id", "1208"),
            ("title", "Alan Turing"),
            ("parent_revision_id", "1368351634"),
        ),
        directory=TURING_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q7251-r2533461519",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-19T23:04:46Z",
        source_file="wikidata-Q7251-r2533461519.json",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7251"
        ),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7251"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2533461519"),
            ("entity_id", "Q7251"),
        ),
        directory=TURING_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:turing00000000000000000001",
        component_digest="2" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def turing_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        11,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_turing_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_turing_queries_stage_real_body_programs(turing_world) -> None:
    world, artifacts, queries = turing_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert [len(query.essential_event_ids) for query in queries] == [4, 5, 7]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1912-06-23"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_TURING_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q7251" in answers[1]
    assert f"POP:{WIKI_TURING_LATE_QUOTE}" in answers[2]
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        assert all(query.answer not in item.text for item in essential)


def test_turing_cf_remove_one_and_surface_gates(turing_world) -> None:
    world, artifacts, queries = turing_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1913-06-23")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{Birth date|df=y|1912|6|23}}",
            "{{Birth date|df=y|0000|6|23}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _einstein_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-736-r1367582180",
        kind="wikipedia_revision",
        occurred_at="2026-08-03T23:48:59Z",
        source_file="wikipedia-736-r1367582180.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1367582180",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1367582180"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1367582180"),
            ("page_id", "736"),
            ("title", "Albert Einstein"),
            ("parent_revision_id", "1367508458"),
        ),
        directory=EINSTEIN_DIRECTORY,
    )
    later = _record(
        record_id="page-736-r1370002284",
        kind="wikipedia_revision",
        occurred_at="2026-08-18T14:47:41Z",
        source_file="wikipedia-736-r1370002284.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1370002284",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1370002284"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1370002284"),
            ("page_id", "736"),
            ("title", "Albert Einstein"),
            ("parent_revision_id", "1367582180"),
        ),
        directory=EINSTEIN_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q937-r2536235382",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-25T14:20:03Z",
        source_file="wikidata-Q937-r2536235382.json",
        source_url=("https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q937"),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q937"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2536235382"),
            ("entity_id", "Q937"),
        ),
        directory=EINSTEIN_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:einstein000000000000000001",
        component_digest="3" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def einstein_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        13,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_einstein_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_einstein_queries_stage_real_body_programs(einstein_world) -> None:
    world, artifacts, queries = einstein_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert [len(query.essential_event_ids) for query in queries] == [4, 5, 7]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1879-03-14"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_EINSTEIN_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q937" in answers[1]
    assert f"POP:{WIKI_EINSTEIN_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_einstein_cf_remove_one_and_surface_gates(einstein_world) -> None:
    world, artifacts, queries = einstein_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1880-03-14")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{Birth date|df=yes|1879|3|14}}",
            "{{Birth date|df=yes|0000|3|14}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _thatcher_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-19831-r1370718369",
        kind="wikipedia_revision",
        occurred_at="2026-08-22T18:25:28Z",
        source_file="wikipedia-19831-r1370718369.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1370718369",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1370718369"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1370718369"),
            ("page_id", "19831"),
            ("title", "Margaret Thatcher"),
            ("parent_revision_id", "1370717872"),
        ),
        directory=THATCHER_DIRECTORY,
    )
    later = _record(
        record_id="page-19831-r1370984131",
        kind="wikipedia_revision",
        occurred_at="2026-08-24T01:17:25Z",
        source_file="wikipedia-19831-r1370984131.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1370984131",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1370984131"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1370984131"),
            ("page_id", "19831"),
            ("title", "Margaret Thatcher"),
            ("parent_revision_id", "1370718369"),
        ),
        directory=THATCHER_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q7416-r2533606292",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-20T08:12:54Z",
        source_file="wikidata-Q7416-r2533606292.json",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7416"
        ),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q7416"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2533606292"),
            ("entity_id", "Q7416"),
        ),
        directory=THATCHER_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:thatcher000000000000000001",
        component_digest="4" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def thatcher_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        15,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_thatcher_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_thatcher_queries_stage_real_body_programs(thatcher_world) -> None:
    world, artifacts, queries = thatcher_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert [len(query.essential_event_ids) for query in queries] == [4, 5, 7]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1925-10-13"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_THATCHER_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q7416" in answers[1]
    assert f"POP:{WIKI_THATCHER_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_thatcher_cf_remove_one_and_surface_gates(thatcher_world) -> None:
    world, artifacts, queries = thatcher_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1926-10-13")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{Birth date|df=y|1925|10|13}}",
            "{{Birth date|df=y|0000|10|13}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _newton_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-14627-r1371266913",
        kind="wikipedia_revision",
        occurred_at="2026-08-25T15:06:25Z",
        source_file="wikipedia-14627-r1371266913.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1371266913",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1371266913"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1371266913"),
            ("page_id", "14627"),
            ("title", "Isaac Newton"),
            ("parent_revision_id", "1370576188"),
        ),
        directory=NEWTON_DIRECTORY,
    )
    later = _record(
        record_id="page-14627-r1371274988",
        kind="wikipedia_revision",
        occurred_at="2026-08-25T16:00:47Z",
        source_file="wikipedia-14627-r1371274988.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1371274988",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1371274988"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1371274988"),
            ("page_id", "14627"),
            ("title", "Isaac Newton"),
            ("parent_revision_id", "1371266913"),
        ),
        directory=NEWTON_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q935-r2533455490",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-19T22:47:38Z",
        source_file="wikidata-Q935-r2533455490.json",
        source_url=("https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q935"),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q935"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2533455490"),
            ("entity_id", "Q935"),
        ),
        directory=NEWTON_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:newton000000000000000000001",
        component_digest="5" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def newton_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        16,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_newton_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_newton_queries_stage_real_body_programs(newton_world) -> None:
    world, artifacts, queries = newton_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert [len(query.essential_event_ids) for query in queries] == [4, 5, 7]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1643-01-04"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_NEWTON_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q935" in answers[1]
    assert f"POP:{WIKI_NEWTON_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_newton_cf_remove_one_and_surface_gates(newton_world) -> None:
    world, artifacts, queries = newton_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1644-01-04")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{Birth date|df=y|1643|01|04}}",
            "{{Birth date|df=y|0000|01|04}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _obama_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-534366-r1371415489",
        kind="wikipedia_revision",
        occurred_at="2026-08-26T08:22:32Z",
        source_file="wikipedia-534366-r1371415489.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1371415489",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1371415489"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1371415489"),
            ("page_id", "534366"),
            ("title", "Barack Obama"),
            ("parent_revision_id", "1370825066"),
        ),
        directory=OBAMA_DIRECTORY,
    )
    later = _record(
        record_id="page-534366-r1371416342",
        kind="wikipedia_revision",
        occurred_at="2026-08-26T08:32:40Z",
        source_file="wikipedia-534366-r1371416342.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1371416342",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1371416342"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1371416342"),
            ("page_id", "534366"),
            ("title", "Barack Obama"),
            ("parent_revision_id", "1371415489"),
        ),
        directory=OBAMA_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q76-r2536275829",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-25T16:24:42Z",
        source_file="wikidata-Q76-r2536275829.json",
        source_url=("https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q76"),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q76"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2536275829"),
            ("entity_id", "Q76"),
        ),
        directory=OBAMA_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:obama00000000000000000000001",
        component_digest="6" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def obama_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        17,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_obama_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_obama_queries_stage_real_body_programs(obama_world) -> None:
    world, artifacts, queries = obama_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1961-08-04"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_OBAMA_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q76" in answers[1]
    assert f"POP:{WIKI_OBAMA_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_obama_cf_remove_one_and_surface_gates(obama_world) -> None:
    world, artifacts, queries = obama_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1962-08-04")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{birth date and age|1961|8|4}}",
            "{{birth date and age|0000|8|4}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _elizabeth_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-12153654-r1371411139",
        kind="wikipedia_revision",
        occurred_at="2026-08-26T07:32:12Z",
        source_file="wikipedia-12153654-r1371411139.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1371411139",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1371411139"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1371411139"),
            ("page_id", "12153654"),
            ("title", "Elizabeth II"),
            ("parent_revision_id", "1370623496"),
        ),
        directory=ELIZABETH_DIRECTORY,
    )
    later = _record(
        record_id="page-12153654-r1371412757",
        kind="wikipedia_revision",
        occurred_at="2026-08-26T07:49:25Z",
        source_file="wikipedia-12153654-r1371412757.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1371412757",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1371412757"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1371412757"),
            ("page_id", "12153654"),
            ("title", "Elizabeth II"),
            ("parent_revision_id", "1371411139"),
        ),
        directory=ELIZABETH_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q9682-r2533011239",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-19T03:23:13Z",
        source_file="wikidata-Q9682-r2533011239.json",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q9682"
        ),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q9682"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2533011239"),
            ("entity_id", "Q9682"),
        ),
        directory=ELIZABETH_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:eliz00000000000000000000001",
        component_digest="7" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def elizabeth_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        19,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_elizabeth_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_elizabeth_queries_stage_real_body_programs(elizabeth_world) -> None:
    world, artifacts, queries = elizabeth_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1926-04-21"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_ELIZABETH_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q9682" in answers[1]
    assert f"POP:{WIKI_ELIZABETH_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_elizabeth_cf_remove_one_and_surface_gates(elizabeth_world) -> None:
    world, artifacts, queries = elizabeth_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1927-04-21")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{Birth date|df=yes|1926|04|21}}",
            "{{Birth date|df=yes|0000|04|21}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _mlk_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-20076-r1370648893",
        kind="wikipedia_revision",
        occurred_at="2026-08-22T07:56:19Z",
        source_file="wikipedia-20076-r1370648893.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1370648893",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1370648893"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1370648893"),
            ("page_id", "20076"),
            ("title", "Martin Luther King Jr."),
            ("parent_revision_id", "1370648761"),
        ),
        directory=MLK_DIRECTORY,
    )
    later = _record(
        record_id="page-20076-r1370915588",
        kind="wikipedia_revision",
        occurred_at="2026-08-23T18:11:16Z",
        source_file="wikipedia-20076-r1370915588.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1370915588",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1370915588"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1370915588"),
            ("page_id", "20076"),
            ("title", "Martin Luther King Jr."),
            ("parent_revision_id", "1370648893"),
        ),
        directory=MLK_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q8027-r2535959791",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-24T22:20:38Z",
        source_file="wikidata-Q8027-r2535959791.json",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q8027"
        ),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q8027"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2535959791"),
            ("entity_id", "Q8027"),
        ),
        directory=MLK_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:mlk0000000000000000000000001",
        component_digest="8" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def mlk_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        21,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_mlk_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_mlk_queries_stage_real_body_programs(mlk_world) -> None:
    world, artifacts, queries = mlk_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1929-01-15"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_MLK_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q8027" in answers[1]
    assert f"POP:{WIKI_MLK_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_mlk_cf_remove_one_and_surface_gates(mlk_world) -> None:
    world, artifacts, queries = mlk_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1930-01-15")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{birth date|1929|1|15}}",
            "{{birth date|0000|1|15}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def _jefferson_workflow() -> SourceWorkflow:
    old = _record(
        record_id="page-29922-r1369058917",
        kind="wikipedia_revision",
        occurred_at="2026-08-12T17:03:47Z",
        source_file="wikipedia-29922-r1369058917.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1369058917",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1369058917"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1369058917"),
            ("page_id", "29922"),
            ("title", "Thomas Jefferson"),
            ("parent_revision_id", "1368339705"),
        ),
        directory=JEFFERSON_DIRECTORY,
    )
    later = _record(
        record_id="page-29922-r1369059101",
        kind="wikipedia_revision",
        occurred_at="2026-08-12T17:05:15Z",
        source_file="wikipedia-29922-r1369059101.json",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1369059101",
        retrieval_url=(
            "https://en.wikipedia.org/w/api.php?action=query&prop=revisions"
            "%7Cpageprops&revids=1369059101"
        ),
        attributes=(
            ("kind", "wikipedia_revision"),
            ("revision_id", "1369059101"),
            ("page_id", "29922"),
            ("title", "Thomas Jefferson"),
            ("parent_revision_id", "1369058917"),
        ),
        directory=JEFFERSON_DIRECTORY,
    )
    entity = _record(
        record_id="entity-Q11812-r2533326601",
        kind="wikidata_entity_revision",
        occurred_at="2026-08-19T17:38:42Z",
        source_file="wikidata-Q11812-r2533326601.json",
        source_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q11812"
        ),
        retrieval_url=(
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q11812"
        ),
        attributes=(
            ("kind", "wikidata_entity_revision"),
            ("revision_id", "2533326601"),
            ("entity_id", "Q11812"),
        ),
        directory=JEFFERSON_DIRECTORY,
    )
    return SourceWorkflow(
        workflow_id="source:wikimedia:jefferson0000000000000000000001",
        component_digest="9" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(old.provenance_id, later.provenance_id, entity.provenance_id),
        records=(old, later, entity),
        relations=(),
    )


@pytest.fixture(scope="module")
def jefferson_world(monkeypatch_module: pytest.MonkeyPatch):
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    materialized = materialize(
        21,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_jefferson_workflow()],
        include_program_joins=False,
    )
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ]
    return materialized.worlds["focal"], materialized.artifacts["focal"], queries


def test_jefferson_queries_stage_real_body_programs(jefferson_world) -> None:
    world, artifacts, queries = jefferson_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert [graph_stats(world, query)["proof_depth"] for query in queries] == [4, 5, 6]
    answers = [query.answer for query in queries]
    assert answers[0] == "BORN:1743-04-13"
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert f"COMM:{WIKI_JEFFERSON_MID_QUOTE}" in answers[1]
    assert "ENTITY:Q11812" in answers[1]
    assert f"POP:{WIKI_JEFFERSON_LATE_QUOTE}" in answers[2]
    rest_ids = [
        artifact.artifact_id
        for artifact in artifacts
        if "wiki_section_appendix_rest" in artifact.artifact_id
    ]
    assert rest_ids
    assert rest_ids[0] not in queries[2].essential_artifact_ids
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    hybrid_counts = []
    for query, expected_tokens in zip(queries, (12_000, 20_000, 40_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "wiki_source_section"
        )
        assert section_tokens >= expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        generated_edges = real_source_relation_edges(world, query, artifacts)
        hybrid_counts.append(len(generated_edges))
        assert (
            generated_edges
            == _replayed_source_metadata(world, query, artifacts)["hybrid_causal_edges"]
        )
    assert hybrid_counts == [3, 6, 8]


def test_jefferson_cf_remove_one_and_surface_gates(jefferson_world) -> None:
    world, artifacts, queries = jefferson_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("BORN:1744-04-13")
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    early = next(
        item
        for item in essential
        if (item.slots or {}).get("event_type") == "wiki_source_section"
    )
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        early,
        text=early.text.replace(
            "{{birth date|1743|4|13}}",
            "{{birth date|0000|4|13}}",
            1,
        ),
    )
    assert corrupted.text != early.text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False


def test_ineligible_wikipedia_fixture_emits_no_claim_queries(
    monkeypatch_module: pytest.MonkeyPatch,
) -> None:
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "researchlab-wiki-source-test-key-32b"
    )
    text = "Ada Lovelace was a mathematician."
    digest = hashlib.sha256(text.encode()).hexdigest()

    def stub(
        *,
        record_id: str,
        kind: str,
        occurred_at: str,
        extra: tuple[tuple[str, str], ...],
    ) -> SourceRecord:
        return SourceRecord(
            record_id=record_id,
            kind=kind,
            occurred_at=occurred_at,
            text=text,
            source_url="https://en.wikipedia.org/w/index.php?oldid=1",
            retrieval_url="https://en.wikipedia.org/w/api.php",
            source_family=kind,
            source_origin=SourceOrigin.REAL_PUBLIC,
            provenance_id=f"sha256:{digest}",
            source_sha256=digest,
            text_sha256=digest,
            facts=(),
            attributes=extra,
        )

    workflow = SourceWorkflow(
        workflow_id="source:wikimedia:fixture0000000000000000",
        component_digest="1" * 64,
        source_kind="wikimedia",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("wikidata_entity_revision", "wikipedia_revision"),
        provenance_ids=(f"sha256:{digest}",),
        records=(
            stub(
                record_id="page-1-r1",
                kind="wikipedia_revision",
                occurred_at="2026-01-01T00:00:00Z",
                extra=(
                    ("kind", "wikipedia_revision"),
                    ("revision_id", "1"),
                    ("page_id", "1"),
                    ("title", "Ada Lovelace"),
                    ("parent_revision_id", "0"),
                ),
            ),
            stub(
                record_id="page-1-r2",
                kind="wikipedia_revision",
                occurred_at="2026-02-01T00:00:00Z",
                extra=(
                    ("kind", "wikipedia_revision"),
                    ("revision_id", "2"),
                    ("page_id", "1"),
                    ("title", "Ada Lovelace"),
                    ("parent_revision_id", "1"),
                ),
            ),
            stub(
                record_id="entity-Q1-r1",
                kind="wikidata_entity_revision",
                occurred_at="2026-01-15T00:00:00Z",
                extra=(
                    ("kind", "wikidata_entity_revision"),
                    ("revision_id", "9"),
                    ("entity_id", "Q1"),
                ),
            ),
        ),
        relations=(),
    )
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[workflow],
        include_program_joins=False,
    )
    assert [
        query
        for query in materialized.queries
        if query.query_type == "wiki_claim_reconstruction"
    ] == []
