from __future__ import annotations

from typing import Any

from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.queries import (
    QuerySpec,
    _merge_overrides,
    instance_topology,
)
from longworld.domains.researchlab.events import apply_event, check_preconditions


def _sim_for(world: SimulatedWorld) -> WorldSimulator:
    return WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )


def eval_answer(
    world: SimulatedWorld, spec: QuerySpec, state_values: dict[str, Any]
) -> str:
    val = state_values.get(spec.answer_key)
    if spec.query_type == "fork_join":
        cache = state_values.get("cause_cache")
        split = state_values.get("cause_split")
        if not cache or not split:
            return "unknown"
        return f"{cache}+{split}"
    if spec.query_type == "delayed_effect":
        c = state_values.get("tokenizer_commit")
        if not c or state_values.get("rerun_score") is None:
            return "unknown"
        return str(c)
    if spec.query_type == "counterfactual":
        if not state_values.get("release_published"):
            return "unknown"
        val = state_values.get("authoritative_score")
        if val is None:
            return "unknown"
        return str(val)
    if spec.query_type == "contradiction":
        body = state_values.get("body_score")
        auth = state_values.get("authoritative_score")
        if body is None or auth is None:
            return "unknown"
        return f"{body} -> {auth}"
    if spec.query_type == "hidden_bridge":
        lic = state_values.get("ship_license")
        if not lic or not state_values.get("release_published"):
            return "unknown"
        return str(lic)
    if val is None or val is False:
        return "unknown"
    return str(val)


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


def build_lab_queries(world: SimulatedWorld) -> list[QuerySpec]:
    if world.spec.get("prefix") != "focal":
        return []
    p = world.spec["project"]
    paper, model, bench = p["paper"], p["model"], p["benchmark"]
    qid = world.spec["world_id"].split(":")[0]
    as_of_now = max(e.time for e in world.events)
    v1 = _event(world, "report_v1")
    cache = _event(world, "log_stale_cache")
    issue = _event(world, "issue_testset")
    rerun = _event(world, "fix_rerun")
    cam = _event(world, "camera_ready")
    rel = _event(world, "release_note")
    tok = _event(world, "commit_tokenizer")
    lic = _event(world, "license_clause")
    queries: list[QuerySpec] = []

    q_cur = QuerySpec(
        query_id=f"{qid}:current_state",
        query_type="current_state",
        question=(
            f"As of {as_of_now.isoformat()}, what is the authoritative score of "
            f"{model} on {bench} for {paper}? Camera-ready prose is not controlling "
            f"if a later release note exists. Reply with the numeric score only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_score",
        essential_event_ids=[rerun.id, rel.id],
        essential_artifact_ids=[_aid(world, "rerun_json"), _aid(world, "release_note")],
        sufficient_event_ids=[rerun.id, rel.id],
        cf_event_id=rerun.id,
        cf_param_updates={"score": round(float(p["final_score"]) + 1.13, 2)},
        cf_answer="",
        invariance_event_id=cam.id,
        invariance_param_updates={"body_score": 11.11},
        gold_expression="authoritative_score adopts rerun",
        proof_depth=3,
        cf_op="score",
        motif="supersession",
        topology_id=instance_topology(
            "lab.release_adopts_rerun", paper, p["final_score"]
        ),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_cur)

    q_fj = QuerySpec(
        query_id=f"{qid}:fork_join",
        query_type="fork_join",
        question=(
            f"For {paper}, which two independent evaluation faults jointly explain "
            f"why the January number exceeded the corrected run? Reply with the "
            f"lab-private cause token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="inflation_cause",
        essential_event_ids=[cache.id, issue.id],
        essential_artifact_ids=[_aid(world, "eval_log"), _aid(world, "github_issue")],
        sufficient_event_ids=[cache.id, issue.id],
        cf_event_id=issue.id,
        cf_param_updates={
            "cause_split": f"SPLIT{int(p['final_score'] * 10) % 90 + 10}"
        },
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "dead00"},
        gold_expression="cache token + split token",
        proof_depth=2,
        cf_op="score",
        motif="fork_join",
        topology_id=instance_topology("lab.cache_and_split", paper, p["cause_token"]),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_fj)

    q_del = QuerySpec(
        query_id=f"{qid}:delayed_effect",
        query_type="delayed_effect",
        question=(
            f"Which git commit of the {paper} tokenizer change is the delayed cause "
            f"that only becomes decision-relevant after the May rerun? Reply with "
            f"the six-hex commit token only. Ignore later release notes."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="tokenizer_commit",
        essential_event_ids=[tok.id, rerun.id],
        essential_artifact_ids=[_aid(world, "git_commit"), _aid(world, "rerun_json")],
        sufficient_event_ids=[tok.id, rerun.id],
        cf_event_id=tok.id,
        cf_param_updates={"commit": f"{p['commit_hash'][:3]}aaa"},
        cf_answer="",
        invariance_event_id=cam.id,
        invariance_param_updates={"body_score": 0.01},
        gold_expression="tokenizer_commit after delayed rerun",
        proof_depth=2,
        cf_op="score",
        motif="delayed_effect",
        topology_id=instance_topology(
            "lab.tokenizer_then_rerun", paper, p["commit_hash"]
        ),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_del)

    q_con = QuerySpec(
        query_id=f"{qid}:contradiction",
        query_type="contradiction",
        question=(
            f"For {paper}, the camera-ready body still quotes an old number while "
            f"the authoritative channel quotes a later number. Reply exactly as "
            f"<body_score> -> <authoritative_score>."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_score",
        essential_event_ids=[cam.id, rel.id, rerun.id],
        essential_artifact_ids=[
            _aid(world, "camera_ready"),
            _aid(world, "release_note"),
            _aid(world, "rerun_json"),
        ],
        sufficient_event_ids=[cam.id, rel.id, rerun.id],
        cf_event_id=cam.id,
        cf_param_updates={"body_score": round(float(p["v1_score"]) - 5.13, 2)},
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "beef00"},
        gold_expression="body_score -> authoritative_score",
        proof_depth=2,
        cf_op="score",
        motif="contradiction",
        topology_id=instance_topology(
            "lab.body_vs_release", p["v1_score"], p["final_score"]
        ),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_con)

    q_cf = QuerySpec(
        query_id=f"{qid}:counterfactual",
        query_type="counterfactual",
        question=(
            f"Suppose the {bench} test-set Issue for {paper} had never been filed. "
            f"What would the authoritative score be as of {as_of_now.isoformat()}? "
            f"Reply with the numeric score only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_score",
        essential_event_ids=[v1.id, rel.id],
        essential_artifact_ids=[_aid(world, "arxiv_v1"), _aid(world, "release_note")],
        sufficient_event_ids=[v1.id, rel.id],
        cf_event_id=v1.id,
        cf_param_updates={"score": round(float(p["v1_score"]) + 3.07, 2)},
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "ffff00"},
        gold_expression="cf: issue never filed keeps v1",
        proof_depth=3,
        cf_op="score",
        motif="counterfactual_supersession",
        topology_id=instance_topology("lab.issue_not_filed", paper, p["v1_score"]),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
        question_overrides={
            issue.id: {"filed": False},
            rerun.id: {"aborted": True},
        },
    )
    queries.append(q_cf)

    q_hid = QuerySpec(
        query_id=f"{qid}:hidden_bridge",
        query_type="hidden_bridge",
        question=(
            f"Which SPDX identifier from the early {paper} LICENSE file becomes "
            f"the shipping license only when the July repository release is cut? "
            f"Reply with the SPDX token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="ship_license",
        essential_event_ids=[lic.id, rel.id],
        essential_artifact_ids=[
            _aid(world, "license_note"),
            _aid(world, "release_note"),
        ],
        sufficient_event_ids=[lic.id, rel.id],
        cf_event_id=lic.id,
        cf_param_updates={"spdx": f"BSD-3-L{p['spdx'][-4:]}"},
        cf_answer="",
        invariance_event_id=tok.id,
        invariance_param_updates={"commit": "aa00aa"},
        gold_expression="ship_license copied at release from pending license",
        proof_depth=2,
        cf_op="version",
        motif="hidden_bridge",
        topology_id=instance_topology("lab.license_then_release", paper, p["spdx"]),
        domain="researchlab",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_hid)

    out: list[QuerySpec] = []
    for q in queries:
        q.answer = gold_from_full(world, q)
        q.cf_answer = cf_from_full(world, q)
        out.append(q)
    return out
