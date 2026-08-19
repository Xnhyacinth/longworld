from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.events import apply_event, check_preconditions


def instance_topology(family: str, *parts: Any) -> str:
    """Motif family plus instance tokens. Scale unit is this string, not QA count."""
    return family + ":" + "/".join(str(p) for p in parts)


@dataclass
class QuerySpec:
    query_id: str
    query_type: str
    question: str
    answer: str
    as_of: date | None
    answer_key: str
    essential_event_ids: list[str]
    essential_artifact_ids: list[str]
    sufficient_event_ids: list[str]
    cf_event_id: str
    cf_param_updates: dict[str, Any]
    cf_answer: str
    invariance_event_id: str | None
    invariance_param_updates: dict[str, Any] = field(default_factory=dict)
    gold_expression: str = ""
    proof_depth: int = 1
    cf_op: str = "version"  # version | amount | date | score
    question_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    motif: str = ""
    truth_regime: str = "real_schema_synthetic_instance"
    topology_id: str = ""
    domain: str = "company"


def _sim_for(world: SimulatedWorld) -> WorldSimulator:
    return WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )


def state_from_artifacts(
    world: SimulatedWorld,
    artifact_event_ids: list[str],
    as_of: date | None = None,
    skip_ids: set[str] | None = None,
    param_overrides: dict[str, dict[str, Any]] | None = None,
):
    allowed = set(artifact_event_ids)
    events = [e for e in world.events if e.id in allowed]
    return _sim_for(world).replay_events(
        events, up_to=as_of, skip_ids=skip_ids, param_overrides=param_overrides
    )


def eval_answer(
    world: SimulatedWorld, spec: QuerySpec, state_values: dict[str, Any]
) -> str:
    key = spec.answer_key
    val = state_values.get(key)
    if spec.query_type == "version_diff":
        v2 = state_values.get("v2_deliverable")
        v3 = state_values.get("v3_deliverable")
        if not v2 or not v3:
            return "unknown"
        return f"{v2} -> {v3}"
    if spec.query_type == "multi_hop":
        rec = state_values.get("revenue_recognized")
        mis = state_values.get("revenue_misrecorded")
        if not rec or not mis:
            return "unknown"
        return str(int(mis) - int(rec))
    if val is None or val == 0 or val is False:
        return "unknown"
    return str(val)


def _merge_overrides(
    spec: QuerySpec, extra: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in spec.question_overrides.items()
    }
    for eid, upd in (extra or {}).items():
        merged[eid] = {**merged.get(eid, {}), **upd}
    return merged


def gold_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    st = _sim_for(world).replay_events(
        world.events, up_to=spec.as_of, param_overrides=_merge_overrides(spec)
    )
    return eval_answer(world, spec, st.values)


def cf_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    extra = {spec.cf_event_id: spec.cf_param_updates}
    st = _sim_for(world).replay_events(
        world.events,
        up_to=spec.as_of,
        param_overrides=_merge_overrides(spec, extra),
    )
    return eval_answer(world, spec, st.values)


def _event(world: SimulatedWorld, suffix: str) -> Event:
    prefix = world.spec["prefix"]
    eid = f"{prefix}.{suffix}"
    for e in world.events:
        if e.id == eid:
            return e
    raise KeyError(eid)


def _aid(world: SimulatedWorld, suffix: str) -> str:
    return f"{world.spec['world_id']}.{world.spec['prefix']}.{suffix}"


def build_queries(world: SimulatedWorld) -> list[QuerySpec]:
    """Six programmatic question types. Answers come from replay, never from an LLM."""
    project = world.spec["project"]
    prefix = world.spec["prefix"]
    if prefix != "focal":
        return []

    sign = _event(world, "sign_contract")
    roadmap = _event(world, "change_roadmap")
    supp = _event(world, "legal_supplement")
    mis = _event(world, "misrecord_revenue")
    aud = _event(world, "audit_correction")

    qid = world.spec["world_id"].split(":")[0]
    queries: list[QuerySpec] = []

    # 1. current_state — legally effective version after the amendment.
    as_of_now = max(e.time for e in world.events)
    q_cur = QuerySpec(
        query_id=f"{qid}:current_state",
        query_type="current_state",
        question=(
            f"As of {as_of_now.isoformat()}, what is the legally effective delivery "
            f"version for project {project['project']} under contract {project['contract_id']}? "
            f"Customer emails are not legal instruments. Reply with the version token only."
        ),
        answer="",  # filled after gold
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[roadmap.id, supp.id],
        essential_artifact_ids=[
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[roadmap.id, supp.id],
        cf_event_id=roadmap.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression="legal_effective_version @ end",
        proof_depth=3,
        cf_op="version",
        motif="supersession",
        topology_id=instance_topology(
            "company.legal_adopt_roadmap",
            project["contract_id"],
            project["roadmap_version"],
        ),
        domain="company",
    )
    queries.append(q_cur)

    # 2. historical_state — version in force before the amendment.
    hist_day = supp.time - timedelta(days=1)
    q_hist = QuerySpec(
        query_id=f"{qid}:historical_state",
        query_type="historical_state",
        question=(
            f"As of {hist_day.isoformat()} (the day before the legal amendment), what "
            f"delivery version was legally in force for {project['project']} "
            f"({project['contract_id']})? Reply with the version token only."
        ),
        answer="",
        as_of=hist_day,
        answer_key="legal_effective_version",
        essential_event_ids=[sign.id],
        essential_artifact_ids=[_aid(world, "contract_email")],
        sufficient_event_ids=[sign.id],
        cf_event_id=sign.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=roadmap.id,
        invariance_param_updates={
            "version": "v9",
            "v3_deliverable": "noise-deliverable",
        },
        gold_expression="legal_effective_version @ pre-amendment",
        proof_depth=1,
        cf_op="version",
        motif="historical_cut",
        topology_id=instance_topology(
            "company.legal_pre_amendment",
            project["contract_id"],
            project["signed_version"],
        ),
        domain="company",
    )
    queries.append(q_hist)

    # 3. multi_hop — audit delta requires June misrecord AND July restatement.
    q_mh = QuerySpec(
        query_id=f"{qid}:multi_hop",
        query_type="multi_hop",
        question=(
            f"After Internal Audit restated {project['project']}, by how many currency "
            f"units did recognized revenue fall relative to the June close? "
            f"Reply with a single integer (June minus restated). Ignore preview drafts."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="audit_adjustment",
        essential_event_ids=[mis.id, aud.id],
        essential_artifact_ids=[
            _aid(world, "finance_june"),
            _aid(world, "finance_july"),
        ],
        sufficient_event_ids=[mis.id, aud.id],
        cf_event_id=aud.id,
        cf_param_updates={"amount": int(project["audited_revenue"]) + 111},
        cf_answer="",
        invariance_event_id=_event(world, "finance_preview").id,
        invariance_param_updates={"amount": 1},
        gold_expression="revenue_misrecorded - revenue_recognized",
        proof_depth=2,
        cf_op="amount",
        motif="fork_join",
        topology_id=instance_topology(
            "company.audit_delta",
            project["misrecorded_revenue"],
            project["audited_revenue"],
        ),
        domain="company",
    )
    queries.append(q_mh)

    # 4. version_diff
    q_vd = QuerySpec(
        query_id=f"{qid}:version_diff",
        query_type="version_diff",
        question=(
            f"For {project['project']}, what deliverable token did the signed packet "
            f"name, and what deliverable token did the Q-cycle roadmap name? "
            f"Reply exactly as: <v2_token> -> <v3_token>."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="v3_deliverable",
        essential_event_ids=[sign.id, roadmap.id],
        essential_artifact_ids=[
            _aid(world, "contract_email"),
            _aid(world, "roadmap_notes"),
        ],
        sufficient_event_ids=[sign.id, roadmap.id],
        cf_event_id=roadmap.id,
        cf_param_updates={
            "v3_deliverable": f"{project['project'].lower()}-stream-api-99"
        },
        cf_answer="",
        invariance_event_id=_event(world, "standup_notes").id,
        invariance_param_updates={"version": "noise-beta"},
        gold_expression="v2_deliverable -> v3_deliverable",
        proof_depth=2,
        cf_op="version",
        motif="chain",
        topology_id=instance_topology(
            "company.deliverable_pair",
            project["v2_deliverable"],
            project["v3_deliverable"],
        ),
        domain="company",
    )
    queries.append(q_vd)

    # 5. counterfactual question (hypothetical on the original documents)
    q_cf = QuerySpec(
        query_id=f"{qid}:counterfactual",
        query_type="counterfactual",
        question=(
            f"Suppose the legal amendment for {project['contract_id']} had NOT adopted "
            f"the roadmap and had instead kept the originally signed delivery version. "
            f"What would the legally effective delivery version be as of "
            f"{as_of_now.isoformat()}? Reply with the version token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[sign.id],
        essential_artifact_ids=[_aid(world, "contract_email")],
        sufficient_event_ids=[sign.id, supp.id],
        # Document-level twin still has to change a different parameter so y_cf != y.
        cf_event_id=sign.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression="cf: supplement keeps signed version",
        proof_depth=3,
        cf_op="version",
        motif="counterfactual_supersession",
        topology_id=instance_topology(
            "company.legal_keep_signed",
            project["contract_id"],
            project["signed_version"],
        ),
        domain="company",
        question_overrides={
            supp.id: {
                "adopt_roadmap": False,
                "keep_signed": True,
                "version": project["signed_version"],
            }
        },
    )
    queries.append(q_cf)

    # 6. aggregation — final recognized revenue
    q_ag = QuerySpec(
        query_id=f"{qid}:aggregation",
        query_type="aggregation",
        question=(
            f"What is the final recognized revenue for {project['project']} after the "
            f"July restatement? Reply with a single integer. Do not use preview drafts "
            f"or the uncorrected June close."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="revenue_recognized",
        essential_event_ids=[aud.id],
        essential_artifact_ids=[_aid(world, "finance_july")],
        sufficient_event_ids=[mis.id, aud.id],
        cf_event_id=aud.id,
        cf_param_updates={"amount": int(project["audited_revenue"]) - 221},
        cf_answer="",
        invariance_event_id=mis.id,
        # Changing June without changing July should NOT change final recognized
        # if we only read the July file... wait, replay still applies both.
        # For invariance we need an event that does not affect this answer.
        invariance_param_updates={"amount": int(project["misrecorded_revenue"]) + 50},
        gold_expression="revenue_recognized @ end",
        proof_depth=1,
        cf_op="amount",
        motif="aggregation",
        topology_id=instance_topology(
            "company.final_recognized", project["audited_revenue"]
        ),
        domain="company",
    )
    # Fix aggregation invariance: June amount change DOES change the multi-hop
    # delta but NOT final recognized if audit sets absolute amount.
    # Replay: misrecord then audit_correction(amount=audited) → recognized = audited.
    # Changing misrecord amount does NOT change final recognized. Good.
    queries.append(q_ag)

    # Decoy A: intervene on a known-invariance event. Gold must not change;
    # the verifier is supposed to drop this candidate.
    q_decoy_inv = QuerySpec(
        query_id=f"{qid}:decoy_invariance",
        query_type="current_state",
        question=q_cur.question,
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[roadmap.id, supp.id],
        essential_artifact_ids=[
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[roadmap.id, supp.id],
        cf_event_id=_event(world, "client_cite_old").id,
        cf_param_updates={"cited_version": f"NOISE-{project['cf_version']}"},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9-noise"},
        gold_expression="decoy: invariance intervention",
        proof_depth=3,
        cf_op="version",
    )
    queries.append(q_decoy_inv)

    # Decoy B: overspecified essentials. Dropping the contract file still
    # reconstructs the legal version, so remove-one must fail.
    q_decoy_over = QuerySpec(
        query_id=f"{qid}:decoy_overspec",
        query_type="current_state",
        question=q_cur.question,
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[sign.id, roadmap.id, supp.id],
        essential_artifact_ids=[
            _aid(world, "contract_email"),
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[roadmap.id, supp.id],
        cf_event_id=roadmap.id,
        cf_param_updates={"version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression="decoy: overspecified essentials",
        proof_depth=3,
        cf_op="version",
    )
    queries.append(q_decoy_over)

    # Fill gold / cf answers from the engine.
    out: list[QuerySpec] = []
    for q in queries:
        q.answer = gold_from_full(world, q)
        q.cf_answer = cf_from_full(world, q)
        out.append(q)
    return out
