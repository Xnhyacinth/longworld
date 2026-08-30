"""Fail-closed Federal Register and Regulations.gov source workflow.

The resulting manifest is a disabled source inventory, not a training world.
Federal Register publication dates are source event times.  Regulations.gov
docket metadata is only a current observation; ``modifyDate`` is never treated
as a historical snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    attestation_key_from_env,
    verify_attestation,
    verify_attestation_identity,
)
from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)
from longworld.core.publicscan import (
    PUBLIC_SCANNER,
    PUBLIC_SCANNER_REVISION,
    sanitize_public_text,
    validate_sanitized_public_payload,
)

REGULATION_FETCH_REQUEST_SCHEMA = "longworld.regulation-fetch-request.v1"
REGULATION_FETCH_INVENTORY_SCHEMA = "longworld.regulation-fetch-inventory.v1"
REGULATION_WORKFLOW_MANIFEST_SCHEMA = "longworld.regulation-workflow-manifest.v1"
FEDERAL_REGISTER_DOCUMENT_URL = "https://www.federalregister.gov/api/v1/documents"
REGULATIONS_DOCKET_URL = "https://api.regulations.gov/v4/dockets"
FEDERAL_REGISTER_TERMS_URL = (
    "https://www.federalregister.gov/reader-aids/"
    "government-policy-and-ofr-procedures/about-this-site"
)
REGULATIONS_TERMS_URL = "https://open.gsa.gov/api/regulationsgov/"
FEDERAL_REGISTER_LICENSE = (
    "U.S. Government public rulemaking metadata; source terms apply."
)
REGULATIONS_LICENSE = (
    "U.S. Government public docket metadata; Regulations.gov terms apply."
)
MAX_REGULATION_MANIFEST_BYTES = 24_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOCKET_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-[12]\d{3}-\d{4,8}$")
_DOCUMENT_NUMBER = re.compile(r"^(?:[12]\d{3}|\d{2}|E\d{1,2}|X\d{2})-[0-9A-Z]+$")
_RIN = re.compile(r"^\d{4}-[A-Z]{2}\d{2}$")
_INVENTORY_FIELDS = {
    "schema_version",
    "source_status",
    "data_stage",
    "hybrid_train_ready",
    "production_eligible",
    "generation_integration",
    "semantic_facts_train_ready",
    "snapshot_semantics",
    "historical_snapshot_claims",
    "comment_content_collected",
    "generated_at",
    "request_file",
    "request_sha256",
    "authorization",
    "source_policies",
    "fetch_receipt",
    "n_retrievals",
}


def _regular_bytes(base: Path, filename: object, digest: object) -> bytes:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ProvenanceError("regulation retrieval file is unsafe")
    expected = str(digest or "")
    if _SHA256.fullmatch(expected) is None:
        raise ProvenanceError("regulation retrieval hash is invalid")
    try:
        raw = _read_regular_file(base / filename, MAX_SOURCE_BYTES)
    except OSError as error:
        raise ProvenanceError(f"cannot read regulation retrieval: {error}") from error
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ProvenanceError("regulation retrieval hash mismatch")
    return raw


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProvenanceError(f"{label} must be an object")
    return value


def _request_binding(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "user_agent",
            "authorization",
            "docket_id",
            "federal_register_document_numbers",
            "regulations_api_key_env",
            "requests_per_second",
            "max_retries",
        }
        or value.get("schema_version") != REGULATION_FETCH_REQUEST_SCHEMA
    ):
        raise ProvenanceError("regulation fetch request binding is invalid")
    docket_id = value.get("docket_id")
    document_numbers = value.get("federal_register_document_numbers")
    if (
        not isinstance(docket_id, str)
        or _DOCKET_ID.fullmatch(docket_id) is None
        or not isinstance(document_numbers, list)
        or not document_numbers
        or len(set(document_numbers)) != len(document_numbers)
        or any(
            not isinstance(item, str) or _DOCUMENT_NUMBER.fullmatch(item) is None
            for item in document_numbers
        )
        or not isinstance(value.get("authorization"), dict)
    ):
        raise ProvenanceError("regulation fetch request identities are invalid")
    return {
        "docket_id": docket_id,
        "document_numbers": document_numbers,
        "authorization": value["authorization"],
    }


def _validate_retrieval_metadata(item: object) -> dict[str, Any]:
    common = {
        "kind",
        "requested_url",
        "final_url",
        "status",
        "content_type",
        "redirect_chain",
        "observed_at",
        "sha256",
        "retrieval_file",
    }
    if not isinstance(item, dict):
        raise ProvenanceError("regulation retrieval receipt is invalid")
    kind = item.get("kind")
    expected_fields = common | (
        {"document_number"}
        if kind == "federal_register_document"
        else {"docket_id"}
        if kind == "regulations_docket_metadata"
        else set()
    )
    if set(item) != expected_fields:
        raise ProvenanceError("regulation retrieval receipt is invalid")
    url = str(item.get("requested_url") or "")
    parsed = urlparse(url)
    if kind == "federal_register_document":
        identity = item.get("document_number")
        expected_url = f"{FEDERAL_REGISTER_DOCUMENT_URL}/{identity}.json"
        expected_content_type = "application/json"
    else:
        identity = item.get("docket_id")
        expected_url = f"{REGULATIONS_DOCKET_URL}/{identity}"
        expected_content_type = "application/vnd.api+json"
    if (
        parsed.scheme != "https"
        or parsed.query
        or parsed.fragment
        or url != expected_url
        or item.get("final_url") != url
        or item.get("status") != 200
        or item.get("content_type") != expected_content_type
        or item.get("redirect_chain") != []
    ):
        raise ProvenanceError(
            "regulation retrieval URL or transport metadata is invalid"
        )
    _parse_timestamp(str(item.get("observed_at") or ""), "regulation observed_at")
    return dict(item)


def _validate_retrieval(
    item: object, base_directory: Path
) -> tuple[dict[str, Any], bytes]:
    validated = _validate_retrieval_metadata(item)
    return validated, _regular_bytes(
        base_directory, validated.get("retrieval_file"), validated.get("sha256")
    )


def _fact(text: str, *, fact_id: str, field: str, value: str) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    pattern = re.compile(
        rf'^  "{re.escape(field)}"\s*:\s*(?P<value>{re.escape(encoded)}),?$',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ProvenanceError(f"regulation source does not uniquely bind {field}")
    match = matches[0]
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": match.group(0),
        "char_start": match.start(),
        "char_end": match.end(),
    }


def _record(
    *,
    record_id: str,
    kind: str,
    selected: dict[str, str],
    retrieval: dict[str, Any],
    source_raw: bytes,
    fact_fields: tuple[str, ...],
    occurred_at: str | None = None,
) -> dict[str, Any]:
    canonical = json.dumps(selected, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        clean, redactions = sanitize_public_text(canonical)
    except ValueError as error:
        raise ProvenanceError("regulation source failed the public scanner") from error
    source_sha256 = hashlib.sha256(source_raw).hexdigest()
    is_docket = kind == "regulations_docket_current_metadata"
    record = {
        "record_id": record_id,
        "kind": kind,
        "observed_at": retrieval["observed_at"],
        "temporal_semantics": (
            "current_snapshot_observation" if is_docket else "source_event_time"
        ),
        "source_family": "regulations_gov" if is_docket else "federal_register",
        "source_origin": "real_public",
        "source_url": retrieval["requested_url"],
        "retrieval_url": retrieval["requested_url"],
        "source_sha256": source_sha256,
        "source_record_sha256": hashlib.sha256(clean.encode()).hexdigest(),
        "text_sha256": hashlib.sha256(clean.encode()).hexdigest(),
        "provenance_id": f"sha256:{source_sha256}",
        "license": REGULATIONS_LICENSE if is_docket else FEDERAL_REGISTER_LICENSE,
        "attribution": "Regulations.gov" if is_docket else "Federal Register",
        "terms_url": REGULATIONS_TERMS_URL if is_docket else FEDERAL_REGISTER_TERMS_URL,
        "parser": "longworld-regulation-json@1",
        "text": clean,
        "facts": [
            _fact(clean, fact_id=field, field=field, value=selected[field])
            for field in fact_fields
        ],
        "privacy_review": {
            "comments_collected": False,
            "emails": "redacted",
            "email_redaction_count": len(redactions),
            "secrets": "fail_closed",
            "scanner": PUBLIC_SCANNER,
            "scanner_revision": PUBLIC_SCANNER_REVISION,
        },
    }
    if occurred_at is not None:
        record["occurred_at"] = occurred_at
    return record


def _classify_document(value: dict[str, Any]) -> str:
    title = str(value.get("title") or "")
    action = str(value.get("action") or "")
    abstract = str(value.get("abstract") or "")
    combined = f"{title} {action} {abstract}".lower()
    if "extension" in combined and "comment" in combined:
        return "federal_register_comment_extension"
    if value.get("type") == "Rule" and "final rule" in combined:
        return "federal_register_final_rule"
    if value.get("type") == "Proposed Rule" and (
        "proposed rule" in combined or "proposing" in combined
    ):
        return "federal_register_proposed_rule"
    raise ProvenanceError("Federal Register document cannot be semantically classified")


def _evidence(record: dict[str, Any], fact_ids: tuple[str, ...]) -> dict[str, Any]:
    facts = [
        next(item for item in record["facts"] if item["fact_id"] == fact_id)
        for fact_id in fact_ids
    ]
    return {
        "record_id": record["record_id"],
        "fact_ids": list(fact_ids),
        "evidence_quotes": [item["evidence_quote"] for item in facts],
        "char_spans": [[item["char_start"], item["char_end"]] for item in facts],
        "source_sha256": record["source_sha256"],
    }


def _relation(
    relation_id: str,
    kind: str,
    source: dict[str, Any],
    target: dict[str, Any],
    source_fact_ids: tuple[str, ...],
    target_fact_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "relation_id": relation_id,
        "kind": kind,
        "source_record_id": source["record_id"],
        "target_record_id": target["record_id"],
        "evidence": [
            _evidence(source, source_fact_ids),
            _evidence(target, target_fact_ids),
        ],
    }


def build_regulation_workflow_from_fetch_inventory(
    payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    fetch_inventory_sha256: str,
) -> dict[str, Any]:
    """Validate official bytes and derive a disabled rulemaking source graph."""
    if set(payload) != _INVENTORY_FIELDS or not (
        payload.get("schema_version") == REGULATION_FETCH_INVENTORY_SCHEMA
        and payload.get("source_status") == "public_api_export"
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("snapshot_semantics")
        == "mixed_source_event_and_current_docket_metadata"
        and payload.get("historical_snapshot_claims") is False
        and payload.get("comment_content_collected") is False
    ):
        raise ProvenanceError("regulation fetch inventory schema or flags are invalid")
    if _SHA256.fullmatch(fetch_inventory_sha256) is None:
        raise ProvenanceError("regulation fetch inventory hash is invalid")
    generated_time = _parse_timestamp(generated_at, "generated_at")
    started_time = _parse_timestamp(
        str(payload.get("generated_at") or ""), "regulation fetch generated_at"
    )
    request_raw = _regular_bytes(
        base_directory, payload.get("request_file"), payload.get("request_sha256")
    )
    request = _request_binding(_json_object(request_raw, "regulation fetch request"))
    if request["authorization"] != payload.get("authorization"):
        raise ProvenanceError("regulation authorization is not request-bound")
    if payload.get("source_policies") != {
        "federal_register": FEDERAL_REGISTER_TERMS_URL,
        "regulations_gov": REGULATIONS_TERMS_URL,
    }:
        raise ProvenanceError("regulation source policies are invalid")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "retrievals",
    }:
        raise ProvenanceError("regulation fetch receipt is invalid")
    raw_retrievals = receipt.get("retrievals")
    if (
        receipt.get("started_at") != payload.get("generated_at")
        or not isinstance(raw_retrievals, list)
        or len(raw_retrievals) != payload.get("n_retrievals")
        or len(raw_retrievals) != len(request["document_numbers"]) + 1
    ):
        raise ProvenanceError("regulation retrieval count or binding is invalid")
    retrievals = [_validate_retrieval(item, base_directory) for item in raw_retrievals]
    completed_time = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "regulation fetch completed_at"
    )
    observed_times = [
        _parse_timestamp(item[0]["observed_at"], "regulation observed_at")
        for item in retrievals
    ]
    if not (
        started_time < observed_times[0]
        and all(prior < current for prior, current in pairwise(observed_times))
        and observed_times[-1] < completed_time <= generated_time
    ):
        raise ProvenanceError("regulation observation timeline is invalid")
    expected_urls = {
        *(
            f"{FEDERAL_REGISTER_DOCUMENT_URL}/{item}.json"
            for item in request["document_numbers"]
        ),
        f"{REGULATIONS_DOCKET_URL}/{request['docket_id']}",
    }
    if {item[0]["requested_url"] for item in retrievals} != expected_urls:
        raise ProvenanceError("regulation retrievals do not exactly match the request")

    docket_pair = next(
        (
            item
            for item in retrievals
            if item[0]["kind"] == "regulations_docket_metadata"
        ),
        None,
    )
    document_pairs = {
        item[0]["document_number"]: item
        for item in retrievals
        if item[0]["kind"] == "federal_register_document"
    }
    if docket_pair is None or set(document_pairs) != set(request["document_numbers"]):
        raise ProvenanceError("regulation retrieval identities are incomplete")
    docket_value = _json_object(docket_pair[1], "Regulations.gov docket")
    data = docket_value.get("data")
    attributes = data.get("attributes") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("id") != request["docket_id"]
        or data.get("type") != "dockets"
        or not isinstance(attributes, dict)
        or attributes.get("docketType") != "Rulemaking"
        or not isinstance(attributes.get("rin"), str)
        or _RIN.fullmatch(attributes["rin"]) is None
        or not all(
            isinstance(attributes.get(field), str) and attributes[field].strip()
            for field in ("agencyId", "title", "modifyDate")
        )
    ):
        raise ProvenanceError("Regulations.gov docket identity or fields are invalid")
    _parse_timestamp(attributes["modifyDate"], "Regulations.gov modifyDate")
    rin = attributes["rin"]
    docket_selected = {
        "agency_id": attributes["agencyId"],
        "docket_id": request["docket_id"],
        "docket_type": attributes["docketType"],
        "modify_date": attributes["modifyDate"],
        "rin": rin,
        "title": attributes["title"],
    }
    docket_record = _record(
        record_id=f"regulations-docket:{request['docket_id']}",
        kind="regulations_docket_current_metadata",
        selected=docket_selected,
        retrieval=docket_pair[0],
        source_raw=docket_pair[1],
        fact_fields=("docket_id", "rin", "title", "modify_date"),
    )
    records = [docket_record]
    federal_records: list[dict[str, Any]] = []
    for number in request["document_numbers"]:
        retrieval, raw = document_pairs[number]
        value = _json_object(raw, "Federal Register document")
        rins = value.get("regulation_id_numbers")
        if value.get("document_number") != number:
            raise ProvenanceError("Federal Register document identity is invalid")
        if not isinstance(rins, list) or rins != [rin]:
            raise ProvenanceError("Federal Register document RIN does not bind docket")
        required_fields = (
            "title",
            "type",
            "abstract",
            "publication_date",
            "action",
            "dates",
            "citation",
        )
        if not all(
            isinstance(value.get(field), str) and value[field].strip()
            for field in required_fields
        ):
            raise ProvenanceError("Federal Register semantic fields are incomplete")
        try:
            occurred = datetime.strptime(value["publication_date"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError as error:
            raise ProvenanceError(
                "Federal Register publication date is invalid"
            ) from error
        selected = {
            "abstract": value["abstract"],
            "action": value["action"],
            "citation": value["citation"],
            "document_number": number,
            "publication_date": value["publication_date"],
            "rin": rin,
            "title": value["title"],
            "type": value["type"],
        }
        record = _record(
            record_id=f"federal-register:{number}",
            kind=_classify_document(value),
            selected=selected,
            retrieval=retrieval,
            source_raw=raw,
            fact_fields=("document_number", "publication_date", "rin", "action"),
            occurred_at=occurred.isoformat().replace("+00:00", "Z"),
        )
        record["document_number"] = number
        federal_records.append(record)
    kinds = [record["kind"] for record in federal_records]
    occurred_times = [
        _parse_timestamp(record["occurred_at"], "Federal Register occurred_at")
        for record in federal_records
    ]
    if (
        kinds[0] != "federal_register_proposed_rule"
        or any(prior >= current for prior, current in pairwise(occurred_times))
        or any(kind != "federal_register_comment_extension" for kind in kinds[1:-1])
        or kinds[-1]
        not in {"federal_register_comment_extension", "federal_register_final_rule"}
    ):
        raise ProvenanceError("Federal Register rulemaking sequence is invalid")
    records.extend(federal_records)
    relations = [
        _relation(
            f"regulation:docket-has-document:{record['document_number']}",
            "docket_has_document",
            docket_record,
            record,
            ("docket_id", "rin"),
            ("document_number", "rin"),
        )
        for record in federal_records
    ]
    relations.extend(
        _relation(
            f"regulation:next:{prior['document_number']}:{current['document_number']}",
            "next_rulemaking_event",
            prior,
            current,
            ("document_number", "publication_date", "rin"),
            ("document_number", "publication_date", "rin"),
        )
        for prior, current in pairwise(federal_records)
    )
    manifest = {
        "schema_version": REGULATION_WORKFLOW_MANIFEST_SCHEMA,
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "semantic_facts_train_ready": False,
        "snapshot_semantics": "mixed_source_event_and_current_docket_metadata",
        "historical_snapshot_claims": False,
        "comment_content_collected": False,
        "generated_at": generated_at,
        "authorization": payload["authorization"],
        "fetch_inventory_sha256": fetch_inventory_sha256,
        "fetch_receipt": receipt,
        "n": len(records),
        "n_relations": len(relations),
        "records": records,
        "relations": relations,
    }
    audit_regulation_workflow_manifest(manifest)
    if (
        len(json.dumps(manifest, ensure_ascii=False).encode())
        > MAX_REGULATION_MANIFEST_BYTES
    ):
        raise ProvenanceError("regulation workflow manifest exceeds size limit")
    return manifest


def _validate_fact(record: dict[str, Any], fact: object) -> None:
    if not isinstance(fact, dict) or set(fact) != {
        "fact_id",
        "field",
        "value",
        "evidence_quote",
        "char_start",
        "char_end",
    }:
        raise ProvenanceError("regulation fact schema is invalid")
    start, end, quote = fact["char_start"], fact["char_end"], fact["evidence_quote"]
    text = record["text"]
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
        or start < 0
        or end != start + len(quote)
        or text[start:end] != quote
        or _json_object(text.encode(), "regulation record text").get(fact["field"])
        != fact["value"]
    ):
        raise ProvenanceError("regulation fact evidence binding is invalid")


def audit_regulation_workflow_manifest(payload: dict[str, Any]) -> None:
    """Recheck disabled flags, record bodies, and evidence-bound relations."""
    required = {
        "schema_version",
        "source_status",
        "data_stage",
        "hybrid_train_ready",
        "production_eligible",
        "generation_integration",
        "semantic_facts_train_ready",
        "snapshot_semantics",
        "historical_snapshot_claims",
        "comment_content_collected",
        "generated_at",
        "authorization",
        "fetch_inventory_sha256",
        "fetch_receipt",
        "n",
        "n_relations",
        "records",
        "relations",
    }
    if set(payload) - {
        "attestation",
        "local_probe_trust_isolation",
    } != required or not (
        payload.get("schema_version") == REGULATION_WORKFLOW_MANIFEST_SCHEMA
        and payload.get("source_status") == "public_api_export"
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("snapshot_semantics")
        == "mixed_source_event_and_current_docket_metadata"
        and payload.get("historical_snapshot_claims") is False
        and payload.get("comment_content_collected") is False
    ):
        raise ProvenanceError(
            "regulation manifest schema or disabled flags are invalid"
        )
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "retrievals",
    }:
        raise ProvenanceError("regulation fetch receipt lineage is invalid")
    raw_retrievals = receipt.get("retrievals")
    if not isinstance(raw_retrievals, list) or not raw_retrievals:
        raise ProvenanceError("regulation fetch receipt lineage is invalid")
    retrievals: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_retrieval in raw_retrievals:
        retrieval = _validate_retrieval_metadata(raw_retrieval)
        key = (str(retrieval["kind"]), str(retrieval["requested_url"]))
        if (
            key in retrievals
            or _SHA256.fullmatch(str(retrieval.get("sha256") or "")) is None
        ):
            raise ProvenanceError("regulation fetch receipt lineage is invalid")
        retrievals[key] = retrieval
    records = payload.get("records")
    relations = payload.get("relations")
    if (
        not isinstance(records, list)
        or not records
        or len(records) != payload.get("n")
        or not isinstance(relations, list)
        or len(relations) != payload.get("n_relations")
    ):
        raise ProvenanceError("regulation manifest counts are invalid")
    record_map: dict[str, dict[str, Any]] = {}
    docket_records: list[dict[str, Any]] = []
    federal_records: list[dict[str, Any]] = []
    observed_rins: set[str] = set()
    used_retrievals: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("record_id"), str):
            raise ProvenanceError("regulation record schema is invalid")
        record_id = record["record_id"]
        if record_id in record_map:
            raise ProvenanceError("regulation record identity is duplicated")
        record_map[record_id] = record
        text = record.get("text")
        if (
            not isinstance(text, str)
            or not text
            or record.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest()
            or record.get("source_origin") != "real_public"
            or _SHA256.fullmatch(str(record.get("source_sha256") or "")) is None
            or record.get("source_record_sha256")
            != hashlib.sha256(text.encode()).hexdigest()
            or record.get("provenance_id") != f"sha256:{record.get('source_sha256')}"
            or record.get("privacy_review", {}).get("comments_collected") is not False
        ):
            raise ProvenanceError("regulation record body or provenance is invalid")
        is_docket = record.get("kind") == "regulations_docket_current_metadata"
        if (
            is_docket
            and (
                record.get("temporal_semantics") != "current_snapshot_observation"
                or "occurred_at" in record
            )
        ) or (
            not is_docket
            and (
                record.get("temporal_semantics") != "source_event_time"
                or not isinstance(record.get("occurred_at"), str)
            )
        ):
            raise ProvenanceError("regulation record temporal semantics are invalid")
        parsed = _json_object(text.encode(), "regulation record text")
        rin = parsed.get("rin")
        if not isinstance(rin, str) or _RIN.fullmatch(rin) is None:
            raise ProvenanceError("regulation record RIN is invalid")
        observed_rins.add(rin)
        if is_docket:
            docket_id = parsed.get("docket_id")
            if (
                set(parsed)
                != {
                    "agency_id",
                    "docket_id",
                    "docket_type",
                    "modify_date",
                    "rin",
                    "title",
                }
                or not isinstance(docket_id, str)
                or record_id != f"regulations-docket:{docket_id}"
                or parsed.get("docket_type") != "Rulemaking"
            ):
                raise ProvenanceError("regulation docket record identity is invalid")
            expected_url = f"{REGULATIONS_DOCKET_URL}/{docket_id}"
            retrieval_key = ("regulations_docket_metadata", expected_url)
            docket_records.append(record)
        else:
            document_number = parsed.get("document_number")
            if (
                set(parsed)
                != {
                    "abstract",
                    "action",
                    "citation",
                    "document_number",
                    "publication_date",
                    "rin",
                    "title",
                    "type",
                }
                or not isinstance(document_number, str)
                or record_id != f"federal-register:{document_number}"
                or record.get("document_number") != document_number
                or record.get("kind") != _classify_document(parsed)
            ):
                raise ProvenanceError(
                    "Federal Register record identity or classification is invalid"
                )
            expected_url = f"{FEDERAL_REGISTER_DOCUMENT_URL}/{document_number}.json"
            retrieval_key = ("federal_register_document", expected_url)
            try:
                expected_time = datetime.strptime(
                    str(parsed.get("publication_date") or ""), "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc)
            except ValueError as error:
                raise ProvenanceError(
                    "Federal Register publication date is invalid"
                ) from error
            if (
                _parse_timestamp(record["occurred_at"], "Federal Register occurred_at")
                != expected_time
            ):
                raise ProvenanceError(
                    "Federal Register publication time binding is invalid"
                )
            federal_records.append(record)
        record_retrieval = retrievals.get(retrieval_key)
        if (
            record_retrieval is None
            or record.get("source_url") != expected_url
            or record.get("retrieval_url") != expected_url
            or record.get("source_sha256") != record_retrieval.get("sha256")
            or record.get("observed_at") != record_retrieval.get("observed_at")
        ):
            raise ProvenanceError("regulation fetch receipt lineage is invalid")
        used_retrievals.add(retrieval_key)
        for fact in record.get("facts", []):
            _validate_fact(record, fact)
    if used_retrievals != set(retrievals):
        raise ProvenanceError("regulation fetch receipt lineage is invalid")
    if len(docket_records) != 1 or len(federal_records) < 2 or len(observed_rins) != 1:
        raise ProvenanceError("regulation record family or RIN coverage is invalid")
    federal_records.sort(key=lambda item: item["occurred_at"])
    kinds = [record["kind"] for record in federal_records]
    if (
        kinds[0] != "federal_register_proposed_rule"
        or any(kind != "federal_register_comment_extension" for kind in kinds[1:-1])
        or kinds[-1]
        not in {"federal_register_comment_extension", "federal_register_final_rule"}
    ):
        raise ProvenanceError(
            "Federal Register record classification sequence is invalid"
        )
    docket_record = docket_records[0]
    expected_relations = {
        **{
            f"regulation:docket-has-document:{record['document_number']}": (
                "docket_has_document",
                docket_record["record_id"],
                record["record_id"],
            )
            for record in federal_records
        },
        **{
            f"regulation:next:{prior['document_number']}:{current['document_number']}": (
                "next_rulemaking_event",
                prior["record_id"],
                current["record_id"],
            )
            for prior, current in pairwise(federal_records)
        },
    }
    if len(relations) != len(expected_relations):
        raise ProvenanceError("regulation relation coverage is invalid")
    relation_ids: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            raise ProvenanceError("regulation relation is invalid")
        relation_id = relation.get("relation_id")
        source = record_map.get(str(relation.get("source_record_id") or ""))
        target = record_map.get(str(relation.get("target_record_id") or ""))
        expected_relation = expected_relations.get(str(relation_id or ""))
        if (
            not isinstance(relation_id, str)
            or relation_id in relation_ids
            or source is None
            or target is None
            or expected_relation
            != (
                relation.get("kind"),
                relation.get("source_record_id"),
                relation.get("target_record_id"),
            )
        ):
            raise ProvenanceError("regulation relation identity is invalid")
        relation_ids.add(relation_id)
        evidence = relation.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != 2:
            raise ProvenanceError("regulation relation evidence is invalid")
        for item, record in zip(evidence, (source, target), strict=True):
            if (
                not isinstance(item, dict)
                or item.get("record_id") != record["record_id"]
            ):
                raise ProvenanceError(
                    "regulation relation evidence endpoint is invalid"
                )
            fact_ids = item.get("fact_ids")
            if not isinstance(fact_ids, list):
                raise ProvenanceError("regulation relation evidence facts are invalid")
            expected = _evidence(record, tuple(fact_ids))
            if item != expected:
                raise ProvenanceError("regulation relation evidence binding is invalid")
    try:
        validate_sanitized_public_payload(payload)
    except ValueError as error:
        raise ProvenanceError(
            "regulation manifest failed the public scanner"
        ) from error


def load_regulation_workflow_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Load and semantically audit a source-attested regulation inventory."""
    try:
        raw = _read_regular_file(path, MAX_REGULATION_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read regulation workflow manifest: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ProvenanceError("regulation workflow manifest must be an object")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("regulation workflow manifest has no valid attestation")
    audit_regulation_workflow_manifest(payload)
    return payload


REGULATION_RULEMAKING_TASK_SCHEMA = "longworld.regulation-rulemaking-task.v2"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _task_source_manifest(task: dict[str, Any]) -> dict[str, Any]:
    if not (
        task.get("schema_version") == REGULATION_RULEMAKING_TASK_SCHEMA
        and task.get("data_stage") == "candidate_task"
        and task.get("train_ready") is False
        and task.get("production_eligible") is False
        and task.get("promotion_eligible") is False
        and task.get("complete_world") is False
        and task.get("promoted") is False
        and task.get("generation_integration") == "disabled"
        and task.get("source_attestation_verified") is False
    ):
        raise ProvenanceError("regulation rulemaking task contract is invalid")
    manifest = task.get("source_manifest")
    if not isinstance(manifest, dict):
        raise ProvenanceError("regulation task source manifest is missing")
    audit_regulation_workflow_manifest(manifest)
    if task.get("source_manifest_sha256") != _canonical_sha256(manifest):
        raise ProvenanceError("regulation task source manifest binding is invalid")
    if task.get("source_receipt") != manifest.get("fetch_receipt"):
        raise ProvenanceError("regulation task source receipt binding is invalid")
    records = manifest.get("records")
    bindings = task.get("source_bindings")
    if not isinstance(records, list) or not isinstance(bindings, dict):
        raise ProvenanceError("regulation task source bindings are invalid")
    record_map = {
        str(record.get("record_id") or ""): record
        for record in records
        if isinstance(record, dict)
    }
    if len(record_map) != len(records) or set(bindings) != set(record_map):
        raise ProvenanceError("regulation task source bindings are invalid")
    for record_id, record in record_map.items():
        expected = {
            "text_sha256": record.get("text_sha256"),
            "source_sha256": record.get("source_sha256"),
            "source_url": record.get("source_url"),
            "observed_at": record.get("observed_at"),
        }
        if bindings.get(record_id) != expected:
            raise ProvenanceError("regulation task source bindings are invalid")
    return manifest


def _task_source_attestation_verified(
    task: dict[str, Any], source_attestation_key: bytes | None
) -> bool:
    if source_attestation_key is None:
        return False
    try:
        manifest = _task_source_manifest(task)
    except ProvenanceError:
        return False
    attestation = manifest.get("attestation")
    if isinstance(attestation, dict) and (
        attestation.get("scheme") == ATTESTATION_V2_SCHEME
    ):
        return verify_attestation_identity(
            manifest,
            source_attestation_key,
            purpose="source_manifest",
            role="source",
            key_id=str(attestation.get("key_id") or ""),
            environment=str(attestation.get("environment") or ""),
        )
    return verify_attestation(
        manifest, source_attestation_key, purpose="source_manifest"
    )


def _record_evidence_item(
    evidence_id: str, expected_kind: str, record: dict[str, Any]
) -> dict[str, Any]:
    text = str(record["text"])
    return {
        "evidence_id": evidence_id,
        "kind": "source_record_body",
        "expected_record_kind": expected_kind,
        "record_id": record["record_id"],
        "surface_text": text,
        "char_start": 0,
        "char_end": len(text),
        "text_sha256": record["text_sha256"],
    }


def _relation_surface(relations: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for relation in sorted(relations, key=lambda item: item["relation_id"]):
        lines.append(
            f"{relation['kind']}: {relation['source_record_id']} -> "
            f"{relation['target_record_id']}"
        )
        for evidence in relation["evidence"]:
            lines.extend(str(quote) for quote in evidence["evidence_quotes"])
    return "\n".join(lines)


def _relation_evidence_items(
    relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": f"source_relation:{relation['relation_id']}",
            "kind": "source_relation",
            "relation_id": relation["relation_id"],
            "surface_text": _relation_surface([relation]),
        }
        for relation in sorted(relations, key=lambda item: item["relation_id"])
    ]


def _counterfactual_final_text(parent_text: str) -> str:
    parsed = _json_object(parent_text.encode(), "regulation final-rule task body")
    if _classify_document(parsed) != "federal_register_final_rule":
        raise ProvenanceError("regulation counterfactual parent is not a final rule")
    parsed["type"] = "Proposed Rule"
    parsed["action"] = "Notice of proposed rulemaking."
    parsed["abstract"] = (
        "The Commission proposes the Non-Compete Clause Rule and seeks comment."
    )
    return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)


def _counterfactual_record(
    task: dict[str, Any], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    twin = task.get("counterfactual_twin")
    if not isinstance(twin, dict):
        raise ProvenanceError("regulation counterfactual twin is missing")
    parent_id = str(twin.get("record_id") or "")
    parent = records.get(parent_id)
    expected_text = (
        _counterfactual_final_text(str(parent.get("text") or ""))
        if parent is not None
        else ""
    )
    text = twin.get("text")
    if (
        parent is None
        or parent.get("kind") != "federal_register_final_rule"
        or twin.get("source_origin") != "synthetic_counterfactual"
        or twin.get("provenance_operation") != "replace_final_rule_with_proposal"
        or twin.get("parent_text_sha256") != parent.get("text_sha256")
        or not isinstance(text, str)
        or text != expected_text
        or twin.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest()
        or _classify_document(_json_object(text.encode(), "regulation CF body"))
        != "federal_register_proposed_rule"
    ):
        raise ProvenanceError("regulation counterfactual replacement is invalid")
    replacement = dict(parent)
    replacement.update(
        text=text,
        text_sha256=twin["text_sha256"],
        source_record_sha256=twin["text_sha256"],
        kind="federal_register_proposed_rule",
        source_origin="synthetic_counterfactual",
    )
    return replacement


def _task_evidence(
    task: dict[str, Any], manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    records = {
        str(record["record_id"]): record for record in manifest.get("records", [])
    }
    raw_items = task.get("evidence_items")
    essentials = task.get("essential_evidence_ids")
    if not isinstance(raw_items, list) or not isinstance(essentials, list):
        raise ProvenanceError("regulation task evidence contract is invalid")
    items = {
        str(item.get("evidence_id") or ""): item
        for item in raw_items
        if isinstance(item, dict)
    }
    if (
        len(items) != len(raw_items)
        or any(not isinstance(item, str) for item in essentials)
        or len(essentials) != len(set(essentials))
        or set(essentials) != set(items)
    ):
        raise ProvenanceError("regulation task essential evidence is invalid")
    return records, items, essentials


def _validate_record_item(
    item: dict[str, Any], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    record = records.get(str(item.get("record_id") or ""))
    start, end = item.get("char_start"), item.get("char_end")
    surface = item.get("surface_text")
    if (
        record is None
        or item.get("kind") != "source_record_body"
        or item.get("expected_record_kind") != record.get("kind")
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(surface, str)
        or start != 0
        or end != len(record["text"])
        or record["text"][start:end] != surface
        or item.get("text_sha256") != record.get("text_sha256")
    ):
        raise ProvenanceError("regulation task body evidence binding is invalid")
    return record


def _validate_relation_item(
    item: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    relations = manifest.get("relations")
    if not isinstance(relations, list) or not all(
        isinstance(relation, dict) for relation in relations
    ):
        raise ProvenanceError("regulation task source relations are invalid")
    matches = [
        relation
        for relation in relations
        if relation.get("relation_id") == item.get("relation_id")
    ]
    if (
        item.get("kind") != "source_relation"
        or len(matches) != 1
        or item.get("surface_text") != _relation_surface(matches)
    ):
        raise ProvenanceError("regulation task relation evidence binding is invalid")
    return matches[0]


def _unknown_rulemaking_state() -> dict[str, Any]:
    return {
        "docket_id": "",
        "rin": "",
        "nprm_document_number": "",
        "extension_document_number": "",
        "final_rule_document_number": "",
        "sequence_valid": False,
        "status": "unknown",
    }


def derive_regulation_rulemaking_state(
    task: dict[str, Any],
    *,
    evidence_ids: list[str] | None = None,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay byte-bound bodies and relations into one rulemaking state."""
    manifest = _task_source_manifest(task)
    records, items, essentials = _task_evidence(task, manifest)
    selected = set(essentials if evidence_ids is None else evidence_ids)
    if not selected.issubset(items):
        raise ProvenanceError("regulation task evidence selection is invalid")
    event_records: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    for evidence_id in selected:
        item = items[evidence_id]
        if item.get("kind") == "source_record_body":
            record = _validate_record_item(item, records)
            event_records[evidence_id] = record
        elif item.get("kind") == "source_relation":
            relations.append(_validate_relation_item(item, manifest))
        else:
            raise ProvenanceError("regulation task evidence kind is invalid")
    if counterfactual:
        replacement = _counterfactual_record(task, records)
        if "final_rule_body" in event_records:
            event_records["final_rule_body"] = replacement
    if (
        set(event_records)
        != {
            "docket_body",
            "nprm_body",
            "extension_body",
            "final_rule_body",
        }
        or not relations
    ):
        return _unknown_rulemaking_state()
    docket = _json_object(
        str(event_records["docket_body"]["text"]).encode(), "regulation docket body"
    )
    classified: dict[str, dict[str, Any]] = {}
    for evidence_id in ("nprm_body", "extension_body", "final_rule_body"):
        record = event_records[evidence_id]
        parsed = _json_object(str(record["text"]).encode(), "regulation event body")
        classified[_classify_document(parsed)] = parsed
    required_kinds = {
        "federal_register_proposed_rule",
        "federal_register_comment_extension",
        "federal_register_final_rule",
    }
    if set(classified) != required_kinds:
        return _unknown_rulemaking_state()
    nprm = classified["federal_register_proposed_rule"]
    extension = classified["federal_register_comment_extension"]
    final_rule = classified["federal_register_final_rule"]
    rin = str(docket.get("rin") or "")
    docket_id = str(docket.get("docket_id") or "")
    if not (
        _DOCKET_ID.fullmatch(docket_id)
        and _RIN.fullmatch(rin)
        and all(event.get("rin") == rin for event in (nprm, extension, final_rule))
    ):
        return _unknown_rulemaking_state()
    docket_record_id = event_records["docket_body"]["record_id"]
    event_record_ids = {
        str(event["document_number"]): event_records[evidence_id]["record_id"]
        for evidence_id, event in (
            ("nprm_body", nprm),
            ("extension_body", extension),
            ("final_rule_body", final_rule),
        )
    }
    relation_edges = {
        (
            str(relation.get("kind") or ""),
            str(relation.get("source_record_id") or ""),
            str(relation.get("target_record_id") or ""),
        )
        for relation in relations
    }
    nprm_id = str(nprm["document_number"])
    extension_id = str(extension["document_number"])
    final_id = str(final_rule["document_number"])
    required_edges = {
        *(
            ("docket_has_document", docket_record_id, record_id)
            for record_id in event_record_ids.values()
        ),
        (
            "next_rulemaking_event",
            event_record_ids[nprm_id],
            event_record_ids[extension_id],
        ),
        (
            "next_rulemaking_event",
            event_record_ids[extension_id],
            event_record_ids[final_id],
        ),
    }
    if not required_edges.issubset(relation_edges):
        return _unknown_rulemaking_state()
    return {
        "docket_id": docket_id,
        "rin": rin,
        "nprm_document_number": nprm_id,
        "extension_document_number": extension_id,
        "final_rule_document_number": final_id,
        "sequence_valid": True,
        "status": "final_rule",
    }


def _rulemaking_answer(state: dict[str, Any]) -> str:
    if state.get("sequence_valid") is not True or state.get("status") != "final_rule":
        return "unknown"
    return (
        f"{state['docket_id']} | RIN {state['rin']} | "
        f"{state['nprm_document_number']} -> "
        f"{state['extension_document_number']} -> "
        f"{state['final_rule_document_number']} | final_rule"
    )


def replay_regulation_rulemaking_task(
    task: dict[str, Any],
    *,
    evidence_ids: list[str] | None = None,
    counterfactual: bool = False,
) -> str:
    """Strictly execute the rulemaking state and answer program."""
    return _rulemaking_answer(
        derive_regulation_rulemaking_state(
            task, evidence_ids=evidence_ids, counterfactual=counterfactual
        )
    )


def build_regulation_rulemaking_task(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compile one non-promoted executable task from an audited source manifest."""
    audit_regulation_workflow_manifest(manifest)
    by_kind = {str(record["kind"]): record for record in manifest.get("records", [])}
    required = {
        "regulations_docket_current_metadata": "docket_body",
        "federal_register_proposed_rule": "nprm_body",
        "federal_register_comment_extension": "extension_body",
        "federal_register_final_rule": "final_rule_body",
    }
    if not set(required).issubset(by_kind):
        raise ProvenanceError(
            "regulation source manifest lacks the rulemaking sequence"
        )
    source_manifest = deepcopy(manifest)
    source_records = {
        str(record["record_id"]): record for record in source_manifest["records"]
    }
    evidence_items = [
        _record_evidence_item(
            evidence_id,
            kind,
            source_records[str(by_kind[kind]["record_id"])],
        )
        for kind, evidence_id in required.items()
    ]
    evidence_items.extend(_relation_evidence_items(source_manifest["relations"]))
    final_record = source_records[
        str(by_kind["federal_register_final_rule"]["record_id"])
    ]
    cf_text = _counterfactual_final_text(str(final_record["text"]))
    task: dict[str, Any] = {
        "schema_version": REGULATION_RULEMAKING_TASK_SCHEMA,
        "data_stage": "candidate_task",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "source_attestation_verified": False,
        "query_type": "rulemaking_sequence",
        "answer_program_id": "regulation.rulemaking_sequence.v1",
        "question": (
            "Did this docket progress from an NPRM through a public-comment "
            "extension to a final rule? Return the docket ID, RIN, and exact "
            "Federal Register document-number sequence."
        ),
        "answer": "",
        "cf_answer": "",
        "essential_evidence_ids": [str(item["evidence_id"]) for item in evidence_items],
        "evidence_items": evidence_items,
        "source_manifest": source_manifest,
        "source_manifest_sha256": _canonical_sha256(source_manifest),
        "source_receipt": deepcopy(source_manifest["fetch_receipt"]),
        "source_bindings": {
            record_id: {
                "text_sha256": record["text_sha256"],
                "source_sha256": record["source_sha256"],
                "source_url": record["source_url"],
                "observed_at": record["observed_at"],
            }
            for record_id, record in source_records.items()
        },
        "counterfactual_twin": {
            "record_id": final_record["record_id"],
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_final_rule_with_proposal",
            "parent_text_sha256": final_record["text_sha256"],
            "text": cf_text,
            "text_sha256": hashlib.sha256(cf_text.encode()).hexdigest(),
        },
    }
    task["answer"] = replay_regulation_rulemaking_task(task)
    task["cf_answer"] = replay_regulation_rulemaking_task(task, counterfactual=True)
    return task


def _semantic_body_corruption(task: dict[str, Any]) -> dict[str, Any]:
    corrupted = deepcopy(task)
    manifest = corrupted["source_manifest"]
    record = next(
        item
        for item in manifest["records"]
        if item["kind"] == "federal_register_comment_extension"
    )
    parsed = _json_object(str(record["text"]).encode(), "regulation extension body")
    parsed.update(
        title="Unrelated public notice",
        action="Notice.",
        abstract="Unrelated public notice.",
    )
    text = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
    digest = hashlib.sha256(text.encode()).hexdigest()
    record.update(text=text, text_sha256=digest, source_record_sha256=digest)
    for fact in record["facts"]:
        fact["value"] = parsed[fact["field"]]
        encoded = json.dumps(fact["value"], ensure_ascii=False)
        pattern = re.compile(
            rf'^  "{re.escape(fact["field"])}"\s*:\s*{re.escape(encoded)},?$',
            re.MULTILINE,
        )
        match = pattern.search(text)
        if match is None:
            raise ProvenanceError("regulation corruption fixture fact is missing")
        fact.update(
            evidence_quote=match.group(0),
            char_start=match.start(),
            char_end=match.end(),
        )
    facts = {fact["fact_id"]: fact for fact in record["facts"]}
    for relation in manifest["relations"]:
        for evidence in relation["evidence"]:
            if evidence["record_id"] != record["record_id"]:
                continue
            selected = [facts[fact_id] for fact_id in evidence["fact_ids"]]
            evidence["evidence_quotes"] = [fact["evidence_quote"] for fact in selected]
            evidence["char_spans"] = [
                [fact["char_start"], fact["char_end"]] for fact in selected
            ]
    relations = {
        relation["relation_id"]: relation for relation in manifest["relations"]
    }
    for item in corrupted["evidence_items"]:
        if item.get("kind") != "source_relation":
            continue
        relation = relations[str(item["relation_id"])]
        item["surface_text"] = _relation_surface([relation])
    corrupted["source_bindings"][record["record_id"]]["text_sha256"] = digest
    item = next(
        evidence
        for evidence in corrupted["evidence_items"]
        if evidence["evidence_id"] == "extension_body"
    )
    item.update(surface_text=text, char_end=len(text), text_sha256=digest)
    corrupted["source_manifest_sha256"] = _canonical_sha256(manifest)
    return corrupted


def audit_regulation_rulemaking_task(
    task: dict[str, Any], *, source_attestation_key: bytes | None = None
) -> dict[str, bool]:
    """Run strict, CF, remove-one, surface, corruption, and receipt gates."""
    essentials = list(task.get("essential_evidence_ids") or [])
    answer = str(task.get("answer") or "")
    cf_answer = str(task.get("cf_answer") or "")
    strict = replay_regulation_rulemaking_task(task) == answer
    cf_replay = replay_regulation_rulemaking_task(task, counterfactual=True)
    singles = [
        replay_regulation_rulemaking_task(task, evidence_ids=[evidence_id])
        for evidence_id in essentials
    ]
    removals = [
        replay_regulation_rulemaking_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        for removed in essentials
    ]
    items = task.get("evidence_items") or []
    state = derive_regulation_rulemaking_state(task)
    answer_inputs = tuple(
        str(state[field])
        for field in (
            "docket_id",
            "rin",
            "nprm_document_number",
            "extension_document_number",
            "final_rule_document_number",
        )
    )
    surface_free = bool(items) and all(
        answer not in str(item.get("surface_text") or "")
        and not all(
            value in json.dumps(item, ensure_ascii=False) for value in answer_inputs
        )
        for item in items
    )
    try:
        corrupted_fails = (
            replay_regulation_rulemaking_task(_semantic_body_corruption(task)) != answer
        )
    except ProvenanceError:
        corrupted_fails = True
    receipt_valid = True
    try:
        _task_source_manifest(task)
    except ProvenanceError:
        receipt_valid = False
    return {
        "strict_replay_sufficient": strict,
        "counterfactual_replay_sufficient": cf_replay == cf_answer,
        "counterfactual_changes_answer": cf_answer != answer,
        "remove_one_fails": bool(removals)
        and all(value != answer for value in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(value != answer for value in singles),
        "essential_surface_gold_free": surface_free,
        "essential_text_grounded": strict,
        "body_corruption_fails": corrupted_fails,
        "receipt_binding_valid": receipt_valid,
        "source_attestation_verified": _task_source_attestation_verified(
            task, source_attestation_key
        ),
    }
