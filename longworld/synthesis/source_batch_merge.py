"""Merge validated native jobs into one candidate pool with semantic dedup."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from itertools import zip_longest
from pathlib import Path
from typing import Any


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _length_bin(tokens: int) -> str:
    if type(tokens) is not int or tokens < 0:
        raise ValueError("merged row lacks measured final tokens")
    for cap, name in (
        (32768, "lt32k"),
        (65536, "32k"),
        (131072, "64k"),
        (262144, "128k"),
    ):
        if tokens < cap:
            return name
    return "ge256k"


def _merge_into(job_root: Path, output_dir: Path, expected_rows: int) -> dict[str, Any]:
    files = {
        name: (output_dir / name).open("x", encoding="utf-8")
        for name in ("train.jsonl", "eval.jsonl", "sample_index.jsonl", "audit.jsonl")
    }
    task_answers: dict[tuple[str, str], tuple[str, str]] = {}
    task_groups: dict[tuple[str, str], set[str]] = {}
    seen_views: set[tuple[str, str, str]] = set()
    seen_samples: set[str] = set()
    groups: set[str] = set()
    counts: Counter[str] = Counter()
    cells: Counter[tuple[str, str, str, str]] = Counter()
    duplicates: list[dict[str, str]] = []
    try:
        for job_dir in sorted(job_root.iterdir()):
            if not job_dir.is_dir():
                continue
            manifest = json.loads((job_dir / "manifest.json").read_text())
            split = manifest["split"]
            job_rows = 0
            with (
                (job_dir / f"{split}.jsonl").open(encoding="utf-8") as row_in,
                (job_dir / "sample_index.jsonl").open(encoding="utf-8") as index_in,
                (job_dir / "audit.jsonl").open(encoding="utf-8") as audit_in,
            ):
                for raw_row, raw_index, raw_audit in zip_longest(
                    row_in, index_in, audit_in
                ):
                    if None in (raw_row, raw_index, raw_audit):
                        raise ValueError(
                            f"job row/index/audit length mismatch: {job_dir}"
                        )
                    row, index, audit = (
                        json.loads(raw_row),
                        json.loads(raw_index),
                        json.loads(raw_audit),
                    )
                    job_rows += 1
                    counts["source_views"] += 1
                    if (
                        not index.get("example_id")
                        or row.get("example_id") != index["example_id"]
                        or audit.get("example_id") != index["example_id"]
                    ):
                        raise ValueError("job row/index/audit example ID mismatch")
                    if index["split"] != split or index["domain"] != manifest["domain"]:
                        raise ValueError("merged row split/domain drift")
                    physical_bin = _length_bin(index["full_chat_tokens"])
                    if index["length_bin"] != physical_bin:
                        if index["length_bin"] != "native":
                            raise ValueError(
                                "declared length bin disagrees with tokens"
                            )
                        index["source_length_label"] = "native"
                        index["length_bin"] = physical_bin
                    task_key = (index["domain"], index["task_id"])
                    task_groups.setdefault(task_key, set()).add(index["source_group"])
                    answer = _dump(row["messages"][1]["content"])
                    prior = task_answers.setdefault(task_key, (split, answer))
                    if prior != (split, answer):
                        raise ValueError("same semantic task crosses split or answer")
                    view_key = (*task_key, index["length_bin"])
                    if view_key in seen_views:
                        duplicates.append(
                            {
                                "job_id": job_dir.name,
                                "task_id": index["task_id"],
                                "length_bin": index["length_bin"],
                            }
                        )
                        continue
                    seen_views.add(view_key)
                    sample_id = str(index["example_id"])
                    if sample_id in seen_samples:
                        raise ValueError("duplicate sample ID after semantic dedup")
                    seen_samples.add(sample_id)
                    groups.add(index["source_group"])
                    counts["accepted_views"] += 1
                    counts[f"{split}_views"] += 1
                    counts[index["length_bin"]] += 1
                    cells[
                        (
                            index["domain"],
                            index["topic"],
                            index["task_type"],
                            index["length_bin"],
                        )
                    ] += 1
                    index["batch_job_id"] = job_dir.name
                    audit["batch_job_id"] = job_dir.name
                    files[f"{split}.jsonl"].write(_dump(row) + "\n")
                    files["sample_index.jsonl"].write(_dump(index) + "\n")
                    files["audit.jsonl"].write(_dump(audit) + "\n")
            if job_rows != manifest["candidate_rows"]:
                raise ValueError("job manifest candidate count mismatch")
    finally:
        for stream in files.values():
            stream.close()
    if counts["source_views"] != expected_rows:
        raise ValueError("source batch count and merged inputs disagree")
    summary = {
        "schema": "longworld.source-batch-merged.v1",
        "candidate_views": counts["accepted_views"],
        "source_views": counts["source_views"],
        "independent_tasks": len(task_answers),
        "tasks_in_multiple_source_groups": sum(
            len(value) > 1 for value in task_groups.values()
        ),
        "duplicate_views_removed": len(duplicates),
        "source_groups": len(groups),
        "length_bins": {
            key: value
            for key, value in sorted(counts.items())
            if key in {"lt32k", "32k", "64k", "128k", "ge256k"}
        },
        "splits": {key: counts[f"{key}_views"] for key in ("train", "eval")},
        "cells": [
            {
                "domain": domain,
                "topic": topic,
                "task_type": operation,
                "length_bin": length,
                "views": count,
            }
            for (domain, topic, operation, length), count in sorted(cells.items())
        ],
        "duplicate_views": duplicates,
        "train_ready": False,
        "files_sha256": {name: _sha(output_dir / name) for name in files},
    }
    (output_dir / "manifest.json").write_text(_dump(summary) + "\n")
    return summary


def merge(job_root: Path, output_dir: Path, expected_rows: int) -> dict[str, Any]:
    """Dedup same task and length, publishing only a complete candidate pool."""
    if output_dir.exists():
        raise ValueError("merged output already exists")
    with tempfile.TemporaryDirectory(
        prefix=".source-merge-", dir=output_dir.parent
    ) as raw:
        temporary = Path(raw)
        summary = _merge_into(job_root, temporary, expected_rows)
        temporary.rename(output_dir)
    return summary
