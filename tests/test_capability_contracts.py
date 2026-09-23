"""Tests for the P73 answer-contract module (capability_contracts)."""

from __future__ import annotations

import json
import re

from longworld.synthesis import capability_contracts as cc
from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records

# Row ids carry a non-hex prefix before the hex body; bare 'r' + hex in words
# like "record" is a false positive, so require a digit right after the prefix.
_ID = re.compile(r"(?:r|k|note-|unit-)[0-9a-f]{4,}")


def _row(family: str, seed: int = 790001):
    world = records.generate_world(seed, family, 200, 20, 2)
    task = world["tasks"][0]
    return family, world["context"], task["instruction"], task["answer"]


def _families():
    return ("filter_aggregate", "group_compare", "join_lookup")


def test_full_provenance_is_verbatim():
    for family in _families():
        _, _, _, answer = _row(family)
        assert cc.project_answer(family, answer, "full-provenance") == answer


def test_answer_only_records_families_drop_id_lists():
    for family in _families():
        _, ctx, instr, answer = _row(family)
        projected = cc.project_answer(family, answer, "answer-only")
        text = cc.render_answer(projected)
        assert "matched" not in text
        assert "records" not in text
        assert "pairs" not in text
        # ids are dropped EXCEPT the per-entity totals, whose keys are entity
        # names (unit-xxxxx) -- the asked-for answer, not provenance.
        if family != "join_lookup":
            assert not _ID.search(text), (family, text[:120])
        # the scalar projection is preserved (answer_value semantics)
        if family == "filter_aggregate":
            assert projected == {"aggregate": answer["aggregate"]}
        if family == "group_compare":
            assert projected["verdict"] == answer["verdict"]
            assert projected["groups"] == answer["groups"]
        if family == "join_lookup":
            assert projected["by_entity"] == answer["by_entity"]
            assert "pairs" not in projected


def test_instruction_swapped_for_answer_only():
    for family in _families():
        _, ctx, instr, _ = _row(family)
        swapped = cc.instruction_for_contract(family, instr, "answer-only")
        assert swapped != instr
        assert "ids" not in swapped
        # the task sentence and Return sentence are swapped; Fields/Program
        # (the task definition) survive verbatim.
        assert " Fields: " in swapped
        src_fields = instr[instr.find(" Fields: ") : instr.find(" Program: ")]
        assert src_fields in swapped
        src_program = instr[
            instr.find(" Program: ") : instr.find("Return one JSON object")
        ]
        assert src_program in swapped
        assert swapped.startswith("Task: ")
        assert swapped.endswith(".")


def test_user_message_keeps_context_under_answer_only():
    for family in _families():
        _, ctx, instr, _ = _row(family)
        user = ctx + "\n\nQUESTION\n" + instr
        out = cc.user_message_for_contract(family, user, "answer-only")
        assert out.startswith(ctx + "\n\nQUESTION\n")
        assert out != user
        tail = out.partition("\n\nQUESTION\n")[2]
        assert "matched" not in tail
        assert "Return one JSON object" in tail


def test_user_message_non_records_unchanged():
    for family in ("alias_locate", "asof_state", "rule_holdout"):
        world = families.generate_world(790002, family, 200, 20, 1, 1)
        task = world["tasks"][0]
        user = world["context"] + "\n\nQUESTION\n" + task["instruction"]
        for contract in cc.CONTRACTS:
            assert cc.user_message_for_contract(family, user, contract) == user


def test_rerender_rejects_non_records_layout():
    try:
        cc.instruction_for_contract("filter_aggregate", "no fields here", "answer-only")
    except ValueError:
        return
    raise AssertionError("malformed records instruction must fail loud")


def test_minimal_evidence_has_counts_not_lists():
    for family in _families():
        _, ctx, instr, answer = _row(family)
        ev = cc.minimal_evidence(family, answer)
        text = cc.render_answer(ev)
        # id LISTS become counts; entity-name keys stay (they are the answer)
        if family != "join_lookup":
            assert not _ID.search(text), (family, text[:120])
        if family == "join_lookup":
            assert all(isinstance(v, int) for v in ev["by_entity"].values())
        assert isinstance(ev, dict)


def test_join_unanswerable_passes_through():
    assert cc.project_answer("join_unanswerable", "UNKNOWN", "answer-only") == "UNKNOWN"
    assert cc.minimal_evidence("join_unanswerable", "UNKNOWN") == "UNKNOWN"


def test_unknown_family_fails_loud():
    try:
        cc.project_answer("nope", {}, "answer-only")
    except ValueError:
        return
    raise AssertionError("unknown family must fail loud")
