"""Adversarial behavioral checks for expanded explicit-speech truth."""

from __future__ import annotations

import pytest

from scripts.p113_book_tasks import _scan
from scripts.p113_book_truth import alias_uncertain, audit_speeches, chapters, speeches


def test_short_later_quote_and_reverse_speech_are_visible_to_both_parsers() -> None:
    text = (
        "“The silver key is missing, surely,” said Alice.\n"
        'Bob asked, "Where is the hidden door?"\n'
        "“No,” cried Alice.\n"
        "“Wait,” panted Alice.\n"
    )
    generated = speeches(text)
    audited = audit_speeches(text)
    assert [(x.quote, x.label, x.verb) for x in generated] == [
        ("The silver key is missing, surely,", "Alice", "said"),
        ("Where is the hidden door?", "Bob", "asked"),
        ("No,", "Alice", "cried"),
        ("Wait,", "Alice", "panted"),
    ]
    assert [(x.quote, x.label, x.verb) for x in audited] == [
        (x.quote, x.label, x.verb) for x in generated
    ]
    context = (
        "=== SECTION: Chapter I ===\n"
        "“The silver key is missing, surely,” said Alice.\n\n"
        "=== SECTION: Chapter II ===\n"
        "“The eastern gate is open tonight,” said Bob.\n"
        "“No,” cried Alice.\n"
        "“Wait,” panted Alice.\n"
    )
    assert _scan(
        context, "Chapter I", "Chapter II", "The silver key is missing, surely,"
    ) == ("Alice", "Wait,")


def test_speaker_alias_or_pronoun_is_not_certified() -> None:
    text = "“Please wait here,” said Holmes.\n“Now come in,” replied Sherlock Holmes.\n“Go,” said he."
    labels = {x.label for x in audit_speeches(text)}
    assert alias_uncertain("Holmes", labels)
    assert alias_uncertain("Sherlock Holmes", labels)
    assert "he" not in labels


def test_chapter_parser_rejects_duplicate_body_number() -> None:
    text = "".join(
        f"CHAPTER {number}. The Voyage\n" + "A" * 1100 + "\n"
        for number in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "I")
    )
    with pytest.raises(ValueError, match="fewer_than_eight_body_chapters"):
        chapters(text)
