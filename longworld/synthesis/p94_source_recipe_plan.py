"""Compile a frozen source support matrix into native, split-safe batch jobs.

Only probed Wiki recipes wired to the P76 executor become executable. Other
observed operations remain visible in the cell ledger without being relabelled
as new candidates. Length policies describe native renderer behavior, not a
guarantee that a final reader lands in a particular token bucket.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from longworld.synthesis.source_batch_plan import RECIPES

SCHEMA = "longworld.p94-source-recipe-plan.v1"
MATRIX_SCHEMA = "longworld.p92-source-router.v1.result"
NATIVE_POOL_SCHEMA = "longworld.source-batch-pool.v2"
NATIVE_CONFIG_SCHEMA = "longworld.p76-source-batch-config.v1"
ROOT = Path(__file__).resolve().parents[2]
LENGTH_POLICIES = (
    "native_whole_pages",
    "paired_32k_64k_or_native",
    "target_32768",
    "target_65536",
    "target_131072",
    "target_262144",
)
GENERIC_YEAR_COLUMNS = frozenset(
    {"Year", "Established", "Opened", "Built", "Completion"}
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pinned(root: Path, pin: dict[str, str]) -> Path:
    if (
        not isinstance(pin, dict)
        or not {"path", "sha256"} <= set(pin)
        or not isinstance(pin["path"], str)
        or not isinstance(pin["sha256"], str)
    ):
        raise ValueError("input pin needs path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("input pin must be project-relative")
    path = root / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"input pin mismatch: {relative}")
    return path


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _cell_id(world_group: str, operation: str, length: str) -> str:
    return hashlib.sha256(
        _dump([world_group, operation, length]).encode("utf-8")
    ).hexdigest()[:20]


def _native_jobs(pool: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    from longworld.synthesis.source_batch_plan import expand
    from scripts.run_source_pool_batch import _snapshot

    jobs, _ = expand(pool, root, _snapshot)
    return jobs


def compile_plan(config: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    """Return a legal-cell ledger and a P76-compatible native job config."""
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong source recipe planner schema")
    requested = config.get("length_policies")
    if (
        not isinstance(requested, list)
        or not requested
        or len(requested) != len(set(requested))
        or any(policy not in LENGTH_POLICIES for policy in requested)
    ):
        raise ValueError("invalid requested length policies")
    verified: dict[str, str] = {}

    def pinned(pin: dict[str, str]) -> Path:
        if not isinstance(pin, dict) or not isinstance(pin.get("path"), str):
            return _pinned(root, pin)
        known = verified.get(pin["path"])
        if known is not None:
            if known != pin["sha256"]:
                raise ValueError(f"conflicting input pin: {pin['path']}")
            return root / pin["path"]
        path = _pinned(root, pin)
        verified[pin["path"]] = pin["sha256"]
        return path

    matrix_path = pinned(config["source_matrix"])
    pool_path = pinned(config["native_source_pool"])
    matrix, pool = _read(matrix_path), _read(pool_path)
    if (
        matrix.get("schema") != MATRIX_SCHEMA
        or pool.get("schema") != NATIVE_POOL_SCHEMA
    ):
        raise ValueError("unsupported matrix or native pool schema")
    prior = pool["prior_source_manifest"]
    pinned(prior)
    if matrix.get("input_sha256", {}).get(prior["path"]) != prior["sha256"]:
        raise ValueError("matrix and native pool prior source manifest disagree")
    sources = matrix.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("matrix lacks sources")
    prior_supported: set[tuple[str, str]] = set()
    if "prior_source_matrix" in config:
        previous = _read(pinned(config["prior_source_matrix"]))
        if previous.get("schema") != MATRIX_SCHEMA:
            raise ValueError("unsupported prior source matrix schema")
        previous_by_world = {row["world_group_id"]: row for row in previous["sources"]}
        for source in sources:
            older = previous_by_world.get(source["world_group_id"])
            if older and (
                older["source_kind"] != source["source_kind"]
                or older["split"] != source["split"]
                or older["source_pins"] != source["source_pins"]
            ):
                raise ValueError("prior world identity changed in source matrix")
        prior_supported = {
            (row["world_group_id"], cell["operation"])
            for row in previous["sources"]
            for cell in row["cells"]
            if cell["status"] == "probe_supported"
        }
    by_world = {}
    for source in sources:
        world_group = source["world_group_id"]
        if world_group in by_world:
            raise ValueError("duplicate world group in source matrix")
        by_world[world_group] = source
        if source["split"] not in {"train", "eval", "conflict"}:
            raise ValueError("invalid matrix split")
        for pin in source["source_pins"]:
            pinned(pin)
    pool_by_sha = {}
    for source in pool["sources"]:
        digest = source["snapshot"]["sha256"]
        if digest in pool_by_sha:
            raise ValueError("duplicate native pool snapshot")
        pool_by_sha[digest] = source
    native_jobs = _native_jobs(pool, root)
    from scripts.run_p76_source_batch import _job_id

    jobs_by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for job in native_jobs:
        digest = job["snapshot"]["sha256"]
        source = pool_by_sha[digest]
        world_group = job["snapshot"]["snapshot_id"]
        record = by_world.get(world_group)
        if (
            record is None
            or record["source_kind"] != "real_wiki"
            or record["split"] != job["split"]
            or record["split"] == "conflict"
            or record["source_pins"][0] != source["snapshot"]
            or (job["domain"], job["topic"])
            not in {
                (label["domain"], label["topic"]) for label in record["domain_topics"]
            }
        ):
            raise ValueError(f"native job contradicts source matrix: {job['name']}")
        if not any(
            cell["operation"] == job["recipe"] and cell["status"] == "probe_supported"
            for cell in record["cells"]
        ):
            raise ValueError(f"native job lacks positive matrix probe: {job['name']}")
        jobs_by_cell[(world_group, job["recipe"])].append(job)
    # Router blocks cross-split page sharing. Recheck it here because a stale
    # matrix or pool must never silently compile such a plan.
    title_splits: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        if source["source_kind"] == "real_wiki" and source["split"] != "conflict":
            for doc in source["document_identities"]:
                title_splits[doc["title"].casefold()].add(source["split"])
    if any(len(splits) > 1 for splits in title_splits.values()):
        raise ValueError("same Wiki title crosses train and eval matrices")

    cells = []
    potential_unwired = []
    executable_names = set()
    for source in sorted(sources, key=lambda row: row["world_group_id"]):
        world_group = source["world_group_id"]
        if source["source_kind"] == "real_wiki":
            year_columns = sorted(
                GENERIC_YEAR_COLUMNS.intersection(
                    source.get("source_shape", {}).get("table_columns", [])
                )
            )
            if year_columns:
                potential_unwired.append(
                    {
                        "world_group_id": world_group,
                        "operation": "generic_year_table_interval",
                        "status": "unprobed_no_native_batch_compiler",
                        "basis": "column names in source shape; complete table not established",
                        "year_columns": year_columns,
                    }
                )
        for native_cell in sorted(source["cells"], key=lambda row: row["operation"]):
            operation = native_cell["operation"]
            jobs = jobs_by_cell.get((world_group, operation), [])
            for length in requested:
                reason = native_cell.get("reason", "")
                status = native_cell["status"]
                if source["split"] == "conflict" or status == "blocked_split_conflict":
                    status = "blocked_split_conflict"
                elif source["source_kind"] != "real_wiki":
                    status = "observed_only_no_native_batch_compiler"
                    reason = "historical candidates do not supply a P76 native job"
                elif operation not in RECIPES:
                    status = "no_native_batch_compiler"
                    reason = "operation is not wired to the P76 native executor"
                elif status != "probe_supported":
                    status = "unsupported_source_structure"
                elif length != RECIPES[operation]:
                    status = "unsupported_length_policy"
                    reason = "native recipe does not implement this length target"
                elif not jobs:
                    status = "native_probe_disagreement"
                    reason = (
                        "router probe was positive but native planner yielded no job"
                    )
                else:
                    status = "executable_native_job"
                    reason = ""
                    executable_names.update(job["name"] for job in jobs)
                cells.append(
                    {
                        "cell_id": _cell_id(world_group, operation, length),
                        "world_group_id": world_group,
                        "source_kind": source["source_kind"],
                        "split": source["split"],
                        "domain_topics": source.get("domain_topics", []),
                        "operation": operation,
                        "capability": native_cell.get("capability"),
                        "length_policy": length,
                        "status": status,
                        "reason": reason,
                        "native_job_ids": sorted(_job_id(job) for job in jobs)
                        if status == "executable_native_job"
                        else [],
                    }
                )
    if len({cell["cell_id"] for cell in cells}) != len(cells):
        raise ValueError("duplicate source/task/length cell")
    selected_jobs = sorted(
        (job for job in native_jobs if job["name"] in executable_names),
        key=_job_id,
    )
    new_jobs = [
        job
        for job in selected_jobs
        if (job["snapshot"]["snapshot_id"], job["recipe"]) not in prior_supported
    ]
    statuses = Counter(cell["status"] for cell in cells)
    return {
        "schema": SCHEMA + ".result",
        "source_matrix": config["source_matrix"],
        "native_source_pool": config["native_source_pool"],
        "prior_source_matrix": config.get("prior_source_matrix"),
        "length_policies": requested,
        "cells": cells,
        "potential_unwired_recipes": potential_unwired,
        "cell_status_counts": dict(sorted(statuses.items())),
        "native_job_config": {
            "schema": NATIVE_CONFIG_SCHEMA,
            "prior_source_manifest": prior,
            "jobs": selected_jobs,
        },
        "new_native_job_config": {
            "schema": NATIVE_CONFIG_SCHEMA,
            "prior_source_manifest": prior,
            "jobs": new_jobs,
        },
        "native_jobs": len(selected_jobs),
        "new_native_jobs": len(new_jobs),
        "source_groups": len(sources),
        "train_ready": False,
    }
