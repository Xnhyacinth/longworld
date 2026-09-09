#!/usr/bin/env python3
"""Run isolated source-world task compilation and persisted-export verification."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_batch(catalog_path: Path, output: Path, workers: int) -> dict:
    catalog_raw = catalog_path.read_bytes()
    catalog = json.loads(catalog_raw)
    jobs = catalog["jobs"]
    if (
        catalog.get("schema_version")
        != "longworld.p63-finance-taskbank-source-catalog.v1"
        or not jobs
    ):
        raise ValueError("invalid taskbank batch catalog")
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")
    names, groups = set(), {}
    for job in jobs:
        name = job["issuer"]
        if (
            not name
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name)
            or name in names
        ):
            raise ValueError("invalid or duplicate job name")
        names.add(name)
        config_raw = (ROOT / job["config"]).read_bytes()
        config = json.loads(config_raw)
        if job["config_sha256"] != hashlib.sha256(config_raw).hexdigest():
            raise ValueError("catalog config hash mismatch")
        if (
            config["source_manifest"] != job["source_manifest"]
            or hashlib.sha256((ROOT / job["source_manifest"]).read_bytes()).hexdigest()
            != job["source_manifest_sha256"]
        ):
            raise ValueError("catalog source manifest binding mismatch")
        group, split = config["split_group_id"], config["split"]
        if group in groups:
            raise ValueError(
                "duplicate source entity in batch; consolidate its task bank"
            )
        groups[group] = split
    output.mkdir(parents=True, exist_ok=False)

    def run(job: dict) -> dict:
        name = job["issuer"]
        base = [
            sys.executable,
            str(ROOT / "scripts/run_with_local_probe_trust.py"),
            "--trust-file",
            job["trust_file"],
            "--role",
            "source",
        ]
        for env in (
            "HF_HOME",
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "TOKENIZERS_PARALLELISM",
        ):
            if env in os.environ:
                base.extend(["--pass-env", env])
        base += [
            "--",
            sys.executable,
            str(ROOT / "scripts/materialize_finance_taskbank.py"),
            "--config",
            str(ROOT / job["config"]),
            "--output",
            str(output / name),
        ]
        for stage, extra in (("build", []), ("validate", ["--validate"])):
            log_path = output / f"{name}.{stage}.log"
            try:
                with log_path.open("x") as log:
                    process = subprocess.run(
                        base + extra,
                        cwd=ROOT,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=7200,
                        check=False,
                    )
            except subprocess.TimeoutExpired:
                return {
                    "name": name,
                    "status": "blocked",
                    "stage": stage,
                    "reason": "timeout",
                    "log": log_path.name,
                }
            if process.returncode:
                return {
                    "name": name,
                    "status": "blocked",
                    "stage": stage,
                    "returncode": process.returncode,
                    "log": log_path.name,
                }
        if (
            hashlib.sha256((ROOT / job["config"]).read_bytes()).hexdigest()
            != job["config_sha256"]
        ):
            return {"name": name, "status": "blocked", "stage": "input_changed"}
        if (
            hashlib.sha256((ROOT / job["source_manifest"]).read_bytes()).hexdigest()
            != job["source_manifest_sha256"]
        ):
            return {"name": name, "status": "blocked", "stage": "source_changed"}
        receipt_path = output / name / "BUILD_RECEIPT.json"
        receipt = json.loads(receipt_path.read_text())
        if receipt["source_manifest"]["sha256"] != job["source_manifest_sha256"]:
            return {
                "name": name,
                "status": "blocked",
                "stage": "receipt_source_mismatch",
            }
        return {
            "name": name,
            "status": "verified_local_candidates",
            "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "semantic_tasks": receipt["accepted_semantic_tasks"],
            "training_views": receipt["training_views"],
            "split": receipt["split"],
            "split_group_id": receipt["split_group_id"],
            "task_families": receipt["task_families"],
            "natural_length_caps": receipt["natural_length_caps"],
            "compiler_metrics": receipt["compiler_metrics"],
            "unique_contexts": receipt["unique_contexts"],
        }

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, job): job["issuer"] for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                result = {
                    "name": futures[future],
                    "status": "blocked",
                    "reason": type(error).__name__ + ": " + str(error),
                }
            results.append(result)
            print(json.dumps(result), flush=True)
    results.sort(key=lambda item: item["name"])
    summary = {
        "schema_version": "longworld.finance-taskbank-batch-receipt.v1",
        "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
        "jobs": results,
        "statuses": dict(Counter(result["status"] for result in results)),
        "accepted_semantic_tasks": sum(
            result.get("semantic_tasks", 0) for result in results
        ),
        "verified_source_entities": sum(
            result["status"] == "verified_local_candidates"
            and result["semantic_tasks"] > 0
            for result in results
        ),
        "production_eligible": False,
        "training_release_eligible": False,
        "split_scope": "isolated taskbank; do not union legacy training sources with this evaluation split",
    }
    (output / "BATCH_RECEIPT.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    result = run_batch(args.catalog.resolve(), args.output.resolve(), args.workers)
    print(json.dumps(result, indent=2))
    if result["statuses"].get("blocked"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
