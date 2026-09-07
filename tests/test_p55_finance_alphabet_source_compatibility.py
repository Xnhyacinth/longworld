"""Alphabet's issuer-linked controls and real statement rows stay fail closed."""

import gzip
from pathlib import Path

import pytest

from longworld.core.issuerfilingworkflow import parse_issuer_ir_rendered_metrics
from longworld.core.provenance import ProvenanceError
from scripts.fetch_issuer_ir_filing_history import _parse_detail

FIXTURES = Path("tests/fixtures/p55_finance_alphabet")


def table_text():
    return gzip.decompress(
        (FIXTURES / "2024_primary_tables.html.gz").read_bytes()
    ).decode()


def parse(text):
    return parse_issuer_ir_rendered_metrics(
        text, report_date="2024-12-31", issuer_cik="0001652044"
    )


def test_real_q4_controls_with_unrelated_script_comment():
    urls = _parse_detail(
        (FIXTURES / "2024_detail_controls.html").read_bytes(),
        expected_form="10-K",
        expected_filing_date="2025-02-05",
        artifact_host="d18rn0p25nwr6d.cloudfront.net",
        artifact_path_prefix="/CIK-0001652044/",
        control_prefix="_ctrl0_ctl33_",
    )
    assert set(urls) == {"annual_report_pdf", "xbrl_zip", "rendered_xbrl_html"}


def test_visible_challenge_still_rejected():
    body = b"<html><h1>CAPTCHA challenge</h1></html>"
    with pytest.raises(ProvenanceError):
        _parse_detail(
            body,
            expected_form="10-K",
            expected_filing_date="2025-02-05",
            artifact_host="d18rn0p25nwr6d.cloudfront.net",
            artifact_path_prefix="/CIK-0001652044/",
            control_prefix="_ctrl0_ctl33_",
        )


def test_real_alphabet_asset_role_closure():
    text = table_text()
    program = parse(text)
    values = {f.role: f.numeric_value for f in program.facts}
    assert values == {
        "assets": 450256,
        "liabilities_and_equity": 450256,
        "revenue": 350018,
        "cash_from_operations": 125299,
    }
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote


@pytest.mark.parametrize(
    "old,new",
    [
        ("0001652044", "0001018724"),
        ("Dec. 31,  2024", "Dec. 31,  2023"),
        ("$ in Millions", "$ in Thousands"),
    ],
)
def test_wrong_identity_period_or_unit_rejected(old, new):
    with pytest.raises(ProvenanceError):
        parse(table_text().replace(old, new))


def test_duplicate_asset_fact_rejected():
    import re

    text = table_text()
    row = next(
        m.group()
        for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.DOTALL)
        if "'defref_us-gaap_Assets'" in m.group()
    )
    with pytest.raises(ProvenanceError):
        parse(text.replace(row, row + row))


def test_broken_asset_identity_rejected():
    text = table_text()
    program = parse(text)
    fact = next(f for f in program.facts if f.role == "assets")
    text = text[: fact.char_start] + "450,257" + text[fact.char_end :]
    with pytest.raises(ProvenanceError):
        parse(text)


def breakdown_text():
    return gzip.decompress(
        (FIXTURES / "2024_breakdown_tables.html.gz").read_bytes()
    ).decode()


def parse_breakdown(text):
    return parse_issuer_ir_rendered_metrics(
        text,
        report_date="2024-12-31",
        issuer_cik="0001652044",
        metric_profile="alphabet.asset-revenue-breakdown.v1",
    )


def test_breakdown_requires_both_revenue_identities_and_exact_spans():
    text = breakdown_text()
    program = parse_breakdown(text)
    values = {f.role: f.numeric_value for f in program.facts}
    assert (
        sum(v for k, v in values.items() if k.startswith("category_"))
        == values["revenue"]
    )
    assert (
        sum(v for k, v in values.items() if k.startswith("geo_")) == values["revenue"]
    )
    assert values["category_hedging"] == values["geo_hedging"] == 211
    assert len(program.facts) == 13
    for f in program.facts:
        assert text[f.char_start : f.char_end] == f.evidence_quote
    # No field added to the old profile and no legacy signed fact set changes.
    assert len(parse(text).facts) == 4


def test_unknown_profile_rejected():
    with pytest.raises(ProvenanceError):
        parse_issuer_ir_rendered_metrics(
            table_text(),
            report_date="2024-12-31",
            issuer_cik="0001652044",
            metric_profile="unchecked",
        )


def test_breakdown_hedging_corruption_rejected():
    text = breakdown_text()
    fact = next(f for f in parse_breakdown(text).facts if f.role == "category_hedging")
    with pytest.raises(ProvenanceError):
        parse_breakdown(text[: fact.char_start] + "212" + text[fact.char_end :])


def test_extra_table_background_is_excluded_from_lower_bands():
    from longworld.core.financehistory import (
        FinancialFiling,
        FinancialSourceRow,
        _candidate_rows,
    )
    from longworld.core.issuerfilingworkflow import ALPHABET_BREAKDOWN_SECTIONS

    filing_id = "issuer-ir:0001652044:2024-12-31"

    def row(section):
        return FinancialSourceRow(
            record_id=section,
            filing_record_id=filing_id,
            report_date="2024-12-31",
            source_url="https://abc.xyz/",
            source_sha256="0" * 64,
            section=section,
            source_char_start=0,
            source_char_end=1,
            source_text="x",
            facts=(),
        )

    rows = (row("CONSOLIDATED BALANCE SHEETS"), row(ALPHABET_BREAKDOWN_SECTIONS[0]))
    filing = FinancialFiling(
        record_id=filing_id,
        filing_date="2025-02-05",
        report_date="2024-12-31",
        source_url="https://abc.xyz/",
        source_sha256="0" * 64,
        rows=rows,
    )
    assert _candidate_rows([filing], {filing_id}, set(), {"assets"}) == [rows[0]]
    assert (
        len(
            _candidate_rows(
                [filing], {filing_id}, set(), {"assets", "category_google_cloud"}
            )
        )
        == 2
    )


@pytest.mark.parametrize(
    "title",
    [
        "CONSOLIDATED STATEMENTS OF INCOME",
        "CONSOLIDATED STATEMENTS OF CASH FLOWS",
        "Revenues - Revenue by Segment (Details)",
        "Revenues - Revenue by Geographic Location (Details)",
    ],
)
def test_quarterly_duration_cannot_supply_annual_operands(title):
    from longworld.core.issuerfilingworkflow import _statement_table

    text = breakdown_text()
    start, end = _statement_table(text, title)
    text = (
        text[:start]
        + text[start:end].replace("12 Months Ended", "3 Months Ended")
        + text[end:]
    )
    with pytest.raises(ProvenanceError):
        parse_breakdown(text)


def test_visible_member_label_cannot_override_xbrl_dimension():
    text = breakdown_text().replace(
        "StatementBusinessSegmentsAxis=goog_GoogleCloudMember",
        "StatementBusinessSegmentsAxis=goog_GoogleServicesMember",
    )
    with pytest.raises(ProvenanceError):
        parse_breakdown(text)


@pytest.mark.parametrize("role", ["category_hedging", "geo_hedging"])
def test_hedge_role_cannot_replay_generic_revenue_even_after_rehash(role):
    import hashlib
    import json

    from longworld.core.financehistory import replay_financial_history

    task = json.loads(gzip.decompress((FIXTURES / "128k_parent.json.gz").read_bytes()))
    assert replay_financial_history(task)["answer"] == task["answer"]
    records = [json.loads(line) for line in task["context"].splitlines()]
    target = next(
        r for r in records if any(f["role"] == role for f in r.get("facts", []))
    )
    old = target["source_text"]
    changed = old.replace(
        "defref_us-gaap_GainLossOnOilAndGasHedgingActivity",
        "defref_us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
    ).replace(">Hedging gains (losses)</a>", ">Total revenues</a>")
    assert old != changed
    delta = len(changed) - len(old)
    for fact in target["facts"]:
        fact["relative_start"] += delta
    target["source_text"] = changed
    target["source_char_end"] += delta
    target["source_text_sha256"] = hashlib.sha256(changed.encode()).hexdigest()
    task["context"] = "\n".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for r in records
    )
    task["context_sha256"] = hashlib.sha256(task["context"].encode()).hexdigest()
    assert replay_financial_history(task)["answer"] == "unknown"
