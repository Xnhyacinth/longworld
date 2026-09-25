"""Selected reader materialization preserves exact shard bytes and masks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.synthesis.unified_candidate_contract import _answer_hash
from scripts import materialize_p95_balanced_selection as materialize
from scripts.train_sft import tokenize_assistant_only


class CharTokenizer:
    chat_template = None

    def __call__(self, value, **_kwargs):
        return {"input_ids": [ord(char) for char in value]}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    index_dir, shard_dir, selection_dir = (
        tmp_path / "index",
        tmp_path / "shard",
        tmp_path / "selection",
    )
    for path in (index_dir, shard_dir, selection_dir):
        path.mkdir()
    tokenizer = CharTokenizer()
    readers = {}
    entries = []
    for split, names in (("train", ("a", "b")), ("eval", ("c",))):
        readers[split] = []
        for row_index, name in enumerate(names):
            messages = [
                {"role": "user", "content": f"Source for {name}?"},
                {"role": "assistant", "content": f"answer-{name}"},
            ]
            encoded = tokenize_assistant_only(tokenizer, messages, 1000)
            full = len(encoded["input_ids"])
            supervised = sum(label != -100 for label in encoded["labels"])
            reader = {"sample_id": name, "messages": messages}
            readers[split].append(reader)
            entries.append(
                {
                    "shard": "toy",
                    "candidate": {
                        "sample_id": name,
                        "semantic_task_id": name,
                        "source_kind": "real_wiki",
                        "source_group": "toy-world",
                        "split": split,
                        "operation": "lookup",
                        "length_bin": "lt32k",
                        "output_file": f"candidate_{split}.jsonl",
                        "row_index": row_index,
                        "answer_sha256": _answer_hash(messages[1]["content"]),
                        "full_chat_tokens": full,
                        "input_tokens": full - supervised,
                        "supervised_tokens": supervised,
                        "tokenizer_profile": "pinned-chat-template",
                    },
                }
            )
        _write(shard_dir / f"candidate_{split}.jsonl", readers[split])
    index_manifest = {
        "refs_sha256": "",
        "shards": [
            {
                "name": "toy",
                "path": "../shard",
                "files_sha256": {
                    f"candidate_{split}.jsonl": _sha(
                        shard_dir / f"candidate_{split}.jsonl"
                    )
                    for split in ("train", "eval")
                },
            }
        ],
    }
    _write(index_dir / "candidate_refs.jsonl", entries)
    index_manifest["refs_sha256"] = _sha(index_dir / "candidate_refs.jsonl")
    (index_dir / "manifest.json").write_text(json.dumps(index_manifest))
    selected = [
        {"selection_rank": rank, **entries[pos]} for rank, pos in enumerate((1, 2, 0))
    ]
    _write(selection_dir / "selected_refs.jsonl", selected)
    (selection_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": materialize.SELECTION_SCHEMA,
                "train_ready": False,
                "input_manifest_sha256": _sha(index_dir / "manifest.json"),
                "input_refs_sha256": index_manifest["refs_sha256"],
                "selected_refs_sha256": _sha(selection_dir / "selected_refs.jsonl"),
                "after": {"views": 3, "independent_semantic_tasks": 3},
            }
        )
    )
    selection_config = tmp_path / "selection_config.json"
    selection_config.write_text(
        json.dumps(
            {
                "input_index": "index",
                "codeforge_proofs": [{"test_proof_pin": True}],
                "seed": 95,
                "max_per_source_group": 4,
                "max_per_cell": 4,
                "max_per_source_kind_by_split": {"train": 4, "eval": 4},
            }
        )
    )
    monkeypatch.setattr(materialize, "ROOT", tmp_path)
    monkeypatch.setattr(materialize, "verify_index", lambda _path: index_manifest)
    monkeypatch.setattr(
        materialize,
        "verify_selection",
        lambda _index, _selection, **_kwargs: json.loads(
            (selection_dir / "manifest.json").read_text()
        ),
    )
    monkeypatch.setattr(materialize, "get_tokenizer", lambda: tokenizer)
    return index_dir, selection_dir, selection_config, shard_dir, readers


def test_materializes_selected_order_and_replays_exact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, selection, config, shard, readers = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "output"
    manifest = materialize.run(index, selection, config, output, max_seq_len=1000)
    assert manifest["selected_views"] == 3
    assert manifest["splits"] == {"eval": 1, "train": 2}
    assert [
        json.loads(line)["sample_id"]
        for line in (output / "train.jsonl").read_text().splitlines()
    ] == ["b", "a"]
    assert (output / "eval.jsonl").read_text() == json.dumps(readers["eval"][0]) + "\n"
    assert (
        materialize.run(
            index, selection, config, output, max_seq_len=1000, verify_only=True
        )
        == manifest
    )
    (shard / "candidate_train.jsonl").write_text(
        (shard / "candidate_train.jsonl").read_text().replace("Source", "Tamper")
    )
    with pytest.raises(ValueError, match="selected shard reader file hash differs"):
        materialize.run(
            index, selection, config, output, max_seq_len=1000, verify_only=True
        )


def test_rejects_selection_pointer_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, selection, config, _shard, _readers = _fixture(tmp_path, monkeypatch)
    selected = [
        json.loads(line)
        for line in (selection / "selected_refs.jsonl").read_text().splitlines()
    ]
    selected[0]["candidate"]["semantic_task_id"] = "wrong"
    _write(selection / "selected_refs.jsonl", selected)
    manifest = json.loads((selection / "manifest.json").read_text())
    manifest["selected_refs_sha256"] = _sha(selection / "selected_refs.jsonl")
    (selection / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="selected pointer or task metadata differs"):
        materialize.run(index, selection, config, tmp_path / "bad", max_seq_len=1000)


def test_rejects_selection_without_pinned_codeforge_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, selection, config, _shard, _readers = _fixture(tmp_path, monkeypatch)
    value = json.loads(config.read_text())
    del value["codeforge_proofs"]
    config.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="CodeForge proof gate"):
        materialize.run(index, selection, config, tmp_path / "unqualified")
