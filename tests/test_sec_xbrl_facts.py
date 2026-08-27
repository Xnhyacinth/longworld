from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from longworld.core import secxbrl
from longworld.core.filingworkflow import (
    parse_sec_filing_components,
    validate_sec_filing_component,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.secxbrl import (
    SEC_NOTE13_MAX_CHARS,
    derive_sec_filing_subsection,
    parse_ixbrl_display_number,
    parse_sec_certification_facts,
    parse_sec_financial_program,
    parse_sec_ixbrl_facts,
    split_sec_filing_section_by_facts,
    validate_sec_certification_fact,
    validate_sec_filing_section,
    validate_sec_xbrl_fact,
)

SOURCE_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "sec_p5_apple_smoke"
)


@pytest.fixture(scope="module")
def local_submission() -> tuple[str, str]:
    manifest = json.loads(
        (SOURCE_DIRECTORY / "sec_filing_manifest.signed.json").read_text()
    )
    filing = manifest["filings"][0]
    source = (SOURCE_DIRECTORY / filing["source_file"]).read_text()
    return source, filing["source_sha256"]


def test_ixbrl_parser_reads_fy2025_sales_mix_and_balance_sheet(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    component = parse_sec_filing_components(source, parent_source_sha256)[0]
    facts = parse_sec_ixbrl_facts(source, component)

    by_id = {fact.fact_id: fact for fact in facts}
    assert by_id["f-72"].numeric_value == 307_003_000_000
    assert by_id["f-75"].numeric_value == 109_158_000_000
    assert by_id["f-78"].numeric_value == 416_161_000_000
    assert by_id["f-181"].numeric_value == 359_241_000_000
    assert by_id["f-201"].numeric_value == 285_508_000_000
    assert by_id["f-219"].numeric_value == 73_733_000_000
    assert by_id["f-72"].unit_ref == "usd"
    assert by_id["f-72"].decimals == "-6"
    assert by_id["f-72"].context_ref == "c-12"
    assert (
        by_id["f-72"].numeric_value + by_id["f-75"].numeric_value
        == by_id["f-78"].numeric_value
    )
    assert (
        by_id["f-201"].numeric_value + by_id["f-219"].numeric_value
        == by_id["f-181"].numeric_value
    )
    for fact in (by_id["f-72"], by_id["f-181"]):
        assert not hasattr(fact, "text")
        assert source[fact.char_start : fact.char_end] == fact.evidence_quote
        assert fact.parent_source_sha256 == parent_source_sha256
        validate_sec_xbrl_fact(source, fact)


def test_financial_role_duplicate_policy_fails_closed_on_conflicting_facts(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )
    fact = program.roles["product_revenue"]
    equivalent = replace(fact, fact_id="equivalent-display")
    conflicting = replace(
        fact,
        fact_id="conflicting-display",
        numeric_value=fact.numeric_value + 1_000_000,
    )

    assert secxbrl._one([fact, equivalent], "product revenue") == fact
    with pytest.raises(ProvenanceError, match="conflicting duplicate"):
        secxbrl._one([fact, conflicting], "product revenue")


def test_member_role_duplicate_policy_fails_closed_on_conflicting_facts(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )
    fact = program.roles["prior_geo_0"]
    equivalent = replace(fact, fact_id="equivalent-prior-geography")
    conflicting = replace(
        fact,
        fact_id="conflicting-prior-geography",
        numeric_value=fact.numeric_value + 1_000_000,
    )
    axis = "us-gaap:StatementBusinessSegmentsAxis"

    selected = secxbrl._one_by_member(
        [fact, equivalent], axis, "prior geographic revenue"
    )
    assert selected[fact.member(axis)] == fact
    with pytest.raises(ProvenanceError, match="conflicting duplicate"):
        secxbrl._one_by_member([fact, conflicting], axis, "prior geographic revenue")


def test_financial_program_binds_non_overlapping_answer_bearing_sections(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )

    assert [section.section_id for section in program.sections] == [
        "item8_operations",
        "item8_balance_sheet",
        "item8_cash_flow",
        "note2_revenue",
        "note7_income_taxes",
        "note8_leases",
        "note9_debt",
        "note13_segments",
        "ex_31_1",
        "ex_31_2",
        "ex_32_1",
    ]
    assert all(
        left.char_end <= right.char_start for left, right in pairwise(program.sections)
    )
    tokens = {
        section.section_id: math.ceil((section.char_end - section.char_start) / 4)
        for section in program.sections
    }
    core_64k = (
        "item8_operations",
        "item8_balance_sheet",
        "note2_revenue",
        "note13_segments",
        "ex_31_1",
        "ex_31_2",
        "ex_32_1",
    )
    extras_128k = (
        "item8_cash_flow",
        "note7_income_taxes",
        "note8_leases",
        "note9_debt",
    )
    assert 12_000 <= tokens["item8_operations"] <= 15_500
    assert (
        24_000
        <= tokens["item8_operations"]
        + tokens["note2_revenue"]
        + tokens["note13_segments"]
        <= 32_000
    )
    assert 40_000 <= sum(tokens[name] for name in core_64k) <= 52_000
    assert 60_000 <= sum(tokens[name] for name in extras_128k) <= 80_000
    assert 100_000 <= sum(tokens.values()) < 124_000
    assert program.roles["product_revenue"].numeric_value == 307_003_000_000
    assert program.roles["service_revenue"].numeric_value == 109_158_000_000
    assert program.roles["total_revenue"].numeric_value == 416_161_000_000
    assert program.roles["prior_total_revenue"].numeric_value == 391_035_000_000
    category = [program.roles[f"category_{index}"] for index in range(5)]
    geo = [program.roles[f"geo_{index}"] for index in range(5)]
    prior_geo = [program.roles[f"prior_geo_{index}"] for index in range(5)]
    assert sum(fact.numeric_value for fact in category) == 416_161_000_000
    assert sum(fact.numeric_value for fact in geo) == 416_161_000_000
    assert sum(fact.numeric_value for fact in prior_geo) == 391_035_000_000
    assert [fact.numeric_value for fact in geo] == [
        178_353_000_000,
        111_032_000_000,
        64_377_000_000,
        28_703_000_000,
        33_696_000_000,
    ]
    assert [fact.numeric_value for fact in prior_geo] == [
        167_045_000_000,
        101_328_000_000,
        66_952_000_000,
        25_052_000_000,
        30_658_000_000,
    ]
    assert [fact.member("us-gaap:StatementBusinessSegmentsAxis") for fact in geo] == [
        fact.member("us-gaap:StatementBusinessSegmentsAxis") for fact in prior_geo
    ]
    assert (
        program.roles["liabilities"].numeric_value
        + program.roles["equity"].numeric_value
        == program.roles["assets"].numeric_value
    )
    for section in program.sections:
        validate_sec_filing_section(source, section)
        assert source[section.char_start : section.char_end]
        assert not hasattr(section, "text")
    assert program.note13_max_chars == SEC_NOTE13_MAX_CHARS


def test_operations_section_splits_into_exhaustive_non_copying_fact_views(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )
    section = next(
        item for item in program.sections if item.section_id == "item8_operations"
    )
    facts = tuple(
        program.roles[role]
        for role in ("product_revenue", "service_revenue", "total_revenue")
    )

    slices = split_sec_filing_section_by_facts(source, section, facts)

    assert slices[0].char_start == section.char_start
    assert slices[-1].char_end == section.char_end
    assert all(left.char_end == right.char_start for left, right in pairwise(slices))
    assert sum(item.char_end - item.char_start for item in slices) == (
        section.char_end - section.char_start
    )
    assert len({item.section_sha256 for item in slices}) == 3
    for index, (item, target) in enumerate(zip(slices, facts, strict=True)):
        validate_sec_filing_section(source, item)
        assert item.char_start <= target.char_start < target.char_end <= item.char_end
        assert all(
            not (item.char_start <= other.char_start < other.char_end <= item.char_end)
            for other_index, other in enumerate(facts)
            if other_index != index
        )


def test_fact_corridor_subsection_is_hash_bound_without_copying(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )
    section = next(
        item for item in program.sections if item.section_id == "note2_revenue"
    )
    last_fact = program.roles["category_4"]

    corridor = derive_sec_filing_subsection(
        source,
        section,
        section_id="note2_revenue_current_mix",
        char_start=section.char_start,
        char_end=min(section.char_end, last_fact.char_end + 4_096),
    )

    validate_sec_filing_section(source, corridor)
    assert corridor.char_start == section.char_start
    assert last_fact.char_end <= corridor.char_end < section.char_end
    assert (
        source[corridor.char_start : corridor.char_end]
        in source[section.char_start : section.char_end]
    )
    assert corridor.provenance_id != section.provenance_id


def test_apple_128k_program_reconciles_unique_item8_identities(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )
    by_id = {section.section_id: section for section in program.sections}

    assert (
        "CONSOLIDATED STATEMENTS OF CASH FLOWS"
        in source[
            by_id["item8_cash_flow"].char_start : by_id["item8_cash_flow"].char_end
        ]
    )
    assert (
        ">Note 7 "
        in source[
            by_id["note7_income_taxes"].char_start : by_id[
                "note7_income_taxes"
            ].char_end
        ]
    )
    assert (
        ">Note 8 "
        in source[by_id["note8_leases"].char_start : by_id["note8_leases"].char_end]
    )
    assert (
        ">Note 9 "
        in source[by_id["note9_debt"].char_start : by_id["note9_debt"].char_end]
    )
    assert (
        program.roles["cfo"].numeric_value
        + program.roles["cfi"].numeric_value
        + program.roles["cff"].numeric_value
        == program.roles["delta_cash"].numeric_value
        == 5_991_000_000
    )
    assert program.roles["cfo"].numeric_value == 111_482_000_000
    assert program.roles["cfi"].numeric_value == 15_195_000_000
    assert program.roles["cff"].numeric_value == -120_686_000_000
    assert (
        program.roles["federal_tax"].numeric_value
        + program.roles["state_tax"].numeric_value
        + program.roles["foreign_tax"].numeric_value
        == program.roles["income_tax"].numeric_value
        == 20_719_000_000
    )
    assert (
        program.roles["lease_current"].numeric_value
        + program.roles["lease_noncurrent"].numeric_value
        == program.roles["lease_total"].numeric_value
        == 12_490_000_000
    )
    assert (
        program.roles["debt_current"].numeric_value
        + program.roles["debt_noncurrent"].numeric_value
        == program.roles["debt_total"].numeric_value
        == 90_678_000_000
    )
    for role in (
        "cfo",
        "cfi",
        "cff",
        "delta_cash",
        "federal_tax",
        "state_tax",
        "foreign_tax",
        "income_tax",
        "lease_current",
        "lease_noncurrent",
        "lease_total",
        "debt_current",
        "debt_noncurrent",
        "debt_total",
    ):
        fact = program.roles[role]
        assert source[fact.char_start : fact.char_end] == fact.evidence_quote
        validate_sec_xbrl_fact(source, fact)


def test_certification_parser_reads_exhibit_officer_names(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    components = {
        item.component_type: item
        for item in parse_sec_filing_components(source, parent_source_sha256)
    }
    names = {
        component_type: parse_sec_certification_facts(source, component)
        for component_type, component in components.items()
        if component_type.startswith("EX-")
    }

    assert [item.name for item in names["EX-31.1"]] == ["Timothy D. Cook"]
    assert [item.name for item in names["EX-31.2"]] == ["Kevan Parekh"]
    assert [item.name for item in names["EX-32.1"]] == [
        "Timothy D. Cook",
        "Kevan Parekh",
    ]
    assert [item.officer_title for item in names["EX-31.1"]] == [
        "Chief Executive Officer"
    ]
    assert [item.officer_title for item in names["EX-31.2"]] == [
        "Chief Financial Officer"
    ]
    assert [item.officer_title for item in names["EX-32.1"]] == [
        "Chief Executive Officer",
        "Chief Financial Officer",
    ]
    assert {item.certification_kind for item in names["EX-31.1"]} == {"section_302"}
    assert {item.certification_kind for item in names["EX-32.1"]} == {"section_906"}
    assert {item.covered_form for facts in names.values() for item in facts} == {
        "Form 10-K"
    }
    assert {item.certification_date for facts in names.values() for item in facts} == {
        "2025-10-31"
    }
    assert [item.covered_period_end for item in names["EX-31.1"]] == [""]
    assert {item.covered_period_end for item in names["EX-32.1"]} == {"2025-09-27"}
    for facts in names.values():
        for fact in facts:
            assert source[fact.char_start : fact.char_end] == fact.name
            assert (
                source[fact.officer_title_char_start : fact.officer_title_char_end]
                == fact.officer_title
            )
            validate_sec_certification_fact(source, fact)


def test_xbrl_and_section_validation_fail_closed_on_tampering(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2025-09-27"
    )
    fact = program.roles["product_revenue"]
    section = program.sections[0]

    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, char_start=fact.char_start + 1))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, numeric_value=1))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, parent_source_sha256="0" * 64))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, component_type="EX-31.1"))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, end_date="2024-09-28"))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, members=()))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, decimals="INF"))
    with pytest.raises(ProvenanceError):
        validate_sec_xbrl_fact(source, replace(fact, unit_ref="shares"))
    with pytest.raises(ProvenanceError):
        validate_sec_filing_section(source, replace(section, section_sha256="0" * 64))
    with pytest.raises(ProvenanceError):
        parse_sec_financial_program(
            source, parent_source_sha256, report_date="2024-09-28"
        )


def test_certification_validation_fails_closed_on_tampering(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    components = {
        item.component_type: item
        for item in parse_sec_filing_components(source, parent_source_sha256)
    }
    fact = parse_sec_certification_facts(source, components["EX-31.1"])[0]

    with pytest.raises(ProvenanceError):
        validate_sec_certification_fact(source, replace(fact, name="Timothy D. C00k"))
    with pytest.raises(ProvenanceError):
        validate_sec_certification_fact(source, replace(fact, component_type="EX-31.2"))
    with pytest.raises(ProvenanceError):
        validate_sec_certification_fact(
            source, replace(fact, component_sha256="0" * 64)
        )
    with pytest.raises(ProvenanceError):
        validate_sec_certification_fact(
            source, replace(fact, officer_title="Chief Financial Officer")
        )
    with pytest.raises(ProvenanceError):
        validate_sec_certification_fact(
            source, replace(fact, certification_date="2025-10-30")
        )
    with pytest.raises(ProvenanceError):
        validate_sec_certification_fact(
            source, replace(fact, provenance_id="derived-sha256:" + "0" * 64)
        )


def test_display_number_applies_scale_and_sign() -> None:
    assert parse_ixbrl_display_number("307,003", scale=6, sign="") == 307_003_000_000
    assert parse_ixbrl_display_number("14,264", scale=6, sign="-") == -14_264_000_000


def test_component_parser_still_covers_required_exhibits(
    local_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = local_submission
    components = parse_sec_filing_components(source, parent_source_sha256)
    for component in components:
        validate_sec_filing_component(source, component)
    assert hashlib.sha256(source.encode()).hexdigest() == parent_source_sha256


def test_financial_program_fails_closed_for_unprogrammed_issuer(
    local_submission: tuple[str, str],
) -> None:
    source, _parent = local_submission
    mutated = source.replace(
        "CENTRAL INDEX KEY:\t\t\t0000320193",
        "CENTRAL INDEX KEY:\t\t\t0000789019",
        1,
    )
    assert mutated != source
    digest = hashlib.sha256(mutated.encode()).hexdigest()
    with pytest.raises(ProvenanceError, match="not defined for this issuer"):
        parse_sec_financial_program(mutated, digest, report_date="2025-09-27")


AMAZON_SOURCE = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "sec_p7_amazon_v1"
    / "0001018724-25-000004.txt"
)


@pytest.fixture(scope="module")
def amazon_submission() -> tuple[str, str]:
    if not AMAZON_SOURCE.is_file():
        pytest.skip("Amazon 10-K inventory is not on this host")
    source = AMAZON_SOURCE.read_text()
    return source, hashlib.sha256(source.encode()).hexdigest()


def test_amazon_financial_program_uses_issuer_headings_not_apple(
    amazon_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = amazon_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2024-12-31"
    )

    assert [section.section_id for section in program.sections] == [
        "item8_cash_flow",
        "item8_operations",
        "item8_balance_sheet",
        "note4_leases",
        "note9_income_taxes",
        "note10_segment_oi",
        "note2_revenue",
        "note13_segments",
        "ex_31_1",
        "ex_31_2",
        "ex_32_1",
        "ex_32_2",
    ]
    assert program.n_category == 7
    assert program.n_geo == 5
    assert program.require_liabilities is False
    assert "liabilities" not in program.roles
    note2 = next(
        section for section in program.sections if section.section_id == "note2_revenue"
    )
    note13 = next(
        section
        for section in program.sections
        if section.section_id == "note13_segments"
    )
    assert ">Note 2 " not in source[note2.char_start : note2.char_end]
    assert (
        "DisaggregationOfRevenueTableTextBlock"
        in source[note2.char_start : note2.char_end]
    )
    assert ">Note 13 " not in source[note13.char_start : note13.char_end]
    assert program.roles["product_revenue"].numeric_value == 272_311_000_000
    assert program.roles["service_revenue"].numeric_value == 365_648_000_000
    assert program.roles["total_revenue"].numeric_value == 637_959_000_000
    category = [program.roles[f"category_{index}"] for index in range(7)]
    geo = [program.roles[f"geo_{index}"] for index in range(5)]
    assert sum(fact.numeric_value for fact in category) == 637_959_000_000
    assert sum(fact.numeric_value for fact in geo) == 637_959_000_000
    assert (
        program.roles["assets"].numeric_value
        == program.roles["liabilities_and_equity"].numeric_value
        == 624_894_000_000
    )
    assert program.roles["equity"].numeric_value == 285_970_000_000
    assert [item.name for item in program.certifications["ex_31_1"]] == [
        "Andrew R. Jassy"
    ]
    assert [item.name for item in program.certifications["ex_31_2"]] == [
        "Brian T. Olsavsky"
    ]
    assert [item.name for item in program.certifications["ex_32_1"]] == [
        "Andrew R. Jassy"
    ]
    assert [item.name for item in program.certifications["ex_32_2"]] == [
        "Brian T. Olsavsky"
    ]
    assert program.certifications["ex_31_1"][0].officer_title == (
        "Chief Executive Officer"
    )
    assert program.certifications["ex_31_2"][0].officer_title == (
        "Chief Financial Officer"
    )
    assert {
        item.certification_kind
        for key, facts in program.certifications.items()
        for item in facts
        if key.startswith("ex_32")
    } == {"section_906"}
    assert {
        item.covered_period_end
        for key, facts in program.certifications.items()
        for item in facts
        if key.startswith("ex_32")
    } == {"2024-12-31"}
    assert {
        item.certification_date
        for facts in program.certifications.values()
        for item in facts
    } == {"2025-02-06"}
    tokens = {
        section.section_id: math.ceil((section.char_end - section.char_start) / 4)
        for section in program.sections
    }
    core_64k = (
        "item8_operations",
        "item8_balance_sheet",
        "note2_revenue",
        "note13_segments",
        "ex_31_1",
        "ex_31_2",
        "ex_32_1",
        "ex_32_2",
    )
    extras_128k = (
        "item8_cash_flow",
        "note4_leases",
        "note9_income_taxes",
        "note10_segment_oi",
    )
    assert 12_000 <= tokens["item8_operations"] <= 16_000
    assert (
        24_000
        <= tokens["item8_operations"]
        + tokens["note2_revenue"]
        + tokens["note13_segments"]
        <= 28_000
    )
    assert 40_000 <= sum(tokens[name] for name in core_64k) <= 48_000
    assert 65_000 <= sum(tokens[name] for name in extras_128k) <= 85_000
    assert 100_000 <= sum(tokens.values()) < 124_000
    components = parse_sec_filing_components(source, parent_source_sha256)
    assert [item.component_type for item in components] == [
        "10-K",
        "EX-31.1",
        "EX-31.2",
        "EX-32.1",
        "EX-32.2",
    ]
    assert program.extra_128k_sections == (
        "item8_cash_flow",
        "note4_leases",
        "note9_income_taxes",
        "note10_segment_oi",
    )
    assert all(
        left.char_end <= right.char_start for left, right in pairwise(program.sections)
    )
    for section in program.sections:
        validate_sec_filing_section(source, section)
        ground = dict(program.section_ground)[section.section_id]
        assert ground in source[section.char_start : section.char_end]


def test_amazon_128k_program_reconciles_unique_item8_identities(
    amazon_submission: tuple[str, str],
) -> None:
    source, parent_source_sha256 = amazon_submission
    program = parse_sec_financial_program(
        source, parent_source_sha256, report_date="2024-12-31"
    )
    by_id = {section.section_id: section for section in program.sections}

    assert (
        "CONSOLIDATED STATEMENTS OF CASH FLOWS"
        in source[
            by_id["item8_cash_flow"].char_start : by_id["item8_cash_flow"].char_end
        ]
    )
    assert (
        ">Note 7 "
        not in source[
            by_id["note9_income_taxes"].char_start : by_id[
                "note9_income_taxes"
            ].char_end
        ]
    )
    assert (
        ">Note 8 "
        not in source[by_id["note4_leases"].char_start : by_id["note4_leases"].char_end]
    )
    assert (
        "IncomeTaxDisclosureTextBlock"
        in source[
            by_id["note9_income_taxes"].char_start : by_id[
                "note9_income_taxes"
            ].char_end
        ]
    )
    assert (
        "LesseeFinanceLeasesTextBlock"
        in source[by_id["note4_leases"].char_start : by_id["note4_leases"].char_end]
    )
    assert (
        "SegmentReportingDisclosureTextBlock"
        in source[
            by_id["note10_segment_oi"].char_start : by_id["note10_segment_oi"].char_end
        ]
    )
    assert "fx" in program.roles
    assert "debt_total" not in program.roles
    assert (
        program.roles["cfo"].numeric_value
        + program.roles["cfi"].numeric_value
        + program.roles["cff"].numeric_value
        != program.roles["delta_cash"].numeric_value
    )
    assert (
        program.roles["cfo"].numeric_value
        + program.roles["cfi"].numeric_value
        + program.roles["cff"].numeric_value
        + program.roles["fx"].numeric_value
        == program.roles["delta_cash"].numeric_value
        == 8_422_000_000
    )
    assert program.roles["cfo"].numeric_value == 115_877_000_000
    assert program.roles["cfi"].numeric_value == -94_342_000_000
    assert program.roles["cff"].numeric_value == -11_812_000_000
    assert program.roles["fx"].numeric_value == -1_301_000_000
    assert (
        program.roles["federal_tax"].numeric_value
        + program.roles["state_tax"].numeric_value
        + program.roles["foreign_tax"].numeric_value
        == program.roles["income_tax"].numeric_value
        == 9_265_000_000
    )
    assert (
        program.roles["lease_current"].numeric_value
        + program.roles["lease_noncurrent"].numeric_value
        == program.roles["lease_total"].numeric_value
        == 79_596_000_000
    )
    assert (
        program.roles["oi_0"].numeric_value
        + program.roles["oi_1"].numeric_value
        + program.roles["oi_2"].numeric_value
        == program.roles["oi_total"].numeric_value
        == 68_593_000_000
    )
    for role in (
        "cfo",
        "cfi",
        "cff",
        "fx",
        "delta_cash",
        "federal_tax",
        "state_tax",
        "foreign_tax",
        "income_tax",
        "lease_current",
        "lease_noncurrent",
        "lease_total",
        "oi_0",
        "oi_1",
        "oi_2",
        "oi_total",
    ):
        fact = program.roles[role]
        assert source[fact.char_start : fact.char_end] == fact.evidence_quote
        validate_sec_xbrl_fact(source, fact)
