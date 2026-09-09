"""Explicit issuer-host inline-XBRL source contract, separate from Q4 rendered tables."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

from longworld.core.attestation import attestation_key_from_env, verify_attestation
from longworld.core.filingworkflow import _EMAIL, _reject_secrets
from longworld.core.issuerfilingworkflow import (
    _JWT_SHAPED,
    IssuerIrMetricFact,
    IssuerIrRenderedProgram,
    _visible_cell_text,
)
from longworld.core.provenance import ProvenanceError

AMD_CIK = "0000002488"
AMD_INLINE_PROFILE = "amd.inline-cash-components.v1"
ISSUER_INLINE_PROVIDER = "equisolve.issuer-host-inline-xbrl.v1"
ISSUER_INLINE_REQUEST_SCHEMA = "longworld.issuer-inline-xbrl-fetch-request.v1"
ISSUER_INLINE_INVENTORY_SCHEMA = "longworld.issuer-inline-xbrl-inventory.v1"
ISSUER_INLINE_MANIFEST_SCHEMA = "longworld.issuer-inline-xbrl-manifest.v1"
ISSUER_INLINE_SOURCE_FAMILY = "issuer_owned_inline_xbrl"
INLINE_SOURCE_NORMALIZATION = "issuer-inline-public-contact-redaction.v1"
AMD_HOST = "ir.amd.com"
AMD_LISTING_PATH = "/financial-information/sec-filings"
AMD_ARTIFACT_PREFIX = AMD_LISTING_PATH + "/content/"
INLINE_ROLES = {
    "revenue": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "operating_income": "us-gaap:OperatingIncomeLoss",
    "assets": "us-gaap:Assets",
    "liabilities_and_equity": "us-gaap:LiabilitiesAndStockholdersEquity",
    "cash_from_operations": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    "cash_from_investing": "us-gaap:NetCashProvidedByUsedInInvestingActivities",
    "cash_from_financing": "us-gaap:NetCashProvidedByUsedInFinancingActivities",
    "cash_period_change": "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
}
_INLINE_TOKEN = re.compile(r"<(/?)ix:(nonfraction|nonnumeric)\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"([A-Za-z_:][A-Za-z0-9_:.-]*)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
_CONTEXT = re.compile(r"<xbrli:context\b([^>]*)>(.*?)</xbrli:context>", re.IGNORECASE | re.DOTALL)
_UNIT = re.compile(r"<xbrli:unit\b([^>]*)>(.*?)</xbrli:unit>", re.IGNORECASE | re.DOTALL)
_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _InlineElement:
    kind: str
    attrs: dict[str, str]
    start: int
    content_start: int
    content_end: int
    end: int


def _attrs(raw: str) -> dict[str, str]:
    if _ATTR.sub("", raw).strip() not in {"", "/"}:
        raise ProvenanceError("inline XBRL attributes must use quoted XML values")
    result: dict[str, str] = {}
    for match in _ATTR.finditer(raw):
        key = match[1].casefold()
        if key in result:
            raise ProvenanceError("inline XBRL attribute is duplicated")
        result[key] = html.unescape(match[3])
    return result


def _elements(text: str) -> list[_InlineElement]:
    stack: list[tuple[str, dict[str, str], int, int]] = []
    result: list[_InlineElement] = []
    ids: set[str] = set()
    for token in _INLINE_TOKEN.finditer(text):
        kind = token[2].casefold()
        if not token[1]:
            attrs = _attrs(token[3])
            identity = attrs.get("id")
            if not identity or identity in ids:
                raise ProvenanceError("inline XBRL fact id is missing or duplicated")
            ids.add(identity)
            if token[3].rstrip().endswith("/"):
                result.append(
                    _InlineElement(
                        kind,
                        attrs,
                        token.start(),
                        token.end(),
                        token.end(),
                        token.end(),
                    )
                )
            else:
                stack.append((kind, attrs, token.start(), token.end()))
        else:
            if not stack or stack[-1][0] != kind:
                raise ProvenanceError("inline XBRL tags are unbalanced")
            _, attrs, start, content_start = stack.pop()
            result.append(
                _InlineElement(
                    kind, attrs, start, content_start, token.start(), token.end()
                )
            )
    if stack:
        raise ProvenanceError("inline XBRL tags are incomplete")
    return sorted(result, key=lambda item: item.start)


def normalize_inline_source(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    """Retain exact numeric/structural bytes while redacting public contact/auth state."""
    sanitized, auth_count = _JWT_SHAPED.subn(b"[redacted-volatile-auth-state]", raw)
    text, email_count = _EMAIL.subn("[redacted-email]", sanitized.decode("utf-8"))
    _reject_secrets(text)
    if _EMAIL.search(text):
        raise ProvenanceError("inline source contact redaction failed")
    output = text.encode()
    return output, {
        "revision": INLINE_SOURCE_NORMALIZATION,
        "retrieval_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha256": hashlib.sha256(output).hexdigest(),
        "email_redactions": email_count,
        "volatile_auth_redactions": auth_count,
    }


def _namespace_check(text: str) -> None:
    for prefix, pattern in {
        "xbrli": r"http://www\.xbrl\.org/2003/instance",
        "iso4217": r"http://www\.xbrl\.org/2003/iso4217",
        "ix": r"http://www\.xbrl\.org/2013/inlineXBRL",
        "ixt": r"http://www\.xbrl\.org/inlineXBRL/transformation/2020-02-12",
        "us-gaap": r"https?://fasb\.org/us-gaap/\d{4}(?:-\d{2}-\d{2})?",
        "dei": r"https?://xbrl\.sec\.gov/dei/\d{4}(?:q[1-4])?",
    }.items():
        values = set(re.findall(r"xmlns:" + prefix + r'="([^"]+)"', text, re.IGNORECASE))
        if len(values) != 1 or re.fullmatch(pattern, next(iter(values))) is None:
            raise ProvenanceError("inline XBRL namespace binding is invalid")


def _context_records(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for match in _CONTEXT.finditer(text):
        key = _attrs(match[1]).get("id")
        if not key or key in result:
            raise ProvenanceError("inline XBRL context id is missing or duplicated")
        body = match[2]
        identifiers = re.findall(
            r"<xbrli:identifier\b([^>]*)>(.*?)</xbrli:identifier>", body, re.IGNORECASE | re.DOTALL
        )
        if (
            len(identifiers) != 1
            or _attrs(identifiers[0][0]).get("scheme") != "http://www.sec.gov/CIK"
        ):
            raise ProvenanceError("inline XBRL context entity is invalid")
        cik = identifiers[0][1].strip()
        if not re.fullmatch(r"\d{10}", cik):
            raise ProvenanceError("inline XBRL context CIK is invalid")
        start = re.findall(r"<xbrli:startdate>([^<]+)</xbrli:startdate>", body, re.IGNORECASE)
        end = re.findall(r"<xbrli:enddate>([^<]+)</xbrli:enddate>", body, re.IGNORECASE)
        instant = re.findall(r"<xbrli:instant>([^<]+)</xbrli:instant>", body, re.IGNORECASE)
        if len(instant) == 1 and not start and not end:
            start_date, end_date = "", date.fromisoformat(instant[0]).isoformat()
        elif len(start) == len(end) == 1 and not instant:
            start_date, end_date = (
                date.fromisoformat(start[0]).isoformat(),
                date.fromisoformat(end[0]).isoformat(),
            )
        else:
            raise ProvenanceError("inline XBRL context period is invalid")
        result[key] = {
            "cik": cik,
            "start": start_date,
            "end": end_date,
            "instant": bool(instant),
            "dimensioned": bool(
                re.search(
                    r"<(?:xbrli:(?:segment|scenario)|xbrldi:(?:explicitmember|typedmember))\b",
                    body,
                    re.IGNORECASE,
                )
            ),
        }
    if not result:
        raise ProvenanceError("inline XBRL contexts are missing")
    return result


def _unit_records(text: str) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for match in _UNIT.finditer(text):
        key = _attrs(match[1]).get("id")
        if not key or key in result:
            raise ProvenanceError("inline XBRL unit id is missing or duplicated")
        result[key] = tuple(
            value.strip()
            for value in re.findall(
                r"<xbrli:measure>([^<]+)</xbrli:measure>", match[2], re.IGNORECASE
            )
        )
    return result


def _inline_number(
    text: str, element: _InlineElement, role: str
) -> tuple[str, int, int]:
    attrs = element.attrs
    if any(
        key.rsplit(":", 1)[-1] == "nil" and value not in {"false", "0"}
        for key, value in attrs.items()
    ):
        raise ProvenanceError(
            "inline financial fact is nil or has an invalid nil marker"
        )
    if element.kind != "nonfraction" or attrs.get("name") != INLINE_ROLES.get(role):
        raise ProvenanceError("inline XBRL financial concept is invalid")
    if (
        attrs.get("unitref") != "usd"
        or attrs.get("scale") != "6"
        or attrs.get("decimals") != "-6"
    ):
        raise ProvenanceError("inline XBRL financial unit or scale is invalid")
    if attrs.get("format", "") not in {"", "ixt:num-dot-decimal"} or attrs.get(
        "sign", ""
    ) not in {"", "-"}:
        raise ProvenanceError("inline XBRL numeric format or sign is invalid")
    inner = text[element.content_start : element.content_end]
    quote = inner.strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", quote):
        raise ProvenanceError("inline XBRL numeric source span is not an integer")
    value = int(quote.replace(",", "")) * (-1 if attrs.get("sign") == "-" else 1)
    start = element.content_start + inner.find(quote)
    return quote, value, start


def inline_row_value(text: str, relative_start: int, quote: str, role: str) -> int:
    matches = [
        item
        for item in _elements(text)
        if item.kind == "nonfraction"
        and item.content_start
        <= relative_start
        < relative_start + len(quote)
        <= item.content_end
    ]
    if len(matches) != 1:
        raise ProvenanceError("inline financial row operand is ambiguous")
    actual_quote, value, start = _inline_number(text, matches[0], role)
    if start != relative_start or actual_quote != quote:
        raise ProvenanceError("inline financial row source span mismatch")
    return value


def parse_issuer_inline_metrics(
    text: str, *, report_date: str, issuer_cik: str, metric_profile: str
) -> IssuerIrRenderedProgram:
    if issuer_cik != AMD_CIK or metric_profile != AMD_INLINE_PROFILE:
        raise ProvenanceError("inline issuer profile is not registered")
    _namespace_check(text)
    contexts = _context_records(text)
    units = _unit_records(text)
    elements = _elements(text)
    identities = [
        item
        for item in elements
        if item.attrs.get("name") == "dei:EntityCentralIndexKey"
    ]
    if not identities or {
        _visible_cell_text(text[item.content_start : item.content_end])
        for item in identities
    } != {AMD_CIK}:
        raise ProvenanceError("inline filing CIK identity is invalid")
    refs = {item.attrs.get("contextref") for item in identities}
    if len(refs) != 1 or not refs <= contexts.keys():
        raise ProvenanceError("inline filing annual identity context is ambiguous")
    annual_ref = next(iter(refs))
    annual = contexts[annual_ref]
    if (
        annual["cik"] != AMD_CIK
        or annual["instant"]
        or annual["dimensioned"]
        or annual["end"] != report_date
    ):
        raise ProvenanceError("inline filing annual context binding is invalid")
    if (
        not 350
        <= (
            date.fromisoformat(annual["end"]) - date.fromisoformat(annual["start"])
        ).days
        <= 380
    ):
        raise ProvenanceError("inline filing period is not annual")
    for concept, expected in (
        ("DocumentType", "10-K"),
        ("DocumentFiscalPeriodFocus", "FY"),
        ("DocumentFiscalYearFocus", report_date[:4]),
    ):
        selected = [
            item for item in elements if item.attrs.get("name") == "dei:" + concept
        ]
        if (
            not selected
            or any(item.attrs.get("contextref") != annual_ref for item in selected)
            or {
                _visible_cell_text(text[item.content_start : item.content_end])
                for item in selected
            }
            != {expected}
        ):
            raise ProvenanceError("inline filing form or fiscal identity is invalid")
    names = [
        item
        for item in elements
        if item.attrs.get("name") == "dei:EntityRegistrantName"
    ]
    if not names or any(
        re.sub(
            r"[^a-z0-9]",
            "",
            _visible_cell_text(text[item.content_start : item.content_end]).casefold(),
        )
        != "advancedmicrodevicesinc"
        or item.attrs.get("contextref") != annual_ref
        for item in names
    ):
        raise ProvenanceError("inline filing registrant identity is invalid")
    date_elements = [
        item
        for item in elements
        if item.attrs.get("name") == "dei:DocumentPeriodEndDate"
    ]
    plain_dates = []
    for item in date_elements:
        raw = text[item.content_start : item.content_end]
        visible = _visible_cell_text(raw)
        try:
            parsed_date = (
                datetime.strptime(re.sub(r"\s+,", ",", visible), "%B %d, %Y")
                .replace(tzinfo=timezone.utc)
                .date()
                .isoformat()
            )
        except ValueError as error:
            raise ProvenanceError("inline filing report date is invalid") from error
        if item.attrs.get("contextref") != annual_ref or parsed_date != report_date:
            raise ProvenanceError("inline filing report date is invalid")
        if raw.strip() == visible:
            plain_dates.append((item.content_start + raw.find(visible), visible))
    if not plain_dates:
        raise ProvenanceError("inline filing exact report-date span is missing")
    section_roles = {
        "Consolidated Statements of Operations": ("revenue", "operating_income"),
        "Consolidated Balance Sheets": ("assets", "liabilities_and_equity"),
        "Consolidated Statements of Cash Flows": (
            "cash_from_operations",
            "cash_from_investing",
            "cash_from_financing",
            "cash_period_change",
        ),
    }
    facts: list[IssuerIrMetricFact] = []
    ranges = []
    for title, roles in section_roles.items():
        tables = [
            table
            for table in _TABLE.finditer(text)
            if _visible_cell_text(text[max(0, table.start() - 2048) : table.start()])
            .casefold()
            .endswith(title.casefold())
        ]
        if len(tables) != 1:
            raise ProvenanceError(
                "inline financial statement heading is missing or ambiguous"
            )
        table = tables[0]
        ranges.append((title, table.start(), table.end()))
        for role in roles:
            selected = []
            for item in elements:
                if (
                    item.kind != "nonfraction"
                    or item.attrs.get("name") != INLINE_ROLES[role]
                    or not table.start() <= item.start < item.end <= table.end()
                ):
                    continue
                context = contexts.get(item.attrs.get("contextref", ""))
                if context is None:
                    raise ProvenanceError("inline numeric context reference is missing")
                expected_instant = role in {"assets", "liabilities_and_equity"}
                if (
                    context["cik"] == AMD_CIK
                    and not context["dimensioned"]
                    and context["instant"] == expected_instant
                    and context["end"] == report_date
                    and (expected_instant or context["start"] == annual["start"])
                ):
                    selected.append(item)
            if len(selected) != 1:
                raise ProvenanceError(
                    "inline required annual operand is missing or ambiguous"
                )
            item = selected[0]
            if units.get(item.attrs.get("unitref", "")) != ("iso4217:USD",):
                raise ProvenanceError("inline financial unit does not resolve to USD")
            quote, value, start = _inline_number(text, item, role)
            facts.append(
                IssuerIrMetricFact(
                    role=role,
                    numeric_value=value,
                    evidence_quote=quote,
                    char_start=start,
                    char_end=start + len(quote),
                    section=title,
                )
            )
    values = {fact.role: fact.numeric_value for fact in facts}
    if (
        values["assets"] != values["liabilities_and_equity"]
        or sum(
            values[r]
            for r in (
                "cash_from_operations",
                "cash_from_investing",
                "cash_from_financing",
            )
        )
        != values["cash_period_change"]
    ):
        raise ProvenanceError("inline balance or cash component identity fails")
    start, display = plain_dates[0]
    return IssuerIrRenderedProgram(
        report_date=report_date,
        report_date_display=display,
        report_date_char_start=start,
        report_date_char_end=start + len(display),
        facts=tuple(facts),
        section_ranges=tuple(ranges),
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _inline_url(value: object, *, listing: bool = False, report_date: str = "") -> str:
    from urllib.parse import parse_qs, urlparse

    raw = str(value or "")
    url = urlparse(raw)
    if (
        url.scheme != "https"
        or url.hostname != AMD_HOST
        or url.username
        or url.password
        or url.port not in {None, 443}
        or url.fragment
        or url.params
    ):
        raise ProvenanceError("inline issuer URL is outside the registered issuer host")
    if listing:
        if url.path != AMD_LISTING_PATH or parse_qs(url.query) != {
            "form_type": ["10-K"]
        }:
            raise ProvenanceError("inline issuer listing URL is not registered")
    elif (
        url.query
        or re.fullmatch(
            re.escape(AMD_ARTIFACT_PREFIX)
            + r"0000002488-\d{2}-\d{6}/amd-"
            + re.escape(report_date.replace("-", ""))
            + r"\.htm",
            url.path,
        )
        is None
    ):
        raise ProvenanceError("inline issuer annual artifact URL is invalid")
    return raw


def validate_inline_request(value: dict[str, Any]) -> dict[str, Any]:
    from longworld.core.issuerfilingworkflow import _authorization

    if (
        value.get("schema_version") != ISSUER_INLINE_REQUEST_SCHEMA
        or value.get("source_provider") != ISSUER_INLINE_PROVIDER
        or value.get("source_profile") != AMD_INLINE_PROFILE
    ):
        raise ProvenanceError("inline source request schema or provider is unsupported")
    issuer = value.get("issuer")
    if issuer != {
        "name": "ADVANCED MICRO DEVICES, INC.",
        "cik": AMD_CIK,
        "host": AMD_HOST,
    }:
        raise ProvenanceError("inline issuer identity is not registered")
    _authorization(value.get("authorization"))
    _inline_url(value.get("listing_url"), listing=True)
    filings = value.get("filings")
    if not isinstance(filings, list) or len(filings) != 4:
        raise ProvenanceError("inline history requires four annual filings")
    ids: set[str] = set()
    years: set[int] = set()
    for filing in filings:
        if not isinstance(filing, dict):
            raise ProvenanceError("inline filing request is invalid")
        identity = str(filing.get("filing_id") or "")
        report = date.fromisoformat(str(filing.get("report_date") or ""))
        filed = date.fromisoformat(str(filing.get("filing_date") or ""))
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", identity)
            or identity in ids
            or filing.get("form") != "10-K"
            or report >= filed
            or report.year in years
        ):
            raise ProvenanceError("inline annual filing identity is invalid")
        ids.add(identity)
        years.add(report.year)
        _inline_url(filing.get("source_url"), report_date=report.isoformat())
    if sorted(years) != list(range(min(years), max(years) + 1)):
        raise ProvenanceError("inline history annual coverage has a gap")
    return value


def validate_inline_listing(text: str, filings: list[dict[str, Any]]) -> None:
    from urllib.parse import urljoin

    found: dict[str, str] = {}
    for row in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row.group(), re.IGNORECASE | re.DOTALL)
        if len(cells) < 2 or _visible_cell_text(cells[1]) != "10-K":
            continue
        filed = (
            datetime.strptime(_visible_cell_text(cells[0]), "%m/%d/%y")
            .replace(tzinfo=timezone.utc)
            .date()
            .isoformat()
        )
        links = {
            urljoin("https://" + AMD_HOST, html.unescape(match[2]))
            for match in re.finditer(
                r"href\s*=\s*([\"'])(.*?)\1", row.group(), re.IGNORECASE | re.DOTALL
            )
        }
        for link in links:
            if not link.endswith(".htm"):
                continue
            if link in found:
                raise ProvenanceError("inline listing repeats an annual document")
            found[link] = filed
    for filing in filings:
        if found.get(str(filing["source_url"])) != filing["filing_date"]:
            raise ProvenanceError(
                "inline annual source is not linked by the issuer listing"
            )


def _read_inventory_source(base: Path, receipt: dict[str, Any]) -> tuple[str, str]:
    from longworld.core.provenance import MAX_SOURCE_BYTES, _read_regular_file

    paths = [receipt.get("raw_source_file"), receipt.get("source_file")]
    if any(not isinstance(name, str) or Path(name).name != name for name in paths):
        raise ProvenanceError("inline inventory source path is unsafe")
    raw = _read_regular_file(base / paths[0], MAX_SOURCE_BYTES)
    stored = _read_regular_file(base / paths[1], MAX_SOURCE_BYTES)
    normalized, transform = normalize_inline_source(raw)
    if (
        normalized != stored
        or receipt.get("normalization") != transform
        or receipt.get("retrieval_bytes") != len(raw)
        or receipt.get("bytes") != len(stored)
    ):
        raise ProvenanceError("inline source normalization or byte binding failed")
    return raw.decode(), stored.decode()


def _derived_facts(program: IssuerIrRenderedProgram) -> list[dict[str, Any]]:
    return [
        {
            "field": "report_date_display",
            "value": program.report_date_display,
            "evidence_quote": program.report_date_display,
            "evidence_char_start": program.report_date_char_start,
            "source_sha256": program.source_sha256,
        },
        *[
            {
                "field": fact.role,
                "value": fact.evidence_quote,
                "numeric_value": fact.numeric_value,
                "evidence_quote": fact.evidence_quote,
                "evidence_char_start": fact.char_start,
                "source_sha256": program.source_sha256,
            }
            for fact in program.facts
        ],
    ]


def _inline_relations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "prior_available_annual_filing",
            "relation_provenance": "derived_temporal_same_issuer",
            "source_record_id": current["record_id"],
            "target_record_id": prior["record_id"],
        }
        for prior, current in pairwise(records)
    ]


def build_issuer_inline_manifest(
    inventory: dict[str, Any], base: Path, *, generated_at: str
) -> dict[str, Any]:
    from longworld.core.provenance import _parse_timestamp

    _parse_timestamp(generated_at, "generated_at")
    if (
        inventory.get("schema_version") != ISSUER_INLINE_INVENTORY_SCHEMA
        or inventory.get("data_stage") != "source_inventory"
        or inventory.get("production_eligible") is not False
        or inventory.get("generation_integration") != "disabled"
    ):
        raise ProvenanceError("inline source inventory boundary is invalid")
    request = validate_inline_request(inventory.get("request") or {})
    listing = inventory.get("listing")
    if (
        not isinstance(listing, dict)
        or listing.get("source_url") != request["listing_url"]
    ):
        raise ProvenanceError("inline listing source receipt is invalid")
    raw_listing, text_listing = _read_inventory_source(base, listing)
    validate_inline_listing(text_listing, request["filings"])
    acquired = inventory.get("filings")
    if not isinstance(acquired, list) or len(acquired) != 4:
        raise ProvenanceError("inline acquired filing count is invalid")
    by_id = {entry.get("filing_id"): entry for entry in acquired}
    if len(by_id) != 4:
        raise ProvenanceError("inline acquired filing identity is duplicated")
    records = []
    for filing in sorted(request["filings"], key=lambda item: item["report_date"]):
        receipt = by_id.get(filing["filing_id"])
        if (
            not isinstance(receipt, dict)
            or receipt.get("source_url") != filing["source_url"]
        ):
            raise ProvenanceError("inline acquired document binding is invalid")
        raw, text = _read_inventory_source(base, receipt)
        program = parse_issuer_inline_metrics(
            text,
            report_date=filing["report_date"],
            issuer_cik=AMD_CIK,
            metric_profile=AMD_INLINE_PROFILE,
        )
        records.append(
            {
                **filing,
                "record_id": f"issuer-inline:{AMD_CIK}:{filing['report_date']}",
                "issuer_name": request["issuer"]["name"],
                "cik": AMD_CIK,
                "retrieval_url": request["listing_url"],
                "raw_text": raw,
                "text": text,
                "source_sha256": program.source_sha256,
                "source_normalization": receipt["normalization"],
                "source_file": receipt["source_file"],
                "derived_facts": _derived_facts(program),
            }
        )
    manifest = {
        "schema_version": ISSUER_INLINE_MANIFEST_SCHEMA,
        "source_provider": ISSUER_INLINE_PROVIDER,
        "source_profile": AMD_INLINE_PROFILE,
        "source_status": "issuer_owned_ir_download",
        "source_family": ISSUER_INLINE_SOURCE_FAMILY,
        "data_stage": "source_inventory",
        "production_eligible": False,
        "generation_integration": "disabled",
        "generated_at": generated_at,
        "authorization": request["authorization"],
        "issuer": request["issuer"],
        "acquisition_inventory_sha256": hashlib.sha256(
            _canonical(inventory)
        ).hexdigest(),
        "listing": {
            "source_url": request["listing_url"],
            "raw_text": raw_listing,
            "text": text_listing,
            "normalization": listing["normalization"],
        },
        "n": 4,
        "records": records,
        "relations": _inline_relations(records),
    }
    _audit_inline_manifest(manifest)
    return manifest


def _audit_inline_manifest(payload: dict[str, Any]) -> None:
    from longworld.core.issuerfilingworkflow import _authorization
    from longworld.core.provenance import _parse_timestamp

    if (
        payload.get("schema_version") != ISSUER_INLINE_MANIFEST_SCHEMA
        or payload.get("source_provider") != ISSUER_INLINE_PROVIDER
        or payload.get("source_profile") != AMD_INLINE_PROFILE
        or payload.get("source_family") != ISSUER_INLINE_SOURCE_FAMILY
        or payload.get("source_status") != "issuer_owned_ir_download"
        or payload.get("data_stage") != "source_inventory"
        or payload.get("production_eligible") is not False
        or payload.get("generation_integration") != "disabled"
    ):
        raise ProvenanceError("inline manifest source contract is invalid")
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    _authorization(payload.get("authorization"))
    records = payload.get("records")
    listing = payload.get("listing")
    if (
        not isinstance(records, list)
        or len(records) != 4
        or payload.get("n") != 4
        or not isinstance(listing, dict)
    ):
        raise ProvenanceError("inline manifest source graph is invalid")
    validate_inline_request(
        {
            "schema_version": ISSUER_INLINE_REQUEST_SCHEMA,
            "source_provider": payload["source_provider"],
            "source_profile": payload["source_profile"],
            "issuer": payload.get("issuer"),
            "authorization": payload["authorization"],
            "listing_url": listing.get("source_url"),
            "filings": records,
        }
    )
    for source in [listing, *records]:
        raw = source.get("raw_text")
        text = source.get("text")
        if not isinstance(raw, str) or not isinstance(text, str):
            raise ProvenanceError("inline manifest exact source text is missing")
        normalized, receipt = normalize_inline_source(raw.encode())
        if normalized.decode() != text or receipt != source.get(
            "source_normalization", source.get("normalization")
        ):
            raise ProvenanceError("inline manifest raw-to-source binding failed")
    validate_inline_listing(listing["text"], records)
    if records != sorted(records, key=lambda item: item["report_date"]):
        raise ProvenanceError("inline manifest annual records are not chronological")
    for record in records:
        if (
            record.get("cik") != AMD_CIK
            or record.get("issuer_name") != payload["issuer"]["name"]
            or record.get("record_id")
            != f"issuer-inline:{AMD_CIK}:{record['report_date']}"
            or record.get("retrieval_url") != listing["source_url"]
            or record.get("source_file") != record["filing_id"] + ".inline_xbrl.html"
        ):
            raise ProvenanceError("inline manifest record issuer binding is invalid")
        program = parse_issuer_inline_metrics(
            record["text"],
            report_date=record["report_date"],
            issuer_cik=AMD_CIK,
            metric_profile=AMD_INLINE_PROFILE,
        )
        if record.get("source_sha256") != program.source_sha256 or record.get(
            "derived_facts"
        ) != _derived_facts(program):
            raise ProvenanceError(
                "inline manifest financial facts are not source-bound"
            )
    if (
        payload.get("relations") != _inline_relations(records)
        or re.fullmatch(
            r"[0-9a-f]{64}", str(payload.get("acquisition_inventory_sha256") or "")
        )
        is None
    ):
        raise ProvenanceError("inline manifest lineage relations are invalid")


def load_issuer_inline_manifest_bytes(
    raw: bytes, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    payload = json.loads(raw.decode())
    if not isinstance(payload, dict) or not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("inline manifest source attestation is invalid")
    _audit_inline_manifest(payload)
    return payload
