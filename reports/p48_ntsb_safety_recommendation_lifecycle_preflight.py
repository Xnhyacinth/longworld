"""Aggregate-only P48 NTSB recommendation-lifecycle preflight."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from transformers import AutoTokenizer

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p48_ntsb_safety_recommendation_lifecycle_preflight_v1.json")
OUTPUT = Path("reports/p48_ntsb_safety_recommendation_lifecycle_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"data.ntsb.gov"})
USER_AGENT = "LongWorld-P48-source-preflight/1.0 xnhyacinth@users.noreply.github.com"
RECOMMENDATION_ID = re.compile(r"[AHMPR]-\d{2}-\d{3}")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE = re.compile(r"(?<!\w)(?:\+?1[ .-]?)?(?:\(?\d{3}\)?[ .-])\d{3}[ .-]\d{4}(?!\w)")
HONORIFIC_PERSON = re.compile(r"(?i)\b(?:Mr|Ms|Mrs|Miss|Dr)\.\s+[A-Z][a-z]+")
PARAGRAPH_BREAK = re.compile(r"(?:\r?\n){2,}")
WHITESPACE = re.compile(r"\s+")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def _date(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"invalid {label}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"timezone-free {label}")
    return parsed


def _fetch(config: dict[str, Any], recommendation_id: str) -> tuple[str, bytes]:
    if RECOMMENDATION_ID.fullmatch(recommendation_id) is None:
        raise ValueError(f"invalid recommendation ID: {recommendation_id}")
    url = config["detail_url_template"].format(
        recommendation_id=quote(recommendation_id, safe="-")
    )
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or not parsed.path.startswith("/carol-main-public/api/Query/GetSrRecord/")
    ):
        raise ValueError(f"unapproved P48 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(
                request, timeout=60, context=ssl.create_default_context()
            ) as response:
                raw = response.read(8_000_001)
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                    raise ValueError(
                        f"unapproved P48 redirect target: {response.geturl()}"
                    )
                if response.status != 200 or not raw or len(raw) > 8_000_000:
                    raise ValueError(f"invalid P48 response for {recommendation_id}")
            break
        except urllib.error.HTTPError as error:
            if error.code not in {403, 429, 500, 502, 503, 504} or attempt == 3:
                raise
            time.sleep(2**attempt)
    return recommendation_id, raw


def _status_lookup(record: dict[str, Any]) -> dict[str, str]:
    matches = [
        item for item in record.get("Lookups", []) if item.get("Column") == "Status"
    ]
    if len(matches) != 1:
        raise ValueError("record does not contain exactly one status lookup")
    result = {
        str(option["Value"]): str(option["DisplayText"])
        for option in matches[0].get("Options", [])
    }
    if len(result) < 10:
        raise ValueError("status lookup is unexpectedly small")
    return result


def _correspondence(record: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for addressee in record.get("Addressees") or []:
        for item in addressee.get("Correspondence") or []:
            if not isinstance(item.get("IsFromNtsb"), bool):
                raise TypeError("correspondence direction is not boolean")
            _date(item.get("CorrespondenceDate", ""), "correspondence date")
            if not isinstance(item.get("ResponseSummary"), str):
                raise TypeError("response summary is not text")
            result.append(item)
    return sorted(result, key=lambda item: item["CorrespondenceDate"])


def _paragraphs(text: str) -> list[str]:
    return [
        normalized
        for paragraph in PARAGRAPH_BREAK.split(text)
        if len(normalized := WHITESPACE.sub(" ", paragraph).strip()) >= 80
    ]


def _token_lengths(tokenizer: Any, texts: list[str]) -> dict[str, int]:
    lengths: dict[str, int] = {}
    for offset in range(0, len(texts), 128):
        batch = texts[offset : offset + 128]
        encoded = tokenizer(
            batch,
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        lengths.update(zip(batch, map(len, encoded), strict=True))
    return lengths


def _band_pack(
    paragraphs: set[str], token_lengths: dict[str, int], lower: int, upper: int
) -> dict[str, Any]:
    ordered = sorted(paragraphs, key=lambda text: (_sha256(text.encode()), text))
    chosen: list[str] = []
    total = 0
    for paragraph in ordered:
        length = token_lengths[paragraph]
        if total + length > upper:
            continue
        chosen.append(paragraph)
        total += length
        if total >= lower:
            break
    digest_input = "".join(f"{_sha256(text.encode())}\n" for text in chosen)
    return {
        "feasible": lower <= total <= upper,
        "qwen_tokens": total,
        "paragraph_count": len(chosen),
        "paragraph_digest_sha256": _sha256(digest_input.encode()),
    }


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        name: list(EXACT_TOKEN_BAND_RANGES[name]) for name in ("32k", "64k", "128k")
    }
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P48 capacity bands must match repository exact bands")
    ids = config["recommendation_ids"]
    if len(ids) != 125 or len(set(ids)) != len(ids) or ids != sorted(ids):
        raise ValueError("P48 frozen recommendation IDs must be 125 sorted unique IDs")

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with ThreadPoolExecutor(max_workers=2) as executor:
        fetched = dict(executor.map(lambda item: _fetch(config, item), ids))
    bundle_lines = [f"{item}:{_sha256(fetched[item])}\n" for item in ids]
    bundle_sha256 = _sha256("".join(bundle_lines).encode())
    if bundle_sha256 != config["expected_bundle_sha256"]:
        raise ValueError(
            "P48 frozen source bundle changed; do not silently refresh the snapshot"
        )

    valid_ids: list[str] = []
    basic_ids: list[str] = []
    rejects: list[dict[str, Any]] = []
    modes: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    correspondence_types: Counter[str] = Counter()
    correspondence_count = 0
    recipient_count = 0
    ntsb_count = 0
    total_addressees = 0
    summaries_with_email = 0
    summaries_with_phone = 0
    summaries_with_honorific_person = 0
    records_with_two_statuses = 0
    paragraph_pools: dict[str, set[str]] = {
        "all_lifecycle_text": set(),
        "contact_excluded_all_authors": set(),
        "ntsb_authored_contact_excluded": set(),
        "ntsb_authored_contact_and_honorific_excluded": set(),
    }

    for recommendation_id in ids:
        record = json.loads(fetched[recommendation_id])
        if record.get("SridCleaned") != recommendation_id.replace("-", ""):
            raise ValueError(f"record ID mismatch for {recommendation_id}")
        status_lookup = _status_lookup(record)
        status = status_lookup.get(str(record.get("Status")))
        if status is None:
            raise ValueError(f"unknown current status for {recommendation_id}")
        modes[str(record.get("Mode"))] += 1
        statuses[status] += 1
        addressees = record.get("Addressees") or []
        total_addressees += len(addressees)
        correspondence = _correspondence(record)
        correspondence_count += len(correspondence)
        recipient = [item for item in correspondence if not item["IsFromNtsb"]]
        ntsb = [item for item in correspondence if item["IsFromNtsb"]]
        recipient_count += len(recipient)
        ntsb_count += len(ntsb)
        correspondence_types.update(
            str(item.get("CorrespondenceType")) for item in correspondence
        )
        closed_date = record.get("DateClosed")
        if closed_date:
            _date(closed_date, "closed date")
        later_ntsb = [
            item
            for item in ntsb
            if any(
                item["CorrespondenceDate"] > prior["CorrespondenceDate"]
                for prior in recipient
            )
        ]
        basic = bool(
            closed_date and status.startswith("Closed") and recipient and later_ntsb
        )
        status_phrase = _canonical(status)
        explicit_current_status = any(
            status_phrase in _canonical(item["ResponseSummary"]) for item in later_ntsb
        )
        if basic:
            basic_ids.append(recommendation_id)
        valid = basic and explicit_current_status
        if not valid:
            reasons = []
            if not closed_date or not status.startswith("Closed"):
                reasons.append("not_closed")
            if not recipient:
                reasons.append("no_recipient_response")
            if recipient and not later_ntsb:
                reasons.append("no_later_ntsb_correspondence")
            if basic and not explicit_current_status:
                reasons.append("current_status_not_explicit_in_later_ntsb_text")
            rejects.append({"recommendation_id": recommendation_id, "reasons": reasons})
            continue
        valid_ids.append(recommendation_id)

        mentioned = set()
        canonical_labels = sorted(
            {_canonical(label) for label in status_lookup.values()},
            key=len,
            reverse=True,
        )
        for item in ntsb:
            canonical_text = _canonical(item["ResponseSummary"])
            mentioned.update(
                label for label in canonical_labels if label in canonical_text
            )
        records_with_two_statuses += len(mentioned) >= 2

        for item in correspondence:
            text = item["ResponseSummary"]
            summaries_with_email += bool(EMAIL.search(text))
            summaries_with_phone += bool(PHONE.search(text))
            summaries_with_honorific_person += bool(HONORIFIC_PERSON.search(text))
            for paragraph in _paragraphs(text):
                paragraph_pools["all_lifecycle_text"].add(paragraph)
                has_contact = bool(EMAIL.search(paragraph) or PHONE.search(paragraph))
                if not has_contact:
                    paragraph_pools["contact_excluded_all_authors"].add(paragraph)
                if item["IsFromNtsb"] and not has_contact:
                    paragraph_pools["ntsb_authored_contact_excluded"].add(paragraph)
                    if not HONORIFIC_PERSON.search(paragraph):
                        paragraph_pools[
                            "ntsb_authored_contact_and_honorific_excluded"
                        ].add(paragraph)

    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=tokenizer_config["local_files_only"],
    )
    all_paragraphs = sorted(set().union(*paragraph_pools.values()))
    token_lengths = _token_lengths(tokenizer, all_paragraphs)
    capacity = {
        name: {
            "exact_unique_paragraph_count": len(paragraphs),
            "exact_unique_qwen_tokens": sum(token_lengths[item] for item in paragraphs),
        }
        for name, paragraphs in paragraph_pools.items()
    }
    conservative_name = "ntsb_authored_contact_and_honorific_excluded"
    conservative = paragraph_pools[conservative_name]
    capacity["conservative_exact_band_pack_preflight"] = {
        band: _band_pack(conservative, token_lengths, *bounds)
        for band, bounds in config["capacity_bands"].items()
    }
    all_bands_feasible = all(
        item["feasible"]
        for item in capacity["conservative_exact_band_pack_preflight"].values()
    )
    capacity["capacity_is_preflight_only"] = True
    capacity["eligible_for_candidate_generation"] = False

    report = {
        "schema_version": "longworld.p48-ntsb-safety-recommendation-lifecycle-preflight-report.v1",
        "data_product": config["data_product"],
        "config_sha256": _sha256(config_raw),
        "source_receipt": {
            "observed_at": observed_at,
            "record_count": len(fetched),
            "total_bytes": sum(map(len, fetched.values())),
            "min_record_bytes": min(map(len, fetched.values())),
            "max_record_bytes": max(map(len, fetched.values())),
            "bundle_sha256": bundle_sha256,
            "raw_records_persisted": False,
            "response_summaries_persisted": False,
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "topology": {
            "mode_counts": dict(sorted(modes.items())),
            "current_status_counts": dict(sorted(statuses.items())),
            "addressee_count": total_addressees,
            "correspondence_count": correspondence_count,
            "recipient_to_ntsb_count": recipient_count,
            "ntsb_to_recipient_count": ntsb_count,
            "correspondence_type_counts": dict(sorted(correspondence_types.items())),
            "basic_closed_lifecycle_count": len(basic_ids),
            "explicit_current_status_lifecycle_count": len(valid_ids),
            "records_with_at_least_two_explicit_status_labels": records_with_two_statuses,
            "rejected_record_count": len(rejects),
            "rejections": rejects,
        },
        "privacy_scan": {
            "scope": "correspondence summaries in explicit-current-status lifecycle records",
            "summaries_with_email": summaries_with_email,
            "summaries_with_phone": summaries_with_phone,
            "summaries_with_honorific_person": summaries_with_honorific_person,
            "detects_all_person_names": False,
            "privacy_cleared": False,
        },
        "capacity": capacity,
        "task_preflight": {
            "input_contract": "dated NTSB-authored correspondence paragraphs plus recipient-response event metadata; recipient-authored text is withheld",
            "deterministic_oracle": [
                "validate recommendation ID, correspondence direction, and timezone-aware ordering",
                "resolve the official current status code through the record-local CAROL status lookup",
                "canonicalize punctuation and require that a later NTSB-authored correspondence explicitly states the current status",
                "return the ordered status-classification timeline, current status, and closure date; reject ambiguity instead of guessing",
            ],
            "formula_dag_branches": [
                "recommendation/transmittal -> recipient-response event",
                "recipient-response event -> later NTSB evaluation and classification",
                "subsequent response/reclassification -> final NTSB closure status and date",
            ],
            "counterfactual_pool_count": records_with_two_statuses,
            "remove_one_expectation": "removing the final status-bearing NTSB correspondence must prevent recovery of the recorded current closure classification from the retained timeline",
            "oracle_implemented_for_candidate_rows": False,
        },
        "rights_and_release": {
            "ntsb_authored_material_public_domain_unless_noted": True,
            "recipient_authored_material_assumed_public_domain": False,
            "third_party_quotation_audit_complete": False,
            "legal_opinion": False,
        },
        "verdict": {
            "source_topology": "PASS",
            "conservative_32k_64k_128k_capacity_preflight": (
                "PASS" if all_bands_feasible else "FAIL"
            ),
            "privacy_and_third_party_rights": "FAIL",
            "historical_snapshot_and_candidate_oracle": "NOT_RUN",
            "train_ready": False,
            "do_not_generate": True,
            "candidate_count": 0,
            "inventory_delta": 0,
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
