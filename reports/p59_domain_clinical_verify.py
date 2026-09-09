"""Verify the frozen Paxlovid candidate without refetching or overwriting it."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_key_from_env
from longworld.core.clinicalworkflow import audit_clinical_trial_approval_task


def main():
    root = ROOT / "data/p59_domain_clinical_paxlovid_v1"
    receipt = json.loads((ROOT / "reports/p59_domain_clinical_paxlovid_receipt.json").read_text())
    for filename, field in (("workflow_manifest.signed.json", "source_manifest_sha256"), ("candidate.jsonl", "candidate_sha256")):
        assert hashlib.sha256((root / filename).read_bytes()).hexdigest() == receipt[field]
    key = attestation_key_from_env("source_manifest")
    if key is None:
        raise ValueError("isolated source verification key required")
    task = json.loads((root / "candidate.jsonl").read_text())
    audit = audit_clinical_trial_approval_task(task, source_attestation_key=key)
    assert audit == receipt["task_audit"]["audit"]
    assert all(audit.values())
    assert task["complete_world"] is False and task["train_ready"] is False
    print(json.dumps({"executable_checks_passed": len(audit), "candidate_rows": 1, "complete_worlds": 0, "training_inventory_delta": 0}, indent=2))


if __name__ == "__main__":
    main()
