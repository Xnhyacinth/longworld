"""Deterministic GovInfo bill-disposition replay over source-bound artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from longworld.core.pack import SEP, wrap_prompt

GOVINFO_DISPOSITION_TASK_SCHEMA = "longworld.govinfo-bill-disposition-task.v1"
GOVINFO_DISPOSITION_ANSWER_PROGRAM = "govinfo.bill_disposition.cross_schema.v1"
GOVINFO_SOURCE_RECEIPT_SCHEMA = "longworld.p52-govinfo-source-receipt.v1"

RETAINED = "R"
MODIFIED = "M"
UNKNOWN = "U"
_SHA256_LENGTH = 64
_TASK_FIELDS = {
    "schema_version",
    "answer_program_id",
    "bill_id",
    "from_stage",
    "to_stage",
    "oracle_revision",
    "oracle_shingle_size",
    "oracle_threshold",
    "counterfactual_code",
    "requested_dispositions",
    "source_receipt_sha256",
}
_REQUEST_FIELDS = {"code", "bill_id", "base_key", "from_stage", "to_stage"}


class GovInfoDispositionError(ValueError):
    """A GovInfo source, oracle, or replay contract is not executable."""


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_words(text: str) -> tuple[str, ...]:
    return tuple(" ".join(text.casefold().split()).split())


def _shingles(text: str, size: int) -> frozenset[tuple[str, ...]]:
    words = _canonical_words(text)
    if len(words) < size:
        return frozenset({words}) if words else frozenset()
    return frozenset(
        tuple(words[index : index + size]) for index in range(len(words) - size + 1)
    )


def _jaccard(left: frozenset[object], right: frozenset[object]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _validate_requests(task: Mapping[str, Any]) -> list[dict[str, str]]:
    requests = task.get("requested_dispositions")
    if not isinstance(requests, list) or not requests:
        raise GovInfoDispositionError("GovInfo task requests are missing")
    normalized: list[dict[str, str]] = []
    for request in requests:
        if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
            raise GovInfoDispositionError("GovInfo task request is malformed")
        item = {field: str(request.get(field) or "") for field in _REQUEST_FIELDS}
        if (
            any(not value for value in item.values())
            or item["bill_id"] != task.get("bill_id")
            or item["from_stage"] != task.get("from_stage")
            or item["to_stage"] != task.get("to_stage")
        ):
            raise GovInfoDispositionError("GovInfo task request identity is invalid")
        normalized.append(item)
    codes = [item["code"] for item in normalized]
    keys = [item["base_key"] for item in normalized]
    if len(set(codes)) != len(codes) or len(set(keys)) != len(keys):
        raise GovInfoDispositionError("GovInfo task requests are duplicated")
    if task.get("counterfactual_code") not in codes:
        raise GovInfoDispositionError("GovInfo counterfactual request is missing")
    return normalized


def validate_govinfo_task(task: object) -> dict[str, Any]:
    """Validate the closed, deterministic disposition answer program."""
    if not isinstance(task, dict) or set(task) != _TASK_FIELDS:
        raise GovInfoDispositionError("GovInfo task fields are invalid")
    if (
        task.get("schema_version") != GOVINFO_DISPOSITION_TASK_SCHEMA
        or task.get("answer_program_id") != GOVINFO_DISPOSITION_ANSWER_PROGRAM
        or not str(task.get("bill_id") or "")
        or not str(task.get("from_stage") or "")
        or not str(task.get("to_stage") or "")
        or task.get("from_stage") == task.get("to_stage")
        or not str(task.get("oracle_revision") or "")
        or isinstance(task.get("oracle_shingle_size"), bool)
        or not isinstance(task.get("oracle_shingle_size"), int)
        or int(task["oracle_shingle_size"]) < 1
        or isinstance(task.get("oracle_threshold"), bool)
        or not isinstance(task.get("oracle_threshold"), (int, float))
        or not 0.0 < float(task["oracle_threshold"]) <= 1.0
        or not _is_sha256(task.get("source_receipt_sha256"))
    ):
        raise GovInfoDispositionError("GovInfo task identity is invalid")
    _validate_requests(task)
    return task


def verify_govinfo_replay_payload(payload: Mapping[str, Any]) -> None:
    """Verify exact frozen-source receipt and task bindings in a sidecar payload."""
    task = validate_govinfo_task(payload.get("govinfo_disposition_task"))
    if payload.get("task_sha256") != _canonical_sha256(task):
        raise GovInfoDispositionError("GovInfo task digest is invalid")
    raw = payload.get("source_receipt_raw_utf8")
    if not isinstance(raw, str) or not raw.endswith("\n"):
        raise GovInfoDispositionError("GovInfo source receipt bytes are invalid")
    if payload.get("source_receipt_sha256") != hashlib.sha256(raw.encode()).hexdigest():
        raise GovInfoDispositionError("GovInfo source receipt digest is invalid")
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GovInfoDispositionError("GovInfo source receipt is not JSON") from error
    if not isinstance(receipt, dict) or raw != _canonical_json(receipt) + "\n":
        raise GovInfoDispositionError("GovInfo source receipt is not canonical")
    authorization = receipt.get("authorization")
    sources = receipt.get("sources")
    if (
        receipt.get("schema_version") != GOVINFO_SOURCE_RECEIPT_SCHEMA
        or receipt.get("data_stage") != "source_inventory"
        or receipt.get("raw_xml_persisted") is not False
        or receipt.get("train_ready") is not False
        or receipt.get("production_eligible") is not False
        or not isinstance(authorization, dict)
        or payload.get("authorization_record_id") != authorization.get("record_id")
        or payload.get("preflight_config_sha256")
        != receipt.get("preflight_config_sha256")
        or payload.get("source_bundle_sha256") != receipt.get("source_bundle_sha256")
        or task.get("source_receipt_sha256") != payload.get("source_receipt_sha256")
        or not isinstance(sources, list)
        or not sources
        or receipt.get("source_count") != len(sources)
    ):
        raise GovInfoDispositionError("GovInfo source receipt binding is invalid")
    bundle_lines: list[str] = []
    seen_urls: set[str] = set()
    for source in sources:
        if (
            not isinstance(source, dict)
            or set(source) != {"bytes", "raw_xml_persisted", "sha256", "url"}
            or not str(source.get("url") or "").startswith("https://www.govinfo.gov/")
            or source.get("url") in seen_urls
            or not _is_sha256(source.get("sha256"))
            or isinstance(source.get("bytes"), bool)
            or not isinstance(source.get("bytes"), int)
            or int(source["bytes"]) < 1
            or source.get("raw_xml_persisted") is not False
        ):
            raise GovInfoDispositionError("GovInfo source inventory is invalid")
        seen_urls.add(str(source["url"]))
        bundle_lines.append(f"{source['url']}:{source['sha256']}")
    observed_bundle = hashlib.sha256(
        ("\n".join(bundle_lines) + "\n").encode()
    ).hexdigest()
    if observed_bundle != payload.get("source_bundle_sha256"):
        raise GovInfoDispositionError("GovInfo source bundle digest is invalid")


def _artifact_documents(
    candidate: Mapping[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, Mapping[str, Any]]]:
    classifications = candidate.get("artifact_classification")
    document_context = candidate.get("document_context")
    if not isinstance(classifications, list) or not isinstance(document_context, str):
        raise GovInfoDispositionError("GovInfo artifact serialization is missing")
    documents = document_context.split(SEP)
    if len(documents) != len(classifications) or not documents:
        raise GovInfoDispositionError("GovInfo artifact serialization is unbound")
    parsed: dict[str, dict[str, Any]] = {}
    classes: dict[str, Mapping[str, Any]] = {}
    ordered_ids: list[str] = []
    for classification, document in zip(classifications, documents, strict=True):
        if not isinstance(classification, Mapping):
            raise GovInfoDispositionError("GovInfo classification is malformed")
        artifact_id = str(classification.get("artifact_id") or "")
        try:
            value = json.loads(document)
        except json.JSONDecodeError as error:
            raise GovInfoDispositionError("GovInfo artifact is not JSON") from error
        if (
            not artifact_id
            or artifact_id in parsed
            or not isinstance(value, dict)
            or document != _canonical_json(value)
        ):
            raise GovInfoDispositionError("GovInfo artifact identity is invalid")
        ordered_ids.append(artifact_id)
        parsed[artifact_id] = value
        classes[artifact_id] = classification
    return ordered_ids, parsed, classes


def _candidate_task(candidate: Mapping[str, Any]) -> dict[str, Any]:
    task = validate_govinfo_task(candidate.get("govinfo_disposition_task"))
    requests = candidate.get("requested_dispositions")
    task_requests = task["requested_dispositions"]
    if (
        candidate.get("answer_program_id") != task.get("answer_program_id")
        or candidate.get("oracle_revision") != task.get("oracle_revision")
        or candidate.get("oracle_shingle_size") != task.get("oracle_shingle_size")
        or candidate.get("oracle_threshold") != task.get("oracle_threshold")
        or not isinstance(requests, list)
        or not requests
        or requests != task_requests[: len(requests)]
    ):
        raise GovInfoDispositionError("GovInfo candidate task binding is invalid")
    return task


def _counterfactual_documents(
    candidate: Mapping[str, Any], parsed: dict[str, dict[str, Any]], selected: set[str]
) -> dict[str, dict[str, Any]]:
    twin = candidate.get("counterfactual_twin")
    if not isinstance(twin, Mapping):
        raise GovInfoDispositionError("GovInfo counterfactual twin is missing")
    source_id = str(twin.get("source_artifact_id") or "")
    target_id = str(twin.get("target_artifact_id") or "")
    parent_value = twin.get("parent_value")
    value = twin.get("value")
    if (
        twin.get("provenance_operation")
        != "replace_target_with_authenticated_source_body"
        or not source_id
        or not target_id
        or source_id == target_id
        or not isinstance(parent_value, Mapping)
        or set(parent_value) != {"source_text", "oracle_text"}
        or not isinstance(value, Mapping)
        or set(value) != {"source_text", "oracle_text"}
    ):
        raise GovInfoDispositionError("GovInfo counterfactual twin is invalid")
    source = parsed.get(source_id)
    target = parsed.get(target_id)
    classifications = candidate.get("artifact_classification")
    target_classification = next(
        (
            item
            for item in classifications or []
            if isinstance(item, Mapping) and item.get("artifact_id") == target_id
        ),
        None,
    )
    materialized = bool(
        isinstance(target_classification, Mapping)
        and target_classification.get("source_origin") == "synthetic_counterfactual"
    )
    expected_source_value = parent_value if materialized else value
    if (
        source is None
        or target is None
        or source.get("artifact_type") != "govinfo_section"
        or target.get("artifact_type") != "govinfo_section"
        or source.get("bill_id") != target.get("bill_id")
        or source.get("base_key") != target.get("base_key")
        or {
            "source_text": source.get("source_text"),
            "oracle_text": source.get("oracle_text"),
        }
        != dict(expected_source_value)
        or {
            "source_text": target.get("source_text"),
            "oracle_text": target.get("oracle_text"),
        }
        != dict(parent_value)
    ):
        raise GovInfoDispositionError(
            "GovInfo counterfactual source binding is invalid"
        )
    output = deepcopy(parsed)
    if source_id in selected and target_id in selected:
        output[target_id]["source_text"] = value["source_text"]
        output[target_id]["oracle_text"] = value["oracle_text"]
        output[target_id]["counterfactual"] = {
            "operation": twin["provenance_operation"],
            "parent_value_sha256": _canonical_sha256(parent_value),
            "replacement_value_sha256": _canonical_sha256(value),
            "source_artifact_id": source_id,
        }
    return output


def _relation_depth(relations: Sequence[Mapping[str, Any]]) -> int:
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        parent = str(relation.get("parent_record_id") or "")
        child = str(relation.get("child_record_id") or "")
        if not parent or not child:
            raise GovInfoDispositionError("GovInfo relation endpoint is invalid")
        adjacency.setdefault(parent, set()).add(child)

    def visit(node: str, stack: frozenset[str]) -> int:
        if node in stack:
            raise GovInfoDispositionError("GovInfo relation graph contains a cycle")
        return max(
            (1 + visit(child, stack | {node}) for child in adjacency.get(node, ())),
            default=0,
        )

    return max(2, max((visit(node, frozenset()) for node in adjacency), default=0))


def replay_govinfo_disposition(
    candidate: Mapping[str, Any],
    evidence_artifact_ids: Sequence[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Execute the cross-schema oracle from exactly the selected artifacts."""
    task = _candidate_task(candidate)
    if candidate.get("context") != wrap_prompt(
        str(candidate.get("question") or ""),
        str(candidate.get("document_context") or ""),
        str(candidate.get("query_timing") or ""),
    ):
        raise GovInfoDispositionError("GovInfo serialized prompt is not canonical")
    _ordered_ids, parsed, classes = _artifact_documents(candidate)
    selected_ids = list(evidence_artifact_ids)
    if len(selected_ids) != len(set(selected_ids)) or any(
        artifact_id not in parsed for artifact_id in selected_ids
    ):
        raise GovInfoDispositionError("GovInfo replay selection is invalid")
    selected = set(selected_ids)
    replay_documents = (
        _counterfactual_documents(candidate, parsed, selected)
        if counterfactual
        else parsed
    )
    relations = {
        (
            str(value.get("bill_id") or ""),
            str(value.get("from_stage") or ""),
            str(value.get("to_stage") or ""),
        ): artifact_id
        for artifact_id, value in replay_documents.items()
        if artifact_id in selected
        and value.get("artifact_type") == "govinfo_transition"
        and value.get("action_date")
        and value.get("action_texts")
    }
    sections: dict[tuple[str, str, str], tuple[str, str]] = {}
    for artifact_id, value in replay_documents.items():
        if (
            artifact_id not in selected
            or value.get("artifact_type") != "govinfo_section"
        ):
            continue
        key = (
            str(value.get("bill_id") or ""),
            str(value.get("stage") or ""),
            str(value.get("base_key") or ""),
        )
        oracle_text = value.get("oracle_text")
        if key in sections or not isinstance(oracle_text, str) or not oracle_text:
            raise GovInfoDispositionError("GovInfo replay section is invalid")
        sections[key] = (artifact_id, oracle_text)
    answer: dict[str, str] = {}
    authentic: list[dict[str, str]] = []
    derived: list[dict[str, str]] = []
    relation_ids: list[str] = []
    for request in candidate["requested_dispositions"]:
        code = str(request["code"])
        bill_id = str(request["bill_id"])
        from_stage = str(request["from_stage"])
        to_stage = str(request["to_stage"])
        base_key = str(request["base_key"])
        relation_artifact = relations.get((bill_id, from_stage, to_stage))
        source = sections.get((bill_id, from_stage, base_key))
        target = sections.get((bill_id, to_stage, base_key))
        if relation_artifact is None or source is None or target is None:
            answer[code] = UNKNOWN
            continue
        similarity = _jaccard(
            _shingles(source[1], int(task["oracle_shingle_size"])),
            _shingles(target[1], int(task["oracle_shingle_size"])),
        )
        answer[code] = (
            RETAINED if similarity >= float(task["oracle_threshold"]) else MODIFIED
        )
        relation_id = f"govinfo:{bill_id}:{from_stage}-{to_stage}:{hashlib.sha256(base_key.encode()).hexdigest()[:20]}"
        source_record = f"{bill_id}:{from_stage}:{base_key}"
        target_record = f"{bill_id}:{to_stage}:{base_key}"
        status_record = f"{bill_id}:bill-status"
        relation_ids.append(relation_id)
        authentic.append(
            {
                "parent_record_id": source_record,
                "child_record_id": target_record,
                "relation_provenance": "authenticated_bill_status_transition",
            }
        )
        derived.extend(
            [
                {
                    "parent_record_id": status_record,
                    "child_record_id": source_record,
                    "relation_provenance": "authenticated_transition_source_endpoint",
                    "source_relation_id": relation_id,
                },
                {
                    "parent_record_id": status_record,
                    "child_record_id": target_record,
                    "relation_provenance": "authenticated_transition_target_endpoint",
                    "source_relation_id": relation_id,
                },
            ]
        )
    source_map = candidate.get("source_record_ids_by_artifact")
    if isinstance(source_map, Mapping):
        source_record_ids = sorted(
            {
                str(record_id)
                for artifact_id in selected_ids
                for record_id in source_map.get(artifact_id, ())
                if record_id
            }
        )
    else:
        source_record_ids = sorted(
            {
                str(classes[artifact_id].get("source_record_id") or artifact_id)
                for artifact_id in selected_ids
            }
        )
    all_relations = [*authentic, *derived]
    return {
        "answer": _canonical_json(answer),
        "source_record_ids": source_record_ids,
        "source_relation_ids": relation_ids,
        "authentic_source_relation_edges": authentic,
        "verified_derived_order_relation_edges": derived,
        "event_count": len(selected),
        "strict_support_event_count": sum(
            value != UNKNOWN for value in answer.values()
        ),
        "proof_depth": _relation_depth(all_relations),
        "hop_count": _relation_depth(all_relations),
    }


def replay_govinfo_disposition_raw_slice(
    candidate: Mapping[str, Any],
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
) -> dict[str, Any]:
    """Replay only complete canonical artifacts visible in an exact raw slice."""
    ordered_ids, parsed, _classes = _artifact_documents(candidate)
    document_by_id = {
        artifact_id: _canonical_json(parsed[artifact_id]) for artifact_id in ordered_ids
    }
    parts = raw_document_context.splitlines()
    if not left_framed and parts:
        parts = parts[1:]
    if not right_framed and parts:
        parts = parts[:-1]
    visible = set(parts)
    selected = [
        artifact_id
        for artifact_id in ordered_ids
        if document_by_id[artifact_id] in visible
    ]
    return replay_govinfo_disposition(candidate, selected)


def materialize_govinfo_counterfactual(
    candidate: Mapping[str, Any],
    artifacts: Sequence[tuple[Mapping[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    """Materialize one source-bound target-body replacement for the CF view."""
    candidate_copy = dict(candidate)
    candidate_copy["artifact_classification"] = [dict(item[0]) for item in artifacts]
    candidate_copy["document_context"] = SEP.join(item[1] for item in artifacts)
    ordered_ids, parsed, _classes = _artifact_documents(candidate_copy)
    twin = candidate.get("counterfactual_twin")
    if not isinstance(twin, Mapping):
        raise GovInfoDispositionError("GovInfo counterfactual twin is missing")
    target_id = str(twin.get("target_artifact_id") or "")
    transformed = _counterfactual_documents(candidate, parsed, set(ordered_ids))
    output: list[tuple[dict[str, Any], str]] = []
    source_binding = candidate.get("source_binding")
    sidecar = candidate.get("task_replay_sidecar")
    if not isinstance(source_binding, Mapping) or not isinstance(sidecar, Mapping):
        raise GovInfoDispositionError(
            "GovInfo counterfactual parent binding is missing"
        )
    for classification, parent_document in artifacts:
        artifact_id = str(classification.get("artifact_id") or "")
        document = _canonical_json(transformed[artifact_id])
        projected_classification = deepcopy(dict(classification))
        if artifact_id == target_id:
            parent_origin = str(classification.get("source_origin") or "")
            parent_provenance = str(classification.get("provenance_id") or "")
            if (
                parent_origin
                not in {"real_public", "real_private_export", "real_derived"}
                or not parent_provenance
            ):
                raise GovInfoDispositionError(
                    "GovInfo counterfactual parent provenance is invalid"
                )
            projected_classification.update(
                {
                    "source_origin": "synthetic_counterfactual",
                    "workflow_kind": "hybrid_causal",
                    "counterfactual_parent_source_origin": parent_origin,
                    "counterfactual_parent_provenance_id": parent_provenance,
                    "counterfactual_parent_text_sha256": hashlib.sha256(
                        parent_document.encode()
                    ).hexdigest(),
                    "counterfactual_parent_source_binding_sha256": _canonical_sha256(
                        source_binding
                    ),
                    "counterfactual_parent_sidecar_sha256": str(
                        sidecar.get("sha256") or ""
                    ),
                    "provenance_id": "counterfactual-projection-sha256:"
                    + hashlib.sha256(document.encode()).hexdigest(),
                }
            )
        output.append((projected_classification, document))
    return output


def govinfo_chronology(
    artifacts: Sequence[tuple[Mapping[str, Any], str]],
) -> list[tuple[str, dict[str, Any], str]]:
    """Order source sections and authenticated transition records by source date."""
    ordered: list[tuple[str, dict[str, Any], str]] = []
    for classification, document in artifacts:
        try:
            value = json.loads(document)
        except json.JSONDecodeError as error:
            raise GovInfoDispositionError(
                "GovInfo chronology artifact is not JSON"
            ) from error
        if not isinstance(value, dict) or document != _canonical_json(value):
            raise GovInfoDispositionError(
                "GovInfo chronology artifact is not canonical"
            )
        artifact_id = str(classification.get("artifact_id") or "")
        if value.get("artifact_type") == "govinfo_section":
            occurred_at = str(value.get("stage_date") or "")
            kind = "0" if value.get("stage") == "enr" else "2"
        elif value.get("artifact_type") == "govinfo_transition":
            occurred_at = str(value.get("action_date") or "")
            kind = "1"
        else:
            raise GovInfoDispositionError(
                "GovInfo chronology artifact type is unsupported"
            )
        if not artifact_id or not occurred_at:
            raise GovInfoDispositionError("GovInfo chronology identity is incomplete")
        key = f"{occurred_at}|{kind}|{artifact_id}"
        ordered.append((key, deepcopy(dict(classification)), document))
    return sorted(ordered, key=lambda item: item[0])


def audit_govinfo_disposition_candidate(
    candidate: Mapping[str, Any],
) -> dict[str, bool]:
    """Audit canonical bytes and the complete source-backed task binding."""
    _candidate_task(candidate)
    ordered_ids, parsed, classifications = _artifact_documents(candidate)
    source_map = candidate.get("source_record_ids_by_artifact")
    checks = {
        "serialized_context_valid": candidate.get("context")
        == wrap_prompt(
            str(candidate.get("question") or ""),
            str(candidate.get("document_context") or ""),
            str(candidate.get("query_timing") or ""),
        ),
        "artifact_json_canonical": bool(parsed),
        "artifact_source_mapping_valid": isinstance(source_map, Mapping)
        and set(source_map) == set(ordered_ids)
        and all(
            isinstance(source_map.get(artifact_id), list)
            and len(source_map[artifact_id]) == 1
            and source_map[artifact_id][0]
            == classifications[artifact_id].get("source_record_id")
            for artifact_id in ordered_ids
        ),
        "source_classification_valid": all(
            classification.get("source_origin")
            in {"real_public", "real_derived", "synthetic_counterfactual"}
            and classification.get("workflow_kind")
            in {"real_source_derived", "hybrid_causal"}
            and classification.get("derived_text_sha256")
            == hashlib.sha256(_canonical_json(parsed[artifact_id]).encode()).hexdigest()
            for artifact_id, classification in classifications.items()
        ),
        "no_padding_or_truncation": candidate.get("padding_tokens", 0) == 0
        and candidate.get("cloned_artifacts", 0) == 0
        and candidate.get("split_or_truncated_sections", 0) == 0
        and candidate.get("truncation_ppm", 0) == 0,
    }
    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if failed:
        raise GovInfoDispositionError(
            "GovInfo candidate audit failed: " + ",".join(failed)
        )
    return checks
