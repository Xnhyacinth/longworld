"""Batch a declared selector/target matrix over pinned real issuer worlds."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.taskbank_context import (
    assemble_analyst_context,
    assemble_statement_context,
    bind_visible_evidence,
    render_document,
)
from longworld.synthesis.length_controller import get_tokenizer
from scripts.materialize_finance_taskbank_v2 import load_world
from scripts.p95_report_finance_shared import canonical
from scripts.p96_finance_factorial import SCHEMA, compile_world
from scripts.run_p95_report_finance_shared import _lines, _render, _write, sha

CODE_FILES = (
    "scripts/p96_finance_factorial.py",
    "scripts/run_p96_finance_factorial.py",
    "scripts/run_p95_report_finance_shared.py",
    "scripts/train_sft.py",
    "longworld/core/finance_taskbank.py",
    "longworld/core/taskbank_context.py",
)


def compile_issuer(
    base_config_path: Path,
    config_sha: str,
    source_sha: str,
    output: Path,
    *,
    max_tasks: int,
    answer_cap: int,
) -> dict:
    if output.exists() or sha(base_config_path) != config_sha:
        raise ValueError("existing issuer output or changed base config")
    base = json.loads(base_config_path.read_text())
    source_path = ROOT / base["source_manifest"]
    if sha(source_path) != source_sha:
        raise ValueError("source manifest pin mismatch")
    world = load_world(base)
    tasks, matrix, planning = compile_world(
        world, max_tasks=max_tasks, answer_cap=answer_cap
    )
    documents = {doc["record_id"]: doc for doc in world["documents"]}
    rendered = {identity: render_document(doc) for identity, doc in documents.items()}
    facts = {fact["fact_id"]: fact for fact in world["facts"]}
    bound = {}
    for task in tasks:
        for identity in task["fact_ids"]:
            if identity in bound:
                continue
            fact = facts[identity]
            evidence = bind_visible_evidence(
                documents[fact["record_id"]],
                rendered[fact["record_id"]],
                {**fact["span"], "value": fact["value"]},
            )
            bound[identity] = {
                **evidence,
                "fact_id": identity,
                "record_id": fact["record_id"],
                "role": fact["role"],
            }
    tokenizer = get_tokenizer()
    contexts = {}
    for name, builder in (
        ("complete_statements", assemble_statement_context),
        ("analyst_packet", assemble_analyst_context),
    ):
        contexts[name] = builder(
            world["documents"],
            rendered,
            [doc["record_id"] for doc in world["documents"]],
        )
    readers, indices, audits, rejects = [], [], [], []
    for task in tasks:
        for variant, (context, mappings) in contexts.items():
            try:
                reader, index, proof = _render(
                    task, variant, context, mappings, bound, tokenizer
                )
            except ValueError as error:
                rejects.append(
                    {
                        "semantic_task_id": task["semantic_task_id"],
                        "variant": variant,
                        "reason": str(error),
                    }
                )
                continue
            index.update(
                {
                    "source_kind": "real_finance",
                    "source_group": world["split_group_id"],
                    "domain": "finance",
                    "topic": world["issuer"]["name"],
                    "split": base["split"],
                }
            )
            proof["program"] = task["program"]
            readers.append(reader)
            indices.append(index)
            audits.append(proof)
    grouped = Counter(item["semantic_task_id"] for item in indices)
    if any(count != 2 for count in grouped.values()):
        raise ValueError("P96 task missing a complete reader-view pair")
    output.mkdir(parents=True)
    _lines(output / "reader.jsonl", readers)
    _lines(output / "sample_index.jsonl", indices)
    _lines(output / "audit.jsonl", audits)
    _lines(output / "support_matrix.jsonl", matrix)
    _write(output / "rejects.json", rejects)
    receipt = {
        "schema": SCHEMA + ".issuer",
        "source_manifest": {"path": base["source_manifest"], "sha256": source_sha},
        "base_config": {
            "path": str(base_config_path.resolve().relative_to(ROOT)),
            "sha256": config_sha,
        },
        "source_collection_id": world["source_collection_id"],
        "world_instance_id": world["world_instance_id"],
        "source_group": world["split_group_id"],
        "split": base["split"],
        "issuer": world["issuer"]["name"],
        "source_documents": len(world["documents"]),
        "source_facts": len(world["facts"]),
        "semantic_tasks": len(grouped),
        "views": len(indices),
        "operations": dict(
            sorted(Counter(item["operation"] for item in indices).items())
        ),
        "length_bins": dict(
            sorted(Counter(item["length_bin"] for item in indices).items())
        ),
        "planning": planning,
        "reader_rejects": rejects,
        "mask_checked": len(audits),
        "code_sha256": {name: sha(ROOT / name) for name in CODE_FILES},
        "file_sha256": {
            name: sha(output / name)
            for name in (
                "reader.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "support_matrix.jsonl",
                "rejects.json",
            )
        },
        "train_ready": False,
    }
    _write(output / "manifest.json", receipt)
    return receipt


def _prior_index(config: dict) -> tuple[dict[str, str], set[str]]:
    pin = config["prior_candidate_index"]
    path = ROOT / pin["path"]
    if sha(path) != pin["sha256"]:
        raise ValueError("prior candidate index pin changed")
    groups, semantic = {}, set()
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        candidate = json.loads(line)["candidate"]
        if candidate["source_kind"] != "real_finance":
            continue
        group, split = candidate["source_group"], candidate["split"]
        if group in groups and groups[group] != split:
            raise ValueError("prior finance source split conflict")
        groups[group] = split
        semantic.add(candidate["semantic_task_id"])
    return groups, semantic


def run_batch(config_path: Path, output: Path, *, workers: int) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or output.exists()
        or not 1 <= workers <= 8
    ):
        raise ValueError("invalid P96 batch configuration or existing output")
    catalog_pin = config["catalog"]
    catalog_path = ROOT / catalog_pin["path"]
    if sha(catalog_path) != catalog_pin["sha256"]:
        raise ValueError("source catalog pin changed")
    catalog = json.loads(catalog_path.read_text())
    if catalog.get("schema_version") != "longworld.finance-taskbank-long-catalog.v1":
        raise ValueError("wrong source catalog schema")
    prior_groups, prior_semantic = _prior_index(config)
    jobs = catalog["jobs"]
    for job in jobs:
        base_path = ROOT / job["config"]
        base = json.loads(base_path.read_text())
        if (
            sha(base_path) != job["config_sha256"]
            or sha(ROOT / job["source_manifest"]) != job["source_manifest_sha256"]
            or base["split_group_id"] in prior_groups
            and prior_groups[base["split_group_id"]] != base["split"]
        ):
            raise ValueError("issuer source, config or prior split pin mismatch")
    output.mkdir(parents=True)

    def run(job: dict) -> dict:
        trust = Path(os.path.expandvars(job["trust_file"]))
        if not trust.is_file() or "${" in str(trust):
            raise ValueError("source trust file unavailable")
        argv = [
            sys.executable,
            str(ROOT / "scripts/run_with_local_probe_trust.py"),
            "--trust-file",
            str(trust),
            "--role",
            "source",
        ]
        for key in (
            "HF_HOME",
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "TOKENIZERS_PARALLELISM",
        ):
            if key in os.environ:
                argv.extend(["--pass-env", key])
        argv.extend(
            [
                "--",
                sys.executable,
                str(Path(__file__)),
                "--worker",
                "--base-config",
                str(ROOT / job["config"]),
                "--config-sha",
                job["config_sha256"],
                "--source-sha",
                job["source_manifest_sha256"],
                "--max-tasks",
                str(config["max_tasks_per_world"]),
                "--answer-cap",
                str(config["answer_bucket_cap"]),
                "--output",
                str(output / job["issuer"]),
            ]
        )
        completed = subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True, timeout=3600, check=False
        )
        (output / f"{job['issuer']}.stdout.log").write_text(completed.stdout)
        (output / f"{job['issuer']}.stderr.log").write_text(completed.stderr)
        if completed.returncode:
            return {
                "issuer": job["issuer"],
                "status": "failed",
                "returncode": completed.returncode,
            }
        path = output / job["issuer"] / "manifest.json"
        receipt = json.loads(path.read_text())
        return {
            "issuer": job["issuer"],
            "status": "verified_candidate",
            "manifest_sha256": sha(path),
            "source_group": receipt["source_group"],
            "split": receipt["split"],
            "views": receipt["views"],
            "semantic_tasks": receipt["semantic_tasks"],
            "matrix_cells": receipt["planning"]["matrix_cells"],
            "cell_statuses": receipt["planning"]["cell_statuses"],
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))
    new_ids = set()
    for result in results:
        if result["status"] != "verified_candidate":
            continue
        for row in (output / result["issuer"] / "sample_index.jsonl").open():
            new_ids.add(json.loads(row)["semantic_task_id"])
    overlap = new_ids & prior_semantic
    receipt = {
        "schema": SCHEMA + ".batch",
        "config_sha256": sha(config_path),
        "catalog_sha256": sha(catalog_path),
        "prior_candidate_index": config["prior_candidate_index"],
        "jobs": results,
        "source_groups": sum(
            item["status"] == "verified_candidate" for item in results
        ),
        "candidate_views": sum(item.get("views", 0) for item in results),
        "semantic_tasks": len(new_ids),
        "exact_prior_semantic_id_overlap": len(overlap),
        "novel_exact_semantic_ids": len(new_ids - prior_semantic),
        "train_ready": False,
    }
    _write(output / "batch_manifest.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--base-config", type=Path)
    parser.add_argument("--config-sha")
    parser.add_argument("--source-sha")
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--answer-cap", type=int)
    args = parser.parse_args()
    if args.worker:
        receipt = compile_issuer(
            args.base_config,
            args.config_sha,
            args.source_sha,
            args.output,
            max_tasks=args.max_tasks,
            answer_cap=args.answer_cap,
        )
        print(
            canonical(
                {"views": receipt["views"], "semantic_tasks": receipt["semantic_tasks"]}
            )
        )
        return 0
    receipt = run_batch(args.config, args.output, workers=args.workers)
    print(canonical(receipt))
    return 0 if receipt["source_groups"] == len(receipt["jobs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
