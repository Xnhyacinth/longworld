"""Reader-byte and semantic-ledger regressions for shard-reference storage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis.sharded_candidate_bank import (
    build_index,
    materialize,
    verify_index,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shard(
    path: Path, sample: str, *, split: str, task: str, group: str, answer: str = "ok"
) -> None:
    path.mkdir()
    files = {
        name: path / name
        for name in (
            "candidate_train.jsonl",
            "candidate_eval.jsonl",
            "sample_index.jsonl",
        )
    }
    for file in files.values():
        file.write_bytes(b"")
    reader = {
        "sample_id": sample,
        "messages": [
            {"role": "user", "content": "context"},
            {"role": "assistant", "content": answer},
        ],
    }
    files[f"candidate_{split}.jsonl"].write_text(
        json.dumps(reader, ensure_ascii=False) + "\n"
    )
    row = {
        "sample_id": sample,
        "task_key": json.dumps(["real_wiki", group, task], separators=(",", ":")),
        "semantic_task_id": task,
        "source_kind": "real_wiki",
        "source_group": group,
        "receipt_sha256": "a" * 64,
        "domain": "nature",
        "topic": "parks",
        "operation": "join",
        "evidence_profile": "native",
        "evidence_status": "checked",
        "dependency_status": "bounded",
        "tokenizer_profile": "pinned-chat-template",
        "split": split,
        "context_sha256": "b" * 64,
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "full_chat_tokens": 100,
        "input_tokens": 90,
        "supervised_tokens": 10,
        "length_bin": "lt32k",
        "source_length_label": None,
        "source_name": sample,
        "native_row_ref": f"native:{sample}",
        "output_file": f"candidate_{split}.jsonl",
        "row_index": 0,
    }
    files["sample_index.jsonl"].write_text(json.dumps(row) + "\n")
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "candidate_views": 1,
        "source_scoped_semantic_tasks": 1,
        "independent_semantic_tasks": 1,
        "views_by_lane": {sample: 1},
        "splits": {split: 1},
        "length_bins": {"lt32k": 1},
        "files_sha256": {name: _sha(file) for name, file in files.items()},
        "train_ready": False,
    }
    files["manifest.json"] = path / "manifest.json"
    files["manifest.json"].write_text(json.dumps(manifest) + "\n")


def test_extend_without_copy_and_materialize_exact_reader_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _shard(first, "a", split="train", task="task-a", group="group-a")
    _shard(second, "b", split="eval", task="task-b", group="group-b")
    base = tmp_path / "base-index"
    index = tmp_path / "extended-index"
    build_index(base, [("first", first)])
    result = build_index(index, [("second", second)], base_index=base)
    assert result["candidate_views"] == result["independent_semantic_tasks"] == 2
    assert not list(index.glob("candidate_train.jsonl"))
    assert result["splits"] == {"train": 1, "eval": 1}
    exported = tmp_path / "export"
    materialize(index, exported)
    assert (exported / "candidate_train.jsonl").read_bytes() == (
        first / "candidate_train.jsonl"
    ).read_bytes()
    assert (exported / "candidate_eval.jsonl").read_bytes() == (
        second / "candidate_eval.jsonl"
    ).read_bytes()
    assert verify_index(index, full_readers=True)["candidate_views"] == 2


def test_rejects_duplicate_sample_and_cross_split_semantic_task(tmp_path: Path) -> None:
    first = tmp_path / "first"
    duplicate = tmp_path / "duplicate"
    _shard(first, "a", split="train", task="task-a", group="group-a")
    _shard(duplicate, "a", split="eval", task="task-b", group="group-b")
    with pytest.raises(ValueError, match="duplicate sample ID"):
        build_index(tmp_path / "index", [("first", first), ("duplicate", duplicate)])
    _shard(tmp_path / "same-task", "c", split="eval", task="task-a", group="group-a")
    with pytest.raises(ValueError, match="source group crosses train/eval"):
        build_index(
            tmp_path / "index-2", [("first", first), ("same", tmp_path / "same-task")]
        )


def test_detects_mutated_reader_before_or_during_export(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _shard(source, "a", split="train", task="task-a", group="group-a")
    index = tmp_path / "index"
    build_index(index, [("source", source)])
    (source / "candidate_train.jsonl").write_text("corrupt\n")
    with pytest.raises(ValueError, match="shard reader changed"):
        verify_index(index, full_readers=True)
    with pytest.raises(ValueError, match="shard reader changed"):
        materialize(index, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_reference_paths_follow_project_symlink(tmp_path: Path) -> None:
    storage = tmp_path / "external-storage"
    storage.mkdir()
    (tmp_path / "data").symlink_to(storage, target_is_directory=True)
    shard = storage / "source"
    _shard(shard, "a", split="train", task="task-a", group="group-a")
    index = tmp_path / "data" / "index"
    build_index(index, [("source", shard)])
    assert verify_index(index)["candidate_views"] == 1


def test_two_train_shards_get_global_materialized_row_positions(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _shard(first, "a", split="train", task="task-a", group="group-a")
    _shard(second, "b", split="train", task="task-b", group="group-b")
    index = tmp_path / "index"
    build_index(index, [("first", first), ("second", second)])
    output = tmp_path / "output"
    materialize(index, output)
    assert (output / "candidate_train.jsonl").read_bytes() == (
        (first / "candidate_train.jsonl").read_bytes()
        + (second / "candidate_train.jsonl").read_bytes()
    )
    rows = [
        json.loads(line)
        for line in (output / "sample_index.jsonl").read_text().splitlines()
    ]
    assert [row["row_index"] for row in rows] == [0, 1]


def test_accepts_explicit_zero_view_split_in_shard_manifest(tmp_path: Path) -> None:
    shard = tmp_path / "train-only"
    _shard(shard, "a", split="train", task="task-a", group="group-a")
    manifest_path = shard / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["splits"]["eval"] = 0
    manifest_path.write_text(json.dumps(manifest) + "\n")

    index = tmp_path / "index"
    build_index(index, [("train-only", shard)])
    assert verify_index(index, full_readers=True)["splits"] == {"train": 1}
