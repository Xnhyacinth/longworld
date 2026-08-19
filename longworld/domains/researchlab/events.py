from __future__ import annotations

from typing import Any

from longworld.core.state import WorldState
from longworld.core.world import Event


def init_values(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "reported_score": None,
        "eval_stale": False,
        "testset_ok": True,
        "rerun_score": None,
        "table_score": None,
        "body_score": None,
        "authoritative_score": None,
        "tokenizer_commit": None,
        "cause_cache": None,
        "cause_split": None,
        "inflation_cause": None,
        "issue_filed": False,
        "fixed": False,
        "release_published": False,
        "pending_license": None,
        "ship_license": None,
        "paper": project["paper"],
        "model": project["model"],
        "benchmark": project["benchmark"],
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    t = ev.type
    if t in {"report_v1", "license_clause"}:
        return True, None
    if t in {"commit_tokenizer", "log_stale_cache", "issue_testset", "status_pulse"}:
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    if t == "fix_rerun":
        if not state.values.get("issue_filed"):
            return False, "issue_not_filed"
        return True, None
    if t in {"camera_ready", "release_note"}:
        if state.values.get("reported_score") is None:
            return False, "no_v1"
        return True, None
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    t = ev.type
    p = ev.params
    eid = ev.id
    day = ev.time
    if t == "license_clause":
        state.set("pending_license", p["spdx"], eid, day)
    elif t == "report_v1":
        state.set("reported_score", p["score"], eid, day)
        state.set("authoritative_score", p["score"], eid, day)
        state.set("body_score", p["score"], eid, day)
        state.set("eval_stale", True, eid, day)
        state.set("testset_ok", False, eid, day)
    elif t == "commit_tokenizer":
        state.set("tokenizer_commit", p["commit"], eid, day)
    elif t == "log_stale_cache":
        state.set("cause_cache", p["cause_cache"], eid, day)
    elif t == "issue_testset":
        if p.get("filed", True):
            state.set("issue_filed", True, eid, day)
            state.set("cause_split", p["cause_split"], eid, day)
    elif t == "fix_rerun":
        if p.get("aborted"):
            return
        state.set("eval_stale", False, eid, day)
        state.set("testset_ok", True, eid, day)
        state.set("fixed", True, eid, day)
        state.set("rerun_score", p["score"], eid, day)
        state.set("table_score", p["score"], eid, day)
    elif t == "camera_ready":
        # Table follows rerun if present; body may linger at v1.
        table = state.values.get("rerun_score") or state.values.get("reported_score")
        body = p.get("body_score", state.values.get("reported_score"))
        state.set("table_score", table, eid, day)
        state.set("body_score", body, eid, day)
    elif t == "release_note":
        state.set("release_published", True, eid, day)
        if p.get("adopt_rerun"):
            rs = state.values.get("rerun_score")
            if rs is not None:
                state.set("authoritative_score", rs, eid, day)
            else:
                state.set(
                    "authoritative_score", state.values.get("reported_score"), eid, day
                )
        else:
            state.set(
                "authoritative_score",
                p.get("score", state.values.get("reported_score")),
                eid,
                day,
            )
        pending = state.values.get("pending_license")
        if pending:
            state.set("ship_license", pending, eid, day)
    elif t == "status_pulse":
        return
