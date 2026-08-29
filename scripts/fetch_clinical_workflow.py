#!/usr/bin/env python3
"""Fetch bounded current ClinicalTrials.gov and openFDA source projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.clinicalworkflow import (
    CLINICAL_ATTRIBUTION,
    CLINICAL_FETCH_INVENTORY_SCHEMA,
    CLINICAL_LICENSE,
    CLINICAL_TERMS_URL,
    CLINICAL_TRIAL_API_ROOT,
    FDA_APPLICATION_API_URL,
    FDA_LABEL_API_URL,
    OPENFDA_ATTRIBUTION,
    OPENFDA_LICENSE,
    OPENFDA_LICENSE_URL,
    OPENFDA_TERMS_URL,
    project_clinical_trial_response,
    project_fda_application_response,
    project_fda_label_response,
    validate_clinical_fetch_request,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _read_regular_file,
)

MAX_RESPONSE_BYTES = 8_000_000
HTTP_TIMEOUT_SECONDS = 30.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    status: int
    final_url: str
    content_type: str


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return HttpResponse(
                body=body,
                status=int(response.status),
                final_url=str(response.geturl()),
                content_type=str(response.headers.get_content_type()),
            )
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        return HttpResponse(
            body=body,
            status=int(error.code),
            final_url=str(error.geturl()),
            content_type=str(error.headers.get_content_type()),
        )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _write(path: Path, value: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _decode_object(body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"{label} response is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProvenanceError(f"{label} response must be an object")
    return value


def _request_urls(request: dict[str, Any]) -> list[tuple[str, str]]:
    application = request["fda_application_number"]
    application_query = urllib.parse.urlencode(
        {"search": f"application_number:{application}", "limit": "1"}
    )
    label_query = urllib.parse.urlencode(
        {
            "search": f'openfda.application_number:"{application}"',
            "limit": "1",
        }
    )
    return [
        ("clinical_trial", f"{CLINICAL_TRIAL_API_ROOT}/{request['nct_id']}"),
        ("fda_application", f"{FDA_APPLICATION_API_URL}?{application_query}"),
        ("fda_label", f"{FDA_LABEL_API_URL}?{label_query}"),
    ]


def _project(kind: str, body: bytes, request: dict[str, Any]) -> dict[str, Any]:
    payload = _decode_object(body, kind)
    if kind == "clinical_trial":
        return project_clinical_trial_response(payload, request["nct_id"])
    if kind == "fda_application":
        return project_fda_application_response(
            payload, request["fda_application_number"]
        )
    if kind == "fda_label":
        return project_fda_label_response(payload, request["fda_application_number"])
    raise ProvenanceError("clinical retrieval kind is invalid")


def _fetch_one(
    kind: str,
    url: str,
    request: dict[str, Any],
    *,
    http_get: Callable[[str, dict[str, str], float], HttpResponse],
    sleep: Callable[[float], None],
) -> tuple[HttpResponse, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "User-Agent": request["user_agent"],
    }
    response: HttpResponse | None = None
    for attempt in range(request["max_retries"] + 1):
        try:
            response = http_get(url, headers, HTTP_TIMEOUT_SECONDS)
        except (OSError, urllib.error.URLError) as error:
            if attempt == request["max_retries"]:
                raise ProvenanceError(f"clinical fetch failed: {error}") from error
            sleep(float(2**attempt))
            continue
        if response.status in _RETRYABLE_STATUS and attempt < request["max_retries"]:
            sleep(float(2**attempt))
            continue
        break
    if response is None:
        raise ProvenanceError("clinical fetch produced no response")
    content_type = response.content_type.split(";", 1)[0].strip().lower()
    if not (
        response.status == 200
        and response.final_url == url
        and content_type == "application/json"
        and 0 < len(response.body) <= MAX_RESPONSE_BYTES
    ):
        raise ProvenanceError("clinical HTTP response metadata is invalid")
    projection = _project(kind, response.body, request)
    return HttpResponse(
        response.body, response.status, response.final_url, content_type
    ), projection


def fetch_clinical_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: Callable[[str, dict[str, str], float], HttpResponse] = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
    clock: Callable[[], str] = _timestamp,
) -> Path:
    """Fetch exact public records and atomically retain only bounded projections."""
    if output_directory.exists() or output_directory.is_symlink():
        raise ProvenanceError("clinical output directory already exists")
    try:
        request_raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read clinical fetch request: {error}") from error
    request = validate_clinical_fetch_request(request_payload)
    started_at = generated_at or clock()
    from longworld.core.provenance import _parse_timestamp

    _parse_timestamp(started_at, "clinical fetch generated_at")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.", dir=output_directory.parent
        )
    )
    try:
        request_name = "clinical_fetch_request.json"
        _write(staging / request_name, request_raw)
        request_sha256 = hashlib.sha256(request_raw).hexdigest()
        retrievals: list[dict[str, Any]] = []
        for index, (kind, url) in enumerate(_request_urls(request), start=1):
            response, projection = _fetch_one(
                kind,
                url,
                request,
                http_get=http_get,
                sleep=sleep,
            )
            projection_raw = _canonical_bytes(projection)
            filename = f"{index:02d}_{kind}.json"
            _write(staging / filename, projection_raw)
            observed_at = clock()
            _parse_timestamp(observed_at, "clinical observed_at")
            retrievals.append(
                {
                    "kind": kind,
                    "requested_url": url,
                    "final_url": response.final_url,
                    "status": response.status,
                    "content_type": response.content_type,
                    "redirect_chain": [],
                    "observed_at": observed_at,
                    "response_sha256": hashlib.sha256(response.body).hexdigest(),
                    "response_bytes": len(response.body),
                    "projection_sha256": hashlib.sha256(projection_raw).hexdigest(),
                    "projection_bytes": len(projection_raw),
                    "retrieval_file": filename,
                    "source_materialization": "privacy_minimized_projection",
                    "excluded_content": [
                        "contacts",
                        "individual_records",
                        "participant_narratives",
                    ],
                    "email_redaction_count": 0,
                }
            )
        completed_at = clock()
        _parse_timestamp(completed_at, "clinical completed_at")
        policies = {
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
        inventory = {
            "schema_version": CLINICAL_FETCH_INVENTORY_SCHEMA,
            "source_status": "public_api_export",
            "data_stage": "source_inventory",
            "hybrid_train_ready": False,
            "production_eligible": False,
            "generation_integration": "disabled",
            "semantic_facts_train_ready": False,
            "snapshot_semantics": "current_state_observed_at_fetch",
            "historical_snapshot_claims": False,
            "generated_at": started_at,
            "request_file": request_name,
            "request_sha256": request_sha256,
            "authorization": request["authorization"],
            "fetch_receipt": {
                "started_at": started_at,
                "completed_at": completed_at,
                "max_retries": request["max_retries"],
                "allowed_actions": request["authorization"]["allowed_actions"],
                "request_file": request_name,
                "request_sha256": request_sha256,
                "user_agent_sha256": hashlib.sha256(
                    request["user_agent"].encode()
                ).hexdigest(),
                "source_policies": policies,
                "retrievals": retrievals,
            },
            "n_retrievals": len(retrievals),
        }
        _write(staging / "clinical_fetch_inventory.json", _canonical_bytes(inventory))
        os.replace(staging, output_directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output_directory / "clinical_fetch_inventory.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(fetch_clinical_workflow(args.request, args.out_dir))


if __name__ == "__main__":
    main()
