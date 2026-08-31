"""Executable as-of reconstruction over real macroeconomic data vintages."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from longworld.core.provenance import ProvenanceError
from longworld.core.staticreplay import (
    STATIC_REPLAY_PAYLOAD_FIELDS,
    StaticSources,
    audit_static_task,
    build_static_task,
    evidence_available,
    fact_values,
    relation_between,
    relation_evidence_fact_ids,
    replay_static_task,
    validate_counterfactual,
)

MACRO_VINTAGE_SOURCE_SCHEMA = "longworld.macro-vintage-source.v1"
MACRO_VINTAGE_TASK_SCHEMA = "longworld.macro-vintage-task.v1"
MACRO_VINTAGE_ADAPTER_ID = "macro.gdp_vintage_reconstruction.v1"
MACRO_VINTAGE_REPLAY_REVISION = "longworld.macro-vintage-replay.v1"
MACRO_VINTAGE_ANSWER_PROGRAM = "macro.value_known_as_of_revision.v1"
MACRO_VINTAGE_TASK_REPLAY_ADAPTER = (
    MACRO_VINTAGE_ADAPTER_ID,
    MACRO_VINTAGE_REPLAY_REVISION,
    "longworld.task-replay-sidecar.v1",
)
MACRO_VINTAGE_REPLAY_PAYLOAD_FIELDS = STATIC_REPLAY_PAYLOAD_FIELDS

_QUERY_FIELDS = {"series_id", "period", "decision_date"}
_FACT_FIELDS = {"series_id", "period", "vintage_date", "available_at", "value"}


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ProvenanceError(f"macro vintage {label} is invalid") from error


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ProvenanceError("macro vintage value is invalid") from error
    if not parsed.is_finite():
        raise ProvenanceError("macro vintage value is invalid")
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _evaluate(
    task: dict[str, Any], sources: StaticSources, counterfactual: bool
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    query = task.get("query")
    if not isinstance(query, dict) or set(query) != _QUERY_FIELDS:
        raise ProvenanceError("macro vintage query schema is invalid")
    series_id = str(query.get("series_id") or "").strip()
    period = str(query.get("period") or "").strip()
    decision_date = _parse_date(str(query.get("decision_date") or ""), "decision date")
    if not series_id or not period:
        raise ProvenanceError("macro vintage query identity is invalid")
    twin = validate_counterfactual(task.get("counterfactual_twin"), sources)
    observations: list[tuple[date, str, dict[str, str]]] = []
    values_by_record: dict[str, dict[str, str]] = {}
    seen_keys: set[tuple[str, str, str]] = set()
    for record_id, record in sources.records.items():
        if record.get("record_kind") != "macro_vintage_observation":
            raise ProvenanceError("macro vintage record kind is invalid")
        values = fact_values(record, _FACT_FIELDS, twin if counterfactual else None)
        vintage_date = _parse_date(values["vintage_date"], "vintage date")
        available_at = _parse_date(values["available_at"], "availability date")
        if (
            available_at != vintage_date
            or str(record.get("occurred_at")) != values["available_at"]
            or (values["series_id"], values["period"], values["available_at"])
            in seen_keys
        ):
            raise ProvenanceError("macro vintage observation identity is invalid")
        _decimal(values["value"])
        values_by_record[record_id] = values
        seen_keys.add((values["series_id"], values["period"], values["available_at"]))
        if (
            values["series_id"] == series_id
            and values["period"] == period
            and available_at <= decision_date
        ):
            observations.append((available_at, record_id, values))
    for relation in sources.relations.values():
        source_id = str(relation.get("source_record_id") or "")
        target_id = str(relation.get("target_record_id") or "")
        source_values = values_by_record[source_id]
        target_values = values_by_record[target_id]
        if (
            relation.get("kind") != "revises_observation"
            or relation.get("relation_provenance")
            != "verified_derived_temporal_same_series"
            or source_values["series_id"] != target_values["series_id"]
            or source_values["period"] != target_values["period"]
            or _parse_date(source_values["available_at"], "availability date")
            <= _parse_date(target_values["available_at"], "availability date")
            or relation_evidence_fact_ids(relation)
            != {
                source_id: {f"{source_id}:value"},
                target_id: {f"{target_id}:value"},
            }
        ):
            raise ProvenanceError("macro vintage revision relation is invalid")
    observations.sort(key=lambda item: (item[0], item[1]))
    if len(observations) < 2:
        raise ProvenanceError(
            "macro vintage history has fewer than two available vintages"
        )
    prior_date, prior_id, prior = observations[-2]
    current_date, current_id, current = observations[-1]
    relation = relation_between(
        sources,
        kind="revises_observation",
        source_id=current_id,
        target_id=prior_id,
    )
    essentials = (prior_id, current_id, str(relation["relation_id"]))
    if (
        twin.get("record_id") != current_id
        or twin.get("fact_id") != f"{current_id}:value"
    ):
        raise ProvenanceError(
            "macro vintage counterfactual must replace the current value"
        )
    if not evidence_available(task, essentials):
        return None, essentials
    prior_value = _decimal(prior["value"])
    current_value = _decimal(current["value"])
    return (
        {
            "series_id": series_id,
            "period": period,
            "decision_date": decision_date.isoformat(),
            "prior_vintage_record_id": prior_id,
            "prior_vintage_date": prior_date.isoformat(),
            "prior_value": prior["value"],
            "current_vintage_record_id": current_id,
            "current_vintage_date": current_date.isoformat(),
            "current_value": current["value"],
            "revision_delta": _decimal_text(current_value - prior_value),
            "source_relation_id": relation["relation_id"],
        },
        essentials,
    )


def build_macro_vintage_task(payload: dict[str, Any]) -> dict[str, Any]:
    return build_static_task(
        payload,
        source_schema=MACRO_VINTAGE_SOURCE_SCHEMA,
        task_schema=MACRO_VINTAGE_TASK_SCHEMA,
        domain="macro_economics",
        adapter_id=MACRO_VINTAGE_ADAPTER_ID,
        replay_revision=MACRO_VINTAGE_REPLAY_REVISION,
        answer_program_id=MACRO_VINTAGE_ANSWER_PROGRAM,
        evaluator=_evaluate,
    )


def replay_macro_vintage_task(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_ids: list[str] | None = None,
) -> str:
    return replay_static_task(
        task,
        source_schema=MACRO_VINTAGE_SOURCE_SCHEMA,
        task_schema=MACRO_VINTAGE_TASK_SCHEMA,
        domain="macro_economics",
        adapter_id=MACRO_VINTAGE_ADAPTER_ID,
        replay_revision=MACRO_VINTAGE_REPLAY_REVISION,
        answer_program_id=MACRO_VINTAGE_ANSWER_PROGRAM,
        evaluator=_evaluate,
        counterfactual=counterfactual,
        evidence_ids=evidence_ids,
    )


def audit_macro_vintage_task(task: dict[str, Any]) -> dict[str, bool]:
    return audit_static_task(task, replay_macro_vintage_task)
