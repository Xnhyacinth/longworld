import pytest

from scripts.p113_report_text_audit import inspect_target, numeric_spans, redact


def _fixture():
    first = "=== Annual filing: filing:2021 ===\nReport date: 2021-12-31\nOperating income\t24,879\nComparison\t24,879\n"
    second = "=== Annual filing: filing:2022 ===\nReport date: 2022-12-31\nOperating income\t68,593\nPrior-year comparison\t24,879\n"
    context = first + second
    start = context.index("24,879")
    item = {
        "record_id": "filing:2021",
        "role": "operating_income",
        "quote": "24,879",
        "context_span": [start, start + 6],
    }
    return context, item


def test_proof_only_redaction_leaves_alternate_visible_numbers():
    context, item = _fixture()
    finding, context_minus = inspect_target(context, item)
    assert finding["numeric_surface_occurrences"] == 3
    assert finding["surviving_after_proof_only"] == 2
    assert finding["same_report_numeric_candidates"] == 1
    assert finding["other_report_numeric_candidates"] == 1
    assert "24,879" not in context_minus
    assert "68,593" in context_minus
    assert len(context_minus) == len(context)


def test_numeric_match_respects_grouping_boundaries():
    text = "1,234 234 1234 1,2345"
    assert [text[start:end] for start, end in numeric_spans(text, 234)] == ["234"]
    assert [text[start:end] for start, end in numeric_spans(text, 1234)] == [
        "1,234",
        "1234",
    ]
    assert redact("$ 1,234", [(2, 7)]) == "$ X,XXX"


def test_stale_evidence_quote_is_rejected():
    context, item = _fixture()
    with pytest.raises(ValueError, match="proof quote"):
        inspect_target(context, {**item, "quote": "24,878"})
