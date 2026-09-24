"""Closed-table scan admission, complete reader replay, and interventions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis import reader_view, wiki_table_scan, wiki_world_bridge

ROOT = Path(__file__).resolve().parents[1]


def _world(folder: str, name: str):
    path = ROOT / f"data/capability_records/{folder}/{name}_snapshot.json"
    return wiki_world_bridge.snapshot_to_world(json.loads(path.read_text()))


@pytest.mark.parametrize(
    ("name", "title", "expected", "format_excluded"),
    [
        ("parks", "List of national parks of Japan", 35, 0),
        ("universities", "List of universities in England", 32, 80),
    ],
)
def test_complete_universe_and_interval_answers(
    name: str, title: str, expected: int, format_excluded: int
) -> None:
    world = _world("p76_wiki_titles_v4", name)
    doc = next(doc for doc in world.documents if doc.title == title)
    parsed, facts = wiki_table_scan.closed_universe(world, doc.doc_id)
    assert len(parsed.eligible) == len(facts) == expected
    assert len(parsed.format_excluded) == format_excluded
    for row in (*parsed.eligible, *parsed.format_excluded):
        assert doc.text[row.year_start : row.year_end] == row.year_cell
        assert row.subject in doc.text[row.row_start : row.row_end]
        assert "Established" in doc.text[row.header_start : row.header_end]
    tasks = wiki_table_scan.build_scan_tasks(world, doc.doc_id)
    assert len(tasks) == 8
    assert len({task.answer["count"] for task in tasks}) >= 5
    for task in tasks:
        assert len(task.consumed_fact_ids) == expected
        assert task.answer == wiki_table_scan.execute_interval(world, task.program)
        context = reader_view.render_documents(world, task.scope.documents).text
        parsed_answer, replayed, _ = wiki_table_scan.reader_interval_replay(
            world, task, context
        )
        assert parsed_answer == task.answer
        assert len(replayed.eligible) == expected
        check = wiki_table_scan.insertion_intervention(world, task, context)
        assert check["eligible_row_count"] == expected
        assert check["hit_answer"]["count"] == task.answer["count"] + 1
        assert check["near_miss_answer"] == task.answer


def test_reader_receives_natural_question_and_all_candidate_year_spans() -> None:
    world = _world("p76_wiki_titles_v4", "parks")
    doc = next(
        doc for doc in world.documents if doc.title == "List of national parks of Japan"
    )
    task = wiki_table_scan.build_scan_tasks(world, doc.doc_id, max_tasks=1)[0]
    row, index, audit = reader_view.compile_task(world, task, source_group="parks-test")
    assert index["question_style"] == "natural_table_scan"
    assert json.loads(row["messages"][1]["content"]) == task.answer
    assert "plain citation-free name cell" in row["messages"][0]["content"]
    assert "=== FACTS ===" not in row["messages"][0]["content"]
    assert {span["ref_id"] for span in audit["source_to_reader_spans"]} == set(
        task.consumed_fact_ids
    )


def test_repaired_hospital_source_admits_only_clean_name_and_year_cells() -> None:
    world = _world("p76_wiki_titles_v6", "hospitals")
    doc = next(
        doc for doc in world.documents if doc.title == "List of hospitals in Greece"
    )
    parsed, facts = wiki_table_scan.closed_universe(world, doc.doc_id)
    assert len(parsed.eligible) == len(facts) == 81
    assert len(parsed.format_excluded) == 10
    assert any(
        row.exclusion_reason == "non_plain_name" for row in parsed.format_excluded
    )
    tasks = wiki_table_scan.build_scan_tasks(world, doc.doc_id)
    assert len(tasks) == 8
    for task in tasks:
        context = reader_view.render_documents(world, task.scope.documents).text
        assert (
            wiki_table_scan.reader_interval_replay(world, task, context)[0]
            == task.answer
        )
        check = wiki_table_scan.insertion_intervention(world, task, context)
        assert check["eligible_row_count"] == 81
