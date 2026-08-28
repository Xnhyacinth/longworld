from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import pairwise

from longworld.core.contentplan import proposition_novelty
from longworld.core.graph import partition_unique_pool, random_walk_event_ids
from longworld.core.render import Artifact
from longworld.core.semantic import (
    artifact_role,
    boilerplate_char_fraction,
    is_boilerplate,
    pulse_doc_ratio,
)
from longworld.domains.company.queries import QuerySpec

SEP = "\n\n===== DOCUMENT =====\n\n"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def join_artifacts(arts: list[Artifact]) -> str:
    return SEP.join(a.text for a in arts)


def length_label(tokens: int) -> str:
    """Bucket is a sample attribute from actual length, not a fill target."""
    if tokens < 6000:
        return "4k"
    if tokens < 12000:
        return "8k"
    if tokens < 24000:
        return "16k"
    if tokens < 40000:
        return "32k"
    if tokens < 80000:
        return "64k"
    if tokens < 160000:
        return "128k"
    return "256k"


@dataclass
class PackedContext:
    artifacts: list[Artifact]
    text: str
    tokens: int
    position_bucket: str
    length_bucket: str
    window_ids: list[str]
    max_evidence_distance: int
    query_timing: str
    ok: bool = True
    n_unique: int = 0
    n_clones: int = 0
    reject_reason: str | None = None
    roles: dict[str, str] = field(default_factory=dict)
    boilerplate_token_ratio: float = 0.0
    pulse_doc_ratio: float = 0.0
    hard_negative_doc_ratio: float = 0.0
    n_gold: int = 0
    n_hard: int = 0
    n_background: int = 0
    requested_max_tokens: int = 0


@dataclass(frozen=True)
class ViewMetrics:
    """Measurements derived from one rendered view, never inherited from full."""

    context_tokens: int
    length_bucket: str
    evidence_count: int
    evidence_span_tokens: int
    query_evidence_distance: int
    max_evidence_distance: int
    position_bucket: str


def _artifact_token_bounds(
    ordered: list[Artifact],
    essential_ids: set[str],
    token_counter: Callable[[str], int] | None = None,
    token_prefix: str = "",
) -> tuple[list[tuple[int, int]], int]:
    if token_counter is not None:
        exact_bounds: list[tuple[int, int]] = []
        context_parts = [token_prefix] if token_prefix else []
        for index, artifact in enumerate(ordered):
            if index:
                context_parts.append(SEP)
            if artifact.artifact_id in essential_ids:
                start = token_counter("".join(context_parts))
                context_parts.append(artifact.text)
                end = token_counter("".join(context_parts))
                exact_bounds.append((start, end))
            else:
                context_parts.append(artifact.text)
        context = "".join(context_parts)
        return exact_bounds, token_counter(context) if context else 0
    bounds: list[tuple[int, int]] = []
    cursor_chars = len(token_prefix)
    for i, artifact in enumerate(ordered):
        if i:
            cursor_chars += len(SEP)
        start = math.ceil(cursor_chars / 4) if cursor_chars else 0
        cursor_chars += len(artifact.text)
        end = math.ceil(cursor_chars / 4)
        if artifact.artifact_id in essential_ids:
            bounds.append((start, end))
    return bounds, math.ceil(cursor_chars / 4) if cursor_chars else 0


def compute_view_metrics(
    ordered: list[Artifact],
    essential_ids: set[str],
    *,
    query_timing: str,
    context: str | None = None,
    token_counter: Callable[[str], int] | None = None,
    token_prefix: str = "",
    query_boundary_tokens: int | None = None,
) -> ViewMetrics:
    """Compute truthful length and distance fields for a rendered view.

    A singleton has no pairwise evidence span. Its legacy
    ``max_evidence_distance`` therefore measures distance to the query boundary.
    For a late query, an evidence document immediately before the query is near,
    regardless of its absolute offset in a 256k context.
    """

    bounds, artifact_tokens = _artifact_token_bounds(
        ordered, essential_ids, token_counter, token_prefix
    )
    context_tokens = (
        (token_counter or estimate_tokens)(context)
        if context is not None
        else artifact_tokens
    )
    if bounds:
        first_start = min(start for start, _ in bounds)
        last_end = max(end for _, end in bounds)
        evidence_span = max(0, last_end - first_start) if len(bounds) >= 2 else 0
        if query_boundary_tokens is not None and query_timing == "late":
            query_distance = max(0, query_boundary_tokens - last_end)
        elif query_boundary_tokens is not None:
            query_distance = max(0, last_end - query_boundary_tokens)
        elif query_timing == "late":
            query_distance = max(0, context_tokens - last_end)
        else:
            query_distance = last_end
        max_distance = evidence_span if len(bounds) >= 2 else query_distance
        midpoint = (first_start + last_end) / 2
        frac = midpoint / max(1, context_tokens)
        position = "front" if frac < 1 / 3 else "back" if frac > 2 / 3 else "middle"
    else:
        evidence_span = 0
        query_distance = 0
        max_distance = 0
        position = "none"
    return ViewMetrics(
        context_tokens=context_tokens,
        length_bucket=length_label(context_tokens),
        evidence_count=len(bounds),
        evidence_span_tokens=evidence_span,
        query_evidence_distance=query_distance,
        max_evidence_distance=max_distance,
        position_bucket=position,
    )


def dependency_class_for_view(spec: QuerySpec, metrics: ViewMetrics, view: str) -> str:
    """Classify the dependency actually present in this rendered view."""

    if view == "minimal":
        return "short_curriculum"
    if metrics.evidence_count == 0:
        return "insufficient_context"
    if spec.query_type == "program_join":
        return "program_join"
    if spec.query_type in {
        "revisitation",
        "ratification",
        "docket_control",
        "source_choice",
    } or (
        int(spec.proof_depth or 0) >= 3
        and metrics.evidence_count >= 3
        and spec.query_type != "program_join"
    ):
        return "deep_dependency"
    if max(metrics.evidence_span_tokens, metrics.query_evidence_distance) >= 8000:
        return "long_range_retrieval"
    return "local_or_mixed"


def dependency_evidence_ids(artifacts: list[Artifact], spec: QuerySpec) -> set[str]:
    """Evidence used by either the semantic proof or strict executable closure."""
    sufficient_event_ids = set(spec.sufficient_event_ids)
    return set(spec.essential_artifact_ids) | {
        artifact.artifact_id
        for artifact in artifacts
        if sufficient_event_ids.intersection(artifact.reveals_events)
    }


def _dedupe(arts: list[Artifact]) -> list[Artifact]:
    seen: set[str] = set()
    out: list[Artifact] = []
    for a in arts:
        if a.artifact_id in seen:
            continue
        seen.add(a.artifact_id)
        out.append(a)
    return out


def pack_view(
    artifacts: list[Artifact],
    spec: QuerySpec,
    filler: list[Artifact],
    *,
    query_timing: str,
    position_bucket: str,
    length_bucket: str,
    target_tokens: int,
    rng: random.Random,
    min_distance_frac: float = 0.0,
    min_distance_tokens: int = 0,
    allow_clone: bool = False,
    walk_ids: list[str] | None = None,
    min_semantic_tokens: int = 800,
    skip_boilerplate: bool = True,
    prefer_ids: set[str] | None = None,
    ordered_corridor_ids: set[str] | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> PackedContext:
    """Pack unique, non-boilerplate documents up to a max token cap.

    Length is a cap, not a KPI. If the unique pool runs out before the cap,
    the pack stays at its natural length. Boilerplate (weekly pulses, the
    fixed prose bank) is never used as fallback fill.
    """
    del allow_clone
    max_tokens = int(target_tokens)
    measure_tokens = token_counter or estimate_tokens
    ess_ids = set(spec.essential_artifact_ids)
    corridor_ids = set(ordered_corridor_ids or ())
    essential_world_ids = {
        artifact_id.split(":", 1)[0] for artifact_id in ess_ids if artifact_id
    }
    sufficient_event_ids = set(spec.sufficient_event_ids)
    pool = _dedupe(list(artifacts) + list(filler))
    if skip_boilerplate:
        pool = [
            a
            for a in pool
            if (a.artifact_id in ess_ids)
            or (a.artifact_id in corridor_ids)
            or sufficient_event_ids.intersection(a.reveals_events)
            or (not is_boilerplate(a))
        ]
    pool = [
        a
        for a in pool
        if a.artifact_id in ess_ids
        or a.artifact_id in corridor_ids
        or measure_tokens(a.text) <= max_tokens
    ]
    pool_index = {artifact.artifact_id: artifact for artifact in pool}
    if corridor_ids and not corridor_ids.issubset(pool_index):
        return PackedContext(
            artifacts=[],
            text="",
            tokens=0,
            position_bucket=position_bucket,
            length_bucket=length_bucket,
            window_ids=[],
            max_evidence_distance=0,
            query_timing=query_timing,
            ok=False,
            reject_reason="source_corridor_missing",
            requested_max_tokens=max_tokens,
        )
    if corridor_ids and not ess_ids.issubset(corridor_ids):
        return PackedContext(
            artifacts=[],
            text="",
            tokens=0,
            position_bucket=position_bucket,
            length_bucket=length_bucket,
            window_ids=[],
            max_evidence_distance=0,
            query_timing=query_timing,
            ok=False,
            reject_reason="source_corridor_missing_essential",
            requested_max_tokens=max_tokens,
        )
    if walk_ids is None:
        g = _artifact_event_graph(pool)
        walk_ids = random_walk_event_ids(
            g,
            starts=list(spec.essential_event_ids),
            n_walks=12,
            walk_len=6,
            forbid=set(spec.essential_event_ids),
            rng=rng,
        )
    parts = partition_unique_pool(pool, spec, walk_ids)
    essential = parts["essential"]
    if len(essential) < len(ess_ids):
        index = {a.artifact_id: a for a in pool}
        essential = [index[i] for i in spec.essential_artifact_ids if i in index]
    essential.sort(key=lambda a: (a.time, a.artifact_id))
    corridor = sorted(
        (pool_index[artifact_id] for artifact_id in corridor_ids),
        key=lambda artifact: (
            int(
                dict((artifact.slots or {}).get("params") or {}).get("source_order")
                or 0
            ),
            artifact.time,
            artifact.artifact_id,
        ),
    )
    corridor_support_ids = {
        artifact.artifact_id
        for artifact in corridor
        if artifact.artifact_id not in ess_ids
        and sufficient_event_ids.intersection(artifact.reveals_events)
    }
    strict_support = [
        artifact
        for artifact in pool
        if artifact.artifact_id not in ess_ids
        and artifact.artifact_id not in corridor_ids
        and artifact.artifact_id.split(":", 1)[0] in essential_world_ids
        and sufficient_event_ids.intersection(artifact.reveals_events)
    ]
    strict_support_ids = {artifact.artifact_id for artifact in strict_support}
    all_support_ids = strict_support_ids | corridor_support_ids
    evidence_ids = ess_ids | all_support_ids
    hard = [
        artifact
        for artifact in parts["hard"]
        if artifact.artifact_id not in all_support_ids
        and artifact.artifact_id not in corridor_ids
    ]
    rest = [
        artifact
        for artifact in parts["rest"]
        if artifact.artifact_id not in all_support_ids
        and artifact.artifact_id not in corridor_ids
    ]
    rng.shuffle(hard)
    rng.shuffle(rest)
    prefer = prefer_ids or set()
    if prefer:
        hard.sort(key=lambda a: 0 if a.artifact_id in prefer else 1)
        rest.sort(key=lambda a: 0 if a.artifact_id in prefer else 1)
    hard.sort(key=lambda a: -_semantic_score(a, prefer, set()))
    rest.sort(key=lambda a: -_semantic_score(a, prefer, set()))
    hard_ids = {a.artifact_id for a in hard}

    def source_corridor_key(artifact: Artifact):
        return (
            int(
                dict((artifact.slots or {}).get("params") or {}).get("source_order")
                or 0
            ),
            artifact.time,
            artifact.artifact_id,
        )

    buffer_n = min(3, max(1, len(hard) + len(rest)))
    need_pre = max(min_distance_tokens, int(min_distance_frac * max_tokens))
    if need_pre >= 8000:
        buffer_n = min(1, buffer_n)
    buffer_src = hard[:buffer_n] if hard else rest[:buffer_n]
    core = (
        sorted(
            _dedupe([*corridor, *strict_support]),
            key=source_corridor_key,
        )
        if corridor
        else [*essential, *strict_support]
    )
    if corridor and measure_tokens(join_artifacts(core)) > max_tokens:
        required_ids = ess_ids | all_support_ids
        selected_core = [
            artifact for artifact in core if artifact.artifact_id in required_ids
        ]
        if measure_tokens(join_artifacts(selected_core)) > max_tokens:
            return PackedContext(
                artifacts=[],
                text="",
                tokens=0,
                position_bucket=position_bucket,
                length_bucket=length_bucket,
                window_ids=[],
                max_evidence_distance=0,
                query_timing=query_timing,
                ok=False,
                reject_reason="strict_support_overflow",
                requested_max_tokens=max_tokens,
            )
        selected_ids = {artifact.artifact_id for artifact in selected_core}
        for artifact in core:
            if artifact.artifact_id in selected_ids:
                continue
            trial_core = [*selected_core, artifact]
            if measure_tokens(join_artifacts(trial_core)) > max_tokens:
                continue
            selected_core.append(artifact)
            selected_ids.add(artifact.artifact_id)
        core = sorted(selected_core, key=source_corridor_key)
    if corridor:
        corridor = core
    mandatory_text = join_artifacts([*core, *buffer_src])
    if measure_tokens(mandatory_text) > max_tokens:
        buffer_src = []
    if measure_tokens(join_artifacts(core)) > max_tokens:
        return PackedContext(
            artifacts=[],
            text="",
            tokens=0,
            position_bucket=position_bucket,
            length_bucket=length_bucket,
            window_ids=[],
            max_evidence_distance=0,
            query_timing=query_timing,
            ok=False,
            reject_reason="strict_support_overflow",
            requested_max_tokens=max_tokens,
        )
    leftover_hard = hard[len(buffer_src) :] if hard else []
    leftover_rest = rest if hard else rest[len(buffer_src) :]
    queue = ([] if corridor else strict_support) + leftover_hard + leftover_rest
    seen_props: set[str] = set()
    for a in essential:
        proposition_novelty(a, seen_props)
    queue.sort(
        key=lambda a: (
            0 if a.artifact_id in all_support_ids else 1,
            -_semantic_score(a, prefer, seen_props),
        )
    )

    n_ess = max(1, len(essential))
    n_gaps = n_ess + 1
    gaps: list[list[Artifact]] = [[] for _ in range(n_gaps)]
    between = n_ess // 2
    n_internal = max(1, len(essential) - 1)
    fill_i = 0

    corridor_before: list[Artifact] = []
    corridor_after: list[Artifact] = []

    def layout() -> list[Artifact]:
        if corridor:
            if query_timing == "late":
                return corridor_before + corridor + corridor_after + buffer_src
            return buffer_src + corridor_before + corridor + corridor_after
        body: list[Artifact] = list(gaps[0])
        for i, ess in enumerate(essential):
            body.append(ess)
            if i + 1 < n_gaps:
                body.extend(gaps[i + 1])
        if not essential:
            body = [x for g in gaps for x in g]
        if len(essential) == 1:
            return buffer_src + body if query_timing == "late" else body + buffer_src
        if query_timing == "late":
            return body + buffer_src
        return buffer_src + body

    def pick_gap(dist_now: int, need: int) -> int:
        nonlocal fill_i
        if len(essential) >= 2 and dist_now < need:
            gap = 1 + (fill_i % n_internal)
            fill_i += 1
            return gap
        if position_bucket == "front":
            return n_gaps - 1
        if position_bucket == "back":
            return 0
        return between if len(essential) >= 2 else n_gaps // 2

    def add_corridor_side(artifact: Artifact) -> list[Artifact]:
        if position_bucket == "front":
            corridor_after.append(artifact)
        elif position_bucket == "back" or len(corridor_before) <= len(corridor_after):
            corridor_before.append(artifact)
        else:
            corridor_after.append(artifact)
        return layout()

    def pop_corridor_side(artifact: Artifact) -> None:
        target = corridor_before if artifact in corridor_before else corridor_after
        target.remove(artifact)

    ordered = layout()
    text = join_artifacts(ordered)
    need = max(min_distance_tokens, int(min_distance_frac * max_tokens))
    seen_text: set[str] = {a.text for a in ordered}
    qi = 0
    while qi < len(queue):
        nxt = queue[qi]
        qi += 1
        if nxt.artifact_id in ess_ids:
            continue
        if (
            skip_boilerplate
            and nxt.artifact_id not in all_support_ids
            and is_boilerplate(nxt)
        ):
            continue
        if nxt.text in seen_text:
            continue
        trial_new = proposition_novelty(nxt, set(seen_props))
        if (
            trial_new == 0
            and nxt.artifact_id not in all_support_ids
            and nxt.artifact_id not in prefer
            and nxt.artifact_id not in hard_ids
            and nxt.doc_type != "source_pack"
        ):
            continue
        dist_now = _evidence_distance(
            ordered,
            evidence_ids,
            text,
            query_timing=query_timing,
            token_counter=token_counter,
        )
        trial_gap = pick_gap(dist_now, need)
        if corridor:
            trial = add_corridor_side(nxt)
        else:
            gaps[trial_gap].append(nxt)
            trial = layout()
        trial_text = join_artifacts(trial)
        if measure_tokens(trial_text) > max_tokens:
            if corridor:
                pop_corridor_side(nxt)
            else:
                gaps[trial_gap].pop()
            continue
        ordered = trial
        text = trial_text
        seen_text.add(nxt.text)
        proposition_novelty(nxt, seen_props)

    ordered = _dedupe(ordered)
    text = join_artifacts(ordered)
    tokens = measure_tokens(text)
    metric_context = (
        wrap_prompt(spec.question, text, query_timing)
        if token_counter is not None
        else text
    )
    metrics = compute_view_metrics(
        ordered,
        evidence_ids,
        query_timing=query_timing,
        context=metric_context,
        token_counter=token_counter,
        token_prefix=(
            prompt_document_prefix(spec.question, query_timing)
            if token_counter is not None
            else ""
        ),
        query_boundary_tokens=(
            prompt_query_boundary(spec.question, text, query_timing, token_counter)
            if token_counter is not None
            else None
        ),
    )
    dist = metrics.max_evidence_distance
    window = [a.artifact_id for a in buffer_src]
    gold_stem = min(ess_ids).split(".", 1)[0] if ess_ids else ""
    roles = {
        a.artifact_id: artifact_role(
            a, ess_ids, hard_ids, gold_stem, support_ids=all_support_ids
        )
        for a in ordered
    }
    n_gold = sum(1 for r in roles.values() if r == "causal_gold")
    n_hard = sum(1 for r in roles.values() if r == "structural_hard_negative")
    n_bg = sum(1 for r in roles.values() if r == "natural_background")
    bank_frac = boilerplate_char_fraction(text)
    pulses = pulse_doc_ratio(ordered)
    actual_bucket = length_label(tokens)
    packed = PackedContext(
        artifacts=ordered,
        text=text,
        tokens=tokens,
        position_bucket=position_bucket,
        length_bucket=actual_bucket or length_bucket,
        window_ids=window,
        max_evidence_distance=dist,
        query_timing=query_timing,
        n_unique=len(ordered),
        n_clones=0,
        roles=roles,
        boilerplate_token_ratio=round(bank_frac, 4),
        pulse_doc_ratio=round(pulses, 4),
        hard_negative_doc_ratio=round(n_hard / max(1, len(ordered) - n_gold), 4),
        n_gold=n_gold,
        n_hard=n_hard,
        n_background=n_bg,
        requested_max_tokens=max_tokens,
    )
    if not all_support_ids.issubset({artifact.artifact_id for artifact in ordered}):
        return replace(
            packed,
            ok=False,
            reject_reason="strict_support_missing",
        )
    if tokens < min_semantic_tokens:
        return replace(
            packed,
            ok=False,
            reject_reason=f"semantic_shortfall:{tokens}<{min_semantic_tokens}",
        )
    if need > 0 and len(evidence_ids) >= 2 and dist < need:
        return replace(
            packed,
            ok=False,
            reject_reason=f"distance_shortfall:{dist}<{need}",
        )
    return packed


_NATIVE_DOC_TYPES = frozenset(
    {
        "email",
        "log",
        "meeting_notes",
        "report",
        "finance_report",
        "code",
        "json",
    }
)


def _ingest_bound(artifact: Artifact) -> bool:
    """Public file that reveals an ingest event. Unbound RFC is not this."""
    return artifact.doc_type == "source_pack" and bool(artifact.reveals_events)


def _semantic_score(artifact: Artifact, prefer: set[str], seen_props: set[str]) -> int:
    """Coverage / novelty / workspace realism. Not a token-gap filler score.

    Unbound source packs are last-resort unique length, not preferred filler.
    Ingest-bound packs may still outrank generic email because they carry gold.
    """
    score = 0
    if artifact.artifact_id in prefer:
        score += 8
    if str(artifact.artifact_id).endswith("latent_decoy"):
        score += 12
    if _ingest_bound(artifact):
        score += 6
    elif artifact.doc_type in _NATIVE_DOC_TYPES:
        score += 4
    plan = (artifact.slots or {}).get("content_plan") or {}
    if plan.get("predecessors"):
        score += 1
    score += min(4, proposition_novelty(artifact, set(seen_props)))
    if is_boilerplate(artifact):
        score -= 20
    return score


def covering_span_tokens(
    ordered: list[Artifact],
    ess_ids: set[str],
    token_counter: Callable[[str], int] | None = None,
) -> int:
    """Token length of the contiguous slice that covers every essential doc."""
    idxs = [i for i, a in enumerate(ordered) if a.artifact_id in ess_ids]
    if not idxs:
        return 0
    lo, hi = min(idxs), max(idxs)
    return (token_counter or estimate_tokens)(join_artifacts(ordered[lo : hi + 1]))


def local_span_too_short(
    ordered: list[Artifact],
    ess_ids: set[str],
    *,
    target_tokens: int,
    token_counter: Callable[[str], int] | None = None,
) -> str | None:
    """Pack-time only. Short unit-test worlds never hit these caps."""
    if len(ess_ids) < 2 or target_tokens < 16000:
        return None
    span = covering_span_tokens(ordered, ess_ids, token_counter)
    if span <= 8000:
        return f"local_8k_solves:{span}"
    if target_tokens >= 32000 and span <= 16000:
        return f"local_16k_solves:{span}"
    return None


def _evidence_distance(
    ordered: list[Artifact],
    ess_ids: set[str],
    text: str,
    query_timing: str = "first",
    token_counter: Callable[[str], int] | None = None,
) -> int:
    return compute_view_metrics(
        ordered,
        ess_ids,
        query_timing=query_timing,
        context=text,
        token_counter=token_counter,
    ).max_evidence_distance


def _artifact_event_graph(artifacts: list[Artifact]):
    import networkx as nx

    g = nx.DiGraph()
    by_event: dict[str, list[str]] = {}
    for a in artifacts:
        for eid in a.reveals_events:
            g.add_node(eid, kind="event")
            by_event.setdefault(eid, []).append(a.artifact_id)
    eids = list(by_event)
    for source_event, target_event in pairwise(eids):
        g.add_edge(source_event, target_event, kind="timeline")
    return g


def prompt_document_prefix(question: str, timing: str) -> str:
    """Return the exact prompt bytes that precede the document context."""
    if timing == "first":
        return f"Question:\n{question}\n\nContext (internal records):\n"
    return "Context (internal records):\n"


def prompt_query_boundary(
    question: str,
    context: str,
    timing: str,
    token_counter: Callable[[str], int],
) -> int:
    """Return query-end (first) or query-start (late) in prompt token space."""
    if timing == "first":
        return token_counter(f"Question:\n{question}")
    return token_counter(f"{prompt_document_prefix(question, timing)}{context}\n\n")


def wrap_prompt(question: str, context: str, timing: str) -> str:
    if timing == "first":
        return (
            f"{prompt_document_prefix(question, timing)}{context}\n\n"
            f"Answer using only the documents. If the documents are insufficient, "
            f"reply exactly: unanswerable"
        )
    return (
        f"{prompt_document_prefix(question, timing)}{context}\n\n"
        f"Question:\n{question}\n\n"
        f"Answer using only the documents. If the documents are insufficient, "
        f"reply exactly: unanswerable"
    )
