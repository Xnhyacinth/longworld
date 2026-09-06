"""Pure normalization for already-verified real-source workflow inventories.

This module deliberately does not read files or verify attestations.  Its caller must
first verify an inventory and its enclosing signed bundle, then opt in with
``signed_bundle_authorized=True``.  The adapters preserve source-grounded text,
facts, relations, and provenance while deriving component identities without using
filenames, record labels, relation labels, or caller-supplied dossier labels.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from longworld.core.documentworkflow import (
    ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA,
    PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA,
    PAPER_WORKFLOW_MANIFEST_SCHEMA,
    WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA,
    audit_elife_review_revision_task,
)
from longworld.core.filingworkflow import (
    SEC_ANNUAL_RELATION_FIELDS,
    SEC_FILING_MANIFEST_SCHEMA,
)
from longworld.core.issuerfilingworkflow import (
    ISSUER_IR_FILING_MANIFEST_SCHEMA,
    ISSUER_IR_SOURCE_FAMILY,
    ISSUER_IR_SOURCE_KIND,
)
from longworld.core.issuerpdfworkflow import (
    ISSUER_OFFICIAL_PDF_INVENTORY_SCHEMA,
    ISSUER_OFFICIAL_PDF_SOURCE_KIND,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import IETF_WORKFLOW_MANIFEST_SCHEMA
from longworld.core.taxonomy import SourceOrigin

SEC_SOURCE_KIND = "sec_filing"
PAPER_SOURCE_KIND = "paper_workflow"
WIKIMEDIA_SOURCE_KIND = "wikimedia"
STANDARDS_SOURCE_KIND = "ietf_standards"
SOURCE_WORKFLOW_ADAPTER_REVISION_V1 = "sourceworkflow@1"
SOURCE_WORKFLOW_ADAPTER_REVISION_V2 = "sourceworkflow@2"
SOURCE_WORKFLOW_ADAPTER_REVISIONS = frozenset(
    {SOURCE_WORKFLOW_ADAPTER_REVISION_V1, SOURCE_WORKFLOW_ADAPTER_REVISION_V2}
)

_SOURCE_KIND_SCHEMAS = {
    SEC_SOURCE_KIND: frozenset({SEC_FILING_MANIFEST_SCHEMA}),
    PAPER_SOURCE_KIND: frozenset(
        {
            PAPER_WORKFLOW_MANIFEST_SCHEMA,
            PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA,
            ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA,
        }
    ),
    WIKIMEDIA_SOURCE_KIND: frozenset({WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA}),
    ISSUER_IR_SOURCE_KIND: frozenset({ISSUER_IR_FILING_MANIFEST_SCHEMA}),
    STANDARDS_SOURCE_KIND: frozenset({IETF_WORKFLOW_MANIFEST_SCHEMA}),
    ISSUER_OFFICIAL_PDF_SOURCE_KIND: frozenset(
        {ISSUER_OFFICIAL_PDF_INVENTORY_SCHEMA}
    ),
}
_SOURCE_KIND_DOMAINS = {
    SEC_SOURCE_KIND: "company",
    PAPER_SOURCE_KIND: "researchlab",
    WIKIMEDIA_SOURCE_KIND: "researchlab",
    ISSUER_IR_SOURCE_KIND: "company",
    STANDARDS_SOURCE_KIND: "standards",
    ISSUER_OFFICIAL_PDF_SOURCE_KIND: "company",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_STATUS = {
    SEC_SOURCE_KIND: "public_sec_download",
    PAPER_SOURCE_KIND: "public_api_export",
    WIKIMEDIA_SOURCE_KIND: "public_api_export",
    ISSUER_IR_SOURCE_KIND: "issuer_owned_ir_download",
    STANDARDS_SOURCE_KIND: "public_api_export",
    ISSUER_OFFICIAL_PDF_SOURCE_KIND: "issuer_owned_official_pdf",
}
_PAPER_RELATION_ROLES = {
    "revision_of": ("manuscript_revision", "manuscript_revision"),
    "reviews": ("peer_review", "manuscript_revision"),
    "responds_to": ("author_response", "peer_review"),
    "evaluates_benchmark": ("manuscript_revision", "benchmark_report"),
    "reproduces_result": ("benchmark_report", "manuscript_revision"),
}
_ELIFE_RELATIONS = {
    "implements_revision_delta",
    "requests_revision",
    "responds_to_review",
    "revision_of",
}
_WIKIMEDIA_RELATIONS = {
    "revision_of",
    "page_describes_entity",
    "entity_resolves_page",
}


@dataclass(frozen=True)
class SourceFact:
    fact_id: str
    field: str
    value: str
    record_id: str
    evidence_quote: str
    char_start: int
    char_end: int
    value_offset: int
    source_sha256: str


@dataclass(frozen=True)
class SourceEvidence:
    record_id: str
    evidence_quote: str
    char_start: int
    char_end: int
    source_sha256: str
    fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRelation:
    relation_id: str
    kind: str
    source_record_id: str
    target_record_id: str
    evidence: tuple[SourceEvidence, ...]


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    kind: str
    occurred_at: str
    text: str
    source_url: str
    retrieval_url: str
    source_family: str
    source_origin: SourceOrigin
    provenance_id: str
    source_sha256: str
    text_sha256: str
    facts: tuple[SourceFact, ...]
    attributes: tuple[tuple[str, str], ...] = ()

    def attribute(self, name: str) -> str:
        return dict(self.attributes).get(name, "")


@dataclass(frozen=True)
class SourceWorkflow:
    workflow_id: str
    component_digest: str
    source_kind: str
    target_domain: str
    source_origin: SourceOrigin
    source_families: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    records: tuple[SourceRecord, ...]
    relations: tuple[SourceRelation, ...]
    source_authorization: dict[str, Any] | None = None


def _objects(
    value: object, label: str, *, allow_empty: bool = False
) -> list[Mapping[str, Any]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or (not value and not allow_empty)
        or not all(isinstance(item, Mapping) for item in value)
    ):
        raise ProvenanceError(f"source workflow {label} must be a non-empty list")
    return list(value)


def _inventory_origin(
    manifest: Mapping[str, Any],
    *,
    source_kind: str,
    signed_bundle_authorized: bool,
) -> SourceOrigin:
    if signed_bundle_authorized is not True:
        raise ProvenanceError(
            "source inventory requires explicit signed bundle authorization"
        )
    if (
        manifest.get("data_stage") != "source_inventory"
        or manifest.get("hybrid_train_ready") is not False
        or manifest.get("production_eligible") is not False
        or manifest.get("generation_integration") != "disabled"
    ):
        raise ProvenanceError("source workflow input is not a disabled inventory")
    status = str(manifest.get("source_status") or "")
    if status == "test_fixture":
        raise ProvenanceError("test fixture cannot be adapted as a real workflow")
    if status == _PUBLIC_STATUS[source_kind] or (
        source_kind == SEC_SOURCE_KIND and status == "issuer_owned_public_export"
    ):
        return SourceOrigin.REAL_PUBLIC
    if status == "authorized_download":
        return SourceOrigin.REAL_PRIVATE_EXPORT
    raise ProvenanceError("source inventory status does not match source kind")


def _identity_attributes(
    raw: Mapping[str, Any], names: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (name, str(raw[name]))
            for name in names
            if raw.get(name) is not None and str(raw[name])
        )
    )


def _facts(
    raw_facts: object,
    *,
    record_id: str,
    text: str,
    text_sha256: str,
    source_sha256: str,
    required: bool,
    binding_field: str,
) -> tuple[SourceFact, ...]:
    if raw_facts is None and not required:
        return ()
    values = _objects(raw_facts, "facts")
    facts: list[SourceFact] = []
    seen_ids: set[str] = set()
    for raw in values:
        fact_id = str(raw.get("fact_id") or "").strip()
        field = str(raw.get("field") or "").strip()
        value = str(raw.get("value") or "").strip()
        quote = str(raw.get("evidence_quote") or "")
        start = raw.get("evidence_char_start")
        if not fact_id or fact_id in seen_ids or not field or not value or not quote:
            raise ProvenanceError("source fact identity is missing or duplicated")
        if (
            not isinstance(start, int)
            or start < 0
            or text[start : start + len(quote)] != quote
        ):
            raise ProvenanceError(
                "source fact evidence span does not match record text"
            )
        value_offset = quote.find(value)
        if value_offset < 0:
            raise ProvenanceError("source fact value is not present in evidence quote")
        expected_binding = (
            source_sha256 if binding_field == "source_sha256" else text_sha256
        )
        if raw.get(binding_field) != expected_binding:
            raise ProvenanceError("source fact provenance does not match its record")
        facts.append(
            SourceFact(
                fact_id=fact_id,
                field=field,
                value=value,
                record_id=record_id,
                evidence_quote=quote,
                char_start=start,
                char_end=start + len(quote),
                value_offset=value_offset,
                source_sha256=source_sha256,
            )
        )
        seen_ids.add(fact_id)
    return tuple(sorted(facts, key=lambda fact: (fact.char_start, fact.fact_id)))


def _record(
    raw: Mapping[str, Any],
    *,
    kind: str,
    occurred_at: str,
    source_family: str,
    source_origin: SourceOrigin,
    identity_fields: tuple[str, ...],
    require_facts: bool = False,
    fact_binding_field: str = "text_sha256",
) -> SourceRecord:
    record_id = str(raw.get("record_id") or "").strip()
    text = raw.get("text")
    source_url = str(raw.get("source_url") or "").strip()
    retrieval_url = str(raw.get("retrieval_url") or "").strip()
    source_sha256 = str(raw.get("source_sha256") or "")
    text_sha256 = str(raw.get("text_sha256") or "")
    provenance_id = str(raw.get("provenance_id") or "")
    if (
        not record_id
        or not kind
        or not occurred_at
        or not source_family
        or not source_url
    ):
        raise ProvenanceError("source workflow record identity is incomplete")
    if not isinstance(text, str) or not text:
        raise ProvenanceError("source workflow record text is missing")
    if (
        _SHA256.fullmatch(source_sha256) is None
        or _SHA256.fullmatch(text_sha256) is None
        or provenance_id != f"sha256:{source_sha256}"
        or hashlib.sha256(text.encode()).hexdigest() != text_sha256
    ):
        raise ProvenanceError("source workflow record provenance is invalid")
    return SourceRecord(
        record_id=record_id,
        kind=kind,
        occurred_at=occurred_at,
        text=text,
        source_url=source_url,
        retrieval_url=retrieval_url,
        source_family=source_family,
        source_origin=source_origin,
        provenance_id=provenance_id,
        source_sha256=source_sha256,
        text_sha256=text_sha256,
        facts=_facts(
            raw.get("derived_facts"),
            record_id=record_id,
            text=text,
            text_sha256=text_sha256,
            source_sha256=source_sha256,
            required=require_facts,
            binding_field=fact_binding_field,
        ),
        attributes=_identity_attributes(raw, identity_fields),
    )


def _document_relation(
    raw: Mapping[str, Any],
    *,
    records: Mapping[str, SourceRecord],
    allowed_kinds: set[str],
) -> SourceRelation:
    relation_id = str(raw.get("relation_id") or "").strip()
    kind = str(raw.get("kind") or "")
    source_id = str(raw.get("source_record_id") or "")
    target_id = str(raw.get("target_record_id") or "")
    evidence_id = str(raw.get("evidence_record_id") or "")
    quote = str(raw.get("evidence_quote") or "")
    start = raw.get("evidence_char_start")
    if (
        not relation_id
        or kind not in allowed_kinds
        or source_id == target_id
        or source_id not in records
        or target_id not in records
        or evidence_id != source_id
        or not quote
        or not isinstance(start, int)
        or start < 0
    ):
        raise ProvenanceError("source relation identity is invalid")
    evidence_record = records[evidence_id]
    if evidence_record.text[start : start + len(quote)] != quote:
        raise ProvenanceError(
            "source relation evidence span does not match record text"
        )
    if raw.get("source_sha256") != evidence_record.source_sha256:
        raise ProvenanceError(
            "source relation provenance does not match evidence record"
        )
    return SourceRelation(
        relation_id=relation_id,
        kind=kind,
        source_record_id=source_id,
        target_record_id=target_id,
        evidence=(
            SourceEvidence(
                record_id=evidence_id,
                evidence_quote=quote,
                char_start=start,
                char_end=start + len(quote),
                source_sha256=evidence_record.source_sha256,
            ),
        ),
    )


def _sec_relation(
    raw: Mapping[str, Any], *, records: Mapping[str, SourceRecord]
) -> SourceRelation:
    relation_id = str(raw.get("relation_id") or "").strip()
    kind = str(raw.get("relation_type") or "")
    source_id = str(raw.get("from_record_id") or "")
    target_id = str(raw.get("to_record_id") or "")
    if (
        not relation_id
        or kind not in {"amends_report", "prior_annual_filing"}
        or source_id == target_id
        or source_id not in records
        or target_id not in records
    ):
        raise ProvenanceError("SEC source relation identity is invalid")
    source = records[source_id]
    target = records[target_id]
    source_form = source.attribute("form")
    if kind == "amends_report":
        shared_report_date = str(raw.get("shared_report_date") or "")
        if (
            source.attribute("cik") != target.attribute("cik")
            or not source_form.endswith("/A")
            or source_form.removesuffix("/A") != target.attribute("form")
            or not shared_report_date
            or source.attribute("report_date") != shared_report_date
            or target.attribute("report_date") != shared_report_date
        ):
            raise ProvenanceError("SEC source relation crosses workflow boundaries")
        required_fields: set[str] | None = None
    else:
        current_report = source.attribute("report_date")
        prior_report = target.attribute("report_date")
        try:
            current_report_day = date.fromisoformat(current_report)
            prior_report_day = date.fromisoformat(prior_report)
            current_filing_day = date.fromisoformat(source.attribute("filing_date"))
            prior_filing_day = date.fromisoformat(target.attribute("filing_date"))
        except ValueError as exc:
            raise ProvenanceError("SEC annual relation dates are invalid") from exc
        expected_relation_id = f"{source_id}:prior_annual:{target_id}"
        intermediate = [
            record
            for record in records.values()
            if record.record_id not in {source_id, target_id}
            and record.attribute("cik") == source.attribute("cik")
            and record.attribute("form") == source_form
            and prior_report_day
            < date.fromisoformat(record.attribute("report_date"))
            < current_report_day
        ]
        if (
            relation_id != expected_relation_id
            or source.attribute("cik") != target.attribute("cik")
            or source_form != "10-K"
            or target.attribute("form") != source_form
            or str(raw.get("current_report_date") or "") != current_report
            or str(raw.get("prior_report_date") or "") != prior_report
            or current_report_day <= prior_report_day
            or current_filing_day <= prior_filing_day
            or intermediate
        ):
            raise ProvenanceError("SEC source relation crosses workflow boundaries")
        required_fields = set(SEC_ANNUAL_RELATION_FIELDS)

    raw_evidence = _objects(raw.get("evidence"), "relation evidence")
    fact_index = {
        record_id: {fact.fact_id: fact for fact in record.facts}
        for record_id, record in records.items()
    }
    evidence: list[SourceEvidence] = []
    referenced: set[tuple[str, str]] = set()
    evidence_records: set[str] = set()
    for item in raw_evidence:
        record_id = str(item.get("record_id") or "")
        fact_ids = item.get("fact_ids")
        if (
            record_id not in {source_id, target_id}
            or not isinstance(fact_ids, Sequence)
            or isinstance(fact_ids, (str, bytes))
            or not fact_ids
            or not all(isinstance(fact_id, str) and fact_id for fact_id in fact_ids)
        ):
            raise ProvenanceError("SEC source relation evidence is invalid")
        evidence_records.add(record_id)
        for fact_id in fact_ids:
            identity = (record_id, fact_id)
            fact = fact_index.get(record_id, {}).get(fact_id)
            if fact is None or identity in referenced:
                raise ProvenanceError("SEC source relation evidence fact is invalid")
            evidence.append(
                SourceEvidence(
                    record_id=record_id,
                    evidence_quote=fact.evidence_quote,
                    char_start=fact.char_start,
                    char_end=fact.char_end,
                    source_sha256=fact.source_sha256,
                    fact_ids=(fact_id,),
                )
            )
            referenced.add(identity)
    if evidence_records != {source_id, target_id}:
        raise ProvenanceError("SEC source relation evidence omits an endpoint")
    grounded_fields = {
        (record_id, fact_index[record_id][fact_id].field)
        for record_id, fact_id in referenced
    }
    if required_fields is not None and grounded_fields != {
        (record_id, field)
        for record_id in (source_id, target_id)
        for field in required_fields
    }:
        raise ProvenanceError("SEC source relation evidence facts are incomplete")
    return SourceRelation(
        relation_id=relation_id,
        kind=kind,
        source_record_id=source_id,
        target_record_id=target_id,
        evidence=tuple(
            sorted(
                evidence,
                key=lambda item: (
                    item.source_sha256,
                    item.char_start,
                    item.fact_ids,
                ),
            )
        ),
    )


def _validate_relation_set(relations: list[SourceRelation]) -> None:
    relation_ids: set[str] = set()
    edges: set[tuple[str, str, str]] = set()
    for relation in relations:
        edge = (
            relation.kind,
            relation.source_record_id,
            relation.target_record_id,
        )
        if relation.relation_id in relation_ids:
            raise ProvenanceError("duplicate source relation id")
        if edge in edges:
            raise ProvenanceError("duplicate relation edge")
        relation_ids.add(relation.relation_id)
        edges.add(edge)


def _record_signature(record: SourceRecord) -> dict[str, Any]:
    fact_signatures = [
        {
            "field": fact.field,
            "value": fact.value,
            "evidence_quote": fact.evidence_quote,
            "char_start": fact.char_start,
            "char_end": fact.char_end,
            "source_sha256": fact.source_sha256,
        }
        for fact in record.facts
    ]
    return {
        "kind": record.kind,
        "occurred_at": record.occurred_at,
        "source_url": record.source_url,
        "retrieval_url": record.retrieval_url,
        "source_family": record.source_family,
        "source_origin": record.source_origin.value,
        "provenance_id": record.provenance_id,
        "source_sha256": record.source_sha256,
        "text_sha256": record.text_sha256,
        "facts": sorted(
            fact_signatures,
            key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False),
        ),
    }


def _relation_signature(
    relation: SourceRelation, records: Mapping[str, SourceRecord]
) -> dict[str, Any]:
    evidence_signatures = [
        {
            "source_sha256": item.source_sha256,
            "evidence_quote": item.evidence_quote,
            "char_start": item.char_start,
            "char_end": item.char_end,
        }
        for item in relation.evidence
    ]
    return {
        "kind": relation.kind,
        "source_sha256": records[relation.source_record_id].source_sha256,
        "target_sha256": records[relation.target_record_id].source_sha256,
        "evidence": sorted(
            evidence_signatures,
            key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False),
        ),
    }


def _component_digest(
    source_kind: str,
    records: Sequence[SourceRecord],
    relations: Sequence[SourceRelation],
) -> str:
    records_by_id = {record.record_id: record for record in records}
    payload = {
        "source_kind": source_kind,
        "records": sorted(
            (_record_signature(record) for record in records),
            key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False),
        ),
        "relations": sorted(
            (_relation_signature(relation, records_by_id) for relation in relations),
            key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False),
        ),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _normalize_components(
    *,
    source_kind: str,
    source_origin: SourceOrigin,
    records: list[SourceRecord],
    relations: list[SourceRelation],
    allow_relationless_singleton: bool = False,
) -> tuple[SourceWorkflow, ...]:
    if len({record.record_id for record in records}) != len(records):
        raise ProvenanceError("duplicate source workflow record id")
    provenance_ids = [record.provenance_id for record in records]
    if len(set(provenance_ids)) != len(provenance_ids):
        raise ProvenanceError("duplicate record provenance")
    _validate_relation_set(relations)
    records_by_id = {record.record_id: record for record in records}
    adjacency: dict[str, set[str]] = {record_id: set() for record_id in records_by_id}
    for relation in relations:
        if (
            relation.source_record_id not in records_by_id
            or relation.target_record_id not in records_by_id
        ):
            raise ProvenanceError("source relation references an unknown record")
        adjacency[relation.source_record_id].add(relation.target_record_id)
        adjacency[relation.target_record_id].add(relation.source_record_id)
        for evidence in relation.evidence:
            if evidence.record_id in records_by_id:
                adjacency[relation.source_record_id].add(evidence.record_id)
                adjacency[evidence.record_id].add(relation.source_record_id)
    disconnected = sorted(
        record_id for record_id, edges in adjacency.items() if not edges
    )
    if disconnected and (len(records) > 1 or not allow_relationless_singleton):
        raise ProvenanceError("source inventory contains a disconnected record")

    components: list[set[str]] = []
    unseen = set(records_by_id)
    while unseen:
        root = min(unseen)
        selected: set[str] = set()
        queue = deque([root])
        while queue:
            record_id = queue.popleft()
            if record_id in selected:
                continue
            selected.add(record_id)
            queue.extend(sorted(adjacency[record_id] - selected))
        unseen -= selected
        components.append(selected)

    workflows: list[SourceWorkflow] = []
    for component in components:
        component_records = sorted(
            (records_by_id[record_id] for record_id in component),
            key=lambda record: (
                record.occurred_at,
                record.source_sha256,
                record.kind,
            ),
        )
        component_relations = [
            relation
            for relation in relations
            if relation.source_record_id in component
            and relation.target_record_id in component
        ]
        if any(
            (relation.source_record_id in component)
            != (relation.target_record_id in component)
            for relation in relations
        ):
            raise ProvenanceError("source relation crosses normalized components")
        component_relations.sort(
            key=lambda relation: json.dumps(
                _relation_signature(relation, records_by_id),
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        digest = _component_digest(source_kind, component_records, component_relations)
        workflows.append(
            SourceWorkflow(
                workflow_id=f"source:{source_kind}:{digest[:24]}",
                component_digest=digest,
                source_kind=source_kind,
                target_domain=_SOURCE_KIND_DOMAINS[source_kind],
                source_origin=source_origin,
                source_families=tuple(
                    sorted({record.source_family for record in component_records})
                ),
                provenance_ids=tuple(
                    sorted(record.provenance_id for record in component_records)
                ),
                records=tuple(component_records),
                relations=tuple(component_relations),
            )
        )
    return tuple(sorted(workflows, key=lambda workflow: workflow.workflow_id))


def _check_schema(
    manifest: Mapping[str, Any], *, source_kind: str, expected_schemas: frozenset[str]
) -> None:
    if manifest.get("schema_version") not in expected_schemas:
        raise ProvenanceError(
            f"source kind {source_kind!r} does not match manifest schema"
        )


def adapt_sec_manifest(
    manifest: Mapping[str, Any],
    *,
    signed_bundle_authorized: bool = False,
    adapter_revision: str = SOURCE_WORKFLOW_ADAPTER_REVISION_V1,
) -> tuple[SourceWorkflow, ...]:
    """Normalize an already-verified SEC source inventory into graph components."""
    _check_schema(
        manifest,
        source_kind=SEC_SOURCE_KIND,
        expected_schemas=_SOURCE_KIND_SCHEMAS[SEC_SOURCE_KIND],
    )
    origin = _inventory_origin(
        manifest,
        source_kind=SEC_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    if adapter_revision not in SOURCE_WORKFLOW_ADAPTER_REVISIONS:
        raise ProvenanceError("unsupported source workflow adapter revision")
    raw_filings = _objects(manifest.get("filings"), "SEC records")
    if manifest.get("n") != len(raw_filings):
        raise ProvenanceError("SEC source inventory count is invalid")
    records = [
        _record(
            raw,
            kind="sec_filing",
            occurred_at=str(raw.get("filing_date") or ""),
            source_family=(
                "issuer_gcs_merged_filing_v2"
                if raw.get("parser") == "issuer_gcs_merged_html@2"
                else "issuer_gcs_merged_filing"
                if raw.get("parser") == "issuer_gcs_merged_html@1"
                else "sec_edgar_submission"
            ),
            source_origin=origin,
            identity_fields=(
                "accession",
                "cik",
                "form",
                "filing_date",
                "report_date",
                *(
                    ("parser",)
                    if adapter_revision == SOURCE_WORKFLOW_ADAPTER_REVISION_V2
                    else ()
                ),
            ),
            require_facts=True,
            fact_binding_field="source_sha256",
        )
        for raw in raw_filings
    ]
    records_by_id = {record.record_id: record for record in records}
    allow_singleton = adapter_revision == SOURCE_WORKFLOW_ADAPTER_REVISION_V2
    raw_relations = _objects(
        manifest.get("filing_relations"),
        "SEC relations",
        allow_empty=allow_singleton,
    )
    relations = [_sec_relation(raw, records=records_by_id) for raw in raw_relations]
    return _normalize_components(
        source_kind=SEC_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
        allow_relationless_singleton=allow_singleton,
    )


def adapt_issuer_ir_manifest(
    manifest: Mapping[str, Any], *, signed_bundle_authorized: bool = False
) -> tuple[SourceWorkflow, ...]:
    """Normalize verified issuer-owned annual filing history."""
    _check_schema(
        manifest,
        source_kind=ISSUER_IR_SOURCE_KIND,
        expected_schemas=_SOURCE_KIND_SCHEMAS[ISSUER_IR_SOURCE_KIND],
    )
    origin = _inventory_origin(
        manifest,
        source_kind=ISSUER_IR_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    raw_records = _objects(manifest.get("records"), "issuer IR records")
    if manifest.get("n") != len(raw_records):
        raise ProvenanceError("issuer IR source inventory count is invalid")
    records = [
        _record(
            raw,
            kind="issuer_ir_filing",
            occurred_at=str(raw.get("filing_date") or ""),
            source_family=ISSUER_IR_SOURCE_FAMILY,
            source_origin=origin,
            identity_fields=(
                "filing_id",
                "issuer_name",
                "cik",
                "form",
                "filing_date",
                "report_date",
            ),
            require_facts=True,
            fact_binding_field="source_sha256",
        )
        for raw in raw_records
    ]
    records_by_id = {record.record_id: record for record in records}
    relations = [
        _document_relation(
            raw,
            records=records_by_id,
            allowed_kinds={"prior_available_annual_filing"},
        )
        for raw in _objects(manifest.get("relations"), "issuer IR relations")
    ]
    return _normalize_components(
        source_kind=ISSUER_IR_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
    )


def adapt_issuer_official_pdf_manifest(
    manifest: Mapping[str, Any], *, signed_bundle_authorized: bool = False
) -> tuple[SourceWorkflow, ...]:
    """Normalize replay-audited issuer-owned annual-report PDFs."""
    _check_schema(
        manifest,
        source_kind=ISSUER_OFFICIAL_PDF_SOURCE_KIND,
        expected_schemas=_SOURCE_KIND_SCHEMAS[ISSUER_OFFICIAL_PDF_SOURCE_KIND],
    )
    origin = _inventory_origin(
        manifest,
        source_kind=ISSUER_OFFICIAL_PDF_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    raw_records = _objects(
        manifest.get("adapted_records"), "issuer official PDF records"
    )
    if manifest.get("n") != len(raw_records):
        raise ProvenanceError("issuer official PDF inventory count is invalid")
    records = [
        _record(
            raw,
            kind="issuer_official_annual_report",
            occurred_at=str(raw.get("occurred_at") or ""),
            source_family=str(raw.get("source_family") or ""),
            source_origin=origin,
            identity_fields=("year", "sections_json"),
            require_facts=True,
        )
        for raw in raw_records
    ]
    records_by_id = {record.record_id: record for record in records}
    relations: list[SourceRelation] = []
    for raw in _objects(
        manifest.get("adapted_relations"), "issuer official PDF relations"
    ):
        relation_id = str(raw.get("relation_id") or "")
        kind = str(raw.get("kind") or "")
        source_id = str(raw.get("source_record_id") or "")
        target_id = str(raw.get("target_record_id") or "")
        source = records_by_id.get(source_id)
        target = records_by_id.get(target_id)
        if (
            kind != "prior_official_annual_report"
            or source is None
            or target is None
            or int(source.attribute("year")) != int(target.attribute("year")) + 1
            or relation_id != f"{source_id}:prior_official:{target_id}"
        ):
            raise ProvenanceError("issuer official PDF relation identity is invalid")
        evidence: list[SourceEvidence] = []
        referenced: set[tuple[str, str]] = set()
        raw_evidence = _objects(
            raw.get("evidence"), "issuer official PDF relation evidence"
        )
        if {str(item.get("record_id") or "") for item in raw_evidence} != {
            source_id,
            target_id,
        }:
            raise ProvenanceError("issuer official PDF relation lacks endpoint evidence")
        for item in raw_evidence:
            record_id = str(item.get("record_id") or "")
            record = records_by_id[record_id]
            fact_ids = item.get("fact_ids")
            if not isinstance(fact_ids, list) or set(fact_ids) != {
                fact.fact_id for fact in record.facts
            }:
                raise ProvenanceError(
                    "issuer official PDF relation evidence is incomplete"
                )
            facts_by_id = {fact.fact_id: fact for fact in record.facts}
            for fact_id in fact_ids:
                identity = (record_id, fact_id)
                if identity in referenced:
                    raise ProvenanceError(
                        "issuer official PDF relation evidence is duplicated"
                    )
                fact = facts_by_id[fact_id]
                evidence.append(
                    SourceEvidence(
                        record_id=record_id,
                        evidence_quote=fact.evidence_quote,
                        char_start=fact.char_start,
                        char_end=fact.char_end,
                        source_sha256=fact.source_sha256,
                        fact_ids=(fact_id,),
                    )
                )
                referenced.add(identity)
        relations.append(
            SourceRelation(
                relation_id=relation_id,
                kind=kind,
                source_record_id=source_id,
                target_record_id=target_id,
                evidence=tuple(evidence),
            )
        )
    return _normalize_components(
        source_kind=ISSUER_OFFICIAL_PDF_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
    )


def adapt_paper_manifest(
    manifest: Mapping[str, Any], *, signed_bundle_authorized: bool = False
) -> tuple[SourceWorkflow, ...]:
    """Normalize an already-verified paper revision/review inventory."""
    if manifest.get("schema_version") == ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA:
        return adapt_elife_review_revision_manifest(
            manifest, signed_bundle_authorized=signed_bundle_authorized
        )
    _check_schema(
        manifest,
        source_kind=PAPER_SOURCE_KIND,
        expected_schemas=_SOURCE_KIND_SCHEMAS[PAPER_SOURCE_KIND],
    )
    origin = _inventory_origin(
        manifest,
        source_kind=PAPER_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    raw_records = _objects(manifest.get("records"), "paper records")
    if manifest.get("n") != len(raw_records):
        raise ProvenanceError("paper source inventory count is invalid")
    records = [
        _record(
            raw,
            kind=str(raw.get("role") or ""),
            occurred_at=str(raw.get("occurred_at") or ""),
            source_family=str(raw.get("source_family") or ""),
            source_origin=origin,
            identity_fields=("work_id", "role", "revision_id"),
        )
        for raw in raw_records
    ]
    records_by_id = {record.record_id: record for record in records}
    raw_relations = _objects(manifest.get("relations"), "paper relations")
    if manifest.get("n_relations") != len(raw_relations):
        raise ProvenanceError("paper source relation count is invalid")
    relations = [
        _document_relation(
            raw,
            records=records_by_id,
            allowed_kinds=set(_PAPER_RELATION_ROLES),
        )
        for raw in raw_relations
    ]
    for relation in relations:
        source = records_by_id[relation.source_record_id]
        target = records_by_id[relation.target_record_id]
        expected_roles = _PAPER_RELATION_ROLES[relation.kind]
        if (
            source.attribute("work_id") != target.attribute("work_id")
            or (source.kind, target.kind) != expected_roles
        ):
            raise ProvenanceError("paper source relation crosses workflow boundaries")
    return _normalize_components(
        source_kind=PAPER_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
    )


def _elife_relation(
    raw: Mapping[str, Any], *, records: Mapping[str, SourceRecord]
) -> SourceRelation:
    relation_id = str(raw.get("relation_id") or "")
    kind = str(raw.get("kind") or "")
    source_id = str(raw.get("source_record_id") or "")
    target_id = str(raw.get("target_record_id") or "")
    raw_evidence = raw.get("evidence")
    if (
        set(raw)
        != {
            "relation_id",
            "kind",
            "source_record_id",
            "target_record_id",
            "evidence",
        }
        or not relation_id
        or kind not in _ELIFE_RELATIONS
        or source_id == target_id
        or source_id not in records
        or target_id not in records
        or not isinstance(raw_evidence, list)
        or not raw_evidence
    ):
        raise ProvenanceError("eLife source relation identity is invalid")
    evidence: list[SourceEvidence] = []
    evidence_ids: set[str] = set()
    for item in raw_evidence:
        if not isinstance(item, Mapping) or set(item) != {
            "evidence_id",
            "record_id",
            "evidence_quote",
            "evidence_char_start",
            "evidence_char_end",
            "source_sha256",
            "text_sha256",
        }:
            raise ProvenanceError("eLife source relation evidence is invalid")
        evidence_id = str(item.get("evidence_id") or "")
        record_id = str(item.get("record_id") or "")
        quote = str(item.get("evidence_quote") or "")
        start = item.get("evidence_char_start")
        end = item.get("evidence_char_end")
        record = records.get(record_id)
        if (
            not evidence_id
            or evidence_id in evidence_ids
            or record is None
            or not quote
            or not isinstance(start, int)
            or not isinstance(end, int)
            or end != start + len(quote)
            or record.text[start:end] != quote
            or item.get("source_sha256") != record.source_sha256
            or item.get("text_sha256") != record.text_sha256
        ):
            raise ProvenanceError("eLife source relation evidence is invalid")
        evidence_ids.add(evidence_id)
        evidence.append(
            SourceEvidence(
                record_id=record_id,
                evidence_quote=quote,
                char_start=start,
                char_end=end,
                source_sha256=record.source_sha256,
                fact_ids=(evidence_id,),
            )
        )
    if {source_id, target_id} - {item.record_id for item in evidence}:
        raise ProvenanceError("eLife source relation lacks endpoint evidence")
    expected_orientation = {
        "requests_revision": ("v1", "v2"),
        "responds_to_review": ("v2", "v1"),
        "revision_of": ("v2", "v1"),
        "implements_revision_delta": ("v2", "v1"),
    }[kind]
    endpoints = (
        records[source_id].attribute("revision_id"),
        records[target_id].attribute("revision_id"),
    )
    if endpoints != expected_orientation:
        raise ProvenanceError("eLife source relation endpoint roles are invalid")
    return SourceRelation(
        relation_id=relation_id,
        kind=kind,
        source_record_id=source_id,
        target_record_id=target_id,
        evidence=tuple(evidence),
    )


def adapt_elife_review_revision_manifest(
    manifest: Mapping[str, Any], *, signed_bundle_authorized: bool = False
) -> tuple[SourceWorkflow, ...]:
    """Normalize a verified eLife review-response-revision inventory."""
    _check_schema(
        manifest,
        source_kind=PAPER_SOURCE_KIND,
        expected_schemas=frozenset({ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA}),
    )
    origin = _inventory_origin(
        manifest,
        source_kind=PAPER_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    audit = audit_elife_review_revision_task(dict(manifest))
    if audit.get("passed") is not True:
        raise ProvenanceError("eLife strict source task replay failed")
    raw_records = _objects(manifest.get("records"), "eLife records")
    if manifest.get("n_records") != len(raw_records):
        raise ProvenanceError("eLife source inventory count is invalid")
    records = [
        _record(
            raw,
            kind="elife_revision",
            occurred_at=str(raw.get("effective_date") or ""),
            source_family="elife_article_xml",
            source_origin=origin,
            identity_fields=(
                "publisher_id",
                "canonical_doi",
                "version_doi",
                "version",
                "revision_id",
            ),
        )
        for raw in raw_records
    ]
    records_by_id = {record.record_id: record for record in records}
    raw_relations = _objects(manifest.get("relations"), "eLife relations")
    if manifest.get("n_relations") != len(raw_relations):
        raise ProvenanceError("eLife source relation count is invalid")
    relations = [_elife_relation(raw, records=records_by_id) for raw in raw_relations]
    if {relation.kind for relation in relations} != _ELIFE_RELATIONS:
        raise ProvenanceError("eLife source relation set is incomplete")
    return _normalize_components(
        source_kind=PAPER_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
    )


def adapt_wikimedia_manifest(
    manifest: Mapping[str, Any], *, signed_bundle_authorized: bool = False
) -> tuple[SourceWorkflow, ...]:
    """Normalize an already-verified Wikipedia/Wikidata source inventory."""
    _check_schema(
        manifest,
        source_kind=WIKIMEDIA_SOURCE_KIND,
        expected_schemas=_SOURCE_KIND_SCHEMAS[WIKIMEDIA_SOURCE_KIND],
    )
    origin = _inventory_origin(
        manifest,
        source_kind=WIKIMEDIA_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    raw_records = _objects(manifest.get("records"), "Wikimedia records")
    if manifest.get("n") != len(raw_records):
        raise ProvenanceError("Wikimedia source inventory count is invalid")
    records = [
        _record(
            raw,
            kind=str(raw.get("kind") or ""),
            occurred_at=str(raw.get("occurred_at") or ""),
            source_family=str(raw.get("kind") or ""),
            source_origin=origin,
            identity_fields=(
                "kind",
                "revision_id",
                "page_id",
                "title",
                "parent_revision_id",
                "entity_id",
            ),
        )
        for raw in raw_records
    ]
    records_by_id = {record.record_id: record for record in records}
    raw_relations = _objects(manifest.get("relations"), "Wikimedia relations")
    if manifest.get("n_relations") != len(raw_relations):
        raise ProvenanceError("Wikimedia source relation count is invalid")
    relations = [
        _document_relation(
            raw,
            records=records_by_id,
            allowed_kinds=set(_WIKIMEDIA_RELATIONS),
        )
        for raw in raw_relations
    ]
    for relation in relations:
        source = records_by_id[relation.source_record_id]
        target = records_by_id[relation.target_record_id]
        if relation.kind == "revision_of":
            valid = (
                source.kind == target.kind == "wikipedia_revision"
                and source.attribute("page_id") == target.attribute("page_id")
                and source.attribute("parent_revision_id")
                == target.attribute("revision_id")
            )
        elif relation.kind == "page_describes_entity":
            valid = (
                source.kind == "wikipedia_revision"
                and target.kind == "wikidata_entity_revision"
            )
        else:
            valid = (
                source.kind == "wikidata_entity_revision"
                and target.kind == "wikipedia_revision"
            )
        if not valid:
            raise ProvenanceError(
                "Wikimedia source relation crosses workflow boundaries"
            )
    return _normalize_components(
        source_kind=WIKIMEDIA_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
    )


def adapt_standards_manifest(
    manifest: Mapping[str, Any], *, signed_bundle_authorized: bool = False
) -> tuple[SourceWorkflow, ...]:
    """Normalize a verified IETF revision/publication graph for standalone replay."""
    _check_schema(
        manifest,
        source_kind=STANDARDS_SOURCE_KIND,
        expected_schemas=_SOURCE_KIND_SCHEMAS[STANDARDS_SOURCE_KIND],
    )
    origin = _inventory_origin(
        manifest,
        source_kind=STANDARDS_SOURCE_KIND,
        signed_bundle_authorized=signed_bundle_authorized,
    )
    raw_primary = _objects(manifest.get("records"), "IETF records")
    raw_supporting = _objects(
        manifest.get("supporting_records"), "IETF supporting records"
    )
    raw_relations = _objects(manifest.get("relations"), "IETF relations")
    if (
        manifest.get("n") != len(raw_primary)
        or manifest.get("n_supporting_records") != len(raw_supporting)
        or manifest.get("n_relations") != len(raw_relations)
    ):
        raise ProvenanceError("IETF source inventory count is invalid")
    referenced_evidence_ids = {
        str(item.get("record_id") or "")
        for relation in raw_relations
        for item in _objects(relation.get("evidence"), "IETF relation evidence")
    }
    selected_raw_records = [
        *raw_primary,
        *(
            record
            for record in raw_supporting
            if str(record.get("record_id") or "") in referenced_evidence_ids
        ),
    ]
    raw_records_by_id = {
        str(record.get("record_id") or ""): record for record in selected_raw_records
    }
    records: list[SourceRecord] = []
    for raw in selected_raw_records:
        transformed = dict(raw)
        grounded_facts = [
            {
                **dict(fact),
                "evidence_char_start": fact.get("char_start"),
                "text_sha256": raw.get("text_sha256"),
            }
            for fact in _objects(raw.get("facts"), "IETF facts")
            if str(fact.get("value") or "") in str(fact.get("evidence_quote") or "")
        ]
        if grounded_facts:
            transformed["derived_facts"] = grounded_facts
        else:
            transformed.pop("derived_facts", None)
        records.append(
            _record(
                transformed,
                kind=str(raw.get("kind") or ""),
                occurred_at=str(raw.get("occurred_at") or ""),
                source_family="ietf_standards",
                source_origin=origin,
                identity_fields=(
                    "draft_name",
                    "revision",
                    "rfc_number",
                    "draft_references",
                ),
                require_facts=str(raw.get("kind") or "") in {"draft_revision", "rfc"},
            )
        )
    records_by_id = {record.record_id: record for record in records}
    relations: list[SourceRelation] = []
    allowed_kinds = {
        "revision_of",
        "published_as",
        "updates",
        "obsoletes",
        "normative_reference",
        "informative_reference",
    }
    for raw in raw_relations:
        relation_id = str(raw.get("relation_id") or "")
        kind = str(raw.get("kind") or "")
        source_id = str(raw.get("source_record_id") or "")
        target_id = str(raw.get("target_record_id") or "")
        if (
            not relation_id
            or kind not in allowed_kinds
            or source_id == target_id
            or source_id not in records_by_id
            or target_id not in records_by_id
        ):
            raise ProvenanceError("IETF source relation identity is invalid")
        evidence: list[SourceEvidence] = []
        dependency_evidence: list[SourceEvidence] = []
        for item in _objects(raw.get("evidence"), "IETF relation evidence"):
            record_id = str(item.get("record_id") or "")
            record = records_by_id.get(record_id)
            quote = str(item.get("evidence_quote") or "")
            start, end = item.get("char_start"), item.get("char_end")
            fact_ids = item.get("fact_ids")
            if (
                record is None
                or not quote
                or not isinstance(start, int)
                or not isinstance(end, int)
                or end != start + len(quote)
                or record.text[start:end] != quote
                or item.get("source_sha256") != record.source_sha256
                or not isinstance(fact_ids, list)
                or len(fact_ids) != 1
                or not isinstance(fact_ids[0], str)
            ):
                raise ProvenanceError("IETF source relation evidence is invalid")
            fact = next(
                (
                    fact
                    for fact in _objects(
                        raw_records_by_id[record_id].get("facts"), "IETF facts"
                    )
                    if fact.get("fact_id") == fact_ids[0]
                ),
                None,
            )
            if (
                fact is None
                or fact.get("evidence_quote") != quote
                or fact.get("char_start") != start
                or fact.get("char_end") != end
            ):
                raise ProvenanceError("IETF source relation evidence fact is invalid")
            bound_evidence = SourceEvidence(
                record_id=record_id,
                evidence_quote=quote,
                char_start=start,
                char_end=end,
                source_sha256=record.source_sha256,
                fact_ids=(fact_ids[0],),
            )
            evidence.append(bound_evidence)
            if record.kind == "datatracker_relation":
                dependency_evidence.append(bound_evidence)
        if not {source_id, target_id}.issubset({item.record_id for item in evidence}):
            raise ProvenanceError("IETF source relation lacks endpoint evidence")
        source_kind = records_by_id[source_id].kind
        target_kind = records_by_id[target_id].kind
        expected_kinds = {
            "revision_of": ("draft_revision", "draft_revision"),
            "published_as": ("draft_revision", "rfc"),
            "updates": ("rfc", "rfc"),
            "obsoletes": ("rfc", "rfc"),
            "normative_reference": ("rfc", "rfc"),
            "informative_reference": ("rfc", "rfc"),
        }[kind]
        if (source_kind, target_kind) != expected_kinds:
            raise ProvenanceError("IETF source relation endpoint roles are invalid")
        if kind in {"normative_reference", "informative_reference"}:
            target_number = records_by_id[target_id].attribute("rfc_number")
            if (
                not target_number.isdigit()
                or len(dependency_evidence) != 1
                or dependency_evidence[0].fact_ids
                != (f"{kind}:{target_number}",)
            ):
                raise ProvenanceError("IETF source dependency evidence is invalid")
        relations.append(
            SourceRelation(
                relation_id=relation_id,
                kind=kind,
                source_record_id=source_id,
                target_record_id=target_id,
                evidence=tuple(evidence),
            )
        )
    return _normalize_components(
        source_kind=STANDARDS_SOURCE_KIND,
        source_origin=origin,
        records=records,
        relations=relations,
    )


def adapt_source_manifest(
    manifest: Mapping[str, Any],
    *,
    source_kind: str,
    signed_bundle_authorized: bool = False,
    adapter_revision: str = SOURCE_WORKFLOW_ADAPTER_REVISION_V1,
) -> tuple[SourceWorkflow, ...]:
    """Dispatch an already-verified manifest by an explicit source kind."""
    expected_schemas = _SOURCE_KIND_SCHEMAS.get(source_kind)
    if expected_schemas is None:
        raise ProvenanceError("unsupported source workflow kind")
    _check_schema(manifest, source_kind=source_kind, expected_schemas=expected_schemas)
    if source_kind == SEC_SOURCE_KIND:
        return adapt_sec_manifest(
            manifest,
            signed_bundle_authorized=signed_bundle_authorized,
            adapter_revision=adapter_revision,
        )
    if source_kind == PAPER_SOURCE_KIND:
        return adapt_paper_manifest(
            manifest, signed_bundle_authorized=signed_bundle_authorized
        )
    if source_kind == ISSUER_IR_SOURCE_KIND:
        return adapt_issuer_ir_manifest(
            manifest, signed_bundle_authorized=signed_bundle_authorized
        )
    if source_kind == ISSUER_OFFICIAL_PDF_SOURCE_KIND:
        return adapt_issuer_official_pdf_manifest(
            manifest, signed_bundle_authorized=signed_bundle_authorized
        )
    if source_kind == STANDARDS_SOURCE_KIND:
        return adapt_standards_manifest(
            manifest, signed_bundle_authorized=signed_bundle_authorized
        )
    return adapt_wikimedia_manifest(
        manifest, signed_bundle_authorized=signed_bundle_authorized
    )
