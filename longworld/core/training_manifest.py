"""Signed binding from a validated release product to transformed training files."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from pathlib import Path
from typing import Any

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.record_contract import sft_row_errors
from longworld.core.release_profile import release_profile, release_profile_sha256

TRAINING_MANIFEST_SCHEMA = "longworld-training-export-v2"
TRAINING_MANIFEST_PURPOSE = "training_export_manifest"
LLAMAFACTORY_SHAREGPT_TRANSFORM_V4 = "longworld-llamafactory-sharegpt-v4"
LLAMAFACTORY_SHAREGPT_SYSTEM_V4 = (
    "You are a careful analyst of long internal records: contracts, lab notes, "
    "git objects, CI logs, and search snapshots. Use only the provided context. "
    "If the context is insufficient, reply exactly: unanswerable"
)
_SHAREGPT_CONDITION_VIEWS = {
    "B1": {"full"},
    "B3": {"full", "cf"},
    "B5": {"full", "cf", "ordered_artifact_view"},
    "B5w": {"full", "cf", "ordered_artifact_view"},
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _safe_relative_path(value: object, *, field: str) -> Path:
    raw = str(value or "")
    path = Path(raw)
    if (
        not raw
        or path.is_absolute()
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field} must be a normalized release-root relative path")
    return path


def _reject_symlink_path(path: Path, root: Path, *, field: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{field} is outside release root") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{field} contains a symlink path")


def training_manifest_release_root(
    manifest_path: Path, manifest: dict[str, Any]
) -> Path:
    """Derive the relocatable release root from the manifest's own relative path."""
    relative = _safe_relative_path(
        manifest.get("manifest_path"), field="training manifest path"
    )
    actual = manifest_path.absolute()
    root = actual
    for _part in relative.parts:
        root = root.parent
    expected = root.joinpath(relative)
    if expected != actual:
        raise ValueError("training manifest is not at its bound release path")
    _reject_symlink_path(actual, root, field="training manifest path")
    return root


def resolve_training_manifest_path(
    manifest_path: Path, manifest: dict[str, Any], relative_path: object
) -> Path:
    """Resolve one manifest-bound release path without permitting traversal."""
    root = training_manifest_release_root(manifest_path, manifest)
    relative = _safe_relative_path(relative_path, field="training manifest artifact")
    path = root.joinpath(relative)
    _reject_symlink_path(path, root, field="training manifest artifact")
    return path


def _iter_jsonl_objects(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"training transform source is invalid JSONL: {path.name}:"
                    f"{line_number}"
                ) from error
            if not isinstance(value, dict):
                raise TypeError("training transform source row must be an object")
            yield value


def _training_row_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    identity = tuple(
        str(row.get(field) or "")
        for field in ("world_id", "query_id", "dossier_id", "view")
    )
    if not all(identity):
        raise ValueError("training transform row identity is incomplete")
    return identity  # type: ignore[return-value]


def _distance_weight(row: dict[str, Any]) -> int:
    if row.get("view") not in {"full", "ordered_artifact_view", "trajectory", "cf"}:
        return 1
    difficulty = row.get("difficulty")
    if not isinstance(difficulty, dict):
        difficulty = {}
    distance = int(difficulty.get("max_evidence_distance") or 0)
    tokens = max(1, int(difficulty.get("context_tokens") or 1))
    return max(1, min(3, 1 + int(2 * distance / tokens)))


def _sharegpt_projection(row: dict[str, Any], *, sample_weight: int) -> dict[str, Any]:
    difficulty = row.get("difficulty")
    if not isinstance(difficulty, dict):
        difficulty = {}
    evidence_distance = difficulty.get("max_evidence_distance") or row.get(
        "evidence_distance", 0
    )
    return {
        "conversations": [
            {"from": "system", "value": LLAMAFACTORY_SHAREGPT_SYSTEM_V4},
            {"from": "human", "value": row["context"]},
            {"from": "gpt", "value": str(row["answer"])},
        ],
        "world_id": row["world_id"],
        "query_id": row["query_id"],
        "dossier_id": row.get("dossier_id"),
        "view": row["view"],
        "query_type": row["query_type"],
        "query_timing": row["query_timing"],
        "length_bucket": row.get("length_bucket"),
        "evidence_distance": evidence_distance,
        "dependency_class": row.get("dependency_class"),
        "sample_weight": sample_weight,
        "base_task_id": row.get("base_task_id"),
        "executable_proof_id": row.get("executable_proof_id"),
        "source_relation_id": row.get("source_relation_id"),
        "answer_program_id": row.get("answer_program_id"),
        "source_origins": row.get("source_origins", []),
        "workflow_kinds": row.get("workflow_kinds", []),
        "workflow_ids": row.get("workflow_ids", []),
        "evidence_roles": row.get("evidence_roles", []),
        "composition_method": row.get("composition_method"),
        "training_objective": row.get("training_objective"),
    }


def _condition_source_rows(
    rows: list[dict[str, Any]],
    *,
    condition: str,
    train_buckets: set[str],
) -> list[dict[str, Any]]:
    views = _SHAREGPT_CONDITION_VIEWS[condition]
    filtered = [
        row
        for row in rows
        if not sft_row_errors(row)
        and row.get("view") in views
        and row.get("length_bucket", "8k") in train_buckets
        and "decoy" not in str(row.get("query_id") or "")
        and not (
            row.get("length_bucket", "8k") in {"32k", "64k", "128k", "256k"}
            and row.get("dependency_class") == "local_or_mixed"
        )
        and not (condition in {"B1", "B3"} and row.get("query_timing") != "first")
    ]
    required = {(view, bucket) for view in views for bucket in train_buckets}
    seen: set[tuple[str, str]] = set()
    heads: list[dict[str, Any]] = []
    tails: list[dict[str, Any]] = []
    for row in filtered:
        key = (
            str(row.get("view") or ""),
            str(row.get("length_bucket") or ""),
        )
        if key in required and key not in seen:
            heads.append(row)
            seen.add(key)
        else:
            tails.append(row)
    missing = sorted(required - seen)
    if missing:
        raise ValueError(f"training transform source coverage is missing: {missing}")
    return [*heads, *tails]


def _source_content_digest(row: dict[str, Any]) -> str:
    digest = str(row.get("content_hash") or "")
    if digest:
        return digest
    return hashlib.sha256(
        json.dumps(
            {"context": row.get("context"), "answer": str(row.get("answer"))},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _token_estimate(row: dict[str, Any]) -> int:
    difficulty = row.get("difficulty")
    if not isinstance(difficulty, dict):
        raise TypeError("training transform source difficulty is malformed")
    return int(difficulty["context_tokens"]) + max(1, len(str(row["answer"])) // 4)


def _select_condition_rows(
    rows: list[dict[str, Any]],
    *,
    condition: str,
    token_cap: int | None,
) -> tuple[list[dict[str, Any]], int]:
    eligible: list[dict[str, Any]] = []
    seen_content: set[str] = set()
    for row in rows:
        digest = _source_content_digest(row)
        if digest in seen_content:
            continue
        seen_content.add(digest)
        eligible.append(row)

    units: list[list[dict[str, Any]]] = []
    if "cf" in _SHAREGPT_CONDITION_VIEWS[condition]:
        twins: dict[str, dict[str, dict[str, Any]]] = {}
        for row in eligible:
            view = str(row.get("view") or "")
            if view not in {"full", "cf"}:
                continue
            dossier = str(row.get("dossier_id") or "")
            if not dossier:
                raise ValueError("training transform twin has no dossier identity")
            twin_views = twins.setdefault(dossier, {})
            if view in twin_views:
                raise ValueError("training transform has a duplicate dossier twin")
            twin_views[view] = row
        if any(set(twin_views) != {"full", "cf"} for twin_views in twins.values()):
            raise ValueError("training transform has asymmetric dossier twins")
        emitted: set[str] = set()
        for row in eligible:
            view = str(row.get("view") or "")
            if view in {"full", "cf"}:
                dossier = str(row["dossier_id"])
                if dossier in emitted:
                    continue
                emitted.add(dossier)
                units.append([twins[dossier]["full"], twins[dossier]["cf"]])
            else:
                units.append([row])
    else:
        units = [[row] for row in eligible]

    selected: list[dict[str, Any]] = []
    used = 0
    weighted = condition == "B5w"
    for unit in units:
        unit_tokens = sum(
            _token_estimate(row) * (_distance_weight(row) if weighted else 1)
            for row in unit
        )
        if token_cap is not None and used + unit_tokens > token_cap:
            continue
        used += unit_tokens
        selected.extend(unit)
    if not selected:
        raise ValueError("training transform token cap selected no rows")
    return selected, used


def _expected_sharegpt_outputs(
    rows: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    profile = release_profile(str(manifest.get("release_profile_id") or ""))
    conditions = tuple(profile.training_conditions)
    train_buckets = set(profile.training_length_buckets)
    shuffled = list(rows)
    random.Random(profile.training_export_seed).shuffle(shuffled)
    prepared = {
        condition: _condition_source_rows(
            shuffled,
            condition=condition,
            train_buckets=train_buckets,
        )
        for condition in conditions
    }
    first_pass = {
        condition: _select_condition_rows(
            condition_rows,
            condition=condition,
            token_cap=None,
        )[1]
        for condition, condition_rows in prepared.items()
    }
    token_cap = min(first_pass.values()) if len(first_pass) > 1 else None
    expected: dict[str, list[dict[str, Any]]] = {}
    for condition, condition_rows in prepared.items():
        selected, _used = _select_condition_rows(
            condition_rows,
            condition=condition,
            token_cap=token_cap,
        )
        transformed = [
            _sharegpt_projection(
                row,
                sample_weight=(_distance_weight(row) if condition == "B5w" else 1),
            )
            for row in selected
        ]
        if condition != "B5w":
            expected[f"{condition}.json"] = transformed
            continue
        groups: dict[int, list[dict[str, Any]]] = {}
        for row in transformed:
            groups.setdefault(int(row["sample_weight"]), []).append(row)
        if len(groups) == 1:
            expected["B5w.json"] = transformed
        else:
            for weight, group in sorted(groups.items()):
                expected[f"B5w.weight{weight}.json"] = group
    return expected


def validate_deterministic_training_transform(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    required: bool = True,
    require_production_trust: bool = False,
) -> int:
    """Require every exported training row to be an exact promoted-row projection."""
    transform_revision = str(manifest.get("transform_revision") or "")
    if transform_revision != LLAMAFACTORY_SHAREGPT_TRANSFORM_V4:
        if required:
            raise ValueError(
                "production training transform has no executable deterministic validator"
            )
        return 0
    source_dir = resolve_training_manifest_path(
        manifest_path, manifest, manifest.get("source_data_dir")
    )
    source_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in _iter_jsonl_objects(source_dir / "train.jsonl"):
        if row.get("data_stage") != "train_ready" or row.get("split") != "train":
            raise ValueError(
                "training transform source is not promoted train-ready data"
            )
        if require_production_trust and any(
            row.get(field) != expected
            for field, expected in {
                "trust_scope": "production",
                "diagnostic_only": False,
                "content_gate_eligible": True,
                "trust_valid_for_production": True,
                "production_eligible": True,
            }.items()
        ):
            raise ValueError(
                "training transform source lacks explicit production trust"
            )
        identity = _training_row_identity(row)
        if identity in source_rows:
            raise ValueError("training transform source identity is duplicated")
        source_rows[identity] = row
    if not source_rows:
        raise ValueError("training transform source has no promoted training rows")

    outputs: dict[str, Path] = {}
    for entry in manifest.get("outputs") or []:
        if not isinstance(entry, dict):
            raise TypeError("training manifest output entry is malformed")
        path = resolve_training_manifest_path(
            manifest_path, manifest, entry.get("path")
        )
        name = path.name
        condition = name.removesuffix(".json")
        if name.startswith("B5w.weight") and name.endswith(".json"):
            condition = "B5w"
        if condition not in _SHAREGPT_CONDITION_VIEWS:
            continue
        if name in outputs:
            raise ValueError("training transform has ambiguous data output names")
        outputs[name] = path

    expected_outputs = _expected_sharegpt_outputs(list(source_rows.values()), manifest)
    if set(outputs) != set(expected_outputs):
        raise ValueError("training transform output set is incomplete or unexpected")
    verified_rows = 0
    for name, expected_rows in sorted(expected_outputs.items()):
        path = outputs[name]
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"training data output is invalid JSON: {name}") from error
        if not isinstance(rows, list) or rows != expected_rows:
            raise ValueError(
                f"training data output differs from deterministic transform: {name}"
            )
        verified_rows += len(rows)
    return verified_rows


def create_training_manifest(
    manifest_path: Path,
    *,
    source_data_dir: Path,
    release_profile_id: str,
    transform_revision: str,
    output_paths: list[Path],
    source_file_sha256: dict[str, str],
    attestation_key: bytes | None,
    upstream_manifest_path: Path | None = None,
    release_root: Path | None = None,
) -> dict[str, Any]:
    """Sign the source quality report and exact bytes of every derived output."""
    if attestation_key is None:
        raise ValueError("report attestation key is required")
    if not release_profile_id or not transform_revision:
        raise ValueError("release profile and transform revision are required")
    manifest_path = manifest_path.absolute()
    source_data_dir = source_data_dir.absolute()
    output_paths = [path.absolute() for path in output_paths]
    if release_root is None:
        release_root = Path(
            os.path.commonpath([manifest_path, source_data_dir, *output_paths])
        )
    release_root = release_root.absolute()
    if release_root.is_symlink():
        raise ValueError("release root cannot be a symlink")
    try:
        manifest_relative = manifest_path.relative_to(release_root).as_posix()
        source_relative = source_data_dir.relative_to(release_root).as_posix()
    except ValueError as error:
        raise ValueError(
            "training manifest inputs must be inside release root"
        ) from error
    _safe_relative_path(manifest_relative, field="training manifest path")
    _safe_relative_path(source_relative, field="source data directory")
    _reject_symlink_path(manifest_path, release_root, field="training manifest path")
    _reject_symlink_path(source_data_dir, release_root, field="source data directory")
    required_source_files = ("quality_report.json", "train.jsonl", "eval.jsonl")
    if set(source_file_sha256) != set(required_source_files):
        raise ValueError("training manifest source file binding is incomplete")
    for name in required_source_files:
        path = source_data_dir / name
        if not path.is_file() or file_sha256(path) != source_file_sha256[name]:
            raise ValueError(f"validated source release changed: {name}")
    output_root = manifest_path.parent
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in output_paths:
        path = raw_path.absolute()
        _reject_symlink_path(path, release_root, field="training output")
        if not path.is_file() or not path.is_relative_to(output_root):
            raise ValueError(
                f"training output is missing or outside export root: {path}"
            )
        relative = path.relative_to(release_root).as_posix()
        if relative in seen or path == manifest_path:
            raise ValueError(f"duplicate or recursive training output: {relative}")
        seen.add(relative)
        entries.append(
            {
                "path": relative,
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    if not entries:
        raise ValueError("training manifest has no outputs")
    payload = {
        "schema_version": TRAINING_MANIFEST_SCHEMA,
        "release_profile_id": release_profile_id,
        "release_profile_sha256": release_profile_sha256(release_profile_id),
        "transform_revision": transform_revision,
        "manifest_path": manifest_relative,
        "source_data_dir": source_relative,
        "source_file_sha256": dict(sorted(source_file_sha256.items())),
        "outputs": sorted(entries, key=lambda item: item["path"]),
    }
    if upstream_manifest_path is not None:
        upstream_manifest_path = upstream_manifest_path.absolute()
        if not upstream_manifest_path.is_file():
            raise ValueError("upstream training manifest is missing")
        _reject_symlink_path(
            upstream_manifest_path, release_root, field="upstream training manifest"
        )
        payload["upstream_manifest"] = {
            "path": upstream_manifest_path.relative_to(release_root).as_posix(),
            "sha256": file_sha256(upstream_manifest_path),
        }
    manifest = attach_attestation(
        payload, attestation_key, purpose=TRAINING_MANIFEST_PURPOSE
    )
    _write_json_atomic(manifest_path, manifest)
    return manifest


def validate_training_manifest(
    manifest_path: Path,
    *,
    expected_release_profile_id: str,
    expected_transform_revision: str,
    attestation_key: bytes | None,
) -> dict[str, Any]:
    """Fail closed if the source report or any transformed output changed."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid training manifest: {manifest_path}") from error
    if not isinstance(manifest, dict) or not verify_attestation(
        manifest, attestation_key, purpose=TRAINING_MANIFEST_PURPOSE
    ):
        raise ValueError("training manifest attestation is invalid")
    if (
        manifest.get("schema_version") != TRAINING_MANIFEST_SCHEMA
        or manifest.get("release_profile_id") != expected_release_profile_id
        or manifest.get("release_profile_sha256")
        != release_profile_sha256(expected_release_profile_id)
        or manifest.get("transform_revision") != expected_transform_revision
    ):
        raise ValueError("training manifest release identity is invalid")
    release_root = training_manifest_release_root(manifest_path, manifest)
    upstream = manifest.get("upstream_manifest")
    if upstream is not None:
        if not isinstance(upstream, dict):
            raise TypeError("upstream training manifest binding is malformed")
        upstream_path = resolve_training_manifest_path(
            manifest_path, manifest, upstream.get("path")
        )
        if not upstream_path.is_file() or file_sha256(upstream_path) != upstream.get(
            "sha256"
        ):
            raise ValueError("upstream training manifest digest changed")
    source_dir = resolve_training_manifest_path(
        manifest_path, manifest, manifest.get("source_data_dir")
    )
    source_digests = manifest.get("source_file_sha256")
    if not isinstance(source_digests, dict) or set(source_digests) != {
        "quality_report.json",
        "train.jsonl",
        "eval.jsonl",
    }:
        raise ValueError("source release digests are missing")
    for name, expected_digest in source_digests.items():
        source_path = source_dir / name
        if not source_path.is_file() or file_sha256(source_path) != expected_digest:
            raise ValueError(f"source release digest changed: {name}")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("training manifest outputs are missing")
    seen: set[str] = set()
    for entry in outputs:
        if not isinstance(entry, dict):
            raise TypeError("training manifest output entry is malformed")
        relative = str(entry.get("path") or "")
        path = resolve_training_manifest_path(manifest_path, manifest, relative)
        if (
            not relative
            or relative in seen
            or not path.is_relative_to(release_root)
            or not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_sha256(path) != entry.get("sha256")
        ):
            raise ValueError(f"training output digest changed: {relative or '?'}")
        seen.add(relative)
    return manifest
