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


def _min_sufficient_subgraph(graph: nx.DiGraph, spec: QuerySpec) -> nx.DiGraph:
    keep = event_ancestors(
        graph, list(spec.essential_event_ids) + list(spec.sufficient_event_ids)
    )
    keep |= set(spec.essential_event_ids)
    return graph.subgraph(keep).copy()


def min_sufficient_subgraph(world: SimulatedWorld, spec: QuerySpec) -> nx.DiGraph:
    """Smallest event subgraph that includes essential events and their ancestors."""
    return _min_sufficient_subgraph(build_causal_graph(world), spec)


def _max_shortest_path_nodes(subgraph: nx.DiGraph, node_ids: list[str]) -> int:
    if len(node_ids) <= 1:
        return 1
    longest = 1
    targets = set(node_ids)
    for source in node_ids:
        distances = nx.single_source_shortest_path_length(subgraph, source)
        longest = max(
            longest,
            max(
                (
                    distance + 1
                    for target, distance in distances.items()
                    if target != source and target in targets
                ),
                default=1,
            ),
        )
    return longest


def _max_dependency_path_nodes(subgraph: nx.DiGraph, node_ids: list[str]) -> int:
    """Longest directed dependency path whose endpoints are declared nodes."""
    nodes = [node_id for node_id in node_ids if node_id in subgraph]
    if len(nodes) <= 1:
        return 1
    targets = set(nodes)
    topological = list(nx.topological_sort(subgraph))
    longest = 1
    for source in nodes:
        distances = {source: 1}
        for node in topological:
            distance = distances.get(node)
            if distance is None:
                continue
            for child in subgraph.successors(node):
                distances[child] = max(distances.get(child, 0), distance + 1)
        longest = max(
            longest,
            max(
                (
                    distance
                    for target, distance in distances.items()
                    if target != source and target in targets
                ),
                default=1,
            ),
        )
    return longest


def hop_count(world: SimulatedWorld, spec: QuerySpec) -> int:
    """SearchArt-style depth: longest path on the sufficient/essential subgraph."""
    sub = min_sufficient_subgraph(world, spec)
    nodes = [e for e in spec.sufficient_event_ids if e in sub]
    if len(nodes) <= 1:
        nodes = [e for e in spec.essential_event_ids if e in sub]
    return _max_shortest_path_nodes(sub, nodes)


def proof_depth(world: SimulatedWorld, spec: QuerySpec) -> int:
    """Longest directed path among essential events (1 if a singleton)."""
    sub = min_sufficient_subgraph(world, spec)
    ess = [e for e in spec.essential_event_ids if e in sub]
    return _max_dependency_path_nodes(sub, ess)


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
                if (
                    data.get("kind") in allowed_kinds
                    and dst not in forbid
                    and graph.nodes[dst].get("kind") == "event"
                ):
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
    sub = _min_sufficient_subgraph(g, spec)
    sufficient = [e for e in spec.sufficient_event_ids if e in sub]
    if len(sufficient) <= 1:
        sufficient = [e for e in spec.essential_event_ids if e in sub]
    essential = [e for e in spec.essential_event_ids if e in sub]
    return {
        "n_event_nodes": sum(
            1 for _, d in g.nodes(data=True) if d.get("kind") == "event"
        ),
        "n_min_subgraph": sub.number_of_nodes(),
        "n_min_edges": sub.number_of_edges(),
        "proof_depth": _max_dependency_path_nodes(sub, essential),
        "hop_count": _max_shortest_path_nodes(sub, sufficient),
        "searchart_width": len(spec.essential_artifact_ids),
        "n_essential_events": len(spec.essential_event_ids),
        "n_essential_artifacts": len(spec.essential_artifact_ids),
    }
