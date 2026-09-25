"""Convert independently replayed P107 Wiki dependency readers to a unified shard."""

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
from scripts.p106_freeze_width_revisions import _sha
from scripts.p107_wiki_dependency_audit import audit
from scripts.p107_wiki_dependency_batch import run
from scripts.run_p92_generic_table_scan import _dump

LANE = "p107_wiki_remote_selector"
EVIDENCE = "two_document_remote_selector_to_complete_table_scan_replayed"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def convert(
    config_path: Path, native_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    native = run(config_path, native_dir, verify_only=True)
    checked = audit(native_dir, config_path)
    audit_path = native_dir / "mask_audit.json"
    if (
        checked["native_manifest_sha256"] != _sha(native_dir / "manifest.json")
        or checked["checked_readers"] != native["candidate_views"]
        or audit_path.read_text()
        != json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ):
        raise ValueError("P107 native reader audit differs")
    readers = {
        split: iter(_rows(native_dir / f"{split}.jsonl")) for split in ("train", "eval")
    }
    indices = _rows(native_dir / "sample_index.jsonl")
    proofs = iter(_rows(native_dir / "audit.jsonl"))
    ledger = CandidateLedger()
    positions: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    lengths: Counter[str] = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p107_unified_", dir=output.parent) as raw:
        stage = Path(raw)
        with (
            (stage / "candidate_train.jsonl").open("x") as train,
            (stage / "candidate_eval.jsonl").open("x") as eval_stream,
            (stage / "sample_index.jsonl").open("x") as index_stream,
        ):
            streams = {"train": train, "eval": eval_stream}
            receipt = native_dir / "manifest.json"
            for index in indices:
                split = index["split"]
                if split not in readers:
                    raise ValueError("P107 invalid native split")
                reader = next(readers[split], None)
                proof = next(proofs, None)
                if (
                    reader is None
                    or proof is None
                    or reader["sample_id"] != index["sample_id"]
                    or proof["sample_id"] != index["sample_id"]
                ):
                    raise ValueError("P107 native row order or identity differs")
                sample_id = index["sample_id"]
                messages = reader["messages"]
                user = messages[0]["content"]
                marker = "\n\nQUESTION\n"
                if user.count(marker) != 1 or index["source_kind"] != "real_wiki":
                    raise ValueError("P107 reader boundary/source kind differs")
                binding = AdapterBinding(
                    source_kind="real_wiki",
                    source_group=index["source_group"],
                    domain=index["domain"],
                    topic=index["topic"],
                    operation=index["operation"],
                    evidence_profile=EVIDENCE,
                    tokenizer_profile="pinned-chat-template",
                    receipt_path=receipt,
                    receipt_sha256=_sha(receipt),
                )
                clean_reader = {"sample_id": sample_id, "messages": messages}
                candidate = normalize_native_candidate(
                    index, clean_reader, binding, context_text=user.split(marker, 1)[0]
                )
                ledger.add(candidate)
                streams[split].write(_dump(clean_reader) + "\n")
                record = candidate.to_dict()
                record.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / f'{split}.jsonl'}:{positions[split]}",
                    output_file=f"candidate_{split}.jsonl",
                    row_index=positions[split],
                )
                index_stream.write(_dump(record) + "\n")
                positions[split] += 1
                splits[split] += 1
                lengths[candidate.length_bin] += 1
        if next(proofs, None) is not None or any(
            next(rows, None) is not None for rows in readers.values()
        ):
            raise ValueError("P107 native row inventory has trailing rows")
        if (
            ledger.rows != native["candidate_views"]
            or ledger.independent_tasks != native["independent_tasks"]
        ):
            raise ValueError("P107 unified task counts differ")
        names = ("candidate_train.jsonl", "candidate_eval.jsonl", "sample_index.jsonl")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "native_manifest_sha256": _sha(native_dir / "manifest.json"),
            "native_final_audit_sha256": _sha(audit_path),
            "native_source_config_sha256": _sha(config_path),
            "native_claim_limit": "two eval-only source-backed selector programs; alternate prose support beyond same-line check unproven",
            "files_sha256": {name: _sha(stage / name) for name in names},
            "train_ready": False,
        }
        (stage / "manifest.json").write_text(_dump(manifest) + "\n")
        if verify_only:
            verify_merge(output)
            for path in stage.iterdir():
                if (
                    not (output / path.name).is_file()
                    or (output / path.name).read_bytes() != path.read_bytes()
                ):
                    raise ValueError("P107 unified shard differs on replay")
        else:
            if output.exists():
                raise ValueError("P107 unified output already exists")
            os.rename(stage, output)
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
