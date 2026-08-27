from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from longworld.core.secvisible import (
    SEC_VISIBLE_TEXT_REVISION,
    normalize_sec_visible_text,
    validate_sec_visible_text,
)

SOURCE_ROOT = Path(__file__).parents[1] / "data" / "source_inventory"
FILING_CASES = (
    ("sec_p5_apple_smoke", "f-72", "307,003"),
    ("sec_p7_amazon_v1", "f-146", "272,311"),
)


def _filing_source(directory: str) -> str:
    root = SOURCE_ROOT / directory
    manifest = json.loads((root / "sec_filing_manifest.signed.json").read_text())
    return (root / manifest["filings"][0]["source_file"]).read_text()


def _fact_table(source: str, fact_id: str, quote: str) -> tuple[int, int, int]:
    fact_tag = source.index(f'id="{fact_id}"')
    fact_start = source.index(">", fact_tag) + 1
    assert source.startswith(quote, fact_start)
    table_start = source.rfind("<table", 0, fact_start)
    table_end = source.index("</table>", fact_start) + len("</table>")
    assert table_start >= 0
    return table_start, table_end, fact_start


@pytest.mark.parametrize(("directory", "fact_id", "quote"), FILING_CASES)
def test_local_filing_fact_quote_maps_back_to_exact_source_range(
    directory: str, fact_id: str, quote: str
) -> None:
    source = _filing_source(directory)
    section_start, section_end, fact_start = _fact_table(source, fact_id, quote)

    visible = normalize_sec_visible_text(
        source, char_start=section_start, char_end=section_end
    )

    matching_ranges = [
        visible.source_ranges(index, index + len(quote))
        for index in range(len(visible.text))
        if visible.text.startswith(quote, index)
    ]
    assert ((fact_start, fact_start + len(quote)),) in matching_ranges
    validate_sec_visible_text(source, visible)


@pytest.mark.parametrize(("directory", "fact_id", "quote"), FILING_CASES)
def test_local_filing_normalization_is_deterministic_and_removes_markup(
    directory: str, fact_id: str, quote: str
) -> None:
    source = _filing_source(directory)
    section_start, section_end, _ = _fact_table(source, fact_id, quote)
    raw_section = source[section_start:section_end]

    first = normalize_sec_visible_text(
        source, char_start=section_start, char_end=section_end
    )
    second = normalize_sec_visible_text(
        source, char_start=section_start, char_end=section_end
    )

    markup_chars = sum(
        len(match.group()) for match in re.finditer(r"<[^>]+>", raw_section)
    )
    assert markup_chars > len(raw_section) // 2
    assert first == second
    assert first.revision == SEC_VISIBLE_TEXT_REVISION
    assert first.source_sha256 == hashlib.sha256(raw_section.encode()).hexdigest()
    assert first.text_sha256 == hashlib.sha256(first.text.encode()).hexdigest()
    assert quote in first.text
    assert "<td" not in first.text.lower()
    assert "</ix:" not in first.text.lower()
    assert first.text.count("<") * 20 < max(1, raw_section.count("<"))


def test_table_order_entities_and_hidden_content_are_preserved_or_excluded() -> None:
    source = (
        "<table><tr><th>Region</th><th>Revenue</th></tr>"
        '<tr><td>Americas</td><td><ix:nonFraction id="f-1">307,003</ix:nonFraction>'
        "&#160;million</td></tr></table>"
        '<div style="display:none">not visible</div>'
        "<ix:hidden>hidden XBRL fact</ix:hidden>"
    )

    visible = normalize_sec_visible_text(source)

    assert visible.text.splitlines() == [
        "Region\tRevenue",
        "Americas\t307,003 million",
    ]
    quote_start = visible.text.index("307,003")
    source_start = source.index("307,003")
    assert visible.source_ranges(quote_start, quote_start + 7) == (
        (source_start, source_start + 7),
    )
    space_start = quote_start + len("307,003")
    entity_start = source.index("&#160;")
    assert visible.source_ranges(space_start, space_start + 1) == (
        (entity_start, entity_start + len("&#160;")),
    )
    assert "not visible" not in visible.text
    assert "hidden XBRL fact" not in visible.text


def test_validation_rejects_a_changed_source_section() -> None:
    source = "<p>Revenue: <b>10</b></p>"
    visible = normalize_sec_visible_text(source)

    with pytest.raises(ValueError, match="source hash"):
        validate_sec_visible_text(source.replace("10", "11"), visible)


def test_exact_source_fact_maps_forward_to_visible_text() -> None:
    source = '<td><ix:nonFraction id="f-1">307,003</ix:nonFraction></td>'
    visible = normalize_sec_visible_text(source)
    source_start = source.index("307,003")

    ranges = visible.visible_ranges(source_start, source_start + len("307,003"))

    assert len(ranges) == 1
    visible_start, visible_end = ranges[0]
    assert visible.text[visible_start:visible_end] == "307,003"


def test_subsection_inherits_hidden_state_from_parent_context() -> None:
    source = '<div style="display:none">SECRET<span>nested</span></div><p>shown</p>'
    start = source.index("SECRET")
    end = source.index("</div>")

    visible = normalize_sec_visible_text(
        source,
        char_start=start,
        char_end=end,
        context_char_start=0,
        context_char_end=len(source),
    )

    assert visible.text == ""


def test_important_hidden_style_is_not_visible() -> None:
    source = '<div style="display: none !important">SECRET</div><p>shown</p>'

    assert normalize_sec_visible_text(source).text == "shown"
