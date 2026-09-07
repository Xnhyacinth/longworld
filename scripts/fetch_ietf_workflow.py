#!/usr/bin/env python3
"""Fetch bounded official IETF records into a disabled source inventory."""

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

IETF_FETCH_REQUEST_SCHEMA = "longworld.ietf-fetch-request.v1"
IETF_FETCH_INVENTORY_SCHEMA = "longworld.ietf-fetch-inventory.v1"
IETF_OPEN_RECORDS_POLICY = "https://www.ietf.org/about/open-records/"
MAX_DRAFTS = 32
MAX_REVISIONS_PER_DRAFT = 32
MAX_RFCS = 64
MAX_RETRIES = 3
MAX_REQUESTS_PER_SECOND = 3.0
MAX_RESPONSE_BYTES = 8_000_000
_DRAFT = re.compile(r"^draft-[a-z0-9]+(?:-[a-z0-9]+)+$")
_REVISION = re.compile(r"^\d{2}$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_ALLOWED_ACTIONS = {
    "fetch_datatracker_document",
    "fetch_datatracker_relation",
    "fetch_draft_revision",
    "fetch_rfc",
}
_RETRYABLE = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    status: int
    final_url: str
    content_type: str


HttpGet = Callable[[str, dict[str, str], float], HttpResponse]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> HttpResponse:
    opener = urllib.request.build_opener(_RejectRedirects())
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        return HttpResponse(
            body=response.read(MAX_RESPONSE_BYTES + 1),
            status=response.status,
            final_url=response.geturl(),
            content_type=response.headers.get_content_type(),
        )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _strings(value: object, label: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ProvenanceError(f"IETF fetch {label} is invalid")
    result = [str(item).strip() for item in value]
    if any(not item or len(item) > 256 for item in result) or len(set(result)) != len(
        result
    ):
        raise ProvenanceError(f"IETF fetch {label} is invalid")
    return result


def _authorization(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("IETF fetch authorization is invalid")
    result: dict[str, Any] = {
        field: str(value.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    }
    if any(not result[field] for field in result):
        raise ProvenanceError("IETF fetch authorization is invalid")
    _parse_timestamp(result["reviewed_at"], "authorization.reviewed_at")
    actions = sorted(
        _strings(value.get("allowed_actions"), "allowed_actions", len(_ALLOWED_ACTIONS))
    )
    if not set(actions).issubset(_ALLOWED_ACTIONS):
        raise ProvenanceError("IETF fetch authorization actions are invalid")
    result["allowed_actions"] = actions
    return result


_IETF_FETCH_REQUEST_FIELDS = {
    "schema_version",
    "user_agent",
    "authorization",
    "drafts",
    "rfc_numbers",
    "approved_public_test_vector_sha256",
    "requests_per_second",
    "max_retries",
}


def _rfc_datatracker_sources(payload: dict[str, Any], rfc_numbers: list[int]) -> list[int]:
    if "rfc_datatracker_sources" not in payload:
        return []
    sources = payload.get("rfc_datatracker_sources")
    requested = set(rfc_numbers)
    if (
        not isinstance(sources, list)
        or not sources
        or len(sources) > MAX_RFCS
        or any(
            isinstance(number, bool) or not isinstance(number, int) or number < 1
            for number in sources
        )
        or len(set(sources)) != len(sources)
        or sources != sorted(sources)
        or any(number not in requested for number in sources)
    ):
        raise ProvenanceError("IETF fetch RFC datatracker sources are invalid")
    return list(sources)


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    extra = set(payload) - _IETF_FETCH_REQUEST_FIELDS
    if (
        not _IETF_FETCH_REQUEST_FIELDS.issubset(payload)
        or extra not in (set(), {"rfc_datatracker_sources"})
        or payload.get("schema_version") != IETF_FETCH_REQUEST_SCHEMA
    ):
        raise ProvenanceError("unsupported IETF fetch request schema")
    user_agent = str(payload.get("user_agent") or "").strip()
    if (
        len(user_agent) > 256
        or _CONTACT_USER_AGENT.fullmatch(user_agent) is None
        or "example.com" in user_agent.lower()
    ):
        raise ProvenanceError("IETF fetch requires a contact User-Agent")
    raw_drafts = payload.get("drafts")
    if (
        not isinstance(raw_drafts, list)
        or not raw_drafts
        or len(raw_drafts) > MAX_DRAFTS
    ):
        raise ProvenanceError("IETF fetch drafts are invalid")
    drafts: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in raw_drafts:
        if not isinstance(item, dict) or set(item) != {"name", "revisions"}:
            raise ProvenanceError("IETF fetch draft entry is invalid")
        name = str(item.get("name") or "")
        revisions = _strings(
            item.get("revisions"), "draft revisions", MAX_REVISIONS_PER_DRAFT
        )
        if (
            _DRAFT.fullmatch(name) is None
            or name in names
            or any(_REVISION.fullmatch(revision) is None for revision in revisions)
            or revisions != sorted(revisions, key=int)
            or any(
                int(current) != int(prior) + 1 for prior, current in pairwise(revisions)
            )
        ):
            raise ProvenanceError("IETF fetch draft name or revisions are invalid")
        names.add(name)
        drafts.append({"name": name, "revisions": revisions})
    numbers = payload.get("rfc_numbers")
    if (
        not isinstance(numbers, list)
        or not numbers
        or len(numbers) > MAX_RFCS
        or any(
            isinstance(number, bool) or not isinstance(number, int) or number < 1
            for number in numbers
        )
        or len(set(numbers)) != len(numbers)
        or numbers != sorted(numbers)
    ):
        raise ProvenanceError("IETF fetch RFC numbers are invalid")
    approved_test_vectors = payload.get("approved_public_test_vector_sha256")
    if (
        not isinstance(approved_test_vectors, list)
        or len(approved_test_vectors) > 128
        or len(set(approved_test_vectors)) != len(approved_test_vectors)
        or any(
            not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in approved_test_vectors
        )
    ):
        raise ProvenanceError("IETF approved test-vector digests are invalid")
    rate = payload.get("requests_per_second")
    retries = payload.get("max_retries")
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= MAX_REQUESTS_PER_SECOND
    ):
        raise ProvenanceError("IETF fetch requests_per_second is invalid")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("IETF fetch max_retries is invalid")
    authorization = _authorization(payload.get("authorization"))
    if set(authorization["allowed_actions"]) != _ALLOWED_ACTIONS:
        raise ProvenanceError("IETF fetch request exceeds authorization actions")
    rfc_numbers = list(numbers)
    return {
        "user_agent": user_agent,
        "authorization": authorization,
        "drafts": drafts,
        "rfc_numbers": rfc_numbers,
        "rfc_datatracker_sources": _rfc_datatracker_sources(payload, rfc_numbers),
        "approved_public_test_vector_sha256": list(approved_test_vectors),
        "requests_per_second": float(rate),
        "max_retries": retries,
    }


class _Downloader:
    def __init__(
        self,
        *,
        user_agent: str,
        requests_per_second: float,
        max_retries: int,
        http_get: HttpGet,
        sleep: Callable[[float], None],
    ) -> None:
        self.headers = {"User-Agent": user_agent}
        self.minimum_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.http_get = http_get
        self.sleep = sleep
        self.last_request_at: float | None = None

    def get(self, url: str, *, content_type: str) -> HttpResponse:
        for attempt in range(self.max_retries + 1):
            now = time.monotonic()
            if self.last_request_at is not None:
                remaining = self.minimum_interval - (now - self.last_request_at)
                if remaining > 0:
                    self.sleep(remaining)
            self.last_request_at = time.monotonic()
            try:
                response = self.http_get(url, self.headers, 90.0)
                if not isinstance(response, HttpResponse):
                    raise ProvenanceError("IETF client omitted response metadata")
                if response.status in _RETRYABLE and attempt < self.max_retries:
                    self.sleep(2.0**attempt)
                    continue
                normalized_type = (
                    response.content_type.partition(";")[0].strip().lower()
                )
                if (
                    response.status != 200
                    or response.final_url != url
                    or normalized_type != content_type
                    or not isinstance(response.body, bytes)
                    or not response.body
                    or len(response.body) > MAX_RESPONSE_BYTES
                ):
                    raise ProvenanceError("IETF response metadata or bytes are invalid")
                return HttpResponse(response.body, 200, url, normalized_type)
            except urllib.error.HTTPError as error:
                if error.code not in _RETRYABLE or attempt == self.max_retries:
                    raise ProvenanceError(
                        f"IETF request failed with HTTP {error.code}"
                    ) from error
                self.sleep(2.0**attempt)
            except urllib.error.URLError as error:
                if attempt == self.max_retries:
                    raise ProvenanceError(
                        f"IETF request failed: {error.reason}"
                    ) from error
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")


def _write(path: Path, body: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def fetch_ietf_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
    clock: Callable[[], str] = _timestamp,
) -> Path:
    try:
        request_raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        payload = json.loads(request_raw.decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read IETF fetch request: {error}") from error
    if not isinstance(payload, dict):
        raise ProvenanceError("IETF fetch request must be an object")
    request = _validate_request(payload)
    timestamp = generated_at or clock()
    _parse_timestamp(timestamp, "generated_at")
    if os.path.lexists(output_directory):
        raise ProvenanceError("IETF fetch output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        requests_per_second=request["requests_per_second"],
        max_retries=request["max_retries"],
        http_get=http_get,
        sleep=sleep,
    )
    request_sha256 = hashlib.sha256(request_raw).hexdigest()
    retrievals: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        dir=output_directory.parent, prefix=f".{output_directory.name}."
    ) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir(mode=0o700)
        request_name = "ietf_fetch_request.json"
        _write(staging / request_name, request_raw)
        specifications: list[tuple[str, str, str, str]] = []
        for draft in request["drafts"]:
            name = draft["name"]
            specifications.append(
                (
                    "datatracker_document",
                    f"datatracker-{name}.json",
                    f"https://datatracker.ietf.org/api/v1/doc/document/{name}/",
                    "application/json",
                )
            )
            specifications.append(
                (
                    "datatracker_relation",
                    f"datatracker-{name}-relations.json",
                    (
                        "https://datatracker.ietf.org/api/v1/doc/relateddocument/"
                        f"?source__name={name}&limit=100"
                    ),
                    "application/json",
                )
            )
            specifications.extend(
                (
                    "draft_revision",
                    f"{name}-{revision}.txt",
                    f"https://www.ietf.org/archive/id/{name}-{revision}.txt",
                    "text/plain",
                )
                for revision in draft["revisions"]
            )
        specifications.extend(
            (
                "rfc",
                f"rfc{number}.txt",
                f"https://www.rfc-editor.org/rfc/rfc{number}.txt",
                "text/plain",
            )
            for number in request["rfc_numbers"]
        )
        for number in request["rfc_datatracker_sources"]:
            specifications.append(
                (
                    "datatracker_document",
                    f"datatracker-rfc{number}.json",
                    f"https://datatracker.ietf.org/api/v1/doc/document/rfc{number}/",
                    "application/json",
                )
            )
            specifications.append(
                (
                    "datatracker_relation",
                    f"datatracker-rfc{number}-relations.json",
                    (
                        "https://datatracker.ietf.org/api/v1/doc/relateddocument/"
                        f"?source__name=rfc{number}&limit=100"
                    ),
                    "application/json",
                )
            )
        for kind, filename, url, content_type in specifications:
            response = downloader.get(url, content_type=content_type)
            observed_at = clock()
            _parse_timestamp(observed_at, "retrieval observed_at")
            _write(staging / filename, response.body)
            retrievals.append(
                {
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
            )
        completed_at = clock()
        started_time = _parse_timestamp(timestamp, "fetch started_at")
        completed_time = _parse_timestamp(completed_at, "fetch completed_at")
        observed_times = [
            _parse_timestamp(item["observed_at"], "retrieval observed_at")
            for item in retrievals
        ]
        if not (
            started_time < observed_times[0]
            and all(prior < current for prior, current in pairwise(observed_times))
            and observed_times[-1] < completed_time
        ):
            raise ProvenanceError("IETF fetch observation timeline is invalid")
        inventory = {
            "schema_version": IETF_FETCH_INVENTORY_SCHEMA,
            "source_status": "public_api_export",
            "data_stage": "source_inventory",
            "hybrid_train_ready": False,
            "production_eligible": False,
            "generation_integration": "disabled",
            "semantic_facts_train_ready": False,
            "generated_at": timestamp,
            "request_file": request_name,
            "request_sha256": request_sha256,
            "authorization": request["authorization"],
            "fetch_receipt": {
                "policy_url": IETF_OPEN_RECORDS_POLICY,
                "started_at": timestamp,
                "completed_at": completed_at,
                "requests_per_second": request["requests_per_second"],
                "max_retries": request["max_retries"],
                "allowed_actions": request["authorization"]["allowed_actions"],
                "request_file": request_name,
                "request_sha256": request_sha256,
                "user_agent_sha256": hashlib.sha256(
                    request["user_agent"].encode()
                ).hexdigest(),
                "retrievals": retrievals,
            },
            "n_retrievals": len(retrievals),
        }
        _write(staging / "ietf_fetch_inventory.json", _canonical_bytes(inventory))
        os.replace(staging, output_directory)
    return output_directory / "ietf_fetch_inventory.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(fetch_ietf_workflow(args.request, args.out_dir))


if __name__ == "__main__":
    main()
