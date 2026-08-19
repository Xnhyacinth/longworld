from __future__ import annotations

from datetime import date, timedelta
from typing import Any

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
    for ev in evs:
        ev.id = ev.id  # prefix already baked in
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
