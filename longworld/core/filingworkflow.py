"""Auditable ingestion contracts for already-downloaded SEC filing sources.

This module does not fetch EDGAR and does not feed production generation.  It
binds local source bytes, SEC identifiers, and source-grounded derived facts in
an attested manifest that a later integration can consume explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from html import unescape
from itertools import pairwise
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
MAX_ISSUER_GCS_DETAIL_BYTES = 1_000_000
SEC_LONGITUDINAL_GCS_FILINGS = 4
SEC_MANIFEST_JSON_FIXED_OVERHEAD_BYTES = 4_000_000
# GCS HTML rejects control bytes that could expand beyond JSON's 2x quote and
# backslash escaping. This aggregate still leaves the independent merged/detail
# response caps and filing-count cap unchanged.
MAX_SEC_MANIFEST_BYTES = (
    2 * SEC_LONGITUDINAL_GCS_FILINGS * (MAX_SOURCE_BYTES + MAX_ISSUER_GCS_DETAIL_BYTES)
    + SEC_MANIFEST_JSON_FIXED_OVERHEAD_BYTES
)
SEC_FILING_COMPONENT_REVISION = "sec-sgml-component-v1"
SEC_MULTI_SPAN_PROJECTION_REVISION = "sec_multi_span_projection_v1"
ISSUER_GCS_MERGED_COMPONENT_REVISION = "issuer_gcs_merged_html@1"
ISSUER_GCS_MERGED_COMPONENT_REVISION_V2 = "issuer_gcs_merged_html@2"
ISSUER_GCS_MERGED_COMPONENT_REVISIONS = frozenset(
    {
        ISSUER_GCS_MERGED_COMPONENT_REVISION,
        ISSUER_GCS_MERGED_COMPONENT_REVISION_V2,
    }
)

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_CIK = re.compile(r"^\d{10}$")
_FORM = re.compile(r"^[A-Z0-9-]+(?:/A)?$")
_PRIMARY_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
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
    "issuer_owned_public_export",
}
_SEC_FAIR_ACCESS_URL = (
    "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data"
)
SEC_IDENTITY_FIELDS = ("accession", "form", "filing_date", "report_date")
SEC_ANNUAL_IDENTITY_FIELDS = (*SEC_IDENTITY_FIELDS, "cik")
SEC_ANNUAL_RELATION_FIELDS = ("cik", "form", "filing_date", "report_date")
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
        "sec_prior_annual_filing_relation",
        "sec_annual_revenue_change",
        "sec_filing_publication_ratification",
        "sec_financial_answer",
    }
)

_SEC_DOCUMENT_BLOCK = re.compile(
    r"^<DOCUMENT>[^\S\n]*\n.*?^</DOCUMENT>[^\S\n]*(?:\n|$)",
    re.MULTILINE | re.DOTALL,
)
_GCS_DOCUMENT_TITLE = re.compile(
    r"(?m)^[ \t]*<title>([^<]+)</title>[ \t]*$",
)
_GCS_DOCUMENT_ANCHOR = re.compile(
    r'<div><a name="([A-Za-z0-9][A-Za-z0-9._-]{0,254})"></a></div>[ \t\r\n]*$'
)
_GCS_V2_DOCUMENT_TITLE = re.compile(
    r"<title>[ \t\r\n]*([^<]+?)[ \t\r\n]*</title>", re.IGNORECASE
)
_GCS_V2_DOCUMENT_ANCHOR = re.compile(
    r'<div><a name="([A-Za-z0-9][A-Za-z0-9._-]{0,254})"></a></div>'
    r"[ \t\r\n]*(?:<!DOCTYPE\b[^>]*>[ \t\r\n]*)?"
    r"(?P<html><html(?:\s[^>]*)?>)",
    re.IGNORECASE,
)
_GCS_V2_EXHIBIT_HEADING = re.compile(
    r"\bExhibit\s+(31\.1|31\.2|32\.1|32\.2)\b", re.IGNORECASE
)
_GCS_V2_LEGACY_PRIMARY = re.compile(r"^msft-10k_(\d{8})\.htm$")
_GCS_CIK_FACT = re.compile(
    r'<ix:nonNumeric\b[^>]*\bname="dei:EntityCentralIndexKey"[^>]*>'
    r"[ \t\r\n]*(\d{10})[ \t\r\n]*</ix:nonNumeric>",
    re.IGNORECASE,
)
_GCS_FORM_FACT = re.compile(
    r'<ix:nonNumeric\b[^>]*\bname="dei:DocumentType"[^>]*>'
    r".*?</ix:nonNumeric>",
    re.IGNORECASE | re.DOTALL,
)
_GCS_SCHEMA_REF = re.compile(r'\bxlink:href="#([A-Za-z0-9][A-Za-z0-9._-]{0,249})\.xsd"')
_GCS_HTML_BOUNDARY = re.compile(r"<html(?:\s[^>]*)?>|</html\s*>", re.IGNORECASE)
_GCS_JSON_EXPANDING_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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
    parser_revision: str = SEC_FILING_COMPONENT_REVISION


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


def _issuer_gcs_component_provenance(
    *,
    component_type: str,
    sequence: int,
    filename: str,
    char_start: int,
    char_end: int,
    parent_source_sha256: str,
    component_sha256: str,
    parser_revision: str = ISSUER_GCS_MERGED_COMPONENT_REVISION,
) -> str:
    payload = {
        "operation": parser_revision,
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


def _issuer_gcs_outer_html_end(source_text: str) -> int:
    depth = 0
    root_start: int | None = None
    root_end: int | None = None
    for match in _GCS_HTML_BOUNDARY.finditer(source_text):
        closing = source_text[match.start() + 1 : match.end()].lstrip().startswith("/")
        if closing:
            if depth <= 0:
                raise ProvenanceError("issuer GCS outer HTML envelope is invalid")
            depth -= 1
            if depth == 0:
                root_end = match.end()
        else:
            if root_end is not None:
                raise ProvenanceError("issuer GCS outer HTML envelope is invalid")
            if root_start is None:
                root_start = match.start()
            depth += 1
    if (
        root_start is None
        or root_end is None
        or depth != 0
        or re.fullmatch(
            r"\s*<!DOCTYPE html>\s*", source_text[:root_start], re.IGNORECASE
        )
        is None
        or source_text[root_end:].strip()
    ):
        raise ProvenanceError("issuer GCS outer HTML envelope is invalid")
    return root_end


def validate_issuer_gcs_merged_component(
    source_text: str, component: SecFilingComponent
) -> None:
    """Replay one exact child-document span from an issuer GCS merged page."""
    parent_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    if (
        component.parser_revision not in ISSUER_GCS_MERGED_COMPONENT_REVISIONS
        or component.parent_source_sha256 != parent_sha256
        or component.char_start < 0
        or component.char_end <= component.char_start
        or component.char_end > len(source_text)
    ):
        raise ProvenanceError("issuer GCS component lineage mismatch")
    block = source_text[component.char_start : component.char_end]
    if not block.startswith("<html") or not block.endswith("</html>"):
        raise ProvenanceError("issuer GCS component boundary mismatch")
    if component.parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION:
        titles = _GCS_DOCUMENT_TITLE.findall(block)
        if titles != [component.component_type]:
            raise ProvenanceError("issuer GCS component title mismatch")
    else:
        _validate_issuer_gcs_v2_component_identity(
            block,
            component_type=component.component_type,
            filename=component.filename,
        )
    component_sha256 = hashlib.sha256(block.encode()).hexdigest()
    if component.component_sha256 != component_sha256:
        raise ProvenanceError("issuer GCS component hash mismatch")
    expected = _issuer_gcs_component_provenance(
        component_type=component.component_type,
        sequence=component.sequence,
        filename=component.filename,
        char_start=component.char_start,
        char_end=component.char_end,
        parent_source_sha256=parent_sha256,
        component_sha256=component_sha256,
        parser_revision=component.parser_revision,
    )
    if component.provenance_id != expected:
        raise ProvenanceError("issuer GCS component provenance mismatch")


def _gcs_v2_title(block: str) -> str:
    titles = [
        re.sub(r"\s+", " ", title).strip()
        for title in _GCS_V2_DOCUMENT_TITLE.findall(block)
    ]
    if len(titles) != 1 or not titles[0]:
        raise ProvenanceError("issuer GCS v2 component title is missing or ambiguous")
    return titles[0]


def _gcs_v2_filename_type(filename: str) -> str | None:
    for component_type in SEC_EXTRACTABLE_COMPONENT_TYPES[1:]:
        major, minor = component_type.removeprefix("EX-").split(".")
        if re.fullmatch(
            rf"msft-ex{major}_?{minor}(?:_\d+)?\.htm", filename, re.IGNORECASE
        ):
            return component_type
    return None


def _gcs_v2_visible_exhibit_types(block: str) -> list[str]:
    visible = unescape(re.sub(r"<[^>]+>", " ", block))
    return [f"EX-{value.upper()}" for value in _GCS_V2_EXHIBIT_HEADING.findall(visible)]


def _validate_issuer_gcs_v2_component_identity(
    block: str, *, component_type: str, filename: str
) -> None:
    title = _gcs_v2_title(block)
    if component_type == "10-K":
        if title not in {"10-K", filename}:
            raise ProvenanceError("issuer GCS v2 main title does not bind filename")
        form_facts = _GCS_FORM_FACT.findall(block)
        if (
            len(form_facts) != 1
            or re.sub(r"<[^>]+>", "", form_facts[0]).strip() != "10-K"
        ):
            raise ProvenanceError("issuer GCS v2 main form binding is invalid")
        return

    filename_type = _gcs_v2_filename_type(filename)
    title_type = title if title in SEC_EXTRACTABLE_COMPONENT_TYPES else None
    if title == filename:
        title_type = filename_type
    heading_types = _gcs_v2_visible_exhibit_types(block)
    if (
        filename_type != component_type
        or title_type != component_type
        or heading_types != [component_type]
    ):
        raise ProvenanceError(
            "issuer GCS v2 exhibit title, heading, and filename are inconsistent"
        )


def _parse_issuer_gcs_merged_components_v2(
    source_text: str,
    *,
    expected_primary_document: str,
    xbrl_start: int,
    xbrl_end: int,
    outer_html_end: int,
) -> tuple[SecFilingComponent, ...]:
    main_segment = source_text[xbrl_start:xbrl_end]
    main_starts = list(re.finditer(r"<html(?:\s[^>]*)?>", main_segment, re.IGNORECASE))
    main_ends = list(re.finditer(r"</html\s*>", main_segment, re.IGNORECASE))
    if (
        len(main_starts) != 1
        or len(main_ends) != 1
        or main_ends[0].start() <= main_starts[0].start()
    ):
        raise ProvenanceError("issuer GCS v2 main document boundary is invalid")
    main_start = xbrl_start + main_starts[0].start()
    main_end = xbrl_start + main_ends[0].end()
    main_block = source_text[main_start:main_end]
    _validate_issuer_gcs_v2_component_identity(
        main_block, component_type="10-K", filename=expected_primary_document
    )
    title = _gcs_v2_title(main_block)
    legacy_primary = _GCS_V2_LEGACY_PRIMARY.fullmatch(expected_primary_document)
    if title == expected_primary_document and legacy_primary is not None:
        expected_schema_stem = f"msft-{legacy_primary.group(1)}"
    else:
        expected_schema_stem = expected_primary_document.removesuffix(".htm")
    if set(_GCS_SCHEMA_REF.findall(main_block)) != {expected_schema_stem}:
        raise ProvenanceError(
            "issuer GCS v2 primary document binding is missing or ambiguous"
        )

    located: list[tuple[str, str, int, int]] = [
        ("10-K", expected_primary_document, main_start, main_end)
    ]
    seen_types = {"10-K"}
    for anchor in _GCS_V2_DOCUMENT_ANCHOR.finditer(
        source_text, xbrl_end, outer_html_end
    ):
        filename = anchor.group(1)
        char_start = anchor.start("html")
        close = source_text.find("</html>", anchor.end("html"), outer_html_end)
        if close < 0:
            raise ProvenanceError("issuer GCS v2 child document boundary is invalid")
        char_end = close + len("</html>")
        block = source_text[char_start:char_end]
        filename_type = _gcs_v2_filename_type(filename)
        heading_types = _gcs_v2_visible_exhibit_types(block)
        title = _gcs_v2_title(block)
        if filename_type is None:
            if heading_types or title in SEC_EXTRACTABLE_COMPONENT_TYPES:
                raise ProvenanceError("issuer GCS v2 exhibit filename is invalid")
            continue
        _validate_issuer_gcs_v2_component_identity(
            block, component_type=filename_type, filename=filename
        )
        if filename_type in seen_types:
            raise ProvenanceError(
                f"issuer GCS component identity is duplicated: {[filename_type]}"
            )
        located.append((filename_type, filename, char_start, char_end))
        seen_types.add(filename_type)

    missing = sorted(set(SEC_REQUIRED_COMPONENT_TYPES) - seen_types)
    if missing:
        raise ProvenanceError(
            f"issuer GCS merged page is missing components: {missing}"
        )
    parent_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    components: list[SecFilingComponent] = []
    for sequence, (component_type, filename, char_start, char_end) in enumerate(
        located, start=1
    ):
        component_sha256 = hashlib.sha256(
            source_text[char_start:char_end].encode()
        ).hexdigest()
        component = SecFilingComponent(
            component_type=component_type,
            sequence=sequence,
            filename=filename,
            char_start=char_start,
            char_end=char_end,
            parent_source_sha256=parent_sha256,
            component_sha256=component_sha256,
            provenance_id=_issuer_gcs_component_provenance(
                component_type=component_type,
                sequence=sequence,
                filename=filename,
                char_start=char_start,
                char_end=char_end,
                parent_source_sha256=parent_sha256,
                component_sha256=component_sha256,
                parser_revision=ISSUER_GCS_MERGED_COMPONENT_REVISION_V2,
            ),
            parser_revision=ISSUER_GCS_MERGED_COMPONENT_REVISION_V2,
        )
        validate_issuer_gcs_merged_component(source_text, component)
        components.append(component)
    return tuple(components)


def parse_issuer_gcs_merged_components(
    source_text: str,
    *,
    expected_accession: str,
    expected_cik: str,
    expected_form: str,
    expected_primary_document: str,
    parser_revision: str = ISSUER_GCS_MERGED_COMPONENT_REVISION,
) -> tuple[SecFilingComponent, ...]:
    """Parse exact child documents without representing issuer bytes as SEC SGML."""
    if (
        _ACCESSION.fullmatch(expected_accession) is None
        or _CIK.fullmatch(expected_cik) is None
        or expected_form != "10-K"
        or _PRIMARY_DOCUMENT.fullmatch(expected_primary_document) is None
        or parser_revision not in ISSUER_GCS_MERGED_COMPONENT_REVISIONS
    ):
        raise ProvenanceError("issuer GCS expected filing identity is invalid")
    xbrl_starts = [match.start() for match in re.finditer(r"<XBRL>", source_text)]
    xbrl_ends = [match.start() for match in re.finditer(r"</XBRL>", source_text)]
    if len(xbrl_starts) != 1 or len(xbrl_ends) != 1 or xbrl_ends[0] <= xbrl_starts[0]:
        raise ProvenanceError("issuer GCS merged XBRL boundary is invalid")
    xbrl_start, xbrl_end = xbrl_starts[0], xbrl_ends[0]
    accession_link = (
        f"/sec-filings/sec-filing/{expected_form.lower()}/{expected_accession}"
    )
    if accession_link not in source_text[:xbrl_start] or (
        f"{expected_accession}.pdf" not in source_text[:xbrl_start]
    ):
        raise ProvenanceError("issuer GCS accession binding is missing")
    cik_values = set(_GCS_CIK_FACT.findall(source_text[xbrl_start:xbrl_end]))
    if cik_values != {expected_cik}:
        raise ProvenanceError("issuer GCS CIK binding is missing or ambiguous")

    outer_html_end = _issuer_gcs_outer_html_end(source_text)
    if parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION_V2:
        return _parse_issuer_gcs_merged_components_v2(
            source_text,
            expected_primary_document=expected_primary_document,
            xbrl_start=xbrl_start,
            xbrl_end=xbrl_end,
            outer_html_end=outer_html_end,
        )
    title_matches = list(
        _GCS_DOCUMENT_TITLE.finditer(source_text, xbrl_start, outer_html_end)
    )
    recognized = set(SEC_EXTRACTABLE_COMPONENT_TYPES)
    selected = [match for match in title_matches if match.group(1) in recognized]
    title_counts = {
        title: sum(match.group(1) == title for match in selected)
        for title in recognized
    }
    duplicated = sorted(title for title, count in title_counts.items() if count > 1)
    if duplicated:
        raise ProvenanceError(
            f"issuer GCS component identity is duplicated: {duplicated}"
        )
    missing = sorted(
        title for title in SEC_REQUIRED_COMPONENT_TYPES if title_counts[title] != 1
    )
    if missing:
        raise ProvenanceError(
            f"issuer GCS merged page is missing components: {missing}"
        )

    if any(
        (match.group(1) == "10-K") != (match.start() < xbrl_end) for match in selected
    ):
        raise ProvenanceError("issuer GCS component placement is invalid")

    main_match = next(match for match in selected if match.group(1) == "10-K")
    main_start = source_text.rfind("<html", xbrl_start, main_match.start())
    main_close = source_text.find("</html>", main_match.end(), xbrl_end)
    main_end = main_close + len("</html>")
    if main_start < xbrl_start or main_close < 0 or main_end > xbrl_end:
        raise ProvenanceError("issuer GCS main document boundary is invalid")
    main_block = source_text[main_start:main_end]
    form_facts = _GCS_FORM_FACT.findall(main_block)
    if len(form_facts) != 1 or re.sub(r"<[^>]+>", "", form_facts[0]).strip() != (
        expected_form
    ):
        raise ProvenanceError("issuer GCS form binding is missing or ambiguous")
    schema_stems = set(_GCS_SCHEMA_REF.findall(main_block))
    expected_stem = expected_primary_document.removesuffix(".htm")
    if schema_stems != {expected_stem}:
        raise ProvenanceError(
            "issuer GCS primary document binding is missing or ambiguous"
        )

    parent_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    components: list[SecFilingComponent] = []
    for sequence, match in enumerate(selected, start=1):
        component_type = match.group(1)
        lower_bound = xbrl_start if component_type == "10-K" else xbrl_end
        upper_bound = xbrl_end if component_type == "10-K" else outer_html_end
        char_start = source_text.rfind("<html", lower_bound, match.start())
        close = source_text.find("</html>", match.end(), upper_bound)
        char_end = close + len("</html>")
        if char_start < lower_bound or close < 0 or char_end > upper_bound:
            raise ProvenanceError("issuer GCS child document boundary is invalid")
        if component_type == "10-K":
            filename = expected_primary_document
        else:
            anchor_area = source_text[max(xbrl_start, char_start - 512) : char_start]
            anchor = _GCS_DOCUMENT_ANCHOR.search(anchor_area)
            if anchor is None:
                raise ProvenanceError("issuer GCS child document filename is missing")
            filename = anchor.group(1)
        component_sha256 = hashlib.sha256(
            source_text[char_start:char_end].encode()
        ).hexdigest()
        component = SecFilingComponent(
            component_type=component_type,
            sequence=sequence,
            filename=filename,
            char_start=char_start,
            char_end=char_end,
            parent_source_sha256=parent_sha256,
            component_sha256=component_sha256,
            provenance_id=_issuer_gcs_component_provenance(
                component_type=component_type,
                sequence=sequence,
                filename=filename,
                char_start=char_start,
                char_end=char_end,
                parent_source_sha256=parent_sha256,
                component_sha256=component_sha256,
            ),
            parser_revision=ISSUER_GCS_MERGED_COMPONENT_REVISION,
        )
        validate_issuer_gcs_merged_component(source_text, component)
        components.append(component)
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


def _validate_sec_identity(
    filing: dict[str, Any], *, parser: str = ""
) -> tuple[str, str, str, str, str, str]:
    accession = str(filing.get("accession") or "")
    cik = str(filing.get("cik") or "")
    form = str(filing.get("form") or "")
    filing_date = str(filing.get("filing_date") or "")
    primary_document = str(filing.get("primary_document") or "")
    source_url = str(filing.get("source_url") or "")
    if _ACCESSION.fullmatch(accession) is None:
        raise ProvenanceError("SEC filing accession is missing or invalid")
    if _CIK.fullmatch(cik) is None:
        raise ProvenanceError("SEC filing CIK must contain ten digits")
    if _FORM.fullmatch(form) is None:
        raise ProvenanceError("SEC filing form is missing or invalid")
    if primary_document and (
        _PRIMARY_DOCUMENT.fullmatch(primary_document) is None
        or Path(primary_document).name != primary_document
    ):
        raise ProvenanceError("SEC filing primary document is invalid")
    try:
        date.fromisoformat(filing_date)
    except ValueError as exc:
        raise ProvenanceError("SEC filing date must be ISO-8601") from exc
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parser in ISSUER_GCS_MERGED_COMPONENT_REVISIONS:
        if (
            parsed.scheme != "https"
            or host != "microsoft.gcs-web.com"
            or re.fullmatch(r"/node/[1-9]\d{0,19}/html", parsed.path) is None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ProvenanceError("issuer GCS merged source URL is invalid")
    elif parsed.scheme != "https" or not (
        host == "sec.gov" or host.endswith(".sec.gov")
    ):
        raise ProvenanceError("SEC filing source URL must be an HTTPS sec.gov URL")
    return accession, cik, form, filing_date, primary_document, source_url


def _source_path(base_directory: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("SEC filing source file is missing")
    relative = Path(value)
    if (
        relative.name != value
        or relative.suffix.lower() not in {".html", ".json", ".txt"}
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


def _validate_source_status_parser(source_status: str, parser: str) -> None:
    if (parser in ISSUER_GCS_MERGED_COMPONENT_REVISIONS) != (
        source_status == "issuer_owned_public_export"
    ):
        raise ProvenanceError("SEC source status and parser are inconsistent")


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
    cik: str,
    form: str,
    filing_date: str,
    report_date: str,
    primary_document: str,
) -> None:
    values = {fact["field"]: fact["value"] for fact in facts}
    expected = {
        "accession": accession,
        "cik": cik,
        "form": form,
        "filing_date": filing_date.replace("-", ""),
        "primary_document": primary_document,
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
            field not in SEC_ANNUAL_IDENTITY_FIELDS
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
    parsed_fields = frozenset(parsed)
    if parsed_fields not in {
        frozenset(SEC_IDENTITY_FIELDS),
        frozenset(SEC_ANNUAL_IDENTITY_FIELDS),
    }:
        return None
    if (
        _ACCESSION.fullmatch(parsed["accession"]) is None
        or ("cik" in parsed and _CIK.fullmatch(parsed["cik"]) is None)
        or _FORM.fullmatch(parsed["form"]) is None
        or not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", parsed["filing_date"])
        or not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", parsed["report_date"])
    ):
        return None
    try:
        raw_filing_date = parsed["filing_date"].replace("-", "")
        raw_report_date = parsed["report_date"].replace("-", "")
        filing_date = date.fromisoformat(
            f"{raw_filing_date[:4]}-{raw_filing_date[4:6]}-{raw_filing_date[6:]}"
        )
        report_date = date.fromisoformat(
            f"{raw_report_date[:4]}-{raw_report_date[4:6]}-{raw_report_date[6:]}"
        )
    except ValueError:
        return None
    identity = {
        "accession": parsed["accession"],
        "form": parsed["form"],
        "filing_date": filing_date.isoformat(),
        "report_date": report_date.isoformat(),
    }
    if "cik" in parsed:
        identity["cik"] = parsed["cik"]
    return identity


def _has_grounded_annual_identity(filing: dict[str, Any]) -> bool:
    facts = filing.get("derived_facts")
    if not isinstance(facts, list) or not all(isinstance(fact, dict) for fact in facts):
        return False
    by_field: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_field.setdefault(str(fact.get("field") or ""), []).append(fact)
    if any(len(by_field.get(field, ())) != 1 for field in SEC_ANNUAL_RELATION_FIELDS):
        return False
    return (
        str(by_field["cik"][0].get("value") or "") == str(filing.get("cik") or "")
        and str(by_field["form"][0].get("value") or "") == str(filing.get("form") or "")
        and str(by_field["filing_date"][0].get("value") or "").replace("-", "")
        == str(filing.get("filing_date") or "").replace("-", "")
        and str(by_field["report_date"][0].get("value") or "").replace("-", "")
        == str(filing.get("report_date") or "").replace("-", "")
    )


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

    annual_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for filing in filings:
        form = str(filing.get("form") or "")
        if form != "10-K" or not _has_grounded_annual_identity(filing):
            continue
        annual_groups.setdefault((str(filing.get("cik") or ""), form), []).append(
            filing
        )
    for annual_filings in annual_groups.values():
        try:
            ordered = sorted(
                annual_filings,
                key=lambda filing: (
                    date.fromisoformat(str(filing["report_date"])),
                    date.fromisoformat(str(filing["filing_date"])),
                    str(filing["record_id"]),
                ),
            )
        except (KeyError, ValueError):
            continue
        report_dates = [str(filing["report_date"]) for filing in ordered]
        if len(set(report_dates)) != len(report_dates):
            continue
        for prior, current in pairwise(ordered):
            if date.fromisoformat(str(current["report_date"])) <= date.fromisoformat(
                str(prior["report_date"])
            ) or date.fromisoformat(str(current["filing_date"])) <= date.fromisoformat(
                str(prior["filing_date"])
            ):
                continue
            relations.append(
                {
                    "relation_id": (
                        f"{current['record_id']}:prior_annual:{prior['record_id']}"
                    ),
                    "relation_type": "prior_annual_filing",
                    "from_record_id": current["record_id"],
                    "to_record_id": prior["record_id"],
                    "current_report_date": current["report_date"],
                    "prior_report_date": prior["report_date"],
                    "derivation_rule": (
                        "adjacent_same_cik_same_form_grounded_annual_filings"
                    ),
                    "evidence": [
                        {
                            "record_id": current["record_id"],
                            "fact_ids": list(SEC_ANNUAL_RELATION_FIELDS),
                        },
                        {
                            "record_id": prior["record_id"],
                            "fact_ids": list(SEC_ANNUAL_RELATION_FIELDS),
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
            or facts_by_field["filing_date"][0].value.replace("-", "")
            != str(attributes.get("filing_date", "")).replace("-", "")
            or facts_by_field["report_date"][0].value.replace("-", "")
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


def validated_sec_annual_endpoints(
    workflow: Any, relation: Any
) -> tuple[Any, Any] | None:
    """Return current/prior annual records only for an exact grounded SEC edge."""
    if (
        getattr(workflow, "source_kind", "") != "sec_filing"
        or not getattr(relation, "relation_id", "")
        or getattr(relation, "kind", "") != "prior_annual_filing"
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
    current = records.get(relation.source_record_id)
    prior = records.get(relation.target_record_id)
    if current is None or prior is None:
        return None
    if (
        any(
            getattr(getattr(record, "source_origin", None), "value", "")
            not in {"real_public", "real_private_export"}
            or record.provenance_id != f"sha256:{record.source_sha256}"
            or hashlib.sha256(record.text.encode()).hexdigest() != record.text_sha256
            for record in (current, prior)
        )
        or current.source_sha256 == prior.source_sha256
    ):
        return None

    attributes_by_record = {
        record.record_id: dict(record.attributes) for record in (current, prior)
    }
    for record in (current, prior):
        attributes = attributes_by_record[record.record_id]
        facts_by_field = {
            field: [fact for fact in record.facts if fact.field == field]
            for field in SEC_ANNUAL_IDENTITY_FIELDS
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
            facts_by_field["cik"][0].value != attributes.get("cik")
            or facts_by_field["accession"][0].value != attributes.get("accession")
            or facts_by_field["form"][0].value != attributes.get("form")
            or facts_by_field["filing_date"][0].value.replace("-", "")
            != str(attributes.get("filing_date", "")).replace("-", "")
            or facts_by_field["report_date"][0].value.replace("-", "")
            != str(attributes.get("report_date", "")).replace("-", "")
            or record.record_id != f"sec:{attributes.get('accession', '')}"
        ):
            return None

    current_attributes = attributes_by_record[current.record_id]
    prior_attributes = attributes_by_record[prior.record_id]
    try:
        current_filing_day = date.fromisoformat(
            str(current_attributes.get("filing_date") or "")
        )
        prior_filing_day = date.fromisoformat(
            str(prior_attributes.get("filing_date") or "")
        )
        current_report_day = date.fromisoformat(
            str(current_attributes.get("report_date") or "")
        )
        prior_report_day = date.fromisoformat(
            str(prior_attributes.get("report_date") or "")
        )
        intermediate = [
            record
            for record in raw_records
            if record.record_id not in {current.record_id, prior.record_id}
            and record.attribute("cik") == current_attributes.get("cik")
            and record.attribute("form") == current_attributes.get("form")
            and prior_report_day
            < date.fromisoformat(record.attribute("report_date"))
            < current_report_day
        ]
    except ValueError:
        return None
    if (
        relation.relation_id != f"{current.record_id}:prior_annual:{prior.record_id}"
        or current_attributes.get("cik") != prior_attributes.get("cik")
        or current_attributes.get("form") != "10-K"
        or prior_attributes.get("form") != "10-K"
        or current_report_day <= prior_report_day
        or current_filing_day <= prior_filing_day
        or current.occurred_at[:10] != current_filing_day.isoformat()
        or prior.occurred_at[:10] != prior_filing_day.isoformat()
        or intermediate
    ):
        return None

    facts = {
        (record.record_id, fact.fact_id): fact
        for record in (current, prior)
        for fact in record.facts
    }
    required = {
        (record.record_id, field)
        for record in (current, prior)
        for field in SEC_ANNUAL_RELATION_FIELDS
    }
    grounded: set[tuple[str, str]] = set()
    if len(relation.evidence) != len(required):
        return None
    for evidence in relation.evidence:
        record = records.get(evidence.record_id)
        if (
            evidence.record_id not in {current.record_id, prior.record_id}
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
            or fact.field not in set(SEC_ANNUAL_RELATION_FIELDS)
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
    return (current, prior) if grounded == required else None


def sec_multi_span_provenance_id(
    *,
    parent_provenance_id: str,
    parent_source_sha256: str,
    parent_text_sha256: str,
    source_ranges: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "operation": SEC_MULTI_SPAN_PROJECTION_REVISION,
        "parent_provenance_id": parent_provenance_id,
        "parent_source_sha256": parent_source_sha256,
        "parent_text_sha256": parent_text_sha256,
        "source_ranges": [
            {
                key: item[key]
                for key in (
                    "source_char_start",
                    "source_char_end",
                    "projected_char_start",
                    "projected_char_end",
                    "text_sha256",
                )
            }
            for item in source_ranges
        ],
    }
    return (
        "derived-sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _sec_multi_span_event_matches_record(
    params: Mapping[str, Any], record: Any
) -> bool:
    text = str(params.get("text") or "")
    ranges = params.get("source_ranges")
    if (
        not isinstance(ranges, Sequence)
        or isinstance(ranges, (str, bytes))
        or not ranges
        or len(ranges) > len(SEC_ANNUAL_IDENTITY_FIELDS)
        or params.get("source_origin") != "real_derived"
        or params.get("parent_provenance_id") != record.provenance_id
        or params.get("provenance_operation") != SEC_MULTI_SPAN_PROJECTION_REVISION
    ):
        return False
    expected_keys = {
        "source_char_start",
        "source_char_end",
        "projected_char_start",
        "projected_char_end",
        "text",
        "text_sha256",
        "parent_source_sha256",
        "parent_text_sha256",
    }
    previous_source_end = 0
    previous_projected_end = 0
    for raw in ranges:
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            return False
        source_start = raw.get("source_char_start")
        source_end = raw.get("source_char_end")
        projected_start = raw.get("projected_char_start")
        projected_end = raw.get("projected_char_end")
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (source_start, source_end, projected_start, projected_end)
        ):
            return False
        assert isinstance(source_start, int)
        assert isinstance(source_end, int)
        assert isinstance(projected_start, int)
        assert isinstance(projected_end, int)
        segment = str(raw.get("text") or "")
        if (
            source_start < previous_source_end
            or source_end <= source_start
            or source_end > len(record.text)
            or projected_start != previous_projected_end
            or projected_end != projected_start + len(segment)
            or record.text[source_start:source_end] != segment
            or text[projected_start:projected_end] != segment
            or hashlib.sha256(segment.encode()).hexdigest() != raw.get("text_sha256")
            or raw.get("parent_source_sha256") != record.source_sha256
            or raw.get("parent_text_sha256") != record.text_sha256
        ):
            return False
        previous_source_end = source_end
        previous_projected_end = projected_end
    if previous_projected_end != len(text):
        return False
    return params.get("provenance_id") == sec_multi_span_provenance_id(
        parent_provenance_id=record.provenance_id,
        parent_source_sha256=record.source_sha256,
        parent_text_sha256=record.text_sha256,
        source_ranges=ranges,
    )


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
    ):
        return False
    multi_span = params.get("provenance_operation") == (
        SEC_MULTI_SPAN_PROJECTION_REVISION
    )
    if multi_span:
        if not _sec_multi_span_event_matches_record(params, record):
            return False
        corridor_start = None
    else:
        if record.text.count(text) != 1:
            return False
        corridor_start = record.text.index(text)
    fact_spans = params.get("fact_spans")
    if (
        not isinstance(fact_spans, Sequence)
        or isinstance(fact_spans, (str, bytes))
        or len(fact_spans)
        not in {
            len(SEC_IDENTITY_FIELDS),
            len(SEC_ANNUAL_IDENTITY_FIELDS),
        }
        or not all(isinstance(span, Mapping) for span in fact_spans)
    ):
        return False
    observed_fields = {str(span.get("field") or "") for span in fact_spans}
    expected_fields = (
        set(SEC_ANNUAL_IDENTITY_FIELDS)
        if observed_fields == set(SEC_ANNUAL_IDENTITY_FIELDS)
        else set(SEC_IDENTITY_FIELDS)
    )
    if observed_fields != expected_fields:
        return False
    facts_by_field = {
        field: [fact for fact in record.facts if fact.field == field]
        for field in expected_fields
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
        if corridor_start is None:
            matching_range = next(
                (
                    item
                    for item in params["source_ranges"]
                    if item["source_char_start"] <= fact.char_start
                    and fact.char_end <= item["source_char_end"]
                ),
                None,
            )
            if matching_range is None:
                return False
            expected_start = int(matching_range["projected_char_start"]) + (
                fact.char_start - int(matching_range["source_char_start"])
            )
        else:
            expected_start = fact.char_start - corridor_start
        expected_end = expected_start + len(fact.evidence_quote)
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
    if seen_fields != expected_fields:
        return False

    if multi_span:
        return True

    assert corridor_start is not None
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
                endpoints = validated_sec_annual_endpoints(workflow, relation)
            if endpoints is None:
                continue
            child, parent = endpoints
            if {
                (workflow_id, child.record_id),
                (workflow_id, parent.record_id),
            } - selected.keys():
                continue
            if any(
                not any(
                    _sec_source_event_matches_record(event, workflow_records[record_id])
                    for event in selected[(workflow_id, record_id)]
                )
                for record_id in (child.record_id, parent.record_id)
            ):
                continue
            edges.append(
                {
                    "parent_record_id": parent.record_id,
                    "child_record_id": child.record_id,
                    "relation": relation.kind,
                    "relation_provenance": "authentic_source",
                    "parent_source_url": parent.source_url,
                    "child_source_url": child.source_url,
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


def _issuer_gcs_detail_value(text: str, label: str) -> str:
    match = re.search(
        rf'<div class="field__label">{re.escape(label)}</div>\s*'
        r'<div class="field__item">(.*?)</div>',
        text,
        re.DOTALL,
    )
    if match is None:
        raise ProvenanceError(f"issuer GCS detail is missing {label}")
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _validate_issuer_gcs_acquisition(
    filing: dict[str, Any], *, merged_text: str, detail_text: str, parser_revision: str
) -> dict[str, Any]:
    if _GCS_JSON_EXPANDING_CONTROL.search(
        merged_text
    ) or _GCS_JSON_EXPANDING_CONTROL.search(detail_text):
        raise ProvenanceError("issuer GCS source contains unsupported control text")
    accession = str(filing.get("accession") or "")
    cik = str(filing.get("cik") or "")
    form = str(filing.get("form") or "")
    filing_date = str(filing.get("filing_date") or "")
    report_date = str(filing.get("report_date") or "")
    primary_document = str(filing.get("primary_document") or "")
    merged_url = str(filing.get("source_url") or "")
    detail_url = str(filing.get("detail_url") or "")
    detail_retrieval_url = str(filing.get("detail_retrieval_url") or detail_url)
    expected_detail_url = (
        f"https://microsoft.gcs-web.com/sec-filings/sec-filing/"
        f"{form.lower()}/{accession}"
    )
    merged_path = urlparse(merged_url).path
    if (
        detail_url != expected_detail_url
        or detail_retrieval_url not in {detail_url, f"{detail_url}?output=1"}
        or f'<link rel="canonical" href="{expected_detail_url}"' not in detail_text
        or f'href="{merged_path}"' not in detail_text
        or f"<title>{accession} | {form} | Microsoft Corporation</title>"
        not in detail_text
    ):
        raise ProvenanceError("issuer GCS detail identity binding is invalid")
    if _issuer_gcs_detail_value(detail_text, "Form").strip() != form:
        raise ProvenanceError("issuer GCS detail form binding is invalid")
    try:
        observed_filing = time.strptime(
            _issuer_gcs_detail_value(detail_text, "Filing Date"), "%b %d, %Y"
        )
        observed_report = time.strptime(
            _issuer_gcs_detail_value(detail_text, "Document Date"), "%b %d, %Y"
        )
    except ValueError as exc:
        raise ProvenanceError("issuer GCS detail date binding is invalid") from exc
    if date(
        observed_filing.tm_year, observed_filing.tm_mon, observed_filing.tm_mday
    ) != date.fromisoformat(filing_date) or date(
        observed_report.tm_year, observed_report.tm_mon, observed_report.tm_mday
    ) != date.fromisoformat(report_date):
        raise ProvenanceError("issuer GCS detail date binding is invalid")
    parse_issuer_gcs_merged_components(
        merged_text,
        expected_accession=accession,
        expected_cik=cik,
        expected_form=form,
        expected_primary_document=primary_document,
        parser_revision=parser_revision,
    )
    facts = filing.get("derived_facts")
    if not isinstance(facts, list):
        raise ProvenanceError("issuer GCS identity facts are invalid")
    values = {
        str(fact.get("field") or ""): str(fact.get("value") or "") for fact in facts
    }
    expected_values = {
        "accession": accession,
        "cik": cik,
        "form": form,
        "filing_date": filing_date,
        "report_date": report_date,
        "primary_document_stem": primary_document.removesuffix(".htm"),
    }
    if any(values.get(field) != value for field, value in expected_values.items()):
        raise ProvenanceError("issuer GCS identity facts do not bind filing")
    receipt = {
        "parser_revision": parser_revision,
        "source_class": "issuer_owned_rendered_filing_not_sec_archives_original",
        "detail_url": detail_url,
        "detail_retrieval_url": detail_retrieval_url,
        "detail_sha256": hashlib.sha256(detail_text.encode()).hexdigest(),
        "detail_text": detail_text,
        "merged_url": merged_url,
        "merged_sha256": hashlib.sha256(merged_text.encode()).hexdigest(),
    }
    if parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION_V2:
        receipt["detail_file"] = str(filing.get("detail_file") or "")
    return receipt


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
        retrieved_at, access_policy, parser = _source_lineage(item)
        _validate_source_status_parser(source_status, parser)
        accession, cik, form, filing_date, primary_document, source_url = (
            _validate_sec_identity(item, parser=parser)
        )
        report_date = _report_date(item)
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
        gcs_acquisition = None
        if source_status == "public_sec_download" or parser == (
            "sec_complete_submission_header@1"
        ):
            canonical_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}/{accession}.txt"
            )
            if source_url != canonical_url:
                raise ProvenanceError("SEC public source URL is not canonical")
            _validate_public_identity_facts(
                facts,
                accession=accession,
                cik=cik,
                form=form,
                filing_date=filing_date,
                report_date=report_date,
                primary_document=primary_document,
            )
        elif (
            source_status == "issuer_owned_public_export"
            and parser in ISSUER_GCS_MERGED_COMPONENT_REVISIONS
        ):
            detail_path = _source_path(base_directory, item.get("detail_file"))
            try:
                detail_raw = _read_regular_file(
                    detail_path, MAX_ISSUER_GCS_DETAIL_BYTES
                )
                detail_text = detail_raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ProvenanceError(
                    f"cannot read issuer GCS detail source: {exc}"
                ) from exc
            detail_sha256 = str(item.get("detail_sha256") or "")
            if (
                _SHA256.fullmatch(detail_sha256) is None
                or hashlib.sha256(detail_raw).hexdigest() != detail_sha256
            ):
                raise ProvenanceError("issuer GCS detail source hash mismatch")
            gcs_item = {**item, "derived_facts": facts}
            gcs_acquisition = _validate_issuer_gcs_acquisition(
                gcs_item,
                merged_text=clean_text,
                detail_text=detail_text,
                parser_revision=parser,
            )
            if gcs_acquisition["detail_sha256"] != detail_sha256:
                raise ProvenanceError("issuer GCS detail source hash mismatch")
        elif source_status == "issuer_owned_public_export":
            raise ProvenanceError("issuer-owned export requires GCS parser revision")
        filing_payload = {
            "record_id": f"sec:{accession}",
            "accession": accession,
            "cik": cik,
            "form": form,
            "filing_date": filing_date,
            "report_date": report_date,
            "primary_document": primary_document,
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
        if gcs_acquisition is not None:
            filing_payload["acquisition_receipt"] = gcs_acquisition
        filings.append(filing_payload)
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


def _replay_exported_manifest_sources(
    payload: dict[str, Any], *, base_directory: Path, manifest_name: str = ""
) -> None:
    for filing in payload["filings"]:
        source_path = _source_path(base_directory, filing.get("source_file"))
        if manifest_name and source_path.name == manifest_name:
            raise ProvenanceError("SEC filing source file conflicts with manifest")
        try:
            source_raw = _read_regular_file(source_path, MAX_SOURCE_BYTES)
            source_text = source_raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProvenanceError(f"cannot replay SEC filing source: {exc}") from exc
        if hashlib.sha256(source_raw).hexdigest() != filing["source_sha256"]:
            raise ProvenanceError("SEC filing source hash mismatch")
        if source_path.suffix.lower() == ".json":
            try:
                json.loads(source_text)
            except json.JSONDecodeError as exc:
                raise ProvenanceError("SEC JSON source is invalid") from exc
        clean_text, redaction_count = _sanitize_source_text(source_text)
        if (
            clean_text != filing["text"]
            or hashlib.sha256(clean_text.encode()).hexdigest() != filing["text_sha256"]
            or redaction_count != filing["privacy_review"]["email_redaction_count"]
        ):
            raise ProvenanceError("SEC filing sanitization replay mismatch")


def _audit_exported_manifest(
    payload: dict[str, Any],
    *,
    base_directory: Path,
    manifest_name: str = "",
) -> None:
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
        parser_name, separator, parser_version = str(
            filing.get("parser") or ""
        ).partition("@")
        if not separator or not parser_name or not parser_version:
            raise ProvenanceError("SEC manifest source parser metadata is invalid")
        parser = f"{parser_name}@{parser_version}"
        _validate_source_status_parser(str(payload.get("source_status") or ""), parser)
        accession, cik, form, filing_date, primary_document, source_url = (
            _validate_sec_identity(filing, parser=parser)
        )
        report_date = _report_date(filing)
        _parse_timestamp(str(filing.get("retrieved_at") or ""), "source retrieved_at")
        if not str(filing.get("access_policy") or "").strip():
            raise ProvenanceError("SEC manifest source access policy is invalid")
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
        if (
            payload.get("source_status") == "public_sec_download"
            or str(filing.get("parser") or "") == "sec_complete_submission_header@1"
        ):
            canonical_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}/{accession}.txt"
            )
            if source_url != canonical_url:
                raise ProvenanceError("SEC public source URL is not canonical")
            _validate_public_identity_facts(
                facts,
                accession=accession,
                cik=cik,
                form=form,
                filing_date=filing_date,
                report_date=report_date,
                primary_document=primary_document,
            )
        elif (
            payload.get("source_status") == "issuer_owned_public_export"
            and parser in ISSUER_GCS_MERGED_COMPONENT_REVISIONS
        ):
            receipt = filing.get("acquisition_receipt")
            if not isinstance(receipt, dict):
                raise ProvenanceError("issuer GCS acquisition receipt is missing")
            detail_text = receipt.get("detail_text")
            if not isinstance(detail_text, str) or not detail_text:
                raise ProvenanceError("issuer GCS detail text is missing")
            if parser == ISSUER_GCS_MERGED_COMPONENT_REVISION_V2:
                detail_path = _source_path(base_directory, receipt.get("detail_file"))
                try:
                    detail_raw = _read_regular_file(
                        detail_path, MAX_ISSUER_GCS_DETAIL_BYTES
                    )
                    replayed_detail_text = detail_raw.decode("utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise ProvenanceError(
                        f"cannot replay issuer GCS detail source: {exc}"
                    ) from exc
                if replayed_detail_text != detail_text or hashlib.sha256(
                    detail_raw
                ).hexdigest() != receipt.get("detail_sha256"):
                    raise ProvenanceError("issuer GCS detail source replay mismatch")
            replayed = _validate_issuer_gcs_acquisition(
                {
                    **filing,
                    "detail_url": receipt.get("detail_url"),
                    "detail_retrieval_url": receipt.get("detail_retrieval_url"),
                    "detail_file": receipt.get("detail_file"),
                },
                merged_text=text,
                detail_text=detail_text,
                parser_revision=parser,
            )
            if receipt != replayed:
                raise ProvenanceError("issuer GCS acquisition receipt mismatch")
        elif payload.get("source_status") == "issuer_owned_public_export":
            raise ProvenanceError("issuer-owned export requires GCS parser revision")
        seen_accessions.add(accession)
        seen_hashes.add(source_sha256)
    if payload.get("filing_relations") != _filing_relations(filings):
        raise ProvenanceError("SEC manifest filing relations are invalid")
    _validate_final_payload(payload)
    _replay_exported_manifest_sources(
        payload,
        base_directory=base_directory,
        manifest_name=manifest_name,
    )


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
    _audit_exported_manifest(
        payload,
        base_directory=path.parent,
        manifest_name=path.name,
    )
    return payload
