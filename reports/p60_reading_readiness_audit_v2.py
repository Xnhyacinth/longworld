"""Read frozen products and write a separate, narrow reading-readiness diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core import p57pipeline as detector

SURFACES = ("question", "document_context", "context")
FLAGS = (
    "known_question_only",
    "needs_byte_hash_tool",
    "needs_source_id_evidence",
)
PATCH = re.compile(r"\bpatch=([0-9a-f]{64})(?![0-9a-f])")
ANCESTRY = re.compile(r"\bancestry=([0-9a-f]{40})->([0-9a-f]{40})(?![0-9a-f])")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def visibility(identifier: str, row: dict) -> dict:
    return {
        field: identifier in row[field] if isinstance(row.get(field), str) else None
        for field in SURFACES
    }


def source_identities(row: dict) -> dict:
    families = row.get("real_source_family_ids") or row.get("source_family_ids") or []
    repos = sorted({
        value.casefold().removesuffix(".git") for value in families
        if isinstance(value, str) and re.fullmatch(r"github\.com/[^/\s]+/[^/\s]+", value)
    })
    ciks = sorted({
        cik for field in ("document_context", "context")
        if isinstance(row.get(field), str)
        for cik in re.findall(
            r'"(?:filing_record_id|source_record_id)"\s*:\s*"issuer-(?:ir|inline):(\d{10}):',
            row[field],
        )
    }) if row.get("domain") == "finance" else []
    workflows = row.get("real_source_workflow_ids")
    valid_workflows = isinstance(workflows, list) and bool(workflows) and all(
        isinstance(value, str) and value for value in workflows
    )
    missing = []
    if row.get("domain") == "codeforge" and not repos:
        missing.append("repository_identity_not_assessed")
    if row.get("domain") == "finance" and not ciks:
        missing.append("issuer_cik_not_assessed")
    if not valid_workflows:
        missing.append("source_workflow_identity_not_assessed")
    return {
        "repositories": repos,
        "issuer_ciks": ciks,
        "real_source_workflow_ids": sorted(set(workflows)) if valid_workflows else [],
        "missing_identity_reasons": missing,
    }


def inspect_row(row: dict) -> dict:
    question, answer = row.get("question"), row.get("answer")
    flags, unassessed = [], []
    valid_question = isinstance(question, str) and bool(question.strip())
    valid_answer = isinstance(answer, (str, dict)) and answer != ""
    if not valid_question:
        unassessed.append("question_missing_or_not_nonempty_string")
    if not valid_answer:
        unassessed.append("answer_missing_or_unsupported_schema")
    question_only = {"applicable": False, "exact_match": None}
    if valid_question and valid_answer:
        try:
            prediction = detector.question_only_codebook_prediction(question)
            question_only["applicable"] = prediction is not None
            if prediction is not None:
                gold = json.loads(answer) if isinstance(answer, str) else answer
                if not isinstance(gold, dict):
                    raise ValueError("codebook answer is not a mapping")
                question_only.update({
                    "prediction": prediction,
                    "exact_match": prediction == gold,
                    "correct_answer_fields": sum(prediction.get(k) == v for k, v in gold.items()),
                    "answer_fields": len(gold),
                })
                if prediction == gold:
                    flags.append("known_question_only")
        except (ValueError, TypeError) as error:
            unassessed.append("question_only_input_error: " + str(error))
    answer_text = answer if isinstance(answer, str) else json.dumps(answer) if valid_answer else ""
    hashes = PATCH.findall(answer_text)
    pairs = ANCESTRY.findall(answer_text)
    patch_visibility = [{"sha256": value, "visibility": visibility(value, row)} for value in hashes]
    ancestry_visibility = [{
        "merge_sha": merge, "merge_visibility": visibility(merge, row),
        "tag_sha": tag, "tag_visibility": visibility(tag, row),
    } for merge, tag in pairs]
    all_surfaces = all(isinstance(row.get(field), str) for field in SURFACES)
    if (hashes or pairs) and not all_surfaces:
        unassessed.append("requested_identifier_visibility_missing_reading_surface")
    missing_hashes = sum(not any(item["visibility"].values()) for item in patch_visibility) if all_surfaces else None
    missing_ids = sum(
        not any(item[field].values())
        for item in ancestry_visibility for field in ("merge_visibility", "tag_visibility")
    ) if all_surfaces else None
    if missing_hashes:
        flags.append("needs_byte_hash_tool")
    if missing_ids:
        flags.append("needs_source_id_evidence")
    return {
        "flags": flags,
        "assessment_status": "known_issue" if flags else "not_assessed" if unassessed else "checked_no_known_issue",
        "not_assessed_reasons": unassessed,
        "question_only": question_only,
        "patch_hash_check": {
            "applicable": bool(hashes), "requested_occurrences": len(hashes),
            "missing_occurrences": missing_hashes, "hash_visibility": patch_visibility,
        },
        "ancestry_id_check": {
            "applicable": bool(pairs), "requested_pairs": len(pairs),
            "requested_id_occurrences": 2 * len(pairs), "missing_id_occurrences": missing_ids,
            "pair_visibility": ancestry_visibility,
            "scope": "Only explicit ancestry=mergeSHA->tagSHA in the actual answer; admission metadata is not a target.",
        },
        "source_identities": source_identities(row),
    }


def counts(rows: list[dict]) -> dict:
    return {
        "rows": len(rows),
        "flag_counts_nonexclusive": {flag: sum(flag in row["flags"] for row in rows) for flag in FLAGS},
        "unique_flagged_rows": sum(bool(row["flags"]) for row in rows),
        "not_assessed_rows": sum(row["assessment_status"] == "not_assessed" for row in rows),
        "checked_no_known_issue_rows": sum(row["assessment_status"] == "checked_no_known_issue" for row in rows),
        "flag_combinations": dict(Counter(" + ".join(row["flags"]) for row in rows if row["flags"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", type=Path, default=ROOT / "reports/p60_reading_readiness_audit_20260908_v2")
    args = parser.parse_args()
    output_json, output_md = args.output_prefix.with_suffix(".json"), args.output_prefix.with_suffix(".md")
    if output_json.exists() or output_md.exists():
        raise ValueError("audit outputs already exist; use a new output prefix")
    snapshot_path = ROOT / "reports/p61_incremental_inventory_20260908.json"
    snapshot = read_json(snapshot_path)
    p58_path = Path(snapshot["baseline"])
    assert sha(p58_path.read_bytes()) == snapshot["baseline_sha256"]
    p58 = read_json(p58_path)
    baseline_path = Path(p58["baseline_path"])
    assert sha(baseline_path.read_bytes()) == p58["baseline_sha256"]
    baseline = read_json(baseline_path)
    source_paths = [snapshot_path, p58_path, baseline_path,
                    ROOT / "reports/p60_reading_readiness_audit_20260908.json",
                    ROOT / "reports/p60_code_ancestry_visibility_20260908.json"]
    inputs = {str(path): sha(path.read_bytes()) for path in source_paths}
    detector_path = Path(detector.__file__)
    detector_hash = sha(detector_path.read_bytes())
    products_to_check = [(p, "p58_baseline") for p in baseline["products"]]
    products_to_check += [(p, "p58_delta") for p in p58["products"]]
    products_to_check += [(p, "p59_p61_increment") for p in snapshot["products"]]
    data_root = Path(baseline["data_root"])
    products, rows, seen = [], [], set()
    for declared, origin in products_to_check:
        name = declared["product"]
        assert Path(name).name == name
        root = (data_root / name).resolve()
        assert root not in seen, "duplicate physical product"
        seen.add(root)
        gate_path = root / "release_gate_receipt.json"
        gate_raw = gate_path.read_bytes()
        gate = json.loads(gate_raw)
        assert gate.get("ok") is True, name
        bindings = {}
        for filename in ("train.jsonl", "eval.jsonl", "quality_report.json"):
            digest = sha((root / filename).read_bytes())
            assert digest == gate["source_file_sha256"][filename], (name, filename)
            if filename in declared.get("hashes", {}):
                assert digest == declared["hashes"][filename]
            bindings[filename] = digest
        product_rows = []
        for split in ("train", "eval"):
            raw = (root / f"{split}.jsonl").read_bytes()
            assert sha(raw) == bindings[f"{split}.jsonl"]
            for line_number, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                record = {
                    "product": name, "origin": origin, "split": split, "row_number": line_number,
                    "query_id": row.get("query_id"), "view": row.get("view"),
                    "world_id": row.get("world_id"), "length_bucket": row.get("length_bucket"),
                    "domain": row.get("domain"), "query_type": row.get("query_type"),
                    "answer_program_id": row.get("answer_program_id"),
                    "source_origins": row.get("source_origins"),
                    "real_source_family_ids": row.get("real_source_family_ids"),
                    "row_line_sha256": sha(line),
                    **inspect_row(row),
                }
                product_rows.append(record)
                rows.append(record)
        assert sha(gate_path.read_bytes()) == sha(gate_raw)
        products.append({
            "product": name, "path": str(root), "origin": origin,
            "release_profile_id": gate.get("release_profile_id"), "gate_ok": True,
            "production_eligible": gate.get("production_eligible"),
            "gate_receipt_sha256": sha(gate_raw), "gate_bound_file_sha256": bindings,
            "summary": counts(product_rows), "rows": product_rows,
        })
    physical = snapshot["current_physical_local_probe_inventory"]
    assert len(products) == physical["products"] == 30
    by_split = {split: [row for row in rows if row["split"] == split] for split in ("train", "eval")}
    assert len(by_split["train"]) == physical["train_rows"] == 246
    assert len(by_split["eval"]) == physical["eval_rows"] == 18
    new_train = [row for row in by_split["train"] if row["origin"] == "p59_p61_increment"]
    assert len(new_train) == snapshot["physical_delta"]["train_rows"] == 24
    identity_keys = ("repositories", "issuer_ciks", "real_source_workflow_ids")
    eval_ids = {key: sorted({value for row in by_split["eval"] for value in row["source_identities"][key]}) for key in identity_keys}
    overlap_rows = []
    for row in new_train:
        identities = row["source_identities"]
        overlaps = {key: sorted(set(identities[key]) & set(eval_ids[key])) for key in identity_keys}
        status = "overlap_found" if any(overlaps.values()) else "not_assessed" if identities["missing_identity_reasons"] else "checked_no_identifier_overlap"
        overlap_rows.append({
            "product": row["product"], "query_id": row["query_id"], "view": row["view"],
            **identities, "overlap": overlaps, "status": status,
        })
    product_ids = defaultdict(lambda: {key: set() for key in identity_keys})
    for row in new_train:
        for key in identity_keys:
            product_ids[row["product"]][key].update(row["source_identities"][key])
    reused_sources = []
    names = sorted(product_ids)
    for index, name in enumerate(names):
        for other in names[index + 1:]:
            overlap = {key: sorted(product_ids[name][key] & product_ids[other][key]) for key in identity_keys}
            if any(overlap.values()):
                reused_sources.append({"products": [name, other], "shared_identifiers": overlap})
    hold = {split: [] for split in by_split}
    task_triggers = defaultdict(list)
    for row in rows:
        if row["flags"] and all(isinstance(row.get(key), str) and row[key] for key in ("world_id", "answer_program_id")):
            task_triggers[(row["world_id"], row["answer_program_id"])].append(row)
    task_hold = {split: [] for split in by_split}
    task_identity_missing = []
    for product in products:
        for row in product["rows"]:
            identity_valid = all(isinstance(row.get(key), str) and row[key] for key in ("world_id", "answer_program_id"))
            task_key = (row["world_id"], row["answer_program_id"])
            row["task_family_hold"] = identity_valid and task_key in task_triggers
            if not identity_valid:
                task_identity_missing.append({"product": row["product"], "query_id": row["query_id"], "view": row["view"]})
            manifest_entry = {
                **{key: row[key] for key in ("product", "world_id", "query_id", "view", "length_bucket", "row_number", "split", "flags", "answer_program_id", "row_line_sha256")},
                "product_path": product["path"],
                "split_file_sha256": product["gate_bound_file_sha256"][f"{row['split']}.jsonl"],
                "gate_receipt_sha256": product["gate_receipt_sha256"],
            }
            if row["flags"]:
                hold[row["split"]].append(manifest_entry)
            if row["task_family_hold"]:
                task_hold[row["split"]].append({
                    **manifest_entry,
                    "hold_basis": "same_world_and_answer_program_as_counterexample",
                    "inherited_hold_without_direct_hit": not row["flags"],
                    "trigger_flags": sorted({flag for trigger in task_triggers[task_key] for flag in trigger["flags"]}),
                })
    summary = counts(rows)
    summary.update({
        "products": len(products), "by_split": {split: counts(values) for split, values in by_split.items()},
        "question_only_applicable_rows": sum(row["question_only"]["applicable"] for row in rows),
        "question_only_exact_cf_rows": sum("known_question_only" in row["flags"] and row["view"] == "cf" for row in rows),
        "patch_hash_requested_occurrences": sum(row["patch_hash_check"]["requested_occurrences"] for row in rows),
        "patch_hash_missing_occurrences": sum(row["patch_hash_check"]["missing_occurrences"] or 0 for row in rows),
        "ancestry_id_requested_occurrences": sum(row["ancestry_id_check"]["requested_id_occurrences"] for row in rows),
        "ancestry_id_missing_occurrences": sum(row["ancestry_id_check"]["missing_id_occurrences"] or 0 for row in rows),
        "task_family_hold_rows": sum(len(values) for values in task_hold.values()),
        "task_family_hold_by_split": {split: len(values) for split, values in task_hold.items()},
        "task_family_inherited_hold_without_direct_hit": sum(not row["flags"] and row["task_family_hold"] for row in rows),
        "task_family_identity_not_assessed_rows": len(task_identity_missing),
    })
    report = {
        "schema_version": "longworld.reading-readiness-audit.v2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Thirty explicitly inventoried gate products; old duplicate versions and ungated candidates excluded.",
        "input_sha256": inputs, "audit_script_sha256": sha(Path(__file__).read_bytes()),
        "detector": {"revision": detector.QUESTION_ONLY_CHECK_REVISION, "module_sha256": detector_hash, "function_source": inspect.getsource(detector.question_only_codebook_prediction)},
        "literal_patterns": {"patch": PATCH.pattern, "ancestry": ANCESTRY.pattern},
        "summary": summary, "direct_counterexample_rows_by_split": hold,
        "hold_rows_by_split": task_hold,
        "task_family_hold_rows_by_split": task_hold,
        "task_family_hold_scope": {
            "key": ["world_id", "answer_program_id"],
            "n_affected_task_instances": len(task_triggers),
            "not_assessed": task_identity_missing,
            "meaning": "Conservative triage of all inventoried views/lengths sharing both identifiers with a counterexample. This does not certify the complement or modify any frozen row eligibility.",
            "cross_program_hold": False,
        },
        "new_train_vs_existing_eval": {
            "new_train_rows": len(new_train), "existing_eval_rows": len(by_split["eval"]),
            "existing_eval_identifier_sets": eval_ids,
            "existing_eval_missing_identity_rows": [row["query_id"] for row in by_split["eval"] if row["source_identities"]["missing_identity_reasons"]],
            "status_counts": dict(Counter(row["status"] for row in overlap_rows)),
            "rows": overlap_rows, "within_new_train_source_reuse": reused_sources,
            "method": "Exact repository, issuer CIK and real-source-workflow ID comparison. CIK uses structured issuer-ir/issuer-inline record IDs; world IDs are not evidence of source separation.",
        },
        "flag_definitions": {
            "known_question_only": "The known public singleton-codebook predictor exactly matches this row; no general no-context-model result is claimed.",
            "needs_byte_hash_tool": "An explicitly requested patch SHA256 is absent literally from all three reading surfaces. Computing it requires exact original patch bytes and a hash implementation.",
            "needs_source_id_evidence": "An explicitly requested ancestry merge/tag SHA is absent literally from all three reading surfaces. Source-admission metadata is not model-visible evidence; a patch hash tool alone does not supply these IDs.",
            "checked_no_known_issue": "No hit from these three narrow checks. Not universal reading safety, training approval, or model evaluation.",
            "not_assessed": "A required field/schema was unavailable for the stated checks.",
        },
        "limitations": [
            "Flags are nonexclusive; use unique_flagged_rows rather than adding flag counts.",
            "Direct matches are counterexamples, not a clean-corpus deletion recipe. Correlated CF/length rows in affected tasks require holding or revalidation even without a direct hit.",
            "Task-family grouping uses both world_id and answer_program_id; it does not sweep a new reading-v2 program merely because the source world is shared.",
            "Existing gate ok and its bound train/eval/quality-report file hashes were rechecked; HMAC signatures were not independently reverified.",
            "No frozen row, product, audit, filter, signature, CURRENT_RELEASE or HF artifact was changed.",
            "Identifier absence is a literal reading-surface test, not a proof of mathematical impossibility or byte recovery from rendered text.",
            "Only actual answer requests trigger digest/ancestry checks; unused source-admission metadata does not flag readable-v2 tasks.",
            "No general model accuracy, dense retrieval, full content deduplication, complete split-leakage, or B5-alignment evaluation was performed.",
            "Source-workflow IDs can differ for tasks over the same source; repository and issuer identifiers are checked separately where applicable.",
            "Views and lengths are correlated and are not independent source worlds.",
        ],
        "products": products,
    }
    # Verify all inspected products and the v1 report remain byte-identical.
    for product in products:
        root = Path(product["path"])
        assert sha((root / "release_gate_receipt.json").read_bytes()) == product["gate_receipt_sha256"]
        for name, expected in product["gate_bound_file_sha256"].items():
            assert sha((root / name).read_bytes()) == expected
    assert all(sha(Path(path).read_bytes()) == digest for path, digest in inputs.items())
    assert sha(detector_path.read_bytes()) == detector_hash
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("x") as handle:
        handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Final reading-readiness audit v2 — 2026-09-08", "",
        "**30 gate-qualified local products: 246 train + 18 eval = 264 rows.** Existing gate-bound file hashes were verified. The initial v1 snapshot is preserved.", "",
        "| Known flag (nonexclusive) | Train | Eval | Total |", "| --- | ---: | ---: | ---: |",
    ]
    for flag in FLAGS:
        lines.append(f"| `{flag}` | {summary['by_split']['train']['flag_counts_nonexclusive'][flag]} | {summary['by_split']['eval']['flag_counts_nonexclusive'][flag]} | {summary['flag_counts_nonexclusive'][flag]} |")
    lines += ["", f"**Unique flagged rows: {summary['unique_flagged_rows']}** ({len(hold['train'])} train / {len(hold['eval'])} eval). Not assessed: {summary['not_assessed_rows']}. **The remaining {summary['checked_no_known_issue_rows']} rows merely did not trigger these three checks; they are not certified universally safe.**", "",
              f"These are **counterexamples, not a recipe for deleting rows to obtain a clean corpus**. Grouping by both existing `world_id` and `answer_program_id` gives a conservative task-instance hold of **{summary['task_family_hold_rows']} rows** ({len(task_hold['train'])} train / {len(task_hold['eval'])} eval), including {summary['task_family_inherited_hold_without_direct_hit']} correlated CF/length rows without a direct hit. Grouping does not cross answer programs, so the distinct reading-v2 successor is not held merely for sharing a source world. Neither this hold nor its complement is a new training certificate.", "",
              "## Explicit ancestry targets", "",
              "The new source-ID flag checks only `ancestry=mergeSHA->tagSHA` actually requested in an answer. These IDs are distinct from a computed patch SHA256. A metadata-only source-admission fact is not a model-visible answer operand. Rows may therefore carry both the byte-hash and source-ID flags.", "",
              "| Product with hidden ancestry target | Train | Eval |", "| --- | ---: | ---: |"]
    for product in products:
        affected = [row for row in product["rows"] if "needs_source_id_evidence" in row["flags"]]
        if affected:
            lines.append(f"| {product['product']} | {sum(row['split']=='train' for row in affected)} | {sum(row['split']=='eval' for row in affected)} |")
    lines += ["", f"Patch digests: {summary['patch_hash_missing_occurrences']} missing of {summary['patch_hash_requested_occurrences']} requested occurrences. Ancestry IDs: {summary['ancestry_id_missing_occurrences']} missing of {summary['ancestry_id_requested_occurrences']} requested occurrences. The known codebook detector exactly solves {summary['question_only_exact_cf_rows']} CF rows; CF is not described as universally solved.", "",
              "## Final additions and source identity comparison", "",
              "The final inventory includes AMD, Pulumi reading-v2, and DuckDB reading-32K. Their actual answers are checked; internal ancestry admission metadata alone cannot trigger a hold.", "",
              f"The **24 newly qualified train rows** were compared against **18 existing eval rows** by repository, structured issuer CIK and real-source-workflow ID. Results: `{dict(Counter(row['status'] for row in overlap_rows))}`. Missing applicable identity fields are reported as not assessed. This is not a full content or semantic leakage audit, and different world IDs are not treated as evidence of source independence.", "",
              "Pulumi recovery, legacy patch-review, and reading-v2 reuse source identifiers within train. Those are distinct tasks over reused sources, not three independently acquired repositories. Existing Wasmtime eval is compared explicitly; unqualified Wasmtime attempts are not included in train.", "",
              "## Machine-readable delivery and limits", "",
              "The JSON companion separates `direct_counterexample_rows_by_split` from the broader `hold_rows_by_split` / `task_family_hold_rows_by_split`. Each machine-readable entry includes query ID, view, row number, split-file SHA256, row-line SHA256 and gate-receipt SHA256. Per-row flags are nonexclusive; inherited task holds are not mislabeled as direct detector hits. The report pins the shared detector implementation and every input report.", "",
              "Only these narrow known issues were assessed. HMAC revalidation, general model evaluation, broader shortcut discovery, full deduplication/split leakage and B5 alignment were not rerun. No frozen product, filter or signature was modified.", "",
              "Reproduce to a fresh report prefix:", "", "```bash", "uv run python reports/p60_reading_readiness_audit_v2.py --output-prefix /tmp/longworld_reading_readiness_v2", "```", ""]
    with output_md.open("x") as handle:
        handle.write("\n".join(lines))
    print(json.dumps({"summary": summary, "identity_checks": report["new_train_vs_existing_eval"]["status_counts"], "outputs": [str(output_json), str(output_md)]}, indent=2))


if __name__ == "__main__":
    main()
