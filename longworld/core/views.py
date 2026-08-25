from __future__ import annotations

from typing import Any

from longworld.core.domain import handlers
from longworld.core.render import Artifact, render_world
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.queries import QuerySpec


def _patch_events(
    events: list[Event], overrides: dict[str, dict[str, Any]]
) -> list[Event]:
    out: list[Event] = []
    for ev in events:
        if ev.id not in overrides:
            out.append(ev)
            continue
        out.append(
            Event(
                id=ev.id,
                type=ev.type,
                time=ev.time,
                params={**ev.params, **overrides[ev.id]},
                visibility=list(ev.visibility),
                preconditions=list(ev.preconditions),
                causal_inputs=list(ev.causal_inputs),
                required_inputs=list(ev.required_inputs),
                relation_kinds=dict(ev.relation_kinds),
            )
        )
    return out


def resimulate(
    world: SimulatedWorld, overrides: dict[str, dict[str, Any]] | None = None
) -> SimulatedWorld:
    events = _patch_events(world.events, overrides or {})
    check_preconditions, apply_event = handlers(world)
    sim = WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )
    ran = sim.run(events)
    ran.spec = world.spec
    return ran


def render_cf_view(
    world: SimulatedWorld, spec: QuerySpec
) -> tuple[SimulatedWorld, list[Artifact]]:
    cf_world = resimulate(world, {spec.cf_event_id: spec.cf_param_updates})
    return cf_world, render_world(cf_world)


def split_views(
    focal_artifacts: list[Artifact],
    parallel_artifacts: list[Artifact],
    spec: QuerySpec,
    cf_artifacts: list[Artifact],
) -> dict[str, list[Artifact]]:
    ess = set(spec.essential_artifact_ids)
    full = list(focal_artifacts) + list(parallel_artifacts)
    minimal = [a for a in focal_artifacts if a.artifact_id in ess]
    dist_only = [a for a in full if a.artifact_id not in ess]
    cf_full = list(cf_artifacts) + list(parallel_artifacts)
    ordered = sorted(full, key=lambda a: (a.time, a.artifact_id))
    return {
        "full": full,
        "minimal": minimal,
        "cf": cf_full,
        "distractor_only": dist_only,
        "ordered_artifact_view": ordered,
        # Deprecated alias: chronological documents, not an ACC trace.
        "trajectory": ordered,
    }


def view_answer(spec: QuerySpec, view: str) -> str:
    if view == "cf":
        return spec.cf_answer
    # Calendar card / distractors cannot support the gold. Training the long
    # answer here is unverified and (for memory) fake long-context length.
    if view in {"distractor_only", "memory"}:
        return "unanswerable"
    return spec.answer


def memory_card(world: SimulatedWorld, spec: QuerySpec) -> str:
    """State-tracking view: process memory without key=value gold dumps."""
    as_of = spec.as_of
    lines = [
        f"# Working notebook for {world.spec.get('world_id')}",
        f"As of {as_of}. Motif {spec.motif or spec.query_type}.",
        (
            "This card records process flags and the event calendar. It does not "
            "dump field assignments. Reconstruct numerals and hashes from cited artifacts."
        ),
        "Calendar:",
    ]
    for ev in world.events:
        if ev.skipped:
            continue
        if as_of is not None and ev.time > as_of:
            continue
        lines.append(f"- {ev.time.isoformat()} {ev.type}")
    flags = []
    values = world.state.values
    for key, label in (
        ("issue_filed", "an enabling issue is on file"),
        ("release_published", "a release or tag channel exists"),
        ("eval_stale", "evaluation cache may be stale"),
        ("test_pass", "the tracked test is green"),
        ("fixed", "a corrected rerun exists"),
    ):
        if key in values:
            flags.append(f"{label}: {'yes' if values.get(key) else 'no'}")
    if flags:
        lines.append("Flags: " + "; ".join(flags) + ".")
    lines.append(
        "Do not answer from this card alone when essential artifacts are absent."
    )
    return "\n".join(lines)
