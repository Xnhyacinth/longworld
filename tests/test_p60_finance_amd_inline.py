"""Issuer-host inline XBRL keeps context, namespace, scale, sign and span bindings."""

import gzip
import re
from pathlib import Path

import pytest

from longworld.core.issuerinlineworkflow import (
    AMD_INLINE_PROFILE,
    inline_row_value,
    normalize_inline_source,
    parse_issuer_inline_metrics,
)
from longworld.core.provenance import ProvenanceError


def source():
    raw = gzip.decompress(
        Path("tests/fixtures/p60_finance_amd/2024_inline.html.gz").read_bytes()
    )
    return normalize_inline_source(raw)[0].decode()


def parse(text):
    return parse_issuer_inline_metrics(
        text,
        report_date="2024-12-28",
        issuer_cik="0000002488",
        metric_profile=AMD_INLINE_PROFILE,
    )


def test_real_amd_annual_values_and_negative_source_spans():
    text = source()
    program = parse(text)
    values = {f.role: f.numeric_value for f in program.facts}
    assert values == {
        "revenue": 25785,
        "operating_income": 1900,
        "assets": 69226,
        "liabilities_and_equity": 69226,
        "cash_from_operations": 3041,
        "cash_from_investing": -1101,
        "cash_from_financing": -2062,
        "cash_period_change": -122,
    }
    for fact in program.facts:
        assert text[fact.char_start : fact.char_end] == fact.evidence_quote
        row = next(
            m
            for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.IGNORECASE | re.DOTALL)
            if m.start() <= fact.char_start < fact.char_end <= m.end()
        )
        assert (
            inline_row_value(
                row.group(),
                fact.char_start - row.start(),
                fact.evidence_quote,
                fact.role,
            )
            == fact.numeric_value
        )
    assert (
        text[program.report_date_char_start : program.report_date_char_end]
        == program.report_date_display
    )


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("0000002488", "0000050863"),
        ("2023-12-31", "2024-10-01"),
        ("iso4217:USD", "iso4217:EUR"),
        ("http://fasb.org/us-gaap/2024", "https://example.org/fake-us-gaap/2024"),
    ),
)
def test_identity_annual_unit_and_namespace_not_relaxed(old, new):
    with pytest.raises(ProvenanceError):
        parse(source().replace(old, new))


def test_dimensioned_annual_context_rejected():
    text = source()
    start = text.index('<xbrli:context id="c-1">')
    end = text.index("</xbrli:entity>", start)
    inserted = '<xbrli:segment><xbrldi:explicitmember dimension="us-gaap:StatementBusinessSegmentsAxis">amd:DataCenterMember</xbrldi:explicitmember></xbrli:segment>'
    with pytest.raises(ProvenanceError):
        parse(text[:end] + inserted + text[end:])


@pytest.mark.parametrize(
    "role",
    (
        "revenue",
        "operating_income",
        "assets",
        "liabilities_and_equity",
        "cash_from_operations",
        "cash_from_investing",
        "cash_from_financing",
        "cash_period_change",
    ),
)
def test_duplicate_required_numeric_fact_rejected(role):
    text = source()
    fact = next(f for f in parse(text).facts if f.role == role)
    row = next(
        m
        for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.IGNORECASE | re.DOTALL)
        if m.start() <= fact.char_start < fact.char_end <= m.end()
    )
    with pytest.raises(ProvenanceError):
        parse(text[: row.end()] + row.group() + text[row.end() :])


def test_changed_sign_and_scale_are_not_ignored():
    text = source()
    fact = next(f for f in parse(text).facts if f.role == "cash_from_investing")
    opening = text.rfind("<ix:nonfraction", 0, fact.char_start)
    attrs = text[opening : fact.char_start]
    assert 'sign="-"' in attrs and 'scale="6"' in attrs
    for changed in (
        attrs.replace('sign="-"', 'sign=""'),
        attrs.replace('scale="6"', 'scale="3"'),
    ):
        with pytest.raises(ProvenanceError):
            parse(text[:opening] + changed + text[fact.char_start :])


def test_generic_cash_program_has_its_own_canonical_identifier():
    from longworld.core.financehistory import _ANSWER_PROGRAMS
    from longworld.core.taskpromotion import _canonical_task_identifiers
    from longworld.core.taskreplaysidecar import FINANCE_TASK_REPLAY_ADAPTER

    def candidate(program_id):
        program = _ANSWER_PROGRAMS[program_id]
        return {
            "world_id": "amd-inline-identity-test",
            "answer_program_id": program_id,
            "finance_task": {
                "answer_program_id": program_id,
                "query_type": program["query_type"],
                "answer_program_operations": list(program["operations"]),
            },
        }

    generic = _canonical_task_identifiers(
        candidate("finance.cash_components_identity.v1"), FINANCE_TASK_REPLAY_ADAPTER
    )
    nvidia = _canonical_task_identifiers(
        candidate("nvidia.cash_components_identity.v1"), FINANCE_TASK_REPLAY_ADAPTER
    )
    assert generic["answer_program_id"] == "finance.cash_components_identity.v1"
    assert generic["semantic_base_task_id"] != nvidia["semantic_base_task_id"]
    assert generic["motif"] == nvidia["motif"]


def test_unrecognized_segment_content_is_not_treated_as_consolidated():
    text = source()
    start = text.index('<xbrli:context id="c-1">')
    end = text.index("</xbrli:entity>", start)
    inserted = '<xbrli:segment><other:typedMember xmlns:other="http://xbrl.org/2006/xbrldi" dimension="amd:DisclosureAxis"><amd:member>other</amd:member></other:typedMember></xbrli:segment>'
    with pytest.raises(ProvenanceError):
        parse(text[:end] + inserted + text[end:])


def test_malformed_numeric_grouping_is_not_silently_normalized():
    text = source()
    fact = next(f for f in parse(text).facts if f.role == "cash_from_investing")
    with pytest.raises(ProvenanceError):
        parse(text[: fact.char_start] + "1,,101" + text[fact.char_end :])


def test_transform_namespace_is_not_just_a_prefix_label():
    with pytest.raises(ProvenanceError):
        parse(
            source().replace(
                "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
                "https://example.org/fake-transform",
            )
        )


@pytest.mark.parametrize("nil_attribute", ("xsi:nil", "alternate:nil"))
def test_nil_numeric_fact_is_not_read_as_present_value(nil_attribute):
    text = source()
    fact = next(f for f in parse(text).facts if f.role == "cash_from_operations")
    opening = text.rfind("<ix:nonfraction", 0, fact.char_start)
    changed = text[:opening] + text[opening:].replace(
        "<ix:nonfraction", f'<ix:nonfraction {nil_attribute}="true"', 1
    )
    with pytest.raises(ProvenanceError):
        parse(changed)


def test_row_replay_rejects_unquoted_sign_instead_of_assuming_positive():
    text = source()
    fact = next(f for f in parse(text).facts if f.role == "cash_from_investing")
    row = next(
        m
        for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.IGNORECASE | re.DOTALL)
        if m.start() <= fact.char_start < fact.char_end <= m.end()
    )
    body = row.group()
    prefix = body[: fact.char_start - row.start()]
    changed_prefix = prefix.replace('sign="-"', "sign=-")
    assert changed_prefix != prefix
    changed = changed_prefix + body[len(prefix) :]
    with pytest.raises(ProvenanceError):
        inline_row_value(changed, len(changed_prefix), fact.evidence_quote, fact.role)
