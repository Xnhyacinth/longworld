"""Executable as-of reconstruction over real macroeconomic data vintages."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from longworld.core.macrovintageworkflow import (
    BEA_GDP_GDI_VINTAGE_XLSX_URL,
    MACRO_VINTAGE_PARSER_REVISION,
    MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA,
)
from longworld.core.pack import SEP, wrap_prompt
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
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA = (
    "longworld.macro-vintage-pipeline-candidate.v1"
)


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


def _pipeline_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _pipeline_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pipeline_artifact(record_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    identity_field = (
        "observation_id"
        if record_type == "macro_vintage_observation"
        else "relation_id"
    )
    artifact_id = str(payload.get(identity_field) or "")
    if not artifact_id:
        raise ProvenanceError("macro vintage pipeline artifact identity is invalid")
    if record_type == "macro_vintage_observation":
        payload_fields = (
            "observation_id",
            "record_kind",
            "series_id",
            "period",
            "estimate_label",
            "vintage_date",
            "available_at",
            "value",
            "unit",
            "release_date_text",
            "source_sha256",
            "provenance",
        )
    else:
        payload_fields = (
            "relation_id",
            "kind",
            "answer_value_changed",
            "source_release_date_text",
            "series_id",
            "period",
            "source_observation_id",
            "target_observation_id",
            "relation_provenance",
            "evidence",
            "source_vintage_date",
        )
    return {
        "artifact_id": artifact_id,
        "record_type": record_type,
        "source_payload": {
            field: deepcopy(payload[field]) for field in payload_fields if field in payload
        },
    }


def _pipeline_order(record: Mapping[str, Any]) -> tuple[str, int, str]:
    payload = record.get("source_payload")
    if not isinstance(payload, Mapping):
        raise ProvenanceError("macro vintage pipeline artifact payload is invalid")
    if record.get("record_type") == "macro_vintage_observation":
        occurred_at = str(payload.get("vintage_date") or "")
        kind_order = 0
    elif record.get("record_type") == "macro_vintage_relation":
        occurred_at = str(payload.get("source_observation_id") or "")
        occurred_at = str(payload.get("source_vintage_date") or occurred_at)
        kind_order = 1
    else:
        raise ProvenanceError("macro vintage pipeline artifact type is invalid")
    return occurred_at, kind_order, str(record.get("artifact_id") or "")


def _validate_pipeline_manifest(
    manifest: Mapping[str, Any], workflow_manifest_sha256: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if (
        manifest.get("schema_version") != MACRO_VINTAGE_WORKFLOW_MANIFEST_SCHEMA
        or manifest.get("parser") != MACRO_VINTAGE_PARSER_REVISION
        or _SHA256.fullmatch(workflow_manifest_sha256) is None
        or _SHA256.fullmatch(str(manifest.get("raw_source_sha256") or "")) is None
    ):
        raise ProvenanceError("macro vintage workflow manifest binding is invalid")
    fetch_inventory_sha256 = str(manifest.get("fetch_inventory_sha256") or "")
    fetch_receipt = manifest.get("fetch_receipt")
    retrieval = (
        fetch_receipt.get("retrieval")
        if isinstance(fetch_receipt, Mapping)
        else None
    )
    if (
        _SHA256.fullmatch(fetch_inventory_sha256) is None
        or not isinstance(fetch_receipt, Mapping)
        or not str(fetch_receipt.get("started_at") or "")
        or not str(fetch_receipt.get("completed_at") or "")
        or not isinstance(retrieval, Mapping)
        or retrieval.get("status") != 200
        or retrieval.get("sha256") != manifest.get("raw_source_sha256")
        or retrieval.get("requested_url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
        or retrieval.get("final_url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
        or not isinstance(retrieval.get("raw_bytes"), int)
        or isinstance(retrieval.get("raw_bytes"), bool)
        or int(retrieval["raw_bytes"]) < 1
    ):
        raise ProvenanceError("macro vintage source receipt is invalid")
    authorization = manifest.get("authorization")
    if not isinstance(authorization, Mapping) or not str(
        authorization.get("record_id") or ""
    ):
        raise ProvenanceError("macro vintage workflow authorization is invalid")
    observations_value = manifest.get("observations")
    relations_value = manifest.get("relations")
    trajectories_value = manifest.get("trajectories")
    if not all(isinstance(value, list) and value for value in (
        observations_value,
        relations_value,
        trajectories_value,
    )):
        raise ProvenanceError("macro vintage workflow inventory is empty")
    observations: dict[str, dict[str, Any]] = {}
    assert isinstance(observations_value, list)
    for raw in observations_value:
        if not isinstance(raw, dict):
            raise ProvenanceError("macro vintage workflow observation is invalid")
        observation_id = str(raw.get("observation_id") or "")
        provenance = raw.get("provenance")
        if (
            not observation_id
            or observation_id in observations
            or raw.get("record_kind") != "macro_vintage_observation"
            or raw.get("source_sha256") != manifest.get("raw_source_sha256")
            or not isinstance(provenance, Mapping)
            or provenance.get("parser") != MACRO_VINTAGE_PARSER_REVISION
            or not str(provenance.get("value_cell") or "")
            or not str(raw.get("series_id") or "")
            or not str(raw.get("period") or "")
            or not str(raw.get("vintage_date") or "")
            or not str(raw.get("value") or "")
        ):
            raise ProvenanceError("macro vintage workflow observation is invalid")
        _parse_date(str(raw["vintage_date"]), "vintage date")
        _decimal(str(raw["value"]))
        observations[observation_id] = raw
    relations: dict[str, dict[str, Any]] = {}
    assert isinstance(relations_value, list)
    for raw in relations_value:
        if not isinstance(raw, dict):
            raise ProvenanceError("macro vintage workflow relation is invalid")
        relation_id = str(raw.get("relation_id") or "")
        source_id = str(raw.get("source_observation_id") or "")
        target_id = str(raw.get("target_observation_id") or "")
        if (
            not relation_id
            or relation_id in relations
            or source_id not in observations
            or target_id not in observations
            or raw.get("kind")
            not in {
                "revises_observation",
                "supersedes_without_observed_value_change",
            }
            or raw.get("relation_provenance")
            != "verified_derived_temporal_same_series"
        ):
            raise ProvenanceError("macro vintage workflow relation is invalid")
        source = observations[source_id]
        target = observations[target_id]
        changed = source["value"] != target["value"]
        if (
            source["series_id"] != target["series_id"]
            or source["period"] != target["period"]
            or source["vintage_date"] <= target["vintage_date"]
            or raw.get("answer_value_changed") is not changed
            or raw.get("kind")
            != (
                "revises_observation"
                if changed
                else "supersedes_without_observed_value_change"
            )
        ):
            raise ProvenanceError("macro vintage workflow relation semantics are invalid")
        bound = deepcopy(raw)
        bound["source_vintage_date"] = source["vintage_date"]
        relations[relation_id] = bound
    assert isinstance(trajectories_value, list)
    trajectories = [deepcopy(value) for value in trajectories_value if isinstance(value, dict)]
    if len(trajectories) != len(trajectories_value):
        raise ProvenanceError("macro vintage workflow trajectory is invalid")
    return observations, relations, trajectories


def _pipeline_path(
    observations: Mapping[str, dict[str, Any]],
    relations: Mapping[str, dict[str, Any]],
    trajectories: Sequence[dict[str, Any]],
    *,
    series_id: str,
    period: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches = [
        trajectory
        for trajectory in trajectories
        if trajectory.get("series_id") == series_id
        and trajectory.get("period") == period
    ]
    if len(matches) != 1:
        raise ProvenanceError("macro vintage target trajectory is not unique")
    observation_ids = matches[0].get("observation_ids")
    relation_ids = matches[0].get("relation_ids")
    if (
        not isinstance(observation_ids, list)
        or len(observation_ids) < 4
        or len(observation_ids) != len(set(observation_ids))
        or not isinstance(relation_ids, list)
        or len(relation_ids) != len(observation_ids) - 1
    ):
        raise ProvenanceError("macro vintage target requires at least four vintages")
    try:
        selected_observations = [observations[value] for value in observation_ids]
        selected_relations = [relations[value] for value in relation_ids]
    except KeyError as error:
        raise ProvenanceError("macro vintage target trajectory is unbound") from error
    if selected_relations[-1]["kind"] != "revises_observation":
        raise ProvenanceError("macro vintage final transition must change the answer")
    return selected_observations, selected_relations


def _pipeline_answer(
    observations: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> str:
    steps = []
    for prior, current, relation in zip(
        observations[:-1], observations[1:], relations, strict=True
    ):
        prior_value = _decimal(str(prior["value"]))
        current_value = _decimal(str(current["value"]))
        changed = current_value != prior_value
        expected_kind = (
            "revises_observation"
            if changed
            else "supersedes_without_observed_value_change"
        )
        if (
            relation.get("target_observation_id") != prior.get("observation_id")
            or relation.get("source_observation_id") != current.get("observation_id")
            or relation.get("kind") != expected_kind
        ):
            raise ProvenanceError("macro vintage path relation is invalid")
        steps.append(
            {
                "from_vintage": prior["vintage_date"],
                "to_vintage": current["vintage_date"],
                "relation_kind": expected_kind,
                "delta": _decimal_text(current_value - prior_value),
            }
        )
    first = observations[0]
    current = observations[-1]
    return _pipeline_json(
        {
            "series_id": first["series_id"],
            "period": first["period"],
            "decision_date": current["vintage_date"],
            "initial_vintage_record_id": first["observation_id"],
            "initial_value": first["value"],
            "current_vintage_record_id": current["observation_id"],
            "current_value": current["value"],
            "cumulative_revision_delta": _decimal_text(
                _decimal(str(current["value"])) - _decimal(str(first["value"]))
            ),
            "revision_steps": steps,
        }
    )


def _candidate_documents(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    context = candidate.get("document_context")
    classifications = candidate.get("artifact_classification")
    if not isinstance(context, str) or not isinstance(classifications, list):
        raise ProvenanceError("macro vintage pipeline document pool is invalid")
    documents = context.split(SEP)
    if len(documents) != len(classifications):
        raise ProvenanceError("macro vintage pipeline document pool is unbound")
    output: dict[str, dict[str, Any]] = {}
    for text, classification in zip(documents, classifications, strict=True):
        if not isinstance(classification, Mapping):
            raise ProvenanceError("macro vintage pipeline classification is invalid")
        try:
            record = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProvenanceError("macro vintage pipeline document is invalid") from error
        artifact_id = str(classification.get("artifact_id") or "")
        if (
            not isinstance(record, dict)
            or _pipeline_json(record) != text
            or record.get("artifact_id") != artifact_id
            or classification.get("provenance_id")
            != f"sha256:{_pipeline_sha256(text)}"
            or artifact_id in output
        ):
            raise ProvenanceError("macro vintage pipeline document binding is invalid")
        output[artifact_id] = record
    return output


def replay_macro_vintage_pipeline_selection(
    candidate: dict[str, Any],
    artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay one selected set of complete source-bound macro records."""
    unknown = {
        "answer": "unknown",
        "source_record_ids": [],
        "source_relation_ids": [],
        "authentic_source_relation_edges": [],
        "verified_derived_order_relation_edges": [],
        "event_count": 0,
        "strict_support_event_count": 0,
        "proof_depth": 0,
        "hop_count": 0,
    }
    try:
        if (
            candidate.get("schema_version")
            != MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA
            or isinstance(artifact_ids, (str, bytes))
            or len(artifact_ids) != len(set(artifact_ids))
        ):
            return unknown
        documents = _candidate_documents(candidate)
        if any(value not in documents for value in artifact_ids):
            return unknown
        selected_documents = {value: documents[value] for value in artifact_ids}
        task = candidate.get("macro_task")
        source_binding = candidate.get("source_binding")
        if not isinstance(task, Mapping) or not isinstance(source_binding, Mapping):
            return unknown
        raw_source_sha256 = str(source_binding.get("raw_source_sha256") or "")
        parser_revision = str(source_binding.get("parser_revision") or "")
        selected_observations: dict[str, dict[str, Any]] = {}
        selected_relations: list[dict[str, Any]] = []
        for artifact_id, document in selected_documents.items():
            payload = document.get("source_payload")
            if not isinstance(payload, Mapping):
                return unknown
            if document.get("record_type") == "macro_vintage_observation":
                provenance = payload.get("provenance")
                observation_id = str(payload.get("observation_id") or "")
                if (
                    observation_id != artifact_id
                    or payload.get("record_kind") != "macro_vintage_observation"
                    or payload.get("source_sha256") != raw_source_sha256
                    or not isinstance(provenance, Mapping)
                    or provenance.get("parser") != parser_revision
                    or not str(provenance.get("sheet_part") or "")
                    or not str(provenance.get("value_cell") or "")
                    or payload.get("available_at") != payload.get("vintage_date")
                ):
                    return unknown
                _parse_date(str(payload.get("vintage_date") or ""), "vintage date")
                _decimal(str(payload.get("value") or ""))
                selected_observations[observation_id] = deepcopy(dict(payload))
            elif document.get("record_type") == "macro_vintage_relation":
                if (
                    payload.get("relation_id") != artifact_id
                    or payload.get("relation_provenance")
                    != "verified_derived_temporal_same_series"
                ):
                    return unknown
                selected_relations.append(deepcopy(dict(payload)))
            else:
                return unknown
        for relation in selected_relations:
            source_observation = selected_observations.get(
                str(relation.get("source_observation_id") or "")
            )
            target_observation = selected_observations.get(
                str(relation.get("target_observation_id") or "")
            )
            if source_observation is None or target_observation is None:
                return unknown
            changed = source_observation["value"] != target_observation["value"]
            expected_kind = (
                "revises_observation"
                if changed
                else "supersedes_without_observed_value_change"
            )
            evidence = relation.get("evidence")
            evidence_by_observation = (
                {
                    str(value.get("observation_id") or ""): value
                    for value in evidence
                    if isinstance(value, Mapping)
                }
                if isinstance(evidence, list)
                else {}
            )
            if (
                relation.get("kind") != expected_kind
                or relation.get("answer_value_changed") is not changed
                or source_observation.get("series_id")
                != target_observation.get("series_id")
                or source_observation.get("period") != target_observation.get("period")
                or not isinstance(evidence, list)
                or len(evidence_by_observation) != len(evidence)
                or set(evidence_by_observation)
                != {
                    str(source_observation["observation_id"]),
                    str(target_observation["observation_id"]),
                }
                or any(
                    value.get("source_sha256") != raw_source_sha256
                    or value.get("cell_reference")
                    != selected_observations[
                        str(value.get("observation_id") or "")
                    ]["provenance"]["value_cell"]
                    for value in evidence_by_observation.values()
                )
            ):
                return unknown
        series_id = str(task.get("series_id") or "")
        period = str(task.get("period") or "")
        start_vintage_date = str(task.get("start_vintage_date") or "")
        decision_date = str(task.get("decision_date") or "")
        if (
            not series_id
            or not period
            or not start_vintage_date
            or not decision_date
            or start_vintage_date > decision_date
        ):
            return unknown
        observations = sorted(
            (
                deepcopy(value)
                for value in selected_observations.values()
                if value.get("series_id") == series_id
                and value.get("period") == period
                and start_vintage_date
                <= str(value.get("vintage_date") or "")
                <= decision_date
            ),
            key=lambda value: (
                str(value.get("vintage_date") or ""),
                str(value.get("observation_id") or ""),
            ),
        )
        relations = [
            deepcopy(value)
            for value in selected_relations
            if value.get("series_id") == series_id
            and value.get("period") == period
        ]
        if (
            len(observations) < 4
            or observations[0].get("vintage_date") != start_vintage_date
            or observations[-1].get("vintage_date") != decision_date
        ):
            return unknown
        observation_ids = [str(value.get("observation_id") or "") for value in observations]
        expected_pairs = set(pairwise(observation_ids))
        relation_pairs = {
            (
                str(value.get("target_observation_id") or ""),
                str(value.get("source_observation_id") or ""),
            )
            for value in relations
        }
        if (
            len(set(observation_ids)) != len(observation_ids)
            or len(relations) != len(observations) - 1
            or relation_pairs != expected_pairs
        ):
            return unknown
        relation_by_pair = {
            (
                str(value["target_observation_id"]),
                str(value["source_observation_id"]),
            ): value
            for value in relations
        }
        relations = [relation_by_pair[pair] for pair in pairwise(observation_ids)]
        relation_ids = [str(value.get("relation_id") or "") for value in relations]
        if len(set(relation_ids)) != len(relation_ids) or any(not value for value in relation_ids):
            return unknown
        if counterfactual:
            twin = candidate.get("counterfactual_twin")
            if not isinstance(twin, Mapping):
                return unknown
            target = next(
                value
                for value in observations
                if value.get("observation_id") == twin.get("record_id")
            )
            if target.get("value") != twin.get("parent_value"):
                return unknown
            target["value"] = twin.get("value")
        answer = _pipeline_answer(observations, relations)
        source_sha256 = raw_source_sha256
        authentic = [
            {
                "parent_record_id": (
                    f"bea-xlsx:{source_sha256}:"
                    f"{observation['provenance']['sheet_part']}:"
                    f"{observation['provenance']['value_cell']}"
                ),
                "child_record_id": str(observation["observation_id"]),
                "relation_id": f"bea:cell-binding:{observation['observation_id']}",
            }
            for observation in observations
        ]
        derived = [
            {
                "parent_record_id": str(relation["target_observation_id"]),
                "child_record_id": str(relation["source_observation_id"]),
                "relation_id": str(relation["relation_id"]),
            }
            for relation in relations
        ]
        return {
            "answer": answer,
            "source_record_ids": list(observation_ids),
            "source_relation_ids": [
                *(value["relation_id"] for value in authentic),
                *(value["relation_id"] for value in derived),
            ],
            "authentic_source_relation_edges": authentic,
            "verified_derived_order_relation_edges": derived,
            "event_count": len(observations) + len(relations),
            "strict_support_event_count": len(observations),
            "proof_depth": len(observations),
            "hop_count": len(relations),
        }
    except (KeyError, StopIteration, TypeError, ValueError, ProvenanceError):
        return unknown


def replay_macro_vintage_pipeline_raw_slice(
    candidate: dict[str, Any],
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay only complete canonical records visible in one raw text slice."""
    try:
        approved = _candidate_documents(candidate)
        artifact_ids: list[str] = []
        lines = raw_document_context.split("\n")
        for index, line in enumerate(lines):
            if (index == 0 and not left_framed) or (
                index == len(lines) - 1 and not right_framed
            ):
                continue
            if not line or line == SEP.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or _pipeline_json(record) != line:
                raise ProvenanceError("macro vintage raw record is not canonical")
            artifact_id = str(record.get("artifact_id") or "")
            if artifact_id not in approved or approved[artifact_id] != record:
                raise ProvenanceError("macro vintage raw record is not approved")
            artifact_ids.append(artifact_id)
        if not artifact_ids:
            raise ProvenanceError("macro vintage raw slice has no complete records")
        return replay_macro_vintage_pipeline_selection(
            candidate, artifact_ids, counterfactual=counterfactual
        )
    except (TypeError, ValueError, json.JSONDecodeError, ProvenanceError):
        return replay_macro_vintage_pipeline_selection(candidate, [])


def build_macro_vintage_pipeline_candidates(
    manifest: Mapping[str, Any],
    *,
    workflow_manifest_sha256: str,
    world_id: str,
    target_series_id: str,
    target_period: str,
    bands: Sequence[tuple[str, int, int]],
    token_counter: Callable[[str], int],
    tokenizer_model_id: str,
    tokenizer_revision: str,
    tokenizer_asset_manifest_sha256: str,
    task_replay_sidecar_binding: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build nested exact-band macro tasks from distinct chronological records."""
    observations, relations, trajectories = _validate_pipeline_manifest(
        manifest, workflow_manifest_sha256
    )
    if (
        not world_id
        or not target_series_id
        or not target_period
        or not callable(token_counter)
        or not tokenizer_model_id
        or _COMMIT_SHA.fullmatch(tokenizer_revision) is None
        or _SHA256.fullmatch(tokenizer_asset_manifest_sha256) is None
        or not bands
    ):
        raise ProvenanceError("macro vintage pipeline identity is invalid")
    if task_replay_sidecar_binding is not None and (
        set(task_replay_sidecar_binding)
        != {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        }
        or task_replay_sidecar_binding.get("adapter_id")
        != MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0]
        or task_replay_sidecar_binding.get("adapter_revision")
        != MACRO_VINTAGE_TASK_REPLAY_ADAPTER[1]
        or task_replay_sidecar_binding.get("sidecar_schema_version")
        != MACRO_VINTAGE_TASK_REPLAY_ADAPTER[2]
        or _SHA256.fullmatch(
            str(task_replay_sidecar_binding.get("sha256") or "")
        )
        is None
    ):
        raise ProvenanceError("macro vintage task replay sidecar binding is invalid")
    path_observations, path_relations = _pipeline_path(
        observations,
        relations,
        trajectories,
        series_id=target_series_id,
        period=target_period,
    )
    observation_artifacts = [
        _pipeline_artifact("macro_vintage_observation", value)
        for value in observations.values()
    ]
    relation_artifacts = [
        _pipeline_artifact("macro_vintage_relation", value)
        for value in relations.values()
    ]
    all_artifacts = sorted(
        [*observation_artifacts, *relation_artifacts], key=_pipeline_order
    )
    all_by_id = {str(value["artifact_id"]): value for value in all_artifacts}
    if len(all_by_id) != len(all_artifacts):
        raise ProvenanceError("macro vintage pipeline artifacts are duplicated")
    target_trajectories = [
        value
        for value in trajectories
        if value.get("series_id") == target_series_id
        and value.get("period") == target_period
    ]
    if len(target_trajectories) != 1:
        raise ProvenanceError("macro vintage target trajectory is not unique")
    target_trajectory_id = str(target_trajectories[0].get("trajectory_id") or "")
    trajectory_prefix_ids: dict[str, list[tuple[str, ...]]] = {}
    claimed_artifact_ids: set[str] = set()
    for trajectory in sorted(
        trajectories, key=lambda value: str(value.get("trajectory_id") or "")
    ):
        trajectory_id = str(trajectory.get("trajectory_id") or "")
        observation_ids = trajectory.get("observation_ids")
        relation_ids = trajectory.get("relation_ids")
        if (
            not trajectory_id
            or trajectory_id in trajectory_prefix_ids
            or not isinstance(observation_ids, list)
            or len(observation_ids) < 2
            or not isinstance(relation_ids, list)
            or len(relation_ids) != len(observation_ids) - 1
            or any(value not in all_by_id for value in (*observation_ids, *relation_ids))
        ):
            raise ProvenanceError("macro vintage packing trajectory is invalid")
        complete_ids = {str(value) for value in (*observation_ids, *relation_ids)}
        if claimed_artifact_ids.intersection(complete_ids):
            raise ProvenanceError("macro vintage packing trajectories overlap")
        claimed_artifact_ids.update(complete_ids)
        prefixes: list[tuple[str, ...]] = [()]
        for length in range(1, len(observation_ids) + 1):
            prefixes.append(
                (
                    *(str(value) for value in observation_ids[:length]),
                    *(str(value) for value in relation_ids[: max(0, length - 1)]),
                )
            )
        trajectory_prefix_ids[trajectory_id] = prefixes
    essential_ids = [
        *(str(value["observation_id"]) for value in path_observations),
        *(str(value["relation_id"]) for value in path_relations),
    ]
    essential_set = set(essential_ids)
    current = path_observations[-1]
    prior = path_observations[-2]
    replacement = _decimal(str(current["value"])) + Decimal(1)
    if replacement == _decimal(str(prior["value"])):
        replacement += Decimal(1)
    replacement_value = _decimal_text(replacement)
    source_binding = {
        "workflow_manifest_sha256": workflow_manifest_sha256,
        "raw_source_sha256": str(manifest["raw_source_sha256"]),
        "fetch_inventory_sha256": str(manifest["fetch_inventory_sha256"]),
        "fetch_receipt_sha256": _pipeline_sha256(
            _pipeline_json(manifest["fetch_receipt"])
        ),
        "source_families": ["bea_gdp_gdi_vintage_workbook"],
        "authorization_record_id": str(manifest["authorization"]["record_id"]),
        "parser_revision": MACRO_VINTAGE_PARSER_REVISION,
    }
    question = (
        f"For BEA series {target_series_id}, period {target_period}, reconstruct "
        f"the complete revision path from {path_observations[0]['vintage_date']} "
        f"through the decision date {current['vintage_date']}. Report every "
        "changed or unchanged transition, the initial and current values, and "
        "the cumulative revision delta."
    )
    task_metadata = {
        "series_id": target_series_id,
        "period": target_period,
        "start_vintage_date": str(path_observations[0]["vintage_date"]),
        "decision_date": str(current["vintage_date"]),
    }

    document_by_id = {
        str(value["artifact_id"]): _pipeline_json(value) for value in all_artifacts
    }
    document_token_cost = {
        artifact_id: token_counter(document)
        for artifact_id, document in document_by_id.items()
    }
    separator_token_cost = token_counter(SEP)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (*document_token_cost.values(), separator_token_cost)
    ):
        raise ProvenanceError("macro vintage packing token costs are invalid")
    artifact_indexes = {
        str(value["artifact_id"]): index for index, value in enumerate(all_artifacts)
    }
    essential_indexes = sorted(artifact_indexes[value] for value in essential_ids)
    span_artifact_ids = {
        str(value["artifact_id"])
        for value in all_artifacts[
            essential_indexes[0] : essential_indexes[-1] + 1
        ]
    }

    def assemble(
        selected: Sequence[dict[str, Any]],
        documents: Sequence[str],
        document_context: str,
        context: str,
        document_context_tokens: int,
        context_tokens: int,
        essential_span_tokens: int,
        minimum_essential_span_tokens: int,
        band_name: str,
        prefix_lengths: Mapping[str, int],
    ) -> dict[str, Any]:
        classifications = [
            {
                "artifact_id": str(value["artifact_id"]),
                "workflow_id": world_id,
                "workflow_kind": "real_source_derived",
                "evidence_role": (
                    "causal_gold"
                    if value["artifact_id"] in essential_set
                    else "natural_background"
                ),
                "source_origin": "real_derived",
                "provenance_id": f"sha256:{_pipeline_sha256(document)}",
            }
            for value, document in zip(selected, documents, strict=True)
        ]
        candidate: dict[str, Any] = {
            "schema_version": MACRO_VINTAGE_PIPELINE_CANDIDATE_SCHEMA,
            "data_product": "worldlong_bea_macro_vintage_candidate_v1",
            "data_stage": "candidate",
            "training_objective": "sft",
            "train_ready": False,
            "production_eligible": False,
            "promotion_eligible": False,
            "complete_world": False,
            "promoted": False,
            "generation_integration": "macro_pipeline_candidate",
            "world_id": world_id,
            "domain": "macro_economics",
            "query_id": f"{world_id}:macro-revision-path:{band_name}",
            "query_type": "macro_vintage_revision_path",
            "query_timing": "first",
            "view": "ordered_release_timeline",
            "composition_method": "as_of_revision_workflow",
            "motif": "revision_path+unchanged_supersession+as_of_reconstruction",
            "length_bucket": band_name,
            "length_fill_method": "distinct_chronological_source_records",
            "trajectory_prefix_lengths": dict(sorted(prefix_lengths.items())),
            "trajectory_prefix_artifact_ids": {
                trajectory_id: list(trajectory_prefix_ids[trajectory_id][length])
                for trajectory_id, length in sorted(prefix_lengths.items())
            },
            "target_trajectory_ids": [target_trajectory_id],
            "background_trajectory_ids": sorted(
                trajectory_id
                for trajectory_id in prefix_lengths
                if trajectory_id != target_trajectory_id
            ),
            "question": question,
            "answer": "",
            "cf_answer": "",
            "context": context,
            "document_context": document_context,
            "artifact_classification": classifications,
            "essential_artifact_ids": list(essential_ids),
            "counterfactual_twin": {
                "record_id": str(current["observation_id"]),
                "fact_id": f"{current['observation_id']}:value",
                "parent_value": str(current["value"]),
                "value": replacement_value,
                "source_origin": "synthetic_counterfactual",
                "provenance_operation": "replace_exact_fact",
            },
            "macro_task": deepcopy(task_metadata),
            "answer_program_id": "macro.as_of_revision_path.v2",
            "strict_replay_revision": MACRO_VINTAGE_REPLAY_REVISION,
            "workflow_ids": [world_id],
            "source_family_ids": ["bea_gdp_gdi_vintage_workbook"],
            "source_urls": [BEA_GDP_GDI_VINTAGE_XLSX_URL],
            "source_binding": deepcopy(source_binding),
            "tokenizer_model_id": tokenizer_model_id,
            "tokenizer_revision": tokenizer_revision,
            "tokenizer_asset_manifest_sha256": tokenizer_asset_manifest_sha256,
            "tokenizer_document_context_tokens": document_context_tokens,
            "tokenizer_essential_span_tokens": essential_span_tokens,
            "minimum_essential_span_tokens": minimum_essential_span_tokens,
            "tokenizer_context_tokens": context_tokens,
            "actual_context_tokens": context_tokens,
            "real_source_verified": False,
            "source_verified_at_materialization": False,
            "source_attestation_verified": False,
            "real_source_token_ratio": 1.0,
            "pipeline_capabilities": {
                "dense_ranking": True,
                "macro_strict_replay": True,
                "generic_strict_replay": task_replay_sidecar_binding is not None,
                "generic_promotion": task_replay_sidecar_binding is not None,
            },
            "promotion_blocker_code": (
                "missing_source_sidecar"
                if task_replay_sidecar_binding is None
                else "missing_candidate_attestation_and_dense_ranking"
            ),
        }
        if task_replay_sidecar_binding is not None:
            candidate["task_replay_sidecar"] = deepcopy(task_replay_sidecar_binding)
        replay = replay_macro_vintage_pipeline_selection(candidate, essential_ids)
        candidate.update(
            {
                "answer": replay["answer"],
                "source_record_ids": replay["source_record_ids"],
                "source_relation_ids": replay["source_relation_ids"],
                "authentic_source_relation_edges": replay[
                    "authentic_source_relation_edges"
                ],
                "verified_derived_order_relation_edges": replay[
                    "verified_derived_order_relation_edges"
                ],
                "event_count": replay["event_count"],
                "strict_support_event_count": replay["strict_support_event_count"],
                "graph": {
                    "proof_depth": replay["proof_depth"],
                    "hop_count": replay["hop_count"],
                },
            }
        )
        candidate["cf_answer"] = replay_macro_vintage_pipeline_selection(
            candidate, essential_ids, counterfactual=True
        )["answer"]
        return candidate

    def selected_ids_for_prefixes(prefix_lengths: Mapping[str, int]) -> set[str]:
        selected_ids: set[str] = set()
        for trajectory_id, length in prefix_lengths.items():
            prefixes = trajectory_prefix_ids.get(trajectory_id)
            if prefixes is None or not 0 <= length < len(prefixes):
                raise ProvenanceError("macro vintage packing prefix is invalid")
            selected_ids.update(prefixes[length])
        return selected_ids

    def estimated_tokens(selected_ids: set[str]) -> int:
        if not selected_ids:
            return 0
        return sum(document_token_cost[value] for value in selected_ids) + (
            len(selected_ids) - 1
        ) * separator_token_cost

    def estimated_essential_span_tokens(selected_ids: set[str]) -> int:
        inside = selected_ids & span_artifact_ids
        if not inside:
            return 0
        return sum(document_token_cost[value] for value in inside) + (
            len(inside) - 1
        ) * separator_token_cost

    def packing_states(
        retained_prefixes: Mapping[str, int], lower: int, upper: int
    ) -> list[tuple[int, int, tuple[tuple[str, int], ...]]]:
        """Bounded deterministic DP over complete trajectory prefixes."""
        background_ids = sorted(
            value for value in trajectory_prefix_ids if value != target_trajectory_id
        )
        base_ids = selected_ids_for_prefixes(retained_prefixes)
        states: dict[
            tuple[int, int, int], tuple[tuple[str, int], ...]
        ] = {
            (
                estimated_tokens(base_ids),
                min(2, len(retained_prefixes) - 1),
                estimated_essential_span_tokens(base_ids),
            ): tuple(sorted(retained_prefixes.items()))
        }
        max_estimate = upper + 1_024
        max_states = 8_192
        minimum_span = max(
            8_193,
            16_385 if lower > 16_384 else 0,
            lower // 2 + 1,
        )

        def pruning_rank(
            item: tuple[
                tuple[int, int, int], tuple[tuple[str, int], ...]
            ],
        ) -> tuple[Any, ...]:
            return (
                0 if item[0][0] <= upper else 1,
                max(0, minimum_span - item[0][2]),
                max(0, lower - item[0][0]),
                abs(lower - item[0][0]),
                -item[0][1],
                item[1],
            )

        def prune(
            values: dict[
                tuple[int, int, int], tuple[tuple[str, int], ...]
            ],
        ) -> dict[tuple[int, int, int], tuple[tuple[str, int], ...]]:
            return dict(sorted(values.items(), key=pruning_rank)[:max_states])

        for trajectory_id in background_ids:
            base_length = int(retained_prefixes.get(trajectory_id, 0))
            maximum = len(trajectory_prefix_ids[trajectory_id]) - 1
            first_extension = max(1, base_length + 1)
            base_prefix_ids = set(
                trajectory_prefix_ids[trajectory_id][base_length]
            )
            extensions: list[tuple[int, int, int]] = []
            for length in range(first_extension, maximum + 1):
                added_ids = (
                    set(trajectory_prefix_ids[trajectory_id][length])
                    - base_prefix_ids
                )
                added_count = len(added_ids)
                added_cost = sum(
                    document_token_cost[value] for value in added_ids
                ) + added_count * separator_token_cost
                inside_ids = added_ids & span_artifact_ids
                added_span = sum(
                    document_token_cost[value] for value in inside_ids
                ) + len(inside_ids) * separator_token_cost
                extensions.append((length, added_span, added_cost))
            extension_costs = [value[2] for value in extensions]
            expanded: dict[
                tuple[int, int, int], tuple[tuple[str, int], ...]
            ] = dict(states)
            for (cost, background_count, span_tokens), selection in states.items():
                fitting = bisect_right(extension_costs, max_estimate - cost)
                if not fitting:
                    continue
                position = bisect_left(selection, (trajectory_id, -1))
                if base_length:
                    if (
                        position >= len(selection)
                        or selection[position][0] != trajectory_id
                    ):
                        raise ProvenanceError(
                            "macro vintage retained packing prefix is invalid"
                        )
                    before = selection[:position]
                    after = selection[position + 1 :]
                else:
                    if (
                        position < len(selection)
                        and selection[position][0] == trajectory_id
                    ):
                        raise ProvenanceError(
                            "macro vintage new packing prefix is invalid"
                        )
                    before = selection[:position]
                    after = selection[position:]
                for extension_index in range(fitting):
                    length, added_span, added_cost = extensions[extension_index]
                    next_cost = cost + added_cost
                    normalized = before + ((trajectory_id, length),) + after
                    if base_length:
                        next_background_count = background_count
                    else:
                        next_background_count = min(2, background_count + 1)
                    key = (
                        next_cost,
                        next_background_count,
                        span_tokens + added_span,
                    )
                    previous = expanded.get(key)
                    if previous is None or normalized < previous:
                        expanded[key] = normalized
            if len(expanded) > max_states:
                states = prune(expanded)
            else:
                states = expanded
            if not states:
                break
        finalists = [
            (cost, span_tokens, selection)
            for (cost, background_count, span_tokens), selection in states.items()
            if background_count >= 2
        ]
        return sorted(
            finalists,
            key=lambda item: (
                0 if lower <= item[0] <= upper else 1,
                max(0, minimum_span - item[1]),
                abs(lower - item[0]),
                item[2],
            ),
        )[:512]

    output: list[dict[str, Any]] = []
    retained_prefixes = {
        target_trajectory_id: len(trajectory_prefix_ids[target_trajectory_id]) - 1
    }
    used_band_names: set[str] = set()
    for band_name, lower, upper in bands:
        if (
            not band_name
            or band_name in used_band_names
            or lower < 1
            or upper < lower
        ):
            raise ProvenanceError("macro vintage pipeline band is invalid")
        used_band_names.add(band_name)
        candidate: dict[str, Any] | None = None
        selected_prefixes: dict[str, int] | None = None
        minimum_essential_span_tokens = max(
            8_193,
            16_385 if lower > 16_384 else 0,
            lower // 2 + 1,
        )
        for _estimated_cost, _estimated_span, selection in packing_states(
            retained_prefixes, lower, upper
        ):
            selected_prefix_map = dict(selection)
            selected_ids = selected_ids_for_prefixes(selected_prefix_map)
            selected = [
                value
                for value in all_artifacts
                if value["artifact_id"] in selected_ids
            ]
            documents = [
                document_by_id[str(value["artifact_id"])] for value in selected
            ]
            document_context = SEP.join(documents)
            document_context_tokens = token_counter(document_context)
            if document_context_tokens < lower:
                continue
            essential_positions = [
                index
                for index, value in enumerate(selected)
                if value["artifact_id"] in essential_set
            ]
            essential_span_tokens = token_counter(
                SEP.join(
                    documents[
                        essential_positions[0] : essential_positions[-1] + 1
                    ]
                )
            )
            if essential_span_tokens < minimum_essential_span_tokens:
                continue
            context = wrap_prompt(question, document_context, "first")
            context_tokens = token_counter(context)
            if context_tokens > upper:
                continue
            candidate = assemble(
                selected,
                documents,
                document_context,
                context,
                document_context_tokens,
                context_tokens,
                essential_span_tokens,
                minimum_essential_span_tokens,
                band_name,
                selected_prefix_map,
            )
            selected_prefixes = selected_prefix_map
            break
        if candidate is None or selected_prefixes is None:
            base_ids = selected_ids_for_prefixes(retained_prefixes)
            retained_document_context = SEP.join(
                document_by_id[str(value["artifact_id"])]
                for value in all_artifacts
                if value["artifact_id"] in base_ids
            )
            retained_document_tokens = token_counter(retained_document_context)
            raise ProvenanceError(
                f"cannot fill exact {band_name} from distinct macro records: "
                f"retained document_context tokens {retained_document_tokens}"
            )
        retained_prefixes = selected_prefixes
        candidate["band_lower_tokens"] = lower
        candidate["band_upper_tokens"] = upper
        audit = audit_macro_vintage_pipeline_candidate(candidate)
        if not audit or not all(audit.values()):
            failed = sorted(key for key, value in audit.items() if not value)
            raise ProvenanceError(
                "macro vintage pipeline candidate audit failed: " + ",".join(failed)
            )
        output.append(candidate)
    return output


def audit_macro_vintage_pipeline_candidate(candidate: dict[str, Any]) -> dict[str, bool]:
    """Audit source binding, strict selection, CF, remove-one and chronology."""
    try:
        documents = _candidate_documents(candidate)
        artifact_ids = list(documents)
        essentials = candidate.get("essential_artifact_ids")
        essentials = essentials if isinstance(essentials, list) else []
        full = replay_macro_vintage_pipeline_selection(candidate, artifact_ids)
        minimal = replay_macro_vintage_pipeline_selection(candidate, essentials)
        cf = replay_macro_vintage_pipeline_selection(
            candidate, artifact_ids, counterfactual=True
        )
        removals = [
            replay_macro_vintage_pipeline_selection(
                candidate, [value for value in essentials if value != removed]
            )["answer"]
            for removed in essentials
        ]
        ordered = [
            _pipeline_order(value) for value in documents.values()
        ]
        prefix_lengths = candidate.get("trajectory_prefix_lengths")
        prefix_artifacts = candidate.get("trajectory_prefix_artifact_ids")
        target_trajectory_ids = candidate.get("target_trajectory_ids")
        background_trajectory_ids = candidate.get("background_trajectory_ids")
        essential_span_tokens = candidate.get("tokenizer_essential_span_tokens")
        minimum_essential_span_tokens = candidate.get(
            "minimum_essential_span_tokens"
        )
        prefix_valid = bool(
            isinstance(prefix_lengths, Mapping)
            and isinstance(prefix_artifacts, Mapping)
            and isinstance(target_trajectory_ids, list)
            and len(target_trajectory_ids) == 1
            and isinstance(background_trajectory_ids, list)
            and len(background_trajectory_ids) >= 2
            and set(prefix_lengths) == set(prefix_artifacts)
            and set(background_trajectory_ids)
            == set(prefix_lengths) - set(target_trajectory_ids)
        )
        prefix_union: set[str] = set()
        if prefix_valid:
            assert isinstance(prefix_lengths, Mapping)
            assert isinstance(prefix_artifacts, Mapping)
            for trajectory_id, selected_value in prefix_artifacts.items():
                length = prefix_lengths.get(trajectory_id)
                if (
                    not isinstance(length, int)
                    or isinstance(length, bool)
                    or length < 1
                    or not isinstance(selected_value, list)
                    or len(selected_value) != len(set(selected_value))
                    or any(value not in documents for value in selected_value)
                    or prefix_union.intersection(selected_value)
                ):
                    prefix_valid = False
                    break
                selected_records = [documents[value] for value in selected_value]
                selected_observations = sorted(
                    (
                        value["source_payload"]
                        for value in selected_records
                        if value["record_type"] == "macro_vintage_observation"
                    ),
                    key=lambda value: (
                        str(value["vintage_date"]),
                        str(value["observation_id"]),
                    ),
                )
                selected_relations = {
                    str(value["source_payload"]["relation_id"]): value[
                        "source_payload"
                    ]
                    for value in selected_records
                    if value["record_type"] == "macro_vintage_relation"
                }
                expected_pairs = [
                    (
                        str(prior["observation_id"]),
                        str(current["observation_id"]),
                    )
                    for prior, current in pairwise(selected_observations)
                ]
                relation_pairs = {
                    (
                        str(value["target_observation_id"]),
                        str(value["source_observation_id"]),
                    )
                    for value in selected_relations.values()
                }
                if (
                    len(selected_observations) != length
                    or len(selected_relations) != length - 1
                    or relation_pairs != set(expected_pairs)
                    or len(
                        {
                            (
                                str(value["series_id"]),
                                str(value["period"]),
                            )
                            for value in selected_observations
                        }
                    )
                    != 1
                ):
                    prefix_valid = False
                    break
                prefix_union.update(selected_value)
            prefix_valid = prefix_valid and prefix_union == set(artifact_ids)
        return {
            "strict_replay_sufficient": full["answer"] == candidate.get("answer"),
            "minimal_replay_sufficient": minimal["answer"]
            == candidate.get("answer"),
            "counterfactual_replay_sufficient": cf["answer"]
            == candidate.get("cf_answer"),
            "counterfactual_changes_answer": cf["answer"] != full["answer"],
            "remove_one_fails": bool(removals)
            and all(value != candidate.get("answer") for value in removals),
            "four_vintage_path": sum(
                value["record_type"] == "macro_vintage_observation"
                and value["artifact_id"] in set(essentials)
                for value in documents.values()
            )
            >= 4,
            "changed_and_unchanged_edges": {
                value["source_payload"]["kind"]
                for value in documents.values()
                if value["record_type"] == "macro_vintage_relation"
                and value["artifact_id"] in set(essentials)
            }
            == {
                "revises_observation",
                "supersedes_without_observed_value_change",
            },
            "chronological_unique_documents": ordered == sorted(ordered)
            and len(artifact_ids) == len(set(artifact_ids)),
            "trajectory_prefix_packing_valid": prefix_valid,
            "semantic_evidence_spread_valid": bool(
                isinstance(essential_span_tokens, int)
                and not isinstance(essential_span_tokens, bool)
                and isinstance(minimum_essential_span_tokens, int)
                and not isinstance(minimum_essential_span_tokens, bool)
                and minimum_essential_span_tokens >= 8_193
                and essential_span_tokens >= minimum_essential_span_tokens
            ),
            "candidate_only_boundary": all(
                candidate.get(field) is False
                for field in (
                    "train_ready",
                    "production_eligible",
                    "promotion_eligible",
                    "complete_world",
                    "promoted",
                )
            ),
        }
    except (KeyError, TypeError, ValueError, ProvenanceError):
        return {"adapter_valid": False}
