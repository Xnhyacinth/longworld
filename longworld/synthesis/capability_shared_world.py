"""P73 shared-world compiler: one world, many capabilities, one context.

The current spine builds family-specific worlds: `generate_world(seed,
family, ...)` renders a world whose rows are purpose-built for ONE family's
programs, so cross-capability sharing never happens — the original project
goal ("one shared world → multiple long-context capabilities") is only
partially realized. This module closes that gap without rewriting the spine:
it composes per-family worlds' *rows* into one shared record space and then
instantiates task programs from MULTIPLE families over the same rendered
context.

Mechanics:
- `build_shared_world(seed, families, length_records, consumed_per_family,
  depth)` samples one family world per requested family at the same seed
  offset, merges their primary/reference rows (id-collision-free: ids are
  96-bit hex draws), and renders ONE context with a multi-family header.
- `applicable_programs(world, families)` re-instantiates each family's
  program against the shared context by RE-USING the source family worlds'
  questions (their constants bind to their own rows, which are all present in
  the shared context), solving them with the owning module's executor.
- Each task row carries its own family and the shared `context_sha256`, so
  the bank index can measure "distinct capabilities per context" directly —
  the metric the user's review asked for.

Honesty limits as everywhere: source_kind "simulated", fail-closed flags.

Design doc: .hl/design/p73_counterexample_synthesis.md §5; related-work notes:
.hl/design/p73_related_work_notes.md (MuSiQue connector discipline, SWE-smith
cost structure).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records

SHARED_VERSION = "capability-shared-world-v1"

# Families whose worlds can share a record space: the records trio renders
# `record`/`reference` rows under a records header; the F-families render
# their own row types under a families header, so cross-module merging is
# done only within each module's own universe.
_RECORDS_SIDE = ("filter_aggregate", "group_compare", "join_lookup")
_FAMILY_SIDE = ("alias_locate", "asof_state", "rule_holdout", "set_complete")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_shared_world(
    seed: int,
    family_group: tuple[str, ...],
    length_records: int = 800,
    consumed_per_family: int = 20,
    depth: int = 2,
) -> dict[str, Any]:
    """One world whose context hosts every family in `family_group`.

    `family_group` must come from one side (records or F-families): the two
    modules render different header contracts, and a shared context can only
    pin one. Cross-side sharing is a later, renderer-level change.
    """
    side = None
    for group in (_RECORDS_SIDE, _FAMILY_SIDE):
        if all(f in group for f in family_group):
            side = group
    if side is None or not family_group:
        raise ValueError("family_group must be non-empty and from one module side")
    if len(set(family_group)) != len(family_group):
        raise ValueError("family_group must not repeat a family")

    per_family = max(64, length_records // max(1, len(family_group)))
    # F-families' evidence multipliers are wider, so their K must stay a small
    # fraction of their L; records families tolerate a sixth. Cap by side.
    if all(f in _RECORDS_SIDE for f in family_group):
        cap = per_family // 6
    else:
        cap = per_family // 12
    consumed = max(4, min(consumed_per_family, cap))
    rows: list[Any] = []
    tasks: list[dict[str, Any]] = []
    header_extras: dict[str, dict[str, Any]] = {}
    solve_rows: dict[str, list[Any]] = {}
    consumed_total = 0
    for offset, family in enumerate(family_group):
        gen = (
            records.generate_world if side is _RECORDS_SIDE else families.generate_world
        )
        # H=1-only families (set_complete, dense_aggregate) cannot take depth 2;
        # they share the world at their own depth.
        family_depth = depth
        if family in ("set_complete", "dense_aggregate"):
            family_depth = 1
        sub = gen(
            seed * 31 + offset,
            family,
            length_records=per_family,
            consumed_records=consumed,
            depth=family_depth,
        )
        sub_rows = _parse_rows(sub["context"], side is _FAMILY_SIDE)
        rows.extend(sub_rows)
        # Keep the sub-world's header EXTRAS (rule_family/modulus/labels for
        # rule worlds, asof cutoff declarations, etc.): the shared header must
        # carry every member's extras under its family name.
        sub_header = _parse_header(sub["context"])
        header_extras[family] = {
            k: v
            for k, v in sub_header.items()
            if k not in ("schema", "family", "rules")
        }
        # F-family solvers validate row SCHEMAS strictly (a set_complete record
        # lacks rule features), so each family's tasks solve over the sub-rows
        # it was generated with — which the merged context contains verbatim.
        # Records-side families share one row schema, so their tasks solve over
        # the FULL merged rows: cross-family row interference is real there.
        solve_rows[family] = rows if side is _RECORDS_SIDE else sub_rows
        for task in sub["tasks"]:
            task = dict(task)
            task["family"] = family
            task["source_world_id"] = sub["world_id"]
            # Sub-worlds each name their variants q0..qn, so namespace by family
            # to keep task ids unique within the shared world.
            task["task_id"] = f"{family}:{task['task_id']}"
            tasks.append(task)
        consumed_total += sum(t["consumed_count"] for t in sub["tasks"])

    # Row ids are 96-bit hex with distinct prefixes per module; collisions
    # across sub-worlds would be a generation defect, so assert it.
    ids = [row.id for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("row id collision across shared-world members")
    rendered = render_shared_context(rows, family_group, header_extras)
    header = json.loads(rendered.splitlines()[0])
    context_sha = hashlib.sha256(rendered.encode()).hexdigest()[:16]
    for task in tasks:
        # Solve with the task's own family header over the rows that family
        # reads (records side: all merged rows — shared schema; F side: the
        # member's sub-rows, which the merged context contains verbatim).
        # The training context carries all families' contracts; a correct
        # reader of the merged text produces exactly these answers.
        fam = family_of(task)
        solve_header = {
            "schema": getattr(families, "VERSION", "capability-families-v1")
            if fam not in _RECORDS_SIDE
            else records.VERSION,
            "family": fam,
            "rules": (
                families.PROTOCOLS[fam]
                if fam not in _RECORDS_SIDE
                else records.PROTOCOLS[fam]
            ),
            **header_extras.get(fam, {}),
        }
        solve_context = "\n".join(
            [_dump(solve_header)] + [_dump(row.visible()) for row in solve_rows[fam]]
        )
        task["answer"] = solve_task(fam, solve_context, task["question"])
        # Keep the exact context the answer was solved with: F-family solvers
        # validate the header strictly (family + rules + the family's own
        # extras, none of which the shared multi-family header carries), and
        # audit-side consumers (witness mutants, solver recheck) need this
        # view. One copy per task: row.visible() reprs are deterministic
        # across a re-parse, so this stays byte-identical on regeneration.
        task["solve_context"] = solve_context
        task["solve_context_sha256"] = hashlib.sha256(
            solve_context.encode()
        ).hexdigest()[:16]

    return {
        "schema_version": SHARED_VERSION,
        "world_id": f"{SHARED_VERSION}-{context_sha}",
        "context_sha256": context_sha,
        "seed": seed,
        "families": sorted(family_group),
        "length_records": len(rows),
        "consumed_rows_total": consumed_total,
        "context": rendered,
        "header": header,
        "tasks": tasks,
        "honesty": {
            "source_kind": "simulated",
            "strict_long_dependency_verified": False,
            "model_utility_measured": False,
            "production_eligible": False,
        },
    }


def family_of(task: dict[str, Any]) -> str:
    return task["capability"] if "capability" in task else task["family"]


def solve_task(family: str, context: str, question: dict[str, Any]) -> Any:
    """Execute one family's program against the shared context."""
    if family in _RECORDS_SIDE:
        return records.solve_visible(context, question)
    return families.solve_visible(context, question)


def _parse_rows(context: str, family_side: bool) -> list[Any]:
    if family_side:
        header, rows = families.parse_context(context)
    else:
        header, rows = records.parse_context(context)
    return rows


def _parse_header(context: str) -> dict[str, Any]:
    return json.loads(context.splitlines()[0])


def _family_context(rows: list[Any], family: str) -> str:
    """The shared rows under ONE family's canonical header, for solving.

    The executor's contract check reads the header; the rows are byte-identical
    to the merged training context, so answers computed here are the answers a
    correct reader of the merged context must produce.
    """
    if family in _RECORDS_SIDE:
        header = {
            "schema": records.VERSION,
            "family": family,
            "rules": records.PROTOCOLS[family],
        }
    else:
        header = {
            "schema": getattr(families, "VERSION", "capability-families-v1"),
            "family": family,
            "rules": families.PROTOCOLS[family],
        }
    body = [_dump(row.visible()) for row in rows]
    return "\n".join([_dump(header)] + body)


def render_shared_context(
    rows: list[Any],
    family_group: tuple[str, ...],
    header_extras: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Re-render merged rows under one multi-family header (the training view).

    The header carries every member family's contract text plus its header
    extras (rule_family/modulus/labels, asof cutoffs) under its own name, so a
    reader of this one context can solve ANY member family's task.
    """
    if all(f in _RECORDS_SIDE for f in family_group):
        protocols = records.PROTOCOLS
    else:
        protocols = families.PROTOCOLS
    header: dict[str, Any] = {
        "schema": (
            records.VERSION
            if all(f in _RECORDS_SIDE for f in family_group)
            else getattr(families, "VERSION", "capability-families-v1")
        ),
        "family": "shared:" + "+".join(sorted(family_group)),
        "rules": {f: protocols[f] for f in sorted(family_group)},
    }
    for family in sorted(family_group):
        extras = (header_extras or {}).get(family, {})
        for key, value in extras.items():
            # Rule-family keys are shared names (rule_family/modulus/labels)
            # across members; namespace them per family to avoid collisions.
            if key in ("rule_family", "modulus", "labels", "rule_structure"):
                header[f"{family}.{key}"] = value
            else:
                header[key] = value
    body = [_dump(row.visible()) for row in rows]
    return "\n".join([_dump(header)] + body)
