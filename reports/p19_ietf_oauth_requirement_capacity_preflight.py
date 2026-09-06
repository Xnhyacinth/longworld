"""Reproduce the P19 OAuth cross-specification capacity/oracle preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

from longworld.core.provenance import ProvenanceError
from longworld.core.standardsworkflow import (
    _clean,
    build_ietf_workflow_from_fetch_inventory,
)
from longworld.core.tokenizer_assets import (
    resolved_tokenizer_asset_manifest_sha256,
)

MODEL_ID = "Qwen/Qwen3.5-4B"
MODEL_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
NEAR_DUP_THRESHOLD = 0.90
CONFIG = Path("configs/p19_ietf_oauth_security_requirement_preflight_v1.json")
OUTPUT = Path("reports/p19_ietf_oauth_security_requirement_capacity_preflight_v1.json")
RFC_NUMBERS = (6749, 6750, 6819, 7636, 8414, 9207, 9700)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def unique_match(text: str, pattern: str, label: str) -> dict[str, Any]:
    matches = list(re.finditer(pattern, text, re.MULTILINE | re.DOTALL))
    if len(matches) != 1:
        raise ValueError(f"{label} matched {len(matches)} times")
    match = matches[0]
    quote = match.group(0)
    return {
        "evidence_id": label,
        "char_start": match.start(),
        "char_end": match.end(),
        "quote_sha256": sha256(quote.encode()),
        "quote": quote,
    }


def load_sources(source_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    inventory_path = source_dir / "ietf_fetch_inventory.json"
    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw)
    config_raw = CONFIG.read_bytes()
    request_raw = (source_dir / str(inventory["request_file"])).read_bytes()
    if request_raw != config_raw or inventory["request_sha256"] != sha256(config_raw):
        raise ValueError("fetched request is not byte-bound to the P19 config")

    texts: dict[str, str] = {}
    by_file = {
        item["retrieval_file"]: item
        for item in inventory["fetch_receipt"]["retrievals"]
    }
    for filename, receipt in by_file.items():
        raw = (source_dir / filename).read_bytes()
        if (
            sha256(raw) != receipt["sha256"]
            or receipt["status"] != 200
            or receipt["requested_url"] != receipt["final_url"]
            or receipt["redirect_chain"] != []
        ):
            raise ValueError(f"retrieval receipt mismatch: {filename}")
        if filename.endswith(".txt"):
            _raw_text, clean, _emails, _private_keys, _digests = _clean(
                raw, approved_private_key_digests=frozenset()
            )
            texts[filename] = clean
    return inventory, texts


def relation_receipts(source_dir: Path, texts: dict[str, str]) -> list[dict[str, Any]]:
    relation_file = "datatracker-draft-ietf-oauth-security-topics-relations.json"
    relation_raw = (source_dir / relation_file).read_bytes()
    related = json.loads(relation_raw)
    selected_targets = {
        "rfc6749": "normative_reference",
        "rfc6750": "normative_reference",
        "rfc6819": "normative_reference",
        "rfc7636": "informative_executable_dependency",
        "rfc8414": "normative_reference",
        "rfc9207": "informative_executable_dependency",
        "rfc9700": "published_as",
    }
    relationship_names = {
        "normative_reference": "refnorm",
        "informative_executable_dependency": "refinfo",
        "published_as": "became_rfc",
    }
    receipts = []
    for target, kind in selected_targets.items():
        expected_relation = (
            f"/api/v1/name/docrelationshipname/{relationship_names[kind]}/"
        )
        expected_target = f"/api/v1/doc/document/{target}/"
        matches = [
            item
            for item in related["objects"]
            if item.get("relationship") == expected_relation
            and item.get("source")
            == "/api/v1/doc/document/draft-ietf-oauth-security-topics/"
            and item.get("target") == expected_target
        ]
        if len(matches) != 1:
            raise ValueError(f"Datatracker relation is not unique: {target}")
        canonical = json.dumps(
            matches[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        receipts.append(
            {
                "kind": kind,
                "source": "draft-ietf-oauth-security-topics",
                "target": target,
                "datatracker_object_sha256": sha256(canonical),
                "retrieval_file": relation_file,
                "retrieval_sha256": sha256(relation_raw),
            }
        )

    update = unique_match(
        texts["rfc9700.txt"],
        r"^Updates: 6749, 6750, 6819[^\n]*$",
        "rfc9700_updates_header",
    )
    update.update(
        {
            "kind": "updates",
            "source": "rfc9700",
            "targets": ["rfc6749", "rfc6750", "rfc6819"],
            "source_sha256": sha256((source_dir / "rfc9700.txt").read_bytes()),
        }
    )
    receipts.append(update)
    return receipts


def oracle_evidence(texts: dict[str, str]) -> list[dict[str, Any]]:
    specifications = [
        (
            "rfc9700.txt",
            "redirect_current",
            (
                r"When comparing client redirection URIs against pre-registered URIs,\s+"
                r"authorization servers MUST utilize exact string matching except for\s+"
                r"port numbers in localhost redirection URIs of native apps \(see\s+"
                r"Section 4\.1\.3\)\."
            ),
        ),
        (
            "rfc6749.txt",
            "redirect_baseline",
            (
                r"When a redirection URI is included in an authorization request, the\s+"
                r"authorization server MUST compare and match.*?using simple string "
                r"comparison as defined in \[RFC3986\] Section 6\.2\.1\."
            ),
        ),
        (
            "rfc9700.txt",
            "bearer_query_current",
            (
                r"Clients MUST NOT pass access tokens in a URI query parameter in\s+"
                r"the way described in Section 2\.3 of \[RFC6750\]\."
            ),
        ),
        (
            "rfc6750.txt",
            "bearer_query_baseline",
            (
                r"Because of the security weaknesses associated with the URI method.*?"
                r"Resource servers MAY support this method\."
            ),
        ),
        (
            "rfc9700.txt",
            "public_refresh_current",
            (
                r"Refresh tokens for public clients MUST be sender-constrained or use\s+"
                r"refresh token rotation as described in Section 4\.14\."
            ),
        ),
        (
            "rfc6819.txt",
            "public_refresh_baseline",
            (
                r"Refresh token rotation is intended to automatically detect and\s+"
                r"prevent attempts to use the same refresh token.*?both revoked\."
            ),
        ),
        (
            "rfc9700.txt",
            "public_pkce_current",
            (
                r"Public clients MUST use PKCE \[RFC7636\] to this end, as motivated\s+"
                r"in Section 4\.5\.3\.1\."
            ),
        ),
        (
            "rfc7636.txt",
            "pkce_s256_dependency",
            (
                r"If the client is capable of using \"S256\", it MUST use \"S256\", as\s+"
                r"\"S256\" is Mandatory To Implement \(MTI\) on the server\."
            ),
        ),
        (
            "rfc9700.txt",
            "metadata_current",
            (
                r"It is therefore RECOMMENDED that authorization servers publish OAuth\s+"
                r"Authorization Server Metadata according to \[RFC8414\] and that clients\s+"
                r"make use of this Authorization Server Metadata \(when available\) to\s+"
                r"configure themselves\."
            ),
        ),
        (
            "rfc8414.txt",
            "metadata_issuer_dependency",
            (
                r"the client MUST ensure that the\s+issuer identifier URL it is using as "
                r"the prefix for the metadata\s+request exactly matches the value of the "
                r"\"issuer\" metadata value in\s+the authorization server metadata document "
                r"received by the client\."
            ),
        ),
        (
            "rfc9700.txt",
            "multi_as_current",
            (
                r"When an OAuth client can interact with more than one authorization\s+"
                r"server, a defense against mix-up attacks \(see Section 4\.4\) is\s+"
                r"REQUIRED\."
            ),
        ),
        (
            "rfc9207.txt",
            "issuer_validation_dependency",
            (
                r"If the value does not match the expected\s+issuer identifier, clients "
                r"MUST reject the authorization response and\s+MUST NOT proceed with the "
                r"authorization grant\."
            ),
        ),
    ]
    evidence = []
    for filename, label, pattern in specifications:
        item = unique_match(texts[filename], pattern, label)
        item["record_id"] = filename.removesuffix(".txt")
        evidence.append(item)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()

    inventory, texts = load_sources(args.source_dir)
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, local_files_only=True
    )
    retrievals = inventory["fetch_receipt"]["retrievals"]
    retrieval_by_file = {item["retrieval_file"]: item for item in retrievals}

    artifacts = []
    comparison_words: dict[str, list[str]] = {}
    for filename in [
        "draft-ietf-oauth-security-topics-29.txt",
        *(f"rfc{number}.txt" for number in RFC_NUMBERS),
    ]:
        text = texts[filename]
        receipt = retrieval_by_file[filename]
        comparison_words[filename] = re.findall(r"[A-Za-z0-9_.:/-]+", text.lower())
        artifacts.append(
            {
                "record_id": filename.removesuffix(".txt"),
                "retrieval_url": receipt["requested_url"],
                "retrieval_observed_at": receipt["observed_at"],
                "source_sha256": receipt["sha256"],
                "clean_text_sha256": sha256(text.encode()),
                "chars": len(text),
                "qwen_exact_tokens": len(
                    tokenizer.encode(text, add_special_tokens=False)
                ),
                "count_for_unique_capacity": filename.startswith("rfc"),
                "exclusion_reason": (
                    "published_as identity cluster with RFC 9700; count published RFC once"
                    if filename.startswith("draft-")
                    else None
                ),
            }
        )

    pairwise = []
    near_duplicate_pairs = []
    published = [item for item in artifacts if item["count_for_unique_capacity"]]
    for left_index, left in enumerate(artifacts):
        for right in artifacts[left_index + 1 :]:
            left_words = comparison_words[f"{left['record_id']}.txt"]
            right_words = comparison_words[f"{right['record_id']}.txt"]
            ratio = SequenceMatcher(None, left_words, right_words).quick_ratio()
            intersection = len(set(left_words) & set(right_words))
            union = len(set(left_words) | set(right_words))
            item = {
                "left": left["record_id"],
                "right": right["record_id"],
                "word_quick_ratio": round(ratio, 6),
                "word_jaccard": round(intersection / union, 6),
            }
            pairwise.append(item)
            if (
                ratio >= NEAR_DUP_THRESHOLD
                and left["count_for_unique_capacity"]
                and right["count_for_unique_capacity"]
            ):
                near_duplicate_pairs.append(item)

    unique_tokens = sum(item["qwen_exact_tokens"] for item in published)
    completed = datetime.fromisoformat(
        inventory["fetch_receipt"]["completed_at"].replace("Z", "+00:00")
    )
    adapter_error = None
    try:
        build_ietf_workflow_from_fetch_inventory(
            inventory,
            args.source_dir,
            generated_at=(completed + timedelta(seconds=1))
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            fetch_inventory_sha256=sha256(
                (args.source_dir / "ietf_fetch_inventory.json").read_bytes()
            ),
        )
    except ProvenanceError as error:
        adapter_error = f"{type(error).__name__}: {error}"

    identity_match_counts = {}
    for number in RFC_NUMBERS:
        text = texts[f"rfc{number}.txt"]
        identity_pattern = re.compile(
            rf"(?im)^\s*(?:RFC[ \t]+{number}[ \t]*|"
            rf"Request for Comments:[ \t]*{number}(?:[ \t]+.*)?)$"
        )
        identity_match_counts[str(number)] = len(identity_pattern.findall(text))

    payload = {
        "schema_version": "longworld.ietf-cross-spec-capacity-preflight.v1",
        "data_product": "p19_ietf_oauth_security_requirement_preflight_v1",
        "verdict": "BLOCKED_ADAPTER_ORACLE_IMPLEMENTATION",
        "do_not_generate": True,
        "hybrid_train_ready": False,
        "production_eligible": False,
        "research_question": (
            "Can official OAuth RFC update and dependency records support a genuinely "
            "cross-specification 64K/128K deterministic requirement-resolution world?"
        ),
        "constraints": [
            "official IETF Datatracker, IETF archive, and RFC Editor bytes only",
            "draft 29 and RFC 9700 are one published_as identity cluster",
            "no unrelated RFC padding or duplicate renderings",
            "pinned offline Qwen tokenizer; existing exact-band and near-dup gates unchanged",
            "no candidate generation before source, relation, oracle, and capacity admission",
        ],
        "tokenizer": {
            "model_id": MODEL_ID,
            "revision": MODEL_REVISION,
            "asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
                MODEL_ID, MODEL_REVISION
            ),
            "local_files_only": True,
            "add_special_tokens": False,
        },
        "exact_64k_band": [65536, 67584],
        "exact_128k_band": [128000, 131072],
        "capacity": {
            "naive_including_published_draft_tokens": sum(
                item["qwen_exact_tokens"] for item in artifacts
            ),
            "published_identity_dedup_tokens": unique_tokens,
            "near_dedup_tokens_at_0_90": unique_tokens,
            "gap_over_64k_lower": unique_tokens - 65536,
            "gap_over_128k_lower": unique_tokens - 128000,
            "fills_64k_unique": unique_tokens >= 65536,
            "fills_128k_unique": unique_tokens >= 128000,
            "near_duplicate_published_pairs": near_duplicate_pairs,
            "published_as_pair": next(
                item
                for item in pairwise
                if item["left"] == "draft-ietf-oauth-security-topics-29"
                and item["right"] == "rfc9700"
            ),
        },
        "source_receipt": {
            "config": str(CONFIG),
            "config_sha256": sha256(CONFIG.read_bytes()),
            "inventory_sha256": sha256(
                (args.source_dir / "ietf_fetch_inventory.json").read_bytes()
            ),
            "policy_url": inventory["fetch_receipt"]["policy_url"],
            "started_at": inventory["fetch_receipt"]["started_at"],
            "completed_at": inventory["fetch_receipt"]["completed_at"],
            "retrievals": retrievals,
        },
        "relations": relation_receipts(args.source_dir, texts),
        "oracle": {
            "program_id": "ietf.oauth_effective_requirement_resolution.v1-proposed",
            "cutoff": "2025-01-31T00:00:00Z",
            "scenario": {
                "client_type": "public",
                "grant": "authorization_code",
                "multiple_authorization_servers": True,
                "redirect_match": "component_or_prefix",
                "bearer_transport": "uri_query",
                "refresh_protection": "rotation",
                "pkce_method": "S256",
                "metadata_used": True,
                "metadata_issuer_matches_request_prefix": True,
                "authorization_response_issuer_matches_expected": True,
            },
            "effective_answer": [
                "redirect_match=FAIL_EXACT_REQUIRED",
                "bearer_transport=FAIL_URI_QUERY_PROHIBITED",
                "refresh_protection=PASS_ROTATION",
                "pkce=PASS_S256",
                "metadata_issuer=PASS_EXACT_MATCH",
                "multi_as_issuer=PASS_MATCHED",
            ],
            "without_rfc9700": [
                "redirect_match=BASELINE_COMPONENT_MATCH_ALLOWED",
                "bearer_transport=BASELINE_DISCOURAGED_WITH_EXCEPTION",
                "refresh_protection=ROTATION_DESCRIBED_NOT_UNIVERSALLY_REQUIRED",
                "pkce=NO_UNIVERSAL_OAUTH_REQUIREMENT_FROM_REMAINING_GRAPH",
                "metadata_issuer=PASS_IF_RFC8414_CHOSEN",
                "multi_as_issuer=PASS_IF_RFC9207_CHOSEN",
            ],
            "counterfactual_changes_answer": True,
            "remove_one_contract": {
                "rfc9700": "effective update requirements revert to baseline modalities",
                "rfc6749": "redirect baseline and OAuth term resolution become unknown",
                "rfc6750": "URI-query baseline comparison becomes unknown",
                "rfc6819": "refresh-rotation historical change becomes unknown",
                "rfc7636": "S256 PKCE execution check becomes unknown",
                "rfc8414": "metadata issuer equality check becomes unknown",
                "rfc9207": "authorization-response issuer check becomes unknown",
                "published_as": "cutoff/version lineage becomes ungrounded",
            },
            "evidence": oracle_evidence(texts),
        },
        "adapter_gate": {
            "current_build_result": adapter_error,
            "rfc_identity_match_counts": identity_match_counts,
            "root_cause": (
                "RFC 6750 contains four standalone page-header identity lines in addition "
                "to its title header, while the current parser requires exactly one match"
            ),
            "subsequent_blockers": [
                "target closure treats only published_as and Updates/Obsoletes targets as grounded; RFC 7636, 8414, and 9207 dependency-only targets cannot close",
                "manifest relation allowlist excludes Datatracker refnorm/refinfo edges",
                "the current task compiler only builds a single draft revision normative hunk, not an update/dependency DAG resolver",
            ],
            "generation_allowed": False,
        },
        "artifacts": artifacts,
        "pairwise": pairwise,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "capacity": payload["capacity"],
                "adapter_gate": payload["adapter_gate"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
