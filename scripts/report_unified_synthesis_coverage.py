"""Report actual world, task, source and length coverage from final reader rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.unified-coverage.v1"


def report(merged_dir: Path) -> dict:
    manifest = verify_merge(merged_dir)
    counters = {
        name: Counter()
        for name in (
            "source_kind",
            "source_name",
            "domain",
            "topic",
            "operation",
            "length_bin",
            "split",
            "evidence_profile",
            "dependency_status",
        )
    }
    by_domain_operation = Counter()
    by_operation_length = Counter()
    by_operation_dependency = Counter()
    tasks_by_operation: dict[str, set[tuple[str, str]]] = defaultdict(set)
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    group_splits: dict[tuple[str, str], str] = {}
    task_keys: set[str] = set()
    global_tasks: dict[tuple[str, str], tuple[str, str]] = {}
    contexts: set[str] = set()
    full_tokens: list[int] = []
    input_tokens = 0
    supervised_tokens = 0
    rows = 0
    with (merged_dir / "sample_index.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            rows += 1
            for name, counter in counters.items():
                counter[str(row.get(name) or "unmeasured")] += 1
            group = (row["source_kind"], row["source_group"])
            groups[group].add(row["operation"])
            prior = group_splits.setdefault(group, row["split"])
            if prior != row["split"]:
                raise ValueError("source group crosses split in final index")
            task_keys.add(row["task_key"])
            global_key = (row["source_kind"], row["semantic_task_id"])
            answer = (row["split"], row["answer_sha256"])
            prior_answer = global_tasks.setdefault(global_key, answer)
            if prior_answer != answer:
                raise ValueError(
                    "semantic task changes split or answer across source groups"
                )
            contexts.add(row["context_sha256"])
            full_tokens.append(row["full_chat_tokens"])
            input_tokens += row["input_tokens"]
            supervised_tokens += row["supervised_tokens"]
            by_domain_operation[(row["domain"], row["operation"])] += 1
            by_operation_length[(row["operation"], row["length_bin"])] += 1
            by_operation_dependency[
                (row["operation"], row.get("dependency_status") or "unmeasured")
            ] += 1
            tasks_by_operation[row["operation"]].add(global_key)
    if rows != manifest["candidate_views"] or not full_tokens:
        raise ValueError("final reader index count mismatch")
    by_kind_groups = Counter(kind for kind, _ in groups)
    multi_operation_groups = Counter(
        kind for (kind, _), operations in groups.items() if len(operations) > 1
    )
    return {
        "schema_version": SCHEMA,
        "input_manifest_sha256": hashlib.sha256(
            (merged_dir / "manifest.json").read_bytes()
        ).hexdigest(),
        "candidate_views": rows,
        "source_scoped_semantic_tasks": len(task_keys),
        "independent_semantic_tasks": len(global_tasks),
        "source_groups": len(groups),
        "source_groups_by_kind": dict(sorted(by_kind_groups.items())),
        "source_groups_with_multiple_operations": sum(
            len(ops) > 1 for ops in groups.values()
        ),
        "source_groups_with_multiple_operations_by_kind": dict(
            sorted(multi_operation_groups.items())
        ),
        "operations_per_source_group": dict(
            sorted(Counter(len(ops) for ops in groups.values()).items())
        ),
        "shared_facts_across_operations": "unmeasured",
        "unique_reader_contexts": len(contexts),
        "full_chat_tokens": {"min": min(full_tokens), "max": max(full_tokens)},
        "loss_mask_tokens": {
            "input": input_tokens,
            "supervised": supervised_tokens,
            "measurement": "native_index_counts; not independent retokenization",
        },
        "counts": {
            name: dict(sorted(counter.items())) for name, counter in counters.items()
        },
        "domain_operation": [
            {"domain": domain, "operation": operation, "views": count}
            for (domain, operation), count in sorted(by_domain_operation.items())
        ],
        "operation_length": [
            {"operation": operation, "length_bin": length, "views": count}
            for (operation, length), count in sorted(by_operation_length.items())
        ],
        "operation_dependency_status": [
            {"operation": operation, "status": status, "views": count}
            for (operation, status), count in sorted(by_operation_dependency.items())
        ],
        "independent_tasks_by_operation": {
            operation: len(tasks)
            for operation, tasks in sorted(tasks_by_operation.items())
        },
        "length_bin_intervals_full_chat_tokens": {
            "lt32k": "[0,32768)",
            "32k": "[32768,65536)",
            "64k": "[65536,131072)",
            "128k": "[131072,262144)",
            "ge256k": "[262144,infinity)",
        },
        "train_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("merged_dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = report(args.merged_dir)
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.output.exists():
        if args.output.read_text() != raw:
            raise ValueError("coverage report changed")
    else:
        args.output.write_text(raw)
    print(
        json.dumps(
            {
                key: value[key]
                for key in (
                    "candidate_views",
                    "source_scoped_semantic_tasks",
                    "source_groups",
                    "source_groups_with_multiple_operations",
                    "full_chat_tokens",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
