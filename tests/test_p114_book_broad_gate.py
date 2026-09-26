"""High-recall veto for later ambiguous named speech after a candidate gold."""

from __future__ import annotations

from scripts.p114_book_broad_gate import later_named_quote_suspicions


def test_reynolds_adverb_between_name_and_verb_vetoes_old_gold() -> None:
    text = (
        '"And were you fearful then?" Reynolds replied.\n'
        '"No, certainly not," Reynolds emphatically replied.\n'
        '"How does he generally punish a thief?" Reynolds smilingly asked as they walked.\n'
    )
    after_gold = text.index("And were you fearful then?") + len(
        "And were you fearful then?"
    )
    found = later_named_quote_suspicions(text, "Reynolds", after_gold)
    assert [item["quote"] for item in found] == [
        "No, certainly not,",
        "How does he generally punish a thief?",
    ]


def test_both_orderings_and_preposed_modifier_are_vetoed() -> None:
    text = (
        '"First observation," Michael said.\n'
        '"Later observation," Michael said bitterly.\n'
        '"Another observation," said quietly Michael.\n'
        'Penelope whispered softly, "Final observation?"\n'
    )
    found_michael = later_named_quote_suspicions(
        text, "Michael", text.index("First observation,") + 2
    )
    assert {item["quote"] for item in found_michael} == {
        "Later observation,",
        "Another observation,",
    }
    found_penelope = later_named_quote_suspicions(text, "Penelope", 0)
    assert any(item["quote"] == "Final observation?" for item in found_penelope)


def test_name_inside_quote_or_next_sentence_does_not_bridge_tag() -> None:
    text = (
        '"Ralph said the old rule was wrong," she whispered.\n'
        '"I agree," she said. "Ralph is still here."\n'
    )
    assert later_named_quote_suspicions(text, "Ralph", 0) == []


def test_single_line_quote_with_multiline_printed_tag_is_vetoed() -> None:
    text = (
        "“First answer,” Jane cried.\n"
        "“I am certainly the most fortunate creature that ever existed!” cried\nJane.\n"
        "“First answer,” Michael said.\n"
        "“He felt so strongly the unwisdom of marriage, didn’t he?” Michael\nsaid.\n"
    )
    assert any(
        item["quote"] == "I am certainly the most fortunate creature that ever existed!"
        for item in later_named_quote_suspicions(text, "Jane", 20)
    )
    assert any(
        item["quote"] == "He felt so strongly the unwisdom of marriage, didn’t he?"
        for item in later_named_quote_suspicions(text, "Michael", 20)
    )
