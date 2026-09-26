from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.p112_report_route import _source_views
from scripts.p115_real_source_shape import (
    _number_visible,
    redact_all_numeric_support,
    visible_four,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/candidates/p96_finance_factorial_wide_batch_v1/amazon"


def source_views():
    return _source_views(SOURCE, json.loads((SOURCE / "manifest.json").read_text()))


def test_report_cells_bind_same_statement_unit_year_and_first_column():
    context, cells = source_views()["analyst_packet"]
    facts = visible_four(context, cells, "revenue")
    assert [fact["year"] for fact in facts] == ["2021", "2022", "2023", "2024"]
    assert [fact["value"] for fact in facts] == [469822, 513983, 574785, 637959]
    # A plausible year elsewhere in the long filing cannot rescue the table header.
    row = facts[-1]
    start = row["evidence"]["context_span"][0]
    unit = list(re.finditer(r"\$\s*in\s*millions", context[:start], re.IGNORECASE))[-1]
    own_header = context[unit.end() : start]
    changed_header = re.sub(r"2024", "2019", own_header, count=1)
    with pytest.raises(
        ValueError, match="same_statement_unit_and_own_year_header_absent"
    ):
        visible_four(
            context[: unit.end()] + changed_header + context[start:], cells, "revenue"
        )


def test_group_deletion_masks_every_exact_comparative_value():
    context, cells = source_views()["complete_statements"]
    facts = visible_four(context, cells, "revenue")
    target = facts[0]
    assert _number_visible(context, target["value"])
    masked, spans = redact_all_numeric_support(context, target["value"], 20)
    assert len(spans) >= 2  # own-year row and at least one later comparison
    assert not _number_visible(masked, target["value"])
    start, end = target["evidence"]["context_span"]
    assert "#" in masked[start:end]
    assert len(masked) == len(context)
