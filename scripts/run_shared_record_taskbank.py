"""Parallel candidate pilot for genuinely shared record-world operations.

One immutable shard per seed.  Each reader example has one question and one
answer; internal programs/provenance stay in the sidecar, outside the reader
text.  --verify-only replays the frozen worlds and checks hashes and masks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.synthesis import shared_record_taskbank as shared
from scripts.run_capability_records import MODEL, REVISION
from scripts.train_sft import tokenize_assistant_only


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generate(job: tuple[int, int, int, int, int]) -> dict:
    return shared.build_world(*job)


def _tokenizer():
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            MODEL, revision=REVISION, local_files_only=True, trust_remote_code=False
        )


def _reader_row(world: dict, task: dict, tokenizer) -> dict:
    messages = [
        {
            "role": "user",
            "content": world["reader_context"] + "\n\nQUESTION\n" + task["instruction"],
        },
        {"role": "assistant", "content": _dump(task["answer"])},
    ]
    encoded = tokenize_assistant_only(tokenizer, messages, 262144)
    labels = encoded["labels"]
    masked = sum(label == -100 for label in labels)
    supervised = len(labels) - masked
    if masked == 0 or supervised == 0 or labels[:masked] != [-100] * masked:
        raise ValueError("invalid assistant-only loss mask")
    if any(label == -100 for label in labels[masked:]):
        raise ValueError("non-contiguous assistant loss mask")
    return {
        "schema_version": shared.VERSION + ".reader.v1",
        "example_id": world["world_id"] + ":" + task["task_id"],
        "semantic_task_id": world["world_id"] + ":" + task["task_id"],
        "world_id": world["world_id"],
        "source_group": world["world_id"],
        "split": "eval" if world["seed"] % 5 == 0 else "train",
        "operation": task["operation"],
        "pair_id": world["world_id"] + ":" + task["pair_id"],
        "context_sha256": world["context_sha256"],
        "messages": messages,
        "full_chat_tokens": len(labels),
        "input_tokens": masked,
        "supervised_tokens": supervised,
        "mask_contract": "assistant_only_no_truncation",
        "train_ready": False,
    }


def _write_shard(output: Path, world: dict, tokenizer) -> dict:
    shard = output / "shards" / world["world_id"]
    shard.mkdir(parents=True, exist_ok=False)
    world_path, rows_path, receipt_path = (
        shard / "world.json",
        shard / "rows.jsonl",
        shard / "receipt.json",
    )
    world_path.write_text(_dump(world) + "\n", encoding="utf-8")
    rows = [_reader_row(world, task, tokenizer) for task in world["tasks"]]
    rows_path.write_text("".join(_dump(row) + "\n" for row in rows), encoding="utf-8")
    receipt = {
        "world_id": world["world_id"],
        "world_sha256": _sha(world_path),
        "rows_sha256": _sha(rows_path),
        "rows": len(rows),
        "pairs": len(world["shared_consumed_row_ids"]),
        "shared_consumed_record_ids": sum(
            len(ids) for ids in world["shared_consumed_row_ids"].values()
        ),
        "operations": dict(Counter(row["operation"] for row in rows)),
        "split": rows[0]["split"],
        "min_full_chat_tokens": min(row["full_chat_tokens"] for row in rows),
        "max_full_chat_tokens": max(row["full_chat_tokens"] for row in rows),
    }
    receipt_path.write_text(_dump(receipt) + "\n", encoding="utf-8")
    return receipt


def verify(output: Path, tokenizer, *, require_manifest: bool = False) -> dict:
    receipts = []
    for receipt_path in sorted((output / "shards").glob("*/receipt.json")):
        shard = receipt_path.parent
        receipt = json.loads(receipt_path.read_text())
        world_path, rows_path = shard / "world.json", shard / "rows.jsonl"
        if receipt["world_sha256"] != _sha(world_path) or receipt[
            "rows_sha256"
        ] != _sha(rows_path):
            raise ValueError(f"shard hash mismatch: {shard}")
        world = json.loads(world_path.read_text())
        if not shared.validate_world(world)["passed"]:
            raise ValueError(f"world replay failed: {shard}")
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        expected = [_reader_row(world, task, tokenizer) for task in world["tasks"]]
        if rows != expected or receipt["rows"] != len(rows):
            raise ValueError(f"reader or mask replay mismatch: {shard}")
        if receipt["shared_consumed_record_ids"] != sum(
            len(ids) for ids in world["shared_consumed_row_ids"].values()
        ):
            raise ValueError(f"shared evidence receipt mismatch: {shard}")
        receipts.append(receipt)
    if not receipts:
        raise ValueError("no completed shared-world shards")
    manifest = {
        "schema_version": shared.VERSION + ".manifest.v1",
        "worlds": len(receipts),
        "reader_rows": sum(item["rows"] for item in receipts),
        "paired_semantic_scopes": sum(item["pairs"] for item in receipts),
        "shared_consumed_record_ids": sum(
            item["shared_consumed_record_ids"] for item in receipts
        ),
        "operations": dict(
            sorted(
                Counter(
                    {
                        name: sum(item["operations"].get(name, 0) for item in receipts)
                        for name in shared.OPERATIONS
                    }
                ).items()
            )
        ),
        "split_rows": dict(
            sorted(
                Counter(
                    {
                        split: sum(
                            item["rows"] for item in receipts if item["split"] == split
                        )
                        for split in ("train", "eval")
                    }
                ).items()
            )
        ),
        "min_full_chat_tokens": min(item["min_full_chat_tokens"] for item in receipts),
        "max_full_chat_tokens": max(item["max_full_chat_tokens"] for item in receipts),
        "tokenizer": {"model_id": MODEL, "revision": REVISION},
        "train_ready": False,
        "evidence_scope": "native solver and per-shared-row deletion on controlled JSON records",
    }
    existing = output / "manifest.json"
    if require_manifest and not existing.is_file():
        raise ValueError("frozen manifest is missing")
    if existing.exists() and json.loads(existing.read_text()) != manifest:
        raise ValueError("frozen manifest disagrees with verified shards")
    return manifest


def build(
    output: Path,
    *,
    worlds: int,
    seed_base: int,
    length_records: int,
    consumed_records: int,
    depth: int,
    variants: int,
    workers: int,
) -> dict:
    """Generate a new native batch from deterministic, independent world seeds."""
    if output.exists() or worlds < 1 or workers < 1:
        raise ValueError("new output and positive worlds/workers are required")
    tokenizer = _tokenizer()
    output.mkdir(parents=True)
    jobs = [
        (seed_base + index, length_records, consumed_records, depth, variants)
        for index in range(worlds)
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for world in executor.map(_generate, jobs):
            _write_shard(output, world, tokenizer)
    manifest = verify(output, tokenizer)
    (output / "manifest.json").write_text(_dump(manifest) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worlds", type=int, default=8)
    parser.add_argument("--seed-base", type=int, default=141000)
    parser.add_argument("--length-records", type=int, default=400)
    parser.add_argument("--consumed-records", type=int, default=20)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--variants", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = (
        verify(args.output, _tokenizer(), require_manifest=True)
        if args.verify_only
        else build(
            args.output,
            worlds=args.worlds,
            seed_base=args.seed_base,
            length_records=args.length_records,
            consumed_records=args.consumed_records,
            depth=args.depth,
            variants=args.variants,
            workers=args.workers,
        )
    )
    print(_dump(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
