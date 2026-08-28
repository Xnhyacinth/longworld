from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from longworld.core.engine import answer_from_artifacts
from longworld.core.promotion import _replayed_source_metadata
from longworld.core.render import render_world
from longworld.core.sourceworkflow import (
    SourceEvidence,
    SourceFact,
    SourceRecord,
    SourceRelation,
    SourceWorkflow,
)
from longworld.core.taxonomy import SourceOrigin, artifact_classification
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.company.queries import build_queries
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company
from scripts.generate import _real_source_tokens, real_source_relation_edges


def _record(
    *, accession: str, form: str, filing_date: str, report_date: str
) -> SourceRecord:
    compact_filing_date = filing_date.replace("-", "")
    compact_report_date = report_date.replace("-", "")
    text = (
        f"ACCESSION NUMBER: {accession}\n"
        f"CONFORMED SUBMISSION TYPE: {form}\n"
        f"CONFORMED PERIOD OF REPORT: {compact_report_date}\n"
        f"FILED AS OF DATE: {compact_filing_date}\n"
        f"Distinct filing narrative for {accession}.\n"
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    record_id = f"sec:{accession}"
    raw_facts = (
        ("accession", "accession", accession),
        ("form", "form", form),
        ("filing_date", "filing_date", compact_filing_date),
        ("report_date", "report_date", compact_report_date),
    )
    facts = tuple(
        SourceFact(
            fact_id=fact_id,
            field=field,
            value=value,
            record_id=record_id,
            evidence_quote=value,
            char_start=text.index(value),
            char_end=text.index(value) + len(value),
            value_offset=0,
            source_sha256=digest,
        )
        for fact_id, field, value in raw_facts
    )
    return SourceRecord(
        record_id=record_id,
        kind="sec_filing",
        occurred_at=filing_date,
        text=text,
        source_url=f"https://www.sec.gov/Archives/{accession}.txt",
        retrieval_url="",
        source_family="sec_edgar_submission",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=facts,
        attributes=(
            ("accession", accession),
            ("cik", "0000320193"),
            ("filing_date", filing_date),
            ("form", form),
            ("report_date", report_date),
        ),
    )


def _workflow() -> SourceWorkflow:
    original = _record(
        accession="0000320193-25-000079",
        form="10-K",
        filing_date="2025-10-31",
        report_date="2025-09-27",
    )
    narrative_start = original.text.index("Distinct filing narrative")
    original = replace(
        original,
        facts=(
            *original.facts,
            SourceFact(
                fact_id="narrative_marker",
                field="revenue",
                value="Distinct",
                record_id=original.record_id,
                evidence_quote="Distinct filing narrative",
                char_start=narrative_start,
                char_end=narrative_start + len("Distinct filing narrative"),
                value_offset=0,
                source_sha256=original.source_sha256,
            ),
        ),
    )
    amendment = _record(
        accession="0000320193-25-000099",
        form="10-K/A",
        filing_date="2025-11-14",
        report_date="2025-09-27",
    )
    evidence = tuple(
        SourceEvidence(
            record_id=record.record_id,
            evidence_quote=fact.evidence_quote,
            char_start=fact.char_start,
            char_end=fact.char_end,
            source_sha256=fact.source_sha256,
            fact_ids=(fact.fact_id,),
        )
        for record in (original, amendment)
        for fact in record.facts
        if fact.field in {"form", "report_date"}
    )
    relation = SourceRelation(
        relation_id=f"{amendment.record_id}:amends:{original.record_id}",
        kind="amends_report",
        source_record_id=amendment.record_id,
        target_record_id=original.record_id,
        evidence=evidence,
    )
    return SourceWorkflow(
        workflow_id="source:sec_filing:multifiling012345678901",
        component_digest="1" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=(original.provenance_id, amendment.provenance_id),
        records=(original, amendment),
        relations=(relation,),
    )


def _world(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-multifiling-test-key-material"
    )
    spec = sample_world_spec(
        11,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[_workflow()],
    )
    return simulate_company(spec)["focal"]


def test_amendment_resolution_replays_two_distinct_filing_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    artifacts = render_world(world)
    query = next(
        item
        for item in build_queries(world)
        if item.query_type == "sec_amendment_resolution"
    )
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert query.answer == (
        "AMENDS | 0000320193-25-000099 | 0000320193-25-000079 | 2025-09-27"
    )
    assert query.cf_answer == (
        "INVALID_RELATION | 0000320193-25-000099 | 0000320193-25-000079 | 2025-09-27"
    )
    assert len(essential) == 3
    assert (
        sum(artifact.doc_type == "sec_amendment_resolution" for artifact in essential)
        == 1
    )
    assert (
        len(
            {
                artifact.slots["params"]["text_sha256"]
                for artifact in essential
                if (artifact.slots or {}).get("real_workflow_record")
            }
        )
        == 2
    )
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=True)
        == query.answer
    )
    replayed = _replayed_source_metadata(world, query, essential)
    assert len(replayed["authentic_source_relation_edges"]) == 1
    assert replayed["authentic_source_relation_edges"][0]["relation"] == "amends_report"
    assert (
        real_source_relation_edges(world, query, essential)
        == replayed["source_relation_edges"]
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

    _, cf_artifacts = render_cf_view(world, query)
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
    verification, _ = verify_question(
        world,
        query,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.counterfactual_replay_sufficient
    assert verification.remove_one_fails
    assert verification.essential_single_doc_insufficient
    assert verification.essential_text_grounded
    assert verification.semantic_sufficient

    cf_world, _ = render_cf_view(world, query)
    assert not any(
        edge["relation_provenance"] == "authentic_source"
        for edge in real_source_relation_edges(cf_world, query, cf_essential)
    )


def test_sec_filing_counterfactual_text_is_synthetic_with_parent_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    query = next(
        item
        for item in build_queries(world)
        if item.query_type == "sec_filing_eligibility"
    )

    _, cf_artifacts = render_cf_view(world, query)
    altered = next(
        artifact
        for artifact in cf_artifacts
        if query.cf_event_id in artifact.reveals_events
    )

    assert (
        artifact_classification(altered).source_origin is SourceOrigin.SYNTHETIC_WORLD
    )
    assert altered.slots["parent_provenance_id"]
    assert altered.slots["params"]["provenance_operation"] == "counterfactual_sec_form"
    assert _real_source_tokens([altered]) == 0


def test_amendment_relation_with_tampered_raw_span_is_not_materialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()
    relation = workflow.relations[0]
    tampered = replace(
        relation.evidence[0],
        char_start=relation.evidence[0].char_start + 1,
        char_end=relation.evidence[0].char_end + 1,
    )
    invalid = replace(
        workflow,
        relations=(replace(relation, evidence=(tampered, *relation.evidence[1:])),),
    )
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-multifiling-test-key-material"
    )
    spec = sample_world_spec(
        11,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[invalid],
    )
    world = simulate_company(spec)["focal"]

    assert not any(
        item.query_type == "sec_amendment_resolution" for item in build_queries(world)
    )


def test_amendment_authentic_edge_requires_both_selected_source_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    artifacts = render_world(world)
    query = next(
        item
        for item in build_queries(world)
        if item.query_type == "sec_amendment_resolution"
    )
    one_endpoint = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
        and (artifact.slots or {}).get("source_record_id") != "sec:0000320193-25-000079"
    ]

    assert not any(
        edge["relation_provenance"] == "authentic_source"
        for edge in real_source_relation_edges(world, query, one_endpoint)
    )


def test_amendment_authentic_edge_rejects_foreign_workflow_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    artifacts = render_world(world)
    query = next(
        item
        for item in build_queries(world)
        if item.query_type == "sec_amendment_resolution"
    )
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    original = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("source_record_id") == "sec:0000320193-25-000079"
    )
    foreign = replace(
        original,
        slots={**original.slots, "source_workflow_id": "source:sec_filing:foreign"},
    )
    selected = [foreign if artifact is original else artifact for artifact in essential]

    assert not any(
        edge["relation_provenance"] == "authentic_source"
        for edge in real_source_relation_edges(world, query, selected)
    )


def test_amendment_authentic_edge_ignores_forged_resolution_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    artifacts = render_world(world)
    query = next(
        item
        for item in build_queries(world)
        if item.query_type == "sec_amendment_resolution"
    )
    workflow = world.spec["project"]["source_workflows"][0]
    world.spec["project"]["source_workflows"] = [replace(workflow, relations=())]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert any(
        artifact.doc_type == "sec_amendment_resolution" for artifact in essential
    )
    assert not any(
        edge["relation_provenance"] == "authentic_source"
        for edge in real_source_relation_edges(world, query, essential)
    )


def test_duplicate_record_ids_across_sec_workflows_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()
    duplicate = replace(
        workflow,
        workflow_id="source:sec_filing:duplicate012345678901",
        component_digest="2" * 64,
    )
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-multifiling-test-key-material"
    )
    spec = sample_world_spec(
        11,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[workflow, duplicate],
    )
    world = simulate_company(spec)["focal"]

    assert not any(
        item.query_type == "sec_amendment_resolution" for item in build_queries(world)
    )
