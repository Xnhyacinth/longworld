"""Report a multi-axis distribution over the P76 inventory and native task specs.

The unified index is a metadata inventory. This report joins only the native
P64 task IDs that are actually present in it; missing operation/topic metadata
remains unknown instead of being guessed from product or domain names.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _native_operations(root: Path, domain: str) -> dict[str, str]:
    operations = {}
    for path in sorted(root.glob("*/tasks.jsonl")):
        for line in path.read_text("utf-8").splitlines():
            row = json.loads(line)
            task_id = row["semantic_task_id"]
            operation = (
                row["task_spec"]["family"] if domain == "finance" else row["program_id"]
            )
            old = operations.setdefault(task_id, operation)
            if old != operation:
                raise ValueError(f"native operation changed for {task_id}")
    return operations


def report(index: Path, finance: Path, codeforge: Path) -> dict:
    native = {
        "finance": _native_operations(finance, "finance"),
        "codeforge": _native_operations(codeforge, "codeforge"),
    }
    cells = Counter()
    by_operation = Counter()
    by_domain = Counter()
    topics = Counter()
    missing_native = Counter()
    source_groups: dict[str, set[str]] = defaultdict(set)
    with index.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if not row["new_independent_task"]:
                continue
            domain = row.get("domain") or "unknown"
            operation = row.get("operation") or row.get("family")
            if operation is None and row["product"] == "p64":
                operation = native.get(domain, {}).get(row["task_id"])
                if operation is None:
                    missing_native[domain] += 1
            operation = operation or "unknown"
            source_kind = row.get("source_kind") or "unknown"
            length = row.get("length_bin") or "unknown"
            cells[(source_kind, domain, operation, length)] += 1
            by_operation[operation] += 1
            by_domain[domain] += 1
            topic = row.get("topic")
            topics[topic if topic else "unknown"] += 1
            if row.get("source_group"):
                source_groups[source_kind].add(str(row["source_group"]))
    return {
        "schema": "longworld.p76.distribution.v1",
        "unique_tasks": sum(cells.values()),
        "note": "Native P64 operation joined by semantic_task_id; other missing operation and topic fields are unknown. This is not a train-ready distribution.",
        "source_groups_by_kind": {
            key: len(value) for key, value in sorted(source_groups.items())
        },
        "domains": dict(sorted(by_domain.items())),
        "operations": dict(sorted(by_operation.items())),
        "topics": dict(sorted(topics.items())),
        "cells": [
            {
                "source_kind": source_kind,
                "domain": domain,
                "operation": operation,
                "length_bin": length,
                "tasks": count,
            }
            for (source_kind, domain, operation, length), count in sorted(cells.items())
        ],
        "missing_native_operation": dict(sorted(missing_native.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--finance", required=True, type=Path)
    parser.add_argument("--codeforge", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = report(args.index, args.finance, args.codeforge)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
