"""Source-bound core-role coverage preflight; never manufactures absent facts."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.financehistory import (
    _CORE_ROLES,
    _parse_number,
    extract_financial_filings,
)
from longworld.core.issuerfilingworkflow import load_issuer_ir_filing_manifest_bytes

parser = argparse.ArgumentParser()
parser.add_argument(
    "--issuer", choices=("meta", "micron", "nvidia", "alphabet"), default="meta"
)
args = parser.parse_args()
sources = {
    "meta": "p57_finance_meta_ir_fy2022_2025_v1/issuer_ir_manifest.signed.json",
    "micron": "p57_finance_micron_ir_fy2022_2025_v1/issuer_ir_manifest.signed.json",
    "nvidia": "p57_finance_nvidia_ir_fy2022_2025_v1/issuer_ir_manifest_market_v1.signed.json",
    "alphabet": "p55_finance_alphabet_2020_2024_v1/issuer_ir_manifest_breakdown_v3.signed.json",
}
source = ROOT / "data/source_inventory" / sources[args.issuer]
raw = source.read_bytes()
manifest = load_issuer_ir_filing_manifest_bytes(raw)
filings = extract_financial_filings(manifest)
rows = []
for filing in filings:
    facts = {
        fact.role: _parse_number(fact.evidence_quote)
        for row in filing.rows
        for fact in row.facts
    }
    missing = sorted(_CORE_ROLES - facts.keys())
    cash = {
        "cash_from_operations",
        "cash_from_investing",
        "cash_from_financing",
        "cash_fx_effect",
        "cash_period_change",
    }
    reconciles = (
        (
            sum(facts[r] for r in cash if r != "cash_period_change")
            == facts["cash_period_change"]
        )
        if cash <= facts.keys()
        else None
    )
    rows.append(
        {
            "report_date": filing.report_date,
            "source_url": filing.source_url,
            "source_sha256": filing.source_sha256,
            "source_rows": len(filing.rows),
            "core_facts": {r: facts[r] for r in sorted(_CORE_ROLES & facts.keys())},
            "missing_roles": missing,
            "cashflow_reconciled": reconciles,
        }
    )
report = {
    "schema_version": "longworld.p58-finance-preflight.v1",
    "answer_program_id": "finance.multi_filing_reconstruction.v1",
    "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
    "source_attestation_verified": True,
    "filings": rows,
    "can_materialize": all(
        not r["missing_roles"] and r["cashflow_reconciled"] for r in rows
    ),
    "new_semantic_tasks_preflighted": 1,
    "generated_candidates": 0,
    "audited_views": 0,
    "qualified_views": 0,
    "production_eligible": False,
    "train_ready": False,
}
output = ROOT / f"reports/p58_finance_{args.issuer}_reconstruction_preflight.json"
output.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
