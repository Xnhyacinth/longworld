"""Compile new partial-reversal and authorization-hold state worlds.

Both mechanisms replay from final visible rows. The QA and one-step policy
lanes are separate, and neither is promoted to training automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import capability_records as records
from longworld.synthesis import shared_record_taskbank as shared
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    NativeCandidate,
    normalize_native_candidate,
)
from scripts import p114_controlled_action_feedback as policy
from scripts.audit_unified_reader_mask import audit_reader
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p133-state-mechanism-batch.v1"
WORLD_SCHEMA = "longworld.p133-state-world.v1"
OUTPUT_SCHEMA = "longworld.p133-state-mechanism-output.v1"
MARKER = "\n\nQUESTION\n"
MECHANISMS = ("partial_reversal", "authorization_hold")
RULES = {
    "partial_reversal": (
        "Apply the stated record filters in order. Each selected record starts "
        "with its amount. A partial_reversal event subtracts its units only "
        "when effective <= asof AND reveal <= known_at. Multiple applicable "
        "reversals add; a residual must stay nonnegative. Report the sum or "
        "the complete sorted record-id/residual list as requested."
    ),
    "authorization_hold": (
        "Apply the stated record filters in order. A hold sets its record's "
        "residual to zero only when effective <= asof AND reveal <= known_at. "
        "A matching grant restores the original amount only when its role is "
        "controller, effective <= asof, reveal <= known_at, and expires >= asof. "
        "Events outside these cutoffs do not apply. Report the sum or the "
        "complete sorted record-id/residual list as requested."
    ),
}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse(
    context: str,
) -> tuple[dict[str, Any], list[records.Row], list[dict[str, Any]]]:
    lines = context.splitlines()
    if len(lines) < 3:
        raise ValueError("state world has no visible rows")
    header = json.loads(lines[0])
    mechanism = header.get("mechanism")
    if header != {
        "schema": WORLD_SCHEMA,
        "mechanism": mechanism,
        "rules": RULES.get(mechanism),
        "filter_rule": records.PROTOCOLS["filter_aggregate"],
    }:
        raise ValueError("visible state mechanism/rules differ")
    rows: list[records.Row] = []
    events: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line in lines[1:]:
        item = json.loads(line)
        if item.get("id") in ids:
            raise ValueError("duplicate visible row ID")
        ids.add(item["id"])
        if item.get("type") == "record":
            rows.append(records._row_from_item(item))
        else:
            events.append(item)
    by_id = {row.id: row for row in rows}
    event_by_id = {item["id"]: item for item in events}
    for event in events:
        kind = event.get("type")
        if kind in {"partial_reversal", "hold"}:
            expected = {"id", "type", "record_id", "entity", "effective", "reveal"}
            if kind == "partial_reversal":
                expected.add("units")
            if set(event) != expected:
                raise ValueError("malformed state-changing event")
            target = by_id.get(event["record_id"])
            if target is None or target.entity != event["entity"]:
                raise ValueError("event target absent or entity mismatch")
            if kind == "partial_reversal" and (
                type(event["units"]) is not int
                or not 0 < event["units"] < target.amount
            ):
                raise ValueError("invalid partial reversal units")
        elif kind == "grant":
            if set(event) != {
                "id",
                "type",
                "hold_id",
                "record_id",
                "entity",
                "role",
                "effective",
                "reveal",
                "expires",
            }:
                raise ValueError("malformed authorization grant")
            hold = event_by_id.get(event["hold_id"])
            if (
                hold is None
                or hold.get("type") != "hold"
                or hold["record_id"] != event["record_id"]
                or hold["entity"] != event["entity"]
                or event["role"] not in {"controller", "viewer"}
            ):
                raise ValueError("grant lacks matching visible hold")
            date.fromisoformat(event["expires"])
        else:
            raise ValueError("unknown visible transition event")
        date.fromisoformat(event["effective"])
        date.fromisoformat(event["reveal"])
    if mechanism == "partial_reversal" and any(
        event["type"] != "partial_reversal" for event in events
    ):
        raise ValueError("partial world contains different events")
    if mechanism == "authorization_hold" and any(
        event["type"] not in {"hold", "grant"} for event in events
    ):
        raise ValueError("authorization world contains different events")
    return header, rows, events


def _scope(rows: list[records.Row], filters: list[dict[str, Any]]) -> list[str]:
    query = {
        "family": "filter_aggregate",
        "steps": [*filters, {"op": "aggregate", "how": "count"}],
    }
    result = records.solve_visible(
        records.render_context(rows, "filter_aggregate"), query
    )
    return result["matched"]


def solve_visible(context: str, question: dict[str, Any]) -> dict[str, Any]:
    """Compute all residuals from the exact header, rows and query cutoffs."""
    header, rows, events = _parse(context)
    if question.get("operation") not in {"net_sum", "residual_entries"}:
        raise ValueError("unknown P133 state operation")
    asof = date.fromisoformat(question["asof"])
    known_at = date.fromisoformat(question["known_at"])
    scoped = set(_scope(rows, question["filters"]))
    if len(scoped) < 6:
        raise ValueError("too few scoped records")
    current = {row.id: row.amount for row in rows if row.id in scoped}
    applicable = lambda event: (
        date.fromisoformat(event["effective"]) <= asof
        and date.fromisoformat(event["reveal"]) <= known_at
    )
    if header["mechanism"] == "partial_reversal":
        for event in events:
            if event["record_id"] in scoped and applicable(event):
                current[event["record_id"]] -= event["units"]
        if any(value < 0 for value in current.values()):
            raise ValueError("partial reversals exceed record amount")
    else:
        active_holds = {
            event["id"]: event
            for event in events
            if event["type"] == "hold"
            and event["record_id"] in scoped
            and applicable(event)
        }
        grants = {
            event["hold_id"]
            for event in events
            if event["type"] == "grant"
            and event["hold_id"] in active_holds
            and event["role"] == "controller"
            and applicable(event)
            and date.fromisoformat(event["expires"]) >= asof
        }
        for hold_id, hold in active_holds.items():
            if hold_id not in grants:
                current[hold["record_id"]] = 0
    entries = [{"id": key, "residual": current[key]} for key in sorted(current)]
    return (
        {"sum": sum(current.values())}
        if question["operation"] == "net_sum"
        else {"entries": entries}
    )


def _event_id(seed: int, pair: int, row_id: str, kind: str) -> str:
    return (
        "e" + hashlib.sha256(f"{seed}:{pair}:{row_id}:{kind}".encode()).hexdigest()[:24]
    )


def build_world(
    seed: int,
    mechanism: str,
    length_records: int,
    consumed: int,
    depth: int,
    variants: int,
) -> dict[str, Any]:
    if mechanism not in MECHANISMS:
        raise ValueError("unknown state mechanism")
    base = shared.build_world(seed, length_records, consumed, depth, variants)
    row_items = [json.loads(line) for line in base["reader_context"].splitlines()[1:]]
    by_id = {item["id"]: item for item in row_items}
    rng = random.Random(seed ^ (0x133A if mechanism == "partial_reversal" else 0x133B))
    events: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for pair in range(variants):
        scoped_ids = base["shared_consumed_row_ids"][f"q{pair}"]
        if len(scoped_ids) < 12:
            raise ValueError("base scope too small")
        chosen = rng.sample(scoped_ids, 8)
        asof = date(2021 + pair, 6, 1)
        known_at = date(2021 + pair, 7, 1)
        for index, record_id in enumerate(chosen):
            record = by_id[record_id]
            effective = asof + timedelta(days=1) if index == 6 else date(2020, 1, 1)
            reveal = known_at + timedelta(days=1) if index == 7 else date(2020, 2, 1)
            common = {
                "id": _event_id(seed, pair, record_id, mechanism),
                "record_id": record_id,
                "entity": record["entity"],
                "effective": effective.isoformat(),
                "reveal": reveal.isoformat(),
            }
            if mechanism == "partial_reversal":
                units = min(record["amount"] - 1, max(200, record["amount"] // 2))
                events.append({**common, "type": "partial_reversal", "units": units})
            else:
                hold = {**common, "type": "hold"}
                events.append(hold)
                if index in {0, 1}:
                    events.append(
                        {
                            "id": _event_id(seed, pair, record_id, "grant"),
                            "type": "grant",
                            "hold_id": hold["id"],
                            "record_id": record_id,
                            "entity": record["entity"],
                            "role": "controller" if index == 0 else "viewer",
                            "effective": date(2020, 3, 1).isoformat(),
                            "reveal": date(2020, 4, 1).isoformat(),
                            "expires": (asof + timedelta(days=30)).isoformat(),
                        }
                    )
        filters = base["tasks"][2 * pair]["question"]["steps"][:-1]
        for operation in ("net_sum", "residual_entries"):
            questions.append(
                {
                    "task_id": f"q{pair}:{operation}",
                    "question": {
                        "operation": operation,
                        "filters": filters,
                        "asof": asof.isoformat(),
                        "known_at": known_at.isoformat(),
                    },
                }
            )
    visible = [*(dump(item) for item in row_items), *(dump(item) for item in events)]
    rng.shuffle(visible)
    header = {
        "schema": WORLD_SCHEMA,
        "mechanism": mechanism,
        "rules": RULES[mechanism],
        "filter_rule": records.PROTOCOLS["filter_aggregate"],
    }
    context = "\n".join([dump(header), *visible])
    tasks = [
        {**item, "answer": solve_visible(context, item["question"])}
        for item in questions
    ]
    if (
        len(
            {
                item["answer"]["sum"]
                for item in tasks
                if item["question"]["operation"] == "net_sum"
            }
        )
        != variants
    ):
        raise ValueError("world scopes do not yield distinct states")
    context_sha = digest(context)
    return {
        "schema_version": WORLD_SCHEMA,
        "world_id": f"p133-{mechanism}-{context_sha[:20]}",
        "seed": seed,
        "mechanism": mechanism,
        "length_records": length_records,
        "consumed_records": consumed,
        "depth": depth,
        "scope_variants": variants,
        "base_record_world_id": base["world_id"],
        "base_record_context_sha256": base["context_sha256"],
        "reader_context": context,
        "context_sha256": context_sha,
        "tasks": tasks,
    }


def _drop(context: str, fact_id: str) -> tuple[str, list[str]]:
    lines = context.splitlines()
    body = [json.loads(line) for line in lines[1:]]
    selected = next((item for item in body if item["id"] == fact_id), None)
    if selected is None:
        raise ValueError("missing intervention fact")
    removed = {fact_id}
    if selected["type"] == "record":
        removed.update(item["id"] for item in body if item.get("record_id") == fact_id)
    if selected["type"] == "hold":
        removed.update(item["id"] for item in body if item.get("hold_id") == fact_id)
    kept = [lines[0], *(dump(item) for item in body if item["id"] not in removed)]
    return "\n".join(kept), sorted(removed)


def _state_witnesses(
    context: str, task: dict[str, Any], target: int, sign: int
) -> list[tuple[str, int, list[str]]]:
    balance = solve_visible(context, task["question"])["sum"]
    chosen, _ = policy.decision(balance, target, 500)
    _, rows, events = _parse(context)
    scoped = set(_scope(rows, task["question"]["filters"]))
    candidates = (
        [
            item["id"]
            for item in events
            if item["type"] in {"partial_reversal", "hold"}
            and item["record_id"] in scoped
        ]
        if sign > 0
        else [row.id for row in rows if row.id in scoped]
    )
    flipped = []
    for fact_id in candidates:
        reduced, removed = _drop(context, fact_id)
        try:
            changed = solve_visible(reduced, task["question"])["sum"]
            alternate, _ = policy.decision(changed, target, 500)
        except ValueError:
            continue
        if alternate != chosen:
            flipped.append((fact_id, changed, removed))
    return flipped


def _filter_text(question: dict[str, Any]) -> str:
    return "; then ".join(
        "keep rows where "
        + " and ".join(
            f"{condition['field']} {condition['op']} {condition['value']}"
            for condition in step["conditions"]
        )
        for step in question["filters"]
    )


def _question(
    question: dict[str, Any],
    *,
    target: int | None = None,
    menu: tuple[str, str] | None = None,
) -> str:
    common = (
        "Use the visible transition rules on these filtered records: "
        f"{_filter_text(question)}. Effective cutoff {question['asof']}; "
        f"disclosure cutoff {question['known_at']}. "
    )
    if target is None:
        if question["operation"] == "net_sum":
            return common + 'Return only one JSON object of the form {"sum":INTEGER}.'
        return common + (
            'Return only one JSON object with key "entries": the complete '
            'sorted list of {"id":RECORD_ID,"residual":INTEGER} for all scoped records.'
        )
    if menu is None:
        raise ValueError("policy action menu missing")
    return common + (
        "This is a simulated one-step decision. The current balance is the sum "
        "of residual scoped amounts. Available actions in this order: "
        f"{menu[0]}, {menu[1]}. ADD_500 raises the balance by 500; REMOVE_500 "
        "lowers it by 500. Feedback is minus the absolute difference between "
        f"the next balance and target {target}. Choose the higher-feedback action. "
        'Return only one JSON object of the form {"action":"ACTION_ID"}.'
    )


def _qa_witnesses(
    context: str, task: dict[str, Any]
) -> list[tuple[str, dict[str, Any], list[str]]]:
    original = solve_visible(context, task["question"])
    _, rows, events = _parse(context)
    scoped = set(_scope(rows, task["question"]["filters"]))
    fact_ids = [item["id"] for item in events if item["record_id"] in scoped]
    result = []
    for fact_id in fact_ids:
        reduced, removed = _drop(context, fact_id)
        try:
            changed = solve_visible(reduced, task["question"])
        except ValueError:
            continue
        if changed != original:
            result.append((fact_id, changed, removed))
    return result


def _edit_event(context: str, event_id: str, field: str, value: Any) -> str:
    lines = context.splitlines()
    changed = 0
    for index, line in enumerate(lines[1:], start=1):
        item = json.loads(line)
        if item["id"] == event_id:
            if item["type"] == "record" or field not in item:
                raise ValueError("invalid visible event edit")
            item[field] = value
            lines[index] = dump(item)
            changed += 1
    if changed != 1:
        raise ValueError("event edit is absent or ambiguous")
    return "\n".join(lines)


def _policy_mechanism_witnesses(
    context: str, task: dict[str, Any], target: int
) -> list[dict[str, Any]]:
    """Find visible transition-event edits/deletions that flip the policy."""
    question = task["question"]
    balance = solve_visible(context, question)["sum"]
    choice, _ = policy.decision(balance, target, 500)
    header, rows, events = _parse(context)
    by_id = {row.id: row for row in rows}
    scoped = set(_scope(rows, question["filters"]))
    options = []
    for event in events:
        if event["record_id"] not in scoped:
            continue
        fact_id = event["id"]
        reduced, removed = _drop(context, fact_id)
        options.append((fact_id, "delete", None, None, None, removed, reduced))
        if header["mechanism"] == "partial_reversal":
            maximum = by_id[event["record_id"]].amount - 1
            if event["units"] < maximum:
                options.append(
                    (
                        fact_id,
                        "edit",
                        "units",
                        event["units"],
                        maximum,
                        [],
                        _edit_event(context, fact_id, "units", maximum),
                    )
                )
        elif event["type"] == "grant" and event["role"] == "controller":
            options.append(
                (
                    fact_id,
                    "edit",
                    "role",
                    "controller",
                    "viewer",
                    [],
                    _edit_event(context, fact_id, "role", "viewer"),
                )
            )
        elif event["type"] == "hold":
            for field, cutoff in (
                ("effective", question["asof"]),
                ("reveal", question["known_at"]),
            ):
                if date.fromisoformat(event[field]) > date.fromisoformat(cutoff):
                    options.append(
                        (
                            fact_id,
                            "edit",
                            field,
                            event[field],
                            cutoff,
                            [],
                            _edit_event(context, fact_id, field, cutoff),
                        )
                    )
    flipped = []
    for fact_id, kind, field, before, after, removed, modified in options:
        try:
            changed = solve_visible(modified, question)["sum"]
            alternate, _ = policy.decision(changed, target, 500)
        except ValueError:
            continue
        if changed != balance and alternate != choice:
            flipped.append(
                {
                    "fact_id": fact_id,
                    "kind": kind,
                    "field": field,
                    "before": before,
                    "after": after,
                    "removed_fact_ids": removed,
                    "changed_balance": changed,
                    "changed_action": alternate,
                }
            )
    return flipped


def _reader_item(
    world: dict[str, Any],
    world_path: Path,
    receipt_path: Path,
    task: dict[str, Any],
    tokenizer: Any,
    config: dict[str, Any],
    *,
    sign: int | None = None,
) -> dict[str, Any]:
    context = world["reader_context"]
    is_policy = sign is not None
    if is_policy:
        balance = solve_visible(context, task["question"])["sum"]
        witness_options = []
        margin = 0
        for candidate_margin in config["target_margins"]:
            target = balance + sign * candidate_margin
            witness_options = _state_witnesses(context, task, target, sign)
            if witness_options:
                margin = candidate_margin
                break
        if not witness_options:
            raise ValueError("policy_has_no_visible_action_flipping_fact")
        target = balance + sign * margin
        chosen, outcomes = policy.decision(balance, target, config["action_delta"])
        if chosen != ("ADD_500" if sign > 0 else "REMOVE_500"):
            raise ValueError("policy target/action direction differs")
        menu = policy.ACTIONS if world["seed"] % 4 in {0, 1} else policy.ACTIONS[::-1]
        question = _question(task["question"], target=target, menu=menu)
        answer = {"action": chosen}
        operation = "choose_action_after_executed_state"
        sample_id = (
            f"{world['world_id']}:{task['task_id']}:{'above' if sign > 0 else 'below'}"
        )
    else:
        witness_options = _qa_witnesses(context, task)
        if not witness_options:
            raise ValueError("QA_has_no_visible_answer_changing_fact")
        question = _question(task["question"])
        answer = task["answer"]
        operation = task["question"]["operation"]
        sample_id = f"{world['world_id']}:{task['task_id']}"
    reader = {
        "sample_id": sample_id,
        "messages": [
            {"role": "user", "content": context + MARKER + question},
            {"role": "assistant", "content": dump(answer)},
        ],
    }
    encoded = tokenize_assistant_only(
        tokenizer, reader["messages"], config["max_full_chat_tokens"]
    )
    ids, labels = encoded["input_ids"], encoded["labels"]
    supervised = sum(label != -100 for label in labels)
    input_tokens = len(ids) - supervised
    if not supervised or labels != [-100] * input_tokens + ids[input_tokens:]:
        raise ValueError("final assistant-only loss mask differs")
    positions, query_start = policy._positions(
        context, reader["messages"], tokenizer, ids
    )
    candidates = sorted(
        (
            (query_start - positions[fact_id][1], fact_id, changed, removed)
            for fact_id, changed, removed in witness_options
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    gap, witness_id, changed, removed = candidates[0]
    if gap < config["minimum_witness_to_query_tokens"]:
        raise ValueError("insufficient_selected_visible_witness_gap")
    mechanism_witness = None
    if is_policy:
        alternatives = _policy_mechanism_witnesses(context, task, target)
        if not alternatives:
            raise ValueError("policy_has_no_mechanism_event_action_flip")
        alternatives.sort(
            key=lambda item: (
                query_start - positions[item["fact_id"]][1],
                item["fact_id"],
            ),
            reverse=True,
        )
        mechanism_witness = dict(alternatives[0])
        mechanism_gap = query_start - positions[mechanism_witness["fact_id"]][1]
        if mechanism_gap < config["minimum_witness_to_query_tokens"]:
            raise ValueError("insufficient_mechanism_event_witness_gap")
        mechanism_witness["fact_token_span"] = list(
            positions[mechanism_witness["fact_id"]]
        )
        mechanism_witness["fact_to_query_tokens"] = mechanism_gap
    split = "eval" if world["seed"] % 5 == 0 else "train"
    source_kind = (
        "controlled_agentic_simulation" if is_policy else "controlled_simulation"
    )
    native = {
        "sample_id": sample_id,
        "semantic_task_id": sample_id,
        "source_group": world["world_id"],
        "source_kind": source_kind,
        "domain": "simulated_state",
        "topic": world["mechanism"],
        "operation": operation,
        "split": split,
        "full_chat_tokens": len(ids),
        "input_tokens": input_tokens,
        "supervised_tokens": supervised,
        "context_sha256": world["context_sha256"],
        "dependency_status": (
            "selected_reader_visible_deletion_changes_policy;"
            "mechanism_event_intervention_flips_policy;bounded_event_gap"
            if is_policy
            else "selected_reader_visible_transition_event_deletion_changes_answer;bounded_event_gap"
        ),
        "evidence_status": "executed_visible_state_and_"
        + ("two_action_feedback" if is_policy else "QA_answer"),
    }
    binding = AdapterBinding(
        source_kind=source_kind,
        source_group=world["world_id"],
        domain=native["domain"],
        topic=world["mechanism"],
        operation=operation,
        evidence_profile="controlled_state_transition",
        tokenizer_profile="pinned-chat-template",
        receipt_path=receipt_path,
        receipt_sha256=file_sha(receipt_path),
    )
    candidate = normalize_native_candidate(
        native, reader, binding, context_text=context
    )
    mask = audit_reader(
        reader, candidate.to_dict(), tokenizer, config["max_full_chat_tokens"]
    )
    proof = {
        "sample_id": sample_id,
        "world_id": world["world_id"],
        "source_world_sha256": file_sha(world_path),
        "mechanism": world["mechanism"],
        "task_id": task["task_id"],
        "question": task["question"],
        "answer": answer,
        "decisive_fact_id": witness_id,
        "decisive_fact_kind": "event" if witness_id.startswith("e") else "record",
        "intervention_removed_fact_ids": removed,
        "reader_minus_answer_or_balance": changed,
        "decisive_fact_token_span": list(positions[witness_id]),
        "query_start_token": query_start,
        "decisive_to_query_tokens": gap,
        "bounded_scope": "selected visible support deletion; alternative supports and global minimum proof unsearched",
    }
    if is_policy:
        changed_choice, changed_outcomes = policy.decision(
            changed, target, config["action_delta"]
        )
        if changed_choice == chosen:
            raise ValueError("selected text intervention did not flip policy")
        proof.update(
            {
                "observed_balance": balance,
                "target": target,
                "available_actions": list(menu),
                "chosen_action": chosen,
                "action_outcomes": outcomes,
                "reader_minus_best_action": changed_choice,
                "reader_minus_action_outcomes": changed_outcomes,
                "mechanism_event_intervention": mechanism_witness,
            }
        )
    return {
        "reader": reader,
        "candidate": candidate.to_dict(),
        "mask": mask,
        "proof": proof,
    }


def _jobs(config: dict[str, Any]) -> list[tuple[int, str, int, int, int, int]]:
    jobs = []
    for mechanism in config["mechanisms"]:
        for length in config["length_records"]:
            for _ in range(config["seeds_per_cell"]):
                jobs.append(
                    (
                        config["seed_base"] + len(jobs),
                        mechanism,
                        length,
                        config["consumed_records"],
                        config["depth"],
                        config["scope_variants"],
                    )
                )
    return jobs


def _build_job(job: tuple[int, str, int, int, int, int]) -> dict[str, Any]:
    return build_world(*job)


def _compile_world(
    args: tuple[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    world_path_text, config = args
    world_path = Path(world_path_text)
    receipt_path = world_path.parent / "receipt.json"
    world = json.loads(world_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if receipt["world_id"] != world["world_id"] or receipt["world_sha256"] != file_sha(
        world_path
    ):
        raise ValueError("frozen P133 source world differs")
    if digest(world["reader_context"]) != world["context_sha256"]:
        raise ValueError("source reader context SHA differs")
    tokenizer = policy._worker_tokenizer()
    accepted: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for task in world["tasks"]:
        if solve_visible(world["reader_context"], task["question"]) != task["answer"]:
            raise ValueError("source task does not replay")
        row = {"world_id": world["world_id"], "task_id": task["task_id"]}
        if task["question"]["operation"] == "net_sum":
            paired = []
            paired_ledger = []
            for sign in (1, -1):
                try:
                    item = _reader_item(
                        world,
                        world_path,
                        receipt_path,
                        task,
                        tokenizer,
                        config,
                        sign=sign,
                    )
                except ValueError as exc:
                    paired_ledger.append(
                        {
                            **row,
                            "lane": "policy",
                            "sign": sign,
                            "status": "rejected",
                            "reason": str(exc),
                        }
                    )
                else:
                    paired.append(item)
                    paired_ledger.append(
                        {**row, "lane": "policy", "sign": sign, "status": "accepted"}
                    )
            if len(paired) == 2:
                accepted.extend(paired)
                ledger.extend(paired_ledger)
            else:
                ledger.extend(
                    {
                        **entry,
                        "status": "rejected",
                        "reason": entry.get("reason", "paired_policy_state_incomplete"),
                    }
                    for entry in paired_ledger
                )
        try:
            item = _reader_item(
                world, world_path, receipt_path, task, tokenizer, config
            )
        except ValueError as exc:
            ledger.append(
                {**row, "lane": "qa", "status": "rejected", "reason": str(exc)}
            )
        else:
            accepted.append(item)
            ledger.append({**row, "lane": "qa", "status": "accepted"})
    return accepted, ledger


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if (
        config.get("schema_version") != SCHEMA
        or config.get("mechanisms") != list(MECHANISMS)
        or config.get("length_records") != [400, 800]
        or type(config.get("seeds_per_cell")) is not int
        or not 1 <= config["seeds_per_cell"] <= 32
        or type(config.get("seed_base")) is not int
        or config["seed_base"] < 1000000
        or config.get("consumed_records") != 20
        or config.get("depth") != 2
        or config.get("scope_variants") != 2
        or config.get("action_delta") != 500
        or config.get("target_margins") != [100, 50]
        or config.get("minimum_witness_to_query_tokens", 0) < 16384
        or config.get("max_full_chat_tokens", 0) > 262144
        or not 1 <= config.get("workers", 0) <= 4
    ):
        raise ValueError("invalid P133 batch config")
    return config


def _prior_worlds(
    config: dict[str, Any],
) -> tuple[set[int], set[str], set[str], dict[str, str]]:
    seeds: set[int] = set()
    world_ids: set[str] = set()
    base_hashes: set[str] = set()
    pins = {}
    for pin in config.get("prior_world_sets", []):
        relative = Path(pin["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("prior source pin must be workspace-relative")
        manifest_path = ROOT / relative
        if file_sha(manifest_path) != pin["sha256"]:
            raise ValueError("prior source-world manifest SHA differs")
        pins[pin["path"]] = pin["sha256"]
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema_version") == OUTPUT_SCHEMA:
            paths = sorted(manifest_path.parent.glob("worlds/*/world.json"))
            expected = manifest.get("gross_source_worlds", manifest["source_worlds"])
        elif (
            manifest.get("schema_version")
            == "longworld.p86-state-shared-world.v2.batch-manifest.v1"
        ):
            paths = sorted(manifest_path.parent.glob("shards/*/world.json"))
            expected = manifest["accepted_jobs"]
        else:
            raise ValueError("unsupported prior source-world set")
        if len(paths) != expected:
            raise ValueError("prior source-world count differs")
        for path in paths:
            if manifest["schema_version"] == OUTPUT_SCHEMA:
                expected_sha = manifest["world_files_sha256"][
                    str(path.relative_to(manifest_path.parent))
                ]
            else:
                receipt = json.loads((path.parent / "receipt.json").read_text())
                expected_sha = receipt["world_sha256"]
            if file_sha(path) != expected_sha:
                raise ValueError("prior source-world bytes differ")
            world = json.loads(path.read_text())
            seeds.add(world["seed"])
            world_ids.add(world["world_id"])
            if "base_record_context_sha256" in world:
                base_hashes.add(world["base_record_context_sha256"])
    return seeds, world_ids, base_hashes, pins


def compile_batch(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = _config(config_path)
    compiler_sha = file_sha(Path(__file__))
    if verify_only:
        frozen = json.loads((output / "manifest.json").read_text())
        if frozen.get("compiler_sha256") != compiler_sha:
            raise ValueError("frozen P133 compiler SHA differs")
    prior_seeds, prior_ids, prior_base_hashes, prior_pins = _prior_worlds(config)
    jobs = _jobs(config)
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        built = list(pool.map(_build_job, jobs))
    if len({world["world_id"] for world in built}) != len(jobs):
        raise ValueError("generated source world ID repeats")
    if any(
        world["seed"] in prior_seeds
        or world["world_id"] in prior_ids
        or world["base_record_context_sha256"] in prior_base_hashes
        for world in built
    ):
        raise ValueError("new P133 source world overlaps a pinned prior source")
    with tempfile.TemporaryDirectory(prefix="p133-state-", dir=output.parent) as raw:
        temp = Path(raw)
        world_paths = []
        for world in built:
            shard = temp / "worlds" / world["world_id"]
            shard.mkdir(parents=True)
            world_path = shard / "world.json"
            world_path.write_text(dump(world) + "\n")
            receipt = {
                "world_id": world["world_id"],
                "world_sha256": file_sha(world_path),
                "base_record_world_id": world["base_record_world_id"],
                "base_record_context_sha256": world["base_record_context_sha256"],
            }
            (shard / "receipt.json").write_text(dump(receipt) + "\n")
            world_paths.append(world_path)
        with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
            batches = list(
                pool.map(_compile_world, ((str(path), config) for path in world_paths))
            )
        admitted_world_ids = {
            world["world_id"]
            for world, (items, ledger) in zip(built, batches)
            if len(items) == 8
            and len(ledger) == 8
            and all(row["status"] == "accepted" for row in ledger)
        }
        rejected_worlds = [
            {
                "world_id": world["world_id"],
                "seed": world["seed"],
                "mechanism": world["mechanism"],
                "length_records": world["length_records"],
                "causal_reasons": sorted(
                    {row["reason"] for row in ledger if row["status"] == "rejected"}
                ),
            }
            for world, (_, ledger) in zip(built, batches)
            if world["world_id"] not in admitted_world_ids
        ]
        accepted = sorted(
            (
                item
                for world, (items, _) in zip(built, batches)
                if world["world_id"] in admitted_world_ids
                for item in items
            ),
            key=lambda item: item["candidate"]["sample_id"],
        )
        attempts = sorted(
            (
                row
                if world["world_id"] in admitted_world_ids
                or row["status"] == "rejected"
                else {
                    **row,
                    "status": "rejected",
                    "reason": "world_incomplete_after_event_gate",
                }
                for world, (_, rows) in zip(built, batches)
                for row in rows
            ),
            key=lambda row: (
                row["world_id"],
                row["task_id"],
                row["lane"],
                row.get("sign", 0),
            ),
        )
        streams = {
            name: []
            for name in (
                "qa_train.jsonl",
                "qa_eval.jsonl",
                "policy_train.jsonl",
                "policy_eval.jsonl",
                "sample_index.jsonl",
                "proofs.jsonl",
                "mask_audit.jsonl",
                "attempt_ledger.jsonl",
            )
        }
        candidate_ledger = CandidateLedger()
        positions = Counter()
        length_bins = Counter()
        mechanisms = Counter()
        actions = Counter()
        group_splits: dict[str, set[str]] = defaultdict(set)
        world_lanes: dict[str, Counter] = defaultdict(Counter)
        for item in accepted:
            row, proof = item["candidate"], item["proof"]
            candidate_ledger.add(NativeCandidate(**row))
            lane = (
                "policy"
                if row["source_kind"] == "controlled_agentic_simulation"
                else "qa"
            )
            split = row["split"]
            file_name = f"{lane}_{split}.jsonl"
            streams[file_name].append(dump(item["reader"]) + "\n")
            streams["sample_index.jsonl"].append(
                dump(
                    {
                        **row,
                        "source_name": "p133_executed_state_" + lane,
                        "native_row_ref": proof["world_id"],
                        "output_file": file_name,
                        "row_index": positions[file_name],
                    }
                )
                + "\n"
            )
            streams["proofs.jsonl"].append(dump(proof) + "\n")
            streams["mask_audit.jsonl"].append(dump(item["mask"]) + "\n")
            positions[file_name] += 1
            length_bins[row["length_bin"]] += 1
            mechanisms[proof["mechanism"]] += 1
            group_splits[row["source_group"]].add(split)
            world_lanes[row["source_group"]][row["operation"]] += 1
            if lane == "policy":
                actions[proof["chosen_action"]] += 1
        streams["attempt_ledger.jsonl"] = [dump(row) + "\n" for row in attempts]
        if any(len(splits) != 1 for splits in group_splits.values()):
            raise ValueError("P133 source world crosses train/eval")
        if len(world_lanes) != len(admitted_world_ids) or any(
            operations
            != {
                "net_sum": 2,
                "residual_entries": 2,
                "choose_action_after_executed_state": 4,
            }
            for operations in world_lanes.values()
        ):
            raise ValueError("P133 world lacks multiple executed operations")
        if actions["ADD_500"] != actions["REMOVE_500"]:
            raise ValueError("P133 policy labels are unbalanced")
        manifest = {
            "schema_version": OUTPUT_SCHEMA,
            "compiler_sha256": compiler_sha,
            "config_sha256": file_sha(config_path),
            "training_contracts": [
                "controlled_state_reader_QA_candidate",
                "isolated_one_step_policy_candidate",
            ],
            "gross_source_worlds": len(built),
            "source_worlds": len(admitted_world_ids),
            "rejected_source_worlds": rejected_worlds,
            "prior_source_manifest_pins": prior_pins,
            "prior_source_world_overlap": 0,
            "source_world_ids": sorted(admitted_world_ids),
            "gross_source_world_ids": sorted(world["world_id"] for world in built),
            "new_transition_mechanisms": list(MECHANISMS),
            "worlds_by_mechanism": dict(
                sorted(
                    Counter(
                        world["mechanism"]
                        for world in built
                        if world["world_id"] in admitted_world_ids
                    ).items()
                )
            ),
            "gross_worlds_by_mechanism": dict(
                sorted(Counter(world["mechanism"] for world in built).items())
            ),
            "gross_task_attempts": len(attempts),
            "accepted_task_views": len(accepted),
            "rejection_reasons": dict(
                sorted(
                    Counter(
                        row["reason"] for row in attempts if row["status"] == "rejected"
                    ).items()
                )
            ),
            "qa_views": positions["qa_train.jsonl"] + positions["qa_eval.jsonl"],
            "policy_views": positions["policy_train.jsonl"]
            + positions["policy_eval.jsonl"],
            "qa_transition_event_witnesses": sum(
                item["candidate"]["source_kind"] == "controlled_simulation"
                and item["proof"]["decisive_fact_kind"] == "event"
                for item in accepted
            ),
            "policy_mechanism_event_action_flips": sum(
                item["candidate"]["source_kind"] == "controlled_agentic_simulation"
                and item["proof"]["mechanism_event_intervention"] is not None
                for item in accepted
            ),
            "independent_semantic_tasks": candidate_ledger.independent_semantic_tasks,
            "split_views": dict(sorted(positions.items())),
            "source_world_split_overlap": 0,
            "actions": dict(sorted(actions.items())),
            "physical_length_bins": dict(sorted(length_bins.items())),
            "views_by_mechanism": dict(sorted(mechanisms.items())),
            "full_chat_tokens": sum(
                item["candidate"]["full_chat_tokens"] for item in accepted
            ),
            "supervised_tokens": sum(
                item["candidate"]["supervised_tokens"] for item in accepted
            ),
            "minimum_selected_witness_to_query_tokens": min(
                item["proof"]["decisive_to_query_tokens"] for item in accepted
            ),
            "maximum_selected_witness_to_query_tokens": max(
                item["proof"]["decisive_to_query_tokens"] for item in accepted
            ),
            "files_sha256": {
                name: digest("".join(lines)) for name, lines in streams.items()
            },
            "world_files_sha256": {
                str(path.relative_to(temp)): file_sha(path)
                for world_path in world_paths
                for path in (world_path, world_path.parent / "receipt.json")
            },
            "claim_limit": "two new synthetic state-transition mechanisms over one record schema; bounded selected visible support deletion, no shortest proof, multi-turn trajectory, real-domain or model-gain claim",
            "train_ready": False,
        }
        streams["manifest.json"] = [dump(manifest) + "\n"]
        for name, lines in streams.items():
            (temp / name).write_text("".join(lines))
        if verify_only:
            for path in temp.rglob("*"):
                if (
                    path.is_file()
                    and (output / path.relative_to(temp)).read_bytes()
                    != path.read_bytes()
                ):
                    raise ValueError(
                        f"frozen P133 artifact differs: {path.relative_to(temp)}"
                    )
            if {
                str(path.relative_to(temp))
                for path in temp.rglob("*")
                if path.is_file()
            } != {
                str(path.relative_to(output))
                for path in output.rglob("*")
                if path.is_file()
            }:
                raise ValueError("frozen P133 artifact file inventory differs")
        else:
            if output.exists():
                raise ValueError("P133 output already exists")
            os.rename(temp, output)
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(dump(compile_batch(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
