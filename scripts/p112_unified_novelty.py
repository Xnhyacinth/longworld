"""Drop exact prior sample repeats before extending an immutable candidate index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from longworld.synthesis.sharded_candidate_bank import _shard_path, verify_index
from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.p112-unified-novelty.v1"
IDENTITY = (
    "source_kind",
    "source_group",
    "semantic_task_id",
    "split",
    "operation",
    "context_sha256",
    "answer_sha256",
    "full_chat_tokens",
    "input_tokens",
    "supervised_tokens",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prior_reader(entry: dict, shard_paths: dict[str, Path]) -> dict:
    row = entry["candidate"]
    path = shard_paths[entry["shard"]] / row["output_file"]
    with path.open() as stream:
        for position, line in enumerate(stream):
            if position == row["row_index"]:
                return json.loads(line)
    raise ValueError("prior candidate reader pointer is missing")


def filter_novel(
    base_index: Path, source_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    base = verify_index(base_index, full_readers=True)
    source = verify_merge(source_dir)
    base_manifest = json.loads((base_index / "manifest.json").read_text())
    shard_paths = {
        item["name"]: _shard_path(base_index, item) for item in base_manifest["shards"]
    }
    prior = {}
    for entry in _rows(base_index / "candidate_refs.jsonl"):
        row = entry["candidate"]
        sample_id = row["sample_id"]
        if sample_id in prior and any(
            prior[sample_id]["candidate"][key] != row[key] for key in IDENTITY
        ):
            raise ValueError("base index repeats a sample with conflicting identity")
        prior[sample_id] = entry
    indices = _rows(source_dir / "sample_index.jsonl")
    readers = {
        split: _rows(source_dir / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    if len(indices) != source["candidate_views"]:
        raise ValueError("source candidate index count differs")
    seen = set()
    seen_positions = set()
    kept = {"train": [], "eval": []}
    kept_index = []
    rejected = []
    lengths = Counter()
    for row in indices:
        sample_id = row["sample_id"]
        split = row["split"]
        position = row["row_index"]
        if (
            sample_id in seen
            or (split, position) in seen_positions
            or split not in readers
            or type(position) is not int
            or not 0 <= position < len(readers[split])
        ):
            raise ValueError("source candidate pointer or sample repeats")
        seen.add(sample_id)
        seen_positions.add((split, position))
        reader = readers[split][position]
        if reader["sample_id"] != sample_id:
            raise ValueError("source candidate reader identity differs")
        old_entry = prior.get(sample_id)
        if old_entry is not None:
            old = old_entry["candidate"]
            if any(old[key] != row[key] for key in IDENTITY):
                raise ValueError("sample ID collides with a different prior reader")
            if _prior_reader(old_entry, shard_paths) != reader:
                raise ValueError("sample ID collides with a different final reader")
            rejected.append(
                {"sample_id": sample_id, "reason": "exact_prior_sample_identity"}
            )
            continue
        item = dict(row)
        item["row_index"] = len(kept[split])
        item["output_file"] = f"candidate_{split}.jsonl"
        kept[split].append(reader)
        kept_index.append(item)
        lengths[item["length_bin"]] += 1
    if len(seen_positions) != sum(len(rows) for rows in readers.values()):
        raise ValueError("source candidate has unindexed reader rows")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="unified_novelty_", dir=output.parent
    ) as tmp:
        stage = Path(tmp)
        payloads = {
            "candidate_train.jsonl": kept["train"],
            "candidate_eval.jsonl": kept["eval"],
            "sample_index.jsonl": kept_index,
            "rejected.jsonl": rejected,
        }
        for name, rows in payloads.items():
            (stage / name).write_text("".join(_dump(item) + "\n" for item in rows))
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "novelty_schema": SCHEMA,
            "candidate_views": len(kept_index),
            "independent_semantic_tasks": len(
                {(row["source_kind"], row["semantic_task_id"]) for row in kept_index}
            ),
            "source_scoped_semantic_tasks": len(
                {
                    (row["source_kind"], row["source_group"], row["semantic_task_id"])
                    for row in kept_index
                }
            ),
            "views_by_lane": {"p112_novel_source_candidates": len(kept_index)},
            "splits": {split: len(rows) for split, rows in kept.items() if rows},
            "length_bins": dict(sorted(lengths.items())),
            "base_refs_sha256": base["refs_sha256"],
            "source_manifest_sha256": _sha(source_dir / "manifest.json"),
            "dropped_exact_prior_samples": len(rejected),
            "files_sha256": {name: _sha(stage / name) for name in payloads},
            "train_ready": False,
        }
        (stage / "manifest.json").write_text(_dump(manifest) + "\n")
        if verify_only:
            verify_merge(output)
            if any(
                not (output / path.name).is_file()
                or (output / path.name).read_bytes() != path.read_bytes()
                for path in stage.iterdir()
            ):
                raise ValueError("unified novelty byte replay differs")
        else:
            if output.exists():
                raise ValueError("unified novelty output already exists")
            os.rename(stage, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-index", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            filter_novel(
                args.base_index,
                args.source_dir,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
