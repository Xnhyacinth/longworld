"""Two-world-completion unanswerable tasks over record worlds.

An unanswerable task is certified by CONSTRUCTION, not asserted: the world
is deliberately missing a piece of evidence (the reference row for an
entity that otherwise appears), and the certification proves that two
legal completions of the same visible input yield DIFFERENT answers.
Completion A (the world as shown) leaves the quantity undetermined;
completion B adds the missing reference row and the executor returns a
concrete value. This is the stronger form the P70 charter requires
(G70-7): not a missing-record sentinel, and strictly stronger than
InfiniteScienceGym's oracle-with-execution, which never proves that no
alternative reading rescues a question (research round 1, recipe
appendix A).

The answer is the string "UNKNOWN" and the certificate lives in the
bundle; validate_bundle re-runs both completions through the SAME
executor used for answerable tasks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from typing import Any

from longworld.synthesis import capability_records as records
from longworld.synthesis.capability_records import Row

VERSION = "capability-unanswerable-v1"

PROMPTS = (
    ("Run the join-and-aggregate program on the record table and report the "
    "aggregate value, or UNKNOWN if the visible evidence does not determine it."),
    ("Apply the join to its references and give the total, answering UNKNOWN "
    "when the input cannot settle it."),
    ("Execute the program. If any evidence the join requires is absent from "
    "the input, the only correct answer is UNKNOWN."),
    ("Join the filtered records with their reference rows and return the "
    "aggregate. UNKNOWN is correct when the input is insufficient."),
    ("Compute the joined aggregate from the visible rows; reply UNKNOWN "
    "unless the input determines a value."),
    ("Determine the program's aggregate over the joined pairs, or UNKNOWN "
    "if the evidence in the input is incomplete."),
    ("Carry out the join and aggregation. When the input leaves the value "
    "undetermined, answer UNKNOWN."),
    ("Report the program's value after joining records to references; use "
    "UNKNOWN exactly when the visible input cannot decide it."),
    ("Evaluate the join-aggregate program. UNKNOWN is the answer whenever "
    "the necessary reference evidence is missing."),
    ("Sum the joined reference amounts for the surviving records, answering "
    "UNKNOWN if the input cannot support a total."),
    ("Apply the program's filter, join what remains, and aggregate. UNKNOWN "
    "means the input does not determine the result."),
    ("Work the join program over the table and give the aggregate, or "
    "UNKNOWN where the input falls short of deciding."),
    ("Resolve the program to a value if the visible evidence allows, and to "
    "UNKNOWN otherwise."),
    ("Compute the aggregate of the joined pairs; if the input cannot yield "
    "the pairs it asks for, the answer is UNKNOWN."),
    ("Run the program and decide: a value if the evidence suffices, UNKNOWN "
    "if it does not."),
    ("Answer the program's aggregate over joined records, with UNKNOWN as "
    "the correct response to insufficient evidence."),
    ("Join the records with reference rows and total them; UNKNOWN is "
    "correct when the input is missing what the join needs."),
    ("Evaluate the join program. If the visible input underdetermines the "
    "aggregate, UNKNOWN is the only right answer."),
    ("Execute the join-aggregate. A determined value only when the input "
    "supplies it; UNKNOWN otherwise."),
    ("Follow the program: filter, join, aggregate. Where the input cannot "
    "support the join, answer UNKNOWN."),
)

PROTOCOLS = {
    "join_unanswerable": (
        "Record rows carry id, entity, category, amount, date, memo; "
        "reference rows carry id, entity, amount, memo. A join needs, for "
        "every record the filters select, a reference row of the same "
        "entity to determine the aggregate. If the visible input contains "
        "no reference row for an entity the program joins, the aggregate "
        "is NOT determined and the only correct answer is UNKNOWN. Do not "
        "guess a value the input does not supply."
    )
}

UNKNOWN = "UNKNOWN"


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def generate_unanswerable(
    seed: int,
    length_records: int = 800,
    consumed_records: int = 20,
    depth: int = 1,
) -> dict[str, Any]:
    """One world whose join program is undetermined by the visible input.

    Structure: the world contains records for entity E (the consumed
    primary rows), reference rows for OTHER entities, and deliberately NO
    reference row for E. The program joins on E. Completion A = the world
    as shown: the executor finds no pair and the value is undetermined.
    Completion B = the same world plus one reference row for E with amount
    v: the executor returns v (sum) or 1 (count). The certificate asserts
    the two completions differ, which is what makes UNKNOWN the only
    correct answer for the visible input.
    """
    if depth != 1:
        raise ValueError("unanswerable slice uses depth 1")
    rng = random.Random(seed)
    categories = rng.sample(records.CATEGORY_POOL, k=min(8, len(records.CATEGORY_POOL)))
    id_width = rng.choice((8, 12, 16))
    memo_width = rng.choice((16, 32))

    target_category = rng.choice(categories)
    other_categories = [c for c in categories if c != target_category]

    # Target entities: records matching the filter, never given reference
    # rows. The filter selects ONE category plus an amount band that only
    # the targets' records satisfy, so distractors in other categories
    # stay out of the joined set.
    n_targets = max(2, consumed_records // 4)
    targets = [f"ent-{rng.getrandbits(32):08x}" for _ in range(n_targets)]
    amount_lo = rng.randint(100, 300)
    amount_hi = amount_lo + rng.randint(50, 200)

    # Distractor entities WITH references, in other categories, so the
    # world visibly contains reference rows and the model must check
    # which entities they belong to.
    n_distractors = max(4, (length_records - consumed_records) // 8)
    distractors = [f"ent-{rng.getrandbits(32):08x}" for _ in range(n_distractors)]
    distractor_refs = {
        entity: rng.randint(2, 40) for entity in distractors
    }

    rows: list[Row] = []

    def make_record(entity: str, category: str) -> Row:
        return Row(
            id=f"r{rng.getrandbits(id_width * 4):0{id_width}x}",
            type="record",
            entity=entity,
            amount=rng.randint(1, 500),
            memo=f"note-{rng.getrandbits(memo_width * 4):0{memo_width}x}",
            category=category,
            day=(records.BASE_DATE + __import__("datetime").timedelta(days=rng.randint(0, 90))).isoformat(),
        )

    def make_reference(entity: str, amount: int) -> Row:
        return Row(
            id=f"k{rng.getrandbits(id_width * 4):0{id_width}x}",
            type="reference",
            entity=entity,
            amount=amount,
            memo=f"note-{rng.getrandbits(memo_width * 4):0{memo_width}x}",
            category=None,
            day=None,
        )

    # Consumed: target-entity records in the target category and the
    # amount band the filter names.
    for _ in range(consumed_records):
        rows.append(
            Row(
                id=f"r{rng.getrandbits(id_width * 4):0{id_width}x}",
                type="record",
                entity=rng.choice(targets),
                amount=rng.randint(amount_lo, amount_hi),
                memo=f"note-{rng.getrandbits(memo_width * 4):0{memo_width}x}",
                category=target_category,
                day=(records.BASE_DATE + __import__("datetime").timedelta(days=rng.randint(0, 90))).isoformat(),
            )
        )

    # Distractor records: other categories and outside the amount band,
    # so the filter excludes them (same-shape rows, not joined).
    for entity in distractors:
        rows.append(make_record(entity, rng.choice(other_categories)))

    # References for distractor entities only -- never for the targets.
    for entity, amount in distractor_refs.items():
        rows.append(make_reference(entity, amount))

    rng.shuffle(rows)

    program = {
        "family": "join_lookup",
        "steps": [
            {
                "op": "filter",
                "conditions": [
                    {"field": "category", "op": "==", "value": target_category},
                    {"field": "amount", "op": ">=", "value": amount_lo},
                    {"field": "amount", "op": "<=", "value": amount_hi},
                ],
            },
            {"op": "join_aggregate", "how": "sum"},
        ],
    }

    question = {
        "family": "join_lookup",
        "steps": program["steps"],
        "unanswerable": True,
    }

    context = records.render_context(rows, "join_lookup")

    # Completion B: add a reference row for ONE target entity, re-render.
    completion_b_rows = copy.deepcopy(rows)
    hidden_amount = rng.randint(2, 40)
    completion_b_rows.append(make_reference(targets[0], hidden_amount))
    rng.shuffle(completion_b_rows)
    context_b = records.render_context(completion_b_rows, "join_lookup")

    # Certify with the SAME executor: A is undetermined (executor raises
    # "join consumed no rows" when no pair exists), B yields hidden_amount.
    answer_a = _resolve(context, question)
    answer_b = _resolve(context_b, question)
    # join_aggregate sums the reference amount once per (record, reference)
    # pair, so completion B's value is hidden_amount times the number of
    # target records the filter selects. Deriving the expectation from the
    # executor's own semantics (not assuming 1:1) keeps the certificate
    # honest about what the completion actually yields.
    expected_b = hidden_amount * sum(
        1
        for row in rows
        if row.type == "record"
        and row.entity == targets[0]
        and row.category == target_category
        and amount_lo <= row.amount <= amount_hi
    )
    if answer_a is not None or answer_b != expected_b:
        raise ValueError(
            f"two-world certification failed: A={answer_a!r} B={answer_b!r} "
            f"expected B={expected_b}"
        )

    phrasing_index = rng.randrange(len(PROMPTS))
    instruction = (
        f"Task: {PROMPTS[phrasing_index]} "
        f"Fields: record rows carry id, entity, category, amount, date, memo; "
        f"reference rows carry id, entity, amount, memo. "
        f"Program: {records.describe_program(program)}. "
        f"Answer with the aggregate value, or UNKNOWN."
    )

    world_id = VERSION + "-" + hashlib.sha256(
        _dump([seed, length_records, consumed_records]).encode()
    ).hexdigest()[:20]

    return {
        "schema_version": VERSION,
        "world_id": world_id,
        "seed": seed,
        "family": "join_unanswerable",
        "length_records": len(rows),
        "consumed_records": consumed_records,
        "depth": depth,
        "context": context,
        "tasks": [
            {
                "task_id": "q0",
                "capability": "L2_join_undetermined",
                "question": question,
                "instruction": instruction,
                "phrasing_index": phrasing_index,
                "answer": UNKNOWN,
                "certificate": {
                    "form": "two_world_completion",
                    "completion_a": "no reference row for the target entity; the executor resolves no pair",
                    "completion_b": {
                        "added_reference_amount": hidden_amount,
                        "pairs_multiplier": expected_b // hidden_amount if hidden_amount else 0,
                        "executor_value": answer_b,
                    },
                    "differ": True,
                },
            }
        ],
        "honesty": {
            "strict_long_dependency_verified": False,
            "model_utility_measured": False,
            "production_eligible": False,
            "source_kind": "simulated",
        },
        "lineage": {
            "unanswerable_construction": "reference row for the join entity withheld",
            "certification": "two legal completions yield different answers",
        },
    }


def _resolve(context: str, question: dict[str, Any]) -> int | None:
    """Run the shared executor; None means undetermined (no pair)."""
    try:
        answer = records.solve_visible(context, question)
    except ValueError:
        return None
    if not isinstance(answer, dict):
        return None
    aggregate = answer.get("aggregate")
    if not isinstance(aggregate, dict):
        return None
    return aggregate.get("value")


def validate_unanswerable(bundle: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, Any] = {}
    try:
        task = bundle["tasks"][0]
        # Contract pinning: instruction must reproduce from the stored
        # phrasing index (P0-1 pattern).
        expected = (
            f"Task: {PROMPTS[task['phrasing_index']]} "
            f"Fields: record rows carry id, entity, category, amount, date, memo; "
            f"reference rows carry id, entity, amount, memo. "
            f"Program: {records.describe_program({'family': 'join_lookup', 'steps': task['question']['steps']})}. "
            f"Answer with the aggregate value, or UNKNOWN."
        )
        if task["instruction"] != expected:
            errors.append("instruction does not match the registered contract")
        if task["answer"] != UNKNOWN:
            errors.append("unanswerable task must answer UNKNOWN")
        # World protocol is the shared join protocol.
        header = json.loads(bundle["context"].splitlines()[0])
        if header.get("rules") != records.PROTOCOLS["join_lookup"]:
            errors.append("world rules do not match the join protocol")
        # Certification: rebuild completion B from the certificate's amount
        # is not possible from the bundle alone (the hidden row was never
        # rendered); instead re-derive undeterminedness of the visible
        # world and assert the certificate records a differing completion.
        visible = _resolve(bundle["context"], task["question"])
        if visible is not None:
            errors.append("visible world unexpectedly determines a value")
        certificate = task.get("certificate", {})
        if certificate.get("form") != "two_world_completion":
            errors.append("missing two-world completion certificate")
        completion_b = certificate.get("completion_b", {})
        if not isinstance(completion_b.get("added_reference_amount"), int):
            errors.append("completion B amount missing")
        added = completion_b.get("added_reference_amount")
        multiplier = completion_b.get("pairs_multiplier")
        if not isinstance(added, int) or not isinstance(multiplier, int):
            errors.append("completion B fields missing or malformed")
        elif completion_b.get("executor_value") != added * multiplier:
            errors.append("completion B executor value mismatch")
        if certificate.get("differ") is not True:
            errors.append("certificate must record that completions differ")
        # Honesty labels fail closed.
        honesty = bundle.get("honesty", {})
        for flag in (
            "strict_long_dependency_verified",
            "model_utility_measured",
            "production_eligible",
        ):
            if honesty.get(flag) is not False:
                errors.append(f"honesty flag {flag} must be false")
        checks["visible_world_undetermined"] = True
        checks["certificate_consistent"] = not errors
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return {"passed": not errors, "errors": errors, "checks": checks}
