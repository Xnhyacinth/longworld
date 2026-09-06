"""Measure real P45 FAERS version chains without persisting case-level data."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
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

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path(
    "configs/p45_healthdata_fda_faers_two_quarter_version_chain_preflight_v1.json"
)
OUTPUT = Path(
    "reports/p45_healthdata_fda_faers_two_quarter_version_chain_preflight_v1.json"
)
ALLOWED_HOSTS = frozenset({"fis.fda.gov"})
USER_AGENT = "LongWorld-P45-source-preflight/1.0 xnhyacinth@users.noreply.github.com"
TABLE_NAME = re.compile(r"^(DEMO|DRUG|INDI|OUTC|REAC|RPSR|THER)\d{2}Q[1-4]\.TXT$")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fetch(url: str) -> tuple[bytes, dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P45 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=180, context=ssl.create_default_context()
    ) as response:
        raw = response.read()
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P45 redirect target: {final_url}")
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


def _table_members(archive: zipfile.ZipFile) -> dict[str, str]:
    members = {}
    for name in archive.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive member: {name}")
        match = TABLE_NAME.fullmatch(path.name.upper())
        if match is None:
            continue
        table = match.group(1)
        if table in members:
            raise ValueError(f"duplicate P45 table: {table}")
        members[table] = name
    return members


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
            raise ValueError(f"schema mismatch for {PurePosixPath(member).name}")
        for row_number, values in enumerate(reader, start=2):
            if len(values) != len(header):
                raise ValueError(
                    f"malformed row {row_number} in {PurePosixPath(member).name}"
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
    return "|".join([table, *fields])


def _coarsen_chain(pattern: str) -> str:
    counter: Counter[str] = Counter()
    categories: dict[str, set[str]] = defaultdict(set)
    for line in pattern.splitlines():
        prefix = line.split("|", 2)[:2]
        group = "|".join(prefix)
        counter[group] += 1
        for field in line.split("|")[2:]:
            if not field.startswith(("drug_seq=", "indi_drug_seq=", "dsg_drug_seq=")):
                categories[group].add(field)
    return "\n".join(
        f"{group}|rows={counter[group]}|values={','.join(sorted(categories[group]))}"
        for group in sorted(counter)
    )


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
    expected_bands = {
        name: list(EXACT_TOKEN_BAND_RANGES[name]) for name in ("32k", "64k", "128k")
    }
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P45 capacity bands must match repository exact bands")

    schemas: dict[str, list[str]] = config["tables"]
    source_receipts = []
    archives: dict[str, zipfile.ZipFile] = {}
    members_by_quarter: dict[str, dict[str, str]] = {}
    for source in config["sources"]:
        raw, receipt = _fetch(source["archive_url"])
        if len(raw) != source["expected_archive_bytes"]:
            raise ValueError(f"archive byte length changed: {source['quarter']}")
        if _sha256(raw) != source["expected_sha256"]:
            raise ValueError(f"archive digest changed: {source['quarter']}")
        archive = zipfile.ZipFile(io.BytesIO(raw))
        members = _table_members(archive)
        if set(members) != set(schemas):
            raise ValueError(f"table set mismatch: {source['quarter']}")
        archives[source["quarter"]] = archive
        members_by_quarter[source["quarter"]] = members
        source_receipts.append(
            {
                **receipt,
                "quarter": source["quarter"],
                "archive_persisted": False,
                "raw_rows_persisted": False,
                "table_members": sorted(members),
            }
        )

    quarter_order = [source["quarter"] for source in config["sources"]]
    if len(quarter_order) != 2:
        raise ValueError("P45 requires exactly two adjacent quarters")
    earlier, later = quarter_order
    demo_by_quarter: dict[str, dict[str, tuple[str, int, str]]] = {}
    primary_parent_by_quarter: dict[str, dict[str, str]] = {}
    duplicate_case_rows = {}
    for quarter in quarter_order:
        archive = archives[quarter]
        members = members_by_quarter[quarter]
        demos: dict[str, tuple[str, int, str]] = {}
        primary_parent: dict[str, str] = {}
        duplicates = 0
        for row in _rows(archive, members["DEMO"], schemas["DEMO"]):
            primary_id = row["primaryid"]
            case_id = row["caseid"]
            version = _integer(row["caseversion"], "caseversion")
            _integer(primary_id, "primaryid")
            _integer(case_id, "caseid")
            if case_id in demos or primary_id in primary_parent:
                duplicates += 1
                continue
            demos[case_id] = (
                primary_id,
                version,
                _safe_row("DEMO", row, set(config["structural_fields"])),
            )
            primary_parent[primary_id] = case_id
        demo_by_quarter[quarter] = demos
        primary_parent_by_quarter[quarter] = primary_parent
        duplicate_case_rows[quarter] = duplicates

    earlier_cases = demo_by_quarter[earlier]
    later_cases = demo_by_quarter[later]
    overlap = set(earlier_cases) & set(later_cases)
    increasing = {
        case_id
        for case_id in overlap
        if later_cases[case_id][1] > earlier_cases[case_id][1]
    }
    same_version = sum(
        later_cases[case_id][1] == earlier_cases[case_id][1] for case_id in overlap
    )
    regressed = sum(
        later_cases[case_id][1] < earlier_cases[case_id][1] for case_id in overlap
    )
    version_delta_counts = Counter(
        later_cases[case_id][1] - earlier_cases[case_id][1] for case_id in increasing
    )

    target_primary: dict[str, dict[str, tuple[str, str]]] = {
        quarter: {} for quarter in quarter_order
    }
    for case_id in increasing:
        target_primary[earlier][earlier_cases[case_id][0]] = (case_id, "OLD")
        target_primary[later][later_cases[case_id][0]] = (case_id, "NEW")

    child_lines: dict[tuple[str, str], list[str]] = defaultdict(list)
    table_metrics: dict[str, dict[str, Any]] = {}
    integrity_ok = all(value == 0 for value in duplicate_case_rows.values())
    allowed = set(config["structural_fields"])
    for quarter in quarter_order:
        archive = archives[quarter]
        members = members_by_quarter[quarter]
        parent = primary_parent_by_quarter[quarter]
        table_metrics[quarter] = {}
        for table, schema in schemas.items():
            if table == "DEMO":
                continue
            rows = 0
            orphan_rows = 0
            mismatched_rows = 0
            version_chain_rows = 0
            for row in _rows(archive, members[table], schema):
                rows += 1
                primary_id = row["primaryid"]
                case_id = row["caseid"]
                expected_case = parent.get(primary_id)
                if expected_case is None:
                    orphan_rows += 1
                    continue
                if expected_case != case_id:
                    mismatched_rows += 1
                    continue
                target = target_primary[quarter].get(primary_id)
                if target is not None:
                    target_case, side = target
                    child_lines[(target_case, side)].append(
                        f"{side}|{_safe_row(table, row, allowed)}"
                    )
                    version_chain_rows += 1
            integrity_ok = integrity_ok and orphan_rows == 0 and mismatched_rows == 0
            table_metrics[quarter][table] = {
                "rows": rows,
                "orphan_primary_rows": orphan_rows,
                "mismatched_case_rows": mismatched_rows,
                "version_chain_rows": version_chain_rows,
            }

    chain_patterns = []
    chains_with_structural_delta = 0
    for case_id in increasing:
        old_demo = f"OLD|{earlier_cases[case_id][2]}"
        new_demo = f"NEW|{later_cases[case_id][2]}"
        old_lines = sorted(child_lines[(case_id, "OLD")])
        new_lines = sorted(child_lines[(case_id, "NEW")])
        if old_demo.removeprefix("OLD|") != new_demo.removeprefix("NEW|") or [
            line.removeprefix("OLD|") for line in old_lines
        ] != [line.removeprefix("NEW|") for line in new_lines]:
            chains_with_structural_delta += 1
        chain_patterns.append("\n".join([old_demo, *old_lines, new_demo, *new_lines]))

    unique_patterns = set(chain_patterns)
    coarsened_patterns = {_coarsen_chain(pattern) for pattern in unique_patterns}
    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=True,
    )
    all_tokens = _token_count(tokenizer, chain_patterns)
    exact_dedup_tokens = _token_count(tokenizer, unique_patterns)
    coarsened_tokens = _token_count(tokenizer, coarsened_patterns)

    capacity = {}
    for name, bounds in expected_bands.items():
        capacity[name] = {
            "exact_dedup_upper_bound_possible": exact_dedup_tokens >= bounds[0],
            "coarsened_estimate_possible": coarsened_tokens >= bounds[0],
            "gap_from_exact_dedup_upper_bound_to_lower_edge": (
                exact_dedup_tokens - bounds[0]
            ),
            "gap_from_coarsened_estimate_to_lower_edge": coarsened_tokens - bounds[0],
        }

    topology_pass = len(increasing) > 0 and regressed == 0
    if topology_pass:
        unique_capacity_gate = "BLOCKED_FORMAL_NEAR_DUP_NOT_RUN"
        active_validity_gate = "BLOCKED_NO_DELETION_OR_MERGE_TOMBSTONES"
        shortcut_gate = "BLOCKED_NOT_RUN"
        answer_program_status = (
            "EXECUTABLE_LATEST_OBSERVED_DESIGN_NOT_LATEST_VALID_ACTIVE"
        )
        remove_one = (
            "removing a selected later-version artifact falls back to the earlier version "
            "and changes the selected-version aggregate; child-row removal changes its control total"
        )
        blocking_reasons = [
            "no source-backed deletion or merge tombstones, so latest valid active state is unresolved",
            "case-level training release has no explicit privacy approval",
            "formal near-duplicate and exact-band materialization have not run",
            "4K/8K/16K and retrieval shortcut audits have not run",
            "global aggregation dependence must not be mislabeled as semantic long-range reasoning",
        ]
    else:
        unique_capacity_gate = "FAIL_ZERO_VERSION_CHAIN_CAPACITY"
        active_validity_gate = "NOT_REACHED_MISSING_VERSION_TOPOLOGY"
        shortcut_gate = "NOT_REACHED_MISSING_VERSION_TOPOLOGY"
        answer_program_status = "BLOCKED_NO_OBSERVED_VERSION_CHAIN"
        remove_one = (
            "not executable: no case occurs in both quarters, so removing a later version "
            "cannot fall back to a source-observed earlier version"
        )
        blocking_reasons = [
            "2013Q3 and 2013Q4 have zero cross-quarter case overlap and zero increasing-version chains",
            "version-chain-only capacity is zero for 32K, 64K, and 128K",
            "no source-backed deletion or merge tombstones, so latest valid active state would remain unresolved even if a version chain were found",
            "case-level training release has no explicit privacy approval",
            "global aggregation dependence must not be mislabeled as semantic long-range reasoning",
        ]
    payload = {
        "schema_version": "longworld.p45-healthdata-version-chain-preflight.v1",
        "data_product": config["data_product"],
        "verdict": (
            "REAL_VERSION_TOPOLOGY_PASS_WORLD_BLOCKED_ACTIVE_VALIDITY_PRIVACY_NEAR_DUP"
            if topology_pass and integrity_ok
            else "SOURCE_TOPOLOGY_FAIL"
        ),
        "rights_gate": "ENGINEERING_PASS_FORMAL_LEGAL_REVIEW_NOT_COMPLETED",
        "privacy_gate": "PASS_ONLY_FOR_STRUCTURE_ONLY_EPHEMERAL_INSPECTION",
        "schema_gate": "PASS" if integrity_ok else "FAIL",
        "version_topology_gate": "PASS" if topology_pass else "FAIL",
        "unique_capacity_gate": unique_capacity_gate,
        "active_validity_gate": active_validity_gate,
        "shortcut_gate": shortcut_gate,
        "world_admission_status": "BLOCKED",
        "do_not_generate": True,
        "train_ready": False,
        "production_eligible": False,
        "research_question": config["research_question"],
        "cutoff": config["cutoff"],
        "constraints": config["constraints"],
        "source_receipt": {
            "official_index_url": config["official_index_url"],
            "config": str(CONFIG),
            "config_sha256": _sha256(config_raw),
            "retrievals": source_receipts,
            "case_identifiers_or_identifier_hashes_persisted": False,
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "relations": {
            "quarter_case_counts": {
                quarter: len(demo_by_quarter[quarter]) for quarter in quarter_order
            },
            "duplicate_case_or_primary_rows": duplicate_case_rows,
            "cross_quarter_case_overlap": len(overlap),
            "strictly_increasing_version_chains": len(increasing),
            "same_version_cross_quarter_cases": same_version,
            "regressed_version_cross_quarter_cases": regressed,
            "version_delta_distribution": {
                str(delta): count
                for delta, count in sorted(version_delta_counts.items())
            },
            "chains_with_structure_only_answer_delta": chains_with_structural_delta,
            "table_metrics": table_metrics,
        },
        "capacity": {
            "version_chain_artifacts": len(chain_patterns),
            "identifier_free_exact_chain_patterns": len(unique_patterns),
            "coarsened_chain_patterns": len(coarsened_patterns),
            "all_version_chain_structure_tokens": all_tokens,
            "identifier_free_exact_dedup_tokens": exact_dedup_tokens,
            "coarsened_structure_tokens": coarsened_tokens,
            "formal_near_duplicate_audit_executed": False,
            "bands": expected_bands,
            "band_assessment": capacity,
            "interpretation": (
                "only real increasing-version cases and their two versions count; unrelated "
                "single-version cases, source identifiers, clinical terms, and demographics are excluded"
            ),
        },
        "answer_program": {
            "status": answer_program_status,
            "steps": [
                "validate each quarter schema and paired primaryid/caseid foreign keys",
                "union only same-case records across the frozen adjacent quarters",
                "select maximum caseversion at the 2013Q4 cutoff and preserve the previous version as the counterfactual",
                "join child rows to each version using both primaryid and caseid",
                "emit version-transition, raw, previous, selected, orphan, mismatch, and per-table control totals",
            ],
            "deterministic": True,
            "answer_changing_remove_one": remove_one,
            "remaining_semantic_gap": (
                "the archives expose no deletion/merge tombstone, so latest observed cannot be "
                "claimed as latest valid active case"
            ),
        },
        "shortcut_assessment": {
            "status": "HIGH_RISK_UNTESTED",
            "32k_64k_128k": (
                "source volume is measured only over real version chains, but exact-band packing "
                "and unchanged near-duplicate gates have not run"
            ),
            "4k_8k_16k": (
                "global controls can force whole-input aggregation when totals are absent, but "
                "that is structural rather than semantic long-range dependence"
            ),
            "required_audits": [
                "contiguous raw 4K/8K/16K",
                "lexical and dense retrieval views",
                "selected-version remove-one fallback",
                "candidate-level exact and near-duplicate gates",
            ],
        },
        "blocking_reasons": blocking_reasons,
        "next_executable_command": (
            "uv run python reports/p45_healthdata_fda_faers_two_quarter_preflight.py"
        ),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "relations": payload["relations"],
                "capacity": payload["capacity"],
                "blocking_reasons": payload["blocking_reasons"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
