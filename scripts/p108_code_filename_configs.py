"""Build the existing CodeForge primary and filename-proof configs from P108 banks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.codeforge_reading_proof import (
    CONTENT_CONTROL,
    CONTENT_PROFILE,
    GRAMMAR,
    PROFILE,
)
from scripts.p108_code_catalog import _dump, _sha


def _write(path: Path, payload: dict, verify_only: bool) -> None:
    content = _dump(payload)
    if verify_only:
        if path.read_text() != content:
            raise ValueError("P108 filename profile config replay differs")
    else:
        if path.exists():
            raise ValueError("P108 filename profile config already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def build(
    bank_manifest: Path,
    bank_config: Path,
    catalog_path: Path,
    proof_path: Path,
    primary_root: Path,
    *,
    verify_only: bool = False,
) -> tuple[dict, dict]:
    catalog_path = catalog_path if catalog_path.is_absolute() else ROOT / catalog_path
    proof_path = proof_path if proof_path.is_absolute() else ROOT / proof_path
    source = json.loads(bank_manifest.read_text())
    if (
        source.get("schema") != "longworld.p108-code-bank.v1.result"
        or source.get("verified_banks") != source.get("signed_source_worlds")
        or source.get("only_repository") is not None
    ):
        raise ValueError("P108 filename profile requires fully verified banks")
    jobs = []
    tokenizers = []
    for row in source["by_repository"]:
        if row["status"] != "bank_verified":
            raise ValueError("P108 filename profile found rejected bank")
        safe = row["repository"].replace("/", "__")
        bank = ROOT / row["bank_root"]
        config_path = bank.parent.parent / "configs" / f"{safe}.json"
        if (
            _sha(config_path) != row["config_sha256"]
            or _sha(bank / "BUILD_RECEIPT.json") != row["receipt_sha256"]
        ):
            raise ValueError("P108 filename profile bank pin changed")
        tokenizers.append(json.loads(config_path.read_text())["tokenizer"])
        jobs.append(
            {
                "repository": safe,
                "config": str(config_path.relative_to(ROOT)),
                "output": row["bank_root"],
            }
        )
    if len({json.dumps(item, sort_keys=True) for item in tokenizers}) != 1:
        raise ValueError("P108 filename profile tokenizer differs across banks")
    if _sha(bank_config) != source["config_sha256"]:
        raise ValueError("P108 filename profile bank config pin differs")
    base = json.loads(bank_config.read_text())
    catalog = {
        "schema_version": "longworld.codeforge-taskbank-catalog.v1",
        "source_bank_manifest_sha256": _sha(bank_manifest),
        "trust_file": "${QJIU_ROOT}/.longworld-agent-probe/p12-probe-12-v2/p17-codeforge-trust/local_probe_trust.json",
        "approved_policy_sha256": [base["public_policy_sha256"]],
        "source_client_sha256": base["source_client_sha256"],
        "jobs": jobs,
    }
    proof = {
        "schema_version": "longworld.codeforge-reading-proof-config.v1",
        "source_catalog": str(catalog_path.relative_to(ROOT)),
        "primary_root": str(primary_root),
        "profile_id": CONTENT_PROFILE,
        "raw_profile_id": PROFILE,
        "content_control": CONTENT_CONTROL,
        "grammar_id": GRAMMAR,
        "windows": [4096, 8192, 16384],
        "tokenizer": tokenizers[0],
        "counterfactual_views": False,
    }
    _write(catalog_path, catalog, verify_only)
    _write(proof_path, proof, verify_only)
    return catalog, proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-manifest", type=Path, required=True)
    parser.add_argument("--bank-config", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    catalog, _ = build(
        args.bank_manifest,
        args.bank_config,
        args.catalog,
        args.proof,
        args.primary_root,
        verify_only=args.verify_only,
    )
    print(_dump({"source_worlds": len(catalog["jobs"]), "status": "PASS"}))


if __name__ == "__main__":
    main()
