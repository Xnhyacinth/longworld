"""Visible same-file TeX links need both the reference and its target."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.p96_paper_caption_qa import render_files
from scripts.p127_same_file_paper_reference import (
    _word_boundary,
    resolve_same_file,
    run,
)


def _context(*, duplicate_target: bool = False, commented_ref: bool = False) -> str:
    reference = "\\ref{sec:target}"
    if commented_ref:
        reference = "% " + reference
    text = (
        "\\section{Detailed Results from the Evaluation Harness}\\label{sec:target}\n"
        "The experiments used two independent source conditions.\n"
        "Our final observations across all measured variants are discussed in "
        + reference
        + " before the closing remarks.\n"
    )
    if duplicate_target:
        text += "\\section{A Different Heading}\\label{sec:target}\n"
    return render_files({"paper.tex": text})


def test_same_file_reference_and_target_are_both_necessary() -> None:
    cue = "Our final observations across all measured variants are discussed in"
    context = _context()
    resolved = resolve_same_file(context, cue)
    assert resolved is not None
    assert resolved["answer"] == "Detailed Results from the Evaluation Harness"
    for span_name in ("reference_span", "target_span"):
        start, end = resolved[span_name]
        minus = context[:start] + "?" * (end - start) + context[end:]
        assert resolve_same_file(minus, cue) is None
    assert resolve_same_file(_context(duplicate_target=True), cue) is None
    assert resolve_same_file(_context(commented_ref=True), cue) is None


def test_label_or_cue_answer_shortcut_is_rejected() -> None:
    assert _word_boundary(
        "The distant comparison is discussed elsewhere",
        "sec:opaque",
        "Detailed Evaluation of Independent Sources",
        3,
    )
    assert not _word_boundary(
        "The distant comparison is discussed elsewhere",
        "sec:evaluation",
        "Detailed Evaluation of Independent Sources",
        3,
    )
    assert not _word_boundary(
        "The text points to details elsewhere in the document",
        "app:synledger",
        "Additional Results of Synthetic Benchmark Syn-Ledger",
        3,
    )
    for label, answer in (
        ("section:method comparisons", "Comparison with other methods"),
        ("app:benchmarks", "Details about the Evaluation Benchmark"),
        ("app:diff_geom", "Explicit differential-geometric setup"),
    ):
        assert not _word_boundary(
            "The further discussion is referenced in a distant source section",
            label,
            answer,
            3,
        )


def test_verify_rejects_compiler_drift_before_rebuilding(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "manifest.json").write_text(json.dumps({"compiler_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="compiler code changed"):
        run(tmp_path / "config.json", output, verify_only=True)
