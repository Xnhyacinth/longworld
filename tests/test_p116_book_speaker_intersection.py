"""Behavioral checks for complete printed-speaker intersections."""

from __future__ import annotations

from scripts.p113_book_truth import audit_speeches
from scripts.p116_book_speaker_intersection import (
    _blind_names,
    _broad_missing,
    _erase_label_support,
    _replace_one_label,
    _unparsed_possible_tag,
)


def test_intersection_requires_both_chapters_and_all_equivalent_supports() -> None:
    context = (
        "=== SECTION: Chapter I ===\n"
        "“Begin now,” said Alice.\n"
        "“Continue,” Alice replied.\n"
        "“Look away,” said Bob.\n\n"
        "=== SECTION: Chapter X ===\n"
        "“We begin,” said Alice.\n"
        "“We continue,” Alice answered.\n"
        "“Please wait,” said Carol.\n"
    )
    assert _blind_names(context, "Chapter I", "Chapter X") == ["Alice"]
    for chapter in ("Chapter I", "Chapter X"):
        removed = _erase_label_support(context, chapter, "Alice")
        assert _blind_names(removed, "Chapter I", "Chapter X") == []
    add_bob = _replace_one_label(context, "Chapter X", "Carol", "Bob")
    assert _blind_names(add_bob, "Chapter I", "Chapter X") == ["Alice", "Bob"]
    add_carol = _replace_one_label(context, "Chapter I", "Bob", "Carol")
    assert _blind_names(add_carol, "Chapter I", "Chapter X") == ["Alice", "Carol"]


def test_broad_gate_vetoes_modifier_tag_missed_by_precise_parsers() -> None:
    chapter = "CHAPTER I.\n“Wait,” Alice slowly said.\n"
    audited = audit_speeches(chapter)
    assert audited == []
    assert _broad_missing(chapter, "Alice", audited)
    assert _unparsed_possible_tag(chapter, audited)


def test_article_titled_speaker_is_vetoed_even_without_known_name() -> None:
    chapter = (
        "CHAPTER XXVI.\n"
        '"And this young man?" said the Prince.\n'
        '"Can you ride?" said the Prince.\n'
    )
    audited = audit_speeches(chapter)
    assert audited == []
    assert _unparsed_possible_tag(chapter, audited)


def test_unrelated_capital_after_pronoun_tag_is_not_a_named_speaker() -> None:
    chapter = (
        'CHAPTER I.\n"Wait," she said—"The fact was known."\n'
        '"Go," he replied, and The Road was long.\n'
    )
    assert not _unparsed_possible_tag(chapter, audit_speeches(chapter))
