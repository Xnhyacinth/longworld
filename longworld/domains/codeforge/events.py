from __future__ import annotations

from typing import Any

from longworld.core.cascade import apply_cascade, check_cascade
from longworld.core.grounded import apply_grounded, check_grounded
from longworld.core.state import WorldState
from longworld.core.world import Event
from longworld.domains.codeforge.schema import materialize_grounded_repo_record


def init_values(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "head": None,
        "authoritative_head": None,
        "broken_hash": None,
        "hotfix_hash": None,
        "test_pass": False,
        "fail_token": None,
        "flake_token": None,
        "pending_spdx": None,
        "ship_spdx": None,
        "issue_filed": False,
        "changelog_hash": None,
        "release_published": False,
        "repo": project["repo"],
        "package": project["package"],
        "rollback_head": None,
        "rollback_spdx": None,
        "pending_public_primary": None,
        "pending_public_alt": None,
        "public_norm": None,
        "pending_latent": None,
        "pending_decoy": None,
        "acked_latent": None,
        "active_latent": None,
        "controlling_latent": None,
        "pending_docket": None,
        "controlling_docket": None,
        "real:release_cycle": 0,
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    grounded = check_grounded(state, ev)
    if grounded is not None:
        return grounded
    casc = check_cascade(state, ev)
    if casc is not None:
        return casc
    t = ev.type
    p = ev.params
    stream = str(p.get("stream") or "")
    if t == "repo_record":
        return True, None
    if t == "dependency_request":
        return True, None
    if t == "maintainer_review":
        return (
            (True, None)
            if state.values.get(f"ws:{stream}:version")
            else (False, "dependency_not_requested")
        )
    if t == "ci_validation":
        return (
            (True, None)
            if state.values.get(f"ws:{stream}:review") == "approved"
            else (False, "review_not_approved")
        )
    if t == "license_clearance":
        return (
            (True, None)
            if state.values.get(f"ws:{stream}:license")
            else (False, "license_not_declared")
        )
    if t == "integration_merge":
        return True, None
    if t == "release_decision":
        return True, None
    if t == "cross_repo_integration":
        return True, None
    if t == "license_clause":
        return True, None
    if t in {"broken_commit", "status_pulse"}:
        return True, None
    if t in {"ci_fail", "issue_bug", "changelog_stale", "tag_release"}:
        if state.values.get("broken_hash") is None:
            return False, "no_broken"
        return True, None
    if t == "hotfix":
        if not state.values.get("issue_filed"):
            return False, "issue_not_filed"
        return True, None
    if t == "rollback_hotfix":
        if state.values.get("broken_hash") is None:
            return False, "no_broken"
        return True, None
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    if apply_grounded(state, ev):
        return
    if apply_cascade(state, ev):
        return
    t = ev.type
    p = ev.params
    eid = ev.id
    day = ev.time
    stream = str(p.get("stream") or "")
    if t == "repo_record":
        _apply_repo_record(state, ev)
    elif t == "dependency_request":
        state.set(f"ws:{stream}:version", p["version"], eid, day)
        state.set(f"ws:{stream}:license", p["license"], eid, day)
        state.set("release_docket", p["docket"], eid, day)
    elif t == "maintainer_review":
        state.set(f"ws:{stream}:review", p["decision"], eid, day)
        state.set(f"ws:{stream}:review_token", p["review_token"], eid, day)
    elif t == "ci_validation":
        state.set(f"ws:{stream}:ci_pass", bool(p.get("passed")), eid, day)
        state.set(f"ws:{stream}:ci_token", p["ci_token"], eid, day)
    elif t == "license_clearance":
        state.set(
            f"ws:{stream}:license_compatible",
            bool(p.get("compatible")),
            eid,
            day,
        )
        state.set(f"ws:{stream}:clearance_token", p["clearance_token"], eid, day)
    elif t == "integration_merge":
        state.set(f"ws:{stream}:merge_token", p["merge_token"], eid, day)
        eligible = bool(
            state.values.get(f"ws:{stream}:ci_pass")
            and state.values.get(f"ws:{stream}:license_compatible")
        )
        state.set(
            f"ws:{stream}:integration_status",
            "merged" if eligible else "blocked",
            eid,
            day,
        )
        if eligible:
            state.set(
                f"ws:{stream}:integrated",
                state.values.get(f"ws:{stream}:version"),
                eid,
                day,
            )
    elif t == "release_decision":
        streams = [str(item) for item in p.get("streams") or []]
        all_integrated = bool(streams) and all(
            state.values.get(f"ws:{item}:integrated") for item in streams
        )
        outcome = (
            "approved"
            if p.get("policy") == "require_all" and all_integrated
            else "blocked"
        )
        state.set("workflow_release_policy", outcome, eid, day)
    elif t == "cross_repo_integration":
        release_prefix = f"real:release:{p['release_record_key']}"
        merge_prefix = f"real:license_decision:{p['merge_record_key']}"
        release_status = state.values.get(f"{release_prefix}:status")
        merge_status = state.values.get(f"{merge_prefix}:status")
        active = bool(p.get("active"))
        status = (
            "approved"
            if active and release_status == "published" and merge_status == "approved"
            else "blocked"
        )
        state.set("cross_repo:status", status, eid, day)
        state.set("cross_repo:policy_id", p["policy_id"], eid, day)
        for output_key, source_key in (
            ("tag", f"{release_prefix}:tag"),
            ("commit", f"{merge_prefix}:commit"),
            ("license", f"{merge_prefix}:license"),
        ):
            value = state.values.get(source_key)
            if value is not None:
                state.set(f"cross_repo:{output_key}", value, eid, day)
    elif t == "license_clause":
        state.set("pending_spdx", p["spdx"], eid, day)
    elif t == "broken_commit":
        state.set("head", p["commit"], eid, day)
        state.set("broken_hash", p["commit"], eid, day)
        state.set("test_pass", False, eid, day)
    elif t == "ci_fail":
        state.set("flake_token", p["flake_token"], eid, day)
    elif t == "issue_bug":
        if p.get("filed", True):
            state.set("issue_filed", True, eid, day)
            state.set("fail_token", p["fail_token"], eid, day)
    elif t == "hotfix":
        if p.get("aborted"):
            return
        state.set("head", p["commit"], eid, day)
        state.set("hotfix_hash", p["commit"], eid, day)
        state.set("test_pass", True, eid, day)
    elif t == "changelog_stale":
        quoted = p.get("quoted_hash") or state.values.get("broken_hash")
        state.set("changelog_hash", quoted, eid, day)
    elif t == "tag_release":
        state.set("release_published", True, eid, day)
        if p.get("adopt_head"):
            state.set("authoritative_head", state.values.get("head"), eid, day)
        else:
            state.set(
                "authoritative_head",
                p.get("commit", state.values.get("broken_hash")),
                eid,
                day,
            )
        pending = state.values.get("pending_spdx")
        if pending:
            state.set("ship_spdx", pending, eid, day)
    elif t == "rollback_hotfix":
        bh = state.values.get("broken_hash")
        if bh is not None:
            state.set("head", bh, eid, day)
            state.set("rollback_head", bh, eid, day)
        else:
            state.set("rollback_head", "revert-hotfix", eid, day)
        pending = state.values.get("pending_spdx")
        if pending is not None:
            state.set("rollback_spdx", pending, eid, day)
    elif t == "status_pulse":
        return


_REPO_FACT_KEYS = (
    "commit",
    "package",
    "version",
    "run",
    "test",
    "tag",
    "license",
    "compatibility_license",
    "project",
    "dependency_project",
    "result",
    "compatible",
)


def _apply_repo_record(state: WorldState, ev: Event) -> None:
    """Replay a body-grounded repository record and resolve its explicit links."""
    p = ev.params
    materialized = materialize_grounded_repo_record(p)
    p.update(materialized)
    replayed_facts = dict(materialized["replayed_body_facts"])
    record_id = str(p["record_key"])
    source_record_id = str(p["record_id"])
    links = [str(link) for link in materialized["replayed_links"]]
    prefix = f"repo:{record_id}"
    state.set(f"{prefix}:kind", str(p["record_kind"]), ev.id, ev.time)
    state.set(f"{prefix}:body_sha256", str(p["body_sha256"]), ev.id, ev.time)
    state.set(f"{prefix}:links", tuple(links), ev.id, ev.time)

    for key in _REPO_FACT_KEYS:
        if key in replayed_facts:
            value = replayed_facts[key]
            state.set(f"{prefix}:{key}", value, ev.id, ev.time)
            state.set(f"{prefix}:resolved:{key}", value, ev.id, ev.time)
            continue
        for link in links:
            value = state.values.get(f"repo:{link}:resolved:{key}")
            if value is not None:
                state.set(f"{prefix}:resolved:{key}", value, ev.id, ev.time)
                break

    for span in materialized["grounded_fact_spans"]:
        for key in span["values"]:
            evidence_prefix = f"{prefix}:grounded:{key}"
            state.set(f"{evidence_prefix}:fact_id", span["fact_id"], ev.id, ev.time)
            state.set(
                f"{evidence_prefix}:text_sha256",
                span["text_sha256"],
                ev.id,
                ev.time,
            )
            state.set(
                f"{evidence_prefix}:char_start",
                span["char_start"],
                ev.id,
                ev.time,
            )
            state.set(f"{evidence_prefix}:char_end", span["char_end"], ev.id, ev.time)

    if p.get("single_workflow"):
        alias_prefix = f"repo:{source_record_id}"
        for key in _REPO_FACT_KEYS:
            value = state.values.get(f"{prefix}:{key}")
            if value is not None:
                state.set(f"{alias_prefix}:{key}", value, ev.id, ev.time)

    kind = str(p["record_kind"])
    if kind == "ci_run":
        run = state.values.get(f"{prefix}:resolved:run") or source_record_id
        result = state.values.get(f"{prefix}:resolved:result")
        test = state.values.get(f"{prefix}:test")
        regression_prefix = f"real:regression:{record_id}"
        state.set(f"{regression_prefix}:run", run, ev.id, ev.time)
        state.set(f"{regression_prefix}:status", result, ev.id, ev.time)
        if result == "failed":
            commit = state.values.get(f"{prefix}:resolved:commit")
            if commit is not None:
                state.set(f"{regression_prefix}:commit", commit, ev.id, ev.time)
            if test is not None:
                state.set(f"{regression_prefix}:test", test, ev.id, ev.time)
                state.set(f"{regression_prefix}:recovered", False, ev.id, ev.time)
                state.set(
                    f"real:failed_check:{p['workflow_id']}:{test}",
                    record_id,
                    ev.id,
                    ev.time,
                )
        elif result == "passed" and test is not None:
            failed_record_id = state.values.get(
                f"real:failed_check:{p['workflow_id']}:{test}"
            )
            if failed_record_id is not None:
                state.set(
                    f"real:regression:{failed_record_id}:recovered",
                    True,
                    ev.id,
                    ev.time,
                )
    elif kind == "merge":
        decision_prefix = f"real:license_decision:{record_id}"
        compatible = state.values.get(f"{prefix}:resolved:compatible")
        review_result = state.values.get(f"{prefix}:resolved:result")
        license_id = state.values.get(f"{prefix}:resolved:license")
        compatibility_license = state.values.get(
            f"{prefix}:resolved:compatibility_license"
        )
        selected_license = license_id or compatibility_license
        decided = (
            compatible is not None
            and compatibility_license is not None
            and (license_id is None or compatibility_license == license_id)
            and review_result == "approved"
        )
        state.set(
            f"{decision_prefix}:status",
            "approved"
            if decided and compatible is True
            else "incompatible"
            if decided and compatible is False
            else "unknown",
            ev.id,
            ev.time,
        )
        for key in (
            "commit",
            "package",
            "version",
            "license",
            "compatibility_license",
            "compatible",
            "result",
        ):
            value = state.values.get(f"{prefix}:resolved:{key}")
            if value is not None:
                state.set(f"{decision_prefix}:{key}", value, ev.id, ev.time)
        if selected_license is not None:
            state.set(f"{decision_prefix}:license", selected_license, ev.id, ev.time)
    elif kind == "release":
        release_prefix = f"real:release:{record_id}"
        ci_links = [
            link for link in links if state.values.get(f"repo:{link}:kind") == "ci_run"
        ]
        ci_results = [
            state.values.get(f"repo:{link}:resolved:result") for link in ci_links
        ]
        result = (
            "failed"
            if "failed" in ci_results
            else "passed"
            if any(item == "passed" for item in ci_results)
            else state.values.get(f"{prefix}:resolved:result")
        )
        license_results = [
            state.values.get(f"repo:{link}:resolved:compatible")
            for link in links
            if state.values.get(f"repo:{link}:kind") == "license"
        ]
        compatible = (
            False
            if False in license_results
            else True
            if license_results and all(item is True for item in license_results)
            else state.values.get(f"{prefix}:resolved:compatible")
        )
        validated: dict[str, Any] = {}
        selection_conflict = False
        for key in ("commit", "package", "version"):
            candidates = {
                state.values.get(f"repo:{link}:resolved:{key}") for link in ci_links
            }
            candidates.discard(None)
            if len(candidates) == 1:
                validated[key] = candidates.pop()
            elif len(candidates) > 1:
                selection_conflict = True
        status = (
            "published"
            if result == "passed" and compatible is not False and not selection_conflict
            else "blocked"
        )
        state.set(f"{release_prefix}:status", status, ev.id, ev.time)
        for key in (
            "tag",
            "package",
            "version",
            "commit",
            "license",
            "compatible",
        ):
            value = validated.get(key, state.values.get(f"{prefix}:resolved:{key}"))
            if value is not None:
                state.set(f"{release_prefix}:{key}", value, ev.id, ev.time)
                state.set(f"real:release:{key}", value, ev.id, ev.time)
        state.set("real:release:status", status, ev.id, ev.time)
        state.set(
            "real:release_cycle", int(p.get("release_cycle") or 1), ev.id, ev.time
        )
