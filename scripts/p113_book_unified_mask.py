"""Audit every P113 unified book reader against final SFT loss masking."""

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
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.p112_book_tasks import _dump
from scripts.p113_book_audit import _rows
from scripts.p113_book_to_unified import LANE

SCHEMA = "longworld.p113-book-unified-all-mask.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(unified_dir: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    merged = verify_merge(unified_dir)
    readers = {
        split: _rows(unified_dir / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    indices = _rows(unified_dir / "sample_index.jsonl")
    if len(indices) != merged["candidate_views"]:
        raise ValueError("book unified index inventory differs")
    tokenizer = get_tokenizer()
    results, seen = [], set()
    for index in indices:
        split, position = index["split"], index["row_index"]
        if (
            split not in readers
            or type(position) is not int
            or not 0 <= position < len(readers[split])
            or (split, position) in seen
            or index["source_kind"] != "real_book"
            or index["source_name"] != LANE
        ):
            raise ValueError("book unified row pointer or lane differs")
        seen.add((split, position))
        results.append(audit_reader(readers[split][position], index, tokenizer, 262144))
    if len(seen) != sum(map(len, readers.values())):
        raise ValueError("book unified reader inventory incomplete")
    content = "".join(_dump(row) + "\n" for row in results).encode()
    receipt = {
        "schema": SCHEMA,
        "unified_manifest_sha256": _sha(unified_dir / "manifest.json"),
        "audited_readers": len(results),
        "splits": dict(sorted(Counter(row["split"] for row in results).items())),
        "mask_rows_sha256": hashlib.sha256(content).hexdigest(),
        "train_ready": False,
    }
    files = {
        "mask_rows.jsonl": content,
        "manifest.json": (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = output_dir / name
        if verify_only:
            if path.read_bytes() != data:
                raise ValueError(f"book unified mask replay differs: {name}")
        elif path.exists() and path.read_bytes() != data:
            raise ValueError(f"book unified mask output differs: {name}")
        else:
            path.write_bytes(data)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unified-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.unified_dir, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
