"""Provenance contract for disabled IETF standards source inventories.

This module validates already-fetched official response bytes. It does not
integrate records into a world or make them train-ready.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from longworld.core.provenance import (
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)
from longworld.core.publicscan import (
    PUBLIC_SCANNER,
    PUBLIC_SCANNER_REVISION,
    sanitize_public_text,
    validate_sanitized_public_payload,
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


def _validate_retrieval_metadata(item: object) -> dict[str, Any]:
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
    return dict(item)


def _validate_retrieval(item: object, base: Path) -> tuple[dict[str, Any], bytes]:
    validated = _validate_retrieval_metadata(item)
    return validated, _regular_bytes(
        base, validated.get("retrieval_file"), validated.get("sha256")
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


IETF_NORMATIVE_TASK_SCHEMA = "longworld.ietf-normative-change-task.v1"
_NORMATIVE_LINE = re.compile(
    r"(?m)^(?P<prefix>[^\r\n]{1,240}?)(?P<keyword>MUST NOT|SHOULD NOT|MUST|SHOULD|MAY)"
    r"(?P<suffix>[^\r\n]{1,400})$"
)
_NORMATIVE_KEYWORD = re.compile(
    r"(?<![A-Z])(?:MUST NOT|SHOULD NOT|MUST|SHOULD|MAY)(?![A-Z])"
)


def audit_ietf_workflow_manifest(manifest: dict[str, Any]) -> None:
    """Fail closed on a signed-manifest payload before bundle adaptation."""
    records = manifest.get("records")
    supporting = manifest.get("supporting_records")
    relations = manifest.get("relations")
    if (
        manifest.get("schema_version") != IETF_WORKFLOW_MANIFEST_SCHEMA
        or manifest.get("source_status") != "public_api_export"
        or manifest.get("data_stage") != "source_inventory"
        or manifest.get("hybrid_train_ready") is not False
        or manifest.get("production_eligible") is not False
        or manifest.get("generation_integration") != "disabled"
        or manifest.get("semantic_facts_train_ready") is not False
        or not isinstance(records, list)
        or not isinstance(supporting, list)
        or not isinstance(relations, list)
        or manifest.get("n") != len(records)
        or manifest.get("n_supporting_records") != len(supporting)
        or manifest.get("n_relations") != len(relations)
    ):
        raise ProvenanceError("IETF signed workflow manifest contract is invalid")
    all_records = [*records, *supporting]
    receipt = manifest.get("fetch_receipt")
    retrievals = receipt.get("retrievals") if isinstance(receipt, dict) else None
    if not isinstance(retrievals, list) or not retrievals:
        raise ProvenanceError("IETF signed manifest receipt lineage is missing")
    lineage: dict[tuple[str, str], dict[str, Any]] = {}
    for retrieval in retrievals:
        retrieval = _validate_retrieval_metadata(retrieval)
        key = (
            str(retrieval.get("kind") or ""),
            str(retrieval.get("requested_url") or ""),
        )
        if (
            not all(key)
            or key in lineage
            or retrieval.get("final_url") != key[1]
            or _SHA256.fullmatch(str(retrieval.get("sha256") or "")) is None
        ):
            raise ProvenanceError("IETF signed manifest receipt lineage is invalid")
        _parse_timestamp(
            str(retrieval.get("observed_at") or ""),
            "IETF signed receipt observed_at",
        )
        lineage[key] = retrieval
    by_id: dict[str, dict[str, Any]] = {}
    used_lineage: set[tuple[str, str]] = set()
    for record in all_records:
        if not isinstance(record, dict):
            raise ProvenanceError("IETF signed workflow record is invalid")
        record_id = str(record.get("record_id") or "")
        text = record.get("text")
        source_sha256 = str(record.get("source_sha256") or "")
        text_sha256 = str(record.get("text_sha256") or "")
        facts = record.get("facts")
        lineage_key = (
            str(record.get("kind") or ""),
            str(record.get("source_url") or ""),
        )
        retrieval = lineage.get(lineage_key)
        if (
            not record_id
            or record_id in by_id
            or not isinstance(text, str)
            or not text
            or _SHA256.fullmatch(source_sha256) is None
            or hashlib.sha256(text.encode()).hexdigest() != text_sha256
            or record.get("provenance_id") != f"sha256:{source_sha256}"
            or record.get("source_family") != "ietf_standards"
            or record.get("source_origin") != "real_public"
            or not isinstance(facts, list)
            or not facts
            or retrieval is None
            or record.get("retrieval_url") != retrieval.get("final_url")
            or source_sha256 != retrieval.get("sha256")
        ):
            raise ProvenanceError(
                "IETF signed workflow record or receipt lineage binding is invalid"
            )
        occurred_at = _parse_timestamp(
            str(record.get("occurred_at") or ""), "IETF signed record occurred_at"
        )
        observed_at = _parse_timestamp(
            str(retrieval.get("observed_at") or ""),
            "IETF signed receipt observed_at",
        )
        semantics = str(record.get("temporal_semantics") or "")
        if (
            semantics == "retrieval_observation_only" and occurred_at != observed_at
        ) or (semantics == "source_event_time" and occurred_at > observed_at):
            raise ProvenanceError(
                "IETF signed manifest receipt lineage time is invalid"
            )
        if semantics not in {"retrieval_observation_only", "source_event_time"}:
            raise ProvenanceError(
                "IETF signed manifest receipt lineage time is invalid"
            )
        used_lineage.add(lineage_key)
        fact_ids: set[str] = set()
        for fact in facts:
            if not isinstance(fact, dict):
                raise ProvenanceError("IETF signed workflow fact is invalid")
            fact_id = str(fact.get("fact_id") or "")
            start, end = fact.get("char_start"), fact.get("char_end")
            quote = fact.get("evidence_quote")
            if (
                not fact_id
                or fact_id in fact_ids
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not isinstance(quote, str)
                or start < 0
                or end != start + len(quote)
                or text[start:end] != quote
                or not str(fact.get("value") or "")
            ):
                raise ProvenanceError("IETF signed workflow fact binding is invalid")
            fact_ids.add(fact_id)
        by_id[record_id] = record
    if used_lineage != set(lineage):
        raise ProvenanceError("IETF signed manifest receipt lineage is incomplete")
    primary_ids = {str(record["record_id"]) for record in records}
    adjacency: dict[str, set[str]] = {record_id: set() for record_id in primary_ids}
    relation_ids: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            raise ProvenanceError("IETF signed workflow relation is invalid")
        relation_id = str(relation.get("relation_id") or "")
        kind = str(relation.get("kind") or "")
        source_id = str(relation.get("source_record_id") or "")
        target_id = str(relation.get("target_record_id") or "")
        evidence = relation.get("evidence")
        if (
            not relation_id
            or relation_id in relation_ids
            or kind not in {"revision_of", "published_as", "updates", "obsoletes"}
            or source_id == target_id
            or source_id not in primary_ids
            or target_id not in primary_ids
            or not isinstance(evidence, list)
            or not evidence
        ):
            raise ProvenanceError("IETF signed workflow relation contract is invalid")
        evidence_ids: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise ProvenanceError("IETF signed relation evidence is invalid")
            record_id = str(item.get("record_id") or "")
            record = by_id.get(record_id)
            start, end = item.get("char_start"), item.get("char_end")
            quote = item.get("evidence_quote")
            if (
                record is None
                or item.get("source_sha256") != record.get("source_sha256")
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not isinstance(quote, str)
                or start < 0
                or end != start + len(quote)
                or record["text"][start:end] != quote
            ):
                raise ProvenanceError(
                    "IETF signed relation evidence binding is invalid"
                )
            evidence_ids.add(record_id)
        if not {source_id, target_id}.issubset(evidence_ids):
            raise ProvenanceError("IETF signed relation lacks endpoint evidence")
        adjacency[source_id].add(target_id)
        adjacency[target_id].add(source_id)
        relation_ids.add(relation_id)
    if len(primary_ids) > 1 and any(not edges for edges in adjacency.values()):
        raise ProvenanceError("IETF signed workflow has a disconnected primary record")
    try:
        validate_sanitized_public_payload(manifest)
    except ValueError as error:
        raise ProvenanceError(
            "IETF signed manifest failed the public scanner"
        ) from error


def _normative_lines(record: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(record.get("text") or "")
    matches: list[dict[str, Any]] = []
    for match in _NORMATIVE_LINE.finditer(text):
        quote = match.group(0).strip()
        start = text.index(quote, match.start(), match.end())
        matches.append(
            {
                "quote": quote,
                "keyword": match.group("keyword"),
                "anchor": (
                    " ".join(match.group("prefix").split()),
                    " ".join(match.group("suffix").split()),
                ),
                "char_start": start,
                "char_end": start + len(quote),
            }
        )
    return matches


def _normative_hunk(
    prior: dict[str, Any], latest: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = [
        (before, after)
        for before in _normative_lines(prior)
        for after in _normative_lines(latest)
        if before["anchor"] == after["anchor"] and before["keyword"] != after["keyword"]
    ]
    if len(changed) != 1:
        raise ProvenanceError(
            "IETF task requires one uniquely byte-bound normative revision hunk"
        )
    return changed[0]


def _task_record_map(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_records = task.get("source_records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ProvenanceError("IETF task source records are missing")
    records = {
        str(record.get("record_id") or ""): record
        for record in raw_records
        if isinstance(record, dict)
    }
    bindings = task.get("source_bindings")
    if (
        len(records) != len(raw_records)
        or not isinstance(bindings, dict)
        or set(bindings) != set(records)
    ):
        raise ProvenanceError("IETF task source bindings are invalid")
    for record_id, record in records.items():
        text = record.get("text")
        digest = hashlib.sha256(str(text).encode()).hexdigest()
        if (
            not isinstance(text, str)
            or not text
            or record.get("text_sha256") != digest
            or bindings.get(record_id) != digest
        ):
            raise ProvenanceError("IETF task source byte binding is invalid")
    return records


def _task_span(
    item: dict[str, Any], records: dict[str, dict[str, Any]]
) -> tuple[str, str]:
    record_id = str(item.get("record_id") or "")
    record = records.get(record_id)
    start, end = item.get("char_start"), item.get("char_end")
    quote = item.get("evidence_quote")
    if (
        record is None
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
        or start < 0
        or end != start + len(quote)
        or record["text"][start:end] != quote
    ):
        raise ProvenanceError("IETF task evidence byte binding is invalid")
    keyword_matches = list(_NORMATIVE_KEYWORD.finditer(quote))
    declared_keyword = str(item.get("keyword") or "")
    if len(keyword_matches) != 1 or declared_keyword != keyword_matches[0].group(0):
        raise ProvenanceError("IETF task normative keyword binding is invalid")
    return quote, declared_keyword


def _task_relation_valid(
    relation: dict[str, Any], records: dict[str, dict[str, Any]]
) -> bool:
    source_id = str(relation.get("source_record_id") or "")
    target_id = str(relation.get("target_record_id") or "")
    evidence = relation.get("evidence")
    if (
        source_id not in records
        or target_id not in records
        or not isinstance(evidence, list)
        or not evidence
    ):
        return False
    evidence_record_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            return False
        record_id = str(item.get("record_id") or "")
        record = records.get(record_id)
        start, end = item.get("char_start"), item.get("char_end")
        quote = item.get("evidence_quote")
        if (
            record is None
            or item.get("source_sha256") != record.get("source_sha256")
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not isinstance(quote, str)
            or start < 0
            or end != start + len(quote)
            or record["text"][start:end] != quote
        ):
            return False
        evidence_record_ids.add(record_id)
    return {source_id, target_id}.issubset(evidence_record_ids)


def _task_fact_value(record: dict[str, Any], fact_id: str) -> str:
    facts = record.get("facts")
    if not isinstance(facts, list):
        raise ProvenanceError("IETF task endpoint facts are missing")
    matches = [
        fact
        for fact in facts
        if isinstance(fact, dict) and fact.get("fact_id") == fact_id
    ]
    if len(matches) != 1:
        raise ProvenanceError("IETF task endpoint fact is not unique")
    fact = matches[0]
    start, end = fact.get("char_start"), fact.get("char_end")
    quote = fact.get("evidence_quote")
    value = str(fact.get("value") or "")
    if (
        not value
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
        or record["text"][start:end] != quote
        or value not in quote
    ):
        raise ProvenanceError("IETF task endpoint fact byte binding is invalid")
    return value


def _normative_answer(
    *,
    prior_record_id: str,
    latest_record_id: str,
    revision_source_id: str,
    revision_target_id: str,
    publication_source_id: str,
    draft_identity: str,
    rfc_number: str,
    before_keyword: str,
    after_keyword: str,
    after_quote: str,
) -> str:
    if (
        not draft_identity
        or not rfc_number
        or revision_source_id != latest_record_id
        or revision_target_id != prior_record_id
        or publication_source_id != latest_record_id
        or not all((before_keyword, after_keyword, after_quote))
    ):
        return "unknown"
    return (
        f"{draft_identity} | RFC {rfc_number} | "
        f"{before_keyword} -> {after_keyword} | {after_quote}"
    )


def replay_ietf_normative_change_task(
    task: dict[str, Any],
    *,
    evidence_ids: list[str] | None = None,
    counterfactual: bool = False,
) -> str:
    """Strictly replay one task from byte-bound spans and two source relations."""
    if (
        task.get("schema_version") != IETF_NORMATIVE_TASK_SCHEMA
        or task.get("data_stage") != "candidate_task"
        or task.get("train_ready") is not False
    ):
        raise ProvenanceError("IETF normative task contract is invalid")
    records = _task_record_map(task)
    raw_items = task.get("evidence_items")
    if not isinstance(raw_items, list):
        raise ProvenanceError("IETF task evidence items are invalid")
    items = {
        str(item.get("evidence_id") or ""): item
        for item in raw_items
        if isinstance(item, dict)
    }
    essentials = task.get("essential_evidence_ids")
    if (
        not isinstance(essentials, list)
        or any(not isinstance(item, str) for item in essentials)
        or len(items) != len(raw_items)
        or set(essentials) != set(items)
    ):
        raise ProvenanceError("IETF task essential evidence contract is invalid")
    selected = set(essentials if evidence_ids is None else evidence_ids)
    if not selected.issubset(items):
        raise ProvenanceError("IETF task evidence selection is invalid")

    before_keyword = ""
    after_keyword = ""
    after_quote = ""
    prior_record_id = ""
    latest_record_id = ""
    revision_source_id = ""
    revision_target_id = ""
    publication_source_id = ""
    draft_identity = ""
    rfc_number = ""
    for evidence_id in selected:
        item = items[evidence_id]
        kind = item.get("kind")
        if kind == "normative_before":
            _quote, before_keyword = _task_span(item, records)
            prior_record_id = str(item["record_id"])
        elif kind == "normative_after":
            after_quote, after_keyword = _task_span(item, records)
            latest_record_id = str(item["record_id"])
        elif kind in {"revision_of", "published_as"}:
            relation = item.get("relation")
            if (
                not isinstance(relation, dict)
                or relation.get("kind") != kind
                or not _task_relation_valid(relation, records)
            ):
                raise ProvenanceError("IETF task relation byte binding is invalid")
            if kind == "revision_of":
                revision_source_id = str(relation["source_record_id"])
                revision_target_id = str(relation["target_record_id"])
            else:
                publication_source_id = str(relation["source_record_id"])
                latest_record_id = str(relation["source_record_id"])
                rfc_record_id = str(relation["target_record_id"])
                draft_identity = _task_fact_value(
                    records[latest_record_id], "draft_revision"
                )
                rfc_number = _task_fact_value(records[rfc_record_id], "rfc_number")
        else:
            raise ProvenanceError("IETF task evidence kind is invalid")

    if counterfactual and items:
        twin = task.get("counterfactual_twin")
        if not isinstance(twin, dict):
            raise ProvenanceError("IETF counterfactual twin is missing")
        after_items = [
            item for item in items.values() if item.get("kind") == "normative_after"
        ]
        if len(after_items) != 1:
            raise ProvenanceError("IETF counterfactual normative parent is invalid")
        after_item = after_items[0]
        parent_quote, parent_keyword = _task_span(after_item, records)
        parent_record_id = str(after_item.get("record_id") or "")
        latest = records.get(parent_record_id)
        text = twin.get("text")
        digest = hashlib.sha256(str(text).encode()).hexdigest()
        parent_start = int(after_item["char_start"])
        parent_end = int(after_item["char_end"])
        keyword_match = _NORMATIVE_KEYWORD.search(parent_quote)
        if keyword_match is None:
            raise ProvenanceError("IETF counterfactual normative parent is invalid")
        keyword_start = parent_start + keyword_match.start()
        keyword_end = parent_start + keyword_match.end()
        replacement = str(twin.get("keyword") or "")
        expected_text = ""
        expected_quote = ""
        if latest is not None and _NORMATIVE_KEYWORD.fullmatch(replacement):
            expected_text = (
                latest["text"][:keyword_start]
                + replacement
                + latest["text"][keyword_end:]
            )
            expected_quote = (
                parent_quote[: keyword_match.start()]
                + replacement
                + parent_quote[keyword_match.end() :]
            )
        if (
            latest is None
            or twin.get("record_id") != parent_record_id
            or twin.get("source_origin") != "synthetic_counterfactual"
            or twin.get("provenance_operation") != "replace_normative_keyword"
            or twin.get("parent_text_sha256") != latest.get("text_sha256")
            or twin.get("parent_evidence_quote") != parent_quote
            or twin.get("parent_char_start") != parent_start
            or twin.get("parent_char_end") != parent_end
            or twin.get("parent_keyword") != parent_keyword
            or twin.get("keyword_char_start") != keyword_start
            or twin.get("keyword_char_end") != keyword_start + len(replacement)
            or replacement == parent_keyword
            or not isinstance(text, str)
            or twin.get("text_sha256") != digest
            or text != expected_text
            or twin.get("evidence_quote") != expected_quote
            or twin.get("char_start") != parent_start
            or twin.get("char_end") != parent_start + len(expected_quote)
        ):
            raise ProvenanceError("IETF counterfactual replacement is invalid")
        if after_item["evidence_id"] in selected:
            after_quote = expected_quote
            after_keyword = replacement

    return _normative_answer(
        prior_record_id=prior_record_id,
        latest_record_id=latest_record_id,
        revision_source_id=revision_source_id,
        revision_target_id=revision_target_id,
        publication_source_id=publication_source_id,
        draft_identity=draft_identity,
        rfc_number=rfc_number,
        before_keyword=before_keyword,
        after_keyword=after_keyword,
        after_quote=after_quote,
    )


def _compile_ietf_normative_change_task(manifest: dict[str, Any]) -> dict[str, Any]:
    if (
        manifest.get("schema_version") != IETF_WORKFLOW_MANIFEST_SCHEMA
        or manifest.get("generation_integration") != "disabled"
        or manifest.get("production_eligible") is not False
    ):
        raise ProvenanceError("IETF task requires a disabled source manifest")
    raw_records = [
        *manifest.get("records", []),
        *manifest.get("supporting_records", []),
    ]
    records = {
        str(record.get("record_id") or ""): record
        for record in raw_records
        if isinstance(record, dict)
    }
    candidates: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
        ]
    ] = []
    relations = manifest.get("relations")
    if not isinstance(relations, list):
        raise ProvenanceError("IETF source relations are missing")
    for revision in relations:
        if not isinstance(revision, dict) or revision.get("kind") != "revision_of":
            continue
        latest = records.get(str(revision.get("source_record_id") or ""))
        prior = records.get(str(revision.get("target_record_id") or ""))
        publications = [
            relation
            for relation in relations
            if isinstance(relation, dict)
            and relation.get("kind") == "published_as"
            and relation.get("source_record_id") == revision.get("source_record_id")
        ]
        if latest is None or prior is None or len(publications) != 1:
            continue
        published = publications[0]
        rfc = records.get(str(published.get("target_record_id") or ""))
        if rfc is None:
            continue
        try:
            before, after = _normative_hunk(prior, latest)
        except ProvenanceError:
            continue
        candidates.append(
            (
                prior,
                latest,
                rfc,
                revision,
                published,
                {"before": before, "after": after},
            )
        )
    if len(candidates) != 1:
        raise ProvenanceError("IETF manifest does not uniquely select a normative task")
    prior, latest, rfc, revision, published, hunk = candidates[0]
    before, after = hunk["before"], hunk["after"]
    evidence_items = [
        {
            "evidence_id": "normative_before",
            "kind": "normative_before",
            "record_id": prior["record_id"],
            "evidence_quote": before["quote"],
            "surface_text": before["quote"],
            "keyword": before["keyword"],
            "char_start": before["char_start"],
            "char_end": before["char_end"],
        },
        {
            "evidence_id": "normative_after",
            "kind": "normative_after",
            "record_id": latest["record_id"],
            "evidence_quote": after["quote"],
            "surface_text": after["quote"],
            "keyword": after["keyword"],
            "char_start": after["char_start"],
            "char_end": after["char_end"],
        },
        {
            "evidence_id": "revision_relation",
            "kind": "revision_of",
            "surface_text": "\n".join(
                str(item["evidence_quote"]) for item in revision["evidence"]
            ),
            "relation": revision,
        },
        {
            "evidence_id": "publication_relation",
            "kind": "published_as",
            "surface_text": "\n".join(
                str(item["evidence_quote"]) for item in published["evidence"]
            ),
            "relation": published,
        },
    ]
    replacement = next(
        keyword
        for keyword in ("MAY", "SHOULD", "MUST")
        if keyword not in {before["keyword"], after["keyword"]}
    )
    cf_quote = str(after["quote"]).replace(str(after["keyword"]), replacement, 1)
    keyword_offset = str(after["quote"]).index(str(after["keyword"]))
    keyword_start = int(after["char_start"]) + keyword_offset
    cf_text = (
        str(latest["text"])[: after["char_start"]]
        + cf_quote
        + str(latest["text"])[after["char_end"] :]
    )
    task: dict[str, Any] = {
        "schema_version": IETF_NORMATIVE_TASK_SCHEMA,
        "data_stage": "candidate_task",
        "train_ready": False,
        "query_type": "normative_change_introducer",
        "answer_program_id": "ietf.normative_change_introducer.v1",
        "question": (
            "Which draft revision introduced the normative keyword change, and "
            "which RFC was that revision published as? Return the prior and current "
            "keywords plus the exact current normative statement."
        ),
        "answer": "",
        "cf_answer": "",
        "essential_evidence_ids": [item["evidence_id"] for item in evidence_items],
        "evidence_items": evidence_items,
        "source_bindings": {
            record_id: record["text_sha256"] for record_id, record in records.items()
        },
        "source_records": list(records.values()),
        "counterfactual_twin": {
            "record_id": latest["record_id"],
            "source_origin": "synthetic_counterfactual",
            "provenance_operation": "replace_normative_keyword",
            "parent_text_sha256": latest["text_sha256"],
            "parent_evidence_quote": after["quote"],
            "parent_char_start": after["char_start"],
            "parent_char_end": after["char_end"],
            "parent_keyword": after["keyword"],
            "text": cf_text,
            "text_sha256": hashlib.sha256(cf_text.encode()).hexdigest(),
            "evidence_quote": cf_quote,
            "keyword": replacement,
            "char_start": after["char_start"],
            "char_end": after["char_start"] + len(cf_quote),
            "keyword_char_start": keyword_start,
            "keyword_char_end": keyword_start + len(replacement),
        },
    }
    task["answer"] = replay_ietf_normative_change_task(task)
    task["cf_answer"] = replay_ietf_normative_change_task(task, counterfactual=True)
    return task


def build_ietf_normative_change_task(manifest: dict[str, Any]) -> dict[str, Any]:
    """Audit a source manifest, then compile one executable task candidate."""
    audit_ietf_workflow_manifest(manifest)
    return _compile_ietf_normative_change_task(manifest)


def materialize_ietf_normative_change_task(workflow: Any) -> dict[str, Any]:
    """Materialize a standalone task from one bundle-authorized workflow."""
    if (
        getattr(workflow, "source_kind", "") != "ietf_standards"
        or getattr(workflow, "target_domain", "") != "standards"
        or not str(getattr(workflow, "component_digest", ""))
    ):
        raise ProvenanceError("IETF task materialization requires a standards workflow")

    def record_payload(record: Any) -> dict[str, Any]:
        attributes = dict(getattr(record, "attributes", ()))
        return {
            "record_id": record.record_id,
            "kind": record.kind,
            "occurred_at": record.occurred_at,
            "source_url": record.source_url,
            "retrieval_url": record.retrieval_url,
            "source_family": record.source_family,
            "source_origin": record.source_origin.value,
            "source_sha256": record.source_sha256,
            "text_sha256": record.text_sha256,
            "provenance_id": record.provenance_id,
            "text": record.text,
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "field": fact.field,
                    "value": fact.value,
                    "evidence_quote": fact.evidence_quote,
                    "char_start": fact.char_start,
                    "char_end": fact.char_end,
                }
                for fact in record.facts
            ],
            **attributes,
        }

    converted_records = [record_payload(record) for record in workflow.records]
    primary = [
        record
        for record in converted_records
        if record["kind"] in {"draft_revision", "rfc"}
    ]
    supporting = [
        record
        for record in converted_records
        if record["kind"] not in {"draft_revision", "rfc"}
    ]
    relations = [
        {
            "relation_id": relation.relation_id,
            "kind": relation.kind,
            "source_record_id": relation.source_record_id,
            "target_record_id": relation.target_record_id,
            "evidence": [
                {
                    "record_id": item.record_id,
                    "fact_ids": list(item.fact_ids),
                    "evidence_quote": item.evidence_quote,
                    "char_start": item.char_start,
                    "char_end": item.char_end,
                    "source_sha256": item.source_sha256,
                }
                for item in relation.evidence
            ],
        }
        for relation in workflow.relations
    ]
    manifest = {
        "schema_version": IETF_WORKFLOW_MANIFEST_SCHEMA,
        "generation_integration": "disabled",
        "production_eligible": False,
        "records": primary,
        "supporting_records": supporting,
        "relations": relations,
    }
    task = _compile_ietf_normative_change_task(manifest)
    task["source_workflow_binding"] = {
        "workflow_id": workflow.workflow_id,
        "component_digest": workflow.component_digest,
    }
    return task


def audit_ietf_normative_change_task(task: dict[str, Any]) -> dict[str, bool]:
    """Replay strict, CF, remove-one, surface, and byte-grounding gates."""
    essentials = list(task.get("essential_evidence_ids") or [])
    answer = str(task.get("answer") or "")
    cf_answer = str(task.get("cf_answer") or "")
    strict = replay_ietf_normative_change_task(task) == answer
    cf_replay = replay_ietf_normative_change_task(task, counterfactual=True)
    singles = [
        replay_ietf_normative_change_task(task, evidence_ids=[evidence_id])
        for evidence_id in essentials
    ]
    removals = [
        replay_ietf_normative_change_task(
            task,
            evidence_ids=[item for item in essentials if item != removed],
        )
        for removed in essentials
    ]
    items = task.get("evidence_items") or []
    surface_free = bool(items) and all(
        answer not in str(item.get("surface_text") or "") for item in items
    )
    return {
        "strict_replay_sufficient": strict,
        "counterfactual_replay_sufficient": cf_replay == cf_answer,
        "counterfactual_changes_answer": cf_answer != answer,
        "remove_one_fails": bool(removals)
        and all(value != answer for value in removals),
        "essential_single_doc_insufficient": bool(singles)
        and all(value != answer for value in singles),
        "essential_surface_gold_free": surface_free,
        "essential_text_grounded": strict,
    }
