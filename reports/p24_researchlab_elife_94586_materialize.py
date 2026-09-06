"""Materialize one authentic eLife 94586 exact-64K parent and v1 sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from p20_researchlab_elife_94586_capacity_preflight import (
    add_artifacts,
    direct_response_paragraphs,
    exact_deduplicate,
    near_deduplicate,
    semantic_blocks,
    visible_text,
)

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.documentworkflow import replay_elife_review_revision_task
from longworld.core.pack import SEP, wrap_prompt
from longworld.core.promotion import CANDIDATE_ATTESTATION_PURPOSE
from longworld.core.taskpromotion import task_sidecar_token_counter
from longworld.core.taskreplaysidecar import (
    ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value).rstrip(b"\n")).hexdigest()


def _write_deterministic(path: Path, raw: bytes) -> None:
    if path.exists() and path.read_bytes() != raw:
        raise ValueError(f"deterministic rebuild differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _validate_inventory(
    config: dict[str, Any], inventory_raw: bytes
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if hashlib.sha256(inventory_raw).hexdigest() != config["source_inventory_sha256"]:
        raise ValueError("P21/P22 source inventory exact bytes changed")
    inventory = json.loads(inventory_raw)
    records = list(inventory.get("records") or [])
    if [record.get("record_id") for record in records] != [
        "elife:94586:v1",
        "elife:94586:v2",
    ]:
        raise ValueError("eLife source records are not the verified v1/v2 pair")
    for record in records:
        text = str(record.get("text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != record.get("text_sha256"):
            raise ValueError("redacted source text hash changed")
        redactions = int(record["privacy_review"]["email_redaction_count"])
        if text.count("[redacted-email]") != redactions:
            raise ValueError("redacted source text receipt changed")
    if (
        sum(record["privacy_review"]["email_redaction_count"] for record in records)
        != 6
    ):
        raise ValueError("eLife six-email redaction receipt changed")
    if replay_elife_review_revision_task(inventory) != "VERIFIED_IMPLEMENTED":
        raise ValueError("verified eLife task no longer strictly replays")
    return inventory, {str(record["record_id"]): record for record in records}


def _capacity_artifacts(
    roots: dict[int, ET.Element], minimum_chars: int
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    blocks = {version: semantic_blocks(root) for version, root in roots.items()}
    for version, root in roots.items():
        for subarticle in root.findall("./sub-article"):
            article_type = subarticle.attrib.get("article-type")
            if article_type == "referee-report":
                add_artifacts(
                    artifacts,
                    kind="review",
                    source=f"v{version}:{subarticle.attrib.get('id')}",
                    texts=[
                        visible_text(item) for item in subarticle.findall("./body//p")
                    ],
                    minimum_chars=minimum_chars,
                )
            elif article_type == "author-comment":
                add_artifacts(
                    artifacts,
                    kind="author_response",
                    source=f"v{version}:{subarticle.attrib.get('id')}",
                    texts=direct_response_paragraphs(subarticle),
                    minimum_chars=minimum_chars,
                )
    matcher = SequenceMatcher(None, blocks[1], blocks[2], autojunk=False)
    for (
        opcode,
        start_before,
        end_before,
        start_after,
        end_after,
    ) in matcher.get_opcodes():
        if opcode == "equal":
            continue
        add_artifacts(
            artifacts,
            kind="delta_before",
            source="v1->v2:before",
            texts=blocks[1][start_before:end_before],
            minimum_chars=minimum_chars,
        )
        add_artifacts(
            artifacts,
            kind="delta_after",
            source="v1->v2:after",
            texts=blocks[2][start_after:end_after],
            minimum_chars=minimum_chars,
        )
    return artifacts


def _record_id_for_source(source: str) -> str:
    if source.startswith("v1:") or source == "v1->v2:before":
        return "elife:94586:v1"
    if source.startswith("v2:") or source == "v1->v2:after":
        return "elife:94586:v2"
    raise ValueError(f"unbound eLife source unit: {source}")


def _near_duplicate(left: str, right: str, threshold: float) -> bool:
    maximum = 2 * min(len(left), len(right)) / (len(left) + len(right))
    if maximum < threshold:
        return False
    common = sum((Counter(left) & Counter(right)).values())
    if 2 * common / (len(left) + len(right)) < threshold:
        return False
    return SequenceMatcher(None, left, right, autojunk=False).quick_ratio() >= threshold


def _fixed_evidence(
    task: dict[str, Any], records: dict[str, dict[str, Any]], token_counter
) -> list[dict[str, Any]]:
    evidence = task["witness"]["evidence"]
    order = (
        "controlling_review",
        "direct_author_response",
        "body_figure_fig5",
        "appendix_APP9",
        "appendix_table_tbl3",
    )
    essential_ids = set(task["essential_evidence_ids"])
    fixed: list[dict[str, Any]] = []
    for evidence_id in order:
        item = evidence[evidence_id]
        record = records[item["record_id"]]
        start = int(item["evidence_char_start"])
        end = int(item["evidence_char_end"])
        text = str(item["evidence_quote"])
        if record["text"][start:end] != text:
            raise ValueError(f"task evidence span drifted: {evidence_id}")
        fixed.append(
            {
                "artifact_id": f"elife:94586:evidence:{evidence_id}",
                "kind": "task_evidence_span",
                "source": item["record_id"],
                "record_id": item["record_id"],
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "chars": len(text),
                "qwen_tokens": token_counter(text),
                "essential": evidence_id in essential_ids,
                "evidence_id": evidence_id,
                "source_char_start": start,
                "source_char_end": end,
                "source_origin": "real_public",
            }
        )
    return fixed


def _spread(
    fixed: list[dict[str, Any]], support: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(fixed, start=1):
        boundary = len(support) * index // (len(fixed) + 1)
        previous = len(support) * (index - 1) // (len(fixed) + 1)
        result.extend(support[previous:boundary])
        result.append(item)
    result.extend(support[len(support) * len(fixed) // (len(fixed) + 1) :])
    return result


def _select_exact_pack(
    fixed: list[dict[str, Any]],
    support: list[dict[str, Any]],
    *,
    question: str,
    lower: int,
    upper: int,
    target: int,
    token_counter,
) -> tuple[list[dict[str, Any]], int]:
    weights = [token_counter(f"{SEP}{item['text']}") for item in support]
    bits = 1
    history: list[int] = []
    for weight in weights:
        history.append(bits)
        bits |= bits << weight
    fixed_prompt_tokens = token_counter(
        wrap_prompt(question, SEP.join(item["text"] for item in fixed), "first")
    )
    desired = max(0, target - fixed_prompt_tokens)
    reachable = [
        value
        for value in range(max(0, desired - 2048), desired + 2049)
        if (bits >> value) & 1
    ]
    for value in sorted(reachable, key=lambda item: (abs(item - desired), item)):
        chosen: list[dict[str, Any]] = []
        remaining = value
        for index in range(len(support) - 1, -1, -1):
            if (history[index] >> remaining) & 1:
                continue
            chosen.append(support[index])
            remaining -= weights[index]
        if remaining != 0:
            raise AssertionError("eLife subset reconstruction failed")
        chosen.reverse()
        artifacts = _spread(fixed, chosen)
        observed = token_counter(
            wrap_prompt(question, SEP.join(item["text"] for item in artifacts), "first")
        )
        if lower <= observed <= upper:
            return artifacts, observed
    raise ValueError(
        "64k natural exact-band blocker: no fixed-evidence plus near-dedup source "
        f"subset reached {lower}-{upper}"
    )


def _provisional_sidecar(
    inventory: dict[str, Any],
    inventory_sha256: str,
    tokenizer: dict[str, Any],
    source_key: bytes,
) -> dict[str, Any]:
    adapter_id, adapter_revision, schema_version = (
        ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER
    )
    records = inventory["records"]
    return build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        sidecar_schema_version=schema_version,
        replay_payload={
            "source_inventory_sha256": inventory_sha256,
            "authorization_record_id": inventory["authorization"]["record_id"],
            "source_record_bindings": [
                {
                    "record_id": record["record_id"],
                    "raw_source_sha256": record["source_sha256"],
                    "redacted_text_sha256": record["text_sha256"],
                    "email_redaction_count": record["privacy_review"][
                        "email_redaction_count"
                    ],
                }
                for record in records
            ],
            "email_redaction_receipt": {
                "replacement": "[redacted-email]",
                "total": sum(
                    record["privacy_review"]["email_redaction_count"]
                    for record in records
                ),
            },
            "relation_kinds": sorted(
                relation["kind"] for relation in inventory["relations"]
            ),
            "elife_review_revision_task": inventory["task"],
            "task_sha256": _canonical_sha256(inventory["task"]),
            "replay_revision": adapter_revision,
            "tokenizer_model_id": tokenizer["model_id"],
            "tokenizer_revision": tokenizer["revision"],
            "tokenizer_asset_manifest_sha256": tokenizer["asset_manifest_sha256"],
            "candidate_content_commitments": [
                {
                    "world_id": "placeholder",
                    "length_bucket": "64k",
                    "content_sha256": "0" * 64,
                }
            ],
        },
        source_attestation_key=source_key,
    )


def build(config_path: Path) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    inventory_path = Path(config["source_inventory"])
    inventory_raw = inventory_path.read_bytes()
    inventory, records = _validate_inventory(config, inventory_raw)
    source_key = attestation_key_from_env("task_replay_sidecar")
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if source_key is None or candidate_key is None:
        raise ValueError("P24 requires separate local probe source and candidate keys")

    provisional = _provisional_sidecar(
        inventory, config["source_inventory_sha256"], config["tokenizer"], source_key
    )
    provisional_raw = _canonical_bytes(provisional)
    provisional_binding = task_replay_sidecar_binding(
        provisional_raw, source_attestation_key=source_key
    )
    with tempfile.TemporaryDirectory(prefix="longworld-elife-p24-") as temporary:
        root = Path(temporary)
        path = root / "TASK_REPLAY_SIDECAR.json"
        path.write_bytes(provisional_raw)
        loaded = load_task_replay_sidecar(
            root, path.name, provisional_binding, source_attestation_key=source_key
        )
    token_counter = task_sidecar_token_counter(loaded)

    roots = {
        int(record["version"]): ET.fromstring(record["text"])
        for record in inventory["records"]
    }
    raw_artifacts = _capacity_artifacts(roots, int(config["minimum_block_chars"]))
    exact, exact_summary = exact_deduplicate(
        raw_artifacts, token_counter.offset_tokenizer
    )
    representatives, near_summary = near_deduplicate(
        exact, float(config["near_duplicate_threshold"])
    )
    if near_summary["representative_qwen_tokens"] != int(
        config["expected_near_dedup_tokens"]
    ):
        raise ValueError("P21 v1/v2 near-dedup capacity changed")

    task = inventory["task"]
    fixed = _fixed_evidence(task, records, token_counter)
    fixed_hashes = {item["sha256"] for item in fixed}
    threshold = float(config["near_duplicate_threshold"])
    if any(
        _near_duplicate(left["text"], right["text"], threshold)
        for index, left in enumerate(fixed)
        for right in fixed[index + 1 :]
    ):
        raise ValueError("required eLife evidence spans are near duplicates")
    support: list[dict[str, Any]] = []
    fixed_conflict_ids: list[str] = []
    for item in representatives:
        if item["sha256"] in fixed_hashes or any(
            _near_duplicate(item["text"], evidence["text"], threshold)
            for evidence in fixed
        ):
            fixed_conflict_ids.append(item["artifact_id"])
            continue
        record_id = _record_id_for_source(str(item["source"]))
        support.append(
            {
                **item,
                "record_id": record_id,
                "essential": False,
                "evidence_id": None,
                "source_char_start": None,
                "source_char_end": None,
                "source_origin": "real_derived",
            }
        )
    lower, upper = (int(value) for value in config["exact_token_band"])
    artifacts, observed_tokens = _select_exact_pack(
        fixed,
        support,
        question=config["task_question"],
        lower=lower,
        upper=upper,
        target=int(config["target_prompt_tokens"]),
        token_counter=token_counter,
    )
    if len({item["sha256"] for item in artifacts}) != len(artifacts):
        raise ValueError("eLife exact pack contains duplicate text")
    final_representatives, final_near_summary = near_deduplicate(artifacts, threshold)
    if len(final_representatives) != len(artifacts):
        raise ValueError("eLife exact pack contains near-duplicate text")
    selected_fixed = {
        item["evidence_id"] for item in artifacts if item["evidence_id"] is not None
    }
    if selected_fixed != set(task["essential_evidence_ids"]):
        raise ValueError("eLife exact pack lost required task evidence")

    workflow_id = config["workflow_id"]
    classifications: list[dict[str, Any]] = []
    artifact_manifest: list[dict[str, Any]] = []
    for index, item in enumerate(artifacts):
        record = records[item["record_id"]]
        exact_span = item["source_char_start"] is not None
        provenance_id = (
            f"source-span-sha256:{record['text_sha256']}:"
            f"{item['source_char_start']}:{item['source_char_end']}:{item['sha256']}"
            if exact_span
            else (
                f"visible-block-sha256:{record['text_sha256']}:"
                f"{item['artifact_id']}:{item['sha256']}"
            )
        )
        evidence_role = (
            "causal_gold"
            if item["essential"]
            else "causal_supporting"
            if item["evidence_id"] is not None
            else "natural_background"
        )
        classifications.append(
            {
                "artifact_id": item["artifact_id"],
                "workflow_id": workflow_id,
                "source_origin": item["source_origin"],
                "workflow_kind": "real_source_derived",
                "evidence_role": evidence_role,
                "provenance_id": provenance_id,
                "source_url": record["source_url"],
                "source_record_id": item["record_id"],
                "source_text_sha256": record["text_sha256"],
                "artifact_text_sha256": item["sha256"],
            }
        )
        artifact_manifest.append(
            {
                "sequence_index": index,
                "artifact_id": item["artifact_id"],
                "record_id": item["record_id"],
                "kind": item["kind"],
                "source_unit": item["source"],
                "evidence_id": item["evidence_id"],
                "evidence_role": evidence_role,
                "source_origin": item["source_origin"],
                "text_sha256": item["sha256"],
                "chars": item["chars"],
                "qwen_tokens_without_separator": item["qwen_tokens"],
                "source_char_start": item["source_char_start"],
                "source_char_end": item["source_char_end"],
            }
        )

    document_context = SEP.join(item["text"] for item in artifacts)
    context = wrap_prompt(config["task_question"], document_context, "first")
    if token_counter(context) != observed_tokens:
        raise ValueError("final eLife prompt token count changed")
    no_documents = wrap_prompt(config["task_question"], "", "first")
    real_source_tokens = observed_tokens - token_counter(no_documents)
    adapter_id, adapter_revision, schema_version = (
        ELIFE_REVIEW_REVISION_TASK_REPLAY_ADAPTER
    )
    unsigned: dict[str, Any] = {
        "world_id": config["world_id"],
        "query_id": config["query_id"],
        "domain": "researchlab",
        "data_stage": "candidate",
        "training_objective": "sft",
        "length_bucket": config["length_bucket"],
        "view": "full",
        "composition_method": "same_case_dossier",
        "query_timing": "first",
        "question": config["task_question"],
        "answer": task["answer"],
        "cf_answer": "CLAIMED_NOT_VERIFIED",
        "document_context": document_context,
        "context": context,
        "artifact_classification": classifications,
        "artifact_manifest": artifact_manifest,
        "source_record_ids_by_artifact": {
            item["artifact_id"]: [item["record_id"]] for item in artifacts
        },
        "essential_artifact_ids": [
            item["artifact_id"] for item in artifacts if item["essential"]
        ],
        "workflow_ids": [workflow_id],
        "source_binding": {
            "source_inventory_sha256": config["source_inventory_sha256"]
        },
        "source_family_ids": ["elife_peer_review_revision"],
        "task_replay_sidecar": {
            "adapter_id": adapter_id,
            "adapter_revision": adapter_revision,
            "sidecar_schema_version": schema_version,
            "sha256": "0" * 64,
        },
        "strict_replay_revision": adapter_revision,
        "elife_review_revision_task": task,
        "counterfactual_twin": {
            "provenance_operation": "remove_required_revision_delta",
            "removed_evidence_id": "body_figure_fig5",
            "answer": "CLAIMED_NOT_VERIFIED",
        },
        "answer_program_id": task["program_id"],
        "source_record_ids": ["elife:94586:v1", "elife:94586:v2"],
        "source_relation_ids": [
            relation["relation_id"] for relation in inventory["relations"]
        ],
        "authentic_source_relation_edges": [
            {
                "parent_record_id": relation["source_record_id"],
                "child_record_id": relation["target_record_id"],
                "relation_type": relation["kind"],
                "relation_id": relation["relation_id"],
            }
            for relation in inventory["relations"]
        ],
        "event_count": len(task["essential_evidence_ids"]),
        "strict_support_event_count": len(task["essential_evidence_ids"]),
        "graph": {"proof_depth": 3, "hop_count": 3},
        "real_source_marginal_tokens": real_source_tokens,
        "real_source_token_ratio": round(real_source_tokens / observed_tokens, 8),
        "tokenizer_model_id": config["tokenizer"]["model_id"],
        "tokenizer_revision": config["tokenizer"]["revision"],
        "tokenizer_asset_manifest_sha256": config["tokenizer"]["asset_manifest_sha256"],
        "tokenizer_context_tokens": observed_tokens,
        "actual_context_tokens": observed_tokens,
        "train_ready": False,
        "production_eligible": False,
        "promoted": False,
    }
    commitment = task_candidate_content_commitment(unsigned)
    sidecar_payload = {
        **{
            key: value
            for key, value in provisional["replay_payload"].items()
            if key != "candidate_content_commitments"
        },
        "candidate_content_commitments": [commitment],
    }
    sidecar = build_task_replay_sidecar(
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        sidecar_schema_version=schema_version,
        replay_payload=sidecar_payload,
        source_attestation_key=source_key,
    )
    sidecar_raw = _canonical_bytes(sidecar)
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_raw, source_attestation_key=source_key
    )
    unsigned["task_replay_sidecar"] = dict(sidecar_binding)
    if task_candidate_content_commitment(unsigned) != commitment:
        raise ValueError("eLife candidate content commitment is unstable")
    signed = attach_attestation(
        unsigned, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
    )
    if task_candidate_content_commitment(signed) != commitment:
        raise ValueError("signed eLife candidate content commitment changed")

    output_dir = Path(config["output_dir"])
    parents_raw = _canonical_bytes(signed)
    receipt = {
        "schema_version": "longworld.elife-review-revision-generation-receipt.v1",
        "data_stage": "candidate",
        "train_ready": False,
        "production_eligible": False,
        "selected": False,
        "promoted": False,
        "source_inventory_sha256": config["source_inventory_sha256"],
        "config_sha256": hashlib.sha256(config_raw).hexdigest(),
        "task_sha256": _canonical_sha256(task),
        "candidate_content_commitment": commitment,
        "sidecar_sha256": sidecar_binding["sha256"],
        "parents_sha256": hashlib.sha256(parents_raw).hexdigest(),
        "artifact_manifest_sha256": _canonical_sha256(artifact_manifest),
        "exact_dedup": exact_summary,
        "near_dedup": near_summary,
        "fixed_evidence_aware_near_dedup": {
            "support_conflicts_removed": len(fixed_conflict_ids),
            "support_conflict_artifact_ids": fixed_conflict_ids,
            "final_artifact_count": len(artifacts),
            "final_representative_count": len(final_representatives),
            "final_pair_count": final_near_summary["pairs"],
            "final_component_ledger_sha256": final_near_summary[
                "component_ledger_sha256"
            ],
        },
        "pack": {
            "length_bucket": config["length_bucket"],
            "exact_token_band": [lower, upper],
            "parent_prompt_tokens": observed_tokens,
            "artifact_count": len(artifacts),
            "essential_artifact_count": sum(item["essential"] for item in artifacts),
            "evidence_role_counts": dict(
                sorted(
                    Counter(item["evidence_role"] for item in classifications).items()
                )
            ),
            "source_origin_counts": dict(
                sorted(
                    Counter(item["source_origin"] for item in classifications).items()
                )
            ),
            "document_utf8_bytes": len(document_context.encode()),
            "real_source_marginal_tokens": real_source_tokens,
        },
        "redaction_receipt": sidecar_payload["email_redaction_receipt"],
        "relation_kinds": sidecar_payload["relation_kinds"],
    }
    receipt_raw = _canonical_bytes(receipt)
    _write_deterministic(output_dir / "TASK_REPLAY_SIDECAR.json", sidecar_raw)
    _write_deterministic(output_dir / "parents.jsonl", parents_raw)
    _write_deterministic(output_dir / "GENERATION_RECEIPT.json", receipt_raw)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().config), sort_keys=True))


if __name__ == "__main__":
    main()
