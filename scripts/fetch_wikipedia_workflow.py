#!/usr/bin/env python3
"""Fetch bounded Wikipedia revision and Wikidata entity source inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.documentworkflow import WIKIPEDIA_WORKFLOW_INPUT_SCHEMA
from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

WIKIMEDIA_FETCH_REQUEST_SCHEMA = "longworld.wikimedia-fetch-request.v1"
WIKIMEDIA_API_POLICY = "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy"
MAX_TITLES = 16
MAX_RETRIES = 3
_LANGUAGE = re.compile(r"^[a-z][a-z0-9-]{1,11}$")
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

HttpGet = Callable[[str, dict[str, str], float], bytes]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _default_http_get(url: str, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_SOURCE_BYTES + 1)


def _authorization(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ProvenanceError("Wikimedia fetch authorization is invalid")
    fields = ("record_id", "scope", "basis", "reviewed_at")
    receipt = {field: str(value.get(field) or "").strip() for field in fields}
    if any(not receipt[field] for field in fields):
        raise ProvenanceError("Wikimedia fetch authorization is invalid")
    _parse_timestamp(receipt["reviewed_at"], "authorization.reviewed_at")
    return receipt


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != WIKIMEDIA_FETCH_REQUEST_SCHEMA:
        raise ProvenanceError("unsupported Wikimedia fetch request schema")
    user_agent = str(payload.get("user_agent") or "").strip()
    if (
        len(user_agent) > 256
        or _CONTACT_USER_AGENT.fullmatch(user_agent) is None
        or "example.com" in user_agent.lower()
    ):
        raise ProvenanceError("Wikimedia fetch requires a contact User-Agent")
    language = str(payload.get("language") or "")
    if _LANGUAGE.fullmatch(language) is None:
        raise ProvenanceError("Wikimedia language is invalid")
    raw_titles = payload.get("titles")
    if (
        not isinstance(raw_titles, list)
        or not raw_titles
        or len(raw_titles) > MAX_TITLES
    ):
        raise ProvenanceError("Wikimedia titles are invalid")
    titles = [str(title).strip() for title in raw_titles]
    if any(not title or len(title) > 256 for title in titles) or len(titles) != len(
        set(titles)
    ):
        raise ProvenanceError("Wikimedia titles are invalid")
    rate = payload.get("requests_per_second", 5)
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= 10
    ):
        raise ProvenanceError("Wikimedia requests_per_second is invalid")
    return {
        "user_agent": user_agent,
        "authorization": _authorization(payload.get("authorization")),
        "language": language,
        "titles": titles,
        "requests_per_second": float(rate),
    }


class _Downloader:
    def __init__(
        self,
        *,
        user_agent: str,
        requests_per_second: float,
        http_get: HttpGet,
        sleep: Callable[[float], None],
    ) -> None:
        self.user_agent = user_agent
        self.minimum_interval = 1.0 / requests_per_second
        self.http_get = http_get
        self.sleep = sleep
        self.last_request_at: float | None = None

    def get_json(self, url: str) -> tuple[bytes, dict[str, Any]]:
        for attempt in range(MAX_RETRIES + 1):
            now = time.monotonic()
            if self.last_request_at is not None:
                remaining = self.minimum_interval - (now - self.last_request_at)
                if remaining > 0:
                    self.sleep(remaining)
            self.last_request_at = time.monotonic()
            try:
                raw = self.http_get(url, {"User-Agent": self.user_agent}, 90.0)
                if not raw or len(raw) > MAX_SOURCE_BYTES:
                    raise ProvenanceError(
                        "Wikimedia response is empty or exceeds size limit"
                    )
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ProvenanceError("Wikimedia response must be a JSON object")
                return raw, value
            except urllib.error.HTTPError as error:
                if error.code not in _RETRYABLE_STATUS or attempt == MAX_RETRIES:
                    raise ProvenanceError(
                        f"Wikimedia request failed with HTTP {error.code}"
                    ) from error
                self.sleep(2.0**attempt)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProvenanceError("Wikimedia response is invalid JSON") from error
            except urllib.error.URLError as error:
                if attempt == MAX_RETRIES:
                    raise ProvenanceError(
                        f"Wikimedia request failed: {error.reason}"
                    ) from error
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")


def _api_url(host: str, parameters: dict[str, object]) -> str:
    return f"https://{host}/w/api.php?{urllib.parse.urlencode(parameters)}"


def _single_page(payload: dict[str, Any]) -> dict[str, Any]:
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise ProvenanceError("Wikimedia response does not identify one page")
    page = pages[0]
    if page.get("missing") is True:
        raise ProvenanceError("Wikimedia page is missing")
    return page


def _validate_title_resolution(
    payload: dict[str, Any], requested_title: str, resolved_title: str
) -> None:
    query = payload.get("query")
    if not isinstance(query, dict):
        raise ProvenanceError("Wikimedia title resolution is missing")
    links: dict[str, str] = {}
    for field in ("normalized", "redirects"):
        values = query.get(field) or []
        if not isinstance(values, list):
            raise ProvenanceError("Wikimedia title resolution is invalid")
        for item in values:
            if not isinstance(item, dict):
                raise ProvenanceError("Wikimedia title resolution is invalid")
            source = str(item.get("from") or "")
            target = str(item.get("to") or "")
            if not source or not target or source in links:
                raise ProvenanceError("Wikimedia title resolution is invalid")
            links[source] = target
    current = requested_title
    seen: set[str] = set()
    while current in links:
        if current in seen:
            raise ProvenanceError("Wikimedia title resolution contains a cycle")
        seen.add(current)
        current = links[current]
    if current != resolved_title:
        raise ProvenanceError(
            "Wikimedia response title is outside the request allowlist"
        )
    if requested_title != resolved_title:
        raise ProvenanceError("Wikimedia title resolution must be exact")


def _revision(page: dict[str, Any]) -> dict[str, Any]:
    revisions = page.get("revisions")
    if (
        not isinstance(revisions, list)
        or len(revisions) != 1
        or not isinstance(revisions[0], dict)
    ):
        raise ProvenanceError("Wikimedia revision response is invalid")
    return revisions[0]


def _evidence(raw: bytes, pattern: re.Pattern[str], label: str) -> tuple[str, int]:
    text = raw.decode("utf-8")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ProvenanceError(f"Wikimedia source does not uniquely bind {label}")
    return matches[0].group(0), matches[0].start()


def _record_common(
    *,
    source_file: Path,
    source_raw: bytes,
    source_url: str,
    retrieval_url: str,
    occurred_at: str,
    retrieved_at: str,
    parser: str,
) -> dict[str, Any]:
    _parse_timestamp(occurred_at, "record occurred_at")
    return {
        "source_url": source_url,
        "retrieval_url": retrieval_url,
        "source_file": source_file.name,
        "source_sha256": hashlib.sha256(source_raw).hexdigest(),
        "occurred_at": occurred_at,
        "retrieved_at": retrieved_at,
        "access_policy": f"public Wikimedia API; policy {WIKIMEDIA_API_POLICY}",
        "parser": {"name": parser, "version": "1"},
    }


def fetch_wikipedia_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
) -> Path:
    try:
        raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(
            f"cannot read Wikimedia fetch request: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ProvenanceError("Wikimedia fetch request must be an object")
    request = _validate_request(payload)
    retrieved_at = generated_at or _timestamp()
    _parse_timestamp(retrieved_at, "generated_at")
    output_directory.mkdir(parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        requests_per_second=request["requests_per_second"],
        http_get=http_get,
        sleep=sleep,
    )
    records: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    title_resolutions: list[dict[str, str]] = []
    seen_pages: set[int] = set()
    seen_entities: set[str] = set()
    wiki_host = f"{request['language']}.wikipedia.org"

    for requested_title in request["titles"]:
        metadata_url = _api_url(
            wiki_host,
            {
                "action": "query",
                "prop": "revisions|pageprops",
                "titles": requested_title,
                "rvprop": "ids|timestamp",
                "rvlimit": 2,
                "format": "json",
                "formatversion": 2,
                "redirects": 1,
            },
        )
        _metadata_raw, metadata = downloader.get_json(metadata_url)
        page = _single_page(metadata)
        page_id = page.get("pageid")
        title = str(page.get("title") or "")
        _validate_title_resolution(metadata, requested_title, title)
        title_resolutions.append(
            {"requested_title": requested_title, "resolved_title": title}
        )
        pageprops = page.get("pageprops")
        entity_id = (
            str(pageprops.get("wikibase_item") or "")
            if isinstance(pageprops, dict)
            else ""
        )
        revisions = page.get("revisions")
        if (
            not isinstance(page_id, int)
            or page_id <= 0
            or page_id in seen_pages
            or not title
            or not re.fullmatch(r"Q[1-9]\d*", entity_id)
            or not isinstance(revisions, list)
            or len(revisions) != 2
            or not all(isinstance(item, dict) for item in revisions)
        ):
            raise ProvenanceError("Wikimedia page metadata is incomplete")
        current, parent = revisions
        current_id = current.get("revid")
        parent_id = parent.get("revid")
        if (
            not isinstance(current_id, int)
            or not isinstance(parent_id, int)
            or current_id <= 0
            or parent_id <= 0
            or current.get("parentid") != parent_id
        ):
            raise ProvenanceError("Wikimedia revision ancestry is invalid")
        seen_pages.add(page_id)

        by_revision: dict[int, tuple[dict[str, Any], bytes]] = {}
        for revision_id in (parent_id, current_id):
            revision_url = _api_url(
                wiki_host,
                {
                    "action": "query",
                    "prop": "revisions|pageprops",
                    "revids": revision_id,
                    "rvprop": "ids|timestamp|content",
                    "rvslots": "main",
                    "format": "json",
                    "formatversion": 2,
                },
            )
            revision_raw, revision_payload = downloader.get_json(revision_url)
            revision_page = _single_page(revision_payload)
            revision = _revision(revision_page)
            if (
                revision_page.get("pageid") != page_id
                or revision_page.get("title") != title
                or revision.get("revid") != revision_id
            ):
                raise ProvenanceError("Wikimedia revision identity mismatch")
            source_file = output_directory / f"wikipedia-{page_id}-r{revision_id}.json"
            _atomic_write(source_file, revision_raw)
            record_id = f"page-{page_id}-r{revision_id}"
            record = {
                "record_id": record_id,
                "kind": "wikipedia_revision",
                "revision_id": revision_id,
                "page_id": page_id,
                "title": title,
                "parent_revision_id": revision.get("parentid") or None,
                **_record_common(
                    source_file=source_file,
                    source_raw=revision_raw,
                    source_url=(f"https://{wiki_host}/w/index.php?oldid={revision_id}"),
                    retrieval_url=revision_url,
                    occurred_at=str(revision.get("timestamp") or ""),
                    retrieved_at=retrieved_at,
                    parser="mediawiki_revision_api",
                ),
            }
            records.append(record)
            by_revision[revision_id] = (record, revision_raw)

        entity_url = _api_url(
            "www.wikidata.org",
            {
                "action": "wbgetentities",
                "ids": entity_id,
                "props": "info|labels|sitelinks",
                "languages": request["language"],
                "sitefilter": f"{request['language']}wiki",
                "format": "json",
            },
        )
        entity_raw, entity_payload = downloader.get_json(entity_url)
        entities = entity_payload.get("entities")
        entity = entities.get(entity_id) if isinstance(entities, dict) else None
        if not isinstance(entity, dict) or entity.get("id") != entity_id:
            raise ProvenanceError("Wikidata entity identity mismatch")
        entity_revision = entity.get("lastrevid")
        occurred_at = str(entity.get("modified") or "")
        if (
            not isinstance(entity_revision, int)
            or entity_revision <= 0
            or entity_id in seen_entities
        ):
            raise ProvenanceError("Wikidata entity revision is invalid")
        _parse_timestamp(occurred_at, "Wikidata entity modified")
        seen_entities.add(entity_id)
        entity_file = output_directory / f"wikidata-{entity_id}-r{entity_revision}.json"
        _atomic_write(entity_file, entity_raw)
        entity_record = {
            "record_id": f"entity-{entity_id}-r{entity_revision}",
            "kind": "wikidata_entity_revision",
            "revision_id": entity_revision,
            "entity_id": entity_id,
            **_record_common(
                source_file=entity_file,
                source_raw=entity_raw,
                source_url=entity_url,
                retrieval_url=entity_url,
                occurred_at=occurred_at,
                retrieved_at=retrieved_at,
                parser="wikibase_entity_api",
            ),
        }
        records.append(entity_record)

        current_record, current_raw = by_revision[current_id]
        parent_record, _parent_raw = by_revision[parent_id]
        parent_quote, parent_start = _evidence(
            current_raw,
            re.compile(rf'"parentid"\s*:\s*{parent_id}(?!\d)'),
            "parent revision",
        )
        entity_quote, entity_start = _evidence(
            current_raw,
            re.compile(rf'"wikibase_item"\s*:\s*"{re.escape(entity_id)}"'),
            "Wikidata entity",
        )
        title_quote, title_start = _evidence(
            entity_raw,
            re.compile(rf'"title"\s*:\s*{re.escape(json.dumps(title))}'),
            "Wikipedia title",
        )
        relations.extend(
            [
                {
                    "relation_id": f"page-r{current_id}-r{parent_id}",
                    "kind": "revision_of",
                    "source_record_id": current_record["record_id"],
                    "target_record_id": parent_record["record_id"],
                    "evidence_record_id": current_record["record_id"],
                    "evidence_quote": parent_quote,
                    "evidence_char_start": parent_start,
                },
                {
                    "relation_id": f"page-{page_id}-{entity_id}",
                    "kind": "page_describes_entity",
                    "source_record_id": current_record["record_id"],
                    "target_record_id": entity_record["record_id"],
                    "evidence_record_id": current_record["record_id"],
                    "evidence_quote": entity_quote,
                    "evidence_char_start": entity_start,
                },
                {
                    "relation_id": f"{entity_id}-page-{page_id}",
                    "kind": "entity_resolves_page",
                    "source_record_id": entity_record["record_id"],
                    "target_record_id": current_record["record_id"],
                    "evidence_record_id": entity_record["record_id"],
                    "evidence_quote": title_quote,
                    "evidence_char_start": title_start,
                },
            ]
        )

    output = {
        "schema_version": WIKIPEDIA_WORKFLOW_INPUT_SCHEMA,
        "source_status": "public_api_export",
        "authorization": request["authorization"],
        "fetch_receipt": {
            "schema_version": "longworld.wikimedia-fetch-receipt.v1",
            "generated_at": retrieved_at,
            "policy_url": WIKIMEDIA_API_POLICY,
            "user_agent_sha256": hashlib.sha256(
                request["user_agent"].encode("utf-8")
            ).hexdigest(),
            "language": request["language"],
            "title_resolutions": title_resolutions,
        },
        "records": records,
        "relations": relations,
    }
    input_path = output_directory / "wikipedia_workflow_input.json"
    _atomic_write(input_path, json.dumps(output, ensure_ascii=False, indent=2).encode())
    return input_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    fetch_wikipedia_workflow(args.request, args.out_dir)


if __name__ == "__main__":
    main()
