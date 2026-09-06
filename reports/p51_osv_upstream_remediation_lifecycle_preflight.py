"""Aggregate-only P51 OSV/upstream remediation lifecycle preflight."""

from __future__ import annotations

import hashlib
import io
import json
import re
import ssl
import tarfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from transformers import AutoTokenizer

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p51_osv_upstream_remediation_lifecycle_preflight_v1.json")
OUTPUT = Path("reports/p51_osv_upstream_remediation_lifecycle_preflight_v1.json")
ALLOWED_HOSTS = frozenset(
    {
        "codeload.github.com",
        "github.com",
        "osv-vulnerabilities.storage.googleapis.com",
        "pypi.org",
        "raw.githubusercontent.com",
    }
)
USER_AGENT = "LongWorld-P51-source-preflight/1.0 xnhyacinth@users.noreply.github.com"
WORD = re.compile(r"[a-z0-9]+")
COMMIT_URL = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/commit/([0-9a-fA-F]{40})(?:\.patch)?$"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fetch(source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    url = source["url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P51 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=300, context=ssl.create_default_context()
    ) as response:
        raw = response.read(source["max_bytes"] + 1)
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P51 redirect target: {final_url}")
        if response.status != 200 or not raw or len(raw) > source["max_bytes"]:
            raise ValueError(f"invalid P51 response: {url}")
    if len(raw) != source["expected_bytes"]:
        raise ValueError(f"P51 source byte length changed: {url}")
    if _sha256(raw) != source["expected_sha256"]:
        raise ValueError(f"P51 source digest changed: {url}")
    return raw, {
        "url": url,
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "raw_source_persisted": False,
    }


def _safe_member_name(name: str) -> bool:
    return not name.startswith("/") and ".." not in name.split("/")


def _load_pypa_archive(
    raw: bytes, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = archive.getmembers()
        unsafe = [item.name for item in members if not _safe_member_name(item.name)]
        links = [item.name for item in members if item.issym() or item.islnk()]
        special = [
            item.name
            for item in members
            if not (item.isfile() or item.isdir() or item.issym() or item.islnk())
        ]
        files = [item for item in members if item.isfile()]
        if unsafe or links or special:
            raise ValueError("unsafe member in P51 PyPA archive")
        if sum(item.size for item in files) > 50_000_000:
            raise ValueError("P51 PyPA archive expands past 50 MB")
        if max(item.size for item in files) > 1_000_000:
            raise ValueError("P51 PyPA archive member exceeds 1 MB")

        licenses = [
            item
            for item in files
            if item.name.endswith(config["license_member_suffix"])
        ]
        if len(licenses) != 1:
            raise ValueError("P51 PyPA archive license member is not unique")
        license_raw = archive.extractfile(licenses[0]).read()
        if (
            len(license_raw) != config["license_expected_bytes"]
            or _sha256(license_raw) != config["license_expected_sha256"]
            or b"Creative Commons Attribution 4.0 International" not in license_raw
        ):
            raise ValueError("P51 PyPA archive license changed")

        advisory_members = [
            item
            for item in files
            if "/vulns/" in item.name and item.name.endswith(".yaml")
        ]
        for item in advisory_members:
            record = yaml.safe_load(archive.extractfile(item).read())
            if not isinstance(record, dict) or not record.get("id"):
                raise ValueError(f"invalid P51 PyPA advisory: {item.name}")
            records.append(record)
    return records, {
        "member_count": len(members),
        "regular_file_count": len(files),
        "advisory_count": len(records),
        "link_count": len(links),
        "unsafe_path_count": len(unsafe),
        "special_member_count": len(special),
        "uncompressed_bytes": sum(item.size for item in files),
        "largest_member_bytes": max(item.size for item in files),
        "license_spdx": config["license_spdx"],
        "license_sha256": _sha256(license_raw),
    }


def _load_osv_export(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        unsafe = [
            item.filename for item in members if not _safe_member_name(item.filename)
        ]
        encrypted = [item.filename for item in members if item.flag_bits & 1]
        if unsafe or encrypted:
            raise ValueError("unsafe member in P51 OSV ZIP")
        if sum(item.file_size for item in members) > 100_000_000:
            raise ValueError("P51 OSV ZIP expands past 100 MB")
        if max(item.file_size for item in members) > 1_000_000:
            raise ValueError("P51 OSV ZIP member exceeds 1 MB")
        if max(item.file_size / max(1, item.compress_size) for item in members) > 100:
            raise ValueError("P51 OSV ZIP compression ratio exceeds 100")
        for item in members:
            if item.is_dir() or not item.filename.endswith(".json"):
                raise ValueError(f"unexpected P51 OSV ZIP member: {item.filename}")
            record = json.loads(archive.read(item))
            if not isinstance(record, dict) or not record.get("id"):
                raise ValueError(f"invalid P51 OSV record: {item.filename}")
            records.append(record)
    return records, {
        "member_count": len(members),
        "record_count": len(records),
        "encrypted_member_count": len(encrypted),
        "unsafe_path_count": len(unsafe),
        "uncompressed_bytes": sum(item.file_size for item in members),
        "largest_member_bytes": max(item.file_size for item in members),
        "maximum_compression_ratio": max(
            item.file_size / max(1, item.compress_size) for item in members
        ),
    }


def _pypi_affected(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in record.get("affected", [])
        if item.get("package", {}).get("ecosystem") == "PyPI"
    ]


def _fixed_values(record: dict[str, Any], range_type: str | None = None) -> list[str]:
    return [
        str(event["fixed"])
        for affected in _pypi_affected(record)
        for affected_range in affected.get("ranges", [])
        if range_type is None or affected_range.get("type") == range_type
        for event in affected_range.get("events", [])
        if "fixed" in event
    ]


def _commit_fix_references(record: dict[str, Any]) -> list[str]:
    return [
        reference["url"]
        for reference in record.get("references", [])
        if reference.get("type") == "FIX"
        and COMMIT_URL.fullmatch(reference.get("url", ""))
    ]


def _affected_version_entries(record: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (affected["package"]["name"], str(version))
        for affected in _pypi_affected(record)
        for version in affected.get("versions", [])
    ]


def _fixed_boundary_entries(record: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (affected["package"]["name"], str(event["fixed"]))
        for affected in _pypi_affected(record)
        for affected_range in affected.get("ranges", [])
        if affected_range.get("type") == "ECOSYSTEM"
        for event in affected_range.get("events", [])
        if "fixed" in event
    ]


def _eligibility_reasons(record: dict[str, Any]) -> list[str]:
    reasons = []
    affected = _pypi_affected(record)
    if not record.get("aliases"):
        reasons.append("missing_alias")
    if not affected:
        reasons.append("missing_pypi_affected")
    if not _fixed_values(record):
        reasons.append("missing_fixed_event")
    if not _commit_fix_references(record):
        reasons.append("missing_github_fix_commit_reference")
    if not any(item.get("versions") for item in affected):
        reasons.append("missing_explicit_affected_versions")
    affected_entries = set(_affected_version_entries(record))
    if affected_entries & set(_fixed_boundary_entries(record)):
        reasons.append("affected_fixed_boundary_conflict")
    return reasons


def _semantic_text(record: dict[str, Any]) -> str:
    affected_output = []
    for affected in _pypi_affected(record):
        package = affected.get("package", {})
        ranges = []
        for affected_range in affected.get("ranges", []):
            events = [
                {
                    key: str(event[key])
                    for key in ("introduced", "fixed", "last_affected", "limit")
                    if key in event
                }
                for event in affected_range.get("events", [])
            ]
            if events:
                ranges.append(
                    {
                        "type": affected_range.get("type", ""),
                        "repo": affected_range.get("repo", ""),
                        "events": events,
                    }
                )
        affected_output.append(
            {
                "package": {
                    key: str(package[key])
                    for key in ("ecosystem", "name", "purl")
                    if key in package
                },
                "ranges": ranges,
            }
        )
    references = [
        {"type": item.get("type", ""), "url": item.get("url", "")}
        for item in record.get("references", [])
        if item.get("type") in {"ADVISORY", "FIX", "PACKAGE"}
    ]
    output = {
        "id": str(record["id"]),
        "aliases": sorted(map(str, record.get("aliases", []))),
        "published": str(record.get("published", "")),
        "modified": str(record.get("modified", "")),
        "withdrawn": str(record.get("withdrawn", "")),
        "summary": str(record.get("summary", "")),
        "details": str(record.get("details", "")),
        "affected": affected_output,
        "references": references,
    }
    return json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical(text: str) -> str:
    return " ".join(WORD.findall(text.lower()))


def _shingles(text: str, size: int) -> frozenset[tuple[str, ...]]:
    words = text.split()
    if len(words) < size:
        return frozenset({tuple(words)})
    return frozenset(zip(*(words[offset:] for offset in range(size)), strict=False))


def _jaccard(
    left: frozenset[tuple[str, ...]], right: frozenset[tuple[str, ...]]
) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _near_deduplicate(
    units: list[dict[str, Any]], size: int, threshold: float
) -> tuple[list[dict[str, Any]], int]:
    kept = []
    kept_shingles: list[frozenset[tuple[str, ...]]] = []
    inverted: dict[tuple[str, ...], list[int]] = defaultdict(list)
    removed = 0
    for unit in sorted(units, key=lambda item: (item["digest"], item["id"])):
        shingles = _shingles(unit["canonical"], size)
        candidates = set()
        for shingle in shingles:
            candidates.update(inverted[shingle])
        duplicate = False
        for index in sorted(candidates):
            prior = kept_shingles[index]
            if (
                min(len(shingles), len(prior)) / max(len(shingles), len(prior))
                < threshold
            ):
                continue
            if _jaccard(shingles, prior) >= threshold:
                duplicate = True
                break
        if duplicate:
            removed += 1
            continue
        index = len(kept)
        kept.append(unit)
        kept_shingles.append(shingles)
        for shingle in shingles:
            inverted[shingle].append(index)
    return kept, removed


def _token_lengths(tokenizer: Any, units: list[dict[str, Any]]) -> dict[str, int]:
    result = {}
    for offset in range(0, len(units), 128):
        batch = units[offset : offset + 128]
        encoded = tokenizer(
            [item["text"] for item in batch],
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        result.update(
            (item["digest"], len(token_ids))
            for item, token_ids in zip(batch, encoded, strict=True)
        )
    return result


def _band_pack(
    units: list[dict[str, Any]], lengths: dict[str, int], lower: int, upper: int
) -> dict[str, Any]:
    selected = []
    total = 0
    for unit in sorted(units, key=lambda item: (item["digest"], item["id"])):
        length = lengths[unit["digest"]]
        if total + length > upper:
            continue
        selected.append((unit, length))
        total += length
        if total >= lower:
            break
    receipt = "".join(
        f"{unit['id']}:{unit['digest']}:{length}\n" for unit, length in selected
    )
    return {
        "feasible": lower <= total <= upper,
        "qwen_tokens": total,
        "whole_advisory_count": len(selected),
        "advisory_receipt_sha256": _sha256(receipt.encode()),
        "padding_tokens": 0,
        "split_or_truncated_advisories": 0,
    }


def _record_projection(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "packages": sorted(
            item.get("package", {}).get("name", "") for item in _pypi_affected(record)
        ),
        "fixed": sorted(_fixed_values(record)),
        "commit_fix_references": sorted(_commit_fix_references(record)),
        "withdrawn": bool(record.get("withdrawn")),
    }


def _validate_chain(
    chain: dict[str, Any],
    pypa_by_id: dict[str, dict[str, Any]],
    osv_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    withdrawn = pypa_by_id[chain["withdrawn_id"]]
    active = pypa_by_id[chain["active_duplicate_id"]]
    expected_prefix = f"Withdrawn as duplicate of {chain['active_duplicate_id']}"
    if not str(withdrawn.get("details", "")).startswith(expected_prefix):
        raise ValueError(f"P51 duplicate edge missing: {chain['withdrawn_id']}")
    if not withdrawn.get("withdrawn") or active.get("withdrawn"):
        raise ValueError(f"P51 lifecycle status mismatch: {chain['withdrawn_id']}")
    shared_aliases = sorted(
        set(withdrawn.get("aliases", [])) & set(active.get("aliases", []))
    )
    if chain["required_shared_alias"] not in shared_aliases:
        raise ValueError(f"P51 shared alias missing: {chain['withdrawn_id']}")
    shared_packages = sorted(
        {item["package"]["name"] for item in _pypi_affected(withdrawn)}
        & {item["package"]["name"] for item in _pypi_affected(active)}
    )
    if chain["shared_package"] not in shared_packages:
        raise ValueError(f"P51 shared package missing: {chain['withdrawn_id']}")
    commit_url = chain["patch_source"]["url"].removesuffix(".patch")
    if commit_url not in _commit_fix_references(withdrawn):
        raise ValueError(
            f"P51 withdrawn FIX reference missing: {chain['withdrawn_id']}"
        )
    if commit_url not in _commit_fix_references(active):
        raise ValueError(
            f"P51 active FIX reference missing: {chain['active_duplicate_id']}"
        )
    if chain["fixed_commit"] not in _fixed_values(active, "GIT"):
        raise ValueError(f"P51 GIT fixed event missing: {chain['active_duplicate_id']}")
    if chain["fixed_release"] not in _fixed_values(active, "ECOSYSTEM"):
        raise ValueError(
            f"P51 release fixed event missing: {chain['active_duplicate_id']}"
        )

    mirror_withdrawn = osv_by_id[chain["withdrawn_id"]]
    mirror_active = osv_by_id[chain["active_duplicate_id"]]
    if _record_projection(withdrawn) != _record_projection(mirror_withdrawn):
        raise ValueError(f"P51 OSV mirror mismatch: {chain['withdrawn_id']}")
    if _record_projection(active) != _record_projection(mirror_active):
        raise ValueError(f"P51 OSV mirror mismatch: {chain['active_duplicate_id']}")
    if chain["active_duplicate_id"] not in mirror_withdrawn.get("aliases", []):
        raise ValueError(
            f"P51 OSV enriched duplicate alias missing: {chain['withdrawn_id']}"
        )

    patch_raw, patch_receipt = _fetch(chain["patch_source"])
    if b"Subject:" not in patch_raw or b"diff --git " not in patch_raw:
        raise ValueError(f"invalid P51 patch body: {chain['fixed_commit']}")
    release_raw, release_receipt = _fetch(chain["release_source"])
    release = json.loads(release_raw)
    if (
        release.get("info", {}).get("name", "").lower() != chain["shared_package"]
        or str(release.get("info", {}).get("version", "")) != chain["fixed_release"]
        or not release.get("urls")
    ):
        raise ValueError(f"invalid P51 PyPI release: {chain['shared_package']}")
    release_artifacts = sorted(
        (
            item.get("filename", ""),
            item.get("digests", {}).get("sha256", ""),
        )
        for item in release["urls"]
    )
    if any(not filename or not digest for filename, digest in release_artifacts):
        raise ValueError(
            f"incomplete P51 PyPI release digest: {chain['shared_package']}"
        )
    release_artifact_receipt = "".join(
        f"{filename}:{digest}\n" for filename, digest in release_artifacts
    )
    license_raw, license_receipt = _fetch(chain["license_source"])
    if chain["license_source"]["required_text"].encode() not in license_raw:
        raise ValueError(f"P51 upstream license mismatch: {chain['shared_package']}")

    return {
        "withdrawn_id": chain["withdrawn_id"],
        "active_duplicate_id": chain["active_duplicate_id"],
        "shared_package": chain["shared_package"],
        "shared_aliases": shared_aliases,
        "fixed_commit": chain["fixed_commit"],
        "fixed_release": chain["fixed_release"],
        "osv_mirror_enriched_with_active_alias": True,
        "upstream_patch_validated": True,
        "pypi_release_file_count": len(release["urls"]),
        "pypi_release_artifact_receipt_sha256": _sha256(
            release_artifact_receipt.encode()
        ),
        "upstream_license_spdx": chain["license_source"]["spdx"],
        "status": "PASS",
    }, [patch_receipt, release_receipt, license_receipt]


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        name: list(EXACT_TOKEN_BAND_RANGES[name]) for name in ("32k", "64k", "128k")
    }
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P51 capacity bands must match repository exact bands")

    pypa_raw, pypa_receipt = _fetch(config["pypa_archive"])
    pypa_records, pypa_safety = _load_pypa_archive(pypa_raw, config["pypa_archive"])
    osv_raw, osv_receipt = _fetch(config["osv_pypi_export"])
    osv_records, osv_safety = _load_osv_export(osv_raw)
    pypa_by_id = {record["id"]: record for record in pypa_records}
    osv_by_id = {record["id"]: record for record in osv_records}
    if len(pypa_by_id) != len(pypa_records) or len(osv_by_id) != len(osv_records):
        raise ValueError("duplicate P51 advisory ID")
    missing_from_mirror = sorted(pypa_by_id.keys() - osv_by_id.keys())
    if missing_from_mirror:
        raise ValueError("P51 pinned PyPA IDs missing from OSV export")

    rejection_reasons: Counter[str] = Counter()
    eligible = []
    for record in pypa_records:
        reasons = _eligibility_reasons(record)
        rejection_reasons.update(reasons)
        if not reasons:
            eligible.append(record)

    threshold = float(config["capacity_filter"]["near_duplicate_jaccard_threshold"])
    shingle_size = int(config["capacity_filter"]["near_duplicate_word_shingle_size"])
    minimum_characters = int(config["capacity_filter"]["minimum_semantic_characters"])
    units = []
    for record in eligible:
        text = _semantic_text(record)
        canonical = _canonical(text)
        if len(canonical) < minimum_characters:
            rejection_reasons["semantic_text_too_short"] += 1
            continue
        units.append(
            {
                "id": record["id"],
                "text": text,
                "canonical": canonical,
                "digest": _sha256(canonical.encode()),
            }
        )
    exact_units = {}
    for unit in units:
        prior = exact_units.setdefault(unit["digest"], unit)
        if prior["canonical"] != unit["canonical"]:
            raise ValueError("P51 canonical digest collision")
    exact_values = list(exact_units.values())
    near_units, near_removed = _near_deduplicate(exact_values, shingle_size, threshold)

    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=tokenizer_config["local_files_only"],
    )
    lengths = _token_lengths(tokenizer, [*exact_values, *near_units])
    exact_tokens = sum(lengths[item["digest"]] for item in exact_values)
    near_tokens = sum(lengths[item["digest"]] for item in near_units)
    token_values = sorted(lengths[item["digest"]] for item in near_units)
    packs = {
        band: _band_pack(near_units, lengths, *bounds)
        for band, bounds in config["capacity_bands"].items()
    }

    topology = []
    receipts = [pypa_receipt, osv_receipt]
    for chain in config["topology_chains"]:
        result, chain_receipts = _validate_chain(chain, pypa_by_id, osv_by_id)
        topology.append(result)
        receipts.extend(chain_receipts)

    affected_entries = [
        item for record in eligible for item in _affected_version_entries(record)
    ]
    fixed_boundaries = [
        item for record in eligible for item in _fixed_boundary_entries(record)
    ]
    git_fixed_values = [
        item for record in eligible for item in _fixed_values(record, "GIT")
    ]
    matching_git_fix_records = sum(
        any(
            match.group(3).lower()
            in {item.lower() for item in _fixed_values(record, "GIT")}
            for url in _commit_fix_references(record)
            if (match := COMMIT_URL.fullmatch(url))
        )
        for record in eligible
    )
    source_bundle = "".join(f"{item['url']}:{item['sha256']}\n" for item in receipts)
    all_packs_feasible = all(item["feasible"] for item in packs.values())
    report = {
        "schema_version": "longworld.p51-osv-upstream-remediation-lifecycle-preflight-report.v1",
        "data_product": config["data_product"],
        "config_sha256": _sha256(config_raw),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_receipt": {
            "source_count": len(receipts),
            "total_bytes": sum(item["bytes"] for item in receipts),
            "bundle_sha256": _sha256(source_bundle.encode()),
            "raw_source_persisted": False,
            "sources": receipts,
        },
        "archive_safety": {
            "pypa_commit": config["pypa_archive"]["commit"],
            "pypa": pypa_safety,
            "osv_generation": config["osv_pypi_export"]["generation"],
            "osv": osv_safety,
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "cross_source": {
            "pypa_advisory_count": len(pypa_records),
            "osv_pypi_record_count": len(osv_records),
            "pypa_ids_present_in_osv_export": len(pypa_by_id),
            "pypa_ids_missing_from_osv_export": 0,
            "selected_topology_record_projection_matches": 2 * len(topology),
            "selected_topology_record_count": 2 * len(topology),
            "projection_fields": [
                "package names",
                "fixed events",
                "GitHub FIX commit references",
                "withdrawn status",
            ],
        },
        "topology": {
            "validated_chain_count": len(topology),
            "chains": topology,
            "generic_related_treated_as_supersession": False,
        },
        "oracle_preflight": {
            "eligible_advisory_count": len(eligible),
            "affected_version_entries": len(affected_entries),
            "unique_affected_package_version_pairs": len(set(affected_entries)),
            "ecosystem_fixed_boundary_entries": len(fixed_boundaries),
            "unique_fixed_package_version_pairs": len(set(fixed_boundaries)),
            "git_fixed_event_entries": len(git_fixed_values),
            "unique_git_fixed_hashes": len(set(git_fixed_values)),
            "records_with_git_fixed_hash_matching_fix_reference": matching_git_fix_records,
            "affected_fixed_boundary_conflicts_in_eligible_pool": 0,
            "version_labels": {
                "AFFECTED": "exact package/version membership in affected.versions",
                "FIXED_BOUNDARY": "exact package/version equality with an ECOSYSTEM fixed event",
                "UNKNOWN": "every other package/version; never infer safe",
            },
            "commit_label": "FIXED_COMMIT only when a GIT fixed event equals a GitHub FIX-reference hash and the upstream receipt validates",
            "candidate_oracle_implemented": False,
        },
        "capacity": {
            "archive_advisory_count": len(pypa_records),
            "eligibility_rejection_reason_counts": dict(
                sorted(rejection_reasons.items())
            ),
            "eligible_advisory_count": len(eligible),
            "semantic_unit_count": len(units),
            "exact_deduplicated_advisory_count": len(exact_values),
            "exact_deduplicated_qwen_tokens": exact_tokens,
            "near_duplicate_word_shingle_size": shingle_size,
            "near_duplicate_jaccard_threshold": threshold,
            "near_deduplicated_advisory_count": len(near_units),
            "near_deduplicated_qwen_tokens": near_tokens,
            "near_duplicate_removed_count": near_removed,
            "minimum_advisory_tokens": token_values[0],
            "median_advisory_tokens": token_values[len(token_values) // 2],
            "maximum_advisory_tokens": token_values[-1],
            "excluded_capacity_fields": config["capacity_filter"][
                "excluded_capacity_fields"
            ],
            "exact_band_pack_preflight": packs,
            "capacity_is_preflight_only": True,
            "eligible_for_candidate_generation": False,
        },
        "rights_and_privacy": {
            "capacity_source_license": config["pypa_archive"]["license_spdx"],
            "topology_upstream_license_count": len(topology),
            "topology_upstream_licenses": sorted(
                {item["upstream_license_spdx"] for item in topology}
            ),
            "patch_and_release_bodies_contribute_capacity": False,
            "source_contains_private_records": False,
            "attribution_and_license_receipts_required_for_future_derivatives": True,
            "technical_rights_preflight": "PASS",
            "legal_opinion": False,
        },
        "verdict": {
            "official_cross_source_topology": "PASS",
            "deterministic_affected_fixed_oracle_preflight": "PASS",
            "near_deduplicated_32k_64k_128k_capacity_preflight": (
                "PASS" if all_packs_feasible else "FAIL"
            ),
            "formal_candidate_gates": "NOT_RUN",
            "train_ready": False,
            "do_not_generate": True,
            "candidate_count": 0,
            "inventory_delta": 0,
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
