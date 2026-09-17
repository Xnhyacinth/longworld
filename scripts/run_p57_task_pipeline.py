#!/usr/bin/env python3
"""Concurrent P57 task pipeline: pack, dense-audit, classify, never auto-promote.

Long-running scheduler for independent semantic tasks. It does not weaken
window gates, does not pad unique leftover into a higher exact band, and never
writes CURRENT_RELEASE or production receipts.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core import p57pipeline as pipeline_rules
from longworld.core.p57pipeline import (  # noqa: E402
    FILTER_RECEIPT_SCHEMA,
    LEDGER_SCHEMA,
    PIPELINE_SCHEMA,
    QUESTION_ONLY_CHECK_REVISION,
    ROUTE_BLOCKED_INSUFFICIENT_PROOF,
    ROUTE_BLOCKED_INSUFFICIENT_UNIQUE,
    ROUTE_BLOCKED_PARENT_EXPLOSION,
    ROUTE_BLOCKED_QUESTION_ONLY,
    classify_job_audits,
    job_pad_errors,
    parent_artifact_explosion_error,
    question_only_codebook_prediction,
)
from longworld.core.promotion import candidate_sha256
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES

MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
_MAX_WORKERS = 16
_MAX_CATALOG_BYTES = 4 * 1024 * 1024
_FILTER_RULES_SHA256 = hashlib.sha256(
    Path(pipeline_rules.__file__).read_bytes()
).hexdigest()


class PipelineCatalogError(ValueError):
    """Raised when the P57 task catalog is unsafe or malformed."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    return rows


def _expand_environment(value: Any) -> Any:
    """Expand ${VAR} in catalog string values, failing closed on an unset variable.

    Catalog paths are absolute by contract: the trust loader rejects a relative
    one outright, and consumer scripts resolve job paths against this value.
    Catalogs therefore cannot hard-code a machine path, and they cannot carry a
    bare ${QJIU_ROOT} either, because nothing downstream expands it. Expansion
    happens here, at the single point every catalog goes through, so a missing
    variable is one clear error instead of a downstream "trust file is missing".
    """
    if isinstance(value, str):
        if "${" not in value:
            return value
        expanded = os.path.expandvars(value)
        if "${" in expanded:
            raise PipelineCatalogError(
                f"catalog value {value!r} references an unset environment "
                "variable; source scripts/uv_project_env.sh before running"
            )
        return expanded
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    return value


def load_catalog(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PipelineCatalogError("catalog is missing or not a regular file")
    raw = path.read_bytes()
    if len(raw) > _MAX_CATALOG_BYTES:
        raise PipelineCatalogError("catalog exceeds size limit")
    catalog = _expand_environment(json.loads(raw.decode("utf-8")))
    if not isinstance(catalog, dict):
        raise PipelineCatalogError("catalog must be a JSON object")
    if catalog.get("schema_version") != PIPELINE_SCHEMA:
        raise PipelineCatalogError("unknown P57 pipeline catalog schema")
    jobs = catalog.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise PipelineCatalogError("catalog jobs are missing")
    seen: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise PipelineCatalogError("catalog job must be an object")
        job_id = str(job.get("job_id") or "")
        if not job_id or job_id in seen:
            raise PipelineCatalogError("catalog job_id is missing or duplicated")
        seen.add(job_id)
        if job.get("auto_promote") is not False:
            raise PipelineCatalogError(
                f"{job_id}: auto_promote must be false; pipeline never promotes"
            )
        if job.get("production_eligible") not in {None, False}:
            raise PipelineCatalogError(f"{job_id}: production_eligible must be false")
        if not job.get("trust_file") or not job.get("projected_dir"):
            raise PipelineCatalogError(f"{job_id}: trust_file and projected_dir required")
        buckets = job.get("primary_buckets")
        if not isinstance(buckets, list) or not buckets:
            raise PipelineCatalogError(f"{job_id}: primary_buckets required")
        if any(not isinstance(bucket, str) or bucket not in EXACT_TOKEN_BAND_RANGES for bucket in buckets):
            raise PipelineCatalogError(f"{job_id}: invalid primary length bucket")
        for field in (
            "trust_file",
            "projected_dir",
            "parents_dir",
            "generate_script",
            "generate_config",
        ):
            value = job.get(field)
            if not value:
                continue
            path = Path(str(value))
            job[field] = str(path if path.is_absolute() else ROOT / path)
    ledger = catalog.get("ledger")
    if ledger:
        ledger_path = Path(str(ledger))
        catalog["ledger"] = str(
            ledger_path if ledger_path.is_absolute() else ROOT / ledger_path
        )
    return catalog


def filter_receipt_complete(
    job: dict[str, Any], *, expected_input_sha256: str
) -> bool:
    receipt_path = Path(job["projected_dir"]) / "FILTER_RECEIPT.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(
        isinstance(receipt, dict)
        and receipt.get("schema_version") == FILTER_RECEIPT_SCHEMA
        and receipt.get("job_id") == job.get("job_id")
        and receipt.get("input_sha256") == expected_input_sha256
        and receipt.get("auto_promote") is False
        and receipt.get("production_eligible") is False
    )


def _job_input_sha256(job: dict[str, Any]) -> str | None:
    projected = Path(job["projected_dir"])
    inputs = {}
    for name in ("audits.jsonl", "candidates.jsonl"):
        path = projected / name
        if path.is_file() and not path.is_symlink():
            inputs[name] = _sha256_file(path)
    parents = job.get("parents_dir")
    if parents:
        path = Path(parents) / "parents.jsonl"
        if path.is_file() and not path.is_symlink():
            inputs["parents.jsonl"] = _sha256_file(path)
    if not inputs:
        return None
    return hashlib.sha256(
        _canonical_bytes(
            {
                "files": inputs,
                "primary_buckets": sorted(set(job["primary_buckets"])),
                "question_only_check_revision": QUESTION_ONLY_CHECK_REVISION,
                "filter_rules_sha256": _FILTER_RULES_SHA256,
            }
        )
    ).hexdigest()


def _trust_command(
    python: Path,
    job: dict[str, Any],
    roles: tuple[str, ...],
    command: list[str],
    *,
    pass_env: tuple[str, ...] = (
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TOKENIZERS_PARALLELISM",
    ),
) -> list[str]:
    argv = [
        str(python),
        str(ROOT / "scripts" / "run_with_local_probe_trust.py"),
        "--trust-file",
        str(job["trust_file"]),
    ]
    for role in roles:
        argv.extend(["--role", role])
    if len(roles) > 1:
        argv.append("--allow-combined-roles")
    for key in pass_env:
        argv.extend(["--pass-env", key])
    argv.append("--")
    argv.extend(command)
    return argv


def _run_command(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    log_path: Path | None = None,
) -> None:
    if log_path is None:
        subprocess.run(argv, check=True, env=env, timeout=timeout, cwd=str(ROOT))
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as handle:
        handle.write(b"$ " + " ".join(argv).encode("utf-8", errors="replace") + b"\n")
        handle.flush()
        subprocess.run(
            argv,
            check=True,
            env=env,
            timeout=timeout,
            cwd=str(ROOT),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )


def _append_ledger(ledger_path: Path, row: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _signed_workflow_missing(config_path: str) -> bool:
    path = Path(config_path)
    if not path.is_file():
        return False
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(config, dict):
        return False
    signed = config.get("signed_workflow_file")
    source_dir = config.get("source_inventory_dir")
    if not signed or not source_dir:
        return False
    return not (Path(str(source_dir)) / str(signed)).is_file()


_STRUCTURAL_LOG_MARKERS = (
    ("task proof complexity is insufficient", ROUTE_BLOCKED_INSUFFICIENT_PROOF),
    (
        "IETF task replay artifact selection is invalid",
        ROUTE_BLOCKED_INSUFFICIENT_PROOF,
    ),
    (
        "raw token window 16k intersecting-artifact",
        ROUTE_BLOCKED_INSUFFICIENT_PROOF,
    ),
    ("parent_artifact_explosion", ROUTE_BLOCKED_PARENT_EXPLOSION),
)


def _log_blocker(log_path: Path) -> str | None:
    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")[-16000:]
    except OSError:
        return None
    for marker, route in _STRUCTURAL_LOG_MARKERS:
        if marker in text:
            return route
    return None


def _parent_artifact_count(job: dict[str, Any]) -> int | None:
    parents_dir = job.get("parents_dir")
    if not parents_dir:
        return None
    path = Path(str(parents_dir)) / "parents.jsonl"
    if not path.is_file() or path.is_symlink():
        return None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                return None
            classifications = row.get("artifact_classification")
            if not isinstance(classifications, list):
                return None
            return len(classifications)
    return None


def _job_log_path(ledger_path: Path, job_id: str) -> Path:
    return ledger_path.parent / "logs" / f"{job_id}.log"


def _write_filter_receipt(job: dict[str, Any], receipt: dict[str, Any]) -> Path:
    projected = Path(job["projected_dir"])
    projected.mkdir(parents=True, exist_ok=True)
    path = projected / "FILTER_RECEIPT.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("filter receipt destination is not a regular file")
    payload = (
        _canonical_bytes(
            {
                **receipt,
                "question_only_check_revision": QUESTION_ONLY_CHECK_REVISION,
                "filter_rules_sha256": _FILTER_RULES_SHA256,
                "primary_buckets": sorted(set(job["primary_buckets"])),
            }
        )
        + b"\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".FILTER_RECEIPT.", dir=projected
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return path


def _checked_primary_candidates(job: dict[str, Any]) -> list[dict[str, Any]]:
    path = Path(job["projected_dir"]) / "candidates.jsonl"
    if not path.is_file():
        if (
            path.exists()
            or path.is_symlink()
            or (path.parent / "audits.jsonl").exists()
        ):
            raise ValueError(
                "question-only check requires readable projected candidates"
            )
        receipt_path = path.parent / "FILTER_RECEIPT.json"
        if receipt_path.is_file():
            old_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(old_receipt, dict):
                raise ValueError("existing filter receipt is not an object")
            if old_receipt.get("strict_eligible") is True:
                raise ValueError(
                    "question-only check requires readable projected candidates"
                )
        return []
    if path.is_symlink():
        raise ValueError("projected candidates must be a regular file")
    buckets = set(job["primary_buckets"])
    selected = []
    query_ids = set()
    for row in _read_jsonl(path):
        band = row.get("length_bucket")
        if not isinstance(band, str) or band not in EXACT_TOKEN_BAND_RANGES:
            raise ValueError("candidate has an invalid length bucket")
        if band not in buckets:
            continue
        query_id = row.get("query_id")
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            raise ValueError("primary candidate query ids are missing or duplicated")
        if not isinstance(row.get("question"), str) or not row["question"].strip():
            raise ValueError("primary candidate question must be a nonempty string")
        if not isinstance(row.get("answer"), (str, dict)):
            raise TypeError("primary candidate answer is unavailable")
        selected.append(row)
        query_ids.add(query_id)
    missing = buckets - {row["length_bucket"] for row in selected}
    if missing:
        raise ValueError(
            "no candidates for primary length buckets: " + ",".join(sorted(missing))
        )
    return selected


def _question_only_shortcut_queries(job: dict[str, Any]) -> list[str]:
    matches = []
    for row in _checked_primary_candidates(job):
        prediction = question_only_codebook_prediction(row["question"])
        if prediction is None:
            continue
        answer = row.get("answer")
        if isinstance(answer, str):
            answer = json.loads(answer)
        if prediction == answer:
            matches.append(str(row.get("query_id") or ""))
    return matches


def _record_question_only_block(
    job: dict[str, Any],
    matches: list[str],
    *,
    ledger_path: Path,
    execute: bool,
) -> dict[str, Any]:
    row = {
        "schema_version": LEDGER_SCHEMA,
        "job_id": job["job_id"],
        "status": "blocked",
        "route": ROUTE_BLOCKED_QUESTION_ONLY,
        "strict_eligible": False,
        "question_only_query_ids": matches,
        "question_only_check_revision": QUESTION_ONLY_CHECK_REVISION,
        "production_eligible": False,
    }
    if execute:
        audits = Path(job["projected_dir"]) / "audits.jsonl"
        _write_filter_receipt(
            job,
            {
                **row,
                "schema_version": FILTER_RECEIPT_SCHEMA,
                "auto_promote": False,
                "input_sha256": _job_input_sha256(job) or "",
                "n_audits": len(_read_jsonl(audits)) if audits.is_file() else 0,
            },
        )
        _append_ledger(ledger_path, row)
    return row


def _classify_existing_audits(job: dict[str, Any], input_sha256: str) -> dict[str, Any]:
    audits_path = Path(job["projected_dir"]) / "audits.jsonl"
    candidates = {row["query_id"]: row for row in _checked_primary_candidates(job)}
    rows = []
    seen = set()
    for audit in _read_jsonl(audits_path):
        query_id = audit.get("query_id")
        if query_id not in candidates:
            continue
        if query_id in seen or audit.get("candidate_sha256") != candidate_sha256(
            candidates[query_id]
        ):
            raise ValueError("dense audit does not bind the current primary candidate")
        rows.append(audit)
        seen.add(query_id)
    if seen != set(candidates):
        raise ValueError("dense audits do not cover every primary candidate")
    classified = classify_job_audits(rows)
    classified.update(
        {
            "job_id": job["job_id"],
            "input_sha256": input_sha256,
            "already_promoted": bool(job.get("already_promoted")),
            "question_only_check_revision": QUESTION_ONLY_CHECK_REVISION,
        }
    )
    _write_filter_receipt(job, classified)
    return classified


def _run_synthesis_stages(
    job: dict[str, Any],
    *,
    python: Path,
    env: dict[str, str],
    timeout: float,
    minilm_model: str,
    minilm_revision: str,
    ledger_path: Path,
    audit_workers: int,
) -> list[str] | None:
    projected = Path(job["projected_dir"])
    projected.mkdir(parents=True, exist_ok=True)
    parents_dir = Path(job["parents_dir"]) if job.get("parents_dir") else None
    generate_script = job.get("generate_script")
    generate_config = job.get("generate_config")
    generate_ready = bool(
        generate_script
        and generate_config
        and Path(str(generate_script)).is_file()
        and Path(str(generate_config)).is_file()
    )
    log_path = _job_log_path(ledger_path, str(job["job_id"]))
    if generate_ready and parents_dir is not None:
        if not (parents_dir / "parents.jsonl").is_file():
            _run_command(
                _trust_command(
                    python,
                    job,
                    ("source", "candidate"),
                    [
                        str(python),
                        str(Path(generate_script)),
                        "--config",
                        str(generate_config),
                    ],
                ),
                env=env,
                timeout=timeout,
                log_path=log_path,
            )
    if parents_dir is None or not (parents_dir / "parents.jsonl").is_file():
        parent_candidates = None
        parent_sidecar = None
    else:
        parent_candidates = parents_dir / "parents.jsonl"
        parent_sidecar = parents_dir / str(
            job.get("sidecar_name") or "TASK_REPLAY_SIDECAR.json"
        )
        if parent_sidecar is not None and not parent_sidecar.is_file():
            parent_sidecar = None
    candidates = projected / "candidates.jsonl"
    if parent_candidates is not None and not candidates.is_file():
        if parent_sidecar is None:
            return
        _run_command(
            _trust_command(
                python,
                job,
                ("source", "candidate"),
                [
                    str(python),
                    str(ROOT / "scripts" / "project_task_candidate_views.py"),
                    "--candidates",
                    str(parent_candidates),
                    "--sidecar",
                    str(parent_sidecar),
                    "--output-dir",
                    str(projected),
                    *[
                        item
                        for bucket in job.get("primary_buckets") or []
                        for item in ("--length-bucket", str(bucket))
                    ],
                ],
            ),
            env=env,
            timeout=timeout,
            log_path=log_path,
        )
    shortcut_queries = _question_only_shortcut_queries(job)
    if shortcut_queries:
        return shortcut_queries
    rankings = projected / "rankings.jsonl"
    if candidates.is_file() and not rankings.is_file():
        _run_command(
            _trust_command(
                python,
                job,
                ("ranker",),
                [
                    str(python),
                    str(ROOT / "scripts" / "rank_candidates_dense.py"),
                    "--candidates",
                    str(candidates),
                    "--output",
                    str(rankings),
                    "--model-id",
                    minilm_model,
                    "--revision",
                    minilm_revision,
                ],
            ),
            env=env,
            timeout=timeout,
            log_path=log_path,
        )
    audits = projected / "audits.jsonl"
    audit_candidates = parent_candidates if parent_candidates is not None else candidates
    audit_sidecar = None
    for name in (
        "TASK_REPLAY_SIDECAR_V3.json",
        str(job.get("sidecar_name") or ""),
        "TASK_REPLAY_SIDECAR.json",
    ):
        if not name:
            continue
        for base in (projected, parents_dir):
            if base is None:
                continue
            candidate_sidecar = base / name
            if candidate_sidecar.is_file():
                audit_sidecar = candidate_sidecar
                break
        if audit_sidecar is not None:
            break
    if parent_sidecar is not None and audit_sidecar is None:
        audit_sidecar = parent_sidecar
    if (
        rankings.is_file()
        and not audits.is_file()
        and audit_candidates is not None
        and audit_candidates.is_file()
        and audit_sidecar is not None
        and audit_sidecar.name == "TASK_REPLAY_SIDECAR_V3.json"
    ):
        _run_command(
            _trust_command(
                python,
                job,
                ("source", "candidate", "ranker", "auditor"),
                [
                    str(python),
                    str(ROOT / "scripts" / "project_task_candidate_views.py"),
                    "--candidates",
                    str(audit_candidates),
                    "--sidecar",
                    str(audit_sidecar),
                    "--output-dir",
                    str(projected),
                    "--audit-only",
                    "--rankings",
                    str(rankings),
                    "--audit-workers",
                    str(audit_workers),
                    *[
                        item
                        for bucket in job.get("primary_buckets") or []
                        for item in ("--length-bucket", str(bucket))
                    ],
                ],
            ),
            env=env,
            timeout=timeout,
            log_path=log_path,
        )


def _execute_job(
    job: dict[str, Any],
    *,
    python: Path,
    env: dict[str, str],
    timeout: float,
    resume: bool,
    minilm_model: str,
    minilm_revision: str,
    ledger_path: Path,
    execute: bool,
    audit_workers: int,
) -> dict[str, Any]:
    job_id = str(job["job_id"])
    pad_errors = job_pad_errors(job)
    if pad_errors:
        row = {
            "schema_version": LEDGER_SCHEMA,
            "job_id": job_id,
            "status": "blocked",
            "route": ROUTE_BLOCKED_INSUFFICIENT_UNIQUE,
            "errors": pad_errors,
            "production_eligible": False,
        }
        if execute:
            _append_ledger(ledger_path, row)
        return row
    input_sha256 = _job_input_sha256(job)
    shortcut_queries = _question_only_shortcut_queries(job)
    if shortcut_queries:
        return _record_question_only_block(
            job, shortcut_queries, ledger_path=ledger_path, execute=execute
        )
    n_parent_artifacts = _parent_artifact_count(job)
    if n_parent_artifacts is not None:
        explosion = parent_artifact_explosion_error(n_parent_artifacts)
        if explosion:
            row = {
                "schema_version": LEDGER_SCHEMA,
                "job_id": job_id,
                "status": "blocked",
                "route": ROUTE_BLOCKED_PARENT_EXPLOSION,
                "errors": [explosion],
                "production_eligible": False,
            }
            if execute:
                receipt = {
                    "schema_version": FILTER_RECEIPT_SCHEMA,
                    "job_id": job_id,
                    "route": ROUTE_BLOCKED_PARENT_EXPLOSION,
                    "n_audits": 0,
                    "strict_eligible": False,
                    "auto_promote": False,
                    "production_eligible": False,
                    "input_sha256": input_sha256 or "",
                    "error": explosion,
                }
                _write_filter_receipt(job, receipt)
                _append_ledger(ledger_path, row)
            return row
    if resume and input_sha256 and filter_receipt_complete(
        job, expected_input_sha256=input_sha256
    ):
        return {
            "job_id": job_id,
            "status": "resumed",
            "production_eligible": False,
        }
    audits_path = Path(job["projected_dir"]) / "audits.jsonl"
    generate_script = job.get("generate_script")
    generate_config = job.get("generate_config")
    compiler_missing = bool(generate_script) and (
        not Path(str(generate_script)).is_file()
        or not generate_config
        or not Path(str(generate_config)).is_file()
    )
    if not compiler_missing and generate_config:
        compiler_missing = _signed_workflow_missing(str(generate_config))
    if execute and not audits_path.is_file() and not compiler_missing:
        try:
            shortcut_queries = _run_synthesis_stages(
                job,
                python=python,
                env=env,
                timeout=timeout,
                minilm_model=minilm_model,
                minilm_revision=minilm_revision,
                ledger_path=ledger_path,
                audit_workers=audit_workers,
            )
            if shortcut_queries:
                return _record_question_only_block(
                    job, shortcut_queries, ledger_path=ledger_path, execute=execute
                )
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            blocker = _log_blocker(_job_log_path(ledger_path, job_id))
            if blocker is not None:
                input_sha256 = _job_input_sha256(job) or ""
                receipt = {
                    "schema_version": FILTER_RECEIPT_SCHEMA,
                    "job_id": job_id,
                    "route": blocker,
                    "n_audits": 0,
                    "strict_eligible": False,
                    "auto_promote": False,
                    "production_eligible": False,
                    "input_sha256": input_sha256,
                    "error": blocker,
                }
                _write_filter_receipt(job, receipt)
                row = {
                    "schema_version": LEDGER_SCHEMA,
                    "job_id": job_id,
                    "status": "blocked",
                    "route": blocker,
                    "production_eligible": False,
                }
                _append_ledger(ledger_path, row)
                return row
            row = {
                "schema_version": LEDGER_SCHEMA,
                "job_id": job_id,
                "status": "failed",
                "error": str(error),
                "production_eligible": False,
            }
            _append_ledger(ledger_path, row)
            return row
    audits_path = Path(job["projected_dir"]) / "audits.jsonl"
    if audits_path.is_file():
        input_sha256 = _job_input_sha256(job) or ""
        if execute:
            classified = _classify_existing_audits(job, input_sha256)
            row = {
                "schema_version": LEDGER_SCHEMA,
                "job_id": job_id,
                "status": "classified",
                "route": classified["route"],
                "n_audits": classified["n_audits"],
                "strict_eligible": classified["strict_eligible"],
                "already_promoted": bool(job.get("already_promoted")),
                "production_eligible": False,
            }
            _append_ledger(ledger_path, row)
            return row
        return {
            "job_id": job_id,
            "status": "planned_classify",
            "production_eligible": False,
        }
    if compiler_missing:
        return {
            "job_id": job_id,
            "status": "pending_compiler",
            "production_eligible": False,
        }
    projected = Path(job["projected_dir"])
    v3_sidecar = (projected / "TASK_REPLAY_SIDECAR_V3.json").is_file()
    parents_dir = Path(job["parents_dir"]) if job.get("parents_dir") else None
    if parents_dir is not None:
        v3_sidecar = v3_sidecar or (parents_dir / "TASK_REPLAY_SIDECAR_V3.json").is_file()
    if (projected / "rankings.jsonl").is_file() and not v3_sidecar:
        return {
            "job_id": job_id,
            "status": "pending_adapter",
            "production_eligible": False,
        }
    if (
        parents_dir is not None
        and (parents_dir / "parents.jsonl").is_file()
        and not v3_sidecar
    ):
        return {
            "job_id": job_id,
            "status": "pending_adapter",
            "production_eligible": False,
        }
    if not job.get("parents_dir"):
        return {
            "job_id": job_id,
            "status": "pending_compiler",
            "production_eligible": False,
        }
    return {
        "job_id": job_id,
        "status": "planned_synthesize",
        "production_eligible": False,
    }


def run_pipeline(
    catalog_path: Path,
    *,
    workers: int = 4,
    timeout_seconds: float = 3600.0,
    execute: bool = False,
    resume: bool = False,
    audit_workers: int = 2,
    job_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    if (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= _MAX_WORKERS
    ):
        raise PipelineCatalogError(f"workers must be between 1 and {_MAX_WORKERS}")
    if (
        isinstance(audit_workers, bool)
        or not isinstance(audit_workers, int)
        or not 1 <= audit_workers <= _MAX_WORKERS
    ):
        raise PipelineCatalogError(
            f"audit_workers must be between 1 and {_MAX_WORKERS}"
        )
    catalog = load_catalog(catalog_path)
    python = Path(str(catalog.get("python") or sys.executable))
    env = os.environ.copy()
    hf_home = catalog.get("hf_home")
    if hf_home:
        env["HF_HOME"] = str(hf_home)
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    ledger_path = Path(
        str(catalog.get("ledger") or (ROOT / "reports" / "p57_pipeline" / "ledger.jsonl"))
    )
    minilm_model = str(catalog.get("minilm_model_id") or MINILM_MODEL)
    minilm_revision = str(catalog.get("minilm_revision") or MINILM_REVISION)
    jobs = list(catalog["jobs"])
    if job_ids:
        wanted = set(job_ids)
        jobs = [job for job in jobs if job["job_id"] in wanted]
        missing = wanted - {job["job_id"] for job in jobs}
        if missing:
            raise PipelineCatalogError(
                "unknown job_id: " + ",".join(sorted(missing))
            )
    if not jobs:
        raise PipelineCatalogError("no catalog jobs selected")
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as executor:
        futures = {
            executor.submit(
                _execute_job,
                job,
                python=python,
                env=env,
                timeout=float(timeout_seconds),
                resume=resume,
                minilm_model=minilm_model,
                minilm_revision=minilm_revision,
                ledger_path=ledger_path,
                execute=execute,
                audit_workers=audit_workers,
            ): job["job_id"]
            for job in jobs
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except (OSError, ValueError, TypeError) as error:
                row = {
                    "schema_version": LEDGER_SCHEMA,
                    "job_id": futures[future],
                    "status": "failed",
                    "error": str(error),
                    "production_eligible": False,
                }
                if execute:
                    failed_job = next(job for job in jobs if job["job_id"] == futures[future])
                    try:
                        _write_filter_receipt(
                            failed_job,
                            {
                                **row,
                                "schema_version": FILTER_RECEIPT_SCHEMA,
                                "strict_eligible": False,
                                "auto_promote": False,
                                "question_only_check_revision": QUESTION_ONLY_CHECK_REVISION,
                            },
                        )
                    except (OSError, ValueError) as receipt_error:
                        row["filter_receipt_error"] = str(receipt_error)
                    _append_ledger(ledger_path, row)
                results.append(row)
    results.sort(key=lambda item: str(item.get("job_id") or ""))
    statuses = {str(item.get("status") or "") for item in results}
    pending = {
        "pending_compiler",
        "pending_adapter",
        "planned_synthesize",
        "planned_classify",
    }
    if any(status == "failed" for status in statuses):
        status = "failed"
    elif statuses & pending:
        status = "draining" if execute else "planned"
    elif execute:
        status = "success"
    else:
        status = "planned"
    return {
        "schema_version": PIPELINE_SCHEMA,
        "status": status,
        "workers": workers,
        "audit_workers": audit_workers,
        "execute": execute,
        "production_eligible": False,
        "jobs": results,
    }


def _acquire_pipeline_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise SystemExit(f"P57 pipeline already running: {lock_path}") from error
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--audit-workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=180.0)
    parser.add_argument("--job-id", action="append", default=[])
    args = parser.parse_args()
    if args.resume and not args.execute:
        raise SystemExit("--resume requires --execute")
    lock_handle = None
    if args.execute:
        catalog = load_catalog(args.catalog)
        ledger_path = Path(
            str(
                catalog.get("ledger")
                or (ROOT / "reports" / "p57_pipeline" / "ledger.jsonl")
            )
        )
        lock_handle = _acquire_pipeline_lock(ledger_path.parent / "pipeline.lock")

    def once() -> dict[str, Any]:
        result = run_pipeline(
            args.catalog,
            workers=args.workers,
            timeout_seconds=args.timeout_seconds,
            execute=args.execute,
            resume=args.resume,
            audit_workers=args.audit_workers,
            job_ids=tuple(args.job_id),
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        pending = sum(
            1
            for job in result["jobs"]
            if job.get("status")
            in {
                "pending_compiler",
                "pending_adapter",
                "planned_synthesize",
                "planned_classify",
            }
        )
        classified = sum(
            1
            for job in result["jobs"]
            if job.get("status") in {"classified", "resumed"}
        )
        blocked = sum(1 for job in result["jobs"] if job.get("status") == "blocked")
        print(
            "P57_PIPELINE_TICK "
            + json.dumps(
                {
                    "status": result["status"],
                    "classified": classified,
                    "pending": pending,
                    "blocked": blocked,
                    "prompt": "Drain P57 task pipeline: synthesize packed parents, dense-audit, classify window routes, do not auto-promote or pad.",
                }
            ),
            flush=True,
        )
        return result

    try:
        result = once()
        if args.watch:
            while True:
                time.sleep(float(args.interval_seconds))
                result = once()
        if result["status"] == "failed":
            return 1
        return 0
    finally:
        if lock_handle is not None:
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
