"""Role × document-purpose discourse. No random paraphrase bank.

Expands a few high-value artifacts using only already-grounded slots and
org roles. Must not introduce new numerals/hashes/SPDX tokens.
Register is sampled per world; expansions are short and typed.
"""

from __future__ import annotations

from longworld.core.process import REGISTER_MARKERS, REGISTERS
from longworld.core.render import Artifact

PROCESS_INSTRUMENTS = frozenset(
    {
        "carveout",
        "rollback_amendment",
        "rollback_hotfix",
        "invalidate_run",
        "announce_hold",
        "ack_latent",
        "reopen_latent",
        "ratify_latent",
    }
)

_STRUCTURE = {
    "instrument": ["docket", "constraint", "next"],
    "ops_log": ["log", "constraint"],
    "standup": ["brief", "parking"],
    "audit_trail": ["trail", "review"],
}


def stamp_register(artifacts: list[Artifact], register: str) -> list[Artifact]:
    reg = register if register in REGISTERS else "instrument"
    for a in artifacts:
        a.slots = {**(a.slots or {}), "register": reg}
    return artifacts


def _role_line(artifact: Artifact) -> str:
    slots = artifact.slots or {}
    role = str(slots.get("author_role") or artifact.role or "").strip()
    audience = str(slots.get("audience_role") or "").strip()
    if not role:
        return ""
    bits = [f"Author-role: {role}"]
    if audience:
        bits.append(f"Audience: {audience}")
    return "\n".join(bits) + "\n"


def expand_discourse(artifacts: list[Artifact]) -> list[Artifact]:
    """Deterministic extra sections for reports / memos / issues."""
    for a in artifacts:
        extra = _section_for(a)
        role = _role_line(a)
        prefix = role if role and role.strip() not in a.text else ""
        if extra and extra not in a.text:
            a.text = a.text.rstrip() + "\n\n" + extra
        if prefix:
            a.text = prefix + a.text
        plan = dict((a.slots or {}).get("content_plan") or {})
        if extra:
            plan.setdefault(
                "discourse_structure",
                list(_STRUCTURE.get(_register(a), _STRUCTURE["instrument"])),
            )
            a.slots = {**(a.slots or {}), "content_plan": plan}
    return artifacts


def _register(a: Artifact) -> str:
    r = str((a.slots or {}).get("register") or "instrument")
    return r if r in REGISTERS else "instrument"


def _section_for(a: Artifact) -> str:
    et = str((a.slots or {}).get("event_type") or "")
    dt = a.doc_type
    if dt in {"source_pack", "json"}:
        return ""
    body = _register_body(a)
    if et in PROCESS_INSTRUMENTS:
        return (
            "## Filing record\n"
            "Filed with the project record after the associated state change. "
            "Distribution is limited to the teams listed in the routing header.\n\n"
            f"{body}"
        )
    if dt in {"report"} or et in {"report_v1", "camera_ready", "release_note"}:
        return (
            "## Notes for readers\n"
            "Section order is summary, evidence, and deferred items. "
            f"Workspace: {a.project}.\n\n"
            f"{body}"
        )
    if dt in {"issue", "log"}:
        return (
            "## Thread context\n"
            "This ticket or log records the workstream status at the time of "
            "filing. Follow-up ownership is recorded in the routing header.\n\n"
            f"{body}"
        )
    if dt == "email" and et in {
        "sign_contract",
        "legal_supplement",
        "announce_hold",
    }:
        return (
            "## Filing purpose\n"
            "Counsel to program-manager, or program-manager to customer-success. "
            "This email records a legal instrument, its cover, or a related hold.\n\n"
            f"{body}"
        )
    if dt in {"email", "meeting_notes", "finance_report"}:
        return body
    return ""


def _register_body(a: Artifact) -> str:
    """Short workplace prose. No gold keys. No shared procedure manifesto."""
    day = a.time.isoformat() if a.time else "undated"
    proj = a.project or "workspace"
    role = str((a.slots or {}).get("author_role") or a.role or "staff")
    et = str((a.slots or {}).get("event_type") or a.doc_type)
    r = _register(a)
    marker = REGISTER_MARKERS[r]
    if r == "audit_trail":
        marker = "Audit trail entry retained for review"
    kind = a.doc_type.replace("_", " ")
    if r == "instrument":
        return (
            f"## Instrument docket ({day})\n"
            f"{marker} in the {proj} file. Channel: {role}. Event class: {et}. "
            f"This {kind} was routed for review and retained with the project file."
        )
    if r == "ops_log":
        return (
            f"## Ops timestamp ({day})\n"
            f"{marker}, workspace {proj}. Operator channel: {role}. "
            f"Class: {et}. This {kind} records the observed operational change "
            f"and the responsible channel."
        )
    if r == "standup":
        return (
            f"## Spoken notes ({day})\n"
            f"{marker} on {proj}. Spoken by {role}. Topic class: {et}. "
            f"This {kind} captures the discussion, open questions, and owners "
            f"recorded during the meeting."
        )
    return (
        f"## Audit note ({day})\n"
        f"{marker} of {proj}. Preparer channel: {role}. Event class: {et}. "
        f"This {kind} is a dated entry in the project audit trail."
    )
