"""Final-reader mask audit must reject drift, truncation and metadata leaks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_unified_reader_mask import (
    audit,
    audit_all,
    audit_reader,
    verify_all,
)
from scripts.train_sft import _render_chat, assistant_prefix_length


class CharTokenizer:
    chat_template = "fixture"

    def apply_chat_template(
        self,
        messages,
        *,
        tokenize=False,
        add_generation_prompt=False,
        enable_thinking=False,
    ):
        del enable_thinking
        value = "".join(f"<{row['role']}>{row['content']}" for row in messages)
        if add_generation_prompt:
            value += "<assistant>"
        return [ord(char) for char in value] if tokenize else value

    def __call__(self, text, *, truncation=False, padding=False):
        del truncation, padding
        return {"input_ids": [ord(char) for char in text]}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict, dict]:
    merged = tmp_path / "merged"
    selected = tmp_path / "selected"
    merged.mkdir()
    selected.mkdir()
    reader = {
        "sample_id": "sample-a",
        "messages": [
            {"role": "user", "content": "Some source.\n\nQUESTION\nWhat happened?"},
            {"role": "assistant", "content": "The event ended."},
        ],
    }
    tokenizer = CharTokenizer()
    messages = reader["messages"]
    prompt = tokenizer(_render_chat(tokenizer, messages[:1], generation_prompt=True))[
        "input_ids"
    ]
    full = tokenizer(_render_chat(tokenizer, messages, generation_prompt=False))[
        "input_ids"
    ]
    input_tokens = assistant_prefix_length(prompt, full)
    index = {
        "sample_id": "sample-a",
        "split": "train",
        "source_kind": "real_wiki",
        "operation": "join",
        "full_chat_tokens": len(full),
        "input_tokens": input_tokens,
        "supervised_tokens": len(full) - input_tokens,
        "tokenizer_profile": "pinned-chat-template",
        "answer_sha256": hashlib.sha256(b"The event ended.").hexdigest(),
        "output_file": "candidate_train.jsonl",
        "row_index": 0,
    }
    (merged / "sample_index.jsonl").write_text(json.dumps(index) + "\n")
    (merged / "candidate_train.jsonl").write_text(json.dumps(reader) + "\n")
    (merged / "candidate_eval.jsonl").write_text("")
    source_manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "candidate_views": 1,
        "splits": {"train": 1},
        "files_sha256": {
            name: _sha(merged / name)
            for name in (
                "sample_index.jsonl",
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
            )
        },
    }
    (merged / "manifest.json").write_text(json.dumps(source_manifest) + "\n")
    selection_row = {
        key: value
        for key, value in index.items()
        if key not in {"input_tokens", "supervised_tokens", "tokenizer_profile"}
    }
    selection_row["selection_rank"] = 0
    (selected / "selection_index.jsonl").write_text(json.dumps(selection_row) + "\n")
    selection_manifest = {
        "schema_version": "longworld.unified-candidate-selection.v1",
        "train_ready": False,
        "input_manifest_sha256": _sha(merged / "manifest.json"),
        "selection_index_sha256": _sha(selected / "selection_index.jsonl"),
        "selected_views": 1,
    }
    (selected / "manifest.json").write_text(json.dumps(selection_manifest) + "\n")
    return merged, selected, reader, index


def test_candidate_mask_audit_binds_reader_and_keeps_candidate_status(
    tmp_path: Path,
) -> None:
    merged, selected, _, _ = _fixture(tmp_path)
    result = audit(
        merged, selected, tmp_path / "out", max_seq_len=1000, tokenizer=CharTokenizer()
    )
    assert result["audited_views"] == 1
    assert result["by_split"] == {"train": 1}
    assert result["train_ready"] is False
    row = json.loads((tmp_path / "out/audit_index.jsonl").read_text())
    assert row["loss_mask_start"] == row["input_tokens"]
    assert row["supervised_tokens"] > 0
    with pytest.raises(ValueError, match="already exists"):
        audit(merged, selected, tmp_path / "out", tokenizer=CharTokenizer())


def test_all_reader_mask_audit_replays_every_shard_row(tmp_path: Path) -> None:
    merged, _, _, _ = _fixture(tmp_path)
    result = audit_all(
        merged, tmp_path / "all", max_seq_len=1000, tokenizer=CharTokenizer()
    )
    assert result["audited_views"] == 1
    assert result["by_split"] == {"train": 1}
    assert result["scope"] == "all_candidate_masks_and_reader_shape_only"
    assert result["train_ready"] is False
    assert (
        verify_all(
            merged, tmp_path / "all", max_seq_len=1000, tokenizer=CharTokenizer()
        )
        == result
    )
    with pytest.raises(ValueError, match="new output"):
        audit_all(merged, tmp_path / "all", tokenizer=CharTokenizer())
    (tmp_path / "all/audit_index.jsonl").write_text("changed\n")
    with pytest.raises(ValueError, match="audit index changed"):
        verify_all(merged, tmp_path / "all", tokenizer=CharTokenizer())


def test_mask_audit_rejects_truncation_and_source_index_drift(tmp_path: Path) -> None:
    merged, selected, _, index = _fixture(tmp_path)
    with pytest.raises(ValueError, match="training sequence length"):
        audit(
            merged,
            selected,
            tmp_path / "short",
            max_seq_len=20,
            tokenizer=CharTokenizer(),
        )
    index["input_tokens"] -= 1
    with pytest.raises(ValueError, match="token count or assistant loss mask"):
        audit_reader(
            json.loads((merged / "candidate_train.jsonl").read_text()),
            index,
            CharTokenizer(),
            1000,
        )


def test_mask_audit_rejects_audit_field_leakage_and_tampering(tmp_path: Path) -> None:
    merged, selected, reader, index = _fixture(tmp_path)
    with pytest.raises(ValueError, match="audit-field leakage"):
        audit_reader({**reader, "proof": ["secret"]}, index, CharTokenizer(), 1000)
    reader["messages"][0]["program"] = "secret"
    with pytest.raises(ValueError, match="audit-field leakage"):
        audit_reader(reader, index, CharTokenizer(), 1000)
    (selected / "selection_index.jsonl").write_text("tampered\n")
    with pytest.raises(ValueError, match="not bound"):
        audit(merged, selected, tmp_path / "bad", tokenizer=CharTokenizer())
