#!/usr/bin/env python3
"""Materialize audited nonproduction BEA macro-vintage history candidates."""

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
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    attestation_key_from_env,
    sanitized_attestation_environment,
)
from longworld.core.macrovintage import (
    audit_macro_vintage_pipeline_candidate,
    build_macro_vintage_pipeline_candidates,
)
from longworld.core.macrovintageworkflow import (
    MACRO_PACKING_PLAN_PURPOSE,
    MACRO_REFERENCE_INDEX_PURPOSE,
    MACRO_REMOTE_SOURCE_RECEIPT_PURPOSE,
    MACRO_VINTAGE_PARSER_REVISION,
    MAX_MACRO_VINTAGE_WORKFLOW_MANIFEST_BYTES,
    MAX_XLSX_BYTES,
    audit_macro_vintage_workflow_manifest,
    read_macro_packing_plan,
    read_macro_remote_source_receipt,
    sign_macro_packing_plan,
    sign_macro_reference_index,
    sign_macro_remote_source_receipt,
)
from longworld.core.provenance import (
    ProvenanceError,
    _read_regular_file,
)
from longworld.core.taskreplaysidecar import (
    MACRO_VINTAGE_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

MAX_CONFIG_BYTES = 256_000


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProvenanceError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ProvenanceError(f"non-finite JSON value: {value}")


def _require_combined_local_probe() -> None:
    if (
        os.environ.get(ATTESTATION_ENVIRONMENT_ENV, "").strip().lower()
        != "probe"
        or os.environ.get(LOCAL_PROBE_COMBINED_ROLES_ENV, "").strip()
        != LOCAL_PROBE_TRUST_ISOLATION_VALUE
    ):
        raise ProvenanceError(
            "Macro combined-role cache path is local-probe diagnostic only"
        )


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
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


def _build_source_sidecar(
    rows: list[dict[str, Any]], workflow: dict[str, Any], source_key: bytes
) -> tuple[bytes, dict[str, str], bytes]:
    if not rows:
        raise ProvenanceError("Macro source sidecar requires candidate rows")
    source_binding = rows[0].get("source_binding")
    tokenizer_identity = (
        rows[0].get("tokenizer_model_id"),
        rows[0].get("tokenizer_revision"),
        rows[0].get("tokenizer_asset_manifest_sha256"),
    )
    if (
        not isinstance(source_binding, dict)
        or any(row.get("source_binding") != source_binding for row in rows[1:])
        or any(
            (
                row.get("tokenizer_model_id"),
                row.get("tokenizer_revision"),
                row.get("tokenizer_asset_manifest_sha256"),
            )
            != tokenizer_identity
            for row in rows[1:]
        )
    ):
        raise ProvenanceError("Macro source sidecar identity is inconsistent")
    commitments = sorted(
        (task_candidate_content_commitment(row) for row in rows),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    sidecar = build_task_replay_sidecar(
        adapter_id=MACRO_VINTAGE_TASK_REPLAY_ADAPTER[0],
        adapter_revision=MACRO_VINTAGE_TASK_REPLAY_ADAPTER[1],
        replay_payload={
            "workflow_manifest_sha256": source_binding.get(
                "workflow_manifest_sha256"
            ),
            "raw_source_sha256": source_binding.get("raw_source_sha256"),
            "fetch_inventory_sha256": source_binding.get(
                "fetch_inventory_sha256"
            ),
            "fetch_receipt": workflow.get("fetch_receipt"),
            "source_families": source_binding.get("source_families"),
            "authorization_record_id": source_binding.get(
                "authorization_record_id"
            ),
            "replay_revision": MACRO_VINTAGE_TASK_REPLAY_ADAPTER[1],
            "tokenizer_model_id": tokenizer_identity[0],
            "tokenizer_revision": tokenizer_identity[1],
            "tokenizer_asset_manifest_sha256": tokenizer_identity[2],
            "candidate_content_commitments": commitments,
        },
        source_attestation_key=source_key,
    )
    sidecar_bytes = _canonical_bytes(sidecar)
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_bytes, source_attestation_key=source_key
    )
    registry_bytes = _canonical_bytes(
        {
            "schema_version": "longworld.replay-path-registry.v2",
            "episode_replay_bundles": {},
            "source_workflow_bundles": {},
            "task_replay_sidecars": {
                sidecar_binding["sha256"]: "TASK_REPLAY_SIDECAR.json"
            },
        }
    )
    return sidecar_bytes, sidecar_binding, registry_bytes


def _read_json(path: Path, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_regular_file(path, max_bytes)
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot load JSON object {path.name}") from error
    if not isinstance(value, dict):
        raise ProvenanceError(f"JSON source {path.name} must be an object")
    return value, raw


def _resolve_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("macro vintage materialization path is invalid")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_tokenizer(model_id: str, revision: str) -> Any:
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=False,
            local_files_only=True,
        )


def _pipeline_source_binding(
    workflow: dict[str, Any], workflow_manifest_sha256: str
) -> dict[str, Any]:
    fetch_receipt = workflow.get("fetch_receipt")
    authorization = workflow.get("authorization")
    if not isinstance(fetch_receipt, dict) or not isinstance(authorization, dict):
        raise ProvenanceError("Macro workflow source identity is invalid")
    return {
        "workflow_manifest_sha256": workflow_manifest_sha256,
        "raw_source_sha256": str(workflow.get("raw_source_sha256") or ""),
        "fetch_inventory_sha256": str(
            workflow.get("fetch_inventory_sha256") or ""
        ),
        "fetch_receipt_sha256": hashlib.sha256(
            json.dumps(
                fetch_receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "source_families": ["bea_gdp_gdi_vintage_workbook"],
        "authorization_record_id": str(authorization.get("record_id") or ""),
        "parser_revision": MACRO_VINTAGE_PARSER_REVISION,
    }


def _load_cached_packing_plan(
    *,
    output_dir: Path,
    source_binding: dict[str, Any],
    fetch_receipt: dict[str, Any],
) -> dict[str, Any] | None:
    packing_path = output_dir / "MACRO_PACKING_PLAN.json"
    if not packing_path.exists():
        return None
    source_key = attestation_key_from_env(MACRO_REMOTE_SOURCE_RECEIPT_PURPOSE)
    promotion_key = attestation_key_from_env(MACRO_PACKING_PLAN_PURPOSE)
    if source_key is None or promotion_key is None:
        raise ProvenanceError("Macro packing cache requires source and promotion keys")
    receipt = read_macro_remote_source_receipt(
        output_dir / "MACRO_REMOTE_SOURCE_RECEIPT.json",
        expected_source_binding=source_binding,
        expected_fetch_receipt=fetch_receipt,
        key=source_key,
    )
    return read_macro_packing_plan(
        packing_path,
        expected_rows=None,
        expected_remote_source_receipt_sha256=receipt["receipt_sha256"],
        key=promotion_key,
    )["packing_plan"]


def materialize_macro_execution_caches(
    rows: list[dict[str, Any]], *, workflow: dict[str, Any], output_dir: Path
) -> dict[str, str]:
    """Write role-separated acceleration metadata without any final gate result."""
    _require_combined_local_probe()
    if not rows or not isinstance(rows[0].get("source_binding"), dict):
        raise ProvenanceError("Macro cache materialization requires candidate rows")
    source_key = attestation_key_from_env(MACRO_REMOTE_SOURCE_RECEIPT_PURPOSE)
    promotion_key = attestation_key_from_env(MACRO_PACKING_PLAN_PURPOSE)
    report_key = attestation_key_from_env(MACRO_REFERENCE_INDEX_PURPOSE)
    if source_key is None or promotion_key is None or report_key is None:
        raise ProvenanceError("Macro cache materialization requires three role keys")

    source_receipt = sign_macro_remote_source_receipt(
        source_binding=rows[0]["source_binding"],
        fetch_receipt=workflow.get("fetch_receipt") or {},
        exported_at=str(workflow.get("generated_at") or ""),
        key=source_key,
    )
    source_receipt_bytes = _canonical_bytes(source_receipt)
    source_receipt_sha256 = hashlib.sha256(source_receipt_bytes).hexdigest()
    packing_plan = sign_macro_packing_plan(
        rows,
        remote_source_receipt_sha256=source_receipt_sha256,
        key=promotion_key,
    )
    packing_plan_bytes = _canonical_bytes(packing_plan)
    packing_plan_sha256 = hashlib.sha256(packing_plan_bytes).hexdigest()
    reference_index = sign_macro_reference_index(
        rows,
        packing_plan_sha256=packing_plan_sha256,
        remote_source_receipt_sha256=source_receipt_sha256,
        key=report_key,
    )
    reference_index_bytes = _canonical_bytes(reference_index)

    _atomic_write(
        output_dir / "MACRO_REMOTE_SOURCE_RECEIPT.json", source_receipt_bytes
    )
    _atomic_write(output_dir / "MACRO_PACKING_PLAN.json", packing_plan_bytes)
    _atomic_write(output_dir / "MACRO_REFERENCE_INDEX.json", reference_index_bytes)
    return {
        "remote_source_receipt_sha256": source_receipt_sha256,
        "packing_plan_sha256": packing_plan_sha256,
        "reference_index_sha256": hashlib.sha256(reference_index_bytes).hexdigest(),
    }


def materialize(config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Reaudit exact workbook bytes and write deterministic macro candidates."""
    _require_combined_local_probe()
    config, _config_raw = _read_json(config_path, MAX_CONFIG_BYTES)
    if config.get("schema_version") != "longworld.macro-vintage-materialization.v1":
        raise ProvenanceError("unsupported macro-vintage materialization config")
    workflow_path = _resolve_path(config.get("workflow_manifest"))
    source_path = _resolve_path(config.get("raw_source"))
    workflow, workflow_raw = _read_json(
        workflow_path, MAX_MACRO_VINTAGE_WORKFLOW_MANIFEST_BYTES
    )
    try:
        source_raw = _read_regular_file(source_path, MAX_XLSX_BYTES)
    except OSError as error:
        raise ProvenanceError("cannot read macro-vintage raw source") from error
    audit_macro_vintage_workflow_manifest(workflow, source_raw=source_raw)

    tokenizer_config = config.get("tokenizer")
    if not isinstance(tokenizer_config, dict):
        raise ProvenanceError("macro-vintage tokenizer config is missing")
    model_id = str(tokenizer_config.get("model_id") or "")
    revision = str(tokenizer_config.get("revision") or "")
    tokenizer = _load_tokenizer(model_id, revision)

    def token_counter(text: str) -> int:
        with sanitized_attestation_environment():
            return len(tokenizer.encode(text, add_special_tokens=False))

    raw_bands = config.get("bands")
    if not isinstance(raw_bands, list) or not raw_bands:
        raise ProvenanceError("macro-vintage bands are missing")
    bands = tuple(
        (
            str(value.get("name") or ""),
            int(value.get("lower_tokens") or 0),
            int(value.get("upper_tokens") or 0),
        )
        for value in raw_bands
        if isinstance(value, dict)
    )
    if len(bands) != len(raw_bands):
        raise ProvenanceError("macro-vintage band config is invalid")
    tokenizer_asset_sha256 = resolved_tokenizer_asset_manifest_sha256(
        model_id, revision
    )
    workflow_sha256 = hashlib.sha256(workflow_raw).hexdigest()
    rows = build_macro_vintage_pipeline_candidates(
        workflow,
        workflow_manifest_sha256=workflow_sha256,
        world_id=str(config.get("world_id") or ""),
        target_series_id=str(config.get("target_series_id") or ""),
        target_period=str(config.get("target_period") or ""),
        bands=bands,
        token_counter=token_counter,
        tokenizer_model_id=model_id,
        tokenizer_revision=revision,
        tokenizer_asset_manifest_sha256=tokenizer_asset_sha256,
        verified_packing_plan=_load_cached_packing_plan(
            output_dir=output_dir,
            source_binding=_pipeline_source_binding(workflow, workflow_sha256),
            fetch_receipt=dict(workflow.get("fetch_receipt") or {}),
        ),
    )
    packing_cache_hit = (output_dir / "MACRO_PACKING_PLAN.json").exists()
    for row in rows:
        row["source_verified_at_materialization"] = True
        row["source_attestation_verified"] = True
        row["real_source_verified"] = True
    source_key = attestation_key_from_env("task_replay_sidecar")
    if source_key is None:
        raise ProvenanceError("Macro materialization requires source role key")
    sidecar_bytes, sidecar_binding, registry_bytes = _build_source_sidecar(
        rows, workflow, source_key
    )
    for row in rows:
        row["task_replay_sidecar"] = dict(sidecar_binding)
        row["pipeline_capabilities"] = {
            **dict(row.get("pipeline_capabilities") or {}),
            "generic_strict_replay": True,
            "generic_promotion": True,
        }
        row["promotion_blocker_code"] = (
            "missing_candidate_attestation_and_dense_ranking"
        )
    audits = [audit_macro_vintage_pipeline_candidate(row) for row in rows]
    if not all(audit and all(audit.values()) for audit in audits):
        raise ProvenanceError("macro-vintage candidate audit failed")
    cache_bindings = materialize_macro_execution_caches(
        rows, workflow=workflow, output_dir=output_dir
    )
    candidate_bytes = b"".join(_canonical_bytes(row) for row in rows)
    report: dict[str, Any] = {
        "schema_version": "longworld.macro-vintage-candidate-manifest.v1",
        "data_stage": "candidate_history_audit",
        "promotion_status": "blocked_missing_candidate_attestation_and_dense_promotion",
        "world_id": str(config.get("world_id") or ""),
        "domain": "macro_economics",
        "attempts": len(rows),
        "accepted_candidates": len(rows),
        "retention": 1.0,
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "workflow_manifest_sha256": workflow_sha256,
        "raw_source_sha256": hashlib.sha256(source_raw).hexdigest(),
        "source_inventory_reaudited": True,
        "source_attestation_verified": True,
        "source_replay_sidecar_attested": True,
        "cache_bindings": cache_bindings,
        "packing_cache_hit": packing_cache_hit,
        "cache_restored_final_gate": False,
        "task_replay_sidecar": sidecar_binding,
        "exact_token_counts_recomputed": True,
        "tokenizer_model_id": model_id,
        "tokenizer_revision": revision,
        "tokenizer_asset_manifest_sha256": tokenizer_asset_sha256,
        "rows": [
            {
                "length_bucket": row["length_bucket"],
                "context_tokens": row["tokenizer_context_tokens"],
                "source_record_count": len(row["source_record_ids"]),
                "source_relation_count": len(row["source_relation_ids"]),
                "essential_evidence_count": len(row["essential_artifact_ids"]),
                "proof_depth": row["graph"]["proof_depth"],
                "answer_sha256": hashlib.sha256(row["answer"].encode()).hexdigest(),
                "audit": audit,
            }
            for row, audit in zip(rows, audits, strict=True)
        ],
        "train_ready": False,
        "production_eligible": False,
        "complete_world": False,
        "promoted": False,
        "generation_integration": "macro_pipeline_candidate",
    }
    _atomic_write(output_dir / "candidates.jsonl", candidate_bytes)
    _atomic_write(output_dir / "TASK_REPLAY_SIDECAR.json", sidecar_bytes)
    _atomic_write(output_dir / "REPLAY_PATH_REGISTRY.json", registry_bytes)
    _atomic_write(output_dir / "MANIFEST.json", _canonical_bytes(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            materialize(args.config, args.output_dir),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
