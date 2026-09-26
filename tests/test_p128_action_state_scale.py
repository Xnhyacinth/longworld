"""Behavioral checks for distinct state observations and policy transitions."""

from __future__ import annotations

from pathlib import Path

from longworld.synthesis import p86_state_shared_world as state
from scripts import p114_controlled_action_feedback as prior
from scripts import p128_action_state_scale as scale


def _fixture() -> tuple[dict, dict]:
    world = state.build_world(1123000, 400, 20, 2, 2)
    source = next(task for task in world["tasks"] if task["task_id"] == "q0:asof_sum")
    return world, source


def test_boundary_variants_execute_distinct_visible_states() -> None:
    world, source = _fixture()
    context = world["reader_context"]
    parsed = state._parse(context)
    results = {}
    for variant in ("base", "effective_plus_one", "disclosure_plus_one"):
        task = scale._state_task(source, variant)
        balance, active_ids, signature = scale._execute_state(context, parsed, task)
        assert balance == state.solve_visible(context, task)["sum"]
        results[variant] = (balance, active_ids, signature)
    assert len({row[2] for row in results.values()}) == 3
    assert results["base"][0] != results["effective_plus_one"][0]
    assert results["base"][0] != results["disclosure_plus_one"][0]
    assert (
        source["question"]["asof"]
        != scale._state_task(source, "effective_plus_one")["question"]["asof"]
    )


def test_policy_alternatives_and_visible_intervention_on_new_state() -> None:
    world, source = _fixture()
    context = world["reader_context"]
    task = scale._state_task(source, "effective_plus_one")
    balance, _, _ = scale._execute_state(context, state._parse(context), task)
    for sign, expected in ((1, "ADD_500"), (-1, "REMOVE_500")):
        target = balance + sign * 250
        choice, outcomes = prior.decision(balance, target, 500)
        assert choice == expected
        assert (
            outcomes["ADD_500"]["next_balance"]
            != outcomes["REMOVE_500"]["next_balance"]
        )
        flips = prior._flipping_facts(context, task, sign, balance, target, 500)
        assert flips
        changed_balance = state.solve_visible(prior._drop(context, flips[0][0]), task)[
            "sum"
        ]
        assert prior.decision(changed_balance, target, 500)[0] != choice


def test_pinned_config_excludes_prior_state() -> None:
    config = scale._config(Path("configs/p128_action_state_scale_v1.json"))
    assert config["exclude_prior_q0_base"] is True
    assert config["source_task_ids"] == ["q0:asof_sum", "q1:asof_sum"]
