#!/usr/bin/env python3
"""Fetch issuer-owned filing artifacts into a disabled, auditable inventory.

This is an acquisition contract, not a substitute for an SEC complete submission.
It verifies that each downloaded artifact is linked by the issuer's own filing-detail
page and that the XBRL instance binds the expected CIK, form, and report date.  The
result deliberately remains generation-disabled until a separate source adapter can
replay the issuer artifacts into Company state without pretending they came from
``sec.gov``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.filingworkflow import _EMAIL
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

ISSUER_IR_FETCH_REQUEST_SCHEMA = "longworld.issuer-ir-fetch-request.v1"
ISSUER_IR_INVENTORY_SCHEMA = "longworld.issuer-ir-inventory.v1"
DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_FILINGS = 16
MAX_RETRIES = 8
MAX_REQUESTS_PER_SECOND = 10.0

_CIK = re.compile(r"^\d{10}$")
_FILING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_FORM = re.compile(r"^[A-Z0-9-]+(?:/A)?$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_CHALLENGE_MARKERS = (
    b"attention required!",
    b"cloudflare ray id",
    b"your request originates from an undeclared automated tool",
    b"captcha",
)
_VOLATILE_JWT = re.compile(
    rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
_ARTIFACTS = {
    "annual_report_pdf": ("_ctrl0_ctl54_hrefItemPdfDownload", ".pdf"),
    "xbrl_zip": ("_ctrl0_ctl54_hrefItemXBRLDownload", ".zip"),
    "rendered_xbrl_html": ("_ctrl0_ctl54_hrefItemXBRLHTMLDownload", ".html"),
}
_EVERGREEN_ARTIFACT_FORMATS = {
    "annual_report_pdf": "pdf",
    "xbrl_zip": "zip",
    "rendered_xbrl_html": "html",
}
_ISSUER_SOURCE_ALLOWLIST = {
    "0001018724": {
        "detail_host": "ir.aboutamazon.com",
        "detail_path": "/sec-filings/sec-filings-details/default.aspx",
        "artifact_host": "d18rn0p25nwr6d.cloudfront.net",
        "artifact_path_prefix": "/CIK-0001018724/",
        "detail_control_prefix": "_ctrl0_ctl54_",
        "ignore_subscribe_captcha": False,
    },
    "0001045810": {
        "detail_host": "investor.nvidia.com",
        "detail_path": ("/financial-info/sec-filings/sec-filings-details/default.aspx"),
        "artifact_host": "d18rn0p25nwr6d.cloudfront.net",
        "artifact_path_prefix": "/CIK-0001045810/",
        "detail_control_prefix": "_ctrl0_ctl78_",
        "ignore_subscribe_captcha": True,
    },
    "0001652044": {
        "detail_host": "abc.xyz",
        "detail_path": "/investor/sec-filings/sec-filings-details/default.aspx",
        "artifact_host": "d18rn0p25nwr6d.cloudfront.net",
        "artifact_path_prefix": "/CIK-0001652044/",
        "detail_control_prefix": "_ctrl0_ctl33_",
        "ignore_subscribe_captcha": False,
    },
    "0001326801": {
        "detail_host": "investor.atmeta.com",
        "detail_path": "/financials/sec-filings-details/default.aspx",
        "artifact_host": "d18rn0p25nwr6d.cloudfront.net",
        "artifact_path_prefix": "/CIK-0001326801/",
        "detail_control_prefix": "_ctrl0_ctl51_",
        "ignore_subscribe_captcha": False,
    },
    "0000723125": {
        "detail_host": "investors.micron.com",
        "detail_path": (
            "/financials/sec-filings/sec-filings-details/default.aspx"
        ),
        "artifact_host": "d18rn0p25nwr6d.cloudfront.net",
        "artifact_path_prefix": "/CIK-0000723125/",
        "detail_control_prefix": "_ctrl0_ctl28_",
        "ignore_subscribe_captcha": False,
        "detail_widget": "evergreen_sec_filing_details",
        "filing_date_strptime": "%B %d, %Y",
    },
}

HttpGet = Callable[[str, dict[str, str], float], tuple[bytes, Mapping[str, str]]]


class _DetailParser(HTMLParser):
    def __init__(
        self,
        control_prefix: str = "_ctrl0_ctl54_",
        detail_widget: str = "item_download",
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.control_prefix = control_prefix
        self.detail_widget = detail_widget
        self.form_parts: list[str] = []
        self.date_parts: list[str] = []
        self.artifact_urls: dict[str, str] = {}
        self._capture: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        identity = attributes.get("id")
        if self.detail_widget == "evergreen_sec_filing_details":
            if tag == "div" and identity == (
                self.control_prefix + "divSecFilingDetailsType"
            ):
                self._capture = self.form_parts
            elif tag == "div" and identity == (
                self.control_prefix + "divSecFilingDetailsDate"
            ):
                self._capture = self.date_parts
            elif tag == "a":
                href = str(attributes.get("href") or "").strip()
                label = str(attributes.get("aria-label") or "").casefold()
                if not href or "download / view" not in label:
                    return
                for role, fmt in _EVERGREEN_ARTIFACT_FORMATS.items():
                    if f"{fmt} format" not in label:
                        continue
                    if role in self.artifact_urls:
                        raise ProvenanceError(
                            "issuer IR detail page has invalid artifact URL"
                        )
                    self.artifact_urls[role] = href
            return
        if tag == "span" and identity == self.control_prefix + "lblForm":
            self._capture = self.form_parts
        elif tag == "span" and identity == self.control_prefix + "lblDate":
            self._capture = self.date_parts
        elif tag == "a" and identity:
            for role, (expected_id, _) in _ARTIFACTS.items():
                if identity == expected_id.replace(
                    "_ctrl0_ctl54_", self.control_prefix
                ):
                    href = str(attributes.get("href") or "").strip()
                    if role in self.artifact_urls or not href:
                        raise ProvenanceError(
                            "issuer IR detail page has invalid artifact URL"
                        )
                    self.artifact_urls[role] = href

    def handle_endtag(self, tag: str) -> None:
        if tag in {"span", "div"}:
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture.append(data)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[bytes, Mapping[str, str]]:
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_RejectRedirects())
    with opener.open(request, timeout=timeout) as response:
        return response.read(MAX_SOURCE_BYTES + 1), dict(response.headers.items())


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

    def get(self, url: str) -> tuple[bytes, Mapping[str, str]]:
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                body, headers = self.http_get(
                    url,
                    {"User-Agent": self.user_agent},
                    DEFAULT_TIMEOUT_SECONDS,
                )
                if not body or len(body) > MAX_SOURCE_BYTES:
                    raise ProvenanceError(
                        "issuer IR response is empty or exceeds size limit"
                    )
                return body, headers
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRYABLE_STATUS or attempt == self.max_retries:
                    raise ProvenanceError(
                        f"issuer IR request failed with HTTP {exc.code}"
                    ) from exc
                self.sleep(2.0**attempt)
            except urllib.error.URLError as exc:
                if attempt == self.max_retries:
                    raise ProvenanceError(
                        f"issuer IR request failed: {exc.reason}"
                    ) from exc
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")


def _host(value: object, field: str) -> str:
    host = str(value or "").strip().lower()
    if _HOST.fullmatch(host) is None:
        raise ProvenanceError(f"issuer IR {field} is invalid")
    return host


def _url(value: object, *, host: str, field: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != host:
        raise ProvenanceError(f"issuer IR {field} must use the allowlisted HTTPS host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProvenanceError(f"issuer IR {field} is invalid") from exc
    if parsed.username or parsed.password or port not in {None, 443}:
        raise ProvenanceError(f"issuer IR {field} is invalid")
    return url


def _detail_url(value: object, *, host: str, path: str) -> str:
    url = _url(value, host=host, field="detail_url")
    parsed = urlparse(url)
    if (
        parsed.path != path
        or parsed.params
        or re.fullmatch(r"FilingId=[1-9]\d{0,19}", parsed.query) is None
        or parsed.fragment
    ):
        raise ProvenanceError("issuer IR detail_url is outside the fixed path boundary")
    return url


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != ISSUER_IR_FETCH_REQUEST_SCHEMA:
        raise ProvenanceError("unsupported issuer IR fetch request schema")
    user_agent = str(payload.get("user_agent") or "").strip()
    if (
        len(user_agent) > 256
        or _CONTACT_USER_AGENT.fullmatch(user_agent) is None
        or any(
            domain in user_agent.lower()
            for domain in ("example.com", "example.net", "example.org")
        )
    ):
        raise ProvenanceError(
            "issuer IR fetch requires a declared organization and contact email User-Agent"
        )
    authorization = payload.get("authorization")
    if not isinstance(authorization, dict):
        raise ProvenanceError("issuer IR authorization receipt is invalid")
    required_receipt = ("record_id", "scope", "basis", "reviewed_at")
    authorization = {
        field: str(authorization.get(field) or "").strip() for field in required_receipt
    }
    if any(not authorization[field] for field in required_receipt):
        raise ProvenanceError("issuer IR authorization receipt is invalid")
    _parse_timestamp(authorization["reviewed_at"], "authorization.reviewed_at")

    issuer = payload.get("issuer")
    if not isinstance(issuer, dict):
        raise ProvenanceError("issuer IR issuer is invalid")
    name = str(issuer.get("name") or "").strip()
    cik = str(issuer.get("cik") or "")
    detail_host = _host(issuer.get("detail_host"), "detail_host")
    artifact_host = _host(issuer.get("artifact_host"), "artifact_host")
    source_policy = _ISSUER_SOURCE_ALLOWLIST.get(cik)
    if not name or _CIK.fullmatch(cik) is None or detail_host == artifact_host:
        raise ProvenanceError("issuer IR issuer is invalid")
    if source_policy is None or (
        detail_host != source_policy["detail_host"]
        or artifact_host != source_policy["artifact_host"]
    ):
        raise ProvenanceError("issuer IR issuer is outside the fixed source allowlist")

    raw_filings = payload.get("filings")
    if (
        not isinstance(raw_filings, list)
        or not 2 <= len(raw_filings) <= MAX_FILINGS
        or not all(isinstance(item, dict) for item in raw_filings)
    ):
        raise ProvenanceError("issuer IR filings must contain two or more records")
    filings: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for raw in raw_filings:
        filing_id = str(raw.get("filing_id") or "")
        form = str(raw.get("form") or "")
        filing_date = str(raw.get("filing_date") or "")
        report_date = str(raw.get("report_date") or "")
        detail_url = _detail_url(
            raw.get("detail_url"),
            host=source_policy["detail_host"],
            path=source_policy["detail_path"],
        )
        if (
            _FILING_ID.fullmatch(filing_id) is None
            or filing_id in seen_ids
            or detail_url in seen_urls
            or _FORM.fullmatch(form) is None
        ):
            raise ProvenanceError("issuer IR filing identity is invalid")
        try:
            filed = date.fromisoformat(filing_date)
            reported = date.fromisoformat(report_date)
        except ValueError as exc:
            raise ProvenanceError("issuer IR filing date is invalid") from exc
        if reported > filed:
            raise ProvenanceError("issuer IR report date is after filing date")
        filings.append(
            {
                "filing_id": filing_id,
                "detail_url": detail_url,
                "form": form,
                "filing_date": filing_date,
                "report_date": report_date,
            }
        )
        seen_ids.add(filing_id)
        seen_urls.add(detail_url)

    rate = payload.get("requests_per_second", 2.0)
    retries = payload.get("max_retries", 3)
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= MAX_REQUESTS_PER_SECOND
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= MAX_RETRIES
    ):
        raise ProvenanceError("issuer IR fetch policy is invalid")
    return {
        "user_agent": user_agent,
        "authorization": authorization,
        "issuer": {
            "name": name,
            "cik": cik,
            "detail_host": source_policy["detail_host"],
            "artifact_host": source_policy["artifact_host"],
        },
        "source_policy": source_policy,
        "filings": filings,
        "requests_per_second": float(rate),
        "max_retries": retries,
    }


def _challenge(body: bytes, *, ignore_subscribe_captcha: bool = False) -> bool:
    sample = body[:64_000].lower()
    if any(marker in sample for marker in _CHALLENGE_MARKERS[:-1]):
        return True
    if b"captcha" not in sample:
        return False
    if ignore_subscribe_captcha and (
        b"uccaptcha" in sample or b"captchacontainer" in sample
    ):
        return False
    return True


def _sanitize_detail_page(body: bytes) -> tuple[bytes, int]:
    """Remove volatile public-auth state and email PII while preserving filing links."""
    sanitized, count = _VOLATILE_JWT.subn(b"[redacted-volatile-auth-state]", body)
    if _VOLATILE_JWT.search(sanitized):
        raise ProvenanceError("issuer IR detail page auth-state redaction failed")
    try:
        text = sanitized.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProvenanceError("issuer IR detail page is not UTF-8") from exc
    redacted, email_count = _EMAIL.subn("[redacted-email]", text)
    if _EMAIL.search(redacted):
        raise ProvenanceError("issuer IR detail page email redaction failed")
    return redacted.encode("utf-8"), count + email_count


def _parse_detail(
    body: bytes,
    *,
    expected_form: str,
    expected_filing_date: str,
    artifact_host: str,
    artifact_path_prefix: str,
    control_prefix: str,
    ignore_subscribe_captcha: bool = False,
    detail_widget: str = "item_download",
    filing_date_strptime: str | None = None,
) -> dict[str, str]:
    alphabet = artifact_path_prefix == "/CIK-0001652044/"
    if detail_widget not in {"item_download", "evergreen_sec_filing_details"}:
        raise ProvenanceError("issuer IR detail page widget is unsupported")
    # Alphabet's ordinary filing page contains an unrelated reCAPTCHA string
    # in navigation JavaScript. Ignore script contents only for this registered
    # page; visible challenge text and other challenge markers still fail.
    challenge_body = body
    if alphabet:
        challenge_body = re.sub(
            rb"<script\b[^>]*>.*?</script>",
            b"",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if any(marker in body.lower() for marker in _CHALLENGE_MARKERS[:-1]):
            raise ProvenanceError("issuer IR detail page returned a challenge page")
    if _challenge(
        challenge_body, ignore_subscribe_captcha=ignore_subscribe_captcha
    ):
        raise ProvenanceError("issuer IR detail page returned a challenge page")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProvenanceError("issuer IR detail page is not UTF-8") from exc
    parser = _DetailParser(control_prefix, detail_widget=detail_widget)
    try:
        parser.feed(text)
    except Exception as exc:
        if isinstance(exc, ProvenanceError):
            raise
        raise ProvenanceError("issuer IR detail page is invalid HTML") from exc
    observed_form = " ".join(parser.form_parts).strip()
    observed_date = " ".join(parser.date_parts).strip()
    if filing_date_strptime:
        date_format = filing_date_strptime
    elif alphabet:
        date_format = "%m/%d/%Y"
    else:
        date_format = "%b %d, %Y"
    try:
        parsed_time = time.strptime(observed_date, date_format)
        parsed_date = date(
            parsed_time.tm_year, parsed_time.tm_mon, parsed_time.tm_mday
        ).isoformat()
    except ValueError as exc:
        raise ProvenanceError("issuer IR detail page has invalid filing date") from exc
    if observed_form != expected_form or parsed_date != expected_filing_date:
        raise ProvenanceError("issuer IR detail page identity mismatch")
    if set(parser.artifact_urls) != set(_ARTIFACTS):
        raise ProvenanceError("issuer IR detail page has missing artifact URL")
    for role, url in parser.artifact_urls.items():
        parsed = urlparse(url)
        suffix = _ARTIFACTS[role][1]
        relative_path = parsed.path.removeprefix(artifact_path_prefix)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ProvenanceError(
                "issuer IR detail page has invalid artifact URL"
            ) from exc
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != artifact_host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or not parsed.path.startswith(artifact_path_prefix)
            or not relative_path
            or "/" in relative_path
            or "\\" in relative_path
            or "%" in relative_path
            or not parsed.path.lower().endswith(suffix)
            or parsed.query
            or parsed.fragment
        ):
            raise ProvenanceError("issuer IR detail page has invalid artifact URL")
    return parser.artifact_urls


def _xbrl_identity(raw: bytes) -> tuple[dict[str, str], str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if any(
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or name.endswith("/")
                for name in names
            ):
                raise ProvenanceError("issuer IR XBRL archive has unsafe members")
            instances = [name for name in names if name.lower().endswith("_htm.xml")]
            if len(instances) != 1:
                raise ProvenanceError(
                    "issuer IR XBRL archive must contain one filing instance"
                )
            info = archive.getinfo(instances[0])
            if info.file_size <= 0 or info.file_size > MAX_SOURCE_BYTES:
                raise ProvenanceError("issuer IR XBRL instance exceeds size limit")
            instance = archive.read(instances[0])
    except (zipfile.BadZipFile, OSError) as exc:
        raise ProvenanceError("issuer IR XBRL artifact is not a valid ZIP") from exc
    try:
        root = ET.fromstring(instance)
    except ET.ParseError as exc:
        raise ProvenanceError("issuer IR XBRL instance is invalid XML") from exc
    wanted = {
        "EntityRegistrantName": "registrant_name",
        "EntityCentralIndexKey": "cik",
        "DocumentType": "form",
        "DocumentPeriodEndDate": "report_date",
    }
    values: dict[str, list[str]] = {field: [] for field in wanted.values()}
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in wanted and element.text and element.text.strip():
            values[wanted[local]].append(element.text.strip())
    if any(len(set(items)) != 1 for items in values.values()):
        raise ProvenanceError("issuer IR XBRL identity is missing or ambiguous")
    identity = {field: items[0] for field, items in values.items()}
    return identity, instances[0]


def _header(headers: Mapping[str, str], name: str) -> str:
    return next(
        (str(value) for key, value in headers.items() if key.lower() == name.lower()),
        "",
    )


def _artifact_receipt(
    *,
    role: str,
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    source_file: str,
) -> dict[str, Any]:
    if role == "annual_report_pdf" and not body.startswith(b"%PDF-"):
        raise ProvenanceError("issuer IR annual report artifact is not a PDF")
    if role == "rendered_xbrl_html" and (
        _challenge(body) or b"<html" not in body[:16_000].lower()
    ):
        raise ProvenanceError("issuer IR rendered XBRL artifact is not HTML")
    return {
        "role": role,
        "source_url": url,
        "source_file": source_file,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "content_type": _header(headers, "Content-Type"),
        "etag": _header(headers, "ETag"),
        "last_modified": _header(headers, "Last-Modified"),
    }


def _filing_relations(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        filings,
        key=lambda filing: (filing["report_date"], filing["filing_date"]),
    )
    return [
        {
            "relation_id": (
                f"issuer-ir:{current['filing_id']}:prior:{prior['filing_id']}"
            ),
            "relation_type": "prior_available_annual_filing",
            "from_filing_id": current["filing_id"],
            "to_filing_id": prior["filing_id"],
            "relation_provenance": "derived_temporal_same_issuer",
            "derivation_rule": (
                "adjacent_report_dates_after_same_issuer_xbrl_identity_validation"
            ),
            "evidence": [
                {
                    "filing_id": current["filing_id"],
                    "fields": ["registrant_name", "cik", "form", "report_date"],
                },
                {
                    "filing_id": prior["filing_id"],
                    "fields": ["registrant_name", "cik", "form", "report_date"],
                },
            ],
        }
        for prior, current in pairwise(ordered)
    ]


def fetch_issuer_ir_filing_history(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
) -> Path:
    """Fetch and hash-bind issuer-owned filing pages and their exact artifacts."""
    try:
        raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read issuer IR fetch request: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProvenanceError("issuer IR fetch request must be an object")
    request = _validate_request(payload)
    timestamp = generated_at or _timestamp()
    _parse_timestamp(timestamp, "generated_at")
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        requests_per_second=request["requests_per_second"],
        max_retries=request["max_retries"],
        http_get=http_get,
        sleep=sleep,
    )

    filings: list[dict[str, Any]] = []
    for filing in request["filings"]:
        detail_body, detail_headers = downloader.get(filing["detail_url"])
        artifact_urls = _parse_detail(
            detail_body,
            expected_form=filing["form"],
            expected_filing_date=filing["filing_date"],
            artifact_host=request["issuer"]["artifact_host"],
            artifact_path_prefix=request["source_policy"]["artifact_path_prefix"],
            control_prefix=request["source_policy"]["detail_control_prefix"],
            ignore_subscribe_captcha=bool(
                request["source_policy"]["ignore_subscribe_captcha"]
            ),
            detail_widget=str(
                request["source_policy"].get("detail_widget") or "item_download"
            ),
            filing_date_strptime=request["source_policy"].get("filing_date_strptime"),
        )
        stored_detail, detail_redactions = _sanitize_detail_page(detail_body)
        prefix = filing["filing_id"]
        detail_file = f"{prefix}.issuer-detail.html"
        _atomic_write(output_directory / detail_file, stored_detail)
        artifacts: list[dict[str, Any]] = []
        xbrl_identity: dict[str, str] | None = None
        xbrl_instance_file = ""
        for role in _ARTIFACTS:
            artifact_body, artifact_headers = downloader.get(artifact_urls[role])
            suffix = _ARTIFACTS[role][1]
            source_file = f"{prefix}.{role}{suffix}"
            if role == "xbrl_zip":
                xbrl_identity, xbrl_instance_file = _xbrl_identity(artifact_body)
            receipt = _artifact_receipt(
                role=role,
                url=artifact_urls[role],
                body=artifact_body,
                headers=artifact_headers,
                source_file=source_file,
            )
            _atomic_write(output_directory / source_file, artifact_body)
            artifacts.append(receipt)
        expected_identity = {
            "registrant_name": request["issuer"]["name"],
            "cik": request["issuer"]["cik"],
            "form": filing["form"],
            "report_date": filing["report_date"],
        }
        if xbrl_identity is None or (
            {field: xbrl_identity[field] for field in ("cik", "form", "report_date")}
            != {
                field: expected_identity[field]
                for field in ("cik", "form", "report_date")
            }
            or re.sub(r"[^a-z0-9]", "", xbrl_identity["registrant_name"].casefold())
            != re.sub(r"[^a-z0-9]", "", expected_identity["registrant_name"].casefold())
        ):
            raise ProvenanceError("issuer IR XBRL identity mismatch")
        filings.append(
            {
                **filing,
                "detail_page": {
                    "source_url": filing["detail_url"],
                    "source_file": detail_file,
                    "retrieval_sha256": hashlib.sha256(detail_body).hexdigest(),
                    "sha256": hashlib.sha256(stored_detail).hexdigest(),
                    "retrieval_bytes": len(detail_body),
                    "bytes": len(stored_detail),
                    "sanitization_revision": "issuer-ir-detail-volatile-auth-v1",
                    "volatile_auth_state_redactions": detail_redactions,
                    "content_type": _header(detail_headers, "Content-Type"),
                    "etag": _header(detail_headers, "ETag"),
                    "last_modified": _header(detail_headers, "Last-Modified"),
                },
                "xbrl_identity": xbrl_identity,
                "xbrl_instance_file": xbrl_instance_file,
                "artifacts": artifacts,
            }
        )

    inventory = {
        "schema_version": ISSUER_IR_INVENTORY_SCHEMA,
        "source_status": "issuer_owned_ir_download",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "integration_blocker": (
            "issuer artifacts are not SEC complete submissions and the current "
            "Company parser requires SGML DOCUMENT components with inline XBRL"
        ),
        "generated_at": timestamp,
        "authorization": request["authorization"],
        "fetch_policy": {
            "requests_per_second": request["requests_per_second"],
            "max_retries": request["max_retries"],
            "user_agent_sha256": hashlib.sha256(
                request["user_agent"].encode()
            ).hexdigest(),
        },
        "issuer": request["issuer"],
        "n": len(filings),
        "filings": filings,
        "filing_relations": _filing_relations(filings),
    }
    output_path = output_directory / "issuer_ir_inventory.json"
    encoded = json.dumps(inventory, ensure_ascii=False, indent=2).encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ProvenanceError("issuer IR inventory exceeds manifest size limit")
    _atomic_write(output_path, encoded)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(fetch_issuer_ir_filing_history(args.request, args.out_dir))


if __name__ == "__main__":
    main()
