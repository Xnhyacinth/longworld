import json
from concurrent.futures import Future

import pytest

from scripts.run_capability_world_pipeline import (
    build_messages,
    fit_bundle,
    split_for_seed,
    write_new_json,
)


class CharacterTokenizer:
    chat_template = "test-only"

    def apply_chat_template(
        self, messages, tokenize=False, add_generation_prompt=False, **kwargs
    ):
        text = "".join(f"<{m['role']}>{m['content']}" for m in messages)
        return text + ("<assistant>" if add_generation_prompt else "")

    def __call__(self, text, **kwargs):
        return {"input_ids": list(text.encode())}


def test_fitted_world_is_complete_and_every_chat_fits():
    tokenizer = CharacterTokenizer()
    bundle, rows = fit_bundle(19, "project", 32000, tokenizer)
    assert len(rows) == 6
    for row in rows:
        assert 30400 <= row["full_chat_tokens"] <= 32000
        assert row["supervised_tokens"] > 0
        assert (
            row["prompt_tokens"] + row["supervised_tokens"] == row["full_chat_tokens"]
        )
        variant = bundle if row["view"] == "factual" else bundle["counterfactual"]
        assert variant["context"] in row["messages"][0]["content"]


def test_query_is_visible_and_answer_occurs_only_in_assistant_slot():
    task = {
        "prompt": "Return the matching value.",
        "question": {"kind": "recall"},
        "answer": "secret-answer",
    }
    messages = build_messages("visible evidence", task)
    assert messages[-1] == {"role": "assistant", "content": json.dumps("secret-answer")}
    assert "secret-answer" not in messages[0]["content"]
    assert "Return the matching value." in messages[0]["content"]


def test_split_does_not_depend_on_length_or_domain_label():
    assert {split_for_seed(seed) for seed in range(20)} == {"train", "eval"}
    assert split_for_seed(3) == split_for_seed(3)


def test_existing_output_is_never_overwritten(tmp_path):
    path = tmp_path / "receipt.json"
    write_new_json(path, {"first": 1})
    with pytest.raises(FileExistsError):
        write_new_json(path, {"second": 2})
    assert json.loads(path.read_text()) == {"first": 1}


def test_impossibly_small_capacity_rejected():
    with pytest.raises(ValueError, match="capacity"):
        fit_bundle(1, "project", 10, CharacterTokenizer())


def test_failed_shard_has_reject_receipt_and_no_completed_manifest(
    tmp_path, monkeypatch
):
    import scripts.run_capability_world_pipeline as pipeline

    class FailedPool:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def submit(self, function, job):
            future = Future()
            future.set_exception(ValueError("injected semantic failure"))
            return future

    monkeypatch.setattr(pipeline, "ProcessPoolExecutor", FailedPool)
    monkeypatch.setattr(
        pipeline, "resolved_tokenizer_asset_manifest_sha256", lambda *args: "test-only"
    )
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="shards failed"):
        pipeline.run(output, [1], [65536], 1)
    assert "injected semantic failure" in (output / "rejects.json").read_text()
    assert not (output / "manifest.json").exists()
    assert not (output / "train.jsonl").exists()
