#!/usr/bin/env python3
"""Fetch bounded official NVD and CISA KEV current-state records."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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

from longworld.core.cyberworkflow import (
    CISA_ATTRIBUTION,
    CISA_KEV_URL,
    CISA_LICENSE,
    CISA_TERMS_URL,
    CYBER_FETCH_INVENTORY_SCHEMA,
    NVD_ATTRIBUTION,
    NVD_CVE_API_URL,
    NVD_LICENSE,
    NVD_TERMS_URL,
    parse_cyber_source_records,
    validate_cyber_fetch_request,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

MAX_RESPONSE_BYTES = 16_000_000
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
        self.headers = {"Accept": "application/json", "User-Agent": user_agent}
        self.minimum_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.http_get = http_get
        self.sleep = sleep
        self.last_request_at: float | None = None

    def get(self, url: str) -> HttpResponse:
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
                    raise ProvenanceError("cyber client omitted response metadata")
                if response.status in _RETRYABLE and attempt < self.max_retries:
                    self.sleep(2.0**attempt)
                    continue
                content_type = response.content_type.partition(";")[0].strip().lower()
                if (
                    response.status != 200
                    or response.final_url != url
                    or content_type != "application/json"
                    or not isinstance(response.body, bytes)
                    or not response.body
                    or len(response.body) > MAX_RESPONSE_BYTES
                ):
                    raise ProvenanceError(
                        "cyber response metadata or bytes are invalid"
                    )
                return HttpResponse(response.body, 200, url, content_type)
            except urllib.error.HTTPError as error:
                if error.code not in _RETRYABLE or attempt == self.max_retries:
                    raise ProvenanceError(
                        f"cyber request failed with HTTP {error.code}"
                    ) from error
                self.sleep(2.0**attempt)
            except urllib.error.URLError as error:
                if attempt == self.max_retries:
                    raise ProvenanceError(
                        f"cyber request failed: {error.reason}"
                    ) from error
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")


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


def fetch_cyber_workflow(
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
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read cyber fetch request: {error}") from error
    request = validate_cyber_fetch_request(request_payload)
    started_at = generated_at or clock()
    _parse_timestamp(started_at, "cyber fetch started_at")
    if os.path.lexists(output_directory):
        raise ProvenanceError("cyber fetch output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        requests_per_second=request["requests_per_second"],
        max_retries=request["max_retries"],
        http_get=http_get,
        sleep=sleep,
    )
    retrievals: list[dict[str, Any]] = []
    nvd_by_id: dict[str, bytes] = {}
    kev_raw: bytes | None = None
    with tempfile.TemporaryDirectory(
        dir=output_directory.parent, prefix=f".{output_directory.name}."
    ) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir(mode=0o700)
        request_name = "cyber_fetch_request.json"
        _write(staging / request_name, request_raw)
        specifications = [
            (
                "nvd_cve",
                f"nvd-{cve_id}.json",
                f"{NVD_CVE_API_URL}?cveId={cve_id}",
                cve_id,
            )
            for cve_id in request["cve_ids"]
        ]
        specifications.append(
            ("cisa_kev", "cisa-known-exploited-vulnerabilities.json", CISA_KEV_URL, "")
        )
        for kind, filename, url, cve_id in specifications:
            response = downloader.get(url)
            observed_at = clock()
            _parse_timestamp(observed_at, "cyber retrieval observed_at")
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
            if kind == "nvd_cve":
                nvd_by_id[cve_id] = response.body
            else:
                kev_raw = response.body
        if kev_raw is None:
            raise ProvenanceError("CISA KEV response is missing")
        parse_cyber_source_records(request["cve_ids"], nvd_by_id, kev_raw)
        completed_at = clock()
        started_time = _parse_timestamp(started_at, "cyber fetch started_at")
        completed_time = _parse_timestamp(completed_at, "cyber fetch completed_at")
        observed_times = [
            _parse_timestamp(item["observed_at"], "cyber retrieval observed_at")
            for item in retrievals
        ]
        if not (
            started_time < observed_times[0]
            and all(prior < current for prior, current in pairwise(observed_times))
            and observed_times[-1] < completed_time
        ):
            raise ProvenanceError("cyber fetch observation timeline is invalid")
        request_sha256 = hashlib.sha256(request_raw).hexdigest()
        inventory = {
            "schema_version": CYBER_FETCH_INVENTORY_SCHEMA,
            "source_status": "public_api_export",
            "data_stage": "source_inventory",
            "hybrid_train_ready": False,
            "production_eligible": False,
            "generation_integration": "disabled",
            "semantic_facts_train_ready": False,
            "snapshot_semantics": "current_state_observed_at_fetch",
            "historical_event_claims": False,
            "generated_at": started_at,
            "request_file": request_name,
            "request_sha256": request_sha256,
            "authorization": request["authorization"],
            "fetch_receipt": {
                "started_at": started_at,
                "completed_at": completed_at,
                "requests_per_second": request["requests_per_second"],
                "max_retries": request["max_retries"],
                "allowed_actions": request["authorization"]["allowed_actions"],
                "request_file": request_name,
                "request_sha256": request_sha256,
                "user_agent_sha256": hashlib.sha256(
                    request["user_agent"].encode()
                ).hexdigest(),
                "source_policies": {
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
                },
                "retrievals": retrievals,
            },
            "n_retrievals": len(retrievals),
        }
        _write(staging / "cyber_fetch_inventory.json", _canonical_bytes(inventory))
        os.replace(staging, output_directory)
    return output_directory / "cyber_fetch_inventory.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(fetch_cyber_workflow(args.request, args.out_dir))


if __name__ == "__main__":
    main()
