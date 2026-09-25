"""Shared record tasks with a disclosed-as-of state dependency.

The same native record rows support raw comparison/aggregation and two state
operations.  State tasks first select a record scope, then apply visible
revocation events using both effective and disclosure dates.  They share
necessary record and event IDs; deletion is checked against the final reader
text.  This is controlled simulation, not a real-document adapter.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, timedelta
from typing import Any

from longworld.synthesis import capability_records as records
from longworld.synthesis import shared_record_taskbank as base_shared

VERSION = "longworld.p86-state-shared-world.v2"
STATE_OPS = ("asof_sum", "asof_complete_set")
RAW_OPS = ("group_compare", "filter_aggregate")
OPERATIONS = (*RAW_OPS, *STATE_OPS)
STATE_RULES = (
    "First apply the stated record filters in order. Records are active by "
    "default. A revoke event excludes its referenced record only when its "
    "effective date is on or before asof AND its reveal date is on or before "
    "known_at. Events with later effective or reveal dates do not apply. "
    "asof_sum adds the amounts of all active scoped records. "
    "asof_complete_set returns every active scoped record id, sorted. "
    "Record and event IDs are distinct; event rows are not records."
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse(context: str) -> tuple[list[records.Row], list[dict[str, Any]]]:
    lines = context.splitlines()
    if len(lines) < 2:
        raise ValueError("reader context has no rows")
    header = json.loads(lines[0])
    expected = {
        "schema": VERSION,
        "family": "shared_record_state",
        "record_rules": {name: records.PROTOCOLS[name] for name in RAW_OPS},
        "state_rules": STATE_RULES,
    }
    if header != expected:
        raise ValueError("unrecognized visible shared-state contract")
    rows: list[records.Row] = []
    events: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line in lines[1:]:
        item = json.loads(line)
        if item["id"] in ids:
            raise ValueError("duplicate visible fact id")
        ids.add(item["id"])
        if item["type"] == "record":
            rows.append(records._row_from_item(item))
        elif item["type"] == "revoke":
            if set(item) != {
                "id",
                "type",
                "record_id",
                "entity",
                "effective",
                "reveal",
            }:
                raise ValueError("malformed revoke event")
            date.fromisoformat(item["effective"])
            date.fromisoformat(item["reveal"])
            events.append(item)
        else:
            raise ValueError("unknown visible row type")
    by_id = {row.id: row for row in rows}
    event_keys: set[str] = set()
    for event in events:
        record_id = event["record_id"]
        if record_id not in by_id or event["entity"] != by_id[record_id].entity:
            raise ValueError("event target absent or entity mismatch")
        if record_id in event_keys:
            raise ValueError("multiple revoke events for one record")
        event_keys.add(record_id)
    return rows, events


def _state_result(
    rows: list[records.Row], events: list[dict[str, Any]], question: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    if question.get("operation") not in STATE_OPS:
        raise ValueError("unknown state operation")
    asof = date.fromisoformat(question["asof"])
    known_at = date.fromisoformat(question["known_at"])
    filters = question["filters"]
    scope_query = {
        "family": "filter_aggregate",
        "steps": [*filters, {"op": "aggregate", "how": "count"}],
    }
    # Native record executor establishes scope from the same visible rows;
    # state logic receives only its matched IDs, never generator bookkeeping.
    scope_answer = records.solve_visible(
        records.render_context(rows, "filter_aggregate"), scope_query
    )
    scoped = set(scope_answer["matched"])
    revoked = {
        event["record_id"]: event
        for event in events
        if event["record_id"] in scoped
        and date.fromisoformat(event["effective"]) <= asof
        and date.fromisoformat(event["reveal"]) <= known_at
    }
    active = [row for row in rows if row.id in scoped and row.id not in revoked]
    if not active or not revoked:
        raise ValueError("state task needs active and revoked scoped records")
    active_ids = sorted(row.id for row in active)
    consumed = sorted([*active_ids, *(event["id"] for event in revoked.values())])
    if question["operation"] == "asof_sum":
        return {"sum": sum(row.amount for row in active)}, consumed
    return {"record_ids": active_ids}, consumed


def solve_visible(context: str, task: dict[str, Any]) -> dict[str, Any]:
    """Replay one task using only the reader-visible header, rows and question."""
    rows, events = _parse(context)
    operation = task["operation"]
    if operation in RAW_OPS:
        return records.solve_visible(
            records.render_context(rows, operation), task["question"]
        )
    answer, _ = _state_result(rows, events, task["question"])
    return answer


def _instruction(question: dict[str, Any]) -> str:
    filters = question["filters"]
    filter_text = "; then ".join(
        "keep rows where "
        + " and ".join(
            f"{condition['field']} {condition['op']} {condition['value']}"
            for condition in step["conditions"]
        )
        for step in filters
    )
    target = (
        "return one JSON object with key sum"
        if question["operation"] == "asof_sum"
        else "return one JSON object with key record_ids, listing every active row id"
    )
    return (
        f"Apply the visible state rules: {filter_text}; use effective cutoff "
        f"{question['asof']} and disclosure cutoff {question['known_at']}; {target}."
    )


def _drop_fact(context: str, fact_id: str) -> str:
    lines = context.splitlines()
    kept = [lines[0]]
    removed = 0
    for line in lines[1:]:
        if json.loads(line)["id"] == fact_id:
            removed += 1
        else:
            kept.append(line)
    if removed != 1:
        raise ValueError("fact deletion is absent or ambiguous")
    return "\n".join(kept)


def _drop_record_support(context: str, record_id: str) -> str:
    """Remove a record and its referencing event so the text stays well formed."""
    lines = context.splitlines()
    kept = [lines[0]]
    removed_record = 0
    for line in lines[1:]:
        item = json.loads(line)
        if item["id"] == record_id:
            removed_record += 1
        elif item.get("record_id") != record_id:
            kept.append(line)
    if removed_record != 1:
        raise ValueError("record deletion is absent or ambiguous")
    return "\n".join(kept)


def _change_event_date(context: str, event_id: str, field: str, value: str) -> str:
    if field not in ("effective", "reveal"):
        raise ValueError("invalid event date field")
    date.fromisoformat(value)
    lines = context.splitlines()
    changed = 0
    for index, line in enumerate(lines[1:], start=1):
        item = json.loads(line)
        if item["id"] == event_id:
            item[field] = value
            lines[index] = _dump(item)
            changed += 1
    if changed != 1:
        raise ValueError("event date change is absent or ambiguous")
    return "\n".join(lines)


def _bounded_text_interventions(context: str, task: dict[str, Any]) -> dict[str, Any]:
    """Probe active records and relevant revocations in actual reader text."""
    original = solve_visible(context, task)
    record_ids = [fact_id for fact_id in task["consumed"] if fact_id.startswith("r")]
    event_ids = [fact_id for fact_id in task["consumed"] if fact_id.startswith("e")]
    probes = record_ids[:4] + event_ids[:4]
    if not record_ids or not event_ids:
        raise ValueError("state task has no necessary record/event overlap")
    for fact_id in probes:
        reduced = (
            _drop_record_support(context, fact_id)
            if fact_id.startswith("r")
            else _drop_fact(context, fact_id)
        )
        changed = solve_visible(reduced, task)
        if changed == original:
            raise ValueError(f"reader deletion did not change answer: {fact_id}")
    return {
        "probed_fact_ids": probes,
        "probed_records": min(4, len(record_ids)),
        "probed_events": min(4, len(event_ids)),
    }


def build_world(
    seed: int,
    length_records: int = 400,
    consumed_records: int = 20,
    depth: int = 2,
    n_variants: int = 2,
) -> dict[str, Any]:
    base = base_shared.build_world(
        seed, length_records, consumed_records, depth, n_variants
    )
    _, rows = records.parse_context(base["reader_context"])
    by_id = {row.id: row for row in rows}
    rng = random.Random(seed ^ 0x86A50F)
    events: list[dict[str, Any]] = []
    state_questions: dict[str, dict[str, Any]] = {}
    event_cases: dict[str, dict[str, list[str]]] = {}
    for index in range(n_variants):
        pair_id = f"q{index}"
        scope = base["shared_consumed_row_ids"][pair_id]
        if len(scope) < 6:
            raise ValueError("shared scope too small for state contrast")
        timely = list(rng.sample(scope, max(2, len(scope) // 3)))
        revoked = set(timely)
        remaining = [record_id for record_id in scope if record_id not in revoked]
        rng.shuffle(remaining)
        future_effective = set(remaining[: len(remaining) // 2])
        future_reveal = set(remaining[len(remaining) // 2 :])
        if len(future_effective) < 2 or len(future_reveal) < 2:
            raise ValueError("scope cannot host both temporal near-miss cases")
        asof_year = 2021 + index
        asof = date(asof_year, 6, 1)
        known_at = date(asof_year, 7, 1)
        cases = {
            "timely": [],
            "future_effective": [],
            "future_reveal": [],
            "effective_boundary": [],
            "reveal_boundary": [],
            "future_effective_boundary": [],
            "future_reveal_boundary": [],
        }
        for record_id in scope:
            row = by_id[record_id]
            event_id = (
                "e"
                + hashlib.sha256(f"{seed}:{index}:{record_id}".encode()).hexdigest()[
                    :24
                ]
            )
            if record_id in revoked:
                case = "timely"
                effective = (
                    asof if record_id == timely[0] else date(asof_year - 1, 3, 1)
                )
                reveal = known_at if record_id == timely[1] else date(asof_year, 3, 1)
            elif record_id in future_effective:
                case = "future_effective"
                effective = (
                    asof + timedelta(days=1)
                    if record_id == min(future_effective)
                    else date(asof_year, 10, 1)
                )
                reveal = date(asof_year, 3, 1)
            else:
                case = "future_reveal"
                effective = date(asof_year - 1, 3, 1)
                reveal = (
                    known_at + timedelta(days=1)
                    if record_id == min(future_reveal)
                    else date(asof_year, 10, 1)
                )
            events.append(
                {
                    "id": event_id,
                    "type": "revoke",
                    "record_id": record_id,
                    "entity": row.entity,
                    "effective": effective.isoformat(),
                    "reveal": reveal.isoformat(),
                }
            )
            cases[case].append(event_id)
            if effective == asof:
                cases["effective_boundary"].append(event_id)
            if reveal == known_at:
                cases["reveal_boundary"].append(event_id)
            if effective == asof + timedelta(days=1):
                cases["future_effective_boundary"].append(event_id)
            if reveal == known_at + timedelta(days=1):
                cases["future_reveal_boundary"].append(event_id)
        if any(not cases[name] for name in cases):
            raise ValueError("missing required temporal boundary case")
        event_cases[pair_id] = cases
        state_questions[pair_id] = {
            "filters": base["tasks"][2 * index]["question"]["steps"][:-1],
            "asof": asof.isoformat(),
            "known_at": known_at.isoformat(),
        }
    visible = [
        *(_dump(row.visible()) for row in rows),
        *(_dump(event) for event in events),
    ]
    rng.shuffle(visible)
    header = {
        "schema": VERSION,
        "family": "shared_record_state",
        "record_rules": {name: records.PROTOCOLS[name] for name in RAW_OPS},
        "state_rules": STATE_RULES,
    }
    context = "\n".join([_dump(header), *visible])
    parsed_rows, parsed_events = _parse(context)
    tasks: list[dict[str, Any]] = []
    for index in range(n_variants):
        pair_id = f"q{index}"
        tasks.extend(dict(task) for task in base["tasks"][2 * index : 2 * index + 2])
        for operation in STATE_OPS:
            question = {"operation": operation, **state_questions[pair_id]}
            answer, consumed = _state_result(parsed_rows, parsed_events, question)
            task = {
                "task_id": f"{pair_id}:{operation}",
                "pair_id": pair_id,
                "operation": operation,
                "question": question,
                "instruction": _instruction(question),
                "answer": answer,
                "consumed": consumed,
            }
            task["text_interventions"] = _bounded_text_interventions(context, task)
            tasks.append(task)
    temporal_contrast_checks = 0
    for index in range(n_variants):
        pair_id = f"q{index}"
        cases = event_cases[pair_id]
        asof = state_questions[pair_id]["asof"]
        known_at = state_questions[pair_id]["known_at"]
        contrasts = (
            (cases["future_effective_boundary"][0], "effective", asof),
            (cases["future_reveal_boundary"][0], "reveal", known_at),
            (
                cases["effective_boundary"][0],
                "effective",
                (date.fromisoformat(asof) + timedelta(days=1)).isoformat(),
            ),
            (
                cases["reveal_boundary"][0],
                "reveal",
                (date.fromisoformat(known_at) + timedelta(days=1)).isoformat(),
            ),
        )
        for task in tasks:
            if task["pair_id"] != pair_id or task["operation"] not in STATE_OPS:
                continue
            for event_id, field, new_date in contrasts:
                changed = solve_visible(
                    _change_event_date(context, event_id, field, new_date), task
                )
                if changed == task["answer"]:
                    raise ValueError(
                        "effective/reveal boundary failed to affect answer"
                    )
                temporal_contrast_checks += 1
    shared_fact_ids: dict[str, dict[str, list[str]]] = {}
    for index in range(n_variants):
        pair_id = f"q{index}"
        cohort = [task for task in tasks if task["pair_id"] == pair_id]
        raw_ids = set(cohort[0]["consumed"]) & set(cohort[1]["consumed"])
        state_ids = set(cohort[2]["consumed"]) & set(cohort[3]["consumed"])
        cross_ids = raw_ids & state_ids
        if not cross_ids or not any(value.startswith("e") for value in state_ids):
            raise ValueError("operations do not share necessary record and event facts")
        shared_fact_ids[pair_id] = {
            "raw_record_ids": sorted(raw_ids),
            "state_fact_ids": sorted(state_ids),
            "cross_operation_record_ids": sorted(cross_ids),
        }
    context_sha = hashlib.sha256(context.encode()).hexdigest()
    return {
        "schema_version": VERSION,
        "world_id": f"p86-state-shared-{context_sha[:20]}",
        "seed": seed,
        "length_records": length_records,
        "consumed_records": consumed_records,
        "depth": depth,
        "n_variants": n_variants,
        "reader_context": context,
        "context_sha256": context_sha,
        "tasks": tasks,
        "shared_fact_ids": shared_fact_ids,
        "event_cases": event_cases,
        "temporal_contrast_checks": temporal_contrast_checks,
        "honesty": dict(records.HONESTY),
    }


def validate_world(world: dict[str, Any]) -> dict[str, Any]:
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
            errors.append("world differs from deterministic rebuild")
        if (
            hashlib.sha256(world["reader_context"].encode()).hexdigest()
            != world["context_sha256"]
        ):
            errors.append("reader context hash mismatch")
        for task in world["tasks"]:
            if solve_visible(world["reader_context"], task) != task["answer"]:
                errors.append(f"reader solver mismatch: {task['task_id']}")
    except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"passed": not errors, "errors": errors}
