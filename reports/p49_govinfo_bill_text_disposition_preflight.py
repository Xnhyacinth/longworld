"""Aggregate-only P49 GovInfo bill-text disposition preflight."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from transformers import AutoTokenizer

from longworld.core.record_contract import EXACT_TOKEN_BAND_RANGES
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p49_govinfo_bill_text_disposition_preflight_v1.json")
OUTPUT = Path("reports/p49_govinfo_bill_text_disposition_preflight_v1.json")
ALLOWED_HOSTS = frozenset({"www.govinfo.gov"})
USER_AGENT = "LongWorld-P49-source-preflight/1.0 xnhyacinth@users.noreply.github.com"
SPACE = re.compile(r"\s+")
WORD = re.compile(r"[a-z0-9]+")
NUMBER_PREFIX = re.compile(
    r"^(?:section|sec|division|title|subtitle|chapter|subchapter|part|subpart)\s+"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _local(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _normalized_text(element: ElementTree.Element) -> str:
    return SPACE.sub(" ", " ".join(element.itertext())).strip()


def _content_text(element: ElementTree.Element, excluded_tags: set[str]) -> str:
    parts = [element.text or ""]
    for child in element:
        if _local(child) not in excluded_tags:
            parts.append(_content_text(child, excluded_tags))
        parts.append(child.tail or "")
    return SPACE.sub(" ", " ".join(parts)).strip()


def _canonical(text: str) -> str:
    return " ".join(WORD.findall(text.lower()))


def _fetch(source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    url = source["url"]
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or not parsed.path.startswith(("/bulkdata/BILLSTATUS/", "/content/pkg/"))
    ):
        raise ValueError(f"unapproved P49 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=180, context=ssl.create_default_context()
    ) as response:
        raw = response.read(12_000_001)
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P49 redirect target: {response.geturl()}")
        if response.status != 200 or not raw or len(raw) > 12_000_000:
            raise ValueError(f"invalid P49 response: {url}")
    if len(raw) != source["expected_bytes"]:
        raise ValueError(f"P49 source byte length changed: {url}")
    if _sha256(raw) != source["expected_sha256"]:
        raise ValueError(f"P49 source digest changed: {url}")
    return raw, {
        "url": url,
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "raw_xml_persisted": False,
    }


def _xml(raw: bytes, label: str) -> ElementTree.Element:
    if b"<!ENTITY" in raw.upper():
        raise ValueError(f"entity declaration rejected in {label}")
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise ValueError(f"malformed XML: {label}") from error


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [item for item in element if _local(item) == name]


def _first_text(element: ElementTree.Element, name: str) -> str:
    for item in element.iter():
        if _local(item) == name:
            return _normalized_text(item)
    return ""


def _direct_text(element: ElementTree.Element, name: str) -> str:
    return next(
        (_normalized_text(item) for item in _children(element, name)),
        "",
    )


def _direct_child(
    element: ElementTree.Element, names: set[str]
) -> ElementTree.Element | None:
    return next((item for item in element if _local(item) in names), None)


def _structural_number(element: ElementTree.Element) -> str:
    number = _direct_child(element, {"enum", "num"})
    if number is None:
        return ""
    value = number.attrib.get("value", "") or _normalized_text(number)
    return NUMBER_PREFIX.sub("", _canonical(value))


def _section_units(
    root: ElementTree.Element,
    structural_ancestors: set[str],
    excluded_tags: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    parents = {child: parent for parent in root.iter() for child in parent}
    raw_units = []
    for section in (item for item in root.iter() if _local(item) == "section"):
        path = []
        parent = parents.get(section)
        while parent is not None:
            tag = _local(parent)
            if tag in structural_ancestors:
                path.append(f"{tag}:{_structural_number(parent)}")
            parent = parents.get(parent)
        path.reverse()
        number = _structural_number(section)
        if number:
            leaf = f"section:{number}"
        else:
            parent = parents.get(section)
            siblings = (
                [item for item in parent if _local(item) == "section"]
                if parent is not None
                else [section]
            )
            leaf = f"section-ordinal:{siblings.index(section) + 1}"
        text = _content_text(section, excluded_tags)
        raw_units.append(
            {
                "base_key": "/".join([*path, leaf]),
                "text": text,
                "canonical": _canonical(text),
            }
        )
    key_counts = Counter(item["base_key"] for item in raw_units)
    ambiguous = sorted(key for key, count in key_counts.items() if count > 1)
    return raw_units, ambiguous


def _unambiguous_map(units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts = Counter(item["base_key"] for item in units)
    return {item["base_key"]: item for item in units if counts[item["base_key"]] == 1}


def _shingles(text: str, size: int) -> frozenset[tuple[str, ...]]:
    words = text.split()
    if len(words) < size:
        return frozenset({tuple(words)})
    return frozenset(zip(*(words[offset:] for offset in range(size)), strict=False))


def _jaccard(
    left: frozenset[tuple[str, ...]], right: frozenset[tuple[str, ...]]
) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _dispositions(
    source: list[dict[str, Any]],
    target: list[dict[str, Any]],
    shingle_size: int,
    threshold: float,
) -> dict[str, Any]:
    source_map = _unambiguous_map(source)
    target_map = _unambiguous_map(target)
    common = sorted(source_map.keys() & target_map.keys())
    retained = 0
    similarity_digest = hashlib.sha256()
    for key in common:
        left = _shingles(source_map[key]["canonical"], shingle_size)
        right = _shingles(target_map[key]["canonical"], shingle_size)
        similarity = _jaccard(left, right)
        retained += similarity >= threshold
        similarity_digest.update(f"{key}:{similarity:.12f}\n".encode())
    source_ambiguous = len(source) - len(source_map)
    target_ambiguous = len(target) - len(target_map)
    return {
        "source_section_count": len(source),
        "target_section_count": len(target),
        "retained": retained,
        "modified": len(common) - retained,
        "removed": len(source_map.keys() - target_map.keys()),
        "added": len(target_map.keys() - source_map.keys()),
        "source_ambiguous_units_excluded": source_ambiguous,
        "target_ambiguous_units_excluded": target_ambiguous,
        "matched_similarity_receipt_sha256": similarity_digest.hexdigest(),
    }


def _status_text_versions(bill: ElementTree.Element) -> list[dict[str, Any]]:
    result = []
    for text_versions in _children(bill, "textVersions"):
        for version in _children(text_versions, "item"):
            formats = _children(version, "formats")
            urls = []
            for formats_item in formats:
                for entry in _children(formats_item, "item"):
                    urls.extend(
                        _normalized_text(value) for value in _children(entry, "url")
                    )
            result.append(
                {
                    "type": next(
                        (_normalized_text(item) for item in _children(version, "type")),
                        "",
                    ),
                    "date": next(
                        (_normalized_text(item) for item in _children(version, "date")),
                        "",
                    ),
                    "urls": urls,
                }
            )
    return result


def _validate_status(
    root: ElementTree.Element, chain: dict[str, Any]
) -> dict[str, Any]:
    bills = _children(root, "bill")
    if len(bills) != 1:
        raise ValueError(f"Bill Status bill count mismatch: {chain['bill_id']}")
    bill = bills[0]
    bill_number = chain["bill_id"].rsplit("-", 1)[-1]
    if (
        _direct_text(bill, "congress") != "118"
        or _direct_text(bill, "number") != bill_number
    ):
        raise ValueError(f"Bill Status identity mismatch: {chain['bill_id']}")
    laws = []
    for container in _children(bill, "laws"):
        for item in _children(container, "item"):
            laws.append(
                {
                    _local(child): _normalized_text(child)
                    for child in item
                    if _local(child) in {"type", "number"}
                }
            )
    if {"type": "Public Law", "number": chain["law_number"]} not in laws:
        raise ValueError(f"public-law relation missing: {chain['bill_id']}")

    versions = _status_text_versions(bill)
    for stage in chain["stages"]:
        if stage["stage"] == "law":
            continue
        matches = [item for item in versions if item["type"] == stage["official_type"]]
        if len(matches) != 1 or stage["url"] not in matches[0]["urls"]:
            raise ValueError(
                f"official text-version relation missing: {chain['bill_id']} {stage['stage']}"
            )
        if stage["stage"] != "enr" and not matches[0]["date"].startswith(stage["date"]):
            raise ValueError(
                f"official text-version date mismatch: {chain['bill_id']} {stage['stage']}"
            )

    actions = []
    for container in _children(bill, "actions"):
        for item in _children(container, "item"):
            actions.append(
                {
                    _local(child): _normalized_text(child)
                    for child in item
                    if _local(child) in {"actionDate", "text", "type"}
                }
            )
    edge_receipts = []
    for edge in chain["action_edges"]:
        matches = [
            item
            for item in actions
            if item.get("actionDate") == edge["date"]
            and edge["required_text"] in item.get("text", "")
        ]
        if not matches:
            raise ValueError(
                f"official action edge missing: {chain['bill_id']} {edge['from']}->{edge['to']}"
            )
        edge_receipts.append(
            {
                "from": edge["from"],
                "to": edge["to"],
                "date": edge["date"],
                "matching_action_count": len(matches),
                "action_types": sorted({item.get("type", "") for item in matches}),
            }
        )
    return {
        "text_version_count": len(versions),
        "action_count": len(actions),
        "validated_edges": edge_receipts,
    }


def _validate_stage(
    root: ElementTree.Element, chain: dict[str, Any], stage: dict[str, Any], rights: str
) -> None:
    expected_roots = {
        "eas": "amendment-doc",
        "eah": "amendment-doc",
        "enr": "bill",
        "law": "pLaw",
    }
    if _local(root) != expected_roots[stage["stage"]]:
        raise ValueError(f"unexpected XML root: {chain['bill_id']} {stage['stage']}")
    observed_rights = [
        _normalized_text(item) for item in root.iter() if _local(item) == "rights"
    ]
    if rights not in observed_rights:
        raise ValueError(
            f"public-domain statement missing: {chain['bill_id']} {stage['stage']}"
        )
    if stage["stage"] in {"eas", "eah"} and _first_text(root, "date") != stage["date"]:
        raise ValueError(f"stage date mismatch: {chain['bill_id']} {stage['stage']}")
    if stage["stage"] == "law":
        if _first_text(root, "approvedDate") != stage["date"]:
            raise ValueError(f"law date mismatch: {chain['bill_id']}")
        bill_number = chain["bill_id"].rsplit("-", 1)[-1]
        expected_href = f"/us/bill/118/hr/{bill_number}"
        if not any(item.attrib.get("href") == expected_href for item in root.iter()):
            raise ValueError(f"public law lacks bill backlink: {chain['bill_id']}")


def _near_deduplicate(
    units: list[dict[str, Any]], shingle_size: int, threshold: float
) -> tuple[list[dict[str, Any]], Counter[str]]:
    kept: list[dict[str, Any]] = []
    removed: Counter[str] = Counter()
    for unit in units:
        shingles = _shingles(unit["canonical"], shingle_size)
        duplicate = False
        for prior in kept:
            prior_shingles = prior["shingles"]
            if (
                min(len(shingles), len(prior_shingles))
                / max(len(shingles), len(prior_shingles), 1)
                < threshold
            ):
                continue
            if _jaccard(shingles, prior_shingles) >= threshold:
                duplicate = True
                break
        if duplicate:
            removed[unit["stage_id"]] += 1
            continue
        kept.append({**unit, "shingles": shingles})
    return kept, removed


def _token_lengths(tokenizer: Any, texts: list[str]) -> dict[str, int]:
    result = {}
    for offset in range(0, len(texts), 128):
        batch = texts[offset : offset + 128]
        encoded = tokenizer(
            batch,
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )["input_ids"]
        result.update(zip(batch, map(len, encoded), strict=True))
    return result


def _band_pack(
    units: list[dict[str, Any]], lengths: dict[str, int], lower: int, upper: int
) -> dict[str, Any]:
    ordered = sorted(
        units, key=lambda item: (_sha256(item["text"].encode()), item["text"])
    )
    chosen = []
    total = 0
    for unit in ordered:
        length = lengths[unit["text"]]
        if total + length > upper:
            continue
        chosen.append(unit)
        total += length
        if total >= lower:
            break
    receipt = "".join(f"{_sha256(item['text'].encode())}\n" for item in chosen)
    return {
        "feasible": lower <= total <= upper,
        "qwen_tokens": total,
        "whole_section_count": len(chosen),
        "section_digest_sha256": _sha256(receipt.encode()),
        "padding_tokens": 0,
        "split_or_truncated_sections": 0,
    }


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {
        name: list(EXACT_TOKEN_BAND_RANGES[name]) for name in ("32k", "64k", "128k")
    }
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P49 capacity bands must match repository exact bands")
    oracle = config["section_oracle"]
    threshold = float(oracle["near_duplicate_jaccard_threshold"])
    shingle_size = int(oracle["near_duplicate_word_shingle_size"])
    min_characters = int(oracle["minimum_section_characters"])
    excluded_tags = set(oracle["presentation_only_tags"])

    receipts = []
    chains_report = []
    capacity_units = []
    stage_unit_counts: Counter[str] = Counter()
    for chain in config["chains"]:
        status_raw, status_receipt = _fetch(chain["status_source"])
        receipts.append(status_receipt)
        status_root = _xml(status_raw, f"{chain['bill_id']} Bill Status")
        status_metrics = _validate_status(status_root, chain)
        stages = []
        for stage in chain["stages"]:
            raw, receipt = _fetch(stage)
            receipts.append(receipt)
            root = _xml(raw, f"{chain['bill_id']} {stage['stage']}")
            _validate_stage(root, chain, stage, config["public_domain_text"])
            units, ambiguous = _section_units(
                root,
                set(oracle["structural_ancestors"]),
                excluded_tags,
            )
            stage_id = f"{chain['bill_id']}:{stage['stage']}"
            admissible = [item for item in units if len(item["text"]) >= min_characters]
            for item in admissible:
                capacity_units.append({**item, "stage_id": stage_id})
            stage_unit_counts[stage_id] = len(admissible)
            stages.append(
                {
                    "stage": stage["stage"],
                    "official_type": stage["official_type"],
                    "date": stage["date"],
                    "section_count": len(units),
                    "capacity_section_count": len(admissible),
                    "ambiguous_base_key_count": len(ambiguous),
                    "ambiguous_section_unit_count": sum(
                        Counter(item["base_key"] for item in units)[key]
                        for key in ambiguous
                    ),
                    "source_sha256": receipt["sha256"],
                    "units": units,
                }
            )
        transitions = []
        for source, target in pairwise(stages):
            transitions.append(
                {
                    "from": source["stage"],
                    "to": target["stage"],
                    **_dispositions(
                        source["units"],
                        target["units"],
                        shingle_size,
                        threshold,
                    ),
                }
            )
        for stage in stages:
            stage.pop("units")
        chains_report.append(
            {
                "bill_id": chain["bill_id"],
                "law_number": chain["law_number"],
                "status_source_sha256": status_receipt["sha256"],
                "status_topology": status_metrics,
                "stages": stages,
                "section_transitions": transitions,
            }
        )

    exact_units = {}
    for unit in capacity_units:
        exact_units.setdefault(unit["canonical"], unit)
    near_units, near_removed = _near_deduplicate(
        capacity_units, shingle_size, threshold
    )
    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=tokenizer_config["local_files_only"],
    )
    texts = sorted({item["text"] for item in [*exact_units.values(), *near_units]})
    lengths = _token_lengths(tokenizer, texts)
    exact_tokens = sum(lengths[item["text"]] for item in exact_units.values())
    near_tokens = sum(lengths[item["text"]] for item in near_units)
    packs = {
        band: _band_pack(near_units, lengths, *bounds)
        for band, bounds in config["capacity_bands"].items()
    }
    all_packs_feasible = all(item["feasible"] for item in packs.values())
    source_bundle = "".join(f"{item['url']}:{item['sha256']}\n" for item in receipts)
    report = {
        "schema_version": "longworld.p49-govinfo-bill-text-disposition-preflight-report.v1",
        "data_product": config["data_product"],
        "config_sha256": _sha256(config_raw),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_receipt": {
            "source_count": len(receipts),
            "total_bytes": sum(item["bytes"] for item in receipts),
            "bundle_sha256": _sha256(source_bundle.encode()),
            "raw_xml_persisted": False,
            "sources": receipts,
        },
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "chains": chains_report,
        "capacity": {
            "minimum_section_characters": min_characters,
            "presentation_only_tags_excluded": sorted(excluded_tags),
            "input_section_count": len(capacity_units),
            "input_section_counts_by_stage": dict(sorted(stage_unit_counts.items())),
            "exact_deduplicated_section_count": len(exact_units),
            "exact_deduplicated_qwen_tokens": exact_tokens,
            "near_duplicate_word_shingle_size": shingle_size,
            "near_duplicate_jaccard_threshold": threshold,
            "near_deduplicated_section_count": len(near_units),
            "near_deduplicated_qwen_tokens": near_tokens,
            "near_duplicate_removed_count": len(capacity_units) - len(near_units),
            "near_duplicate_removed_counts_by_stage": dict(
                sorted(near_removed.items())
            ),
            "exact_band_pack_preflight": packs,
            "capacity_is_preflight_only": True,
            "eligible_for_candidate_generation": False,
        },
        "oracle_preflight": {
            "key": "normalized division/title/subtitle/chapter/subchapter/part/subpart numbers plus section number; unnumbered direct siblings use ordinal",
            "ambiguous_key_policy": "exclude every unit sharing a duplicate structural key; never guess an alignment",
            "dispositions": {
                "retained": "same unambiguous key and canonical five-word-shingle Jaccard >= 0.90",
                "modified": "same unambiguous key and canonical five-word-shingle Jaccard < 0.90",
                "removed": "unambiguous source key absent from target",
                "added": "unambiguous target key absent from source",
            },
            "candidate_oracle_implemented": False,
            "formal_near_duplicate_gate_executed": False,
            "formal_shortcut_and_remove_one_gates_executed": False,
        },
        "rights_and_privacy": {
            "selected_texts_with_explicit_public_domain_statement": 8,
            "selected_text_count": 8,
            "third_party_content_assumed_public_domain": False,
            "source_contains_private_records": False,
            "technical_rights_preflight": "PASS",
            "legal_opinion": False,
        },
        "verdict": {
            "official_version_and_action_topology": "PASS",
            "deterministic_section_disposition_preflight": "PASS",
            "near_deduplicated_32k_64k_128k_capacity_preflight": (
                "PASS" if all_packs_feasible else "FAIL"
            ),
            "formal_candidate_gates": "NOT_RUN",
            "train_ready": False,
            "do_not_generate": True,
            "candidate_count": 0,
            "inventory_delta": 0,
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
