#!/usr/bin/env python3
"""Report exercised semantic coverage from an exact local release inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.promotion import RELEASE_GATE_PURPOSE
from scripts.audit_release_union import (
    LOCAL_RELEASE_INVENTORY_SCHEMA,
    ReleaseUnionError,
    audit_release_union,
)


class SemanticCoverageError(ValueError):
    """The inventory or one of its bound promoted files is inconsistent."""


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SemanticCoverageError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise SemanticCoverageError(f"{label} must be a JSON object")
    return value


def _bound_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise SemanticCoverageError("promoted file path is missing")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise SemanticCoverageError("promoted file path is unsafe")
    root = root.resolve(strict=True)
    try:
        path = (root / Path(*relative.parts)).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as error:
        raise SemanticCoverageError(
            "promoted file path escapes the workspace"
        ) from error
    if not path.is_file():
        raise SemanticCoverageError("promoted file is not a regular file")
    return path


def _replay_bound_inventory(
    inventory: dict[str, Any], *, workspace_root: Path
) -> dict[str, Any]:
    if (
        inventory.get("schema_version") != LOCAL_RELEASE_INVENTORY_SCHEMA
        or inventory.get("inventory_integrity_ok") is not True
        or inventory.get("trust_mode") != "local_engineering"
        or inventory.get("production_eligible") is not False
    ):
        raise SemanticCoverageError("release inventory identity is invalid")
    releases = inventory.get("releases")
    if not isinstance(releases, list) or not releases:
        raise SemanticCoverageError("inventory contains no releases")
    release_dirs: list[Path] = []
    root = workspace_root.resolve(strict=True)
    for release in releases:
        relative_value = release.get("release") if isinstance(release, dict) else None
        if not isinstance(relative_value, str) or not relative_value:
            raise SemanticCoverageError("release directory binding is missing")
        relative = PurePosixPath(relative_value)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise SemanticCoverageError("release directory binding is unsafe")
        try:
            release_dir = (root / Path(*relative.parts)).resolve(strict=True)
            release_dir.relative_to(root)
        except (OSError, ValueError) as error:
            raise SemanticCoverageError(
                "release directory binding escapes the workspace"
            ) from error
        if not release_dir.is_dir() or release_dir.is_symlink():
            raise SemanticCoverageError("release directory binding is invalid")
        release_dirs.append(release_dir)
    target_profile = inventory.get("target_release_profile_id")
    if not isinstance(target_profile, str) or not target_profile:
        raise SemanticCoverageError("target release profile is missing")
    try:
        return audit_release_union(
            release_dirs,
            root=root,
            target_release_profile_id=target_profile,
            gate_attestation_key=attestation_key_from_env(RELEASE_GATE_PURPOSE),
            inventory_attestation_key=attestation_key_from_env("release_inventory"),
        )
    except (ReleaseUnionError, ValueError) as error:
        raise SemanticCoverageError(
            f"signed release-union replay failed: {error}"
        ) from error


def _load_bound_rows(
    inventory: dict[str, Any], *, workspace_root: Path
) -> list[dict[str, Any]]:
    releases = inventory.get("releases")
    if not isinstance(releases, list) or not releases:
        raise SemanticCoverageError("inventory contains no releases")
    rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for release in releases:
        files = release.get("files") if isinstance(release, dict) else None
        if not isinstance(files, list):
            raise SemanticCoverageError("release file list is invalid")
        for entry in files:
            if not isinstance(entry, dict):
                raise SemanticCoverageError("release file binding is invalid")
            relative = str(entry.get("path") or "")
            if not relative.endswith(("/train.jsonl", "/eval.jsonl")):
                continue
            if entry.get("role") != "promoted_data":
                raise SemanticCoverageError(
                    "promoted JSONL binding must have role promoted_data"
                )
            if relative in seen_paths:
                raise SemanticCoverageError("promoted JSONL path is duplicated")
            path = _bound_path(workspace_root, relative)
            raw = path.read_bytes()
            if entry.get("bytes") != len(raw):
                raise SemanticCoverageError(
                    f"promoted file byte count drift: {relative}"
                )
            if entry.get("sha256") != hashlib.sha256(raw).hexdigest():
                raise SemanticCoverageError(f"promoted file digest drift: {relative}")
            for line_number, line in enumerate(raw.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise SemanticCoverageError(
                        f"invalid promoted JSONL row: {relative}:{line_number}"
                    ) from error
                if not isinstance(row, dict):
                    raise SemanticCoverageError("promoted row must be a JSON object")
                rows.append(row)
            seen_paths.add(relative)
    return rows


def _relation_kinds(rows: list[dict[str, Any]], *, provenance: str) -> set[str]:
    kinds: set[str] = set()
    for row in rows:
        edges = row.get("source_relation_edges") or []
        if not isinstance(edges, list):
            raise SemanticCoverageError("source relation edges must be a list")
        for edge in edges:
            if not isinstance(edge, dict):
                raise SemanticCoverageError("source relation edge must be an object")
            if edge.get("relation_provenance") == provenance and edge.get("relation"):
                kinds.add(str(edge["relation"]))
    return kinds


def _declared_int(inventory: dict[str, Any], field: str) -> int | None:
    value = inventory.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SemanticCoverageError(f"inventory {field} is invalid")
    return value


def audit_semantic_coverage(
    inventory_path: Path, *, workspace_root: Path
) -> dict[str, Any]:
    """Recompute exercised coverage without treating template capacity as data."""
    inventory = _load_object(inventory_path, label="release inventory")
    replayed = _replay_bound_inventory(inventory, workspace_root=workspace_root)
    if replayed != inventory:
        raise SemanticCoverageError(
            "inventory differs from signed release-union replay"
        )
    rows = _load_bound_rows(inventory, workspace_root=workspace_root)
    worlds = {str(row.get("world_id") or "") for row in rows}
    content_hashes = {str(row.get("content_hash") or "") for row in rows}
    if "" in worlds or "" in content_hashes:
        raise SemanticCoverageError("promoted row identity is missing")
    token_total = 0
    for row in rows:
        token_count = row.get("actual_context_tokens")
        difficulty = row.get("difficulty")
        if (
            isinstance(token_count, bool)
            or not isinstance(token_count, int)
            or row.get("tokenizer_context_tokens") != token_count
            or not isinstance(difficulty, dict)
            or difficulty.get("context_tokens") != token_count
        ):
            raise SemanticCoverageError("promoted row exact token metadata is invalid")
        token_total += token_count

    checks = {
        "n_rows": len(rows),
        "n_worlds": len(worlds),
        "n_content_hashes": len(content_hashes),
        "n_reported_context_tokens": token_total,
    }
    labels = {
        "n_rows": "row count",
        "n_worlds": "world count",
        "n_content_hashes": "content hash count",
        "n_reported_context_tokens": "context token count",
    }
    for field, observed in checks.items():
        declared = _declared_int(inventory, field)
        if declared is not None and declared != observed:
            raise SemanticCoverageError(
                f"inventory {labels[field]} drift: declared={declared}, observed={observed}"
            )

    program_ops: set[str] = set()
    for row in rows:
        operations = row.get("program_ops") or []
        if not isinstance(operations, list):
            raise SemanticCoverageError("program ops must be a list")
        for operation in operations:
            if not isinstance(operation, dict) or not operation.get("op"):
                raise SemanticCoverageError("program op is invalid")
            program_ops.add(str(operation["op"]))

    bucket_counts = Counter(str(row.get("length_bucket") or "") for row in rows)
    if "" in bucket_counts:
        raise SemanticCoverageError("promoted row length bucket is missing")
    authentic = _relation_kinds(rows, provenance="authentic_source")
    for row in rows:
        source_edges = row.get("source_relation_edges") or []
        declared_edges = row.get("authentic_source_relation_edges") or []
        if not isinstance(source_edges, list) or not isinstance(declared_edges, list):
            raise SemanticCoverageError(
                "authentic source relation edges must be a list"
            )
        canonical = [
            edge
            for edge in source_edges
            if isinstance(edge, dict)
            and edge.get("relation_provenance") == "authentic_source"
        ]
        if declared_edges != canonical:
            raise SemanticCoverageError(
                "authentic relation alias differs from canonical source edges"
            )

    def values(field: str) -> list[str]:
        return sorted({str(row[field]) for row in rows if row.get(field)})

    real_source_families = sorted(
        {
            str(family)
            for row in rows
            for family in (row.get("real_source_family_ids") or [])
        }
    )
    return {
        "schema_version": "longworld-semantic-coverage-report-v1",
        "inventory": inventory_path.as_posix(),
        "n_rows": len(rows),
        "n_worlds": len(worlds),
        "n_content_hashes": len(content_hashes),
        "n_exact_context_tokens": token_total,
        "length_bucket_counts": dict(sorted(bucket_counts.items())),
        "domains": values("domain"),
        "query_types": values("query_type"),
        "motifs": values("motif"),
        "n_answer_programs": len(values("answer_program_id")),
        "n_executable_proofs": len(values("executable_proof_id")),
        "n_program_ops": len(program_ops),
        "program_ops": sorted(program_ops),
        "authentic_source_relation_kinds": sorted(authentic),
        "synthetic_executable_relation_kinds": sorted(
            _relation_kinds(rows, provenance="synthetic_executable")
        ),
        "real_source_families": real_source_families,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report = audit_semantic_coverage(args.inventory, workspace_root=args.workspace_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
