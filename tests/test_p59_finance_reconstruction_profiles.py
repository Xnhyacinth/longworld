"""New issuer reconstruction profiles preserve old source facts and bind real operands."""

import gzip
import re
from pathlib import Path

import pytest

from longworld.core.issuerfilingworkflow import parse_issuer_ir_rendered_metrics
from longworld.core.provenance import ProvenanceError

CASES = {
    "alphabet": (
        "0001652044",
        "2024-12-31",
        "alphabet.financial-reconstruction.v1",
        "alphabet.asset-revenue-breakdown.v1",
        "p55_finance_alphabet/2024_breakdown_tables.html.gz",
        {
            "operating_income": 112390,
            "cash_from_investing": -45536,
            "cash_from_financing": -79733,
            "cash_fx_effect": -612,
            "cash_period_change": -582,
        },
    ),
    "micron": (
        "0000723125",
        "2025-08-28",
        "micron.financial-reconstruction.v1",
        None,
        "p57_finance_micron/2025_primary_tables.html.gz",
        {
            "operating_income": 9770,
            "cash_from_investing": -14087,
            "cash_from_financing": -850,
            "cash_fx_effect": 6,
            "cash_period_change": 2594,
        },
    ),
    "nvidia": (
        "0001045810",
        "2025-01-26",
        "nvidia.cash-components-reconstruction.v1",
        "nvidia.income-market-segment.v1",
        "p57_finance_nvidia/2025_primary_tables.html.gz",
        {
            "operating_income": 81453,
            "cash_from_investing": -20421,
            "cash_from_financing": -42359,
            "cash_period_change": 1309,
        },
    ),
}


def source(issuer):
    return gzip.decompress(
        (Path("tests/fixtures") / CASES[issuer][4]).read_bytes()
    ).decode()


def parse(issuer, text, *, old=False):
    cik, date, profile, old_profile, _, _ = CASES[issuer]
    return parse_issuer_ir_rendered_metrics(
        text,
        report_date=date,
        issuer_cik=cik,
        metric_profile=old_profile if old else profile,
    )


@pytest.mark.parametrize("issuer", CASES)
def test_real_operands_and_old_facts_preserved(issuer):
    text = source(issuer)
    old = parse(issuer, text, old=True)
    new = parse(issuer, text)
    facts = {f.role: f for f in new.facts}
    expected = CASES[issuer][5]
    assert {role: facts[role].numeric_value for role in expected} == expected
    assert all(facts[f.role] == f for f in old.facts)
    assert len(new.facts) == len(old.facts) + len(expected)
    assert all(text[f.char_start : f.char_end] == f.evidence_quote for f in new.facts)
    if issuer == "nvidia":
        assert "cash_fx_effect" not in facts


@pytest.mark.parametrize(
    ("issuer", "role"),
    [
        (issuer, role)
        for issuer, case in CASES.items()
        for role in (
            "revenue",
            "assets",
            "liabilities_and_equity",
            "cash_from_operations",
            *case[5],
        )
    ],
)
def test_duplicate_required_operand_rejected(issuer, role):
    text = source(issuer)
    fact = next(f for f in parse(issuer, text).facts if f.role == role)
    row = next(
        r
        for r in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S)
        if r.start() <= fact.char_start < r.end()
    )
    with pytest.raises(ProvenanceError):
        parse(issuer, text[: row.end()] + row.group() + text[row.end() :])


@pytest.mark.parametrize("issuer", CASES)
def test_cash_identity_corruption_rejected(issuer):
    text = source(issuer)
    fact = next(f for f in parse(issuer, text).facts if f.role == "cash_period_change")
    with pytest.raises(ProvenanceError, match="cash"):
        parse(issuer, text[: fact.char_start] + "999999" + text[fact.char_end :])


@pytest.mark.parametrize("issuer", CASES)
@pytest.mark.parametrize(
    ("old", "new"),
    (("$ in Millions", "$ in Thousands"), ("12 Months Ended", "3 Months Ended")),
)
def test_units_and_annual_duration_not_relaxed(issuer, old, new):
    with pytest.raises(ProvenanceError):
        parse(issuer, source(issuer).replace(old, new))


def test_nvidia_cash_program_reports_values_and_executable_cf():
    import hashlib
    import json
    from longworld.core.financehistory import (
        FINANCIAL_HISTORY_SCHEMA,
        _CORE_ROLES,
        _context,
        replay_financial_history,
    )

    text = source("nvidia")
    program = parse("nvidia", text)
    program_id = "nvidia.cash_components_identity.v1"
    binding = {"fixture": "nvidia-fy2025-primary-tables"}
    records = [
        {
            "record_type": "financial_history_header",
            "schema_version": FINANCIAL_HISTORY_SCHEMA,
            "world_id": "nvidia-fixture",
            "source_binding": binding,
            "answer_program_id": program_id,
            "cik": "0001045810",
        },
        {"record_type": "filing", "source_record_id": "nvidia-2025"},
    ]
    for i, row in enumerate(re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S)):
        facts = [
            f
            for f in program.facts
            if f.role in _CORE_ROLES
            and row.start() <= f.char_start < f.char_end <= row.end()
        ]
        if not facts:
            continue
        records.append(
            {
                "record_type": "financial_source_row",
                "source_record_id": f"row-{i}",
                "filing_record_id": "nvidia-2025",
                "report_date": "2025-01-26",
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
    revenue = next(r for r in records[2:] if r["facts"][0]["role"] == "revenue")
    quote = revenue["facts"][0]["evidence_quote"]
    task = {
        "context": _context(records),
        "world_id": "nvidia-fixture",
        "source_binding": binding,
        "answer_program_id": program_id,
        "counterfactual_twin": {
            "record_id": revenue["source_record_id"],
            "role": "revenue",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_exact_span",
            "parent_value": quote,
            "value": quote.replace("130,497", "130,498"),
        },
    }
    replay = replay_financial_history(task)
    assert replay["answer"] != "unknown"
    answer = json.loads(replay["answer"])["annual_observations"][0]
    assert answer["cash_from_operations"] == 64089
    assert answer["cash_from_investing"] == -20421
    assert answer["cash_from_financing"] == -42359
    assert answer["cash_period_change"] == answer["cash_components_sum"] == 1309
    assert answer["operating_margin_basis_points"] == 81453 * 10000 // 130497
    assert "cash_fx_effect" not in answer
    cf = json.loads(replay_financial_history(task, counterfactual=True)["answer"])[
        "annual_observations"
    ][0]
    assert cf["revenue"] == 130498 and cf != answer
    ids = [r["source_record_id"] for r in records[2:]]
    for missing in ids:
        assert (
            replay_financial_history(
                task, evidence_ids=[i for i in ids if i != missing]
            )["answer"]
            == "unknown"
        )


def test_nvidia_program_gets_distinct_canonical_task_identity():
    from copy import deepcopy
    from longworld.core.financehistory import _ANSWER_PROGRAMS
    from longworld.core.promotion import PromotionError
    from longworld.core.taskpromotion import _canonical_task_identifiers
    from longworld.core.taskreplaysidecar import FINANCE_TASK_REPLAY_ADAPTER

    program_id = "nvidia.cash_components_identity.v1"
    program = _ANSWER_PROGRAMS[program_id]
    candidate = {
        "world_id": "nvidia-identity-fixture",
        "answer_program_id": program_id,
        "finance_task": {
            "answer_program_id": program_id,
            "query_type": program["query_type"],
            "answer_program_operations": list(program["operations"]),
        },
        "counterfactual_twin": {"provenance_operation": "replace_exact_span"},
    }
    ids = _canonical_task_identifiers(candidate, FINANCE_TASK_REPLAY_ADAPTER)
    assert ids["answer_program_id"] == program_id
    assert (
        ids["motif"]
        == "cash_components_sum+disclosed_net_change+operating_margin+balance_sheet_certification"
    )
    forged = deepcopy(candidate)
    forged["finance_task"]["answer_program_operations"] = list(
        _ANSWER_PROGRAMS["finance.multi_filing_reconstruction.v1"]["operations"]
    )
    with pytest.raises(PromotionError, match="finance answer program is unsupported"):
        _canonical_task_identifiers(forged, FINANCE_TASK_REPLAY_ADAPTER)
