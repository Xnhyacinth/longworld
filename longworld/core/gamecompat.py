"""Executable release, save-schema, and protocol compatibility replay."""

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

GAME_COMPAT_SOURCE_SCHEMA = "longworld.game-compat-source.v1"
GAME_COMPAT_TASK_SCHEMA = "longworld.game-compat-task.v1"
GAME_COMPAT_ADAPTER_ID = "game.release_compatibility.v1"
GAME_COMPAT_REPLAY_REVISION = "longworld.game-compat-replay.v1"
GAME_COMPAT_ANSWER_PROGRAM = "game.release_save_protocol_compatibility.v1"
GAME_COMPAT_TASK_REPLAY_ADAPTER = (
    GAME_COMPAT_ADAPTER_ID,
    GAME_COMPAT_REPLAY_REVISION,
    "longworld.task-replay-sidecar.v1",
)
GAME_COMPAT_REPLAY_PAYLOAD_FIELDS = STATIC_REPLAY_PAYLOAD_FIELDS

_QUERY_FIELDS = {
    "release_record_id",
    "save_record_id",
    "peer_record_id",
    "decision_date",
}
_RELEASE_FACTS = {
    "project",
    "version",
    "released_at",
    "min_save_schema",
    "max_save_schema",
    "protocol_version",
}
_SAVE_FACTS = {"project", "save_id", "save_schema", "created_by_release", "observed_at"}
_PEER_FACTS = {"project", "endpoint_id", "protocol_version", "observed_at"}
_VERSION = re.compile(r"^(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){1,3}$")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ProvenanceError("game compatibility date is invalid") from error


def _integer(value: str, label: str) -> int:
    if not value.isdigit():
        raise ProvenanceError(f"game compatibility {label} is invalid")
    return int(value)


def _evaluate(
    task: dict[str, Any], sources: StaticSources, counterfactual: bool
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    query = task.get("query")
    if not isinstance(query, dict) or set(query) != _QUERY_FIELDS:
        raise ProvenanceError("game compatibility query schema is invalid")
    release_id = str(query.get("release_record_id") or "")
    save_id = str(query.get("save_record_id") or "")
    peer_id = str(query.get("peer_record_id") or "")
    release = sources.records.get(release_id)
    save = sources.records.get(save_id)
    peer = sources.records.get(peer_id)
    if release is None or save is None or peer is None:
        raise ProvenanceError("game compatibility source roles are invalid")
    values_by_record: dict[str, dict[str, str]] = {}
    for record_id, record in sources.records.items():
        kind = record.get("record_kind")
        if kind == "game_release":
            values = fact_values(record, _RELEASE_FACTS)
            if (
                _VERSION.fullmatch(values["version"]) is None
                or str(record.get("occurred_at")) != values["released_at"]
            ):
                raise ProvenanceError("game compatibility release record is invalid")
            _date(values["released_at"])
            for field in ("min_save_schema", "max_save_schema", "protocol_version"):
                _integer(values[field], field)
        elif kind == "game_save_observation":
            values = fact_values(record, _SAVE_FACTS)
            if str(record.get("occurred_at")) != values["observed_at"]:
                raise ProvenanceError("game compatibility save record is invalid")
            _date(values["observed_at"])
            _integer(values["save_schema"], "save schema")
        elif kind == "game_protocol_observation":
            values = fact_values(record, _PEER_FACTS)
            if str(record.get("occurred_at")) != values["observed_at"]:
                raise ProvenanceError("game compatibility protocol record is invalid")
            _date(values["observed_at"])
            _integer(values["protocol_version"], "protocol version")
        else:
            raise ProvenanceError("game compatibility source roles are invalid")
        values_by_record[record_id] = values
    allowed_relation_kinds = {
        "save_compatibility_evaluated_against",
        "protocol_compatibility_evaluated_against",
    }
    for item in sources.relations.values():
        source_id = str(item.get("source_record_id") or "")
        target_id = str(item.get("target_record_id") or "")
        target_kind = sources.records[target_id].get("record_kind")
        expected_target_kind = (
            "game_save_observation"
            if item.get("kind") == "save_compatibility_evaluated_against"
            else "game_protocol_observation"
        )
        source_fields = (
            {f"{source_id}:min_save_schema", f"{source_id}:max_save_schema"}
            if expected_target_kind == "game_save_observation"
            else {f"{source_id}:protocol_version"}
        )
        target_field = (
            f"{target_id}:save_schema"
            if expected_target_kind == "game_save_observation"
            else f"{target_id}:protocol_version"
        )
        if (
            item.get("kind") not in allowed_relation_kinds
            or item.get("relation_provenance") != "verified_derived_cross_source"
            or sources.records[source_id].get("record_kind") != "game_release"
            or target_kind != expected_target_kind
            or values_by_record[source_id]["project"]
            != values_by_record[target_id]["project"]
            or relation_evidence_fact_ids(item)
            != {source_id: source_fields, target_id: {target_field}}
        ):
            raise ProvenanceError("game compatibility source relation is invalid")
    if (
        release.get("record_kind") != "game_release"
        or save.get("record_kind") != "game_save_observation"
        or peer.get("record_kind") != "game_protocol_observation"
    ):
        raise ProvenanceError("game compatibility source roles are invalid")
    twin = validate_counterfactual(task.get("counterfactual_twin"), sources)
    release_values = fact_values(
        release, _RELEASE_FACTS, twin if counterfactual else None
    )
    save_values = fact_values(save, _SAVE_FACTS)
    peer_values = fact_values(peer, _PEER_FACTS)
    decision_date = _date(str(query.get("decision_date") or ""))
    if (
        len(
            {
                release_values["project"],
                save_values["project"],
                peer_values["project"],
            }
        )
        != 1
        or str(release.get("occurred_at")) != release_values["released_at"]
        or str(save.get("occurred_at")) != save_values["observed_at"]
        or str(peer.get("occurred_at")) != peer_values["observed_at"]
        or max(
            _date(release_values["released_at"]),
            _date(save_values["observed_at"]),
            _date(peer_values["observed_at"]),
        )
        > decision_date
    ):
        raise ProvenanceError("game compatibility source state is inconsistent")
    min_save = _integer(release_values["min_save_schema"], "minimum save schema")
    max_save = _integer(release_values["max_save_schema"], "maximum save schema")
    save_schema = _integer(save_values["save_schema"], "save schema")
    release_protocol = _integer(release_values["protocol_version"], "release protocol")
    peer_protocol = _integer(peer_values["protocol_version"], "peer protocol")
    if min_save > max_save:
        raise ProvenanceError("game compatibility save schema range is invalid")
    save_relation = relation_between(
        sources,
        kind="save_compatibility_evaluated_against",
        source_id=release_id,
        target_id=save_id,
    )
    protocol_relation = relation_between(
        sources,
        kind="protocol_compatibility_evaluated_against",
        source_id=release_id,
        target_id=peer_id,
    )
    essentials = (
        release_id,
        save_id,
        peer_id,
        str(save_relation["relation_id"]),
        str(protocol_relation["relation_id"]),
    )
    if (
        twin.get("record_id") != release_id
        or twin.get("fact_id") != f"{release_id}:protocol_version"
    ):
        raise ProvenanceError("game counterfactual must replace release protocol")
    if not evidence_available(task, essentials):
        return None, essentials
    save_compatible = min_save <= save_schema <= max_save
    protocol_compatible = release_protocol == peer_protocol
    return (
        {
            "decision_date": decision_date.isoformat(),
            "project": release_values["project"],
            "release_version": release_values["version"],
            "save_id": save_values["save_id"],
            "save_schema": save_values["save_schema"],
            "save_compatible": save_compatible,
            "peer_endpoint_id": peer_values["endpoint_id"],
            "release_protocol_version": release_values["protocol_version"],
            "peer_protocol_version": peer_values["protocol_version"],
            "protocol_compatible": protocol_compatible,
            "overall_compatible": save_compatible and protocol_compatible,
            "source_relation_ids": [
                save_relation["relation_id"],
                protocol_relation["relation_id"],
            ],
        },
        essentials,
    )


def build_game_compatibility_task(payload: dict[str, Any]) -> dict[str, Any]:
    return build_static_task(
        payload,
        source_schema=GAME_COMPAT_SOURCE_SCHEMA,
        task_schema=GAME_COMPAT_TASK_SCHEMA,
        domain="open_source_games",
        adapter_id=GAME_COMPAT_ADAPTER_ID,
        replay_revision=GAME_COMPAT_REPLAY_REVISION,
        answer_program_id=GAME_COMPAT_ANSWER_PROGRAM,
        evaluator=_evaluate,
    )


def replay_game_compatibility_task(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_ids: list[str] | None = None,
) -> str:
    return replay_static_task(
        task,
        source_schema=GAME_COMPAT_SOURCE_SCHEMA,
        task_schema=GAME_COMPAT_TASK_SCHEMA,
        domain="open_source_games",
        adapter_id=GAME_COMPAT_ADAPTER_ID,
        replay_revision=GAME_COMPAT_REPLAY_REVISION,
        answer_program_id=GAME_COMPAT_ANSWER_PROGRAM,
        evaluator=_evaluate,
        counterfactual=counterfactual,
        evidence_ids=evidence_ids,
    )


def audit_game_compatibility_task(task: dict[str, Any]) -> dict[str, bool]:
    return audit_static_task(task, replay_game_compatibility_task)
