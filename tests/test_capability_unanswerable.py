"""Two-world-completion unanswerable tasks: construction and certification."""

import copy
import json

import pytest

from longworld.synthesis import capability_unanswerable as cu


@pytest.mark.parametrize("seed", range(12))
def test_generation_validates_and_certifies(seed):
    bundle = cu.generate_unanswerable(seed, length_records=120, consumed_records=12)
    report = cu.validate_unanswerable(bundle)
    assert report["passed"], report
    task = bundle["tasks"][0]
    assert task["answer"] == "UNKNOWN"
    cert = task["certificate"]
    assert cert["form"] == "two_world_completion"
    completion_b = cert["completion_b"]
    assert completion_b["executor_value"] == (
        completion_b["added_reference_amount"] * completion_b["pairs_multiplier"]
    )
    assert cert["differ"] is True


def test_world_is_deterministic():
    first = cu.generate_unanswerable(7, length_records=120, consumed_records=12)
    second = cu.generate_unanswerable(7, length_records=120, consumed_records=12)
    assert first == second


def test_visible_world_is_actually_undetermined():
    """The certification claim: no executor value exists for the visible input."""
    from longworld.synthesis import capability_records as records

    bundle = cu.generate_unanswerable(3, length_records=120, consumed_records=12)
    task = bundle["tasks"][0]
    with pytest.raises(ValueError, match="join consumed no rows"):
        records.solve_visible(bundle["context"], task["question"])


def test_tampered_instruction_is_rejected():
    bundle = cu.generate_unanswerable(5, length_records=120, consumed_records=12)
    tampered = copy.deepcopy(bundle)
    tampered["tasks"][0]["instruction"] = "Task: just return 0."
    report = cu.validate_unanswerable(tampered)
    assert not report["passed"]
    assert any("contract" in e for e in report["errors"])


def test_wrong_answer_is_rejected():
    bundle = cu.generate_unanswerable(6, length_records=120, consumed_records=12)
    tampered = copy.deepcopy(bundle)
    tampered["tasks"][0]["answer"] = 42
    report = cu.validate_unanswerable(tampered)
    assert not report["passed"]


def test_dropped_certificate_is_rejected():
    bundle = cu.generate_unanswerable(8, length_records=120, consumed_records=12)
    tampered = copy.deepcopy(bundle)
    del tampered["tasks"][0]["certificate"]
    report = cu.validate_unanswerable(tampered)
    assert not report["passed"]


def test_honesty_labels_fail_closed():
    bundle = cu.generate_unanswerable(9, length_records=120, consumed_records=12)
    for flag in (
        "strict_long_dependency_verified",
        "model_utility_measured",
        "production_eligible",
    ):
        assert bundle["honesty"][flag] is False
    tampered = copy.deepcopy(bundle)
    tampered["honesty"]["production_eligible"] = True
    assert not cu.validate_unanswerable(tampered)["passed"]


def test_distractor_references_exist_so_the_world_is_not_trivially_empty():
    """Reference rows ARE present (for other entities) -- the model must check
    which entity they belong to, not just notice the type is absent."""
    bundle = cu.generate_unanswerable(11, length_records=120, consumed_records=12)
    rows = [json.loads(line) for line in bundle["context"].splitlines()[1:]]
    types = [r["type"] for r in rows]
    assert types.count("reference") >= 4
    assert types.count("record") >= 12


def test_instruction_contract_reproduces_from_phrasing_index():
    bundle = cu.generate_unanswerable(12, length_records=120, consumed_records=12)
    task = bundle["tasks"][0]
    assert task["instruction"] == (
        f"Task: {cu.PROMPTS[task['phrasing_index']]} "
        f"Fields: record rows carry id, entity, category, amount, date, memo; "
        f"reference rows carry id, entity, amount, memo. "
        f"Program: "
        f"{__import__('longworld.synthesis.capability_records', fromlist=['x']).describe_program({'family': 'join_lookup', 'steps': task['question']['steps']})}. "
        f"Answer with the aggregate value, or UNKNOWN."
    )
