"""Reverify pinned Alphabet source manifests and summarize measured oracle output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from longworld.core.domainhistory import audit_cumulative_history
from longworld.core.financehistory import (
    audit_financial_history_candidate,
    extract_financial_filings,
)
from longworld.core.issuerfilingworkflow import load_issuer_ir_filing_manifest_bytes

ROOT = Path("data/source_inventory/p55_finance_alphabet_2020_2024_v1")
OUTPUT = Path("reports/p55_finance_alphabet_source_oracle_preflight_v1.json")


def summarize() -> dict:
    sources = []
    for name in (
        "issuer_ir_manifest.signed.json",
        "issuer_ir_manifest_2021_2024.signed.json",
        "issuer_ir_manifest_breakdown_v3.signed.json",
    ):
        raw = (ROOT / name).read_bytes()
        manifest = load_issuer_ir_filing_manifest_bytes(raw)
        filings = extract_financial_filings(manifest)
        sources.append(
            {
                "manifest_path": str(ROOT / name),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "verified": True,
                "profile": manifest.get(
                    "financial_metric_profile", "alphabet.four-core-roles"
                ),
                "filings": [
                    {
                        "report_date": f.report_date,
                        "source_rows": len(f.rows),
                        "source_row_chars": sum(len(r.source_text) for r in f.rows),
                        "source_sha256": f.source_sha256,
                        "source_url": f.source_url,
                    }
                    for f in filings
                ],
                "facts": [
                    {
                        "report_date": r["report_date"],
                        "text_sha256": r["text_sha256"],
                        "derived_facts": r["derived_facts"],
                    }
                    for r in manifest["records"]
                ],
            }
        )
    candidates = {}
    for version, directory in [
        ("v2", "p55_finance_alphabet_asset_trajectory_2021_2024_v2"),
        ("v3", "p55_finance_alphabet_asset_breakdown_v3"),
    ]:
        base = Path("reports") / directory
        manifest = json.loads((base / "MANIFEST.json").read_text())
        raw = (base / "candidates.jsonl").read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["candidate_sha256"]:
            raise ValueError("Alphabet parent bytes changed")
        rows = [json.loads(line) for line in raw.splitlines() if line]
        audits = [audit_financial_history_candidate(row) for row in rows]
        if (
            audits != [row["audit"] for row in manifest["rows"]]
            or not all(all(audit.values()) for audit in audits)
            or audit_cumulative_history(rows)
        ):
            raise ValueError("Alphabet parent oracle changed")
        candidates[version] = {
            "manifest_path": str(base / "MANIFEST.json"),
            "candidate_sha256": manifest["candidate_sha256"],
            "rows": manifest["rows"],
            "cumulative_growth_errors": manifest["cumulative_growth_errors"],
            "train_ready": manifest["train_ready"],
        }
        projection = base / "projected/MANIFEST.json"
        if projection.exists():
            candidates[version]["projection"] = json.loads(projection.read_text())
            projected_raw = (projection.parent / "candidates.jsonl").read_bytes()
            if (
                hashlib.sha256(projected_raw).hexdigest()
                != candidates[version]["projection"]["projection_candidates_sha256"]
            ):
                raise ValueError("Alphabet projection bytes changed")
    return {
        "schema_version": "longworld.p55-alphabet-source-oracle-preflight.v1",
        "sources": sources,
        "candidates": candidates,
        "original_window_failure": {
            "config": "configs/p55_finance_alphabet_asset_trajectory_v1.json",
            "observed_error": "cannot fill exact 16k from verified source rows: 15977 tokens",
            "status": "frozen observed materialization failure; no padding or packer modification",
        },
        "entity_diversity": "new Alphabet source component; existing financial answer program",
        "inventory_delta": 0,
        "production_eligible": False,
        "scope": "source signatures and local oracle verified; shared selection/promotion not claimed",
    }


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(summarize(), indent=2, ensure_ascii=False) + "\n")
