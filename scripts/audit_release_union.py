#!/usr/bin/env python3
"""Audit identity and byte bindings across promoted release directories.

This is a local cross-release identity check, not a replacement for independent
attestation or production-approval verification of each release receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

GATE_REVISION = "longworld-quality-gate-v6"
RECEIPT_NAMES = ("release_gate_pass.json", "release_gate_receipt.json")
SOURCE_FILES = ("quality_report.json", "train.jsonl", "eval.jsonl")


class ReleaseUnionError(ValueError):
    """Raised when one release or the combined release set is invalid."""


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseUnionError(f"{path}: invalid JSON") from error
    if not isinstance(value, dict):
        raise ReleaseUnionError(f"{path}: expected a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ReleaseUnionError(f"{path}: cannot read release rows") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReleaseUnionError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(value, dict):
            raise ReleaseUnionError(f"{path}:{line_number}: expected a JSON object")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ReleaseUnionError(f"{path}: cannot read bound release file") from error


def _integer_field(payload: dict[str, Any], field: str, path: Path) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReleaseUnionError(f"{path}: invalid {field}")
    return value


def _receipt_path(release: Path) -> Path:
    matches = [release / name for name in RECEIPT_NAMES if (release / name).is_file()]
    if len(matches) != 1:
        raise ReleaseUnionError(f"{release}: expected exactly one release gate receipt")
    return matches[0]


def _audit_release(release: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    release = release.resolve()
    if not release.is_dir():
        raise ReleaseUnionError(f"{release}: promoted release directory is missing")
    receipt_path = _receipt_path(release)
    receipt = _read_json_object(receipt_path)
    if (
        receipt.get("ok") is not True
        or receipt.get("errors") != []
        or receipt.get("gate_revision") != GATE_REVISION
    ):
        raise ReleaseUnionError(f"{receipt_path}: release gate v6 did not pass")

    source_hashes = receipt.get("source_file_sha256")
    if not isinstance(source_hashes, dict) or set(source_hashes) != set(SOURCE_FILES):
        raise ReleaseUnionError(f"{receipt_path}: source_file_sha256 is incomplete")
    for filename in SOURCE_FILES:
        source_path = release / filename
        expected = source_hashes.get(filename)
        if not isinstance(expected, str) or _sha256(source_path) != expected:
            raise ReleaseUnionError(f"{source_path}: source_file_sha256 mismatch")

    rows = [
        *_read_jsonl(release / "train.jsonl"),
        *_read_jsonl(release / "eval.jsonl"),
    ]
    worlds: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        for field in ("world_id", "content_hash"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ReleaseUnionError(
                    f"{release}: row {row_number} has missing {field}"
                )
        worlds.add(str(row["world_id"]))

    report_path = release / "quality_report.json"
    report = _read_json_object(report_path)
    expected_rows = len(rows)
    expected_worlds = len(worlds)
    for payload, path in ((receipt, receipt_path), (report, report_path)):
        if _integer_field(payload, "n_rows", path) != expected_rows:
            raise ReleaseUnionError(f"{path}: n_rows does not match release rows")
        if _integer_field(payload, "n_worlds", path) != expected_worlds:
            raise ReleaseUnionError(f"{path}: n_worlds does not match release rows")

    return (
        {
            "release": str(release),
            "receipt": str(receipt_path),
            "n_rows": expected_rows,
            "n_worlds": expected_worlds,
        },
        rows,
    )


def audit_release_union(release_dirs: list[Path]) -> dict[str, Any]:
    """Validate releases individually and reject identity collisions in their union."""
    if not release_dirs:
        raise ReleaseUnionError("at least one promoted release directory is required")

    releases: list[dict[str, Any]] = []
    world_owners: dict[str, Path] = {}
    content_owners: dict[str, Path] = {}
    row_count = 0
    for release_dir in release_dirs:
        release_path = release_dir.resolve()
        summary, rows = _audit_release(release_path)
        releases.append(summary)
        row_count += len(rows)
        for row in rows:
            world_id = str(row["world_id"])
            world_owner = world_owners.setdefault(world_id, release_path)
            if world_owner != release_path:
                raise ReleaseUnionError(
                    f"world_id {world_id!r} appears in multiple releases: "
                    f"{world_owner} and {release_path}"
                )
            content_hash = str(row["content_hash"])
            content_owner = content_owners.get(content_hash)
            if content_owner is not None:
                location = (
                    "multiple releases"
                    if content_owner != release_path
                    else "one release"
                )
                raise ReleaseUnionError(
                    f"content_hash {content_hash!r} is duplicated in {location}: "
                    f"{content_owner} and {release_path}"
                )
            content_owners[content_hash] = release_path

    return {
        "ok": True,
        "scope": "cross_release_identity_only",
        "gate_revision": GATE_REVISION,
        "n_releases": len(releases),
        "n_rows": row_count,
        "n_worlds": len(world_owners),
        "n_content_hashes": len(content_owners),
        "releases": releases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = audit_release_union(args.release_dirs)
    except ReleaseUnionError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
