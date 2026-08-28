"""Auditable ingestion contracts for already-downloaded SEC filing sources.

This module does not fetch EDGAR and does not feed production generation.  It
binds local source bytes, SEC identifiers, and source-grounded derived facts in
an attested manifest that a later integration can consume explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.attestation import (
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

SEC_FILING_INPUT_SCHEMA = "longworld.sec-filing-input.v1"
SEC_FILING_MANIFEST_SCHEMA = "longworld.sec-filing-manifest.v1"
SEC_SCANNER = "longworld-public-secret-patterns"
SEC_SCANNER_REVISION = "v2"
MAX_SEC_FILINGS = 512
MAX_SEC_MANIFEST_BYTES = 32_000_000
SEC_FILING_COMPONENT_REVISION = "sec-sgml-component-v1"

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_CIK = re.compile(r"^\d{10}$")
_FORM = re.compile(r"^[A-Z0-9-]+(?:/A)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])")
_SECRET_PATTERNS = (
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SOURCE_STATUSES = {
    "test_fixture",
    "public_sec_download",
    "authorized_download",
}
_SEC_FAIR_ACCESS_URL = (
    "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data"
)
SEC_IDENTITY_FIELDS = ("accession", "form", "filing_date", "report_date")
SEC_REQUIRED_COMPONENT_TYPES = ("10-K", "EX-31.1", "EX-31.2", "EX-32.1")
SEC_OPTIONAL_COMPONENT_TYPES = ("EX-32.2",)
SEC_EXTRACTABLE_COMPONENT_TYPES = (
    *SEC_REQUIRED_COMPONENT_TYPES,
    *SEC_OPTIONAL_COMPONENT_TYPES,
)
SEC_HYBRID_CHILD_EVENT_TYPES = frozenset(
    {
        "sec_filing_eligibility_policy",
        "sec_filing_approval",
        "sec_amendment_resolution",
        "sec_filing_publication_ratification",
        "sec_financial_answer",
    }
)

_SEC_DOCUMENT_BLOCK = re.compile(
    r"^<DOCUMENT>[^\S\n]*\n.*?^</DOCUMENT>[^\S\n]*(?:\n|$)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class SecFilingComponent:
    """Hash-bound coordinates for one component of an SEC submission.

    Component text deliberately remains in the parent submission. Consumers use the
    validated coordinates to take a view without storing or serializing a second copy.
    """

    component_type: str
    sequence: int
    filename: str
    char_start: int
    char_end: int
    parent_source_sha256: str
    component_sha256: str
    provenance_id: str


def _payload_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _payload_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _payload_strings(item)


def _reject_secrets(text: str) -> None:
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        raise ProvenanceError("source text contains a credential-shaped secret")


def _sanitize_source_text(text: str) -> tuple[str, int]:
    _reject_secrets(text)
    return _EMAIL.sub("[redacted-email]", text), len(_EMAIL.findall(text))


def _sec_component_header(block: str) -> tuple[str, int, str]:
    text_marker = re.search(r"(?m)^<TEXT>[^\S\n]*$", block)
    if text_marker is None:
        raise ProvenanceError("SEC filing component has no TEXT boundary")
    header = block[: text_marker.start()]

    def value(tag: str) -> str:
        matches = re.findall(
            rf"(?m)^<{re.escape(tag)}>[ \t]*([^\r\n<]+?)[ \t\r]*$", header
        )
        if len(matches) != 1 or not matches[0].strip():
            raise ProvenanceError(f"SEC filing component has invalid {tag}")
        return matches[0].strip()

    component_type = value("TYPE")
    raw_sequence = value("SEQUENCE")
    filename = value("FILENAME")
    if not raw_sequence.isdigit() or int(raw_sequence) <= 0:
        raise ProvenanceError("SEC filing component has invalid SEQUENCE")
    if Path(filename).name != filename or Path(filename).is_absolute():
        raise ProvenanceError("SEC filing component has unsafe FILENAME")
    return component_type, int(raw_sequence), filename


def _sec_component_provenance(
    *,
    component_type: str,
    sequence: int,
    filename: str,
    char_start: int,
    char_end: int,
    parent_source_sha256: str,
    component_sha256: str,
) -> str:
    payload = {
        "operation": SEC_FILING_COMPONENT_REVISION,
        "component_type": component_type,
        "sequence": sequence,
        "filename": filename,
        "char_start": char_start,
        "char_end": char_end,
        "parent_source_sha256": parent_source_sha256,
        "component_sha256": component_sha256,
    }
    return (
        "derived-sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def validate_sec_filing_component(
    source_text: str, component: SecFilingComponent
) -> None:
    """Validate component coordinates and lineage against the complete submission."""
    parent_source_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    if (
        not isinstance(component.parent_source_sha256, str)
        or _SHA256.fullmatch(component.parent_source_sha256) is None
        or component.parent_source_sha256 != parent_source_sha256
    ):
        raise ProvenanceError("SEC filing component parent source hash mismatch")
    if (
        isinstance(component.char_start, bool)
        or isinstance(component.char_end, bool)
        or not isinstance(component.char_start, int)
        or not isinstance(component.char_end, int)
        or component.char_start < 0
        or component.char_end <= component.char_start
        or component.char_end > len(source_text)
    ):
        raise ProvenanceError("SEC filing component range is invalid")
    block = source_text[component.char_start : component.char_end]
    if _SEC_DOCUMENT_BLOCK.fullmatch(block) is None:
        raise ProvenanceError("SEC filing component range is not a DOCUMENT block")
    component_sha256 = hashlib.sha256(block.encode()).hexdigest()
    if (
        not isinstance(component.component_sha256, str)
        or _SHA256.fullmatch(component.component_sha256) is None
        or component.component_sha256 != component_sha256
    ):
        raise ProvenanceError("SEC filing component hash mismatch")
    component_type, sequence, filename = _sec_component_header(block)
    if (
        component_type not in SEC_EXTRACTABLE_COMPONENT_TYPES
        or isinstance(component.sequence, bool)
        or not isinstance(component.sequence, int)
        or component.component_type != component_type
        or component.sequence != sequence
        or component.filename != filename
    ):
        raise ProvenanceError("SEC filing component header metadata mismatch")
    expected_provenance = _sec_component_provenance(
        component_type=component_type,
        sequence=sequence,
        filename=filename,
        char_start=component.char_start,
        char_end=component.char_end,
        parent_source_sha256=parent_source_sha256,
        component_sha256=component_sha256,
    )
    if component.provenance_id != expected_provenance:
        raise ProvenanceError("SEC filing component provenance mismatch")


def parse_sec_filing_components(
    source_text: str, parent_source_sha256: str
) -> tuple[SecFilingComponent, ...]:
    """Parse the main filing and certifications from an SEC complete submission."""
    if (
        _SHA256.fullmatch(parent_source_sha256) is None
        or hashlib.sha256(source_text.encode()).hexdigest() != parent_source_sha256
    ):
        raise ProvenanceError("SEC filing component parent source hash mismatch")
    blocks = list(_SEC_DOCUMENT_BLOCK.finditer(source_text))
    start_count = len(re.findall(r"(?m)^<DOCUMENT>[^\S\n]*$", source_text))
    end_count = len(re.findall(r"(?m)^</DOCUMENT>[^\S\n]*$", source_text))
    if not blocks or len(blocks) != start_count or len(blocks) != end_count:
        raise ProvenanceError("SEC filing submission has invalid DOCUMENT boundaries")

    required = set(SEC_REQUIRED_COMPONENT_TYPES)
    extractable = set(SEC_EXTRACTABLE_COMPONENT_TYPES)
    components: list[SecFilingComponent] = []
    seen_types: set[str] = set()
    seen_sequences: set[int] = set()
    for match in blocks:
        block = match.group()
        component_type, sequence, filename = _sec_component_header(block)
        if component_type not in extractable:
            continue
        if component_type in seen_types or sequence in seen_sequences:
            raise ProvenanceError("SEC filing component identity is duplicated")
        component_sha256 = hashlib.sha256(block.encode()).hexdigest()
        component = SecFilingComponent(
            component_type=component_type,
            sequence=sequence,
            filename=filename,
            char_start=match.start(),
            char_end=match.end(),
            parent_source_sha256=parent_source_sha256,
            component_sha256=component_sha256,
            provenance_id=_sec_component_provenance(
                component_type=component_type,
                sequence=sequence,
                filename=filename,
                char_start=match.start(),
                char_end=match.end(),
                parent_source_sha256=parent_source_sha256,
                component_sha256=component_sha256,
            ),
        )
        validate_sec_filing_component(source_text, component)
        components.append(component)
        seen_types.add(component_type)
        seen_sequences.add(sequence)
    missing = required - seen_types
    if missing:
        raise ProvenanceError(
            f"SEC filing submission is missing required components: {sorted(missing)}"
        )
    return tuple(components)


def _validate_final_payload(payload: dict[str, Any]) -> None:
    for value in _payload_strings(payload):
        _reject_secrets(value)
        if _EMAIL.search(value):
            raise ProvenanceError("SEC manifest contains unredacted email PII")


def _validate_authorization(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProvenanceError("SEC source authorization receipt is invalid")
    required = ("record_id", "scope", "basis", "reviewed_at")
    authorization = {field: str(value.get(field) or "").strip() for field in required}
    if any(not authorization[field] for field in required):
        raise ProvenanceError("SEC source authorization receipt is invalid")
    _parse_timestamp(authorization["reviewed_at"], "authorization.reviewed_at")
    return authorization


def _validate_fetch_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceError("SEC public download fetch receipt is invalid")
    if value.get("schema_version") != "longworld.sec-fetch-receipt.v1":
        raise ProvenanceError("SEC public download fetch receipt is invalid")
    generated_at = str(value.get("generated_at") or "")
    _parse_timestamp(generated_at, "fetch_receipt.generated_at")
    rate = value.get("requests_per_second")
    retries = value.get("max_retries")
    user_agent_sha256 = str(value.get("user_agent_sha256") or "")
    if (
        value.get("policy_url") != _SEC_FAIR_ACCESS_URL
        or isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= 10
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
        or _SHA256.fullmatch(user_agent_sha256) is None
    ):
        raise ProvenanceError("SEC public download fetch receipt is invalid")
    return {
        "schema_version": "longworld.sec-fetch-receipt.v1",
        "generated_at": generated_at,
        "policy_url": _SEC_FAIR_ACCESS_URL,
        "requests_per_second": float(rate),
        "max_retries": retries,
        "user_agent_sha256": user_agent_sha256,
    }


def _validate_sec_identity(filing: dict[str, Any]) -> tuple[str, str, str, str, str]:
    accession = str(filing.get("accession") or "")
    cik = str(filing.get("cik") or "")
    form = str(filing.get("form") or "")
    filing_date = str(filing.get("filing_date") or "")
    source_url = str(filing.get("source_url") or "")
    if _ACCESSION.fullmatch(accession) is None:
        raise ProvenanceError("SEC filing accession is missing or invalid")
    if _CIK.fullmatch(cik) is None:
        raise ProvenanceError("SEC filing CIK must contain ten digits")
    if accession.split("-", 1)[0] != cik:
        raise ProvenanceError("SEC filing accession does not match its CIK")
    if _FORM.fullmatch(form) is None:
        raise ProvenanceError("SEC filing form is missing or invalid")
    try:
        date.fromisoformat(filing_date)
    except ValueError as exc:
        raise ProvenanceError("SEC filing date must be ISO-8601") from exc
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "sec.gov" or host.endswith(".sec.gov")):
        raise ProvenanceError("SEC filing source URL must be an HTTPS sec.gov URL")
    return accession, cik, form, filing_date, source_url


def _source_path(base_directory: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("SEC filing source file is missing")
    relative = Path(value)
    if (
        relative.name != value
        or relative.suffix.lower() not in {".json", ".txt"}
        or relative.is_absolute()
    ):
        raise ProvenanceError("SEC filing source file is unsafe or unsupported")
    return base_directory / relative


def _source_lineage(filing: dict[str, Any]) -> tuple[str, str, str]:
    retrieved_at = str(filing.get("retrieved_at") or "")
    access_policy = str(filing.get("access_policy") or "").strip()
    parser = filing.get("parser")
    _parse_timestamp(retrieved_at, "source retrieved_at")
    if not access_policy:
        raise ProvenanceError("SEC filing source access policy is missing")
    if not isinstance(parser, dict):
        raise ProvenanceError("SEC filing source parser metadata is missing")
    parser_name = str(parser.get("name") or "").strip()
    parser_version = str(parser.get("version") or "").strip()
    if not parser_name or not parser_version:
        raise ProvenanceError("SEC filing source parser metadata is invalid")
    return retrieved_at, access_policy, f"{parser_name}@{parser_version}"


def _report_date(filing: dict[str, Any]) -> str:
    report_date = str(filing.get("report_date") or "")
    if report_date:
        try:
            date.fromisoformat(report_date)
        except ValueError as exc:
            raise ProvenanceError("SEC filing report date must be ISO-8601") from exc
    return report_date


def _derived_facts(
    value: object,
    *,
    raw_text: str,
    clean_text: str,
    source_sha256: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ProvenanceError("SEC filing requires source-grounded derived facts")
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ProvenanceError("SEC derived fact must be an object")
        fact_id = str(item.get("fact_id") or "").strip()
        field = str(item.get("field") or "").strip()
        fact_value = str(item.get("value") or "").strip()
        quote = str(item.get("evidence_quote") or "")
        start = item.get("evidence_char_start")
        if not fact_id or fact_id in seen:
            raise ProvenanceError("SEC derived fact id is missing or duplicated")
        if not field or not fact_value or not quote or not isinstance(start, int):
            raise ProvenanceError("SEC derived fact is incomplete")
        if start < 0 or raw_text[start : start + len(quote)] != quote:
            raise ProvenanceError(
                "SEC derived fact evidence span does not match source"
            )
        if fact_value not in quote:
            raise ProvenanceError("SEC derived fact value is not present in evidence")
        _reject_secrets(quote)
        if _EMAIL.search(quote) or _EMAIL.search(fact_value):
            raise ProvenanceError("SEC derived fact contains email PII")
        clean_start = len(_EMAIL.sub("[redacted-email]", raw_text[:start]))
        if clean_text[clean_start : clean_start + len(quote)] != quote:
            raise ProvenanceError(
                "SEC derived fact evidence was removed by sanitization"
            )
        facts.append(
            {
                "fact_id": fact_id,
                "field": field,
                "value": fact_value,
                "evidence_quote": quote,
                "evidence_char_start": clean_start,
                "raw_evidence_char_start": start,
                "source_sha256": source_sha256,
            }
        )
        seen.add(fact_id)
    return facts


def _validate_public_identity_facts(
    facts: list[dict[str, Any]],
    *,
    accession: str,
    form: str,
    filing_date: str,
    report_date: str,
) -> None:
    values = {fact["field"]: fact["value"] for fact in facts}
    expected = {
        "accession": accession,
        "form": form,
        "filing_date": filing_date.replace("-", ""),
    }
    if report_date:
        expected["report_date"] = report_date.replace("-", "")
    if any(values.get(field) != value for field, value in expected.items()):
        raise ProvenanceError("SEC public source facts do not bind filing identity")


def parse_sec_identity_spans(text: str, raw_facts: object) -> dict[str, str] | None:
    """Parse filing identity only from exact, replayed body spans.

    Event replay uses this consumer-side check instead of trusting copied manifest
    values. Invalid or incomplete spans fail closed so a corrupted filing body cannot
    write downstream workflow state.
    """
    if not isinstance(raw_facts, Sequence) or isinstance(raw_facts, (str, bytes)):
        return None
    parsed: dict[str, str] = {}
    for raw in raw_facts:
        if not isinstance(raw, Mapping):
            return None
        field = str(raw.get("field") or "")
        quote = str(raw.get("evidence_quote") or "")
        start = raw.get("char_start")
        end = raw.get("char_end")
        offset = raw.get("value_offset")
        length = raw.get("value_length")
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (start, end, offset, length)
        ):
            return None
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert isinstance(offset, int)
        assert isinstance(length, int)
        if (
            field not in SEC_IDENTITY_FIELDS
            or field in parsed
            or not quote
            or start < 0
            or end <= start
            or offset < 0
            or length <= 0
            or text[start:end] != quote
            or offset + length > len(quote)
        ):
            return None
        parsed[field] = quote[offset : offset + length]
    if set(parsed) != set(SEC_IDENTITY_FIELDS):
        return None
    if (
        _ACCESSION.fullmatch(parsed["accession"]) is None
        or _FORM.fullmatch(parsed["form"]) is None
        or not re.fullmatch(r"\d{8}", parsed["filing_date"])
        or not re.fullmatch(r"\d{8}", parsed["report_date"])
    ):
        return None
    try:
        filing_date = date.fromisoformat(
            f"{parsed['filing_date'][:4]}-{parsed['filing_date'][4:6]}-"
            f"{parsed['filing_date'][6:]}"
        )
        report_date = date.fromisoformat(
            f"{parsed['report_date'][:4]}-{parsed['report_date'][4:6]}-"
            f"{parsed['report_date'][6:]}"
        )
    except ValueError:
        return None
    return {
        "accession": parsed["accession"],
        "form": parsed["form"],
        "filing_date": filing_date.isoformat(),
        "report_date": report_date.isoformat(),
    }


def _filing_relations(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for amendment in filings:
        form = str(amendment["form"])
        report_date = str(amendment.get("report_date") or "")
        if not form.endswith("/A") or not report_date:
            continue
        base_form = form.removesuffix("/A")
        candidates = [
            filing
            for filing in filings
            if filing["cik"] == amendment["cik"]
            and filing["form"] == base_form
            and filing.get("report_date") == report_date
            and filing["filing_date"] <= amendment["filing_date"]
        ]
        if len(candidates) != 1:
            continue
        original = candidates[0]
        relations.append(
            {
                "relation_id": (
                    f"{amendment['record_id']}:amends:{original['record_id']}"
                ),
                "relation_type": "amends_report",
                "from_record_id": amendment["record_id"],
                "to_record_id": original["record_id"],
                "shared_report_date": report_date,
                "derivation_rule": ("unique_prior_same_cik_base_form_and_report_date"),
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
        )
    return sorted(relations, key=lambda relation: relation["relation_id"])


def validated_sec_amendment_endpoints(
    workflow: Any, relation: Any
) -> tuple[Any, Any] | None:
    """Return amendment/original records only for an exact grounded SEC relation."""
    if (
        getattr(workflow, "source_kind", "") != "sec_filing"
        or not getattr(relation, "relation_id", "")
        or getattr(relation, "kind", "") != "amends_report"
        or getattr(relation, "source_record_id", "")
        == getattr(relation, "target_record_id", "")
    ):
        return None
    raw_records = tuple(getattr(workflow, "records", ()))
    records = {record.record_id: record for record in raw_records}
    if (
        len(records) != len(raw_records)
        or len({record.source_sha256 for record in raw_records}) != len(raw_records)
        or len({record.provenance_id for record in raw_records}) != len(raw_records)
        or set(getattr(workflow, "provenance_ids", ()))
        != {record.provenance_id for record in raw_records}
    ):
        return None
    amendment = records.get(relation.source_record_id)
    original = records.get(relation.target_record_id)
    if amendment is None or original is None:
        return None
    if any(
        getattr(getattr(record, "source_origin", None), "value", "")
        not in {"real_public", "real_private_export"}
        or record.provenance_id != f"sha256:{record.source_sha256}"
        for record in (amendment, original)
    ):
        return None
    if (
        amendment.source_sha256 == original.source_sha256
        or amendment.text_sha256 == original.text_sha256
        or hashlib.sha256(amendment.text.encode()).hexdigest() != amendment.text_sha256
        or hashlib.sha256(original.text.encode()).hexdigest() != original.text_sha256
    ):
        return None

    amendment_attributes = dict(amendment.attributes)
    original_attributes = dict(original.attributes)
    try:
        amendment_day = date.fromisoformat(amendment.occurred_at[:10])
        original_day = date.fromisoformat(original.occurred_at[:10])
    except ValueError:
        return None
    for record, attributes in (
        (amendment, amendment_attributes),
        (original, original_attributes),
    ):
        facts_by_field = {
            field: [fact for fact in record.facts if fact.field == field]
            for field in SEC_IDENTITY_FIELDS
        }
        if any(len(facts) != 1 for facts in facts_by_field.values()):
            return None
        for fact in (facts[0] for facts in facts_by_field.values()):
            if (
                fact.record_id != record.record_id
                or fact.source_sha256 != record.source_sha256
                or fact.char_start < 0
                or fact.char_end != fact.char_start + len(fact.evidence_quote)
                or record.text[fact.char_start : fact.char_end] != fact.evidence_quote
                or fact.value_offset < 0
                or fact.evidence_quote[
                    fact.value_offset : fact.value_offset + len(fact.value)
                ]
                != fact.value
            ):
                return None
        if (
            facts_by_field["accession"][0].value != attributes.get("accession")
            or facts_by_field["form"][0].value != attributes.get("form")
            or facts_by_field["filing_date"][0].value
            != str(attributes.get("filing_date", "")).replace("-", "")
            or facts_by_field["report_date"][0].value
            != str(attributes.get("report_date", "")).replace("-", "")
            or record.record_id != f"sec:{attributes.get('accession', '')}"
        ):
            return None
    amendment_form = amendment_attributes.get("form", "")
    shared_report_date = amendment_attributes.get("report_date", "")
    if (
        not amendment_form.endswith("/A")
        or amendment_form.removesuffix("/A") != original_attributes.get("form")
        or not shared_report_date
        or shared_report_date != original_attributes.get("report_date")
        or amendment_attributes.get("cik") != original_attributes.get("cik")
        or amendment_day < original_day
    ):
        return None

    facts = {
        (record.record_id, fact.fact_id): fact
        for record in (amendment, original)
        for fact in record.facts
    }
    required = {
        (record.record_id, field)
        for record in (amendment, original)
        for field in ("form", "report_date")
    }
    if len(relation.evidence) != len(required):
        return None
    grounded: set[tuple[str, str]] = set()
    for evidence in relation.evidence:
        record = records.get(evidence.record_id)
        if (
            evidence.record_id not in {amendment.record_id, original.record_id}
            or record is None
            or evidence.source_sha256 != record.source_sha256
            or evidence.char_start < 0
            or evidence.char_end <= evidence.char_start
            or record.text[evidence.char_start : evidence.char_end]
            != evidence.evidence_quote
            or len(evidence.fact_ids) != 1
        ):
            return None
        fact = facts.get((evidence.record_id, evidence.fact_ids[0]))
        if (
            fact is None
            or fact.field not in {"form", "report_date"}
            or fact.evidence_quote != evidence.evidence_quote
            or fact.char_start != evidence.char_start
            or fact.char_end != evidence.char_end
            or fact.source_sha256 != evidence.source_sha256
        ):
            return None
        identity = (record.record_id, fact.field)
        if identity in grounded:
            return None
        grounded.add(identity)
    if grounded != required:
        return None
    return amendment, original


def _sec_source_event_matches_record(event: Any, record: Any) -> bool:
    params = getattr(event, "params", None)
    if not isinstance(params, Mapping) or getattr(event, "type", "") != "sec_filing":
        return False
    text = str(params.get("text") or "")
    if (
        not text
        or hashlib.sha256(text.encode()).hexdigest() != params.get("text_sha256")
        or params.get("source_sha256") != record.source_sha256
        or params.get("source_family") != record.source_family
        or params.get("source_url") != record.source_url
        or params.get("retrieval_url") != record.retrieval_url
        or record.text.count(text) != 1
    ):
        return False
    corridor_start = record.text.index(text)
    fact_spans = params.get("fact_spans")
    if (
        not isinstance(fact_spans, Sequence)
        or isinstance(fact_spans, (str, bytes))
        or len(fact_spans) != len(SEC_IDENTITY_FIELDS)
        or not all(isinstance(span, Mapping) for span in fact_spans)
    ):
        return False
    facts_by_field = {
        field: [fact for fact in record.facts if fact.field == field]
        for field in SEC_IDENTITY_FIELDS
    }
    if any(len(facts) != 1 for facts in facts_by_field.values()):
        return False
    seen_fields: set[str] = set()
    for span in fact_spans:
        field = str(span.get("field") or "")
        facts = facts_by_field.get(field)
        if facts is None or len(facts) != 1 or field in seen_fields:
            return False
        fact = facts[0]
        expected_start = fact.char_start - corridor_start
        expected_end = fact.char_end - corridor_start
        if (
            span.get("fact_id") != fact.fact_id
            or span.get("evidence_quote") != fact.evidence_quote
            or span.get("char_start") != expected_start
            or span.get("char_end") != expected_end
            or span.get("value_offset") != fact.value_offset
            or span.get("value_length") != len(fact.value)
            or expected_start < 0
            or expected_end > len(text)
            or text[expected_start:expected_end] != fact.evidence_quote
        ):
            return False
        seen_fields.add(field)
    if seen_fields != set(SEC_IDENTITY_FIELDS):
        return False

    if corridor_start == 0 and len(text) == len(record.text):
        return (
            params.get("provenance_id") == record.provenance_id
            and not params.get("parent_provenance_id")
            and not params.get("provenance_operation")
            and params.get("source_origin")
            == getattr(record.source_origin, "value", "")
        )
    corridor_end = corridor_start + len(text)
    excerpt_sha256 = hashlib.sha256(text.encode()).hexdigest()
    expected_provenance = (
        "derived-sha256:"
        + hashlib.sha256(
            (
                f"sec_evidence_corridor|{record.provenance_id}|"
                f"{corridor_start}|{corridor_end}|{excerpt_sha256}"
            ).encode()
        ).hexdigest()
    )
    return (
        params.get("provenance_id") == expected_provenance
        and params.get("parent_provenance_id") == record.provenance_id
        and params.get("provenance_operation") == "sec_evidence_corridor"
        and params.get("source_origin") == "real_derived"
    )


def selected_sec_source_relation_edges(
    world: Any, spec: Any, artifacts: Sequence[Any]
) -> list[dict[str, str]]:
    """Derive authentic SEC edges only from selected, exact source artifacts."""
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
    selected: dict[tuple[str, str], list[Any]] = {}
    for artifact in artifacts:
        slots = getattr(artifact, "slots", None) or {}
        workflow_id = str(slots.get("source_workflow_id") or "")
        record_id = str(slots.get("source_record_id") or "")
        revealed = [
            event_index[event_id]
            for event_id in getattr(artifact, "reveals_events", ())
            if event_id in event_index and event_index[event_id].type == "sec_filing"
        ]
        if (
            slots.get("real_workflow_record") is not True
            or getattr(artifact, "doc_type", "") != "sec_filing"
            or slots.get("event_type") != "sec_filing"
            or not workflow_id
            or not record_id
            or len(revealed) != 1
        ):
            continue
        event = revealed[0]
        classification = slots.get("classification")
        event_text = str(event.params.get("text") or "")
        if (
            not isinstance(classification, Mapping)
            or event.params.get("workflow_id") != workflow_id
            or event.params.get("record_id") != record_id
            or slots.get("params") != event.params
            or not event_text
            or str(getattr(artifact, "text", "")).count(event_text) != 1
            or classification.get("source_origin")
            not in {"real_public", "real_private_export", "real_derived"}
        ):
            continue
        selected.setdefault((workflow_id, record_id), []).append(event)

    project = getattr(world, "spec", {}).get("project")
    if not isinstance(project, Mapping):
        return []
    workflows_by_id: dict[str, list[Any]] = {}
    for workflow in project.get("source_workflows") or ():
        workflows_by_id.setdefault(
            str(getattr(workflow, "workflow_id", "")), []
        ).append(workflow)
    edges: list[dict[str, str]] = []
    for workflow_id in sorted({item[0] for item in selected}):
        matches = workflows_by_id.get(workflow_id, [])
        if len(matches) != 1:
            continue
        workflow = matches[0]
        workflow_records = {
            record.record_id: record for record in getattr(workflow, "records", ())
        }
        for relation in getattr(workflow, "relations", ()):
            endpoints = validated_sec_amendment_endpoints(workflow, relation)
            if endpoints is None:
                continue
            amendment, original = endpoints
            if {
                (workflow_id, amendment.record_id),
                (workflow_id, original.record_id),
            } - selected.keys():
                continue
            if any(
                not any(
                    _sec_source_event_matches_record(event, workflow_records[record_id])
                    for event in selected[(workflow_id, record_id)]
                )
                for record_id in (amendment.record_id, original.record_id)
            ):
                continue
            edges.append(
                {
                    "parent_record_id": original.record_id,
                    "child_record_id": amendment.record_id,
                    "relation": relation.kind,
                    "relation_provenance": "authentic_source",
                    "parent_source_url": original.source_url,
                    "child_source_url": amendment.source_url,
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


def build_sec_filing_manifest(
    input_payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Validate local SEC source files and build an unsigned audit manifest."""
    if input_payload.get("schema_version") != SEC_FILING_INPUT_SCHEMA:
        raise ProvenanceError("unsupported SEC filing input schema")
    source_status = str(input_payload.get("source_status") or "")
    if source_status not in _SOURCE_STATUSES:
        raise ProvenanceError("SEC input requires an explicit source status")
    authorization = _validate_authorization(input_payload.get("authorization"))
    fetch_receipt = None
    if source_status == "public_sec_download":
        fetch_receipt = _validate_fetch_receipt(input_payload.get("fetch_receipt"))
    _parse_timestamp(generated_at, "generated_at")
    raw_filings = input_payload.get("filings")
    if (
        not isinstance(raw_filings, list)
        or not raw_filings
        or len(raw_filings) > MAX_SEC_FILINGS
    ):
        raise ProvenanceError("SEC input has an invalid filing count")

    filings: list[dict[str, Any]] = []
    seen_accessions: set[str] = set()
    seen_hashes: set[str] = set()
    for item in raw_filings:
        if not isinstance(item, dict):
            raise ProvenanceError("SEC filing must be an object")
        accession, cik, form, filing_date, source_url = _validate_sec_identity(item)
        report_date = _report_date(item)
        retrieved_at, access_policy, parser = _source_lineage(item)
        source_path = _source_path(base_directory, item.get("source_file"))
        try:
            raw = _read_regular_file(source_path, MAX_SOURCE_BYTES)
            raw_text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProvenanceError(f"cannot read SEC filing source: {exc}") from exc
        if source_path.suffix.lower() == ".json":
            try:
                json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise ProvenanceError("SEC JSON source is invalid") from exc
        source_sha256 = str(item.get("source_sha256") or "")
        if _SHA256.fullmatch(source_sha256) is None:
            raise ProvenanceError("SEC filing source sha256 is missing or invalid")
        if hashlib.sha256(raw).hexdigest() != source_sha256:
            raise ProvenanceError("SEC filing source hash mismatch")
        if accession in seen_accessions:
            raise ProvenanceError("duplicate accession in SEC input")
        if source_sha256 in seen_hashes:
            raise ProvenanceError("duplicate source_sha256 in SEC input")

        clean_text, redaction_count = _sanitize_source_text(raw_text)
        facts = _derived_facts(
            item.get("derived_facts"),
            raw_text=raw_text,
            clean_text=clean_text,
            source_sha256=source_sha256,
        )
        if source_status == "public_sec_download":
            canonical_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}/{accession}.txt"
            )
            if source_url != canonical_url:
                raise ProvenanceError("SEC public source URL is not canonical")
            _validate_public_identity_facts(
                facts,
                accession=accession,
                form=form,
                filing_date=filing_date,
                report_date=report_date,
            )
        filings.append(
            {
                "record_id": f"sec:{accession}",
                "accession": accession,
                "cik": cik,
                "form": form,
                "filing_date": filing_date,
                "report_date": report_date,
                "source_url": source_url,
                "source_file": source_path.name,
                "source_sha256": source_sha256,
                "provenance_id": f"sha256:{source_sha256}",
                "retrieved_at": retrieved_at,
                "access_policy": access_policy,
                "parser": parser,
                "text_sha256": hashlib.sha256(clean_text.encode()).hexdigest(),
                "text": clean_text,
                "derived_facts": facts,
                "privacy_review": {
                    "emails": "redacted",
                    "email_redaction_count": redaction_count,
                    "secrets": "fail_closed",
                    "scanner": SEC_SCANNER,
                    "scanner_revision": SEC_SCANNER_REVISION,
                },
            }
        )
        seen_accessions.add(accession)
        seen_hashes.add(source_sha256)

    manifest = {
        "schema_version": SEC_FILING_MANIFEST_SCHEMA,
        "source_status": source_status,
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "generated_at": generated_at,
        "authorization": authorization,
        "n": len(filings),
        "filings": filings,
        "filing_relations": _filing_relations(filings),
    }
    if fetch_receipt is not None:
        manifest["fetch_receipt"] = fetch_receipt
    _validate_final_payload(manifest)
    if len(json.dumps(manifest, ensure_ascii=False).encode()) > MAX_SEC_MANIFEST_BYTES:
        raise ProvenanceError("SEC filing manifest exceeds size limit")
    return manifest


def _audit_exported_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SEC_FILING_MANIFEST_SCHEMA:
        raise ProvenanceError("unsupported SEC filing manifest schema")
    if (
        payload.get("production_eligible") is not False
        or payload.get("data_stage") != "source_inventory"
        or payload.get("hybrid_train_ready") is not False
        or payload.get("generation_integration") != "disabled"
    ):
        raise ProvenanceError("SEC filing skeleton cannot be production eligible")
    if str(payload.get("source_status") or "") not in _SOURCE_STATUSES:
        raise ProvenanceError("SEC manifest has an invalid source status")
    if payload.get("source_status") == "public_sec_download":
        _validate_fetch_receipt(payload.get("fetch_receipt"))
    elif "fetch_receipt" in payload:
        raise ProvenanceError("SEC manifest has an unexpected fetch receipt")
    _validate_authorization(payload.get("authorization"))
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    filings = payload.get("filings")
    if (
        not isinstance(filings, list)
        or not filings
        or len(filings) > MAX_SEC_FILINGS
        or payload.get("n") != len(filings)
    ):
        raise ProvenanceError("SEC manifest has an invalid filing count")
    seen_accessions: set[str] = set()
    seen_hashes: set[str] = set()
    for filing in filings:
        if not isinstance(filing, dict):
            raise ProvenanceError("SEC manifest filing must be an object")
        accession, cik, form, filing_date, source_url = _validate_sec_identity(filing)
        report_date = _report_date(filing)
        _parse_timestamp(str(filing.get("retrieved_at") or ""), "source retrieved_at")
        if not str(filing.get("access_policy") or "").strip():
            raise ProvenanceError("SEC manifest source access policy is invalid")
        parser_name, separator, parser_version = str(
            filing.get("parser") or ""
        ).partition("@")
        if not separator or not parser_name or not parser_version:
            raise ProvenanceError("SEC manifest source parser metadata is invalid")
        source_sha256 = str(filing.get("source_sha256") or "")
        if _SHA256.fullmatch(source_sha256) is None:
            raise ProvenanceError("SEC manifest source sha256 is invalid")
        if filing.get("provenance_id") != f"sha256:{source_sha256}":
            raise ProvenanceError("SEC manifest provenance id is invalid")
        if filing.get("record_id") != f"sec:{accession}":
            raise ProvenanceError("SEC manifest record id is invalid")
        source_file = filing.get("source_file")
        if not isinstance(source_file, str) or Path(source_file).name != source_file:
            raise ProvenanceError("SEC manifest source file is invalid")
        if accession in seen_accessions or source_sha256 in seen_hashes:
            raise ProvenanceError("SEC manifest contains a duplicate filing")
        text = filing.get("text")
        if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != (
            filing.get("text_sha256")
        ):
            raise ProvenanceError("SEC manifest text hash mismatch")
        privacy = filing.get("privacy_review")
        if not isinstance(privacy, dict) or privacy != {
            "emails": "redacted",
            "email_redaction_count": privacy.get("email_redaction_count"),
            "secrets": "fail_closed",
            "scanner": SEC_SCANNER,
            "scanner_revision": SEC_SCANNER_REVISION,
        }:
            raise ProvenanceError("SEC manifest privacy review is invalid")
        if (
            not isinstance(privacy["email_redaction_count"], int)
            or privacy["email_redaction_count"] < 0
        ):
            raise ProvenanceError("SEC manifest privacy review is invalid")
        facts = filing.get("derived_facts")
        if not isinstance(facts, list) or not facts:
            raise ProvenanceError("SEC manifest has no derived facts")
        fact_ids: set[str] = set()
        for fact in facts:
            if not isinstance(fact, dict):
                raise ProvenanceError("SEC manifest derived fact is invalid")
            fact_id = str(fact.get("fact_id") or "")
            field = str(fact.get("field") or "")
            quote = str(fact.get("evidence_quote") or "")
            value = str(fact.get("value") or "")
            start = fact.get("evidence_char_start")
            if (
                not fact_id
                or not field
                or fact_id in fact_ids
                or not isinstance(start, int)
                or start < 0
                or text[start : start + len(quote)] != quote
                or value not in quote
                or fact.get("source_sha256") != source_sha256
            ):
                raise ProvenanceError(
                    "SEC manifest derived fact is not source-grounded"
                )
            fact_ids.add(fact_id)
        if payload.get("source_status") == "public_sec_download":
            canonical_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}/{accession}.txt"
            )
            if source_url != canonical_url:
                raise ProvenanceError("SEC public source URL is not canonical")
            _validate_public_identity_facts(
                facts,
                accession=accession,
                form=form,
                filing_date=filing_date,
                report_date=report_date,
            )
        seen_accessions.add(accession)
        seen_hashes.add(source_sha256)
    if payload.get("filing_relations") != _filing_relations(filings):
        raise ProvenanceError("SEC manifest filing relations are invalid")
    _validate_final_payload(payload)


def load_sec_filing_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Load and verify a source-attested SEC filing manifest."""
    try:
        raw = _read_regular_file(path, MAX_SEC_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read SEC filing manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProvenanceError("SEC filing manifest must be an object")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("SEC filing manifest has no valid source attestation")
    _audit_exported_manifest(payload)
    return payload
