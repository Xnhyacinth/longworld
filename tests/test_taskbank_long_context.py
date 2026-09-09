import pytest

from longworld.core.taskbank_context import render_document
from longworld.core.taskbank_long_context import (
    assemble_plan,
    choose_primary,
    context_options,
)
from tests.test_finance_taskbank import world as source_world


@pytest.fixture
def world(tmp_path, monkeypatch):
    return source_world.__wrapped__(tmp_path, monkeypatch)


class Tokenizer:
    def encode(self, text, **kwargs):
        return range(len(text) * 100)


def test_complete_source_plans_no_duplicate_documents_and_primary(world):
    documents = world["documents"][:3]
    rendered = {d["record_id"]: render_document(d) for d in documents}
    options = context_options(
        documents, rendered, [d["record_id"] for d in documents], Tokenizer()
    )
    assert options
    for option in options:
        assert len(option["plan"]) == 3
        assert all(option["text"].count(d["source_url"]) == 1 for d in documents)
        assert 32768 < option["tokens"] <= 261120
        assert (
            option["text"] == assemble_plan(documents, rendered, option["plan"])["text"]
        )
    assert choose_primary(options, 3) in options
    with pytest.raises(ValueError, match="duplicated"):
        assemble_plan(
            documents,
            rendered,
            [{"record_id": documents[0]["record_id"], "mode": "full"}] * 2,
        )


def test_capacity_does_not_claim_exact_band_or_pad(world):
    doc = world["documents"][0]
    rendered = {doc["record_id"]: render_document(doc)}
    options = context_options([doc], rendered, [doc["record_id"]], Tokenizer())
    assert all(o["tokens"] == len(o["text"]) * 100 for o in options)
    assert any(not o["matches_exact_token_range"] for o in options)


def test_unknown_source_and_mode_fail(world):
    doc = world["documents"][0]
    rendered = {doc["record_id"]: render_document(doc)}
    with pytest.raises(ValueError):
        assemble_plan([doc], rendered, [{"record_id": "missing", "mode": "full"}])
    with pytest.raises(ValueError):
        assemble_plan([doc], rendered, [{"record_id": doc["record_id"], "mode": "pad"}])


def test_long_tokenization_never_observes_source_credentials(world, monkeypatch):
    import os

    from longworld.core.finance_taskbank_v2 import compile_taskbank
    from scripts.materialize_finance_taskbank_v2 import _samples

    monkeypatch.setenv("LONGWORLD_SOURCE_ATTESTATION_KEY", "source-test-key-" * 4)

    class GuardedTokenizer(Tokenizer):
        def encode(self, text, **kwargs):
            if "LONGWORLD_SOURCE_ATTESTATION_KEY" in os.environ:
                raise AssertionError("source credentials reached tokenizer")
            return super().encode(text, **kwargs)

    docs = {d["record_id"]: d for d in world["documents"]}
    rendered = {identity: render_document(doc) for identity, doc in docs.items()}
    assert list(
        _samples(
            {"split": "train"},
            world,
            compile_taskbank(world),
            docs,
            rendered,
            GuardedTokenizer(),
            [],
        )
    )
