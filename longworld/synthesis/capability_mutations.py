"""P73 semantic mutants and witness computation (counterexample-guided synthesis).

The P72 training evidence (reports/gw_per_sample_diagnostics_20260921.md) showed
the trained arms fail by *approximate programs*: they answer with nodes at the
wrong depth, ignore empty-gold scopes, and enumerate ids that pass only some of
the conditions. A bank whose only guarantee is "the gold is executable" cannot
distinguish a correct solver from these plausible near-miss solvers. This
module makes that distinction computable per row: for each task program we
evaluate a small set of *semantic mutants* (plausible wrong programs) and mark
a row "witness-rich" when at least one mutant's answer differs from the
correct one on that row's world.

Everything here is audit-side and pure: no RNG at import, no writes, no
changes to bank generation. `measure_witness_coverage.py` consumes it;
generation-time witness selection (the p73 pilot) is a later step that reuses
the same functions.

Three-way metric (P74 charter §0.2): every mutant result carries
`applicable` (the wrong program is DEFINED on this instance), `valid_output`
(it returned a result rather than erroring) and `distinguished` (valid output
AND different from the correct answer). Syntax errors, type-unsupported
programs and semantic wrong answers are never merged into one score: only
valid-output differences count toward the headline
`semantic_distinguished_fraction`, whose denominator stays ALL of the
family's mutants (a dead or not-applicable mutant keeps diluting the score by
design, so deadness stays visible in the family numbers). The legacy field
names `distinguished`/`distinguished_fraction` carry the same semantic values;
the old error-counts-as-distinguished rule is gone.

Design doc: .hl/design/p73_counterexample_synthesis.md §3.1/§3.2.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records


class MutantNotApplicable(ValueError):
    """The wrong program is not defined on this instance, with a reason.

    Not applicable is a property of the (instance, mutant) pair — e.g. the
    relax mutant needs >=3 scope conditions so the bent program still clears
    the solver's 2-to-4 gate — not an error in the audit: the mutant simply
    has no output to distinguish, and the reason string is carried into the
    report.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# Records families share one executor and program schema, so their mutants are
# expressed once per family below. F-family mutants are family-specific because
# their program schemas differ (bind/asof/learn/scope steps).
_RECORDS_FAMILIES = ("filter_aggregate", "group_compare", "join_lookup")

_MUTANT_SETS: dict[str, dict[str, str]] = {
    "filter_aggregate": {
        "drop_one_condition": "drop the first condition of the first filter step",
        "and_to_or": "survive on ANY condition of the first filter step",
        "ignore_exclusion": "drop the category-!= condition",
        "last_value": "report the amount of the last record row in context order",
    },
    "group_compare": {
        "drop_one_condition": "drop the first condition of the first filter step",
        "and_to_or": "survive on ANY condition of the first filter step",
        "ignore_exclusion": "drop the category-!= condition",
    },
    "join_lookup": {
        "drop_one_condition": "drop the first condition of the first filter step",
        "and_to_or": "survive on ANY condition of the first filter step",
        "ignore_exclusion": "drop the category-!= condition",
    },
    "alias_locate": {
        "nearest_lexical": "bind to the lexicographically closest entity id",
        "last_declaration": "bind to the last-declared alias binding",
    },
    "asof_state": {
        "latest_text": "fold in reveal-date order and use the last event's world",
        "ignore_revocation": "treat set_aside/release as ordinary debits/credits",
    },
    "rule_holdout": {
        "wrong_rule_family": (
            "apply the other rule family's formula over the query entity's "
            "features under the world's declared parameters"
        ),
        "ignore_demos": "label everything with the first declared label",
    },
    "set_complete": {
        "relax_one_condition": "drop one scope condition (superset error)",
        "tighten_one_condition": (
            "sharpen one numeric scope bound by one unit (subset error); "
            "not applicable when the scope has no numeric bound to sharpen"
        ),
    },
}


def mutants_for(family: str) -> list[str]:
    """Names of the mutants defined for a family, in a fixed order."""
    return sorted(_MUTANT_SETS.get(family, {}))


def answer_of(family: str, context: str, program: dict[str, Any]) -> Any:
    """The correct answer by execution, delegating to the owning module.

    join_unanswerable worlds are records join_lookup worlds with an adapted
    task contract; its rows are uniformly "UNKNOWN" and carry no mutant
    surface, so it is simply unsupported here (see witness_report).
    """
    if family in _RECORDS_FAMILIES:
        return records.solve_visible(context, program)
    if family in families.FAMILIES:
        return families.solve_visible(context, program)
    raise ValueError(f"unsupported family: {family}")


# ---------------------------------------------------------------------------
# Records-family mutants
# ---------------------------------------------------------------------------


def _records_survivors(
    rows: list[records.Row], steps: list[dict[str, Any]], mode: str
) -> list[records.Row]:
    """The table a mutant keeps: same chain semantics with one condition bent.

    modes: `drop_first` (drop the first condition of the first filter step),
    `any` (disjunction inside the first step), `no_exclusion` (drop the
    category-!= condition wherever it appears).
    """
    table = [row for row in rows if row.type == "record"]
    for index, step in enumerate(steps[:-1]):
        conditions = step["conditions"]
        if mode == "drop_first" and index == 0:
            kept = conditions[1:]
        elif mode == "any" and index == 0:
            kept = None  # handled below
        elif mode == "no_exclusion":
            kept = [c for c in conditions if c["field"] != "category"]
        else:
            kept = conditions
        if mode == "any" and index == 0:
            table = [
                row for row in table if any(records._holds(row, c) for c in conditions)
            ]
        else:
            table = [row for row in table if all(records._holds(row, c) for c in kept)]
    return table


def _drop_first_program(program: dict[str, Any]) -> dict[str, Any]:
    steps = [dict(s) for s in program["steps"]]
    first = dict(steps[0])
    conds = list(first["conditions"])
    first["conditions"] = conds[1:] if len(conds) > 1 else conds
    steps[0] = first
    return {**program, "steps": steps}


def _no_exclusion_program(program: dict[str, Any]) -> dict[str, Any]:
    steps = [dict(s) for s in program["steps"]]
    out = []
    changed = False
    for step in steps[:-1]:
        kept = [c for c in step["conditions"] if c["field"] != "category"]
        if len(kept) != len(step["conditions"]):
            changed = True
            step = {**step, "conditions": kept}
        out.append(step)
    if not changed:
        return program
    return {**program, "steps": out + [dict(steps[-1])]}


def _records_mutant_answer(
    family: str, context: str, program: dict[str, Any], name: str
) -> Any:
    header, rows = records.parse_context(context)
    terminal = program["steps"][-1]
    if name == "last_value":
        table = _records_survivors(rows, program["steps"], "plain")
        if not table:
            raise ValueError("last_value: no surviving rows")
        last = max(table, key=lambda r: (context.find(f'"{r.id}"'), r.id))
        return {"aggregate": {"how": "amount", "value": last.amount}}
    mode = {
        "drop_one_condition": "drop_first",
        "and_to_or": "any",
        "ignore_exclusion": "no_exclusion",
    }.get(name)
    if mode is None:
        raise ValueError(f"unknown records mutant: {name}")
    # group_compare answers carry per-group record id lists only for the two
    # named groups; the terminal itself reads the filtered table, so a mutant
    # that changes survivors needs the terminal re-run over the mutant table.
    table = _records_survivors(rows, program["steps"], mode)
    if family == "filter_aggregate":
        return records._terminal(table, [], terminal)
    if family == "group_compare":
        return records._terminal(table, [], terminal)
    references = [row for row in rows if row.type == "reference"]
    return records._terminal(table, references, terminal)


# ---------------------------------------------------------------------------
# F-family mutants
# ---------------------------------------------------------------------------


def _alias_mutant_answer(context: str, program: dict[str, Any], name: str) -> Any:
    header, rows = families.parse_context(context)
    steps = program["steps"]
    # The bind step names the alias; find its declaration row and the entity it
    # names (see _alias_bound). Mutants re-bind to a wrong-but-plausible target.
    bind = steps[0]
    alias = bind.get("alias")
    declarations = [row for row in rows if row.type == "alias" and row.alias == alias]
    if not declarations:
        raise ValueError("alias mutant: no declaration row")
    if name == "nearest_lexical":
        # The entity whose id sorts closest to the alias name -- the "picked by
        # surface form" shortcut. Only entities with rows can be picked. When
        # the lexically-nearest entity cannot produce the terminal's required
        # answer shape (a single-match terminal that locates not-exactly-one
        # row, or an empty-match declaration whose rows matched), the shortcut
        # has no legal single-match answer for that target: that is the
        # charter's applicability question (the wrong program is not defined
        # on this instance), not a distinction, so it surfaces as
        # MutantNotApplicable instead of an error.
        targets = sorted(
            {
                row.entity
                for row in rows
                if row.type in ("record", "event", "alias") and row.entity
            }
        )
        if not targets:
            raise ValueError("alias mutant: no entities")
        target = min(targets, key=lambda e: (abs(len(e) - len(alias)), e))
        # alias programs bind through the declaration row, not an explicit
        # entity key: re-point the declaration row's entity to the mutant
        # target.
        rebound = [
            (
                row
                if not (row.type == "alias" and row.alias == alias)
                else _row_with_entity(row, target)
            )
            for row in rows
        ]
        try:
            return families._solve_alias(rebound, program)
        except ValueError as exc:
            message = str(exc)
            if (
                "the single-match query does not locate exactly one row" in message
                or "the query declares an empty match but rows matched" in message
            ):
                raise MutantNotApplicable(
                    "no single-match target for the lexically-nearest entity"
                ) from exc
            raise
    elif name == "last_declaration":
        # The spine enforces exactly one declaration row per alias handle
        # (_alias_bound: "the alias handle does not name exactly one entity"),
        # so "the last-declared binding" is always THE binding: the wrong
        # program is not defined as a distinct program on any spine world.
        if len(declarations) == 1:
            raise MutantNotApplicable(
                "spine enforces single declaration per alias handle"
            )
        target = declarations[-1].entity
    else:
        raise ValueError(f"unknown alias mutant: {name}")
    # alias programs bind through the declaration row, not an explicit entity
    # key: re-point the declaration row's entity to the mutant target.
    rows = [
        (
            row
            if not (row.type == "alias" and row.alias == alias)
            else _row_with_entity(row, target)
        )
        for row in rows
    ]
    return families._solve_alias(rows, program)


def _row_with_entity(row: Any, entity: str) -> Any:
    import dataclasses

    return dataclasses.replace(row, entity=entity)


def _asof_mutant_answer(context: str, program: dict[str, Any], name: str) -> Any:
    if name == "latest_text":
        # The "use the last thing the text says" shortcut: fold the revealed
        # events in reveal-date order (reading order) instead of the
        # effective-date order the correct program uses. _solve_rows takes
        # the fold order as a parameter, so the wrong program is the same
        # solver run on the other order; where the two orders coincide on a
        # world the mutant is legitimately non-distinguishing there.
        header, rows = families.parse_context(context)
        return families._solve_rows(header, rows, program, order="reveal")
    if name == "ignore_revocation":
        header, rows = families.parse_context(context)
        # Pretend set_aside/release never happened: swap their kinds so the
        # fold never touches `held`.
        rows = [
            (
                _row_with_kind(row, "debit")
                if row.type == "event" and row.kind == "set_aside"
                else _row_with_kind(row, "credit")
                if row.type == "event" and row.kind == "release"
                else row
            )
            for row in rows
        ]
        import json as _json

        ctx = "\n".join(
            [_json.dumps(header, sort_keys=True, separators=(",", ":"))]
            + [
                _json.dumps(_row_visible(row), sort_keys=True, separators=(",", ":"))
                for row in rows
            ]
        )
        return families.solve_visible(ctx, program)
    raise ValueError(f"unknown asof mutant: {name}")


def _row_with_kind(row: Any, kind: str) -> Any:
    import dataclasses

    return dataclasses.replace(row, kind=kind)


def _row_visible(row: Any) -> dict[str, Any]:
    return row.visible() if hasattr(row, "visible") else row


def _wrong_rule_family_answer(
    header: dict[str, Any], rows: list[Any], program: dict[str, Any]
) -> Any:
    """The wrong-family shortcut: apply the other family's formula directly.

    A shortcut model that learned the wrong structure does not re-infer the
    other family's parameters from the demonstrations (which correctly errors
    when they are ambiguous -- 80/84 on the pilot bank). It *applies a rule it
    shouldn't*: the formula of the other structural family over the query
    entity's features, under the world's own declared parameters (the modulus
    and the label alphabet in the header), with the other family's free
    parameters at their canonical values:

    * threshold_class, (a*x + b*y + c) mod m < t: (1, 1, 0, 1) -- "the summed
      features' residue is the zero class", the first label on the zero class.
    * parity_vote, the parity of how many rows fall in a counted residue
      class: residue 0, flip 0 -- the first label on the even count.

    That is a well-defined wrong program -- valid output, different rule, same
    inputs -- so under the P74 §0.2 three-way schema it lands in the
    valid-output cell instead of the error column, and its answer carries the
    rule family it actually applied. The formula functions are the solver's
    own (_threshold_label / _parity_label), so the shortcut is the other
    family's real formula, not a re-implementation.
    """
    other = (
        families.RULE_FAMILIES[
            (families.RULE_FAMILIES.index(header.get("rule_family")) + 1)
            % len(families.RULE_FAMILIES)
        ]
        if header.get("rule_family") in families.RULE_FAMILIES
        else None
    )
    if other is None:
        raise ValueError("rule mutant: no rule family in header")
    # The world's own declared modulus and labels: parity_vote declares
    # PARITY_MODULUS and threshold_class a MODULUS_POOL member, so each
    # formula runs under the parameters the header actually pins.
    modulus = header.get("modulus")
    labels = header.get("labels") or []
    if type(modulus) is not int or not isinstance(labels, list) or len(labels) != 2:
        raise ValueError("rule mutant: no usable modulus or labels in header")

    def shortcut_label(entity_rows: list[Any]) -> str:
        if other == "threshold_class":
            return families._threshold_label(
                (1, 1, 0, 1),
                (sum(row.x for row in entity_rows), sum(row.y for row in entity_rows)),
                labels,
                modulus,
            )
        return families._parity_label(
            (0, 0), [(row.x, row.y) for row in entity_rows], labels, modulus
        )

    def shortcut_features(entity_rows: list[Any]) -> dict[str, int]:
        # The features the shortcut itself computed, in the other family's
        # own feature alphabet: the summed (x, y) for the threshold formula,
        # the (rows, counted) pair for the parity formula (the counted
        # residue under the world's declared modulus, not the parity family's
        # own PARITY_MODULUS).
        if other == "threshold_class":
            return {
                "x": sum(row.x for row in entity_rows),
                "y": sum(row.y for row in entity_rows),
            }
        return {
            "rows": len(entity_rows),
            "counted": sum((row.x + row.y) % modulus == 0 for row in entity_rows),
        }

    entities = families._rule_entities(rows)
    terminal = program["steps"][-1]
    op = terminal["op"]
    if op in ("label", "verify"):
        entity = terminal.get("entity")
        if not isinstance(entity, str) or entity not in entities:
            raise ValueError("the query names an entity with no rows")
        actual = shortcut_label(entities[entity])
        if op == "verify":
            claim = terminal.get("claim")
            if not isinstance(claim, str) or claim not in labels:
                raise ValueError("a verify query must claim a declared label")
            return {
                "rule_family": other,
                "entity": entity,
                "claim": claim,
                "label": actual,
                "holds": claim == actual,
            }
        return {
            "rule_family": other,
            "entity": entity,
            "label": actual,
            "features": shortcut_features(entities[entity]),
        }
    named = families._named_entities(terminal, entities)
    table = {entity: shortcut_label(entities[entity]) for entity in named}
    if op == "labels":
        return {"rule_family": other, "labels": table, "count": len(named)}
    if op == "label_list":
        return [table[entity] for entity in named]
    wanted = terminal.get("label")
    if not isinstance(wanted, str) or wanted not in labels:
        raise ValueError("the query must name a declared label")
    if op == "label_set":
        carried = sorted(entity for entity in named if table[entity] == wanted)
        return {
            "rule_family": other,
            "label": wanted,
            "entities": carried,
            "count": len(carried),
        }
    return {
        "rule_family": other,
        "label": wanted,
        "count": sum(table[entity] == wanted for entity in named),
        "entities": len(named),
    }


def _rule_mutant_answer(context: str, program: dict[str, Any], name: str) -> Any:
    header, rows = families.parse_context(context)

    if name == "wrong_rule_family":
        return _wrong_rule_family_answer(header, rows, program)
    if name == "ignore_demos":
        # The "default label" shortcut: label every entity with the first
        # declared label without reading the demos. The inference still runs
        # (the solver requires a unique rule), but every demo now carries the
        # same label, so the inferred rule is a trivial one -- that is the
        # shortcut model's effective behavior.
        labels = header.get("labels") or []
        if not labels:
            raise ValueError("rule mutant: no labels in header")
        rows = [
            _row_with_label(row, labels[0]) if row.type == "demo" else row
            for row in rows
        ]
        try:
            return families._solve_rows(header, rows, program)
        except ValueError:
            # The mutated demos can leave the rule ambiguous; the shortcut
            # model answers with the default label regardless, so report that
            # directly instead of failing.
            terminal = program["steps"][-1]
            if terminal.get("op") in ("label", "verify"):
                return {"label": labels[0]}
            return {"labels": {}}
    raise ValueError(f"unknown rule mutant: {name}")


def _row_with_label(row: Any, label: str) -> Any:
    import dataclasses

    return dataclasses.replace(row, label=label)


def _set_mutant_answer(context: str, program: dict[str, Any], name: str) -> Any:
    steps = program["steps"]
    scope = steps[0]
    if name == "relax_one_condition":
        # Dropping one condition must leave the bent program inside the
        # solver's 2-to-4 scope gate: a 2-condition scope drops to 1 and the
        # wrong program is not defined (it would be rejected as a malformed
        # scope, which is a legality error, not a semantic wrong answer).
        if len(scope["conditions"]) < 3:
            raise MutantNotApplicable(
                "scope has fewer than 3 conditions; the relaxed program "
                "falls below the solver's 2-to-4 condition gate"
            )
        kept = list(scope["conditions"][1:])
    elif name == "tighten_one_condition":
        # The subset error: sharpen the numeric bound a member sits exactly
        # on, so that boundary member falls out of the set. The wrong program
        # is DEFINED only where it can bite: the scope must carry a numeric
        # bound to sharpen (amount or date with >= or <=) and a member must
        # sit exactly on one, else the subset error has no boundary row to
        # drop -- that is the charter's applicability question, not a
        # distinction. Both facts are read from the set solver's own data
        # (families._set_members over the parsed rows), and the bent program
        # is re-executed so the solver's outcome -- including a valid answer
        # that happens to coincide, e.g. a contains verdict whose named row
        # is not the dropped boundary member -- counts honestly.
        _, rows = families.parse_context(context)
        conds = list(scope["conditions"])
        bounds = [
            index
            for index, cond in enumerate(conds)
            if cond["field"] in ("amount", "date") and cond["op"] in (">=", "<=")
        ]
        if not bounds:
            raise MutantNotApplicable(
                "scope has no numeric bound condition; the tightened program "
                "has no bound to sharpen"
            )
        members = families._set_members(rows, steps)

        def member_on(index: int) -> bool:
            cond = conds[index]
            field = "amount" if cond["field"] == "amount" else "day"
            return any(getattr(row, field) == cond["value"] for row in members)

        hits = [index for index in bounds if member_on(index)]
        if not hits:
            raise MutantNotApplicable(
                "no world member sits exactly on the scope's numeric bound; "
                "the subset error has no boundary member to drop"
            )
        kept = [dict(cond) for cond in conds]
        index = hits[0]
        cond = kept[index]
        if cond["field"] == "amount":
            cond["value"] = (
                cond["value"] + 1 if cond["op"] == ">=" else cond["value"] - 1
            )
        else:
            bound = date.fromisoformat(cond["value"])
            moved = (
                bound + timedelta(days=1)
                if cond["op"] == ">="
                else bound - timedelta(days=1)
            )
            cond["value"] = moved.isoformat()
        kept[index] = cond
    else:
        raise ValueError(f"unknown set mutant: {name}")
    mutated = {**program, "steps": [{**scope, "conditions": kept}, dict(steps[-1])]}
    return families.solve_visible(context, mutated)


# ---------------------------------------------------------------------------
# Witness report
# ---------------------------------------------------------------------------


def _equal(a: Any, b: Any) -> bool:
    return _canonical(a) == _canonical(b)


def _canonical(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {
            k: _canonical(v) for k, v in sorted(value.items(), key=lambda kv: kv[0])
        }
    return value


def witness_report(
    family: str, context: str, program: dict[str, Any]
) -> dict[str, Any]:
    """Per-row witness computation: which mutants this row's world separates.

    Returns {"unsupported": true} for families without a mutant surface
    (join_unanswerable, dense_aggregate, research_*): their contracts are
    single-step verdicts whose "wrong program" space is not yet modeled.

    Three-way schema per mutant (P74 charter §0.2): `applicable` (the wrong
    program is defined on this instance), `valid_output` (it returned an
    answer rather than erroring), `distinguished` (valid output AND != correct
    answer) with `semantic_distinguished` as its alias in the output dict.
    Erroring mutants are NOT semantically distinguished. The denominator of
    `semantic_distinguished_fraction` is every mutant of the family, so dead
    or not-applicable mutants dilute the fraction and stay visible in the
    family aggregation.
    """
    if family not in _MUTANT_SETS:
        return {"unsupported": True}
    try:
        answer = answer_of(family, context, program)
    except Exception as exc:  # malformed rows must fail loud in the audit
        return {"unsupported": True, "error": f"correct answer failed: {exc}"}
    mutants: dict[str, Any] = {}
    semantic_distinguished = 0
    for name in mutants_for(family):
        try:
            if family in _RECORDS_FAMILIES:
                mutated = _records_mutant_answer(family, context, program, name)
            elif family == "alias_locate":
                mutated = _alias_mutant_answer(context, program, name)
            elif family == "asof_state":
                mutated = _asof_mutant_answer(context, program, name)
            elif family == "rule_holdout":
                mutated = _rule_mutant_answer(context, program, name)
            else:
                mutated = _set_mutant_answer(context, program, name)
        except MutantNotApplicable as exc:
            # The wrong program has no output on this instance: neither a
            # valid output nor a distinction; the reason records why.
            mutants[name] = {
                "applicable": False,
                "not_applicable_reason": exc.reason,
                "valid_output": False,
                "distinguished": False,
                "semantic_distinguished": False,
            }
            continue
        except Exception as exc:
            # An erroring mutant cannot produce a legal answer, so it is NOT
            # semantically distinguished (the old error-is-distinguished rule
            # conflated legality errors with semantic wrong answers); the
            # error text keeps the audit informative.
            mutants[name] = {
                "applicable": True,
                "valid_output": False,
                "error": str(exc),
                "distinguished": False,
                "semantic_distinguished": False,
            }
            continue
        differs = not _equal(answer, mutated)
        if differs:
            semantic_distinguished += 1
        mutants[name] = {
            "applicable": True,
            "valid_output": True,
            "answer": mutated,
            "distinguished": differs,
            "semantic_distinguished": differs,
        }
    total = len(mutants_for(family))
    return {
        "answer": answer,
        "mutants": mutants,
        "distinguished_count": semantic_distinguished,
        "distinguished_fraction": round(semantic_distinguished / total, 3),
        "semantic_distinguished_count": semantic_distinguished,
        "semantic_distinguished_fraction": round(semantic_distinguished / total, 3),
    }
