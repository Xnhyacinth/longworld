from __future__ import annotations

import hashlib
from datetime import date
from itertools import pairwise
from typing import Any

from longworld.core.cascade import apply_cascade, check_cascade
from longworld.core.filingworkflow import parse_sec_identity_spans
from longworld.core.grounded import apply_grounded, check_grounded
from longworld.core.provenance import ProvenanceError
from longworld.core.secxbrl import parse_ixbrl_display_number
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
        "sec_filing_facts": {},
        "sec_filing_relations": {},
        "sec_eligibility_candidates": {},
        "sec_eligibility_approvals": {},
        "sec_xbrl_facts": {},
        "sec_certification_facts": {},
        "issuer_ir_metrics": {},
        "issuer_ir_relations": {},
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
    if ev.type == "sec_filing":
        return True, None
    if ev.type == "sec_filing_eligibility_policy":
        facts = state.values.get("sec_filing_facts") or {}
        if str(ev.params.get("record_id") or "") not in facts:
            return False, "sec_filing_missing"
        return True, None
    if ev.type == "sec_filing_approval":
        candidates = state.values.get("sec_eligibility_candidates") or {}
        if str(ev.params.get("record_id") or "") not in candidates:
            return False, "sec_eligibility_missing"
        return True, None
    if ev.type == "sec_amendment_resolution":
        facts = state.values.get("sec_filing_facts") or {}
        if any(
            str(ev.params.get(field) or "") not in facts
            for field in ("record_id", "target_record_id")
        ):
            return False, "sec_amendment_endpoint_missing"
        return True, None
    if ev.type == "sec_prior_annual_filing_relation":
        facts = state.values.get("sec_filing_facts") or {}
        if any(
            str(ev.params.get(field) or "") not in facts
            for field in ("record_id", "target_record_id")
        ):
            return False, "sec_annual_endpoint_missing"
        return True, None
    if ev.type == "sec_annual_revenue_change":
        current_id = str(ev.params.get("record_id") or "")
        prior_id = str(ev.params.get("target_record_id") or "")
        role = str(ev.params.get("required_role") or "")
        xbrl = state.values.get("sec_xbrl_facts") or {}
        if not role or any(
            role not in (xbrl.get(record_id) or {})
            for record_id in (current_id, prior_id)
        ):
            return False, "sec_annual_financial_fact_missing"
        relation_id = str(ev.params.get("required_relation_id") or "")
        if relation_id not in (state.values.get("sec_filing_relations") or {}):
            return False, "sec_annual_relation_missing"
        return True, None
    if ev.type == "sec_filing_publication_ratification":
        approvals = state.values.get("sec_eligibility_approvals") or {}
        if str(ev.params.get("record_id") or "") not in approvals:
            return False, "sec_eligibility_approval_missing"
        return True, None
    if ev.type == "sec_source_section":
        return True, None
    if ev.type == "issuer_ir_source_section":
        return True, None
    if ev.type == "issuer_ir_prior_filing_relation":
        metrics = state.values.get("issuer_ir_metrics") or {}
        if any(
            str(ev.params.get(field) or "") not in metrics
            for field in ("record_id", "target_record_id")
        ):
            return False, "issuer_ir_relation_endpoint_missing"
        return True, None
    if ev.type == "issuer_ir_cross_year_answer":
        metrics = state.values.get("issuer_ir_metrics") or {}
        required_roles = ev.params.get("required_roles")
        record_ids = ev.params.get("record_ids")
        if (
            not isinstance(required_roles, list)
            or not isinstance(record_ids, list)
            or any(
                any(
                    str(role) not in (metrics.get(str(record_id)) or {})
                    for role in required_roles
                )
                for record_id in record_ids
            )
        ):
            return False, "issuer_ir_metrics_missing"
        relations = state.values.get("issuer_ir_relations") or {}
        if any(
            str(relation_id) not in relations
            for relation_id in ev.params.get("required_relation_ids") or []
        ):
            return False, "issuer_ir_relation_missing"
        return True, None
    if ev.type == "sec_financial_answer":
        if str(ev.params.get("compose") or "compute") == "copy":
            prerequisite = str(ev.params.get("prerequisite_answer_key") or "")
            if not prerequisite or not isinstance(state.values.get(prerequisite), str):
                return False, "sec_financial_prerequisite_missing"
            return True, None
        record_id = str(ev.params.get("record_id") or "")
        xbrl = (state.values.get("sec_xbrl_facts") or {}).get(record_id) or {}
        required = ev.params.get("required_roles")
        if not isinstance(required, list) or any(
            str(role) not in xbrl for role in required
        ):
            return False, "sec_xbrl_facts_missing"
        prerequisite = str(ev.params.get("prerequisite_answer_key") or "")
        if prerequisite and not isinstance(state.values.get(prerequisite), str):
            return False, "sec_financial_prerequisite_missing"
        answer_family = str(ev.params.get("answer_family") or "")
        if answer_family == "balance_certification" or (
            not answer_family and ev.params.get("control_tier") in {"64k", "128k"}
        ):
            certs = (state.values.get("sec_certification_facts") or {}).get(
                record_id
            ) or {}
            sox = list(certs.get("ex_32_1") or []) + list(certs.get("ex_32_2") or [])
            if (
                len(certs.get("ex_31_1") or []) != 1
                or len(certs.get("ex_31_2") or []) != 1
                or len(sox) != 2
            ):
                return False, "sec_certification_missing"
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
    if t == "sec_filing":
        text = str(p.get("text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != p.get("text_sha256"):
            return
        parsed = parse_sec_identity_spans(text, p.get("fact_spans"))
        if parsed is None:
            return
        filing_facts = dict(state.values.get("sec_filing_facts") or {})
        filing_facts[str(p["record_id"])] = parsed
        state.set("sec_filing_facts", filing_facts, eid, day)
    elif t == "sec_filing_eligibility_policy":
        record_id = str(p.get("record_id") or "")
        selected_facts = (state.values.get("sec_filing_facts") or {}).get(record_id)
        if not isinstance(selected_facts, dict):
            return
        try:
            filing_date = date.fromisoformat(str(selected_facts["filing_date"]))
            report_date = date.fromisoformat(str(selected_facts["report_date"]))
            max_days_after_report = int(p["max_days_after_report"])
        except (KeyError, TypeError, ValueError):
            return
        accepted_forms = p.get("accepted_forms")
        if not isinstance(accepted_forms, list) or not all(
            isinstance(form, str) and form for form in accepted_forms
        ):
            return
        eligibility_candidate = {
            **selected_facts,
            "eligible": (
                str(selected_facts.get("form") or "") in accepted_forms
                and 0 <= (filing_date - report_date).days <= max_days_after_report
            ),
        }
        candidates = dict(state.values.get("sec_eligibility_candidates") or {})
        candidates[record_id] = eligibility_candidate
        state.set("sec_eligibility_candidates", candidates, eid, day)
    elif t == "sec_filing_approval":
        record_id = str(p.get("record_id") or "")
        approval_candidate = (state.values.get("sec_eligibility_candidates") or {}).get(
            record_id
        )
        if not isinstance(approval_candidate, dict):
            return
        status = (
            "APPROVED"
            if approval_candidate.get("eligible") and p.get("approved") is True
            else "BLOCKED"
        )
        answer = (
            f"{status} | {approval_candidate.get('report_date')} | "
            f"{approval_candidate.get('accession')}"
        )
        approvals = dict(state.values.get("sec_eligibility_approvals") or {})
        approvals[record_id] = answer
        state.set("sec_eligibility_approvals", approvals, eid, day)
    elif t in {
        "sec_amendment_resolution",
        "sec_prior_annual_filing_relation",
        "sec_annual_revenue_change",
        "sec_filing_publication_ratification",
    }:
        if t == "sec_amendment_resolution":
            amendment_id = str(p.get("record_id") or "")
            original_id = str(p.get("target_record_id") or "")
            filing_facts = state.values.get("sec_filing_facts") or {}
            amendment = filing_facts.get(amendment_id)
            original = filing_facts.get(original_id)
            answer_key = str(p.get("answer_key") or "")
            if (
                not isinstance(amendment, dict)
                or not isinstance(original, dict)
                or not answer_key
            ):
                return
            amendment_form = str(amendment.get("form") or "")
            try:
                valid_dates = date.fromisoformat(
                    str(amendment.get("filing_date") or "")
                ) >= date.fromisoformat(str(original.get("filing_date") or ""))
            except ValueError:
                return
            is_amendment = (
                amendment_form.endswith("/A")
                and amendment_form.removesuffix("/A") == str(original.get("form") or "")
                and amendment.get("report_date") == original.get("report_date")
                and valid_dates
            )
            status = "AMENDS" if is_amendment else "INVALID_RELATION"
            answer = (
                f"{status} | {amendment.get('accession')} | "
                f"{original.get('accession')} | {amendment.get('report_date')}"
            )
            relations = dict(state.values.get("sec_filing_relations") or {})
            relations[str(p.get("source_relation_id") or "")] = answer
            state.set("sec_filing_relations", relations, eid, day)
            state.set(answer_key, answer, eid, day)
            return
        if t == "sec_prior_annual_filing_relation":
            current_id = str(p.get("record_id") or "")
            prior_id = str(p.get("target_record_id") or "")
            filing_facts = state.values.get("sec_filing_facts") or {}
            current = filing_facts.get(current_id)
            prior = filing_facts.get(prior_id)
            answer_key = str(p.get("answer_key") or "")
            relation_id = str(p.get("source_relation_id") or "")
            if (
                not isinstance(current, dict)
                or not isinstance(prior, dict)
                or not answer_key
                or not relation_id
                or p.get("resolution_kind") != "prior_annual_filing"
                or p.get("relation_provenance") != "authentic_source"
                or p.get("adjacency_proof")
                != "manifest_adapter_canonical_recomputation"
            ):
                return
            try:
                ordered = date.fromisoformat(
                    str(current.get("filing_date") or "")
                ) > date.fromisoformat(
                    str(prior.get("filing_date") or "")
                ) and date.fromisoformat(
                    str(current.get("report_date") or "")
                ) > date.fromisoformat(str(prior.get("report_date") or ""))
            except ValueError:
                return
            valid = (
                isinstance(current.get("cik"), str)
                and len(current["cik"]) == 10
                and current["cik"].isdigit()
                and current.get("cik") == prior.get("cik")
                and current.get("form") == "10-K"
                and prior.get("form") == "10-K"
                and ordered
            )
            status = "PRIOR_ANNUAL" if valid else "INVALID_RELATION"
            answer = (
                f"{status} | {current.get('accession')} | "
                f"{prior.get('accession')} | {current.get('report_date')} | "
                f"{prior.get('report_date')}"
            )
            relations = dict(state.values.get("sec_filing_relations") or {})
            relations[relation_id] = answer
            state.set("sec_filing_relations", relations, eid, day)
            state.set(answer_key, answer, eid, day)
            return
        if t == "sec_annual_revenue_change":
            current_id = str(p.get("record_id") or "")
            prior_id = str(p.get("target_record_id") or "")
            role = str(p.get("required_role") or "")
            relation_id = str(p.get("required_relation_id") or "")
            relations = state.values.get("sec_filing_relations") or {}
            relation_answer = str(relations.get(relation_id) or "")
            xbrl = state.values.get("sec_xbrl_facts") or {}
            facts = state.values.get("sec_filing_facts") or {}
            answer_key = str(p.get("answer_key") or "")
            if (
                not relation_answer.startswith("PRIOR_ANNUAL |")
                or not answer_key
                or not role
            ):
                return
            try:
                current_value = int(xbrl[current_id][role])
                prior_value = int(xbrl[prior_id][role])
                current_report = str(facts[current_id]["report_date"])
                prior_report = str(facts[prior_id]["report_date"])
            except (KeyError, TypeError, ValueError):
                return
            answer = (
                f"ANNUAL_REVENUE_CHANGE | {current_report} | {current_value} | "
                f"{prior_report} | {prior_value} | {current_value - prior_value}"
            )
            state.set(answer_key, answer, eid, day)
            return
        record_id = str(p.get("record_id") or "")
        prerequisite_answer_key = str(p.get("prerequisite_answer_key") or "")
        ratified_answer: object = (
            state.values.get(prerequisite_answer_key)
            if prerequisite_answer_key
            else (state.values.get("sec_eligibility_approvals") or {}).get(record_id)
        )
        if not isinstance(ratified_answer, str) or p.get("ratified") is not True:
            return
        answer_key = str(p.get("answer_key") or "")
        if not answer_key:
            return
        state.set(answer_key, ratified_answer, eid, day)
    elif t == "sec_source_section":
        text = str(p.get("text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != p.get("text_sha256"):
            return
        prefix = "SEC source section\n"
        if not text.startswith(prefix) or hashlib.sha256(
            text[len(prefix) :].encode()
        ).hexdigest() != p.get("section_sha256"):
            return
        record_id = str(p.get("record_id") or "")
        section_id = str(p.get("section_id") or "")
        if not record_id or not section_id:
            return
        xbrl_facts = dict(state.values.get("sec_xbrl_facts") or {})
        cert_facts = dict(state.values.get("sec_certification_facts") or {})
        record_xbrl = dict(xbrl_facts.get(record_id) or {})
        record_certs = dict(cert_facts.get(record_id) or {})
        cert_names = list(record_certs.get(section_id) or [])
        for span in p.get("fact_spans") or []:
            if not isinstance(span, dict):
                return
            start = span.get("char_start")
            end = span.get("char_end")
            quote = str(span.get("evidence_quote") or "")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or text[start:end] != quote
            ):
                return
            kind = str(span.get("kind") or "xbrl")
            if kind == "xbrl":
                role = str(span.get("role") or "")
                scale = span.get("scale")
                numeric_value = span.get("numeric_value")
                if (
                    isinstance(scale, bool)
                    or not isinstance(scale, (int, str))
                    or isinstance(numeric_value, bool)
                    or not isinstance(numeric_value, (int, str))
                ):
                    return
                try:
                    value = parse_ixbrl_display_number(
                        quote,
                        scale=int(scale),
                        sign=str(span.get("sign") or ""),
                    )
                except (TypeError, ValueError, ProvenanceError):
                    return
                if not role or value != int(numeric_value):
                    return
                if role in record_xbrl and record_xbrl[role] != value:
                    return
                record_xbrl[role] = value
            elif kind == "certification":
                evidence_spans = span.get("evidence_spans")
                if not isinstance(evidence_spans, dict):
                    return
                certification = {
                    "name": quote,
                    "officer_title": str(span.get("officer_title") or ""),
                    "certification_kind": str(span.get("certification_kind") or ""),
                    "covered_form": str(span.get("covered_form") or ""),
                    "covered_period_end": str(span.get("covered_period_end") or ""),
                    "certification_date": str(span.get("certification_date") or ""),
                }
                required_cert_fields = {
                    "officer_title",
                    "certification_kind",
                    "covered_form",
                    "certification_date",
                }
                if certification["certification_kind"] == "section_906":
                    required_cert_fields.add("covered_period_end")
                elif certification["certification_kind"] != "section_302":
                    return
                if any(not certification[field] for field in required_cert_fields):
                    return
                if set(evidence_spans) != required_cert_fields:
                    return
                for field, evidence in evidence_spans.items():
                    if field not in certification or not isinstance(evidence, dict):
                        return
                    field_start = evidence.get("char_start")
                    field_end = evidence.get("char_end")
                    field_quote = str(evidence.get("evidence_quote") or "")
                    if (
                        isinstance(field_start, bool)
                        or isinstance(field_end, bool)
                        or not isinstance(field_start, int)
                        or not isinstance(field_end, int)
                        or field_start < 0
                        or field_end <= field_start
                        or text[field_start:field_end] != field_quote
                        or str(evidence.get("value") or "") != certification[field]
                    ):
                        return
                cert_names.append(certification)
            else:
                return
        if cert_names:
            record_certs[section_id] = cert_names
            cert_facts[record_id] = record_certs
            state.set("sec_certification_facts", cert_facts, eid, day)
        if record_xbrl != dict(xbrl_facts.get(record_id) or {}):
            xbrl_facts[record_id] = record_xbrl
            state.set("sec_xbrl_facts", xbrl_facts, eid, day)
    elif t == "issuer_ir_source_section":
        text = str(p.get("text") or "")
        prefix = "Issuer IR rendered XBRL statement\n"
        if (
            hashlib.sha256(text.encode()).hexdigest() != p.get("text_sha256")
            or not text.startswith(prefix)
            or hashlib.sha256(text[len(prefix) :].encode()).hexdigest()
            != p.get("section_sha256")
        ):
            return
        record_id = str(p.get("record_id") or "")
        if not record_id:
            return
        metrics = dict(state.values.get("issuer_ir_metrics") or {})
        record_metrics = dict(metrics.get(record_id) or {})
        for span in p.get("fact_spans") or []:
            if not isinstance(span, dict):
                return
            start = span.get("char_start")
            end = span.get("char_end")
            quote = str(span.get("evidence_quote") or "")
            role = str(span.get("role") or "")
            numeric_value = span.get("numeric_value")
            fact_kind = str(span.get("kind") or "numeric")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < len(prefix)
                or end <= start
                or text[start:end] != quote
                or not role
                or isinstance(numeric_value, bool)
                or not isinstance(numeric_value, int)
            ):
                return
            if fact_kind in {"policy_presence", "disclosure_presence"}:
                if numeric_value != 1 or not quote.strip():
                    return
                parsed_value = 1
            elif fact_kind == "numeric":
                display = quote.replace("$", "").strip()
                negative = display.startswith("(") and display.endswith(")")
                if negative:
                    display = display[1:-1]
                try:
                    parsed_value = parse_ixbrl_display_number(
                        display, scale=0, sign="-" if negative else ""
                    )
                except (TypeError, ValueError, ProvenanceError):
                    return
            else:
                return
            if parsed_value != numeric_value:
                return
            if role in record_metrics and record_metrics[role] != parsed_value:
                return
            record_metrics[role] = parsed_value
        if not record_metrics:
            return
        metrics[record_id] = record_metrics
        state.set("issuer_ir_metrics", metrics, eid, day)
    elif t == "issuer_ir_prior_filing_relation":
        source_id = str(p.get("record_id") or "")
        target_id = str(p.get("target_record_id") or "")
        relation_id = str(p.get("source_relation_id") or "")
        metrics = state.values.get("issuer_ir_metrics") or {}
        if (
            not source_id
            or not target_id
            or not relation_id
            or source_id not in metrics
            or target_id not in metrics
        ):
            return
        relations = dict(state.values.get("issuer_ir_relations") or {})
        relations[relation_id] = {
            "source_record_id": source_id,
            "target_record_id": target_id,
            "kind": str(p.get("relation_kind") or ""),
        }
        state.set("issuer_ir_relations", relations, eid, day)
    elif t == "issuer_ir_cross_year_answer":
        answer_key = str(p.get("answer_key") or "")
        record_ids = p.get("record_ids")
        required_roles = p.get("required_roles")
        record_role_extensions = p.get("record_role_extensions") or {}
        required_relation_ids = p.get("required_relation_ids")
        prerequisite_answer_key = str(p.get("prerequisite_answer_key") or "")
        prerequisite_record_ids = p.get("prerequisite_record_ids")
        prerequisite_required_roles = p.get("prerequisite_required_roles")
        prerequisite_record_role_extensions = p.get(
            "prerequisite_record_role_extensions"
        )
        metrics = state.values.get("issuer_ir_metrics") or {}
        relations = state.values.get("issuer_ir_relations") or {}
        if (
            not answer_key
            or not isinstance(record_ids, list)
            or not isinstance(required_roles, list)
            or not isinstance(record_role_extensions, dict)
            or any(
                str(record_id) not in {str(item) for item in record_ids}
                or not isinstance(roles, list)
                or any(not isinstance(role, str) or not role for role in roles)
                for record_id, roles in record_role_extensions.items()
            )
            or not isinstance(required_relation_ids, list)
            or any(
                str(relation_id) not in relations
                for relation_id in required_relation_ids
            )
            or any(
                any(
                    str(role) not in (metrics.get(str(record_id)) or {})
                    for role in required_roles
                )
                for record_id in record_ids
            )
            or any(
                any(
                    str(role) not in (metrics.get(str(record_id)) or {})
                    for role in record_role_extensions.get(str(record_id), [])
                )
                for record_id in record_ids
            )
        ):
            return

        def compose_answer(
            ids: list[Any],
            roles: list[Any],
            extensions: dict[str, list[Any]] | None = None,
        ) -> str | None:
            extensions = extensions or {}
            if any(
                any(
                    str(role) not in (metrics.get(str(record_id)) or {})
                    for role in [*roles, *extensions.get(str(record_id), [])]
                )
                for record_id in ids
            ):
                return None
            rows = []
            for record_id in ids:
                values = metrics[str(record_id)]
                record_roles = [*roles, *extensions.get(str(record_id), [])]
                year = str(record_id).rsplit(":", 1)[-1][:4]
                rows.append(
                    year
                    + ":"
                    + ",".join(
                        f"{str(role).upper()}={int(values[str(role)])}"
                        for role in record_roles
                    )
                )
            deltas = []
            for prior_id, current_id in pairwise(ids):
                prior = metrics[str(prior_id)]
                current = metrics[str(current_id)]
                years = (
                    f"{str(prior_id).rsplit(':', 1)[-1][:4]}→"
                    f"{str(current_id).rsplit(':', 1)[-1][:4]}"
                )
                deltas.append(
                    years
                    + ":"
                    + ",".join(
                        f"Δ{str(role).upper()}="
                        f"{int(current[str(role)]) - int(prior[str(role)])}"
                        for role in roles
                    )
                )
            return " | ".join([*rows, *deltas])

        if prerequisite_answer_key:
            if (
                not isinstance(prerequisite_record_ids, list)
                or not isinstance(prerequisite_required_roles, list)
                or not isinstance(prerequisite_record_role_extensions, dict)
            ):
                return
            prerequisite_answer = compose_answer(
                prerequisite_record_ids,
                prerequisite_required_roles,
                prerequisite_record_role_extensions,
            )
            if (
                prerequisite_answer is None
                or state.values.get(prerequisite_answer_key) != prerequisite_answer
            ):
                return
        composed_answer = compose_answer(
            record_ids, required_roles, record_role_extensions
        )
        if composed_answer is not None:
            state.set(answer_key, composed_answer, eid, day)
    elif t == "sec_financial_answer":
        record_id = str(p.get("record_id") or "")
        answer_key = str(p.get("answer_key") or "")
        if not record_id or not answer_key:
            return
        if str(p.get("compose") or "compute") == "copy":
            prior = state.values.get(str(p.get("prerequisite_answer_key") or ""))
            if not isinstance(prior, str) or not prior:
                return
            state.set(answer_key, prior, eid, day)
            return
        tier = str(p.get("control_tier") or "")
        xbrl = dict((state.values.get("sec_xbrl_facts") or {}).get(record_id) or {})
        certs = dict(
            (state.values.get("sec_certification_facts") or {}).get(record_id) or {}
        )
        required = p.get("required_roles")
        if (
            not record_id
            or not answer_key
            or not isinstance(required, list)
            or any(str(role) not in xbrl for role in required)
        ):
            return
        answer_family = str(p.get("answer_family") or "")
        if answer_family:
            if answer_family == "balance_certification":
                ceo = certs.get("ex_31_1") or []
                cfo = certs.get("ex_31_2") or []
                sox = list(certs.get("ex_32_1") or []) + list(
                    certs.get("ex_32_2") or []
                )
                if len(ceo) != 1 or len(cfo) != 1 or len(sox) != 2:
                    return
            state.set(answer_key, "READY", eid, day)
            return
        prerequisite_key = str(p.get("prerequisite_answer_key") or "")
        prior = state.values.get(prerequisite_key) if prerequisite_key else ""
        if prerequisite_key and not isinstance(prior, str):
            return
        parts: list[str] = []
        if tier == "16k":
            gap = (
                xbrl["product_revenue"]
                + xbrl["service_revenue"]
                - xbrl["total_revenue"]
            )
            parts.append(
                "MIX:"
                f"{xbrl['product_revenue']}+{xbrl['service_revenue']}="
                f"{xbrl['total_revenue']}|GAP:{gap}|"
                f"STATUS:{'PASS' if gap == 0 else 'FAIL'}"
            )
        elif tier == "32k":
            if not prior:
                return
            category_keys = sorted(
                (key for key in xbrl if key.startswith("category_")),
                key=lambda key: int(key.split("_", 1)[1]),
            )
            geo_keys = sorted(
                (key for key in xbrl if key.startswith("geo_")),
                key=lambda key: int(key.split("_", 1)[1]),
            )
            if not category_keys or not geo_keys:
                return
            category = "+".join(str(xbrl[key]) for key in category_keys)
            geo = "+".join(str(xbrl[key]) for key in geo_keys)
            total = xbrl["total_revenue"]
            parts.extend([str(prior), f"CAT:{category}={total}", f"GEO:{geo}={total}"])
        elif tier == "64k":
            if not prior:
                return
            current_geo_keys = sorted(
                (key for key in xbrl if key.startswith("geo_")),
                key=lambda key: int(key.split("_", 1)[1]),
            )
            prior_geo_keys = sorted(
                (key for key in xbrl if key.startswith("prior_geo_")),
                key=lambda key: int(key.rsplit("_", 1)[1]),
            )
            if (
                not current_geo_keys
                or len(current_geo_keys) != len(prior_geo_keys)
                or sum(xbrl[key] for key in prior_geo_keys)
                != xbrl["prior_total_revenue"]
            ):
                return
            geo_deltas = [
                xbrl[current] - xbrl[previous]
                for current, previous in zip(
                    current_geo_keys, prior_geo_keys, strict=True
                )
            ]
            max_geo_index = max(
                range(len(geo_deltas)), key=lambda index: abs(geo_deltas[index])
            )
            prior_geography = (
                "GEO_PRIOR:"
                + "+".join(str(xbrl[key]) for key in prior_geo_keys)
                + f"={xbrl['prior_total_revenue']}"
            )
            geography_yoy = (
                "GEO_YOY:"
                + ",".join(f"{delta:+d}" for delta in geo_deltas)
                + f"|MAX_INDEX:{max_geo_index}:{geo_deltas[max_geo_index]:+d}"
            )
            ceo = certs.get("ex_31_1") or []
            cfo = certs.get("ex_31_2") or []
            sox = list(certs.get("ex_32_1") or []) + list(certs.get("ex_32_2") or [])
            if len(ceo) != 1 or len(cfo) != 1 or len(sox) != 2:
                return
            certification_rows = [*ceo, *cfo, *sox]
            if not all(isinstance(row, dict) for row in certification_rows):
                return
            if (
                ceo[0].get("officer_title") != "Chief Executive Officer"
                or cfo[0].get("officer_title") != "Chief Financial Officer"
                or ceo[0].get("certification_kind") != "section_302"
                or cfo[0].get("certification_kind") != "section_302"
                or any(row.get("certification_kind") != "section_906" for row in sox)
                or any(
                    row.get("covered_form") != "Form 10-K" for row in certification_rows
                )
            ):
                return
            certification_dates = {
                str(row.get("certification_date") or "") for row in certification_rows
            }
            covered_periods = {str(row.get("covered_period_end") or "") for row in sox}
            if (
                len(certification_dates) != 1
                or "" in certification_dates
                or len(covered_periods) != 1
                or "" in covered_periods
            ):
                return
            ceo_name = str(ceo[0]["name"])
            cfo_name = str(cfo[0]["name"])
            sox_names = [str(row["name"]) for row in sox]
            if set(sox_names) != {ceo_name, cfo_name}:
                return
            certification_trace = (
                f"CERT:CEO:{ceo_name}||CFO:{cfo_name}||"
                f"SOX906:{sox_names[0]}+{sox_names[1]}||"
                f"CERT_SCOPE:Form 10-K@{next(iter(covered_periods))}#"
                f"{next(iter(certification_dates))}"
            )
            if "liabilities" in xbrl:
                balance = (
                    f"BS:{xbrl['liabilities']}+{xbrl['equity']}="
                    f"{xbrl['assets']}|{xbrl['liabilities_and_equity']}"
                )
            else:
                reconstructed = xbrl["assets"] - xbrl["equity"]
                if xbrl["assets"] != xbrl["liabilities_and_equity"]:
                    return
                balance = (
                    f"BS:{xbrl['assets']}-{xbrl['equity']}="
                    f"{reconstructed}|{xbrl['liabilities_and_equity']}"
                )
            parts.extend(
                [
                    str(prior),
                    prior_geography,
                    geography_yoy,
                    balance,
                    certification_trace,
                ]
            )
        elif tier == "128k":
            if not prior:
                return
            apple_needed = (
                "cfo",
                "cfi",
                "cff",
                "delta_cash",
                "federal_tax",
                "state_tax",
                "foreign_tax",
                "income_tax",
                "lease_current",
                "lease_noncurrent",
                "lease_total",
                "debt_current",
                "debt_noncurrent",
                "debt_total",
            )
            amazon_needed = (
                "cfo",
                "cfi",
                "cff",
                "fx",
                "delta_cash",
                "federal_tax",
                "state_tax",
                "foreign_tax",
                "income_tax",
                "lease_current",
                "lease_noncurrent",
                "lease_total",
                "oi_0",
                "oi_1",
                "oi_2",
                "oi_total",
            )
            apple_ok = all(key in xbrl for key in apple_needed) and (
                xbrl["cfo"] + xbrl["cfi"] + xbrl["cff"] == xbrl["delta_cash"]
                and xbrl["federal_tax"] + xbrl["state_tax"] + xbrl["foreign_tax"]
                == xbrl["income_tax"]
                and xbrl["lease_current"] + xbrl["lease_noncurrent"]
                == xbrl["lease_total"]
                and xbrl["debt_current"] + xbrl["debt_noncurrent"] == xbrl["debt_total"]
            )
            amazon_ok = all(key in xbrl for key in amazon_needed) and (
                xbrl["cfo"] + xbrl["cfi"] + xbrl["cff"] + xbrl.get("fx", 0)
                == xbrl["delta_cash"]
                and xbrl["federal_tax"] + xbrl["state_tax"] + xbrl["foreign_tax"]
                == xbrl["income_tax"]
                and xbrl["lease_current"] + xbrl["lease_noncurrent"]
                == xbrl["lease_total"]
                and xbrl["oi_0"] + xbrl["oi_1"] + xbrl["oi_2"] == xbrl["oi_total"]
            )
            if apple_ok:
                cash_terms = f"{xbrl['cfo']}+{xbrl['cfi']}+{xbrl['cff']}"
                extra = (
                    f"DEBT:{xbrl['debt_current']}+{xbrl['debt_noncurrent']}="
                    f"{xbrl['debt_total']}"
                )
            elif amazon_ok:
                cash_terms = f"{xbrl['cfo']}+{xbrl['cfi']}+{xbrl['cff']}+{xbrl['fx']}"
                extra = (
                    f"OI:{xbrl['oi_0']}+{xbrl['oi_1']}+{xbrl['oi_2']}="
                    f"{xbrl['oi_total']}"
                )
            else:
                return
            parts.extend(
                [
                    str(prior),
                    f"CF:{cash_terms}={xbrl['delta_cash']}",
                    (
                        f"TAX:{xbrl['federal_tax']}+{xbrl['state_tax']}+"
                        f"{xbrl['foreign_tax']}={xbrl['income_tax']}"
                    ),
                    (
                        f"LEASE:{xbrl['lease_current']}+{xbrl['lease_noncurrent']}="
                        f"{xbrl['lease_total']}"
                    ),
                    extra,
                ]
            )
        else:
            return
        state.set(answer_key, "||".join(parts), eid, day)
    elif t == "sign_contract":
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
