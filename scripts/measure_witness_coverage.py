#!/usr/bin/env python3
"""Witness coverage audit: which mutants each bank row's world separates.

The P72 diagnosis showed the trained arms fail by approximate programs (wrong
depth, non-empty answers on empty-gold scopes, partial-condition matches). This
audit makes that measurable per row: for every row, run the P73 semantic
mutants on the row's world and record whether each mutant's answer differs
from the correct one. Rows whose worlds separate at least one mutant are
"witness-rich"; a bank whose rows are uniformly witness-poor trains the same
surface as one whose rows only guarantee an executable gold.

Two shard layouts are read:
- shared worlds (p73_shared_v1): shards/<world>/world.json is one JSON object
  whose `tasks` carry a family and a stored `solve_context` — the exact
  per-family view (family header + that family's rows) the answer was solved
  with, because family solvers validate the header strictly and the merged
  multi-family header would not pass.
- legacy worlds (p71/p72 banks): world.json is likewise one JSON object, but
  single-family: every task solves over `context` as rendered.

Per-row results are keyed by example_id ("world_id:task_id") under
`example_details`, so the C/D arm split can consume per-row
distinguished_fraction. Writes <bank>/verification.witness.json.
Deterministic: sorted iteration everywhere, no RNG.

Usage:
  python scripts/measure_witness_coverage.py --bank data/capability_records/p73_shared_v1 [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis.capability_mutations import (  # noqa: E402
    witness_report,
)

# Families whose solvers the audit can validate against a family-specific
# header (the shared world's header family is "shared:...", so every task is
# solved under its own header rebuilt from the shared header's fields).
_SUPPORTED_FAMILIES = (
    "filter_aggregate",
    "group_compare",
    "join_lookup",
    "alias_locate",
    "asof_state",
    "rule_holdout",
    "set_complete",
)


def _audit_row(
    fam: str, ctx: str, question: dict, stats: dict, world_id: str, task_id: str
) -> dict | None:
    """One task's witness computation; returns the per-row detail or None."""
    if fam not in _SUPPORTED_FAMILIES:
        stats["unsupported"] += 1
        return None
    if not ctx:
        return None
    report = witness_report(fam, ctx, question)
    if report.get("unsupported"):
        stats["unsupported"] += 1
        if "error" in report:
            stats["errors"] += 1
            stats["error_samples"].append((world_id, task_id, report["error"]))
        return None
    stats["rows"] += 1
    frac = report["distinguished_fraction"]
    stats["distinguished_fraction_sum"] += frac
    if report["distinguished_count"] > 0:
        stats["witness_rich"] += 1
    answer = report["answer"]
    if (
        isinstance(answer, dict)
        and (answer.get("empty") is True or answer.get("ids") == [])
        or answer == []
    ):
        stats["empty_answer"] += 1
    for name, result in report["mutants"].items():
        if result.get("distinguished"):
            stats["per_mutant_distinguished"][name] += 1
        elif "error" in result:
            stats["per_mutant_error"][name] += 1
    return {
        "family": fam,
        "distinguished_fraction": frac,
        "distinguished_count": report["distinguished_count"],
        "answer": answer,
        "mutants": report["mutants"],
    }


def _iter_shared_tasks(world: dict):
    """(world_id, task_id, family, solve_context, question) for shared tasks."""
    world_id = world["world_id"]
    for task in world["tasks"]:
        yield (
            world_id,
            task.get("task_id"),
            task["family"],
            task.get("solve_context", ""),
            task["question"],
        )


def _iter_legacy_tasks(world: dict):
    """Legacy single-family worlds solve every task over the world context."""
    world_id = world["world_id"]
    fam = world["family"]
    for task in world["tasks"]:
        yield world_id, task.get("task_id"), fam, world["context"], task["question"]


def audit_bank(bank: Path, limit: int | None) -> dict:
    shard_root = bank / "shards"
    if not shard_root.exists():
        raise SystemExit(f"no shards under {shard_root}")
    shard_files = sorted(shard_root.glob("*/world.json"))
    per_family: dict[str, dict] = defaultdict(
        lambda: {
            "rows": 0,
            "witness_rich": 0,
            "distinguished_fraction_sum": 0.0,
            "per_mutant_distinguished": defaultdict(int),
            "per_mutant_error": defaultdict(int),
            "unsupported": 0,
            "errors": 0,
            "error_samples": [],
            "empty_answer": 0,
        }
    )
    example_details: dict[str, dict] = {}
    seen = 0
    for shard_path in shard_files:
        world = json.loads(shard_path.read_text())
        is_shared = world.get("schema_version") == "capability-shared-world-v1"
        task_iter = (
            _iter_shared_tasks(world) if is_shared else _iter_legacy_tasks(world)
        )
        for world_id, task_id, fam, ctx, question in task_iter:
            if fam not in per_family:
                # A family outside the known set still gets an explicit bucket
                # so the report shows it instead of dropping it silently.
                per_family[fam]
            stats = per_family[fam]
            # rows_audited counts every row encountered, unsupported families
            # included, matching the pre-refactor legacy report's headline.
            seen += 1
            detail = _audit_row(fam, ctx, question, stats, world_id, task_id)
            if detail is None:
                continue
            example_details[f"{world_id}:{task_id}"] = detail
            if limit is not None and seen >= limit:
                break
        if limit is not None and seen >= limit:
            break
    out = {"rows_audited": seen, "families": {}, "example_details": example_details}
    for family in sorted(per_family):
        stats = per_family[family]
        rows = stats["rows"]
        if rows == 0:
            out["families"][family] = {
                "unsupported_rows": stats["unsupported"],
                "error_rows": stats["errors"],
            }
            continue
        family_out = {
            "rows": rows,
            "unsupported_rows": stats["unsupported"],
            "error_rows": stats["errors"],
            "witness_rich": stats["witness_rich"],
            "witness_rich_rate": round(stats["witness_rich"] / rows, 3),
            "mean_distinguished_fraction": round(
                stats["distinguished_fraction_sum"] / rows, 3
            ),
            "empty_answer_rows": stats["empty_answer"],
            "per_mutant_distinguished": dict(
                sorted(stats["per_mutant_distinguished"].items())
            ),
            "per_mutant_error": dict(sorted(stats["per_mutant_error"].items())),
        }
        if stats["error_samples"]:
            # capped so the report stays readable; the full error text is in
            # the affected rows' example_details entries
            family_out["error_samples"] = stats["error_samples"][:5]
        out["families"][family] = family_out
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--details",
        action="store_true",
        help="print per-row example_id and distinguished_fraction",
    )
    args = parser.parse_args()
    report = audit_bank(args.bank, args.limit)
    out_path = args.bank / "verification.witness.json"
    out_path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(f"rows audited: {report['rows_audited']}")
    for family, stats in report["families"].items():
        if "rows" not in stats:
            print(
                f"{family:20s} unsupported rows={stats.get('unsupported_rows', 0)} "
                f"errors={stats.get('error_rows', 0)}"
            )
            continue
        mutants = ", ".join(
            f"{k}={v}/{stats['rows']}"
            for k, v in stats["per_mutant_distinguished"].items()
        )
        rich = "{:.1%}".format(stats["witness_rich_rate"])
        print(
            f"{family:20s} rows={stats['rows']:5d} rich={rich:>5s} "
            f"mean-frac={stats['mean_distinguished_fraction']:.3f} "
            f"empty={stats['empty_answer_rows']} | {mutants}"
        )
    if args.details:
        for example_id, detail in report["example_details"].items():
            print(f"{example_id}: {detail['distinguished_fraction']}")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
