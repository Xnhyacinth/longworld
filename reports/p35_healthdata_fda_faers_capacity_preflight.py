"""Aggregate-only P35 FAERS rights/privacy/schema/capacity preflight."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import ssl
import urllib.request
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from transformers import AutoTokenizer

from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p35_healthdata_fda_faers_case_version_preflight_v1.json")
OUTPUT = Path("reports/p35_healthdata_fda_faers_capacity_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"fis.fda.gov"})
USER_AGENT = "LongWorld-P35-source-preflight/1.0 xnhyacinth@users.noreply.github.com"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fetch(url: str) -> tuple[bytes, dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P35 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=180, context=ssl.create_default_context()
    ) as response:
        raw = response.read()
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P35 redirect target: {final_url}")
        return raw, {
            "requested_url": url,
            "final_url": final_url,
            "status": response.status,
            "content_type": response.headers.get_content_type(),
            "content_length_header": response.headers.get("Content-Length"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "observed_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }


def _validated_member_names(archive: zipfile.ZipFile) -> dict[str, str]:
    resolved = {}
    for name in archive.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive member: {name}")
        if not name.endswith(".txt"):
            continue
        basename = path.name
        if basename in resolved:
            raise ValueError(f"duplicate archive basename: {basename}")
        resolved[basename] = name
    return resolved


def _rows(
    archive: zipfile.ZipFile,
    member: str,
    expected_header: list[str],
) -> Iterable[dict[str, str]]:
    with (
        archive.open(member) as raw_file,
        io.TextIOWrapper(raw_file, encoding="latin-1", newline="") as text_file,
    ):
        reader = csv.reader(text_file, delimiter="$")
        header = next(reader)
        if header != expected_header:
            raise ValueError(
                f"schema mismatch for {PurePosixPath(member).name}: {header}"
            )
        for row_number, values in enumerate(reader, start=2):
            if len(values) != len(header):
                raise ValueError(
                    f"malformed row {row_number} in {PurePosixPath(member).name}: "
                    f"expected {len(header)} fields, observed {len(values)}"
                )
            yield dict(zip(header, values, strict=True))


def _integer(value: str, label: str) -> int:
    if not value.isdigit():
        raise ValueError(f"non-integer {label}")
    return int(value)


def _safe_row(table: str, row: dict[str, str], allowed: set[str]) -> str:
    fields = [
        f"{key}={row[key].strip()}"
        for key in row
        if key in allowed and row[key].strip()
    ]
    return "|".join([table.removesuffix("13Q3.txt"), *fields])


def _coarsen_case(pattern: str) -> str:
    lines = pattern.splitlines()
    demo = lines[0]
    child_lines = lines[1:]
    table_counts = Counter(line.split("|", 1)[0] for line in child_lines)
    categorical_sets: dict[str, set[str]] = defaultdict(set)
    for line in child_lines:
        table = line.split("|", 1)[0]
        for item in line.split("|")[1:]:
            if not item.startswith(("drug_seq=", "indi_drug_seq=", "dsg_drug_seq=")):
                categorical_sets[table].add(item)
    summary = [demo]
    for table in sorted(table_counts):
        values = ",".join(sorted(categorical_sets[table]))
        summary.append(f"{table}|rows={table_counts[table]}|values={values}")
    return "\n".join(summary)


def _token_count(tokenizer: Any, texts: Iterable[str], batch_size: int = 256) -> int:
    total = 0
    batch = []
    for text in texts:
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


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {"64k": [64_000, 65_536], "128k": [128_000, 131_072]}
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P35 capacity bands must match the repository exact bands")
    source = config["source"]
    archive_raw, receipt = _fetch(source["archive_url"])
    if len(archive_raw) != source["expected_archive_bytes"]:
        raise ValueError("P35 archive byte length changed")
    if _sha256(archive_raw) != source["expected_sha256"]:
        raise ValueError("P35 archive digest changed")

    expected_tables: dict[str, list[str]] = source["expected_tables"]
    archive = zipfile.ZipFile(io.BytesIO(archive_raw))
    members = _validated_member_names(archive)
    if set(members) != set(expected_tables):
        raise ValueError(
            f"P35 table set mismatch: expected {sorted(expected_tables)}, "
            f"observed {sorted(members)}"
        )

    demo_name = "DEMO13Q3.txt"
    primary_to_case: dict[str, tuple[str, int]] = {}
    versions_by_case: dict[str, list[tuple[int, str]]] = defaultdict(list)
    demo_structural: dict[str, str] = {}
    demo_row_count = 0
    primary_duplicates = 0
    exact_demo_duplicates = 0
    seen_demo_rows: set[tuple[str, ...]] = set()
    allowed = set(config["privacy_policy"]["structural_fields_allowed_for_preflight"])
    for row in _rows(archive, members[demo_name], expected_tables[demo_name]):
        demo_row_count += 1
        primary_id = row["primaryid"]
        case_id = row["caseid"]
        version = _integer(row["caseversion"], "caseversion")
        _integer(primary_id, "primaryid")
        _integer(case_id, "caseid")
        if primary_id in primary_to_case:
            primary_duplicates += 1
        else:
            primary_to_case[primary_id] = (case_id, version)
            versions_by_case[case_id].append((version, primary_id))
            demo_structural[primary_id] = _safe_row(demo_name, row, allowed)
        raw_tuple = tuple(row[field] for field in expected_tables[demo_name])
        if raw_tuple in seen_demo_rows:
            exact_demo_duplicates += 1
        seen_demo_rows.add(raw_tuple)

    latest_by_case: dict[str, str] = {}
    same_version_ties = 0
    for case_id, versions in versions_by_case.items():
        version_counts = Counter(version for version, _ in versions)
        same_version_ties += sum(count - 1 for count in version_counts.values())
        latest_version = max(version_counts)
        latest_candidates = [
            primary_id for version, primary_id in versions if version == latest_version
        ]
        if len(latest_candidates) != 1:
            continue
        latest_by_case[case_id] = latest_candidates[0]
    latest_primary_ids = set(latest_by_case.values())

    table_metrics: dict[str, dict[str, Any]] = {}
    case_children: dict[str, list[str]] = defaultdict(list)
    for basename, header in expected_tables.items():
        if basename == demo_name:
            continue
        row_count = 0
        latest_row_count = 0
        stale_row_count = 0
        orphan_primary_count = 0
        mismatched_case_count = 0
        structural_counter: Counter[str] = Counter()
        for row in _rows(archive, members[basename], header):
            row_count += 1
            primary_id = row["primaryid"]
            case_id = row["caseid"]
            parent = primary_to_case.get(primary_id)
            if parent is None:
                orphan_primary_count += 1
                continue
            if parent[0] != case_id:
                mismatched_case_count += 1
                continue
            safe_row = _safe_row(basename, row, allowed)
            structural_counter[safe_row] += 1
            if primary_id in latest_primary_ids:
                latest_row_count += 1
                case_children[primary_id].append(safe_row)
            else:
                stale_row_count += 1
        table_metrics[basename] = {
            "rows": row_count,
            "latest_valid_rows": latest_row_count,
            "stale_version_rows": stale_row_count,
            "orphan_primary_rows": orphan_primary_count,
            "mismatched_case_rows": mismatched_case_count,
            "identifier_free_structural_row_patterns": len(structural_counter),
            "max_repetition_of_one_structural_pattern": max(
                structural_counter.values(), default=0
            ),
        }

    case_patterns = []
    cases_with_no_child_rows = 0
    for primary_id in latest_primary_ids:
        child_lines = sorted(case_children.get(primary_id, []))
        if not child_lines:
            cases_with_no_child_rows += 1
        case_patterns.append("\n".join([demo_structural[primary_id], *child_lines]))
    unique_case_patterns = set(case_patterns)
    coarsened_case_patterns = {
        _coarsen_case(pattern) for pattern in unique_case_patterns
    }

    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=True,
    )
    all_structural_tokens = _token_count(tokenizer, case_patterns)
    exact_dedup_structural_tokens = _token_count(tokenizer, unique_case_patterns)
    coarsened_structural_tokens = _token_count(tokenizer, coarsened_case_patterns)

    integrity_ok = (
        primary_duplicates == 0
        and same_version_ties == 0
        and all(
            metrics["orphan_primary_rows"] == 0 and metrics["mismatched_case_rows"] == 0
            for metrics in table_metrics.values()
        )
    )
    upper_64k = exact_dedup_structural_tokens >= config["capacity_bands"]["64k"][0]
    upper_128k = exact_dedup_structural_tokens >= config["capacity_bands"]["128k"][0]
    lower_64k = coarsened_structural_tokens >= config["capacity_bands"]["64k"][0]
    lower_128k = coarsened_structural_tokens >= config["capacity_bands"]["128k"][0]

    payload = {
        "schema_version": "longworld.p35-healthdata-capacity-preflight.v1",
        "data_product": config["data_product"],
        "verdict": "SOURCE_ACCESS_PASS_WORLD_BLOCKED_MISSING_VERSION_TOPOLOGY",
        "rights_gate": "ENGINEERING_PASS_FORMAL_LEGAL_REVIEW_NOT_COMPLETED",
        "privacy_gate": "PASS_ONLY_FOR_STRUCTURE_ONLY_EPHEMERAL_INSPECTION",
        "schema_gate": "PASS" if integrity_ok else "FAIL",
        "unique_capacity_gate": "BLOCKED_FORMAL_NEAR_DUP_NOT_RUN",
        "world_admission_status": "BLOCKED_MISSING_VERSION_TOPOLOGY",
        "do_not_generate": True,
        "train_ready": False,
        "production_eligible": False,
        "research_question": config["research_question"],
        "constraints": config["constraints"],
        "source_receipt": {
            **receipt,
            "quarter": source["quarter"],
            "official_index_url": source["official_page_url"],
            "archive_persisted": False,
            "raw_rows_persisted": False,
            "config": str(CONFIG),
            "config_sha256": _sha256(config_raw),
            "archive_member_count": len(archive.namelist()),
            "text_table_members": sorted(members),
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "schema_and_relations": {
            "schemas": expected_tables,
            "demographic_rows": demo_row_count,
            "unique_primary_records": len(primary_to_case),
            "case_count": len(versions_by_case),
            "quarter_local_latest_case_count": len(latest_by_case),
            "cases_with_multiple_versions": sum(
                len(versions) > 1 for versions in versions_by_case.values()
            ),
            "max_case_version": max(
                version
                for versions in versions_by_case.values()
                for version, _ in versions
            ),
            "duplicate_primary_records": primary_duplicates,
            "exact_duplicate_demographic_rows": exact_demo_duplicates,
            "same_case_version_ties": same_version_ties,
            "quarter_local_latest_cases_without_child_rows": cases_with_no_child_rows,
            "tables": table_metrics,
            "global_latest_semantics": (
                "not established: this source is one non-cumulative quarter, so later "
                "quarters may supersede or deactivate these cases"
            ),
        },
        "privacy_and_rights": {
            "public_scope": "FDA publishes the QDE as public raw individual case safety reports; narratives are excluded from the QDE and require privacy review before FOIA disclosure",
            "reuse_scope": "FDA website content is generally public domain and openFDA is generally CC0, but this is an engineering gate rather than a formal legal opinion on every submitted field",
            "source_system_contains_personal_data": True,
            "retained_for_capacity": config["privacy_policy"][
                "structural_fields_allowed_for_preflight"
            ],
            "excluded_from_persistence": config["privacy_policy"]["never_persist"],
            "join_key_policy": config["privacy_policy"]["production_join_key"],
            "case_identifier_or_hash_written_to_report": False,
            "formal_privacy_review_completed": False,
            "formal_legal_review_completed": False,
        },
        "capacity": {
            "latest_case_artifacts": len(case_patterns),
            "identifier_free_exact_case_patterns": len(unique_case_patterns),
            "coarsened_structure_patterns": len(coarsened_case_patterns),
            "all_structure_only_tokens": all_structural_tokens,
            "identifier_free_exact_dedup_tokens": exact_dedup_structural_tokens,
            "coarsened_structure_tokens": coarsened_structural_tokens,
            "formal_near_duplicate_audit_executed": False,
            "near_duplicate_upper_bound_tokens": exact_dedup_structural_tokens,
            "coarsened_conservative_estimate_tokens": coarsened_structural_tokens,
            "gap_from_near_duplicate_upper_bound_to_64k_lower": (
                exact_dedup_structural_tokens - config["capacity_bands"]["64k"][0]
            ),
            "gap_from_near_duplicate_upper_bound_to_128k_lower": (
                exact_dedup_structural_tokens - config["capacity_bands"]["128k"][0]
            ),
            "64k_upper_bound_possible": upper_64k,
            "128k_upper_bound_possible": upper_128k,
            "64k_coarsened_estimate_possible": lower_64k,
            "128k_coarsened_estimate_possible": lower_128k,
            "bands": config["capacity_bands"],
            "interpretation": (
                "counts exclude identifiers, demographics, product names, clinical terms, "
                "dates, dose, lot, and reporter/manufacturer fields; exact-dedup is only an "
                "upper bound until unchanged candidate-level near-duplicate gates run"
            ),
        },
        "oracle_assessment": {
            "status": "MISSING_CASE_VERSION_HISTORY_NOT_ADMITTED",
            "task_topology": [
                "select the maximum valid case version within the frozen source snapshot",
                "join each child table on the paired primaryid/caseid foreign key",
                "report raw, stale-version, latest-version, orphan, and mismatch control totals",
            ],
            "deterministic_oracle": True,
            "answer_changing_remove_one": {
                "latest_case_artifact": "changes selected-case and one or more joined-table control totals",
                "retained_child_row": "decrements the corresponding latest-valid table total",
                "stale_case_artifact": "changes raw/stale-version controls even if the selected latest record is unchanged",
            },
            "medical_claims_prohibited": [
                "causality",
                "incidence_or_prevalence",
                "product_risk_ranking",
                "comparative_safety",
                "clinical_advice",
            ],
        },
        "shortcut_assessment": {
            "status": "HIGH_RISK_UNTESTED",
            "4k_8k_16k_risk": (
                "a global reconciliation total requires all retained rows only if no supplied "
                "metadata leaks precomputed row counts; this is aggregation dependence, not yet "
                "evidence of semantic long-range reasoning"
            ),
            "raw_window_audit_executed": False,
            "lexical_retrieval_audit_executed": False,
            "embedding_retrieval_audit_executed": False,
            "required_next_test": (
                "materialize a local non-training proof slice with no precomputed totals, then "
                "verify full, remove-one, and contiguous 4K/8K/16K views under the unchanged gates"
            ),
        },
        "blocking_reasons": [
            "one non-cumulative quarter supports only quarter-local latest-version semantics, not a globally latest record",
            "formal case-level near-duplicate capacity has not been measured",
            "privacy approval is limited to ephemeral structure-only inspection; no release approval exists for case-level training text",
            "4K/8K/16K and retrieval shortcut audits have not run",
            "the proposed dependence is global aggregation and must not be mislabeled as semantic long-range reasoning",
        ],
        "next_actions": [
            "keep this route at zero train-ready rows and do not generate a candidate",
            "if continued, freeze a multi-quarter cutoff or use an authoritative latest-only snapshot so latest-version semantics are well-defined",
            "obtain explicit privacy/release approval for the exact field projection before any case-level training materialization",
            "run formal near-duplicate and 4K/8K/16K shortcut audits on a local structure-only proof slice",
            "reject the world if capacity is driven by surrogate ordinals, repeated templates, or leaked control totals",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "schema_gate": payload["schema_gate"],
                "schema_and_relations": payload["schema_and_relations"],
                "capacity": payload["capacity"],
                "blocking_reasons": payload["blocking_reasons"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
