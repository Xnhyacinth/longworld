"""Behavioral checks for a coverage inventory across frozen data products."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.build_p76_batch_index import build


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_cross_product_dedup_and_future_wiki_registration(tmp_path: Path) -> None:
    p64 = _write(
        tmp_path / "p64.jsonl",
        [
            {
                "domain": "codeforge",
                "semantic_task_id": "task-a",
                "sample_id": "sample-a",
                "group_id": "repo-a",
                "split": "train",
                "full_message_tokens": 35000,
            },
        ],
    )
    p66 = _write(
        tmp_path / "p66.jsonl",
        [
            {
                "domain": "codeforge",
                "semantic_task_id": "task-a",
                "sample_id": "sample-a",
                "group_id": "repo-a",
                "split": "train",
                "full_message_tokens": 35000,
                "evidence_class": "content_backed_scoped_certificate",
            },
            {
                "domain": "cyber_osv",
                "semantic_task_id": "task-b",
                "sample_id": "sample-b",
                "group_id": "source-b",
                "split": "eval",
                "full_message_tokens": 96000,
            },
        ],
    )
    p71 = _write(
        tmp_path / "p71.jsonl",
        [
            {
                "semantic_task_id": "world:q0",
                "example_id": "world:q0",
                "world_id": "world",
                "group_id": "world",
                "family": "alias_locate",
                "split": "train",
                "full_message_tokens": 32768,
            },
        ],
    )
    p75 = _write(
        tmp_path / "p75.jsonl",
        [
            {
                "source_group": "wiki-a",
                "task_id": "TB-001",
                "example_id": "wiki-row-a",
                "family": "locate",
                "split": "eval",
                "full_chat_tokens": 200000,
            },
        ],
    )
    p76 = _write(
        tmp_path / "p76.jsonl",
        [
            {
                "source_group": "wiki-a",
                "task_id": "TB-001",
                "example_id": "wiki-row-b",
                "family": "locate",
                "split": "eval",
                "full_chat_tokens": 200000,
            },
        ],
    )
    inputs = [
        ("p76_wiki_native", p76),
        ("p71", p71),
        ("p66", p66),
        ("p64", p64),
        ("p75", p75),
    ]
    first = tmp_path / "first"
    second = tmp_path / "second"
    coverage = build(inputs, first)
    assert coverage["source_rows"] == 6
    assert coverage["unique_independent_tasks"] == 4
    assert coverage["unique_samples"] == 5
    assert coverage["duplicate_task_rows"] == 2
    assert coverage["duplicate_sample_rows"] == 1
    rows = _rows(first / "sample_index.jsonl")
    assert [row["product"] for row in rows] == [
        "p64",
        "p66",
        "p66",
        "p71",
        "p75",
        "p76_wiki_native",
    ]
    assert rows[1]["new_independent_task"] is False
    assert rows[1]["new_sample"] is False
    assert rows[-1]["new_independent_task"] is False
    assert rows[-1]["new_sample"] is True
    assert (
        coverage["dimensions"]["product"]["p76_wiki_native"]["distinct_tasks_present"]
        == 1
    )
    assert coverage["known_worlds_by_product"]["p64"] == 0
    assert rows[0]["family"] is None
    assert rows[0]["world_id"] is None
    assert rows[0]["length_bin"] == "32k_to_lt64k"
    assert rows[2]["length_bin"] == "64k_to_lt128k"
    assert json.loads((first / "manifest.json").read_text())["train_ready"] is False
    build(inputs, second)
    for name in (
        "sample_index.jsonl",
        "coverage.json",
        "rejects.jsonl",
        "manifest.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_split_collision_is_rejected_without_contaminating_counts(
    tmp_path: Path,
) -> None:
    first = _write(
        tmp_path / "first.jsonl",
        [
            {
                "domain": "codeforge",
                "semantic_task_id": "task-a",
                "sample_id": "a",
                "group_id": "repo-a",
                "split": "train",
            },
        ],
    )
    second = _write(
        tmp_path / "second.jsonl",
        [
            {
                "domain": "codeforge",
                "semantic_task_id": "task-a",
                "sample_id": "b",
                "group_id": "repo-a",
                "split": "eval",
            },
            {
                "domain": "codeforge",
                "semantic_task_id": "task-c",
                "sample_id": "c",
                "group_id": "repo-a",
                "split": "eval",
            },
            {
                "domain": "codeforge",
                "semantic_task_id": "task-d",
                "sample_id": "d",
                "group_id": "repo-b",
                "split": "eval",
            },
            {
                "domain": "codeforge",
                "semantic_task_id": "task-d",
                "sample_id": "d",
                "group_id": "repo-b",
                "split": "eval",
                "full_message_tokens": 50000,
            },
        ],
    )
    output = tmp_path / "out"
    coverage = build([("p64", first), ("p66", second)], output)
    assert coverage["source_rows"] == 5
    assert coverage["accepted_rows"] == 2
    assert coverage["unique_independent_tasks"] == 2
    assert [row["reason"] for row in _rows(output / "rejects.jsonl")] == [
        "task_split_collision",
        "source_group_split_collision",
        "sample_identity_collision",
    ]
    assert all(
        row["split"] in {"train", "eval"}
        for row in _rows(output / "sample_index.jsonl")
    )


def test_shared_world_and_wiki_cross_version_identity(tmp_path: Path) -> None:
    p71 = _write(
        tmp_path / "p71.jsonl",
        [
            {
                "semantic_task_id": "same",
                "example_id": "record",
                "group_id": "world",
                "world_id": "world",
                "split": "train",
            }
        ],
    )
    p73 = _write(
        tmp_path / "p73.jsonl",
        [
            {
                "semantic_task_id": "same",
                "example_id": "shared",
                "group_id": "world",
                "world_id": "world",
                "split": "train",
            }
        ],
    )
    p75 = _write(
        tmp_path / "p75.jsonl",
        [
            {
                "source_group": "wiki",
                "task_id": "task",
                "example_id": "old",
                "split": "train",
            }
        ],
    )
    p76 = _write(
        tmp_path / "p76.jsonl",
        [
            {
                "source_group": "wiki",
                "task_id": "task",
                "example_id": "new",
                "domain": "astronomy",
                "split": "train",
            }
        ],
    )
    coverage = build(
        [("p71", p71), ("p73", p73), ("p75", p75), ("p76_wiki_new", p76)],
        tmp_path / "out",
    )
    assert coverage["unique_independent_tasks"] == 3
    assert (
        coverage["dimensions"]["source_kind"]["simulated_shared"]["unique_tasks"] == 1
    )
