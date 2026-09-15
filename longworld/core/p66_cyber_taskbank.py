"""Deterministic local-candidate tasks over licensed PyPA OSV advisories."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from longworld.core.provenance import ProvenanceError

SCHEMA = "longworld.p66-cyber-task.v1"
COMMIT_URL = re.compile(
    r"https://github\.com/[^/]+/[^/]+/commit/([0-9a-fA-F]{40})(?:\.patch)?$"
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def semantic_advisory(record: dict[str, Any]) -> dict[str, Any]:
    """Project a PyPA OSV advisory without repeated affected-version lists."""
    affected = []
    for item in record.get("affected", []):
        package = item.get("package", {})
        if package.get("ecosystem") != "PyPI":
            continue
        ranges = []
        for raw_range in item.get("ranges", []):
            events = [
                {
                    key: str(event[key])
                    for key in ("introduced", "fixed", "last_affected", "limit")
                    if key in event
                }
                for event in raw_range.get("events", [])
                if isinstance(event, dict)
            ]
            if events:
                ranges.append(
                    {
                        "type": str(raw_range.get("type", "")),
                        "repo": str(raw_range.get("repo", "")),
                        "events": events,
                    }
                )
        affected.append(
            {
                "package": {
                    key: str(package[key])
                    for key in ("ecosystem", "name", "purl")
                    if key in package
                },
                "ranges": ranges,
            }
        )
    return {
        "id": str(record["id"]),
        "aliases": sorted(map(str, record.get("aliases", []))),
        "published": str(record.get("published", "")),
        "modified": str(record.get("modified", "")),
        "withdrawn": str(record.get("withdrawn", "")),
        "summary": str(record.get("summary", "")),
        "details": str(record.get("details", "")),
        "affected": affected,
        "references": [
            {"type": str(item.get("type", "")), "url": str(item.get("url", ""))}
            for item in record.get("references", [])
            if item.get("type") in {"ADVISORY", "FIX", "PACKAGE"}
        ],
    }


def package_names(record: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item.get("package", {}).get("name", "")).lower()
            for item in record.get("affected", [])
            if item.get("package", {}).get("ecosystem") == "PyPI"
            and item.get("package", {}).get("name")
        }
    )


def _fixed(record: dict[str, Any], range_type: str) -> list[str]:
    return sorted(
        {
            str(event["fixed"])
            for item in record.get("affected", [])
            for raw_range in item.get("ranges", [])
            if raw_range.get("type") == range_type
            for event in raw_range.get("events", [])
            if isinstance(event, dict) and "fixed" in event
        }
    )


def _row(record: dict[str, Any], family: str) -> dict[str, Any]:
    base = {
        "id": record["id"],
        "packages": package_names(record),
    }
    if family == "lifecycle_matrix":
        return {
            **base,
            "aliases": record.get("aliases", []),
            "published": record.get("published", ""),
            "modified": record.get("modified", ""),
            "withdrawn": record.get("withdrawn", ""),
            "ecosystem_fixed": _fixed(record, "ECOSYSTEM"),
            "git_fixed": _fixed(record, "GIT"),
        }
    if family == "remediation_commit_join":
        reference_hashes = {
            match.group(1).lower()
            for item in record.get("references", [])
            if item.get("type") == "FIX"
            and (match := COMMIT_URL.fullmatch(str(item.get("url", ""))))
        }
        return {
            **base,
            "ecosystem_fixed": _fixed(record, "ECOSYSTEM"),
            "verified_fix_commits": sorted(
                value
                for value in _fixed(record, "GIT")
                if value.lower() in reference_hashes
            ),
        }
    raise ProvenanceError("unsupported P66 cyber task family")


def replay(context: str, target_ids: Sequence[str], family: str) -> str:
    """Replay the finite target-ID program, returning UNKNOWN on missing evidence."""
    try:
        records = [json.loads(line) for line in context.splitlines() if line]
        if any(
            canonical_json(item) != line
            for item, line in zip(records, context.splitlines(), strict=True)
        ):
            raise ProvenanceError("noncanonical P66 cyber context")
        by_id = {str(item.get("id", "")): item for item in records}
        if (
            len(by_id) != len(records)
            or not target_ids
            or any(item not in by_id for item in target_ids)
        ):
            return "UNKNOWN"
        selected = [by_id[item] for item in target_ids]
        if family in {"lifecycle_matrix", "remediation_commit_join"}:
            result: object = [_row(item, family) for item in selected]
        elif family == "temporal_order":
            ordered = sorted(
                selected, key=lambda item: (item.get("modified", ""), item["id"])
            )
            result = {
                "ordered_ids": [item["id"] for item in ordered],
                "earliest_published": min(
                    selected, key=lambda item: (item.get("published", ""), item["id"])
                )["id"],
                "latest_modified": max(
                    selected, key=lambda item: (item.get("modified", ""), item["id"])
                )["id"],
            }
        else:
            raise ProvenanceError("unsupported P66 cyber task family")
        return canonical_json(result)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ProvenanceError):
        return "UNKNOWN"


def question(target_ids: Sequence[str], family: str) -> str:
    instructions = {
        "lifecycle_matrix": "Return lifecycle fields and exact fixed events for each target",
        "remediation_commit_join": "Join GIT fixed events to exact GitHub FIX-reference commits for each target",
        "temporal_order": "Order targets by modified time and identify published/modified extrema",
    }
    if family not in instructions:
        raise ProvenanceError("unsupported P66 cyber task family")
    schemas = {
        "lifecycle_matrix": "an array of objects with keys id, packages, aliases, published, modified, withdrawn, ecosystem_fixed, git_fixed",
        "remediation_commit_join": "an array of objects with keys id, packages, ecosystem_fixed, verified_fix_commits",
        "temporal_order": "an object with keys ordered_ids, earliest_published, latest_modified",
    }
    return (
        f"{instructions[family]}. Target advisory IDs: {canonical_json(list(target_ids))}. "
        "Use only the supplied canonical advisory records. Preserve target order except for the "
        f"explicit temporal ordering field. Return {schemas[family]} as canonical JSON, "
        "or UNKNOWN if any target is absent."
    )


def make_task(
    *,
    context: str,
    target_ids: Sequence[str],
    family: str,
    world_id: str,
    split: str,
    band: str,
    variant: int,
    source_binding: dict[str, Any],
) -> dict[str, Any]:
    answer = replay(context, target_ids, family)
    if answer == "UNKNOWN":
        raise ProvenanceError("P66 cyber task does not replay")
    task_id = f"{world_id}:{family}:v{variant:02d}"
    return {
        "schema_version": SCHEMA,
        "task_id": task_id,
        "world_id": world_id,
        "domain": "cyber",
        "split": split,
        "length_bucket": band,
        "family": family,
        "variant": variant,
        "question": question(target_ids, family),
        "context": context,
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "target_ids": list(target_ids),
        "answer": answer,
        "source_binding": source_binding,
        "real_source_derived": True,
        "strict_long_dependency_verified": False,
        "training_candidate": True,
        "production_eligible": False,
        "promoted": False,
    }


def remove_target(context: str, target_id: str) -> str:
    return "\n".join(
        line for line in context.splitlines() if json.loads(line).get("id") != target_id
    )


def audit_task(task: dict[str, Any]) -> dict[str, bool]:
    replayed = replay(task["context"], task["target_ids"], task["family"])
    removed = [
        replay(
            remove_target(task["context"], target), task["target_ids"], task["family"]
        )
        for target in task["target_ids"]
    ]
    records = task["context"].splitlines()
    last_packed = records[-1]
    compact = "\n".join(
        line for line in records if json.loads(line)["id"] in set(task["target_ids"])
    )
    return {
        "replay_exact": replayed == task["answer"],
        "remove_each_target_fails": all(value == "UNKNOWN" for value in removed),
        "single_record_insufficient": all(
            replay(line, task["target_ids"], task["family"]) == "UNKNOWN"
            for line in records
        ),
        "last_packed_record_insufficient": replay(
            last_packed, task["target_ids"], task["family"]
        )
        == "UNKNOWN",
        "compact_target_records_succeeds": replay(
            compact, task["target_ids"], task["family"]
        )
        == task["answer"],
        "non_release_boundary": task["strict_long_dependency_verified"] is False
        and task["production_eligible"] is False
        and task["promoted"] is False,
    }
