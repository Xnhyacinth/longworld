"""A real bridge-table join must use both final reader documents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis import wiki_row_binding, wiki_world_bridge
from longworld.synthesis.shared_semantic_world import Document, SemanticWorld

ROOT = Path(__file__).resolve().parents[1]
BRIDGES = ROOT / "data/capability_records/p76_wiki_titles_v4/bridges_snapshot.json"


def _real_tasks() -> tuple[wiki_row_binding.JoinTask, ...]:
    if not BRIDGES.is_file():
        pytest.skip("optional frozen Wiki bridge snapshot is absent")
    world = wiki_world_bridge.snapshot_to_world(json.loads(BRIDGES.read_text()))
    return wiki_row_binding.build_join_tasks(world, max_tasks=100)


def _synthetic_tasks() -> tuple[wiki_row_binding.JoinTask, ...]:
    world = SemanticWorld(
        (
            Document(
                "d1",
                "Alpha List",
                "Name | Code\nAlpha Observatory | QX-42\nBeta Observatory | QX-43\nGamma Observatory | QX-44",
            ),
            Document(
                "d2",
                "Beta List",
                "Name | Height\nAlpha Observatory | 1108 m\nBeta Observatory | 1202 m\nGamma Observatory | 1410 m",
            ),
        ),
        (),
        (),
    )
    return wiki_row_binding.build_join_tasks(world)


def test_two_table_join_replays_and_needs_two_visible_cells():
    tasks = _synthetic_tasks()
    assert len(tasks) == 6
    assert len({task.task_id for task in tasks}) == len(tasks)
    assert all(task.first_title != task.second_title for task in tasks)
    assert all(task.alternatives >= 2 for task in tasks)
    for task in tasks:
        assert task.answer == wiki_row_binding.reader_replay(task, task.context)
        assert task.context[task.first_span[0] : task.first_span[1]]
        assert task.context[task.bound_name_span[0] : task.bound_name_span[1]]
        assert task.context[task.target_span[0] : task.target_span[1]]
        assert "=== FACTS ===" not in task.context
        assert len(wiki_row_binding.reader_interventions(task)) == 2
        for start, end in (task.first_text_span, task.second_text_span):
            masked = task.context[:start] + "?" * (end - start) + task.context[end:]
            with pytest.raises(ValueError):
                wiki_row_binding.reader_replay(task, masked)


def test_reader_replay_uses_changed_visible_target_value_not_hidden_gold():
    task = _synthetic_tasks()[0]
    start, end = task.target_span
    original = task.context[start:end]
    assert "1108" in original
    mutated = (
        task.context[:start] + original.replace("1108", "1109", 1) + task.context[end:]
    )
    assert wiki_row_binding.reader_replay(task, mutated) == "1109 m"
    assert task.answer == "1108 m"
    with pytest.raises(ValueError, match="context length drift"):
        wiki_row_binding.reader_replay(task, task.context[: task.second_text_span[0]])


def test_optional_frozen_bridge_snapshot_has_independent_joins():
    tasks = _real_tasks()
    assert len(tasks) == 16
    assert len({task.task_id for task in tasks}) == len(tasks)
    assert all(task.first_title != task.second_title for task in tasks)
    assert all(task.alternatives >= 2 for task in tasks)
