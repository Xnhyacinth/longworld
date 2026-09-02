#!/usr/bin/env python3
"""Fetch one bounded Microsoft FY2022--FY2025 issuer filing history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.filingworkflow import (
    MAX_ISSUER_GCS_DETAIL_BYTES,
    SEC_FILING_INPUT_SCHEMA,
    build_sec_filing_manifest,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

REQUEST_SCHEMA = "longworld.microsoft-gcs-annual-fetch-request.v1"
GCS_HOST = "microsoft.gcs-web.com"
EXPECTED_FISCAL_YEARS = (2022, 2023, 2024, 2025)
EXPECTED_CIK = "0000789019"
EXPECTED_FORM = "10-K"
MAX_REQUESTS_PER_SECOND = 5.0
MAX_TIMEOUT_SECONDS = 120.0
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_USER_AGENT = re.compile(r"^\S(?:.*\S)?\s+[\w.+-]+@[\w.-]+$")
_CHALLENGE_MARKERS = (
    b"<title>just a moment",
    b"/cdn-cgi/challenge-platform",
    b'id="challenge-form"',
    b"captcha-delivery",
    b"akamai bot manager",
)


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    final_url: str
    status: int
    headers: Mapping[str, str]


HttpGet = Callable[[str, dict[str, str], float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(
                body=response.read(MAX_SOURCE_BYTES + 1),
                final_url=response.geturl(),
                status=response.status,
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            raise ProvenanceError(
                "Microsoft GCS fetch rejected an HTTP redirect"
            ) from error
        raise ProvenanceError(
            f"Microsoft GCS fetch failed with HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        raise ProvenanceError(f"Microsoft GCS fetch failed: {error.reason}") from error


def _read_request(path: Path) -> dict[str, Any]:
    try:
        raw = _read_regular_file(path, MAX_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read Microsoft GCS request: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != REQUEST_SCHEMA:
        raise ProvenanceError("unsupported Microsoft GCS request schema")
    return payload


def _validate_authorization(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProvenanceError("Microsoft GCS authorization receipt is invalid")
    fields = ("record_id", "scope", "basis", "reviewed_at")
    receipt = {field: str(value.get(field) or "").strip() for field in fields}
    if any(not receipt[field] for field in fields):
        raise ProvenanceError("Microsoft GCS authorization receipt is invalid")
    _parse_timestamp(receipt["reviewed_at"], "authorization.reviewed_at")
    return receipt


def _validate_url(value: object, expected: str) -> str:
    url = str(value or "")
    parsed = urlparse(url)
    if (
        url != expected
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GCS_HOST
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ProvenanceError("Microsoft GCS filing URL is invalid")
    return url


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    user_agent = str(payload.get("user_agent") or "").strip()
    if len(user_agent) > 256 or _USER_AGENT.fullmatch(user_agent) is None:
        raise ProvenanceError("Microsoft GCS fetch User-Agent is invalid")
    authorization = _validate_authorization(payload.get("authorization"))
    rate = payload.get("requests_per_second")
    timeout = payload.get("timeout_seconds")
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= MAX_REQUESTS_PER_SECOND
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < float(timeout) <= MAX_TIMEOUT_SECONDS
    ):
        raise ProvenanceError("Microsoft GCS fetch bounds are invalid")
    raw_filings = payload.get("filings")
    if not isinstance(raw_filings, list) or len(raw_filings) != len(
        EXPECTED_FISCAL_YEARS
    ):
        raise ProvenanceError("Microsoft GCS request must contain four annual filings")

    filings: list[dict[str, Any]] = []
    seen_accessions: set[str] = set()
    seen_nodes: set[int] = set()
    for raw, expected_year in zip(raw_filings, EXPECTED_FISCAL_YEARS, strict=True):
        if not isinstance(raw, dict):
            raise ProvenanceError("Microsoft GCS filing request is invalid")
        fiscal_year = raw.get("fiscal_year")
        node_id = raw.get("node_id")
        accession = str(raw.get("accession") or "")
        cik = str(raw.get("cik") or "")
        form = str(raw.get("form") or "")
        filing_date = str(raw.get("filing_date") or "")
        report_date = str(raw.get("report_date") or "")
        primary_document = str(raw.get("primary_document") or "")
        if (
            fiscal_year != expected_year
            or isinstance(node_id, bool)
            or not isinstance(node_id, int)
            or not 1 <= node_id <= 10**12
            or _ACCESSION.fullmatch(accession) is None
            or cik != EXPECTED_CIK
            or form != EXPECTED_FORM
            or report_date != f"{expected_year}-06-30"
            or primary_document
            != (
                "msft-10k_20220630.htm"
                if expected_year == 2022
                else f"msft-{expected_year}0630.htm"
            )
        ):
            raise ProvenanceError("Microsoft GCS filing identity is invalid")
        try:
            filing_day = date.fromisoformat(filing_date)
        except ValueError as error:
            raise ProvenanceError("Microsoft GCS filing date is invalid") from error
        if filing_day.year != expected_year:
            raise ProvenanceError("Microsoft GCS filing date is invalid")
        if accession in seen_accessions or node_id in seen_nodes:
            raise ProvenanceError("Microsoft GCS filing identities are duplicated")
        detail_url = _validate_url(
            raw.get("detail_url"),
            f"https://{GCS_HOST}/sec-filings/sec-filing/10-k/{accession}",
        )
        merged_url = _validate_url(
            raw.get("merged_url"), f"https://{GCS_HOST}/node/{node_id}/html"
        )
        filings.append(
            {
                "fiscal_year": expected_year,
                "accession": accession,
                "cik": cik,
                "form": form,
                "filing_date": filing_date,
                "report_date": report_date,
                "primary_document": primary_document,
                "node_id": node_id,
                "detail_url": detail_url,
                "merged_url": merged_url,
            }
        )
        seen_accessions.add(accession)
        seen_nodes.add(node_id)
    return {
        "user_agent": user_agent,
        "authorization": authorization,
        "requests_per_second": float(rate),
        "timeout_seconds": float(timeout),
        "filings": filings,
    }


def _validated_html(
    response: HttpResponse, *, requested_url: str, max_bytes: int
) -> str:
    content_type = str(response.headers.get("Content-Type") or "").lower()
    lowered = response.body[:256_000].lower()
    if response.final_url != requested_url:
        raise ProvenanceError("Microsoft GCS fetch rejected a redirect")
    if response.status != 200:
        raise ProvenanceError(f"Microsoft GCS fetch failed with HTTP {response.status}")
    if not content_type.startswith("text/html"):
        raise ProvenanceError("Microsoft GCS response content type is invalid")
    if not response.body or len(response.body) > max_bytes:
        raise ProvenanceError("Microsoft GCS response size is invalid")
    if any(marker in lowered for marker in _CHALLENGE_MARKERS):
        raise ProvenanceError("Microsoft GCS response is a challenge page")
    try:
        text = response.body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProvenanceError("Microsoft GCS response is not UTF-8") from error
    if "<html" not in text.lower():
        raise ProvenanceError("Microsoft GCS response body is not HTML")
    return text


def _fact(text: str, *, fact_id: str, field: str, value: str, quote: str) -> dict:
    try:
        start = text.index(quote)
    except ValueError as error:
        raise ProvenanceError(
            f"Microsoft GCS merged body is missing {field} identity"
        ) from error
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": quote,
        "evidence_char_start": start,
    }


def _pattern_fact(
    text: str, *, fact_id: str, field: str, value: str, pattern: str
) -> dict:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match is None:
        raise ProvenanceError(f"Microsoft GCS merged body is missing {field} identity")
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": match.group(0),
        "evidence_char_start": match.start(),
    }


def _filing_input(filing: dict[str, Any], merged: str, detail: str) -> dict[str, Any]:
    primary_stem = filing["primary_document"].removesuffix(".htm")
    facts = [
        _pattern_fact(
            merged,
            fact_id="accession",
            field="accession",
            value=filing["accession"],
            pattern=(r'title="' + re.escape(filing["accession"]) + r'\.pdf"'),
        ),
        _pattern_fact(
            merged,
            fact_id="cik",
            field="cik",
            value=filing["cik"],
            pattern=(
                r'<ix:nonNumeric\b[^>]*\bname="dei:EntityCentralIndexKey"[^>]*>'
                + r"[\s\S]{0,2000}?"
                + re.escape(filing["cik"])
                + r"[\s\S]{0,2000}?</ix:nonNumeric>"
            ),
        ),
        _pattern_fact(
            merged,
            fact_id="form",
            field="form",
            value=filing["form"],
            pattern=(
                r'<ix:nonNumeric\b[^>]*\bname="dei:DocumentType"[^>]*>'
                + r"[\s\S]{0,2000}?"
                + re.escape(filing["form"])
                + r"[\s\S]{0,2000}?</ix:nonNumeric>"
            ),
        ),
        _pattern_fact(
            merged,
            fact_id="filing_date",
            field="filing_date",
            value=filing["filing_date"],
            pattern=(
                r"<!--\s*Creation Date\s*:\s*"
                + re.escape(filing["filing_date"])
                + r"T[^>]{1,100}-->"
            ),
        ),
        _pattern_fact(
            merged,
            fact_id="report_date",
            field="report_date",
            value=filing["report_date"],
            pattern=(
                r"<xbrli:endDate>"
                + re.escape(filing["report_date"])
                + r"</xbrli:endDate>"
            ),
        ),
        _pattern_fact(
            merged,
            fact_id="primary_document_stem",
            field="primary_document_stem",
            value=primary_stem,
            pattern=(
                r"<title>[\s]*" + re.escape(primary_stem) + r"\.htm[\s]*</title>"
                if filing["fiscal_year"] <= 2022
                else r'xlink:href="#' + re.escape(primary_stem) + r'\.xsd"'
            ),
        ),
    ]
    merged_raw = merged.encode()
    detail_raw = detail.encode()
    return {
        "accession": filing["accession"],
        "cik": filing["cik"],
        "form": filing["form"],
        "filing_date": filing["filing_date"],
        "report_date": filing["report_date"],
        "primary_document": filing["primary_document"],
        "source_url": filing["merged_url"],
        "source_file": f"msft-{filing['fiscal_year']}0630.html",
        "source_sha256": hashlib.sha256(merged_raw).hexdigest(),
        "detail_url": filing["detail_url"],
        "detail_retrieval_url": f"{filing['detail_url']}?output=1",
        "detail_file": f"detail-{filing['fiscal_year']}.html",
        "detail_sha256": hashlib.sha256(detail_raw).hexdigest(),
        "retrieved_at": "",
        "access_policy": "public issuer-owned Microsoft investor-relations export",
        "parser": {"name": "issuer_gcs_merged_html", "version": "2"},
        "derived_facts": facts,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def fetch_microsoft_gcs_annual_history(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
) -> Path:
    """Fetch, validate, and atomically publish the unsigned four-filing input."""
    request = _validate_request(_read_request(request_path))
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    _parse_timestamp(timestamp, "generated_at")
    if output_directory.exists():
        raise ProvenanceError("Microsoft GCS output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.", dir=output_directory.parent
        )
    )
    try:
        exported = []
        headers = {"User-Agent": request["user_agent"], "Accept": "text/html"}
        minimum_interval = 1.0 / request["requests_per_second"]
        for filing_index, filing in enumerate(request["filings"]):
            if filing_index:
                sleep(minimum_interval)
            merged_response = http_get(
                filing["merged_url"], headers, request["timeout_seconds"]
            )
            merged = _validated_html(
                merged_response,
                requested_url=filing["merged_url"],
                max_bytes=MAX_SOURCE_BYTES,
            )
            sleep(minimum_interval)
            detail_retrieval_url = f"{filing['detail_url']}?output=1"
            detail_response = http_get(
                detail_retrieval_url, headers, request["timeout_seconds"]
            )
            detail = _validated_html(
                detail_response,
                requested_url=detail_retrieval_url,
                max_bytes=MAX_ISSUER_GCS_DETAIL_BYTES,
            )
            item = _filing_input(filing, merged, detail)
            item["retrieved_at"] = timestamp
            _atomic_write(temporary_directory / item["source_file"], merged.encode())
            _atomic_write(temporary_directory / item["detail_file"], detail.encode())
            exported.append(item)
        payload = {
            "schema_version": SEC_FILING_INPUT_SCHEMA,
            "source_status": "issuer_owned_public_export",
            "authorization": request["authorization"],
            "filings": exported,
        }
        build_sec_filing_manifest(payload, temporary_directory, generated_at=timestamp)
        input_name = "sec_filing_input.json"
        _atomic_write(
            temporary_directory / input_name,
            json.dumps(payload, ensure_ascii=False, indent=2).encode(),
        )
        os.replace(temporary_directory, output_directory)
        return output_directory / input_name
    except BaseException:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(fetch_microsoft_gcs_annual_history(args.request, args.out_dir))


if __name__ == "__main__":
    main()
