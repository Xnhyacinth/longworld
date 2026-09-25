"""Bounded visible-cell selector interventions for frozen P95 report readers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_p95_report_finance_shared import _rows, audit, replay_answer
from scripts.p95_report_finance_shared import canonical
from scripts.run_p95_report_finance_shared import SEPARATOR, sha

SCHEMA = "longworld.p95-report-visible-selector-intervention.v1"
SELECTOR_ROLES = {
    "middle_margin_then_cash": {"revenue", "operating_income"},
    "largest_cash_jump_then_revenue": {"cash_from_operations"},
    "max_revenue_growth_then_cash": {"revenue"},
    "largest_margin_swing_then_cash": {"revenue", "operating_income"},
}


def _replacement_quotes(quote: str, *, allow_signed: bool = False):
    positions = [i for i, char in enumerate(quote) if char.isdigit()]
    if not positions:
        return
    size = len(positions)
    for digits in (
        "9" * size,
        "1" + "0" * (size - 1),
        "2" + "0" * (size - 1),
        "5" * size,
        "8" * size,
        "1" * size,
        "3" * size,
        "7" * size,
    ):
        chars = list(quote)
        for index, digit in zip(positions, digits, strict=True):
            chars[index] = digit
        changed = "".join(chars)
        if changed != quote:
            yield changed
    # An operating-income loss is a legitimate statement value. Parentheses
    # preserve cell width here while changing the sign and selector outcome.
    if allow_signed and quote.replace(",", "").isdigit() and len(quote) >= 4:
        yield "(" + "9" * (len(quote) - 2) + ")"


def _target_period(answer: dict) -> str:
    return answer.get("fiscal_year") or answer["transition"]


def _target_value(answer: dict) -> int:
    values = [value for value in answer.values() if type(value) is int]
    if len(values) != 1:
        raise ValueError("report answer lacks one numeric target")
    return values[0]


def _intervene(
    context: str, evidence: list[dict], operation: str, baseline: dict
) -> dict | None:
    for item in sorted(evidence, key=lambda row: (row["record_id"], row["role"])):
        if item["role"] not in SELECTOR_ROLES[operation]:
            continue
        start, end = item["context_span"]
        if context[start:end] != item["quote"]:
            raise ValueError("selector quote disagrees with final context")
        for replacement in _replacement_quotes(
            item["quote"], allow_signed=item["role"] == "operating_income"
        ):
            changed = context[:start] + replacement + context[end:]
            if len(changed) != len(context):
                raise ValueError("intervention changed reader length")
            supports = [
                {**row, "quote": replacement} if row is item else row
                for row in evidence
            ]
            try:
                answer = replay_answer(changed, supports, operation)
            except ValueError:
                continue
            if _target_period(answer) != _target_period(baseline) and _target_value(
                answer
            ) != _target_value(baseline):
                return {
                    "selector_record_id": item["record_id"],
                    "selector_role": item["role"],
                    "reader_char_span": [start, end],
                    "old_quote": item["quote"],
                    "new_quote": replacement,
                    "old_answer": baseline,
                    "intervened_answer": answer,
                    "scope": "equal-character-length synthetic edit of one final-visible numeric cell",
                }
    return None


def run(
    catalog_path: Path,
    batch_dir: Path,
    final_audit: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    verified = audit(catalog_path, batch_dir, final_audit, verify_only=True)
    accepted, rejected = [], []
    for source in verified["sources"]:
        native = batch_dir / source["issuer"]
        readers = _rows(native / "reader.jsonl")
        indices = _rows(native / "sample_index.jsonl")
        audits = _rows(native / "audit.jsonl")
        grouped = defaultdict(list)
        for reader, index, proof in zip(readers, indices, audits, strict=True):
            grouped[index["variant"]].append((reader, index, proof))
        for variant, rows in sorted(grouped.items()):
            contexts = {
                index["context_sha256"]: reader["messages"][0]["content"].rsplit(
                    SEPARATOR, 1
                )[0]
                for reader, index, _ in rows
            }
            if len(contexts) != 1:
                raise ValueError(
                    "same issuer/view tasks do not share one reader context"
                )
            context = next(iter(contexts.values()))
            union = {}
            for _, _, proof in rows:
                for item in proof["evidence"]:
                    key = item["record_id"], item["role"]
                    previous = union.get(key)
                    if previous is not None and (
                        previous["context_span"] != item["context_span"]
                        or previous["quote"] != item["quote"]
                    ):
                        raise ValueError("shared visible fact has inconsistent spans")
                    union[key] = item
            evidence = list(union.values())
            for reader, index, _ in rows:
                baseline = json.loads(reader["messages"][1]["content"])
                operation = index["operation"]
                if replay_answer(context, evidence, operation) != baseline:
                    raise ValueError(
                        "shared visible cells do not reproduce original answer"
                    )
                intervention = _intervene(context, evidence, operation, baseline)
                row = {
                    "sample_id": index["sample_id"],
                    "semantic_task_id": index["semantic_task_id"],
                    "issuer": source["issuer"],
                    "variant": variant,
                    "operation": operation,
                }
                if intervention is None:
                    rejected.append(
                        {
                            **row,
                            "reason": "bounded_same_length_selector_search_found_no_target_change",
                        }
                    )
                else:
                    accepted.append({**row, **intervention})
    result = {
        "schema": SCHEMA,
        "catalog_sha256": sha(catalog_path),
        "batch_manifest_sha256": sha(batch_dir / "batch_manifest.json"),
        "final_audit_sha256": sha(final_audit),
        "attempted_views": verified["mask"]["views"],
        "accepted_interventions": len(accepted),
        "rejected_interventions": len(rejected),
        "accepted": sorted(accepted, key=lambda row: row["sample_id"]),
        "rejected": sorted(rejected, key=lambda row: row["sample_id"]),
        "interpretation": "bounded synthetic selector sensitivity only; not exhaustive visible-text necessity",
        "train_ready": False,
    }
    payload = canonical(result) + "\n"
    receipt_path = output.with_suffix(".receipt.json")
    code = {
        name: sha(ROOT / name)
        for name in (
            "scripts/audit_p95_report_selector_interventions.py",
            "scripts/audit_p95_report_finance_shared.py",
            "scripts/p95_report_finance_shared.py",
        )
    }
    receipt = {
        "schema": SCHEMA + ".receipt",
        "sidecar_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "batch_manifest_sha256": result["batch_manifest_sha256"],
        "final_audit_sha256": result["final_audit_sha256"],
        "catalog_sha256": result["catalog_sha256"],
        "auditor_code_sha256": code,
        "attempted_views": result["attempted_views"],
        "accepted_interventions": result["accepted_interventions"],
        "train_ready": False,
    }
    receipt_payload = canonical(receipt) + "\n"
    if verify_only:
        if (
            not output.is_file()
            or output.read_text() != payload
            or not receipt_path.is_file()
            or receipt_path.read_text() != receipt_payload
        ):
            raise ValueError("selector intervention audit drift")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
        with receipt_path.open("x", encoding="utf-8") as stream:
            stream.write(receipt_payload)
    return {
        key: value
        for key, value in result.items()
        if key not in {"accepted", "rejected"}
    }


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
            run(
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
