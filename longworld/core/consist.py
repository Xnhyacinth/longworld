from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from longworld.core.render import Artifact
from longworld.core.world import SimulatedWorld

FACT_RE = re.compile(r"^[\-\*\s]*([a-z][a-z0-9_]{2,})=(.+)$")
FACTS_HEADER_RE = re.compile(
    r"(recorded facts|line items \(authoritative\)|recorded decisions \(authoritative\))",
    re.IGNORECASE,
)


@dataclass
class ScanIssue:
    artifact_id: str
    kind: str
    detail: str


@dataclass
class ScanResult:
    ok: bool
    issues: list[ScanIssue]


def parse_facts(text: str) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("- ").strip()
        m = FACT_RE.match(line)
        if m:
            facts.append((m.group(1), m.group(2)))
    return facts


def consistency_scan(sim: SimulatedWorld, artifacts: list[Artifact]) -> ScanResult:
    """Narrative must be grounded in event params; key=value fact dumps are leaks."""
    issues: list[ScanIssue] = []
    events = {e.id: e for e in sim.events}

    for art in artifacts:
        if FACTS_HEADER_RE.search(art.text):
            issues.append(ScanIssue(art.artifact_id, "shortcut_header", "facts block"))
        leaked = parse_facts(art.text)
        if leaked:
            issues.append(
                ScanIssue(
                    art.artifact_id,
                    "shortcut_kv",
                    ",".join(k for k, _ in leaked[:6]),
                )
            )
        for val in art.slots.get("ground_values") or []:
            if str(val) and str(val) not in art.text:
                issues.append(
                    ScanIssue(art.artifact_id, "ungrounded", f"missing {val}")
                )
        for eid in art.reveals_events:
            ev = events.get(eid)
            if ev is None:
                issues.append(ScanIssue(art.artifact_id, "unknown_event", eid))
                continue
            if art.intentional_stale and ev.type in {
                "client_cite_old",
                "client_followup",
            }:
                cited = str(ev.params.get("cited_version", ""))
                if cited and cited not in art.text:
                    issues.append(ScanIssue(art.artifact_id, "stale_unbacked", cited))
            _check_state_alignment(sim, art, ev, issues)
    return ScanResult(ok=len(issues) == 0, issues=issues)


def _check_state_alignment(
    sim: SimulatedWorld,
    art: Artifact,
    ev: Any,
    issues: list[ScanIssue],
) -> None:
    t = ev.type
    p = ev.params
    when = art.time
    if t == "sign_contract":
        expected = _value_at(sim, "contract_version", when)
        if expected is not None and str(expected) != str(p["version"]):
            issues.append(
                ScanIssue(
                    art.artifact_id, "mismatch", f"signed {p['version']} vs {expected}"
                )
            )
    if t == "change_roadmap":
        expected = _value_at(sim, "roadmap_version", when)
        if expected is not None and str(expected) != str(p["version"]):
            issues.append(
                ScanIssue(
                    art.artifact_id, "mismatch", f"roadmap {p['version']} vs {expected}"
                )
            )
    if t == "misrecord_revenue":
        expected = _value_at(sim, "revenue_misrecorded", when)
        if expected is not None and int(expected) != int(p["amount"]):
            issues.append(
                ScanIssue(
                    art.artifact_id, "mismatch", f"june {p['amount']} vs {expected}"
                )
            )
    if t == "audit_correction":
        expected = _value_at(sim, "revenue_recognized", when)
        if expected is not None and int(expected) != int(p["amount"]):
            issues.append(
                ScanIssue(
                    art.artifact_id, "mismatch", f"july {p['amount']} vs {expected}"
                )
            )


def _value_at(sim: SimulatedWorld, key: str, when: date) -> Any:
    cur = sim.init_values.get(key)
    for d in sim.state.history:
        if d.key == key and d.time <= when:
            cur = d.new
    return cur
