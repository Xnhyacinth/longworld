"""P0 grafting: identity. LLM may later rewrite prose but never slots."""

from __future__ import annotations

from longworld.core.render import Artifact


def graft_artifacts(artifacts: list[Artifact], enabled: bool = False) -> list[Artifact]:
    if not enabled:
        return artifacts
    # Reserved for P1. Any rewrite must be followed by consistency_scan.
    return artifacts
