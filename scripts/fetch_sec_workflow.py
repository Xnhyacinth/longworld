#!/usr/bin/env python3
"""Fetch a bounded, allowlisted set of public EDGAR complete submissions.

The output is an unsigned ``longworld.sec-filing-input.v1`` payload for
``export_sec_workflow.py``.  Downloading and source attestation intentionally
remain separate steps, and neither step enables generation or production use.
"""

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
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.filingworkflow import MAX_SEC_FILINGS, SEC_FILING_INPUT_SCHEMA
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

SEC_FETCH_REQUEST_SCHEMA = "longworld.sec-fetch-request.v1"
SEC_FAIR_ACCESS_URL = (
    "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data"
)
DEFAULT_REQUESTS_PER_SECOND = 5.0
SEC_REQUESTS_PER_SECOND_CEILING = 10.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_CIKS = 128
MAX_FORMS = 32

_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_FORM = re.compile(r"^[A-Z0-9-]+(?:/A)?$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

HttpGet = Callable[[str, dict[str, str], float], tuple[bytes, Mapping[str, str]]]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[bytes, Mapping[str, str]]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_SOURCE_BYTES + 1), dict(response.headers.items())


def _valid_user_agent(value: object) -> str:
    user_agent = str(value or "").strip()
    if (
        len(user_agent) > 256
        or _CONTACT_USER_AGENT.fullmatch(user_agent) is None
        or any(
            domain in user_agent.lower()
            for domain in ("example.com", "example.net", "example.org")
        )
    ):
        raise ProvenanceError(
            "SEC fetch requires a declared organization and contact email User-Agent"
        )
    return user_agent


def _positive_int(value: object, field: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ProvenanceError(f"SEC fetch {field} is invalid")
    return value


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SEC_FETCH_REQUEST_SCHEMA:
        raise ProvenanceError("unsupported SEC fetch request schema")
    user_agent = _valid_user_agent(payload.get("user_agent"))
    authorization = payload.get("authorization")
    if not isinstance(authorization, dict):
        raise ProvenanceError("SEC fetch authorization receipt is invalid")
    required = ("record_id", "scope", "basis", "reviewed_at")
    authorization = {
        field: str(authorization.get(field) or "").strip() for field in required
    }
    if any(not authorization[field] for field in required):
        raise ProvenanceError("SEC fetch authorization receipt is invalid")
    _parse_timestamp(authorization["reviewed_at"], "authorization.reviewed_at")

    raw_ciks = payload.get("allowed_ciks")
    if not isinstance(raw_ciks, list) or not raw_ciks or len(raw_ciks) > MAX_CIKS:
        raise ProvenanceError("SEC fetch allowed_ciks is invalid")
    ciks = [str(value) for value in raw_ciks]
    if any(_CIK.fullmatch(cik) is None for cik in ciks) or len(set(ciks)) != len(ciks):
        raise ProvenanceError("SEC fetch allowed_ciks is invalid")

    raw_forms = payload.get("forms")
    if not isinstance(raw_forms, list) or not raw_forms or len(raw_forms) > MAX_FORMS:
        raise ProvenanceError("SEC fetch forms is invalid")
    forms = [str(value) for value in raw_forms]
    if any(_FORM.fullmatch(form) is None for form in forms) or len(set(forms)) != len(
        forms
    ):
        raise ProvenanceError("SEC fetch forms is invalid")

    max_filings = _positive_int(
        payload.get("max_filings_per_cik"),
        "max_filings_per_cik",
        maximum=32,
    )
    if len(ciks) * max_filings > MAX_SEC_FILINGS:
        raise ProvenanceError("SEC fetch request exceeds the filing manifest limit")
    rate = payload.get("requests_per_second", DEFAULT_REQUESTS_PER_SECOND)
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= SEC_REQUESTS_PER_SECOND_CEILING
    ):
        raise ProvenanceError("SEC fetch requests_per_second is invalid")
    retries = payload.get("max_retries", DEFAULT_MAX_RETRIES)
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("SEC fetch max_retries is invalid")
    return {
        "user_agent": user_agent,
        "authorization": authorization,
        "allowed_ciks": ciks,
        "forms": forms,
        "max_filings_per_cik": max_filings,
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
        self.user_agent = user_agent
        self.minimum_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.http_get = http_get
        self.sleep = sleep
        self.last_request_at: float | None = None

    def _throttle(self) -> None:
        now = time.monotonic()
        if self.last_request_at is not None:
            remaining = self.minimum_interval - (now - self.last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self.last_request_at = time.monotonic()

    def _request(self, url: str) -> tuple[bytes, Mapping[str, str]]:
        headers = {
            "User-Agent": self.user_agent,
        }
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                body, response_headers = self.http_get(
                    url, headers, DEFAULT_TIMEOUT_SECONDS
                )
                if not body or len(body) > MAX_SOURCE_BYTES:
                    raise ProvenanceError("SEC response is empty or exceeds size limit")
                return body, response_headers
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRYABLE_STATUS or attempt == self.max_retries:
                    raise ProvenanceError(
                        f"SEC request failed with HTTP {exc.code}"
                    ) from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    if retry_after is None:
                        raise ValueError
                    delay = min(max(float(retry_after), 0.0), 60.0)
                except (TypeError, ValueError):
                    delay = 2.0**attempt
                self.sleep(delay)
            except urllib.error.URLError as exc:
                if attempt == self.max_retries:
                    raise ProvenanceError(f"SEC request failed: {exc.reason}") from exc
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")

    def cached(self, url: str, path: Path, *, retrieved_at: str) -> tuple[bytes, str]:
        """Fetch from SEC and atomically replace the local evidence cache.

        A writable local cache is not source provenance, so every invocation
        performs an online request.  The sidecar remains useful for auditing
        bytes already returned by SEC, but can never suppress revalidation.
        """
        metadata_path = path.with_suffix(path.suffix + ".download.json")
        body, headers = self._request(url)
        digest = hashlib.sha256(body).hexdigest()
        metadata = {
            "schema_version": "longworld.sec-download-cache.v1",
            "url": url,
            "sha256": digest,
            "retrieved_at": retrieved_at,
            "user_agent_sha256": hashlib.sha256(self.user_agent.encode()).hexdigest(),
            "etag": str(headers.get("ETag") or ""),
            "last_modified": str(headers.get("Last-Modified") or ""),
        }
        _atomic_write(path, body)
        _atomic_write(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode(),
        )
        return body, retrieved_at


def _recent_rows(payload: dict[str, Any], cik: str) -> list[dict[str, str]]:
    if str(payload.get("cik") or "").zfill(10) != cik:
        raise ProvenanceError("SEC submissions response CIK mismatch")
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise ProvenanceError("SEC submissions response has no recent filings")
    fields = ("accessionNumber", "filingDate", "reportDate", "form")
    columns = [recent.get(field) for field in fields]
    if any(not isinstance(column, list) for column in columns):
        raise ProvenanceError("SEC submissions response columns are invalid")
    lengths = {len(column) for column in columns if isinstance(column, list)}
    if len(lengths) != 1:
        raise ProvenanceError("SEC submissions response columns have unequal lengths")
    rows: list[dict[str, str]] = []
    for values in zip(*columns):
        if any(not isinstance(value, str) for value in values):
            raise ProvenanceError("SEC submissions response row is invalid")
        rows.append(dict(zip(fields, values, strict=True)))
    return rows


def _validate_row(row: dict[str, str], cik: str) -> None:
    if _ACCESSION.fullmatch(row["accessionNumber"]) is None:
        raise ProvenanceError("SEC submissions response accession is invalid")
    if row["accessionNumber"].split("-", 1)[0] != cik:
        raise ProvenanceError("SEC submissions response accession does not match CIK")
    if _FORM.fullmatch(row["form"]) is None:
        raise ProvenanceError("SEC submissions response form is invalid")
    try:
        date.fromisoformat(row["filingDate"])
        if row["reportDate"]:
            date.fromisoformat(row["reportDate"])
    except ValueError as exc:
        raise ProvenanceError("SEC submissions response date is invalid") from exc


def _header_fact(
    text: str,
    *,
    fact_id: str,
    field: str,
    label: str,
    expected: str,
) -> dict[str, Any]:
    pattern = re.compile(
        rf"^[ \t]*{re.escape(label)}:[ \t]*(\S.*?)[ \t]*\r?$", re.MULTILINE
    )
    matches = [match for match in pattern.finditer(text) if match.group(1) == expected]
    if len(matches) != 1:
        raise ProvenanceError(f"SEC complete submission does not uniquely bind {field}")
    match = matches[0]
    return {
        "fact_id": fact_id,
        "field": field,
        "value": expected,
        "evidence_quote": match.group(0),
        "evidence_char_start": match.start(),
    }


def _derived_header_facts(text: str, row: dict[str, str]) -> list[dict[str, Any]]:
    facts = [
        _header_fact(
            text,
            fact_id="accession",
            field="accession",
            label="ACCESSION NUMBER",
            expected=row["accessionNumber"],
        ),
        _header_fact(
            text,
            fact_id="form",
            field="form",
            label="CONFORMED SUBMISSION TYPE",
            expected=row["form"],
        ),
        _header_fact(
            text,
            fact_id="filing_date",
            field="filing_date",
            label="FILED AS OF DATE",
            expected=row["filingDate"].replace("-", ""),
        ),
    ]
    if row["reportDate"]:
        facts.append(
            _header_fact(
                text,
                fact_id="report_date",
                field="report_date",
                label="CONFORMED PERIOD OF REPORT",
                expected=row["reportDate"].replace("-", ""),
            )
        )
    return facts


def fetch_sec_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
) -> Path:
    """Fetch allowlisted public filings and return the unsigned input path."""
    try:
        raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        request_payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read SEC fetch request: {exc}") from exc
    if not isinstance(request_payload, dict):
        raise ProvenanceError("SEC fetch request must be an object")
    request = _validate_request(request_payload)
    timestamp = generated_at or _timestamp()
    _parse_timestamp(timestamp, "generated_at")
    output_directory.mkdir(parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        requests_per_second=request["requests_per_second"],
        max_retries=request["max_retries"],
        http_get=http_get,
        sleep=sleep,
    )

    exported: list[dict[str, Any]] = []
    seen_accessions: set[str] = set()
    for cik in request["allowed_ciks"]:
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        submissions_path = output_directory / f"CIK{cik}.submissions.json"
        submissions_raw, _ = downloader.cached(
            submissions_url, submissions_path, retrieved_at=timestamp
        )
        try:
            submissions = json.loads(submissions_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProvenanceError("SEC submissions response is invalid JSON") from exc
        if not isinstance(submissions, dict):
            raise ProvenanceError("SEC submissions response must be an object")
        rows = [
            row
            for row in _recent_rows(submissions, cik)
            if row["form"] in request["forms"]
        ][: request["max_filings_per_cik"]]
        if not rows:
            raise ProvenanceError(
                f"SEC submissions response has no selected filing for {cik}"
            )
        for row in rows:
            _validate_row(row, cik)
            accession = row["accessionNumber"]
            if accession in seen_accessions:
                raise ProvenanceError(
                    "SEC submissions response duplicates an accession"
                )
            seen_accessions.add(accession)
            accession_directory = accession.replace("-", "")
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_directory}/{accession}.txt"
            )
            source_path = output_directory / f"{accession}.txt"
            source_raw, retrieved_at = downloader.cached(
                source_url, source_path, retrieved_at=timestamp
            )
            try:
                source_text = source_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProvenanceError(
                    "SEC complete submission is not UTF-8; preserve it outside this v1 contract"
                ) from exc
            exported.append(
                {
                    "accession": accession,
                    "cik": cik,
                    "form": row["form"],
                    "filing_date": row["filingDate"],
                    "report_date": row["reportDate"],
                    "source_url": source_url,
                    "source_file": source_path.name,
                    "source_sha256": hashlib.sha256(source_raw).hexdigest(),
                    "retrieved_at": retrieved_at,
                    "access_policy": (
                        "SEC public EDGAR fair access; bounded allowlisted fetch; "
                        f"policy {SEC_FAIR_ACCESS_URL}"
                    ),
                    "parser": {
                        "name": "sec_complete_submission_header",
                        "version": "1",
                    },
                    "derived_facts": _derived_header_facts(source_text, row),
                }
            )

    payload = {
        "schema_version": SEC_FILING_INPUT_SCHEMA,
        "source_status": "public_sec_download",
        "authorization": request["authorization"],
        "fetch_receipt": {
            "schema_version": "longworld.sec-fetch-receipt.v1",
            "generated_at": timestamp,
            "policy_url": SEC_FAIR_ACCESS_URL,
            "requests_per_second": request["requests_per_second"],
            "max_retries": request["max_retries"],
            "user_agent_sha256": hashlib.sha256(
                request["user_agent"].encode()
            ).hexdigest(),
        },
        "filings": exported,
    }
    input_path = output_directory / "sec_filing_input.json"
    _atomic_write(
        input_path,
        json.dumps(payload, ensure_ascii=False, indent=2).encode(),
    )
    return input_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch bounded public EDGAR complete submissions for later attestation"
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    fetch_sec_workflow(args.request, args.out_dir)


if __name__ == "__main__":
    main()
