"""Freeze exact Wiki revisions for P97 pages with table-width rejections.

Responses are cached one revision per file. A rerun verifies cached bytes and
revision identity before fetching missing entries; it never silently refreshes
an existing revision. This is source acquisition only, not reader admission.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "data/capability_records/p97_wiki_gated_delta_v1/source_pool.json"
AUDIT = ROOT / "data/candidates/p100_wiki_categorical_scan_v3/page_audit.jsonl"
POOL_SHA256 = "094353d647ae888821ede5683fd912d125f336018317ffc9364b40b7cd840268"
AUDIT_SHA256 = "8e3e008858a122d4a722c96d132f24d21bf9eeef5897417e4053bf09de379846"
API = "https://en.wikipedia.org/w/api.php"
SCHEMA = "longworld.p104-wiki-exact-revision-freeze.v1"
MAX_RESPONSE_BYTES = 4_000_000


class SourceResponseError(Exception):
    """An HTTP response failed the pinned source contract."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_pinned(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if _sha(raw) != expected:
        raise ValueError(f"pinned source changed: {path}")
    return raw


def plan() -> list[dict[str, Any]]:
    """Enumerate distinct affected pages from the pinned audit and source pool."""
    pool = json.loads(_read_pinned(POOL, POOL_SHA256))
    audit = [json.loads(row) for row in _read_pinned(AUDIT, AUDIT_SHA256).splitlines()]
    sources = {source["name"]: source for source in pool["sources"]}
    affected = {
        (row["source_group"], row["doc_id"])
        for row in audit
        if any(
            item["reason"] == "row_width_mismatch" for item in row["rejected_tables"]
        )
    }
    jobs = []
    for source_group, doc_id in sorted(affected):
        source = sources[source_group]
        snapshot_path = ROOT / source["snapshot"]["path"]
        snapshot = json.loads(_read_pinned(snapshot_path, source["snapshot"]["sha256"]))
        doc = next(doc for doc in snapshot["documents"] if doc["doc_id"] == doc_id)
        match = re.fullmatch(
            r"https://en\.wikipedia\.org/w/index\.php\?oldid=(\d+)", doc["revision_url"]
        )
        if match is None:
            raise ValueError(f"unrecognized pinned revision URL: {doc['revision_url']}")
        jobs.append(
            {
                "doc_id": doc_id,
                "source_group": source_group,
                "domain": source["domain"],
                "topic": source["topic"],
                "split": source["split"],
                "title": doc["title"],
                "page_url": doc["page_url"],
                "revision_url": doc["revision_url"],
                "revid": int(match.group(1)),
                "source_snapshot_path": source["snapshot"]["path"],
                "source_snapshot_sha256": source["snapshot"]["sha256"],
                "license": snapshot["source"]["license"],
                "user_agent": snapshot["source"]["user_agent"],
            }
        )
    return jobs


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
        if delay:
            time.sleep(delay)


def _request_url(revid: int) -> str:
    return (
        API
        + "?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "revids": revid,
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "format": "json",
                "formatversion": 2,
            }
        )
    )


def _validate_response(raw: bytes, job: dict[str, Any]) -> dict[str, Any]:
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("revision response empty or too large")
    payload = json.loads(raw)
    pages = payload.get("query", {}).get("pages", [])
    if len(pages) != 1 or payload.get("error"):
        raise ValueError("revision response has missing/ambiguous page or API error")
    page = pages[0]
    revisions = page.get("revisions", [])
    if len(revisions) != 1:
        raise ValueError("revision response has missing/ambiguous revision")
    revision = revisions[0]
    main = revision.get("slots", {}).get("main", {})
    content = main.get("content")
    if (
        page.get("title") != job["title"]
        or revision.get("revid") != job["revid"]
        or not isinstance(page.get("pageid"), int)
        or not isinstance(content, str)
        or not content
        or main.get("contentmodel") != "wikitext"
    ):
        raise ValueError("revision identity/content mismatch")
    return {
        "pageid": page["pageid"],
        "revid": revision["revid"],
        "revision_timestamp": revision.get("timestamp"),
        "response_sha256": _sha(raw),
        "response_bytes": len(raw),
        "wikitext_sha256": _sha(content.encode("utf-8")),
        "wikitext_chars": len(content),
    }


def _fetch(
    job: dict[str, Any], destination: Path, limiter: RateLimiter
) -> dict[str, Any]:
    path = destination / "responses" / f"{job['doc_id']}-r{job['revid']}.json"
    url = _request_url(job["revid"])
    if path.exists():
        raw = path.read_bytes()
        origin = "verified-cache"
    else:
        raw = b""
        for attempt in range(3):
            limiter.wait()
            request = urllib.request.Request(
                url,
                headers={"User-Agent": job["user_agent"], "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                break
            except urllib.error.HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                retry_after = error.headers.get("Retry-After")
                time.sleep(
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else 2**attempt
                )
            except urllib.error.URLError:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        try:
            _validate_response(raw, job)
        except (ValueError, json.JSONDecodeError) as error:
            raise SourceResponseError(str(error)) from error
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
        origin = "live-api"
    try:
        details = _validate_response(raw, job)
    except (ValueError, json.JSONDecodeError) as error:
        raise SourceResponseError(str(error)) from error
    return {
        **{key: value for key, value in job.items() if key != "user_agent"},
        **details,
        "request_url": url,
        "response_path": str(path.relative_to(destination)),
        "fetched_via": origin,
    }


def _verify_completed_manifest(
    manifest: dict[str, Any], jobs: list[dict[str, Any]], destination: Path
) -> bool:
    if manifest.get("schema") != SCHEMA:
        raise ValueError("existing freeze manifest has the wrong schema")
    if manifest.get("failed_pages") or manifest.get("frozen_pages") != len(jobs):
        return False
    if (
        manifest.get("planned_pages") != len(jobs)
        or manifest.get("source_pool_sha256") != POOL_SHA256
        or manifest.get("page_audit_sha256") != AUDIT_SHA256
    ):
        raise ValueError("existing completed manifest has different frozen inputs")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != len(jobs):
        raise ValueError("existing completed manifest has incomplete records")
    by_job = {(job["doc_id"], job["revid"]): job for job in jobs}
    seen = set()
    for record in records:
        key = (record.get("doc_id"), record.get("revid"))
        if key not in by_job or key in seen:
            raise ValueError(
                "existing completed manifest has unexpected/duplicate revision"
            )
        seen.add(key)
        job = by_job[key]
        for field in job.keys() - {"user_agent"}:
            if record.get(field) != job[field]:
                raise ValueError(
                    f"existing completed manifest source mismatch: {field}"
                )
        expected_path = f"responses/{job['doc_id']}-r{job['revid']}.json"
        if record.get("response_path") != expected_path:
            raise ValueError("existing completed manifest has unexpected response path")
        raw = (destination / expected_path).read_bytes()
        details = _validate_response(raw, job)
        if any(record.get(field) != value for field, value in details.items()):
            raise ValueError(
                "existing completed manifest response hash/identity changed"
            )
        if record.get("request_url") != _request_url(job["revid"]):
            raise ValueError("existing completed manifest request URL changed")
    return True


def _run_locked(
    destination: Path,
    jobs: list[dict[str, Any]],
    workers: int,
    requests_per_second: float,
) -> dict[str, Any]:
    output = destination / "manifest.json"
    if output.exists():
        existing = json.loads(output.read_text())
        if _verify_completed_manifest(existing, jobs, destination):
            return existing
    limiter = RateLimiter(requests_per_second)
    accepted = []
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(_fetch, job, destination, limiter): job for job in jobs
        }
        for future in as_completed(pending):
            job = pending[future]
            try:
                accepted.append(future.result())
            except (
                SourceResponseError,
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
            ) as error:
                failures.append(
                    {
                        "doc_id": job["doc_id"],
                        "title": job["title"],
                        "revid": job["revid"],
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
    manifest = {
        "schema": SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_pool_sha256": POOL_SHA256,
        "page_audit_sha256": AUDIT_SHA256,
        "planned_pages": len(jobs),
        "frozen_pages": len(accepted),
        "failed_pages": len(failures),
        "workers": workers,
        "requests_per_second": requests_per_second,
        "records": sorted(
            accepted, key=lambda row: (row["source_group"], row["doc_id"])
        ),
        "failures": sorted(failures, key=lambda row: row["doc_id"]),
    }
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    return manifest


def run(destination: Path, workers: int, requests_per_second: float) -> dict[str, Any]:
    if workers < 1 or workers > 4 or not 0 < requests_per_second <= 2:
        raise ValueError("pilot requires 1-4 workers and at most 2 requests/s")
    jobs = plan()
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".freeze.lock").open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                f"another freeze owns output directory: {destination}"
            ) from error
        try:
            return _run_locked(destination, jobs, workers, requests_per_second)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/candidates/p104_wiki_width_raw_v1"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--requests-per-second", type=float, default=2.0)
    args = parser.parse_args()
    manifest = run(args.output.resolve(), args.workers, args.requests_per_second)
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in ("planned_pages", "frozen_pages", "failed_pages")
            },
            indent=2,
        )
    )
    if manifest["failed_pages"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
