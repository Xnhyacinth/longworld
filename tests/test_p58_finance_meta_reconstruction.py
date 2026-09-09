"""Opt-in reconstruction binds annual source cash rows without changing asset gold."""

import gzip
from pathlib import Path

import pytest

from longworld.core.issuerfilingworkflow import (
    META_CIK,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.provenance import ProvenanceError

PROFILE = "meta.financial-reconstruction.v1"


def source():
    return gzip.decompress(
        Path("tests/fixtures/p57_finance_meta/2025_primary_tables.html.gz").read_bytes()
    ).decode()


def parse(text):
    return parse_issuer_ir_rendered_metrics(
        text, report_date="2025-12-31", issuer_cik=META_CIK, metric_profile=PROFILE
    )


def test_reconstruction_adds_exact_signed_cash_operands():
    text = source()
    program = parse(text)
    facts = {f.role: f for f in program.facts}
    assert {
        r: facts[r].numeric_value
        for r in (
            "operating_income",
            "cash_from_investing",
            "cash_from_financing",
            "cash_fx_effect",
            "cash_period_change",
        )
    } == {
        "operating_income": 83276,
        "cash_from_investing": -102003,
        "cash_from_financing": -20370,
        "cash_fx_effect": 235,
        "cash_period_change": -6338,
    }
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote
    old = parse_issuer_ir_rendered_metrics(
        text, report_date="2025-12-31", issuer_cik=META_CIK
    )
    assert len(old.facts) == 6
    assert all(facts[f.role] == f for f in old.facts)


def test_reconstruction_rejects_broken_cash_identity():
    text = source()
    fact = next(f for f in parse(text).facts if f.role == "cash_fx_effect")
    with pytest.raises(ProvenanceError, match="cash-flow identity"):
        parse(text[: fact.char_start] + "236" + text[fact.char_end :])


@pytest.mark.parametrize(
    "role",
    (
        "revenue",
        "assets",
        "liabilities_and_equity",
        "cash_from_operations",
        "operating_income",
        "cash_from_investing",
        "cash_from_financing",
        "cash_fx_effect",
        "cash_period_change",
    ),
)
def test_reconstruction_rejects_duplicate_numeric_operand(role):
    import re

    text = source()
    fact = next(f for f in parse(text).facts if f.role == role)
    row = next(
        r
        for r in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S)
        if r.start() <= fact.char_start < r.end()
    )
    with pytest.raises(ProvenanceError, match="ambiguous"):
        parse(text[: row.end()] + row.group() + text[row.end() :])


def test_reconstruction_source_rows_replay_through_shared_finance_adapter():
    import hashlib
    import json
    import re
    from longworld.core.financehistory import (
        FINANCIAL_HISTORY_SCHEMA,
        _context,
        replay_financial_history,
    )

    text = source()
    program = parse(text)
    binding = {"test_source": "real_meta_fixture"}
    records = [
        {
            "record_type": "financial_history_header",
            "schema_version": FINANCIAL_HISTORY_SCHEMA,
            "world_id": "meta-fixture",
            "source_binding": binding,
            "answer_program_id": "finance.multi_filing_reconstruction.v1",
        },
        {"record_type": "filing", "source_record_id": "meta-2025"},
    ]
    for i, row in enumerate(re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S)):
        facts = [
            f
            for f in program.facts
            if row.start() <= f.char_start < f.char_end <= row.end()
            and not f.role.startswith("category_")
        ]
        if not facts:
            continue
        records.append(
            {
                "record_type": "financial_source_row",
                "source_record_id": f"row-{i}",
                "filing_record_id": "meta-2025",
                "report_date": "2025-12-31",
                "source_text": row.group(),
                "source_text_sha256": hashlib.sha256(row.group().encode()).hexdigest(),
                "facts": [
                    {
                        "role": f.role,
                        "evidence_quote": f.evidence_quote,
                        "relative_start": f.char_start - row.start(),
                    }
                    for f in facts
                ],
            }
        )
    result = replay_financial_history(
        {
            "context": _context(records),
            "world_id": "meta-fixture",
            "source_binding": binding,
            "answer_program_id": "finance.multi_filing_reconstruction.v1",
        }
    )
    assert result["answer"] != "unknown"
    answer = json.loads(result["answer"])
    assert answer["annual_checks"][0]["cashflow_reconciled"] is True
