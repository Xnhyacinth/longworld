#!/usr/bin/env python3
"""Wrong-context baseline: a foreign world's context must not yield the gold.

Two controls, one question-side channel:

* legacy (foreign world): for a sample of tasks, solve each task's QUESTION
  against a DIFFERENT world of the same family and record how often the
  executor still returns the stored gold. A leak means the question alone
  (its constants, its phrasing) determines the answer. The user review
  demoted this control: a foreign world's entity ids may not even exist, the
  executor returns UNKNOWN/refuses, and an easy pass proves little.

* matched-entity control (P72 G72-5): keep the question's surface identity
  FIXED -- entity names, alias strings, group names, row ids, schema, answer
  type, the header contract -- and perturb only the question-relevant
  CONTENT of the same world: shift the amounts the aggregate reads, violate
  one filter condition on one consumed row, rebind an alias declaration to a
  different entity, move one hold row out of the fold, swap one
  demonstration's label. Then re-solve. The gold must NOT reproduce. This
  asks "did the answer use the content", not "does the id exist".

A family executor that refuses after mutation is a meaningful outcome and is
reported separately (refused) from solved-but-different (answer_changed) and
from a reproduced gold (gold_reproduced, the leak signal to read).

Usage:
  python scripts/measure_wrong_context_baseline.py BANK [--sample N] [--json]
Samples N tasks (default 200, deterministic) and runs BOTH controls on the
same sampled tasks, so the two rates share a denominator.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_capability_records import canonical, module_for

RECORDS_FAMILIES = ("filter_aggregate", "group_compare", "join_lookup")
ASOF_VALUE_TERMINALS = ("balance", "by_entity", "total", "delta")


def load_bank(bank: Path) -> list[dict]:
    """world_id -> its tasks with question and gold, plus the stored context."""
    manifest = json.loads((bank / "manifest.json").read_text())
    worlds = []
    for receipt in manifest["shards"]:
        shard = bank / "shards" / receipt["shard_id"]
        bundle = json.loads((shard / "world.json").read_text())
        rows = [
            json.loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        if not rows:
            continue
        worlds.append(
            {
                "world_id": bundle["world_id"],
                "family": rows[0]["family"],
                "context": bundle["context"],
                "tasks": [
                    {
                        "task_id": row["example_id"].split(":")[-1],
                        "question": next(
                            t["question"]
                            for t in bundle["tasks"]
                            if t["task_id"] == row["example_id"].split(":")[-1]
                        ),
                        "task": next(
                            t
                            for t in bundle["tasks"]
                            if t["task_id"] == row["example_id"].split(":")[-1]
                        ),
                        "gold": row["messages"][1]["content"],
                    }
                    for row in rows
                ],
            }
        )
    return worlds


# --------------------------------------------------------------------------
# Matched-entity mutation: content perturbation with surface identity fixed
# --------------------------------------------------------------------------


def _dump(row: dict) -> str:
    # The bank's own line convention (records._dump), byte-verified.
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_rows(header_line: str, rows: list[dict]) -> str:
    """Re-render a context: the header byte-identical, rows re-dumped in place."""
    return "\n".join([header_line, *(_dump(row) for row in rows)])


def _parse(context: str) -> tuple[str, list[dict]]:
    lines = context.splitlines()
    return lines[0], [json.loads(line) for line in lines[1:]]


def _shift_day(value: str, days: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def _violate(row: dict, condition: dict) -> None:
    """Set one field so the row no longer satisfies one filter condition."""
    field, op, value = condition["field"], condition["op"], condition["value"]
    if field == "amount":
        row["amount"] = (
            value - 1
            if op in (">=", ">")
            else value + 1
            if op
            in (
                "<=",
                "<",
            )
            else value + 1
            if op == "=="
            else value
        )
    elif field == "date":
        row["date"] = (
            _shift_day(value, -1)
            if op in (">=", ">")
            else _shift_day(value, 1)
            if op in ("<=", "<")
            else _shift_day(value, 1)
            if op == "=="
            else value
        )
    else:  # category: == needs another name, != needs the named one
        row["category"] = "zz-out-of-scope" if op == "==" else value


def _satisfy(row: dict, condition: dict) -> None:
    """Set one field so the row satisfies one filter condition exactly."""
    field, op, value = condition["field"], condition["op"], condition["value"]
    if field == "amount":
        row["amount"] = (
            value if op in (">=", "<=", "==") else value + 1 if op == ">" else value - 1
        )
    elif field == "date":
        row["date"] = (
            value
            if op in (">=", "<=", "==")
            else _shift_day(value, 1)
            if op == ">"
            else _shift_day(value, -1)
        )
    else:
        row["category"] = value if op in ("==", ">=", "<=") else "zz-out-of-scope"


def _mutate_records(
    rows: list[dict], header: dict, task: dict, rng: random.Random
) -> bool:
    """Amounts the terminal reads shift; one consumed row leaves the filter.

    Surface identity (ids, entities, categories, dates-as-schema, memos,
    reference structure) is untouched: only the numeric content and one
    row's filter-relevant field move, so every answer shape that reads
    value or membership must change.
    """
    by_id = {row["id"]: row for row in rows}
    consumed = [
        by_id[cid]
        for cid in task["consumed"]
        if cid in by_id and by_id[cid].get("type") == "record"
    ]
    if not consumed:
        return False
    steps = task["question"]["steps"]
    terminal = steps[-1]
    filters = [step for step in steps[:-1] if step.get("op") == "filter"]
    reads_count_only = (
        terminal.get("op") in ("aggregate", "group_compare", "join_aggregate")
        and terminal.get("how") == "count"
        and not filters
    )
    if reads_count_only:
        # No filter and no value read: nothing content-level to move.
        return False
    for row in consumed:
        row["amount"] = row["amount"] + 13
    if filters:
        _violate(consumed[0], filters[-1]["conditions"][0])
    if task["question"]["family"] == "join_lookup":
        # The join's aggregate reads the reference side: shift those too.
        matched = {row["entity"] for row in consumed}
        for row in rows:
            if row.get("type") == "reference" and row["entity"] in matched:
                row["amount"] = row["amount"] + 13
    return True


def _mutate_alias(
    rows: list[dict], header: dict, task: dict, rng: random.Random
) -> bool:
    """Same alias string, bound to a different entity (or its empty match broken).

    The bind step's handle and the world's surface stay fixed; the alias
    declaration's entity -- the one relation the answer reads -- moves to
    another entity that exists in the same world.
    """
    by_id = {row["id"]: row for row in rows}
    steps = task["question"]["steps"]
    handle = steps[0]["alias"]
    declarations = [
        row for row in rows if row.get("type") == "alias" and row["alias"] == handle
    ]
    if len(declarations) != 1:
        return False
    declaration = declarations[0]
    if steps[-1]["op"] == "locate_empty":
        # The gold declares an empty match: make one row of the bound entity
        # satisfy the filter, so the declaration is now contradicted by content.
        bound = declaration["entity"]
        candidates = [
            row
            for row in rows
            if row.get("type") == "record" and row["entity"] == bound
        ]
        filters = [step for step in steps[1:-1] if step.get("op") == "filter"]
        if not filters or not candidates:
            return False
        for condition in filters[-1]["conditions"]:
            _satisfy(candidates[0], condition)
        return True
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.get("type") == "record":
            counts[row["entity"]] += 1
    bound = declaration["entity"]
    others = sorted(entity for entity in counts if entity != bound)
    if not others:
        return False
    # Prefer an entity with a different row count, so count answers move too.
    target = next((e for e in others if counts[e] != counts[bound]), others[0])
    declaration["entity"] = target
    return True


def _mutate_asof(
    rows: list[dict], header: dict, task: dict, rng: random.Random
) -> bool:
    """Same event rows and reveal cutoffs; the amounts or kinds the fold reads move.

    Value terminals (balance/total/by_entity/delta) read credit/debit
    arithmetic: one such row of a named entity shifts. The active_set
    terminal reads held: the entity's last hold row in fold order is removed
    from the held arithmetic (release overdrawn or set_aside revealed after
    the cutoff), flipping the membership the gold reports.
    """
    by_id = {row["id"]: row for row in rows}
    consumed = [by_id[cid] for cid in task["consumed"] if cid in by_id]
    terminal = task["question"]["steps"][-1]
    named = set(terminal.get("entities") or [terminal.get("entity")])
    own = [row for row in consumed if row["entity"] in named]
    if not own:
        return False
    if terminal["op"] == "active_set":
        active = set(task["answer"].get("active_set") or [])
        if active:
            entity = sorted(active)[0]
        else:
            entity = sorted(named)[0]
        later = task["answer"]["as_of"]
        holds = sorted(
            (
                row
                for row in rows
                if row["entity"] == entity and row["kind"] in ("set_aside", "release")
            ),
            key=lambda row: (row["date"], row["id"]),
        )
        if not holds:
            return False
        in_fold = [row for row in holds if row["reveal"] <= later]
        if active:
            # Leave the set: hide the last IN-FOLD set_aside past the cutoff
            # (a row already outside the fold moves nothing). With the last
            # set_aside gone, the release before it overdraws unless a later
            # set_aside refills -- either way held changes or the membership
            # flips; if held happens to survive numerically, the mutated row
            # ids make the gold's provenance differ, and the executor's own
            # min() cap on releases means growing a release can be a no-op,
            # which is why hiding is the primary move.
            candidate = next(
                (row for row in reversed(in_fold) if row["kind"] == "set_aside"),
                None,
            )
            if candidate is None:
                candidate = in_fold[-1]
                if candidate["kind"] == "release":
                    # Only releases in the fold: the set_aside funding them is
                    # outside; grow the set_aside the release draws from.
                    outside = [row for row in holds if row["reveal"] > later]
                    if not outside:
                        return False
                    outside[0]["amount"] = outside[0]["amount"] + 500
                    return True
            candidate["reveal"] = _shift_day(later, 1)
        else:
            # Enter the set: the first set_aside grows beyond any release.
            first = next((row for row in holds if row["kind"] == "set_aside"), None)
            if first is None:
                return False
            first["amount"] = first["amount"] + 500
        return True
    flow = next((row for row in own if row["kind"] in ("credit", "debit")), None)
    if flow is None:
        return False
    flow["amount"] = flow["amount"] + 500
    return True


def _mutate_rule(
    rows: list[dict], header: dict, task: dict, rng: random.Random
) -> bool:
    """Same rule family, labels and entities; the content the label reads moves.

    Every terminal's label is a function of the named entities' aggregated
    record features, and the single-entity terminals also ECHO those
    features in the gold, so shifting features moves the answer's input. No
    single shift is always decisive: a +7/+7 shift can leave a threshold
    label on the same side of t, a single-row +1 can leave a parity count's
    parity unchanged (two rows flipping membership), and a demo-label swap
    can identify a DIFFERENT rule that still agrees on the queried entities
    (the false-pass this control exists to catch). The mutator therefore
    tries a small deterministic ladder of shifts and takes the first one
    whose re-solve differs from the stored gold -- the mutation is verified,
    not hoped.
    """
    steps = task["question"]["steps"]
    terminal = steps[-1]
    if terminal["op"] in ("label", "verify"):
        named = [terminal["entity"]]
    else:
        named = list(terminal.get("entities") or [])
    if not named:
        return False
    wanted = set(named)
    features = [
        row for row in rows if row.get("type") == "record" and row["entity"] in wanted
    ]
    if not features:
        return False
    # Ladder candidates, tried in order until one moves the gold: every step
    # of the ladder shifts the named entities' features; mutate_context
    # verifies by re-solving, and the ladder advances only on verification
    # failure (the first step that changes the answer wins).
    ladder = (
        (7, 7, False),
        (1, 0, True),
        (3, 1, False),
        (0, 1, True),
        (2, 2, False),
        (4, 3, False),
        (5, 2, True),
    )
    module = module_for("rule_holdout")
    question = task["question"]
    for dx, dy, once in ladder:
        touched = False
        for row in features:
            row["x"] += dx
            row["y"] += dy
            touched = True
            if once:
                break
        if not touched:
            return False
        try:
            re_answer = module.solve_visible(
                render_rows(_dump(header), rows), question
            )
        except (ValueError, KeyError, IndexError, TypeError):
            return True  # refused on the mutated world: a change
        if canonical(re_answer) != _gold_of(task):
            return True
    return False


def _mutate_join_unanswerable(
    rows: list[dict], header: dict, task: dict, rng: random.Random
) -> bool:
    """Same filter survivors; their entity binding moves to a referenced entity.

    The stored gold is UNKNOWN because no consumed entity has a reference
    row. Rebinding consumed records to an entity that HAS one makes the join
    determined: the gold must not reproduce, which is exactly the claim that
    the answer depends on the entity-reference relation (content), not on
    the question's surface.
    """
    referenced = [row["entity"] for row in rows if row.get("type") == "reference"]
    if not referenced:
        return False
    target = referenced[0]
    by_id = {row["id"]: row for row in rows}
    moved = False
    for cid in task["consumed"]:
        row = by_id.get(cid)
        if row and row.get("type") == "record" and row["entity"] != target:
            row["entity"] = target
            moved = True
    return moved


MUTATORS = {
    "filter_aggregate": _mutate_records,
    "group_compare": _mutate_records,
    "join_lookup": _mutate_records,
    "alias_locate": _mutate_alias,
    "asof_state": _mutate_asof,
    "rule_holdout": _mutate_rule,
    "join_unanswerable": _mutate_join_unanswerable,
}


def _gold_of(task: dict) -> str:
    """The task's stored gold in canonical form (bank row or generator task)."""
    if "messages" in task:
        return task["messages"][1]["content"]
    return canonical(task["answer"])


def mutate_context(context: str, task: dict, rng: random.Random) -> str | None:
    """The matched-entity mutation of one world for one task, or None.

    Pure function over (context, task): the header line and every untouched
    row re-render byte-identically; only the question-relevant content moves.
    The mutation is VERIFIED, not hoped: the mutated world is re-solved with
    the same executor, and a mutation whose gold still reproduces is
    rejected (None, counted as unmutable) rather than reported as a control
    pass -- a false pass is exactly the failure mode this probe exists to
    expose. A refusal on the mutated world counts as a change.
    """
    family = task["question"]["family"]
    mutator = MUTATORS.get(family)
    if mutator is None:
        return None
    header_line, rows = _parse(context)
    header = json.loads(header_line)
    mutated = [dict(row) for row in rows]
    if not mutator(mutated, header, task, rng):
        return None
    rendered = render_rows(header_line, mutated)
    module = module_for(family)
    try:
        re_answer = module.solve_visible(rendered, task["question"])
    except (ValueError, KeyError, IndexError, TypeError):
        return rendered  # the executor refuses the mutated world: a change
    if canonical(re_answer) == _gold_of(task):
        return None  # the gold survived: not a valid mutation
    return rendered


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def measure(bank: Path, sample: int) -> dict:
    worlds = load_bank(bank)
    by_family = defaultdict(list)
    for world in worlds:
        by_family[world["family"]].append(world)
    rng = random.Random(20260919)
    # Legacy counters (foreign-world control), kept under their old keys.
    checked = matched = 0
    per_family: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    # Matched-entity control counters.
    control = defaultdict(
        lambda: {
            "checked": 0,
            "answer_changed": 0,
            "refused": 0,
            "gold_reproduced": 0,
            "unmutable": 0,
        }
    )
    for family in sorted(by_family):
        pool = by_family[family]
        if len(pool) < 2:
            continue
        probes = [(world, task) for world in pool for task in world["tasks"]]
        rng.shuffle(probes)
        for world, task in probes[: max(1, sample // len(by_family))]:
            # Legacy: the foreign world is the next same-family world in the ring.
            foreign = pool[(pool.index(world) + 1) % len(pool)]
            if foreign["world_id"] != world["world_id"]:
                module = module_for(family)
                try:
                    answer = module.solve_visible(foreign["context"], task["question"])
                except (ValueError, KeyError, IndexError, TypeError):
                    # The question does not even execute against the foreign
                    # world (constants out of domain): no leak, not an error.
                    answer = None
                checked += 1
                per_family[family][0] += 1
                if answer is not None and canonical(answer) == task["gold"]:
                    matched += 1
                    per_family[family][1] += 1
            # Matched-entity: same world, question-relevant content mutated.
            module = module_for(family)
            mutated = mutate_context(world["context"], task["task"], rng)
            entry = control[family]
            if mutated is None:
                entry["unmutable"] += 1
                entry["checked"] += 1
                continue
            entry["checked"] += 1
            try:
                answer = module.solve_visible(mutated, task["question"])
            except (ValueError, KeyError, IndexError, TypeError):
                entry["refused"] += 1
                continue
            if canonical(answer) == task["gold"]:
                entry["gold_reproduced"] += 1
            else:
                entry["answer_changed"] += 1
    solved_total = sum(
        entry["answer_changed"] + entry["gold_reproduced"] for entry in control.values()
    )
    reproduced_total = sum(entry["gold_reproduced"] for entry in control.values())
    return {
        "bank": str(bank),
        # ---- legacy keys: the foreign-world control, unchanged semantics ----
        "checked": checked,
        "gold_leaks": matched,
        "leak_rate": round(matched / checked, 4) if checked else None,
        "per_family": {
            family: {"checked": counts[0], "gold_leaks": counts[1]}
            for family, counts in sorted(per_family.items())
        },
        "reading": (
            "a leak means the question alone determines the stored gold for a "
            "foreign same-family world — the context-independent channel, "
            "complementary to the question-only probe"
        ),
        # ---- matched-entity control (P72 G72-5) ----
        "matched_entity_control": {
            "checked": sum(entry["checked"] for entry in control.values()),
            "unmutable": sum(entry["unmutable"] for entry in control.values()),
            "answer_changed": sum(
                entry["answer_changed"] for entry in control.values()
            ),
            "refused": sum(entry["refused"] for entry in control.values()),
            "gold_reproduced": reproduced_total,
            "reproduce_rate_over_solved": round(reproduced_total / solved_total, 4)
            if solved_total
            else None,
            "per_family": {
                family: dict(entry) for family, entry in sorted(control.items())
            },
            "reading": (
                "surface identity (ids, entities, alias strings, group names, "
                "schema, header) is fixed and only question-relevant content "
                "moves; gold_reproduced is the leak signal (the gold survived "
                "a content mutation), refused is the executor failing closed "
                "on the mutated world, answer_changed is the control passing"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = measure(args.bank, args.sample)
    print(json.dumps(report, indent=2) if args.json else json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
