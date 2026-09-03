"""Reproduce the P20 eLife review-response-revision capacity preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p20_researchlab_elife_94586_review_revision_preflight_v1.json")
OUTPUT = Path("reports/p20_researchlab_elife_94586_review_revision_preflight_v1.json")
RAW_BASE = "https://raw.githubusercontent.com"
BLOCK_TAGS = frozenset(
    {"p", "td", "th", "caption", "disp-formula", "title", "list-item"}
)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def normalize(text: str) -> str:
    return " ".join(text.split())


def visible_text(element: ET.Element) -> str:
    parts: list[str] = []

    def append(node: ET.Element, *, include_tail: bool = True) -> None:
        name = local_name(node)
        if name == "alternatives":
            choice = next(
                (child for child in node if local_name(child) == "tex-math"),
                next(iter(node), None),
            )
            if choice is not None:
                append(choice, include_tail=False)
        elif name in {"graphic", "inline-graphic"}:
            for descendant in node.iter():
                if local_name(descendant) == "alt-text" and descendant.text:
                    parts.append(descendant.text)
        else:
            if node.text:
                parts.append(node.text)
            for child in node:
                append(child)
        if include_tail and node.tail:
            parts.append(node.tail)

    append(element)
    return normalize("".join(parts))


def semantic_blocks(root: ET.Element) -> list[str]:
    blocks: list[str] = []
    for xpath in ("./body", "./back/app-group"):
        container = root.find(xpath)
        if container is None:
            continue
        for element in container.iter():
            name = local_name(element)
            is_leaf_candidate = name in BLOCK_TAGS - {"p"} and not any(
                local_name(descendant) in BLOCK_TAGS
                for descendant in list(element.iter())[1:]
            )
            if name == "p" or is_leaf_candidate:
                text = visible_text(element)
                if text:
                    blocks.append(text)
    return blocks


def direct_response_paragraphs(subarticle: ET.Element) -> list[str]:
    paragraphs: list[str] = []

    def walk(element: ET.Element, inside_quote: bool = False) -> None:
        inside_quote = inside_quote or local_name(element) == "disp-quote"
        if local_name(element) == "p" and not inside_quote:
            text = visible_text(element)
            if text:
                paragraphs.append(text)
        for child in element:
            walk(child, inside_quote)

    body = subarticle.find("./body")
    if body is not None:
        walk(body)
    return paragraphs


def git_blob_sha1(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode()
    return hashlib.sha1(header + raw).hexdigest()


def fetch_versions(
    config: dict[str, Any],
) -> tuple[dict[int, ET.Element], list[dict[str, Any]]]:
    commit = config["source"]["repository_commit"]
    repository = config["source"]["repository"]
    roots: dict[int, ET.Element] = {}
    receipts: list[dict[str, Any]] = []
    for expected in config["versions"]:
        url = f"{RAW_BASE}/{repository}/{commit}/{expected['path']}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": config["user_agent"], "Accept": "application/xml"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(expected["bytes"] + 1)
            status = response.status
            final_url = response.geturl()
            media_type = response.headers.get_content_type()
        observed = {
            "version": expected["version"],
            "requested_url": url,
            "final_url": final_url,
            "http_status": status,
            "media_type": media_type,
            "bytes": len(raw),
            "sha256": sha256(raw),
            "git_blob_sha1": git_blob_sha1(raw),
            "repository_commit": commit,
            "path_commit": expected["path_commit"],
        }
        if status != 200 or final_url != url:
            raise ValueError(
                f"untrusted retrieval for eLife version {expected['version']}"
            )
        for key in ("bytes", "sha256", "git_blob_sha1"):
            if observed[key] != expected[key]:
                raise ValueError(
                    f"eLife version {expected['version']} {key} mismatch: "
                    f"{observed[key]} != {expected[key]}"
                )
        roots[expected["version"]] = ET.fromstring(raw)
        receipts.append(observed)
    return roots, receipts


def singleton_text(root: ET.Element, xpath: str, label: str) -> str:
    elements = root.findall(xpath)
    if len(elements) != 1:
        raise ValueError(f"{label} matched {len(elements)} times")
    return visible_text(elements[0])


def version_identity(
    root: ET.Element, expected: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    article_meta = root.find("./front/article-meta")
    if article_meta is None:
        raise ValueError("missing article-meta")
    ids = {
        (item.attrib.get("pub-id-type"), item.attrib.get("specific-use")): normalize(
            "".join(item.itertext())
        )
        for item in article_meta.findall("./article-id")
    }
    title = singleton_text(
        root, "./front/article-meta/title-group/article-title", "article title"
    )
    date_type = "original-publication" if expected["version"] == 1 else "update"
    dates = article_meta.findall(f"./pub-date[@date-type='{date_type}']")
    if len(dates) != 1:
        raise ValueError(
            f"version {expected['version']} {date_type} date is not unique"
        )
    effective_date = dates[0].attrib.get("iso-8601-date")
    licenses = article_meta.findall("./permissions/license")
    license_urls = {
        item.attrib.get("{http://www.w3.org/1999/xlink}href") for item in licenses
    }
    observed = {
        "publisher_id": ids.get(("publisher-id", None)),
        "canonical_doi": ids.get(("doi", None)),
        "version_doi": ids.get(("doi", "version")),
        "title": title,
        "effective_date": effective_date,
        "license_urls": sorted(url for url in license_urls if url),
    }
    expected_identity = {
        "publisher_id": candidate["publisher_id"],
        "canonical_doi": candidate["canonical_doi"],
        "version_doi": expected["version_doi"],
        "title": candidate["title"],
        "effective_date": expected["effective_date"],
    }
    for key, value in expected_identity.items():
        if observed[key] != value:
            raise ValueError(
                f"eLife version {expected['version']} {key} mismatch: "
                f"{observed[key]!r} != {value!r}"
            )
    return observed


def subarticle_receipts(root: ET.Element, version: int) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for subarticle in root.findall("./sub-article"):
        dois = [
            normalize("".join(item.itertext()))
            for item in subarticle.findall("./front-stub/article-id")
            if item.attrib.get("pub-id-type") == "doi"
        ]
        if len(dois) != 1:
            raise ValueError(
                f"eLife v{version} subarticle {subarticle.attrib.get('id')} DOI count"
            )
        text = visible_text(subarticle)
        receipts.append(
            {
                "xml_id": subarticle.attrib.get("id"),
                "article_type": subarticle.attrib.get("article-type"),
                "doi": dois[0],
                "visible_chars": len(text),
                "visible_text_sha256": sha256(text.encode()),
                "paragraphs": len(subarticle.findall("./body//p")),
            }
        )
    return receipts


def add_artifacts(
    target: list[dict[str, Any]],
    *,
    kind: str,
    source: str,
    texts: list[str],
    minimum_chars: int,
) -> None:
    for index, text in enumerate(texts, start=1):
        if len(text) < minimum_chars:
            continue
        target.append(
            {
                "artifact_id": f"artifact:{len(target) + 1:04d}:{source}:{kind}:{index:04d}",
                "kind": kind,
                "source": source,
                "text": text,
            }
        )


def capacity_artifacts(
    roots: dict[int, ET.Element], minimum_chars: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, list[str]]]:
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

    comparisons: list[dict[str, Any]] = []
    for before, after in ((1, 2), (2, 3)):
        matcher = SequenceMatcher(None, blocks[before], blocks[after], autojunk=False)
        changed_before = 0
        changed_after = 0
        opcode_counts: Counter[str] = Counter()
        for (
            opcode,
            start_before,
            end_before,
            start_after,
            end_after,
        ) in matcher.get_opcodes():
            opcode_counts[opcode] += 1
            if opcode == "equal":
                continue
            before_texts = blocks[before][start_before:end_before]
            after_texts = blocks[after][start_after:end_after]
            changed_before += len(before_texts)
            changed_after += len(after_texts)
            add_artifacts(
                artifacts,
                kind="delta_before",
                source=f"v{before}->v{after}:before",
                texts=before_texts,
                minimum_chars=minimum_chars,
            )
            add_artifacts(
                artifacts,
                kind="delta_after",
                source=f"v{before}->v{after}:after",
                texts=after_texts,
                minimum_chars=minimum_chars,
            )
        comparisons.append(
            {
                "before": before,
                "after": after,
                "sequence_ratio": round(matcher.ratio(), 8),
                "opcode_counts": dict(sorted(opcode_counts.items())),
                "changed_blocks_before": changed_before,
                "changed_blocks_after": changed_after,
            }
        )
    return artifacts, comparisons, blocks


def exact_deduplicate(
    artifacts: list[dict[str, Any]], tokenizer: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicate_count = 0
    for artifact in artifacts:
        text = artifact["text"]
        digest = sha256(text.encode())
        if digest in seen:
            duplicate_count += 1
            continue
        tokens = len(tokenizer.encode(text, add_special_tokens=False))
        seen[digest] = artifact["artifact_id"]
        unique.append(
            {
                **artifact,
                "sha256": digest,
                "chars": len(text),
                "qwen_tokens": tokens,
            }
        )
    by_kind: dict[str, dict[str, int]] = defaultdict(
        lambda: {"blocks": 0, "chars": 0, "qwen_tokens": 0}
    )
    for artifact in unique:
        values = by_kind[artifact["kind"]]
        values["blocks"] += 1
        values["chars"] += artifact["chars"]
        values["qwen_tokens"] += artifact["qwen_tokens"]
    ledger = [
        {
            key: artifact[key]
            for key in (
                "artifact_id",
                "kind",
                "source",
                "sha256",
                "chars",
                "qwen_tokens",
            )
        }
        for artifact in unique
    ]
    return unique, {
        "input_blocks": len(artifacts),
        "duplicate_blocks_removed": duplicate_count,
        "unique_blocks": len(unique),
        "unique_chars": sum(item["chars"] for item in unique),
        "unique_qwen_tokens": sum(item["qwen_tokens"] for item in unique),
        "by_kind": dict(sorted(by_kind.items())),
        "artifact_ledger_sha256": sha256(canonical(ledger)),
    }


def near_deduplicate(
    artifacts: list[dict[str, Any]], threshold: float
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parents = list(range(len(artifacts)))
    counters = [Counter(item["text"]) for item in artifacts]

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(artifacts):
        left_text = left["text"]
        for right_index in range(left_index + 1, len(artifacts)):
            right = artifacts[right_index]
            right_text = right["text"]
            maximum = (
                2
                * min(len(left_text), len(right_text))
                / (len(left_text) + len(right_text))
            )
            if maximum < threshold:
                continue
            common = sum((counters[left_index] & counters[right_index]).values())
            counter_ratio = 2 * common / (len(left_text) + len(right_text))
            if counter_ratio < threshold:
                continue
            ratio = SequenceMatcher(
                None, left_text, right_text, autojunk=False
            ).quick_ratio()
            if ratio >= threshold:
                union(left_index, right_index)
                pairs.append(
                    {
                        "left": left["artifact_id"],
                        "right": right["artifact_id"],
                        "quick_ratio": round(ratio, 8),
                    }
                )

    components: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, artifact in enumerate(artifacts):
        components[find(index)].append(artifact)
    representatives = [
        max(component, key=lambda item: (item["qwen_tokens"], item["artifact_id"]))
        for _, component in sorted(components.items())
    ]
    pair_ledger = sorted(pairs, key=lambda item: (item["left"], item["right"]))
    component_ledger = [
        {
            "members": sorted(item["artifact_id"] for item in component),
            "representative": max(
                component,
                key=lambda item: (item["qwen_tokens"], item["artifact_id"]),
            )["artifact_id"],
        }
        for _, component in sorted(components.items())
    ]
    return representatives, {
        "threshold": threshold,
        "pairs": len(pairs),
        "pair_ledger_sha256": sha256(canonical(pair_ledger)),
        "components": len(components),
        "multi_member_components": sum(
            1 for component in components.values() if len(component) > 1
        ),
        "component_ledger_sha256": sha256(canonical(component_ledger)),
        "representative_qwen_tokens": sum(
            item["qwen_tokens"] for item in representatives
        ),
    }


def subset_witness(artifacts: list[dict[str, Any]], band: list[int]) -> dict[str, Any]:
    lower, upper = band
    total = sum(item["qwen_tokens"] for item in artifacts)
    bits = 1
    history: list[int] = []
    for artifact in artifacts:
        history.append(bits)
        bits |= bits << artifact["qwen_tokens"]
    reachable = next(
        (
            tokens
            for tokens in range(lower, min(upper, total) + 1)
            if (bits >> tokens) & 1
        ),
        None,
    )
    chosen: list[str] = []
    if reachable is not None:
        remaining = reachable
        for index in range(len(artifacts) - 1, -1, -1):
            if (history[index] >> remaining) & 1:
                continue
            artifact = artifacts[index]
            chosen.append(artifact["artifact_id"])
            remaining -= artifact["qwen_tokens"]
        if remaining != 0:
            raise AssertionError("subset witness reconstruction failed")
        chosen.reverse()
    return {
        "band": band,
        "feasible": reachable is not None,
        "reachable_qwen_tokens": reachable,
        "witness_artifact_ids": chosen,
        "capacity_gap_from_lower": total - lower,
    }


def oracle_receipt(
    roots: dict[int, ET.Element], config: dict[str, Any]
) -> dict[str, Any]:
    witness = config["oracle_witness"]
    review_matches: list[dict[str, Any]] = []
    response_matches: list[dict[str, Any]] = []
    for version, root in roots.items():
        for subarticle in root.findall("./sub-article"):
            article_type = subarticle.attrib.get("article-type")
            for paragraph in subarticle.findall("./body//p"):
                text = visible_text(paragraph)
                digest = sha256(text.encode())
                item = {
                    "version": version,
                    "subarticle_id": subarticle.attrib.get("id"),
                    "article_type": article_type,
                    "paragraph_sha256": digest,
                    "chars": len(text),
                }
                if digest == witness["review_paragraph_sha256"]:
                    review_matches.append(item)
                if digest == witness["response_paragraph_sha256"]:
                    response_matches.append(item)
    controlling_reviews = [
        item
        for item in review_matches
        if item["version"] == 1 and item["article_type"] == "referee-report"
    ]
    direct_responses = [
        item
        for item in response_matches
        if item["version"] == 2 and item["article_type"] == "author-comment"
    ]
    if len(controlling_reviews) != 1 or len(direct_responses) != 1:
        raise ValueError("review-to-response witness is not unique")

    transitions = {
        "body_figure_fig5": [
            len(roots[1].findall("./body//fig[@id='fig5']")),
            len(roots[2].findall("./body//fig[@id='fig5']")),
        ],
        "appendix_APP9": [
            len(roots[1].findall("./back/app-group/app[@id='APP9']")),
            len(roots[2].findall("./back/app-group/app[@id='APP9']")),
        ],
        "appendix_table_tbl3": [
            len(roots[1].findall("./back/app-group//table-wrap[@id='tbl3']")),
            len(roots[2].findall("./back/app-group//table-wrap[@id='tbl3']")),
        ],
    }
    if transitions != witness["required_v1_to_v2_object_transitions"]:
        raise ValueError(f"revision-object transition mismatch: {transitions}")
    return {
        "program_id": config["candidate"]["transition_program"],
        "controlling_review": controlling_reviews[0],
        "review_quote_embedded_in_v2_author_comment": any(
            item["version"] == 2 and item["article_type"] == "author-comment"
            for item in review_matches
        ),
        "direct_author_response": direct_responses[0],
        "v1_to_v2_object_transitions": transitions,
        "full_disposition": "VERIFIED_IMPLEMENTED",
        "without_controlling_review": "UNKNOWN_NO_REVIEW_CLAIM",
        "without_required_revision_delta": "CLAIMED_NOT_VERIFIED",
        "remove_review_changes_disposition": True,
        "remove_delta_changes_disposition": True,
        "model_written_gold": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()

    config_raw = args.config.read_bytes()
    config = json.loads(config_raw)
    roots, source_receipts = fetch_versions(config)
    versions = [
        {
            **version_identity(root, expected, config["candidate"]),
            "version": expected["version"],
            "subarticles": subarticle_receipts(root, expected["version"]),
        }
        for expected in config["versions"]
        for root in [roots[expected["version"]]]
    ]
    expected_license = config["source"]["license_url"]
    if any(item["license_urls"] != [expected_license] for item in versions):
        raise ValueError("eLife license URL is missing or ambiguous")

    policy = config["capacity_policy"]
    tokenizer_config = policy["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=tokenizer_config["local_files_only"],
    )
    artifacts, revision_comparisons, blocks = capacity_artifacts(
        roots, policy["minimum_block_chars"]
    )
    exact, exact_summary = exact_deduplicate(artifacts, tokenizer)
    representatives, near_summary = near_deduplicate(
        exact, policy["near_duplicate_threshold"]
    )
    bands = {
        "exact_64k": subset_witness(representatives, policy["exact_64k_band"]),
        "exact_128k": subset_witness(representatives, policy["exact_128k_band"]),
    }
    oracle = oracle_receipt(roots, config)
    relation_ok = (
        sum(
            item["article_type"] == "referee-report"
            for item in versions[0]["subarticles"]
        )
        >= 2
        and sum(
            item["article_type"] == "author-comment"
            for item in versions[1]["subarticles"]
        )
        == 1
        and oracle["full_disposition"] == "VERIFIED_IMPLEMENTED"
    )
    if not relation_ok:
        raise ValueError("review-response-revision relation gate failed")

    payload = {
        "schema_version": "longworld.elife-review-revision-capacity-preflight.v1",
        "data_product": config["data_product"],
        "verdict": "BLOCKED_128K_CAPACITY",
        "do_not_generate": True,
        "hybrid_train_ready": False,
        "production_eligible": False,
        "candidate": config["candidate"],
        "constraints": [
            "official eLife repository bytes at one pinned commit only",
            "review/response text and adjacent changed manuscript blocks only",
            "unchanged paper body, bibliography, metadata, nested quotes, and derived views excluded",
            "pinned offline Qwen tokenizer and 0.90 near-duplicate collapse",
            "both exact 64K and 128K bands required before generation",
        ],
        "source_receipt": {
            "config": str(args.config),
            "config_sha256": sha256(config_raw),
            "publisher_process_url": config["source"]["publisher_process_url"],
            "repository_url": config["source"]["repository_url"],
            "retrievals": source_receipts,
        },
        "version_records": versions,
        "topology_gate": {
            "passed": relation_ok,
            "v1_referee_reports": sum(
                item["article_type"] == "referee-report"
                for item in versions[0]["subarticles"]
            ),
            "v2_author_comments": sum(
                item["article_type"] == "author-comment"
                for item in versions[1]["subarticles"]
            ),
            "immutable_version_dois": [item["version_doi"] for item in versions],
        },
        "oracle": oracle,
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "capacity_method": {
            "minimum_block_chars": policy["minimum_block_chars"],
            "near_duplicate_threshold": policy["near_duplicate_threshold"],
            "semantic_block_tags": sorted(BLOCK_TAGS),
            "math_rendering": "tex-math preferred inside alternatives",
            "graphics": "excluded except source alt-text",
            "revision_delta": "SequenceMatcher exact-block opcodes over adjacent body plus appendix versions",
            "exact_dedup": "normalized UTF-8 text SHA-256",
            "near_dedup": "connected components at SequenceMatcher.quick_ratio >= 0.90; retain maximum-token member",
        },
        "article_block_inventory": {
            f"v{version}": {
                "blocks": len(items),
                "chars": sum(len(item) for item in items),
            }
            for version, items in blocks.items()
        },
        "revision_comparisons": revision_comparisons,
        "capacity": {
            "exact_dedup": exact_summary,
            "near_dedup": near_summary,
            "bands_after_near_dedup": bands,
            "64k_feasible": bands["exact_64k"]["feasible"],
            "128k_feasible": bands["exact_128k"]["feasible"],
        },
        "generation": {
            "generated": False,
            "candidate_rows": 0,
            "reason": "128K source-capacity gate failed before generation",
        },
    }
    if bands["exact_128k"]["feasible"]:
        raise ValueError("fail-closed verdict is stale because 128K became feasible")
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "exact_dedup_qwen_tokens": exact_summary["unique_qwen_tokens"],
                "near_dedup_qwen_tokens": near_summary["representative_qwen_tokens"],
                "64k_feasible": bands["exact_64k"]["feasible"],
                "64k_witness_qwen_tokens": bands["exact_64k"]["reachable_qwen_tokens"],
                "128k_feasible": bands["exact_128k"]["feasible"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
