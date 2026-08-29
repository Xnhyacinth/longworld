"""Fail-closed ClinicalTrials.gov and openFDA source-inventory contract.

Only privacy-minimized projections of current API responses are retained.  The
raw response digest remains in the retrieval receipt, but this module never
represents the observation as a historical snapshot or a training world.
"""

from __future__ import annotations

import hashlib
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from longworld.core.attestation import (
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    attestation_key_from_env,
    verify_attestation,
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
    return unsigned
