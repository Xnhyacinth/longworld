"""Behavioral checks for the read-only candidate coverage inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p113_coverage_matrix as matrix


def _entry(
    sample: str, task: str, *, split: str = "train", operation: str = "lookup"
) -> dict:
    return {
        "shard": "fixture",
        "candidate": {
            "sample_id": sample,
            "semantic_task_id": task,
            "source_kind": "real_wiki",
            "source_group": "same-world",
            "source_name": "fixture",
            "native_row_ref": "source:1",
            "receipt_sha256": "receipt",
            "domain": "science",
            "topic": "unknown",
            "operation": operation,
            "dependency_status": None,
            "length_bin": "32k",
            "split": split,
            "context_sha256": "context",
            "input_tokens": 100,
            "supervised_tokens": 5,
            "full_chat_tokens": 105,
        },
    }


def test_coverage_distinguishes_views_tasks_groups_and_missing_provenance() -> None:
    rows = [
        _entry("a", "task-a"),
        _entry("b", "task-a"),
        _entry("c", "task-b", operation="join"),
    ]
    report = matrix.coverage(rows)
    assert (
        report["views"],
        report["independent_semantic_tasks"],
        report["typed_source_groups"],
    ) == (3, 2, 1)
    assert report["multi_operation_groups"] == 1
    assert report["dimensions"]["operation"]["lookup"]["tasks"] == 1
    assert report["dimensions"]["dependency_status"]["<null>"]["views"] == 3
    assert report["source_metadata_missing_views"]["topic"] == 3
    assert report["supervised_tokens"] == 15
    assert report["source_connected_component_split"].startswith("unknown")


def test_coverage_reports_cross_split_metadata_overlap_without_certifying_components() -> (
    None
):
    report = matrix.coverage(
        [_entry("a", "task-a"), _entry("b", "task-b", split="eval")]
    )
    assert report["source_group_train_eval_overlap"] == ["real_wiki:same-world"]
    assert report["exact_context_hash_train_eval_overlap"] == 1
    assert report["source_connected_component_split"].startswith("unknown")


def test_coverage_rejects_inconsistent_token_accounting() -> None:
    row = _entry("a", "task-a")
    row["candidate"]["full_chat_tokens"] = 106
    with pytest.raises(ValueError, match="token accounting"):
        matrix.coverage([row])


def test_report_rejects_selection_row_modified_after_manifest_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_dir = tmp_path / "index"
    selection_dir = tmp_path / "selection"
    index_dir.mkdir()
    selection_dir.mkdir()
    row = _entry("a", "task-a")
    refs = index_dir / "candidate_refs.jsonl"
    refs.write_text(json.dumps(row) + "\n")
    index_manifest = index_dir / "manifest.json"
    index_manifest.write_text("{}\n")
    selected = selection_dir / "selected_refs.jsonl"
    changed = {
        **row,
        "selection_rank": 0,
        "candidate": {**row["candidate"], "domain": "changed"},
    }
    selected.write_text(json.dumps(changed) + "\n")
    selected_manifest = {
        "train_ready": False,
        "input_manifest_sha256": matrix._sha(index_manifest),
        "input_refs_sha256": matrix._sha(refs),
        "selected_refs_sha256": matrix._sha(selected),
    }
    (selection_dir / "manifest.json").write_text(json.dumps(selected_manifest))
    monkeypatch.setattr(
        matrix, "verify_index", lambda _: {"refs_sha256": matrix._sha(refs)}
    )
    with pytest.raises(ValueError, match="selected row differs"):
        matrix.build_report(index_dir, selection_dir)
