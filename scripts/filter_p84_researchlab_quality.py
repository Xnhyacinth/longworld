"""Separate P84 raw revision candidates from useful prose-change supervision.

The native source/compiler contract is preserved. This conservative filter
requires every requested path to have distinct, substantial prose excerpts;
LaTeX commands, removals, copied file paths, and duplicate answer evidence
stay visible in a per-task rejection ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = "longworld.p84-researchlab-content-filter.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def classify(task: dict) -> tuple[str, int]:
    """Return a conservative task-level reason and number of prose replacements."""
    answers = task["answer"]
    prose = sum(
        answer["status"] == "replaced"
        and not answer["old_excerpt"].lstrip().startswith("\\")
        and not answer["new_excerpt"].lstrip().startswith("\\")
        and len(answer["old_excerpt"].strip()) >= 80
        and len(answer["new_excerpt"].strip()) >= 80
        for answer in answers
    )
    if any(answer["status"] != "replaced" for answer in answers):
        return "removed_or_added_file", prose
    if prose != len(answers):
        return "latex_command_heading_or_short_excerpt", prose
    pairs = [(answer["old_excerpt"], answer["new_excerpt"]) for answer in answers]
    if len(set(pairs)) != len(pairs):
        return "duplicate_answer_evidence", prose
    if any(
        "copy" in part.casefold()
        for answer in answers
        for part in Path(answer["path"]).parts
    ):
        return "copied_source_path", prose
    return "prose_revision_review_candidate", prose


def run(native_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise ValueError("quality output directory must be new")
    receipt = json.loads((native_dir / "BUILD_RECEIPT.json").read_text())
    if receipt.get("schema_version") != "longworld.p66-researchlab-taskbank-receipt.v1":
        raise ValueError("wrong native ResearchLab receipt")
    for relative, digest in receipt["files"].items():
        path = Path(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or _sha(native_dir / path) != digest
        ):
            raise ValueError("native source or reader hash changed")
    candidates = _rows(native_dir / "candidates.jsonl")
    train = _rows(native_dir / "train.jsonl")
    if (
        len(candidates) != receipt["semantic_tasks"]
        or len(train) != receipt["train_rows"]
    ):
        raise ValueError("native candidate count drift")
    by_id = {row["sample_id"]: row for row in train}
    if len(by_id) != len(train):
        raise ValueError("duplicate train reader ID")
    selected = []
    rejected = []
    reasons = Counter()
    prose_rows = 0
    for task in candidates:
        task_id = task["semantic_task_id"]
        if task["split"] != "train" or task_id not in by_id:
            raise ValueError("candidate is missing a train reader")
        reason, prose_entries = classify(task)
        prose_rows += prose_entries > 0
        item = {
            "semantic_task_id": task_id,
            "family_id": task["family_id"],
            "old_version": task["old_version"],
            "new_version": task["new_version"],
            "paths": task["paths"],
            "context_sha256": task["context_sha256"],
            "prose_replacement_entries": prose_entries,
            "decision": reason,
        }
        (selected if reason == "prose_revision_review_candidate" else rejected).append(
            item
        )
        reasons[reason] += 1
    output_dir.mkdir(parents=True)
    for name, rows in (
        ("selected_index.jsonl", selected),
        ("rejected.jsonl", rejected),
    ):
        (output_dir / name).write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            )
        )
    result = {
        "schema": SCHEMA,
        "native_receipt_sha256": _sha(native_dir / "BUILD_RECEIPT.json"),
        "raw_native_tasks": len(candidates),
        "rows_with_any_prose_replacement": prose_rows,
        "quality_review_candidates": len(selected),
        "rejected_tasks": len(rejected),
        "reasons": dict(sorted(reasons.items())),
        "selected_index_sha256": _sha(output_dir / "selected_index.jsonl"),
        "rejected_sha256": _sha(output_dir / "rejected.jsonl"),
        "train_ready": False,
        "claim_limit": "surface-level prose and duplicate-evidence filter, not semantic novelty or model dependency proof",
    }
    (output_dir / "quality_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.native_dir, args.output_dir), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
