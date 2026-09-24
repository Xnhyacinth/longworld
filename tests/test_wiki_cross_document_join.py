"""A frozen two-table join must survive independent reader replay."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from longworld.synthesis import (
    reader_view,
    wiki_cross_document_join,
    wiki_world_bridge,
)
from longworld.synthesis.shared_semantic_world import SemanticWorld

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "data/capability_records/p74_wiki_snapshot_v1/astronomical_observatories_snapshot.json"
)


def _world() -> SemanticWorld:
    return wiki_world_bridge.snapshot_to_world(json.loads(SOURCE.read_text()))


def test_real_two_document_join_has_two_supported_cells_and_value_blind_replay():
    world = _world()
    tasks = wiki_cross_document_join.build_join_tasks(world)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.answer == 1984
    assert len(set(task.scope.documents)) == 2
    assert len(task.consumed_fact_ids) == 2
    assert "Apache Point Observatory" not in task.question
    assert wiki_cross_document_join.execute_join(world, task.program) == task.answer
    context = reader_view.render_documents(world, task.scope.documents).text
    assert "=== FACTS ===" not in context
    assert wiki_cross_document_join.reader_join_replay(world, task, context) == 1984
    report = wiki_cross_document_join.reader_join_intervention(world, task, context)
    assert report["status"] == "both_scoped_table_cells_necessary_for_parser"
    assert len(report["interventions"]) == 2
    for item in task.proof:
        for span, expected in zip(item.spans, item.span_texts):
            assert world._docs[span.doc_id].text[span.start : span.end] == expected

    # A different year in the second reader table changes the answer even
    # though the hidden fact program and gold remain at 1984.
    visible_span = report["interventions"][1]["masked_cell_span"]
    start, end = visible_span["start"], visible_span["end"]
    assert context[start:end] == "1984"
    mutated = context[:start] + "1990" + context[end:]
    cells = wiki_cross_document_join._reader_cells(
        world, task, mutated, allow_masked=True
    )
    assert cells[1][0] == 1990


def test_duplicate_first_selector_is_rejected_by_final_reader_replay():
    world = _world()
    task = wiki_cross_document_join.build_join_tasks(world, max_tasks=1)[0]
    bind_doc = task.program["bind_doc_id"]
    docs = tuple(
        replace(
            doc,
            text=doc.text
            + "\nName | Established | Location\nOther Observatory | 1985 | Elsewhere\n",
        )
        if doc.doc_id == bind_doc
        else doc
        for doc in world.documents
    )
    duplicate = SemanticWorld(docs, world.entities, world.facts)
    context = reader_view.render_documents(duplicate, task.scope.documents).text
    with pytest.raises(ValueError, match="unique first-table binding"):
        wiki_cross_document_join.reader_join_replay(duplicate, task, context)


def test_frozen_pool_reports_zero_where_two_document_join_is_unsupported():
    pool = json.loads((ROOT / "configs/p76_source_pool_v2.json").read_text())
    yields = {}
    for source in pool["sources"]:
        snapshot = json.loads((ROOT / source["snapshot"]["path"]).read_text())
        world = wiki_world_bridge.snapshot_to_world(snapshot)
        yields[source["name"]] = len(wiki_cross_document_join.build_join_tasks(world))
    assert yields == {
        source["name"]: int(source["name"] == "wiki_observatories")
        for source in pool["sources"]
    }
