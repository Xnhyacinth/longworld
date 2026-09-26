"""Behavioral checks for literal cross-section speech binding."""

from __future__ import annotations

from scripts.p112_book_tasks import (
    Section,
    _proposals,
    _reader_sections,
    sections,
    solve,
    speeches,
)


def test_cross_section_speaker_binding_and_intervention() -> None:
    context = (
        "=== SECTION: Chapter I ===\n"
        "“The silver key is missing, surely,” said Alice.\n"
        "=== SECTION: Chapter II ===\n"
        "“The brass key is here, surely,” said Bob.\n"
        "“The gate remains open tonight,” said Alice.\n"
        "“The second bell has just sounded,” said Bob.\n"
        "“The northern entrance is now sealed,” said Alice."
    )
    anchor = "The silver key is missing, surely,"
    assert (
        solve(context, "Chapter I", "Chapter II", anchor)
        == "The northern entrance is now sealed,"
    )
    assert (
        solve(
            context.replace("said Alice", "said Bob", 1),
            "Chapter I",
            "Chapter II",
            anchor,
        )
        == "The second bell has just sounded,"
    )
    assert (
        solve(
            context.replace("said Alice", "said ", 1), "Chapter I", "Chapter II", anchor
        )
        is None
    )
    without_alice_target = context.replace(
        "“The gate remains open tonight,” said Alice.", ""
    ).replace("“The northern entrance is now sealed,” said Alice.", "")
    assert solve(without_alice_target, "Chapter I", "Chapter II", anchor) is None


def test_ambiguous_anchor_cannot_be_solved() -> None:
    context = (
        "=== SECTION: Chapter I ===\n"
        "“The silver key is missing, surely,” said Alice.\n"
        "“The silver key is missing, surely,” said Bob.\n"
        "=== SECTION: Chapter II ===\n"
        "“The gate remains open tonight,” said Alice."
    )
    assert (
        solve(context, "Chapter I", "Chapter II", "The silver key is missing, surely,")
        is None
    )
    assert len(speeches(_reader_sections(context)["Chapter I"])) == 2


def test_chapter_parser_keeps_original_text() -> None:
    body = "".join(
        f"CHAPTER {roman}.\n" + "A" * 1001 + "\n"
        for roman in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")
    )
    parsed = sections(body)
    assert len(parsed) == 8
    assert parsed[0].title == "Chapter I"
    assert parsed[-1].text == body[parsed[-1].original_start :].rstrip()


def test_task_sampling_does_not_repeat_anchor_or_answer() -> None:
    parsed = [
        Section(
            "Chapter I",
            "“The little lantern was hidden yesterday,” said Alice. "
            "“The second lantern remains in the cupboard,” said Alice.",
            0,
        ),
        Section(
            "Chapter II",
            "“The eastern gate is still open tonight,” said Alice. "
            "“The western gate is now closed tonight,” said Bob.",
            100,
        ),
        Section(
            "Chapter III",
            "“The northern gate is still open tonight,” said Alice. "
            "“The southern gate is now closed tonight,” said Bob.",
            200,
        ),
    ]
    proposals, rejects = _proposals(parsed, max_tasks=10)
    assert len(proposals) == 3
    assert len({row["anchor"].quote for row in proposals}) == 3
    assert len({row["answer"].quote for row in proposals}) == 3
    assert rejects["duplicate_target_answer_across_tasks"] > 0
