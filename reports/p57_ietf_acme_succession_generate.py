"""Build ACME issuance succession parents without HTTP/3 5-field packing."""

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
    build_ietf_acme_issuance_succession_task,
    materialize_ietf_acme_issuance_counterfactual,
    render_ietf_cross_spec_prompt,
)
from longworld.core.taskpromotion import task_sidecar_token_counter
from longworld.core.taskproof import replay_ietf_cross_spec_candidate
from longworld.core.p57pipeline import MAX_PARENT_ARTIFACTS
from longworld.core.taskpromotion import _dossier_spread
from longworld.core.taskreplaysidecar import (
    IETF_ACME_ISSUANCE_TASK_REPLAY_ADAPTER,
    build_task_replay_sidecar,
    load_task_replay_sidecar,
    task_candidate_content_commitment,
    task_replay_sidecar_binding,
)
from reports.p57_ietf_http3_quic_requirement_generate import (
    _artifacts_for_bucket,
    _canonical_bytes,
    _sha256,
    _source_units,
    _visible_source_span,
)

_COMPILE_BLOCKER = (
    "ACME 32k needs a signed 8555-only workflow; leftover RFCs 8737/8738/8823/"
    "9444/9773 are not 8555 relation targets, unique 82082 must not pad to 128k, "
    "and 8555-only 48577 is the honest 32k band"
)
_ACME_MIN_ZIPPER_TAIL = 12
_ACME_MAX_ZIPPER_TAIL = 24
_ACME_WINDOW_16K = 16_384
_ACME_MIN_GOLD_PREFIX_CHARS = 24000
_ACME_GOLD_TRAIL_MIN_GROUPS = 2
_ACME_MULTI_QUOTE_GAP_CHARS = 8000


def _acme_chronology(
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
        raise ValueError("ACME leftover has no visible source units")
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


def _split_acme_multi_quote_gold(
    artifacts: list[dict[str, Any]],
    *,
    task: dict[str, Any],
    span_id_width: int,
) -> list[dict[str, Any]]:
    """Split an 8555 gold chunk that holds two distant quotes.

    Three gold artifacts let BM25 top-3 retrieve the answer. RFC 8555 has four
    quote clusters; the middle 8k chunk currently holds directory_nonce and
    domain_validation together.
    """
    quotes_by_record: dict[str, list[tuple[int, int]]] = {}
    for evidence in task.get("evidence_items") or []:
        quotes_by_record.setdefault(str(evidence["record_id"]), []).append(
            (int(evidence["char_start"]), int(evidence["char_end"]))
        )
    output: list[dict[str, Any]] = []
    for item in artifacts:
        if not item.get("essential"):
            output.append(item)
            continue
        record_id = str(item["record_id"])
        start = int(item["char_start"])
        end = int(item["char_end"])
        quotes = sorted(
            (quote_start, quote_end)
            for quote_start, quote_end in quotes_by_record.get(record_id, [])
            if start < quote_end <= end
        )
        split_at: int | None = None
        for (_left_start, left_end), (right_start, _right_end) in zip(
            quotes, quotes[1:]
        ):
            if right_start - left_end >= _ACME_MULTI_QUOTE_GAP_CHARS:
                split_at = left_end + (right_start - left_end) // 2
                break
        if split_at is None:
            output.append(item)
            continue
        text = str(item["text"])
        rel_split = split_at - start
        units = _source_units(text)
        snapped = rel_split
        for unit_start, unit_end in units:
            if unit_start <= rel_split <= unit_end:
                snapped = unit_end
                break
        if snapped <= 0 or snapped >= len(text):
            output.append(item)
            continue
        left_text = text[:snapped]
        right_text = text[snapped:]
        if not (
            _visible_source_span(text, 0, snapped)
            and _visible_source_span(text, snapped, len(text))
        ):
            output.append(item)
            continue
        left_digest = hashlib.sha256(left_text.encode()).hexdigest()
        right_digest = hashlib.sha256(right_text.encode()).hexdigest()
        output.append(
            {
                **item,
                "artifact_id": (
                    f"{record_id}:chars:"
                    f"{start:0{span_id_width}d}-"
                    f"{start + snapped:0{span_id_width}d}:"
                    f"{left_digest[:12]}"
                ),
                "char_start": start,
                "char_end": start + snapped,
                "text": left_text,
                "text_sha256": left_digest,
                "essential": True,
            }
        )
        output.append(
            {
                **item,
                "artifact_id": (
                    f"{record_id}:chars:"
                    f"{start + snapped:0{span_id_width}d}-"
                    f"{end:0{span_id_width}d}:"
                    f"{right_digest[:12]}"
                ),
                "char_start": start + snapped,
                "char_end": end,
                "text": right_text,
                "text_sha256": right_digest,
                "essential": True,
            }
        )
    output.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["record_id"]),
            int(item["char_start"]),
        )
    )
    return output


def _split_acme_gold_trailing_leftover(
    artifacts: list[dict[str, Any]],
    *,
    task: dict[str, Any],
    span_id_width: int,
    max_artifacts: int = MAX_PARENT_ARTIFACTS,
    min_groups: int = _ACME_GOLD_TRAIL_MIN_GROUPS,
) -> list[dict[str, Any]]:
    """Peel leftover after the last gold quote so 8555 golds are not chrono-adjacent.

    Three 8k gold chunks zipper to spread 0/2/4; a 16k intersecting window then
    retrieves the answer. Trailing leftover from the first gold sits between
    golds in chronology and pushes later gold past 16k.
    """
    quote_ends: dict[str, list[int]] = {}
    for evidence in task.get("evidence_items") or []:
        record_id = str(evidence.get("record_id") or "")
        quote_ends.setdefault(record_id, []).append(int(evidence["char_end"]))
    gold_indexes = [
        index for index, item in enumerate(artifacts) if item.get("essential")
    ]
    last_gold = max(gold_indexes) if gold_indexes else -1
    output: list[dict[str, Any]] = []
    for index, item in enumerate(artifacts):
        if not item.get("essential"):
            output.append(item)
            continue
        record_id = str(item["record_id"])
        start = int(item["char_start"])
        end = int(item["char_end"])
        last_quote = max(
            (quote for quote in quote_ends.get(record_id, []) if start < quote <= end),
            default=None,
        )
        text = str(item["text"])
        if last_quote is None:
            output.append(item)
            continue
        rel_quote = last_quote - start
        units = _source_units(text)
        prefix_end = rel_quote
        for unit_start, unit_end in units:
            if unit_start < rel_quote:
                prefix_end = max(prefix_end, unit_end)
        if prefix_end < _ACME_MIN_GOLD_PREFIX_CHARS:
            for _unit_start, unit_end in units:
                if unit_end >= _ACME_MIN_GOLD_PREFIX_CHARS and unit_end >= rel_quote:
                    prefix_end = unit_end
                    break
        suffix_units = [
            (unit_start, unit_end)
            for unit_start, unit_end in units
            if unit_start >= prefix_end
        ]
        if len(suffix_units) < 2:
            output.append(item)
            continue
        prefix_text = text[:prefix_end]
        if not _visible_source_span(text, 0, prefix_end):
            output.append(item)
            continue
        prefix_digest = hashlib.sha256(prefix_text.encode()).hexdigest()
        output.append(
            {
                **item,
                "artifact_id": (
                    f"{record_id}:chars:"
                    f"{start:0{span_id_width}d}-"
                    f"{start + prefix_end:0{span_id_width}d}:"
                    f"{prefix_digest[:12]}"
                ),
                "char_start": start,
                "char_end": start + prefix_end,
                "text": prefix_text,
                "text_sha256": prefix_digest,
                "essential": True,
            }
        )
        shifted = [
            (unit_start - prefix_end, unit_end - prefix_end)
            for unit_start, unit_end in suffix_units
        ]
        budget = max(
            1,
            max_artifacts - len(output) - (len(artifacts) - index - 1),
        )
        groups = _coarsen_source_units(
            shifted,
            min_groups=min(min_groups, len(shifted), budget),
            max_groups=min(min_groups, len(shifted), budget),
        )
        suffix_text = text[prefix_end:]
        for group_start, group_end in groups:
            chunk = suffix_text[group_start:group_end]
            if not _visible_source_span(suffix_text, group_start, group_end):
                continue
            digest = hashlib.sha256(chunk.encode()).hexdigest()
            output.append(
                {
                    **item,
                    "artifact_id": (
                        f"{record_id}:chars:"
                        f"{start + prefix_end + group_start:0{span_id_width}d}-"
                        f"{start + prefix_end + group_end:0{span_id_width}d}:"
                        f"{digest[:12]}"
                    ),
                    "char_start": start + prefix_end + group_start,
                    "char_end": start + prefix_end + group_end,
                    "text": chunk,
                    "text_sha256": digest,
                    "essential": False,
                }
            )
    if len(output) > max_artifacts:
        raise ValueError(
            f"ACME gold trailing leftover exploded to {len(output)}>{max_artifacts}"
        )
    output.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["record_id"]),
            int(item["char_start"]),
        )
    )
    return output


def _explode_acme_zipper_tail(
    artifacts: list[dict[str, Any]],
    *,
    span_id_width: int,
    max_artifacts: int = MAX_PARENT_ARTIFACTS,
    min_tail: int = _ACME_MIN_ZIPPER_TAIL,
    max_tail: int = _ACME_MAX_ZIPPER_TAIL,
) -> list[dict[str, Any]]:
    """Split pin-last RFC 8555 leftover into coarsened blank-line groups."""
    gold_indexes = [index for index, item in enumerate(artifacts) if item.get("essential")]
    if not gold_indexes:
        raise ValueError("ACME packing has no gold artifacts")
    last_gold = max(gold_indexes)
    head = artifacts[: last_gold + 1]
    tail = artifacts[last_gold + 1 :]
    if not tail:
        raise ValueError(
            "ACME packing needs leftover after RFC 8555 gold so the zipper "
            "does not pair late gold with abstract gold"
        )
    if any(item.get("essential") for item in tail):
        raise ValueError("ACME zipper tail must not contain gold")
    budget = max_artifacts - len(head)
    if budget < min_tail:
        raise ValueError("ACME zipper tail cannot fit under the parent artifact cap")
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
            "ACME zipper tail is still one leftover artifact; coarsened "
            f"groups={len(exploded)}<{min_tail}"
        )
    packed = head + exploded
    if len(packed) > max_artifacts:
        raise ValueError(
            f"ACME zipper tail exploded to {len(packed)}>{max_artifacts} artifacts"
        )
    packed.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["record_id"]),
            int(item["char_start"]),
        )
    )
    return packed


def _require_acme_zipper_clears_16k(
    artifacts: list[dict[str, Any]],
    token_counter,
    question: str,
) -> list[dict[str, Any]]:
    spread = _dossier_spread(_acme_chronology(artifacts))
    gold_positions = [
        index
        for index, (item, _document) in enumerate(spread)
        if item.get("essential")
    ]
    if len(gold_positions) < 2:
        raise ValueError("ACME zipper needs at least two gold artifacts")
    start, stop = min(gold_positions), max(gold_positions) + 1
    span_tokens = token_counter(
        render_ietf_cross_spec_prompt(
            question,
            SEP.join(document for _item, document in spread[start:stop]),
            "first",
        )
    )
    if span_tokens <= _ACME_WINDOW_16K:
        raise ValueError(
            "ACME zipper still retrieves gold inside 16k: "
            f"spread={start}:{stop} tokens={span_tokens}"
        )
    gold_ids = {item["artifact_id"] for item, _document in spread if item.get("essential")}
    for window_start in range(len(spread)):
        parts: list[str] = []
        hit: set[str] = set()
        for item, document in spread[window_start:]:
            parts.append(document)
            if item.get("essential"):
                hit.add(item["artifact_id"])
            window_tokens = token_counter(
                render_ietf_cross_spec_prompt(
                    question, SEP.join(parts), "first"
                )
            )
            if gold_ids <= hit and window_tokens <= _ACME_WINDOW_16K:
                raise ValueError(
                    "ACME zipper 16k window contains all gold artifacts: "
                    f"start={window_start} tokens={window_tokens}"
                )
            if window_tokens > _ACME_WINDOW_16K:
                if gold_ids <= hit:
                    raise ValueError(
                        "ACME zipper 16k intersecting window contains all gold "
                        f"artifacts: start={window_start} tokens={window_tokens}"
                    )
                break
    ordered = sorted(
        artifacts,
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["record_id"]),
            int(item["char_start"]),
        ),
    )
    gold_ids = {item["artifact_id"] for item in ordered if item.get("essential")}
    for window_start in range(len(ordered)):
        parts = []
        hit: set[str] = set()
        for item in ordered[window_start:]:
            parts.append(str(item["text"]))
            if item.get("essential"):
                hit.add(item["artifact_id"])
            window_tokens = token_counter(
                render_ietf_cross_spec_prompt(question, SEP.join(parts), "first")
            )
            if gold_ids <= hit and window_tokens <= _ACME_WINDOW_16K:
                raise ValueError(
                    "ACME chronology 16k window contains all gold artifacts: "
                    f"start={window_start} tokens={window_tokens}"
                )
            if window_tokens > _ACME_WINDOW_16K:
                if gold_ids <= hit:
                    raise ValueError(
                        "ACME chronology 16k intersecting window contains all gold "
                        f"artifacts: start={window_start} tokens={window_tokens}"
                    )
                break
    return artifacts


def _acme_swap_end_tail_for_between_gold(
    artifacts: list[dict[str, Any]],
    *,
    task: dict[str, Any],
    span_id_width: int,
) -> list[dict[str, Any]]:
    """Pack the 8555 gap between golds instead of the RFC-end leftover.

    Full/cf views concatenate chronology, not the zipper. Pin-last end leftover
    leaves golds in the first 16k of RFC 8555.
    """
    rfc_id = "ietf:rfc:8555"
    record = next(
        item
        for item in task["source_manifest"]["records"]
        if str(item["record_id"]) == rfc_id
    )
    full = str(record["text"])
    golds = sorted(
        (
            item
            for item in artifacts
            if item.get("essential") and str(item["record_id"]) == rfc_id
        ),
        key=lambda item: int(item["char_start"]),
    )
    if len(golds) < 2:
        return artifacts
    packed = sorted(
        (int(item["char_start"]), int(item["char_end"]))
        for item in artifacts
        if str(item["record_id"]) == rfc_id
    )
    first_gold_start = int(golds[0]["char_start"])
    last_gold_start = int(golds[-1]["char_start"])
    last_gold_end = int(golds[-1]["char_end"])
    cursor = 0
    gaps: list[tuple[int, int]] = []
    for start, end in packed:
        if cursor < start:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(full):
        gaps.append((cursor, len(full)))
    between = [
        gap
        for gap in gaps
        if gap[0] >= first_gold_start
        and gap[1] <= last_gold_start
        and gap[1] - gap[0] >= 4000
        and _visible_source_span(full, gap[0], gap[1])
    ]
    if not between:
        return artifacts
    gap_start, gap_end = max(between, key=lambda gap: gap[1] - gap[0])
    kept = [
        item
        for item in artifacts
        if not (
            str(item["record_id"]) == rfc_id
            and not item.get("essential")
            and int(item["char_start"]) >= last_gold_end
        )
    ]
    chunk = full[gap_start:gap_end]
    digest = hashlib.sha256(chunk.encode()).hexdigest()
    kept.append(
        {
            **golds[-1],
            "artifact_id": (
                f"{rfc_id}:chars:"
                f"{gap_start:0{span_id_width}d}-"
                f"{gap_end:0{span_id_width}d}:"
                f"{digest[:12]}"
            ),
            "char_start": gap_start,
            "char_end": gap_end,
            "text": chunk,
            "text_sha256": digest,
            "essential": False,
            "source_sha256": record["source_sha256"],
            "source_url": record["source_url"],
        }
    )
    kept.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["record_id"]),
            int(item["char_start"]),
        )
    )
    return kept


def _merge_acme_adjacent_leftover(
    artifacts: list[dict[str, Any]],
    *,
    span_id_width: int,
) -> list[dict[str, Any]]:
    """Merge the last two leftover artifacts to shed SEP tokens back into the 32k band."""
    gold_indexes = [
        index for index, item in enumerate(artifacts) if item.get("essential")
    ]
    if not gold_indexes:
        raise ValueError("ACME merge needs gold artifacts")
    last_gold = max(gold_indexes)
    merge_at = None
    search_from = last_gold + 1
    if len(artifacts) - last_gold - 1 < _ACME_MIN_ZIPPER_TAIL + 1:
        search_from = 1
    for index in range(len(artifacts) - 1, search_from - 1, -1):
        if index <= last_gold and search_from == last_gold + 1:
            break
        left, right = artifacts[index - 1], artifacts[index]
        if index - 1 < 0:
            break
        if (
            not left.get("essential")
            and not right.get("essential")
            and str(left["record_id"]) == str(right["record_id"])
            and int(left["char_end"]) == int(right["char_start"])
        ):
            merge_at = index - 1
            break
    if merge_at is None:
        raise ValueError("ACME overflow leftover is not an adjacent zipper-tail pair")
    left, right = artifacts[merge_at], artifacts[merge_at + 1]
    text = str(left["text"]) + str(right["text"])
    digest = hashlib.sha256(text.encode()).hexdigest()
    record_id = str(left["record_id"])
    merged = {
        **left,
        "artifact_id": (
            f"{record_id}:chars:"
            f"{int(left['char_start']):0{span_id_width}d}-"
            f"{int(right['char_end']):0{span_id_width}d}:"
            f"{digest[:12]}"
        ),
        "char_start": int(left["char_start"]),
        "char_end": int(right["char_end"]),
        "text": text,
        "text_sha256": digest,
        "essential": False,
    }
    return artifacts[:merge_at] + [merged] + artifacts[merge_at + 2 :]


def _trim_acme_between_gold_until_band(
    artifacts: list[dict[str, Any]],
    *,
    span_id_width: int,
    token_counter,
    question: str,
    lower: int,
    upper: int,
) -> tuple[list[dict[str, Any]], int]:
    golds = sorted(
        (item for item in artifacts if item.get("essential")),
        key=lambda item: (str(item["record_id"]), int(item["char_start"])),
    )
    if len(golds) < 2:
        raise ValueError("ACME trim needs two gold artifacts")
    first_gold_end = int(golds[0]["char_end"])
    last_gold_start = int(golds[-1]["char_start"])
    between = [
        (index, item)
        for index, item in enumerate(artifacts)
        if not item.get("essential")
        and str(item["record_id"]) == "ietf:rfc:8555"
        and first_gold_end <= int(item["char_start"]) < last_gold_start
    ]
    if not between:
        raise ValueError("ACME trim needs leftover between gold artifacts")
    index, item = max(between, key=lambda value: int(value[1]["char_end"]) - int(value[1]["char_start"]))
    text = str(item["text"])
    units = _source_units(text)
    observed = token_counter(
        render_ietf_cross_spec_prompt(
            question, SEP.join(part["text"] for part in artifacts), "first"
        )
    )
    while observed > upper and len(units) > 2:
        trial_units = units[:-1]
        trimmed = text[: trial_units[-1][1]]
        if not _visible_source_span(text, 0, trial_units[-1][1]):
            break
        digest = hashlib.sha256(trimmed.encode()).hexdigest()
        start = int(item["char_start"])
        trial_item = {
            **item,
            "artifact_id": (
                f"{item['record_id']}:chars:"
                f"{start:0{span_id_width}d}-"
                f"{start + trial_units[-1][1]:0{span_id_width}d}:"
                f"{digest[:12]}"
            ),
            "char_end": start + trial_units[-1][1],
            "text": trimmed,
            "text_sha256": digest,
            "essential": False,
        }
        trial = list(artifacts)
        trial[index] = trial_item
        trial_tokens = token_counter(
            render_ietf_cross_spec_prompt(
                question, SEP.join(part["text"] for part in trial), "first"
            )
        )
        if trial_tokens < lower:
            break
        artifacts[index] = trial_item
        item = trial_item
        units = trial_units
        observed = trial_tokens
    return artifacts, observed


def build(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "longworld.ietf-acme-generation-config.v1":
        raise ValueError("unsupported IETF ACME generation config")
    packing = config.get("packing") or {}
    if packing.get("isolate_evidence_ids"):
        raise ValueError("ACME packing must not isolate HTTP/3-style quote artifacts")
    buckets = config.get("length_buckets") or {}
    if any(bucket != "32k" for bucket in buckets):
        raise ValueError("ACME packing is 32k only; unique 48577 must not pad to 64k")
    if "32k" not in buckets:
        raise ValueError("ACME packing requires the 32k band")
    signed_name = config.get("signed_workflow_file")
    source_dir = Path(config["source_inventory_dir"]) if "source_inventory_dir" in config else None
    signed_path = (source_dir / signed_name) if source_dir is not None and signed_name else None
    if signed_path is None or not signed_path.is_file():
        raise ValueError(_COMPILE_BLOCKER)
    bucket_chunk = int(
        (packing.get("chunk_max_tokens_by_bucket") or {}).get("32k")
        or packing.get("chunk_max_tokens")
        or 0
    )
    if bucket_chunk < 8192:
        raise ValueError("ACME 32k leftover chunks must stay thick enough for 4k/8k zipper ends")
    inventory_path = source_dir / config["fetch_inventory_file"]
    inventory_raw = inventory_path.read_bytes()
    if hashlib.sha256(inventory_raw).hexdigest() != config["fetch_inventory_sha256"]:
        raise ValueError("ACME fetch inventory hash changed")
    signed = json.loads(signed_path.read_text())
    source_key = attestation_key_from_env("source_manifest")
    if source_key is None or not verify_attestation(
        signed, source_key, purpose="source_manifest"
    ):
        raise ValueError("ACME signed workflow attestation is invalid")
    if hashlib.sha256(signed_path.read_bytes()).hexdigest() != config["signed_workflow_sha256"]:
        raise ValueError("ACME signed workflow hash changed")
    manifest = {key: value for key, value in signed.items() if key != "attestation"}
    task = build_ietf_acme_issuance_succession_task(manifest)
    materialized = materialize_ietf_acme_issuance_counterfactual(
        task,
        evidence_id=str(packing.get("counterfactual_evidence_id") or "current_protocol"),
    )

    sidecar_key = attestation_key_from_env("task_replay_sidecar")
    candidate_key = attestation_key_from_env(CANDIDATE_ATTESTATION_PURPOSE)
    if sidecar_key is None or candidate_key is None:
        raise ValueError("ACME generate requires local probe source and candidate keys")
    adapter_id, adapter_revision, schema_version = IETF_ACME_ISSUANCE_TASK_REPLAY_ADAPTER
    tokenizer = config["tokenizer"]
    output_dir = Path(config["output_dir"]).resolve()
    if (output_dir / "parents.jsonl").exists():
        raise ValueError(f"ACME generate refuses to overwrite {output_dir}")
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
        span_id_width = int(packing.get("span_id_width") or 0)
        artifacts = _acme_swap_end_tail_for_between_gold(
            artifacts,
            task=task,
            span_id_width=span_id_width,
        )
        artifacts = _split_acme_multi_quote_gold(
            artifacts,
            task=task,
            span_id_width=span_id_width,
        )
        artifacts = _split_acme_gold_trailing_leftover(
            artifacts,
            task=task,
            span_id_width=span_id_width,
        )
        artifacts = _explode_acme_zipper_tail(
            artifacts,
            span_id_width=span_id_width,
        )
        seen_text: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in artifacts:
            digest = str(item["text_sha256"])
            if digest in seen_text:
                if item.get("essential"):
                    raise ValueError("ACME gold artifact text is duplicated")
                continue
            seen_text.add(digest)
            deduped.append(item)
        artifacts = deduped
        question = str(task["question"])
        observed_tokens = token_counter(
            render_ietf_cross_spec_prompt(
                question, SEP.join(item["text"] for item in artifacts), "first"
            )
        )
        while observed_tokens > int(upper):
            artifacts, observed_tokens = _trim_acme_between_gold_until_band(
                artifacts,
                span_id_width=span_id_width,
                token_counter=token_counter,
                question=question,
                lower=int(lower),
                upper=int(upper),
            )
            if observed_tokens > int(upper):
                try:
                    artifacts = _merge_acme_adjacent_leftover(
                        artifacts, span_id_width=span_id_width
                    )
                except ValueError:
                    raise ValueError(
                        f"{bucket} ACME zipper-tail pack left the exact band: "
                        f"observed={observed_tokens}, band={lower}-{upper}"
                    )
                observed_tokens = token_counter(
                    render_ietf_cross_spec_prompt(
                        question, SEP.join(item["text"] for item in artifacts), "first"
                    )
                )
        artifacts = _require_acme_zipper_clears_16k(
            artifacts, token_counter, question
        )
        observed_tokens = token_counter(
            render_ietf_cross_spec_prompt(
                question, SEP.join(item["text"] for item in artifacts), "first"
            )
        )
        if not int(lower) <= observed_tokens <= int(upper):
            raise ValueError(
                f"{bucket} ACME zipper-tail pack left the exact band: "
                f"observed={observed_tokens}, band={lower}-{upper}"
            )
        classifications = [
            {
                "artifact_id": item["artifact_id"],
                "workflow_id": config.get(
                    "workflow_id", "p57-ietf-acme-issuance-succession-v1"
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
            "schema_version": "longworld.ietf-acme-generation-candidate.v1",
            "world_id": config.get("world_id", "ietf-acme-issuance-succession-v1"),
            "query_id": (
                f"{config.get('world_id', 'ietf-acme-issuance-succession-v1')}:{bucket}"
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
                f"{bucket} ACME parent replay is not the bound codebook: "
                f"{replay['answer']}"
            )
        if counterfactual_replay["answer"] == replay["answer"]:
            raise ValueError(f"{bucket} ACME counterfactual collapsed onto the parent")
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
            raise ValueError("IETF ACME essential artifact minimization failed")
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
        "schema_version": "longworld.ietf-acme-generation-receipt.v1",
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
        "blocker": None,
    }
    (output_dir / "GENERATION_RECEIPT.json").write_bytes(_canonical_bytes(receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().config), sort_keys=True))


if __name__ == "__main__":
    main()
