"""Discover a bounded arXiv work cohort and optionally fetch its last two sources.

Metadata and source requests are sequential and paced at >=3 seconds. The
download stage reuses the existing provenance-preserving paper fetcher. This
tool records failures instead of manufacturing a source cohort from stale data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.p66_researchlab_taskbank import load_text_tar
from longworld.core.provenance import ProvenanceError
from scripts.fetch_paper_workflow import fetch_paper_workflow
from scripts.run_p86_frozen_paper_batch import _dedupe, _quality_hunks

SCHEMA = "longworld.p87-arxiv-bounded-acquisition.v1"
ATOM = "http://www.w3.org/2005/Atom"
ARXIV_ID = re.compile(r"(?:https?://arxiv\.org/abs/)(\d{4}\.\d{4,5})v([1-9]\d*)\Z")
MAX_METADATA_BYTES = 4_000_000


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _split(work_id: str) -> str:
    residue = int.from_bytes(hashlib.sha256(work_id.encode()).digest()[:8], "big") % 10
    return "eval" if residue < 2 else "train"


def _parse_atom(raw: bytes, category: str) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    if root.tag != f"{{{ATOM}}}feed":
        raise ValueError("arXiv response is not an Atom feed")
    entries = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        identifier = entry.findtext(f"{{{ATOM}}}id") or ""
        match = ARXIV_ID.fullmatch(identifier)
        if match is None:
            continue
        license_links = [
            link.attrib["href"]
            for link in entry.findall(f"{{{ATOM}}}link")
            if link.attrib.get("rel") == "license" and link.attrib.get("href")
        ]
        for element in entry:
            if element.tag.rsplit("}", 1)[-1] == "license":
                license_links.extend(
                    value
                    for value in (element.attrib.get("href"), element.text)
                    if value
                )
        entries.append(
            {
                "work_id": match.group(1),
                "latest_version": int(match.group(2)),
                "category_query": category,
                "title": " ".join((entry.findtext(f"{{{ATOM}}}title") or "").split()),
                "updated": entry.findtext(f"{{{ATOM}}}updated"),
                "license_status": "reported"
                if license_links
                else "not_reported_by_atom",
                "license_uri": sorted(set(license_links)),
                "source_url": identifier,
            }
        )
    return entries


def _http_get(url: str, user_agent: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            if response.geturl() != url or response.status != 200:
                raise ValueError("arXiv metadata redirect or HTTP status changed")
            raw = response.read(MAX_METADATA_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(MAX_METADATA_BYTES + 1)
        raise ValueError(
            f"arXiv metadata HTTP {error.code}; response_sha256={_sha_bytes(body)}"
        ) from error
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("arXiv metadata response exceeds bound")
    return raw


def _capacity_probe(
    source_dir: Path, selected: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    reports = []
    for work in selected:
        old_version, new_version = work["versions"]
        before = source_dir / f"arxiv-{work['work_id']}{old_version}.source.tar"
        after = source_dir / f"arxiv-{work['work_id']}{new_version}.source.tar"
        if not before.is_file() or not after.is_file():
            reports.append(
                {"work_id": work["work_id"], "status": "source_archive_missing"}
            )
            continue
        old, old_duplicates = _dedupe(load_text_tar(before))
        new, new_duplicates = _dedupe(load_text_tar(after))
        shared = old.keys() & new.keys()
        changed = [path for path in shared if old[path] != new[path]]
        quality = [
            path for path in changed if _quality_hunks(old[path], new[path], 120)
        ]
        reports.append(
            {
                "work_id": work["work_id"],
                "split": work["split"],
                "versions": work["versions"],
                "status": "parsed_source_capacity_only",
                "shared_files": len(shared),
                "changed_files": len(changed),
                "quality_prose_files": len(quality),
                "duplicate_copy_paths_removed": old_duplicates + new_duplicates,
                "source_char_capacity": sum(
                    len(old[path]) + len(new[path]) for path in shared
                ),
                "actual_final_reader_tokens_measured": False,
            }
        )
    return reports


def acquire(
    config_path: Path,
    output_dir: Path,
    *,
    fetch_sources: bool,
    http_get: Callable[[str, str], bytes] = _http_get,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    if (
        config.get("schema") != SCHEMA
        or config.get("api_url") != "https://export.arxiv.org/api/query"
        or config.get("minimum_request_interval_seconds", 0) < 3
        or not 1 <= config.get("metadata_request_limit", 0) <= 8
        or not 1 <= config.get("page_size", 0) <= 100
        or not 1 <= config.get("source_archive_limit", 0) <= 8
        or not 1 <= config.get("source_work_limit", 0) <= 4
        or config.get("source_work_limit", 0) * 2 > config["source_archive_limit"]
        or config.get("sort_by") not in {"submittedDate", "lastUpdatedDate"}
        or config.get("sort_order") not in {"ascending", "descending"}
        or config.get("content_use") != "local_research_only_no_redistribution"
        or not 1 <= len(config.get("categories", []))
        or len(config.get("categories", [])) > config.get("metadata_request_limit", 0)
        or len(config.get("categories", [])) != len(set(config.get("categories", [])))
        or not all(
            re.fullmatch(r"[a-z-]+\.[A-Za-z-]+", category)
            for category in config.get("categories", [])
        )
    ):
        raise ValueError("unsafe acquisition configuration")
    output_dir.mkdir(parents=True)
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir()
    last_request: float | None = None
    pages = []
    discovered = []
    errors = []
    for category in config["categories"]:
        params = {
            "search_query": "cat:" + category,
            "sortBy": config["sort_by"],
            "sortOrder": config["sort_order"],
            "start": 0,
            "max_results": config["page_size"],
        }
        url = config["api_url"] + "?" + urllib.parse.urlencode(params)
        if last_request is not None:
            remaining = config["minimum_request_interval_seconds"] - (
                monotonic() - last_request
            )
            if remaining > 0:
                sleep(remaining)
        last_request = monotonic()
        try:
            raw = http_get(url, config["user_agent"])
            entries = _parse_atom(raw, category)
        except (OSError, ValueError, ET.ParseError) as error:
            errors.append(
                {
                    "category": category,
                    "url": url,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            break
        filename = category.replace(".", "_").replace("-", "_") + ".atom.xml"
        (metadata_dir / filename).write_bytes(raw)
        pages.append(
            {
                "category": category,
                "url": url,
                "file": "metadata/" + filename,
                "sha256": _sha_bytes(raw),
                "entries": len(entries),
            }
        )
        discovered.extend(entries)
    by_work = {}
    for item in discovered:
        prior = by_work.get(item["work_id"])
        if prior is None or item["latest_version"] > prior["latest_version"]:
            by_work[item["work_id"]] = item
    candidates = [
        item
        for item in by_work.values()
        if item["latest_version"] >= config["minimum_latest_version"]
        and item["work_id"] not in config["exclude_work_ids"]
    ]
    candidates.sort(
        key=lambda item: (
            config["categories"].index(item["category_query"]),
            item["work_id"],
        )
    )
    selected = []
    chosen_categories = set()
    if not errors:
        for item in candidates:
            if item["category_query"] in chosen_categories:
                continue
            latest = item["latest_version"]
            selected.append(
                {
                    **item,
                    "split": _split(item["work_id"]),
                    "versions": [f"v{latest - 1}", f"v{latest}"],
                }
            )
            chosen_categories.add(item["category_query"])
            if len(selected) >= config["source_work_limit"]:
                break
    request_status = "not_requested"
    capacity = []
    if fetch_sources and selected:
        request = {
            "schema_version": "longworld.paper-fetch-request.v1",
            "user_agent": config["user_agent"],
            "authorization": {
                "record_id": "P87-BOUND-ARXIV-LOCAL-RESEARCH",
                "scope": "at most eight source archives for local research capacity probe",
                "basis": "project owner requested bounded public-source acquisition for LongWorld research",
                "reviewed_at": _now(),
                "allowed_actions": ["fetch_arxiv_metadata", "fetch_arxiv_source"],
            },
            "arxiv_version_ids": [
                work["work_id"] + version
                for work in selected
                for version in work["versions"]
            ],
            "openreview_forum_ids": [],
            "fetch_arxiv_source": True,
            "requests_per_second": 1 / config["minimum_request_interval_seconds"],
            "max_retries": 0,
        }
        _write(output_dir / "paper_fetch_request.json", request)
        # The reused fetcher has its own sequential limiter, so bridge the
        # interval after the last metadata request before invoking it.
        if last_request is not None:
            remaining = config["minimum_request_interval_seconds"] - (
                monotonic() - last_request
            )
            if remaining > 0:
                sleep(remaining)
        try:
            fetch_paper_workflow(
                output_dir / "paper_fetch_request.json",
                output_dir / "source_inventory",
                sleep=sleep,
            )
            request_status = "fetched_local_research"
            capacity = _capacity_probe(output_dir / "source_inventory", selected)
        except (OSError, ValueError, ProvenanceError, tarfile.TarError) as error:
            request_status = "fetch_failed"
            errors.append(
                {
                    "stage": "source_fetch",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    if errors and not pages:
        status = "metadata_failed"
    elif errors and request_status == "not_requested":
        status = "metadata_partial_failed"
    elif request_status == "fetch_failed":
        status = "source_fetch_failed"
    elif request_status == "not_requested":
        status = "metadata_only"
    else:
        status = request_status
    acquired_archives = 0
    if request_status == "fetched_local_research":
        acquired_archives = len(
            list((output_dir / "source_inventory").glob("*.source.tar"))
        )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha_bytes(config_raw),
        "status": status,
        "metadata_pages": pages,
        "metadata_entries": len(discovered),
        "distinct_works": len(by_work),
        "multi_revision_nonexisting_candidates": len(candidates),
        "selected_works": selected,
        "source_archives_planned": len(selected) * 2 if fetch_sources else 0,
        "source_archives_acquired": acquired_archives,
        "source_fetch_status": request_status,
        "capacity_probe": capacity,
        "errors": errors,
        "single_connection": True,
        "minimum_request_interval_seconds": config["minimum_request_interval_seconds"],
        "content_use": config["content_use"],
        "train_ready": False,
    }
    _write(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--fetch-sources", action="store_true")
    args = parser.parse_args()
    manifest = acquire(args.config, args.output_dir, fetch_sources=args.fetch_sources)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 1 if manifest["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
