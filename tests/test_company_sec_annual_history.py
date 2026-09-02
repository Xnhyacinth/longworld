from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from longworld.core.engine import answer_from_artifacts, semantic_answer_from_artifacts
from longworld.core.filingworkflow import (
    _audit_exported_manifest,
    _filing_relations,
    _sec_source_event_matches_record,
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
from longworld.core.taxonomy import SourceOrigin, artifact_classification
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


def test_financial_programs_bind_to_cumulative_four_filing_history() -> None:
    workflow_id = "source:sec_filing:fourannualhistory0123456789"
    record_ids = [f"sec:annual-{year}" for year in range(2022, 2026)]
    events = [
        Event(
            id=f"focal.sec_filing_0_{index}",
            type="sec_filing",
            time=date(year, 7, 30),
            params={"workflow_id": workflow_id, "record_id": record_id},
            visibility=[f"focal.sec_filing_0_{index}"],
        )
        for index, (year, record_id) in enumerate(zip(range(2022, 2026), record_ids))
    ]
    for index, (prior_id, current_id) in enumerate(pairwise(record_ids)):
        events.append(
            Event(
                id=f"focal.sec_prior_annual_0_{index}",
                type="sec_prior_annual_filing_relation",
                time=date(2023 + index, 8, 2),
                params={
                    "workflow_id": workflow_id,
                    "record_id": current_id,
                    "target_record_id": prior_id,
                    "source_relation_id": f"{current_id}:prior:{prior_id}",
                },
                visibility=[f"focal.sec_prior_annual_0_{index}"],
            )
        )
    for section_name in (
        "item1_business",
        "item2_properties",
        "item3_legal_proceedings",
        "item7a_market_risk",
        "note11_income_taxes",
    ):
        events.append(
            Event(
                id=f"focal.section.{record_ids[-2]}.{section_name}",
                type="sec_source_section",
                time=date(2024, 7, 30),
                params={
                    "workflow_id": workflow_id,
                    "record_id": record_ids[-2],
                    "section_id": section_name,
                },
                visibility=[f"focal.section.{record_ids[-2]}.{section_name}"],
            )
        )
    answers: list[Event] = []
    for record_id in record_ids:
        for tier in ("16k", "32k", "64k"):
            answer = Event(
                id=f"focal.financial.{record_id}.{tier}",
                type="sec_financial_answer",
                time=date(2030, 1, 1),
                params={
                    "workflow_id": workflow_id,
                    "record_id": record_id,
                    "control_tier": tier,
                },
                visibility=[f"focal.financial.{record_id}.{tier}"],
            )
            answers.append(answer)
            events.append(answer)
    independent_facet = Event(
        id="focal.financial.cashflow_tax_market_risk",
        type="sec_financial_answer",
        time=date(2030, 1, 1),
        params={
            "workflow_id": workflow_id,
            "record_id": record_ids[-1],
            "control_tier": "64k",
            "answer_family": "cashflow_tax_market_risk",
        },
        visibility=["focal.financial.cashflow_tax_market_risk"],
    )
    events.append(independent_facet)
    narrative_answers: list[Event] = []
    for tier in ("16k", "32k", "64k"):
        narrative = Event(
            id=f"focal.financial.narrative.{tier}",
            type="sec_financial_answer",
            time=date(2030, 1, 1),
            params={
                "workflow_id": workflow_id,
                "record_id": record_ids[-1],
                "control_tier": tier,
                "answer_family": "narrative_reconciliation",
                "section_event_ids": [],
            },
            visibility=[f"focal.financial.narrative.{tier}"],
        )
        narrative_answers.append(narrative)
        events.append(narrative)

    company_simulate._bind_financial_programs_to_annual_history(
        events, workflow_id=workflow_id
    )

    assert "history_profile_active" not in independent_facet.params
    assert independent_facet.required_inputs == []

    for narrative in narrative_answers:
        assert (
            narrative.params["history_control_tier"] == narrative.params["control_tier"]
        )
        assert narrative.params["required_record_ids"] == record_ids[-2:]
        assert len(narrative.params["required_relation_ids"]) == 1
        assert (
            sum(
                input_id.startswith("focal.sec_filing_")
                for input_id in narrative.required_inputs
            )
            == 2
        )

    selected = [
        answer
        for answer in answers
        if answer.params["history_control_tier"] == answer.params["control_tier"]
    ]
    assert [answer.params["control_tier"] for answer in selected] == [
        "16k",
        "32k",
        "64k",
    ]
    for expected_records, answer in zip(range(2, 5), selected, strict=True):
        assert answer.params["required_record_ids"] == record_ids[:expected_records]
        assert len(answer.params["required_relation_ids"]) == expected_records - 1
        assert (
            sum(
                input_id.startswith("focal.sec_filing_")
                for input_id in answer.required_inputs
            )
            == expected_records
        )
        assert (
            sum(
                input_id.startswith("focal.sec_prior_annual_")
                for input_id in answer.required_inputs
            )
            == expected_records - 1
        )


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


def _live_shaped_gcs_v2_record() -> SourceRecord:
    accession = "0001564590-22-026876"
    cik = "0000789019"
    filing_date = "2022-07-28"
    report_date = "2022-06-30"
    text = (
        f'<a title="{accession}.pdf">PDF</a>\n'
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey">{cik}</ix:nonNumeric>\n'
        f"<!-- Creation Date :{filing_date}T17:24:57+00:00 -->\n"
        f"<xbrli:endDate>{report_date}</xbrli:endDate>\n"
        + ("historical-filing-body\n" * 2_000)
        + '<ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>\n'
        + "Revenue: 198270\n"
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    record_id = f"sec:{accession}"
    facts = []
    for fact_id, field, value in (
        ("accession", "accession", accession),
        ("cik", "cik", cik),
        ("filing_date", "filing_date", filing_date),
        ("report_date", "report_date", report_date),
        ("form", "form", "10-K"),
        ("revenue", "revenue", "198270"),
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
        source_url="https://microsoft.gcs-web.com/node/30786/html",
        retrieval_url="",
        source_family="issuer_gcs_merged_filing_v2",
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


def _annual_relation(current: SourceRecord, prior: SourceRecord) -> SourceRelation:
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
    return SourceRelation(
        relation_id=f"{current.record_id}:prior_annual:{prior.record_id}",
        kind="prior_annual_filing",
        source_record_id=current.record_id,
        target_record_id=prior.record_id,
        evidence=evidence,
    )


def _annual_history_workflow() -> SourceWorkflow:
    records = tuple(
        _source_record(
            accession=f"0000950170-{year % 100:02d}-{year:06d}",
            filing_date=f"{year}-07-30",
            report_date=f"{year}-06-30",
            revenue=str(180_000 + (year - 2020) * 11_000),
        )
        for year in range(2020, 2025)
    )
    return SourceWorkflow(
        workflow_id="source:sec_filing:annualhistoryfiveyear0123456789",
        component_digest="2" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=tuple(record.provenance_id for record in records),
        records=records,
        relations=tuple(
            _annual_relation(current, prior) for prior, current in pairwise(records)
        ),
    )


def _ambiguous_annual_workflows() -> tuple[SourceWorkflow, ...]:
    prior = _source_record(
        accession="0000950170-20-002020",
        filing_date="2020-07-30",
        report_date="2020-06-30",
        revenue="180000",
    )
    current_a = _source_record(
        accession="0000950170-21-002021",
        filing_date="2021-07-30",
        report_date="2021-06-30",
        revenue="191000",
    )
    current_b = _source_record(
        accession="0000950170-21-102021",
        filing_date="2021-07-31",
        report_date="2021-06-30",
        revenue="202000",
    )
    branch_relations = (
        _annual_relation(current_a, prior),
        _annual_relation(current_b, prior),
    )
    branched = SourceWorkflow(
        workflow_id="source:sec_filing:annualbranch012345678901",
        component_digest="3" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=tuple(
            record.provenance_id for record in (prior, current_a, current_b)
        ),
        records=(prior, current_a, current_b),
        relations=branch_relations,
    )
    relation = branch_relations[0]
    duplicate = replace(
        branched,
        workflow_id="source:sec_filing:annualduplicate0123456789",
        component_digest="4" * 64,
        provenance_ids=(prior.provenance_id, current_a.provenance_id),
        records=(prior, current_a),
        relations=(relation, relation),
    )
    prior_b = _source_record(
        accession="0001018724-20-002020",
        cik="0001018724",
        filing_date="2020-02-01",
        report_date="2019-12-31",
        revenue="280000",
    )
    current_c = _source_record(
        accession="0001018724-21-002021",
        cik="0001018724",
        filing_date="2021-02-01",
        report_date="2020-12-31",
        revenue="291000",
    )
    disconnected_records = (prior, current_a, prior_b, current_c)
    disconnected = SourceWorkflow(
        workflow_id="source:sec_filing:annualdisconnected0123456",
        component_digest="5" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=tuple(record.provenance_id for record in disconnected_records),
        records=disconnected_records,
        relations=(
            _annual_relation(current_a, prior),
            _annual_relation(current_c, prior_b),
        ),
    )
    same_period_prior = _source_record(
        accession="0000950170-20-102020",
        filing_date="2020-07-31",
        report_date="2020-06-30",
        revenue="181000",
    )
    fork_records = (prior, same_period_prior, current_a)
    forked = SourceWorkflow(
        workflow_id="source:sec_filing:annualfork01234567890123",
        component_digest="6" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=tuple(record.provenance_id for record in fork_records),
        records=fork_records,
        relations=(
            _annual_relation(current_a, prior),
            _annual_relation(current_a, same_period_prior),
        ),
    )
    hidden_branch = replace(
        branched,
        workflow_id="source:sec_filing:annualhiddenbranch012345",
        component_digest="9" * 64,
        relations=(branch_relations[0],),
    )
    history = _annual_history_workflow()
    missing_adjacency = replace(
        history,
        workflow_id="source:sec_filing:annualmissingedge0123456",
        component_digest="a" * 64,
        relations=(history.relations[-1],),
    )
    return (
        branched,
        duplicate,
        disconnected,
        forked,
        hidden_branch,
        missing_adjacency,
    )


def _two_filing_workflow(
    *,
    workflow_id: str,
    component_digest: str,
    accession_prefix: str,
    cik: str,
    revenue_delta: int,
) -> SourceWorkflow:
    prior = _source_record(
        accession=f"{accession_prefix}-20-000001",
        cik=cik,
        filing_date="2020-07-30",
        report_date="2020-06-30",
        revenue="180000",
    )
    current = _source_record(
        accession=f"{accession_prefix}-21-000001",
        cik=cik,
        filing_date="2021-07-30",
        report_date="2021-06-30",
        revenue=str(180000 + revenue_delta),
    )
    return SourceWorkflow(
        workflow_id=workflow_id,
        component_digest=component_digest,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=(prior.provenance_id, current.provenance_id),
        records=(prior, current),
        relations=(_annual_relation(current, prior),),
    )


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
    assert query.preferred_length_buckets == ["16k"]
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


def test_gcs_v2_long_identity_uses_replayable_exact_multi_span_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)
    monkeypatch.setattr(
        company_simulate, "_sec_financial_events", _contract_financial_events
    )
    prior = _live_shaped_gcs_v2_record()
    current = _source_record(
        accession="0000950170-23-035122",
        filing_date="2023-07-27",
        report_date="2023-06-30",
        revenue="211915",
    )
    relation = _annual_relation(current, prior)
    workflow = SourceWorkflow(
        workflow_id="source:sec_filing:gcsv2longidentity012345",
        component_digest="7" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=(
            "issuer_gcs_merged_filing_v2",
            "sec_edgar_submission",
        ),
        provenance_ids=(prior.provenance_id, current.provenance_id),
        records=(prior, current),
        relations=(relation,),
        source_authorization={"adapter_revision": "sourceworkflow@2"},
    )

    materialized = materialize(
        1244,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    event = next(
        event
        for event in materialized.worlds["focal"].events
        if event.type == "sec_filing" and event.params["record_id"] == prior.record_id
    )
    assert event.params["provenance_operation"] == "sec_multi_span_projection_v1"
    assert event.params["parent_provenance_id"] == prior.provenance_id
    assert len(event.params["source_ranges"]) == 5
    assert all(
        item["parent_source_sha256"] == prior.source_sha256
        and prior.text[item["source_char_start"] : item["source_char_end"]]
        == item["text"]
        and hashlib.sha256(item["text"].encode()).hexdigest() == item["text_sha256"]
        for item in event.params["source_ranges"]
    )
    assert _sec_source_event_matches_record(event, prior)
    corrupted_params = deepcopy(event.params)
    corrupted_params["source_ranges"][0]["text_sha256"] = "0" * 64
    assert not _sec_source_event_matches_record(
        replace(event, params=corrupted_params), prior
    )
    assert any(
        event.type == "sec_prior_annual_filing_relation"
        for event in materialized.worlds["focal"].events
    )


def test_annual_history_grows_real_dependencies_across_all_bands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)
    monkeypatch.setattr(
        company_simulate, "_sec_financial_events", _contract_financial_events
    )
    materialized = materialize(
        1243,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[_annual_history_workflow()],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    artifacts = materialized.artifacts["focal"]
    queries = sorted(
        (
            query
            for query in materialized.queries
            if query.query_type == "sec_annual_revenue_change"
        ),
        key=lambda query: ("16k", "32k", "64k", "128k").index(
            query.preferred_length_buckets[0]
        ),
    )

    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
        ["128k"],
    ]
    assert len({query.base_task_group for query in queries}) == 1
    assert [query.proof_depth for query in queries] == sorted(
        query.proof_depth for query in queries
    )
    assert len({query.proof_depth for query in queries}) == len(queries)

    previous_ids: set[str] = set()
    for expected_records, query in zip(range(2, 6), queries, strict=True):
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert sum(item.doc_type == "sec_filing" for item in essential) == (
            expected_records
        )
        assert (
            sum(item.doc_type == "sec_source_section" for item in essential)
            == expected_records
        )
        assert sum(
            item.doc_type == "sec_prior_annual_filing_relation" for item in essential
        ) == (expected_records - 1)
        assert previous_ids < set(query.essential_event_ids)
        previous_ids = set(query.essential_event_ids)
        assert sum(
            op["op"] == "VALIDATE_PRIOR_ANNUAL_FILING" for op in query.program_ops
        ) == (expected_records - 1)
        assert (
            sum(op["op"] == "READ_XBRL_FACT" for op in query.program_ops)
            == expected_records
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
        assert verification.semantic_sufficient, notes
        assert verification.counterfactual_replay_sufficient, notes
        assert query.cf_answer != query.answer
        assert verification.remove_one_fails, notes
        assert verification.essential_single_doc_insufficient, notes

    longest = queries[-1]
    longest_essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in longest.essential_artifact_ids
    ]
    oldest_section = next(
        artifact
        for artifact in longest_essential
        if artifact.doc_type == "sec_source_section"
        and artifact.slots["params"]["record_id"].endswith("20-002020")
    )
    revenue_quote = oldest_section.slots["params"]["fact_spans"][0]["evidence_quote"]
    corrupted = [
        replace(
            artifact,
            text=artifact.text.replace(revenue_quote, f"{revenue_quote} CORRUPTED", 1),
        )
        if artifact.artifact_id == oldest_section.artifact_id
        else artifact
        for artifact in longest_essential
    ]
    assert (
        answer_from_artifacts(world, longest, corrupted, enforce_preconditions=True)
        == longest.answer
    )
    assert (
        semantic_answer_from_artifacts(
            world, longest, corrupted, enforce_preconditions=True
        )
        == "unknown"
    )


@pytest.mark.parametrize("workflow", _ambiguous_annual_workflows())
def test_ambiguous_annual_graph_does_not_emit_answer_or_query(
    monkeypatch: pytest.MonkeyPatch,
    workflow: SourceWorkflow,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)
    monkeypatch.setattr(
        company_simulate, "_sec_financial_events", _contract_financial_events
    )

    materialized = materialize(
        1244,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[workflow],
        include_program_joins=False,
    )

    assert not any(
        event.type == "sec_annual_revenue_change"
        for event in materialized.worlds["focal"].events
    )
    assert not any(
        query.query_type == "sec_annual_revenue_change"
        for query in materialized.queries
    )


@pytest.mark.parametrize("collision", ("component_digest", "workflow_id"))
def test_independent_annual_workflows_have_collision_free_answer_and_query_ids(
    monkeypatch: pytest.MonkeyPatch,
    collision: str,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)
    monkeypatch.setattr(
        company_simulate, "_sec_financial_events", _contract_financial_events
    )
    shared_workflow_id = "source:sec_filing:sharedannualworkflow012345"
    shared_digest = "7" * 64
    first = _two_filing_workflow(
        workflow_id=(
            shared_workflow_id
            if collision == "workflow_id"
            else "source:sec_filing:firstannualworkflow012345"
        ),
        component_digest=shared_digest,
        accession_prefix="0000950170",
        cik="0000789019",
        revenue_delta=10,
    )
    second = _two_filing_workflow(
        workflow_id=(
            shared_workflow_id
            if collision == "workflow_id"
            else "source:sec_filing:secondannualworkflow01234"
        ),
        component_digest=(
            shared_digest if collision == "component_digest" else "8" * 64
        ),
        accession_prefix="0001018724",
        cik="0001018724",
        revenue_delta=40,
    )
    materialized = materialize(
        1245,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[first, second],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    artifacts = materialized.artifacts["focal"]
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "sec_annual_revenue_change"
    ]

    assert len(queries) == 2
    assert len({query.answer_key for query in queries}) == 2
    assert len({query.query_id for query in queries}) == 2
    for query in queries:
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert (
            answer_from_artifacts(world, query, essential, enforce_preconditions=True)
            == query.answer
        )


def test_missing_revenue_evidence_cannot_hide_ambiguous_annual_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LONGWORLD_ATTESTATION_KEY", ARTIFACT_KEY)

    def selective_financial_events(**kwargs) -> list[Event]:
        record = kwargs["record"]
        if record.record_id.endswith("21-102021"):
            return []
        return _contract_financial_events(**kwargs)

    monkeypatch.setattr(
        company_simulate, "_sec_financial_events", selective_financial_events
    )
    branched = _ambiguous_annual_workflows()[0]
    materialized = materialize(
        1246,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=[branched],
        include_program_joins=False,
    )

    assert not any(
        event.type == "sec_annual_revenue_change"
        for event in materialized.worlds["focal"].events
    )
    assert not any(
        query.query_type == "sec_annual_revenue_change"
        for query in materialized.queries
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
    assert query.preferred_length_buckets == ["16k"]
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

    filing_artifacts = [
        artifact for artifact in essential if artifact.doc_type == "sec_filing"
    ]
    assert len(filing_artifacts) == 2
    assert all(
        artifact.slots["params"]["text"] in artifact.text
        and artifact_classification(artifact).source_origin
        == SourceOrigin(artifact.slots["params"]["source_origin"])
        and artifact_classification(artifact).provenance_id
        == artifact.slots["params"]["provenance_id"]
        for artifact in filing_artifacts
    )

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

    current_filing = filing_artifacts[-1]
    original_report_date = current_filing.slots["params"]["ground_values"][-1]
    corrupted_filing = [
        replace(
            artifact,
            text=artifact.text.replace(original_report_date, "2024-06-29", 1),
        )
        if artifact.artifact_id == current_filing.artifact_id
        else artifact
        for artifact in essential
    ]
    assert (
        semantic_answer_from_artifacts(
            world, query, corrupted_filing, enforce_preconditions=True
        )
        == "unknown"
    )
