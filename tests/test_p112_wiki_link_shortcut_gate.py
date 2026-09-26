"""Reader-visible aliases must not masquerade as link-dependent evidence."""

import pytest

from scripts.p112_wiki_link_shortcut_gate import _shortcut, _target_body


def test_rejects_exact_and_prefix_title_aliases() -> None:
    context = (
        "=== DOCUMENT: American University of Cyprus (revision 1) ===\n"
        "motto: Scientia Potentia Est\n"
        "=== DOCUMENT: Airport Hotel metro station (revision 2) ===\n"
        "address: Main Street\n"
    )
    assert (
        _shortcut(
            context, "American University of Cyprus", "American University of Cyprus"
        )
        == "row_label_identifies_target_title"
    )
    assert (
        _shortcut(context, "Airport Hotel", "Airport Hotel metro station")
        == "row_label_identifies_target_title"
    )


def test_rejects_body_alias_but_accepts_a_distinct_link_name() -> None:
    context = (
        "=== DOCUMENT: Harilaq Fortress (revision 3) ===\nbuilt: Prehistoric times\n"
    )
    assert _shortcut(context, "Ariljača", "Harilaq Fortress") is None
    assert (
        _shortcut(
            context + "The Ariljača site is nearby.\n", "Ariljača", "Harilaq Fortress"
        )
        == "row_label_visible_in_target_body"
    )


def test_requires_a_unique_final_reader_target() -> None:
    with pytest.raises(ValueError, match="not unique"):
        _target_body("=== DOCUMENT: Other (revision 4) ===\nbody", "Missing")
