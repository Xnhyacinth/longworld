"""Normalize audited P100 categorical scans into a candidate-only shard."""

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
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p100_wiki_categorical_audit import AUDIT_SCHEMA, audit
from scripts.p100_wiki_categorical_scan import SCHEMA, _dump, _sha, run

LANE = "p100_wiki_category"
EVIDENCE = "complete_visible_categorical_table_rows_replayed"


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError("blank native categorical row")
            yield json.loads(line)


def convert(
    config_path: Path, native_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    native = run(config_path, native_dir, verify_only=True)
    checked = audit(native_dir)
    manifest_path = native_dir / "manifest.json"
    if (
        native.get("schema") != SCHEMA + ".result"
        or checked.get("schema") != AUDIT_SCHEMA
        or checked.get("manifest_sha256") != _sha(manifest_path)
        or checked.get("checked_views") != native["candidate_views"]
        or native.get("train_ready") is not False
        or checked.get("train_ready") is not False
    ):
        raise ValueError("native categorical proof or mask receipt differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p100_wiki_unified_", dir=output.parent
    ) as temp:
        staging = Path(temp)
        streams = {
            split: _rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
        }
        proof_rows = _rows(native_dir / "audit.jsonl")
        splits: Counter[str] = Counter()
        lengths: Counter[str] = Counter()
        positions: Counter[str] = Counter()
        ledger = CandidateLedger()
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
                if split not in streams:
                    raise ValueError("native categorical split invalid")
                raw_reader = next(streams[split], None)
                proof = next(proof_rows, None)
                if raw_reader is None or proof is None:
                    raise ValueError("native categorical row inventory incomplete")
                sample_id = index["sample_id"]
                reader = {"sample_id": sample_id, "messages": raw_reader["messages"]}
                if (
                    raw_reader["sample_id"] != sample_id
                    or proof["sample_id"] != sample_id
                    or checked["reader_sha256"].get(sample_id)
                    != hashlib.sha256(_dump(reader).encode()).hexdigest()
                ):
                    raise ValueError("native categorical reader/audit identity differs")
                user = reader["messages"][0]["content"]
                marker = "\n\nQUESTION\n"
                if user.count(marker) != 1:
                    raise ValueError("categorical reader question boundary ambiguous")
                binding = AdapterBinding(
                    source_kind="real_wiki",
                    source_group=index["source_group"],
                    domain=index["domain"],
                    topic=index["topic"],
                    operation=index["operation"],
                    evidence_profile=EVIDENCE,
                    tokenizer_profile="pinned-chat-template",
                    receipt_path=manifest_path,
                    receipt_sha256=_sha(manifest_path),
                )
                candidate = normalize_native_candidate(
                    index, reader, binding, context_text=user.split(marker, 1)[0]
                )
                ledger.add(candidate)
                destinations[split].write(_dump(reader) + "\n")
                record = candidate.to_dict()
                record.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / f'{split}.jsonl'}:{positions[split]}",
                    output_file=f"candidate_{split}.jsonl",
                    row_index=positions[split],
                )
                out_index.write(_dump(record) + "\n")
                positions[split] += 1
                splits[split] += 1
                lengths[candidate.length_bin] += 1
        if next(proof_rows, None) is not None or any(
            next(stream, None) is not None for stream in streams.values()
        ):
            raise ValueError("native categorical row inventory has trailing rows")
        if (
            ledger.rows != native["candidate_views"]
            or ledger.independent_tasks != native["independent_tasks"]
            or dict(splits) != native["split_views"]
        ):
            raise ValueError("unified categorical counts differ")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "native_manifest_sha256": _sha(manifest_path),
            "native_final_audit_sha256": _sha(native_dir / "mask_audit.json"),
            "native_source_config_sha256": _sha(config_path),
            "native_claim_limit": "complete contiguous table rows and bounded category-cell edits; prose alternatives and unparsed tables unchecked",
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
        (staging / "manifest.json").write_text(_dump(manifest) + "\n")
        if verify_only:
            verify_merge(output)
            for path in staging.iterdir():
                if (
                    not (output / path.name).is_file()
                    or (output / path.name).read_bytes() != path.read_bytes()
                ):
                    raise ValueError("unified categorical shard changed on replay")
        else:
            if output.exists():
                raise ValueError("unified categorical output already exists")
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
        _dump(
            convert(
                args.config, args.native_dir, args.output, verify_only=args.verify_only
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
