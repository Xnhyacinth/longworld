"""Behavioral gates for same-context, distinct-operation joint tasks."""

import hashlib

import pytest

from scripts.p125_joint_task_compiler import operation_family, pair_reason, split_reader


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_reader_context_can_precede_or_follow_the_task() -> None:
    context = "Document A\nDocument B"
    assert split_reader(context + "\n\nQUESTION\nFind X", _sha(context)) == (
        context,
        "Find X",
        "context_first",
    )
    assert split_reader("Find X\n\nSource records:\n" + context, _sha(context)) == (
        context,
        "Find X",
        "question_first",
    )
    # Two nested marker spellings must not count as two different boundaries.
    assert split_reader(context + "\n\nQuestion:\nFind X", _sha(context))[1] == "Find X"
    with pytest.raises(ValueError, match="unrecognized"):
        split_reader("Wrong\n\nQUESTION\nFind X", _sha(context))


def test_parameter_variants_do_not_count_as_distinct_operations() -> None:
    a = {
        "dependency_status": "bounded",
        "source_kind": "real_finance",
        "domain": "finance",
        "semantic_task_id": "a",
        "operation": "max_abs_delta:revenue->cash",
        "answer_sha256": "one",
    }
    b = {**a, "semantic_task_id": "b", "operation": "max_abs_delta:income->cash", "answer_sha256": "two"}
    assert operation_family(a["operation"]) == "max_abs_delta"
    assert pair_reason(a, b) == "same_operation_family"
    assert pair_reason(a, {**b, "operation": "filtered_aggregate"}) is None
    assert pair_reason(a, {**b, "operation": "filtered_aggregate", "dependency_status": None}) == "missing_component_dependency_status"
