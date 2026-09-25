"""Measure P105 final-chat length, table footprint and evidence-query gaps."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from scripts.audit_wiki_join_positions import token_span
from scripts.run_p92_generic_table_scan import _sha
from scripts.train_sft import _render_chat

SCHEMA = "longworld.p105-length-evidence-report.v1"
SEPARATOR = "\n\nQUESTION\n"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _stats(values: list[int | float]) -> dict:
    return {
        "minimum": min(values),
        "median": statistics.median(values),
        "maximum": max(values),
    }


def report(native_dir: Path) -> dict:
    manifest = json.loads((native_dir / "manifest.json").read_text())
    indices = _rows(native_dir / "sample_index.jsonl")
    proofs = {row["sample_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    readers = {
        row["sample_id"]: row
        for split in ("train", "eval")
        for row in _rows(native_dir / f"{split}.jsonl")
    }
    if (
        set(proofs) != set(readers)
        or len(indices) != len(readers) != manifest["candidate_views"]
    ):
        raise ValueError("P105 length report inventory mismatch")
    tokenizer = get_tokenizer()
    rows = []
    for index in indices:
        sample_id = index["sample_id"]
        proof = proofs[sample_id]
        user = readers[sample_id]["messages"][0]["content"]
        suffix = SEPARATOR + proof["question"]
        if not user.endswith(suffix):
            raise ValueError("P105 final question boundary differs")
        context = user[: -len(suffix)]
        prompt = _render_chat(
            tokenizer, [{"role": "user", "content": user}], generation_prompt=True
        )
        if prompt.count(user) != 1:
            raise ValueError("P105 final user prompt is ambiguous")
        offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
            "offset_mapping"
        ]
        user_start = prompt.index(user)
        context_tokens = token_span(offsets, user_start, user_start + len(context))
        table_left, table_right = proof["reader_table_span"]
        table_tokens = token_span(
            offsets, user_start + table_left, user_start + table_right
        )
        question_start = user_start + len(context) + len(SEPARATOR)
        question_tokens = token_span(
            offsets, question_start, question_start + len(proof["question"])
        )
        evidence_end = max(
            cell["prompt_value_token_span"][1] for cell in proof["candidate_rows"]
        )
        context_count = context_tokens[1] - context_tokens[0]
        table_count = table_tokens[1] - table_tokens[0]
        if not 0 < table_count <= context_count or evidence_end > question_tokens[0]:
            raise ValueError("P105 table/context/query token order differs")
        rows.append(
            {
                "sample_id": sample_id,
                "world_id": index["world_id"],
                "source_group": index["source_group"],
                "domain": index["domain"],
                "full_chat_tokens": index["full_chat_tokens"],
                "supervised_tokens": index["supervised_tokens"],
                "context_tokens": context_count,
                "target_table_tokens": table_count,
                "outside_target_table_tokens": context_count - table_count,
                "outside_target_table_fraction": round(
                    (context_count - table_count) / context_count, 6
                ),
                "evidence_token_extent": index["evidence_token_extent"],
                "last_evidence_to_question_tokens": question_tokens[0] - evidence_end,
            }
        )
    return {
        "schema": SCHEMA,
        "native_manifest_sha256": _sha(native_dir / "manifest.json"),
        "rows": rows,
        "summary": {
            "tasks": len(rows),
            "worlds": dict(sorted(Counter(row["world_id"] for row in rows).items())),
            "full_chat_tokens": _stats([row["full_chat_tokens"] for row in rows]),
            "supervised_tokens_total": sum(row["supervised_tokens"] for row in rows),
            "evidence_token_extent": _stats(
                [row["evidence_token_extent"] for row in rows]
            ),
            "last_evidence_to_question_tokens": _stats(
                [row["last_evidence_to_question_tokens"] for row in rows]
            ),
            "outside_target_table_fraction": _stats(
                [row["outside_target_table_fraction"] for row in rows]
            ),
        },
        "interpretation": "table-local L2 complete-set evidence; table-external text fraction is not a claim that those tokens are semantically irrelevant",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = report(args.native_dir)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.verify_only:
        if not args.output.is_file() or args.output.read_text() != content:
            raise ValueError("P105 length/evidence report replay differs")
    else:
        if args.output.exists():
            raise ValueError("P105 length/evidence output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
