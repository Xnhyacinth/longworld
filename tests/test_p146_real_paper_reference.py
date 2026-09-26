"""Real TeX QA must bind a visible reference to a distinct target span."""

from __future__ import annotations

import json

from longworld.core.p66_researchlab_taskbank import load_text_tar
from longworld.synthesis.length_controller import get_tokenizer
from scripts import p146_real_paper_reference as paper
from scripts.p96_paper_caption_qa import render_files
from scripts.run_p86_frozen_paper_batch import _dedupe
from scripts.train_sft import tokenize_assistant_only

CONFIG = paper.ROOT / "configs/p146_real_paper_reference_v1.json"
OUTPUT = paper.ROOT / "data/candidates/p146_real_paper_reference_v6"


def _example() -> tuple[str, str]:
    cue = "The following independent assessment examines several distant experimental conditions"
    context = render_files(
        {
            "source.tex": cue + " in \\ref{sec:target}.\n",
            "target.tex": (
                "\\section{Detailed Evaluation of the Final Outcomes}\\label{sec:target}\n"
                "Independent measurements showed consistent gains across all six evaluation settings.\n"
            ),
        }
    )
    return context, cue


def test_cross_file_heading_and_opening_sentence_need_both_visible_spans() -> None:
    context, cue = _example()
    for mode, answer in (
        ("heading_or_caption", "Detailed Evaluation of the Final Outcomes"),
        (
            "section_opening",
            "Independent measurements showed consistent gains across all six evaluation settings.",
        ),
    ):
        resolved = paper.resolve(context, cue, mode)
        assert resolved is not None
        assert resolved["answer"] == answer
        assert resolved["source_path"] == "source.tex"
        assert resolved["target_path"] == "target.tex"
        for name in ("reference_span", "target_span"):
            start, end = resolved[name]
            minus = context[:start] + "?" * (end - start) + context[end:]
            assert paper.resolve(minus, cue, mode) is None


def test_cue_removes_tex_markup_without_exposing_label() -> None:
    context, cue = _example()
    text = context.split("<<< PAPER_SOURCE_FILE path=source.tex >>>\n", 1)[1]
    reference = paper.REF.search(text)
    assert reference is not None
    candidates = paper.candidate_cues(text, reference)
    assert any(item.startswith(cue) for item in candidates)
    assert all(
        "\\" not in item and "$" not in item and "~" not in item for item in candidates
    )
    assert paper.resolve(context, cue) is not None


def test_frozen_reader_source_gold_mask_and_distance_replay() -> None:
    summary = paper.run(CONFIG, OUTPUT, verify_only=True)
    assert summary["candidate_views"] == 9
    indexes = paper.rows(OUTPUT / "sample_index.jsonl")
    audits = paper.rows(OUTPUT / "audit.jsonl")
    readers = {
        split: paper.rows(OUTPUT / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    tokenizer = get_tokenizer()
    source_cache: dict[str, str] = {}
    seen = set()
    prior_splits: dict[str, set[str]] = {}
    prior_answers = set()
    for row in paper.rows(
        paper.ROOT / "data/candidates/p139_candidate_refs_probe_v1/candidate_refs.jsonl"
    ):
        prior = row["candidate"]
        prior_splits.setdefault(prior["source_group"], set()).add(prior["split"])
        if prior["source_kind"].startswith("real_paper"):
            prior_answers.add((prior["source_group"], prior["answer_sha256"]))
    for index, audit in zip(indexes, audits):
        assert index["sample_id"] == audit["sample_id"]
        reader = readers[index["split"]][index["row_index"]]
        user, answer = (row["content"] for row in reader["messages"])
        context, question = user.split(paper.SEPARATOR, 1)
        archive = audit["source_archive"]
        if archive["path"] not in source_cache:
            files, _ = _dedupe(load_text_tar(paper.pin(archive)))
            source_cache[archive["path"]] = render_files(files)
        assert context == source_cache[archive["path"]]
        assert context.count(audit["source_cue"]) == 1
        assert answer == audit["answer"]
        assert answer.casefold() not in question.casefold()
        assert audit["reference_label"] not in question
        assert context.casefold().count(answer.casefold()) == 1
        assert index["bounded_support_gap_tokens"] >= 12288
        assert index["last_evidence_to_query_tokens"] > 0
        assert (
            paper.resolve(context, audit["source_cue"], audit["answer_mode"])["answer"]
            == answer
        )
        for name in ("reference_span", "target_span"):
            start, end = audit["reader_char_spans"][
                "reference" if name == "reference_span" else "target"
            ]
            minus = context[:start] + "?" * (end - start) + context[end:]
            assert (
                paper.sha(minus.encode()) == audit["reader_context_minus_sha256"][name]
            )
            assert (
                paper.resolve(minus, audit["source_cue"], audit["answer_mode"]) is None
            )
        encoded = tokenize_assistant_only(tokenizer, reader["messages"], 262144)
        full = len(encoded["input_ids"])
        supervised = sum(label != -100 for label in encoded["labels"])
        assert (full, supervised, full - supervised) == (
            index["full_chat_tokens"],
            index["supervised_tokens"],
            index["input_tokens"],
        )
        identity = (index["source_group"], index["answer_sha256"])
        assert identity not in seen
        assert identity not in prior_answers
        assert prior_splits.get(index["source_group"], {index["split"]}) == {
            index["split"]
        }
        seen.add(identity)
    assert len(indexes) == len(audits) == 9
    assert {row["split"] for row in indexes} == {"train", "eval"}
    assert json.loads((OUTPUT / "manifest.json").read_text())["train_ready"] is False
