"""Deterministic, group-atomic unseen evaluation splits."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from longworld.core.attestation import attach_attestation, verify_attestation
from longworld.core.production_trust import (
    verify_embedded_production_approval_from_env,
)
from longworld.core.release_profile import release_profile, release_profile_sha256

UNSEEN_AXES = (
    "world_entity",
    "topology_operator",
    "source_document_family",
    "domain_composition",
)
UNSEEN_SPLIT_SCHEMA = "longworld-unseen-splits-v2"
UNSEEN_SPLIT_ATTESTATION_PURPOSE = "training_export_manifest"
UNSEEN_TRUST_MODES = ("local_engineering", "production")
_RELEASE_GATE_SCHEMA = "longworld-release-gate-pass-v1"
_RELEASE_GATE_REVISION = "longworld-quality-gate-v2"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def split_group_key(row: dict[str, Any], axis: str) -> str:
    """Return the semantic unit that must not cross an unseen split."""
    if axis == "world_entity":
        key = str(row.get("world_id") or "")
    elif axis == "topology_operator":
        topology = str(row.get("canonical_topology") or "")
        ops = row.get("program_ops") or []
        query_type = str(row.get("query_type") or "")
        operator_identity = ops or (
            [{"op": f"QUERY:{query_type}"}] if query_type else []
        )
        key = (
            f"{topology}|{_canonical_json(operator_identity)}"
            if topology and operator_identity
            else ""
        )
    elif axis == "source_document_family":
        if row.get("real_source_verified") is not True:
            raise ValueError("row has no verified real source family")
        families = sorted(
            {
                str(item).strip().lower()
                for item in row.get("source_family_ids") or []
                if str(item).strip()
            }
        )
        key = "+".join(families)
    elif axis == "domain_composition":
        domains = sorted(
            {
                str(item).strip().lower()
                for item in row.get("composition_domains") or []
                if str(item).strip()
            }
        )
        motif = str(row.get("motif") or "")
        key = "+".join(domains) + f"|{motif}" if len(domains) >= 2 and motif else ""
    else:
        raise ValueError(f"unsupported unseen axis: {axis}")
    if not key:
        raise ValueError(f"row lacks a group key for unseen axis {axis}")
    return key


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("world_id") or ""),
        str(row.get("base_task_id") or row.get("query_id") or ""),
        str(row.get("view") or ""),
        _sha256_bytes(_canonical_json(row).encode()),
    )


def _serialized_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    ordered = sorted(rows, key=_row_sort_key)
    return b"".join((_canonical_json(row) + "\n").encode("utf-8") for row in ordered)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        staged_path = Path(staged.name)
        staged.write(payload)
        staged.flush()
    staged_path.replace(path)


def _invalidate_split_outputs(output_dir: Path, axes: Iterable[str]) -> None:
    (output_dir / "manifest.json").unlink(missing_ok=True)
    for axis in axes:
        for name in ("train.jsonl", "eval.jsonl"):
            (output_dir / axis / name).unlink(missing_ok=True)


def _has_production_attestation(
    value: dict[str, Any], key: bytes | None, *, purpose: str, role: str
) -> bool:
    identity = value.get("attestation")
    return (
        isinstance(identity, dict)
        and identity.get("scheme") == "hmac-sha256-v2"
        and identity.get("purpose") == purpose
        and identity.get("role") == role
        and identity.get("environment") == "production"
        and bool(str(identity.get("key_id") or ""))
        and verify_attestation(value, key, purpose=purpose)
    )


def _validated_release_binding(
    release_manifest: object,
    *,
    n_rows: int,
    source_file_sha256: object,
    attestation_key: bytes | None,
) -> dict[str, Any]:
    if not isinstance(release_manifest, dict) or not _has_production_attestation(
        release_manifest,
        attestation_key,
        purpose="release_gate_pass",
        role="auditor",
    ):
        raise ValueError("production unseen release attestation is invalid")
    release_sources = release_manifest.get("source_file_sha256")
    if (
        release_manifest.get("schema_version") != _RELEASE_GATE_SCHEMA
        or release_manifest.get("gate_revision") != _RELEASE_GATE_REVISION
        or release_manifest.get("ok") is not True
        or release_manifest.get("errors") != []
        or release_manifest.get("n_rows") != n_rows
        or _SHA256.fullmatch(str(release_manifest.get("release_profile_sha256") or ""))
        is None
        or not str(release_manifest.get("release_profile_id") or "")
        or not isinstance(release_sources, dict)
        or set(release_sources) != {"quality_report.json", "train.jsonl", "eval.jsonl"}
        or any(
            _SHA256.fullmatch(str(digest or "")) is None
            for digest in release_sources.values()
        )
        or release_manifest.get("quality_report_sha256")
        != release_sources.get("quality_report.json")
    ):
        raise ValueError("production unseen release manifest is invalid")
    release_profile_id = str(release_manifest["release_profile_id"])
    profile = release_profile(release_profile_id)
    if profile.environment != "production" or release_manifest.get(
        "release_profile_sha256"
    ) != release_profile_sha256(release_profile_id):
        raise ValueError("production unseen release profile binding is invalid")
    expected_sources = {
        name: release_sources[name] for name in ("train.jsonl", "eval.jsonl")
    }
    if source_file_sha256 != expected_sources:
        raise ValueError("production unseen release source binding is invalid")
    try:
        verify_embedded_production_approval_from_env(
            release_manifest.get("production_approval"),
            release_profile_id=release_profile_id,
            release_profile_sha256=release_profile_sha256(release_profile_id),
            source_file_sha256=release_sources,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("production unseen production approval is invalid") from error
    return {
        "purpose": "release_gate_pass",
        "manifest_sha256": _sha256_bytes(
            _canonical_json(release_manifest).encode("utf-8")
        ),
        "release_profile_id": release_manifest["release_profile_id"],
        "release_profile_sha256": release_manifest["release_profile_sha256"],
        "source_file_sha256": dict(sorted(release_sources.items())),
    }


def _validate_split_outputs(payload: dict[str, Any], manifest_path: Path) -> None:
    axes = payload.get("axes")
    if not isinstance(axes, dict) or not axes or set(axes).difference(UNSEEN_AXES):
        raise ValueError("unseen split manifest axes are invalid")
    for axis, details in axes.items():
        if not isinstance(details, dict):
            raise TypeError("unseen split manifest axis is invalid")
        axis_dir = manifest_path.parent / axis
        status = details.get("status")
        if status == "blocked":
            if any(
                (axis_dir / name).exists() for name in ("train.jsonl", "eval.jsonl")
            ):
                raise ValueError("blocked unseen split has output files")
            continue
        if status != "ready":
            raise ValueError("unseen split manifest status is invalid")
        for prefix in ("train", "eval"):
            output = axis_dir / f"{prefix}.jsonl"
            try:
                raw = output.read_bytes()
            except OSError as error:
                raise ValueError("unseen split output is missing") from error
            if _sha256_bytes(raw) != details.get(f"{prefix}_sha256"):
                raise ValueError("unseen split output digest is invalid")


def _dossier_group_errors(rows: list[dict[str, Any]], axis: str) -> list[str]:
    if axis in {"source_document_family", "domain_composition"}:
        return []
    groups_by_dossier: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        dossier = str(row.get("dossier_id") or "")
        if dossier:
            try:
                groups_by_dossier[dossier].add(split_group_key(row, axis))
            except ValueError:
                if axis != "source_document_family":
                    raise
    return sorted(
        dossier for dossier, groups in groups_by_dossier.items() if len(groups) != 1
    )


def _component_groups(
    tokens_by_dossier: dict[str, set[str]],
) -> dict[str, str]:
    """Collapse co-occurring semantic tokens into leakage-safe components."""
    parent: dict[str, str] = {}

    def find(family: str) -> str:
        parent.setdefault(family, family)
        if parent[family] != family:
            parent[family] = find(parent[family])
        return parent[family]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for tokens in tokens_by_dossier.values():
        ordered = sorted(tokens)
        for token in ordered:
            find(token)
        for token in ordered[1:]:
            union(ordered[0], token)
    components: defaultdict[str, set[str]] = defaultdict(set)
    for family in parent:
        components[find(family)].add(family)
    component_key = {
        family: "+".join(sorted(components[find(family)])) for family in parent
    }
    return {
        dossier: component_key[min(tokens)]
        for dossier, tokens in tokens_by_dossier.items()
        if tokens
    }


def _component_groups_by_dossier(
    rows: list[dict[str, Any]], axis: str
) -> dict[str, str]:
    tokens_by_dossier: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        dossier = str(row.get("dossier_id") or "")
        if not dossier:
            continue
        if axis == "world_entity":
            world_id = str(row.get("world_id") or "")
            if world_id:
                tokens_by_dossier[dossier].add(f"world:{world_id}")
            tokens_by_dossier[dossier].update(
                f"entity:{item}"
                for item in row.get("workflow_ids") or []
                if str(item).strip()
            )
        elif axis == "topology_operator":
            topology = str(row.get("canonical_topology") or "")
            if topology:
                tokens_by_dossier[dossier].add(f"topology:{topology}")
            ops = row.get("program_ops") or []
            if ops:
                tokens_by_dossier[dossier].update(
                    f"operator:{_canonical_json(op)}" for op in ops
                )
            elif row.get("query_type"):
                tokens_by_dossier[dossier].add(f"operator:QUERY:{row['query_type']}")
        elif axis == "source_document_family":
            if row.get("real_source_verified") is not True:
                continue
            tokens_by_dossier[dossier].update(
                f"source:{str(item).strip().lower()}"
                for item in row.get("source_family_ids") or []
                if str(item).strip()
            )
        elif axis == "domain_composition":
            domains = {
                str(item).strip().lower()
                for item in row.get("composition_domains") or []
                if str(item).strip()
            }
            if len(domains) < 2:
                continue
            tokens_by_dossier[dossier].update(f"domain:{item}" for item in domains)
            motif = str(row.get("motif") or "")
            if motif:
                tokens_by_dossier[dossier].add(f"motif:{motif}")
    return _component_groups(tokens_by_dossier)


def _world_entity_coverage(rows: list[dict[str, Any]]) -> str:
    worlds_by_entity: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("real_source_verified") is not True:
            continue
        world_id = str(row.get("world_id") or "")
        for value in row.get("workflow_ids") or []:
            entity_id = str(value).strip()
            if entity_id and entity_id != world_id:
                worlds_by_entity[entity_id].add(world_id)
    if any(len(worlds) > 1 for worlds in worlds_by_entity.values()):
        return "world_entity_atomic"
    return "world_atomic_only"


def build_unseen_splits(
    rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    axes: Iterable[str] = UNSEEN_AXES,
    eval_ratio: float = 0.2,
    split_seed: int = 0,
    trust_mode: str = "local_engineering",
    source_file_sha256: dict[str, str] | None = None,
    release_manifest: dict[str, Any] | None = None,
    promotion_attestation_key: bytes | None = None,
    release_attestation_key: bytes | None = None,
    manifest_attestation_key: bytes | None = None,
) -> dict[str, dict[str, Any]]:
    """Write one train/eval complement per unseen axis and return its manifest.

    Selection is by complete semantic group, not row. Hash ordering makes the
    result independent of input order while an exact group count guarantees a
    non-empty evaluation when at least two groups exist.
    """
    if not rows:
        raise ValueError("cannot build unseen splits from no rows")
    if not 0 < eval_ratio < 1:
        raise ValueError("eval_ratio must be between zero and one")
    if trust_mode not in UNSEEN_TRUST_MODES:
        raise ValueError("unseen split trust mode is invalid")
    selected_axes = list(axes)
    if len(selected_axes) != len(set(selected_axes)):
        raise ValueError("unseen axes must be unique")
    unknown = sorted(set(selected_axes).difference(UNSEEN_AXES))
    if unknown:
        raise ValueError(f"unsupported unseen axes: {unknown}")
    _invalidate_split_outputs(output_dir, selected_axes)
    for row in rows:
        dossier_id = row.get("dossier_id")
        if not isinstance(dossier_id, str) or not dossier_id:
            raise ValueError(
                "unseen split requires dossier_id to be a canonical string"
            )
        if dossier_id != dossier_id.strip():
            raise ValueError(
                "unseen split requires dossier_id to be a canonical string"
            )

    release_binding: dict[str, Any] | None = None
    if trust_mode == "production":
        for index, row in enumerate(rows):
            if not _has_production_attestation(
                row,
                promotion_attestation_key,
                purpose="sft_row",
                role="promotion",
            ):
                raise ValueError(
                    f"production unseen row attestation is invalid: {index}"
                )
        release_binding = _validated_release_binding(
            release_manifest,
            n_rows=len(rows),
            source_file_sha256=source_file_sha256,
            attestation_key=release_attestation_key,
        )
    elif any(
        value is not None
        for value in (
            source_file_sha256,
            release_manifest,
            promotion_attestation_key,
            release_attestation_key,
            manifest_attestation_key,
        )
    ):
        raise ValueError(
            "local engineering split cannot carry production trust material"
        )

    input_payload = _serialized_jsonl(rows)
    manifest: dict[str, dict[str, Any]] = {}
    for axis in selected_axes:
        dossier_errors = _dossier_group_errors(rows, axis)
        if dossier_errors:
            raise ValueError(
                f"dossier spans unseen groups for {axis}: {dossier_errors[:3]}"
            )
        if (
            trust_mode == "production"
            and axis == "world_entity"
            and _world_entity_coverage(rows) == "world_atomic_only"
        ):
            axis_dir = output_dir / axis
            for name in ("train.jsonl", "eval.jsonl"):
                (axis_dir / name).unlink(missing_ok=True)
            manifest[axis] = {
                "status": "blocked",
                "reason": "world_atomic_only",
                "coverage": "world_atomic_only",
                "n_rows": len(rows),
            }
            continue
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        ungrouped_rows: list[dict[str, Any]] = []
        component_dossier_groups = _component_groups_by_dossier(rows, axis)
        for row in rows:
            try:
                dossier = str(row.get("dossier_id") or "")
                group = component_dossier_groups.get(dossier) or split_group_key(
                    row, axis
                )
                grouped[group].append(row)
            except ValueError:
                if axis not in {"source_document_family", "domain_composition"}:
                    raise
                ungrouped_rows.append(row)
        if len(grouped) < 2:
            axis_dir = output_dir / axis
            for name in ("train.jsonl", "eval.jsonl"):
                (axis_dir / name).unlink(missing_ok=True)
            manifest[axis] = {
                "status": "blocked",
                "reason": "fewer_than_two_groups",
                "n_groups": len(grouped),
                "n_rows": len(rows),
                "n_rows_without_group": len(ungrouped_rows),
            }
            continue

        ranked_groups = sorted(
            grouped,
            key=lambda key: _sha256_bytes(f"{split_seed}|{axis}|{key}".encode()),
        )
        n_eval = min(
            len(ranked_groups) - 1,
            max(1, math.ceil(len(ranked_groups) * eval_ratio)),
        )
        eval_groups = set(ranked_groups[:n_eval])
        train_rows: list[dict[str, Any]] = []
        eval_rows: list[dict[str, Any]] = []
        for group, members in grouped.items():
            (eval_rows if group in eval_groups else train_rows).extend(members)
        if axis == "source_document_family":
            train_rows.extend(ungrouped_rows)

        train_payload = _serialized_jsonl(train_rows)
        eval_payload = _serialized_jsonl(eval_rows)
        axis_dir = output_dir / axis
        _write_atomic(axis_dir / "train.jsonl", train_payload)
        _write_atomic(axis_dir / "eval.jsonl", eval_payload)
        manifest[axis] = {
            "status": "ready",
            "n_groups": len(grouped),
            "n_train_groups": len(grouped) - n_eval,
            "n_eval_groups": n_eval,
            "n_train_rows": len(train_rows),
            "n_eval_rows": len(eval_rows),
            "n_rows_without_group": len(ungrouped_rows),
            "train_sha256": _sha256_bytes(train_payload),
            "eval_sha256": _sha256_bytes(eval_payload),
            "eval_group_sha256": _sha256_bytes(
                _canonical_json(sorted(eval_groups)).encode("utf-8")
            ),
        }
        if axis == "world_entity":
            manifest[axis]["coverage"] = _world_entity_coverage(rows)

    manifest_payload = {
        "schema_version": UNSEEN_SPLIT_SCHEMA,
        "trust_mode": trust_mode,
        "production_eligible": trust_mode == "production",
        "row_attestations_verified": trust_mode == "production",
        "release_manifest": release_binding,
        "input_source_file_sha256": (
            dict(sorted(source_file_sha256.items()))
            if source_file_sha256 is not None
            else None
        ),
        "input_sha256": _sha256_bytes(input_payload),
        "n_input_rows": len(rows),
        "eval_ratio": eval_ratio,
        "split_seed": split_seed,
        "axes": manifest,
    }
    if trust_mode == "production":
        if manifest_attestation_key is None:
            raise ValueError("production unseen manifest attestation key is missing")
        manifest_payload = attach_attestation(
            manifest_payload,
            manifest_attestation_key,
            purpose=UNSEEN_SPLIT_ATTESTATION_PURPOSE,
        )
        if not _has_production_attestation(
            manifest_payload,
            manifest_attestation_key,
            purpose=UNSEEN_SPLIT_ATTESTATION_PURPOSE,
            role="report",
        ):
            raise ValueError("production unseen manifest attestation is invalid")
    _write_atomic(
        output_dir / "manifest.json",
        (json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n").encode(),
    )
    return manifest


def load_unseen_split_manifest(
    manifest_path: Path,
    *,
    trust_mode: str,
    release_manifest: dict[str, Any] | None = None,
    release_attestation_key: bytes | None = None,
    manifest_attestation_key: bytes | None = None,
) -> dict[str, Any]:
    """Reload a split manifest and recheck its trust chain and output bytes."""
    if trust_mode not in UNSEEN_TRUST_MODES:
        raise ValueError("unseen split trust mode is invalid")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("unseen split manifest is invalid") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != UNSEEN_SPLIT_SCHEMA
        or payload.get("trust_mode") != trust_mode
        or not isinstance(payload.get("n_input_rows"), int)
        or int(payload["n_input_rows"]) <= 0
    ):
        raise ValueError("unseen split manifest identity is invalid")

    if trust_mode == "production":
        if (
            payload.get("production_eligible") is not True
            or payload.get("row_attestations_verified") is not True
            or not _has_production_attestation(
                payload,
                manifest_attestation_key,
                purpose=UNSEEN_SPLIT_ATTESTATION_PURPOSE,
                role="report",
            )
        ):
            raise ValueError("production unseen manifest attestation is invalid")
        expected_release = _validated_release_binding(
            release_manifest,
            n_rows=payload["n_input_rows"],
            source_file_sha256=payload.get("input_source_file_sha256"),
            attestation_key=release_attestation_key,
        )
        if payload.get("release_manifest") != expected_release:
            raise ValueError("production unseen release manifest binding is invalid")
    elif (
        payload.get("production_eligible") is not False
        or payload.get("row_attestations_verified") is not False
        or payload.get("release_manifest") is not None
        or payload.get("input_source_file_sha256") is not None
        or "attestation" in payload
        or release_manifest is not None
        or release_attestation_key is not None
        or manifest_attestation_key is not None
    ):
        raise ValueError("local engineering manifest overstates production trust")

    _validate_split_outputs(payload, manifest_path)
    return payload
