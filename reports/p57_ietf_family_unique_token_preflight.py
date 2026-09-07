#!/usr/bin/env python3
"""Measure exact unique RFC-body tokens for a fetched IETF family inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

TOKENIZER_MODEL_ID = "Qwen/Qwen3.5-4B"
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rows(inventory: Path, tokenizer: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(inventory.iterdir()):
        if not path.is_file() or path.name == "ietf_fetch_inventory.json":
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        tokens = len(tokenizer(text, add_special_tokens=False)["input_ids"])
        rows.append(
            {
                "file": path.name,
                "bytes": len(raw),
                "sha256": _sha256(raw),
                "tokens": tokens,
            }
        )
    return rows


def _rfc_sum(rows: Sequence[dict[str, Any]]) -> int:
    return sum(row["tokens"] for row in rows if row["file"].startswith("rfc"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--exclude-inventory", type=Path, action="append", default=[])
    args = parser.parse_args()
    tokenizer = AutoTokenizer.from_pretrained(
        TOKENIZER_MODEL_ID,
        revision=TOKENIZER_REVISION,
        local_files_only=True,
    )
    rows = _rows(args.inventory, tokenizer)
    excluded_rfc_files: set[str] = set()
    for other in args.exclude_inventory:
        excluded_rfc_files.update(
            path.name
            for path in other.iterdir()
            if path.is_file() and path.name.startswith("rfc")
        )
    unique_delta_rows = [
        row
        for row in rows
        if row["file"].startswith("rfc") and row["file"] not in excluded_rfc_files
    ]
    payload = {
        "tokenizer_model_id": TOKENIZER_MODEL_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "inventory": str(args.inventory),
        "rfc_token_sum": _rfc_sum(rows),
        "all_token_sum": sum(row["tokens"] for row in rows),
        "unique_delta_rfc_files": [row["file"] for row in unique_delta_rows],
        "unique_delta_rfc_token_sum": sum(row["tokens"] for row in unique_delta_rows),
        "excluded_rfc_files": sorted(excluded_rfc_files),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
