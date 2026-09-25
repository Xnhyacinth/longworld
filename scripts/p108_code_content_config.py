"""Derive a P99 content-task batch from every verified P108 source bank."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p108_code_catalog import _dump, _sha


def build(bank_manifest: Path, output: Path, *, verify_only: bool = False) -> dict:
    source = json.loads(bank_manifest.read_text())
    if (
        source.get("schema") != "longworld.p108-code-bank.v1.result"
        or source.get("only_repository") is not None
        or source.get("verified_banks") != source.get("signed_source_worlds")
        or source.get("verified_banks", 0) < 1
    ):
        raise ValueError("P108 content config needs all verified repository banks")
    banks = []
    for row in source["by_repository"]:
        if row["status"] != "bank_verified":
            raise ValueError("P108 bank was rejected")
        bank = ROOT / row["bank_root"]
        receipt = bank / "BUILD_RECEIPT.json"
        if _sha(receipt) != row["receipt_sha256"]:
            raise ValueError("P108 bank receipt pin changed")
        parsed = json.loads(receipt.read_text())
        group = "https://github.com/" + row["repository"]
        if parsed["source_group_id"] != group or parsed["split"] != row["split"]:
            raise ValueError("P108 bank source/split changed")
        banks.append(
            {
                "bank_root": row["bank_root"],
                "receipt_sha256": row["receipt_sha256"],
                "source_group_id": group,
                "split": row["split"],
            }
        )
    payload = {
        "schema_version": "longworld.p99-code-content.v1",
        "min_evidence_span_tokens": 16384,
        "max_chat_tokens": 262144,
        "max_tasks_per_repository": 12,
        "source_bank_manifest_sha256": _sha(bank_manifest),
        "banks": banks,
    }
    content = _dump(payload)
    if verify_only:
        if output.read_text() != content:
            raise ValueError("P108 content config replay differs")
    else:
        if output.exists():
            raise ValueError("P108 content config already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = build(args.bank_manifest, args.output, verify_only=args.verify_only)
    print(_dump({"banks": len(result["banks"]), "status": "PASS"}))


if __name__ == "__main__":
    main()
