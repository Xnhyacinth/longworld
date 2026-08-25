#!/usr/bin/env python3
"""Rank every candidate artifact with a pinned dense embedding model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    sanitized_attestation_environment,
)
from longworld.core.pack import SEP
from longworld.core.promotion import DENSE_RANKING_PURPOSE, candidate_sha256

MODEL_BACKEND = "sentence-transformers-6.0.0"
_MODEL_VERSION = "6.0.0"
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
CHUNK_MAX_TOKENS = 192
CHUNK_OVERLAP_TOKENS = 32


class Encoder(Protocol):
    tokenizer: Any

    def encode(self, texts: list[str], **kwargs: Any) -> Any: ...


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    if not rows:
        raise ValueError("candidate input is empty")
    return rows


def _artifact_bindings(candidate: dict[str, Any]) -> list[dict[str, str]]:
    classifications = candidate.get("artifact_classification")
    context = candidate.get("document_context")
    if not isinstance(classifications, list) or not classifications:
        raise ValueError("candidate has no artifact classification")
    if not isinstance(context, str) or not context.strip():
        raise ValueError("candidate has no document context")
    documents = context.split(SEP)
    if len(documents) != len(classifications):
        raise ValueError("artifact classification and document count differ")

    bindings: list[dict[str, str]] = []
    seen: set[str] = set()
    for classification, text in zip(classifications, documents):
        if not isinstance(classification, dict):
            raise TypeError("candidate artifact classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        if not artifact_id or artifact_id in seen:
            raise ValueError("candidate artifact ids are missing or duplicated")
        if not text.strip():
            raise ValueError(f"candidate artifact text is empty: {artifact_id}")
        seen.add(artifact_id)
        bindings.append(
            {
                "artifact_id": artifact_id,
                "text": text,
                "text_sha256": _sha256_text(text),
            }
        )
    return bindings


def _float32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _normalize_float32(raw: Any) -> list[float]:
    try:
        vector = [_float32(float(value)) for value in raw]
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("dense model returned an invalid embedding") from error
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("dense model returned an invalid embedding")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm == 0.0:
        raise ValueError("dense model returned a zero or invalid embedding")
    return [_float32(value / norm) for value in vector]


def _dot_float32(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("dense model returned inconsistent embedding dimensions")
    score = 0.0
    for left_value, right_value in zip(left, right):
        score = _float32(score + _float32(left_value * right_value))
    return score


def _chunk_text(text: str, tokenizer: Any) -> list[str]:
    encoded = tokenizer(text, add_special_tokens=False, truncation=False)
    token_ids = encoded.get("input_ids") if isinstance(encoded, Mapping) else None
    if not isinstance(token_ids, list) or any(
        not isinstance(token_id, int) for token_id in token_ids
    ):
        raise ValueError("dense tokenizer returned invalid token ids")
    if not token_ids or len(token_ids) <= CHUNK_MAX_TOKENS:
        return [text]
    stride = CHUNK_MAX_TOKENS - CHUNK_OVERLAP_TOKENS
    chunks = [
        tokenizer.decode(
            token_ids[start : start + CHUNK_MAX_TOKENS],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        for start in range(0, len(token_ids), stride)
    ]
    chunks = [str(chunk) for chunk in chunks if str(chunk).strip()]
    if not chunks:
        raise ValueError("dense tokenizer produced no nonempty chunks")
    return chunks


def _load_model(model_id: str, revision: str) -> Encoder:
    try:
        import sentence_transformers
    except ImportError as error:
        raise RuntimeError(
            "sentence-transformers==6.0.0 is required for dense ranking"
        ) from error
    if getattr(sentence_transformers, "__version__", None) != _MODEL_VERSION:
        raise RuntimeError(
            "sentence-transformers==6.0.0 is required for reproducible dense ranking"
        )
    return sentence_transformers.SentenceTransformer(
        model_id,
        revision=revision,
        trust_remote_code=False,
        model_kwargs={"use_safetensors": True},
    )


def _rank_candidate(
    candidate: dict[str, Any],
    model: Encoder,
    *,
    model_id: str,
    revision: str,
    batch_size: int,
    embedding_cache: dict[str, list[float]],
) -> dict[str, Any]:
    question = candidate.get("question")
    query_id = candidate.get("query_id")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("candidate question is missing")
    if not isinstance(query_id, str) or not query_id:
        raise ValueError("candidate query id is missing")
    bindings = _artifact_bindings(candidate)
    query_chunks = _chunk_text(question, model.tokenizer)
    artifact_chunks = [
        _chunk_text(binding["text"], model.tokenizer) for binding in bindings
    ]
    texts = [*query_chunks, *(chunk for chunks in artifact_chunks for chunk in chunks)]
    digests = [_sha256_text(value) for value in texts]
    missing: dict[str, str] = {}
    for digest, value in zip(digests, texts):
        if digest not in embedding_cache:
            missing.setdefault(digest, value)
    if missing:
        raw_embeddings = model.encode(
            list(missing.values()),
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision="float32",
            show_progress_bar=False,
        )
        try:
            normalized = [_normalize_float32(raw) for raw in raw_embeddings]
        except TypeError as error:
            raise ValueError("dense model returned invalid embeddings") from error
        if len(normalized) != len(missing):
            raise ValueError("dense model returned the wrong number of embeddings")
        embedding_cache.update(zip(missing, normalized))
    embeddings = [embedding_cache[digest] for digest in digests]

    query_embeddings = embeddings[: len(query_chunks)]
    artifact_embeddings: list[list[list[float]]] = []
    cursor = len(query_chunks)
    for chunks in artifact_chunks:
        artifact_embeddings.append(embeddings[cursor : cursor + len(chunks)])
        cursor += len(chunks)
    scored = [
        (
            binding,
            max(
                _dot_float32(query_embedding, artifact_embedding)
                for query_embedding in query_embeddings
                for artifact_embedding in chunk_embeddings
            ),
            len(chunks),
        )
        for binding, chunks, chunk_embeddings in zip(
            bindings, artifact_chunks, artifact_embeddings
        )
    ]
    scored.sort(key=lambda item: -item[1])
    return {
        "schema_version": "dense-ranking-v2",
        "ranker_type": "dense_embedding",
        "query_id": query_id,
        "candidate_sha256": candidate_sha256(candidate),
        "query_sha256": _sha256_text(question),
        "model": {
            "provider": "huggingface",
            "model_id": model_id,
            "revision": revision,
            "backend": MODEL_BACKEND,
            "score_metric": "dot_product",
            "chunking": {
                "strategy": "tokenizer_token_windows",
                "max_tokens": CHUNK_MAX_TOKENS,
                "overlap_tokens": CHUNK_OVERLAP_TOKENS,
                "aggregation": "max_similarity",
            },
        },
        "artifacts": [
            {
                "rank": rank,
                "artifact_id": binding["artifact_id"],
                "text_sha256": binding["text_sha256"],
                "score": score,
                "chunk_count": chunk_count,
            }
            for rank, (binding, score, chunk_count) in enumerate(scored, start=1)
        ],
    }


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def rank_candidates(
    candidates_path: Path,
    output_path: Path,
    *,
    model_id: str,
    revision: str,
    batch_size: int = 32,
    model: Encoder | None = None,
    attestation_key: bytes | None = None,
) -> int:
    """Rank every document for every unique candidate, then atomically write JSONL."""
    if not model_id.strip():
        raise ValueError("model id is required")
    if not _COMMIT_SHA.fullmatch(revision):
        raise ValueError("model revision must be a full 40-character commit SHA")
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    key = (
        attestation_key
        if attestation_key is not None
        else attestation_key_from_env(DENSE_RANKING_PURPOSE)
    )
    if key is None:
        raise ValueError("LONGWORLD_ATTESTATION_KEY must contain at least 32 bytes")

    candidates = _read_jsonl(candidates_path)
    seen: set[str] = set()
    for candidate in candidates:
        digest = candidate_sha256(candidate)
        if digest in seen:
            raise ValueError(f"duplicate candidate_sha256: {digest}")
        seen.add(digest)

    with sanitized_attestation_environment():
        encoder = model if model is not None else _load_model(model_id, revision)
        embedding_cache: dict[str, list[float]] = {}
        unsigned_rankings = [
            _rank_candidate(
                candidate,
                encoder,
                model_id=model_id,
                revision=revision,
                batch_size=batch_size,
                embedding_cache=embedding_cache,
            )
            for candidate in candidates
        ]
    rankings = [
        attach_attestation(ranking, key, purpose=DENSE_RANKING_PURPOSE)
        for ranking in unsigned_rankings
    ]
    _write_jsonl_atomic(output_path, rankings)
    return len(rankings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    count = rank_candidates(
        args.candidates,
        args.output,
        model_id=args.model_id,
        revision=args.revision,
        batch_size=args.batch_size,
    )
    print(json.dumps({"rows": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
