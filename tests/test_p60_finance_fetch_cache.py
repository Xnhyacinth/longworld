"""Repeated acquisition must not replace a frozen inline source snapshot."""

import json
from pathlib import Path

from longworld.core import issuerinlineworkflow
from scripts.fetch_issuer_ir_filing_history import fetch_issuer_ir_filing_history


def test_existing_inline_inventory_is_verified_without_refetch(tmp_path, monkeypatch):
    request_path = Path("configs/p60_finance_amd_inline_fetch_v1.json")
    request = json.loads(request_path.read_text())
    inventory = {
        "schema_version": issuerinlineworkflow.ISSUER_INLINE_INVENTORY_SCHEMA,
        "request": request,
        "generated_at": "2026-09-08T00:00:00Z",
    }
    output = tmp_path / "issuer_inline_inventory.json"
    original = (json.dumps(inventory) + "\n").encode()
    output.write_bytes(original)
    verified = []

    def verify(value, base, *, generated_at):
        assert base == tmp_path and generated_at == inventory["generated_at"]
        verified.append(value)
        return {}

    monkeypatch.setattr(issuerinlineworkflow, "build_issuer_inline_manifest", verify)

    def forbidden_fetch(*args):
        raise AssertionError("frozen source must not be fetched again")

    assert (
        fetch_issuer_ir_filing_history(request_path, tmp_path, http_get=forbidden_fetch)
        == output
    )
    assert output.read_bytes() == original
    assert verified == [inventory]
