"""Cheap lexical retrieval gates (no extra deps).

BM25-lite / TF ranking on the packed artifact pool. A sample fails the
shortcut gate when the single top-ranked document already replays to gold
and the proof claims two or more essential artifacts.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from longworld.core.engine import answer_from_artifacts
from longworld.core.pack import estimate_tokens
from longworld.core.render import Artifact
from longworld.core.world import SimulatedWorld
from longworld.domains.company.queries import QuerySpec

_TOKEN = re.compile(r"[a-z0-9]+")


def _replay_answer(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    *,
    extra_overrides: dict[str, dict[str, Any]] | None,
) -> str:
    return answer_from_artifacts(
        world,
        spec,
        artifacts,
        extra_overrides=extra_overrides,
        enforce_preconditions=True,
    )


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _bm25(
    query: list[str],
    doc: list[str],
    df: Counter,
    n_docs: int,
    avgdl: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    if not doc:
        return 0.0
    tf = Counter(doc)
    score = 0.0
    dl = len(doc)
    for term in query:
        f = tf.get(term, 0)
        if f == 0:
            continue
        n_t = df.get(term, 0)
        idf = math.log(1.0 + (n_docs - n_t + 0.5) / (n_t + 0.5))
        denom = f + k1 * (1.0 - b + b * dl / max(avgdl, 1.0))
        score += idf * (f * (k1 + 1.0) / denom)
    return score


def bm25_rank(
    query: str, artifacts: list[Artifact], k: int = 5
) -> list[tuple[float, Artifact]]:
    docs = [tokenize(a.text) for a in artifacts]
    q = tokenize(query)
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    scored = [(_bm25(q, d, df, n, avgdl), a) for a, d in zip(artifacts, docs)]
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def bm25_top1_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    *,
    expected_answer: str | None = None,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict]:
    """True when BM25 top-1 is not a sufficient proof (or the task is 1-doc)."""
    notes: dict = {"bm25_top1_id": None, "bm25_top1_ans": None, "bm25_score": 0.0}
    if len(spec.essential_artifact_ids) < 2:
        notes["bm25_skip"] = "single_essential"
        return True, notes
    pool = [a for a in artifacts if a.text]
    if not pool:
        return True, notes
    ranked = bm25_rank(spec.question, pool, k=1)
    if not ranked:
        return True, notes
    score, top = ranked[0]
    ans = _replay_answer(world, spec, [top], extra_overrides=extra_overrides)
    notes["bm25_top1_id"] = top.artifact_id
    notes["bm25_top1_ans"] = ans
    notes["bm25_score"] = round(score, 4)
    return ans != (
        expected_answer if expected_answer is not None else spec.answer
    ), notes


def bm25_topk_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    k: int = 3,
    *,
    expected_answer: str | None = None,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict]:
    """Return whether the union of the top-k BM25-lite documents misses gold."""
    ranked = bm25_rank(spec.question, [a for a in artifacts if a.text], k=max(k, 1))
    selected = [artifact for _, artifact in ranked]
    prefix_answers = [
        _replay_answer(
            world,
            spec,
            selected[:prefix],
            extra_overrides=extra_overrides,
        )
        for prefix in range(1, len(selected) + 1)
    ]
    answer = prefix_answers[-1] if prefix_answers else None
    notes: dict[str, object] = {
        "k": k,
        "artifact_ids": [artifact.artifact_id for artifact in selected],
        "scores": [round(score, 4) for score, _ in ranked],
        "answer": answer,
        "prefix_answers": prefix_answers,
    }
    target = expected_answer if expected_answer is not None else spec.answer
    return all(value != target for value in prefix_answers), notes


def lexical_tfidf_rank(
    query: str, artifacts: list[Artifact], k: int = 5
) -> list[tuple[float, Artifact]]:
    """Rank documents by sparse lexical TF-IDF cosine similarity.

    This is deliberately named as a lexical baseline. It is not an embedding
    retriever and must not be reported as one.
    """
    docs = [tokenize(a.text) for a in artifacts]
    query_tokens = tokenize(query)
    n_docs = len(docs)
    if n_docs == 0 or not query_tokens:
        return []
    df: Counter = Counter()
    for doc in docs:
        df.update(set(doc))

    def weights(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        return {
            term: count * (math.log((n_docs + 1.0) / (df.get(term, 0) + 1.0)) + 1.0)
            for term, count in counts.items()
        }

    query_weights = weights(query_tokens)
    query_norm = math.sqrt(sum(value * value for value in query_weights.values()))
    scored: list[tuple[float, Artifact]] = []
    for artifact, doc in zip(artifacts, docs):
        doc_weights = weights(doc)
        doc_norm = math.sqrt(sum(value * value for value in doc_weights.values()))
        dot = sum(
            query_weights.get(term, 0.0) * value for term, value in doc_weights.items()
        )
        score = dot / (query_norm * doc_norm) if query_norm and doc_norm else 0.0
        scored.append((score, artifact))
    scored.sort(key=lambda item: -item[0])
    return scored[:k]


def lexical_tfidf_topk_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    k: int = 3,
    *,
    expected_answer: str | None = None,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict]:
    """Return whether sparse lexical TF-IDF top-k retrieval misses gold."""
    ranked = lexical_tfidf_rank(
        spec.question, [a for a in artifacts if a.text], k=max(k, 1)
    )
    selected = [artifact for _, artifact in ranked]
    prefix_answers = [
        _replay_answer(
            world,
            spec,
            selected[:prefix],
            extra_overrides=extra_overrides,
        )
        for prefix in range(1, len(selected) + 1)
    ]
    answer = prefix_answers[-1] if prefix_answers else None
    notes = {
        "k": k,
        "artifact_ids": [artifact.artifact_id for artifact in selected],
        "scores": [round(score, 4) for score, _ in ranked],
        "answer": answer,
        "prefix_answers": prefix_answers,
    }
    target = expected_answer if expected_answer is not None else spec.answer
    return all(value != target for value in prefix_answers), notes


def embedding_topk_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    *,
    ranked_artifact_ids: list[str] | None,
    model_id: str | None,
    k: int = 3,
    expected_answer: str | None = None,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict]:
    """Replay externally computed dense-retrieval top-k results.

    LongWorld deliberately does not substitute lexical TF-IDF for embeddings. A
    production caller must supply the ordered artifact ids from a named embedding
    model; a missing or malformed audit fails closed.
    """
    notes: dict[str, object] = {
        "k": k,
        "model_id": model_id,
        "artifact_ids": [],
        "answer": None,
        "audit_available": False,
    }
    pool = {artifact.artifact_id: artifact for artifact in artifacts if artifact.text}
    required = min(max(k, 1), len(pool))
    if (
        not model_id
        or not ranked_artifact_ids
        or len(ranked_artifact_ids) < required
        or len(ranked_artifact_ids) != len(set(ranked_artifact_ids))
        or any(
            artifact_id not in pool for artifact_id in ranked_artifact_ids[:required]
        )
    ):
        return False, notes
    selected_ids = ranked_artifact_ids[:required]
    selected = [pool[artifact_id] for artifact_id in selected_ids]
    prefix_answers = [
        _replay_answer(
            world,
            spec,
            selected[:prefix],
            extra_overrides=extra_overrides,
        )
        for prefix in range(1, len(selected) + 1)
    ]
    answer = prefix_answers[-1] if prefix_answers else None
    notes.update(
        {
            "artifact_ids": selected_ids,
            "answer": answer,
            "prefix_answers": prefix_answers,
            "audit_available": True,
        }
    )
    target = expected_answer if expected_answer is not None else spec.answer
    return all(value != target for value in prefix_answers), notes


def contiguous_windows_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    window_sizes: tuple[int, ...] = (4000, 8000, 16000),
    *,
    necessary_artifact_ids: set[str] | None = None,
    necessary_set_proven: bool = False,
    expected_answer: str | None = None,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict[str, dict]]:
    """Exhaustively test artifact-aligned contiguous windows at each token budget.

    Every contiguous sequence of complete artifacts that fits a budget is replayed.
    A single artifact larger than the budget is also tested conservatively because a
    text window could contain its decisive passage. Budgets at least as large as the
    whole context are marked not applicable instead of treating the full context as a
    local shortcut.
    """
    costs = [estimate_tokens(artifact.text) for artifact in artifacts]
    total_tokens = sum(costs)
    notes: dict[str, dict] = {}
    all_insufficient = True
    target = expected_answer if expected_answer is not None else spec.answer
    necessary_ids = set(necessary_artifact_ids or ())
    necessary_positions = [
        index
        for index, artifact in enumerate(artifacts)
        if artifact.artifact_id in necessary_ids
    ]
    complete_necessary_positions = len(necessary_positions) == len(necessary_ids)
    necessary_first = min(necessary_positions, default=-1)
    necessary_last = max(necessary_positions, default=-1)

    def contains_necessary(start: int, end: int) -> bool:
        return bool(
            complete_necessary_positions
            and necessary_ids
            and start <= necessary_first
            and end >= necessary_last
        )

    for size in window_sizes:
        key = str(size)
        if total_tokens <= size:
            notes[key] = {
                "applicable": False,
                "checked": 0,
                "answer": None,
                "artifact_ids": [],
                "proof_mode": "not_applicable",
            }
            continue
        checked = 0
        hit_answer: str | None = None
        hit_ids: list[str] = []
        for start in range(len(artifacts)):
            used = 0
            for end in range(start, len(artifacts)):
                cost = costs[end]
                if used + cost > size:
                    if end == start:
                        subset = [artifacts[start]]
                        checked += 1
                        if necessary_set_proven and not contains_necessary(start, end):
                            answer = None
                        else:
                            answer = _replay_answer(
                                world,
                                spec,
                                subset,
                                extra_overrides=extra_overrides,
                            )
                        if answer == target:
                            hit_answer = answer
                            hit_ids = [artifacts[start].artifact_id]
                    break
                used += cost
                checked += 1
                if necessary_set_proven and not contains_necessary(start, end):
                    continue
                subset = artifacts[start : end + 1]
                answer = _replay_answer(
                    world,
                    spec,
                    subset,
                    extra_overrides=extra_overrides,
                )
                if answer == target:
                    hit_answer = answer
                    hit_ids = [artifact.artifact_id for artifact in subset]
                    break
            if hit_answer is not None:
                break
        if hit_answer is not None:
            all_insufficient = False
        notes[key] = {
            "applicable": True,
            "checked": checked,
            "answer": hit_answer,
            "artifact_ids": hit_ids,
            "proof_mode": "exhaustive_replay",
        }
    return all_insufficient, notes
