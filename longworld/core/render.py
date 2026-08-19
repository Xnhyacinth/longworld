from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from longworld.core.world import Event, SimulatedWorld
from longworld.domains.company.templates_env import jinja_env


@dataclass
class Artifact:
    artifact_id: str
    doc_type: str  # email | meeting_notes | finance_report
    time: date
    project: str
    prefix: str
    reveals_events: list[str]
    text: str
    facts: list[str]
    slots: dict[str, Any] = field(default_factory=dict)
    is_focal: bool = True
    intentional_stale: bool = False
    stale_event_id: str | None = None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-")


def _person_email_local(name: str) -> str:
    return slug(name)


def _ground_values(ev: Event) -> list[str]:
    """Values that must appear in the rendered document (except adopt-only legal)."""
    p = ev.params
    t = ev.type
    if t == "sign_contract":
        return [str(p["contract_id"]), str(p["version"]), str(p["v2_deliverable"])]
    if t == "change_roadmap":
        return [str(p["version"]), str(p["v3_deliverable"])]
    if t in {"client_cite_old", "client_followup"}:
        return [str(p["cited_version"])]
    if t == "legal_supplement":
        if p.get("adopt_roadmap"):
            return []
        return [str(p["version"])]
    if t == "release_beta":
        return [str(p["version"])]
    if t in {"misrecord_revenue", "audit_correction", "finance_preview"}:
        return [str(p["amount"])]
    if t == "status_pulse":
        return [str(p["ticket"]), str(p["week_index"])]
    return []


def _body_for(kind: str, project: dict[str, Any], ev: Event) -> dict[str, Any]:
    people = project["people"]
    company = project["company"]
    t = ev.type
    date_s = ev.time.isoformat()
    if t == "sign_contract":
        return {
            "doc_type": "email",
            "subject": f"{project['contract_id']} executed for {project['project']}",
            "sender": people["counsel"],
            "recipient": people["pm"],
            "dest_domain": "internal.example",
            "greeting": f"Counsel to {people['pm']}:",
            "body": (
                f"The master services agreement with {project['customer']} is executed. "
                f"Delivery is locked to {ev.params['version']} under "
                f"{ev.params['contract_id']}. Product may still discuss later roadmaps; "
                f"those discussions do not change the signed file until a numbered "
                f"amendment exists. Please archive {ev.params['contract_id']} under "
                f"Legal Counsel. The signed batch surface is named "
                f"{ev.params['v2_deliverable']}."
            ),
            "closing": "Regards,",
            "department": project["departments"]["legal"],
            "thread_id": f"thr-{project['contract_id']}-sign",
        }
    if t == "change_roadmap":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} Q-cycle roadmap",
            "owner": people["pm"],
            "attendees": [people["pm"], people["eng"], people["csm"]],
            "agenda": [
                "Status of signed delivery",
                "Whether to retarget the next numbered version",
                "Customer communication risks",
            ],
            "discussion": (
                f"{people['pm']} noted that engineering wants the stream surface. "
                f"This meeting records a product decision only. Legal Counsel was not "
                f"present and did not execute an amendment in this room. {people['csm']} "
                f"warned that {project['customer']} still operates from the signed packet. "
                f"The product target recorded here is {ev.params['version']}, with "
                f"deliverable name {ev.params['v3_deliverable']}."
            ),
            "actions": [
                f"{people['eng']}: spike the stream surface",
                f"{people['csm']}: do not promise dates to {project['customer']}",
            ],
            "parking": "Pricing for the stream surface is out of scope for this notes file.",
            "department": project["departments"]["product"],
        }
    if t == "client_cite_old":
        return {
            "doc_type": "email",
            "subject": f"{project['project']} delivery version confirmation",
            "sender": people["customer_contact"],
            "recipient": people["csm"],
            "dest_domain": slug(company) + ".example",
            "greeting": f"{people['csm']},",
            "body": (
                f"From {project['customer']}: we are scheduling acceptance against "
                f"{ev.params['cited_version']} from the original signed packet. Please "
                f"do not silently move us to a later numbered release. Our integration "
                f"tests still assume the batch surface. If an amendment exists we have "
                f"not received it. This citation is our PMO printout, not a legal file."
            ),
            "closing": "Thank you,",
            "department": f"{project['customer']} PMO",
            "thread_id": f"thr-{project['contract_id']}-client",
            "intentional_stale": True,
        }
    if t == "client_followup":
        return {
            "doc_type": "email",
            "subject": f"Re: {project['project']} delivery version confirmation",
            "sender": people["customer_contact"],
            "recipient": people["csm"],
            "dest_domain": slug(company) + ".example",
            "greeting": f"{people['csm']},",
            "body": (
                f"Following up. {project['customer']} still has not seen an amendment. "
                f"We continue to cite {ev.params['cited_version']} in internal tickets. "
                f"Please confirm whether anything legally changed; this email itself is "
                f"not a legal instrument."
            ),
            "closing": "Best,",
            "department": f"{project['customer']} PMO",
            "thread_id": f"thr-{project['contract_id']}-client",
            "intentional_stale": True,
        }
    if t == "legal_supplement":
        adopt = ev.params.get("adopt_roadmap")
        body = (
            f"Amendment to {project['contract_id']} is executed by {people['counsel']}. "
            f"The signed packet is superseded for delivery versioning. "
        )
        if adopt:
            body += (
                "This amendment does not reprint a version numeral. It adopts the "
                "product roadmap target recorded in the Q-cycle notes for this project. "
                "Anyone reconstructing the legally effective delivery version must read "
                "that notes file together with this amendment. A customer email citing "
                "the old packet is not controlling."
            )
        else:
            body += f"The legally effective delivery version is {ev.params['version']}."
        return {
            "doc_type": "email",
            "subject": f"Amendment executed — {project['contract_id']}",
            "sender": people["counsel"],
            "recipient": people["pm"],
            "dest_domain": "internal.example",
            "greeting": f"{people['pm']},",
            "body": body,
            "closing": "On file,",
            "department": project["departments"]["legal"],
            "thread_id": f"thr-{project['contract_id']}-amend",
        }
    if t == "grant_access":
        return {
            "doc_type": "email",
            "subject": f"Staging access for {project['customer']}",
            "sender": people["eng"],
            "recipient": people["csm"],
            "dest_domain": "internal.example",
            "greeting": "Team,",
            "body": (
                "Staging credentials were issued after the amendment file was present. "
                "This note does not restate the legally effective version. Access is a "
                "separate flag from revenue recognition."
            ),
            "closing": "—",
            "department": project["departments"]["product"],
            "thread_id": f"thr-{project['contract_id']}-access",
        }
    if t == "release_beta":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} beta cut",
            "owner": people["eng"],
            "attendees": [people["eng"], people["pm"], people["csm"]],
            "agenda": ["Cut criteria", "Customer communication", "Finance handoff"],
            "discussion": (
                f"Engineering tagged a beta as {ev.params['version']}. This does not by "
                f"itself change the legal version. Finance should not book amounts from "
                f"this meeting."
            ),
            "actions": [f"{people['finance']}: wait for a period close"],
            "parking": "GA date not committed.",
            "department": project["departments"]["product"],
        }
    if t == "standup_notes":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} standup",
            "owner": people["eng"],
            "attendees": [people["eng"], people["pm"]],
            "agenda": ["Yesterday", "Today", "Blockers"],
            "discussion": (
                "Standup chatter about the beta tag. No legal or finance decisions. "
                "Do not treat standup as a source for recognized revenue or the "
                "legally effective delivery version."
            ),
            "actions": ["None"],
            "parking": "None",
            "department": project["departments"]["product"],
        }
    if t == "status_pulse":
        p = ev.params
        return {
            "doc_type": "email",
            "subject": f"{project['project']} weekly pulse {p['week_index']}",
            "sender": people["pm"],
            "recipient": people["csm"],
            "dest_domain": "internal.example",
            "greeting": "Team,",
            "body": (
                f"Weekly pulse {p['week_index']} for {project['project']}. Ticket "
                f"{p['ticket']} is open against {p['blocker']}. This note does not "
                f"restate delivery versioning, contract identifiers, or recognized "
                f"revenue. It exists so the project timeline has unique intervening "
                f"documents between legal and finance events."
            ),
            "closing": "— pm",
            "department": project["departments"]["product"],
            "thread_id": f"thr-{project['contract_id']}-pulse-{p['week_index']}",
        }
    if t == "finance_preview":
        return {
            "doc_type": "finance_report",
            "period": "preview-unaudited",
            "preparer": people["finance"],
            "status": "draft",
            "narrative": (
                f"Unaudited preview for {project['project']}. Draft figure "
                f"{ev.params['amount']} is not the period close and must not be used as "
                f"recognized revenue. A later June close may still contain an error that "
                f"audit will restate."
            ),
            "notes": "Preview figures can be coincidentally close to a later misrecord.",
            "department": project["departments"]["revenue"],
        }
    if t == "misrecord_revenue":
        return {
            "doc_type": "finance_report",
            "period": "June-close",
            "preparer": people["finance"],
            "status": "posted-unaudited",
            "narrative": (
                f"{people['finance']} posted recognized revenue for {project['customer']} "
                f"/ {project['project']} at period close. The posted figure is "
                f"{ev.params['amount']}. Internal Audit has not yet reviewed the cut. "
                f"Treat this file as the June number only."
            ),
            "notes": "If a later restatement exists, the later file controls.",
            "department": project["departments"]["revenue"],
        }
    if t == "audit_correction":
        return {
            "doc_type": "finance_report",
            "period": "July-restatement",
            "preparer": people["auditor"],
            "status": "restated-final",
            "narrative": (
                f"{people['auditor']} completed the restatement. The June close overstated "
                f"recognition. The restated recognized-revenue number is "
                f"{ev.params['amount']}. Do not add the June figure on top of this figure."
            ),
            "notes": "Adjustment equals restated amount minus the June misrecord.",
            "department": "Internal Audit",
        }
    if t == "legal_reminder":
        return {
            "doc_type": "email",
            "subject": f"File hygiene for {project['contract_id']}",
            "sender": people["counsel"],
            "recipient": people["pm"],
            "dest_domain": "internal.example",
            "greeting": f"{people['pm']},",
            "body": (
                "Reminder to keep the amendment next to the signed packet. This reminder "
                "does not restate the adopted version numeral. Reconstruct versioning "
                "from the amendment plus the roadmap notes, not from this ping."
            ),
            "closing": "— counsel",
            "department": project["departments"]["legal"],
            "thread_id": f"thr-{project['contract_id']}-hygiene",
        }
    raise KeyError(t)


def render_world(sim: SimulatedWorld) -> list[Artifact]:
    domain = sim.spec.get("domain")
    if domain == "researchlab":
        from longworld.domains.researchlab.render import render_lab

        return render_lab(sim)
    if domain == "codeforge":
        from longworld.domains.codeforge.render import render_code

        return render_code(sim)
    env = jinja_env()
    project = sim.spec["project"]
    prefix = sim.spec["prefix"]
    is_focal = bool(project.get("is_focal", prefix == "focal"))
    people = project["people"]
    artifacts: list[Artifact] = []
    for ev in sim.events:
        if ev.skipped:
            continue
        meta = _body_for(ev.type, project, ev)
        facts: list[str] = []
        ground = _ground_values(ev)
        doc_type = meta["doc_type"]
        vis = ev.visibility[0]
        aid = f"{sim.spec['world_id']}.{vis}"
        common = {
            "artifact_id": aid,
            "date": ev.time.isoformat(),
            "project": project["project"],
            "company": project["company"],
            "customer": project["customer"],
            "facts": facts,
            "company_slug": slug(project["company"]),
        }
        if doc_type == "email":
            tmpl = env.get_template("email.j2")
            sender = meta["sender"]
            recipient = meta["recipient"]
            dest = meta.get("dest_domain", "internal.example")
            text = tmpl.render(
                **common,
                subject=meta["subject"],
                sender=sender,
                sender_slug=_person_email_local(sender),
                recipient=recipient,
                recipient_slug=_person_email_local(recipient),
                dest_domain=dest,
                thread_id=meta["thread_id"],
                greeting=meta["greeting"],
                body=meta["body"],
                closing=meta["closing"],
                department=meta["department"],
            )
        elif doc_type == "meeting_notes":
            tmpl = env.get_template("meeting_notes.j2")
            text = tmpl.render(
                **common,
                meeting_title=meta["meeting_title"],
                owner=meta["owner"],
                attendees=meta["attendees"],
                agenda=meta["agenda"],
                discussion=meta["discussion"],
                actions=meta["actions"],
                parking=meta["parking"],
                department=meta["department"],
            )
        else:
            tmpl = env.get_template("finance_report.j2")
            text = tmpl.render(
                **common,
                period=meta["period"],
                preparer=meta["preparer"],
                status=meta["status"],
                narrative=meta["narrative"],
                notes=meta["notes"],
                department=meta["department"],
            )
        artifacts.append(
            Artifact(
                artifact_id=aid,
                doc_type=doc_type,
                time=ev.time,
                project=project["project"],
                prefix=prefix,
                reveals_events=[ev.id],
                text=text,
                facts=facts,
                slots={
                    "event_type": ev.type,
                    "params": dict(ev.params),
                    "ground_values": ground,
                },
                is_focal=is_focal,
                intentional_stale=bool(meta.get("intentional_stale")),
                stale_event_id=ev.id if meta.get("intentional_stale") else None,
            )
        )
    artifacts.sort(key=lambda a: (a.time, a.artifact_id))
    return artifacts
