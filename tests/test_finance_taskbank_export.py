from scripts.materialize_finance_taskbank import hold_reasons


def test_scope_and_fact_holds_cover_every_variant_of_a_task():
    world = {
        "source_collection_id": "source-a",
        "world_instance_id": "world-a",
        "split_group_id": "issuer-a",
    }
    task = {"semantic_task_id": "task-a", "consumed_fact_ids": ["fact-1", "fact-2"]}
    for variant in ("full", "counterfactual", "reordered"):
        assert hold_reasons(
            world, {**task, "variant": variant}, {"semantic_task_ids": ["task-a"]}
        ) == ["semantic_task_ids"]
        assert hold_reasons(
            world, {**task, "variant": variant}, {"fact_ids": ["fact-2"]}
        ) == ["fact_ids"]
    assert hold_reasons(world, task, {"split_group_ids": ["issuer-a"]}) == [
        "split_group_ids"
    ]
    assert hold_reasons(world, task, {"semantic_task_ids": ["task-b"]}) == []
