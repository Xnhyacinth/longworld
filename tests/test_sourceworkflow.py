from __future__ import annotations

import copy
import hashlib

import pytest

from longworld.core.provenance import ProvenanceError
from longworld.core.sourceworkflow import (
    PAPER_SOURCE_KIND,
    SEC_SOURCE_KIND,
    SOURCE_WORKFLOW_ADAPTER_REVISION_V1,
    SOURCE_WORKFLOW_ADAPTER_REVISION_V2,
    WIKIMEDIA_SOURCE_KIND,
    adapt_paper_manifest,
    adapt_sec_manifest,
    adapt_source_manifest,
    adapt_wikimedia_manifest,
)
from longworld.core.taxonomy import SourceOrigin


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fact(text: str, fact_id: str, field: str, value: str) -> dict:
    quote = next(line for line in text.splitlines() if value in line)
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": quote,
        "evidence_char_start": text.index(quote),
    }


def _sec_filing(
    accession: str, form: str, revenue: str, *, cik: str = "0000000001"
) -> dict:
    text = "\n".join(
        (
            f"ACCESSION NUMBER: {accession}",
            f"CONFORMED SUBMISSION TYPE: {form}",
            "FILED AS OF DATE: 20260220",
            "CONFORMED PERIOD OF REPORT: 20251231",
            f"Revenue after audit adjustment: {revenue}",
        )
    )
    source_sha256 = _digest(text)
    facts = [
        _fact(text, "accession", "accession", accession),
        _fact(text, "form", "form", form),
        _fact(text, "filing_date", "filing_date", "20260220"),
        _fact(text, "report_date", "report_date", "20251231"),
        _fact(text, "revenue", "revenue", revenue),
    ]
    for fact in facts:
        fact["source_sha256"] = source_sha256
    return {
        "record_id": f"sec:{accession}",
        "accession": accession,
        "cik": cik,
        "form": form,
        "filing_date": "2026-02-20",
        "report_date": "2025-12-31",
        "source_url": f"https://www.sec.gov/Archives/{accession}.txt",
        "source_file": f"{accession}.txt",
        "source_sha256": source_sha256,
        "text_sha256": _digest(text),
        "provenance_id": f"sha256:{source_sha256}",
        "parser": "sec_complete_submission_header@1",
        "text": text,
        "derived_facts": facts,
    }


def _sec_manifest(cik: str = "0000000001") -> dict:
    original = _sec_filing(f"{cik}-26-000001", "10-K", "USD 40 million", cik=cik)
    amendment = _sec_filing(f"{cik}-26-000002", "10-K/A", "USD 42 million", cik=cik)
    return {
        "schema_version": "longworld.sec-filing-manifest.v1",
        "source_status": "public_sec_download",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "n": 2,
        "filings": [original, amendment],
        "filing_relations": [
            {
                "relation_id": "amendment-to-original",
                "relation_type": "amends_report",
                "from_record_id": amendment["record_id"],
                "to_record_id": original["record_id"],
                "shared_report_date": "2025-12-31",
                "evidence": [
                    {
                        "record_id": amendment["record_id"],
                        "fact_ids": ["form", "report_date"],
                    },
                    {
                        "record_id": original["record_id"],
                        "fact_ids": ["form", "report_date"],
                    },
                ],
            }
        ],
    }


def _document_record(
    record_id: str,
    text: str,
    *,
    source_family: str,
    occurred_at: str,
    **identity: object,
) -> dict:
    source_sha256 = _digest(text)
    return {
        "record_id": record_id,
        "source_url": f"https://example.invalid/source/{record_id}",
        "source_file": f"{record_id}.txt",
        "source_sha256": source_sha256,
        "text_sha256": _digest(text),
        "provenance_id": f"sha256:{source_sha256}",
        "occurred_at": occurred_at,
        "retrieved_at": "2026-08-24T09:00:00Z",
        "parser": "fixture_text@1",
        "text": text,
        "source_family": source_family,
        **identity,
    }


def _relation(
    relation_id: str,
    kind: str,
    source: dict,
    target: dict,
    quote: str,
) -> dict:
    return {
        "relation_id": relation_id,
        "kind": kind,
        "source_record_id": source["record_id"],
        "target_record_id": target["record_id"],
        "evidence_record_id": source["record_id"],
        "evidence_quote": quote,
        "evidence_char_start": source["text"].index(quote),
        "source_sha256": source["source_sha256"],
    }


def _paper_manifest() -> dict:
    v1 = _document_record(
        "manuscript-v1",
        "Revision v1 reports accuracy of 81.0%.",
        source_family="arxiv_record",
        occurred_at="2025-01-01T00:00:00Z",
        work_id="arxiv:2401.00001",
        role="manuscript_revision",
        revision_id="v1",
    )
    v2 = _document_record(
        "manuscript-v2",
        "Revision v2 supersedes revision v1 and reports accuracy of 84.2%.",
        source_family="arxiv_record",
        occurred_at="2025-02-01T00:00:00Z",
        work_id="arxiv:2401.00001",
        role="manuscript_revision",
        revision_id="v2",
    )
    review = _document_record(
        "review-1",
        "The review evaluates revision v2 and requests variance analysis.",
        source_family="openreview_note",
        occurred_at="2025-02-03T00:00:00Z",
        work_id="arxiv:2401.00001",
        role="peer_review",
        revision_id="review-1",
    )
    relations = [
        _relation(
            "revision-v2-v1",
            "revision_of",
            v2,
            v1,
            "Revision v2 supersedes revision v1",
        ),
        _relation(
            "review-v2",
            "reviews",
            review,
            v2,
            "The review evaluates revision v2",
        ),
    ]
    return {
        "schema_version": "longworld.paper-workflow-manifest.v1",
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "n": 3,
        "n_relations": 2,
        "records": [v1, v2, review],
        "relations": relations,
    }


def test_fetch_paper_manifest_v2_uses_text_hash_for_derived_fact_binding() -> None:
    manifest = _paper_manifest()
    manifest["schema_version"] = "longworld.paper-workflow-manifest.v2"
    revision = manifest["records"][1]
    value = "accuracy of 84.2%"
    quote = revision["text"]
    revision["derived_facts"] = [
        {
            "fact_id": "revision-added-text",
            "field": "revision_added_text",
            "value": value,
            "evidence_quote": quote,
            "evidence_char_start": 0,
            "text_sha256": revision["text_sha256"],
        }
    ]

    [workflow] = adapt_source_manifest(
        manifest,
        source_kind=PAPER_SOURCE_KIND,
        signed_bundle_authorized=True,
    )

    [fact] = next(
        record.facts
        for record in workflow.records
        if record.record_id == "manuscript-v2"
    )
    assert fact.value == value
    assert fact.source_sha256 == revision["source_sha256"]


def _wikimedia_manifest() -> dict:
    old = _document_record(
        "page-200-r100",
        "Ada Lovelace was a mathematician.",
        source_family="ignored-caller-family",
        occurred_at="2025-02-01T00:00:00Z",
        kind="wikipedia_revision",
        revision_id=100,
        page_id=200,
        title="Ada Lovelace",
        parent_revision_id=None,
    )
    current = _document_record(
        "page-200-r101",
        (
            "This revision replaces revision 100. The page maps to Wikidata entity "
            "Q7259."
        ),
        source_family="ignored-caller-family",
        occurred_at="2025-03-01T00:00:00Z",
        kind="wikipedia_revision",
        revision_id=101,
        page_id=200,
        title="Ada Lovelace",
        parent_revision_id=100,
    )
    entity = _document_record(
        "entity-Q7259-r220",
        'Entity Q7259 has English label "Ada Lovelace".',
        source_family="ignored-caller-family",
        occurred_at="2025-03-02T00:00:00Z",
        kind="wikidata_entity_revision",
        revision_id=220,
        entity_id="Q7259",
    )
    relations = [
        _relation(
            "page-r101-r100",
            "revision_of",
            current,
            old,
            "This revision replaces revision 100",
        ),
        _relation(
            "page-Q7259",
            "page_describes_entity",
            current,
            entity,
            "The page maps to Wikidata entity Q7259",
        ),
        _relation(
            "Q7259-page",
            "entity_resolves_page",
            entity,
            current,
            'Entity Q7259 has English label "Ada Lovelace"',
        ),
    ]
    return {
        "schema_version": "longworld.wikipedia-workflow-manifest.v2",
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "n": 3,
        "n_relations": 3,
        "records": [old, current, entity],
        "relations": relations,
    }


def test_disabled_inventory_requires_explicit_signed_bundle_authorization() -> None:
    with pytest.raises(ProvenanceError, match="signed bundle authorization"):
        adapt_sec_manifest(_sec_manifest())

    with pytest.raises(ProvenanceError, match="signed bundle authorization"):
        adapt_sec_manifest(_sec_manifest(), signed_bundle_authorized=1)  # type: ignore[arg-type]


def test_fixture_inventory_cannot_be_adapted_as_real() -> None:
    manifest = _sec_manifest()
    manifest["source_status"] = "test_fixture"

    with pytest.raises(ProvenanceError, match="test fixture"):
        adapt_sec_manifest(manifest, signed_bundle_authorized=True)


def test_sec_adapter_preserves_exact_facts_and_builds_component_identity() -> None:
    [workflow] = adapt_sec_manifest(_sec_manifest(), signed_bundle_authorized=True)

    assert workflow.source_kind == SEC_SOURCE_KIND
    assert workflow.target_domain == "company"
    assert workflow.source_origin is SourceOrigin.REAL_PUBLIC
    assert workflow.source_families == ("sec_edgar_submission",)
    assert len(workflow.records) == 2
    assert len(workflow.relations) == 1
    assert len(workflow.relations[0].evidence) == 4
    revenue = next(
        fact
        for record in workflow.records
        for fact in record.facts
        if fact.value == "USD 42 million"
    )
    assert revenue.evidence_quote[revenue.value_offset :] == revenue.value
    assert revenue.char_end == revenue.char_start + len(revenue.evidence_quote)


def test_sec_adapter_accepts_one_authentic_filing_without_inventing_a_relation() -> (
    None
):
    manifest = _sec_manifest()
    manifest["filings"] = manifest["filings"][:1]
    manifest["filing_relations"] = []
    manifest["n"] = 1

    [workflow] = adapt_sec_manifest(
        manifest,
        signed_bundle_authorized=True,
        adapter_revision=SOURCE_WORKFLOW_ADAPTER_REVISION_V2,
    )

    assert len(workflow.records) == 1
    assert workflow.relations == ()
    assert workflow.records[0].facts


def test_v1_sec_adapter_keeps_rejecting_relationless_singletons() -> None:
    manifest = _sec_manifest()
    manifest["filings"] = manifest["filings"][:1]
    manifest["filing_relations"] = []
    manifest["n"] = 1

    with pytest.raises(ProvenanceError, match="relations.*non-empty"):
        adapt_sec_manifest(
            manifest,
            signed_bundle_authorized=True,
            adapter_revision=SOURCE_WORKFLOW_ADAPTER_REVISION_V1,
        )


def test_component_identity_ignores_filename_and_caller_labels() -> None:
    original = _paper_manifest()
    [first] = adapt_paper_manifest(original, signed_bundle_authorized=True)
    relabelled = copy.deepcopy(original)
    renames = {
        "manuscript-v1": "caller-label-a",
        "manuscript-v2": "caller-label-b",
        "review-1": "caller-label-c",
    }
    for index, record in enumerate(relabelled["records"]):
        record["record_id"] = renames[record["record_id"]]
        record["source_file"] = f"gold-answer-{index}.txt"
        record["work_id"] = "caller-invented-work-label"
    for index, relation in enumerate(relabelled["relations"]):
        relation["relation_id"] = f"caller-relation-{index}"
        relation["source_record_id"] = renames[relation["source_record_id"]]
        relation["target_record_id"] = renames[relation["target_record_id"]]
        relation["evidence_record_id"] = renames[relation["evidence_record_id"]]

    [second] = adapt_paper_manifest(relabelled, signed_bundle_authorized=True)

    assert second.workflow_id == first.workflow_id


def test_component_identity_ignores_fact_labels_even_when_spans_match() -> None:
    first_manifest = _sec_manifest()
    second_manifest = copy.deepcopy(first_manifest)

    def add_alias(manifest: dict, primary_id: str, alias_id: str) -> None:
        filing = manifest["filings"][1]
        form = next(
            fact for fact in filing["derived_facts"] if fact["fact_id"] == "form"
        )
        form["fact_id"] = primary_id
        alias = {**form, "fact_id": alias_id, "field": "submission_form_alias"}
        filing["derived_facts"].append(alias)
        evidence = manifest["filing_relations"][0]["evidence"][0]
        evidence["fact_ids"] = [primary_id, alias_id, "report_date"]

    add_alias(first_manifest, "a-primary", "z-alias")
    add_alias(second_manifest, "z-primary", "a-alias")

    [first] = adapt_sec_manifest(first_manifest, signed_bundle_authorized=True)
    [second] = adapt_sec_manifest(second_manifest, signed_bundle_authorized=True)

    assert second.workflow_id == first.workflow_id


def test_exact_fact_span_corruption_is_rejected() -> None:
    manifest = _sec_manifest()
    fact = manifest["filings"][1]["derived_facts"][-1]
    fact["evidence_char_start"] += 1

    with pytest.raises(ProvenanceError, match="fact evidence span"):
        adapt_sec_manifest(manifest, signed_bundle_authorized=True)


def test_exact_relation_span_corruption_is_rejected() -> None:
    manifest = _paper_manifest()
    manifest["relations"][0]["evidence_quote"] = "revision v1 and fake gold"

    with pytest.raises(ProvenanceError, match="relation evidence span"):
        adapt_paper_manifest(manifest, signed_bundle_authorized=True)


def test_duplicate_relation_edge_is_rejected() -> None:
    manifest = _paper_manifest()
    duplicate = copy.deepcopy(manifest["relations"][0])
    duplicate["relation_id"] = "different-label-same-edge"
    manifest["relations"].append(duplicate)
    manifest["n_relations"] += 1

    with pytest.raises(ProvenanceError, match="duplicate relation edge"):
        adapt_paper_manifest(manifest, signed_bundle_authorized=True)


def test_paper_relation_cannot_cross_work_boundaries() -> None:
    manifest = _paper_manifest()
    manifest["records"][-1]["work_id"] = "arxiv:9999.99999"

    with pytest.raises(ProvenanceError, match="crosses workflow boundaries"):
        adapt_paper_manifest(manifest, signed_bundle_authorized=True)


def test_record_disconnected_from_all_relations_is_rejected() -> None:
    manifest = _paper_manifest()
    orphan = _document_record(
        "orphan",
        "An unrelated document with no workflow edge.",
        source_family="openreview_note",
        occurred_at="2025-02-04T00:00:00Z",
        work_id="arxiv:2401.00001",
        role="author_response",
        revision_id="orphan",
    )
    manifest["records"].append(orphan)
    manifest["n"] += 1

    with pytest.raises(ProvenanceError, match="disconnected record"):
        adapt_paper_manifest(manifest, signed_bundle_authorized=True)


def test_distinct_connected_components_are_normalized_independently() -> None:
    manifest = _sec_manifest()
    second = _sec_manifest("0000000002")
    second["filing_relations"][0]["relation_id"] = "second-amendment-to-original"
    manifest["filings"].extend(second["filings"])
    manifest["filing_relations"].extend(second["filing_relations"])
    manifest["n"] = 4

    workflows = adapt_sec_manifest(manifest, signed_bundle_authorized=True)

    assert len(workflows) == 2
    assert [workflow.workflow_id for workflow in workflows] == sorted(
        workflow.workflow_id for workflow in workflows
    )


def test_paper_source_families_and_private_origin_are_preserved() -> None:
    manifest = _paper_manifest()
    manifest["source_status"] = "authorized_download"

    [workflow] = adapt_paper_manifest(manifest, signed_bundle_authorized=True)

    assert workflow.source_origin is SourceOrigin.REAL_PRIVATE_EXPORT
    assert workflow.source_families == ("arxiv_record", "openreview_note")
    assert {record.source_family for record in workflow.records} == {
        "arxiv_record",
        "openreview_note",
    }


def test_wikimedia_family_is_derived_from_verified_record_kind() -> None:
    [workflow] = adapt_wikimedia_manifest(
        _wikimedia_manifest(), signed_bundle_authorized=True
    )

    assert workflow.source_kind == WIKIMEDIA_SOURCE_KIND
    assert workflow.target_domain == "researchlab"
    assert workflow.source_families == (
        "wikidata_entity_revision",
        "wikipedia_revision",
    )
    assert {relation.kind for relation in workflow.relations} == {
        "revision_of",
        "page_describes_entity",
        "entity_resolves_page",
    }


def test_duplicate_record_provenance_is_rejected() -> None:
    manifest = _paper_manifest()
    manifest["records"][1]["source_sha256"] = manifest["records"][0]["source_sha256"]
    manifest["records"][1]["provenance_id"] = manifest["records"][0]["provenance_id"]
    manifest["relations"][0]["source_sha256"] = manifest["records"][0]["source_sha256"]

    with pytest.raises(ProvenanceError, match="duplicate record provenance"):
        adapt_paper_manifest(manifest, signed_bundle_authorized=True)


def test_explicit_dispatch_rejects_kind_schema_mismatch() -> None:
    with pytest.raises(ProvenanceError, match="does not match manifest schema"):
        adapt_source_manifest(
            _paper_manifest(),
            source_kind=SEC_SOURCE_KIND,
            signed_bundle_authorized=True,
        )
