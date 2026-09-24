"""Plan and execute a pinned source pool across supported reader operations.

This is the source-facing control plane: source/domain/topic and task quotas are
substituted in one config; native probes decide legal cells; the P76 executor
keeps its existing source, reader, answer, receipt and split checks.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.source_batch_merge import merge
from longworld.synthesis.source_batch_plan import SCHEMA, expand
from scripts.run_p76_source_batch import (
    CONFIG_SCHEMA,
    _dump,
    _json,
    _sha,
    _write_new,
)
from scripts.run_p76_source_batch import run as run_jobs

RESULT_SCHEMA = "longworld.source-pool-batch.v2"


def _reason_class(reason: str) -> str:
    if reason.startswith("no closed Established table"):
        return "no_closed_established_table"
    if reason.startswith("too few clean table rows"):
        return "too_few_clean_table_rows"
    return reason


def _snapshot(root: Path, pin: dict[str, Any]) -> dict[str, Any]:
    if not all(key in pin for key in ("path", "sha256")):
        raise ValueError("snapshot pin incomplete")
    path = Path(pin["path"])
    path = path if path.is_absolute() else root / path
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"snapshot pin mismatch: {pin['path']}")
    value = _json(path)
    actual = value.get("source", {}).get("revisions")
    if (
        not value.get("snapshot_id")
        or not isinstance(actual, dict)
        or {doc["title"] for doc in value.get("documents", [])} != set(actual)
    ):
        raise ValueError(f"snapshot metadata mismatch: {pin['path']}")
    return value


def _planned(config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong source pool schema")
    jobs, skipped = expand(config, ROOT, _snapshot)
    return {
        "schema": CONFIG_SCHEMA,
        "prior_source_manifest": config["prior_source_manifest"],
        "jobs": jobs,
    }, skipped


def run(
    config_path: Path, output_dir: Path, *, workers: int = 2, resume: bool = False
) -> dict[str, Any]:
    config = _json(config_path)
    job_config, skipped = _planned(config)
    plan = {
        "schema": RESULT_SCHEMA + ".plan",
        "source_pool_sha256": _sha(config_path),
        "planner_sha256": _sha(ROOT / "longworld/synthesis/source_batch_plan.py"),
        "merge_sha256": _sha(ROOT / "longworld/synthesis/source_batch_merge.py"),
        "runner_sha256": _sha(Path(__file__)),
        "job_config": job_config,
        "unsupported_cells": skipped,
    }
    plan_path = output_dir / "plan.json"
    job_path = output_dir / "job_config.json"
    if output_dir.exists():
        if not resume or _json(plan_path) != plan or _json(job_path) != job_config:
            raise ValueError("existing pool output does not match pinned plan")
    else:
        if resume:
            raise ValueError("cannot resume missing source pool output")
        output_dir.mkdir(parents=True)
        _write_new(plan_path, plan)
        _write_new(job_path, job_config)
    batch = run_jobs(job_path, output_dir / "batch", workers=workers, resume=resume)
    merged_dir = output_dir / "merged"
    if merged_dir.exists():
        merged = _json(merged_dir / "manifest.json")
        if not resume or any(
            _sha(merged_dir / name) != digest
            for name, digest in merged["files_sha256"].items()
        ):
            raise ValueError("merged output already exists or changed")
    else:
        merged = merge(output_dir / "batch/jobs", merged_dir, batch["candidate_rows"])
    zero_yield_jobs = [
        job_id
        for job_id in sorted(batch["job_receipt_sha256"])
        if _json(output_dir / "batch/jobs" / job_id / "receipt.json")["candidate_rows"]
        == 0
    ]
    result = {
        "schema": RESULT_SCHEMA,
        "source_pool_sha256": _sha(config_path),
        "plan_sha256": _sha(plan_path),
        "batch_manifest_sha256": _sha(output_dir / "batch/batch_manifest.json"),
        "merged_manifest_sha256": _sha(merged_dir / "manifest.json"),
        "source_pool_entries": len(config["sources"]),
        "planned_jobs": len(job_config["jobs"]),
        "jobs_with_candidates": len(job_config["jobs"]) - len(zero_yield_jobs),
        "zero_yield_jobs": zero_yield_jobs,
        "unsupported_cells": len(skipped),
        "unsupported_reasons": dict(
            sorted(Counter(_reason_class(cell["reason"]) for cell in skipped).items())
        ),
        "domains": dict(
            sorted(Counter(job["domain"] for job in job_config["jobs"]).items())
        ),
        "task_operations": batch["recipes"],
        "candidate_views": merged["candidate_views"],
        "independent_tasks": merged["independent_tasks"],
        "tasks_in_multiple_source_groups": merged["tasks_in_multiple_source_groups"],
        "duplicate_views_removed": merged["duplicate_views_removed"],
        "rejected_rows": batch["rejected_rows"],
        "train_ready": False,
    }
    result_path = output_dir / "result.json"
    if result_path.exists():
        if _json(result_path) != result:
            raise ValueError("source pool result drift on resume")
    else:
        _write_new(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            run(args.config, args.output_dir, workers=args.workers, resume=args.resume)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
