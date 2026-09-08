"""Build HTTP/2 succession parents without HTTP/3 5-field packing.

The compiler lives here on purpose: shared longworld/core adapters stay
owned by other tracks. Import helpers from standardsworkflow; do not add
functions there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    verify_attestation,
)
from longworld.core.pack import SEP
from longworld.core.promotion import (
    CANDIDATE_ATTESTATION_PURPOSE,
    _APPROVED_EXACT_TOKENIZERS,
    _load_replay_tokenizer_uncached,
    _resolved_local_tokenizer_revision,
    _token_counter_for,
    candidate_sha256,
)
from longworld.core.taskproof import relation_proof_depth, replay_ietf_cross_spec_candidate
from longworld.core.taskreplaysidecar import (
    IETF_HTTP2_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.taskpromotion import (
    _STANDARD_TASK_VIEWS,
    _STANDARD_VIEW_COMPOSITIONS,
    _dossier_spread,
    _ietf_chronology,
    _ietf_counterfactual_artifacts,
    _task_view_artifacts,
    task_sidecar_token_counter,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import (
    IETF_HTTP2_SUCCESSION_ANSWER_PROGRAM,
    IETF_HTTP2_SUCCESSION_TASK_SCHEMA,
    _canonical_sha256,
    audit_ietf_http2_succession_task,
    audit_ietf_workflow_manifest,
    build_ietf_http2_succession_task,
    materialize_ietf_http2_counterfactual,
    render_ietf_cross_spec_prompt,
    replay_ietf_http2_succession_task,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from reports.p57_ietf_http3_quic_requirement_generate import (
    _artifacts_for_bucket,
    _canonical_bytes,
    _sha256,
)

IETF_HTTP2_GENERATION_CONFIG_SCHEMA = "longworld.ietf-http2-generation-config.v1"
IETF_HTTP2_GENERATION_CANDIDATE_SCHEMA = (
    "longworld.ietf-http2-succession-generation-candidate.v1"
)


def validate_http2_generation_config(config: dict[str, Any]) -> None:
    """Refuse HTTP/3 isolate packing, 128k padding, and thin leftover chunks."""
    if config.get("schema_version") != IETF_HTTP2_GENERATION_CONFIG_SCHEMA:
        raise ValueError("unsupported IETF HTTP/2 generation config")
    if config["packing"].get("isolate_evidence_ids"):
        raise ValueError("HTTP/2 packing must not isolate HTTP/3-style quote artifacts")
    if any(bucket != "64k" for bucket in config["length_buckets"]):
        raise ValueError("HTTP/2 unique leftover is a 64k natural band; 128k is blocked")
    bucket_chunk = int(
        (config["packing"].get("chunk_max_tokens_by_bucket") or {}).get("64k")
        or config["packing"].get("chunk_max_tokens")
        or 0
    )
    if bucket_chunk < 8192:
        raise ValueError(
            "HTTP/2 64k leftover chunks must stay thick enough for 4k/8k zipper ends"
        )


def _load_http2_token_counter(tokenizer_config: dict[str, Any]):
    model_id = str(tokenizer_config["model_id"])
    revision = str(tokenizer_config["revision"])
    asset_digest = str(tokenizer_config["asset_manifest_sha256"])
    if (model_id, revision) not in _APPROVED_EXACT_TOKENIZERS:
        raise ValueError("HTTP/2 exact tokenizer pin is not approved")
    loaded_revision = _resolved_local_tokenizer_revision(model_id, revision)
    observed = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    if loaded_revision != revision or observed != asset_digest:
        raise ValueError("HTTP/2 exact tokenizer assets do not match")
    tokenizer = _load_replay_tokenizer_uncached(model_id, revision)
    loaded_asset = resolved_tokenizer_asset_manifest_sha256(model_id, revision)
    if loaded_asset != observed:
        raise ValueError("HTTP/2 exact tokenizer assets do not match")
    counter = _token_counter_for(tokenizer)
    if counter is None:
        raise ValueError("HTTP/2 exact tokenizer is unavailable")
    counter.offset_tokenizer = tokenizer
    counter._json_window_tokenizer = tokenizer
    return counter


def build(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    validate_http2_generation_config(config)
    source_dir = Path(config["source_inventory_dir"])
    inventory_path = source_dir / config["fetch_inventory_file"]
    inventory_raw = inventory_path.read_bytes()
    if hashlib.sha256(inventory_raw).hexdigest() != config["fetch_inventory_sha256"]:
        raise ValueError("HTTP/2 fetch inventory hash changed")
    signed_path = source_dir / config["signed_workflow_file"]
    signed = json.loads(signed_path.read_text())
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None or not verify_attestation(
        signed, source_key, purpose="source_manifest"
    ):
        raise ValueError("HTTP/2 signed workflow attestation is invalid")
    if hashlib.sha256(signed_path.read_bytes()).hexdigest() != config[
        "signed_workflow_sha256"
    ]:
        raise ValueError("HTTP/2 signed workflow hash changed")
    manifest = {key: value for key, value in signed.items() if key != "attestation"}
    task = build_ietf_http2_succession_task(manifest)
    materialized = materialize_ietf_http2_counterfactual(
        task,
        evidence_id=str(
            config["packing"].get("counterfactual_evidence_id") or "current_protocol"
        ),
    )

    sidecar_key = attestation_key_from_env("task_replay_sidecar")
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if sidecar_key is None or candidate_key is None:
        raise ValueError("HTTP/2 generate requires local probe source and candidate keys")
    adapter_id, adapter_revision, schema_version = IETF_HTTP2_TASK_REPLAY_ADAPTER
    tokenizer = config["tokenizer"]
    output_dir = Path(config["output_dir"]).resolve()
    if (output_dir / "parents.jsonl").exists():
        raise ValueError(f"HTTP/2 generate refuses to overwrite {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    token_counter = _load_http2_token_counter(tokenizer)

    unsigned: list[dict[str, Any]] = []
    packs: dict[str, dict[str, Any]] = {}
    for bucket, (lower, upper) in config["length_buckets"].items():
        configured_record_ids = (
            config["packing"].get("record_ids_by_bucket") or {}
        ).get(bucket)
        artifacts, observed_tokens = _artifacts_for_bucket(
            task,
            token_counter,
            bucket,
            int(lower),
            int(upper),
            int(config["packing"]["target_margin_tokens"]),
            chunked_record_ids=frozenset(
                config["packing"].get("chunked_record_ids") or []
            ),
            chunk_max_tokens=int(
                (config["packing"].get("chunk_max_tokens_by_bucket") or {}).get(bucket)
                or config["packing"].get("chunk_max_tokens")
                or 0
            ),
            span_id_width=int(config["packing"].get("span_id_width") or 0),
            isolate_evidence_ids=frozenset(),
            support_priority_record_ids=tuple(
                config["packing"].get("support_priority_record_ids") or []
            ),
            allowed_record_ids=(
                frozenset(configured_record_ids)
                if configured_record_ids is not None
                else None
            ),
            published_draft_policy=str(
                config["packing"].get("published_draft_policy") or ""
            ),
            pin_last_leftover_record_ids=frozenset(
                config["packing"].get("pin_last_leftover_record_ids") or []
            ),
        )
        classifications = [
            {
                "artifact_id": item["artifact_id"],
                "workflow_id": config.get(
                    "workflow_id", "p57-ietf-http2-succession-v1"
                ),
                "source_origin": "real_public",
                "workflow_kind": "real_source_derived",
                "evidence_role": (
                    "causal_gold" if item["essential"] else "causal_supporting"
                ),
                "provenance_id": (
                    f"source-span-sha256:{item['source_sha256']}:{item['char_start']}:"
                    f"{item['char_end']}:{item['text_sha256']}"
                ),
                "source_url": item["source_url"],
                "source_record_id": item["record_id"],
                "source_char_start": item["char_start"],
                "source_char_end": item["char_end"],
            }
            for item in artifacts
        ]
        source_map = {item["artifact_id"]: [item["record_id"]] for item in artifacts}
        essential_ids = [item["artifact_id"] for item in artifacts if item["essential"]]
        document_context = SEP.join(item["text"] for item in artifacts)
        question = str(task["question"])
        candidate: dict[str, Any] = {
            "schema_version": IETF_HTTP2_GENERATION_CANDIDATE_SCHEMA,
            "world_id": config.get("world_id", "ietf-http2-succession-v1"),
            "query_id": (
                f"{config.get('world_id', 'ietf-http2-succession-v1')}:{bucket}"
            ),
            "domain": "standards",
            "data_stage": "candidate",
            "training_objective": "sft",
            "length_bucket": bucket,
            "view": "full",
            "composition_method": "same_case_dossier",
            "query_timing": "first",
            "question": question,
            "answer": "",
            "cf_answer": "",
            "document_context": document_context,
            "context": render_ietf_cross_spec_prompt(
                question, document_context, "first"
            ),
            "artifact_classification": classifications,
            "source_record_ids_by_artifact": source_map,
            "essential_artifact_ids": essential_ids,
            "source_binding": {
                "signed_manifest_sha256": task["source_manifest_sha256"]
            },
            "source_family_ids": ["ietf_standards"],
            "task_replay_sidecar": {
                "adapter_id": adapter_id,
                "adapter_revision": adapter_revision,
                "sidecar_schema_version": schema_version,
                "sha256": "0" * 64,
            },
            "strict_replay_revision": adapter_revision,
            "ietf_requirement_task": deepcopy(task),
            "counterfactual_twin": deepcopy(materialized["counterfactual_twin"]),
            "answer_program_id": task["answer_program_id"],
            "graph": {"proof_depth": 2, "hop_count": 2},
            "real_source_token_ratio": 1.0,
            "tokenizer_model_id": tokenizer["model_id"],
            "tokenizer_revision": tokenizer["revision"],
            "tokenizer_asset_manifest_sha256": tokenizer["asset_manifest_sha256"],
            "tokenizer_context_tokens": observed_tokens,
            "actual_context_tokens": observed_tokens,
            "train_ready": False,
            "production_eligible": False,
            "promoted": False,
        }
        replay = replay_ietf_cross_spec_candidate(candidate, list(source_map))
        counterfactual_replay = replay_ietf_cross_spec_candidate(
            candidate, list(source_map), counterfactual=True
        )
        expected_answer = json.dumps(
            task["answer"], sort_keys=True, separators=(",", ":")
        )
        if replay["answer"] != expected_answer:
            raise ValueError(
                f"{bucket} HTTP/2 parent replay is not the bound codebook: "
                f"{replay['answer']}"
            )
        if counterfactual_replay["answer"] == replay["answer"]:
            raise ValueError(f"{bucket} HTTP/2 counterfactual collapsed onto the parent")
        candidate["answer"] = replay["answer"]
        candidate["cf_answer"] = counterfactual_replay["answer"]
        essential_ids = list(source_map)
        for artifact_id in list(essential_ids):
            reduced_ids = [item for item in essential_ids if item != artifact_id]
            if (
                replay_ietf_cross_spec_candidate(candidate, reduced_ids)["answer"]
                == candidate["answer"]
            ):
                essential_ids = reduced_ids
        if (
            not essential_ids
            or replay_ietf_cross_spec_candidate(candidate, essential_ids)["answer"]
            != candidate["answer"]
            or any(
                replay_ietf_cross_spec_candidate(
                    candidate,
                    [item for item in essential_ids if item != removed],
                )["answer"]
                == candidate["answer"]
                for removed in essential_ids
            )
        ):
            raise ValueError("IETF HTTP/2 essential artifact minimization failed")
        candidate["essential_artifact_ids"] = essential_ids
        for classification in classifications:
            classification["evidence_role"] = (
                "causal_gold"
                if classification["artifact_id"] in essential_ids
                else "causal_supporting"
            )
        candidate["graph"] = {
            "proof_depth": replay["proof_depth"],
            "hop_count": replay["hop_count"],
        }
        for field in (
            "source_record_ids",
            "source_relation_ids",
            "authentic_source_relation_edges",
            "verified_derived_order_relation_edges",
            "event_count",
            "strict_support_event_count",
        ):
            candidate[field] = deepcopy(replay[field])
        unsigned.append(candidate)
        packs[bucket] = {
            "parent_prompt_tokens": observed_tokens,
            "artifact_count": len(artifacts),
            "essential_artifact_count": len(essential_ids),
            "source_span_bytes": sum(len(item["text"].encode()) for item in artifacts),
            "source_record_ids": sorted({item["record_id"] for item in artifacts}),
        }

    commitments = sorted(
        (task_candidate_content_commitment(row) for row in unsigned),
        key=lambda item: (item["world_id"], item["length_bucket"]),
    )
    sidecar = build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        sidecar_schema_version=schema_version,
        replay_payload={
            "source_manifest_sha256": task["source_manifest_sha256"],
            "fetch_inventory_sha256": manifest["fetch_inventory_sha256"],
            "authorization_record_id": manifest["authorization"]["record_id"],
            "ietf_requirement_task": task,
            "task_sha256": _sha256(task),
            "replay_revision": adapter_revision,
            "tokenizer_model_id": tokenizer["model_id"],
            "tokenizer_revision": tokenizer["revision"],
            "tokenizer_asset_manifest_sha256": tokenizer["asset_manifest_sha256"],
            "candidate_content_commitments": commitments,
        },
        source_attestation_key=sidecar_key,
    )
    sidecar_raw = _canonical_bytes(sidecar)
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_raw, source_attestation_key=sidecar_key
    )
    signed_rows: list[dict[str, Any]] = []
    for candidate in unsigned:
        candidate["task_replay_sidecar"] = dict(sidecar_binding)
        signed_rows.append(
            attach_attestation(
                candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
        )

    candidates_raw = b"".join(_canonical_bytes(row) for row in signed_rows)
    (output_dir / "TASK_REPLAY_SIDECAR.json").write_bytes(sidecar_raw)
    (output_dir / "parents.jsonl").write_bytes(candidates_raw)
    receipt = {
        "schema_version": "longworld.ietf-http2-generation-receipt.v1",
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "selected": False,
        "promoted": False,
        "adapter_registered": True,
        "fetch_inventory_sha256": config["fetch_inventory_sha256"],
        "manifest_sha256": _sha256(manifest),
        "task_sha256": _sha256(task),
        "predecessor_report_manifest_sha256": config[
            "predecessor_report_manifest_sha256"
        ],
        "predecessor_report_task_sha256": config["predecessor_report_task_sha256"],
        "sidecar_sha256": hashlib.sha256(sidecar_raw).hexdigest(),
        "parents_sha256": hashlib.sha256(candidates_raw).hexdigest(),
        "packs": packs,
        "answer_program_id": IETF_HTTP2_SUCCESSION_ANSWER_PROGRAM,
    }
    (output_dir / "GENERATION_RECEIPT.json").write_bytes(_canonical_bytes(receipt))
    return receipt


def project_packed_views(config_path: Path) -> dict[str, Any]:
    """Project full/cf/ordered views from packed HTTP/2 parents without a core adapter."""
    config = json.loads(config_path.read_text())
    validate_http2_generation_config(config)
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if candidate_key is None:
        raise ValueError("HTTP/2 view projection requires a candidate key")
    parents_path = Path(config["output_dir"]).resolve() / "parents.jsonl"
    parents = [
        json.loads(line)
        for line in parents_path.read_text().splitlines()
        if line.strip()
    ]
    if not parents:
        raise ValueError("HTTP/2 packed parents are missing")
    token_counter = _load_http2_token_counter(config["tokenizer"])
    lower, upper = config["length_buckets"]["64k"]
    report_dir = ROOT / "reports" / "p57_ietf_http2_succession_v1" / "projected"
    if (report_dir / "candidates.jsonl").exists():
        raise ValueError(f"HTTP/2 view projection refuses to overwrite {report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)
    projected: list[dict[str, Any]] = []
    packs: dict[str, dict[str, Any]] = {}
    for parent in parents:
        artifacts = _task_view_artifacts(parent)
        all_ids = [str(value[0].get("artifact_id") or "") for value in artifacts]
        factual = replay_ietf_cross_spec_candidate(parent, all_ids)
        counterfactual = replay_ietf_cross_spec_candidate(
            parent, all_ids, counterfactual=True
        )
        if factual.get("answer") != parent.get("answer") or counterfactual.get(
            "answer"
        ) != parent.get("cf_answer"):
            raise ValueError("HTTP/2 parent view replay does not match packed answers")
        cf_artifacts = _ietf_counterfactual_artifacts(parent, artifacts)
        chronology = _ietf_chronology(parent, artifacts)
        ordered_artifacts = [
            (classification, document) for _key, classification, document in chronology
        ]
        full_artifacts = _dossier_spread(chronology)
        if [value[0]["artifact_id"] for value in full_artifacts] == [
            value[0]["artifact_id"] for value in ordered_artifacts
        ]:
            raise ValueError("HTTP/2 full and ordered artifact views are not distinct")
        by_view = {
            "full": full_artifacts,
            "cf": cf_artifacts,
            "ordered_artifact_view": ordered_artifacts,
        }
        dossier_id = candidate_sha256(parent)[:20]
        for view in _STANDARD_TASK_VIEWS:
            view_artifacts = by_view[view]
            document_context = SEP.join(
                document for _classification, document in view_artifacts
            )
            question = str(parent["question"])
            context = render_ietf_cross_spec_prompt(question, document_context, "first")
            context_tokens = token_counter(context)
            if not int(lower) <= context_tokens <= int(upper):
                raise ValueError(
                    f"HTTP/2 {view} exact-band blocker: observed={context_tokens}, "
                    f"band={lower}-{upper}"
                )
            unsigned = deepcopy(parent)
            unsigned.pop("attestation", None)
            unsigned["view"] = view
            unsigned["composition_method"] = _STANDARD_VIEW_COMPOSITIONS[view]
            unsigned["dossier_id"] = dossier_id
            unsigned["query_id"] = f"{parent['query_id']}:standard:{view}"
            unsigned["answer"] = (
                parent["cf_answer"] if view == "cf" else parent["answer"]
            )
            unsigned["cf_answer"] = (
                parent["answer"] if view == "cf" else parent["cf_answer"]
            )
            unsigned["document_context"] = document_context
            unsigned["context"] = context
            unsigned["artifact_classification"] = [
                deepcopy(classification) for classification, _document in view_artifacts
            ]
            view_ids = [
                str(classification.get("artifact_id") or "")
                for classification, _document in view_artifacts
            ]
            view_replay = replay_ietf_cross_spec_candidate(
                unsigned, view_ids, counterfactual=(view == "cf")
            )
            if view_replay.get("answer") != unsigned["answer"]:
                raise ValueError(f"HTTP/2 {view} factual replay does not match answer")
            unsigned["tokenizer_context_tokens"] = context_tokens
            unsigned["actual_context_tokens"] = context_tokens
            unsigned["graph"] = {
                "proof_depth": view_replay["proof_depth"],
                "hop_count": view_replay["hop_count"],
            }
            for field in (
                "source_record_ids",
                "source_relation_ids",
                "authentic_source_relation_edges",
                "verified_derived_order_relation_edges",
                "event_count",
                "strict_support_event_count",
            ):
                unsigned[field] = deepcopy(view_replay[field])
            unsigned["task_view_projection"] = {
                "schema_version": "longworld.task-view-projection.v1",
                "parent_candidate_sha256": candidate_sha256(parent),
                "view": view,
                "adapter_registered": False,
            }
            unsigned["train_ready"] = False
            unsigned["production_eligible"] = False
            unsigned["promoted"] = False
            unsigned["promotion_blocker_code"] = "http2_core_adapter_unregistered"
            signed = attach_attestation(
                unsigned, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
            projected.append(signed)
            packs[view] = {
                "context_tokens": context_tokens,
                "artifact_count": len(view_artifacts),
            }
    projected.sort(
        key=lambda row: (str(row.get("length_bucket") or ""), str(row.get("view") or ""))
    )
    out_bytes = b"".join(_canonical_bytes(row) for row in projected)
    (report_dir / "candidates.jsonl").write_bytes(out_bytes)
    manifest = {
        "schema_version": "longworld.task-view-projection-manifest.v1",
        "input_candidate_count": len(parents),
        "projection_candidate_count": len(projected),
        "views": list(_STANDARD_TASK_VIEWS),
        "adapter_registered": False,
        "train_ready": False,
        "production_eligible": False,
        "dense_audit_complete": False,
        "packs": packs,
        "projection_candidates_sha256": hashlib.sha256(out_bytes).hexdigest(),
    }
    (report_dir / "MANIFEST.json").write_bytes(_canonical_bytes(manifest))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--project-views",
        action="store_true",
        help="project full/cf/ordered views from already packed parents",
    )
    args = parser.parse_args()
    if args.project_views:
        print(json.dumps(project_packed_views(args.config), sort_keys=True))
        return
    print(json.dumps(build(args.config), sort_keys=True))


if __name__ == "__main__":
    main()
