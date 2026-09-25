"""Parameterised exact-revision Wiki freeze for a pinned width-rejection pool."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p104_freeze_width_revisions import (
    RateLimiter,
    SourceResponseError,
    _fetch,
    _request_url,
    _validate_response,
)

SCHEMA = "longworld.p106-wiki-exact-revision-freeze.v1"
PLAN_SCHEMA = "longworld.p106-wiki-raw-revision-plan.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P106 pin path must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P106 pinned input differs: {relative}")
    return path


def _document_inventory(sources: list[dict]) -> list[tuple[dict, dict, dict]]:
    rows = []
    for source in sources:
        snapshot = json.loads(_pin(source["snapshot"]).read_text())
        rows.extend((source, snapshot, doc) for doc in snapshot["documents"])
    return rows


def plan(config_path: Path) -> tuple[dict, list[dict], dict]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != PLAN_SCHEMA:
        raise ValueError("P106 config schema differs")
    pool = json.loads(_pin(config["source_pool"]).read_text())
    prior = json.loads(_pin(config["prior_p97_pool"]).read_text())
    gate = json.loads(_pin(config["source_gate"]).read_text())
    audit = [
        json.loads(line) for line in _pin(config["page_audit"]).read_text().splitlines()
    ]
    if pool.get("schema") != "longworld.source-batch-pool.v2" or not gate.get(
        "prior_router", {}
    ).get("sha256"):
        raise ValueError("P95 pool/global prior gate differs")
    if gate.get("accepted_groups") != len(pool["sources"]):
        raise ValueError("P95 pool source count differs from global gate")
    wanted = {
        (row["source_group"], row["doc_id"])
        for row in audit
        if any(
            item["reason"] == "row_width_mismatch" for item in row["rejected_tables"]
        )
    }
    gross = sum(
        item["reason"] == "row_width_mismatch"
        for row in audit
        for item in row["rejected_tables"]
    )
    prior_titles, prior_urls = {}, {}
    for source, _snapshot, doc in _document_inventory(prior["sources"]):
        prior_titles[doc["title"].casefold()] = source["split"]
        prior_urls[doc["page_url"]] = source["split"]
    jobs = []
    titles, urls = {}, {}
    for source, snapshot, doc in _document_inventory(pool["sources"]):
        if (source["name"], doc["doc_id"]) not in wanted:
            continue
        title_key = doc["title"].casefold()
        url_key = doc["page_url"]
        if title_key in prior_titles or url_key in prior_urls:
            raise ValueError(f"P106 source duplicates P97 title/URL: {doc['title']}")
        if title_key in titles or url_key in urls:
            raise ValueError(f"P106 candidate duplicates title/URL: {doc['title']}")
        titles[title_key] = source["split"]
        urls[url_key] = source["split"]
        match = re.fullmatch(
            r"https://en\.wikipedia\.org/w/index\.php\?oldid=(\d+)", doc["revision_url"]
        )
        if match is None:
            raise ValueError(f"P106 revision URL cannot be pinned: {doc['title']}")
        jobs.append(
            {
                "doc_id": doc["doc_id"],
                "source_group": source["name"],
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
    jobs.sort(key=lambda row: (row["source_group"], row["doc_id"]))
    if len(jobs) != len(wanted):
        raise ValueError("P106 width audit pages missing from source pool")
    support = {
        "gross_width_rejections": gross,
        "affected_pages": len(jobs),
        "affected_source_groups": len({row["source_group"] for row in jobs}),
        "split_pages": dict(sorted(Counter(row["split"] for row in jobs).items())),
        "domain_pages": dict(sorted(Counter(row["domain"] for row in jobs).items())),
        "prior_p97_title_url_overlap": 0,
    }
    return config, jobs, support


def _verify_completed(
    manifest: dict, config: dict, jobs: list[dict], destination: Path
) -> bool:
    if manifest.get("schema") != SCHEMA:
        raise ValueError("P106 existing manifest schema differs")
    if manifest.get("failed_pages") or manifest.get("frozen_pages") != len(jobs):
        return False
    if manifest.get("planned_pages") != len(jobs) or manifest.get("source_pins") != {
        name: config[name]
        for name in ("source_pool", "page_audit", "source_gate", "prior_p97_pool")
    }:
        raise ValueError("P106 completed manifest source pins differ")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != len(jobs):
        raise ValueError("P106 completed manifest records differ")
    by_key = {(job["doc_id"], job["revid"]): job for job in jobs}
    seen = set()
    for record in records:
        key = (record.get("doc_id"), record.get("revid"))
        if key not in by_key or key in seen:
            raise ValueError("P106 completed manifest has duplicate/foreign revision")
        seen.add(key)
        job = by_key[key]
        if any(
            record.get(field) != value
            for field, value in job.items()
            if field != "user_agent"
        ):
            raise ValueError("P106 completed source metadata changed")
        expected_path = f"responses/{job['doc_id']}-r{job['revid']}.json"
        if record.get("response_path") != expected_path:
            raise ValueError("P106 response path changed")
        details = _validate_response((destination / expected_path).read_bytes(), job)
        if any(
            record.get(field) != value for field, value in details.items()
        ) or record.get("request_url") != _request_url(job["revid"]):
            raise ValueError("P106 response hash/revision changed")
    return True


def run(config_path: Path, destination: Path) -> dict:
    config, jobs, support = plan(config_path)
    workers = config.get("max_workers")
    rate = config.get("requests_per_second")
    if (
        not isinstance(workers, int)
        or not 1 <= workers <= 4
        or not isinstance(rate, (int, float))
        or not 0 < rate <= 2
    ):
        raise ValueError("P106 workers/rate exceed bounded source pilot")
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".freeze.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("P106 output already has a writer") from error
        try:
            output = destination / "manifest.json"
            if output.exists():
                existing = json.loads(output.read_text())
                if _verify_completed(existing, config, jobs, destination):
                    return existing
            limiter = RateLimiter(float(rate))
            accepted, failed = [], []
            with ThreadPoolExecutor(max_workers=workers) as executor:
                pending = {
                    executor.submit(_fetch, job, destination, limiter): job
                    for job in jobs
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
                        failed.append(
                            {
                                "doc_id": job["doc_id"],
                                "title": job["title"],
                                "error_type": type(error).__name__,
                                "error": str(error),
                            }
                        )
            manifest = {
                "schema": SCHEMA,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_pins": {
                    name: config[name]
                    for name in (
                        "source_pool",
                        "page_audit",
                        "source_gate",
                        "prior_p97_pool",
                    )
                },
                "plan_support": support,
                "planned_pages": len(jobs),
                "frozen_pages": len(accepted),
                "failed_pages": len(failed),
                "workers": workers,
                "requests_per_second": rate,
                "records": sorted(
                    accepted, key=lambda row: (row["source_group"], row["doc_id"])
                ),
                "failures": sorted(failed, key=lambda row: row["doc_id"]),
            }
            temporary = output.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            )
            temporary.replace(output)
            return manifest
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config, args.output.resolve())
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "planned_pages",
                    "frozen_pages",
                    "failed_pages",
                    "plan_support",
                )
            },
            sort_keys=True,
        )
    )
    if result["failed_pages"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
