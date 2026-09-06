"""P47 USAspending certified-period reconciliation preflight.

Raw transaction and subaward rows are inspected only in memory.  The report
contains aggregate metrics and source digests, never source identifiers.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import ssl
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from transformers import AutoTokenizer

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path(
    "configs/p47_usaspending_certified_period_reconciliation_preflight_v1.json"
)
OUTPUT = Path("reports/p47_usaspending_certified_period_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"api.usaspending.gov", "files.usaspending.gov"})
USER_AGENT = "LongWorld-P47-source-preflight/1.0 xnhyacinth@users.noreply.github.com"
PAGE_LIMIT = 100


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request(
    url: str, payload: dict[str, Any] | None = None
) -> tuple[bytes, dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P47 source URL: {url}")
    data = None
    headers = {"User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(
        request, timeout=180, context=ssl.create_default_context()
    ) as response:
        raw = response.read()
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P47 redirect target: {final_url}")
        return raw, {
            "requested_url": url,
            "final_url": final_url,
            "status": response.status,
            "content_type": response.headers.get_content_type(),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }


def _json_request(
    url: str, payload: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw, receipt = _request(url, payload)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object from {url}")
    return value, receipt


def _head_status(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P47 source URL: {url}")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(
            request, timeout=30, context=ssl.create_default_context()
        ) as response:
            return {
                "url": url,
                "status": response.status,
                "content_type": response.headers.get_content_type(),
                "content_length": response.headers.get("Content-Length"),
            }
    except urllib.error.HTTPError as error:
        return {
            "url": url,
            "status": error.code,
            "content_type": error.headers.get_content_type(),
            "content_length": error.headers.get("Content-Length"),
        }


def _date(value: str) -> date:
    text = value.strip()
    try:
        if "/" in text:
            month, day, year = (int(part) for part in text.split("/"))
            return date(year, month, day)
        return date.fromisoformat(text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unexpected action date format: {text!r}") from error


def _coarsen(field: str, value: str) -> str:
    lowered = field.lower()
    if "date" in lowered:
        try:
            return _date(value).strftime("%Y-%m")
        except ValueError:
            return "DATE_PRESENT"
    if any(marker in lowered for marker in ("amount", "obligation", "outlay")):
        try:
            number = Decimal(value.replace(",", ""))
        except InvalidOperation:
            return "NUMERIC_PRESENT"
        if number == 0:
            return "ZERO"
        sign = "POS" if number > 0 else "NEG"
        magnitude = math.floor(math.log10(float(abs(number))))
        return f"{sign}_1E{magnitude}"
    return value


def _safe_line(group: str, row: dict[str, Any], fields: list[str]) -> str:
    parts = [group]
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            parts.append(f"{field}={value}")
    return "|".join(parts)


def _coarsened_line(group: str, row: dict[str, Any], fields: list[str]) -> str:
    parts = [group]
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            parts.append(f"{field}={_coarsen(field, value)}")
    return "|".join(parts)


def _token_count(tokenizer: Any, texts: set[str], batch_size: int = 256) -> int:
    total = 0
    batch: list[str] = []
    for text in sorted(texts):
        batch.append(text)
        if len(batch) == batch_size:
            encoded = tokenizer(
                batch,
                add_special_tokens=False,
                padding=False,
                truncation=False,
            )["input_ids"]
            total += sum(len(item) for item in encoded)
            batch.clear()
    if batch:
        encoded = tokenizer(
            batch,
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        total += sum(len(item) for item in encoded)
    return total


def _archive_rows(
    raw: bytes,
    spec: dict[str, Any],
    start: date,
    earlier_end: date,
    later_end: date,
) -> tuple[
    dict[str, Any], dict[str, list[str]], dict[str, list[str]], dict[str, set[str]]
]:
    archive = zipfile.ZipFile(io.BytesIO(raw))
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate archive member: {spec['group']}")
    if len(infos) != 1 or not names[0].lower().endswith(".csv"):
        raise ValueError(f"unexpected archive layout: {spec['group']}")
    member = PurePosixPath(names[0])
    if member.is_absolute() or ".." in member.parts or "\\" in names[0]:
        raise ValueError(f"unsafe archive member: {names[0]}")
    info = infos[0]
    if info.file_size != spec["expected_csv_bytes"]:
        raise ValueError(f"CSV byte length changed: {spec['group']}")
    csv_digest = hashlib.sha256()
    with archive.open(info) as source:
        while chunk := source.read(1024 * 1024):
            csv_digest.update(chunk)
    if csv_digest.hexdigest() != spec["expected_csv_sha256"]:
        raise ValueError(f"CSV digest changed: {spec['group']}")

    award_lines: dict[str, list[str]] = defaultdict(list)
    award_coarsened_lines: dict[str, list[str]] = defaultdict(list)
    award_periods: dict[str, set[str]] = defaultdict(set)
    transaction_keys: set[str] = set()
    duplicate_transaction_keys = 0
    selected_rows = 0
    annual_rows = 0
    missing_key_rows = 0
    selected_types: Counter[str] = Counter()
    safe_lines: set[str] = set()
    coarsened_lines: set[str] = set()
    first_date: date | None = None
    last_date: date | None = None
    with archive.open(info) as raw_file:
        raw_header = raw_file.readline()
        if _sha256(raw_header) != spec["expected_header_sha256"]:
            raise ValueError(f"CSV header changed: {spec['group']}")
        header = next(csv.reader([raw_header.decode("utf-8-sig")]))
        required = {
            spec["transaction_key"],
            spec["award_key"],
            spec["award_type_field"],
            *spec["safe_fields"],
        }
        if not required.issubset(header):
            raise ValueError(f"required field missing: {spec['group']}")
        text_file = io.TextIOWrapper(raw_file, encoding="utf-8", newline="")
        reader = csv.DictReader(text_file, fieldnames=header)
        for row in reader:
            annual_rows += 1
            award_type = (row[spec["award_type_field"]] or "").strip()
            if award_type not in spec["award_type_codes"]:
                continue
            action_date = _date(row["action_date"])
            if not start <= action_date <= later_end:
                continue
            selected_rows += 1
            selected_types[award_type] += 1
            first_date = (
                action_date if first_date is None else min(first_date, action_date)
            )
            last_date = (
                action_date if last_date is None else max(last_date, action_date)
            )
            transaction_key = (row[spec["transaction_key"]] or "").strip()
            award_key = (row[spec["award_key"]] or "").strip()
            if not transaction_key or not award_key:
                missing_key_rows += 1
                continue
            if transaction_key in transaction_keys:
                duplicate_transaction_keys += 1
            transaction_keys.add(transaction_key)
            period = "P1_P6" if action_date <= earlier_end else "P7_P9"
            award_periods[award_key].add(period)
            line = _safe_line(spec["group"], row, spec["safe_fields"])
            coarsened = _coarsened_line(spec["group"], row, spec["safe_fields"])
            safe_lines.add(line)
            coarsened_lines.add(coarsened)
            award_lines[award_key].append(line)
            award_coarsened_lines[award_key].append(coarsened)
    return (
        {
            "csv_member_name": member.name,
            "csv_member_bytes": info.file_size,
            "csv_member_sha256": csv_digest.hexdigest(),
            "column_count": len(header),
            "header_sha256": _sha256(raw_header),
            "annual_rows": annual_rows,
            "selected_p1_p9_rows": selected_rows,
            "selected_unique_transaction_keys": len(transaction_keys),
            "duplicate_selected_transaction_keys": duplicate_transaction_keys,
            "missing_selected_transaction_or_award_keys": missing_key_rows,
            "selected_distinct_awards": len(award_lines),
            "selected_award_type_counts": dict(sorted(selected_types.items())),
            "selected_action_date_min": first_date.isoformat() if first_date else None,
            "selected_action_date_max": last_date.isoformat() if last_date else None,
            "cross_p6_p9_awards": sum(
                len(periods) == 2 for periods in award_periods.values()
            ),
            "identifier_free_exact_units": len(safe_lines),
            "amount_date_coarsened_units": len(coarsened_lines),
            "identifier_free_exact_lines": safe_lines,
            "amount_date_coarsened_lines": coarsened_lines,
        },
        award_lines,
        award_coarsened_lines,
        award_periods,
    )


def _search_filters(config: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    cutoff = config["cutoff"]
    return {
        "award_type_codes": codes,
        "time_period": [
            {
                "start_date": cutoff["start_date"],
                "end_date": cutoff["later_end_date"],
            }
        ],
        "agencies": [
            {
                "type": "awarding",
                "tier": "toptier",
                "name": config["agency"]["name"],
            }
        ],
    }


def _download_count(
    config: dict[str, Any], codes: list[str], spending_level: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _json_request(
        config["official_endpoints"]["download_count"],
        {
            "filters": _search_filters(config, codes),
            "spending_level": spending_level,
        },
    )


def _subawards(
    config: dict[str, Any], spec: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    page = 1
    while True:
        payload = {
            "spending_level": "subawards",
            "subawards": True,
            "limit": PAGE_LIMIT,
            "page": page,
            "sort": "Sub-Award Date",
            "order": "asc",
            "filters": _search_filters(config, spec["award_type_codes"]),
            "fields": config["subaward_fields"],
        }
        raw, _receipt = _request(
            config["official_endpoints"]["subaward_search"], payload
        )
        digest.update(raw)
        response = json.loads(raw)
        batch = response.get("results")
        metadata = response.get("page_metadata")
        if not isinstance(batch, list) or not isinstance(metadata, dict):
            raise TypeError(f"malformed subaward page: {spec['group']} page {page}")
        for row in batch:
            if not isinstance(row, dict):
                raise TypeError("malformed subaward row")
        rows.extend(batch)
        if not metadata.get("hasNext"):
            break
        page += 1
        if page > 5000:
            raise ValueError("subaward pagination bound exceeded")
    return rows, {
        "pages": page,
        "returned_grouped_rows": len(rows),
        "combined_page_response_sha256": digest.hexdigest(),
        "raw_rows_persisted": False,
        "identifiers_or_identifier_hashes_persisted": False,
    }


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        name: list(EXACT_TOKEN_BAND_RANGES[name]) for name in ("32k", "64k", "128k")
    }
    if config["capacity_bands"] != expected_bands:
        raise ValueError("P47 capacity bands must match repository exact bands")

    agency_response, agency_receipt = _json_request(
        config["official_endpoints"]["agency_reference"]
    )
    agency_matches = [
        item
        for item in agency_response.get("results", [])
        if item.get("toptier_code") == config["agency"]["toptier_code"]
        and item.get("abbreviation") == config["agency"]["abbreviation"]
        and item.get("agency_name") == config["agency"]["name"]
    ]
    agency_identity_pass = len(agency_matches) == 1

    submission_receipts = []
    certification = {}
    for period in (
        config["cutoff"]["earlier_period"],
        config["cutoff"]["later_period"],
    ):
        url = config["official_endpoints"]["submission_history_template"].format(
            period=period
        )
        response, receipt = _json_request(url)
        results = response.get("results", [])
        certified = sorted(
            item["certification_date"]
            for item in results
            if item.get("certification_date")
        )
        certification[str(period)] = {
            "published_versions": len(results),
            "certified_versions": len(certified),
            "latest_certification_date": certified[-1] if certified else None,
            "uncertified_published_versions": sum(
                item.get("certification_date") is None for item in results
            ),
        }
        submission_receipts.append({**receipt, "period": period})
    certification_pass = all(
        item["certified_versions"] >= 1 for item in certification.values()
    )

    archive_receipts = []
    monthly_list_receipts = []
    archive_metrics = {}
    award_lines: dict[str, dict[str, list[str]]] = {}
    award_coarsened_lines: dict[str, dict[str, list[str]]] = {}
    award_periods: dict[str, dict[str, set[str]]] = {}
    all_exact_lines: set[str] = set()
    all_coarsened_lines: set[str] = set()
    count_metrics = {}
    start = date.fromisoformat(config["cutoff"]["start_date"])
    earlier_end = date.fromisoformat(config["cutoff"]["earlier_end_date"])
    later_end = date.fromisoformat(config["cutoff"]["later_end_date"])
    for spec in config["archives"]:
        monthly_files, monthly_receipt = _json_request(
            config["official_endpoints"]["monthly_file_list"],
            {
                "agency": config["agency"]["bulk_download_agency_id"],
                "fiscal_year": config["cutoff"]["fiscal_year"],
                "type": spec["group"],
            },
        )
        advertised = [
            item
            for item in monthly_files.get("monthly_files", [])
            if item.get("url") == spec["url"]
        ]
        if len(advertised) != 1:
            raise ValueError(
                f"frozen archive is not currently advertised: {spec['group']}"
            )
        monthly_list_receipts.append(
            {
                **monthly_receipt,
                "group": spec["group"],
                "advertised_file_name": advertised[0].get("file_name"),
                "advertised_updated_date": advertised[0].get("updated_date"),
            }
        )
        raw, receipt = _request(spec["url"])
        if (
            len(raw) != spec["expected_bytes"]
            or _sha256(raw) != spec["expected_sha256"]
        ):
            raise ValueError(f"archive receipt changed: {spec['group']}")
        metrics, lines, coarse_lines, periods = _archive_rows(
            raw, spec, start, earlier_end, later_end
        )
        all_exact_lines.update(metrics.pop("identifier_free_exact_lines"))
        all_coarsened_lines.update(metrics.pop("amount_date_coarsened_lines"))
        archive_metrics[spec["group"]] = metrics
        award_lines[spec["group"]] = lines
        award_coarsened_lines[spec["group"]] = coarse_lines
        award_periods[spec["group"]] = periods
        archive_receipts.append(
            {
                **receipt,
                "group": spec["group"],
                "archive_persisted": False,
                "raw_rows_persisted": False,
            }
        )
        count, count_receipt = _download_count(
            config, spec["award_type_codes"], "transactions"
        )
        count_metrics[spec["group"]] = {
            "count_api_transactions": count["calculated_count"],
            "archive_selected_transactions": metrics["selected_p1_p9_rows"],
            "difference": metrics["selected_p1_p9_rows"] - count["calculated_count"],
            "count_response_sha256": count_receipt["sha256"],
        }

    subaward_metrics = {}
    relation_artifacts: set[str] = set()
    coarsened_relation_artifacts: set[str] = set()
    total_joined = 0
    total_eligible = 0
    all_subaward_exact_lines: set[str] = set()
    all_subaward_coarsened_lines: set[str] = set()
    for spec in config["subaward_groups"]:
        count, count_receipt = _download_count(
            config, spec["award_type_codes"], "subawards"
        )
        rows, search_receipt = _subawards(config, spec)
        internal_ids: set[str] = set()
        source_records: set[tuple[str, str, str, str]] = set()
        duplicate_internal_ids = 0
        duplicate_source_records = 0
        missing_prime_keys = 0
        unmatched_prime_keys = 0
        joined_rows = 0
        eligible_rows = 0
        subaward_lines_by_award: dict[str, list[str]] = defaultdict(list)
        subaward_coarsened_by_award: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            internal_id = str(row.get("internal_id") or "")
            if internal_id in internal_ids:
                duplicate_internal_ids += 1
            internal_ids.add(internal_id)
            prime_key = str(row.get("prime_award_generated_internal_id") or "")
            source_tuple = (
                prime_key,
                str(row.get("Sub-Award ID") or ""),
                str(row.get("Sub-Award Date") or ""),
                str(row.get("Sub-Award Amount") or ""),
            )
            if source_tuple in source_records:
                duplicate_source_records += 1
            source_records.add(source_tuple)
            safe_fields = ["Sub-Award Amount", "Sub-Award Date", "Sub-Award Type"]
            line = _safe_line(f"subaward_{spec['group']}", row, safe_fields)
            coarsened = _coarsened_line(f"subaward_{spec['group']}", row, safe_fields)
            all_subaward_exact_lines.add(line)
            all_subaward_coarsened_lines.add(coarsened)
            if not prime_key:
                missing_prime_keys += 1
                continue
            if prime_key not in award_lines[spec["group"]]:
                unmatched_prime_keys += 1
                continue
            joined_rows += 1
            subaward_lines_by_award[prime_key].append(line)
            subaward_coarsened_by_award[prime_key].append(coarsened)
            if award_periods[spec["group"]].get(prime_key) == {"P1_P6", "P7_P9"}:
                eligible_rows += 1
        eligible_awards = {
            key
            for key in subaward_lines_by_award
            if award_periods[spec["group"]].get(key) == {"P1_P6", "P7_P9"}
        }
        for key in eligible_awards:
            relation_artifacts.add(
                "\n".join(
                    sorted(
                        set(award_lines[spec["group"]][key])
                        | set(subaward_lines_by_award[key])
                    )
                )
            )
            coarsened_relation_artifacts.add(
                "\n".join(
                    sorted(
                        set(award_coarsened_lines[spec["group"]][key])
                        | set(subaward_coarsened_by_award[key])
                    )
                )
            )
        total_joined += joined_rows
        total_eligible += eligible_rows
        subaward_metrics[spec["group"]] = {
            "count_api_rows": count["calculated_count"],
            "configured_expected_count_api": spec["expected_count_api"],
            **search_receipt,
            "count_minus_grouped_search_rows": count["calculated_count"] - len(rows),
            "unique_internal_ids": len(internal_ids),
            "duplicate_internal_ids": duplicate_internal_ids,
            "duplicate_prime_subaward_date_amount_records": duplicate_source_records,
            "missing_prime_generated_key_rows": missing_prime_keys,
            "joined_to_prime_archive_rows": joined_rows,
            "unmatched_prime_archive_rows": unmatched_prime_keys,
            "joined_to_cross_p6_p9_prime_rows": eligible_rows,
            "cross_p6_p9_prime_awards_with_subawards": len(eligible_awards),
            "count_response_sha256": count_receipt["sha256"],
        }

    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=True,
    )
    source_layer_capacity = {
        "prime_identifier_free_exact_tokens": _token_count(tokenizer, all_exact_lines),
        "prime_amount_date_coarsened_tokens": _token_count(
            tokenizer, all_coarsened_lines
        ),
        "subaward_identifier_free_exact_tokens": _token_count(
            tokenizer, all_subaward_exact_lines
        ),
        "subaward_amount_date_coarsened_tokens": _token_count(
            tokenizer, all_subaward_coarsened_lines
        ),
        "cross_p6_p9_prime_plus_subaward_artifacts": len(relation_artifacts),
        "cross_p6_p9_relation_exact_tokens": _token_count(
            tokenizer, relation_artifacts
        ),
        "cross_p6_p9_relation_amount_date_coarsened_tokens": _token_count(
            tokenizer, coarsened_relation_artifacts
        ),
    }

    file_c_metrics = []
    file_c_available = True
    for probe in config["file_c_probes"]:
        status, receipt = _json_request(probe["status_url"])
        file_status = _head_status(probe["file_url"])
        usable = status.get("status") == "finished" and file_status["status"] == 200
        file_c_available = file_c_available and usable
        file_c_metrics.append(
            {
                "period": probe["period"],
                "status_url": probe["status_url"],
                "file_url": probe["file_url"],
                "download_job_status": status.get("status"),
                "status_response_sha256": receipt["sha256"],
                "file_head_status": file_status["status"],
                "file_content_type": file_status["content_type"],
                "usable_file_c_archive": usable,
                "file_c_rows_persisted": False,
            }
        )

    certified_chain_tokens = 0
    band_assessment = {
        name: {
            "source_layer_exact_capacity_possible": source_layer_capacity[
                "cross_p6_p9_relation_exact_tokens"
            ]
            >= bounds[0],
            "source_layer_coarsened_capacity_possible": source_layer_capacity[
                "cross_p6_p9_relation_amount_date_coarsened_tokens"
            ]
            >= bounds[0],
            "admissible_certified_chain_capacity_possible": certified_chain_tokens
            >= bounds[0],
            "certified_chain_gap_to_lower_edge": certified_chain_tokens - bounds[0],
        }
        for name, bounds in expected_bands.items()
    }
    schema_pass = all(
        metrics["missing_selected_transaction_or_award_keys"] == 0
        for metrics in archive_metrics.values()
    )
    count_reconciliation_pass = all(
        metrics["difference"] == 0 for metrics in count_metrics.values()
    )
    blocker = [
        "P6 and P9 File C jobs remain unavailable, so no row-level certified-period to D1/D2 award-key bridge was observed",
        "the current annual prime archives were regenerated in August 2026 and are not frozen as-certified P6/P9 snapshots",
        "File F subawards are reported by prime recipients through SAM.gov, not certified by NASA, and the official site warns of duplication and other quality defects",
        "the download count and grouped subaward search disagree for at least one award-type group, requiring raw File F reconciliation before an oracle can use subaward totals",
        "official repository CC0 terms do not by themselves resolve reuse rights and privacy/publicity interests for every third-party record field",
        "formal exact packing, near-duplicate, derived-view, remove-one, truncation, and 4K/8K/16K shortcut audits have not run",
    ]
    payload = {
        "schema_version": "longworld.p47-usaspending-certified-period-preflight.v1",
        "data_product": config["data_product"],
        "verdict": "SOURCE_CAPACITY_PASS_CERTIFIED_RELATION_CHAIN_FAIL",
        "rights_gate": "BLOCKED_DATASET_FIELD_REUSE_SCOPE_NOT_FORMALLY_CLEARED",
        "privacy_gate": "PASS_ONLY_FOR_EPHEMERAL_IDENTIFIER_FREE_PROJECTION",
        "credential_gate": "PASS_PUBLIC_API_OBSERVED_WITHOUT_CREDENTIALS",
        "agency_identity_gate": "PASS" if agency_identity_pass else "FAIL",
        "certification_metadata_gate": "PASS" if certification_pass else "FAIL",
        "prime_archive_schema_gate": "PASS" if schema_pass else "FAIL",
        "prime_count_reconciliation_gate": "PASS"
        if count_reconciliation_pass
        else "FAIL",
        "file_c_certified_bridge_gate": "PASS" if file_c_available else "FAIL",
        "subaward_certification_gate": "FAIL_NOT_AGENCY_CERTIFIED",
        "unique_capacity_gate": "FAIL_ZERO_ADMISSIBLE_CERTIFIED_CHAIN_TOKENS",
        "shortcut_gate": "NOT_RUN_BLOCKED_UPSTREAM",
        "world_admission_status": "BLOCKED",
        "do_not_generate": True,
        "train_ready": False,
        "production_eligible": False,
        "candidate_count": 0,
        "research_question": config["research_question"],
        "cutoff": config["cutoff"],
        "constraints": config["constraints"],
        "privacy_exclusions": config["privacy_exclusions"],
        "source_receipt": {
            "config": str(CONFIG),
            "config_sha256": _sha256(config_raw),
            "observed_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "agency_reference": agency_receipt,
            "submission_history": submission_receipts,
            "monthly_file_lists": monthly_list_receipts,
            "archives": archive_receipts,
            "raw_rows_or_source_identifiers_persisted": False,
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "certification": certification,
        "archive_metrics": archive_metrics,
        "transaction_count_reconciliation": count_metrics,
        "subaward_metrics": subaward_metrics,
        "relations": {
            "joined_subaward_rows": total_joined,
            "joined_subaward_rows_with_cross_p6_p9_prime": total_eligible,
            "certified_file_c_rows_observed": 0,
            "certified_file_c_to_prime_award_joins_observed": 0,
            "certified_file_c_to_prime_to_subaward_chains_observed": 0,
        },
        "file_c_probe": file_c_metrics,
        "capacity": {
            "source_layer_only_not_admissible": source_layer_capacity,
            "admissible_certified_chain_tokens": certified_chain_tokens,
            "formal_near_duplicate_audit_executed": False,
            "bands": expected_bands,
            "band_assessment": band_assessment,
            "interpretation": "prime and subaward source layers may have ample identifier-free volume, but none is admitted to the requested certified-period chain without observable P6/P9 File C rows and a valid certification boundary",
        },
        "answer_program": {
            "status": "BLOCKED_MISSING_CERTIFIED_FILE_C_BRIDGE",
            "deterministic_design": [
                "freeze P6 and P9 certified submission versions and source hashes",
                "validate File C schema and join PIID or FAIN/URI to D1/D2 using the official broker C8 and C11 rules",
                "reconcile File C and D1 obligations using C23.1 and cross-period outlay continuation using C27.1",
                "join separately sourced File F subawards to the prime award and deduplicate exact source records",
                "emit period-delta orphan mismatch duplicate raw and reconciled control totals",
            ],
            "answer_changing_remove_one": "not executable until a selected P9 File C row is source-observed; then removing it must change its TAS-award period delta or create an orphan/control-total mismatch",
            "medical_or_policy_causality_allowed": False,
        },
        "shortcut_assessment": {
            "status": "HIGH_RISK_UNTESTED",
            "risk": "award keys make lexical retrieval easy and aggregate totals can collapse the task; neither proves long-context dependence",
            "required_audits": [
                "4K 8K and 16K contiguous raw windows",
                "lexical and dense retrieval views",
                "answer-changing P9 File C remove-one replay",
                "exact and near-duplicate candidate audit",
                "derived-view and truncation gates",
            ],
        },
        "blocking_reasons": blocker,
        "next_executable_command": "uv run python reports/p47_usaspending_certified_period_preflight.py",
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "gates": {
                    key: value
                    for key, value in payload.items()
                    if key.endswith("_gate")
                },
                "relations": payload["relations"],
                "capacity": payload["capacity"],
                "blocking_reasons": blocker,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
