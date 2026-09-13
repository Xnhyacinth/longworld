"""A bounded, fully simulated ledger world; three tasks share its observations.

Public API: generate_bundle returns JSON-serializable candidates; solve_visible
uses only its two visible arguments; validate_bundle checks gold and paired CF.
This v1 grammar is one family across domain labels, not natural-language mastery.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from datetime import date, timedelta
from typing import Any

from longworld.core.world import Event, WorldSimulator

VERSION = "capability-ledger-v1"
RULES = (
    "Start each entity balance at zero. bind declares a unique alias. "
    "record adds amount only when approved=true; all records retain memo. "
    "spend applies only if its required events applied and balance >= max(cost, minimum_balance); "
    "then subtract cost. Process events in listed chronological order. "
    "Recall counts all records of the aliased entity, one-based. "
    "Aggregation sums approved record amounts, excluding spends. "
    "State returns the final balance. No events are omitted."
)


def _simulator(world_id, seed):
    def check(state, event):
        if event.type == "spend":
            return state.values.get("balance:" + event.params["entity"], 0) >= max(
                event.params["cost"], event.params.get("minimum_balance", 0)
            ), "insufficient_balance"
        return True, None

    def apply(state, event):
        p = event.params
        if event.type == "bind":
            state.set("alias:" + p["alias"], p["entity"], event.id, event.time)
        elif event.type == "record":
            count_key = "record_count:" + p["entity"]
            count = state.values.get(count_key, 0) + 1
            state.set(count_key, count, event.id, event.time)
            state.set(f"memo:{p['entity']}:{count}", p["memo"], event.id, event.time)
            if p["approved"]:
                key = "balance:" + p["entity"]
                state.set(
                    key, state.values.get(key, 0) + p["amount"], event.id, event.time
                )
                key = "sum:" + p["entity"]
                state.set(
                    key, state.values.get(key, 0) + p["amount"], event.id, event.time
                )
        else:
            key = "balance:" + p["entity"]
            state.set(key, state.values.get(key, 0) - p["cost"], event.id, event.time)

    return WorldSimulator(
        {"world_id": world_id, "seed": seed, "schema_version": VERSION},
        {},
        check,
        apply,
    )


def _render(events):
    rows = [{"schema": VERSION, "rules": RULES, "event_count": len(events)}]
    rows.extend(
        {
            "id": e.id,
            "time": e.time.isoformat(),
            "type": e.type,
            "params": e.params,
            "required_inputs": e.required_inputs,
        }
        for e in events
    )
    return "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows)


def solve_visible(context: str, question: dict[str, Any]) -> Any:
    """Interpret visible records independently; never read oracle state or gold."""
    try:
        rows = [json.loads(line) for line in context.splitlines()]
        if (
            not rows
            or rows[0].get("schema") != VERSION
            or rows[0].get("rules") != RULES
        ):
            raise ValueError("unsupported or missing visible contract")
        if rows[0]["event_count"] != len(rows) - 1:
            raise ValueError("visible event count mismatch")
        aliases, balances, totals, memos = {}, {}, {}, {}
        seen, applied = set(), set()
        previous = None
        for row in rows[1:]:
            identity = row["id"]
            order = (date.fromisoformat(row["time"]), identity)
            if identity in seen or (previous is not None and order <= previous):
                raise ValueError("duplicate or unordered event")
            previous = order
            parents = row["required_inputs"]
            if any(parent not in seen for parent in parents):
                raise ValueError("unknown event dependency")
            seen.add(identity)
            p, kind = row["params"], row["type"]
            entity = p["entity"]
            if kind != "spend" and parents:
                raise ValueError("only spend events may have dependencies")
            if kind == "bind":
                if p["alias"] in aliases:
                    raise ValueError("duplicate alias")
                aliases[p["alias"]] = entity
            elif kind == "record":
                if type(p["amount"]) is not int or type(p["approved"]) is not bool:
                    raise ValueError("invalid record amount or approval")
                memos.setdefault(entity, []).append(p["memo"])
                if p["approved"]:
                    totals[entity] = totals.get(entity, 0) + p["amount"]
                    balances[entity] = balances.get(entity, 0) + p["amount"]
            elif kind == "spend":
                if type(p["cost"]) is not int or p["cost"] < 0:
                    raise ValueError("invalid spend cost")
                if not set(parents).issubset(applied) or balances.get(entity, 0) < max(
                    p["cost"], p.get("minimum_balance", 0)
                ):
                    continue
                balances[entity] = balances.get(entity, 0) - p["cost"]
            else:
                raise ValueError("unknown event type")
            applied.add(identity)
        entity = aliases[question["alias"]]
        if question["operation"] == "recall":
            ordinal = question["ordinal"]
            if type(ordinal) is not int or ordinal < 1:
                raise ValueError("invalid ordinal")
            return memos[entity][ordinal - 1]
        if question["operation"] == "aggregate":
            return totals.get(entity, 0)
        if question["operation"] == "state":
            return balances.get(entity, 0)
        raise ValueError("unknown query operation")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("incomplete or malformed visible evidence/query") from exc


def _tasks(state, alias, ordinal):
    entity = state.values["alias:" + alias]
    answers = [
        state.values[f"memo:{entity}:{ordinal}"],
        state.values["sum:" + entity],
        state.values["balance:" + entity],
    ]
    tasks = []
    for capability, operation, answer in zip(
        ["recall_binding", "dense_aggregation", "state_transitions"],
        ["recall", "aggregate", "state"],
        answers,
    ):
        question = {"operation": operation, "alias": alias}
        if operation == "recall":
            question["ordinal"] = ordinal
        tasks.append(
            {
                "task_id": capability,
                "capability": capability,
                "question": question,
                "prompt": "Follow the visible ledger rules and answer this query as JSON: "
                + json.dumps(question, sort_keys=True),
                "answer": answer,
            }
        )
    return tasks


def generate_bundle(
    seed: int, domain: str = "project", n_records: int = 24
) -> dict[str, Any]:
    """Generate one reproducible world and an answer-changing replayed twin.

    Domain is a presentation label only. n_records increases causal workload;
    no length or strict-dependency certification is asserted here.
    """
    if type(n_records) is not int or n_records < 6:
        raise ValueError("n_records must be an integer >= 6")
    rng = random.Random(seed)
    world_id = f"{VERSION}-{hashlib.sha256(f'{seed}:{domain}:{n_records}'.encode()).hexdigest()[:20]}"
    entities = [f"unit-{rng.getrandbits(48):012x}" for _ in range(3)]
    aliases = [f"handle-{rng.getrandbits(48):012x}" for _ in range(3)]
    events = []

    def add(kind, params, parents=()):
        index = len(events)
        events.append(
            Event(
                id=f"e{index:08d}",
                type=kind,
                time=date(2000, 1, 1) + timedelta(days=index),
                params=params,
                visibility=["reader"],
                required_inputs=list(parents),
                causal_inputs=list(parents),
            )
        )

    for entity, alias in zip(entities, aliases):
        add("bind", {"entity": entity, "alias": alias})
    target_entity_index = rng.randrange(3)
    qualifying_targets = []
    for index in range(n_records):
        entity = entities[index % 3]
        approved = index % 5 != 4
        amount = rng.randint(10, 50)
        add(
            "record",
            {
                "entity": entity,
                "amount": amount,
                "approved": approved,
                "memo": f"authorization-{rng.getrandbits(96):024x}",
            },
        )
        if entity == entities[target_entity_index] and approved:
            qualifying_targets.append((len(events) - 1, index // 3 + 1))
        if index % 6 == 5:
            for spend_entity in entities:
                add("spend", {"entity": spend_entity, "cost": 50, "minimum_balance": 0})
    later_targets = qualifying_targets[len(qualifying_targets) // 2 :]
    target_index, ordinal = rng.choice(later_targets)
    target_id = events[target_index].id
    simulator = _simulator(world_id, seed)
    baseline = simulator.run(copy.deepcopy(events))
    candidates = [item for item in qualifying_targets if item[0] != target_index]
    rng.shuffle(candidates)
    found = False
    for state_index, _ in candidates[:24]:
        state_event = events[state_index]
        for amount, approved in [
            (10, True),
            (50, True),
            (state_event.params["amount"], False),
        ]:
            if (amount, approved) == (
                state_event.params["amount"],
                state_event.params["approved"],
            ):
                continue
            overrides = {
                state_event.id: {"amount": amount, "approved": approved},
                target_id: {"memo": f"authorization-{rng.getrandbits(96):024x}"},
            }
            changed_base = copy.deepcopy(events)
            for event in changed_base:
                event.params.update(overrides.get(event.id, {}))
            alternate = simulator.run(changed_base)
            toggled = [
                left.id
                for left, right in zip(baseline.events, alternate.events)
                if left.type == "spend" and left.skipped != right.skipped
            ]
            if not toggled:
                continue
            add(
                "spend",
                {
                    "entity": entities[target_entity_index],
                    "cost": 1,
                    "minimum_balance": 0,
                },
                [toggled[-1]],
            )
            world = simulator.run(copy.deepcopy(events))
            changed_events = copy.deepcopy(events)
            for event in changed_events:
                event.params.update(overrides.get(event.id, {}))
            changed_world = simulator.run(changed_events)
            balance_key = "balance:" + entities[target_entity_index]
            found = world.events[-1].skipped != changed_world.events[
                -1
            ].skipped and world.state.values.get(
                balance_key, 0
            ) != changed_world.state.values.get(balance_key, 0)
            if found:
                break
            events.pop()
        if found:
            break
    if not found:
        raise ValueError(
            "no bounded answer-changing intervention found; reject candidate"
        )
    replayed = simulator.replay_events(events, param_overrides=overrides)
    if replayed.values != changed_world.state.values:
        raise AssertionError("intervention replay disagrees with fresh simulation")
    return {
        "schema_version": VERSION,
        "world_id": world_id,
        "seed": seed,
        "domain": domain,
        "n_records": n_records,
        "context": _render(events),
        "tasks": _tasks(world.state, aliases[target_entity_index], ordinal),
        "counterfactual": {
            "context": _render(changed_events),
            "tasks": _tasks(replayed, aliases[target_entity_index], ordinal),
            "intervention": {"param_overrides": overrides},
            "checks": {
                "descendant_changed": world.events[-1].skipped
                != changed_world.events[-1].skipped
            },
        },
        "lineage": {
            "source_type": "fully_simulated",
            "topology_family": VERSION,
            "domain_is_presentation_label": True,
        },
        "admission": {
            "candidate": True,
            "strict_long_dependency": False,
            "framework_ready": False,
            "release_ready": False,
        },
    }


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Validate visible answers, all paired changes, and deterministic lineage."""
    errors = []
    checks = {}
    try:
        expected = generate_bundle(
            bundle["seed"], bundle["domain"], bundle["n_records"]
        )
        checks["deterministic_reproduction"] = bundle == expected
        for name, view in [
            ("original", bundle),
            ("counterfactual", bundle["counterfactual"]),
        ]:
            for task in view["tasks"]:
                checks[f"{name}:{task['capability']}"] = (
                    solve_visible(view["context"], task["question"]) == task["answer"]
                )
        checks["all_answers_changed"] = all(
            a["answer"] != b["answer"]
            for a, b in zip(bundle["tasks"], bundle["counterfactual"]["tasks"])
        )
        checks["descendant_recomputed"] = bundle["counterfactual"]["checks"][
            "descendant_changed"
        ]
        errors.extend(name for name, passed in checks.items() if not passed)
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return {"passed": not errors, "errors": errors, "checks": checks}
