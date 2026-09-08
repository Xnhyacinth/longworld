"""DNSSEC succession oracle and 64k leftover packing without HTTP/3 5-field packing.

Oracle lives here because this family does not own longworld/core/*.py.
Graph signing uses scripts/export_ietf_workflow.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.pack import SEP
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    _APPROVED_EXACT_TOKENIZERS,
    _load_replay_tokenizer_uncached,
    _resolved_local_tokenizer_revision,
    _token_counter_for,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import (
    audit_ietf_workflow_manifest,
    render_ietf_cross_spec_prompt,
)
from longworld.core.taskproof import relation_proof_depth
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from reports.p57_ietf_http3_quic_requirement_generate import (
    _artifacts_for_bucket,
    _canonical_bytes,
    _sha256,
)

IETF_DNSSEC_SUCCESSION_TASK_SCHEMA = "longworld.ietf-dnssec-succession-task.v1"
_DNSSEC_SUCCESSION_ANSWERS = {
    "current_protocol": "DNSSEC_PROTOCOL_RFC4035",
    "updates_dns_concepts": "RFC1034",
    "updates_dns_implementation": "RFC1035",
}
_DNSSEC_SUCCESSION_CODEBOOK = {
    "current_protocol": {
        "code": "DNSSEC_PROTOCOL_RFC4035",
        "meaning": "RFC 4035 is DNSSEC protocol modifications",
    },
    "updates_dns_concepts": {
        "code": "RFC1034",
        "meaning": "RFC 4035 updates DNS concepts and facilities",
    },
    "updates_dns_implementation": {
        "code": "RFC1035",
        "meaning": "RFC 4035 updates DNS implementation and specification",
    },
}
_DNSSEC_SUCCESSION_SCENARIO = {
    "protocol": "dnssec",
    "publication": "rfc4035",
    "succession": "obsoletes_and_updates",
}
_DNSSEC_SUCCESSION_EVIDENCE = {
    "current_protocol": (
        4035,
        r"This document defines the DNSSEC protocol operations\.",
    ),
    "updates_dns_concepts": (
        4035,
        r"Updates: 1034, 1035",
    ),
    "updates_dns_implementation": (
        4035,
        r"described in \[RFC1034\], \[RFC1035\], and the subsequent documents that\s+"
        r"update them",
    ),
}
_DNSSEC_SUCCESSION_BRANCHES = {
    "current_protocol": ("current_protocol", None, None, None),
    "updates_dns_concepts": ("updates_dns_concepts", None, "updates", 1034),
    "updates_dns_implementation": (
        "updates_dns_implementation",
        None,
        "updates",
        1035,
    ),
}
_DNSSEC_RFC_NUMBERS = {1034, 1035, 4033, 4034, 4035}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _dnssec_succession_question() -> str:
    return (
        "Resolve the effective DNSSEC protocol succession at "
        "2026-01-31T00:00:00Z for this scenario (canonical JSON): "
        + json.dumps(
            _DNSSEC_SUCCESSION_SCENARIO, sort_keys=True, separators=(",", ":")
        )
        + ". Return exactly one JSON object with these keys in this order: "
        + json.dumps(tuple(_DNSSEC_SUCCESSION_CODEBOOK), separators=(",", ":"))
        + ". Use this exact per-field output codebook (canonical JSON): "
        + json.dumps(
            _DNSSEC_SUCCESSION_CODEBOOK, sort_keys=True, separators=(",", ":")
        )
        + ". Resolve each field independently from the supplied RFC graph; use "
        'the string "UNKNOWN" only for a field whose required evidence or relation '
        "is absent."
    )


def _dnssec_rfc_records(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    for record in manifest["records"]:
        number = record.get("rfc_number")
        if isinstance(number, int):
            if number in records:
                raise ProvenanceError("IETF DNSSEC RFC identity is duplicated")
            records[number] = record
    if set(records) != _DNSSEC_RFC_NUMBERS:
        raise ProvenanceError("IETF DNSSEC RFC graph is incomplete")
    return records


def _dnssec_succession_relation(
    manifest: dict[str, Any], *, kind: str, target_number: int
) -> dict[str, Any]:
    matches = [
        relation
        for relation in manifest["relations"]
        if relation.get("kind") == kind
        and relation.get("source_record_id") == "ietf:rfc:4035"
        and relation.get("target_record_id") == f"ietf:rfc:{target_number}"
    ]
    if len(matches) != 1:
        raise ProvenanceError("IETF DNSSEC succession relation is not unique")
    return matches[0]


def _dnssec_succession_evidence(
    records: dict[int, dict[str, Any]], evidence_id: str, specification: tuple[int, str]
) -> dict[str, Any]:
    number, pattern = specification
    record = records[number]
    matches = list(re.finditer(pattern, record["text"], re.MULTILINE | re.DOTALL))
    if len(matches) != 1:
        raise ProvenanceError("IETF DNSSEC succession evidence is not unique")
    match = matches[0]
    quote = match.group(0)
    return {
        "evidence_id": evidence_id,
        "record_id": record["record_id"],
        "evidence_quote": quote,
        "char_start": match.start(),
        "char_end": match.end(),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "source_sha256": record["source_sha256"],
    }


def build_ietf_dnssec_succession_task(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compile DNSSEC succession from RFC 4035 Updates of RFC 1034/1035."""
    audit_ietf_workflow_manifest(manifest)
    records = _dnssec_rfc_records(manifest)
    evidence = [
        _dnssec_succession_evidence(records, evidence_id, specification)
        for evidence_id, specification in _DNSSEC_SUCCESSION_EVIDENCE.items()
    ]
    succession_relations = [
        _dnssec_succession_relation(manifest, kind=kind, target_number=target_number)
        for _field, (_current, _dependency, kind, target_number) in (
            _DNSSEC_SUCCESSION_BRANCHES.items()
        )
        if kind is not None and target_number is not None
    ]
    publication = [
        relation
        for relation in manifest["relations"]
        if relation.get("kind") == "published_as"
        and relation.get("target_record_id") == "ietf:rfc:4035"
    ]
    if len(publication) != 1:
        raise ProvenanceError("IETF DNSSEC publication relation is not unique")
    task = {
        "schema_version": IETF_DNSSEC_SUCCESSION_TASK_SCHEMA,
        "query_type": "protocol_succession_resolution",
        "answer_program_id": "ietf.dnssec_succession.v1",
        "question": _dnssec_succession_question(),
        "cutoff": "2026-01-31T00:00:00Z",
        "scenario": dict(_DNSSEC_SUCCESSION_SCENARIO),
        "source_manifest": deepcopy(manifest),
        "source_manifest_sha256": _canonical_sha256(manifest),
        "evidence_items": evidence,
        "essential_evidence_ids": list(_DNSSEC_SUCCESSION_EVIDENCE),
        "essential_relation_ids": [
            publication[0]["relation_id"],
            *(relation["relation_id"] for relation in succession_relations),
        ],
        "answer": dict(_DNSSEC_SUCCESSION_ANSWERS),
    }
    replay_ietf_dnssec_succession_task(task)
    return task


def replay_ietf_dnssec_succession_task(
    task: dict[str, Any],
    *,
    evidence_ids: list[str] | None = None,
    relation_ids: list[str] | None = None,
) -> dict[str, str]:
    """Replay DNSSEC succession with optional evidence or relation removal."""
    required_fields = {
        "schema_version",
        "query_type",
        "answer_program_id",
        "question",
        "cutoff",
        "scenario",
        "source_manifest",
        "source_manifest_sha256",
        "evidence_items",
        "essential_evidence_ids",
        "essential_relation_ids",
        "answer",
    }
    if (
        not isinstance(task, dict)
        or set(task) != required_fields
        or task.get("schema_version") != IETF_DNSSEC_SUCCESSION_TASK_SCHEMA
        or task.get("query_type") != "protocol_succession_resolution"
        or task.get("answer_program_id") != "ietf.dnssec_succession.v1"
        or task.get("question") != _dnssec_succession_question()
        or task.get("cutoff") != "2026-01-31T00:00:00Z"
        or task.get("scenario") != _DNSSEC_SUCCESSION_SCENARIO
        or task.get("essential_evidence_ids") != list(_DNSSEC_SUCCESSION_EVIDENCE)
        or task.get("answer") != _DNSSEC_SUCCESSION_ANSWERS
    ):
        raise ProvenanceError("IETF DNSSEC task contract is invalid")
    manifest = task.get("source_manifest")
    if not isinstance(manifest, dict):
        raise ProvenanceError("IETF DNSSEC source manifest is missing")
    audit_ietf_workflow_manifest(manifest)
    if task.get("source_manifest_sha256") != _canonical_sha256(manifest):
        raise ProvenanceError("IETF DNSSEC source manifest binding is invalid")
    records = _dnssec_rfc_records(manifest)
    raw_evidence = task.get("evidence_items")
    if not isinstance(raw_evidence, list):
        raise ProvenanceError("IETF DNSSEC evidence is invalid")
    items = {
        str(item.get("evidence_id") or ""): item
        for item in raw_evidence
        if isinstance(item, dict)
    }
    if set(items) != set(_DNSSEC_SUCCESSION_EVIDENCE) or len(items) != len(
        raw_evidence
    ):
        raise ProvenanceError("IETF DNSSEC evidence identity is invalid")
    for evidence_id, specification in _DNSSEC_SUCCESSION_EVIDENCE.items():
        expected = _dnssec_succession_evidence(records, evidence_id, specification)
        if items[evidence_id] != expected:
            raise ProvenanceError("IETF DNSSEC evidence binding is invalid")
    all_evidence = set(items)
    selected_evidence = all_evidence if evidence_ids is None else set(evidence_ids)
    if (
        not isinstance(evidence_ids, (list, type(None)))
        or len(selected_evidence) != len(evidence_ids or selected_evidence)
        or not selected_evidence.issubset(all_evidence)
    ):
        raise ProvenanceError("IETF DNSSEC evidence selection is invalid")
    relations = {
        str(relation["relation_id"]): relation for relation in manifest["relations"]
    }
    essential_relations = task.get("essential_relation_ids")
    if (
        not isinstance(essential_relations, list)
        or len(set(essential_relations)) != len(essential_relations)
        or any(item not in relations for item in essential_relations)
    ):
        raise ProvenanceError("IETF DNSSEC relation identity is invalid")
    selected_relations = (
        set(essential_relations) if relation_ids is None else set(relation_ids)
    )
    if (
        not isinstance(relation_ids, (list, type(None)))
        or len(selected_relations) != len(relation_ids or selected_relations)
        or not selected_relations.issubset(set(essential_relations))
    ):
        raise ProvenanceError("IETF DNSSEC relation selection is invalid")
    publication = next(
        relation
        for relation in manifest["relations"]
        if relation.get("kind") == "published_as"
        and relation.get("target_record_id") == "ietf:rfc:4035"
    )
    expected_essential_relations = [
        publication["relation_id"],
        *(
            _dnssec_succession_relation(
                manifest, kind=kind, target_number=target_number
            )["relation_id"]
            for _field, (_current, _dependency, kind, target_number) in (
                _DNSSEC_SUCCESSION_BRANCHES.items()
            )
            if kind is not None and target_number is not None
        ),
    ]
    if essential_relations != expected_essential_relations:
        raise ProvenanceError("IETF DNSSEC task contract is invalid")
    result: dict[str, str] = {}
    publication_present = publication["relation_id"] in selected_relations
    for field, (current, dependency, kind, target_number) in (
        _DNSSEC_SUCCESSION_BRANCHES.items()
    ):
        needed = {current} if dependency is None else {current, dependency}
        relations_present = publication_present
        if kind is not None and target_number is not None:
            relation = _dnssec_succession_relation(
                manifest, kind=kind, target_number=target_number
            )
            relations_present = (
                publication_present and relation["relation_id"] in selected_relations
            )
        if needed.issubset(selected_evidence) and relations_present:
            result[field] = _DNSSEC_SUCCESSION_ANSWERS[field]
        else:
            result[field] = "UNKNOWN"
    return result


def audit_ietf_dnssec_succession_task(task: dict[str, Any]) -> dict[str, bool]:
    """Audit strict replay plus every remove-one evidence and relation replay."""
    answer = task.get("answer")
    evidence = list(task.get("essential_evidence_ids") or [])
    relations = list(task.get("essential_relation_ids") or [])
    return {
        "strict_replay": replay_ietf_dnssec_succession_task(task) == answer,
        "remove_one_evidence_fails": bool(evidence)
        and all(
            replay_ietf_dnssec_succession_task(
                task,
                evidence_ids=[item for item in evidence if item != removed],
            )
            != answer
            for removed in evidence
        ),
        "remove_one_relation_fails": bool(relations)
        and all(
            replay_ietf_dnssec_succession_task(
                task,
                relation_ids=[item for item in relations if item != removed],
            )
            != answer
            for removed in relations
        ),
    }


def materialize_ietf_dnssec_counterfactual(
    task: dict[str, Any],
    *,
    evidence_id: str = "current_protocol",
) -> dict[str, Any]:
    """Exclude one byte-bound DNSSEC succession quote without invented text."""
    replay_ietf_dnssec_succession_task(task)
    evidence = next(
        (
            item
            for item in task["evidence_items"]
            if item.get("evidence_id") == evidence_id
        ),
        None,
    )
    if not isinstance(evidence, dict):
        raise ProvenanceError("IETF DNSSEC counterfactual requirement is invalid")
    manifest = task["source_manifest"]
    record = next(
        item
        for item in manifest["records"]
        if item.get("record_id") == evidence.get("record_id")
    )
    parent = str(record["text"])
    parent_value = str(evidence["evidence_quote"])
    char_start = int(evidence["char_start"])
    char_end = int(evidence["char_end"])
    if parent[char_start:char_end] != parent_value:
        raise ProvenanceError("IETF DNSSEC counterfactual requirement span is invalid")
    value = " " * len(parent_value)
    child = parent[:char_start] + value + parent[char_end:]
    byte_start = len(parent[:char_start].encode())
    byte_end = byte_start + len(parent_value.encode())
    selected_evidence = [
        item["evidence_id"]
        for item in task["evidence_items"]
        if item.get("evidence_id") != evidence["evidence_id"]
    ]
    answer = replay_ietf_dnssec_succession_task(task, evidence_ids=selected_evidence)
    return {
        "record_id": record["record_id"],
        "parent_text": parent,
        "text": child,
        "answer": answer,
        "counterfactual_twin": {
            "provenance_operation": "exclude_exact_source_span",
            "source_origin": "synthetic_counterfactual",
            "record_id": record["record_id"],
            "evidence_id": evidence["evidence_id"],
            "char_start": char_start,
            "char_end": char_end,
            "byte_start": byte_start,
            "byte_end": byte_end,
            "parent_value": parent_value,
            "value": value,
            "parent_text_sha256": hashlib.sha256(parent.encode()).hexdigest(),
            "text_sha256": hashlib.sha256(child.encode()).hexdigest(),
            "parent_source_sha256": record["source_sha256"],
            "source_manifest_sha256": task["source_manifest_sha256"],
        },
    }


def _replay_dnssec_candidate(
    candidate: dict[str, Any],
    evidence_artifact_ids: list[str],
    *,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay DNSSEC succession from selected source-record artifacts."""
    from longworld.core.taskproof import TaskProofError

    task = candidate.get("ietf_requirement_task")
    source_records = candidate.get("source_record_ids_by_artifact")
    classifications = candidate.get("artifact_classification")
    if (
        not isinstance(task, dict)
        or not isinstance(source_records, dict)
        or not isinstance(classifications, list)
    ):
        raise TaskProofError("IETF task replay contract is missing")
    if candidate.get("question") != task.get("question"):
        raise TaskProofError("candidate question is not bound to the IETF task")
    classification_by_artifact = {
        str(item.get("artifact_id") or ""): item
        for item in classifications
        if isinstance(item, dict)
    }
    if len(classification_by_artifact) != len(classifications):
        raise TaskProofError("IETF task replay artifact spans are invalid")
    document_context = candidate.get("document_context")
    documents = document_context.split(SEP) if isinstance(document_context, str) else []
    if len(documents) != len(classifications):
        raise TaskProofError("IETF task replay artifact bytes are missing")
    document_by_artifact = {
        str(classification.get("artifact_id") or ""): document
        for classification, document in zip(classifications, documents, strict=True)
        if isinstance(classification, dict)
    }
    if len(document_by_artifact) != len(documents):
        raise TaskProofError("IETF task replay artifact bytes are invalid")
    selected_artifacts = list(evidence_artifact_ids)
    if len(selected_artifacts) != len(set(selected_artifacts)) or any(
        artifact_id not in source_records for artifact_id in selected_artifacts
    ):
        raise TaskProofError("IETF task replay artifact selection is invalid")
    manifest_records = {
        str(record.get("record_id") or ""): record
        for record in (task.get("source_manifest") or {}).get("records") or []
        if isinstance(record, dict)
    }
    selected_spans: dict[str, list[tuple[int, int]]] = {}
    for artifact_id in selected_artifacts:
        record_ids = source_records[artifact_id]
        classification = classification_by_artifact.get(artifact_id)
        if (
            not isinstance(record_ids, list)
            or len(record_ids) != 1
            or not isinstance(record_ids[0], str)
            or not isinstance(classification, dict)
            or classification.get("source_record_id") != record_ids[0]
        ):
            raise TaskProofError("IETF task replay source mapping is invalid")
        record_id = record_ids[0]
        record = manifest_records.get(record_id)
        start = classification.get("source_char_start")
        end = classification.get("source_char_end")
        if (
            not isinstance(record, dict)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not 0 <= start < end <= len(str(record.get("text") or ""))
        ):
            raise TaskProofError("IETF task replay artifact spans are invalid")
        source_document = str(record["text"])[start:end]
        document = document_by_artifact[artifact_id]
        if classification.get("source_origin") == "synthetic_counterfactual":
            twin = candidate.get("counterfactual_twin")
            evidence = next(
                (
                    item
                    for item in task.get("evidence_items") or []
                    if isinstance(item, dict)
                    and item.get("evidence_id")
                    == (twin.get("evidence_id") if isinstance(twin, dict) else None)
                ),
                None,
            )
            evidence_start = (
                evidence.get("char_start") if isinstance(evidence, dict) else None
            )
            evidence_end = (
                evidence.get("char_end") if isinstance(evidence, dict) else None
            )
            parent_value = (
                evidence.get("evidence_quote") if isinstance(evidence, dict) else None
            )
            if (
                not isinstance(twin, dict)
                or twin.get("provenance_operation") != "exclude_exact_source_span"
                or not isinstance(evidence, dict)
                or evidence.get("record_id") != record_id
                or not isinstance(evidence_start, int)
                or not isinstance(evidence_end, int)
                or not isinstance(parent_value, str)
                or not start <= evidence_start < evidence_end <= end
            ):
                raise TaskProofError("IETF counterfactual artifact bytes are invalid")
            local_start = evidence_start - start
            local_end = evidence_end - start
            expected = (
                source_document[:local_start]
                + " " * len(parent_value)
                + source_document[local_end:]
            )
            if source_document[local_start:local_end] != parent_value or document != expected:
                raise TaskProofError("IETF counterfactual artifact bytes are invalid")
        elif document != source_document:
            raise TaskProofError("IETF task replay artifact source bytes are invalid")
        selected_spans.setdefault(record_id, []).append((start, end))
    selected_records = set(selected_spans)
    excluded_evidence_id = ""
    if counterfactual:
        twin = candidate.get("counterfactual_twin")
        excluded_evidence_id = (
            str(twin.get("evidence_id") or "") if isinstance(twin, dict) else ""
        )
        if excluded_evidence_id not in {
            str(item.get("evidence_id") or "")
            for item in task.get("evidence_items") or []
            if isinstance(item, dict)
        }:
            raise TaskProofError("IETF counterfactual evidence binding is invalid")
    evidence_ids = [
        item["evidence_id"]
        for item in task.get("evidence_items") or []
        if any(
            start <= item.get("char_start") and item.get("char_end") <= end
            for start, end in selected_spans.get(str(item.get("record_id") or ""), [])
        )
        and item.get("evidence_id") != excluded_evidence_id
    ]
    relations = [
        relation
        for relation in (task.get("source_manifest") or {}).get("relations") or []
        if relation.get("source_record_id") in selected_records
        and relation.get("target_record_id") in selected_records
    ]
    relation_ids = [str(relation["relation_id"]) for relation in relations]
    replay_relation_ids = [
        relation_id
        for relation_id in task.get("essential_relation_ids") or []
        if relation_id in relation_ids
    ]
    answer = replay_ietf_dnssec_succession_task(
        task,
        evidence_ids=evidence_ids,
        relation_ids=replay_relation_ids,
    )
    proof_depth = relation_proof_depth(
        [
            {
                "parent_record_id": relation["source_record_id"],
                "child_record_id": relation["target_record_id"],
            }
            for relation in relations
        ]
        if relations
        else []
    )
    return {
        "answer": json.dumps(answer, sort_keys=True, separators=(",", ":")),
        "source_record_ids": sorted(selected_records),
        "source_relation_ids": relation_ids,
        "authentic_source_relation_edges": [
            {
                "parent_record_id": relation["source_record_id"],
                "child_record_id": relation["target_record_id"],
                "relation_provenance": relation["kind"],
            }
            for relation in relations
        ],
        "verified_derived_order_relation_edges": [],
        "event_count": len(evidence_ids),
        "strict_support_event_count": len(
            [value for value in answer.values() if value != "UNKNOWN"]
        ),
        "proof_depth": proof_depth,
        "hop_count": proof_depth,
    }


def _dnssec_token_counter(tokenizer: dict[str, Any]):
    model_id = str(tokenizer["model_id"])
    revision = str(tokenizer["revision"])
    asset_digest = str(tokenizer["asset_manifest_sha256"])
    if (model_id, revision) not in _APPROVED_EXACT_TOKENIZERS:
        raise ValueError("DNSSEC exact tokenizer pin is not approved")
    loaded_revision = _resolved_local_tokenizer_revision(model_id, revision)
    observed_asset_digest = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    if loaded_revision != revision or observed_asset_digest != asset_digest:
        raise ValueError("DNSSEC exact tokenizer assets do not match")
    loaded = _load_replay_tokenizer_uncached(model_id, revision)
    loaded_asset_digest = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    if loaded_asset_digest != observed_asset_digest:
        raise ValueError("DNSSEC exact tokenizer assets do not match")
    counter = _token_counter_for(loaded)
    if counter is None:
        raise ValueError("DNSSEC exact tokenizer is unavailable")
    counter.offset_tokenizer = loaded
    counter._json_window_tokenizer = loaded
    return counter


def build(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.ietf-dnssec-generation-config.v1":
        raise ValueError("unsupported IETF DNSSEC generation config")
    if config["packing"].get("isolate_evidence_ids"):
        raise ValueError("DNSSEC packing must not isolate HTTP/3-style quote artifacts")
    if any(bucket in config["length_buckets"] for bucket in ("16k", "32k", "128k")):
        raise ValueError("DNSSEC unique leftover is 64k-only; do not pad to 128k")
    if list(config["length_buckets"]) != ["64k"]:
        raise ValueError("DNSSEC packing is natural 64k only")
    bucket_chunk = int(
        (config["packing"].get("chunk_max_tokens_by_bucket") or {}).get("64k")
        or config["packing"].get("chunk_max_tokens")
        or 0
    )
    if bucket_chunk < 8192:
        raise ValueError(
            "DNSSEC 64k leftover chunks must stay thick enough for 4k zipper ends"
        )
    source_dir = Path(config["source_inventory_dir"])
    inventory_path = source_dir / config["fetch_inventory_file"]
    inventory_raw = inventory_path.read_bytes()
    if hashlib.sha256(inventory_raw).hexdigest() != config["fetch_inventory_sha256"]:
        raise ValueError("DNSSEC fetch inventory hash changed")
    signed_path = source_dir / config["signed_workflow_file"]
    signed = json.loads(signed_path.read_text())
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None or not verify_attestation(
        signed, source_key, purpose="source_manifest"
    ):
        raise ValueError("DNSSEC signed workflow attestation is invalid")
    if hashlib.sha256(signed_path.read_bytes()).hexdigest() != config[
        "signed_workflow_sha256"
    ]:
        raise ValueError("DNSSEC signed workflow hash changed")
    manifest = {key: value for key, value in signed.items() if key != "attestation"}
    task = build_ietf_dnssec_succession_task(manifest)
    materialized = materialize_ietf_dnssec_counterfactual(
        task,
        evidence_id=str(
            config["packing"].get("counterfactual_evidence_id") or "current_protocol"
        ),
    )
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if candidate_key is None:
        raise ValueError("DNSSEC generate requires a local probe candidate key")
    tokenizer = config["tokenizer"]
    token_counter = _dnssec_token_counter(tokenizer)
    output_dir = Path(config["output_dir"]).resolve()
    if (output_dir / "parents.jsonl").exists():
        raise ValueError(f"DNSSEC generate refuses to overwrite {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    unsigned: list[dict[str, Any]] = []
    packs: dict[str, dict[str, Any]] = {}
    for bucket, (lower, upper) in config["length_buckets"].items():
        configured_record_ids = (
            config["packing"].get("record_ids_by_bucket") or {}
        ).get(bucket)
        artifacts, observed_tokens = _artifacts_for_bucket(
            task,
            token_counter,
            bucket,
            int(lower),
            int(upper),
            int(config["packing"]["target_margin_tokens"]),
            chunked_record_ids=frozenset(
                config["packing"].get("chunked_record_ids") or []
            ),
            chunk_max_tokens=int(
                (config["packing"].get("chunk_max_tokens_by_bucket") or {}).get(bucket)
                or config["packing"].get("chunk_max_tokens")
                or 0
            ),
            span_id_width=int(config["packing"].get("span_id_width") or 0),
            isolate_evidence_ids=frozenset(),
            support_priority_record_ids=tuple(
                config["packing"].get("support_priority_record_ids") or []
            ),
            allowed_record_ids=(
                frozenset(configured_record_ids)
                if configured_record_ids is not None
                else None
            ),
            published_draft_policy=str(
                config["packing"].get("published_draft_policy") or ""
            ),
            pin_last_leftover_record_ids=frozenset(
                config["packing"].get("pin_last_leftover_record_ids") or []
            ),
        )
        classifications = [
            {
                "artifact_id": item["artifact_id"],
                "workflow_id": config.get(
                    "workflow_id", "p57-ietf-dnssec-succession-v1"
                ),
                "source_origin": "real_public",
                "workflow_kind": "real_source_derived",
                "evidence_role": (
                    "causal_gold" if item["essential"] else "causal_supporting"
                ),
                "provenance_id": (
                    f"source-span-sha256:{item['source_sha256']}:{item['char_start']}:"
                    f"{item['char_end']}:{item['text_sha256']}"
                ),
                "source_url": item["source_url"],
                "source_record_id": item["record_id"],
                "source_char_start": item["char_start"],
                "source_char_end": item["char_end"],
            }
            for item in artifacts
        ]
        source_map = {item["artifact_id"]: [item["record_id"]] for item in artifacts}
        essential_ids = [item["artifact_id"] for item in artifacts if item["essential"]]
        document_context = SEP.join(item["text"] for item in artifacts)
        question = str(task["question"])
        candidate: dict[str, Any] = {
            "schema_version": "longworld.ietf-dnssec-generation-candidate.v1",
            "world_id": config.get("world_id", "ietf-dnssec-succession-v1"),
            "query_id": (
                f"{config.get('world_id', 'ietf-dnssec-succession-v1')}:{bucket}"
            ),
            "domain": "standards",
            "data_stage": "candidate",
            "training_objective": "sft",
            "length_bucket": bucket,
            "view": "full",
            "composition_method": "same_case_dossier",
            "query_timing": "first",
            "question": question,
            "answer": "",
            "cf_answer": "",
            "document_context": document_context,
            "context": render_ietf_cross_spec_prompt(
                question, document_context, "first"
            ),
            "artifact_classification": classifications,
            "source_record_ids_by_artifact": source_map,
            "essential_artifact_ids": essential_ids,
            "source_binding": {
                "signed_manifest_sha256": task["source_manifest_sha256"]
            },
            "source_family_ids": ["ietf_standards"],
            "strict_replay_revision": "longworld.ietf-dnssec-succession-replay.v1",
            "ietf_requirement_task": deepcopy(task),
            "counterfactual_twin": deepcopy(materialized["counterfactual_twin"]),
            "answer_program_id": task["answer_program_id"],
            "graph": {"proof_depth": 2, "hop_count": 2},
            "real_source_token_ratio": 1.0,
            "tokenizer_model_id": tokenizer["model_id"],
            "tokenizer_revision": tokenizer["revision"],
            "tokenizer_asset_manifest_sha256": tokenizer["asset_manifest_sha256"],
            "tokenizer_context_tokens": observed_tokens,
            "actual_context_tokens": observed_tokens,
            "train_ready": False,
            "production_eligible": False,
            "promoted": False,
        }
        replay = _replay_dnssec_candidate(candidate, list(source_map))
        counterfactual_replay = _replay_dnssec_candidate(
            candidate, list(source_map), counterfactual=True
        )
        expected_answer = json.dumps(
            task["answer"], sort_keys=True, separators=(",", ":")
        )
        if replay["answer"] != expected_answer:
            raise ValueError(
                f"{bucket} DNSSEC parent replay is not the bound codebook: "
                f"{replay['answer']}"
            )
        if counterfactual_replay["answer"] == replay["answer"]:
            raise ValueError(f"{bucket} DNSSEC counterfactual collapsed onto the parent")
        candidate["answer"] = replay["answer"]
        candidate["cf_answer"] = counterfactual_replay["answer"]
        essential_ids = list(source_map)
        for artifact_id in list(essential_ids):
            reduced_ids = [item for item in essential_ids if item != artifact_id]
            if (
                _replay_dnssec_candidate(candidate, reduced_ids)["answer"]
                == candidate["answer"]
            ):
                essential_ids = reduced_ids
        if (
            not essential_ids
            or _replay_dnssec_candidate(candidate, essential_ids)["answer"]
            != candidate["answer"]
            or any(
                _replay_dnssec_candidate(
                    candidate,
                    [item for item in essential_ids if item != removed],
                )["answer"]
                == candidate["answer"]
                for removed in essential_ids
            )
        ):
            raise ValueError("IETF DNSSEC essential artifact minimization failed")
        candidate["essential_artifact_ids"] = essential_ids
        for classification in classifications:
            classification["evidence_role"] = (
                "causal_gold"
                if classification["artifact_id"] in essential_ids
                else "causal_supporting"
            )
        candidate["graph"] = {
            "proof_depth": replay["proof_depth"],
            "hop_count": replay["hop_count"],
        }
        for field in (
            "source_record_ids",
            "source_relation_ids",
            "authentic_source_relation_edges",
            "verified_derived_order_relation_edges",
            "event_count",
            "strict_support_event_count",
        ):
            candidate[field] = deepcopy(replay[field])
        unsigned.append(candidate)
        packs[bucket] = {
            "parent_prompt_tokens": observed_tokens,
            "artifact_count": len(artifacts),
            "essential_artifact_count": len(essential_ids),
            "source_span_bytes": sum(len(item["text"].encode()) for item in artifacts),
        }

    signed_rows: list[dict[str, Any]] = []
    for candidate in unsigned:
        signed_rows.append(
            attach_attestation(
                candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
        )
    candidates_raw = b"".join(_canonical_bytes(row) for row in signed_rows)
    (output_dir / "parents.jsonl").write_bytes(candidates_raw)
    receipt = {
        "schema_version": "longworld.ietf-dnssec-generation-receipt.v1",
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "selected": False,
        "promoted": False,
        "fetch_inventory_sha256": config["fetch_inventory_sha256"],
        "manifest_sha256": _sha256(manifest),
        "task_sha256": _sha256(task),
        "parents_sha256": hashlib.sha256(candidates_raw).hexdigest(),
        "packs": packs,
        "sidecar_adapter_blocker": (
            "standards.ietf_dnssec_succession.v1 is not registered in core; "
            "dense rank+audit via project_task_candidate_views is blocked"
        ),
    }
    (output_dir / "GENERATION_RECEIPT.json").write_bytes(_canonical_bytes(receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().config), sort_keys=True))


if __name__ == "__main__":
    main()
