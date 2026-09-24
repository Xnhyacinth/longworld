"""Table comparison must use distinct source rows and visible year cells."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from longworld.synthesis import (
    reader_view,
    wiki_adapter,
    wiki_evidence,
    wiki_table_tasks,
    wiki_world_bridge,
)
from longworld.synthesis.shared_semantic_world import SemanticWorld


def _world():
    pages = {}
    members = []
    for pageid, prefix, offset in ((1, "Alpha", 0), (2, "Beta", 40)):
        title = f"List of {prefix.lower()} observatories"
        rows = "\n".join(
            f"|-\n| {prefix} {index} Observatory || {1900 + offset + index} || Testland"
            for index in range(12)
        )
        pages[pageid] = wiki_adapter.PageRecord(
            pageid=pageid,
            title=title,
            revid=100 + pageid,
            timestamp="2026-09-24T00:00:00Z",
            wikitext=(
                '{| class="wikitable"\n'
                "! Name !! Established !! Location\n"
                f"{rows}\n"
                "|}\n"
            ),
        )
        members.append(wiki_adapter.Member(pageid, title))
    snapshot = wiki_adapter.build_snapshot(
        members=members,
        pages=pages,
        link_meta=[],
        rights={
            "text": "Creative Commons Attribution-Share Alike 4.0",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
            "page_url_prefix": "https://en.wikipedia.org/wiki/",
        },
        category_title="test observatory lists",
        collection_kind="title_bundle",
        frozen_at="2026-09-24T00:00:00Z",
    )
    return wiki_world_bridge.snapshot_to_world(snapshot)


def _with_extra_row(world, doc_id: str, header: str, row: str):
    docs = tuple(
        replace(doc, text=doc.text + "\n" + header + "\n" + row + "\n")
        if doc.doc_id == doc_id
        else doc
        for doc in world.documents
    )
    return SemanticWorld(docs, world.entities, world.facts)


def test_disjoint_cross_document_pairs_execute_and_compile_without_oracle_leak():
    world = _world()
    tasks = wiki_table_tasks.build_table_pair_tasks(world, max_tasks=12)
    assert len(tasks) == 12
    consumed = [fact_id for task in tasks for fact_id in task.consumed_fact_ids]
    assert len(consumed) == len(set(consumed))
    for task in tasks:
        wiki_table_tasks.validate_table_pair_task(world, task)
        assert len(set(task.scope.documents)) == 2
        assert task.answer == wiki_table_tasks.execute_table_pair(world, task.program)
        assert (
            len(
                {world.facts_by_id[fact_id].value for fact_id in task.consumed_fact_ids}
            )
            == 2
        )
    task = tasks[0]
    row, index, audit = reader_view.compile_task(world, task, source_group="table-test")
    assert index["question_style"] == "natural_source_compare"
    assert json.loads(row["messages"][1]["content"]) == task.answer
    assert set(task.answer) == {"years", "earlier"}
    assert len(task.answer["years"]) == 2
    assert (
        wiki_table_tasks.reader_table_replay(
            world, task, reader_view.render_documents(world, task.scope.documents).text
        )
        == task.answer
    )
    assert "=== FACTS ===" not in row["messages"][0]["content"]
    assert {span["ref_id"] for span in audit["source_to_reader_spans"]} == set(
        task.consumed_fact_ids
    )


def test_reader_deletion_masks_all_equivalent_table_cells():
    world = _world()
    task = wiki_table_tasks.build_table_pair_tasks(world, max_tasks=1)[0]
    first_fact = world.facts_by_id[task.consumed_fact_ids[0]]
    evidence = wiki_evidence.check_locate_fact(world, first_fact)
    assert evidence.supported and evidence.header and evidence.row
    duplicated_world = _with_extra_row(
        world, first_fact.supporting_spans[0].doc_id, evidence.header, evidence.row
    )
    duplicated = reader_view.render_documents(
        duplicated_world, task.scope.documents
    ).text
    report = wiki_table_tasks.reader_year_cell_intervention(
        duplicated_world, task, duplicated
    )
    assert report["status"] == "scoped_table_parser_year_cells_removed"
    assert report["deleted_cell_count"] == 3
    assert len(report["deleted_cell_offsets"]) == 3
    assert "unrestricted prose equivalence unchecked" in report["certification_scope"]


def test_conflicting_visible_year_is_rejected():
    world = _world()
    task = wiki_table_tasks.build_table_pair_tasks(world, max_tasks=1)[0]
    fact = world.facts_by_id[task.consumed_fact_ids[0]]
    evidence = wiki_evidence.check_locate_fact(world, fact)
    assert evidence.supported and evidence.header and evidence.row
    conflicting = evidence.row.replace(str(fact.value), "2025", 1)
    conflicting_world = _with_extra_row(
        world, fact.supporting_spans[0].doc_id, evidence.header, conflicting
    )
    conflicting_context = reader_view.render_documents(
        conflicting_world, task.scope.documents
    ).text
    with pytest.raises(ValueError, match="unique source year"):
        wiki_table_tasks.reader_year_cell_intervention(
            conflicting_world, task, conflicting_context
        )


def test_university_foundation_header_is_replayed_from_correct_source_page():
    pages = {
        3: wiki_adapter.PageRecord(
            3,
            "List of universities in England",
            103,
            "2026-09-24T00:00:00Z",
            '{| class="wikitable"\n! Name !! Established\n|-\n| Alpha University || 1950\n|-\n| Beta University || 1990\n|}\n',
        ),
        4: wiki_adapter.PageRecord(
            4,
            "List of universities in Wales",
            104,
            "2026-09-24T00:00:00Z",
            '{| class="wikitable"\n! Name !! Year offoundation\n|-\n| Gamma University || 1872\n|-\n| Delta University || 1960\n|}\n',
        ),
    }
    snapshot = wiki_adapter.build_snapshot(
        members=[
            wiki_adapter.Member(pageid, page.title) for pageid, page in pages.items()
        ],
        pages=pages,
        link_meta=[],
        rights={
            "text": "Creative Commons Attribution-Share Alike 4.0",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.en",
            "page_url_prefix": "https://en.wikipedia.org/wiki/",
        },
        category_title="test university lists",
        collection_kind="title_bundle",
        frozen_at="2026-09-24T00:00:00Z",
    )
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    tasks = wiki_table_tasks.build_table_pair_tasks(world)
    assert len(tasks) == 2
    assert all("Year offoundation" in task.question for task in tasks)
    for task in tasks:
        wiki_table_tasks.validate_table_pair_task(world, task)
        assert "which named entry" in task.question
        assert (
            wiki_table_tasks.reader_table_replay(
                world,
                task,
                reader_view.render_documents(world, task.scope.documents).text,
            )
            == task.answer
        )
