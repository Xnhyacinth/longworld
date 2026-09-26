"""Exact prior readers may be dropped; identity collisions must stop a campaign."""

import hashlib
import json

import pytest

from scripts import p112_unified_novelty as novelty


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _candidate(sample_id, position, answer="answer"):
    return {
        "sample_id": sample_id,
        "source_kind": "grounded_simulation",
        "source_group": "rfc",
        "semantic_task_id": sample_id,
        "split": "train",
        "operation": "rule_set",
        "context_sha256": "context-" + sample_id,
        "answer_sha256": answer,
        "full_chat_tokens": 100,
        "input_tokens": 90,
        "supervised_tokens": 10,
        "length_bin": "lt32k",
        "row_index": position,
    }


def _source(root, rows):
    root.mkdir()
    _write(root / "sample_index.jsonl", rows)
    _write(
        root / "candidate_train.jsonl",
        [{"sample_id": row["sample_id"], "messages": []} for row in rows],
    )
    _write(root / "candidate_eval.jsonl", [])
    files = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "sample_index.jsonl",
            "candidate_train.jsonl",
            "candidate_eval.jsonl",
        )
    }
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "longworld.unified-candidates.v1",
                "candidate_views": len(rows),
                "splits": {"train": len(rows)},
                "files_sha256": files,
            }
        )
    )


def test_drops_only_exact_prior_sample_and_replays(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    old_shard = tmp_path / "old_shard"
    old_shard.mkdir()
    _write(old_shard / "candidate_train.jsonl", [{"sample_id": "same", "messages": []}])
    (base / "manifest.json").write_text(
        json.dumps({"shards": [{"name": "old", "path": "../old_shard"}]})
    )
    old = _candidate("same", 0)
    old["output_file"] = "candidate_train.jsonl"
    _write(base / "candidate_refs.jsonl", [{"shard": "old", "candidate": old}])
    monkeypatch.setattr(
        novelty,
        "verify_index",
        lambda _, full_readers=False: {"refs_sha256": "base"},
    )
    source = tmp_path / "source"
    _source(source, [_candidate("same", 0), _candidate("new", 1)])
    output = tmp_path / "filtered"
    result = novelty.filter_novel(base, source, output)
    assert result["candidate_views"] == 1
    assert result["dropped_exact_prior_samples"] == 1
    assert json.loads((output / "sample_index.jsonl").read_text())["sample_id"] == "new"
    assert novelty.filter_novel(base, source, output, verify_only=True) == result

    conflicting = tmp_path / "conflicting"
    _source(conflicting, [_candidate("same", 0, answer="different")])
    with pytest.raises(ValueError, match="collides with a different prior"):
        novelty.filter_novel(base, conflicting, tmp_path / "rejected")

    different_question = tmp_path / "different_question"
    _source(different_question, [_candidate("same", 0)])
    _write(
        different_question / "candidate_train.jsonl",
        [{"sample_id": "same", "messages": [{"role": "user", "content": "different"}]}],
    )
    manifest_path = different_question / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"]["candidate_train.jsonl"] = hashlib.sha256(
        (different_question / "candidate_train.jsonl").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="different final reader"):
        novelty.filter_novel(base, different_question, tmp_path / "question_rejected")
