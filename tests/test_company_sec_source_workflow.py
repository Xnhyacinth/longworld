from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from longworld.core.engine import answer_from_artifacts
from longworld.core.promotion import _replayed_source_metadata
from longworld.core.render import render_world
from longworld.core.sampler import materialize
from longworld.core.sourceworkflow import SourceFact, SourceRecord, SourceWorkflow
from longworld.core.taxonomy import SourceOrigin, artifact_classification
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.company.queries import build_queries
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company
from scripts.generate import context_source_relation_count, real_source_relation_edges


def _workflow() -> SourceWorkflow:
    text = (
        "<SEC-DOCUMENT>0000320193-25-000079.txt : 20251031\n"
        "<SEC-HEADER>0000320193-25-000079.hdr.sgml : 20251031\n"
        "<ACCEPTANCE-DATETIME>20251031060126\n"
        "ACCESSION NUMBER:\t\t0000320193-25-000079\n"
        "CONFORMED SUBMISSION TYPE:\t10-K\n"
        "PUBLIC DOCUMENT COUNT:\t\t91\n"
        "CONFORMED PERIOD OF REPORT:\t20250927\n"
        "FILED AS OF DATE:\t\t20251031\n"
        "DATE AS OF CHANGE:\t\t20251031\n"
        "FILING VALUES:\n"
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    raw_facts = (
        ("accession", "accession", "0000320193-25-000079"),
        ("form", "form", "10-K"),
        ("filing_date", "filing_date", "20251031"),
        ("report_date", "report_date", "20250927"),
    )
    facts = []
    for fact_id, field, value in raw_facts:
        start = text.index(value)
        facts.append(
            SourceFact(
                fact_id=fact_id,
                field=field,
                value=value,
                record_id="sec:0000320193-25-000079",
                evidence_quote=value,
                char_start=start,
                char_end=start + len(value),
                value_offset=0,
                source_sha256=digest,
            )
        )
    record = SourceRecord(
        record_id="sec:0000320193-25-000079",
        kind="sec_filing",
        occurred_at="2025-10-31",
        text=text,
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/0000320193-25-000079.txt"
        ),
        retrieval_url="",
        source_family="sec_edgar_submission",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=tuple(facts),
        attributes=(
            ("accession", "0000320193-25-000079"),
            ("cik", "0000320193"),
            ("filing_date", "2025-10-31"),
            ("form", "10-K"),
            ("report_date", "2025-09-27"),
        ),
    )
    return SourceWorkflow(
        workflow_id="source:sec_filing:0123456789abcdef01234567",
        component_digest="0" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=(record.provenance_id,),
        records=(record,),
        relations=(),
    )


def _materialized(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-source-test-key-material-32-bytes"
    )
    spec = sample_world_spec(
        7,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[_workflow()],
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    query = next(
        query
        for query in build_queries(world)
        if query.query_type == "sec_filing_eligibility"
    )
    return world, artifacts, query


def test_shared_sampler_routes_sec_source_workflow_into_company() -> None:
    result = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[_workflow()],
        include_program_joins=False,
    )

    query = next(
        item for item in result.queries if item.query_type == "sec_filing_eligibility"
    )
    assert result.scan_ok, result.scan_issues
    assert query.answer == "APPROVED | 2025-09-27 | 0000320193-25-000079"
    assert not any(
        item.query_type == "sec_financial_reconstruction" for item in result.queries
    )


def test_real_sec_history_grows_across_length_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-source-test-key-material-32-bytes"
    )
    spec = sample_world_spec(
        7,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[_workflow()],
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    queries = [
        query
        for query in build_queries(world)
        if query.query_type == "sec_filing_eligibility"
    ]

    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert [len(query.essential_event_ids) for query in queries] == [4, 5, 6]
    assert [query.proof_depth for query in queries] == [4, 5, 6]
    assert len({query.base_task_group for query in queries}) == 1
    assert len({query.answer for query in queries}) == 1
    assert len({query.cf_answer for query in queries}) == 1
    assert all("Workflow:" not in query.question for query in queries)
    assert all("Control tier:" not in query.question for query in queries)
    assert all(
        "Control tier:" not in artifact.text
        for artifact in artifacts
        if artifact.doc_type == "sec_filing_publication_ratification"
    )
    for query in queries:
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )

    relation_counts = []
    replayed_relation_counts = []
    authentic_relation_counts = []
    hybrid_relation_counts = []
    base_task_ids = []
    for query in queries:
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        relation_counts.append(
            context_source_relation_count(world, artifacts, spec=query)
        )
        metadata = _replayed_source_metadata(world, query, essential)
        replayed_relation_counts.append(metadata["context_source_relation_count"])
        authentic_relation_counts.append(
            len(metadata["authentic_source_relation_edges"])
        )
        hybrid_relation_counts.append(len(metadata["hybrid_causal_edges"]))
        base_task_ids.append(metadata["base_task_id"])
    assert relation_counts == [3, 4, 5]
    assert replayed_relation_counts == [3, 4, 5]
    assert authentic_relation_counts == [0, 0, 0]
    assert hybrid_relation_counts == [3, 4, 5]
    assert len(set(base_task_ids)) == 1
    for query in queries:
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        edges = _replayed_source_metadata(world, query, essential)[
            "hybrid_causal_edges"
        ]
        generated_edges = real_source_relation_edges(world, query, essential)
        assert generated_edges == edges
        assert all(
            edge["parent_record_id"] != edge["child_record_id"] for edge in edges
        )
        assert all(
            edge["source_record_id"] == "sec:0000320193-25-000079" for edge in edges
        )


def test_real_sec_body_drives_state_answer_and_counterfactual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, artifacts, query = _materialized(monkeypatch)
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert len(essential) == 4
    assert query.answer == "APPROVED | 2025-09-27 | 0000320193-25-000079"
    assert query.cf_answer == "BLOCKED | 2025-09-27 | 0000320193-25-000079"
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=True)
        == query.answer
    )
    assert all(
        answer_from_artifacts(
            world,
            query,
            [item for item in essential if item.artifact_id != removed.artifact_id],
            enforce_preconditions=True,
        )
        == "unknown"
        for removed in essential
    )
    assert all(query.answer not in artifact.text for artifact in essential)

    _, cf_artifacts = render_cf_view(world, query)
    verification, notes = verify_question(
        world,
        query,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.counterfactual_replay_sufficient
    assert verification.remove_one_fails
    assert verification.essential_single_doc_insufficient
    assert verification.essential_surface_gold_free
    assert all(
        check["replay_answer"] != query.answer
        for check in notes["essential_single_docs"]
    )

    source_artifact = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("real_workflow_record")
    )
    classification = artifact_classification(source_artifact)
    assert classification.source_origin == SourceOrigin.REAL_PUBLIC
    assert (source_artifact.slots or {}).get("source_record_id") == (
        "sec:0000320193-25-000079"
    )

    cf_essential = [
        artifact
        for artifact in cf_artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    assert (
        answer_from_artifacts(
            world,
            query,
            cf_essential,
            extra_overrides={query.cf_event_id: query.cf_param_updates},
            enforce_preconditions=True,
        )
        == query.cf_answer
    )


def test_real_sec_filing_window_depends_on_report_date_from_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, artifacts, query = _materialized(monkeypatch)
    source = next(
        artifact for artifact in artifacts if artifact.doc_type == "sec_filing"
    )
    late_text = str(source.slots["params"]["text"]).replace("20251031", "20251231")
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    source_event_id = source.reveals_events[0]
    fact_spans = [
        {
            **fact,
            "evidence_quote": str(fact["evidence_quote"]).replace(
                "20251031", "20251231"
            ),
        }
        for fact in source.slots["params"]["fact_spans"]
    ]

    assert (
        answer_from_artifacts(
            world,
            query,
            essential,
            extra_overrides={
                source_event_id: {
                    "text": late_text,
                    "text_sha256": hashlib.sha256(late_text.encode()).hexdigest(),
                    "fact_spans": fact_spans,
                }
            },
            enforce_preconditions=True,
        )
        == "BLOCKED | 2025-09-27 | 0000320193-25-000079"
    )


def test_real_sec_corrupted_body_fails_semantic_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, artifacts, query = _materialized(monkeypatch)
    corrupted = [
        replace(artifact, text="Corrupted SEC filing body with no filing facts.")
        if (artifact.slots or {}).get("real_workflow_record")
        else artifact
        for artifact in artifacts
    ]

    verification, _ = verify_question(
        world,
        query,
        corrupted,
        verification_mode="candidate",
    )

    assert not verification.essential_text_grounded
    assert not verification.semantic_sufficient


def test_large_sec_submission_uses_a_bounded_attested_evidence_corridor() -> None:
    workflow = _workflow()
    original = workflow.records[0]
    text = original.text + ("AUTHENTIC FILING SECTION\n" * 100_000)
    digest = hashlib.sha256(text.encode()).hexdigest()
    record = replace(
        original,
        text=text,
        source_sha256=digest,
        text_sha256=digest,
        provenance_id=f"sha256:{digest}",
        facts=tuple(replace(fact, source_sha256=digest) for fact in original.facts),
    )
    workflow = replace(
        workflow,
        provenance_ids=(record.provenance_id,),
        records=(record,),
    )

    result = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    source = next(
        artifact
        for artifact in result.artifacts["focal"]
        if (artifact.slots or {}).get("real_workflow_record")
    )
    query = next(
        item for item in result.queries if item.query_type == "sec_filing_eligibility"
    )

    assert len(source.text) <= 33_000
    assert (source.slots or {})["parent_provenance_id"] == record.provenance_id
    assert artifact_classification(source).source_origin is SourceOrigin.REAL_DERIVED
    assert query.answer == "APPROVED | 2025-09-27 | 0000320193-25-000079"
