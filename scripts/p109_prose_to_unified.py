"""Normalize blind-audited P109 official-prose/simulated-state readers."""

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
from scripts.p109_prose_audit import audit
from scripts.p109_prose_compile import QUESTION_MARKER, run
from scripts.p109_prose_support import _sha

LANE = "p109_official_rfc_numeric_rule"
EVIDENCE = "official_rfc_numeric_minimum_and_simulated_state_replayed"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def convert(
    config_path: Path, native_dir: Path, output: Path, *, verify_only: bool = False
) -> dict:
    native = run(config_path, native_dir, verify_only=True)
    checked = audit(native_dir, config_path)
    native_path = native_dir / "manifest.json"
    audit_path = native_dir / "mask_audit.json"
    if (
        checked["native_manifest_sha256"] != _sha(native_path)
        or checked["checked_readers"] != native["candidate_views"]
        or audit_path.read_text()
        != json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ):
        raise ValueError("P109 native rule/reader/mask proof differs")
    readers = {row["sample_id"]: row for row in _rows(native_dir / "train.jsonl")}
    indices = _rows(native_dir / "sample_index.jsonl")
    proofs = {row["sample_id"]: row for row in _rows(native_dir / "audit.jsonl")}
    if len(readers) != len(indices) != len(proofs) != native[
        "candidate_views"
    ] or _rows(native_dir / "eval.jsonl"):
        raise ValueError("P109 native reader/index/proof inventory differs")
    ledger = CandidateLedger()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p109-prose-unified-", dir=output.parent
    ) as raw:
        stage = Path(raw)
        positions: Counter[str] = Counter()
        lengths: Counter[str] = Counter()
        (stage / "candidate_eval.jsonl").write_text("")
        with (
            (stage / "candidate_train.jsonl").open("x") as train,
            (stage / "sample_index.jsonl").open("x") as index_stream,
        ):
            for index in indices:
                sample_id = index["sample_id"]
                reader, proof = readers[sample_id], proofs[sample_id]
                suffix = QUESTION_MARKER + proof["question"]
                user = reader["messages"][0]["content"]
                if (
                    not user.endswith(suffix)
                    or index["split"] != "train"
                    or index["source_kind"] != "grounded_simulation"
                ):
                    raise ValueError(
                        "P109 native reader/question/source boundary differs"
                    )
                clean = {"sample_id": sample_id, "messages": reader["messages"]}
                binding = AdapterBinding(
                    source_kind="grounded_simulation",
                    source_group=index["source_group"],
                    domain=index["domain"],
                    topic=index["topic"],
                    operation=index["operation"],
                    evidence_profile=EVIDENCE,
                    tokenizer_profile="pinned-chat-template",
                    receipt_path=native_path,
                    receipt_sha256=_sha(native_path),
                )
                candidate = normalize_native_candidate(
                    index, clean, binding, context_text=user[: -len(suffix)]
                )
                ledger.add(candidate)
                train.write(_dump(clean) + "\n")
                record = candidate.to_dict()
                record.update(
                    source_name=LANE,
                    native_row_ref=f"{native_dir / 'train.jsonl'}:{positions['train']}",
                    native_audit_ref=f"{native_dir / 'audit.jsonl'}:{positions['train']}",
                    output_file="candidate_train.jsonl",
                    row_index=positions["train"],
                )
                index_stream.write(_dump(record) + "\n")
                positions["train"] += 1
                lengths[candidate.length_bin] += 1
        if (
            ledger.rows != native["candidate_views"]
            or ledger.independent_tasks != native["independent_tasks"]
        ):
            raise ValueError("P109 unified task inventory differs")
        names = ("candidate_train.jsonl", "candidate_eval.jsonl", "sample_index.jsonl")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": {"train": ledger.rows},
            "length_bins": dict(sorted(lengths.items())),
            "native_manifest_sha256": _sha(native_path),
            "native_final_audit_sha256": _sha(audit_path),
            "native_source_config_sha256": _sha(config_path),
            "native_claim_limit": "two official numeric-rule sources and paired simulated states; bounded intervention only",
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
                    raise ValueError("P109 unified shard replay differs")
        else:
            if output.exists():
                raise ValueError("P109 unified output already exists")
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
