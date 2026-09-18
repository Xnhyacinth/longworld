"""P69 record-world compiler, slice 1: typed query programs over simulated rows.

Gold is produced by a pure-Python typed executor (TableLong's spine, no SQL
engine and no dataframe anywhere): every task is a typed operation program
whose answer is executed, never authored, and whose provenance is the exact
set of row ids the program consumed.

(L, K, H) are independent knobs. L is the number of primary rows rendered in
the context; K is the number of rows the query program consumes; H is the
depth of the chained filter steps. The necessary rows are built first and the
remaining L - sum(K_v) rows are same-schema distractors: they are exposure and
interference, never evidence, and they are never reported inside `consumed`.

Honesty limits, all fail-closed: `strict_long_dependency_verified`,
`model_utility_measured` and `production_eligible` are false everywhere and
validate_bundle() rejects a bundle that claims otherwise; `source_kind` is
"simulated"; L - K padding is length exposure, not semantic scale; a
K-consistency check counts consumed rows, not difficulty.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

VERSION = "capability-records-v1"
BASE_DATE = date(2019, 1, 1)
WINDOW_DAYS = 40
GAP_DAYS = 20
SUBWINDOW_DAYS = 24
# Amount-cap offsets above each task's own floor. A depth-3 task narrows again
# to CAPS[3] // 2, so a depth-3 decoy that sits in (h + CAPS[3] // 2, h + CAPS[3]]
# passes the first two filter steps and fails the third: that is what makes H a
# chain of operations rather than one longer conjunction.
CAPS = {1: 900, 2: 700, 3: 700}
# Each world draws its own category vocabulary from this pool, and the drawn
# size ranges over the whole pool. That size is a shape lever, not decoration:
# the masked answer shape carries the `breakdown` width and the group sizes, so
# a single global enum would cap the corpus at a few dozen distinct shapes.
CATEGORY_POOL = (
    "aurora", "boreal", "cinder", "delta", "ember", "fjord", "garnet", "hollow",
    "indigo", "juniper", "kelvin", "lumen", "marrow", "nimbus", "onyx", "petrel",
    "quartz", "rimer", "solace", "tundra", "umber", "verdigris", "willow",
    "yarrow", "zephyr", "alcove", "bramble", "cobalt", "drift", "estuary",
    "fathom", "gable", "heath", "inlet", "jetty", "kestrel", "lantern", "marsh",
    "nadir", "orchard", "pallet", "quarry", "ridge", "sable", "thicket",
    "upland", "vellum", "wicker", "xenon", "yarrow-2", "zenith", "amber",
    "basalt", "cedar", "dune", "elm", "flint", "grove", "harbor", "iron",
    "jade", "knoll", "lagoon", "mesa", "nettle", "oakum", "prairie", "quill",
)
AGGREGATES = ("sum", "count", "max", "min")
JOIN_AGGREGATES = ("sum", "count")
VERDICTS = ("GT", "LT", "EQ")
FIELD_OPS = {
    "amount": ("==", "!=", ">=", "<=", ">", "<"),
    "date": ("==", "!=", ">=", "<=", ">", "<"),
    "category": ("==", "!="),
}
FAMILIES = ("filter_aggregate", "group_compare", "join_lookup")

# The visible rules text and the instruction phrasings are part of the task
# contract: the executor reads only the structured program, so a reworded
# rules line or instruction that contradicts the executor would still solve.
# validate_bundle() pins both to these constants and re-renders every
# instruction from (program, phrasing index), rejecting any deviation. The
# phrasing pool is deliberately wide (>= 20 per family, ACC's lesson: 6,396
# instructions for 10,770 rows) and rotated by seed.
PROTOCOLS: dict[str, str] = {
    "filter_aggregate": (
        "Each rendered row is a JSON object. Rows of type record are the table; "
        "reference rows are not. Apply the program's filter steps in order, "
        "keeping the rows that satisfy every condition of the step; a condition "
        "compares one of amount, date, category to a constant with ==, !=, >=, "
        "<=, > or <. Then report the aggregate over the surviving rows: sum and "
        "max and min use the amount field, count counts rows. matched lists the "
        "surviving row ids; breakdown counts the surviving rows per category. "
        "Rows that fail any filter step are unused. No rows are omitted."
    ),
    "group_compare": (
        "Each rendered row is a JSON object. Rows of type record are the table; "
        "reference rows are not. Apply the program's filter steps in order, "
        "keeping the rows that satisfy every condition of the step; a condition "
        "compares one of amount, date, category to a constant with ==, !=, >=, "
        "<=, > or <. Then group the surviving rows by category and aggregate "
        "within each group: sum uses the amount field, count counts rows. The "
        "program names exactly two groups; report their values, their row ids, "
        "and a verdict comparing the first named group against the second (GT "
        "when the first is larger, LT when smaller, EQ when equal). Surviving "
        "rows in other groups are unused. No rows are omitted."
    ),
    "join_lookup": (
        "Each rendered row is a JSON object. Rows of type record are the table; "
        "reference rows carry a per-entity amount. Apply the program's filter "
        "steps in order, keeping the rows that satisfy every condition of the "
        "step; a condition compares one of amount, date, category to a constant "
        "with ==, !=, >=, <=, > or <. Then join each surviving row to the "
        "reference row with the same entity, one pair per surviving row, and "
        "report the aggregate over the matched pairs: sum adds the reference "
        "amount of each pair, count counts pairs. by_entity totals the reference "
        "amount per matched entity. Rows without a reference row are unused. No "
        "rows are omitted."
    ),
}

PROMPTS: dict[str, tuple[str, ...]] = {
    "filter_aggregate": (
        "Execute the typed program below over the rendered records and return its result.",
        "Run the filter-and-aggregate program on the record table and report the answer.",
        "Evaluate this query program against the rows and give the resulting aggregate.",
        "Apply the stated filters to the table, aggregate the survivors, and answer.",
        "Carry out the following typed query over the records and return the outcome.",
        "Read the record table, run the program exactly as written, and report its result.",
        "Compute the requested aggregate after applying the program's filters in order.",
        "Process the rows with the program below and return the computed aggregate.",
        "Work through the program against the rendered rows and state the answer.",
        "Run this table program and return the aggregate, the matched ids and the breakdown.",
        "Solve the typed record query below and report the result as specified.",
        "Execute the program over the rendered table; report the aggregate and provenance.",
        "Follow the program's filter steps over the rows, then aggregate and answer.",
        "Apply the program to the record rows and give the aggregate it asks for.",
        "Interpret the query program, evaluate it on the table, and return the answer.",
        "Evaluate the filter-aggregate program on the rendered records and answer.",
        "Execute the stated operations over the table in order and report the result.",
        "Run the query program on the rows below and return the aggregate it produces.",
        "Apply each filter step in sequence over the records, then report the aggregate.",
        "Compute the program's result over the rendered record table.",
        "Execute the following program on the rows and report the aggregate with its ids.",
        "Evaluate this filter-and-aggregate program over the records and answer.",
        "Solve the record query by running the program and report the aggregate.",
        "Run the program over the table and give the aggregate, counts and provenance.",
    ),
    "group_compare": (
        "Execute the typed program below over the rendered records and return its result.",
        "Run the group-and-compare program on the record table and report the verdict.",
        "Evaluate this query program against the rows and compare the named groups.",
        "Apply the stated filters, group the survivors, aggregate, and give the verdict.",
        "Carry out the following typed query over the records and return the outcome.",
        "Read the record table, run the program exactly as written, and report its result.",
        "Compute the two named group aggregates after filtering, then compare them.",
        "Process the rows with the program below and return the group values and verdict.",
        "Work through the program against the rendered rows and state the answer.",
        "Run this table program and return the group values, their ids and the verdict.",
        "Solve the typed record query below and report the result as specified.",
        "Execute the program over the rendered table; report the comparison and provenance.",
        "Follow the program's filter steps, group by category, and answer with the verdict.",
        "Apply the program to the record rows and give the comparison it asks for.",
        "Interpret the query program, evaluate it on the table, and return the answer.",
        "Evaluate the group-compare program on the rendered records and answer.",
        "Execute the stated operations over the table in order and report the result.",
        "Run the query program on the rows below and return the group comparison.",
        "Apply each filter step in sequence, aggregate within the named groups, answer.",
        "Compute the program's result over the rendered record table.",
        "Execute the following program on the rows and report the verdict with its ids.",
        "Evaluate this group-and-compare program over the records and answer.",
        "Solve the record query by running the program and report the group verdict.",
        "Run the program over the table and give the comparison and its provenance.",
    ),
    "join_lookup": (
        "Execute the typed program below over the rendered records and return its result.",
        "Run the join-and-aggregate program on the record table and report the answer.",
        "Evaluate this query program against the rows and join them to the references.",
        "Apply the stated filters, join the survivors on entity, aggregate, and answer.",
        "Carry out the following typed query over the records and return the outcome.",
        "Read the record table, run the program exactly as written, and report its result.",
        "Compute the requested aggregate over the joined pairs after filtering.",
        "Process the rows with the program below and return the joined aggregate.",
        "Work through the program against the rendered rows and state the answer.",
        "Run this table program and return the matched pairs, totals and aggregate.",
        "Solve the typed record query below and report the result as specified.",
        "Execute the program over the rendered table; report the join result and provenance.",
        "Follow the program's filter steps, join on entity, and answer with the aggregate.",
        "Apply the program to the record rows and give the join result it asks for.",
        "Interpret the query program, evaluate it on the table, and return the answer.",
        "Evaluate the join-lookup program on the rendered records and answer.",
        "Execute the stated operations over the table in order and report the result.",
        "Run the query program on the rows below and return the joined aggregate.",
        "Apply each filter step in sequence, join the survivors, and report the total.",
        "Compute the program's result over the rendered record table.",
        "Execute the following program on the rows and report the aggregate with its pairs.",
        "Evaluate this join-and-aggregate program over the records and answer.",
        "Solve the record query by running the program and report the joined total.",
        "Run the program over the table and give the aggregate and its matched pairs.",
    ),
}

RETURNS: dict[str, str] = {
    "filter_aggregate": (
        "Return one JSON object with keys aggregate (how, value), matched "
        "(surviving row ids) and breakdown (category to count)."
    ),
    "group_compare": (
        "Return one JSON object with keys groups (category to value), left and "
        "right (each group, value, records) and verdict (GT, LT or EQ)."
    ),
    "join_lookup": (
        "Return one JSON object with keys pairs (record id, reference id), "
        "by_entity (entity to total) and aggregate (how, value)."
    ),
}

FIELDS: dict[str, str] = {
    family: "record rows carry id, entity, category, amount, date, memo"
    + ("; reference rows carry id, entity, amount, memo."
       if family == "join_lookup" else ".")
    for family in FAMILIES
}

HONESTY: dict[str, Any] = {
    "source_kind": "simulated",
    "strict_long_dependency_verified": False,
    "model_utility_measured": False,
    "production_eligible": False,
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fresh_entity(rng: random.Random, used: set[str]) -> str:
    """A distractor row's entity, drawn from the world's unused names.

    Row entities are only joined through reference rows, so an unreferenced name
    is free; the world-wide registry exists so no two reference rows can ever
    share one, which the join rejects as ambiguous.
    """
    entity = f"unit-{rng.getrandbits(20):05x}"
    while entity in used:
        entity = f"unit-{rng.getrandbits(20):05x}"
    used.add(entity)
    return entity


def _new_id(prefix: str, rng: random.Random, width: int = 24) -> str:
    """One row identity at the world's own hex width.

    The width is a per-world presentation knob (id widths differ between
    systems). It also multiplies the rendered shape space, because every
    id-bearing part of an answer -- matched lists, group records, joined pairs --
    renders at that width; a single global width would pin those shapes.
    """
    return prefix + format(rng.getrandbits(4 * width), f"0{width}x")


@dataclass(frozen=True)
class Row:
    id: str
    type: str
    entity: str
    amount: int
    memo: str
    category: str | None = None
    day: str | None = None

    def visible(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "entity": self.entity,
            "amount": self.amount,
            "memo": self.memo,
        }
        if self.category is not None:
            row["category"] = self.category
        if self.day is not None:
            row["date"] = self.day
        return row


def render_context(rows: list[Row], family: str) -> str:
    """One header line pinning schema and rules, then one JSON object per row."""
    header = {
        "schema": VERSION,
        "family": family,
        "rules": PROTOCOLS[family],
    }
    return "\n".join([_dump(header)] + [_dump(row.visible()) for row in rows])


def parse_context(context: str) -> tuple[dict[str, Any], list[Row]]:
    lines = context.splitlines()
    if not lines:
        raise ValueError("empty context")
    header = json.loads(lines[0])
    rows = []
    for line in lines[1:]:
        item = json.loads(line)
        rows.append(
            Row(
                id=item["id"],
                type=item["type"],
                entity=item["entity"],
                amount=item["amount"],
                memo=item["memo"],
                category=item.get("category"),
                day=item.get("date"),
            )
        )
    return header, rows


def _holds(row: Row, condition: dict[str, Any]) -> bool:
    field, op, target = condition["field"], condition["op"], condition["value"]
    if field not in FIELD_OPS or op not in FIELD_OPS[field]:
        raise ValueError("unsupported predicate")
    value = {"amount": row.amount, "date": row.day, "category": row.category}[field]
    if field == "amount":
        if type(target) is not int:
            raise ValueError("amount predicate must be an integer")
    elif not isinstance(target, str):
        raise ValueError("text predicate must be a string")
    elif value is None:
        raise ValueError("predicate field missing on row")
    if op == "==":
        return value == target
    if op == "!=":
        return value != target
    if op == ">=":
        return value >= target
    if op == "<=":
        return value <= target
    if op == ">":
        return value > target
    return value < target


def _filter_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for step in steps:
        if step.get("op") != "filter":
            raise ValueError("only filter steps may precede the terminal operation")
        conditions = step.get("conditions")
        if not isinstance(conditions, list) or not 2 <= len(conditions) <= 3:
            raise ValueError("a filter step needs 2 or 3 conditions")
        fields = {condition.get("field") for condition in conditions}
        if not 2 <= len(fields) <= 3:
            raise ValueError("a filter predicate must span 2 or 3 fields")
    return steps


def _aggregate_value(rows: list[Row], how: str) -> int:
    if how not in AGGREGATES:
        raise ValueError("unsupported aggregate")
    if not rows:
        raise ValueError("aggregate over an empty relation")
    amounts = [row.amount for row in rows]
    if how == "sum":
        return sum(amounts)
    if how == "count":
        return len(amounts)
    if how == "max":
        return max(amounts)
    return min(amounts)


def _terminal(table: list[Row], references: list[Row], step: dict[str, Any]) -> Any:
    op = step.get("op")
    how = step.get("how")
    if op == "aggregate":
        if how not in AGGREGATES:
            raise ValueError("unsupported aggregate")
        breakdown: dict[str, int] = {}
        for row in table:
            breakdown[row.category] = breakdown.get(row.category, 0) + 1
        return {
            "aggregate": {"how": how, "value": _aggregate_value(table, how)},
            "matched": sorted(row.id for row in table),
            "breakdown": breakdown,
        }
    if op == "group_compare":
        groups = step.get("groups")
        if not isinstance(groups, list) or len(groups) != 2 or len(set(groups)) != 2:
            raise ValueError("group_compare needs two distinct groups")
        if how not in ("sum", "count"):
            raise ValueError("unsupported group aggregate")
        buckets: dict[str, list[Row]] = {name: [] for name in groups}
        for row in table:
            if row.category in buckets:
                buckets[row.category].append(row)
        if any(not buckets[name] for name in groups):
            raise ValueError("empty comparison group")
        values = {
            name: _aggregate_value(buckets[name], how) for name in groups
        }
        left, right = groups
        verdict = (
            "GT" if values[left] > values[right]
            else "LT" if values[left] < values[right]
            else "EQ"
        )
        return {
            "groups": values,
            "left": {
                "group": left,
                "value": values[left],
                "records": sorted(row.id for row in buckets[left]),
            },
            "right": {
                "group": right,
                "value": values[right],
                "records": sorted(row.id for row in buckets[right]),
            },
            "verdict": verdict,
        }
    if op == "join_aggregate":
        if how not in JOIN_AGGREGATES:
            raise ValueError("unsupported join aggregate")
        by_entity: dict[str, Row] = {}
        for reference in references:
            if reference.entity in by_entity:
                raise ValueError("duplicate reference row for one entity")
            by_entity[reference.entity] = reference
        pairs, totals, value = [], {}, 0
        for row in sorted(table, key=lambda item: item.id):
            reference = by_entity.get(row.entity)
            if reference is None:
                continue
            pairs.append([row.id, reference.id])
            totals[reference.entity] = (
                totals.get(reference.entity, 0) + reference.amount
            )
            value += reference.amount
        if not pairs:
            raise ValueError("join consumed no rows")
        if how == "count":
            value = len(pairs)
        return {
            "pairs": pairs,
            "by_entity": totals,
            "aggregate": {"how": how, "value": value},
        }
    raise ValueError("unsupported terminal operation")


def solve_visible(context: str, question: dict[str, Any]) -> Any:
    """Independent executor over visible rows only; never reads gold or hidden state."""
    try:
        header, rows = parse_context(context)
        family = header.get("family")
        if (
            header.get("schema") != VERSION
            or family not in PROTOCOLS
            or header.get("rules") != PROTOCOLS[family]
        ):
            raise ValueError("unsupported or missing visible contract")
        if question.get("family") != family:
            raise ValueError("program family does not match the visible contract")
        steps = question.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
            raise ValueError("invalid program chain")
        table = [row for row in rows if row.type == "record"]
        for row in rows:
            if row.type not in ("record", "reference"):
                raise ValueError("unknown row type")
        for step in _filter_steps(steps[:-1]):
            table = [
                row
                for row in table
                if all(_holds(row, condition) for condition in step["conditions"])
            ]
        return _terminal(
            table, [row for row in rows if row.type == "reference"], steps[-1]
        )
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("incomplete or malformed visible records/program") from exc


def describe_program(program: dict[str, Any]) -> str:
    parts = []
    for step in program["steps"]:
        if step["op"] == "filter":
            joined = " and ".join(
                f"{c['field']} {c['op']} {c['value']}" for c in step["conditions"]
            )
            parts.append(f"keep rows where {joined}")
        elif step["op"] == "aggregate":
            parts.append(f"report {step['how']} of amount over the survivors")
        elif step["op"] == "group_compare":
            first, second = step["groups"]
            parts.append(
                f"group the survivors by category, report {step['how']} of amount "
                f"per group, and compare group {first} against group {second}"
            )
        elif step["op"] == "join_aggregate":
            parts.append(
                f"join the survivors to reference rows on entity and report "
                f"{step['how']} of the reference amount"
            )
        else:
            raise ValueError("unknown program step")
    return "; then ".join(parts)


def render_instruction(
    family: str, program: dict[str, Any], phrasing_index: int
) -> str:
    prompts = PROMPTS[family]
    if not 0 <= phrasing_index < len(prompts):
        raise ValueError("invalid phrasing index")
    return (
        f"Task: {prompts[phrasing_index]}. "
        f"Fields: {FIELDS[family]} "
        f"Program: {describe_program(program)}. "
        f"{RETURNS[family]}"
    )


def _split_sizes(rng: random.Random, consumed: int, n_variants: int) -> list[int]:
    """K is sampled per task over a wide band around the cell value.

    K is a sampled knob (iGSM samples its op the same way), and the width of the
    draw is the single largest lever on answer-shape diversity: the rendered
    shape carries the consumed-row count, so a narrow draw around the cell value
    pins that dimension to a handful of values. The band is a third of the cell
    value on each side, floored at 4.
    """
    spread = max(2, consumed // 3)
    return [
        max(4, consumed + rng.randrange(-spread, spread + 1))
        for _ in range(n_variants)
    ]


def plan_variants(length_records: int, consumed: int, requested: int) -> int:
    """How many K-sized tasks fit in L while leaving real distractor room."""
    room = length_records - max(8, length_records // 10)
    return max(1, min(requested, room // max(4, (consumed * 6) // 5)))


def generate_world(
    seed: int,
    family: str = "filter_aggregate",
    length_records: int = 800,
    consumed_records: int = 60,
    depth: int = 2,
    n_variants: int = 4,
) -> dict[str, Any]:
    """Build one record world: L rows, one typed program per variant, executed gold."""
    if family not in PROTOCOLS:
        raise ValueError("unsupported family")
    if type(length_records) is not int or length_records < 32:
        raise ValueError("length_records must be an integer >= 32")
    if type(consumed_records) is not int or consumed_records < 4:
        raise ValueError("consumed_records must be an integer >= 4")
    if type(depth) is not int or not 1 <= depth <= 3:
        raise ValueError("depth must be 1, 2 or 3")
    if type(n_variants) is not int or not 1 <= n_variants <= 8:
        raise ValueError("n_variants must be 1..8")
    if n_variants != plan_variants(length_records, consumed_records, n_variants):
        raise ValueError("K budget does not fit inside L; shrink K or the variant count")
    rng = random.Random(seed)
    sizes = _split_sizes(rng, consumed_records, n_variants)
    if family == "group_compare":
        sizes = [size + size % 2 for size in sizes]
    # A world's own category vocabulary: 4 to 24 names drawn from the pool, so
    # the alphabet (and therefore the rendered width of category-bearing
    # answers) varies per world rather than being one global enum.
    # The vocabulary size is drawn over the whole pool (bounded below so a
    # group_compare world keeps two named groups plus an excluded third).
    categories = rng.sample(
        CATEGORY_POOL, rng.randint(4, min(len(CATEGORY_POOL), max(4, consumed_records)))
    )
    # One id width for this world's rows and references (see _new_id).
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    windows, subwindows, floors, excluded = [], [], [], []
    for index in range(n_variants):
        start = BASE_DATE + timedelta(days=index * (WINDOW_DAYS + GAP_DAYS))
        windows.append(
            (
                start.isoformat(),
                (start + timedelta(days=WINDOW_DAYS)).isoformat(),
            )
        )
        subwindows.append(
            (
                start.isoformat(),
                (start + timedelta(days=SUBWINDOW_DAYS)).isoformat(),
            )
        )
        floors.append(rng.randint(120, 880))
        excluded.append(rng.choice(categories))
    # Shuffle the amount floor away from the day-window order before deriving
    # the band: a task's amount ceiling must stay a function of its own floor.
    rng.shuffle(floors)
    rng.shuffle(excluded)
    # The tightest amount ceiling of the task's chain. depth 3 narrows again
    # inside its own inner date sub-window, which is what lets a decoy pass
    # steps 1-2 and die on step 3.
    step_cap = CAPS[depth] if depth < 3 else CAPS[3] // 2
    join = family == "join_lookup"

    def record(day: str, amount: int, category: str, entity: str) -> Row:
        return Row(
            id=_new_id("r", rng, id_width),
            type="record",
            entity=entity,
            amount=amount,
            memo=_new_id("note-", rng, memo_width),
            category=category,
            day=day,
        )

    def reference(entity: str) -> Row:
        return Row(
            id=_new_id("k", rng, id_width),
            type="reference",
            entity=entity,
            amount=rng.randint(2, 40),
            memo=_new_id("note-", rng, memo_width),
        )

    rows: list[Row] = []
    references: list[Row] = []
    used_entities: set[str] = set()
    consumed: list[list[str]] = []
    groups: list[tuple[str, str, str]] = []
    for index, size in enumerate(sizes):
        # Every task's necessary rows sit inside its own date window -- and, at
        # depth 3, inside its own inner sub-window -- and inside its own amount
        # band, because a necessary row that fails any step of its own program
        # would not be consumed at all. The distractor budget is what remains.
        start, stop = subwindows[index] if depth == 3 else windows[index]
        span = (date.fromisoformat(stop) - date.fromisoformat(start)).days
        # Entity cardinality is drawn independently of K, so the join result's
        # pair count and its distinct-entity count vary separately instead of
        # being locked to one ratio. The draw is world-wide, not per task: two
        # tasks drawing disjoint entity sets keeps every reference unique across
        # the whole rendered context.
        peers = rng.randint(max(1, size // 4), size)
        entities = []
        while len(entities) < peers:
            entity = f"unit-{rng.getrandbits(20):05x}"
            if entity not in used_entities:
                used_entities.add(entity)
                entities.append(entity)
        band = floors[index]
        how = "sum" if (seed + index) % 2 else "count"
        target = rng.choice(VERDICTS)
        if family == "group_compare":
            pair = rng.sample(categories, 2)
            # A comparison group must survive its own program: excluding a
            # category that is one of the two named groups empties it.
            excluded[index] = rng.choice(
                [name for name in categories if name not in pair]
            )
            # The verdict is sampled uniformly and then *built*: count mode
            # carries it in the group sizes, sum mode in disjoint amount bands
            # (first_size == second_size there, so a shared band cannot make the
            # verdict depend on which group drew a larger value).
            if how == "count":
                shift = {"GT": rng.randint(1, 3), "LT": -rng.randint(1, 3), "EQ": 0}[
                    target
                ]
                first_size = min(max(2, size // 2 + shift), size - 2)
                second_size = size - first_size
                widest = max(first_size, second_size)
                if step_cap + 1 < widest:
                    raise ValueError("amount band too narrow for the group sizes")
                pool = rng.sample(range(band, band + step_cap + 1), widest)
                amounts = pool[:first_size] + pool[:second_size]
            else:
                # Unequal group sizes as well as unequal values: the two rungs
                # are otherwise pinned to one ratio and the group cardinalities
                # collapse to a single pair.
                shift = {"GT": rng.randint(1, 3), "LT": -rng.randint(1, 3), "EQ": 0}[
                    target
                ]
                first_size = min(max(2, size // 2 + shift), size - 2)
                second_size = size - first_size
                middle = band + step_cap // 2
                if min(step_cap // 2, step_cap - step_cap // 2) < max(
                    first_size, second_size
                ):
                    raise ValueError("amount band too narrow for the group sizes")
                lower = rng.sample(range(band, middle), first_size)
                upper = rng.sample(range(middle, band + step_cap + 1), second_size)
                amounts = upper + lower if target == "GT" else lower + upper
                if target == "EQ":
                    # Equal values require equal cardinalities (a shared pool).
                    amounts = lower[:second_size] + list(lower[:first_size])
            row_categories = [pair[0]] * first_size + [pair[1]] * second_size
            groups.append((pair[0], pair[1], how))
        else:
            groups.append(("", "", how))
            amounts = rng.sample(range(band, band + step_cap + 1), size)
            row_categories = [
                rng.choice([name for name in categories if name != excluded[index]])
                for _ in range(size)
            ]
        ids = []
        for offset in range(size):
            day = (
                date.fromisoformat(start) + timedelta(days=rng.randrange(span))
            ).isoformat()
            row = record(
                day, amounts[offset], row_categories[offset], entities[offset % peers]
            )
            rows.append(row)
            ids.append(row.id)
        consumed.append(sorted(ids))
        if join:
            # `entities` is distinct by construction above, and it must be used
            # as a list, not a set: iterating a set of strings follows hash
            # order, which Python randomizes per process, and that would make
            # the generated world differ between worker processes.
            references.extend(reference(entity) for entity in entities)
    padding = length_records - sum(sizes)
    if padding < 1:
        raise ValueError("no distractor budget left; shrink K or the variant count")
    # Decoy rows pass the first filter step and fail a deeper one, so H is a real
    # operation chain rather than a longer conjunction in disguise. Only the
    # amount cap and the excluded category may admit them: admitting one inside
    # every necessary step would silently make it evidence.
    decoys = 0 if depth == 1 else max(1, padding // 4)
    for index in range(padding):
        owner = index % n_variants
        start, stop = windows[owner]
        span = (date.fromisoformat(stop) - date.fromisoformat(start)).days
        if index < decoys:
            # A depth-2 decoy sits inside the task window with the task's own
            # excluded category (passes step 1, dies on step 2); a depth-3 decoy
            # sits inside the window but outside the inner sub-window with an
            # allowed category (passes steps 1-2, dies on step 3).
            low, high = date.fromisoformat(start), date.fromisoformat(stop)
            if depth == 3:
                # Strictly after the inner sub-window: step 3's date bound is
                # inclusive, so a decoy on the edge would become evidence.
                low = max(low, date.fromisoformat(subwindows[owner][1])) + timedelta(
                    days=1
                )
            day = (low + timedelta(days=rng.randrange((high - low).days))).isoformat()
            category = (
                rng.choice([name for name in categories if name != excluded[owner]])
                if depth == 3
                else excluded[owner]
            )
            amount = floors[owner] + rng.randint(0, step_cap)
        else:
            style = index % 3
            if style == 0:
                day = (date.fromisoformat(stop) + timedelta(days=1)).isoformat()
            elif style == 1:
                day = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
            else:
                day = (
                    BASE_DATE
                    + timedelta(days=n_variants * (WINDOW_DAYS + GAP_DAYS) + index)
                ).isoformat()
            amount = rng.randint(1, max(2, floors[owner] - 1))
            if style == 0:
                # Plausible-looking magnitude, but outside every task window.
                amount = floors[owner] + rng.randint(0, step_cap)
            category = rng.choice(categories)
        rows.append(record(day, amount, category, _fresh_entity(rng, used_entities)))
    if join:
        for _ in range(max(1, sum(sizes) // 4)):
            entity = f"unit-{rng.getrandbits(20):05x}"
            while entity in used_entities:
                entity = f"unit-{rng.getrandbits(20):05x}"
            # Register the drawn entity before the next draw: without this, two
            # decoy references can collide and the join finds a duplicate.
            used_entities.add(entity)
            references.append(reference(entity))
    rng.shuffle(rows)
    rendered = rows + references
    rng.shuffle(rendered)
    context = render_context(rendered, family)
    tasks = []
    for index, size in enumerate(sizes):
        first, second, how = groups[index]
        cap = floors[index] + step_cap
        steps: list[dict[str, Any]] = [
            {
                "op": "filter",
                "conditions": [
                    {"field": "date", "op": ">=", "value": windows[index][0]},
                    {"field": "date", "op": "<=", "value": windows[index][1]},
                    {"field": "amount", "op": ">=", "value": floors[index]},
                ],
            }
        ]
        if depth >= 2:
            steps.append(
                {
                    "op": "filter",
                    "conditions": [
                        {"field": "amount", "op": "<=", "value": cap},
                        {"field": "category", "op": "!=", "value": excluded[index]},
                    ],
                }
            )
        if depth >= 3:
            steps.append(
                {
                    "op": "filter",
                    "conditions": [
                        {
                            "field": "amount",
                            "op": "<=",
                            "value": floors[index] + CAPS[3] // 2,
                        },
                        {"field": "date", "op": ">=", "value": subwindows[index][0]},
                        {"field": "date", "op": "<=", "value": subwindows[index][1]},
                    ],
                }
            )
        if family == "filter_aggregate":
            steps.append(
                {
                    "op": "aggregate",
                    "how": AGGREGATES[(seed + index) % len(AGGREGATES)],
                }
            )
        elif family == "group_compare":
            steps.append({"op": "group_compare", "groups": [first, second], "how": how})
        else:
            steps.append(
                {
                    "op": "join_aggregate",
                    "how": JOIN_AGGREGATES[(seed + index) % len(JOIN_AGGREGATES)],
                }
            )
        program = {"family": family, "steps": steps}
        # Provenance is computed by replaying the same executor step by step,
        # not declared by the generator: `consumed` is whatever relation the
        # final step actually reads. A generation-time bookkeeping error then
        # shows up as a provenance mismatch instead of silently overstating the
        # necessary set.
        generated = consumed[index]
        actual = _executor_provenance(rendered, program)
        if sorted(actual) != sorted(generated):
            raise ValueError(
                "generator provenance disagrees with the executor: "
                f"extra={sorted(set(generated) - set(actual))[:2]} "
                f"missing={sorted(set(actual) - set(generated))[:2]} "
                f"task={program['steps']}"
            )
        phrasing_index = rng.randrange(len(PROMPTS[family]))
        tasks.append(
            {
                "task_id": f"q{index}",
                "capability": family,
                "question": program,
                "instruction": render_instruction(family, program, phrasing_index),
                "phrasing_index": phrasing_index,
                "answer": solve_visible(context, program),
                "consumed": sorted(actual),
                "consumed_count": len(actual),
            }
        )
    identity = _dump([VERSION, seed, family, length_records, consumed_records, depth, n_variants])
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
            "padding_rows": length_records - sum(task["consumed_count"] for task in tasks),
            "decoy_padding_rows": decoys,
            "padding_is_exposure_not_semantic_scale": True,
        },
        "honesty": dict(HONESTY),
    }


def _executor_provenance(rendered: list[Row], program: dict[str, Any]) -> list[str]:
    """Row ids the program's terminal step actually reads (executor-derived)."""
    table = [row for row in rendered if row.type == "record"]
    for step in _filter_steps(program["steps"][:-1]):
        table = [
            row
            for row in table
            if all(_holds(row, condition) for condition in step["conditions"])
        ]
    terminal = program["steps"][-1]
    if terminal["op"] == "group_compare":
        groups = set(terminal["groups"])
        return sorted(row.id for row in table if row.category in groups)
    if terminal["op"] == "join_aggregate":
        known = {row.entity for row in rendered if row.type == "reference"}
        return sorted(row.id for row in table if row.entity in known)
    return sorted(row.id for row in table)


def answer_value(answer: dict[str, Any]) -> Any:
    """The computed-value projection of an answer: the aggregate scalars only.

    `matched`, `breakdown`, `records` and `pairs` are id lists or counts over the
    retained set, so they change on *any* removal and would make the remove-one
    intervention vacuous. Necessity is therefore checked on the scalar the
    program computes: aggregate.value for filter/join, the two group values plus
    the verdict for group_compare.
    """
    if "aggregate" in answer:
        return answer["aggregate"]
    return {"groups": answer["groups"], "verdict": answer["verdict"]}


def _remove_line(context: str, line: str) -> str:
    reduced = context.replace(line + "\n", "", 1)
    if reduced == context:
        reduced = context.replace("\n" + line, "", 1)
    if reduced == context:
        raise ValueError("target row line is not present in the context")
    return reduced


def window_ablation(bundle: dict[str, Any], probes: int = 24) -> dict[str, Any]:
    """Cheap 1/2-context window gate: is the answer reachable from a half window?

    This is an approximation of the repo's 4k/8k/16k window gates on the
    rendered text, not a token-level proof; the header row that pins the
    contract is one line of the context and is itself part of any window.
    """
    lines = bundle["context"].splitlines()
    half = max(1, len(lines) // 2)
    starts = sorted({round(i * (len(lines) - half) / max(1, probes - 1)) for i in range(probes)})
    attempted = matching = 0
    for start in starts:
        chunk = "\n".join(lines[start : start + half])
        for task in bundle["tasks"]:
            attempted += 1
            try:
                if solve_visible(chunk, task["question"]) == task["answer"]:
                    matching += 1
            except ValueError:
                continue
    return {
        "windows_attempted": attempted,
        "windows_matching_gold": matching,
        "derivable_fraction": round(matching / attempted, 4) if attempted else 0.0,
        "definition": "contiguous half-context raw-line windows, contract pinned",
    }


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Re-execute gold, re-pin the contract, and run the interventions."""
    errors: list[str] = []
    checks: dict[str, Any] = {}
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
        header, rows = parse_context(bundle["context"])
        if header.get("rules") != PROTOCOLS[bundle["family"]]:
            errors.append("rules text does not match the registered contract")
        if header.get("family") != bundle["family"]:
            errors.append("header family mismatch")
        if len(rows) != bundle["length_accounting"]["rendered_rows"]:
            errors.append("rendered row count mismatch")
        by_id = {row.id: row for row in rows}
        lines = {row.id: _dump(row.visible()) for row in rows}
        necessary_total = 0
        for task in bundle["tasks"]:
            if task["instruction"] != render_instruction(
                bundle["family"], task["question"], task["phrasing_index"]
            ):
                errors.append("instruction does not match the registered contract")
            if bundle["world_id"] in bundle["context"] + task["instruction"]:
                errors.append("world identity leaked into the visible text")
            if solve_visible(bundle["context"], task["question"]) != task["answer"]:
                errors.append("answer is not reproducible by the visible executor")
            consumed = sorted(task["consumed"])
            if len(consumed) != task["consumed_count"] or len(set(consumed)) != len(consumed):
                errors.append("consumed provenance is not a distinct id set")
            if any(row_id not in by_id for row_id in consumed):
                errors.append("consumed id missing from the rendered rows")
            # Provenance is the executor's own reading of the program, re-derived
            # here from the rendered rows; a declared list that disagrees is
            # rejected rather than trusted, because the remove-one check below
            # would otherwise test the wrong set.
            if consumed != _executor_provenance(rows, task["question"]):
                errors.append("consumed provenance disagrees with the executor")
            # Remove-one on the rendered line: every necessary row must move the
            # answer, and no distractor may move it. For sum, count and every
            # group/join aggregate each consumed row contributes, so sensitivity
            # is per row. min and max are the exception and are checked exactly
            # rather than weakened: removing a consumed row changes the answer
            # iff that row is tied at the extremal value, so the required count
            # is the number of tied rows, not K.
            gold = answer_value(task["answer"])
            shifted = 0
            for row_id in consumed:
                reduced = _remove_line(bundle["context"], lines[row_id])
                try:
                    changed = answer_value(
                        solve_visible(reduced, task["question"])
                    ) != gold
                except ValueError:
                    changed = True
                shifted += changed
            how = task["question"]["steps"][-1].get("how")
            if how in ("min", "max"):
                extremal = task["answer"]["aggregate"]["value"]
                ties = sum(by_id[row_id].amount == extremal for row_id in consumed)
                if ties != 1:
                    errors.append("extremum is not unique; necessity is undefined")
                required = 1
                mode = "unique_extremum"
            else:
                required = len(consumed)
                mode = "per_row"
            checks["necessity_mode:" + task["task_id"]] = mode
            checks["necessary_rows_removed:" + task["task_id"]] = shifted
            checks["consumed:" + task["task_id"]] = len(consumed)
            necessary_total += len(consumed)
            probe = [
                row.id
                for row in rows
                if row.id not in set(consumed) and row.type == "record"
            ]
            stable = 0
            for row_id in probe[:16]:
                reduced = _remove_line(bundle["context"], lines[row_id])
                stable += answer_value(solve_visible(reduced, task["question"])) == gold
            checks["distractor_rows_removed:" + task["task_id"]] = stable
            if stable != len(probe[:16]):
                errors.append("a distractor row changed the answer")
            if shifted != required:
                errors.append(
                    "remove-one necessity failed: "
                    f"{shifted} sensitive rows, expected {required} ({mode})"
                )
        accounting = bundle["length_accounting"]
        if accounting["record_rows"] != bundle["length_records"]:
            errors.append("length accounting mismatch")
        if sum(accounting["consumed_rows_per_task"]) != necessary_total:
            errors.append("consumed row accounting mismatch")
        if len(
            {row_id for task in bundle["tasks"] for row_id in task["consumed"]}
        ) == 0:
            errors.append("no consumed rows at all")
        checks["window_ablation"] = window_ablation(bundle)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"passed": not errors, "errors": errors, "checks": checks}
