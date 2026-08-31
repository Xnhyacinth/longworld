"""Fail-closed NVD and CISA KEV source-inventory contract.

The APIs expose current records.  This module therefore records observations,
not fabricated historical API snapshots, and deliberately does not integrate
the resulting manifest into world generation.
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

CYBER_FETCH_REQUEST_SCHEMA = "longworld.cyber-fetch-request.v1"
CYBER_FETCH_INVENTORY_SCHEMA = "longworld.cyber-fetch-inventory.v1"
CYBER_WORKFLOW_MANIFEST_SCHEMA = "longworld.cyber-workflow-manifest.v1"
CYBER_KEV_REMEDIATION_TASK_SCHEMA = "longworld.cyber-kev-remediation-task.v1"
NVD_CVE_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
CISA_KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)
NVD_TERMS_URL = "https://nvd.nist.gov/developers/terms-of-use"
NVD_ATTRIBUTION = (
    "This product uses the NVD API but is not endorsed or certified by the NVD."
)
NVD_LICENSE = "NIST public data; NVD API terms of use apply."
CISA_TERMS_URL = "https://www.cisa.gov/about/website-policies"
CISA_ATTRIBUTION = (
    "Cybersecurity and Infrastructure Security Agency Known Exploited "
    "Vulnerabilities Catalog"
)
CISA_LICENSE = "U.S. Government public data; CISA website policies apply."
MAX_CVE_IDS = 32
MAX_CYBER_MANIFEST_BYTES = 16_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CVE_ID = re.compile(r"^CVE-(?:1999|2\d{3})-[1-9]\d{3,}$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_ALLOWED_ACTIONS = {"fetch_cisa_kev", "fetch_nvd_cve"}
_REQUEST_FIELDS = {
    "schema_version",
    "user_agent",
    "authorization",
    "cve_ids",
    "requests_per_second",
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
    "historical_event_claims",
    "generated_at",
    "request_file",
    "request_sha256",
    "authorization",
    "fetch_receipt",
    "n_retrievals",
}


def _strings(value: object, label: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ProvenanceError(f"cyber fetch {label} is invalid")
    result = [str(item).strip() for item in value]
    if any(not item or len(item) > 256 for item in result) or len(result) != len(
        set(result)
    ):
        raise ProvenanceError(f"cyber fetch {label} is invalid")
    return result


def validate_cyber_fetch_request(value: object) -> dict[str, Any]:
    """Validate the exact credential-free public fetch request schema."""
    if (
        not isinstance(value, dict)
        or set(value) != _REQUEST_FIELDS
        or value.get("schema_version") != CYBER_FETCH_REQUEST_SCHEMA
    ):
        raise ProvenanceError("cyber fetch request schema is invalid")
    user_agent = str(value.get("user_agent") or "").strip()
    if len(user_agent) > 256 or _CONTACT_USER_AGENT.fullmatch(user_agent) is None:
        raise ProvenanceError("cyber fetch requires a contact User-Agent")
    authorization = value.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("cyber fetch authorization is invalid")
    normalized_authorization: dict[str, Any] = {
        field: str(authorization.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    }
    if any(not item for item in normalized_authorization.values()):
        raise ProvenanceError("cyber fetch authorization is invalid")
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
        raise ProvenanceError("cyber fetch authorization actions are invalid")
    normalized_authorization["allowed_actions"] = actions
    cve_ids = _strings(value.get("cve_ids"), "CVE identities", MAX_CVE_IDS)
    if any(
        _CVE_ID.fullmatch(cve_id) is None for cve_id in cve_ids
    ) or cve_ids != sorted(cve_ids):
        raise ProvenanceError("cyber fetch CVE identities are invalid")
    rate = value.get("requests_per_second")
    retries = value.get("max_retries")
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= 1.0
    ):
        raise ProvenanceError("cyber fetch requests_per_second is invalid")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("cyber fetch max_retries is invalid")
    return {
        "user_agent": user_agent,
        "authorization": normalized_authorization,
        "cve_ids": cve_ids,
        "requests_per_second": float(rate),
        "max_retries": retries,
    }


def _regular_bytes(base: Path, filename: object, digest: object) -> bytes:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ProvenanceError("cyber retrieval file is unsafe")
    expected = str(digest or "")
    if _SHA256.fullmatch(expected) is None:
        raise ProvenanceError("cyber retrieval hash is invalid")
    try:
        raw = _read_regular_file(base / filename, MAX_SOURCE_BYTES)
    except OSError as error:
        raise ProvenanceError(f"cannot read cyber retrieval: {error}") from error
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ProvenanceError("cyber retrieval hash mismatch")
    return raw


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"{label} response is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ProvenanceError(f"{label} response must be an object")
    return payload


def parse_cyber_source_records(
    cve_ids: list[str], nvd_responses: dict[str, bytes], kev_response: bytes
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Return the unique current NVD/KEV records for every requested CVE."""
    if set(nvd_responses) != set(cve_ids):
        raise ProvenanceError("NVD response identities do not match the request")
    kev_payload = _json_object(kev_response, "CISA KEV")
    vulnerabilities = kev_payload.get("vulnerabilities")
    if (
        not isinstance(vulnerabilities, list)
        or not isinstance(kev_payload.get("count"), int)
        or kev_payload["count"] != len(vulnerabilities)
    ):
        raise ProvenanceError("CISA KEV catalog count is invalid")
    pairs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for cve_id in cve_ids:
        nvd_payload = _json_object(nvd_responses[cve_id], "NVD")
        raw_vulnerabilities = nvd_payload.get("vulnerabilities")
        nvd_matches = [
            item["cve"]
            for item in raw_vulnerabilities or []
            if isinstance(item, dict)
            and isinstance(item.get("cve"), dict)
            and item["cve"].get("id") == cve_id
        ]
        if (
            not isinstance(raw_vulnerabilities, list)
            or nvd_payload.get("totalResults") != 1
            or len(raw_vulnerabilities) != 1
            or len(nvd_matches) != 1
        ):
            raise ProvenanceError("NVD CVE identity is not unique")
        kev_matches = [
            item
            for item in vulnerabilities
            if isinstance(item, dict) and item.get("cveID") == cve_id
        ]
        if len(kev_matches) != 1:
            raise ProvenanceError("CISA KEV CVE identity is not unique")
        nvd_record, kev_record = nvd_matches[0], kev_matches[0]
        if not (
            isinstance(nvd_record.get("published"), str)
            and isinstance(nvd_record.get("lastModified"), str)
            and isinstance(nvd_record.get("vulnStatus"), str)
            and any(
                isinstance(item, dict)
                and item.get("lang") == "en"
                and str(item.get("value") or "").strip()
                for item in nvd_record.get("descriptions") or []
            )
            and isinstance(nvd_record.get("references"), list)
            and nvd_record["references"]
        ):
            raise ProvenanceError("NVD CVE semantic fields are incomplete")
        if not all(
            isinstance(kev_record.get(field), str) and str(kev_record[field]).strip()
            for field in (
                "vendorProject",
                "product",
                "vulnerabilityName",
                "dateAdded",
                "shortDescription",
                "requiredAction",
                "dueDate",
            )
        ):
            raise ProvenanceError("CISA KEV semantic fields are incomplete")
        if (
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(kev_record["dateAdded"])) is None
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(kev_record["dueDate"])) is None
        ):
            raise ProvenanceError("CISA KEV semantic dates are invalid")
        pairs[cve_id] = (nvd_record, kev_record)
    return pairs


def _fact(text: str, *, fact_id: str, field: str, value: str) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    pattern = re.compile(
        rf'^  "{re.escape(field)}"\s*:\s*(?P<value>{re.escape(encoded)}),?$',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ProvenanceError(f"cyber source does not uniquely bind {field}")
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
    cve_id: str,
    kind: str,
    value: dict[str, Any],
    retrieval: dict[str, Any],
    source_raw: bytes,
) -> dict[str, Any]:
    canonical_raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        clean_text, redactions = sanitize_public_text(canonical_raw)
    except ValueError as error:
        raise ProvenanceError("cyber source failed the public scanner") from error
    if kind == "nvd_cve":
        facts = [
            _fact(clean_text, fact_id="cve_id", field="id", value=cve_id),
            _fact(
                clean_text,
                fact_id="published",
                field="published",
                value=str(value["published"]),
            ),
            _fact(
                clean_text,
                fact_id="last_modified",
                field="lastModified",
                value=str(value["lastModified"]),
            ),
            _fact(
                clean_text,
                fact_id="vulnerability_status",
                field="vulnStatus",
                value=str(value["vulnStatus"]),
            ),
        ]
        record_id = f"nvd:{cve_id}"
        license_value = NVD_LICENSE
        attribution = NVD_ATTRIBUTION
        terms_url = NVD_TERMS_URL
    else:
        facts = [
            _fact(clean_text, fact_id="cve_id", field="cveID", value=cve_id),
            _fact(
                clean_text,
                fact_id="date_added",
                field="dateAdded",
                value=str(value["dateAdded"]),
            ),
            _fact(
                clean_text,
                fact_id="due_date",
                field="dueDate",
                value=str(value["dueDate"]),
            ),
            _fact(
                clean_text,
                fact_id="required_action",
                field="requiredAction",
                value=str(value["requiredAction"]),
            ),
        ]
        record_id = f"cisa-kev:{cve_id}"
        license_value = CISA_LICENSE
        attribution = CISA_ATTRIBUTION
        terms_url = CISA_TERMS_URL
    source_sha256 = hashlib.sha256(source_raw).hexdigest()
    return {
        "record_id": record_id,
        "kind": kind,
        "cve_id": cve_id,
        "observed_at": retrieval["observed_at"],
        "temporal_semantics": "current_snapshot_observation",
        "source_family": "nvd" if kind == "nvd_cve" else "cisa_kev",
        "source_origin": "real_public",
        "source_url": retrieval["requested_url"],
        "retrieval_url": retrieval["requested_url"],
        "source_sha256": source_sha256,
        "source_record_sha256": hashlib.sha256(clean_text.encode()).hexdigest(),
        "text_sha256": hashlib.sha256(clean_text.encode()).hexdigest(),
        "provenance_id": f"sha256:{source_sha256}",
        "license": license_value,
        "attribution": attribution,
        "terms_url": terms_url,
        "parser": "longworld-cyber-json@1",
        "text": clean_text,
        "facts": facts,
        "privacy_review": {
            "emails": "redacted",
            "email_redaction_count": len(redactions),
            "secrets": "fail_closed",
            "scanner": PUBLIC_SCANNER,
            "scanner_revision": PUBLIC_SCANNER_REVISION,
        },
    }


def _evidence(record: dict[str, Any]) -> dict[str, Any]:
    fact = next(item for item in record["facts"] if item.get("fact_id") == "cve_id")
    return {
        "record_id": record["record_id"],
        "fact_ids": ["cve_id"],
        "evidence_quote": fact["evidence_quote"],
        "char_start": fact["char_start"],
        "char_end": fact["char_end"],
        "source_sha256": record["source_sha256"],
    }


def _validate_retrieval(
    item: object, base_directory: Path
) -> tuple[dict[str, Any], bytes]:
    required = {
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
    if not isinstance(item, dict) or set(item) != required:
        raise ProvenanceError("cyber retrieval receipt is invalid")
    kind = item.get("kind")
    url = str(item.get("requested_url") or "")
    parsed = urlparse(url)
    valid = False
    if kind == "nvd_cve":
        query = parse_qs(parsed.query, strict_parsing=True)
        valid = (
            parsed.hostname == "services.nvd.nist.gov"
            and parsed.path == "/rest/json/cves/2.0"
            and set(query) == {"cveId"}
            and len(query["cveId"]) == 1
            and _CVE_ID.fullmatch(query["cveId"][0]) is not None
        )
    elif kind == "cisa_kev":
        valid = (
            parsed.hostname == "www.cisa.gov"
            and parsed.path
            == "/sites/default/files/feeds/known_exploited_vulnerabilities.json"
            and not parsed.query
        )
    if (
        parsed.scheme != "https"
        or parsed.netloc != parsed.hostname
        or parsed.fragment
        or parsed.params
        or not valid
        or item.get("final_url") != url
        or item.get("status") != 200
        or item.get("content_type") != "application/json"
        or item.get("redirect_chain") != []
    ):
        raise ProvenanceError("cyber retrieval URL or transport metadata is invalid")
    _parse_timestamp(str(item.get("observed_at") or ""), "cyber observed_at")
    return dict(item), _regular_bytes(
        base_directory, item.get("retrieval_file"), item.get("sha256")
    )


def _source_policies() -> dict[str, dict[str, str]]:
    return {
        "cisa_kev": {
            "attribution": CISA_ATTRIBUTION,
            "license": CISA_LICENSE,
            "terms_url": CISA_TERMS_URL,
        },
        "nvd": {
            "attribution": NVD_ATTRIBUTION,
            "license": NVD_LICENSE,
            "terms_url": NVD_TERMS_URL,
        },
    }


def build_cyber_workflow_from_fetch_inventory(
    payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    fetch_inventory_sha256: str,
) -> dict[str, Any]:
    """Validate official bytes and derive a disabled current-snapshot graph."""
    if set(payload) != _INVENTORY_FIELDS or not (
        payload.get("schema_version") == CYBER_FETCH_INVENTORY_SCHEMA
        and payload.get("source_status") == "public_api_export"
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("snapshot_semantics") == "current_state_observed_at_fetch"
        and payload.get("historical_event_claims") is False
    ):
        raise ProvenanceError("cyber fetch inventory schema or flags are invalid")
    if _SHA256.fullmatch(fetch_inventory_sha256) is None:
        raise ProvenanceError("cyber fetch inventory hash is invalid")
    fetch_started = _parse_timestamp(
        str(payload.get("generated_at") or ""), "cyber fetch generated_at"
    )
    export_time = _parse_timestamp(generated_at, "generated_at")
    request_raw = _regular_bytes(
        base_directory, payload.get("request_file"), payload.get("request_sha256")
    )
    try:
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("cyber fetch request is invalid") from error
    request = validate_cyber_fetch_request(request_payload)
    if request["authorization"] != payload.get("authorization"):
        raise ProvenanceError("cyber authorization is not request-bound")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "requests_per_second",
        "max_retries",
        "allowed_actions",
        "request_file",
        "request_sha256",
        "user_agent_sha256",
        "source_policies",
        "retrievals",
    }:
        raise ProvenanceError("cyber fetch receipt is invalid")
    if not (
        receipt.get("request_file") == payload.get("request_file")
        and receipt.get("request_sha256") == payload.get("request_sha256")
        and receipt.get("started_at") == payload.get("generated_at")
        and receipt.get("requests_per_second") == request["requests_per_second"]
        and receipt.get("max_retries") == request["max_retries"]
        and receipt.get("allowed_actions")
        == request["authorization"]["allowed_actions"]
        and receipt.get("source_policies") == _source_policies()
        and receipt.get("user_agent_sha256")
        == hashlib.sha256(request["user_agent"].encode()).hexdigest()
    ):
        raise ProvenanceError("cyber request and receipt bindings are invalid")
    raw_retrievals = receipt.get("retrievals")
    if (
        not isinstance(raw_retrievals, list)
        or len(raw_retrievals) != payload.get("n_retrievals")
        or len(raw_retrievals) != len(request["cve_ids"]) + 1
    ):
        raise ProvenanceError("cyber retrieval count is invalid")
    retrievals = [_validate_retrieval(item, base_directory) for item in raw_retrievals]
    completed_time = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "cyber fetch completed_at"
    )
    observed_times = [
        _parse_timestamp(item[0]["observed_at"], "cyber observed_at")
        for item in retrievals
    ]
    if not (
        fetch_started < observed_times[0]
        and all(prior < current for prior, current in pairwise(observed_times))
        and observed_times[-1] < completed_time <= export_time
    ):
        raise ProvenanceError("cyber retrieval observation timeline is invalid")
    expected_urls = {
        *(f"{NVD_CVE_API_URL}?cveId={cve_id}" for cve_id in request["cve_ids"]),
        CISA_KEV_URL,
    }
    if {item[0]["requested_url"] for item in retrievals} != expected_urls:
        raise ProvenanceError("cyber retrievals do not exactly match the request")
    nvd_by_id: dict[str, bytes] = {}
    nvd_receipts: dict[str, dict[str, Any]] = {}
    kev_raw: bytes | None = None
    kev_receipt: dict[str, Any] | None = None
    for item, raw in retrievals:
        if item["kind"] == "nvd_cve":
            cve_id = parse_qs(urlparse(item["requested_url"]).query)["cveId"][0]
            nvd_by_id[cve_id] = raw
            nvd_receipts[cve_id] = item
        else:
            if kev_raw is not None:
                raise ProvenanceError("CISA KEV retrieval is duplicated")
            kev_raw, kev_receipt = raw, item
    if kev_raw is None or kev_receipt is None:
        raise ProvenanceError("CISA KEV retrieval is missing")
    pairs = parse_cyber_source_records(request["cve_ids"], nvd_by_id, kev_raw)
    records: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for cve_id in request["cve_ids"]:
        nvd_value, kev_value = pairs[cve_id]
        nvd_record = _record(
            cve_id=cve_id,
            kind="nvd_cve",
            value=nvd_value,
            retrieval=nvd_receipts[cve_id],
            source_raw=nvd_by_id[cve_id],
        )
        kev_record = _record(
            cve_id=cve_id,
            kind="cisa_kev_entry",
            value=kev_value,
            retrieval=kev_receipt,
            source_raw=kev_raw,
        )
        records.extend((nvd_record, kev_record))
        relations.append(
            {
                "relation_id": f"cyber:listed-in-kev:{cve_id}",
                "kind": "listed_in_kev",
                "source_record_id": kev_record["record_id"],
                "target_record_id": nvd_record["record_id"],
                "evidence": [_evidence(kev_record), _evidence(nvd_record)],
            }
        )
    manifest = {
        "schema_version": CYBER_WORKFLOW_MANIFEST_SCHEMA,
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "semantic_facts_train_ready": False,
        "snapshot_semantics": "current_state_observed_at_fetch",
        "historical_event_claims": False,
        "generated_at": generated_at,
        "authorization": payload["authorization"],
        "fetch_inventory_sha256": fetch_inventory_sha256,
        "fetch_receipt": receipt,
        "n": len(records),
        "n_relations": len(relations),
        "records": sorted(records, key=lambda item: item["record_id"]),
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
    }
    audit_cyber_workflow_manifest(manifest)
    if (
        len(json.dumps(manifest, ensure_ascii=False).encode())
        > MAX_CYBER_MANIFEST_BYTES
    ):
        raise ProvenanceError("cyber workflow manifest exceeds size limit")
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
        raise ProvenanceError("cyber fact schema is invalid")
    text = record["text"]
    start, end, quote = fact["char_start"], fact["char_end"], fact["evidence_quote"]
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
        or start < 0
        or end != start + len(quote)
        or text[start:end] != quote
    ):
        raise ProvenanceError("cyber fact evidence binding is invalid")
    parsed = json.loads(text)
    if parsed.get(fact["field"]) != fact["value"]:
        raise ProvenanceError("cyber fact field binding is invalid")


def audit_cyber_workflow_manifest(payload: dict[str, Any]) -> None:
    """Recheck disabled flags, identities, facts, relations, and clean text."""
    required = {
        "schema_version",
        "source_status",
        "data_stage",
        "hybrid_train_ready",
        "production_eligible",
        "generation_integration",
        "semantic_facts_train_ready",
        "snapshot_semantics",
        "historical_event_claims",
        "generated_at",
        "authorization",
        "fetch_inventory_sha256",
        "fetch_receipt",
        "n",
        "n_relations",
        "records",
        "relations",
    }
    observed_fields = set(payload) - {"attestation", "local_probe_trust_isolation"}
    if observed_fields != required or not (
        payload.get("schema_version") == CYBER_WORKFLOW_MANIFEST_SCHEMA
        and payload.get("source_status") == "public_api_export"
        and payload.get("data_stage") == "source_inventory"
        and payload.get("hybrid_train_ready") is False
        and payload.get("production_eligible") is False
        and payload.get("generation_integration") == "disabled"
        and payload.get("semantic_facts_train_ready") is False
        and payload.get("snapshot_semantics") == "current_state_observed_at_fetch"
        and payload.get("historical_event_claims") is False
    ):
        raise ProvenanceError("cyber manifest schema or disabled flags are invalid")
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    if _SHA256.fullmatch(str(payload.get("fetch_inventory_sha256") or "")) is None:
        raise ProvenanceError("cyber manifest fetch inventory hash is invalid")
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "started_at",
        "completed_at",
        "requests_per_second",
        "max_retries",
        "allowed_actions",
        "request_file",
        "request_sha256",
        "user_agent_sha256",
        "source_policies",
        "retrievals",
    }:
        raise ProvenanceError("cyber manifest fetch receipt lineage is invalid")
    raw_retrievals = receipt.get("retrievals")
    if not isinstance(raw_retrievals, list) or not raw_retrievals:
        raise ProvenanceError("cyber manifest fetch receipt lineage is invalid")
    retrievals: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw_retrievals:
        if not isinstance(item, dict) or set(item) != {
            "kind",
            "requested_url",
            "final_url",
            "status",
            "content_type",
            "redirect_chain",
            "observed_at",
            "sha256",
            "retrieval_file",
        }:
            raise ProvenanceError("cyber manifest fetch receipt lineage is invalid")
        kind = str(item.get("kind") or "")
        requested_url = str(item.get("requested_url") or "")
        key = (kind, requested_url)
        if (
            kind not in {"nvd_cve", "cisa_kev"}
            or not requested_url
            or key in retrievals
            or item.get("final_url") != requested_url
            or item.get("status") != 200
            or item.get("content_type") != "application/json"
            or item.get("redirect_chain") != []
            or _SHA256.fullmatch(str(item.get("sha256") or "")) is None
        ):
            raise ProvenanceError("cyber manifest fetch receipt lineage is invalid")
        _parse_timestamp(str(item.get("observed_at") or ""), "retrieval observed_at")
        retrievals[key] = item
    records = payload.get("records")
    relations = payload.get("relations")
    if (
        not isinstance(records, list)
        or not records
        or len(records) != payload.get("n")
        or not isinstance(relations, list)
        or len(relations) != payload.get("n_relations")
        or len(records) != 2 * len(relations)
    ):
        raise ProvenanceError("cyber manifest counts are invalid")
    record_map: dict[str, dict[str, Any]] = {}
    used_retrievals: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ProvenanceError("cyber record is invalid")
        if set(record) != {
            "record_id",
            "kind",
            "cve_id",
            "observed_at",
            "temporal_semantics",
            "source_family",
            "source_origin",
            "source_url",
            "retrieval_url",
            "source_sha256",
            "source_record_sha256",
            "text_sha256",
            "provenance_id",
            "license",
            "attribution",
            "terms_url",
            "parser",
            "text",
            "facts",
            "privacy_review",
        }:
            raise ProvenanceError("cyber record schema is invalid")
        record_kind = record.get("kind")
        cve_id = str(record.get("cve_id") or "")
        expected_id = (
            f"nvd:{cve_id}" if record_kind == "nvd_cve" else f"cisa-kev:{cve_id}"
        )
        if (
            record_kind not in {"nvd_cve", "cisa_kev_entry"}
            or _CVE_ID.fullmatch(cve_id) is None
            or record.get("record_id") != expected_id
        ):
            raise ProvenanceError("cyber record identity is invalid")
        if expected_id in record_map:
            raise ProvenanceError("cyber record identity is duplicated")
        record_map[expected_id] = record
        text = record.get("text")
        if (
            not isinstance(text, str)
            or not text
            or record.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest()
            or record.get("temporal_semantics") != "current_snapshot_observation"
            or "occurred_at" in record
            or record.get("source_origin") != "real_public"
            or record.get("provenance_id") != f"sha256:{record.get('source_sha256')}"
            or _SHA256.fullmatch(str(record.get("source_sha256") or "")) is None
        ):
            raise ProvenanceError("cyber record body or provenance is invalid")
        _parse_timestamp(str(record.get("observed_at") or ""), "record observed_at")
        expected_url = (
            f"{NVD_CVE_API_URL}?cveId={cve_id}"
            if record_kind == "nvd_cve"
            else CISA_KEV_URL
        )
        retrieval_key = (
            "nvd_cve" if record_kind == "nvd_cve" else "cisa_kev",
            expected_url,
        )
        retrieval = retrievals.get(retrieval_key)
        if (
            retrieval is None
            or record.get("source_url") != expected_url
            or record.get("retrieval_url") != expected_url
            or record.get("source_sha256") != retrieval.get("sha256")
            or record.get("observed_at") != retrieval.get("observed_at")
        ):
            raise ProvenanceError("cyber manifest fetch receipt lineage is invalid")
        used_retrievals.add(retrieval_key)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProvenanceError("cyber record text is not JSON") from error
        identity_field = "id" if record_kind == "nvd_cve" else "cveID"
        if parsed.get(identity_field) != cve_id:
            raise ProvenanceError("cyber record text identity is invalid")
        if (
            record.get("source_record_sha256")
            != hashlib.sha256(text.encode()).hexdigest()
        ):
            raise ProvenanceError("cyber record body or provenance is invalid")
        expected_policy = _source_policies()[
            "nvd" if record_kind == "nvd_cve" else "cisa_kev"
        ]
        if any(
            record.get(field) != expected_policy[field] for field in expected_policy
        ):
            raise ProvenanceError("cyber record license or attribution is invalid")
        facts = record.get("facts")
        required_fact_ids = (
            {"cve_id", "published", "last_modified", "vulnerability_status"}
            if record_kind == "nvd_cve"
            else {"cve_id", "date_added", "due_date", "required_action"}
        )
        if (
            not isinstance(facts, list)
            or {item.get("fact_id") for item in facts if isinstance(item, dict)}
            != required_fact_ids
        ):
            raise ProvenanceError("cyber record facts are incomplete")
        for fact in facts:
            _validate_fact(record, fact)
    expected_relations = {
        cve_id
        for cve_id in (record["cve_id"] for record in records)
        if f"nvd:{cve_id}" in record_map and f"cisa-kev:{cve_id}" in record_map
    }
    observed_relations: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            raise ProvenanceError("cyber relation is invalid")
        source = record_map.get(str(relation.get("source_record_id") or ""))
        target = record_map.get(str(relation.get("target_record_id") or ""))
        if (
            relation.get("kind") != "listed_in_kev"
            or source is None
            or target is None
            or source["kind"] != "cisa_kev_entry"
            or target["kind"] != "nvd_cve"
            or source["cve_id"] != target["cve_id"]
            or relation.get("relation_id") != f"cyber:listed-in-kev:{source['cve_id']}"
        ):
            raise ProvenanceError("cyber CVE/KEV relation identity is invalid")
        cve_id = source["cve_id"]
        if cve_id in observed_relations:
            raise ProvenanceError("cyber CVE/KEV relation is duplicated")
        observed_relations.add(cve_id)
        evidence = relation.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != 2:
            raise ProvenanceError("cyber relation evidence is invalid")
        for item in evidence:
            if not isinstance(item, dict):
                raise ProvenanceError("cyber relation evidence is invalid")
            record = record_map.get(str(item.get("record_id") or ""))
            if record is not source and record is not target:
                raise ProvenanceError("cyber relation evidence endpoint is invalid")
            fact = next(
                (
                    candidate
                    for candidate in record["facts"]
                    if candidate["fact_id"] == "cve_id"
                ),
                None,
            )
            if fact is None or item != {
                "record_id": record["record_id"],
                "fact_ids": ["cve_id"],
                "evidence_quote": fact["evidence_quote"],
                "char_start": fact["char_start"],
                "char_end": fact["char_end"],
                "source_sha256": record["source_sha256"],
            }:
                raise ProvenanceError("cyber relation evidence binding is invalid")
    if observed_relations != expected_relations:
        raise ProvenanceError("cyber CVE/KEV relation coverage is invalid")
    if used_retrievals != set(retrievals):
        raise ProvenanceError("cyber manifest fetch receipt lineage is invalid")
    try:
        validate_sanitized_public_payload(payload)
    except ValueError as error:
        raise ProvenanceError("cyber manifest failed the public scanner") from error


def load_cyber_workflow_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Load and semantically audit a source-attested cyber inventory."""
    try:
        raw = _read_regular_file(path, MAX_CYBER_MANIFEST_BYTES)
    except OSError as error:
        raise ProvenanceError(
            f"cannot read cyber workflow manifest: {error}"
        ) from error
    return load_cyber_workflow_manifest_bytes(raw, attestation_key=attestation_key)


def load_cyber_workflow_manifest_bytes(
    raw: bytes, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Audit the exact manifest bytes whose digest is bound downstream."""
    if len(raw) > MAX_CYBER_MANIFEST_BYTES:
        raise ProvenanceError("cyber workflow manifest exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read cyber workflow manifest: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ProvenanceError("cyber workflow manifest must be an object")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError("cyber workflow manifest has no valid attestation")
    audit_cyber_workflow_manifest(payload)
    return payload


_CYBER_KEV_QUESTION = (
    "For the CVE joined across the NVD record and the CISA KEV catalog, return "
    "the NVD vulnerability status, the KEV date added, remediation due date, "
    "and the exact required action."
)
_CYBER_KEV_ESSENTIALS = {
    "date_added",
    "due_date",
    "listed_in_kev_relation",
    "required_action",
    "vulnerability_status",
}
_CYBER_TASK_FIELDS = {
    "schema_version",
    "data_stage",
    "train_ready",
    "production_eligible",
    "promotion_eligible",
    "complete_world",
    "promoted",
    "generation_integration",
    "source_attestation_verified",
    "query_type",
    "answer_program_id",
    "question",
    "answer",
    "cf_answer",
    "essential_evidence_ids",
    "evidence_items",
    "source_bindings",
    "source_records",
    "source_receipt",
    "source_manifest_binding",
    "counterfactual_twin",
}
_CYBER_RECORD_FIELDS = {
    "record_id",
    "kind",
    "cve_id",
    "observed_at",
    "temporal_semantics",
    "source_family",
    "source_origin",
    "source_url",
    "retrieval_url",
    "source_sha256",
    "source_record_sha256",
    "text_sha256",
    "provenance_id",
    "license",
    "attribution",
    "terms_url",
    "parser",
    "text",
    "facts",
    "privacy_review",
}
_CYBER_BINDING_FIELDS = {
    "text_sha256",
    "source_record_sha256",
    "source_sha256",
    "source_url",
    "retrieval_url",
    "observed_at",
}
_CYBER_RECEIPT_FIELDS = {
    "started_at",
    "completed_at",
    "requests_per_second",
    "max_retries",
    "allowed_actions",
    "request_file",
    "request_sha256",
    "user_agent_sha256",
    "source_policies",
    "retrievals",
}
_CYBER_RETRIEVAL_FIELDS = {
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
_CYBER_SOURCE_MANIFEST_BINDING_FIELDS = {
    "source_manifest_sha256",
    "manifest_metadata",
    "attestation",
}


def _cyber_manifest_digest(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _cyber_task_contract(task: dict[str, Any]) -> None:
    source_binding = task.get("source_manifest_binding")
    manifest_metadata = (
        source_binding.get("manifest_metadata")
        if isinstance(source_binding, dict)
        else None
    )
    attestation = (
        source_binding.get("attestation") if isinstance(source_binding, dict) else None
    )
    if set(task) != _CYBER_TASK_FIELDS or not (
        task.get("schema_version") == CYBER_KEV_REMEDIATION_TASK_SCHEMA
        and task.get("data_stage") == "candidate_task"
        and task.get("train_ready") is False
        and task.get("production_eligible") is False
        and task.get("promotion_eligible") is False
        and task.get("complete_world") is False
        and task.get("promoted") is False
        and task.get("generation_integration") == "disabled"
        and task.get("source_attestation_verified") is False
        and task.get("query_type") == "kev_remediation_window"
        and task.get("answer_program_id") == "cyber.kev_remediation_window.v1"
        and task.get("question") == _CYBER_KEV_QUESTION
        and isinstance(task.get("answer"), str)
        and isinstance(task.get("cf_answer"), str)
        and isinstance(source_binding, dict)
        and set(source_binding) == _CYBER_SOURCE_MANIFEST_BINDING_FIELDS
        and _SHA256.fullmatch(str(source_binding.get("source_manifest_sha256") or ""))
        is not None
        and isinstance(manifest_metadata, dict)
        and not {
            "records",
            "relations",
            "fetch_receipt",
            "attestation",
        }.intersection(manifest_metadata)
        and (attestation is None or isinstance(attestation, dict))
    ):
        raise ProvenanceError("cyber KEV task contract is invalid")


def _cyber_task_bound_manifest(task: dict[str, Any]) -> dict[str, Any] | None:
    binding = task.get("source_manifest_binding")
    items = task.get("evidence_items")
    if not isinstance(binding, dict) or not isinstance(items, list):
        return None
    metadata = binding.get("manifest_metadata")
    relation_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("kind") == "source_relation"
    ]
    if not isinstance(metadata, dict) or len(relation_items) != 1:
        return None
    manifest = {
        **metadata,
        "fetch_receipt": task.get("source_receipt"),
        "records": task.get("source_records"),
        "relations": [relation_items[0].get("relation")],
    }
    attestation = binding.get("attestation")
    if attestation is not None:
        manifest["attestation"] = attestation
    if binding.get("source_manifest_sha256") != _cyber_manifest_digest(manifest):
        return None
    return manifest


def _cyber_task_source_attestation_verified(
    task: dict[str, Any], source_attestation_key: bytes | None
) -> bool:
    if source_attestation_key is None:
        return False
    signed = _cyber_task_bound_manifest(task)
    attestation = signed.get("attestation") if isinstance(signed, dict) else None
    if not isinstance(signed, dict) or not isinstance(attestation, dict):
        return False
    if attestation.get("scheme") == ATTESTATION_V2_SCHEME:
        signature_valid = verify_attestation_identity(
            signed,
            source_attestation_key,
            purpose="source_manifest",
            role="source",
            key_id=str(attestation.get("key_id") or ""),
            environment=str(attestation.get("environment") or ""),
        )
    else:
        signature_valid = verify_attestation(
            signed, source_attestation_key, purpose="source_manifest"
        )
    if not signature_valid:
        return False
    try:
        audit_cyber_workflow_manifest(signed)
    except ProvenanceError:
        return False
    return True


def _cyber_task_record_map(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_records = task.get("source_records")
    bindings = task.get("source_bindings")
    if (
        not isinstance(raw_records, list)
        or len(raw_records) != 2
        or not isinstance(bindings, dict)
        or len(bindings) != 2
    ):
        raise ProvenanceError("cyber task source binding is invalid")
    records: dict[str, dict[str, Any]] = {}
    for record in raw_records:
        if not isinstance(record, dict) or set(record) != _CYBER_RECORD_FIELDS:
            raise ProvenanceError("cyber task source binding is invalid")
        record_id = str(record.get("record_id") or "")
        kind = record.get("kind")
        cve_id = str(record.get("cve_id") or "")
        expected_record_id = (
            f"nvd:{cve_id}" if kind == "nvd_cve" else f"cisa-kev:{cve_id}"
        )
        expected_url = (
            f"{NVD_CVE_API_URL}?cveId={cve_id}" if kind == "nvd_cve" else CISA_KEV_URL
        )
        expected_family = "nvd" if kind == "nvd_cve" else "cisa_kev"
        text = record.get("text")
        digest = hashlib.sha256(str(text).encode()).hexdigest()
        binding = bindings.get(record_id)
        if (
            kind not in {"nvd_cve", "cisa_kev_entry"}
            or _CVE_ID.fullmatch(cve_id) is None
            or record_id != expected_record_id
            or record_id in records
            or not isinstance(text, str)
            or not text
            or record.get("text_sha256") != digest
            or record.get("source_record_sha256") != digest
            or record.get("source_origin") != "real_public"
            or record.get("temporal_semantics") != "current_snapshot_observation"
            or record.get("source_family") != expected_family
            or record.get("source_url") != expected_url
            or record.get("retrieval_url") != expected_url
            or _SHA256.fullmatch(str(record.get("source_sha256") or "")) is None
            or record.get("provenance_id") != f"sha256:{record.get('source_sha256')}"
            or not isinstance(binding, dict)
            or set(binding) != _CYBER_BINDING_FIELDS
            or any(binding[field] != record[field] for field in _CYBER_BINDING_FIELDS)
        ):
            raise ProvenanceError("cyber task source binding is invalid")
        _parse_timestamp(str(record.get("observed_at") or ""), "record observed_at")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProvenanceError("cyber task source state is invalid") from error
        identity_field = "id" if kind == "nvd_cve" else "cveID"
        if not isinstance(parsed, dict) or parsed.get(identity_field) != cve_id:
            raise ProvenanceError("cyber task source state identity is invalid")
        required_fact_ids = (
            {"cve_id", "published", "last_modified", "vulnerability_status"}
            if kind == "nvd_cve"
            else {"cve_id", "date_added", "due_date", "required_action"}
        )
        facts = record.get("facts")
        if (
            not isinstance(facts, list)
            or len(facts) != len(required_fact_ids)
            or {
                str(fact.get("fact_id") or "")
                for fact in facts
                if isinstance(fact, dict)
            }
            != required_fact_ids
        ):
            raise ProvenanceError("cyber task source state is invalid")
        for fact in facts:
            _validate_fact(record, fact)
        records[record_id] = record
    if len({record["cve_id"] for record in records.values()}) != 1:
        raise ProvenanceError("cyber task source state identity is invalid")
    _cyber_task_receipt(task, records)
    return records


def _cyber_task_receipt(
    task: dict[str, Any], records: dict[str, dict[str, Any]]
) -> None:
    receipt = task.get("source_receipt")
    if not isinstance(receipt, dict) or set(receipt) != _CYBER_RECEIPT_FIELDS:
        raise ProvenanceError("cyber task receipt binding is invalid")
    rate = receipt.get("requests_per_second")
    retries = receipt.get("max_retries")
    if not (
        isinstance(rate, (int, float))
        and not isinstance(rate, bool)
        and 0 < float(rate) <= 1.0
        and isinstance(retries, int)
        and not isinstance(retries, bool)
        and 0 <= retries <= 8
        and receipt.get("allowed_actions") == sorted(_ALLOWED_ACTIONS)
        and receipt.get("source_policies") == _source_policies()
        and isinstance(receipt.get("request_file"), str)
        and Path(str(receipt["request_file"])).name == receipt["request_file"]
        and _SHA256.fullmatch(str(receipt.get("request_sha256") or "")) is not None
        and _SHA256.fullmatch(str(receipt.get("user_agent_sha256") or "")) is not None
    ):
        raise ProvenanceError("cyber task receipt binding is invalid")
    _parse_timestamp(str(receipt.get("started_at") or ""), "receipt started_at")
    _parse_timestamp(str(receipt.get("completed_at") or ""), "receipt completed_at")
    raw_retrievals = receipt.get("retrievals")
    if not isinstance(raw_retrievals, list) or len(raw_retrievals) != len(records):
        raise ProvenanceError("cyber task receipt binding is invalid")
    retrievals: dict[tuple[str, str], dict[str, Any]] = {}
    for retrieval in raw_retrievals:
        if not isinstance(retrieval, dict) or set(retrieval) != _CYBER_RETRIEVAL_FIELDS:
            raise ProvenanceError("cyber task receipt binding is invalid")
        key = (
            str(retrieval.get("kind") or ""),
            str(retrieval.get("requested_url") or ""),
        )
        if (
            key in retrievals
            or retrieval.get("final_url") != key[1]
            or retrieval.get("status") != 200
            or retrieval.get("content_type") != "application/json"
            or retrieval.get("redirect_chain") != []
            or _SHA256.fullmatch(str(retrieval.get("sha256") or "")) is None
            or not isinstance(retrieval.get("retrieval_file"), str)
            or Path(str(retrieval["retrieval_file"])).name
            != retrieval["retrieval_file"]
        ):
            raise ProvenanceError("cyber task receipt binding is invalid")
        _parse_timestamp(
            str(retrieval.get("observed_at") or ""), "retrieval observed_at"
        )
        retrievals[key] = retrieval
    for record in records.values():
        kind = "nvd_cve" if record["kind"] == "nvd_cve" else "cisa_kev"
        expected_url = (
            f"{NVD_CVE_API_URL}?cveId={record['cve_id']}"
            if kind == "nvd_cve"
            else CISA_KEV_URL
        )
        retrieval = retrievals.get((kind, expected_url))
        if (
            retrieval is None
            or record["source_url"] != expected_url
            or record["retrieval_url"] != expected_url
            or retrieval["sha256"] != record["source_sha256"]
            or retrieval["observed_at"] != record["observed_at"]
        ):
            raise ProvenanceError("cyber task receipt binding is invalid")


def _cyber_task_fact(item: dict[str, Any], records: dict[str, dict[str, Any]]) -> str:
    if set(item) != {
        "evidence_id",
        "kind",
        "record_id",
        "fact_id",
        "field",
        "value",
        "evidence_quote",
        "surface_text",
        "char_start",
        "char_end",
    }:
        raise ProvenanceError("cyber task fact evidence is invalid")
    evidence_id = str(item.get("evidence_id") or "")
    expected = {
        "vulnerability_status": ("nvd_cve", "vulnerability_status", "vulnStatus"),
        "date_added": ("cisa_kev_entry", "date_added", "dateAdded"),
        "due_date": ("cisa_kev_entry", "due_date", "dueDate"),
        "required_action": (
            "cisa_kev_entry",
            "required_action",
            "requiredAction",
        ),
    }.get(evidence_id)
    record = records.get(str(item.get("record_id") or ""))
    if expected is None or record is None or record.get("kind") != expected[0]:
        raise ProvenanceError("cyber task fact state is invalid")
    matching_facts = [
        fact
        for fact in record["facts"]
        if isinstance(fact, dict) and fact.get("fact_id") == expected[1]
    ]
    if len(matching_facts) != 1:
        raise ProvenanceError("cyber task fact state is invalid")
    fact = matching_facts[0]
    _validate_fact(record, fact)
    if item != {
        "evidence_id": evidence_id,
        "kind": "source_fact",
        "record_id": record["record_id"],
        "fact_id": expected[1],
        "field": expected[2],
        "value": fact["value"],
        "evidence_quote": fact["evidence_quote"],
        "surface_text": fact["evidence_quote"],
        "char_start": fact["char_start"],
        "char_end": fact["char_end"],
    }:
        raise ProvenanceError("cyber task fact evidence byte binding is invalid")
    return str(fact["value"])


def _cyber_task_relation(
    item: dict[str, Any], records: dict[str, dict[str, Any]]
) -> str:
    if set(item) != {"evidence_id", "kind", "surface_text", "relation"} or not (
        item.get("evidence_id") == "listed_in_kev_relation"
        and item.get("kind") == "source_relation"
    ):
        raise ProvenanceError("cyber task relation evidence is invalid")
    relation = item.get("relation")
    if not isinstance(relation, dict) or set(relation) != {
        "relation_id",
        "kind",
        "source_record_id",
        "target_record_id",
        "evidence",
    }:
        raise ProvenanceError("cyber task relation evidence is invalid")
    source = records.get(str(relation.get("source_record_id") or ""))
    target = records.get(str(relation.get("target_record_id") or ""))
    if (
        source is None
        or target is None
        or source["kind"] != "cisa_kev_entry"
        or target["kind"] != "nvd_cve"
        or source["cve_id"] != target["cve_id"]
        or relation.get("kind") != "listed_in_kev"
        or relation.get("relation_id") != f"cyber:listed-in-kev:{source['cve_id']}"
    ):
        raise ProvenanceError("cyber task relation state is invalid")
    evidence = relation.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != 2:
        raise ProvenanceError("cyber task relation evidence is invalid")
    expected_evidence: list[dict[str, Any]] = []
    for record in (source, target):
        cve_facts = [
            fact
            for fact in record["facts"]
            if isinstance(fact, dict) and fact.get("fact_id") == "cve_id"
        ]
        if len(cve_facts) != 1:
            raise ProvenanceError("cyber task relation evidence is invalid")
        fact = cve_facts[0]
        _validate_fact(record, fact)
        expected_evidence.append(
            {
                "record_id": record["record_id"],
                "fact_ids": ["cve_id"],
                "evidence_quote": fact["evidence_quote"],
                "char_start": fact["char_start"],
                "char_end": fact["char_end"],
                "source_sha256": record["source_sha256"],
            }
        )
    if evidence != expected_evidence or item.get("surface_text") != "\n".join(
        str(evidence_item["evidence_quote"]) for evidence_item in expected_evidence
    ):
        raise ProvenanceError("cyber task relation evidence byte binding is invalid")
    return str(source["cve_id"])


def _cyber_remediation_answer(
    *,
    cve_id: str,
    vulnerability_status: str,
    date_added: str,
    due_date: str,
    required_action: str,
) -> str:
    if not all(
        (cve_id, vulnerability_status, date_added, due_date, required_action)
    ) or not (
        _CVE_ID.fullmatch(cve_id)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_added)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date)
        and date_added <= due_date
    ):
        return "unknown"
    return (
        f"{cve_id} | NVD status: {vulnerability_status} | "
        f"KEV added: {date_added} | due: {due_date} | "
        f"required action: {required_action}"
    )


def _cyber_counterfactual_due_date(
    task: dict[str, Any],
    items: dict[str, dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> str:
    due_item = items.get("due_date")
    twin = task.get("counterfactual_twin")
    if due_item is None or not isinstance(twin, dict):
        raise ProvenanceError("cyber counterfactual replacement is invalid")
    parent_record = records.get(str(due_item.get("record_id") or ""))
    parent_value = str(due_item.get("value") or "")
    replacement = str(twin.get("value") or "")
    quote = str(due_item.get("evidence_quote") or "")
    start = due_item.get("char_start")
    end = due_item.get("char_end")
    encoded_parent = json.dumps(parent_value, ensure_ascii=False)
    encoded_replacement = json.dumps(replacement, ensure_ascii=False)
    expected_quote = ""
    expected_text = ""
    expected_end: int | None = None
    if (
        parent_record is not None
        and type(start) is int
        and type(end) is int
        and quote.count(encoded_parent) == 1
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", replacement)
    ):
        expected_quote = quote.replace(encoded_parent, encoded_replacement, 1)
        expected_text = (
            str(parent_record["text"])[:start]
            + expected_quote
            + str(parent_record["text"])[end:]
        )
        expected_end = start + len(expected_quote)
    text = twin.get("text")
    if set(twin) != {
        "record_id",
        "source_origin",
        "provenance_operation",
        "parent_text_sha256",
        "parent_evidence_quote",
        "parent_char_start",
        "parent_char_end",
        "parent_value",
        "text",
        "text_sha256",
        "evidence_quote",
        "value",
        "char_start",
        "char_end",
    } or not (
        parent_record is not None
        and replacement != parent_value
        and twin.get("record_id") == parent_record["record_id"]
        and twin.get("source_origin") == "synthetic_counterfactual"
        and twin.get("provenance_operation") == "replace_kev_due_date"
        and twin.get("parent_text_sha256") == parent_record["text_sha256"]
        and twin.get("parent_evidence_quote") == quote
        and twin.get("parent_char_start") == start
        and twin.get("parent_char_end") == end
        and twin.get("parent_value") == parent_value
        and isinstance(text, str)
        and twin.get("text_sha256") == hashlib.sha256(text.encode()).hexdigest()
        and text == expected_text
        and twin.get("evidence_quote") == expected_quote
        and twin.get("char_start") == start
        and twin.get("char_end") == expected_end
    ):
        raise ProvenanceError("cyber counterfactual replacement is invalid")
    return replacement


def replay_cyber_kev_remediation_task(
    task: dict[str, Any],
    *,
    evidence_ids: list[str] | None = None,
    counterfactual: bool = False,
) -> str:
    """Replay the NVD-to-KEV answer program from byte-bound source state."""
    _cyber_task_contract(task)
    records = _cyber_task_record_map(task)
    raw_items = task.get("evidence_items")
    essentials = task.get("essential_evidence_ids")
    if not isinstance(raw_items, list) or not isinstance(essentials, list):
        raise ProvenanceError("cyber task essential evidence contract is invalid")
    items = {
        str(item.get("evidence_id") or ""): item
        for item in raw_items
        if isinstance(item, dict)
    }
    if (
        len(raw_items) != len(items)
        or any(not isinstance(item, str) for item in essentials)
        or len(essentials) != len(set(essentials))
        or set(essentials) != _CYBER_KEV_ESSENTIALS
        or set(items) != _CYBER_KEV_ESSENTIALS
    ):
        raise ProvenanceError("cyber task essential evidence contract is invalid")
    selected_values = essentials if evidence_ids is None else evidence_ids
    if not isinstance(selected_values, list) or any(
        not isinstance(item, str) for item in selected_values
    ):
        raise ProvenanceError("cyber task evidence selection is invalid")
    if len(selected_values) != len(set(selected_values)) or not set(
        selected_values
    ).issubset(items):
        raise ProvenanceError("cyber task evidence selection is invalid")
    cve_id = ""
    values = {
        "vulnerability_status": "",
        "date_added": "",
        "due_date": "",
        "required_action": "",
    }
    for evidence_id in selected_values:
        item = items[evidence_id]
        if evidence_id == "listed_in_kev_relation":
            cve_id = _cyber_task_relation(item, records)
        else:
            values[evidence_id] = _cyber_task_fact(item, records)
    replacement_due_date = _cyber_counterfactual_due_date(task, items, records)
    if counterfactual and "due_date" in selected_values:
        values["due_date"] = replacement_due_date
    return _cyber_remediation_answer(cve_id=cve_id, **values)


def _cyber_task_fact_item(record: dict[str, Any], evidence_id: str) -> dict[str, Any]:
    facts = [
        fact
        for fact in record["facts"]
        if isinstance(fact, dict) and fact.get("fact_id") == evidence_id
    ]
    if len(facts) != 1:
        raise ProvenanceError("cyber task source fact is not unique")
    fact = facts[0]
    return {
        "evidence_id": evidence_id,
        "kind": "source_fact",
        "record_id": record["record_id"],
        "fact_id": fact["fact_id"],
        "field": fact["field"],
        "value": fact["value"],
        "evidence_quote": fact["evidence_quote"],
        "surface_text": fact["evidence_quote"],
        "char_start": fact["char_start"],
        "char_end": fact["char_end"],
    }


def build_cyber_kev_remediation_task(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compile one disabled standalone candidate from one audited NVD/KEV join."""
    audit_cyber_workflow_manifest(manifest)
    records = manifest.get("records")
    relations = manifest.get("relations")
    if (
        not isinstance(records, list)
        or len(records) != 2
        or not isinstance(relations, list)
        or len(relations) != 1
    ):
        raise ProvenanceError("cyber manifest does not uniquely select one KEV task")
    record_map = {
        str(record.get("kind") or ""): record
        for record in records
        if isinstance(record, dict)
    }
    nvd = record_map.get("nvd_cve")
    kev = record_map.get("cisa_kev_entry")
    if nvd is None or kev is None:
        raise ProvenanceError("cyber manifest does not uniquely select one KEV task")
    relation = relations[0]
    evidence_items = [
        _cyber_task_fact_item(nvd, "vulnerability_status"),
        _cyber_task_fact_item(kev, "date_added"),
        _cyber_task_fact_item(kev, "due_date"),
        _cyber_task_fact_item(kev, "required_action"),
        {
            "evidence_id": "listed_in_kev_relation",
            "kind": "source_relation",
            "surface_text": "\n".join(
                str(item["evidence_quote"]) for item in relation["evidence"]
            ),
            "relation": relation,
        },
    ]
    due_item = next(
        item for item in evidence_items if item["evidence_id"] == "due_date"
    )
    parent_value = str(due_item["value"])
    replacement = "2099-12-31" if parent_value != "2099-12-31" else "2098-12-31"
    encoded_parent = json.dumps(parent_value, ensure_ascii=False)
    encoded_replacement = json.dumps(replacement, ensure_ascii=False)
    parent_quote = str(due_item["evidence_quote"])
    if parent_quote.count(encoded_parent) != 1:
        raise ProvenanceError("cyber due-date evidence is not uniquely replaceable")
    cf_quote = parent_quote.replace(encoded_parent, encoded_replacement, 1)
    cf_text = (
        str(kev["text"])[: due_item["char_start"]]
        + cf_quote
        + str(kev["text"])[due_item["char_end"] :]
    )
    copied_records = json.loads(json.dumps(records, ensure_ascii=False))
    copied_receipt = json.loads(
        json.dumps(manifest["fetch_receipt"], ensure_ascii=False)
    )
    copied_manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
    manifest_metadata = {
        key: value
        for key, value in copied_manifest.items()
        if key not in {"records", "relations", "fetch_receipt", "attestation"}
    }
    task: dict[str, Any] = {
        "schema_version": CYBER_KEV_REMEDIATION_TASK_SCHEMA,
        "data_stage": "candidate_task",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "source_attestation_verified": False,
        "query_type": "kev_remediation_window",
        "answer_program_id": "cyber.kev_remediation_window.v1",
        "question": _CYBER_KEV_QUESTION,
        "answer": "",
        "cf_answer": "",
        "essential_evidence_ids": [str(item["evidence_id"]) for item in evidence_items],
        "evidence_items": json.loads(json.dumps(evidence_items, ensure_ascii=False)),
        "source_bindings": {
            record["record_id"]: {
                field: record[field] for field in _CYBER_BINDING_FIELDS
            }
            for record in copied_records
        },
        "source_records": copied_records,
        "source_receipt": copied_receipt,
        "source_manifest_binding": {
            "source_manifest_sha256": _cyber_manifest_digest(copied_manifest),
            "manifest_metadata": manifest_metadata,
            "attestation": copied_manifest.get("attestation"),
        },
        "counterfactual_twin": {
            "record_id": kev["record_id"],
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_kev_due_date",
            "parent_text_sha256": kev["text_sha256"],
            "parent_evidence_quote": parent_quote,
            "parent_char_start": due_item["char_start"],
            "parent_char_end": due_item["char_end"],
            "parent_value": parent_value,
            "text": cf_text,
            "text_sha256": hashlib.sha256(cf_text.encode()).hexdigest(),
            "evidence_quote": cf_quote,
            "value": replacement,
            "char_start": due_item["char_start"],
            "char_end": due_item["char_start"] + len(cf_quote),
        },
    }
    task["answer"] = replay_cyber_kev_remediation_task(task)
    task["cf_answer"] = replay_cyber_kev_remediation_task(task, counterfactual=True)
    audit = audit_cyber_kev_remediation_task(task)
    if not all(
        value for name, value in audit.items() if name != "source_attestation_verified"
    ):
        raise ProvenanceError("cyber KEV task failed executable audit")
    return task


def audit_cyber_kev_remediation_task(
    task: dict[str, Any], *, source_attestation_key: bytes | None = None
) -> dict[str, bool]:
    """Run strict, counterfactual, remove-one, and surface-negative gates."""
    essentials = list(task.get("essential_evidence_ids") or [])
    answer = str(task.get("answer") or "")
    cf_answer = str(task.get("cf_answer") or "")
    strict_replay = replay_cyber_kev_remediation_task(task)
    cf_replay = replay_cyber_kev_remediation_task(task, counterfactual=True)
    singles = [
        replay_cyber_kev_remediation_task(task, evidence_ids=[evidence_id])
        for evidence_id in essentials
    ]
    removals = [
        replay_cyber_kev_remediation_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        for removed in essentials
    ]
    items = task.get("evidence_items")
    if not isinstance(items, list):
        raise ProvenanceError("cyber task evidence items are invalid")
    item_map = {
        str(item.get("evidence_id") or ""): item
        for item in items
        if isinstance(item, dict)
    }
    records = _cyber_task_record_map(task)
    answer_inputs = {
        _cyber_task_relation(item_map["listed_in_kev_relation"], records),
        *(
            str(item_map[evidence_id]["value"])
            for evidence_id in _CYBER_KEV_ESSENTIALS
            if evidence_id != "listed_in_kev_relation"
        ),
    }
    canonical_answer = " ".join(re.findall(r"[a-z0-9]+", answer.casefold()))
    canonical_inputs = {
        " ".join(re.findall(r"[a-z0-9]+", value.casefold())) for value in answer_inputs
    }
    surfaces = [
        " ".join(
            re.findall(r"[a-z0-9]+", str(item.get("surface_text") or "").casefold())
        )
        for item in items
        if isinstance(item, dict)
    ]
    surface_free = (
        bool(surfaces)
        and len(surfaces) == len(items)
        and all(
            canonical_answer not in surface
            and not all(value in surface for value in canonical_inputs)
            for surface in surfaces
        )
    )
    return {
        "strict_replay_sufficient": strict_replay == answer,
        "counterfactual_replay_sufficient": cf_replay == cf_answer,
        "counterfactual_changes_answer": cf_answer != answer,
        "remove_one_fails": bool(removals)
        and all(value == "unknown" for value in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(value == "unknown" for value in singles),
        "essential_surface_gold_free": surface_free,
        "essential_text_grounded": strict_replay == answer,
        "source_attestation_verified": _cyber_task_source_attestation_verified(
            task, source_attestation_key
        ),
    }
