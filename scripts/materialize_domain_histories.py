#!/usr/bin/env python3
"""Materialize source-bound cumulative domain-history candidates."""

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
from longworld.core.clinicalworkflow import audit_clinical_trial_approval_task
from longworld.core.cyberworkflow import load_cyber_workflow_manifest
from longworld.core.domainhistory import (
    HistoryBand,
    audit_cumulative_history,
    audit_kev_catalog_history_candidate,
    audit_kev_pipeline_candidate,
    build_kev_catalog_history_candidates,
    build_kev_pipeline_candidate,
    build_kev_pipeline_replay_manifest,
    kev_pipeline_replay_manifest_binding,
    verify_bound_json_retrieval,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from longworld.core.regulationworkflow import audit_regulation_rulemaking_task
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

MAX_CONFIG_BYTES = 256_000
MAX_CANDIDATE_BYTES = 8_000_000


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


def _resolve_config_path(config_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("history config path is invalid")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _load_tokenizer(model_id: str, revision: str):
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
        )


def _capacity_check(
    candidate_path: Path,
    *,
    domain: str,
    key: bytes,
    token_counter,
    minimum_tokens: int,
) -> dict[str, Any]:
    candidate_raw = _read_regular_file(candidate_path, MAX_CANDIDATE_BYTES)
    try:
        task = json.loads(candidate_raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot load candidate {candidate_path.name}") from error
    if not isinstance(task, dict):
        raise ProvenanceError(f"candidate {candidate_path.name} must be an object")
    if domain == "clinical":
        audit = audit_clinical_trial_approval_task(task, source_attestation_key=key)
    elif domain == "regulation":
        audit = audit_regulation_rulemaking_task(task, source_attestation_key=key)
    else:
        raise ProvenanceError(f"unsupported capacity-check domain: {domain}")
    if not audit or not all(audit.values()):
        raise ProvenanceError(f"{domain} executable task failed source audit")
    records = task.get("source_records")
    if not isinstance(records, list) or not records:
        manifest = task.get("source_manifest")
        records = manifest.get("records") if isinstance(manifest, dict) else None
    if not isinstance(records, list) or not records:
        raise ProvenanceError(f"{domain} task has no source records")
    record_ids: list[str] = []
    bodies: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise ProvenanceError(f"{domain} task source record is invalid")
        record_id = str(record.get("record_id") or "")
        text = record.get("text")
        if not record_id or not isinstance(text, str) or not text:
            raise ProvenanceError(f"{domain} task source record is incomplete")
        record_ids.append(record_id)
        bodies.append(text)
    if len(record_ids) != len(set(record_ids)):
        raise ProvenanceError(f"{domain} task repeats source records")
    body_hashes = [hashlib.sha256(body.encode()).hexdigest() for body in bodies]
    if len(body_hashes) != len(set(body_hashes)):
        raise ProvenanceError(f"{domain} task repeats source bodies")
    tokens = token_counter("\n".join(bodies))
    return {
        "schema_version": "longworld.domain-history-capacity-reject.v1",
        "domain": domain,
        "candidate_path": str(candidate_path.relative_to(ROOT)),
        "candidate_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "source_record_count": len(record_ids),
        "unique_source_body_count": len(body_hashes),
        "source_body_tokens": tokens,
        "required_lower_band_tokens": minimum_tokens,
        "reason": (
            "insufficient_verified_source_tokens"
            if tokens < minimum_tokens
            else "source_capacity_available"
        ),
        "audit": audit,
        "train_ready": False,
        "complete_world": False,
    }


def materialize(config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Run source verification, exact packing, executable audits and receipts."""
    config = _read_json(config_path, MAX_CONFIG_BYTES)
    if config.get("schema_version") != "longworld.domain-history-materialization.v1":
        raise ProvenanceError("unsupported domain-history materialization config")
    key = attestation_key_from_env("source_manifest")
    if key is None:
        raise ProvenanceError("domain-history materialization requires source key")
    tokenizer_config = config.get("tokenizer")
    if not isinstance(tokenizer_config, dict):
        raise ProvenanceError("domain-history tokenizer config is missing")
    model_id = str(tokenizer_config.get("model_id") or "")
    revision = str(tokenizer_config.get("revision") or "")
    tokenizer = _load_tokenizer(model_id, revision)

    def token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    bands_value = config.get("bands")
    if not isinstance(bands_value, list) or not bands_value:
        raise ProvenanceError("domain-history bands are missing")
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
        raise ProvenanceError("domain-history band config is invalid")

    cyber = config.get("cyber_kev_history")
    if not isinstance(cyber, dict):
        raise ProvenanceError("cyber KEV history config is missing")
    manifest_path = _resolve_config_path(config_path, cyber.get("signed_manifest"))
    catalog_path = _resolve_config_path(config_path, cyber.get("catalog_response"))
    signed_manifest_raw = _read_regular_file(manifest_path, MAX_CANDIDATE_BYTES)
    manifest = load_cyber_workflow_manifest(manifest_path, attestation_key=key)
    receipt = manifest.get("fetch_receipt")
    retrievals = receipt.get("retrievals") if isinstance(receipt, dict) else None
    matching = [
        item
        for item in retrievals or []
        if isinstance(item, dict) and item.get("kind") == "cisa_kev"
    ]
    if len(matching) != 1:
        raise ProvenanceError("signed cyber receipt does not select one CISA catalog")
    retrieval = matching[0]
    catalog = verify_bound_json_retrieval(catalog_path, retrieval)
    source_binding = {
        "source_url": retrieval["final_url"],
        "observed_at": retrieval["observed_at"],
        "retrieval_sha256": retrieval["sha256"],
        "signed_manifest_sha256": hashlib.sha256(signed_manifest_raw).hexdigest(),
    }
    rows = build_kev_catalog_history_candidates(
        catalog,
        world_id=str(cyber.get("world_id") or ""),
        source_binding=source_binding,
        bands=bands,
        token_counter=token_counter,
        tokenizer_model_id=model_id,
        tokenizer_revision=revision,
    )
    candidate_lines = [_canonical_bytes(row) for row in rows]
    candidate_bytes = b"\n".join(candidate_lines) + b"\n"

    checks = config.get("capacity_checks")
    if not isinstance(checks, list):
        raise ProvenanceError("domain-history capacity checks are missing")
    capacity_results = [
        _capacity_check(
            _resolve_config_path(config_path, item.get("candidate")),
            domain=str(item.get("domain") or ""),
            key=key,
            token_counter=token_counter,
            minimum_tokens=bands[0].lower_tokens,
        )
        for item in checks
        if isinstance(item, dict)
    ]
    if len(capacity_results) != len(checks):
        raise ProvenanceError("domain-history capacity check config is invalid")

    row_audits = [audit_kev_catalog_history_candidate(row) for row in rows]
    cumulative_errors = audit_cumulative_history(rows)
    accepted = sum(1 for audit in row_audits if audit and all(audit.values()))
    asset_digest = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    pipeline_config = config.get("pipeline_candidate_export")
    if pipeline_config is None:
        pipeline_config = {"enabled": False}
    if not isinstance(pipeline_config, dict) or not isinstance(
        pipeline_config.get("enabled"), bool
    ):
        raise ProvenanceError("pipeline candidate export config is invalid")
    pipeline_rows: list[dict[str, Any]] = []
    pipeline_audits: list[dict[str, bool]] = []
    pipeline_bytes = b""
    replay_manifest_bytes = b""
    pipeline_output: dict[str, Any] | None = None
    if pipeline_config["enabled"]:
        document_shards = pipeline_config.get("document_shards")
        if (
            isinstance(document_shards, bool)
            or not isinstance(document_shards, int)
            or document_shards != 4
        ):
            raise ProvenanceError("pipeline export requires exactly four shards")
        candidate_key = attestation_key_from_env("candidate_row")
        if candidate_key is None:
            raise ProvenanceError(
                "pipeline candidate export requires candidate attestation key"
            )
        replay_manifest = build_kev_pipeline_replay_manifest(
            source_binding=source_binding,
            source_manifest_name=manifest_path.name,
            source_response_name=catalog_path.name,
            tokenizer_model_id=model_id,
            tokenizer_revision=revision,
            tokenizer_asset_manifest_sha256=asset_digest,
            source_attestation_key=key,
        )
        replay_manifest_bytes = _canonical_bytes(replay_manifest) + b"\n"
        replay_binding = kev_pipeline_replay_manifest_binding(
            replay_manifest_bytes, key
        )
        pipeline_rows = [
            build_kev_pipeline_candidate(
                row,
                token_counter=token_counter,
                tokenizer_asset_manifest_sha256=asset_digest,
                replay_manifest_binding=replay_binding,
                candidate_attestation_key=candidate_key,
                document_shards=document_shards,
            )
            for row in rows
        ]
        pipeline_audits = [
            audit_kev_pipeline_candidate(row, token_counter=token_counter)
            for row in pipeline_rows
        ]
        pipeline_accepted = sum(
            1 for audit in pipeline_audits if audit and all(audit.values())
        )
        if pipeline_accepted != len(pipeline_rows):
            raise ProvenanceError("KEV pipeline candidate export audit failed")
        pipeline_bytes = (
            b"\n".join(_canonical_bytes(row) for row in pipeline_rows) + b"\n"
        )
        pipeline_output = {
            "status": "ranker_ready_promotion_adapter_pending",
            "promotion_adapter_available": False,
            "promotion_blocker_code": "missing_domain_replay_adapter:cyber",
            "attempts": len(pipeline_rows),
            "accepted_candidates": pipeline_accepted,
            "retention": pipeline_accepted / max(1, len(pipeline_rows)),
            "candidate_sha256": hashlib.sha256(pipeline_bytes).hexdigest(),
            "replay_manifest_sha256": hashlib.sha256(replay_manifest_bytes).hexdigest(),
            "source_attestation_verified": True,
            "candidate_attestation_present": True,
            "strict_replay_verified": True,
            "rows": [
                {
                    "length_bucket": row["length_bucket"],
                    "tokenizer_context_tokens": row["tokenizer_context_tokens"],
                    "artifact_count": len(row["artifact_classification"]),
                    "source_record_count": len(row["source_record_ids"]),
                    "source_relation_count": len(row["source_relation_ids"]),
                    "audit": audit,
                }
                for row, audit in zip(pipeline_rows, pipeline_audits, strict=True)
            ],
            "train_ready": False,
            "production_eligible": False,
            "complete_world": False,
            "promoted": False,
        }
    manifest_output: dict[str, Any] = {
        "schema_version": "longworld.domain-history-candidate-manifest.v1",
        "data_stage": "candidate_history_audit",
        "promotion_status": "ignored_non_world_candidates",
        "world_id": rows[0]["world_id"],
        "domain": "cyber",
        "attempts": len(rows),
        "capacity_check_attempts": len(capacity_results),
        "accepted_candidates": accepted,
        "rejected_capacity_checks": sum(
            item["reason"] == "insufficient_verified_source_tokens"
            for item in capacity_results
        ),
        "retention": accepted / max(1, len(rows)),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "source_manifest_sha256": source_binding["signed_manifest_sha256"],
        "source_response_sha256": source_binding["retrieval_sha256"],
        "source_attestation_verified": True,
        "source_response_digest_verified": True,
        "exact_token_counts_recomputed": True,
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": asset_digest,
        "rows": [
            {
                "length_bucket": row["length_bucket"],
                "context_tokens": row["tokenizer_context_tokens"],
                "context_sha256": row["context_sha256"],
                "source_record_count": len(row["source_record_ids"]),
                "source_relation_count": len(row["source_relation_ids"]),
                "authentic_source_relation_count": len(
                    row["authentic_source_relation_edges"]
                ),
                "verified_derived_order_relation_count": len(
                    row["verified_derived_order_relation_edges"]
                ),
                "essential_evidence_count": len(row["essential_evidence_ids"]),
                "strict_support_event_count": row["strict_support_event_count"],
                "proof_depth": row["graph"]["proof_depth"],
                "answer_sha256": hashlib.sha256(row["answer"].encode()).hexdigest(),
                "audit": audit,
            }
            for row, audit in zip(rows, row_audits, strict=True)
        ],
        "cumulative_growth_errors": cumulative_errors,
        "capacity_checks": capacity_results,
        "train_ready": False,
        "production_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "disabled",
        "signed": False,
    }
    if pipeline_output is not None:
        manifest_output["pipeline_export"] = pipeline_output
    if accepted != len(rows) or cumulative_errors:
        raise ProvenanceError("domain-history executable audit failed")
    rejects_bytes = b"".join(
        _canonical_bytes(item) + b"\n"
        for item in capacity_results
        if item["reason"] != "source_capacity_available"
    )
    _atomic_write(output_dir / "candidates.jsonl", candidate_bytes)
    _atomic_write(output_dir / "capacity_rejects.jsonl", rejects_bytes)
    if pipeline_output is not None:
        _atomic_write(output_dir / "pipeline_candidates.jsonl", pipeline_bytes)
        _atomic_write(output_dir / "KEV_REPLAY_MANIFEST.json", replay_manifest_bytes)
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
