from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from longworld.core.cascade import cascade_events
from longworld.core.grounded import grounded_events
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.events import (
    apply_event,
    check_preconditions,
    init_values,
)


def _date(start: date, offset: int) -> date:
    return start + timedelta(days=offset)


def events_for_project(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    off = project["event_offsets"]
    pid = project["project"].lower()
    evs: list[Event] = []

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    evs.append(
        Event(
            id=eid("sign_contract"),
            type="sign_contract",
            time=_date(start, off["sign_contract"]),
            params={
                "version": project["signed_version"],
                "contract_id": project["contract_id"],
                "v2_deliverable": project["v2_deliverable"],
            },
            visibility=[f"{prefix}.contract_email"],
            causal_inputs=[],
        )
    )
    evs.append(
        Event(
            id=eid("change_roadmap"),
            type="change_roadmap",
            time=_date(start, off["change_roadmap"]),
            params={
                "version": project["roadmap_version"],
                "v3_deliverable": project["v3_deliverable"],
            },
            visibility=[f"{prefix}.roadmap_notes"],
            causal_inputs=[eid("sign_contract")],
        )
    )
    evs.append(
        Event(
            id=eid("client_cite_old"),
            type="client_cite_old",
            time=_date(start, off["client_cite_old"]),
            params={"cited_version": project["signed_version"]},
            visibility=[f"{prefix}.client_email"],
            causal_inputs=[eid("sign_contract")],
        )
    )
    evs.append(
        Event(
            id=eid("client_followup"),
            type="client_followup",
            time=_date(start, off["client_followup"]),
            params={"cited_version": project["signed_version"]},
            visibility=[f"{prefix}.client_followup"],
            causal_inputs=[eid("client_cite_old")],
        )
    )
    # Focal worlds bind legal version to the roadmap so meeting notes are
    # necessary. Parallel worlds may name the version in the supplement.
    adopt = bool(project.get("is_focal", False))
    evs.append(
        Event(
            id=eid("legal_supplement"),
            type="legal_supplement",
            time=_date(start, off["legal_supplement"]),
            params={
                "version": project["supplement_version"],
                "adopt_roadmap": adopt,
            },
            visibility=[f"{prefix}.legal_email"],
            preconditions=["signed"],
            causal_inputs=[eid("sign_contract"), eid("change_roadmap")],
        )
    )
    evs.append(
        Event(
            id=eid("grant_access"),
            type="grant_access",
            time=_date(start, off["grant_access"]),
            params={},
            visibility=[f"{prefix}.access_email"],
            causal_inputs=[eid("legal_supplement")],
        )
    )
    evs.append(
        Event(
            id=eid("release_beta"),
            type="release_beta",
            time=_date(start, off["release_beta"]),
            params={
                "version": project.get(
                    "beta_tag", f"{project['supplement_version']}-beta"
                )
            },
            visibility=[f"{prefix}.beta_notes"],
            causal_inputs=[eid("legal_supplement")],
        )
    )
    evs.append(
        Event(
            id=eid("standup_notes"),
            type="standup_notes",
            time=_date(start, off["standup_notes"]),
            params={
                "version": project.get(
                    "beta_tag", f"{project['supplement_version']}-beta"
                )
            },
            visibility=[f"{prefix}.standup_notes"],
            causal_inputs=[eid("release_beta")],
        )
    )
    evs.append(
        Event(
            id=eid("finance_preview"),
            type="finance_preview",
            time=_date(start, off["finance_preview"]),
            params={"amount": project["misrecorded_revenue"]},
            visibility=[f"{prefix}.finance_preview"],
            causal_inputs=[eid("release_beta")],
        )
    )
    evs.append(
        Event(
            id=eid("misrecord_revenue"),
            type="misrecord_revenue",
            time=_date(start, off["misrecord_revenue"]),
            params={"amount": project["misrecorded_revenue"]},
            visibility=[f"{prefix}.finance_june"],
            causal_inputs=[eid("release_beta")],
        )
    )
    evs.append(
        Event(
            id=eid("audit_correction"),
            type="audit_correction",
            time=_date(start, off["audit_correction"]),
            params={"amount": project["audited_revenue"]},
            visibility=[f"{prefix}.finance_july"],
            causal_inputs=[eid("misrecord_revenue")],
        )
    )
    evs.append(
        Event(
            id=eid("legal_reminder"),
            type="legal_reminder",
            time=_date(start, off["legal_reminder"]),
            params={"nudge": "file-hygiene"},
            visibility=[f"{prefix}.legal_reminder"],
            causal_inputs=[eid("legal_supplement")],
        )
    )
    evs.append(
        Event(
            id=eid("renewal_roadmap"),
            type="renewal_roadmap",
            time=_date(start, off["renewal_roadmap"]),
            params={
                "version": project["renewal_roadmap_version"],
                "v3_deliverable": (
                    f"{project['project']}-renewal-stream-"
                    f"{project['renewal_roadmap_version'][-5:-2]}"
                ),
                "cycle": 2,
                "workflow": "renewal",
            },
            visibility=[f"{prefix}.renewal_roadmap"],
            causal_inputs=[eid("audit_correction"), eid("legal_supplement")],
            relation_kinds={
                eid("audit_correction"): "delayed_cause",
                eid("legal_supplement"): "supersedes",
            },
        )
    )
    evs.append(
        Event(
            id=eid("renewal_amendment"),
            type="renewal_amendment",
            time=_date(start, off["renewal_amendment"]),
            params={
                "version": project["renewal_roadmap_version"],
                "adopt_roadmap": True,
                "cycle": 2,
                "workflow": "renewal",
            },
            visibility=[f"{prefix}.renewal_amendment"],
            preconditions=["signed"],
            causal_inputs=[eid("renewal_roadmap"), eid("audit_correction")],
            relation_kinds={
                eid("renewal_roadmap"): "derived_from",
                eid("audit_correction"): "cross_stream_trigger",
            },
        )
    )
    evs.append(
        Event(
            id=eid("renewal_release"),
            type="renewal_release",
            time=_date(start, off["renewal_release"]),
            params={
                "version": project["renewal_beta_tag"],
                "cycle": 2,
                "workflow": "renewal",
            },
            visibility=[f"{prefix}.renewal_release"],
            causal_inputs=[eid("renewal_amendment")],
        )
    )
    evs.append(
        Event(
            id=eid("renewal_close"),
            type="renewal_close",
            time=_date(start, off["renewal_close"]),
            params={
                "amount": project["renewal_misrecorded_revenue"],
                "cycle": 2,
                "workflow": "renewal",
            },
            visibility=[f"{prefix}.renewal_close"],
            causal_inputs=[eid("renewal_release")],
        )
    )
    evs.append(
        Event(
            id=eid("renewal_audit"),
            type="renewal_audit",
            time=_date(start, off["renewal_audit"]),
            params={
                "amount": project["renewal_audited_revenue"],
                "cycle": 2,
                "workflow": "renewal",
            },
            visibility=[f"{prefix}.renewal_audit"],
            causal_inputs=[eid("renewal_close"), eid("renewal_release")],
            relation_kinds={
                eid("renewal_close"): "corrects",
                eid("renewal_release"): "cross_stream_trigger",
            },
        )
    )
    previous_audit_id = eid("renewal_audit")
    workstreams = list(project.get("workstreams") or [])
    trace_pivot = len(workstreams) // 2
    for index, workstream in enumerate(workstreams):
        stem = f"cycle_{index:03d}"
        plan_id = eid(f"{stem}_plan")
        failure_id = eid(f"{stem}_failure")
        recovery_id = eid(f"{stem}_recovery")
        release_id = eid(f"{stem}_release")
        audit_id = eid(f"{stem}_audit")
        base_day = 340 + index * 28
        common = {
            "cycle_index": index,
            "cycle_id": workstream["id"],
            "kind": workstream["kind"],
            "agreement_id": workstream["agreement_id"],
        }
        evs.extend(
            [
                Event(
                    id=plan_id,
                    type="cycle_plan",
                    time=_date(start, base_day),
                    params={
                        **common,
                        "plan_version": workstream["plan_version"],
                        "candidate_token": workstream["candidate_token"],
                    },
                    visibility=[f"{prefix}.{stem}_plan"],
                    causal_inputs=[previous_audit_id],
                    required_inputs=[previous_audit_id],
                    relation_kinds={previous_audit_id: "delayed_cause"},
                ),
                Event(
                    id=failure_id,
                    type="cycle_failure",
                    time=_date(start, base_day + 5),
                    params={
                        **common,
                        "candidate_token": workstream["candidate_token"],
                        "incident_token": workstream["incident_token"],
                        "failure_mode": workstream["failure_mode"],
                    },
                    visibility=[f"{prefix}.{stem}_failure"],
                    causal_inputs=[plan_id],
                    required_inputs=[plan_id],
                    relation_kinds={plan_id: "observed_in"},
                ),
                Event(
                    id=recovery_id,
                    type="cycle_recovery",
                    time=_date(start, base_day + 11),
                    params={
                        **common,
                        "resolution_token": workstream["resolution_token"],
                    },
                    visibility=[f"{prefix}.{stem}_recovery"],
                    causal_inputs=[failure_id],
                    required_inputs=[failure_id],
                    relation_kinds={failure_id: "corrects"},
                ),
                Event(
                    id=release_id,
                    type="cycle_release",
                    time=_date(start, base_day + 17),
                    params={
                        **common,
                        "release_token": workstream["release_token"],
                    },
                    visibility=[f"{prefix}.{stem}_release"],
                    causal_inputs=[recovery_id],
                    required_inputs=[recovery_id],
                    relation_kinds={recovery_id: "enables"},
                ),
                Event(
                    id=audit_id,
                    type="cycle_audit",
                    time=_date(start, base_day + 23),
                    params={
                        **common,
                        "amount": workstream["audit_amount"],
                        "close_amount": workstream["close_amount"],
                        **(
                            {
                                "trace_first_index": 0,
                                "trace_pivot_index": trace_pivot,
                                "trace_final_index": len(workstreams) - 1,
                            }
                            if index == len(workstreams) - 1
                            else {}
                        ),
                    },
                    visibility=[f"{prefix}.{stem}_audit"],
                    causal_inputs=[release_id],
                    required_inputs=[release_id],
                    relation_kinds={release_id: "audits"},
                ),
            ]
        )
        previous_audit_id = audit_id
    if project.get("process", {}).get("carveout"):
        evs.append(
            Event(
                id=eid("carveout"),
                type="carveout",
                time=_date(start, off["carveout"]),
                params={"jurisdiction": project["carveout_jurisdiction"]},
                visibility=[f"{prefix}.carveout_memo"],
                causal_inputs=[eid("sign_contract"), eid("legal_supplement")],
                relation_kinds={
                    eid("sign_contract"): "enables",
                    eid("legal_supplement"): "exception",
                },
            )
        )
        evs.append(
            Event(
                id=eid("announce_hold"),
                type="announce_hold",
                time=_date(start, off.get("announce_hold", 82)),
                params={},
                visibility=[f"{prefix}.announce_hold"],
                causal_inputs=[eid("carveout"), eid("release_beta")],
                relation_kinds={
                    eid("carveout"): "delayed_cause",
                    eid("release_beta"): "enables",
                },
            )
        )
    if project.get("process", {}).get("rollback"):
        evs.append(
            Event(
                id=eid("rollback_amendment"),
                type="rollback_amendment",
                time=_date(start, off["rollback_amendment"]),
                params={},
                visibility=[f"{prefix}.rollback_memo"],
                preconditions=["signed"],
                causal_inputs=[eid("legal_supplement"), eid("sign_contract")],
                relation_kinds={
                    eid("legal_supplement"): "supersedes",
                    eid("sign_contract"): "derived_from",
                },
            )
        )
    evs.extend(
        grounded_events(
            project,
            prefix,
            start,
            ingest_off=4,
            alt_off=5,
            adopt_off=62,
        )
    )
    evs.extend(
        cascade_events(
            project,
            prefix,
            start,
            seed_off=int(off.get("latent_seed", 8)),
            ack_off=int(off.get("latent_ack", 99)),
            reopen_off=int(off.get("latent_reopen", 136)),
            ratify_off=int(off.get("latent_ratify", 150)),
        )
    )
    blockers = (
        "vendor lead time",
        "staging quota",
        "customer calendar",
        "license queue",
        "design review",
        "security questionnaire",
    )
    for i, pulse_off in enumerate(project.get("pulse_offsets") or []):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, int(pulse_off)),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"T-{pid[:4].upper()}-{1000 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("sign_contract")],
            )
        )
    evs.sort(key=lambda e: (e.time, e.id))
    _ = pid
    return evs


def simulate_company(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
    """Simulate focal + parallel projects independently (visibility isolation)."""
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
            },
            init_values=init_values(project),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        evs = events_for_project(project, prefix)
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
    return worlds
