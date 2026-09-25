"""Reader-visible support must precede natural report task generation."""

from __future__ import annotations

import pytest

from scripts import p103_report_support_matrix as probe


def test_bare_note_reference_needs_a_visible_unique_heading_and_body():
    text = (
        "The inventory valuation discussion in this filing refers to Note 2 "
        "for the accounting treatment used during the year.\n"
        + "Intervening report text that does not answer the note question.\n" * 20
        + "Note 2 — Summary of Significant Accounting Policies\n"
        + "The company recognizes inventory at the lower of cost and net realizable value.\n"
    )
    result = probe.scan_visible(text)
    assert result["notes_with_unique_title_and_body"] == 1
    assert result["structurally_bound_bare_note_lines"] == 1
    assert result["note_reference_status"] == {"bound": 1}


def test_local_title_and_missing_target_are_rejected():
    text = (
        "For the loss contingencies, see Note 7 — Commitments and Contingencies "
        "in the audited financial statements.\n"
        "For the investment terms, refer to Note 9 of the annual filing for details.\n"
        "Note 7 — Commitments and Contingencies\n"
        "The company evaluates claims when a loss is probable and estimable.\n"
    )
    result = probe.scan_visible(text)
    assert result["structurally_bound_bare_note_lines"] == 0
    assert result["note_reference_status"]["answer_on_reference_line"] == 1
    assert result["note_reference_status"]["target_heading_absent"] == 1


def test_table_link_requires_complete_numeric_row_and_nonlocal_answer():
    text = (
        "\tDebt balance (Note 4)\t120\t145\n"
        "\tNote 4 — Borrowings\t\n"
        "Note 4 — Borrowings\n"
        "The company maintains revolving credit facilities with several lenders.\n"
    )
    result = probe.scan_visible(text)
    assert result["structurally_bound_table_note_rows"] == 1
    assert result["table_note_status"]["bound"] == 1
    assert result["table_note_status"]["no_complete_row_with_numeric_cell"] == 1


def test_item_navigation_is_not_rule_application():
    text = (
        "The risk discussion refers to Part II, Item 7 for a fuller statement of management analysis.\n"
        "Item 7. Management Discussion and Analysis\n"
        "Management explains the operating trends and the factors affecting revenue.\n"
    )
    result = probe.scan_visible(text)
    assert result["item_reference_status"] == {
        "visible_navigation_only_no_typed_rule": 1
    }


def test_source_pin_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="workspace-relative"):
        probe._pin({"path": "../outside.json", "sha256": "0" * 64})
