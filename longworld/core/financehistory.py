"""Executable cumulative histories from issuer-owned financial filings.

The adapter preserves complete, non-overlapping rendered-XBRL table rows.  It
never pads or copies rows: a longer band must add a filing, source rows, facts,
and source-bound relations.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any
from urllib.parse import urlparse

from longworld.core.domainhistory import HistoryBand, audit_cumulative_history
from longworld.core.issuerfilingworkflow import parse_issuer_ir_rendered_metrics
from longworld.core.pack import SEP
from longworld.core.provenance import ProvenanceError

FINANCIAL_HISTORY_SCHEMA = "longworld.financial-cumulative-history.v1"
FINANCIAL_HISTORY_REPLAY_REVISION = "longworld.financial-history-replay.v1"
FINANCE_PIPELINE_CANDIDATE_SCHEMA = "longworld.finance-pipeline-candidate.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_NUMBER = re.compile(r"^\(?\$?[ \t]*[0-9][0-9,]*\)?$")
_EXPECTED_BANDS = ("16k", "32k", "64k")
_CORE_ROLES = frozenset(
    {
        "revenue",
        "operating_income",
        "assets",
        "liabilities_and_equity",
        "cash_from_operations",
        "cash_from_investing",
        "cash_from_financing",
        "cash_fx_effect",
        "cash_period_change",
    }
)
_SEMANTIC_ROLE_MARKERS = {
    "revenue": (
        "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        "Total net sales",
    ),
    "operating_income": ("defref_us-gaap_OperatingIncomeLoss", "Operating income"),
    "assets": ("defref_us-gaap_Assets", "Total assets"),
    "liabilities_and_equity": (
        "defref_us-gaap_LiabilitiesAndStockholdersEquity",
        "Total liabilities and stockholders",
    ),
    "cash_from_operations": (
        "defref_us-gaap_NetCashProvidedByUsedInOperatingActivities",
        "operating activities",
    ),
    "cash_from_investing": (
        "defref_us-gaap_NetCashProvidedByUsedInInvestingActivities",
        "investing activities",
    ),
    "cash_from_financing": (
        "defref_us-gaap_NetCashProvidedByUsedInFinancingActivities",
        "financing activities",
    ),
    "cash_fx_effect": (
        "defref_us-gaap_EffectOfExchangeRateOnCash",
        "Foreign currency effect on cash",
    ),
    "cash_period_change": (
        "defref_us-gaap_CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        "Net increase (decrease) in cash",
    ),
}


@dataclass(frozen=True)
class FinancialFact:
    """One source-text fact span used by the executable answer program."""

    role: str
    evidence_quote: str
    relative_start: int


@dataclass(frozen=True)
class FinancialSourceRow:
    """One complete rendered-XBRL row with exact source lineage."""

    record_id: str
    filing_record_id: str
    report_date: str
    source_url: str
    source_sha256: str
    section: str
    source_char_start: int
    source_char_end: int
    source_text: str
    facts: tuple[FinancialFact, ...]


@dataclass(frozen=True)
class FinancialFiling:
    """One verified annual filing and its distinct statement rows."""

    record_id: str
    filing_date: str
    report_date: str
    source_url: str
    source_sha256: str
    rows: tuple[FinancialSourceRow, ...]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _parse_number(value: str) -> int | None:
    if _NUMBER.fullmatch(value) is None:
        return None
    negative = value.startswith("(") and value.endswith(")")
    digits = re.sub(r"[^0-9]", "", value)
    return -int(digits) if negative else int(digits)


def _row_record(row: FinancialSourceRow) -> dict[str, Any]:
    return {
        "record_type": "financial_source_row",
        "source_record_id": row.record_id,
        "filing_record_id": row.filing_record_id,
        "report_date": row.report_date,
        "source_url": row.source_url,
        "source_sha256": row.source_sha256,
        "section": row.section,
        "source_char_start": row.source_char_start,
        "source_char_end": row.source_char_end,
        "source_text": row.source_text,
        "source_text_sha256": _sha256_text(row.source_text),
        "facts": [
            {
                "role": fact.role,
                "evidence_quote": fact.evidence_quote,
                "relative_start": fact.relative_start,
            }
            for fact in row.facts
        ],
    }


def _filing_record(filing: FinancialFiling) -> dict[str, Any]:
    return {
        "record_type": "filing",
        "source_record_id": filing.record_id,
        "filing_date": filing.filing_date,
        "report_date": filing.report_date,
        "source_url": filing.source_url,
        "source_sha256": filing.source_sha256,
    }


def _relation_record(
    current: FinancialFiling, prior: FinancialFiling
) -> dict[str, Any]:
    return {
        "record_type": "filing_relation",
        "relation_id": f"{current.record_id}:prior:{prior.record_id}",
        "kind": "prior_available_annual_filing",
        "source_record_id": current.record_id,
        "target_record_id": prior.record_id,
        "relation_provenance": "verified_derived_temporal_same_issuer",
    }


def _context(records: Sequence[dict[str, Any]]) -> str:
    return "\n".join(_canonical_json(record) for record in records)


def _validate_source_rows(filings: Sequence[FinancialFiling]) -> None:
    if len(filings) < 2:
        raise ProvenanceError("financial history requires at least two filings")
    if list(filings) != sorted(filings, key=lambda filing: filing.report_date):
        raise ProvenanceError("financial filings are not chronological")
    filing_ids: set[str] = set()
    row_ids: set[str] = set()
    row_hashes: set[str] = set()
    for filing in filings:
        parsed = urlparse(filing.source_url)
        if (
            not filing.record_id
            or filing.record_id in filing_ids
            or parsed.scheme != "https"
            or not parsed.netloc
            or _SHA256.fullmatch(filing.source_sha256) is None
            or not filing.rows
        ):
            raise ProvenanceError("financial filing identity is invalid")
        filing_ids.add(filing.record_id)
        previous_end = -1
        for row in sorted(filing.rows, key=lambda item: item.source_char_start):
            row_hash = _sha256_text(row.source_text)
            if row.record_id in row_ids or row_hash in row_hashes:
                raise ProvenanceError("duplicate financial source row")
            if (
                row.filing_record_id != filing.record_id
                or row.report_date != filing.report_date
                or row.source_url != filing.source_url
                or row.source_sha256 != filing.source_sha256
                or not row.section
                or not row.source_text
                or row.source_char_start < previous_end
                or row.source_char_end - row.source_char_start != len(row.source_text)
            ):
                raise ProvenanceError("financial source row lineage is invalid")
            roles: set[str] = set()
            for fact in row.facts:
                start = fact.relative_start
                end = start + len(fact.evidence_quote)
                if (
                    not fact.role
                    or fact.role in roles
                    or not fact.evidence_quote
                    or start < 0
                    or row.source_text[start:end] != fact.evidence_quote
                ):
                    raise ProvenanceError("financial fact span is invalid")
                roles.add(fact.role)
            previous_end = row.source_char_end
            row_ids.add(row.record_id)
            row_hashes.add(row_hash)


def extract_financial_filings(manifest: dict[str, Any]) -> tuple[FinancialFiling, ...]:
    """Extract non-overlapping XBRL rows from an already verified issuer manifest."""
    issuer = manifest.get("issuer")
    records = manifest.get("records")
    if (
        manifest.get("schema_version") != "longworld.issuer-ir-filing-manifest.v1"
        or not isinstance(issuer, dict)
        or not isinstance(records, list)
        or len(records) < 2
    ):
        raise ProvenanceError("issuer financial manifest is invalid")
    filings: list[FinancialFiling] = []
    seen_row_hashes: set[str] = set()
    for record in sorted(records, key=lambda item: str(item.get("report_date") or "")):
        if not isinstance(record, dict):
            raise ProvenanceError("issuer financial record is invalid")
        source_text = record.get("text")
        report_date = str(record.get("report_date") or "")
        if not isinstance(source_text, str) or not source_text:
            raise ProvenanceError("issuer financial source text is missing")
        program = parse_issuer_ir_rendered_metrics(source_text, report_date=report_date)
        facts_by_span = sorted(program.facts, key=lambda fact: fact.char_start)
        source_rows: list[FinancialSourceRow] = []
        local_row_indexes: dict[str, int] = {}
        mapped_facts: set[str] = set()
        for section, section_start, section_end in sorted(
            program.section_ranges, key=lambda value: value[1]
        ):
            for row_index, match in enumerate(
                _ROW.finditer(source_text, section_start, section_end)
            ):
                row_start, row_end = match.span()
                raw_row = match.group()
                raw_hash = _sha256_text(raw_row)
                row_facts = [
                    fact
                    for fact in facts_by_span
                    if row_start <= fact.char_start < fact.char_end <= row_end
                ]
                if raw_hash in seen_row_hashes:
                    prior_index = local_row_indexes.get(raw_hash)
                    if prior_index is not None and row_facts:
                        prior = source_rows[prior_index]
                        merged = {fact.role: fact for fact in prior.facts}
                        merged.update(
                            {
                                fact.role: FinancialFact(
                                    role=fact.role,
                                    evidence_quote=fact.evidence_quote,
                                    relative_start=fact.char_start - row_start,
                                )
                                for fact in row_facts
                            }
                        )
                        source_rows[prior_index] = replace(
                            prior,
                            facts=tuple(merged[role] for role in sorted(merged)),
                        )
                        mapped_facts.update(fact.role for fact in row_facts)
                    continue
                seen_row_hashes.add(raw_hash)
                section_id = hashlib.sha256(section.encode()).hexdigest()[:12]
                source_rows.append(
                    FinancialSourceRow(
                        record_id=(
                            f"{record['record_id']}:section:{section_id}:row:{row_index}"
                        ),
                        filing_record_id=str(record.get("record_id") or ""),
                        report_date=report_date,
                        source_url=str(record.get("source_url") or ""),
                        source_sha256=str(record.get("source_sha256") or ""),
                        section=section,
                        source_char_start=row_start,
                        source_char_end=row_end,
                        source_text=raw_row,
                        facts=tuple(
                            FinancialFact(
                                role=fact.role,
                                evidence_quote=fact.evidence_quote,
                                relative_start=fact.char_start - row_start,
                            )
                            for fact in row_facts
                        ),
                    )
                )
                local_row_indexes[raw_hash] = len(source_rows) - 1
                mapped_facts.update(fact.role for fact in row_facts)
        expected_core_facts = {
            fact.role for fact in program.facts if fact.role in _CORE_ROLES
        }
        if not expected_core_facts <= mapped_facts:
            raise ProvenanceError(
                "issuer core financial facts do not map to exact rows"
            )
        filings.append(
            FinancialFiling(
                record_id=str(record.get("record_id") or ""),
                filing_date=str(record.get("filing_date") or ""),
                report_date=report_date,
                source_url=str(record.get("source_url") or ""),
                source_sha256=str(record.get("source_sha256") or ""),
                rows=tuple(source_rows),
            )
        )
    _validate_source_rows(filings)
    return tuple(filings)


def _parse_context(context: object) -> list[dict[str, Any]]:
    if not isinstance(context, str) or not context:
        raise ProvenanceError("financial history context is empty")
    records: list[dict[str, Any]] = []
    for line in context.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProvenanceError(
                "financial history context is invalid JSON"
            ) from error
        if not isinstance(record, dict) or _canonical_json(record) != line:
            raise ProvenanceError("financial history context is not canonical")
        records.append(record)
    if records[0].get("record_type") != "financial_history_header":
        raise ProvenanceError("financial history header is invalid")
    return records


def _replace_counterfactual(records: list[dict[str, Any]], twin: object) -> None:
    if not isinstance(twin, dict):
        raise ProvenanceError("financial counterfactual is missing")
    matches = [
        record
        for record in records
        if record.get("record_type") == "financial_source_row"
        and record.get("source_record_id") == twin.get("record_id")
    ]
    if (
        len(matches) != 1
        or twin.get("source_origin") != "synthetic_counterfactual"
        or twin.get("provenance_operation") != "replace_exact_span"
    ):
        raise ProvenanceError("financial counterfactual identity is invalid")
    record = matches[0]
    facts = record.get("facts")
    fact_matches = [
        fact
        for fact in facts or []
        if isinstance(fact, dict) and fact.get("role") == twin.get("role")
    ]
    if len(fact_matches) != 1:
        raise ProvenanceError("financial counterfactual fact is invalid")
    fact = fact_matches[0]
    start = fact.get("relative_start")
    parent = twin.get("parent_value")
    replacement = twin.get("value")
    source_text = record.get("source_text")
    if (
        not isinstance(start, int)
        or not isinstance(parent, str)
        or not isinstance(replacement, str)
        or not isinstance(source_text, str)
        or len(parent) != len(replacement)
        or source_text[start : start + len(parent)] != parent
        or parent == replacement
    ):
        raise ProvenanceError("financial counterfactual span is invalid")
    record["source_text"] = (
        source_text[:start] + replacement + source_text[start + len(parent) :]
    )
    fact["evidence_quote"] = replacement


def _filing_chain(
    selected_filing_ids: set[str], relations: Sequence[dict[str, Any]]
) -> list[str]:
    if len(relations) != len(selected_filing_ids) - 1:
        raise ProvenanceError("financial filing chain is incomplete")
    prior_by_current: dict[str, str] = {}
    next_by_prior: dict[str, str] = {}
    relation_ids: set[str] = set()
    for relation in relations:
        current = str(relation.get("source_record_id") or "")
        prior = str(relation.get("target_record_id") or "")
        relation_id = str(relation.get("relation_id") or "")
        if (
            relation.get("kind") != "prior_available_annual_filing"
            or relation.get("relation_provenance")
            != "verified_derived_temporal_same_issuer"
            or relation_id != f"{current}:prior:{prior}"
            or relation_id in relation_ids
            or current not in selected_filing_ids
            or prior not in selected_filing_ids
            or current == prior
            or current in prior_by_current
            or prior in next_by_prior
        ):
            raise ProvenanceError("financial filing chain relation is invalid")
        relation_ids.add(relation_id)
        prior_by_current[current] = prior
        next_by_prior[prior] = current
    oldest = selected_filing_ids - set(prior_by_current)
    if len(oldest) != 1:
        raise ProvenanceError("financial filing chain has no unique origin")
    order = [oldest.pop()]
    while order[-1] in next_by_prior:
        order.append(next_by_prior[order[-1]])
    if set(order) != selected_filing_ids or len(order) != len(selected_filing_ids):
        raise ProvenanceError("financial filing chain does not cover the history")
    return order


def _answer(facts: Sequence[dict[str, Any]], filing_chain: Sequence[str]) -> str:
    filing_order = {filing_id: index for index, filing_id in enumerate(filing_chain)}
    ledger = sorted(
        facts,
        key=lambda fact: (
            filing_order[fact["filing_record_id"]],
            fact["role"],
            fact["record_id"],
        ),
    )
    by_year: dict[str, dict[str, int | str]] = {}
    for fact in ledger:
        by_year.setdefault(fact["report_date"], {})[fact["role"]] = fact["value"]
    annual_checks: list[dict[str, Any]] = []
    for report_date, values in sorted(by_year.items()):
        check: dict[str, Any] = {"report_date": report_date}
        if {"assets", "liabilities_and_equity"} <= values.keys():
            check["balance_sheet_certified"] = (
                values["assets"] == values["liabilities_and_equity"]
            )
        if {"revenue", "operating_income"} <= values.keys() and int(values["revenue"]):
            check["operating_margin_basis_points"] = (
                int(values["operating_income"]) * 10_000 // int(values["revenue"])
            )
        cash_roles = {
            "cash_from_operations",
            "cash_from_investing",
            "cash_from_financing",
            "cash_fx_effect",
            "cash_period_change",
        }
        if cash_roles <= values.keys():
            check["cashflow_reconciled"] = (
                sum(
                    int(values[role])
                    for role in cash_roles
                    if role != "cash_period_change"
                )
                == values["cash_period_change"]
            )
        annual_checks.append(check)
    revenue = [
        fact
        for fact in ledger
        if fact["role"] == "revenue" and isinstance(fact["value"], int)
    ]
    trajectory = None
    if len(revenue) >= 2:
        trajectory = {
            "earliest_report_date": revenue[0]["report_date"],
            "latest_report_date": revenue[-1]["report_date"],
            "revenue_change": revenue[-1]["value"] - revenue[0]["value"],
        }
    return _canonical_json(
        {
            "annual_checks": annual_checks,
            "cross_filing_revenue_trajectory": trajectory,
            "filing_chain": list(filing_chain),
        }
    )


def replay_financial_history(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Recompute the answer solely from serialized financial source rows."""
    try:
        records = _parse_context(task.get("context"))
        header = records[0]
        if (
            header.get("schema_version") != FINANCIAL_HISTORY_SCHEMA
            or header.get("world_id") != task.get("world_id")
            or header.get("source_binding") != task.get("source_binding")
        ):
            raise ProvenanceError("financial history header binding mismatch")
        for record in records:
            if record.get("record_type") != "financial_source_row":
                continue
            source_text = record.get("source_text")
            if not isinstance(source_text, str) or _sha256_text(
                source_text
            ) != record.get("source_text_sha256"):
                raise ProvenanceError("financial source row digest mismatch")
        if counterfactual:
            _replace_counterfactual(records, task.get("counterfactual_twin"))
        selected = set(evidence_ids) if evidence_ids is not None else None
        row_records = [
            record
            for record in records
            if record.get("record_type") == "financial_source_row"
            and (selected is None or record.get("source_record_id") in selected)
        ]
        filing_records = {
            str(record.get("source_record_id")): record
            for record in records
            if record.get("record_type") == "filing"
        }
        relation_records = [
            record
            for record in records
            if record.get("record_type") == "filing_relation"
        ]
        selected_filing_ids = {
            str(record.get("filing_record_id")) for record in row_records
        }
        if not row_records or not selected_filing_ids <= filing_records.keys():
            raise ProvenanceError("financial replay selection is empty")
        fact_ledger: list[dict[str, Any]] = []
        essential_ids: list[str] = []
        roles_by_report: set[tuple[str, str]] = set()
        for record in row_records:
            source_text = str(record["source_text"])
            facts = record.get("facts")
            if not isinstance(facts, list):
                raise ProvenanceError("financial row facts are invalid")
            row_is_essential = False
            for fact in facts:
                if not isinstance(fact, dict):
                    raise ProvenanceError("financial row fact is invalid")
                role = str(fact.get("role") or "")
                if role not in _CORE_ROLES:
                    continue
                start = fact.get("relative_start")
                quote = fact.get("evidence_quote")
                markers = _SEMANTIC_ROLE_MARKERS[role]
                if (
                    not isinstance(start, int)
                    or not isinstance(quote, str)
                    or source_text[start : start + len(quote)] != quote
                    or not all(marker in source_text for marker in markers)
                ):
                    raise ProvenanceError("financial row fact span mismatch")
                identity = (str(record["report_date"]), role)
                if identity in roles_by_report:
                    raise ProvenanceError(
                        "financial replay has a duplicate annual role"
                    )
                roles_by_report.add(identity)
                numeric = _parse_number(quote)
                fact_ledger.append(
                    {
                        "record_id": record["source_record_id"],
                        "filing_record_id": record["filing_record_id"],
                        "report_date": record["report_date"],
                        "role": role,
                        "value": numeric if numeric is not None else quote,
                    }
                )
                row_is_essential = True
            if row_is_essential:
                essential_ids.append(str(record["source_record_id"]))
        if not fact_ledger:
            raise ProvenanceError("financial replay has no facts")
        selected_relations = [
            relation
            for relation in relation_records
            if relation.get("source_record_id") in selected_filing_ids
            and relation.get("target_record_id") in selected_filing_ids
        ]
        filing_chain = _filing_chain(selected_filing_ids, selected_relations)
        containment_edges = [
            [
                str(record["filing_record_id"]),
                str(record["source_record_id"]),
                f"contains-exact-span:{record['filing_record_id']}:{record['source_record_id']}",
            ]
            for record in row_records
            if record["source_record_id"] in essential_ids
        ]
        derived_edges = [
            [
                str(relation["source_record_id"]),
                str(relation["target_record_id"]),
                str(relation["relation_id"]),
            ]
            for relation in selected_relations
        ]
        source_record_ids = [
            *sorted(selected_filing_ids),
            *(str(record["source_record_id"]) for record in row_records),
        ]
        relation_ids = [
            *(edge[2] for edge in containment_edges),
            *(edge[2] for edge in derived_edges),
        ]
        return {
            "answer": _answer(fact_ledger, filing_chain),
            "source_record_ids": source_record_ids,
            "source_relation_ids": relation_ids,
            "essential_evidence_ids": essential_ids,
            "authentic_source_relation_edges": containment_edges,
            "verified_derived_relation_edges": derived_edges,
            "event_count": len(source_record_ids),
            "strict_support_event_count": len(selected_filing_ids) + len(essential_ids),
            "proof_depth": 3 + len(derived_edges),
            "hop_count": 2 + len(derived_edges),
        }
    except (KeyError, TypeError, ValueError, ProvenanceError):
        return {
            "answer": "unknown",
            "source_record_ids": [],
            "source_relation_ids": [],
            "essential_evidence_ids": [],
            "authentic_source_relation_edges": [],
            "verified_derived_relation_edges": [],
            "event_count": 0,
            "strict_support_event_count": 0,
            "proof_depth": 0,
            "hop_count": 0,
        }


def _replacement_quote(value: str) -> str:
    numeric = _parse_number(value)
    if numeric is None or numeric < 0:
        raise ProvenanceError("financial counterfactual requires a positive number")
    replacement = f"{numeric + 1:,}"
    prefix = "$ " if "$" in value else ""
    replacement = prefix + replacement
    if len(replacement) != len(value):
        replacement = value[:-1] + str((int(value[-1]) + 1) % 10)
    if len(replacement) != len(value) or replacement == value:
        raise ProvenanceError("cannot construct financial counterfactual")
    return replacement


def _candidate_rows(
    filings: Sequence[FinancialFiling], selected_ids: set[str], used_ids: set[str]
) -> list[FinancialSourceRow]:
    rows = [
        row
        for filing in filings
        if filing.record_id in selected_ids
        for row in filing.rows
        if row.record_id not in used_ids
    ]
    return sorted(
        rows,
        key=lambda row: (
            not bool(row.facts),
            row.report_date,
            row.source_char_start,
            row.record_id,
        ),
    )


def build_financial_history_candidates(
    filings: Sequence[FinancialFiling],
    *,
    world_id: str,
    issuer_name: str,
    cik: str,
    source_binding: dict[str, Any],
    bands: Sequence[HistoryBand],
    token_counter: Callable[[str], int],
    tokenizer_model_id: str,
    tokenizer_revision: str,
) -> list[dict[str, Any]]:
    """Build exact nested 16/32/64K histories from distinct filing rows."""
    _validate_source_rows(filings)
    if (
        not world_id
        or not issuer_name
        or not cik
        or not tokenizer_model_id
        or _COMMIT_SHA.fullmatch(tokenizer_revision) is None
        or [band.name for band in bands] != list(_EXPECTED_BANDS[: len(bands)])
    ):
        raise ProvenanceError("financial history materialization identity is invalid")
    if (
        set(source_binding)
        != {
            "signed_manifest_sha256",
            "source_family",
            "authorization_record_id",
        }
        or _SHA256.fullmatch(str(source_binding.get("signed_manifest_sha256") or ""))
        is None
        or not str(source_binding.get("source_family") or "").strip()
        or not str(source_binding.get("authorization_record_id") or "").strip()
    ):
        raise ProvenanceError("financial history source binding is invalid")
    records: list[dict[str, Any]] = [
        {
            "record_type": "financial_history_header",
            "schema_version": FINANCIAL_HISTORY_SCHEMA,
            "world_id": world_id,
            "issuer_name": issuer_name,
            "cik": cik,
            "source_binding": deepcopy(source_binding),
        }
    ]
    used_row_ids: set[str] = set()
    selected_filing_ids: set[str] = set()
    output: list[dict[str, Any]] = []
    for band_index, band in enumerate(bands):
        required_filing_count = min(len(filings), band_index + 2)
        if required_filing_count < band_index + 2:
            raise ProvenanceError(f"cannot fill exact {band.name}: too few filings")
        newly_selected = filings[:required_filing_count]
        for filing_index, filing in enumerate(newly_selected):
            if filing.record_id in selected_filing_ids:
                continue
            records.append(_filing_record(filing))
            selected_filing_ids.add(filing.record_id)
            if filing_index:
                records.append(
                    _relation_record(filing, newly_selected[filing_index - 1])
                )
            mandatory = [
                row
                for row in filing.rows
                if row.facts
                and any(fact.role in _CORE_ROLES for fact in row.facts)
                and row.record_id not in used_row_ids
            ]
            for row in mandatory:
                records.append(_row_record(row))
                used_row_ids.add(row.record_id)
        context = _context(records)
        tokens = token_counter(context)
        if tokens > band.upper_tokens:
            raise ProvenanceError(
                f"cannot fill exact {band.name}: mandatory history is {tokens} tokens"
            )
        for row in _candidate_rows(filings, selected_filing_ids, used_row_ids):
            candidate_records = [*records, _row_record(row)]
            candidate_context = _context(candidate_records)
            candidate_tokens = token_counter(candidate_context)
            if candidate_tokens <= band.upper_tokens:
                records = candidate_records
                context = candidate_context
                tokens = candidate_tokens
                used_row_ids.add(row.record_id)
            if tokens >= band.lower_tokens:
                break
        if not band.lower_tokens <= tokens <= band.upper_tokens:
            raise ProvenanceError(
                f"cannot fill exact {band.name} from verified source rows: {tokens} tokens"
            )
        task: dict[str, Any] = {
            "schema_version": FINANCIAL_HISTORY_SCHEMA,
            "data_stage": "candidate_history",
            "train_ready": False,
            "production_eligible": False,
            "promotion_eligible": False,
            "complete_world": False,
            "promoted": False,
            "generation_integration": "disabled",
            "world_id": world_id,
            "domain": "finance",
            "workflow_kind": "real_source_derived",
            "query_type": "multi_filing_financial_reconstruction",
            "answer_program_id": "finance.multi_filing_reconstruction.v1",
            "answer_program_operations": [
                "source_span_parse",
                "cross_filing_trajectory",
                "balance_sheet_certification",
                "cashflow_reconciliation",
                "operating_margin_reconciliation",
            ],
            "semantic_growth_group_id": f"{world_id}|multi-filing-finance",
            "length_bucket": band.name,
            "question": (
                "Using the source-span financial facts and the complete prior-"
                "filing chain, report the earliest-to-latest "
                "revenue change and independently certify each available balance "
                "sheet, cash-flow reconciliation, and operating margin."
            ),
            "context": context,
            "context_sha256": _sha256_text(context),
            "answer": "",
            "cf_answer": "",
            "source_binding": deepcopy(source_binding),
            "selected_filing_count": required_filing_count,
            "tokenizer_model_id": tokenizer_model_id,
            "tokenizer_revision": tokenizer_revision,
            "tokenizer_context_tokens": tokens,
            "actual_context_tokens": tokens,
            "band_lower_tokens": band.lower_tokens,
            "band_upper_tokens": band.upper_tokens,
            "semantic_tokens": {
                "internal": tokens,
                "event_bearing": 0,
                "proof_bearing": 0,
                "causal_supporting": 0,
                "generic_background": 0,
            },
            "real_source_verified": False,
            "source_verified_at_materialization": False,
            "real_source_token_ratio": 0.0,
            "strict_replay_revision": FINANCIAL_HISTORY_REPLAY_REVISION,
        }
        replay = replay_financial_history(task)
        if replay["answer"] == "unknown":
            raise ProvenanceError("financial history base replay failed")
        revenue_records = [
            record
            for record in records
            if record.get("record_type") == "financial_source_row"
            and any(
                isinstance(fact, dict) and fact.get("role") == "revenue"
                for fact in record.get("facts") or []
            )
        ]
        if not revenue_records:
            raise ProvenanceError("financial history has no revenue fact")
        target = revenue_records[-1]
        target_fact = next(
            fact for fact in target["facts"] if fact.get("role") == "revenue"
        )
        task["counterfactual_twin"] = {
            "record_id": target["source_record_id"],
            "role": "revenue",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_exact_span",
            "parent_value": target_fact["evidence_quote"],
            "value": _replacement_quote(target_fact["evidence_quote"]),
        }
        task.update(
            {
                "answer": replay["answer"],
                "source_record_ids": replay["source_record_ids"],
                "source_relation_ids": replay["source_relation_ids"],
                "essential_evidence_ids": replay["essential_evidence_ids"],
                "authentic_source_relation_edges": replay[
                    "authentic_source_relation_edges"
                ],
                "verified_derived_relation_edges": replay[
                    "verified_derived_relation_edges"
                ],
                "event_count": replay["event_count"],
                "strict_support_event_count": replay["strict_support_event_count"],
                "graph": {
                    "proof_depth": replay["proof_depth"],
                    "hop_count": replay["hop_count"],
                },
            }
        )
        row_records = [
            record
            for record in records
            if record.get("record_type") == "financial_source_row"
        ]
        essential_ids = set(replay["essential_evidence_ids"])
        event_text = "\n".join(str(record["source_text"]) for record in row_records)
        proof_text = "\n".join(
            str(record["source_text"])
            for record in row_records
            if record["source_record_id"] in essential_ids
        )
        task["semantic_tokens"]["event_bearing"] = token_counter(event_text)
        task["semantic_tokens"]["proof_bearing"] = token_counter(proof_text)
        task["real_source_token_ratio"] = (
            task["semantic_tokens"]["event_bearing"] / tokens
        )
        task["cf_answer"] = replay_financial_history(task, counterfactual=True)[
            "answer"
        ]
        audit = audit_financial_history_candidate(task)
        if not audit or not all(audit.values()):
            failed = sorted(name for name, passed in audit.items() if not passed)
            raise ProvenanceError(
                "financial history candidate failed executable audit: "
                + ",".join(failed)
            )
        output.append(task)
    cumulative_errors = audit_cumulative_history(output)
    if cumulative_errors:
        raise ProvenanceError(
            "financial history failed cumulative growth: " + ",".join(cumulative_errors)
        )
    return output


def _pipeline_artifact_id(record: dict[str, Any]) -> str:
    if record.get("record_type") == "filing_relation":
        value = record.get("relation_id")
    else:
        value = record.get("source_record_id")
    if not isinstance(value, str) or not value:
        raise ProvenanceError("financial pipeline record identity is invalid")
    return value


def _pipeline_relation_partitions(task: dict[str, Any]) -> dict[str, Any]:
    def records(value: object, provenance: str) -> list[dict[str, str]]:
        if not isinstance(value, list):
            raise ProvenanceError("financial pipeline relation partition is invalid")
        output: list[dict[str, str]] = []
        for edge in value:
            if (
                not isinstance(edge, list)
                or len(edge) != 3
                or any(not isinstance(item, str) or not item for item in edge)
            ):
                raise ProvenanceError("financial pipeline relation edge is invalid")
            output.append(
                {
                    "source_record_id": edge[0],
                    "target_record_id": edge[1],
                    "relation_id": edge[2],
                    "relation_provenance": provenance,
                }
            )
        return output

    return {
        "authentic_exact_span_containment": records(
            task.get("authentic_source_relation_edges"),
            "authentic_exact_span_containment",
        ),
        "verified_derived_temporal": records(
            task.get("verified_derived_relation_edges"),
            "verified_derived_temporal_same_issuer",
        ),
    }


def build_finance_pipeline_candidate(
    task: dict[str, Any],
    *,
    task_replay_sidecar_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt an audited finance history to the shared document-ranker boundary.

    The result remains candidate-only.  When a source-role task sidecar is
    supplied, the shared task audit can dispatch Finance replay; independent
    upstream proof gates are still required before promotion.
    """
    finance_audit = audit_financial_history_candidate(task)
    if not finance_audit or not all(finance_audit.values()):
        failed = sorted(name for name, passed in finance_audit.items() if not passed)
        raise ProvenanceError(
            "financial pipeline input failed executable audit: " + ",".join(failed)
        )
    records = _parse_context(task.get("context"))
    artifact_records = records[1:]
    artifact_ids = [_pipeline_artifact_id(record) for record in artifact_records]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ProvenanceError("financial pipeline artifact identities are duplicated")
    filing_ids = {
        _pipeline_artifact_id(record)
        for record in artifact_records
        if record.get("record_type") == "filing"
    }
    relation_ids = {
        _pipeline_artifact_id(record)
        for record in artifact_records
        if record.get("record_type") == "filing_relation"
    }
    essential_rows = set(task.get("essential_evidence_ids") or [])
    essential_ids = [
        artifact_id
        for artifact_id in artifact_ids
        if artifact_id in filing_ids
        or artifact_id in relation_ids
        or artifact_id in essential_rows
    ]
    replay = replay_financial_history(task)
    answer = json.loads(str(task["answer"]))
    filing_chain = answer.get("filing_chain")
    if (
        replay["answer"] != task.get("answer")
        or not isinstance(filing_chain, list)
        or filing_ids != set(filing_chain)
    ):
        raise ProvenanceError("financial pipeline state replay is invalid")

    classifications: list[dict[str, str]] = []
    source_urls: set[str] = set()
    for artifact_id, record in zip(artifact_ids, artifact_records, strict=True):
        record_type = str(record.get("record_type") or "")
        if record_type == "financial_source_row":
            source_origin = "real_derived"
            provenance_id = "exact-span-sha256:" + str(
                record.get("source_text_sha256") or ""
            )
            source_urls.add(str(record.get("source_url") or ""))
        elif record_type == "filing":
            source_origin = "real_public"
            provenance_id = "source-sha256:" + str(record.get("source_sha256") or "")
            source_urls.add(str(record.get("source_url") or ""))
        elif record_type == "filing_relation":
            source_origin = "real_derived"
            provenance_id = "derived-relation-sha256:" + _sha256_text(
                _canonical_json(record)
            )
        else:
            raise ProvenanceError("financial pipeline record type is unsupported")
        classifications.append(
            {
                "artifact_id": artifact_id,
                "source_origin": source_origin,
                "workflow_kind": "real_source_derived",
                "evidence_role": (
                    "causal_gold"
                    if artifact_id in essential_ids
                    else "natural_background"
                ),
                "workflow_id": str(task["world_id"]),
                "provenance_id": provenance_id,
            }
        )
    source_binding = task.get("source_binding")
    source_family = (
        str(source_binding.get("source_family") or "")
        if isinstance(source_binding, dict)
        else ""
    )
    if "" in source_urls or not source_family:
        raise ProvenanceError("financial pipeline source identity is invalid")
    if task_replay_sidecar_binding is not None and (
        set(task_replay_sidecar_binding)
        != {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        }
        or task_replay_sidecar_binding.get("adapter_id") != "finance.multi_filing.v1"
        or task_replay_sidecar_binding.get("adapter_revision")
        != FINANCIAL_HISTORY_REPLAY_REVISION
        or task_replay_sidecar_binding.get("sidecar_schema_version")
        != "longworld.task-replay-sidecar.v1"
        or _SHA256.fullmatch(str(task_replay_sidecar_binding.get("sha256") or ""))
        is None
    ):
        raise ProvenanceError("finance task replay sidecar binding is invalid")

    candidate = deepcopy(task)
    candidate.update(
        {
            "schema_version": FINANCE_PIPELINE_CANDIDATE_SCHEMA,
            "data_stage": "candidate",
            "training_objective": "sft",
            "query_id": (
                f"{task['world_id']}:{task['answer_program_id']}:"
                f"{task['length_bucket']}:full"
            ),
            "view": "full",
            "query_timing": "late",
            "composition_method": "same_case_dossier",
            "document_context": SEP.join(
                _canonical_json(record) for record in artifact_records
            ),
            "artifact_classification": classifications,
            "essential_artifact_ids": essential_ids,
            "source_family_ids": [source_family],
            "source_urls": sorted(source_urls),
            "source_relation_partitions": _pipeline_relation_partitions(task),
            "finance_state": {
                "schema_version": "longworld.finance-state.v1",
                "filing_chain": filing_chain,
                "selected_filing_count": task["selected_filing_count"],
                "event_count": task["event_count"],
                "strict_support_event_count": task["strict_support_event_count"],
            },
            "finance_task": {
                "query_type": task["query_type"],
                "answer_program_id": task["answer_program_id"],
                "answer_program_operations": list(task["answer_program_operations"]),
            },
            "finance_replay_contract": {
                "adapter_id": "finance.multi_filing.v1",
                "revision": FINANCIAL_HISTORY_REPLAY_REVISION,
                "counterfactual_twin": deepcopy(task["counterfactual_twin"]),
                "input_audit": finance_audit,
            },
            "pipeline_capabilities": {
                "dense_ranking": True,
                "finance_strict_replay": True,
                "generic_strict_replay": False,
                "generic_promotion": False,
            },
            "generation_integration": "finance_dense_candidate",
        }
    )
    if task_replay_sidecar_binding is not None:
        candidate["task_replay_sidecar"] = deepcopy(task_replay_sidecar_binding)
        candidate["generation_integration"] = "task_replay_sidecar_bound"
    return candidate


def replay_finance_pipeline_selection(
    candidate: dict[str, Any],
    artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Strictly replay only the documents selected by a retrieval stage."""
    if (
        candidate.get("schema_version") != FINANCE_PIPELINE_CANDIDATE_SCHEMA
        or not isinstance(artifact_ids, Sequence)
        or isinstance(artifact_ids, (str, bytes))
        or any(not isinstance(value, str) or not value for value in artifact_ids)
        or len(artifact_ids) != len(set(artifact_ids))
    ):
        return {"answer": "unknown"}
    try:
        records = _parse_context(candidate.get("context"))
        by_id = {_pipeline_artifact_id(record): record for record in records[1:]}
        if not set(artifact_ids) <= by_id.keys():
            raise ProvenanceError("financial pipeline selection is not source-bound")
        selected = [
            records[0],
            *(
                record
                for record in records[1:]
                if _pipeline_artifact_id(record) in artifact_ids
            ),
        ]
        replay_task = deepcopy(candidate)
        replay_task["context"] = _context(selected)
        replay_task["context_sha256"] = _sha256_text(replay_task["context"])
        return replay_financial_history(replay_task, counterfactual=counterfactual)
    except (KeyError, ProvenanceError):
        return {"answer": "unknown"}


def audit_finance_pipeline_candidate(candidate: dict[str, Any]) -> dict[str, bool]:
    """Audit the finance adapter without claiming generic promotion support."""
    try:
        records = _parse_context(candidate.get("context"))
        artifact_records = records[1:]
        artifact_ids = [_pipeline_artifact_id(record) for record in artifact_records]
        classifications = candidate.get("artifact_classification")
        documents = str(candidate.get("document_context") or "").split(SEP)
        classified_ids = [
            str(item.get("artifact_id") or "")
            for item in classifications or []
            if isinstance(item, dict)
        ]
        document_binding = (
            isinstance(classifications, list)
            and len(documents) == len(artifact_records) == len(classifications)
            and documents == [_canonical_json(record) for record in artifact_records]
            and classified_ids == artifact_ids
        )
        essentials = candidate.get("essential_artifact_ids")
        essentials = essentials if isinstance(essentials, list) else []
        base = replay_finance_pipeline_selection(candidate, essentials)
        cf = replay_finance_pipeline_selection(
            candidate, essentials, counterfactual=True
        )
        removals = [
            replay_finance_pipeline_selection(
                candidate, [value for value in essentials if value != removed]
            )["answer"]
            for removed in essentials
        ]
        partitions = candidate.get("source_relation_partitions")
        authentic = (
            partitions.get("authentic_exact_span_containment")
            if isinstance(partitions, dict)
            else None
        )
        derived = (
            partitions.get("verified_derived_temporal")
            if isinstance(partitions, dict)
            else None
        )
        relation_split = bool(
            isinstance(authentic, list)
            and isinstance(derived, list)
            and authentic
            and derived
            and {
                str(item.get("relation_id") or "")
                for item in authentic
                if isinstance(item, dict)
            }.isdisjoint(
                {
                    str(item.get("relation_id") or "")
                    for item in derived
                    if isinstance(item, dict)
                }
            )
        )
        input_audit = audit_financial_history_candidate(candidate)
        return {
            "input_finance_audit_green": bool(input_audit)
            and all(input_audit.values()),
            "document_binding_valid": document_binding,
            "selected_strict_replay_sufficient": base["answer"]
            == candidate.get("answer"),
            "selected_counterfactual_replay_sufficient": cf["answer"]
            == candidate.get("cf_answer"),
            "selected_counterfactual_changes_answer": cf["answer"] != base["answer"],
            "selected_remove_one_fails": bool(removals)
            and all(answer != candidate.get("answer") for answer in removals),
            "authentic_derived_relation_split": relation_split,
            "candidate_only_boundary": candidate.get("pipeline_capabilities")
            == {
                "dense_ranking": True,
                "finance_strict_replay": True,
                "generic_strict_replay": False,
                "generic_promotion": False,
            }
            and all(
                candidate.get(field) is False
                for field in (
                    "train_ready",
                    "production_eligible",
                    "promotion_eligible",
                    "complete_world",
                    "promoted",
                )
            ),
        }
    except (KeyError, ProvenanceError):
        return {"adapter_valid": False}


def audit_finance_dense_ranking(
    candidate: dict[str, Any], ranking: dict[str, Any], *, k: int = 3
) -> dict[str, Any]:
    """Replay a shared dense-ranking result through the finance adapter."""
    pipeline_audit = audit_finance_pipeline_candidate(candidate)
    if not pipeline_audit or not all(pipeline_audit.values()):
        raise ProvenanceError("finance dense audit received an invalid candidate")
    unsigned_candidate = {
        key: value for key, value in candidate.items() if key != "attestation"
    }
    expected_candidate_sha256 = _sha256_text(_canonical_json(unsigned_candidate))
    if (
        ranking.get("schema_version") != "dense-ranking-v2"
        or ranking.get("ranker_type") != "dense_embedding"
        or ranking.get("query_id") != candidate.get("query_id")
        or ranking.get("candidate_sha256") != expected_candidate_sha256
        or ranking.get("query_sha256")
        != _sha256_text(str(candidate.get("question") or ""))
    ):
        raise ProvenanceError("finance dense ranking identity is invalid")
    classifications = candidate.get("artifact_classification")
    documents = str(candidate.get("document_context") or "").split(SEP)
    if not isinstance(classifications, list) or len(classifications) != len(documents):
        raise ProvenanceError("finance dense candidate document binding is invalid")
    expected = {
        str(classification["artifact_id"]): _sha256_text(document)
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    }
    raw_artifacts = ranking.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(expected):
        raise ProvenanceError("finance dense ranking artifact pool is incomplete")
    ranked_ids: list[str] = []
    scores: list[float] = []
    for expected_rank, item in enumerate(raw_artifacts, start=1):
        if not isinstance(item, dict) or item.get("rank") != expected_rank:
            raise ProvenanceError("finance dense ranking order is invalid")
        artifact_id = str(item.get("artifact_id") or "")
        try:
            score = float(item["score"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProvenanceError("finance dense ranking score is invalid") from error
        if (
            artifact_id in ranked_ids
            or artifact_id not in expected
            or item.get("text_sha256") != expected[artifact_id]
            or not math.isfinite(score)
            or not isinstance(item.get("chunk_count"), int)
            or isinstance(item.get("chunk_count"), bool)
            or int(item["chunk_count"]) < 1
        ):
            raise ProvenanceError("finance dense ranking artifact is invalid")
        ranked_ids.append(artifact_id)
        scores.append(score)
    if set(ranked_ids) != set(expected) or any(
        left < right for left, right in pairwise(scores)
    ):
        raise ProvenanceError("finance dense ranking pool or scores are invalid")
    if k < 1 or k >= len(ranked_ids):
        raise ProvenanceError("finance dense top-k is outside the artifact pool")
    prefix_answers = [
        replay_finance_pipeline_selection(candidate, ranked_ids[:prefix])["answer"]
        for prefix in range(1, k + 1)
    ]
    full_answer = replay_finance_pipeline_selection(candidate, ranked_ids)["answer"]
    expected_answer = str(candidate.get("answer") or "")
    if expected_answer in prefix_answers or full_answer != expected_answer:
        raise ProvenanceError("finance dense retrieval gate failed")
    return {
        "schema_version": "longworld.finance-dense-audit.v1",
        "candidate_sha256": expected_candidate_sha256,
        "query_id": candidate["query_id"],
        "k": k,
        "top_k_artifact_ids": ranked_ids[:k],
        "strict_replay_prefix_answers": prefix_answers,
        "expected_answer": expected_answer,
        "embedding_topk_insufficient": True,
        "full_pool_strict_replay_sufficient": True,
        "strict_replay_revision": FINANCIAL_HISTORY_REPLAY_REVISION,
        "generic_promotion_ready": False,
    }


def audit_financial_history_candidate(task: dict[str, Any]) -> dict[str, bool]:
    """Audit strict replay, CF, remove-one, corruption, and candidate boundary."""
    replay = replay_financial_history(task)
    cf_replay = replay_financial_history(task, counterfactual=True)
    essentials = task.get("essential_evidence_ids")
    essentials = essentials if isinstance(essentials, list) else []
    removals = [
        replay_financial_history(
            task,
            evidence_ids=[value for value in essentials if value != removed],
        )["answer"]
        for removed in essentials
    ]
    singles = [
        replay_financial_history(task, evidence_ids=[essential])["answer"]
        for essential in essentials
    ]
    corrupted = deepcopy(task)
    numeric_corruption_fails = False
    try:
        records = _parse_context(corrupted.get("context"))
        targets = [
            (record, fact)
            for record in records
            if record.get("source_record_id") in essentials
            for fact in record.get("facts") or []
            if isinstance(fact, dict)
        ]
        target, fact = next(
            (record, value)
            for record, value in targets
            if isinstance(value.get("evidence_quote"), str)
            and (numeric := _parse_number(value["evidence_quote"])) is not None
            and numeric >= 0
        )
        start = fact["relative_start"]
        source_text = target["source_text"]
        quote = fact["evidence_quote"]
        replacement = _replacement_quote(quote)
        target["source_text"] = (
            source_text[:start] + replacement + source_text[start + len(quote) :]
        )
        target["source_text_sha256"] = _sha256_text(target["source_text"])
        fact["evidence_quote"] = replacement
        corrupted["context"] = _context(records)
        corrupted["context_sha256"] = _sha256_text(corrupted["context"])
        corrupted_answer = replay_financial_history(corrupted)["answer"]
        numeric_corruption_fails = corrupted_answer not in (
            "unknown",
            task.get("answer"),
        )
    except (KeyError, StopIteration, IndexError, ProvenanceError):
        numeric_corruption_fails = False
    label_corruption_fails = False
    try:
        label_corrupted = deepcopy(task)
        records = _parse_context(label_corrupted.get("context"))
        target, fact = next(
            (record, value)
            for record in records
            if record.get("source_record_id") in essentials
            for value in record.get("facts") or []
            if isinstance(value, dict) and value.get("role") in _SEMANTIC_ROLE_MARKERS
        )
        marker = _SEMANTIC_ROLE_MARKERS[str(fact["role"])][1]
        target["source_text"] = str(target["source_text"]).replace(
            marker, "X" * len(marker), 1
        )
        target["source_text_sha256"] = _sha256_text(target["source_text"])
        label_corrupted["context"] = _context(records)
        label_corrupted["context_sha256"] = _sha256_text(label_corrupted["context"])
        label_corruption_fails = (
            replay_financial_history(label_corrupted)["answer"] == "unknown"
        )
    except (KeyError, StopIteration, IndexError, ProvenanceError):
        label_corruption_fails = False
    relation_removals: list[str] = []
    relation_corruption_fails = False
    try:
        records = _parse_context(task.get("context"))
        relation_ids = [
            str(record["relation_id"])
            for record in records
            if record.get("record_type") == "filing_relation"
        ]
        for relation_id in relation_ids:
            relation_removed = deepcopy(task)
            relation_removed["context"] = _context(
                [
                    record
                    for record in records
                    if record.get("relation_id") != relation_id
                ]
            )
            relation_removed["context_sha256"] = _sha256_text(
                relation_removed["context"]
            )
            relation_removals.append(
                replay_financial_history(relation_removed)["answer"]
            )
        relation_corrupted = deepcopy(task)
        corrupted_records = _parse_context(relation_corrupted.get("context"))
        relation = next(
            record
            for record in corrupted_records
            if record.get("record_type") == "filing_relation"
        )
        relation["target_record_id"] = relation["source_record_id"]
        relation_corrupted["context"] = _context(corrupted_records)
        relation_corrupted["context_sha256"] = _sha256_text(
            relation_corrupted["context"]
        )
        relation_corruption_fails = (
            replay_financial_history(relation_corrupted)["answer"] == "unknown"
        )
    except (KeyError, StopIteration, ProvenanceError):
        relation_corruption_fails = False
    declared_tokens = task.get("tokenizer_context_tokens")
    lower = task.get("band_lower_tokens")
    upper = task.get("band_upper_tokens")
    source_ids = task.get("source_record_ids")
    source_ids = source_ids if isinstance(source_ids, list) else []
    return {
        "strict_replay_sufficient": replay["answer"] == task.get("answer"),
        "counterfactual_replay_sufficient": cf_replay["answer"] != "unknown"
        and cf_replay["answer"] == task.get("cf_answer"),
        "counterfactual_changes_answer": cf_replay["answer"] != replay["answer"],
        "remove_one_fails": bool(removals)
        and all(answer != task.get("answer") for answer in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(answer != task.get("answer") for answer in singles),
        "essential_surface_gold_free": str(task.get("answer") or "")
        not in str(task.get("context") or ""),
        "semantic_corruption_fails": numeric_corruption_fails
        and label_corruption_fails,
        "remove_one_relation_fails": bool(relation_removals)
        and all(answer != task.get("answer") for answer in relation_removals),
        "relation_corruption_fails": relation_corruption_fails,
        "context_digest_valid": task.get("context_sha256")
        == _sha256_text(str(task.get("context") or "")),
        "no_duplicate_source_records": len(source_ids) == len(set(source_ids)),
        "exact_token_band_declared": bool(
            isinstance(declared_tokens, int)
            and not isinstance(declared_tokens, bool)
            and isinstance(lower, int)
            and isinstance(upper, int)
            and lower <= declared_tokens <= upper
        ),
        "replayed_source_records_match": source_ids == replay["source_record_ids"],
        "replayed_source_relations_match": task.get("source_relation_ids")
        == replay["source_relation_ids"],
        "replayed_essentials_match": essentials == replay["essential_evidence_ids"],
        "replayed_graph_matches": task.get("event_count") == replay["event_count"]
        and task.get("strict_support_event_count")
        == replay["strict_support_event_count"]
        and (task.get("graph") or {}).get("proof_depth") == replay["proof_depth"]
        and (task.get("graph") or {}).get("hop_count") == replay["hop_count"]
        and task.get("authentic_source_relation_edges")
        == replay["authentic_source_relation_edges"]
        and task.get("verified_derived_relation_edges")
        == replay["verified_derived_relation_edges"],
        "non_promoted_boundary": all(
            task.get(field) is False
            for field in (
                "train_ready",
                "production_eligible",
                "promotion_eligible",
                "complete_world",
                "promoted",
            )
        ),
    }
