from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any

from longworld.core.cascade import cascade_events
from longworld.core.grounded import grounded_events
from longworld.core.groundedspan import normalize_fact_value
from longworld.core.provenance import ProvenanceError
from longworld.core.sourceworkflow import PAPER_SOURCE_KIND, WIKIMEDIA_SOURCE_KIND
from longworld.core.wikiparse import (
    WIKI_ENTITY_VIEW_PREFIX,
    WIKI_FACT_PARSER_REVISION_V2,
    WIKI_SECTION_REVISION,
    WIKI_SECTION_VIEW_PREFIX,
    parse_wiki_claim_program,
)
from longworld.core.world import Event, SimulatedWorld, WorldSimulator
from longworld.domains.researchlab.events import (
    apply_event,
    check_preconditions,
    init_values,
)

_WIKI_SECTION_OFFSETS = {
    "early_work": 0,
    "commemoration": 8,
    "wikidata_entity": 8,
    "popular_culture": 401,
    "appendix_rest": 402,
}
_WIKI_CLAIM_TIERS = (
    (
        "16k",
        ((5, "compute", "birth-date reconstruction"),),
        ("early_work",),
    ),
    (
        "32k",
        ((400, "compute", "commemoration and entity resolution"),),
        ("early_work", "commemoration", "wikidata_entity"),
    ),
    (
        "64k",
        ((800, "compute", "popular-culture reconstruction"),),
        ("early_work", "commemoration", "popular_culture", "wikidata_entity"),
    ),
)
ARXIV_SOURCE_ENVELOPE_REVISION = "researchlab-arxiv-source-envelope-v1"
_WIKI_LEGACY_ROLE_TAGS = {
    "born": "BORN",
    "early_career": "CAREER",
    "revolutionary_committee": "COMMITTEE",
    "early_transition": "EARLY",
    "commemoration": "COMM",
    "entity": "ENTITY",
    "popular_culture": "POP",
}


def _date(start: date, months: int, extra_days: int = 0) -> date:
    # Approximate month steps without dateutil.
    return start + timedelta(days=30 * months + extra_days)


def _semantic_arxiv_body(
    record: Any,
    *,
    included_basenames: frozenset[str] | None = None,
    bind_file_spans: bool = False,
) -> tuple[str, str, list[str], list[dict[str, Any]]]:
    """Preserve semantic LaTeX while excluding non-body preamble formatting."""
    try:
        payload = json.loads(record.text)
    except json.JSONDecodeError as error:
        raise ValueError("arXiv source record is not canonical JSON") from error
    raw_sources = payload.get("latex_sources") if isinstance(payload, dict) else None
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("arXiv source record has no LaTeX body")
    selected: list[tuple[str, str]] = []
    paths: set[str] = set()
    selected_basenames: set[str] = set()
    for source in raw_sources:
        if not isinstance(source, dict) or set(source) not in (
            {"path", "text"},
            {"path", "text", "sha256"},
        ):
            raise ValueError("arXiv LaTeX source entry is invalid")
        path = str(source["path"])
        text = str(source["text"])
        if not path or path in paths or not text:
            raise ValueError("arXiv LaTeX source entry is invalid")
        paths.add(path)
        basename = path.rsplit("/", 1)[-1]
        if basename == "preamble.tex" or (
            included_basenames is not None and basename not in included_basenames
        ):
            continue
        if basename in selected_basenames:
            raise ValueError("arXiv selected source basename is not unique")
        selected_basenames.add(basename)
        selected.append((path, text))
    if not selected:
        raise ValueError("arXiv semantic LaTeX body is empty")
    revision_id = record.attribute("revision_id")
    if not revision_id:
        raise ValueError("arXiv source record has no revision identity")
    header = (
        f"% arXiv manuscript revision {revision_id}\n"
        f"% arXiv submitted_at {record.occurred_at}\n"
    )
    body = header + "\n\n".join(text for _path, text in selected)
    source_file_spans: list[dict[str, Any]] = []
    cursor = len(header)
    for index, (path, text) in enumerate(selected):
        if index:
            cursor += 2
        end = cursor + len(text)
        source_file_spans.append(
            {
                "path": path,
                "basename": path.rsplit("/", 1)[-1],
                "char_start": cursor,
                "char_end": end,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        cursor = end
    if cursor != len(body):
        raise ValueError("arXiv selected source spans do not cover the body")
    text_sha256 = hashlib.sha256(body.encode()).hexdigest()
    excluded_paths = sorted(
        path
        for path in paths
        if path.rsplit("/", 1)[-1] == "preamble.tex"
        or (
            included_basenames is not None
            and path.rsplit("/", 1)[-1] not in included_basenames
        )
    )
    operation = (
        "arxiv_semantic_latex_body_v2"
        if bind_file_spans
        else "arxiv_semantic_latex_body_v1"
    )
    digest_payload = {
        "operation": operation,
        "parent_provenance_id": record.provenance_id,
        "excluded_paths": excluded_paths,
        "text_sha256": text_sha256,
    }
    if bind_file_spans:
        digest_payload.update(
            {
                "source_file_spans": source_file_spans,
                "source_view_basenames": sorted(selected_basenames),
            }
        )
    provenance = hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return body, f"derived-sha256:{provenance}", excluded_paths, source_file_spans


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _arxiv_source_envelope(
    *,
    workflow: Any,
    record: Any,
    text_sha256: str,
    provenance_id: str,
    provenance_operation: str = "arxiv_semantic_latex_body_v1",
    source_file_spans: list[dict[str, Any]] | None = None,
    source_view_basenames: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "revision": ARXIV_SOURCE_ENVELOPE_REVISION,
        "workflow_id": workflow.workflow_id,
        "record_id": record.record_id,
        "revision_id": record.attribute("revision_id"),
        "occurred_at": record.occurred_at,
        "source_sha256": record.source_sha256,
        "parent_provenance_id": record.provenance_id,
        "source_url": record.source_url,
        "retrieval_url": record.retrieval_url,
        "text_sha256": text_sha256,
        "provenance_id": provenance_id,
    }
    if provenance_operation == "arxiv_semantic_latex_body_v2":
        payload["source_file_spans"] = list(source_file_spans or [])
        payload["source_view_basenames"] = list(source_view_basenames or [])
    return payload


def _grounded_fact(
    *, source_id: str, text: str, fact_id: str, quote: str, char_start: int
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "source_id": source_id,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "quote": quote,
        "char_start": char_start,
        "char_end": char_start + len(quote),
        "normalized_quote": normalize_fact_value(quote),
    }


def _grounded_source(
    *, source_id: str, text: str, facts: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "visible_text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "facts": facts,
        "relations": [],
    }


def _wiki_section_fact_spans(
    section: Any, *, shift: int, source_id: str
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for fact in section.facts:
        span = fact.to_span(shift=shift)
        span["fact_id"] = f"{source_id}:{fact.role}"
        spans.append(span)
    return spans


def _wiki_grounded_source(
    *, event_id: str, section: Any, section_text: str, prefix_text: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spans = _wiki_section_fact_spans(
        section, shift=len(prefix_text), source_id=event_id
    )
    ground_start = section_text.index(section.ground_value)
    facts = [
        _grounded_fact(
            source_id=event_id,
            text=section_text,
            fact_id=f"{event_id}:ground",
            quote=section.ground_value,
            char_start=ground_start,
        ),
        *(
            _grounded_fact(
                source_id=event_id,
                text=section_text,
                fact_id=str(span["fact_id"]),
                quote=str(span["evidence_quote"]),
                char_start=int(span["char_start"]),
            )
            for span in spans
        ),
    ]
    return _grounded_source(source_id=event_id, text=section_text, facts=facts), spans


def _wikipedia_source_workflow_events(
    workflow: Any, prefix: str, workflow_index: int
) -> list[Event]:
    wiki_records = [
        record for record in workflow.records if record.kind == "wikipedia_revision"
    ]
    entity_records = [
        record
        for record in workflow.records
        if record.kind == "wikidata_entity_revision"
    ]
    if len(wiki_records) != 2 or len(entity_records) != 1:
        return []
    later = max(wiki_records, key=lambda record: (record.occurred_at, record.record_id))
    entity = entity_records[0]
    wiki_hash = hashlib.sha256(later.text.encode()).hexdigest()
    entity_hash = hashlib.sha256(entity.text.encode()).hexdigest()
    if wiki_hash != later.text_sha256 or entity_hash != entity.text_sha256:
        return []
    try:
        program = parse_wiki_claim_program(
            wikipedia_text=later.text,
            entity_text=entity.text,
            wikipedia_parent_hash=wiki_hash,
            entity_parent_hash=entity_hash,
        )
    except ProvenanceError:
        return []
    semantic_tags = program.fact_parser_revision == WIKI_FACT_PARSER_REVISION_V2
    later_day = date.fromisoformat(later.occurred_at[:10])
    events: list[Event] = []
    section_ids: dict[str, str] = {}
    early_section_names = [
        section.section_id
        for section in program.sections
        if section.section_id.startswith("early_")
    ]
    early_offsets = {name: index for index, name in enumerate(early_section_names)}
    for section in program.sections:
        record = entity if section.section_id == "wikidata_entity" else later
        prefix_text = (
            WIKI_ENTITY_VIEW_PREFIX
            if section.section_id == "wikidata_entity"
            else WIKI_SECTION_VIEW_PREFIX
        )
        section_text = prefix_text + section.wikitext
        event_id = (
            f"{prefix}.wiki_section_{section.section_id}_{workflow_index}_"
            f"{later.record_id}"
        )
        section_ids[section.section_id] = event_id
        grounded_source, fact_spans = _wiki_grounded_source(
            event_id=event_id,
            section=section,
            section_text=section_text,
            prefix_text=prefix_text,
        )
        section_offset = _WIKI_SECTION_OFFSETS.get(
            section.section_id, early_offsets.get(section.section_id)
        )
        if section_offset is None:
            return []
        events.append(
            Event(
                id=event_id,
                type="wiki_source_section",
                time=later_day + timedelta(days=section_offset),
                params={
                    "workflow_id": workflow.workflow_id,
                    "record_id": later.record_id,
                    "section_record_id": record.record_id,
                    "section_id": section.section_id,
                    "text": section_text,
                    "text_sha256": hashlib.sha256(section_text.encode()).hexdigest(),
                    "source_sha256": record.source_sha256,
                    "parent_source_sha256": section.parent_sha256,
                    "section_sha256": section.section_sha256,
                    "parent_provenance_id": record.provenance_id,
                    "provenance_id": section.provenance_id,
                    "provenance_operation": WIKI_SECTION_REVISION,
                    "source_binding_provenance": "verified_derived",
                    "fact_parser_revision": program.fact_parser_revision,
                    "source_origin": "real_derived",
                    "source_family": record.source_family,
                    "source_url": record.source_url,
                    "retrieval_url": record.retrieval_url,
                    "fact_spans": fact_spans,
                    "grounded_source": grounded_source,
                    "ground_values": [section.ground_value],
                },
                visibility=[event_id],
            )
        )
    record_event_ids = {
        later.record_id: section_ids.get("early_work", "")
        or section_ids.get("early_birth", ""),
        entity.record_id: section_ids.get("wikidata_entity", ""),
    }
    relation_event_ids: list[str] = []
    relation_ids: list[str] = []
    records_by_id = {record.record_id: record for record in workflow.records}
    for relation_index, relation in enumerate(workflow.relations):
        source_event_id = record_event_ids.get(relation.source_record_id, "")
        target_event_id = record_event_ids.get(relation.target_record_id, "")
        if (
            relation.kind not in {"page_describes_entity", "entity_resolves_page"}
            or not source_event_id
            or not target_event_id
            or source_event_id == target_event_id
            or len(relation.evidence) != 1
        ):
            continue
        source = records_by_id[relation.source_record_id]
        target = records_by_id[relation.target_record_id]
        evidence = relation.evidence[0]
        relation_event_id = (
            f"{prefix}.wiki_source_relation_{workflow_index}_{relation_index}"
        )
        provenance_id = "derived-sha256:" + _canonical_digest(
            {
                "workflow_id": workflow.workflow_id,
                "relation_id": relation.relation_id,
                "kind": relation.kind,
                "source_provenance_id": source.provenance_id,
                "target_provenance_id": target.provenance_id,
                "evidence_quote": evidence.evidence_quote,
                "evidence_char_start": evidence.char_start,
            }
        )
        events.append(
            Event(
                id=relation_event_id,
                type="wiki_source_relation",
                time=max(
                    date.fromisoformat(source.occurred_at[:10]),
                    date.fromisoformat(target.occurred_at[:10]),
                )
                + timedelta(days=9),
                params={
                    "workflow_id": workflow.workflow_id,
                    "record_id": later.record_id,
                    "relation_id": relation.relation_id,
                    "relation_kind": relation.kind,
                    "source_record_id": relation.source_record_id,
                    "target_record_id": relation.target_record_id,
                    "source_record_event_id": source_event_id,
                    "target_record_event_id": target_event_id,
                    "source_url": source.source_url,
                    "target_source_url": target.source_url,
                    "source_family": source.source_family,
                    "evidence_record_id": evidence.record_id,
                    "evidence_quote": evidence.evidence_quote,
                    "evidence_char_start": evidence.char_start,
                    "evidence_char_end": evidence.char_end,
                    "source_sha256": source.source_sha256,
                    "target_sha256": target.source_sha256,
                    "source_provenance_id": source.provenance_id,
                    "target_provenance_id": target.provenance_id,
                    "provenance_id": provenance_id,
                    "source_binding_provenance": "authentic_source_api",
                    "relation_provenance": "authentic_source_api",
                    "source_origin": "real_derived",
                    "ground_values": [relation.kind, evidence.evidence_quote],
                },
                visibility=[relation_event_id],
                causal_inputs=[source_event_id, target_event_id],
                required_inputs=[source_event_id, target_event_id],
                relation_kinds={
                    source_event_id: "source_endpoint",
                    target_event_id: "target_endpoint",
                },
            )
        )
        relation_event_ids.append(relation_event_id)
        relation_ids.append(relation.relation_id)
    prior_id = ""
    prior_key = ""
    compute_keys: dict[str, str] = {}
    used_sections: set[str] = set()
    early_sections = tuple(early_section_names) or ("early_work",)
    sections_by_id = {section.section_id: section for section in program.sections}
    roles_by_section = {
        section_id: [fact.role for fact in sections_by_id[section_id].facts]
        for section_id in section_ids
    }
    role_tags = (
        {
            fact.role: fact.answer_tag
            for section in program.sections
            for fact in section.facts
        }
        if semantic_tags
        else _WIKI_LEGACY_ROLE_TAGS
    )
    early_roles = [
        role for section_id in early_sections for role in roles_by_section[section_id]
    ]
    middle_roles = roles_by_section["commemoration"]
    entity_roles = roles_by_section["wikidata_entity"]
    late_roles = roles_by_section["popular_culture"]
    tier_roles = {
        "16k": early_roles,
        "32k": [*early_roles, *middle_roles, *entity_roles],
        "64k": [*early_roles, *middle_roles, *entity_roles, *late_roles],
    }
    append_roles = {
        "16k": early_roles,
        "32k": [*middle_roles, *entity_roles],
        "64k": late_roles,
    }
    for control_tier, rungs, default_sections in _WIKI_CLAIM_TIERS:
        needed_sections = (*early_sections, *default_sections[1:])
        new_sections = [name for name in needed_sections if name not in used_sections]
        for rung_index, (offset, compose, stage) in enumerate(rungs):
            event_id = (
                f"{prefix}.wiki_claim_{control_tier}_{compose}_{rung_index}_"
                f"{workflow_index}_{later.record_id}"
            )
            if compose == "copy":
                parents = [prior_id] if prior_id else []
                answer_key = (
                    f"wiki_claim_reconstruction:{later.record_id}:"
                    f"{control_tier}_rung{rung_index}"
                )
                required_roles: list[str] = []
            else:
                # Continue the published chain. Re-attaching ancestor sections
                # as parallel parents shortens shortest-path proof depth.
                parents = [prior_id] if prior_id else []
                parents.extend(section_ids[name] for name in new_sections)
                if control_tier in {"32k", "64k"}:
                    parents.extend(relation_event_ids)
                answer_key = (
                    f"wiki_claim_reconstruction:{later.record_id}:{control_tier}"
                )
                required_roles = list(tier_roles[control_tier])
            relation_kinds = {
                parent: (
                    "extends"
                    if parent == prior_id
                    else "reads_relation"
                    if parent in relation_event_ids
                    else "reads_section"
                )
                for parent in parents
            }
            events.append(
                Event(
                    id=event_id,
                    type="wiki_claim_answer",
                    time=later_day
                    + timedelta(
                        days=(
                            max(offset, len(early_sections))
                            if control_tier == "16k"
                            else offset
                        )
                    ),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": later.record_id,
                        "control_tier": control_tier,
                        "control_stage": stage,
                        "compose": compose,
                        "answer_key": answer_key,
                        "prerequisite_answer_key": prior_key
                        if compose == "copy"
                        else (
                            compute_keys["16k"]
                            if control_tier == "32k"
                            else compute_keys.get("32k", "")
                            if control_tier == "64k"
                            else ""
                        ),
                        "required_roles": required_roles,
                        **(
                            {
                                "append_roles": list(append_roles[control_tier]),
                                "role_tags": {
                                    role: role_tags[role] for role in required_roles
                                },
                            }
                            if semantic_tags
                            else {}
                        ),
                        "response_schema": "||".join(
                            f"{role_tags[role]}:<value>" for role in required_roles
                        ),
                        "required_relation_ids": list(relation_ids)
                        if control_tier in {"32k", "64k"}
                        else [],
                        "section_event_ids": [
                            section_ids[name] for name in needed_sections
                        ],
                        "ground_values": ["wiki-reconstructed", stage],
                        "relation_provenance": "synthetic_executable",
                    },
                    visibility=[event_id],
                    causal_inputs=parents,
                    required_inputs=list(parents),
                    relation_kinds=relation_kinds,
                )
            )
            prior_id = event_id
            prior_key = answer_key
            if compose == "compute":
                compute_keys[control_tier] = answer_key
        used_sections.update(needed_sections)
    return events


_ARXIV_16K_VIEWS: dict[str, frozenset[str] | None] = {
    "v1": frozenset({"example.tex", "illustrations_sup.tex", "main.tex"}),
    "v2": frozenset(
        {
            "acknowledgements.tex",
            "discussion.tex",
            "experiments_details.tex",
            "main.tex",
        }
    ),
    "v3": frozenset({"abstract.tex"}),
}
_ARXIV_32K_FILES = frozenset(
    {
        "abstract.tex",
        "acknowledgements.tex",
        "example.tex",
        "experiments_sup.tex",
        "expressions.tex",
        "feature.tex",
        "introduction.tex",
        "main.tex",
    }
)
_ARXIV_TERMINAL_FILES = frozenset({"abstract.tex", "acknowledgements.tex"})
_ARXIV_TIER_OFFSETS = {"16k": 0, "32k": 365, "64k": 730}


def _paper_multiband_records(workflow: Any) -> dict[str, Any] | None:
    records = {record.attribute("revision_id"): record for record in workflow.records}
    if set(records) != {"v1", "v2", "v3"} or len(workflow.relations) != 2:
        return None
    relation_pairs = {
        (
            records["v2"].record_id,
            records["v1"].record_id,
        ),
        (
            records["v3"].record_id,
            records["v2"].record_id,
        ),
    }
    if {
        (relation.source_record_id, relation.target_record_id)
        for relation in workflow.relations
        if relation.kind == "revision_of" and len(relation.evidence) == 1
    } != relation_pairs:
        return None
    delta_facts = [
        fact for fact in records["v2"].facts if fact.field == "revision_added_text"
    ]
    if len(delta_facts) != 1:
        return None
    try:
        v1_16k, _, _, v1_spans = _semantic_arxiv_body(
            records["v1"],
            included_basenames=_ARXIV_16K_VIEWS["v1"],
            bind_file_spans=True,
        )
        v2_16k, _, _, v2_spans = _semantic_arxiv_body(
            records["v2"],
            included_basenames=_ARXIV_16K_VIEWS["v2"],
            bind_file_spans=True,
        )
        required_new_include = "parts/acknowledgements"
        v1_files = {
            span["basename"]: v1_16k[span["char_start"] : span["char_end"]]
            for span in v1_spans
        }
        v2_files = {
            span["basename"]: v2_16k[span["char_start"] : span["char_end"]]
            for span in v2_spans
        }
        delta_value = delta_facts[0].value
        if (
            "main.tex" not in v1_files
            or "main.tex" not in v2_files
            or "acknowledgements.tex" not in v2_files
            or required_new_include in v1_files["main.tex"]
            or required_new_include not in v2_files["main.tex"]
            or v2_files["acknowledgements.tex"].count(delta_value) != 1
        ):
            return None
        for revision_id, record in records.items():
            _semantic_arxiv_body(
                record,
                included_basenames=_ARXIV_16K_VIEWS[revision_id],
                bind_file_spans=True,
            )
            _semantic_arxiv_body(
                record,
                included_basenames=_ARXIV_32K_FILES,
                bind_file_spans=True,
            )
    except ValueError:
        return None
    return records


def _multiband_arxiv_revision_event(
    *,
    workflow: Any,
    record: Any,
    prefix: str,
    workflow_index: int,
    record_index: int,
    tier: str,
    included_basenames: frozenset[str] | None,
) -> tuple[Event, str, str, dict[str, str]]:
    body, provenance_id, excluded_paths, source_file_spans = _semantic_arxiv_body(
        record,
        included_basenames=included_basenames,
        bind_file_spans=True,
    )
    event_id = f"{prefix}.arxiv_revision_{tier}_{workflow_index}_{record_index}"
    revision_id = record.attribute("revision_id")
    revision_start = body.index(revision_id)
    revision_fact_id = f"{event_id}:revision_id"
    submitted_at = record.occurred_at
    submitted_start = body.index(submitted_at)
    grounded_facts = [
        _grounded_fact(
            source_id=event_id,
            text=body,
            fact_id=revision_fact_id,
            quote=revision_id,
            char_start=revision_start,
        ),
        _grounded_fact(
            source_id=event_id,
            text=body,
            fact_id=f"{event_id}:submitted_at",
            quote=submitted_at,
            char_start=submitted_start,
        ),
    ]
    source_fact_ids: dict[str, str] = {}
    for fact_index, fact in enumerate(record.facts):
        if body.count(fact.value) != 1:
            continue
        fact_id = f"{event_id}:source_fact_{fact_index}"
        source_fact_ids[fact.fact_id] = fact_id
        grounded_facts.append(
            _grounded_fact(
                source_id=event_id,
                text=body,
                fact_id=fact_id,
                quote=fact.value,
                char_start=body.index(fact.value),
            )
        )
    text_sha256 = hashlib.sha256(body.encode()).hexdigest()
    source_envelope = _arxiv_source_envelope(
        workflow=workflow,
        record=record,
        text_sha256=text_sha256,
        provenance_id=provenance_id,
        provenance_operation="arxiv_semantic_latex_body_v2",
        source_file_spans=source_file_spans,
        source_view_basenames=sorted(
            str(span["basename"]) for span in source_file_spans
        ),
    )
    event = Event(
        id=event_id,
        type="arxiv_revision",
        time=date.fromisoformat(record.occurred_at[:10])
        + timedelta(days=_ARXIV_TIER_OFFSETS[tier]),
        params={
            "workflow_id": workflow.workflow_id,
            "record_id": record.record_id,
            "revision_id": revision_id,
            "occurred_at": record.occurred_at,
            "source_view_tier": tier,
            "text": body,
            "text_sha256": text_sha256,
            "source_sha256": record.source_sha256,
            "parent_provenance_id": record.provenance_id,
            "provenance_id": provenance_id,
            "provenance_operation": "arxiv_semantic_latex_body_v2",
            "source_binding_provenance": "verified_derived",
            "canonical_source_envelope_sha256": _canonical_digest(source_envelope),
            "excluded_paths": excluded_paths,
            "source_file_spans": source_file_spans,
            "source_view_basenames": sorted(
                str(span["basename"]) for span in source_file_spans
            ),
            "source_origin": "real_derived",
            "source_family": record.source_family,
            "source_url": record.source_url,
            "retrieval_url": record.retrieval_url,
            "ground_values": [
                record.occurred_at[:10],
                *(fact.value for fact in record.facts if fact.value in body),
            ],
            "grounded_source": _grounded_source(
                source_id=event_id, text=body, facts=grounded_facts
            ),
        },
        visibility=[event_id],
    )
    return event, body, revision_fact_id, source_fact_ids


def _multiband_arxiv_relation_event(
    *,
    workflow: Any,
    records: dict[str, Any],
    relation: Any,
    relation_index: int,
    workflow_index: int,
    tier: str,
    source_event: Event,
    target_event: Event,
    source_body: str,
    source_revision_fact_id: str,
    target_revision_fact_id: str,
    source_fact_ids: dict[str, str],
    prior_relation_id: str = "",
) -> Event:
    source = records[str(source_event.params["revision_id"])]
    target = records[str(target_event.params["revision_id"])]
    selected_facts = [
        fact for fact in source.facts if fact.field == "revision_added_text"
    ]
    delta_fact = selected_facts[0] if len(selected_facts) == 1 else None
    grounded_fact_id = (
        source_fact_ids.get(delta_fact.fact_id) if delta_fact is not None else None
    )
    relation_mode = "semantic_delta" if grounded_fact_id else "lineage_only"
    if relation_mode == "semantic_delta":
        assert delta_fact is not None and grounded_fact_id is not None
        fact_start = source_body.index(delta_fact.value)
        evidence_fact_ids = [
            source_revision_fact_id,
            target_revision_fact_id,
            grounded_fact_id,
        ]
    else:
        fact_start = -1
        evidence_fact_ids = [source_revision_fact_id, target_revision_fact_id]
    event_id = (
        f"{source_event.id.rsplit('.arxiv_revision_', 1)[0]}."
        f"arxiv_revision_relation_{tier}_{workflow_index}_{relation_index}"
    )
    workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
    grounded_relation = {
        "relation_id": relation.relation_id,
        "relation_type": relation.kind,
        "source_id": source_event.id,
        "target_id": target_event.id,
        "claimed_provenance_class": "authentic_source_api",
        "evidence_fact_ids": evidence_fact_ids,
        "proof_mode": "structural_closure_only",
    }
    evidence = relation.evidence[0]
    causal_inputs = [target_event.id, source_event.id]
    relation_kinds = {
        target_event.id: "revision_of",
        source_event.id: "derived_from",
    }
    if prior_relation_id:
        causal_inputs.append(prior_relation_id)
        relation_kinds[prior_relation_id] = "extends"
    return Event(
        id=event_id,
        type="arxiv_revision_relation",
        time=max(source_event.time, target_event.time),
        params={
            "workflow_id": workflow.workflow_id,
            "work_id": source.attribute("work_id"),
            "source_view_tier": tier,
            "relation_mode": relation_mode,
            "candidate_key": (f"real_revision_delta_candidate:{workflow_key}:{tier}"),
            "relation_id": relation.relation_id,
            "relation_kind": relation.kind,
            "source_record_id": source.record_id,
            "target_record_id": target.record_id,
            "source_revision_id": source.attribute("revision_id"),
            "target_revision_id": target.attribute("revision_id"),
            "source_record_event_id": source_event.id,
            "target_record_event_id": target_event.id,
            "source_url": source.source_url,
            "target_source_url": target.source_url,
            "source_family": source.source_family,
            "evidence_quote": evidence.evidence_quote,
            "evidence_char_start": evidence.char_start,
            "fact_id": delta_fact.fact_id if delta_fact is not None else "",
            "grounded_fact_id": grounded_fact_id or "",
            "fact_char_start": fact_start,
            "fact_char_end": (
                fact_start + len(delta_fact.value) if delta_fact is not None else -1
            ),
            "fact_value_offset": 0,
            "fact_value_length": len(delta_fact.value) if delta_fact is not None else 0,
            "grounded_relation": grounded_relation,
            "relation_provenance": "authentic_source_api",
        },
        visibility=[event_id],
        causal_inputs=causal_inputs,
        required_inputs=[target_event.id, source_event.id],
        relation_kinds=relation_kinds,
    )


def _paper_multiband_events(
    workflow: Any, prefix: str, workflow_index: int, records: dict[str, Any]
) -> list[Event]:
    record_indices = {
        record.record_id: index for index, record in enumerate(workflow.records)
    }
    views: dict[str, dict[str, frozenset[str] | None]] = {
        "16k": _ARXIV_16K_VIEWS,
        "32k": {
            "v1": _ARXIV_32K_FILES,
            "v2": _ARXIV_32K_FILES,
            "v3": _ARXIV_TERMINAL_FILES,
        },
        "64k": {"v1": None, "v2": None, "v3": _ARXIV_TERMINAL_FILES},
    }
    events: list[Event] = []
    event_by_tier: dict[str, dict[str, Event]] = {}
    body_by_tier: dict[str, dict[str, str]] = {}
    revision_fact_by_tier: dict[str, dict[str, str]] = {}
    source_facts_by_tier: dict[str, dict[str, dict[str, str]]] = {}
    for tier, tier_views in views.items():
        event_by_tier[tier] = {}
        body_by_tier[tier] = {}
        revision_fact_by_tier[tier] = {}
        source_facts_by_tier[tier] = {}
        for revision_id, included in tier_views.items():
            record = records[revision_id]
            event, body, revision_fact, source_facts = _multiband_arxiv_revision_event(
                workflow=workflow,
                record=record,
                prefix=prefix,
                workflow_index=workflow_index,
                record_index=record_indices[record.record_id],
                tier=tier,
                included_basenames=included,
            )
            events.append(event)
            event_by_tier[tier][revision_id] = event
            body_by_tier[tier][revision_id] = body
            revision_fact_by_tier[tier][revision_id] = revision_fact
            source_facts_by_tier[tier][revision_id] = source_facts

    relations = {
        (relation.source_record_id, relation.target_record_id): (index, relation)
        for index, relation in enumerate(workflow.relations)
    }
    v2_v1 = relations[(records["v2"].record_id, records["v1"].record_id)]
    v3_v2 = relations[(records["v3"].record_id, records["v2"].record_id)]
    relation_events: dict[str, list[Event]] = {"16k": [], "32k": [], "64k": []}
    for tier in ("32k", "64k"):
        relation_index, relation = v2_v1
        delta_relation = _multiband_arxiv_relation_event(
            workflow=workflow,
            records=records,
            relation=relation,
            relation_index=relation_index,
            workflow_index=workflow_index,
            tier=tier,
            source_event=event_by_tier[tier]["v2"],
            target_event=event_by_tier[tier]["v1"],
            source_body=body_by_tier[tier]["v2"],
            source_revision_fact_id=revision_fact_by_tier[tier]["v2"],
            target_revision_fact_id=revision_fact_by_tier[tier]["v1"],
            source_fact_ids=source_facts_by_tier[tier]["v2"],
        )
        events.append(delta_relation)
        relation_events[tier].append(delta_relation)
        if tier == "64k":
            relation_index, relation = v3_v2
            terminal_relation = _multiband_arxiv_relation_event(
                workflow=workflow,
                records=records,
                relation=relation,
                relation_index=relation_index,
                workflow_index=workflow_index,
                tier=tier,
                source_event=event_by_tier[tier]["v3"],
                target_event=event_by_tier[tier]["v2"],
                source_body=body_by_tier[tier]["v3"],
                source_revision_fact_id=revision_fact_by_tier[tier]["v3"],
                target_revision_fact_id=revision_fact_by_tier[tier]["v2"],
                source_fact_ids=source_facts_by_tier[tier]["v3"],
                prior_relation_id=delta_relation.id,
            )
            events.append(terminal_relation)
            relation_events[tier].append(terminal_relation)

    delta_fact = next(
        fact for fact in records["v2"].facts if fact.field == "revision_added_text"
    )
    for tier in ("16k", "32k", "64k"):
        source = event_by_tier[tier]["v2"]
        target = event_by_tier[tier]["v1"]
        fact_start = body_by_tier[tier]["v2"].index(delta_fact.value)
        tier_relations = relation_events[tier]
        decision_id = f"{prefix}.arxiv_revision_decision_{tier}_{workflow_index}"
        proof_events = [target.id, source.id, event_by_tier[tier]["v3"].id]
        proof_events.extend(relation.id for relation in tier_relations)
        proof_events.append(decision_id)
        workflow_key = hashlib.sha256(workflow.workflow_id.encode()).hexdigest()[:12]
        causal_inputs = (
            [target.id, source.id, event_by_tier[tier]["v3"].id]
            if tier == "16k"
            else [tier_relations[-1].id]
        )
        events.append(
            Event(
                id=decision_id,
                type="arxiv_revision_decision",
                time=max(
                    event_by_tier[tier][revision_id].time
                    for revision_id in event_by_tier[tier]
                )
                + timedelta(days=1),
                params={
                    "workflow_id": workflow.workflow_id,
                    "work_id": records["v2"].attribute("work_id"),
                    "control_tier": tier,
                    "decision_mode": (
                        "direct_delta"
                        if tier == "16k"
                        else "terminal_chain"
                        if tier == "64k"
                        else "relation_delta"
                    ),
                    "answer_key": (f"real_revision_added_text:{workflow_key}:{tier}"),
                    "candidate_key": (
                        f"real_revision_delta_candidate:{workflow_key}:{tier}"
                    ),
                    "source_record_id": records["v2"].record_id,
                    "target_record_id": records["v1"].record_id,
                    "terminal_record_id": records["v3"].record_id,
                    "source_record_event_id": source.id,
                    "target_record_event_id": target.id,
                    "terminal_record_event_id": event_by_tier[tier]["v3"].id,
                    "relation_event_id": tier_relations[0].id if tier_relations else "",
                    "required_relation_ids": [
                        str(relation.params["relation_id"])
                        for relation in tier_relations
                    ],
                    "fact_id": delta_fact.fact_id,
                    "grounded_fact_id": source_facts_by_tier[tier]["v2"].get(
                        delta_fact.fact_id, ""
                    ),
                    "fact_char_start": fact_start,
                    "fact_char_end": fact_start + len(delta_fact.value),
                    "fact_value_offset": 0,
                    "fact_value_length": len(delta_fact.value),
                    "required_new_include": (
                        "parts/acknowledgements" if tier == "16k" else ""
                    ),
                    "required_new_include_source_basename": (
                        "main.tex" if tier == "16k" else ""
                    ),
                    "fact_source_basename": "acknowledgements.tex",
                    "proof_event_ids": proof_events,
                },
                visibility=[decision_id],
                causal_inputs=causal_inputs,
                required_inputs=list(causal_inputs),
                relation_kinds={
                    event_id: "reads_source"
                    if tier == "16k"
                    else "applies_revision_chain"
                    for event_id in causal_inputs
                },
            )
        )
    return events


def _source_workflow_events(project: dict[str, Any], prefix: str) -> list[Event]:
    events: list[Event] = []
    for workflow_index, workflow in enumerate(project.get("source_workflows") or []):
        if workflow.source_kind == WIKIMEDIA_SOURCE_KIND:
            events.extend(
                _wikipedia_source_workflow_events(workflow, prefix, workflow_index)
            )
            continue
        if workflow.source_kind != PAPER_SOURCE_KIND:
            continue
        multiband_records = _paper_multiband_records(workflow)
        if multiband_records is not None:
            events.extend(
                _paper_multiband_events(
                    workflow, prefix, workflow_index, multiband_records
                )
            )
            continue
        record_event_ids: dict[str, str] = {}
        record_bodies: dict[str, str] = {}
        record_revision_fact_ids: dict[str, str] = {}
        record_source_fact_ids: dict[str, dict[str, str]] = {}
        records = {record.record_id: record for record in workflow.records}
        for record_index, record in enumerate(workflow.records):
            body, provenance_id, excluded_paths, _source_file_spans = (
                _semantic_arxiv_body(record)
            )
            event_id = f"{prefix}.arxiv_revision_{workflow_index}_{record_index}"
            record_event_ids[record.record_id] = event_id
            record_bodies[record.record_id] = body
            revision_id = record.attribute("revision_id")
            revision_start = body.index(revision_id)
            revision_fact_id = f"{event_id}:revision_id"
            submitted_at = record.occurred_at
            submitted_start = body.index(submitted_at)
            submitted_fact_id = f"{event_id}:submitted_at"
            record_revision_fact_ids[record.record_id] = revision_fact_id
            grounded_facts = [
                _grounded_fact(
                    source_id=event_id,
                    text=body,
                    fact_id=revision_fact_id,
                    quote=revision_id,
                    char_start=revision_start,
                ),
                _grounded_fact(
                    source_id=event_id,
                    text=body,
                    fact_id=submitted_fact_id,
                    quote=submitted_at,
                    char_start=submitted_start,
                ),
            ]
            source_fact_ids: dict[str, str] = {}
            for fact_index, fact in enumerate(record.facts):
                if body.count(fact.value) != 1:
                    continue
                grounded_fact_id = f"{event_id}:source_fact_{fact_index}"
                source_fact_ids[fact.fact_id] = grounded_fact_id
                grounded_facts.append(
                    _grounded_fact(
                        source_id=event_id,
                        text=body,
                        fact_id=grounded_fact_id,
                        quote=fact.value,
                        char_start=body.index(fact.value),
                    )
                )
            record_source_fact_ids[record.record_id] = source_fact_ids
            text_sha256 = hashlib.sha256(body.encode()).hexdigest()
            source_envelope = _arxiv_source_envelope(
                workflow=workflow,
                record=record,
                text_sha256=text_sha256,
                provenance_id=provenance_id,
            )
            events.append(
                Event(
                    id=event_id,
                    type="arxiv_revision",
                    time=date.fromisoformat(record.occurred_at[:10]),
                    params={
                        "workflow_id": workflow.workflow_id,
                        "record_id": record.record_id,
                        "revision_id": record.attribute("revision_id"),
                        "occurred_at": record.occurred_at,
                        "text": body,
                        "text_sha256": text_sha256,
                        "source_sha256": record.source_sha256,
                        "parent_provenance_id": record.provenance_id,
                        "provenance_id": provenance_id,
                        "provenance_operation": "arxiv_semantic_latex_body_v1",
                        "source_binding_provenance": "verified_derived",
                        "canonical_source_envelope_sha256": _canonical_digest(
                            source_envelope
                        ),
                        "excluded_paths": excluded_paths,
                        "source_origin": "real_derived",
                        "source_family": record.source_family,
                        "source_url": record.source_url,
                        "retrieval_url": record.retrieval_url,
                        "ground_values": [
                            record.occurred_at[:10],
                            *(
                                fact.value
                                for fact in record.facts
                                if fact.value in body
                            ),
                        ],
                        "grounded_source": _grounded_source(
                            source_id=event_id,
                            text=body,
                            facts=grounded_facts,
                        ),
                    },
                    visibility=[
                        f"{prefix}.arxiv_revision_{workflow_index}_{record_index}"
                    ],
                )
            )
        for relation_index, relation in enumerate(workflow.relations):
            if relation.kind != "revision_of":
                continue
            source = records[relation.source_record_id]
            selected_facts = [
                fact for fact in source.facts if fact.field == "revision_added_text"
            ]
            if len(selected_facts) != 1 or len(relation.evidence) != 1:
                continue
            fact = selected_facts[0]
            source_body = record_bodies[relation.source_record_id]
            if source_body.count(fact.value) != 1:
                raise ValueError("revision-added fact is not unique in semantic body")
            fact_start = source_body.index(fact.value)
            evidence = relation.evidence[0]
            relation_event_id = (
                f"{prefix}.arxiv_revision_relation_{workflow_index}_{relation_index}"
            )
            decision_event_id = (
                f"{prefix}.arxiv_revision_decision_{workflow_index}_{relation_index}"
            )
            source_event_id = record_event_ids[relation.source_record_id]
            target_event_id = record_event_ids[relation.target_record_id]
            relation_key = hashlib.sha256(
                f"{workflow.workflow_id}|{relation.relation_id}".encode()
            ).hexdigest()[:12]
            candidate_key = f"real_revision_delta_candidate:{relation_key}"
            answer_key = f"real_revision_added_text:{relation_key}"
            selected_grounded_fact_id = record_source_fact_ids[
                relation.source_record_id
            ].get(fact.fact_id)
            if not selected_grounded_fact_id:
                continue
            grounded_relation = {
                "relation_id": relation.relation_id,
                "relation_type": relation.kind,
                "source_id": source_event_id,
                "target_id": target_event_id,
                "claimed_provenance_class": "authentic_source_api",
                "evidence_fact_ids": [
                    record_revision_fact_ids[relation.source_record_id],
                    record_revision_fact_ids[relation.target_record_id],
                    selected_grounded_fact_id,
                ],
                "proof_mode": "structural_closure_only",
            }
            events.extend(
                [
                    Event(
                        id=relation_event_id,
                        type="arxiv_revision_relation",
                        time=date.fromisoformat(source.occurred_at[:10]),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "work_id": source.attribute("work_id"),
                            "candidate_key": candidate_key,
                            "relation_id": relation.relation_id,
                            "relation_kind": relation.kind,
                            "source_record_id": relation.source_record_id,
                            "target_record_id": relation.target_record_id,
                            "source_revision_id": source.attribute("revision_id"),
                            "target_revision_id": records[
                                relation.target_record_id
                            ].attribute("revision_id"),
                            "source_record_event_id": source_event_id,
                            "target_record_event_id": target_event_id,
                            "source_url": source.source_url,
                            "target_source_url": records[
                                relation.target_record_id
                            ].source_url,
                            "source_family": source.source_family,
                            "evidence_quote": evidence.evidence_quote,
                            "evidence_char_start": evidence.char_start,
                            "fact_id": fact.fact_id,
                            "grounded_fact_id": selected_grounded_fact_id,
                            "fact_char_start": fact_start,
                            "fact_char_end": fact_start + len(fact.value),
                            "fact_value_offset": 0,
                            "fact_value_length": len(fact.value),
                            "grounded_relation": grounded_relation,
                            "relation_provenance": "authentic_source_api",
                        },
                        visibility=[
                            (
                                f"{prefix}.arxiv_revision_relation_"
                                f"{workflow_index}_{relation_index}"
                            )
                        ],
                        causal_inputs=[target_event_id, source_event_id],
                        required_inputs=[target_event_id, source_event_id],
                        relation_kinds={
                            target_event_id: "revision_of",
                            source_event_id: "derived_from",
                        },
                    ),
                    Event(
                        id=decision_event_id,
                        type="arxiv_revision_decision",
                        time=date.fromisoformat(source.occurred_at[:10])
                        + timedelta(days=1),
                        params={
                            "workflow_id": workflow.workflow_id,
                            "candidate_key": candidate_key,
                            "answer_key": answer_key,
                            "relation_id": relation.relation_id,
                            "relation_event_id": relation_event_id,
                            "source_record_event_id": source_event_id,
                            "target_record_event_id": target_event_id,
                        },
                        visibility=[
                            (
                                f"{prefix}.arxiv_revision_decision_"
                                f"{workflow_index}_{relation_index}"
                            )
                        ],
                        causal_inputs=[relation_event_id],
                        required_inputs=[relation_event_id],
                        relation_kinds={relation_event_id: "enables"},
                    ),
                ]
            )
    return events


def canonical_researchlab_source_event_envelope(
    world_spec: dict[str, Any],
    *,
    event_id: str,
    event_type: str,
    record_id: str = "",
    relation_id: str = "",
    section_id: str = "",
) -> dict[str, Any] | None:
    """Rebuild one source event only from canonical workflows in ``world_spec``."""
    project = world_spec.get("project")
    prefix = world_spec.get("prefix")
    if not isinstance(project, dict) or not isinstance(prefix, str) or not prefix:
        return None
    if event_type not in {
        "arxiv_revision",
        "arxiv_revision_relation",
        "wiki_source_relation",
        "wiki_source_section",
    }:
        return None
    matches = [
        event
        for event in _source_workflow_events(project, prefix)
        if event.id == event_id
        and event.type == event_type
        and (not record_id or event.params.get("record_id") == record_id)
        and (not relation_id or event.params.get("relation_id") == relation_id)
        and (not section_id or event.params.get("section_id") == section_id)
    ]
    if len(matches) != 1:
        return None
    event = matches[0]
    return {
        "id": event.id,
        "type": event.type,
        "time": event.time,
        "params": event.params,
        "visibility": list(event.visibility),
        "preconditions": list(event.preconditions),
        "causal_inputs": list(event.causal_inputs),
        "required_inputs": list(event.required_inputs),
        "relation_kinds": dict(event.relation_kinds),
        "skipped": event.skipped,
        "skip_reason": event.skip_reason,
    }


def selected_wiki_source_relation_edges(
    world: SimulatedWorld, spec: Any, artifacts: list[Any]
) -> list[dict[str, str]]:
    """Return signed Wikimedia relations whose two source endpoints are selected."""
    selected_event_ids = set(getattr(spec, "sufficient_event_ids", ()))
    selected_event_ids &= {
        event_id
        for artifact in artifacts
        for event_id in getattr(artifact, "reveals_events", ())
    }
    events = {
        event.id: event for event in world.events if event.id in selected_event_ids
    }
    edges: list[dict[str, str]] = []
    for relation in events.values():
        if relation.type != "wiki_source_relation":
            continue
        params = relation.params
        source_event_id = str(params.get("source_record_event_id") or "")
        target_event_id = str(params.get("target_record_event_id") or "")
        source = events.get(source_event_id)
        target = events.get(target_event_id)
        canonical = canonical_researchlab_source_event_envelope(
            world.spec,
            event_id=relation.id,
            event_type=relation.type,
            record_id=str(params.get("record_id") or ""),
            relation_id=str(params.get("relation_id") or ""),
        )
        observed = {
            "id": relation.id,
            "type": relation.type,
            "time": relation.time,
            "params": relation.params,
            "visibility": list(relation.visibility),
            "preconditions": list(relation.preconditions),
            "causal_inputs": list(relation.causal_inputs),
            "required_inputs": list(relation.required_inputs),
            "relation_kinds": dict(relation.relation_kinds),
            "skipped": relation.skipped,
            "skip_reason": relation.skip_reason,
        }
        if (
            canonical != observed
            or source is None
            or target is None
            or source.type != "wiki_source_section"
            or target.type != "wiki_source_section"
            or set(relation.required_inputs) != {source_event_id, target_event_id}
            or source_event_id == target_event_id
            or str(source.params.get("section_record_id") or "")
            != str(params.get("source_record_id") or "")
            or str(target.params.get("section_record_id") or "")
            != str(params.get("target_record_id") or "")
            or str(source.params.get("source_url") or "")
            != str(params.get("source_url") or "")
            or str(target.params.get("source_url") or "")
            != str(params.get("target_source_url") or "")
        ):
            continue
        edges.append(
            {
                "parent_record_id": str(params["source_record_id"]),
                "child_record_id": str(params["target_record_id"]),
                "relation": str(params["relation_kind"]),
                "relation_provenance": "authentic_source",
                "parent_source_url": str(params["source_url"]),
                "child_source_url": str(params["target_source_url"]),
            }
        )
    return sorted(
        edges,
        key=lambda edge: (
            edge["relation"],
            edge["parent_record_id"],
            edge["child_record_id"],
        ),
    )


def canonical_researchlab_source_visible_text(
    event_or_envelope: Event | dict[str, Any],
) -> str | None:
    """Render source-visible bytes shared by canonical replay and artifacts."""
    event_type: Any
    params: Any
    if isinstance(event_or_envelope, Event):
        event_type = event_or_envelope.type
        params = event_or_envelope.params
    elif isinstance(event_or_envelope, dict):
        event_type = event_or_envelope.get("type")
        params = event_or_envelope.get("params")
    else:
        return None
    if not isinstance(event_type, str) or not isinstance(params, dict):
        return None
    if event_type in {"arxiv_revision", "wiki_source_section"}:
        text = params.get("text")
        return text if isinstance(text, str) and text else None
    if event_type == "arxiv_revision_relation":
        required = (
            "relation_kind",
            "source_revision_id",
            "target_revision_id",
            "evidence_quote",
        )
        if any(not isinstance(params.get(field), str) for field in required):
            return None
        return (
            json.dumps(
                {
                    "kind": "arxiv_revision_relation",
                    "relation": params["relation_kind"],
                    "source_revision": params["source_revision_id"],
                    "target_revision": params["target_revision_id"],
                    "evidence": params["evidence_quote"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    if event_type == "wiki_source_relation":
        wiki_required = (
            "relation_id",
            "relation_kind",
            "source_record_id",
            "target_record_id",
            "evidence_quote",
        )
        if any(not isinstance(params.get(field), str) for field in wiki_required):
            return None
        return (
            json.dumps(
                {
                    "kind": "wiki_source_relation",
                    "relation_id": params["relation_id"],
                    "relation": params["relation_kind"],
                    "source_record": params["source_record_id"],
                    "target_record": params["target_record_id"],
                    "evidence": params["evidence_quote"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return None


def _experiment_events(
    project: dict[str, Any], prefix: str, start: date
) -> list[Event]:
    workstreams = list(project.get("experiment_workstreams") or [])
    if not workstreams:
        return []

    def event_id(workstream_id: str, stage: str) -> str:
        return f"{prefix}.experiment_{workstream_id}_{stage}"

    events: list[Event] = []
    lane_count = min(6, len(workstreams))
    for workstream in workstreams:
        workstream_id = str(workstream["id"])
        lane = int(workstream["lane"])
        cycle = int(workstream["cycle"])
        base_month = 21 + (cycle - 1) * 6
        day_offset = 2 * (lane - 1)
        upstream_id = workstream.get("upstream_id")
        upstream_recovery = (
            event_id(str(upstream_id), "recovery") if upstream_id else None
        )
        revision_inputs = [upstream_recovery] if upstream_recovery else []
        revision_relations = (
            {upstream_recovery: "derived_from"} if upstream_recovery else {}
        )
        shared = {
            "workstream": workstream_id,
            "lane": lane,
            "cycle": cycle,
            "upstream_id": upstream_id,
            "objective": workstream["objective"],
            "dataset_slice": workstream["dataset_slice"],
            "metric": workstream["metric"],
            "environment": workstream["environment"],
            "failure_mode": workstream["failure_mode"],
            "recovery_action": workstream["recovery_action"],
        }
        revision_id = event_id(workstream_id, "revision")
        review_id = event_id(workstream_id, "review")
        benchmark_id = event_id(workstream_id, "benchmark")
        failure_id = event_id(workstream_id, "failure")
        recovery_id = event_id(workstream_id, "recovery")
        events.extend(
            [
                Event(
                    id=revision_id,
                    type="experiment_revision",
                    time=_date(start, base_month, day_offset),
                    params={**shared, "revision": workstream["revision"]},
                    visibility=[f"{prefix}.experiment_{workstream_id}_revision"],
                    causal_inputs=revision_inputs,
                    required_inputs=revision_inputs,
                    relation_kinds=revision_relations,
                ),
                Event(
                    id=review_id,
                    type="experiment_review",
                    time=_date(start, base_month + 1, day_offset),
                    params={
                        **shared,
                        "review": workstream["review"],
                        "revision": workstream["revision"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_review"],
                    causal_inputs=[revision_id],
                    required_inputs=[revision_id],
                    relation_kinds={revision_id: "derived_from"},
                ),
                Event(
                    id=benchmark_id,
                    type="experiment_benchmark",
                    time=_date(start, base_month + 2, day_offset),
                    params={
                        **shared,
                        "benchmark": workstream["benchmark"],
                        "review": workstream["review"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_benchmark"],
                    causal_inputs=[review_id],
                    required_inputs=[review_id],
                    relation_kinds={review_id: "enables"},
                ),
                Event(
                    id=failure_id,
                    type="experiment_failure",
                    time=_date(start, base_month + 3, day_offset),
                    params={
                        **shared,
                        "failure": workstream["failure"],
                        "benchmark": workstream["benchmark"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_failure"],
                    causal_inputs=[benchmark_id],
                    required_inputs=[benchmark_id],
                    relation_kinds={benchmark_id: "contradicts"},
                ),
                Event(
                    id=recovery_id,
                    type="experiment_recovery",
                    time=_date(start, base_month + 4, day_offset),
                    params={
                        **shared,
                        "run": workstream["recovery"],
                        "failure": workstream["failure"],
                        "benchmark": workstream["benchmark"],
                    },
                    visibility=[f"{prefix}.experiment_{workstream_id}_recovery"],
                    causal_inputs=[failure_id, benchmark_id],
                    required_inputs=[failure_id],
                    relation_kinds={
                        failure_id: "supersedes",
                        benchmark_id: "derived_from",
                    },
                ),
            ]
        )

    recovery_ids = [
        event_id(str(workstream["id"]), "recovery") for workstream in workstreams
    ]
    last_cycle = max(int(workstream["cycle"]) for workstream in workstreams)
    events.append(
        Event(
            id=f"{prefix}.experiment_matrix_decision",
            type="experiment_matrix_decision",
            time=_date(start, 21 + (last_cycle - 1) * 6 + 5, 15),
            params={
                "workstream_ids": [str(workstream["id"]) for workstream in workstreams],
                "lane_count": lane_count,
            },
            visibility=[f"{prefix}.experiment_matrix_decision"],
            causal_inputs=recovery_ids,
            required_inputs=recovery_ids,
            relation_kinds={event_id_: "enables" for event_id_ in recovery_ids},
        )
    )
    return events


def events_for_lab(project: dict[str, Any], prefix: str) -> list[Event]:
    start = date.fromisoformat(project["start"])
    pid = project["paper"].lower()
    v1 = project["v1_score"]
    final = project["final_score"]
    cause = project["cause_token"]
    cache_tok, split_tok = cause.split("+", 1)
    adopt = bool(project.get("is_focal", False))

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    evs: list[Event] = [
        Event(
            id=eid("license_clause"),
            type="license_clause",
            time=_date(start, 0, 1),
            params={"spdx": project["spdx"]},
            visibility=[f"{prefix}.license_note"],
            causal_inputs=[],
        ),
        Event(
            id=eid("report_v1"),
            type="report_v1",
            time=_date(start, 0, 2),
            params={"score": v1},
            visibility=[f"{prefix}.arxiv_v1"],
            causal_inputs=[],
        ),
        Event(
            id=eid("commit_tokenizer"),
            type="commit_tokenizer",
            time=_date(start, 1, 4),
            params={"commit": project["commit_hash"]},
            visibility=[f"{prefix}.git_commit"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "enables"},
        ),
        Event(
            id=eid("log_stale_cache"),
            type="log_stale_cache",
            time=_date(start, 2, 6),
            params={"cause_cache": cache_tok},
            visibility=[f"{prefix}.eval_log"],
            causal_inputs=[eid("commit_tokenizer")],
            relation_kinds={eid("commit_tokenizer"): "derived_from"},
        ),
        Event(
            id=eid("issue_testset"),
            type="issue_testset",
            time=_date(start, 3, 3),
            params={
                "filed": True,
                "cause_cache": cache_tok,
                "cause_split": split_tok,
            },
            visibility=[f"{prefix}.github_issue"],
            causal_inputs=[eid("report_v1")],
            relation_kinds={eid("report_v1"): "contradicts"},
        ),
        Event(
            id=eid("fix_rerun"),
            type="fix_rerun",
            time=_date(start, 4, 8),
            params={"score": final},
            visibility=[f"{prefix}.rerun_json"],
            preconditions=["issue_filed"],
            causal_inputs=[eid("issue_testset"), eid("log_stale_cache")],
            relation_kinds={
                eid("issue_testset"): "enables",
                eid("log_stale_cache"): "derived_from",
            },
        ),
        Event(
            id=eid("camera_ready"),
            type="camera_ready",
            time=_date(start, 5, 5),
            params={"body_score": round(float(v1) + 0.01, 2)},
            visibility=[f"{prefix}.camera_ready"],
            causal_inputs=[eid("fix_rerun"), eid("report_v1")],
            relation_kinds={
                eid("fix_rerun"): "supersedes",
                eid("report_v1"): "contradicts",
            },
        ),
        Event(
            id=eid("release_note"),
            type="release_note",
            time=_date(start, 6, 2),
            params={"adopt_rerun": adopt},
            visibility=[f"{prefix}.release_note"],
            causal_inputs=[
                eid("fix_rerun"),
                eid("camera_ready"),
                eid("license_clause"),
            ],
            relation_kinds={
                eid("fix_rerun"): "derived_from",
                eid("camera_ready"): "supersedes",
                eid("license_clause"): "enables",
            },
        ),
    ]
    if project.get("process", {}).get("invalidate"):
        evs.append(
            Event(
                id=eid("invalidate_run"),
                type="invalidate_run",
                time=_date(start, 7, 3),
                params={"dataset": project["dataset_v2"]},
                visibility=[f"{prefix}.dataset_card"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "supersedes"},
            )
        )
    evs.extend(
        [
            Event(
                id=eid("submit_revision_2"),
                type="submit_revision_2",
                time=_date(start, 8, 5),
                params={
                    "revision": project["revision_v2"],
                    "benchmark": project["benchmark_v2"],
                },
                visibility=[f"{prefix}.revision_2"],
                causal_inputs=[eid("camera_ready"), eid("fix_rerun")],
                relation_kinds={
                    eid("camera_ready"): "supersedes",
                    eid("fix_rerun"): "derived_from",
                },
            ),
            Event(
                id=eid("review_round_1"),
                type="review_round_1",
                time=_date(start, 9, 8),
                params={"requirement": project["review_round1"]},
                visibility=[f"{prefix}.review_1"],
                causal_inputs=[eid("submit_revision_2")],
                required_inputs=[eid("submit_revision_2")],
                relation_kinds={eid("submit_revision_2"): "derived_from"},
            ),
            Event(
                id=eid("respond_round_1"),
                type="respond_round_1",
                time=_date(start, 10, 6),
                params={"response": project["response_round1"]},
                visibility=[f"{prefix}.response_1"],
                causal_inputs=[eid("review_round_1")],
                required_inputs=[eid("review_round_1")],
                relation_kinds={eid("review_round_1"): "derived_from"},
            ),
            Event(
                id=eid("reproduction_failure"),
                type="reproduction_failure",
                time=_date(start, 11, 9),
                params={
                    "run": project["reproduction_failure"],
                    "benchmark": project["benchmark_v2"],
                },
                visibility=[f"{prefix}.reproduction_failure"],
                causal_inputs=[eid("respond_round_1")],
                required_inputs=[eid("respond_round_1")],
                relation_kinds={eid("respond_round_1"): "contradicts"},
            ),
            Event(
                id=eid("benchmark_patch"),
                type="benchmark_patch",
                time=_date(start, 13, 4),
                params={
                    "benchmark": project["benchmark_v3"],
                    "commit": project["benchmark_patch_commit"],
                },
                visibility=[f"{prefix}.benchmark_patch"],
                causal_inputs=[eid("reproduction_failure")],
                required_inputs=[eid("reproduction_failure")],
                relation_kinds={eid("reproduction_failure"): "supersedes"},
            ),
            Event(
                id=eid("review_round_2"),
                type="review_round_2",
                time=_date(start, 14, 7),
                params={"requirement": project["review_round2"]},
                visibility=[f"{prefix}.review_2"],
                causal_inputs=[eid("review_round_1"), eid("benchmark_patch")],
                required_inputs=[eid("benchmark_patch")],
                relation_kinds={
                    eid("review_round_1"): "supersedes",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
            Event(
                id=eid("respond_round_2"),
                type="respond_round_2",
                time=_date(start, 15, 9),
                params={"response": project["response_round2"]},
                visibility=[f"{prefix}.response_2"],
                causal_inputs=[eid("review_round_2"), eid("respond_round_1")],
                required_inputs=[eid("review_round_2")],
                relation_kinds={
                    eid("review_round_2"): "derived_from",
                    eid("respond_round_1"): "supersedes",
                },
            ),
            Event(
                id=eid("reproduction_recovery"),
                type="reproduction_recovery",
                time=_date(start, 17, 3),
                params={
                    "run": project["reproduction_success"],
                    "benchmark": project["benchmark_v3"],
                },
                visibility=[f"{prefix}.reproduction_recovery"],
                causal_inputs=[eid("respond_round_2"), eid("benchmark_patch")],
                required_inputs=[eid("respond_round_2"), eid("benchmark_patch")],
                relation_kinds={
                    eid("respond_round_2"): "enables",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
            Event(
                id=eid("submit_revision_3"),
                type="submit_revision_3",
                time=_date(start, 18, 6),
                params={"revision": project["revision_v3"]},
                visibility=[f"{prefix}.revision_3"],
                causal_inputs=[
                    eid("respond_round_2"),
                    eid("reproduction_recovery"),
                ],
                required_inputs=[eid("reproduction_recovery")],
                relation_kinds={
                    eid("respond_round_2"): "derived_from",
                    eid("reproduction_recovery"): "enables",
                },
            ),
            Event(
                id=eid("resolve_review"),
                type="resolve_review",
                time=_date(start, 19, 5),
                params={},
                visibility=[f"{prefix}.review_resolution"],
                causal_inputs=[
                    eid("review_round_2"),
                    eid("respond_round_2"),
                    eid("submit_revision_3"),
                ],
                required_inputs=[eid("respond_round_2"), eid("submit_revision_3")],
                relation_kinds={
                    eid("review_round_2"): "derived_from",
                    eid("respond_round_2"): "derived_from",
                    eid("submit_revision_3"): "enables",
                },
            ),
            Event(
                id=eid("meta_decision"),
                type="meta_decision",
                time=_date(start, 20, 8),
                params={"accepted": True},
                visibility=[f"{prefix}.meta_decision"],
                causal_inputs=[
                    eid("resolve_review"),
                    eid("respond_round_2"),
                    eid("reproduction_recovery"),
                    eid("benchmark_patch"),
                ],
                required_inputs=[
                    eid("resolve_review"),
                    eid("reproduction_recovery"),
                ],
                relation_kinds={
                    eid("resolve_review"): "enables",
                    eid("respond_round_2"): "derived_from",
                    eid("reproduction_recovery"): "enables",
                    eid("benchmark_patch"): "derived_from",
                },
            ),
        ]
    )
    evs.extend(
        grounded_events(
            project,
            prefix,
            start,
            ingest_off=4,
            alt_off=6,
            adopt_off=62,
        )
    )
    evs.extend(
        cascade_events(
            project,
            prefix,
            start,
            seed_off=12,
            ack_off=95,
            reopen_off=248,
            ratify_off=270,
        )
    )
    evs.extend(_experiment_events(project, prefix, start))
    evs.extend(_source_workflow_events(project, prefix))
    blockers = (
        "GPU quota",
        "reviewer latency",
        "dataset mirror",
        "license check",
        "plot regeneration",
    )
    n_pulses = int(project.get("n_pulses", 10))
    for i in range(n_pulses):
        evs.append(
            Event(
                id=eid(f"status_pulse_{i}"),
                type="status_pulse",
                time=_date(start, 0, 9 + i * 11),
                params={
                    "week_index": i + 1,
                    "blocker": blockers[i % len(blockers)],
                    "ticket": f"RL-{pid[:4].upper()}-{200 + i}",
                },
                visibility=[f"{prefix}.status_pulse_{i}"],
                causal_inputs=[eid("report_v1")],
                relation_kinds={eid("report_v1"): "observed_in"},
            )
        )
    evs.sort(key=lambda e: (e.time, e.id))
    _ = pid
    return evs


def simulate_lab(spec: dict[str, Any]) -> dict[str, SimulatedWorld]:
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
                "domain": "researchlab",
            },
            init_values=init_values(project),
            check_preconditions=check_preconditions,
            apply_event=apply_event,
        )
        evs = events_for_lab(project, prefix)
        worlds[prefix] = sim.run(evs)
        worlds[prefix].spec["project"] = project
        worlds[prefix].spec["prefix"] = prefix
        worlds[prefix].spec["domain"] = "researchlab"
    return worlds
