from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from longworld.core.render import Artifact, semantic_attestation_valid
from longworld.core.world import SimulatedWorld

FACT_RE = re.compile(r"^[\-\*\s]*([a-z][a-z0-9_]{2,})=(.+)$")
FACTS_HEADER_RE = re.compile(
    r"(recorded facts|line items \(authoritative\)|recorded decisions \(authoritative\))",
    re.IGNORECASE,
)
TEXT_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
CLAUSE_RE = re.compile(r"[\n\r.;!?]+")
NEGATED_ASSERTION_RE = re.compile(
    r"\b(?:is|are|was|were|remains?|became|be)\s+not\s+"
    r"(?:effective|authoritative|adopted|approved|executed|valid|correct)\b|"
    r"\b(?:never\s+(?:became|was)|invalid|incorrect|rejected)\b",
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


def is_real_workflow_body(artifact: Artifact) -> bool:
    classification = dict((artifact.slots or {}).get("classification") or {})
    return bool(
        classification.get("source_origin")
        in {"real_public", "real_private_export", "real_derived"}
        and classification.get("workflow_kind")
        in {"real_source_derived", "hybrid_causal"}
        and classification.get("provenance_id")
    )


def artifact_text_issues(
    sim: SimulatedWorld,
    artifacts: list[Artifact],
    *,
    require_attestation: bool = False,
) -> list[ScanIssue]:
    """Find revealed events whose asserted evidence is absent from document text.

    This is intentionally a conservative corruption detector, not a general natural
    language parser. Renderers already record concrete ``ground_values`` in slots;
    relationship-only artifacts additionally need a lexical anchor for their event
    type. Keeping this check separate from event replay prevents intact hidden event
    ids from making corrupted text look sufficient.
    """
    events = {event.id: event for event in sim.events}
    issues: list[ScanIssue] = []
    for art in artifacts:
        if not art.reveals_events:
            continue
        text = art.text or ""
        if require_attestation and not semantic_attestation_valid(art):
            issues.append(
                ScanIssue(
                    art.artifact_id,
                    "semantic_attestation",
                    "evidence text is not bound to a trusted renderer output",
                )
            )
        expected_digest = str((art.slots or {}).get("semantic_text_sha256") or "")
        if (
            expected_digest
            and hashlib.sha256(text.encode()).hexdigest() != expected_digest
        ):
            issues.append(
                ScanIssue(
                    art.artifact_id,
                    "text_integrity",
                    "rendered evidence text changed after semantic binding",
                )
            )
        lowered_tokens = TEXT_TOKEN_RE.findall(text.lower())
        if len(set(lowered_tokens)) < 6:
            issues.append(
                ScanIssue(art.artifact_id, "corrupt_text", "too little lexical content")
            )
        ground_values = [
            str(value) for value in art.slots.get("ground_values") or [] if str(value)
        ]
        folded_text = text.casefold()
        for value in ground_values:
            if value.casefold() not in folded_text:
                issues.append(
                    ScanIssue(art.artifact_id, "ungrounded", f"missing {value}")
                )
                continue
            value_clauses = [
                clause
                for clause in CLAUSE_RE.split(text)
                if value.lower() in clause.lower()
            ]
            if value_clauses and all(
                NEGATED_ASSERTION_RE.search(clause) for clause in value_clauses
            ):
                issues.append(
                    ScanIssue(
                        art.artifact_id,
                        "negated_ground_value",
                        f"only negated assertions for {value}",
                    )
                )
        for event_id in art.reveals_events:
            event = events.get(event_id)
            if event is None:
                issues.append(ScanIssue(art.artifact_id, "unknown_event", event_id))
                continue
            if ground_values:
                continue
            anchors = [
                token
                for token in TEXT_TOKEN_RE.findall(event.type.lower())
                if len(token) >= 4
            ]
            anchored = any(
                word.startswith(anchor[:5])
                for anchor in anchors
                for word in lowered_tokens
            )
            if not anchored:
                issues.append(
                    ScanIssue(
                        art.artifact_id,
                        "event_unanchored",
                        f"no lexical anchor for {event.type}",
                    )
                )
    return issues


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
        if art.doc_type != "source_pack" and not is_real_workflow_body(art):
            if FACTS_HEADER_RE.search(art.text):
                issues.append(
                    ScanIssue(art.artifact_id, "shortcut_header", "facts block")
                )
            leaked = parse_facts(art.text)
            if leaked:
                issues.append(
                    ScanIssue(
                        art.artifact_id,
                        "shortcut_kv",
                        ",".join(k for k, _ in leaked[:6]),
                    )
                )
        folded_text = art.text.casefold()
        for val in art.slots.get("ground_values") or []:
            if str(val) and str(val).casefold() not in folded_text:
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
