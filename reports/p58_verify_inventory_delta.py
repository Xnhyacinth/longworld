"""Recheck the two P58 local products and add them to the frozen inventory snapshot."""

import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    baseline_path = root / "reports/p58_inventory_audit_20260908.json"
    base = json.loads(baseline_path.read_text())
    products = []
    for name, expected_count in (
        ("p58-finance-meta-reconstruction-64k-probe-1-v1-promoted-v1", 3),
        ("p58-code-transformers-review-ancestry-probe-1-v1-promoted-v1", 6),
    ):
        path = Path(base["data_root"]) / name
        gate = json.loads((path / "release_gate_receipt.json").read_text())
        assert gate["ok"] is True and gate["content_gate_eligible"] is True
        assert gate["production_eligible"] is False and not gate["errors"]
        checks = {}
        for filename, expected_hash in gate["source_file_sha256"].items():
            checks[filename] = (
                hashlib.sha256((path / filename).read_bytes()).hexdigest()
                == expected_hash
            )
        export = json.loads(
            (path / "llamafactory/training_export_manifest.json").read_text()
        )
        for item in export["outputs"]:
            content = (path / item["path"]).read_bytes()
            checks[item["path"]] = (
                hashlib.sha256(content).hexdigest() == item["sha256"]
                and len(content) == item["bytes"]
            )
        assert all(checks.values())
        train = list(map(json.loads, (path / "train.jsonl").read_text().splitlines()))
        assert len(train) == expected_count
        assert not (path / "eval.jsonl").read_text().strip()
        # Native CodeForge omits the redundant optional train_ready flag.
        assert all(
            row["data_stage"] == "train_ready"
            and row["content_gate_eligible"] is True
            and row["production_eligible"] is False
            and ("train_ready" not in row or row["train_ready"] is True)
            for row in train
        )
        meta = json.loads((path / "llamafactory/B5.meta.json").read_text())
        assert (
            meta["n"]
            == len(json.loads((path / "llamafactory/B5.json").read_text()))
            == expected_count
        )
        assert meta["duplicates_dropped"] == meta["contract_rejects"] == 0
        products.append(
            {
                "product": name,
                "gate_ok": True,
                "train_rows": len(train),
                "eval_rows": 0,
                "exact_context_tokens": sum(
                    row["actual_context_tokens"] for row in train
                ),
                "b5_examples": meta["n"],
                "b5_estimated_tokens": meta["tokens_est"],
                "hash_checks": checks,
                "world_ids": sorted({row["world_id"] for row in train}),
                "release_profile_sha256": gate["release_profile_sha256"],
                "production_eligible": False,
            }
        )
    baseline = base["physical_product_union"]
    delta = {
        key: sum(product[key] for product in products)
        for key in (
            "train_rows",
            "exact_context_tokens",
            "b5_examples",
            "b5_estimated_tokens",
        )
    }
    delta.update(
        products=2,
        new_source_entities=0,
        new_domains=0,
        new_tasks_on_existing_sources=2,
    )
    current = {
        "products": baseline["products"] + delta["products"],
        "train_rows": baseline["rows"]["train"] + delta["train_rows"],
        "train_exact_context_tokens": baseline["exact_recorded_tokens"]["train"]
        + delta["exact_context_tokens"],
        "eval_rows": baseline["rows"]["eval"],
        "eval_exact_context_tokens": baseline["exact_recorded_tokens"]["eval"],
        "b5_examples": baseline["b5_examples"] + delta["b5_examples"],
        "b5_estimated_tokens": baseline["b5_estimated_tokens"]
        + delta["b5_estimated_tokens"],
        "production_eligible_rows": 0,
    }
    report = {
        "schema_version": "longworld.p58-final-inventory-delta.v1",
        "baseline_path": str(baseline_path),
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "products": products,
        "delta": delta,
        "current_physical_local_probe_inventory": current,
        "limitations": [
            "Existing gate-qualified local diagnostic products, not a new signed union or production release.",
            "Exact-token values are summed from hash-verified rows, not fresh retokenization.",
            "Conversion validators separately verified new export bindings; this script checks hashes and counts, not HMAC signatures.",
            "Baseline IETF shortcut findings remain unresolved; the inventory is not certified shortcut-free.",
            "Cross-product near-dedup and split leakage were not rerun.",
            "World IDs and view/length counts do not establish independent source entities.",
        ],
    }
    (root / "reports/p58_scaleout_inventory_final_20260908.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"delta": delta, "current": current}, indent=2))


if __name__ == "__main__":
    main()
