"""Classify empty arXiv transport responses before P105 provenance parsing.

P105's parser checks body length before HTTP status, so an empty 429 becomes a
source-shape rejection. This scoped adapter stops the batch on transport
failure, preserving existing P105 archives and receipts unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import p105_paper_acquire as p105
from scripts.p105_paper_http import get_source


def classified_get_source(url: str, headers: dict[str, str], timeout: float):
    response = get_source(url, headers, timeout)
    if response.status != 200:
        raise ValueError(
            f"curl transport failed: HTTP {response.status}; "
            f"body_bytes={len(response.body)}; url={url}"
        )
    if not response.body:
        raise ValueError(f"curl transport failed: empty HTTP 200 body; url={url}")
    return response


def verify_fetch(config_path: Path, output_dir: Path) -> dict:
    """Replay a completed or bounded transport-stopped packet without network."""
    config, catalog = p105._config(config_path)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    with p105._output_lock(output_dir):
        lock = json.loads((output_dir / "lock.json").read_text())
        if lock != {
            "schema": p105.SCHEMA + ".lock",
            "config_sha256": p105._sha(config_path),
            "catalog_manifest_sha256": config["catalog_manifest"]["sha256"],
        }:
            raise ValueError("P110 source packet lock drift")
        result = json.loads((output_dir / "manifest.json").read_text())
        if (
            result.get("schema") != p105.SCHEMA + ".fetch-result"
            or result.get("status")
            not in {
                "complete",
                "complete_with_source_rejects",
                "transport_blocked",
                "archive_budget_exceeded",
            }
            or result.get("config_sha256") != p105._sha(config_path)
            or result.get("catalog_manifest_sha256")
            != config["catalog_manifest"]["sha256"]
            or result.get("selected_works") != len(catalog["selected_works"])
            or result.get("content_use") != config["content_use"]
        ):
            raise ValueError("P110 source packet manifest drift")
        cached = [
            p105._existing_inventory(p105._work_dir(output_dir, work["work_id"]), work)
            for work in catalog["selected_works"]
        ]
        receipts = [item[0] for item in cached if item is not None]
        errors = result["errors"]
        if (
            result["source_receipts"] != receipts
            or result["frozen_works"] != len(receipts)
            or result["frozen_source_archives"] != 2 * len(receipts)
            or result["archive_bytes"] != sum(row["archive_bytes"] for row in receipts)
            or result["archive_bytes"] > 250_000_000
            or result["license_unknown_works"]
            != sum(row["license_status"] != "reported" for row in receipts)
            or len({row["work_id"] for row in errors}) != len(errors)
            or any(
                row["status"]
                not in {"source_parse_rejected", "transport_failure", "budget_stop"}
                for row in errors
            )
            or any(
                row["work_id"]
                not in {work["work_id"] for work in catalog["selected_works"]}
                for row in errors
            )
        ):
            raise ValueError("P110 source packet receipts or rejection ledger differ")
        if result["status"] == "transport_blocked":
            if not errors or errors[-1]["status"] != "transport_failure":
                raise ValueError(
                    "P110 transport stop has no terminal transport failure"
                )
        elif any(row["status"] == "transport_failure" for row in errors):
            raise ValueError("P110 completed packet contains transport failure")
        return result


def run(
    config_path: Path,
    output_dir: Path,
    *,
    resume: bool = False,
    verify_only: bool = False,
    backoff_seconds: int = 90,
    max_transport_retries: int = 2,
) -> dict:
    if verify_only:
        return verify_fetch(config_path, output_dir)
    if not 60 <= backoff_seconds <= 600 or not 0 <= max_transport_retries <= 3:
        raise ValueError("P110 transport backoff outside bounded range")
    for attempt in range(max_transport_retries + 1):
        result = p105.fetch(
            config_path,
            output_dir,
            resume=resume or attempt > 0,
            source_get=classified_get_source,
        )
        if result["status"] != "transport_blocked" or attempt == max_transport_retries:
            return result
        time.sleep(backoff_seconds)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--backoff-seconds", type=int, default=90)
    parser.add_argument("--max-transport-retries", type=int, default=2)
    args = parser.parse_args()
    result = run(
        args.config,
        args.output_dir,
        resume=args.resume,
        verify_only=args.verify_only,
        backoff_seconds=args.backoff_seconds,
        max_transport_retries=args.max_transport_retries,
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "source_receipts"},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
