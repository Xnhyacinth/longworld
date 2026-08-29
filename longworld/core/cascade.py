"""Revisitation, ratification, and docket-control over dormant tokens.

Seed writes LT-* ; a separate docket seed writes DK-*. Ack, reopen, and
ratify copy state forward and must not reprint either token.

Revisitation gold is active_latent (LT-* after reopen).
Ratification gold is controlling_latent (LT-* after ratify).
Docket-control gold is controlling_docket (DK-* after ratify, only if
the latent cascade activated). Pack without the docket seed or without
ratify does not answer docket-control.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from longworld.core.asof import find_event, world_as_of
from longworld.core.state import WorldState
from longworld.core.world import Event, SimulatedWorld

SEED_TYPE = "seed_latent"
DECOY_TYPE = "seed_decoy"
DOCKET_TYPE = "seed_docket"
ACK_TYPE = "ack_latent"
REOPEN_TYPE = "reopen_latent"
RATIFY_TYPE = "ratify_latent"
CASCADE_TYPES = frozenset(
    {SEED_TYPE, DECOY_TYPE, DOCKET_TYPE, ACK_TYPE, REOPEN_TYPE, RATIFY_TYPE}
)


def counterfactual_token(token: str) -> str:
    """A different world-private token; last digit flips, never equals gold."""
    if not token:
        return "LT-CF0001"
    alt = f"{token[:-1]}8" if token[-1] == "9" else f"{token[:-1]}9"
    if alt == token:
        return f"{token}X"
    return alt


def cascade_init() -> dict[str, Any]:
    return {
        "pending_latent": None,
        "pending_decoy": None,
        "acked_latent": None,
        "active_latent": None,
        "controlling_latent": None,
        "pending_docket": None,
        "controlling_docket": None,
    }


def apply_cascade(state: WorldState, ev: Event) -> bool:
    t, p, eid, day = ev.type, ev.params, ev.id, ev.time
    if t == SEED_TYPE:
        state.set("pending_latent", p["token"], eid, day)
        return True
    if t == DECOY_TYPE:
        state.set("pending_decoy", p["token"], eid, day)
        return True
    if t == DOCKET_TYPE:
        state.set("pending_docket", p["token"], eid, day)
        return True
    if t == ACK_TYPE:
        src = state.values.get("pending_latent")
        if not src:
            return True
        state.set("acked_latent", src, eid, day)
        return True
    if t == REOPEN_TYPE:
        src = state.values.get("acked_latent")
        if not src:
            return True
        state.set("active_latent", src, eid, day)
        return True
    if t == RATIFY_TYPE:
        src = state.values.get("active_latent")
        if not src:
            return True
        state.set("controlling_latent", src, eid, day)
        dock = state.values.get("pending_docket")
        if dock:
            state.set("controlling_docket", dock, eid, day)
        return True
    return False


def check_cascade(state: WorldState, ev: Event) -> tuple[bool, str | None] | None:
    if ev.type == SEED_TYPE:
        return True, None
    if ev.type == DECOY_TYPE:
        return True, None
    if ev.type == DOCKET_TYPE:
        return True, None
    if ev.type == ACK_TYPE:
        if not state.values.get("pending_latent"):
            return False, "no_pending_latent"
        return True, None
    if ev.type == REOPEN_TYPE:
        if not state.values.get("acked_latent"):
            return False, "no_acked_latent"
        return True, None
    if ev.type == RATIFY_TYPE:
        if not state.values.get("active_latent"):
            return False, "no_active_latent"
        return True, None
    return None


def cascade_events(
    project: dict[str, Any],
    prefix: str,
    start: date,
    *,
    seed_off: int,
    ack_off: int,
    reopen_off: int,
    ratify_off: int,
) -> list[Event]:
    if not (project.get("process") or {}).get("cascade"):
        return []
    token = project.get("latent_token")
    if not token:
        return []

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    out = [
        Event(
            id=eid(SEED_TYPE),
            type=SEED_TYPE,
            time=start + timedelta(days=seed_off),
            params={"token": token},
            visibility=[f"{prefix}.latent_seed"],
            causal_inputs=[],
        ),
    ]
    decoy = str(project.get("decoy_latent_token") or "")
    if decoy and decoy != token:
        out.append(
            Event(
                id=eid(DECOY_TYPE),
                type=DECOY_TYPE,
                time=start + timedelta(days=seed_off + 4),
                params={"token": decoy},
                visibility=[f"{prefix}.latent_decoy"],
                causal_inputs=[],
            )
        )
    docket = str(project.get("docket_token") or "")
    if docket and docket != token:
        out.append(
            Event(
                id=eid(DOCKET_TYPE),
                type=DOCKET_TYPE,
                time=start + timedelta(days=seed_off + 2),
                params={"token": docket},
                visibility=[f"{prefix}.docket_seed"],
                causal_inputs=[],
            )
        )
    ratify_inputs = [eid(REOPEN_TYPE)]
    ratify_kinds = {eid(REOPEN_TYPE): "derived_from"}
    if docket and docket != token:
        ratify_inputs.append(eid(DOCKET_TYPE))
        ratify_kinds[eid(DOCKET_TYPE)] = "derived_from"
    out.extend(
        [
            Event(
                id=eid(ACK_TYPE),
                type=ACK_TYPE,
                time=start + timedelta(days=ack_off),
                params={},
                visibility=[f"{prefix}.latent_ack"],
                causal_inputs=[eid(SEED_TYPE)],
                relation_kinds={eid(SEED_TYPE): "derived_from"},
            ),
            Event(
                id=eid(REOPEN_TYPE),
                type=REOPEN_TYPE,
                time=start + timedelta(days=reopen_off),
                params={},
                visibility=[f"{prefix}.latent_reopen"],
                causal_inputs=[eid(ACK_TYPE)],
                relation_kinds={eid(ACK_TYPE): "derived_from"},
            ),
            Event(
                id=eid(RATIFY_TYPE),
                type=RATIFY_TYPE,
                time=start + timedelta(days=ratify_off),
                params={},
                visibility=[f"{prefix}.latent_ratify"],
                causal_inputs=ratify_inputs,
                relation_kinds=ratify_kinds,
            ),
        ]
    )
    return out


def _world_label(world: SimulatedWorld) -> tuple[str, str]:
    project = world.spec["project"]
    domain = str(world.spec.get("domain") or "company")
    if domain == "researchlab":
        label = str(project.get("paper") or "the paper")
    elif domain == "codeforge":
        label = str(project.get("repo") or "the repository")
    else:
        label = str(project.get("project") or "the project")
    return domain, label


def _token_in_question(spec, token: str, decoy: str) -> bool:
    if token and token in spec.question:
        return True
    return bool(decoy and decoy in spec.question)


def build_revisitation_query(
    world: SimulatedWorld,
    invariance_event_id: str,
    invariance_param_updates: dict[str, Any],
):
    from longworld.domains.company.queries import QuerySpec, instance_topology

    seed = find_event(world, SEED_TYPE)
    ack = find_event(world, ACK_TYPE)
    reopen = find_event(world, REOPEN_TYPE)
    if seed is None or ack is None or reopen is None:
        return None
    project = world.spec["project"]
    token = str(project.get("latent_token") or seed.params.get("token") or "")
    if not token:
        return None
    domain, label = _world_label(world)
    qid = world.spec["world_id"].split(":")[0]
    prefix = world.spec["prefix"]
    spec = QuerySpec(
        query_id=f"{qid}:revisitation",
        query_type="revisitation",
        question=(
            f"After the case-reopened instrument for {label}, which latent "
            f"file-code is now active? The reopen memo and the earlier ack do "
            f"not reprint the code. Reconstruct it from the dormant seed filing "
            f"that only becomes decision-relevant at reopen. Other dormant "
            f"filings that were never acknowledged are not the active code. "
            f"Reply with the file-code token only."
        ),
        answer="",
        as_of=world_as_of(world),
        answer_key="active_latent",
        essential_event_ids=[seed.id, ack.id, reopen.id],
        essential_artifact_ids=[
            f"{world.spec['world_id']}.{prefix}.latent_seed",
            f"{world.spec['world_id']}.{prefix}.latent_ack",
            f"{world.spec['world_id']}.{prefix}.latent_reopen",
        ],
        sufficient_event_ids=[seed.id, ack.id, reopen.id],
        cf_event_id=seed.id,
        cf_param_updates={"token": counterfactual_token(token)},
        cf_answer="",
        invariance_event_id=invariance_event_id,
        invariance_param_updates=dict(invariance_param_updates),
        gold_expression="active_latent copies pending_latent via ack then reopen",
        proof_depth=3,
        cf_op="version",
        motif="revisitation",
        topology_id=instance_topology(f"{domain}.latent_revisit", label, token),
        domain=domain,
        truth_regime="real_schema_synthetic_instance",
    )
    decoy = str(project.get("decoy_latent_token") or "")
    if _token_in_question(spec, token, decoy):
        return None
    return spec


def build_ratification_query(
    world: SimulatedWorld,
    invariance_event_id: str,
    invariance_param_updates: dict[str, Any],
):
    from longworld.domains.company.queries import QuerySpec, instance_topology

    seed = find_event(world, SEED_TYPE)
    ack = find_event(world, ACK_TYPE)
    reopen = find_event(world, REOPEN_TYPE)
    ratify = find_event(world, RATIFY_TYPE)
    if seed is None or ack is None or reopen is None or ratify is None:
        return None
    project = world.spec["project"]
    token = str(project.get("latent_token") or seed.params.get("token") or "")
    if not token:
        return None
    domain, label = _world_label(world)
    qid = world.spec["world_id"].split(":")[0]
    prefix = world.spec["prefix"]
    spec = QuerySpec(
        query_id=f"{qid}:ratification",
        query_type="ratification",
        question=(
            f"After the case-ratified instrument for {label}, which latent "
            f"file-code is now controlling? Reopen made a code active; "
            f"ratification is the later write that makes it controlling. The "
            f"ratify memo, the reopen memo, and the ack do not reprint the "
            f"code. Reconstruct it from the dormant seed filing. Other dormant "
            f"filings that were never acknowledged are not the controlling "
            f"code. Reply with the file-code token only."
        ),
        answer="",
        as_of=world_as_of(world),
        answer_key="controlling_latent",
        essential_event_ids=[seed.id, ack.id, reopen.id, ratify.id],
        essential_artifact_ids=[
            f"{world.spec['world_id']}.{prefix}.latent_seed",
            f"{world.spec['world_id']}.{prefix}.latent_ack",
            f"{world.spec['world_id']}.{prefix}.latent_reopen",
            f"{world.spec['world_id']}.{prefix}.latent_ratify",
        ],
        sufficient_event_ids=[seed.id, ack.id, reopen.id, ratify.id],
        cf_event_id=seed.id,
        cf_param_updates={"token": counterfactual_token(token)},
        cf_answer="",
        invariance_event_id=invariance_event_id,
        invariance_param_updates=dict(invariance_param_updates),
        gold_expression=(
            "controlling_latent copies active_latent via ratify after reopen"
        ),
        proof_depth=4,
        cf_op="version",
        motif="ratification",
        topology_id=instance_topology(f"{domain}.latent_ratify", label, token),
        domain=domain,
        truth_regime="real_schema_synthetic_instance",
    )
    decoy = str(project.get("decoy_latent_token") or "")
    if _token_in_question(spec, token, decoy):
        return None
    return spec


def build_docket_control_query(
    world: SimulatedWorld,
    invariance_event_id: str,
    invariance_param_updates: dict[str, Any],
):
    from longworld.domains.company.queries import QuerySpec, instance_topology

    seed = find_event(world, SEED_TYPE)
    docket = find_event(world, DOCKET_TYPE)
    ack = find_event(world, ACK_TYPE)
    reopen = find_event(world, REOPEN_TYPE)
    ratify = find_event(world, RATIFY_TYPE)
    if (
        seed is None
        or docket is None
        or ack is None
        or reopen is None
        or ratify is None
    ):
        return None
    project = world.spec["project"]
    dock = str(project.get("docket_token") or docket.params.get("token") or "")
    latent = str(project.get("latent_token") or "")
    if not dock or dock == latent:
        return None
    domain, label = _world_label(world)
    qid = world.spec["world_id"].split(":")[0]
    prefix = world.spec["prefix"]
    spec = QuerySpec(
        query_id=f"{qid}:docket_control",
        query_type="docket_control",
        question=(
            f"After the case-ratified instrument for {label}, which docket-code "
            f"is now controlling? The dormant file-code is not the docket-code. "
            f"Ack, reopen, and ratify do not reprint the docket-code. "
            f"Reconstruct it from the early docket filing that only becomes "
            f"controlling when the latent cascade is ratified. Reply with the "
            f"docket-code token only."
        ),
        answer="",
        as_of=world_as_of(world),
        answer_key="controlling_docket",
        essential_event_ids=[seed.id, docket.id, ack.id, reopen.id, ratify.id],
        essential_artifact_ids=[
            f"{world.spec['world_id']}.{prefix}.latent_seed",
            f"{world.spec['world_id']}.{prefix}.docket_seed",
            f"{world.spec['world_id']}.{prefix}.latent_ack",
            f"{world.spec['world_id']}.{prefix}.latent_reopen",
            f"{world.spec['world_id']}.{prefix}.latent_ratify",
        ],
        sufficient_event_ids=[seed.id, docket.id, ack.id, reopen.id, ratify.id],
        cf_event_id=docket.id,
        cf_param_updates={"token": counterfactual_token(dock)},
        cf_answer="",
        invariance_event_id=invariance_event_id,
        invariance_param_updates=dict(invariance_param_updates),
        gold_expression=(
            "controlling_docket copies pending_docket at ratify only if "
            "active_latent is set"
        ),
        proof_depth=4,
        cf_op="version",
        motif="docket_control",
        topology_id=instance_topology(f"{domain}.latent_docket", label, dock),
        domain=domain,
        truth_regime="real_schema_synthetic_instance",
    )
    decoy = str(project.get("decoy_latent_token") or "")
    if _token_in_question(spec, dock, decoy) or (latent and latent in spec.question):
        return None
    return spec
