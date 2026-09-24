"""P85 derives distinct prose changes from visible, nonduplicated records."""

from __future__ import annotations

import json

import pytest

from longworld.core.p66_researchlab_taskbank import render_records
from scripts import compile_p85_paper_revision_qa as compiler


def test_identical_copied_source_is_removed_but_conflict_fails() -> None:
    files, duplicates = compiler._canonical_files(
        {"chapter.tex": "one", "chapter copy/chapter.tex": "one"}
    )
    assert files == {"chapter.tex": "one"}
    assert duplicates == 1
    with pytest.raises(ValueError, match="disagree"):
        compiler._canonical_files(
            {"chapter.tex": "one", "chapter copy/chapter.tex": "two"}
        )


def test_visible_solver_needs_both_versions_of_both_files() -> None:
    old_main = (
        "The measured latent variable is generated from a Gaussian with mean z, "
        "and this paragraph explains why the posterior correction matters for inference."
    )
    new_main = old_main.replace("mean z", "mean x")
    old_appendix = (
        "In the detailed derivation, the generative density is called an encoder "
        "and the approximation is called a decoder under this formulation."
    )
    new_appendix = old_appendix.replace("called an encoder", "called a decoder")
    context, spans = render_records(
        "v4",
        "v5",
        [
            ("main.tex", old_main, new_main),
            ("appendix.tex", old_appendix, new_appendix),
        ],
    )
    selectors = {
        "main.tex": compiler._anchor(old_main),
        "appendix.tex": compiler._anchor(old_appendix),
    }
    expected = {
        "main.tex": {"v4": old_main, "v5": new_main},
        "appendix.tex": {"v4": old_appendix, "v5": new_appendix},
    }
    assert compiler.solve(context, selectors, "v4", "v5") == expected
    for start, end in spans.values():
        masked = context[:start] + "?" * (end - start) + context[end:]
        assert compiler.solve(masked, selectors, "v4", "v5") is None


def test_frozen_p85_candidate_has_far_unique_evidence_and_mask() -> None:
    output = compiler.ROOT / "data/candidates/p85_aevb_revision_qa_v3"
    if not (output / "manifest.json").is_file():
        pytest.skip("frozen P85 trial is not mounted")
    manifest = json.loads((output / "manifest.json").read_text())
    audit = json.loads((output / "audit.jsonl").read_text())
    assert manifest["independent_tasks"] == 1
    assert manifest["train_rows"] == 1
    assert manifest["intervention_checks"] == 8
    assert manifest["bounded_evidence_extent_tokens"] >= 8192
    assert len(audit["evidence_spans"]) == 4
    assert (
        len({(row["start_token"], row["end_token"]) for row in audit["evidence_spans"]})
        == 4
    )
    assert {row["kind"] for row in audit["interventions"]} == {"record", "line"}
    reader = json.loads((output / "train.jsonl").read_text())
    context = reader["messages"][0]["content"].split("\n\nQUESTION\n", 1)[0]
    assert compiler.solve(context, audit["selectors"], "v4", "v5") == audit["answer"]
    assert all(
        context.count(audit["answer"][span["path"]][span["version"]]) == 1
        for span in audit["evidence_spans"]
    )
    assert manifest["train_ready"] is False
