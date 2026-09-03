"""Source-bound cumulative histories for long-context candidate construction.

The first executable adapter builds chronological audit histories from one
cryptographically bound CISA KEV catalog snapshot.  Longer bands append whole,
distinct catalog records; no record is copied, truncated, or padded.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.attestation import (
    LOCAL_PROBE_TRUST_ISOLATION_FIELD,
    attach_attestation,
    verify_attestation,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

DOMAIN_CUMULATIVE_HISTORY_SCHEMA = "longworld.domain-cumulative-history.v1"
KEV_HISTORY_REPLAY_REVISION = "longworld.kev-catalog-history-replay.v1"
KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA = "longworld.kev-history-replay-manifest.v1"
CROSS_CVE_HISTORY_SCHEMA = "longworld.cyber-cross-cve-history.v1"
CROSS_CVE_HISTORY_REPLAY_REVISION = "longworld.cyber-cross-cve-replay.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_CVE_ID = re.compile(r"^CVE-(?:1999|2\d{3})-\d{4,}$")
_EXPECTED_BANDS = ("16k", "32k", "64k", "128k")
_ENTRY_FIELDS = {
    "cveID",
    "vendorProject",
    "product",
    "vulnerabilityName",
    "dateAdded",
    "shortDescription",
    "requiredAction",
    "dueDate",
    "knownRansomwareCampaignUse",
    "notes",
    "cwes",
}
_CROSS_CVE_QUESTION = (
    "Across the NVD-to-CISA KEV joins, reconstruct every selected CVE "
    "remediation record, verify date-then-CVE chronology, and return ransomware "
    "use, status, remediation-window extrema, and vendor/year checkpoints."
)


@dataclass(frozen=True)
class HistoryBand:
    """One exact context-token band accepted by a materialization run."""

    name: str
    lower_tokens: int
    upper_tokens: int

    def __post_init__(self) -> None:
        if (
            not self.name
            or isinstance(self.lower_tokens, bool)
            or isinstance(self.upper_tokens, bool)
            or self.lower_tokens <= 0
            or self.upper_tokens < self.lower_tokens
        ):
            raise ValueError("invalid cumulative-history band")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_bound_json_retrieval(
    path: Path, retrieval: dict[str, Any]
) -> dict[str, Any]:
    """Read one JSON response only when its signed-receipt fields match bytes."""
    if not isinstance(retrieval, dict):
        raise ProvenanceError("source retrieval receipt is invalid")
    file_name = retrieval.get("retrieval_file")
    source_url = str(retrieval.get("final_url") or "")
    parsed_url = urlparse(source_url)
    if (
        not isinstance(file_name, str)
        or Path(file_name).name != file_name
        or path.name != file_name
        or retrieval.get("status") != 200
        or parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or _SHA256.fullmatch(str(retrieval.get("sha256") or "")) is None
    ):
        raise ProvenanceError("source retrieval receipt is invalid")
    _parse_timestamp(str(retrieval.get("observed_at") or ""), "observed_at")
    raw = _read_regular_file(path, MAX_SOURCE_BYTES)
    if hashlib.sha256(raw).hexdigest() != retrieval["sha256"]:
        raise ProvenanceError("source retrieval digest mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("source retrieval is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ProvenanceError("source retrieval JSON must be an object")
    return payload


def _validate_catalog(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    vulnerabilities = catalog.get("vulnerabilities")
    if (
        not isinstance(vulnerabilities, list)
        or not vulnerabilities
        or isinstance(catalog.get("count"), bool)
        or catalog.get("count") != len(vulnerabilities)
        or not all(
            isinstance(catalog.get(field), str) and str(catalog[field]).strip()
            for field in ("title", "catalogVersion", "dateReleased")
        )
    ):
        raise ProvenanceError("CISA KEV catalog metadata is invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in vulnerabilities:
        if not isinstance(item, dict) or set(item) != _ENTRY_FIELDS:
            raise ProvenanceError("CISA KEV catalog entry contract is invalid")
        cve_id = str(item.get("cveID") or "")
        if _CVE_ID.fullmatch(cve_id) is None:
            raise ProvenanceError("CISA KEV catalog entry has invalid CVE ID")
        if cve_id in seen:
            raise ProvenanceError(f"CISA KEV catalog has duplicate CVE: {cve_id}")
        seen.add(cve_id)
        for field in _ENTRY_FIELDS - {"cwes"}:
            if not isinstance(item.get(field), str):
                raise ProvenanceError("CISA KEV catalog entry has invalid text field")
        cwes = item.get("cwes")
        if not isinstance(cwes, list) or not all(
            isinstance(value, str) for value in cwes
        ):
            raise ProvenanceError("CISA KEV catalog entry has invalid CWE list")
        try:
            date.fromisoformat(item["dateAdded"])
            date.fromisoformat(item["dueDate"])
        except ValueError as error:
            raise ProvenanceError("CISA KEV catalog entry has invalid date") from error
        normalized.append(deepcopy(item))
    return sorted(normalized, key=lambda item: (item["dateAdded"], item["cveID"]))


def _catalog_header(catalog: dict[str, Any], retrieval_sha256: str) -> dict[str, Any]:
    return {
        "record_type": "catalog_snapshot",
        "source_record_id": f"cisa-kev-catalog:sha256:{retrieval_sha256}",
        "title": catalog["title"],
        "catalogVersion": catalog["catalogVersion"],
        "dateReleased": catalog["dateReleased"],
        "sourceEntryCount": catalog["count"],
    }


def _entry_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_type": "vulnerability",
        "source_record_id": f"cisa-kev:{item['cveID']}",
        "source_payload": item,
    }


def _context(records: Sequence[dict[str, Any]]) -> str:
    return "\n".join(_canonical_json(record) for record in records)


def _parse_context(context: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(context, str) or not context:
        raise ProvenanceError("KEV history context is empty")
    records: list[dict[str, Any]] = []
    for line in context.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProvenanceError(
                "KEV history context has invalid JSON record"
            ) from error
        if not isinstance(record, dict) or _canonical_json(record) != line:
            raise ProvenanceError("KEV history context record is not canonical")
        records.append(record)
    header = records[0]
    if (
        header.get("record_type") != "catalog_snapshot"
        or not str(header.get("source_record_id") or "").startswith(
            "cisa-kev-catalog:sha256:"
        )
        or not isinstance(header.get("sourceEntryCount"), int)
    ):
        raise ProvenanceError("KEV history catalog header is invalid")
    entries: list[dict[str, Any]] = []
    for record in records[1:]:
        payload = record.get("source_payload")
        if (
            record.get("record_type") != "vulnerability"
            or not isinstance(payload, dict)
            or record.get("source_record_id") != f"cisa-kev:{payload.get('cveID')}"
        ):
            raise ProvenanceError("KEV history source record is invalid")
        entries.append(payload)
    if not entries or header["sourceEntryCount"] < len(entries):
        raise ProvenanceError("KEV history selection is invalid")
    if entries != sorted(entries, key=lambda item: (item["dateAdded"], item["cveID"])):
        raise ProvenanceError("KEV history records are not chronological")
    if len({item["cveID"] for item in entries}) != len(entries):
        raise ProvenanceError("KEV history repeats a source record")
    return header, entries


def _record_ids(header: dict[str, Any], entries: Sequence[dict[str, Any]]) -> list[str]:
    return [
        str(header["source_record_id"]),
        *(f"cisa-kev:{e['cveID']}" for e in entries),
    ]


def _relation_ids(
    header: dict[str, Any], entries: Sequence[dict[str, Any]]
) -> list[str]:
    root = str(header["source_record_id"])
    contains = [
        f"catalog-contains:{root}:cisa-kev:{entry['cveID']}" for entry in entries
    ]
    chronology = [
        f"verified-derived-order:cisa-kev:{before['cveID']}:cisa-kev:{after['cveID']}"
        for before, after in pairwise(entries)
    ]
    return [*contains, *chronology]


def _authentic_relation_edges(
    header: dict[str, Any], entries: Sequence[dict[str, Any]]
) -> list[list[str]]:
    root = str(header["source_record_id"])
    return [
        [
            root,
            f"cisa-kev:{entry['cveID']}",
            f"catalog-contains:{root}:cisa-kev:{entry['cveID']}",
        ]
        for entry in entries
    ]


def _derived_order_relation_edges(
    entries: Sequence[dict[str, Any]],
) -> list[list[str]]:
    return [
        [
            f"cisa-kev:{before['cveID']}",
            f"cisa-kev:{after['cveID']}",
            f"verified-derived-order:cisa-kev:{before['cveID']}:cisa-kev:{after['cveID']}",
        ]
        for before, after in pairwise(entries)
    ]


def _entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "cve_id": entry["cveID"],
        "date_added": entry["dateAdded"],
        "due_date": entry["dueDate"],
        "known_ransomware_use": entry["knownRansomwareCampaignUse"],
        "required_action": entry["requiredAction"],
    }


def _answer(header: dict[str, Any], entries: Sequence[dict[str, Any]]) -> str:
    checkpoints: list[dict[str, Any]] = []
    by_year: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_year.setdefault(entry["dateAdded"][:4], []).append(entry)
    for year, annual in sorted(by_year.items()):
        checkpoints.append(
            {
                "year": year,
                "entry_count": len(annual),
                "known_ransomware_count": sum(
                    item["knownRansomwareCampaignUse"] == "Known" for item in annual
                ),
                **_entry_summary(annual[-1]),
            }
        )
    maximum = max(
        entries,
        key=lambda item: (
            (
                date.fromisoformat(item["dueDate"])
                - date.fromisoformat(item["dateAdded"])
            ).days,
            item["dateAdded"],
            item["cveID"],
        ),
    )
    return _canonical_json(
        {
            "catalog_version": header["catalogVersion"],
            "release_date": header["dateReleased"],
            "date_then_cve_order_valid": all(
                (before["dateAdded"], before["cveID"])
                <= (after["dateAdded"], after["cveID"])
                for before, after in pairwise(entries)
            ),
            "same_day_tie_break": "cve_id_lexical",
            "selected_entry_count": len(entries),
            "first_added": _entry_summary(entries[0]),
            "last_added": _entry_summary(entries[-1]),
            "maximum_remediation_window": {
                **_entry_summary(maximum),
                "days": (
                    date.fromisoformat(maximum["dueDate"])
                    - date.fromisoformat(maximum["dateAdded"])
                ).days,
            },
            "year_end_checkpoints": checkpoints,
        }
    )


def _cross_cve_line_id(record: dict[str, Any]) -> str:
    if record.get("record_type") == "cyber_source_record":
        return str(record.get("record_id") or "")
    if record.get("record_type") == "cyber_source_relation":
        return str(record.get("relation_id") or "")
    return ""


def _parse_cross_cve_context(context: object) -> list[dict[str, Any]]:
    if not isinstance(context, str) or not context:
        raise ProvenanceError("cross-CVE context is empty")
    records: list[dict[str, Any]] = []
    identities: set[str] = set()
    for line in context.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProvenanceError("cross-CVE context has invalid JSON") from error
        identity = _cross_cve_line_id(record) if isinstance(record, dict) else ""
        if (
            not isinstance(record, dict)
            or _canonical_json(record) != line
            or not identity
            or identity in identities
        ):
            raise ProvenanceError("cross-CVE context record is invalid")
        identities.add(identity)
        records.append(record)
    return records


def _cross_cve_joined_records(
    records: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_records: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("record_type") == "cyber_source_record":
            record_id = str(record.get("record_id") or "")
            cve_id = str(record.get("cve_id") or "")
            kind = str(record.get("kind") or "")
            text = record.get("text")
            parsed_url = urlparse(str(record.get("source_url") or ""))
            if (
                kind not in {"nvd_cve", "cisa_kev_entry"}
                or _CVE_ID.fullmatch(cve_id) is None
                or record_id
                != (f"nvd:{cve_id}" if kind == "nvd_cve" else f"cisa-kev:{cve_id}")
                or parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or _SHA256.fullmatch(str(record.get("source_sha256") or "")) is None
                or not isinstance(text, str)
                or record.get("text_sha256") != _sha256_text(text)
            ):
                raise ProvenanceError("cross-CVE source record is invalid")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                raise ProvenanceError("cross-CVE source text is invalid") from error
            identity_field = "id" if kind == "nvd_cve" else "cveID"
            if not isinstance(payload, dict) or payload.get(identity_field) != cve_id:
                raise ProvenanceError("cross-CVE source identity is invalid")
            source_records[record_id] = {**record, "payload": payload}
        elif record.get("record_type") == "cyber_source_relation":
            relation_id = str(record.get("relation_id") or "")
            if record.get("kind") != "listed_in_kev" or not relation_id.startswith(
                "cyber:listed-in-kev:CVE-"
            ):
                raise ProvenanceError("cross-CVE source relation is invalid")
            relations[relation_id] = record
        else:
            raise ProvenanceError("cross-CVE record type is invalid")

    joined: list[dict[str, Any]] = []
    for relation_id, relation in relations.items():
        cve_id = relation_id.removeprefix("cyber:listed-in-kev:")
        nvd = source_records.get(f"nvd:{cve_id}")
        kev = source_records.get(f"cisa-kev:{cve_id}")
        if (
            nvd is None
            or kev is None
            or relation.get("source_record_id") != kev["record_id"]
            or relation.get("target_record_id") != nvd["record_id"]
        ):
            continue
        nvd_payload = nvd["payload"]
        kev_payload = kev["payload"]
        required = (
            "dateAdded",
            "dueDate",
            "knownRansomwareCampaignUse",
            "product",
            "requiredAction",
            "vendorProject",
        )
        if not isinstance(nvd_payload.get("vulnStatus"), str) or not all(
            isinstance(kev_payload.get(field), str) for field in required
        ):
            raise ProvenanceError("cross-CVE answer facts are incomplete")
        date_added = date.fromisoformat(kev_payload["dateAdded"])
        due_date = date.fromisoformat(kev_payload["dueDate"])
        joined.append(
            {
                "cve_id": cve_id,
                "vulnerability_status": nvd_payload["vulnStatus"],
                "date_added": date_added.isoformat(),
                "due_date": due_date.isoformat(),
                "remediation_window_days": (due_date - date_added).days,
                "known_ransomware_use": kev_payload["knownRansomwareCampaignUse"],
                "vendor": kev_payload["vendorProject"],
                "product": kev_payload["product"],
                "required_action": kev_payload["requiredAction"],
            }
        )
    if len(joined) < 2:
        raise ProvenanceError("cross-CVE replay requires at least two complete joins")
    return sorted(joined, key=lambda item: (item["date_added"], item["cve_id"]))


def _cross_cve_answer(joined: Sequence[dict[str, Any]]) -> str:
    vendor_counts: dict[str, int] = {}
    year_counts: dict[str, int] = {}
    for item in joined:
        vendor_counts[item["vendor"]] = vendor_counts.get(item["vendor"], 0) + 1
        year = item["date_added"][:4]
        year_counts[year] = year_counts.get(year, 0) + 1
    maximum = max(
        joined,
        key=lambda item: (
            item["remediation_window_days"],
            item["date_added"],
            item["cve_id"],
        ),
    )
    return _canonical_json(
        {
            "selected_cve_count": len(joined),
            "date_then_cve_order_valid": all(
                (left["date_added"], left["cve_id"])
                <= (right["date_added"], right["cve_id"])
                for left, right in pairwise(joined)
            ),
            "known_ransomware_count": sum(
                item["known_ransomware_use"] == "Known" for item in joined
            ),
            "maximum_remediation_window": {
                "cve_id": maximum["cve_id"],
                "days": maximum["remediation_window_days"],
            },
            "vendor_counts": [
                {"vendor": vendor, "count": count}
                for vendor, count in sorted(vendor_counts.items())
            ],
            "year_counts": [
                {"year": year, "count": count}
                for year, count in sorted(year_counts.items())
            ],
            "joined_records": list(joined),
        }
    )


def replay_cross_cve_remediation_history(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Replay a cross-CVE NVD/KEV aggregation from complete source-role records."""
    try:
        records = _parse_cross_cve_context(task.get("context"))
        if evidence_ids is not None:
            selected = list(evidence_ids)
            if (
                isinstance(evidence_ids, (str, bytes))
                or any(not isinstance(value, str) or not value for value in selected)
                or len(selected) != len(set(selected))
            ):
                raise ProvenanceError("cross-CVE evidence selection is invalid")
            available = {_cross_cve_line_id(record) for record in records}
            if not set(selected) <= available:
                raise ProvenanceError("cross-CVE evidence selection is invalid")
            selected_ids = set(selected)
            records = [
                record
                for record in records
                if _cross_cve_line_id(record) in selected_ids
            ]
        if counterfactual:
            twin = task.get("counterfactual_twin")
            if not isinstance(twin, dict):
                raise ProvenanceError("cross-CVE counterfactual is missing")
            matches = [
                record
                for record in records
                if record.get("record_type") == "cyber_source_record"
                and record.get("record_id") == twin.get("record_id")
            ]
            if (
                len(matches) != 1
                or twin.get("source_origin") != "synthetic_counterfactual"
                or twin.get("provenance_operation") != "replace_due_date"
                or not isinstance(twin.get("parent_value"), str)
                or not isinstance(twin.get("value"), str)
                or twin["parent_value"] == twin["value"]
            ):
                raise ProvenanceError("cross-CVE counterfactual is invalid")
            record = matches[0]
            payload = json.loads(str(record["text"]))
            if payload.get("dueDate") != twin["parent_value"]:
                raise ProvenanceError("cross-CVE counterfactual parent is invalid")
            date.fromisoformat(twin["value"])
            payload["dueDate"] = twin["value"]
            record["text"] = _canonical_json(payload)
            record["text_sha256"] = _sha256_text(record["text"])
        joined = _cross_cve_joined_records(records)
        source_record_ids = sorted(
            record["record_id"]
            for record in records
            if record.get("record_type") == "cyber_source_record"
        )
        relation_ids = sorted(
            record["relation_id"]
            for record in records
            if record.get("record_type") == "cyber_source_relation"
        )
        authentic_edges = [
            [
                f"cisa-kev:{item['cve_id']}",
                f"nvd:{item['cve_id']}",
                f"cyber:listed-in-kev:{item['cve_id']}",
            ]
            for item in joined
        ]
        derived_edges = [
            [
                f"nvd:{left['cve_id']}",
                f"nvd:{right['cve_id']}",
                (f"verified-derived-order:nvd:{left['cve_id']}:nvd:{right['cve_id']}"),
            ]
            for left, right in pairwise(joined)
        ]
        return {
            "answer": _cross_cve_answer(joined),
            "source_record_ids": source_record_ids,
            "source_relation_ids": relation_ids,
            "authentic_source_relation_edges": authentic_edges,
            "verified_derived_order_relation_edges": derived_edges,
            "event_count": len(source_record_ids) + len(relation_ids),
            "strict_support_event_count": len(source_record_ids) + len(relation_ids),
            "proof_depth": len(joined),
            "hop_count": len(joined),
        }
    except (KeyError, TypeError, ValueError, ProvenanceError):
        return {
            "answer": "unknown",
            "source_record_ids": [],
            "source_relation_ids": [],
            "authentic_source_relation_edges": [],
            "verified_derived_order_relation_edges": [],
            "event_count": 0,
            "strict_support_event_count": 0,
            "proof_depth": 0,
            "hop_count": 0,
        }


def _cross_cve_source_units(
    manifest: dict[str, Any],
) -> list[tuple[str, str, list[str]]]:
    raw_records = manifest.get("records")
    raw_relations = manifest.get("relations")
    if not isinstance(raw_records, list) or not isinstance(raw_relations, list):
        raise ProvenanceError("cross-CVE source manifest is incomplete")
    records = {
        str(record.get("record_id") or ""): record
        for record in raw_records
        if isinstance(record, dict)
    }
    relations = {
        str(relation.get("relation_id") or ""): relation
        for relation in raw_relations
        if isinstance(relation, dict)
    }
    if len(records) != len(raw_records) or len(relations) != len(raw_relations):
        raise ProvenanceError("cross-CVE source manifest repeats identities")
    units: list[tuple[str, str, list[str]]] = []
    joined_record_ids: set[str] = set()
    for relation_id, relation in relations.items():
        if not relation_id.startswith("cyber:listed-in-kev:"):
            raise ProvenanceError("cross-CVE source relation is invalid")
        cve_id = relation_id.removeprefix("cyber:listed-in-kev:")
        nvd = records.get(f"nvd:{cve_id}")
        kev = records.get(f"cisa-kev:{cve_id}")
        if nvd is None or kev is None:
            raise ProvenanceError("cross-CVE source join is incomplete")
        joined_record_ids.update((nvd["record_id"], kev["record_id"]))
        documents: list[str] = []
        for record in (nvd, kev):
            documents.append(
                _canonical_json(
                    {
                        "record_type": "cyber_source_record",
                        "record_id": record["record_id"],
                        "kind": record["kind"],
                        "cve_id": record["cve_id"],
                        "source_url": record["source_url"],
                        "source_sha256": record["source_sha256"],
                        "text_sha256": record["text_sha256"],
                        "text": record["text"],
                    }
                )
            )
        documents.append(
            _canonical_json(
                {
                    "record_type": "cyber_source_relation",
                    "relation_id": relation_id,
                    "kind": relation["kind"],
                    "source_record_id": relation["source_record_id"],
                    "target_record_id": relation["target_record_id"],
                }
            )
        )
        try:
            date_added = str(json.loads(str(kev["text"]))["dateAdded"])
            date.fromisoformat(date_added)
        except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
            raise ProvenanceError("cross-CVE source date is invalid") from error
        units.append((date_added, cve_id, documents))
    if joined_record_ids != set(records):
        raise ProvenanceError("cross-CVE source join coverage is incomplete")
    if len(units) < 2 or len(units) > 20:
        raise ProvenanceError("cross-CVE adapter requires 2 to 20 complete joins")
    return sorted(units, key=lambda item: (item[0], item[1]))


def _cross_cve_subset_documents(
    units: Sequence[tuple[str, str, list[str]]], mask: int
) -> list[str]:
    return [
        document
        for index, (_date_added, _cve_id, documents) in enumerate(units)
        if mask & (1 << index)
        for document in documents
    ]


_RAW_TOKEN_WINDOW = 4096


def _cross_cve_document_cve_id(record: Mapping[str, Any]) -> str:
    return str(
        record.get("cve_id")
        or str(record.get("relation_id") or "").removeprefix("cyber:listed-in-kev:")
    )


def _cross_cve_cve_dates(documents: Sequence[str]) -> dict[str, str]:
    dates: dict[str, str] = {}
    for document in documents:
        record = json.loads(document)
        if record.get("kind") != "cisa_kev_entry":
            continue
        payload = json.loads(str(record.get("text") or ""))
        cve_id = str(record.get("cve_id") or "")
        date_added = (
            str(payload.get("dateAdded") or "") if isinstance(payload, dict) else ""
        )
        if cve_id and date_added:
            dates[cve_id] = date_added
    return dates


def _cross_cve_chrono_documents(documents: Sequence[str]) -> list[str]:
    dates = _cross_cve_cve_dates(documents)
    keyed: list[tuple[str, str]] = []
    for document in documents:
        record = json.loads(document)
        cve_id = _cross_cve_document_cve_id(record)
        ident = str(record.get("record_id") or record.get("relation_id") or "")
        keyed.append((f"{dates.get(cve_id, '')}|{cve_id}|{ident}", document))
    return [document for _key, document in sorted(keyed)]


def _cross_cve_spread_of(documents: Sequence[str]) -> list[str]:
    spread: list[str] = []
    left = 0
    right = len(documents) - 1
    while left <= right:
        spread.append(documents[left])
        left += 1
        if left <= right:
            spread.append(documents[right])
            right -= 1
    return spread


def _cross_cve_spread_documents(documents: Sequence[str]) -> list[str]:
    return _cross_cve_spread_of(_cross_cve_chrono_documents(documents))


def _cross_cve_token_spans(
    documents: Sequence[str], token_counter: Callable[[str], int]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for index in range(len(documents)):
        start = token_counter(SEP.join(documents[:index]) + SEP) if index else 0
        end = token_counter(SEP.join(documents[: index + 1]))
        spans.append((start, end))
    return spans


def _cross_cve_selected_joins_complete(
    documents: Sequence[str], selected: Sequence[int]
) -> bool:
    required: dict[str, set[str]] = {}
    observed: dict[str, set[str]] = {}
    for index, document in enumerate(documents):
        record = json.loads(document)
        cve_id = _cross_cve_document_cve_id(record)
        kind = str(record.get("kind") or "")
        if not cve_id or not kind:
            return False
        required.setdefault(cve_id, set()).add(kind)
        if index in selected:
            observed.setdefault(cve_id, set()).add(kind)
    return bool(required) and all(
        kinds <= observed.get(cve_id, set()) for cve_id, kinds in required.items()
    )


def _cross_cve_order_has_4k_gold_window(
    documents: Sequence[str], token_counter: Callable[[str], int]
) -> bool:
    """Return True when a 4k intersecting window can replay the full gold join."""
    if len(documents) < 2:
        return False
    spans = _cross_cve_token_spans(documents, token_counter)
    total = spans[-1][1]
    if total < _RAW_TOKEN_WINDOW:
        return False
    starts = {0}
    for start, end in spans:
        starts.add(start)
        if end > 0:
            starts.add(end - 1)
    max_start = total - _RAW_TOKEN_WINDOW
    for start in starts:
        window_start = min(max(start, 0), max_start)
        window_end = window_start + _RAW_TOKEN_WINDOW
        intersecting = [
            index
            for index, (artifact_start, artifact_end) in enumerate(spans)
            if artifact_start < window_end and artifact_end > window_start
        ]
        if _cross_cve_selected_joins_complete(documents, intersecting):
            return True
    return False


def _cross_cve_documents_fail_4k_bound(
    documents: Sequence[str], token_counter: Callable[[str], int]
) -> bool:
    """Skip packs whose unit, ordered, or spread views leak gold into a 4k window."""
    unit = list(documents)
    chrono = _cross_cve_chrono_documents(unit)
    return any(
        _cross_cve_order_has_4k_gold_window(order, token_counter)
        for order in (
            unit,
            chrono,
            _cross_cve_spread_of(unit),
            _cross_cve_spread_of(chrono),
        )
    )


def _select_cross_cve_band_mask(
    units: Sequence[tuple[str, str, list[str]]],
    *,
    required_mask: int,
    band: HistoryBand,
    token_counter: Callable[[str], int],
) -> tuple[int, list[str], int]:
    unit_weights = [token_counter(SEP.join(unit[2])) for unit in units]
    for mask in range(1, 1 << len(units)):
        if mask & required_mask != required_mask or mask.bit_count() < max(
            2, required_mask.bit_count() + 1
        ):
            continue
        approximate = (
            sum(
                weight
                for index, weight in enumerate(unit_weights)
                if mask & (1 << index)
            )
            + max(0, mask.bit_count() - 1) * token_counter(SEP)
            + token_counter(_CROSS_CVE_QUESTION)
            + 256
        )
        if not band.lower_tokens - 4_096 <= approximate <= band.upper_tokens + 4_096:
            continue
        documents = _cross_cve_subset_documents(units, mask)
        tokens = token_counter(
            wrap_prompt(_CROSS_CVE_QUESTION, SEP.join(documents), "first")
        )
        if band.lower_tokens <= tokens <= band.upper_tokens:
            if _cross_cve_documents_fail_4k_bound(documents, token_counter):
                continue
            return mask, documents, tokens
    raise ProvenanceError(f"verified cross-CVE source cannot fill {band.name}")


def build_cross_cve_remediation_history_candidates(
    manifest: dict[str, Any],
    *,
    world_id: str,
    source_binding: dict[str, Any],
    bands: Sequence[HistoryBand],
    token_counter: Callable[[str], int],
    tokenizer_model_id: str,
    tokenizer_revision: str,
) -> list[dict[str, Any]]:
    """Build nested exact-band cross-source CVE histories without padding."""
    if (
        not world_id
        or not tokenizer_model_id
        or _COMMIT_SHA.fullmatch(tokenizer_revision) is None
        or [band.name for band in bands] != list(_EXPECTED_BANDS[: len(bands)])
    ):
        raise ProvenanceError("cross-CVE materialization identity is invalid")
    required_binding = {
        "source_manifest_sha256",
        "fetch_inventory_sha256",
        "authorization_record_id",
        "observed_at",
    }
    if set(source_binding) != required_binding or any(
        _SHA256.fullmatch(str(source_binding.get(field) or "")) is None
        for field in ("source_manifest_sha256", "fetch_inventory_sha256")
    ):
        raise ProvenanceError("cross-CVE source binding is invalid")
    if not str(source_binding.get("authorization_record_id") or ""):
        raise ProvenanceError("cross-CVE authorization binding is invalid")
    _parse_timestamp(str(source_binding.get("observed_at") or ""), "observed_at")

    units = _cross_cve_source_units(manifest)
    rows: list[dict[str, Any]] = []
    selected_mask = 0
    previous_cves: set[str] = set()
    for band in bands:
        selected_mask, documents, context_tokens = _select_cross_cve_band_mask(
            units,
            required_mask=selected_mask,
            band=band,
            token_counter=token_counter,
        )
        context = "\n".join(documents)
        provisional = {
            "context": context,
            "counterfactual_twin": {},
        }
        replay = replay_cross_cve_remediation_history(provisional)
        if replay["answer"] == "unknown":
            raise ProvenanceError("cross-CVE selected source does not replay")
        joined = json.loads(replay["answer"])["joined_records"]
        target = joined[-1]
        replacement = (
            "2099-12-31" if target["due_date"] != "2099-12-31" else "2098-12-31"
        )
        twin = {
            "record_id": f"cisa-kev:{target['cve_id']}",
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_due_date",
            "parent_value": target["due_date"],
            "value": replacement,
        }
        task = {"context": context, "counterfactual_twin": twin}
        replay = replay_cross_cve_remediation_history(task)
        selected_cves = {item["cve_id"] for item in joined}
        if not previous_cves < selected_cves:
            raise ProvenanceError("cross-CVE semantic state did not grow")
        previous_cves = selected_cves
        row: dict[str, Any] = {
            "schema_version": CROSS_CVE_HISTORY_SCHEMA,
            "data_stage": "candidate_history",
            "train_ready": False,
            "production_eligible": False,
            "promotion_eligible": False,
            "complete_world": False,
            "promoted": False,
            "generation_integration": "disabled",
            "world_id": world_id,
            "domain": "cyber",
            "workflow_kind": "real_source_derived",
            "query_type": "cross_cve_remediation_reconstruction",
            "answer_program_id": "cyber.cross_cve_remediation_reconstruction.v1",
            "answer_program_operations": [
                "JOIN_NVD_CISA_BY_CVE",
                "ORDER_DATE_THEN_CVE",
                "GROUP_VENDOR_AND_YEAR",
                "COUNT_KNOWN_RANSOMWARE",
                "SELECT_MAX_REMEDIATION_WINDOW",
            ],
            "semantic_growth_group_id": f"{world_id}|cross-cve-remediation",
            "length_bucket": band.name,
            "question": _CROSS_CVE_QUESTION,
            "context": context,
            "context_sha256": _sha256_text(context),
            "answer": replay["answer"],
            "cf_answer": replay_cross_cve_remediation_history(
                task, counterfactual=True
            )["answer"],
            "counterfactual_twin": twin,
            "source_binding": deepcopy(source_binding),
            "source_record_ids": replay["source_record_ids"],
            "source_relation_ids": replay["source_relation_ids"],
            "essential_evidence_ids": [
                *replay["source_record_ids"],
                *replay["source_relation_ids"],
            ],
            "event_count": replay["event_count"],
            "strict_support_event_count": replay["strict_support_event_count"],
            "graph": {
                "proof_depth": replay["proof_depth"],
                "hop_count": replay["hop_count"],
            },
            "authentic_source_relation_edges": replay[
                "authentic_source_relation_edges"
            ],
            "verified_derived_order_relation_edges": replay[
                "verified_derived_order_relation_edges"
            ],
            "tokenizer_model_id": tokenizer_model_id,
            "tokenizer_revision": tokenizer_revision,
            "tokenizer_context_tokens": context_tokens,
            "actual_context_tokens": context_tokens,
            "band_lower_tokens": band.lower_tokens,
            "band_upper_tokens": band.upper_tokens,
            "semantic_tokens": {
                "internal": context_tokens,
                "event_bearing": context_tokens,
                "proof_bearing": context_tokens,
                "causal_supporting": 0,
                "generic_background": 0,
            },
            "real_source_verified": False,
            "source_verified_at_materialization": True,
            "real_source_token_ratio": 1.0,
            "strict_replay_revision": CROSS_CVE_HISTORY_REPLAY_REVISION,
        }
        audit = audit_cross_cve_remediation_history_candidate(
            row, token_counter=token_counter
        )
        if not audit or not all(audit.values()):
            failed = sorted(name for name, passed in audit.items() if not passed)
            raise ProvenanceError(
                "cross-CVE history candidate failed: " + ",".join(failed)
            )
        rows.append(row)
    return rows


def audit_cross_cve_remediation_history_candidate(
    task: dict[str, Any], *, token_counter: Callable[[str], int]
) -> dict[str, bool]:
    """Recompute answer, CF, evidence necessity, corruption and exact length."""
    replay = replay_cross_cve_remediation_history(task)
    cf_replay = replay_cross_cve_remediation_history(task, counterfactual=True)
    essentials = task.get("essential_evidence_ids")
    essentials = essentials if isinstance(essentials, list) else []
    removals = [
        replay_cross_cve_remediation_history(
            task,
            evidence_ids=[value for value in essentials if value != removed],
        )["answer"]
        for removed in essentials
    ]
    singles = [
        replay_cross_cve_remediation_history(task, evidence_ids=[value])["answer"]
        for value in essentials
    ]
    corrupted = deepcopy(task)
    lines = str(corrupted.get("context") or "").splitlines()
    corruption_fails = False
    source_index = next(
        (
            index
            for index, line in enumerate(lines)
            if json.loads(line).get("record_type") == "cyber_source_record"
        ),
        None,
    )
    if source_index is not None:
        record = json.loads(lines[source_index])
        record["text"] += " CORRUPTED"
        lines[source_index] = _canonical_json(record)
        corrupted["context"] = "\n".join(lines)
        corruption_fails = (
            replay_cross_cve_remediation_history(corrupted)["answer"] == "unknown"
        )
    document_context = SEP.join(str(task.get("context") or "").splitlines())
    recomputed_tokens = token_counter(
        wrap_prompt(str(task.get("question") or ""), document_context, "first")
    )
    declared_tokens = task.get("tokenizer_context_tokens")
    lower = task.get("band_lower_tokens")
    upper = task.get("band_upper_tokens")
    return {
        "strict_replay_sufficient": replay["answer"] == task.get("answer"),
        "counterfactual_replay_sufficient": cf_replay["answer"]
        == task.get("cf_answer"),
        "counterfactual_changes_answer": cf_replay["answer"] != replay["answer"],
        "remove_one_fails": bool(removals)
        and all(answer != task.get("answer") for answer in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(answer != task.get("answer") for answer in singles),
        "semantic_corruption_fails": corruption_fails,
        "answer_surface_free": str(task.get("answer") or "") not in document_context,
        "source_records_replayed": task.get("source_record_ids")
        == replay["source_record_ids"],
        "source_relations_replayed": task.get("source_relation_ids")
        == replay["source_relation_ids"],
        "graph_replayed": task.get("event_count") == replay["event_count"]
        and task.get("strict_support_event_count")
        == replay["strict_support_event_count"]
        and (task.get("graph") or {}).get("proof_depth") == replay["proof_depth"]
        and (task.get("graph") or {}).get("hop_count") == replay["hop_count"]
        and task.get("authentic_source_relation_edges")
        == replay["authentic_source_relation_edges"]
        and task.get("verified_derived_order_relation_edges")
        == replay["verified_derived_order_relation_edges"],
        "exact_token_count_recomputed": isinstance(declared_tokens, int)
        and not isinstance(declared_tokens, bool)
        and declared_tokens == recomputed_tokens,
        "exact_token_band_recomputed": isinstance(lower, int)
        and isinstance(upper, int)
        and lower <= recomputed_tokens <= upper,
        "non_promoted_boundary": all(
            task.get(field) is False
            for field in (
                "train_ready",
                "production_eligible",
                "promotion_eligible",
                "complete_world",
                "promoted",
            )
        ),
    }


def _cross_cve_pipeline_documents(context: str) -> list[str]:
    lines = [line for line in str(context).splitlines() if line]
    if len(lines) < 4:
        raise ProvenanceError("cross-CVE pipeline requires at least four source lines")
    for line in lines:
        record = json.loads(line)
        if (
            not isinstance(record, dict)
            or _canonical_json(record) != line
            or not _cross_cve_line_id(record)
        ):
            raise ProvenanceError("cross-CVE pipeline document line is invalid")
    return lines


def _cross_cve_unknown_replay() -> dict[str, Any]:
    return {
        "answer": "unknown",
        "source_record_ids": [],
        "source_relation_ids": [],
        "authentic_source_relation_edges": [],
        "verified_derived_order_relation_edges": [],
        "event_count": 0,
        "strict_support_event_count": 0,
        "proof_depth": 0,
        "hop_count": 0,
    }


def replay_cross_cve_pipeline_candidate(
    candidate: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_artifact_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Replay one serialized cross-CVE candidate from its document bodies."""
    try:
        document_context = candidate.get("document_context")
        classifications = candidate.get("artifact_classification")
        if (
            not isinstance(document_context, str)
            or not isinstance(classifications, list)
            or not classifications
        ):
            raise ProvenanceError("cross-CVE pipeline artifact pool is missing")
        documents = document_context.split(SEP)
        if len(documents) != len(classifications):
            raise ProvenanceError("cross-CVE pipeline artifact pool is unbound")
        available_artifact_ids = [
            str(item.get("artifact_id") or "")
            for item in classifications
            if isinstance(item, dict)
        ]
        if (
            len(available_artifact_ids) != len(classifications)
            or "" in available_artifact_ids
            or len(available_artifact_ids) != len(set(available_artifact_ids))
        ):
            raise ProvenanceError("cross-CVE pipeline artifact identities are invalid")
        selected_artifact_ids: set[str] | None = None
        if evidence_artifact_ids is not None:
            selected_values = list(evidence_artifact_ids)
            if (
                isinstance(evidence_artifact_ids, (str, bytes))
                or any(
                    not isinstance(value, str) or not value for value in selected_values
                )
                or len(selected_values) != len(set(selected_values))
                or not set(selected_values) <= set(available_artifact_ids)
            ):
                raise ProvenanceError(
                    "cross-CVE pipeline evidence selection is invalid"
                )
            selected_artifact_ids = set(selected_values)
        context_records: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for document, classification in zip(documents, classifications, strict=True):
            if not isinstance(classification, dict):
                raise ProvenanceError("cross-CVE pipeline classification is malformed")
            artifact_id = str(classification.get("artifact_id") or "")
            records = []
            for line in document.splitlines():
                if not line:
                    continue
                record = json.loads(line)
                if (
                    not isinstance(record, dict)
                    or _canonical_json(record) != line
                    or not _cross_cve_line_id(record)
                ):
                    raise ProvenanceError("cross-CVE pipeline document is invalid")
                records.append(record)
            context_records.extend(records)
            if selected_artifact_ids is None or artifact_id in selected_artifact_ids:
                evidence_ids.extend(_cross_cve_line_id(record) for record in records)
        task = {
            "context": "\n".join(_canonical_json(record) for record in context_records),
            "counterfactual_twin": deepcopy(candidate.get("counterfactual_twin")),
        }
        return replay_cross_cve_remediation_history(
            task,
            counterfactual=counterfactual,
            evidence_ids=evidence_ids if selected_artifact_ids is not None else None,
        )
    except (KeyError, TypeError, ValueError, ProvenanceError, json.JSONDecodeError):
        return _cross_cve_unknown_replay()


def replay_cross_cve_pipeline_raw_slice(
    candidate: dict[str, Any],
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay only complete, source-bound JSONL records visible in a raw slice."""
    try:
        if not isinstance(raw_document_context, str) or not raw_document_context:
            raise ProvenanceError("cross-CVE raw replay slice is empty")
        documents = str(candidate.get("document_context") or "").split(SEP)
        approved_records = {
            _canonical_json(json.loads(line))
            for document in documents
            for line in document.splitlines()
            if line
        }
        parts = raw_document_context.split("\n")
        records: list[dict[str, Any]] = []
        for index, line in enumerate(parts):
            if (index == 0 and not left_framed) or (
                index == len(parts) - 1 and not right_framed
            ):
                continue
            if not line or line == SEP.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ProvenanceError(
                    "cross-CVE raw slice has invalid framed JSON"
                ) from error
            if (
                not isinstance(record, dict)
                or _canonical_json(record) != line
                or line not in approved_records
            ):
                raise ProvenanceError("cross-CVE raw slice record is not source-bound")
            records.append(record)
        if not records:
            raise ProvenanceError("cross-CVE raw slice has no complete records")
        replay = replay_cross_cve_remediation_history(
            {
                "context": "\n".join(_canonical_json(record) for record in records),
                "counterfactual_twin": deepcopy(candidate.get("counterfactual_twin")),
            },
            counterfactual=counterfactual,
        )
        replay["raw_slice_record_count"] = len(records)
        return replay
    except (KeyError, TypeError, ValueError, ProvenanceError, json.JSONDecodeError):
        replay = _cross_cve_unknown_replay()
        replay["raw_slice_record_count"] = 0
        return replay


def build_cross_cve_pipeline_candidate(
    history: dict[str, Any],
    *,
    token_counter: Callable[[str], int],
    tokenizer_asset_manifest_sha256: str,
    candidate_attestation_key: bytes,
    task_replay_sidecar_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize an audited cross-CVE history for ranking and adapter replay."""
    history_audit = audit_cross_cve_remediation_history_candidate(
        history, token_counter=token_counter
    )
    if not history_audit or not all(history_audit.values()):
        raise ProvenanceError("cross-CVE pipeline input failed history audit")
    if _SHA256.fullmatch(tokenizer_asset_manifest_sha256) is None:
        raise ProvenanceError("cross-CVE pipeline tokenizer asset digest is invalid")
    if task_replay_sidecar_binding is not None and (
        set(task_replay_sidecar_binding)
        != {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        }
        or task_replay_sidecar_binding.get("adapter_id")
        != "cyber.cross_cve_remediation.v1"
        or task_replay_sidecar_binding.get("adapter_revision")
        != CROSS_CVE_HISTORY_REPLAY_REVISION
        or task_replay_sidecar_binding.get("sidecar_schema_version")
        != "longworld.task-replay-sidecar.v1"
        or _SHA256.fullmatch(str(task_replay_sidecar_binding.get("sha256") or ""))
        is None
    ):
        raise ProvenanceError("cross-CVE task replay sidecar binding is invalid")
    documents = _cross_cve_pipeline_documents(str(history.get("context") or ""))
    classifications: list[dict[str, Any]] = []
    source_ids_by_artifact: dict[str, list[str]] = {}
    for index, document in enumerate(documents):
        text_sha256 = _sha256_text(document)
        artifact_id = (
            f"{history['world_id']}.cross_cve_{history['length_bucket']}_"
            f"{index:02d}_{text_sha256[:12]}"
        )
        record = json.loads(document)
        record_id = _cross_cve_line_id(record)
        source_ids_by_artifact[artifact_id] = [record_id]
        classifications.append(
            {
                "artifact_id": artifact_id,
                "workflow_id": history["world_id"],
                "workflow_kind": "real_source_derived",
                "evidence_role": "causal_gold",
                "source_origin": "real_derived",
                "provenance_id": f"sha256:{text_sha256}",
            }
        )
    artifact_ids = [item["artifact_id"] for item in classifications]
    document_context = SEP.join(documents)
    question = str(history["question"])
    context = wrap_prompt(question, document_context, "first")
    context_tokens = token_counter(context)
    query_id = (
        f"{history['world_id']}.cross_cve_remediation_reconstruction:first:spread:"
        f"{history['length_bucket']}"
    )
    source = history.get("source_binding")
    if not isinstance(source, dict):
        raise ProvenanceError("cross-CVE pipeline source binding is missing")
    candidate: dict[str, Any] = {
        "schema_version": "p3.0",
        "data_product": "worldlong_cyber_cross_cve_candidate_v1",
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "ranker_ready_promotion_adapter_pending",
        "world_id": history["world_id"],
        "seed": 0,
        "domain": "cyber",
        "workflow_kind": history["workflow_kind"],
        "query_id": query_id,
        "query_type": history["query_type"],
        "query_timing": "first",
        "position_bucket": "spread",
        "length_bucket": history["length_bucket"],
        "question": question,
        "answer": history["answer"],
        "cf_answer": history["cf_answer"],
        "view": "ordered_artifact_view",
        "context": context,
        "document_context": document_context,
        "essential_artifact_ids": artifact_ids,
        "counterfactual_twin": deepcopy(history["counterfactual_twin"]),
        "pipeline_capabilities": {
            "dense_ranking": True,
            "cyber_strict_replay": True,
            "generic_strict_replay": False,
            "generic_promotion": False,
        },
        "artifact_classification": classifications,
        "source_record_ids_by_artifact": source_ids_by_artifact,
        "workflow_ids": [history["world_id"]],
        "training_objective": "sft",
        "composition_method": "causal_timeline",
        "motif": "nvd_kev_join+vendor_year_aggregation+remediation_window",
        "base_task_id": _sha256_text(
            f"{history['world_id']}|{history['answer_program_id']}"
        )[:20],
        "semantic_base_task_id": _sha256_text(f"cyber|{history['answer_program_id']}")[
            :20
        ],
        "dossier_id": _sha256_text(
            _canonical_json(
                {
                    "world_id": history["world_id"],
                    "query_type": history["query_type"],
                    "artifact_ids": artifact_ids,
                }
            )
        )[:20],
        "answer_program_id": history["answer_program_id"],
        "semantic_growth_group_id": history["semantic_growth_group_id"],
        "source_binding": deepcopy(source),
        "source_family_ids": [
            "nvd_cve_api",
            "cisa_known_exploited_vulnerabilities_catalog",
        ],
        "source_record_ids": list(history["source_record_ids"]),
        "source_relation_ids": list(history["source_relation_ids"]),
        "authentic_source_relation_edges": deepcopy(
            history["authentic_source_relation_edges"]
        ),
        "verified_derived_order_relation_edges": deepcopy(
            history["verified_derived_order_relation_edges"]
        ),
        "event_count": history["event_count"],
        "strict_support_event_count": history["strict_support_event_count"],
        "graph": deepcopy(history["graph"]),
        "tokenizer_model_id": history["tokenizer_model_id"],
        "tokenizer_revision": history["tokenizer_revision"],
        "tokenizer_asset_manifest_sha256": tokenizer_asset_manifest_sha256,
        "tokenizer_context_tokens": context_tokens,
        "actual_context_tokens": context_tokens,
        "band_lower_tokens": history["band_lower_tokens"],
        "band_upper_tokens": history["band_upper_tokens"],
        "semantic_tokens": {
            "internal": context_tokens,
            "event_bearing": context_tokens,
            "proof_bearing": context_tokens,
            "causal_supporting": 0,
            "generic_background": 0,
        },
        "real_source_verified": False,
        "source_verified_at_materialization": True,
        "real_source_token_ratio": 1.0,
        "strict_replay_revision": history["strict_replay_revision"],
        "cross_cve_replay_contract": {
            "adapter_id": "cyber.cross_cve_remediation.v1",
            "revision": CROSS_CVE_HISTORY_REPLAY_REVISION,
        },
        "promotion_blocker_code": "missing_domain_replay_adapter:cyber_cross_cve",
    }
    if task_replay_sidecar_binding is not None:
        candidate["task_replay_sidecar"] = deepcopy(task_replay_sidecar_binding)
        candidate["generation_integration"] = "task_replay_sidecar_bound"
        candidate["promotion_blocker_code"] = "signed_upstream_proof_gates_pending"
    audit = audit_cross_cve_pipeline_candidate(candidate, token_counter=token_counter)
    if not audit or not all(audit.values()):
        failed = sorted(name for name, passed in audit.items() if not passed)
        raise ProvenanceError(
            "cross-CVE pipeline candidate failed executable audit: " + ",".join(failed)
        )
    return attach_attestation(
        candidate, candidate_attestation_key, purpose="candidate_row"
    )


def audit_cross_cve_pipeline_candidate(
    candidate: dict[str, Any], *, token_counter: Callable[[str], int]
) -> dict[str, bool]:
    """Audit standard serialization without claiming shared promotion support."""
    replay = replay_cross_cve_pipeline_candidate(candidate)
    cf_replay = replay_cross_cve_pipeline_candidate(candidate, counterfactual=True)
    classifications = candidate.get("artifact_classification")
    classifications = classifications if isinstance(classifications, list) else []
    artifact_ids = [
        str(item.get("artifact_id") or "")
        for item in classifications
        if isinstance(item, dict)
    ]
    removals = [
        replay_cross_cve_pipeline_candidate(
            candidate,
            evidence_artifact_ids=[value for value in artifact_ids if value != removed],
        )["answer"]
        for removed in artifact_ids
    ]
    singles = [
        replay_cross_cve_pipeline_candidate(
            candidate, evidence_artifact_ids=[artifact_id]
        )["answer"]
        for artifact_id in artifact_ids
    ]
    top_three = [
        replay_cross_cve_pipeline_candidate(
            candidate, evidence_artifact_ids=list(selected)
        )["answer"]
        for selected in combinations(artifact_ids, 3)
    ]
    context = candidate.get("document_context")
    documents = context.split(SEP) if isinstance(context, str) else []
    source_ids_by_artifact = candidate.get("source_record_ids_by_artifact")
    source_ids_by_artifact = (
        source_ids_by_artifact if isinstance(source_ids_by_artifact, dict) else {}
    )
    artifact_bindings_valid = len(documents) == len(classifications) and bool(documents)
    reconstructed_source_ids: list[str] = []
    reconstructed_relation_ids: list[str] = []
    if artifact_bindings_valid:
        try:
            for document, classification in zip(
                documents, classifications, strict=True
            ):
                if not isinstance(classification, dict):
                    raise ProvenanceError("classification is malformed")
                artifact_id = str(classification.get("artifact_id") or "")
                text_sha256 = _sha256_text(document)
                record = json.loads(document)
                record_id = _cross_cve_line_id(record)
                if record.get("record_type") == "cyber_source_relation":
                    reconstructed_relation_ids.append(record_id)
                else:
                    reconstructed_source_ids.append(record_id)
                artifact_bindings_valid = artifact_bindings_valid and bool(
                    artifact_id
                    and classification.get("provenance_id") == f"sha256:{text_sha256}"
                    and classification.get("source_origin") == "real_derived"
                    and classification.get("workflow_kind") == "real_source_derived"
                    and source_ids_by_artifact.get(artifact_id) == [record_id]
                )
        except (TypeError, ValueError, ProvenanceError, json.JSONDecodeError):
            artifact_bindings_valid = False
    corrupted = deepcopy(candidate)
    corrupted_documents = list(documents)
    corruption_fails = False
    if corrupted_documents:
        try:
            record = json.loads(corrupted_documents[0])
            if record.get("record_type") == "cyber_source_record":
                record["text"] += " CORRUPTED"
                corrupted_documents[0] = _canonical_json(record)
                corrupted["document_context"] = SEP.join(corrupted_documents)
                corruption_fails = (
                    replay_cross_cve_pipeline_candidate(corrupted)["answer"]
                    == "unknown"
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            corruption_fails = False
    replay_contract = candidate.get("cross_cve_replay_contract")
    replay_contract_valid = (
        isinstance(replay_contract, dict)
        and replay_contract.get("adapter_id") == "cyber.cross_cve_remediation.v1"
        and replay_contract.get("revision") == CROSS_CVE_HISTORY_REPLAY_REVISION
    )
    declared_tokens = candidate.get("tokenizer_context_tokens")
    recomputed_tokens = token_counter(str(candidate.get("context") or ""))
    lower = candidate.get("band_lower_tokens")
    upper = candidate.get("band_upper_tokens")
    return {
        "strict_replay_sufficient": replay["answer"] == candidate.get("answer"),
        "counterfactual_replay_sufficient": cf_replay["answer"]
        == candidate.get("cf_answer"),
        "counterfactual_changes_answer": cf_replay["answer"] != replay["answer"],
        "remove_one_artifact_fails": bool(removals)
        and all(answer != candidate.get("answer") for answer in removals),
        "essential_single_artifact_insufficient": bool(singles)
        and all(answer != candidate.get("answer") for answer in singles),
        "every_top_three_insufficient": bool(top_three)
        and all(answer != candidate.get("answer") for answer in top_three),
        "semantic_corruption_fails": corruption_fails,
        "artifact_text_bindings_valid": artifact_bindings_valid,
        "source_records_reconstructed": sorted(reconstructed_source_ids)
        == candidate.get("source_record_ids")
        == replay["source_record_ids"],
        "source_relations_reconstructed": sorted(reconstructed_relation_ids)
        == candidate.get("source_relation_ids")
        == replay["source_relation_ids"],
        "replay_contract_binding_valid": replay_contract_valid,
        "exact_token_count_recomputed": isinstance(declared_tokens, int)
        and not isinstance(declared_tokens, bool)
        and declared_tokens == recomputed_tokens,
        "exact_token_band_recomputed": isinstance(lower, int)
        and isinstance(upper, int)
        and lower <= recomputed_tokens <= upper,
        "ranker_contract_shape_valid": bool(
            candidate.get("data_stage") == "candidate"
            and candidate.get("training_objective") == "sft"
            and candidate.get("view") == "ordered_artifact_view"
            and candidate.get("composition_method") == "causal_timeline"
            and candidate.get("essential_artifact_ids") == artifact_ids
            and len(artifact_ids) == len(set(artifact_ids)) >= 4
            and candidate.get("pipeline_capabilities")
            == {
                "dense_ranking": True,
                "cyber_strict_replay": True,
                "generic_strict_replay": False,
                "generic_promotion": False,
            }
        ),
        "non_promoted_boundary": all(
            candidate.get(field) is False
            for field in (
                "train_ready",
                "production_eligible",
                "promotion_eligible",
                "complete_world",
                "promoted",
            )
        ),
    }


def replay_kev_catalog_history(
    task: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Execute the catalog chronology program from serialized context records."""
    try:
        header, entries = _parse_context(task.get("context"))
        if evidence_ids is not None:
            selected = set(evidence_ids)
            header_id = str(header["source_record_id"])
            if header_id not in selected:
                raise ProvenanceError("KEV history catalog header was removed")
            entries = [
                entry for entry in entries if f"cisa-kev:{entry['cveID']}" in selected
            ]
            if not entries:
                raise ProvenanceError("KEV history has no selected entries")
        if counterfactual:
            twin = task.get("counterfactual_twin")
            if not isinstance(twin, dict):
                raise ProvenanceError("KEV history counterfactual is missing")
            target = next(
                (
                    entry
                    for entry in entries
                    if f"cisa-kev:{entry['cveID']}" == twin.get("record_id")
                ),
                None,
            )
            if (
                target is None
                or twin.get("source_origin") != "synthetic_counterfactual"
                or twin.get("provenance_operation") != "replace_due_date"
                or target["dueDate"] != twin.get("parent_value")
                or not isinstance(twin.get("value"), str)
                or twin["value"] == twin["parent_value"]
            ):
                raise ProvenanceError(
                    "KEV history counterfactual replacement is invalid"
                )
            date.fromisoformat(twin["value"])
            target["dueDate"] = twin["value"]
        record_ids = _record_ids(header, entries)
        relation_ids = _relation_ids(header, entries)
        return {
            "answer": _answer(header, entries),
            "source_record_ids": record_ids,
            "source_relation_ids": relation_ids,
            "authentic_source_relation_edges": _authentic_relation_edges(
                header, entries
            ),
            "verified_derived_order_relation_edges": _derived_order_relation_edges(
                entries
            ),
            "event_count": len(record_ids),
            "strict_support_event_count": len(record_ids),
            "proof_depth": len(entries),
            "hop_count": len(entries),
        }
    except (KeyError, TypeError, ValueError, ProvenanceError):
        return {
            "answer": "unknown",
            "source_record_ids": [],
            "source_relation_ids": [],
            "authentic_source_relation_edges": [],
            "verified_derived_order_relation_edges": [],
            "event_count": 0,
            "strict_support_event_count": 0,
            "proof_depth": 0,
            "hop_count": 0,
        }


def _find_prefix_count(
    records: Sequence[dict[str, Any]],
    *,
    minimum_count: int,
    band: HistoryBand,
    token_counter: Callable[[str], int],
) -> tuple[int, str, int]:
    low = minimum_count
    high = len(records) - 1
    if low > high:
        raise ProvenanceError(f"insufficient source records for {band.name}")
    while low < high:
        middle = (low + high) // 2
        context = _context(records[: middle + 1])
        if token_counter(context) >= band.lower_tokens:
            high = middle
        else:
            low = middle + 1
    selected = low
    context = _context(records[: selected + 1])
    tokens = token_counter(context)
    if not band.lower_tokens <= tokens <= band.upper_tokens:
        raise ProvenanceError(
            f"verified source cannot fill exact {band.name} band: {tokens} tokens"
        )
    return selected, context, tokens


def build_kev_catalog_history_candidates(
    catalog: dict[str, Any],
    *,
    world_id: str,
    source_binding: dict[str, Any],
    bands: Sequence[HistoryBand],
    token_counter: Callable[[str], int],
    tokenizer_model_id: str,
    tokenizer_revision: str,
) -> list[dict[str, Any]]:
    """Build exact nested KEV histories from whole verified source records."""
    if (
        not world_id
        or not tokenizer_model_id
        or _COMMIT_SHA.fullmatch(tokenizer_revision) is None
    ):
        raise ProvenanceError("KEV history materialization identity is invalid")
    required_binding = {
        "source_url",
        "observed_at",
        "retrieval_sha256",
        "signed_manifest_sha256",
    }
    if set(source_binding) != required_binding or (
        _SHA256.fullmatch(str(source_binding.get("retrieval_sha256") or "")) is None
        or _SHA256.fullmatch(str(source_binding.get("signed_manifest_sha256") or ""))
        is None
    ):
        raise ProvenanceError("KEV history source binding is invalid")
    parsed_url = urlparse(str(source_binding["source_url"]))
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ProvenanceError("KEV history source URL is invalid")
    _parse_timestamp(str(source_binding["observed_at"]), "observed_at")
    if [band.name for band in bands] != list(_EXPECTED_BANDS[: len(bands)]):
        raise ProvenanceError("KEV history bands must follow the registered order")

    entries = _validate_catalog(catalog)
    header = _catalog_header(catalog, str(source_binding["retrieval_sha256"]))
    all_records = [header, *(_entry_record(entry) for entry in entries)]
    rows: list[dict[str, Any]] = []
    previous_entry_count = 0
    for band in bands:
        entry_count, context, context_tokens = _find_prefix_count(
            all_records,
            minimum_count=previous_entry_count + 1,
            band=band,
            token_counter=token_counter,
        )
        selected_records = all_records[: entry_count + 1]
        selected_entries = entries[:entry_count]
        replay_header = selected_records[0]
        source_record_ids = _record_ids(replay_header, selected_entries)
        source_relation_ids = _relation_ids(replay_header, selected_entries)
        last = selected_entries[-1]
        replacement = last["dateAdded"]
        if replacement == last["dueDate"]:
            replacement = date.fromordinal(
                date.fromisoformat(replacement).toordinal() + 1
            ).isoformat()
        row: dict[str, Any] = {
            "schema_version": DOMAIN_CUMULATIVE_HISTORY_SCHEMA,
            "data_stage": "candidate_history",
            "train_ready": False,
            "production_eligible": False,
            "promotion_eligible": False,
            "complete_world": False,
            "promoted": False,
            "generation_integration": "disabled",
            "world_id": world_id,
            "domain": "cyber",
            "workflow_kind": "real_source_derived",
            "query_type": "kev_catalog_chronology_audit",
            "answer_program_id": "cyber.kev_catalog_chronology_audit.v1",
            "semantic_growth_group_id": f"{world_id}|kev-catalog-history",
            "length_bucket": band.name,
            "question": (
                "Audit the chronologically ordered CISA KEV catalog slice. Return "
                "the selected entry count, first and last additions, maximum "
                "remediation window, deterministic date-then-CVE order validity, "
                "and each year's entry count, known-ransomware count, and final "
                "record under that explicit lexical tie-break."
            ),
            "context": context,
            "context_sha256": _sha256_text(context),
            "answer": "",
            "cf_answer": "",
            "counterfactual_twin": {
                "record_id": f"cisa-kev:{last['cveID']}",
                "source_origin": "synthetic_counterfactual",
                "provenance_operation": "replace_due_date",
                "parent_value": last["dueDate"],
                "value": replacement,
            },
            "source_binding": deepcopy(source_binding),
            "source_record_ids": source_record_ids,
            "source_relation_ids": source_relation_ids,
            "essential_evidence_ids": source_record_ids,
            "event_count": len(source_record_ids),
            "strict_support_event_count": len(source_record_ids),
            "graph": {
                "proof_depth": len(selected_entries),
                "hop_count": len(selected_entries),
            },
            "authentic_source_relation_edges": _authentic_relation_edges(
                replay_header, selected_entries
            ),
            "verified_derived_order_relation_edges": _derived_order_relation_edges(
                selected_entries
            ),
            "tokenizer_model_id": tokenizer_model_id,
            "tokenizer_revision": tokenizer_revision,
            "tokenizer_context_tokens": context_tokens,
            "actual_context_tokens": context_tokens,
            "band_lower_tokens": band.lower_tokens,
            "band_upper_tokens": band.upper_tokens,
            "semantic_tokens": {
                "internal": context_tokens,
                "event_bearing": context_tokens,
                "proof_bearing": context_tokens,
                "causal_supporting": 0,
                "generic_background": 0,
            },
            "real_source_verified": False,
            "source_verified_at_materialization": True,
            "real_source_token_ratio": 1.0,
            "strict_replay_revision": KEV_HISTORY_REPLAY_REVISION,
        }
        replay = replay_kev_catalog_history(row)
        row["answer"] = replay["answer"]
        row["cf_answer"] = replay_kev_catalog_history(row, counterfactual=True)[
            "answer"
        ]
        audit = audit_kev_catalog_history_candidate(row)
        if not audit or not all(audit.values()):
            failed = sorted(name for name, passed in audit.items() if not passed)
            raise ProvenanceError(
                f"KEV history candidate failed executable audit: {','.join(failed)}"
            )
        rows.append(row)
        previous_entry_count = entry_count
    cumulative_errors = audit_cumulative_history(rows)
    if cumulative_errors:
        raise ProvenanceError(
            "KEV history failed cumulative growth: " + ",".join(cumulative_errors)
        )
    return rows


def audit_kev_catalog_history_candidate(task: dict[str, Any]) -> dict[str, bool]:
    """Audit replay, CF, remove-one, corruption, lineage and exact-band gates."""
    replay = replay_kev_catalog_history(task)
    cf_replay = replay_kev_catalog_history(task, counterfactual=True)
    essentials = task.get("essential_evidence_ids")
    essentials = essentials if isinstance(essentials, list) else []
    removals = [
        replay_kev_catalog_history(
            task,
            evidence_ids=[value for value in essentials if value != removed],
        )["answer"]
        for removed in essentials
    ]
    singles = [
        replay_kev_catalog_history(task, evidence_ids=[evidence_id])["answer"]
        for evidence_id in essentials
    ]
    corrupted = deepcopy(task)
    context = str(corrupted.get("context") or "")
    lines = context.splitlines()
    corruption_fails = False
    if len(lines) > 1:
        last = json.loads(lines[-1])
        payload = last["source_payload"]
        payload["requiredAction"] = f"CORRUPTED {payload['requiredAction']}"
        lines[-1] = _canonical_json(last)
        corrupted["context"] = "\n".join(lines)
        corruption_fails = replay_kev_catalog_history(corrupted)["answer"] != task.get(
            "answer"
        )
    binding = task.get("source_binding")
    binding_valid = isinstance(binding, dict) and set(binding) == {
        "source_url",
        "observed_at",
        "retrieval_sha256",
        "signed_manifest_sha256",
    }
    if binding_valid and isinstance(binding, dict):
        try:
            _parse_timestamp(str(binding["observed_at"]), "observed_at")
            parsed = urlparse(str(binding["source_url"]))
            binding_valid = bool(
                parsed.scheme in {"http", "https"}
                and parsed.netloc
                and _SHA256.fullmatch(str(binding["retrieval_sha256"]))
                and _SHA256.fullmatch(str(binding["signed_manifest_sha256"]))
            )
        except ProvenanceError:
            binding_valid = False
    declared_tokens = task.get("tokenizer_context_tokens")
    lower = task.get("band_lower_tokens")
    upper = task.get("band_upper_tokens")
    exact_bound = bool(
        isinstance(declared_tokens, int)
        and not isinstance(declared_tokens, bool)
        and isinstance(lower, int)
        and isinstance(upper, int)
        and lower <= declared_tokens <= upper
    )
    source_ids = task.get("source_record_ids")
    source_ids = source_ids if isinstance(source_ids, list) else []
    return {
        "strict_replay_sufficient": replay["answer"] == task.get("answer"),
        "counterfactual_replay_sufficient": cf_replay["answer"]
        == task.get("cf_answer"),
        "counterfactual_changes_answer": cf_replay["answer"] != replay["answer"],
        "remove_one_fails": bool(removals)
        and all(answer != task.get("answer") for answer in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(answer != task.get("answer") for answer in singles),
        "essential_surface_gold_free": bool(lines)
        and all(str(task.get("answer") or "") not in line for line in lines),
        "semantic_corruption_fails": corruption_fails,
        "source_binding_shape_valid": binding_valid,
        "context_digest_valid": task.get("context_sha256") == _sha256_text(context),
        "no_duplicate_source_records": len(source_ids) == len(set(source_ids)),
        "exact_token_band_declared": exact_bound,
        "replayed_source_records_match": source_ids == replay["source_record_ids"],
        "replayed_source_relations_match": task.get("source_relation_ids")
        == replay["source_relation_ids"],
        "replayed_graph_matches": task.get("event_count") == replay["event_count"]
        and task.get("strict_support_event_count")
        == replay["strict_support_event_count"]
        and (task.get("graph") or {}).get("proof_depth") == replay["proof_depth"]
        and (task.get("graph") or {}).get("hop_count") == replay["hop_count"]
        and task.get("authentic_source_relation_edges")
        == replay["authentic_source_relation_edges"]
        and task.get("verified_derived_order_relation_edges")
        == replay["verified_derived_order_relation_edges"],
        "non_promoted_boundary": all(
            task.get(field) is False
            for field in (
                "train_ready",
                "production_eligible",
                "promotion_eligible",
                "complete_world",
                "promoted",
            )
        ),
    }


def audit_cumulative_history(rows: Sequence[dict[str, Any]]) -> list[str]:
    """Return stable violations of strict registered-band source-history growth."""
    errors: list[str] = []
    if [row.get("length_bucket") for row in rows] != list(_EXPECTED_BANDS[: len(rows)]):
        errors.append("bands_not_in_registered_order")
        return errors
    for before, after in pairwise(rows):
        label = f"{before['length_bucket']}->{after['length_bucket']}"
        if not str(after.get("context") or "").startswith(
            str(before.get("context") or "") + "\n"
        ):
            errors.append(f"context_not_strict_prefix:{label}")
        for field, reason in (
            ("source_record_ids", "source_records_not_strictly_nested"),
            ("source_relation_ids", "source_relations_not_strictly_nested"),
            ("essential_evidence_ids", "essentials_not_strictly_nested"),
        ):
            before_values = before.get(field)
            after_values = after.get(field)
            if not (
                isinstance(before_values, list)
                and isinstance(after_values, list)
                and set(before_values) < set(after_values)
            ):
                errors.append(f"{reason}:{label}")
        for field, reason in (
            ("event_count", "event_count_not_growing"),
            ("strict_support_event_count", "strict_support_not_growing"),
        ):
            if int(after.get(field) or 0) <= int(before.get(field) or 0):
                errors.append(f"{reason}:{label}")
        if int((after.get("graph") or {}).get("proof_depth") or 0) <= int(
            (before.get("graph") or {}).get("proof_depth") or 0
        ):
            errors.append(f"proof_depth_not_growing:{label}")
        if int((after.get("semantic_tokens") or {}).get("internal") or 0) <= int(
            (before.get("semantic_tokens") or {}).get("internal") or 0
        ):
            errors.append(f"semantic_tokens_not_growing:{label}")
    return errors


def _pipeline_document_records(document: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in document.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProvenanceError("KEV pipeline document has invalid JSON") from error
        if not isinstance(record, dict) or _canonical_json(record) != line:
            raise ProvenanceError("KEV pipeline document record is not canonical")
        records.append(record)
    if not records:
        raise ProvenanceError("KEV pipeline document is empty")
    return records


def _source_file_binding(name: str, sha256: str) -> dict[str, str]:
    if Path(name).name != name or not name or _SHA256.fullmatch(sha256) is None:
        raise ProvenanceError("KEV replay source file binding is invalid")
    return {"name": name, "sha256": sha256}


def build_kev_pipeline_replay_manifest(
    *,
    source_binding: dict[str, Any],
    source_manifest_name: str,
    source_response_name: str,
    tokenizer_model_id: str,
    tokenizer_revision: str,
    tokenizer_asset_manifest_sha256: str,
    source_attestation_key: bytes,
) -> dict[str, Any]:
    """Build a source-role sidecar for future registered Cyber replay."""
    required_binding = {
        "source_url",
        "observed_at",
        "retrieval_sha256",
        "signed_manifest_sha256",
    }
    if set(source_binding) != required_binding:
        raise ProvenanceError("KEV replay source binding is invalid")
    source_url = str(source_binding.get("source_url") or "")
    parsed_url = urlparse(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ProvenanceError("KEV replay source URL is invalid")
    _parse_timestamp(str(source_binding.get("observed_at") or ""), "observed_at")
    if (
        not tokenizer_model_id
        or _COMMIT_SHA.fullmatch(tokenizer_revision) is None
        or _SHA256.fullmatch(tokenizer_asset_manifest_sha256) is None
    ):
        raise ProvenanceError("KEV replay tokenizer identity is invalid")
    source_manifest = _source_file_binding(
        source_manifest_name, str(source_binding["signed_manifest_sha256"])
    )
    source_response = _source_file_binding(
        source_response_name, str(source_binding["retrieval_sha256"])
    )
    payload: dict[str, Any] = {
        "schema_version": KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
        "data_stage": "source_replay_manifest",
        "source_kind": "cisa_known_exploited_vulnerabilities_catalog",
        "workflow_kind": "real_source_derived",
        "strict_replay_revision": KEV_HISTORY_REPLAY_REVISION,
        "source_binding": deepcopy(source_binding),
        "source_manifest": source_manifest,
        "source_response": source_response,
        "tokenizer": {
            "model_id": tokenizer_model_id,
            "revision": tokenizer_revision,
            "asset_manifest_sha256": tokenizer_asset_manifest_sha256,
        },
        "train_ready": False,
        "production_eligible": False,
    }
    return attach_attestation(
        payload, source_attestation_key, purpose="source_manifest"
    )


def verify_kev_pipeline_replay_manifest_bytes(
    raw: bytes, source_attestation_key: bytes
) -> dict[str, Any]:
    """Verify the exact serialized source sidecar and its closed schema."""
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("KEV replay manifest is not valid UTF-8 JSON") from error
    if not isinstance(manifest, dict):
        raise ProvenanceError("KEV replay manifest must be an object")
    if not verify_attestation(
        manifest, source_attestation_key, purpose="source_manifest"
    ):
        raise ProvenanceError("KEV replay manifest attestation is invalid")
    expected_fields = {
        "schema_version",
        "data_stage",
        "source_kind",
        "workflow_kind",
        "strict_replay_revision",
        "source_binding",
        "source_manifest",
        "source_response",
        "tokenizer",
        "train_ready",
        "production_eligible",
        "attestation",
    }
    if LOCAL_PROBE_TRUST_ISOLATION_FIELD in manifest:
        expected_fields.add(LOCAL_PROBE_TRUST_ISOLATION_FIELD)
    source_binding = manifest.get("source_binding")
    source_manifest = manifest.get("source_manifest")
    source_response = manifest.get("source_response")
    tokenizer = manifest.get("tokenizer")
    if (
        set(manifest) != expected_fields
        or manifest.get("schema_version") != KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA
        or manifest.get("data_stage") != "source_replay_manifest"
        or manifest.get("source_kind") != "cisa_known_exploited_vulnerabilities_catalog"
        or manifest.get("workflow_kind") != "real_source_derived"
        or manifest.get("strict_replay_revision") != KEV_HISTORY_REPLAY_REVISION
        or manifest.get("train_ready") is not False
        or manifest.get("production_eligible") is not False
        or not isinstance(source_binding, dict)
        or set(source_binding)
        != {
            "source_url",
            "observed_at",
            "retrieval_sha256",
            "signed_manifest_sha256",
        }
        or not isinstance(source_manifest, dict)
        or set(source_manifest) != {"name", "sha256"}
        or not isinstance(source_response, dict)
        or set(source_response) != {"name", "sha256"}
        or not isinstance(tokenizer, dict)
        or set(tokenizer) != {"model_id", "revision", "asset_manifest_sha256"}
        or not str(tokenizer.get("model_id") or "")
        or _COMMIT_SHA.fullmatch(str(tokenizer.get("revision") or "")) is None
        or _SHA256.fullmatch(str(tokenizer.get("asset_manifest_sha256") or "")) is None
    ):
        raise ProvenanceError("KEV replay manifest contract is invalid")
    source_url = urlparse(str(source_binding.get("source_url") or ""))
    if source_url.scheme not in {"http", "https"} or not source_url.netloc:
        raise ProvenanceError("KEV replay manifest source URL is invalid")
    _parse_timestamp(str(source_binding.get("observed_at") or ""), "observed_at")
    _source_file_binding(
        str(source_manifest.get("name") or ""),
        str(source_manifest.get("sha256") or ""),
    )
    _source_file_binding(
        str(source_response.get("name") or ""),
        str(source_response.get("sha256") or ""),
    )
    if source_manifest.get("sha256") != source_binding.get(
        "signed_manifest_sha256"
    ) or source_response.get("sha256") != source_binding.get("retrieval_sha256"):
        raise ProvenanceError("KEV replay manifest source digests disagree")
    return manifest


def kev_pipeline_replay_manifest_binding(
    raw: bytes, source_attestation_key: bytes
) -> dict[str, str]:
    """Return the exact binding serialized into each candidate row."""
    manifest = verify_kev_pipeline_replay_manifest_bytes(raw, source_attestation_key)
    source_binding = manifest["source_binding"]
    assert isinstance(source_binding, dict)
    return {
        "schema_version": KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest_sha256": str(source_binding["signed_manifest_sha256"]),
        "source_response_sha256": str(source_binding["retrieval_sha256"]),
        "replay_revision": KEV_HISTORY_REPLAY_REVISION,
    }


def _pipeline_record_id(record: dict[str, Any]) -> str:
    if record.get("record_type") == "catalog_snapshot":
        record_id = str(record.get("source_record_id") or "")
    else:
        payload = record.get("source_payload")
        record_id = (
            f"cisa-kev:{payload.get('cveID')}" if isinstance(payload, dict) else ""
        )
    if not record_id:
        raise ProvenanceError("KEV pipeline source record identity is invalid")
    return record_id


def _pipeline_documents(context: str, count: int) -> list[str]:
    lines = context.splitlines()
    if count < 4 or count > len(lines):
        raise ProvenanceError("KEV pipeline document shard count is invalid")
    base, remainder = divmod(len(lines), count)
    documents: list[str] = []
    cursor = 0
    for index in range(count):
        width = base + (index < remainder)
        documents.append("\n".join(lines[cursor : cursor + width]))
        cursor += width
    return documents


def _pipeline_documents_for_windows(
    history: Mapping[str, Any],
    *,
    minimum_count: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    """Use the fewest exact-band shards that expose a real 4K subwindow."""
    context = str(history.get("context") or "")
    question = str(history.get("question") or "")
    lower = history.get("band_lower_tokens")
    upper = history.get("band_upper_tokens")
    if (
        not context
        or not question
        or isinstance(lower, bool)
        or not isinstance(lower, int)
        or isinstance(upper, bool)
        or not isinstance(upper, int)
    ):
        raise ProvenanceError("KEV pipeline exact-band inputs are invalid")
    lines = context.splitlines()

    def admissible(documents: list[str]) -> bool:
        prompt_tokens = token_counter(
            wrap_prompt(question, SEP.join(documents), "first")
        )
        return lower <= prompt_tokens <= upper and any(
            token_counter(document) <= _RAW_TOKEN_WINDOW for document in documents
        )

    documents = _pipeline_documents(context, minimum_count)
    if admissible(documents):
        return documents
    for tail_width in range(1, len(lines) - minimum_count + 1):
        tail = "\n".join(lines[-tail_width:])
        if token_counter(tail) > _RAW_TOKEN_WINDOW:
            break
        documents = [
            *_pipeline_documents("\n".join(lines[:-tail_width]), minimum_count),
            tail,
        ]
        if admissible(documents):
            return documents
    raise ProvenanceError(
        "KEV pipeline cannot expose a strict 4K window inside the exact band"
    )


def replay_kev_pipeline_candidate(
    candidate: dict[str, Any],
    *,
    counterfactual: bool = False,
    evidence_artifact_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Replay one standard serialized KEV candidate from its document bodies."""
    try:
        document_context = candidate.get("document_context")
        classifications = candidate.get("artifact_classification")
        if (
            not isinstance(document_context, str)
            or not isinstance(classifications, list)
            or not classifications
        ):
            raise ProvenanceError("KEV pipeline artifact pool is missing")
        documents = document_context.split(SEP)
        if len(documents) != len(classifications):
            raise ProvenanceError("KEV pipeline artifact pool is unbound")
        available_artifact_ids = [
            str(item.get("artifact_id") or "")
            for item in classifications
            if isinstance(item, dict)
        ]
        if (
            len(available_artifact_ids) != len(classifications)
            or "" in available_artifact_ids
            or len(available_artifact_ids) != len(set(available_artifact_ids))
        ):
            raise ProvenanceError("KEV pipeline artifact identities are invalid")
        selected_artifact_ids: set[str] | None = None
        if evidence_artifact_ids is not None:
            selected_values = list(evidence_artifact_ids)
            if (
                isinstance(evidence_artifact_ids, (str, bytes))
                or any(
                    not isinstance(value, str) or not value for value in selected_values
                )
                or len(selected_values) != len(set(selected_values))
                or not set(selected_values) <= set(available_artifact_ids)
            ):
                raise ProvenanceError("KEV pipeline evidence selection is invalid")
            selected_artifact_ids = set(selected_values)
        context_records: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for document, classification in zip(documents, classifications, strict=True):
            if not isinstance(classification, dict):
                raise ProvenanceError("KEV pipeline classification is malformed")
            artifact_id = str(classification.get("artifact_id") or "")
            records = _pipeline_document_records(document)
            context_records.extend(records)
            if selected_artifact_ids is None or artifact_id in selected_artifact_ids:
                evidence_ids.extend(_pipeline_record_id(record) for record in records)
        task = {
            "context": "\n".join(_canonical_json(record) for record in context_records),
            "counterfactual_twin": deepcopy(candidate.get("counterfactual_twin")),
        }
        return replay_kev_catalog_history(
            task,
            counterfactual=counterfactual,
            evidence_ids=evidence_ids if selected_artifact_ids is not None else None,
        )
    except (KeyError, TypeError, ValueError, ProvenanceError):
        return {
            "answer": "unknown",
            "source_record_ids": [],
            "source_relation_ids": [],
            "authentic_source_relation_edges": [],
            "verified_derived_order_relation_edges": [],
            "event_count": 0,
            "strict_support_event_count": 0,
            "proof_depth": 0,
            "hop_count": 0,
        }


def replay_kev_pipeline_raw_slice(
    candidate: dict[str, Any],
    raw_document_context: str,
    *,
    left_framed: bool,
    right_framed: bool,
    counterfactual: bool = False,
) -> dict[str, Any]:
    """Replay only complete, source-bound JSONL records visible in a raw slice."""
    try:
        if not isinstance(raw_document_context, str) or not raw_document_context:
            raise ProvenanceError("KEV raw replay slice is empty")
        documents = str(candidate.get("document_context") or "").split(SEP)
        approved_records = {
            _canonical_json(record)
            for document in documents
            for record in _pipeline_document_records(document)
        }
        parts = raw_document_context.split("\n")
        records: list[dict[str, Any]] = []
        for index, line in enumerate(parts):
            if (index == 0 and not left_framed) or (
                index == len(parts) - 1 and not right_framed
            ):
                continue
            if not line or line == SEP.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ProvenanceError(
                    "KEV raw slice has invalid framed JSON"
                ) from error
            if (
                not isinstance(record, dict)
                or _canonical_json(record) != line
                or line not in approved_records
            ):
                raise ProvenanceError("KEV raw slice record is not source-bound")
            records.append(record)
        if not records:
            raise ProvenanceError("KEV raw slice has no complete records")
        replay = replay_kev_catalog_history(
            {
                "context": _context(records),
                "counterfactual_twin": deepcopy(candidate.get("counterfactual_twin")),
            },
            counterfactual=counterfactual,
        )
        replay["raw_slice_record_count"] = len(records)
        return replay
    except (KeyError, TypeError, ValueError, ProvenanceError):
        replay = replay_kev_catalog_history({"context": ""})
        replay["raw_slice_record_count"] = 0
        return replay


def build_kev_pipeline_candidate(
    history: dict[str, Any],
    *,
    token_counter: Callable[[str], int],
    tokenizer_asset_manifest_sha256: str,
    replay_manifest_binding: dict[str, Any],
    candidate_attestation_key: bytes,
    task_replay_sidecar_binding: dict[str, Any] | None = None,
    document_shards: int = 4,
) -> dict[str, Any]:
    """Serialize an audited KEV history for ranking and adapter-based replay."""
    history_audit = audit_kev_catalog_history_candidate(history)
    if not history_audit or not all(history_audit.values()):
        raise ProvenanceError("KEV pipeline input failed history audit")
    required_replay_binding = {
        "schema_version",
        "sha256",
        "source_manifest_sha256",
        "source_response_sha256",
        "replay_revision",
    }
    if (
        set(replay_manifest_binding) != required_replay_binding
        or replay_manifest_binding.get("schema_version")
        != KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA
        or replay_manifest_binding.get("replay_revision") != KEV_HISTORY_REPLAY_REVISION
        or any(
            _SHA256.fullmatch(str(replay_manifest_binding.get(field) or "")) is None
            for field in (
                "sha256",
                "source_manifest_sha256",
                "source_response_sha256",
            )
        )
        or replay_manifest_binding.get("source_manifest_sha256")
        != (history.get("source_binding") or {}).get("signed_manifest_sha256")
        or replay_manifest_binding.get("source_response_sha256")
        != (history.get("source_binding") or {}).get("retrieval_sha256")
    ):
        raise ProvenanceError("KEV pipeline replay manifest binding is invalid")
    if _SHA256.fullmatch(tokenizer_asset_manifest_sha256) is None:
        raise ProvenanceError("KEV pipeline tokenizer asset digest is invalid")
    if task_replay_sidecar_binding is not None and (
        set(task_replay_sidecar_binding)
        != {
            "adapter_id",
            "adapter_revision",
            "sidecar_schema_version",
            "sha256",
        }
        or task_replay_sidecar_binding.get("adapter_id") != "cyber.kev_history.v1"
        or task_replay_sidecar_binding.get("adapter_revision")
        != KEV_HISTORY_REPLAY_REVISION
        or task_replay_sidecar_binding.get("sidecar_schema_version")
        != "longworld.task-replay-sidecar.v1"
        or _SHA256.fullmatch(str(task_replay_sidecar_binding.get("sha256") or ""))
        is None
    ):
        raise ProvenanceError("KEV task replay sidecar binding is invalid")

    documents = _pipeline_documents_for_windows(
        history,
        minimum_count=document_shards,
        token_counter=token_counter,
    )
    classifications: list[dict[str, Any]] = []
    source_ids_by_artifact: dict[str, list[str]] = {}
    for index, document in enumerate(documents):
        text_sha256 = _sha256_text(document)
        artifact_id = (
            f"{history['world_id']}.kev_history_{history['length_bucket']}_"
            f"{index:02d}_{text_sha256[:12]}"
        )
        record_ids = [
            _pipeline_record_id(record)
            for record in _pipeline_document_records(document)
        ]
        source_ids_by_artifact[artifact_id] = record_ids
        classifications.append(
            {
                "artifact_id": artifact_id,
                "workflow_id": history["world_id"],
                "workflow_kind": "real_source_derived",
                "evidence_role": "causal_gold",
                "source_origin": "real_derived",
                "provenance_id": f"sha256:{text_sha256}",
            }
        )
    artifact_ids = [item["artifact_id"] for item in classifications]
    document_context = SEP.join(documents)
    question = str(history["question"])
    context = wrap_prompt(question, document_context, "first")
    context_tokens = token_counter(context)
    query_id = (
        f"{history['world_id']}.kev_catalog_chronology_audit:first:spread:"
        f"{history['length_bucket']}"
    )
    candidate: dict[str, Any] = {
        "schema_version": "p3.0",
        "data_product": "worldlong_cyber_kev_candidate_v1",
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "promotion_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "ranker_ready_promotion_adapter_pending",
        "world_id": history["world_id"],
        "seed": 0,
        "domain": "cyber",
        "workflow_kind": history["workflow_kind"],
        "query_id": query_id,
        "query_type": history["query_type"],
        "query_timing": "first",
        "position_bucket": "spread",
        "length_bucket": history["length_bucket"],
        "question": question,
        "answer": history["answer"],
        "cf_answer": history["cf_answer"],
        "view": "ordered_artifact_view",
        "context": context,
        "document_context": document_context,
        "essential_artifact_ids": artifact_ids,
        "counterfactual_twin": deepcopy(history["counterfactual_twin"]),
        "pipeline_capabilities": {
            "dense_ranking": True,
            "cyber_strict_replay": True,
            "generic_strict_replay": False,
            "generic_promotion": False,
        },
        "artifact_classification": classifications,
        "source_record_ids_by_artifact": source_ids_by_artifact,
        "workflow_ids": [history["world_id"]],
        "training_objective": "sft",
        "composition_method": "causal_timeline",
        "motif": "chronology+annual_aggregation+remediation_window",
        "base_task_id": _sha256_text(
            f"{history['world_id']}|{history['answer_program_id']}"
        )[:20],
        "semantic_base_task_id": _sha256_text(f"cyber|{history['answer_program_id']}")[
            :20
        ],
        "dossier_id": _sha256_text(
            _canonical_json(
                {
                    "world_id": history["world_id"],
                    "query_type": history["query_type"],
                    "artifact_ids": artifact_ids,
                }
            )
        )[:20],
        "answer_program_id": history["answer_program_id"],
        "semantic_growth_group_id": history["semantic_growth_group_id"],
        "source_binding": deepcopy(history["source_binding"]),
        "source_family_ids": ["cisa_known_exploited_vulnerabilities_catalog"],
        "source_record_ids": list(history["source_record_ids"]),
        "source_relation_ids": list(history["source_relation_ids"]),
        "authentic_source_relation_edges": deepcopy(
            history["authentic_source_relation_edges"]
        ),
        "verified_derived_order_relation_edges": deepcopy(
            history["verified_derived_order_relation_edges"]
        ),
        "event_count": history["event_count"],
        "strict_support_event_count": history["strict_support_event_count"],
        "graph": deepcopy(history["graph"]),
        "tokenizer_model_id": history["tokenizer_model_id"],
        "tokenizer_revision": history["tokenizer_revision"],
        "tokenizer_asset_manifest_sha256": tokenizer_asset_manifest_sha256,
        "tokenizer_context_tokens": context_tokens,
        "actual_context_tokens": context_tokens,
        "band_lower_tokens": history["band_lower_tokens"],
        "band_upper_tokens": history["band_upper_tokens"],
        "semantic_tokens": {
            "internal": context_tokens,
            "event_bearing": context_tokens,
            "proof_bearing": context_tokens,
            "causal_supporting": 0,
            "generic_background": 0,
        },
        "real_source_verified": False,
        "source_verified_at_materialization": True,
        "real_source_token_ratio": 1.0,
        "strict_replay_revision": history["strict_replay_revision"],
        "domain_history_replay_manifest": deepcopy(replay_manifest_binding),
        "promotion_blocker_code": "missing_domain_replay_adapter:cyber",
    }
    if task_replay_sidecar_binding is not None:
        candidate["task_replay_sidecar"] = deepcopy(task_replay_sidecar_binding)
        candidate["generation_integration"] = "task_replay_sidecar_bound"
        candidate["promotion_blocker_code"] = "signed_upstream_proof_gates_pending"
    audit = audit_kev_pipeline_candidate(candidate, token_counter=token_counter)
    if not audit or not all(audit.values()):
        failed = sorted(name for name, passed in audit.items() if not passed)
        raise ProvenanceError(
            f"KEV pipeline candidate failed executable audit: {','.join(failed)}"
        )
    return attach_attestation(
        candidate, candidate_attestation_key, purpose="candidate_row"
    )


def audit_kev_pipeline_candidate(
    candidate: dict[str, Any], *, token_counter: Callable[[str], int]
) -> dict[str, bool]:
    """Audit standard serialization without claiming shared promotion support."""
    replay = replay_kev_pipeline_candidate(candidate)
    cf_replay = replay_kev_pipeline_candidate(candidate, counterfactual=True)
    classifications = candidate.get("artifact_classification")
    classifications = classifications if isinstance(classifications, list) else []
    artifact_ids = [
        str(item.get("artifact_id") or "")
        for item in classifications
        if isinstance(item, dict)
    ]
    removals = [
        replay_kev_pipeline_candidate(
            candidate,
            evidence_artifact_ids=[value for value in artifact_ids if value != removed],
        )["answer"]
        for removed in artifact_ids
    ]
    singles = [
        replay_kev_pipeline_candidate(candidate, evidence_artifact_ids=[artifact_id])[
            "answer"
        ]
        for artifact_id in artifact_ids
    ]
    top_three = [
        replay_kev_pipeline_candidate(candidate, evidence_artifact_ids=list(selected))[
            "answer"
        ]
        for selected in combinations(artifact_ids, 3)
    ]
    context = candidate.get("document_context")
    documents = context.split(SEP) if isinstance(context, str) else []
    source_ids_by_artifact = candidate.get("source_record_ids_by_artifact")
    source_ids_by_artifact = (
        source_ids_by_artifact if isinstance(source_ids_by_artifact, dict) else {}
    )
    artifact_bindings_valid = len(documents) == len(classifications) and bool(documents)
    reconstructed_source_ids: list[str] = []
    if artifact_bindings_valid:
        try:
            for document, classification in zip(
                documents, classifications, strict=True
            ):
                if not isinstance(classification, dict):
                    raise ProvenanceError("classification is malformed")
                artifact_id = str(classification.get("artifact_id") or "")
                text_sha256 = _sha256_text(document)
                document_record_ids = [
                    _pipeline_record_id(record)
                    for record in _pipeline_document_records(document)
                ]
                reconstructed_source_ids.extend(document_record_ids)
                artifact_bindings_valid = artifact_bindings_valid and bool(
                    artifact_id
                    and classification.get("provenance_id") == f"sha256:{text_sha256}"
                    and classification.get("source_origin") == "real_derived"
                    and classification.get("workflow_kind") == "real_source_derived"
                    and source_ids_by_artifact.get(artifact_id) == document_record_ids
                )
        except (TypeError, ValueError, ProvenanceError):
            artifact_bindings_valid = False
    corrupted = deepcopy(candidate)
    corrupted_documents = list(documents)
    corruption_fails = False
    if corrupted_documents:
        lines = corrupted_documents[-1].splitlines()
        last = json.loads(lines[-1])
        payload = last.get("source_payload")
        if isinstance(payload, dict):
            payload["requiredAction"] = f"CORRUPTED {payload['requiredAction']}"
            lines[-1] = _canonical_json(last)
            corrupted_documents[-1] = "\n".join(lines)
            corrupted["document_context"] = SEP.join(corrupted_documents)
            corruption_fails = replay_kev_pipeline_candidate(corrupted)[
                "answer"
            ] != candidate.get("answer")
    replay_binding = candidate.get("domain_history_replay_manifest")
    replay_binding_valid = isinstance(replay_binding, dict) and set(replay_binding) == {
        "schema_version",
        "sha256",
        "source_manifest_sha256",
        "source_response_sha256",
        "replay_revision",
    }
    if replay_binding_valid and isinstance(replay_binding, dict):
        replay_binding_valid = bool(
            replay_binding.get("schema_version") == KEV_PIPELINE_REPLAY_MANIFEST_SCHEMA
            and replay_binding.get("replay_revision") == KEV_HISTORY_REPLAY_REVISION
            and all(
                _SHA256.fullmatch(str(replay_binding.get(field) or ""))
                for field in (
                    "sha256",
                    "source_manifest_sha256",
                    "source_response_sha256",
                )
            )
        )
    declared_tokens = candidate.get("tokenizer_context_tokens")
    recomputed_tokens = token_counter(str(candidate.get("context") or ""))
    lower = candidate.get("band_lower_tokens")
    upper = candidate.get("band_upper_tokens")
    return {
        "strict_replay_sufficient": replay["answer"] == candidate.get("answer"),
        "counterfactual_replay_sufficient": cf_replay["answer"]
        == candidate.get("cf_answer"),
        "counterfactual_changes_answer": cf_replay["answer"] != replay["answer"],
        "remove_one_artifact_fails": bool(removals)
        and all(answer != candidate.get("answer") for answer in removals),
        "essential_single_artifact_insufficient": bool(singles)
        and all(answer != candidate.get("answer") for answer in singles),
        "every_top_three_insufficient": bool(top_three)
        and all(answer != candidate.get("answer") for answer in top_three),
        "semantic_corruption_fails": corruption_fails,
        "artifact_text_bindings_valid": artifact_bindings_valid,
        "source_records_reconstructed": reconstructed_source_ids
        == candidate.get("source_record_ids")
        == replay["source_record_ids"],
        "source_relations_reconstructed": candidate.get("source_relation_ids")
        == replay["source_relation_ids"],
        "replay_manifest_binding_valid": replay_binding_valid,
        "exact_token_count_recomputed": isinstance(declared_tokens, int)
        and not isinstance(declared_tokens, bool)
        and declared_tokens == recomputed_tokens,
        "exact_token_band_recomputed": isinstance(lower, int)
        and isinstance(upper, int)
        and lower <= recomputed_tokens <= upper,
        "ranker_contract_shape_valid": bool(
            candidate.get("data_stage") == "candidate"
            and candidate.get("training_objective") == "sft"
            and candidate.get("view") == "ordered_artifact_view"
            and candidate.get("composition_method") == "causal_timeline"
            and candidate.get("essential_artifact_ids") == artifact_ids
            and len(artifact_ids) == len(set(artifact_ids)) >= 4
            and candidate.get("pipeline_capabilities")
            == {
                "dense_ranking": True,
                "cyber_strict_replay": True,
                "generic_strict_replay": False,
                "generic_promotion": False,
            }
        ),
        "non_promoted_boundary": all(
            candidate.get(field) is False
            for field in (
                "train_ready",
                "production_eligible",
                "promotion_eligible",
                "complete_world",
                "promoted",
            )
        ),
    }
