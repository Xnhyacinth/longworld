"""Select a bounded review set from verified final-reader metadata.

This writes pointers and a hash-bound decision ledger, never copies long
reader bodies or promotes candidates to training-ready data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from longworld.synthesis.unified_candidate_contract import _answer_hash
from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.unified-candidate-selection.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _policy(path: Path) -> dict[str, Any]:
    policy = _json(path)
    required = {
        "schema_version",
        "selection_seed",
        "source_kinds",
        "evidence_profiles",
        "dependency_statuses",
        "minimum_full_chat_tokens",
        "maximum_views_per_semantic_task",
        "maximum_tasks_per_source_group",
        "maximum_tasks_per_operation",
    }
    if set(policy) != required or policy["schema_version"] != SCHEMA:
        raise ValueError("invalid candidate selection policy")
    for key in ("source_kinds", "evidence_profiles", "dependency_statuses"):
        values = policy[key]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))
        ):
            raise ValueError(f"invalid {key} allowlist")
    for key in (
        "minimum_full_chat_tokens",
        "maximum_views_per_semantic_task",
        "maximum_tasks_per_source_group",
        "maximum_tasks_per_operation",
    ):
        value = policy[key]
        if type(value) is not int or value < (0 if key.startswith("minimum") else 1):
            raise ValueError(f"invalid {key}")
    if type(policy["selection_seed"]) is not int or policy["selection_seed"] < 0:
        raise ValueError("invalid selection seed")
    return policy


def _rank(seed: int, sample_id: str) -> str:
    return hashlib.sha256(f"{seed}|{sample_id}".encode()).hexdigest()


def _verify_selected_readers(merged_dir: Path, selected: list[dict[str, Any]]) -> None:
    """Check pointer identity and gold at the actual final-reader row."""
    by_split: dict[str, dict[int, dict[str, Any]]] = {"train": {}, "eval": {}}
    for row in selected:
        by_split[row["split"]][row["row_index"]] = row
    for split, targets in by_split.items():
        if not targets:
            continue
        remaining = set(targets)
        with (merged_dir / f"candidate_{split}.jsonl").open(encoding="utf-8") as stream:
            for position, line in enumerate(stream):
                if position not in targets:
                    continue
                reader = json.loads(line)
                target = targets[position]
                messages = reader.get("messages")
                if (
                    reader.get("sample_id") != target["sample_id"]
                    or not isinstance(messages, list)
                    or len(messages) != 2
                    or not isinstance(messages[1], dict)
                    or messages[1].get("role") != "assistant"
                    or not isinstance(messages[1].get("content"), str)
                    or _answer_hash(messages[1]["content"]) != target["answer_sha256"]
                ):
                    raise ValueError(
                        "selected final reader differs from candidate index"
                    )
                remaining.remove(position)
                if not remaining:
                    break
        if remaining:
            raise ValueError("selected final reader row is missing")


def select(merged_dir: Path, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    """Validate the source bank, then apply scope checks and bounded quotas."""
    merged_dir = Path(merged_dir)
    policy_path = Path(policy_path)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("selection output already exists")
    source = verify_merge(merged_dir)
    policy = _policy(policy_path)
    allowed = {
        "source_kind": set(policy["source_kinds"]),
        "evidence_profile": set(policy["evidence_profiles"]),
        "dependency_status": set(policy["dependency_statuses"]),
    }
    candidates: list[dict[str, Any]] = []
    rejected = Counter()
    seen_samples: set[str] = set()
    global_tasks: dict[tuple[str, str], tuple[str, str]] = {}
    group_splits: dict[tuple[str, str], str] = {}
    positions = Counter()
    with (merged_dir / "sample_index.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            sample_id = row["sample_id"]
            split = row["split"]
            if (
                not isinstance(sample_id, str)
                or not sample_id
                or sample_id in seen_samples
                or split not in {"train", "eval"}
                or row["output_file"] != f"candidate_{split}.jsonl"
                or row["row_index"] != positions[split]
            ):
                raise ValueError("source candidate index identity/position changed")
            seen_samples.add(sample_id)
            positions[split] += 1
            key = (row["source_kind"], row["semantic_task_id"])
            answer = (split, row["answer_sha256"])
            if key in global_tasks and global_tasks[key] != answer:
                raise ValueError("source semantic task changes split or answer")
            global_tasks[key] = answer
            group = (row["source_kind"], row["source_group"])
            if group in group_splits and group_splits[group] != split:
                raise ValueError("source group crosses train/eval split")
            group_splits[group] = split
            if type(row["full_chat_tokens"]) is not int or row["full_chat_tokens"] < 1:
                raise ValueError("invalid physical token count")
            reason = next(
                (
                    f"{field}_outside_policy"
                    for field, values in allowed.items()
                    if (row.get(field) or "unmeasured") not in values
                ),
                None,
            )
            if (
                reason is None
                and row["full_chat_tokens"] < policy["minimum_full_chat_tokens"]
            ):
                reason = "below_minimum_tokens"
            if reason is not None:
                rejected[reason] += 1
                continue
            candidates.append(
                {
                    key: row[key]
                    for key in (
                        "sample_id",
                        "task_key",
                        "semantic_task_id",
                        "source_kind",
                        "source_group",
                        "domain",
                        "topic",
                        "operation",
                        "split",
                        "length_bin",
                        "full_chat_tokens",
                        "answer_sha256",
                        "source_name",
                        "output_file",
                        "row_index",
                    )
                }
            )
    if (
        len(seen_samples) != source["candidate_views"]
        or dict(positions) != source["splits"]
    ):
        raise ValueError("source candidate index count differs from manifest")
    candidates.sort(
        key=lambda row: (
            _rank(policy["selection_seed"], row["sample_id"]),
            row["sample_id"],
        )
    )
    selected: list[dict[str, Any]] = []
    task_views = Counter()
    group_tasks: dict[tuple[str, str], set[tuple[str, str]]] = {}
    operation_tasks: dict[str, set[tuple[str, str]]] = {}
    for row in candidates:
        task = (row["source_kind"], row["semantic_task_id"])
        group = (row["source_kind"], row["source_group"])
        known_group = group_tasks.setdefault(group, set())
        known_operation = operation_tasks.setdefault(row["operation"], set())
        if task_views[task] >= policy["maximum_views_per_semantic_task"]:
            rejected["semantic_task_view_cap"] += 1
        elif (
            task not in known_group
            and len(known_group) >= policy["maximum_tasks_per_source_group"]
        ):
            rejected["source_group_task_cap"] += 1
        elif (
            task not in known_operation
            and len(known_operation) >= policy["maximum_tasks_per_operation"]
        ):
            rejected["operation_task_cap"] += 1
        else:
            task_views[task] += 1
            known_group.add(task)
            known_operation.add(task)
            selected.append(row)
    _verify_selected_readers(merged_dir, selected)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="candidate-selection-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        index_path = temp / "selection_index.jsonl"
        with index_path.open("x", encoding="utf-8") as stream:
            for rank, row in enumerate(selected):
                stream.write(
                    json.dumps(
                        {**row, "selection_rank": rank},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
        manifest = {
            "schema_version": SCHEMA,
            "input_manifest_sha256": _sha(merged_dir / "manifest.json"),
            "policy_sha256": _sha(policy_path),
            "source_views": source["candidate_views"],
            "selected_views": len(selected),
            "selected_independent_tasks": len(task_views),
            "selected_source_groups": sum(
                bool(tasks) for tasks in group_tasks.values()
            ),
            "selected_by_operation": dict(
                sorted(Counter(row["operation"] for row in selected).items())
            ),
            "selected_by_length": dict(
                sorted(Counter(row["length_bin"] for row in selected).items())
            ),
            "rejected_by_reason": dict(sorted(rejected.items())),
            "selection_index_sha256": _sha(index_path),
            "selection_scope": "review_priority_only",
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.rename(temp, output_dir)
    return manifest


def verify_selection(
    merged_dir: Path, policy_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Recompute the lightweight decision ledger against pinned source bytes."""
    output_dir = Path(output_dir)
    stored = _json(output_dir / "manifest.json")
    if stored.get("schema_version") != SCHEMA or stored.get(
        "selection_index_sha256"
    ) != _sha(output_dir / "selection_index.jsonl"):
        raise ValueError("selection manifest or index changed")
    with tempfile.TemporaryDirectory(
        prefix="candidate-selection-verify-", dir=output_dir.parent
    ) as raw:
        regenerated = select(merged_dir, policy_path, Path(raw) / "selection")
        if stored != regenerated or stored["selection_index_sha256"] != _sha(
            Path(raw) / "selection/selection_index.jsonl"
        ):
            raise ValueError("selection differs from pinned source or policy")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("merged_dir", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        verify_selection(args.merged_dir, args.policy, args.output)
        if args.verify_only
        else select(args.merged_dir, args.policy, args.output)
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "selected_views",
                    "selected_independent_tasks",
                    "rejected_by_reason",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
