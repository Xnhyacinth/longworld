from __future__ import annotations

from typing import Any

from longworld.core.state import WorldState
from longworld.core.world import Event


def init_values(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": None,
        "legal_effective_version": None,
        "roadmap_version": None,
        "release_status": "planned",
        "release_version": None,
        "revenue_recognized": 0,
        "revenue_misrecorded": 0,
        "audit_adjustment": 0,
        "access_granted": False,
        "v2_deliverable": None,
        "v3_deliverable": None,
        "contract_id": None,
        "project": project["project"],
        "customer": project["customer"],
        "stale_client_cite": None,
        "signed": False,
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    t = ev.type
    if t == "sign_contract":
        return True, None
    if t in {"change_roadmap", "client_cite_old", "legal_supplement", "grant_access"}:
        if not state.values.get("signed"):
            return False, "contract_not_signed"
        return True, None
    if t == "release_beta":
        if not state.values.get("signed"):
            return False, "contract_not_signed"
        if state.values.get("legal_effective_version") is None:
            return False, "no_legal_version"
        return True, None
    if t in {"misrecord_revenue", "finance_preview"}:
        if state.values.get("release_status") not in {"beta", "released"}:
            return False, "not_released"
        return True, None
    if t == "audit_correction":
        if not state.values.get("revenue_misrecorded"):
            return False, "no_misrecord"
        return True, None
    if t in {"client_followup", "legal_reminder", "standup_notes", "status_pulse"}:
        if not state.values.get("signed"):
            return False, "contract_not_signed"
        return True, None
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    t = ev.type
    p = ev.params
    eid = ev.id
    day = ev.time
    if t == "sign_contract":
        state.set("signed", True, eid, day)
        state.set("contract_version", p["version"], eid, day)
        state.set("legal_effective_version", p["version"], eid, day)
        state.set("contract_id", p["contract_id"], eid, day)
        if "v2_deliverable" in p:
            state.set("v2_deliverable", p["v2_deliverable"], eid, day)
    elif t == "change_roadmap":
        state.set("roadmap_version", p["version"], eid, day)
        if "v3_deliverable" in p:
            state.set("v3_deliverable", p["v3_deliverable"], eid, day)
    elif t == "client_cite_old":
        state.set("stale_client_cite", p["cited_version"], eid, day)
    elif t == "legal_supplement":
        # Adopt the named effective version. If the event only points at the
        # roadmap, read it from current state so the meeting notes stay necessary.
        if p.get("keep_signed"):
            cv = state.values.get("contract_version")
            if cv is None:
                return
            state.set("legal_effective_version", cv, eid, day)
        elif p.get("adopt_roadmap"):
            rv = state.values.get("roadmap_version")
            if rv is None:
                return
            state.set("legal_effective_version", rv, eid, day)
        else:
            state.set("legal_effective_version", p["version"], eid, day)
    elif t == "grant_access":
        state.set("access_granted", True, eid, day)
    elif t == "release_beta":
        state.set("release_status", "beta", eid, day)
        state.set("release_version", p["version"], eid, day)
    elif t == "misrecord_revenue":
        amt = int(p["amount"])
        state.set("revenue_misrecorded", amt, eid, day)
        state.set("revenue_recognized", amt, eid, day)
    elif t == "audit_correction":
        amt = int(p["amount"])
        prev = int(state.values.get("revenue_misrecorded") or 0)
        state.set("audit_adjustment", amt - prev, eid, day)
        state.set("revenue_recognized", amt, eid, day)
    elif t in {
        "client_followup",
        "finance_preview",
        "legal_reminder",
        "standup_notes",
        "status_pulse",
    }:
        # Trajectory / distractor events: they lengthen unique history but
        # do not write the answer keys.
        return
