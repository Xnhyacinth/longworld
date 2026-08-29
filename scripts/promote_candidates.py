#!/usr/bin/env python3
"""Audit external dense rankings and promote strictly replayed SFT candidates."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from itertools import groupby
from pathlib import Path
from typing import Any

from longworld.core.attestation import (
    PREDECESSOR_GATE_KEY_ENV,
    PREDECESSOR_GATE_KEY_ID_ENV,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.promotion import (
    RELEASE_SELECTION_PURPOSE,
    RELEASE_SELECTION_SCHEMA,
    PromotionError,
    candidate_sha256,
    candidate_structural_preflight,
    create_dense_audit,
    create_train_ready_report,
    promote_candidate,
    select_release_worlds,
)
from longworld.core.release_profile import release_profile, release_profile_sha256

REPLAY_PATH_REGISTRY_SCHEMA = "longworld.replay-path-registry.v1"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def _index(
    rows: list[dict[str, Any]], *, label: str, key_field: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row.get(key_field) or "")
        if not identity:
            raise ValueError(f"{label} row has no {key_field}")
        if identity in indexed:
            raise ValueError(f"duplicate {label} {key_field}: {identity}")
        indexed[identity] = row
    return indexed


def _write_jsonl_batch_atomic(
    outputs: list[tuple[Path, list[dict[str, Any]]]],
) -> None:
    if not outputs:
        raise ValueError("JSONL output batch is empty")
    resolved: set[Path] = set()
    for path, _rows in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.is_dir():
            raise ValueError(f"output parent is not a directory: {path.parent}")
        if path.is_dir():
            raise ValueError(f"output target is a directory: {path}")
        identity = path.resolve()
        if identity in resolved:
            raise ValueError(f"batch output path alias: {path}")
        resolved.add(identity)

    staged: list[tuple[Path, Path]] = []
    try:
        for path, rows in outputs:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temporary_path = Path(temporary_name)
            staged.append((temporary_path, path))
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    for row in rows:
                        handle.write(
                            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                        )
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
        for temporary_path, path in staged:
            os.replace(temporary_path, path)
    except BaseException:
        for temporary_path, _path in staged:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        raise


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_jsonl_batch_atomic([(path, rows)])


def _reject_path_aliases(paths: dict[str, Path | None]) -> None:
    resolved: dict[Path, str] = {}
    for label, path in paths.items():
        if path is None:
            continue
        identity = path.resolve()
        previous = resolved.get(identity)
        if previous is not None:
            raise ValueError(
                f"path alias: {previous} and {label} resolve to {identity}"
            )
        resolved[identity] = label


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(candidate.get("world_id") or ""),
        str(candidate.get("query_id") or ""),
        str(candidate.get("view") or ""),
        candidate_sha256(candidate),
    )


def _preflight_reject_sort_key(reject: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(reject.get("world_id") or ""),
        str(reject.get("query_id") or ""),
        str(reject.get("candidate_sha256") or ""),
    )


def preflight_candidates(
    candidates_path: Path,
    accepted_candidates_path: Path,
    rejects_path: Path,
    *,
    release_profile_id: str,
) -> int:
    """Filter structurally incomplete worlds before external dense ranking."""
    _reject_path_aliases(
        {
            "candidates": candidates_path,
            "accepted_candidates": accepted_candidates_path,
            "rejects": rejects_path,
        }
    )
    candidate_key = attestation_key_from_env("candidate_row")
    if candidate_key is None:
        raise ValueError("candidate attestation key is required")
    candidates = _read_jsonl(candidates_path)
    if not candidates:
        raise ValueError("candidate input is empty")
    accepted, rejects = candidate_structural_preflight(
        candidates,
        release_profile_id,
        candidate_attestation_key=candidate_key,
    )
    _write_jsonl_batch_atomic(
        [
            (
                accepted_candidates_path,
                sorted(accepted, key=_candidate_sort_key),
            ),
            (rejects_path, sorted(rejects, key=_preflight_reject_sort_key)),
        ]
    )
    return len(accepted)


def _world_batches(
    candidates: list[dict[str, Any]], receipts: dict[str, dict[str, Any]]
) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
    ordered = sorted(candidates, key=_candidate_sort_key)
    if any(not str(candidate.get("world_id") or "") for candidate in ordered):
        raise ValueError("candidate row has no world_id")
    return [
        [
            (candidate, receipts[candidate_sha256(candidate)])
            for candidate in world_candidates
        ]
        for _world_id, world_candidates in groupby(
            ordered, key=lambda candidate: str(candidate["world_id"])
        )
    ]


def _load_replay_registry(path: Path) -> dict[str, dict[str, Path]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("replay registry is missing or not a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("replay registry is not valid UTF-8 JSON") from error
    fields = {
        "schema_version",
        "episode_replay_bundles",
        "source_workflow_bundles",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("replay registry fields are invalid")
    if payload.get("schema_version") != REPLAY_PATH_REGISTRY_SCHEMA:
        raise ValueError("replay registry schema is invalid")
    registry: dict[str, dict[str, Path]] = {}
    for field in ("episode_replay_bundles", "source_workflow_bundles"):
        raw_mapping = payload.get(field)
        if not isinstance(raw_mapping, dict):
            raise TypeError(f"replay registry {field} is invalid")
        resolved: dict[str, Path] = {}
        for digest, raw_path in raw_mapping.items():
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not isinstance(raw_path, str)
                or not raw_path.strip()
            ):
                raise ValueError(f"replay registry {field} entry is invalid")
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"replay registry {field} path is invalid")
            resolved[digest] = candidate.absolute()
        registry[field] = resolved
    return registry


def _candidate_replay_paths(
    candidate: dict[str, Any],
    episode_bundle_path: Path | None,
    source_bundle_path: Path | None,
    replay_registry: dict[str, dict[str, Path]] | None,
) -> tuple[Path | None, Path | None]:
    if replay_registry is None:
        return episode_bundle_path, source_bundle_path
    episode_binding = candidate.get("episode_replay_bundle")
    source_binding = candidate.get("source_workflow_bundle")
    if episode_binding is not None and source_binding is not None:
        raise PromotionError("candidate cannot bind two replay bundle types")
    if isinstance(episode_binding, dict):
        digest = str(episode_binding.get("sha256") or "")
        path = replay_registry["episode_replay_bundles"].get(digest)
        if path is None:
            raise PromotionError("episode replay bundle is missing from registry")
        return path, None
    if isinstance(source_binding, dict):
        digest = str(source_binding.get("sha256") or "")
        path = replay_registry["source_workflow_bundles"].get(digest)
        if path is None:
            raise PromotionError("source workflow bundle is missing from registry")
        return None, path
    return None, None


def _audit_world(
    batch: list[tuple[dict[str, Any], dict[str, Any]]],
    k: int,
    episode_bundle_path: Path | None,
    source_bundle_path: Path | None,
    candidate_key: bytes,
    ranking_key: bytes,
    audit_key: bytes,
    source_key: bytes | None,
    filter_mode: bool,
    replay_registry: dict[str, dict[str, Path]] | None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    audits: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for candidate, ranking in batch:
        digest = candidate_sha256(candidate)
        try:
            candidate_episode_path, candidate_source_path = _candidate_replay_paths(
                candidate,
                episode_bundle_path,
                source_bundle_path,
                replay_registry,
            )
            audits.append(
                create_dense_audit(
                    candidate,
                    ranking,
                    k=k,
                    episode_bundle_path=candidate_episode_path,
                    source_bundle_path=candidate_source_path,
                    candidate_attestation_key=candidate_key,
                    ranking_attestation_key=ranking_key,
                    audit_attestation_key=audit_key,
                    episode_attestation_key=source_key,
                    source_attestation_key=source_key,
                )
            )
        except PromotionError as error:
            errors[digest] = str(error)
    if not errors:
        accepted_ids = (
            [candidate_sha256(candidate) for candidate, _ranking in batch]
            if filter_mode
            else []
        )
        return audits, accepted_ids, []

    world_id = str(batch[0][0]["world_id"])
    summary = "; ".join(
        f"{digest}:{reason}" for digest, reason in sorted(errors.items())
    )
    if not filter_mode:
        raise PromotionError(f"world {world_id} dense audit failed: {summary}")
    rejects = []
    for candidate, _ranking in batch:
        digest = candidate_sha256(candidate)
        rejects.append(
            {
                "schema_version": "dense-audit-reject-v1",
                "candidate_sha256": digest,
                "world_id": world_id,
                "query_id": str(candidate.get("query_id") or ""),
                "reason": errors.get(
                    digest,
                    f"world atomic rejection after sibling failure: {summary}",
                ),
            }
        )
    return [], [], rejects


def _promote_world(
    batch: list[tuple[dict[str, Any], dict[str, Any]]],
    episode_bundle_path: Path | None,
    source_bundle_path: Path | None,
    candidate_key: bytes,
    audit_key: bytes,
    promotion_key: bytes,
    source_key: bytes | None,
    expected_split: str | None,
    release_selection: dict[str, Any] | None,
    replay_registry: dict[str, dict[str, Path]] | None,
) -> list[dict[str, Any]]:
    promoted: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for candidate, audit in batch:
        digest = candidate_sha256(candidate)
        try:
            candidate_episode_path, candidate_source_path = _candidate_replay_paths(
                candidate,
                episode_bundle_path,
                source_bundle_path,
                replay_registry,
            )
            promoted.append(
                promote_candidate(
                    candidate,
                    audit,
                    episode_bundle_path=candidate_episode_path,
                    source_bundle_path=candidate_source_path,
                    candidate_attestation_key=candidate_key,
                    audit_attestation_key=audit_key,
                    promotion_attestation_key=promotion_key,
                    episode_attestation_key=source_key,
                    source_attestation_key=source_key,
                    expected_split=expected_split,
                    release_selection_receipt=release_selection,
                )
            )
        except PromotionError as error:
            errors[digest] = str(error)
    if errors:
        world_id = str(batch[0][0]["world_id"])
        summary = "; ".join(
            f"{digest}:{reason}" for digest, reason in sorted(errors.items())
        )
        raise PromotionError(f"world {world_id} promotion failed: {summary}")
    return promoted


def _matched_inputs(
    candidates_path: Path, receipts_path: Path, *, receipt_label: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = _read_jsonl(candidates_path)
    if not candidates:
        raise ValueError("candidate input is empty")
    candidate_ids: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        identity = candidate_sha256(candidate)
        if identity in candidate_ids:
            raise ValueError(f"duplicate candidate candidate_sha256: {identity}")
        candidate_ids[identity] = candidate
    receipts = _index(
        _read_jsonl(receipts_path),
        label=receipt_label,
        key_field="candidate_sha256",
    )
    missing = sorted(set(candidate_ids) - set(receipts))
    extra = sorted(set(receipts) - set(candidate_ids))
    if missing or extra:
        raise ValueError(
            f"{receipt_label} coverage mismatch: missing={missing}, extra={extra}"
        )
    return candidates, receipts


def audit_rankings(
    candidates_path: Path,
    rankings_path: Path,
    output_path: Path,
    *,
    k: int,
    episode_bundle_path: Path | None = None,
    source_bundle_path: Path | None = None,
    accepted_candidates_path: Path | None = None,
    rejects_path: Path | None = None,
    release_profile_id: str | None = None,
    workers: int = 1,
    replay_registry_path: Path | None = None,
) -> int:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if replay_registry_path is not None and (
        episode_bundle_path is not None or source_bundle_path is not None
    ):
        raise ValueError("replay registry cannot be combined with a single bundle path")
    filter_mode = accepted_candidates_path is not None or rejects_path is not None
    if filter_mode and (accepted_candidates_path is None or rejects_path is None):
        raise ValueError(
            "accepted_candidates_path and rejects_path must be provided together"
        )
    _reject_path_aliases(
        {
            "candidates": candidates_path,
            "rankings": rankings_path,
            "audit_output": output_path,
            "accepted_candidates": accepted_candidates_path,
            "rejects": rejects_path,
            "episode_bundle": episode_bundle_path,
            "source_bundle": source_bundle_path,
            "replay_registry": replay_registry_path,
        }
    )
    replay_registry = (
        _load_replay_registry(replay_registry_path)
        if replay_registry_path is not None
        else None
    )
    if release_profile_id is not None:
        required_k = release_profile(release_profile_id).dense_top_k
        if k != required_k:
            raise ValueError(
                f"release profile {release_profile_id} requires dense top-k={required_k}"
            )
    candidate_key = attestation_key_from_env("candidate_row")
    ranking_key = attestation_key_from_env("dense_ranking")
    audit_key = attestation_key_from_env("dense_retrieval_audit")
    source_key = attestation_key_from_env("episode_replay_bundle")
    if candidate_key is None or ranking_key is None or audit_key is None:
        raise ValueError("candidate, ranker, and auditor attestation keys are required")
    candidates = _read_jsonl(candidates_path)
    if not candidates:
        raise ValueError("candidate input is empty")
    structurally_accepted = candidates
    structural_rejects: list[dict[str, Any]] = []
    if release_profile_id is not None:
        structurally_accepted, structural_rejects = candidate_structural_preflight(
            candidates,
            release_profile_id,
            candidate_attestation_key=candidate_key,
        )
    if structural_rejects and not filter_mode:
        missing_by_world = {
            str(reject["world_id"]): tuple(reject["missing_exact_length_buckets"])
            for reject in structural_rejects
        }
        details = ",".join(
            f"{world_id}={'+'.join(missing_by_world[world_id])}"
            for world_id in sorted(missing_by_world)
        )
        raise PromotionError("candidate structural preflight failed: " + details)
    candidates_by_digest: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        digest = candidate_sha256(candidate)
        if digest in candidates_by_digest:
            raise ValueError(f"duplicate candidate candidate_sha256: {digest}")
        candidates_by_digest[digest] = candidate
    rankings = _index(
        _read_jsonl(rankings_path), label="ranking", key_field="candidate_sha256"
    )
    accepted_ids = {candidate_sha256(candidate) for candidate in structurally_accepted}
    missing = sorted(accepted_ids - set(rankings))
    extra = sorted(set(rankings) - accepted_ids)
    if missing or extra:
        raise ValueError(f"ranking coverage mismatch: missing={missing}, extra={extra}")
    rankings = {digest: rankings[digest] for digest in accepted_ids}
    batches = _world_batches(structurally_accepted, rankings)
    if not batches:
        results = []
    elif workers == 1:
        results = [
            _audit_world(
                batch,
                k,
                episode_bundle_path,
                source_bundle_path,
                candidate_key,
                ranking_key,
                audit_key,
                source_key,
                filter_mode,
                replay_registry,
            )
            for batch in batches
        ]
    else:
        with ProcessPoolExecutor(
            max_workers=min(workers, len(batches)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            futures = [
                executor.submit(
                    _audit_world,
                    batch,
                    k,
                    episode_bundle_path,
                    source_bundle_path,
                    candidate_key,
                    ranking_key,
                    audit_key,
                    source_key,
                    filter_mode,
                    replay_registry,
                )
                for batch in batches
            ]
            results = [future.result() for future in futures]
    audits = [audit for result in results for audit in result[0]]
    accepted = [
        candidates_by_digest[digest] for result in results for digest in result[1]
    ]
    rejects = sorted(
        structural_rejects + [reject for result in results for reject in result[2]],
        key=_preflight_reject_sort_key,
    )
    outputs = [(output_path, audits)]
    if filter_mode:
        assert accepted_candidates_path is not None and rejects_path is not None
        outputs.extend([(accepted_candidates_path, accepted), (rejects_path, rejects)])
    _write_jsonl_batch_atomic(outputs)
    return len(audits)


def promote_rows(
    candidates_path: Path,
    audits_path: Path,
    output_path: Path,
    *,
    episode_bundle_path: Path | None = None,
    source_bundle_path: Path | None = None,
    expected_split: str | None = None,
    release_selection_path: Path | None = None,
    workers: int = 1,
    replay_registry_path: Path | None = None,
) -> int:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if replay_registry_path is not None and (
        episode_bundle_path is not None or source_bundle_path is not None
    ):
        raise ValueError("replay registry cannot be combined with a single bundle path")
    replay_registry = (
        _load_replay_registry(replay_registry_path)
        if replay_registry_path is not None
        else None
    )
    candidate_key = attestation_key_from_env("candidate_row")
    audit_key = attestation_key_from_env("dense_retrieval_audit")
    promotion_key = attestation_key_from_env("sft_row")
    source_key = attestation_key_from_env("episode_replay_bundle")
    if candidate_key is None or audit_key is None or promotion_key is None:
        raise ValueError(
            "candidate, auditor, and promotion attestation keys are required"
        )
    release_selection = None
    if release_selection_path is not None:
        release_selection = json.loads(
            release_selection_path.read_text(encoding="utf-8")
        )
        if not isinstance(release_selection, dict):
            raise TypeError("release selection receipt must be a JSON object")
    if not _read_jsonl(candidates_path):
        if _read_jsonl(audits_path):
            raise ValueError("empty candidate input has dense audit rows")
        if expected_split not in {"train", "eval"} or release_selection is None:
            raise ValueError("empty candidate input requires a selected split")
        profile_id = str(release_selection.get("release_profile_id") or "")
        profile = release_profile(profile_id)
        split_by_world = release_selection.get("split_by_world")
        expected_worlds = (
            profile.min_train_worlds
            if expected_split == "train"
            else profile.min_eval_worlds
        )
        if (
            not verify_attestation(
                release_selection,
                audit_key,
                purpose=RELEASE_SELECTION_PURPOSE,
            )
            or release_selection.get("schema_version") != RELEASE_SELECTION_SCHEMA
            or release_selection.get("release_profile_sha256")
            != release_profile_sha256(profile_id)
            or release_selection.get("split_strategy") != profile.split_strategy
            or not isinstance(split_by_world, dict)
            or release_selection.get("n_selected_worlds")
            != profile.expected_promoted_worlds
            or len(split_by_world) != profile.expected_promoted_worlds
            or list(split_by_world.values()).count("train") != profile.min_train_worlds
            or list(split_by_world.values()).count("eval") != profile.min_eval_worlds
        ):
            raise ValueError("empty candidate input has invalid release selection")
        if expected_worlds != 0:
            raise ValueError("selected split is not empty")
        _write_jsonl_atomic(output_path, [])
        return 0
    candidates, audits = _matched_inputs(
        candidates_path, audits_path, receipt_label="dense audit"
    )
    batches = _world_batches(candidates, audits)
    if workers == 1:
        results = [
            _promote_world(
                batch,
                episode_bundle_path,
                source_bundle_path,
                candidate_key,
                audit_key,
                promotion_key,
                source_key,
                expected_split,
                release_selection,
                replay_registry,
            )
            for batch in batches
        ]
    else:
        with ProcessPoolExecutor(
            max_workers=min(workers, len(batches)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            futures = [
                executor.submit(
                    _promote_world,
                    batch,
                    episode_bundle_path,
                    source_bundle_path,
                    candidate_key,
                    audit_key,
                    promotion_key,
                    source_key,
                    expected_split,
                    release_selection,
                    replay_registry,
                )
                for batch in batches
            ]
            results = [future.result() for future in futures]
    promoted = [row for result in results for row in result]
    _write_jsonl_atomic(output_path, promoted)
    return len(promoted)


def write_train_ready_report(
    candidate_report_path: Path,
    candidate_paths: list[Path],
    row_paths: list[Path],
    output_path: Path,
    release_selection_path: Path | None = None,
) -> int:
    report_key = attestation_key_from_env("quality_report")
    candidate_key = attestation_key_from_env("candidate_row")
    promotion_key = attestation_key_from_env("sft_row")
    if report_key is None or candidate_key is None or promotion_key is None:
        raise ValueError(
            "report, candidate, and promotion attestation keys are required"
        )
    candidate_report = json.loads(candidate_report_path.read_text(encoding="utf-8"))
    if not isinstance(candidate_report, dict):
        raise TypeError("candidate quality report must be a JSON object")
    candidates = [row for path in candidate_paths for row in _read_jsonl(path)]
    rows = [row for path in row_paths for row in _read_jsonl(path)]
    release_selection = None
    if release_selection_path is not None:
        release_selection = json.loads(
            release_selection_path.read_text(encoding="utf-8")
        )
        if not isinstance(release_selection, dict):
            raise TypeError("release selection receipt must be a JSON object")
    report = create_train_ready_report(
        candidate_report,
        candidates,
        rows,
        report_attestation_key=report_key,
        candidate_attestation_key=candidate_key,
        promotion_attestation_key=promotion_key,
        selection_attestation_key=attestation_key_from_env("release_world_selection"),
        release_selection_receipt=release_selection,
    )
    _write_jsonl_atomic(output_path, [report])
    return len(rows)


def select_worlds(
    candidate_paths: list[Path],
    audit_paths: list[Path],
    *,
    release_profile_id: str,
    train_candidates_path: Path,
    eval_candidates_path: Path,
    train_audits_path: Path,
    eval_audits_path: Path,
    receipt_path: Path,
    predecessor_gate_receipt_path: Path | None = None,
) -> int:
    candidate_key = attestation_key_from_env("candidate_row")
    audit_key = attestation_key_from_env("dense_retrieval_audit")
    if candidate_key is None or audit_key is None:
        raise ValueError("candidate and auditor attestation keys are required")
    predecessor_gate_receipt = None
    if predecessor_gate_receipt_path is not None:
        predecessor_gate_receipt = json.loads(
            predecessor_gate_receipt_path.read_text(encoding="utf-8")
        )
        if not isinstance(predecessor_gate_receipt, dict):
            raise TypeError("predecessor gate receipt must be a JSON object")
    candidates = [row for path in candidate_paths for row in _read_jsonl(path)]
    audits = [row for path in audit_paths for row in _read_jsonl(path)]
    selected, receipt = select_release_worlds(
        candidates,
        audits,
        release_profile_id,
        candidate_attestation_key=candidate_key,
        audit_attestation_key=audit_key,
        predecessor_gate_receipt=predecessor_gate_receipt,
        predecessor_gate_attestation_key=(
            os.environ.get(PREDECESSOR_GATE_KEY_ENV, "").encode() or None
        ),
        predecessor_gate_key_id=os.environ.get(PREDECESSOR_GATE_KEY_ID_ENV, "").strip(),
    )
    splits = receipt["split_by_world"]
    selected_ids = {candidate_sha256(candidate) for candidate in selected}
    selected_audits = [
        audit
        for audit in audits
        if str(audit.get("candidate_sha256") or "") in selected_ids
    ]
    audits_by_id = {str(audit["candidate_sha256"]): audit for audit in selected_audits}
    train_candidates = [
        candidate
        for candidate in selected
        if splits[str(candidate["world_id"])] == "train"
    ]
    eval_candidates = [
        candidate
        for candidate in selected
        if splits[str(candidate["world_id"])] == "eval"
    ]
    _write_jsonl_atomic(train_candidates_path, train_candidates)
    _write_jsonl_atomic(eval_candidates_path, eval_candidates)
    _write_jsonl_atomic(
        train_audits_path,
        [audits_by_id[candidate_sha256(row)] for row in train_candidates],
    )
    _write_jsonl_atomic(
        eval_audits_path,
        [audits_by_id[candidate_sha256(row)] for row in eval_candidates],
    )
    _write_jsonl_atomic(receipt_path, [receipt])
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser(
        "audit", help="validate external dense rankings and sign replay receipts"
    )
    audit.add_argument("--candidates", type=Path, required=True)
    audit.add_argument("--rankings", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--top-k", type=int, default=3)
    audit.add_argument("--release-profile", required=True)
    audit.add_argument("--episode-bundle", type=Path)
    audit.add_argument("--source-bundle", type=Path)
    audit.add_argument("--replay-registry", type=Path)
    audit.add_argument("--accepted-candidates", type=Path)
    audit.add_argument("--rejects", type=Path)
    audit.add_argument("--workers", type=int, default=1)

    preflight = subparsers.add_parser(
        "preflight",
        help="filter worlds missing required exact bands before dense ranking",
    )
    preflight.add_argument("--candidates", type=Path, required=True)
    preflight.add_argument("--accepted-candidates", type=Path, required=True)
    preflight.add_argument("--rejects", type=Path, required=True)
    preflight.add_argument("--release-profile", required=True)

    promote = subparsers.add_parser(
        "promote", help="strictly replay audited candidates and sign train-ready rows"
    )
    promote.add_argument("--candidates", type=Path, required=True)
    promote.add_argument("--audits", type=Path, required=True)
    promote.add_argument("--output", type=Path, required=True)
    promote.add_argument("--episode-bundle", type=Path)
    promote.add_argument("--source-bundle", type=Path)
    promote.add_argument("--replay-registry", type=Path)
    promote.add_argument("--expected-split", choices=("train", "eval"), required=True)
    promote.add_argument("--release-selection", type=Path, required=True)
    promote.add_argument("--workers", type=int, default=1)

    select = subparsers.add_parser(
        "select", help="select complete audited worlds and assign release splits"
    )
    select.add_argument("--candidates", type=Path, nargs="+", required=True)
    select.add_argument("--audits", type=Path, nargs="+", required=True)
    select.add_argument("--release-profile", required=True)
    select.add_argument("--train-candidates", type=Path, required=True)
    select.add_argument("--eval-candidates", type=Path, required=True)
    select.add_argument("--train-audits", type=Path, required=True)
    select.add_argument("--eval-audits", type=Path, required=True)
    select.add_argument("--receipt", type=Path, required=True)
    select.add_argument("--predecessor-gate-receipt", type=Path)

    report = subparsers.add_parser(
        "report", help="sign the exact promoted train/eval row set"
    )
    report.add_argument("--candidate-report", type=Path, required=True)
    report.add_argument("--candidates", type=Path, nargs="+", required=True)
    report.add_argument("--rows", type=Path, nargs="+", required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--release-selection", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "preflight":
        count = preflight_candidates(
            args.candidates,
            args.accepted_candidates,
            args.rejects,
            release_profile_id=args.release_profile,
        )
    elif args.command == "audit":
        count = audit_rankings(
            args.candidates,
            args.rankings,
            args.output,
            k=args.top_k,
            episode_bundle_path=args.episode_bundle,
            source_bundle_path=args.source_bundle,
            accepted_candidates_path=args.accepted_candidates,
            rejects_path=args.rejects,
            release_profile_id=args.release_profile,
            workers=args.workers,
            replay_registry_path=args.replay_registry,
        )
    elif args.command == "promote":
        count = promote_rows(
            args.candidates,
            args.audits,
            args.output,
            episode_bundle_path=args.episode_bundle,
            source_bundle_path=args.source_bundle,
            expected_split=args.expected_split,
            release_selection_path=args.release_selection,
            workers=args.workers,
            replay_registry_path=args.replay_registry,
        )
    elif args.command == "select":
        count = select_worlds(
            args.candidates,
            args.audits,
            release_profile_id=args.release_profile,
            train_candidates_path=args.train_candidates,
            eval_candidates_path=args.eval_candidates,
            train_audits_path=args.train_audits,
            eval_audits_path=args.eval_audits,
            receipt_path=args.receipt,
            predecessor_gate_receipt_path=args.predecessor_gate_receipt,
        )
    else:
        count = write_train_ready_report(
            args.candidate_report,
            args.candidates,
            args.rows,
            args.output,
            args.release_selection,
        )
    output = (
        args.receipt
        if args.command == "select"
        else args.accepted_candidates
        if args.command == "preflight"
        else args.output
    )
    print(json.dumps({"command": args.command, "rows": count, "output": str(output)}))


if __name__ == "__main__":
    main()
