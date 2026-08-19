from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.researchlab.events import (
    apply_event,
    check_preconditions,
    init_values,
)


def _date(start: date, months: int, extra_days: int = 0) -> date:
    # Approximate month steps without dateutil.
    return start + timedelta(days=30 * months + extra_days)


def events_for_lab(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    pid = project["paper"].lower()
    v1 = project["v1_score"]
    final = project["final_score"]
    cause = project["cause_token"]
    cache_tok, split_tok = cause.split("+", 1)
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
            id=eid("report_v1"),
            type="report_v1",
            time=_date(start, 0, 2),
            params={"score": v1},
            visibility=[f"{prefix}.arxiv_v1"],
            causal_inputs=[],
        ),
        Event(
            id=eid("commit_tokenizer"),
            type="commit_tokenizer",
            time=_date(start, 1, 4),
            params={"commit": project["commit_hash"]},
            visibility=[f"{prefix}.git_commit"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "enables"},
        ),
        Event(
            id=eid("log_stale_cache"),
            type="log_stale_cache",
            time=_date(start, 2, 6),
            params={"cause_cache": cache_tok},
            visibility=[f"{prefix}.eval_log"],
            causal_inputs=[eid("commit_tokenizer")],
            relation_kinds={eid("commit_tokenizer"): "derived_from"},
        ),
        Event(
            id=eid("issue_testset"),
            type="issue_testset",
            time=_date(start, 3, 3),
            params={
                "filed": True,
                "cause_cache": cache_tok,
                "cause_split": split_tok,
            },
            visibility=[f"{prefix}.github_issue"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "contradicts"},
        ),
        Event(
            id=eid("fix_rerun"),
            type="fix_rerun",
            time=_date(start, 4, 8),
            params={"score": final},
            visibility=[f"{prefix}.rerun_json"],
            preconditions=["issue_filed"],
            causal_inputs=[eid("issue_testset"), eid("log_stale_cache")],
            relation_kinds={
                eid("issue_testset"): "enables",
                eid("log_stale_cache"): "derived_from",
            },
        ),
        Event(
            id=eid("camera_ready"),
            type="camera_ready",
            time=_date(start, 5, 5),
            params={"body_score": round(float(v1) + 0.01, 2)},
            visibility=[f"{prefix}.camera_ready"],
            causal_inputs=[eid("fix_rerun"), eid("report_v1")],
            relation_kinds={
                eid("fix_rerun"): "supersedes",
                eid("report_v1"): "contradicts",
            },
        ),
        Event(
            id=eid("release_note"),
            type="release_note",
            time=_date(start, 6, 2),
            params={"adopt_rerun": adopt},
            visibility=[f"{prefix}.release_note"],
            causal_inputs=[
                eid("fix_rerun"),
                eid("camera_ready"),
                eid("license_clause"),
            ],
            relation_kinds={
                eid("fix_rerun"): "derived_from",
                eid("camera_ready"): "supersedes",
                eid("license_clause"): "enables",
            },
        ),
    ]
    blockers = (
        "GPU quota",
        "reviewer latency",
        "dataset mirror",
        "license check",
        "plot regeneration",
    )
    n_pulses = int(project.get("n_pulses") or 10)
    for i in range(n_pulses):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, 0, 9 + i * 11),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"RL-{pid[:4].upper()}-{200 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "observed_in"},
            )
        )
    _ = pid
    return evs


def simulate_lab(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
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
                "domain": "researchlab",
            },
            init_values=init_values(project),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        evs = events_for_lab(project, prefix)
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
        worlds[prefix].spec["domain"] = "researchlab"
    return worlds
