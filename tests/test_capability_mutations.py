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


def test_wrong_rule_family_applies_other_family_formula():
    # W2-M redesign: the shortcut no longer re-infers the other family's rule
    # from the demos (which correctly errors on ambiguity, 80/84 on the pilot
    # bank). It APPLIES the other family's formula over the query entity's
    # features under the world's declared parameters -- a valid wrong
    # program, so it lands in the valid-output cell of the three-way schema,
    # carries the other rule family in its answer, and mostly differs.
    saw_both_families = set()
    saw_distinction = 0
    for offset in range(6):
        world = _family_world("rule_holdout", 780030 + offset, 12, 2)
        header, rows = families.parse_context(world["context"])
        declared = header["rule_family"]
        other = "parity_vote" if declared == "threshold_class" else "threshold_class"
        entities = families._rule_entities(rows)
        for task in world["tasks"]:
            report = mut.witness_report(
                "rule_holdout", world["context"], task["question"]
            )
            result = report["mutants"]["wrong_rule_family"]
            assert result["applicable"] is True
            assert result["valid_output"] is True, result
            answer = result["answer"]
            terminal = task["question"]["steps"][-1]
            if terminal["op"] == "label_list":
                # the bare-list shape carries no rule_family key; the labels
                # are checked directly against the formula below
                named = terminal["entities"]
                assert isinstance(answer, list) and len(answer) == len(named)
                label_map = dict(zip(named, answer))
            else:
                # the answer names the rule family it actually applied
                assert answer["rule_family"] == other
                saw_both_families.add(other)
                label_map = answer.get("labels", {})
            # the shortcut's labels are the other family's formula over the
            # entity features, recomputed independently here
            for entity, label in label_map.items():
                entity_rows = entities[entity]
                if other == "threshold_class":
                    value = (
                        sum(r.x for r in entity_rows) + sum(r.y for r in entity_rows)
                    ) % header["modulus"]
                    expected = header["labels"][0] if value < 1 else header["labels"][1]
                else:
                    counted = sum(
                        (r.x + r.y) % header["modulus"] == 0 for r in entity_rows
                    )
                    expected = header["labels"][counted % 2]
                assert label == expected, (entity, label, expected)
            if result["semantic_distinguished"]:
                saw_distinction += 1
    assert saw_distinction > 0, "wrong_rule_family should distinguish on some worlds"


def test_three_way_schema_erroring_mutant_not_semantically_distinguished(monkeypatch):
    # The schema rule "an erroring mutant is NOT semantically distinguished"
    # (P74 §0.2) outlives the W2-M redesign: wrong_rule_family no longer
    # errors naturally, so the error cell is exercised by breaking one
    # mutant deliberately and checking the accounting.
    world = _family_world("rule_holdout", 780031, 12, 2)
    task = world["tasks"][0]

    def boom(context, program, name):
        raise ValueError("boom: synthetic mutant failure")

    monkeypatch.setattr(mut, "_rule_mutant_answer", boom)
    report = mut.witness_report("rule_holdout", world["context"], task["question"])
    result = report["mutants"]["wrong_rule_family"]
    assert result["applicable"] is True
    assert result["valid_output"] is False
    assert "error" in result
    assert result["distinguished"] is False
    assert result["semantic_distinguished"] is False
    assert report["semantic_distinguished_count"] == sum(
        1 for r in report["mutants"].values() if r["semantic_distinguished"]
    )


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
    # tighten_one_condition (W2-M): applicable only where a member sits
    # exactly on the scope's numeric bound; both cells must appear across a
    # seed sweep, and the not-applicable reason names the missing fact.
    saw_tighten = set()
    for offset in range(8):
        world = _family_world("set_complete", 780050 + offset, 12, 1)
        for task in world["tasks"]:
            report = mut.witness_report(
                "set_complete", world["context"], task["question"]
            )
            result = report["mutants"]["tighten_one_condition"]
            if not result["applicable"]:
                saw_tighten.add("n/a")
                assert "numeric bound" in result["not_applicable_reason"]
                assert result["valid_output"] is False
                assert result["semantic_distinguished"] is False
            else:
                saw_tighten.add("applicable")
                assert result["valid_output"] is True
    assert "n/a" in saw_tighten and "applicable" in saw_tighten


def test_tighten_not_applicable_without_numeric_bound():
    # A scope of category/equality conditions only: the tightened program has
    # no bound to sharpen, so the mutant is not applicable with that reason.
    # Generated worlds always keep a numeric bound, so the fixture is built
    # by hand from the world's own row vocabulary.
    world = _family_world("set_complete", 780050, 12, 1)
    header, rows = families.parse_context(world["context"])
    categories = sorted({row.category for row in rows if row.type == "record"})
    record = next(row for row in rows if row.type == "record")
    other = next(c for c in categories if c != record.category)
    program = {
        "family": "set_complete",
        "steps": [
            {
                "op": "scope",
                "conditions": [
                    {"field": "category", "op": "==", "value": record.category},
                    {"field": "category", "op": "!=", "value": other},
                ],
            },
            {"op": "set_list", "shape": "ids"},
        ],
    }
    context = world["context"]
    report = mut.witness_report("set_complete", context, program)
    result = report["mutants"]["tighten_one_condition"]
    assert result["applicable"] is False
    assert "no numeric bound" in result["not_applicable_reason"]
    # relax on a 2-condition scope: the other not-applicable cell, unchanged
    assert report["mutants"]["relax_one_condition"]["applicable"] is False


def test_nearest_lexical_not_applicable_reason():
    # W2-M: when the lexically-nearest entity cannot produce the terminal's
    # required answer shape (single-match / empty-declare), the shortcut has
    # no legal answer -- an applicability fact, not an error and not a
    # distinction. Both outcomes appear across a seed sweep.
    saw = set()
    for offset in range(8):
        world = _family_world("alias_locate", 780060 + offset, 12, 2)
        for task in world["tasks"]:
            report = mut.witness_report(
                "alias_locate", world["context"], task["question"]
            )
            result = report["mutants"]["nearest_lexical"]
            if not result["applicable"]:
                saw.add("n/a")
                assert "no single-match target" in result["not_applicable_reason"]
                assert result["valid_output"] is False
                assert result["semantic_distinguished"] is False
            else:
                saw.add("applicable")
                assert result["valid_output"] is True
    assert "n/a" in saw and "applicable" in saw


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
