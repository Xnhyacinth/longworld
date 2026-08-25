#!/usr/bin/env python3
"""Fetch bounded arXiv and OpenReview records into an auditable inventory.

This stage records official response bytes and parser-derived record files. It
does not authorize generation: a signed source-workflow bundle and strict replay
are still required before any record may affect an SFT row.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.provenance import (
    MAX_MANIFEST_BYTES,
    ProvenanceError,
    _parse_timestamp,
    _read_regular_file,
)

PAPER_FETCH_REQUEST_SCHEMA = "longworld.paper-fetch-request.v1"
PAPER_FETCH_INVENTORY_SCHEMA = "longworld.paper-fetch-inventory.v1"
ARXIV_API_MANUAL = "https://info.arxiv.org/help/api/user-manual.html"
OPENREVIEW_API_DEFINITION = (
    "https://docs.openreview.net/reference/api-v2/openapi-definition"
)
MAX_ARXIV_VERSIONS = 32
MAX_OPENREVIEW_FORUMS = 16
MAX_RETRIES = 3
MAX_REQUESTS_PER_SECOND = 3.0
MAX_PAPER_RESPONSE_BYTES = 64_000_000
MAX_ARXIV_ATOM_RESPONSE_BYTES = 4_000_000
MAX_OPENREVIEW_RESPONSE_BYTES = 16_000_000
MAX_ARCHIVE_MEMBERS = 2_048
MAX_ARCHIVE_DECLARED_BYTES = 64_000_000
MAX_ARCHIVE_EXPANSION_RATIO = 100
MAX_ARCHIVE_STREAM_BYTES = 72_000_000
MAX_LATEX_FILE_BYTES = 4_000_000
MAX_LATEX_TOTAL_BYTES = 8_000_000
MAX_OPENREVIEW_NOTES = 512
MAX_OPENREVIEW_CONTENT_FIELDS = 64
MAX_OPENREVIEW_FIELD_BYTES = 1_000_000
MAX_OPENREVIEW_TOTAL_TEXT_BYTES = 8_000_000
ARXIV_ARCHIVE_PARSER_REVISION = "arxiv_source_tar_v2"
_ALLOWED_ACTIONS = {
    "fetch_arxiv_metadata",
    "fetch_arxiv_source",
    "fetch_openreview_forum",
}
_ARXIV_ATOM_CONTENT_TYPES = {
    "application/atom+xml",
    "application/xml",
    "text/xml",
}
_ARXIV_ARCHIVE_CONTENT_TYPES = {
    "application/gzip",
    "application/octet-stream",
    "application/x-eprint",
    "application/x-eprint-tar",
    "application/x-gzip",
    "application/x-tar",
}
_OPENREVIEW_CONTENT_TYPES = {"application/json"}
_RETRYABLE = {429, 500, 502, 503, 504}
_CONTACT_USER_AGENT = re.compile(
    r"^\S(?:.*\S)?\s+[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$"
)
_ARXIV_VERSION_ID = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7}))v(?P<version>[1-9]\d*)$"
)
_ARXIV_LINK = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:\s*)(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HttpRedirect:
    status: int
    from_url: str
    to_url: str


@dataclass(frozen=True)
class HttpResponse:
    """Bounded HTTP bytes with the transport metadata needed for provenance."""

    body: bytes
    status: int
    final_url: str
    content_type: str
    redirect_chain: tuple[HttpRedirect, ...] = ()


HttpGet = Callable[[str, dict[str, str], float], HttpResponse]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _allowed_redirect_url(requested_url: str) -> str | None:
    parsed = urllib.parse.urlsplit(requested_url)
    prefix = "/e-print/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "export.arxiv.org"
        or not parsed.path.startswith(prefix)
        or not parsed.path.removeprefix(prefix)
        or parsed.query
        or parsed.fragment
    ):
        return None
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/src/{parsed.path.removeprefix(prefix)}",
            "",
            "",
        )
    )


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, requested_url: str) -> None:
        super().__init__()
        self.requested_url = requested_url
        self.allowed_url = _allowed_redirect_url(requested_url)
        self.redirect_chain: list[HttpRedirect] = []

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        if (
            self.redirect_chain
            or self.allowed_url is None
            or code != 301
            or req.full_url != self.requested_url
            or newurl != self.allowed_url
        ):
            return None
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            self.redirect_chain.append(
                HttpRedirect(
                    status=code,
                    from_url=self.requested_url,
                    to_url=self.allowed_url,
                )
            )
        return redirected


def _default_http_get(
    url: str, headers: dict[str, str], timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers)
    redirect_handler = _PinnedRedirectHandler(url)
    opener = urllib.request.build_opener(redirect_handler)
    with opener.open(request, timeout=timeout) as response:
        return HttpResponse(
            body=response.read(MAX_PAPER_RESPONSE_BYTES + 1),
            status=response.status,
            final_url=response.geturl(),
            content_type=response.headers.get_content_type(),
            redirect_chain=tuple(redirect_handler.redirect_chain),
        )


def _authorization(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceError("paper fetch authorization is invalid")
    fields = ("record_id", "scope", "basis", "reviewed_at")
    receipt = {field: str(value.get(field) or "").strip() for field in fields}
    if any(not receipt[field] for field in fields):
        raise ProvenanceError("paper fetch authorization is invalid")
    _parse_timestamp(receipt["reviewed_at"], "authorization.reviewed_at")
    allowed_actions = _unique_strings(
        value.get("allowed_actions"),
        "authorization.allowed_actions",
        maximum=len(_ALLOWED_ACTIONS),
    )
    if not set(allowed_actions).issubset(_ALLOWED_ACTIONS):
        raise ProvenanceError("paper fetch authorization.allowed_actions is invalid")
    return {**receipt, "allowed_actions": sorted(allowed_actions)}


def _unique_strings(
    value: object, label: str, *, maximum: int, allow_empty: bool = False
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or len(value) > maximum
    ):
        raise ProvenanceError(f"paper fetch {label} is invalid")
    result = [str(item).strip() for item in value]
    if any(not item or len(item) > 256 for item in result) or len(set(result)) != len(
        result
    ):
        raise ProvenanceError(f"paper fetch {label} is invalid")
    return result


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != PAPER_FETCH_REQUEST_SCHEMA:
        raise ProvenanceError("unsupported paper fetch request schema")
    user_agent = str(payload.get("user_agent") or "").strip()
    if (
        len(user_agent) > 256
        or _CONTACT_USER_AGENT.fullmatch(user_agent) is None
        or "example." in user_agent.lower()
    ):
        raise ProvenanceError("paper fetch requires a contact User-Agent")
    arxiv_ids = _unique_strings(
        payload.get("arxiv_version_ids"),
        "arxiv_version_ids",
        maximum=MAX_ARXIV_VERSIONS,
        allow_empty=True,
    )
    if any(_ARXIV_VERSION_ID.fullmatch(item) is None for item in arxiv_ids):
        raise ProvenanceError("paper fetch arxiv_version_ids is invalid")
    forum_ids = _unique_strings(
        payload.get("openreview_forum_ids"),
        "openreview_forum_ids",
        maximum=MAX_OPENREVIEW_FORUMS,
        allow_empty=True,
    )
    if any(re.fullmatch(r"[A-Za-z0-9_-]{3,128}", item) is None for item in forum_ids):
        raise ProvenanceError("paper fetch openreview_forum_ids is invalid")
    if not arxiv_ids and not forum_ids:
        raise ProvenanceError("paper fetch request has no source identifiers")
    rate = payload.get("requests_per_second", 1.0)
    retries = payload.get("max_retries", MAX_RETRIES)
    fetch_arxiv_source = payload.get("fetch_arxiv_source", False)
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0 < float(rate) <= MAX_REQUESTS_PER_SECOND
    ):
        raise ProvenanceError("paper fetch requests_per_second is invalid")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 8
    ):
        raise ProvenanceError("paper fetch max_retries is invalid")
    if not isinstance(fetch_arxiv_source, bool):
        raise ProvenanceError("paper fetch fetch_arxiv_source is invalid")
    if fetch_arxiv_source and not arxiv_ids:
        raise ProvenanceError(
            "paper fetch fetch_arxiv_source requires arxiv_version_ids"
        )
    authorization = _authorization(payload.get("authorization"))
    required_actions: set[str] = set()
    if arxiv_ids:
        required_actions.add("fetch_arxiv_metadata")
    if fetch_arxiv_source:
        required_actions.add("fetch_arxiv_source")
    if forum_ids:
        required_actions.add("fetch_openreview_forum")
    if not required_actions.issubset(set(authorization["allowed_actions"])):
        raise ProvenanceError(
            "paper fetch request exceeds authorization.allowed_actions"
        )
    return {
        "user_agent": user_agent,
        "authorization": authorization,
        "arxiv_version_ids": arxiv_ids,
        "openreview_forum_ids": forum_ids,
        "requests_per_second": float(rate),
        "max_retries": retries,
        "fetch_arxiv_source": fetch_arxiv_source,
    }


class _Downloader:
    def __init__(
        self,
        *,
        user_agent: str,
        requests_per_second: float,
        max_retries: int,
        http_get: HttpGet,
        sleep: Callable[[float], None],
    ) -> None:
        self.headers = {"User-Agent": user_agent}
        self.minimum_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.http_get = http_get
        self.sleep = sleep
        self.last_request_at: float | None = None

    def get(
        self,
        url: str,
        *,
        source_name: str,
        content_types: set[str],
        max_response_bytes: int,
    ) -> HttpResponse:
        for attempt in range(self.max_retries + 1):
            now = time.monotonic()
            if self.last_request_at is not None:
                remaining = self.minimum_interval - (now - self.last_request_at)
                if remaining > 0:
                    self.sleep(remaining)
            self.last_request_at = time.monotonic()
            try:
                response = self.http_get(url, self.headers, 90.0)
                if not isinstance(response, HttpResponse):
                    raise ProvenanceError(
                        f"{source_name} client omitted response metadata"
                    )
                if (
                    isinstance(response.status, bool)
                    or not isinstance(response.status, int)
                    or not isinstance(response.final_url, str)
                    or not isinstance(response.content_type, str)
                    or not isinstance(response.redirect_chain, tuple)
                    or any(
                        not isinstance(redirect, HttpRedirect)
                        for redirect in response.redirect_chain
                    )
                ):
                    raise ProvenanceError(
                        f"{source_name} client returned invalid response metadata"
                    )
                raw = response.body
                if (
                    not isinstance(raw, bytes)
                    or not raw
                    or len(raw) > max_response_bytes
                ):
                    raise ProvenanceError(
                        f"{source_name} response is empty or exceeds size limit"
                    )
                if response.status in _RETRYABLE and attempt < self.max_retries:
                    self.sleep(2.0**attempt)
                    continue
                if response.status != 200:
                    raise ProvenanceError(
                        f"{source_name} request failed with HTTP {response.status}"
                    )
                if not response.redirect_chain:
                    if response.final_url != url:
                        raise ProvenanceError(
                            f"{source_name} response final URL is invalid"
                        )
                else:
                    expected_redirect = _allowed_redirect_url(url)
                    redirect = response.redirect_chain[0]
                    if (
                        len(response.redirect_chain) != 1
                        or expected_redirect is None
                        or redirect.status != 301
                        or redirect.from_url != url
                        or redirect.to_url != expected_redirect
                        or response.final_url != expected_redirect
                    ):
                        raise ProvenanceError(
                            f"{source_name} response redirect chain is invalid"
                        )
                content_type = response.content_type.partition(";")[0].strip().lower()
                if content_type not in content_types:
                    raise ProvenanceError(
                        f"{source_name} response content type is invalid"
                    )
                return HttpResponse(
                    body=raw,
                    status=response.status,
                    final_url=response.final_url,
                    content_type=content_type,
                    redirect_chain=response.redirect_chain,
                )
            except urllib.error.HTTPError as error:
                if error.code not in _RETRYABLE or attempt == self.max_retries:
                    raise ProvenanceError(
                        f"{source_name} request failed with HTTP {error.code}"
                    ) from error
                self.sleep(2.0**attempt)
            except urllib.error.URLError as error:
                if attempt == self.max_retries:
                    raise ProvenanceError(
                        f"{source_name} request failed: {error.reason}"
                    ) from error
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fact(source_text: str, *, fact_id: str, field: str, value: str) -> dict[str, Any]:
    quote = (
        f"{json.dumps(field, ensure_ascii=False)}: "
        f"{json.dumps(value, ensure_ascii=False)}"
    )
    if not value or source_text.count(quote) != 1:
        raise ProvenanceError(f"paper source does not uniquely bind {field}")
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": quote,
        "evidence_char_start": source_text.index(quote),
    }


def _atom_text(entry: ET.Element, name: str) -> str:
    value = entry.findtext(f"{{http://www.w3.org/2005/Atom}}{name}")
    return " ".join(str(value or "").split())


class _BoundedArchiveReader(io.RawIOBase):
    def __init__(self, stream: Any, compressed_size: int) -> None:
        super().__init__()
        self.stream = stream
        self.total = 0
        self.ratio_limit = compressed_size * MAX_ARCHIVE_EXPANSION_RATIO

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        limit = min(MAX_ARCHIVE_STREAM_BYTES, self.ratio_limit)
        remaining = limit - self.total
        bounded_size = remaining + 1 if size < 0 else min(size, remaining + 1)
        payload = self.stream.read(bounded_size)
        self.total += len(payload)
        if self.total > self.ratio_limit:
            raise ProvenanceError(
                "arXiv source archive compression ratio exceeds limit"
            )
        if self.total > MAX_ARCHIVE_STREAM_BYTES:
            raise ProvenanceError("arXiv source archive stream exceeds size limit")
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


def _latex_sources(raw: bytes) -> tuple[list[dict[str, str]], int]:
    stream = _decompressed_archive_stream(raw)
    try:
        archive_context = tarfile.open(  # noqa: SIM115 - closed immediately below
            fileobj=_BoundedArchiveReader(stream, len(raw)), mode="r|"
        )
        by_digest: dict[str, tuple[str, str]] = {}
        total_bytes = 0
        declared_bytes = 0
        member_count = 0
        with archive_context as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise ProvenanceError(
                        "arXiv source archive member count is invalid"
                    )
                if member.size < 0:
                    raise ProvenanceError(
                        "arXiv source archive declared size is invalid"
                    )
                declared_bytes += member.size
                if declared_bytes > MAX_ARCHIVE_DECLARED_BYTES:
                    raise ProvenanceError(
                        "arXiv source archive declared size exceeds limit"
                    )
                if declared_bytes > len(raw) * MAX_ARCHIVE_EXPANSION_RATIO:
                    raise ProvenanceError(
                        "arXiv source archive compression ratio exceeds limit"
                    )
                name = member.name
                path = PurePosixPath(name)
                if (
                    not name
                    or "\\" in name
                    or path.is_absolute()
                    or ".." in path.parts
                    or len(name) > 512
                ):
                    raise ProvenanceError("arXiv source archive has an unsafe member")
                if not member.isfile() or path.suffix.lower() != ".tex":
                    continue
                if member.size <= 0 or member.size > MAX_LATEX_FILE_BYTES:
                    raise ProvenanceError("arXiv LaTeX source file size is invalid")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ProvenanceError("arXiv LaTeX source file is unreadable")
                with handle:
                    payload = handle.read(MAX_LATEX_FILE_BYTES + 1)
                if len(payload) != member.size:
                    raise ProvenanceError("arXiv LaTeX source file size changed")
                total_bytes += len(payload)
                if total_bytes > MAX_LATEX_TOTAL_BYTES:
                    raise ProvenanceError("arXiv LaTeX source exceeds size limit")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ProvenanceError("arXiv LaTeX source is not UTF-8") from error
                digest = hashlib.sha256(payload).hexdigest()
                current = by_digest.get(digest)
                if current is None or (len(name), name) < (
                    len(current[0]),
                    current[0],
                ):
                    by_digest[digest] = (name, text)
    except (EOFError, OSError, lzma.LZMAError, tarfile.TarError) as error:
        raise ProvenanceError("arXiv source archive is malformed") from error
    finally:
        stream.close()
    if member_count == 0:
        raise ProvenanceError("arXiv source archive member count is invalid")
    if not by_digest:
        raise ProvenanceError("arXiv source archive has no LaTeX text")
    sources = [
        {"path": name, "sha256": digest, "text": text}
        for digest, (name, text) in sorted(
            by_digest.items(), key=lambda item: item[1][0]
        )
    ]
    return sources, sum(len(item["text"]) for item in sources)


def _arxiv_record(
    raw: bytes,
    expected_id: str,
    *,
    latex_sources: list[dict[str, str]] | None = None,
    request_sha256: str = "",
    retrieval: dict[str, Any] | None = None,
    source_archive: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bytes]:
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.IGNORECASE):
        raise ProvenanceError("arXiv response contains unsupported XML declarations")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ProvenanceError("arXiv response is invalid Atom XML") from error
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if len(entries) != 1:
        raise ProvenanceError("arXiv response must contain exactly one entry")
    entry = entries[0]
    entry_id = _atom_text(entry, "id")
    actual_id = entry_id.rstrip("/").rsplit("/", 1)[-1]
    allowed_entry_ids = {
        f"http://arxiv.org/abs/{expected_id}",
        f"https://arxiv.org/abs/{expected_id}",
    }
    if entry_id.rstrip("/") not in allowed_entry_ids or actual_id != expected_id:
        raise ProvenanceError("arXiv response identity does not match request")
    match = _ARXIV_VERSION_ID.fullmatch(actual_id)
    if match is None:
        raise ProvenanceError("arXiv response identity is invalid")
    published = _atom_text(entry, "published")
    updated = _atom_text(entry, "updated")
    _parse_timestamp(published, "arXiv published")
    _parse_timestamp(updated, "arXiv updated")
    title = _atom_text(entry, "title")
    summary = _atom_text(entry, "summary")
    if not title or not summary:
        raise ProvenanceError("arXiv response has no title or summary")
    authors = [
        " ".join(str(author.text or "").split())
        for author in entry.findall(
            "{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name"
        )
    ]
    if not authors or any(not author for author in authors):
        raise ProvenanceError("arXiv response has no authors")
    version_number = int(match.group("version"))
    source_payload: dict[str, Any] = {
        "authors": authors,
        "entry_id": f"https://arxiv.org/abs/{actual_id}",
        "kind": "arxiv_api_entry",
        "published": published,
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "summary": summary,
        "title": title,
        "updated": updated,
        "version": f"v{version_number}",
    }
    if request_sha256:
        source_payload["request_sha256"] = request_sha256
    if retrieval is not None:
        source_payload["retrieval"] = retrieval
    if version_number > 1:
        source_payload["previous_revision_id"] = f"v{version_number - 1}"
    if latex_sources is not None:
        source_payload["latex_sources"] = latex_sources
    if source_archive is not None:
        source_payload["source_archive"] = source_archive
    source = _canonical_bytes(source_payload)
    source_text = source.decode()
    record = {
        "record_id": f"arxiv:{actual_id}",
        "kind": "arxiv",
        "source_family": "arxiv_atom_api",
        "source_origin": "public_api",
        "work_id": f"arxiv:{match.group('base')}",
        "revision_id": f"v{version_number}",
        "previous_revision_id": source_payload.get("previous_revision_id"),
        "occurred_at": updated,
        "source_url": f"https://arxiv.org/abs/{actual_id}",
        "facts": [
            _fact(source_text, fact_id="title", field="title", value=title),
            _fact(source_text, fact_id="summary", field="summary", value=summary),
        ],
    }
    return record, source


def _millisecond_timestamp(value: object, field: str) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1_000_000_000_000
    ):
        raise ProvenanceError(f"OpenReview {field} timestamp is invalid")
    try:
        timestamp = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ProvenanceError(f"OpenReview {field} timestamp is invalid") from error
    return timestamp.isoformat().replace("+00:00", "Z")


def _content_values(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_OPENREVIEW_CONTENT_FIELDS:
        raise ProvenanceError("OpenReview note content is invalid")
    content: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 256:
            raise ProvenanceError("OpenReview note content key is invalid")
        raw_value = item.get("value") if isinstance(item, dict) else item
        if isinstance(raw_value, str) and raw_value.strip():
            clean_value = raw_value.strip()
            if len(clean_value.encode()) > MAX_OPENREVIEW_FIELD_BYTES:
                raise ProvenanceError("OpenReview content field exceeds size limit")
            content[key] = clean_value
    if not content:
        raise ProvenanceError("OpenReview note has no textual content")
    return content


def _openreview_records(
    raw: bytes,
    expected_forum: str,
    *,
    request_sha256: str = "",
    retrieval: dict[str, Any] | None = None,
) -> list[tuple[dict[str, Any], bytes]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("OpenReview response is invalid JSON") from error
    notes = payload.get("notes") if isinstance(payload, dict) else None
    if not isinstance(notes, list) or not notes:
        raise ProvenanceError("OpenReview response has no notes")
    if len(notes) > MAX_OPENREVIEW_NOTES:
        raise ProvenanceError("OpenReview response has too many notes")
    records: list[tuple[dict[str, Any], bytes]] = []
    seen: set[str] = set()
    total_text_bytes = 0
    for note in notes:
        if not isinstance(note, dict):
            raise ProvenanceError("OpenReview note is invalid")
        note_id = str(note.get("id") or "")
        forum = str(note.get("forum") or "")
        replyto_raw = note.get("replyto")
        replyto = str(replyto_raw) if replyto_raw else None
        invitation = str(note.get("invitation") or "")
        if (
            re.fullmatch(r"[A-Za-z0-9_-]{3,128}", note_id) is None
            or note_id in seen
            or forum != expected_forum
            or not invitation
            or len(invitation) > 512
            or (
                replyto is not None
                and re.fullmatch(r"[A-Za-z0-9_-]{3,128}", replyto) is None
            )
        ):
            raise ProvenanceError("OpenReview forum identity is invalid")
        seen.add(note_id)
        date_field = next(
            (field for field in ("pdate", "cdate", "mdate") if note.get(field)),
            None,
        )
        if date_field is None:
            raise ProvenanceError("OpenReview note timestamp is missing")
        occurred_at = _millisecond_timestamp(note[date_field], date_field)
        content = _content_values(note.get("content"))
        total_text_bytes += sum(len(value.encode()) for value in content.values())
        if total_text_bytes > MAX_OPENREVIEW_TOTAL_TEXT_BYTES:
            raise ProvenanceError("OpenReview derived text exceeds size limit")
        linked_arxiv_ids = sorted(
            {
                match.group("id")
                for text in content.values()
                for match in _ARXIV_LINK.finditer(text)
            }
        )
        source_payload = {
            "content": content,
            "forum": forum,
            "id": note_id,
            "invitation": invitation,
            "kind": "openreview_api_note",
            "occurred_at": occurred_at,
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
            "replyto": replyto,
        }
        if request_sha256:
            source_payload["request_sha256"] = request_sha256
        if retrieval is not None:
            source_payload["retrieval"] = retrieval
        source = _canonical_bytes(source_payload)
        source_text = source.decode()
        facts = [
            _fact(
                source_text,
                fact_id=f"content:{field}",
                field=field,
                value=value,
            )
            for field, value in sorted(content.items())
        ]
        records.append(
            (
                {
                    "record_id": f"openreview:{note_id}",
                    "kind": "openreview",
                    "source_family": "openreview_v2_api",
                    "source_origin": "public_api",
                    "work_id": f"openreview:{forum}",
                    "revision_id": note_id,
                    "forum_id": forum,
                    "replyto": replyto,
                    "invitation": invitation,
                    "occurred_at": occurred_at,
                    "linked_arxiv_ids": linked_arxiv_ids,
                    "source_url": (
                        f"https://openreview.net/forum?id={urllib.parse.quote(forum)}"
                        + (
                            ""
                            if note_id == forum
                            else f"&noteId={urllib.parse.quote(note_id)}"
                        )
                    ),
                    "facts": facts,
                },
                source,
            )
        )
    if expected_forum not in seen:
        raise ProvenanceError("OpenReview response omits the forum submission note")
    return records


def _retrieval_metadata(
    response: HttpResponse,
    *,
    requested_url: str,
) -> dict[str, Any]:
    return {
        "requested_url": requested_url,
        "final_url": response.final_url,
        "status": response.status,
        "content_type": response.content_type,
        "redirect_chain": [
            {
                "status": redirect.status,
                "from_url": redirect.from_url,
                "to_url": redirect.to_url,
            }
            for redirect in response.redirect_chain
        ],
        "sha256": hashlib.sha256(response.body).hexdigest(),
    }


def fetch_paper_workflow(
    request_path: Path,
    output_directory: Path,
    *,
    http_get: HttpGet = _default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    generated_at: str | None = None,
) -> Path:
    """Fetch official source bytes and atomically publish a disabled inventory."""
    try:
        request_raw = _read_regular_file(request_path, MAX_MANIFEST_BYTES)
        payload = json.loads(request_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read paper fetch request: {error}") from error
    if not isinstance(payload, dict):
        raise ProvenanceError("paper fetch request must be an object")
    request = _validate_request(payload)
    request_sha256 = hashlib.sha256(request_raw).hexdigest()
    timestamp = generated_at or _timestamp()
    _parse_timestamp(timestamp, "generated_at")
    if os.path.lexists(output_directory):
        raise ProvenanceError("paper fetch output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    downloader = _Downloader(
        user_agent=request["user_agent"],
        requests_per_second=request["requests_per_second"],
        max_retries=request["max_retries"],
        http_get=http_get,
        sleep=sleep,
    )

    with tempfile.TemporaryDirectory(
        dir=output_directory.parent, prefix=f".{output_directory.name}."
    ) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir(mode=0o700)
        os.chmod(staging, 0o700)
        request_name = "paper_fetch_request.json"
        _write(staging / request_name, request_raw)
        records: list[dict[str, Any]] = []
        retrievals: list[dict[str, Any]] = []

        for arxiv_id in request["arxiv_version_ids"]:
            retrieval_url = (
                "https://export.arxiv.org/api/query?"
                + urllib.parse.urlencode({"id_list": arxiv_id})
            )
            response = downloader.get(
                retrieval_url,
                source_name="arXiv",
                content_types=_ARXIV_ATOM_CONTENT_TYPES,
                max_response_bytes=MAX_ARXIV_ATOM_RESPONSE_BYTES,
            )
            retrieval = _retrieval_metadata(
                response,
                requested_url=retrieval_url,
            )
            raw_name = f"arxiv-{arxiv_id}.atom.xml"
            _write(staging / raw_name, response.body)
            archive_response: HttpResponse | None = None
            archive_retrieval: dict[str, Any] | None = None
            latex_sources: list[dict[str, str]] | None = None
            latex_char_count = 0
            archive_url = ""
            archive_name = ""
            if request["fetch_arxiv_source"]:
                archive_url = f"https://export.arxiv.org/e-print/{arxiv_id}"
                archive_response = downloader.get(
                    archive_url,
                    source_name="arXiv",
                    content_types=_ARXIV_ARCHIVE_CONTENT_TYPES,
                    max_response_bytes=MAX_PAPER_RESPONSE_BYTES,
                )
                archive_retrieval = {
                    **_retrieval_metadata(
                        archive_response,
                        requested_url=archive_url,
                    ),
                    "parser": ARXIV_ARCHIVE_PARSER_REVISION,
                }
                latex_sources, latex_char_count = _latex_sources(archive_response.body)
                archive_name = f"arxiv-{arxiv_id}.source.tar"
                _write(staging / archive_name, archive_response.body)
            record, source = _arxiv_record(
                response.body,
                arxiv_id,
                latex_sources=latex_sources,
                request_sha256=request_sha256,
                retrieval=retrieval,
                source_archive=archive_retrieval,
            )
            source_name = f"arxiv-{arxiv_id}.record.json"
            _write(staging / source_name, source)
            record.update(
                {
                    "source_file": source_name,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "request_sha256": request_sha256,
                    "retrieval_file": raw_name,
                    "retrieval_sha256": retrieval["sha256"],
                    "retrieval_requested_url": retrieval["requested_url"],
                    "retrieval_url": retrieval["final_url"],
                    "retrieval_status": retrieval["status"],
                    "retrieval_content_type": retrieval["content_type"],
                    "retrieval_redirect_chain": retrieval["redirect_chain"],
                    "retrieved_at": timestamp,
                    "parser": "arxiv_atom_v1",
                }
            )
            if archive_response is not None and archive_retrieval is not None:
                record.update(
                    {
                        "source_archive_file": archive_name,
                        "source_archive_sha256": archive_retrieval["sha256"],
                        "source_archive_requested_url": archive_retrieval[
                            "requested_url"
                        ],
                        "source_archive_url": archive_retrieval["final_url"],
                        "source_archive_status": archive_retrieval["status"],
                        "source_archive_content_type": archive_retrieval[
                            "content_type"
                        ],
                        "source_archive_redirect_chain": archive_retrieval[
                            "redirect_chain"
                        ],
                        "source_archive_parser": archive_retrieval["parser"],
                        "latex_char_count": latex_char_count,
                    }
                )
            records.append(record)
            retrievals.append(
                {
                    "source_family": "arxiv_atom_api",
                    **retrieval,
                    "retrieval_file": raw_name,
                }
            )
            if archive_response is not None and archive_retrieval is not None:
                retrievals.append(
                    {
                        "source_family": "arxiv_source_archive",
                        **archive_retrieval,
                        "retrieval_file": archive_name,
                    }
                )

        for forum_id in request["openreview_forum_ids"]:
            retrieval_url = (
                "https://api2.openreview.net/notes?"
                + urllib.parse.urlencode({"forum": forum_id})
            )
            response = downloader.get(
                retrieval_url,
                source_name="OpenReview",
                content_types=_OPENREVIEW_CONTENT_TYPES,
                max_response_bytes=MAX_OPENREVIEW_RESPONSE_BYTES,
            )
            retrieval = _retrieval_metadata(
                response,
                requested_url=retrieval_url,
            )
            raw_name = f"openreview-{forum_id}.notes.json"
            _write(staging / raw_name, response.body)
            for record, source in _openreview_records(
                response.body,
                forum_id,
                request_sha256=request_sha256,
                retrieval=retrieval,
            ):
                note_id = record["revision_id"]
                source_name = f"openreview-{note_id}.record.json"
                _write(staging / source_name, source)
                record.update(
                    {
                        "source_file": source_name,
                        "source_sha256": hashlib.sha256(source).hexdigest(),
                        "request_sha256": request_sha256,
                        "retrieval_file": raw_name,
                        "retrieval_sha256": retrieval["sha256"],
                        "retrieval_requested_url": retrieval["requested_url"],
                        "retrieval_url": retrieval["final_url"],
                        "retrieval_status": retrieval["status"],
                        "retrieval_content_type": retrieval["content_type"],
                        "retrieval_redirect_chain": retrieval["redirect_chain"],
                        "retrieved_at": timestamp,
                        "parser": "openreview_note_v2",
                    }
                )
                records.append(record)
            retrievals.append(
                {
                    "source_family": "openreview_v2_api",
                    **retrieval,
                    "retrieval_file": raw_name,
                }
            )

        inventory = {
            "schema_version": PAPER_FETCH_INVENTORY_SCHEMA,
            "source_status": "public_api_export",
            "data_stage": "source_inventory",
            "hybrid_train_ready": False,
            "production_eligible": False,
            "generation_integration": "disabled",
            "semantic_facts_train_ready": False,
            "generated_at": timestamp,
            "request_file": request_name,
            "request_sha256": request_sha256,
            "authorization": request["authorization"],
            "fetch_receipt": {
                "arxiv_policy_url": ARXIV_API_MANUAL,
                "openreview_api_definition_url": OPENREVIEW_API_DEFINITION,
                "requests_per_second": request["requests_per_second"],
                "max_retries": request["max_retries"],
                "fetch_arxiv_source": request["fetch_arxiv_source"],
                "allowed_actions": request["authorization"]["allowed_actions"],
                "request_file": request_name,
                "request_sha256": request_sha256,
                "user_agent_sha256": hashlib.sha256(
                    request["user_agent"].encode()
                ).hexdigest(),
                "retrievals": retrievals,
            },
            "n_records": len(records),
            "records": records,
        }
        inventory_path = staging / "paper_fetch_inventory.json"
        _write(inventory_path, _canonical_bytes(inventory))
        os.replace(staging, output_directory)
    return output_directory / "paper_fetch_inventory.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(fetch_paper_workflow(args.request, args.out_dir))


if __name__ == "__main__":
    main()
