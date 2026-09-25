"""Normalize independently audited P95 report readers into a unified shard."""

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
    TokenCounts,
    normalize_native_candidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_p95_report_finance_shared import audit
from scripts.p95_report_finance_shared import canonical
from scripts.run_p95_report_finance_shared import SEPARATOR, sha

LANE = "p95_report_finance"


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _normalize_pair(index: dict, reader: dict, native_manifest: Path, native_sha: str):
    if set(reader) != {"sample_id", "messages"}:
        raise ValueError("P95 reader has hidden metadata fields")
    user = reader["messages"][0]["content"]
    if user.count(SEPARATOR) != 1:
        raise ValueError("P95 reader context boundary changed")
    context = user.split(SEPARATOR, 1)[0]
    scoped = {
        **index,
        "evidence_status": "native_visible_report_cells_replayed",
        "token_measurement": "pinned-chat-template",
    }
    binding = AdapterBinding(
        source_kind="real_finance",
        source_group=index["source_group"],
        domain="finance",
        topic=index["topic"],
        operation=index["operation"],
        evidence_profile="native_visible_report_cells_replayed",
        tokenizer_profile="pinned-chat-template",
        receipt_path=native_manifest,
        receipt_sha256=native_sha,
    )
    counts = TokenCounts(
        input_tokens=index["input_tokens"],
        supervised_tokens=index["supervised_tokens"],
        full_chat_tokens=index["full_chat_tokens"],
    )
    return normalize_native_candidate(
        scoped, reader, binding, context_text=context, token_counts=counts
    )


def convert(
    catalog_path: Path,
    batch_dir: Path,
    final_audit: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    audited = audit(catalog_path, batch_dir, final_audit, verify_only=True)
    batch = json.loads((batch_dir / "batch_manifest.json").read_text())
    if (
        audited["batch_manifest_sha256"] != sha(batch_dir / "batch_manifest.json")
        or audited["mask"]["views"] != batch["views"]
        or audited["mask"]["visible_answer_replays"] != batch["views"]
    ):
        raise ValueError("P95 final audit and native batch disagree")
    receipts = {row["issuer"]: row for row in batch["jobs"]}
    catalog = json.loads(catalog_path.read_text())
    if len(receipts) != len(catalog["jobs"]):
        raise ValueError("P95 batch/catalog issuer count changed")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p95_report_unified_", dir=output.parent
    ) as temp:
        staging = Path(temp)
        positions = Counter()
        splits = Counter()
        lengths = Counter()
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
            for job in catalog["jobs"]:
                issuer = job["issuer"]
                native = batch_dir / issuer
                manifest_path = native / "manifest.json"
                manifest_sha = sha(manifest_path)
                if receipts[issuer]["manifest_sha256"] != manifest_sha:
                    raise ValueError("P95 issuer manifest differs from batch receipt")
                for row_number, (index, reader) in enumerate(
                    zip(
                        _rows(native / "sample_index.jsonl"),
                        _rows(native / "reader.jsonl"),
                        strict=True,
                    )
                ):
                    candidate = _normalize_pair(
                        index, reader, manifest_path, manifest_sha
                    )
                    ledger.add(candidate)
                    split = candidate.split
                    sample = {
                        "sample_id": candidate.sample_id,
                        "messages": reader["messages"],
                    }
                    destinations[split].write(canonical(sample) + "\n")
                    record = candidate.to_dict()
                    record.update(
                        source_name=LANE,
                        native_row_ref=f"{native / 'reader.jsonl'}:{row_number}",
                        output_file=f"candidate_{split}.jsonl",
                        row_index=positions[split],
                    )
                    index_stream.write(canonical(record) + "\n")
                    positions[split] += 1
                    splits[split] += 1
                    lengths[candidate.length_bin] += 1
        if (
            ledger.rows != audited["mask"]["views"]
            or ledger.independent_tasks != audited["semantic_tasks"]
        ):
            raise ValueError("P95 normalized task count changed")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "native_batch_manifest_sha256": sha(batch_dir / "batch_manifest.json"),
            "native_final_audit_sha256": sha(final_audit),
            "files_sha256": {
                name: sha(staging / name)
                for name in (
                    "candidate_train.jsonl",
                    "candidate_eval.jsonl",
                    "sample_index.jsonl",
                )
            },
            "train_ready": False,
        }
        (staging / "manifest.json").write_text(
            canonical(manifest) + "\n", encoding="utf-8"
        )
        if verify_only:
            verify_merge(output)
            for path in staging.iterdir():
                if (
                    not (output / path.name).is_file()
                    or (output / path.name).read_bytes() != path.read_bytes()
                ):
                    raise ValueError("P95 unified shard changed on replay")
        else:
            if output.exists():
                raise ValueError("P95 unified shard output already exists")
            os.rename(staging, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            convert(
                args.catalog,
                args.batch_dir,
                args.final_audit,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
