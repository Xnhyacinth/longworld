"""Normalize audited P96 raw-LaTeX paper QA into a candidate-only shard."""

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
    _answer_hash,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.run_p96_paper_reference_qa import SEPARATOR, canonical, run

LANE = "p96_paper_reference"
EVIDENCE = "source_visible_raw_latex_cross_file_reference_replayed"
DEPENDENCY = "bounded_final_reader_reference_and_target_deletions"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError(f"blank native paper row: {path}")
            yield json.loads(line)


def _normalize_pair(index: dict, reader: dict, proof: dict, manifest_path: Path):
    if set(reader) != {"sample_id", "messages"}:
        raise ValueError("P96 reader contains hidden audit metadata")
    sample_id = index["sample_id"]
    if (
        reader["sample_id"] != sample_id
        or proof["sample_id"] != sample_id
        or reader["messages"][1]["content"] != proof["answer"]
    ):
        raise ValueError("P96 paper reader/index/evidence identity differs")
    user = reader["messages"][0]["content"]
    if user.count(SEPARATOR) != 1:
        raise ValueError("P96 paper question boundary ambiguous")
    context = user.split(SEPARATOR, 1)[0]
    if (
        hashlib.sha256(reader["messages"][1]["content"].encode()).hexdigest()
        != index["answer_sha256"]
    ):
        raise ValueError("P96 paper native answer hash differs")
    scoped = {
        **index,
        "answer_sha256": _answer_hash(reader["messages"][1]["content"]),
        "evidence_status": EVIDENCE,
        "dependency_status": DEPENDENCY,
        "token_measurement": "pinned-chat-template",
    }
    binding = AdapterBinding(
        source_kind="real_paper_source",
        source_group=index["source_group"],
        domain="researchlab",
        topic=index["topic"],
        operation=index["operation"],
        evidence_profile=EVIDENCE,
        tokenizer_profile="pinned-chat-template",
        receipt_path=manifest_path,
        receipt_sha256=_sha(manifest_path),
    )
    return normalize_native_candidate(
        scoped,
        reader,
        binding,
        context_text=context,
        token_counts=TokenCounts(
            input_tokens=index["input_tokens"],
            supervised_tokens=index["supervised_tokens"],
            full_chat_tokens=index["full_chat_tokens"],
        ),
    )


def convert(
    config_path: Path,
    native_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    """Replay the native compiler/audit, then emit only reader messages and index."""
    native = run(config_path, native_dir, verify_only=True)
    native_manifest = native_dir / "manifest.json"
    final_audit = native_dir / "mask_audit.json"
    checked = json.loads(final_audit.read_text())
    if (
        native.get("schema") != "longworld.p96-paper-reference-qa.v1.result"
        or native.get("train_ready") is not False
        or checked.get("schema") != "longworld.p96-paper-reference-qa.v1.final-audit"
        or checked.get("manifest_sha256") != _sha(native_manifest)
        or checked.get("checked_views") != native["candidate_views"]
        or checked.get("train_ready") is not False
    ):
        raise ValueError("P96 native manifest or final mask receipt differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p96_paper_unified_", dir=output.parent
    ) as temp:
        staging = Path(temp)
        splits = Counter()
        lengths = Counter()
        positions = Counter()
        ledger = CandidateLedger()
        native_readers = {
            split: _rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
        }
        native_audits = _rows(native_dir / "audit.jsonl")
        with (
            (staging / "candidate_train.jsonl").open("x", encoding="utf-8") as train,
            (staging / "candidate_eval.jsonl").open(
                "x", encoding="utf-8"
            ) as eval_stream,
            (staging / "sample_index.jsonl").open("x", encoding="utf-8") as out_index,
        ):
            destinations = {"train": train, "eval": eval_stream}
            for index in _rows(native_dir / "sample_index.jsonl"):
                split = index["split"]
                if split not in native_readers:
                    raise ValueError("P96 native split invalid")
                reader = next(native_readers[split], None)
                proof = next(native_audits, None)
                if reader is None or proof is None:
                    raise ValueError("P96 native reader or audit missing")
                candidate = _normalize_pair(index, reader, proof, native_manifest)
                sample_id = candidate.sample_id
                if (
                    checked["reader_sha256"].get(sample_id)
                    != hashlib.sha256(canonical(reader).encode()).hexdigest()
                ):
                    raise ValueError("P96 final reader hash differs from mask audit")
                ledger.add(candidate)
                destinations[split].write(canonical(reader) + "\n")
                record = candidate.to_dict()
                record.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / f'{split}.jsonl'}:{positions[split]}",
                    output_file=f"candidate_{split}.jsonl",
                    row_index=positions[split],
                )
                out_index.write(canonical(record) + "\n")
                positions[split] += 1
                splits[split] += 1
                lengths[candidate.length_bin] += 1
        if next(native_audits, None) is not None or any(
            next(reader, None) is not None for reader in native_readers.values()
        ):
            raise ValueError("P96 native row inventory has trailing rows")
        if (
            ledger.rows != native["candidate_views"]
            or ledger.independent_tasks != native["independent_tasks"]
            or dict(splits) != native["split_tasks"]
        ):
            raise ValueError("P96 unified task or split count differs")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "native_manifest_sha256": _sha(native_manifest),
            "native_final_audit_sha256": _sha(final_audit),
            "native_source_config_sha256": _sha(config_path),
            "native_claim_limit": "raw LaTeX source reference graph; rendered PDF and semantic alternatives unchecked",
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
                    raise ValueError("P96 unified shard changed on replay")
        else:
            if output.exists():
                raise ValueError("P96 unified output already exists")
            os.rename(staging, output)
    return verify_merge(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            convert(
                args.config,
                args.native_dir,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
