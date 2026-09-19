"""P72 research/experiments domain adapter (G72-4, route A).

A domain adapter on the record-world spine: the mechanism library is the
P69/P70 one (the as-of fold semantics of ``asof_state``, the chained join of
``join_lookup``, the typed filter steps of both), but the world is a research
lab's experiment lifecycle, not a ledger. What changes is the domain, not the
mechanism -- object semantics, field meanings with units and legality
constraints, relationship structure, row types and renderer vocabulary:

* Row types are five, distinct from the records ``record``/``reference``
  pair: ``config`` (one experiment configuration: method, instrument, and
  the unit that instrument reports), ``batch`` (one sample batch,
  referencing exactly one config), ``measurement`` (one run: the batch it
  ran on, the value in its config's unit, the date it was taken),
  ``retraction`` (a batch-level retraction, with reason, whose effect starts
  at its disclosed date) and ``erratum`` (a late-disclosed method-level
  correction that retro-invalidates, from its disclosed date, every run of
  that method taken strictly before its cutoff).
* Domain legality, checked by ``_domain_legality`` and enforced fail-closed
  by the executor: a batch references exactly one rendered config; a
  measurement references exactly one rendered batch; an instrument reports
  exactly one unit, so every config of an instrument carries that unit and
  every measurement carries its chain's unit; a retraction references a
  rendered batch and is not disclosed before the runs it retracts were
  taken; an erratum is not disclosed before its own cutoff. The same JOIN
  over config -> batch -> measurement is therefore validated by domain
  legality, not just by type matching.
* ``research_run`` (as-of + filter): as of the query date, which runs count,
  given the retractions and corrections revealed by then? Status is derived
  with precedence retracted > invalidated > valid, exactly as a disclosure
  timeline replay requires. Moving one disclosure across the query date
  flips the answer (``reveal_flip``).
* ``research_join`` (chained join): valid measurements of instrument X
  across configs of method Y, executor-computed over domain-legal
  references only. Removing one side of a join key would leave a dangling
  reference -- an illegal world -- so the join intervention deletes both
  sides of the occurrence (``subtree_removal``: the row plus every row that
  references it, transitively).

(L, K, H) as elsewhere: L is the number of measurement rows rendered (the
measurement is this world's record analog; config, batch and disclosure rows
are rendering extras outside L, exactly as join_lookup's reference rows are);
K is the post-scope-and-filter measurement population a task's terminal
reads; H (1 or 2) is the number of chained pre-terminal operations. The
necessary rows are built first and the remaining L - K measurement rows are
foreign-method chains: exposure and interference, never evidence.

Honesty limits, all fail-closed: ``strict_long_dependency_verified``,
``model_utility_measured`` and ``production_eligible`` are false everywhere
and validate_bundle() rejects a bundle that claims otherwise;
``source_kind`` is "simulated_domain" -- a deliberately new value for a new
domain, where the finance-style modules say "simulated" -- and the runner's
per-row lineage carries the bundle's honesty through. L - K padding is
length exposure, not semantic scale.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

VERSION = "capability-research-v1"
BASE_DATE = date(2020, 1, 1)
# Per-task lifecycle layout: runs are taken inside the window, the query date
# sits past the window, and disclosures straddle the query date (live ones
# before it, exposure ones after it).
WINDOW_DAYS = 40
GAP_DAYS = 100
QUERY_OFFSET = 80
FAMILIES = ("research_run", "research_join")
PRIMARY_TYPES = ("measurement",)
# Primary rows one consumed row costs at most, per family: the chain adds a
# batch and a config per few measurements, plus disclosures. plan_variants()
# uses one shared value so a (K, n_variants) cell it accepts is hostable by
# either family.
EVIDENCE_MULTIPLIER = 3

# The domain's own vocabularies: real research-process terms, drawn per world
# the way the records module draws categories (the drawn size is a shape
# lever, because the rendered width of method- and site-bearing answers
# carries the drawn alphabet).
METHOD_POOL = (
    "qpcr",
    "elisa",
    "hplc",
    "gcms",
    "nmr",
    "flow_cytometry",
    "western_blot",
    "titration",
    "colorimetry",
    "microarray",
    "patch_clamp",
    "sequencing",
    "southern_blot",
    "isotope_ratio",
    "calorimetry",
    "polarimetry",
    "turbidimetry",
    "electrophoresis",
    "immunoblot",
    "spectrofluorometry",
    "gravimetry",
    "conductometry",
    "refractometry",
    "amperometry",
    "voltammetry",
    "cytometry",
    "fluorometry",
    "northern_blot",
)
SITE_POOL = (
    "bench_3",
    "cold_room",
    "hood_2",
    "annex_1",
    "suite_c",
    "bay_5",
    "room_118",
    "module_k",
    "wing_d",
    "station_9",
    "lab_2f",
    "outpost_7",
    "cleanroom_a",
    "atrium_b",
    "dock_4",
    "vault_e",
)
REASONS = (
    "contamination",
    "calibration_drift",
    "sample_mixup",
    "reagent_expiry",
    "protocol_deviation",
    "instrument_fault",
    "labeling_error",
    "temperature_excursion",
)
# An instrument reports exactly one unit: the domain legality constraint that
# makes unit consistency checkable along any chain, not just renderable.
INSTRUMENT_UNITS = {
    "flx800_reader": "od450",
    "genex_thermocycler": "copies_per_ml",
    "h_class_chromatograph": "mg_per_dl",
    "trace_gc": "ppb",
    "avance_spectrometer": "hz",
    "cyto_flex": "events_per_ul",
    "blot_imager": "od700",
    "titrator_t50": "ml_titrant",
    "cuvette_7000": "od600",
    "scanner_gx": "rfu",
    "clamp_amp": "millivolts",
    "novaseq_seq": "reads_per_mm2",
    "cps_detector": "counts_per_s",
    "dsc_214": "millijoules",
    "polar_p300": "degrees",
    "nephelo_2100": "ntu",
    "gel_doc": "od_corrected",
    "plate_fluor": "rfu",
    "balance_xp": "milligrams",
    "cond_probe_5": "siemens",
    "ar2000_refractometer": "brix",
    "potentiostat_p20": "millivolts",
}
# A filter condition compares one joined field to a constant; the numeric
# fields take the full comparison set and the categorical ones equality only,
# as in the records convention.
FIELD_OPS: dict[str, tuple[str, ...]] = {
    "value": ("==", "!=", ">=", "<=", ">", "<"),
    "date": ("==", "!=", ">=", "<=", ">", "<"),
    "instrument": ("==", "!="),
    "unit": ("==", "!="),
    "site": ("==", "!="),
    "method": ("==", "!="),
}
RUN_TERMINALS = ("valid_count", "status_counts", "valid_list", "valid_total")
RUN_TERMINALS_BY_DEPTH: dict[int, tuple[str, ...]] = {
    1: ("valid_count", "status_counts"),
    2: RUN_TERMINALS,
}
JOIN_HOWS = ("count", "sum")

# The visible rules text and the instruction phrasings are part of the task
# contract: the executor reads only the structured program, so a reworded
# rules line or instruction that contradicts the executor would still solve.
# validate_bundle() pins both to these constants and re-renders every
# instruction from (program, phrasing index), rejecting any deviation.
PROTOCOLS: dict[str, str] = {
    "research_run": (
        "Each rendered row is a JSON object. Rows of type config declare one "
        "experiment configuration: method, instrument and the unit that "
        "instrument reports. Rows of type batch declare one sample batch and "
        "reference exactly one config. Rows of type measurement declare one "
        "measurement run: the batch it ran on, the value in its config's "
        "unit, and the date it was taken. Rows of type retraction retract "
        "every measurement of one batch from their disclosed date onward and "
        "carry the reason. Rows of type erratum are late-disclosed "
        "corrections for one method: from their disclosed date onward, every "
        "measurement of that method taken strictly before the erratum's "
        "cutoff date is invalidated. As of the program's query date, a "
        "surviving run is retracted when a retraction of its batch is "
        "disclosed at or before the query date; it is invalidated when no "
        "retraction applies and an erratum of its method is disclosed at or "
        "before the query date while the run predates that erratum's cutoff; "
        "otherwise it is valid. Apply the program's scope step (the named "
        "method) and then its filter steps in order, keeping the runs that "
        "satisfy every condition; a condition compares method, instrument, "
        "unit, site, value or date to a constant with the stated operator. "
        "Then report over the surviving runs at the query date: valid_count "
        "(how many are valid), status_counts (how many are valid, retracted "
        "and invalidated), valid_list (the sorted row ids of the valid runs) "
        "or valid_total (the summed values of the valid runs). Runs that "
        "fail the scope or any filter step are unused, and runs whose "
        "retraction or erratum is disclosed after the query date count as "
        "valid. No rows are omitted."
    ),
    "research_join": (
        "Each rendered row is a JSON object. Rows of type config declare one "
        "experiment configuration: method, instrument and the unit that "
        "instrument reports. Rows of type batch declare one sample batch and "
        "reference exactly one config. Rows of type measurement declare one "
        "measurement run: the batch it ran on, the value in its config's "
        "unit, and the date it was taken. Rows of type retraction retract "
        "every measurement of one batch from their disclosed date onward and "
        "carry the reason; every retraction the world carries applies, "
        "whenever it was disclosed. The join chain is measurement to its "
        "batch to its config: a run references exactly one batch, a batch "
        "references exactly one config, and a run is valid exactly when no "
        "retraction names its batch. Apply the program's scope step (the "
        "named method) and then its filter steps in order over the joined "
        "runs -- a condition compares method, instrument, unit, site, value "
        "or date to a constant with the stated operator -- then aggregate "
        "the valid joined runs: count counts them, sum adds their values. "
        "chains lists each valid run's [measurement id, batch id, config "
        "id]; by_config totals the aggregate per config. Runs that fail the "
        "scope or any filter step, and runs of retracted batches, are "
        "unused. No rows are omitted."
    ),
}

PROMPTS: dict[str, tuple[str, ...]] = {
    "research_run": (
        "Execute the typed program below over the rendered lab records and return the state it asks for.",
        "Run the as-of program on the experiment records and report which runs count at the query date.",
        "Evaluate this program against the measurement rows and give the as-of answer.",
        "Replay the disclosure timeline up to the query date and report the run states it asks for.",
        "Carry out the following typed query over the lab records and return the outcome.",
        "Read the experiment records, run the program exactly as written, and report its result.",
        "Compute how many runs are valid as of the query date, after the scope and filters.",
        "Process the lab records with the program below and return the as-of run states.",
        "Work through the program against the rendered records and state the answer.",
        "Run this records program and return the valid count, statuses or ids it asks for.",
        "Solve the typed lifecycle query below and report the result as specified.",
        "Execute the program over the rendered table; report the state and its query date.",
        "Fold the disclosed retractions and errata by the query date, then answer.",
        "Apply the program to the measurement rows and give the as-of state it asks for.",
        "Interpret the query program, evaluate it on the records, and return the answer.",
        "Evaluate the as-of lifecycle program on the rendered lab records and answer.",
        "Execute the stated operations over the records in order and report the result.",
        "Run the query program on the rows below and return the as-of run states.",
        "Apply the scope and filters in sequence, derive each run's status at the query date, and answer.",
        "Compute the program's result over the rendered experiment records.",
        "Execute the following program on the rows and report the counts with their query date.",
        "Evaluate this disclosure-timeline program over the records and answer.",
        "Solve the lifecycle query by running the program and report the valid runs.",
        "Run the program over the lab records and give the counts, list or total it asks for.",
    ),
    "research_join": (
        "Execute the typed program below over the rendered lab records and return its result.",
        "Run the join-and-aggregate program on the experiment records and report the answer.",
        "Evaluate this program against the rows and join the runs to their batches and configs.",
        "Apply the stated scope and filters, walk the measurement chains, aggregate, and answer.",
        "Carry out the following typed query over the lab records and return the outcome.",
        "Read the experiment records, run the program exactly as written, and report its result.",
        "Compute the requested aggregate over the valid joined runs after the scope and filters.",
        "Process the lab records with the program below and return the joined aggregate.",
        "Work through the program against the rendered records and state the answer.",
        "Run this records program and return the chains, the per-config totals and the aggregate.",
        "Solve the typed join query below and report the result as specified.",
        "Execute the program over the rendered table; report the join result and its provenance.",
        "Follow the scope step, resolve each run's batch and config, and answer with the aggregate.",
        "Apply the program to the measurement rows and give the chained join it asks for.",
        "Interpret the query program, evaluate it on the records, and return the answer.",
        "Evaluate the chained-join program on the rendered lab records and answer.",
        "Execute the stated operations over the records in order and report the result.",
        "Run the query program on the rows below and return the joined aggregate.",
        "Apply the scope and filter steps in sequence, join the survivors to their chains, and report the total.",
        "Compute the program's result over the rendered experiment records.",
        "Execute the following program on the rows and report the aggregate with its chains.",
        "Evaluate this config-batch-measurement join over the records and answer.",
        "Solve the join query by running the program and report the aggregate over the valid runs.",
        "Run the program over the lab records and give the aggregate and its joined chains.",
    ),
}

RETURNS: dict[str, str] = {
    "research_run": (
        "Return one JSON object with the query date and the method, carrying "
        "the valid count, the valid, retracted and invalidated counts, or "
        "the summed values the program asks for; valid_list returns the "
        "bare sorted row ids of the valid runs."
    ),
    "research_join": (
        "Return one JSON object with keys chains (each valid run's "
        "[measurement id, batch id, config id]), by_config (config id to "
        "value) and aggregate (how, value)."
    ),
}

FIELDS: dict[str, str] = {
    "research_run": (
        "config rows carry id, method, instrument, unit, memo; batch rows "
        "carry id, config, site, memo; measurement rows carry id, batch, "
        "unit, value, date, memo; retraction rows carry id, batch, reason, "
        "disclosed, memo; erratum rows carry id, method, cutoff, disclosed, "
        "memo."
    ),
    "research_join": (
        "config rows carry id, method, instrument, unit, memo; batch rows "
        "carry id, config, site, memo; measurement rows carry id, batch, "
        "unit, value, date, memo; retraction rows carry id, batch, reason, "
        "disclosed, memo."
    ),
}

CAPABILITY_LEVELS: dict[str, str] = {
    "research_run": "R1_lifecycle_asof_status",
    "research_join": "R2_chained_join_aggregate",
}

HONESTY: dict[str, Any] = {
    "source_kind": "simulated_domain",
    "strict_long_dependency_verified": False,
    "model_utility_measured": False,
    "production_eligible": False,
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_id(prefix: str, rng: random.Random, used: set[str], width: int) -> str:
    """One row identity at the world's own hex width (a per-world presentation knob)."""
    ident = prefix + format(rng.getrandbits(4 * width), f"0{width}x")
    while ident in used:
        ident = prefix + format(rng.getrandbits(4 * width), f"0{width}x")
    used.add(ident)
    return ident


@dataclass(frozen=True)
class Row:
    id: str
    type: str
    memo: str
    method: str | None = None
    instrument: str | None = None
    unit: str | None = None
    site: str | None = None
    config: str | None = None
    batch: str | None = None
    value: int | None = None
    day: str | None = None
    reason: str | None = None
    cutoff: str | None = None
    disclosed: str | None = None

    def visible(self) -> dict[str, Any]:
        row: dict[str, Any] = {"id": self.id, "type": self.type, "memo": self.memo}
        for key, value in (
            ("method", self.method),
            ("instrument", self.instrument),
            ("unit", self.unit),
            ("site", self.site),
            ("config", self.config),
            ("batch", self.batch),
            ("value", self.value),
            ("date", self.day),
            ("reason", self.reason),
            ("cutoff", self.cutoff),
            ("disclosed", self.disclosed),
        ):
            if value is not None:
                row[key] = value
        return row


def render_context(rows: list[Row], family: str) -> str:
    """One header line pinning schema, family and rules, then one JSON row per line."""
    header = {"schema": VERSION, "family": family, "rules": PROTOCOLS[family]}
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
                memo=item["memo"],
                method=item.get("method"),
                instrument=item.get("instrument"),
                unit=item.get("unit"),
                site=item.get("site"),
                config=item.get("config"),
                batch=item.get("batch"),
                value=item.get("value"),
                day=item.get("date"),
                reason=item.get("reason"),
                cutoff=item.get("cutoff"),
                disclosed=item.get("disclosed"),
            )
        )
    return header, rows


# --------------------------------------------------------------------------
# domain legality + chain resolution: what makes a world a legal lab
# --------------------------------------------------------------------------


def _domain_legality(rows: list[Row], family: str) -> None:
    """Fail-closed domain legality: reference integrity, unit consistency, lifecycle sanity.

    A type-matching JOIN would accept any row whose keys line up; this is the
    domain's own constraint set, and the executor refuses a world that
    violates it: a batch must reference exactly one rendered config; a
    measurement must reference exactly one rendered batch and carry the unit
    its config's instrument reports; a retraction must name a rendered batch,
    and its disclosure must postdate the runs it retracts (a lab cannot
    retract a run before it exists); an erratum must name a rendered method
    and be disclosed after its own cutoff. The join family carries no errata,
    so an erratum row in one is illegal there.
    """
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("two rows share one id")
    for row in rows:
        if row.type == "config":
            if row.method is None or row.instrument is None or row.unit is None:
                raise ValueError("a config must declare method, instrument and unit")
            if INSTRUMENT_UNITS.get(row.instrument) != row.unit:
                raise ValueError("an instrument does not report that unit")
        elif row.type == "batch":
            if row.config not in by_id or by_id[row.config].type != "config":
                raise ValueError("a batch must reference exactly one rendered config")
            if row.site is None:
                raise ValueError("a batch must declare its site")
        elif row.type == "measurement":
            if row.batch not in by_id or by_id[row.batch].type != "batch":
                raise ValueError(
                    "a measurement must reference exactly one rendered batch"
                )
            batch = by_id[row.batch]
            if batch.config not in by_id:
                raise ValueError(
                    "a measurement's chain reaches a config that is not rendered"
                )
            config = by_id[batch.config]
            if config.type != "config":
                raise ValueError("a batch must reference exactly one config")
            if row.unit != config.unit:
                raise ValueError(
                    "a measurement's unit must be consistent with its config's "
                    "instrument"
                )
            if row.value is None or row.day is None:
                raise ValueError("a measurement must carry a value and a date")
        elif row.type == "retraction":
            if row.batch not in by_id or by_id[row.batch].type != "batch":
                raise ValueError("a retraction must name a rendered batch")
            if row.reason is None or row.disclosed is None:
                raise ValueError("a retraction must carry reason and disclosed date")
            for target in rows:
                if (
                    target.type == "measurement"
                    and target.batch == row.batch
                    and row.disclosed < target.day
                ):
                    raise ValueError(
                        "a retraction is disclosed before a run it retracts"
                    )
        elif row.type == "erratum":
            if family != "research_run":
                raise ValueError("the join world carries no errata")
            if row.method is None or row.disclosed is None or row.cutoff is None:
                raise ValueError("an erratum must carry method, cutoff and disclosed")
            if row.disclosed <= row.cutoff:
                raise ValueError("an erratum is disclosed before its own cutoff")
            if not any(
                candidate.type == "config" and candidate.method == row.method
                for candidate in rows
            ):
                raise ValueError("an erratum must name a rendered method")
        else:
            raise ValueError("unknown row type")


def _chains(rows: list[Row]) -> dict[str, tuple[Row, Row, Row]]:
    """Each measurement's legal chain: (measurement, batch, config) by row id.

    Domain legality already guarantees the chain resolves, so this is pure
    resolution; it is split out so interventions can rebuild the chain index
    after a row replacement without re-validating the whole world.
    """
    by_id = {row.id: row for row in rows}
    return {
        row.id: (row, by_id[row.batch], by_id[by_id[row.batch].config])
        for row in rows
        if row.type == "measurement"
    }


def _status(
    chains: dict[str, tuple[Row, Row, Row]], disclosed_by: str, row_id: str
) -> str:
    """One run's status at a query date: retracted > invalidated > valid.

    Precedence is the domain's: a retraction kills the whole batch outright,
    so a run that is both retracted and invalidated still reports retracted --
    the reason the lab revoked it, not the later correction, is what a
    disclosure-timeline replay must show.
    """
    run, batch, config = chains[row_id]
    for candidate in disclosed_by.get(batch.id, ()):
        if candidate.disclosed <= disclosed_by["query"]:
            return "retracted"
    for candidate in disclosed_by.get(("erratum", config.method), ()):
        if candidate.disclosed <= disclosed_by["query"] and run.day < candidate.cutoff:
            return "invalidated"
    return "valid"


def _holds(triple: tuple[Row, Row, Row], condition: dict[str, Any]) -> bool:
    """One joined-field condition against one measurement's chain."""
    run, batch, config = triple
    field, op, target = condition["field"], condition["op"], condition["value"]
    if field not in FIELD_OPS or op not in FIELD_OPS[field]:
        raise ValueError("unsupported predicate")
    values = {
        "method": config.method,
        "instrument": config.instrument,
        "unit": run.unit,
        "site": batch.site,
        "value": run.value,
        "date": run.day,
    }
    value = values[field]
    if value is None:
        raise ValueError("predicate field missing on the chain")
    if field in ("value", "date"):
        if type(target) is not int and field == "value":
            raise ValueError("a value predicate must be an integer")
        if field == "date" and not isinstance(target, str):
            raise ValueError("a date predicate must be a string")
    elif not isinstance(target, str):
        raise ValueError("a text predicate must be a string")
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


def _program_steps(question: dict[str, Any], family: str) -> list[dict[str, Any]]:
    """Validate the program chain: a scope step, 0-1 filter steps, a terminal."""
    steps = question.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 3:
        raise ValueError("invalid program chain")
    if steps[0].get("op") != "scope" or not isinstance(steps[0].get("method"), str):
        raise ValueError("a scope step naming the method must come first")
    for step in steps[1:-1]:
        conditions = step.get("conditions")
        if step.get("op") != "filter" or not isinstance(conditions, list):
            raise ValueError("only a filter step may follow the scope step")
        if not 2 <= len(conditions) <= 3:
            raise ValueError("a filter step needs 2 or 3 conditions")
        fields = {condition.get("field") for condition in conditions}
        if not 2 <= len(fields) <= 3:
            raise ValueError("a filter predicate must span 2 or 3 fields")
    terminal = steps[-1]
    if family == "research_run":
        if terminal.get("op") not in RUN_TERMINALS:
            raise ValueError("unsupported terminal operation")
        if not isinstance(terminal.get("as_of"), str):
            raise ValueError("a lifecycle query must carry its as-of date")
    else:
        if (
            terminal.get("op") != "join_aggregate"
            or terminal.get("how") not in JOIN_HOWS
        ):
            raise ValueError("unsupported join terminal")
    return steps


def _scope_and_filter(
    chains: dict[str, tuple[Row, Row, Row]], steps: list[dict[str, Any]]
) -> dict[str, tuple[Row, Row, Row]]:
    """The chains the scope and filter steps keep, keyed by measurement id."""
    method = steps[0]["method"]
    kept = {
        row_id: triple
        for row_id, triple in chains.items()
        if triple[2].method == method
    }
    for step in steps[1:-1]:
        kept = {
            row_id: triple
            for row_id, triple in kept.items()
            if all(_holds(triple, condition) for condition in step["conditions"])
        }
    return kept


def _disclosure_index(rows: list[Row]) -> dict[Any, tuple[Row, ...]]:
    """Where the executor looks disclosures up: batch -> retractions, method -> errata.

    The "query" key is filled by the caller with the query date row -- a
    dummy -- so _status reads one uniform map. Fail-closed on duplicates: a
    batch may be retracted once (a second retraction of one batch is a
    contradiction the lab would resolve, not a world the executor answers).
    """
    index: dict[Any, list[Row]] = {}
    for row in rows:
        if row.type == "retraction":
            index.setdefault(row.batch, []).append(row)
        elif row.type == "erratum":
            index.setdefault(("erratum", row.method), []).append(row)
    for entries in index.values():
        if len(entries) > 1:
            raise ValueError("one batch or one method carries two disclosures")
    return {key: tuple(entries) for key, entries in index.items()}


def _solve_rows(
    header: dict[str, Any], rows: list[Row], question: dict[str, Any]
) -> Any:
    """The shared research executor over parsed rows."""
    family = header.get("family")
    if (
        header.get("schema") != VERSION
        or family not in PROTOCOLS
        or header.get("rules") != PROTOCOLS[family]
    ):
        raise ValueError("unsupported or missing visible contract")
    if question.get("family") != family:
        raise ValueError("program family does not match the visible contract")
    steps = _program_steps(question, family)
    _domain_legality(rows, family)
    chains = _chains(rows)
    kept = _scope_and_filter(chains, steps)
    terminal = steps[-1]
    if family == "research_join":
        # Every retraction the world carries applies, whenever disclosed: the
        # join family's contract has no query date, so an as-of filter would be
        # finance semantics reskinned rather than the lab's own rule.
        valid = {
            row_id: triple
            for row_id, triple in kept.items()
            if not any(
                row.type == "retraction" and row.batch == triple[1].id for row in rows
            )
        }
        if not valid:
            raise ValueError("join consumed no rows")
        by_config: dict[str, Any] = {}
        ordered = sorted(valid, key=lambda row_id: valid[row_id][0].id)
        chain_list = [
            [row_id, valid[row_id][1].id, valid[row_id][2].id] for row_id in ordered
        ]
        for row_id in ordered:
            by_config[valid[row_id][2].id] = by_config.get(valid[row_id][2].id, 0) + 1
        value = len(ordered)
        if terminal["how"] == "sum":
            value = sum(valid[row_id][0].value for row_id in ordered)
            by_config = {}
            for row_id in ordered:
                by_config[valid[row_id][2].id] = (
                    by_config.get(valid[row_id][2].id, 0) + valid[row_id][0].value
                )
        return {
            "chains": chain_list,
            "by_config": by_config,
            "aggregate": {"how": terminal["how"], "value": value},
        }
    disclosed_by = _disclosure_index(rows)
    disclosed_by["query"] = terminal["as_of"]
    statuses = {row_id: _status(chains, disclosed_by, row_id) for row_id in kept}
    op = terminal["op"]
    valid_ids = sorted(
        row_id for row_id, status in statuses.items() if status == "valid"
    )
    if op == "valid_count":
        return {
            "as_of": terminal["as_of"],
            "method": steps[0]["method"],
            "valid": len(valid_ids),
            "retracted": sum(status == "retracted" for status in statuses.values()),
            "invalidated": sum(status == "invalidated" for status in statuses.values()),
        }
    if op == "status_counts":
        return {
            "as_of": terminal["as_of"],
            "method": steps[0]["method"],
            "counts": {
                "valid": len(valid_ids),
                "retracted": sum(status == "retracted" for status in statuses.values()),
                "invalidated": sum(
                    status == "invalidated" for status in statuses.values()
                ),
            },
        }
    if op == "valid_list":
        return valid_ids
    total = sum(chains[row_id][0].value for row_id in valid_ids)
    return {
        "as_of": terminal["as_of"],
        "method": steps[0]["method"],
        "total": total,
        "valid": len(valid_ids),
    }


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
        op = step.get("op")
        if op == "scope":
            parts.append(
                f"take the measurement runs whose config's method is {step['method']}"
            )
        elif op == "filter":
            joined = " and ".join(
                f"{c['field']} {c['op']} {c['value']}" for c in step["conditions"]
            )
            parts.append(f"keep the joined runs where {joined}")
        elif op == "join_aggregate":
            parts.append(
                f"join each surviving run to its batch and its config and report "
                f"{step['how']} over the valid runs"
            )
        else:
            if "as_of" in step:
                parts.append(f"derive each run's status as of {step['as_of']} and ")
            tail = {
                "valid_count": "report how many runs are valid",
                "status_counts": (
                    "report how many runs are valid, retracted and invalidated"
                ),
                "valid_list": "report the valid runs' row ids as a bare sorted list",
                "valid_total": "report the summed values of the valid runs",
            }[op]
            parts.append(tail)
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
    """K is sampled per task over a band around the cell value (records convention)."""
    spread = max(2, consumed // 3)
    return [
        max(4, consumed + rng.randrange(-spread, spread + 1)) for _ in range(n_variants)
    ]


def plan_variants(length_records: int, consumed: int, requested: int) -> int:
    """How many K-sized tasks fit in L while leaving real distractor room.

    The multiplier is the widest per-family evidence cost (a consumed run may
    cost its chain's batch and config too), so a cell this accepts is
    hostable by either family even though the dispatch does not know which
    will generate the world.
    """
    room = length_records - max(8, length_records // 10)
    return max(
        1, min(requested, room // max(4, (consumed * EVIDENCE_MULTIPLIER * 6) // 5))
    )


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def _task_window(index: int) -> tuple[date, date, date]:
    """One task's lifecycle window: (window start, window end, query date).

    Runs are taken inside the window; the query date sits past it, so every
    run predates the query and the as-of fold is a replay, not a race.
    """
    start = BASE_DATE + timedelta(days=index * (WINDOW_DAYS + GAP_DAYS))
    return (
        start,
        start + timedelta(days=WINDOW_DAYS),
        start + timedelta(days=QUERY_OFFSET),
    )


def _live_decoys(depth: int, family: str, size: int) -> int:
    """In-scope decoy runs per task: same method, fail the filter or the retraction."""
    if depth == 1:
        # No filter step to fail; the join family's near-miss rows are the
        # retracted-batch runs, counted separately.
        return 0 if family == "research_run" else max(2, size // 4)
    return max(2, size // 4)


def _build_task(
    rng: random.Random,
    seed: int,
    index: int,
    size: int,
    depth: int,
    family: str,
    method: str,
    id_width: int,
    memo_width: int,
    used_ids: set[str],
) -> tuple[list[Row], dict[str, Any]]:
    """One task's rows (configs, batches, runs, disclosures) and its spec.

    The kept population is exactly `size` runs, built first; decoys and
    near-misses are added around it. Statuses at the query date are mixed by
    construction -- at least one valid, one retracted and one invalidated run
    for the lifecycle family, with one run that is covered by both the
    retraction and the erratum so the retracted > invalidated precedence is
    exercised, not just declared.
    """

    def ident(prefix: str) -> str:
        return _new_id(prefix, rng, used_ids, id_width)

    def memo() -> str:
        return "note-" + format(rng.getrandbits(4 * memo_width), f"0{memo_width}x")

    start, stop, query = _task_window(index)
    query_text = query.isoformat()
    # 2-3 configs of the method, one per distinct instrument, so an
    # instrument-pinned filter is a real discriminator between configs.
    instruments = rng.sample(list(INSTRUMENT_UNITS), rng.randint(2, 3))
    configs = [
        Row(
            id=ident("c"),
            type="config",
            memo=memo(),
            method=method,
            instrument=instrument,
            unit=INSTRUMENT_UNITS[instrument],
        )
        for instrument in instruments
    ]
    focus = instruments[(seed + index) % len(instruments)]
    pattern = (seed + index) % 2 if depth >= 2 else 2
    # Batches per config: the instrument-pinned pattern puts 2-3 on the focus
    # config (the kept population rides them) and one on each other config;
    # the other patterns spread 3-4 batches over all configs.
    if pattern == 0:
        focus_id = next(config.id for config in configs if config.instrument == focus)
        layout = [focus_id] * rng.randint(2, 3) + [
            config.id for config in configs if config.id != focus_id
        ]
    else:
        layout = [
            configs[position % len(configs)].id for position in range(rng.randint(3, 4))
        ]
    sites = rng.sample(SITE_POOL, len(layout))
    batches = [
        Row(
            id=ident("b"),
            type="batch",
            memo=memo(),
            config=layout[position],
            site=sites[position],
        )
        for position in range(len(layout))
    ]
    value_lo = rng.randint(100, 400)
    value_hi = value_lo + rng.randint(80, 500)
    cutoff = start + timedelta(days=rng.randint(12, WINDOW_DAYS - 12))
    date_floor = start + timedelta(days=10)
    rows: list[Row] = [*configs, *batches]

    def run(batch: Row, day: date, value: int) -> Row:
        return Row(
            id=ident("m"),
            type="measurement",
            memo=memo(),
            batch=batch.id,
            unit=next(config.unit for config in configs if config.id == batch.config),
            value=value,
            day=day.isoformat(),
        )

    focus_configs = [config.id for config in configs if config.instrument == focus]
    if pattern == 0:
        # Instrument-pinned: the kept batches all ride the focus config, so
        # the instrument filter passes them; the remaining batches ride the
        # other configs and the wrong-instrument decoys come from there.
        kept_batches = [batch for batch in batches if batch.config in focus_configs]
        spare_batches = [
            batch for batch in batches if batch.config not in focus_configs
        ]
        if len(kept_batches) < 2:
            # Every batch rode the focus config; move the spares' retraction
            # role to the kept side by re-pointing one spare at the focus.
            spare = spare_batches[0] if spare_batches else None
            if spare is not None:
                repointed = replace(
                    spare, config=focus_configs[(seed + index) % len(focus_configs)]
                )
                batches = [
                    repointed if batch.id == spare.id else batch for batch in batches
                ]
                kept_batches = [repointed]
                spare_batches = [b for b in spare_batches if b.id != spare.id]
    else:
        kept_batches = list(batches)
        spare_batches = []
    if len(kept_batches) < 2:
        raise ValueError("a task needs two kept batches for a mixed status answer")
    retracted_batch = kept_batches[(seed + index) % len(kept_batches)]
    hidden_batch = kept_batches[(seed + index + 1) % len(kept_batches)]
    # The kept population: `size` runs over the kept batches, with the
    # retracted batch carrying at least two (one pre-cutoff, to exercise the
    # precedence, one post-cutoff, to keep the retraction individually
    # decisive) and every other kept batch carrying at least one.
    schedule = [position % len(kept_batches) for position in range(size)]
    rng.shuffle(schedule)
    if schedule.count((seed + index) % len(kept_batches)) < 2:
        schedule[0] = (seed + index) % len(kept_batches)
        schedule[1] = (seed + index) % len(kept_batches)
    for position in range(len(kept_batches)):
        if position != (seed + index) % len(kept_batches) and not schedule.count(
            position
        ):
            schedule[min(2, len(schedule) - 1)] = position
    kept: list[Row] = []
    seen_retracted = 0
    late = 0
    early = 0
    for position in schedule:
        batch = kept_batches[position]
        # Instrument-pinned pattern: kept runs ride the focus instrument's
        # batches; the date is free and splits around the cutoff. The
        # date-floored pattern: kept runs sit at or after the floor, the
        # early ones (before the cutoff) invalidated by the live erratum.
        if pattern == 1:
            if batch.id == retracted_batch.id and seen_retracted == 0:
                # The precedence probe's first retracted run sits between the
                # date floor and the cutoff: it passes the filter and is
                # covered by both the retraction and the erratum.
                day = date_floor + timedelta(
                    days=rng.randrange(0, max(1, (cutoff - date_floor).days))
                )
            else:
                day = date_floor + timedelta(
                    days=rng.randrange((stop - date_floor).days)
                )
                if early == 0 and batch.id != retracted_batch.id:
                    # A pre-cutoff, post-floor run: invalidated, decisive for
                    # the status counts. Strictly between the floor and the
                    # cutoff, so the live erratum always covers it.
                    span = max(1, (cutoff - date_floor).days)
                    day = date_floor + timedelta(days=rng.randrange(0, span))
                    early += 1
        else:
            day = start + timedelta(days=rng.randrange(WINDOW_DAYS))
            if batch.id == retracted_batch.id and seen_retracted == 0:
                # The precedence probe's first retracted run predates the
                # cutoff, so it is covered by both the retraction and the
                # erratum.
                day = start + timedelta(days=rng.randrange(0, 5))
            elif late == 0 and batch.id != retracted_batch.id:
                # A post-cutoff run on a live batch: valid whatever else the
                # timeline does, so the valid status always has a carrier.
                day = cutoff + timedelta(
                    days=rng.randrange(1, max(2, (stop - cutoff).days))
                )
                late += 1
            elif early == 0 and batch.id != retracted_batch.id:
                # A pre-cutoff run on a live batch: invalidated by the live
                # erratum, the status the counts report.
                day = start + timedelta(days=rng.randrange(0, 10))
                early += 1
        seen_retracted += batch.id == retracted_batch.id
        value = rng.randint(value_lo, value_hi)
        kept.append(run(batch, day, value))
    rows.extend(kept)
    # Decoys: in-scope runs that fail exactly one filter condition (depth 2)
    # or, for the join family, runs of a retracted batch (near-misses the
    # retraction alone excludes).
    decoy_rows: list[Row] = []
    decoy_count = _live_decoys(depth, family, size)
    other_configs = [config.id for config in configs if config.instrument != focus]
    for position in range(decoy_count):
        if family == "research_join" and position < max(2, size // 8):
            # A near-miss: passes scope and filter, excluded only by the
            # retraction the world carries.
            batch = retracted_batch
            day = start + timedelta(days=rng.randrange(WINDOW_DAYS))
            value = rng.randint(value_lo, value_hi)
        elif depth >= 2 and pattern == 0 and other_configs and position % 2 == 0:
            # Same method, wrong instrument: fails the instrument condition.
            wanted = other_configs[position % len(other_configs)]
            batch = next(
                candidate for candidate in batches if candidate.config == wanted
            )
            day = start + timedelta(days=rng.randrange(WINDOW_DAYS))
            value = rng.randint(value_lo, value_hi)
        else:
            # Below the value floor: fails the value condition; in the
            # date-floored pattern every other decoy instead predates the
            # floor and fails the date condition.
            batch = batches[position % len(batches)]
            day = start + timedelta(days=rng.randrange(WINDOW_DAYS))
            value = rng.randint(1, max(1, value_lo - 1))
            if pattern == 1 and position % 2 == 0:
                day = start + timedelta(days=rng.randrange(0, 10))
                value = rng.randint(value_lo, value_hi)
        decoy_rows.append(run(batch, day, value))
    rows.extend(decoy_rows)
    disclosures: list[Row] = []
    if family == "research_run":
        # The live retraction: disclosed at or before the query date, after
        # every run of its batch. Its removal moves the retracted runs back
        # to valid, so it is individually decisive.
        disclosures.append(
            Row(
                id=ident("x"),
                type="retraction",
                memo=memo(),
                batch=retracted_batch.id,
                reason=rng.choice(REASONS),
                disclosed=(query - timedelta(days=rng.randint(5, 15))).isoformat(),
            )
        )
        # The live erratum: cutoff inside the window, disclosed before the
        # query date and after the cutoff. Runs before the cutoff are
        # invalidated; the precedence probe's first retracted run is one of
        # them, and must still report retracted.
        disclosures.append(
            Row(
                id=ident("e"),
                type="erratum",
                memo=memo(),
                method=method,
                cutoff=cutoff.isoformat(),
                disclosed=(query - timedelta(days=rng.randint(1, 10))).isoformat(),
            )
        )
        # A hidden retraction and a hidden erratum: disclosed after the query
        # date, so at the query date their runs are valid. That is the as-of
        # discriminator a reader cannot shortcut.
        disclosures.append(
            Row(
                id=ident("x"),
                type="retraction",
                memo=memo(),
                batch=hidden_batch.id,
                reason=rng.choice(REASONS),
                disclosed=(query + timedelta(days=rng.randint(1, 30))).isoformat(),
            )
        )
        # No hidden erratum: one erratum per method is the domain rule, and
        # the hidden retraction alone is the as-of discriminator whose move
        # across the query date flips the answer.
    else:
        # The join family: every retraction applies whenever disclosed, so
        # the retraction dates only have to postdate the runs they retract.
        disclosures.append(
            Row(
                id=ident("x"),
                type="retraction",
                memo=memo(),
                batch=retracted_batch.id,
                reason=rng.choice(REASONS),
                disclosed=(stop + timedelta(days=rng.randint(1, 20))).isoformat(),
            )
        )
    rows.extend(disclosures)
    conditions: list[dict[str, Any]] = []
    if depth >= 2:
        if pattern == 0:
            conditions = [
                {"field": "instrument", "op": "==", "value": focus},
                {"field": "value", "op": ">=", "value": value_lo},
            ]
        else:
            conditions = [
                {"field": "date", "op": ">=", "value": date_floor.isoformat()},
                {"field": "value", "op": ">=", "value": value_lo},
            ]
    terminal: dict[str, Any]
    if family == "research_run":
        terminal_name = RUN_TERMINALS_BY_DEPTH[depth][
            (seed + index) % len(RUN_TERMINALS_BY_DEPTH[depth])
        ]
        terminal = {"op": terminal_name, "as_of": query_text}
    else:
        terminal = {
            "op": "join_aggregate",
            "how": JOIN_HOWS[(seed + index) % len(JOIN_HOWS)],
        }
    steps = [
        {"op": "scope", "method": method},
        *([{"op": "filter", "conditions": conditions}] if conditions else []),
        terminal,
    ]
    spec = {
        "task_id": f"q{index}",
        "question": {"family": family, "steps": steps},
        "retracted_batch": retracted_batch.id,
        "decoy_rows": [row.id for row in decoy_rows],
        "query": query_text,
    }
    return rows, spec


def _padding_chains(
    rng: random.Random,
    count: int,
    methods: list[str],
    id_width: int,
    memo_width: int,
    used_ids: set[str],
) -> list[Row]:
    """Foreign-method chains that fill L: domain-legal, never evidence.

    Each chain is a config of an unqueried method, one or two batches and
    their runs; some batches carry a legal retraction so the padding has the
    same row-type mix as the evidence. No task's scope names these methods,
    so no padding row can ever enter a kept population.
    """

    def ident(prefix: str) -> str:
        return _new_id(prefix, rng, used_ids, id_width)

    def memo() -> str:
        return "note-" + format(rng.getrandbits(4 * memo_width), f"0{memo_width}x")

    rows: list[Row] = []
    remaining = count
    while remaining > 0:
        method = rng.choice(methods)
        instrument = rng.choice(list(INSTRUMENT_UNITS))
        config = Row(
            id=ident("c"),
            type="config",
            memo=memo(),
            method=method,
            instrument=instrument,
            unit=INSTRUMENT_UNITS[instrument],
        )
        rows.append(config)
        for _ in range(rng.randint(1, 2)):
            batch = Row(
                id=ident("b"),
                type="batch",
                memo=memo(),
                config=config.id,
                site=rng.choice(SITE_POOL),
            )
            rows.append(batch)
            take = min(remaining, rng.randint(1, 6))
            last_day = None
            for _ in range(take):
                day = BASE_DATE + timedelta(
                    days=rng.randrange(0, 900 + WINDOW_DAYS + GAP_DAYS)
                )
                last_day = max(last_day or day, day)
                rows.append(
                    Row(
                        id=ident("m"),
                        type="measurement",
                        memo=memo(),
                        batch=batch.id,
                        unit=config.unit,
                        value=rng.randint(1, 500),
                        day=day.isoformat(),
                    )
                )
            remaining -= take
            if last_day is not None and rng.randrange(3) == 0:
                rows.append(
                    Row(
                        id=ident("x"),
                        type="retraction",
                        memo=memo(),
                        batch=batch.id,
                        reason=rng.choice(REASONS),
                        disclosed=(
                            last_day + timedelta(days=rng.randint(1, 30))
                        ).isoformat(),
                    )
                )
    return rows


def generate_world(
    seed: int,
    family: str = "research_run",
    length_records: int = 800,
    consumed_records: int = 60,
    depth: int = 2,
    n_variants: int = 4,
) -> dict[str, Any]:
    """Build one research world: L measurement rows, one typed program per variant."""
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
    id_width = rng.choice((8, 12, 16, 20, 24))
    memo_width = rng.choice((16, 32, 48))
    # One method per task plus a foreign pool, drawn per world: the drawn
    # vocabulary is a shape lever, as the records categories are.
    methods = rng.sample(list(METHOD_POOL), n_variants + rng.randint(2, 4))
    task_methods, foreign_methods = methods[:n_variants], methods[n_variants:]
    used_ids: set[str] = set()
    rows: list[Row] = []
    specs: list[dict[str, Any]] = []
    for index, size in enumerate(sizes):
        task_rows, spec = _build_task(
            rng,
            seed,
            index,
            size,
            depth,
            family,
            task_methods[index],
            id_width,
            memo_width,
            used_ids,
        )
        rows.extend(task_rows)
        specs.append(spec)
    primary = sum(1 for row in rows if row.type in PRIMARY_TYPES)
    padding = length_records - primary
    if padding < 1:
        raise ValueError("no distractor budget left; shrink K or the variant count")
    rows.extend(
        _padding_chains(rng, padding, foreign_methods, id_width, memo_width, used_ids)
    )
    _domain_legality(rows, family)
    rng.shuffle(rows)
    context = render_context(rows, family)
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
            raise ValueError("the family's decisive rows are not individually decisive")
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
            }
        )
    consumed_rows = {row_id for task in tasks for row_id in task["consumed"]}
    primary_consumed = sum(
        1 for row in rows if row.id in consumed_rows and row.type in PRIMARY_TYPES
    )
    decoys = sum(len(spec["decoy_rows"]) for spec in specs)
    accounting = {
        "record_rows": length_records,
        "reference_rows": len(rows) - length_records,
        "rendered_rows": len(rows),
        "consumed_rows_per_task": [task["consumed_count"] for task in tasks],
        "consumed_primary_rows": primary_consumed,
        "padding_rows": length_records - primary_consumed,
        "decoy_padding_rows": decoys,
        "padding_is_exposure_not_semantic_scale": True,
    }
    identity = _dump(
        [VERSION, seed, family, length_records, consumed_records, depth, n_variants]
    )
    return {
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


# --------------------------------------------------------------------------
# evidence, sensitivity, interventions
# --------------------------------------------------------------------------


def _executor_evidence(rows: list[Row], question: dict[str, Any]) -> list[str]:
    """Row ids the program's terminal step actually reads (executor-derived).

    For the lifecycle family the answer is a replay: every in-scope run's
    status is derived, so all of the method's runs are evidence, together
    with every disclosure that decides one of them. For the join family the
    evidence is the valid chains plus the retraction rows that exclude the
    near-miss runs -- a near-miss exclusion is a fact the reader must read,
    not decoration. Both share the scope: a foreign-method row is never
    evidence, and neither is a foreign batch or config of an in-scope
    method's filter failures.
    """
    family = question["family"]
    method = question["steps"][0]["method"]
    chains = _chains(rows)
    kept = {
        row_id: triple
        for row_id, triple in chains.items()
        if triple[2].method == method
    }
    filtered = _scope_and_filter(chains, question["steps"])
    if family == "research_run":
        # Every in-scope run's status is derived, so all of the method's runs
        # are evidence, together with every disclosure that decides one of
        # them -- including the post-query ones, whose dates the reader must
        # compare against the query date to keep their runs valid.
        read = set(kept)
        for row in rows:
            if (
                row.type == "retraction"
                and any(triple[1].id == row.batch for triple in kept.values())
            ) or (row.type == "erratum" and row.method == method):
                read.add(row.id)
        return sorted(read)
    # join family: the valid chains, the retraction rows that decide
    # near-misses, and every batch and config the valid chains traverse.
    read = set()
    for run, batch, config in filtered.values():
        read.update((run.id, batch.id, config.id))
    for row in rows:
        if row.type == "retraction" and any(
            triple[1].id == row.batch for triple in kept.values()
        ):
            read.add(row.id)
    return sorted(read)


def _differs(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
) -> bool:
    """Does the intervened world change the answer, or stop answering at all?"""
    try:
        return _solve_rows(header, rows, question) != answer
    except ValueError:
        return True


def _sensitivity(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    consumed: list[str],
) -> list[str]:
    """Which consumed rows move the answer when dropped, measured by re-execution."""
    return sorted(
        row_id
        for row_id in consumed
        if _differs(header, [row for row in rows if row.id != row_id], question, answer)
    )


def _structurally_necessary(
    family: str, rows: list[Row], question: dict[str, Any], necessary: list[str]
) -> bool:
    """The family's own decisive-row claims, checked against the measured set."""
    by_id = {row.id: row for row in rows}
    if family == "research_run":
        # Every status the answer actually reports must be carried by a
        # decisive row: a retracted count needs a decisive retraction or a
        # decisive retracted run, an invalidated count a decisive pre-cutoff
        # run. A valid_list or valid_total answer reports only the valid runs
        # (plus their count), so only that status needs a carrier there -- a
        # retracted run moving in or out changes neither the list nor the
        # total, and demanding its decisiveness would demand a property the
        # terminal cannot observe.
        steps = question["steps"]
        method = steps[0]["method"]
        terminal_op = steps[-1]["op"]
        reports = (
            {"valid", "retracted", "invalidated"}
            if terminal_op in ("valid_count", "status_counts")
            else {"valid"}
        )
        chains = _chains(rows)
        kept = {
            row_id: triple
            for row_id, triple in chains.items()
            if triple[2].method == method
        }
        as_of = steps[-1]["as_of"]
        disclosed_by = _disclosure_index(rows)
        disclosed_by["query"] = as_of
        decisive = set(necessary)
        statuses = {row_id: _status(chains, disclosed_by, row_id) for row_id in kept}
        for status in reports:
            carriers = [
                row_id
                for row_id, value in statuses.items()
                if value == status and by_id[row_id].type == "measurement"
            ]
            if not carriers:
                # The answer reports the status but the world carries no run
                # in it: a zero count a reader could emit without reading.
                return False
            if status == "retracted" and not any(
                by_id[row_id].type == "retraction" for row_id in decisive
            ):
                # A retraction row must itself be decisive when any retracted
                # run is counted: the count moves with its disclosure.
                return False
            if not any(row_id in decisive for row_id in carriers):
                return False
        return True
    # Join family: every chain row of the aggregate must be decisive -- a
    # valid run moves count and sum, its batch and config references make
    # the chain resolvable (dropping one makes the world illegal, which is
    # the removal the two-sided intervention covers), and the retraction
    # rows that exclude near-misses move the aggregate the other way.
    read = set(_executor_evidence(rows, question))
    if not (read & set(necessary)):
        return False
    for row_id in read:
        row = by_id[row_id]
        if (
            row.type == "measurement"
            and row_id not in necessary
            # Every aggregated run is decisive for count and sum.
            and row_id in {item[0] for item in _solve_rows_chain_list(rows, question)}
        ):
            return False
    return True


def _solve_rows_chain_list(
    rows: list[Row], question: dict[str, Any]
) -> list[list[str]]:
    """The joined chain list of a join question (measurement, batch, config ids)."""
    family = question["family"]
    header = {
        "schema": VERSION,
        "family": family,
        "rules": PROTOCOLS[family],
    }
    try:
        answer = _solve_rows(header, rows, question)
    except ValueError:
        return []
    return answer["chains"]


def _subtree_removal(rows: list[Row], root_id: str) -> tuple[list[Row], list[str]]:
    """Remove one row and every row that transitively references it.

    Deleting one side of a join key leaves a dangling reference -- an illegal
    world the executor rejects -- so the join intervention removes both
    sides: a measurement's subtree is itself; a batch's subtree is the batch
    plus its measurements; a config's is the config, its batches, their
    measurements and any retractions of those batches.
    """
    doomed = {root_id}
    frontier = [root_id]
    while frontier:
        current = frontier.pop()
        for row in rows:
            if row.id in doomed:
                continue
            if (row.type == "batch" and row.config == current) or (
                row.type in ("measurement", "retraction") and row.batch == current
            ):
                doomed.add(row.id)
                frontier.append(row.id)
    return [row for row in rows if row.id not in doomed], sorted(doomed)


def _intervention(
    family: str,
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """The family's own domain intervention, measured at generation time."""
    if family == "research_join":
        return _join_intervention(header, rows, question, answer, spec)
    return _run_intervention(header, rows, question, answer, spec)


def _run_intervention(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Moving one disclosure across the query date must flip the answer.

    The hidden retraction is disclosed after the query date: moving its
    disclosed date back across the query date (before the runs it retracts,
    which the domain requires) turns its runs from valid to retracted, which
    every run terminal reports. The erratum is the same move on the method's
    correction. The chosen row is whichever flips the answer on this world,
    re-measured here rather than assumed.
    """
    query = spec["query"]
    by_id = {row.id: row for row in rows}
    method = question["steps"][0]["method"]
    # The hidden disclosure rows of this task's method: disclosed strictly
    # after the query date, so at the query date their runs are valid.
    hidden = [
        row
        for row in rows
        if row.type in ("retraction", "erratum")
        and row.disclosed > query
        and (
            (row.type == "erratum" and row.method == method)
            or (
                row.type == "retraction"
                and by_id[by_id[row.batch].config].method == method
            )
        )
    ]
    moved_to = (date.fromisoformat(query) - timedelta(days=1)).isoformat()
    for row in hidden:
        # The moved date must respect domain legality: a retraction cannot be
        # disclosed before the runs it retracts; an erratum after its cutoff.
        if row.type == "retraction":
            runs = [
                target
                for target in rows
                if target.type == "measurement" and target.batch == row.batch
            ]
            if runs and moved_to < max(target.day for target in runs):
                continue
        else:
            if moved_to <= row.cutoff:
                continue
        moved = [
            replace(candidate, disclosed=moved_to)
            if candidate.id == row.id
            else candidate
            for candidate in rows
        ]
        try:
            _domain_legality(moved, question["family"])
        except ValueError:
            continue
        if _differs(header, moved, question, answer):
            return {
                "kind": "reveal_flip",
                "row": row.id,
                "type": row.type,
                "before": row.disclosed,
                "after": moved_to,
            }
    raise ValueError("no disclosure crosses the query date and flips the answer")


def _join_intervention(
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Removing both sides of a join key occurrence must flip the answer.

    The root is one valid chain's batch: deleting the batch alone would leave
    dangling measurements -- an illegal world -- so the subtree carries its
    runs too, and the aggregate loses every run of the batch, flipping count
    and sum alike.
    """
    chains = _solve_rows_chain_list(rows, question)
    if not chains:
        raise ValueError("the join intervention has no chain to remove")
    measurement_id, batch_id, config_id = chains[0]
    reduced, removed = _subtree_removal(rows, batch_id)
    try:
        _domain_legality(reduced, question["family"])
    except ValueError as exc:
        raise ValueError(f"the subtree removal left an illegal world: {exc}") from exc
    if not _differs(header, reduced, question, answer):
        raise ValueError("removing both sides of a join key did not flip the answer")
    return {
        "kind": "subtree_removal",
        "root": batch_id,
        "removed": removed,
        "chain": [measurement_id, batch_id, config_id],
    }


def answer_value(answer: Any) -> Any:
    """The computed-value projection of an answer: identity, as in capability_families.

    Every answer here either *is* the computed state (a count map, a total,
    a chain list) or carries it verbatim; no id-bearing field exists that
    would make remove-one vacuous by construction, so necessity is measured
    by the sensitivity partition rather than assumed per row.
    """
    return answer


def window_ablation(bundle: dict[str, Any], probes: int = 24) -> dict[str, Any]:
    """Cheap 1/2-context window gate: is the answer reachable from a half window?"""
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


def _validate_intervention(
    family: str,
    header: dict[str, Any],
    rows: list[Row],
    question: dict[str, Any],
    answer: Any,
    intervention: dict[str, Any],
    checks: dict[str, Any],
) -> list[str]:
    """Re-run the declared intervention and require the flip."""
    errors: list[str] = []
    by_id = {row.id: row for row in rows}
    kind = intervention.get("kind")
    if kind == "reveal_flip":
        row = by_id.get(intervention["row"])
        if row is None or row.disclosed != intervention["before"]:
            return ["the declared reveal-flip row does not carry the declared date"]
        if row.type != intervention.get("type"):
            return ["the declared reveal-flip type does not match the row"]
        moved = [
            replace(candidate, disclosed=intervention["after"])
            if candidate.id == row.id
            else candidate
            for candidate in rows
        ]
        flipped = _differs(header, moved, question, answer)
    elif kind == "subtree_removal":
        root = intervention["root"]
        if root not in by_id:
            return ["the declared removal root is not rendered"]
        reduced, removed = _subtree_removal(rows, root)
        if sorted(removed) != sorted(intervention["removed"]):
            return ["the declared removed rows do not match the subtree"]
        flipped = _differs(header, reduced, question, answer)
    else:
        return [f"unknown intervention kind {kind!r}"]
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
    """World-level domain checks the family is defined by."""
    by_id = {row.id: row for row in rows}
    if family == "research_run":
        # The two as-of discriminators must be real, not declared: the
        # hidden rows must exist, and the precedence probe (a run covered by
        # both the live retraction and the live erratum) must report
        # retracted. Every terminal shape must be exercised across tasks.
        shapes = set()
        precedence_probes = 0
        for task in bundle["tasks"]:
            question = task["question"]
            method = question["steps"][0]["method"]
            shapes.add(question["steps"][-1]["op"])
            hidden = [
                row
                for row in rows
                if row.type in ("retraction", "erratum")
                and (
                    (row.type == "erratum" and row.method == method)
                    or (
                        row.type == "retraction"
                        and by_id[by_id[row.batch].config].method == method
                    )
                )
                and row.disclosed > question["steps"][-1]["as_of"]
            ]
            if not hidden:
                errors.append("a task has no post-query disclosure to distinguish")
            # The precedence probe: at least one run is both retracted and
            # invalidated; its status must be retracted.
            chains = _chains(rows)
            kept = {
                row_id: triple
                for row_id, triple in chains.items()
                if triple[2].method == method
            }
            disclosed_by = _disclosure_index(rows)
            disclosed_by["query"] = question["steps"][-1]["as_of"]
            for row_id, (run, batch, config) in kept.items():
                live_retraction = any(
                    row.type == "retraction"
                    and row.batch == batch.id
                    and row.disclosed <= disclosed_by["query"]
                    for row in rows
                )
                live_erratum = any(
                    row.type == "erratum"
                    and row.method == config.method
                    and row.disclosed <= disclosed_by["query"]
                    and run.day < row.cutoff
                    for row in rows
                )
                if live_retraction and live_erratum:
                    precedence_probes += 1
                    if _status(chains, disclosed_by, row_id) != "retracted":
                        errors.append(
                            "a run covered by both a retraction and an erratum is "
                            "not retracted"
                        )
        checks["terminal_shapes"] = sorted(shapes)
        checks["precedence_probes"] = precedence_probes
        if precedence_probes == 0:
            errors.append(
                "no run is covered by both a retraction and an erratum; precedence "
                "is not exercised"
            )
        return
    # Join family: every chain in every answer is domain-legal (each
    # measurement's batch and config exist and are of the right type), and
    # near-miss runs (retracted batches) are rendered so the retraction rule
    # is load-bearing, not vacuous.
    near_misses = 0
    for task in bundle["tasks"]:
        question = task["question"]
        method = question["steps"][0]["method"]
        for triple in task["answer"]["chains"]:
            measurement_id, batch_id, config_id = triple
            if (
                by_id[measurement_id].type != "measurement"
                or by_id[batch_id].type != "batch"
                or by_id[config_id].type != "config"
                or by_id[measurement_id].batch != batch_id
                or by_id[batch_id].config != config_id
            ):
                errors.append("an answer chain is not a domain-legal reference")
        kept = {
            row_id: triple
            for row_id, triple in _chains(rows).items()
            if triple[2].method == method
        }
        filtered = _scope_and_filter(_chains(rows), question["steps"])
        near_misses += sum(
            1
            for row_id in set(kept) - set(filtered)
            if any(
                row.type == "retraction" and row.batch == kept[row_id][1].id
                for row in rows
            )
        ) + sum(
            1
            for row_id in set(filtered)
            if any(
                row.type == "retraction" and row.batch == filtered[row_id][1].id
                for row in rows
            )
        )
    checks["near_miss_runs"] = near_misses
    if near_misses == 0:
        errors.append(
            "no retracted-batch run is rendered; the retraction rule is vacuous"
        )


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Re-execute gold, re-pin the contract, and run the domain interventions."""
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
        if len(rows) != bundle["length_accounting"]["rendered_rows"]:
            errors.append("rendered row count mismatch")
        # Domain legality is re-checked here, not trusted from generation:
        # a hand-edited world with a dangling batch reference is invalid,
        # exactly as a measurement of a nonexistent batch is.
        _domain_legality(rows, family)
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
                errors.append("the family's decisive rows are not in the necessary set")
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
