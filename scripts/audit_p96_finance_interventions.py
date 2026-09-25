"""Bounded final-visible selector edits for declarative P96 finance programs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_p95_report_selector_interventions import _replacement_quotes
from scripts.audit_p96_finance_factorial import _rows, audit, visible_replay
from scripts.p95_report_finance_shared import canonical
from scripts.p96_finance_factorial import SCHEMA
from scripts.run_p95_report_finance_shared import SEPARATOR, sha

INTERVENTION_SCHEMA = SCHEMA + ".visible-selector-intervention"


def _target_value(answer: dict) -> int:
    value = answer.get(
        "value_usd_millions", answer.get("later_year_value_usd_millions")
    )
    if type(value) is not int:
        raise ValueError("intervention target answer is not one integer")
    return value


def intervene(
    context: str, evidence: list[dict], program: dict, baseline: dict, selected: str
) -> dict | None:
    roles = set(program["selector"]["metrics"])
    for item in sorted(evidence, key=lambda row: (row["record_id"], row["role"])):
        if item["role"] not in roles:
            continue
        start, end = item["context_span"]
        if context[start:end] != item["quote"]:
            raise ValueError("visible selector quote changed before intervention")
        for replacement in _replacement_quotes(
            item["quote"], allow_signed=item["role"] != "revenue"
        ):
            changed = context[:start] + replacement + context[end:]
            if len(changed) != len(context):
                raise ValueError("synthetic edit changed reader character length")
            support = [
                {**row, "quote": replacement} if row is item else row
                for row in evidence
            ]
            try:
                answer, next_record = visible_replay(changed, support, program)
            except ValueError:
                continue
            if next_record != selected and _target_value(answer) != _target_value(
                baseline
            ):
                return {
                    "selector_record_id": item["record_id"],
                    "selector_role": item["role"],
                    "reader_char_span": [start, end],
                    "old_quote": item["quote"],
                    "new_quote": replacement,
                    "old_selected_record": selected,
                    "new_selected_record": next_record,
                    "old_answer": baseline,
                    "intervened_answer": answer,
                }
    return None


def run(
    config_path: Path,
    batch_dir: Path,
    final_audit: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    verified = audit(config_path, batch_dir, final_audit, verify_only=True)
    accepted, rejected = [], []
    by_source = Counter()
    for source in verified["sources"]:
        native = batch_dir / source["issuer"]
        grouped = defaultdict(list)
        for reader, index, proof in zip(
            _rows(native / "reader.jsonl"),
            _rows(native / "sample_index.jsonl"),
            _rows(native / "audit.jsonl"),
            strict=True,
        ):
            grouped[index["variant"]].append((reader, index, proof))
        for variant, rows in sorted(grouped.items()):
            contexts = {
                index["context_sha256"]: reader["messages"][0]["content"].rsplit(
                    SEPARATOR, 1
                )[0]
                for reader, index, _ in rows
            }
            if len(contexts) != 1:
                raise ValueError("same issuer/variant tasks do not share context")
            context = next(iter(contexts.values()))
            union = {}
            for _, _, proof in rows:
                for item in proof["evidence"]:
                    key = item["record_id"], item["role"]
                    prior = union.get(key)
                    if prior is not None and (
                        prior["context_span"] != item["context_span"]
                        or prior["quote"] != item["quote"]
                    ):
                        raise ValueError(
                            "same source cell has inconsistent visible spans"
                        )
                    union[key] = item
            evidence = list(union.values())
            for reader, index, proof in rows:
                program = proof["program"]
                baseline = json.loads(reader["messages"][1]["content"])
                replayed, selected = visible_replay(context, evidence, program)
                if (
                    replayed != baseline
                    or selected != proof["trace"]["selected_record_id"]
                ):
                    raise ValueError("shared visible cells do not reproduce baseline")
                result = intervene(context, evidence, program, baseline, selected)
                header = {
                    "sample_id": index["sample_id"],
                    "semantic_task_id": index["semantic_task_id"],
                    "issuer": source["issuer"],
                    "variant": variant,
                    "operation": index["operation"],
                }
                if result is None:
                    rejected.append(
                        {
                            **header,
                            "reason": "bounded_equal_length_search_found_no_new_target",
                        }
                    )
                else:
                    accepted.append({**header, **result})
                    by_source[source["issuer"]] += 1
    result = {
        "schema": INTERVENTION_SCHEMA,
        "config_sha256": sha(config_path),
        "batch_manifest_sha256": sha(batch_dir / "batch_manifest.json"),
        "final_audit_sha256": sha(final_audit),
        "attempted_views": verified["mask"]["views"],
        "accepted_interventions": len(accepted),
        "rejected_interventions": len(rejected),
        "accepted_by_source": dict(sorted(by_source.items())),
        "accepted": sorted(accepted, key=lambda row: row["sample_id"]),
        "rejected": sorted(rejected, key=lambda row: row["sample_id"]),
        "interpretation": "bounded synthetic selector sensitivity, not exhaustive reader-text necessity",
        "train_ready": False,
    }
    payload = canonical(result) + "\n"
    receipt_path = output.with_suffix(".receipt.json")
    receipt = {
        "schema": INTERVENTION_SCHEMA + ".receipt",
        "sidecar_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "config_sha256": result["config_sha256"],
        "batch_manifest_sha256": result["batch_manifest_sha256"],
        "final_audit_sha256": result["final_audit_sha256"],
        "auditor_code_sha256": {
            name: sha(ROOT / name)
            for name in (
                "scripts/audit_p96_finance_interventions.py",
                "scripts/audit_p96_finance_factorial.py",
                "scripts/p96_finance_factorial.py",
            )
        },
        "accepted_interventions": len(accepted),
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
            raise ValueError("P96 selector intervention receipt drift")
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
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            run(
                args.config,
                args.batch_dir,
                args.final_audit,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
