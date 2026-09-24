"""Export frozen Wiki worlds as local P75 reader research candidates.

The files named train/eval are split candidates, not approved training data.
The manifest sets train_ready=false until relation-level evidence and an
independent reader check exist. No network or GPU work is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import length_controller as lc
from longworld.synthesis import (
    reader_view,
    wiki_evidence,
    wiki_source_compare,
    wiki_world_bridge,
    world_task_bank,
)

DEFAULT_SNAPSHOTS = ROOT / "data/capability_records/p74_wiki_snapshot_v1"
DEFAULT_OUTPUT = ROOT / "data/p75_real_reader_candidates_v1"
SCHEMA = "longworld.p75-real-reader-export.v1"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_path(path: Path) -> str:
    lexical = path.absolute()
    try:
        return str(lexical.relative_to(ROOT.absolute()))
    except ValueError:
        return str(lexical)


def _admit_task(world: Any, task: Any) -> str | None:
    """Admission seam for later relation-level evidence validation."""
    if task.family == "source_compare":
        try:
            wiki_source_compare.validate_source_compare_task(world, task)
        except ValueError as error:
            return f"source_compare_invalid:{error}"
        return None
    if (
        task.family == "as_of_state"
        and getattr(world, "bridging", {}).get("timeline") == "unversioned"
    ):
        return "unversioned_source_as_of"
    if task.family != "locate":
        return "explicit_program_question:unsupported_task_family"
    relation = task.program["steps"][-1].get("relation")
    if (
        task.template != "locate_attribute"
        or relation not in reader_view.NATURAL_RELATION_QUESTIONS
    ):
        return "unreviewed_relation_question"
    if relation == "established in" and not (
        isinstance(task.answer_rendered, int) and 1000 <= task.answer_rendered <= 9999
    ):
        return "establishment_not_single_year"
    try:
        reader_view.natural_locate_question(task)
    except ValueError as error:
        return str(error)
    if not task.consumed_fact_ids:
        return "relation_evidence_failed:no consumed facts"
    for fact_id in task.consumed_fact_ids:
        fact = world.facts_by_id[fact_id]
        check = wiki_evidence.check_locate_fact(world, fact)
        if not check.supported:
            return f"relation_evidence_failed:{check.reason}"
    return None


def _split_groups(
    groups: list[str], eval_fraction: float, weights: dict[str, int]
) -> dict[str, str]:
    if not 0 < eval_fraction < 1:
        raise ValueError("eval_fraction must be between 0 and 1")
    eval_count = max(1, round(len(groups) * eval_fraction)) if len(groups) > 1 else 0
    target_per_group = (
        sum(weights[group] for group in groups) * eval_fraction / eval_count
        if eval_count
        else 0
    )
    ordered = sorted(
        groups,
        key=lambda value: (
            abs(weights[value] - target_per_group),
            _sha(value.encode("utf-8")),
            value,
        ),
    )
    eval_groups = set(ordered[:eval_count])
    return {group: "eval" if group in eval_groups else "train" for group in groups}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    data = "".join(_dump(row) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


def export(
    snapshot_paths: list[Path],
    output_dir: Path,
    *,
    per_family: int = 24,
    compare_cap: int = 4,
    max_full_tokens: int = 262144,
    eval_fraction: float = 0.2,
    tokenizer: Any | None = None,
) -> dict[str, Any]:
    if per_family < 0 or compare_cap < 0 or max_full_tokens <= 0:
        raise ValueError("task caps and max_full_tokens must be nonnegative/positive")
    if tokenizer is None:
        tokenizer = lc.get_tokenizer()
    source_rows: list[dict[str, Any]] = []
    candidates: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    rejects: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    for path in sorted(snapshot_paths):
        raw = path.read_bytes()
        snapshot = json.loads(raw)
        group = snapshot.get("snapshot_id")
        if not isinstance(group, str) or not group or group in seen_groups:
            raise ValueError(f"missing or repeated source group: {path}")
        seen_groups.add(group)
        source = snapshot.get("source", {})
        source_rows.append(
            {
                "source_group": group,
                "snapshot_file": _source_path(path),
                "snapshot_sha256": _sha(raw),
                "source_category": source.get("category"),
                "license": source.get("license"),
                "frozen_at": snapshot.get("frozen_at"),
                "revisions": source.get("revisions", {}),
                "revision_count": len(source.get("revisions", {})),
            }
        )
        try:
            world = wiki_world_bridge.snapshot_to_world(snapshot)
            typed, _typing = wiki_world_bridge.structurally_typed_world(world)
            bank = world_task_bank.build_task_bank(
                typed,
                {family: per_family for family in world_task_bank.CAPABILITY_FAMILIES},
            )
        except (ValueError, KeyError, TypeError) as error:
            rejects.append(
                {"source_group": group, "reason": f"source_build_failed:{error}"}
            )
            continue
        for skip in bank.skipped:
            rejects.append(
                {
                    "source_group": group,
                    "family": skip.family,
                    "reason": f"family_skipped:{skip.reason}",
                }
            )
        comparison_tasks = wiki_source_compare.build_source_compare_tasks(
            typed, max_tasks=compare_cap
        )
        for task in (*bank.tasks, *comparison_tasks):
            reason = _admit_task(typed, task)
            if reason is not None:
                rejects.append(
                    {
                        "source_group": group,
                        "task_id": task.task_id,
                        "family": task.family,
                        "reason": reason,
                    }
                )
                continue
            try:
                row, index, audit = reader_view.compile_task(
                    typed,
                    task,
                    source_group=group,
                    tokenizer=tokenizer,
                    max_full_tokens=max_full_tokens,
                )
            except ValueError as error:
                rejects.append(
                    {
                        "source_group": group,
                        "task_id": task.task_id,
                        "family": task.family,
                        "reason": str(error),
                    }
                )
                continue
            checks = [
                wiki_evidence.check_locate_fact(typed, typed.facts_by_id[fact_id])
                for fact_id in task.consumed_fact_ids
            ]
            index["evidence_status"] = (
                "subject_relation_value_structural_check; independent_reader_unchecked"
            )
            audit["relation_evidence"] = [
                {
                    "fact_id": fact_id,
                    "reason": check.reason,
                    "span": check.span.to_dict() if check.span else None,
                    "row": check.row,
                    "header": check.header,
                }
                for fact_id, check in zip(task.consumed_fact_ids, checks)
            ]
            candidates.append((group, row, index, audit))

    eligible_groups = sorted({group for group, _row, _index, _audit in candidates})
    group_weights = Counter(group for group, _row, _index, _audit in candidates)
    splits = _split_groups(eligible_groups, eval_fraction, group_weights)
    titles_by_split: dict[str, set[str]] = {"train": set(), "eval": set()}
    for source_row in source_rows:
        split = splits.get(source_row["source_group"])
        if split is not None:
            titles_by_split[split].update(source_row["revisions"])
    overlap = titles_by_split["train"] & titles_by_split["eval"]
    if overlap:
        raise ValueError(f"train/eval source title overlap: {sorted(overlap)[:5]}")
    output_dir.mkdir(parents=True, exist_ok=True)
    train_rows: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for group, row, index, audit in sorted(
        candidates, key=lambda item: item[1]["example_id"]
    ):
        split = splits[group]
        (train_rows if split == "train" else eval_rows).append(row)
        index_rows.append({**index, "split": split})
        audit_rows.append(audit)
    source_rows = [
        {**row, "split": splits.get(row["source_group"], "excluded")}
        for row in sorted(source_rows, key=lambda item: item["source_group"])
    ]
    rejects.sort(
        key=lambda row: (
            row.get("source_group", ""),
            row.get("task_id", ""),
            row.get("family", ""),
            row["reason"],
        )
    )
    digests = {
        "train.jsonl": _write_jsonl(output_dir / "train.jsonl", train_rows),
        "eval.jsonl": _write_jsonl(output_dir / "eval.jsonl", eval_rows),
        "sample_index.jsonl": _write_jsonl(
            output_dir / "sample_index.jsonl", index_rows
        ),
        "audit.jsonl": _write_jsonl(output_dir / "audit.jsonl", audit_rows),
        "rejects.jsonl": _write_jsonl(output_dir / "rejects.jsonl", rejects),
        "source_manifest.jsonl": _write_jsonl(
            output_dir / "source_manifest.jsonl", source_rows
        ),
    }
    manifest = {
        "schema": SCHEMA,
        "profile": reader_view.PROFILE,
        "quality_status": "research_candidate",
        "evidence_status": (
            "subject_relation_value_structural_check; independent_reader_unchecked"
        ),
        "train_ready": False,
        "train_ready_reason": (
            "structural row/sentence checks are conservative but incomplete; "
            "independent reader and train/eval source-overlap checks are pending"
        ),
        "source_world_atomic_split": True,
        "split_method": "source_group_row_weight_closest_to_target_v1",
        "train_eval_source_title_overlap": 0,
        "source_groups": len(seen_groups),
        "eligible_source_groups": len(eligible_groups),
        "rows": {"train": len(train_rows), "eval": len(eval_rows)},
        "reject_reasons": dict(
            sorted(Counter(row["reason"] for row in rejects).items())
        ),
        "config": {
            "per_family": per_family,
            "compare_cap": compare_cap,
            "max_full_tokens": max_full_tokens,
            "eval_fraction": eval_fraction,
            "tokenizer_model": lc.TOKENIZER_MODEL,
            "tokenizer_revision": lc.TOKENIZER_REVISION,
        },
        "files_sha256": digests,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots-dir", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument(
        "--snapshots", help="comma-separated snapshot stems without _snapshot"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--per-family", type=int, default=24)
    parser.add_argument("--compare-cap", type=int, default=4)
    parser.add_argument("--max-full-tokens", type=int, default=262144)
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    args = parser.parse_args(argv)
    names = args.snapshots.split(",") if args.snapshots else None
    paths = (
        [args.snapshots_dir / f"{name}_snapshot.json" for name in names]
        if names
        else sorted(args.snapshots_dir.glob("*_snapshot.json"))
    )
    if not paths or any(not path.is_file() for path in paths):
        parser.error("no matching frozen snapshot files")
    manifest = export(
        paths,
        args.output_dir,
        per_family=args.per_family,
        compare_cap=args.compare_cap,
        max_full_tokens=args.max_full_tokens,
        eval_fraction=args.eval_fraction,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "rows": manifest["rows"],
                "train_ready": manifest["train_ready"],
                "reject_reasons": manifest["reject_reasons"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
