from __future__ import annotations

import hashlib
from typing import Any

from longworld.core.render import (
    Artifact,
    _audience_role,
    _author_role,
    _body_for,
    _person_email_local,
    slug,
)
from longworld.core.render import (
    _ground_values as _core_ground_values,
)
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)
from longworld.core.world import Event, SimulatedWorld
from longworld.domains.company.templates_env import jinja_env

_RENEWAL_BASE_TYPE = {
    "renewal_roadmap": "change_roadmap",
    "renewal_amendment": "legal_supplement",
    "renewal_release": "release_beta",
    "renewal_close": "misrecord_revenue",
    "renewal_audit": "audit_correction",
}
_CYCLE_BASE_TYPE = {
    "cycle_plan": "change_roadmap",
    "cycle_failure": "standup_notes",
    "cycle_recovery": "grant_access",
    "cycle_release": "release_beta",
    "cycle_audit": "audit_correction",
}
_EVENT_BASE_TYPE = {**_RENEWAL_BASE_TYPE, **_CYCLE_BASE_TYPE}


def _renewal_meta(project: dict[str, Any], event: Event) -> dict[str, Any]:
    people = project["people"]
    event_type = event.type
    if event_type == "renewal_roadmap":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} renewal roadmap",
            "owner": people["pm"],
            "attendees": [people["pm"], people["eng"], people["finance"]],
            "agenda": [
                "Prior-cycle audit findings",
                "Renewal delivery target",
                "Required legal instrument",
            ],
            "discussion": (
                f"The renewal workstream opened after the first-cycle audit. Product "
                f"selected {event.params['version']} and deliverable "
                f"{event.params['v3_deliverable']}. This roadmap is not itself a legal "
                "instrument; Legal must execute the renewal amendment before release."
            ),
            "actions": [
                f"{people['counsel']}: prepare the renewal amendment",
                f"{people['eng']}: stage the renewal release candidate",
            ],
            "parking": "The renewal close remains in the finance workstream.",
            "department": project["departments"]["product"],
        }
    if event_type == "renewal_amendment":
        return {
            "doc_type": "email",
            "subject": f"Renewal amendment executed — {project['contract_id']}",
            "sender": people["counsel"],
            "recipient": people["pm"],
            "dest_domain": "internal.example",
            "greeting": f"{people['pm']},",
            "body": (
                f"The renewal amendment for {project['contract_id']} is executed. It "
                "adopts the delivery target in the renewal roadmap without reprinting "
                "the version token. The earlier audit triggered this separate renewal "
                "workstream but does not choose the legal version."
            ),
            "closing": "On file,",
            "department": project["departments"]["legal"],
            "thread_id": f"thr-{project['contract_id']}-renewal-amendment",
        }
    if event_type == "renewal_release":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} renewal release",
            "owner": people["eng"],
            "attendees": [people["eng"], people["pm"], people["finance"]],
            "agenda": ["Renewal amendment", "Release tag", "Finance handoff"],
            "discussion": (
                f"Engineering cut renewal release {event.params['version']} after Legal "
                "filed the renewal amendment. This tag identifies the release but does "
                "not restate the roadmap version or a recognized-revenue amount."
            ),
            "actions": [f"{people['finance']}: open the renewal close"],
            "parking": "The renewal audit remains pending.",
            "department": project["departments"]["product"],
        }
    if event_type == "renewal_close":
        return {
            "doc_type": "finance_report",
            "period": "renewal-close",
            "preparer": people["finance"],
            "status": "posted-unaudited",
            "narrative": (
                f"The renewal release workstream posted {event.params['amount']} as its "
                "recognized-revenue amount. This is the renewal close, not the prior "
                "cycle's final audit amount. Internal Audit has not yet corrected it."
            ),
            "notes": "A later renewal audit controls this workstream.",
            "department": project["departments"]["revenue"],
        }
    if event_type == "renewal_audit":
        return {
            "doc_type": "finance_report",
            "period": "renewal-audit",
            "preparer": people["auditor"],
            "status": "restated-final",
            "narrative": (
                f"Internal Audit completed the renewal audit. The final renewal "
                f"recognized-revenue amount is {event.params['amount']}; it replaces "
                "the unaudited renewal close and does not replace the prior-cycle audit."
            ),
            "notes": "Read the two audit cycles separately before tracing the change.",
            "department": "Internal Audit",
        }
    raise KeyError(event_type)


def _cycle_meta(project: dict[str, Any], event: Event) -> dict[str, Any]:
    people = project["people"]
    params = event.params
    cycle_id = str(params["cycle_id"])
    agreement_id = str(params["agreement_id"])
    kind = str(params["kind"]).replace("-", " ")
    if event.type == "cycle_plan":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} {cycle_id} contract plan",
            "owner": people["pm"],
            "attendees": [people["pm"], people["counsel"], people["eng"]],
            "agenda": [
                f"Prior audit dependency for {cycle_id}",
                f"Contract revision {agreement_id}",
                f"Release candidate {params['candidate_token']}",
            ],
            "discussion": (
                f"The {cycle_id} {kind} workstream begins only after the preceding "
                "cycle's final "
                f"audit. The team opened agreement {agreement_id} under plan "
                f"{params['plan_version']} and assigned candidate "
                f"{params['candidate_token']}. The preceding audit is historical "
                f"evidence, not authorization for the {cycle_id} release. Engineering "
                "must record validation before recovery can proceed."
            ),
            "actions": [
                f"{people['eng']}: validate {params['candidate_token']}",
                f"{people['counsel']}: retain {agreement_id} with the cycle file",
            ],
            "parking": f"Revenue for {cycle_id} remains unrecognized until its audit.",
            "department": project["departments"]["product"],
        }
    if event.type == "cycle_failure":
        return {
            "doc_type": "email",
            "subject": f"Failed release attempt — {cycle_id}",
            "sender": people["eng"],
            "recipient": people["pm"],
            "dest_domain": "internal.example",
            "greeting": f"{people['pm']},",
            "body": (
                f"Candidate {params['candidate_token']} failed the {kind} validation. "
                f"The incident is filed as {params['incident_token']}; the observed "
                f"failure mode was {params['failure_mode']}. The candidate is blocked and "
                f"must not move to the {cycle_id} release register. Operations will issue "
                f"a separate {cycle_id} recovery record after reproducing the fault. That "
                f"later {cycle_id} record will name the corrective action, while this "
                "failure report remains the authority for the incident identity."
            ),
            "closing": "— engineering",
            "department": project["departments"]["product"],
            "thread_id": f"thr-{agreement_id}-{cycle_id}-failure",
        }
    if event.type == "cycle_recovery":
        return {
            "doc_type": "email",
            "subject": f"Recovery accepted — {cycle_id}",
            "sender": people["eng"],
            "recipient": people["counsel"],
            "dest_domain": "internal.example",
            "greeting": f"{people['counsel']},",
            "body": (
                f"The failed {kind} attempt has been reproduced and corrected under "
                f"resolution {params['resolution_token']}. The incident identifier is "
                f"intentionally not repeated in the {cycle_id} recovery; pair this record "
                "with the immediately preceding failure report when reconstructing the "
                f"control history. Resolution for {cycle_id} clears technical validation "
                "but does not itself create a release tag or a financial amount."
            ),
            "closing": "— recovery lead",
            "department": project["departments"]["product"],
            "thread_id": f"thr-{agreement_id}-{cycle_id}-recovery",
        }
    if event.type == "cycle_release":
        return {
            "doc_type": "meeting_notes",
            "meeting_title": f"{project['project']} {cycle_id} recovered release",
            "owner": people["eng"],
            "attendees": [people["eng"], people["pm"], people["finance"]],
            "agenda": ["Recovery evidence", "Release registration", "Audit handoff"],
            "discussion": (
                f"After the recorded recovery, Engineering registered release "
                f"{params['release_token']} for the {kind} cycle. The release inherits "
                f"agreement {agreement_id} but does not restate the incident or resolution "
                "tokens. Finance may now prepare the cycle close; Internal Audit must still "
                f"publish the controlling {cycle_id} amount before the next contract "
                "cycle opens."
            ),
            "actions": [f"{people['auditor']}: complete the {cycle_id} audit"],
            "parking": f"The cycle after {cycle_id} is blocked on this final audit.",
            "department": project["departments"]["product"],
        }
    if event.type == "cycle_audit":
        return {
            "doc_type": "finance_report",
            "period": f"{cycle_id}-final-audit",
            "preparer": people["auditor"],
            "status": "restated-final",
            "narrative": (
                f"Internal Audit closed the {kind} cycle for agreement {agreement_id}. "
                f"Finance had proposed {params['close_amount']}; after testing the "
                f"recovered release, Audit set the controlling recognized-revenue amount "
                f"to {params['amount']}. This amount closes only {cycle_id}. Its approval "
                "is the prerequisite for the following contract cycle and does not replace "
                "earlier audit records in the longitudinal trace."
            ),
            "notes": (
                f"For {cycle_id}, use the final amount instead of the proposed close."
            ),
            "department": "Internal Audit",
        }
    raise KeyError(event.type)


def _ground_values(event: Event) -> list[str]:
    if event.type == "renewal_roadmap":
        return [str(event.params["version"]), str(event.params["v3_deliverable"])]
    if event.type == "renewal_amendment":
        return []
    if event.type == "renewal_release":
        return [str(event.params["version"])]
    if event.type in {"renewal_close", "renewal_audit"}:
        return [str(event.params["amount"])]
    if event.type == "cycle_plan":
        return [
            str(event.params["agreement_id"]),
            str(event.params["plan_version"]),
            str(event.params["candidate_token"]),
        ]
    if event.type == "cycle_failure":
        return [
            str(event.params["candidate_token"]),
            str(event.params["incident_token"]),
            str(event.params["failure_mode"]),
        ]
    if event.type == "cycle_recovery":
        return [str(event.params["resolution_token"])]
    if event.type == "cycle_release":
        return [str(event.params["release_token"])]
    if event.type == "cycle_audit":
        return [str(event.params["amount"]), str(event.params["close_amount"])]
    return _core_ground_values(event)


def render_company(sim: SimulatedWorld) -> list[Artifact]:
    """Render company artifacts, including the event-bearing renewal workstream."""
    from longworld.core.grounded import SKIP_RENDER_TYPES

    env = jinja_env()
    project = sim.spec["project"]
    prefix = sim.spec["prefix"]
    is_focal = bool(project.get("is_focal", prefix == "focal"))
    renewal_types = set(_RENEWAL_BASE_TYPE)
    cycle_types = set(_CYCLE_BASE_TYPE)
    artifacts: list[Artifact] = []
    for event in sim.events:
        if event.skipped or event.type in SKIP_RENDER_TYPES:
            continue
        if event.type in {
            "sec_filing",
            "sec_filing_eligibility_policy",
            "sec_filing_approval",
            "sec_amendment_resolution",
            "sec_filing_publication_ratification",
            "sec_source_section",
            "sec_financial_answer",
        }:
            params = event.params
            artifact_id = f"{sim.spec['world_id']}.{event.visibility[0]}"
            if event.type == "sec_filing" or event.type == "sec_source_section":
                text = str(params["text"])
                source_origin = SourceOrigin(str(params["source_origin"]))
                workflow_kind = WorkflowKind.HYBRID_CAUSAL
                provenance_id = str(params["provenance_id"])
                evidence_role = EvidenceRole.CAUSAL_SUPPORTING
                real_record = True
            elif event.type == "sec_filing_eligibility_policy":
                accepted = ", ".join(str(item) for item in params["accepted_forms"])
                text = (
                    "SEC filing release-eligibility policy\n"
                    f"Accepted annual forms: {accepted}.\n"
                    "Filing window: no later than "
                    f"{params['max_days_after_report']} days after the reported "
                    "period end.\n"
                    "The policy engine must read the selected filing body to determine "
                    "its actual form, filing date, reporting period, and accession. This "
                    "memo does not restate those filing-specific facts."
                )
                source_origin = SourceOrigin.SYNTHETIC_WORLD
                workflow_kind = WorkflowKind.HYBRID_CAUSAL
                provenance_id = (
                    "derived-sha256:" + hashlib.sha256(text.encode()).hexdigest()
                )
                evidence_role = EvidenceRole.CAUSAL_SUPPORTING
                real_record = False
            elif event.type == "sec_filing_approval":
                text = (
                    "SEC filing release committee decision\n"
                    "Status: committee-approved. The committee authorizes publication "
                    "of the result produced by the eligibility policy. This decision "
                    "does not repeat the filing form, dates, accession, or computed "
                    "eligibility status."
                )
                source_origin = SourceOrigin.SYNTHETIC_WORLD
                workflow_kind = WorkflowKind.HYBRID_CAUSAL
                provenance_id = (
                    "derived-sha256:" + hashlib.sha256(text.encode()).hexdigest()
                )
                evidence_role = EvidenceRole.CAUSAL_SUPPORTING
                real_record = False
            elif event.type == "sec_amendment_resolution":
                text = (
                    "SEC filing amendment relation control\n"
                    "Status: relation-evaluated. This control resolves the linked "
                    "filings only after reading both filing bodies. It does not repeat "
                    "their forms, accessions, filing dates, or reporting periods."
                )
                source_origin = SourceOrigin.SYNTHETIC_WORLD
                workflow_kind = WorkflowKind.HYBRID_CAUSAL
                provenance_id = (
                    "derived-sha256:" + hashlib.sha256(text.encode()).hexdigest()
                )
                evidence_role = EvidenceRole.CAUSAL_GOLD
                real_record = False
            elif event.type == "sec_financial_answer":
                text = (
                    "SEC financial answer. "
                    f"Checkpoint: {params['control_stage']}. "
                    "Status: financial-reconstructed. Reconstruct the tagged program "
                    "from cited statements, notes, and certifications. This memo does "
                    "not restate amounts."
                )
                source_origin = SourceOrigin.SYNTHETIC_WORLD
                workflow_kind = WorkflowKind.HYBRID_CAUSAL
                provenance_id = (
                    "derived-sha256:" + hashlib.sha256(text.encode()).hexdigest()
                )
                evidence_role = EvidenceRole.CAUSAL_GOLD
                real_record = False
            else:
                text = (
                    "SEC filing publication ratification\n"
                    f"Decision checkpoint: {params['control_stage']}. "
                    "Status: publication-ratified. This control activates the "
                    "committee's prior decision without repeating the filing form, "
                    "dates, accession, or computed eligibility result."
                )
                source_origin = SourceOrigin.SYNTHETIC_WORLD
                workflow_kind = WorkflowKind.HYBRID_CAUSAL
                provenance_id = (
                    "derived-sha256:" + hashlib.sha256(text.encode()).hexdigest()
                )
                evidence_role = EvidenceRole.CAUSAL_SUPPORTING
                real_record = False
            artifact = Artifact(
                artifact_id=artifact_id,
                doc_type=event.type,
                time=event.time,
                project=project["project"],
                prefix=prefix,
                reveals_events=[event.id],
                text=text,
                facts=[],
                slots={
                    "event_type": event.type,
                    "params": dict(params),
                    "ground_values": list(params.get("ground_values") or []),
                    "author_role": "sec_release_committee",
                    "audience_role": "sec_release_committee",
                    "real_workflow_record": real_record,
                    "source_workflow_id": str(params["workflow_id"]),
                    "source_record_id": str(params.get("record_id") or ""),
                    "parent_provenance_id": str(
                        params.get("parent_provenance_id") or ""
                    ),
                    "source_url": str(params.get("source_url") or ""),
                    "source_family": str(params.get("source_family") or ""),
                },
                is_focal=is_focal,
                role="sec_release_committee",
            )
            classify_artifact(
                artifact,
                source_origin=source_origin,
                workflow_kind=workflow_kind,
                evidence_role=evidence_role,
                workflow_id=sim.spec["world_id"],
                provenance_id=provenance_id,
            )
            artifacts.append(artifact)
            continue
        if event.type in renewal_types:
            meta = _renewal_meta(project, event)
        elif event.type in cycle_types:
            meta = _cycle_meta(project, event)
        else:
            meta = _body_for(event.type, project, event)
        ground = _ground_values(event)
        doc_type = str(meta["doc_type"])
        artifact_id = f"{sim.spec['world_id']}.{event.visibility[0]}"
        common = {
            "artifact_id": artifact_id,
            "date": event.time.isoformat(),
            "project": project["project"],
            "company": project["company"],
            "customer": project["customer"],
            "facts": [],
            "company_slug": slug(project["company"]),
        }
        if doc_type == "email":
            sender = str(meta["sender"])
            recipient = str(meta["recipient"])
            text = env.get_template("email.j2").render(
                **common,
                subject=meta["subject"],
                sender=sender,
                sender_slug=_person_email_local(sender),
                recipient=recipient,
                recipient_slug=_person_email_local(recipient),
                dest_domain=meta.get("dest_domain", "internal.example"),
                thread_id=meta["thread_id"],
                greeting=meta["greeting"],
                body=meta["body"],
                closing=meta["closing"],
                department=meta["department"],
            )
        elif doc_type == "meeting_notes":
            text = env.get_template("meeting_notes.j2").render(
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
            text = env.get_template("finance_report.j2").render(
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
                artifact_id=artifact_id,
                doc_type=doc_type,
                time=event.time,
                project=project["project"],
                prefix=prefix,
                reveals_events=[event.id],
                text=text,
                facts=[],
                slots={
                    "event_type": event.type,
                    "params": dict(event.params),
                    "ground_values": ground,
                    "author_role": _author_role(
                        project, _EVENT_BASE_TYPE.get(event.type, event.type)
                    ),
                    "audience_role": _audience_role(
                        _EVENT_BASE_TYPE.get(event.type, event.type)
                    ),
                },
                is_focal=is_focal,
                intentional_stale=bool(meta.get("intentional_stale")),
                stale_event_id=event.id if meta.get("intentional_stale") else None,
                role=_author_role(
                    project, _EVENT_BASE_TYPE.get(event.type, event.type)
                ),
            )
        )
    artifacts.sort(key=lambda artifact: (artifact.time, artifact.artifact_id))
    return artifacts
