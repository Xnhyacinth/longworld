"""The P84 quality gate excludes formatting and repeated evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import filter_p84_researchlab_quality as quality


def _answer(path: str, old: str, new: str, status: str = "replaced") -> dict:
    return {"path": path, "status": status, "old_excerpt": old, "new_excerpt": new}


def test_quality_filter_requires_distinct_substantial_prose() -> None:
    old = "The inference method estimates the posterior distribution using a stochastic objective."
    new = "The inference algorithm estimates the posterior distribution using a corrected stochastic objective."
    valid = {"answer": [_answer("main.tex", old, new)]}
    assert quality.classify(valid) == ("prose_revision_review_candidate", 1)
    duplicate = {
        "answer": [
            _answer("main.tex", old, new),
            _answer("main copy/main.tex", old, new),
        ]
    }
    assert quality.classify(duplicate)[0] == "duplicate_answer_evidence"
    command = {
        "answer": [_answer("main.tex", "\\title{Old paper}", "\\title{New paper}")]
    }
    assert quality.classify(command)[0] == "latex_command_heading_or_short_excerpt"
    removal = {"answer": [_answer("main.tex", old, "", "removed")]}
    assert quality.classify(removal)[0] == "removed_or_added_file"


def test_frozen_aevb_raw_candidates_fail_quality_gate(tmp_path: Path) -> None:
    native = quality.ROOT / "data/candidates/p84_researchlab_aevb_native_v1"
    if not (native / "BUILD_RECEIPT.json").is_file():
        pytest.skip("frozen P84 native candidate bank is not mounted")
    result = quality.run(native, tmp_path / "quality")
    assert result["raw_native_tasks"] == 11
    assert result["rows_with_any_prose_replacement"] == 3
    assert result["quality_review_candidates"] == 0
    assert result["reasons"] == {
        "duplicate_answer_evidence": 3,
        "latex_command_heading_or_short_excerpt": 5,
        "removed_or_added_file": 3,
    }
