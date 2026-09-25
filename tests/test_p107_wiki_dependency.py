"""Behavioral checks for P107 final-reader dependency replay."""

import pytest

from scripts.p107_wiki_dependency_audit import _blind_answer, _edited
from scripts.p107_wiki_dependency_batch import _require_target_before_remote


def _synthetic_reader():
    table = (
        "Name | Type | Note\n"
        "Alpha | Keep | a\n"
        "Beta | Other | b\n"
        "Gamma | Keep | c\n"
        "Delta | Other | d\n"
        "Epsilon | Third | e\n"
        "Zeta | Third | f\n"
        "Eta | Fourth | g\n"
        "Theta | Fourth | h\n"
    )
    remote = "# Remote\n## List\nName | Type | Note\nSelector | Keep | x\n"
    prefix = "## List\n"
    middle = "\n# Second document\n"
    context = prefix + table + middle + remote
    target_left = len(prefix)
    remote_left = len(prefix + table + middle)
    proof = {
        "remote_doc_reader_span": [remote_left, remote_left + len(remote)],
        "selector_name": "Selector",
        "selector_column": "Type",
        "target_table_span": [target_left, target_left + len(table)],
        "target_heading": "List",
        "target_column": "Type",
    }
    return context, proof


def test_target_edit_before_remote_document_preserves_selector_binding():
    context, proof = _synthetic_reader()
    category, baseline = _blind_answer(context, proof)
    assert category == "Keep"
    assert baseline == {"count": 2, "entries": ["Alpha", "Gamma"]}

    hit_left = context.index("Other | b")
    hit = _edited(
        context, [hit_left, hit_left + len("Other")], "Keep", proof, remote=False
    )
    assert hit[:2] == ("Keep", {"count": 3, "entries": ["Alpha", "Beta", "Gamma"]})

    control_left = context.index("Third | e")
    control = _edited(
        context,
        [control_left, control_left + len("Third")],
        "Fourth",
        proof,
        remote=False,
    )
    assert control[:2] == (category, baseline)


def test_remote_selector_edit_changes_downstream_set_and_control_does_not():
    context, proof = _synthetic_reader()
    value_left = context.index("Selector | Keep") + len("Selector | ")
    changed = _edited(
        context, [value_left, value_left + len("Keep")], "Other", proof, remote=True
    )
    assert changed[:2] == ("Other", {"count": 2, "entries": ["Beta", "Delta"]})

    control_left = context.index("Selector | Keep | x") + len("Selector | Keep | ")
    control = _edited(
        context, [control_left, control_left + 1], "y", proof, remote=True
    )
    assert control[:2] == ("Keep", {"count": 2, "entries": ["Alpha", "Gamma"]})


def test_remote_first_layout_is_rejected_until_offsets_are_supported():
    _require_target_before_remote([10, 20], 20)
    with pytest.raises(ValueError, match="target table before remote"):
        _require_target_before_remote([30, 60], 10)
