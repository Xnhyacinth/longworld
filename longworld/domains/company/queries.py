from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from longworld.core.asof import core_as_of, find_event, world_as_of
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
    program_ops: list[dict[str, Any]] = field(default_factory=list)
    preferred_length_buckets: list[str] = field(default_factory=list)
    semantic_growth_group: str = ""


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
    if spec.query_type == "compare_belief":
        stale = state_values.get("stale_client_cite")
        legal = state_values.get("legal_effective_version")
        if not stale or not legal:
            return "unknown"
        return f"{stale} || {legal}"
    if spec.query_type == "delayed_effect":
        blocker = state_values.get("release_blocker")
        if not blocker:
            return "unknown"
        return str(blocker)
    if spec.query_type == "cross_stream":
        rec = state_values.get("revenue_recognized")
        legal = state_values.get("legal_effective_version")
        if rec in (None, 0, False) or not legal:
            return "unknown"
        return f"{rec}@{legal}"
    if spec.query_type == "renewal_control_trace":
        first = state_values.get("audit_cycle_1")
        renewal = state_values.get("audit_cycle_2")
        legal = state_values.get("renewal_legal_effective_version")
        release = state_values.get("renewal_release_version")
        if first in (None, 0, False) or renewal in (None, 0, False):
            return "unknown"
        if not legal or not release:
            return "unknown"
        return f"{first}->{renewal}@{legal}#{release}"
    if spec.query_type == "legal_financial_release_trace":
        legal = state_values.get("legal_effective_version")
        release = state_values.get("release_version")
        revenue = state_values.get("revenue_recognized")
        if not legal or not release or revenue in (None, 0, False):
            return "unknown"
        return f"{legal}#{release}@{revenue}"
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
    from longworld.core.domain import eval_answer as deval

    st = _sim_for(world).replay_events(
        world.events, up_to=spec.as_of, param_overrides=_merge_overrides(spec)
    )
    return deval(world, spec, st.values)


def cf_from_full(world: SimulatedWorld, spec: QuerySpec) -> str:
    from longworld.core.domain import eval_answer as deval

    extra = {spec.cf_event_id: spec.cf_param_updates}
    st = _sim_for(world).replay_events(
        world.events,
        up_to=spec.as_of,
        param_overrides=_merge_overrides(spec, extra),
    )
    return deval(world, spec, st.values)


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

    # Core questions stop before process extensions (rollback).
    as_of_now = core_as_of(world)
    as_of_end = world_as_of(world)
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

    q_belief = QuerySpec(
        query_id=f"{qid}:compare_belief",
        query_type="compare_belief",
        question=(
            f"For {project['project']} ({project['contract_id']}), what delivery "
            f"version does the customer still believe is in force, and what "
            f"version is legally effective after the numbered amendment? Reply "
            f"exactly as: <customer_belief> || <legal_version>. Customer email "
            f"is not a legal instrument."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[
            _event(world, "client_cite_old").id,
            roadmap.id,
            supp.id,
        ],
        essential_artifact_ids=[
            _aid(world, "client_email"),
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
        ],
        sufficient_event_ids=[
            _event(world, "client_cite_old").id,
            roadmap.id,
            supp.id,
        ],
        cf_event_id=_event(world, "client_cite_old").id,
        cf_param_updates={"cited_version": project["cf_version"]},
        cf_answer="",
        invariance_event_id=_event(world, "standup_notes").id,
        invariance_param_updates={"version": "noise-beta"},
        gold_expression="stale_client_cite || legal_effective_version",
        proof_depth=3,
        cf_op="version",
        motif="source_authority",
        topology_id=instance_topology(
            "company.belief_vs_legal",
            project["signed_version"],
            project["roadmap_version"],
        ),
        domain="company",
    )
    queries.append(q_belief)

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

    carve = find_event(world, "carveout")
    if carve is not None:
        j = str(project.get("carveout_jurisdiction") or "J-NA")
        q_ex = QuerySpec(
            query_id=f"{qid}:exception_scope",
            query_type="exception_scope",
            question=(
                f"For jurisdiction {j} on contract {project['contract_id']}, which "
                f"delivery version still governs after the numbered amendment? "
                f"The carve-out memo does not reprint the token. Reply with the "
                f"version token only."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="carveout_version",
            essential_event_ids=[sign.id, carve.id],
            essential_artifact_ids=[
                _aid(world, "contract_email"),
                _aid(world, "carveout_memo"),
            ],
            sufficient_event_ids=[sign.id, carve.id],
            cf_event_id=sign.id,
            cf_param_updates={"version": project["cf_version"]},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="carveout_version copies signed packet",
            proof_depth=2,
            cf_op="version",
            motif="exception",
            topology_id=instance_topology(
                "company.carveout_keeps_signed", project["contract_id"], j
            ),
            domain="company",
        )
        queries.append(q_ex)

    hold = find_event(world, "announce_hold")
    if carve is not None and hold is not None:
        q_del = QuerySpec(
            query_id=f"{qid}:delayed_effect",
            query_type="delayed_effect",
            question=(
                f"Which jurisdiction now blocks the public announcement for "
                f"{project['project']} ({project['contract_id']})? The hold notice "
                f"does not reprint the token; reconstruct it from the earlier "
                f"carve-out instrument. Reply with the jurisdiction token only."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="release_blocker",
            essential_event_ids=[carve.id, hold.id],
            essential_artifact_ids=[
                _aid(world, "carveout_memo"),
                _aid(world, "announce_hold"),
            ],
            sufficient_event_ids=[carve.id, hold.id],
            cf_event_id=carve.id,
            cf_param_updates={"jurisdiction": f"J-HOLD{project['contract_id'][-4:]}"},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="release_blocker copies carveout_jurisdiction at announce",
            proof_depth=2,
            cf_op="version",
            motif="delayed_effect",
            topology_id=instance_topology(
                "company.carveout_then_announce",
                project["contract_id"],
                j,
            ),
            domain="company",
        )
        queries.append(q_del)

    rb = find_event(world, "rollback_amendment")
    if rb is not None:
        q_rb = QuerySpec(
            query_id=f"{qid}:rollback_state",
            query_type="rollback_state",
            question=(
                f"After the amendment for {project['contract_id']} was withdrawn, "
                f"what is the legally effective delivery version for "
                f"{project['project']}? Reply with the version token only."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="legal_effective_version",
            essential_event_ids=[sign.id, rb.id],
            essential_artifact_ids=[
                _aid(world, "contract_email"),
                _aid(world, "rollback_memo"),
            ],
            sufficient_event_ids=[sign.id, rb.id],
            cf_event_id=sign.id,
            cf_param_updates={"version": project["cf_version"]},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="rollback restores signed legal version",
            proof_depth=2,
            cf_op="version",
            motif="rollback",
            topology_id=instance_topology(
                "company.amendment_withdrawn",
                project["contract_id"],
                project["signed_version"],
            ),
            domain="company",
        )
        queries.append(q_rb)

        q_xs = QuerySpec(
            query_id=f"{qid}:cross_stream",
            query_type="cross_stream",
            question=(
                f"After the amendment for {project['contract_id']} was withdrawn, "
                f"what recognized revenue is booked against the restored legal "
                f"delivery version for {project['project']}? Reply exactly as "
                f"<integer>@<version> using the executed packet, the July "
                f"restatement, and the withdrawal instrument. Do not use June "
                f"or customer email."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="revenue_recognized",
            essential_event_ids=[sign.id, aud.id, rb.id],
            essential_artifact_ids=[
                _aid(world, "contract_email"),
                _aid(world, "finance_july"),
                _aid(world, "rollback_memo"),
            ],
            sufficient_event_ids=[sign.id, aud.id, rb.id],
            cf_event_id=aud.id,
            cf_param_updates={"amount": int(project["audited_revenue"]) + 173},
            cf_answer="",
            invariance_event_id=_event(world, "client_cite_old").id,
            invariance_param_updates={"cited_version": "v9"},
            gold_expression="revenue_recognized @ post-rollback legal version",
            proof_depth=3,
            cf_op="amount",
            motif="cross_workstream",
            topology_id=instance_topology(
                "company.revenue_against_rolled_legal",
                project["audited_revenue"],
                project["signed_version"],
            ),
            domain="company",
        )
        queries.append(q_xs)

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

    renewal_roadmap = _event(world, "renewal_roadmap")
    renewal_amendment = _event(world, "renewal_amendment")
    renewal_release = _event(world, "renewal_release")
    renewal_audit = _event(world, "renewal_audit")
    q_renewal = QuerySpec(
        query_id=f"{qid}:renewal_control_trace",
        query_type="renewal_control_trace",
        question=(
            f"For the second release cycle of {project['project']} under "
            f"{project['contract_id']}, trace the first final audit amount to the "
            "renewal final audit amount, then identify the delivery version adopted "
            "by the renewal amendment and the renewal release tag. Reply exactly as "
            "<first_audit>-><renewal_audit>@<legal_version>#<release_tag>. The "
            "renewal amendment does not reprint its adopted version."
        ),
        answer="",
        as_of=as_of_end,
        answer_key="audit_cycle_2",
        essential_event_ids=[
            aud.id,
            renewal_roadmap.id,
            renewal_amendment.id,
            renewal_release.id,
            renewal_audit.id,
        ],
        essential_artifact_ids=[
            _aid(world, "finance_july"),
            _aid(world, "renewal_roadmap"),
            _aid(world, "renewal_amendment"),
            _aid(world, "renewal_release"),
            _aid(world, "renewal_audit"),
        ],
        sufficient_event_ids=[renewal_audit.id],
        cf_event_id=renewal_roadmap.id,
        cf_param_updates={
            "version": project["cf_version"],
            "v3_deliverable": f"{project['project']}-renewal-stream-cf",
        },
        cf_answer="",
        invariance_event_id=_event(world, "finance_preview").id,
        invariance_param_updates={"amount": 1},
        gold_expression=(
            "audit_cycle_1 -> audit_cycle_2 @ renewal_legal_effective_version "
            "# renewal_release_version"
        ),
        proof_depth=7,
        cf_op="version",
        motif="longitudinal_cross_workstream",
        topology_id=instance_topology(
            "company.renewal_control_trace",
            project["contract_id"],
            project["renewal_roadmap_version"],
            project["renewal_beta_tag"],
        ),
        domain="company",
        program_ops=[
            {"op": "LOOKUP", "field": "audit_cycle_1"},
            {"op": "LOOKUP", "field": "audit_cycle_2"},
            {"op": "JOIN", "field": "renewal_legal_effective_version"},
            {"op": "JOIN", "field": "renewal_release_version"},
        ],
        preferred_length_buckets=["64k", "128k", "256k"],
        semantic_growth_group="company.renewal_cycle",
    )
    queries.append(q_renewal)

    release = _event(world, "release_beta")
    q_legal_financial = QuerySpec(
        query_id=f"{qid}:legal_financial_release_trace",
        query_type="legal_financial_release_trace",
        question=(
            f"For the first release cycle of {project['project']} under "
            f"{project['contract_id']}, combine the roadmap version adopted by the "
            "legal supplement, the release tag, and the final audited revenue. Reply "
            "exactly as <legal_version>#<release_tag>@<audited_revenue>. No single "
            "document repeats all three values."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="legal_effective_version",
        essential_event_ids=[roadmap.id, supp.id, release.id, aud.id],
        essential_artifact_ids=[
            _aid(world, "roadmap_notes"),
            _aid(world, "legal_email"),
            _aid(world, "beta_notes"),
            _aid(world, "finance_july"),
        ],
        sufficient_event_ids=[
            sign.id,
            roadmap.id,
            supp.id,
            release.id,
            mis.id,
            aud.id,
        ],
        cf_event_id=roadmap.id,
        cf_param_updates={
            "version": project["cf_version"],
            "v3_deliverable": f"{project['project']}-financial-trace-cf",
        },
        cf_answer="",
        invariance_event_id=_event(world, "client_cite_old").id,
        invariance_param_updates={"cited_version": "v9"},
        gold_expression=(
            "JOIN(legal_supplement.roadmap_version, release.tag, audit.final_revenue)"
        ),
        proof_depth=6,
        cf_op="version",
        motif="legal_financial_release_join",
        topology_id=instance_topology(
            "company.legal_financial_release_trace",
            project["contract_id"],
            project["beta_tag"],
        ),
        domain="company",
        program_ops=[
            {"op": "RESOLVE_LEGAL_ADOPTION"},
            {"op": "FOLLOW_RELEASE_TAG"},
            {"op": "REQUIRE_FINAL_AUDIT"},
            {"op": "FORMAT_VERSION_RELEASE_REVENUE"},
        ],
        preferred_length_buckets=["32k", "64k"],
        semantic_growth_group="company.legal_financial_release",
    )
    queries.append(q_legal_financial)

    workstreams = list(project.get("workstreams") or [])
    if len(workstreams) >= 3:
        first_index = 0
        pivot_index = len(workstreams) // 2
        final_index = len(workstreams) - 1
        pivot_recovery = _event(world, f"cycle_{pivot_index:03d}_recovery")
        final_audit = _event(world, f"cycle_{final_index:03d}_audit")
        event_index = {event.id: event for event in world.events}
        closure: set[str] = set()

        def visit(event_id: str) -> None:
            if event_id in closure or event_id not in event_index:
                return
            event = event_index[event_id]
            for parent_id in (*event.required_inputs, *event.causal_inputs):
                visit(parent_id)
            closure.add(event_id)

        visit(final_audit.id)
        sufficient = [
            event.id
            for event in world.events
            if event.id in closure and not event.skipped
        ]
        q_portfolio = QuerySpec(
            query_id=f"{qid}:portfolio_recovery_trace",
            query_type="portfolio_recovery_trace",
            question=(
                f"Across the {len(workstreams)} sequential contract workstreams for "
                f"{project['project']}, reconstruct every cycle in chronological "
                "order. For each cycle report its incident, corrective action, "
                "recovered release, and controlling audit amount as "
                "<incident>=><resolution>#<release>@<audit>; join all cycle records "
                "with ` | `. Recovery and release records do not repeat the incident, "
                "and no single audit summarizes an earlier cycle."
            ),
            answer="",
            as_of=as_of_end,
            answer_key="portfolio_control_trace",
            essential_event_ids=[
                _event(world, f"cycle_{index:03d}_{stage}").id
                for index in range(len(workstreams))
                for stage in ("failure", "recovery", "release", "audit")
            ],
            essential_artifact_ids=[
                _aid(world, f"cycle_{index:03d}_{stage}")
                for index in range(len(workstreams))
                for stage in ("failure", "recovery", "release", "audit")
            ],
            sufficient_event_ids=sufficient,
            cf_event_id=pivot_recovery.id,
            cf_param_updates={
                "resolution_token": (
                    f"FIX-CF-{project['contract_id'][-3:]}-{pivot_index + 1:03d}"
                )
            },
            cf_answer="",
            invariance_event_id=_event(world, "finance_preview").id,
            invariance_param_updates={"amount": 1},
            gold_expression=(
                "FOR_EACH_CYCLE(incident => resolution # release @ audit) "
                "IN_CHRONOLOGICAL_ORDER"
            ),
            proof_depth=5 * len(workstreams) - 1,
            cf_op="version",
            motif="sequential_failure_recovery_portfolio",
            topology_id=instance_topology(
                "company.portfolio_recovery_trace",
                workstreams[first_index]["id"],
                workstreams[pivot_index]["id"],
                workstreams[final_index]["id"],
            ),
            domain="company",
            program_ops=[
                {"op": "FOR_EACH_CYCLE", "count": len(workstreams)},
                {
                    "op": "REQUIRE_FIELDS",
                    "fields": ["incident", "resolution", "release", "audit"],
                },
                {"op": "FORMAT_CHRONOLOGICAL_TRACE"},
            ],
            preferred_length_buckets=["64k"],
            semantic_growth_group="company.portfolio_recovery",
        )
        queries.append(q_portfolio)

    from longworld.core.cascade import (
        build_docket_control_query,
        build_ratification_query,
        build_revisitation_query,
    )
    from longworld.core.grounded import (
        build_content_grounded_query,
        build_source_choice_query,
        build_source_grounded_query,
    )

    q_g = build_source_grounded_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_g is not None:
        queries.append(q_g)
    q_content = build_content_grounded_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_content is not None:
        queries.append(q_content)
    q_choice = build_source_choice_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_choice is not None:
        queries.append(q_choice)

    q_rev = build_revisitation_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_rev is not None:
        queries.append(q_rev)
    q_rat = build_ratification_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_rat is not None:
        queries.append(q_rat)
    q_dock = build_docket_control_query(
        world,
        _event(world, "client_cite_old").id,
        {"cited_version": "v9"},
    )
    if q_dock is not None:
        queries.append(q_dock)

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
