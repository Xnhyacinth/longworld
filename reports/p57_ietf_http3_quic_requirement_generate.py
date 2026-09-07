"""Build authentic P57 IETF HTTP/3–QUIC parent candidates without selecting or promoting."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from itertools import pairwise
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
    build_ietf_http3_quic_requirement_task,
    materialize_ietf_http3_quic_counterfactual,
    render_ietf_cross_spec_prompt,
)
from longworld.core.taskpromotion import task_sidecar_token_counter
from longworld.core.taskproof import replay_ietf_cross_spec_candidate
from longworld.core.taskreplaysidecar import (
    IETF_HTTP3_QUIC_TASK_REPLAY_ADAPTER,
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


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value).rstrip(b"\n")).hexdigest()


def _source_units(text: str) -> list[tuple[int, int]]:
    spans = [
        (match.start(), match.end())
        for match in re.finditer(r".*?(?:\n[ \t]*\n|\f|\Z)", text, re.DOTALL)
        if match.end() > match.start()
    ]
    if (
        not spans
        or spans[0][0] != 0
        or spans[-1][1] != len(text)
        or any(left[1] != right[0] for left, right in pairwise(spans))
    ):
        raise ValueError("natural source units do not exactly partition source bytes")
    return spans


def _visible_source_span(text: str, start: int, end: int) -> bool:
    return bool(text[start:end].strip())


def _essential_span(record: dict[str, Any], task: dict[str, Any]) -> tuple[int, int]:
    text = str(record["text"])
    evidence = [
        item
        for item in task["evidence_items"]
        if item["record_id"] == record["record_id"]
    ]
    units = _source_units(text)
    if evidence:
        start = min(int(item["char_start"]) for item in evidence)
        end = max(int(item["char_end"]) for item in evidence)
        selected = [span for span in units if span[0] < end and start < span[1]]
        if not selected:
            raise ValueError(f"evidence is outside source units: {record['record_id']}")
        return selected[0][0], selected[-1][1]
    if not str(record["record_id"]).startswith("ietf:draft:"):
        raise ValueError(f"record has no essential evidence: {record['record_id']}")
    return units[0]


def _merge_units(units: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(units):
        if merged and merged[-1][1] == start:
            merged[-1] = merged[-1][0], end
        else:
            merged.append((start, end))
    return merged


def _chunk_source_units(
    text: str,
    evidence: list[dict[str, Any]],
    token_counter,
    max_tokens: int,
) -> list[tuple[int, int]]:
    units: list[tuple[int, int]] = []
    for start, end in _source_units(text):
        if units and any(
            int(item["char_start"]) < start < int(item["char_end"]) for item in evidence
        ):
            units[-1] = units[-1][0], end
        else:
            units.append((start, end))
    chunks: list[tuple[int, int]] = []
    start, end = units[0]
    for unit_start, unit_end in units[1:]:
        if token_counter(text[start:unit_end]) > max_tokens:
            chunks.append((start, end))
            start = unit_start
        end = unit_end
    chunks.append((start, end))
    if any(token_counter(text[start:end]) > max_tokens for start, end in chunks):
        raise ValueError("a natural source unit exceeds the configured chunk limit")
    if (
        chunks[0][0] != 0
        or chunks[-1][1] != len(text)
        or any(left[1] != right[0] for left, right in pairwise(chunks))
    ):
        raise ValueError("natural chunks do not exactly partition source bytes")
    if any(
        sum(
            start <= int(item["char_start"]) and int(item["char_end"]) <= end
            for start, end in chunks
        )
        != 1
        for item in evidence
    ):
        raise ValueError("authenticated evidence is not covered by exactly one chunk")
    return chunks


def _isolate_evidence_spans(
    chunks: list[tuple[int, int]],
    evidence: list[dict[str, Any]],
    evidence_ids: frozenset[str],
) -> list[tuple[int, int]]:
    selected = [
        item for item in evidence if str(item.get("evidence_id") or "") in evidence_ids
    ]
    if {str(item["evidence_id"]) for item in selected} != evidence_ids:
        raise ValueError("isolated evidence ids are not exact")
    output: list[tuple[int, int]] = []
    for start, end in chunks:
        boundaries = {
            start,
            end,
            *(
                boundary
                for item in selected
                if start <= int(item["char_start"]) and int(item["char_end"]) <= end
                for boundary in (int(item["char_start"]), int(item["char_end"]))
            ),
        }
        output.extend(pairwise(sorted(boundaries)))
    if any(
        sum(
            start == int(item["char_start"]) and end == int(item["char_end"])
            for start, end in output
        )
        != 1
        for item in selected
    ):
        raise ValueError("authenticated evidence was not isolated exactly")
    return output


def _host_counterfactual_span(
    chunks: list[tuple[int, int]],
    evidence: list[dict[str, Any]],
    evidence_id: str,
) -> list[tuple[int, int]]:
    """Keep the CF target quote inside authentic remnant text.

    Isolated exact-quote artifacts become all-whitespace under
    exclude_exact_source_span and are dropped from the CF view, so proof
    cannot restore the parent answer. Merge the quote with one adjacent
    non-quote chunk from the same record. Do not merge two evidence quotes.
    """
    target = next(
        (item for item in evidence if str(item.get("evidence_id") or "") == evidence_id),
        None,
    )
    if not isinstance(target, dict):
        raise ValueError("counterfactual evidence is missing from this record")
    quote_start = int(target["char_start"])
    quote_end = int(target["char_end"])
    hosts = [
        index
        for index, (start, end) in enumerate(chunks)
        if start <= quote_start and quote_end <= end
    ]
    if len(hosts) != 1:
        raise ValueError("counterfactual evidence host is not unique")
    host = hosts[0]
    host_start, host_end = chunks[host]
    if host_start < quote_start or quote_end < host_end:
        return chunks
    other_quotes = {
        (int(item["char_start"]), int(item["char_end"]))
        for item in evidence
        if str(item.get("evidence_id") or "")
        and str(item.get("evidence_id") or "") != evidence_id
    }
    neighbors = [
        index
        for index in (host - 1, host + 1)
        if 0 <= index < len(chunks) and chunks[index] not in other_quotes
    ]
    if not neighbors:
        raise ValueError("counterfactual evidence has no authentic host remnant")
    neighbor = neighbors[0]
    first, last = min(host, neighbor), max(host, neighbor)
    if last != first + 1:
        raise ValueError("counterfactual host merge is not adjacent")
    merged = [
        *chunks[:first],
        (chunks[first][0], chunks[last][1]),
        *chunks[last + 1 :],
    ]
    merged_hosts = [
        (start, end)
        for start, end in merged
        if start <= quote_start and quote_end <= end
    ]
    if len(merged_hosts) != 1 or merged_hosts[0] == (quote_start, quote_end):
        raise ValueError("counterfactual evidence host is still quote-only")
    return merged


def _coarsen_bearer_with_companion(
    chunks: list[tuple[int, int]],
    evidence: list[dict[str, Any]],
    companion_evidence_id: str,
    *,
    evidence_id: str = "bearer_current",
) -> list[tuple[int, int]]:
    selected = {
        str(item.get("evidence_id") or ""): item
        for item in evidence
        if str(item.get("evidence_id") or "")
        in {evidence_id, companion_evidence_id}
    }
    if (
        not companion_evidence_id
        or not evidence_id
        or companion_evidence_id == evidence_id
        or set(selected) != {evidence_id, companion_evidence_id}
    ):
        raise ValueError("counterfactual companion evidence is not exact")
    containing_indices: list[int] = []
    for selected_id in (evidence_id, companion_evidence_id):
        item = selected[selected_id]
        matches = [
            index
            for index, (start, end) in enumerate(chunks)
            if start <= int(item["char_start"]) and int(item["char_end"]) <= end
        ]
        if len(matches) != 1:
            raise ValueError("counterfactual companion chunk is not unique")
        containing_indices.append(matches[0])
    first, last = min(containing_indices), max(containing_indices)
    coarsened = [
        *chunks[:first],
        (chunks[first][0], chunks[last][1]),
        *chunks[last + 1 :],
    ]
    if any(
        sum(
            start <= int(item["char_start"]) and int(item["char_end"]) <= end
            for start, end in coarsened
        )
        != 1
        for item in selected.values()
    ):
        raise ValueError("counterfactual companion evidence is not coarsened")
    return coarsened


def _artifacts_for_bucket(
    task: dict[str, Any],
    token_counter,
    bucket: str,
    lower: int,
    upper: int,
    margin: int,
    *,
    chunked_record_ids: frozenset[str] = frozenset(),
    chunk_max_tokens: int = 0,
    span_id_width: int = 0,
    isolate_evidence_ids: frozenset[str] = frozenset(),
    counterfactual_companion_evidence_id: str = "",
    counterfactual_evidence_id: str = "",
    support_priority_record_ids: tuple[str, ...] = (),
    allowed_record_ids: frozenset[str] | None = None,
    published_draft_policy: str = "",
    pin_last_leftover_record_ids: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], int]:
    manifest_records = list(task["source_manifest"]["records"])
    manifest_record_ids = {str(record["record_id"]) for record in manifest_records}
    if allowed_record_ids is not None and (
        not allowed_record_ids or not allowed_record_ids.issubset(manifest_record_ids)
    ):
        raise ValueError("allowed IETF record ids are invalid")
    records = [
        record
        for record in manifest_records
        if allowed_record_ids is None or record["record_id"] in allowed_record_ids
    ]
    by_id = {record["record_id"]: record for record in records}
    spans_by_record: dict[str, list[tuple[int, int, bool]]] = {}
    support_units: list[tuple[str, int, int]] = []
    for record in records:
        record_id = str(record["record_id"])
        text = str(record["text"])
        evidence = [
            item for item in task["evidence_items"] if item["record_id"] == record_id
        ]
        if (
            not evidence
            and not record_id.startswith("ietf:draft:")
            and record_id not in chunked_record_ids
        ):
            leftover = [
                (start, end)
                for start, end in _source_units(text)
                if _visible_source_span(text, start, end)
            ]
            if not leftover:
                raise ValueError(f"{record_id} leftover-only record has no visible source")
            spans_by_record[record_id] = [
                (leftover[0][0], leftover[-1][1], False)
            ]
            continue
        if record_id in chunked_record_ids:
            if chunk_max_tokens < 1:
                raise ValueError("chunked IETF records require a token limit")
            chunks = _chunk_source_units(
                text, evidence, token_counter, chunk_max_tokens
            )
            coarsen_evidence_id = counterfactual_evidence_id or (
                "bearer_current" if counterfactual_companion_evidence_id else ""
            )
            if counterfactual_companion_evidence_id and any(
                item.get("evidence_id") == coarsen_evidence_id for item in evidence
            ):
                chunks = _coarsen_bearer_with_companion(
                    chunks,
                    evidence,
                    counterfactual_companion_evidence_id,
                    evidence_id=coarsen_evidence_id,
                )
            record_isolations = frozenset(
                evidence_id
                for evidence_id in isolate_evidence_ids
                if any(item["evidence_id"] == evidence_id for item in evidence)
            )
            if record_isolations:
                chunks = _isolate_evidence_spans(chunks, evidence, record_isolations)
            if counterfactual_evidence_id and any(
                item.get("evidence_id") == counterfactual_evidence_id
                for item in evidence
            ):
                chunks = _host_counterfactual_span(
                    chunks, evidence, counterfactual_evidence_id
                )
            chunk_spans = [
                (
                    start,
                    end,
                    any(
                        start <= int(item["char_start"])
                        and int(item["char_end"]) <= end
                        for item in evidence
                    ),
                )
                for start, end in chunks
            ]
            essential_chunks = [span for span in chunk_spans if span[2]]
            leftover_chunks = [span for span in chunk_spans if not span[2]]
            if not essential_chunks and leftover_chunks:
                first_leftover = leftover_chunks[0]
                spans_by_record[record_id] = [
                    (first_leftover[0], first_leftover[1], False)
                ]
                leftover_chunks = leftover_chunks[1:]
            else:
                spans_by_record[record_id] = essential_chunks
            if record_id in pin_last_leftover_record_ids and leftover_chunks:
                last_leftover = leftover_chunks[-1]
                spans_by_record[record_id] = [
                    *spans_by_record[record_id],
                    (last_leftover[0], last_leftover[1], False),
                ]
                leftover_chunks = leftover_chunks[:-1]
            source_units = _source_units(text)
            explode_support = bucket in {"16k", "32k"}
            for start, end, essential in leftover_chunks:
                if essential:
                    continue
                inner = [
                    (unit_start, unit_end)
                    for unit_start, unit_end in source_units
                    if start <= unit_start < unit_end <= end
                ]
                if (
                    explode_support
                    and inner
                    and inner[0][0] == start
                    and inner[-1][1] == end
                    and all(
                        left[1] == right[0]
                        for left, right in pairwise(inner)
                    )
                ):
                    support_units.extend(
                        (record_id, unit_start, unit_end)
                        for unit_start, unit_end in inner
                        if _visible_source_span(text, unit_start, unit_end)
                    )
                elif _visible_source_span(text, start, end):
                    support_units.append((record_id, start, end))
            continue
        essential_start, essential_end = _essential_span(record, task)
        spans_by_record[record_id] = [(essential_start, essential_end, True)]
        if (
            published_draft_policy == "identity_paragraph_only"
            and record_id.startswith("ietf:draft:")
        ):
            continue
        support_units.extend(
            (record_id, start, end)
            for start, end in _source_units(text)
            if (end <= essential_start or start >= essential_end)
            and _visible_source_span(text, start, end)
        )

    if len(set(support_priority_record_ids)) != len(support_priority_record_ids) or any(
        not record_id or record_id not in by_id
        for record_id in support_priority_record_ids
    ):
        raise ValueError("support priority record ids are invalid")
    if len(pin_last_leftover_record_ids) != len(
        set(pin_last_leftover_record_ids)
    ) or any(
        not record_id or record_id not in by_id
        for record_id in pin_last_leftover_record_ids
    ):
        raise ValueError("pin-last leftover record ids are invalid")
    priority = {
        record_id: index for index, record_id in enumerate(support_priority_record_ids)
    }
    support_units.sort(key=lambda item: priority.get(item[0], len(priority)))

    selected_units: list[tuple[str, int, int]] = []
    target = min(lower + margin, upper)

    def materialize() -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        seen_text: set[str] = set()
        for record in records:
            record_id = str(record["record_id"])
            selected_supports = [
                (start, end) for rid, start, end in selected_units if rid == record_id
            ]
            if record_id not in chunked_record_ids:
                selected_supports = _merge_units(selected_supports)
            spans = [
                *spans_by_record[record_id],
                *((start, end, False) for start, end in selected_supports),
            ]
            for start, end, is_essential in spans:
                text = str(record["text"])[start:end]
                if not text.strip():
                    if is_essential:
                        raise ValueError(
                            f"{record_id} essential artifact bytes are blank"
                        )
                    continue
                digest = hashlib.sha256(text.encode()).hexdigest()
                if digest in seen_text:
                    if is_essential:
                        raise ValueError(
                            f"{record_id} essential artifact bytes are duplicated"
                        )
                    continue
                seen_text.add(digest)
                artifacts.append(
                    {
                        "artifact_id": (
                            f"{record_id}:chars:"
                            f"{start:0{span_id_width}d}-{end:0{span_id_width}d}:"
                            f"{digest[:12]}"
                        ),
                        "record_id": record_id,
                        "char_start": start,
                        "char_end": end,
                        "text": text,
                        "text_sha256": digest,
                        "source_sha256": record["source_sha256"],
                        "source_url": record["source_url"],
                        "essential": is_essential,
                    }
                )
        artifacts.sort(
            key=lambda item: (
                str(by_id[item["record_id"]]["occurred_at"]),
                item["record_id"],
                item["char_start"],
            )
        )
        for record_id in chunked_record_ids:
            selected = sorted(
                (item["char_start"], item["char_end"])
                for item in artifacts
                if item["record_id"] == record_id
            )
            if any(left[1] > right[0] for left, right in pairwise(selected)):
                raise ValueError(f"{record_id} natural chunks overlap")
        return artifacts

    question = str(task["question"])

    def measure_units(
        units: list[tuple[str, int, int]],
    ) -> tuple[list[dict[str, Any]], int]:
        selected_units[:] = units
        artifacts = materialize()
        tokens = token_counter(
            render_ietf_cross_spec_prompt(
                question, SEP.join(x["text"] for x in artifacts), "first"
            )
        )
        return artifacts, tokens

    def measure(prefix_size: int) -> tuple[list[dict[str, Any]], int]:
        return measure_units(support_units[:prefix_size])

    left, right = 0, len(support_units)
    while left < right:
        middle = (left + right) // 2
        _artifacts, tokens = measure(middle)
        if tokens < target:
            left = middle + 1
        else:
            right = middle
    artifacts, tokens = measure(left)
    if tokens > upper and left:
        fallback_units = support_units[: left - 1]
        artifacts, tokens = measure_units(fallback_units)
        for unit in support_units[left - 1 :]:
            candidate_units = [*fallback_units, unit]
            candidate_artifacts, candidate_tokens = measure_units(candidate_units)
            if candidate_tokens > upper:
                continue
            fallback_units = candidate_units
            artifacts, tokens = candidate_artifacts, candidate_tokens
            if tokens >= target:
                break
    if not lower <= tokens <= upper:
        raise ValueError(
            f"{bucket} natural exact-band blocker: observed={tokens}, band={lower}-{upper}"
        )
    if len({item["text_sha256"] for item in artifacts}) != len(artifacts):
        raise ValueError(f"{bucket} natural pack has duplicate artifact bytes")
    return artifacts, tokens


def build(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.ietf-http3-generation-config.v1":
        raise ValueError("unsupported IETF HTTP/3 generation config")
    source_dir = Path(config["source_inventory_dir"])
    inventory_path = source_dir / config["fetch_inventory_file"]
    inventory_raw = inventory_path.read_bytes()
    if hashlib.sha256(inventory_raw).hexdigest() != config["fetch_inventory_sha256"]:
        raise ValueError("HTTP/3 fetch inventory hash changed")
    signed_path = source_dir / config["signed_workflow_file"]
    signed = json.loads(signed_path.read_text())
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None or not verify_attestation(
        signed, source_key, purpose="source_manifest"
    ):
        raise ValueError("HTTP/3 signed workflow attestation is invalid")
    if hashlib.sha256(signed_path.read_bytes()).hexdigest() != config[
        "signed_workflow_sha256"
    ]:
        raise ValueError("HTTP/3 signed workflow hash changed")
    manifest = {key: value for key, value in signed.items() if key != "attestation"}
    task = build_ietf_http3_quic_requirement_task(manifest)
    materialized = materialize_ietf_http3_quic_counterfactual(
        task,
        evidence_id=str(
            config["packing"].get("counterfactual_evidence_id") or "transport_current"
        ),
    )

    sidecar_key = attestation_key_from_env("task_replay_sidecar")
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if sidecar_key is None or candidate_key is None:
        raise ValueError("HTTP/3 generate requires local probe source and candidate keys")
    source_key = sidecar_key
    adapter_id, adapter_revision, schema_version = IETF_HTTP3_QUIC_TASK_REPLAY_ADAPTER
    tokenizer = config["tokenizer"]
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
                    "length_bucket": "64k",
                    "content_sha256": "0" * 64,
                }
            ],
        },
        source_attestation_key=source_key,
    )
    provisional_raw = _canonical_bytes(provisional_sidecar)
    provisional_path = (
        Path(config["output_dir"]) / ".provisional-sidecar.json"
    ).resolve()
    provisional_path.parent.mkdir(parents=True, exist_ok=True)
    provisional_path.write_bytes(provisional_raw)
    binding = task_replay_sidecar_binding(
        provisional_raw, source_attestation_key=source_key
    )
    loaded = load_task_replay_sidecar(
        provisional_path.parent,
        provisional_path.name,
        binding,
        source_attestation_key=source_key,
    )
    token_counter = task_sidecar_token_counter(loaded)

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
            chunk_max_tokens=int(config["packing"].get("chunk_max_tokens") or 0),
            span_id_width=int(config["packing"].get("span_id_width") or 0),
            isolate_evidence_ids=frozenset(
                config["packing"].get("isolate_evidence_ids") or []
            ),
            counterfactual_companion_evidence_id=str(
                config["packing"].get("counterfactual_companion_evidence_id") or ""
            ),
            counterfactual_evidence_id=str(
                config["packing"].get("counterfactual_evidence_id") or ""
            ),
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
        )
        classifications = [
            {
                "artifact_id": item["artifact_id"],
                "workflow_id": config.get(
                    "workflow_id", "p57-ietf-http3-quic-requirement-v1"
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
            "schema_version": "longworld.ietf-http3-generation-candidate.v1",
            "world_id": config.get("world_id", "ietf-http3-quic-requirement-v1"),
            "query_id": (
                f"{config.get('world_id', 'ietf-http3-quic-requirement-v1')}:{bucket}"
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
                f"{bucket} HTTP/3 parent replay is not the bound codebook: "
                f"{replay['answer']}"
            )
        if counterfactual_replay["answer"] == replay["answer"]:
            raise ValueError(f"{bucket} HTTP/3 counterfactual collapsed onto the parent")
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
            raise ValueError("IETF essential artifact minimization failed")
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
        source_attestation_key=source_key,
    )
    sidecar_raw = _canonical_bytes(sidecar)
    sidecar_binding = task_replay_sidecar_binding(
        sidecar_raw, source_attestation_key=source_key
    )
    signed: list[dict[str, Any]] = []
    for candidate in unsigned:
        candidate["task_replay_sidecar"] = dict(sidecar_binding)
        signed.append(
            attach_attestation(
                candidate, candidate_key, purpose=CANDIDATE_ATTESTATION_PURPOSE
            )
        )

    output_dir = Path(config["output_dir"]).resolve()
    candidates_raw = b"".join(_canonical_bytes(row) for row in signed)
    (output_dir / "TASK_REPLAY_SIDECAR.json").write_bytes(sidecar_raw)
    (output_dir / "parents.jsonl").write_bytes(candidates_raw)
    provisional_path.unlink()
    receipt = {
        "schema_version": "longworld.ietf-http3-generation-receipt.v1",
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
