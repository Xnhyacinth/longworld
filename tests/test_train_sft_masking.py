import pytest

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
