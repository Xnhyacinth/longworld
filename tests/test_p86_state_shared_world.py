"""P86 state operations reuse records and depend on visible disclosure events."""

import json

from longworld.synthesis import p86_state_shared_world as shared


def test_raw_and_asof_operations_share_necessary_record_facts():
    world = shared.build_world(860001, 400, 20, 2, 2)
    assert shared.validate_world(world)["passed"]
    assert len(world["tasks"]) == 8
    for pair_id, overlap in world["shared_fact_ids"].items():
        tasks = {
            task["operation"]: task
            for task in world["tasks"]
            if task["pair_id"] == pair_id
        }
        assert set(tasks) == set(shared.OPERATIONS)
        assert len(overlap["cross_operation_record_ids"]) >= 4
        assert any(fact_id.startswith("e") for fact_id in overlap["state_fact_ids"])
        assert tasks["asof_sum"]["consumed"] == tasks["asof_complete_set"]["consumed"]
        assert tasks["asof_sum"]["answer"]["sum"] > 0
        assert tasks["asof_complete_set"]["answer"]["record_ids"]
        for task in tasks.values():
            assert shared.solve_visible(world["reader_context"], task) == task["answer"]


def test_revoke_deletion_changes_both_state_targets_but_late_reveal_does_not():
    world = shared.build_world(860002, 400, 20, 2, 2)
    pair = [task for task in world["tasks"] if task["pair_id"] == "q0"]
    state = [task for task in pair if task["operation"] in shared.STATE_OPS]
    event_id = next(
        fact_id for fact_id in state[0]["consumed"] if fact_id.startswith("e")
    )
    without_revoke = shared._drop_fact(world["reader_context"], event_id)
    for task in state:
        assert shared.solve_visible(without_revoke, task) != task["answer"]
        assert task["text_interventions"]["probed_events"] > 0
    active_id = next(
        fact_id for fact_id in state[0]["consumed"] if fact_id.startswith("r")
    )
    without_active_record = shared._drop_record_support(
        world["reader_context"], active_id
    )
    for task in state:
        assert shared.solve_visible(without_active_record, task) != task["answer"]
    events = [
        json.loads(line)
        for line in world["reader_context"].splitlines()[1:]
        if json.loads(line)["type"] == "revoke"
    ]
    late = next(
        event
        for event in events
        if event["record_id"]
        in world["shared_fact_ids"]["q0"]["cross_operation_record_ids"]
    )
    # An active record has a late-reveal revoke; deleting that near miss must
    # leave the as-of answer unchanged.
    without_late = shared._drop_fact(world["reader_context"], late["id"])
    for task in state:
        if late["id"] not in task["consumed"]:
            assert shared.solve_visible(without_late, task) == task["answer"]


def test_effective_and_reveal_boundaries_each_change_state_answers():
    world = shared.build_world(860004, 400, 20, 2, 2)
    assert world["temporal_contrast_checks"] == 16
    cases = world["event_cases"]["q0"]
    tasks = [
        task
        for task in world["tasks"]
        if task["pair_id"] == "q0" and task["operation"] in shared.STATE_OPS
    ]
    for name, field, target in (
        ("future_effective_boundary", "effective", tasks[0]["question"]["asof"]),
        ("future_reveal_boundary", "reveal", tasks[0]["question"]["known_at"]),
    ):
        assert cases[name]
        modified = shared._change_event_date(
            world["reader_context"], cases[name][0], field, target
        )
        for task in tasks:
            assert shared.solve_visible(modified, task) != task["answer"]


def test_tampered_event_or_answer_fails_closed():
    world = shared.build_world(860003, 400, 20, 2, 2)
    world["tasks"][2]["answer"]["sum"] += 1
    assert not shared.validate_world(world)["passed"]
    world = shared.build_world(860003, 400, 20, 2, 2)
    lines = world["reader_context"].splitlines()
    event_index = next(
        index for index, line in enumerate(lines) if '"type":"revoke"' in line
    )
    event = json.loads(lines[event_index])
    event["entity"] = "wrong-entity"
    lines[event_index] = shared._dump(event)
    world["reader_context"] = "\n".join(lines)
    assert not shared.validate_world(world)["passed"]
