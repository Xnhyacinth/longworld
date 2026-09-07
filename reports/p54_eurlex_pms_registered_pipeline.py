"""Build one source-attested 32K EUR-Lex diagnostic parent, without promotion."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.eurlexworkflow import (
    EURLEX_PMS_ANSWER_PROGRAM,
    EURLEX_PMS_REPLAY_REVISION,
    EURLEX_PMS_TASK_SCHEMA,
    audit_eurlex_pms_candidate,
    eurlex_canonical_json,
    extract_pms_chain,
    replay_eurlex_pms_candidate,
    replay_pms_chain,
)
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.render import Artifact
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.taskpromotion import _canonical_task_identifiers
from longworld.core.taskreplaysidecar import (
    EURLEX_PMS_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from reports.p54_eurlex_source_span_oracle import _digest, _geometry, _write

CONFIG = Path("configs/p54_eurlex_pms_registered_v2.json")


def _bytes(value: object) -> bytes:
    return (eurlex_canonical_json(value) + "\n").encode()


def _persist(path: Path, raw: bytes) -> None:
    if path.exists() and path.read_bytes() != raw:
        raise ValueError(f"refusing to replace frozen P54 output: {path}")
    path.write_bytes(raw)


def run(config_path: Path) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    source_key = attestation_key_from_env("source_manifest")
    candidate_key = attestation_key_from_env("candidate_row")
    if source_key is None or candidate_key is None:
        raise ValueError("P54 parent generation requires source and candidate roles")
    raw, sources = {}, {}
    for source in config["sources"]:
        body = (
            Path(config["raw_source_directory"]) / f"{source['source_id']}.raw"
        ).read_bytes()
        if (
            _digest(body) != source["expected_sha256"]
            or len(body) != source["expected_bytes"]
        ):
            raise ValueError("P54 source bytes differ from the frozen receipt")
        raw[source["source_id"]] = body
        sources[source["source_id"]] = {
            "url": source["url"],
            "sha256": _digest(body),
            "bytes": len(body),
            "raw_utf8": body.decode(),
        }
    directory = Path(config["source_directory"])
    directory.mkdir(parents=True, exist_ok=True)
    spans = extract_pms_chain(raw["act"])
    tok = config["tokenizer"]
    if (
        resolved_tokenizer_asset_manifest_sha256(tok["model_id"], tok["revision"])
        != tok["asset_manifest_sha256"]
    ):
        raise ValueError("P54 tokenizer assets changed")
    tokenizer = AutoTokenizer.from_pretrained(
        tok["model_id"],
        revision=tok["revision"],
        local_files_only=True,
        trust_remote_code=False,
    )

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    geometry, artifacts = _geometry(
        config,
        raw,
        spans,
        tokenizer,
        replay=replay_pms_chain,
        cf_span_sha256=spans[-1]["raw_span_sha256"],
    )
    geometry["counterfactual"] = (
        "synthetic dataset visibility intervention: retain the terminal source "
        "record identity, withhold its body, and expose source_body_withheld=true; "
        "the official law and source are unchanged"
    )
    geometry["shared_counterfactual_materializer_registered"] = True
    if not geometry["all_views_in_32k_band"]:
        raise ValueError("P54 canonical source record geometry cannot fit 32K")
    source_receipt = attach_attestation(
        {
            "schema_version": "longworld.eurlex-source-receipt.v1",
            "data_stage": "source_inventory",
            "authorization_record_id": config["authorization"]["record_id"],
            "preflight_config_sha256": _digest(config_raw),
            "sources": sources,
            "train_ready": False,
            "production_eligible": False,
        },
        source_key,
        purpose="source_manifest",
    )
    source_raw = _bytes(source_receipt)
    source_sha = _digest(source_raw)
    task = {
        "schema_version": EURLEX_PMS_TASK_SCHEMA,
        "answer_program_id": EURLEX_PMS_ANSWER_PROGRAM,
        "question": config["task"]["question"],
        "source_receipt_sha256": source_sha,
        "artifacts": [
            {"artifact_id": item["artifact_id"], "source_span": item["source_span"]}
            for item in artifacts
        ],
        "essential_artifact_ids": [f"act:{span['byte_start']}" for span in spans],
        "cf_artifact_id": f"act:{spans[-1]['byte_start']}",
    }
    essential = set(task["essential_artifact_ids"])
    world_id = "p54-eurlex-mdr-pms-risk-control-v1"
    classifications = [
        {
            "artifact_id": item["artifact_id"],
            "source_record_id": item["artifact_id"],
            "source_origin": "real_public",
            "workflow_kind": "real_source_derived",
            "workflow_id": world_id,
            "evidence_role": "causal_gold"
            if item["artifact_id"] in essential
            else "natural_background",
            "provenance_id": "eurlex-source-span-sha256:"
            + item["source_span"]["raw_span_sha256"],
            "derived_text_sha256": _digest(item["text"].encode()),
            "source_sha256": sources["act"]["sha256"],
            "source_url": sources["act"]["url"],
            "source_byte_start": item["source_span"]["byte_start"],
            "source_byte_end": item["source_span"]["byte_end"],
        }
        for item in artifacts
    ]
    document_context = SEP.join(item["text"] for item in artifacts)
    question = task["question"]
    context = wrap_prompt(question, document_context, "first")
    tokens = count(context)
    source_binding = {
        "source_receipt_sha256": source_sha,
        "source_bundle_sha256": _digest(
            eurlex_canonical_json(
                {name: source["sha256"] for name, source in sources.items()}
            ).encode()
        ),
        "preflight_config_sha256": _digest(config_raw),
        "authorization_record_id": config["authorization"]["record_id"],
    }
    candidate = {
        "schema_version": "longworld.p54-eurlex-pms-parent.v1",
        "data_product": config["data_product"],
        "world_id": world_id,
        "query_id": world_id + ":32k:parent",
        "domain": "public_law",
        "data_stage": "candidate",
        "training_objective": "sft",
        "length_bucket": "32k",
        "view": "full",
        "composition_method": "same_case_dossier",
        "query_timing": "first",
        "question": question,
        "document_context": document_context,
        "context": context,
        "artifact_classification": classifications,
        "source_record_ids_by_artifact": {
            item["artifact_id"]: [item["artifact_id"]] for item in artifacts
        },
        "essential_artifact_ids": task["essential_artifact_ids"],
        "eurlex_pms_task": task,
        "answer_program_id": EURLEX_PMS_ANSWER_PROGRAM,
        "strict_replay_revision": EURLEX_PMS_REPLAY_REVISION,
        "source_binding": source_binding,
        "source_family_ids": ["eur-lex.europa.eu/legal-content"],
        "counterfactual_twin": {
            "provenance_operation": "withhold_source_body",
            "target_artifact_id": task["cf_artifact_id"],
            "parent_value": False,
            "value": True,
            "intervention_scope": "dataset source visibility only; not legal history",
        },
        "dossier_id": _digest((world_id + "|32k").encode())[:20],
        "semantic_growth_group_id": world_id + "|pms-reference-chain",
        "base_task_id": _digest(world_id.encode())[:20],
        "dependency_class": "long_range",
        "tokenizer_model_id": tok["model_id"],
        "tokenizer_revision": tok["revision"],
        "tokenizer_asset_manifest_sha256": tok["asset_manifest_sha256"],
        "tokenizer_context_tokens": tokens,
        "actual_context_tokens": tokens,
        "real_source_token_ratio": (tokens - count(wrap_prompt(question, "", "first")))
        / tokens,
        "padding_tokens": 0,
        "cloned_artifacts": 0,
        "split_or_truncated_sections": 0,
        "truncation_ppm": 0,
        "promotion_eligible": False,
        "train_ready": False,
        "production_eligible": False,
        "promoted": False,
    }
    ids = [item["artifact_id"] for item in artifacts]
    full = replay_eurlex_pms_candidate(candidate, ids)
    cf = replay_eurlex_pms_candidate(candidate, ids, counterfactual=True)
    candidate["answer"], candidate["cf_answer"] = full["answer"], cf["answer"]
    for field in (
        "source_record_ids",
        "source_relation_ids",
        "authentic_source_relation_edges",
        "verified_derived_order_relation_edges",
        "event_count",
        "strict_support_event_count",
    ):
        candidate[field] = full[field]
    candidate["graph"] = {
        "proof_depth": full["proof_depth"],
        "hop_count": full["hop_count"],
        "n_essential_events": len(essential),
    }
    candidate["near_dup_sentence_ratio"] = round(
        sentence_near_dup_ratio(
            [
                Artifact(
                    artifact_id=item["artifact_id"],
                    doc_type="legal_source",
                    time=date(2017, 4, 5),
                    project=world_id,
                    prefix="",
                    reveals_events=[],
                    text=item["text"],
                    facts=[],
                )
                for item in artifacts
            ]
        ),
        4,
    )
    candidate.update(
        _canonical_task_identifiers(candidate, EURLEX_PMS_TASK_REPLAY_ADAPTER)
    )
    audit_eurlex_pms_candidate(candidate)
    payload = {
        **source_binding,
        "source_receipt_raw_utf8": source_raw.decode(),
        "eurlex_pms_task": task,
        "task_sha256": _digest(eurlex_canonical_json(task).encode()),
        "replay_revision": EURLEX_PMS_REPLAY_REVISION,
        "tokenizer_model_id": tok["model_id"],
        "tokenizer_revision": tok["revision"],
        "tokenizer_asset_manifest_sha256": tok["asset_manifest_sha256"],
        "candidate_content_commitments": [task_candidate_content_commitment(candidate)],
    }
    sidecar = build_task_replay_sidecar(
        adapter_id=EURLEX_PMS_TASK_REPLAY_ADAPTER[0],
        adapter_revision=EURLEX_PMS_TASK_REPLAY_ADAPTER[1],
        replay_payload=payload,
        source_attestation_key=source_key,
    )
    sidecar_raw = _bytes(sidecar)
    candidate["task_replay_sidecar"] = task_replay_sidecar_binding(
        sidecar_raw, source_attestation_key=source_key
    )
    signed = attach_attestation(candidate, candidate_key, purpose="candidate_row")
    _persist(directory / "source_receipt.json", source_raw)
    _persist(directory / "parent_sidecar.json", sidecar_raw)
    _persist(directory / "parents.jsonl", _bytes(signed))
    _persist(directory / "geometry_artifacts.json", _bytes(artifacts))
    report = {
        "schema_version": "longworld.p54-eurlex-pms-parent-report.v1",
        "parent_candidate_count": 1,
        "parent_path": str(directory / "parents.jsonl"),
        "parent_sidecar_path": str(directory / "parent_sidecar.json"),
        "source_receipt_sha256": source_sha,
        "geometry": geometry,
        "parent_context_tokens": tokens,
        "artifact_count": len(artifacts),
        "essential_count": len(essential),
        "near_dup_sentence_ratio": candidate["near_dup_sentence_ratio"],
        "full_answer": json.loads(full["answer"]),
        "cf_answer": json.loads(cf["answer"]),
        "train_ready": False,
        "inventory_delta": 0,
    }
    _write(Path("reports/p54_eurlex_pms_registered_v2.json"), report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    report = run(args.config)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "parent_candidate_count",
                    "parent_path",
                    "parent_sidecar_path",
                    "parent_context_tokens",
                    "artifact_count",
                    "near_dup_sentence_ratio",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
