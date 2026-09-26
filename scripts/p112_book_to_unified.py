"""Bind independently audited P112 book readers to the shared candidate bank."""

from __future__ import annotations

import argparse
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
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p112_book_audit import _rows, _sha, audit
from scripts.p112_book_tasks import SEP, _dump

LANE = "p112_real_book_cross_section_speaker"
EVIDENCE = "literal_quote_speaker_and_target_statement_text_interventions_checked"


def convert(
    source_dir: Path, native_dir: Path, output_dir: Path, *, verify_only: bool = False
) -> dict:
    audited = audit(source_dir, native_dir, verify_only=True)
    indices = _rows(native_dir / "sample_index.jsonl")
    readers = {
        split: iter(_rows(native_dir / f"{split}.jsonl")) for split in ("train", "eval")
    }
    ledger = CandidateLedger()
    counts, lengths, positions = Counter(), Counter(), Counter()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p112_book_unified_", dir=output_dir.parent
    ) as temporary:
        stage = Path(temporary)
        with (
            (stage / "candidate_train.jsonl").open("x") as train,
            (stage / "candidate_eval.jsonl").open("x") as eval_stream,
            (stage / "sample_index.jsonl").open("x") as index_stream,
        ):
            streams = {"train": train, "eval": eval_stream}
            receipt = native_dir / "manifest.json"
            for index in indices:
                split = index["split"]
                reader = next(readers[split], None)
                if reader is None or reader["sample_id"] != index["sample_id"]:
                    raise ValueError("book native reader/index order differs")
                user = reader["messages"][0]["content"]
                if user.count(SEP) != 1 or index["source_kind"] != "real_book":
                    raise ValueError("book reader boundary/source kind differs")
                binding = AdapterBinding(
                    source_kind="real_book",
                    source_group=index["source_group"],
                    domain=index["domain"],
                    topic=index["topic"],
                    operation=index["operation"],
                    evidence_profile=EVIDENCE,
                    tokenizer_profile="pinned-chat-template",
                    receipt_path=receipt,
                    receipt_sha256=_sha(receipt),
                )
                candidate = normalize_native_candidate(
                    index, reader, binding, context_text=user.split(SEP, 1)[0]
                )
                ledger.add(candidate)
                streams[split].write(_dump(reader) + "\n")
                row = candidate.to_dict()
                row.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / f'{split}.jsonl'}:{positions[split]}",
                    output_file=f"candidate_{split}.jsonl",
                    row_index=positions[split],
                )
                index_stream.write(_dump(row) + "\n")
                positions[split] += 1
                counts[split] += 1
                lengths[candidate.length_bin] += 1
        if any(next(reader, None) is not None for reader in readers.values()):
            raise ValueError("book native reader has trailing rows")
        if ledger.rows != audited["checked_readers"]:
            raise ValueError("book unified/native counts differ")
        names = ("candidate_train.jsonl", "candidate_eval.jsonl", "sample_index.jsonl")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(sorted(counts.items())),
            "length_bins": dict(sorted(lengths.items())),
            "source_manifest_sha256": _sha(source_dir / "manifest.json"),
            "native_manifest_sha256": _sha(native_dir / "manifest.json"),
            "native_final_audit_sha256": _sha(native_dir / "mask_audit.json"),
            "native_claim_limit": "Literal cross-section speaker binding in three US-public-domain books; bounded interventions and final mask, no broader narrative or model-gain claim.",
            "files_sha256": {name: _sha(stage / name) for name in names},
            "train_ready": False,
        }
        (stage / "manifest.json").write_text(_dump(manifest) + "\n")
        if verify_only:
            verify_merge(output_dir)
            for path in stage.iterdir():
                if (
                    not (output_dir / path.name).is_file()
                    or (output_dir / path.name).read_bytes() != path.read_bytes()
                ):
                    raise ValueError("book unified output replay differs")
        else:
            if output_dir.exists():
                raise ValueError("book unified output already exists")
            os.rename(stage, output_dir)
    return verify_merge(output_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            convert(
                args.source_dir,
                args.native_dir,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
