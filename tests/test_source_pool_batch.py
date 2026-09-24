"""The source pool expands deterministically and never invents task support."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.synthesis import (
    reader_view,
    wiki_adapter,
    wiki_table_lookup,
    wiki_table_scan,
    wiki_world_bridge,
)
from longworld.synthesis.source_batch_merge import merge
from scripts.run_source_pool_batch import _planned, _snapshot

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/p76_source_pool_v2.json"


def test_real_source_pool_expands_supported_cells_only() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if any(
        not (ROOT / item["snapshot"]["path"]).is_file() for item in config["sources"]
    ):
        pytest.skip("frozen source pool is not mounted")
    first, skipped = _planned(config)
    second, repeated_skips = _planned(config)
    assert (first, skipped) == (second, repeated_skips)
    assert len(config["sources"]) == 14
    assert {job["recipe"] for job in first["jobs"]} == {
        "wiki_table_pair",
        "wiki_table_scan",
        "wiki_table_lookup",
    }
    assert len({job["name"] for job in first["jobs"]}) == len(first["jobs"])
    assert any(item["source"] == "wiki_bridges_lists" for item in skipped)
    assert all(job["snapshot"]["revisions"] for job in first["jobs"])
    assert all(job["snapshot"]["snapshot_id"] for job in first["jobs"])


def test_source_pool_rejects_unpinned_and_unsupported_recipe() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["requested_recipes"] = ["imaginary_task"]
    with pytest.raises(ValueError, match="requested_recipes"):
        _planned(config)
    config["requested_recipes"] = ["wiki_table_pair"]
    config["sources"][0]["snapshot"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="snapshot pin mismatch"):
        _planned(config)


def test_snapshot_metadata_must_match_pinned_source(tmp_path: Path) -> None:
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(
            {
                "snapshot_id": "test",
                "source": {"revisions": {"One": 1}},
                "documents": [{"title": "Two"}],
            }
        ),
        encoding="utf-8",
    )
    from scripts.run_p76_source_batch import _sha

    with pytest.raises(ValueError, match="snapshot metadata mismatch"):
        _snapshot(ROOT, {"path": str(path), "sha256": _sha(path)})


def test_table_scan_uses_visible_aliases_in_final_reader_answer() -> None:
    path = ROOT / "data/capability_records/p76_wiki_titles_v4/astronomy_snapshot.json"
    if not path.is_file():
        pytest.skip("frozen Wiki snapshot is not mounted")
    world = wiki_world_bridge.snapshot_to_world(json.loads(path.read_text()))
    target = next(
        doc
        for doc in world.documents
        if doc.title == "List of astronomical observatories"
    )
    tasks = wiki_table_scan.build_scan_tasks(world, target.doc_id, max_tasks=16)
    assert len(tasks) >= 8
    for task in tasks:
        reader = reader_view.render_documents(world, task.scope.documents)
        replayed, parsed, _ = wiki_table_scan.reader_interval_replay(
            world, task, reader.text
        )
        assert replayed == task.answer
        assert replayed["count"] < len(parsed.eligible)


def test_lookup_reads_and_masks_final_visible_table_cell() -> None:
    path = ROOT / "data/capability_records/p76_wiki_titles_v6/hospitals_snapshot.json"
    if not path.is_file():
        pytest.skip("frozen Wiki snapshot is not mounted")
    world = wiki_world_bridge.snapshot_to_world(json.loads(path.read_text()))
    typed, _ = wiki_world_bridge.structurally_typed_world(world)
    tasks = wiki_table_lookup.build_lookup_tasks(typed, max_tasks=8)
    assert len(tasks) == 8
    admitted = rejected_shortcuts = 0
    for task in tasks:
        context = reader_view.render_documents(typed, task.scope.documents)
        observed, _ = wiki_table_lookup.reader_lookup(typed, task, context.text)
        assert observed == task.answer_rendered
        try:
            check = wiki_table_lookup.reader_cell_intervention(
                typed, task, context.text
            )
        except ValueError as error:
            assert "equivalent_subject_answer_elsewhere" in str(error)
            rejected_shortcuts += 1
        else:
            assert check["status"] == "scoped_named_table_cell_removed"
            admitted += 1
    assert admitted > 0 and rejected_shortcuts > 0


def test_linked_region_cell_has_source_fact_and_visible_alias_answer() -> None:
    path = (
        ROOT / "data/capability_records/p76_wiki_expansion_v2/snapshots/"
        "wiki_zoos_snapshot.json"
    )
    if not path.is_file():
        pytest.skip("frozen expansion source is not mounted")
    world = wiki_world_bridge.snapshot_to_world(json.loads(path.read_text()))
    tasks = wiki_table_lookup.build_lookup_tasks(world, max_tasks=8)
    assert tasks
    assert any(
        world.facts_by_id[task.consumed_fact_ids[0]].value_type == "entity"
        for task in tasks
    )
    for task in tasks:
        assert wiki_table_lookup.execute_lookup(world, task.program) == task.answer
        context = reader_view.render_documents(world, task.scope.documents)
        assert (
            wiki_table_lookup.reader_lookup(world, task, context.text)[0] == task.answer
        )


def test_nonleading_name_column_keeps_stadium_as_row_subject() -> None:
    body = (
        "# List of stadiums\n"
        "# | Image | Stadium | Capacity | City | Opened\n"
        "1 | photo | Arena X | 1000 | Tirana | 1990\n"
    )
    _title, header, row = wiki_adapter.structured_lines(body)
    assert wiki_adapter._row_subject(row, header.cells, "List of stadiums") == "Arena X"
    assert (
        wiki_table_lookup._cells(body, "Arena X", "City", "located in")[0][0]
        == "Tirana"
    )
    assert (
        wiki_table_lookup._cells(body, "Arena X", "Opened", "opened in")[0][0] == 1990
    )


def test_merge_deduplicates_same_semantic_task_and_length(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    root.mkdir()
    for name, group in (("a", "source_a"), ("b", "source_b")):
        job = root / name
        job.mkdir()
        (job / "manifest.json").write_text(
            json.dumps({"split": "train", "domain": "astronomy", "candidate_rows": 1})
        )
        (job / "train.jsonl").write_text(
            json.dumps(
                {
                    "example_id": f"sample_{name}",
                    "messages": [
                        {"role": "user", "content": "q"},
                        {"role": "assistant", "content": "answer"},
                    ],
                }
            )
            + "\n"
        )
        (job / "sample_index.jsonl").write_text(
            json.dumps(
                {
                    "split": "train",
                    "domain": "astronomy",
                    "topic": "observatories",
                    "task_id": "same",
                    "example_id": f"sample_{name}",
                    "length_bin": "32k",
                    "full_chat_tokens": 40000,
                    "task_type": "scan",
                    "source_group": group,
                }
            )
            + "\n"
        )
        (job / "audit.jsonl").write_text(
            json.dumps({"example_id": f"sample_{name}"}) + "\n"
        )
    output = tmp_path / "merged"
    summary = merge(root, output, 2)
    assert summary["source_views"] == 2
    assert summary["candidate_views"] == summary["independent_tasks"] == 1
    assert summary["duplicate_views_removed"] == 1
    assert len((output / "train.jsonl").read_text().splitlines()) == 1
    (root / "b/audit.jsonl").write_text("{}\n")
    bad_output = tmp_path / "bad_merged"
    with pytest.raises(ValueError, match="example ID mismatch"):
        merge(root, bad_output, 2)
    assert not bad_output.exists()


def test_merge_maps_native_policy_to_actual_token_bucket(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    job = root / "job"
    job.mkdir(parents=True)
    (job / "manifest.json").write_text(
        json.dumps({"split": "eval", "domain": "education", "candidate_rows": 1})
    )
    (job / "eval.jsonl").write_text(
        json.dumps(
            {
                "example_id": "sample_one",
                "messages": [{"content": "q"}, {"content": "a"}],
            }
        )
        + "\n"
    )
    (job / "sample_index.jsonl").write_text(
        json.dumps(
            {
                "split": "eval",
                "domain": "education",
                "topic": "universities",
                "task_id": "one",
                "example_id": "sample_one",
                "length_bin": "native",
                "full_chat_tokens": 17500,
                "task_type": "pair",
                "source_group": "source_one",
            }
        )
        + "\n"
    )
    (job / "audit.jsonl").write_text(json.dumps({"example_id": "sample_one"}) + "\n")
    result = merge(root, tmp_path / "merged", 1)
    assert result["length_bins"] == {"lt32k": 1}
    row = json.loads((tmp_path / "merged/sample_index.jsonl").read_text())
    assert row["length_bin"] == "lt32k"
    assert row["source_length_label"] == "native"
