"""Provenance-first inventory contracts for scholarly and Wikimedia workflows.

The builders consume already-downloaded API responses or text fixtures. They do
not fetch sources and they do not make records train-ready. Every workflow edge
must be stated in the source record at an exact character span; filenames never
participate in identity or relation validation.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import json
import lzma
import re
import tarfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from longworld.core.attestation import (
    attestation_key_from_env,
    canonical_attested_payload,
    verify_attestation,
)
from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    SourceLineage,
    _parse_timestamp,
    _read_regular_file,
)
from longworld.core.realworkflow import (
    MAX_WORKFLOW_RECORD_CHARS,
    MAX_WORKFLOW_RECORDS,
    RealWorkflow,
    WorkflowFact,
    WorkflowRecord,
)
from longworld.core.scholarly import format_revision_added_delta
from longworld.core.taxonomy import SourceOrigin

PAPER_WORKFLOW_INPUT_SCHEMA = "longworld.paper-workflow-input.v1"
PAPER_WORKFLOW_MANIFEST_SCHEMA = "longworld.paper-workflow-manifest.v1"
PAPER_FETCH_INVENTORY_SCHEMA = "longworld.paper-fetch-inventory.v1"
PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA = "longworld.paper-workflow-manifest.v2"
ELIFE_REVIEW_REVISION_REQUEST_SCHEMA = (
    "longworld.elife-review-revision-fetch-request.v1"
)
ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA = "longworld.elife-review-revision-inventory.v1"
WIKIPEDIA_WORKFLOW_INPUT_SCHEMA = "longworld.wikipedia-workflow-input.v2"
WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA = "longworld.wikipedia-workflow-manifest.v2"
WIKIMEDIA_FETCH_RECEIPT_SCHEMA = "longworld.wikimedia-fetch-receipt.v1"
WIKIMEDIA_USER_AGENT_POLICY = "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy"
DOCUMENT_WORKFLOW_SCANNER = "longworld-public-secret-patterns"
DOCUMENT_WORKFLOW_SCANNER_REVISION = "v2"
MAX_DOCUMENT_RECORDS = 512
MAX_DOCUMENT_RELATIONS = 2_048
MAX_DOCUMENT_MANIFEST_BYTES = 32_000_000
MAX_PAPER_FETCH_FILE_BYTES = 64_000_000
MAX_PAPER_ARCHIVE_MEMBERS = 2_048
MAX_PAPER_ARCHIVE_DECLARED_BYTES = 64_000_000
MAX_PAPER_ARCHIVE_EXPANSION_RATIO = 100
MAX_PAPER_ARCHIVE_STREAM_BYTES = 72_000_000
ARXIV_API_MANUAL = "https://info.arxiv.org/help/api/user-manual.html"
OPENREVIEW_API_DEFINITION = (
    "https://docs.openreview.net/reference/api-v2/openapi-definition"
)
ARXIV_ARCHIVE_PARSER_REVISION = "arxiv_source_tar_v2"
ELIFE_XML_PARSER_REVISION = "elife-article-xml@1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENTITY_ID = re.compile(r"^Q[1-9]\d*$")
_ARXIV_ID = re.compile(r"^(?P<base>(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7}))(?P<version>v\d+)$")
_ARXIV_LINK = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:\s*)(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])")
_SECRET_PATTERNS = (
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SOURCE_STATUSES = {
    "test_fixture",
    "public_api_export",
    "authorized_download",
}
_PAPER_ROLES = {
    "manuscript_revision",
    "peer_review",
    "author_response",
    "benchmark_report",
}
_PAPER_SOURCE_FAMILIES = {"arxiv_record", "openreview_note"}
_FORBIDDEN_FACT_FIELDS = {
    "filename",
    "revision_stem",
    "source_file",
    "source_path",
}
_PAPER_RELATION_ROLES = {
    "revision_of": ("manuscript_revision", "manuscript_revision"),
    "reviews": ("peer_review", "manuscript_revision"),
    "responds_to": ("author_response", "peer_review"),
    "evaluates_benchmark": ("manuscript_revision", "benchmark_report"),
    "reproduces_result": ("benchmark_report", "manuscript_revision"),
}
_WIKIMEDIA_KINDS = {"wikipedia_revision", "wikidata_entity_revision"}
_WIKIMEDIA_CONTENT_LICENSES = {
    "wikipedia_revision": "CC BY-SA 4.0; GFDL",
    "wikidata_entity_revision": "CC0-1.0",
}
_WIKIMEDIA_RELATION_KINDS = {
    "revision_of",
    "page_describes_entity",
    "entity_resolves_page",
}
_PAPER_FETCH_TOP_LEVEL_FIELDS = {
    "schema_version",
    "source_status",
    "data_stage",
    "hybrid_train_ready",
    "production_eligible",
    "generation_integration",
    "semantic_facts_train_ready",
    "generated_at",
    "request_file",
    "request_sha256",
    "authorization",
    "fetch_receipt",
    "n_records",
    "records",
}
_PAPER_FETCH_RECEIPT_FIELDS = {
    "arxiv_policy_url",
    "openreview_api_definition_url",
    "requests_per_second",
    "max_retries",
    "fetch_arxiv_source",
    "allowed_actions",
    "request_file",
    "request_sha256",
    "user_agent_sha256",
    "retrievals",
}
_PAPER_FETCH_RETRIEVAL_FIELDS = {
    "source_family",
    "requested_url",
    "final_url",
    "status",
    "content_type",
    "redirect_chain",
    "sha256",
    "retrieval_file",
}
_PAPER_FETCH_CONTENT_TYPES = {
    "arxiv_atom_api": {"application/atom+xml", "application/xml", "text/xml"},
    "arxiv_source_archive": {
        "application/gzip",
        "application/octet-stream",
        "application/x-eprint",
        "application/x-eprint-tar",
        "application/x-gzip",
        "application/x-tar",
    },
    "openreview_v2_api": {"application/json"},
}

_ELIFE_REQUEST_FIELDS = {
    "schema_version",
    "data_product",
    "authorization",
    "source",
    "candidate",
    "versions",
    "oracle_witness",
    "length_policy",
}
_ELIFE_EVIDENCE_IDS = frozenset(
    {
        "controlling_review",
        "direct_author_response",
        "body_figure_fig5",
        "appendix_APP9",
        "appendix_table_tbl3",
    }
)


def _elife_git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def _elife_local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _elife_visible_text(element: ET.Element) -> str:
    parts: list[str] = []

    def append(node: ET.Element, *, include_tail: bool = True) -> None:
        name = _elife_local_name(node)
        if name == "alternatives":
            choice = next(
                (child for child in node if _elife_local_name(child) == "tex-math"),
                next(iter(node), None),
            )
            if choice is not None:
                append(choice, include_tail=False)
        elif name in {"graphic", "inline-graphic"}:
            for descendant in node.iter():
                if _elife_local_name(descendant) == "alt-text" and descendant.text:
                    parts.append(descendant.text)
        else:
            if node.text:
                parts.append(node.text)
            for child in node:
                append(child)
        if include_tail and node.tail:
            parts.append(node.tail)

    append(element)
    return " ".join("".join(parts).split())


def _elife_article_identity(
    root: ET.Element,
    *,
    version: int,
    expected: dict[str, Any],
    candidate: dict[str, Any],
    license_url: str,
) -> dict[str, Any]:
    article_meta = root.find("./front/article-meta")
    if article_meta is None:
        raise ProvenanceError(f"eLife v{version} article-meta is missing")
    identifiers = {
        (item.attrib.get("pub-id-type"), item.attrib.get("specific-use")): " ".join(
            "".join(item.itertext()).split()
        )
        for item in article_meta.findall("./article-id")
    }
    title_nodes = article_meta.findall("./title-group/article-title")
    date_type = "original-publication" if version == 1 else "update"
    date_nodes = article_meta.findall(f"./pub-date[@date-type='{date_type}']")
    licenses = {
        item.attrib.get("{http://www.w3.org/1999/xlink}href")
        for item in article_meta.findall("./permissions/license")
    }
    observed = {
        "publisher_id": identifiers.get(("publisher-id", None)),
        "canonical_doi": identifiers.get(("doi", None)),
        "version_doi": identifiers.get(("doi", "version")),
        "title": _elife_visible_text(title_nodes[0]) if len(title_nodes) == 1 else "",
        "effective_date": (
            date_nodes[0].attrib.get("iso-8601-date") if len(date_nodes) == 1 else None
        ),
    }
    expected_identity = {
        "publisher_id": candidate.get("publisher_id"),
        "canonical_doi": candidate.get("canonical_doi"),
        "version_doi": expected.get("version_doi"),
        "title": candidate.get("title"),
        "effective_date": expected.get("effective_date"),
    }
    if observed != expected_identity:
        raise ProvenanceError(f"eLife v{version} DOI or article identity mismatch")
    if licenses != {license_url}:
        raise ProvenanceError(f"eLife v{version} license identity mismatch")
    return observed


def _elife_direct_paragraphs(subarticle: ET.Element) -> list[ET.Element]:
    paragraphs: list[ET.Element] = []

    def walk(element: ET.Element, inside_quote: bool = False) -> None:
        inside_quote = inside_quote or _elife_local_name(element) == "disp-quote"
        if _elife_local_name(element) == "p" and not inside_quote:
            paragraphs.append(element)
        for child in element:
            walk(child, inside_quote)

    body = subarticle.find("./body")
    if body is not None:
        walk(body)
    return paragraphs


def _elife_witness(
    roots: dict[int, ET.Element], request: dict[str, Any]
) -> dict[str, Any]:
    witness = request["oracle_witness"]
    review_digest = str(witness.get("review_paragraph_sha256") or "")
    response_digest = str(witness.get("response_paragraph_sha256") or "")
    if (
        _SHA256.fullmatch(review_digest) is None
        or _SHA256.fullmatch(response_digest) is None
    ):
        raise ProvenanceError("eLife oracle paragraph hash is invalid")

    controlling_reviews: list[dict[str, Any]] = []
    direct_responses: list[dict[str, Any]] = []
    review_quote_in_response = False
    for version, root in roots.items():
        for subarticle in root.findall("./sub-article"):
            article_type = subarticle.attrib.get("article-type")
            for paragraph in subarticle.findall("./body//p"):
                text = _elife_visible_text(paragraph)
                digest = hashlib.sha256(text.encode()).hexdigest()
                if (
                    version == 1
                    and article_type == "referee-report"
                    and digest == review_digest
                ):
                    controlling_reviews.append(
                        {
                            "version": version,
                            "subarticle_id": subarticle.attrib.get("id"),
                            "paragraph_sha256": digest,
                            "text": text,
                        }
                    )
            if version == 2 and article_type == "author-comment":
                review_quote_in_response = review_quote_in_response or any(
                    hashlib.sha256(_elife_visible_text(paragraph).encode()).hexdigest()
                    == review_digest
                    for paragraph in subarticle.findall("./body/disp-quote//p")
                )
                for paragraph in _elife_direct_paragraphs(subarticle):
                    text = _elife_visible_text(paragraph)
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    if digest == response_digest:
                        direct_responses.append(
                            {
                                "version": version,
                                "subarticle_id": subarticle.attrib.get("id"),
                                "paragraph_sha256": digest,
                                "text": text,
                            }
                        )
    if (
        len(controlling_reviews) != 1
        or len(direct_responses) != 1
        or not review_quote_in_response
    ):
        raise ProvenanceError("eLife review-response witness is not unique")

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
    expected_transitions = witness.get("required_v1_to_v2_object_transitions")
    if transitions != expected_transitions or any(
        counts != [0, 1] for counts in transitions.values()
    ):
        raise ProvenanceError("eLife revision-object transition mismatch")
    return {
        "controlling_review": controlling_reviews[0],
        "direct_author_response": direct_responses[0],
        "review_quote_embedded_in_v2_author_comment": True,
        "v1_to_v2_object_transitions": transitions,
    }


def _elife_evidence(
    record: dict[str, Any], evidence_id: str, quote: str
) -> dict[str, Any]:
    text = str(record["text"])
    if not quote or text.count(quote) != 1:
        raise ProvenanceError(f"eLife {evidence_id} evidence is not unique")
    start = text.index(quote)
    return {
        "evidence_id": evidence_id,
        "record_id": record["record_id"],
        "evidence_quote": quote,
        "evidence_char_start": start,
        "evidence_char_end": start + len(quote),
        "source_sha256": record["source_sha256"],
        "text_sha256": record["text_sha256"],
    }


def _elife_task_and_relations(
    records: list[dict[str, Any]],
    witness: dict[str, Any],
    *,
    program_id: object,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    v1, v2 = records
    evidence = {
        "controlling_review": _elife_evidence(
            v1, "controlling_review", witness["controlling_review"]["text"]
        ),
        "direct_author_response": _elife_evidence(
            v2,
            "direct_author_response",
            witness["direct_author_response"]["text"],
        ),
        "body_figure_fig5": _elife_evidence(v2, "body_figure_fig5", '<fig id="fig5"'),
        "appendix_APP9": _elife_evidence(v2, "appendix_APP9", '<app id="APP9"'),
        "appendix_table_tbl3": _elife_evidence(
            v2, "appendix_table_tbl3", '<table-wrap id="tbl3"'
        ),
        "v1_version_doi": _elife_evidence(
            v1,
            "v1_version_doi",
            '<article-id pub-id-type="doi" specific-use="version">'
            f"{v1['version_doi']}</article-id>",
        ),
        "v2_version_doi": _elife_evidence(
            v2,
            "v2_version_doi",
            '<article-id pub-id-type="doi" specific-use="version">'
            f"{v2['version_doi']}</article-id>",
        ),
    }
    grounded_witness = {**witness, "evidence": evidence}
    task = {
        "program_id": program_id,
        "answer": "VERIFIED_IMPLEMENTED",
        "essential_evidence_ids": sorted(_ELIFE_EVIDENCE_IDS),
        "witness": grounded_witness,
        "model_written_gold": False,
    }
    relations = [
        {
            "relation_id": "elife:94586:review-requests-revision",
            "kind": "requests_revision",
            "source_record_id": v1["record_id"],
            "target_record_id": v2["record_id"],
            "evidence": [
                evidence["controlling_review"],
                evidence["v2_version_doi"],
            ],
        },
        {
            "relation_id": "elife:94586:response-to-review",
            "kind": "responds_to_review",
            "source_record_id": v2["record_id"],
            "target_record_id": v1["record_id"],
            "evidence": [
                evidence["direct_author_response"],
                evidence["controlling_review"],
            ],
        },
        {
            "relation_id": "elife:94586:v2-revision-of-v1",
            "kind": "revision_of",
            "source_record_id": v2["record_id"],
            "target_record_id": v1["record_id"],
            "evidence": [evidence["v2_version_doi"], evidence["v1_version_doi"]],
        },
        {
            "relation_id": "elife:94586:v2-implements-review-delta",
            "kind": "implements_revision_delta",
            "source_record_id": v2["record_id"],
            "target_record_id": v1["record_id"],
            "evidence": [
                evidence["direct_author_response"],
                evidence["body_figure_fig5"],
                evidence["appendix_APP9"],
                evidence["appendix_table_tbl3"],
                evidence["v1_version_doi"],
            ],
        },
    ]
    return task, relations


def build_elife_review_revision_inventory(
    request: dict[str, Any],
    version_bytes: dict[int, bytes],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Build a disabled eLife v1/v2 source inventory from pinned XML bytes."""
    if (
        set(request) != _ELIFE_REQUEST_FIELDS
        or request.get("schema_version") != ELIFE_REVIEW_REVISION_REQUEST_SCHEMA
    ):
        raise ProvenanceError("unsupported eLife review-revision request schema")
    _parse_timestamp(generated_at, "generated_at")
    authorization = request.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("eLife source authorization is invalid")
    _validate_authorization(authorization)
    if authorization.get("allowed_actions") != ["fetch_pinned_elife_xml"]:
        raise ProvenanceError("eLife source authorization action is invalid")

    source = request.get("source")
    candidate = request.get("candidate")
    versions = request.get("versions")
    length_policy = request.get("length_policy")
    if (
        not isinstance(source, dict)
        or not isinstance(candidate, dict)
        or not isinstance(versions, list)
        or len(versions) != 2
        or not isinstance(length_policy, dict)
        or length_policy.get("allowed_bands") != ["64k"]
        or length_policy.get("exact_64k_band") != [65536, 67584]
        or not isinstance(length_policy.get("measured_near_dedup_capacity"), int)
        or length_policy["measured_near_dedup_capacity"] < 65536
    ):
        raise ProvenanceError("eLife v1/v2 64K request is invalid")
    repository = str(source.get("repository") or "")
    commit = str(source.get("repository_commit") or "")
    repository_url = str(source.get("repository_url") or "")
    license_url = str(source.get("license_url") or "")
    if (
        repository != "elifesciences/elife-article-xml"
        or repository_url != "https://github.com/elifesciences/elife-article-xml"
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or license_url != "https://creativecommons.org/licenses/by/4.0/"
    ):
        raise ProvenanceError("eLife official repository identity is invalid")
    if set(version_bytes) != {1, 2}:
        raise ProvenanceError("eLife inventory requires exactly v1 and v2 bytes")

    roots: dict[int, ET.Element] = {}
    records: list[dict[str, Any]] = []
    for expected_version, expected in zip((1, 2), versions, strict=True):
        if (
            not isinstance(expected, dict)
            or expected.get("version") != expected_version
        ):
            raise ProvenanceError("eLife version order or identity is invalid")
        raw = version_bytes[expected_version]
        if not isinstance(raw, bytes) or not raw:
            raise ProvenanceError(f"eLife v{expected_version} bytes are invalid")
        observed = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "git_blob_sha1": _elife_git_blob_sha1(raw),
        }
        for field, value in observed.items():
            if expected.get(field) != value:
                raise ProvenanceError(f"eLife v{expected_version} {field} mismatch")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as error:
            raise ProvenanceError(
                f"eLife v{expected_version} XML is invalid"
            ) from error
        raw_text = raw.decode("utf-8")
        _reject_secrets(raw_text)
        clean_text = _EMAIL.sub("[redacted-email]", raw_text)
        text_sha256 = hashlib.sha256(clean_text.encode()).hexdigest()
        roots[expected_version] = root
        identity = _elife_article_identity(
            root,
            version=expected_version,
            expected=expected,
            candidate=candidate,
            license_url=license_url,
        )
        path = str(expected.get("path") or "")
        if (
            PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or path
            != f"preprints/elife-preprint-{candidate.get('publisher_id')}-v{expected_version}.xml"
        ):
            raise ProvenanceError(
                f"eLife v{expected_version} repository path is invalid"
            )
        records.append(
            {
                "record_id": f"elife:{candidate['publisher_id']}:v{expected_version}",
                "version": expected_version,
                "revision_id": f"v{expected_version}",
                **identity,
                "repository_path": path,
                "repository_commit": commit,
                "source_url": (
                    f"https://raw.githubusercontent.com/{repository}/{commit}/{path}"
                ),
                "bytes": observed["bytes"],
                "source_sha256": observed["sha256"],
                "provenance_id": f"sha256:{observed['sha256']}",
                "git_blob_sha1": observed["git_blob_sha1"],
                "parser": ELIFE_XML_PARSER_REVISION,
                "text_sha256": text_sha256,
                "text": clean_text,
                "privacy_review": {
                    "emails": "redacted",
                    "email_redaction_count": len(_EMAIL.findall(raw_text)),
                    "secrets": "fail_closed",
                    "scanner": DOCUMENT_WORKFLOW_SCANNER,
                    "scanner_revision": DOCUMENT_WORKFLOW_SCANNER_REVISION,
                },
            }
        )
    witness = _elife_witness(roots, request)
    task, relations = _elife_task_and_relations(
        records, witness, program_id=candidate.get("transition_program")
    )
    request_sha256 = hashlib.sha256(
        json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return {
        "schema_version": ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA,
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "generated_at": generated_at,
        "data_product": request.get("data_product"),
        "authorization": dict(authorization),
        "source": dict(source),
        "length_policy": dict(length_policy),
        "request": request,
        "request_sha256": request_sha256,
        "n_records": len(records),
        "records": records,
        "n_relations": len(relations),
        "relations": relations,
        "task": task,
    }


def replay_elife_review_revision_task(
    inventory: dict[str, Any], *, removed_evidence_ids: frozenset[str] = frozenset()
) -> str:
    """Replay the deterministic eLife claim disposition after evidence removal."""
    if not removed_evidence_ids.issubset(_ELIFE_EVIDENCE_IDS):
        raise ProvenanceError("eLife task removal references unknown evidence")
    task = inventory.get("task")
    if not isinstance(task, dict) or set(task.get("essential_evidence_ids") or []) != (
        _ELIFE_EVIDENCE_IDS
    ):
        raise ProvenanceError("eLife task evidence contract is invalid")
    if "controlling_review" in removed_evidence_ids:
        return "UNKNOWN_NO_REVIEW_CLAIM"
    if "direct_author_response" in removed_evidence_ids:
        return "UNKNOWN_NO_AUTHOR_RESPONSE"
    if removed_evidence_ids & {
        "body_figure_fig5",
        "appendix_APP9",
        "appendix_table_tbl3",
    }:
        return "CLAIMED_NOT_VERIFIED"
    return "VERIFIED_IMPLEMENTED"


def audit_elife_review_revision_task(inventory: dict[str, Any]) -> dict[str, Any]:
    """Reparse redacted sources and audit strict plus every remove-one replay."""
    if inventory.get("schema_version") != ELIFE_REVIEW_REVISION_INVENTORY_SCHEMA:
        raise ProvenanceError("unsupported eLife review-revision inventory schema")
    records = inventory.get("records")
    request = inventory.get("request")
    if (
        not isinstance(records, list)
        or len(records) != 2
        or not isinstance(request, dict)
    ):
        raise ProvenanceError("eLife review-revision inventory is invalid")
    _parse_timestamp(str(inventory.get("generated_at") or ""), "generated_at")
    expected_request_sha256 = hashlib.sha256(
        json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if (
        inventory.get("source_status") != "public_api_export"
        or inventory.get("data_stage") != "source_inventory"
        or inventory.get("hybrid_train_ready") is not False
        or inventory.get("production_eligible") is not False
        or inventory.get("generation_integration") != "disabled"
        or inventory.get("request_sha256") != expected_request_sha256
        or inventory.get("n_records") != 2
        or inventory.get("n_relations") != 4
        or inventory.get("authorization") != request.get("authorization")
        or inventory.get("source") != request.get("source")
        or inventory.get("length_policy") != request.get("length_policy")
    ):
        raise ProvenanceError("eLife inventory does not match its source contract")
    versions = request.get("versions")
    candidate = request.get("candidate")
    source = request.get("source")
    if (
        not isinstance(versions, list)
        or len(versions) != 2
        or not isinstance(candidate, dict)
        or not isinstance(source, dict)
    ):
        raise ProvenanceError("eLife inventory request is invalid")
    roots: dict[int, ET.Element] = {}
    for record, expected in zip(records, versions, strict=True):
        if not isinstance(record, dict) or not isinstance(expected, dict):
            raise ProvenanceError("eLife inventory source record is invalid")
        text = record.get("text")
        privacy = record.get("privacy_review")
        expected_version = expected.get("version")
        expected_sha256 = expected.get("sha256")
        if (
            not isinstance(text, str)
            or _EMAIL.search(text)
            or hashlib.sha256(text.encode()).hexdigest() != record.get("text_sha256")
            or record.get("source_sha256") != expected_sha256
            or record.get("provenance_id") != f"sha256:{expected_sha256}"
            or record.get("bytes") != expected.get("bytes")
            or record.get("git_blob_sha1") != expected.get("git_blob_sha1")
            or record.get("version") != expected_version
            or record.get("repository_path") != expected.get("path")
            or record.get("repository_commit") != source.get("repository_commit")
            or record.get("parser") != ELIFE_XML_PARSER_REVISION
            or not isinstance(privacy, dict)
            or privacy
            != {
                "emails": "redacted",
                "email_redaction_count": privacy.get("email_redaction_count"),
                "secrets": "fail_closed",
                "scanner": DOCUMENT_WORKFLOW_SCANNER,
                "scanner_revision": DOCUMENT_WORKFLOW_SCANNER_REVISION,
            }
            or not isinstance(privacy["email_redaction_count"], int)
            or privacy["email_redaction_count"] < 0
            or text.count("[redacted-email]") != privacy["email_redaction_count"]
        ):
            raise ProvenanceError("eLife inventory does not match its source bytes")
        expected_url = (
            f"https://raw.githubusercontent.com/{source['repository']}/"
            f"{source['repository_commit']}/{expected['path']}"
        )
        if record.get("source_url") != expected_url:
            raise ProvenanceError("eLife inventory source URL is invalid")
        try:
            root = ET.fromstring(text.encode())
        except ET.ParseError as error:
            raise ProvenanceError("eLife redacted source XML is invalid") from error
        roots[int(expected_version)] = root
        _elife_article_identity(
            root,
            version=int(expected_version),
            expected=expected,
            candidate=candidate,
            license_url=str(source.get("license_url") or ""),
        )
    witness = _elife_witness(roots, request)
    expected_task, expected_relations = _elife_task_and_relations(
        records, witness, program_id=candidate.get("transition_program")
    )
    if (
        inventory.get("task") != expected_task
        or inventory.get("relations") != expected_relations
    ):
        raise ProvenanceError("eLife inventory does not match its source relations")

    strict = replay_elife_review_revision_task(inventory)
    without_review = replay_elife_review_revision_task(
        inventory, removed_evidence_ids=frozenset({"controlling_review"})
    )
    without_response = replay_elife_review_revision_task(
        inventory, removed_evidence_ids=frozenset({"direct_author_response"})
    )
    delta_results = {
        evidence_id: replay_elife_review_revision_task(
            inventory, removed_evidence_ids=frozenset({evidence_id})
        )
        for evidence_id in sorted(
            {
                "body_figure_fig5",
                "appendix_APP9",
                "appendix_table_tbl3",
            }
        )
    }
    expected = str(inventory["task"].get("answer") or "")
    result = {
        "program_id": inventory["task"].get("program_id"),
        "strict_answer": strict,
        "without_controlling_review": without_review,
        "without_direct_response": without_response,
        "without_required_revision_delta": delta_results,
        "remove_review_fails": without_review != expected,
        "remove_response_fails": without_response != expected,
        "remove_delta_fails": all(
            answer != expected for answer in delta_results.values()
        ),
        "model_written_gold": inventory["task"].get("model_written_gold"),
    }
    result["passed"] = bool(
        strict == expected
        and result["remove_review_fails"]
        and result["remove_response_fails"]
        and result["remove_delta_fails"]
        and result["model_written_gold"] is False
    )
    return result


def _reject_secrets(text: str) -> None:
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        raise ProvenanceError("source text contains a credential-shaped secret")


def _contains_identity(text: str, identity: object) -> bool:
    value = str(identity)
    return (
        re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text)
        is not None
    )


def _validate_authorization(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProvenanceError("document source authorization receipt is invalid")
    required = ("record_id", "scope", "basis", "reviewed_at")
    receipt = {field: str(value.get(field) or "").strip() for field in required}
    if any(not receipt[field] for field in required):
        raise ProvenanceError("document source authorization receipt is invalid")
    _parse_timestamp(receipt["reviewed_at"], "authorization.reviewed_at")
    return receipt


def _safe_source_path(base_directory: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("document source file is missing")
    relative = Path(value)
    if (
        relative.name != value
        or relative.is_absolute()
        or relative.suffix.lower() not in {".json", ".md", ".txt"}
    ):
        raise ProvenanceError("document source file is unsafe or unsupported")
    return base_directory / relative


def _fetch_file_bytes(
    base_directory: Path, value: object, expected_sha256: object, *, label: str
) -> tuple[Path, bytes]:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ProvenanceError(f"paper fetch {label} file is unsafe")
    digest = str(expected_sha256 or "")
    if _SHA256.fullmatch(digest) is None:
        raise ProvenanceError(f"paper fetch {label} hash is invalid")
    path = base_directory / value
    try:
        raw = _read_regular_file(path, MAX_PAPER_FETCH_FILE_BYTES)
    except OSError as error:
        raise ProvenanceError(f"cannot read paper fetch {label}: {error}") from error
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ProvenanceError(f"paper fetch {label} hash mismatch")
    return path, raw


def _validate_fetch_retrieval(
    value: object, base_directory: Path
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, dict):
        raise ProvenanceError("paper fetch retrieval metadata is invalid")
    source_family = str(value.get("source_family") or "")
    expected_fields = set(_PAPER_FETCH_RETRIEVAL_FIELDS)
    if source_family == "arxiv_source_archive":
        expected_fields.add("parser")
    if set(value) != expected_fields or source_family not in _PAPER_FETCH_CONTENT_TYPES:
        raise ProvenanceError("paper fetch retrieval metadata is invalid")
    requested_url = str(value.get("requested_url") or "")
    final_url = str(value.get("final_url") or "")
    requested = urlparse(requested_url)
    content_type = str(value.get("content_type") or "").lower()
    if (
        requested.scheme != "https"
        or requested.netloc != (requested.hostname or "")
        or requested.fragment
        or requested.params
        or value.get("status") != 200
        or content_type not in _PAPER_FETCH_CONTENT_TYPES[source_family]
    ):
        raise ProvenanceError("paper fetch final retrieval metadata is invalid")
    redirect_chain = value.get("redirect_chain")
    if not isinstance(redirect_chain, list):
        raise ProvenanceError("paper fetch redirect chain is invalid")
    if redirect_chain:
        expected_final = requested_url.replace("/e-print/", "/src/", 1)
        if (
            source_family != "arxiv_source_archive"
            or len(redirect_chain) != 1
            or redirect_chain[0]
            != {
                "status": 301,
                "from_url": requested_url,
                "to_url": expected_final,
            }
            or final_url != expected_final
        ):
            raise ProvenanceError("paper fetch redirect chain is invalid")
    elif final_url != requested_url:
        raise ProvenanceError("paper fetch final retrieval metadata is invalid")
    if source_family == "arxiv_atom_api":
        valid_url = (
            requested.hostname == "export.arxiv.org"
            and requested.path == "/api/query"
            and set(parse_qs(requested.query)) == {"id_list"}
            and len(parse_qs(requested.query)["id_list"]) == 1
        )
    elif source_family == "arxiv_source_archive":
        valid_url = (
            requested.hostname == "export.arxiv.org"
            and requested.path.startswith("/e-print/")
            and not requested.query
            and value.get("parser") == ARXIV_ARCHIVE_PARSER_REVISION
        )
    else:
        valid_url = (
            requested.hostname == "api2.openreview.net"
            and requested.path == "/notes"
            and set(parse_qs(requested.query)) == {"forum"}
            and len(parse_qs(requested.query)["forum"]) == 1
        )
    if not valid_url:
        raise ProvenanceError("paper fetch requested URL is invalid")
    _path, raw = _fetch_file_bytes(
        base_directory,
        value.get("retrieval_file"),
        value.get("sha256"),
        label="retrieval",
    )
    return dict(value), raw


class _BoundedArchiveReader(io.RawIOBase):
    def __init__(self, stream: Any, compressed_size: int) -> None:
        super().__init__()
        self.stream = stream
        self.total = 0
        self.ratio_limit = compressed_size * MAX_PAPER_ARCHIVE_EXPANSION_RATIO

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        limit = min(MAX_PAPER_ARCHIVE_STREAM_BYTES, self.ratio_limit)
        remaining = limit - self.total
        bounded_size = remaining + 1 if size < 0 else min(size, remaining + 1)
        payload = self.stream.read(bounded_size)
        self.total += len(payload)
        if self.total > self.ratio_limit:
            raise ProvenanceError(
                "paper source archive compression ratio exceeds limit"
            )
        if self.total > MAX_PAPER_ARCHIVE_STREAM_BYTES:
            raise ProvenanceError("paper source archive stream exceeds size limit")
        return payload


def _decompressed_archive_stream(raw: bytes) -> Any:
    source = io.BytesIO(raw)
    if raw.startswith(b"\x1f\x8b"):
        return gzip.GzipFile(fileobj=source, mode="rb")
    if raw.startswith(b"BZh"):
        return bz2.BZ2File(source, mode="rb")
    if raw.startswith(b"\xfd7zXZ\x00"):
        return lzma.LZMAFile(source, mode="rb")
    return source


def _latex_sources_from_archive(raw: bytes) -> list[dict[str, str]]:
    stream = _decompressed_archive_stream(raw)
    try:
        context = tarfile.open(  # noqa: SIM115 - malformed archives raise on open
            fileobj=_BoundedArchiveReader(stream, len(raw)), mode="r|"
        )
        with context as archive:
            by_digest: dict[str, tuple[str, str]] = {}
            total = 0
            declared_bytes = 0
            member_count = 0
            for member in archive:
                member_count += 1
                if member_count > MAX_PAPER_ARCHIVE_MEMBERS:
                    raise ProvenanceError(
                        "paper source archive member count is invalid"
                    )
                if member.size < 0:
                    raise ProvenanceError(
                        "paper source archive declared size is invalid"
                    )
                declared_bytes += member.size
                if declared_bytes > MAX_PAPER_ARCHIVE_DECLARED_BYTES:
                    raise ProvenanceError(
                        "paper source archive declared size exceeds limit"
                    )
                if declared_bytes > len(raw) * MAX_PAPER_ARCHIVE_EXPANSION_RATIO:
                    raise ProvenanceError(
                        "paper source archive compression ratio exceeds limit"
                    )
                path = PurePosixPath(member.name)
                if (
                    not member.name
                    or "\\" in member.name
                    or path.is_absolute()
                    or ".." in path.parts
                    or len(member.name) > 512
                ):
                    raise ProvenanceError("paper source archive member is unsafe")
                if not member.isfile() or path.suffix.lower() != ".tex":
                    continue
                if member.size <= 0 or member.size > 4_000_000:
                    raise ProvenanceError("paper LaTeX source size is invalid")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ProvenanceError("paper LaTeX source is unreadable")
                payload = handle.read(4_000_001)
                if len(payload) != member.size:
                    raise ProvenanceError("paper LaTeX source size changed")
                total += len(payload)
                if total > 8_000_000:
                    raise ProvenanceError("paper LaTeX source exceeds size limit")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ProvenanceError("paper LaTeX source is not UTF-8") from error
                digest = hashlib.sha256(payload).hexdigest()
                current = by_digest.get(digest)
                if current is None or (len(member.name), member.name) < (
                    len(current[0]),
                    current[0],
                ):
                    by_digest[digest] = (member.name, text)
            if member_count == 0:
                raise ProvenanceError("paper source archive member count is invalid")
    except ProvenanceError:
        raise
    except (EOFError, OSError, lzma.LZMAError, tarfile.TarError) as error:
        raise ProvenanceError("paper source archive is malformed") from error
    finally:
        stream.close()
    if not by_digest:
        raise ProvenanceError("paper source archive has no LaTeX text")
    return [
        {"path": name, "sha256": digest, "text": text}
        for digest, (name, text) in sorted(
            by_digest.items(), key=lambda item: item[1][0]
        )
    ]


def _validated_latex_sources(value: object) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise ProvenanceError("paper source has no LaTeX sources")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "text"}:
            raise ProvenanceError("paper LaTeX source binding is invalid")
        path = str(item.get("path") or "")
        text = item.get("text")
        posix_path = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or posix_path.is_absolute()
            or ".." in posix_path.parts
            or posix_path.suffix.lower() != ".tex"
            or path in result
            or not isinstance(text, str)
            or not text
            or hashlib.sha256(text.encode()).hexdigest() != item.get("sha256")
        ):
            raise ProvenanceError("paper LaTeX source binding is invalid")
        result[path] = text
    return result


def _semantic_latex_lines(text: str) -> list[str]:
    candidates: list[str] = []
    boilerplate = (
        "this paper is organized",
        "all rights reserved",
        "preprint submitted",
    )
    for raw_line in text.splitlines():
        line = re.split(r"(?<!\\)%", raw_line, maxsplit=1)[0].strip()
        fragments = (
            re.split(r"(?<=[.!?])\s+", line)
            if any(character in line for character in "{}\\")
            else [line]
        )
        for fragment in fragments:
            exact = fragment.strip()
            normalized = " ".join(exact.split())
            words = re.findall(r"[A-Za-z]{2,}", normalized)
            if (
                len(normalized) < 40
                or len(words) < 7
                or normalized.startswith(("\\", "%"))
                or any(character in normalized for character in "{}\\")
                or normalized[-1:] not in ".!?"
                or normalized.lower().startswith(boilerplate)
                or ".tex" in normalized.lower()
            ):
                continue
            candidates.append(exact)
    return candidates


def _semantic_candidate_rank(candidate: str) -> tuple[int, int, int, int]:
    lowered = candidate.casefold()
    funding_terms = len(
        re.findall(r"\b(?:fund(?:ed|ing)?|grant|support(?:ed)?)\b", lowered)
    )
    named_funders = len(re.findall(r"\b(?:darpa|erc|nih|nsf|onr)\b", lowered))
    grant_numbers = len(re.findall(r"\b\d{4,}\b", lowered))
    return funding_terms, named_funders, grant_numbers, len(candidate)


def _revision_added_text_fact(
    previous_sources: dict[str, str],
    current_sources: dict[str, str],
    *,
    current_record_text: str,
) -> dict[str, Any] | None:
    previous = " ".join(
        " ".join(previous_sources[path].split()) for path in sorted(previous_sources)
    ).casefold()
    candidates: list[tuple[tuple[int, int, int, int], str, str]] = []
    for path in sorted(current_sources):
        for candidate in _semantic_latex_lines(current_sources[path]):
            normalized = " ".join(candidate.split())
            if (
                normalized.casefold() in previous
                or current_record_text.count(candidate) != 1
                or not format_revision_added_delta(normalized)
            ):
                continue
            candidates.append((_semantic_candidate_rank(normalized), path, candidate))
    if not candidates:
        return None
    _rank, _path, selected = min(
        candidates,
        key=lambda item: (
            tuple(-score for score in item[0]),
            item[1],
            item[2],
        ),
    )
    return {
        "fact_id": "revision-added-text",
        "field": "revision_added_text",
        "value": selected,
        "evidence_quote": selected,
        "evidence_char_start": current_record_text.index(selected),
    }


def _parser_and_lineage(record: dict[str, Any]) -> tuple[str, str, str]:
    occurred_at = str(record.get("occurred_at") or "")
    retrieved_at = str(record.get("retrieved_at") or "")
    access_policy = str(record.get("access_policy") or "").strip()
    parser = record.get("parser")
    _parse_timestamp(occurred_at, "record occurred_at")
    _parse_timestamp(retrieved_at, "record retrieved_at")
    if not access_policy or not isinstance(parser, dict):
        raise ProvenanceError("document source lineage is incomplete")
    parser_name = str(parser.get("name") or "").strip()
    parser_version = str(parser.get("version") or "").strip()
    if not parser_name or not parser_version:
        raise ProvenanceError("document source parser metadata is invalid")
    return occurred_at, retrieved_at, f"{parser_name}@{parser_version}"


def _clean_derived_facts(
    value: object, *, raw_text: str, clean_text: str, text_sha256: str
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 512:
        raise ProvenanceError("document derived facts are invalid")
    cleaned: list[dict[str, Any]] = []
    fact_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "fact_id",
            "field",
            "value",
            "evidence_quote",
            "evidence_char_start",
        }:
            raise ProvenanceError("document derived fact is invalid")
        fact_id = str(item.get("fact_id") or "").strip()
        field = str(item.get("field") or "").strip()
        fact_value = str(item.get("value") or "").strip()
        quote = str(item.get("evidence_quote") or "")
        start = item.get("evidence_char_start")
        if (
            not fact_id
            or fact_id in fact_ids
            or not field
            or field in _FORBIDDEN_FACT_FIELDS
            or not fact_value
            or not quote
            or not isinstance(start, int)
            or start < 0
        ):
            raise ProvenanceError("document derived fact field or identity is invalid")
        if raw_text[start : start + len(quote)] != quote:
            raise ProvenanceError("document derived fact evidence span is invalid")
        if raw_text.count(quote) != 1 or fact_value not in quote:
            raise ProvenanceError("document derived fact evidence is not unique")
        if _EMAIL.search(quote) or _EMAIL.search(fact_value):
            raise ProvenanceError("document derived fact contains email PII")
        _reject_secrets(quote)
        _reject_secrets(fact_value)
        clean_start = len(_EMAIL.sub("[redacted-email]", raw_text[:start]))
        if clean_text[clean_start : clean_start + len(quote)] != quote:
            raise ProvenanceError("document derived fact evidence span is invalid")
        cleaned.append(
            {
                "fact_id": fact_id,
                "field": field,
                "value": fact_value,
                "evidence_quote": quote,
                "evidence_char_start": clean_start,
                "text_sha256": text_sha256,
            }
        )
        fact_ids.add(fact_id)
    return cleaned


def _audit_derived_facts(value: object, *, text: str, text_sha256: str) -> None:
    if not isinstance(value, list) or not value or len(value) > 512:
        raise ProvenanceError("document derived facts are invalid")
    fact_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "fact_id",
            "field",
            "value",
            "evidence_quote",
            "evidence_char_start",
            "text_sha256",
        }:
            raise ProvenanceError("document derived fact is invalid")
        fact_id = str(item.get("fact_id") or "").strip()
        field = str(item.get("field") or "").strip()
        fact_value = str(item.get("value") or "").strip()
        quote = str(item.get("evidence_quote") or "")
        start = item.get("evidence_char_start")
        if (
            not fact_id
            or fact_id in fact_ids
            or not field
            or field in _FORBIDDEN_FACT_FIELDS
            or not fact_value
            or fact_value not in quote
            or not isinstance(start, int)
            or start < 0
            or text[start : start + len(quote)] != quote
            or text.count(quote) != 1
            or item.get("text_sha256") != text_sha256
        ):
            raise ProvenanceError("document derived fact binding is invalid")
        if _EMAIL.search(quote) or _EMAIL.search(fact_value):
            raise ProvenanceError("document derived fact contains email PII")
        _reject_secrets(quote)
        _reject_secrets(fact_value)
        fact_ids.add(fact_id)


def _source_record(
    record: dict[str, Any],
    base_directory: Path,
    *,
    identity: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    record_id = str(record.get("record_id") or "").strip()
    source_url = str(record.get("source_url") or "")
    retrieval_url = str(record.get("retrieval_url") or "")
    if not record_id:
        raise ProvenanceError("document record id is missing")
    _reject_secrets(source_url)
    _reject_secrets(retrieval_url)
    occurred_at, retrieved_at, parser = _parser_and_lineage(record)
    source_path = _safe_source_path(base_directory, record.get("source_file"))
    try:
        raw = _read_regular_file(source_path, MAX_SOURCE_BYTES)
        raw_text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProvenanceError(f"cannot read document source: {exc}") from exc
    if source_path.suffix.lower() == ".json":
        try:
            json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ProvenanceError("document JSON source is invalid") from exc
    source_sha256 = str(record.get("source_sha256") or "")
    if _SHA256.fullmatch(source_sha256) is None:
        raise ProvenanceError("document source sha256 is missing or invalid")
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ProvenanceError("document source hash mismatch")
    _reject_secrets(raw_text)
    clean_text = _EMAIL.sub("[redacted-email]", raw_text)
    text_sha256 = hashlib.sha256(clean_text.encode()).hexdigest()
    cleaned_record = {
        "record_id": record_id,
        **identity,
        "source_url": source_url,
        "source_file": source_path.name,
        "source_sha256": source_sha256,
        "provenance_id": f"sha256:{source_sha256}",
        "occurred_at": occurred_at,
        "retrieved_at": retrieved_at,
        "access_policy": str(record["access_policy"]).strip(),
        "parser": parser,
        "text_sha256": text_sha256,
        "text": clean_text,
        "privacy_review": {
            "emails": "redacted",
            "email_redaction_count": len(_EMAIL.findall(raw_text)),
            "secrets": "fail_closed",
            "scanner": DOCUMENT_WORKFLOW_SCANNER,
            "scanner_revision": DOCUMENT_WORKFLOW_SCANNER_REVISION,
        },
    }
    if retrieval_url:
        cleaned_record["retrieval_url"] = retrieval_url
    if "derived_facts" in record:
        cleaned_record["derived_facts"] = _clean_derived_facts(
            record["derived_facts"],
            raw_text=raw_text,
            clean_text=clean_text,
            text_sha256=text_sha256,
        )
    return cleaned_record, raw_text


def _paper_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    work_id = str(record.get("work_id") or "").strip()
    role = str(record.get("role") or "")
    source_family = str(record.get("source_family") or "")
    revision_id = str(record.get("revision_id") or "").strip()
    if not work_id or not revision_id or role not in _PAPER_ROLES:
        raise ProvenanceError("paper record identity is invalid")
    if source_family not in _PAPER_SOURCE_FAMILIES:
        raise ProvenanceError("paper source family is invalid")
    parsed = urlparse(str(record.get("source_url") or ""))
    host = (parsed.hostname or "").lower()
    expected_domain = (
        "arxiv.org" if source_family == "arxiv_record" else "openreview.net"
    )
    if (
        parsed.scheme != "https"
        or not (host == expected_domain or host.endswith(f".{expected_domain}"))
        or parsed.netloc != host
        or parsed.fragment
        or parsed.params
    ):
        raise ProvenanceError("paper source URL does not match its source family")
    if source_family == "arxiv_record" and role != "manuscript_revision":
        raise ProvenanceError("arXiv records must represent manuscript revisions")
    if source_family == "arxiv_record" and not parsed.path.rstrip("/").endswith(
        revision_id
    ):
        raise ProvenanceError("arXiv source URL does not bind the revision identity")
    if source_family == "arxiv_record":
        url_identity = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        matched = _ARXIV_ID.fullmatch(url_identity)
        if (
            matched is None
            or revision_id != matched.group("version")
            or work_id != f"arxiv:{matched.group('base')}"
        ):
            raise ProvenanceError("arXiv work identity is not derived from its URL")
    if source_family == "openreview_note":
        query = parse_qs(parsed.query)
        direct_note = set(query) == {"id"} and query.get("id") == [revision_id]
        forum_note = (
            set(query) == {"id", "noteId"}
            and query.get("noteId") == [revision_id]
            and query.get("id") == [work_id.removeprefix("openreview:")]
        )
        if not direct_note and not forum_note:
            raise ProvenanceError(
                "OpenReview source URL does not bind the note identity"
            )
    return {
        "work_id": work_id,
        "role": role,
        "source_family": source_family,
        "revision_id": revision_id,
    }


def _wikipedia_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    kind = str(record.get("kind") or "")
    revision_id = record.get("revision_id")
    if (
        kind not in _WIKIMEDIA_KINDS
        or not isinstance(revision_id, int)
        or revision_id <= 0
    ):
        raise ProvenanceError("Wikimedia record identity is invalid")
    parsed = urlparse(str(record.get("source_url") or ""))
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.netloc != host
        or parsed.fragment
        or parsed.params
    ):
        raise ProvenanceError("Wikimedia source URL must use HTTPS")
    if kind == "wikipedia_revision":
        page_id = record.get("page_id")
        parent_revision_id = record.get("parent_revision_id")
        title = str(record.get("title") or "").strip()
        oldids = parse_qs(parsed.query).get("oldid", [])
        if (
            not host.endswith(".wikipedia.org")
            or not isinstance(page_id, int)
            or page_id <= 0
            or not title
            or parsed.path != "/w/index.php"
            or set(parse_qs(parsed.query)) != {"oldid"}
            or (
                parent_revision_id is not None
                and (not isinstance(parent_revision_id, int) or parent_revision_id <= 0)
            )
            or oldids != [str(revision_id)]
        ):
            raise ProvenanceError(
                "Wikipedia revision source oldid or identity is invalid"
            )
        return {
            "kind": kind,
            "revision_id": revision_id,
            "page_id": page_id,
            "title": title,
            "parent_revision_id": parent_revision_id,
        }
    entity_id = str(record.get("entity_id") or "")
    entity_ids = {
        item
        for raw_ids in parse_qs(parsed.query).get("ids", [])
        for item in raw_ids.split("|")
    }
    entity_data_path = f"/Special:EntityData/{entity_id}.json"
    if (
        host not in {"wikidata.org", "www.wikidata.org"}
        or _ENTITY_ID.fullmatch(entity_id) is None
        or (entity_id not in entity_ids and not parsed.path.endswith(entity_data_path))
    ):
        raise ProvenanceError("Wikidata entity revision identity is invalid")
    return {"kind": kind, "revision_id": revision_id, "entity_id": entity_id}


def _clean_relation(
    value: object,
    *,
    records: dict[str, dict[str, Any]],
    raw_texts: dict[str, str] | None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceError("document relation must be an object")
    relation_id = str(value.get("relation_id") or "").strip()
    kind = str(value.get("kind") or "")
    source_id = str(value.get("source_record_id") or "")
    target_id = str(value.get("target_record_id") or "")
    evidence_id = str(value.get("evidence_record_id") or "")
    quote = str(value.get("evidence_quote") or "")
    start = value.get("evidence_char_start")
    if (
        not relation_id
        or source_id == target_id
        or source_id not in records
        or target_id not in records
        or evidence_id != source_id
        or not quote
        or not isinstance(start, int)
        or start < 0
    ):
        raise ProvenanceError("document relation identity is invalid")
    source_text = records[evidence_id]["text"]
    clean_start = start
    if raw_texts is not None:
        raw_text = raw_texts[evidence_id]
        if raw_text[start : start + len(quote)] != quote:
            raise ProvenanceError(
                "document relation evidence span does not match source"
            )
        if _EMAIL.search(quote):
            raise ProvenanceError("document relation evidence contains email PII")
        _reject_secrets(quote)
        clean_start = len(_EMAIL.sub("[redacted-email]", raw_text[:start]))
    if source_text[clean_start : clean_start + len(quote)] != quote:
        raise ProvenanceError("document relation evidence span does not match source")
    expected_source_hash = records[evidence_id]["source_sha256"]
    if raw_texts is None and value.get("source_sha256") != expected_source_hash:
        raise ProvenanceError("document relation source hash is invalid")
    return {
        "relation_id": relation_id,
        "kind": kind,
        "source_record_id": source_id,
        "target_record_id": target_id,
        "evidence_record_id": evidence_id,
        "evidence_quote": quote,
        "evidence_char_start": clean_start,
        "source_sha256": expected_source_hash,
    }


def _validate_paper_relation(
    relation: dict[str, Any], records: dict[str, dict[str, Any]]
) -> None:
    kind = relation["kind"]
    expected_roles = _PAPER_RELATION_ROLES.get(kind)
    source = records[relation["source_record_id"]]
    target = records[relation["target_record_id"]]
    if expected_roles is None or (source["role"], target["role"]) != expected_roles:
        raise ProvenanceError(f"paper {kind} relation has invalid record roles")
    if source["work_id"] != target["work_id"]:
        raise ProvenanceError(f"paper {kind} relation crosses unrelated works")
    quote = relation["evidence_quote"]
    if not _contains_identity(quote, target["revision_id"]):
        raise ProvenanceError(f"paper {kind} relation evidence omits target identity")
    if kind == "revision_of" and _parse_timestamp(
        source["occurred_at"], "record occurred_at"
    ) <= _parse_timestamp(target["occurred_at"], "record occurred_at"):
        raise ProvenanceError("paper revision relation is not chronological")
    if kind in {"reviews", "responds_to", "reproduces_result"} and _parse_timestamp(
        source["occurred_at"], "record occurred_at"
    ) <= _parse_timestamp(target["occurred_at"], "record occurred_at"):
        raise ProvenanceError(f"paper {kind} relation is not chronological")


def _validate_wikipedia_relation(
    relation: dict[str, Any], records: dict[str, dict[str, Any]]
) -> None:
    kind = relation["kind"]
    source = records[relation["source_record_id"]]
    target = records[relation["target_record_id"]]
    quote = relation["evidence_quote"]
    if kind == "revision_of":
        valid = (
            source["kind"] == target["kind"] == "wikipedia_revision"
            and source["page_id"] == target["page_id"]
            and source["parent_revision_id"] == target["revision_id"]
            and _contains_identity(quote, target["revision_id"])
        )
        if not valid:
            raise ProvenanceError("Wikipedia parent revision relation is invalid")
    elif kind == "page_describes_entity":
        valid = (
            source["kind"] == "wikipedia_revision"
            and target["kind"] == "wikidata_entity_revision"
            and _contains_identity(quote, target["entity_id"])
        )
        if not valid:
            raise ProvenanceError("Wikipedia entity relation evidence is invalid")
    elif kind == "entity_resolves_page":
        valid = (
            source["kind"] == "wikidata_entity_revision"
            and target["kind"] == "wikipedia_revision"
            and _contains_identity(quote, target["title"])
        )
        if not valid:
            raise ProvenanceError("Wikidata page relation evidence is invalid")
    else:
        raise ProvenanceError("unsupported Wikimedia relation kind")


def _manifest_header(
    *,
    schema_version: str,
    source_status: str,
    generated_at: str,
    authorization: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "source_status": source_status,
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "generated_at": generated_at,
        "authorization": authorization,
    }


def _validate_wikimedia_fetch_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "generated_at",
        "policy_url",
        "user_agent_sha256",
        "language",
        "title_resolutions",
    }:
        raise ProvenanceError("Wikimedia public API fetch receipt is invalid")
    generated_at = str(value.get("generated_at") or "")
    _parse_timestamp(generated_at, "Wikimedia fetch generated_at")
    language = str(value.get("language") or "")
    user_agent_sha256 = str(value.get("user_agent_sha256") or "")
    resolutions = value.get("title_resolutions")
    if (
        value.get("schema_version") != WIKIMEDIA_FETCH_RECEIPT_SCHEMA
        or value.get("policy_url") != WIKIMEDIA_USER_AGENT_POLICY
        or re.fullmatch(r"[a-z][a-z0-9-]{1,11}", language) is None
        or _SHA256.fullmatch(user_agent_sha256) is None
        or not isinstance(resolutions, list)
        or not resolutions
    ):
        raise ProvenanceError("Wikimedia public API fetch receipt is invalid")
    cleaned: list[dict[str, str]] = []
    requested: set[str] = set()
    resolved: set[str] = set()
    for item in resolutions:
        if not isinstance(item, dict) or set(item) != {
            "requested_title",
            "resolved_title",
        }:
            raise ProvenanceError("Wikimedia title resolution receipt is invalid")
        source = str(item.get("requested_title") or "").strip()
        target = str(item.get("resolved_title") or "").strip()
        if not source or not target or source in requested or target in resolved:
            raise ProvenanceError("Wikimedia title resolution receipt is invalid")
        if source != target:
            raise ProvenanceError("Wikimedia title resolution must be exact")
        requested.add(source)
        resolved.add(target)
        cleaned.append({"requested_title": source, "resolved_title": target})
    return {
        "schema_version": WIKIMEDIA_FETCH_RECEIPT_SCHEMA,
        "generated_at": generated_at,
        "policy_url": WIKIMEDIA_USER_AGENT_POLICY,
        "user_agent_sha256": user_agent_sha256,
        "language": language,
        "title_resolutions": cleaned,
    }


def _validate_public_wikimedia_records(payload: dict[str, Any]) -> None:
    receipt = _validate_wikimedia_fetch_receipt(payload.get("fetch_receipt"))
    language = receipt["language"]
    resolved_titles = {item["resolved_title"] for item in receipt["title_resolutions"]}
    page_titles: set[str] = set()
    entity_payloads: list[dict[str, Any]] = []
    for record in payload.get("records") or []:
        if not isinstance(record, dict) or not str(
            record.get("source_file") or ""
        ).endswith(".json"):
            raise ProvenanceError("Wikimedia public API source must be JSON")
        try:
            raw = json.loads(str(record.get("text") or ""))
        except json.JSONDecodeError as error:
            raise ProvenanceError(
                "Wikimedia public API source is invalid JSON"
            ) from error
        if not isinstance(raw, dict):
            raise ProvenanceError("Wikimedia public API source is invalid JSON")
        kind = record.get("kind")
        retrieval = urlparse(str(record.get("retrieval_url") or ""))
        retrieval_query = parse_qs(retrieval.query)
        if (
            retrieval.scheme != "https"
            or retrieval.path != "/w/api.php"
            or retrieval.fragment
            or retrieval.params
        ):
            raise ProvenanceError("Wikimedia public API retrieval URL is invalid")
        if kind == "wikipedia_revision":
            if record.get("parser") != "mediawiki_revision_api@1":
                raise ProvenanceError("Wikipedia public API parser is invalid")
            query = raw.get("query")
            pages = query.get("pages") if isinstance(query, dict) else None
            if (
                not isinstance(pages, list)
                or len(pages) != 1
                or not isinstance(pages[0], dict)
            ):
                raise ProvenanceError("Wikipedia public API page is invalid")
            page = pages[0]
            revisions = page.get("revisions")
            revision = (
                revisions[0]
                if isinstance(revisions, list) and len(revisions) == 1
                else None
            )
            title = str(record.get("title") or "")
            expected_retrieval_query = {
                "action": ["query"],
                "prop": ["revisions|pageprops"],
                "revids": [str(record.get("revision_id"))],
                "rvprop": ["ids|timestamp|content"],
                "rvslots": ["main"],
                "format": ["json"],
                "formatversion": ["2"],
            }
            if (
                not isinstance(revision, dict)
                or page.get("pageid") != record.get("page_id")
                or page.get("title") != title
                or revision.get("revid") != record.get("revision_id")
                or (revision.get("parentid") or None)
                != record.get("parent_revision_id")
                or revision.get("timestamp") != record.get("occurred_at")
                or (urlparse(str(record.get("source_url") or "")).hostname or "")
                != f"{language}.wikipedia.org"
                or (retrieval.hostname or "") != f"{language}.wikipedia.org"
                or retrieval.netloc != f"{language}.wikipedia.org"
                or retrieval_query != expected_retrieval_query
            ):
                raise ProvenanceError(
                    "Wikipedia public API identity or retrieval URL is invalid"
                )
            page_titles.add(title)
        elif kind == "wikidata_entity_revision":
            if record.get("parser") != "wikibase_entity_api@1":
                raise ProvenanceError("Wikidata public API parser is invalid")
            entities = raw.get("entities")
            entity_id = str(record.get("entity_id") or "")
            entity = entities.get(entity_id) if isinstance(entities, dict) else None
            expected_retrieval_query = {
                "action": ["wbgetentities"],
                "ids": [entity_id],
                "props": ["info|labels|sitelinks"],
                "languages": [language],
                "sitefilter": [f"{language}wiki"],
                "format": ["json"],
            }
            if (
                not isinstance(entity, dict)
                or entity.get("id") != entity_id
                or entity.get("lastrevid") != record.get("revision_id")
                or entity.get("modified") != record.get("occurred_at")
                or (retrieval.hostname or "") != "www.wikidata.org"
                or retrieval.netloc != "www.wikidata.org"
                or retrieval_query != expected_retrieval_query
                or record.get("retrieval_url") != record.get("source_url")
            ):
                raise ProvenanceError(
                    "Wikidata public API identity or retrieval URL is invalid"
                )
            entity_payloads.append(entity)
        else:
            raise ProvenanceError("Wikimedia public API record kind is invalid")
    if page_titles != resolved_titles:
        raise ProvenanceError("Wikimedia fetch receipt does not bind resolved titles")
    for entity in entity_payloads:
        sitelinks = entity.get("sitelinks")
        sitelink = (
            sitelinks.get(f"{language}wiki") if isinstance(sitelinks, dict) else None
        )
        if not isinstance(sitelink, dict) or sitelink.get("title") not in page_titles:
            raise ProvenanceError("Wikidata sitelink does not bind a resolved page")


def _build_manifest(
    input_payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    input_schema: str,
    manifest_schema: str,
    identity_builder: Callable[[dict[str, Any]], dict[str, Any]],
    required_relations: set[str],
    relation_validator: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None],
) -> dict[str, Any]:
    if input_payload.get("schema_version") != input_schema:
        raise ProvenanceError("unsupported document workflow input schema")
    source_status = str(input_payload.get("source_status") or "")
    if source_status not in _SOURCE_STATUSES:
        raise ProvenanceError("document input requires an explicit source status")
    authorization = _validate_authorization(input_payload.get("authorization"))
    _parse_timestamp(generated_at, "generated_at")
    raw_records = input_payload.get("records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or len(raw_records) > MAX_DOCUMENT_RECORDS
    ):
        raise ProvenanceError("document workflow has an invalid record count")

    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    raw_texts: dict[str, str] = {}
    hashes: set[str] = set()
    for value in raw_records:
        if not isinstance(value, dict):
            raise ProvenanceError("document workflow record must be an object")
        record, raw_text = _source_record(
            value, base_directory, identity=identity_builder(value)
        )
        record_id = record["record_id"]
        source_hash = record["source_sha256"]
        if record_id in by_id or source_hash in hashes:
            raise ProvenanceError("document workflow record id or source is duplicated")
        records.append(record)
        by_id[record_id] = record
        raw_texts[record_id] = raw_text
        hashes.add(source_hash)

    raw_relations = input_payload.get("relations")
    if (
        not isinstance(raw_relations, list)
        or not raw_relations
        or len(raw_relations) > MAX_DOCUMENT_RELATIONS
    ):
        raise ProvenanceError("document workflow has an invalid relation count")
    relations: list[dict[str, Any]] = []
    relation_ids: set[str] = set()
    relation_edges: set[tuple[str, str, str]] = set()
    for value in raw_relations:
        relation = _clean_relation(value, records=by_id, raw_texts=raw_texts)
        if relation["relation_id"] in relation_ids:
            raise ProvenanceError("document workflow relation id is duplicated")
        edge = (
            relation["kind"],
            relation["source_record_id"],
            relation["target_record_id"],
        )
        if edge in relation_edges:
            raise ProvenanceError("document workflow relation edge is duplicated")
        relation_validator(relation, by_id)
        relations.append(relation)
        relation_ids.add(relation["relation_id"])
        relation_edges.add(edge)
    if {relation["kind"] for relation in relations} != required_relations:
        raise ProvenanceError("document workflow is missing required source relations")
    connected = {
        record_id
        for relation in relations
        for record_id in (
            relation["source_record_id"],
            relation["target_record_id"],
        )
    }
    if connected != set(by_id):
        raise ProvenanceError(
            "document workflow contains a record not connected by relations"
        )

    manifest = {
        **_manifest_header(
            schema_version=manifest_schema,
            source_status=source_status,
            generated_at=generated_at,
            authorization=authorization,
        ),
        "n": len(records),
        "n_relations": len(relations),
        "records": records,
        "relations": relations,
    }
    if (
        len(json.dumps(manifest, ensure_ascii=False).encode())
        > MAX_DOCUMENT_MANIFEST_BYTES
    ):
        raise ProvenanceError("document workflow manifest exceeds size limit")
    return manifest


def build_paper_workflow_manifest(
    input_payload: dict[str, Any], base_directory: Path, *, generated_at: str
) -> dict[str, Any]:
    """Build an unsigned paper revision/review/benchmark source inventory."""
    return _build_manifest(
        input_payload,
        base_directory,
        generated_at=generated_at,
        input_schema=PAPER_WORKFLOW_INPUT_SCHEMA,
        manifest_schema=PAPER_WORKFLOW_MANIFEST_SCHEMA,
        identity_builder=_paper_record_identity,
        required_relations=set(_PAPER_RELATION_ROLES),
        relation_validator=_validate_paper_relation,
    )


def _validate_paper_fetch_header(
    payload: dict[str, Any], base_directory: Path
) -> tuple[dict[str, Any], dict[tuple[str, str], tuple[dict[str, Any], bytes]]]:
    if set(payload) != _PAPER_FETCH_TOP_LEVEL_FIELDS or (
        payload.get("schema_version") != PAPER_FETCH_INVENTORY_SCHEMA
        or payload.get("source_status") != "public_api_export"
        or payload.get("data_stage") != "source_inventory"
        or payload.get("hybrid_train_ready") is not False
        or payload.get("production_eligible") is not False
        or payload.get("generation_integration") != "disabled"
        or payload.get("semantic_facts_train_ready") is not False
    ):
        raise ProvenanceError(
            "paper fetch inventory schema or disabled flags are invalid"
        )
    _parse_timestamp(str(payload.get("generated_at") or ""), "paper fetch generated_at")
    request_sha256 = str(payload.get("request_sha256") or "")
    if _SHA256.fullmatch(request_sha256) is None:
        raise ProvenanceError("paper fetch request hash is invalid")
    request_file = str(payload.get("request_file") or "")
    _request_path, request_raw = _fetch_file_bytes(
        base_directory, request_file, request_sha256, label="request"
    )
    try:
        request_payload = json.loads(request_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("paper fetch request file is invalid") from error
    if not isinstance(request_payload, dict):
        raise ProvenanceError("paper fetch request file is invalid")
    authorization = payload.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("paper fetch authorization is invalid")
    cleaned_authorization: dict[str, Any] = _validate_authorization(authorization)
    actions = authorization.get("allowed_actions")
    allowed_actions = {
        "fetch_arxiv_metadata",
        "fetch_arxiv_source",
        "fetch_openreview_forum",
    }
    if (
        not isinstance(actions, list)
        or not actions
        or actions != sorted(actions)
        or len(set(actions)) != len(actions)
        or not set(actions).issubset(allowed_actions)
    ):
        raise ProvenanceError("paper fetch authorization actions are invalid")
    cleaned_authorization["allowed_actions"] = list(actions)
    receipt = payload.get("fetch_receipt")
    if not isinstance(receipt, dict) or set(receipt) != _PAPER_FETCH_RECEIPT_FIELDS:
        raise ProvenanceError("paper fetch receipt schema is invalid")
    rate = receipt.get("requests_per_second")
    retries = receipt.get("max_retries")
    fetch_source = receipt.get("fetch_arxiv_source")
    if (
        receipt.get("arxiv_policy_url") != ARXIV_API_MANUAL
        or receipt.get("openreview_api_definition_url") != OPENREVIEW_API_DEFINITION
        or receipt.get("request_sha256") != request_sha256
        or receipt.get("request_file") != request_file
        or receipt.get("allowed_actions") != actions
        or _SHA256.fullmatch(str(receipt.get("user_agent_sha256") or "")) is None
        or isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= 3
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
        or not isinstance(fetch_source, bool)
    ):
        raise ProvenanceError("paper fetch request or receipt metadata is invalid")
    raw_retrievals = receipt.get("retrievals")
    if not isinstance(raw_retrievals, list) or not raw_retrievals:
        raise ProvenanceError("paper fetch receipt has no retrievals")
    retrievals: dict[tuple[str, str], tuple[dict[str, Any], bytes]] = {}
    for raw_retrieval in raw_retrievals:
        retrieval, raw = _validate_fetch_retrieval(raw_retrieval, base_directory)
        key = (retrieval["source_family"], retrieval["retrieval_file"])
        if key in retrievals:
            raise ProvenanceError("paper fetch retrieval is duplicated")
        retrievals[key] = (retrieval, raw)
    families = {key[0] for key in retrievals}
    required_actions = set()
    if "arxiv_atom_api" in families:
        required_actions.add("fetch_arxiv_metadata")
    if "arxiv_source_archive" in families:
        required_actions.add("fetch_arxiv_source")
    if "openreview_v2_api" in families:
        required_actions.add("fetch_openreview_forum")
    if required_actions != set(actions) or fetch_source != (
        "arxiv_source_archive" in families
    ):
        raise ProvenanceError("paper fetch actions do not bind retrieval families")
    if set(request_payload) != {
        "schema_version",
        "user_agent",
        "authorization",
        "arxiv_version_ids",
        "openreview_forum_ids",
        "fetch_arxiv_source",
        "requests_per_second",
        "max_retries",
    }:
        raise ProvenanceError("paper fetch request file schema is invalid")
    arxiv_ids = sorted(
        parse_qs(urlparse(retrieval[0]["requested_url"]).query)["id_list"][0]
        for key, retrieval in retrievals.items()
        if key[0] == "arxiv_atom_api"
    )
    forum_ids = sorted(
        parse_qs(urlparse(retrieval[0]["requested_url"]).query)["forum"][0]
        for key, retrieval in retrievals.items()
        if key[0] == "openreview_v2_api"
    )
    user_agent = str(request_payload.get("user_agent") or "")
    if (
        request_payload.get("schema_version") != "longworld.paper-fetch-request.v1"
        or request_payload.get("authorization") != authorization
        or sorted(request_payload.get("arxiv_version_ids") or []) != arxiv_ids
        or sorted(request_payload.get("openreview_forum_ids") or []) != forum_ids
        or request_payload.get("fetch_arxiv_source") != fetch_source
        or float(request_payload.get("requests_per_second") or 0) != float(rate)
        or request_payload.get("max_retries") != retries
        or hashlib.sha256(user_agent.encode()).hexdigest()
        != receipt.get("user_agent_sha256")
    ):
        raise ProvenanceError("paper fetch request file metadata is invalid")
    cleaned_receipt = dict(receipt)
    cleaned_receipt["retrievals"] = [retrievals[key][0] for key in sorted(retrievals)]
    return {
        "authorization": cleaned_authorization,
        "fetch_receipt": cleaned_receipt,
        "request_file": request_file,
        "request_sha256": request_sha256,
    }, retrievals


def _record_retrieval(
    record: dict[str, Any],
    retrievals: dict[tuple[str, str], tuple[dict[str, Any], bytes]],
    *,
    source_family: str,
) -> tuple[dict[str, Any], bytes]:
    key = (source_family, str(record.get("retrieval_file") or ""))
    bound = retrievals.get(key)
    if bound is None:
        raise ProvenanceError("paper record has no exact fetch retrieval")
    retrieval, raw = bound
    if {
        "requested_url": record.get("retrieval_requested_url"),
        "final_url": record.get("retrieval_url"),
        "status": record.get("retrieval_status"),
        "content_type": record.get("retrieval_content_type"),
        "redirect_chain": record.get("retrieval_redirect_chain"),
        "sha256": record.get("retrieval_sha256"),
    } != {
        key: retrieval[key]
        for key in (
            "requested_url",
            "final_url",
            "status",
            "content_type",
            "redirect_chain",
            "sha256",
        )
    }:
        raise ProvenanceError("paper record final retrieval metadata is invalid")
    return retrieval, raw


def _fetch_facts(value: object, source_text: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ProvenanceError("paper fetch record facts are invalid")
    facts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "fact_id",
            "field",
            "value",
            "evidence_quote",
            "evidence_char_start",
        }:
            raise ProvenanceError("paper fetch record fact is invalid")
        start = item.get("evidence_char_start")
        quote = str(item.get("evidence_quote") or "")
        if (
            not isinstance(start, int)
            or start < 0
            or source_text[start : start + len(quote)] != quote
            or source_text.count(quote) != 1
        ):
            raise ProvenanceError("paper fetch record fact span is invalid")
        facts.append(dict(item))
    return facts


def _arxiv_source_payload(
    record: dict[str, Any],
    source_payload: dict[str, Any],
    retrieval: dict[str, Any],
    retrieval_raw: bytes,
    *,
    request_sha256: str,
    archive: tuple[dict[str, Any], bytes] | None,
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    arxiv_id = str(record.get("record_id") or "").removeprefix("arxiv:")
    match = _ARXIV_ID.fullmatch(arxiv_id)
    if match is None or record.get("revision_id") != match.group("version"):
        raise ProvenanceError("paper arXiv record identity is invalid")
    try:
        root = ET.fromstring(retrieval_raw)
    except ET.ParseError as error:
        raise ProvenanceError("paper arXiv retrieval XML is invalid") from error
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if len(entries) != 1:
        raise ProvenanceError("paper arXiv retrieval entry count is invalid")
    entry = entries[0]

    def atom_text(name: str) -> str:
        value = entry.findtext(f"{{http://www.w3.org/2005/Atom}}{name}")
        return " ".join(str(value or "").split())

    authors = [
        " ".join(str(author.text or "").split())
        for author in entry.findall(
            "{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name"
        )
    ]
    version_number = int(match.group("version").removeprefix("v"))
    expected: dict[str, Any] = {
        "authors": authors,
        "entry_id": f"https://arxiv.org/abs/{arxiv_id}",
        "kind": "arxiv_api_entry",
        "published": atom_text("published"),
        "raw_response_sha256": retrieval["sha256"],
        "request_sha256": request_sha256,
        "retrieval": {
            key: retrieval[key]
            for key in (
                "requested_url",
                "final_url",
                "status",
                "content_type",
                "redirect_chain",
                "sha256",
            )
        },
        "summary": atom_text("summary"),
        "title": atom_text("title"),
        "updated": atom_text("updated"),
        "version": f"v{version_number}",
    }
    entry_id = atom_text("id")
    if entry_id.rstrip("/").rsplit("/", 1)[-1] != arxiv_id:
        raise ProvenanceError("paper arXiv retrieval identity is invalid")
    if version_number > 1:
        expected["previous_revision_id"] = f"v{version_number - 1}"
    latex_sources: dict[str, str] | None = None
    archive_metadata: dict[str, Any] | None = None
    if archive is not None:
        archive_retrieval, archive_raw = archive
        archive_metadata = {
            key: archive_retrieval[key]
            for key in (
                "requested_url",
                "final_url",
                "status",
                "content_type",
                "redirect_chain",
                "sha256",
                "parser",
            )
        }
        archive_sources = _latex_sources_from_archive(archive_raw)
        expected["latex_sources"] = archive_sources
        expected["source_archive"] = archive_metadata
        latex_sources = _validated_latex_sources(archive_sources)
    if source_payload != expected:
        raise ProvenanceError("paper arXiv canonical source record is invalid")
    return latex_sources, archive_metadata


def _openreview_role(record: dict[str, Any]) -> str:
    invitation = str(record.get("invitation") or "")
    replyto = record.get("replyto")
    note_id = str(record.get("revision_id") or "")
    forum_id = str(record.get("forum_id") or "")
    suffix = invitation.rsplit("/", 1)[-1].casefold()
    if note_id == forum_id and replyto is None and suffix == "submission":
        return "manuscript_revision"
    if replyto == forum_id and suffix == "official_review":
        return "peer_review"
    if isinstance(replyto, str) and replyto != forum_id and suffix == "author_response":
        return "author_response"
    raise ProvenanceError("paper OpenReview invitation has no safe workflow role")


def _openreview_occurred_at(note: dict[str, Any]) -> str:
    raw = next(
        (note[field] for field in ("pdate", "cdate", "mdate") if note.get(field)),
        None,
    )
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1_000_000_000_000:
        raise ProvenanceError("paper OpenReview timestamp is invalid")
    try:
        return (
            datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError) as error:
        raise ProvenanceError("paper OpenReview timestamp is invalid") from error


def _convert_openreview_fetch_record(
    value: dict[str, Any],
    *,
    base_directory: Path,
    header: dict[str, Any],
    retrievals: dict[tuple[str, str], tuple[dict[str, Any], bytes]],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    expected_fields = {
        "record_id",
        "kind",
        "source_family",
        "source_origin",
        "work_id",
        "revision_id",
        "forum_id",
        "replyto",
        "invitation",
        "occurred_at",
        "linked_arxiv_ids",
        "source_url",
        "facts",
        "source_file",
        "source_sha256",
        "request_sha256",
        "retrieval_file",
        "retrieval_sha256",
        "retrieval_requested_url",
        "retrieval_url",
        "retrieval_status",
        "retrieval_content_type",
        "retrieval_redirect_chain",
        "retrieved_at",
        "parser",
    }
    if set(value) != expected_fields:
        raise ProvenanceError("paper fetch OpenReview record schema is invalid")
    if (
        value.get("source_family") != "openreview_v2_api"
        or value.get("source_origin") != "public_api"
        or value.get("request_sha256") != header["request_sha256"]
        or value.get("parser") != "openreview_note_v2"
    ):
        raise ProvenanceError("paper fetch OpenReview lineage is invalid")
    retrieval, retrieval_raw = _record_retrieval(
        value, retrievals, source_family="openreview_v2_api"
    )
    source_path, source_raw = _fetch_file_bytes(
        base_directory,
        value.get("source_file"),
        value.get("source_sha256"),
        label="source record",
    )
    try:
        source_payload = json.loads(source_raw.decode("utf-8"))
        retrieval_payload = json.loads(retrieval_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("paper OpenReview source JSON is invalid") from error
    notes = (
        retrieval_payload.get("notes") if isinstance(retrieval_payload, dict) else None
    )
    note_id = str(value.get("revision_id") or "")
    matches = (
        [note for note in notes if isinstance(note, dict) and note.get("id") == note_id]
        if isinstance(notes, list)
        else []
    )
    if len(matches) != 1:
        raise ProvenanceError("paper OpenReview retrieval note identity is invalid")
    note = matches[0]
    raw_content = note.get("content")
    if not isinstance(raw_content, dict):
        raise ProvenanceError("paper OpenReview content is invalid")
    content: dict[str, str] = {}
    for field, item in raw_content.items():
        raw_value = item.get("value") if isinstance(item, dict) else item
        if isinstance(field, str) and isinstance(raw_value, str) and raw_value.strip():
            content[field] = raw_value.strip()
    expected_source = {
        "content": content,
        "forum": str(note.get("forum") or ""),
        "id": note_id,
        "invitation": str(note.get("invitation") or ""),
        "kind": "openreview_api_note",
        "occurred_at": _openreview_occurred_at(note),
        "raw_response_sha256": retrieval["sha256"],
        "replyto": str(note["replyto"]) if note.get("replyto") else None,
        "request_sha256": header["request_sha256"],
        "retrieval": {
            key: retrieval[key]
            for key in (
                "requested_url",
                "final_url",
                "status",
                "content_type",
                "redirect_chain",
                "sha256",
            )
        },
    }
    if source_payload != expected_source:
        raise ProvenanceError("paper OpenReview canonical source record is invalid")
    linked_arxiv_ids = sorted(
        {
            match.group("id")
            for text in content.values()
            for match in _ARXIV_LINK.finditer(text)
        }
    )
    if (
        value.get("record_id") != f"openreview:{note_id}"
        or value.get("forum_id") != expected_source["forum"]
        or value.get("work_id") != f"openreview:{expected_source['forum']}"
        or value.get("replyto") != expected_source["replyto"]
        or value.get("invitation") != expected_source["invitation"]
        or value.get("occurred_at") != expected_source["occurred_at"]
        or value.get("linked_arxiv_ids") != linked_arxiv_ids
    ):
        raise ProvenanceError("paper OpenReview record metadata is invalid")
    role = _openreview_role(value)
    source_text = source_raw.decode("utf-8")
    facts = _fetch_facts(value.get("facts"), source_text)
    if {fact["field"]: fact["value"] for fact in facts} != content:
        raise ProvenanceError("paper OpenReview fact values are invalid")
    workflow_record = {
        "record_id": value["record_id"],
        "work_id": value["work_id"],
        "role": role,
        "source_family": "openreview_note",
        "source_url": value["source_url"],
        "source_file": source_path.name,
        "source_sha256": value["source_sha256"],
        "revision_id": value["revision_id"],
        "occurred_at": value["occurred_at"],
        "retrieved_at": value["retrieved_at"],
        "access_policy": header["authorization"]["basis"],
        "parser": {"name": value["parser"], "version": "consumer-v2"},
        "derived_facts": facts,
    }
    metadata = {
        "request_sha256": header["request_sha256"],
        "retrieval": {
            **{
                key: retrieval[key]
                for key in (
                    "requested_url",
                    "final_url",
                    "status",
                    "content_type",
                    "redirect_chain",
                    "sha256",
                )
            },
            "file": retrieval["retrieval_file"],
            "parser": value["parser"],
            "retrieved_at": value["retrieved_at"],
        },
    }
    return workflow_record, metadata, source_text


def build_paper_workflow_from_fetch_inventory(
    input_payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    fetch_inventory_sha256: str,
) -> dict[str, Any]:
    """Consume a disabled hardened fetch inventory into an attested-source payload."""
    if _SHA256.fullmatch(fetch_inventory_sha256) is None:
        raise ProvenanceError("paper fetch inventory hash is invalid")
    header, retrievals = _validate_paper_fetch_header(input_payload, base_directory)
    raw_records = input_payload.get("records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or input_payload.get("n_records") != len(raw_records)
        or len(raw_records) > MAX_DOCUMENT_RECORDS
    ):
        raise ProvenanceError("paper fetch inventory record count is invalid")
    workflow_records: list[dict[str, Any]] = []
    record_metadata: dict[str, dict[str, Any]] = {}
    source_payloads: dict[str, dict[str, Any]] = {}
    latex_by_record: dict[str, dict[str, str]] = {}
    openreview_source_texts: dict[str, str] = {}
    used_retrievals: set[tuple[str, str]] = set()
    record_ids: set[str] = set()
    for value in raw_records:
        if not isinstance(value, dict):
            raise ProvenanceError("paper fetch record is invalid")
        kind = str(value.get("kind") or "")
        if kind == "openreview":
            workflow_record, metadata, source_text = _convert_openreview_fetch_record(
                value,
                base_directory=base_directory,
                header=header,
                retrievals=retrievals,
            )
            record_id = workflow_record["record_id"]
            if record_id in record_ids:
                raise ProvenanceError("paper fetch record id is duplicated")
            record_ids.add(record_id)
            if value.get("retrieved_at") != input_payload.get("generated_at"):
                raise ProvenanceError(
                    "paper fetch OpenReview retrieval time is invalid"
                )
            workflow_records.append(workflow_record)
            record_metadata[record_id] = metadata
            source_payloads[record_id] = json.loads(source_text)
            openreview_source_texts[record_id] = source_text
            used_retrievals.add(
                ("openreview_v2_api", str(value.get("retrieval_file") or ""))
            )
            continue
        if kind != "arxiv":
            raise ProvenanceError("paper fetch record kind is invalid")
        common_fields = {
            "record_id",
            "kind",
            "source_family",
            "source_origin",
            "work_id",
            "revision_id",
            "previous_revision_id",
            "occurred_at",
            "source_url",
            "facts",
            "source_file",
            "source_sha256",
            "request_sha256",
            "retrieval_file",
            "retrieval_sha256",
            "retrieval_requested_url",
            "retrieval_url",
            "retrieval_status",
            "retrieval_content_type",
            "retrieval_redirect_chain",
            "retrieved_at",
            "parser",
        }
        archive_fields = {
            "source_archive_file",
            "source_archive_sha256",
            "source_archive_requested_url",
            "source_archive_url",
            "source_archive_status",
            "source_archive_content_type",
            "source_archive_redirect_chain",
            "source_archive_parser",
            "latex_char_count",
        }
        has_archive = "source_archive_file" in value
        if set(value) != common_fields | (archive_fields if has_archive else set()):
            raise ProvenanceError("paper fetch arXiv record schema is invalid")
        record_id = str(value.get("record_id") or "")
        if record_id in record_ids:
            raise ProvenanceError("paper fetch record id is duplicated")
        record_ids.add(record_id)
        if (
            value.get("source_family") != "arxiv_atom_api"
            or value.get("source_origin") != "public_api"
            or value.get("request_sha256") != header["request_sha256"]
            or value.get("retrieved_at") != input_payload.get("generated_at")
            or value.get("parser") != "arxiv_atom_v1"
        ):
            raise ProvenanceError("paper fetch arXiv record lineage is invalid")
        retrieval, retrieval_raw = _record_retrieval(
            value, retrievals, source_family="arxiv_atom_api"
        )
        retrieval_key = ("arxiv_atom_api", retrieval["retrieval_file"])
        used_retrievals.add(retrieval_key)
        source_path, source_raw = _fetch_file_bytes(
            base_directory,
            value.get("source_file"),
            value.get("source_sha256"),
            label="source record",
        )
        try:
            source_payload = json.loads(source_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProvenanceError(
                "paper fetch source record is invalid JSON"
            ) from error
        if not isinstance(source_payload, dict):
            raise ProvenanceError("paper fetch source record is invalid")
        archive: tuple[dict[str, Any], bytes] | None = None
        if has_archive:
            archive_key = (
                "arxiv_source_archive",
                str(value.get("source_archive_file") or ""),
            )
            archive = retrievals.get(archive_key)
            if archive is None:
                raise ProvenanceError("paper record has no exact source archive")
            archive_retrieval, archive_raw = archive
            if {
                "requested_url": value.get("source_archive_requested_url"),
                "final_url": value.get("source_archive_url"),
                "status": value.get("source_archive_status"),
                "content_type": value.get("source_archive_content_type"),
                "redirect_chain": value.get("source_archive_redirect_chain"),
                "sha256": value.get("source_archive_sha256"),
                "parser": value.get("source_archive_parser"),
            } != {
                key: archive_retrieval[key]
                for key in (
                    "requested_url",
                    "final_url",
                    "status",
                    "content_type",
                    "redirect_chain",
                    "sha256",
                    "parser",
                )
            }:
                raise ProvenanceError("paper record source archive metadata is invalid")
            if len(archive_raw) > MAX_PAPER_FETCH_FILE_BYTES or hashlib.sha256(
                archive_raw
            ).hexdigest() != value.get("source_archive_sha256"):
                raise ProvenanceError("paper fetch source archive hash mismatch")
            used_retrievals.add(archive_key)
        latex_sources, archive_metadata = _arxiv_source_payload(
            value,
            source_payload,
            retrieval,
            retrieval_raw,
            request_sha256=header["request_sha256"],
            archive=archive,
        )
        source_text = source_raw.decode("utf-8")
        facts = _fetch_facts(value.get("facts"), source_text)
        if {fact["field"] for fact in facts} != {"title", "summary"}:
            raise ProvenanceError("paper fetch arXiv facts are incomplete")
        facts_by_field = {fact["field"]: fact for fact in facts}
        if any(
            facts_by_field[field]["value"] != source_payload.get(field)
            for field in ("title", "summary")
        ):
            raise ProvenanceError("paper fetch arXiv fact value is invalid")
        arxiv_id = record_id.removeprefix("arxiv:")
        if (
            value.get("work_id")
            != f"arxiv:{arxiv_id.removesuffix(str(value.get('revision_id') or ''))}"
            or value.get("source_url") != f"https://arxiv.org/abs/{arxiv_id}"
            or value.get("occurred_at") != source_payload.get("updated")
        ):
            raise ProvenanceError("paper fetch arXiv record identity is invalid")
        if value.get("previous_revision_id") != source_payload.get(
            "previous_revision_id"
        ):
            raise ProvenanceError("paper previous revision metadata is invalid")
        workflow_records.append(
            {
                "record_id": record_id,
                "work_id": value["work_id"],
                "role": "manuscript_revision",
                "source_family": "arxiv_record",
                "source_url": value["source_url"],
                "source_file": source_path.name,
                "source_sha256": value["source_sha256"],
                "revision_id": value["revision_id"],
                "occurred_at": value["occurred_at"],
                "retrieved_at": value["retrieved_at"],
                "access_policy": header["authorization"]["basis"],
                "parser": {"name": value["parser"], "version": "consumer-v2"},
                "derived_facts": facts,
            }
        )
        source_payloads[record_id] = source_payload
        if latex_sources is not None:
            latex_by_record[record_id] = latex_sources
            if value.get("latex_char_count") != sum(
                len(text) for text in latex_sources.values()
            ):
                raise ProvenanceError("paper LaTeX character count is invalid")
        metadata = {
            "request_sha256": header["request_sha256"],
            "retrieval": {
                **{
                    key: retrieval[key]
                    for key in (
                        "requested_url",
                        "final_url",
                        "status",
                        "content_type",
                        "redirect_chain",
                        "sha256",
                    )
                },
                "file": retrieval["retrieval_file"],
                "parser": value["parser"],
                "retrieved_at": value["retrieved_at"],
            },
        }
        if archive_metadata is not None:
            metadata["source_archive"] = {
                **archive_metadata,
                "file": value["source_archive_file"],
            }
        record_metadata[record_id] = metadata

    if used_retrievals != set(retrievals):
        raise ProvenanceError("paper fetch receipt contains an unused retrieval")
    by_work_revision = {
        (record["work_id"], record["revision_id"]): record
        for record in workflow_records
    }
    relations: list[dict[str, Any]] = []
    latex_revision_works: set[str] = set()
    revision_fact_works: set[str] = set()
    for record in workflow_records:
        if record["source_family"] != "arxiv_record":
            continue
        source_payload = source_payloads[record["record_id"]]
        previous_revision = source_payload.get("previous_revision_id")
        if previous_revision is None:
            continue
        target = by_work_revision.get((record["work_id"], previous_revision))
        if target is None:
            raise ProvenanceError("paper previous revision is missing from inventory")
        quote = f'"previous_revision_id": {json.dumps(previous_revision)}'
        source_text = (base_directory / record["source_file"]).read_text(
            encoding="utf-8"
        )
        if source_text.count(quote) != 1:
            raise ProvenanceError("paper previous revision source binding is invalid")
        relations.append(
            {
                "relation_id": f"{record['record_id']}:revision-of",
                "kind": "revision_of",
                "source_record_id": record["record_id"],
                "target_record_id": target["record_id"],
                "evidence_record_id": record["record_id"],
                "evidence_quote": quote,
                "evidence_char_start": source_text.index(quote),
            }
        )
        if record["record_id"] in latex_by_record:
            if target["record_id"] not in latex_by_record:
                raise ProvenanceError("paper previous revision has no LaTeX source")
            latex_revision_works.add(record["work_id"])
            revision_fact = _revision_added_text_fact(
                latex_by_record[target["record_id"]],
                latex_by_record[record["record_id"]],
                current_record_text=source_text,
            )
            if revision_fact is not None:
                record["derived_facts"].append(revision_fact)
                revision_fact_works.add(record["work_id"])
    if latex_revision_works - revision_fact_works:
        raise ProvenanceError("paper revision has no reliable new semantic LaTeX body")
    records_by_id = {record["record_id"]: record for record in workflow_records}
    for record_id, source_text in openreview_source_texts.items():
        source = records_by_id[record_id]
        if source["role"] == "manuscript_revision":
            continue
        replyto = source_payloads[record_id].get("replyto")
        target = records_by_id.get(f"openreview:{replyto}")
        if target is None:
            raise ProvenanceError("paper OpenReview reply target is missing")
        kind = "reviews" if source["role"] == "peer_review" else "responds_to"
        quote = f'"replyto": {json.dumps(replyto)}'
        if source_text.count(quote) != 1:
            raise ProvenanceError("paper OpenReview reply relation is not exact-span")
        relations.append(
            {
                "relation_id": f"{record_id}:{kind}",
                "kind": kind,
                "source_record_id": record_id,
                "target_record_id": target["record_id"],
                "evidence_record_id": record_id,
                "evidence_quote": quote,
                "evidence_char_start": source_text.index(quote),
            }
        )
    if not relations:
        raise ProvenanceError("paper fetch inventory has no derivable relation")
    workflow_input = {
        "schema_version": PAPER_WORKFLOW_INPUT_SCHEMA,
        "source_status": "public_api_export",
        "authorization": header["authorization"],
        "records": workflow_records,
        "relations": relations,
    }
    manifest = _build_manifest(
        workflow_input,
        base_directory,
        generated_at=generated_at,
        input_schema=PAPER_WORKFLOW_INPUT_SCHEMA,
        manifest_schema=PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA,
        identity_builder=_paper_record_identity,
        required_relations={relation["kind"] for relation in relations},
        relation_validator=_validate_paper_relation,
    )
    for record in manifest["records"]:
        record.update(record_metadata[record["record_id"]])
    manifest.update(
        {
            "authorization": header["authorization"],
            "fetch_inventory_sha256": fetch_inventory_sha256,
            "request_file": header["request_file"],
            "request_sha256": header["request_sha256"],
            "fetch_receipt": header["fetch_receipt"],
        }
    )
    return manifest


def build_wikipedia_workflow_manifest(
    input_payload: dict[str, Any], base_directory: Path, *, generated_at: str
) -> dict[str, Any]:
    """Build an unsigned Wikipedia revision/page/Wikidata source inventory."""
    manifest = _build_manifest(
        input_payload,
        base_directory,
        generated_at=generated_at,
        input_schema=WIKIPEDIA_WORKFLOW_INPUT_SCHEMA,
        manifest_schema=WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA,
        identity_builder=_wikipedia_record_identity,
        required_relations=set(_WIKIMEDIA_RELATION_KINDS),
        relation_validator=_validate_wikipedia_relation,
    )
    if input_payload.get("source_status") == "public_api_export":
        manifest["fetch_receipt"] = _validate_wikimedia_fetch_receipt(
            input_payload.get("fetch_receipt")
        )
        _validate_public_wikimedia_records(manifest)
    elif "fetch_receipt" in input_payload:
        raise ProvenanceError("non-public Wikimedia input has a fetch receipt")
    return manifest


def _audit_manifest(
    payload: dict[str, Any],
    *,
    schema_version: str,
    identity_builder: Callable[[dict[str, Any]], dict[str, Any]],
    required_relations: set[str],
    relation_validator: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None],
) -> None:
    if payload.get("schema_version") != schema_version:
        raise ProvenanceError("unsupported document workflow manifest schema")
    if (
        payload.get("source_status") not in _SOURCE_STATUSES
        or payload.get("data_stage") != "source_inventory"
        or payload.get("hybrid_train_ready") is not False
        or payload.get("production_eligible") is not False
        or payload.get("generation_integration") != "disabled"
    ):
        raise ProvenanceError("document workflow manifest overstates readiness")
    _validate_authorization(payload.get("authorization"))
    _parse_timestamp(str(payload.get("generated_at") or ""), "generated_at")
    raw_records = payload.get("records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or len(raw_records) > MAX_DOCUMENT_RECORDS
        or payload.get("n") != len(raw_records)
    ):
        raise ProvenanceError("document workflow manifest has invalid records")
    records: dict[str, dict[str, Any]] = {}
    hashes: set[str] = set()
    for record in raw_records:
        if not isinstance(record, dict):
            raise ProvenanceError("document workflow manifest record is invalid")
        identity_builder(record)
        record_id = str(record.get("record_id") or "")
        source_hash = str(record.get("source_sha256") or "")
        text = record.get("text")
        parser_name, separator, parser_version = str(
            record.get("parser") or ""
        ).partition("@")
        source_file = record.get("source_file")
        if (
            not record_id
            or record_id in records
            or _SHA256.fullmatch(source_hash) is None
            or source_hash in hashes
            or record.get("provenance_id") != f"sha256:{source_hash}"
            or not isinstance(text, str)
            or hashlib.sha256(text.encode()).hexdigest() != record.get("text_sha256")
            or not separator
            or not parser_name
            or not parser_version
            or not isinstance(source_file, str)
            or Path(source_file).name != source_file
            or Path(source_file).suffix.lower() not in {".json", ".md", ".txt"}
            or not str(record.get("access_policy") or "").strip()
        ):
            raise ProvenanceError(
                "document workflow manifest source binding is invalid"
            )
        _parse_timestamp(str(record.get("occurred_at") or ""), "record occurred_at")
        _parse_timestamp(str(record.get("retrieved_at") or ""), "record retrieved_at")
        privacy = record.get("privacy_review")
        if not isinstance(privacy, dict) or privacy != {
            "emails": "redacted",
            "email_redaction_count": privacy.get("email_redaction_count"),
            "secrets": "fail_closed",
            "scanner": DOCUMENT_WORKFLOW_SCANNER,
            "scanner_revision": DOCUMENT_WORKFLOW_SCANNER_REVISION,
        }:
            raise ProvenanceError("document workflow privacy review is invalid")
        if (
            not isinstance(privacy["email_redaction_count"], int)
            or privacy["email_redaction_count"] < 0
            or _EMAIL.search(text)
        ):
            raise ProvenanceError("document workflow privacy review is invalid")
        if (
            privacy["email_redaction_count"] == 0
            and source_hash != record["text_sha256"]
        ):
            raise ProvenanceError(
                "document workflow manifest source binding is invalid"
            )
        _reject_secrets(text)
        if "derived_facts" in record:
            _audit_derived_facts(
                record["derived_facts"], text=text, text_sha256=record["text_sha256"]
            )
        records[record_id] = record
        hashes.add(source_hash)

    raw_relations = payload.get("relations")
    if (
        not isinstance(raw_relations, list)
        or not raw_relations
        or len(raw_relations) > MAX_DOCUMENT_RELATIONS
        or payload.get("n_relations") != len(raw_relations)
    ):
        raise ProvenanceError("document workflow manifest has invalid relations")
    relation_ids: set[str] = set()
    relation_edges: set[tuple[str, str, str]] = set()
    relations: list[dict[str, Any]] = []
    for value in raw_relations:
        relation = _clean_relation(value, records=records, raw_texts=None)
        if relation["relation_id"] in relation_ids:
            raise ProvenanceError("document workflow relation id is duplicated")
        edge = (
            relation["kind"],
            relation["source_record_id"],
            relation["target_record_id"],
        )
        if edge in relation_edges:
            raise ProvenanceError("document workflow relation edge is duplicated")
        if (
            relation.get("source_sha256")
            != records[relation["evidence_record_id"]]["source_sha256"]
        ):
            raise ProvenanceError("document relation source hash is invalid")
        relation_validator(relation, records)
        relation_ids.add(relation["relation_id"])
        relation_edges.add(edge)
        relations.append(relation)
    if {relation["kind"] for relation in relations} != required_relations:
        raise ProvenanceError("document workflow is missing required source relations")
    connected = {
        record_id
        for relation in relations
        for record_id in (
            relation["source_record_id"],
            relation["target_record_id"],
        )
    }
    if connected != set(records):
        raise ProvenanceError(
            "document workflow contains a record not connected by relations"
        )


def _load_manifest(
    path: Path,
    *,
    attestation_key: bytes | None,
    schema_version: str,
    identity_builder: Callable[[dict[str, Any]], dict[str, Any]],
    required_relations: set[str],
    relation_validator: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None],
) -> dict[str, Any]:
    try:
        raw = _read_regular_file(path, MAX_DOCUMENT_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read document workflow manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProvenanceError("document workflow manifest must be an object")
    if not verify_attestation(
        payload,
        attestation_key or attestation_key_from_env("source_manifest"),
        purpose="source_manifest",
    ):
        raise ProvenanceError(
            "document workflow manifest has no valid source attestation"
        )
    _audit_manifest(
        payload,
        schema_version=schema_version,
        identity_builder=identity_builder,
        required_relations=required_relations,
        relation_validator=relation_validator,
    )
    return payload


def _audit_fetched_paper_manifest(payload: dict[str, Any]) -> None:
    request_sha256 = str(payload.get("request_sha256") or "")
    if (
        _SHA256.fullmatch(str(payload.get("fetch_inventory_sha256") or "")) is None
        or _SHA256.fullmatch(request_sha256) is None
        or not isinstance(payload.get("request_file"), str)
        or Path(payload["request_file"]).name != payload["request_file"]
    ):
        raise ProvenanceError("paper fetch manifest binding is invalid")
    receipt = payload.get("fetch_receipt")
    if (
        not isinstance(receipt, dict)
        or set(receipt) != _PAPER_FETCH_RECEIPT_FIELDS
        or receipt.get("request_sha256") != request_sha256
        or receipt.get("request_file") != payload.get("request_file")
    ):
        raise ProvenanceError("paper fetch manifest receipt is invalid")
    authorization = payload.get("authorization")
    if not isinstance(authorization, dict) or authorization.get(
        "allowed_actions"
    ) != receipt.get("allowed_actions"):
        raise ProvenanceError("paper fetch manifest authorization is invalid")
    for retrieval in receipt.get("retrievals") or []:
        if not isinstance(retrieval, dict):
            raise ProvenanceError("paper fetch manifest retrieval is invalid")
        expected = set(_PAPER_FETCH_RETRIEVAL_FIELDS)
        if retrieval.get("source_family") == "arxiv_source_archive":
            expected.add("parser")
        if set(retrieval) != expected:
            raise ProvenanceError("paper fetch manifest retrieval is invalid")
    latex_revision_works: set[str] = set()
    revision_fact_works: set[str] = set()
    for record in payload.get("records") or []:
        if (
            not isinstance(record, dict)
            or record.get("request_sha256") != request_sha256
        ):
            raise ProvenanceError("paper fetch manifest record binding is invalid")
        retrieval = record.get("retrieval")
        if not isinstance(retrieval, dict) or set(retrieval) != {
            "requested_url",
            "final_url",
            "status",
            "content_type",
            "redirect_chain",
            "sha256",
            "file",
            "parser",
            "retrieved_at",
        }:
            raise ProvenanceError("paper fetch manifest record retrieval is invalid")
        try:
            source_payload = json.loads(record["text"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ProvenanceError(
                "paper fetch manifest source text is invalid"
            ) from error
        if (
            not isinstance(source_payload, dict)
            or source_payload.get("request_sha256") != request_sha256
            or source_payload.get("retrieval")
            != {
                key: retrieval[key]
                for key in (
                    "requested_url",
                    "final_url",
                    "status",
                    "content_type",
                    "redirect_chain",
                    "sha256",
                )
            }
        ):
            raise ProvenanceError("paper fetch manifest source lineage is invalid")
        archive = record.get("source_archive")
        if archive is not None:
            if not isinstance(archive, dict) or set(archive) != {
                "requested_url",
                "final_url",
                "status",
                "content_type",
                "redirect_chain",
                "sha256",
                "parser",
                "file",
            }:
                raise ProvenanceError("paper fetch manifest archive is invalid")
            if source_payload.get("source_archive") != {
                key: archive[key]
                for key in (
                    "requested_url",
                    "final_url",
                    "status",
                    "content_type",
                    "redirect_chain",
                    "sha256",
                    "parser",
                )
            }:
                raise ProvenanceError("paper fetch manifest archive lineage is invalid")
        if source_payload.get("previous_revision_id") is not None and isinstance(
            source_payload.get("latex_sources"), list
        ):
            latex_revision_works.add(str(record.get("work_id") or ""))
        if any(
            fact.get("field") == "revision_added_text"
            for fact in record.get("derived_facts") or []
            if isinstance(fact, dict)
        ):
            revision_fact_works.add(str(record.get("work_id") or ""))
    if revision_fact_works - latex_revision_works:
        raise ProvenanceError("paper fetch manifest revision-added fact is misplaced")
    if latex_revision_works - revision_fact_works:
        raise ProvenanceError("paper fetch manifest lacks revision-added body fact")


def load_paper_workflow_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Load and verify a source-attested scholarly workflow inventory."""
    try:
        unsigned_probe = json.loads(
            _read_regular_file(path, MAX_DOCUMENT_MANIFEST_BYTES).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read document workflow manifest: {error}"
        ) from error
    if not isinstance(unsigned_probe, dict):
        raise ProvenanceError("document workflow manifest must be an object")
    schema = unsigned_probe.get("schema_version")
    if schema == PAPER_WORKFLOW_MANIFEST_SCHEMA:
        required_relations = set(_PAPER_RELATION_ROLES)
    elif schema == PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA:
        raw_relations = unsigned_probe.get("relations")
        if not isinstance(raw_relations, list) or not raw_relations:
            raise ProvenanceError("paper fetch manifest has no source relations")
        required_relations = {
            str(relation.get("kind") or "")
            for relation in raw_relations
            if isinstance(relation, dict)
        }
        if not required_relations or not required_relations.issubset(
            _PAPER_RELATION_ROLES
        ):
            raise ProvenanceError("paper fetch manifest relation kinds are invalid")
    else:
        raise ProvenanceError("unsupported document workflow manifest schema")
    payload = _load_manifest(
        path,
        attestation_key=attestation_key,
        schema_version=str(schema),
        identity_builder=_paper_record_identity,
        required_relations=required_relations,
        relation_validator=_validate_paper_relation,
    )
    if schema == PAPER_FETCH_WORKFLOW_MANIFEST_SCHEMA:
        _audit_fetched_paper_manifest(payload)
    return payload


def load_wikipedia_workflow_manifest(
    path: Path, *, attestation_key: bytes | None = None
) -> dict[str, Any]:
    """Load and verify a source-attested Wikipedia/Wikidata workflow inventory."""
    payload = _load_manifest(
        path,
        attestation_key=attestation_key,
        schema_version=WIKIPEDIA_WORKFLOW_MANIFEST_SCHEMA,
        identity_builder=_wikipedia_record_identity,
        required_relations=set(_WIKIMEDIA_RELATION_KINDS),
        relation_validator=_validate_wikipedia_relation,
    )
    if payload.get("source_status") == "public_api_export":
        _validate_public_wikimedia_records(payload)
    elif "fetch_receipt" in payload:
        raise ProvenanceError("non-public Wikimedia manifest has a fetch receipt")
    return payload


def _wikimedia_body_fact(
    *,
    key: str,
    record_id: str,
    text: str,
    field: str,
    value: str | int,
) -> WorkflowFact:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    pattern = re.compile(rf'"{re.escape(field)}"\s*:\s*(?P<value>{re.escape(encoded)})')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ProvenanceError(f"Wikimedia source body does not uniquely bind {field}")
    match = matches[0]
    start, end = match.span("value")
    decoded = json.loads(match.group("value"))
    if isinstance(decoded, str):
        start += 1
        end -= 1
    if text[start:end] != str(decoded):
        raise ProvenanceError(f"Wikimedia source body does not losslessly bind {field}")
    return WorkflowFact(
        key=key,
        value=str(decoded),
        record_id=record_id,
        char_start=start,
        char_end=end,
        source_quote=match.group(0),
    )


def _wikimedia_relation_fact(
    relation: dict[str, Any], records: dict[str, dict[str, Any]]
) -> WorkflowFact:
    target = records[relation["target_record_id"]]
    if relation["kind"] == "revision_of":
        value = str(target["revision_id"])
    elif relation["kind"] == "page_describes_entity":
        value = str(target["entity_id"])
    else:
        value = str(target["title"])
    quote = relation["evidence_quote"]
    offset = quote.find(value)
    if offset < 0 or quote.find(value, offset + len(value)) >= 0:
        raise ProvenanceError(
            "Wikimedia relation evidence does not uniquely bind its target"
        )
    start = relation["evidence_char_start"] + offset
    return WorkflowFact(
        key=f"relation:{relation['relation_id']}:target",
        value=value,
        record_id=relation["evidence_record_id"],
        char_start=start,
        char_end=start + len(value),
        source_quote=quote,
    )


def _wikipedia_real_workflow_episode(
    payload: dict[str, Any], *, source_path: Path
) -> RealWorkflow:
    """Convert one verified public Wikimedia manifest into a replay episode."""
    if payload.get("source_status") != "public_api_export":
        raise ProvenanceError(
            "only an authenticated public Wikimedia API export is a real episode"
        )
    raw_records = payload["records"]
    if len(raw_records) > MAX_WORKFLOW_RECORDS:
        raise ProvenanceError("Wikimedia workflow contains too many replay records")
    records_by_id = {record["record_id"]: record for record in raw_records}
    ordered = sorted(
        raw_records,
        key=lambda record: (
            _parse_timestamp(record["occurred_at"], "record occurred_at"),
            record["source_sha256"],
            record["record_id"],
        ),
    )
    order = {record["record_id"]: index for index, record in enumerate(ordered)}
    relations_by_source: dict[str, list[dict[str, Any]]] = {
        record_id: [] for record_id in records_by_id
    }
    neighbors: dict[str, set[str]] = {record_id: set() for record_id in records_by_id}
    for relation in payload["relations"]:
        source_id = relation["source_record_id"]
        target_id = relation["target_record_id"]
        clean_relation = {
            "relation_id": relation["relation_id"],
            "kind": relation["kind"],
            "source_record_id": source_id,
            "target_record_id": target_id,
            "evidence_record_id": relation["evidence_record_id"],
            "evidence_quote": relation["evidence_quote"],
            "evidence_char_start": relation["evidence_char_start"],
            "evidence_char_end": relation["evidence_char_start"]
            + len(relation["evidence_quote"]),
            "source_sha256": relation["source_sha256"],
        }
        relations_by_source[source_id].append(clean_relation)
        neighbors[source_id].add(target_id)
        neighbors[target_id].add(source_id)

    facts: dict[str, WorkflowFact] = {}
    workflow_records: list[WorkflowRecord] = []
    for record in ordered:
        record_id = record["record_id"]
        text = record["text"]
        if len(text) > MAX_WORKFLOW_RECORD_CHARS:
            raise ProvenanceError(f"Wikimedia workflow record {record_id} is too large")
        fact_fields: tuple[tuple[str, str, str | int | None], ...]
        if record["kind"] == "wikipedia_revision":
            fact_fields = (
                ("revision_id", "revid", record["revision_id"]),
                ("page_id", "pageid", record["page_id"]),
                ("title", "title", record["title"]),
                ("parent_revision_id", "parentid", record["parent_revision_id"]),
                ("occurred_at", "timestamp", record["occurred_at"]),
            )
        else:
            fact_fields = (
                ("revision_id", "lastrevid", record["revision_id"]),
                ("entity_id", "id", record["entity_id"]),
                ("occurred_at", "modified", record["occurred_at"]),
            )
        for name, body_field, value in fact_fields:
            if value is None:
                continue
            key = f"record:{record_id}:{name}"
            facts[key] = _wikimedia_body_fact(
                key=key,
                record_id=record_id,
                text=text,
                field=body_field,
                value=value,
            )
        attributes = {
            key: record[key]
            for key in (
                "kind",
                "revision_id",
                "page_id",
                "title",
                "parent_revision_id",
                "entity_id",
                "source_url",
                "retrieval_url",
                "source_sha256",
                "text_sha256",
                "provenance_id",
                "parser",
            )
            if record.get(key) is not None
        }
        attributes["content_license"] = _WIKIMEDIA_CONTENT_LICENSES[record["kind"]]
        attributes["source_relations"] = tuple(
            sorted(
                relations_by_source[record_id],
                key=lambda relation: (
                    relation["kind"],
                    relation["target_record_id"],
                    relation["relation_id"],
                ),
            )
        )
        workflow_records.append(
            WorkflowRecord(
                record_id=record_id,
                kind=record["kind"],
                occurred_at=record["occurred_at"],
                text=text,
                links=tuple(
                    sorted(
                        (
                            related
                            for related in neighbors[record_id]
                            if order[related] < order[record_id]
                        ),
                        key=order.__getitem__,
                    )
                ),
                attributes=attributes,
                source_pointer=f"{source_path}#/records/{raw_records.index(record)}/text",
            )
        )
    for relation in payload["relations"]:
        fact = _wikimedia_relation_fact(relation, records_by_id)
        facts[fact.key] = fact

    signature_records = [
        {
            "kind": record["kind"],
            "occurred_at": record["occurred_at"],
            "source_url": record["source_url"],
            "retrieval_url": record["retrieval_url"],
            "source_sha256": record["source_sha256"],
            "text_sha256": record["text_sha256"],
        }
        for record in ordered
    ]
    signature_relations = [
        {
            "kind": relation["kind"],
            "source_sha256": records_by_id[relation["source_record_id"]][
                "source_sha256"
            ],
            "target_sha256": records_by_id[relation["target_record_id"]][
                "source_sha256"
            ],
            "evidence_source_sha256": relation["source_sha256"],
            "evidence_quote": relation["evidence_quote"],
            "evidence_char_start": relation["evidence_char_start"],
        }
        for relation in payload["relations"]
    ]
    signature = {
        "records": sorted(
            signature_records,
            key=lambda value: json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        ),
        "relations": sorted(
            signature_relations,
            key=lambda value: json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            signature, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    content_licenses = {
        _WIKIMEDIA_CONTENT_LICENSES[str(record["kind"])] for record in ordered
    }
    retrieved_at = max(
        ordered,
        key=lambda record: _parse_timestamp(
            record["retrieved_at"], "record retrieved_at"
        ),
    )["retrieved_at"]
    page_records = [
        record for record in ordered if record["kind"] == "wikipedia_revision"
    ]
    lineage_digest = hashlib.sha256(canonical_attested_payload(payload)).hexdigest()
    return RealWorkflow(
        workflow_id=f"wikimedia:{digest[:24]}",
        source_kind="wikimedia",
        source_origin=SourceOrigin.REAL_PUBLIC,
        lineage=SourceLineage(
            provenance_id=f"sha256:{lineage_digest}",
            url=page_records[-1]["source_url"],
            license="; ".join(sorted(content_licenses)),
            retrieved_at=retrieved_at,
            parser="wikipedia_workflow_manifest@2",
            sha256=lineage_digest,
            revision=",".join(str(record["revision_id"]) for record in ordered),
            source_path=str(source_path),
        ),
        records=tuple(workflow_records),
        facts=facts,
    )


def load_wikipedia_real_workflow_episode(
    path: Path, *, attestation_key: bytes | None = None
) -> RealWorkflow:
    """Verify and adapt a public Wikipedia/Wikidata manifest for deterministic replay."""
    payload = load_wikipedia_workflow_manifest(path, attestation_key=attestation_key)
    return _wikipedia_real_workflow_episode(payload, source_path=path)
