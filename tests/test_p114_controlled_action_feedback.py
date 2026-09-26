"""Policy action outcomes and source-state interventions are executable."""

from __future__ import annotations

from longworld.synthesis import p86_state_shared_world as state
from scripts import p114_controlled_action_feedback as policy


def test_actions_have_distinct_transitions_and_feedback() -> None:
    add, outcomes = policy.decision(1000, 1250, 500)
    assert add == "ADD_500"
    assert outcomes["ADD_500"] == {"next_balance": 1500, "feedback": -250}
    assert outcomes["REMOVE_500"] == {"next_balance": 500, "feedback": -750}
    remove, opposite = policy.decision(1000, 750, 500)
    assert remove == "REMOVE_500"
    assert opposite["REMOVE_500"]["feedback"] > opposite["ADD_500"]["feedback"]


def test_real_state_solver_fact_removal_flips_both_policy_targets() -> None:
    world = state.build_world(1123000, 400, 20, 2, 2)
    task = next(task for task in world["tasks"] if task["task_id"] == "q0:asof_sum")
    context = world["reader_context"]
    balance = state.solve_visible(context, task)["sum"]
    assert balance == task["answer"]["sum"]
    add_witnesses = policy._flipping_facts(
        context, task, 1, balance, balance + 250, 500
    )
    remove_witnesses = policy._flipping_facts(
        context, task, -1, balance, balance - 250, 500
    )
    assert add_witnesses and all(fact.startswith("e") for fact, _ in add_witnesses)
    assert remove_witnesses and all(
        fact.startswith("r") for fact, _ in remove_witnesses
    )
    event_kind, event_removed = policy._intervention_scope(context, add_witnesses[0][0])
    record_kind, record_removed = policy._intervention_scope(
        context, remove_witnesses[0][0]
    )
    assert (event_kind, event_removed) == ("single_event", [add_witnesses[0][0]])
    assert record_kind == "record_support_group"
    assert len(record_removed) == 2
    assert remove_witnesses[0][0] in record_removed


def test_question_exposes_actions_and_reward_rule_without_gold_balance() -> None:
    world = state.build_world(1123000, 400, 20, 2, 2)
    task = next(task for task in world["tasks"] if task["task_id"] == "q0:asof_sum")
    balance = task["answer"]["sum"]
    question = policy._question(task, balance + 250, 500, policy.ACTIONS[::-1])
    assert "REMOVE_500, ADD_500" in question
    assert "minus the absolute difference" in question
    assert str(balance) not in question


def test_target_only_probe_uses_held_out_worlds() -> None:
    def item(target: int, action: str, split: str) -> dict:
        return {
            "candidate": {"split": split},
            "proof": {"target": target, "chosen_action": action},
        }

    report = policy._no_history_probe(
        [
            item(0, "REMOVE_500", "train"),
            item(100, "ADD_500", "train"),
            item(40, "REMOVE_500", "eval"),
            item(60, "ADD_500", "eval"),
        ]
    )
    assert (report["train_correct"], report["eval_correct"]) == (2, 2)
    assert report["threshold"] == 50
