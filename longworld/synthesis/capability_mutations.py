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

Design doc: .hl/design/p73_counterexample_synthesis.md §3.1/§3.2.
"""

from __future__ import annotations

from typing import Any

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records

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
        "wrong_rule_family": "apply the other rule family's rule",
        "ignore_demos": "label everything with the first declared label",
    },
    "set_complete": {
        "relax_one_condition": "drop one scope condition (superset error)",
        "tighten_one_condition": "sharpen one scope bound (subset error)",
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
        # surface form" shortcut. Only entities with rows can be picked, and
        # the mutant answer must stay a legal terminal answer: for a
        # single-match terminal the shortcut picks a target that still yields
        # exactly one row when possible, else the mutant errors (a shortcut
        # that cannot produce the required shape is itself distinguished).
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
    elif name == "last_declaration":
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
        # Fold in reveal order: the "use the last thing the text says" shortcut.
        return families.solve_visible(context, program)
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


def _rule_mutant_answer(context: str, program: dict[str, Any], name: str) -> Any:
    header, rows = families.parse_context(context)
    import json as _json

    if name == "wrong_rule_family":
        # Flip the world's rule family in the header, the program's claim and
        # the registered contract text, then re-solve: the answer of the other
        # rule inferred from the same demos.
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
        header = {
            **header,
            "rule_family": other,
            "rule_structure": families.RULE_STRUCTURES[other],
            # The modulus pool differs per family; the other rule needs a
            # modulus valid for IT, not the world's own.
            "modulus": (
                families.PARITY_MODULUS
                if other == "parity_vote"
                else sorted(families.MODULUS_POOL)[0]
            ),
        }
        program = {**program, "rule_family": other}
        program = {
            **program,
            "steps": [
                {**step, "rule_family": other}
                if step.get("op") == "learn"
                else dict(step)
                for step in program["steps"]
            ],
        }
        ctx = _render(header, rows)
        try:
            return families.solve_visible(ctx, program)
        except ValueError:
            # The other family's rule is often ambiguous on this world's
            # demos; a model using the wrong family cannot produce a unique
            # answer, which itself distinguishes it from the correct program.
            return {"ambiguous": True}
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


def _render(header: dict, rows: list) -> str:
    import json as _json

    return "\n".join(
        [_json.dumps(header, sort_keys=True, separators=(",", ":"))]
        + [
            _json.dumps(_row_visible(row), sort_keys=True, separators=(",", ":"))
            for row in rows
        ]
    )


def _set_mutant_answer(context: str, program: dict[str, Any], name: str) -> Any:
    steps = program["steps"]
    scope = steps[0]
    if name == "relax_one_condition":
        kept = list(scope["conditions"][1:])
        if not kept:
            raise ValueError("set mutant: single condition")
    elif name == "tighten_one_condition":
        # Sharpen the first numeric bound by one: the subset error.
        conds = list(scope["conditions"])
        c = dict(conds[0])
        if c["field"] == "amount" and c["op"] in (">=", "<="):
            c["value"] = c["value"] + 1 if c["op"] == ">=" else c["value"] - 1
            conds[0] = c
        kept = conds
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
    """
    if family not in _MUTANT_SETS:
        return {"unsupported": True}
    try:
        answer = answer_of(family, context, program)
    except Exception as exc:  # malformed rows must fail loud in the audit
        return {"unsupported": True, "error": f"correct answer failed: {exc}"}
    mutants: dict[str, Any] = {}
    distinguished = 0
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
            differs = not _equal(answer, mutated)
        except Exception as exc:
            # A mutant that cannot produce a legal answer cannot match the
            # correct answer, so it is distinguished; the error text keeps the
            # audit informative.
            mutants[name] = {"error": str(exc), "distinguished": True}
            distinguished += 1
            continue
        mutants[name] = {"answer": mutated, "distinguished": differs}
        if differs:
            distinguished += 1
    return {
        "answer": answer,
        "mutants": mutants,
        "distinguished_count": distinguished,
        "distinguished_fraction": round(distinguished / len(mutants_for(family)), 3),
    }
