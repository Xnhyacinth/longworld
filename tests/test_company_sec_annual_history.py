from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from longworld.core.engine import answer_from_artifacts, semantic_answer_from_artifacts
from longworld.core.filingworkflow import (
    _audit_exported_manifest,
    _filing_relations,
    validated_sec_annual_endpoints,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.sampler import materialize
from longworld.core.sourceworkflow import (
    SOURCE_WORKFLOW_ADAPTER_REVISION_V2,
    SourceEvidence,
    SourceFact,
    SourceRecord,
    SourceRelation,
    SourceWorkflow,
    adapt_sec_manifest,
)
from longworld.core.taxonomy import SourceOrigin
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.core.world import Event
from longworld.domains.company import simulate as company_simulate

HISTORY_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "data/source_inventory/p12_wave1_company_microsoft_gcs_history_v1/"
    "sec_filing_manifest.p12.wave1.signed.json"
)
ARTIFACT_KEY = "annual-history-artifact-test-key-material-32-bytes"


def _filing(
    *,
    accession: str,
    cik: str = "0000789019",
    form: str = "10-K",
    filing_date: str,
    report_date: str,
) -> dict:
    record_id = f"sec:{accession}"
    return {
        "record_id": record_id,
        "accession": accession,
        "cik": cik,
        "form": form,
        "filing_date": filing_date,
        "report_date": report_date,
        "derived_facts": [
            {"fact_id": "cik", "field": "cik", "value": cik},
            {"fact_id": "form", "field": "form", "value": form},
            {
                "fact_id": "filing_date",
                "field": "filing_date",
                "value": filing_date,
            },
            {
                "fact_id": "report_date",
                "field": "report_date",
                "value": report_date,
            },
        ],
    }


def test_filing_relations_derives_only_adjacent_same_issuer_annual_history() -> None:
    fy2023 = _filing(
        accession="0000950170-23-035122",
        filing_date="2023-07-27",
        report_date="2023-06-30",
    )
    fy2024 = _filing(
        accession="0000950170-24-087843",
        filing_date="2024-07-30",
        report_date="2024-06-30",
    )
    fy2025 = _filing(
        accession="0000950170-25-100235",
        filing_date="2025-07-30",
        report_date="2025-06-30",
    )
    other_issuer = _filing(
        accession="0001018724-25-000004",
        cik="0001018724",
        filing_date="2025-02-07",
        report_date="2024-12-31",
    )

    relations = _filing_relations([fy2025, other_issuer, fy2023, fy2024])

    assert [relation["relation_type"] for relation in relations] == [
        "prior_annual_filing",
        "prior_annual_filing",
    ]
    assert {
        (relation["from_record_id"], relation["to_record_id"]) for relation in relations
    } == {
        (fy2024["record_id"], fy2023["record_id"]),
        (fy2025["record_id"], fy2024["record_id"]),
    }
    assert all(
        relation["evidence"]
        == [
            {
                "record_id": relation["from_record_id"],
                "fact_ids": ["cik", "form", "filing_date", "report_date"],
            },
            {
                "record_id": relation["to_record_id"],
                "fact_ids": ["cik", "form", "filing_date", "report_date"],
            },
        ]
        for relation in relations
    )


def test_filing_relations_rejects_ungrounded_or_reversed_annual_history() -> None:
    prior = _filing(
        accession="0000950170-23-035122",
        filing_date="2023-07-27",
        report_date="2023-06-30",
    )
    current = _filing(
        accession="0000950170-24-087843",
        filing_date="2024-07-30",
        report_date="2024-06-30",
    )

    tampered = json.loads(json.dumps(current))
    next(fact for fact in tampered["derived_facts"] if fact["field"] == "report_date")[
        "value"
    ] = "2024-06-29"
    assert _filing_relations([prior, tampered]) == []

    reversed_filing_date = json.loads(json.dumps(current))
    reversed_filing_date["filing_date"] = "2023-07-01"
    next(
        fact
        for fact in reversed_filing_date["derived_facts"]
        if fact["field"] == "filing_date"
    )["value"] = "2023-07-01"
    assert _filing_relations([prior, reversed_filing_date]) == []


def _source_record(
    *,
    accession: str,
    filing_date: str,
    report_date: str,
    revenue: str,
    cik: str = "0000789019",
) -> SourceRecord:
    text = (
        "SEC FILING RECORD\n"
        f"ACCESSION NUMBER: {accession}\n"
        f"CENTRAL INDEX KEY: {cik}\n"
        "CONFORMED SUBMISSION TYPE: 10-K\n"
        f"FILED AS OF DATE: {filing_date}\n"
        f"CONFORMED PERIOD OF REPORT: {report_date}\n"
        f"Revenue: {revenue}\n"
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    record_id = f"sec:{accession}"
    facts = []
    for fact_id, field, value in (
        ("accession", "accession", accession),
        ("cik", "cik", cik),
        ("form", "form", "10-K"),
        ("filing_date", "filing_date", filing_date),
        ("report_date", "report_date", report_date),
        ("revenue", "revenue", revenue),
    ):
        start = text.index(value)
        facts.append(
            SourceFact(
                fact_id=fact_id,
                field=field,
                value=value,
                record_id=record_id,
                evidence_quote=value,
                char_start=start,
                char_end=start + len(value),
                value_offset=0,
                source_sha256=digest,
            )
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
        facts=tuple(facts),
        attributes=(
            ("accession", accession),
            ("cik", cik),
            ("filing_date", filing_date),
            ("form", "10-K"),
            ("report_date", report_date),
        ),
    )


def _annual_workflow() -> tuple[SourceWorkflow, SourceRelation]:
    prior = _source_record(
        accession="0000950170-23-035122",
        filing_date="2023-07-27",
        report_date="2023-06-30",
        revenue="211915",
    )
    current = _source_record(
        accession="0000950170-24-087843",
        filing_date="2024-07-30",
        report_date="2024-06-30",
        revenue="245122",
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
        for record in (current, prior)
        for fact in record.facts
        if fact.field in {"cik", "form", "filing_date", "report_date"}
    )
    relation = SourceRelation(
        relation_id=f"{current.record_id}:prior_annual:{prior.record_id}",
        kind="prior_annual_filing",
        source_record_id=current.record_id,
        target_record_id=prior.record_id,
        evidence=evidence,
    )
    workflow = SourceWorkflow(
        workflow_id="source:sec_filing:annualhistory0123456789",
        component_digest="1" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=(prior.provenance_id, current.provenance_id),
        records=(prior, current),
        relations=(relation,),
    )
    return workflow, relation


def _contract_financial_events(
    *,
    workflow: SourceWorkflow,
    record: SourceRecord,
    prefix: str,
    workflow_index: int,
    record_index: int,
    filing_day: date,
) -> list[Event]:
    """Test-only parser-boundary fixture; it is not an exportable source inventory."""
    [revenue] = [fact for fact in record.facts if fact.field == "revenue"]
    visible = (
        "Annual consolidated total revenue reported in source facts: "
        f"{revenue.evidence_quote}."
    )
    text_prefix = "SEC source section\n"
    text = text_prefix + visible
    section_id = "contract_total_revenue"
    event_id = f"{prefix}.sec_section_{section_id}_{workflow_index}_{record_index}"
    event = Event(
        id=event_id,
        type="sec_source_section",
        time=filing_day,
        params={
            "workflow_id": workflow.workflow_id,
            "record_id": record.record_id,
            "section_id": section_id,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "section_sha256": hashlib.sha256(visible.encode()).hexdigest(),
            "source_sha256": record.source_sha256,
            "source_origin": "real_derived",
            "source_family": record.source_family,
            "source_url": record.source_url,
            "retrieval_url": record.retrieval_url,
            "provenance_id": f"derived-sha256:{hashlib.sha256(text.encode()).hexdigest()}",
            "parent_provenance_id": record.provenance_id,
            "provenance_operation": "test_contract_fact_projection",
            "fact_spans": [
                {
                    "role": "total_revenue",
                    "numeric_value": int(revenue.value),
                    "evidence_quote": revenue.evidence_quote,
                    "char_start": len(text_prefix)
                    + visible.index(revenue.evidence_quote),
                    "char_end": len(text_prefix)
                    + visible.index(revenue.evidence_quote)
                    + len(revenue.evidence_quote),
                    "scale": 0,
                    "sign": "",
                    "kind": "xbrl",
                }
            ],
            "ground_values": [revenue.evidence_quote],
        },
        visibility=[event_id],
    )
    cache_key = company_simulate._sec_section_cache_key(workflow, record)
    assert cache_key is not None
    company_simulate._SEC_CANONICAL_SECTION_CACHE[cache_key] = {
        section_id: {
            "type": event.type,
            "time": event.time,
            "params": deepcopy(event.params),
            "preconditions": [],
            "causal_inputs": [],
            "required_inputs": [],
            "relation_kinds": {},
            "skipped": False,
            "skip_reason": None,
        }
    }
    return [event]


def test_validated_annual_endpoints_require_exact_grounded_adjacent_records() -> None:
    workflow, relation = _annual_workflow()

    endpoints = validated_sec_annual_endpoints(workflow, relation)

    assert endpoints is not None
    current, prior = endpoints
    assert current.attribute("report_date") == "2024-06-30"
    assert prior.attribute("report_date") == "2023-06-30"

    middle = _source_record(
        accession="0000950170-24-000001",
        filing_date="2024-01-30",
        report_date="2023-12-31",
        revenue="220000",
    )
    nonadjacent = replace(
        workflow,
        records=(*workflow.records, middle),
        provenance_ids=(*workflow.provenance_ids, middle.provenance_id),
    )
    assert validated_sec_annual_endpoints(nonadjacent, relation) is None

    reversed_relation = replace(
        relation,
        relation_id=f"{prior.record_id}:prior_annual:{current.record_id}",
        source_record_id=prior.record_id,
        target_record_id=current.record_id,
    )
    assert validated_sec_annual_endpoints(workflow, reversed_relation) is None

    wrong_cik_prior = replace(
        prior,
        attributes=tuple(
            (key, "0001018724" if key == "cik" else value)
            for key, value in prior.attributes
        ),
    )
    cross_issuer = replace(workflow, records=(wrong_cik_prior, current))
    assert validated_sec_annual_endpoints(cross_issuer, relation) is None


def test_annual_contract_materializes_and_replays_without_local_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)
    monkeypatch.setattr(
        company_simulate, "_sec_financial_events", _contract_financial_events
    )
    workflow, _ = _annual_workflow()

    materialized = materialize(
        1242,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    artifacts = materialized.artifacts["focal"]
    [query] = [
        item
        for item in materialized.queries
        if item.query_type == "sec_annual_revenue_change"
    ]
    relation_event = next(
        event
        for event in world.events
        if event.type == "sec_prior_annual_filing_relation"
    )
    assert relation_event.params["relation_provenance"] == "authentic_source"
    assert (
        relation_event.params["adjacency_proof"]
        == "manifest_adapter_canonical_recomputation"
    )
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert query.answer == (
        "ANNUAL_REVENUE_CHANGE | 2024-06-30 | 245122 | 2023-06-30 | 211915 | 33207"
    )
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=True)
        == query.answer
    )
    _, cf_artifacts = render_cf_view(world, query)
    verification, notes = verify_question(
        world,
        query,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.strict_executable_sufficient, notes
    assert verification.semantic_sufficient, notes["essential_text_issues"]
    assert verification.counterfactual_replay_sufficient, notes
    assert query.cf_answer != query.answer
    assert verification.remove_one_fails, notes
    assert verification.essential_single_doc_insufficient, notes

    current_section = next(
        artifact
        for artifact in essential
        if artifact.doc_type == "sec_source_section"
        and artifact.slots["params"]["record_id"].endswith("24-087843")
    )
    revenue_quote = current_section.slots["params"]["fact_spans"][0]["evidence_quote"]
    body_corrupted = [
        replace(
            artifact,
            text=artifact.text.replace(revenue_quote, f"{revenue_quote} CORRUPTED", 1),
        )
        if artifact.artifact_id == current_section.artifact_id
        else artifact
        for artifact in essential
    ]
    assert (
        semantic_answer_from_artifacts(
            world, query, body_corrupted, enforce_preconditions=True
        )
        == "unknown"
    )

    prior_source = next(
        artifact
        for artifact in essential
        if artifact.doc_type == "sec_filing"
        and artifact.slots["params"]["record_id"].endswith("23-035122")
    )
    mismatched_slots = deepcopy(prior_source.slots)
    cik_span = next(
        span
        for span in mismatched_slots["params"]["fact_spans"]
        if span["field"] == "cik"
    )
    prior_text = mismatched_slots["params"]["text"]
    mismatched_text = prior_text.replace(cik_span["evidence_quote"], "0001018724", 1)
    cik_span["evidence_quote"] = "0001018724"
    mismatched_slots["params"]["text"] = mismatched_text
    mismatched_slots["params"]["text_sha256"] = hashlib.sha256(
        mismatched_text.encode()
    ).hexdigest()
    mismatched_slots["params"]["ground_values"] = [
        "0001018724" if value == "0000789019" else value
        for value in mismatched_slots["params"]["ground_values"]
    ]
    assert (
        answer_from_artifacts(
            world,
            query,
            essential,
            extra_overrides={
                prior_source.reveals_events[0]: mismatched_slots["params"]
            },
            enforce_preconditions=True,
        )
        == "unknown"
    )


@pytest.mark.skipif(
    not HISTORY_MANIFEST.is_file(),
    reason="requires the local Microsoft FY2023/FY2024 source inventory",
)
def test_annual_manifest_audit_recomputes_relation_graph() -> None:
    manifest = json.loads(HISTORY_MANIFEST.read_text(encoding="utf-8"))
    manifest.pop("attestation")
    manifest["filing_relations"] = _filing_relations(manifest["filings"])

    _audit_exported_manifest(manifest, base_directory=HISTORY_MANIFEST.parent)

    tampered = json.loads(json.dumps(manifest))
    tampered["filing_relations"][0]["prior_report_date"] = "2023-06-29"
    with pytest.raises(ProvenanceError, match="filing relations are invalid"):
        _audit_exported_manifest(tampered, base_directory=HISTORY_MANIFEST.parent)


@pytest.mark.skipif(
    not HISTORY_MANIFEST.is_file(),
    reason="requires the local Microsoft FY2023/FY2024 source inventory",
)
def test_annual_revenue_answer_replays_both_filings_relation_and_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)
    manifest = json.loads(HISTORY_MANIFEST.read_text(encoding="utf-8"))
    manifest.pop("attestation")
    manifest["filing_relations"] = _filing_relations(manifest["filings"])
    [workflow] = adapt_sec_manifest(
        manifest,
        signed_bundle_authorized=True,
        adapter_revision=SOURCE_WORKFLOW_ADAPTER_REVISION_V2,
    )
    materialized = materialize(
        1241,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    artifacts = materialized.artifacts["focal"]
    [query] = [
        query
        for query in materialized.queries
        if query.query_type == "sec_annual_revenue_change"
    ]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]

    assert query.answer == (
        "ANNUAL_REVENUE_CHANGE | 2024-06-30 | 245122000000 | "
        "2023-06-30 | 211915000000 | 33207000000"
    )
    assert (
        answer_from_artifacts(world, query, essential, enforce_preconditions=True)
        == query.answer
    )

    _, cf_artifacts = render_cf_view(world, query)
    verification, notes = verify_question(
        world,
        query,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert query.cf_answer != query.answer
    assert verification.counterfactual_replay_sufficient, {
        key: notes[key]
        for key in ("cf_ans", "cf_replay_ans", "cf_missing_essential", "cf_text_issues")
    }
    assert verification.strict_executable_sufficient, notes
    assert verification.semantic_sufficient, notes
    assert verification.remove_one_fails
    assert verification.essential_single_doc_insufficient
    assert verification.essential_text_grounded, notes

    controls = [
        artifact
        for artifact in essential
        if artifact.doc_type
        in {"sec_prior_annual_filing_relation", "sec_annual_revenue_change"}
    ]
    assert len(controls) == 2
    assert all("245122000000" not in artifact.text for artifact in controls)
    assert all("211915000000" not in artifact.text for artifact in controls)

    current_section = next(
        artifact
        for artifact in essential
        if artifact.doc_type == "sec_source_section"
        and artifact.slots["params"]["record_id"].endswith("24-087843")
    )
    revenue_quote = next(
        span["evidence_quote"]
        for span in current_section.slots["params"]["fact_spans"]
        if span.get("role") == "total_revenue"
    )
    corrupted = [
        replace(
            artifact,
            text=artifact.text.replace(revenue_quote, f"{revenue_quote} CORRUPTED", 1),
        )
        if artifact.artifact_id == current_section.artifact_id
        else artifact
        for artifact in essential
    ]
    assert (
        semantic_answer_from_artifacts(
            world, query, corrupted, enforce_preconditions=True
        )
        == "unknown"
    )
