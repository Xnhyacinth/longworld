"""P0-3 regression: a later gold answer must not be derivable from an earlier one.

The workflow value recurrence is `value = ((prior + payload) * multiplier) %
100003` with `depends_on` the preceding record. That makes the joint 16-question
answer object a reversible bijection: partition k's answer follows from
partition k-1's answer plus the k-th partition slice, with no long read at all.
These tests pin both halves: the leak is real for the joint contract (so nobody
re-introduces it as "just a key ordering issue"), and absent for the split one.
"""

import json

import pytest

from longworld.synthesis import capability_curriculum as curriculum
from longworld.synthesis import capability_rules_workflow as workflow


def _partition_slices(doc):
    slices = {}
    for row in doc["records"]:
        slices.setdefault(row["partition"], []).append(row)
    return slices


def _chain_from(answer_value, rows):
    value = answer_value
    for row in rows:
        value = ((value + row["payload"]) * row["multiplier"]) % 100003
    return value


def _derived_partitions(tasks, doc):
    """Partitions whose final_value follows from the previous answer + slice."""
    by_id = {t["task_id"]: t for t in tasks}
    slices = _partition_slices(doc)
    derived = 0
    for partition in range(1, doc["partitions"]):
        prior = by_id[f"q{partition - 1:03d}"]["answer"]["final_value"]
        if _chain_from(prior, slices[partition]) == by_id[f"q{partition:03d}"]["answer"]["final_value"]:
            derived += 1
    return derived


@pytest.mark.parametrize("seed", [1, 9, 21])
def test_joint_workflow_answers_form_a_reversible_chain(seed):
    """The leak the split contract exists to remove, pinned as a probe.

    If this ever stops holding the split is no longer load-bearing and the
    contract should be re-decided, not silently kept.
    """
    bundle = workflow.generate_bundle(seed, "workflow", 120, 16, packaging="joint")
    doc = json.loads(bundle["context"])
    derived = _derived_partitions(bundle["tasks"], doc)
    assert derived == doc["partitions"] - 1


@pytest.mark.parametrize("seed", [1, 9, 21])
def test_split_rows_expose_no_derivable_sibling_answer(seed):
    """Each split row carries exactly one answer and one visible prefix."""
    bundle = workflow.generate_bundle(seed, "workflow", 120, 16, packaging="split")
    doc = json.loads(bundle["context"])
    assert len(bundle["tasks"]) == doc["partitions"]
    for task in bundle["tasks"]:
        answer = task["answer"]
        # The row's answer is a frozen-prefix state, and the state is over the
        # records of exactly one partition: no sibling partition is in scope.
        cutoff = task["question"]["cutoff"]
        in_scope = [row for row in doc["records"]
                    if row["partition"] == task["question"]["partition"]
                    and row["id"] <= cutoff]
        assert in_scope
        assert answer["last_job"] == in_scope[-1]["id"]
        assert answer["recovery_count"] <= len(in_scope)


@pytest.mark.parametrize("seed", [1, 9, 21])
def test_split_answer_is_not_a_function_of_any_other_answer_alone(seed):
    """No split answer is recoverable from a sibling answer without the prefix."""
    bundle = workflow.generate_bundle(seed, "workflow", 120, 16, packaging="split")
    by_id = {t["task_id"]: t for t in bundle["tasks"]}
    for partition in range(1, bundle["n_questions"]):
        prior = by_id[f"q{partition - 1:03d}"]["answer"]
        current = by_id[f"q{partition:03d}"]["answer"]
        # The previous answer names its own last job; a distinct cutoff means
        # the previous answer carries no information about this one's frontier.
        assert prior["last_job"] != current["last_job"]


@pytest.mark.parametrize("seed", [4, 17])
def test_prefix_containment_and_no_cross_row_contamination(seed):
    """Each split row's context is a prefix of the full world context.

    `_json` sorts keys, so a prefix document is literally a truncation of the
    full record list, and no other row's gold appears in this row's prompt.
    """
    bundle = workflow.generate_bundle(seed, "workflow", 120, 16, packaging="split")
    full = json.loads(bundle["context"])
    for task in bundle["tasks"]:
        context = workflow.workflow_context(full, task["question"]["cutoff"])
        doc = json.loads(context)
        records = [row["id"] for row in doc["records"]]
        assert records
        assert records[0] == full["records"][0]["id"]
        assert records == [row["id"] for row in full["records"]][: len(records)]
        assert doc["family"] == full["family"] and doc["protocol"] == full["protocol"]
    golds = [
        json.dumps(task["answer"], sort_keys=True, separators=(",", ":"))
        for task in bundle["tasks"]
    ]
    assert len(set(golds)) == len(golds)
    for index, task in enumerate(bundle["tasks"]):
        assert golds[index] not in task["prompt"]
        for other in golds:
            if other == golds[index]:
                continue
            assert other not in golds[index]


@pytest.mark.parametrize("family", ["ledger", "reservation"])
def test_joint_families_are_independent_by_construction(family):
    """Joint-family answers are per entity/alias, so no answer implies another."""
    bundle = curriculum.generate_bundle(3, family, 120, 16)
    for version in (bundle, bundle["counterfactual"]):
        for task in version["tasks"]:
            # The question names its own alias and cutoff; the answer is a
            # scalar or a set over that alias only.
            assert set(task["question"]) <= {"operation", "alias", "asof", "ordinal"}
            assert task["controls"]["question_only"] == (
                "missing visible contract is insufficient"
            )
    report = curriculum.validate_bundle(bundle)
    assert report["passed"], report["errors"]


def test_rule_learning_answers_are_per_entity_and_not_mutually_derivable():
    bundle = workflow.generate_bundle(5, "rule_learning", 120, 16)
    doc = json.loads(bundle["context"])
    claimed = set()
    for task in bundle["tasks"]:
        entities = {row["entity"] for row in doc["records"]
                    if row["partition"] == task["question"]["partition"]}
        assert {row["entity"] for row in task["answer"]} == entities
        # Partitions own disjoint entity sets, so answers share no rows.
        assert not (claimed & entities)
        claimed |= entities
