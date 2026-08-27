from __future__ import annotations

import networkx as nx
import pytest

from longworld.core import graph as graph_module
from longworld.core.causal import build_causal_graph
from longworld.core.graph import graph_stats, min_sufficient_subgraph
from longworld.core.sampler import materialize


def _pairwise_reference(graph: nx.DiGraph, node_ids: list[str]) -> int:
    longest = 1
    for source in node_ids:
        for target in node_ids:
            if source != target and nx.has_path(graph, source, target):
                longest = max(
                    longest,
                    nx.shortest_path_length(graph, source, target) + 1,
                )
    return longest


def test_shortest_path_span_handles_singleton_and_disconnected_nodes() -> None:
    graph = nx.DiGraph()
    graph.add_nodes_from(("a", "b"))

    assert graph_module._max_shortest_path_nodes(graph, ["a"]) == 1
    assert graph_module._max_shortest_path_nodes(graph, ["a", "b"]) == 1

    graph.add_edge("a", "b")

    assert graph_module._max_shortest_path_nodes(graph, ["a", "b"]) == 2


@pytest.mark.parametrize("domain", ["company", "researchlab"])
def test_graph_stats_matches_pairwise_reference(domain: str) -> None:
    materialized = materialize(
        3,
        n_parallel=1,
        n_pulses=0,
        domain=domain,
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = min(materialized.queries, key=lambda item: len(item.sufficient_event_ids))
    graph = build_causal_graph(world)
    subgraph = min_sufficient_subgraph(world, spec)
    essential = [item for item in spec.essential_event_ids if item in subgraph]
    sufficient = [item for item in spec.sufficient_event_ids if item in subgraph]
    if len(sufficient) <= 1:
        sufficient = essential

    stats = graph_stats(world, spec)

    assert stats["n_event_nodes"] == sum(
        data.get("kind") == "event" for _, data in graph.nodes(data=True)
    )
    assert stats["proof_depth"] == _pairwise_reference(subgraph, essential)
    assert stats["hop_count"] == _pairwise_reference(subgraph, sufficient)
