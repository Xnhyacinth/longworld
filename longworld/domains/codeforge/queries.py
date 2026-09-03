from __future__ import annotations

import hashlib
import re
from typing import Any

from longworld.core.asof import find_event, world_as_of
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.codeforge.events import apply_event, check_preconditions
from longworld.domains.codeforge.multiband import (
    bind_cumulative_failure_recovery_history,
    bind_cumulative_patch_review_test_history,
    bind_cumulative_release_history,
)
from longworld.domains.company.queries import (
    QuerySpec,
    _merge_overrides,
    instance_topology,
)

_PATCH_EVIDENCE = re.compile(
    r"(?m)^(?:diff --(?:git )?|\d+ files changed \(GitHub commit API summary\):)"
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
    if spec.query_type == "patch_review_test_ancestry":
        selections: list[str] = []
        for op in spec.program_ops:
            if op.get("op") != "JOIN_PATCH_REVIEW_TEST_ANCESTRY":
                continue
            patch_key = str(op["patch_record_key"])
            review_key = str(op["review_record_key"])
            ci_key = str(op["ci_record_key"])
            merge_key = str(op["merge_record_key"])
            release_key = str(op["release_record_key"])
            patch_prefix = f"repo:{patch_key}"
            release_prefix = f"real:release:{release_key}"
            patch_sha256 = state_values.get(f"real:patch:{patch_key}:sha256")
            patch_commit = state_values.get(f"{patch_prefix}:commit")
            review = state_values.get(f"real:review:{review_key}:decision")
            test = state_values.get(f"repo:{ci_key}:test")
            test_result = state_values.get(f"repo:{ci_key}:result")
            test_commit = state_values.get(f"repo:{ci_key}:resolved:commit")
            merge_head = state_values.get(
                f"real:patch_review_merge:{merge_key}:head_commit"
            )
            tag = state_values.get(f"{release_prefix}:tag")
            merge_sha = state_values.get(f"{release_prefix}:ancestry:merge_commit_sha")
            tag_sha = state_values.get(f"{release_prefix}:ancestry:tag_commit_sha")
            compare_status = state_values.get(
                f"{release_prefix}:ancestry:compare_status"
            )
            if (
                not all(
                    (
                        patch_sha256,
                        patch_commit,
                        test,
                        test_commit,
                        merge_head,
                        tag,
                        merge_sha,
                        tag_sha,
                    )
                )
                or review not in {"approved", "rejected"}
                or test_result not in {"passed", "failed"}
                or compare_status not in {"ahead", "identical"}
                or patch_commit != test_commit
                or patch_commit != merge_head
            ):
                return "unknown"
            selections.append(
                f"tag={tag};patch={patch_sha256};review={review};"
                f"test={test}:{test_result};ancestry={merge_sha}->{tag_sha}"
            )
        return " | ".join(selections) if selections else "unknown"
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
    if spec.query_type == "cross_stream":
        spdx = state_values.get("rollback_spdx")
        if spdx is None or state_values.get("rollback_head") is None:
            return "unknown"
        return str(spdx)
    if spec.query_type == "release_eligibility":
        policy = state_values.get("workflow_release_policy")
        if policy == "approved":
            streams = [str(item["id"]) for item in world.spec["project"]["workstreams"]]
            eligibility_required = (
                "review_token",
                "ci_token",
                "clearance_token",
                "merge_token",
                "integrated",
            )
            if any(
                state_values.get(f"ws:{stream}:{key}") is None
                for stream in streams
                for key in eligibility_required
            ):
                return "unknown"
            versions = [
                state_values.get(f"ws:{stream}:integrated") for stream in streams
            ]
            if not versions or any(version is None for version in versions):
                return "unknown"
            return " | ".join(str(version) for version in versions)
        if policy == "blocked":
            docket = state_values.get("release_docket")
            return f"BLOCKED-{docket}" if docket else "unknown"
        return "unknown"
    if spec.query_type in {"release_ci_matrix", "release_license_matrix"}:
        policy = state_values.get("workflow_release_policy")
        if policy == "blocked":
            return (
                "BLOCKED-CI"
                if spec.query_type == "release_ci_matrix"
                else "BLOCKED-LICENSE"
            )
        if policy != "approved":
            return "unknown"
        streams = [str(item["id"]) for item in world.spec["project"]["workstreams"]]
        matrix_required = (
            "version",
            "review_token",
            "ci_token",
            "clearance_token",
            "merge_token",
            "integrated",
        )
        if any(
            state_values.get(f"ws:{stream}:{key}") is None
            for stream in streams
            for key in matrix_required
        ):
            return "unknown"
        value_key = (
            "ci_token" if spec.query_type == "release_ci_matrix" else "clearance_token"
        )
        return " | ".join(
            str(state_values[f"ws:{stream}:{value_key}"]) for stream in streams
        )
    if spec.query_type == "version_selection":
        prefix = spec.answer_key
        tag = state_values.get(f"{prefix}:tag")
        status = state_values.get(f"{prefix}:status")
        if not tag:
            return "unknown"
        if status == "blocked":
            return f"BLOCKED-{tag}"
        if status != "published":
            return "unknown"
        package = state_values.get(f"{prefix}:package")
        version = state_values.get(f"{prefix}:version")
        commit = state_values.get(f"{prefix}:commit")
        if package and version:
            return f"{package}@{version} -> {tag}"
        if commit:
            return f"{commit} -> {tag}"
        return "unknown"
    if spec.query_type == "release_supersession_trace":
        selections: list[str] = []
        for record_key in spec.answer_key.split("|"):
            prefix = f"real:release:{record_key}"
            tag = state_values.get(f"{prefix}:tag")
            status = state_values.get(f"{prefix}:status")
            if not tag:
                return "unknown"
            if status == "blocked":
                selections.append(f"BLOCKED-{tag}")
                continue
            if status != "published":
                return "unknown"
            package = state_values.get(f"{prefix}:package")
            version = state_values.get(f"{prefix}:version")
            commit = state_values.get(f"{prefix}:commit")
            if package and version:
                selections.append(f"{package}@{version} -> {tag}")
            elif commit:
                selections.append(f"{commit} -> {tag}")
            else:
                return "unknown"
        return " | ".join(selections)
    if spec.query_type == "ci_regression_origin":
        answer_keys = spec.answer_key.split("|")
        prefix = answer_keys[0]
        status = state_values.get(f"{prefix}:status")
        run = state_values.get(f"{prefix}:run")
        if status == "passed" and run:
            return f"NO-REGRESSION-{run}"
        commit = state_values.get(f"{prefix}:commit")
        test = state_values.get(f"{prefix}:test")
        if (
            status == "failed"
            and commit
            and test
            and state_values.get(f"{prefix}:recovered") is True
        ):
            answer = f"{commit} :: {test}"
            if len(answer_keys) == 1:
                return answer
            release_prefix = f"real:release:{answer_keys[1]}"
            if state_values.get(f"{release_prefix}:status") != "published":
                return "unknown"
            tag = state_values.get(f"{release_prefix}:tag")
            if not tag:
                return "unknown"
            if len(answer_keys) == 2:
                return f"{answer} -> {tag}"
            return "unknown"
        return "unknown"
    if spec.query_type == "license_compatibility":
        prefix = spec.answer_key
        package = state_values.get(f"{prefix}:package")
        version = state_values.get(f"{prefix}:version")
        license_id = state_values.get(f"{prefix}:license")
        compatible = state_values.get(f"{prefix}:compatible")
        commit = state_values.get(f"{prefix}:commit")
        if (
            not license_id
            or compatible is None
            or not ((package and version) or commit)
        ):
            return "unknown"
        if compatible is False:
            selected = f"{package}@{version}" if package and version else str(commit)
            return f"INCOMPATIBLE-{selected} :: {license_id}"
        if state_values.get(f"{prefix}:status") not in {"published", "approved"}:
            return "unknown"
        selected = f"{package}@{version}" if package and version else str(commit)
        return f"{selected} :: {license_id} :: compatible"
    if spec.query_type == "cross_repo_release_dependency":
        status = state_values.get("cross_repo:status")
        if status == "blocked":
            policy_id = state_values.get("cross_repo:policy_id")
            return f"BLOCKED-{policy_id}" if policy_id else "unknown"
        if status != "approved":
            return "unknown"
        tag = state_values.get("cross_repo:tag")
        commit = state_values.get("cross_repo:commit")
        license_id = state_values.get("cross_repo:license")
        if not tag or not commit or not license_id:
            return "unknown"
        return f"{tag} :: {commit} :: {license_id}"
    if spec.query_type == "failure_recovery_release_trace":
        recovery_ops = [
            op
            for op in spec.program_ops
            if op.get("op") == "JOIN_FAILURE_RECOVERY_RELEASE"
        ]
        if recovery_ops:
            selections: list[str] = []
            for op in recovery_ops:
                fail_ci_key = str(op["fail_ci_record_key"])
                broken_key = str(op["broken_commit_record_key"])
                repair_key = str(op["repair_commit_record_key"])
                review_key = str(op["review_record_key"])
                pass_ci_key = str(op["pass_ci_record_key"])
                merge_key = str(op["merge_record_key"])
                release_key = str(op["release_record_key"])
                release_prefix = f"real:release:{release_key}"
                broken_sha = state_values.get(f"repo:{broken_key}:commit")
                fail_commit = state_values.get(f"repo:{fail_ci_key}:resolved:commit")
                fail_test = state_values.get(f"repo:{fail_ci_key}:test")
                fail_result = state_values.get(f"repo:{fail_ci_key}:result")
                repair_sha = state_values.get(f"repo:{repair_key}:commit")
                review = state_values.get(f"real:review:{review_key}:decision")
                pass_test = state_values.get(f"repo:{pass_ci_key}:test")
                pass_result = state_values.get(f"repo:{pass_ci_key}:result")
                pass_commit = state_values.get(f"repo:{pass_ci_key}:resolved:commit")
                merge_head = state_values.get(
                    f"real:patch_review_merge:{merge_key}:head_commit"
                )
                tag = state_values.get(f"{release_prefix}:tag")
                merge_sha = state_values.get(
                    f"{release_prefix}:ancestry:merge_commit_sha"
                )
                tag_sha = state_values.get(f"{release_prefix}:ancestry:tag_commit_sha")
                compare_status = state_values.get(
                    f"{release_prefix}:ancestry:compare_status"
                )
                if (
                    not all(
                        (
                            broken_sha,
                            fail_commit,
                            fail_test,
                            repair_sha,
                            pass_test,
                            pass_commit,
                            merge_head,
                            tag,
                            merge_sha,
                            tag_sha,
                        )
                    )
                    or fail_result != "failed"
                    or pass_result not in {"passed", "failed"}
                    or review not in {"approved", "rejected"}
                    or compare_status not in {"ahead", "identical"}
                    or broken_sha != fail_commit
                    or repair_sha != pass_commit
                    or repair_sha != merge_head
                    or fail_test != pass_test
                    or broken_sha == repair_sha
                ):
                    return "unknown"
                selections.append(
                    f"tag={tag};broken={broken_sha};fail={fail_test}:{fail_result};"
                    f"repair={repair_sha};review={review};"
                    f"test={pass_test}:{pass_result};ancestry={merge_sha}->{tag_sha}"
                )
            return " | ".join(selections) if selections else "unknown"
        broken = state_values.get("broken_hash")
        flake = state_values.get("flake_token")
        failure = state_values.get("fail_token")
        hotfix = state_values.get("hotfix_hash")
        authoritative = state_values.get("authoritative_head")
        license_id = state_values.get("ship_spdx")
        if not all(
            (broken, flake, failure, hotfix, authoritative, license_id)
        ) or not state_values.get("release_published"):
            return "unknown"
        return f"{broken}[{flake}+{failure}] -> {hotfix}#{authoritative}@{license_id}"
    if val is None or val is False:
        return "unknown"
    return str(val)


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

    q_failure_recovery = QuerySpec(
        query_id=f"{qid}:failure_recovery_release_trace",
        query_type="failure_recovery_release_trace",
        question=(
            f"For {repo}, reconstruct the broken commit, the paired CI and issue "
            "failure tokens, the hotfix, the commit adopted by the release tag, and "
            "the shipped SPDX identifier. Reply exactly as "
            "<broken>[<ci_token>+<issue_token>] -> <hotfix>#<released_head>@<SPDX>. "
            "No single artifact contains the complete trace."
        ),
        answer="",
        as_of=as_of_now,
        answer_key="authoritative_head",
        essential_event_ids=[
            broken.id,
            ci.id,
            issue.id,
            hotfix.id,
            lic.id,
            tag.id,
        ],
        essential_artifact_ids=[
            _aid(world, "broken_commit"),
            _aid(world, "ci_log"),
            _aid(world, "github_issue"),
            _aid(world, "hotfix_commit"),
            _aid(world, "license_note"),
            _aid(world, "tag_release"),
        ],
        sufficient_event_ids=[
            broken.id,
            ci.id,
            issue.id,
            hotfix.id,
            lic.id,
            tag.id,
        ],
        cf_event_id=hotfix.id,
        cf_param_updates={"commit": p["hotfix_hash"][:3] + "fff"},
        cf_answer="",
        invariance_event_id=log.id,
        invariance_param_updates={"quoted_hash": "fffffff"},
        gold_expression=(
            "FOLLOW(broken, ci, issue, hotfix, tag, license) then "
            "FORMAT(failure_recovery_release_trace)"
        ),
        proof_depth=6,
        cf_op="version",
        motif="failure_recovery_release_trace",
        topology_id=instance_topology(
            "code.failure_recovery_release_trace",
            repo,
            p["broken_hash"],
            p["hotfix_hash"],
        ),
        domain="codeforge",
        truth_regime="synthetic_executable",
        program_ops=[
            {"op": "PAIR_CI_AND_ISSUE_FAILURE"},
            {"op": "FOLLOW_HOTFIX_RECOVERY"},
            {"op": "RESOLVE_TAGGED_HEAD"},
            {"op": "ATTACH_SHIPPED_LICENSE"},
            {"op": "FORMAT_FAILURE_RECOVERY_RELEASE"},
        ],
        preferred_length_buckets=["32k", "64k"],
        semantic_growth_group="codeforge_failure_recovery_release",
    )
    if not p.get("real_workflow_ids"):
        queries.append(q_failure_recovery)

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

    rb = find_event(world, "rollback_hotfix")
    if rb is not None:
        q_rb = QuerySpec(
            query_id=f"{qid}:rollback_state",
            query_type="rollback_state",
            question=(
                f"After the emergency revert on {repo}, which git object is HEAD? "
                f"The revert note does not reprint a hash. Reply with the short "
                f"hash only."
            ),
            answer="",
            as_of=world_as_of(world),
            answer_key="rollback_head",
            essential_event_ids=[broken.id, rb.id],
            essential_artifact_ids=[
                _aid(world, "broken_commit"),
                _aid(world, "rollback_note"),
            ],
            sufficient_event_ids=[broken.id, rb.id],
            cf_event_id=broken.id,
            cf_param_updates={"commit": p["broken_hash"][:3] + "eee"},
            cf_answer="",
            invariance_event_id=ci.id,
            invariance_param_updates={"flake_token": "FLAKE11"},
            gold_expression="rollback_head copies broken_hash",
            proof_depth=2,
            cf_op="version",
            motif="rollback",
            topology_id=instance_topology(
                "code.hotfix_reverted", repo, p["broken_hash"]
            ),
            domain="codeforge",
            truth_regime="real_schema_synthetic_instance",
        )
        queries.append(q_rb)

        q_xs = QuerySpec(
            query_id=f"{qid}:cross_stream",
            query_type="cross_stream",
            question=(
                f"After the hotfix revert for {repo}, which SPDX was in force at "
                f"rollback? Reconstruct from LICENSE plus the revert note. Reply "
                f"SPDX only."
            ),
            answer="",
            as_of=world_as_of(world),
            answer_key="rollback_spdx",
            essential_event_ids=[lic.id, rb.id],
            essential_artifact_ids=[
                _aid(world, "license_note"),
                _aid(world, "rollback_note"),
            ],
            sufficient_event_ids=[lic.id, rb.id],
            cf_event_id=lic.id,
            cf_param_updates={"spdx": f"BSD-3-X{p['spdx'][-4:]}"},
            cf_answer="",
            invariance_event_id=ci.id,
            invariance_param_updates={"flake_token": "FLAKE22"},
            gold_expression="rollback_spdx copied at rollback from pending license",
            proof_depth=2,
            cf_op="version",
            motif="cross_workstream",
            topology_id=instance_topology(
                "code.rollback_under_license", repo, p["spdx"]
            ),
            domain="codeforge",
            truth_regime="real_schema_synthetic_instance",
        )
        queries.append(q_xs)

    workstreams = list(p.get("workstreams") or [])
    release = find_event(world, "workflow_release_decision")
    if workstreams and release is not None:
        essential_event_ids: list[str] = []
        essential_artifact_ids: list[str] = []
        for i, _workstream in enumerate(workstreams):
            for stage in ("request", "review", "ci", "license", "merge"):
                essential_event_ids.append(_event(world, f"workflow_{i}_{stage}").id)
                essential_artifact_ids.append(_aid(world, f"workflow_{i}_{stage}"))
        essential_event_ids.append(release.id)
        essential_artifact_ids.append(_aid(world, "workflow_release_decision"))
        q_release = QuerySpec(
            query_id=f"{qid}:release_eligibility",
            query_type="release_eligibility",
            question=(
                f"At the final release gate for {repo}, were all dependency "
                f"workstreams eligible? If approved, return the integrated versions "
                f"in the gate's listed workstream order separated by ` | `. If the "
                f"derived outcome blocked release, return `BLOCKED-` followed by the release "
                f"docket from the original requests."
            ),
            answer="",
            as_of=world_as_of(world),
            answer_key="workflow_release_policy",
            essential_event_ids=essential_event_ids,
            essential_artifact_ids=essential_artifact_ids,
            sufficient_event_ids=list(essential_event_ids),
            cf_event_id=_event(world, "workflow_0_ci").id,
            cf_param_updates={"passed": False},
            cf_answer="",
            invariance_event_id=log.id,
            invariance_param_updates={"quoted_hash": "fffffff"},
            gold_expression=(
                "REQUIRE_ALL(request,review,ci,license,merge) then "
                "FORMAT(integrated_versions)"
            ),
            proof_depth=6,
            cf_op="status",
            motif="cross_stream_release_gate",
            topology_id=instance_topology(
                "code.release_eligibility",
                repo,
                len(workstreams),
                *(str(item["id"]) for item in workstreams),
            ),
            domain="codeforge",
            truth_regime="real_schema_synthetic_instance",
            program_ops=[
                {"op": "FOLLOW_REQUIRED_INPUTS"},
                {"op": "REQUIRE_ALL", "streams": len(workstreams)},
                {"op": "FORMAT_INTEGRATED_VERSIONS"},
            ],
        )
        queries.append(q_release)
        matrix_tasks = (
            (
                "release_ci_matrix",
                "CI run receipts",
                "ci",
                "FORMAT_CI_RECEIPTS",
                "cross_stream_ci_matrix",
            ),
            (
                "release_license_matrix",
                "license clearance receipts",
                "license",
                "FORMAT_LICENSE_CLEARANCES",
                "cross_stream_license_matrix",
            ),
        )
        for query_type, label, cf_stage, format_op, motif in matrix_tasks:
            queries.append(
                QuerySpec(
                    query_id=f"{qid}:{query_type}",
                    query_type=query_type,
                    question=(
                        f"At the final release gate for {repo}, reconstruct every "
                        f"workstream before returning its {label} in the gate's "
                        "listed workstream order separated by ` | `. If the "
                        f"derived outcome is blocked, reply "
                        f"`{'BLOCKED-CI' if cf_stage == 'ci' else 'BLOCKED-LICENSE'}`."
                    ),
                    answer="",
                    as_of=world_as_of(world),
                    answer_key="workflow_release_policy",
                    essential_event_ids=list(essential_event_ids),
                    essential_artifact_ids=list(essential_artifact_ids),
                    sufficient_event_ids=list(essential_event_ids),
                    cf_event_id=_event(world, f"workflow_0_{cf_stage}").id,
                    cf_param_updates={"passed": False}
                    if cf_stage == "ci"
                    else {"compatible": False},
                    cf_answer="",
                    invariance_event_id=log.id,
                    invariance_param_updates={"quoted_hash": "fffffff"},
                    gold_expression=(
                        f"REQUIRE_ALL(request,review,ci,license,merge) then {format_op}"
                    ),
                    proof_depth=6,
                    cf_op="status",
                    motif=motif,
                    topology_id=instance_topology(
                        f"code.{query_type}",
                        repo,
                        len(workstreams),
                        *(str(item["id"]) for item in workstreams),
                    ),
                    domain="codeforge",
                    truth_regime="real_schema_synthetic_instance",
                    program_ops=[
                        {"op": "FOLLOW_REQUIRED_INPUTS"},
                        {"op": "REQUIRE_ALL", "streams": len(workstreams)},
                        {"op": format_op},
                    ],
                )
            )

    queries.extend(_build_real_repo_queries(world, log))

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
        ci.id,
        {"flake_token": "FLAKE33"},
    )
    if q_g is not None:
        queries.append(q_g)
    q_content = build_content_grounded_query(
        world,
        ci.id,
        {"flake_token": "FLAKE33"},
    )
    if q_content is not None:
        queries.append(q_content)
    q_choice = build_source_choice_query(world, ci.id, {"flake_token": "FLAKE33"})
    if q_choice is not None:
        queries.append(q_choice)

    q_rev = build_revisitation_query(world, ci.id, {"flake_token": "FLAKE33"})
    if q_rev is not None:
        queries.append(q_rev)
    q_rat = build_ratification_query(world, ci.id, {"flake_token": "FLAKE33"})
    if q_rat is not None:
        queries.append(q_rat)
    q_dock = build_docket_control_query(world, ci.id, {"flake_token": "FLAKE33"})
    if q_dock is not None:
        queries.append(q_dock)

    out: list[QuerySpec] = []
    for q in queries:
        q.answer = gold_from_full(world, q)
        q.cf_answer = cf_from_full(world, q)
        if q.query_type in {
            "patch_review_test_ancestry",
            "version_selection",
            "release_supersession_trace",
            "ci_regression_origin",
            "license_compatibility",
            "cross_repo_release_dependency",
        } or (
            q.query_type == "failure_recovery_release_trace"
            and any(
                op.get("op") == "JOIN_FAILURE_RECOVERY_RELEASE" for op in q.program_ops
            )
        ):
            _minimize_real_semantic_proof(world, q)
            from longworld.core.graph import proof_depth as replayed_proof_depth

            q.proof_depth = replayed_proof_depth(world, q)
        out.append(q)
    bind_cumulative_release_history(world, out)
    bind_cumulative_patch_review_test_history(world, out)
    bind_cumulative_failure_recovery_history(world, out)
    return out


def _event_artifact_id(world: SimulatedWorld, event: Event) -> str:
    return f"{world.spec['world_id']}.{event.visibility[0]}"


def _required_closure(world: SimulatedWorld, target: Event) -> list[Event]:
    by_id = {event.id: event for event in world.events}
    selected: set[str] = set()

    def visit(event: Event) -> None:
        if event.id in selected:
            return
        for parent_id in event.required_inputs:
            parent = by_id.get(parent_id)
            if parent is not None:
                visit(parent)
        selected.add(event.id)

    visit(target)
    return [event for event in world.events if event.id in selected]


def _proof_depth(events: list[Event], target: Event) -> int:
    selected = {event.id: event for event in events}
    memo: dict[str, int] = {}

    def depth(event_id: str) -> int:
        if event_id in memo:
            return memo[event_id]
        event = selected[event_id]
        parents = [parent for parent in event.required_inputs if parent in selected]
        memo[event_id] = 1 + max((depth(parent) for parent in parents), default=0)
        return memo[event_id]

    return depth(target.id)


def _repo_kind(event: Event, kind: str) -> bool:
    return event.type == "repo_record" and event.params.get("record_kind") == kind


def _minimize_real_semantic_proof(world: SimulatedWorld, spec: QuerySpec) -> None:
    """Keep only body events whose removal changes the real-task answer."""
    event_index = {event.id: event for event in world.events}
    selected = list(spec.essential_event_ids)
    changed = True
    while changed:
        changed = False
        for event_id in list(selected):
            remaining = [item for item in selected if item != event_id]
            state = _sim_for(world).replay_events(
                [event_index[item] for item in remaining if item in event_index],
                up_to=spec.as_of,
                enforce_preconditions=False,
            )
            if eval_answer(world, spec, state.values) == spec.answer:
                selected = remaining
                changed = True
                break
    spec.essential_event_ids = selected
    spec.essential_artifact_ids = [
        _event_artifact_id(world, event_index[event_id])
        for event_id in selected
        if event_id in event_index
    ]


def _build_real_repo_queries(
    world: SimulatedWorld, invariance_event: Event
) -> list[QuerySpec]:
    releases = [event for event in world.events if _repo_kind(event, "release")]
    qid = world.spec["world_id"].split(":")[0]
    repo = world.spec["project"]["repo"]
    queries: list[QuerySpec] = []
    release = None
    release_prefix = ""
    release_events: list[Event] = []
    release_event_ids: list[str] = []
    release_artifact_ids: list[str] = []
    if releases:
        release = max(
            releases,
            key=lambda event: (event.time, event.id),
        )
        release_repo = str(release.params.get("source_url") or repo)
        release_key = str(release.params["record_key"])
        release_prefix = f"real:release:{release_key}"
        release_events = _required_closure(world, release)
        release_event_ids = [event.id for event in release_events]
        release_artifact_ids = [
            _event_artifact_id(world, event) for event in release_events
        ]
    event_by_id = {event.id: event for event in world.events}
    patch_cycles: list[tuple[Event, list[Event], dict[str, Any]]] = []
    recovery_cycles: list[tuple[Event, list[Event], dict[str, Any]]] = []
    for selected_release in sorted(releases, key=lambda event: (event.time, event.id)):
        ancestry = dict(selected_release.params.get("release_ancestry") or {})
        if not ancestry:
            continue
        direct_events = [
            event_by_id[event_id]
            for event_id in selected_release.causal_inputs
            if event_id in event_by_id
        ]
        merge = next(
            (event for event in direct_events if _repo_kind(event, "merge")), None
        )
        passed_ci = [
            event
            for event in direct_events
            if _repo_kind(event, "ci_run") and event.params.get("result") == "passed"
        ]
        if merge is None or not passed_ci:
            continue
        merge_inputs = [
            event_by_id[event_id]
            for event_id in merge.causal_inputs
            if event_id in event_by_id
        ]
        review = next(
            (
                event
                for event in merge_inputs
                if _repo_kind(event, "review")
                and event.params.get("result") == "approved"
            ),
            None,
        )
        patch = next(
            (
                event
                for event in merge_inputs
                if _repo_kind(event, "commit")
                and _PATCH_EVIDENCE.search(str(event.params.get("body_text") or ""))
            ),
            None,
        )
        if patch is None or review is None:
            continue
        patch_commit = str(patch.params.get("commit") or "")
        ci = next(
            (
                event
                for event in passed_ci
                if event.params.get("commit") == patch_commit
                and event.params.get("test")
            ),
            None,
        )
        if ci is None:
            continue
        selected_events = [patch, review, ci, merge, selected_release]
        release_key = str(selected_release.params["record_key"])
        workflow_id = selected_release.params.get("workflow_id")
        recovered_choices: list[tuple[Event, Event, Event]] = []
        for pass_ci in passed_ci:
            test_name = pass_ci.params.get("test")
            if pass_ci.params.get("commit") != patch_commit or not test_name:
                continue
            for event in world.events:
                if (
                    not _repo_kind(event, "ci_run")
                    or event.params.get("workflow_id") != workflow_id
                    or event.params.get("result") != "failed"
                    or event.params.get("test") != test_name
                    or not event.params.get("commit")
                    or event.params.get("commit") == patch_commit
                    or int(event.params.get("source_order") or 0)
                    >= int(pass_ci.params.get("source_order") or 0)
                ):
                    continue
                broken_commit = next(
                    (
                        event_by_id[parent_id]
                        for parent_id in event.causal_inputs
                        if parent_id in event_by_id
                        and _repo_kind(event_by_id[parent_id], "commit")
                        and event_by_id[parent_id].params.get("commit")
                        == event.params.get("commit")
                    ),
                    None,
                )
                if broken_commit is not None:
                    recovered_choices.append((event, broken_commit, pass_ci))
        if recovered_choices:
            fail_ci, broken_commit, pass_ci = max(
                recovered_choices,
                key=lambda item: (
                    int(item[2].params.get("source_order") or 0)
                    - int(item[0].params.get("source_order") or 0),
                    len(str(item[0].params.get("body_text") or "")),
                    item[0].id,
                ),
            )
            recovery_cycles.append(
                (
                    selected_release,
                    [
                        broken_commit,
                        fail_ci,
                        patch,
                        review,
                        pass_ci,
                        merge,
                        selected_release,
                    ],
                    {
                        "op": "JOIN_FAILURE_RECOVERY_RELEASE",
                        "fail_ci_record_key": fail_ci.params["record_key"],
                        "broken_commit_record_key": broken_commit.params["record_key"],
                        "repair_commit_record_key": patch.params["record_key"],
                        "review_record_key": review.params["record_key"],
                        "pass_ci_record_key": pass_ci.params["record_key"],
                        "merge_record_key": merge.params["record_key"],
                        "release_record_key": release_key,
                    },
                )
            )
        patch_cycles.append(
            (
                selected_release,
                selected_events,
                {
                    "op": "JOIN_PATCH_REVIEW_TEST_ANCESTRY",
                    "patch_record_key": patch.params["record_key"],
                    "review_record_key": review.params["record_key"],
                    "ci_record_key": ci.params["record_key"],
                    "merge_record_key": merge.params["record_key"],
                    "release_record_key": release_key,
                },
            )
        )
    patch_cycles = patch_cycles[-8:]
    for cycle_count in (1, 2, 4, 8):
        if cycle_count > len(patch_cycles):
            continue
        selected_cycles = patch_cycles[-cycle_count:]
        selected_events = [
            event
            for _release, cycle_events, _op in selected_cycles
            for event in cycle_events
        ]
        selected_release = selected_cycles[-1][0]
        selected_ci = selected_cycles[-1][1][2]
        release_keys = [
            str(cycle_release.params["record_key"])
            for cycle_release, _events, _op in selected_cycles
        ]
        bucket = {1: "16k", 2: "32k", 4: "64k", 8: "128k"}[cycle_count]
        queries.append(
            QuerySpec(
                query_id=(
                    f"{qid}:patch_review_test_ancestry:{cycle_count}_cycles:"
                    f"{selected_release.params['record_id']}"
                ),
                query_type="patch_review_test_ancestry",
                question=(
                    f"For the latest {cycle_count} real {repo} release cycle"
                    f"{'s' if cycle_count != 1 else ''}, execute each source-linked "
                    "proof joining the merged patch diff, its approved review, one "
                    "selected final pre-merge test result, and the verified "
                    "merge-to-tag ancestry. Report cycles chronologically as "
                    "`tag=...;patch=SHA256;review=approved;test=name:result;"
                    "ancestry=merge_sha->tag_sha`, separated by ` | `."
                ),
                answer="",
                as_of=world_as_of(world),
                answer_key="|".join(release_keys),
                essential_event_ids=[event.id for event in selected_events],
                essential_artifact_ids=[
                    _event_artifact_id(world, event) for event in selected_events
                ],
                sufficient_event_ids=[event.id for event in selected_events],
                cf_event_id=selected_ci.id,
                cf_param_updates={"result": "failed"},
                cf_answer="",
                invariance_event_id=invariance_event.id,
                invariance_param_updates={"quoted_hash": "fffffff"},
                gold_expression=(
                    "JOIN(patch_diff_sha256, approved_review, "
                    "selected_pre_merge_test, verified_release_ancestry)"
                ),
                proof_depth=4 + cycle_count,
                cf_op="test_result",
                motif="patch_review_test_ancestry",
                topology_id=instance_topology(
                    "code.real_patch_review_test_ancestry",
                    *[
                        cycle_release.params["record_id"]
                        for cycle_release, _events, _op in selected_cycles
                    ],
                ),
                domain="codeforge",
                truth_regime="real_workflow_hybrid_executable",
                program_ops=[op for _release, _events, op in selected_cycles],
                preferred_length_buckets=[bucket],
                semantic_growth_group=(
                    f"{selected_release.params.get('source_url') or repo}|"
                    "patch_review_test_ancestry"
                ),
            )
        )
    recovery_cycles = recovery_cycles[-8:]
    for cycle_count in (1, 2, 4, 8):
        if cycle_count > len(recovery_cycles):
            continue
        selected_cycles = recovery_cycles[-cycle_count:]
        selected_events = [
            event
            for _release, cycle_events, _op in selected_cycles
            for event in cycle_events
        ]
        selected_release = selected_cycles[-1][0]
        selected_pass_ci = selected_cycles[-1][1][4]
        release_keys = [
            str(cycle_release.params["record_key"])
            for cycle_release, _events, _op in selected_cycles
        ]
        bucket = {1: "16k", 2: "32k", 4: "64k", 8: "128k"}[cycle_count]
        queries.append(
            QuerySpec(
                query_id=(
                    f"{qid}:failure_recovery_release_trace:{cycle_count}_cycles:"
                    f"{selected_release.params['record_id']}"
                ),
                query_type="failure_recovery_release_trace",
                question=(
                    f"For the latest {cycle_count} real {repo} failure-recovery "
                    f"release cycle{'s' if cycle_count != 1 else ''}, reconstruct "
                    "each source-linked proof from the failing CI commit through the "
                    "repair commit, the approved review, the recovered same-name "
                    "pre-merge test, and the verified merge-to-tag ancestry. Report "
                    "cycles chronologically as "
                    "`tag=...;broken=SHA;fail=name:failed;repair=SHA;review=approved;"
                    "test=name:result;ancestry=merge_sha->tag_sha`, separated by ` | `."
                ),
                answer="",
                as_of=world_as_of(world),
                answer_key="|".join(release_keys),
                essential_event_ids=[event.id for event in selected_events],
                essential_artifact_ids=[
                    _event_artifact_id(world, event) for event in selected_events
                ],
                sufficient_event_ids=[event.id for event in selected_events],
                cf_event_id=selected_pass_ci.id,
                cf_param_updates={"result": "failed"},
                cf_answer="",
                invariance_event_id=invariance_event.id,
                invariance_param_updates={"quoted_hash": "fffffff"},
                gold_expression=(
                    "FOLLOW(failed_ci, repair_commit, approved_review, "
                    "same_name_recovery, verified_release_ancestry) then "
                    "FORMAT(failure_recovery_release_trace)"
                ),
                proof_depth=6 + cycle_count,
                cf_op="test_result",
                motif="failure_recovery_release_trace",
                topology_id=instance_topology(
                    "code.real_failure_recovery_release_trace",
                    *[
                        cycle_release.params["record_id"]
                        for cycle_release, _events, _op in selected_cycles
                    ],
                ),
                domain="codeforge",
                truth_regime="real_workflow_hybrid_executable",
                program_ops=[op for _release, _events, op in selected_cycles],
                preferred_length_buckets=[bucket],
                semantic_growth_group=(
                    f"{selected_release.params.get('source_url') or repo}|"
                    "failure_recovery_release_trace"
                ),
            )
        )
    if release is not None:
        raw_same_repo_releases = sorted(
            [
                event
                for event in releases
                if event.params.get("source_url") == release.params.get("source_url")
            ],
            key=lambda event: (event.time, event.id),
        )
        release_closures = {
            event.id: _required_closure(world, event)
            for event in raw_same_repo_releases
        }
        event_index = {event.id: event for event in world.events}

        def release_cycle_closure(target: Event) -> list[Event]:
            selected: set[str] = set()

            def visit(event: Event) -> None:
                if event.id in selected:
                    return
                for parent_id in event.required_inputs:
                    if event.relation_kinds.get(parent_id) == "supersedes":
                        continue
                    parent = event_index.get(parent_id)
                    if parent is not None:
                        visit(parent)
                selected.add(event.id)

            visit(target)
            return [event for event in world.events if event.id in selected]

        def release_richness(event: Event) -> tuple[int, int, Any, str]:
            closure = release_closures[event.id]
            return (
                len(closure),
                sum(len(str(item.params.get("body_text") or "")) for item in closure),
                event.time,
                event.id,
            )

        def release_is_executable(event: Event) -> bool:
            prefix = f"real:release:{event.params['record_key']}"
            release_ci = [
                item
                for item in release_cycle_closure(event)
                if _repo_kind(item, "ci_run")
            ]
            return bool(
                release_ci
                and any(item.params.get("result") == "passed" for item in release_ci)
                and not any(
                    item.params.get("result") == "failed" for item in release_ci
                )
                and world.state.values.get(f"{prefix}:status") == "published"
                and world.state.values.get(f"{prefix}:tag")
                and (
                    world.state.values.get(f"{prefix}:version")
                    or world.state.values.get(f"{prefix}:commit")
                )
            )

        releases_by_tag: dict[str, list[Event]] = {}
        for event in raw_same_repo_releases:
            tag_key = str(event.params.get("tag") or "").strip().lower()
            if tag_key:
                releases_by_tag.setdefault(tag_key, []).append(event)
        same_repo_releases = sorted(
            [
                max(
                    [event for event in tagged if release_is_executable(event)]
                    or tagged,
                    key=release_richness,
                )
                for tagged in releases_by_tag.values()
            ],
            key=lambda event: (event.time, event.id),
        )
        executable_releases = [
            event for event in same_repo_releases if release_is_executable(event)
        ]
        release_history: list[Event] = []
        for candidate in reversed(executable_releases):
            if not release_history:
                release_history.append(candidate)
            else:
                child = release_history[0]
                if candidate.id in {
                    *child.required_inputs,
                    *child.causal_inputs,
                }:
                    release_history.insert(0, candidate)
        independent_release_cycles = all(
            not any(
                _repo_kind(event, "release") and event.id != release.id
                for event in release_cycle_closure(release)
            )
            for release in release_history
        )
        if len(release_history) > 3:
            release_history = min(
                (
                    release_history[index : index + 3]
                    for index in range(len(release_history) - 2)
                ),
                key=lambda history: release_richness(history[-1]),
            )
        selected_releases = (
            release_history[-1:] if independent_release_cycles else release_history[:1]
        )
        version_buckets = {event.id: "16k" for event in selected_releases}
        for selected_release in selected_releases:
            selected_key = str(selected_release.params["record_key"])
            selected_prefix = f"real:release:{selected_key}"
            selected_events = release_cycle_closure(selected_release)
            selected_event_ids = [event.id for event in selected_events]
            selected_artifact_ids = [
                _event_artifact_id(world, event) for event in selected_events
            ]
            selected_ci = [
                event
                for event in selected_events
                if _repo_kind(event, "ci_run")
                and event.params.get("result") == "passed"
            ]
            selected_tag = world.state.values.get(f"{selected_prefix}:tag")
            selected_version_value = world.state.values.get(
                f"{selected_prefix}:version"
            )
            selected_commit_value = world.state.values.get(f"{selected_prefix}:commit")
            if not (
                selected_tag
                and (selected_version_value or selected_commit_value)
                and selected_ci
            ):
                continue
            direct_inputs = set(selected_release.required_inputs)
            cf_ci = next(
                (event for event in selected_ci if event.id in direct_inputs),
                selected_ci[-1],
            )
            is_latest = selected_release.id == same_repo_releases[-1].id
            release_label = "latest release" if is_latest else f"release {selected_tag}"
            preferred_buckets = [version_buckets[selected_release.id]]
            queries.append(
                QuerySpec(
                    query_id=(
                        f"{qid}:version_selection:"
                        f"{selected_release.params['record_id']}"
                    ),
                    query_type="version_selection",
                    question=(
                        f"For the {release_label} from {release_repo}, apply the "
                        "hybrid world's executable policy: follow the body-linked "
                        "candidate, every release-linked selected CI check, and the "
                        "required supersession chain. The selected-check set is "
                        "observed workflow evidence, not a verified historical "
                        "branch-protection policy. Which package version, or validated "
                        "commit when no package version is stated, was selected? "
                        "Reply `package@version -> tag` or `commit -> tag`. If a "
                        "linked CI gate fails, reply `BLOCKED-tag`."
                    ),
                    answer="",
                    as_of=world_as_of(world),
                    answer_key=selected_prefix,
                    essential_event_ids=selected_event_ids,
                    essential_artifact_ids=selected_artifact_ids,
                    sufficient_event_ids=list(selected_event_ids),
                    cf_event_id=cf_ci.id,
                    cf_param_updates={"result": "failed"},
                    cf_answer="",
                    invariance_event_id=invariance_event.id,
                    invariance_param_updates={"quoted_hash": "fffffff"},
                    gold_expression=(
                        "FOLLOW(release.required_inputs, supersession, ci, candidate) "
                        "then FORMAT(selection, tag)"
                    ),
                    proof_depth=_proof_depth(selected_events, selected_release),
                    cf_op="status",
                    motif="version_selection",
                    topology_id=instance_topology(
                        "code.real_version_selection",
                        *world.spec["project"].get("real_workflow_ids", []),
                        selected_release.params["record_id"],
                    ),
                    domain="codeforge",
                    truth_regime="real_workflow_hybrid_executable",
                    program_ops=[
                        {"op": "FOLLOW_REQUIRED_INPUTS"},
                        {"op": "FOLLOW_RELEASE_SUPERSESSION"},
                        {"op": "REQUIRE_ALL_CI"},
                        {"op": "SELECT_RELEASE_REVISION"},
                    ],
                    preferred_length_buckets=preferred_buckets,
                    semantic_growth_group=f"{release_repo}|release_history",
                )
            )

        if len(release_history) >= 2:
            trace_specs = [(2, "32k")]
            if len(release_history) >= 3:
                trace_specs.append((3, "64k"))
            for trace_count, trace_bucket in trace_specs:
                trace_releases = (
                    release_history[-trace_count:]
                    if independent_release_cycles
                    else release_history[:trace_count]
                )
                trace_event_ids = {
                    event.id
                    for selected_release in trace_releases
                    for event in release_cycle_closure(selected_release)
                }
                trace_events = [
                    event for event in world.events if event.id in trace_event_ids
                ]
                latest_trace_release = trace_releases[-1]
                latest_direct_inputs = set(latest_trace_release.required_inputs)
                latest_ci = [
                    event
                    for event in release_cycle_closure(latest_trace_release)
                    if _repo_kind(event, "ci_run")
                    and event.params.get("result") == "passed"
                ]
                trace_cf_ci = next(
                    (event for event in latest_ci if event.id in latest_direct_inputs),
                    latest_ci[-1],
                )
                trace_keys = [
                    str(event.params["record_key"]) for event in trace_releases
                ]
                queries.append(
                    QuerySpec(
                        query_id=(
                            f"{qid}:release_supersession_trace:{trace_count}_of_"
                            f"{len(release_history)}"
                        ),
                        query_type="release_supersession_trace",
                        question=(
                            f"For the {'final' if independent_release_cycles else 'first'} "
                            f"{trace_count} executable release cycles "
                            f"in the selected {len(release_history)}-cycle history window from "
                            f"{release_repo}, "
                            "apply the hybrid world's executable policy and follow "
                            "each body-linked candidate, its release-linked selected CI checks, "
                            "and the validated executable supersession controls. The "
                            "selected-check set is observed workflow evidence, not a "
                            "verified historical branch-protection policy. Report "
                            "every selection in chronological order, using "
                            "`package@version -> tag` or `commit -> tag` for each "
                            f"cycle and separating the {trace_count} entries with "
                            "` | `. Use `BLOCKED-tag` for a failed cycle."
                        ),
                        answer="",
                        as_of=world_as_of(world),
                        answer_key="|".join(trace_keys),
                        essential_event_ids=[event.id for event in trace_events],
                        essential_artifact_ids=[
                            _event_artifact_id(world, event) for event in trace_events
                        ],
                        sufficient_event_ids=[event.id for event in trace_events],
                        cf_event_id=trace_cf_ci.id,
                        cf_param_updates={"result": "failed"},
                        cf_answer="",
                        invariance_event_id=invariance_event.id,
                        invariance_param_updates={"quoted_hash": "fffffff"},
                        gold_expression=(
                            "FOLLOW_RELEASE_CYCLES then FOLLOW_SUPERSESSION then "
                            "REQUIRE_EACH_CI then FORMAT_CHRONOLOGICAL_SELECTIONS"
                        ),
                        proof_depth=(
                            max(
                                _proof_depth(trace_events, trace_release)
                                for trace_release in trace_releases
                            )
                            + 1
                        ),
                        cf_op="latest_cycle_status",
                        motif="release_supersession_trace",
                        topology_id=instance_topology(
                            "code.real_release_supersession_trace",
                            *world.spec["project"].get("real_workflow_ids", []),
                            *[event.params["record_id"] for event in trace_releases],
                        ),
                        domain="codeforge",
                        truth_regime="real_workflow_hybrid_executable",
                        program_ops=[
                            {"op": "FOLLOW_REQUIRED_INPUTS"},
                            {"op": "FOLLOW_RELEASE_SUPERSESSION"},
                            {
                                "op": "REQUIRE_EACH_RELEASE_CI",
                                "cycle_count": trace_count,
                            },
                            {"op": "ORDER_RELEASE_CYCLES"},
                            {
                                "op": "FORMAT_RELEASE_SELECTION_TRACE",
                                "cycle_count": trace_count,
                            },
                        ],
                        preferred_length_buckets=[trace_bucket],
                        semantic_growth_group=f"{release_repo}|release_history",
                    )
                )

    failed_ci = [
        event
        for event in world.events
        if _repo_kind(event, "ci_run") and event.params.get("result") == "failed"
    ]
    recovered_failures: list[tuple[Event, Event]] = []
    passed_ci = [
        event
        for event in world.events
        if _repo_kind(event, "ci_run") and event.params.get("result") == "passed"
    ]
    for failure in failed_ci:
        recovery = next(
            (
                event
                for event in passed_ci
                if int(event.params.get("source_order") or 0)
                > int(failure.params.get("source_order") or 0)
                and event.params.get("workflow_id") == failure.params.get("workflow_id")
                and event.params.get("test") == failure.params.get("test")
            ),
            None,
        )
        if recovery is not None:
            recovered_failures.append((failure, recovery))
    if recovered_failures:
        cross_commit = [
            pair
            for pair in recovered_failures
            if pair[0].params.get("commit") != pair[1].params.get("commit")
        ]
        ranked_recoveries = sorted(
            cross_commit or recovered_failures,
            key=lambda pair: (
                int(pair[1].params.get("source_order") or 0)
                - int(pair[0].params.get("source_order") or 0),
                sum(
                    len(str(event.params.get("body_text") or ""))
                    for target in pair
                    for event in _required_closure(world, target)
                ),
                pair[0].id,
                pair[1].id,
            ),
            reverse=True,
        )
        emitted_regressions = 0
        for failure, recovery in ranked_recoveries:
            regression_key = str(failure.params["record_key"])
            regression_prefix = f"real:regression:{regression_key}"
            origin_commit = world.state.values.get(f"{regression_prefix}:commit")
            if (
                origin_commit
                and world.state.values.get(f"{regression_prefix}:test")
                and world.state.values.get(f"{regression_prefix}:status") == "failed"
                and world.state.values.get(f"{regression_prefix}:recovered") is True
            ):
                recovery_release = next(
                    (
                        event
                        for event in world.events
                        if _repo_kind(event, "release")
                        and event.params.get("workflow_id")
                        == recovery.params.get("workflow_id")
                        and recovery.id
                        in {item.id for item in release_cycle_closure(event)}
                        and release_is_executable(event)
                    ),
                    None,
                )
                selected_ids = {
                    event.id
                    for target in (failure, recovery)
                    for event in _required_closure(world, target)
                }
                if recovery_release is not None:
                    selected_ids.update(
                        event.id for event in release_cycle_closure(recovery_release)
                    )
                failure_events = [
                    event for event in world.events if event.id in selected_ids
                ]
                origin_event = next(
                    event
                    for event in reversed(failure_events)
                    if event.params.get("commit") == origin_commit
                    and dict(event.params.get("source_body_facts") or {}).get("commit")
                    == origin_commit
                )
                release_key = (
                    str(recovery_release.params["record_key"])
                    if recovery_release is not None
                    else ""
                )
                queries.append(
                    QuerySpec(
                        query_id=(
                            f"{qid}:ci_regression_origin:{failure.params['record_id']}"
                        ),
                        query_type="ci_regression_origin",
                        question=(
                            f"In the real {repo} workflow, which linked commit "
                            "originated the named failing CI check that a later "
                            "same-name check recovered"
                            + (
                                " and which linked release published that recovery? "
                                "Reply `commit :: check -> tag`."
                                if recovery_release is not None
                                else "? Reply `commit :: check`."
                            )
                        ),
                        answer="",
                        as_of=world_as_of(world),
                        answer_key=(
                            f"{regression_prefix}|{release_key}"
                            if release_key
                            else regression_prefix
                        ),
                        essential_event_ids=[event.id for event in failure_events],
                        essential_artifact_ids=[
                            _event_artifact_id(world, event) for event in failure_events
                        ],
                        sufficient_event_ids=[event.id for event in failure_events],
                        cf_event_id=origin_event.id,
                        cf_param_updates={
                            "commit": hashlib.sha1(
                                (f"longworld-counterfactual:{origin_commit}").encode()
                            ).hexdigest()
                            if len(str(origin_commit)) == 40
                            else f"cf-{origin_commit}"
                        },
                        cf_answer="",
                        invariance_event_id=invariance_event.id,
                        invariance_param_updates={"quoted_hash": "fffffff"},
                        gold_expression=(
                            "FOLLOW(failed_ci.links, commit) then "
                            "REQUIRE(same_name_recovery)"
                            + (
                                " then FOLLOW(recovery, release) then "
                                "REQUIRE(release_published)"
                                if recovery_release is not None
                                else ""
                            )
                            + " then FORMAT(commit, check, release)"
                        ),
                        proof_depth=_proof_depth(
                            failure_events, recovery_release or recovery
                        ),
                        cf_op="origin_commit",
                        motif="ci_regression_origin",
                        topology_id=instance_topology(
                            "code.real_ci_regression",
                            failure.params["workflow_id"],
                            failure.params["record_id"],
                        ),
                        domain="codeforge",
                        truth_regime="real_workflow_hybrid_executable",
                        program_ops=[
                            {"op": "FOLLOW_REQUIRED_INPUTS"},
                            {"op": "REQUIRE_SAME_NAME_RECOVERY"},
                            *(
                                [
                                    {"op": "FOLLOW_RECOVERY_RELEASE"},
                                    {"op": "REQUIRE_RELEASE_PUBLISHED"},
                                ]
                                if recovery_release is not None
                                else []
                            ),
                            {"op": "JOIN_CI_TEST_WITH_COMMIT"},
                        ],
                    )
                )
                emitted_regressions += 1
                if emitted_regressions == 2:
                    break

    compatible = world.state.values.get(f"{release_prefix}:compatible")
    license_id = world.state.values.get(f"{release_prefix}:license")
    package = world.state.values.get(f"{release_prefix}:package")
    version = world.state.values.get(f"{release_prefix}:version")
    licenses = [event for event in release_events if _repo_kind(event, "license")]
    if (
        release is not None
        and compatible is True
        and license_id
        and package
        and version
        and licenses
    ):
        license_event = licenses[-1]
        queries.append(
            QuerySpec(
                query_id=f"{qid}:license_compatibility:{release.params['record_id']}",
                query_type="license_compatibility",
                question=(
                    f"For the final real {repo} release, combine the linked dependency "
                    "candidate, license decision, CI gates, and release record. Reply "
                    "`package@version :: SPDX :: compatible`; if the license decision "
                    "is incompatible, reply `INCOMPATIBLE-package@version :: SPDX`."
                ),
                answer="",
                as_of=world_as_of(world),
                answer_key=release_prefix,
                essential_event_ids=release_event_ids,
                essential_artifact_ids=release_artifact_ids,
                sufficient_event_ids=list(release_event_ids),
                cf_event_id=license_event.id,
                cf_param_updates={"compatible": False},
                cf_answer="",
                invariance_event_id=invariance_event.id,
                invariance_param_updates={"quoted_hash": "fffffff"},
                gold_expression="JOIN(candidate, license, ci, release)",
                proof_depth=_proof_depth(release_events, release),
                cf_op="status",
                motif="license_compatibility",
                topology_id=instance_topology(
                    "code.real_license_compatibility",
                    license_event.params["workflow_id"],
                    release.params["record_id"],
                ),
                domain="codeforge",
                truth_regime="real_workflow_hybrid_executable",
                program_ops=[
                    {"op": "FOLLOW_REQUIRED_INPUTS"},
                    {"op": "JOIN_VERSION_LICENSE_RELEASE"},
                ],
            )
        )
    merges = [event for event in world.events if _repo_kind(event, "merge")]
    if merges:
        merge = merges[-1]
        decision_key = str(merge.params["record_key"])
        decision_prefix = f"real:license_decision:{decision_key}"
        decision_events = _required_closure(world, merge)
        compatibility_sources = [
            event for event in decision_events if event.params.get("compatible") is True
        ]
        decision_license = world.state.values.get(f"{decision_prefix}:license")
        decision_commit = world.state.values.get(f"{decision_prefix}:commit")
        if compatibility_sources and decision_license and decision_commit:
            compatibility_source = compatibility_sources[-1]
            queries.append(
                QuerySpec(
                    query_id=f"{qid}:license_compatibility:{merge.params['record_id']}",
                    query_type="license_compatibility",
                    question=(
                        f"For the real merged {repo} workflow, combine the pull-request "
                        "license decision, approved review, merged commit, and repository "
                        "license when separately recorded. Reply "
                        "`commit :: SPDX :: compatible`; if the decision is incompatible, "
                        "reply `INCOMPATIBLE-commit :: SPDX`."
                    ),
                    answer="",
                    as_of=world_as_of(world),
                    answer_key=decision_prefix,
                    essential_event_ids=[event.id for event in decision_events],
                    essential_artifact_ids=[
                        _event_artifact_id(world, event) for event in decision_events
                    ],
                    sufficient_event_ids=[event.id for event in decision_events],
                    cf_event_id=compatibility_source.id,
                    cf_param_updates={"compatible": False},
                    cf_answer="",
                    invariance_event_id=invariance_event.id,
                    invariance_param_updates={"quoted_hash": "fffffff"},
                    gold_expression=(
                        "JOIN(merge, pull_license_decision, approved_review, commit, "
                        "repo_license_if_recorded)"
                    ),
                    proof_depth=_proof_depth(decision_events, merge),
                    cf_op="status",
                    motif="license_compatibility",
                    topology_id=instance_topology(
                        "code.real_license_compatibility_merge",
                        merge.params["workflow_id"],
                        merge.params["record_id"],
                    ),
                    domain="codeforge",
                    truth_regime="real_workflow_hybrid_executable",
                    program_ops=[
                        {"op": "FOLLOW_REQUIRED_INPUTS"},
                        {"op": "REQUIRE_APPROVED_REVIEW"},
                        {"op": "JOIN_COMMIT_LICENSE_DECISION"},
                    ],
                )
            )
    cross_repo = next(
        (event for event in world.events if event.type == "cross_repo_integration"),
        None,
    )
    if (
        cross_repo is not None
        and world.state.values.get("cross_repo:status") == "approved"
    ):
        cross_events = _required_closure(world, cross_repo)
        merge_event = next(
            event
            for event in cross_events
            if _repo_kind(event, "merge")
            and event.params["record_key"] == cross_repo.params["merge_record_key"]
        )
        original_commit = str(merge_event.params.get("commit") or "")
        if original_commit:
            alternate_commit = original_commit[:3] + "f" * max(
                4, len(original_commit) - 3
            )
            queries.append(
                QuerySpec(
                    query_id=f"{qid}:cross_repo_release_dependency",
                    query_type="cross_repo_release_dependency",
                    question=(
                        f"Under simulated integration policy "
                        f"{cross_repo.params['policy_id']}, combine the controlling "
                        f"real release from {cross_repo.params['release_source_url']} "
                        f"with the approved real merge and license from "
                        f"{cross_repo.params['merge_source_url']}. Reply "
                        "`tag :: commit :: SPDX`; if the integration policy is "
                        "blocked, reply `BLOCKED-policy-id`."
                    ),
                    answer="",
                    as_of=world_as_of(world),
                    answer_key="cross_repo:status",
                    essential_event_ids=[event.id for event in cross_events],
                    essential_artifact_ids=[
                        _event_artifact_id(world, event) for event in cross_events
                    ],
                    sufficient_event_ids=[event.id for event in cross_events],
                    cf_event_id=merge_event.id,
                    cf_param_updates={"commit": alternate_commit},
                    cf_answer="",
                    invariance_event_id=invariance_event.id,
                    invariance_param_updates={"quoted_hash": "fffffff"},
                    gold_expression=(
                        "JOIN(real_release, simulated_cross_repo_policy, "
                        "real_approved_merge, real_license)"
                    ),
                    proof_depth=_proof_depth(cross_events, cross_repo),
                    cf_op="version",
                    motif="cross_repo_release_dependency",
                    topology_id=instance_topology(
                        "code.cross_repo_release_dependency",
                        cross_repo.params["release_source_url"],
                        cross_repo.params["merge_source_url"],
                    ),
                    domain="codeforge",
                    truth_regime="real_workflow_hybrid_executable",
                    program_ops=[
                        {"op": "FOLLOW_REQUIRED_INPUTS"},
                        {"op": "REQUIRE_CROSS_REPO_POLICY"},
                        {"op": "JOIN_RELEASE_MERGE_LICENSE"},
                    ],
                    preferred_length_buckets=["32k"],
                    semantic_growth_group="cross_repo_release_dependency",
                )
            )
    return queries
