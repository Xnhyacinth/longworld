"""Convert audited P111 linked-list readers into the shared candidate schema."""

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
from scripts.p111_wiki_linked_audit import audit
from scripts.p111_wiki_linked_batch import SEP, _dump
from scripts.p111_wiki_linked_freeze import _sha

LANE = "p111_wiki_linked_entity"
EVIDENCE = "visible_list_selector_wikilink_and_linked_article_field_replayed"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def convert(
    native_dir: Path,
    target_manifest: Path,
    body_ledger: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    checked = audit(native_dir, target_manifest, body_ledger, verify_only=True)
    indices = _rows(native_dir / "sample_index.jsonl")
    readers = {
        split: iter(_rows(native_dir / f"{split}.jsonl")) for split in ("train", "eval")
    }
    ledger = CandidateLedger()
    counts = Counter()
    lengths = Counter()
    positions = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p111_unified_", dir=output.parent
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
                    raise ValueError("P111 native row order differs")
                user = reader["messages"][0]["content"]
                if user.count(SEP) != 1 or index["source_kind"] != "real_wiki":
                    raise ValueError("P111 final reader source/boundary differs")
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
            raise ValueError("P111 native reader inventory has trailing row")
        if ledger.rows != checked["checked_readers"]:
            raise ValueError("P111 unified reader count differs")
        names = ("candidate_train.jsonl", "candidate_eval.jsonl", "sample_index.jsonl")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(sorted(counts.items())),
            "length_bins": dict(sorted(lengths.items())),
            "native_manifest_sha256": _sha(native_dir / "manifest.json"),
            "native_final_audit_sha256": _sha(native_dir / "mask_audit.json"),
            "target_manifest_sha256": _sha(target_manifest),
            "body_ledger_sha256": _sha(body_ledger),
            "native_claim_limit": "five source-backed linked-article tasks from three worlds; no broad-domain or 64K claim",
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
                    raise ValueError("P111 unified byte replay differs")
        else:
            if output.exists():
                raise ValueError("P111 unified output already exists")
            os.rename(stage, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--body-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            convert(
                args.native_dir,
                args.target_manifest,
                args.body_ledger,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
