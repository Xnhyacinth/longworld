"""Tests for the P73 mutation/witness module (capability_mutations)."""

from __future__ import annotations

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_mutations as mut
from longworld.synthesis import capability_records as records


def _records_world(family: str, seed: int):
    return records.generate_world(seed, family, 200, 20, 2)


def _family_world(family: str, seed: int, k: int = 12, depth: int = 2):
    return families.generate_world(seed, family, 200, k, depth)


def test_mutants_run_and_something_distinguished_records():
    seen_distinguished = 0
    for family in ("filter_aggregate", "group_compare", "join_lookup"):
        for seed in (780001, 780002, 780003):
            world = _records_world(family, seed)
            for task in world["tasks"]:
                report = mut.witness_report(family, world["context"], task["question"])
                assert not report.get("unsupported"), (family, seed)
                for name, result in report["mutants"].items():
                    # three-way schema: every result carries all three flags
                    # plus the reason/error text when it has one
                    assert result["applicable"] in (True, False), (family, name)
                    assert result["valid_output"] in (True, False), (family, name)
                    assert "distinguished" in result, (family, name)
                    assert "semantic_distinguished" in result, (family, name)
                    if not result["applicable"]:
                        assert result["not_applicable_reason"], (family, name)
                seen_distinguished += report["distinguished_count"]
    assert seen_distinguished > 0


def test_mutants_run_and_something_distinguished_families():
    seen_distinguished = 0
    cases = [
        ("alias_locate", 780004, 12, 2),
        ("asof_state", 780005, 12, 2),
        ("rule_holdout", 780006, 12, 2),
        ("set_complete", 780007, 12, 1),
    ]
    for family, seed, k, depth in cases:
        for offset in range(3):
            world = _family_world(family, seed + offset, k, depth)
            for task in world["tasks"]:
                report = mut.witness_report(family, world["context"], task["question"])
                assert not report.get("unsupported"), (family, seed + offset)
                seen_distinguished += report["distinguished_count"]
    assert seen_distinguished > 0


def test_three_way_schema_erroring_mutant_not_semantically_distinguished():
    # wrong_rule_family on a world whose demos leave the other rule family
    # ambiguous: the shortcut program cannot produce a legal answer. Under
    # the P74 §0.2 split that is applicable + invalid output + NOT
    # semantically distinguished (the old rule counted it distinguished).
    saw_error_outcome = saw_distinction = False
    for offset in range(6):
        world = _family_world("rule_holdout", 780030 + offset, 12, 2)
        for task in world["tasks"]:
            report = mut.witness_report(
                "rule_holdout", world["context"], task["question"]
            )
            result = report["mutants"]["wrong_rule_family"]
            if not result["valid_output"]:
                saw_error_outcome = True
                assert "error" in result
                assert result["distinguished"] is False
                assert result["semantic_distinguished"] is False
                assert report["semantic_distinguished_count"] == sum(
                    1 for r in report["mutants"].values() if r["semantic_distinguished"]
                )
            else:
                # a real wrong-family answer differs from the correct one on
                # these worlds, but the assertion only guards the schema
                if result["semantic_distinguished"]:
                    saw_distinction = True
                    assert result["answer"] is not None
    assert saw_error_outcome, "expected at least one ambiguous-other-rule row"


def test_not_applicable_mutants_carry_reasons():
    # last_declaration: the spine enforces exactly one declaration per alias
    # handle, so the mutant is not defined on any spine world.
    world = _family_world("alias_locate", 780040, 12, 2)
    for task in world["tasks"]:
        report = mut.witness_report("alias_locate", world["context"], task["question"])
        result = report["mutants"]["last_declaration"]
        assert result["applicable"] is False
        assert result["valid_output"] is False
        assert result["semantic_distinguished"] is False
        assert "single declaration" in result["not_applicable_reason"]
    # relax_one_condition: a 2-condition scope leaves the bent program below
    # the solver's 2-to-4 gate; a 3+ condition scope stays applicable.
    saw_both = set()
    for offset in range(8):
        world = _family_world("set_complete", 780050 + offset, 12, 1)
        for task in world["tasks"]:
            report = mut.witness_report(
                "set_complete", world["context"], task["question"]
            )
            result = report["mutants"]["relax_one_condition"]
            conditions = task["question"]["steps"][0]["conditions"]
            if len(conditions) < 3:
                saw_both.add("n/a")
                assert result["applicable"] is False
                assert "2-to-4" in result["not_applicable_reason"]
            else:
                saw_both.add("applicable")
                assert result["applicable"] is True
    assert "n/a" in saw_both and "applicable" in saw_both


def test_latest_text_folds_in_reveal_order():
    # The real implementation: same solver, order="reveal" fold instead of
    # the correct program's event-date order. Not the same code path as
    # answer_of anymore; on worlds where the two orders coincide the mutant
    # is legitimately non-distinguishing.
    from longworld.synthesis import capability_families as fam_mod

    saw_distinction = saw_coincidence = False
    for offset in range(6):
        world = _family_world("asof_state", 780060 + offset, 12, 2)
        for task in world["tasks"]:
            report = mut.witness_report(
                "asof_state", world["context"], task["question"]
            )
            result = report["mutants"]["latest_text"]
            assert result["applicable"] is True
            assert result["valid_output"] is True
            header, rows = fam_mod.parse_context(world["context"])
            reveal = fam_mod._solve_rows(header, rows, task["question"], order="reveal")
            event = fam_mod._solve_rows(header, rows, task["question"], order="event")
            assert result["answer"] == reveal
            if reveal != event:
                saw_distinction = True
                assert result["semantic_distinguished"] is True
            else:
                saw_coincidence = True
                assert result["semantic_distinguished"] is False
    assert saw_distinction, "latest_text should distinguish on some worlds"
    # coincidence is allowed (not required) on small worlds


def test_answer_of_matches_solve_visible():
    for family in ("filter_aggregate", "group_compare", "join_lookup"):
        world = _records_world(family, 780010)
        for task in world["tasks"]:
            program = task["question"]
            assert mut.answer_of(
                family, world["context"], program
            ) == records.solve_visible(world["context"], program)
    for family in ("alias_locate", "asof_state", "rule_holdout", "set_complete"):
        world = _family_world(family, 780011, 12, 2 if family != "set_complete" else 1)
        for task in world["tasks"]:
            program = task["question"]
            assert mut.answer_of(
                family, world["context"], program
            ) == families.solve_visible(world["context"], program)


def test_unsupported_family_is_explicit():
    report = mut.witness_report("join_unanswerable", "", {})
    assert report == {"unsupported": True}
    assert mut.mutants_for("join_unanswerable") == []
    assert mut.mutants_for("dense_aggregate") == []


def test_witness_fraction_bounds():
    world = _records_world("filter_aggregate", 780012)
    report = mut.witness_report(
        "filter_aggregate", world["context"], world["tasks"][0]["question"]
    )
    n = len(mut.mutants_for("filter_aggregate"))
    assert 0 <= report["distinguished_count"] <= n
    assert 0.0 <= report["distinguished_fraction"] <= 1.0
    assert (
        report["distinguished_count"] / n == report["distinguished_fraction"]
        or abs(report["distinguished_count"] / n - report["distinguished_fraction"])
        < 1e-9
    )
    # the semantic headline is the same value under the new name
    assert report["semantic_distinguished_count"] == report["distinguished_count"]
    assert report["semantic_distinguished_fraction"] == report["distinguished_fraction"]
    # the denominator stays ALL mutants: dead or not-applicable mutants dilute
    assert (
        report["semantic_distinguished_count"]
        == sum(1 for r in report["mutants"].values() if r["semantic_distinguished"])
        <= n
    )
