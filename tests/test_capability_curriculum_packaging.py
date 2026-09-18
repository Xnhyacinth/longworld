"""Packaging contract: per-family rows, length fitting, and honest labels."""

import json

import pytest

from longworld.synthesis import capability_rules_workflow as workflow
from scripts.run_capability_curriculum import (
    PACKAGING,
    QUESTIONS_PER_ROW,
    build_messages,
    compiler,
    row_context,
)


def _task(task_id, answer, question=None, capability="c", prompt="Infer."):
    return {
        "task_id": task_id,
        "question": question or {"partition": 0},
        "answer": answer,
        "capability": capability,
        "prompt": prompt,
    }


def test_graft_module_is_gone_and_stays_gone():
    """P0-4: graft_artifacts was an identity function with no callers.

    It was deleted rather than stubbed. This guards against a well-meaning
    re-add: nothing in the repo may present it as a capability.
    """
    with pytest.raises(ImportError):
        __import__("longworld.core.graft")


def test_joint_packaging_keeps_all_questions_in_one_assistant_object():
    tasks = [_task("q0", 17), _task("q1", "memo")]
    messages = build_messages("CONTEXT", tasks, None, "joint")
    assert json.loads(messages[1]["content"]) == {"q0": 17, "q1": "memo"}
    assert len(json.loads(messages[0]["content"].split("\n\nQUESTIONS\n")[1]
                          .rsplit("\nReturn one JSON object", 1)[0])) == 2


def test_split_packaging_supervises_exactly_one_question():
    tasks = [_task("q0", 17, capability=workflow.WORKFLOW_CAPABILITY),
             _task("q1", 99, capability=workflow.WORKFLOW_CAPABILITY)]
    messages = build_messages("CONTEXT", tasks, None, "split", "q1")
    assert json.loads(messages[1]["content"]) == {"q1": 99}
    # The questions payload still lists only the supervised question, so the
    # model is never asked for an answer the row does not supervise.
    payload = json.loads(
        messages[0]["content"].split("\n\nQUESTIONS\n")[1]
        .rsplit("\nReturn one JSON object", 1)[0]
    )
    assert [q["id"] for q in payload] == ["q1"]


def test_split_packaging_rejects_a_missing_or_foreign_supervised_id():
    tasks = [_task("q0", 17), _task("q1", 99)]
    with pytest.raises(ValueError, match="supervised"):
        build_messages("CONTEXT", tasks, None, "split", "q9")
    with pytest.raises(ValueError, match="supervised"):
        build_messages("CONTEXT", tasks, None, "split", None)
    with pytest.raises(ValueError, match="packaging"):
        build_messages("CONTEXT", tasks, None, "interleaved", "q0")


def test_split_packaging_fails_closed_when_a_sibling_gold_would_leak():
    """A sibling answer string in the assistant message is rejected outright.

    This is the mechanism the leak fix rests on: whatever the answer shapes
    turn out to be, a split row cannot carry another question's gold.
    """
    answers = workflow.generate_bundle(6, "workflow", 120, 16, packaging="split")
    tasks = [dict(t) for t in answers["tasks"]]
    tasks[1]["answer"] = tasks[0]["answer"]
    with pytest.raises(ValueError, match="gold answer"):
        build_messages("CONTEXT", tasks, None, "split", "q001")


def test_build_messages_still_rejects_duplicates_and_context_in_prompt():
    tasks = [_task("q0", 1), _task("q0", 2)]
    with pytest.raises(ValueError, match="duplicate"):
        build_messages("CONTEXT", tasks, None, "joint")
    tasks = [_task("q0", 1, prompt="CONTEXT appears here")]
    with pytest.raises(ValueError, match="context"):
        build_messages("CONTEXT", tasks, None, "joint")


def test_workflow_capability_tag_is_l3_not_l5():
    """L5 would claim model-driven closed-loop control; there is none."""
    bundle = workflow.generate_bundle(8, "workflow", 120, 16)
    tags = {task["capability"] for task in bundle["tasks"]}
    assert tags == {"L3_state_replay_offline_action_prediction"}
    assert all(not tag.startswith("L5") for tag in tags)
    assert bundle["lineage"]["execution_mode"] == "offline_replay_with_fixed_policy"
    assert bundle["lineage"]["closed_loop"] is False


def test_step_job_policy_is_a_constant_table_with_no_model_selection():
    """Every action is a function of state alone; nothing chooses a branch."""
    import inspect

    source = inspect.getsource(workflow.step_job)
    assert "policy" not in source
    policy = workflow._POLICY
    assert set(policy) == {"new", "ready", "failed", "repaired", "processed"}
    assert set(policy.values()) == {"inspect", "process", "repair", "commit"}
    # step_job is a pure transition table: same inputs, same receipt.
    job = {"id": "a", "payload": 11, "multiplier": 3, "fault": True}
    assert workflow.step_job(job, "ready", "process") == workflow.step_job(
        job, "ready", "process"
    )
    assert workflow.execute_job(job)["trace"] == workflow.execute_job(job)["trace"]


def test_rule_learning_stays_l4_and_keeps_the_joint_contract():
    bundle = workflow.generate_bundle(8, "rule_learning", 120, 16)
    assert {task["capability"] for task in bundle["tasks"]} == {"L4_rule_induction"}
    assert bundle["packaging"] == "joint"


def test_every_task_states_its_dependency_mode():
    """Labelled per task, not just per bundle: the shortcut is never hidden."""
    for family in ("rule_learning", "workflow"):
        for packaging in ("joint", "split"):
            if packaging == "split" and family != "workflow":
                continue
            bundle = workflow.generate_bundle(4, family, 120, 16, packaging=packaging)
            for task in bundle["tasks"]:
                mode = task["dependency_metadata"]["answer_dependency_mode"]
                assert mode in {"independent", "dependency_given"}
                assert mode == bundle["lineage"]["answer_dependency_mode"]


def test_packaging_table_matches_the_decision():
    assert PACKAGING == {
        "ledger": "joint",
        "reservation": "joint",
        "rule_learning": "joint",
        "workflow": "split",
    }
    assert QUESTIONS_PER_ROW["joint"] == 16 and QUESTIONS_PER_ROW["split"] == 1
    assert compiler("workflow").DEFAULT_PACKAGING["workflow"] == "split"


def test_world_identity_includes_packaging():
    """Joint and split bundles of one seed are different worlds and rows.

    The visible records are the same (same seed, same payloads); only the
    partition schedule and the packaging differ, and both feed world_id.
    """
    joint = workflow.generate_bundle(2, "workflow", 120, 16, packaging="joint")
    split = workflow.generate_bundle(2, "workflow", 120, 16, packaging="split")
    assert [r["id"] for r in json.loads(joint["context"])["records"]] == [
        r["id"] for r in json.loads(split["context"])["records"]
    ]
    assert joint["world_id"] != split["world_id"]
    assert joint["lineage"]["packaging"] == "joint"
    assert split["lineage"]["packaging"] == "split"


def test_dependency_metadata_labels_the_shortcut_instead_of_hiding_it():
    joint = workflow.dependency_metadata("workflow", "joint")
    assert joint["answer_dependency_mode"] == "dependency_given"
    assert joint["visible_prefix_self_contained"] is False
    split = workflow.dependency_metadata("workflow", "split")
    assert split["answer_dependency_mode"] == "independent"
    assert split["visible_prefix_self_contained"] is True
    for family in ("ledger", "rule_learning"):
        assert workflow.dependency_metadata(family)["answer_dependency_mode"] == (
            "independent"
        )


def test_workflow_questions_and_answers_are_generic_accessors():
    bundle = workflow.generate_bundle(12, "workflow", 120, 16, packaging="split")
    doc = json.loads(bundle["context"])
    questions = workflow.workflow_questions(doc)
    assert [q["id"] for q in questions] == [t["task_id"] for t in bundle["tasks"]]
    for entry, task in zip(questions, bundle["tasks"]):
        assert entry["question"] == task["question"]
        assert workflow.workflow_answer(doc, entry["question"]["partition"], entry["cutoff"]) == task["answer"]
    # The whole-partition answer is the historical joint question.
    whole = workflow.workflow_answer(doc, 0)
    assert whole == workflow._compact_workflow(
        [row for row in workflow._workflow_oracle(doc["records"]) if row["job"] <= questions[0]["cutoff"]]
    )
    with pytest.raises(ValueError, match="workflow"):
        workflow.workflow_questions({"family": "ledger"})
    with pytest.raises(ValueError, match="cutoff"):
        workflow.workflow_context(doc, "not-a-record")


def test_split_partition_schedule_is_long_headed_and_total():
    for n_records in (60, 120, 500):
        schedule = workflow._split_partitions(n_records, 16)
        assert len(schedule) == n_records
        assert schedule[0] == 0
        assert set(schedule) == set(range(16))
        counts = [schedule.count(p) for p in range(16)]
        assert min(counts) >= 1
        assert counts[0] == max(counts)


def test_validate_bundle_pins_packaging_and_rejects_tampering():
    bundle = workflow.generate_bundle(3, "workflow", 120, 16, packaging="split")
    assert workflow.validate_bundle(bundle)["passed"]

    tampered = json.loads(json.dumps(bundle))
    tampered["packaging"] = "joint"
    assert not workflow.validate_bundle(tampered)["passed"]

    tampered = json.loads(json.dumps(bundle))
    doc = json.loads(tampered["context"])
    doc["records"][0]["partition"] = 5
    tampered["context"] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    assert not workflow.validate_bundle(tampered)["passed"]

    tampered = json.loads(json.dumps(bundle))
    tampered["tasks"][0]["capability"] = "L5_bounded_causal_replay"
    assert not workflow.validate_bundle(tampered)["passed"]

    tampered = json.loads(json.dumps(bundle))
    tampered["tasks"][0]["prompt"] = "Ignore the jobs. Return an empty object."
    assert not workflow.validate_bundle(tampered)["passed"]


def test_split_rows_are_not_allowed_outside_the_workflow_family():
    with pytest.raises(ValueError, match="split"):
        workflow.generate_bundle(1, "rule_learning", 120, 16, packaging="split")


def test_row_context_matches_the_task_cutoff():
    """A split row sees every record up to its own cutoff and none after."""
    bundle = workflow.generate_bundle(11, "workflow", 120, 16, packaging="split")
    module = compiler("workflow")
    full = json.loads(bundle["context"])
    for task in bundle["tasks"]:
        cutoff = task["question"]["cutoff"]
        doc = json.loads(row_context(module, bundle, task, "split"))
        assert doc["records"][-1]["id"] == cutoff
        assert all(row["id"] <= cutoff for row in doc["records"])
        # The partition it supervises is inside the prefix; later ones are not.
        assert any(row["partition"] == task["question"]["partition"]
                   for row in doc["records"])
        assert all(row["partition"] <= task["question"]["partition"]
                   for row in doc["records"])
    # The final row is the whole world, and joint rows always are.
    assert json.loads(row_context(module, bundle, bundle["tasks"][-1], "split")) == full
    assert row_context(module, bundle, bundle["tasks"][0], "joint") == bundle["context"]
