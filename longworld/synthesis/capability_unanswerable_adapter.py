"""Spine adapter for the join_unanswerable family (P71, track B4).

`capability_unanswerable` builds two-world-completion tasks, but not on the
runner spine: no (L, K, H, n) surface, no exact-L worlds, no
length_accounting, and its question carries the *world* family
("join_lookup"), which FAMILY_MODULES-style dispatch cannot resolve. This
module adapts that contract onto the six-family module surface so
"join_unanswerable" can become a bank family:

* The bank family is "join_unanswerable" -- the dispatch key, the bundle
  family and the task question's family. The world itself stays a records
  join_lookup world: same Row shape, same id/memo/entity conventions, same
  render_context header and the same executor. That is what keeps the
  unanswerable rows' formats, lengths and sources matched to the answerable
  join rows (the plan's distribution-matching requirement). Every executor
  call translates the question's family back to "join_lookup"; nothing else
  about the program changes.
* One task per question, the answer always "UNKNOWN", depth always 1, and
  the original module's own 20-phrasing PROMPTS bank: the constraints of
  capability_unanswerable, kept. A world hosts n_variants independently
  undetermined joins, one certificate each, so a shared
  variants_per_world config schedules this family exactly like the others.
* The two-world certificate lives per task under task["certificate"] and is
  mirrored at the bundle top level under "unanswerable_certificate" (keyed
  by task id). The runner's _rows copies only the pinned task keys into
  rows.jsonl, while run_shard writes the whole bundle to world.json, so the
  bundle-level mirror is the copy an audit actually reads.

Honesty limits are the bank's, fail-closed: strict_long_dependency_verified,
model_utility_measured and production_eligible are false and
validate_bundle() rejects a bundle that claims otherwise; source_kind is
"simulated". L - K padding is length exposure, not semantic scale.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import timedelta
from typing import Any

from longworld.synthesis import capability_records as records
from longworld.synthesis import capability_unanswerable as cu

VERSION = cu.VERSION
FAMILIES = ("join_unanswerable",)
# The world protocol is the shared join_lookup contract the context header
# pins and the executor enforces; the UNKNOWN rule rides the instruction
# (PROMPTS + RETURNS). PROTOCOLS follows the records convention -- the text
# a validator compares against the header -- so it is join_lookup's.
PROTOCOLS: dict[str, str] = {"join_unanswerable": records.PROTOCOLS["join_lookup"]}
PROMPTS: dict[str, tuple[str, ...]] = {"join_unanswerable": cu.PROMPTS}
RETURNS: dict[str, str] = {
    "join_unanswerable": "Answer with the aggregate value, or UNKNOWN."
}
FIELDS: dict[str, str] = {"join_unanswerable": records.FIELDS["join_lookup"]}
HONESTY: dict[str, Any] = dict(records.HONESTY)
UNKNOWN = cu.UNKNOWN


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_id(prefix: str, rng: random.Random, width: int = 24) -> str:
    """One row identity at the world's own hex width (records convention)."""
    return prefix + format(rng.getrandbits(4 * width), f"0{width}x")


def _fresh_entity(rng: random.Random, used: set[str]) -> str:
    """A fresh entity name; no two entities of one world may share one."""
    entity = f"unit-{rng.getrandbits(20):05x}"
    while entity in used:
        entity = f"unit-{rng.getrandbits(20):05x}"
    used.add(entity)
    return entity


def _executor_question(question: dict[str, Any]) -> dict[str, Any]:
    """The join_lookup view of a stored question: family translation only."""
    if question.get("family") != "join_unanswerable":
        raise ValueError("not a join_unanswerable question")
    return {**question, "family": "join_lookup"}


def solve_visible(context: str, question: dict[str, Any]) -> Any:
    """The shared records executor, over the translated question."""
    return records.solve_visible(context, _executor_question(question))


def _resolve(context: str, question: dict[str, Any]) -> int | None:
    """Run the shared executor; None means undetermined (no pair)."""
    try:
        answer = solve_visible(context, question)
    except ValueError:
        return None
    if not isinstance(answer, dict):
        return None
    aggregate = answer.get("aggregate")
    if not isinstance(aggregate, dict):
        return None
    return aggregate.get("value")


def _join_domain(context: str, question: dict[str, Any]) -> list[str]:
    """Row ids the filter chain keeps: the join's input domain.

    Derived through the shared executor itself rather than declared by the
    generator: the same filter steps with a count terminal report `matched`,
    which is exactly the post-filter table the join step reads. The
    undetermined join then pairs none of them, so this is the relation the
    program consumed while failing -- the K of the (L, K, H) grid.
    """
    steps = question["steps"]
    counted = records.solve_visible(
        context,
        {
            "family": "join_lookup",
            "steps": [*steps[:-1], {"op": "aggregate", "how": "count"}],
        },
    )
    return counted["matched"]


def render_instruction(
    family: str, question: dict[str, Any], phrasing_index: int
) -> str:
    """The stored instruction, re-rendered from (question, phrasing index).

    Byte-identical to capability_unanswerable's instruction contract, so a
    reworded prompt cannot reach a training row: the runner's
    build_messages gate re-renders through this function and compares.
    """
    prompts = PROMPTS[family]
    if not 0 <= phrasing_index < len(prompts):
        raise ValueError("invalid phrasing index")
    return (
        f"Task: {prompts[phrasing_index]} "
        f"Fields: {FIELDS[family]} "
        f"Program: {records.describe_program(question)}. "
        f"{RETURNS[family]}"
    )


def plan_variants(length_records: int, consumed: int, requested: int) -> int:
    """How many K-sized undetermined joins fit in L with real distractor room."""
    room = length_records - max(8, length_records // 10)
    return max(1, min(requested, room // max(4, (consumed * 6) // 5)))


def generate_world(
    seed: int,
    family: str = "join_unanswerable",
    length_records: int = 800,
    consumed_records: int = 60,
    depth: int = 1,
    n_variants: int = 4,
) -> dict[str, Any]:
    """Build one undetermined-join world on the runner spine.

    L record rows exactly (consumed join domains plus same-schema distractor
    records), reference rows rendered as extras outside L, one typed program
    per variant. Per task: the filter selects a category and an amount band
    whose K matching records belong to target entities that deliberately
    have no reference row, while distractor entities in other categories do
    -- so the world visibly contains references, and the model must check
    which entities they belong to. Completion A (the world as shown) leaves
    the aggregate undetermined; completion B adds one reference row for a
    target entity and the same executor returns a concrete value. Both run
    here, at generation time, and the certificate records the difference.
    """
    if family not in PROTOCOLS:
        raise ValueError("unsupported family")
    if type(length_records) is not int or length_records < 32:
        raise ValueError("length_records must be an integer >= 32")
    if type(consumed_records) is not int or consumed_records < 4:
        raise ValueError("consumed_records must be an integer >= 4")
    if type(depth) is not int or depth != 1:
        raise ValueError("depth must be 1 for join_unanswerable")
    if type(n_variants) is not int or not 1 <= n_variants <= 8:
        raise ValueError("n_variants must be 1..8")
    if n_variants != plan_variants(length_records, consumed_records, n_variants):
        raise ValueError(
            "K budget does not fit inside L; shrink K or the variant count"
        )
    rng = random.Random(seed)
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    # One distinct category per task plus at least one residual category for
    # the distractor records: a residual-category row fails every task's
    # filter on the category condition alone, so no record that carries (or
    # sits next to) a reference can ever enter a join domain.
    categories = rng.sample(
        records.CATEGORY_POOL, min(len(records.CATEGORY_POOL), max(8, n_variants + 1))
    )
    residual = categories[n_variants:]

    used: set[str] = set()

    def record(entity: str, category: str, amount: int) -> records.Row:
        return records.Row(
            id=_new_id("r", rng, id_width),
            type="record",
            entity=entity,
            amount=amount,
            memo=_new_id("note-", rng, memo_width),
            category=category,
            day=(records.BASE_DATE + timedelta(days=rng.randint(0, 90))).isoformat(),
        )

    def reference(entity: str, amount: int) -> records.Row:
        return records.Row(
            id=_new_id("k", rng, id_width),
            type="reference",
            entity=entity,
            amount=amount,
            memo=_new_id("note-", rng, memo_width),
        )

    specs = []
    for index in range(n_variants):
        amount_lo = rng.randint(100, 300)
        specs.append(
            {
                "category": categories[index],
                "lo": amount_lo,
                "hi": amount_lo + rng.randint(50, 200),
                "targets": [
                    _fresh_entity(rng, used)
                    for _ in range(max(2, consumed_records // 4))
                ],
            }
        )

    rows: list[records.Row] = []
    for spec in specs:
        for offset in range(consumed_records):
            # The first row of the domain pins the first target entity, so
            # completion B always pairs at least once and the certificate
            # never degenerates to a second undetermined completion.
            entity = spec["targets"][0] if offset == 0 else rng.choice(spec["targets"])
            rows.append(
                record(entity, spec["category"], rng.randint(spec["lo"], spec["hi"]))
            )

    # Distractor entities WITH references, in residual categories: reference
    # rows are visibly present, but never for an entity a program joins.
    references: list[records.Row] = []
    n_referenced = max(4, (length_records - len(rows)) // 8)
    for _ in range(n_referenced):
        entity = _fresh_entity(rng, used)
        rows.append(record(entity, rng.choice(residual), rng.randint(1, 500)))
        references.append(reference(entity, rng.randint(2, 40)))
    plain = length_records - len(rows)
    if plain < 0:
        raise ValueError("no distractor budget left; shrink K or the variant count")
    for _ in range(plain):
        rows.append(
            record(_fresh_entity(rng, used), rng.choice(residual), rng.randint(1, 500))
        )

    rendered = rows + references
    rng.shuffle(rendered)
    context = records.render_context(rendered, "join_lookup")
    by_id = {row.id: row for row in rendered}

    tasks = []
    certificates: dict[str, Any] = {}
    for index, spec in enumerate(specs):
        question = {
            "family": "join_unanswerable",
            "steps": [
                {
                    "op": "filter",
                    "conditions": [
                        {"field": "category", "op": "==", "value": spec["category"]},
                        {"field": "amount", "op": ">=", "value": spec["lo"]},
                        {"field": "amount", "op": "<=", "value": spec["hi"]},
                    ],
                },
                {"op": "join_aggregate", "how": "sum"},
            ],
            "unanswerable": True,
        }
        consumed = _join_domain(context, question)
        if len(consumed) != consumed_records:
            raise ValueError(
                "the join domain is not exactly K rows; distractor leakage"
            )
        # Completion A: the world as shown. The executor must refuse.
        if _resolve(context, question) is not None:
            raise ValueError("the visible world unexpectedly determines a value")
        # Completion B: the same world plus one reference row for the first
        # target entity. join_aggregate sums the reference amount once per
        # pair, so the executor's own semantics fix the expected value.
        hidden = rng.randint(2, 40)
        target = spec["targets"][0]
        completion_rows = list(rendered) + [reference(target, hidden)]
        rng.shuffle(completion_rows)
        answer_b = _resolve(
            records.render_context(completion_rows, "join_lookup"), question
        )
        multiplicity = sum(1 for row_id in consumed if by_id[row_id].entity == target)
        expected_b = hidden * multiplicity
        if answer_b != expected_b:
            raise ValueError(
                f"two-world certification failed: B={answer_b!r} "
                f"expected B={expected_b}"
            )
        certificate = {
            "form": "two_world_completion",
            "completion_a": (
                "no reference row for the target entity; the executor resolves no pair"
            ),
            "completion_b": {
                "added_reference_amount": hidden,
                "pairs_multiplier": multiplicity,
                "executor_value": answer_b,
            },
            "differ": True,
        }
        phrasing_index = rng.randrange(len(PROMPTS[family]))
        tasks.append(
            {
                "task_id": f"q{index}",
                "capability": "L2_join_undetermined",
                "question": question,
                "instruction": render_instruction(family, question, phrasing_index),
                "phrasing_index": phrasing_index,
                "answer": UNKNOWN,
                "consumed": consumed,
                "consumed_count": len(consumed),
                "certificate": certificate,
            }
        )
        certificates[f"q{index}"] = certificate

    identity = _dump(
        [VERSION, seed, family, length_records, consumed_records, depth, n_variants]
    )
    return {
        "schema_version": VERSION,
        "world_id": VERSION + "-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
        "seed": seed,
        "family": family,
        "length_records": length_records,
        "consumed_records": consumed_records,
        "depth": depth,
        "n_variants": n_variants,
        "tasks": tasks,
        "context": context,
        "length_accounting": {
            "record_rows": length_records,
            "reference_rows": len(references),
            "rendered_rows": len(rendered),
            "consumed_rows_per_task": [task["consumed_count"] for task in tasks],
            "padding_rows": length_records
            - sum(task["consumed_count"] for task in tasks),
            "decoy_padding_rows": 0,
            "padding_is_exposure_not_semantic_scale": True,
        },
        "honesty": dict(HONESTY),
        "unanswerable_certificate": certificates,
        "lineage": {
            "unanswerable_construction": "reference row for the join entity withheld",
            "certification": "two legal completions yield different answers",
        },
    }


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Regenerate, re-pin the contract, and re-check the certification."""
    errors: list[str] = []
    checks: dict[str, Any] = {}
    undetermined = True
    certificate_ok = True
    try:
        expected = generate_world(
            bundle["seed"],
            bundle["family"],
            bundle["length_records"],
            bundle["consumed_records"],
            bundle["depth"],
            bundle["n_variants"],
        )
        checks["deterministic_reproduction"] = bundle == expected
        if bundle["honesty"] != HONESTY or any(
            bundle["honesty"][key] for key in HONESTY if key != "source_kind"
        ):
            errors.append("honesty labels must stay fail-closed")
        family = bundle["family"]
        if family not in PROTOCOLS:
            raise ValueError("unsupported family")
        if not bundle["tasks"]:
            errors.append("a world must carry at least one task")
        # The world is a records join_lookup world; the bank family is the
        # dispatch key. Both facts are pinned here.
        header, rows = records.parse_context(bundle["context"])
        if header.get("schema") != records.VERSION:
            errors.append("world schema does not match the records contract")
        if header.get("family") != "join_lookup":
            errors.append("world header must pin the join_lookup contract")
        if header.get("rules") != PROTOCOLS[family]:
            errors.append("rules text does not match the registered contract")
        if len(rows) != bundle["length_accounting"]["rendered_rows"]:
            errors.append("rendered row count mismatch")
        if bundle.get("unanswerable_certificate", {}) != {
            task["task_id"]: task.get("certificate") for task in bundle["tasks"]
        }:
            errors.append(
                "the bundle-level certificate mirror does not match the tasks"
            )
            certificate_ok = False
        for task in bundle["tasks"]:
            question = task["question"]
            if question.get("family") != family:
                errors.append("task family does not match the bundle family")
            if task["instruction"] != render_instruction(
                family, question, task["phrasing_index"]
            ):
                errors.append("instruction does not match the registered contract")
            if task["answer"] != UNKNOWN:
                errors.append("unanswerable task must answer UNKNOWN")
            if bundle["world_id"] in bundle["context"] + task["instruction"]:
                errors.append("world identity leaked into the visible text")
            consumed = sorted(task["consumed"])
            if len(consumed) != task["consumed_count"] or len(set(consumed)) != len(
                consumed
            ):
                errors.append("consumed provenance is not a distinct id set")
            if any(row_id not in {row.id for row in rows} for row_id in consumed):
                errors.append("consumed id missing from the rendered rows")
            if consumed != _join_domain(bundle["context"], question):
                errors.append("consumed provenance disagrees with the executor")
            if _resolve(bundle["context"], question) is not None:
                errors.append("visible world unexpectedly determines a value")
                undetermined = False
            certificate = task.get("certificate", {})
            if certificate.get("form") != "two_world_completion":
                errors.append("missing two-world completion certificate")
                certificate_ok = False
            completion_b = certificate.get("completion_b", {})
            added = completion_b.get("added_reference_amount")
            multiplier = completion_b.get("pairs_multiplier")
            if not isinstance(added, int) or not isinstance(multiplier, int):
                errors.append("completion B fields missing or malformed")
                certificate_ok = False
            elif completion_b.get("executor_value") != added * multiplier:
                errors.append("completion B executor value mismatch")
                certificate_ok = False
            if certificate.get("differ") is not True:
                errors.append("certificate must record that completions differ")
                certificate_ok = False
            checks["consumed:" + task["task_id"]] = len(consumed)
        accounting = bundle["length_accounting"]
        if accounting["record_rows"] != bundle["length_records"]:
            errors.append("length accounting mismatch")
        if accounting["consumed_rows_per_task"] != [
            task["consumed_count"] for task in bundle["tasks"]
        ]:
            errors.append("consumed row accounting mismatch")
        if accounting["padding_rows"] != bundle["length_records"] - sum(
            accounting["consumed_rows_per_task"]
        ):
            errors.append("padding accounting mismatch")
        if accounting["reference_rows"] != sum(
            1 for row in rows if row.type == "reference"
        ):
            errors.append("reference row accounting mismatch")
        if accounting["decoy_padding_rows"] != 0:
            errors.append("depth-1 worlds carry no decoy padding")
        checks["visible_world_undetermined"] = undetermined
        checks["certificate_consistent"] = certificate_ok
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"passed": not errors, "errors": errors, "checks": checks}
