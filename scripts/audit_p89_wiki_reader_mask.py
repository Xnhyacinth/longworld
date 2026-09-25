"""Audit final P89 native Wiki readers against the pinned assistant-only mask."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.train_sft import tokenize_assistant_only


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(merged_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise ValueError("audit output must be new")
    rows = [
        json.loads(line)
        for line in (merged_dir / "sample_index.jsonl").read_text().splitlines()
    ]
    by_id = {row["example_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate example identity")
    tokenizer = get_tokenizer()
    checked = []
    seen = set()
    for split in ("train", "eval"):
        for line in (merged_dir / f"{split}.jsonl").read_text().splitlines():
            reader = json.loads(line)
            index = by_id.get(reader["example_id"])
            if index is None or index["split"] != split or reader["example_id"] in seen:
                raise ValueError("reader/index identity or split mismatch")
            seen.add(reader["example_id"])
            messages = reader["messages"]
            if [message["role"] for message in messages] != ["user", "assistant"]:
                raise ValueError("reader message roles differ")
            encoded = tokenize_assistant_only(tokenizer, messages, 65536)
            full = len(encoded["input_ids"])
            supervised = sum(label != -100 for label in encoded["labels"])
            if (
                full != index["full_chat_tokens"]
                or supervised != index["supervised_tokens"]
                or full - supervised != index["input_tokens"]
                or encoded["labels"][: full - supervised]
                != [-100] * (full - supervised)
                or encoded["labels"][full - supervised :]
                != encoded["input_ids"][full - supervised :]
            ):
                raise ValueError("final tokenization or assistant mask drift")
            checked.append(
                {
                    "example_id": reader["example_id"],
                    "split": split,
                    "full_chat_tokens": full,
                    "supervised_tokens": supervised,
                }
            )
    if seen != set(by_id):
        raise ValueError("index has readers missing from final files")
    output_dir.mkdir(parents=True)
    (output_dir / "audit_index.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n"
            for row in sorted(checked, key=lambda item: item["example_id"])
        )
    )
    manifest = {
        "schema": "longworld.p89-wiki-reader-mask-audit.v1",
        "source_index_sha256": _sha(merged_dir / "sample_index.jsonl"),
        "train_sha256": _sha(merged_dir / "train.jsonl"),
        "eval_sha256": _sha(merged_dir / "eval.jsonl"),
        "checked_rows": len(checked),
        "split_counts": dict(sorted(Counter(row["split"] for row in checked).items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in checked),
        "supervised_tokens": sum(row["supervised_tokens"] for row in checked),
        "audit_index_sha256": _sha(output_dir / "audit_index.jsonl"),
        "train_ready": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.merged_dir, args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
