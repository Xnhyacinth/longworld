from __future__ import annotations

import networkx as nx

from longworld.core.world import SimulatedWorld


def build_causal_graph(world: SimulatedWorld) -> nx.DiGraph:
    """Event → state-key graph plus event-to-event causal_inputs."""
    g = nx.DiGraph()
    for ev in world.events:
        g.add_node(
            ev.id, kind="event", type=ev.type, time=str(ev.time), skipped=ev.skipped
        )
        for parent in ev.causal_inputs:
            kind = ev.relation_kinds.get(parent, "causes")
            g.add_edge(parent, ev.id, kind=kind)
    for delta in world.state.history:
        key_node = f"state:{delta.key}"
        g.add_node(key_node, kind="state", key=delta.key)
        g.add_edge(delta.event_id, key_node, kind="writes")
    return g


def events_writing(world: SimulatedWorld, key: str) -> list[str]:
    return [d.event_id for d in world.state.history if d.key == key]


def last_writer(world: SimulatedWorld, key: str) -> str | None:
    writers = events_writing(world, key)
    return writers[-1] if writers else None


def proof_nodes_for_keys(world: SimulatedWorld, keys: list[str]) -> list[str]:
    nodes: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for eid in events_writing(world, key):
            if eid not in seen:
                seen.add(eid)
                nodes.append(eid)
    return nodes
