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
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.attestation import attestation_key_from_env, verify_attestation
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
