from __future__ import annotations

from typing import Any

from longworld.core.cascade import apply_cascade, check_cascade
from longworld.core.grounded import apply_grounded, check_grounded
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
        "audit_cycle_1": None,
        "audit_cycle_2": None,
        "renewal_roadmap_version": None,
        "renewal_v3_deliverable": None,
        "renewal_legal_effective_version": None,
        "renewal_release_status": "planned",
        "renewal_release_version": None,
        "renewal_revenue_misrecorded": 0,
        "renewal_revenue_recognized": 0,
        "portfolio_control_trace": None,
        "access_granted": False,
        "v2_deliverable": None,
        "v3_deliverable": None,
        "contract_id": None,
        "project": project["project"],
        "customer": project["customer"],
        "stale_client_cite": None,
        "signed": False,
        "carveout_version": None,
        "carveout_jurisdiction": None,
        "rollback_applied": False,
        "release_blocker": None,
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
    }


def check_preconditions(state: WorldState, ev: Event) -> tuple[bool, str | None]:
    grounded = check_grounded(state, ev)
    if grounded is not None:
        return grounded
    casc = check_cascade(state, ev)
    if casc is not None:
        return casc
    if ev.type in {
        "cycle_plan",
        "cycle_failure",
        "cycle_recovery",
        "cycle_release",
        "cycle_audit",
    }:
        if not state.values.get("signed"):
            return False, "contract_not_signed"
        return True, None
    t = {
        "renewal_roadmap": "change_roadmap",
        "renewal_amendment": "legal_supplement",
        "renewal_release": "release_beta",
        "renewal_close": "misrecord_revenue",
        "renewal_audit": "audit_correction",
    }.get(ev.type, ev.type)
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
    if t == "carveout":
        if not state.values.get("signed"):
            return False, "contract_not_signed"
        return True, None
    if t == "rollback_amendment":
        if state.values.get("legal_effective_version") is None:
            return False, "no_legal_version"
        return True, None
    if t == "announce_hold":
        if not state.values.get("carveout_jurisdiction"):
            return False, "no_carveout"
        if state.values.get("release_status") not in {"beta", "released"}:
            return False, "not_released"
        return True, None
    return True, None


def apply_event(state: WorldState, ev: Event) -> None:
    if apply_grounded(state, ev):
        return
    if apply_cascade(state, ev):
        return
    if ev.type.startswith("cycle_"):
        p = ev.params
        eid = ev.id
        day = ev.time
        index = int(p["cycle_index"])
        key = f"cycle_{index:03d}"
        if ev.type == "cycle_plan":
            state.set(f"{key}_plan", p["plan_version"], eid, day)
        elif ev.type == "cycle_failure":
            state.set(f"{key}_incident", p["incident_token"], eid, day)
        elif ev.type == "cycle_recovery":
            state.set(f"{key}_resolution", p["resolution_token"], eid, day)
        elif ev.type == "cycle_release":
            state.set(f"{key}_release", p["release_token"], eid, day)
        elif ev.type == "cycle_audit":
            state.set(f"{key}_audit", int(p["amount"]), eid, day)
            if "trace_final_index" in p:
                final = int(p["trace_final_index"])
                cycle_records = []
                for cycle_index in range(final + 1):
                    prefix = f"cycle_{cycle_index:03d}"
                    values = (
                        state.values.get(f"{prefix}_incident"),
                        state.values.get(f"{prefix}_resolution"),
                        state.values.get(f"{prefix}_release"),
                        state.values.get(f"{prefix}_audit"),
                    )
                    if any(value in {None, 0, ""} for value in values):
                        return
                    incident, resolution, release, amount = values
                    cycle_records.append(f"{incident}=>{resolution}#{release}@{amount}")
                if cycle_records:
                    state.set(
                        "portfolio_control_trace",
                        " | ".join(cycle_records),
                        eid,
                        day,
                    )
        return
    t = {
        "renewal_roadmap": "change_roadmap",
        "renewal_amendment": "legal_supplement",
        "renewal_release": "release_beta",
        "renewal_close": "misrecord_revenue",
        "renewal_audit": "audit_correction",
    }.get(ev.type, ev.type)
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
        roadmap_key = (
            "renewal_roadmap_version"
            if p.get("workflow") == "renewal"
            else "roadmap_version"
        )
        state.set(roadmap_key, p["version"], eid, day)
        if "v3_deliverable" in p:
            state.set(
                "renewal_v3_deliverable"
                if p.get("workflow") == "renewal"
                else "v3_deliverable",
                p["v3_deliverable"],
                eid,
                day,
            )
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
            renewal = p.get("workflow") == "renewal"
            rv = state.values.get(
                "renewal_roadmap_version" if renewal else "roadmap_version"
            )
            if rv is None:
                return
            state.set(
                "renewal_legal_effective_version"
                if renewal
                else "legal_effective_version",
                rv,
                eid,
                day,
            )
        else:
            state.set("legal_effective_version", p["version"], eid, day)
    elif t == "grant_access":
        state.set("access_granted", True, eid, day)
    elif t == "release_beta":
        renewal = p.get("workflow") == "renewal"
        state.set(
            "renewal_release_status" if renewal else "release_status", "beta", eid, day
        )
        state.set(
            "renewal_release_version" if renewal else "release_version",
            p["version"],
            eid,
            day,
        )
    elif t == "misrecord_revenue":
        amt = int(p["amount"])
        renewal = p.get("workflow") == "renewal"
        state.set(
            "renewal_revenue_misrecorded" if renewal else "revenue_misrecorded",
            amt,
            eid,
            day,
        )
        state.set(
            "renewal_revenue_recognized" if renewal else "revenue_recognized",
            amt,
            eid,
            day,
        )
    elif t == "audit_correction":
        amt = int(p["amount"])
        renewal = p.get("workflow") == "renewal"
        misrecorded_key = (
            "renewal_revenue_misrecorded" if renewal else "revenue_misrecorded"
        )
        recognized_key = (
            "renewal_revenue_recognized" if renewal else "revenue_recognized"
        )
        prev = int(state.values.get(misrecorded_key) or 0)
        if not renewal:
            state.set("audit_adjustment", amt - prev, eid, day)
        state.set(recognized_key, amt, eid, day)
        cycle = int(p.get("cycle", 1))
        state.set(f"audit_cycle_{cycle}", amt, eid, day)
    elif t == "carveout":
        # Version is copied from signed state, never from params.
        state.set("carveout_jurisdiction", p["jurisdiction"], eid, day)
        cv = state.values.get("contract_version")
        if cv is None:
            return
        state.set("carveout_version", cv, eid, day)
    elif t == "rollback_amendment":
        cv = state.values.get("contract_version")
        if cv is None:
            return
        state.set("legal_effective_version", cv, eid, day)
        state.set("rollback_applied", True, eid, day)
    elif t == "announce_hold":
        # Delayed cause: early carve-out becomes decision-relevant at announce.
        j = state.values.get("carveout_jurisdiction")
        if not j:
            return
        state.set("release_blocker", j, eid, day)
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
