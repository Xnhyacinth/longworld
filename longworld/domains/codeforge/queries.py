from __future__ import annotations

from typing import Any

from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.codeforge.events import apply_event, check_preconditions
from longworld.domains.company.queries import (
    QuerySpec,
    _merge_overrides,
    instance_topology,
)


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
        flake = state_values.get("flake_token")
        fail = state_values.get("fail_token")
        if not flake or not fail:
            return "unknown"
        return f"{flake}+{fail}"
    if spec.query_type == "delayed_effect":
        h = state_values.get("broken_hash")
        if not h or state_values.get("flake_token") is None:
            return "unknown"
        return str(h)
    if spec.query_type == "counterfactual":
        if not state_values.get("release_published"):
            return "unknown"
        val = state_values.get("authoritative_head")
        if val is None:
            return "unknown"
        return str(val)
    if spec.query_type == "contradiction":
        quoted = state_values.get("changelog_hash")
        auth = state_values.get("authoritative_head")
        if quoted is None or auth is None:
            return "unknown"
        return f"{quoted} -> {auth}"
    if spec.query_type == "hidden_bridge":
        lic = state_values.get("ship_spdx")
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


def build_code_queries(world: SimulatedWorld) -> list[QuerySpec]:
    if world.spec.get("prefix") != "focal":
        return []
    p = world.spec["project"]
    repo = p["repo"]
    qid = world.spec["world_id"].split(":")[0]
    as_of_now = max(e.time for e in world.events)
    lic = _event(world, "license_clause")
    broken = _event(world, "broken_commit")
    ci = _event(world, "ci_fail")
    issue = _event(world, "issue_bug")
    hotfix = _event(world, "hotfix")
    log = _event(world, "changelog_stale")
    tag = _event(world, "tag_release")
    queries: list[QuerySpec] = []

    q_cur = QuerySpec(
        query_id=f"{qid}:current_state",
        query_type="current_state",
        question=(
            f"As of {as_of_now.isoformat()}, what git object is the authoritative "
            f"HEAD of {repo}? Changelog prose is not a tag. Reply with the short "
            f"hash only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_head",
        essential_event_ids=[hotfix.id, tag.id],
        essential_artifact_ids=[
            _aid(world, "hotfix_commit"),
            _aid(world, "tag_release"),
        ],
        sufficient_event_ids=[hotfix.id, tag.id],
        cf_event_id=hotfix.id,
        cf_param_updates={"commit": p["hotfix_hash"][:3] + "aaa"},
        cf_answer="",
        invariance_event_id=log.id,
        invariance_param_updates={"quoted_hash": "dead000"},
        gold_expression="authoritative_head adopts hotfix HEAD",
        proof_depth=3,
        cf_op="version",
        motif="supersession",
        topology_id=instance_topology("code.tag_adopts_hotfix", repo, p["hotfix_hash"]),
        domain="codeforge",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_cur)

    q_fj = QuerySpec(
        query_id=f"{qid}:fork_join",
        query_type="fork_join",
        question=(
            f"For {repo}, which two independent tokens jointly identify the "
            f"evaluation fault (CI flake plus issue fail)? Reply with the "
            f"lab-private token pair only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="fail_token",
        essential_event_ids=[ci.id, issue.id],
        essential_artifact_ids=[_aid(world, "ci_log"), _aid(world, "github_issue")],
        sufficient_event_ids=[ci.id, issue.id],
        cf_event_id=issue.id,
        cf_param_updates={
            "fail_token": (
                "FAIL88"
                if p["fail_token"]
                == f"FAIL{(int(p['fail_token'][-2:]) + 17) % 90 + 10:02d}"
                else f"FAIL{(int(p['fail_token'][-2:]) + 17) % 90 + 10:02d}"
            )
        },
        cf_answer="",
        invariance_event_id=broken.id,
        invariance_param_updates={"commit": "1111111"},
        gold_expression="flake_token + fail_token",
        proof_depth=2,
        cf_op="version",
        motif="fork_join",
        topology_id=instance_topology(
            "code.flake_and_fail", p["flake_token"], p["fail_token"]
        ),
        domain="codeforge",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_fj)

    q_del = QuerySpec(
        query_id=f"{qid}:delayed_effect",
        query_type="delayed_effect",
        question=(
            f"Which commit of {repo} is the delayed cause that CI later blames "
            f"without reprinting the hash? Reply with the short hash only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="broken_hash",
        essential_event_ids=[broken.id, ci.id],
        essential_artifact_ids=[_aid(world, "broken_commit"), _aid(world, "ci_log")],
        sufficient_event_ids=[broken.id, ci.id],
        cf_event_id=broken.id,
        cf_param_updates={"commit": p["broken_hash"][:3] + "bbb"},
        cf_answer="",
        invariance_event_id=log.id,
        invariance_param_updates={"quoted_hash": "eeeeeee"},
        gold_expression="broken_hash after CI witness",
        proof_depth=2,
        cf_op="version",
        motif="delayed_effect",
        topology_id=instance_topology("code.ci_blames_broken", repo, p["broken_hash"]),
        domain="codeforge",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_del)

    q_con = QuerySpec(
        query_id=f"{qid}:contradiction",
        query_type="contradiction",
        question=(
            f"For {repo}, the changelog still quotes an old revision while the "
            f"authoritative tag adopts a later object. Reply exactly as "
            f"<quoted_hash> -> <authoritative_hash>."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_head",
        essential_event_ids=[log.id, tag.id, hotfix.id],
        essential_artifact_ids=[
            _aid(world, "changelog"),
            _aid(world, "tag_release"),
            _aid(world, "hotfix_commit"),
        ],
        sufficient_event_ids=[log.id, tag.id, hotfix.id],
        cf_event_id=log.id,
        cf_param_updates={"quoted_hash": p["broken_hash"][:3] + "ccc"},
        cf_answer="",
        invariance_event_id=lic.id,
        invariance_param_updates={"spdx": "MIT-X0000"},
        gold_expression="changelog_hash -> authoritative_head",
        proof_depth=2,
        cf_op="version",
        motif="contradiction",
        topology_id=instance_topology(
            "code.changelog_vs_tag", p["broken_hash"], p["hotfix_hash"]
        ),
        domain="codeforge",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_con)

    q_hid = QuerySpec(
        query_id=f"{qid}:hidden_bridge",
        query_type="hidden_bridge",
        question=(
            f"Which SPDX identifier from the early {repo} LICENSE file becomes "
            f"the shipping license only when the git tag is cut? Reply with the "
            f"SPDX token only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="ship_spdx",
        essential_event_ids=[lic.id, tag.id],
        essential_artifact_ids=[
            _aid(world, "license_note"),
            _aid(world, "tag_release"),
        ],
        sufficient_event_ids=[lic.id, tag.id],
        cf_event_id=lic.id,
        cf_param_updates={"spdx": f"BSD-3-X{p['spdx'][-4:]}"},
        cf_answer="",
        invariance_event_id=ci.id,
        invariance_param_updates={"flake_token": "FLAKE00"},
        gold_expression="ship_spdx copied at tag from pending license",
        proof_depth=2,
        cf_op="version",
        motif="hidden_bridge",
        topology_id=instance_topology("code.license_then_tag", repo, p["spdx"]),
        domain="codeforge",
        truth_regime="real_schema_synthetic_instance",
    )
    queries.append(q_hid)

    q_cf = QuerySpec(
        query_id=f"{qid}:counterfactual",
        query_type="counterfactual",
        question=(
            f"Suppose the GitHub issue for {repo} had never been filed. What "
            f"would the authoritative HEAD be as of {as_of_now.isoformat()}? "
            f"Reply with the short hash only."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_head",
        essential_event_ids=[broken.id, tag.id],
        essential_artifact_ids=[
            _aid(world, "broken_commit"),
            _aid(world, "tag_release"),
        ],
        sufficient_event_ids=[broken.id, tag.id],
        cf_event_id=broken.id,
        cf_param_updates={"commit": p["broken_hash"][:3] + "ddd"},
        cf_answer="",
        invariance_event_id=ci.id,
        invariance_param_updates={"flake_token": "FLAKE99"},
        gold_expression="cf: issue never filed keeps broken HEAD",
        proof_depth=3,
        cf_op="version",
        motif="counterfactual_supersession",
        topology_id=instance_topology("code.issue_not_filed", repo, p["broken_hash"]),
        domain="codeforge",
        truth_regime="real_schema_synthetic_instance",
        question_overrides={
            issue.id: {"filed": False},
            hotfix.id: {"aborted": True},
        },
    )
    queries.append(q_cf)

    out: list[QuerySpec] = []
    for q in queries:
        q.answer = gold_from_full(world, q)
        q.cf_answer = cf_from_full(world, q)
        out.append(q)
    return out
