"""P105 admits only an exact, unique, macro-free visible target."""

from __future__ import annotations

import pytest

from scripts.p105_paper_quality_gate import _quality_status


def test_target_text_must_match_final_reader_bytes():
    context = "Heading\nwith line break"
    assert (
        _quality_status(context, "Heading with line break", (0, len(context)))
        == "rejected_gold_not_exact_visible_span"
    )
    assert (
        _quality_status(context, context, (0, len(context)))
        == "accepted_raw_tex_reference"
    )


def test_duplicate_or_unexpanded_target_is_rejected():
    context = "Heading\nHeading"
    assert (
        _quality_status(context, "Heading", (0, 7))
        == "rejected_alternate_visible_title_occurrence"
    )
    assert (
        _quality_status("\\method", "\\method", (0, 7))
        == "rejected_unexpanded_tex_command_in_gold"
    )
    with pytest.raises(ValueError, match="outside reader"):
        _quality_status("Heading", "Heading", (0, 20))
