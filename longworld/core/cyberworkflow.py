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

CYBER_FETCH_REQUEST_SCHEMA = "longworld.cyber-fetch-request.v1"
CYBER_FETCH_INVENTORY_SCHEMA = "longworld.cyber-fetch-inventory.v1"
CYBER_WORKFLOW_MANIFEST_SCHEMA = "longworld.cyber-workflow-manifest.v1"
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
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
