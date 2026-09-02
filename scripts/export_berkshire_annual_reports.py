#!/usr/bin/env python3
"""Fetch and source-attest four Berkshire official annual-report PDFs."""

from __future__ import annotations

import argparse
import hashlib
import io
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
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pypdf
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.issuerpdfworkflow import (
    ISSUER_OFFICIAL_PDF_INVENTORY_SCHEMA,
    ISSUER_OFFICIAL_PDF_SOURCE_KIND,
    SUPPORTED_PARSER,
)
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

REQUEST_SCHEMA = "longworld.berkshire-annual-report-request.v1"
INVENTORY_SCHEMA = ISSUER_OFFICIAL_PDF_INVENTORY_SCHEMA
ISSUER = {
    "name": "Berkshire Hathaway Inc.",
    "cik": "0001067983",
    "official_host": "www.berkshirehathaway.com",
}
EXPECTED_YEARS = (2021, 2022, 2023, 2024)
PARSER = SUPPORTED_PARSER
SOURCE_FAMILY = "berkshire_official_annual_report_pdf"
MAX_REQUESTS_PER_SECOND = 5.0
MAX_TIMEOUT_SECONDS = 120.0
_USER_AGENT = re.compile(r"^\S(?:.*\S)?\s+[\w.+-]+@[\w.-]+$")


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
                "Berkshire annual-report fetch rejected an HTTP redirect"
            ) from error
        raise ProvenanceError(
            f"Berkshire annual-report fetch failed with HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        raise ProvenanceError(
            f"Berkshire annual-report fetch failed: {error.reason}"
        ) from error


def _header(headers: Mapping[str, str], name: str) -> str:
    return next(
        (str(value) for key, value in headers.items() if key.lower() == name.lower()),
        "",
    )


def _expected_url(year: int) -> str:
    return f"https://{ISSUER['official_host']}/{year}ar/{year}ar.pdf"


def _read_request(path: Path) -> dict[str, Any]:
    try:
        raw = _read_regular_file(path, MAX_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read Berkshire annual-report request: {error}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema_version") != REQUEST_SCHEMA:
        raise ProvenanceError("unsupported Berkshire annual-report request schema")
    return payload


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    user_agent = str(payload.get("user_agent") or "").strip()
    if len(user_agent) > 256 or _USER_AGENT.fullmatch(user_agent) is None:
        raise ProvenanceError("Berkshire annual-report User-Agent is invalid")
    authorization = payload.get("authorization")
    fields = ("record_id", "scope", "basis", "reviewed_at")
    if not isinstance(authorization, dict):
        raise ProvenanceError("Berkshire annual-report authorization is invalid")
    receipt = {field: str(authorization.get(field) or "").strip() for field in fields}
    if any(not receipt[field] for field in fields):
        raise ProvenanceError("Berkshire annual-report authorization is invalid")
    _parse_timestamp(receipt["reviewed_at"], "authorization.reviewed_at")

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
        raise ProvenanceError("Berkshire annual-report fetch bounds are invalid")

    reports = payload.get("reports")
    if not isinstance(reports, list) or len(reports) != len(EXPECTED_YEARS):
        raise ProvenanceError("Berkshire request must contain four annual reports")
    normalized: list[dict[str, Any]] = []
    for raw, year in zip(reports, EXPECTED_YEARS, strict=True):
        if not isinstance(raw, dict) or raw.get("year") != year:
            raise ProvenanceError("Berkshire annual-report year is invalid")
        url = str(raw.get("url") or "")
        parsed = urlparse(url)
        if (
            url != _expected_url(year)
            or parsed.scheme != "https"
            or (parsed.hostname or "").lower() != ISSUER["official_host"]
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ProvenanceError("Berkshire annual-report URL is invalid")
        normalized.append({"year": year, "url": url})
    return {
        "user_agent": user_agent,
        "authorization": receipt,
        "requests_per_second": float(rate),
        "timeout_seconds": float(timeout),
        "reports": normalized,
    }


def _extract_pdf(raw: bytes) -> tuple[str, int, int]:
    if not raw.startswith(b"%PDF-") or len(raw) > MAX_SOURCE_BYTES:
        raise ProvenanceError("Berkshire annual-report response is not a bounded PDF")
    if pypdf.__version__ != PARSER["version"]:
        raise ProvenanceError("Berkshire annual-report parser version is not pinned")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or not reader.pages:
            raise ProvenanceError("Berkshire annual-report PDF is not extractable")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ProvenanceError:
        raise
    except Exception as error:
        raise ProvenanceError("Berkshire annual-report PDF is invalid") from error
    text = "\n\n".join(pages)
    nonempty_pages = sum(bool(page.strip()) for page in pages)
    if len(text.strip()) < 40 or nonempty_pages == 0:
        raise ProvenanceError("Berkshire annual-report extracted text is too short")
    return text, len(pages), nonempty_pages


def _section_receipts(
    text: str, year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risk_start = text.rfind("Item 1A. Risk Factors")
    business_start = text.rfind("Item 1. Business Description", 0, risk_start)
    item2_start = text.find("Item 2. Description of Properties", risk_start)
    cyber_start = text.find("Item 1C. Cybersecurity", risk_start, item2_start)
    if min(business_start, risk_start, item2_start) < 0 or (
        year >= 2023 and cyber_start < 0
    ):
        raise ProvenanceError("Berkshire annual-report section boundary is missing")
    boundaries = [
        ("item1_business", business_start, risk_start),
        (
            "item1a_risk_factors",
            risk_start,
            cyber_start if cyber_start >= 0 else item2_start,
        ),
    ]
    if cyber_start >= 0:
        boundaries.append(("item1c_cybersecurity", cyber_start, item2_start))
    sections = [
        {
            "section_id": section_id,
            "char_start": start,
            "char_end": end,
            "sha256": hashlib.sha256(text[start:end].encode()).hexdigest(),
        }
        for section_id, start, end in boundaries
    ]

    def fact(
        *, section_id: str, field: str, pattern: str, value: str | None = None
    ) -> dict[str, Any]:
        section = next(item for item in sections if item["section_id"] == section_id)
        section_start = section["char_start"]
        section_end = section["char_end"]
        if not isinstance(section_start, int) or not isinstance(section_end, int):
            raise ProvenanceError("Berkshire section receipt is invalid")
        match = re.search(
            pattern,
            text[section_start:section_end],
            re.MULTILINE,
        )
        if match is None:
            raise ProvenanceError("Berkshire annual-report narrative fact is missing")
        quote = match.group(0)
        start = section_start + match.start()
        selected_value = value or quote
        if selected_value not in quote:
            raise ProvenanceError("Berkshire narrative fact value is not grounded")
        return {
            "fact_id": f"berkshire-{year}-{field}",
            "section_id": section_id,
            "field": field,
            "value": selected_value,
            "evidence_quote": quote,
            "evidence_char_start": start,
            "evidence_char_end": start + len(quote),
        }

    if year == 2021:
        pilot = fact(
            section_id="item1_business",
            field="pilot_investment_mention",
            pattern=r"a 38\.6% interest in Pilot Travel Centers LLC",
        )
    else:
        pilot = fact(
            section_id="item1_business",
            field="pilot_dedicated_operating_section",
            pattern=r"^Pilot Travel Centers(?: \(PTC\))?$",
            value="Pilot Travel Centers",
        )
    if year < 2023:
        cyber = fact(
            section_id="item1a_risk_factors",
            field="cyber_risk_factor",
            pattern=r"^Cyber security risks$",
        )
        facts = [pilot, cyber]
    else:
        cyber = fact(
            section_id="item1c_cybersecurity",
            field="cyber_dedicated_item1c",
            pattern=r"^Item 1C\. Cybersecurity$",
        )
        oversight = fact(
            section_id="item1c_cybersecurity",
            field="cyber_audit_committee_oversight",
            pattern=(
                r"The Audit Committee of Berkshire(?:'s|’s) Board of Directors has "
                r"responsibility for oversight"
            ),
            value="Audit Committee",
        )
        facts = [pilot, cyber, oversight]
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    return sections, [
        {**item, "text_sha256": text_sha256} for item in facts
    ]


def _write(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    os.chmod(path, 0o600)


def load_verified_berkshire_annual_report_inventory(
    manifest_path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Verify the signed inventory and replay every PDF-to-text receipt."""
    try:
        manifest = json.loads(
            _read_regular_file(manifest_path, MAX_MANIFEST_BYTES).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read Berkshire annual-report inventory: {error}"
        ) from error
    key = attestation_key or attestation_key_from_env("source_manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != INVENTORY_SCHEMA
        or manifest.get("issuer") != ISSUER
        or manifest.get("parser") != PARSER
        or not verify_attestation(manifest, key, purpose="source_manifest")
    ):
        raise ProvenanceError("Berkshire annual-report inventory attestation is invalid")
    records = manifest.get("records")
    if (
        not isinstance(records, list)
        or manifest.get("n") != len(EXPECTED_YEARS)
        or len(records) != len(EXPECTED_YEARS)
    ):
        raise ProvenanceError("Berkshire annual-report inventory records are invalid")
    base = manifest_path.parent
    for record, year in zip(records, EXPECTED_YEARS, strict=True):
        prefix = f"berkshire-{year}-annual"
        if (
            not isinstance(record, dict)
            or record.get("year") != year
            or record.get("url") != _expected_url(year)
            or record.get("pdf_file") != f"{prefix}.pdf"
            or record.get("text_file") != f"{prefix}.txt"
        ):
            raise ProvenanceError("Berkshire annual-report record identity is invalid")
        pdf_raw = _read_regular_file(base / record["pdf_file"], MAX_SOURCE_BYTES)
        text_raw = _read_regular_file(base / record["text_file"], MAX_SOURCE_BYTES)
        if (
            hashlib.sha256(pdf_raw).hexdigest() != record.get("pdf_sha256")
            or len(pdf_raw) != record.get("pdf_bytes")
        ):
            raise ProvenanceError("Berkshire annual-report PDF receipt is invalid")
        if (
            hashlib.sha256(text_raw).hexdigest() != record.get("text_sha256")
            or len(text_raw) != record.get("text_bytes")
        ):
            raise ProvenanceError("Berkshire annual-report text receipt is invalid")
        text, page_count, nonempty_pages = _extract_pdf(pdf_raw)
        sections, derived_facts = _section_receipts(text, year)
        if (
            text.encode("utf-8") != text_raw
            or len(text) != record.get("extracted_chars")
            or page_count != record.get("page_count")
            or nonempty_pages != record.get("nonempty_pages")
            or sections != record.get("sections")
            or derived_facts != record.get("derived_facts")
        ):
            raise ProvenanceError("Berkshire annual-report parser receipt is invalid")
    return manifest


def export_berkshire_annual_reports(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    attestation_key: bytes | None = None,
    generated_at: str | None = None,
) -> Path:
    """Fetch exact official PDFs and atomically publish one signed inventory."""
    request = _validate_request(_read_request(request_path))
    if output_directory.exists():
        raise ProvenanceError("Berkshire annual-report output already exists")
    key = attestation_key or attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("Berkshire export requires a source attestation key")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    _parse_timestamp(timestamp, "generated_at")

    output_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.stage-", dir=output_directory.parent
        )
    )
    try:
        records: list[dict[str, Any]] = []
        last_request_at: float | None = None
        for report in request["reports"]:
            now = time.monotonic()
            if last_request_at is not None:
                delay = 1.0 / request["requests_per_second"] - (now - last_request_at)
                if delay > 0:
                    sleep(delay)
            last_request_at = time.monotonic()
            response = http_get(
                report["url"],
                {"User-Agent": request["user_agent"]},
                request["timeout_seconds"],
            )
            content_type = _header(response.headers, "Content-Type")
            if (
                response.status != 200
                or response.final_url != report["url"]
                or content_type.partition(";")[0].strip().lower()
                != "application/pdf"
            ):
                raise ProvenanceError(
                    "Berkshire annual-report response identity is invalid"
                )
            text, page_count, nonempty_pages = _extract_pdf(response.body)
            sections, derived_facts = _section_receipts(text, report["year"])
            prefix = f"berkshire-{report['year']}-annual"
            pdf_file = f"{prefix}.pdf"
            text_file = f"{prefix}.txt"
            text_raw = text.encode("utf-8")
            _write(stage / pdf_file, response.body)
            _write(stage / text_file, text_raw)
            records.append(
                {
                    **report,
                    "record_id": f"berkshire:{report['year']}",
                    "occurred_at": f"{report['year']}-12-31",
                    "source_family": SOURCE_FAMILY,
                    "pdf_file": pdf_file,
                    "pdf_sha256": hashlib.sha256(response.body).hexdigest(),
                    "pdf_bytes": len(response.body),
                    "content_type": content_type,
                    "page_count": page_count,
                    "nonempty_pages": nonempty_pages,
                    "text_file": text_file,
                    "text_sha256": hashlib.sha256(text_raw).hexdigest(),
                    "text_bytes": len(text_raw),
                    "extracted_chars": len(text),
                    "sections": sections,
                    "derived_facts": derived_facts,
                }
            )
        manifest = attach_attestation(
            {
                "schema_version": INVENTORY_SCHEMA,
                "source_status": "issuer_owned_official_pdf",
                "data_stage": "source_inventory",
                "hybrid_train_ready": False,
                "production_eligible": False,
                "generation_integration": "disabled",
                "generated_at": timestamp,
                "authorization": request["authorization"],
                "issuer": ISSUER,
                "parser": PARSER,
                "fetch_policy": {
                    "requests_per_second": request["requests_per_second"],
                    "timeout_seconds": request["timeout_seconds"],
                    "redirects_allowed": False,
                    "user_agent_sha256": hashlib.sha256(
                        request["user_agent"].encode()
                    ).hexdigest(),
                },
                "n": len(records),
                "records": records,
                "relations": [
                    {
                        "relation_id": (
                            f"{current['record_id']}:prior_official:"
                            f"{prior['record_id']}"
                        ),
                        "kind": "prior_official_annual_report",
                        "source_record_id": current["record_id"],
                        "target_record_id": prior["record_id"],
                        "evidence": [
                            {
                                "record_id": record["record_id"],
                                "fact_ids": [
                                    fact["fact_id"]
                                    for fact in record["derived_facts"]
                                ],
                            }
                            for record in (current, prior)
                        ],
                    }
                    for prior, current in pairwise(records)
                ],
                "source_kind": ISSUER_OFFICIAL_PDF_SOURCE_KIND,
            },
            key,
            purpose="source_manifest",
        )
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise ProvenanceError("Berkshire annual-report inventory is too large")
        _write(stage / "berkshire_annual_report_inventory.signed.json", encoded)
        os.replace(stage, output_directory)
        return output_directory / "berkshire_annual_report_inventory.signed.json"
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(export_berkshire_annual_reports(args.request, args.out_dir))


if __name__ == "__main__":
    main()
