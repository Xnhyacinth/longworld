from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.codeforge.events import (
    apply_event,
    check_preconditions,
    init_values,
)


def _date(start: date, months: int, extra_days: int = 0) -> date:
    return start + timedelta(days=30 * months + extra_days)


def events_for_repo(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    repo = project["repo"].lower()
    adopt = bool(project.get("is_focal", False))

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    evs: list[Event] = [
        Event(
            id=eid("license_clause"),
            type="license_clause",
            time=_date(start, 0, 1),
            params={"spdx": project["spdx"]},
            visibility=[f"{prefix}.license_note"],
            causal_inputs=[],
        ),
        Event(
            id=eid("broken_commit"),
            type="broken_commit",
            time=_date(start, 0, 8),
            params={"commit": project["broken_hash"]},
            visibility=[f"{prefix}.broken_commit"],
            causal_inputs=[eid("license_clause")],
            relation_kinds={eid("license_clause"): "enables"},
        ),
        Event(
            id=eid("ci_fail"),
            type="ci_fail",
            time=_date(start, 1, 3),
            params={"flake_token": project["flake_token"]},
            visibility=[f"{prefix}.ci_log"],
            causal_inputs=[eid("broken_commit")],
            relation_kinds={eid("broken_commit"): "derived_from"},
        ),
        Event(
            id=eid("issue_bug"),
            type="issue_bug",
            time=_date(start, 2, 2),
            params={"filed": True, "fail_token": project["fail_token"]},
            visibility=[f"{prefix}.github_issue"],
            causal_inputs=[eid("broken_commit")],
            relation_kinds={eid("broken_commit"): "contradicts"},
        ),
        Event(
            id=eid("hotfix"),
            type="hotfix",
            time=_date(start, 3, 5),
            params={"commit": project["hotfix_hash"]},
            visibility=[f"{prefix}.hotfix_commit"],
            preconditions=["issue_filed"],
            causal_inputs=[eid("issue_bug"), eid("ci_fail")],
            relation_kinds={
                eid("issue_bug"): "enables",
                eid("ci_fail"): "derived_from",
            },
        ),
        Event(
            id=eid("changelog_stale"),
            type="changelog_stale",
            time=_date(start, 4, 2),
            params={"quoted_hash": project["broken_hash"]},
            visibility=[f"{prefix}.changelog"],
            causal_inputs=[eid("broken_commit"), eid("hotfix")],
            relation_kinds={
                eid("broken_commit"): "contradicts",
                eid("hotfix"): "supersedes",
            },
        ),
        Event(
            id=eid("tag_release"),
            type="tag_release",
            time=_date(start, 5, 4),
            params={"adopt_head": adopt},
            visibility=[f"{prefix}.tag_release"],
            causal_inputs=[eid("hotfix"), eid("license_clause")],
            relation_kinds={
                eid("hotfix"): "derived_from",
                eid("license_clause"): "enables",
            },
        ),
    ]
    blockers = (
        "runner quota",
        "reviewer latency",
        "wheel mirror",
        "license check",
        "docs rebuild",
    )
    n_pulses = int(project.get("n_pulses") or 10)
    for i in range(n_pulses):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, 0, 11 + i * 11),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"CF-{repo[:4].upper()}-{300 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("broken_commit")],
                relation_kinds={eid("broken_commit"): "observed_in"},
            )
        )
    return evs


def simulate_code(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
    worlds: dict[str, SimulatedWorld] = {}
    projects = [("focal", spec["focal"])] + [
        (f"par{i}", p) for i, p in enumerate(spec["parallels"])
    ]
    for prefix, project in projects:
        sim = WorldSimulator(
            spec={
                **spec,
                "world_id": f"{spec['world_id']}:{prefix}",
                "project": project,
                "domain": "codeforge",
            },
            init_values=init_values(project),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        evs = events_for_repo(project, prefix)
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
        worlds[prefix].spec["domain"] = "codeforge"
    return worlds
