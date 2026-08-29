"""Provenance contract for disabled IETF standards source inventories.

This module validates already-fetched official response bytes. It does not
integrate records into a world or make them train-ready.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from importlib import import_module
from itertools import pairwise
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

_public_scanner = import_module("scripts.export_github_workflow")
PUBLIC_SCANNER = cast(str, _public_scanner.PUBLIC_SCANNER)
PUBLIC_SCANNER_REVISION = cast(str, _public_scanner.PUBLIC_SCANNER_REVISION)
sanitize_public_text = cast(
    Callable[[str], tuple[str, list[str]]], _public_scanner.sanitize_public_text
)
validate_sanitized_public_payload = cast(
    Callable[[dict[str, Any]], None],
    _public_scanner.validate_sanitized_public_payload,
)

IETF_FETCH_INVENTORY_SCHEMA = "longworld.ietf-fetch-inventory.v1"
IETF_WORKFLOW_MANIFEST_SCHEMA = "longworld.ietf-workflow-manifest.v1"
IETF_OPEN_RECORDS_POLICY = "https://www.ietf.org/about/open-records/"
MAX_IETF_RECORDS = 128
MAX_IETF_MANIFEST_BYTES = 32_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DRAFT = re.compile(r"^draft-[a-z0-9]+(?:-[a-z0-9]+)+$")
_DRAFT_ID = re.compile(
    r"(?<![A-Za-z0-9])(?P<name>draft-[a-z0-9]+(?:-[a-z0-9]+)+)-(?P<revision>\d{2})(?![A-Za-z0-9])"
)
_RFC_ID = re.compile(
    r"(?<![A-Za-z0-9])RFC[ \t]+(?P<number>[1-9]\d*)(?![A-Za-z0-9])", re.IGNORECASE
)
_RFC_RELATION_LINE = re.compile(
    r"(?i)^[ \t]*(?P<kind>Updates|Obsoletes):[ \t]*(?P<value>[^\r\n]+)[ \t]*$"
)
_RFC_RELATION_CONTINUATION = re.compile(
    r"(?i)^[ \t]+(?P<value>(?:RFC[ \t]+)?[1-9]\d*"
    r"(?:[ \t]*,[ \t]*(?:RFC[ \t]+)?[1-9]\d*)*[ \t]*,?)[ \t]*$"
)
_RFC_RELATION_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])(?:RFC[ \t]+)?(?P<number>[1-9]\d*)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?)-----.*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")


def _regular_bytes(base: Path, filename: object, digest: object) -> bytes:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ProvenanceError("IETF retrieval file is unsafe")
    expected = str(digest or "")
    if _SHA256.fullmatch(expected) is None:
        raise ProvenanceError("IETF retrieval hash is invalid")
    try:
        raw = _read_regular_file(base / filename, MAX_SOURCE_BYTES)
    except OSError as error:
        raise ProvenanceError(f"cannot read IETF retrieval: {error}") from error
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ProvenanceError("IETF retrieval hash mismatch")
    return raw


def _clean(
    raw: bytes, *, approved_private_key_digests: frozenset[str]
) -> tuple[str, str, int, int, frozenset[str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ProvenanceError("IETF source is not UTF-8") from error
    observed_private_key_digests: set[str] = set()

    def redact_approved_private_key(match: re.Match[str]) -> str:
        digest = hashlib.sha256(match.group(0).encode()).hexdigest()
        if digest not in approved_private_key_digests:
            raise ProvenanceError("IETF private-key test vector is not digest-approved")
        observed_private_key_digests.add(digest)
        return "[redacted-public-standards-private-key-test-vector]"

    without_private_keys, private_key_count = _PRIVATE_KEY_BLOCK.subn(
        redact_approved_private_key, text
    )
    if _PRIVATE_KEY_BEGIN.search(without_private_keys):
        raise ProvenanceError(
            "IETF private-key boundary is incomplete or not digest-approved"
        )
    try:
        clean, redactions = sanitize_public_text(without_private_keys)
    except ValueError as error:
        raise ProvenanceError("IETF source failed the public scanner") from error
    return (
        text,
        clean,
        len(redactions),
        private_key_count,
        frozenset(observed_private_key_digests),
    )


def _fact(
    *,
    fact_id: str,
    field: str,
    value: str,
    raw_text: str,
    clean_text: str,
    pattern: re.Pattern[str],
) -> dict[str, Any]:
    matches = list(pattern.finditer(raw_text))
    if len(matches) != 1:
        raise ProvenanceError(f"IETF source does not uniquely bind {field}")
    quote = matches[0].group(0)
    if _EMAIL.search(quote):
        raise ProvenanceError("IETF fact contains email PII")
    clean_matches = list(re.finditer(re.escape(quote), clean_text))
    if len(clean_matches) != 1:
        raise ProvenanceError(f"IETF cleaned source does not uniquely bind {field}")
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": quote,
        "raw_char_start": matches[0].start(),
        "raw_char_end": matches[0].end(),
        "char_start": clean_matches[0].start(),
        "char_end": clean_matches[0].end(),
    }


def _record(
    *,
    record_id: str,
    kind: str,
    occurred_at: str,
    source_url: str,
    raw: bytes,
    facts: list[dict[str, Any]],
    clean_text: str,
    email_count: int,
    private_key_count: int,
    temporal_semantics: str = "source_event_time",
) -> dict[str, Any]:
    _parse_timestamp(occurred_at, "IETF record occurred_at")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    return {
        "record_id": record_id,
        "kind": kind,
        "occurred_at": occurred_at,
        "temporal_semantics": temporal_semantics,
        "source_url": source_url,
        "retrieval_url": source_url,
        "source_family": "ietf_standards",
        "source_origin": "real_public",
        "source_sha256": source_sha256,
        "text_sha256": hashlib.sha256(clean_text.encode()).hexdigest(),
        "provenance_id": f"sha256:{source_sha256}",
        "text": clean_text,
        "facts": facts,
        "privacy_review": {
            "emails": "redacted",
            "email_redaction_count": email_count,
            "private_key_redaction_count": private_key_count,
            "secrets": (
                "redacted_then_scanned" if private_key_count else "fail_closed"
            ),
            "private_key_redaction_policy": "exact_sha256_allowlist",
            "scanner": PUBLIC_SCANNER,
            "scanner_revision": PUBLIC_SCANNER_REVISION,
        },
    }


def _text_timestamp(text: str) -> str:
    header = "\n".join(text.splitlines()[:80])
    full = re.search(r"(?<!\w)(\d{1,2} [A-Z][a-z]+ \d{4})(?!\w)", header)
    month = re.search(r"(?<!\w)([A-Z][a-z]+ \d{4})(?!\w)", header)
    selected = full or month
    if selected is None:
        raise ProvenanceError("IETF source publication time is missing")
    try:
        parsed = datetime.strptime(
            selected.group(1), "%d %B %Y" if full else "%B %Y"
        ).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ProvenanceError("IETF source publication time is missing") from error
    return parsed.isoformat().replace("+00:00", "Z")


def _rfc_relation_headers(text: str) -> list[tuple[str, list[int], str]]:
    lines = text.splitlines()[:80]
    headers: list[tuple[str, list[int], str]] = []
    index = 0
    while index < len(lines):
        match = _RFC_RELATION_LINE.fullmatch(lines[index])
        if match is None:
            index += 1
            continue
        kind = match.group("kind").lower()
        quote_lines = [lines[index]]
        values = [match.group("value")]
        cursor = index + 1
        while cursor < len(lines):
            continuation = _RFC_RELATION_CONTINUATION.fullmatch(lines[cursor])
            if continuation is None:
                break
            quote_lines.append(lines[cursor])
            values.append(continuation.group("value"))
            cursor += 1
        combined = "\n".join(values)
        targets = [
            int(number.group("number"))
            for number in _RFC_RELATION_NUMBER.finditer(combined)
        ]
        if not targets or values[-1].rstrip().endswith(","):
            raise ProvenanceError("RFC relation header is incomplete")
        headers.append((kind, targets, "\n".join(quote_lines)))
        index = cursor
    return headers


def _validate_rfc_target_closure(
    *, requested: set[int], published: set[int], referenced: set[int]
) -> None:
    if not referenced.issubset(requested) or published | referenced != requested:
        raise ProvenanceError("requested RFC relation target is not grounded")


def _evidence(record: dict[str, Any], fact_id: str) -> dict[str, Any]:
    fact = next((item for item in record["facts"] if item["fact_id"] == fact_id), None)
    if fact is None:
        raise ProvenanceError("IETF relation evidence fact is missing")
    return {
        "record_id": record["record_id"],
        "fact_ids": [fact_id],
        "evidence_quote": fact["evidence_quote"],
        "char_start": fact["char_start"],
        "char_end": fact["char_end"],
        "source_sha256": record["source_sha256"],
    }


def _relation(
    kind: str,
    source: dict[str, Any],
    target: dict[str, Any],
    source_fact: str | list[str],
    target_fact: str | list[str],
    *,
    supporting_evidence: list[tuple[dict[str, Any], str]] | None = None,
) -> dict[str, Any]:
    source_facts = [source_fact] if isinstance(source_fact, str) else source_fact
    target_facts = [target_fact] if isinstance(target_fact, str) else target_fact
    return {
        "relation_id": f"ietf:{kind}:{source['record_id']}:{target['record_id']}",
        "kind": kind,
        "source_record_id": source["record_id"],
        "target_record_id": target["record_id"],
        "evidence": [
            *(_evidence(source, fact_id) for fact_id in source_facts),
            *(_evidence(target, fact_id) for fact_id in target_facts),
            *(
                _evidence(record, fact_id)
                for record, fact_id in supporting_evidence or []
            ),
        ],
    }


def _validate_bound_request(value: object) -> dict[str, Any]:
    fields = {
        "schema_version",
        "user_agent",
        "authorization",
        "drafts",
        "rfc_numbers",
        "approved_public_test_vector_sha256",
        "requests_per_second",
        "max_retries",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema_version") != "longworld.ietf-fetch-request.v1"
    ):
        raise ProvenanceError("IETF fetch request schema is invalid")
    authorization = value.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "record_id",
        "scope",
        "basis",
        "reviewed_at",
        "allowed_actions",
    }:
        raise ProvenanceError("IETF fetch authorization is invalid")
    if any(
        not str(authorization.get(field) or "").strip()
        for field in ("record_id", "scope", "basis", "reviewed_at")
    ):
        raise ProvenanceError("IETF fetch authorization is invalid")
    _parse_timestamp(
        str(authorization["reviewed_at"]), "IETF authorization reviewed_at"
    )
    expected_actions = [
        "fetch_datatracker_document",
        "fetch_datatracker_relation",
        "fetch_draft_revision",
        "fetch_rfc",
    ]
    drafts = value.get("drafts")
    numbers = value.get("rfc_numbers")
    rate = value.get("requests_per_second")
    retries = value.get("max_retries")
    approved_test_vectors = value.get("approved_public_test_vector_sha256")
    if (
        authorization.get("allowed_actions") != expected_actions
        or not isinstance(drafts, list)
        or not drafts
        or not isinstance(numbers, list)
        or not numbers
        or numbers != sorted(numbers)
        or len(set(numbers)) != len(numbers)
        or any(
            isinstance(number, bool) or not isinstance(number, int) or number < 1
            for number in numbers
        )
        or isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= 3
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
        or not isinstance(approved_test_vectors, list)
        or len(approved_test_vectors) > 128
        or len(set(approved_test_vectors)) != len(approved_test_vectors)
        or any(
            not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
            for digest in approved_test_vectors
        )
    ):
        raise ProvenanceError("IETF fetch request controls are invalid")
    names: set[str] = set()
    for draft in drafts:
        if not isinstance(draft, dict) or set(draft) != {"name", "revisions"}:
            raise ProvenanceError("IETF fetch request draft is invalid")
        name = str(draft.get("name") or "")
        revisions = draft.get("revisions")
        if (
            _DRAFT.fullmatch(name) is None
            or name in names
            or not isinstance(revisions, list)
            or not revisions
            or any(not isinstance(revision, str) for revision in revisions)
            or any(re.fullmatch(r"\d{2}", revision) is None for revision in revisions)
            or revisions != sorted(revisions, key=int)
            or len(set(revisions)) != len(revisions)
            or any(
                int(current) != int(prior) + 1 for prior, current in pairwise(revisions)
            )
        ):
            raise ProvenanceError("IETF fetch request draft identity is invalid")
        names.add(name)
    return value


def _validate_retrieval(item: object, base: Path) -> tuple[dict[str, Any], bytes]:
    fields = {
        "kind",
        "requested_url",
        "final_url",
        "status",
        "content_type",
        "redirect_chain",
        "observed_at",
        "sha256",
        "retrieval_file",
    }
    if not isinstance(item, dict) or set(item) != fields:
        raise ProvenanceError("IETF retrieval receipt is invalid")
    url = str(item.get("requested_url") or "")
    parsed = urlparse(url)
    kind = str(item.get("kind") or "")
    valid = False
    if kind == "datatracker_document":
        valid = (
            parsed.hostname == "datatracker.ietf.org"
            and re.fullmatch(
                r"/api/v1/doc/document/draft-[a-z0-9]+(?:-[a-z0-9]+)+/", parsed.path
            )
            is not None
            and item.get("content_type") == "application/json"
        )
    elif kind == "datatracker_relation":
        query = parse_qs(parsed.query)
        valid = (
            parsed.hostname == "datatracker.ietf.org"
            and parsed.path == "/api/v1/doc/relateddocument/"
            and set(query) == {"source__name", "limit"}
            and query.get("limit") == ["100"]
            and len(query.get("source__name", [])) == 1
            and _DRAFT.fullmatch(query["source__name"][0]) is not None
            and item.get("content_type") == "application/json"
        )
    elif kind == "draft_revision":
        valid = (
            parsed.hostname == "www.ietf.org"
            and re.fullmatch(
                r"/archive/id/draft-[a-z0-9]+(?:-[a-z0-9]+)+-\d{2}\.txt", parsed.path
            )
            is not None
            and item.get("content_type") == "text/plain"
        )
    elif kind == "rfc":
        valid = (
            parsed.hostname == "www.rfc-editor.org"
            and re.fullmatch(r"/rfc/rfc[1-9]\d*\.txt", parsed.path) is not None
            and item.get("content_type") == "text/plain"
        )
    if (
        parsed.scheme != "https"
        or parsed.netloc != parsed.hostname
        or (parsed.query and kind != "datatracker_relation")
        or parsed.fragment
        or parsed.params
        or not valid
        or item.get("final_url") != url
        or item.get("status") != 200
        or item.get("redirect_chain") != []
    ):
        raise ProvenanceError("IETF retrieval URL or transport metadata is invalid")
    _parse_timestamp(str(item.get("observed_at") or ""), "IETF retrieval observed_at")
    return dict(item), _regular_bytes(
        base, item.get("retrieval_file"), item.get("sha256")
    )


def build_ietf_workflow_from_fetch_inventory(
    payload: dict[str, Any],
    base_directory: Path,
    *,
    generated_at: str,
    fetch_inventory_sha256: str,
) -> dict[str, Any]:
    """Validate official bytes and derive a disabled standards source graph."""
    required = {
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
        "n_retrievals",
    }
    if set(payload) != required or (
        payload.get("schema_version") != IETF_FETCH_INVENTORY_SCHEMA
        or payload.get("source_status") != "public_api_export"
        or payload.get("data_stage") != "source_inventory"
        or payload.get("hybrid_train_ready") is not False
        or payload.get("production_eligible") is not False
        or payload.get("generation_integration") != "disabled"
        or payload.get("semantic_facts_train_ready") is not False
    ):
        raise ProvenanceError(
            "IETF fetch inventory schema or disabled flags are invalid"
        )
    fetch_started = _parse_timestamp(
        str(payload.get("generated_at") or ""), "IETF fetch generated_at"
    )
    export_time = _parse_timestamp(generated_at, "generated_at")
    if _SHA256.fullmatch(fetch_inventory_sha256) is None:
        raise ProvenanceError("IETF fetch inventory hash is invalid")
    request_raw = _regular_bytes(
        base_directory, payload.get("request_file"), payload.get("request_sha256")
    )
    try:
        request = _validate_bound_request(json.loads(request_raw.decode()))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("IETF fetch request is invalid") from error
    receipt = payload.get("fetch_receipt")
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "policy_url",
            "started_at",
            "completed_at",
            "requests_per_second",
            "max_retries",
            "allowed_actions",
            "request_file",
            "request_sha256",
            "user_agent_sha256",
            "retrievals",
        }
        or receipt.get("policy_url") != IETF_OPEN_RECORDS_POLICY
    ):
        raise ProvenanceError("IETF fetch receipt is invalid")
    if (
        receipt.get("request_file") != payload.get("request_file")
        or receipt.get("request_sha256") != payload.get("request_sha256")
        or receipt.get("allowed_actions")
        != payload.get("authorization", {}).get("allowed_actions")
        or hashlib.sha256(str(request.get("user_agent") or "").encode()).hexdigest()
        != receipt.get("user_agent_sha256")
        or request.get("authorization") != payload.get("authorization")
        or request.get("requests_per_second") != receipt.get("requests_per_second")
        or request.get("max_retries") != receipt.get("max_retries")
        or receipt.get("started_at") != payload.get("generated_at")
    ):
        raise ProvenanceError("IETF request and receipt bindings are invalid")
    raw_retrievals = receipt.get("retrievals")
    if (
        not isinstance(raw_retrievals, list)
        or len(raw_retrievals) != payload.get("n_retrievals")
        or not raw_retrievals
    ):
        raise ProvenanceError("IETF retrieval count is invalid")
    retrievals = [_validate_retrieval(item, base_directory) for item in raw_retrievals]
    completed_time = _parse_timestamp(
        str(receipt.get("completed_at") or ""), "IETF fetch completed_at"
    )
    observed_times = [
        _parse_timestamp(item[0]["observed_at"], "IETF retrieval observed_at")
        for item in retrievals
    ]
    if not (
        fetch_started < observed_times[0]
        and all(prior < current for prior, current in pairwise(observed_times))
        and observed_times[-1] < completed_time <= export_time
    ):
        raise ProvenanceError("IETF retrieval observation timeline is invalid")
    expected_urls = (
        {
            f"https://datatracker.ietf.org/api/v1/doc/document/{draft['name']}/"
            for draft in request.get("drafts", [])
        }
        | {
            "https://datatracker.ietf.org/api/v1/doc/relateddocument/"
            f"?source__name={draft['name']}&limit=100"
            for draft in request.get("drafts", [])
        }
        | {
            f"https://www.ietf.org/archive/id/{draft['name']}-{revision}.txt"
            for draft in request.get("drafts", [])
            for revision in draft.get("revisions", [])
        }
        | {
            f"https://www.rfc-editor.org/rfc/rfc{number}.txt"
            for number in request.get("rfc_numbers", [])
        }
    )
    if {item[0]["requested_url"] for item in retrievals} != expected_urls:
        raise ProvenanceError("IETF retrievals do not exactly match the request")

    records: list[dict[str, Any]] = []
    supporting_records: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, Any]] = {}
    publication_edges: dict[str, tuple[dict[str, Any], int]] = {}
    drafts: dict[tuple[str, str], dict[str, Any]] = {}
    rfcs: dict[int, dict[str, Any]] = {}
    rfc_relation_targets: dict[int, list[tuple[str, int]]] = {}
    approved_private_key_digests = frozenset(
        request["approved_public_test_vector_sha256"]
    )
    observed_private_key_digests: set[str] = set()
    for retrieval, raw in retrievals:
        (
            raw_text,
            clean_text,
            email_count,
            private_key_count,
            private_key_digests,
        ) = _clean(raw, approved_private_key_digests=approved_private_key_digests)
        observed_private_key_digests.update(private_key_digests)
        url = retrieval["requested_url"]
        if retrieval["kind"] == "datatracker_document":
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as error:
                raise ProvenanceError(
                    "Datatracker document response is invalid JSON"
                ) from error
            name = str(data.get("name") or "")
            path_name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            revision = str(data.get("rev") or "")
            if (
                name != path_name
                or _DRAFT.fullmatch(name) is None
                or re.fullmatch(r"\d{2}", revision) is None
            ):
                raise ProvenanceError("Datatracker document identity is invalid")
            time_value = str(data.get("time") or "")
            _parse_timestamp(time_value, "Datatracker document time")
            name_fact = _fact(
                fact_id="draft_name",
                field="draft_name",
                value=name,
                raw_text=raw_text,
                clean_text=clean_text,
                pattern=re.compile(rf'"name"\s*:\s*"{re.escape(name)}"'),
            )
            record = _record(
                record_id=f"ietf:datatracker:{name}",
                kind="datatracker_document",
                occurred_at=time_value,
                source_url=url,
                raw=raw,
                facts=[name_fact],
                clean_text=clean_text,
                email_count=email_count,
                private_key_count=private_key_count,
            )
            record.update(
                {
                    "draft_name": name,
                    "revision": revision,
                }
            )
            metadata[name] = record
        elif retrieval["kind"] == "datatracker_relation":
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as error:
                raise ProvenanceError(
                    "Datatracker relation response is invalid JSON"
                ) from error
            objects = data.get("objects") if isinstance(data, dict) else None
            name = parse_qs(urlparse(url).query)["source__name"][0]
            became = [
                item
                for item in objects or []
                if isinstance(item, dict)
                and item.get("relationship")
                == "/api/v1/name/docrelationshipname/became_rfc/"
                and item.get("source") == f"/api/v1/doc/document/{name}/"
                and re.fullmatch(
                    r"/api/v1/doc/document/rfc[1-9]\d*/", str(item.get("target") or "")
                )
            ]
            if len(became) != 1:
                raise ProvenanceError("Datatracker became_rfc relation is not unique")
            number = int(
                str(became[0]["target"]).removesuffix("/").rsplit("rfc", 1)[-1]
            )
            source_value = f"/api/v1/doc/document/{name}/"
            target_value = f"/api/v1/doc/document/rfc{number}/"
            edge_pattern = re.compile(
                rf'\{{(?=[^{{}}]*"relationship"\s*:\s*"/api/v1/name/docrelationshipname/became_rfc/")'
                rf'(?=[^{{}}]*"source"\s*:\s*"{re.escape(source_value)}")'
                rf'(?=[^{{}}]*"target"\s*:\s*"{re.escape(target_value)}")[^{{}}]*\}}'
            )
            edge_fact = _fact(
                fact_id="became_rfc",
                field="became_rfc",
                value=f"{name}->RFC {number}",
                raw_text=raw_text,
                clean_text=clean_text,
                pattern=edge_pattern,
            )
            metadata_record = metadata.get(name)
            if metadata_record is None:
                raise ProvenanceError("Datatracker relation has no document metadata")
            record = _record(
                record_id=f"ietf:datatracker-relation:{name}:rfc{number}",
                kind="datatracker_relation",
                occurred_at=retrieval["observed_at"],
                source_url=url,
                raw=raw,
                facts=[edge_fact],
                clean_text=clean_text,
                email_count=email_count,
                private_key_count=private_key_count,
                temporal_semantics="retrieval_observation_only",
            )
            record.update({"draft_name": name, "rfc_number": number})
            publication_edges[name] = (record, number)
        elif retrieval["kind"] == "draft_revision":
            match = _DRAFT_ID.search(raw_text)
            url_id = Path(urlparse(url).path).stem
            if (
                match is None
                or match.group(0) != url_id
                or raw_text.count(match.group(0)) != 1
            ):
                raise ProvenanceError("IETF draft body does not bind its URL identity")
            name, revision = match.group("name"), match.group("revision")
            identity = match.group(0)
            fact = _fact(
                fact_id="draft_revision",
                field="draft_revision",
                value=identity,
                raw_text=raw_text,
                clean_text=clean_text,
                pattern=re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(identity)}(?![A-Za-z0-9])"
                ),
            )
            record = _record(
                record_id=f"ietf:draft:{identity}",
                kind="draft_revision",
                occurred_at=_text_timestamp(raw_text),
                source_url=url,
                raw=raw,
                facts=[fact],
                clean_text=clean_text,
                email_count=email_count,
                private_key_count=private_key_count,
            )
            record.update({"draft_name": name, "revision": revision})
            drafts[(name, revision)] = record
        else:
            number = int(Path(urlparse(url).path).stem.removeprefix("rfc"))
            identity_pattern = re.compile(
                rf"(?im)^\s*(?:RFC[ \t]+{number}[ \t]*|"
                rf"Request for Comments:[ \t]*{number}(?:[ \t]+.*)?)$"
            )
            matches = list(identity_pattern.finditer(raw_text))
            if len(matches) != 1:
                raise ProvenanceError(
                    "RFC body does not uniquely bind its URL identity"
                )
            fact = _fact(
                fact_id="rfc_number",
                field="rfc_number",
                value=str(number),
                raw_text=raw_text,
                clean_text=clean_text,
                pattern=identity_pattern,
            )
            relation_facts: list[dict[str, Any]] = []
            targets: list[tuple[str, int]] = []
            relation_identities: set[tuple[str, int]] = set()
            for kind, header_targets, evidence_quote in _rfc_relation_headers(raw_text):
                for target in header_targets:
                    relation_identity = (kind, target)
                    if relation_identity in relation_identities:
                        raise ProvenanceError("RFC relation target is duplicated")
                    relation_identities.add(relation_identity)
                    relation_facts.append(
                        _fact(
                            fact_id=f"{kind}:{target}",
                            field=kind,
                            value=str(target),
                            raw_text=raw_text,
                            clean_text=clean_text,
                            pattern=re.compile(re.escape(evidence_quote)),
                        )
                    )
                    targets.append(relation_identity)
            draft_identities = sorted(
                {
                    match.group(0)
                    for match in _DRAFT_ID.finditer(raw_text)
                    if raw_text.count(match.group(0)) == 1
                }
            )
            for identity in draft_identities:
                relation_facts.append(
                    _fact(
                        fact_id=f"draft_reference:{identity}",
                        field="draft_reference",
                        value=identity,
                        raw_text=raw_text,
                        clean_text=clean_text,
                        pattern=re.compile(
                            rf"(?<![A-Za-z0-9]){re.escape(identity)}(?![A-Za-z0-9])"
                        ),
                    )
                )
            record = _record(
                record_id=f"ietf:rfc:{number}",
                kind="rfc",
                occurred_at=_text_timestamp(raw_text),
                source_url=url,
                raw=raw,
                facts=[fact, *relation_facts],
                clean_text=clean_text,
                email_count=email_count,
                private_key_count=private_key_count,
            )
            record["rfc_number"] = number
            record["draft_references"] = draft_identities
            rfcs[number] = record
            rfc_relation_targets[number] = targets
        if retrieval["kind"] in {"datatracker_document", "datatracker_relation"}:
            supporting_records.append(record)
        else:
            records.append(record)

    if observed_private_key_digests != approved_private_key_digests:
        raise ProvenanceError("IETF approved private-key test vectors are not exact")

    relations: list[dict[str, Any]] = []
    for draft in request["drafts"]:
        name = draft["name"]
        revisions = draft["revisions"]
        for prior, current in pairwise(revisions):
            relations.append(
                _relation(
                    "revision_of",
                    drafts[(name, current)],
                    drafts[(name, prior)],
                    "draft_revision",
                    "draft_revision",
                )
            )
        metadata_record = metadata[name]
        edge_record, number = publication_edges[name]
        latest = drafts[(name, revisions[-1])]
        rfc = rfcs.get(number)
        if rfc is None:
            raise ProvenanceError("IETF published_as endpoints are not both grounded")
        relations.append(
            _relation(
                "published_as",
                latest,
                rfc,
                "draft_revision",
                "rfc_number",
                supporting_evidence=[(edge_record, "became_rfc")],
            )
        )
        if latest["revision"] != metadata_record["revision"]:
            raise ProvenanceError(
                "Datatracker latest revision does not match requested history"
            )
    referenced_targets: set[int] = set()
    published_numbers = {number for _record, number in publication_edges.values()}
    for number, targets in rfc_relation_targets.items():
        source = rfcs[number]
        for kind, target_number in targets:
            target_record = rfcs.get(target_number)
            if target_record is None:
                raise ProvenanceError("requested RFC relation target is missing")
            relations.append(
                _relation(
                    kind,
                    source,
                    target_record,
                    f"{kind}:{target_number}",
                    "rfc_number",
                )
            )
            referenced_targets.add(target_number)
    _validate_rfc_target_closure(
        requested=set(request["rfc_numbers"]),
        published=published_numbers,
        referenced=referenced_targets,
    )
    all_records = [*records, *supporting_records]
    if len(all_records) > MAX_IETF_RECORDS or len(
        {item["record_id"] for item in all_records}
    ) != len(all_records):
        raise ProvenanceError("IETF workflow record identities are invalid")
    manifest = {
        "schema_version": IETF_WORKFLOW_MANIFEST_SCHEMA,
        "source_status": "public_api_export",
        "data_stage": "source_inventory",
        "hybrid_train_ready": False,
        "production_eligible": False,
        "generation_integration": "disabled",
        "semantic_facts_train_ready": False,
        "generated_at": generated_at,
        "authorization": payload["authorization"],
        "fetch_inventory_sha256": fetch_inventory_sha256,
        "fetch_receipt": receipt,
        "n": len(records),
        "n_supporting_records": len(supporting_records),
        "n_relations": len(relations),
        "records": sorted(
            records, key=lambda item: (item["occurred_at"], item["record_id"])
        ),
        "supporting_records": sorted(
            supporting_records,
            key=lambda item: (item["occurred_at"], item["record_id"]),
        ),
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
    }
    try:
        validate_sanitized_public_payload(manifest)
    except ValueError as error:
        raise ProvenanceError("IETF manifest failed the public scanner") from error
    if len(json.dumps(manifest, ensure_ascii=False).encode()) > MAX_IETF_MANIFEST_BYTES:
        raise ProvenanceError("IETF workflow manifest exceeds size limit")
    return manifest
