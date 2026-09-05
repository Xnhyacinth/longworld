"""Run aggregate-only P54 EUR-Lex legislative-chain capacity preflight."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import html
import json
import os
import re
import ssl
import tempfile
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from transformers import AutoTokenizer

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p54_eurlex_legislative_chain_preflight_v1.json")
OUTPUT = Path("reports/p54_eurlex_legislative_chain_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"eur-lex.europa.eu"})
# EUR-Lex injects request-specific observability JavaScript for application-like
# user agents. Its curl representation is byte-stable and contains the same legal text.
USER_AGENT = "curl/8.10.1"
WORD = re.compile(r"[a-z0-9]+")
SPACE = re.compile(r"\s+")
BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "p"})


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalize(text: str) -> str:
    return SPACE.sub(" ", html.unescape(text).replace("\xa0", " ")).strip()


def _canonical(text: str) -> str:
    return " ".join(WORD.findall(text.lower()))


def _unit(
    artifact_id: str, source_id: str, text: str, *, source_ordinal: int | None = None
) -> dict[str, Any]:
    normalized = _normalize(text)
    unit = {
        "artifact_id": artifact_id,
        "source_id": source_id,
        "text": normalized,
        "text_sha256": _sha256(normalized.encode()),
        "canonical": _canonical(normalized),
    }
    if source_ordinal is not None:
        unit["source_ordinal"] = source_ordinal
    return unit


class _SemanticHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._block_tag: str | None = None
        self._block_parts: list[str] = []
        self.document_parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._finish_block()
            self._block_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if not self._skip_depth and tag == self._block_tag:
            self._finish_block()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.document_parts.append(data)
        if self._block_tag is not None:
            self._block_parts.append(data)

    def close(self) -> None:
        super().close()
        self._finish_block()

    def _finish_block(self) -> None:
        text = _normalize(" ".join(self._block_parts))
        if text:
            self.blocks.append(text)
        self._block_tag = None
        self._block_parts = []


def _extract_semantic_units(
    raw: bytes, *, source_id: str, minimum_characters: int
) -> tuple[str, list[dict[str, Any]]]:
    parser = _SemanticHTMLParser()
    parser.feed(raw.decode("utf-8"))
    parser.close()
    document_text = _normalize(" ".join(parser.document_parts))
    units = [
        _unit(
            f"{source_id}:block:{index:05d}",
            source_id,
            text,
            source_ordinal=index,
        )
        for index, text in enumerate(parser.blocks)
        if len(_canonical(text)) >= minimum_characters
    ]
    if not document_text or not units:
        raise ValueError(f"P54 source has no semantic text: {source_id}")
    return document_text, units


def _shingles(text: str, size: int) -> frozenset[tuple[str, ...]]:
    words = text.split()
    if len(words) < size:
        return frozenset({tuple(words)})
    return frozenset(zip(*(words[offset:] for offset in range(size)), strict=False))


def _near_deduplicate(
    units: list[dict[str, Any]], *, size: int, threshold: float
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    kept_shingles: list[frozenset[tuple[str, ...]]] = []
    inverted: dict[tuple[str, ...], list[int]] = defaultdict(list)
    removed = 0
    for unit in sorted(
        units, key=lambda item: (item["text_sha256"], item["artifact_id"])
    ):
        shingles = _shingles(unit["canonical"], size)
        candidates: set[int] = set()
        for shingle in shingles:
            candidates.update(inverted[shingle])
        duplicate = False
        for index in sorted(candidates):
            prior = kept_shingles[index]
            if (
                min(len(shingles), len(prior)) / max(len(shingles), len(prior))
                < threshold
            ):
                continue
            union = shingles | prior
            similarity = len(shingles & prior) / len(union) if union else 1.0
            if similarity >= threshold:
                duplicate = True
                break
        if duplicate:
            removed += 1
            continue
        index = len(kept)
        kept.append(unit)
        kept_shingles.append(shingles)
        for shingle in shingles:
            inverted[shingle].append(index)
    return kept, removed


def _fetch(source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    url = source["url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P54 source URL: {url}")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(
        request, timeout=300, context=ssl.create_default_context()
    ) as response:
        raw = response.read(int(source["max_bytes"]) + 1)
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P54 redirect target: {final_url}")
        if response.status != 200 or not raw or len(raw) > source["max_bytes"]:
            raise ValueError(f"invalid P54 response: {url}")
    if (
        len(raw) != source["expected_bytes"]
        or _sha256(raw) != source["expected_sha256"]
    ):
        raise ValueError(f"P54 source receipt changed: {source['source_id']}")
    return raw, {
        "source_id": source["source_id"],
        "url": url,
        "final_url": final_url,
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "raw_source_persisted": False,
    }


def _token_lengths(tokenizer: Any, units: list[dict[str, Any]]) -> None:
    for offset in range(0, len(units), 128):
        batch = units[offset : offset + 128]
        encoded = tokenizer(
            [item["text"] for item in batch],
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        for item, token_ids in zip(batch, encoded, strict=True):
            item["token_count"] = len(token_ids)


def _matches_rule(artifact: dict[str, Any], rule: dict[str, str]) -> bool:
    return (
        artifact.get("source_id") == rule["source_id"]
        and re.search(
            rule["pattern"], artifact.get("text", ""), flags=re.IGNORECASE | re.DOTALL
        )
        is not None
    )


def _replay_oracle(
    artifacts: list[dict[str, Any]],
    rules: dict[str, dict[str, str]],
    *,
    expected_correction_count: int,
    answer: str | None = None,
) -> dict[str, Any]:
    matched = {
        role: any(_matches_rule(artifact, rule) for artifact in artifacts)
        for role, rule in rules.items()
    }
    correction_count = sum(
        len(re.findall(r"\bOn page\b", artifact.get("text", ""), re.IGNORECASE))
        for artifact in artifacts
        if artifact.get("source_id") == "corrigendum"
    )
    if not all(matched.values()) or correction_count != expected_correction_count:
        return {
            "status": "UNKNOWN",
            "answer": "unknown",
            "matched_roles": matched,
            "correction_count": correction_count,
        }
    return {
        "status": "PASS",
        "answer": answer or "resolved",
        "matched_roles": matched,
        "correction_count": correction_count,
    }


def _find_evidence(units: list[dict[str, Any]], rule: dict[str, str]) -> dict[str, Any]:
    matches = [item for item in units if _matches_rule(item, rule)]
    if not matches:
        raise ValueError(f"P54 oracle evidence is absent: {rule['source_id']}")
    return min(matches, key=lambda item: (len(item["text"]), item["artifact_id"]))


def _pack_exact_band(
    essential: list[dict[str, Any]],
    background: list[dict[str, Any]],
    *,
    lower: int,
    upper: int,
    target_margin: int,
) -> dict[str, Any]:
    selected = list(essential)
    selected_ids = {item["artifact_id"] for item in selected}
    selected_digests = {item["text_sha256"] for item in selected}
    total = sum(int(item["token_count"]) for item in selected)
    target = min(upper, lower + target_margin)
    for item in sorted(
        background, key=lambda value: (value["text_sha256"], value["artifact_id"])
    ):
        if (
            item["artifact_id"] in selected_ids
            or item["text_sha256"] in selected_digests
            or total + int(item["token_count"]) > upper
        ):
            continue
        selected.append(item)
        selected_ids.add(item["artifact_id"])
        selected_digests.add(item["text_sha256"])
        total += int(item["token_count"])
        if total >= target:
            break
    return {
        "feasible": lower <= total <= upper,
        "source_tokens": total,
        "deficit_tokens": max(0, lower - total),
        "artifact_count": len(selected),
        "padding_tokens": 0,
        "cloned_artifacts": 0,
        "split_or_truncated_artifacts": 0,
        "artifacts": selected,
    }


def _order_by_authentic_chain(
    artifacts: list[dict[str, Any]], *, source_order: list[str]
) -> list[dict[str, Any]]:
    if len(source_order) != len(set(source_order)):
        raise ValueError("P54 authentic source order contains duplicates")
    source_ranks = {source_id: rank for rank, source_id in enumerate(source_order)}
    ordering_keys: set[tuple[int, int]] = set()
    for artifact in artifacts:
        source_id = artifact.get("source_id")
        ordinal = artifact.get("source_ordinal")
        if source_id not in source_ranks or not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError("P54 artifact lacks an authentic source position")
        key = (source_ranks[source_id], ordinal)
        if key in ordering_keys:
            raise ValueError("P54 authentic source position is ambiguous")
        ordering_keys.add(key)
    return sorted(
        artifacts,
        key=lambda item: (source_ranks[item["source_id"]], item["source_ordinal"]),
    )


def _artifact_aligned_shortcut_audit(
    artifacts: list[dict[str, Any]],
    rules: dict[str, dict[str, str]],
    *,
    expected_correction_count: int,
    windows: list[int],
) -> dict[str, Any]:
    role_names = tuple(sorted(rules))
    role_masks = [
        {role for role in role_names if _matches_rule(artifact, rules[role])}
        for artifact in artifacts
    ]
    correction_counts = [
        (
            len(re.findall(r"\bOn page\b", artifact.get("text", ""), re.IGNORECASE))
            if artifact.get("source_id") == "corrigendum"
            else 0
        )
        for artifact in artifacts
    ]
    nonzero_corrections = [count for count in correction_counts if count]
    expected_nonzero = [expected_correction_count] if expected_correction_count else []
    if nonzero_corrections != expected_nonzero:
        raise ValueError("P54 shortcut pool has ambiguous corrigendum evidence")
    token_prefix = [0]
    correction_prefix = [0]
    role_prefix = {role: [0] for role in role_names}
    for artifact, matched, correction_count in zip(
        artifacts, role_masks, correction_counts, strict=True
    ):
        cost = int(artifact["token_count"])
        if cost <= 0:
            raise ValueError("P54 shortcut artifact token cost is invalid")
        token_prefix.append(token_prefix[-1] + cost)
        correction_prefix.append(correction_prefix[-1] + correction_count)
        for role in role_names:
            role_prefix[role].append(role_prefix[role][-1] + (role in matched))

    results = {}
    all_insufficient = True
    for limit in windows:
        checked = 0
        witness: list[str] = []
        for start in range(len(artifacts)):
            if int(artifacts[start]["token_count"]) > limit:
                end = start + 1
            else:
                end = (
                    bisect.bisect_right(
                        token_prefix, token_prefix[start] + limit, lo=start + 1
                    )
                    - 1
                )
            checked += 1
            roles_complete = all(
                role_prefix[role][end] - role_prefix[role][start] > 0
                for role in role_names
            )
            corrections = correction_prefix[end] - correction_prefix[start]
            if roles_complete and corrections == expected_correction_count:
                witness = [item["artifact_id"] for item in artifacts[start:end]]
            if witness:
                break
        if witness:
            all_insufficient = False
        results[str(limit)] = {
            "checked_artifact_windows": checked,
            "insufficient": not witness,
            "witness_artifact_ids": witness,
        }
    return {
        "proof_mode": "monotone_maximal_complete_artifact_regex_oracle_replay",
        "all_insufficient": all_insufficient,
        "windows": results,
    }


def _metadata_evidence(raw: bytes, chain: dict[str, Any]) -> dict[str, Any]:
    text = raw.decode("utf-8")
    identifiers = [
        chain["proposal_celex"],
        chain["position_celex"],
        chain["adopted_act_celex"],
        chain["corrigendum_celex"],
        chain["procedure_id"],
    ]
    if any(identifier not in text for identifier in identifiers):
        raise ValueError("P54 metadata omits a selected legislative identifier")
    corrected_pattern = re.compile(
        r"<RESOURCE_LEGAL_CORRECTED_BY_RESOURCE_LEGAL.*?"
        + re.escape(chain["corrigendum_celex"])
        + r".*?</RESOURCE_LEGAL_CORRECTED_BY_RESOURCE_LEGAL>",
        re.DOTALL,
    )
    match = corrected_pattern.search(text)
    if match is None:
        raise ValueError("P54 metadata lacks the explicit corrected-by edge")
    projection = " ".join(
        [
            chain["proposal_celex"],
            chain["position_celex"],
            chain["adopted_act_celex"],
            chain["corrigendum_celex"],
            chain["procedure_id"],
            _normalize(match.group(0)),
        ]
    )
    return _unit("metadata:selected-chain", "metadata", projection, source_ordinal=0)


def _rights_check(raw: bytes) -> dict[str, Any]:
    text = _normalize(raw.decode("utf-8"))
    required = [
        "2011/833/EU",
        "commercial or non-commercial purposes",
        "acknowledge the source of the documents",
    ]
    missing = [phrase for phrase in required if phrase not in text]
    if missing:
        raise ValueError(f"P54 EUR-Lex rights notice changed: {missing}")
    return {
        "technical_rights_preflight": "PASS",
        "legal_opinion": False,
        "legal_documents_reuse_basis": "Decision 2011/833/EU",
        "editorial_content_used": False,
        "attribution_required": True,
        "third_party_exclusions_must_be_reviewed": True,
    }


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def run(config_path: Path, output_path: Path) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        name: list(EXACT_TOKEN_BAND_RANGES[name]) for name in ("32k", "64k", "128k")
    }
    if config["length_buckets"] != expected_bands:
        raise ValueError("P54 length bands differ from repository exact bands")
    if config["release_boundary"] != {
        "data_stage": "aggregate_preflight",
        "candidate_count": 0,
        "train_ready": False,
        "production_eligible": False,
        "inventory_delta": 0,
    }:
        raise ValueError("P54 release boundary is not fail closed")

    fetched: dict[str, bytes] = {}
    receipts = []
    for source in config["sources"]:
        raw, receipt = _fetch(source)
        fetched[source["source_id"]] = raw
        receipts.append(receipt)

    normalization = config["normalization"]
    document_texts: dict[str, str] = {}
    source_units: dict[str, list[dict[str, Any]]] = {}
    for source_id in ("proposal", "position", "act", "corrigendum"):
        document_text, units = _extract_semantic_units(
            fetched[source_id],
            source_id=source_id,
            minimum_characters=normalization["minimum_oracle_block_characters"],
        )
        document_texts[source_id] = document_text
        source_units[source_id] = units

    chain = config["selected_chain"]
    metadata = _metadata_evidence(fetched["metadata"], chain)
    corrigendum = _unit(
        "corrigendum:whole-document",
        "corrigendum",
        document_texts["corrigendum"],
        source_ordinal=0,
    )
    all_content_units = [
        item
        for source_id in ("proposal", "position", "act")
        for item in source_units[source_id]
    ]
    near_units_all, near_removed = _near_deduplicate(
        all_content_units,
        size=normalization["near_duplicate_word_shingle_size"],
        threshold=normalization["near_duplicate_jaccard_threshold"],
    )
    rules = config["oracle_rules"]
    near_units = [
        item
        for item in near_units_all
        if len(item["canonical"])
        >= normalization["minimum_background_block_characters"]
        or any(_matches_rule(item, rule) for rule in rules.values())
    ]
    essential = [
        _find_evidence(near_units, rules["proposal"]),
        _find_evidence(near_units, rules["position"]),
        _find_evidence(near_units, rules["act"]),
        corrigendum,
        metadata,
    ]
    essential_ids = {item["artifact_id"] for item in essential}
    overlap_units = [
        item
        for item in near_units
        if any(_matches_rule(item, rule) for rule in rules.values())
    ]
    overlap_ids = {item["artifact_id"] for item in overlap_units}
    background = [
        item
        for item in near_units
        if item["artifact_id"] not in essential_ids | overlap_ids
    ]

    tokenizer_config = config["tokenizer"]
    resolved_assets = resolved_tokenizer_asset_manifest_sha256(
        tokenizer_config["model_id"], tokenizer_config["revision"]
    )
    if resolved_assets != tokenizer_config["asset_manifest_sha256"]:
        raise ValueError("P54 tokenizer asset manifest changed")
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=True,
        trust_remote_code=False,
    )
    unique_for_tokens = {
        item["artifact_id"]: item for item in [*near_units, corrigendum, metadata]
    }
    _token_lengths(tokenizer, list(unique_for_tokens.values()))

    full_replay = _replay_oracle(
        essential,
        rules,
        expected_correction_count=chain["expected_correction_count"],
        answer=chain["answer"],
    )
    remove_one = {
        item["artifact_id"]: _replay_oracle(
            [candidate for candidate in essential if candidate is not item],
            rules,
            expected_correction_count=chain["expected_correction_count"],
            answer=chain["answer"],
        )["status"]
        for item in essential
    }
    minimal_evidence_pass = full_replay["status"] == "PASS" and all(
        status == "UNKNOWN" for status in remove_one.values()
    )

    packs = {}
    for bucket, bounds in config["length_buckets"].items():
        packed = _pack_exact_band(
            essential,
            background,
            lower=bounds[0],
            upper=bounds[1],
            target_margin=config["packing"]["target_margin_tokens"],
        )
        packed_artifacts = packed.pop("artifacts")
        packed_ids = {item["artifact_id"] for item in packed_artifacts}
        ordered = _order_by_authentic_chain(
            packed_artifacts,
            source_order=config["shortcut_preflight"]["authentic_source_order"],
        )
        shortcut = _artifact_aligned_shortcut_audit(
            ordered,
            rules,
            expected_correction_count=chain["expected_correction_count"],
            windows=config["shortcut_preflight"]["artifact_aligned_windows"],
        )
        packs[bucket] = {
            **packed,
            "artifact_ids_sha256": _sha256("\n".join(sorted(packed_ids)).encode()),
            "essential_artifact_count": len(essential),
            "shortcut_artifact_order": {
                "basis": "predeclared_source_order_then_natural_block_ordinal",
                "source_order": config["shortcut_preflight"]["authentic_source_order"],
            },
            "artifact_aligned_shortcut_preflight": shortcut,
            "raw_token_window_audit": "NOT_RUN_REQUIRED_BEFORE_CANDIDATE_GENERATION",
        }

    rights = _rights_check(fetched["rights"])
    all_exact = all(item["feasible"] for item in packs.values())
    all_artifact_windows = all(
        item["artifact_aligned_shortcut_preflight"]["all_insufficient"]
        for item in packs.values()
    )
    report = {
        "schema_version": "longworld.p54-eurlex-legislative-chain-preflight-report.v1",
        "data_product": config["data_product"],
        "config_sha256": _sha256(config_raw),
        "observed_at": config["authorization"]["reviewed_at"],
        "workflow_comparison": config["workflow_comparison"],
        "selected_world": config["world"],
        "selected_chain": chain,
        "source_receipt": {
            "source_count": len(receipts),
            "total_bytes": sum(item["bytes"] for item in receipts),
            "bundle_sha256": _sha256(
                "".join(
                    f"{item['url']}:{item['sha256']}\n" for item in receipts
                ).encode()
            ),
            "raw_source_persisted": False,
            "sources": receipts,
        },
        "rights_and_privacy": rights,
        "normalization": {
            "semantic_block_count": len(all_content_units),
            "near_deduplicated_block_count_before_length_filter": len(near_units_all),
            "retained_block_count_after_length_filter": len(near_units),
            "length_filtered_block_count": len(near_units_all) - len(near_units),
            "near_duplicate_removed_count": near_removed,
            "near_duplicate_word_shingle_size": normalization[
                "near_duplicate_word_shingle_size"
            ],
            "near_duplicate_jaccard_threshold": normalization[
                "near_duplicate_jaccard_threshold"
            ],
            "whole_semantic_blocks_only": True,
            "split_or_truncated_artifacts": 0,
        },
        "capacity": {
            "near_deduplicated_authentic_source_tokens": sum(
                int(item["token_count"]) for item in near_units
            ),
            "metadata_projection_tokens": metadata["token_count"],
            "corrigendum_whole_document_tokens": corrigendum["token_count"],
            "oracle_overlap_background_units_excluded": len(overlap_units),
            "exact_band_task_pack_preflight": packs,
        },
        "oracle_preflight": {
            "answer": chain["answer"],
            "full_minimal_replay": full_replay,
            "remove_one_status": remove_one,
            "minimal_evidence_pass": minimal_evidence_pass,
            "correction_count_derived_from_source": full_replay["correction_count"],
            "question_only_answer_available": False,
        },
        "candidate_generation_gate": {
            "aggregate_capacity_exact_all_bands": all_exact,
            "artifact_aligned_shortcuts_insufficient": all_artifact_windows,
            "raw_token_shortcuts_audited": False,
            "shared_task_replay_adapter_registered": False,
            "eligible_for_candidate_generation": False,
            "next_step": "implement a registered source-span replay adapter and exhaustive pinned-token raw-window audit before materializing any candidate",
        },
        "verdict": {
            "official_multidocument_topology": "PASS",
            "technical_rights_preflight": rights["technical_rights_preflight"],
            "minimal_evidence_oracle_preflight": (
                "PASS" if minimal_evidence_pass else "FAIL"
            ),
            "near_deduplicated_32k_64k_128k_capacity_preflight": (
                "PASS" if all_exact else "FAIL"
            ),
            "artifact_aligned_shortcut_preflight": (
                "PASS" if all_artifact_windows else "FAIL"
            ),
            "formal_candidate_gates": "NOT_RUN",
            "candidate_count": 0,
            "train_ready": False,
            "inventory_delta": 0,
            "do_not_generate": True,
        },
    }
    raw = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    _write_atomic(output_path, raw)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = run(args.config, args.output)
    print(json.dumps(report["verdict"], sort_keys=True))


if __name__ == "__main__":
    main()
