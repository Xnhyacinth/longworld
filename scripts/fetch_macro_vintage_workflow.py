#!/usr/bin/env python3
"""Fetch one bounded BEA GDP/GDI vintage workbook for a local probe."""

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

from longworld.core.macrovintageworkflow import (
    BEA_GDP_GDI_VINTAGE_XLSX_URL,
    BEA_LICENSE,
    BEA_TERMS_URL,
    BEA_XLSX_CONTENT_TYPE,
    MACRO_VINTAGE_FETCH_INVENTORY_SCHEMA,
    MACRO_VINTAGE_FETCH_REQUEST_SCHEMA,
    MAX_XLSX_BYTES,
    parse_bea_vintage_xlsx,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

_SOURCE_FILENAME = "bea-gdp-gdi-vintage-history.xlsx"
_REQUEST_FILENAME = "macro_vintage_fetch_request.json"
_ALLOWED_ACTION = "fetch_bea_gdp_gdi_vintage_xlsx"
_RETRYABLE = {429, 500, 502, 503, 504}
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    status: int
    final_url: str
    content_type: str
    last_modified: str
    etag: str


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
            body=response.read(MAX_XLSX_BYTES + 1),
            status=response.status,
            final_url=response.geturl(),
            content_type=response.headers.get_content_type(),
            last_modified=response.headers.get("Last-Modified", ""),
            etag=response.headers.get("ETag", ""),
        )


def _authorization(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("macro vintage fetch authorization is invalid")
    result: dict[str, Any] = {
        field: str(value.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    }
    if any(not item or len(item) > 512 for item in result.values()):
        raise ProvenanceError("macro vintage fetch authorization is invalid")
    _parse_timestamp(result["reviewed_at"], "authorization.reviewed_at")
    actions = value.get("allowed_actions")
    if actions != [_ALLOWED_ACTION]:
        raise ProvenanceError("macro vintage fetch authorization actions are invalid")
    result["allowed_actions"] = list(actions)
    return result


def validate_macro_vintage_fetch_request(value: object) -> dict[str, Any]:
    """Validate a single-URL, credential-free BEA fetch request."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "user_agent",
            "authorization",
            "url",
            "max_retries",
        }
        or value.get("schema_version") != MACRO_VINTAGE_FETCH_REQUEST_SCHEMA
        or value.get("url") != BEA_GDP_GDI_VINTAGE_XLSX_URL
    ):
        raise ProvenanceError("macro vintage fetch request schema is invalid")
    user_agent = str(value.get("user_agent") or "").strip()
    retries = value.get("max_retries")
    if len(user_agent) > 256 or _CONTACT_USER_AGENT.fullmatch(user_agent) is None:
        raise ProvenanceError("macro vintage fetch requires a contact User-Agent")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 3
    ):
        raise ProvenanceError("macro vintage fetch max_retries is invalid")
    return {
        "user_agent": user_agent,
        "authorization": _authorization(value.get("authorization")),
        "url": BEA_GDP_GDI_VINTAGE_XLSX_URL,
        "max_retries": retries,
    }


def _safe_header(value: str, label: str) -> str:
    if len(value) > 512 or any(character in "\r\n" for character in value):
        raise ProvenanceError(f"macro vintage {label} response header is invalid")
    return value


def _download(
    request: dict[str, Any],
    *,
    http_get: HttpGet,
    sleep: Callable[[float], None],
) -> HttpResponse:
    headers = {
        "Accept": BEA_XLSX_CONTENT_TYPE,
        "User-Agent": request["user_agent"],
    }
    for attempt in range(request["max_retries"] + 1):
        try:
            response = http_get(request["url"], headers, 90.0)
            if not isinstance(response, HttpResponse):
                raise ProvenanceError("macro vintage client omitted response metadata")
            content_type = response.content_type.partition(";")[0].strip().lower()
            if response.status in _RETRYABLE and attempt < request["max_retries"]:
                sleep(2.0**attempt)
                continue
            if (
                response.status != 200
                or response.final_url != request["url"]
                or content_type != BEA_XLSX_CONTENT_TYPE
                or not isinstance(response.body, bytes)
                or not response.body
                or len(response.body) > MAX_XLSX_BYTES
            ):
                raise ProvenanceError(
                    "macro vintage response metadata or bytes are invalid"
                )
            return HttpResponse(
                body=response.body,
                status=200,
                final_url=request["url"],
                content_type=content_type,
                last_modified=_safe_header(response.last_modified, "Last-Modified"),
                etag=_safe_header(response.etag, "ETag"),
            )
        except urllib.error.HTTPError as error:
            if error.code not in _RETRYABLE or attempt == request["max_retries"]:
                raise ProvenanceError(
                    f"macro vintage request failed with HTTP {error.code}"
                ) from error
            sleep(2.0**attempt)
        except urllib.error.URLError as error:
            if attempt == request["max_retries"]:
                raise ProvenanceError(
                    f"macro vintage request failed: {error.reason}"
                ) from error
            sleep(2.0**attempt)
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


def fetch_macro_vintage_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
    clock: Callable[[], str] = _timestamp,
) -> Path:
    """Fetch, validate, and persist one disabled raw-response-bound inventory."""
    try:
        request_raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read macro vintage fetch request: {error}"
        ) from error
    request = validate_macro_vintage_fetch_request(request_payload)
    started_at = generated_at or clock()
    _parse_timestamp(started_at, "macro vintage fetch started_at")
    if os.path.lexists(output_directory):
        raise ProvenanceError("macro vintage fetch output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    response = _download(request, http_get=http_get, sleep=sleep)
    parsed = parse_bea_vintage_xlsx(response.body)
    observed_at = clock()
    completed_at = clock()
    times = [
        _parse_timestamp(started_at, "macro vintage fetch started_at"),
        _parse_timestamp(observed_at, "macro vintage fetch observed_at"),
        _parse_timestamp(completed_at, "macro vintage fetch completed_at"),
    ]
    if any(prior >= current for prior, current in pairwise(times)):
        raise ProvenanceError("macro vintage fetch timeline is invalid")
    with tempfile.TemporaryDirectory(
        dir=output_directory.parent, prefix=f".{output_directory.name}."
    ) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir(mode=0o700)
        _write(staging / _REQUEST_FILENAME, request_raw)
        _write(staging / _SOURCE_FILENAME, response.body)
        retrieval = {
            "requested_url": BEA_GDP_GDI_VINTAGE_XLSX_URL,
            "final_url": response.final_url,
            "status": response.status,
            "content_type": response.content_type,
            "redirect_chain": [],
            "observed_at": observed_at,
            "sha256": parsed.source_sha256,
            "retrieval_file": _SOURCE_FILENAME,
            "raw_bytes": len(response.body),
            "last_modified": response.last_modified,
            "etag": response.etag,
        }
        inventory = {
            "schema_version": MACRO_VINTAGE_FETCH_INVENTORY_SCHEMA,
            "source_status": "local_probe_public_download",
            "trust_scope": "local_probe",
            "diagnostic_only": True,
            "data_stage": "source_inventory",
            "hybrid_train_ready": False,
            "production_eligible": False,
            "generation_integration": "disabled",
            "semantic_facts_train_ready": False,
            "generated_at": started_at,
            "request_file": _REQUEST_FILENAME,
            "request_sha256": hashlib.sha256(request_raw).hexdigest(),
            "authorization": request["authorization"],
            "source_policies": {
                "bea": {"terms_url": BEA_TERMS_URL, "license": BEA_LICENSE}
            },
            "source_file": _SOURCE_FILENAME,
            "source_sha256": parsed.source_sha256,
            "raw_bytes": len(response.body),
            "fetch_receipt": {
                "started_at": started_at,
                "completed_at": completed_at,
                "retrieval": retrieval,
            },
            "n_retrievals": 1,
        }
        _write(staging / "fetch_inventory.json", _canonical_bytes(inventory))
        os.replace(staging, output_directory)
    return output_directory / "fetch_inventory.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    fetch_macro_vintage_workflow(args.request, args.out_dir)


if __name__ == "__main__":
    main()
