"""Materialize a source-bound candidate subset and replay every final mask."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from longworld.synthesis.sharded_candidate_bank import _shard_path, verify_index
from scripts.audit_unified_reader_mask import audit_reader

SCHEMA = "longworld.sharded-candidate-subset.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError(f"blank JSONL row: {path}")
            yield json.loads(line)


def _pin(pin: dict[str, str]) -> Path:
    path = Path(pin["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("subset source path escapes workspace")
    resolved = ROOT / path
    if _sha(resolved) != pin["sha256"]:
        raise ValueError(f"subset source pin changed: {path}")
    return resolved


def _write_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _source_worlds(
    pool_paths: list[Path], groups: dict[str, str]
) -> list[dict[str, Any]]:
    worlds = {}
    for pool_path in pool_paths:
        pool = json.loads(pool_path.read_text())
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError("subset source pool schema changed")
        for entry in pool["sources"]:
            pin = entry["snapshot"]
            snapshot = json.loads(_pin(pin).read_text())
            group = snapshot["snapshot_id"]
            if group not in groups:
                continue
            if group in worlds or entry["split"] != groups[group]:
                raise ValueError("subset world repeated or split changed")
            worlds[group] = {
                "source_group": group,
                "split": entry["split"],
                "domain": entry["domain"],
                "topic": entry["topic"],
                "snapshot": pin,
                "license": snapshot["source"]["license"],
                "documents": [
                    {
                        "title": doc["title"],
                        "page_url": doc["page_url"],
                        "revision_url": doc["revision_url"],
                        "revision": snapshot["source"]["revisions"][doc["title"]],
                    }
                    for doc in snapshot["documents"]
                ],
            }
    if set(worlds) != set(groups):
        raise ValueError("subset task world absent from pinned source pool")
    titles: dict[str, str] = {}
    urls: dict[str, str] = {}
    for world in worlds.values():
        split = world["split"]
        for doc in world["documents"]:
            for key, seen in (
                (doc["title"].casefold(), titles),
                (doc["page_url"], urls),
            ):
                if not key:
                    continue
                previous = seen.setdefault(key, split)
                if previous != split:
                    raise ValueError("subset page crosses train/eval source pools")
    return [worlds[key] for key in sorted(worlds)]


def export(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("subset output must be new")
    config = json.loads(config_path.read_text())
    common = {"schema", "index", "lane_operations", "max_full_chat_tokens"}
    if (
        set(config) not in (common | {"source_pool"}, common | {"source_pools"})
        or config["schema"] != SCHEMA
        or not isinstance(config["lane_operations"], dict)
        or not config["lane_operations"]
        or type(config["max_full_chat_tokens"]) is not int
        or config["max_full_chat_tokens"] < 2
    ):
        raise ValueError("invalid sharded candidate subset config")
    if any(
        not isinstance(lane, str)
        or not lane
        or not isinstance(operations, list)
        or not operations
        or any(not isinstance(op, str) or not op for op in operations)
        or len(operations) != len(set(operations))
        for lane, operations in config["lane_operations"].items()
    ):
        raise ValueError("invalid subset lane/operation support")
    rules = {
        lane: set(operations) for lane, operations in config["lane_operations"].items()
    }
    index_path = Path(config["index"])
    if index_path.is_absolute() or ".." in index_path.parts:
        raise ValueError("subset index escapes workspace")
    index_dir = ROOT / index_path
    bank = verify_index(index_dir, full_readers=True)
    source_pins = (
        [config["source_pool"]] if "source_pool" in config else config["source_pools"]
    )
    if (
        not isinstance(source_pins, list)
        or not source_pins
        or len(source_pins)
        != len({json.dumps(pin, sort_keys=True) for pin in source_pins})
    ):
        raise ValueError("subset source pools must be unique pinned entries")
    source_pools = [_pin(pin) for pin in source_pins]
    selected = []
    groups: dict[str, str] = {}
    for ref in _rows(index_dir / "candidate_refs.jsonl"):
        candidate = ref["candidate"]
        if candidate["operation"] not in rules.get(candidate["source_name"], ()):
            continue
        if candidate["source_kind"] != "real_wiki":
            raise ValueError("subset lane selected a non-Wiki source")
        if candidate["full_chat_tokens"] > config["max_full_chat_tokens"]:
            raise ValueError("subset reader exceeds token budget")
        group = candidate["source_group"]
        if group in groups and groups[group] != candidate["split"]:
            raise ValueError("subset source group crosses split")
        groups[group] = candidate["split"]
        selected.append(ref)
    if not selected or {ref["candidate"]["split"] for ref in selected} != {
        "train",
        "eval",
    }:
        raise ValueError("subset must have train and eval readers")
    world_manifest = _source_worlds(source_pools, groups)
    shards = {entry["name"]: entry for entry in bank["shards"]}
    targets: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for ref in selected:
        row = ref["candidate"]
        key = ref["shard"], row["split"]
        if row["row_index"] in targets[key]:
            raise ValueError("subset repeats a reader pointer")
        targets[key][row["row_index"]] = row
    tokenizer = get_tokenizer()
    readers = {}
    audits = {}
    for (shard_name, split), positions in targets.items():
        source = _shard_path(index_dir, shards[shard_name])
        pending = set(positions)
        for position, reader in enumerate(_rows(source / f"candidate_{split}.jsonl")):
            if position not in pending:
                continue
            candidate = positions[position]
            if reader.get("sample_id") != candidate["sample_id"]:
                raise ValueError("subset reader pointer changed")
            checked = audit_reader(
                reader, candidate, tokenizer, config["max_full_chat_tokens"]
            )
            readers[candidate["sample_id"]] = reader
            audits[candidate["sample_id"]] = checked
            pending.remove(position)
            if not pending:
                break
        if pending:
            raise ValueError("subset reader body missing")
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "eval": []}
    sample_index = []
    task_specs: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in selected:
        row = ref["candidate"]
        sample_id = row["sample_id"]
        split = row["split"]
        task_key = row["source_kind"], row["semantic_task_id"]
        task = task_specs.setdefault(
            task_key,
            {
                "semantic_task_id": row["semantic_task_id"],
                "source_group": row["source_group"],
                "split": split,
                "operation": row["operation"],
                "answer_sha256": row["answer_sha256"],
                "sample_ids": [],
            },
        )
        if any(
            task[key] != row[key]
            for key in ("source_group", "split", "operation", "answer_sha256")
        ):
            raise ValueError("subset semantic task contract changed across views")
        task["sample_ids"].append(sample_id)
        by_split[split].append(readers[sample_id])
        sample_index.append(
            {
                **row,
                "pack_file": f"{split}.jsonl",
                "pack_row_index": len(by_split[split]) - 1,
            }
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="candidate-subset-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        _write_lines(temp / "train.jsonl", by_split["train"])
        _write_lines(temp / "eval.jsonl", by_split["eval"])
        _write_lines(temp / "sample_index.jsonl", sample_index)
        _write_lines(
            temp / "task_specs.jsonl",
            [task_specs[key] for key in sorted(task_specs)],
        )
        _write_lines(
            temp / "mask_audit.jsonl",
            [audits[key] for key in sorted(audits)],
        )
        (temp / "source_manifest.json").write_text(
            json.dumps(world_manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        )
        counts = Counter(row["length_bin"] for row in sample_index)
        ops = Counter(row["operation"] for row in sample_index)
        manifest = {
            "schema": SCHEMA + ".result",
            "config_sha256": _sha(config_path),
            "index_manifest_sha256": _sha(index_dir / "manifest.json"),
            "index_refs_sha256": bank["refs_sha256"],
            **(
                {"source_pool_sha256": _sha(source_pools[0])}
                if "source_pool" in config
                else {"source_pools_sha256": [_sha(path) for path in source_pools]}
            ),
            "candidate_views": len(sample_index),
            "independent_semantic_tasks": len(task_specs),
            "source_groups": len(groups),
            "splits": {split: len(rows) for split, rows in by_split.items()},
            "length_bins": dict(sorted(counts.items())),
            "operations": dict(sorted(ops.items())),
            "mask_audited_views": len(audits),
            "full_chat_tokens": sum(row["full_chat_tokens"] for row in sample_index),
            "supervised_tokens": sum(row["supervised_tokens"] for row in sample_index),
            "tokenizer": {
                "model_id": TOKENIZER_MODEL,
                "revision": TOKENIZER_REVISION,
            },
            "files_sha256": {
                name: _sha(temp / name)
                for name in (
                    "train.jsonl",
                    "eval.jsonl",
                    "sample_index.jsonl",
                    "task_specs.jsonl",
                    "mask_audit.jsonl",
                    "source_manifest.json",
                )
            },
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        os.rename(temp, output_dir)
    return manifest


def verify(config_path: Path, output_dir: Path) -> dict[str, Any]:
    stored = json.loads((output_dir / "manifest.json").read_text())
    for name, digest in stored["files_sha256"].items():
        if _sha(output_dir / name) != digest:
            raise ValueError(f"subset output changed: {name}")
    with tempfile.TemporaryDirectory(
        prefix="candidate-subset-verify-", dir=output_dir.parent
    ) as raw:
        rebuilt = export(config_path, Path(raw) / "subset")
        if rebuilt != stored:
            raise ValueError("subset manifest differs from source replay")
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        verify(args.config, args.output)
        if args.verify_only
        else export(args.config, args.output)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
