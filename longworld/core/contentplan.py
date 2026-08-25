"""Structured content plans stored on artifacts, not dumped into the document.

SearchArt conditions generation on an evidence subgraph; we condition render
on a plan (goal + new propositions). The plan stays in slots so it cannot
become a kv shortcut in the packed context.
"""

from __future__ import annotations

from typing import Any

from longworld.core.render import Artifact


def plan_from_artifact(artifact: Artifact) -> dict[str, Any]:
    slots = artifact.slots or {}
    props = [str(x) for x in (slots.get("ground_values") or []) if str(x)]
    et = str(slots.get("event_type") or artifact.doc_type)
    register = str(slots.get("register") or "instrument")
    return {
        "artifact_type": artifact.doc_type,
        "source_events": list(artifact.reveals_events),
        "communicative_goal": et,
        "new_propositions": props,
        "style_cluster": f"{register}:{artifact.doc_type}",
    }


def attach_content_plans(artifacts: list[Artifact]) -> list[Artifact]:
    for a in artifacts:
        plan = plan_from_artifact(a)
        a.slots = {**(a.slots or {}), "content_plan": plan}
        if not a.role:
            a.role = ""
    return artifacts


def proposition_novelty(artifact: Artifact, seen: set[str]) -> int:
    plan = (artifact.slots or {}).get("content_plan") or plan_from_artifact(artifact)
    props = [p for p in plan.get("new_propositions") or [] if p]
    n = 0
    for p in props:
        if p not in seen:
            seen.add(p)
            n += 1
    return n
