"""P74 T7: the topic x capability x length support matrix and the six-stage
generation funnel, computed from the real wave-2 artifacts.

Charter (.hl/design/p74_real_shared_worlds.md) §16 T7 row: consume the whole
wave's outputs — the frozen wiki snapshot manifest (W2-W), the wiki->world
bridge (W2-A), the structure-driven task bank (W2-B), the length controller
band report (T5) — and report ACTUAL coverage: every (topic, capability
family, length band) cell, with infeasible and empty cells kept visible
("不可行格不消失").

The matrix is honest about cell states:
- supported: the bank produced >= 1 executed, non-degenerate task;
- skipped:   the bank recorded a structure-skip reason (the family's
             enabling structure is absent in that world);
- infeasible: structure partially exists but nothing instantiated (e.g.
             versioned timelines with zero bindable subjects), or a zero
             the bank did not classify — never silently dropped.

The funnel is charter §11 milestone-2's six stages (source availability ->
fact/relation parse -> program instantiability -> semantic dedup retention
-> text answerability -> dependency profile pass). Each stage reports
count-in / count-out / rate over the worlds in scope (the demo semantic
world plus every bridged wiki topic), plus real-topic-only columns, because
the demo world currently carries all the mass and the aggregate rate would
otherwise hide the real bottleneck. Stages without data render as
"not yet measured" rather than being invented.

Usage:
  .venv/bin/python scripts/p74_support_matrix.py \
      [--manifest PATH] [--length-report PATH] [--json OUT.json] \
      [--cache PATH] [--no-demo] [--reuse-cache]

Defaults: the frozen manifest under data/capability_records/
p74_wiki_snapshot_v1/, the wave-2 length report (searched: data/
capability_records, then the job tmp dir; rebuilt deterministically from
scripts/p74_length_report.py when absent), output
data/capability_records/p74_support_matrix_v1.json, cache
$CLAUDE_JOB_DIR/tmp/p74_support_matrix_worlds.json.

Deterministic: sorted iteration everywhere, no RNG, standard library only
for this module's own logic; the gathering layer runs the already-tested
bank/bridge/length modules read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    REPO / "data" / "capability_records" / "p74_wiki_snapshot_v1" / "manifest.json"
)
DEFAULT_JSON_OUT = REPO / "data" / "capability_records" / "p74_support_matrix_v1.json"
SCHEMA = "longworld.p74-support-matrix.v1"

# The bank's family universe is the matrix's capability columns.
from longworld.synthesis.world_task_bank import CAPABILITY_FAMILIES

LARGE_BUDGET = {family: 1000 for family in CAPABILITY_FAMILIES}

FUNNEL_STAGES = (
    "source_availability",
    "fact_parse",
    "program_instantiability",
    "semantic_dedup_retention",
    "text_answerability",
    "dependency_profile_pass",
)


# --- pure core: plain dicts in, plain dicts out; fully unit-testable ---


def family_cell(
    count: int,
    skip_reason: str | None = None,
    infeasible_reason: str | None = None,
) -> dict[str, Any]:
    """One (world, family) matrix cell; zeros are classified, never dropped."""
    if count > 0:
        return {"status": "supported", "tasks": int(count)}
    if skip_reason is not None:
        return {"status": "skipped", "tasks": 0, "reason": skip_reason}
    if infeasible_reason is not None:
        return {"status": "infeasible", "tasks": 0, "reason": infeasible_reason}
    return {
        "status": "infeasible",
        "tasks": 0,
        "reason": (
            "zero tasks with no recorded skip or rejection (unclassified zero "
            "kept visible per charter: infeasible cells do not disappear)"
        ),
    }


def world_row(
    name: str,
    kind: str,
    topic: str,
    families: dict[str, dict[str, Any]],
    *,
    pages: int | None = None,
    entities: int | None = None,
    typed_entities: int | None = None,
    facts: int | None = None,
    band: int | None = None,
    band_verdict: str | None = None,
    band_feasible: bool | None = None,
    natural_tokens: int | None = None,
    candidates: dict[str, int] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """One matrix row: a world, its length placement and its family cells."""
    row: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "topic": topic,
        "families": {family: families.get(family) for family in CAPABILITY_FAMILIES},
    }
    if pages is not None:
        row["pages"] = pages
    if entities is not None:
        row["entities"] = entities
    if typed_entities is not None:
        row["typed_entities"] = typed_entities
    if facts is not None:
        row["facts"] = facts
    if band is not None:
        row["length"] = {
            "band": band,
            "verdict": band_verdict,
            "feasible": band_feasible,
            "natural_tokens": natural_tokens,
        }
    if candidates is not None:
        row["candidates"] = dict(candidates)
    if note is not None:
        row["note"] = note
    return row


def band_cells(length_report: dict[str, Any]) -> list[dict[str, Any]]:
    """The band table from the T5 report, every band visible, empty included."""
    cells = []
    for cell in length_report.get("band_cells", []):
        cells.append(
            {
                "band": int(cell["band"]),
                "assigned": list(cell.get("assigned", [])),
                "success": int(cell.get("success", 0)),
                "pending_expansion": int(cell.get("pending_expansion", 0)),
                "failure": int(cell.get("failure", 0)),
                "infeasible": int(cell.get("infeasible", 0)),
                "empty": bool(cell.get("empty", False)),
            }
        )
    return sorted(cells, key=lambda c: c["band"])


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cell status counts per family and overall; zeros stay in the report."""
    by_status: dict[str, int] = {}
    per_family = {
        family: {"supported": 0, "skipped": 0, "infeasible": 0}
        for family in CAPABILITY_FAMILIES
    }
    total_tasks = 0
    for row in rows:
        for family in CAPABILITY_FAMILIES:
            cell = row["families"][family]
            if cell is None:
                continue
            status = cell["status"]
            by_status[status] = by_status.get(status, 0) + 1
            per_family[family][status] += 1
            total_tasks += cell.get("tasks", 0)
    return {
        "worlds": len(rows),
        "cells": sum(by_status.values()),
        "by_status": {
            status: by_status.get(status, 0)
            for status in ("supported", "skipped", "infeasible")
        },
        "per_family": per_family,
        "total_tasks": total_tasks,
    }


def funnel_stage(
    name: str,
    unit: str,
    count_in: int,
    count_out: int,
    *,
    real_in: int | None = None,
    real_out: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """One measured funnel stage: in >= out, rate = out/in (0 when in == 0)."""
    stage: dict[str, Any] = {
        "stage": name,
        "status": "measured",
        "unit": unit,
        "in": int(count_in),
        "out": int(count_out),
    }
    stage["rate"] = (
        round(count_out / count_in, 4)
        if count_in > 0
        else (0.0 if count_in == 0 else None)
    )
    if real_in is not None:
        stage["real_topic_in"] = int(real_in)
    if real_out is not None:
        stage["real_topic_out"] = int(real_out)
    if note:
        stage["note"] = note
    return stage


def not_measured_stage(name: str, unit: str, note: str) -> dict[str, Any]:
    """A funnel stage for which no artifact exists yet — honest placeholder."""
    return {
        "stage": name,
        "status": "not-yet-measured",
        "unit": unit,
        "in": None,
        "out": None,
        "rate": None,
        "note": note,
    }


def validate_funnel(stages: list[dict[str, Any]]) -> None:
    """Funnel arithmetic: non-negative integers, out <= in, rate in [0, 1]."""
    for stage in stages:
        name = stage.get("stage", "<unnamed>")
        if stage.get("status") == "not-yet-measured":
            if stage.get("rate") is not None or stage.get("in") is not None:
                raise ValueError(
                    f"funnel stage {name}: not-yet-measured carries numbers"
                )
            continue
        count_in, count_out = stage.get("in"), stage.get("out")
        if not isinstance(count_in, int) or not isinstance(count_out, int):
            raise TypeError(f"funnel stage {name}: counts must be integers")
        if count_in < 0 or count_out < 0:
            raise ValueError(f"funnel stage {name}: negative counts")
        if count_out > count_in:
            raise ValueError(
                f"funnel stage {name}: out ({count_out}) exceeds in ({count_in})"
            )
        rate = stage.get("rate")
        if rate is None or not 0.0 <= rate <= 1.0:
            raise ValueError(f"funnel stage {name}: rate {rate} outside [0, 1]")


def build_funnel(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the funnel; the bottleneck is the earliest stage that zeroes."""
    validate_funnel(stages)
    bottleneck = None
    for stage in stages:
        if stage.get("status") != "measured":
            continue
        if stage["out"] == 0:
            bottleneck = {
                "stage": stage["stage"],
                "why": "count-out is zero at this stage",
                "note": stage.get("note", ""),
            }
            break
    if bottleneck is None:
        measured = [s for s in stages if s.get("status") == "measured"]
        if measured:
            worst = min(measured, key=lambda s: s["rate"])
            if worst["rate"] < 1.0:
                bottleneck = {
                    "stage": worst["stage"],
                    "why": f"lowest retention rate {worst['rate']}",
                    "note": worst.get("note", ""),
                }
    # real-topic bottleneck: same logic on the real_topic_* columns only
    real_bottleneck = None
    for stage in stages:
        if stage.get("status") != "measured":
            continue
        real_out = stage.get("real_topic_out")
        if real_out is not None and real_out == 0:
            real_bottleneck = {
                "stage": stage["stage"],
                "why": "real-topic count-out is zero at this stage",
                "note": stage.get("note", ""),
            }
            break
    return {
        "stages": stages,
        "bottleneck": bottleneck,
        "real_topic_bottleneck": real_bottleneck,
        "note": (
            "real_topic_* columns count only the 7 bridged wiki topics; the "
            "demo semantic world is synthetic and carries no source-funnel mass"
        ),
    }


def _reason_codes(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Assign stable codes (S1.., I1..) to non-supported cells, in row order."""
    legend: dict[str, dict[str, str]] = {}
    counters = {"S": 0, "I": 0}
    for row in rows:
        for family in CAPABILITY_FAMILIES:
            cell = row["families"][family]
            if cell is None or cell["status"] == "supported":
                continue
            prefix = "S" if cell["status"] == "skipped" else "I"
            counters[prefix] += 1
            code = f"{prefix}{counters[prefix]}"
            cell["code"] = code
            legend[code] = {
                "world": row["name"],
                "family": family,
                "status": cell["status"],
                "reason": cell["reason"],
            }
    return legend


def _print_table(lines: list[tuple[str, ...]], out: list[str]) -> None:
    widths = [max(len(row[i]) for row in lines) for i in range(len(lines[0]))]
    for row in lines:
        out.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())


def _fmt_rate(stage: dict[str, Any]) -> str:
    if stage.get("status") == "not-yet-measured":
        return "not yet measured"
    rate = stage["rate"]
    return "-" if rate is None else f"{rate:.3f}"


def render_matrix_table(rows: list[dict[str, Any]]) -> str:
    """world x family matrix with the length placement; codes -> full reasons."""
    out: list[str] = []
    header = (
        "world",
        "kind",
        "band",
        "band verdict",
        *CAPABILITY_FAMILIES,
    )
    lines = [header]
    for row in rows:
        length = row.get("length", {})
        cells = []
        for family in CAPABILITY_FAMILIES:
            cell = row["families"][family]
            if cell is None:
                cells.append("-")
            elif cell["status"] == "supported":
                cells.append(str(cell["tasks"]))
            else:
                # raw rows (before build_matrix) carry no code yet
                cells.append(cell.get("code", cell["status"]))
        lines.append(
            (
                row["name"],
                row["kind"],
                str(length.get("band", "-")),
                str(length.get("verdict", "-")),
                *cells,
            )
        )
    _print_table(lines, out)
    return "\n".join(out)


def render_reason_legend(legend: dict[str, dict[str, str]]) -> str:
    if not legend:
        return "cell codes: none (every cell supported)"
    lines = [("code", "world", "family", "status", "reason")]
    for code in sorted(legend, key=lambda c: (c[0], int(c[1:]))):
        entry = legend[code]
        lines.append(
            (
                code,
                entry["world"],
                entry["family"],
                entry["status"],
                entry["reason"],
            )
        )
    out: list[str] = []
    _print_table(lines, out)
    return "\n".join(out)


def render_structure_table(rows: list[dict[str, Any]]) -> str:
    """Per-world source structure: what the funnel actually had to work on."""
    out: list[str] = []
    lines = [
        ("world", "topic", "pages", "entities", "typed", "facts", "cand-in", "cand-out")
    ]
    for row in rows:
        cand = row.get("candidates", {})
        lines.append(
            (
                row["name"],
                row.get("topic", "-"),
                str(row.get("pages", "-")),
                str(row.get("entities", "-")),
                str(row.get("typed_entities", "-")),
                str(row.get("facts", "-")),
                str(cand.get("in", "-")),
                str(cand.get("out", "-")),
            )
        )
    _print_table(lines, out)
    return "\n".join(out)


def render_band_table(cells: list[dict[str, Any]]) -> str:
    """Band distribution; empty bands render as EMPTY, never dropped."""
    out: list[str] = []
    lines = [
        (
            "band",
            "assigned",
            "success",
            "pending-expansion",
            "failure",
            "infeasible",
            "empty",
        )
    ]
    for cell in cells:
        lines.append(
            (
                str(cell["band"]),
                str(len(cell["assigned"])),
                str(cell["success"]),
                str(cell["pending_expansion"]),
                str(cell["failure"]),
                str(cell["infeasible"]),
                "EMPTY" if cell["empty"] else "",
            )
        )
    _print_table(lines, out)
    return "\n".join(out)


def render_funnel_table(funnel: dict[str, Any]) -> str:
    out: list[str] = []
    lines = [("stage", "unit", "in", "out", "rate", "real-in", "real-out")]
    for stage in funnel["stages"]:
        lines.append(
            (
                stage["stage"],
                stage["unit"],
                "-" if stage.get("in") is None else str(stage["in"]),
                "-" if stage.get("out") is None else str(stage["out"]),
                _fmt_rate(stage),
                "-"
                if stage.get("real_topic_in") is None
                else str(stage["real_topic_in"]),
                "-"
                if stage.get("real_topic_out") is None
                else str(stage["real_topic_out"]),
            )
        )
    _print_table(lines, out)
    out.append("")
    bottleneck = funnel.get("bottleneck")
    if bottleneck:
        out.append(
            f"bottleneck: {bottleneck['stage']} — {bottleneck['why']}"
            + (f" ({bottleneck['note']})" if bottleneck.get("note") else "")
        )
    else:
        out.append("bottleneck: no stage zeroes out or drops below rate 1.0")
    real_bottleneck = funnel.get("real_topic_bottleneck")
    if real_bottleneck:
        out.append(
            f"real-topic bottleneck: {real_bottleneck['stage']} — "
            f"{real_bottleneck['why']}"
        )
    return "\n".join(out)


def render_report(matrix: dict[str, Any], funnel: dict[str, Any]) -> str:
    rows = matrix["rows"]
    out: list[str] = []
    out.append("P74 T7 support matrix (topic x capability family x length band)")
    out.append(
        "Length bands use T5 text-concat for real Wiki and audit render for the demo; neither is a final reader message."
    )
    out.append(f"worlds: {len(rows)}; families: {', '.join(CAPABILITY_FAMILIES)}")
    out.append("")
    out.append("Matrix (cell = task count when supported, else a code):")
    out.append(render_matrix_table(rows))
    out.append("")
    out.append("Cell-code reasons (skipped / infeasible cells stay visible):")
    out.append(render_reason_legend(matrix["reason_legend"]))
    out.append("")
    out.append("Source structure per world (what the funnel worked on):")
    out.append(render_structure_table(rows))
    out.append("")
    out.append("Length band distribution (empty bands stay visible):")
    out.append(render_band_table(matrix["band_cells"]))
    out.append("")
    out.append(
        "Coverage summary: "
        + json.dumps(matrix["coverage_summary"], ensure_ascii=False)
    )
    out.append("")
    out.append("Generation funnel (charter §11 milestone 2):")
    out.append(render_funnel_table(funnel))
    out.append("")
    for stage in funnel["stages"]:
        if stage.get("note"):
            out.append(f"  {stage['stage']}: {stage['note']}")
    out.append("")
    for note in matrix.get("notes", []):
        out.append(f"note: {note}")
    return "\n".join(out)


def build_matrix(
    world_details: list[dict[str, Any]],
    length_report: dict[str, Any],
    *,
    include_demo: bool,
) -> dict[str, Any]:
    """Assemble the matrix dict from gathered per-world details + T5 report."""
    length_by_name = {}
    for row in length_report.get("band_assignment", []):
        length_by_name[row["name"]] = row
    length_kind_by_name = {
        row["name"]: row["kind"]
        for row in length_report.get("inputs", [])
        if "name" in row and "kind" in row
    }

    rows = []
    for detail in world_details:
        if detail["kind"] == "demo" and not include_demo:
            continue
        length = length_by_name.get(detail["name"])
        candidates = {
            "in": detail.get("candidates_in"),
            "out": detail.get("candidates_out"),
            "rejected": detail.get("rejected"),
            "rejected_log_may_be_capped": detail.get("rejected_capped", False),
        }
        row = world_row(
            detail["name"],
            detail["kind"],
            detail["topic"],
            detail["family_cells"],
            pages=detail.get("pages"),
            entities=detail.get("entities"),
            typed_entities=detail.get("typed_entities"),
            facts=detail.get("facts"),
            band=length.get("band") if length else None,
            band_verdict=length.get("verdict") if length else None,
            band_feasible=length.get("feasible") if length else None,
            natural_tokens=length.get("natural_tokens") if length else None,
            candidates=candidates,
            note=detail.get("note"),
        )
        if "length" in row:
            row["length"]["measured_view"] = length_kind_by_name.get(
                detail["name"], "unknown"
            )
        rows.append(row)
    legend = _reason_codes(rows)
    return {
        "family_columns": list(CAPABILITY_FAMILIES),
        "rows": rows,
        "reason_legend": legend,
        "coverage_summary": coverage_summary(rows),
        "band_cells": band_cells(length_report),
        "bands": length_report.get("bands"),
        "notes": [
            "infeasible and empty cells are never dropped (charter §16 T7)",
            (
                "family cells come from the W2-B bank's own skip log plus derived "
                "infeasible classification for zeros the bank did not skip"
            ),
            "band columns come from the T5 length report; row length.measured_view records text-concat or semantic-world, neither the final chat length",
        ],
    }


# --- gathering layer: runs the landed wave-2 modules read-only ---


def _typed_entities(world: Any) -> int:
    return sum(1 for entity in world.entities if entity.entity_type is not None)


def _zero_families(
    bank: Any,
) -> tuple[dict[str, str], list[str]]:
    """Map family -> skip reason from the bank's skip log; list the rest."""
    skips = {skip.family: skip.reason for skip in bank.skipped}
    counts = bank.counts()
    unclassified = [
        family
        for family in CAPABILITY_FAMILIES
        if counts[family] == 0 and family not in skips
    ]
    return skips, unclassified


def _classify_family_cells(
    bank: Any, world: Any, detail: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Every family cell classified: supported, skipped, or infeasible."""
    counts = bank.counts()
    skips, _unclassified = _zero_families(bank)
    cells = {}
    for family in CAPABILITY_FAMILIES:
        if counts[family] > 0:
            cells[family] = family_cell(counts[family])
        elif family in skips:
            cells[family] = family_cell(0, skip_reason=skips[family])
        else:
            versioned = bank.structure.get("versioned_relations") or []
            typed = detail.get("typed_entities", 0)
            total = detail.get("entities") or 0
            if family == "as_of_state" and versioned:
                reason = (
                    f"versioned relations exist ({', '.join(versioned[:3])}) but "
                    f"0 typed/bindable subjects: {typed}/{total} entities typed, "
                    "so no candidate resolves and none is skipped by the bank"
                )
            else:
                reason = (
                    f"bank recorded no skip, rejection or task for this family "
                    f"({typed}/{total} entities typed; structure below the "
                    "family's enumeration threshold)"
                )
            cells[family] = family_cell(0, infeasible_reason=reason)
    return cells


def _measure_bank(
    world: Any,
) -> dict[str, Any]:
    """Run the bank at export budget and at full enumeration budget."""
    from longworld.synthesis import world_task_bank as wtb

    bank = wtb.build_task_bank(world)  # default export budget
    full = wtb.build_task_bank(world, dict(LARGE_BUDGET))
    counts_full = full.counts()
    rejected_total = len(full.rejected)
    executed_total = sum(counts_full.values())
    candidates_in = executed_total + rejected_total
    # exact-program-identity dedup over the full enumerated set
    seen: set[str] = set()
    for task in full.tasks:
        seen.add(json.dumps(task.program, sort_keys=True))
    # text answerability: every fact-lineage proof item doc-anchored
    doc_ids = {doc.doc_id for doc in world.documents}
    answerable = 0
    for task in bank.tasks:
        fact_items = [item for item in task.proof if item.kind == "fact"]
        if fact_items and all(
            item.spans and all(span.doc_id in doc_ids for span in item.spans)
            for item in fact_items
        ):
            answerable += 1
    fold_total = len(bank.by_family("multi_hop"))
    fold_pass = sum(
        1 for task in bank.by_family("multi_hop") if task.metrics.get("non_foldable")
    )
    return {
        "counts": bank.counts(),
        "skipped": [
            {"family": skip.family, "reason": skip.reason} for skip in bank.skipped
        ],
        "candidates_in": candidates_in,
        "candidates_out": executed_total,
        "rejected": rejected_total,
        "rejected_capped": rejected_total >= wtb.REJECTED_LOG_CAP,
        "distinct_programs": len(seen),
        "tasks_exported": len(bank.tasks),
        "text_answerable": answerable,
        "fold_total": fold_total,
        "fold_pass": fold_pass,
    }


def _gather_demo() -> dict[str, Any]:
    from scripts.demo_p74_world import build_demo_world

    world = build_demo_world()
    detail = {
        "name": "demo-world",
        "kind": "demo",
        "topic": "synthetic observatory demo (scripts/demo_p74_world.py)",
        "pages": len(world.documents),
        "entities": len(world.entities),
        "typed_entities": _typed_entities(world),
        "facts": len(world.facts),
        "note": "synthetic semantic world; not a real source — carries no "
        "source-funnel mass, shown for the milestone-1 acceptance baseline",
    }
    detail.update(_measure_bank(world))
    detail["family_cells"] = _classify_family_cells(_rebuild_bank(world), world, detail)
    return detail


def _gather_wiki(manifest: dict[str, Any], snapshot_dir: Path) -> list[dict[str, Any]]:
    from longworld.synthesis import wiki_world_bridge as wb

    details = []
    for snap_info in manifest["snapshots"]:
        name = snap_info["name"]
        path = snapshot_dir / f"{name}.json"
        snapshot = json.loads(path.read_text())
        try:
            world = wb.snapshot_to_world(snapshot)
            world, typing_stats = wb.structurally_typed_world(world)
        except Exception as error:  # noqa: BLE001 - bridge failure is a report row
            details.append(
                {
                    "name": name,
                    "kind": "wiki",
                    "topic": snap_info["category"],
                    "pages": snap_info["pages"],
                    "entities": snap_info["entities"],
                    "typed_entities": 0,
                    "facts": snap_info["facts"],
                    "note": f"bridge failed: {type(error).__name__}: {error}",
                    "candidates_in": 0,
                    "candidates_out": 0,
                    "rejected": 0,
                    "distinct_programs": 0,
                    "tasks_exported": 0,
                    "text_answerable": 0,
                    "fold_total": 0,
                    "fold_pass": 0,
                    "family_cells": {
                        family: family_cell(
                            0,
                            infeasible_reason=(
                                f"source not bridged into a SemanticWorld: "
                                f"{type(error).__name__}: {error}"
                            ),
                        )
                        for family in CAPABILITY_FAMILIES
                    },
                }
            )
            continue
        bridging = getattr(world, "bridging", None) or {}
        detail = {
            "name": name,
            "kind": "wiki",
            "topic": snap_info["category"],
            "pages": snap_info["pages"],
            "entities": snap_info["entities"],
            "typed_entities": _typed_entities(world),
            "facts": len(world.facts),
            "note": (
                f"bridged: {bridging.get('facts_in', '?')} facts in / "
                f"{bridging.get('facts_out', '?')} out, timeline mode "
                f"'{bridging.get('timeline', '?')}'; structural typing "
                f"{typing_stats['families']} families"
            ),
        }
        detail.update(_measure_bank(world))
        detail["family_cells"] = _classify_family_cells(
            _rebuild_bank(world), world, detail
        )
        details.append(detail)
    return details


def _rebuild_bank(world: Any) -> Any:
    from longworld.synthesis import world_task_bank as wtb

    return wtb.build_task_bank(world)


def build_funnel_from_details(
    details: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """The §11 milestone-2 funnel from the gathered numbers."""
    wiki = [d for d in details if d["kind"] == "wiki"]
    demo = [d for d in details if d["kind"] == "demo"]
    bridged = [
        d for d in wiki if not str(d.get("note", "")).startswith("bridge failed")
    ]

    facts_in = int(manifest.get("totals", {}).get("facts", 0))
    facts_out = sum(d["facts"] for d in bridged)
    typed_total = sum(d["typed_entities"] for d in bridged)
    entities_total = sum(d["entities"] for d in bridged)

    cand_in = sum(d["candidates_in"] for d in details)
    cand_out = sum(d["candidates_out"] for d in details)
    executed = sum(d["candidates_out"] for d in details)
    distinct = sum(d["distinct_programs"] for d in details)
    exported = sum(d["tasks_exported"] for d in details)
    answerable = sum(d["text_answerable"] for d in details)
    fold_total = sum(d["fold_total"] for d in details)
    fold_pass = sum(d["fold_pass"] for d in details)
    capped_worlds = sum(bool(d.get("rejected_capped")) for d in details)

    stages = [
        funnel_stage(
            "source_availability",
            "topics",
            len(manifest.get("snapshots", [])),
            len(bridged),
            real_in=len(manifest.get("snapshots", [])),
            real_out=len(bridged),
            note=(
                "frozen wiki topics vs topics bridged into a SemanticWorld; "
                "milestone-2 scale (hundreds of topics) not yet attempted"
            ),
        ),
        funnel_stage(
            "fact_parse",
            "facts",
            facts_in,
            facts_out,
            real_in=facts_in,
            real_out=facts_out,
            note=(
                f"frozen facts surviving the bridge with verbatim supporting "
                f"spans; {typed_total}/{entities_total} entities have a "
                "structural role assigned after bridging, not a semantic type"
            ),
        ),
        funnel_stage(
            "program_instantiability",
            "candidate programs",
            cand_in,
            cand_out,
            real_in=sum(d["candidates_in"] for d in wiki),
            real_out=sum(d["candidates_out"] for d in wiki),
            note=(
                "enumerated candidates that execute non-degenerately; "
                f"{capped_worlds} world(s) may have rejection logs capped at 40, "
                "so the denominator is a lower bound and rate an upper bound; "
                "demo and real Wiki worlds are counted separately"
                if demo
                else (
                    "real Wiki worlds only; "
                    f"{capped_worlds} world(s) may have rejection logs capped at 40, "
                    "so the denominator is a lower bound and rate an upper bound"
                )
            ),
        ),
        funnel_stage(
            "semantic_dedup_retention",
            "programs",
            executed,
            distinct,
            real_in=sum(d["candidates_out"] for d in wiki),
            real_out=sum(d["distinct_programs"] for d in wiki),
            note=(
                "exact program-identity dedup over the enumerated set; "
                "near-duplicate / semantic dedup is not yet built (measured "
                "rate is an upper bound on retention)"
            ),
        ),
        funnel_stage(
            "text_answerability",
            "exported tasks",
            exported,
            answerable,
            real_in=sum(d["tasks_exported"] for d in wiki),
            real_out=sum(d["text_answerable"] for d in wiki),
            note=(
                "export-budget tasks whose every fact-lineage proof item "
                "carries doc-anchored spans; this checks span integrity, "
                "not relation entailment or final reader answerability"
            ),
        ),
        funnel_stage(
            "dependency_profile_pass",
            "multi_hop tasks",
            fold_total,
            fold_pass,
            real_in=sum(d["fold_total"] for d in wiki),
            real_out=sum(d["fold_pass"] for d in wiki),
            note=(
                "multi_hop tasks passing the dependency_ops non-foldability "
                "gate (required at execution) with >= 4 consumed facts"
            ),
        ),
        not_measured_stage(
            "witness_row_distinction",
            "witness rows",
            "P73 witness/audit artifacts cover the P73 shared-world arms, a "
            "different population; no witness has run over P74 tasks yet",
        ),
    ]
    return build_funnel(stages)


def _find_length_report(explicit: Path | None) -> tuple[dict[str, Any], str]:
    """Load the T5 report: explicit path, data dir, job tmp, or rebuild."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    else:
        candidates.extend(
            sorted(
                (REPO / "data" / "capability_records").glob("p74_length_report*.json")
            )
        )
        job_dir = os.environ.get("CLAUDE_JOB_DIR")
        if job_dir:
            candidates.append(Path(job_dir) / "tmp" / "p74_length_report_default.json")
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text()), str(path)
    # deterministic rebuild through the T5 script's own builder
    from scripts.p74_length_report import DEFAULT_WIKI_DIR, build_report

    report = build_report(
        sorted(
            p for p in DEFAULT_WIKI_DIR.glob("*.json") if "freeze-log" not in p.name
        ),
        include_demo=True,
        include_calibration=True,
        bands=[8192, 32768, 49152, 65536, 98304, 131072, 262144],
    )
    return report, "rebuilt via scripts/p74_length_report.build_report"


def _default_cache() -> Path:
    job_dir = os.environ.get("CLAUDE_JOB_DIR")
    if job_dir:
        return Path(job_dir) / "tmp" / "p74_support_matrix_worlds.json"
    return REPO / "tmp" / "p74_support_matrix_worlds.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--length-report", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--no-demo", action="store_true")
    parser.add_argument("--reuse-cache", action="store_true")
    args = parser.parse_args(argv)

    cache_path = args.cache or _default_cache()
    manifest = json.loads(args.manifest.read_text())
    snapshot_dir = Path(args.manifest).parent

    if args.reuse_cache and cache_path.is_file():
        world_details = json.loads(cache_path.read_text())["worlds"]
        source = f"cache {cache_path}"
    else:
        world_details = []
        if not args.no_demo:
            world_details.append(_gather_demo())
        world_details.extend(_gather_wiki(manifest, snapshot_dir))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"manifest": str(args.manifest), "worlds": world_details},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        source = "fresh run (bridge + task bank, read-only)"

    length_report, length_source = _find_length_report(args.length_report)
    matrix = build_matrix(world_details, length_report, include_demo=not args.no_demo)
    funnel = build_funnel_from_details(world_details, manifest)

    report = {
        "schema_version": SCHEMA,
        "script": "scripts/p74_support_matrix.py",
        "generated_by": "T7 support matrix + funnel report",
        "inputs": {
            "manifest": str(args.manifest),
            "manifest_totals": manifest.get("totals"),
            "length_report": length_source,
            "world_details": source,
        },
        "matrix": matrix,
        "funnel": funnel,
        "notes": [
            "infeasible and empty cells never disappear (charter §16 T7)",
            "Wiki as_of_state bank counts are diagnostic: snapshots have no revision or revocation semantics, so these are not qualified historical-state tasks",
            "real Wiki length bands use raw document text-concat; demo uses audit render; neither is final reader input length",
            (
                "real_topic_* funnel columns count only bridged wiki topics; the "
                "demo world is synthetic and excluded from them"
            ),
            (
                "witness/audit numbers for P74 tasks are not yet measured (P73 "
                "witness artifacts cover a different population)"
            ),
        ],
    }

    text = render_report(matrix, funnel)
    print(text)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"\nJSON report written to {args.json}")
    print(f"world details cache: {cache_path} ({source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
