"""Cheap lexical retrieval gates (no extra deps).

BM25-lite / TF ranking on the packed artifact pool. A sample fails the
shortcut gate when the single top-ranked document already replays to gold
and the proof claims two or more essential artifacts.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from collections import Counter
from itertools import pairwise
from typing import Any

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.engine import answer_from_artifacts, answer_from_events
from longworld.core.pack import SEP, estimate_tokens, join_artifacts
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


def raw_token_fact_windows_insufficient(
    world: SimulatedWorld,
    spec: QuerySpec,
    artifacts: list[Artifact],
    tokenizer: Any,
    window_sizes: tuple[int, ...] = (4000, 8000, 16000),
    *,
    expected_answer: str | None = None,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Replay every distinct exact-token SEC source-span window."""
    if spec.query_type != "sec_financial_reconstruction":
        return True, {"applicable": False, "proof_mode": "not_sec_financial"}
    event_index = {event.id: event for event in world.events}
    compute_ids = {
        event_id
        for event_id in spec.sufficient_event_ids
        if event_id in event_index
        and event_index[event_id].type == "sec_financial_answer"
    }
    compute = next(
        (
            event_index[event_id]
            for event_id in compute_ids
            if event_index[event_id].params.get("answer_key") == spec.answer_key
        ),
        None,
    )
    if compute is None:
        return False, {"applicable": True, "error": "missing_answer_event"}
    source_claims: Counter[str] = Counter()
    artifact_prefix = f"{world.spec.get('world_id', '')}."
    for artifact in artifacts:
        event_ids = list(artifact.reveals_events)
        source_event_ids = [
            event_id
            for event_id in event_ids
            if event_id in event_index
            and event_index[event_id].type == "sec_source_section"
        ]
        if source_event_ids and (len(event_ids) != 1 or len(source_event_ids) != 1):
            return False, {
                "applicable": True,
                "error": "ambiguous_source_event_artifact",
            }
        if source_event_ids and not artifact.artifact_id.startswith(artifact_prefix):
            return False, {
                "applicable": True,
                "error": "foreign_source_event_artifact",
            }
        source_claims.update(source_event_ids)
    if any(count != 1 for count in source_claims.values()):
        return False, {
            "applicable": True,
            "error": "duplicate_source_event_artifact",
        }
    document_context = join_artifacts(artifacts)
    try:
        with sanitized_attestation_environment():
            encoded = tokenizer(
                document_context,
                add_special_tokens=False,
                return_offsets_mapping=True,
            )
        token_ids = list(encoded["input_ids"])
        offsets = [tuple(item) for item in encoded["offset_mapping"]]
    except (KeyError, TypeError, ValueError, NotImplementedError) as error:
        return False, {
            "applicable": True,
            "error": "tokenizer_has_no_exact_offset_mapping",
            "detail": str(error),
        }
    if (
        len(token_ids) != len(offsets)
        or any(
            not isinstance(start, int)
            or not isinstance(end, int)
            or isinstance(start, bool)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(document_context)
            for start, end in offsets
        )
        or any(
            left_start > right_start or left_end > right_start
            for (left_start, left_end), (right_start, _right_end) in pairwise(offsets)
        )
    ):
        return False, {"applicable": True, "error": "invalid_token_offsets"}
    token_starts = [start for start, _end in offsets]
    token_ends = [end for _start, end in offsets]

    def token_bounds(char_start: int, char_end: int) -> tuple[int, int] | None:
        start = bisect.bisect_right(token_ends, char_start)
        end = bisect.bisect_left(token_starts, char_end)
        return (start, end) if start < end else None

    source_params: dict[str, dict[str, Any]] = {}
    span_bounds: dict[tuple[str, int], tuple[int, int]] = {}
    span_roles: dict[tuple[str, int], str] = {}
    cursor = 0
    for index, artifact in enumerate(artifacts):
        if index:
            cursor += len(SEP)
        artifact_start = cursor
        cursor += len(artifact.text)
        event_ids = list(artifact.reveals_events)
        if len(event_ids) != 1:
            continue
        event = event_index.get(event_ids[0])
        if event is None or event.type != "sec_source_section":
            continue
        slots = artifact.slots or {}
        params = slots.get("params")
        expected = {**event.params, **(extra_overrides or {}).get(event.id, {})}
        if (
            slots.get("event_type") != event.type
            or not isinstance(params, dict)
            or params != expected
        ):
            return False, {
                "applicable": True,
                "error": "invalid_source_event_artifact",
                "artifact_id": artifact.artifact_id,
            }
        declared_text = str(params.get("text") or "")
        prefix = "SEC source section\n"
        if (
            not declared_text
            or artifact.text.count(declared_text) != 1
            or hashlib.sha256(declared_text.encode()).hexdigest()
            != params.get("text_sha256")
            or not declared_text.startswith(prefix)
            or hashlib.sha256(declared_text[len(prefix) :].encode()).hexdigest()
            != params.get("section_sha256")
        ):
            return False, {
                "applicable": True,
                "error": "invalid_source_event_artifact",
                "artifact_id": artifact.artifact_id,
            }
        source_params[event.id] = params
        declared_start = artifact.text.index(declared_text)
        fact_spans = params.get("fact_spans") or []
        if not isinstance(fact_spans, list):
            return False, {
                "applicable": True,
                "error": "invalid_source_fact_spans",
                "artifact_id": artifact.artifact_id,
            }
        for span_index, span in enumerate(fact_spans):
            if not isinstance(span, dict):
                return False, {
                    "applicable": True,
                    "error": "invalid_source_fact_spans",
                    "artifact_id": artifact.artifact_id,
                }
            kind = str(span.get("kind") or "xbrl")
            if kind not in {"xbrl", "certification"}:
                return False, {
                    "applicable": True,
                    "error": "invalid_source_fact_spans",
                    "artifact_id": artifact.artifact_id,
                }
            start = span.get("char_start")
            end = span.get("char_end")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(declared_text)
            ):
                return False, {
                    "applicable": True,
                    "error": "invalid_source_fact_spans",
                    "artifact_id": artifact.artifact_id,
                }
            quote = str(span.get("evidence_quote") or "")
            if not quote or declared_text[start:end] != quote:
                return False, {
                    "applicable": True,
                    "error": "invalid_source_fact_spans",
                    "artifact_id": artifact.artifact_id,
                }
            component_bounds = [
                (
                    artifact_start + declared_start + start,
                    artifact_start + declared_start + end,
                )
            ]
            valid = True
            if kind == "certification":
                evidence_spans = span.get("evidence_spans")
                if not isinstance(evidence_spans, dict) or not evidence_spans:
                    return False, {
                        "applicable": True,
                        "error": "invalid_source_fact_spans",
                        "artifact_id": artifact.artifact_id,
                    }
                for evidence in evidence_spans.values():
                    if not isinstance(evidence, dict):
                        valid = False
                        break
                    field_start = evidence.get("char_start")
                    field_end = evidence.get("char_end")
                    field_quote = str(evidence.get("evidence_quote") or "")
                    if (
                        isinstance(field_start, bool)
                        or isinstance(field_end, bool)
                        or not isinstance(field_start, int)
                        or not isinstance(field_end, int)
                        or field_start < 0
                        or field_end <= field_start
                        or field_end > len(declared_text)
                        or not field_quote
                        or declared_text[field_start:field_end] != field_quote
                    ):
                        valid = False
                        break
                    component_bounds.append(
                        (
                            artifact_start + declared_start + field_start,
                            artifact_start + declared_start + field_end,
                        )
                    )
            if not valid:
                return False, {
                    "applicable": True,
                    "error": "invalid_source_fact_spans",
                    "artifact_id": artifact.artifact_id,
                }
            mapped = [token_bounds(start, end) for start, end in component_bounds]
            if any(item is None for item in mapped):
                return False, {
                    "applicable": True,
                    "error": "unmapped_source_fact_span",
                    "artifact_id": artifact.artifact_id,
                }
            item_bounds = [item for item in mapped if item is not None]
            key = (event.id, span_index)
            span_bounds[key] = (
                min(start for start, _end in item_bounds),
                max(end for _start, end in item_bounds),
            )
            span_roles[key] = (
                str(span.get("role") or "")
                if kind == "xbrl"
                else f"cert:{event.id}:{span_index}"
            )
    if not source_params or not span_bounds:
        return False, {
            "applicable": True,
            "error": "missing_answer_fact_spans",
        }
    target = expected_answer if expected_answer is not None else spec.answer
    structural_source_ids = set(source_params)
    replay_cache: dict[frozenset[tuple[str, int]], str] = {}

    def replay(visible: frozenset[tuple[str, int]]) -> str:
        cached = replay_cache.get(visible)
        if cached is not None:
            return cached
        overrides = {
            event_id: dict(values)
            for event_id, values in (extra_overrides or {}).items()
        }
        for event_id, params in source_params.items():
            overrides[event_id] = {
                **overrides.get(event_id, {}),
                **params,
                "fact_spans": [
                    span
                    for span_index, span in enumerate(params.get("fact_spans") or [])
                    if (event_id, span_index) in visible
                ],
            }
        answer = answer_from_events(
            world,
            spec,
            structural_source_ids | compute_ids,
            extra_overrides=overrides,
            enforce_preconditions=True,
        )
        replay_cache[visible] = answer
        return answer

    full_visible = frozenset(span_bounds)
    full_answer = replay(full_visible)
    if full_answer != target:
        return False, {
            "applicable": True,
            "error": "full_source_span_replay_failed",
            "source_only_replay_answer": full_answer,
            "expected_answer": target,
        }
    proof_event_ids = {
        str(source_event_id)
        for compute_event_id in compute_ids
        for source_event_id in event_index[compute_event_id].params.get(
            "section_event_ids"
        )
        or []
    }
    required_roles = {
        str(role)
        for event_id in compute_ids
        for role in event_index[event_id].params.get("required_roles") or []
    }
    proof_bounds = [
        bounds
        for key, bounds in span_bounds.items()
        if key[0] in proof_event_ids
        and (
            span_roles.get(key, "").startswith("cert:")
            or span_roles.get(key, "") in required_roles
        )
    ]
    evidence_span = (
        max(end for _start, end in proof_bounds)
        - min(start for start, _end in proof_bounds)
        if proof_bounds
        else 0
    )
    total_tokens = len(token_ids)
    windows: dict[str, dict[str, Any]] = {}
    all_insufficient = True
    for size in window_sizes:
        applicable = total_tokens > size
        hit_answer: str | None = None
        hit_start: int | None = None
        hit_visible: frozenset[tuple[str, int]] = frozenset()
        max_start = max(0, total_tokens - size)
        starts = {0, max_start}
        for start, end in span_bounds.values():
            starts.add(min(max_start, max(0, end - size)))
            starts.add(min(max_start, max(0, start + 1)))
        checked_masks: set[frozenset[tuple[str, int]]] = set()
        for start in sorted(starts):
            if not applicable:
                break
            end = start + size
            visible = frozenset(
                key
                for key, (fact_start, fact_end) in span_bounds.items()
                if start <= fact_start and fact_end <= end
            )
            if visible in checked_masks:
                continue
            checked_masks.add(visible)
            answer = replay(visible)
            if answer == target:
                hit_answer = answer
                hit_start = start
                hit_visible = visible
                break
        if hit_answer is not None:
            all_insufficient = False
        windows[str(size)] = {
            "applicable": applicable,
            "checked_span_masks": len(checked_masks),
            "answer": hit_answer,
            "witness_start": hit_start,
            "witness_end": hit_start + size if hit_start is not None else None,
            "visible_roles": sorted(
                {span_roles[key] for key in hit_visible if span_roles.get(key)}
            ),
        }
    return all_insufficient, {
        "applicable": True,
        "proof_mode": "pinned_tokenizer_source_span_replay",
        "tokenizer_model_id": str(getattr(tokenizer, "name_or_path", "") or ""),
        "tokenizer_revision": str(
            getattr(tokenizer, "init_kwargs", {}).get("_commit_hash") or ""
        ),
        "total_tokens": total_tokens,
        "tokenizer_evidence_span_tokens": evidence_span,
        "source_only_replay_answer": full_answer,
        "span_count": len(span_bounds),
        "replayed_span_masks": len(replay_cache),
        "windows": windows,
    }
