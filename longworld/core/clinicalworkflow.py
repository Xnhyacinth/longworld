"""Fail-closed ClinicalTrials.gov and openFDA source-inventory contract.

Only privacy-minimized projections of current API responses are retained.  The
raw response digest remains in the retrieval receipt, but this module never
represents the observation as a historical snapshot or a training world.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from longworld.core.attestation import (
    ATTESTATION_V2_SCHEME,
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    attestation_key_from_env,
    verify_attestation,
    verify_attestation_identity,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
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

CLINICAL_FETCH_REQUEST_SCHEMA = "longworld.clinical-fetch-request.v1"
CLINICAL_FETCH_INVENTORY_SCHEMA = "longworld.clinical-fetch-inventory.v1"
CLINICAL_WORKFLOW_MANIFEST_SCHEMA = "longworld.clinical-workflow-manifest.v1"
CLINICAL_TRIAL_API_ROOT = "https://clinicaltrials.gov/api/v2/studies"
FDA_APPLICATION_API_URL = "https://api.fda.gov/drug/drugsfda.json"
FDA_LABEL_API_URL = "https://api.fda.gov/drug/label.json"
CLINICAL_TERMS_URL = "https://clinicaltrials.gov/data-about-studies/learn-about-api"
CLINICAL_ATTRIBUTION = "ClinicalTrials.gov"
CLINICAL_LICENSE = (
    "U.S. Government public registry data; ClinicalTrials.gov terms apply."
)
OPENFDA_TERMS_URL = "https://open.fda.gov/apis/"
OPENFDA_LICENSE_URL = "https://open.fda.gov/license/"
OPENFDA_ATTRIBUTION = "openFDA"
OPENFDA_LICENSE = "openFDA public data; openFDA license and attribution apply."
MAX_CLINICAL_MANIFEST_BYTES = 16_000_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NCT_ID = re.compile(r"^NCT\d{8}$")
_NCT_MENTION = re.compile(r"\bNCT\d{8}\b")
_FDA_APPLICATION = re.compile(r"^(?:NDA|BLA)\d{6}$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE = re.compile(r"^\d{8}$")
_ALLOWED_ACTIONS = {
    "fetch_clinical_trial",
    "fetch_fda_application",
    "fetch_fda_label",
}
_REQUEST_FIELDS = {
    "schema_version",
    "user_agent",
    "authorization",
    "nct_id",
    "fda_application_number",
    "max_retries",
}
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
    "generated_at",
    "request_file",
    "request_sha256",
    "authorization",
    "fetch_receipt",
    "n_retrievals",
}


def _strings(value: object, label: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ProvenanceError(f"clinical fetch {label} is invalid")
    result = [str(item).strip() for item in value]
    if any(not item or len(item) > 256 for item in result) or len(result) != len(
        set(result)
    ):
        raise ProvenanceError(f"clinical fetch {label} is invalid")
    return result


def validate_clinical_fetch_request(value: object) -> dict[str, Any]:
    """Validate the exact credential-free public fetch request."""
    if (
        not isinstance(value, dict)
        or set(value) != _REQUEST_FIELDS
        or value.get("schema_version") != CLINICAL_FETCH_REQUEST_SCHEMA
    ):
        raise ProvenanceError("clinical fetch request schema is invalid")
    user_agent = str(value.get("user_agent") or "").strip()
    if len(user_agent) > 256 or _CONTACT_USER_AGENT.fullmatch(user_agent) is None:
        raise ProvenanceError("clinical fetch requires a contact User-Agent")
    authorization = value.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("clinical fetch authorization is invalid")
    normalized_authorization: dict[str, Any] = {
        field: str(authorization.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    }
    if any(not item for item in normalized_authorization.values()):
        raise ProvenanceError("clinical fetch authorization is invalid")
    _parse_timestamp(
        normalized_authorization["reviewed_at"], "authorization.reviewed_at"
    )
    actions = sorted(
        _strings(
            authorization.get("allowed_actions"),
            "authorization actions",
            len(_ALLOWED_ACTIONS),
        )
    )
    if set(actions) != _ALLOWED_ACTIONS:
        raise ProvenanceError("clinical fetch authorization actions are invalid")
    normalized_authorization["allowed_actions"] = actions
    nct_id = str(value.get("nct_id") or "")
    if _NCT_ID.fullmatch(nct_id) is None:
        raise ProvenanceError("clinical fetch NCT identity is invalid")
    application_number = str(value.get("fda_application_number") or "")
    if _FDA_APPLICATION.fullmatch(application_number) is None:
        raise ProvenanceError("clinical fetch FDA application identity is invalid")
    retries = value.get("max_retries")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("clinical fetch max_retries is invalid")
    return {
        "user_agent": user_agent,
        "authorization": normalized_authorization,
        "nct_id": nct_id,
        "fda_application_number": application_number,
        "max_retries": retries,
    }


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceError(f"{label} must be an object")
    return value


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProvenanceError(f"{label} is missing")
    return value.strip()


def project_clinical_trial_response(payload: object, nct_id: str) -> dict[str, Any]:
    """Select aggregate registry facts while excluding contacts and participant data."""
    root = _object(payload, "ClinicalTrials.gov response")
    protocol = _object(root.get("protocolSection"), "trial protocolSection")
    identification = _object(
        protocol.get("identificationModule"), "trial identificationModule"
    )
    if identification.get("nctId") != nct_id:
        raise ProvenanceError("ClinicalTrials.gov response identity mismatch")
    organization = _object(identification.get("organization"), "trial organization")
    status = _object(protocol.get("statusModule"), "trial statusModule")
    completion = _object(status.get("completionDateStruct"), "trial completion date")
    last_update = _object(
        status.get("lastUpdatePostDateStruct"), "trial last update date"
    )
    outcomes = _object(protocol.get("outcomesModule"), "trial outcomesModule")
    primary = outcomes.get("primaryOutcomes")
    if not isinstance(primary, list) or not primary or not isinstance(primary[0], dict):
        raise ProvenanceError("trial primary outcome is missing")
    first_outcome = primary[0]
    completion_date = _nonempty(completion.get("date"), "trial completion date")
    last_update_post_date = _nonempty(last_update.get("date"), "trial last update date")
    projected = {
        "nct_id": nct_id,
        "organization": _nonempty(organization.get("fullName"), "trial organization"),
        "brief_title": _nonempty(identification.get("briefTitle"), "trial brief title"),
        "official_title": _nonempty(
            identification.get("officialTitle"), "trial official title"
        ),
        "overall_status": _nonempty(status.get("overallStatus"), "trial status"),
        "status_verified_date": _nonempty(
            status.get("statusVerifiedDate"), "trial verified date"
        ),
        "completion_date": completion_date,
        "last_update_post_date": last_update_post_date,
        "primary_outcome_measure": _nonempty(
            first_outcome.get("measure"), "trial primary outcome measure"
        ),
        "primary_outcome_time_frame": _nonempty(
            first_outcome.get("timeFrame"), "trial primary outcome time frame"
        ),
        "has_results": root.get("hasResults") is True,
    }
    if (
        _DATE.fullmatch(completion_date) is None
        or _DATE.fullmatch(last_update_post_date) is None
    ):
        raise ProvenanceError("trial semantic date is invalid")
    return _sanitize_projection(projected)


def _single_fda_result(payload: object, label: str) -> dict[str, Any]:
    root = _object(payload, f"{label} response")
    meta = _object(root.get("meta"), f"{label} meta")
    results_meta = _object(meta.get("results"), f"{label} results meta")
    results = root.get("results")
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
        or results_meta.get("limit") != 1
        or results_meta.get("total") != 1
    ):
        raise ProvenanceError(f"{label} response identity is not unique")
    return results[0]


def project_fda_application_response(
    payload: object, application_number: str
) -> dict[str, Any]:
    result = _single_fda_result(payload, "openFDA application")
    if result.get("application_number") != application_number:
        raise ProvenanceError("openFDA application response identity mismatch")
    submissions = result.get("submissions")
    originals = [
        item
        for item in submissions or []
        if isinstance(item, dict)
        and item.get("submission_type") == "ORIG"
        and item.get("submission_number") == "1"
    ]
    if not isinstance(submissions, list) or len(originals) != 1:
        raise ProvenanceError("openFDA original application submission is not unique")
    original = originals[0]
    approval_date = _nonempty(
        original.get("submission_status_date"), "FDA original approval date"
    )
    if (
        original.get("submission_status") != "AP"
        or _COMPACT_DATE.fullmatch(approval_date) is None
    ):
        raise ProvenanceError("openFDA original application is not approved")
    products = result.get("products")
    product_names = sorted(
        {
            str(item.get("brand_name") or "").strip()
            for item in products or []
            if isinstance(item, dict) and str(item.get("brand_name") or "").strip()
        }
    )
    if not product_names:
        raise ProvenanceError("openFDA application products are missing")
    return _sanitize_projection(
        {
            "application_number": application_number,
            "sponsor_name": _nonempty(result.get("sponsor_name"), "FDA sponsor"),
            "original_submission_status": "AP",
            "original_approval_date": (
                f"{approval_date[:4]}-{approval_date[4:6]}-{approval_date[6:]}"
            ),
            "original_review_priority": _nonempty(
                original.get("review_priority"), "FDA review priority"
            ),
            "product_names": product_names,
        }
    )


def project_fda_label_response(
    payload: object, application_number: str
) -> dict[str, Any]:
    result = _single_fda_result(payload, "openFDA label")
    openfda = _object(result.get("openfda"), "openFDA label metadata")
    applications = openfda.get("application_number")
    if applications != [application_number]:
        raise ProvenanceError("openFDA label response identity mismatch")
    indications = result.get("indications_and_usage")
    clinical_studies = result.get("clinical_studies")
    if (
        not isinstance(indications, list)
        or not indications
        or not all(isinstance(item, str) and item.strip() for item in indications)
        or not isinstance(clinical_studies, list)
        or not clinical_studies
        or not all(isinstance(item, str) and item.strip() for item in clinical_studies)
    ):
        raise ProvenanceError("openFDA label semantic sections are incomplete")
    study_text = "\n".join(clinical_studies)
    referenced_nct_ids = sorted(set(_NCT_MENTION.findall(study_text)))
    effective_time = _nonempty(result.get("effective_time"), "FDA label effective time")
    if _COMPACT_DATE.fullmatch(effective_time) is None:
        raise ProvenanceError("FDA label effective time is invalid")
    return _sanitize_projection(
        {
            "application_number": application_number,
            "label_id": _nonempty(result.get("id"), "FDA label id"),
            "label_set_id": _nonempty(result.get("set_id"), "FDA label set id"),
            "effective_date": (
                f"{effective_time[:4]}-{effective_time[4:6]}-{effective_time[6:]}"
            ),
            "version": _nonempty(str(result.get("version") or ""), "FDA label version"),
            "indications_and_usage": "\n".join(indications),
            "clinical_studies": study_text,
            "referenced_nct_ids": referenced_nct_ids,
        }
    )


def _sanitize_projection(value: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    try:
        clean, redactions = sanitize_public_text(raw)
        if redactions:
            raise ValueError("selected clinical fields contain email PII")
        projected = json.loads(clean)
        validate_sanitized_public_payload(projected)
    except (ValueError, json.JSONDecodeError) as error:
        raise ProvenanceError("clinical source failed the public scanner") from error
    if not isinstance(projected, dict):
        raise ProvenanceError("clinical projection must be an object")
    return projected


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _regular_projection(base: Path, item: dict[str, Any]) -> dict[str, Any]:
    filename = item.get("retrieval_file")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ProvenanceError("clinical retrieval file is unsafe")
    expected = str(item.get("projection_sha256") or "")
    if _SHA256.fullmatch(expected) is None:
        raise ProvenanceError("clinical projection hash is invalid")
    try:
        raw = _read_regular_file(base / filename, MAX_SOURCE_BYTES)
    except OSError as error:
        raise ProvenanceError(f"cannot read clinical projection: {error}") from error
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ProvenanceError("clinical projection hash mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("clinical projection is not UTF-8 JSON") from error
    if not isinstance(payload, dict) or raw != _canonical_bytes(payload):
        raise ProvenanceError("clinical projection is not canonical JSON")
    return payload


def _expected_url(
    kind: str, request: dict[str, Any]
) -> tuple[str, str, dict[str, list[str]]]:
    nct_id = request["nct_id"]
    application = request["fda_application_number"]
    if kind == "clinical_trial":
        return "clinicaltrials.gov", f"/api/v2/studies/{nct_id}", {}
    if kind == "fda_application":
        return (
            "api.fda.gov",
            "/drug/drugsfda.json",
            {
                "search": [f"application_number:{application}"],
                "limit": ["1"],
            },
        )
    if kind == "fda_label":
        return (
            "api.fda.gov",
            "/drug/label.json",
            {
                "search": [f'openfda.application_number:"{application}"'],
                "limit": ["1"],
            },
        )
    raise ProvenanceError("clinical retrieval kind is invalid")


def _validate_retrieval_metadata(
    item: object, request: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "kind",
        "requested_url",
        "final_url",
        "status",
        "content_type",
        "redirect_chain",
        "observed_at",
        "response_sha256",
        "response_bytes",
        "projection_sha256",
        "projection_bytes",
        "retrieval_file",
        "source_materialization",
        "excluded_content",
        "email_redaction_count",
    }
    if not isinstance(item, dict) or set(item) != fields:
        raise ProvenanceError("clinical retrieval receipt is invalid")
    url = str(item.get("requested_url") or "")
    parsed = urlparse(url)
    host, path, query = _expected_url(str(item.get("kind") or ""), request)
    try:
        parsed_query = parse_qs(parsed.query, strict_parsing=True)
    except ValueError as error:
        raise ProvenanceError("clinical retrieval URL is invalid") from error
    if not (
        parsed.scheme == "https"
        and parsed.hostname == host
        and parsed.netloc == host
        and parsed.path == path
        and parsed_query == query
        and not parsed.fragment
        and not parsed.params
        and item.get("final_url") == url
        and item.get("status") == 200
        and item.get("content_type") == "application/json"
        and item.get("redirect_chain") == []
        and item.get("source_materialization") == "privacy_minimized_projection"
        and item.get("excluded_content")
        == ["contacts", "individual_records", "participant_narratives"]
        and item.get("email_redaction_count") == 0
        and isinstance(item.get("response_bytes"), int)
        and 0 < item["response_bytes"] <= MAX_SOURCE_BYTES
        and isinstance(item.get("projection_bytes"), int)
        and 0 < item["projection_bytes"] <= item["response_bytes"]
        and _SHA256.fullmatch(str(item.get("response_sha256") or "")) is not None
    ):
        raise ProvenanceError("clinical retrieval URL or transport metadata is invalid")
    _parse_timestamp(str(item.get("observed_at") or ""), "clinical observed_at")
    return dict(item)


def _validate_retrieval(
    item: object, base: Path, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    validated = _validate_retrieval_metadata(item, request)
    return validated, _regular_projection(base, validated)


def _fact(state_text: str, fact_id: str, field: str) -> dict[str, Any]:
    state = json.loads(state_text)
    value = state[field]
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    evidence_quote = f'  "{field}": {encoded.replace(chr(10), chr(10) + "  ")}'
    start = state_text.find(evidence_quote)
    if start < 0 or state_text.find(evidence_quote, start + 1) >= 0:
        raise ProvenanceError(f"clinical state does not uniquely bind {field}")
    end = start + len(evidence_quote)
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": evidence_quote,
        "char_start": start,
        "char_end": end,
    }


def _source_policy(kind: str) -> tuple[str, str, str, str]:
    if kind == "clinical_trial":
        return (
            "clinicaltrials_gov",
            CLINICAL_LICENSE,
            CLINICAL_ATTRIBUTION,
            CLINICAL_TERMS_URL,
        )
    return "openfda", OPENFDA_LICENSE, OPENFDA_ATTRIBUTION, OPENFDA_TERMS_URL


def _record(
    kind: str,
    projection: dict[str, Any],
    retrieval: dict[str, Any],
    fact_fields: list[tuple[str, str]],
) -> dict[str, Any]:
    if kind == "clinical_trial":
        identity = projection["nct_id"]
        record_id = f"clinicaltrials:{identity}"
    else:
        identity = projection["application_number"]
        record_id = f"openfda-{'application' if kind == 'fda_application' else 'label'}:{identity}"
    source_family, license_value, attribution, terms_url = _source_policy(kind)
    text = json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True)
    return {
        "record_id": record_id,
        "kind": kind,
        "identity": identity,
        "observed_at": retrieval["observed_at"],
        "temporal_semantics": "current_snapshot_observation",
        "source_family": source_family,
        "source_origin": "real_public",
        "source_url": retrieval["requested_url"],
        "retrieval_url": retrieval["requested_url"],
        "source_response_sha256": retrieval["response_sha256"],
        "source_projection_sha256": retrieval["projection_sha256"],
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "provenance_id": f"sha256:{retrieval['projection_sha256']}",
        "license": license_value,
        "attribution": attribution,
        "terms_url": terms_url,
        "parser": "longworld-clinical-json-projection@1",
        "state": projection,
        "text": text,
        "facts": [_fact(text, fact_id, field) for fact_id, field in fact_fields],
        "privacy_review": {
            "source_materialization": "privacy_minimized_projection",
            "excluded_content": [
                "contacts",
                "individual_records",
                "participant_narratives",
            ],
            "emails": "absent",
            "secrets": "fail_closed",
            "scanner": PUBLIC_SCANNER,
            "scanner_revision": PUBLIC_SCANNER_REVISION,
        },
    }


def _evidence(record: dict[str, Any], fact_id: str) -> dict[str, Any]:
    fact = next(item for item in record["facts"] if item.get("fact_id") == fact_id)
    return {
        "record_id": record["record_id"],
        "fact_ids": [fact_id],
        "evidence_quote": fact["evidence_quote"],
        "char_start": fact["char_start"],
        "char_end": fact["char_end"],
        "source_projection_sha256": record["source_projection_sha256"],
    }


def _relations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_kind = {record["kind"]: record for record in records}
    trial = by_kind["clinical_trial"]
    application = by_kind["fda_application"]
    label = by_kind["fda_label"]
    nct_id = trial["state"]["nct_id"]
    if (
        nct_id not in label["state"]["referenced_nct_ids"]
        or nct_id not in label["state"]["clinical_studies"]
    ):
        raise ProvenanceError("FDA label lacks an explicit NCT identifier join")
    application_number = application["state"]["application_number"]
    if label["state"]["application_number"] != application_number:
        raise ProvenanceError("FDA label/application identity mismatch")
    return [
        {
            "relation_id": f"label-explicitly-references-trial:{application_number}:{nct_id}",
            "kind": "label_explicitly_references_trial",
            "source_record_id": label["record_id"],
            "target_record_id": trial["record_id"],
            "join_key": {"kind": "nct_id", "value": nct_id},
            "evidence": [
                _evidence(label, "referenced_nct_id"),
                _evidence(trial, "nct_id"),
            ],
        },
        {
            "relation_id": f"label-matches-application:{application_number}",
            "kind": "label_matches_application",
            "source_record_id": label["record_id"],
            "target_record_id": application["record_id"],
            "join_key": {
                "kind": "fda_application_number",
                "value": application_number,
            },
            "evidence": [
                _evidence(label, "application_number"),
                _evidence(application, "application_number"),
            ],
        },
    ]


def _policies() -> dict[str, dict[str, str]]:
    return {
        "clinicaltrials_gov": {
            "attribution": CLINICAL_ATTRIBUTION,
            "license": CLINICAL_LICENSE,
            "terms_url": CLINICAL_TERMS_URL,
        },
        "openfda": {
            "attribution": OPENFDA_ATTRIBUTION,
            "license": OPENFDA_LICENSE,
            "license_url": OPENFDA_LICENSE_URL,
            "terms_url": OPENFDA_TERMS_URL,
        },
    }


def build_clinical_workflow_from_fetch_inventory(
    payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    fetch_inventory_sha256: str,
) -> dict[str, Any]:
    """Validate exact projections and derive a disabled source relation graph."""
    if set(payload) != _INVENTORY_FIELDS or not (
        payload.get("schema_version") == CLINICAL_FETCH_INVENTORY_SCHEMA
        and payload.get("source_status") == "public_api_export"
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("snapshot_semantics") == "current_state_observed_at_fetch"
        and payload.get("historical_snapshot_claims") is False
    ):
        raise ProvenanceError("clinical fetch inventory schema or flags are invalid")
    if _SHA256.fullmatch(fetch_inventory_sha256) is None:
        raise ProvenanceError("clinical fetch inventory hash is invalid")
    fetch_time = _parse_timestamp(
        str(payload.get("generated_at") or ""), "clinical fetch generated_at"
    )
    export_time = _parse_timestamp(generated_at, "generated_at")
    if export_time < fetch_time:
        raise ProvenanceError("clinical export predates fetch")
    request_file = payload.get("request_file")
    if not isinstance(request_file, str) or Path(request_file).name != request_file:
        raise ProvenanceError("clinical request file is unsafe")
    try:
        request_raw = _read_regular_file(
            base_directory / request_file, MAX_MANIFEST_BYTES
        )
    except OSError as error:
        raise ProvenanceError(f"cannot read clinical request: {error}") from error
    if hashlib.sha256(request_raw).hexdigest() != payload.get("request_sha256"):
        raise ProvenanceError("clinical request hash mismatch")
    try:
        request_payload = json.loads(request_raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("clinical request is invalid") from error
    request = validate_clinical_fetch_request(request_payload)
    if request["authorization"] != payload.get("authorization"):
        raise ProvenanceError("clinical authorization is not request-bound")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "max_retries",
        "allowed_actions",
        "request_file",
        "request_sha256",
        "user_agent_sha256",
        "source_policies",
        "retrievals",
    }:
        raise ProvenanceError("clinical fetch receipt is invalid")
    started = _parse_timestamp(str(receipt.get("started_at") or ""), "fetch started_at")
    completed = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "fetch completed_at"
    )
    if not (
        started == fetch_time
        and started <= completed <= export_time
        and receipt.get("max_retries") == request["max_retries"]
        and receipt.get("allowed_actions")
        == request["authorization"]["allowed_actions"]
        and receipt.get("request_file") == request_file
        and receipt.get("request_sha256") == payload.get("request_sha256")
        and receipt.get("user_agent_sha256")
        == hashlib.sha256(request["user_agent"].encode()).hexdigest()
        and receipt.get("source_policies") == _policies()
    ):
        raise ProvenanceError("clinical fetch receipt binding is invalid")
    raw_retrievals = receipt.get("retrievals")
    if (
        not isinstance(raw_retrievals, list)
        or len(raw_retrievals) != 3
        or payload.get("n_retrievals") != 3
    ):
        raise ProvenanceError("clinical retrieval count is invalid")
    validated = [
        _validate_retrieval(item, base_directory, request) for item in raw_retrievals
    ]
    retrievals = [item for item, _projection in validated]
    timestamps = [
        _parse_timestamp(item["observed_at"], "clinical observed_at")
        for item in retrievals
    ]
    if any(before > after for before, after in pairwise(timestamps)) or any(
        not started <= timestamp <= completed for timestamp in timestamps
    ):
        raise ProvenanceError("clinical retrieval chronology is invalid")
    projections = {item["kind"]: projection for item, projection in validated}
    if set(projections) != {"clinical_trial", "fda_application", "fda_label"}:
        raise ProvenanceError("clinical retrieval kinds are invalid")
    normalized = {
        "clinical_trial": project_clinical_trial_response(
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": projections["clinical_trial"].get("nct_id"),
                        "organization": {
                            "fullName": projections["clinical_trial"].get(
                                "organization"
                            )
                        },
                        "briefTitle": projections["clinical_trial"].get("brief_title"),
                        "officialTitle": projections["clinical_trial"].get(
                            "official_title"
                        ),
                    },
                    "statusModule": {
                        "statusVerifiedDate": projections["clinical_trial"].get(
                            "status_verified_date"
                        ),
                        "overallStatus": projections["clinical_trial"].get(
                            "overall_status"
                        ),
                        "completionDateStruct": {
                            "date": projections["clinical_trial"].get("completion_date")
                        },
                        "lastUpdatePostDateStruct": {
                            "date": projections["clinical_trial"].get(
                                "last_update_post_date"
                            )
                        },
                    },
                    "outcomesModule": {
                        "primaryOutcomes": [
                            {
                                "measure": projections["clinical_trial"].get(
                                    "primary_outcome_measure"
                                ),
                                "timeFrame": projections["clinical_trial"].get(
                                    "primary_outcome_time_frame"
                                ),
                            }
                        ]
                    },
                },
                "hasResults": projections["clinical_trial"].get("has_results"),
            },
            request["nct_id"],
        )
    }
    if normalized["clinical_trial"] != projections["clinical_trial"]:
        raise ProvenanceError("clinical trial projection fields are invalid")
    application_projection = projections["fda_application"]
    label_projection = projections["fda_label"]
    if not (
        set(application_projection)
        == {
            "application_number",
            "sponsor_name",
            "original_submission_status",
            "original_approval_date",
            "original_review_priority",
            "product_names",
        }
        and application_projection.get("application_number")
        == request["fda_application_number"]
        and application_projection.get("original_submission_status") == "AP"
        and _DATE.fullmatch(
            str(application_projection.get("original_approval_date") or "")
        )
        and isinstance(application_projection.get("product_names"), list)
        and application_projection["product_names"]
        and set(label_projection)
        == {
            "application_number",
            "label_id",
            "label_set_id",
            "effective_date",
            "version",
            "indications_and_usage",
            "clinical_studies",
            "referenced_nct_ids",
        }
        and label_projection.get("application_number")
        == request["fda_application_number"]
        and _DATE.fullmatch(str(label_projection.get("effective_date") or ""))
        and label_projection.get("referenced_nct_ids")
        == sorted(
            set(
                _NCT_MENTION.findall(
                    str(label_projection.get("clinical_studies") or "")
                )
            )
        )
    ):
        raise ProvenanceError("openFDA projection fields or identity are invalid")
    for projection in projections.values():
        try:
            validate_sanitized_public_payload(projection)
        except ValueError as error:
            raise ProvenanceError(
                "clinical projection failed the public scanner"
            ) from error
    retrieval_by_kind = {item["kind"]: item for item in retrievals}
    records = [
        _record(
            "clinical_trial",
            projections["clinical_trial"],
            retrieval_by_kind["clinical_trial"],
            [
                ("nct_id", "nct_id"),
                ("overall_status", "overall_status"),
                ("completion_date", "completion_date"),
                ("primary_outcome_measure", "primary_outcome_measure"),
            ],
        ),
        _record(
            "fda_application",
            application_projection,
            retrieval_by_kind["fda_application"],
            [
                ("application_number", "application_number"),
                ("original_approval_date", "original_approval_date"),
                ("original_submission_status", "original_submission_status"),
            ],
        ),
        _record(
            "fda_label",
            label_projection,
            retrieval_by_kind["fda_label"],
            [
                ("application_number", "application_number"),
                ("referenced_nct_id", "referenced_nct_ids"),
                ("effective_date", "effective_date"),
            ],
        ),
    ]
    manifest = {
        "schema_version": CLINICAL_WORKFLOW_MANIFEST_SCHEMA,
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "semantic_facts_train_ready": False,
        "snapshot_semantics": "current_state_observed_at_fetch",
        "historical_snapshot_claims": False,
        "generated_at": generated_at,
        "fetch_inventory": {
            "file": base_directory.name + "/clinical_fetch_inventory.json",
            "sha256": fetch_inventory_sha256,
        },
        "fetch_receipt": receipt,
        "authorization": request["authorization"],
        "source_policies": _policies(),
        "records": records,
        "relations": _relations(records),
        "n": len(records),
        "n_relations": 2,
    }
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(payload: dict[str, Any]) -> None:
    expected_fields = {
        "schema_version",
        "source_status",
        "data_stage",
        "hybrid_train_ready",
        "production_eligible",
        "generation_integration",
        "semantic_facts_train_ready",
        "snapshot_semantics",
        "historical_snapshot_claims",
        "generated_at",
        "fetch_inventory",
        "fetch_receipt",
        "authorization",
        "source_policies",
        "records",
        "relations",
        "n",
        "n_relations",
    }
    if (
        payload.get(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
        == LOCAL_PROBE_TRUST_ISOLATION_VALUE
    ):
        expected_fields.add(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
    if not (
        set(payload) == expected_fields
        and payload.get("schema_version") == CLINICAL_WORKFLOW_MANIFEST_SCHEMA
        and payload.get("source_status") == "public_api_export"
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("snapshot_semantics") == "current_state_observed_at_fetch"
        and payload.get("historical_snapshot_claims") is False
        and payload.get("source_policies") == _policies()
    ):
        raise ProvenanceError("clinical workflow flags or policies are invalid")
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    fetch_inventory = payload.get("fetch_inventory")
    authorization = payload.get("authorization")
    if (
        not isinstance(fetch_inventory, dict)
        or set(fetch_inventory) != {"file", "sha256"}
        or not isinstance(fetch_inventory.get("file"), str)
        or not fetch_inventory["file"].endswith("/clinical_fetch_inventory.json")
        or _SHA256.fullmatch(str(fetch_inventory.get("sha256") or "")) is None
        or not isinstance(authorization, dict)
        or set(authorization)
        != {"record_id", "scope", "basis", "reviewed_at", "allowed_actions"}
        or authorization.get("allowed_actions") != sorted(_ALLOWED_ACTIONS)
    ):
        raise ProvenanceError("clinical workflow lineage metadata is invalid")
    _parse_timestamp(
        str(authorization.get("reviewed_at") or ""), "authorization.reviewed_at"
    )
    records = payload.get("records")
    relations = payload.get("relations")
    if (
        not isinstance(records, list)
        or len(records) != 3
        or payload.get("n") != 3
        or not isinstance(relations, list)
        or len(relations) != 2
        or payload.get("n_relations") != 2
    ):
        raise ProvenanceError("clinical workflow counts are invalid")
    if {
        str(record.get("kind") or "") for record in records if isinstance(record, dict)
    } != {
        "clinical_trial",
        "fda_application",
        "fda_label",
    }:
        raise ProvenanceError("clinical workflow record kinds are invalid")
    for raw_record in records:
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "record_id",
            "kind",
            "identity",
            "observed_at",
            "temporal_semantics",
            "source_family",
            "source_origin",
            "source_url",
            "retrieval_url",
            "source_response_sha256",
            "source_projection_sha256",
            "text_sha256",
            "provenance_id",
            "license",
            "attribution",
            "terms_url",
            "parser",
            "state",
            "text",
            "facts",
            "privacy_review",
        }:
            raise ProvenanceError("clinical workflow record is invalid")
        state = raw_record.get("state")
        text = raw_record.get("text")
        if not isinstance(state, dict) or not isinstance(text, str):
            raise ProvenanceError("clinical workflow state is invalid")
        expected_text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
        if text != expected_text or hashlib.sha256(
            text.encode()
        ).hexdigest() != raw_record.get("text_sha256"):
            raise ProvenanceError("clinical workflow text hash is invalid")
        if raw_record.get("temporal_semantics") != "current_snapshot_observation" or (
            "occurred_at" in raw_record
        ):
            raise ProvenanceError("clinical workflow temporal semantics are invalid")
        kind = raw_record["kind"]
        if kind == "clinical_trial":
            identity = state.get("nct_id")
            expected_record_id = f"clinicaltrials:{identity}"
            expected_family = "clinicaltrials_gov"
            expected_fact_ids = {
                "nct_id",
                "overall_status",
                "completion_date",
                "primary_outcome_measure",
            }
            state_valid = (
                set(state)
                == {
                    "nct_id",
                    "organization",
                    "brief_title",
                    "official_title",
                    "overall_status",
                    "status_verified_date",
                    "completion_date",
                    "last_update_post_date",
                    "primary_outcome_measure",
                    "primary_outcome_time_frame",
                    "has_results",
                }
                and isinstance(identity, str)
                and _NCT_ID.fullmatch(identity) is not None
                and state.get("has_results") is True
                and _DATE.fullmatch(str(state.get("completion_date") or "")) is not None
            )
        elif kind == "fda_application":
            identity = state.get("application_number")
            expected_record_id = f"openfda-application:{identity}"
            expected_family = "openfda"
            expected_fact_ids = {
                "application_number",
                "original_approval_date",
                "original_submission_status",
            }
            state_valid = (
                set(state)
                == {
                    "application_number",
                    "sponsor_name",
                    "original_submission_status",
                    "original_approval_date",
                    "original_review_priority",
                    "product_names",
                }
                and isinstance(identity, str)
                and _FDA_APPLICATION.fullmatch(identity) is not None
                and state.get("original_submission_status") == "AP"
                and _DATE.fullmatch(str(state.get("original_approval_date") or ""))
                is not None
                and isinstance(state.get("product_names"), list)
                and bool(state["product_names"])
            )
        else:
            identity = state.get("application_number")
            expected_record_id = f"openfda-label:{identity}"
            expected_family = "openfda"
            expected_fact_ids = {
                "application_number",
                "referenced_nct_id",
                "effective_date",
            }
            clinical_studies = str(state.get("clinical_studies") or "")
            references = state.get("referenced_nct_ids")
            state_valid = (
                set(state)
                == {
                    "application_number",
                    "label_id",
                    "label_set_id",
                    "effective_date",
                    "version",
                    "indications_and_usage",
                    "clinical_studies",
                    "referenced_nct_ids",
                }
                and isinstance(identity, str)
                and _FDA_APPLICATION.fullmatch(identity) is not None
                and _DATE.fullmatch(str(state.get("effective_date") or "")) is not None
                and references == sorted(set(_NCT_MENTION.findall(clinical_studies)))
            )
        if not (
            state_valid
            and raw_record.get("identity") == identity
            and raw_record.get("record_id") == expected_record_id
            and raw_record.get("source_family") == expected_family
            and raw_record.get("source_origin") == "real_public"
            and raw_record.get("retrieval_url") == raw_record.get("source_url")
            and raw_record.get("parser") == "longworld-clinical-json-projection@1"
            and raw_record.get("license")
            and raw_record.get("attribution")
            and raw_record.get("terms_url")
            and raw_record.get("privacy_review")
            == {
                "source_materialization": "privacy_minimized_projection",
                "excluded_content": [
                    "contacts",
                    "individual_records",
                    "participant_narratives",
                ],
                "emails": "absent",
                "secrets": "fail_closed",
                "scanner": PUBLIC_SCANNER,
                "scanner_revision": PUBLIC_SCANNER_REVISION,
            }
        ):
            raise ProvenanceError("clinical workflow record identity is invalid")
        projection_hash = str(raw_record.get("source_projection_sha256") or "")
        if (
            _SHA256.fullmatch(projection_hash) is None
            or hashlib.sha256(_canonical_bytes(state)).hexdigest() != projection_hash
            or raw_record.get("provenance_id") != f"sha256:{projection_hash}"
            or _SHA256.fullmatch(str(raw_record.get("source_response_sha256") or ""))
            is None
        ):
            raise ProvenanceError("clinical workflow provenance is invalid")
        facts = raw_record.get("facts")
        if (
            not isinstance(facts, list)
            or {
                str(fact.get("fact_id") or "")
                for fact in facts
                if isinstance(fact, dict)
            }
            != expected_fact_ids
        ):
            raise ProvenanceError("clinical workflow facts are invalid")
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) != {
                "fact_id",
                "field",
                "value",
                "evidence_quote",
                "char_start",
                "char_end",
            }:
                raise ProvenanceError("clinical workflow fact schema is invalid")
            start, end = fact["char_start"], fact["char_end"]
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or not 0 <= start < end <= len(text)
                or text[start:end] != fact["evidence_quote"]
                or state.get(fact["field"]) != fact["value"]
            ):
                raise ProvenanceError("clinical workflow fact evidence is invalid")
        _parse_timestamp(str(raw_record.get("observed_at") or ""), "record observed_at")
    by_kind = {record["kind"]: record for record in records}
    manifest_request = {
        "nct_id": by_kind["clinical_trial"]["identity"],
        "fda_application_number": by_kind["fda_application"]["identity"],
    }
    receipt = payload.get("fetch_receipt")
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "started_at",
            "completed_at",
            "max_retries",
            "allowed_actions",
            "request_file",
            "request_sha256",
            "user_agent_sha256",
            "source_policies",
            "retrievals",
        }
        or receipt.get("source_policies") != _policies()
    ):
        raise ProvenanceError("clinical fetch receipt lineage is invalid")
    raw_retrievals = receipt.get("retrievals")
    if not isinstance(raw_retrievals, list) or len(raw_retrievals) != len(records):
        raise ProvenanceError("clinical fetch receipt lineage is invalid")
    retrievals: dict[str, dict[str, Any]] = {}
    for raw_retrieval in raw_retrievals:
        retrieval = _validate_retrieval_metadata(raw_retrieval, manifest_request)
        kind = str(retrieval["kind"])
        if kind in retrievals:
            raise ProvenanceError("clinical fetch receipt lineage is invalid")
        retrievals[kind] = retrieval
    if set(retrievals) != set(by_kind):
        raise ProvenanceError("clinical fetch receipt lineage is invalid")
    for kind, record in by_kind.items():
        retrieval = retrievals[kind]
        parsed = urlparse(record["source_url"])
        host, path, query = _expected_url(kind, manifest_request)
        try:
            parsed_query = parse_qs(parsed.query, strict_parsing=True)
        except ValueError as error:
            raise ProvenanceError("clinical workflow source URL is invalid") from error
        if not (
            parsed.scheme == "https"
            and parsed.hostname == host
            and parsed.netloc == host
            and parsed.path == path
            and parsed_query == query
            and not parsed.fragment
            and not parsed.params
        ):
            raise ProvenanceError("clinical workflow source URL is invalid")
        if (
            record.get("source_url") != retrieval.get("requested_url")
            or record.get("retrieval_url") != retrieval.get("final_url")
            or record.get("source_response_sha256") != retrieval.get("response_sha256")
            or record.get("source_projection_sha256")
            != retrieval.get("projection_sha256")
            or record.get("observed_at") != retrieval.get("observed_at")
            or retrieval.get("projection_bytes")
            != len(_canonical_bytes(record["state"]))
        ):
            raise ProvenanceError("clinical fetch receipt lineage is invalid")
    if relations != _relations(records):
        raise ProvenanceError("clinical workflow relation evidence is invalid")
    try:
        validate_sanitized_public_payload(payload)
    except ValueError as error:
        raise ProvenanceError("clinical workflow failed the public scanner") from error


def load_clinical_workflow_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Load a signed source inventory and revalidate all semantic bindings."""
    try:
        raw = _read_regular_file(path, MAX_CLINICAL_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read clinical workflow manifest: {error}"
        ) from error
    if not isinstance(payload, dict) or not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("clinical workflow manifest attestation is invalid")
    unsigned = {key: value for key, value in payload.items() if key != "attestation"}
    _validate_manifest(unsigned)
    return payload


CLINICAL_TRIAL_APPROVAL_TASK_SCHEMA = "longworld.clinical-trial-approval-task.v2"


def _task_source_manifest(task: dict[str, Any]) -> dict[str, Any]:
    if not (
        task.get("schema_version") == CLINICAL_TRIAL_APPROVAL_TASK_SCHEMA
        and task.get("data_stage") == "candidate_task"
        and task.get("train_ready") is False
        and task.get("production_eligible") is False
        and task.get("promotion_eligible") is False
        and task.get("complete_world") is False
        and task.get("promoted") is False
        and task.get("generation_integration") == "disabled"
        and task.get("source_attestation_verified") is False
    ):
        raise ProvenanceError("clinical trial approval task contract is invalid")
    manifest = task.get("source_manifest")
    if not isinstance(manifest, dict):
        raise ProvenanceError("clinical task source manifest is missing")
    unsigned = {key: value for key, value in manifest.items() if key != "attestation"}
    _validate_manifest(unsigned)
    if (
        task.get("source_manifest_sha256")
        != hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    ):
        raise ProvenanceError("clinical task source manifest binding is invalid")
    if task.get("source_records") != unsigned.get("records"):
        raise ProvenanceError("clinical task source manifest projection is invalid")
    if task.get("source_receipt") != unsigned.get("fetch_receipt"):
        raise ProvenanceError("clinical task source receipt binding is invalid")
    inventory = unsigned.get("fetch_inventory")
    receipt = unsigned.get("fetch_receipt")
    if not isinstance(inventory, dict) or not isinstance(receipt, dict):
        raise ProvenanceError("clinical task source manifest lineage is invalid")
    if task.get("source_inventory_binding") != {
        "fetch_inventory_sha256": inventory.get("sha256"),
        "receipt_sha256": hashlib.sha256(_canonical_bytes(receipt)).hexdigest(),
    }:
        raise ProvenanceError("clinical task source manifest lineage is invalid")
    manifest_relations = {
        str(relation.get("kind") or ""): relation
        for relation in unsigned.get("relations", [])
        if isinstance(relation, dict)
    }
    evidence_items = task.get("evidence_items")
    if not isinstance(evidence_items, list):
        raise ProvenanceError("clinical task evidence contract is invalid")
    task_relations = {
        str(item.get("kind") or ""): item.get("relation")
        for item in evidence_items
        if isinstance(item, dict) and isinstance(item.get("relation"), dict)
    }
    if task_relations != manifest_relations:
        raise ProvenanceError("clinical task source relation binding is invalid")
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


def _task_record_map(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_records = task.get("source_records")
    bindings = task.get("source_bindings")
    if not isinstance(raw_records, list) or len(raw_records) != 3:
        raise ProvenanceError("clinical task source records are invalid")
    records = {
        str(record.get("record_id") or ""): record
        for record in raw_records
        if isinstance(record, dict)
    }
    if (
        len(records) != len(raw_records)
        or not isinstance(bindings, dict)
        or set(bindings) != set(records)
    ):
        raise ProvenanceError("clinical task source bindings are invalid")
    for record_id, record in records.items():
        binding = bindings.get(record_id)
        text = record.get("text")
        state = record.get("state")
        if not isinstance(binding, dict) or set(binding) != {
            "text_sha256",
            "projection_sha256",
            "response_sha256",
        }:
            raise ProvenanceError("clinical task source binding schema is invalid")
        if not isinstance(text, str) or not isinstance(state, dict):
            raise ProvenanceError("clinical task source state is invalid")
        expected_text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
        text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        projection_sha256 = hashlib.sha256(_canonical_bytes(state)).hexdigest()
        if (
            text != expected_text
            or record.get("text_sha256") != text_sha256
            or record.get("source_projection_sha256") != projection_sha256
            or binding.get("text_sha256") != text_sha256
            or binding.get("projection_sha256") != projection_sha256
            or binding.get("response_sha256") != record.get("source_response_sha256")
        ):
            raise ProvenanceError("clinical task source byte/state binding is invalid")
    return records


def _task_receipt_binding_valid(
    task: dict[str, Any], records: dict[str, dict[str, Any]]
) -> bool:
    receipt = task.get("source_receipt")
    inventory_binding = task.get("source_inventory_binding")
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "started_at",
            "completed_at",
            "max_retries",
            "allowed_actions",
            "request_file",
            "request_sha256",
            "user_agent_sha256",
            "source_policies",
            "retrievals",
        }
        or not isinstance(inventory_binding, dict)
        or set(inventory_binding)
        != {
            "fetch_inventory_sha256",
            "receipt_sha256",
        }
        or _SHA256.fullmatch(str(inventory_binding.get("fetch_inventory_sha256") or ""))
        is None
        or inventory_binding.get("receipt_sha256")
        != hashlib.sha256(_canonical_bytes(receipt)).hexdigest()
    ):
        raise ProvenanceError("clinical task receipt binding is invalid")
    retries = receipt.get("max_retries")
    if (
        receipt.get("source_policies") != _policies()
        or receipt.get("allowed_actions") != sorted(_ALLOWED_ACTIONS)
        or receipt.get("request_file") != "clinical_fetch_request.json"
        or _SHA256.fullmatch(str(receipt.get("request_sha256") or "")) is None
        or _SHA256.fullmatch(str(receipt.get("user_agent_sha256") or "")) is None
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("clinical task receipt binding is invalid")
    started = _parse_timestamp(
        str(receipt.get("started_at") or ""), "clinical task receipt started_at"
    )
    completed = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "clinical task receipt completed_at"
    )
    if completed < started:
        raise ProvenanceError("clinical task receipt binding is invalid")
    by_kind = {record["kind"]: record for record in records.values()}
    if set(by_kind) != {"clinical_trial", "fda_application", "fda_label"}:
        raise ProvenanceError("clinical task receipt binding is invalid")
    request = {
        "nct_id": by_kind["clinical_trial"]["identity"],
        "fda_application_number": by_kind["fda_application"]["identity"],
    }
    raw_retrievals = receipt.get("retrievals")
    if not isinstance(raw_retrievals, list) or len(raw_retrievals) != 3:
        raise ProvenanceError("clinical task receipt binding is invalid")
    retrievals: dict[str, dict[str, Any]] = {}
    for raw_retrieval in raw_retrievals:
        retrieval = _validate_retrieval_metadata(raw_retrieval, request)
        kind = str(retrieval["kind"])
        if kind in retrievals:
            raise ProvenanceError("clinical task receipt binding is invalid")
        retrievals[kind] = retrieval
    if set(retrievals) != set(by_kind):
        raise ProvenanceError("clinical task receipt binding is invalid")
    for kind, record in by_kind.items():
        retrieval = retrievals[kind]
        observed = _parse_timestamp(
            str(retrieval.get("observed_at") or ""),
            "clinical task receipt observed_at",
        )
        if not (
            started <= observed <= completed
            and retrieval["requested_url"] == record["source_url"]
            and retrieval["final_url"] == record["retrieval_url"]
            and retrieval["response_sha256"] == record["source_response_sha256"]
            and retrieval["projection_sha256"] == record["source_projection_sha256"]
            and retrieval["projection_bytes"] == len(_canonical_bytes(record["state"]))
            and retrieval["observed_at"] == record["observed_at"]
        ):
            raise ProvenanceError("clinical task receipt binding is invalid")
    return True


def _task_fact(record: dict[str, Any], fact_id: str) -> dict[str, Any]:
    facts = record.get("facts")
    matches = [
        fact
        for fact in facts or []
        if isinstance(fact, dict) and fact.get("fact_id") == fact_id
    ]
    if len(matches) != 1:
        raise ProvenanceError("clinical task fact is not unique")
    fact = matches[0]
    start, end = fact.get("char_start"), fact.get("char_end")
    quote = fact.get("evidence_quote")
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
        or start < 0
        or end != start + len(quote)
        or record["text"][start:end] != quote
        or record["state"].get(fact.get("field")) != fact.get("value")
    ):
        raise ProvenanceError("clinical task fact evidence binding is invalid")
    return fact


def _task_fact_group(
    item: dict[str, Any], records: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    record = records.get(str(item.get("record_id") or ""))
    fact_ids = item.get("fact_ids")
    if (
        record is None
        or not isinstance(fact_ids, list)
        or not fact_ids
        or any(not isinstance(fact_id, str) for fact_id in fact_ids)
        or len(fact_ids) != len(set(fact_ids))
    ):
        raise ProvenanceError("clinical task fact group is invalid")
    facts = {fact_id: _task_fact(record, fact_id) for fact_id in fact_ids}
    surface = "\n".join(str(fact["evidence_quote"]) for fact in facts.values())
    if item.get("surface_text") != surface:
        raise ProvenanceError("clinical task fact group evidence is invalid")
    return record, facts


def _task_relation(
    item: dict[str, Any], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    relation = item.get("relation")
    if not isinstance(relation, dict):
        raise ProvenanceError("clinical task relation is invalid")
    source = records.get(str(relation.get("source_record_id") or ""))
    target = records.get(str(relation.get("target_record_id") or ""))
    evidence = relation.get("evidence")
    if source is None or target is None or not isinstance(evidence, list):
        raise ProvenanceError("clinical task relation is invalid")
    evidence_ids: set[str] = set()
    for raw_evidence in evidence:
        if not isinstance(raw_evidence, dict):
            raise ProvenanceError("clinical task relation evidence is invalid")
        record = records.get(str(raw_evidence.get("record_id") or ""))
        start, end = raw_evidence.get("char_start"), raw_evidence.get("char_end")
        quote = raw_evidence.get("evidence_quote")
        if (
            record is None
            or raw_evidence.get("source_projection_sha256")
            != record.get("source_projection_sha256")
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not isinstance(quote, str)
            or start < 0
            or end != start + len(quote)
            or record["text"][start:end] != quote
        ):
            raise ProvenanceError("clinical task relation evidence binding is invalid")
        evidence_ids.add(record["record_id"])
    if not {source["record_id"], target["record_id"]}.issubset(evidence_ids):
        raise ProvenanceError("clinical task relation endpoint evidence is incomplete")
    surface = "\n".join(
        str(evidence_item["evidence_quote"]) for evidence_item in evidence
    )
    if item.get("surface_text") != surface:
        raise ProvenanceError("clinical task relation surface binding is invalid")
    kind = relation.get("kind")
    join_key = relation.get("join_key")
    if not isinstance(join_key, dict):
        raise ProvenanceError("clinical task relation join is invalid")
    if kind == "label_explicitly_references_trial":
        value = target["state"].get("nct_id")
        valid = (
            source["kind"] == "fda_label"
            and target["kind"] == "clinical_trial"
            and join_key == {"kind": "nct_id", "value": value}
            and value in source["state"].get("referenced_nct_ids", [])
            and value in source["state"].get("clinical_studies", "")
        )
    elif kind == "label_matches_application":
        value = target["state"].get("application_number")
        valid = (
            source["kind"] == "fda_label"
            and target["kind"] == "fda_application"
            and join_key == {"kind": "fda_application_number", "value": value}
            and source["state"].get("application_number") == value
        )
    else:
        valid = False
    if not valid:
        raise ProvenanceError("clinical task relation join is invalid")
    return relation


def _clinical_answer(
    *,
    trial_record_id: str,
    application_record_id: str,
    label_record_id: str,
    trial_relation: dict[str, Any] | None,
    application_relation: dict[str, Any] | None,
    nct_id: str,
    overall_status: str,
    primary_outcome: str,
    application_number: str,
    approval_date: str,
    effective_date: str,
) -> str:
    if (
        trial_relation is None
        or application_relation is None
        or trial_relation.get("source_record_id") != label_record_id
        or trial_relation.get("target_record_id") != trial_record_id
        or application_relation.get("source_record_id") != label_record_id
        or application_relation.get("target_record_id") != application_record_id
        or trial_relation.get("join_key") != {"kind": "nct_id", "value": nct_id}
        or application_relation.get("join_key")
        != {"kind": "fda_application_number", "value": application_number}
        or not all(
            (
                nct_id,
                overall_status,
                primary_outcome,
                application_number,
                approval_date,
                effective_date,
            )
        )
    ):
        return "unknown"
    return (
        f"{nct_id} | {overall_status} | {primary_outcome} | {application_number} | "
        f"original approval {approval_date} | label effective {effective_date}"
    )


def _clinical_surface_is_gold_free(surface: str, answer: str) -> bool:
    """Reject verbatim or format-transformed disclosure of every answer input."""
    parts = answer.split(" | ")
    if (
        len(parts) != 6
        or not parts[4].startswith("original approval ")
        or not parts[5].startswith("label effective ")
    ):
        return False
    answer_inputs = (
        *parts[:4],
        parts[4].removeprefix("original approval "),
        parts[5].removeprefix("label effective "),
    )
    return answer not in surface and not all(
        value in surface for value in answer_inputs
    )


def replay_clinical_trial_approval_task(
    task: dict[str, Any],
    *,
    evidence_ids: list[str] | None = None,
    counterfactual: bool = False,
) -> str:
    """Strictly execute the clinical task from source bodies and receipt bindings."""
    _task_source_manifest(task)
    if (
        task.get("query_type") != "trial_approval_trace"
        or task.get("answer_program_id") != "clinical.trial_approval_trace.v1"
    ):
        raise ProvenanceError("clinical trial approval task contract is invalid")
    records = _task_record_map(task)
    _task_receipt_binding_valid(task, records)
    raw_items = task.get("evidence_items")
    essentials = task.get("essential_evidence_ids")
    if not isinstance(raw_items, list) or not isinstance(essentials, list):
        raise ProvenanceError("clinical task evidence contract is invalid")
    items = {
        str(item.get("evidence_id") or ""): item
        for item in raw_items
        if isinstance(item, dict)
    }
    if any(not isinstance(item, str) for item in essentials):
        raise ProvenanceError("clinical task essential evidence contract is invalid")
    if (
        len(items) != len(raw_items)
        or set(items) != set(essentials)
        or len(essentials) != len(set(essentials))
    ):
        raise ProvenanceError("clinical task essential evidence contract is invalid")
    selected_ids = essentials if evidence_ids is None else evidence_ids
    if any(not isinstance(item, str) for item in selected_ids):
        raise ProvenanceError("clinical task evidence selection is invalid")
    selected = set(selected_ids)
    if not selected.issubset(items):
        raise ProvenanceError("clinical task evidence selection is invalid")

    nct_id = overall_status = primary_outcome = ""
    application_number = approval_date = effective_date = ""
    trial_record_id = application_record_id = label_record_id = ""
    trial_relation: dict[str, Any] | None = None
    application_relation: dict[str, Any] | None = None
    for evidence_id in selected:
        item = items[evidence_id]
        kind = item.get("kind")
        if kind == "trial_summary":
            record, facts = _task_fact_group(item, records)
            trial_record_id = record["record_id"]
            nct_id = str(facts["nct_id"]["value"])
            overall_status = str(facts["overall_status"]["value"])
            primary_outcome = str(facts["primary_outcome_measure"]["value"])
        elif kind == "approval_date":
            record, facts = _task_fact_group(item, records)
            application_record_id = record["record_id"]
            approval_date = str(facts["original_approval_date"]["value"])
        elif kind == "label_effective_date":
            record, facts = _task_fact_group(item, records)
            label_record_id = record["record_id"]
            effective_date = str(facts["effective_date"]["value"])
        elif kind == "label_explicitly_references_trial":
            trial_relation = _task_relation(item, records)
        elif kind == "label_matches_application":
            application_relation = _task_relation(item, records)
            join_key = application_relation["join_key"]
            application_number = str(join_key["value"])
        else:
            raise ProvenanceError("clinical task evidence kind is invalid")

    if counterfactual and items:
        twin = task.get("counterfactual_twin")
        parent = next(
            (
                item
                for item in items.values()
                if item.get("kind") == "label_effective_date"
            ),
            None,
        )
        if not isinstance(twin, dict) or parent is None:
            raise ProvenanceError("clinical counterfactual twin is missing")
        parent_record, parent_facts = _task_fact_group(parent, records)
        fact = parent_facts["effective_date"]
        parent_value = str(fact["value"])
        replacement = str(twin.get("value") or "")
        value_start = parent_record["text"].find(
            parent_value, int(fact["char_start"]), int(fact["char_end"])
        )
        value_end = value_start + len(parent_value)
        expected_state = dict(parent_record["state"])
        expected_state["effective_date"] = replacement
        expected_text = json.dumps(
            expected_state, ensure_ascii=False, indent=2, sort_keys=True
        )
        expected_quote = str(fact["evidence_quote"]).replace(
            parent_value, replacement, 1
        )
        text = twin.get("text")
        if (
            twin.get("record_id") != parent_record["record_id"]
            or twin.get("source_origin") != "synthetic_counterfactual"
            or twin.get("provenance_operation") != "replace_label_effective_date"
            or twin.get("parent_text_sha256") != parent_record["text_sha256"]
            or twin.get("parent_evidence_quote") != fact["evidence_quote"]
            or twin.get("parent_value") != parent_value
            or replacement == parent_value
            or _DATE.fullmatch(replacement) is None
            or value_start < 0
            or twin.get("value_char_start") != value_start
            or twin.get("value_char_end") != value_start + len(replacement)
            or twin.get("state") != expected_state
            or not isinstance(text, str)
            or text != expected_text
            or twin.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest()
            or twin.get("evidence_quote") != expected_quote
            or twin.get("char_start") != fact["char_start"]
            or twin.get("char_end") != int(fact["char_start"]) + len(expected_quote)
            or value_end > int(fact["char_end"])
        ):
            raise ProvenanceError("clinical counterfactual replacement is invalid")
        if parent["evidence_id"] in selected:
            effective_date = replacement

    return _clinical_answer(
        trial_record_id=trial_record_id,
        application_record_id=application_record_id,
        label_record_id=label_record_id,
        trial_relation=trial_relation,
        application_relation=application_relation,
        nct_id=nct_id,
        overall_status=overall_status,
        primary_outcome=primary_outcome,
        application_number=application_number,
        approval_date=approval_date,
        effective_date=effective_date,
    )


def build_clinical_trial_approval_task(manifest: dict[str, Any]) -> dict[str, Any]:
    """Audit a disabled source inventory and compile one executable candidate."""
    source_manifest = deepcopy(manifest)
    unsigned_manifest = {
        key: value for key, value in source_manifest.items() if key != "attestation"
    }
    _validate_manifest(unsigned_manifest)
    records = deepcopy(unsigned_manifest["records"])
    by_kind = {record["kind"]: record for record in records}
    relations = {
        relation["kind"]: relation
        for relation in deepcopy(unsigned_manifest["relations"])
    }

    def fact_item(
        evidence_id: str, kind: str, record: dict[str, Any], fact_ids: list[str]
    ) -> dict[str, Any]:
        facts = [_task_fact(record, fact_id) for fact_id in fact_ids]
        return {
            "evidence_id": evidence_id,
            "kind": kind,
            "record_id": record["record_id"],
            "fact_ids": fact_ids,
            "surface_text": "\n".join(str(fact["evidence_quote"]) for fact in facts),
        }

    def relation_item(evidence_id: str, relation: dict[str, Any]) -> dict[str, Any]:
        return {
            "evidence_id": evidence_id,
            "kind": relation["kind"],
            "surface_text": "\n".join(
                str(item["evidence_quote"]) for item in relation["evidence"]
            ),
            "relation": relation,
        }

    trial = by_kind["clinical_trial"]
    application = by_kind["fda_application"]
    label = by_kind["fda_label"]
    evidence_items = [
        fact_item(
            "trial_summary",
            "trial_summary",
            trial,
            ["nct_id", "overall_status", "primary_outcome_measure"],
        ),
        relation_item(
            "trial_label_relation", relations["label_explicitly_references_trial"]
        ),
        relation_item(
            "label_application_relation", relations["label_matches_application"]
        ),
        fact_item(
            "approval_date",
            "approval_date",
            application,
            ["original_approval_date"],
        ),
        fact_item(
            "label_effective_date",
            "label_effective_date",
            label,
            ["effective_date"],
        ),
    ]
    effective_fact = _task_fact(label, "effective_date")
    parent_value = str(effective_fact["value"])
    replacement = "2030-01-01"
    value_start = label["text"].find(
        parent_value,
        int(effective_fact["char_start"]),
        int(effective_fact["char_end"]),
    )
    twin_state = dict(label["state"])
    twin_state["effective_date"] = replacement
    twin_text = json.dumps(twin_state, ensure_ascii=False, indent=2, sort_keys=True)
    twin_quote = str(effective_fact["evidence_quote"]).replace(
        parent_value, replacement, 1
    )
    receipt = deepcopy(unsigned_manifest["fetch_receipt"])
    task: dict[str, Any] = {
        "schema_version": CLINICAL_TRIAL_APPROVAL_TASK_SCHEMA,
        "data_stage": "candidate_task",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "source_attestation_verified": False,
        "query_type": "trial_approval_trace",
        "answer_program_id": "clinical.trial_approval_trace.v1",
        "question": (
            "For the completed registered trial explicitly referenced by the FDA "
            "label, return its NCT ID, status and primary outcome measure; then "
            "follow the label's exact application-number relation to the original "
            "approval date and report the label effective date."
        ),
        "answer": "",
        "cf_answer": "",
        "essential_evidence_ids": [item["evidence_id"] for item in evidence_items],
        "evidence_items": evidence_items,
        "source_bindings": {
            record["record_id"]: {
                "text_sha256": record["text_sha256"],
                "projection_sha256": record["source_projection_sha256"],
                "response_sha256": record["source_response_sha256"],
            }
            for record in records
        },
        "source_records": records,
        "source_manifest": source_manifest,
        "source_manifest_sha256": hashlib.sha256(
            _canonical_bytes(source_manifest)
        ).hexdigest(),
        "source_inventory_binding": {
            "fetch_inventory_sha256": unsigned_manifest["fetch_inventory"]["sha256"],
            "receipt_sha256": hashlib.sha256(_canonical_bytes(receipt)).hexdigest(),
        },
        "source_receipt": receipt,
        "counterfactual_twin": {
            "record_id": label["record_id"],
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_label_effective_date",
            "parent_text_sha256": label["text_sha256"],
            "parent_evidence_quote": effective_fact["evidence_quote"],
            "parent_value": parent_value,
            "value": replacement,
            "state": twin_state,
            "text": twin_text,
            "text_sha256": hashlib.sha256(twin_text.encode()).hexdigest(),
            "evidence_quote": twin_quote,
            "char_start": effective_fact["char_start"],
            "char_end": int(effective_fact["char_start"]) + len(twin_quote),
            "value_char_start": value_start,
            "value_char_end": value_start + len(replacement),
        },
    }
    task["answer"] = replay_clinical_trial_approval_task(task)
    task["cf_answer"] = replay_clinical_trial_approval_task(task, counterfactual=True)
    return task


def audit_clinical_trial_approval_task(
    task: dict[str, Any], *, source_attestation_key: bytes | None = None
) -> dict[str, bool]:
    """Run strict, CF, remove-one, surface and receipt gates."""
    essentials = list(task.get("essential_evidence_ids") or [])
    answer = str(task.get("answer") or "")
    cf_answer = str(task.get("cf_answer") or "")
    strict = replay_clinical_trial_approval_task(task) == answer
    cf_replay = replay_clinical_trial_approval_task(task, counterfactual=True)
    singles = [
        replay_clinical_trial_approval_task(task, evidence_ids=[evidence_id])
        for evidence_id in essentials
    ]
    removals = [
        replay_clinical_trial_approval_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        for removed in essentials
    ]
    evidence_items = task.get("evidence_items") or []
    surface_free = bool(evidence_items) and all(
        _clinical_surface_is_gold_free(str(item.get("surface_text") or ""), answer)
        for item in evidence_items
    )
    records = _task_record_map(task)
    return {
        "strict_replay_sufficient": strict,
        "counterfactual_replay_sufficient": cf_replay == cf_answer,
        "counterfactual_changes_answer": cf_answer != answer,
        "remove_one_fails": bool(removals)
        and all(value == "unknown" for value in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(value == "unknown" for value in singles),
        "essential_surface_gold_free": surface_free,
        "essential_text_grounded": strict,
        "receipt_binding_valid": _task_receipt_binding_valid(task, records),
        "source_attestation_verified": _task_source_attestation_verified(
            task, source_attestation_key
        ),
    }
