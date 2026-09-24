"""The review selector must preserve split/task identity and bounded quotas."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.select_unified_candidate_index import SCHEMA, select, verify_selection


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value) + "\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    merged = tmp_path / "merged"
    merged.mkdir()
    rows = []
    for position, (sample, task, group, operation, source_kind) in enumerate(
        (
            ("a", "task-a", "group-a", "join", "real_wiki"),
            ("b", "task-a", "group-a", "join", "real_wiki"),
            ("c", "task-c", "group-a", "join", "real_wiki"),
            ("d", "task-d", "group-b", "scan", "real_wiki"),
            ("e", "task-e", "group-c", "join", "controlled_simulation"),
        )
    ):
        rows.append(
            {
                "sample_id": sample,
                "task_key": f"{source_kind}:{group}:{task}",
                "semantic_task_id": task,
                "source_kind": source_kind,
                "source_group": group,
                "domain": "test",
                "topic": "test",
                "operation": operation,
                "split": "train",
                "length_bin": "64k",
                "full_chat_tokens": 70000,
                "answer_sha256": hashlib.sha256(b"gold").hexdigest(),
                "source_name": "test",
                "output_file": "candidate_train.jsonl",
                "row_index": position,
                "evidence_profile": "bounded",
                "dependency_status": "two_cells",
            }
        )
    (merged / "sample_index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    readers = [
        {
            "sample_id": row["sample_id"],
            "messages": [
                {"role": "user", "content": "context"},
                {"role": "assistant", "content": "gold"},
            ],
        }
        for row in rows
    ]
    (merged / "candidate_train.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in readers)
    )
    (merged / "candidate_eval.jsonl").write_text("")
    _write_json(
        merged / "manifest.json",
        {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": len(rows),
            "splits": {"train": len(rows)},
            "files_sha256": {
                name: _sha(merged / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
        },
    )
    policy = tmp_path / "policy.json"
    _write_json(
        policy,
        {
            "schema_version": SCHEMA,
            "selection_seed": 4,
            "source_kinds": ["real_wiki"],
            "evidence_profiles": ["bounded"],
            "dependency_statuses": ["two_cells"],
            "minimum_full_chat_tokens": 32768,
            "maximum_views_per_semantic_task": 1,
            "maximum_tasks_per_source_group": 1,
            "maximum_tasks_per_operation": 2,
        },
    )
    return merged, policy


def test_selection_is_deterministic_and_reports_all_drops(tmp_path: Path) -> None:
    merged, policy = _fixture(tmp_path)
    first = select(merged, policy, tmp_path / "first")
    second = select(merged, policy, tmp_path / "second")
    assert first == second
    assert (tmp_path / "first/selection_index.jsonl").read_bytes() == (
        tmp_path / "second/selection_index.jsonl"
    ).read_bytes()
    assert first["selected_views"] == 2
    assert first["selected_independent_tasks"] == 2
    assert first["selected_source_groups"] == 2
    assert sum(first["rejected_by_reason"].values()) == 3
    assert first["selection_scope"] == "review_priority_only"
    assert first["train_ready"] is False
    assert verify_selection(merged, policy, tmp_path / "first") == first
    with pytest.raises(ValueError, match="already exists"):
        select(merged, policy, tmp_path / "first")
    (tmp_path / "first/selection_index.jsonl").write_text("changed\n")
    with pytest.raises(ValueError, match="manifest or index changed"):
        verify_selection(merged, policy, tmp_path / "first")


def test_selection_can_limit_review_to_named_native_lanes(tmp_path: Path) -> None:
    merged, policy_path = _fixture(tmp_path)
    policy = json.loads(policy_path.read_text())
    policy["source_names"] = ["another_wave"]
    _write_json(policy_path, policy)

    result = select(merged, policy_path, tmp_path / "none")

    assert result["selected_views"] == 0
    assert result["rejected_by_reason"]["source_name_outside_policy"] == 4


def test_selection_fails_on_changed_source_or_cross_split_task(tmp_path: Path) -> None:
    merged, policy = _fixture(tmp_path)
    (merged / "candidate_train.jsonl").write_text("changed\n")
    with pytest.raises(ValueError, match="candidate file changed"):
        select(merged, policy, tmp_path / "bad")

    readers = [
        {
            "sample_id": sample,
            "messages": [
                {"role": "user", "content": "context"},
                {"role": "assistant", "content": "gold"},
            ],
        }
        for sample in ("a", "b", "c", "d", "e")
    ]
    (merged / "candidate_train.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in readers)
    )
    rows = [
        json.loads(line)
        for line in (merged / "sample_index.jsonl").read_text().splitlines()
    ]
    rows[1]["answer_sha256"] = "b" * 64
    (merged / "sample_index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    manifest = json.loads((merged / "manifest.json").read_text())
    manifest["files_sha256"]["sample_index.jsonl"] = _sha(merged / "sample_index.jsonl")
    _write_json(merged / "manifest.json", manifest)
    with pytest.raises(ValueError, match="semantic task changes split or answer"):
        select(merged, policy, tmp_path / "bad")


def test_selection_rejects_rehashed_wrong_reader_identity(tmp_path: Path) -> None:
    merged, policy = _fixture(tmp_path)
    readers = [
        json.loads(line)
        for line in (merged / "candidate_train.jsonl").read_text().splitlines()
    ]
    for reader in readers:
        reader["sample_id"] = "different"
    (merged / "candidate_train.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in readers)
    )
    manifest = json.loads((merged / "manifest.json").read_text())
    manifest["files_sha256"]["candidate_train.jsonl"] = _sha(
        merged / "candidate_train.jsonl"
    )
    _write_json(merged / "manifest.json", manifest)
    with pytest.raises(ValueError, match="final reader differs"):
        select(merged, policy, tmp_path / "bad")


def test_selection_rejects_source_group_crossing_split(tmp_path: Path) -> None:
    merged, policy = _fixture(tmp_path)
    indexes = [
        json.loads(line)
        for line in (merged / "sample_index.jsonl").read_text().splitlines()
    ]
    readers = [
        json.loads(line)
        for line in (merged / "candidate_train.jsonl").read_text().splitlines()
    ]
    moved = indexes.pop(2)
    moved.update(split="eval", output_file="candidate_eval.jsonl", row_index=0)
    for position, row in enumerate(indexes):
        row["row_index"] = position
    indexes.insert(2, moved)
    (merged / "sample_index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in indexes)
    )
    (merged / "candidate_train.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in readers[:2] + readers[3:])
    )
    _write_json(merged / "candidate_eval.jsonl", readers[2])
    manifest = json.loads((merged / "manifest.json").read_text())
    manifest["splits"] = {"train": 4, "eval": 1}
    manifest["files_sha256"] = {
        name: _sha(merged / name)
        for name in (
            "candidate_train.jsonl",
            "candidate_eval.jsonl",
            "sample_index.jsonl",
        )
    }
    _write_json(merged / "manifest.json", manifest)
    with pytest.raises(ValueError, match="source group crosses"):
        select(merged, policy, tmp_path / "bad")


def test_explicit_unmeasured_policy_matches_missing_dependency_status(
    tmp_path: Path,
) -> None:
    merged, policy_path = _fixture(tmp_path)
    indexes = [
        json.loads(line)
        for line in (merged / "sample_index.jsonl").read_text().splitlines()
    ]
    indexes[-1]["dependency_status"] = None
    (merged / "sample_index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in indexes)
    )
    manifest = json.loads((merged / "manifest.json").read_text())
    manifest["files_sha256"]["sample_index.jsonl"] = _sha(merged / "sample_index.jsonl")
    _write_json(merged / "manifest.json", manifest)
    policy = json.loads(policy_path.read_text())
    policy["source_kinds"] = ["controlled_simulation"]
    policy["dependency_statuses"] = ["unmeasured"]
    _write_json(policy_path, policy)
    result = select(merged, policy_path, tmp_path / "selected")
    assert result["selected_views"] == 1
