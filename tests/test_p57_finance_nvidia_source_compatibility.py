"""NVIDIA issuer-linked statement rows stay fail closed."""

import gzip
from pathlib import Path

import pytest

from longworld.core.issuerfilingworkflow import (
    NVIDIA_MARKET_SEGMENT_PROFILE,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.provenance import ProvenanceError

FIXTURES = Path("tests/fixtures/p57_finance_nvidia")
INVENTORY = Path(
    "data/source_inventory/p57_finance_nvidia_ir_fy2022_2025_v1"
)


def table_text():
    return gzip.decompress(
        (FIXTURES / "2025_primary_tables.html.gz").read_bytes()
    ).decode()


def parse(text, *, report_date="2025-01-26", profile=NVIDIA_MARKET_SEGMENT_PROFILE):
    return parse_issuer_ir_rendered_metrics(
        text,
        report_date=report_date,
        issuer_cik="0001045810",
        metric_profile=profile,
    )


def test_real_nvidia_core_and_market_identity():
    text = table_text()
    program = parse(text)
    values = {fact.role: fact.numeric_value for fact in program.facts}
    assert values == {
        "revenue": 130497,
        "assets": 111601,
        "liabilities_and_equity": 111601,
        "cash_from_operations": 64089,
        "market_data_center": 115186,
        "market_gaming": 11350,
        "market_professional_visualization": 1878,
        "market_automotive": 1694,
        "market_oem_other": 389,
    }
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("0001045810", "0001018724"),
        ("Jan. 26, 2025", "Jan. 28, 2024"),
        ("$ in Millions", "$ in Thousands"),
    ),
)
def test_wrong_identity_period_or_unit_rejected(old, new):
    with pytest.raises(ProvenanceError):
        parse(table_text().replace(old, new))


def test_broken_balance_identity_rejected():
    text = table_text()
    program = parse(text)
    fact = next(item for item in program.facts if item.role == "assets")
    text = text[: fact.char_start] + "111,602" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="balance-sheet identity"):
        parse(text)


def test_broken_market_sum_rejected():
    text = table_text()
    program = parse(text)
    fact = next(item for item in program.facts if item.role == "market_gaming")
    text = text[: fact.char_start] + "11,351" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="market-segment revenue identity"):
        parse(text)


@pytest.mark.parametrize(
    ("filing_id", "report_date"),
    (
        ("nvidia-fy2025-annual", "2025-01-26"),
        ("nvidia-fy2024-annual", "2024-01-28"),
        ("nvidia-fy2023-annual", "2023-01-29"),
        ("nvidia-fy2022-annual", "2022-01-30"),
    ),
)
def test_downloaded_nvidia_years_keep_market_identity(filing_id, report_date):
    path = INVENTORY / f"{filing_id}.rendered_xbrl_html.html"
    if not path.is_file():
        pytest.skip("NVIDIA source inventory is not present")
    program = parse(path.read_text(encoding="utf-8"), report_date=report_date)
    values = {fact.role: fact.numeric_value for fact in program.facts}
    markets = [value for role, value in values.items() if role.startswith("market_")]
    assert values["assets"] == values["liabilities_and_equity"]
    assert sum(markets) == values["revenue"]
    assert set(values) >= {
        "revenue",
        "assets",
        "cash_from_operations",
        "market_data_center",
        "market_gaming",
    }


def test_downloaded_nvidia_years_show_data_center_gaming_crossover():
    inventory = INVENTORY
    years = (
        ("nvidia-fy2022-annual", "2022-01-30"),
        ("nvidia-fy2023-annual", "2023-01-29"),
        ("nvidia-fy2024-annual", "2024-01-28"),
        ("nvidia-fy2025-annual", "2025-01-26"),
    )
    observations = []
    for filing_id, report_date in years:
        path = inventory / f"{filing_id}.rendered_xbrl_html.html"
        if not path.is_file():
            pytest.skip("NVIDIA source inventory is not present")
        program = parse(path.read_text(encoding="utf-8"), report_date=report_date)
        values = {fact.role: fact.numeric_value for fact in program.facts}
        markets = [value for role, value in values.items() if role.startswith("market_")]
        market_facts = [fact for fact in program.facts if fact.role.startswith("market_")]
        span = path.read_text(encoding="utf-8")
        concepts = set()
        for fact in market_facts:
            window = span[max(0, fact.char_start - 500) : fact.char_end]
            if "RevenueFromContractWithCustomerExcludingAssessedTax" in window:
                concepts.add("RevenueFromContractWithCustomerExcludingAssessedTax")
            elif "defref_us-gaap_Revenues" in window:
                concepts.add("Revenues")
        assert len(concepts) == 1
        observations.append(
            {
                "report_date": report_date,
                "dc": values["market_data_center"],
                "gaming": values["market_gaming"],
                "mix": sum(markets) == values["revenue"],
                "concept": next(iter(concepts)),
            }
        )
    assert [item["dc"] > item["gaming"] for item in observations] == [
        False,
        True,
        True,
        True,
    ]
    assert all(item["mix"] for item in observations)
    assert observations[0]["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert {item["concept"] for item in observations[1:]} == {"Revenues"}
