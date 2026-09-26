"""Export final-audited report route readers to the shared candidate contract."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import CandidateLedger
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p95_report_finance_shared import canonical
from scripts.p95_report_finance_to_unified import _normalize_pair
from scripts.p112_report_audit import audit
from scripts.p112_report_route import _rows
from scripts.run_p95_report_finance_shared import sha

LANE = "p112_report_period_delta"


def convert(
    config: Path,
    native: Path,
    final_audit: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    checked = audit(config, native, final_audit, verify_only=True)
    batch = json.loads((native / "batch_manifest.json").read_text())
    if checked["mask"]["views"] != batch["views"]:
        raise ValueError("P112 audit does not cover all native readers")
    if not verify_only and output.exists():
        raise ValueError("unified output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p112_report_unified_", dir=output.parent
    ) as temp:
        staging = Path(temp)
        ledger = CandidateLedger()
        splits, lengths, operations, positions = (
            Counter(),
            Counter(),
            Counter(),
            Counter(),
        )
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
            for job in batch["jobs"]:
                folder = native / job["issuer"]
                manifest_path = folder / "manifest.json"
                manifest_sha = sha(manifest_path)
                if manifest_sha != job["manifest_sha256"]:
                    raise ValueError("P112 issuer receipt drift")
                for row_number, (reader, index) in enumerate(
                    zip(
                        _rows(folder / "reader.jsonl"),
                        _rows(folder / "sample_index.jsonl"),
                        strict=True,
                    )
                ):
                    candidate = _normalize_pair(
                        index, reader, manifest_path, manifest_sha
                    )
                    ledger.add(candidate)
                    split = candidate.split
                    destinations[split].write(
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
                        native_row_ref=f"{folder / 'reader.jsonl'}:{row_number}",
                        output_file=f"candidate_{split}.jsonl",
                        row_index=positions[split],
                    )
                    index_stream.write(canonical(record) + "\n")
                    positions[split] += 1
                    splits[split] += 1
                    lengths[candidate.length_bin] += 1
                    operations[candidate.operation] += 1
        if (
            ledger.rows != batch["views"]
            or ledger.independent_tasks != batch["semantic_tasks"]
        ):
            raise ValueError("P112 unified ledger cardinality mismatch")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(sorted(splits.items())),
            "length_bins": dict(sorted(lengths.items())),
            "operations": dict(sorted(operations.items())),
            "native_batch_manifest_sha256": sha(native / "batch_manifest.json"),
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
            for source in staging.iterdir():
                if (
                    not (output / source.name).is_file()
                    or source.read_bytes() != (output / source.name).read_bytes()
                ):
                    raise ValueError(f"P112 unified replay drift: {source.name}")
        else:
            staging.rename(output)
            verify_merge(output)
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = convert(
        args.config,
        args.native,
        args.final_audit,
        args.output,
        verify_only=args.verify_only,
    )
    print(
        canonical({key: value for key, value in result.items() if key != "operations"})
    )


if __name__ == "__main__":
    main()
