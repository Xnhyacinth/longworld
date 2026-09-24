"""Regression tests for fail-closed Wiki candidate delta projection."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis import wiki_delta_adapter as delta
from longworld.synthesis.unified_candidate_contract import NativeCandidate


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(
    sample: str, task: str = "task-a", split: str = "train"
) -> NativeCandidate:
    return NativeCandidate(
        sample_id=sample,
        task_key=json.dumps(["real_wiki", "group-a", task], separators=(",", ":")),
        semantic_task_id=task,
        source_kind="real_wiki",
        source_group="group-a",
        receipt_sha256="a" * 64,
        domain="geography",
        topic="mountains",
        operation="table_cell_lookup",
        evidence_profile="table_checked",
        evidence_status="table_checked",
        dependency_status="source_row_removed",
        tokenizer_profile="pinned-chat-template",
        split=split,
        context_sha256="b" * 64,
        answer_sha256="c" * 64,
        full_chat_tokens=100,
        input_tokens=90,
        supervised_tokens=10,
        length_bin="lt32k",
        source_length_label=None,
    )


def _fixture(tmp_path: Path, candidates: list[NativeCandidate]):
    base = tmp_path / "base"
    base.mkdir()
    source = _candidate("sample-a")
    _write(
        base / "sample_index.jsonl",
        {
            **source.to_dict(),
            "output_file": "candidate_train.jsonl",
            "row_index": 0,
        },
    )
    _write(
        base / "candidate_train.jsonl",
        {
            "sample_id": source.sample_id,
            "messages": [
                {"role": "user", "content": "source"},
                {"role": "assistant", "content": "gold"},
            ],
        },
    )
    (base / "candidate_eval.jsonl").write_text("")
    _write(
        base / "manifest.json",
        {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": 1,
            "splits": {"train": 1},
            "files_sha256": {
                name: _sha(base / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
        },
    )

    native = tmp_path / "native"
    merged = native / "merged"
    merged.mkdir(parents=True)
    for name in ("train.jsonl", "eval.jsonl", "sample_index.jsonl", "audit.jsonl"):
        (merged / name).write_text("")
    _write(
        merged / "manifest.json",
        {
            "schema": "longworld.source-batch-merged.v1",
            "candidate_views": len(candidates),
            "files_sha256": {
                name: _sha(merged / name)
                for name in (
                    "train.jsonl",
                    "eval.jsonl",
                    "sample_index.jsonl",
                    "audit.jsonl",
                )
            },
        },
    )
    _write(native / "plan.json", {"source_pool_sha256": "d" * 64})
    _write(native / "batch/batch_manifest.json", {"candidate_rows": len(candidates)})
    _write(
        native / "result.json",
        {
            "schema": "longworld.source-pool-batch.v2",
            "source_pool_sha256": "d" * 64,
            "candidate_views": len(candidates),
            "plan_sha256": _sha(native / "plan.json"),
            "batch_manifest_sha256": _sha(native / "batch/batch_manifest.json"),
            "merged_manifest_sha256": _sha(merged / "manifest.json"),
        },
    )
    return base, native


def _fake_wiki(candidates: list[NativeCandidate]):
    for candidate in candidates:
        yield (
            candidate,
            {
                "messages": [
                    {"role": "user", "content": "source"},
                    {"role": "assistant", "content": "gold"},
                ]
            },
            f"native:{candidate.sample_id}",
        )


def test_projection_counts_only_new_semantic_tasks(tmp_path: Path, monkeypatch):
    candidates = [
        _candidate("sample-a"),
        _candidate("sample-b", task="task-b"),
        _candidate("sample-c", task="task-a"),
    ]
    # Same semantic answer in another view remains a new view, not a new task.
    candidates[2] = dataclasses.replace(candidates[2], context_sha256="e" * 64)
    base, native = _fixture(tmp_path, candidates)
    monkeypatch.setattr(delta, "_wiki", lambda lane: _fake_wiki(candidates))

    result = delta.project_delta(native, base, tmp_path / "out")

    assert result["native_views"] == 3
    assert result["exact_base_duplicates"] == 1
    assert result["new_views"] == 2
    assert result["new_independent_semantic_tasks"] == 1
    assert result["splits"] == {"train": 2}
    assert [
        row["sample_id"] for row in delta._rows(tmp_path / "out/sample_index.jsonl")
    ] == ["sample-b", "sample-c"]
    assert (
        _sha(tmp_path / "out/manifest.json")
        == delta._json(tmp_path / "out/ADAPTER_RECEIPT.json")["manifest_sha256"]
    )
    verified = delta.verify_delta(tmp_path / "out", native, base)
    assert verified["rows"] == 2
    assert verified["new_independent_semantic_tasks"] == 1


def test_same_sample_with_changed_answer_fails(tmp_path: Path, monkeypatch):
    conflicting = dataclasses.replace(_candidate("sample-a"), answer_sha256="e" * 64)
    base, native = _fixture(tmp_path, [conflicting])
    monkeypatch.setattr(delta, "_wiki", lambda lane: _fake_wiki([conflicting]))

    with pytest.raises(ValueError, match="sample ID conflicts"):
        delta.project_delta(native, base, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_same_sample_with_changed_question_fails(tmp_path: Path, monkeypatch):
    candidate = _candidate("sample-a")
    base, native = _fixture(tmp_path, [candidate])
    monkeypatch.setattr(
        delta,
        "_wiki",
        lambda lane: iter(
            [
                (
                    candidate,
                    {
                        "messages": [
                            {"role": "user", "content": "different question"},
                            {"role": "assistant", "content": "gold"},
                        ]
                    },
                    "native:sample-a",
                )
            ]
        ),
    )

    with pytest.raises(ValueError, match="sample ID conflicts"):
        delta.project_delta(native, base, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_semantic_task_cross_split_fails(tmp_path: Path, monkeypatch):
    conflicting = _candidate("sample-b", split="eval")
    base, native = _fixture(tmp_path, [conflicting])
    monkeypatch.setattr(delta, "_wiki", lambda lane: _fake_wiki([conflicting]))

    with pytest.raises(ValueError, match="split"):
        delta.project_delta(native, base, tmp_path / "out")


def test_native_hash_drift_fails_before_reader_projection(tmp_path: Path, monkeypatch):
    base, native = _fixture(tmp_path, [_candidate("sample-b", task="task-b")])
    (native / "merged/train.jsonl").write_text("changed\n")
    monkeypatch.setattr(
        delta, "_wiki", lambda lane: pytest.fail("reader should not run")
    )

    with pytest.raises(ValueError, match="native Wiki train.jsonl changed"):
        delta.project_delta(native, base, tmp_path / "out")


def test_verified_delta_rejects_rehashed_reader_or_task_count(
    tmp_path: Path, monkeypatch
):
    candidate = _candidate("sample-b", task="task-b")
    base, native = _fixture(tmp_path, [candidate])
    monkeypatch.setattr(delta, "_wiki", lambda lane: _fake_wiki([candidate]))
    output = tmp_path / "out"
    delta.project_delta(native, base, output)

    reader_path = output / "candidate_train.jsonl"
    reader = next(delta._rows(reader_path))
    reader["messages"][1]["content"] = "wrong answer"
    _write(reader_path, reader)
    manifest = delta._json(output / "manifest.json")
    manifest["files_sha256"]["candidate_train.jsonl"] = _sha(reader_path)
    _write(output / "manifest.json", manifest)
    receipt = delta._json(output / "ADAPTER_RECEIPT.json")
    receipt["manifest_sha256"] = _sha(output / "manifest.json")
    _write(output / "ADAPTER_RECEIPT.json", receipt)
    with pytest.raises(ValueError, match="differs from native reader/index"):
        delta.verify_delta(output, native, base)

    reader["messages"][1]["content"] = "gold"
    _write(reader_path, reader)
    manifest["files_sha256"]["candidate_train.jsonl"] = _sha(reader_path)
    manifest["new_independent_semantic_tasks"] = 0
    _write(output / "manifest.json", manifest)
    receipt["manifest_sha256"] = _sha(output / "manifest.json")
    receipt["new_independent_semantic_tasks"] = 0
    _write(output / "ADAPTER_RECEIPT.json", receipt)
    with pytest.raises(ValueError, match="semantic task counts differ"):
        delta.verify_delta(output, native, base)
