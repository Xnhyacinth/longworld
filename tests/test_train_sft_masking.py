import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import train_sft
from scripts.train_sft import tokenize_assistant_only


class CharTokenizer:
    chat_template = "fake"

    def apply_chat_template(
        self,
        messages,
        *,
        tokenize=False,
        add_generation_prompt=False,
        enable_thinking=False,
    ):
        del enable_thinking
        text = "".join(f"<{m['role']}>{m['content']}" for m in messages)
        if add_generation_prompt:
            text += "<assistant>"
        if tokenize:
            return [ord(c) for c in text]
        return text

    def __call__(self, text, *, truncation, padding, max_length=None):
        del truncation, padding
        ids = [ord(c) for c in text]
        if max_length is not None:
            ids = ids[:max_length]
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}


def test_only_assistant_answer_tokens_receive_loss() -> None:
    tokenizer = CharTokenizer()
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "very long context"},
        {"role": "assistant", "content": "ANSWER"},
    ]
    encoded = tokenize_assistant_only(tokenizer, messages, max_length=10_000)
    labels = encoded["labels"]
    supervised = [token for token in labels if token != -100]
    assert supervised
    assert "ANSWER" in "".join(chr(token) for token in supervised)
    assert all(label == -100 for label in labels[: labels.index(supervised[0])])


def test_source_and_late_query_are_not_silently_truncated() -> None:
    tokenizer = CharTokenizer()
    messages = [
        {"role": "user", "content": "x" * 100},
        {"role": "assistant", "content": "ANSWER"},
    ]
    with pytest.raises(ValueError, match="source and query exceed"):
        tokenize_assistant_only(tokenizer, messages, max_length=20)


def test_answer_larger_than_the_sequence_is_rejected() -> None:
    tokenizer = CharTokenizer()
    messages = [
        {"role": "user", "content": "context"},
        {"role": "assistant", "content": "ANSWER" * 20},
    ]
    with pytest.raises(ValueError, match="assistant answer exceeds"):
        tokenize_assistant_only(tokenizer, messages, max_length=20)


def test_diagnostic_train_cli_rejects_empty_post_filter_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "release"
    output = tmp_path / "sft"
    monkeypatch.setattr(
        train_sft,
        "load_release_product",
        lambda *_a, **_k: SimpleNamespace(
            train_rows=[
                {
                    "view": "full",
                    "query_timing": "first",
                    "length_bucket": "16k",
                }
            ]
        ),
    )
    monkeypatch.setattr(train_sft, "sft_row_errors", lambda _row: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_sft.py",
            "--condition",
            "B1",
            "--data",
            str(data),
            "--out-dir",
            str(output),
            "--length-bucket",
            "64k",
            "--release-profile",
            "p3-probe-12-v1",
            "--diagnostic-only",
            "--prepare-only",
        ],
    )

    with pytest.raises(SystemExit, match="no rows remain after filtering"):
        train_sft.main()

    assert not output.exists()
