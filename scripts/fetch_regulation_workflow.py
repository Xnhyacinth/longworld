#!/usr/bin/env python3
"""Fetch bounded Federal Register documents and Regulations.gov docket metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

REGULATION_FETCH_REQUEST_SCHEMA = "longworld.regulation-fetch-request.v1"
REGULATION_FETCH_INVENTORY_SCHEMA = "longworld.regulation-fetch-inventory.v1"
FEDERAL_REGISTER_DOCUMENT_URL = "https://www.federalregister.gov/api/v1/documents"
REGULATIONS_DOCKET_URL = "https://api.regulations.gov/v4/dockets"
FEDERAL_REGISTER_TERMS_URL = (
    "https://www.federalregister.gov/reader-aids/"
    "government-policy-and-ofr-procedures/about-this-site"
)
REGULATIONS_TERMS_URL = "https://open.gsa.gov/api/regulationsgov/"
MAX_DOCUMENTS = 32
MAX_RESPONSE_BYTES = 8_000_000
_RETRYABLE = {429, 500, 502, 503, 504}
_DOCUMENT_NUMBER = re.compile(r"^(?:[12]\d{3}|\d{2}|E\d{1,2}|X\d{2})-[0-9A-Z]+$")
_DOCKET_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-[12]\d{3}-\d{4,8}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_ALLOWED_ACTIONS = {
    "fetch_federal_register_document",
    "fetch_regulations_docket_metadata",
}


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    status: int
    final_url: str
    content_type: str


HttpGet = Callable[[str, dict[str, str], float], HttpResponse]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_RejectRedirects())
    with opener.open(request, timeout=timeout) as response:
        return HttpResponse(
            body=response.read(MAX_RESPONSE_BYTES + 1),
            status=response.status,
            final_url=response.geturl(),
            content_type=response.headers.get_content_type(),
        )


def _strings(value: object, label: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ProvenanceError(f"regulation fetch {label} is invalid")
    result = [str(item).strip() for item in value]
    if any(not item or len(item) > 256 for item in result) or len(result) != len(
        set(result)
    ):
        raise ProvenanceError(f"regulation fetch {label} is invalid")
    return result


def _authorization(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("regulation fetch authorization is invalid")
    result: dict[str, Any] = {
        field: str(value.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    }
    if any(not item for item in result.values()):
        raise ProvenanceError("regulation fetch authorization is invalid")
    _parse_timestamp(result["reviewed_at"], "authorization.reviewed_at")
    actions = sorted(_strings(value.get("allowed_actions"), "authorization actions", 2))
    if set(actions) != _ALLOWED_ACTIONS:
        raise ProvenanceError("regulation fetch authorization actions are invalid")
    result["allowed_actions"] = actions
    return result


def validate_regulation_fetch_request(value: object) -> dict[str, Any]:
    """Validate a credential-free, metadata-only regulation request."""
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
        raise ProvenanceError("regulation fetch request schema is invalid")
    user_agent = str(value.get("user_agent") or "").strip()
    if len(user_agent) > 256 or _CONTACT_USER_AGENT.fullmatch(user_agent) is None:
        raise ProvenanceError("regulation fetch requires a contact User-Agent")
    docket_id = str(value.get("docket_id") or "").strip()
    if _DOCKET_ID.fullmatch(docket_id) is None:
        raise ProvenanceError("regulation fetch docket identity is invalid")
    document_numbers = _strings(
        value.get("federal_register_document_numbers"),
        "document identities",
        MAX_DOCUMENTS,
    )
    if any(_DOCUMENT_NUMBER.fullmatch(item) is None for item in document_numbers):
        raise ProvenanceError("regulation fetch document identity is invalid")
    api_key_env = str(value.get("regulations_api_key_env") or "").strip()
    if _ENV_NAME.fullmatch(api_key_env) is None:
        raise ProvenanceError("regulation fetch API key environment is invalid")
    rate = value.get("requests_per_second")
    retries = value.get("max_retries")
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= 3.0
    ):
        raise ProvenanceError("regulation fetch requests_per_second is invalid")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("regulation fetch max_retries is invalid")
    return {
        "user_agent": user_agent,
        "authorization": _authorization(value.get("authorization")),
        "docket_id": docket_id,
        "federal_register_document_numbers": document_numbers,
        "regulations_api_key_env": api_key_env,
        "requests_per_second": float(rate),
        "max_retries": retries,
    }


class _Downloader:
    def __init__(
        self,
        *,
        user_agent: str,
        api_key: str,
        requests_per_second: float,
        max_retries: int,
        http_get: HttpGet,
        sleep: Callable[[float], None],
    ) -> None:
        self.user_agent = user_agent
        self.api_key = api_key
        self.minimum_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.http_get = http_get
        self.sleep = sleep
        self.last_request_at: float | None = None

    def get(self, url: str, *, regulations: bool = False) -> HttpResponse:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if regulations:
            headers["X-Api-Key"] = self.api_key
        for attempt in range(self.max_retries + 1):
            now = time.monotonic()
            if self.last_request_at is not None:
                remaining = self.minimum_interval - (now - self.last_request_at)
                if remaining > 0:
                    self.sleep(remaining)
            self.last_request_at = time.monotonic()
            try:
                response = self.http_get(url, headers, 90.0)
                if not isinstance(response, HttpResponse):
                    raise ProvenanceError("regulation client omitted response metadata")
                if response.status in _RETRYABLE and attempt < self.max_retries:
                    self.sleep(2.0**attempt)
                    continue
                content_type = response.content_type.partition(";")[0].strip().lower()
                if (
                    response.status != 200
                    or response.final_url != url
                    or content_type
                    != (
                        "application/vnd.api+json"
                        if regulations
                        else "application/json"
                    )
                    or not isinstance(response.body, bytes)
                    or not response.body
                    or len(response.body) > MAX_RESPONSE_BYTES
                ):
                    raise ProvenanceError(
                        "regulation response metadata or bytes are invalid"
                    )
                return HttpResponse(response.body, 200, url, content_type)
            except urllib.error.HTTPError as error:
                if error.code not in _RETRYABLE or attempt == self.max_retries:
                    raise ProvenanceError(
                        f"regulation request failed with HTTP {error.code}"
                    ) from error
                self.sleep(2.0**attempt)
            except urllib.error.URLError as error:
                if attempt == self.max_retries:
                    raise ProvenanceError(
                        f"regulation request failed: {error.reason}"
                    ) from error
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"{label} response is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProvenanceError(f"{label} response must be an object")
    return value


def _validate_retrieved_sources(
    *,
    docket_id: str,
    document_numbers: list[str],
    documents: list[bytes],
    docket: bytes,
) -> None:
    docket_payload = _json_object(docket, "Regulations.gov docket")
    data = docket_payload.get("data")
    attributes = data.get("attributes") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("id") != docket_id
        or data.get("type") != "dockets"
        or not isinstance(attributes, dict)
        or attributes.get("docketType") != "Rulemaking"
        or not isinstance(attributes.get("rin"), str)
        or not attributes["rin"].strip()
    ):
        raise ProvenanceError("Regulations.gov docket identity is invalid")
    rin = attributes["rin"].strip()
    publication_dates: list[str] = []
    for requested, raw in zip(document_numbers, documents, strict=True):
        payload = _json_object(raw, "Federal Register document")
        if payload.get("document_number") != requested:
            raise ProvenanceError("Federal Register document identity is invalid")
        rins = payload.get("regulation_id_numbers")
        if not isinstance(rins, list) or rins != [rin]:
            raise ProvenanceError("Federal Register document RIN is invalid")
        publication_date = payload.get("publication_date")
        if not isinstance(publication_date, str):
            raise ProvenanceError("Federal Register publication date is invalid")
        try:
            datetime.strptime(publication_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as error:
            raise ProvenanceError(
                "Federal Register publication date is invalid"
            ) from error
        publication_dates.append(publication_date)
    if any(prior >= current for prior, current in pairwise(publication_dates)):
        raise ProvenanceError("Federal Register document sequence is invalid")


def _write(path: Path, body: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def fetch_regulation_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    api_key: str | None = None,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
    clock: Callable[[], str] = _timestamp,
) -> Path:
    """Write a disabled source inventory without fetching comment records."""
    try:
        request_raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read regulation fetch request: {error}"
        ) from error
    request = validate_regulation_fetch_request(request_payload)
    resolved_key = (
        api_key
        if api_key is not None
        else os.environ.get(request["regulations_api_key_env"], "")
    )
    if (
        not resolved_key
        or len(resolved_key) > 512
        or resolved_key.strip() != resolved_key
        or any(ord(character) < 33 for character in resolved_key)
    ):
        raise ProvenanceError("Regulations.gov API key is missing or invalid")
    started_at = generated_at or clock()
    _parse_timestamp(started_at, "regulation fetch started_at")
    if os.path.lexists(output_directory):
        raise ProvenanceError("regulation fetch output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        api_key=resolved_key,
        requests_per_second=request["requests_per_second"],
        max_retries=request["max_retries"],
        http_get=http_get,
        sleep=sleep,
    )
    retrievals: list[dict[str, Any]] = []
    document_bodies: list[bytes] = []
    docket_body: bytes | None = None
    with tempfile.TemporaryDirectory(
        dir=output_directory.parent, prefix=f".{output_directory.name}."
    ) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir(mode=0o700)
        request_name = "regulation_fetch_request.json"
        _write(staging / request_name, request_raw)
        specifications = [
            (
                "federal_register_document",
                f"federal-register-{number}.json",
                f"{FEDERAL_REGISTER_DOCUMENT_URL}/{number}.json",
                number,
                False,
            )
            for number in request["federal_register_document_numbers"]
        ]
        specifications.append(
            (
                "regulations_docket_metadata",
                f"regulations-docket-{request['docket_id']}.json",
                f"{REGULATIONS_DOCKET_URL}/{request['docket_id']}",
                request["docket_id"],
                True,
            )
        )
        for kind, filename, url, identity, is_regulations in specifications:
            response = downloader.get(url, regulations=is_regulations)
            observed_at = clock()
            _parse_timestamp(observed_at, "regulation retrieval observed_at")
            _write(staging / filename, response.body)
            item = {
                "kind": kind,
                "requested_url": url,
                "final_url": response.final_url,
                "status": response.status,
                "content_type": response.content_type,
                "redirect_chain": [],
                "observed_at": observed_at,
                "sha256": hashlib.sha256(response.body).hexdigest(),
                "retrieval_file": filename,
            }
            if kind == "federal_register_document":
                item["document_number"] = identity
                document_bodies.append(response.body)
            else:
                item["docket_id"] = identity
                docket_body = response.body
            retrievals.append(item)
        if docket_body is None:
            raise ProvenanceError("Regulations.gov docket response is missing")
        _validate_retrieved_sources(
            docket_id=request["docket_id"],
            document_numbers=request["federal_register_document_numbers"],
            documents=document_bodies,
            docket=docket_body,
        )
        completed_at = clock()
        times = [
            _parse_timestamp(started_at, "regulation fetch started_at"),
            *[
                _parse_timestamp(item["observed_at"], "regulation observed_at")
                for item in retrievals
            ],
            _parse_timestamp(completed_at, "regulation fetch completed_at"),
        ]
        if any(prior >= current for prior, current in pairwise(times)):
            raise ProvenanceError("regulation fetch observation timeline is invalid")
        inventory = {
            "schema_version": REGULATION_FETCH_INVENTORY_SCHEMA,
            "source_status": "public_api_export",
            "data_stage": "source_inventory",
            "hybrid_train_ready": False,
            "production_eligible": False,
            "generation_integration": "disabled",
            "semantic_facts_train_ready": False,
            "snapshot_semantics": "mixed_source_event_and_current_docket_metadata",
            "historical_snapshot_claims": False,
            "comment_content_collected": False,
            "generated_at": started_at,
            "request_file": request_name,
            "request_sha256": hashlib.sha256(request_raw).hexdigest(),
            "authorization": request["authorization"],
            "source_policies": {
                "federal_register": FEDERAL_REGISTER_TERMS_URL,
                "regulations_gov": REGULATIONS_TERMS_URL,
            },
            "fetch_receipt": {
                "started_at": started_at,
                "completed_at": completed_at,
                "retrievals": retrievals,
            },
            "n_retrievals": len(retrievals),
        }
        _write(staging / "fetch_inventory.json", _canonical_bytes(inventory))
        os.replace(staging, output_directory)
    return output_directory / "fetch_inventory.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    fetch_regulation_workflow(args.request, args.out_dir)


if __name__ == "__main__":
    main()
