"""Normalize independently audited P105 Wiki grid readers into one shard."""

from __future__ import annotations

import argparse
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
from scripts.p105_wiki_grid_audit import audit
from scripts.p105_wiki_grid_batch import SCHEMA, run
from scripts.run_p92_generic_table_scan import _dump, _sha

LANE = "p105_wiki_grid"
EVIDENCE = "raw_wikitext_cell_to_final_reader_complete_set_replayed"


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError("blank P105 native row")
            yield json.loads(line)


def convert(
    config_path: Path, native_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    native = run(config_path, native_dir, workers=4, verify_only=True)
    checked = audit(native_dir)
    mask_path = native_dir / "mask_audit.json"
    mask_bytes = (
        json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    manifest_path = native_dir / "manifest.json"
    if (
        native.get("schema") != SCHEMA + ".result"
        or native.get("train_ready") is not False
        or checked["native_manifest_sha256"] != _sha(manifest_path)
        or checked["verified_readers"] != native["candidate_views"]
        or mask_path.read_text() != mask_bytes
    ):
        raise ValueError("native P105 source/mask proof differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p105_unified_", dir=output.parent
    ) as temporary:
        staging = Path(temporary)
        streams = {
            split: _rows(native_dir / f"{split}.jsonl") for split in ("train", "eval")
        }
        proofs = _rows(native_dir / "audit.jsonl")
        positions: Counter[str] = Counter()
        splits: Counter[str] = Counter()
        lengths: Counter[str] = Counter()
        ledger = CandidateLedger()
        with (
            (staging / "candidate_train.jsonl").open("x", encoding="utf-8") as train,
            (staging / "candidate_eval.jsonl").open(
                "x", encoding="utf-8"
            ) as eval_stream,
            (staging / "sample_index.jsonl").open(
                "x", encoding="utf-8"
            ) as index_stream,
        ):
            destinations = {"train": train, "eval": eval_stream}
            for index in _rows(native_dir / "sample_index.jsonl"):
                split = index["split"]
                if split not in streams:
                    raise ValueError("P105 split invalid")
                native_reader = next(streams[split], None)
                proof = next(proofs, None)
                if native_reader is None or proof is None:
                    raise ValueError("P105 row inventory incomplete")
                sample_id = index["sample_id"]
                if (
                    native_reader["sample_id"] != sample_id
                    or proof["sample_id"] != sample_id
                ):
                    raise ValueError("P105 reader/proof sample identity differs")
                reader = {"sample_id": sample_id, "messages": native_reader["messages"]}
                user = reader["messages"][0]["content"]
                marker = "\n\nQUESTION\n"
                if user.count(marker) != 1:
                    raise ValueError("P105 reader question boundary ambiguous")
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
                row = candidate.to_dict()
                row.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / f'{split}.jsonl'}:{positions[split]}",
                    output_file=f"candidate_{split}.jsonl",
                    row_index=positions[split],
                )
                index_stream.write(_dump(row) + "\n")
                positions[split] += 1
                splits[split] += 1
                lengths[candidate.length_bin] += 1
        if next(proofs, None) is not None or any(
            next(stream, None) is not None for stream in streams.values()
        ):
            raise ValueError("P105 native row inventory has trailing rows")
        if (
            ledger.rows != native["candidate_views"]
            or ledger.independent_tasks != native["independent_tasks"]
        ):
            raise ValueError("P105 unified task counts differ")
        files = ("candidate_train.jsonl", "candidate_eval.jsonl", "sample_index.jsonl")
        result = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "native_manifest_sha256": _sha(manifest_path),
            "native_final_audit_sha256": _sha(mask_path),
            "native_source_config_sha256": _sha(config_path),
            "native_claim_limit": "complete source-grid rows and bounded final-reader edits; prose alternatives unchecked",
            "files_sha256": {name: _sha(staging / name) for name in files},
            "train_ready": False,
        }
        (staging / "manifest.json").write_text(_dump(result) + "\n")
        if verify_only:
            verify_merge(output)
            for path in staging.iterdir():
                if (
                    not (output / path.name).is_file()
                    or (output / path.name).read_bytes() != path.read_bytes()
                ):
                    raise ValueError("P105 unified shard differs on replay")
        else:
            if output.exists():
                raise ValueError("P105 unified output already exists")
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
        _dump(
            convert(
                args.config, args.native_dir, args.output, verify_only=args.verify_only
            )
        )
    )


if __name__ == "__main__":
    main()
