"""Canonical proof-program archive.

Novelty is graph hash of anonymized ops, not world seed.
"""

from __future__ import annotations

from longworld.core.topology import canonical_topology
from longworld.domains.company.queries import QuerySpec


def program_hash(spec: QuerySpec) -> str:
    return canonical_topology(spec)


def is_novel(spec: QuerySpec, seen: set[str]) -> bool:
    h = program_hash(spec)
    if h in seen:
        return False
    seen.add(h)
    return True
