"""Versioned multi-query worlds. Symbolic validity is not measured model utility.

Questions are isolated answer branches. Topic metadata does not change physics.
The compact control is an oracle entity filter, not a learned retrieval result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from datetime import date, timedelta
from typing import Any

from longworld.core.world import Event, WorldSimulator

VERSION = "capability-curriculum-v2"
RULES = {
    "ledger": "Start balance and approved total at zero. credit adds amount to both when approved. debit subtracts amount only if sufficient balance and required events applied. Active is the sorted set of applied debit IDs. Recall counts credits including unapproved ones.",
    "reservation": "Start capacity and supplied total at zero. supply adds amount to both. reserve succeeds only if free capacity suffices and required events applied; subtract amount and store reservation ID. release returns the amount of its referenced active reservation and removes it, otherwise does nothing. Active is the sorted set of active reservation IDs. Recall counts supply events.",
}


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def topic_digest(topic: dict | None) -> str:
    """Bind the topic metadata the runner prepends to the user message.

    The topic is the one model-visible surface that is not derived from the
    simulated events, and the runner renders it into the prompt verbatim, so
    it needs the same generation-time binding the visible prompt/protocol has
    in capability_rules_workflow. The digest covers the canonical serialization
    of the whole topic dict, so any tampering (title, domain, field, id)
    changes it. It belongs in lineage: the parallel test requires world_id and
    context to be identical across different topics.
    """
    return hashlib.sha256(_dump(topic).encode()).hexdigest()


def _render(events, family):
    header = {
        "schema": VERSION,
        "family": family,
        "rules": RULES[family],
        "event_count": len(events),
    }
    return "\n".join(
        [_dump(header)]
        + [
            _dump(
                {
                    "id": e.id,
                    "time": e.time.isoformat(),
                    "type": e.type,
                    "params": e.params,
                    "required_inputs": e.required_inputs,
                }
            )
            for e in events
        ]
    )


def _oracle(events, family, seed):
    # Truth comes from simulator transitions, independently of visible parsing.
    def check(state, event):
        p = event.params
        if event.type in ("debit", "reserve"):
            return state.values.get(p["entity"] + ":balance", 0) >= p[
                "amount"
            ], "insufficient"
        return True, None

    def apply(state, event):
        p, kind = event.params, event.type
        entity = p["entity"]
        key = entity + ":balance"
        balance = state.values.get(key, 0)
        if kind == "bind":
            state.set("alias:" + p["alias"], entity, event.id, event.time)
            return
        if kind in ("credit", "supply"):
            if p.get("approved", True):
                state.set(key, balance + p["amount"], event.id, event.time)
                total_key = entity + ":total"
                state.set(
                    total_key,
                    state.values.get(total_key, 0) + p["amount"],
                    event.id,
                    event.time,
                )
        elif kind in ("debit", "reserve"):
            state.set(key, balance - p["amount"], event.id, event.time)
            state.set("active:" + event.id, (entity, p["amount"]), event.id, event.time)
        elif kind == "release":
            active_key = "active:" + p["reservation"]
            active = state.values.get(active_key)
            if active:
                state.set(key, balance + active[1], event.id, event.time)
                state.set(active_key, None, event.id, event.time)

    simulator = WorldSimulator({"world_id": VERSION, "seed": seed}, {}, check, apply)
    world = simulator.run(copy.deepcopy(events))
    # Build compact per-entity history from applied events once, no replay per QA.
    history = {e.params["entity"]: [] for e in events if e.type == "bind"}
    for event in world.events:
        if event.type != "bind":
            history[event.params["entity"]].append(event)
    return world, history


def _oracle_answer(world, history, question, events):
    entity = world.state.values["alias:" + question["alias"]]
    eligible = [e for e in history[entity] if e.id <= question["asof"]]
    op = question["operation"]
    if op == "recall":
        return [e.params["memo"] for e in eligible if e.type in ("credit", "supply")][
            question["ordinal"] - 1
        ]
    if op == "aggregate":
        return sum(
            e.params["amount"]
            for e in eligible
            if e.type in ("credit", "supply") and e.params.get("approved", True)
        )
    if op == "active":
        active = set()
        for e in eligible:
            if not e.skipped and e.type in ("debit", "reserve"):
                active.add(e.id)
            elif not e.skipped and e.type == "release":
                active.discard(e.params["reservation"])
        return sorted(active)
    # WorldState history records the simulator's actual as-of state changes.
    key = entity + ":balance"
    result = 0
    cutoff_time = next(e.time for e in events if e.id == question["asof"])
    for change in world.state.history:
        if change.key == key and change.time <= cutoff_time:
            result = change.new
    return result


def solve_visible(context: str, question: dict[str, Any]) -> Any:
    """Independent interpreter over visible input only; missing contract rejects."""
    try:
        rows = [json.loads(line) for line in context.splitlines()]
        header = rows[0]
        family = header["family"]
        if (
            header["schema"] != VERSION
            or header["rules"] != RULES[family]
            or header["event_count"] != len(rows) - 1
        ):
            raise ValueError("invalid visible contract or missing evidence")
        aliases, balances, totals, memos, active = {}, {}, {}, {}, {}
        seen, applied = set(), set()
        previous = None
        cutoff = question["asof"]
        valid_cutoff = False
        for row in rows[1:]:
            identity, kind, p = row["id"], row["type"], row["params"]
            order = (date.fromisoformat(row["time"]), identity)
            if identity in seen or (previous is not None and order <= previous):
                raise ValueError("duplicate or unordered event")
            previous = order
            if not set(row["required_inputs"]).issubset(seen):
                raise ValueError("unknown dependency")
            seen.add(identity)
            valid_cutoff |= identity == cutoff
            entity = p["entity"]
            if kind not in (
                {"bind", "credit", "debit"}
                if family == "ledger"
                else {"bind", "supply", "reserve", "release"}
            ):
                raise ValueError("invalid family event")
            if kind in ("credit", "supply", "debit", "reserve") and (
                type(p["amount"]) is not int or p["amount"] < 0
            ):
                raise ValueError("invalid amount")
            if kind not in ("debit", "reserve") and row["required_inputs"]:
                raise ValueError("only debit/reserve events may require inputs")
            if kind == "credit" and type(p["approved"]) is not bool:
                raise ValueError("invalid approval")
            if identity > cutoff:
                continue
            if kind == "bind":
                if p["alias"] in aliases:
                    raise ValueError("duplicate alias")
                aliases[p["alias"]] = entity
                balances[entity], totals[entity], memos[entity], active[entity] = (
                    0,
                    0,
                    [],
                    {},
                )
            elif kind in ("credit", "supply"):
                memos[entity].append(p["memo"])
                if p.get("approved", True):
                    balances[entity] += p["amount"]
                    totals[entity] += p["amount"]
            elif kind in ("debit", "reserve"):
                if (
                    not set(row["required_inputs"]).issubset(applied)
                    or balances[entity] < p["amount"]
                ):
                    continue
                balances[entity] -= p["amount"]
                active[entity][identity] = p["amount"]
            elif kind == "release":
                balances[entity] += active[entity].pop(p["reservation"], 0)
            applied.add(identity)
        if not valid_cutoff:
            raise ValueError("missing cutoff event")
        entity = aliases[question["alias"]]
        op = question["operation"]
        if op == "recall":
            ordinal = question["ordinal"]
            if type(ordinal) is not int or ordinal < 1:
                raise ValueError("invalid ordinal")
            return memos[entity][ordinal - 1]
        if op == "aggregate":
            return totals[entity]
        if op == "state":
            return balances[entity]
        if op == "active":
            return sorted(active[entity])
        raise ValueError("unknown operation")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("incomplete or malformed visible evidence/query") from exc


def generate_bundle(
    seed: int,
    family: str = "ledger",
    n_records: int = 120,
    n_questions: int = 16,
    topic: dict | None = None,
) -> dict[str, Any]:
    if family not in RULES:
        raise ValueError("unsupported family")
    if type(n_records) is not int or n_records < 16:
        raise ValueError("n_records must be >= 16")
    if type(n_questions) is not int or not 1 <= n_questions <= 16:
        raise ValueError("n_questions must be 1..16")
    rng = random.Random(seed)
    entities = [f"unit-{rng.getrandbits(48):012x}" for _ in range(4)]
    aliases = [f"alias-{rng.getrandbits(48):012x}" for _ in range(4)]
    events = []

    def add(kind, entity, parents=(), **params):
        index = len(events)
        events.append(
            Event(
                f"e{index:08d}",
                kind,
                date(2000, 1, 1) + timedelta(days=index),
                {"entity": entity, **params},
                ["reader"],
                required_inputs=list(parents),
            )
        )
        return events[-1].id

    for entity, alias in zip(entities, aliases):
        add("bind", entity, alias=alias)
    source_ids, state_cutoffs, first_attempts = [], [], []
    for entity in entities:
        initial_amount = rng.randint(20, 30)
        source_ids.append(
            add(
                "credit" if family == "ledger" else "supply",
                entity,
                amount=initial_amount,
                memo=f"note-{rng.getrandbits(96):024x}",
                **({"approved": True} if family == "ledger" else {}),
            )
        )
        first = add(
            "debit" if family == "ledger" else "reserve",
            entity,
            amount=initial_amount + 5,
        )
        first_attempts.append(first)
        add(
            "credit" if family == "ledger" else "supply",
            entity,
            amount=rng.randint(10, 40),
            memo=f"note-{rng.getrandbits(96):024x}",
            **({"approved": True} if family == "ledger" else {}),
        )
    ordinary_reservations = {entity: [] for entity in entities}
    # T3 fix: track the conservative running balance per entity so debit
    # amounts are drawn against it. Without this, credits (mean ~25, approval
    # ~0.81) outweigh debits (mean ~6.4) 6.6:1 in total, balances grow +11.4
    # per event, and from the mid third of the context 99.7% of debits pass
    # the sufficiency conditional trivially (measured over 30 seeds) -- the
    # only conditional mechanism in the ledger family goes inert.
    # The tracked balance is an upper bound (skipped debits are not
    # subtracted), so drawn amounts stay conservative.
    balances = {entity: 0 for entity in entities}
    freeable: list[int] = []
    for event in events:
        if event.params.get("approved", True) and event.type in (
            "credit",
            "supply",
        ):
            balances[event.params["entity"]] += event.params["amount"]

    def add_conditional(kind, entity, **params):
        """Add a debit/reserve whose amount is drawn against the entity's
        tracked balance. ~1/3 of the time it exceeds the balance by 1-3 so
        failures exist throughout the context (real withdrawal requests do
        get declined) and so the counterfactual -- raising an earlier credit
        by 5-20 -- can genuinely free it, flipping downstream application
        state as the descendant_recomputed check requires."""
        balance = max(0, balances[entity])
        amount = rng.randint(1, max(2, balance))
        if rng.random() < 0.34:
            amount = balance + rng.randint(1, 3)
            freeable.append(len(events))
        else:
            balances[entity] = balance - amount
        return add(kind, entity, amount=amount, **params)

    while len(events) < n_records:
        entity = rng.choice(entities)
        if family == "reservation" and rng.random() < 0.35:
            reservations = ordinary_reservations[entity]
            if reservations and rng.random() < 0.5:
                add("release", entity, reservation=rng.choice(reservations))
            else:
                ordinary_reservations[entity].append(
                    add_conditional("reserve", entity)
                )
        elif family == "ledger" and rng.random() < 0.35:
            add_conditional("debit", entity)
        else:
            add(
                "credit" if family == "ledger" else "supply",
                entity,
                amount=rng.randint(10, 40),
                memo=f"note-{rng.getrandbits(96):024x}",
                **({"approved": rng.random() > 0.2} if family == "ledger" else {}),
            )
            balances[entity] += events[-1].params["amount"]
    for entity, first in zip(entities, first_attempts):
        state_cutoffs.append(
            add(
                "debit" if family == "ledger" else "reserve",
                entity,
                parents=[first],
                amount=1,
            )
        )
    # T1 fix: the counterfactual used to modify event index 4 (the first
    # credit of entities[0]) in every world -- intervention position 0.0323
    # with zero variance over 20 seeds. Draw the target from credit/supply
    # events, stratified into early/mid/late thirds, and verify by oracle
    # simulation that the bump actually flips a downstream conditional
    # (descendant_recomputed requires it). A candidate qualifies only if
    # raising it by the drawn delta toggles some event's application state;
    # freeable gaps grow as later over-balance conditionals stack, so the
    # filter must simulate rather than reason from the generation-time gap.
    world, history = _oracle(events, family, seed)

    def try_intervention(index: int, delta: int) -> bool:
        probe = copy.deepcopy(events)
        probe[index].params["amount"] += delta
        probe_world, _ = _oracle(probe, family, seed)
        return any(
            a.skipped != b.skipped
            for a, b in zip(world.events, probe_world.events)
        )

    candidates = [i for i, e in enumerate(events) if e.type in ("credit", "supply")]
    third = max(1, len(candidates) // 3)
    strata = [candidates[:third], candidates[third : 2 * third], candidates[2 * third:]]
    strata = [s for s in strata if s]
    rng.shuffle(strata)
    target_index, delta = 4, 10  # historical fallback
    for stratum in strata:
        for candidate in stratum:
            attempt_delta = rng.randint(5, 20)
            if try_intervention(candidate, attempt_delta):
                target_index, delta = candidate, attempt_delta
                break
        else:
            continue
        break
    alternate = copy.deepcopy(events)
    alternate[target_index].params["amount"] += delta
    cf_world, cf_history = _oracle(alternate, family, seed)

    # T2 fix: recall ordinals used to be 1 + (supply_count - 1) * i // 3, an
    # exact function of the entity index (entity 0 always ordinal 1 -- "take
    # the first memo" answered 25% of recall tasks). Draw them per entity
    # BEFORE tasks_for runs, so the factual and counterfactual views share
    # the same ordinals and stay a paired sample.
    recall_ordinals = {}
    for i, entity in enumerate(entities):
        supply_count = sum(
            1
            for e in events
            if e.params.get("entity") == entity
            and e.type in ("credit", "supply")
        )
        recall_ordinals[i] = rng.randint(1, max(1, supply_count))

    def tasks_for(view, trace, visible_events):
        tasks = []
        for op, capability in [
            ("state", "state_transitions"),
            ("aggregate", "dense_aggregation"),
            ("recall", "recall_binding"),
            ("active", "state_set_integration"),
        ]:
            for i, alias in enumerate(aliases):
                entity_events = [
                    e for e in visible_events if e.params["entity"] == entities[i]
                ]
                cutoff = state_cutoffs[i] if op == "state" else entity_events[-1].id
                question = {"operation": op, "alias": alias, "asof": cutoff}
                if op == "recall":
                    question["ordinal"] = recall_ordinals[i]
                compact = [e for e in entity_events if e.id <= cutoff]
                task = {
                    "task_id": f"{op}-{i}",
                    "capability": capability,
                    "question": question,
                    "prompt": "Use the visible rules. At the specified inclusive event cutoff, answer this independent query as JSON: "
                    + _dump(question),
                    "answer": _oracle_answer(view, trace, question, visible_events),
                    "controls": {
                        "oracle_compact_context": _render(compact, family),
                        "compact_selection": "oracle entity filter; not learned retrieval",
                        "question_only": "missing visible contract is insufficient",
                    },
                }
                tasks.append(task)
        # T4 fix: plain prefix truncation turned any n_questions < 16 into a
        # single-capability dataset (n=4 -> 4 state_transitions, measured).
        # Round-robin across capability families instead, so every family
        # stays within max-min <= 1 of balanced at any n.
        buckets = {
            capability: []
            for capability in (
                "state_transitions",
                "dense_aggregation",
                "recall_binding",
                "state_set_integration",
            )
        }
        for task in tasks:
            buckets[task["capability"]].append(task)
        selected: list[dict] = []
        order = sorted(buckets)
        offset = rng.randrange(len(order))
        while len(selected) < n_questions:
            for name in order[offset:] + order[:offset]:
                if buckets[name] and len(selected) < n_questions:
                    selected.append(buckets[name].pop(0))
        # Sorting after selection keeps the factual and counterfactual task
        # lists in identical order: the rng draw above is consumed once per
        # tasks_for call, so an unsorted return would interleave families
        # differently between the two views and break the paired-sample zip.
        return sorted(selected, key=lambda t: t["task_id"])

    tasks = tasks_for(world, history, events)
    cf_tasks = tasks_for(cf_world, cf_history, alternate)
    changed = sum(a["answer"] != b["answer"] for a, b in zip(tasks, cf_tasks))
    toggled = [
        a.id for a, b in zip(world.events, cf_world.events) if a.skipped != b.skipped
    ]
    identity = _dump([VERSION, seed, family, n_records])
    return {
        "schema_version": VERSION,
        "world_id": VERSION + "-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
        "seed": seed,
        "family": family,
        "topic": topic,
        "n_records": n_records,
        "n_questions": n_questions,
        "context": _render(events, family),
        "tasks": tasks,
        "counterfactual": {
            "context": _render(alternate, family),
            "tasks": cf_tasks,
            "intervention": {
                "param_overrides": {
                    events[target_index].id: {
                        "amount": alternate[target_index].params["amount"]
                    }
                }
            },
            "checks": {
                "changed_answer_count": changed,
                "descendant_changed": bool(toggled),
                "changed_application_events": toggled,
            },
        },
        "lineage": {
            "source_type": "fully_simulated",
            "topology_family": VERSION + ":" + family,
            "topic_is_presentation_label": True,
            "topic_digest": topic_digest(topic),
            "topic_view_id": hashlib.sha256(
                _dump([identity, topic]).encode()
            ).hexdigest()[:20],
            "qa_layout": "independent_branches",
        },
        "admission": {
            "candidate": True,
            "strict_long_dependency": False,
            "model_utility_measured": False,
            "framework_ready": False,
            "release_ready": False,
        },
    }


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    errors, checks = [], {}
    try:
        checks["deterministic_reproduction"] = bundle == generate_bundle(
            bundle["seed"],
            bundle["family"],
            bundle["n_records"],
            bundle["n_questions"],
            bundle.get("topic"),
        )
        # The runner prepends bundle["topic"] verbatim to the user message, and
        # nothing else in the bundle constrains it: topic_view_id is recomputed
        # from the topic, so a tampered topic matches itself. Recompute the
        # digest from the topic carried in the bundle and compare it with the
        # generation-time value in lineage.
        checks["topic_digest_matches_generated_topic"] = bundle["lineage"].get(
            "topic_digest"
        ) == topic_digest(bundle.get("topic"))
        for label, view in [
            ("original", bundle),
            ("counterfactual", bundle["counterfactual"]),
        ]:
            for task in view["tasks"]:
                checks[label + ":" + task["task_id"]] = (
                    solve_visible(view["context"], task["question"]) == task["answer"]
                )
                checks[label + ":compact:" + task["task_id"]] = (
                    solve_visible(
                        task["controls"]["oracle_compact_context"], task["question"]
                    )
                    == task["answer"]
                )
        checks["answer_sensitive_intervention"] = (
            bundle["counterfactual"]["checks"]["changed_answer_count"] > 0
        )
        checks["descendant_recomputed"] = bundle["counterfactual"]["checks"][
            "descendant_changed"
        ]
        checks["unique_questions"] = (
            len({_dump(t["question"]) for t in bundle["tasks"]})
            == bundle["n_questions"]
        )
        errors.extend(name for name, passed in checks.items() if not passed)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append(str(exc))
    return {"passed": not errors, "errors": errors, "checks": checks}
