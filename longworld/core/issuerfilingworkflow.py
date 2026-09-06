"""Auditable issuer-owned filing history and rendered-XBRL metric parsing."""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.attestation import attestation_key_from_env, verify_attestation
from longworld.core.filingworkflow import _EMAIL, _reject_secrets
from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

ISSUER_IR_FILING_MANIFEST_SCHEMA = "longworld.issuer-ir-filing-manifest.v1"
ISSUER_IR_SOURCE_KIND = "issuer_ir_filing"
ISSUER_IR_SOURCE_FAMILY = "issuer_ir_rendered_xbrl"
MAX_ISSUER_IR_MANIFEST_BYTES = 32_000_000
ISSUER_IR_HYBRID_CHILD_EVENT_TYPES = frozenset(
    {"issuer_ir_prior_filing_relation", "issuer_ir_cross_year_answer"}
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CIK = re.compile(r"^\d{10}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_JWT_SHAPED = re.compile(
    rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
_NUMBER = re.compile(r"\(?\$?[ \t]*[0-9][0-9,]*\)?")
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_HEADER_CELL = re.compile(r"<th\b[^>]*>(.*?)</th>", re.IGNORECASE | re.DOTALL)
ISSUER_IR_OPERATIONS_SECTION = "Consolidated Statements of Operations"
ISSUER_IR_BALANCE_SECTION = "Consolidated Balance Sheets"
ISSUER_IR_CASH_FLOW_SECTION = (
    "Consolidated Statements of Cash Flows - USD ($) $ in Millions"
)
ISSUER_IR_EQUITY_SECTION = (
    "Consolidated Statements of Stockholders' Equity - USD ($) shares in Millions, "
    "$ in Millions"
)
ISSUER_IR_SEGMENT_SECTION = (
    "Segment Information - Reportable Segments and Reconciliation to Consolidated "
    "Net Income (Details) - USD ($) $ in Millions"
)
ISSUER_IR_REVENUE_SECTION = (
    "Segment Information - Disaggregation of Revenue (Details) - USD ($) $ in Millions"
)
ISSUER_IR_LEASE_SECTION = (
    "Leases - Operating and Finance Lease Reconciliation (Details) - USD ($) $ in "
    "Millions"
)
ISSUER_IR_COMMITMENT_SECTION = (
    "Commitments and Contingencies - Principal Contractual Commitments Excluding "
    "Open Orders (Details) - USD ($) $ in Millions"
)
ISSUER_IR_TAX_SECTION = (
    "Income Taxes - Components of Provision for Income Taxes, Net (Details) - USD "
    "($) $ in Millions"
)
ISSUER_IR_FAIR_VALUE_SECTION = (
    "Financial Instruments - Fair Values on Recurring Basis (Details) - USD ($) $ "
    "in Millions"
)
ISSUER_IR_POLICY_SECTION = (
    "Description of Business, Accounting Policies, and Supplemental Disclosures "
    "(Policies)"
)
_ISSUER_IR_TAX_RATE_RECONCILIATION_SECTION = (
    "Income Taxes - Items Accounting for Differences Between Income Taxes "
    "Computed at Federal Statutory Rate and Provision Recorded for Income "
    "Taxes (Details) - USD ($) $ in Millions"
)

_SECTION_TITLE_ALIASES = {
    _ISSUER_IR_TAX_RATE_RECONCILIATION_SECTION: (
        (
            "Income Taxes - Items Accounting for Differences Between Income Taxes "
            "Computed at Federal Statutory Rate and Provision Recorded for Income "
            "Taxes (Details) - USD ($)"
        ),
    ),
}

_CONCEPT_ALIASES = {
    "cash_fx_effect": ("defref_us-gaap_EffectOfExchangeRateOnCashAndCashEquivalents",),
}

_DISCLOSURE_SECTIONS = (
    (
        "disclosure_debt_terms",
        "Debt - Additional Information (Details)",
        "Debt - Additional Information (Details)",
    ),
    (
        "disclosure_acquired_intangibles",
        (
            "Acquisitions, Goodwill, and Acquired Intangible Assets - Acquired "
            "Intangible Assets (Details) - USD ($) $ in Millions"
        ),
        (
            "Acquisitions, Goodwill, and Acquired Intangible Assets - Acquired "
            "Intangible Assets (Details)"
        ),
    ),
    (
        "disclosure_other_income",
        (
            "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures - Other Income (Expense), Net (Details) - USD ($) shares "
            "in Millions, $ in Millions"
        ),
        (
            "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures - Other Income (Expense), Net (Details)"
        ),
    ),
    (
        "disclosure_equity_detail",
        "Stockholders' Equity - Additional Information (Details) - USD ($)",
        "Stockholders' Equity - Additional Information (Details)",
    ),
    (
        "disclosure_segment_capex",
        (
            "Segment Information - Reconciliation of Property and Equipment "
            "Additions from Segments to Consolidated (Details) - USD ($) $ in Millions"
        ),
        (
            "Segment Information - Reconciliation of Property and Equipment "
            "Additions from Segments to Consolidated (Details)"
        ),
    ),
    (
        "disclosure_deferred_tax",
        (
            "Income Taxes - Deferred Income Tax Assets and Liabilities (Details) "
            "- USD ($) $ in Millions"
        ),
        "Income Taxes - Deferred Income Tax Assets and Liabilities (Details)",
    ),
    (
        "disclosure_stock_compensation",
        (
            "Stockholders' Equity - Stock-based Compensation Expense (Details) "
            "- USD ($) $ in Millions"
        ),
        "Stockholders' Equity - Stock-based Compensation Expense (Details)",
    ),
    (
        "disclosure_goodwill_rollforward",
        (
            "Acquisitions, Goodwill, and Acquired Intangible Assets - Summary of "
            "Goodwill Activity by Segment (Details) - USD ($) $ in Millions"
        ),
        (
            "Acquisitions, Goodwill, and Acquired Intangible Assets - Summary of "
            "Goodwill Activity by Segment (Details)"
        ),
    ),
    (
        "disclosure_supplemental_cashflow",
        (
            "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures - Supplemental Cash Flow Information (Details) - USD ($) "
            "$ in Millions"
        ),
        (
            "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures - Supplemental Cash Flow Information (Details)"
        ),
    ),
    (
        "disclosure_accounts_receivable",
        (
            "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures - Accounts Receivable, Net and Other (Details) - USD ($) "
            "$ in Millions"
        ),
        (
            "Description of Business, Accounting Policies, and Supplemental "
            "Disclosures - Accounts Receivable, Net and Other (Details)"
        ),
    ),
    (
        "disclosure_rsu_activity",
        (
            "Stockholders' Equity - Restricted Stock Unit Activity (Details) - "
            "Restricted Stock Units - $ / shares shares in Millions"
        ),
        "Stockholders' Equity - Restricted Stock Unit Activity (Details)",
    ),
    (
        "disclosure_tax_rate_reconciliation",
        _ISSUER_IR_TAX_RATE_RECONCILIATION_SECTION,
        (
            "Income Taxes - Items Accounting for Differences Between Income Taxes "
            "Computed at Federal Statutory Rate and Provision Recorded for Income "
            "Taxes (Details)"
        ),
    ),
)

ISSUER_IR_SECTIONS_16K = (
    ISSUER_IR_OPERATIONS_SECTION,
    ISSUER_IR_BALANCE_SECTION,
    ISSUER_IR_CASH_FLOW_SECTION,
    ISSUER_IR_EQUITY_SECTION,
    ISSUER_IR_SEGMENT_SECTION,
    ISSUER_IR_LEASE_SECTION,
    ISSUER_IR_COMMITMENT_SECTION,
    ISSUER_IR_TAX_SECTION,
    ISSUER_IR_FAIR_VALUE_SECTION,
    _DISCLOSURE_SECTIONS[2][1],
)
ISSUER_IR_SECTIONS_32K = (
    ISSUER_IR_OPERATIONS_SECTION,
    ISSUER_IR_BALANCE_SECTION,
    ISSUER_IR_CASH_FLOW_SECTION,
    ISSUER_IR_EQUITY_SECTION,
    ISSUER_IR_SEGMENT_SECTION,
    ISSUER_IR_REVENUE_SECTION,
    ISSUER_IR_LEASE_SECTION,
    ISSUER_IR_COMMITMENT_SECTION,
    ISSUER_IR_TAX_SECTION,
    ISSUER_IR_FAIR_VALUE_SECTION,
    _DISCLOSURE_SECTIONS[0][1],
    _DISCLOSURE_SECTIONS[1][1],
    _DISCLOSURE_SECTIONS[2][1],
)
ISSUER_IR_SECTIONS_64K = (
    ISSUER_IR_OPERATIONS_SECTION,
    ISSUER_IR_BALANCE_SECTION,
    ISSUER_IR_CASH_FLOW_SECTION,
    ISSUER_IR_EQUITY_SECTION,
    ISSUER_IR_SEGMENT_SECTION,
    ISSUER_IR_REVENUE_SECTION,
    ISSUER_IR_LEASE_SECTION,
    ISSUER_IR_COMMITMENT_SECTION,
    ISSUER_IR_TAX_SECTION,
    ISSUER_IR_FAIR_VALUE_SECTION,
    *(section for _, section, _ in _DISCLOSURE_SECTIONS),
    ISSUER_IR_POLICY_SECTION,
)
ISSUER_IR_SECTIONS_64K_REQUIRED = (*ISSUER_IR_SECTIONS_64K[:19],)

_METRICS = (
    (
        "revenue",
        ISSUER_IR_OPERATIONS_SECTION,
        "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        0,
    ),
    (
        "operating_income",
        ISSUER_IR_OPERATIONS_SECTION,
        "defref_us-gaap_OperatingIncomeLoss",
        0,
    ),
    (
        "assets",
        ISSUER_IR_BALANCE_SECTION,
        "defref_us-gaap_Assets",
        0,
    ),
    (
        "current_liabilities",
        ISSUER_IR_BALANCE_SECTION,
        "defref_us-gaap_LiabilitiesCurrent",
        0,
    ),
    (
        "stockholders_equity",
        ISSUER_IR_BALANCE_SECTION,
        "defref_us-gaap_StockholdersEquity",
        0,
    ),
    (
        "liabilities_and_equity",
        ISSUER_IR_BALANCE_SECTION,
        "defref_us-gaap_LiabilitiesAndStockholdersEquity",
        0,
    ),
    (
        "cash_from_operations",
        ISSUER_IR_CASH_FLOW_SECTION,
        "defref_us-gaap_NetCashProvidedByUsedInOperatingActivities",
        0,
    ),
    (
        "cash_from_investing",
        ISSUER_IR_CASH_FLOW_SECTION,
        "defref_us-gaap_NetCashProvidedByUsedInInvestingActivities",
        0,
    ),
    (
        "cash_from_financing",
        ISSUER_IR_CASH_FLOW_SECTION,
        "defref_us-gaap_NetCashProvidedByUsedInFinancingActivities",
        0,
    ),
    (
        "cash_fx_effect",
        ISSUER_IR_CASH_FLOW_SECTION,
        "defref_us-gaap_EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        0,
    ),
    (
        "cash_period_change",
        ISSUER_IR_CASH_FLOW_SECTION,
        "defref_us-gaap_CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        0,
    ),
    (
        "equity_rollforward_end",
        ISSUER_IR_EQUITY_SECTION,
        "defref_us-gaap_StockholdersEquity",
        -1,
    ),
    (
        "segment_consolidated_revenue",
        ISSUER_IR_SEGMENT_SECTION,
        "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        0,
    ),
    (
        "channel_consolidated_revenue",
        ISSUER_IR_REVENUE_SECTION,
        "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        0,
    ),
    (
        "operating_lease_gross",
        ISSUER_IR_LEASE_SECTION,
        "defref_us-gaap_LesseeOperatingLeaseLiabilityPaymentsDue",
        0,
    ),
    (
        "finance_lease_gross",
        ISSUER_IR_LEASE_SECTION,
        "defref_us-gaap_FinanceLeaseLiabilityPaymentsDue",
        0,
    ),
    (
        "lease_gross",
        ISSUER_IR_LEASE_SECTION,
        "defref_amzn_LeaseLiabilityPaymentsDue",
        0,
    ),
    (
        "lease_present_value",
        ISSUER_IR_LEASE_SECTION,
        "defref_amzn_LeaseLiability",
        0,
    ),
    (
        "lease_noncurrent",
        ISSUER_IR_LEASE_SECTION,
        "defref_amzn_LeaseLiabilityNoncurrent",
        0,
    ),
    (
        "commitment_debt",
        ISSUER_IR_COMMITMENT_SECTION,
        "defref_amzn_LongTermDebtIncludingInterest",
        0,
    ),
    (
        "commitment_operating_lease",
        ISSUER_IR_COMMITMENT_SECTION,
        "defref_us-gaap_LesseeOperatingLeaseLiabilityPaymentsDue",
        0,
    ),
    (
        "commitment_finance_lease",
        ISSUER_IR_COMMITMENT_SECTION,
        "defref_us-gaap_FinanceLeaseLiabilityPaymentsDue",
        0,
    ),
    (
        "commitment_financing",
        ISSUER_IR_COMMITMENT_SECTION,
        "defref_amzn_FinancingObligationsFutureMinimumPaymentsDue",
        0,
    ),
    (
        "commitment_other",
        ISSUER_IR_COMMITMENT_SECTION,
        "defref_us-gaap_OtherCommitment",
        0,
    ),
    (
        "commitment_total",
        ISSUER_IR_COMMITMENT_SECTION,
        "defref_us-gaap_ContractualObligation",
        0,
    ),
    *(
        (
            f"tax_{authority}",
            ISSUER_IR_TAX_SECTION,
            "defref_us-gaap_IncomeTaxExpenseBenefit",
            occurrence,
        )
        for occurrence, authority in enumerate(("total", "federal", "state", "foreign"))
    ),
    (
        "cash_and_short_term_investments",
        ISSUER_IR_FAIR_VALUE_SECTION,
        "defref_amzn_CashCashEquivalentsAndMarketableSecurities",
        0,
    ),
    (
        "restricted_cash_and_investments",
        ISSUER_IR_FAIR_VALUE_SECTION,
        "defref_us-gaap_RestrictedCashAndInvestments",
        0,
    ),
    (
        "unrestricted_cash_and_investments",
        ISSUER_IR_FAIR_VALUE_SECTION,
        "defref_amzn_CashCashEquivalentsAndMarketableSecuritiesExcludingRestrictedCashAndInvestments",
        0,
    ),
)

_CONTEXT_METRICS = (
    *(
        (
            f"segment_{segment}_revenue",
            ISSUER_IR_SEGMENT_SECTION,
            "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
            label,
        )
        for segment, label in (
            ("north_america", "North America"),
            ("international", "International"),
            ("aws", "AWS"),
        )
    ),
    *(
        (
            f"channel_{channel}_revenue",
            ISSUER_IR_REVENUE_SECTION,
            "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
            label,
        )
        for channel, label in (
            ("online_stores", "Online stores"),
            ("physical_stores", "Physical stores"),
            ("third_party_services", "Third-party seller services"),
            ("advertising_services", "Advertising services"),
            ("subscription_services", "Subscription services"),
            ("aws", "AWS"),
            ("other", "Other"),
        )
    ),
)

_POLICY_FACTS = (
    (
        "policy_cash_equivalents_three_months",
        "original maturity of three months or less",
    ),
    ("policy_inventory_fifo", "first-in, first-out method"),
    (
        "policy_lease_longer_than_twelve_months",
        "contractual terms longer than twelve months",
    ),
)


@dataclass(frozen=True)
class IssuerIrMetricFact:
    role: str
    numeric_value: int
    evidence_quote: str
    char_start: int
    char_end: int
    section: str
    kind: str = "numeric"


@dataclass(frozen=True)
class IssuerIrRenderedProgram:
    report_date: str
    report_date_display: str
    report_date_char_start: int
    report_date_char_end: int
    facts: tuple[IssuerIrMetricFact, ...]
    section_ranges: tuple[tuple[str, int, int], ...]
    source_sha256: str


def _display_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.strftime('%b')}. {parsed.day:02d}, {parsed.year}"


def _statement_table(source_text: str, title: str) -> tuple[int, int]:
    title_matches = []
    accepted_titles = (title, *_SECTION_TITLE_ALIASES.get(title, ()))
    for match in re.finditer(
        r"<strong\b[^>]*>(.*?)</strong>", source_text, re.IGNORECASE | re.DOTALL
    ):
        visible_title = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        visible_title = re.sub(r"\s+", " ", visible_title).strip()
        visible_casefold = visible_title.casefold()
        if any(
            visible_casefold == accepted.casefold()
            or visible_casefold.startswith(f"{accepted.casefold()} -")
            for accepted in accepted_titles
        ):
            title_matches.append(match)
    if len(title_matches) != 1:
        raise ProvenanceError(f"issuer IR rendered XBRL is missing {title}")
    title_start = title_matches[0].start()
    table_start = source_text.rfind("<table", 0, title_start)
    table_end = source_text.find("</table>", title_start)
    if table_start < 0 or table_end < 0:
        raise ProvenanceError("issuer IR rendered XBRL statement table is invalid")
    table_end += len("</table>")
    return table_start, table_end


def _metric_fact(
    source_text: str,
    *,
    role: str,
    title: str,
    concept: str,
    table_start: int,
    table_end: int,
    occurrence: int,
    value_cell_index: int,
    display_date: str,
) -> IssuerIrMetricFact:
    table = source_text[table_start:table_end]
    exact_concepts = tuple(
        f"'{item}'" for item in (concept, *_CONCEPT_ALIASES.get(role, ()))
    )
    matches = [
        row
        for row in _ROW.finditer(table)
        if any(exact_concept in row.group() for exact_concept in exact_concepts)
    ]
    if role == "equity_rollforward_end":
        matches = [
            row
            for row in matches
            if (cells := list(_CELL.finditer(row.group())))
            and _visible_cell_text(cells[0].group(1))
            == f"Ending Balance at {display_date}"
        ]
    if not matches:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} row is missing")
    if role in _CONCEPT_ALIASES and len(matches) != 1:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} row is ambiguous")
    try:
        row = matches[occurrence]
    except IndexError as exc:
        raise ProvenanceError(
            f"issuer IR rendered XBRL {role} occurrence is missing"
        ) from exc
    cells = list(_CELL.finditer(row.group()))
    if len(cells) <= value_cell_index or not any(
        exact_concept in cells[0].group() for exact_concept in exact_concepts
    ):
        raise ProvenanceError(f"issuer IR rendered XBRL {role} row is invalid")
    value_cell = cells[value_cell_index]
    number_matches = list(_NUMBER.finditer(value_cell.group(1)))
    if len(number_matches) != 1:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} value is ambiguous")
    number = number_matches[0]
    evidence_quote = number.group()
    negative = evidence_quote.startswith("(") and evidence_quote.endswith(")")
    digits = re.sub(r"[^0-9]", "", evidence_quote)
    if not digits:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} value is invalid")
    char_start = table_start + row.start() + value_cell.start(1) + number.start()
    char_end = char_start + len(evidence_quote)
    if source_text[char_start:char_end] != evidence_quote:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} span mismatch")
    return IssuerIrMetricFact(
        role=role,
        numeric_value=-int(digits) if negative else int(digits),
        evidence_quote=evidence_quote,
        char_start=char_start,
        char_end=char_end,
        section=title,
    )


def _visible_cell_text(cell_html: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", cell_html))
    return re.sub(r"\s+", " ", text).strip()


def _report_value_cell_index(table: str, display_date: str) -> int:
    matches: list[int] = []
    for row in _ROW.finditer(table):
        headers = list(_HEADER_CELL.finditer(row.group()))
        has_row_label = bool(headers and 'class="tl"' in headers[0].group())
        matches.extend(
            index if has_row_label else index + 1
            for index, header in enumerate(headers)
            if _visible_cell_text(header.group(1)) == display_date
        )
    if len(set(matches)) != 1:
        raise ProvenanceError("issuer IR rendered XBRL report column is ambiguous")
    return matches[0]


def _context_metric_fact(
    source_text: str,
    *,
    role: str,
    title: str,
    concept: str,
    context_label: str,
    table_start: int,
    table_end: int,
    value_cell_index: int,
) -> IssuerIrMetricFact:
    table = source_text[table_start:table_end]
    rows = list(_ROW.finditer(table))
    context_index = None
    for index, row in enumerate(rows):
        cells = list(_CELL.finditer(row.group()))
        if cells and _visible_cell_text(cells[0].group(1)) == context_label:
            if context_index is not None:
                raise ProvenanceError(
                    f"issuer IR rendered XBRL {role} context is ambiguous"
                )
            context_index = index
    if context_index is None:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} context is missing")
    exact_concept = f"'{concept}'"
    for row in rows[context_index + 1 :]:
        cells = list(_CELL.finditer(row.group()))
        if not cells:
            continue
        if "defref_srt_ProductOrServiceAxis" in row.group() or (
            "defref_us-gaap_StatementBusinessSegmentsAxis" in row.group()
        ):
            break
        if exact_concept not in row.group() or exact_concept not in cells[0].group():
            continue
        if len(cells) <= value_cell_index:
            break
        value_cell = cells[value_cell_index]
        number_matches = list(_NUMBER.finditer(value_cell.group(1)))
        if len(number_matches) != 1:
            break
        number = number_matches[0]
        evidence_quote = number.group()
        negative = evidence_quote.startswith("(") and evidence_quote.endswith(")")
        digits = re.sub(r"[^0-9]", "", evidence_quote)
        char_start = table_start + row.start() + value_cell.start(1) + number.start()
        char_end = char_start + len(evidence_quote)
        if not digits or source_text[char_start:char_end] != evidence_quote:
            break
        return IssuerIrMetricFact(
            role=role,
            numeric_value=-int(digits) if negative else int(digits),
            evidence_quote=evidence_quote,
            char_start=char_start,
            char_end=char_end,
            section=title,
        )
    raise ProvenanceError(f"issuer IR rendered XBRL {role} row is missing")


def _policy_fact(
    source_text: str,
    *,
    role: str,
    evidence_quote: str,
    section: str,
    table_start: int,
    table_end: int,
) -> IssuerIrMetricFact:
    starts = [
        match.start()
        for match in re.finditer(re.escape(evidence_quote), source_text, re.IGNORECASE)
        if table_start <= match.start() and match.end() <= table_end
    ]
    if len(starts) != 1:
        raise ProvenanceError(f"issuer IR rendered XBRL {role} text is ambiguous")
    char_start = starts[0]
    return IssuerIrMetricFact(
        role=role,
        numeric_value=1,
        evidence_quote=source_text[char_start : char_start + len(evidence_quote)],
        char_start=char_start,
        char_end=char_start + len(evidence_quote),
        section=section,
        kind=(
            "policy_presence"
            if section == ISSUER_IR_POLICY_SECTION
            else "disclosure_presence"
        ),
    )


ALPHABET_ASSET_BREAKDOWN_PROFILE = "alphabet.asset-revenue-breakdown.v1"
ALPHABET_BREAKDOWN_SECTIONS = (
    "Revenues - Revenue by Segment (Details)",
    "Revenues - Revenue by Geographic Location (Details)",
    "Revenues (Revenue by Segment) (Details)",
    "Revenues (Revenue by Geographic Location) (Details)",
)


def _alphabet_annual_value_index(table: str, display_date: str) -> int:
    headers = [
        _visible_cell_text(cell[1])
        for row in list(_ROW.finditer(table))[:2]
        for cell in _HEADER_CELL.finditer(row.group())
    ]
    durations = [value for value in headers if "Months Ended" in value]
    if durations != ["12 Months Ended"]:
        raise ProvenanceError("Alphabet financial duration is not annual")
    return _report_value_cell_index(table, display_date)


def _alphabet_breakdown_facts(
    source_text: str, report_date: str, display_date: str
) -> tuple[tuple[IssuerIrMetricFact, ...], tuple[tuple[str, int, int], ...]]:
    """Read disjoint segment/geography operands and the stated hedge adjustment."""
    titles = (
        ALPHABET_BREAKDOWN_SECTIONS[:2]
        if report_date >= "2022-01-01"
        else ALPHABET_BREAKDOWN_SECTIONS[2:]
    )
    groups = (
        (
            "category",
            (
                (
                    "google_services",
                    "Google Services",
                    "us-gaap_StatementBusinessSegmentsAxis=goog_GoogleServicesMember",
                ),
                (
                    "google_cloud",
                    "Google Cloud",
                    "us-gaap_StatementBusinessSegmentsAxis=goog_GoogleCloudMember",
                ),
                (
                    "other_bets",
                    "Other Bets",
                    "us-gaap_StatementBusinessSegmentsAxis=us-gaap_AllOtherSegmentsMember",
                ),
            ),
        ),
        (
            "geo",
            (
                (
                    "united_states",
                    "United States",
                    "srt_StatementGeographicalAxis=country_US",
                ),
                ("emea", "EMEA", "srt_StatementGeographicalAxis=us-gaap_EMEAMember"),
                ("apac", "APAC", "srt_StatementGeographicalAxis=srt_AsiaPacificMember"),
                (
                    "other_americas",
                    "Other Americas",
                    "srt_StatementGeographicalAxis=goog_AmericasExcludingUnitedStatesMember",
                ),
            ),
        ),
    )
    facts: list[IssuerIrMetricFact] = []
    ranges: list[tuple[str, int, int]] = []
    revenue_concept = (
        "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    for title, (prefix, members) in zip(titles, groups, strict=True):
        start, end = _statement_table(source_text, title)
        ranges.append((title, start, end))
        table = source_text[start:end]
        heading = re.search(
            r"<strong\b[^>]*>(.*?)</strong>", table, re.IGNORECASE | re.DOTALL
        )
        if heading is None or not _visible_cell_text(heading[1]).endswith(
            "- USD ($) $ in Millions"
        ):
            raise ProvenanceError("Alphabet breakdown units are not USD millions")
        value_index = _alphabet_annual_value_index(table, display_date)
        rows = list(_ROW.finditer(table))
        for role, label, axis_member in members:
            matches = [
                i
                for i, row in enumerate(rows)
                if 'class="rh"' in row.group()
                and (cells := list(_CELL.finditer(row.group())))
                and _visible_cell_text(cells[0][1]) == label
            ]
            if len(matches) != 1:
                raise ProvenanceError(
                    "Alphabet breakdown member is missing or ambiguous"
                )
            index = matches[0]
            dimensions = re.findall(r"'defref_([^']*Axis=[^']+)'", rows[index].group())
            if dimensions != [axis_member]:
                raise ProvenanceError("Alphabet breakdown dimension identity mismatch")
            next_index = next(
                (
                    j
                    for j in range(index + 1, len(rows))
                    if 'class="rh"' in rows[j].group()
                ),
                len(rows),
            )
            operands = [
                row
                for row in rows[index + 1 : next_index]
                if f"'{revenue_concept}'" in row.group()
            ]
            if len(operands) != 1:
                raise ProvenanceError(
                    "Alphabet breakdown operand is missing or ambiguous"
                )
            row = operands[0]
            facts.append(
                _metric_fact(
                    source_text,
                    role=f"{prefix}_{role}",
                    title=title,
                    concept=revenue_concept,
                    table_start=start + row.start(),
                    table_end=start + row.end(),
                    occurrence=0,
                    value_cell_index=value_index,
                    display_date=display_date,
                )
            )
        hedge_concept = "defref_us-gaap_GainLossOnOilAndGasHedgingActivity"
        hedge_rows = [row for row in rows if f"'{hedge_concept}'" in row.group()]
        if len(hedge_rows) != 1 or "Hedging gains (losses)" not in _visible_cell_text(
            hedge_rows[0].group()
        ):
            raise ProvenanceError("Alphabet hedge adjustment is missing or ambiguous")
        facts.append(
            _metric_fact(
                source_text,
                role=f"{prefix}_hedging",
                title=title,
                concept=hedge_concept,
                table_start=start,
                table_end=end,
                occurrence=0,
                value_cell_index=value_index,
                display_date=display_date,
            )
        )
    return tuple(facts), tuple(ranges)


def _parse_alphabet_asset_metrics(
    source_text: str, *, report_date: str, metric_profile: str | None = None
) -> IssuerIrRenderedProgram:
    """Bind the four existing asset-trajectory operands, in USD millions."""
    display_date = _display_date(report_date)
    cover_start, cover_end = _statement_table(source_text, "Cover Page")
    cover = source_text[cover_start:cover_end]
    for concept, expected in (
        ("EntityCentralIndexKey", "0001652044"),
        ("DocumentType", "10-K"),
        ("DocumentPeriodEndDate", display_date),
    ):
        rows = [
            row
            for row in _ROW.finditer(cover)
            if f"'defref_dei_{concept}'" in row.group()
        ]
        if len(rows) != 1:
            raise ProvenanceError("Alphabet filing identity is missing or ambiguous")
        cells = list(_CELL.finditer(rows[0].group()))
        if len(cells) < 2 or _visible_cell_text(cells[1].group(1)) != expected:
            raise ProvenanceError("Alphabet filing identity mismatch")
    metrics = (
        (
            "revenue",
            "CONSOLIDATED STATEMENTS OF INCOME",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ),
        ("assets", "CONSOLIDATED BALANCE SHEETS", "Assets"),
        (
            "liabilities_and_equity",
            "CONSOLIDATED BALANCE SHEETS",
            "LiabilitiesAndStockholdersEquity",
        ),
        (
            "cash_from_operations",
            "CONSOLIDATED STATEMENTS OF CASH FLOWS",
            "NetCashProvidedByUsedInOperatingActivities",
        ),
    )
    ranges = {title: _statement_table(source_text, title) for _, title, _ in metrics}
    facts = []
    for role, title, concept in metrics:
        start, end = ranges[title]
        table = source_text[start:end]
        if role == "revenue" and report_date < "2022-01-01":
            concept = "Revenues"
        heading = re.search(
            r"<strong\b[^>]*>(.*?)</strong>", table, re.IGNORECASE | re.DOTALL
        )
        if heading is None or not _visible_cell_text(heading[1]).endswith(
            "- USD ($) $ in Millions"
        ):
            raise ProvenanceError("Alphabet financial units are not USD millions")
        exact_concept = f"'defref_us-gaap_{concept}'"
        if sum(exact_concept in row.group() for row in _ROW.finditer(table)) != 1:
            raise ProvenanceError("Alphabet financial operand is missing or ambiguous")
        facts.append(
            _metric_fact(
                source_text,
                role=role,
                title=title,
                concept="defref_us-gaap_" + concept,
                table_start=start,
                table_end=end,
                occurrence=0,
                value_cell_index=(
                    _alphabet_annual_value_index(table, display_date)
                    if role in {"revenue", "cash_from_operations"}
                    else _report_value_cell_index(table, display_date)
                ),
                display_date=display_date,
            )
        )
    values = {fact.role: fact.numeric_value for fact in facts}
    if values["assets"] != values["liabilities_and_equity"]:
        raise ProvenanceError("Alphabet balance-sheet identity fails")
    if metric_profile == ALPHABET_ASSET_BREAKDOWN_PROFILE:
        extra_facts, extra_ranges = _alphabet_breakdown_facts(
            source_text, report_date, display_date
        )
        for prefix in ("category_", "geo_"):
            if (
                sum(f.numeric_value for f in extra_facts if f.role.startswith(prefix))
                != values["revenue"]
            ):
                raise ProvenanceError("Alphabet revenue breakdown identity fails")
        facts.extend(extra_facts)
        ranges.update({title: (start, end) for title, start, end in extra_ranges})
    start, end = ranges[metrics[0][1]]
    dates = list(re.finditer(re.escape(display_date), source_text[start:end]))
    if len(dates) != 1:
        raise ProvenanceError("Alphabet current-year column is ambiguous")
    date_start = start + dates[0].start()
    return IssuerIrRenderedProgram(
        report_date=report_date,
        report_date_display=display_date,
        report_date_char_start=date_start,
        report_date_char_end=date_start + len(display_date),
        facts=tuple(facts),
        section_ranges=tuple((title, *bounds) for title, bounds in ranges.items()),
        source_sha256=hashlib.sha256(source_text.encode()).hexdigest(),
    )


def parse_issuer_ir_rendered_metrics(
    source_text: str,
    *,
    report_date: str,
    issuer_cik: str | None = None,
    metric_profile: str | None = None,
) -> IssuerIrRenderedProgram:
    """Parse current-year metrics from exact issuer-rendered statement rows."""
    if not isinstance(source_text, str) or not source_text:
        raise ProvenanceError("issuer IR rendered XBRL text is missing")
    if metric_profile is not None and (
        issuer_cik != "0001652044" or metric_profile != ALPHABET_ASSET_BREAKDOWN_PROFILE
    ):
        raise ProvenanceError("unsupported issuer financial metric profile")
    if issuer_cik == "0001652044":
        return _parse_alphabet_asset_metrics(
            source_text, report_date=report_date, metric_profile=metric_profile
        )
    try:
        display_date = _display_date(report_date)
    except ValueError as exc:
        raise ProvenanceError("issuer IR report date is invalid") from exc
    section_ranges: dict[str, tuple[int, int]] = {}
    for _, title, _, _ in (*_METRICS, *_CONTEXT_METRICS):
        section_ranges.setdefault(title, _statement_table(source_text, title))
    for _, title, _ in _DISCLOSURE_SECTIONS:
        section_ranges.setdefault(title, _statement_table(source_text, title))
    section_ranges[ISSUER_IR_POLICY_SECTION] = _statement_table(
        source_text, ISSUER_IR_POLICY_SECTION
    )
    numeric_titles = {title for _, title, *_ in (*_METRICS, *_CONTEXT_METRICS)}
    value_columns = {
        title: (
            1
            if title == ISSUER_IR_EQUITY_SECTION
            else _report_value_cell_index(source_text[start:end], display_date)
        )
        for title, (start, end) in section_ranges.items()
        if title in numeric_titles
    }
    facts = tuple(
        _metric_fact(
            source_text,
            role=role,
            title=title,
            concept=concept,
            table_start=section_ranges[title][0],
            table_end=section_ranges[title][1],
            occurrence=occurrence,
            value_cell_index=value_columns[title],
            display_date=display_date,
        )
        for role, title, concept, occurrence in _METRICS
    )
    facts += tuple(
        _context_metric_fact(
            source_text,
            role=role,
            title=title,
            concept=concept,
            context_label=context_label,
            table_start=section_ranges[title][0],
            table_end=section_ranges[title][1],
            value_cell_index=value_columns[title],
        )
        for role, title, concept, context_label in _CONTEXT_METRICS
    )
    facts += tuple(
        _policy_fact(
            source_text,
            role=role,
            evidence_quote=evidence_quote,
            section=ISSUER_IR_POLICY_SECTION,
            table_start=section_ranges[ISSUER_IR_POLICY_SECTION][0],
            table_end=section_ranges[ISSUER_IR_POLICY_SECTION][1],
        )
        for role, evidence_quote in _POLICY_FACTS
    )
    facts += tuple(
        _policy_fact(
            source_text,
            role=role,
            evidence_quote=evidence_quote,
            section=section,
            table_start=section_ranges[section][0],
            table_end=section_ranges[section][1],
        )
        for role, section, evidence_quote in _DISCLOSURE_SECTIONS
    )
    operation_start, operation_end = section_ranges[
        "Consolidated Statements of Operations"
    ]
    date_matches = [
        match
        for match in re.finditer(re.escape(display_date), source_text)
        if operation_start <= match.start() < operation_end
    ]
    if len(date_matches) != 1:
        raise ProvenanceError("issuer IR rendered XBRL report date is ambiguous")
    date_match = date_matches[0]
    return IssuerIrRenderedProgram(
        report_date=report_date,
        report_date_display=display_date,
        report_date_char_start=date_match.start(),
        report_date_char_end=date_match.end(),
        facts=facts,
        section_ranges=tuple(
            (title, *section_ranges[title]) for title in sorted(section_ranges)
        ),
        source_sha256=hashlib.sha256(source_text.encode()).hexdigest(),
    )


def _artifact(filing: dict[str, Any], role: str) -> dict[str, Any]:
    artifacts = filing.get("artifacts")
    if not isinstance(artifacts, list):
        raise ProvenanceError("issuer IR inventory artifacts are invalid")
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("role") == role
    ]
    if len(matches) != 1:
        raise ProvenanceError(f"issuer IR inventory requires one {role} artifact")
    return matches[0]


def _regular_source(base_directory: Path, filename: object) -> tuple[Path, bytes]:
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ProvenanceError("issuer IR source file is unsafe")
    path = base_directory / filename
    try:
        return path, _read_regular_file(path, MAX_SOURCE_BYTES)
    except OSError as exc:
        raise ProvenanceError(f"cannot read issuer IR source: {exc}") from exc


def _authorization(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProvenanceError("issuer IR authorization receipt is invalid")
    required = ("record_id", "scope", "basis", "reviewed_at")
    receipt = {field: str(value.get(field) or "").strip() for field in required}
    if any(not receipt[field] for field in required):
        raise ProvenanceError("issuer IR authorization receipt is invalid")
    _parse_timestamp(receipt["reviewed_at"], "authorization.reviewed_at")
    return receipt


def _strict_https_url(value: object, *, host: str, field: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProvenanceError(f"issuer IR {field} URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise ProvenanceError(f"issuer IR {field} URL is invalid")
    return raw


def _xbrl_identity(raw: bytes) -> dict[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if any(
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or name.endswith("/")
                for name in names
            ):
                raise ProvenanceError("issuer IR XBRL archive has unsafe members")
            instances = [name for name in names if name.lower().endswith("_htm.xml")]
            if len(instances) != 1:
                raise ProvenanceError(
                    "issuer IR XBRL archive must contain one filing instance"
                )
            info = archive.getinfo(instances[0])
            if info.file_size <= 0 or info.file_size > MAX_SOURCE_BYTES:
                raise ProvenanceError("issuer IR XBRL instance exceeds size limit")
            instance = archive.read(instances[0])
    except (OSError, zipfile.BadZipFile) as exc:
        raise ProvenanceError("issuer IR XBRL artifact is not a valid ZIP") from exc
    try:
        root = ET.fromstring(instance)
    except ET.ParseError as exc:
        raise ProvenanceError("issuer IR XBRL instance is invalid XML") from exc
    wanted = {
        "EntityRegistrantName": "registrant_name",
        "EntityCentralIndexKey": "cik",
        "DocumentType": "form",
        "DocumentPeriodEndDate": "report_date",
    }
    values: dict[str, list[str]] = {field: [] for field in wanted.values()}
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in wanted and element.text and element.text.strip():
            values[wanted[local]].append(element.text.strip())
    if any(len(set(items)) != 1 for items in values.values()):
        raise ProvenanceError("issuer IR XBRL identity is missing or ambiguous")
    return {field: items[0] for field, items in values.items()}


def _detail_value(text: str, identity: str) -> str:
    match = re.search(
        rf"<span\b[^>]*\bid=[\"']{re.escape(identity)}[\"'][^>]*>(.*?)</span>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise ProvenanceError("issuer IR detail-page identity is missing")
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(match.group(1))).split())


def _audit_acquisition_inventory(
    inventory: dict[str, Any], base_directory: Path
) -> tuple[dict[str, str], list[dict[str, Any]], str]:
    if (
        inventory.get("schema_version") != "longworld.issuer-ir-inventory.v1"
        or inventory.get("source_status") != "issuer_owned_ir_download"
        or inventory.get("data_stage") != "source_inventory"
        or inventory.get("hybrid_train_ready") is not False
        or inventory.get("generation_integration") != "disabled"
        or inventory.get("production_eligible") is not False
    ):
        raise ProvenanceError("issuer IR acquisition inventory is invalid")
    _authorization(inventory.get("authorization"))
    issuer = inventory.get("issuer")
    if not isinstance(issuer, dict):
        raise ProvenanceError("issuer IR inventory issuer is invalid")
    normalized = {
        "name": str(issuer.get("name") or "").strip(),
        "cik": str(issuer.get("cik") or ""),
        "detail_host": str(issuer.get("detail_host") or "").strip().lower(),
        "artifact_host": str(issuer.get("artifact_host") or "").strip().lower(),
    }
    if (
        not normalized["name"]
        or _CIK.fullmatch(normalized["cik"]) is None
        or _HOST.fullmatch(normalized["detail_host"]) is None
        or _HOST.fullmatch(normalized["artifact_host"]) is None
        or normalized["detail_host"] == normalized["artifact_host"]
    ):
        raise ProvenanceError("issuer IR inventory issuer is invalid")
    filings = inventory.get("filings")
    if (
        not isinstance(filings, list)
        or len(filings) < 2
        or inventory.get("n") != len(filings)
    ):
        raise ProvenanceError("issuer IR inventory filing count is invalid")
    for filing in filings:
        if not isinstance(filing, dict):
            raise ProvenanceError("issuer IR inventory filing is invalid")
        detail = filing.get("detail_page")
        if not isinstance(detail, dict):
            raise ProvenanceError("issuer IR detail-page receipt is invalid")
        detail_url = _strict_https_url(
            detail.get("source_url"),
            host=normalized["detail_host"],
            field="detail-page",
        )
        _, detail_raw = _regular_source(base_directory, detail.get("source_file"))
        if (
            hashlib.sha256(detail_raw).hexdigest() != detail.get("sha256")
            or len(detail_raw) != detail.get("bytes")
            or _JWT_SHAPED.search(detail_raw)
        ):
            raise ProvenanceError("issuer IR detail-page receipt is invalid")
        try:
            detail_text = detail_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProvenanceError("issuer IR detail page is not UTF-8") from exc
        _reject_secrets(detail_text)
        if _EMAIL.search(detail_text):
            raise ProvenanceError("issuer IR detail page contains email PII")
        form = str(filing.get("form") or "")
        filing_date = str(filing.get("filing_date") or "")
        alphabet = normalized["cik"] == "0001652044"
        control_prefix = "_ctrl0_ctl33_" if alphabet else "_ctrl0_ctl54_"
        if alphabet and normalized["detail_host"] != "abc.xyz":
            raise ProvenanceError("Alphabet detail host mismatch")
        if _detail_value(detail_text, control_prefix + "lblForm") != form:
            raise ProvenanceError("issuer IR detail-page form mismatch")
        observed_date = _detail_value(detail_text, control_prefix + "lblDate")
        try:
            parsed = time.strptime(
                observed_date, "%m/%d/%Y" if alphabet else "%b %d, %Y"
            )
            parsed_date = date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
        except ValueError as exc:
            raise ProvenanceError("issuer IR detail-page date is invalid") from exc
        if parsed_date.isoformat() != filing_date or not detail_url:
            raise ProvenanceError("issuer IR detail-page date mismatch")

        artifacts = filing.get("artifacts")
        if not isinstance(artifacts, list) or Counter(
            str(item.get("role") or "") for item in artifacts if isinstance(item, dict)
        ) != Counter(
            {
                "annual_report_pdf": 1,
                "xbrl_zip": 1,
                "rendered_xbrl_html": 1,
            }
        ):
            raise ProvenanceError("issuer IR inventory artifacts are invalid")
        actual_identity: dict[str, str] | None = None
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ProvenanceError("issuer IR inventory artifact is invalid")
            source_url = _strict_https_url(
                artifact.get("source_url"),
                host=normalized["artifact_host"],
                field="artifact",
            )
            parsed_url = urlparse(source_url)
            if not parsed_url.path.startswith(f"/CIK-{normalized['cik']}/"):
                raise ProvenanceError("issuer IR artifact CIK path is invalid")
            if source_url not in html.unescape(detail_text):
                raise ProvenanceError("issuer IR artifact is not bound by detail page")
            _, artifact_raw = _regular_source(
                base_directory, artifact.get("source_file")
            )
            if hashlib.sha256(artifact_raw).hexdigest() != artifact.get(
                "sha256"
            ) or len(artifact_raw) != artifact.get("bytes"):
                raise ProvenanceError("issuer IR artifact receipt is invalid")
            role = artifact["role"]
            if role == "annual_report_pdf" and not artifact_raw.startswith(b"%PDF-"):
                raise ProvenanceError("issuer IR annual report is not a PDF")
            if (
                role == "rendered_xbrl_html"
                and b"<html" not in artifact_raw[:16000].lower()
            ):
                raise ProvenanceError("issuer IR rendered XBRL is not HTML")
            if role == "xbrl_zip":
                actual_identity = _xbrl_identity(artifact_raw)
        expected_identity = {
            "registrant_name": actual_identity.get("registrant_name", "")
            if actual_identity is not None
            else "",
            "cik": normalized["cik"],
            "form": form,
            "report_date": str(filing.get("report_date") or ""),
        }
        if (
            actual_identity != expected_identity
            or filing.get("xbrl_identity") != expected_identity
            or re.sub(r"[^a-z0-9]", "", expected_identity["registrant_name"].casefold())
            != re.sub(r"[^a-z0-9]", "", normalized["name"].casefold())
        ):
            raise ProvenanceError("issuer IR XBRL identity mismatch")
    digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return normalized, filings, digest


def build_issuer_ir_filing_manifest(
    inventory: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Bind a verified acquisition inventory to exact rendered-XBRL records."""
    _parse_timestamp(generated_at, "generated_at")
    issuer, raw_filings, acquisition_inventory_sha256 = _audit_acquisition_inventory(
        inventory, base_directory
    )
    cik = issuer["cik"]
    records: list[dict[str, Any]] = []
    filing_ids: dict[str, str] = {}
    programs: dict[str, IssuerIrRenderedProgram] = {}
    for filing in raw_filings:
        if not isinstance(filing, dict):
            raise ProvenanceError("issuer IR inventory filing is invalid")
        filing_id = str(filing.get("filing_id") or "")
        report_date = str(filing.get("report_date") or "")
        filing_date = str(filing.get("filing_date") or "")
        form = str(filing.get("form") or "")
        xbrl_identity = filing.get("xbrl_identity")
        if (
            not isinstance(xbrl_identity, dict)
            or xbrl_identity.get("cik") != cik
            or xbrl_identity.get("form") != form
            or xbrl_identity.get("report_date") != report_date
            or re.sub(
                r"[^a-z0-9]",
                "",
                str(xbrl_identity.get("registrant_name") or "").casefold(),
            )
            != re.sub(r"[^a-z0-9]", "", issuer["name"].casefold())
        ):
            raise ProvenanceError("issuer IR XBRL identity mismatch")
        artifact = _artifact(filing, "rendered_xbrl_html")
        source_file = artifact.get("source_file")
        _, raw = _regular_source(base_directory, source_file)
        source_sha256 = hashlib.sha256(raw).hexdigest()
        if source_sha256 != artifact.get("sha256"):
            raise ProvenanceError("issuer IR rendered XBRL source hash mismatch")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProvenanceError("issuer IR rendered XBRL is not UTF-8") from exc
        _reject_secrets(text)
        if _EMAIL.search(text):
            raise ProvenanceError("issuer IR rendered XBRL contains email PII")
        program = parse_issuer_ir_rendered_metrics(
            text,
            report_date=report_date,
            issuer_cik=cik,
            metric_profile=inventory.get("financial_metric_profile"),
        )
        record_id = f"issuer-ir:{cik}:{report_date}"
        if filing_id in filing_ids or record_id in filing_ids.values():
            raise ProvenanceError("issuer IR filing identity is duplicated")
        facts = [
            {
                "fact_id": "report_date_display",
                "field": "report_date_display",
                "value": program.report_date_display,
                "evidence_quote": program.report_date_display,
                "evidence_char_start": program.report_date_char_start,
                "source_sha256": source_sha256,
            }
        ]
        facts.extend(
            {
                "fact_id": fact.role,
                "field": fact.role,
                "value": fact.evidence_quote,
                "numeric_value": fact.numeric_value,
                "evidence_quote": fact.evidence_quote,
                "evidence_char_start": fact.char_start,
                "source_sha256": source_sha256,
            }
            for fact in program.facts
        )
        detail = filing.get("detail_page")
        assert isinstance(detail, dict)
        source_url = str(artifact.get("source_url") or "")
        retrieval_url = str(detail.get("source_url") or "")
        acquisition_receipt = {
            "detail_page_sha256": detail["sha256"],
            **{
                f"{item['role']}_sha256": item["sha256"] for item in filing["artifacts"]
            },
        }
        records.append(
            {
                "record_id": record_id,
                "filing_id": filing_id,
                "issuer_name": str(issuer.get("name") or ""),
                "cik": cik,
                "form": form,
                "filing_date": filing_date,
                "report_date": report_date,
                "source_url": source_url,
                "retrieval_url": retrieval_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "provenance_id": f"sha256:{source_sha256}",
                "text": text,
                "derived_facts": facts,
                "acquisition_receipt": acquisition_receipt,
            }
        )
        filing_ids[filing_id] = record_id
        programs[record_id] = program

    raw_relations = inventory.get("filing_relations")
    if not isinstance(raw_relations, list) or len(raw_relations) != len(records) - 1:
        raise ProvenanceError("issuer IR temporal relations are invalid")
    records_by_id = {record["record_id"]: record for record in records}
    relations: list[dict[str, Any]] = []
    for relation in raw_relations:
        if not isinstance(relation, dict):
            raise ProvenanceError("issuer IR temporal relation is invalid")
        source_record_id = filing_ids.get(str(relation.get("from_filing_id") or ""))
        target_record_id = filing_ids.get(str(relation.get("to_filing_id") or ""))
        if (
            relation.get("relation_type") != "prior_available_annual_filing"
            or relation.get("relation_provenance") != "derived_temporal_same_issuer"
            or source_record_id is None
            or target_record_id is None
            or source_record_id == target_record_id
        ):
            raise ProvenanceError("issuer IR temporal relation is invalid")
        source_record = records_by_id[source_record_id]
        program = programs[source_record_id]
        relations.append(
            {
                "relation_id": f"{source_record_id}:prior:{target_record_id}",
                "kind": "prior_available_annual_filing",
                "source_record_id": source_record_id,
                "target_record_id": target_record_id,
                "evidence_record_id": source_record_id,
                "evidence_quote": program.report_date_display,
                "evidence_char_start": program.report_date_char_start,
                "source_sha256": source_record["source_sha256"],
            }
        )
    manifest = {
        "schema_version": ISSUER_IR_FILING_MANIFEST_SCHEMA,
        "source_status": "issuer_owned_ir_download",
        "source_family": ISSUER_IR_SOURCE_FAMILY,
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "generated_at": generated_at,
        "authorization": _authorization(inventory.get("authorization")),
        "issuer": issuer,
        "acquisition_inventory_sha256": acquisition_inventory_sha256,
        "n": len(records),
        "records": sorted(records, key=lambda record: record["report_date"]),
        "relations": sorted(relations, key=lambda relation: relation["relation_id"]),
    }
    if inventory.get("financial_metric_profile") is not None:
        manifest["financial_metric_profile"] = inventory["financial_metric_profile"]
    _audit_issuer_ir_filing_manifest(manifest)
    return manifest


def _audit_issuer_ir_filing_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != ISSUER_IR_FILING_MANIFEST_SCHEMA:
        raise ProvenanceError("unsupported issuer IR filing manifest schema")
    if (
        payload.get("source_status") != "issuer_owned_ir_download"
        or payload.get("source_family") != ISSUER_IR_SOURCE_FAMILY
        or payload.get("data_stage") != "source_inventory"
        or payload.get("hybrid_train_ready") is not False
        or payload.get("production_eligible") is not False
        or payload.get("generation_integration") != "disabled"
    ):
        raise ProvenanceError("issuer IR filing manifest status is invalid")
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    _authorization(payload.get("authorization"))
    issuer = payload.get("issuer")
    acquisition_inventory_sha256 = str(
        payload.get("acquisition_inventory_sha256") or ""
    )
    if (
        not isinstance(issuer, dict)
        or _SHA256.fullmatch(acquisition_inventory_sha256) is None
    ):
        raise ProvenanceError("issuer IR acquisition binding is invalid")
    issuer_name = str(issuer.get("name") or "").strip()
    issuer_cik = str(issuer.get("cik") or "")
    detail_host = str(issuer.get("detail_host") or "").strip().lower()
    artifact_host = str(issuer.get("artifact_host") or "").strip().lower()
    if (
        not issuer_name
        or _CIK.fullmatch(issuer_cik) is None
        or _HOST.fullmatch(detail_host) is None
        or _HOST.fullmatch(artifact_host) is None
        or detail_host == artifact_host
    ):
        raise ProvenanceError("issuer IR manifest issuer is invalid")
    records = payload.get("records")
    relations = payload.get("relations")
    if (
        not isinstance(records, list)
        or len(records) < 2
        or payload.get("n") != len(records)
        or not isinstance(relations, list)
        or len(relations) != len(records) - 1
    ):
        raise ProvenanceError("issuer IR filing manifest graph is invalid")
    ids: set[str] = set()
    records_by_id: dict[str, dict[str, Any]] = {}
    programs_by_record: dict[str, IssuerIrRenderedProgram] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ProvenanceError("issuer IR filing record is invalid")
        record_id = str(record.get("record_id") or "")
        source_sha256 = str(record.get("source_sha256") or "")
        text = record.get("text")
        acquisition_receipt = record.get("acquisition_receipt")
        expected_receipt_fields = {
            "detail_page_sha256",
            "annual_report_pdf_sha256",
            "xbrl_zip_sha256",
            "rendered_xbrl_html_sha256",
        }
        if (
            not record_id
            or record_id in ids
            or _SHA256.fullmatch(source_sha256) is None
            or record.get("provenance_id") != f"sha256:{source_sha256}"
            or not isinstance(text, str)
            or hashlib.sha256(text.encode()).hexdigest() != record.get("text_sha256")
            or record.get("text_sha256") != source_sha256
            or record.get("issuer_name") != issuer_name
            or record.get("cik") != issuer_cik
            or not isinstance(acquisition_receipt, dict)
            or set(acquisition_receipt) != expected_receipt_fields
            or any(
                _SHA256.fullmatch(str(acquisition_receipt.get(field) or "")) is None
                for field in expected_receipt_fields
            )
            or acquisition_receipt.get("rendered_xbrl_html_sha256") != source_sha256
        ):
            raise ProvenanceError("issuer IR filing record provenance is invalid")
        _strict_https_url(
            record.get("source_url"), host=artifact_host, field="record source"
        )
        _strict_https_url(
            record.get("retrieval_url"), host=detail_host, field="record retrieval"
        )
        program = parse_issuer_ir_rendered_metrics(
            text,
            report_date=str(record.get("report_date") or ""),
            issuer_cik=issuer_cik,
            metric_profile=payload.get("financial_metric_profile"),
        )
        expected_facts = {
            "report_date_display": {
                "fact_id": "report_date_display",
                "field": "report_date_display",
                "value": program.report_date_display,
                "evidence_quote": program.report_date_display,
                "evidence_char_start": program.report_date_char_start,
                "source_sha256": source_sha256,
            },
            **{
                fact.role: {
                    "fact_id": fact.role,
                    "field": fact.role,
                    "value": fact.evidence_quote,
                    "numeric_value": fact.numeric_value,
                    "evidence_quote": fact.evidence_quote,
                    "evidence_char_start": fact.char_start,
                    "source_sha256": source_sha256,
                }
                for fact in program.facts
            },
        }
        facts = record.get("derived_facts")
        if (
            not isinstance(facts, list)
            or not all(isinstance(fact, dict) for fact in facts)
            or len(facts) != len(expected_facts)
        ):
            raise ProvenanceError("issuer IR filing facts are invalid")
        facts_by_id = {str(fact.get("fact_id") or ""): fact for fact in facts}
        if len(facts_by_id) != len(facts) or facts_by_id != expected_facts:
            raise ProvenanceError("issuer IR filing facts are invalid")
        ids.add(record_id)
        records_by_id[record_id] = record
        programs_by_record[record_id] = program
    ordered_records = sorted(
        records_by_id.values(), key=lambda item: str(item["report_date"])
    )
    expected_relations = {}
    for target, source in pairwise(ordered_records):
        source_id = str(source["record_id"])
        target_id = str(target["record_id"])
        program = programs_by_record[source_id]
        relation_id = f"{source_id}:prior:{target_id}"
        expected_relations[relation_id] = {
            "relation_id": relation_id,
            "kind": "prior_available_annual_filing",
            "source_record_id": source_id,
            "target_record_id": target_id,
            "evidence_record_id": source_id,
            "evidence_quote": program.report_date_display,
            "evidence_char_start": program.report_date_char_start,
            "source_sha256": source["source_sha256"],
        }
    if not all(isinstance(relation, dict) for relation in relations):
        raise ProvenanceError("issuer IR filing relation is invalid")
    relations_by_id = {
        str(relation.get("relation_id") or ""): relation for relation in relations
    }
    if len(relations_by_id) != len(relations) or relations_by_id != expected_relations:
        raise ProvenanceError("issuer IR filing relation is invalid")


def selected_issuer_ir_source_relation_edges(
    world: Any, spec: Any, artifacts: Sequence[Any]
) -> list[dict[str, str]]:
    """Return signed issuer-history edges whose real endpoints are in this view."""
    from longworld.domains.company.simulate import (
        canonical_issuer_ir_source_section_envelopes,
    )

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
    workflow_matches: dict[str, list[tuple[int, Any]]] = {}
    for workflow_index, workflow in enumerate(project.get("source_workflows") or ()):
        if getattr(workflow, "source_kind", "") == ISSUER_IR_SOURCE_KIND:
            workflow_matches.setdefault(
                str(getattr(workflow, "workflow_id", "")), []
            ).append((workflow_index, workflow))
    workflows = {
        workflow_id: matches[0]
        for workflow_id, matches in workflow_matches.items()
        if workflow_id and len(matches) == 1
    }
    canonical_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    selected_records: set[tuple[str, str]] = set()
    relation_artifacts: set[str] = set()
    world_id = str(getattr(world, "spec", {}).get("world_id") or "")
    for artifact in artifacts:
        slots = getattr(artifact, "slots", None) or {}
        workflow_id = str(slots.get("source_workflow_id") or "")
        record_id = str(slots.get("source_record_id") or "")
        classification = slots.get("classification")
        revealed = [
            event_index[event_id]
            for event_id in getattr(artifact, "reveals_events", ())
            if event_id in event_index
            and event_index[event_id].type == "issuer_ir_source_section"
        ]
        if len(revealed) == 1 and workflow_id in workflows:
            event = revealed[0]
            workflow_index, workflow = workflows[workflow_id]
            record_matches = [
                (record_index, record)
                for record_index, record in enumerate(getattr(workflow, "records", ()))
                if getattr(record, "record_id", "") == record_id
            ]
            section_name = str(event.params.get("section_name") or "")
            if len(record_matches) == 1 and section_name in ISSUER_IR_SECTIONS_64K:
                record_index, record = record_matches[0]
                cache_key = (workflow_id, record_id)
                if cache_key not in canonical_cache:
                    canonical_cache[cache_key] = (
                        canonical_issuer_ir_source_section_envelopes(
                            workflow=workflow, record=record
                        )
                    )
                section_index = ISSUER_IR_SECTIONS_64K.index(section_name)
                expected_event_id = (
                    f"{getattr(world, 'spec', {}).get('prefix', '')}."
                    f"issuer_ir_section_{section_index}_{workflow_index}_{record_index}"
                )
                canonical = canonical_cache[cache_key].get(section_name)
                observed = {
                    "type": event.type,
                    "time": event.time,
                    "params": event.params,
                    "preconditions": list(event.preconditions),
                    "causal_inputs": list(event.causal_inputs),
                    "required_inputs": list(event.required_inputs),
                    "relation_kinds": dict(event.relation_kinds),
                    "skipped": event.skipped,
                    "skip_reason": event.skip_reason,
                }
                if (
                    slots.get("real_workflow_record") is True
                    and isinstance(classification, Mapping)
                    and classification.get("source_origin")
                    in {"real_public", "real_private_export", "real_derived"}
                    and event.id == expected_event_id
                    and list(event.visibility) == [expected_event_id]
                    and event.params.get("workflow_id") == workflow_id
                    and event.params.get("record_id") == record_id
                    and slots.get("event_type") == event.type
                    and slots.get("params") == event.params
                    and getattr(artifact, "doc_type", "") == event.type
                    and getattr(artifact, "artifact_id", "")
                    == f"{world_id}.{expected_event_id}"
                    and getattr(artifact, "time", None) == event.time
                    and str(getattr(artifact, "text", "")).count(
                        str(event.params.get("text") or "")
                    )
                    == 1
                    and canonical == observed
                ):
                    selected_records.add((workflow_id, record_id))
        relation_revealed = [
            event_index[event_id]
            for event_id in getattr(artifact, "reveals_events", ())
            if event_id in event_index
            and event_index[event_id].type == "issuer_ir_prior_filing_relation"
        ]
        if len(relation_revealed) == 1:
            event = relation_revealed[0]
            expected_text = (
                "Issuer annual-filing temporal relation control\n"
                f"Current filing record: {event.params.get('record_id')}.\n"
                f"Prior filing record: {event.params.get('target_record_id')}.\n"
                "Status: prior-annual-filing-validated. This control binds "
                "these issuer-owned rendered statements in chronological order."
            )
            if (
                slots.get("event_type") == event.type
                and slots.get("params") == event.params
                and getattr(artifact, "doc_type", "") == event.type
                and getattr(artifact, "artifact_id", "")
                == f"{world_id}.{event.visibility[0]}"
                and getattr(artifact, "time", None) == event.time
                and str(getattr(artifact, "text", "")).count(expected_text) == 1
            ):
                relation_artifacts.add(event.id)

    edges: list[dict[str, str]] = []
    for event in event_index.values():
        if (
            event.type != "issuer_ir_prior_filing_relation"
            or event.id not in relation_artifacts
        ):
            continue
        workflow_id = str(event.params.get("workflow_id") or "")
        workflow_match = workflows.get(workflow_id)
        if workflow_match is None:
            continue
        _, workflow = workflow_match
        matches = [
            relation
            for relation in getattr(workflow, "relations", ())
            if relation.relation_id == event.params.get("source_relation_id")
            and relation.kind == "prior_available_annual_filing"
        ]
        if len(matches) != 1:
            continue
        relation = matches[0]
        if (
            event.params.get("record_id") != relation.source_record_id
            or event.params.get("target_record_id") != relation.target_record_id
            or {
                (workflow_id, relation.source_record_id),
                (workflow_id, relation.target_record_id),
            }
            - selected_records
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


def load_issuer_ir_filing_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    try:
        raw = _read_regular_file(path, MAX_ISSUER_IR_MANIFEST_BYTES)
    except OSError as exc:
        raise ProvenanceError(f"cannot read issuer IR filing manifest: {exc}") from exc
    return load_issuer_ir_filing_manifest_bytes(raw, attestation_key=attestation_key)


def load_issuer_ir_filing_manifest_bytes(
    raw: bytes, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Verify the exact issuer-manifest bytes supplied by the caller."""
    if len(raw) > MAX_ISSUER_IR_MANIFEST_BYTES:
        raise ProvenanceError("issuer IR filing manifest is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read issuer IR filing manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProvenanceError("issuer IR filing manifest must be an object")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError(
            "issuer IR filing manifest has no valid source attestation"
        )
    _audit_issuer_ir_filing_manifest(payload)
    return payload
