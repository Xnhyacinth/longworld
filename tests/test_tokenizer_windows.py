"""Exact interval counts must preserve the tokenizer's actual boundaries."""

import json
from types import SimpleNamespace

import pytest

from longworld.core.pack import SEP
from longworld.core.tokenizer_windows import certified_json_window_counter

RUNTIME_PATTERN = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}|"
    r" ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)


@pytest.fixture
def tokenizer_counter():
    tokenizers = pytest.importorskip("tokenizers")
    backend = tokenizers.Tokenizer(
        tokenizers.models.BPE(continuing_subword_prefix="", end_of_word_suffix="")
    )
    backend.normalizer = tokenizers.normalizers.NFC()
    backend.pre_tokenizer = tokenizers.pre_tokenizers.Sequence(
        [
            tokenizers.pre_tokenizers.Split(
                tokenizers.Regex(RUNTIME_PATTERN), behavior="isolated"
            ),
            tokenizers.pre_tokenizers.ByteLevel(
                add_prefix_space=False, use_regex=False
            ),
        ]
    )
    backend.train_from_iterator(
        ['{"text":"document source café"}' + SEP] * 5,
        tokenizers.trainers.BpeTrainer(
            vocab_size=320,
            initial_alphabet=tokenizers.pre_tokenizers.ByteLevel.alphabet(),
            show_progress=False,
            special_tokens=["<ADDED>"],
            continuing_subword_prefix="",
            end_of_word_suffix="",
        ),
    )
    state = json.loads(backend.to_str())
    state["post_processor"] = {
        "type": "ByteLevel",
        "add_prefix_space": False,
        "trim_offsets": False,
        "use_regex": False,
    }
    backend = tokenizers.Tokenizer.from_str(json.dumps(state))
    tokenizer = SimpleNamespace(
        backend_tokenizer=backend,
        encode=lambda text, **kwargs: backend.encode(text, **kwargs).ids,
    )

    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=False))

    count._json_window_tokenizer = tokenizer
    return count


def documents():
    return [
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for value in [
            {"text": "cafe\u0301 and 中文"},
            {"text": 'a } boundary { with quotes " and newline\n'},
            {"empty": {}, "number": 12345},
            {"text": SEP},
            {"text": "कर्म 😀"},
            {"value": [True, False, None]},
        ]
    ]


def test_every_interval_has_the_exact_reference_count(tokenizer_counter):
    docs = documents()
    fast = certified_json_window_counter(docs, tokenizer_counter)
    assert fast is not None
    for start in range(len(docs)):
        for stop in range(start + 1, len(docs) + 1):
            assert fast(start, stop) == tokenizer_counter(SEP.join(docs[start:stop]))
    fast.verify_unchanged()
    assert certified_json_window_counter(docs, lambda text: len(text)) is None


def test_window_proof_keeps_every_receipt_with_fewer_encodes(
    tokenizer_counter, monkeypatch
):
    from longworld.core import taskproof

    docs = documents()
    ids = [f"artifact-{index}" for index in range(len(docs))]
    spans, total = taskproof._artifact_token_spans(docs, tokenizer_counter)
    monkeypatch.setattr(
        taskproof,
        "_WINDOW_BANDS",
        {"4k": total // 4, "8k": total // 2, "16k": total - 1},
    )
    calls = []

    def count(text):
        calls.append(text)
        return tokenizer_counter(text)

    arguments = {
        "artifact_ids": ids,
        "documents": docs,
        "spans": spans,
        "full_tokens": total,
        "token_counter": count,
        "replay_answer": lambda selected: json.dumps(selected),
        "expected_answer": "not present",
    }
    reference = taskproof._contiguous_window_proof(**arguments)
    slow_count = len(calls)
    calls.clear()
    count._json_window_tokenizer = tokenizer_counter._json_window_tokenizer
    actual = taskproof._contiguous_window_proof(**arguments)
    assert actual == reference
    assert len(calls) < slow_count


@pytest.mark.parametrize(
    "change",
    [
        "normalizer",
        "pattern",
        "prefix_space",
        "postprocessor",
        "dropout",
        "padding",
        "truncation",
        "added_lstrip",
        "added_normalized",
        "added_non_ascii",
    ],
)
def test_unknown_runtime_retains_the_reference_path(tokenizer_counter, change):
    tokenizers = pytest.importorskip("tokenizers")
    tokenizer = tokenizer_counter._json_window_tokenizer
    state = json.loads(tokenizer.backend_tokenizer.to_str())
    if change == "normalizer":
        state["normalizer"] = {"type": "Lowercase"}
    elif change == "pattern":
        state["pre_tokenizer"]["pretokenizers"][0]["pattern"]["Regex"] = r"\S+"
    elif change == "prefix_space":
        state["pre_tokenizer"]["pretokenizers"][1]["add_prefix_space"] = True
    elif change == "postprocessor":
        state["post_processor"]["trim_offsets"] = True
    elif change == "dropout":
        state["model"]["dropout"] = 0.1
    elif change == "added_lstrip":
        state["added_tokens"][0]["lstrip"] = True
    elif change == "added_normalized":
        state["added_tokens"][0]["normalized"] = True
    elif change == "added_non_ascii":
        state["added_tokens"][0]["content"] = "é"
    tokenizer.backend_tokenizer = tokenizers.Tokenizer.from_str(json.dumps(state))
    if change == "padding":
        tokenizer.backend_tokenizer.enable_padding(length=100)
    elif change == "truncation":
        tokenizer.backend_tokenizer.enable_truncation(max_length=8)
    assert certified_json_window_counter(documents(), tokenizer_counter) is None


@pytest.mark.parametrize("document", ['{"text":"<ADDED>"}', "[]", '{ "x":1}'])
def test_added_token_or_noncanonical_document_falls_back(tokenizer_counter, document):
    assert certified_json_window_counter([document], tokenizer_counter) is None


def test_backend_mutation_cannot_certify_window_counts(tokenizer_counter):
    tokenizers = pytest.importorskip("tokenizers")
    fast = certified_json_window_counter(documents(), tokenizer_counter)
    assert fast is not None
    tokenizer_counter._json_window_tokenizer.backend_tokenizer.normalizer = (
        tokenizers.normalizers.Lowercase()
    )
    with pytest.raises(ValueError, match="tokenizer changed"):
        fast.verify_unchanged()


def test_shortcut_rejection_is_identical(tokenizer_counter, monkeypatch):
    from longworld.core import taskproof

    docs = documents()
    ids = [f"artifact-{index}" for index in range(len(docs))]
    spans, total = taskproof._artifact_token_spans(docs, tokenizer_counter)
    monkeypatch.setattr(taskproof, "_WINDOW_BANDS", {"4k": total // 2})
    messages = []
    for counter in (lambda text: tokenizer_counter(text), tokenizer_counter):
        with pytest.raises(taskproof.TaskProofError) as caught:
            taskproof._contiguous_window_proof(
                artifact_ids=ids,
                documents=docs,
                spans=spans,
                full_tokens=total,
                token_counter=counter,
                replay_answer=lambda selected: (
                    "gold" if ids[2] in selected else "unknown"
                ),
                expected_answer="gold",
            )
        messages.append(str(caught.value))
    assert messages[0] == messages[1]
