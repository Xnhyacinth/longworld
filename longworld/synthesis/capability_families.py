"""P70 capability families: alias binding (L1), as-of state (L3), rule holdout (L4), set completeness (L2).

Four record-world families on the P69 (L, K, H) compiler spine. Each owns its
visible contract, its own pure-Python executor and its own interventions:

* ``alias_locate`` (L1, binding/location): record rows plus alias declarations
  rendered as extra rows. The program binds an alias handle to the entity its
  declaration names and returns the matching row ids. The handle -> entity map
  is re-drawn per world and every task carries a same-shape twin entity, so the
  binding -- not the category, the amount or the date -- is the discriminator,
  and swapping two entities' alias declarations flips the answer.
* ``asof_state`` (L3, time-separated state): every event carries an event date
  and a reveal date. An AS OF answer keeps the events whose reveal is at or
  before the query date and folds them in *event-date* order under a state
  machine whose ``release`` is capped by what is held at that moment, so the
  fold order is load-bearing rather than a commutative sum. Moving one reveal
  date across the query date flips the answer.
* ``rule_holdout`` (L4, structured holdout): two *structural* rule families,
  ``threshold_class`` and ``parity_vote``, whose demonstrations identify the
  rule uniquely by brute-force enumeration over the hypothesis space. The
  runner-facing split is by rule structure (``holdout_plan``), so an eval bank
  holds one structure out while training on the other.
* ``set_complete`` (L2, exhaustive set membership): one scope step declares
  the conditions every member satisfies, and the answer is the *complete* set
  -- a missing member is a wrong answer and so is an extra row, which is a
  property of the data (the gold is the full set) rather than of a grader.
  Same-schema sibling rows fail at least one condition and are exposure,
  never members. Inserting a legal hit into a region no member covers must
  enter the answer (``insert_hit``). An H=1 family: only depth 1 is
  generated, and the generator rejects a deeper one.

(L, K, H) are independent knobs, as in capability_records: L is the number of
primary rows rendered in the context, K is the number of rows the program
consumes, H is the number of chained pre-terminal operations (1 or 2). Binding
and demonstration rows (alias declarations, demonstrations) are rendered extras
outside L, exactly as join_lookup's reference rows are in capability_records.
The necessary rows are built first and the remaining primary rows are
same-schema distractors: exposure and interference, never evidence.

Honesty limits, all fail-closed: ``strict_long_dependency_verified``,
``model_utility_measured`` and ``production_eligible`` are false everywhere and
validate_bundle() rejects a bundle that claims otherwise; ``source_kind`` is
"simulated"; L - K padding is length exposure, not semantic scale. Per-row
remove-one necessity is *measured*, not proved: the bundle carries a declared
two-way sensitivity partition over the consumed rows and validate_bundle()
re-derives it by re-execution over the parsed rows, so a hand-edited partition
or a hidden non-decisive consumed row is rejected, but the measurement is an
execution result and not a proof about a reader. Demonstrations are jointly
necessary rather than individually necessary -- a single demonstration removed
from a minimal identifying set leaves the true rule consistent -- so their
necessity is checked as the joint removal of every demonstration row.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

VERSION = "capability-families-v1"
BASE_DATE = date(2019, 1, 1)
WINDOW_DAYS = 40
GAP_DAYS = 20
AMOUNT_CAP = 700
FAMILIES = ("alias_locate", "asof_state", "rule_holdout", "set_complete")
RULE_FAMILIES = ("threshold_class", "parity_vote")
PRIMARY_TYPES = ("record", "event")
# Each world draws its own vocabulary (as in capability_records): the drawn size
# is a shape lever, because the rendered width of a category-, alias- or
# label-bearing answer carries the drawn alphabet.
CATEGORY_POOL = (
    "aurora",
    "boreal",
    "cinder",
    "delta",
    "ember",
    "fjord",
    "garnet",
    "hollow",
    "indigo",
    "juniper",
    "kelvin",
    "lumen",
    "marrow",
    "nimbus",
    "onyx",
    "petrel",
    "quartz",
    "rimer",
    "solace",
    "tundra",
    "umber",
    "verdigris",
    "willow",
    "yarrow",
    "zephyr",
    "alcove",
    "bramble",
    "cobalt",
    "drift",
    "estuary",
    "fathom",
    "gable",
    "heath",
    "inlet",
    "jetty",
    "kestrel",
    "lantern",
    "marsh",
    "nadir",
    "orchard",
    "pallet",
    "quarry",
    "ridge",
    "sable",
    "thicket",
    "upland",
    "vellum",
    "wicker",
    "xenon",
    "zenith",
    "amber",
    "basalt",
    "cedar",
    "dune",
    "elm",
    "flint",
    "grove",
    "harbor",
    "iron",
    "jade",
    "knoll",
    "lagoon",
    "mesa",
    "nettle",
    "oakum",
    "prairie",
    "quill",
)
ALIAS_POOL = (
    "accord",
    "anchor",
    "arbour",
    "atlas",
    "basin",
    "beacon",
    "belfry",
    "birch",
    "bramble",
    "breeze",
    "bronze",
    "cadence",
    "cairn",
    "canopy",
    "cedar",
    "chalet",
    "cinder",
    "cobalt",
    "compass",
    "coppice",
    "cornice",
    "crescent",
    "crystal",
    "current",
    "cypress",
    "dahlia",
    "delta",
    "dune",
    "echo",
    "ember",
    "estuary",
    "fathom",
    "ferry",
    "flint",
    "fossil",
    "fresco",
    "gable",
    "garnet",
    "glacier",
    "granite",
    "grove",
    "harbor",
    "harrow",
    "heather",
    "hollow",
    "indigo",
    "inlet",
    "ivory",
    "juniper",
    "keystone",
    "lantern",
    "lattice",
    "ledger",
    "lumen",
    "marble",
    "meadow",
    "mesa",
    "nimbus",
    "northwind",
    "obsidian",
    "onyx",
    "orchard",
    "pebble",
    "pennant",
    "quartz",
    "quill",
    "ridge",
    "ripple",
    "sable",
    "sextant",
    "solace",
    "sparrow",
    "summit",
    "tamarind",
    "thicket",
    "thistle",
    "timber",
    "tundra",
    "umber",
    "vellum",
    "verdigris",
    "wicker",
    "willow",
    "yarrow",
    "zenith",
)
LABEL_POOL = (
    "label_alpha",
    "label_beta",
    "label_gamma",
    "label_delta",
    "label_epsilon",
    "label_zeta",
    "label_eta",
    "label_theta",
    "label_iota",
    "label_kappa",
    "label_lambda",
    "label_mu",
    "label_nu",
    "label_xi",
    "label_omicron",
    "label_pi",
    "label_rho",
    "label_sigma",
    "label_tau",
    "label_upsilon",
    "label_phi",
    "label_chi",
    "label_psi",
    "label_omega",
)
KINDS = ("credit", "debit", "set_aside", "release")
HOLD_KINDS = ("set_aside", "release")
MODULUS_POOL: tuple[int, ...] = (5, 7, 11)
# parity_vote's declared modulus is 4, not 2: the label is still the parity of a
# counted residue class, but a four-residue feature domain leaves 16 row points
# and 4 counted classes, so "the query entity's features were not demonstrated"
# is a constraint the sampler can actually satisfy. Under modulus 2 the domain
# has four points and the demonstrations exhaust it.
PARITY_MODULUS = 4
FIELD_OPS: dict[str, tuple[str, ...]] = {
    "amount": ("==", "!=", ">=", "<=", ">", "<"),
    "date": ("==", "!=", ">=", "<=", ">", "<"),
    "category": ("==", "!="),
}
ALIAS_TERMINALS = (
    "locate",
    "locate_count",
    "locate_list",
    "locate_one",
    "locate_empty",
)
ASOF_TERMINALS = ("balance", "by_entity", "total", "active_set")
RULE_TERMINALS = (
    "label",
    "labels",
    "label_list",
    "label_set",
    "label_counts",
    "verify",
)
# The terminal a task draws is a function of (seed, task index), so a seed sweep
# covers every shape instead of leaving the schedule to a coin flip.
ALIAS_TERMINALS_BY_DEPTH: dict[int, tuple[str, ...]] = {
    1: ("locate", "locate_list", "locate_count"),
    2: ALIAS_TERMINALS,
}
RULE_TERMINALS_BY_DEPTH: dict[int, tuple[str, ...]] = {
    1: ("label", "verify"),
    2: ("labels", "label_list", "label_set", "label_counts"),
}
SET_TERMINALS = ("set_list", "set_count", "set_contains", "set_missing")
# Each terminal is drawn with its own answer shape, so the arity bins the P69
# F2 card demands are structural DOF, not phrasing: the shapes below are the
# full per-terminal rotation (mask_shape distinguishes every one of them).
SET_SHAPES: dict[str, tuple[str, ...]] = {
    "set_list": ("ids", "ids_count", "ids_groups"),
    # set_count's bare integer is one masked shape however many members there
    # are, so the dict-of-counts shape is the arity-bearing one; the pair stays
    # (the card's list-vs-count-vs-dict DOF) but the rotation favors the dict.
    "set_count": ("count_groups", "count_groups", "count"),
    "set_contains": (
        "contains",
        "contains_verdict",
        "contains_entity",
        "contains_count",
    ),
    "set_missing": ("missing", "missing_named", "missing_count"),
}
# Primary rows one consumed row costs at most, per family. plan_variants() uses
# the widest value, so a (K, n_variants) cell it accepts is hostable by every
# family even though the dispatch does not know which family will run.
EVIDENCE_MULTIPLIER = {
    "alias_locate": 2,
    "asof_state": 2,
    "rule_holdout": 2,
    "set_complete": 2,
}

# The visible protocol and the instruction phrasings are part of the task
# contract: the executors read only the structured program, so a reworded rules
# line or instruction that contradicts the executor would still solve.
# validate_bundle() pins both to these constants and re-renders every
# instruction from (program, phrasing index), rejecting any deviation.
PROTOCOLS: dict[str, str] = {
    "alias_locate": (
        "Each rendered row is a JSON object. Rows of type record are the table; "
        "rows of type alias declare one handle for exactly one entity. Resolve "
        "the program's bind step first: the handle names exactly the entity its "
        "alias row declares, and only that entity's record rows are candidates. "
        "Then apply the program's filter step, if present, keeping the rows that "
        "satisfy every condition; a condition compares amount, date or category "
        "to a constant with ==, !=, >=, <=, > or <. Report the located rows: ids "
        "lists their row ids sorted, count is how many there are, empty is true "
        "when none matched, and an empty match is a legal declared answer rather "
        "than a missing row. Record rows of other entities are never evidence, "
        "even when every filter condition would match them. No rows are omitted."
    ),
    "asof_state": (
        "Each rendered row is a JSON object of type event. Every event carries "
        "an event date and a reveal date: it happened at date and became visible "
        "at reveal. An AS OF answer is computed from the events whose reveal is "
        "at or before the query date, folded in event-date order with the row id "
        "as tie-break, one state per entity: credit adds amount to balance; "
        "debit subtracts amount from balance; set_aside subtracts amount from "
        "balance and adds it to held; release moves min(amount, held) from held "
        "back to balance, so a release larger than what is held at that moment "
        "is capped. An entity is active when its held is above zero. The program "
        "names the entities it asks about and the answer covers exactly those "
        "entities: balance (one named entity's balance, held and active flag), "
        "by_entity (each named entity's balance and held), total (the summed "
        "balance) or active_set (the sorted named entities whose held is above "
        "zero). A delta step folds a second time at a later reveal date and the "
        "answer reports both readings and their change. Events revealed after "
        "the query date are not part of the answer, and events of unnamed "
        "entities are not evidence. No rows are omitted."
    ),
    "rule_holdout": (
        "Each rendered row is a JSON object. Rows of type demo are labelled "
        "demonstrations: points lists the [x, y] feature readings of one "
        "demonstrated entity and label is that entity's label. Rows of type "
        "record are one feature reading of an entity a question may ask about. "
        "An entity's features are its own rows' x and y summed. The world "
        "declares one rule family, a modulus and two labels; the rule is the "
        "only rule of the declared family consistent with every demonstration, "
        "and no demonstration carries a feature point of an entity the questions "
        "ask about. Infer the rule from the demonstrations, then answer for the "
        "entities the program names: label (one entity's label and features), "
        "labels (each named entity's label), label_list (their labels in the "
        "named order), label_set (the named entities carrying a named label), "
        "label_counts (how many named entities carry a named label) or verify "
        "(whether a claimed label holds for one entity). Records of unnamed "
        "entities are not evidence. No rows are omitted."
    ),
    "set_complete": (
        "Each rendered row is a JSON object of type record. A scope step "
        "declares 2 to 4 conditions, each comparing amount, date or category "
        "to a constant with ==, !=, >=, <=, > or <; a row is a member of the "
        "set exactly when it satisfies every condition, and the set is "
        "exhaustive: gold is the complete membership, so a missing member is "
        "as wrong as an extra row. Rows that fail at least one condition are "
        "siblings, never members, however much of the condition range they "
        "overlap. Answer the program's terminal in the shape it declares: "
        "set_list with shape ids (the bare sorted row ids of every member), "
        "ids_count (the sorted ids with the member count) or ids_groups (the "
        "count with the members grouped by entity, each group's ids sorted); "
        "set_count with shape count (the bare member count) or count_groups "
        "(the count with the per-entity member counts); set_contains with "
        "shape contains (the named row id with its membership verdict), "
        "contains_verdict (the bare membership verdict for the named row id), "
        "contains_entity (the named row id with its entity and verdict) or "
        "contains_count (the named row id with its verdict and the member "
        "count); "
        "set_missing with shape missing (the bare sorted named row ids that "
        "are not members), missing_count (the sorted missing ids with how "
        "many there are) or missing_named (the sorted missing ids with the "
        "sorted named ids). Every id list in an answer is in the answer's own "
        "sorted order, not the presentation order. No rows are omitted."
    ),
}

# Per-rule-family structure text, pinned into the header as `rule_structure`:
# the structure class is declared -- it is what defines the family -- while the
# parameters are not, and the demonstrations are the only route to them.
RULE_STRUCTURES: dict[str, str] = {
    "threshold_class": (
        "rule_family threshold_class: an entity's label is the first label when "
        "(a*x + b*y + c) modulo modulus is below t and the second label "
        "otherwise, where x and y are the entity's summed features and a, b, c "
        "and t are unknown integers with 1 <= a < modulus, 1 <= b < modulus, "
        "0 <= c < modulus and 1 <= t < modulus."
    ),
    "parity_vote": (
        "rule_family parity_vote: an entity's label is a function of the parity "
        "of how many of its rows have (x + y) modulo modulus equal to q, where "
        "the residue q and which parity carries which label are fixed by the "
        "demonstrations and are not declared."
    ),
}

RETURNS: dict[str, str] = {
    "alias_locate": (
        "Return one JSON object with keys alias, entity, ids (sorted row ids), "
        "count and empty, or the count or single id the program asks for."
    ),
    "asof_state": (
        "Return one JSON object with the query's as-of date and the balance, "
        "held, total, active set or per-entity state the program asks for."
    ),
    "rule_holdout": (
        "Return one JSON object with rule_family and the label, labels, label "
        "set, label counts or verdict the program asks for."
    ),
    "set_complete": (
        "Return the exhaustive member set the scope defines, in the shape the "
        "terminal declares: bare ids, ids with a count, ids grouped by entity, "
        "the member count with or without per-entity counts, a membership "
        "verdict for a named row, or the sorted named ids that are missing."
    ),
}

FIELDS: dict[str, str] = {
    "alias_locate": (
        "record rows carry id, entity, category, amount, date, memo; alias rows "
        "carry id, alias, entity, memo."
    ),
    "asof_state": "event rows carry id, entity, kind, amount, date, reveal, memo.",
    "rule_holdout": (
        "demo rows carry id, points, label, memo; record rows carry id, entity, "
        "x, y, memo."
    ),
    "set_complete": ("record rows carry id, entity, category, amount, date, memo."),
}

# What each family trains: L1 binding/location, L3 time-separated state replay,
# L4 finite-rule induction under a structured holdout. Nothing here claims
# closed-loop control or measured model utility.
CAPABILITY_LEVELS: dict[str, str] = {
    "alias_locate": "L1_alias_binding_location",
    "asof_state": "L3_asof_state_reveal_split",
    "rule_holdout": "L4_rule_induction_structured_holdout",
    "set_complete": "L2_exhaustive_set_membership",
}

HONESTY: dict[str, Any] = {
    "source_kind": "simulated",
    "strict_long_dependency_verified": False,
    "model_utility_measured": False,
    "production_eligible": False,
}

PROMPTS: dict[str, tuple[str, ...]] = {
    "alias_locate": (
        "Execute the typed program below over the rendered rows and return the located row ids.",
        "Run the alias-locate program on the table and report the rows the handle names.",
        "Evaluate this program against the rows and give the ids the alias resolves to.",
        "Resolve the alias, apply the stated filters in order, and answer with the matching ids.",
        "Carry out the following typed query over the records and return the located rows.",
        "Read the record table, run the program exactly as written, and report its result.",
        "Compute the rows the alias locates once the program's filters are applied.",
        "Process the rows with the program below and return the located row ids.",
        "Work through the program against the rendered rows and state the answer.",
        "Run this table program and return the entity, the ids and the count it asks for.",
        "Solve the typed record query below and report the result as specified.",
        "Execute the program over the rendered table; report the bound entity and provenance.",
        "Follow the bind step, apply each filter in order, and answer with the located ids.",
        "Apply the program to the record rows and give the ids it asks for.",
        "Interpret the query program, evaluate it on the table, and return the answer.",
        "Evaluate the alias-locate program on the rendered records and answer.",
        "Execute the stated operations over the table in order and report the result.",
        "Run the query program on the rows below and return the located row ids.",
        "Resolve the handle, filter the candidate rows in sequence, and report the ids.",
        "Compute the program's result over the rendered record table.",
        "Execute the following program on the rows and report the ids with their count.",
        "Evaluate this alias-resolution program over the records and answer.",
        "Solve the record query by running the program and report the located rows.",
        "Run the program over the table and give the located ids and their count.",
        "Apply the bind and filter steps to the records and return the matching row ids.",
    ),
    "asof_state": (
        "Execute the typed program below over the rendered events and return the state it asks for.",
        "Run the as-of program on the event table and report the state at the query date.",
        "Evaluate this program against the events and give the as-of answer.",
        "Fold the visible events in event-date order and report the state at the query date.",
        "Carry out the following typed query over the events and return the state.",
        "Read the event table, run the program exactly as written, and report its result.",
        "Compute the state the program asks about as of the stated reveal date.",
        "Process the events with the program below and return the as-of state.",
        "Work through the program against the rendered events and state the answer.",
        "Run this event program and return the balances, totals and active set it asks for.",
        "Solve the typed state query below and report the result as specified.",
        "Execute the program over the rendered events; report the state and its date.",
        "Take the events revealed by the query date, fold them in order, and answer.",
        "Apply the program to the events and give the as-of state it asks for.",
        "Interpret the query program, evaluate it on the events, and return the answer.",
        "Evaluate the as-of program on the rendered events and answer.",
        "Execute the stated operations over the events in order and report the result.",
        "Run the query program on the rows below and return the as-of state.",
        "Fold each revealed event in event-date order under the stated rules and answer.",
        "Compute the program's result over the rendered event table.",
        "Execute the following program on the events and report the state with its date.",
        "Evaluate this as-of-state program over the events and answer.",
        "Solve the event query by running the program and report the as-of state.",
        "Run the program over the events and give the balances, totals or active set.",
        "Apply the reveal cutoff and the fold to the events and return the state requested.",
    ),
    "rule_holdout": (
        "Execute the typed program below over the rendered rows and return the labels it asks for.",
        "Infer the rule from the demonstrations, run the program, and report the labels.",
        "Evaluate this program against the records and give the labels the entities carry.",
        "Learn the rule from the labelled demonstrations and answer for the named entities.",
        "Carry out the following typed query over the records and return the labels.",
        "Read the demonstration and record rows, run the program as written, and report it.",
        "Compute the labels the program asks for from the demonstrated rule.",
        "Process the rows with the program below and return the labels it asks for.",
        "Work through the program against the rendered rows and state the answer.",
        "Run this induction program and return the labels, label set or counts it asks for.",
        "Solve the typed rule query below and report the result as specified.",
        "Execute the program over the rendered rows; report the labels and their evidence.",
        "Infer the rule from the demonstrations, aggregate each named entity, and answer.",
        "Apply the program to the records and give the labels it asks for.",
        "Interpret the query program, evaluate it on the rows, and return the answer.",
        "Evaluate the rule-induction program on the rendered rows and answer.",
        "Execute the stated operations over the rows in order and report the result.",
        "Run the query program on the rows below and return the entity labels.",
        "Infer the rule, then report the labels the program asks about in its order.",
        "Compute the program's result over the rendered rows.",
        "Execute the following program on the rows and report the labels with their counts.",
        "Evaluate this rule-holdout program over the records and answer.",
        "Solve the rule query by running the program and report the labels.",
        "Run the program over the rows and give the labels or verdict it asks for.",
        "Learn the declared rule structure from the demonstrations and answer the query.",
    ),
    "set_complete": (
        "Execute the typed program below over the rendered rows and return the complete member set.",
        "Run the scope over every row and report the exhaustive set it defines.",
        "Evaluate this program against the records and give the full membership it asks for.",
        "Apply the declared conditions to all the rows and answer with the complete set.",
        "Carry out the following typed query over the records and return every member.",
        "Read the record table, run the program exactly as written, and report its result.",
        "Compute the complete set of rows the scope admits, then answer the terminal.",
        "Process every row with the program below and return the membership it defines.",
        "Work through the program against the rendered rows and state the answer.",
        "Run this set program and return the ids, the count or the verdict it asks for.",
        "Solve the typed set query below and report the result as specified.",
        "Execute the program over the rendered rows; report the members it admits.",
        "Take the scope's conditions, filter nothing less than every row, and answer.",
        "Apply the program to the records and give the exhaustive answer it asks for.",
        "Interpret the query program, evaluate it on every row, and return the answer.",
        "Evaluate the set-completeness program on the rendered rows and answer.",
        "Execute the stated conditions over the rows in order and report the full set.",
        "Run the query program on the rows below and return the membership asked for.",
        "Resolve the scope, keep exactly the rows that satisfy it, and report the set.",
        "Compute the program's result over the rendered record table.",
        "Execute the following program on the rows and report the members with their count.",
        "Evaluate this exhaustive-set program over the records and answer.",
        "Solve the set query by running the program and report the complete membership.",
        "Run the program over the table and give the ids, count or missing list it asks for.",
        "Apply every condition to every row and return the exhaustive set the scope defines.",
    ),
}

_HYPOTHESES: dict[int, list[tuple[int, int, int, int]]] = {}
_RULE_CACHE: dict[tuple[Any, ...], tuple[Any, ...]] = {}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_id(prefix: str, rng: random.Random, width: int = 24) -> str:
    """One row identity at the world's own hex width (a per-world presentation knob)."""
    return prefix + format(rng.getrandbits(4 * width), f"0{width}x")


def _fresh_entity(rng: random.Random, used: set[str]) -> str:
    """A fresh entity name; no two entities of one world may share a name."""
    entity = f"unit-{rng.getrandbits(20):05x}"
    while entity in used:
        entity = f"unit-{rng.getrandbits(20):05x}"
    used.add(entity)
    return entity


@dataclass(frozen=True)
class Row:
    id: str
    type: str
    entity: str
    memo: str
    amount: int | None = None
    category: str | None = None
    day: str | None = None
    alias: str | None = None
    kind: str | None = None
    reveal: str | None = None
    x: int | None = None
    y: int | None = None
    points: tuple[tuple[int, int], ...] | None = None
    label: str | None = None

    def visible(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "entity": self.entity,
            "memo": self.memo,
        }
        for key, value in (
            ("amount", self.amount),
            ("category", self.category),
            ("date", self.day),
            ("alias", self.alias),
            ("kind", self.kind),
            ("reveal", self.reveal),
            ("x", self.x),
            ("y", self.y),
            ("points", self.points),
            ("label", self.label),
        ):
            if value is not None:
                row[key] = value
        return row


def render_context(
    rows: list[Row], family: str, header_extra: dict[str, Any] | None = None
) -> str:
    """One header line pinning schema, family and rules, then one JSON row per line."""
    header: dict[str, Any] = {
        "schema": VERSION,
        "family": family,
        "rules": PROTOCOLS[family],
    }
    if header_extra:
        header.update(header_extra)
    return "\n".join([_dump(header)] + [_dump(row.visible()) for row in rows])


def parse_context(context: str) -> tuple[dict[str, Any], list[Row]]:
    lines = context.splitlines()
    if not lines:
        raise ValueError("empty context")
    header = json.loads(lines[0])
    rows = []
    for line in lines[1:]:
        item = json.loads(line)
        points = item.get("points")
        rows.append(
            Row(
                id=item["id"],
                type=item["type"],
                entity=item["entity"],
                memo=item["memo"],
                amount=item.get("amount"),
                category=item.get("category"),
                day=item.get("date"),
                alias=item.get("alias"),
                kind=item.get("kind"),
                reveal=item.get("reveal"),
                x=item.get("x"),
                y=item.get("y"),
                points=tuple(tuple(point) for point in points) if points else None,
                label=item.get("label"),
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


def _filter_conditions(step: dict[str, Any]) -> list[dict[str, Any]]:
    """A filter step carries 2 or 3 conditions spanning 2 or 3 distinct fields."""
    if step.get("op") != "filter":
        raise ValueError("only a filter step may precede the terminal operation")
    conditions = step.get("conditions")
    if not isinstance(conditions, list) or not 2 <= len(conditions) <= 3:
        raise ValueError("a filter step needs 2 or 3 conditions")
    fields = {condition.get("field") for condition in conditions}
    if not 2 <= len(fields) <= 3:
        raise ValueError("a filter predicate must span 2 or 3 fields")
    return conditions


def _conditions_text(conditions: list[dict[str, Any]]) -> str:
    return " and ".join(
        f"{condition['field']} {condition['op']} {condition['value']}"
        for condition in conditions
    )


# --------------------------------------------------------------------------
# alias_locate (L1): binding is the only route to the answer
# --------------------------------------------------------------------------


def _alias_steps(question: dict[str, Any]) -> list[dict[str, Any]]:
    steps = question.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 3:
        raise ValueError("invalid program chain")
    if steps[0].get("op") != "bind" or not isinstance(steps[0].get("alias"), str):
        raise ValueError("a bind step must come first")
    for step in steps[1:-1]:
        _filter_conditions(step)
    if steps[-1].get("op") not in ALIAS_TERMINALS:
        raise ValueError("unsupported terminal operation")
    return steps


def _alias_bound(rows: list[Row], steps: list[dict[str, Any]]) -> tuple[Row, list[Row]]:
    """The declaration row the bind step reads and the candidate rows it selects."""
    handle = steps[0]["alias"]
    declarations = [row for row in rows if row.type == "alias" and row.alias == handle]
    if len(declarations) != 1:
        raise ValueError("the alias handle does not name exactly one entity")
    declaration = declarations[0]
    table = [
        row for row in rows if row.type == "record" and row.entity == declaration.entity
    ]
    for step in steps[1:-1]:
        conditions = _filter_conditions(step)
        table = [
            row
            for row in table
            if all(_holds(row, condition) for condition in conditions)
        ]
    return declaration, table


def _solve_alias(rows: list[Row], question: dict[str, Any]) -> Any:
    steps = _alias_steps(question)
    declaration, table = _alias_bound(rows, steps)
    ids = sorted(row.id for row in table)
    terminal = steps[-1]["op"]
    if terminal == "locate":
        return {
            "alias": declaration.alias,
            "entity": declaration.entity,
            "ids": ids,
            "count": len(ids),
            "empty": not ids,
        }
    if terminal == "locate_list":
        return ids
    if terminal == "locate_count":
        return {
            "alias": declaration.alias,
            "entity": declaration.entity,
            "count": len(ids),
        }
    if terminal == "locate_empty":
        # The program *declares* that the condition locates nothing, and the
        # executor enforces the declaration: an empty match is a state of the
        # world, not a missing row, so a world where rows did match is a
        # malformed query rather than an empty answer.
        if ids:
            raise ValueError("the query declares an empty match but rows matched")
        return {
            "alias": declaration.alias,
            "entity": declaration.entity,
            "ids": [],
            "count": 0,
            "empty": True,
        }
    if len(ids) != 1:
        raise ValueError("the single-match query does not locate exactly one row")
    return {"alias": declaration.alias, "entity": declaration.entity, "id": ids[0]}


def _alias_evidence(rows: list[Row], question: dict[str, Any]) -> list[str]:
    declaration, table = _alias_bound(rows, _alias_steps(question))
    return sorted([declaration.id, *(row.id for row in table)])


# --------------------------------------------------------------------------
# asof_state (L3): event time and reveal time are separate
# --------------------------------------------------------------------------


def _asof_steps(question: dict[str, Any]) -> list[dict[str, Any]]:
    steps = question.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 3:
        raise ValueError("invalid program chain")
    if steps[0].get("op") != "asof" or not isinstance(steps[0].get("reveal"), str):
        raise ValueError("an as-of step must come first")
    for step in steps[1:-1]:
        if step.get("op") != "delta" or not isinstance(step.get("reveal"), str):
            raise ValueError("only a delta step may follow the as-of step")
    if steps[-1].get("op") not in ASOF_TERMINALS:
        raise ValueError("unsupported terminal operation")
    return steps


def _asof_cutoffs(steps: list[dict[str, Any]]) -> tuple[str, str]:
    """The earlier and later reveal dates the program folds at."""
    earlier = steps[0]["reveal"]
    later = steps[1]["reveal"] if len(steps) == 3 else earlier
    if later < earlier:
        raise ValueError("the delta step must not precede the as-of step")
    return earlier, later


def _asof_named(terminal: dict[str, Any]) -> list[str]:
    """The entities the terminal asks about."""
    if terminal["op"] == "balance":
        entity = terminal.get("entity")
        if not isinstance(entity, str):
            raise ValueError("a balance query must name one entity")
        return [entity]
    entities = terminal.get("entities")
    if not isinstance(entities, list) or not 1 <= len(entities) <= 12:
        raise ValueError("a state query must name 1 to 12 entities")
    if any(not isinstance(entity, str) for entity in entities):
        raise ValueError("named entities must be strings")
    if len(set(entities)) != len(entities):
        raise ValueError("named entities must be distinct")
    return list(entities)


def _fold(
    rows: list[Row], cutoff: str, entities: list[str], order: str = "event"
) -> dict[str, dict[str, int]]:
    """Fold the events revealed by the cutoff, in event-date or reveal-date order."""
    if order not in ("event", "reveal"):
        raise ValueError("unsupported fold order")
    key = "day" if order == "event" else "reveal"
    named = set(entities)
    events = [
        row
        for row in rows
        if row.type == "event" and row.entity in named and row.reveal <= cutoff
    ]
    events.sort(key=lambda row: (getattr(row, key), row.id))
    state: dict[str, dict[str, int]] = {
        entity: {"balance": 0, "held": 0} for entity in entities
    }
    for event in events:
        account = state[event.entity]
        if event.kind == "credit":
            account["balance"] += event.amount
        elif event.kind == "debit":
            account["balance"] -= event.amount
        elif event.kind == "set_aside":
            account["balance"] -= event.amount
            account["held"] += event.amount
        elif event.kind == "release":
            moved = min(event.amount, account["held"])
            account["held"] -= moved
            account["balance"] += moved
        else:
            raise ValueError("unknown event kind")
    return state


def _solve_asof(
    rows: list[Row], question: dict[str, Any], order: str = "event"
) -> dict[str, Any]:
    steps = _asof_steps(question)
    earlier, later = _asof_cutoffs(steps)
    terminal = steps[-1]
    entities = _asof_named(terminal)
    before = _fold(rows, earlier, entities, order)
    after = _fold(rows, later, entities, order)
    chained = len(steps) == 3
    if terminal["op"] == "balance":
        entity = entities[0]
        answer: dict[str, Any] = {
            "as_of": later,
            "entity": entity,
            "balance": after[entity]["balance"],
            "held": after[entity]["held"],
            "active": after[entity]["held"] > 0,
        }
        if chained:
            answer.update(
                {
                    "from": earlier,
                    "balance_before": before[entity]["balance"],
                    "change": after[entity]["balance"] - before[entity]["balance"],
                }
            )
        return answer
    if terminal["op"] == "by_entity":
        answer = {
            "as_of": later,
            "entities": entities,
            "balance": {entity: after[entity]["balance"] for entity in entities},
            "held": {entity: after[entity]["held"] for entity in entities},
        }
        if chained:
            answer.update(
                {
                    "from": earlier,
                    "change": {
                        entity: after[entity]["balance"] - before[entity]["balance"]
                        for entity in entities
                    },
                }
            )
        return answer
    if terminal["op"] == "total":
        answer = {
            "as_of": later,
            "entities": entities,
            "total": sum(after[entity]["balance"] for entity in entities),
        }
        if chained:
            answer.update(
                {
                    "from": earlier,
                    "total_before": sum(
                        before[entity]["balance"] for entity in entities
                    ),
                    "change": sum(
                        after[entity]["balance"] - before[entity]["balance"]
                        for entity in entities
                    ),
                }
            )
        return answer
    active = sorted(entity for entity in entities if after[entity]["held"] > 0)
    answer = {"as_of": later, "entities": entities, "active_set": active}
    if chained:
        was = {entity for entity in entities if before[entity]["held"] > 0}
        now = set(active)
        answer.update(
            {
                "from": earlier,
                "became_active": sorted(now - was),
                "left_active": sorted(was - now),
            }
        )
    return answer


def _asof_evidence(rows: list[Row], question: dict[str, Any]) -> list[str]:
    steps = _asof_steps(question)
    _, later = _asof_cutoffs(steps)
    named = set(_asof_named(steps[-1]))
    return sorted(
        row.id
        for row in rows
        if row.type == "event" and row.entity in named and row.reveal <= later
    )


# --------------------------------------------------------------------------
# rule_holdout (L4): two structural rule families, split by structure
# --------------------------------------------------------------------------


def _rule_steps(question: dict[str, Any]) -> list[dict[str, Any]]:
    steps = question.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 3:
        raise ValueError("invalid program chain")
    learned = steps[0]
    if (
        learned.get("op") != "learn"
        or learned.get("rule_family") not in RULE_STRUCTURES
    ):
        raise ValueError("a learn step must come first")
    for step in steps[1:-1]:
        if step.get("op") != "aggregate":
            raise ValueError("only an aggregate step may follow the learn step")
    if steps[-1].get("op") not in RULE_TERMINALS:
        raise ValueError("unsupported terminal operation")
    return steps


def _threshold_label(
    rule: tuple[int, int, int, int],
    point: tuple[int, int],
    labels: list[str],
    modulus: int,
) -> str:
    a, b, c, t = rule
    value = (a * point[0] + b * point[1] + c) % modulus
    return labels[0] if value < t else labels[1]


def _parity_label(
    rule: tuple[int, int],
    points: list[tuple[int, int]],
    labels: list[str],
    modulus: int = PARITY_MODULUS,
) -> str:
    """The parity of how many rows fall in the counted residue class."""
    residue, flip = rule
    count = sum((x + y) % modulus == residue for x, y in points)
    return labels[(count % 2) ^ flip]


def _threshold_hypotheses(modulus: int) -> list[tuple[int, int, int, int]]:
    """One representative per *distinguishable* threshold rule.

    Several parameter tuples induce the same label function on the feature
    domain (a shifted coefficient triple can land in the same residue set under
    its own threshold), and no demonstration can separate those. Enumerating one
    representative per label-function signature makes "the demonstrations
    identify exactly one rule" a claim about distinguishable rules, which is the
    only version a reader could ever satisfy.
    """
    cached = _HYPOTHESES.get(modulus)
    if cached is None:
        signatures: dict[tuple[bool, ...], tuple[int, int, int, int]] = {}
        for rule in itertools.product(
            range(1, modulus), range(1, modulus), range(modulus), range(1, modulus)
        ):
            signatures.setdefault(_threshold_signature(rule, modulus), rule)
        cached = sorted(signatures.values())
        _HYPOTHESES[modulus] = cached
    return cached


def _parity_hypotheses() -> list[tuple[int, int]]:
    """One hypothesis per (counted residue class, label parity): 4 in total."""
    return [(residue, flip) for residue in range(PARITY_MODULUS) for flip in (0, 1)]


def _total_features(
    points: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> tuple[int, int]:
    return (sum(x for x, _ in points), sum(y for _, y in points))


def _infer_rule(
    rule_family: str,
    demonstrations: list[tuple[Any, Any]],
    modulus: int,
    labels: list[str],
) -> tuple[Any, ...]:
    """The unique rule of the declared family consistent with every demonstration.

    Mirrors capability_rules_workflow._infer: the hypotheses are enumerated by
    brute force and exactly one may survive, so a demonstration set that leaves
    the rule ambiguous is a generation failure rather than a guessable task.
    """
    cache_key = (rule_family, modulus, tuple(labels), tuple(demonstrations))
    cached = _RULE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if rule_family == "threshold_class":
        consistent: list[tuple[Any, ...]] = [
            rule
            for rule in _threshold_hypotheses(modulus)
            if all(
                _threshold_label(rule, _total_features(points), labels, modulus)
                == label
                for points, label in demonstrations
            )
        ]
    elif rule_family == "parity_vote":
        consistent = [
            rule
            for rule in _parity_hypotheses()
            if all(
                _parity_label(rule, list(points), labels, modulus) == label
                for points, label in demonstrations
            )
        ]
    else:
        raise ValueError("unsupported rule family")
    if len(consistent) != 1:
        raise ValueError("demonstrations must identify exactly one rule")
    if len(_RULE_CACHE) > 4096:
        _RULE_CACHE.clear()
    _RULE_CACHE[cache_key] = consistent[0]
    return consistent[0]


def _rule_label(
    rule_family: str,
    rule: tuple[Any, ...],
    rows: list[Row],
    modulus: int,
    labels: list[str],
) -> str:
    if rule_family == "threshold_class":
        return _threshold_label(
            rule,
            (sum(row.x for row in rows), sum(row.y for row in rows)),
            labels,
            modulus,
        )
    return _parity_label(rule, [(row.x, row.y) for row in rows], labels, modulus)


def _rule_header(header: dict[str, Any]) -> tuple[str, int, list[str]]:
    rule_family = header.get("rule_family")
    modulus, labels = header.get("modulus"), header.get("labels")
    if rule_family not in RULE_STRUCTURES:
        raise ValueError("unsupported or missing rule family")
    if header.get("rule_structure") != RULE_STRUCTURES[rule_family]:
        raise ValueError("rule structure text does not match the registered contract")
    if type(modulus) is not int or (
        modulus not in MODULUS_POOL
        if rule_family == "threshold_class"
        else modulus != PARITY_MODULUS
    ):
        raise ValueError("invalid declared modulus")
    if (
        not isinstance(labels, list)
        or len(labels) != 2
        or any(not isinstance(label, str) for label in labels)
        or len(set(labels)) != 2
    ):
        raise ValueError("invalid declared label alphabet")
    return rule_family, modulus, list(labels)


def _rows_for(rows: list[Row], entity: str) -> list[Row]:
    """The record rows of one entity (generation-time convenience)."""
    return [row for row in rows if row.type == "record" and row.entity == entity]


def _rule_entities(rows: list[Row]) -> dict[str, list[Row]]:
    entities: dict[str, list[Row]] = {}
    for row in rows:
        if row.type != "record":
            continue
        if row.x is None or row.y is None:
            raise ValueError("a record row is missing its features")
        entities.setdefault(row.entity, []).append(row)
    return entities


def _features(rule_family: str, rows: list[Row]) -> dict[str, int]:
    if rule_family == "threshold_class":
        return {"x": sum(row.x for row in rows), "y": sum(row.y for row in rows)}
    return {
        "rows": len(rows),
        "counted": sum((row.x + row.y) % PARITY_MODULUS == 0 for row in rows),
    }


def _named_entities(
    terminal: dict[str, Any], entities: dict[str, list[Row]]
) -> list[str]:
    named = terminal.get("entities")
    if not isinstance(named, list) or not 1 <= len(named) <= 12:
        raise ValueError("a label query must name 1 to 12 entities")
    if len(set(named)) != len(named):
        raise ValueError("named entities must be distinct")
    for entity in named:
        if not isinstance(entity, str) or entity not in entities:
            raise ValueError("the query names an entity with no rows")
    return list(named)


def _solve_rule(
    header: dict[str, Any], rows: list[Row], question: dict[str, Any]
) -> Any:
    rule_family, modulus, labels = _rule_header(header)
    steps = _rule_steps(question)
    if (
        steps[0].get("rule_family") != rule_family
        or question.get("rule_family") != rule_family
    ):
        raise ValueError("program rule family does not match the visible contract")
    if any(row.type not in ("demo", "record") for row in rows):
        raise ValueError("unknown row type")
    demonstrations = [(row.points, row.label) for row in rows if row.type == "demo"]
    if not demonstrations or any(label is None for _, label in demonstrations):
        raise ValueError("the world declares no usable demonstrations")
    rule = _infer_rule(rule_family, demonstrations, modulus, labels)
    entities = _rule_entities(rows)
    terminal = steps[-1]
    op = terminal["op"]
    if op in ("label", "verify"):
        entity = terminal.get("entity")
        if not isinstance(entity, str) or entity not in entities:
            raise ValueError("the query names an entity with no rows")
        actual = _rule_label(rule_family, rule, entities[entity], modulus, labels)
        if op == "verify":
            claim = terminal.get("claim")
            if not isinstance(claim, str) or claim not in labels:
                raise ValueError("a verify query must claim a declared label")
            return {
                "rule_family": rule_family,
                "entity": entity,
                "claim": claim,
                "label": actual,
                "holds": claim == actual,
            }
        return {
            "rule_family": rule_family,
            "entity": entity,
            "label": actual,
            "features": _features(rule_family, entities[entity]),
        }
    named = _named_entities(terminal, entities)
    table = {
        entity: _rule_label(rule_family, rule, entities[entity], modulus, labels)
        for entity in named
    }
    if op == "labels":
        return {"rule_family": rule_family, "labels": table, "count": len(named)}
    if op == "label_list":
        return [table[entity] for entity in named]
    wanted = terminal.get("label")
    if not isinstance(wanted, str) or wanted not in labels:
        raise ValueError("the query must name a declared label")
    if op == "label_set":
        carried = sorted(entity for entity in named if table[entity] == wanted)
        return {
            "rule_family": rule_family,
            "label": wanted,
            "entities": carried,
            "count": len(carried),
        }
    return {
        "rule_family": rule_family,
        "label": wanted,
        "count": sum(table[entity] == wanted for entity in named),
        "entities": len(named),
    }


def _rule_evidence(rows: list[Row], question: dict[str, Any]) -> list[str]:
    terminal = _rule_steps(question)[-1]
    if terminal["op"] in ("label", "verify"):
        named = {terminal.get("entity")}
    else:
        named = set(terminal.get("entities") or [])
    return sorted(
        row.id
        for row in rows
        if row.type == "demo" or (row.type == "record" and row.entity in named)
    )


# --------------------------------------------------------------------------
# set_complete (L2): gold is the exhaustive membership
# --------------------------------------------------------------------------


def _set_steps(question: dict[str, Any]) -> list[dict[str, Any]]:
    steps = question.get("steps")
    if not isinstance(steps, list) or len(steps) != 2:
        raise ValueError("a set program is exactly a scope step and a terminal")
    scope, terminal = steps
    if scope.get("op") != "scope":
        raise ValueError("a scope step must come first")
    conditions = scope.get("conditions")
    if not isinstance(conditions, list) or not 2 <= len(conditions) <= 4:
        raise ValueError("a scope step needs 2 to 4 conditions")
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {
            "field",
            "op",
            "value",
        }:
            raise ValueError("a scope condition is a field, an op and a value")
        field, op = condition["field"], condition["op"]
        if field not in FIELD_OPS or op not in FIELD_OPS[field]:
            raise ValueError("unsupported predicate")
        value = condition["value"]
        if field == "amount" and type(value) is not int:
            raise ValueError("an amount predicate must be an integer")
        if field != "amount" and not isinstance(value, str):
            raise ValueError("a text predicate must be a string")
    if terminal.get("op") not in SET_TERMINALS:
        raise ValueError("unsupported terminal operation")
    shape = terminal.get("shape")
    if shape not in SET_SHAPES[terminal["op"]]:
        raise ValueError("unsupported terminal shape")
    if terminal["op"] == "set_contains":
        row_id = terminal.get("id")
        if not isinstance(row_id, str):
            raise ValueError("a contains query must name one row id")
    elif terminal["op"] == "set_missing":
        named = terminal.get("ids")
        if not isinstance(named, list) or not 2 <= len(named) <= 8:
            raise ValueError("a missing query must name 2 to 8 row ids")
        if any(not isinstance(row_id, str) for row_id in named):
            raise ValueError("named row ids must be strings")
        if len(set(named)) != len(named):
            raise ValueError("named row ids must be distinct")
    return steps


def _set_members(rows: list[Row], steps: list[dict[str, Any]]) -> list[Row]:
    """The exhaustive membership: every row that satisfies every condition."""
    conditions = steps[0]["conditions"]
    members = [row for row in rows if row.type == "record"]
    for condition in conditions:
        members = [row for row in members if _holds(row, condition)]
    return members


def _set_evidence(rows: list[Row], question: dict[str, Any]) -> list[str]:
    """Provenance: the full membership for set terminals, the named rows for verdicts.

    A set answer is exhaustive, so its evidence is every member -- a reader
    cannot know the set is complete without reading them all. A verdict names
    its own rows; membership still has to be decided, but the answer only
    covers the ids it names, exactly as _rule_evidence covers only the named
    entities. Siblings are never evidence: they are what the reader must
    exclude, not read.
    """
    steps = _set_steps(question)
    terminal = steps[-1]
    if terminal["op"] == "set_contains":
        # A contains verdict is fail-closed about its own row: the program
        # names a rendered row, and a world that no longer renders it is a
        # malformed query rather than a negative verdict. The contains_count
        # shape also reads the whole membership (its count key), so its
        # evidence is the membership plus the named row.
        row_id = terminal["id"]
        if not any(row.id == row_id for row in rows if row.type == "record"):
            raise ValueError("the contains query names a row that is not rendered")
        if terminal["shape"] == "contains_count":
            members = set(row.id for row in _set_members(rows, steps))
            return sorted({row_id, *members})
        return [row_id]
    if terminal["op"] == "set_missing":
        wanted = set(terminal["ids"])
        return sorted(
            row.id for row in rows if row.type == "record" and row.id in wanted
        )
    return sorted(row.id for row in _set_members(rows, steps))


def _solve_set(rows: list[Row], question: dict[str, Any]) -> Any:
    steps = _set_steps(question)
    terminal = steps[-1]
    if terminal["op"] == "set_contains":
        # Fail-closed: the program names a rendered row, and a world that no
        # longer renders it is a malformed query rather than a verdict.
        row_id = terminal["id"]
        if not any(row.id == row_id for row in rows if row.type == "record"):
            raise ValueError("the contains query names a row that is not rendered")
    members = _set_members(rows, steps)
    ids = sorted(row.id for row in members)
    by_entity: dict[str, list[str]] = {}
    for row in sorted(members, key=lambda row: (row.entity, row.id)):
        by_entity.setdefault(row.entity, []).append(row.id)
    op, shape = terminal["op"], terminal["shape"]
    if op == "set_list":
        if shape == "ids":
            return ids
        if shape == "ids_count":
            return {"ids": ids, "count": len(ids)}
        return {"count": len(ids), "entities": by_entity}
    if op == "set_count":
        if shape == "count":
            return len(ids)
        return {
            "count": len(ids),
            "entities": {
                entity: len(entity_ids) for entity, entity_ids in by_entity.items()
            },
        }
    if op == "set_contains":
        row_id = terminal["id"]
        contains = row_id in set(ids)
        if shape == "contains_verdict":
            return contains
        if shape == "contains_entity":
            row = next(row for row in rows if row.id == row_id)
            return {"id": row_id, "entity": row.entity, "contains": contains}
        if shape == "contains_count":
            return {
                "id": row_id,
                "contains": contains,
                "count": len(ids),
            }
        return {"id": row_id, "contains": contains}
    named = sorted(terminal["ids"])
    rendered = {row.id for row in rows if row.type == "record"}
    if not set(named) <= rendered:
        # Fail-closed: a named row that is no longer rendered is a malformed
        # query, not an implicitly-missing one -- else dropping the sibling
        # row would "answer" it into the missing list for free.
        raise ValueError("the missing query names a row that is not rendered")
    member_ids = set(ids)
    missing = sorted(row_id for row_id in named if row_id not in member_ids)
    if shape == "missing":
        return missing
    if shape == "missing_count":
        return {"missing": missing, "count": len(missing)}
    return {"ids": named, "missing": missing}


def _solve_rows(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    order: str = "event",
) -> Any:
    family = header.get("family")
    if (
        header.get("schema") != VERSION
        or family not in PROTOCOLS
        or header.get("rules") != PROTOCOLS[family]
    ):
        raise ValueError("unsupported or missing visible contract")
    if question.get("family") != family:
        raise ValueError("program family does not match the visible contract")
    if family == "alias_locate":
        return _solve_alias(rows, question)
    if family == "asof_state":
        return _solve_asof(rows, question, order)
    if family == "set_complete":
        return _solve_set(rows, question)
    return _solve_rule(header, rows, question)


def solve_visible(context: str, question: dict[str, Any]) -> Any:
    """Independent executor over visible rows only; never reads gold or hidden state."""
    try:
        header, rows = parse_context(context)
        return _solve_rows(header, rows, question)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("incomplete or malformed visible records/program") from exc


def describe_program(program: dict[str, Any]) -> str:
    parts = []
    for step in program["steps"]:
        op = step["op"]
        if op == "bind":
            parts.append(
                f"bind the alias {step['alias']} to the entity its declaration names"
            )
        elif op == "filter":
            parts.append(
                f"keep candidate rows where {_conditions_text(step['conditions'])}"
            )
        elif op == "locate":
            parts.append("report the located rows: sorted ids, count and empty flag")
        elif op == "locate_list":
            parts.append("report the located row ids as a bare sorted list")
        elif op == "locate_count":
            parts.append("report how many rows the alias located")
        elif op == "locate_one":
            parts.append("report the single located row id")
        elif op == "locate_empty":
            parts.append(
                "report that the condition locates no row, with the alias it bound"
            )
        elif op == "asof":
            parts.append(f"take the state as of reveal date {step['reveal']}")
        elif op == "delta":
            parts.append(
                f"fold again as of reveal date {step['reveal']} and report the change"
            )
        elif op == "balance":
            parts.append(f"report the balance and held of entity {step['entity']}")
        elif op == "by_entity":
            parts.append("report the balance and held of every named entity")
        elif op == "total":
            parts.append("report the summed balance of the named entities")
        elif op == "active_set":
            parts.append("report the sorted named entities whose held is above zero")
        elif op == "learn":
            parts.append(
                f"infer the {step['rule_family']} rule from the demonstrations"
            )
        elif op == "aggregate":
            parts.append("aggregate every named entity's rows into its features")
        elif op == "label":
            parts.append(f"report the label of entity {step['entity']}")
        elif op == "labels":
            parts.append("report every named entity's label")
        elif op == "label_list":
            parts.append(
                "report the named entities' labels as a bare list in named order"
            )
        elif op == "label_set":
            parts.append(f"report which named entities carry the label {step['label']}")
        elif op == "label_counts":
            parts.append(
                f"report how many named entities carry the label {step['label']}"
            )
        elif op == "verify":
            parts.append(
                f"report whether entity {step['entity']} carries the claimed label "
                f"{step['claim']}"
            )
        elif op == "scope":
            parts.append(
                f"collect every row where {_conditions_text(step['conditions'])}"
            )
        elif op == "set_list":
            if step["shape"] == "ids":
                parts.append("report every member's row id as a bare sorted list")
            elif step["shape"] == "ids_count":
                parts.append(
                    "report every member's row id as a sorted list with the member count"
                )
            else:
                parts.append(
                    "report the member count with the members grouped by entity"
                )
        elif op == "set_count":
            if step["shape"] == "count":
                parts.append("report how many rows the scope admits")
            else:
                parts.append(
                    "report how many rows the scope admits with per-entity counts"
                )
        elif op == "set_contains":
            row_id = step["id"]
            if step["shape"] == "contains_verdict":
                parts.append(f"report only whether row {row_id} is a member of the set")
            elif step["shape"] == "contains_entity":
                parts.append(
                    f"report whether row {row_id} is a member of the set, naming it "
                    "and its entity"
                )
            elif step["shape"] == "contains_count":
                parts.append(
                    f"report whether row {row_id} is a member of the set, with the "
                    "member count"
                )
            else:
                parts.append(
                    f"report whether row {row_id} is a member of the set, naming it"
                )
        elif op == "set_missing":
            if step["shape"] == "missing":
                parts.append(
                    "report as a bare sorted list which of the named row ids are not "
                    "members of the set"
                )
            elif step["shape"] == "missing_count":
                parts.append(
                    "report which of the named row ids are not members of the set, "
                    "with how many there are"
                )
            else:
                parts.append(
                    "report the sorted named row ids with which of them are not "
                    "members of the set"
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
    """K is sampled per task over a band around the cell value (as in capability_records)."""
    spread = max(2, consumed // 3)
    return [
        max(4, consumed + rng.randrange(-spread, spread + 1)) for _ in range(n_variants)
    ]


def plan_variants(length_records: int, consumed: int, requested: int) -> int:
    """How many K-sized tasks fit in L while leaving real distractor room.

    The multiplier is the widest per-family evidence cost, so a cell this
    accepts is hostable by every family even though the dispatch does not know
    which family will generate the world.
    """
    widest = max(EVIDENCE_MULTIPLIER.values())
    room = length_records - max(8, length_records // 10)
    return max(1, min(requested, room // max(4, (consumed * widest * 6) // 5)))


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def _alias_world(
    rng: random.Random, sizes: list[int], depth: int, seed: int
) -> tuple[list[Row], list[dict[str, Any]], dict[str, Any]]:
    """Rows, task specs and rendering extras for one alias_locate world."""
    categories = rng.sample(
        CATEGORY_POOL, rng.randint(4, min(len(CATEGORY_POOL), max(4, len(sizes) * 5)))
    )
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    handles = list(ALIAS_POOL)
    rng.shuffle(handles)
    used: set[str] = set()

    def next_handle() -> str:
        if not handles:
            raise ValueError("alias pool exhausted")
        return handles.pop()

    def record(day: str, amount: int, category: str, entity: str) -> Row:
        return Row(
            id=_new_id("r", rng, id_width),
            type="record",
            entity=entity,
            memo=_new_id("note-", rng, memo_width),
            amount=amount,
            category=category,
            day=day,
        )

    def declaration(entity: str) -> Row:
        return Row(
            id=_new_id("a", rng, id_width),
            type="alias",
            entity=entity,
            memo=_new_id("note-", rng, memo_width),
            alias=next_handle(),
        )

    rows: list[Row] = []
    specs: list[dict[str, Any]] = []
    terminals = ALIAS_TERMINALS_BY_DEPTH[depth]
    for index, size in enumerate(sizes):
        terminal = terminals[(seed + index) % len(terminals)]
        start = BASE_DATE + timedelta(days=index * (WINDOW_DAYS + GAP_DAYS))
        stop = start + timedelta(days=WINDOW_DAYS)
        floor = rng.randint(120, 880)
        category = rng.choice(categories)
        other = rng.choice([name for name in categories if name != category])
        entity = _fresh_entity(rng, used)
        twin = _fresh_entity(rng, used)
        target_declaration = declaration(entity)
        twin_declaration = declaration(twin)
        amounts = rng.sample(range(floor, floor + AMOUNT_CAP + 1), max(1, size))

        def day(start: date = start) -> str:
            return (start + timedelta(days=rng.randrange(WINDOW_DAYS))).isoformat()

        if terminal == "locate_empty":
            # No row of the bound entity carries the queried category while the
            # twin does: the empty answer is declared and binding-only.
            conditions = [
                {"field": "category", "op": "==", "value": other},
                {"field": "date", "op": ">=", "value": start.isoformat()},
                {"field": "date", "op": "<=", "value": stop.isoformat()},
            ]
            target_pattern = [
                (day(), amounts[position % len(amounts)], category)
                for position in range(size)
            ]
            twin_pattern = [
                (day(), rng.randint(floor, floor + AMOUNT_CAP), other)
                for _ in range(size)
            ]
        elif terminal == "locate_one":
            conditions = [
                {"field": "date", "op": ">=", "value": start.isoformat()},
                {"field": "date", "op": "<=", "value": stop.isoformat()},
                {"field": "amount", "op": ">=", "value": floor},
            ]
            target_pattern = [(day(), amounts[0], category)]
            target_pattern += [
                (day(), rng.randint(1, max(1, floor - 1)), category)
                for _ in range(size - 1)
            ]
            rng.shuffle(target_pattern)
            # The twin is isomorphic row for row: one row above the floor and
            # the rest below it, so a filter-only reader still cannot separate.
            twin_pattern = [(target_pattern[0][0], target_pattern[0][1], category)]
            twin_pattern += [
                (day(), rng.randint(1, max(1, floor - 1)), category)
                for _ in range(size - 1)
            ]
        else:
            conditions = [
                {"field": "date", "op": ">=", "value": start.isoformat()},
                {"field": "date", "op": "<=", "value": stop.isoformat()},
                {"field": "amount", "op": ">=", "value": floor},
            ]
            target_pattern = [
                (day(), amounts[position], category)
                for position in range(max(1, size - 1))
            ]
            twin_pattern = list(target_pattern)
        for pattern, owner in ((target_pattern, entity), (twin_pattern, twin)):
            for row_day, amount, row_category in pattern:
                rows.append(record(row_day, amount, row_category, owner))
        rows.append(target_declaration)
        rows.append(twin_declaration)
        specs.append(
            {
                "task_id": f"q{index}",
                "question": {
                    "family": "alias_locate",
                    "steps": [
                        {"op": "bind", "alias": target_declaration.alias},
                        *(
                            [{"op": "filter", "conditions": conditions}]
                            if depth >= 2
                            else []
                        ),
                        {"op": terminal},
                    ],
                },
                "bound_entity": entity,
                "twin_entity": twin,
                "declaration_row": target_declaration.id,
                "twin_declaration_row": twin_declaration.id,
                "queried_category": (other if terminal == "locate_empty" else category),
            }
        )
    return (
        rows,
        specs,
        {
            "header": None,
            "id_width": id_width,
            "memo_width": memo_width,
            "categories": categories,
        },
    )


def _asof_world(
    rng: random.Random, sizes: list[int], depth: int, seed: int
) -> tuple[list[Row], list[dict[str, Any]], dict[str, Any]]:
    """Rows, task specs and rendering extras for one asof_state world."""
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    used: set[str] = set()
    rows: list[Row] = []
    specs: list[dict[str, Any]] = []

    def event(entity: str, day: str, reveal: str, kind: str, amount: int) -> Row:
        return Row(
            id=_new_id("e", rng, id_width),
            type="event",
            entity=entity,
            memo=_new_id("note-", rng, memo_width),
            amount=amount,
            kind=kind,
            day=day,
            reveal=reveal,
        )

    for index, size in enumerate(sizes):
        terminal = ASOF_TERMINALS[(seed + index) % len(ASOF_TERMINALS)]
        start = BASE_DATE + timedelta(days=index * (WINDOW_DAYS + GAP_DAYS))
        earlier = (start + timedelta(days=WINDOW_DAYS // 2)).isoformat()
        later = (start + timedelta(days=WINDOW_DAYS + 8)).isoformat()
        named = [_fresh_entity(rng, used) for _ in range(rng.randint(2, 4))]

        def day_from(low: date, span: int) -> str:
            return (low + timedelta(days=rng.randrange(max(1, span)))).isoformat()

        # Every named entity's hold rows are revealed in the *opposite* order to
        # their event dates: each set_aside becomes visible only at the later
        # cutoff while each release is visible at the earlier one. That is what
        # makes event time and reveal time non-interchangeable -- a reveal-date
        # fold reaches a release before the set_aside that funds it, and the cap
        # then moves nothing -- while an event-date fold moves the reserved
        # amount. No other row of an entity touches held, so the hold rows carry
        # the answer and every entity contributes membership as well as value.
        #
        # Two shapes per world: a partial release (the hold survives the release
        # and both rows are decisive for the balance) and a full release with a
        # second set_aside after it (the hold is emptied and rebuilt, so the
        # entity's held value differs between the two readings even when the
        # balance contribution cancels).
        for position, entity in enumerate(named):
            reserved = rng.randint(30, 240)
            set_day = day_from(start, WINDOW_DAYS // 2)
            if position % 2 == 0:
                release_day = (
                    date.fromisoformat(set_day) + timedelta(days=rng.randrange(1, 6))
                ).isoformat()
                rows.append(event(entity, set_day, later, "set_aside", reserved))
                rows.append(
                    event(
                        entity,
                        release_day,
                        earlier,
                        "release",
                        reserved - rng.randint(1, max(1, reserved // 2)),
                    )
                )
            else:
                release_day = (
                    date.fromisoformat(set_day) + timedelta(days=rng.randrange(1, 6))
                ).isoformat()
                rows.append(event(entity, set_day, later, "set_aside", reserved))
                rows.append(
                    event(
                        entity,
                        release_day,
                        earlier,
                        "release",
                        reserved + rng.randint(0, 40),
                    )
                )
                rows.append(
                    event(
                        entity,
                        (
                            date.fromisoformat(release_day) + timedelta(days=1)
                        ).isoformat(),
                        later,
                        "set_aside",
                        rng.randint(20, 200),
                    )
                )
            rows.append(
                event(
                    entity, day_from(start, 8), earlier, "credit", rng.randint(10, 400)
                )
            )
            rows.append(
                event(
                    entity, day_from(start, 8), earlier, "debit", rng.randint(10, 200)
                )
            )
        # The rest of the visible budget duplicates events, one copy visible by
        # the earlier cutoff and one in the tail a delta step has to separate.
        # Duplicates are how the world stays inside its budget without inventing
        # new evidence: a duplicated row can never be individually decisive, so
        # the necessity claim stays honest about what carries the answer.
        # Duplicates carry no hold kind: a second hold would change held and
        # make a decisive row look decorative.
        base = size // 2 if depth >= 2 else size
        head = max(0, base - 4 * len(named))
        copies: list[Row] = []
        for position in range(head):
            entity = named[position % len(named)]
            copies.append(
                event(
                    entity,
                    day_from(start, WINDOW_DAYS + 8),
                    earlier,
                    KINDS[position % 2],
                    rng.randint(10, 300),
                )
            )
        rows.extend(copies)
        for position in range(max(0, size - 4 * len(named) - head)):
            template = copies[position % len(copies)] if copies else None
            if template is None:
                break
            rows.append(
                event(
                    template.entity,
                    template.day,
                    day_from(date.fromisoformat(earlier), 9),
                    template.kind,
                    template.amount,
                )
            )
        header = {
            "schema": VERSION,
            "family": "asof_state",
            "rules": PROTOCOLS["asof_state"],
        }
        steps: list[dict[str, Any]] = [{"op": "asof", "reveal": later}]
        if depth >= 2:
            steps = [
                {"op": "asof", "reveal": earlier},
                {"op": "delta", "reveal": later},
            ]
        steps.append(
            {"op": "balance", "entity": named[0]}
            if terminal == "balance"
            else {"op": terminal, "entities": list(named)}
        )
        question = {"family": "asof_state", "steps": steps}
        hidden: list[tuple[str, str]] = []
        # The guaranteed reveal flip: one visible row that is decisive for this
        # task's answer is revealed after the query date instead, so moving its
        # reveal back across the query date flips the answer. The row is chosen
        # so the world it leaves behind is still valid -- every named entity
        # keeps a decisive row of its own -- which is checked here on the moved
        # world rather than assumed.
        answer = _solve_rows(header, rows, question)
        decisive = _sensitivity(
            header, rows, question, answer, _asof_evidence(rows, question)
        )
        by_id = {row.id: row for row in rows}
        hidden_reveal = day_from(date.fromisoformat(later) + timedelta(days=1), 20)
        crossed: tuple[str, str, str] | None = None
        for row_id in decisive:
            moved = [
                replace(row, reveal=hidden_reveal) if row.id == row_id else row
                for row in rows
            ]
            moved_answer = _solve_rows(header, moved, question)
            if moved_answer == answer:
                continue
            moved_consumed = _asof_evidence(moved, question)
            if not _structurally_necessary(
                "asof_state",
                moved,
                question,
                _sensitivity(header, moved, question, moved_answer, moved_consumed),
            ):
                continue
            crossed = (row_id, by_id[row_id].kind, by_id[row_id].reveal)
            rows = moved
            break
        if crossed is None:
            raise ValueError(
                "no decisive row can cross the query date and keep the world valid"
            )
        hidden.append((crossed[0], crossed[1]))
        # Further hidden rows: revealed after the query date, so they are outside
        # every answer and are pure exposure.
        for position in range(max(3, size // 5)):
            entity = named[position % len(named)]
            kind = HOLD_KINDS[position % len(HOLD_KINDS)]
            row = event(
                entity,
                day_from(start, WINDOW_DAYS + 8),
                day_from(date.fromisoformat(later) + timedelta(days=1), 20),
                kind,
                rng.randint(10, 300),
            )
            rows.append(row)
            hidden.append((row.id, kind))
        specs.append(
            {
                "task_id": f"q{index}",
                "question": question,
                "named_entities": list(named),
                "hidden_events": hidden,
                "crossed_reveal": crossed[2],
                "later": later,
            }
        )
    return rows, specs, {"header": None, "id_width": id_width, "memo_width": memo_width}


def _reduced_point(
    rule_family: str,
    points: list[tuple[int, int]],
    modulus: int,
    rule: tuple[Any, ...],
) -> tuple[int, int]:
    """The feature signature a reader computes for one entity.

    threshold_class: the reduced point (sumX mod modulus, sumY mod modulus) the
    threshold is applied to. Record features carry an additive offset that is a
    multiple of the modulus, so an entity's reduced point lives in the same
    space as the demonstrations' single points. parity_vote: the vote pair (row
    count, rows in the counted residue class) the label is read off. This is the
    unit in which "the query entity's features were not demonstrated" is both a
    meaningful requirement and a checkable one.
    """
    if rule_family == "threshold_class":
        total = _total_features(points)
        return (total[0] % modulus, total[1] % modulus)
    counted = sum((x + y) % modulus == rule[0] for x, y in points)
    return (len(points), counted)


def _threshold_signature(
    rule: tuple[int, int, int, int], modulus: int
) -> tuple[bool, ...]:
    a, b, c, t = rule
    return tuple(
        (a * x + b * y + c) % modulus < t
        for y in range(modulus)
        for x in range(modulus)
    )


def _sample_threshold_rule(
    rng: random.Random, modulus: int
) -> tuple[int, int, int, int]:
    """A drawn rule, re-expressed as the enumerated representative of its function.

    Several parameter tuples induce the same label function, and the inference
    returns the enumeration's representative, so the world has to declare the
    representative too: otherwise the bundle would look like a rule mismatch to
    any validator that re-infers the rule from the demonstrations.
    """
    drawn = (
        rng.randrange(1, modulus),
        rng.randrange(1, modulus),
        rng.randrange(modulus),
        rng.randrange(1, modulus),
    )
    signature = _threshold_signature(drawn, modulus)
    return next(
        rule
        for rule in _threshold_hypotheses(modulus)
        if _threshold_signature(rule, modulus) == signature
    )


def _sample_demonstrations(
    rng: random.Random,
    rule_family: str,
    modulus: int,
    labels: list[str],
    rule: tuple[Any, ...],
) -> list[list[tuple[int, int]]]:
    """Demonstrations that identify the rule uniquely, by greedy splitting.

    The hypothesis space is enumerated by brute force and each round keeps the
    candidate demonstration that leaves the fewest consistent rules, so the
    published set is minimal-ish and its uniqueness is a measured property of
    the enumeration rather than an assumption.
    """
    if rule_family == "threshold_class":
        pool = [(x, y) for x in range(modulus) for y in range(modulus)]
        consistent: list[tuple[Any, ...]] = list(_threshold_hypotheses(modulus))
        candidates: list[list[tuple[int, int]]] = [[point] for point in pool]

        def predict(hyp: tuple[Any, ...], points: list[tuple[int, int]]) -> str:
            return _threshold_label(hyp, _total_features(points), labels, modulus)

    else:
        pool = [(x, y) for x in range(modulus) for y in range(modulus)]
        consistent = list(_parity_hypotheses())
        # Demonstrations carry more rows than any query entity (whose own rows
        # number at most six), so a demonstration's signature -- its row count
        # and its counted residue population -- can never coincide with a query
        # entity's. That keeps "the query's features were not demonstrated"
        # satisfiable while still being a real constraint. The candidate pool is
        # sampled rather than enumerated: the multiset space is in the hundreds
        # of thousands, and the greedy only has to find *a* separating
        # demonstration -- the uniqueness check below is what proves it did.
        candidates = [
            [rng.choice(pool) for _ in range(count)]
            for count in (5, 6, 7)
            for _ in range(120)
        ]

        def predict(hyp: tuple[Any, ...], points: list[tuple[int, int]]) -> str:
            return _parity_label(hyp, points, labels, modulus)

    rng.shuffle(candidates)
    chosen: list[list[tuple[int, int]]] = []
    rounds = 32 if rule_family == "parity_vote" else 12
    for _ in range(rounds):
        if len(consistent) == 1:
            break
        best: tuple[int, list[tuple[int, int]], str] | None = None
        for candidate in candidates:
            if candidate in chosen:
                continue
            label = predict(rule, candidate)
            kept = sum(predict(hyp, candidate) == label for hyp in consistent)
            if kept < len(consistent) and (best is None or kept < best[0]):
                best = (kept, candidate, label)
        if best is None:
            break
        chosen.append(best[1])
        consistent = [hyp for hyp in consistent if predict(hyp, best[1]) == best[2]]
    if len(consistent) != 1:
        raise ValueError("demonstrations must identify exactly one rule")
    if rule_family == "parity_vote":
        # Every parity rule agrees on every even-counted demonstration, so a
        # uniquely identifying set must contain at least one odd-counted one:
        # that demonstration is what separates the two flips from each other.
        residue, flip = consistent[0]
        if all(
            predict((residue, 1 - flip), points) == predict((residue, flip), points)
            for points in chosen
        ):
            raise ValueError("the demonstrations leave the parity flip undetermined")
    return chosen


def _rule_rows(
    rng: random.Random,
    points: list[tuple[int, int]],
    offset: int,
    id_width: int,
    memo_width: int,
    entity: str,
) -> list[Row]:
    """One entity's record rows, features shifted above the demonstrated domain."""
    return [
        Row(
            id=_new_id("r", rng, id_width),
            type="record",
            entity=entity,
            memo=_new_id("note-", rng, memo_width),
            x=x + offset,
            y=y + offset,
        )
        for x, y in points
    ]


def _parity_entity(
    rng: random.Random,
    modulus: int,
    rule: tuple[Any, ...],
    side: int,
    seen: set[tuple[int, int]],
) -> list[tuple[int, int]] | None:
    """A parity entity: every counted row flips the counted parity when dropped.

    The counted residue class is fixed by the rule, so the entity's signature is
    (row count, rows in that class) and the label is that pair's parity. Both
    parities are reachable, and a padding row outside the class moves the row
    count without moving the counted population, which is the freedom that keeps
    the entity off the demonstrated signatures.
    """
    residue, _ = rule
    counted = 1 if side == 1 else 2
    cells = [
        (x, y)
        for x in range(modulus)
        for y in range(modulus)
        if (x + y) % modulus == residue
    ]
    others = [
        (x, y)
        for x in range(modulus)
        for y in range(modulus)
        if (x + y) % modulus != residue
    ]
    for size in range(max(2, counted), 7):
        candidates: list[list[tuple[int, int]]] = [
            [rng.choice(cells) for _ in range(counted)]
            + [rng.choice(others) for _ in range(size - counted)]
        ]
        for _ in range(24):
            candidates.append(
                [rng.choice(cells) for _ in range(counted)]
                + [rng.choice(others) for _ in range(size - counted)]
            )
        for points in candidates:
            rng.shuffle(points)
            if _reduced_point("parity_vote", points, modulus, rule) in seen:
                continue
            return points
    return None


def _threshold_entity(
    rng: random.Random,
    modulus: int,
    rule: tuple[int, int, int, int],
    side: int,
    seen: set[tuple[int, int]],
) -> list[tuple[int, int]] | None:
    """A threshold entity: dropping any row crosses the threshold.

    Written out, the requirement is that every row's own contribution k_i lands
    the total on the other side of the threshold: with V the entity's residue,
    each k_i must satisfy (V - k_i) mod modulus < t exactly when V is not below
    t. So the row contributions are drawn from that feasible set and the y
    coordinates stay free, expressed through x = a^-1 (k - b y) so that the
    row's contribution is exactly k. Sampling the contributions instead of the
    points turns a search over a tiny condition into a direct construction.
    """
    a, b, c, t = rule
    inverse = pow(a, -1, modulus)
    wanted = side == 0
    values = [v for v in range(modulus) if (v < t) == wanted]
    for size in (1, 2, 3, 4):
        for _ in range(40):
            value = rng.choice(values)
            feasible = [
                k
                for k in range(1, modulus)
                if ((value - k) % modulus < t) != (value < t)
            ]
            if not feasible:
                continue
            picks = []
            remaining = (value - c) % modulus
            for position in range(size):
                options = (
                    [k for k in feasible if (remaining - k) % modulus in feasible]
                    if size == 1
                    else feasible
                )
                if position == size - 1:
                    options = [k for k in feasible if k == remaining]
                if not options:
                    break
                k = rng.choice(options)
                remaining = (remaining - k) % modulus
                y = rng.randrange(modulus)
                picks.append(((inverse * (k - b * y)) % modulus, y))
            if len(picks) != size:
                continue
            if _reduced_point("threshold_class", picks, modulus, rule) in seen:
                continue
            return picks
    return None


def _rule_entity_rows(
    rng: random.Random,
    rule_family: str,
    modulus: int,
    rule: tuple[Any, ...],
    offset: int,
    side: int,
    seen: set[tuple[int, int]],
    id_width: int,
    memo_width: int,
    entity: str,
) -> list[Row] | None:
    """One entity whose every counted row is individually decisive for its label.

    The demonstration feature points live in [0, modulus), the record features
    start above a modulus multiple of that domain, so no record reads a
    demonstrated feature point; the entity's own signature must stay off `seen`,
    which holds the signatures the demonstrations publish.
    """
    if rule_family == "parity_vote":
        points = _parity_entity(rng, modulus, rule, side, seen)
    else:
        points = _threshold_entity(rng, modulus, rule, side, seen)
    if points is None:
        return None
    return _rule_rows(rng, points, offset, id_width, memo_width, entity)


def _rule_world(
    rng: random.Random, sizes: list[int], depth: int, seed: int
) -> tuple[list[Row], list[dict[str, Any]], dict[str, Any]]:
    """Rows, task specs and rendering extras for one rule_holdout world."""
    rule_family = RULE_FAMILIES[seed % len(RULE_FAMILIES)]
    modulus = (
        PARITY_MODULUS if rule_family == "parity_vote" else rng.choice(MODULUS_POOL)
    )
    labels = rng.sample(LABEL_POOL, 2)
    rule: tuple[Any, ...] = (
        _sample_threshold_rule(rng, modulus)
        if rule_family == "threshold_class"
        else (0, 0)
    )
    offset = modulus * rng.randint(3, 9)
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    demonstrations = _sample_demonstrations(rng, rule_family, modulus, labels, rule)
    rows: list[Row] = []
    for index, points in enumerate(demonstrations):
        rows.append(
            Row(
                id=_new_id("d", rng, id_width),
                type="demo",
                entity=f"demo-{index}",
                memo=_new_id("note-", rng, memo_width),
                points=tuple(points),
                label=(
                    _threshold_label(rule, _total_features(points), labels, modulus)
                    if rule_family == "threshold_class"
                    else _parity_label(rule, list(points), labels, modulus)
                ),
            )
        )
    # The feature signatures the demonstrations publish: every query entity has
    # to stay off this set, so the label is never read off a demonstrated point.
    seen = {
        _reduced_point(rule_family, points, modulus, rule) for points in demonstrations
    }
    used: set[str] = set()
    specs: list[dict[str, Any]] = []
    terminals = RULE_TERMINALS_BY_DEPTH[depth]
    for index, size in enumerate(sizes):
        terminal = terminals[(seed + index) % len(terminals)]
        # Two entities at least (a one-entity answer would be a constant), and
        # a width that varies per task: the group-level shapes carry the entity
        # count, so a narrow draw would pin their rendered width.
        count = rng.randint(2, max(2, min(8, max(4, size) // 2)))
        named: list[str] = []
        # Every task names entities on both sides of the rule: a group-level
        # answer always carries both labels, so no task is a constant answer
        # with a decorative evidence trail.
        sides = [0, 1, *(rng.randint(0, 1) for _ in range(max(0, count - 2)))]
        rng.shuffle(sides)
        if terminal == "label":
            # A single-entity query still carries a named twin whose label is
            # the other one, so even that terminal's world has both labels.
            count = max(2, count)
        for position in range(count):
            entity = _fresh_entity(rng, used)
            entity_rows = _rule_entity_rows(
                rng,
                rule_family,
                modulus,
                rule,
                offset,
                sides[position],
                seen,
                id_width,
                memo_width,
                entity,
            )
            if entity_rows is None:
                raise ValueError("could not build a decisive entity")
            rows.extend(entity_rows)
            named.append(entity)
        steps: list[dict[str, Any]] = [
            {"op": "learn", "rule_family": rule_family},
            *([{"op": "aggregate"}] if depth >= 2 else []),
        ]
        if terminal == "label":
            steps.append({"op": "label", "entity": named[0]})
        elif terminal == "verify":
            actual = _rule_label(
                rule_family, rule, _rows_for(rows, named[0]), modulus, labels
            )
            claim = actual if rng.randrange(2) else labels[1 - labels.index(actual)]
            steps.append({"op": "verify", "entity": named[0], "claim": claim})
        elif terminal in ("labels", "label_list"):
            steps.append({"op": terminal, "entities": list(named)})
        else:
            steps.append(
                {
                    "op": terminal,
                    "entities": list(named),
                    "label": labels[(seed + index) % 2],
                }
            )
        specs.append(
            {
                "task_id": f"q{index}",
                "question": {
                    "family": "rule_holdout",
                    "rule_family": rule_family,
                    "steps": steps,
                },
            }
        )
    header = {
        "rule_family": rule_family,
        "modulus": modulus,
        "labels": list(labels),
        "rule_structure": RULE_STRUCTURES[rule_family],
    }
    return (
        rows,
        specs,
        {
            "header": header,
            "id_width": id_width,
            "memo_width": memo_width,
            "rule": rule,
        },
    )


# --------------------------------------------------------------------------
# set_complete generation
# --------------------------------------------------------------------------


def _set_world(
    rng: random.Random, sizes: list[int], depth: int, seed: int
) -> tuple[list[Row], list[dict[str, Any]], dict[str, Any]]:
    """Rows, task specs and rendering extras for one set_complete world.

    Each task pins a scope (2 to 4 conditions) and builds exactly the rows it
    needs: K members drawn from a fresh entity population, same-schema
    siblings that fail exactly one condition, and the insert-hit row -- a
    legal hit injected into a region of the condition space no member covers
    (a disjoint date window or a fresh entity's rows), which must enter the
    answer when the intervention re-renders it inside the scope. Every task
    runs over the whole world, so another task's members are its siblings:
    the exhaustiveness is real, not staged.
    """
    categories = rng.sample(
        CATEGORY_POOL, rng.randint(4, min(len(CATEGORY_POOL), max(4, len(sizes) * 5)))
    )
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    used: set[str] = set()
    rows: list[Row] = []
    specs: list[dict[str, Any]] = []

    def record(day: str, amount: int, category: str, entity: str) -> Row:
        return Row(
            id=_new_id("r", rng, id_width),
            type="record",
            entity=entity,
            memo=_new_id("note-", rng, memo_width),
            amount=amount,
            category=category,
            day=day,
        )

    for index, size in enumerate(sizes):
        terminal = SET_TERMINALS[(seed + index) % len(SET_TERMINALS)]
        # The shape rotation is coupled to the terminal rotation but a period
        # off it, so a seed sweep covers every (terminal, shape) pair while the
        # bare-count shape -- arity-invisible when masked -- is diluted by the
        # arity-rich dict shapes landing on the same terminal.
        shape = SET_SHAPES[terminal][
            (seed // len(SET_TERMINALS) + index) % len(SET_SHAPES[terminal])
        ]
        # The unordered pin is its own rotation axis, decoupled from the
        # terminal rotation: else one parity of seed pins the whole sweep's
        # set_list tasks to a single presentation order.
        unordered = bool((seed // len(SET_TERMINALS) + index) % 2)
        start = BASE_DATE + timedelta(days=index * (WINDOW_DAYS + GAP_DAYS))
        stop = start + timedelta(days=WINDOW_DAYS)
        floor = rng.randint(120, 880)
        cap = rng.randint(floor + 40, floor + AMOUNT_CAP)
        category = rng.choice(categories)
        members: list[Row] = []
        # Members share entities in small groups (2 to 6 entities hold the K
        # rows), so the grouped answer shapes carry a real group structure
        # rather than one row per entity.
        member_entities = max(2, min(6, size // 3))
        entities = []
        for _ in range(member_entities):
            entity = f"unit-{rng.getrandbits(20):05x}"
            while entity in used:
                entity = f"unit-{rng.getrandbits(20):05x}"
            used.add(entity)
            entities.append(entity)
        for position in range(size):
            members.append(
                record(
                    (start + timedelta(days=rng.randrange(WINDOW_DAYS))).isoformat(),
                    rng.randint(floor, cap),
                    category,
                    entities[position % member_entities],
                )
            )
        conditions: list[dict[str, Any]] = [
            {"field": "category", "op": "==", "value": category},
            {"field": "date", "op": ">=", "value": start.isoformat()},
            {"field": "date", "op": "<=", "value": stop.isoformat()},
            {"field": "amount", "op": ">=", "value": floor},
            {"field": "amount", "op": "<=", "value": cap},
        ]
        if rng.randrange(2):
            rng.shuffle(conditions)
        conditions = conditions[: rng.choice((2, 3, 4))]
        if not any(
            condition["field"] == "category" and condition["op"] == "=="
            for condition in conditions
        ):
            conditions[0] = {"field": "category", "op": "==", "value": category}
        # Siblings: same schema, same category-family, fail exactly one
        # condition. One sibling population per dropped condition axis.
        siblings: list[Row] = []
        sibling_count = max(4, size // 2)
        other = rng.choice([name for name in categories if name != category])
        for position in range(sibling_count):
            entity = f"unit-{rng.getrandbits(20):05x}"
            while entity in used:
                entity = f"unit-{rng.getrandbits(20):05x}"
            used.add(entity)
            if position % 3 == 0:
                # Wrong category, inside the date and amount range.
                siblings.append(
                    record(
                        (
                            start + timedelta(days=rng.randrange(WINDOW_DAYS))
                        ).isoformat(),
                        rng.randint(floor, cap),
                        other,
                        entity,
                    )
                )
            elif position % 3 == 1:
                # Outside the date window (before it), inside amount and category.
                siblings.append(
                    record(
                        (
                            start - timedelta(days=rng.randrange(1, GAP_DAYS))
                        ).isoformat(),
                        rng.randint(floor, cap),
                        category,
                        entity,
                    )
                )
            else:
                # Outside the amount range, inside date and category.
                siblings.append(
                    record(
                        (
                            start + timedelta(days=rng.randrange(WINDOW_DAYS))
                        ).isoformat(),
                        rng.randint(1, max(1, floor - 1)),
                        category,
                        entity,
                    )
                )
        # The insert-hit row: a legal hit in a previously uncovered region of
        # the text -- a fresh entity carrying the queried category, a date
        # before the window, an amount above the cap. It is built against the
        # *final* condition list (the trim may have dropped the bounds it was
        # planned to violate), and it must satisfy every condition but one so
        # the repair moves exactly one field and the answer must take it.
        kept = {(condition["field"], condition["op"]) for condition in conditions}

        def region_value(field: str, op: str, inside: bool) -> Any:
            if field == "date":
                if op == ">=":
                    return (
                        (start + timedelta(days=rng.randrange(WINDOW_DAYS))).isoformat()
                        if inside
                        else (
                            start - timedelta(days=rng.randrange(1, GAP_DAYS))
                        ).isoformat()
                    )
                return (
                    (start + timedelta(days=rng.randrange(WINDOW_DAYS))).isoformat()
                    if inside
                    else (stop + timedelta(days=rng.randrange(1, GAP_DAYS))).isoformat()
                )
            if field == "amount":
                if op == ">=":
                    return (
                        rng.randint(floor, cap)
                        if inside
                        else rng.randint(1, max(1, floor - 1))
                    )
                return rng.randint(floor, cap) if inside else cap + rng.randint(1, 60)
            return category if inside else other

        insert_entity = f"unit-{rng.getrandbits(20):05x}"
        while insert_entity in used:
            insert_entity = f"unit-{rng.getrandbits(20):05x}"
        used.add(insert_entity)
        # Violate exactly one kept condition axis, chosen among those the row
        # can cross; every other kept condition is satisfied.
        candidates = sorted(kept)
        rng.shuffle(candidates)
        violated, insert_day, insert_amount, insert_category = (
            None,
            (start + timedelta(days=rng.randrange(WINDOW_DAYS))).isoformat(),
            rng.randint(floor, cap),
            category,
        )
        for field, op in candidates:
            if (field, op) == ("category", "=="):
                continue
            violated = (field, op)
            value = region_value(field, op, inside=False)
            if field == "date":
                insert_day = value
            else:
                insert_amount = value
            break
        if violated is None:
            # Only the category condition was kept: the insert row carries the
            # sibling category, and the repair moves it across that boundary.
            insert_category = other
        insert_row = record(insert_day, insert_amount, insert_category, insert_entity)
        # 50% of rows present the member set unordered; the answer is sorted.
        # The pin above covers both parities across a seed sweep and both
        # within every world of >=2 variants (an all-coin world would leave a
        # quarter of 2-variant worlds all-ordered).
        presented: list[Row] = list(members) + list(siblings) + [insert_row]
        if unordered:
            rng.shuffle(presented)
        rows.extend(presented)
        steps = [
            {"op": "scope", "conditions": conditions},
            {"op": terminal, "shape": shape},
        ]
        if terminal in ("set_contains", "set_missing"):
            # Named rows span both sides of the boundary: members, a sibling
            # that violates a kept condition, and the insert-hit row (itself a
            # non-member until repaired) -- so the verdict carries a real
            # discriminator, the boundary row is never sliced out, and the
            # insert intervention moves a *named* row across the boundary.
            boundary_sibling = next(
                sibling
                for sibling in siblings
                if any(not _holds(sibling, condition) for condition in conditions)
            )
            probe_members = [
                member.id for member in members[: max(2, min(5, size // 3))]
            ]
            rng.shuffle(probe_members)
            if terminal == "set_contains":
                # The queried row is the insert-hit row: its verdict is False
                # as rendered, and repairing it into the scope flips the named
                # row's own membership -- the intervention is about the row it
                # asks for, not one the repair happens to add elsewhere. The
                # sibling stays in the world as the discriminator a reader
                # must not take. The contains_count shape may instead name a
                # member (verdict True), because its count key keeps the
                # repair flip observable either way; the id-only and bare
                # verdict shapes stay pinned to the insert row, whose own
                # membership the repair flips.
                if shape == "contains_count" and rng.randrange(2):
                    steps[1]["id"] = probe_members[0]
                else:
                    steps[1]["id"] = insert_row.id
            else:
                # The named-probe arity is a shape axis: 2 to 6 members plus a
                # drawn 1 to 3 of the boundary siblings plus the insert row,
                # so both the named arity and the missing arity vary per task.
                boundary_rows = [
                    sibling.id
                    for sibling in siblings
                    if any(not _holds(sibling, condition) for condition in conditions)
                ]
                rng.shuffle(boundary_rows)
                probe_count = rng.randint(2, 4)
                boundary_count = rng.randint(1, min(3, len(boundary_rows)))
                steps[1]["ids"] = (
                    probe_members[:probe_count]
                    + boundary_rows[:boundary_count]
                    + [insert_row.id]
                )
        question = {
            "family": "set_complete",
            "steps": steps,
            "unordered": unordered,
        }
        specs.append(
            {
                "task_id": f"q{index}",
                "question": question,
                "sibling_rows": [sibling.id for sibling in siblings],
                "insert_row": insert_row.id,
                "scope": {
                    "conditions": [dict(condition) for condition in conditions],
                },
            }
        )
    return (
        rows,
        specs,
        {
            "header": None,
            "id_width": id_width,
            "memo_width": memo_width,
            "categories": categories,
        },
    )


def _padding_rows(
    rng: random.Random, family: str, count: int, extra: dict[str, Any], used: set[str]
) -> list[Row]:
    """Same-schema rows that are exposure, never evidence."""
    id_width = extra.get("id_width", 16)
    memo_width = extra.get("memo_width", 32)
    categories = extra.get("categories") or list(CATEGORY_POOL[:8])
    rows = []
    for index in range(count):
        entity = _fresh_entity(rng, used)
        if family == "alias_locate":
            rows.append(
                Row(
                    id=_new_id("r", rng, id_width),
                    type="record",
                    entity=entity,
                    memo=_new_id("note-", rng, memo_width),
                    amount=rng.randint(1, 999),
                    category=categories[index % len(categories)],
                    day=(BASE_DATE + timedelta(days=index)).isoformat(),
                )
            )
        elif family == "asof_state":
            day = BASE_DATE + timedelta(days=index * 3)
            rows.append(
                Row(
                    id=_new_id("e", rng, id_width),
                    type="event",
                    entity=entity,
                    memo=_new_id("note-", rng, memo_width),
                    amount=rng.randint(1, 400),
                    kind=KINDS[index % len(KINDS)],
                    day=day.isoformat(),
                    reveal=(day + timedelta(days=60)).isoformat(),
                )
            )
        elif family == "set_complete":
            rows.append(
                Row(
                    id=_new_id("r", rng, id_width),
                    type="record",
                    entity=entity,
                    memo=_new_id("note-", rng, memo_width),
                    amount=rng.randint(1, 999),
                    category=categories[index % len(categories)],
                    day=(
                        BASE_DATE + timedelta(days=rng.randrange(-90, 90))
                    ).isoformat(),
                )
            )
        else:
            offset = (2 if family == "parity_vote" else 3) * rng.randint(3, 9)
            rows.append(
                Row(
                    id=_new_id("r", rng, id_width),
                    type="record",
                    entity=entity,
                    memo=_new_id("note-", rng, memo_width),
                    x=rng.randint(offset, offset + 9),
                    y=rng.randint(offset, offset + 9),
                )
            )
    return rows


def generate_world(
    seed: int,
    family: str = "alias_locate",
    length_records: int = 800,
    consumed_records: int = 60,
    depth: int = 2,
    n_variants: int = 4,
) -> dict[str, Any]:
    """Build one capability world: L primary rows, one typed program per variant, executed gold."""
    if family not in PROTOCOLS:
        raise ValueError("unsupported family")
    if type(length_records) is not int or length_records < 32:
        raise ValueError("length_records must be an integer >= 32")
    if type(consumed_records) is not int or consumed_records < 4:
        raise ValueError("consumed_records must be an integer >= 4")
    if type(depth) is not int or not 1 <= depth <= 2:
        raise ValueError("depth must be 1 or 2")
    if type(n_variants) is not int or not 1 <= n_variants <= 8:
        raise ValueError("n_variants must be 1..8")
    if n_variants != plan_variants(length_records, consumed_records, n_variants):
        raise ValueError(
            "K budget does not fit inside L; shrink K or the variant count"
        )
    rng = random.Random(seed)
    sizes = _split_sizes(rng, consumed_records, n_variants)
    if family == "alias_locate":
        rows, specs, extra = _alias_world(rng, sizes, depth, seed)
    elif family == "asof_state":
        rows, specs, extra = _asof_world(rng, sizes, depth, seed)
    elif family == "set_complete":
        if depth != 1:
            raise ValueError("set_complete is an H=1 family; only depth 1 is generated")
        rows, specs, extra = _set_world(rng, sizes, depth, seed)
    else:
        rows, specs, extra = _rule_world(rng, sizes, depth, seed)
    primary = sum(1 for row in rows if row.type in PRIMARY_TYPES)
    padding = length_records - primary
    if padding < 1:
        raise ValueError("no distractor budget left; shrink K or the variant count")
    used = {row.entity for row in rows}
    rows.extend(_padding_rows(rng, family, padding, extra, used))
    rng.shuffle(rows)
    context = render_context(rows, family, extra.get("header"))
    header = json.loads(context.splitlines()[0])
    tasks = []
    for spec in specs:
        question = spec["question"]
        answer = _solve_rows(header, rows, question)
        consumed = _executor_evidence(rows, question)
        necessary = _sensitivity(header, rows, question, answer, consumed)
        if not necessary:
            raise ValueError("no consumed row is individually decisive")
        if not _structurally_necessary(family, rows, question, necessary):
            raise ValueError("the family's decisive row is not individually decisive")
        intervention = _intervention(family, header, rows, question, answer, spec)
        phrasing_index = rng.randrange(len(PROMPTS[family]))
        tasks.append(
            {
                "task_id": spec["task_id"],
                "capability": family,
                "capability_level": CAPABILITY_LEVELS[family],
                "question": question,
                "instruction": render_instruction(family, question, phrasing_index),
                "phrasing_index": phrasing_index,
                "answer": answer,
                "consumed": consumed,
                "consumed_count": len(consumed),
                "necessary": necessary,
                "intervention": intervention,
                **(
                    {"queried_category": spec["queried_category"]}
                    if family == "alias_locate"
                    else {}
                ),
            }
        )
    consumed_rows = {row_id for task in tasks for row_id in task["consumed"]}
    primary_consumed = sum(
        1 for row in rows if row.id in consumed_rows and row.type in PRIMARY_TYPES
    )
    accounting = {
        "record_rows": length_records,
        "reference_rows": len(rows) - length_records,
        "rendered_rows": len(rows),
        "consumed_rows_per_task": [task["consumed_count"] for task in tasks],
        "consumed_primary_rows": primary_consumed,
        "padding_rows": length_records - primary_consumed,
        "decoy_padding_rows": _decoys(family, specs, extra),
        "padding_is_exposure_not_semantic_scale": True,
    }
    identity = _dump(
        [VERSION, seed, family, length_records, consumed_records, depth, n_variants]
    )
    bundle: dict[str, Any] = {
        "schema_version": VERSION,
        "world_id": VERSION + "-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
        "seed": seed,
        "family": family,
        "capability_level": CAPABILITY_LEVELS[family],
        "length_records": length_records,
        "consumed_records": consumed_records,
        "depth": depth,
        "n_variants": n_variants,
        "tasks": tasks,
        "context": context,
        "length_accounting": accounting,
        "honesty": dict(HONESTY),
    }
    if family == "rule_holdout":
        bundle["rule"] = {
            "rule_family": extra["header"]["rule_family"],
            "modulus": extra["header"]["modulus"],
            "labels": extra["header"]["labels"],
            "parameters": list(extra["rule"]),
            "hypotheses_enumerated": _hypothesis_count(
                extra["header"]["rule_family"], extra["header"]["modulus"]
            ),
            "holdout": holdout_plan(extra["header"]["rule_family"]),
        }
    return bundle


def _executor_evidence(rows: list[Row], question: dict[str, Any]) -> list[str]:
    """Row ids the program's terminal step actually reads (executor-derived)."""
    family = question["family"]
    if family == "alias_locate":
        return _alias_evidence(rows, question)
    if family == "asof_state":
        return _asof_evidence(rows, question)
    if family == "rule_holdout":
        return _rule_evidence(rows, question)
    if family == "set_complete":
        return _set_evidence(rows, question)
    raise ValueError("unsupported family")


def _differs(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    order: str = "event",
) -> bool:
    """Does the intervened world change the answer, or stop answering at all?

    A family executor fails closed -- a single-match query that no longer
    locates exactly one row raises rather than returning a set -- so a raised
    ValueError counts as a change, exactly as capability_records treats a
    filter that empties the relation.
    """
    try:
        return _solve_rows(header, rows, question, order) != answer
    except ValueError:
        return True


def _sensitivity(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    consumed: list[str],
) -> list[str]:
    """Which consumed rows move the answer when dropped, measured by re-execution.

    The probe is applied to the parsed rows; every row is one rendered line, so
    dropping the row is the line deletion capability_records performs, and
    validate_bundle() re-derives exactly this partition.
    """
    return sorted(
        row_id
        for row_id in consumed
        if _differs(header, [row for row in rows if row.id != row_id], question, answer)
    )


def _structurally_necessary(
    family: str, rows: list[Row], question: dict[str, Any], necessary: list[str]
) -> bool:
    """The family's own decisive-row claim, checked against the measured set."""
    decisive = set(necessary)
    if family == "alias_locate":
        # The binding row must be decisive: without it there is no entity.
        declaration, _ = _alias_bound(rows, _alias_steps(question))
        return declaration.id in decisive
    if family == "asof_state":
        by_id = {row.id: row for row in rows}
        # Every entity the answer covers must contribute at least one decisive
        # row of its own: no name is answered for free.
        for entity in _asof_named(_asof_steps(question)[-1]):
            if not any(by_id[row_id].entity == entity for row_id in decisive):
                return False
        # When the task reads a set-aside or release row, at least one of those
        # rows must be decisive: the capped release propagation is the family's
        # state semantics, not decoration.
        holds = [
            row_id
            for row_id in _asof_evidence(rows, question)
            if by_id[row_id].kind in HOLD_KINDS
        ]
        return not holds or any(row_id in decisive for row_id in holds)
    if family == "set_complete":
        # The exhaustive gold means every member is individually decisive for
        # a set answer: removing any one of them shrinks the complete set,
        # which the measured partition must confirm row by row. A contains
        # task names one row, so that row must be decisive: the verdict flips
        # with its own fields. A missing task names rows on both sides of the
        # boundary, so at least one named row of each kind must be decisive.
        terminal = _set_steps(question)[-1]
        members = set(row.id for row in _set_members(rows, _set_steps(question)))
        if terminal["op"] == "set_contains":
            return terminal["id"] in decisive
        if terminal["op"] == "set_missing":
            named = set(terminal["ids"])
            decisive_named = named & decisive
            return bool(decisive_named & members) and bool(decisive_named - members)
        return members <= decisive
    terminal = _rule_steps(question)[-1]
    named = (
        [terminal.get("entity")]
        if terminal["op"] in ("label", "verify")
        else list(terminal.get("entities") or [])
    )
    # Every named entity must carry at least one decisive row of its own: the
    # label is not free.
    by_id = {row.id: row for row in rows}
    for entity in named:
        if not any(by_id[row_id].entity == entity for row_id in decisive):
            return False
    return True


def _decoys(family: str, specs: list[dict[str, Any]], extra: dict[str, Any]) -> int:
    """Rows built to be confusable with the evidence but never read by it."""
    if family == "alias_locate":
        return sum(
            1 for spec in specs for _ in (spec["bound_entity"], spec["twin_entity"])
        )
    if family == "asof_state":
        return sum(len(spec["hidden_events"]) for spec in specs)
    if family == "set_complete":
        # Every task's sibling population plus its insert-hit row are built to
        # be confusable with the membership but never read by it.
        return sum(
            len(spec["sibling_rows"]) + 1 for spec in specs if spec.get("sibling_rows")
        )
    return 0


def _intervention(
    family: str,
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """The family's own binding/time intervention, measured at generation time."""
    if family == "alias_locate":
        return _alias_intervention(header, rows, question, answer, spec)
    if family == "asof_state":
        return _asof_intervention(header, rows, question, answer, spec)
    if family == "set_complete":
        return _set_intervention(header, rows, question, answer, spec)
    return _rule_intervention(header, rows, question, answer)


def _alias_intervention(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Swapping two entities' alias declarations must flip the answer."""
    target, partner = spec["bound_entity"], spec["twin_entity"]
    swapped = []
    for row in rows:
        if row.id == spec["declaration_row"]:
            swapped.append(replace(row, entity=partner))
        elif row.id == spec["twin_declaration_row"]:
            swapped.append(replace(row, entity=target))
        else:
            swapped.append(row)
    if not _differs(header, swapped, question, answer):
        raise ValueError("swapping the alias declarations did not flip the answer")
    return {
        "kind": "alias_swap",
        "rows": [spec["declaration_row"], spec["twin_declaration_row"]],
        "entities": [target, partner],
    }


def _asof_intervention(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Moving one reveal date across the query date must flip the answer."""
    by_id = {row.id: row for row in rows}
    for row_id, kind in spec["hidden_events"]:
        row = by_id[row_id]
        moved = [
            replace(candidate, reveal=spec["crossed_reveal"])
            if candidate.id == row_id
            else candidate
            for candidate in rows
        ]
        if _differs(header, moved, question, answer):
            return {
                "kind": "reveal_flip",
                "row": row_id,
                "event_kind": kind,
                "before": row.reveal,
                "after": spec["crossed_reveal"],
            }
    raise ValueError("no reveal date crosses the query date and flips the answer")


def _rule_intervention(
    header: dict[str, Any], rows: list[Row], question: dict[str, Any], answer: Any
) -> dict[str, Any]:
    """Removing every demonstration must break the inferred rule."""
    demos = sorted(row.id for row in rows if row.type == "demo")
    reduced = [row for row in rows if row.type != "demo"]
    if not _differs(header, reduced, question, answer):
        raise ValueError("removing every demonstration did not change the answer")
    return {"kind": "demo_removal", "rows": demos}


def _set_intervention(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Inserting a legal hit into an uncovered region must enter the answer.

    The insert-hit row (a fresh entity's row that satisfies every condition
    but one and sits in a region the members do not cover: before the window,
    above the cap) is repaired into the scope -- the one violated field is
    moved inside -- and the answer must flip: the complete set grows by
    exactly that row. The question already names the insert row (the
    generation draws it among the probes), so a set answer takes it, a
    missing answer drops it, and a contains verdict flips with it. The one
    exception is the bare-verdict shape, which cannot carry an id: its probe
    is widened to the id-bearing shape for the moved world only, because a
    bare True is not distinguishable from a bare True for another row.
    """
    insert_id = spec["insert_row"]
    scope = spec["scope"]["conditions"]
    bounds: dict[tuple[str, str], str | int | None] = {
        (condition["field"], condition["op"]): condition["value"] for condition in scope
    }
    moved = []
    for row in rows:
        if row.id != insert_id:
            moved.append(row)
            continue
        day = row.day
        low = bounds.get(("date", ">="))
        if low is not None and day < low:
            day = low
        high = bounds.get(("date", "<="))
        if high is not None and day > high:
            day = high
        amount = row.amount
        floor = bounds.get(("amount", ">="))
        ceil = bounds.get(("amount", "<="))
        if floor is not None and amount < floor:
            amount = floor
        if ceil is not None and amount > ceil:
            amount = ceil
        category = row.category
        wanted = bounds.get(("category", "=="))
        if wanted is not None and category != wanted:
            category = wanted
        moved.append(replace(row, day=day, amount=amount, category=category))
    terminal = question["steps"][-1]
    if terminal["op"] == "set_contains" and terminal["shape"] == "contains_verdict":
        # The bare verdict cannot carry the hit's id; widen the probe for the
        # moved world so the flip is observable.
        moved_question = {
            **question,
            "steps": [
                question["steps"][0],
                dict(terminal, id=insert_id, shape="contains"),
            ],
        }
    else:
        moved_question = question
    if not _differs(header, moved, moved_question, answer):
        raise ValueError("inserting the legal hit did not change the answer")
    return {
        "kind": "insert_hit",
        "row": insert_id,
        "conditions": [dict(condition) for condition in scope],
    }


def answer_value(answer: Any) -> Any:
    """The computed-value projection of an answer.

    Unlike capability_records this projection is the identity: every family's
    answer either *is* the computed state (a balance, a total, an active set, a
    label map, a located id set) or carries it verbatim, so there is no
    id-bearing field that would make remove-one vacuous by construction. The
    remaining question -- whether a consumed row is individually decisive -- is
    answered by the measured sensitivity partition rather than by an assumed
    per-row rule.
    """
    return answer


def window_ablation(bundle: dict[str, Any], probes: int = 24) -> dict[str, Any]:
    """Cheap 1/2-context window gate: is the answer reachable from a half window?

    This is an approximation of the repo's 4k/8k/16k window gates on the
    rendered text, not a token-level proof; the header row that pins the
    contract is one line of the context and is part of every window.
    """
    lines = bundle["context"].splitlines()
    half = max(1, len(lines) // 2)
    starts = sorted(
        {round(i * (len(lines) - half) / max(1, probes - 1)) for i in range(probes)}
    )
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


def holdout_plan(rule_family: str) -> dict[str, Any]:
    """The structured split: train on one rule family, evaluate on the other.

    The split is by *rule structure*, not by seed. An eval row built from the
    held-out family has a rule family that never appears in training, so the
    reader has to transfer the induction procedure rather than a coefficient
    map, and the eval bank generates its held-out rows with its own family --
    a context generated under one structure cannot answer the other, because
    the header pins the structure and the executor rejects the mismatch.
    """
    if rule_family not in RULE_STRUCTURES:
        raise ValueError("unsupported rule family")
    held_out = [name for name in RULE_FAMILIES if name != rule_family]
    return {
        "trained_rule_family": rule_family,
        "train_rule_families": [rule_family],
        "eval_rule_families": held_out,
        "basis": "rule_structure",
        "seed_disjoint": True,
        "eval_rows_are_generated_by_their_own_family": True,
    }


def split_for_rule_family(rule_family: str, trained_rule_family: str) -> str:
    """Which side of the structured split one rule family's rows belong to."""
    plan = holdout_plan(trained_rule_family)
    if rule_family in plan["train_rule_families"]:
        return "train"
    return "eval"


def _hypothesis_count(rule_family: str, modulus: int) -> int:
    if rule_family == "threshold_class":
        return len(_threshold_hypotheses(modulus))
    return len(_parity_hypotheses())


def _validate_intervention(
    family: str,
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    intervention: dict[str, Any],
    checks: dict[str, Any],
) -> list[str]:
    """Re-run the family intervention the bundle declares and require the flip."""
    errors: list[str] = []
    kind = intervention.get("kind")
    by_id = {row.id: row for row in rows}
    if kind == "alias_swap":
        first, second = intervention["rows"]
        if any(row_id not in by_id for row_id in (first, second)):
            return ["the declared alias-swap rows are not rendered"]
        entities = [by_id[first].entity, by_id[second].entity]
        if sorted(entities) != sorted(intervention["entities"]):
            return ["the declared alias-swap entities do not match the rows"]
        swapped = []
        for row in rows:
            if row.id == first:
                swapped.append(replace(row, entity=by_id[second].entity))
            elif row.id == second:
                swapped.append(replace(row, entity=by_id[first].entity))
            else:
                swapped.append(row)
        flipped = _differs(header, swapped, question, answer)
    elif kind == "reveal_flip":
        row = by_id.get(intervention["row"])
        if row is None or row.reveal != intervention["before"]:
            return ["the declared reveal-flip row does not carry the declared reveal"]
        if row.kind != intervention["event_kind"]:
            return ["the declared reveal-flip kind does not match the row"]
        moved = [
            replace(candidate, reveal=intervention["after"])
            if candidate.id == row.id
            else candidate
            for candidate in rows
        ]
        flipped = _solve_rows(header, moved, question) != answer
    elif kind == "insert_hit":
        insert_id = intervention.get("row")
        row = by_id.get(insert_id)
        if row is None:
            return ["the declared insert-hit row is not rendered"]
        if [dict(condition) for condition in intervention["conditions"]] != [
            dict(condition) for condition in question["steps"][0]["conditions"]
        ]:
            return ["the declared insert-hit conditions do not match the program"]
        bounds = {
            (condition["field"], condition["op"]): condition["value"]
            for condition in intervention["conditions"]
        }
        moved = []
        for candidate in rows:
            if candidate.id != insert_id:
                moved.append(candidate)
                continue
            day = candidate.day
            low = bounds.get(("date", ">="))
            if low is not None and day < low:
                day = low
            high = bounds.get(("date", "<="))
            if high is not None and day > high:
                day = high
            amount = candidate.amount
            floor = bounds.get(("amount", ">="))
            ceil = bounds.get(("amount", "<="))
            if floor is not None and amount < floor:
                amount = floor
            if ceil is not None and amount > ceil:
                amount = ceil
            category = candidate.category
            wanted = bounds.get(("category", "=="))
            if wanted is not None and category != wanted:
                category = wanted
            moved.append(replace(candidate, day=day, amount=amount, category=category))
        terminal = question["steps"][-1]
        if terminal["op"] == "set_contains":
            moved_question = {
                **question,
                "steps": [question["steps"][0], dict(terminal, id=insert_id)],
            }
        elif terminal["op"] == "set_missing":
            ids = list(terminal["ids"])
            if insert_id not in ids:
                ids = [*ids, insert_id] if len(ids) < 8 else [*ids[1:], insert_id]
            moved_question = {
                **question,
                "steps": [question["steps"][0], dict(terminal, ids=ids)],
            }
        else:
            moved_question = question
        flipped = _differs(header, moved, moved_question, answer)
    else:
        selected = set(intervention["rows"])
        if any(row_id not in by_id for row_id in selected):
            return ["the declared removed rows are not rendered"]
        reduced = [row for row in rows if row.id not in selected]
        flipped = _differs(header, reduced, question, answer)
    checks["intervention:" + question.get("family", family)] = flipped
    if not flipped:
        errors.append("the declared intervention did not change the answer")
    return errors


def _family_checks(
    family: str,
    bundle: dict[str, Any],
    header: dict[str, Any],
    rows: list[Row],
    errors: list[str],
    checks: dict[str, Any],
) -> None:
    """World-level structural checks the family is defined by."""
    if family == "alias_locate":
        # Binding is a function: every handle names exactly one entity.
        declarations = [row for row in rows if row.type == "alias"]
        handles = [row.alias for row in declarations]
        if len(set(handles)) != len(handles):
            errors.append("an alias handle does not name exactly one entity")
        # Every task carries same-shape negatives: rows of *other* entities in
        # the task's own queried category, which the handle is the only
        # discriminator against. Without them a reader could take the category
        # shortcut and never resolve the alias.
        for task in bundle["tasks"]:
            question = task["question"]
            declaration, table = _alias_bound(rows, _alias_steps(question))
            queried = task["queried_category"]
            twins = [
                row
                for row in rows
                if row.type == "record"
                and row.entity != declaration.entity
                and row.category == queried
            ]
            if not twins:
                errors.append("no same-shape negative entity for a located task")
            checks["same_shape_negatives:" + task["task_id"]] = len(twins)
            if table and any(row.category != queried for row in table):
                errors.append("a located row is not in the task's queried category")
        return
    if family == "asof_state":
        # Event time and reveal time must not be interchangeable. The claim is
        # made at the fold, not at the answer: the capped release makes the two
        # orders disagree about an entity's (balance, held) even where a set-
        # valued terminal happens to coincide. The answer-level count is
        # reported as well, because that is the observable a reader sees.
        state_disagreements = answer_disagreements = hold_flips = 0
        for task in bundle["tasks"]:
            question = task["question"]
            earlier, later = _asof_cutoffs(_asof_steps(question))
            entities = _asof_named(_asof_steps(question)[-1])
            for cutoff in {earlier, later}:
                state_disagreements += _fold(rows, cutoff, entities) != _fold(
                    rows, cutoff, entities, "reveal"
                )
            answer_disagreements += (
                _solve_rows(header, rows, question, order="reveal") != task["answer"]
            )
            by_id = {row.id: row for row in rows}
            # Revocation propagation: the sign of a set-aside or release row
            # must reach the answer, not just be rendered.
            if any(by_id[row_id].kind in HOLD_KINDS for row_id in task["necessary"]):
                hold_flips += 1
        checks["reveal_order_state_disagreements"] = state_disagreements
        checks["reveal_order_answer_disagreements"] = answer_disagreements
        checks["tasks_carrying_a_decisive_hold_row"] = hold_flips
        if not state_disagreements:
            errors.append(
                "the reveal order changes no state; the times are not separate"
            )
        if hold_flips != len(bundle["tasks"]):
            errors.append("a set-aside or release row is not decisive for its task")
        return
    if family == "set_complete":
        # Gold is the exhaustive membership, re-derived here over the whole
        # world; a set answer carries the members, a verdict answer its own
        # named rows, and every answer list is sorted regardless of the
        # presentation order the task pinned.
        unordered = 0
        for task in bundle["tasks"]:
            question = task["question"]
            steps = _set_steps(question)
            members = _set_members(rows, steps)
            ids = sorted(row.id for row in members)
            answer = task["answer"]
            terminal = steps[-1]
            if terminal["op"] == "set_list":
                if isinstance(answer, list):
                    shown = answer
                elif "ids" in answer:
                    shown = answer["ids"]
                else:
                    shown = sorted(
                        row_id
                        for entity_ids in answer["entities"].values()
                        for row_id in entity_ids
                    )
                if sorted(shown) != ids:
                    errors.append("a set answer is not the exhaustive membership")
            elif terminal["op"] == "set_missing":
                shown = answer if isinstance(answer, list) else answer["missing"]
                member_ids = set(ids)
                expected_missing = sorted(
                    row_id
                    for row_id in sorted(terminal["ids"])
                    if row_id not in member_ids
                )
                if sorted(shown) != expected_missing:
                    errors.append("a missing answer is not the named non-members")
                if (
                    not isinstance(answer, list)
                    and terminal["shape"] == "missing_count"
                    and answer.get("count") != len(expected_missing)
                ):
                    errors.append("a missing count disagrees with the membership")
            elif terminal["op"] == "set_contains":
                verdict = answer if isinstance(answer, bool) else answer["contains"]
                if verdict != (terminal["id"] in set(ids)):
                    errors.append("a contains verdict disagrees with the membership")
                if (
                    terminal["shape"] == "contains_entity"
                    and answer.get("entity") is not None
                ):
                    entity = next(
                        row.entity for row in rows if row.id == terminal["id"]
                    )
                    if answer["entity"] != entity:
                        errors.append("a contains entity disagrees with the row")
            else:
                # set_count: the count and, where the shape declares it, the
                # per-entity counts are the exhaustive membership in numbers.
                counts: dict[str, int] = {}
                for row in members:
                    counts[row.entity] = counts.get(row.entity, 0) + 1
                if isinstance(answer, int):
                    if answer != len(ids):
                        errors.append("a count answer disagrees with the membership")
                elif answer["count"] != len(ids) or answer.get("entities") != counts:
                    errors.append("a count answer disagrees with the membership")
            if set(task["necessary"]) != set(ids) and terminal["op"] in (
                "set_list",
                "set_count",
            ):
                errors.append("a member is not in the necessary partition")
            if question.get("unordered") is True:
                unordered += 1
            checks["members:" + task["task_id"]] = len(ids)
        # The unordered-presentation DOF has to be exercised: 50% of tasks pin
        # it, so a world where none did would be a degenerate draw.
        if not unordered:
            errors.append("no task pins the unordered presentation")
        checks["unordered_tasks"] = unordered
        return
    rule_family, modulus, labels = _rule_header(header)
    records = _rule_entities(rows)
    demos = [(row.points, row.label) for row in rows if row.type == "demo"]
    demonstration_points = {point for points, _ in demos for point in points}
    rule = _infer_rule(rule_family, demos, modulus, labels)
    demonstrated_signatures = {
        _reduced_point(rule_family, list(points), modulus, rule) for points, _ in demos
    }
    # Unseen features, at the level the rule reads: no entity a question asks
    # about may aggregate to a feature point the demonstrations publish.
    asked = sorted(
        {
            entity
            for task in bundle["tasks"]
            for entity in (
                [task["question"]["steps"][-1].get("entity")]
                if task["question"]["steps"][-1]["op"] in ("label", "verify")
                else task["question"]["steps"][-1].get("entities") or []
            )
        }
    )
    for entity in asked:
        entity_rows = records.get(entity)
        if not entity_rows:
            errors.append("a queried entity has no records")
            continue
        if (
            _reduced_point(
                rule_family, [(row.x, row.y) for row in entity_rows], modulus, rule
            )
            in demonstrated_signatures
        ):
            errors.append("a query entity's feature signature was demonstrated")
        if any((row.x, row.y) in demonstration_points for row in entity_rows):
            errors.append("a record repeats a demonstrated feature point")
    # Labels are not a frozen majority: among the entities of any group-level
    # task both labels must appear.
    rule = _infer_rule(
        rule_family,
        [(row.points, row.label) for row in rows if row.type == "demo"],
        modulus,
        labels,
    )
    # No task is a constant answer: among the entities the world carries for a
    # task, both labels appear, so a reader cannot answer by always emitting one.
    for task in bundle["tasks"]:
        terminal = _rule_steps(task["question"])[-1]
        if terminal["op"] in ("label", "verify"):
            entity = terminal.get("entity")
            carried = {
                _rule_label(rule_family, rule, entity_rows, modulus, labels)
                for name, entity_rows in records.items()
                if name != entity
            }
        else:
            carried = {
                _rule_label(rule_family, rule, records[entity], modulus, labels)
                for entity in terminal.get("entities") or []
            }
        checks["labels:" + task["task_id"]] = len(carried)
        if len(carried) < 2:
            errors.append("a label task is a frozen majority class")


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Re-execute gold, re-pin the contract, and run the family interventions."""
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
        family = bundle["family"]
        if family not in PROTOCOLS:
            raise ValueError("unsupported family")
        if bundle.get("capability_level") != CAPABILITY_LEVELS[family]:
            errors.append("capability level does not match the registered contract")
        header, rows = parse_context(bundle["context"])
        if header.get("rules") != PROTOCOLS[family]:
            errors.append("rules text does not match the registered contract")
        if header.get("family") != family:
            errors.append("header family mismatch")
        if family == "rule_holdout":
            rule_family, modulus, labels = _rule_header(header)
            declared = bundle.get("rule", {})
            if (
                declared.get("rule_family") != rule_family
                or declared.get("modulus") != modulus
                or declared.get("labels") != labels
                or declared.get("holdout") != holdout_plan(rule_family)
            ):
                errors.append("declared rule block does not match the visible contract")
            rule = _infer_rule(
                rule_family,
                [(row.points, row.label) for row in rows if row.type == "demo"],
                modulus,
                labels,
            )
            if list(rule) != list(declared.get("parameters", [])):
                errors.append(
                    "the visible demonstrations do not identify the declared rule"
                )
            checks["rule_hypotheses_enumerated"] = _hypothesis_count(
                rule_family, modulus
            )
        if len(rows) != bundle["length_accounting"]["rendered_rows"]:
            errors.append("rendered row count mismatch")
        by_id = {row.id: row for row in rows}
        necessary_total = 0
        for task in bundle["tasks"]:
            question = task["question"]
            if task["instruction"] != render_instruction(
                family, question, task["phrasing_index"]
            ):
                errors.append("instruction does not match the registered contract")
            if task.get("capability") != family or question.get("family") != family:
                errors.append(
                    "task capability tag does not match the registered contract"
                )
            if task.get("capability_level") != CAPABILITY_LEVELS[family]:
                errors.append(
                    "task capability level does not match the registered contract"
                )
            if bundle["world_id"] in bundle["context"] + task["instruction"]:
                errors.append("world identity leaked into the visible text")
            if solve_visible(bundle["context"], question) != task["answer"]:
                errors.append("answer is not reproducible by the visible executor")
            consumed = sorted(task["consumed"])
            if len(consumed) != task["consumed_count"] or len(set(consumed)) != len(
                consumed
            ):
                errors.append("consumed provenance is not a distinct id set")
            if any(row_id not in by_id for row_id in consumed):
                errors.append("consumed id missing from the rendered rows")
            if consumed != _executor_evidence(rows, question):
                errors.append("consumed provenance disagrees with the executor")
            necessary = sorted(task["necessary"])
            if not set(necessary) <= set(consumed):
                errors.append("the necessary set is not a subset of the consumed set")
            measured = _sensitivity(header, rows, question, task["answer"], consumed)
            if measured != necessary:
                errors.append(
                    "the declared sensitivity partition does not match the measurement"
                )
            if not _structurally_necessary(family, rows, question, necessary):
                errors.append("the family's decisive row is not in the necessary set")
            checks["consumed:" + task["task_id"]] = len(consumed)
            checks["necessary:" + task["task_id"]] = len(necessary)
            necessary_total += len(consumed)
            probe = [
                row.id
                for row in rows
                if row.id not in set(consumed) and row.type in PRIMARY_TYPES
            ]
            stable = 0
            for row_id in probe[:16]:
                reduced = [row for row in rows if row.id != row_id]
                stable += not _differs(header, reduced, question, task["answer"])
            checks["distractor_rows_removed:" + task["task_id"]] = stable
            if stable != len(probe[:16]):
                errors.append("a distractor row changed the answer")
            errors.extend(
                _validate_intervention(
                    family,
                    header,
                    rows,
                    question,
                    task["answer"],
                    task["intervention"],
                    checks,
                )
            )
        _family_checks(family, bundle, header, rows, errors, checks)
        accounting = bundle["length_accounting"]
        if accounting["record_rows"] != bundle["length_records"]:
            errors.append("length accounting mismatch")
        if sum(accounting["consumed_rows_per_task"]) != necessary_total:
            errors.append("consumed row accounting mismatch")
        if (
            accounting["consumed_primary_rows"] + accounting["padding_rows"]
            != accounting["record_rows"]
        ):
            errors.append("primary padding accounting mismatch")
        if (
            len({row_id for task in bundle["tasks"] for row_id in task["consumed"]})
            == 0
        ):
            errors.append("no consumed rows at all")
        checks["window_ablation"] = window_ablation(bundle)
    except (ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"passed": not errors, "errors": errors, "checks": checks}
