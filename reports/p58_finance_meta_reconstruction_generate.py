"""Build an immutable opt-in source successor and existing finance pipeline parents."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.issuerfilingworkflow import (
    META_RECONSTRUCTION_PROFILE,
    build_issuer_ir_filing_manifest,
    load_issuer_ir_filing_manifest_bytes,
)
from scripts.materialize_finance_histories import materialize
from scripts.prepare_finance_pipeline_candidates import prepare

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path, required=True)
args = parser.parse_args()
config = json.loads(args.config.read_text())
source_dir = ROOT / "data/source_inventory/p57_finance_meta_ir_fy2022_2025_v1"
old_raw = (source_dir / "issuer_ir_manifest.signed.json").read_bytes()
old = load_issuer_ir_filing_manifest_bytes(old_raw)
inventory = json.loads((source_dir / "issuer_ir_inventory.json").read_text())
inventory["financial_metric_profile"] = META_RECONSTRUCTION_PROFILE
manifest = build_issuer_ir_filing_manifest(
    inventory, source_dir, generated_at="2026-09-08T12:00:00Z"
)
assert [r["source_sha256"] for r in manifest["records"]] == [
    r["source_sha256"] for r in old["records"]
]
key = attestation_key_from_env("source_manifest")
if key is None:
    raise ValueError("source key required")
manifest = attach_attestation(manifest, key, purpose="source_manifest")
raw = (
    json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    + "\n"
).encode()
source_out = ROOT / config["signed_issuer_manifest"]
source_out.parent.mkdir(parents=True, exist_ok=True)
if source_out.exists() and source_out.read_bytes() != raw:
    raise ValueError("immutable source successor already exists with different content")
source_out.write_bytes(raw)
load_issuer_ir_filing_manifest_bytes(raw)
output = ROOT / "reports/p58_finance_meta_reconstruction_v1"
report = materialize(args.config, output / "materialized")
prepared = prepare(output / "materialized/candidates.jsonl", output / "parents")
(output / "parents/parents.jsonl").write_bytes(
    (output / "parents/candidates.jsonl").read_bytes()
)
receipt = {
    "source_parent_sha256": hashlib.sha256(old_raw).hexdigest(),
    "source_successor_sha256": hashlib.sha256(raw).hexdigest(),
    "source_bytes_unchanged": True,
    "profile": META_RECONSTRUCTION_PROFILE,
    "parent_count": prepared["rows"],
    "primary_bucket": "64k",
    "production_eligible": False,
    "train_ready": False,
}
(output / "GENERATION_RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
