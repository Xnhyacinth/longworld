#!/usr/bin/env python3
"""Run registered source-taskbank build/replay stages without promoting their claims.

Reuses the existing isolated source-role runner. Each native domain validator
owns its semantic and dependency contract; successful execution is not a strict
or production certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.provenance import _read_regular_file
from scripts.prepare_p64_training import data_mount_path

SCHEMA = "longworld.source-taskbank-pipeline.v1"


def json_file(path):
    value = json.loads(_read_regular_file(path, 16_000_000))
    if not isinstance(value, dict):
        raise TypeError("pipeline JSON must be an object")
    return value


def project_path(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("pipeline paths must be project-relative")
    return data_mount_path(ROOT / path)


def digest(path):
    return hashlib.sha256(_read_regular_file(path, 512_000_000)).hexdigest()


def run_pipeline(catalog_path, report_root, workers):
    catalog_raw = _read_regular_file(catalog_path, 1_000_000)
    catalog = json.loads(catalog_raw)
    if catalog.get("schema_version") != SCHEMA or not catalog.get("jobs"):
        raise ValueError("invalid source-taskbank pipeline catalog")
    if not 1 <= workers <= 8:
        raise ValueError("pipeline worker count outside 1..8")
    names, outputs, jobs = set(), set(), []
    for job in catalog["jobs"]:
        name = job["job_id"]
        if not re.fullmatch(r"[a-z0-9_-]+", name) or name in names:
            raise ValueError("invalid or duplicate pipeline job")
        names.add(name)
        script, config, output = (
            project_path(job[k]) for k in ("script", "config", "output")
        )
        if not script.is_relative_to(ROOT / "scripts") or script.suffix != ".py":
            raise ValueError("job must use a reviewed repository Python script")
        if output in outputs:
            raise ValueError("jobs cannot share output directories")
        outputs.add(output)
        pins = {
            str(p): digest(p)
            for p in (
                script,
                config,
                *(project_path(p) for p in job.get("input_files", [])),
            )
        }
        if job["input_sha256"] != pins:
            raise ValueError("catalog input/code pins do not match")
        receipt_name = job.get("receipt", "BUILD_RECEIPT.json")
        if Path(receipt_name).name != receipt_name:
            raise ValueError("receipt must be a direct output member")
        jobs.append((job, script, config, output, pins))
    report_root.mkdir(parents=True, exist_ok=False)

    def execute(item):
        job, script, config, output, pins = item
        name = job["job_id"]
        receipt = output / job.get("receipt", "BUILD_RECEIPT.json")
        env = dict(os.environ)
        env.update(CUDA_VISIBLE_DEVICES="", TOKENIZERS_PARALLELISM="false")
        if "public_policy_sha256" in job:
            values = job["public_policy_sha256"]
            if not values or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in values):
                raise ValueError("invalid public policy pins")
            env["LONGWORLD_PUBLIC_POLICY_SHA256"] = ",".join(values)
            client = job["source_client_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", client):
                raise ValueError("invalid source client pin")
            env["LONGWORLD_GH_BINARY_SHA256"] = client
        pass_keys = (
            "CUDA_VISIBLE_DEVICES",
            "TOKENIZERS_PARALLELISM",
            "HF_HOME",
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "LONGWORLD_PUBLIC_POLICY_SHA256",
            "LONGWORLD_GH_BINARY_SHA256",
        )
        if job.get("trust_file"):
            command = [
                sys.executable,
                str(ROOT / "scripts/run_with_local_probe_trust.py"),
                "--trust-file",
                job["trust_file"],
                "--role",
                "source",
            ]
        elif job.get("source_authority") == "official_hash_pinned":
            # This native source verifier uses official endpoint/byte pins, not
            # an invented local signature. Pass only the required runtime env.
            env = {
                key: value
                for key, value in env.items()
                if key in {*pass_keys, "PATH", "LANG", "LC_ALL", "TZ"}
            }
            command = []
        else:
            raise ValueError("job source authority is missing")
        for key in pass_keys:
            if key in env and command:
                command += ["--pass-env", key]
        command += (["--"] if command else []) + [
            sys.executable,
            str(script),
            "--config",
            str(config),
            "--output",
            str(output),
        ]
        if job.get("stage_workers"):
            if (
                type(job["stage_workers"]) is not int
                or not 1 <= job["stage_workers"] <= 8
            ):
                raise ValueError("invalid native stage workers")
            command += ["--workers", str(job["stage_workers"])]
        if output.exists() and not receipt.is_file():
            return {
                "job_id": name,
                "status": "held_incomplete_output",
                "output": str(output),
            }
        stages = (
            [("validate", ["--validate"])]
            if receipt.exists()
            else [("build", []), ("validate", ["--validate"])]
        )
        for stage, flags in stages:
            with (
                (report_root / f"{name}.{stage}.stdout").open("x") as stdout,
                (report_root / f"{name}.{stage}.stderr").open("x") as stderr,
            ):
                result = subprocess.run(
                    command + flags,
                    cwd=ROOT,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=14400,
                    check=False,
                )
            if result.returncode:
                return {
                    "job_id": name,
                    "status": "failed",
                    "stage": stage,
                    "returncode": result.returncode,
                }
        if any(digest(Path(path)) != sha for path, sha in pins.items()):
            raise ValueError("pipeline input changed during execution")
        value = json_file(receipt)
        if value.get("schema_version") != job["receipt_schema"]:
            raise ValueError("unexpected native result receipt")
        return {
            "job_id": name,
            "status": "native_replay_verified",
            "output": str(output),
            "receipt": str(receipt),
            "receipt_sha256": digest(receipt),
            "receipt_schema": value["schema_version"],
            "strict_eligibility_inferred": False,
            "production_eligible": False,
            "input_sha256": pins,
        }

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(execute, item): item[0]["job_id"] for item in jobs}
        for future in as_completed(futures):
            try:
                result = future.result()
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                result = {
                    "job_id": futures[future],
                    "status": "failed",
                    "error": type(error).__name__ + ": " + str(error),
                }
            results.append(result)
            print(json.dumps(result), flush=True)
    result = {
        "schema_version": "longworld.source-taskbank-pipeline-receipt.v1",
        "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
        "jobs": sorted(results, key=lambda r: r["job_id"]),
        "production_eligible": False,
        "scope": "native build/replay execution only; each task's quality remains governed by its native receipt",
    }
    (report_root / "PIPELINE_RECEIPT.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--report-root", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    result = run_pipeline(args.catalog, data_mount_path(args.report_root), args.workers)
    if any(j["status"] != "native_replay_verified" for j in result["jobs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
