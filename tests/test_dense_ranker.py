from __future__ import annotations

import hashlib
import json
import sys
import types
from collections import UserDict
from pathlib import Path
from typing import Any

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.pack import SEP
from longworld.core.promotion import DENSE_RANKING_PURPOSE, candidate_sha256

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rank_candidates_dense import (
    MODEL_BACKEND,
    rank_candidates,
)

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
KEY = b"longworld-dense-ranker-test-key-32-bytes"


def _candidate() -> dict[str, Any]:
    documents = ["exact answer document", "near document", "another near document"]
    return {
        "query_id": "query-1",
        "question": "Which document has the answer?",
        "document_context": SEP.join(documents),
        "artifact_classification": [
            {"artifact_id": artifact_id}
            for artifact_id in ("artifact-z", "artifact-a", "artifact-m")
        ],
        "data_stage": "candidate",
    }


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.tokenizer = FakeTokenizer()

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append((texts, kwargs))
        return [
            [3.0, 4.0]
            if "Which document" in text or "exact answer" in text or set(text) == {"x"}
            else [0.0, 10.0]
            for text in texts
        ]


class FakeTokenizer:
    def __call__(self, text: str, **_kwargs: Any) -> dict[str, list[int]]:
        return {"input_ids": [ord(character) for character in text]}

    def decode(self, token_ids: list[int], **_kwargs: Any) -> str:
        return "".join(chr(token_id) for token_id in token_ids)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_dense_ranker_binds_full_pool_and_stably_ranks_float32_dot(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [candidate])
    model = FakeModel()

    count = rank_candidates(
        candidates_path,
        output_path,
        model_id=MODEL_ID,
        revision=REVISION,
        batch_size=8,
        model=model,
        attestation_key=KEY,
    )

    assert count == 1
    ranking = json.loads(output_path.read_text(encoding="utf-8"))
    assert ranking["schema_version"] == "dense-ranking-v2"
    assert ranking["ranker_type"] == "dense_embedding"
    assert verify_attestation(ranking, KEY, purpose=DENSE_RANKING_PURPOSE)
    assert ranking["query_id"] == candidate["query_id"]
    assert ranking["candidate_sha256"] == candidate_sha256(candidate)
    assert (
        ranking["query_sha256"]
        == hashlib.sha256(candidate["question"].encode()).hexdigest()
    )
    assert ranking["model"] == {
        "provider": "huggingface",
        "model_id": MODEL_ID,
        "revision": REVISION,
        "backend": MODEL_BACKEND,
        "score_metric": "dot_product",
        "chunking": {
            "strategy": "tokenizer_token_windows",
            "max_tokens": 192,
            "overlap_tokens": 32,
            "aggregation": "max_similarity",
        },
    }
    assert [row["artifact_id"] for row in ranking["artifacts"]] == [
        "artifact-z",
        "artifact-a",
        "artifact-m",
    ]
    assert [row["rank"] for row in ranking["artifacts"]] == [1, 2, 3]
    assert [row["chunk_count"] for row in ranking["artifacts"]] == [1, 1, 1]
    assert ranking["artifacts"][1]["score"] == ranking["artifacts"][2]["score"]
    assert (
        ranking["artifacts"][0]["text_sha256"]
        == hashlib.sha256(b"exact answer document").hexdigest()
    )
    assert model.calls == [
        (
            [candidate["question"], *candidate["document_context"].split(SEP)],
            {
                "batch_size": 8,
                "convert_to_numpy": True,
                "normalize_embeddings": True,
                "precision": "float32",
                "show_progress_bar": False,
            },
        )
    ]


def test_dense_ranker_reuses_exact_text_embeddings_across_candidate_views(
    tmp_path: Path,
) -> None:
    first = _candidate()
    second = {**_candidate(), "query_id": "query-2"}
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [first, second])
    model = FakeModel()

    assert (
        rank_candidates(
            candidates_path,
            output_path,
            model_id=MODEL_ID,
            revision=REVISION,
            model=model,
            attestation_key=KEY,
        )
        == 2
    )

    assert len(model.calls) == 1


def test_dense_ranker_chunks_long_artifacts_without_silent_truncation(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    candidate["document_context"] = SEP.join(["x" * 500, "near", "other"])
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [candidate])
    model = FakeModel()

    rank_candidates(
        candidates_path,
        output_path,
        model_id=MODEL_ID,
        revision=REVISION,
        model=model,
        attestation_key=KEY,
    )

    ranking = json.loads(output_path.read_text(encoding="utf-8"))
    long_artifact = next(
        row for row in ranking["artifacts"] if row["artifact_id"] == "artifact-z"
    )
    assert long_artifact["chunk_count"] == 4
    assert max(len(text) for text in model.calls[0][0]) <= 192


def test_dense_ranker_accepts_tokenizer_mapping_outputs(tmp_path: Path) -> None:
    class MappingTokenizer(FakeTokenizer):
        def __call__(self, text: str, **_kwargs: Any) -> UserDict[str, list[int]]:
            return UserDict({"input_ids": [ord(character) for character in text]})

    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [_candidate()])
    model = FakeModel()
    model.tokenizer = MappingTokenizer()

    assert (
        rank_candidates(
            candidates_path,
            output_path,
            model_id=MODEL_ID,
            revision=REVISION,
            model=model,
            attestation_key=KEY,
        )
        == 1
    )


@pytest.mark.parametrize("revision", ["main", "1110a243"])
def test_dense_ranker_requires_full_commit_revision(
    tmp_path: Path, revision: str
) -> None:
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [_candidate()])

    with pytest.raises(ValueError, match="40-character commit SHA"):
        rank_candidates(
            candidates_path,
            output_path,
            model_id=MODEL_ID,
            revision=revision,
            model=FakeModel(),
            attestation_key=KEY,
        )

    assert not output_path.exists()


def test_dense_ranker_rejects_unbound_documents_without_touching_output(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    candidate["artifact_classification"].pop()
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [candidate])
    output_path.write_text("existing output\n", encoding="utf-8")

    with pytest.raises(ValueError, match="document count"):
        rank_candidates(
            candidates_path,
            output_path,
            model_id=MODEL_ID,
            revision=REVISION,
            model=FakeModel(),
            attestation_key=KEY,
        )

    assert output_path.read_text(encoding="utf-8") == "existing output\n"


def test_dense_ranker_rejects_duplicate_candidate_digest(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    candidate = _candidate()
    _write_jsonl(candidates_path, [candidate, candidate])

    with pytest.raises(ValueError, match="duplicate candidate_sha256"):
        rank_candidates(
            candidates_path,
            output_path,
            model_id=MODEL_ID,
            revision=REVISION,
            model=FakeModel(),
            attestation_key=KEY,
        )

    assert not output_path.exists()


def test_default_loader_disables_remote_code_and_requires_safetensors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeSentenceTransformer(FakeModel):
        def __init__(self, model_id: str, **kwargs: Any) -> None:
            super().__init__()
            calls.append((model_id, kwargs))

    module = types.ModuleType("sentence_transformers")
    module.__version__ = "6.0.0"
    module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [_candidate()])

    rank_candidates(
        candidates_path,
        output_path,
        model_id=MODEL_ID,
        revision=REVISION,
        attestation_key=KEY,
    )

    assert calls == [
        (
            MODEL_ID,
            {
                "revision": REVISION,
                "trust_remote_code": False,
                "model_kwargs": {"use_safetensors": True},
            },
        )
    ]


def test_default_loader_reports_missing_or_wrong_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = types.ModuleType("sentence_transformers")
    module.__version__ = "5.1.2"
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    candidates_path = tmp_path / "candidates.jsonl"
    _write_jsonl(candidates_path, [_candidate()])

    with pytest.raises(RuntimeError, match="sentence-transformers==6.0.0"):
        rank_candidates(
            candidates_path,
            tmp_path / "rankings.jsonl",
            model_id=MODEL_ID,
            revision=REVISION,
            attestation_key=KEY,
        )


def test_dense_model_never_sees_attestation_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_names = (
        ATTESTATION_ENV,
        ATTESTATION_ENVIRONMENT_ENV,
        *ROLE_KEY_ENVS.values(),
        *ROLE_KEY_ID_ENVS.values(),
    )
    for index, name in enumerate(secret_names):
        monkeypatch.setenv(name, f"secret-{index}")

    class InspectingModel(FakeModel):
        def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            assert all(name not in __import__("os").environ for name in secret_names)
            return super().encode(texts, **kwargs)

    candidates_path = tmp_path / "candidates.jsonl"
    output_path = tmp_path / "rankings.jsonl"
    _write_jsonl(candidates_path, [_candidate()])

    rank_candidates(
        candidates_path,
        output_path,
        model_id=MODEL_ID,
        revision=REVISION,
        model=InspectingModel(),
        attestation_key=KEY,
    )

    assert all(
        __import__("os").environ[name] == f"secret-{index}"
        for index, name in enumerate(secret_names)
    )
