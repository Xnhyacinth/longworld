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
                    # every listed mutant either computes or records an error;
                    # both are explicit, never silent
                    assert "distinguished" in result or "error" in result
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
