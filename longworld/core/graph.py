"""Causal graph operations: min-sufficient subgraph, random-walk hard negatives.

The company world is synthetic, but the graph is executable: every edge is an
event-sourced write or causal_input recorded by the simulator. Packing and
filters consume this graph so length comes from unique walk-sampled documents,
not cloned archive copies.
"""

from __future__ import annotations

import random
from typing import Any

import networkx as nx

from longworld.core.causal import build_causal_graph
from longworld.core.render import Artifact
from longworld.core.world import SimulatedWorld
from longworld.domains.company.queries import QuerySpec


def event_ancestors(graph: nx.DiGraph, node_ids: list[str]) -> set[str]:
    out: set[str] = set()
    for nid in node_ids:
        if nid not in graph:
            continue
        out.add(nid)
        out |= nx.ancestors(graph, nid)
    return {n for n in out if graph.nodes[n].get("kind") == "event"}


def min_sufficient_subgraph(world: SimulatedWorld, spec: QuerySpec) -> nx.DiGraph:
    """Smallest event subgraph that includes essential events and their ancestors."""
    g = build_causal_graph(world)
    keep = event_ancestors(
        g, list(spec.essential_event_ids) + list(spec.sufficient_event_ids)
    )
    keep |= set(spec.essential_event_ids)
    return g.subgraph(keep).copy()


def hop_count(world: SimulatedWorld, spec: QuerySpec) -> int:
    """SearchArt-style depth: longest path on the sufficient/essential subgraph."""
    sub = min_sufficient_subgraph(world, spec)
    nodes = [e for e in spec.sufficient_event_ids if e in sub]
    if len(nodes) <= 1:
        nodes = [e for e in spec.essential_event_ids if e in sub]
    if len(nodes) <= 1:
        return 1
    longest = 1
    for a in nodes:
        for b in nodes:
            if a == b:
                continue
            if nx.has_path(sub, a, b):
                longest = max(longest, nx.shortest_path_length(sub, a, b) + 1)
    return longest


def proof_depth(world: SimulatedWorld, spec: QuerySpec) -> int:
    """Longest directed path among essential events (1 if a singleton)."""
    sub = min_sufficient_subgraph(world, spec)
    ess = [e for e in spec.essential_event_ids if e in sub]
    if len(ess) <= 1:
        return 1
    longest = 1
    for a in ess:
        for b in ess:
            if a == b:
                continue
            if nx.has_path(sub, a, b):
                longest = max(longest, nx.shortest_path_length(sub, a, b) + 1)
    return longest


def random_walk_event_ids(
    graph: nx.DiGraph,
    starts: list[str],
    *,
    n_walks: int,
    walk_len: int,
    forbid: set[str],
    rng: random.Random,
) -> list[str]:
    """Walk from proof-adjacent nodes; skip the necessary set (hard negatives)."""
    starts = [s for s in starts if s in graph]
    if not starts:
        starts = [n for n, d in graph.nodes(data=True) if d.get("kind") == "event"]
    found: list[str] = []
    seen: set[str] = set()
    for _ in range(max(1, n_walks)):
        cur = rng.choice(starts)
        for _ in range(max(1, walk_len)):
            nbrs = list(graph.successors(cur)) + list(graph.predecessors(cur))
            nbrs = [
                n
                for n in nbrs
                if graph.nodes[n].get("kind") == "event" and n not in forbid
            ]
            if not nbrs:
                break
            cur = rng.choice(nbrs)
            if cur not in seen and cur not in forbid:
                seen.add(cur)
                found.append(cur)
    return found


def typed_walk_event_ids(
    graph: nx.DiGraph,
    starts: list[str],
    *,
    allowed_kinds: set[str],
    n_walks: int,
    walk_len: int,
    forbid: set[str],
    rng: random.Random,
) -> list[str]:
    """Meta-path walk: only follow typed edges (no uniform back-and-forth)."""
    starts = [s for s in starts if s in graph]
    if not starts:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for _ in range(max(1, n_walks)):
        cur = rng.choice(starts)
        for _ in range(max(1, walk_len)):
            nxt = []
            for _, dst, data in graph.out_edges(cur, data=True):
                if data.get("kind") in allowed_kinds and dst not in forbid:
                    if graph.nodes[dst].get("kind") == "event":
                        nxt.append(dst)
            if not nxt:
                break
            cur = rng.choice(nxt)
            if cur not in seen and cur not in forbid:
                seen.add(cur)
                found.append(cur)
    return found


def artifacts_revealing(
    artifacts: list[Artifact], event_ids: set[str]
) -> list[Artifact]:
    out = []
    for a in artifacts:
        if event_ids.intersection(a.reveals_events):
            out.append(a)
    return out


def partition_unique_pool(
    artifacts: list[Artifact],
    spec: QuerySpec,
    walk_ids: list[str],
) -> dict[str, list[Artifact]]:
    """Split unique documents into essential / graph-hard-negatives / rest.

    Never returns clones. Callers must grow the unique pool if rest is short.
    """
    ess_ids = set(spec.essential_artifact_ids)
    walk_set = set(walk_ids)
    essential, hard, rest = [], [], []
    seen: set[str] = set()
    for a in artifacts:
        if a.artifact_id in seen:
            continue
        seen.add(a.artifact_id)
        if a.artifact_id in ess_ids:
            essential.append(a)
        elif walk_set.intersection(a.reveals_events):
            hard.append(a)
        else:
            rest.append(a)
    return {"essential": essential, "hard": hard, "rest": rest}


def graph_stats(world: SimulatedWorld, spec: QuerySpec) -> dict[str, Any]:
    g = build_causal_graph(world)
    sub = min_sufficient_subgraph(world, spec)
    return {
        "n_event_nodes": sum(
            1 for _, d in g.nodes(data=True) if d.get("kind") == "event"
        ),
        "n_min_subgraph": sub.number_of_nodes(),
        "n_min_edges": sub.number_of_edges(),
        "proof_depth": proof_depth(world, spec),
        "hop_count": hop_count(world, spec),
        "searchart_width": len(spec.essential_artifact_ids),
        "n_essential_events": len(spec.essential_event_ids),
        "n_essential_artifacts": len(spec.essential_artifact_ids),
    }
