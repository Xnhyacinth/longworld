"""Exact iXBRL fact and non-overlapping section views over SEC components."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
from datetime import date
from html import unescape
from itertools import pairwise

from longworld.core.filingworkflow import (
    ISSUER_GCS_MERGED_COMPONENT_REVISION,
    SEC_REQUIRED_COMPONENT_TYPES,
    SecFilingComponent,
    parse_issuer_gcs_merged_components,
    parse_sec_filing_components,
    validate_issuer_gcs_merged_component,
    validate_sec_filing_component,
)
from longworld.core.provenance import ProvenanceError

SEC_XBRL_FACT_REVISION = "sec-ixbrl-fact-v2"
SEC_SECTION_REVISION = "sec-filing-section-v1"
SEC_CERT_FACT_REVISION = "sec-certification-fact-v1"
SEC_NOTE13_MAX_CHARS = 45_000
REVENUE_CONCEPT = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
PRODUCT_AXIS = "srt:ProductOrServiceAxis"
PRODUCT_MEMBER = "us-gaap:ProductMember"
SERVICE_MEMBER = "us-gaap:ServiceMember"
SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
CONSOLIDATION_AXIS = "srt:ConsolidationItemsAxis"
OPERATING_SEGMENT_MEMBER = "us-gaap:OperatingSegmentsMember"
GEOGRAPHICAL_AXIS = "srt:StatementGeographicalAxis"
TAX_AUTHORITY_AXIS = "us-gaap:IncomeTaxAuthorityAxis"
DOMESTIC_TAX_MEMBER = "us-gaap:DomesticCountryMember"
STATE_TAX_MEMBER = "us-gaap:StateAndLocalJurisdictionMember"
FOREIGN_TAX_MEMBER = "us-gaap:ForeignCountryMember"
FX_CASH_CONCEPT = (
    "us-gaap:EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCash"
    "EquivalentsIncludingDisposalGroupAndDiscontinuedOperations"
)
OPERATING_INCOME_CONCEPT = "us-gaap:OperatingIncomeLoss"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IX_TOKEN = re.compile(r"<(/?)ix:nonFraction\b([^>]*)>", re.IGNORECASE)
_IX_ATTR = re.compile(r'([A-Za-z0-9:]+)="([^"]*)"')
_CONTEXT = re.compile(
    r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>',
    re.DOTALL | re.IGNORECASE,
)
_START_DATE = re.compile(r"<xbrli:startDate>([^<]+)</xbrli:startDate>", re.IGNORECASE)
_END_DATE = re.compile(r"<xbrli:endDate>([^<]+)</xbrli:endDate>", re.IGNORECASE)
_INSTANT = re.compile(r"<xbrli:instant>([^<]+)</xbrli:instant>", re.IGNORECASE)
_MEMBER = re.compile(
    r'<xbrldi:explicitMember dimension="([^"]+)">([^<]+)</xbrldi:explicitMember>',
    re.IGNORECASE,
)
_CERT_NAME = re.compile(r"I, ([A-Z][A-Za-z .'-]+), (?:[^,\n]+, )?certify")
_GCS_906_CERT_NAME = re.compile(
    r"\), ([A-Z][A-Za-z .'-]+), Chief (?:Executive|Financial) Officer "
    r"of the Company, does hereby certify"
)
_CERT_DATE = re.compile(
    r"Date&#58;\s*([A-Z][a-z]+(?:(?:&#160;)|(?:&nbsp;)|\s)+\d{1,2},\s*\d{4})"
)
_GCS_CERT_DATE = re.compile(r">([A-Z][a-z]+\s+\d{1,2},\s*\d{4})</(?:font|span)>")
_CERT_PERIOD = re.compile(
    r"(?:fiscal\s+)?year ended\s+"
    r"([A-Z][a-z]+(?:(?:&#160;)|(?:&nbsp;)|\s)+\d{1,2},\s*\d{4})",
    re.IGNORECASE,
)
_CERT_FORM = re.compile(r"Form\s+10-K", re.IGNORECASE)
_CERT_906 = re.compile(r"Section(?:(?:&#160;)|(?:&nbsp;)|\s)+906", re.IGNORECASE)
_OFFICER_TITLES = ("Chief Executive Officer", "Chief Financial Officer")
_CENTRAL_INDEX_KEY = re.compile(
    r"^[ \t]*CENTRAL INDEX KEY:[ \t]*(\d{1,10})[ \t]*\r?$",
    re.MULTILINE,
)
_GCS_ACCESSION = re.compile(r'title="(\d{10}-\d{2}-\d{6})\.pdf"')
_GCS_CIK = re.compile(
    r'<ix:nonNumeric\b[^>]*\bname="dei:EntityCentralIndexKey"[^>]*>'
    r"[ \t\r\n]*(\d{10})[ \t\r\n]*</ix:nonNumeric>",
    re.IGNORECASE,
)
_GCS_PRIMARY_SCHEMA = re.compile(
    r'\bxlink:href="#([A-Za-z0-9][A-Za-z0-9._-]{0,249})\.xsd"'
)
_HEADINGS = (
    (
        "item8_operations",
        "10-K",
        "CONSOLIDATED STATEMENTS OF OPERATIONS",
        "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME",
        None,
    ),
    (
        "item8_balance_sheet",
        "10-K",
        "CONSOLIDATED BALANCE SHEETS",
        "CONSOLIDATED STATEMENTS OF SHAREHOLDERS",
        None,
    ),
    (
        "item8_cash_flow",
        "10-K",
        "CONSOLIDATED STATEMENTS OF CASH FLOWS",
        ">Note 1 ",
        None,
    ),
    ("note2_revenue", "10-K", ">Note 2 ", ">Note 3 ", None),
    ("note7_income_taxes", "10-K", ">Note 7 ", ">Note 8 ", None),
    ("note8_leases", "10-K", ">Note 8 ", ">Note 9 ", None),
    ("note9_debt", "10-K", ">Note 9 ", ">Note 10 ", None),
    ("note13_segments", "10-K", ">Note 13 ", None, SEC_NOTE13_MAX_CHARS),
)


@dataclass(frozen=True)
class IssuerFinancialSpec:
    headings: tuple[tuple[str, str, str, str | None, int | None], ...]
    n_category: int
    n_geo: int
    geo_axis: str
    geo_requires_operating_segment: bool
    require_liabilities: bool
    extra_cert_components: tuple[str, ...]
    section_ground: tuple[tuple[str, str], ...]
    category_exclude_members: tuple[str, ...]
    extra_128k_sections: tuple[str, ...]
    cash_flow_includes_fx: bool
    service_member: str = SERVICE_MEMBER


_APPLE_GROUND = (
    ("item8_operations", "CONSOLIDATED STATEMENTS OF OPERATIONS"),
    ("item8_balance_sheet", "CONSOLIDATED BALANCE SHEETS"),
    ("item8_cash_flow", "CONSOLIDATED STATEMENTS OF CASH FLOWS"),
    ("note2_revenue", "Note 2"),
    ("note7_income_taxes", "Note 7"),
    ("note8_leases", "Note 8"),
    ("note9_debt", "Note 9"),
    ("note13_segments", "Note 13"),
    ("ex_31_1", "EX-31.1"),
    ("ex_31_2", "EX-31.2"),
    ("ex_32_1", "EX-32.1"),
)
_AMAZON_HEADINGS = (
    (
        "item8_operations",
        "10-K",
        "CONSOLIDATED STATEMENTS OF OPERATIONS",
        "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME",
        None,
    ),
    (
        "item8_balance_sheet",
        "10-K",
        "CONSOLIDATED BALANCE SHEETS",
        "CONSOLIDATED STATEMENTS OF STOCKHOLDERS",
        None,
    ),
    (
        "note2_revenue",
        "10-K",
        'name="us-gaap:DisaggregationOfRevenueTableTextBlock"',
        (
            "Net sales are attributed to countries primarily based on "
            "country-focused online and physical stores"
        ),
        None,
    ),
    (
        "note13_segments",
        "10-K",
        (
            "Net sales are attributed to countries primarily based on "
            "country-focused online and physical stores"
        ),
        'id="f-1388"',
        None,
    ),
    (
        "item8_cash_flow",
        "10-K",
        (
            "CONSOLIDATED STATEMENTS OF CASH FLOWS</span></div>"
            '<div style="text-align:center">'
        ),
        "CONSOLIDATED STATEMENTS OF OPERATIONS",
        None,
    ),
    (
        "note4_leases",
        "10-K",
        'name="us-gaap:LesseeFinanceLeasesTextBlock"',
        'name="us-gaap:BusinessCombinationDisclosureTextBlock"',
        None,
    ),
    (
        "note9_income_taxes",
        "10-K",
        'name="us-gaap:IncomeTaxDisclosureTextBlock"',
        'name="us-gaap:SegmentReportingDisclosureTextBlock"',
        None,
    ),
    (
        "note10_segment_oi",
        "10-K",
        'name="us-gaap:SegmentReportingDisclosureTextBlock"',
        'name="us-gaap:DisaggregationOfRevenueTableTextBlock"',
        None,
    ),
)
_AMAZON_GROUND = (
    ("item8_operations", "CONSOLIDATED STATEMENTS OF OPERATIONS"),
    ("item8_balance_sheet", "CONSOLIDATED BALANCE SHEETS"),
    ("note2_revenue", "DisaggregationOfRevenueTableTextBlock"),
    (
        "note13_segments",
        (
            "Net sales are attributed to countries primarily based on "
            "country-focused online and physical stores"
        ),
    ),
    ("item8_cash_flow", "CONSOLIDATED STATEMENTS OF CASH FLOWS"),
    ("note4_leases", "LesseeFinanceLeasesTextBlock"),
    ("note9_income_taxes", "IncomeTaxDisclosureTextBlock"),
    ("note10_segment_oi", "SegmentReportingDisclosureTextBlock"),
    ("ex_31_1", "EX-31.1"),
    ("ex_31_2", "EX-31.2"),
    ("ex_32_1", "EX-32.1"),
    ("ex_32_2", "EX-32.2"),
)
_MICROSOFT_HEADINGS = (
    (
        "item8_operations",
        "10-K",
        'id="income_statements"',
        'id="comprehensive_income_statements"',
        None,
    ),
    (
        "item8_balance_sheet",
        "10-K",
        'id="balance_sheets"',
        'id="cash_flows_statements"',
        None,
    ),
    (
        "item8_cash_flow",
        "10-K",
        'id="cash_flows_statements"',
        'id="stockholders_equity_statements"',
        None,
    ),
    (
        "note10_debt",
        "10-K",
        'name="us-gaap:DebtDisclosureTextBlock"',
        'name="us-gaap:IncomeTaxDisclosureTextBlock"',
        None,
    ),
    (
        "note11_income_taxes",
        "10-K",
        'name="us-gaap:IncomeTaxDisclosureTextBlock"',
        'name="us-gaap:RevenueFromContractWithCustomerTextBlock"',
        None,
    ),
    (
        "note14_leases",
        "10-K",
        'name="msft:LesseeOperatingAndFinanceLeasesTextBlock"',
        'name="us-gaap:LegalMattersAndContingenciesTextBlock"',
        None,
    ),
    (
        "note13_segments",
        "10-K",
        'name="us-gaap:RevenueFromExternalCustomersByGeographicAreasTableTextBlock"',
        (
            'name="us-gaap:ScheduleOfEntityWideInformationRevenueFromExternalCustomersByProductsAndServicesTextBlock"'
        ),
        None,
    ),
    (
        "note2_revenue",
        "10-K",
        (
            'name="us-gaap:ScheduleOfEntityWideInformationRevenueFromExternalCustomersByProductsAndServicesTextBlock"'
        ),
        'name="us-gaap:LongLivedAssetsByGeographicAreasTableTextBlock"',
        None,
    ),
)
_MICROSOFT_GROUND = (
    ("item8_operations", 'id="income_statements"'),
    ("item8_balance_sheet", 'id="balance_sheets"'),
    ("item8_cash_flow", 'id="cash_flows_statements"'),
    ("note10_debt", "DebtDisclosureTextBlock"),
    ("note11_income_taxes", "IncomeTaxDisclosureTextBlock"),
    ("note14_leases", "LesseeOperatingAndFinanceLeasesTextBlock"),
    ("note13_segments", "RevenueFromExternalCustomersByGeographicAreasTableTextBlock"),
    (
        "note2_revenue",
        "ScheduleOfEntityWideInformationRevenueFromExternalCustomersByProductsAndServicesTextBlock",
    ),
    ("ex_31_1", "EX-31.1"),
    ("ex_31_2", "EX-31.2"),
    ("ex_32_1", "EX-32.1"),
    ("ex_32_2", "EX-32.2"),
)
_ISSUER_SPECS = {
    "0000320193": IssuerFinancialSpec(
        headings=_HEADINGS,
        n_category=5,
        n_geo=5,
        geo_axis=SEGMENT_AXIS,
        geo_requires_operating_segment=True,
        require_liabilities=True,
        extra_cert_components=(),
        section_ground=_APPLE_GROUND,
        category_exclude_members=(PRODUCT_MEMBER,),
        extra_128k_sections=(
            "item8_cash_flow",
            "note7_income_taxes",
            "note8_leases",
            "note9_debt",
        ),
        cash_flow_includes_fx=False,
    ),
    "0001018724": IssuerFinancialSpec(
        headings=_AMAZON_HEADINGS,
        n_category=7,
        n_geo=5,
        geo_axis=GEOGRAPHICAL_AXIS,
        geo_requires_operating_segment=False,
        require_liabilities=False,
        extra_cert_components=("EX-32.2",),
        section_ground=_AMAZON_GROUND,
        category_exclude_members=(PRODUCT_MEMBER, SERVICE_MEMBER),
        extra_128k_sections=(
            "item8_cash_flow",
            "note4_leases",
            "note9_income_taxes",
            "note10_segment_oi",
        ),
        cash_flow_includes_fx=True,
    ),
    "0000789019": IssuerFinancialSpec(
        headings=_MICROSOFT_HEADINGS,
        n_category=10,
        n_geo=2,
        geo_axis=GEOGRAPHICAL_AXIS,
        geo_requires_operating_segment=False,
        require_liabilities=True,
        extra_cert_components=("EX-32.2",),
        section_ground=_MICROSOFT_GROUND,
        category_exclude_members=(PRODUCT_MEMBER, "us-gaap:ServiceOtherMember"),
        extra_128k_sections=(
            "item8_cash_flow",
            "note10_debt",
            "note11_income_taxes",
            "note14_leases",
        ),
        cash_flow_includes_fx=True,
        service_member="us-gaap:ServiceOtherMember",
    ),
}


@dataclass(frozen=True)
class SecXbrlContext:
    context_id: str
    start_date: str
    end_date: str
    instant: bool
    members: tuple[tuple[str, str], ...]

    def member(self, axis: str) -> str:
        return dict(self.members).get(axis, "")


@dataclass(frozen=True)
class SecXbrlFact:
    fact_id: str
    concept: str
    context_ref: str
    unit_ref: str
    decimals: str
    scale: int
    sign: str
    evidence_quote: str
    numeric_value: int
    char_start: int
    char_end: int
    component_type: str
    component_sha256: str
    parent_source_sha256: str
    provenance_id: str
    start_date: str
    end_date: str
    instant: bool
    members: tuple[tuple[str, str], ...]

    def member(self, axis: str) -> str:
        return dict(self.members).get(axis, "")


@dataclass(frozen=True)
class SecFilingSection:
    section_id: str
    component_type: str
    char_start: int
    char_end: int
    parent_source_sha256: str
    component_sha256: str
    section_sha256: str
    provenance_id: str


@dataclass(frozen=True)
class SecCertificationFact:
    component_type: str
    name: str
    char_start: int
    char_end: int
    parent_source_sha256: str
    component_sha256: str
    officer_title: str
    officer_title_char_start: int
    officer_title_char_end: int
    certification_kind: str
    kind_char_start: int
    kind_char_end: int
    covered_form: str
    form_char_start: int
    form_char_end: int
    covered_period_end: str
    period_char_start: int
    period_char_end: int
    certification_date: str
    date_char_start: int
    date_char_end: int
    provenance_id: str


@dataclass(frozen=True)
class SecFinancialProgram:
    sections: tuple[SecFilingSection, ...]
    roles: dict[str, SecXbrlFact]
    certifications: dict[str, tuple[SecCertificationFact, ...]]
    note13_max_chars: int = SEC_NOTE13_MAX_CHARS
    n_category: int = 5
    n_geo: int = 5
    require_liabilities: bool = True
    section_ground: tuple[tuple[str, str], ...] = ()
    extra_128k_sections: tuple[str, ...] = ()


def _source_components(
    source_text: str, parent_source_sha256: str
) -> tuple[SecFilingComponent, ...]:
    if "microsoft.gcs-web.com/sec-filings/sec-filing/" not in source_text:
        return parse_sec_filing_components(source_text, parent_source_sha256)
    if hashlib.sha256(source_text.encode()).hexdigest() != parent_source_sha256:
        raise ProvenanceError("issuer GCS parent source hash mismatch")
    accessions = set(_GCS_ACCESSION.findall(source_text))
    ciks = set(_GCS_CIK.findall(source_text))
    primary_stems = set(_GCS_PRIMARY_SCHEMA.findall(source_text))
    if len(accessions) != 1 or len(ciks) != 1 or len(primary_stems) != 1:
        raise ProvenanceError("issuer GCS filing identity is missing or ambiguous")
    return parse_issuer_gcs_merged_components(
        source_text,
        expected_accession=next(iter(accessions)),
        expected_cik=next(iter(ciks)),
        expected_form="10-K",
        expected_primary_document=f"{next(iter(primary_stems))}.htm",
    )


def _validate_source_component(source_text: str, component: SecFilingComponent) -> None:
    if component.parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION:
        validate_issuer_gcs_merged_component(source_text, component)
    else:
        validate_sec_filing_component(source_text, component)


def parse_ixbrl_display_number(raw: str, *, scale: int, sign: str) -> int:
    cleaned = raw.strip().replace(",", "").replace(" ", "")
    if re.fullmatch(r"-?\d+", cleaned) is None:
        raise ProvenanceError("SEC iXBRL fact value is not an integer")
    if isinstance(scale, bool) or not isinstance(scale, int):
        raise ProvenanceError("SEC iXBRL scale is invalid")
    value = (
        int(cleaned) * (10**scale) if scale >= 0 else int(cleaned) // (10 ** (-scale))
    )
    if sign == "-":
        return -value
    if sign:
        raise ProvenanceError("SEC iXBRL sign is invalid")
    return value


def _derived_id(payload: dict[str, object]) -> str:
    return (
        "derived-sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _xbrl_fact_provenance(fact: SecXbrlFact) -> str:
    return _derived_id(
        {
            "operation": SEC_XBRL_FACT_REVISION,
            "fact_id": fact.fact_id,
            "concept": fact.concept,
            "context_ref": fact.context_ref,
            "unit_ref": fact.unit_ref,
            "decimals": fact.decimals,
            "scale": fact.scale,
            "sign": fact.sign,
            "evidence_quote": fact.evidence_quote,
            "numeric_value": fact.numeric_value,
            "char_start": fact.char_start,
            "char_end": fact.char_end,
            "component_type": fact.component_type,
            "component_sha256": fact.component_sha256,
            "parent_source_sha256": fact.parent_source_sha256,
            "start_date": fact.start_date,
            "end_date": fact.end_date,
            "instant": fact.instant,
            "members": fact.members,
        }
    )


def _parse_contexts(component_text: str) -> dict[str, SecXbrlContext]:
    contexts: dict[str, SecXbrlContext] = {}
    for match in _CONTEXT.finditer(component_text):
        body = match.group(2)
        start = _START_DATE.search(body)
        end = _END_DATE.search(body)
        instant = _INSTANT.search(body)
        if instant is not None and (start is not None or end is not None):
            continue
        if instant is not None:
            start_date = ""
            end_date = instant.group(1).strip()
        elif start is not None and end is not None:
            start_date = start.group(1).strip()
            end_date = end.group(1).strip()
        else:
            continue
        members = tuple(
            sorted((axis, member.strip()) for axis, member in _MEMBER.findall(body))
        )
        context_id = match.group(1)
        if context_id in contexts:
            raise ProvenanceError("SEC iXBRL context id is duplicated")
        contexts[context_id] = SecXbrlContext(
            context_id=context_id,
            start_date=start_date,
            end_date=end_date,
            instant=instant is not None,
            members=members,
        )
    return contexts


def parse_sec_ixbrl_facts(
    source_text: str, component: SecFilingComponent
) -> tuple[SecXbrlFact, ...]:
    """Parse leaf iXBRL numeric facts from one parent-hash-bound component."""
    _validate_source_component(source_text, component)
    block = source_text[component.char_start : component.char_end]
    contexts = _parse_contexts(block)
    stack: list[tuple[int, dict[str, str], int]] = []
    facts: list[SecXbrlFact] = []
    seen: set[str] = set()
    for match in _IX_TOKEN.finditer(block):
        if match.group(1) == "/":
            if not stack:
                raise ProvenanceError("SEC iXBRL fact has unmatched close tag")
            _start, attrs, content_start = stack.pop()
            inner = block[content_start : match.start()]
            if re.search(r"<ix:nonFraction\b", inner, re.IGNORECASE):
                continue
            fact_id = attrs.get("id", "").strip()
            concept = attrs.get("name", "").strip()
            context_ref = attrs.get("contextRef", "").strip()
            unit_ref = attrs.get("unitRef", "").strip()
            decimals = attrs.get("decimals", "").strip()
            raw_scale = attrs.get("scale", "0").strip() or "0"
            sign = attrs.get("sign", "")
            quote = inner.strip()
            context = contexts.get(context_ref)
            if (
                not fact_id
                or not concept
                or not context_ref
                or context is None
                or not unit_ref
                or (decimals != "INF" and not decimals.lstrip("-").isdigit())
                or not raw_scale.lstrip("-").isdigit()
            ):
                continue
            if fact_id in seen:
                raise ProvenanceError("SEC iXBRL fact id is duplicated")
            try:
                numeric_value = parse_ixbrl_display_number(
                    quote, scale=int(raw_scale), sign=sign
                )
            except ProvenanceError:
                continue
            char_start = component.char_start + content_start + inner.find(quote)
            char_end = char_start + len(quote)
            parent_source_sha256 = component.parent_source_sha256
            fact = SecXbrlFact(
                fact_id=fact_id,
                concept=concept,
                context_ref=context_ref,
                unit_ref=unit_ref,
                decimals=decimals,
                scale=int(raw_scale),
                sign=sign,
                evidence_quote=quote,
                numeric_value=numeric_value,
                char_start=char_start,
                char_end=char_end,
                component_type=component.component_type,
                component_sha256=component.component_sha256,
                parent_source_sha256=parent_source_sha256,
                provenance_id="",
                start_date=context.start_date,
                end_date=context.end_date,
                instant=context.instant,
                members=context.members,
            )
            fact = replace(fact, provenance_id=_xbrl_fact_provenance(fact))
            _validate_sec_xbrl_fact(
                source_text,
                fact,
                component,
                context,
                component_validated=True,
            )
            facts.append(fact)
            seen.add(fact_id)
        else:
            attrs = dict(_IX_ATTR.findall(match.group(2)))
            if match.group(0).rstrip().endswith("/>"):
                if attrs.get("xsi:nil", "").lower() != "true":
                    raise ProvenanceError(
                        "SEC self-closing iXBRL fact is not explicitly nil"
                    )
                continue
            stack.append((match.start(), attrs, match.end()))
    if stack:
        raise ProvenanceError("SEC iXBRL fact has unmatched open tag")
    if not facts:
        raise ProvenanceError("SEC filing component has no iXBRL facts")
    return tuple(facts)


def _validate_sec_xbrl_fact(
    source_text: str,
    fact: SecXbrlFact,
    component: SecFilingComponent,
    context: SecXbrlContext | None = None,
    *,
    component_validated: bool = False,
) -> None:
    if not component_validated:
        _validate_source_component(source_text, component)
    parent_source_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    if (
        not isinstance(fact.parent_source_sha256, str)
        or _SHA256.fullmatch(fact.parent_source_sha256) is None
        or fact.parent_source_sha256 != parent_source_sha256
    ):
        raise ProvenanceError("SEC iXBRL fact parent source hash mismatch")
    if (
        isinstance(fact.char_start, bool)
        or isinstance(fact.char_end, bool)
        or not isinstance(fact.char_start, int)
        or not isinstance(fact.char_end, int)
        or fact.char_start < 0
        or fact.char_end <= fact.char_start
        or fact.char_end > len(source_text)
        or source_text[fact.char_start : fact.char_end] != fact.evidence_quote
        or fact.char_start < component.char_start
        or fact.char_end > component.char_end
    ):
        raise ProvenanceError("SEC iXBRL fact evidence span does not match source")
    if (
        fact.component_type != component.component_type
        or fact.component_sha256 != component.component_sha256
    ):
        raise ProvenanceError("SEC iXBRL fact component lineage mismatch")
    tag_start = source_text.rfind(
        "<ix:nonFraction", component.char_start, fact.char_start
    )
    tag_end = source_text.find(">", tag_start, fact.char_start + 1)
    if (
        tag_start < component.char_start
        or tag_end < tag_start
        or source_text[tag_end + 1 : fact.char_start].strip()
    ):
        raise ProvenanceError("SEC iXBRL fact opening tag does not match source")
    attributes = dict(_IX_ATTR.findall(source_text[tag_start : tag_end + 1]))
    if attributes.get("id", "").strip() != fact.fact_id or (
        attributes.get("name", "").strip(),
        attributes.get("contextRef", "").strip(),
        attributes.get("unitRef", "").strip(),
        attributes.get("decimals", "").strip(),
        attributes.get("scale", "0").strip() or "0",
        attributes.get("sign", ""),
    ) != (
        fact.concept,
        fact.context_ref,
        fact.unit_ref,
        fact.decimals,
        str(fact.scale),
        fact.sign,
    ):
        raise ProvenanceError("SEC iXBRL fact attributes do not match source")
    if context is None:
        component_text = source_text[component.char_start : component.char_end]
        context = _parse_contexts(component_text).get(fact.context_ref)
    if context is None or (
        context.start_date,
        context.end_date,
        context.instant,
        context.members,
    ) != (fact.start_date, fact.end_date, fact.instant, fact.members):
        raise ProvenanceError("SEC iXBRL fact context does not match source")
    expected = parse_ixbrl_display_number(
        fact.evidence_quote, scale=fact.scale, sign=fact.sign
    )
    if fact.numeric_value != expected:
        raise ProvenanceError("SEC iXBRL fact numeric value mismatch")
    expected_provenance = _xbrl_fact_provenance(fact)
    if fact.provenance_id != expected_provenance:
        raise ProvenanceError("SEC iXBRL fact provenance mismatch")


def validate_sec_xbrl_fact(source_text: str, fact: SecXbrlFact) -> None:
    """Replay a fact's exact span, value, component lineage, and provenance."""
    components = {
        item.component_type: item
        for item in _source_components(
            source_text, hashlib.sha256(source_text.encode()).hexdigest()
        )
    }
    component = components.get(fact.component_type)
    if component is None:
        raise ProvenanceError("SEC iXBRL fact component lineage mismatch")
    _validate_sec_xbrl_fact(source_text, fact, component)


def _unique_index(text: str, needle: str, start: int, end: int) -> int:
    chunk = text[start:end]
    first = chunk.find(needle)
    if first < 0 or chunk.find(needle, first + 1) >= 0:
        raise ProvenanceError(f"SEC section heading is missing or duplicated: {needle}")
    return start + first


def _section_from_headings(
    source_text: str,
    component: SecFilingComponent,
    *,
    section_id: str,
    start_heading: str,
    end_heading: str | None,
    max_chars: int | None,
) -> SecFilingSection:
    start = _unique_index(
        source_text, start_heading, component.char_start, component.char_end
    )
    if end_heading is not None:
        end = _unique_index(
            source_text, end_heading, component.char_start, component.char_end
        )
    elif max_chars is not None:
        end = min(component.char_end, start + max_chars)
    else:
        raise ProvenanceError("SEC section is missing an end bound")
    if end <= start:
        raise ProvenanceError("SEC section range is invalid")
    block = source_text[start:end]
    section_sha256 = hashlib.sha256(block.encode()).hexdigest()
    section = SecFilingSection(
        section_id=section_id,
        component_type=component.component_type,
        char_start=start,
        char_end=end,
        parent_source_sha256=component.parent_source_sha256,
        component_sha256=component.component_sha256,
        section_sha256=section_sha256,
        provenance_id=_derived_id(
            {
                "operation": SEC_SECTION_REVISION,
                "section_id": section_id,
                "component_type": component.component_type,
                "char_start": start,
                "char_end": end,
                "parent_source_sha256": component.parent_source_sha256,
                "component_sha256": component.component_sha256,
                "section_sha256": section_sha256,
            }
        ),
    )
    _validate_sec_filing_section(source_text, section, component)
    return section


def _component_section(
    source_text: str, component: SecFilingComponent, section_id: str
) -> SecFilingSection:
    block = source_text[component.char_start : component.char_end]
    section_sha256 = hashlib.sha256(block.encode()).hexdigest()
    section = SecFilingSection(
        section_id=section_id,
        component_type=component.component_type,
        char_start=component.char_start,
        char_end=component.char_end,
        parent_source_sha256=component.parent_source_sha256,
        component_sha256=component.component_sha256,
        section_sha256=section_sha256,
        provenance_id=_derived_id(
            {
                "operation": SEC_SECTION_REVISION,
                "section_id": section_id,
                "component_type": component.component_type,
                "char_start": component.char_start,
                "char_end": component.char_end,
                "parent_source_sha256": component.parent_source_sha256,
                "component_sha256": component.component_sha256,
                "section_sha256": section_sha256,
            }
        ),
    )
    _validate_sec_filing_section(source_text, section, component)
    return section


def _validate_sec_filing_section(
    source_text: str, section: SecFilingSection, component: SecFilingComponent
) -> None:
    _validate_source_component(source_text, component)
    parent_source_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    if (
        not isinstance(section.parent_source_sha256, str)
        or _SHA256.fullmatch(section.parent_source_sha256) is None
        or section.parent_source_sha256 != parent_source_sha256
    ):
        raise ProvenanceError("SEC filing section parent source hash mismatch")
    if (
        isinstance(section.char_start, bool)
        or isinstance(section.char_end, bool)
        or not isinstance(section.char_start, int)
        or not isinstance(section.char_end, int)
        or section.char_start < 0
        or section.char_end <= section.char_start
        or section.char_end > len(source_text)
        or section.char_start < component.char_start
        or section.char_end > component.char_end
    ):
        raise ProvenanceError("SEC filing section range is invalid")
    if (
        section.component_type != component.component_type
        or section.component_sha256 != component.component_sha256
    ):
        raise ProvenanceError("SEC filing section component lineage mismatch")
    block = source_text[section.char_start : section.char_end]
    section_sha256 = hashlib.sha256(block.encode()).hexdigest()
    if (
        not isinstance(section.section_sha256, str)
        or _SHA256.fullmatch(section.section_sha256) is None
        or section.section_sha256 != section_sha256
    ):
        raise ProvenanceError("SEC filing section hash mismatch")
    expected = _derived_id(
        {
            "operation": SEC_SECTION_REVISION,
            "section_id": section.section_id,
            "component_type": section.component_type,
            "char_start": section.char_start,
            "char_end": section.char_end,
            "parent_source_sha256": parent_source_sha256,
            "component_sha256": section.component_sha256,
            "section_sha256": section_sha256,
        }
    )
    if section.provenance_id != expected:
        raise ProvenanceError("SEC filing section provenance mismatch")


def validate_sec_filing_section(source_text: str, section: SecFilingSection) -> None:
    """Replay a non-copying section view against its exact SEC component."""
    components = {
        item.component_type: item
        for item in _source_components(
            source_text, hashlib.sha256(source_text.encode()).hexdigest()
        )
    }
    component = components.get(section.component_type)
    if component is None:
        raise ProvenanceError("SEC filing section component lineage mismatch")
    _validate_sec_filing_section(source_text, section, component)


def derive_sec_filing_subsection(
    source_text: str,
    section: SecFilingSection,
    *,
    section_id: str,
    char_start: int,
    char_end: int,
) -> SecFilingSection:
    """Derive one hash-bound, non-copying interval inside a validated section."""
    validate_sec_filing_section(source_text, section)
    if (
        not section_id
        or char_start < section.char_start
        or char_end > section.char_end
        or char_end <= char_start
    ):
        raise ProvenanceError("SEC filing subsection range is invalid")
    section_sha256 = hashlib.sha256(
        source_text[char_start:char_end].encode()
    ).hexdigest()
    item = SecFilingSection(
        section_id=section_id,
        component_type=section.component_type,
        char_start=char_start,
        char_end=char_end,
        parent_source_sha256=section.parent_source_sha256,
        component_sha256=section.component_sha256,
        section_sha256=section_sha256,
        provenance_id="",
    )
    item = replace(
        item,
        provenance_id=_derived_id(
            {
                "operation": SEC_SECTION_REVISION,
                "section_id": item.section_id,
                "component_type": item.component_type,
                "char_start": item.char_start,
                "char_end": item.char_end,
                "parent_source_sha256": item.parent_source_sha256,
                "component_sha256": item.component_sha256,
                "section_sha256": item.section_sha256,
            }
        ),
    )
    validate_sec_filing_section(source_text, item)
    return item


def split_sec_filing_section_by_facts(
    source_text: str,
    section: SecFilingSection,
    facts: tuple[SecXbrlFact, ...],
) -> tuple[SecFilingSection, ...]:
    """Partition a section into exhaustive, non-copying, fact-centered views."""
    if not facts:
        raise ProvenanceError("SEC section split requires target facts")
    components = {
        item.component_type: item
        for item in _source_components(
            source_text, hashlib.sha256(source_text.encode()).hexdigest()
        )
    }
    component = components.get(section.component_type)
    if component is None:
        raise ProvenanceError("SEC filing section component lineage mismatch")
    _validate_sec_filing_section(source_text, section, component)
    for fact in facts:
        _validate_sec_xbrl_fact(source_text, fact, component)
        if not _in_section(fact, section):
            raise ProvenanceError("SEC section split fact is outside section")
    if any(left.char_end > right.char_start for left, right in pairwise(facts)):
        raise ProvenanceError("SEC section split facts are not ordered and disjoint")

    boundaries = [section.char_start]
    boundaries.extend(
        (left.char_end + right.char_start) // 2 for left, right in pairwise(facts)
    )
    boundaries.append(section.char_end)
    slices: list[SecFilingSection] = []
    for index, fact in enumerate(facts):
        start = boundaries[index]
        end = boundaries[index + 1]
        if not start <= fact.char_start < fact.char_end <= end:
            raise ProvenanceError("SEC section split does not contain target fact")
        item = derive_sec_filing_subsection(
            source_text,
            section,
            section_id=f"{section.section_id}__{fact.fact_id}",
            char_start=start,
            char_end=end,
        )
        slices.append(item)
    return tuple(slices)


def _certification_date(raw: str) -> str:
    normalized = re.sub(r"\s+", " ", unescape(raw).replace("\xa0", " ")).strip()
    try:
        parsed = time.strptime(normalized, "%B %d, %Y")
        return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday).isoformat()
    except ValueError as exc:
        raise ProvenanceError("SEC certification date is invalid") from exc


def _required_cert_match(
    pattern: re.Pattern[str], text: str, label: str
) -> re.Match[str]:
    match = pattern.search(text)
    if match is None:
        raise ProvenanceError(f"SEC certification is missing {label}")
    return match


def parse_sec_certification_facts(
    source_text: str, component: SecFilingComponent
) -> tuple[SecCertificationFact, ...]:
    _validate_source_component(source_text, component)
    block = source_text[component.char_start : component.char_end]
    facts: list[SecCertificationFact] = []
    name_pattern = (
        _GCS_906_CERT_NAME
        if component.parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION
        and component.component_type.startswith("EX-32.")
        else _CERT_NAME
    )
    name_matches = list(name_pattern.finditer(block))
    for index, match in enumerate(name_matches):
        segment_start = match.start()
        segment_end = (
            name_matches[index + 1].start()
            if index + 1 < len(name_matches)
            else len(block)
        )
        segment = block[segment_start:segment_end]
        report_start = 0 if index == 0 else segment_start
        report_segment = block[report_start:segment_end]
        name = match.group(1).strip()
        char_start = component.char_start + match.start(1)
        char_end = char_start + len(name)
        if source_text[char_start:char_end] != name:
            raise ProvenanceError("SEC certification name span does not match source")

        title_hits = [
            (segment.rfind(title), title)
            for title in _OFFICER_TITLES
            if title in segment
        ]
        if not title_hits:
            raise ProvenanceError("SEC certification is missing officer title")
        title_offset, officer_title = max(title_hits)
        title_start = component.char_start + segment_start + title_offset
        title_end = title_start + len(officer_title)

        form_match = _required_cert_match(_CERT_FORM, report_segment, "covered form")
        form_start = component.char_start + report_start + form_match.start()
        form_end = component.char_start + report_start + form_match.end()
        covered_form = source_text[form_start:form_end]

        date_pattern = (
            _GCS_CERT_DATE
            if component.parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION
            else _CERT_DATE
        )
        date_matches = list(date_pattern.finditer(segment))
        if not date_matches:
            raise ProvenanceError("SEC certification is missing signature date")
        date_match = date_matches[-1]
        date_start = component.char_start + segment_start + date_match.start(1)
        date_end = component.char_start + segment_start + date_match.end(1)
        certification_date = _certification_date(source_text[date_start:date_end])

        period_match = _CERT_PERIOD.search(report_segment)
        if period_match is None:
            covered_period_end = ""
            period_start = 0
            period_end = 0
        else:
            period_start = component.char_start + report_start + period_match.start(1)
            period_end = component.char_start + report_start + period_match.end(1)
            covered_period_end = _certification_date(
                source_text[period_start:period_end]
            )

        if component.component_type.startswith("EX-31."):
            certification_kind = "section_302"
            if component.parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION:
                kind_text = f"Exhibit {component.component_type.removeprefix('EX-')}"
                kind_offsets = [
                    match.start() for match in re.finditer(re.escape(kind_text), block)
                ]
                if len(kind_offsets) != 1:
                    raise ProvenanceError(
                        "SEC certification kind evidence is missing or ambiguous"
                    )
                kind_offset = kind_offsets[0]
            else:
                kind_text = component.component_type
                kind_offset = block.find(kind_text)
                if kind_offset < 0:
                    raise ProvenanceError("SEC certification kind evidence is missing")
            kind_start = component.char_start + kind_offset
            kind_end = kind_start + len(kind_text)
        else:
            certification_kind = "section_906"
            kind_match = _required_cert_match(
                _CERT_906, report_segment, "Section 906 basis"
            )
            kind_start = component.char_start + report_start + kind_match.start()
            kind_end = component.char_start + report_start + kind_match.end()
            if not covered_period_end:
                raise ProvenanceError("SEC Section 906 certification has no period end")

        fact = SecCertificationFact(
            component_type=component.component_type,
            name=name,
            char_start=char_start,
            char_end=char_end,
            parent_source_sha256=component.parent_source_sha256,
            component_sha256=component.component_sha256,
            officer_title=officer_title,
            officer_title_char_start=title_start,
            officer_title_char_end=title_end,
            certification_kind=certification_kind,
            kind_char_start=kind_start,
            kind_char_end=kind_end,
            covered_form=covered_form,
            form_char_start=form_start,
            form_char_end=form_end,
            covered_period_end=covered_period_end,
            period_char_start=period_start,
            period_char_end=period_end,
            certification_date=certification_date,
            date_char_start=date_start,
            date_char_end=date_end,
            provenance_id="",
        )
        fact = replace(fact, provenance_id=_certification_provenance(fact))
        _validate_sec_certification_fact(source_text, fact, component)
        facts.append(fact)
    if component.component_type == "EX-32.1":
        if len(facts) not in {1, 2}:
            raise ProvenanceError("SEC certification officer count is invalid")
    elif len(facts) != 1:
        raise ProvenanceError("SEC certification officer count is invalid")
    return tuple(facts)


def _certification_provenance(fact: SecCertificationFact) -> str:
    return _derived_id(
        {
            "operation": SEC_CERT_FACT_REVISION,
            "component_type": fact.component_type,
            "name": fact.name,
            "char_start": fact.char_start,
            "char_end": fact.char_end,
            "parent_source_sha256": fact.parent_source_sha256,
            "component_sha256": fact.component_sha256,
            "officer_title": fact.officer_title,
            "officer_title_char_start": fact.officer_title_char_start,
            "officer_title_char_end": fact.officer_title_char_end,
            "certification_kind": fact.certification_kind,
            "kind_char_start": fact.kind_char_start,
            "kind_char_end": fact.kind_char_end,
            "covered_form": fact.covered_form,
            "form_char_start": fact.form_char_start,
            "form_char_end": fact.form_char_end,
            "covered_period_end": fact.covered_period_end,
            "period_char_start": fact.period_char_start,
            "period_char_end": fact.period_char_end,
            "certification_date": fact.certification_date,
            "date_char_start": fact.date_char_start,
            "date_char_end": fact.date_char_end,
        }
    )


def _validate_sec_certification_fact(
    source_text: str,
    fact: SecCertificationFact,
    component: SecFilingComponent,
) -> None:
    _validate_source_component(source_text, component)
    parent_source_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
    if fact.parent_source_sha256 != parent_source_sha256:
        raise ProvenanceError("SEC certification parent source hash mismatch")
    if (
        isinstance(fact.char_start, bool)
        or isinstance(fact.char_end, bool)
        or not isinstance(fact.char_start, int)
        or not isinstance(fact.char_end, int)
        or fact.char_start < component.char_start
        or fact.char_end > component.char_end
        or fact.char_end <= fact.char_start
        or source_text[fact.char_start : fact.char_end] != fact.name
    ):
        raise ProvenanceError("SEC certification evidence span does not match source")
    if (
        fact.component_type != component.component_type
        or fact.component_sha256 != component.component_sha256
    ):
        raise ProvenanceError("SEC certification component lineage mismatch")
    spans = (
        (fact.officer_title_char_start, fact.officer_title_char_end),
        (fact.kind_char_start, fact.kind_char_end),
        (fact.form_char_start, fact.form_char_end),
        (fact.date_char_start, fact.date_char_end),
    )
    if any(
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < component.char_start
        or end <= start
        or end > component.char_end
        for start, end in spans
    ):
        raise ProvenanceError("SEC certification attribute span is invalid")
    if (
        fact.officer_title not in _OFFICER_TITLES
        or source_text[fact.officer_title_char_start : fact.officer_title_char_end]
        != fact.officer_title
        or source_text[fact.form_char_start : fact.form_char_end].lower() != "form 10-k"
        or source_text[fact.form_char_start : fact.form_char_end] != fact.covered_form
        or fact.covered_form.lower() != "form 10-k"
        or _certification_date(source_text[fact.date_char_start : fact.date_char_end])
        != fact.certification_date
    ):
        raise ProvenanceError("SEC certification attributes do not match source")
    kind_quote = re.sub(
        r"\s+",
        " ",
        unescape(source_text[fact.kind_char_start : fact.kind_char_end]).replace(
            "\xa0", " "
        ),
    ).strip()
    if fact.component_type.startswith("EX-31."):
        expected_kind_quote = fact.component_type
        if component.parser_revision == ISSUER_GCS_MERGED_COMPONENT_REVISION:
            expected_kind_quote = f"Exhibit {fact.component_type.removeprefix('EX-')}"
        if (
            fact.certification_kind != "section_302"
            or kind_quote != expected_kind_quote
            or fact.covered_period_end
            or fact.period_char_start != 0
            or fact.period_char_end != 0
        ):
            raise ProvenanceError("SEC Section 302 certification metadata is invalid")
    elif (
        fact.certification_kind != "section_906"
        or kind_quote.lower() != "section 906"
        or not fact.covered_period_end
        or fact.period_char_start < component.char_start
        or fact.period_char_end <= fact.period_char_start
        or fact.period_char_end > component.char_end
        or _certification_date(
            source_text[fact.period_char_start : fact.period_char_end]
        )
        != fact.covered_period_end
    ):
        raise ProvenanceError("SEC Section 906 certification metadata is invalid")
    if fact.provenance_id != _certification_provenance(fact):
        raise ProvenanceError("SEC certification provenance mismatch")


def validate_sec_certification_fact(
    source_text: str, fact: SecCertificationFact
) -> None:
    """Replay a certification officer span against its exact exhibit component."""
    components = {
        item.component_type: item
        for item in _source_components(
            source_text, hashlib.sha256(source_text.encode()).hexdigest()
        )
    }
    component = components.get(fact.component_type)
    if component is None:
        raise ProvenanceError("SEC certification component lineage mismatch")
    _validate_sec_certification_fact(source_text, fact, component)


def _in_section(fact: SecXbrlFact, section: SecFilingSection) -> bool:
    return section.char_start <= fact.char_start < fact.char_end <= section.char_end


def _fy_duration(fact: SecXbrlFact, report_date: str) -> bool:
    return (not fact.instant) and fact.end_date == report_date


def _fy_instant(fact: SecXbrlFact, report_date: str) -> bool:
    return fact.instant and fact.end_date == report_date and not fact.members


def _one(facts: list[SecXbrlFact], label: str) -> SecXbrlFact:
    if not facts:
        raise ProvenanceError(f"SEC financial program is missing {label}")
    signatures = {
        (
            fact.numeric_value,
            fact.unit_ref,
            fact.decimals,
            fact.scale,
            fact.sign,
            fact.start_date,
            fact.end_date,
            fact.instant,
            fact.members,
        )
        for fact in facts
    }
    if len(signatures) != 1:
        raise ProvenanceError(
            f"SEC financial program has a conflicting duplicate for {label}"
        )
    return min(facts, key=lambda item: item.char_start)


def _one_by_member(
    facts: list[SecXbrlFact], axis: str, label: str
) -> dict[str, SecXbrlFact]:
    grouped: dict[str, list[SecXbrlFact]] = {}
    for fact in facts:
        member = fact.member(axis)
        if not member:
            raise ProvenanceError(
                f"SEC financial program is missing member for {label}"
            )
        grouped.setdefault(member, []).append(fact)
    return {
        member: _one(member_facts, f"{label} member {member}")
        for member, member_facts in grouped.items()
    }


def _header_cik(source_text: str) -> str:
    values = {
        match.group(1).zfill(10) for match in _CENTRAL_INDEX_KEY.finditer(source_text)
    }
    if not values and "microsoft.gcs-web.com/sec-filings/sec-filing/" in source_text:
        values = set(_GCS_CIK.findall(source_text))
    if len(values) != 1:
        raise ProvenanceError("SEC financial program issuer CIK is not unique")
    return next(iter(values))


def parse_sec_financial_program(
    source_text: str, parent_source_sha256: str, *, report_date: str
) -> SecFinancialProgram:
    """Select staged, non-overlapping sections whose facts enter answer programs."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date or ""):
        raise ProvenanceError("SEC financial program report date is invalid")
    spec = _ISSUER_SPECS.get(_header_cik(source_text))
    if spec is None:
        raise ProvenanceError("SEC financial program is not defined for this issuer")
    components = {
        item.component_type: item
        for item in _source_components(source_text, parent_source_sha256)
    }
    required = (*SEC_REQUIRED_COMPONENT_TYPES, *spec.extra_cert_components)
    missing = [item for item in required if item not in components]
    if missing:
        raise ProvenanceError("SEC financial program is missing required components")
    filing = components["10-K"]
    facts = parse_sec_ixbrl_facts(source_text, filing)
    sections = [
        _section_from_headings(
            source_text,
            filing,
            section_id=section_id,
            start_heading=start_heading,
            end_heading=end_heading,
            max_chars=max_chars,
        )
        for section_id, _component_type, start_heading, end_heading, max_chars in spec.headings
    ]
    cert_types = ("EX-31.1", "EX-31.2", "EX-32.1", *spec.extra_cert_components)
    sections.extend(
        [
            _component_section(
                source_text,
                components[component_type],
                component_type.lower().replace("-", "_").replace(".", "_"),
            )
            for component_type in cert_types
        ]
    )
    ordered = tuple(sorted(sections, key=lambda item: item.char_start))
    for left, right in pairwise(ordered):
        if left.char_end > right.char_start:
            raise ProvenanceError("SEC financial sections overlap")
    by_id = {section.section_id: section for section in ordered}
    ops = by_id["item8_operations"]
    note2 = by_id["note2_revenue"]
    note13 = by_id["note13_segments"]
    balance = by_id["item8_balance_sheet"]
    all_revenue = [
        fact for fact in facts if fact.concept == REVENUE_CONCEPT and not fact.instant
    ]
    duration_revenue = [fact for fact in all_revenue if _fy_duration(fact, report_date)]
    roles: dict[str, SecXbrlFact] = {
        "product_revenue": _one(
            [
                fact
                for fact in duration_revenue
                if _in_section(fact, ops)
                and fact.member(PRODUCT_AXIS) == PRODUCT_MEMBER
            ],
            "product revenue",
        ),
        "service_revenue": _one(
            [
                fact
                for fact in duration_revenue
                if _in_section(fact, ops)
                and fact.member(PRODUCT_AXIS) == spec.service_member
            ],
            "service revenue",
        ),
        "total_revenue": _one(
            [
                fact
                for fact in duration_revenue
                if _in_section(fact, ops) and not fact.members
            ],
            "total revenue",
        ),
        "assets": _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:Assets"
                and _fy_instant(fact, report_date)
                and _in_section(fact, balance)
            ],
            "assets",
        ),
        "equity": _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:StockholdersEquity"
                and _fy_instant(fact, report_date)
                and _in_section(fact, balance)
            ],
            "equity",
        ),
        "liabilities_and_equity": _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:LiabilitiesAndStockholdersEquity"
                and _fy_instant(fact, report_date)
                and _in_section(fact, balance)
            ],
            "liabilities and equity",
        ),
    }
    if spec.require_liabilities:
        roles["liabilities"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:Liabilities"
                and _fy_instant(fact, report_date)
                and _in_section(fact, balance)
            ],
            "liabilities",
        )
    prior_periods = sorted(
        {
            fact.end_date
            for fact in all_revenue
            if fact.end_date < report_date and _in_section(fact, ops)
        }
    )
    if not prior_periods:
        raise ProvenanceError("SEC financial program is missing prior-period revenue")
    prior_report_date = prior_periods[-1]
    roles["prior_total_revenue"] = _one(
        [
            fact
            for fact in all_revenue
            if fact.end_date == prior_report_date
            and _in_section(fact, ops)
            and not fact.members
        ],
        "prior-period total revenue",
    )
    category = sorted(
        _one_by_member(
            [
                fact
                for fact in duration_revenue
                if _in_section(fact, note2)
                and fact.member(PRODUCT_AXIS)
                and fact.member(PRODUCT_AXIS) not in spec.category_exclude_members
            ],
            PRODUCT_AXIS,
            "category revenue",
        ).values(),
        key=lambda item: item.char_start,
    )
    if spec.geo_requires_operating_segment:
        geo = sorted(
            _one_by_member(
                [
                    fact
                    for fact in duration_revenue
                    if _in_section(fact, note13)
                    and fact.member(spec.geo_axis)
                    and fact.member(CONSOLIDATION_AXIS) == OPERATING_SEGMENT_MEMBER
                ],
                spec.geo_axis,
                "geographic revenue",
            ).values(),
            key=lambda item: item.char_start,
        )
    else:
        geo = sorted(
            _one_by_member(
                [
                    fact
                    for fact in duration_revenue
                    if _in_section(fact, note13) and fact.member(spec.geo_axis)
                ],
                spec.geo_axis,
                "geographic revenue",
            ).values(),
            key=lambda item: item.char_start,
        )
    if len(category) != spec.n_category:
        raise ProvenanceError("SEC financial program category mix is incomplete")
    if len(geo) != spec.n_geo:
        raise ProvenanceError("SEC financial program geographic mix is incomplete")
    prior_geo_by_member = _one_by_member(
        [
            fact
            for fact in all_revenue
            if fact.end_date == prior_report_date
            and _in_section(fact, note13)
            and fact.member(spec.geo_axis)
            and (
                not spec.geo_requires_operating_segment
                or fact.member(CONSOLIDATION_AXIS) == OPERATING_SEGMENT_MEMBER
            )
        ],
        spec.geo_axis,
        "prior geographic revenue",
    )
    geo_members = [fact.member(spec.geo_axis) for fact in geo]
    if (
        len(prior_geo_by_member) != spec.n_geo
        or len(set(geo_members)) != spec.n_geo
        or set(prior_geo_by_member) != set(geo_members)
    ):
        raise ProvenanceError(
            "SEC financial program prior geographic mix is incomplete"
        )
    for index, fact in enumerate(category):
        roles[f"category_{index}"] = fact
    for index, fact in enumerate(geo):
        roles[f"geo_{index}"] = fact
        roles[f"prior_geo_{index}"] = prior_geo_by_member[fact.member(spec.geo_axis)]
    total = roles["total_revenue"].numeric_value
    if sum(fact.numeric_value for fact in category) != total:
        raise ProvenanceError("SEC category mix does not reconcile to total revenue")
    if sum(fact.numeric_value for fact in geo) != total:
        raise ProvenanceError("SEC geographic mix does not reconcile to total revenue")
    if (
        sum(fact.numeric_value for fact in prior_geo_by_member.values())
        != roles["prior_total_revenue"].numeric_value
    ):
        raise ProvenanceError(
            "SEC prior geographic mix does not reconcile to total revenue"
        )
    if (
        roles["product_revenue"].numeric_value + roles["service_revenue"].numeric_value
        != total
    ):
        raise ProvenanceError("SEC product/service mix does not reconcile to total")
    if roles["assets"].numeric_value != roles["liabilities_and_equity"].numeric_value:
        raise ProvenanceError("SEC balance-sheet identity does not hold")
    if spec.require_liabilities and (
        roles["liabilities"].numeric_value + roles["equity"].numeric_value
        != roles["assets"].numeric_value
    ):
        raise ProvenanceError("SEC balance-sheet identity does not hold")
    cash = by_id.get("item8_cash_flow")
    if cash is not None:
        roles["cfo"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:NetCashProvidedByUsedInOperatingActivities"
                and _fy_duration(fact, report_date)
                and _in_section(fact, cash)
                and not fact.members
            ],
            "operating cash flow",
        )
        roles["cfi"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:NetCashProvidedByUsedInInvestingActivities"
                and _fy_duration(fact, report_date)
                and _in_section(fact, cash)
                and not fact.members
            ],
            "investing cash flow",
        )
        roles["cff"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:NetCashProvidedByUsedInFinancingActivities"
                and _fy_duration(fact, report_date)
                and _in_section(fact, cash)
                and not fact.members
            ],
            "financing cash flow",
        )
        roles["delta_cash"] = _one(
            [
                fact
                for fact in facts
                if fact.concept
                == (
                    "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCash"
                    "EquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect"
                )
                and _fy_duration(fact, report_date)
                and _in_section(fact, cash)
                and not fact.members
            ],
            "change in cash",
        )
        cash_sum = (
            roles["cfo"].numeric_value
            + roles["cfi"].numeric_value
            + roles["cff"].numeric_value
        )
        if spec.cash_flow_includes_fx:
            roles["fx"] = _one(
                [
                    fact
                    for fact in facts
                    if fact.concept == FX_CASH_CONCEPT
                    and _fy_duration(fact, report_date)
                    and _in_section(fact, cash)
                    and not fact.members
                ],
                "cash-flow FX effect",
            )
            cash_sum += roles["fx"].numeric_value
        if cash_sum != roles["delta_cash"].numeric_value:
            raise ProvenanceError("SEC cash-flow identity does not hold")
    note7 = by_id.get("note7_income_taxes")
    note9_tax = by_id.get("note9_income_taxes")
    if note7 is not None:
        roles["federal_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept
                == "us-gaap:FederalIncomeTaxExpenseBenefitContinuingOperations"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note7)
                and not fact.members
            ],
            "federal income tax",
        )
        roles["state_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept
                == "us-gaap:StateAndLocalIncomeTaxExpenseBenefitContinuingOperations"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note7)
                and not fact.members
            ],
            "state income tax",
        )
        roles["foreign_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept
                == "us-gaap:ForeignIncomeTaxExpenseBenefitContinuingOperations"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note7)
                and not fact.members
            ],
            "foreign income tax",
        )
        roles["income_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:IncomeTaxExpenseBenefit"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note7)
                and not fact.members
            ],
            "income tax expense",
        )
        if (
            roles["federal_tax"].numeric_value
            + roles["state_tax"].numeric_value
            + roles["foreign_tax"].numeric_value
            != roles["income_tax"].numeric_value
        ):
            raise ProvenanceError("SEC income-tax roll does not hold")
    elif note9_tax is not None:
        roles["federal_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:IncomeTaxExpenseBenefit"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note9_tax)
                and fact.member(TAX_AUTHORITY_AXIS) == DOMESTIC_TAX_MEMBER
            ],
            "domestic income tax",
        )
        roles["state_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:IncomeTaxExpenseBenefit"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note9_tax)
                and fact.member(TAX_AUTHORITY_AXIS) == STATE_TAX_MEMBER
            ],
            "state income tax",
        )
        roles["foreign_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:IncomeTaxExpenseBenefit"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note9_tax)
                and fact.member(TAX_AUTHORITY_AXIS) == FOREIGN_TAX_MEMBER
            ],
            "foreign income tax",
        )
        roles["income_tax"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:IncomeTaxExpenseBenefit"
                and _fy_duration(fact, report_date)
                and _in_section(fact, note9_tax)
                and not fact.members
            ],
            "income tax expense",
        )
        if (
            roles["federal_tax"].numeric_value
            + roles["state_tax"].numeric_value
            + roles["foreign_tax"].numeric_value
            != roles["income_tax"].numeric_value
        ):
            raise ProvenanceError("SEC income-tax roll does not hold")
    note8 = by_id.get("note8_leases") or by_id.get("note4_leases")
    if note8 is not None:
        roles["lease_current"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:OperatingLeaseLiabilityCurrent"
                and _fy_instant(fact, report_date)
                and _in_section(fact, note8)
            ],
            "current lease liability",
        )
        roles["lease_noncurrent"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:OperatingLeaseLiabilityNoncurrent"
                and _fy_instant(fact, report_date)
                and _in_section(fact, note8)
            ],
            "noncurrent lease liability",
        )
        roles["lease_total"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:OperatingLeaseLiability"
                and _fy_instant(fact, report_date)
                and _in_section(fact, note8)
            ],
            "total lease liability",
        )
        if (
            roles["lease_current"].numeric_value
            + roles["lease_noncurrent"].numeric_value
            != roles["lease_total"].numeric_value
        ):
            raise ProvenanceError("SEC lease identity does not hold")
    note9 = by_id.get("note9_debt")
    if note9 is not None:
        roles["debt_current"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:LongTermDebtCurrent"
                and _fy_instant(fact, report_date)
                and _in_section(fact, note9)
            ],
            "current debt",
        )
        roles["debt_noncurrent"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:LongTermDebtNoncurrent"
                and _fy_instant(fact, report_date)
                and _in_section(fact, note9)
            ],
            "noncurrent debt",
        )
        roles["debt_total"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == "us-gaap:LongTermDebt"
                and _fy_instant(fact, report_date)
                and _in_section(fact, note9)
            ],
            "total debt",
        )
        if (
            roles["debt_current"].numeric_value + roles["debt_noncurrent"].numeric_value
            != roles["debt_total"].numeric_value
        ):
            raise ProvenanceError("SEC debt identity does not hold")
    note10 = by_id.get("note10_segment_oi")
    if note10 is not None:
        segment_oi = sorted(
            (
                fact
                for fact in facts
                if fact.concept == OPERATING_INCOME_CONCEPT
                and _fy_duration(fact, report_date)
                and _in_section(fact, note10)
                and fact.member(SEGMENT_AXIS)
            ),
            key=lambda item: item.char_start,
        )
        if len(segment_oi) != 3:
            raise ProvenanceError("SEC segment operating-income count is invalid")
        for index, fact in enumerate(segment_oi):
            roles[f"oi_{index}"] = fact
        roles["oi_total"] = _one(
            [
                fact
                for fact in facts
                if fact.concept == OPERATING_INCOME_CONCEPT
                and _fy_duration(fact, report_date)
                and _in_section(fact, note10)
                and not fact.members
            ],
            "total operating income",
        )
        if (
            roles["oi_0"].numeric_value
            + roles["oi_1"].numeric_value
            + roles["oi_2"].numeric_value
            != roles["oi_total"].numeric_value
        ):
            raise ProvenanceError("SEC segment operating-income identity does not hold")
    certifications = {
        component_type.lower()
        .replace("-", "_")
        .replace(".", "_"): parse_sec_certification_facts(
            source_text, components[component_type]
        )
        for component_type in cert_types
    }
    return SecFinancialProgram(
        sections=ordered,
        roles=roles,
        certifications=certifications,
        note13_max_chars=SEC_NOTE13_MAX_CHARS,
        n_category=spec.n_category,
        n_geo=spec.n_geo,
        require_liabilities=spec.require_liabilities,
        section_ground=spec.section_ground,
        extra_128k_sections=spec.extra_128k_sections,
    )
