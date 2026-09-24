"""Recheck selected candidate readers against the exact SFT assistant mask.

This is a candidate-only audit. It reads final reader bytes, never exports a
training set and never changes the source bank or selection ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from longworld.synthesis.unified_candidate_contract import _answer_hash
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.unified-reader-mask-audit.v1"
SELECTION_SCHEMA = "longworld.unified-candidate-selection.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def audit_reader(
    reader: dict[str, Any], index: dict[str, Any], tokenizer: Any, max_seq_len: int
) -> dict[str, Any]:
    """Verify one final byte-level reader and the training loader's loss mask."""
    if (
        set(reader) != {"sample_id", "messages"}
        or reader["sample_id"] != index["sample_id"]
    ):
        raise ValueError("reader identity or audit-field leakage")
    messages = reader["messages"]
    if (
        not isinstance(messages, list)
        or len(messages) != 2
        or any(
            not isinstance(item, dict) or set(item) != {"role", "content"}
            for item in messages
        )
        or [item["role"] for item in messages] != ["user", "assistant"]
        or any(
            not isinstance(item["content"], str) or not item["content"]
            for item in messages
        )
    ):
        raise ValueError("reader message shape or audit-field leakage")
    if _answer_hash(messages[1]["content"]) != index["answer_sha256"]:
        raise ValueError("reader answer differs from selected candidate")
    encoded = tokenize_assistant_only(tokenizer, messages, max_length=max_seq_len)
    labels = encoded["labels"]
    ids = encoded["input_ids"]
    input_tokens = index["input_tokens"]
    supervised_tokens = index["supervised_tokens"]
    full_tokens = index["full_chat_tokens"]
    if (
        type(input_tokens) is not int
        or type(supervised_tokens) is not int
        or type(full_tokens) is not int
        or input_tokens < 1
        or supervised_tokens < 1
        or len(ids) != full_tokens
        or input_tokens + supervised_tokens != full_tokens
        or labels[:input_tokens] != [-100] * input_tokens
        or labels[input_tokens:] != ids[input_tokens:]
        or len(labels[input_tokens:]) != supervised_tokens
        or index.get("tokenizer_profile") != "pinned-chat-template"
    ):
        raise ValueError("final chat token count or assistant loss mask differs")
    return {
        "sample_id": index["sample_id"],
        "split": index["split"],
        "source_kind": index["source_kind"],
        "operation": index["operation"],
        "full_chat_tokens": full_tokens,
        "input_tokens": input_tokens,
        "supervised_tokens": supervised_tokens,
        "loss_mask_start": input_tokens,
        "reader_sha256": hashlib.sha256(
            json.dumps(
                reader, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "status": "exact_assistant_mask_checked",
    }


def audit(
    merged_dir: Path,
    selection_dir: Path,
    output_dir: Path,
    *,
    max_seq_len: int = 262144,
    tokenizer: Any | None = None,
) -> dict[str, Any]:
    """Stream selected readers; bind every count to source and selection hashes."""
    if max_seq_len < 2:
        raise ValueError("max_seq_len must exceed one token")
    if output_dir.exists():
        raise ValueError("audit output already exists")
    source = verify_merge(merged_dir)
    selection = _json(selection_dir / "manifest.json")
    if (
        selection.get("schema_version") != SELECTION_SCHEMA
        or selection.get("train_ready") is not False
        or selection.get("input_manifest_sha256") != _sha(merged_dir / "manifest.json")
        or selection.get("selection_index_sha256")
        != _sha(selection_dir / "selection_index.jsonl")
    ):
        raise ValueError("selection is not bound to candidate source bytes")
    selected: dict[str, dict[int, dict[str, Any]]] = {"train": {}, "eval": {}}
    seen: set[str] = set()
    with (selection_dir / "selection_index.jsonl").open(encoding="utf-8") as stream:
        for rank, line in enumerate(stream):
            row = json.loads(line)
            split = row.get("split")
            position = row.get("row_index")
            sample_id = row.get("sample_id")
            if (
                split not in selected
                or type(position) is not int
                or position < 0
                or row.get("output_file") != f"candidate_{split}.jsonl"
                or row.get("selection_rank") != rank
                or not isinstance(sample_id, str)
                or sample_id in seen
                or position in selected[split]
            ):
                raise ValueError("invalid selected reader pointer")
            seen.add(sample_id)
            selected[split][position] = row
    if len(seen) != selection.get("selected_views"):
        raise ValueError("selection count differs from index")

    # The source index is compact; check metadata pointers before reading bodies.
    pending = {(split, pos) for split, rows in selected.items() for pos in rows}
    positions = Counter()
    with (merged_dir / "sample_index.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            index = json.loads(line)
            split = index["split"]
            pos = positions[split]
            positions[split] += 1
            if (split, pos) not in pending:
                continue
            selected_row = selected[split][pos]
            if any(
                index.get(key) != value
                for key, value in selected_row.items()
                if key != "selection_rank"
            ):
                raise ValueError("selected pointer differs from candidate index")
            selected_row.update(
                input_tokens=index["input_tokens"],
                supervised_tokens=index["supervised_tokens"],
                tokenizer_profile=index["tokenizer_profile"],
            )
            pending.remove((split, pos))
    if pending or sum(positions.values()) != source["candidate_views"]:
        raise ValueError("candidate source index is incomplete")

    tokenizer = tokenizer or get_tokenizer()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="reader-mask-audit-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        results: list[dict[str, Any]] = []
        for split in ("train", "eval"):
            targets = selected[split]
            if not targets:
                continue
            remaining = set(targets)
            with (merged_dir / f"candidate_{split}.jsonl").open(
                encoding="utf-8"
            ) as stream:
                for position, line in enumerate(stream):
                    if position not in remaining:
                        continue
                    results.append(
                        audit_reader(
                            json.loads(line), targets[position], tokenizer, max_seq_len
                        )
                    )
                    remaining.remove(position)
                    if not remaining:
                        break
            if remaining:
                raise ValueError("selected final reader row is missing")
        results.sort(key=lambda row: row["sample_id"])
        result_path = temp / "audit_index.jsonl"
        with result_path.open("x", encoding="utf-8") as stream:
            for row in results:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        manifest = {
            "schema_version": SCHEMA,
            "source_manifest_sha256": _sha(merged_dir / "manifest.json"),
            "selection_manifest_sha256": _sha(selection_dir / "manifest.json"),
            "selection_index_sha256": _sha(selection_dir / "selection_index.jsonl"),
            "tokenizer": {"model": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
            "max_seq_len": max_seq_len,
            "audited_views": len(results),
            "by_split": dict(sorted(Counter(row["split"] for row in results).items())),
            "full_chat_tokens": sum(row["full_chat_tokens"] for row in results),
            "supervised_tokens": sum(row["supervised_tokens"] for row in results),
            "audit_index_sha256": _sha(result_path),
            "scope": "candidate_mask_and_reader_shape_only",
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.rename(temp, output_dir)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("merged_dir", type=Path)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seq-len", type=int, default=262144)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(
                args.merged_dir,
                args.selection,
                args.output,
                max_seq_len=args.max_seq_len,
            )
        )
    )


if __name__ == "__main__":
    main()
