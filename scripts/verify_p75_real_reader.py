"""Verify the P75 local reader export against its actual SFT loss mask."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import length_controller as lc
from scripts.train_sft import tokenize_assistant_only


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _token_limit(manifest: dict) -> int:
    if manifest.get("schema") == "longworld.p75-real-reader-export.v1":
        limit = manifest["config"]["max_full_tokens"]
    elif manifest.get("schema") in {
        "longworld.p76-wiki-table-pairs.v5",
        "longworld.p76-wiki-table-scan.v2",
    }:
        limit = 262144
    else:
        raise ValueError("unsupported reader export schema")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("invalid max_full_tokens in reader manifest")
    return limit


def verify(directory: Path, *, write: bool = True) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files_sha256"].items():
        observed = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if observed != expected:
            raise ValueError(f"file digest mismatch: {name}")
    indices = {
        row["example_id"]: row for row in _rows(directory / "sample_index.jsonl")
    }
    audits = {row["example_id"]: row for row in _rows(directory / "audit.jsonl")}
    rows = {split: _rows(directory / f"{split}.jsonl") for split in ("train", "eval")}
    all_rows = [row for split in ("train", "eval") for row in rows[split]]
    if len(indices) != len(audits) or len(indices) != len(all_rows):
        raise ValueError("row/index/audit cardinality mismatch")
    if len({row["example_id"] for row in all_rows}) != len(all_rows):
        raise ValueError("duplicate example_id")
    groups = {"train": set(), "eval": set()}
    tokenizer = lc.get_tokenizer()
    max_full_tokens = _token_limit(manifest)
    supervised = 0
    max_full = 0
    exact_mappings = 0
    for split in ("train", "eval"):
        for row in rows[split]:
            example_id = row["example_id"]
            index = indices[example_id]
            audit = audits[example_id]
            if index["split"] != split or audit["example_id"] != example_id:
                raise ValueError(f"split or audit mismatch: {example_id}")
            groups[split].add(index["source_group"])
            user_text = row["messages"][0]["content"]
            if any(
                marker in user_text
                for marker in ("=== FACTS ===", "=== ENTITIES ===", "=== SCOPE ===")
            ):
                raise ValueError(f"audit index leaked into reader text: {example_id}")
            if "program" in row or "proof" in row:
                raise ValueError(f"audit field leaked into reader row: {example_id}")
            encoded = tokenize_assistant_only(
                tokenizer, row["messages"], max_full_tokens
            )
            if len(encoded["input_ids"]) != index["full_chat_tokens"]:
                raise ValueError(f"full chat length mismatch: {example_id}")
            labels = encoded["labels"]
            active = sum(label != -100 for label in labels)
            if not 0 < active < len(labels):
                raise ValueError(f"empty or all-source supervision: {example_id}")
            if (
                len(labels) - active != index["input_tokens"]
                or active != index["supervised_tokens"]
            ):
                raise ValueError(f"assistant mask count mismatch: {example_id}")
            supervised += active
            max_full = max(max_full, len(labels))
            context = user_text.split("\n\nQUESTION\n", 1)[0]
            for mapping in audit["source_to_reader_spans"]:
                excerpt = context[mapping["reader_start"] : mapping["reader_end"]]
                if (
                    hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                    != mapping["text_sha256"]
                ):
                    raise ValueError(f"reader evidence drift: {example_id}")
                if (
                    not 0
                    <= mapping["prompt_token_start"]
                    < mapping["prompt_token_end"]
                    <= len(labels)
                ):
                    raise ValueError(
                        f"evidence token offset out of bounds: {example_id}"
                    )
                exact_mappings += 1
            if not index["fact_value_token_spans"]:
                raise ValueError(f"missing fact value token span: {example_id}")
    if groups["train"] & groups["eval"]:
        raise ValueError("train/eval source worlds overlap")
    verification_schema = {
        "longworld.p75-real-reader-export.v1": "longworld.p75-real-reader-verification.v1",
        "longworld.p76-wiki-table-pairs.v5": "longworld.p76-wiki-table-verification.v1",
        "longworld.p76-wiki-table-scan.v2": "longworld.p76-wiki-table-scan-verification.v1",
    }[manifest["schema"]]
    result = {
        "schema": verification_schema,
        "rows": {split: len(rows[split]) for split in ("train", "eval")},
        "source_groups": {split: len(groups[split]) for split in ("train", "eval")},
        "max_full_chat_tokens": max_full,
        "supervised_tokens": supervised,
        "exact_evidence_span_mappings": exact_mappings,
        "checks": "file_hashes; source_split; no_audit_markers; reader_span_hashes; pinned_chat_tokens; assistant_only_mask",
        "train_ready": False,
    }
    if write:
        (directory / "verification.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=ROOT / "data/p75_real_reader_candidates_v1",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="verify immutable exports without adding verification.json",
    )
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, write=not args.no_write), sort_keys=True))


if __name__ == "__main__":
    main()
