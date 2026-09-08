"""Build SSH architecture succession parents without HTTP/3 5-field packing."""

from __future__ import annotations

import argparse
import hashlib
import json
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
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.standardsworkflow import (
    build_ietf_ssh_architecture_succession_task,
    materialize_ietf_ssh_architecture_counterfactual,
    render_ietf_cross_spec_prompt,
)
from longworld.core.taskpromotion import task_sidecar_token_counter
from longworld.core.taskproof import replay_ietf_cross_spec_candidate
from longworld.core.taskreplaysidecar import (
    IETF_SSH_ARCHITECTURE_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from longworld.core.p57pipeline import MAX_PARENT_ARTIFACTS
from longworld.core.taskpromotion import _dossier_spread
from reports.p57_ietf_http3_quic_requirement_generate import (
    _artifacts_for_bucket,
    _canonical_bytes,
    _sha256,
    _source_units,
    _visible_source_span,
)


_SUCCESSOR_GOLD_RECORD_IDS = frozenset({"ietf:rfc:4252", "ietf:rfc:4254"})
_SSH_MIN_ZIPPER_TAIL = 12
_SSH_MAX_ZIPPER_TAIL = 24
_SSH_WINDOW_16K = 16_384


def _require_ssh_cross_rfc_gold(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep authentic successor-RFC gold so chronology can clear the 16k bound.

    RFC 4251 is only ~16.6k tokens. Two gold quotes on that one RFC cannot sit
    more than 16k apart in ordered chronology. User-auth and connection gold
    belong on RFC 4252/4254; leftover 4254 chunks after that gold keep the
    zipper from pairing the late gold with the early 4251 gold.
    """
    gold_records = {
        str(item.get("record_id") or "")
        for item in artifacts
        if item.get("essential")
    }
    if "ietf:rfc:4251" not in gold_records:
        raise ValueError("SSH packing needs RFC 4251 architecture gold")
    if not (gold_records & _SUCCESSOR_GOLD_RECORD_IDS):
        raise ValueError(
            "SSH packing needs authentic RFC 4252/4254 gold so chronology "
            "can clear the 16k intersecting bound"
        )
    return artifacts


def _ssh_chronology(
    artifacts: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], str]]:
    return sorted(
        (
            (
                f"{item.get('occurred_at') or ''}|{item['artifact_id']}",
                item,
                str(item["text"]),
            )
            for item in artifacts
        ),
        key=lambda value: value[0],
    )


def _coarsen_source_units(
    units: list[tuple[int, int]],
    *,
    min_groups: int,
    max_groups: int,
) -> list[tuple[int, int]]:
    if not units:
        raise ValueError("SSH leftover has no visible source units")
    target = min(len(units), max(min_groups, min(max_groups, len(units))))
    if len(units) <= target:
        return list(units)
    total_chars = units[-1][1] - units[0][0]
    per_group = max(1, (total_chars + target - 1) // target)
    grouped: list[tuple[int, int]] = []
    start, end = units[0]
    for index, (unit_start, unit_end) in enumerate(units[1:], start=1):
        remaining_units = len(units) - index
        remaining_groups = target - len(grouped) - 1
        if unit_end - start < per_group and remaining_units > remaining_groups:
            end = unit_end
            continue
        grouped.append((start, end))
        start, end = unit_start, unit_end
        if len(grouped) == target - 1:
            grouped.append((start, units[-1][1]))
            return grouped
    grouped.append((start, end))
    return grouped


def _explode_ssh_zipper_tail(
    artifacts: list[dict[str, Any]],
    *,
    span_id_width: int,
    max_artifacts: int = MAX_PARENT_ARTIFACTS,
    min_tail: int = _SSH_MIN_ZIPPER_TAIL,
    max_tail: int = _SSH_MAX_ZIPPER_TAIL,
) -> list[dict[str, Any]]:
    """Split pin-last RFC 4254 leftover into coarsened blank-line groups.

    One leftover artifact zippers next to RFC 4251 gold. Many tail groups push
    successor gold past the 16k intersecting bound without exploding 4251/4252.
    """
    gold_indexes = [index for index, item in enumerate(artifacts) if item.get("essential")]
    if not gold_indexes:
        raise ValueError("SSH packing has no gold artifacts")
    last_gold = max(gold_indexes)
    head = artifacts[: last_gold + 1]
    tail = artifacts[last_gold + 1 :]
    if not tail:
        raise ValueError(
            "SSH packing needs leftover after successor gold so the zipper "
            "does not pair RFC 4254 gold with RFC 4251 gold"
        )
    if any(item.get("essential") for item in tail):
        raise ValueError("SSH zipper tail must not contain gold")
    budget = max_artifacts - len(head)
    if budget < min_tail:
        raise ValueError("SSH zipper tail cannot fit under the parent artifact cap")
    exploded: list[dict[str, Any]] = []
    for item in tail:
        text = str(item["text"])
        groups = _coarsen_source_units(
            _source_units(text),
            min_groups=min_tail if len(tail) == 1 else 1,
            max_groups=min(max_tail, budget),
        )
        record_id = str(item["record_id"])
        base_start = int(item["char_start"])
        for start, end in groups:
            chunk = text[start:end]
            if not _visible_source_span(text, start, end):
                continue
            digest = hashlib.sha256(chunk.encode()).hexdigest()
            exploded.append(
                {
                    **item,
                    "artifact_id": (
                        f"{record_id}:chars:"
                        f"{base_start + start:0{span_id_width}d}-"
                        f"{base_start + end:0{span_id_width}d}:"
                        f"{digest[:12]}"
                    ),
                    "char_start": base_start + start,
                    "char_end": base_start + end,
                    "text": chunk,
                    "text_sha256": digest,
                    "essential": False,
                }
            )
    if len(exploded) < min_tail:
        raise ValueError(
            "SSH zipper tail is still one leftover artifact; coarsened "
            f"groups={len(exploded)}<{min_tail}"
        )
    packed = head + exploded
    if len(packed) > max_artifacts:
        raise ValueError(
            f"SSH zipper tail exploded to {len(packed)}>{max_artifacts} artifacts"
        )
    packed.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["record_id"]),
            int(item["char_start"]),
        )
    )
    return packed


def _require_ssh_zipper_clears_16k(
    artifacts: list[dict[str, Any]],
    token_counter,
    question: str,
) -> list[dict[str, Any]]:
    spread = _dossier_spread(_ssh_chronology(artifacts))
    gold_positions = [
        index
        for index, (item, _document) in enumerate(spread)
        if item.get("essential")
    ]
    if len(gold_positions) < 2:
        raise ValueError("SSH zipper needs at least two gold artifacts")
    start, stop = min(gold_positions), max(gold_positions) + 1
    span_tokens = token_counter(
        render_ietf_cross_spec_prompt(
            question,
            SEP.join(document for _item, document in spread[start:stop]),
            "first",
        )
    )
    if span_tokens <= _SSH_WINDOW_16K:
        raise ValueError(
            "SSH zipper still retrieves gold inside 16k: "
            f"spread={start}:{stop} tokens={span_tokens}"
        )
    return artifacts


def build(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.ietf-ssh-generation-config.v1":
        raise ValueError("unsupported IETF SSH generation config")
    packing = config.get("packing") or {}
    if packing.get("isolate_evidence_ids"):
        raise ValueError("SSH packing must not isolate HTTP/3-style quote artifacts")
    priority = packing.get("support_priority_record_ids") or []
    if not isinstance(priority, list) or not priority or priority[0] != "ietf:rfc:4251":
        raise ValueError(
            "SSH 32k packing must fill RFC 4251 leftover before successor-RFC gold"
        )
    chunked = packing.get("chunked_record_ids") or []
    if "ietf:rfc:4251" in chunked or "ietf:rfc:4252" in chunked:
        raise ValueError(
            "SSH must not explode RFC 4251/4252 into tiny chunks; leftover "
            "RFC 4254 after successor gold is enough for the zipper tail"
        )
    if list(chunked) != ["ietf:rfc:4254"]:
        raise ValueError("SSH leftover chunking is RFC 4254 only")
    chunk_max = int(
        (packing.get("chunk_max_tokens_by_bucket") or {}).get("32k")
        or packing.get("chunk_max_tokens")
        or 0
    )
    if chunk_max < 8192:
        raise ValueError("SSH leftover chunks must stay >=8192 tokens")
    buckets = config.get("length_buckets") or {}
    if any(bucket != "32k" for bucket in buckets):
        raise ValueError("SSH packing is 32k only; unique 52837 must not pad to 64k")
    if "32k" not in buckets:
        raise ValueError("SSH packing requires the 32k band")
    source_dir = Path(config["source_inventory_dir"])
    inventory_path = source_dir / config["fetch_inventory_file"]
    inventory_raw = inventory_path.read_bytes()
    if hashlib.sha256(inventory_raw).hexdigest() != config["fetch_inventory_sha256"]:
        raise ValueError("SSH fetch inventory hash changed")
    signed_path = source_dir / config["signed_workflow_file"]
    signed = json.loads(signed_path.read_text())
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None or not verify_attestation(
        signed, source_key, purpose="source_manifest"
    ):
        raise ValueError("SSH signed workflow attestation is invalid")
    if hashlib.sha256(signed_path.read_bytes()).hexdigest() != config[
        "signed_workflow_sha256"
    ]:
        raise ValueError("SSH signed workflow hash changed")
    manifest = {key: value for key, value in signed.items() if key != "attestation"}
    task = build_ietf_ssh_architecture_succession_task(manifest)
    materialized = materialize_ietf_ssh_architecture_counterfactual(
        task,
        evidence_id=str(packing.get("counterfactual_evidence_id") or "current_protocol"),
    )

    sidecar_key = attestation_key_from_env("task_replay_sidecar")
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if sidecar_key is None or candidate_key is None:
        raise ValueError("SSH generate requires local probe source and candidate keys")
    adapter_id, adapter_revision, schema_version = IETF_SSH_ARCHITECTURE_TASK_REPLAY_ADAPTER
    tokenizer = config["tokenizer"]
    output_dir = Path(config["output_dir"]).resolve()
    if (output_dir / "parents.jsonl").exists():
        raise ValueError(f"SSH generate refuses to overwrite {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    provisional_sidecar = build_task_replay_sidecar(
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
            "candidate_content_commitments": [
                {
                    "world_id": "placeholder",
                    "length_bucket": "32k",
                    "content_sha256": "0" * 64,
                }
            ],
        },
        source_attestation_key=sidecar_key,
    )
    provisional_raw = _canonical_bytes(provisional_sidecar)
    provisional_path = output_dir / ".provisional-sidecar.json"
    provisional_path.write_bytes(provisional_raw)
    binding = task_replay_sidecar_binding(
        provisional_raw, source_attestation_key=sidecar_key
    )
    loaded = load_task_replay_sidecar(
        provisional_path.parent,
        provisional_path.name,
        binding,
        source_attestation_key=sidecar_key,
    )
    token_counter = task_sidecar_token_counter(loaded)

    unsigned: list[dict[str, Any]] = []
    packs: dict[str, dict[str, Any]] = {}
    for bucket, (lower, upper) in config["length_buckets"].items():
        configured_record_ids = (packing.get("record_ids_by_bucket") or {}).get(bucket)
        artifacts, observed_tokens = _artifacts_for_bucket(
            task,
            token_counter,
            bucket,
            int(lower),
            int(upper),
            int(packing["target_margin_tokens"]),
            chunked_record_ids=frozenset(packing.get("chunked_record_ids") or []),
            chunk_max_tokens=int(
                (packing.get("chunk_max_tokens_by_bucket") or {}).get(bucket)
                or packing.get("chunk_max_tokens")
                or 0
            ),
            span_id_width=int(packing.get("span_id_width") or 0),
            isolate_evidence_ids=frozenset(),
            support_priority_record_ids=tuple(
                packing.get("support_priority_record_ids") or []
            ),
            allowed_record_ids=(
                frozenset(configured_record_ids)
                if configured_record_ids is not None
                else None
            ),
            published_draft_policy=str(packing.get("published_draft_policy") or ""),
            pin_last_leftover_record_ids=frozenset(
                packing.get("pin_last_leftover_record_ids") or []
            ),
        )
        occurred_at_by_record = {
            str(record["record_id"]): str(record.get("occurred_at") or "")
            for record in task["source_manifest"]["records"]
        }
        for item in artifacts:
            item["occurred_at"] = occurred_at_by_record[str(item["record_id"])]
        artifacts = _require_ssh_cross_rfc_gold(artifacts)
        artifacts = _explode_ssh_zipper_tail(
            artifacts,
            span_id_width=int(packing.get("span_id_width") or 0),
        )
        question = str(task["question"])
        artifacts = _require_ssh_zipper_clears_16k(
            artifacts, token_counter, question
        )
        observed_tokens = token_counter(
            render_ietf_cross_spec_prompt(
                question, SEP.join(item["text"] for item in artifacts), "first"
            )
        )
        if not int(lower) <= observed_tokens <= int(upper):
            raise ValueError(
                f"{bucket} SSH zipper-tail pack left the exact band: "
                f"observed={observed_tokens}, band={lower}-{upper}"
            )
        classifications = [
            {
                "artifact_id": item["artifact_id"],
                "workflow_id": config.get(
                    "workflow_id", "p57-ietf-ssh-architecture-succession-v1"
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
            "schema_version": "longworld.ietf-ssh-generation-candidate.v1",
            "world_id": config.get("world_id", "ietf-ssh-architecture-succession-v1"),
            "query_id": (
                f"{config.get('world_id', 'ietf-ssh-architecture-succession-v1')}:{bucket}"
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
                f"{bucket} SSH parent replay is not the bound codebook: "
                f"{replay['answer']}"
            )
        if counterfactual_replay["answer"] == replay["answer"]:
            raise ValueError(f"{bucket} SSH counterfactual collapsed onto the parent")
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
            len(essential_ids) < 2
            or not essential_ids
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
            raise ValueError("IETF SSH essential artifact minimization failed")
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
            **{
                key: value
                for key, value in provisional_sidecar["replay_payload"].items()
                if key != "candidate_content_commitments"
            },
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
    provisional_path.unlink()
    receipt = {
        "schema_version": "longworld.ietf-ssh-generation-receipt.v1",
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "selected": False,
        "promoted": False,
        "fetch_inventory_sha256": config["fetch_inventory_sha256"],
        "manifest_sha256": _sha256(manifest),
        "task_sha256": _sha256(task),
        "predecessor_report_manifest_sha256": config[
            "predecessor_report_manifest_sha256"
        ],
        "predecessor_report_task_sha256": config["predecessor_report_task_sha256"],
        "sidecar_sha256": sidecar_binding["sha256"],
        "parents_sha256": hashlib.sha256(candidates_raw).hexdigest(),
        "packs": packs,
    }
    (output_dir / "GENERATION_RECEIPT.json").write_bytes(_canonical_bytes(receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().config), sort_keys=True))


if __name__ == "__main__":
    main()
