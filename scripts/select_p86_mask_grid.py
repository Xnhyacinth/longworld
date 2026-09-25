"""Select one final reader per source/operation/actual-length cell for mask audit.

This is an audit sample, not a training selection. It prefers train rows in
each occupied cell and writes only pointers to the existing candidate bank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.unified-candidate-selection.v1"
INDEX_FIELDS = (
    "sample_id",
    "task_key",
    "semantic_task_id",
    "source_kind",
    "source_group",
    "domain",
    "topic",
    "operation",
    "split",
    "length_bin",
    "full_chat_tokens",
    "answer_sha256",
    "source_name",
    "output_file",
    "row_index",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _priority(row: dict[str, Any], seed: int) -> tuple[int, str, str]:
    return (
        row["split"] != "train",
        hashlib.sha256(f"{seed}|{row['sample_id']}".encode()).hexdigest(),
        row["sample_id"],
    )


def select(
    merged_dir: Path,
    output_dir: Path,
    *,
    seed: int = 86,
    split_filter: str | None = None,
) -> dict[str, Any]:
    if (
        output_dir.exists()
        or type(seed) is not int
        or seed < 0
        or split_filter not in (None, "train", "eval")
    ):
        raise ValueError("selection output must be new and seed nonnegative")
    source = verify_merge(merged_dir)
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    positions = Counter()
    seen_samples: set[str] = set()
    with (merged_dir / "sample_index.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            split = row["split"]
            if (
                split not in {"train", "eval"}
                or row["output_file"] != f"candidate_{split}.jsonl"
                or row["row_index"] != positions[split]
                or row["sample_id"] in seen_samples
            ):
                raise ValueError("candidate index identity or position changed")
            positions[split] += 1
            seen_samples.add(row["sample_id"])
            if split_filter is not None and split != split_filter:
                continue
            cell = (row["source_kind"], row["operation"], row["length_bin"])
            current = best.get(cell)
            if current is None or _priority(row, seed) < _priority(current, seed):
                best[cell] = row
    if (
        len(seen_samples) != source["candidate_views"]
        or dict(positions) != source["splits"]
    ):
        raise ValueError("candidate index count differs from manifest")
    selected = [best[cell] for cell in sorted(best)]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p86-mask-grid-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        index_path = temp / "selection_index.jsonl"
        with index_path.open("x", encoding="utf-8") as stream:
            for rank, row in enumerate(selected):
                stream.write(
                    json.dumps(
                        {
                            **{field: row[field] for field in INDEX_FIELDS},
                            "selection_rank": rank,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
        manifest = {
            "schema_version": SCHEMA,
            "input_manifest_sha256": _sha(merged_dir / "manifest.json"),
            "source_views": source["candidate_views"],
            "selection_seed": seed,
            "selected_views": len(selected),
            "selected_independent_tasks": len(
                {(row["source_kind"], row["semantic_task_id"]) for row in selected}
            ),
            "selected_source_groups": len(
                {(row["source_kind"], row["source_group"]) for row in selected}
            ),
            "selected_by_source_kind": dict(
                sorted(Counter(row["source_kind"] for row in selected).items())
            ),
            "selected_by_length": dict(
                sorted(Counter(row["length_bin"] for row in selected).items())
            ),
            "selected_by_split": dict(
                sorted(Counter(row["split"] for row in selected).items())
            ),
            "selection_index_sha256": _sha(index_path),
            "selection_scope": "mask_audit_grid_only",
            "train_ready": False,
        }
        if split_filter is not None:
            manifest["selection_split"] = split_filter
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        os.rename(temp, output_dir)
    return manifest


def verify_selection(
    merged_dir: Path,
    output_dir: Path,
    *,
    seed: int = 86,
    split_filter: str | None = None,
) -> dict[str, Any]:
    """Recompute the grid and its pointers from frozen candidate bytes."""
    stored = json.loads((output_dir / "manifest.json").read_text())
    if stored.get("selection_index_sha256") != _sha(
        output_dir / "selection_index.jsonl"
    ):
        raise ValueError("stored selection index changed")
    with tempfile.TemporaryDirectory(
        prefix="p86-mask-grid-verify-", dir=output_dir.parent
    ) as raw:
        rebuilt_dir = Path(raw) / "selection"
        rebuilt = select(merged_dir, rebuilt_dir, seed=seed, split_filter=split_filter)
        if (
            rebuilt != stored
            or _sha(rebuilt_dir / "selection_index.jsonl")
            != stored["selection_index_sha256"]
        ):
            raise ValueError("mask grid differs from frozen source")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("merged_dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=86)
    parser.add_argument("--split", choices=("all", "train", "eval"), default="all")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    split_filter = None if args.split == "all" else args.split
    result = (
        verify_selection(
            args.merged_dir, args.output, seed=args.seed, split_filter=split_filter
        )
        if args.verify_only
        else select(
            args.merged_dir, args.output, seed=args.seed, split_filter=split_filter
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
