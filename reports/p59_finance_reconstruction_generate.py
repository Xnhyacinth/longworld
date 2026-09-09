"""Verify immutable issuer successors, cheap replay gates, then existing finance packing."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.financehistory import (
    FINANCIAL_HISTORY_SCHEMA,
    _CORE_ROLES,
    _context,
    _filing_record,
    _relation_record,
    _replacement_quote,
    _row_record,
    extract_financial_filings,
    replay_financial_history,
)
from longworld.core.issuerfilingworkflow import (
    build_issuer_ir_filing_manifest,
    load_issuer_ir_filing_manifest_bytes,
)
from scripts.materialize_finance_histories import materialize
from scripts.prepare_finance_pipeline_candidates import prepare

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path, required=True)
parser.add_argument("--preflight-only", action="store_true")
args = parser.parse_args()
config = json.loads(args.config.read_text())
parent_path = ROOT / config["source_parent_manifest"]
parent_raw = parent_path.read_bytes()
if hashlib.sha256(parent_raw).hexdigest() != config["source_parent_sha256"]:
    raise ValueError("source parent hash differs from frozen configuration")
parent = load_issuer_ir_filing_manifest_bytes(parent_raw)
inventory_path = ROOT / config["source_inventory"]
inventory = json.loads(inventory_path.read_text())
inventory["financial_metric_profile"] = config["source_profile"]
manifest = build_issuer_ir_filing_manifest(
    inventory, inventory_path.parent, generated_at=config["source_generated_at"]
)
for old, new in zip(parent["records"], manifest["records"], strict=True):
    if (
        old["text"] != new["text"]
        or old["source_sha256"] != new["source_sha256"]
        or any(f not in new["derived_facts"] for f in old["derived_facts"])
    ):
        raise ValueError(
            "successor changed frozen source text or original derived facts"
        )
key = attestation_key_from_env("source_manifest")
if key is None:
    raise ValueError("source role required")
source_out = ROOT / config["signed_issuer_manifest"]
source_out.parent.mkdir(parents=True, exist_ok=True)
if source_out.exists():
    raw = source_out.read_bytes()
    existing = load_issuer_ir_filing_manifest_bytes(raw)
    unsigned = {k: v for k, v in existing.items() if k != "attestation"}
    # Source-only preflight and combined-role packing have distinct signing
    # envelopes. Reuse the verified immutable source instead of re-signing it.
    if (
        unsigned.get("local_probe_trust_isolation")
        == "non_independent_local_diagnostic"
    ):
        manifest["local_probe_trust_isolation"] = "non_independent_local_diagnostic"
    if unsigned != manifest:
        raise ValueError("immutable source successor already differs")
else:
    signed = attach_attestation(manifest, key, purpose="source_manifest")
    raw = (
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    source_out.write_bytes(raw)
manifest = load_issuer_ir_filing_manifest_bytes(raw)
filings = extract_financial_filings(manifest)
required = _CORE_ROLES - (
    {"cash_fx_effect"}
    if config["answer_program_id"] == "nvidia.cash_components_identity.v1"
    else set()
)
binding = {
    "signed_manifest_sha256": hashlib.sha256(raw).hexdigest(),
    "source_family": manifest["source_family"],
    "authorization_record_id": manifest["authorization"]["record_id"],
}
records = [
    {
        "record_type": "financial_history_header",
        "schema_version": FINANCIAL_HISTORY_SCHEMA,
        "world_id": config["world_id"],
        "source_binding": binding,
        "answer_program_id": config["answer_program_id"],
        "cik": manifest["issuer"]["cik"],
    }
]
annual = []
for index, filing in enumerate(filings):
    records.append(_filing_record(filing))
    if index:
        records.append(_relation_record(filing, filings[index - 1]))
    chosen = [row for row in filing.rows if any(f.role in required for f in row.facts)]
    observed = {f.role for row in chosen for f in row.facts if f.role in required}
    if observed != required:
        raise ValueError("annual required source roles incomplete")
    records.extend(_row_record(row) for row in chosen)
    derived = next(
        r for r in manifest["records"] if r["report_date"] == filing.report_date
    )
    annual.append(
        {
            "report_date": filing.report_date,
            "source_sha256": filing.source_sha256,
            "source_url": filing.source_url,
            "required_values": {
                f["field"]: f["numeric_value"]
                for f in derived["derived_facts"]
                if f["field"] in required
            },
        }
    )
task = {
    "context": _context(records),
    "world_id": config["world_id"],
    "source_binding": binding,
    "answer_program_id": config["answer_program_id"],
}
base = replay_financial_history(task)
if base["answer"] == "unknown":
    raise ValueError("cheap source-only base replay failed")
revenue_row = [
    r for r in records if any(f.get("role") == "revenue" for f in r.get("facts", []))
][-1]
revenue = next(f for f in revenue_row["facts"] if f["role"] == "revenue")
task["counterfactual_twin"] = {
    "record_id": revenue_row["source_record_id"],
    "role": "revenue",
    "source_origin": "synthetic_counterfactual",
    "provenance_operation": "replace_exact_span",
    "parent_value": revenue["evidence_quote"],
    "value": _replacement_quote(revenue["evidence_quote"]),
}
cf = replay_financial_history(task, counterfactual=True)
ids = base["essential_evidence_ids"]
remove_one = all(
    replay_financial_history(task, evidence_ids=[i for i in ids if i != missing])[
        "answer"
    ]
    != base["answer"]
    for missing in ids
)
if cf["answer"] == "unknown" or cf["answer"] == base["answer"] or not remove_one:
    raise ValueError("cheap CF or remove-one replay failed")
output = ROOT / config["output_dir"]
output.mkdir(parents=True, exist_ok=True)
receipt = {
    "schema_version": "longworld.p59-finance-reconstruction-preflight.v1",
    "source_parent_sha256": config["source_parent_sha256"],
    "source_successor_sha256": hashlib.sha256(raw).hexdigest(),
    "source_text_and_old_facts_unchanged": True,
    "world_id": config["world_id"],
    "source_profile": config["source_profile"],
    "answer_program_id": config["answer_program_id"],
    "annual": annual,
    "required_roles": sorted(required),
    "new_semantic_tasks": 1,
    "new_source_worlds": 0,
    "base_replay_sufficient": True,
    "counterfactual_changes_numeric_answer": True,
    "remove_one_fails": remove_one,
    "essential_source_rows": len(ids),
    "source_only_answer": json.loads(base["answer"]),
    "source_only_cf_answer": json.loads(cf["answer"]),
    "production_eligible": False,
}
(output / "SOURCE_PREFLIGHT.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(
    json.dumps(
        {
            k: v
            for k, v in receipt.items()
            if k not in ("annual", "source_only_answer", "source_only_cf_answer")
        },
        indent=2,
    ),
    flush=True,
)
if args.preflight_only:
    raise SystemExit(0)
materialized = materialize(args.config, output / "materialized")
prepared = prepare(output / "materialized/candidates.jsonl", output / "parents")
(output / "parents/parents.jsonl").write_bytes(
    (output / "parents/candidates.jsonl").read_bytes()
)
(output / "GENERATION_RECEIPT.json").write_text(
    json.dumps(
        {
            "world_id": config["world_id"],
            "source_successor_sha256": hashlib.sha256(raw).hexdigest(),
            "parents": prepared["rows"],
            "primary_bucket": "64k",
            "finance_executable_audits": len(materialized["rows"]),
            "production_eligible": False,
            "train_ready": False,
        },
        indent=2,
    )
    + "\n"
)
