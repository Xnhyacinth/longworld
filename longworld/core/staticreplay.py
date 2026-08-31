"""Shared fail-closed primitives for small static source-bound replay adapters."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from longworld.core.provenance import ProvenanceError

STATIC_SOURCE_FIELDS = {
    "schema_version",
    "world_id",
    "source_records",
    "source_relations",
    "query",
    "counterfactual_twin",
}
STATIC_TASK_FIELDS = {
    "schema_version",
    "data_stage",
    "train_ready",
    "production_eligible",
    "promotion_eligible",
    "promoted",
    "complete_world",
    "generation_integration",
    "domain",
    "world_id",
    "adapter_id",
    "strict_replay_revision",
    "answer_program_id",
    "source_records",
    "source_relations",
    "query",
    "counterfactual_twin",
    "source_payload_sha256",
    "state",
    "answer",
    "cf_answer",
    "essential_evidence_ids",
}
STATIC_REPLAY_PAYLOAD_FIELDS = frozenset(
    {
        "signed_manifest_sha256",
        "source_families",
        "authorization_record_id",
        "replay_revision",
        "tokenizer_model_id",
        "tokenizer_revision",
        "tokenizer_asset_manifest_sha256",
        "candidate_content_commitments",
    }
)

_RECORD_FIELDS = {
    "record_id",
    "record_kind",
    "occurred_at",
    "source_family",
    "source_origin",
    "source_url",
    "retrieval_url",
    "source_sha256",
    "text_sha256",
    "text",
    "facts",
}
_FACT_FIELDS = {
    "fact_id",
    "field",
    "value",
    "evidence_quote",
    "char_start",
    "char_end",
}
_RELATION_FIELDS = {
    "relation_id",
    "kind",
    "source_record_id",
    "target_record_id",
    "relation_provenance",
    "evidence",
}
_RELATION_EVIDENCE_FIELDS = {"record_id", "fact_ids"}
_TWIN_FIELDS = {
    "record_id",
    "fact_id",
    "parent_value",
    "value",
    "source_origin",
    "provenance_operation",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_RELATION_PROVENANCE = {
    "source_declared",
    "verified_derived_cross_source",
    "verified_derived_temporal_same_series",
}
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


@dataclass(frozen=True)
class StaticSources:
    records: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    facts: dict[str, tuple[str, dict[str, Any]]]


Evaluation = tuple[dict[str, Any] | None, tuple[str, ...]]
Evaluator = Callable[[dict[str, Any], StaticSources, bool], Evaluation]


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProvenanceError(f"static replay {label} is invalid")
    return value


def _https_url(value: object, label: str) -> str:
    normalized = _nonempty(value, label)
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise ProvenanceError(f"static replay {label} is invalid")
    return normalized


def _validate_text_source(text: str) -> None:
    if any(ord(character) < 32 and character not in "\t\n\r" for character in text):
        raise ProvenanceError("static replay binary source body is invalid")
    compact = "".join(text.split())
    if len(compact) < 1_024 or len(compact) % 4 or _BASE64.fullmatch(compact) is None:
        return
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return
    if len(decoded) >= 768:
        raise ProvenanceError("static replay base64 source body is forbidden")


def validate_sources(records_value: object, relations_value: object) -> StaticSources:
    if not isinstance(records_value, list) or len(records_value) < 2:
        raise ProvenanceError("static replay source records are invalid")
    records: dict[str, dict[str, Any]] = {}
    facts: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw_record in records_value:
        if not isinstance(raw_record, dict) or set(raw_record) != _RECORD_FIELDS:
            raise ProvenanceError("static replay source record schema is invalid")
        record_id = _nonempty(raw_record.get("record_id"), "record identity")
        text = _nonempty(raw_record.get("text"), "record body")
        _validate_text_source(text)
        if (
            record_id in records
            or raw_record.get("source_origin")
            not in {"real_public", "real_private_export"}
            or _SHA256.fullmatch(str(raw_record.get("source_sha256") or "")) is None
            or raw_record.get("text_sha256")
            != hashlib.sha256(text.encode()).hexdigest()
        ):
            raise ProvenanceError("static replay source record provenance is invalid")
        for field in ("record_kind", "occurred_at", "source_family"):
            _nonempty(raw_record.get(field), f"record {field}")
        _https_url(raw_record.get("source_url"), "source URL")
        _https_url(raw_record.get("retrieval_url"), "retrieval URL")
        raw_facts = raw_record.get("facts")
        if not isinstance(raw_facts, list) or not raw_facts:
            raise ProvenanceError("static replay source facts are invalid")
        fields: set[str] = set()
        for raw_fact in raw_facts:
            if not isinstance(raw_fact, dict) or set(raw_fact) != _FACT_FIELDS:
                raise ProvenanceError("static replay source fact schema is invalid")
            fact_id = _nonempty(raw_fact.get("fact_id"), "fact identity")
            field = _nonempty(raw_fact.get("field"), "fact field")
            value = _nonempty(raw_fact.get("value"), "fact value")
            quote = _nonempty(raw_fact.get("evidence_quote"), "fact evidence")
            start = raw_fact.get("char_start")
            end = raw_fact.get("char_end")
            if (
                fact_id in facts
                or field in fields
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end != start + len(quote)
                or text[start:end] != quote
                or quote.count(value) != 1
            ):
                raise ProvenanceError("static replay source fact binding is invalid")
            facts[fact_id] = (record_id, raw_fact)
            fields.add(field)
        records[record_id] = raw_record
    if not isinstance(relations_value, list) or not relations_value:
        raise ProvenanceError("static replay source relations are invalid")
    relations: dict[str, dict[str, Any]] = {}
    for raw_relation in relations_value:
        if not isinstance(raw_relation, dict) or set(raw_relation) != _RELATION_FIELDS:
            raise ProvenanceError("static replay source relation schema is invalid")
        relation_id = _nonempty(raw_relation.get("relation_id"), "relation identity")
        source_id = str(raw_relation.get("source_record_id") or "")
        target_id = str(raw_relation.get("target_record_id") or "")
        if (
            relation_id in relations
            or source_id not in records
            or target_id not in records
            or source_id == target_id
            or raw_relation.get("relation_provenance")
            not in _ALLOWED_RELATION_PROVENANCE
        ):
            raise ProvenanceError("static replay source relation is invalid")
        _nonempty(raw_relation.get("kind"), "relation kind")
        evidence = raw_relation.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != 2:
            raise ProvenanceError("static replay relation evidence is invalid")
        evidence_records: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict) or set(item) != _RELATION_EVIDENCE_FIELDS:
                raise ProvenanceError("static replay relation evidence is invalid")
            record_id = str(item.get("record_id") or "")
            fact_ids = item.get("fact_ids")
            if (
                record_id in evidence_records
                or record_id not in {source_id, target_id}
                or not isinstance(fact_ids, list)
                or not fact_ids
                or fact_ids != sorted(set(fact_ids))
                or any(
                    fact_id not in facts or facts[fact_id][0] != record_id
                    for fact_id in fact_ids
                )
            ):
                raise ProvenanceError("static replay relation evidence is invalid")
            evidence_records.add(record_id)
        if evidence_records != {source_id, target_id}:
            raise ProvenanceError("static replay relation endpoints are unbound")
        relations[relation_id] = raw_relation
    return StaticSources(records=records, relations=relations, facts=facts)


def fact_map(record: Mapping[str, Any], expected_fields: set[str]) -> dict[str, str]:
    values = {
        str(item["field"]): str(item["value"])
        for item in record.get("facts", [])
        if isinstance(item, dict)
    }
    if set(values) != expected_fields:
        raise ProvenanceError("static replay domain fact schema is invalid")
    return values


def validate_counterfactual(twin: object, sources: StaticSources) -> dict[str, Any]:
    if not isinstance(twin, dict) or set(twin) != _TWIN_FIELDS:
        raise ProvenanceError("static replay counterfactual schema is invalid")
    record_id = str(twin.get("record_id") or "")
    fact_id = str(twin.get("fact_id") or "")
    fact_binding = sources.facts.get(fact_id)
    if (
        record_id not in sources.records
        or fact_binding is None
        or fact_binding[0] != record_id
        or twin.get("parent_value") != fact_binding[1].get("value")
        or not isinstance(twin.get("value"), str)
        or not twin["value"]
        or twin.get("value") == twin.get("parent_value")
        or twin.get("source_origin") != "synthetic_counterfactual"
        or twin.get("provenance_operation") != "replace_exact_fact"
    ):
        raise ProvenanceError("static replay counterfactual binding is invalid")
    return twin


def fact_values(
    record: Mapping[str, Any],
    expected_fields: set[str],
    twin: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    values = fact_map(record, expected_fields)
    if twin is not None and twin.get("record_id") == record.get("record_id"):
        matches = [
            item
            for item in record.get("facts", [])
            if isinstance(item, dict) and item.get("fact_id") == twin.get("fact_id")
        ]
        if len(matches) != 1:
            raise ProvenanceError("static replay counterfactual fact is invalid")
        values[str(matches[0]["field"])] = str(twin["value"])
    return values


def selected_evidence(task: Mapping[str, Any]) -> set[str] | None:
    value = task.get("_selected_evidence_ids")
    if value is None:
        return None
    return set(value)


def evidence_available(task: Mapping[str, Any], essentials: Sequence[str]) -> bool:
    selected = selected_evidence(task)
    return selected is None or set(essentials) <= selected


def relation_between(
    sources: StaticSources,
    *,
    kind: str,
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    matches = [
        relation
        for relation in sources.relations.values()
        if relation.get("kind") == kind
        and relation.get("source_record_id") == source_id
        and relation.get("target_record_id") == target_id
    ]
    if len(matches) != 1:
        raise ProvenanceError("static replay required source relation is missing")
    return matches[0]


def relation_evidence_fact_ids(
    relation: Mapping[str, Any],
) -> dict[str, set[str]]:
    evidence = relation.get("evidence")
    if not isinstance(evidence, list):  # already checked; retain fail-closed API
        raise ProvenanceError("static replay relation evidence is invalid")
    return {
        str(item["record_id"]): set(item["fact_ids"])
        for item in evidence
        if isinstance(item, dict)
    }


def _source_projection(task: Mapping[str, Any], source_schema: str) -> dict[str, Any]:
    return {
        "schema_version": source_schema,
        "world_id": task["world_id"],
        "source_records": task["source_records"],
        "source_relations": task["source_relations"],
        "query": task["query"],
        "counterfactual_twin": task["counterfactual_twin"],
    }


def _validate_source_payload(payload: object, source_schema: str) -> StaticSources:
    if (
        not isinstance(payload, dict)
        or set(payload) != STATIC_SOURCE_FIELDS
        or payload.get("schema_version") != source_schema
    ):
        raise ProvenanceError("static replay source payload schema is invalid")
    _nonempty(payload.get("world_id"), "world identity")
    if not isinstance(payload.get("query"), dict):
        raise ProvenanceError("static replay query schema is invalid")
    sources = validate_sources(
        payload.get("source_records"), payload.get("source_relations")
    )
    validate_counterfactual(payload.get("counterfactual_twin"), sources)
    return sources


def build_static_task(
    payload: dict[str, Any],
    *,
    source_schema: str,
    task_schema: str,
    domain: str,
    adapter_id: str,
    replay_revision: str,
    answer_program_id: str,
    evaluator: Evaluator,
) -> dict[str, Any]:
    sources = _validate_source_payload(payload, source_schema)
    base = {
        "schema_version": task_schema,
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "promoted": False,
        "complete_world": False,
        "generation_integration": "disabled",
        "domain": domain,
        "world_id": payload["world_id"],
        "adapter_id": adapter_id,
        "strict_replay_revision": replay_revision,
        "answer_program_id": answer_program_id,
        "source_records": deepcopy(payload["source_records"]),
        "source_relations": deepcopy(payload["source_relations"]),
        "query": deepcopy(payload["query"]),
        "counterfactual_twin": deepcopy(payload["counterfactual_twin"]),
        "source_payload_sha256": canonical_sha256(payload),
    }
    state, essentials = evaluator(base, sources, False)
    cf_state, cf_essentials = evaluator(base, sources, True)
    if (
        state is None
        or cf_state is None
        or not essentials
        or essentials != cf_essentials
        or len(essentials) != len(set(essentials))
    ):
        raise ProvenanceError("static replay task is not executable")
    answer = canonical_json(state)
    cf_answer = canonical_json(cf_state)
    if answer == cf_answer:
        raise ProvenanceError("static replay counterfactual does not change the answer")
    return {
        **base,
        "state": state,
        "answer": answer,
        "cf_answer": cf_answer,
        "essential_evidence_ids": list(essentials),
    }


def replay_static_task(
    task: dict[str, Any],
    *,
    source_schema: str,
    task_schema: str,
    domain: str,
    adapter_id: str,
    replay_revision: str,
    answer_program_id: str,
    evaluator: Evaluator,
    counterfactual: bool = False,
    evidence_ids: Sequence[str] | None = None,
) -> str:
    if (
        not isinstance(task, dict)
        or set(task) != STATIC_TASK_FIELDS
        or task.get("schema_version") != task_schema
        or task.get("data_stage") != "candidate"
        or task.get("train_ready") is not False
        or task.get("production_eligible") is not False
        or task.get("promotion_eligible") is not False
        or task.get("promoted") is not False
        or task.get("complete_world") is not False
        or task.get("generation_integration") != "disabled"
        or task.get("domain") != domain
        or task.get("adapter_id") != adapter_id
        or task.get("strict_replay_revision") != replay_revision
        or task.get("answer_program_id") != answer_program_id
    ):
        raise ProvenanceError("static replay task schema or identity is invalid")
    projection = _source_projection(task, source_schema)
    sources = _validate_source_payload(projection, source_schema)
    if task.get("source_payload_sha256") != canonical_sha256(projection):
        raise ProvenanceError("static replay source payload digest is invalid")
    base_state, essentials = evaluator(task, sources, False)
    cf_state, cf_essentials = evaluator(task, sources, True)
    if (
        base_state is None
        or cf_state is None
        or essentials != cf_essentials
        or task.get("state") != base_state
        or task.get("answer") != canonical_json(base_state)
        or task.get("cf_answer") != canonical_json(cf_state)
        or task.get("essential_evidence_ids") != list(essentials)
    ):
        raise ProvenanceError("static replay state or answer binding is invalid")
    if evidence_ids is not None:
        if (
            not isinstance(evidence_ids, Sequence)
            or isinstance(evidence_ids, (str, bytes))
            or any(not isinstance(item, str) or not item for item in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            raise ProvenanceError("static replay evidence selection is invalid")
        known = set(sources.records) | set(sources.relations)
        if not set(evidence_ids) <= known:
            raise ProvenanceError("static replay evidence selection is unknown")
        selected = set(evidence_ids)
        selected_records = {
            record_id: record
            for record_id, record in sources.records.items()
            if record_id in selected
        }
        selected_relations = {
            relation_id: relation
            for relation_id, relation in sources.relations.items()
            if relation_id in selected
            and relation["source_record_id"] in selected_records
            and relation["target_record_id"] in selected_records
        }
        selected_facts = {
            fact_id: binding
            for fact_id, binding in sources.facts.items()
            if binding[0] in selected_records
        }
        sources = StaticSources(
            records=selected_records,
            relations=selected_relations,
            facts=selected_facts,
        )
    selected_task = dict(task)
    selected_task["_selected_evidence_ids"] = (
        None if evidence_ids is None else tuple(evidence_ids)
    )
    try:
        state, _ = evaluator(selected_task, sources, counterfactual)
    except ProvenanceError:
        if evidence_ids is None:
            raise
        return "unknown"
    return "unknown" if state is None else canonical_json(state)


def audit_static_task(
    task: dict[str, Any], replay: Callable[..., str]
) -> dict[str, bool]:
    answer = replay(task)
    cf_answer = replay(task, counterfactual=True)
    essentials = task.get("essential_evidence_ids")
    if not isinstance(essentials, list) or not essentials:
        raise ProvenanceError("static replay essential evidence is invalid")
    removals = [
        replay(task, evidence_ids=[item for item in essentials if item != removed])
        for removed in essentials
    ]
    singletons = [replay(task, evidence_ids=[item]) for item in essentials]
    return {
        "strict_replay_matches_answer": answer == task.get("answer"),
        "state_replayed_from_sources": answer == canonical_json(task.get("state")),
        "counterfactual_replay_matches_cf_answer": cf_answer == task.get("cf_answer"),
        "counterfactual_changes_answer": cf_answer != answer,
        "remove_one_fails": all(value != answer for value in removals),
        "single_essential_insufficient": all(value != answer for value in singletons),
        "source_relations_replayed": any(
            item
            in {relation.get("relation_id") for relation in task["source_relations"]}
            for item in essentials
        ),
    }
