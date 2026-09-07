"""Micron issuer-linked statement rows stay fail closed."""

import gzip
import json
from pathlib import Path

import pytest

from longworld.core.issuerfilingworkflow import (
    MICRON_CIK,
    build_issuer_ir_filing_manifest,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.provenance import ProvenanceError

FIXTURES = Path("tests/fixtures/p57_finance_micron")
INVENTORY = Path("data/source_inventory/p57_finance_micron_ir_fy2022_2025_v1")


def table_text():
    return gzip.decompress(
        (FIXTURES / "2025_primary_tables.html.gz").read_bytes()
    ).decode()


def parse(text, *, report_date="2025-08-28"):
    return parse_issuer_ir_rendered_metrics(
        text,
        report_date=report_date,
        issuer_cik=MICRON_CIK,
    )


def test_real_micron_core_technology_and_geo_identity():
    text = table_text()
    program = parse(text)
    values = {fact.role: fact.numeric_value for fact in program.facts}
    assert values == {
        "revenue": 37378,
        "assets": 82798,
        "liabilities_and_equity": 82798,
        "cash_from_operations": 17525,
        "category_dram": 28578,
        "category_nand": 8503,
        "category_other": 297,
        "geo_us": 24113,
        "geo_taiwan": 5672,
        "geo_mainland_china": 2639,
        "geo_other_asia_pacific": 1913,
        "geo_hong_kong": 1138,
        "geo_japan": 895,
        "geo_europe": 625,
        "geo_other": 383,
    }
    assert (
        values["category_dram"]
        + values["category_nand"]
        + values["category_other"]
        == values["revenue"]
    )
    assert (
        values["geo_us"]
        + values["geo_taiwan"]
        + values["geo_mainland_china"]
        + values["geo_other_asia_pacific"]
        + values["geo_hong_kong"]
        + values["geo_japan"]
        + values["geo_europe"]
        + values["geo_other"]
        == values["revenue"]
    )
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (MICRON_CIK, "0001018724"),
        ("Aug. 28, 2025", "Aug. 29, 2024"),
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
    text = text[: fact.char_start] + "82,799" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="balance-sheet identity"):
        parse(text)


def test_broken_technology_sum_rejected():
    text = table_text()
    program = parse(text)
    fact = next(item for item in program.facts if item.role == "category_nand")
    text = text[: fact.char_start] + "8,504" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="technology revenue identity"):
        parse(text)


def test_broken_geography_sum_rejected():
    text = table_text()
    program = parse(text)
    fact = next(item for item in program.facts if item.role == "geo_europe")
    text = text[: fact.char_start] + "626" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="geography revenue identity"):
        parse(text)


@pytest.mark.parametrize(
    ("filing_id", "report_date"),
    (
        ("micron-fy2025-annual", "2025-08-28"),
        ("micron-fy2024-annual", "2024-08-29"),
        ("micron-fy2023-annual", "2023-08-31"),
        ("micron-fy2022-annual", "2022-09-01"),
    ),
)
def test_downloaded_micron_years_keep_balance_and_mix_identity(filing_id, report_date):
    path = INVENTORY / f"{filing_id}.rendered_xbrl_html.html"
    if not path.is_file():
        pytest.skip("Micron source inventory is not present")
    program = parse(path.read_text(encoding="utf-8"), report_date=report_date)
    values = {fact.role: fact.numeric_value for fact in program.facts}
    assert values["assets"] == values["liabilities_and_equity"]
    assert (
        values["category_dram"]
        + values["category_nand"]
        + values["category_other"]
        == values["revenue"]
    )
    geo_total = sum(value for role, value in values.items() if role.startswith("geo_"))
    assert geo_total == values["revenue"]
    if report_date >= "2023-08-01":
        assert "geo_europe" in values
    else:
        assert "geo_europe" not in values


def test_real_micron_inventory_compiles_evergreen_control_prefix():
    inventory_path = INVENTORY / "issuer_ir_inventory.json"
    if not inventory_path.is_file():
        pytest.skip("Micron source inventory is not present")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    manifest = build_issuer_ir_filing_manifest(
        inventory,
        INVENTORY,
        generated_at="2026-09-07T06:30:00Z",
    )
    assert manifest["issuer"]["cik"] == MICRON_CIK
    assert manifest["issuer"]["detail_host"] == "investors.micron.com"
    assert all(
        record["source_url"].startswith(
            "https://d18rn0p25nwr6d.cloudfront.net/CIK-0000723125/"
        )
        for record in manifest["records"]
    )
