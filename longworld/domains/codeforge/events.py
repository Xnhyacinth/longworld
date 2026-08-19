from __future__ import annotations

from typing import Any

from longworld.core.state import WorldState
from longworld.core.world import Event


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
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    t = ev.type
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
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    t = ev.type
    p = ev.params
    eid = ev.id
    day = ev.time
    if t == "license_clause":
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
    elif t == "status_pulse":
        return
