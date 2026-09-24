"""Two-source comparison over frozen, conflicting observatory list rows."""

from __future__ import annotations

import json
from pathlib import Path

from longworld.synthesis import reader_view, wiki_source_compare, wiki_world_bridge


def _world():
    path = (
        Path(__file__).resolve().parents[1]
        / "data/capability_records/p74_wiki_snapshot_v1/astronomical_observatories_snapshot.json"
    )
    snapshot = json.loads(path.read_text())
    return wiki_world_bridge.structurally_typed_world(
        wiki_world_bridge.snapshot_to_world(snapshot)
    )[0]


def test_source_comparison_requires_two_distinct_supported_documents():
    world = _world()
    tasks = wiki_source_compare.build_source_compare_tasks(world)
    assert len(tasks) == 2
    assert {task.program["relation"] for task in tasks} == {
        "established in",
        "located in",
    }
    for task in tasks:
        wiki_source_compare.validate_source_compare_task(world, task)
        assert (
            wiki_source_compare.execute_source_compare(world, task.program)
            == task.answer
        )
        assert len(task.consumed_fact_ids) == len(task.scope.documents) == 2
        assert len(set(task.answer.values())) == 2
        assert all(title in task.question for title in task.answer)
        assert (
            world.facts_by_id[task.consumed_fact_ids[0]].qualifiers["table_column"]
            in task.question
        )
        for side in task.program["sides"]:
            fact = world.facts_by_id[side["fact_id"]]
            doc = world._docs[side["doc_id"]]
            assert doc.title in task.answer
            assert task.answer[doc.title] == fact.value


def test_source_comparison_reader_hides_the_oracle_and_maps_both_facts():
    world = _world()
    task = wiki_source_compare.build_source_compare_tasks(world)[0]
    row, index, audit = reader_view.compile_task(world, task, source_group="wiki-1")
    user = row["messages"][0]["content"]
    assert index["question_style"] == "natural_source_compare"
    assert index["document_count"] == 2
    assert user.endswith(reader_view.QUESTION_SEPARATOR + task.question)
    assert "=== FACTS ===" not in user and "=== ENTITIES ===" not in user
    assert set(audit["consumed_fact_ids"]) == set(task.consumed_fact_ids)
    assert {span["ref_id"] for span in audit["source_to_reader_spans"]} == set(
        task.consumed_fact_ids
    )
    assert json.loads(row["messages"][1]["content"]) == task.answer
