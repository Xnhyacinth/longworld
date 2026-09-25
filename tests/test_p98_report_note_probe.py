"""Behavioral checks for conservative annual-report Note feasibility screening."""

from scripts.p98_report_note_probe import scan_visible


def test_self_labeled_reference_is_shortcut_and_not_bare() -> None:
    text = (
        "The assets are reviewed annually. See Note 5 — Acquisitions and Goodwill.\n"
        "The same assets are reviewed annually. See Note 5 — Acquisitions and Goodwill.\n"
    )
    result = scan_visible(text)
    assert result["self_labeled_reference_lines"] == 2
    assert result["bare_reference_lines"] == 0
    assert result["exactly_two_distinct_reference_notes"] == ["5"]


def test_bare_reference_needs_visible_numbered_target() -> None:
    text = (
        "The contract treatment is described in more detail. See Note 7 for details.\n"
        "The legal exposure is explained in that disclosure. Refer to Note 7.\n"
    )
    unresolved = scan_visible(text)
    assert unresolved["bare_notes_without_numbered_heading"] == ["7"]
    assert unresolved["exactly_two_distinct_reference_notes"] == ["7"]
    resolved = scan_visible(text + "Note 7 — Commitments and Contingencies\n")
    assert resolved["bare_notes_without_numbered_heading"] == []


def test_repeated_rendered_paragraph_is_one_distinct_reference() -> None:
    line = "For the details of our tax contingencies, see Note 14 in the annual filing."
    result = scan_visible(line + "\n" + line + "\n")
    assert result["usable_reference_lines"] == 2
    assert result["distinct_reference_lines"] == 1
    assert result["exactly_two_distinct_reference_notes"] == []
