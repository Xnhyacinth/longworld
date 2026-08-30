#!/usr/bin/env python3
"""Export raw, provenance-coherent workflow documents for continued pretraining."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.provenance import ProvenanceError, SourceLineage
from longworld.core.realworkflow import RealWorkflow
from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES

COHERENT_WORKFLOW_KINDS = {
    "real_source_derived",
}
ALLOWED_COMPOSITIONS = {"provenance_graph", "causal_timeline", "same_case_dossier"}
CAUSAL_ROLES = {"causal_gold", "causal_supporting"}
SOURCE_ORIGINS = {
    "real_public",
    "real_private_export",
    "real_derived",
}
QA_PROMPT_RE = re.compile(
    r"(?mi)^\s*(?:question|assistant|system|human|gpt)\s*:\s*|"
    r"<\|im_start\|>|<\|im_end\|>|\[/?INST\]|"
    r"[\"']role[\"']\s*:\s*[\"'](?:assistant|user|system)[\"']"
)
MAX_JSONL_LINE_BYTES = 16_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_BULK_METADATA_FIELDS = {
    "base_workflow_id",
    "length_bucket",
    "tokenizer_context_tokens",
    "tokenizer_model_id",
    "tokenizer_revision",
    "tokenizer_asset_manifest_sha256",
    "source_record_count",
}
_LONGITUDINAL_METADATA_FIELDS = {
    "longitudinal_gate_revision",
    "minimum_source_event_count",
    "source_elapsed_seconds",
    "source_event_count",
}
_BULK_BANDS = EXACT_TOKEN_BAND_RANGES


def iter_jsonl(path: Path):
    with path.open("rb") as source:
        while line := source.readline(MAX_JSONL_LINE_BYTES + 1):
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise ValueError(f"JSONL line exceeds size limit: {path}")
            if line.strip():
                yield json.loads(line.decode("utf-8"))


def _reject_reason(row: dict) -> str | None:
    if not verify_attestation(
        row, attestation_key_from_env("cpt_row"), purpose="cpt_row"
    ):
        return "invalid_or_missing_attestation"
    if row.get("training_objective") != "cpt":
        return "not_cpt_objective"
    if row.get("composition_method") not in ALLOWED_COMPOSITIONS:
        return "random_concat_forbidden"
    if row.get("producer") != "realworkflow_to_cpt@1":
        return "untrusted_cpt_producer"
    lineage = row.get("source_lineage")
    if not isinstance(lineage, dict):
        return "missing_source_lineage"
    source_digest = str(row.get("source_export_digest") or "")
    if not source_digest or lineage.get("provenance_id") != source_digest:
        return "source_lineage_mismatch"
    try:
        validated_lineage = SourceLineage(
            provenance_id=str(lineage.get("provenance_id") or ""),
            url=str(lineage.get("url") or ""),
            license=str(lineage.get("license") or ""),
            retrieved_at=str(lineage.get("retrieved_at") or ""),
            parser=str(lineage.get("parser") or ""),
            sha256=str(lineage.get("sha256") or ""),
            revision=str(lineage.get("revision") or ""),
            source_path=str(lineage.get("source_path") or ""),
        )
    except ProvenanceError:
        return "invalid_source_lineage"
    if not validated_lineage.url.startswith("https://") or not (
        validated_lineage.revision
    ):
        return "invalid_source_lineage"
    classes = row.get("artifact_classification")
    workflow_ids = {str(value) for value in row.get("workflow_ids") or [] if value}
    if not isinstance(classes, list) or len(classes) < 2 or len(workflow_ids) != 1:
        return "incoherent_workflow"
    workflow_id = next(iter(workflow_ids))
    if any(
        not isinstance(item, dict)
        or item.get("workflow_id") != workflow_id
        or item.get("workflow_kind") not in COHERENT_WORKFLOW_KINDS
        or item.get("evidence_role") not in CAUSAL_ROLES
        or item.get("source_origin") not in SOURCE_ORIGINS
        or not str(item.get("provenance_id") or "")
        for item in classes
    ):
        return "incoherent_workflow"
    if any(item.get("provenance_id") != source_digest for item in classes):
        return "source_classification_mismatch"
    records = row.get("workflow_records")
    if not isinstance(records, list) or len(records) < 2:
        return "missing_workflow_records"
    class_ids = {
        str(item.get("artifact_id") or "") for item in classes if isinstance(item, dict)
    }
    record_ids = {
        str(record.get("record_id") or "")
        for record in records
        if isinstance(record, dict)
    }
    if "" in record_ids or record_ids != class_ids or len(record_ids) != len(records):
        return "workflow_record_mismatch"
    present_bulk_fields = _BULK_METADATA_FIELDS.intersection(row)
    if present_bulk_fields:
        if present_bulk_fields != _BULK_METADATA_FIELDS:
            return "incomplete_bulk_metadata"
        bucket = str(row.get("length_bucket") or "")
        token_range = _BULK_BANDS.get(bucket)
        context_tokens = row.get("tokenizer_context_tokens")
        if (
            token_range is None
            or not isinstance(context_tokens, int)
            or not token_range[0] <= context_tokens <= token_range[1]
            or not str(row.get("base_workflow_id") or "")
            or not str(row.get("tokenizer_model_id") or "")
            or _COMMIT_SHA.fullmatch(str(row.get("tokenizer_revision") or "")) is None
            or _SHA256.fullmatch(str(row.get("tokenizer_asset_manifest_sha256") or ""))
            is None
            or row.get("source_record_count") != len(records)
        ):
            return "invalid_bulk_metadata"
    present_longitudinal_fields = _LONGITUDINAL_METADATA_FIELDS.intersection(row)
    if present_longitudinal_fields:
        if present_longitudinal_fields != _LONGITUDINAL_METADATA_FIELDS:
            return "incomplete_longitudinal_metadata"
        event_count = row.get("source_event_count")
        minimum_event_count = row.get("minimum_source_event_count")
        elapsed_seconds = row.get("source_elapsed_seconds")
        if (
            not isinstance(event_count, int)
            or not isinstance(minimum_event_count, int)
            or not isinstance(elapsed_seconds, int)
            or minimum_event_count <= 0
            or event_count < minimum_event_count
            or elapsed_seconds < 0
            or row.get("longitudinal_gate_revision") != "git-distinct-commit-v1"
        ):
            return "invalid_longitudinal_metadata"

    seen: set[str] = set()
    previous_time: datetime | None = None
    texts: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("workflow_id") != workflow_id:
            return "workflow_record_mismatch"
        if not str(record.get("source_pointer") or "") or not str(
            record.get("kind") or ""
        ):
            return "workflow_record_mismatch"
        text = str(record.get("text") or "")
        if not text.strip():
            return "empty_workflow_record"
        if QA_PROMPT_RE.search(text):
            return "qa_prompt_contamination"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if record.get("sha256") != digest:
            return "record_hash_mismatch"
        try:
            occurred_at = datetime.fromisoformat(
                str(record.get("occurred_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            return "invalid_workflow_time"
        if occurred_at.tzinfo is None:
            return "invalid_workflow_time"
        if previous_time is not None and occurred_at < previous_time:
            return "workflow_not_chronological"
        previous_time = occurred_at
        predecessors = record.get("predecessor_ids")
        if not isinstance(predecessors, list):
            return "invalid_workflow_edges"
        predecessor_ids = {str(value) for value in predecessors if value}
        if not predecessor_ids.issubset(seen):
            return "invalid_workflow_edges"
        if index and not predecessor_ids:
            return "disconnected_workflow"
        if not index and predecessor_ids:
            return "invalid_workflow_edges"
        seen.add(str(record["record_id"]))
        texts.append(text)

    reconstructed = "\n\n".join(texts)
    if not str(row.get("document_context") or "").strip():
        return "empty_document_context"
    if str(row["document_context"]) != reconstructed:
        return "document_context_mismatch"
    return None


def cpt_row_from_workflow(
    workflow: RealWorkflow, *, attestation_key: bytes | None = None
) -> dict:
    """Build the only accepted CPT row shape from a validated RealWorkflow."""
    key = attestation_key or attestation_key_from_env("cpt_row")
    if key is None:
        raise ValueError("CPT row production requires an attestation key")
    records = [
        {
            "record_id": record.record_id,
            "kind": record.kind,
            "occurred_at": record.occurred_at,
            "text": record.text,
            "predecessor_ids": list(record.links),
            "workflow_id": workflow.workflow_id,
            "source_pointer": record.source_pointer,
            "sha256": hashlib.sha256(record.text.encode()).hexdigest(),
        }
        for record in workflow.records
    ]
    row = {
        "producer": "realworkflow_to_cpt@1",
        "document_context": "\n\n".join(record.text for record in workflow.records),
        "training_objective": "cpt",
        "composition_method": "provenance_graph",
        "workflow_ids": [workflow.workflow_id],
        "source_export_digest": workflow.lineage.provenance_id,
        "source_lineage": {
            "provenance_id": workflow.lineage.provenance_id,
            "url": workflow.lineage.url,
            "license": workflow.lineage.license,
            "retrieved_at": workflow.lineage.retrieved_at,
            "revision": workflow.lineage.revision,
            "parser": workflow.lineage.parser,
            "sha256": workflow.lineage.sha256,
            "source_path": workflow.lineage.source_path,
        },
        "artifact_classification": [
            {
                "artifact_id": record.record_id,
                "workflow_id": workflow.workflow_id,
                "workflow_kind": "real_source_derived",
                "evidence_role": (
                    "causal_gold" if index == len(records) - 1 else "causal_supporting"
                ),
                "source_origin": workflow.source_origin.value,
                "provenance_id": workflow.lineage.provenance_id,
            }
            for index, record in enumerate(workflow.records)
        ],
        "workflow_records": records,
    }
    return attach_attestation(row, key, purpose="cpt_row")


def export_cpt_rows(rows: Iterable[dict], destination: Path) -> dict:
    """Write exact-deduplicated raw documents; never export an SFT prompt."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    rejects: Counter = Counter()
    seen: set[str] = set()
    exported_count = 0
    duplicates = 0
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as output:
            temporary_name = output.name
            for row in rows:
                reason = _reject_reason(row)
                if reason:
                    rejects[reason] += 1
                    continue
                document = str(row["document_context"])
                digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
                if digest in seen:
                    duplicates += 1
                    continue
                seen.add(digest)
                metadata = {
                    "workflow_id": row["workflow_ids"][0],
                    "composition_method": row["composition_method"],
                }
                if _BULK_METADATA_FIELDS.issubset(row):
                    metadata.update(
                        {
                            field: row[field]
                            for field in (
                                "base_workflow_id",
                                "length_bucket",
                                "tokenizer_context_tokens",
                                "tokenizer_model_id",
                                "tokenizer_revision",
                                "tokenizer_asset_manifest_sha256",
                                "source_record_count",
                            )
                        }
                    )
                    metadata["source_export_digest"] = row["source_export_digest"]
                if _LONGITUDINAL_METADATA_FIELDS.issubset(row):
                    metadata.update(
                        {
                            field: row[field]
                            for field in (
                                "longitudinal_gate_revision",
                                "minimum_source_event_count",
                                "source_elapsed_seconds",
                                "source_event_count",
                            )
                        }
                    )
                output.write(
                    json.dumps(
                        {"text": document, "metadata": metadata},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                exported_count += 1
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {
        "n_exported": exported_count,
        "n_duplicates": duplicates,
        "reject_reasons": dict(rejects),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "cpt" / "train.jsonl"
    )
    args = parser.parse_args()
    report = export_cpt_rows(iter_jsonl(args.data), args.output)
    print(json.dumps(report, indent=2))
    if not report["n_exported"]:
        raise SystemExit("CPT export produced no coherent workflow documents")


if __name__ == "__main__":
    main()
