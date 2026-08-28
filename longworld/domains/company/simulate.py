from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict
from copy import deepcopy
from datetime import date, timedelta
from html import unescape
from typing import Any

from longworld.core.cascade import cascade_events
from longworld.core.filingworkflow import validated_sec_amendment_endpoints
from longworld.core.grounded import grounded_events
from longworld.core.provenance import ProvenanceError
from longworld.core.secvisible import (
    normalize_sec_visible_text,
    sec_visible_provenance_id,
)
from longworld.core.secxbrl import (
    derive_sec_filing_subsection,
    parse_sec_financial_program,
    split_sec_filing_section_by_facts,
)
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.company.events import (
    apply_event,
    check_preconditions,
    init_values,
)

_SEC_REPLAY_MAX_CHARS = 32_000
_SEC_SECTION_VIEW_PREFIX = "SEC source section\n"
_SEC_FACT_CORRIDOR_MARGIN = 10_600
_SEC_CANONICAL_SECTION_CACHE_MAX = 512
_SEC_CANONICAL_SECTION_CACHE: OrderedDict[
    tuple[Any, ...], dict[str, dict[str, Any]]
] = OrderedDict()
_SEC_SECTION_OFFSETS = {
    "item8_operations": 0,
    "note2_revenue": 8,
    "note13_segments": 8,
    "item8_balance_sheet": 401,
    "item8_cash_flow": 801,
    "note7_income_taxes": 801,
    "note8_leases": 801,
    "note9_debt": 801,
    "note4_leases": 801,
    "note9_income_taxes": 801,
    "note10_segment_oi": 801,
    "ex_31_1": 401,
    "ex_31_2": 401,
    "ex_32_1": 401,
    "ex_32_2": 401,
}
_SEC_64K_SECTIONS = (
    "note13_prior_segments",
    "item8_balance_sheet",
    "ex_31_1",
    "ex_31_2",
    "ex_32_1",
)
_SEC_128K_EXTRA_ROLES = (
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
    "debt_current",
    "debt_noncurrent",
    "debt_total",
    "oi_0",
    "oi_1",
    "oi_2",
    "oi_total",
)
_SEC_FINANCIAL_TIERS = (
    (
        "16k",
        ((5, "compute", "annual product and service mix"),),
        (
            "item8_operations_product_revenue",
            "item8_operations_service_revenue",
            "item8_operations_total_revenue",
        ),
    ),
    (
        "32k",
        ((400, "compute", "category mix and geographic totals"),),
        ("note2_revenue", "note13_segments"),
    ),
    (
        "64k",
        ((800, "compute", "balance-sheet identity and officer certifications"),),
        _SEC_64K_SECTIONS,
    ),
    (
        "128k",
        (
            (
                1_600,
                "compute",
                "cash-flow identity and remaining note reconciliations",
            ),
        ),
        (),
    ),
)


def _sec_tier_roles(program, control_tier: str) -> tuple[str, ...]:
    mix = ("product_revenue", "service_revenue", "total_revenue")
    cats = tuple(f"category_{index}" for index in range(program.n_category))
    geos = tuple(f"geo_{index}" for index in range(program.n_geo))
    if control_tier == "16k":
        return mix
    if control_tier == "32k":
        return mix + cats + geos
    balance = (
        ("assets", "liabilities", "equity", "liabilities_and_equity")
        if program.require_liabilities
        else ("assets", "equity", "liabilities_and_equity")
    )
    core = mix + cats + geos + balance
    if control_tier in {"64k", "128k"}:
        core += ("prior_total_revenue",) + tuple(
            f"prior_geo_{index}" for index in range(program.n_geo)
        )
    if control_tier != "128k":
        return core
    return core + tuple(role for role in _SEC_128K_EXTRA_ROLES if role in program.roles)


def _date(start: date, offset: int) -> date:
    return start + timedelta(days=offset)


def _sec_section_cache_key(workflow, record) -> tuple[Any, ...] | None:
    source_sha256 = hashlib.sha256(record.text.encode()).hexdigest()
    if source_sha256 != record.source_sha256 or source_sha256 != record.text_sha256:
        return None
    return (
        workflow.workflow_id,
        workflow.component_digest,
        record.record_id,
        source_sha256,
        record.provenance_id,
        str(record.source_origin),
        record.source_family,
        record.source_url,
        record.retrieval_url,
        str(record.occurred_at),
        tuple(record.attributes),
    )


def _section_fact_spans(
    source_text: str,
    program,
    *,
    visible,
    char_start: int,
    char_end: int,
    shift: int = 0,
) -> list[dict[str, Any]]:
    def readable_quote(raw: str) -> str:
        return " ".join(unescape(raw).split())

    def visible_interval(
        source_start: int, source_end: int, expected: str
    ) -> tuple[int, int]:
        ranges = visible.visible_ranges(source_start, source_end)
        if len(ranges) != 1:
            raise ProvenanceError("SEC fact does not have one readable source span")
        start, end = ranges[0]
        if visible.text[start:end] != expected:
            raise ProvenanceError("SEC readable fact does not match source evidence")
        return start + shift, end + shift

    spans: list[dict[str, Any]] = []
    for role, fact in program.roles.items():
        if not (char_start <= fact.char_start < fact.char_end <= char_end):
            continue
        source_evidence_quote = source_text[fact.char_start : fact.char_end]
        evidence_quote = readable_quote(source_evidence_quote)
        start, end = visible_interval(fact.char_start, fact.char_end, evidence_quote)
        spans.append(
            {
                "kind": "xbrl",
                "role": role,
                "fact_id": fact.fact_id,
                "evidence_quote": evidence_quote,
                "source_evidence_quote": source_evidence_quote,
                "char_start": start,
                "char_end": end,
                "source_char_start": fact.char_start,
                "source_char_end": fact.char_end,
                "scale": fact.scale,
                "sign": fact.sign,
                "numeric_value": fact.numeric_value,
            }
        )
    section_id = next(
        (
            section.section_id
            for section in program.sections
            if section.char_start == char_start and section.char_end == char_end
        ),
        "",
    )
    for fact in program.certifications.get(section_id, ()):
        source_name = source_text[fact.char_start : fact.char_end]
        visible_name = readable_quote(source_name)
        start, end = visible_interval(fact.char_start, fact.char_end, visible_name)
        raw_spans = {
            "officer_title": (
                fact.officer_title,
                fact.officer_title_char_start,
                fact.officer_title_char_end,
            ),
            "certification_kind": (
                fact.certification_kind,
                fact.kind_char_start,
                fact.kind_char_end,
            ),
            "covered_form": (
                fact.covered_form,
                fact.form_char_start,
                fact.form_char_end,
            ),
            "certification_date": (
                fact.certification_date,
                fact.date_char_start,
                fact.date_char_end,
            ),
        }
        if fact.period_char_start and fact.period_char_end:
            raw_spans["covered_period_end"] = (
                fact.covered_period_end,
                fact.period_char_start,
                fact.period_char_end,
            )
        evidence_spans = {}
        for field, (value, field_start, field_end) in raw_spans.items():
            source_evidence_quote = source_text[field_start:field_end]
            evidence_quote = readable_quote(source_evidence_quote)
            visible_start, visible_end = visible_interval(
                field_start, field_end, evidence_quote
            )
            evidence_spans[field] = {
                "value": value,
                "evidence_quote": evidence_quote,
                "source_evidence_quote": source_evidence_quote,
                "char_start": visible_start,
                "char_end": visible_end,
                "source_char_start": field_start,
                "source_char_end": field_end,
            }
        spans.append(
            {
                "kind": "certification",
                "role": "officer_name",
                "evidence_quote": visible_name,
                "source_evidence_quote": source_name,
                "char_start": start,
                "char_end": end,
                "source_char_start": fact.char_start,
                "source_char_end": fact.char_end,
                "officer_title": fact.officer_title,
                "certification_kind": fact.certification_kind,
                "covered_form": fact.covered_form,
                "covered_period_end": fact.covered_period_end,
                "certification_date": fact.certification_date,
                "evidence_spans": evidence_spans,
            }
        )
    return spans


def _sec_financial_events(
    *,
    workflow,
    record,
    prefix: str,
    workflow_index: int,
    record_index: int,
    filing_day: date,
) -> list[Event]:
    full_text = record.text
    parent_hash = hashlib.sha256(full_text.encode()).hexdigest()
    if parent_hash != record.text_sha256:
        return []
    try:
        program = parse_sec_financial_program(
            full_text,
            parent_hash,
            report_date=record.attribute("report_date"),
        )
    except ProvenanceError:
        return []
    events: list[Event] = []
    section_ids: dict[str, str] = {}
    for section in program.sections:
        views = [(section, "", "")]
        if section.section_id == "item8_operations":
            roles = (
                "product_revenue",
                "service_revenue",
                "total_revenue",
            )
            try:
                corridor = derive_sec_filing_subsection(
                    full_text,
                    section,
                    section_id="item8_operations_program_corridor",
                    char_start=section.char_start,
                    char_end=min(
                        section.char_end,
                        program.roles["total_revenue"].char_end + 40_000,
                    ),
                )
                slices = split_sec_filing_section_by_facts(
                    full_text,
                    corridor,
                    tuple(program.roles[role] for role in roles),
                )
            except ProvenanceError:
                return []
            views = [
                (view, role, f"item8_operations_{role}")
                for view, role in zip(slices, roles, strict=True)
            ]
        elif section.section_id == "note2_revenue":
            first_category = program.roles["category_0"]
            last_category = program.roles[f"category_{program.n_category - 1}"]
            corridor_start = max(
                section.char_start,
                first_category.char_start - _SEC_FACT_CORRIDOR_MARGIN,
            )
            corridor_end = min(
                section.char_end,
                last_category.char_end + _SEC_FACT_CORRIDOR_MARGIN,
            )
            current_view = derive_sec_filing_subsection(
                full_text,
                section,
                section_id="note2_revenue_current_mix",
                char_start=corridor_start,
                char_end=corridor_end,
            )
            views = []
            if corridor_start > section.char_start:
                preamble = derive_sec_filing_subsection(
                    full_text,
                    section,
                    section_id="note2_revenue_preamble",
                    char_start=section.char_start,
                    char_end=corridor_start,
                )
                views.append((preamble, "", ""))
            views.append((current_view, "", "note2_revenue"))
            if corridor_end < section.char_end:
                tail = derive_sec_filing_subsection(
                    full_text,
                    section,
                    section_id="note2_revenue_tail",
                    char_start=corridor_end,
                    char_end=section.char_end,
                )
                views.append((tail, "", ""))
        elif section.section_id == "note13_segments":
            first_current = program.roles["geo_0"]
            current = program.roles[f"geo_{program.n_geo - 1}"]
            current_start = max(
                section.char_start,
                first_current.char_start - _SEC_FACT_CORRIDOR_MARGIN,
            )
            current_end = min(
                section.char_end,
                current.char_end + _SEC_FACT_CORRIDOR_MARGIN,
            )
            current_view = derive_sec_filing_subsection(
                full_text,
                section,
                section_id="note13_current_geography",
                char_start=current_start,
                char_end=current_end,
            )
            prior_view = derive_sec_filing_subsection(
                full_text,
                section,
                section_id="note13_prior_geography",
                char_start=current_end,
                char_end=section.char_end,
            )
            views = []
            if current_start > section.char_start:
                preamble = derive_sec_filing_subsection(
                    full_text,
                    section,
                    section_id="note13_geography_preamble",
                    char_start=section.char_start,
                    char_end=current_start,
                )
                views.append((preamble, "", ""))
            views.extend(
                [
                    (current_view, "", "note13_segments"),
                    (prior_view, "", "note13_prior_segments"),
                ]
            )
        elif section.section_id == "item8_balance_sheet":
            core_start = max(
                section.char_start,
                program.roles["assets"].char_start - 14_000,
            )
            preamble = derive_sec_filing_subsection(
                full_text,
                section,
                section_id="item8_balance_sheet_preamble",
                char_start=section.char_start,
                char_end=core_start,
            )
            core = derive_sec_filing_subsection(
                full_text,
                section,
                section_id="item8_balance_sheet_core",
                char_start=core_start,
                char_end=section.char_end,
            )
            views = [
                (preamble, "", ""),
                (core, "", "item8_balance_sheet"),
            ]
        for view, financial_role, section_alias in views:
            view_id = view.section_id
            char_start = view.char_start
            char_end = view.char_end
            try:
                visible = normalize_sec_visible_text(
                    full_text,
                    char_start=char_start,
                    char_end=char_end,
                    context_char_start=section.char_start,
                    context_char_end=section.char_end,
                )
                fact_spans = _section_fact_spans(
                    full_text,
                    program,
                    visible=visible,
                    char_start=char_start,
                    char_end=char_end,
                    shift=len(_SEC_SECTION_VIEW_PREFIX),
                )
            except (ProvenanceError, ValueError):
                continue
            if not visible.text:
                continue
            if section_alias == "note13_segments":
                fact_spans = [
                    span
                    for span in fact_spans
                    if not str(span.get("role") or "").startswith("prior_geo_")
                ]
            elif section_alias == "note13_prior_segments":
                fact_spans = [
                    span
                    for span in fact_spans
                    if str(span.get("role") or "").startswith("prior_geo_")
                ]
            section_text = _SEC_SECTION_VIEW_PREFIX + visible.text
            event_id = f"{prefix}.sec_section_{view_id}_{workflow_index}_{record_index}"
            section_ids[view_id] = event_id
            if section_alias and fact_spans:
                section_ids[section_alias] = event_id
            ground_values = (
                [
                    str(span["evidence_quote"])
                    for span in fact_spans
                    if span.get("kind") == "xbrl"
                ]
                if view.section_id != section.section_id
                else [dict(program.section_ground)[section.section_id]]
            )
            visible_provenance_id = sec_visible_provenance_id(
                parent_provenance_id=view.provenance_id,
                raw_section_sha256=view.section_sha256,
                visible_text_sha256=visible.text_sha256,
                revision=visible.revision,
            )
            events.append(
                Event(
                    id=event_id,
                    type="sec_source_section",
                    time=filing_day
                    + timedelta(days=_SEC_SECTION_OFFSETS[section.section_id]),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": record.record_id,
                        "section_id": view_id,
                        "parent_section_id": section.section_id,
                        "financial_role": financial_role,
                        "section_alias": section_alias,
                        "component_type": section.component_type,
                        "text": section_text,
                        "text_sha256": hashlib.sha256(
                            section_text.encode()
                        ).hexdigest(),
                        "source_sha256": record.source_sha256,
                        "parent_source_sha256": parent_hash,
                        "section_sha256": visible.text_sha256,
                        "raw_section_sha256": view.section_sha256,
                        "visible_text_revision": visible.revision,
                        "component_sha256": section.component_sha256,
                        "parent_char_start": char_start,
                        "parent_char_end": char_end,
                        "context_char_start": visible.context_char_start,
                        "context_char_end": visible.context_char_end,
                        "provenance_id": visible_provenance_id,
                        "parent_provenance_id": view.provenance_id,
                        "provenance_operation": visible.revision,
                        "source_origin": "real_derived",
                        "source_family": record.source_family,
                        "source_url": record.source_url,
                        "retrieval_url": record.retrieval_url,
                        "fact_spans": fact_spans,
                        "ground_values": ground_values,
                    },
                    visibility=[event_id],
                )
            )
    prior_id = ""
    prior_key = ""
    compute_ids: dict[str, str] = {}
    compute_keys: dict[str, str] = {}
    for control_tier, rungs, tier_sections in _SEC_FINANCIAL_TIERS:
        predecessor = {
            "32k": "16k",
            "64k": "32k",
            "128k": "64k",
        }.get(control_tier)
        if predecessor and predecessor not in compute_ids:
            continue
        needed_sections: tuple[str, ...] = tier_sections
        if control_tier == "128k":
            extras = program.extra_128k_sections
            if not extras or any(name not in section_ids for name in extras):
                continue
            needed_sections = extras
        if any(name not in section_ids for name in needed_sections):
            continue
        selected_sections = list(needed_sections)
        if control_tier in {"64k", "128k"} and "ex_32_2" in section_ids:
            selected_sections.append("ex_32_2")
        for rung_index, (offset, compose, stage) in enumerate(rungs):
            event_id = (
                f"{prefix}.sec_financial_{control_tier}_{compose}_{rung_index}_"
                f"{workflow_index}_{record_index}"
            )
            parents = [section_ids[name] for name in selected_sections]
            if prior_id:
                parents.append(prior_id)
            answer_key = (
                f"sec_financial_reconstruction:{record.record_id}:{control_tier}"
            )
            required_roles = list(_sec_tier_roles(program, control_tier))
            relation_kinds = {
                parent: ("extends" if parent == prior_id else "reads_section")
                for parent in parents
            }
            events.append(
                Event(
                    id=event_id,
                    type="sec_financial_answer",
                    time=filing_day + timedelta(days=offset),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": record.record_id,
                        "control_tier": control_tier,
                        "control_stage": stage,
                        "compose": compose,
                        "answer_key": answer_key,
                        "prerequisite_answer_key": prior_key,
                        "required_roles": required_roles,
                        "section_event_ids": [
                            section_ids[name] for name in selected_sections
                        ],
                        "ground_values": ["financial-reconstructed", stage],
                    },
                    visibility=[event_id],
                    causal_inputs=parents,
                    required_inputs=parents,
                    relation_kinds=relation_kinds,
                )
            )
            prior_id = event_id
            prior_key = answer_key
            compute_ids[control_tier] = event_id
            compute_keys[control_tier] = answer_key
    cache_key = _sec_section_cache_key(workflow, record)
    if cache_key is not None:
        section_events = [
            event for event in events if event.type == "sec_source_section"
        ]
        canonical_section_ids = [
            str(event.params["section_id"]) for event in section_events
        ]
        if len(canonical_section_ids) != len(set(canonical_section_ids)):
            return []
        _SEC_CANONICAL_SECTION_CACHE[cache_key] = {
            str(event.params["section_id"]): {
                "type": event.type,
                "time": event.time,
                "params": deepcopy(event.params),
                "preconditions": list(event.preconditions),
                "causal_inputs": list(event.causal_inputs),
                "required_inputs": list(event.required_inputs),
                "relation_kinds": dict(event.relation_kinds),
                "skipped": event.skipped,
                "skip_reason": event.skip_reason,
            }
            for event in section_events
        }
        _SEC_CANONICAL_SECTION_CACHE.move_to_end(cache_key)
        while len(_SEC_CANONICAL_SECTION_CACHE) > _SEC_CANONICAL_SECTION_CACHE_MAX:
            _SEC_CANONICAL_SECTION_CACHE.popitem(last=False)
    return events


def canonical_sec_source_section_envelope(
    *, workflow, record, section_id: str
) -> dict[str, Any] | None:
    """Return the raw-source-regenerated envelope for one SEC section view."""

    cache_key = _sec_section_cache_key(workflow, record)
    if cache_key is None:
        return None
    sections = _SEC_CANONICAL_SECTION_CACHE.get(cache_key)
    if sections is not None:
        _SEC_CANONICAL_SECTION_CACHE.move_to_end(cache_key)
    if sections is None:
        try:
            filing_day = date.fromisoformat(str(record.occurred_at)[:10])
        except (AttributeError, TypeError, ValueError):
            return None
        _sec_financial_events(
            workflow=workflow,
            record=record,
            prefix="canonical",
            workflow_index=0,
            record_index=0,
            filing_day=filing_day,
        )
        sections = _SEC_CANONICAL_SECTION_CACHE.get(cache_key)
    if sections is None or section_id not in sections:
        return None
    return deepcopy(sections[section_id])


def _source_workflow_events(project: dict[str, Any], prefix: str) -> list[Event]:
    events: list[Event] = []
    source_workflows = project.get("source_workflows") or []
    record_id_counts = Counter(
        record.record_id for workflow in source_workflows for record in workflow.records
    )
    for workflow_index, workflow in enumerate(source_workflows):
        for record_index, record in enumerate(workflow.records):
            if record_id_counts[record.record_id] != 1:
                continue
            required_fields = {"accession", "form", "filing_date", "report_date"}
            facts_by_field = {
                field: [fact for fact in record.facts if fact.field == field]
                for field in required_fields
            }
            if any(len(facts) != 1 for facts in facts_by_field.values()):
                continue
            source_id = f"{prefix}.sec_filing_{workflow_index}_{record_index}"
            policy_id = f"{prefix}.sec_policy_{workflow_index}_{record_index}"
            approval_id = f"{prefix}.sec_approval_{workflow_index}_{record_index}"
            filing_day = date.fromisoformat(record.occurred_at[:10])
            max_days_after_report = 60
            fact_spans = [
                {
                    "fact_id": fact.fact_id,
                    "field": fact.field,
                    "evidence_quote": fact.evidence_quote,
                    "char_start": fact.char_start,
                    "char_end": fact.char_end,
                    "value_offset": fact.value_offset,
                    "value_length": len(fact.value),
                }
                for field in sorted(required_fields)
                for fact in facts_by_field[field]
            ]
            source_text = record.text
            source_origin = record.source_origin.value
            provenance_id = record.provenance_id
            parent_provenance_id = ""
            if len(source_text) > _SEC_REPLAY_MAX_CHARS:
                first_fact = min(int(fact["char_start"]) for fact in fact_spans)
                last_fact = max(int(fact["char_end"]) for fact in fact_spans)
                if last_fact - first_fact > _SEC_REPLAY_MAX_CHARS:
                    continue
                corridor_start = max(0, last_fact - _SEC_REPLAY_MAX_CHARS)
                corridor_start = min(corridor_start, first_fact)
                corridor_end = min(
                    len(source_text), corridor_start + _SEC_REPLAY_MAX_CHARS
                )
                source_text = source_text[corridor_start:corridor_end]
                fact_spans = [
                    {
                        **fact,
                        "char_start": int(fact["char_start"]) - corridor_start,
                        "char_end": int(fact["char_end"]) - corridor_start,
                    }
                    for fact in fact_spans
                ]
                excerpt_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
                parent_provenance_id = record.provenance_id
                provenance_id = (
                    "derived-sha256:"
                    + hashlib.sha256(
                        (
                            f"sec_evidence_corridor|{record.provenance_id}|"
                            f"{corridor_start}|{corridor_end}|{excerpt_sha256}"
                        ).encode()
                    ).hexdigest()
                )
                source_origin = "real_derived"
            ratification_events: list[Event] = []
            ratification_parent = approval_id
            for control_tier, control_stage, offset in (
                ("16k", "initial publication authorization", 180),
                ("32k", "interim publication reaffirmation", 1_500),
                ("64k", "final archive ratification", 3_000),
            ):
                ratification_id = (
                    f"{prefix}.sec_ratification_{control_tier}_"
                    f"{workflow_index}_{record_index}"
                )
                answer_key = f"sec_filing_eligibility:{record.record_id}:{control_tier}"
                ratification_events.append(
                    Event(
                        id=ratification_id,
                        type="sec_filing_publication_ratification",
                        time=filing_day + timedelta(days=offset),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "record_id": record.record_id,
                            "source_approval_event_id": approval_id,
                            "prerequisite_event_id": ratification_parent,
                            "prerequisite_answer_key": (
                                str(ratification_events[-1].params["answer_key"])
                                if ratification_events
                                else ""
                            ),
                            "answer_key": answer_key,
                            "control_tier": control_tier,
                            "control_stage": control_stage,
                            "ratified": True,
                            "ground_values": [
                                "publication-ratified",
                                control_stage,
                            ],
                        },
                        visibility=[ratification_id],
                        causal_inputs=[ratification_parent],
                        required_inputs=[ratification_parent],
                        relation_kinds={ratification_parent: "ratifies"},
                    )
                )
                ratification_parent = ratification_id
            events.extend(
                [
                    Event(
                        id=source_id,
                        type="sec_filing",
                        time=filing_day,
                        params={
                            "workflow_id": workflow.workflow_id,
                            "record_id": record.record_id,
                            "text": source_text,
                            "text_sha256": hashlib.sha256(
                                source_text.encode()
                            ).hexdigest(),
                            "source_sha256": record.source_sha256,
                            "provenance_id": provenance_id,
                            "parent_provenance_id": parent_provenance_id,
                            "provenance_operation": (
                                "sec_evidence_corridor" if parent_provenance_id else ""
                            ),
                            "source_origin": source_origin,
                            "source_family": record.source_family,
                            "source_url": record.source_url,
                            "retrieval_url": record.retrieval_url,
                            "fact_spans": fact_spans,
                            "ground_values": [
                                facts_by_field[field][0].value
                                for field in sorted(required_fields)
                            ],
                        },
                        visibility=[
                            f"{prefix}.sec_filing_{workflow_index}_{record_index}"
                        ],
                    ),
                    Event(
                        id=policy_id,
                        type="sec_filing_eligibility_policy",
                        time=filing_day + timedelta(days=1),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "record_id": record.record_id,
                            "source_record_event_id": source_id,
                            "accepted_forms": ["10-K", "10-K/A"],
                            "max_days_after_report": max_days_after_report,
                            "ground_values": [
                                "10-K",
                                "10-K/A",
                                str(max_days_after_report),
                            ],
                        },
                        visibility=[
                            f"{prefix}.sec_policy_{workflow_index}_{record_index}"
                        ],
                        causal_inputs=[source_id],
                        required_inputs=[source_id],
                        relation_kinds={source_id: "evaluates"},
                    ),
                    Event(
                        id=approval_id,
                        type="sec_filing_approval",
                        time=filing_day + timedelta(days=2),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "record_id": record.record_id,
                            "policy_event_id": policy_id,
                            "approved": True,
                            "ground_values": ["committee-approved"],
                        },
                        visibility=[
                            f"{prefix}.sec_approval_{workflow_index}_{record_index}"
                        ],
                        causal_inputs=[policy_id],
                        required_inputs=[policy_id],
                        relation_kinds={policy_id: "authorizes"},
                    ),
                    *ratification_events,
                ]
            )
            events.extend(
                _sec_financial_events(
                    workflow=workflow,
                    record=record,
                    prefix=prefix,
                    workflow_index=workflow_index,
                    record_index=record_index,
                    filing_day=filing_day,
                )
            )
        record_indexes = {
            record.record_id: record_index
            for record_index, record in enumerate(workflow.records)
        }
        for relation_index, relation in enumerate(workflow.relations):
            endpoints = validated_sec_amendment_endpoints(workflow, relation)
            if endpoints is None:
                continue
            amendment, original = endpoints
            if any(
                record_id_counts[record.record_id] != 1
                for record in (amendment, original)
            ):
                continue
            amendment_source_id = (
                f"{prefix}.sec_filing_{workflow_index}_"
                f"{record_indexes[amendment.record_id]}"
            )
            original_source_id = (
                f"{prefix}.sec_filing_{workflow_index}_"
                f"{record_indexes[original.record_id]}"
            )
            resolution_id = (
                f"{prefix}.sec_amendment_resolution_{workflow_index}_{relation_index}"
            )
            relation_day = date.fromisoformat(amendment.occurred_at[:10])
            events.append(
                Event(
                    id=resolution_id,
                    type="sec_amendment_resolution",
                    time=relation_day + timedelta(days=3),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": amendment.record_id,
                        "target_record_id": original.record_id,
                        "source_relation_id": relation.relation_id,
                        "resolution_kind": "amends_report",
                        "answer_key": (
                            f"sec_amendment_resolution:{relation.relation_id}"
                        ),
                        "control_stage": "amendment graph resolution",
                        "ground_values": ["relation-evaluated"],
                    },
                    visibility=[resolution_id],
                    causal_inputs=[original_source_id, amendment_source_id],
                    required_inputs=[original_source_id, amendment_source_id],
                    relation_kinds={
                        original_source_id: "amends_target",
                        amendment_source_id: "amendment_source",
                    },
                )
            )
    return events


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
    evs.extend(_source_workflow_events(project, prefix))
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
