"""Freeze a bounded, resumable multi-query arXiv metadata cohort.

Network requests are sequential and globally spaced by at least the configured
interval. Metadata only proposes source works; it never creates a QA target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.acquire_p87_arxiv_sources import _parse_atom
from scripts.p104_paper_source_discovery import _inventory_row, _split
from scripts.p105_paper_http import get_metadata
from scripts.run_p86_frozen_paper_batch import _inventory

SCHEMA = "longworld.p105-paper-catalog.v1"
API = "https://export.arxiv.org/api/query"
QUERY = re.compile(r"cat:[a-z-]+\.[A-Za-z-]+\Z")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _path(raw: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P105 catalog paths must be workspace-relative")
    return ROOT / relative


def _pin(pin: dict[str, str]) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P105 catalog pin requires path and sha256")
    path = _path(pin["path"])
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P105 catalog pin drift: {pin['path']}")
    return path


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _config(path: Path) -> dict:
    value = json.loads(path.read_text())
    queries = value.get("queries")
    if (
        value.get("schema") != SCHEMA + ".config"
        or value.get("api_url") != API
        or value.get("content_use") != "local_research_only_no_redistribution"
        or type(value.get("request_interval_seconds")) not in (float, int)
        or value["request_interval_seconds"] < 3.2
        or type(value.get("page_size")) is not int
        or not 1 <= value["page_size"] <= 100
        or type(value.get("per_query_work_limit")) is not int
        or not 1 <= value["per_query_work_limit"] <= 4
        or type(value.get("work_limit")) is not int
        or not 1 <= value["work_limit"] <= 20
        or type(value.get("minimum_latest_version")) is not int
        or value["minimum_latest_version"] < 2
        or not isinstance(queries, list)
        or not 2 <= len(queries) <= 10
        or len(queries) != len(set(queries))
        or any(
            not isinstance(query, str) or QUERY.fullmatch(query) is None
            for query in queries
        )
        or "@" not in value.get("user_agent", "")
    ):
        raise ValueError("P105 catalog config exceeds bounded API contract")
    return value


def _prior_work_ids(config: dict) -> tuple[set[str], dict[str, str]]:
    ids: set[str] = set()
    inventory_pins: dict[str, str] = {}
    for pin in config["prior_source_configs"]:
        source = json.loads(_pin(pin).read_text())
        if source.get("schema") != "longworld.p86-frozen-paper-batch.v1":
            raise ValueError("P105 prior source config schema mismatch")
        ids.update(_inventory(entry)["work_id"] for entry in source["families"])
    index_path = _pin(config["prior_candidate_index"])
    index = json.loads(index_path.read_text())
    if _sha(index_path.parent / "candidate_refs.jsonl") != index["refs_sha256"]:
        raise ValueError("P105 prior candidate index changed")
    for line in (index_path.parent / "candidate_refs.jsonl").read_text().splitlines():
        source_group = json.loads(line)["candidate"].get("source_group", "")
        if source_group.startswith("researchlab:arxiv:"):
            ids.add(source_group.rsplit(":", 1)[-1])
    inventory_root = _path(config["frozen_inventory_root"])
    for path in sorted(inventory_root.rglob("paper_fetch_inventory.json")):
        row = _inventory_row(path)
        inventory_pins[row["inventory"]] = _sha(path)
        if "work_id" in row:
            ids.add(row["work_id"])
    return ids, inventory_pins


def _page_url(config: dict, query: str) -> str:
    return (
        API
        + "?"
        + urllib.parse.urlencode(
            {
                "search_query": query,
                "sortBy": "lastUpdatedDate",
                "sortOrder": "descending",
                "start": 0,
                "max_results": config["page_size"],
            }
        )
    )


def _read_page(
    output_dir: Path, index: int, query: str, url: str
) -> tuple[dict, list[dict]] | None:
    directory = output_dir / "metadata" / f"query_{index:03d}"
    if not directory.exists():
        return None
    if {path.name for path in directory.iterdir()} != {
        "response.atom.xml",
        "receipt.json",
    }:
        raise ValueError("P105 cached metadata page inventory drift")
    receipt = json.loads((directory / "receipt.json").read_text())
    raw = (directory / "response.atom.xml").read_bytes()
    if (
        receipt["query"] != query
        or receipt["url"] != url
        or receipt["sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("P105 cached metadata request or bytes changed")
    entries = _parse_atom(raw, query)
    if receipt["entries"] != len(entries):
        raise ValueError("P105 cached Atom parse count changed")
    return receipt, entries


def _write_page(
    output_dir: Path, index: int, query: str, url: str, raw: bytes
) -> tuple[dict, list[dict]]:
    entries = _parse_atom(raw, query)
    receipt = {
        "query": query,
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "entries": len(entries),
        "retrieved_at": _now(),
        "license_reported_entries": sum(
            row["license_status"] == "reported" for row in entries
        ),
    }
    parent = output_dir / "metadata"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".p105_metadata_", dir=parent) as temporary:
        staging = Path(temporary) / "page"
        staging.mkdir()
        (staging / "response.atom.xml").write_bytes(raw)
        (staging / "receipt.json").write_text(_dump(receipt))
        os.rename(staging, parent / f"query_{index:03d}")
    return receipt, entries


def _select(
    config: dict, pages: list[tuple[dict, list[dict]]], prior_ids: set[str]
) -> tuple[list[dict], dict]:
    by_work: dict[str, dict] = {}
    for receipt, entries in pages:
        for entry in entries:
            previous = by_work.get(entry["work_id"])
            if previous is None or entry["latest_version"] > previous["latest_version"]:
                by_work[entry["work_id"]] = entry
    selected = []
    seen = set()
    reasons = Counter()
    for query in config["queries"]:
        accepted_here = 0
        candidates = [row for row in by_work.values() if row["category_query"] == query]
        for item in candidates:
            work_id = item["work_id"]
            if work_id in prior_ids:
                reasons["prior_work"] += 1
                continue
            if item["latest_version"] < config["minimum_latest_version"]:
                reasons["single_revision"] += 1
                continue
            if work_id in seen:
                reasons["cross_query_duplicate"] += 1
                continue
            selected.append(
                {
                    **item,
                    "split": _split(work_id),
                    "versions": [
                        f"v{item['latest_version'] - 1}",
                        f"v{item['latest_version']}",
                    ],
                }
            )
            seen.add(work_id)
            accepted_here += 1
            if (
                accepted_here >= config["per_query_work_limit"]
                or len(selected) >= config["work_limit"]
            ):
                break
        if len(selected) >= config["work_limit"]:
            break
    return selected, {
        "distinct_metadata_works": len(by_work),
        "selection_rejections": dict(sorted(reasons.items())),
    }


def run(
    config_path: Path,
    output_dir: Path,
    *,
    resume: bool = False,
    http_get=get_metadata,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> dict:
    config = _config(config_path)
    config_sha = _sha(config_path)
    prior_ids, inventory_pins = _prior_work_ids(config)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    lock = {
        "schema": SCHEMA + ".lock",
        "config_sha256": config_sha,
        "prior_inventory_sha256": inventory_pins,
    }
    if output_dir.exists():
        if not resume or json.loads((output_dir / "lock.json").read_text()) != lock:
            raise ValueError("P105 metadata resume lock changed")
    else:
        if resume:
            raise ValueError("P105 cannot resume missing metadata cohort")
        output_dir.mkdir(parents=True)
        (output_dir / "lock.json").write_text(_dump(lock))
    pages = []
    errors = []
    # Conservative startup gap also covers a restart immediately after a
    # previous process's final request.
    last_request = monotonic()
    for index, query in enumerate(config["queries"]):
        url = _page_url(config, query)
        cached = _read_page(output_dir, index, query, url)
        if cached is not None:
            pages.append(cached)
            continue
        if last_request is not None:
            remaining = config["request_interval_seconds"] - (
                monotonic() - last_request
            )
            if remaining > 0:
                sleep(remaining)
        last_request = monotonic()
        try:
            raw = http_get(url, config["user_agent"])
            pages.append(_write_page(output_dir, index, query, url, raw))
        except (OSError, ValueError) as error:
            errors.append(
                {
                    "query": query,
                    "url": url,
                    "error_type": type(error).__name__,
                    "reason": str(error),
                }
            )
            break
    selected, coverage = (
        _select(config, pages, prior_ids)
        if not errors and len(pages) == len(config["queries"])
        else (
            [],
            {
                "distinct_metadata_works": len(
                    {row["work_id"] for _receipt, entries in pages for row in entries}
                ),
                "selection_rejections": {},
            },
        )
    )
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": config_sha,
        "status": "complete"
        if not errors and len(pages) == len(config["queries"])
        else "metadata_failed",
        "query_pages": [receipt for receipt, _entries in pages],
        "metadata_entries": sum(len(entries) for _receipt, entries in pages),
        **coverage,
        "prior_work_ids": len(prior_ids),
        "selected_works": selected,
        "selected_count": len(selected),
        "selected_splits": dict(
            sorted(Counter(row["split"] for row in selected).items())
        ),
        "errors": errors,
        "minimum_request_interval_seconds": config["request_interval_seconds"],
        "single_connection": True,
        "source_archives_acquired": 0,
        "content_use": config["content_use"],
        "train_ready": False,
    }
    temporary = output_dir / ".manifest.tmp"
    temporary.write_text(_dump(manifest))
    os.replace(temporary, output_dir / "manifest.json")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output_dir, resume=args.resume)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "selected_works"},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
