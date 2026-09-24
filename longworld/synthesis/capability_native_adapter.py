"""Bounded P71 capability-world adapter for a shared synthesis scheduler.

The native runner remains the authority for world construction, answers,
length fitting, and split assignment. This layer only probes its plan and
checks that its completed exports can be consumed as one source group.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import run_capability_records as native

SOURCE_KIND = "controlled_simulation"
MAX_SHARDS = 10_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(config: dict[str, Any], *, max_shards: int = MAX_SHARDS) -> dict[str, Any]:
    """Measure a native plan without generating worlds or loading a tokenizer."""
    if type(max_shards) is not int or max_shards < 1:
        raise ValueError("max_shards must be positive")
    if config.get("schema_version") != "longworld.record-world-config.v1":
        raise ValueError("unsupported capability-world config schema")
    families = config.get("families")
    if (
        not isinstance(families, list)
        or not families
        or len(families) != len(set(families))
    ):
        raise ValueError("capability-world families must be nonempty and unique")
    for family in families:
        native.module_for(family)
    if type(config.get("workers")) is not int or config["workers"] < 1:
        raise ValueError("workers must be positive")
    plan = native.make_plan(config)
    if not plan or len(plan) > max_shards:
        raise ValueError(f"capability-world plan must contain 1..{max_shards} shards")
    seeds = [job["seed"] for job in plan]
    if len(seeds) != len(set(seeds)):
        raise ValueError("capability-world plan reuses a world seed")
    targets = [job["token_target"] for job in plan]
    if any(type(target) is not int or target < 4096 for target in targets):
        raise ValueError("token targets must be >= 4096")
    cells = Counter((job["family"], job["depth"], job["token_target"]) for job in plan)
    allowed_depths = {
        native.records: {1, 2, 3},
        native.families: {1, 2},
        native.research: {1, 2},
        native.unanswerable: {1},
    }
    unsupported = Counter(
        (job["family"], job["depth"], job["token_target"])
        for job in plan
        if job["depth"] not in allowed_depths[native.module_for(job["family"])]
    )
    splits = Counter(job["split"] for job in plan)
    if set(splits) - {"train", "eval"}:
        raise ValueError("invalid capability-world split")
    return {
        "source_kind": SOURCE_KIND,
        "planned_shards": len(plan),
        "planned_task_upper_bound": sum(job["n_variants"] for job in plan),
        "families": sorted(set(families)),
        "split_shards": dict(sorted(splits.items())),
        "token_targets": sorted(set(targets)),
        "unsupported_depth_shards": sum(unsupported.values()),
        "unsupported_depth_cells": [
            {"family": family, "depth": depth, "token_target": target, "shards": count}
            for (family, depth, target), count in sorted(unsupported.items())
        ],
        "cells": [
            {"family": family, "depth": depth, "token_target": target, "shards": count}
            for (family, depth, target), count in sorted(cells.items())
        ],
    }


def verify_output(destination: Path) -> dict[str, Any]:
    """Fail closed on partial, changed, or solver-invalid native exports."""
    destination = destination.resolve()
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("schema_version") != native.SCHEMA
        or manifest.get("status") != "local_symbolic_record_worlds_complete"
    ):
        raise ValueError("capability-world manifest is not complete")
    verification = json.loads((destination / "verification.json").read_text())
    solver = verification.get("solver_recheck", {})
    rows = manifest.get("rows")
    if (
        type(rows) is not int
        or rows < 1
        or solver.get("passed") is not True
        or solver.get("checked") != rows
        or solver.get("mismatches") != 0
    ):
        raise ValueError("capability-world solver verification failed")
    if manifest.get("completed_shards") != len(manifest.get("shards", [])):
        raise ValueError("capability-world shard count mismatch")
    files = manifest.get("files", {})
    names = ("train.jsonl", "eval.jsonl", "sample_index.jsonl")
    if any(_sha256(destination / name) != files.get(name) for name in names):
        raise ValueError("capability-world export hash mismatch")
    split_ids: dict[str, set[str]] = {"train": set(), "eval": set()}
    for split, ids in split_ids.items():
        with (destination / f"{split}.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("split") != split or not row.get("example_id"):
                    raise ValueError("capability-world row split or ID mismatch")
                if row["example_id"] in ids:
                    raise ValueError("duplicate capability-world example")
                ids.add(row["example_id"])
    if split_ids["train"] & split_ids["eval"]:
        raise ValueError("capability-world example crosses split")
    if {split: len(ids) for split, ids in split_ids.items()} != manifest.get(
        "split_rows"
    ):
        raise ValueError("capability-world split count mismatch")
    index_ids: set[str] = set()
    world_splits: dict[str, str] = {}
    with (destination / "sample_index.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            split = row.get("split")
            example_id = row.get("example_id")
            world_id = row.get("world_id")
            if (
                split not in split_ids
                or example_id not in split_ids[split]
                or example_id in index_ids
                or not world_id
                or row.get("group_id") != world_id
                or row.get("admission_status") != "completed"
                or type(row.get("full_message_tokens")) is not int
            ):
                raise ValueError("capability-world sample index invalid")
            prior = world_splits.setdefault(world_id, split)
            if prior != split:
                raise ValueError("capability-world world crosses split")
            index_ids.add(example_id)
    if index_ids != split_ids["train"] | split_ids["eval"] or len(index_ids) != rows:
        raise ValueError("capability-world index does not cover exports")
    return {
        "source_kind": SOURCE_KIND,
        "status": "verified_native_candidate",
        "rows": rows,
        "semantic_tasks": manifest["semantic_tasks"],
        "worlds": len(world_splits),
        "split_rows": manifest["split_rows"],
        "families": manifest["families"],
        "train_ready": False,
        "paths": {
            "train": str(destination / "train.jsonl"),
            "eval": str(destination / "eval.jsonl"),
            "sample_index": str(destination / "sample_index.jsonl"),
            "manifest": str(manifest_path),
            "verification": str(destination / "verification.json"),
            "state": str(destination / "state.json"),
        },
        "files_sha256": {name: files[name] for name in names},
    }


def run_native(
    config_path: Path,
    destination: Path,
    *,
    resume: bool = False,
    max_shards: int = MAX_SHARDS,
) -> dict[str, Any]:
    """Run a fresh native bank, or adopt an already complete matching bank."""
    config_path = Path(config_path)
    destination = Path(destination)
    config = json.loads(config_path.read_text())
    planned = probe(config, max_shards=max_shards)
    if (
        planned["unsupported_depth_shards"]
        and not (destination / "manifest.json").exists()
    ):
        raise ValueError(
            "fresh capability-world plan contains unsupported family/depth cells"
        )
    if destination.exists() and (destination / "manifest.json").exists():
        if not resume:
            raise ValueError("existing capability-world output requires resume")
        state = json.loads((destination / "state.json").read_text())
        if state.get("config") != config:
            raise ValueError("capability-world resume config changed")
        for name, digest in state.get("code_sha256", {}).items():
            if _sha256(native.ROOT / name) != digest:
                raise ValueError("capability-world resume code changed")
    else:
        native.run(config_path, destination, resume=resume)
    result = verify_output(destination)
    if result["semantic_tasks"] > planned["planned_task_upper_bound"]:
        raise ValueError("capability-world task count exceeds native plan")
    result["planned_shards"] = planned["planned_shards"]
    return result
