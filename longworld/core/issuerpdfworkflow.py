"""Fail-closed replay for issuer-owned annual-report PDF inventories."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlparse

import pypdf
from pypdf import PdfReader

from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _read_regular_file,
)

ISSUER_OFFICIAL_PDF_INVENTORY_SCHEMA = "longworld.issuer-official-pdf-inventory.v1"
ISSUER_OFFICIAL_PDF_SOURCE_KIND = "issuer_official_pdf"
MAX_ISSUER_OFFICIAL_PDF_MANIFEST_BYTES = 4_000_000
MAX_ISSUER_OFFICIAL_PDF_BYTES = 32_000_000
SUPPORTED_PARSER = {"name": "pypdf", "version": "6.0.0"}


def _literal_file(value: object, suffix: str) -> str:
    if not isinstance(value, str) or not value or value != PurePath(value).name:
        raise ProvenanceError("issuer official PDF inventory file is unsafe")
    if not value.endswith(suffix):
        raise ProvenanceError("issuer official PDF inventory file type is invalid")
    return value


def _extract(raw: bytes) -> tuple[str, int, int]:
    if (
        not raw.startswith(b"%PDF-")
        or len(raw) > MAX_ISSUER_OFFICIAL_PDF_BYTES
        or pypdf.__version__ != SUPPORTED_PARSER["version"]
    ):
        raise ProvenanceError("issuer official PDF or parser identity is invalid")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or not reader.pages:
            raise ProvenanceError("issuer official PDF is not extractable")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ProvenanceError:
        raise
    except Exception as error:
        raise ProvenanceError("issuer official PDF is invalid") from error
    return "\n\n".join(pages), len(pages), sum(bool(page.strip()) for page in pages)


def audit_issuer_official_pdf_inventory(
    payload: Mapping[str, Any], base_directory: Path
) -> dict[str, Any]:
    """Replay PDF extraction and return a normalized in-memory adapter payload."""
    issuer = payload.get("issuer")
    if (
        payload.get("schema_version") != ISSUER_OFFICIAL_PDF_INVENTORY_SCHEMA
        or payload.get("source_status") != "issuer_owned_official_pdf"
        or payload.get("data_stage") != "source_inventory"
        or payload.get("hybrid_train_ready") is not False
        or payload.get("production_eligible") is not False
        or payload.get("generation_integration") != "disabled"
        or payload.get("parser") != SUPPORTED_PARSER
        or not isinstance(issuer, Mapping)
        or not str(issuer.get("name") or "").strip()
        or not str(issuer.get("official_host") or "").strip()
    ):
        raise ProvenanceError("issuer official PDF inventory contract is invalid")
    records = payload.get("records")
    if (
        not isinstance(records, list)
        or not records
        or payload.get("n") != len(records)
        or len(records) > 32
    ):
        raise ProvenanceError("issuer official PDF inventory count is invalid")
    adapted_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_years: set[int] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ProvenanceError("issuer official PDF record is invalid")
        record_id = str(record.get("record_id") or "")
        source_family = str(record.get("source_family") or "")
        occurred_at = str(record.get("occurred_at") or "")
        year = record.get("year")
        url = str(record.get("url") or "")
        parsed_url = urlparse(url)
        if (
            not record_id
            or record_id in seen_ids
            or not source_family
            or isinstance(year, bool)
            or not isinstance(year, int)
            or year in seen_years
            or occurred_at != f"{year}-12-31"
            or parsed_url.scheme != "https"
            or parsed_url.hostname != issuer["official_host"]
            or record.get("content_type") != "application/pdf"
        ):
            raise ProvenanceError("issuer official PDF record identity is invalid")
        pdf_file = _literal_file(record.get("pdf_file"), ".pdf")
        text_file = _literal_file(record.get("text_file"), ".txt")
        pdf_raw = _read_regular_file(
            base_directory / pdf_file, MAX_ISSUER_OFFICIAL_PDF_BYTES
        )
        text_raw = _read_regular_file(base_directory / text_file, MAX_SOURCE_BYTES)
        if (
            hashlib.sha256(pdf_raw).hexdigest() != record.get("pdf_sha256")
            or len(pdf_raw) != record.get("pdf_bytes")
            or hashlib.sha256(text_raw).hexdigest() != record.get("text_sha256")
            or len(text_raw) != record.get("text_bytes")
        ):
            raise ProvenanceError("issuer official PDF byte receipt is invalid")
        try:
            stored_text = text_raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProvenanceError("issuer official PDF text is not UTF-8") from error
        text, page_count, nonempty_pages = _extract(pdf_raw)
        if (
            text != stored_text
            or len(text) != record.get("extracted_chars")
            or page_count != record.get("page_count")
            or nonempty_pages != record.get("nonempty_pages")
        ):
            raise ProvenanceError("issuer official PDF parser receipt is invalid")
        raw_sections = record.get("sections")
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ProvenanceError("issuer official PDF sections are invalid")
        section_ranges: dict[str, tuple[int, int]] = {}
        for section in raw_sections:
            if not isinstance(section, Mapping):
                raise ProvenanceError("issuer official PDF section is invalid")
            section_id = str(section.get("section_id") or "")
            start, end = section.get("char_start"), section.get("char_end")
            if (
                not section_id
                or section_id in section_ranges
                or isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(text)
                or hashlib.sha256(text[start:end].encode()).hexdigest()
                != section.get("sha256")
            ):
                raise ProvenanceError("issuer official PDF section receipt is invalid")
            section_ranges[section_id] = (start, end)
        raw_facts = record.get("derived_facts")
        if not isinstance(raw_facts, list) or not raw_facts:
            raise ProvenanceError("issuer official PDF facts are invalid")
        facts: list[dict[str, Any]] = []
        fact_ids: set[str] = set()
        for fact in raw_facts:
            if not isinstance(fact, Mapping):
                raise ProvenanceError("issuer official PDF fact is invalid")
            fact_id = str(fact.get("fact_id") or "")
            section_id = str(fact.get("section_id") or "")
            start, end = fact.get("evidence_char_start"), fact.get("evidence_char_end")
            quote = str(fact.get("evidence_quote") or "")
            value = str(fact.get("value") or "")
            bounds = section_ranges.get(section_id)
            if (
                not fact_id
                or fact_id in fact_ids
                or bounds is None
                or isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not bounds[0] <= start < end <= bounds[1]
                or text[start:end] != quote
                or not value
                or value not in quote
                or fact.get("text_sha256") != record.get("text_sha256")
            ):
                raise ProvenanceError("issuer official PDF fact receipt is invalid")
            facts.append(dict(fact))
            fact_ids.add(fact_id)
        adapted_records.append(
            {
                "record_id": record_id,
                "year": year,
                "kind": "issuer_official_annual_report",
                "occurred_at": occurred_at,
                "source_url": url,
                "retrieval_url": url,
                "source_family": source_family,
                "source_sha256": record["pdf_sha256"],
                "text_sha256": record["text_sha256"],
                "provenance_id": f"sha256:{record['pdf_sha256']}",
                "text": text,
                "derived_facts": facts,
                "sections_json": json.dumps(
                    [dict(section) for section in raw_sections],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        seen_ids.add(record_id)
        seen_years.add(year)
    if [record["year"] for record in adapted_records] != sorted(seen_years):
        raise ProvenanceError("issuer official PDF records are not ordered by year")

    raw_relations = payload.get("relations")
    if not isinstance(raw_relations, list) or len(raw_relations) != len(records) - 1:
        raise ProvenanceError("issuer official PDF relations are invalid")
    records_by_id = {record["record_id"]: record for record in adapted_records}
    expected_edges = {
        (current["record_id"], prior["record_id"])
        for prior, current in pairwise(adapted_records)
    }
    seen_edges: set[tuple[str, str]] = set()
    adapted_relations: list[dict[str, Any]] = []
    for relation in raw_relations:
        if not isinstance(relation, Mapping):
            raise ProvenanceError("issuer official PDF relation is invalid")
        source_id = str(relation.get("source_record_id") or "")
        target_id = str(relation.get("target_record_id") or "")
        edge = (source_id, target_id)
        source = records_by_id.get(source_id)
        target = records_by_id.get(target_id)
        if (
            relation.get("kind") != "prior_official_annual_report"
            or edge not in expected_edges
            or edge in seen_edges
            or source is None
            or target is None
            or relation.get("relation_id") != f"{source_id}:prior_official:{target_id}"
        ):
            raise ProvenanceError("issuer official PDF relation identity is invalid")
        raw_evidence = relation.get("evidence")
        if not isinstance(raw_evidence, list) or {
            item.get("record_id") if isinstance(item, Mapping) else None
            for item in raw_evidence
        } != {source_id, target_id}:
            raise ProvenanceError("issuer official PDF relation evidence is invalid")
        for item in raw_evidence:
            assert isinstance(item, Mapping)
            record = records_by_id[str(item["record_id"])]
            if item.get("fact_ids") != [
                fact["fact_id"] for fact in record["derived_facts"]
            ]:
                raise ProvenanceError(
                    "issuer official PDF relation evidence is incomplete"
                )
        adapted_relations.append(dict(relation))
        seen_edges.add(edge)
    if seen_edges != expected_edges:
        raise ProvenanceError("issuer official PDF relation sequence is incomplete")
    adapted = dict(payload)
    adapted["adapted_records"] = adapted_records
    adapted["adapted_relations"] = adapted_relations
    return adapted


def _selected_pdf_section_record(
    *, event: Any, artifact: Any, workflow_id: str, record: Any, world_id: str
) -> bool:
    from longworld.core.render import semantic_attestation_valid

    params = getattr(event, "params", None)
    slots = getattr(artifact, "slots", None) or {}
    classification = slots.get("classification")
    if not isinstance(params, Mapping) or not isinstance(classification, Mapping):
        return False
    start, end = params.get("source_char_start"), params.get("source_char_end")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > len(record.text)
    ):
        return False
    raw_section = record.text[start:end]
    raw_sha256 = hashlib.sha256(raw_section.encode()).hexdigest()
    operation = str(params.get("provenance_operation") or "")
    expected_provenance = (
        "derived-sha256:"
        + hashlib.sha256(
            (f"{operation}|{record.provenance_id}|{start}|{end}|{raw_sha256}").encode()
        ).hexdigest()
    )
    text = str(params.get("text") or "")
    return (
        bool(operation)
        and params.get("workflow_id") == workflow_id
        and params.get("record_id") == record.record_id
        and params.get("source_sha256") == record.source_sha256
        and params.get("source_family") == record.source_family
        and params.get("source_url") == record.source_url
        and params.get("retrieval_url") == record.retrieval_url
        and params.get("parent_provenance_id") == record.provenance_id
        and params.get("section_sha256") == raw_sha256
        and params.get("text_sha256") == hashlib.sha256(text.encode()).hexdigest()
        and params.get("provenance_id") == expected_provenance
        and text.count(raw_section) == 1
        and list(getattr(event, "visibility", ())) == [event.id]
        and slots.get("real_workflow_record") is True
        and slots.get("source_workflow_id") == workflow_id
        and slots.get("source_record_id") == record.record_id
        and slots.get("event_type") == event.type
        and slots.get("params") == params
        and classification.get("source_origin")
        in {"real_public", "real_private_export", "real_derived"}
        and getattr(artifact, "artifact_id", "") == f"{world_id}.{event.id}"
        and getattr(artifact, "doc_type", "") == event.type
        and getattr(artifact, "time", None) == event.time
        and str(getattr(artifact, "text", "")).count(text) == 1
        and semantic_attestation_valid(artifact)
    )


def selected_issuer_official_pdf_relation_edges(
    world: Any, spec: Any, artifacts: Sequence[Any]
) -> list[dict[str, str]]:
    """Return signed official-PDF relations with exact rendered endpoints."""
    from longworld.core.render import semantic_attestation_valid

    visible_event_ids = {
        event_id
        for artifact in artifacts
        for event_id in getattr(artifact, "reveals_events", ())
    }
    sufficient_ids = set(getattr(spec, "sufficient_event_ids", ()) or ())
    if sufficient_ids:
        visible_event_ids &= sufficient_ids
    event_index = {
        event.id: event
        for event in getattr(world, "events", ())
        if event.id in visible_event_ids
    }
    project = getattr(world, "spec", {}).get("project")
    if not isinstance(project, Mapping):
        return []
    workflow_matches: dict[str, list[Any]] = {}
    for workflow in project.get("source_workflows") or ():
        if getattr(workflow, "source_kind", "") != ISSUER_OFFICIAL_PDF_SOURCE_KIND:
            continue
        workflow_matches.setdefault(
            str(getattr(workflow, "workflow_id", "")), []
        ).append(workflow)
    workflows = {
        workflow_id: matches[0]
        for workflow_id, matches in workflow_matches.items()
        if workflow_id and len(matches) == 1
    }
    selected_events: dict[str, tuple[str, str]] = {}
    world_id = str(getattr(world, "spec", {}).get("world_id") or "")
    relation_artifacts: set[str] = set()
    for artifact in artifacts:
        slots = getattr(artifact, "slots", None) or {}
        workflow_id = str(slots.get("source_workflow_id") or "")
        workflow = workflows.get(workflow_id)
        revealed = [
            event_index[event_id]
            for event_id in getattr(artifact, "reveals_events", ())
            if event_id in event_index
        ]
        if workflow is None or len(revealed) != 1:
            continue
        event = revealed[0]
        if event.type == "issuer_official_pdf_prior_annual_relation":
            expected_text = (
                "Issuer official annual-report temporal relation control\n"
                f"Current official record: {event.params.get('record_id')}.\n"
                f"Prior official record: {event.params.get('target_record_id')}.\n"
                f"Signed relation: {event.params.get('source_relation_id')}.\n"
                "Status: prior-official-annual-report-validated."
            )
            if (
                slots.get("real_workflow_record") is False
                and slots.get("event_type") == event.type
                and slots.get("params") == event.params
                and getattr(artifact, "artifact_id", "") == f"{world_id}.{event.id}"
                and getattr(artifact, "doc_type", "") == event.type
                and getattr(artifact, "time", None) == event.time
                and str(getattr(artifact, "text", "")).count(expected_text) == 1
                and semantic_attestation_valid(artifact)
            ):
                relation_artifacts.add(event.id)
            continue
        record_id = str(slots.get("source_record_id") or "")
        record_matches = [
            record
            for record in getattr(workflow, "records", ())
            if record.record_id == record_id
        ]
        if len(record_matches) == 1 and _selected_pdf_section_record(
            event=event,
            artifact=artifact,
            workflow_id=workflow_id,
            record=record_matches[0],
            world_id=world_id,
        ):
            selected_events[event.id] = (workflow_id, record_id)

    edges: list[dict[str, str]] = []
    for event in event_index.values():
        if (
            event.type != "issuer_official_pdf_prior_annual_relation"
            or event.id not in relation_artifacts
        ):
            continue
        workflow_id = str(event.params.get("workflow_id") or "")
        workflow = workflows.get(workflow_id)
        authorization = getattr(workflow, "source_authorization", None)
        if (
            workflow is None
            or not isinstance(authorization, Mapping)
            or authorization.get("source_kind") != ISSUER_OFFICIAL_PDF_SOURCE_KIND
            or authorization.get("workflow_id") != workflow_id
            or authorization.get("component_digest") != workflow.component_digest
            or not isinstance(authorization.get("attestation"), Mapping)
        ):
            continue
        relation_matches = [
            relation
            for relation in getattr(workflow, "relations", ())
            if relation.relation_id == event.params.get("source_relation_id")
            and relation.kind == "prior_official_annual_report"
            and relation.kind == event.params.get("relation_kind")
            and relation.source_record_id == event.params.get("record_id")
            and relation.target_record_id == event.params.get("target_record_id")
        ]
        if len(relation_matches) != 1:
            continue
        relation = relation_matches[0]
        parent_records = {
            selected_events[parent_id]
            for parent_id in event.required_inputs
            if parent_id in selected_events
        }
        if (
            set(event.required_inputs) != set(event.causal_inputs)
            or len(event.required_inputs) != 2
            or parent_records
            != {
                (workflow_id, relation.source_record_id),
                (workflow_id, relation.target_record_id),
            }
        ):
            continue
        records = {
            record.record_id: record for record in getattr(workflow, "records", ())
        }
        source = records.get(relation.source_record_id)
        target = records.get(relation.target_record_id)
        if source is None or target is None:
            continue
        edges.append(
            {
                "parent_record_id": target.record_id,
                "child_record_id": source.record_id,
                "relation": relation.kind,
                "relation_provenance": "authentic_source",
                "parent_source_url": target.source_url,
                "child_source_url": source.source_url,
            }
        )
    return sorted(
        edges,
        key=lambda edge: (
            edge["parent_record_id"],
            edge["child_record_id"],
            edge["relation"],
        ),
    )
