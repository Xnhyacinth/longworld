"""Recheck P59-P61 product hashes/counts without changing any signed product."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = {
    "p59-finance-alphabet-reconstruction-64k-probe-1-v1-promoted-v1": "text_or_numeric_target_checked",
    "p59-finance-micron-reconstruction-64k-probe-1-v1-promoted-v1": "text_or_numeric_target_checked",
    "p59-finance-nvidia-cash-components-64k-probe-1-v1-promoted-v1": "text_or_numeric_target_checked",
    "p59-code-pulumi-recovery-64k-probe-1-v1-promoted-v1": "hidden_attribute_diagnostic",
    "p60-code-pulumi-patch-review-64k-probe-1-v1-promoted-v1": "byte_hash_and_hidden_attribute_diagnostic",
    "p60-finance-amd-inline-cash-components-64k-probe-1-v1-promoted-v1": "text_or_numeric_target_checked",
    "p60-code-pulumi-patch-files-reading-v2-64k-probe-1-v1-promoted-v1": "text_or_numeric_target_checked",
    "p61-code-duckdb-reading-32k-probe-1-v1-promoted-v1": "text_or_numeric_target_checked",
}


def verify_product(name, disposition):
    path = (ROOT / "data/releases" / name).resolve()
    gate = json.loads((path / "release_gate_receipt.json").read_text())
    assert gate["ok"] is True and gate["content_gate_eligible"] is True
    assert gate["production_eligible"] is False and not gate["errors"]
    required = {"train.jsonl", "eval.jsonl", "quality_report.json"}
    assert required <= set(gate["source_file_sha256"])
    hashes = {}
    for relative, expected in gate["source_file_sha256"].items():
        file_path = (path / relative).resolve()
        assert file_path.is_relative_to(path)
        observed = hashlib.sha256(file_path.read_bytes()).hexdigest()
        assert observed == expected
        hashes[relative] = observed
    manifest = json.loads(
        (path / "llamafactory/training_export_manifest.json").read_text()
    )
    assert manifest["release_profile_sha256"] == gate["release_profile_sha256"]
    assert len(manifest["outputs"]) == 4
    for item in manifest["outputs"]:
        file_path = (path / item["path"]).resolve()
        assert file_path.is_relative_to(path)
        raw = file_path.read_bytes()
        observed = hashlib.sha256(raw).hexdigest()
        assert len(raw) == item["bytes"] and observed == item["sha256"]
        hashes[item["path"]] = observed
    train = list(map(json.loads, (path / "train.jsonl").read_text().splitlines()))
    assert len(train) == 3 and not (path / "eval.jsonl").read_text().strip()
    assert all(
        row["data_stage"] == "train_ready"
        and row["content_gate_eligible"] is True
        and row["production_eligible"] is False
        for row in train
    )
    b5 = json.loads((path / "llamafactory/B5.meta.json").read_text())
    assert b5["n"] == len(json.loads((path / "llamafactory/B5.json").read_text())) == 3
    assert b5["duplicates_dropped"] == b5["contract_rejects"] == 0
    return {
        "product": name,
        "disposition": disposition,
        "train_rows": len(train),
        "eval_rows": 0,
        "exact_context_tokens": sum(row["actual_context_tokens"] for row in train),
        "b5_examples": b5["n"],
        "b5_estimated_tokens": b5["tokens_est"],
        "world_ids": sorted({row["world_id"] for row in train}),
        "hashes": hashes,
        "gate_ok": True,
        "production_eligible": False,
    }


def totals(products):
    return {
        key: sum(product[key] for product in products)
        for key in (
            "train_rows",
            "eval_rows",
            "exact_context_tokens",
            "b5_examples",
            "b5_estimated_tokens",
        )
    }


def main():
    baseline_path = ROOT / "reports/p58_scaleout_inventory_final_20260908.json"
    baseline = json.loads(baseline_path.read_text())[
        "current_physical_local_probe_inventory"
    ]
    products = [
        verify_product(name, disposition) for name, disposition in PRODUCTS.items()
    ]
    delta = totals(products)
    reading = totals(
        [p for p in products if p["disposition"] == "text_or_numeric_target_checked"]
    )
    diagnostic = totals(
        [p for p in products if p["disposition"] != "text_or_numeric_target_checked"]
    )
    current = {
        "products": baseline["products"] + len(products),
        "train_rows": baseline["train_rows"] + delta["train_rows"],
        "train_exact_context_tokens": baseline["train_exact_context_tokens"]
        + delta["exact_context_tokens"],
        "eval_rows": baseline["eval_rows"],
        "eval_exact_context_tokens": baseline["eval_exact_context_tokens"],
        "b5_examples": baseline["b5_examples"] + delta["b5_examples"],
        "b5_estimated_tokens": baseline["b5_estimated_tokens"]
        + delta["b5_estimated_tokens"],
        "production_eligible_rows": 0,
    }
    report = {
        "schema_version": "longworld.p59-p61-inventory-closeout.v1",
        "baseline": str(baseline_path),
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "products": products,
        "physical_delta": delta,
        "text_or_numeric_target_delta": reading,
        "execution_or_hidden_attribute_diagnostic_delta": diagnostic,
        "new_source_entities": ["AMD", "Pulumi", "DuckDB"],
        "new_domains_admitted": 0,
        "current_physical_local_probe_inventory": current,
        "limitations": [
            "Physical local-gate counts are not a universal reading-readiness certificate.",
            "Two new gate-qualified products are held as execution/hidden-attribute diagnostics.",
            "The source and export validators ran separately; this verifier rechecks bound hashes and counts, not HMAC signatures.",
            "Exact counts are recorded tokenizer counts, not a new retokenization.",
            "No new model-accuracy evaluation, full content deduplication or full split-leakage audit is claimed.",
            "CURRENT_RELEASE and HF are unchanged; production eligibility remains false.",
        ],
    }
    (ROOT / "reports/p61_incremental_inventory_20260908.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "delta": delta,
                "text_or_numeric": reading,
                "diagnostic": diagnostic,
                "current": current,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
