"""Normalize the signed P132 native review-to-final-diff readers for candidate audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.codeforge_taskbank import canonical
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    TokenCounts,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p132_review_diff_join import SCHEMA, scoped_join, sha

LANE = "p132_review_diff_join"
SEPARATOR = "\n\nSource records:\n"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def convert(native_dir: Path, output: Path, *, verify_only: bool = False) -> dict:
    manifest_path = native_dir / "manifest.json"
    native = json.loads(manifest_path.read_text())
    if native["schema_version"] != SCHEMA or native["train_ready"] is not False:
        raise ValueError("P132 native claim boundary differs")
    if sha(ROOT / "scripts/p132_review_diff_join.py") != native["code_sha256"]:
        raise ValueError("P132 native compiler changed")
    for name, digest in native["files_sha256"].items():
        if sha(native_dir / name) != digest:
            raise ValueError("P132 native file SHA differs: " + name)
    readers = {
        split: rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
    }
    indices = rows(native_dir / "sample_index.jsonl")
    proofs = rows(native_dir / "audit.jsonl")
    if len(indices) != len(proofs) or len(indices) != native["views"]:
        raise ValueError("P132 native inventory differs")
    ledger = CandidateLedger()
    streams = {split: [] for split in readers}
    unified_index = []
    positions = Counter()
    for audit_pos, (index, proof) in enumerate(zip(indices, proofs, strict=True)):
        split = index["split"]
        reader_pos = positions[split]
        if index["output_file"] != f"{split}.jsonl" or index["row_index"] != reader_pos:
            raise ValueError("P132 native reader row order differs")
        reader = readers[split][reader_pos]
        positions[split] += 1
        if (
            reader["example_id"] != index["sample_id"]
            or proof["sample_id"] != index["sample_id"]
            or proof["answer"] != json.loads(reader["messages"][1]["content"])
            or proof["comment_and_target_text_interventions_change_answer"] is not True
            or proof["excluded_path_insertion"]["answer"] == proof["answer"]
        ):
            raise ValueError("P132 native reader/proof relation differs")
        user = reader["messages"][0]["content"]
        if user.count(SEPARATOR) != 1:
            raise ValueError("P132 question/context separator ambiguous")
        context = user.split(SEPARATOR, 1)[1]
        visible = json.loads(context)
        answer, _ = scoped_join(visible)
        if answer != proof["answer"]:
            raise ValueError("P132 visible reader answer differs")
        binding = AdapterBinding(
            source_kind="real_code_workflow",
            source_group=index["source_group"],
            domain="codeforge",
            topic=index["source_group"].rstrip("/").rsplit("/", 1)[-1],
            operation="reviewed_final_diff_paths",
            evidence_profile="review_comment_final_head_exact_join_v1",
            tokenizer_profile="pinned-chat-template",
            receipt_path=manifest_path,
            receipt_sha256=sha(manifest_path),
        )
        candidate = normalize_native_candidate(
            {
                **index,
                "evidence_status": binding.evidence_profile,
                "token_measurement": binding.tokenizer_profile,
            },
            reader,
            binding,
            context_text=context,
            token_counts=TokenCounts(
                index["full_chat_tokens"] - index["assistant_tokens"],
                index["assistant_tokens"],
                index["full_chat_tokens"],
            ),
        )
        ledger.add(candidate)
        streams[split].append(
            {"sample_id": candidate.sample_id, "messages": reader["messages"]}
        )
        entry = candidate.to_dict()
        entry.update(
            source_name=LANE,
            native_row_ref=f"{native_dir / f'{split}.jsonl'}:{reader_pos}",
            native_audit_ref=f"{native_dir / 'audit.jsonl'}:{audit_pos}",
            output_file=f"candidate_{split}.jsonl",
            row_index=reader_pos,
            observed_witness_gap_tokens=proof["evidence"][
                "max_item_final_chat_minimum_gap_tokens"
            ],
            content_proof_scope=proof["certificate_scope"],
        )
        unified_index.append(entry)
    if any(positions[split] != len(readers[split]) for split in readers):
        raise ValueError("P132 native reader inventory has trailing rows")
    if (
        ledger.rows != native["views"]
        or ledger.independent_tasks != native["semantic_tasks"]
    ):
        raise ValueError("P132 normalized task count differs")
    files = {
        "candidate_train.jsonl": streams["train"],
        "candidate_eval.jsonl": streams["eval"],
        "sample_index.jsonl": unified_index,
    }
    if not verify_only:
        output.mkdir(parents=True, exist_ok=False)
    digests = {}
    for name, data in files.items():
        content = "".join(canonical(row) + "\n" for row in data)
        if verify_only:
            if (output / name).read_text() != content:
                raise ValueError("P132 unified shard changed on replay: " + name)
        else:
            (output / name).write_text(content)
        digests[name] = hashlib.sha256(content.encode()).hexdigest()
    summary = {
        "schema_version": "longworld.unified-candidates.v1",
        "converter_sha256": sha(Path(__file__)),
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "views_by_lane": {LANE: ledger.rows},
        "splits": dict(sorted(Counter(index["split"] for index in indices).items())),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in unified_index).items())
        ),
        "operations": {"reviewed_final_diff_paths": ledger.rows},
        "native_manifest_sha256": sha(manifest_path),
        "native_audit_sha256": sha(native_dir / "audit.jsonl"),
        "native_rejected_scopes": native["rejects"],
        "answer_members": native["answer_members"],
        "long_answer_members": native["long_answer_members"],
        "tasks_with_clean_long_member": native["tasks_with_clean_long_member"],
        "long_distance_claim": native["long_distance_claim"],
        "claim_limit": "exact review-comment path versus merged-head diff join on pinned two-PR reader; no global shortcut exclusion",
        "files_sha256": digests,
        "train_ready": False,
        "redistribution_status": "local_research_only_no_redistribution",
    }
    if verify_only:
        if json.loads((output / "manifest.json").read_text()) != summary:
            raise ValueError("P132 unified manifest changed on replay")
    else:
        (output / "manifest.json").write_text(canonical(summary) + "\n")
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            convert(args.native_dir, args.output, verify_only=args.verify_only),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
