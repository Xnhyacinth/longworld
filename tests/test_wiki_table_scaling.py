"""Inverse table lookup needs a real row key and a value-blind reader replay."""

from __future__ import annotations

import json

import pytest

from longworld.synthesis import (
    reader_view,
    wiki_adapter,
    wiki_table_lookup,
    wiki_world_bridge,
)


def _world(rows: tuple[tuple[str, str, str], ...]):
    title = "List of sample museums"
    body = "\n".join(
        f"|-\n| {name} || {neighborhood} || {kind}" for name, neighborhood, kind in rows
    )
    page = wiki_adapter.PageRecord(
        pageid=1,
        title=title,
        revid=2,
        timestamp="2026-09-24T00:00:00Z",
        wikitext=(
            f'{{| class="wikitable"\n! Name !! Neighborhood !! Type\n{body}\n|}}\n'
        ),
    )
    snapshot = wiki_adapter.build_snapshot(
        members=[wiki_adapter.Member(1, title)],
        pages={1: page},
        link_meta=[],
        rights={
            "text": "Creative Commons Attribution-Share Alike 4.0",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
            "page_url_prefix": "https://en.wikipedia.org/wiki/",
        },
        category_title="test museums",
        collection_kind="title_bundle",
        frozen_at="2026-09-24T00:00:00Z",
    )
    return wiki_world_bridge.snapshot_to_world(snapshot)


def test_inverse_lookup_replays_name_from_visible_row_and_masks_it() -> None:
    world = _world(
        (("Alpha Museum", "Riverside", "Art"), ("Beta Museum", "Market", "Science"))
    )
    tasks = wiki_table_lookup.build_lookup_tasks(world)
    task = next(task for task in tasks if task.answer == "Alpha Museum")
    assert task.program["selector_column"] == "Type"
    assert task.program["selector"] == "Art"
    assert "Alpha Museum" not in task.question
    row, index, audit = reader_view.compile_task(world, task, source_group="museums")
    assert row["messages"][1]["content"] == json.dumps("Alpha Museum")
    assert index["question_style"] == "natural_table_lookup"
    assert len(audit["source_to_reader_spans"]) == 1
    context = reader_view.render_documents(world, task.scope.documents).text
    assert wiki_table_lookup.reader_lookup(world, task, context)[0] == task.answer
    assert (
        wiki_table_lookup.reader_cell_intervention(world, task, context)["status"]
        == "scoped_named_table_cell_removed"
    )
    changed = context.replace("Alpha Museum", "Gamma Museum", 1)
    assert (
        wiki_table_lookup.reader_lookup(world, task, changed, allow_masked=True)[0]
        == "Gamma Museum"
    )
    with pytest.raises(ValueError, match="final reader context drift"):
        wiki_table_lookup.reader_cell_intervention(world, task, changed)


def test_duplicate_selector_values_do_not_create_inverse_task() -> None:
    world = _world(
        (("Alpha Museum", "Riverside", "Art"), ("Beta Museum", "Riverside", "Art"))
    )
    assert wiki_table_lookup.build_lookup_tasks(world) == ()


def test_unsafe_selector_and_non_name_answer_are_rejected() -> None:
    world = _world((("Alpha Museum", "https://example.org/a", "Art"),))
    # The clean Type cell can still identify this row, but the URL cannot be
    # included in the task question as a substitute for source understanding.
    task = wiki_table_lookup.build_lookup_tasks(world)[0]
    assert task.program["selector_column"] == "Type"
    assert "https://" not in task.question
    invalid_name = _world((("70*", "Riverside", "Art"),))
    assert wiki_table_lookup.build_lookup_tasks(invalid_name) == ()
