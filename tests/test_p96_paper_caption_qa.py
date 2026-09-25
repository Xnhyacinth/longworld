"""Reader-visible paper cross-reference QA regressions."""

from __future__ import annotations

import pytest

from scripts.p96_paper_caption_qa import (
    discover,
    render_files,
    resolve,
    shortcut_reason,
)


def _files() -> dict[str, str]:
    return {
        "results.tex": (
            "\\section{Results}\n"
            "The controlled experiment describes a surprising behavior in Figure "
            "\\ref{fig:trial} that our model reproduces.\n"
        ),
        "figures.tex": (
            "\\begin{figure}\n\\caption{A controlled visual comparison of model behavior "
            "across three different tasks.}\n\\label{fig:trial}\n\\end{figure}\n"
        ),
    }


def test_cross_file_figure_caption_is_resolved_from_unique_result_cue() -> None:
    context = render_files(_files())
    candidates, reasons = discover(context)
    assert reasons["refs_examined"] == 1
    assert len(candidates) == 1
    result = resolve(context, candidates[0]["cue"])
    assert result is not None
    assert result["kind"] == "figure"
    assert result["caption"] == (
        "A controlled visual comparison of model behavior across three different tasks."
    )
    for span in (result["reference_span"], result["caption_span"]):
        masked = context[: span[0]] + "?" * (span[1] - span[0]) + context[span[1] :]
        assert resolve(masked, candidates[0]["cue"]) is None


def test_duplicate_target_label_or_commented_reference_is_not_admitted() -> None:
    files = _files()
    files["other.tex"] = files["figures.tex"]
    assert discover(render_files(files))[0] == []
    files = _files()
    files["results.tex"] = "% " + files["results.tex"].replace(
        "\\section{Results}\n", ""
    )
    assert discover(render_files(files))[0] == []


def test_cross_file_section_reference_returns_source_heading() -> None:
    context = render_files(
        {
            "experiments.tex": (
                "We compare the measured outcomes against the method described "
                "earlier in Section \\ref{sec:setup} for this experiment.\n"
            ),
            "method.tex": "\\section{Experimental Setup}\\label{sec:setup}\nDetails.\n",
        }
    )
    found, _ = discover(context)
    assert len(found) == 1
    result = resolve(context, found[0]["cue"])
    assert result is not None and result["caption"] == "Experimental Setup"
    assert result["kind"] == "section"


def test_duplicate_file_record_rejected() -> None:
    with pytest.raises(ValueError, match="repeated"):
        discover(render_files(_files()) + render_files(_files()))


@pytest.mark.parametrize(
    ("cue", "label", "answer", "kind", "reason"),
    [
        (
            "These Consistency Checks use medical time series data.",
            "subsec:consistency_checks",
            "Consistency Checks",
            "section",
            "answer_in_question_cue",
        ),
        (
            "We compare the results with earlier experiments.",
            "subsec:consistency_checks",
            "Consistency Checks",
            "section",
            "answer_encoded_in_label",
        ),
        (
            "We selected dropout using the cited method.",
            "sec:reg",
            "Regularization",
            "section",
            "one_word_heading_label_prefix",
        ),
        (
            "We include generated examples in the cited appendix.",
            "app:samples",
            "Text Samples",
            "section",
            "short_heading_label_keyword",
        ),
        (
            "The results complement the quantitative analysis elsewhere.",
            "subsec:vaes",
            "Challenging our Assumptions with Disentangled VAEs",
            "section",
            None,
        ),
    ],
)
def test_gold_shortcuts_are_filtered_without_rejecting_partial_topic_labels(
    cue: str, label: str, answer: str, kind: str, reason: str | None
) -> None:
    assert shortcut_reason(cue, label, answer, kind) == reason
