"""Canonical proof-program topology: types and ops, not entity IDs."""

from __future__ import annotations

import math
import re
from collections import Counter

from longworld.domains.company.queries import QuerySpec

_TRAILING_INDEX = re.compile(r"_\d+$")


def event_op(event_id: str) -> str:
    leaf = event_id.split(".")[-1]
    return _TRAILING_INDEX.sub("", leaf)


def canonical_topology(spec: QuerySpec) -> str:
    """Anonymized proof signature. JOIN programs hash to motif compositions."""
    if spec.query_type == "program_join" and spec.program_ops:
        types = sorted(str(s.get("query_type") or "") for s in spec.program_ops)
        steps = "+".join(
            f"{'LOOKUP' if i == 0 else 'JOIN'}:{t}" for i, t in enumerate(types)
        )
        return "|".join(
            [
                spec.domain or "na",
                "+".join(sorted(str(spec.motif or spec.query_type).split("+"))),
                "program_join",
                steps,
                f"d{spec.proof_depth}",
                f"n{len(spec.essential_artifact_ids)}",
            ]
        )
    ops = [event_op(e) for e in spec.essential_event_ids]
    return "|".join(
        [
            spec.domain or "na",
            spec.motif or spec.query_type,
            spec.query_type,
            "+".join(ops),
            f"d{spec.proof_depth}",
            f"n{len(spec.essential_artifact_ids)}",
        ]
    )


def topology_family(spec: QuerySpec) -> str:
    tid = spec.topology_id or ""
    if ":" in tid:
        return tid.split(":", 1)[0]
    return spec.motif or spec.query_type


def effective_number(counts: Counter | dict[str, int]) -> float:
    """N_eff = exp(-sum p log p). 1 if a single pattern monopolizes."""
    if not counts:
        return 0.0
    n = sum(counts.values())
    if n <= 0:
        return 0.0
    ent = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / n
        ent -= p * math.log(p)
    return math.exp(ent)


def max_family_share(counts: Counter | dict[str, int]) -> float:
    n = sum(counts.values())
    if n <= 0:
        return 0.0
    return max(counts.values()) / n
