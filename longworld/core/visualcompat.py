"""Executable compatibility checks over model and component source revisions."""

from __future__ import annotations

import re
from datetime import date
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

VISUAL_COMPAT_SOURCE_SCHEMA = "longworld.visual-compat-source.v1"
VISUAL_COMPAT_TASK_SCHEMA = "longworld.visual-compat-task.v1"
VISUAL_COMPAT_ADAPTER_ID = "visual.model_pipeline_compatibility.v1"
VISUAL_COMPAT_REPLAY_REVISION = "longworld.visual-compat-replay.v1"
VISUAL_COMPAT_ANSWER_PROGRAM = "visual.compatible_pipeline_at_cutoff.v1"
VISUAL_COMPAT_TASK_REPLAY_ADAPTER = (
    VISUAL_COMPAT_ADAPTER_ID,
    VISUAL_COMPAT_REPLAY_REVISION,
    "longworld.task-replay-sidecar.v1",
)
VISUAL_COMPAT_REPLAY_PAYLOAD_FIELDS = STATIC_REPLAY_PAYLOAD_FIELDS

_QUERY_FIELDS = {"model_record_id", "release_record_id", "decision_date"}
_MODEL_FACTS = {
    "model_id",
    "model_revision",
    "component_name",
    "min_component_version",
    "max_component_version",
    "available_at",
}
_RELEASE_FACTS = {"component_name", "component_version", "released_at"}
_VERSION = re.compile(r"^(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){1,3}$")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ProvenanceError("visual compatibility date is invalid") from error


def _version(value: str) -> tuple[int, ...]:
    if _VERSION.fullmatch(value) is None:
        raise ProvenanceError("visual compatibility version is invalid")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _evaluate(
    task: dict[str, Any], sources: StaticSources, counterfactual: bool
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    query = task.get("query")
    if not isinstance(query, dict) or set(query) != _QUERY_FIELDS:
        raise ProvenanceError("visual compatibility query schema is invalid")
    model_id = str(query.get("model_record_id") or "")
    release_id = str(query.get("release_record_id") or "")
    model = sources.records.get(model_id)
    release = sources.records.get(release_id)
    if model is None or release is None:
        raise ProvenanceError("visual compatibility source roles are invalid")
    values_by_record: dict[str, dict[str, str]] = {}
    for record_id, record in sources.records.items():
        if record.get("record_kind") == "visual_model_revision":
            values = fact_values(record, _MODEL_FACTS)
            if str(record.get("occurred_at")) != values["available_at"] or _version(
                values["min_component_version"]
            ) > _version(values["max_component_version"]):
                raise ProvenanceError("visual compatibility model record is invalid")
            _date(values["available_at"])
        elif record.get("record_kind") == "visual_component_release":
            values = fact_values(record, _RELEASE_FACTS)
            if str(record.get("occurred_at")) != values["released_at"]:
                raise ProvenanceError("visual compatibility release record is invalid")
            _version(values["component_version"])
            _date(values["released_at"])
        else:
            raise ProvenanceError("visual compatibility source roles are invalid")
        values_by_record[record_id] = values
    for item in sources.relations.values():
        source_id = str(item.get("source_record_id") or "")
        target_id = str(item.get("target_record_id") or "")
        source = sources.records[source_id]
        target = sources.records[target_id]
        if (
            item.get("kind") != "compatibility_evaluated_against"
            or item.get("relation_provenance") != "verified_derived_cross_source"
            or source.get("record_kind") != "visual_model_revision"
            or target.get("record_kind") != "visual_component_release"
            or values_by_record[source_id]["component_name"]
            != values_by_record[target_id]["component_name"]
            or relation_evidence_fact_ids(item)
            != {
                source_id: {
                    f"{source_id}:min_component_version",
                    f"{source_id}:max_component_version",
                },
                target_id: {f"{target_id}:component_version"},
            }
        ):
            raise ProvenanceError("visual compatibility source relation is invalid")
    if (
        model.get("record_kind") != "visual_model_revision"
        or release.get("record_kind") != "visual_component_release"
    ):
        raise ProvenanceError("visual compatibility source roles are invalid")
    decision_date = _date(str(query.get("decision_date") or ""))
    twin = validate_counterfactual(task.get("counterfactual_twin"), sources)
    model_values = fact_values(model, _MODEL_FACTS)
    release_values = fact_values(
        release, _RELEASE_FACTS, twin if counterfactual else None
    )
    min_version = _version(model_values["min_component_version"])
    max_version = _version(model_values["max_component_version"])
    component_version = _version(release_values["component_version"])
    if (
        min_version > max_version
        or model_values["component_name"] != release_values["component_name"]
        or str(model.get("occurred_at")) != model_values["available_at"]
        or str(release.get("occurred_at")) != release_values["released_at"]
        or _date(model_values["available_at"]) > decision_date
        or _date(release_values["released_at"]) > decision_date
    ):
        raise ProvenanceError("visual compatibility source state is inconsistent")
    relation = relation_between(
        sources,
        kind="compatibility_evaluated_against",
        source_id=model_id,
        target_id=release_id,
    )
    essentials = (model_id, release_id, str(relation["relation_id"]))
    if (
        twin.get("record_id") != release_id
        or twin.get("fact_id") != f"{release_id}:component_version"
    ):
        raise ProvenanceError("visual counterfactual must replace component version")
    if not evidence_available(task, essentials):
        return None, essentials
    return (
        {
            "decision_date": decision_date.isoformat(),
            "model_id": model_values["model_id"],
            "model_revision": model_values["model_revision"],
            "component_name": model_values["component_name"],
            "component_version": release_values["component_version"],
            "min_component_version": model_values["min_component_version"],
            "max_component_version": model_values["max_component_version"],
            "compatible": min_version <= component_version <= max_version,
            "source_relation_id": relation["relation_id"],
        },
        essentials,
    )


def build_visual_compatibility_task(payload: dict[str, Any]) -> dict[str, Any]:
    return build_static_task(
        payload,
        source_schema=VISUAL_COMPAT_SOURCE_SCHEMA,
        task_schema=VISUAL_COMPAT_TASK_SCHEMA,
        domain="visual_models",
        adapter_id=VISUAL_COMPAT_ADAPTER_ID,
        replay_revision=VISUAL_COMPAT_REPLAY_REVISION,
        answer_program_id=VISUAL_COMPAT_ANSWER_PROGRAM,
        evaluator=_evaluate,
    )


def replay_visual_compatibility_task(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_ids: list[str] | None = None,
) -> str:
    return replay_static_task(
        task,
        source_schema=VISUAL_COMPAT_SOURCE_SCHEMA,
        task_schema=VISUAL_COMPAT_TASK_SCHEMA,
        domain="visual_models",
        adapter_id=VISUAL_COMPAT_ADAPTER_ID,
        replay_revision=VISUAL_COMPAT_REPLAY_REVISION,
        answer_program_id=VISUAL_COMPAT_ANSWER_PROGRAM,
        evaluator=_evaluate,
        counterfactual=counterfactual,
        evidence_ids=evidence_ids,
    )


def audit_visual_compatibility_task(task: dict[str, Any]) -> dict[str, bool]:
    return audit_static_task(task, replay_visual_compatibility_task)
