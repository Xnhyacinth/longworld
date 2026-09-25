"""Convert audited P96 finance readers into one CF-filtered unified shard."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import CandidateLedger
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_p96_finance_factorial import _rows, audit
from scripts.p95_report_finance_shared import canonical
from scripts.p95_report_finance_to_unified import _normalize_pair
from scripts.p96_finance_factorial import SCHEMA
from scripts.run_p96_finance_factorial import sha

LANE = "p96_finance_factorial"
INTERVENTION_CODE = (
    "scripts/audit_p96_finance_interventions.py",
    "scripts/audit_p96_finance_factorial.py",
    "scripts/p96_finance_factorial.py",
)


def _sidecar(
    batch_dir: Path, final_audit: Path, sidecar: Path
) -> tuple[set[str], dict[str, int]]:
    receipt_path = sidecar.with_suffix(".receipt.json")
    receipt = json.loads(receipt_path.read_text())
    result = json.loads(sidecar.read_text())
    if (
        receipt.get("schema") != SCHEMA + ".visible-selector-intervention.receipt"
        or result.get("schema") != SCHEMA + ".visible-selector-intervention"
        or receipt["sidecar_sha256"] != sha(sidecar)
        or receipt["batch_manifest_sha256"] != sha(batch_dir / "batch_manifest.json")
        or receipt["final_audit_sha256"] != sha(final_audit)
        or receipt["accepted_interventions"] != len(result["accepted"])
        or receipt["auditor_code_sha256"]
        != {name: sha(ROOT / name) for name in INTERVENTION_CODE}
    ):
        raise ValueError("P96 selector intervention sidecar receipt drift")
    accepted = {row["sample_id"] for row in result["accepted"]}
    rejected = {row["sample_id"] for row in result["rejected"]}
    if (
        len(accepted) != len(result["accepted"])
        or len(rejected) != len(result["rejected"])
        or accepted & rejected
        or len(accepted) + len(rejected) != result["attempted_views"]
    ):
        raise ValueError("P96 selector intervention sample IDs inconsistent")
    return accepted, dict(
        sorted(Counter(row["reason"] for row in result["rejected"]).items())
    )


def convert(
    config_path: Path,
    batch_dir: Path,
    final_audit: Path,
    sidecar: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    audited = audit(config_path, batch_dir, final_audit, verify_only=True)
    accepted, rejection_reasons = _sidecar(batch_dir, final_audit, sidecar)
    if len(accepted) + sum(rejection_reasons.values()) != audited["mask"]["views"]:
        raise ValueError("P96 selector sidecar does not cover final reader views")
    config = json.loads(config_path.read_text())
    catalog = json.loads((ROOT / config["catalog"]["path"]).read_text())
    by_task: dict[str, set[str]] = defaultdict(set)
    for job in catalog["jobs"]:
        for index in _rows(batch_dir / job["issuer"] / "sample_index.jsonl"):
            by_task[index["semantic_task_id"]].add(index["sample_id"])
    eligible = {
        identity
        for identity, samples in by_task.items()
        if len(samples) == 2 and samples <= accepted
    }
    if not eligible:
        raise ValueError("P96 selector audit admitted no complete semantic tasks")
    batch = json.loads((batch_dir / "batch_manifest.json").read_text())
    receipts = {row["issuer"]: row for row in batch["jobs"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p96_finance_unified_", dir=output.parent
    ) as temp:
        staging = Path(temp)
        ledger = CandidateLedger()
        positions, splits, lengths, operations = (
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
            for job in catalog["jobs"]:
                issuer = job["issuer"]
                native = batch_dir / issuer
                receipt_path = native / "manifest.json"
                receipt_sha = sha(receipt_path)
                if receipts[issuer]["manifest_sha256"] != receipt_sha:
                    raise ValueError("P96 issuer manifest changed")
                for row_number, (index, reader) in enumerate(
                    zip(
                        _rows(native / "sample_index.jsonl"),
                        _rows(native / "reader.jsonl"),
                        strict=True,
                    )
                ):
                    if index["semantic_task_id"] not in eligible:
                        continue
                    candidate = _normalize_pair(
                        index, reader, receipt_path, receipt_sha
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
                        native_row_ref=f"{native / 'reader.jsonl'}:{row_number}",
                        output_file=f"candidate_{split}.jsonl",
                        row_index=positions[split],
                    )
                    index_stream.write(canonical(record) + "\n")
                    positions[split] += 1
                    splits[split] += 1
                    lengths[candidate.length_bin] += 1
                    operations[candidate.operation] += 1
        if (
            ledger.independent_tasks != len(eligible)
            or ledger.rows != len(eligible) * 2
        ):
            raise ValueError("P96 CF-selected task/view count changed")
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": ledger.rows,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "views_by_lane": {LANE: ledger.rows},
            "splits": dict(splits),
            "length_bins": dict(sorted(lengths.items())),
            "operations": dict(sorted(operations.items())),
            "native_batch_manifest_sha256": sha(batch_dir / "batch_manifest.json"),
            "native_final_audit_sha256": sha(final_audit),
            "selector_intervention_sha256": sha(sidecar),
            "selector_intervention_receipt_sha256": sha(
                sidecar.with_suffix(".receipt.json")
            ),
            "audited_semantic_tasks": audited["semantic_tasks"],
            "excluded_by_selector_intervention": audited["semantic_tasks"]
            - len(eligible),
            "selector_intervention_rejection_reasons": rejection_reasons,
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
                    raise ValueError("P96 unified shard changed on replay")
        else:
            if output.exists():
                raise ValueError("P96 unified shard already exists")
            os.rename(staging, output)
    return verify_merge(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument("--selector-sidecar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            convert(
                args.config,
                args.batch_dir,
                args.final_audit,
                args.selector_sidecar,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
