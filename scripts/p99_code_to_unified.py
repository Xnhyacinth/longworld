"""Project P99 code-content proof candidates into the unified reader contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    TokenCounts,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p99_code_content_tasks import canonical, run

LANE = "p99_code_content"
EVIDENCE = "reader_visible_added_code_two_pr_control_v1"
DEPENDENCY = "content_backed_two_source_scoped_certificate"
SEPARATOR = "\n\nSource records:\n"


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError("blank P99 native row")
            yield json.loads(line)


def _normalize_pair(index: dict, reader: dict, proof: dict, receipt: Path):
    sample = index["sample_id"]
    if (
        reader.get("example_id") != sample
        or proof.get("sample_id") != sample
        or index.get("dependency_status") != DEPENDENCY
        or index.get("source_kind") != "real_code_workflow"
        or proof.get("reader_visible_replay") is not True
        or proof.get("filename_preserving_added_code_removal_changes_answer")
        is not True
        or proof.get("all_selected_identifiers_absent_after_added_code_removal")
        is not True
        or proof.get("single_raw_16k_window_insufficient_for_both_witnesses")
        is not True
        or proof.get("evidence_token_span", 0) <= 16384
        or json.loads(reader["messages"][1]["content"]) != proof["answer"]
    ):
        raise ValueError("P99 native reader/index/proof identity or scope differs")
    user = reader["messages"][0]["content"]
    if user.count(SEPARATOR) != 1:
        raise ValueError("P99 reader question/context boundary is ambiguous")
    context = user.split(SEPARATOR, 1)[1]
    scoped = {
        **index,
        "evidence_status": EVIDENCE,
        "token_measurement": "pinned-chat-template",
    }
    binding = AdapterBinding(
        source_kind="real_code_workflow",
        source_group=index["source_group"],
        domain="codeforge",
        topic=index["source_group"].rstrip("/").rsplit("/", 1)[-1],
        operation="added_line_cross_pr_complete_set",
        evidence_profile=EVIDENCE,
        tokenizer_profile="pinned-chat-template",
        receipt_path=receipt,
        receipt_sha256=_sha(receipt),
    )
    candidate = normalize_native_candidate(
        scoped,
        reader,
        binding,
        context_text=context,
        token_counts=TokenCounts(
            input_tokens=index["full_chat_tokens"] - index["assistant_tokens"],
            supervised_tokens=index["assistant_tokens"],
            full_chat_tokens=index["full_chat_tokens"],
        ),
    )
    return candidate


def convert(
    config_path: Path, native_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    native = run(config_path, native_dir, verify_only=True)
    receipt = native_dir / "manifest.json"
    if (
        native.get("schema_version") != "longworld.p99-code-content.v1"
        or native.get("train_ready") is not False
        or native.get("strict_long_dependency_verified") is not False
    ):
        raise ValueError("P99 native manifest claim boundary changed")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p99_code_unified_", dir=output.parent
    ) as raw:
        staging = Path(raw)
        readers = {
            split: _rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
        }
        proofs = _rows(native_dir / "audit.jsonl")
        ledger = CandidateLedger()
        splits, lengths, positions = Counter(), Counter(), Counter()
        with (
            (staging / "candidate_train.jsonl").open("x", encoding="utf-8") as train,
            (staging / "candidate_eval.jsonl").open(
                "x", encoding="utf-8"
            ) as eval_stream,
            (staging / "sample_index.jsonl").open(
                "x", encoding="utf-8"
            ) as index_stream,
        ):
            streams = {"train": train, "eval": eval_stream}
            for audit_row, index in enumerate(_rows(native_dir / "sample_index.jsonl")):
                split = index["split"]
                if split not in readers:
                    raise ValueError("P99 native split invalid")
                reader = next(readers[split], None)
                proof = next(proofs, None)
                if reader is None or proof is None:
                    raise ValueError("P99 native reader/proof missing")
                candidate = _normalize_pair(index, reader, proof, receipt)
                ledger.add(candidate)
                streams[split].write(
                    canonical(
                        {
                            "sample_id": candidate.sample_id,
                            "messages": reader["messages"],
                        }
                    )
                    + "\n"
                )
                record = candidate.to_dict()
                record.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / f'{split}.jsonl'}:{positions[split]}",
                    native_audit_ref=f"{native_dir / 'audit.jsonl'}:{audit_row}",
                    content_proof_scope="two added-line witnesses; filename-preserving code removal; single 16K raw window only",
                    observed_witness_span_tokens=proof["evidence_token_span"],
                    output_file=f"candidate_{split}.jsonl",
                    row_index=positions[split],
                )
                index_stream.write(canonical(record) + "\n")
                positions[split] += 1
                splits[split] += 1
                lengths[candidate.length_bin] += 1
        if next(proofs, None) is not None or any(
            next(stream, None) is not None for stream in readers.values()
        ):
            raise ValueError("P99 native row inventory has trailing rows")
        if (
            ledger.rows != native["views"]
            or ledger.independent_tasks != native["semantic_tasks"]
        ):
            raise ValueError("P99 task/view count changed during normalization")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "operations": {"added_line_cross_pr_complete_set": ledger.rows},
            "native_manifest_sha256": _sha(receipt),
            "native_config_sha256": _sha(config_path),
            "native_audit_sha256": _sha(native_dir / "audit.jsonl"),
            "native_rejected_scopes": native["rejected_scopes"],
            "claim_limit": "finite identifier matching over two merged-head added-code patches; excludes no arbitrary paraphrase or multi-window retrieval",
            "files_sha256": {
                name: _sha(staging / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
            "train_ready": False,
        }
        (staging / "manifest.json").write_text(canonical(manifest) + "\n")
        if verify_only:
            verify_merge(output)
            for path in staging.iterdir():
                if (
                    not (output / path.name).is_file()
                    or (output / path.name).read_bytes() != path.read_bytes()
                ):
                    raise ValueError("P99 unified shard changed on replay")
        else:
            if output.exists():
                raise ValueError("P99 unified output already exists")
            os.rename(staging, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            convert(
                args.config, args.native_dir, args.output, verify_only=args.verify_only
            )
        )
    )


if __name__ == "__main__":
    main()
