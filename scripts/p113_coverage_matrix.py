"""Recompute candidate/selection coverage from frozen sharded metadata.

This is an inventory, not an eligibility or dependency certificate. In
particular, source-group IDs do not certify source connected components.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from longworld.synthesis.sharded_candidate_bank import verify_index

SCHEMA = "longworld.p113-coverage-matrix.v1"
DIMENSIONS = (
    "source_kind",
    "domain",
    "topic",
    "operation",
    "length_bin",
    "dependency_status",
    "split",
)
SOURCE_FIELDS = (
    "source_group",
    "source_name",
    "native_row_ref",
    "receipt_sha256",
    "domain",
    "topic",
    "dependency_status",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _label(value: Any) -> str:
    return "<null>" if value is None else str(value)


def coverage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Count true task identities separately from views and label vocabulary."""
    dimensions: dict[str, dict[str, Any]] = {}
    for field in DIMENSIONS:
        groups: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "views": 0,
                "tasks": set(),
                "input_tokens": 0,
                "supervised_tokens": 0,
            }
        )
        for entry in entries:
            row = entry["candidate"]
            slot = groups[_label(row.get(field))]
            slot["views"] += 1
            slot["tasks"].add((row["source_kind"], row["semantic_task_id"]))
            slot["input_tokens"] += row["input_tokens"]
            slot["supervised_tokens"] += row["supervised_tokens"]
        dimensions[field] = {
            label: {
                **{key: value for key, value in slot.items() if key != "tasks"},
                "tasks": len(slot["tasks"]),
            }
            for label, slot in sorted(groups.items())
        }

    group_tasks: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    group_operations: dict[tuple[str, str], set[str]] = defaultdict(set)
    group_splits: dict[tuple[str, str], set[str]] = defaultdict(set)
    context_splits: dict[str, set[str]] = defaultdict(set)
    tasks: set[tuple[str, str]] = set()
    missing = Counter()
    total_input = total_supervised = 0
    for entry in entries:
        row = entry["candidate"]
        kind, task_id = row["source_kind"], row["semantic_task_id"]
        group = kind, row["source_group"]
        task = kind, task_id
        tasks.add(task)
        group_tasks[group].add(task)
        group_operations[group].add(row["operation"])
        group_splits[group].add(row["split"])
        if row.get("context_sha256"):
            context_splits[row["context_sha256"]].add(row["split"])
        for field in SOURCE_FIELDS:
            if row.get(field) in (None, "", "unknown", "unmeasured"):
                missing[field] += 1
        if row["input_tokens"] + row["supervised_tokens"] != row["full_chat_tokens"]:
            raise ValueError("final-chat token accounting differs")
        total_input += row["input_tokens"]
        total_supervised += row["supervised_tokens"]
    overlap = sorted(
        f"{kind}:{group}"
        for (kind, group), splits in group_splits.items()
        if len(splits) > 1
    )
    return {
        "views": len(entries),
        "independent_semantic_tasks": len(tasks),
        "typed_source_groups": len(group_tasks),
        "distinct_context_hashes": len(context_splits),
        "input_tokens": total_input,
        "supervised_tokens": total_supervised,
        "supervised_fraction": total_supervised / (total_input + total_supervised)
        if entries
        else 0,
        "dimensions": dimensions,
        "multi_operation_groups": sum(
            len(ops) > 1 for ops in group_operations.values()
        ),
        "multi_task_groups": sum(
            len(task_set) > 1 for task_set in group_tasks.values()
        ),
        "groups_by_operation_count": {
            str(count): n
            for count, n in sorted(Counter(map(len, group_operations.values())).items())
        },
        "source_group_train_eval_overlap": overlap,
        "exact_context_hash_train_eval_overlap": sum(
            len(splits) > 1 for splits in context_splits.values()
        ),
        "source_metadata_missing_views": {
            field: missing[field] for field in SOURCE_FIELDS
        },
        "source_connected_component_split": "unknown: no connected-component identity in candidate metadata",
    }


def build_report(index_dir: Path, selection_dir: Path) -> dict[str, Any]:
    index = verify_index(index_dir)
    selection_path = selection_dir / "selected_refs.jsonl"
    selection_manifest_path = selection_dir / "manifest.json"
    selection = json.loads(selection_manifest_path.read_text(encoding="utf-8"))
    if (
        selection.get("train_ready") is not False
        or selection.get("input_manifest_sha256") != _sha(index_dir / "manifest.json")
        or selection.get("input_refs_sha256") != index["refs_sha256"]
        or selection.get("selected_refs_sha256") != _sha(selection_path)
    ):
        raise ValueError("selection is not bound to the frozen candidate index")
    candidates = _rows(index_dir / "candidate_refs.jsonl")
    selected = _rows(selection_path)
    candidate_by_id = {entry["candidate"]["sample_id"]: entry for entry in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("candidate sample ID repeats")
    selected_ids: set[str] = set()
    for rank, entry in enumerate(selected):
        sample_id = entry["candidate"]["sample_id"]
        if (
            entry.get("selection_rank") != rank
            or sample_id in selected_ids
            or candidate_by_id.get(sample_id)
            != {key: value for key, value in entry.items() if key != "selection_rank"}
        ):
            raise ValueError("selected row differs from frozen candidate index")
        selected_ids.add(sample_id)
    rejected = [
        entry
        for entry in candidates
        if entry["candidate"]["sample_id"] not in selected_ids
    ]
    selected_coverage = coverage(selected)
    if (
        len(candidates) != selection["before"]["views"]
        or selected_coverage["views"] != selection["after"]["views"]
        or selected_coverage["independent_semantic_tasks"]
        != selection["after"]["independent_semantic_tasks"]
        or selected_coverage["typed_source_groups"]
        != selection["after"]["source_groups"]
    ):
        raise ValueError("selected coverage differs from selection manifest")
    if "eligible" in selection and not (
        len(selected) <= selection["eligible"]["views"] <= len(candidates)
    ):
        raise ValueError("selection eligibility stage has impossible view count")
    return {
        "schema_version": SCHEMA,
        "index_manifest_sha256": _sha(index_dir / "manifest.json"),
        "index_refs_sha256": index["refs_sha256"],
        "selection_manifest_sha256": _sha(selection_manifest_path),
        "selected_refs_sha256": selection["selected_refs_sha256"],
        "candidate": coverage(candidates),
        "selected": selected_coverage,
        "unselected": coverage(rejected),
        "selection_receipt_stages": {
            stage: {
                key: selection[stage][key]
                for key in ("views", "independent_semantic_tasks", "source_groups")
            }
            for stage in ("before", "eligible", "after")
            if stage in selection
        },
        "preselection_excluded_views": (
            selection["before"]["views"] - selection["eligible"]["views"]
            if "eligible" in selection
            else None
        ),
        "eligible_not_selected_views": (
            selection["eligible"]["views"] - selection["after"]["views"]
            if "eligible" in selection
            else None
        ),
        "unselected_reason": "unknown: selected-ref difference is not a rejection-reason ledger",
        "interpretation": "domain/topic/operation are metadata labels, not independent mechanisms; typed source groups are not certified source connected components",
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = build_report(args.index, args.selection)
    if args.verify_only:
        if json.loads(args.output.read_text(encoding="utf-8")) != report:
            raise ValueError("coverage report does not replay")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(
        json.dumps(
            {
                "candidate_views": report["candidate"]["views"],
                "selected_tasks": report["selected"]["independent_semantic_tasks"],
                "selected_groups": report["selected"]["typed_source_groups"],
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
