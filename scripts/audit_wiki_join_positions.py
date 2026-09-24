"""Map two bounded Wiki JOIN evidence cells into final-reader token positions."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.wiki-join-position-audit.v1"
SEPARATOR = "\n\nQUESTION\n"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def token_span(offsets: list[tuple[int, int]], start: int, end: int) -> tuple[int, int]:
    """Return the tokens overlapping a preserved Python character span."""
    positions = [
        position
        for position, (left, right) in enumerate(offsets)
        if right > left and left < end and right > start
    ]
    if (
        not positions
        or offsets[positions[0]][0] > start
        or offsets[positions[-1]][1] < end
    ):
        raise ValueError("evidence character span is not covered by tokenizer offsets")
    return positions[0], positions[-1] + 1


def audit(
    native_dir: Path,
    merged_dir: Path,
    source_name: str,
    output_dir: Path,
    *,
    tokenizer=None,
) -> dict:
    if output_dir.exists():
        raise ValueError("position audit output already exists")
    native_manifest = json.loads((native_dir / "manifest.json").read_text())
    if native_manifest.get("schema") != "longworld.wiki-row-binding-probe.v1":
        raise ValueError("wrong Wiki JOIN native schema")
    for name, expected in native_manifest["files_sha256"].items():
        if _sha(native_dir / name) != expected:
            raise ValueError(f"native Wiki JOIN file changed: {name}")
    verify_merge(merged_dir)
    native_index = {
        row["example_id"]: row for row in _rows(native_dir / "sample_index.jsonl")
    }
    native_audit = {row["example_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    native_readers = {
        row["example_id"]: row
        for split in ("train", "eval")
        for row in _rows(native_dir / f"{split}.jsonl")
    }
    selected = {}
    for row in _rows(merged_dir / "sample_index.jsonl"):
        if row["source_name"] == source_name:
            selected[(row["split"], row["row_index"])] = row
    if (
        not selected
        or len(selected) != native_manifest["views"]
        or set(native_index) != set(native_audit) != set(native_readers)
    ):
        raise ValueError("native and merged Wiki JOIN rows differ in cardinality")
    tokenizer = tokenizer or get_tokenizer()
    reports = []
    for split in ("train", "eval"):
        with (merged_dir / f"candidate_{split}.jsonl").open(encoding="utf-8") as stream:
            for row_index, line in enumerate(stream):
                merged = selected.get((split, row_index))
                if merged is None:
                    continue
                reader = json.loads(line)
                sample_id = merged["sample_id"]
                native = native_index.get(sample_id)
                evidence = native_audit.get(sample_id)
                original = native_readers.get(sample_id)
                if (
                    native is None
                    or evidence is None
                    or original is None
                    or reader["sample_id"] != sample_id
                    or reader["messages"] != original["messages"]
                    or native["split"] != split
                    or native["full_chat_tokens"] != merged["full_chat_tokens"]
                ):
                    raise ValueError("final reader or native evidence identity changed")
                messages = reader["messages"]
                user = messages[0]["content"]
                context_chars = native["context_chars"]
                if user[context_chars : context_chars + len(SEPARATOR)] != SEPARATOR:
                    raise ValueError("final reader question boundary changed")
                prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
                user_start = prompt.find(user)
                if user_start < 0 or prompt.count(user) != 1:
                    raise ValueError("user text is not unique in chat template")
                offsets = tokenizer(
                    prompt, truncation=False, return_offsets_mapping=True
                )["offset_mapping"]
                positions = {}
                for name in ("selector", "target"):
                    start, end = evidence[name + "_span"]
                    if not 0 <= start < end <= context_chars:
                        raise ValueError("native evidence exceeds reader context")
                    positions[name] = token_span(
                        offsets, user_start + start, user_start + end
                    )
                query = token_span(
                    offsets,
                    user_start + context_chars + len(SEPARATOR),
                    user_start + len(user),
                )[0]
                labels = tokenize_assistant_only(tokenizer, messages, 262144)["labels"]
                if (
                    len(labels) != merged["full_chat_tokens"]
                    or sum(label != -100 for label in labels)
                    != merged["supervised_tokens"]
                    or max(end for _, end in positions.values())
                    > merged["input_tokens"]
                ):
                    raise ValueError("evidence crosses final assistant mask")
                first = min(start for start, _ in positions.values())
                last = max(end for _, end in positions.values())
                reports.append(
                    {
                        "sample_id": sample_id,
                        "split": split,
                        "source_name": source_name,
                        "full_chat_tokens": merged["full_chat_tokens"],
                        "selector_token_span": positions["selector"],
                        "target_token_span": positions["target"],
                        "query_token_start": query,
                        "evidence_extent_tokens": last - first,
                        "last_evidence_to_query_tokens": query - last,
                        "scope": evidence["necessity_scope"],
                    }
                )
    if len(reports) != len(selected):
        raise ValueError("some final Wiki JOIN readers were not mapped")
    output_dir.mkdir(parents=True)
    index_path = output_dir / "position_index.jsonl"
    index_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in reports),
        encoding="utf-8",
    )
    result = {
        "schema_version": SCHEMA,
        "native_manifest_sha256": _sha(native_dir / "manifest.json"),
        "merged_manifest_sha256": _sha(merged_dir / "manifest.json"),
        "source_name": source_name,
        "mapped_views": len(reports),
        "evidence_extent_tokens": {
            "min": min(row["evidence_extent_tokens"] for row in reports),
            "max": max(row["evidence_extent_tokens"] for row in reports),
        },
        "position_index_sha256": _sha(index_path),
        "scope": "actual token positions of two bounded table cells",
        "train_ready": False,
    }
    (output_dir / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_dir", type=Path)
    parser.add_argument("merged_dir", type=Path)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(args.native_dir, args.merged_dir, args.source_name, args.output)
        )
    )


if __name__ == "__main__":
    main()
