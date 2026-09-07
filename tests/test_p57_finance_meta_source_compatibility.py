"""Meta issuer-linked statement rows stay fail closed."""

import gzip
import json
from pathlib import Path

import pytest

from longworld.core.issuerfilingworkflow import (
    META_CIK,
    build_issuer_ir_filing_manifest,
    parse_issuer_ir_rendered_metrics,
)
from longworld.core.provenance import ProvenanceError

FIXTURES = Path("tests/fixtures/p57_finance_meta")
INVENTORY = Path("data/source_inventory/p57_finance_meta_ir_fy2022_2025_v1")


def table_text():
    return gzip.decompress(
        (FIXTURES / "2025_primary_tables.html.gz").read_bytes()
    ).decode()


def parse(text, *, report_date="2025-12-31"):
    return parse_issuer_ir_rendered_metrics(
        text,
        report_date=report_date,
        issuer_cik=META_CIK,
    )


def test_real_meta_core_identity():
    text = table_text()
    program = parse(text)
    values = {fact.role: fact.numeric_value for fact in program.facts}
    assert values == {
        "revenue": 200966,
        "assets": 366021,
        "liabilities_and_equity": 366021,
        "cash_from_operations": 115800,
        "category_family_of_apps": 198759,
        "category_reality_labs": 2207,
    }
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (META_CIK, "0001018724"),
        ("Dec. 31, 2025", "Dec. 31, 2024"),
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
    text = text[: fact.char_start] + "366,022" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="balance-sheet identity"):
        parse(text)


def test_broken_segment_sum_rejected():
    text = table_text()
    program = parse(text)
    fact = next(
        item for item in program.facts if item.role == "category_reality_labs"
    )
    text = text[: fact.char_start] + "2,208" + text[fact.char_end :]
    with pytest.raises(ProvenanceError, match="segment revenue identity"):
        parse(text)


@pytest.mark.parametrize(
    ("filing_id", "report_date"),
    (
        ("meta-fy2025-annual", "2025-12-31"),
        ("meta-fy2024-annual", "2024-12-31"),
        ("meta-fy2023-annual", "2023-12-31"),
        ("meta-fy2022-annual", "2022-12-31"),
    ),
)
def test_downloaded_meta_years_keep_balance_identity(filing_id, report_date):
    path = INVENTORY / f"{filing_id}.rendered_xbrl_html.html"
    if not path.is_file():
        pytest.skip("Meta source inventory is not present")
    program = parse(path.read_text(encoding="utf-8"), report_date=report_date)
    values = {fact.role: fact.numeric_value for fact in program.facts}
    assert values["assets"] == values["liabilities_and_equity"]
    assert (
        values["category_family_of_apps"] + values["category_reality_labs"]
        == values["revenue"]
    )
    assert set(values) == {
        "revenue",
        "assets",
        "liabilities_and_equity",
        "cash_from_operations",
        "category_family_of_apps",
        "category_reality_labs",
    }


def test_real_meta_inventory_compiles_atmeta_control_prefix():
    inventory_path = INVENTORY / "issuer_ir_inventory.json"
    if not inventory_path.is_file():
        pytest.skip("Meta source inventory is not present")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    manifest = build_issuer_ir_filing_manifest(
        inventory,
        INVENTORY,
        generated_at="2026-09-07T04:00:00Z",
    )
    assert manifest["n"] == 4
    assert len(manifest["relations"]) == 3
    assert all(
        record["source_url"].startswith(
            "https://d18rn0p25nwr6d.cloudfront.net/CIK-0001326801/"
        )
        for record in manifest["records"]
    )
    assert all(
        "sec.gov/Archives" not in record["source_url"]
        for record in manifest["records"]
    )
