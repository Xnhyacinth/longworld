from __future__ import annotations

import math
import random
from dataclasses import dataclass

from longworld.core.graph import partition_unique_pool, random_walk_event_ids
from longworld.core.render import Artifact
from longworld.domains.company.queries import QuerySpec

SEP = "\n\n===== DOCUMENT =====\n\n"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def join_artifacts(arts: list[Artifact]) -> str:
    return SEP.join(a.text for a in arts)


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
    min_distance_frac: float = 0.35,
    min_distance_tokens: int = 0,
    allow_clone: bool = False,
    walk_ids: list[str] | None = None,
) -> PackedContext:
    """Pack unique documents only. Evidence spans are separated by unique filler.

    Hard-negative documents come from a causal-graph random walk. If the unique
    pool cannot reach ``target_tokens``, the pack is rejected (no archive clones).
    """
    del allow_clone  # clones are forbidden; argument kept so callers can assert
    ess_ids = set(spec.essential_artifact_ids)
    pool = _dedupe(list(artifacts) + list(filler))
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
        # Fall back to id lookup in the combined pool.
        index = {a.artifact_id: a for a in pool}
        essential = [index[i] for i in spec.essential_artifact_ids if i in index]
    hard = parts["hard"]
    rest = parts["rest"]
    rng.shuffle(hard)
    rng.shuffle(rest)

    buffer_n = min(3, max(1, len(hard) + len(rest)))
    buffer_src = hard[:buffer_n] if hard else rest[:buffer_n]
    leftover_hard = hard[len(buffer_src) :] if hard else []
    leftover_rest = rest if hard else rest[len(buffer_src) :]
    queue = leftover_hard + leftover_rest
    rng.shuffle(queue)

    n_ess = max(1, len(essential))
    n_gaps = n_ess + 1
    gaps: list[list[Artifact]] = [[] for _ in range(n_gaps)]
    between = n_ess // 2  # gap index between first and last essential

    def layout() -> list[Artifact]:
        body: list[Artifact] = list(gaps[0])
        for i, ess in enumerate(essential):
            body.append(ess)
            if i + 1 < n_gaps:
                body.extend(gaps[i + 1])
        if not essential:
            body = [x for g in gaps for x in g]
        if query_timing == "late":
            return body + buffer_src
        return buffer_src + body

    def pick_gap(dist_now: int, need: int) -> int:
        if len(essential) >= 2 and dist_now < need:
            return between
        if position_bucket == "front":
            return n_gaps - 1
        if position_bucket == "back":
            return 0
        # middle: grow both sides of the evidence span
        return between if len(essential) >= 2 else n_gaps // 2

    ordered = layout()
    text = join_artifacts(ordered)
    need = max(min_distance_tokens, int(min_distance_frac * target_tokens))
    qi = 0
    while estimate_tokens(text) < target_tokens and qi < len(queue):
        dist_now = _evidence_distance(ordered, ess_ids, text)
        gaps[pick_gap(dist_now, need)].append(queue[qi])
        qi += 1
        ordered = layout()
        text = join_artifacts(ordered)

    ordered = _dedupe(ordered)
    text = join_artifacts(ordered)
    tokens = estimate_tokens(text)
    dist = _evidence_distance(ordered, ess_ids, text)
    window = [a.artifact_id for a in buffer_src]
    if tokens < target_tokens:
        return PackedContext(
            artifacts=ordered,
            text=text,
            tokens=tokens,
            position_bucket=position_bucket,
            length_bucket=length_bucket,
            window_ids=window,
            max_evidence_distance=dist,
            query_timing=query_timing,
            ok=False,
            n_unique=len(ordered),
            n_clones=0,
            reject_reason=f"unique_shortfall:{tokens}<{target_tokens}",
        )
    if len(essential) >= 2 and dist < need:
        return PackedContext(
            artifacts=ordered,
            text=text,
            tokens=tokens,
            position_bucket=position_bucket,
            length_bucket=length_bucket,
            window_ids=window,
            max_evidence_distance=dist,
            query_timing=query_timing,
            ok=False,
            n_unique=len(ordered),
            n_clones=0,
            reject_reason=f"evidence_distance:{dist}<{need}",
        )
    return PackedContext(
        artifacts=ordered,
        text=text,
        tokens=tokens,
        position_bucket=position_bucket,
        length_bucket=length_bucket,
        window_ids=window,
        max_evidence_distance=dist,
        query_timing=query_timing,
        ok=True,
        n_unique=len(ordered),
        n_clones=0,
    )


def _evidence_distance(ordered: list[Artifact], ess_ids: set[str], text: str) -> int:
    positions: list[int] = []
    cursor = 0
    for a in ordered:
        if a.artifact_id in ess_ids:
            positions.append(estimate_tokens(text[:cursor]) if cursor else 0)
        cursor += len(a.text) + len(SEP)
    if len(positions) >= 2:
        return max(positions) - min(positions)
    if positions:
        return positions[0]
    return 0


def _artifact_event_graph(artifacts: list[Artifact]):
    import networkx as nx

    g = nx.DiGraph()
    by_event: dict[str, list[str]] = {}
    for a in artifacts:
        for eid in a.reveals_events:
            g.add_node(eid, kind="event")
            by_event.setdefault(eid, []).append(a.artifact_id)
    eids = list(by_event)
    # Weak temporal chain so walks have somewhere to go even without world.events.
    for a, b in zip(eids, eids[1:]):
        g.add_edge(a, b, kind="timeline")
    return g


def wrap_prompt(question: str, context: str, timing: str) -> str:
    if timing == "first":
        return (
            f"Question:\n{question}\n\n"
            f"Context (company documents):\n{context}\n\n"
            f"Answer using only the documents. If the documents are insufficient, "
            f"reply exactly: unanswerable"
        )
    return (
        f"Context (company documents):\n{context}\n\n"
        f"Question:\n{question}\n\n"
        f"Answer using only the documents. If the documents are insufficient, "
        f"reply exactly: unanswerable"
    )
