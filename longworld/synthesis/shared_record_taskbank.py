"""Two record operations over the same generated entities and evidence rows.

This controlled pilot deliberately starts with one native record world.  A
second program is compiled from each original program's filter chain; no
family-specific rows are appended.  Sharing means both operations actually
consume the same visible row IDs, not merely the same context hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from longworld.synthesis import capability_records as records

VERSION = "longworld.shared-record-taskbank.v1"
OPERATIONS = ("group_compare", "filter_aggregate")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _solve_context(reader_context: str, operation: str) -> str:
    header, separator, body = reader_context.partition("\n")
    if not separator:
        raise ValueError("reader context has no record rows")
    visible = json.loads(header)
    if visible.get("schema") != VERSION or visible.get("rules") != {
        name: records.PROTOCOLS[name] for name in OPERATIONS
    }:
        raise ValueError("shared reader contract is missing an operation")
    return records.render_context([], operation) + "\n" + body


def _value_changes_when_removed(
    context: str, program: dict[str, Any], answer: dict[str, Any], row_id: str
) -> bool:
    lines = context.splitlines()
    retained = [lines[0]]
    removed = 0
    for line in lines[1:]:
        if json.loads(line)["id"] == row_id:
            removed += 1
        else:
            retained.append(line)
    if removed != 1:
        raise ValueError("consumed row is absent or repeated in reader text")
    try:
        changed = records.solve_visible("\n".join(retained), program)
    except ValueError:
        return True
    return records.answer_value(changed) != records.answer_value(answer)


def build_world(
    seed: int,
    length_records: int = 400,
    consumed_records: int = 20,
    depth: int = 2,
    n_variants: int = 2,
) -> dict[str, Any]:
    """Compile paired operations from one native row space; fail on false sharing."""
    base = records.generate_world(
        seed, "group_compare", length_records, consumed_records, depth, n_variants
    )
    checked = records.validate_bundle(base)
    if not checked["passed"]:
        raise ValueError(f"native world failed validation: {checked['errors']}")
    _, rows = records.parse_context(base["context"])
    reader_header = {
        "schema": VERSION,
        "family": "shared_record",
        "rules": {name: records.PROTOCOLS[name] for name in OPERATIONS},
    }
    reader_context = "\n".join(
        [_dump(reader_header), *(_dump(row.visible()) for row in rows)]
    )
    if [_dump(row.visible()) for row in rows] != base["context"].splitlines()[1:]:
        raise ValueError("reader rows differ from the validated native world")
    tasks: list[dict[str, Any]] = []
    for index, original in enumerate(base["tasks"]):
        group_program = original["question"]
        # Alternate the terminal statistic, so the second target is not just
        # the arithmetic sum of the two reported group values.
        group_how = group_program["steps"][-1]["how"]
        aggregate_program = {
            "family": "filter_aggregate",
            "steps": [
                *group_program["steps"][:-1],
                {"op": "aggregate", "how": "count" if group_how == "sum" else "sum"},
            ],
        }
        for operation, program, phrasing_index in (
            ("group_compare", group_program, original["phrasing_index"]),
            (
                "filter_aggregate",
                aggregate_program,
                (seed + index) % len(records.PROMPTS["filter_aggregate"]),
            ),
        ):
            solve_context = _solve_context(reader_context, operation)
            answer = records.solve_visible(solve_context, program)
            consumed = records._executor_provenance(rows, program)
            if len(consumed) < 4:
                raise ValueError("operation consumed too few rows")
            tasks.append(
                {
                    "task_id": f"q{index}:{operation}",
                    "pair_id": f"q{index}",
                    "operation": operation,
                    "question": program,
                    "instruction": records.render_instruction(
                        operation, program, phrasing_index
                    ),
                    "phrasing_index": phrasing_index,
                    "answer": answer,
                    "consumed": consumed,
                }
            )
    for index in range(n_variants):
        pair = tasks[2 * index : 2 * index + 2]
        if pair[0]["consumed"] != pair[1]["consumed"]:
            raise ValueError("operations do not share their consumed record rows")
        if pair[0]["consumed"] != base["tasks"][index]["consumed"]:
            raise ValueError("shared row provenance differs from native world")
        for task in pair:
            solve_context = _solve_context(reader_context, task["operation"])
            for row_id in task["consumed"]:
                if not _value_changes_when_removed(
                    solve_context, task["question"], task["answer"], row_id
                ):
                    raise ValueError(
                        "shared row is not necessary to the computed value"
                    )
    context_sha = hashlib.sha256(reader_context.encode()).hexdigest()
    return {
        "schema_version": VERSION,
        "world_id": f"shared-record-{context_sha[:20]}",
        "seed": seed,
        "length_records": length_records,
        "consumed_records": consumed_records,
        "depth": depth,
        "n_variants": n_variants,
        "reader_context": reader_context,
        "context_sha256": context_sha,
        "tasks": tasks,
        "shared_consumed_row_ids": {
            f"q{index}": tasks[2 * index]["consumed"] for index in range(n_variants)
        },
        "honesty": dict(records.HONESTY),
    }


def validate_world(world: dict[str, Any]) -> dict[str, Any]:
    """Rebuild from the seed and independently replay every final reader row."""
    errors: list[str] = []
    try:
        expected = build_world(
            world["seed"],
            world["length_records"],
            world["consumed_records"],
            world["depth"],
            world["n_variants"],
        )
        if world != expected:
            errors.append("world differs from deterministic, native-validated rebuild")
        if (
            hashlib.sha256(world["reader_context"].encode()).hexdigest()
            != world["context_sha256"]
        ):
            errors.append("reader context hash mismatch")
        for task in world["tasks"]:
            operation = task["operation"]
            context = _solve_context(world["reader_context"], operation)
            if records.solve_visible(context, task["question"]) != task["answer"]:
                errors.append(f"reader replay mismatch: {task['task_id']}")
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"passed": not errors, "errors": errors}
