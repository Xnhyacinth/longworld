"""Independent all-row final-chat and assistant-mask audit for P112."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.run_shared_record_taskbank import _tokenizer

SCHEMA = "longworld.p112-world-factor-mask.v1"


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(candidate_dir: Path, output: Path, *, verify_only: bool = False) -> dict:
    source = verify_merge(candidate_dir)
    if source.get("campaign_schema") != "longworld.p112-world-factor-campaign.v1":
        raise ValueError("not a P112 factorized candidate shard")
    if source["proofs_sha256"] != sha(candidate_dir / "proofs.jsonl"):
        raise ValueError("source proof ledger changed")
    proofs = {
        row["sample_id"]: row
        for row in (
            json.loads(line)
            for line in (candidate_dir / "proofs.jsonl").read_text().splitlines()
        )
    }
    tokenizer = _tokenizer()
    positions = Counter()
    rows = []
    operations = Counter()
    lengths = Counter()
    supervised = 0
    max_lineage = 0
    long_lineage = 0
    with (
        (candidate_dir / "candidate_train.jsonl").open() as train,
        (candidate_dir / "candidate_eval.jsonl").open() as eval_stream,
        (candidate_dir / "sample_index.jsonl").open() as index_stream,
    ):
        streams = {"train": train, "eval": eval_stream}
        for line in index_stream:
            index = json.loads(line)
            split = index["split"]
            if (
                index["row_index"] != positions[split]
                or index["output_file"] != f"candidate_{split}.jsonl"
            ):
                raise ValueError("reader index position differs")
            reader_line = streams[split].readline()
            if not reader_line:
                raise ValueError("reader missing from indexed split")
            reader = json.loads(reader_line)
            checked = audit_reader(reader, index, tokenizer, 262144)
            proof = proofs.pop(index["sample_id"])
            span = proof["bounded_lineage_token_span"]
            evidence_end = (
                index["input_tokens"] - proof["last_consumed_fact_to_input_end_tokens"]
            )
            evidence_start = evidence_end - span
            if not 0 <= evidence_start < evidence_end <= index["input_tokens"]:
                raise ValueError("observed lineage crosses prompt or mask")
            checked.update(
                observed_lineage_token_span=[evidence_start, evidence_end],
                lineage_status="bounded_consumed_fact_envelope_not_shortest_proof",
            )
            rows.append(checked)
            positions[split] += 1
            operations[index["operation"]] += 1
            lengths[index["length_bin"]] += 1
            supervised += index["supervised_tokens"]
            max_lineage = max(max_lineage, span)
            long_lineage += span >= 16384
        if any(stream.readline() for stream in streams.values()):
            raise ValueError("unindexed reader remains")
    if proofs or len(rows) != source["candidate_views"]:
        raise ValueError("proof or reader inventory differs")
    lines = "".join(dump(row) + "\n" for row in rows)
    if not verify_only:
        if output.exists():
            raise ValueError("mask audit output already exists")
        output.mkdir(parents=True)
        (output / "rows.jsonl").write_text(lines)
    elif (output / "rows.jsonl").read_text() != lines:
        raise ValueError("frozen mask audit rows differ")
    manifest = {
        "schema": SCHEMA,
        "source_manifest_sha256": sha(candidate_dir / "manifest.json"),
        "source_proofs_sha256": source["proofs_sha256"],
        "checked_rows": len(rows),
        "split_rows": dict(sorted(positions.items())),
        "operations": dict(sorted(operations.items())),
        "physical_length_bins": dict(sorted(lengths.items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in rows),
        "supervised_tokens": supervised,
        "long_bounded_lineage_rows_16k": long_lineage,
        "max_bounded_lineage_span_tokens": max_lineage,
        "rows_sha256": sha(output / "rows.jsonl"),
        "train_ready": False,
    }
    content = dump(manifest) + "\n"
    if verify_only:
        if (output / "manifest.json").read_text() != content:
            raise ValueError("frozen mask audit manifest differs")
    else:
        (output / "manifest.json").write_text(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(dump(audit(args.candidates, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
