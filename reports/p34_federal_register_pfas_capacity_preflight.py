"""Fetch and measure the P34 EPA PFAS correction dossier from official sources."""

from __future__ import annotations

import hashlib
import io
import json
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pypdf import PdfReader
from transformers import AutoTokenizer

from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

CONFIG = Path("configs/p34_federal_register_pfas_correction_preflight_v1.json")
OUTPUT = Path("reports/p34_federal_register_pfas_capacity_preflight_v1.json")
SOURCE_PREFIX = Path("sources/research_p34_federal_register_pfas")
ALLOWED_HOSTS = frozenset({"www.federalregister.gov", "www.govinfo.gov"})
USER_AGENT = "LongWorld-P34-source-preflight/1.0 xnhyacinth@users.noreply.github.com"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fetch(url: str) -> tuple[bytes, dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"unapproved P34 source URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=120, context=context) as response:
        raw = response.read()
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved P34 redirect target: {final_url}")
        return raw, {
            "requested_url": url,
            "final_url": final_url,
            "status": response.status,
            "content_type": response.headers.get_content_type(),
            "content_length_header": response.headers.get("Content-Length"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "observed_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }


def _normalized_page(text: str) -> tuple[str, int, int]:
    text = text.replace("\u00ad", "").replace("\x00", "").replace("\u2013", "-")
    text, email_count = re.subn(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "<EMAIL_REDACTED>",
        text,
    )
    text, phone_count = re.subn(
        r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)",
        "<PHONE_REDACTED>",
        text,
    )
    lines = []
    in_contact_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if upper.startswith("FOR FURTHER INFORMATION CONTACT"):
            in_contact_block = True
            continue
        if in_contact_block and upper.startswith("SUPPLEMENTARY INFORMATION"):
            in_contact_block = False
        if in_contact_block:
            continue
        if re.match(r"^(?:\d+\s+)?Federal Register\s*/\s*Vol\.", line):
            continue
        if line.startswith("VerDate "):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if line:
            lines.append(line)
    return " ".join(lines), email_count, phone_count


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_.:/§()-]+", text.lower())


def _find(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _union(parent: list[int], left: int, right: int) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def _unique_contains(text: str, phrase: str, label: str) -> dict[str, Any]:
    count = text.count(phrase)
    if count != 1:
        raise ValueError(f"{label} expected once, observed {count}")
    return {
        "evidence_id": label,
        "normalized_quote": phrase,
        "quote_sha256": _sha256(phrase.encode()),
    }


def main() -> None:
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    expected_bands = {"64k": [64_000, 65_536], "128k": [128_000, 131_072]}
    if config.get("capacity_bands") != expected_bands:
        raise ValueError("P34 capacity bands must match the repository exact bands")
    tokenizer_config = config["tokenizer"]
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_config["model_id"],
        revision=tokenizer_config["revision"],
        local_files_only=True,
    )

    retrievals = []
    documents = []
    all_pages = []
    normalized_documents: dict[str, str] = {}
    for document in config["documents"]:
        number = document["document_number"]
        metadata_raw, metadata_receipt = _fetch(document["metadata_url"])
        metadata = json.loads(metadata_raw)
        if (
            metadata["document_number"] != number
            or metadata["publication_date"] != document["publication_date"]
            or config["dossier_id"] not in metadata["docket_ids"]
            or config["regulation_id_number"] not in metadata["regulation_id_numbers"]
            or metadata["pdf_url"] != document["official_pdf_url"]
        ):
            raise ValueError(f"Federal Register metadata relation mismatch: {number}")
        metadata_path = Path(f"{SOURCE_PREFIX}_{number}_metadata.json")
        metadata_path.write_bytes(metadata_raw)
        metadata_receipt.update(
            {"role": document["role"], "retrieval_file": str(metadata_path)}
        )
        retrievals.append(metadata_receipt)

        pdf_raw, pdf_receipt = _fetch(document["official_pdf_url"])
        if not pdf_raw.startswith(b"%PDF"):
            raise ValueError(f"GovInfo response is not a PDF: {number}")
        pdf_path = Path(f"{SOURCE_PREFIX}_{number}.pdf")
        pdf_path.write_bytes(pdf_raw)
        pdf_receipt.update({"role": document["role"], "retrieval_file": str(pdf_path)})
        retrievals.append(pdf_receipt)

        reader = PdfReader(io.BytesIO(pdf_raw))
        extracted_pages = [page.extract_text() or "" for page in reader.pages]
        extracted_text = "\f".join(extracted_pages)
        title_match = re.search(
            r"\s+".join(re.escape(part) for part in metadata["title"].split()),
            extracted_text,
        )
        if title_match is None:
            raise ValueError(f"document title is absent from GovInfo PDF: {number}")
        title_index = title_match.start()
        agency_matches = list(
            re.finditer(
                r"ENVIRONMENTAL\s+PROTECTION\s+AGENCY",
                extracted_text[:title_index],
            )
        )
        if not agency_matches:
            raise ValueError(f"agency heading is absent from GovInfo PDF: {number}")
        document_start = agency_matches[-1].start()
        number_pattern = re.escape(number).replace(r"\-", "[-–]")
        end_match = re.search(
            rf"\[FR Doc\. {number_pattern} Filed[^\]]*\]",
            extracted_text[title_index:],
        )
        if end_match is None:
            raise ValueError(
                f"document end marker is absent from GovInfo PDF: {number}"
            )
        document_end = title_index + end_match.end()
        first_pdf_page = extracted_text[:document_start].count("\f") + 1
        selected_pages = extracted_text[document_start:document_end].split("\f")
        document_pages = []
        email_redactions = 0
        phone_redactions = 0
        for page_offset, page_text in enumerate(selected_pages):
            page_index = first_pdf_page + page_offset
            normalized, emails, phones = _normalized_page(page_text)
            email_redactions += emails
            phone_redactions += phones
            if not normalized:
                continue
            artifact = {
                "artifact_id": f"{number}:page:{page_index}",
                "document_number": number,
                "role": document["role"],
                "page_index": page_index,
                "clean_text_sha256": _sha256(normalized.encode()),
                "chars": len(normalized),
                "qwen_exact_tokens": len(
                    tokenizer.encode(normalized, add_special_tokens=False)
                ),
                "_text": normalized,
                "_words": _words(normalized),
            }
            document_pages.append(artifact)
            all_pages.append(artifact)
        normalized_text = "\n".join(item["_text"] for item in document_pages)
        normalized_documents[number] = normalized_text
        normalized_path = Path(f"{SOURCE_PREFIX}_{number}_normalized.txt")
        normalized_path.write_text(normalized_text + "\n", encoding="utf-8")
        documents.append(
            {
                "role": document["role"],
                "document_number": number,
                "publication_date": metadata["publication_date"],
                "type": metadata["type"],
                "action": metadata["action"],
                "docket_ids": metadata["docket_ids"],
                "regulation_id_numbers": metadata["regulation_id_numbers"],
                "effective_on": metadata.get("effective_on"),
                "official_pdf_sha256": pdf_receipt["sha256"],
                "official_pdf_bytes": len(pdf_raw),
                "pdf_pages": len(reader.pages),
                "selected_pdf_pages": len(selected_pages),
                "normalized_pages": len(document_pages),
                "normalized_text_sha256": _sha256(normalized_text.encode()),
                "normalized_qwen_tokens": sum(
                    item["qwen_exact_tokens"] for item in document_pages
                ),
                "email_redactions": email_redactions,
                "telephone_redactions": phone_redactions,
            }
        )

    parent = list(range(len(all_pages)))
    exact_seen: dict[str, int] = {}
    exact_duplicate_pairs = []
    for index, artifact in enumerate(all_pages):
        digest = artifact["clean_text_sha256"]
        if digest in exact_seen:
            _union(parent, index, exact_seen[digest])
            exact_duplicate_pairs.append(
                [all_pages[exact_seen[digest]]["artifact_id"], artifact["artifact_id"]]
            )
        else:
            exact_seen[digest] = index

    near_duplicate_pairs = []
    threshold = config["near_duplicate_threshold"]
    for left_index, left in enumerate(all_pages):
        left_words = left["_words"]
        for right_index in range(left_index + 1, len(all_pages)):
            right = all_pages[right_index]
            right_words = right["_words"]
            if not left_words or not right_words:
                continue
            upper_bound = (
                2
                * min(len(left_words), len(right_words))
                / (len(left_words) + len(right_words))
            )
            if upper_bound < threshold:
                continue
            ratio = SequenceMatcher(
                None, left_words, right_words, autojunk=False
            ).quick_ratio()
            if ratio >= threshold:
                _union(parent, left_index, right_index)
                near_duplicate_pairs.append(
                    {
                        "left": left["artifact_id"],
                        "right": right["artifact_id"],
                        "word_quick_ratio": round(ratio, 6),
                    }
                )

    components: dict[int, list[dict[str, Any]]] = {}
    for index, artifact in enumerate(all_pages):
        components.setdefault(_find(parent, index), []).append(artifact)
    near_dedup_tokens = sum(
        max(component, key=lambda item: item["qwen_exact_tokens"])["qwen_exact_tokens"]
        for component in components.values()
    )
    exact_dedup_tokens = sum(
        artifact["qwen_exact_tokens"]
        for index, artifact in enumerate(all_pages)
        if exact_seen[artifact["clean_text_sha256"]] == index
    )

    final = normalized_documents["2024-07773"]
    correction = normalized_documents["2024-12645"]
    proposal_trigger = "any mixture containing one or more of them"
    final_trigger = (
        "any mixture containing two or more of PFHxS, PFNA, HFPO-DA, and PFBS"
    )
    corrected_reference = (
        "The effective date for § 141.61(c)(2)(i) through (vii) is April 26, 2029."
    )
    obsolete_reference = (
        "The effective date for paragraphs (c)(34) through (40) of § 141.61 "
        "(listed in table 4 to paragraph (c)) is April 26, 2029."
    )
    proposed_first_page = min(
        (
            artifact
            for artifact in all_pages
            if artifact["document_number"] == "2023-05471"
        ),
        key=lambda artifact: artifact["page_index"],
    )
    proposal_evidence = _unique_contains(
        proposed_first_page["_text"],
        proposal_trigger,
        "proposed_mixture_trigger",
    )
    proposal_evidence["source_artifact_id"] = proposed_first_page["artifact_id"]
    decisive_pages = {
        proposed_first_page["artifact_id"]: proposed_first_page,
    }
    for document_number, phrase in (
        ("2024-07773", final_trigger),
        ("2024-07773", obsolete_reference),
        ("2024-12645", corrected_reference),
    ):
        matches = [
            artifact
            for artifact in all_pages
            if artifact["document_number"] == document_number
            and phrase in artifact["_text"]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"decisive page for {document_number} expected once, observed {len(matches)}"
            )
        decisive_pages[matches[0]["artifact_id"]] = matches[0]
    evidence = [
        proposal_evidence,
        _unique_contains(final, final_trigger, "final_mixture_trigger"),
        _unique_contains(final, obsolete_reference, "final_obsolete_designation"),
        _unique_contains(correction, corrected_reference, "corrected_designation"),
    ]
    if correction.count("FR Doc. 2024-07773") != 1:
        raise ValueError("correction does not uniquely identify its target document")

    document_word_sequences = {
        number: _words(text) for number, text in normalized_documents.items()
    }
    document_similarity = []
    numbers = list(document_word_sequences)
    for left_index, left in enumerate(numbers):
        for right in numbers[left_index + 1 :]:
            document_similarity.append(
                {
                    "left": left,
                    "right": right,
                    "word_quick_ratio": round(
                        SequenceMatcher(
                            None,
                            document_word_sequences[left],
                            document_word_sequences[right],
                            autojunk=False,
                        ).quick_ratio(),
                        6,
                    ),
                }
            )

    payload = {
        "schema_version": "longworld.p34-federal-register-capacity-preflight.v1",
        "data_product": config["data_product"],
        "verdict": "SOURCE_CAPACITY_PASS_WORLD_BLOCKED_RAW_WINDOW_UNTESTED",
        "source_capacity_status": "PASS",
        "world_admission_status": "BLOCKED_RAW_WINDOW_UNTESTED",
        "do_not_generate": True,
        "train_ready": False,
        "production_eligible": False,
        "research_question": config["research_question"],
        "constraints": config["constraints"],
        "cutoff": config["cutoff"],
        "tokenizer": {
            **tokenizer_config,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                tokenizer_config["model_id"], tokenizer_config["revision"]
            ),
        },
        "source_receipt": {
            "config": str(CONFIG),
            "config_sha256": _sha256(config_raw),
            "retrievals": retrievals,
        },
        "relation_evidence": {
            "shared_docket_id": config["dossier_id"],
            "shared_regulation_id_number": config["regulation_id_number"],
            "chronology": [document["document_number"] for document in documents],
            "correction_target_document_number": "2024-07773",
            "relation_kinds": [
                "proposed_version_of_rulemaking",
                "finalizes_proposal",
                "corrects_final_rule",
                "supersedes_entry_designation_at_cutoff",
            ],
        },
        "documents": documents,
        "capacity": {
            "raw_page_tokens": sum(
                artifact["qwen_exact_tokens"] for artifact in all_pages
            ),
            "exact_dedup_page_tokens": exact_dedup_tokens,
            "near_dedup_page_tokens_at_0_90": near_dedup_tokens,
            "dedup_unit": "official_pdf_page_after_document_boundary_and_contact_exclusion",
            "same_document_near_duplicate_pairs_included": True,
            "page_artifact_count": len(all_pages),
            "exact_component_count": len(exact_seen),
            "near_dedup_component_count": len(components),
            "exact_duplicate_pair_count": len(exact_duplicate_pairs),
            "near_duplicate_pair_count": len(near_duplicate_pairs),
            "fills_64k_unique": near_dedup_tokens >= expected_bands["64k"][0],
            "fills_128k_unique": near_dedup_tokens >= 128000,
            "gap_over_64k_lower": near_dedup_tokens - expected_bands["64k"][0],
            "gap_over_128k_lower": near_dedup_tokens - 128000,
            "bands": config["capacity_bands"],
            "near_duplicate_pairs": near_duplicate_pairs,
            "document_similarity": document_similarity,
        },
        "oracle_design": {
            "status": "PROPOSED_SHORTCUT_PRONE_NOT_ADMITTED",
            "program_id": "govpolicy.pfas_proposal_final_correction_resolution.v1-proposed",
            "question_state": {
                "proposal_mixture_trigger": "one_or_more",
                "final_mixture_trigger": "two_or_more",
                "controlling_reference_at_cutoff": "40_CFR_141.61(c)(2)(i)-(vii)",
                "effective_date": "2029-04-26",
            },
            "without_correction": {
                "controlling_reference_at_cutoff": "40_CFR_141.61(c)(34)-(40)",
                "status": "obsolete_entry_designation",
            },
            "counterfactual_changes_answer": True,
            "remove_one_contract": {
                "2023-05471": "proposal mixture trigger becomes unknown",
                "2024-07773": "final trigger and correction target become ungrounded",
                "2024-12645": "cutoff-effective entry designation remains obsolete",
            },
            "evidence": evidence,
        },
        "world_admission": {
            "blocked": True,
            "blocker": "current decisive evidence is short-view sufficient",
            "raw_window_audit_executed": False,
            "correction_document_page_sum_tokens": next(
                item["normalized_qwen_tokens"]
                for item in documents
                if item["document_number"] == "2024-12645"
            ),
            "decisive_page_artifact_ids": sorted(decisive_pages),
            "decisive_page_token_upper_sum": sum(
                item["qwen_exact_tokens"] for item in decisive_pages.values()
            ),
            "risk": (
                "the correction document alone contains both obsolete and corrected "
                "references below a 4K page-sum budget; all four decisive pages total "
                "below 8K, so source capacity does not establish a 64K/128K dependency"
            ),
            "padding_prohibited": True,
            "next_minimum_oracle_experiment": [
                "define a multi-output compliance state whose necessary operands occur in naturally separated proposal, final-rule sections/tables, and correction spans",
                "bind every operand and run independent full/remove-one/without-correction replay before candidate materialization",
                "run contiguous 4K/8K/16K plus BM25, lexical, and embedding top-k shortcut audits on only the necessary natural artifacts",
                "reject the dossier if any short view is sufficient; never add unrelated pages to manufacture 64K/128K dependence",
            ],
        },
        "privacy_and_rights": {
            "authority": "official GovInfo PDFs; FederalRegister.gov metadata is not itself the legal edition",
            "reuse": "U.S. Government works are generally public domain under 17 U.S.C. 105; embedded copyrighted material remains excluded",
            "scope_exclusions": [
                "docket comments",
                "confidential business information",
                "incorporated third-party standards",
                "contact blocks and contact identifiers",
            ],
            "formal_legal_review_completed": False,
        },
        "remaining_gates": [
            "implement a receipt-bound GovInfo PDF adapter and post-redaction spans",
            "bind correction operations to exact old and new regulatory spans",
            "implement independent full/counterfactual/remove-one replay",
            "materialize natural chunks and pass unchanged exact-band, near-duplicate, derived-view, raw-window, truncation, and dense gates",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "documents": documents,
                "capacity": {
                    key: value
                    for key, value in payload["capacity"].items()
                    if key not in {"near_duplicate_pairs", "document_similarity"}
                },
                "oracle_design": payload["oracle_design"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
