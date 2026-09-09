#!/usr/bin/env python3
"""Materialize source-bound multi-filing financial history candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attestation_key_from_env,
    sanitized_attestation_environment,
)
from longworld.core.domainhistory import HistoryBand, audit_cumulative_history
from longworld.core.filingworkflow import (
    MAX_SEC_MANIFEST_BYTES,
    load_sec_filing_manifest,
)
from longworld.core.financehistory import (
    audit_financial_history_candidate,
    build_finance_pipeline_candidate,
    build_financial_history_candidates,
    extract_financial_filings,
    extract_sec_financial_filings,
    MICRON_DUAL_PARTITION_PROGRAM,
    NVIDIA_MARKET_MIX_CROSSOVER_PROGRAM,
    CASH_COMPONENTS_PROGRAM,
)
from longworld.core.issuerfilingworkflow import (
    MAX_ISSUER_IR_MANIFEST_BYTES,
    load_issuer_ir_filing_manifest_bytes,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.taskreplaysidecar import (
    FINANCE_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

MAX_CONFIG_BYTES = 256_000


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _read_json(path: Path, max_bytes: int) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular_file(path, max_bytes).decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot load JSON object {path.name}") from error
    if not isinstance(value, dict):
        raise ProvenanceError(f"JSON source {path.name} must be an object")
    return value


def _resolve_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("finance history config path is invalid")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_tokenizer(model_id: str, revision: str):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
        )


def materialize(config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Verify one signed issuer history and write deterministic candidates."""
    config = _read_json(config_path, MAX_CONFIG_BYTES)
    if config.get("schema_version") != "longworld.finance-history-materialization.v1":
        raise ProvenanceError("unsupported finance-history materialization config")
    manifest_path = _resolve_path(config.get("signed_issuer_manifest"))
    manifest_kind = str(config.get("source_manifest_kind") or "issuer_ir")
    if manifest_kind == "issuer_ir":
        manifest_raw = _read_regular_file(manifest_path, MAX_ISSUER_IR_MANIFEST_BYTES)
        manifest = load_issuer_ir_filing_manifest_bytes(manifest_raw)
        filings = extract_financial_filings(manifest)
        issuer = manifest.get("issuer")
        source_family = str(manifest.get("source_family") or "")
    elif manifest_kind == "issuer_sec":
        manifest_raw = _read_regular_file(manifest_path, MAX_SEC_MANIFEST_BYTES)
        manifest = load_sec_filing_manifest(manifest_path)
        issuer = config.get("issuer")
        if not isinstance(issuer, dict):
            raise ProvenanceError("finance-history SEC issuer binding is invalid")
        filings = extract_sec_financial_filings(
            manifest, cik=str(issuer.get("cik") or "")
        )
        source_family = "issuer_owned_sec_ixbrl"
    else:
        raise ProvenanceError("unsupported finance-history source manifest kind")
    tokenizer_config = config.get("tokenizer")
    if not isinstance(tokenizer_config, dict):
        raise ProvenanceError("finance-history tokenizer config is missing")
    model_id = str(tokenizer_config.get("model_id") or "")
    revision = str(tokenizer_config.get("revision") or "")
    tokenizer = _load_tokenizer(model_id, revision)

    def token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    bands_value = config.get("bands")
    if not isinstance(bands_value, list) or not bands_value:
        raise ProvenanceError("finance-history bands are missing")
    bands = tuple(
        HistoryBand(
            str(item.get("name") or ""),
            int(item.get("lower_tokens") or 0),
            int(item.get("upper_tokens") or 0),
        )
        for item in bands_value
        if isinstance(item, dict)
    )
    if len(bands) != len(bands_value):
        raise ProvenanceError("finance-history band config is invalid")
    authorization = manifest.get("authorization")
    if not isinstance(issuer, dict) or not isinstance(authorization, dict):
        raise ProvenanceError("finance-history issuer binding is invalid")
    source_binding = {
        "signed_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "source_family": source_family,
        "authorization_record_id": str(authorization.get("record_id") or ""),
    }
    rows = build_financial_history_candidates(
        filings,
        world_id=str(config.get("world_id") or ""),
        issuer_name=str(issuer.get("name") or ""),
        cik=str(issuer.get("cik") or ""),
        source_binding=source_binding,
        bands=bands,
        token_counter=token_counter,
        tokenizer_model_id=model_id,
        tokenizer_revision=revision,
        answer_program_id=str(
            config.get("answer_program_id") or "finance.multi_filing_reconstruction.v1"
        ),
    )
    tokenizer_asset_manifest_sha256 = resolved_tokenizer_asset_manifest_sha256(
        model_id, revision
    )
    for row in rows:
        row["source_verified_at_materialization"] = True
        row["tokenizer_asset_manifest_sha256"] = tokenizer_asset_manifest_sha256
    pipeline_commitments = sorted(
        (
            task_candidate_content_commitment(build_finance_pipeline_candidate(row))
            for row in rows
        ),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    source_key = attestation_key_from_env("task_replay_sidecar")
    if source_key is None:
        raise ProvenanceError("finance history requires source replay attestation key")
    sidecar = build_task_replay_sidecar(
        adapter_id=FINANCE_TASK_REPLAY_ADAPTER[0],
        adapter_revision=FINANCE_TASK_REPLAY_ADAPTER[1],
        replay_payload={
            **source_binding,
            "replay_revision": FINANCE_TASK_REPLAY_ADAPTER[1],
            "tokenizer_model_id": model_id,
            "tokenizer_revision": revision,
            "tokenizer_asset_manifest_sha256": tokenizer_asset_manifest_sha256,
            "candidate_content_commitments": pipeline_commitments,
        },
        source_attestation_key=source_key,
    )
    sidecar_bytes = _canonical_bytes(sidecar) + b"\n"
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_bytes, source_attestation_key=source_key
    )
    replay_registry_bytes = (
        _canonical_bytes(
            {
                "schema_version": "longworld.replay-path-registry.v2",
                "episode_replay_bundles": {},
                "source_workflow_bundles": {},
                "task_replay_sidecars": {
                    sidecar_binding["sha256"]: "TASK_REPLAY_SIDECAR.json"
                },
            }
        )
        + b"\n"
    )
    audits = [audit_financial_history_candidate(row) for row in rows]
    answer_program_id = str(
        config.get("answer_program_id") or "finance.multi_filing_reconstruction.v1"
    )
    cumulative_errors = (
        []
        if answer_program_id
        in {NVIDIA_MARKET_MIX_CROSSOVER_PROGRAM, MICRON_DUAL_PARTITION_PROGRAM, CASH_COMPONENTS_PROGRAM}
        else audit_cumulative_history(rows)
    )
    if not all(audit and all(audit.values()) for audit in audits) or cumulative_errors:
        raise ProvenanceError("finance-history executable audit failed")
    candidate_bytes = b"\n".join(_canonical_bytes(row) for row in rows) + b"\n"
    manifest_output: dict[str, Any] = {
        "schema_version": "longworld.finance-history-candidate-manifest.v1",
        "data_stage": "candidate_history_audit",
        "promotion_status": "ignored_non_world_candidates",
        "world_id": rows[0]["world_id"],
        "domain": "finance",
        "attempts": len(rows),
        "accepted_candidates": len(rows),
        "retention": 1.0,
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "source_manifest_sha256": source_binding["signed_manifest_sha256"],
        "source_attestation_verified": True,
        "exact_token_counts_recomputed": True,
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": tokenizer_asset_manifest_sha256,
        "task_replay_sidecar": sidecar_binding,
        "source_replay_sidecar_attested": True,
        "source_filing_count": len(filings),
        "unique_available_source_row_count": sum(
            len(filing.rows) for filing in filings
        ),
        "rows": [
            {
                "length_bucket": row["length_bucket"],
                "context_tokens": row["tokenizer_context_tokens"],
                "context_sha256": row["context_sha256"],
                "selected_filing_count": row["selected_filing_count"],
                "source_record_count": len(row["source_record_ids"]),
                "source_relation_count": len(row["source_relation_ids"]),
                "authentic_containment_relation_count": len(
                    row["authentic_source_relation_edges"]
                ),
                "verified_derived_temporal_relation_count": len(
                    row["verified_derived_relation_edges"]
                ),
                "essential_evidence_count": len(row["essential_evidence_ids"]),
                "proof_depth": row["graph"]["proof_depth"],
                "answer_sha256": hashlib.sha256(row["answer"].encode()).hexdigest(),
                "audit": audit,
            }
            for row, audit in zip(rows, audits, strict=True)
        ],
        "cumulative_growth_errors": cumulative_errors,
        "train_ready": False,
        "production_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "signed": False,
    }
    _atomic_write(output_dir / "candidates.jsonl", candidate_bytes)
    _atomic_write(output_dir / "TASK_REPLAY_SIDECAR.json", sidecar_bytes)
    _atomic_write(output_dir / "REPLAY_PATH_REGISTRY.json", replay_registry_bytes)
    _atomic_write(
        output_dir / "MANIFEST.json", _canonical_bytes(manifest_output) + b"\n"
    )
    return manifest_output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = materialize(args.config, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
