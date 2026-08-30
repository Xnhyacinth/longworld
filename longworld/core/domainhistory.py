"""Source-bound cumulative histories for long-context candidate construction.

The first executable adapter builds chronological audit histories from one
cryptographically bound CISA KEV catalog snapshot.  Longer bands append whole,
distinct catalog records; no record is copied, truncated, or padded.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

DOMAIN_CUMULATIVE_HISTORY_SCHEMA = "longworld.domain-cumulative-history.v1"
KEV_HISTORY_REPLAY_REVISION = "longworld.kev-catalog-history-replay.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_CVE_ID = re.compile(r"^CVE-(?:1999|2\d{3})-\d{4,}$")
_EXPECTED_BANDS = ("16k", "32k", "64k")
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
        raise ProvenanceError("KEV history bands must be ordered 16k/32k/64k")

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
    """Return stable violations of strict 16/32/64K source-history growth."""
    errors: list[str] = []
    if [row.get("length_bucket") for row in rows] != list(_EXPECTED_BANDS[: len(rows)]):
        errors.append("bands_not_ordered_16k_32k_64k")
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
