"""Bounded, resumable scheduler for explicitly supported native synthesis recipes.

The scheduler varies only fields the native compiler already understands. It
never changes a source's domain or topic metadata to manufacture diversity.
Native compilers remain responsible for evidence, answers, masks and admission.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from longworld.synthesis.unified_candidate_contract import _answer_hash

SCHEMA = "longworld.p92-factorial-batch.v1"
SCHEMA_V2 = "longworld.p92-factorial-batch.v2"
ROOT = Path(__file__).resolve().parents[2]
CODE_PATHS = {
    "shared_state": (
        "scripts/run_p86_state_shared_batch.py",
        "longworld/synthesis/p86_state_shared_world.py",
        "scripts/run_shared_record_taskbank.py",
        "scripts/run_capability_records.py",
        "scripts/train_sft.py",
    ),
    "hybrid_rfc": (
        "scripts/run_p87_hybrid_rfc_pilot.py",
        "longworld/synthesis/length_controller.py",
        "longworld/synthesis/unified_candidate_contract.py",
        "scripts/audit_wiki_join_positions.py",
        "scripts/train_sft.py",
    ),
    "wiki_numeric": (
        "scripts/run_p91_wiki_numeric_table.py",
        "longworld/synthesis/p91_wiki_numeric_table.py",
        "longworld/synthesis/length_controller.py",
        "scripts/audit_wiki_join_positions.py",
        "scripts/train_sft.py",
    ),
    "wiki_source_pool": (
        "scripts/run_source_pool_batch.py",
        "scripts/run_p76_source_batch.py",
        "scripts/export_p76_wiki_tables.py",
        "scripts/export_p76_wiki_scan.py",
        "scripts/export_p76_wiki_lookup.py",
        "longworld/synthesis/source_batch_plan.py",
        "longworld/synthesis/source_batch_merge.py",
        "longworld/synthesis/wiki_world_bridge.py",
        "longworld/synthesis/wiki_table_tasks.py",
        "longworld/synthesis/wiki_table_scan.py",
        "longworld/synthesis/wiki_table_lookup.py",
    ),
}
KINDS = {
    "shared_state": {
        "overrides": {"seed_base", "cells"},
        "domain": "simulation",
        "topic": "shared_record_state",
        "operations": {
            "group_compare",
            "filter_aggregate",
            "asof_sum",
            "asof_complete_set",
        },
    },
    "hybrid_rfc": {
        "overrides": {"seeds", "filler_chars", "source_group"},
        "domain": "protocol",
        "topic": "http3_quic",
        "operations": {"http3_support_set", "first_control_frame_violations"},
    },
    "wiki_numeric": {
        "overrides": {"intervals"},
        "domain": None,
        "topic": None,
        "operations": {"closed_numeric_table_interval"},
    },
}
BINS = (32768, 65536, 131072, 262144)
SOURCE_KINDS = {
    "shared_state": "controlled_simulation",
    "hybrid_rfc": "grounded_simulation",
    "wiki_numeric": "real_wiki",
    "wiki_source_pool": "real_wiki",
}
POOL_OPERATIONS = {
    "wiki_table_pair": "table_pair_earlier_year",
    "wiki_table_scan": "dense_table_interval_scan",
    "wiki_table_lookup": "table_cell_lookup",
}
KINDS["wiki_source_pool"] = {
    "overrides": {"max_tasks_by_recipe"},
    "domain": "from_pinned_sources",
    "topic": "from_pinned_sources",
    "operations": set(POOL_OPERATIONS.values()),
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_file(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("recipe config must be project-relative")
    resolved = (ROOT / path).resolve(strict=True)
    linked_data = (ROOT / "data").resolve()
    allowed = resolved.is_relative_to(ROOT) or (
        path.parts[0] == "data" and resolved.is_relative_to(linked_data)
    )
    if not resolved.is_file() or not allowed:
        raise ValueError("recipe config is not a project file")
    return resolved


def _bin(tokens: int) -> str:
    return next((f"<{limit}" for limit in BINS if tokens < limit), ">=262144")


def plan(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    version = config.get("schema_version")
    if version not in {SCHEMA, SCHEMA_V2}:
        raise ValueError("unsupported factorial batch schema")
    recipes = config.get("recipes")
    if not isinstance(recipes, list) or not recipes:
        raise ValueError("at least one recipe is required")
    jobs = []
    ids: set[str] = set()
    planned_cells = []
    for recipe in recipes:
        if not isinstance(recipe, dict) or set(recipe) != {
            "name",
            "kind",
            "base_config",
            "overrides",
            "domain",
            "topic",
            "operations",
        }:
            raise ValueError("invalid recipe fields")
        name, kind = recipe["name"], recipe["kind"]
        if (
            not isinstance(name, str)
            or not name.replace("_", "").isalnum()
            or name in ids
        ):
            raise ValueError("invalid or repeated recipe name")
        ids.add(name)
        if kind not in KINDS:
            raise ValueError("unsupported native compiler")
        contract = KINDS[kind]
        base_path = _root_file(recipe["base_config"])
        base = json.loads(base_path.read_text())
        legal_operations = (
            {POOL_OPERATIONS[item] for item in base["requested_recipes"]}
            if kind == "wiki_source_pool"
            else contract["operations"]
        )
        if set(recipe["operations"]) != legal_operations or len(
            recipe["operations"]
        ) != len(legal_operations):
            raise ValueError("native operation bundle must be declared exactly")
        overrides = recipe["overrides"]
        if (
            not isinstance(overrides, dict)
            or (not overrides and kind != "wiki_source_pool")
            or set(overrides) - contract["overrides"]
        ):
            raise ValueError("unsupported recipe override")
        expected_domain = (
            base.get("domain") if kind == "wiki_numeric" else contract["domain"]
        )
        expected_topic = (
            base.get("topic") if kind == "wiki_numeric" else contract["topic"]
        )
        if (recipe["domain"], recipe["topic"]) != (expected_domain, expected_topic):
            raise ValueError("recipe cannot relabel a native source")
        native = {**base, **overrides}
        if kind == "shared_state":
            from scripts.run_p86_state_shared_batch import plan as state_plan

            state_jobs = state_plan(native)
            for cell in native["cells"]:
                planned_cells.append(
                    {
                        "recipe": name,
                        "domain": recipe["domain"],
                        "topic": recipe["topic"],
                        "task_bundle": sorted(contract["operations"]),
                        "length_parameter": cell["length_records"],
                        "depth": cell["depth"],
                        "worlds": cell["worlds"],
                    }
                )
            if len(state_jobs) != sum(cell["worlds"] for cell in native["cells"]):
                raise ValueError("state plan size changed")
        elif kind == "hybrid_rfc":
            if (
                len(native["seeds"]) < 2
                or len(set(native["seeds"])) != len(native["seeds"])
                or len(set(native["filler_chars"])) != len(native["filler_chars"])
            ):
                raise ValueError("hybrid requires distinct paired worlds and lengths")
            for value in native["filler_chars"]:
                planned_cells.append(
                    {
                        "recipe": name,
                        "domain": recipe["domain"],
                        "topic": recipe["topic"],
                        "task_bundle": sorted(contract["operations"]),
                        "length_parameter": value,
                        "worlds": len(native["seeds"]),
                    }
                )
        elif kind == "wiki_source_pool":
            from scripts.run_source_pool_batch import _planned

            supported, skipped = _planned(native)
            for source_job in supported["jobs"]:
                planned_cells.append(
                    {
                        "recipe": name,
                        "domain": source_job["domain"],
                        "topic": source_job["topic"],
                        "task_bundle": [POOL_OPERATIONS[source_job["recipe"]]],
                        "length_parameter": source_job["length_policy"],
                        "worlds": 1,
                        "source_name": source_job["name"],
                    }
                )
            # Preserve unsupported source/recipe combinations in the frozen
            # job, rather than counting them as emitted tasks.
            if not supported["jobs"]:
                raise ValueError("source pool has no native-supported cells")
        else:
            if not native.get("intervals") or len(
                {tuple(x) for x in native["intervals"]}
            ) != len(native["intervals"]):
                raise ValueError("numeric intervals must be distinct")
            for low, high in native["intervals"]:
                if type(low) is not int or type(high) is not int or low >= high:
                    raise ValueError("invalid numeric interval")
                planned_cells.append(
                    {
                        "recipe": name,
                        "domain": recipe["domain"],
                        "topic": recipe["topic"],
                        "task_bundle": sorted(contract["operations"]),
                        "length_parameter": "frozen_source_length",
                        "interval": [low, high],
                        "worlds": 1,
                    }
                )
        config_hash = hashlib.sha256(_dump(native).encode()).hexdigest()
        job = {
            "job_id": f"{name}-{config_hash[:12]}",
            "name": name,
            "kind": kind,
            "base_config": recipe["base_config"],
            "base_sha256": _sha(base_path),
            "config_sha256": config_hash,
            "config": native,
            "domain": recipe["domain"],
            "topic": recipe["topic"],
            "operations": sorted(legal_operations),
            **({"unsupported_cells": skipped} if kind == "wiki_source_pool" else {}),
        }
        if version == SCHEMA_V2:
            code_paths = (
                "longworld/synthesis/p92_factorial_batch.py",
                "scripts/run_p92_factorial_batch.py",
                *CODE_PATHS[kind],
            )
            job["compiler_code_sha256"] = {
                member: _sha(_root_file(member)) for member in code_paths
            }
        jobs.append(job)
    return {
        "schema_version": version + ".resolved",
        "plan_sha256": _sha(config_path),
        "jobs": jobs,
        "planned_cells": planned_cells,
    }


def _native_files(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): _sha(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _native_index(job: dict[str, Any], native: Path) -> tuple[list[dict], dict]:
    kind = job["kind"]
    manifest_file = "result.json" if kind == "wiki_source_pool" else "manifest.json"
    manifest = json.loads((native / manifest_file).read_text())
    if manifest.get("train_ready") is not False:
        raise ValueError("native output is not candidate-only")
    if kind in {"hybrid_rfc", "wiki_numeric"} and any(
        _sha(native / member) != digest
        for member, digest in manifest["files_sha256"].items()
    ):
        raise ValueError("native reader or index hash changed")
    if kind == "shared_state":
        if json.loads((native / "plan.json").read_text()) != job["config"]:
            raise ValueError("native state plan changed")
        rows = []
        for shard in sorted((native / "shards").iterdir()):
            receipt = shard / "receipt.json"
            if not receipt.exists():
                continue
            native_receipt = json.loads(receipt.read_text())
            if (
                _sha(shard / "world.json") != native_receipt["world_sha256"]
                or _sha(shard / "rows.jsonl") != native_receipt["rows_sha256"]
            ):
                raise ValueError("native state shard hash changed")
            rows.extend(
                json.loads(line)
                for line in (shard / "rows.jsonl").read_text().splitlines()
            )
        if len(rows) != manifest["reader_rows"]:
            raise ValueError("native state row count changed")
    elif kind == "wiki_source_pool":
        if any(
            _sha(native / member) != manifest[key]
            for member, key in (
                ("plan.json", "plan_sha256"),
                ("batch/batch_manifest.json", "batch_manifest_sha256"),
                ("merged/manifest.json", "merged_manifest_sha256"),
            )
        ):
            raise ValueError("source-pool native plan or merge hash changed")
        merged = json.loads((native / "merged/manifest.json").read_text())
        if any(
            _sha(native / "merged" / member) != digest
            for member, digest in merged["files_sha256"].items()
        ):
            raise ValueError("source-pool merged reader hash changed")
        rows = [
            {**row, "operation": row["task_type"]}
            for line in (native / "merged/sample_index.jsonl").read_text().splitlines()
            for row in [json.loads(line)]
        ]
        if len(rows) != manifest["candidate_views"]:
            raise ValueError("source pool merged index count changed")
        if "compiler_code_sha256" in job:
            answers = {}
            for split in ("train", "eval"):
                with (native / "merged" / f"{split}.jsonl").open() as stream:
                    for line in stream:
                        reader = json.loads(line)
                        answers[reader["example_id"]] = _answer_hash(
                            reader["messages"][1]["content"]
                        )
            if len(answers) != len(rows):
                raise ValueError("source pool reader/index count changed")
            for row in rows:
                row["answer_sha256"] = answers[row["example_id"]]
    else:
        rows = [
            json.loads(line)
            for line in (native / "sample_index.jsonl").read_text().splitlines()
        ]
        if len(rows) != manifest["candidate_views"]:
            raise ValueError("native index count changed")
    operations = {row["operation"] for row in rows}
    if operations and (
        operations - set(job["operations"])
        or (kind != "wiki_source_pool" and operations != set(job["operations"]))
    ):
        raise ValueError("native operations differ from declared legal bundle")
    if kind == "wiki_numeric" and any(
        (row["domain"], row["topic"]) != (job["domain"], job["topic"]) for row in rows
    ):
        raise ValueError("numeric source metadata changed")
    return rows, manifest


def _run_job(
    job: dict[str, Any], output: Path, compiler_workers: int
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    target = output / job["job_id"]
    if target.exists():
        return _verify_job(job, target)
    stage = output / f".stage-{job['job_id']}"
    config_path = stage / "config.json"
    if stage.exists():
        if (
            not config_path.is_file()
            or json.loads(config_path.read_text()) != job["config"]
        ):
            raise ValueError("staged job configuration changed")
    else:
        stage.mkdir()
        config_path.write_text(_dump(job["config"]) + "\n")
    native = stage / "native"
    complete_native = False
    if native.exists():
        try:
            _native_index(job, native)
        except (FileNotFoundError, KeyError, ValueError):
            if job["kind"] in {"hybrid_rfc", "wiki_numeric"}:
                raise ValueError(
                    "partial native output cannot be resumed by this compiler; "
                    f"staged files preserved at {native}"
                ) from None
        else:
            complete_native = True
    if not complete_native:
        start = time.monotonic()
        if job["kind"] == "shared_state":
            from scripts.run_p86_state_shared_batch import run

            run(config_path, native, workers=compiler_workers, resume=native.exists())
        elif job["kind"] == "hybrid_rfc":
            from scripts.run_p87_hybrid_rfc_pilot import run

            run(config_path, native, workers=compiler_workers)
        elif job["kind"] == "wiki_source_pool":
            from scripts.run_source_pool_batch import run

            run(config_path, native, workers=compiler_workers, resume=native.exists())
        else:
            from scripts.run_p91_wiki_numeric_table import run

            run(config_path, native)
        elapsed = round(time.monotonic() - start, 3)
        (stage / "elapsed.json").write_text(_dump({"elapsed_seconds": elapsed}) + "\n")
    elif (stage / "elapsed.json").exists():
        elapsed = json.loads((stage / "elapsed.json").read_text())["elapsed_seconds"]
    else:
        elapsed = 0.0
    rows, manifest = _native_index(job, native)
    receipt = _summarize_job(job, rows, manifest, elapsed)
    if complete_native and not (stage / "elapsed.json").exists():
        receipt["recovered_elapsed_unknown"] = True
    receipt["files_sha256"] = _native_files(native)
    (stage / "receipt.json").write_text(_dump(receipt) + "\n")
    os.rename(stage, target)
    return receipt


def _launch_job(
    job: dict[str, Any], config_path: Path, output: Path, compiler_workers: int
) -> dict[str, Any]:
    """Start a clean Python process; native multiprocessing never forks a thread."""
    command = [
        sys.executable,
        str(ROOT / "scripts/run_p92_factorial_batch.py"),
        "--config",
        str(config_path),
        "--output",
        str(output),
        "--single-job",
        job["job_id"],
        "--compiler-workers",
        str(compiler_workers),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"native job {job['job_id']} failed:\n{completed.stderr}")
    result = json.loads(completed.stdout)
    if result["job_id"] != job["job_id"]:
        raise ValueError("native subprocess returned another job")
    return result


def _summarize_job(
    job: dict[str, Any], rows: list[dict], manifest: dict, elapsed: float
) -> dict[str, Any]:
    groups = {row["source_group"] for row in rows}
    tasks = {
        row.get("semantic_task_id", row.get("task_id", row.get("example_id")))
        for row in rows
    }
    if None in tasks or len(tasks) < 1 and rows:
        raise ValueError("native semantic task identity is missing")
    splits: dict[str, str] = {}
    for row in rows:
        group, split = row["source_group"], row["split"]
        if group in splits and splits[group] != split:
            raise ValueError("source group leaked across splits")
        splits[group] = split
    if job["kind"] == "shared_state":
        rejected = manifest["rejected_jobs"]
        reject_reasons = manifest["rejection_reasons"]
        mask_rows = len(rows)  # Native run verifies every final reader/mask.
    elif job["kind"] == "wiki_source_pool":
        rejected = manifest["rejected_rows"]
        reject_reasons = {}
        mask_rows = 0  # Source-pool export has no batch-wide final-mask replay.
    else:
        rejected = manifest["rejected"]
        reject_reasons = manifest.get("rejection_reasons", {})
        mask_rows = manifest.get(
            "mask_audited", len(rows) if job["kind"] == "hybrid_rfc" else 0
        )
    receipt = {
        "schema_version": (SCHEMA_V2 if "compiler_code_sha256" in job else SCHEMA)
        + ".job-receipt",
        "job_id": job["job_id"],
        "kind": job["kind"],
        "base_sha256": job["base_sha256"],
        "config_sha256": job["config_sha256"],
        "native_manifest_sha256": hashlib.sha256(_dump(manifest).encode()).hexdigest(),
        "domain": job["domain"],
        "topic": job["topic"],
        "candidate_views": len(rows),
        "semantic_tasks": len(tasks),
        "source_groups": len(groups),
        "source_group_ids": sorted(groups),
        "operation_rows": dict(
            sorted(Counter(row["operation"] for row in rows).items())
        ),
        "split_rows": dict(sorted(Counter(row["split"] for row in rows).items())),
        "actual_full_chat_token_bins": dict(
            sorted(Counter(_bin(row["full_chat_tokens"]) for row in rows).items())
        ),
        "native_mask_audited_rows": mask_rows,
        "rejected": rejected,
        "rejection_reasons": reject_reasons,
        "elapsed_seconds": round(elapsed, 3),
        "train_ready": False,
    }
    if "compiler_code_sha256" in job:
        receipt["compiler_code_sha256"] = job["compiler_code_sha256"]
        receipt["source_group_splits"] = dict(sorted(splits.items()))
        receipt["source_group_operations"] = {
            group: sorted(
                {row["operation"] for row in rows if row["source_group"] == group}
            )
            for group in sorted(groups)
        }
        task_answers = {}
        global_tasks = set()
        for row in rows:
            task = row.get(
                "semantic_task_id", row.get("task_id", row.get("example_id"))
            )
            answer = row.get("answer_sha256") or _answer_hash(
                row["messages"][1]["content"]
            )
            key = _dump([SOURCE_KINDS[job["kind"]], row["source_group"], task])
            if key in task_answers and task_answers[key] != answer:
                raise ValueError("one source-scoped task has conflicting answers")
            task_answers[key] = answer
            global_tasks.add(_dump([SOURCE_KINDS[job["kind"]], task]))
        receipt["source_scoped_task_answers"] = dict(sorted(task_answers.items()))
        receipt["global_semantic_task_keys"] = sorted(global_tasks)
        receipt["sample_ids"] = sorted(
            {row.get("example_id", row.get("sample_id")) for row in rows}
        )
        if None in receipt["sample_ids"] or len(receipt["sample_ids"]) != len(rows):
            raise ValueError("native sample IDs are absent or duplicated")
    if job["kind"] == "wiki_source_pool":
        receipt["source_domains"] = sorted({row["domain"] for row in rows})
        receipt["source_topics"] = sorted({row["topic"] for row in rows})
        receipt["unsupported_cells"] = len(job["unsupported_cells"])
        receipt["unsupported_reasons"] = manifest["unsupported_reasons"]
        receipt["pinned_source_snapshots"] = len(job["config"]["sources"])
    return receipt


def _verify_job(job: dict[str, Any], target: Path) -> dict[str, Any]:
    receipt = json.loads((target / "receipt.json").read_text())
    config_path = target / "config.json"
    if (
        hashlib.sha256(_dump(json.loads(config_path.read_text())).encode()).hexdigest()
        != job["config_sha256"]
        or receipt["job_id"] != job["job_id"]
        or receipt["base_sha256"] != job["base_sha256"]
    ):
        raise ValueError("resumed job plan changed")
    native = target / "native"
    if _native_files(native) != receipt["files_sha256"]:
        raise ValueError("resumed native artifact changed")
    rows, manifest = _native_index(job, native)
    expected = _summarize_job(job, rows, manifest, receipt["elapsed_seconds"])
    if any(receipt[key] != value for key, value in expected.items()):
        raise ValueError("resumed job summary changed")
    return receipt


def _aggregate(
    plan_data: dict[str, Any], receipts: list[dict[str, Any]]
) -> dict[str, Any]:
    bins: Counter[str] = Counter()
    operations: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    groups: set[str] = set()
    rejections: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    domains: set[str] = set()
    topics: set[str] = set()
    group_splits: dict[str, str] = {}
    group_operations: dict[str, set[str]] = {}
    task_answers: dict[str, str] = {}
    global_tasks: set[str] = set()
    sample_ids: set[str] = set()
    identity_bound = all("source_group_splits" in item for item in receipts)
    for receipt in receipts:
        bins.update(receipt["actual_full_chat_token_bins"])
        operations.update(receipt["operation_rows"])
        splits.update(receipt["split_rows"])
        groups.update(receipt["source_group_ids"])
        rejections.update(receipt["rejection_reasons"])
        if receipt["kind"] == "wiki_source_pool":
            domains.update(receipt["source_domains"])
            topics.update(receipt["source_topics"])
            unsupported.update(receipt["unsupported_reasons"])
        else:
            domains.add(receipt["domain"])
            topics.add(receipt["topic"])
        if identity_bound:
            for group, split in receipt["source_group_splits"].items():
                if group in group_splits and group_splits[group] != split:
                    raise ValueError("source group leaked across splits")
                group_splits[group] = split
            for group, operations_for_group in receipt[
                "source_group_operations"
            ].items():
                group_operations.setdefault(group, set()).update(operations_for_group)
            current_tasks = receipt["source_scoped_task_answers"]
            current_samples = set(receipt["sample_ids"])
            if sample_ids & current_samples:
                raise ValueError("duplicate sample across recipes")
            for task_key, answer in current_tasks.items():
                if task_key in task_answers and task_answers[task_key] != answer:
                    raise ValueError("conflicting answer for reused semantic task")
                task_answers[task_key] = answer
            global_tasks.update(receipt["global_semantic_task_keys"])
            sample_ids.update(current_samples)
    if not identity_bound and len(groups) != sum(
        item["source_groups"] for item in receipts
    ):
        raise ValueError("source group identity reused between jobs")
    return {
        "schema_version": plan_data["schema_version"].removesuffix(".resolved")
        + ".result",
        "plan_sha256": plan_data["plan_sha256"],
        "planned_jobs": len(plan_data["jobs"]),
        "completed_jobs": len(receipts),
        "planned_cells": plan_data["planned_cells"],
        "candidate_views": sum(item["candidate_views"] for item in receipts),
        "semantic_tasks": (
            len(global_tasks)
            if identity_bound
            else sum(item["semantic_tasks"] for item in receipts)
        ),
        **(
            {
                "gross_per_recipe_semantic_tasks": sum(
                    item["semantic_tasks"] for item in receipts
                ),
                "source_scoped_semantic_tasks": len(task_answers),
            }
            if identity_bound
            else {}
        ),
        "source_groups": len(groups),
        **(
            {
                "multi_operation_source_groups": sum(
                    len(ops) > 1 for ops in group_operations.values()
                )
            }
            if identity_bound
            else {}
        ),
        "domains": sorted(domains),
        "topics": sorted(topics),
        "operations": dict(sorted(operations.items())),
        "splits": dict(sorted(splits.items())),
        "actual_full_chat_token_bins": dict(sorted(bins.items())),
        "native_mask_audited_rows": sum(
            item["native_mask_audited_rows"] for item in receipts
        ),
        "rejected": sum(item["rejected"] for item in receipts),
        "rejection_reasons": dict(sorted(rejections.items())),
        "compiler_elapsed_seconds_sum": round(
            sum(item["elapsed_seconds"] for item in receipts), 3
        ),
        "train_ready": False,
        **(
            {
                "unsupported_cells": sum(
                    item.get("unsupported_cells", 0) for item in receipts
                ),
                "unsupported_reasons": dict(sorted(unsupported.items())),
            }
            if unsupported
            else {}
        ),
    }


def run(
    config_path: Path,
    output: Path,
    *,
    workers: int = 2,
    compiler_workers: int = 2,
    resume: bool = False,
) -> dict[str, Any]:
    if workers < 1 or compiler_workers < 1:
        raise ValueError("worker counts must be positive")
    planned = plan(config_path)
    if output.exists():
        if not resume:
            raise ValueError("output exists; use resume for the frozen plan")
        prior = json.loads((output / "plan.json").read_text())
        if prior != planned:
            raise ValueError("frozen plan changed")
    else:
        output.mkdir(parents=True)
        (output / "plan.json").write_text(_dump(planned) + "\n")
    jobs = planned["jobs"]
    receipts: dict[str, dict] = {}
    # Threads launch separate Python processes; native process pools are formed
    # only in a subprocess main thread. This avoids fork-from-thread hazards.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = iter(jobs)
        active = {}
        for _ in range(min(workers, len(jobs))):
            job = next(pending)
            active[
                executor.submit(_launch_job, job, config_path, output, compiler_workers)
            ] = job
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                job = active.pop(future)
                receipts[job["job_id"]] = future.result()
                following = next(pending, None)
                if following is not None:
                    active[
                        executor.submit(
                            _launch_job,
                            following,
                            config_path,
                            output,
                            compiler_workers,
                        )
                    ] = following
    result = _aggregate(planned, [receipts[job["job_id"]] for job in jobs])
    (output / "manifest.json").write_text(_dump(result) + "\n")
    return result


def verify(config_path: Path, output: Path) -> dict[str, Any]:
    planned = plan(config_path)
    if json.loads((output / "plan.json").read_text()) != planned:
        raise ValueError("frozen plan changed")
    stored_manifest = json.loads((output / "manifest.json").read_text())
    receipts = [_verify_job(job, output / job["job_id"]) for job in planned["jobs"]]
    adopted = _verify_adoption(output, planned, receipts)
    if adopted != ("adoption_sha256" in stored_manifest):
        raise ValueError("batch adoption provenance missing")
    result = _aggregate(planned, receipts)
    if adopted:
        result["adoption_sha256"] = _sha(output / "adoption.json")
    if stored_manifest != result:
        raise ValueError("batch summary changed")
    return result


def _verify_adoption(
    output: Path, planned: dict[str, Any], receipts: list[dict[str, Any]]
) -> bool:
    sidecar = output / "adoption.json"
    marked = any(
        "adopted_from_job_receipt_sha256" in receipt
        or "execution_code_unpinned_at_source" in receipt
        for receipt in receipts
    )
    if sidecar.exists() != marked:
        raise ValueError("adoption sidecar or job provenance marker missing")
    if not marked:
        return False
    adoption = json.loads(sidecar.read_text())
    source_output = (output.resolve() / adoption["source_output_relative"]).resolve()
    source_config = (output.resolve() / adoption["source_config_relative"]).resolve()
    source_plan = json.loads((source_output / "plan.json").read_text())
    source_schema = adoption.get("source_schema_version")
    if (
        source_schema not in {SCHEMA + ".resolved", SCHEMA_V2 + ".resolved"}
        or source_plan.get("schema_version") != source_schema
        or source_plan.get("plan_sha256") != _sha(source_config)
        or adoption.get("source_plan_sha256") != _sha(source_output / "plan.json")
        or adoption.get("source_manifest_sha256")
        != _sha(source_output / "manifest.json")
    ):
        raise ValueError("adoption source schema changed")
    unpinned = source_schema == SCHEMA + ".resolved"
    source_jobs = {job["name"]: job for job in source_plan["jobs"]}
    if set(source_jobs) != {job["name"] for job in planned["jobs"]}:
        raise ValueError("adoption source jobs changed")
    if (
        adoption.get("target_plan_sha256") != _sha(output / "plan.json")
        or adoption.get("execution_code_unpinned_at_source") is not unpinned
        or adoption.get("native_bytes_copied_and_sha256_verified") is not True
        or any(
            receipt.get("execution_code_unpinned_at_source") is not unpinned
            or receipt.get("adopted_from_job_receipt_sha256")
            != _sha(source_output / source_jobs[job["name"]]["job_id"] / "receipt.json")
            for job, receipt in zip(planned["jobs"], receipts)
        )
    ):
        raise ValueError("adoption provenance changed")
    return True


def adopt_verified_native(
    config_path: Path, output: Path, source_config: Path, source_output: Path
) -> dict[str, Any]:
    """Copy frozen verified native bytes into a current v2 code-bound plan.

    V1 sources are marked as post-hoc code bindings. V2 sources retain their
    original code hashes, and native compiler hashes must match the target.
    """
    if output.exists():
        raise ValueError("adopted output must be new")
    old_plan = json.loads((source_output / "plan.json").read_text())
    if old_plan.get("plan_sha256") != _sha(source_config) or old_plan.get(
        "schema_version"
    ) not in {SCHEMA + ".resolved", SCHEMA_V2 + ".resolved"}:
        raise ValueError("adoption source plan/config mismatch")
    old_receipts = [
        _verify_job(job, source_output / job["job_id"]) for job in old_plan["jobs"]
    ]
    source = _aggregate(old_plan, old_receipts)
    if json.loads((source_output / "manifest.json").read_text()) != source:
        raise ValueError("adoption source manifest changed")
    old_is_v1 = old_plan["schema_version"] == SCHEMA + ".resolved"
    new_plan = plan(config_path)
    if new_plan["schema_version"] != SCHEMA_V2 + ".resolved":
        raise ValueError("adoption target must use v2 code-bound plan")
    old_jobs = {job["name"]: job for job in old_plan["jobs"]}
    if set(old_jobs) != {job["name"] for job in new_plan["jobs"]}:
        raise ValueError("adoption recipes differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p92-adopt-", dir=output.parent) as raw:
        stage = Path(raw)
        (stage / "plan.json").write_text(_dump(new_plan) + "\n")
        receipts = []
        for job in new_plan["jobs"]:
            old = old_jobs[job["name"]]
            if any(
                job[key] != old[key]
                for key in (
                    "kind",
                    "base_sha256",
                    "config_sha256",
                    "config",
                    "operations",
                )
            ):
                raise ValueError("adoption changes native generation inputs")
            if not old_is_v1 and any(
                old["compiler_code_sha256"][member]
                != job["compiler_code_sha256"][member]
                for member in CODE_PATHS[job["kind"]]
            ):
                raise ValueError("native compiler code changed since source execution")
            prior = _verify_job(old, source_output / old["job_id"])
            target = stage / job["job_id"]
            target.mkdir()
            (target / "config.json").write_text(_dump(job["config"]) + "\n")
            shutil.copytree(source_output / old["job_id"] / "native", target / "native")
            if _native_files(target / "native") != prior["files_sha256"]:
                raise ValueError("copied native bytes changed during adoption")
            rows, manifest = _native_index(job, target / "native")
            receipt = _summarize_job(job, rows, manifest, prior["elapsed_seconds"])
            receipt["files_sha256"] = prior["files_sha256"]
            receipt["adopted_from_job_receipt_sha256"] = _sha(
                source_output / old["job_id"] / "receipt.json"
            )
            receipt["execution_code_unpinned_at_source"] = old_is_v1
            (target / "receipt.json").write_text(_dump(receipt) + "\n")
            receipts.append(receipt)
        (stage / "adoption.json").write_text(
            _dump(
                {
                    "source_plan_sha256": _sha(source_output / "plan.json"),
                    "source_manifest_sha256": _sha(source_output / "manifest.json"),
                    "source_output_relative": os.path.relpath(
                        source_output.resolve(), output.resolve()
                    ),
                    "source_config_relative": os.path.relpath(
                        source_config.resolve(), output.resolve()
                    ),
                    "target_plan_sha256": _sha(stage / "plan.json"),
                    "source_schema_version": old_plan["schema_version"],
                    "execution_code_unpinned_at_source": old_is_v1,
                    "native_bytes_copied_and_sha256_verified": True,
                }
            )
            + "\n"
        )
        manifest = _aggregate(new_plan, receipts)
        manifest["adoption_sha256"] = _sha(stage / "adoption.json")
        (stage / "manifest.json").write_text(_dump(manifest) + "\n")
        os.rename(stage, output)
    return manifest


def _novelty_counts(
    current: list[tuple[str, str, str, str]],
    baseline: list[tuple[str, str, str, str]],
) -> dict[str, Any]:
    """Separate exact identity collision from conservative answer overlap."""
    prior_ids = {task for _, _, task, _ in baseline}
    prior_scoped_ids = {
        (group, operation, task) for group, operation, task, _ in baseline
    }
    prior_signatures = {
        (group, operation, answer) for group, operation, _, answer in baseline
    }
    prior_groups = {group for group, _, _, _ in baseline}
    by_group: dict[str, Counter[str]] = {}
    for group, operation, task, answer in current:
        counts = by_group.setdefault(group, Counter())
        counts["views"] += 1
        if task in prior_ids:
            counts["global_task_id_overlap"] += 1
        if (group, operation, task) in prior_scoped_ids:
            counts["source_scoped_task_key_overlap"] += 1
        if (group, operation, answer) in prior_signatures:
            counts["same_source_operation_answer_overlap"] += 1
        if group not in prior_groups:
            counts["previously_unindexed_source_group_views"] += 1
    return {
        "views": len(current),
        "global_task_id_overlap": sum(
            x["global_task_id_overlap"] for x in by_group.values()
        ),
        "source_scoped_task_key_overlap": sum(
            x["source_scoped_task_key_overlap"] for x in by_group.values()
        ),
        "same_source_operation_answer_overlap": sum(
            x["same_source_operation_answer_overlap"] for x in by_group.values()
        ),
        "previously_unindexed_source_groups": sum(
            group not in prior_groups for group in by_group
        ),
        "previously_unindexed_source_group_views": sum(
            x["previously_unindexed_source_group_views"] for x in by_group.values()
        ),
        "by_source_group": {
            key: dict(value) for key, value in sorted(by_group.items())
        },
    }


def audit_baseline(
    config_path: Path, output: Path, baseline_index: Path, report: Path
) -> dict[str, Any]:
    """Audit task/source overlap against a verified prior candidate index.

    Same-source answer signatures are *possible* semantic overlap, not a proof
    that distinct questions are identical. New source groups are also only
    source-level novelty, not proof of a new reasoning mechanism.
    """
    from longworld.synthesis.sharded_candidate_bank import verify_index

    verify(config_path, output)
    verify_index(baseline_index)
    baseline = []
    with (baseline_index / "candidate_refs.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)["candidate"]
            baseline.append(
                (
                    row["source_group"],
                    row["operation"],
                    row["semantic_task_id"],
                    row["answer_sha256"],
                )
            )
    planned = plan(config_path)
    by_kind = {}
    for job in planned["jobs"]:
        native = output / job["job_id"] / "native"
        rows, _ = _native_index(job, native)
        answers = {}
        if job["kind"] == "wiki_source_pool":
            for split in ("train", "eval"):
                with (native / "merged" / f"{split}.jsonl").open() as stream:
                    for line in stream:
                        reader = json.loads(line)
                        answers[reader["example_id"]] = _answer_hash(
                            reader["messages"][1]["content"]
                        )
        current = []
        for row in rows:
            task = row.get(
                "semantic_task_id", row.get("task_id", row.get("example_id"))
            )
            answer = row.get("answer_sha256")
            if answer is None:
                answer = (
                    _answer_hash(row["messages"][1]["content"])
                    if job["kind"] == "shared_state"
                    else answers[row["example_id"]]
                )
            current.append((row["source_group"], row["operation"], task, answer))
        by_kind[job["name"]] = _novelty_counts(current, baseline)
    result = {
        "schema_version": SCHEMA + ".baseline-audit",
        "plan_sha256": planned["plan_sha256"],
        "baseline_refs_sha256": _sha(baseline_index / "candidate_refs.jsonl"),
        "baseline_views": len(baseline),
        "recipes": by_kind,
        "novelty_scope": "exact task ID, same-source operation+answer overlap, and source-group presence; no semantic equivalence proof",
        "train_ready": False,
    }
    if report.exists():
        if json.loads(report.read_text()) != result:
            raise ValueError("baseline audit output changed")
    else:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(_dump(result) + "\n")
    return result


def extract_new_wiki_readers(
    batch: Path,
    baseline_index: Path,
    question_audit: Path,
    output: Path,
    extra_prior_shards: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Freeze source-scoped and globally new Wiki tasks only."""
    from longworld.synthesis.sharded_candidate_bank import verify_index
    from longworld.synthesis.unified_candidate_merge import verify_merge

    if output.exists():
        raise ValueError("new-only output must be new")
    verify_index(baseline_index)
    audit = json.loads(question_audit.read_text())
    baseline_sha = _sha(baseline_index / "candidate_refs.jsonl")
    audit_baseline_sha = audit.get(
        "baseline_refs_sha256", audit.get("baseline_p91_refs_sha256")
    )
    expected_scoped_overlap = audit.get("source_scoped_task_key_overlap")
    if expected_scoped_overlap is None:
        expected_scoped_overlap = sum(
            audit.get("source_scoped_key_overlaps", {}).values()
        )
    if audit_baseline_sha != baseline_sha or expected_scoped_overlap is None:
        raise ValueError("question fingerprint audit is not bound to baseline")
    old_keys = set()
    old_global: dict[tuple[str, str], set[str]] = {}
    with (baseline_index / "candidate_refs.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)["candidate"]
            old_keys.add(
                (row["source_group"], row["operation"], row["semantic_task_id"])
            )
            old_global.setdefault(
                (row["source_kind"], row["semantic_task_id"]), set()
            ).add(row["answer_sha256"])
    extra_pins = []
    for prior in extra_prior_shards:
        prior_manifest = json.loads((prior / "manifest.json").read_text())
        extra_pins.append(
            {"path": str(prior), "manifest_sha256": _sha(prior / "manifest.json")}
        )
        if prior_manifest.get("schema_version") == "longworld.unified-candidates.v1":
            verify_merge(prior)
            for line in (prior / "sample_index.jsonl").open():
                row = json.loads(line)
                old_keys.add(
                    (row["source_group"], row["operation"], row["semantic_task_id"])
                )
                old_global.setdefault(
                    (row["source_kind"], row["semantic_task_id"]), set()
                ).add(row["answer_sha256"])
        elif prior_manifest.get("schema_version", "").endswith(".wiki-new-only.v2"):
            if any(
                _sha(prior / name) != digest
                for name, digest in prior_manifest["files_sha256"].items()
            ):
                raise ValueError("prior new-only Wiki bytes changed")
            prior_readers = {
                split: (prior / f"{split}.jsonl").open() for split in ("train", "eval")
            }
            try:
                for line in (prior / "sample_index.jsonl").open():
                    row = json.loads(line)
                    reader = json.loads(prior_readers[row["split"]].readline())
                    if reader["example_id"] != row["example_id"]:
                        raise ValueError("prior new-only reader/index differ")
                    answer = _answer_hash(reader["messages"][1]["content"])
                    old_keys.add(
                        (row["source_group"], row["task_type"], row["task_id"])
                    )
                    old_global.setdefault(("real_wiki", row["task_id"]), set()).add(
                        answer
                    )
                if any(stream.readline() for stream in prior_readers.values()):
                    raise ValueError("prior new-only reader has extra rows")
            finally:
                for stream in prior_readers.values():
                    stream.close()
        else:
            raise ValueError("unsupported extra prior shard schema")
    source = batch / "native/merged"
    source_manifest = json.loads((source / "manifest.json").read_text())
    if source_manifest.get("schema") != "longworld.source-batch-merged.v1":
        raise ValueError("source is not a native Wiki merged batch")
    if any(
        _sha(source / name) != digest
        for name, digest in source_manifest["files_sha256"].items()
    ):
        raise ValueError("native Wiki merged bytes changed")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p92-new-wiki-", dir=output.parent) as raw:
        temp = Path(raw)
        readers = {
            split: (source / f"{split}.jsonl").open() for split in ("train", "eval")
        }
        writers = {
            split: (temp / f"{split}.jsonl").open("x") for split in ("train", "eval")
        }
        index_writer = (temp / "sample_index.jsonl").open("x")
        audit_writer = (temp / "audit.jsonl").open("x")
        selected = []
        source_scoped_candidates = global_overlap_removed = 0
        selected_global: dict[tuple[str, str], str] = {}
        try:
            with (
                (source / "sample_index.jsonl").open() as index_stream,
                (source / "audit.jsonl").open() as source_audit,
            ):
                for line in index_stream:
                    row = json.loads(line)
                    split = row["split"]
                    reader_line = readers[split].readline()
                    audit_line = source_audit.readline()
                    if not reader_line or not audit_line:
                        raise ValueError("native Wiki index/reader/audit count differs")
                    reader, evidence = json.loads(reader_line), json.loads(audit_line)
                    if (
                        reader["example_id"] != row["example_id"]
                        or evidence["example_id"] != row["example_id"]
                    ):
                        raise ValueError(
                            "native Wiki index/reader/audit identity differs"
                        )
                    key = (row["source_group"], row["task_type"], row["task_id"])
                    if key in old_keys:
                        continue
                    source_scoped_candidates += 1
                    answer = _answer_hash(reader["messages"][1]["content"])
                    global_key = ("real_wiki", row["task_id"])
                    prior_answers = old_global.get(global_key)
                    if prior_answers:
                        if prior_answers != {answer}:
                            raise ValueError(
                                "global Wiki task ID has conflicting answer"
                            )
                        global_overlap_removed += 1
                        continue
                    if global_key in selected_global:
                        if selected_global[global_key] != answer:
                            raise ValueError(
                                "selected global Wiki task has conflicting answer"
                            )
                        global_overlap_removed += 1
                        continue
                    selected_global[global_key] = answer
                    selected.append(row)
                    writers[split].write(reader_line)
                    index_writer.write(line)
                    audit_writer.write(audit_line)
                if source_audit.readline() or any(
                    stream.readline() for stream in readers.values()
                ):
                    raise ValueError("native Wiki reader/audit has extra rows")
        finally:
            for stream in (
                *readers.values(),
                *writers.values(),
                index_writer,
                audit_writer,
            ):
                stream.close()
        if (
            source_scoped_candidates != audit["gross_views"] - expected_scoped_overlap
            or len(selected) != source_scoped_candidates - global_overlap_removed
            or len(selected)
            != len(
                {(r["source_group"], r["task_type"], r["task_id"]) for r in selected}
            )
        ):
            raise ValueError(
                "new-only count or task identity differs from fingerprint audit"
            )
        splits = Counter(row["split"] for row in selected)
        group_splits = {}
        for row in selected:
            group = row["source_group"]
            if group in group_splits and group_splits[group] != row["split"]:
                raise ValueError("new-only source group leaked across splits")
            group_splits[group] = row["split"]
        result = {
            "schema_version": SCHEMA + ".wiki-new-only.v2",
            "source_merged_manifest_sha256": _sha(source / "manifest.json"),
            "source_job_receipt_sha256": _sha(batch / "receipt.json"),
            "baseline_refs_sha256": baseline_sha,
            "question_fingerprint_audit_sha256": _sha(question_audit),
            "extra_prior_shards": extra_pins,
            "source_scoped_candidates": source_scoped_candidates,
            "global_semantic_overlap_removed": global_overlap_removed,
            "candidate_views": len(selected),
            "source_scoped_semantic_tasks": len(selected),
            "global_semantic_tasks": len(selected_global),
            "source_groups": len(group_splits),
            "splits": dict(sorted(splits.items())),
            "operations": dict(
                sorted(Counter(row["task_type"] for row in selected).items())
            ),
            "native_length_bins": dict(
                sorted(Counter(row["length_bin"] for row in selected).items())
            ),
            "full_chat_token_bins": dict(
                sorted(
                    Counter(_bin(row["full_chat_tokens"]) for row in selected).items()
                )
            ),
            "mask_audited_rows": 0,
            "files_sha256": {
                name: _sha(temp / name)
                for name in (
                    "train.jsonl",
                    "eval.jsonl",
                    "sample_index.jsonl",
                    "audit.jsonl",
                )
            },
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(_dump(result) + "\n")
        os.rename(temp, output)
    return result


def audit_new_wiki_mask(output: Path, report: Path) -> dict[str, Any]:
    """Check every selected reader against the pinned assistant-only mask."""
    from longworld.synthesis.length_controller import (
        TOKENIZER_MODEL,
        TOKENIZER_REVISION,
        get_tokenizer,
    )
    from scripts.train_sft import tokenize_assistant_only

    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("schema_version") not in {
        SCHEMA + ".wiki-new-only.v1",
        SCHEMA + ".wiki-new-only.v2",
    }:
        raise ValueError("wrong new-only Wiki manifest")
    if any(
        _sha(output / name) != digest
        for name, digest in manifest["files_sha256"].items()
    ):
        raise ValueError("new-only Wiki reader bytes changed")
    tokenizer = get_tokenizer()
    readers = {split: (output / f"{split}.jsonl").open() for split in ("train", "eval")}
    counts = Counter()
    full_tokens = supervised_tokens = 0
    try:
        with (output / "sample_index.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                reader = json.loads(readers[row["split"]].readline())
                if (
                    reader["example_id"] != row["example_id"]
                    or set(reader["messages"][0]) != {"role", "content"}
                    or set(reader["messages"][1]) != {"role", "content"}
                    or [x["role"] for x in reader["messages"]] != ["user", "assistant"]
                ):
                    raise ValueError("reader identity or model-visible shape changed")
                encoded = tokenize_assistant_only(tokenizer, reader["messages"], 262144)
                ids, labels = encoded["input_ids"], encoded["labels"]
                input_tokens = row["input_tokens"]
                if (
                    len(ids) != row["full_chat_tokens"]
                    or input_tokens + row["supervised_tokens"] != len(ids)
                    or labels[:input_tokens] != [-100] * input_tokens
                    or labels[input_tokens:] != ids[input_tokens:]
                ):
                    raise ValueError(
                        f"final reader assistant mask changed: {row['example_id']}"
                    )
                counts[row["split"]] += 1
                full_tokens += len(ids)
                supervised_tokens += row["supervised_tokens"]
        if any(stream.readline() for stream in readers.values()):
            raise ValueError("new-only Wiki reader has extra rows")
    finally:
        for stream in readers.values():
            stream.close()
    if sum(counts.values()) != manifest["candidate_views"]:
        raise ValueError("mask audit count differs from selected reader count")
    result = {
        "schema_version": SCHEMA + ".wiki-new-only-mask.v1",
        "source_manifest_sha256": _sha(output / "manifest.json"),
        "tokenizer": {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
        "checked_rows": sum(counts.values()),
        "splits": dict(sorted(counts.items())),
        "full_chat_tokens": full_tokens,
        "supervised_tokens": supervised_tokens,
        "status": "all_final_reader_assistant_masks_checked",
        "train_ready": False,
    }
    if report.exists():
        if json.loads(report.read_text()) != result:
            raise ValueError("new-only mask report changed")
    else:
        report.write_text(_dump(result) + "\n")
    return result
