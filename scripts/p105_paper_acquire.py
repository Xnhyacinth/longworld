"""Resume bounded arXiv source fetches, then probe raw-TeX shape in four workers.

Network fetches are sequential with a gap between work units. The existing
paper fetcher owns atomic inventory publication and archive provenance; the
P104/P96 parsers own source shape and reader task validation.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.provenance import ProvenanceError
from scripts.fetch_paper_workflow import fetch_paper_workflow
from scripts.p104_paper_source_discovery import _probe
from scripts.p105_paper_http import MIN_INTERVAL_SECONDS, get_source
from scripts.run_p86_frozen_paper_batch import _inventory

SCHEMA = "longworld.p105-paper-acquisition.v1"


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _path(raw: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P105 source paths must be workspace-relative")
    return ROOT / relative


def _pin(pin: dict[str, str]) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P105 source pin needs path and sha256")
    path = _path(pin["path"])
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P105 source pin drift: {pin['path']}")
    return path


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _config(path: Path) -> tuple[dict, dict]:
    config = json.loads(path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("content_use") != "local_research_only_no_redistribution"
        or type(config.get("request_interval_seconds")) not in (float, int)
        or config["request_interval_seconds"] < MIN_INTERVAL_SECONDS
        or config.get("workers") != 4
        or type(config.get("work_limit")) is not int
        or not 1 <= config["work_limit"] <= 20
        or type(config.get("max_archive_bytes_total")) is not int
        or not 1 <= config["max_archive_bytes_total"] <= 1_000_000_000
    ):
        raise ValueError("P105 source config exceeds bounded acquisition contract")
    catalog_path = _pin(config["catalog_manifest"])
    catalog = json.loads(catalog_path.read_text())
    if (
        catalog.get("schema") != "longworld.p105-paper-catalog.v1.result"
        or catalog.get("status") != "complete"
        or not 1 <= catalog.get("selected_count", 0) <= config["work_limit"]
    ):
        raise ValueError("P105 source catalog has no completed bounded cohort")
    if catalog.get("content_use") != config["content_use"] or catalog[
        "selected_count"
    ] != len(catalog["selected_works"]):
        raise ValueError("P105 catalog source use or work count drift")
    _pin(config["qa_template"])
    _pin(config["prior_candidate_index"])
    return config, catalog


def _request(work: dict, config: dict) -> dict:
    version_ids = [work["work_id"] + version for version in work["versions"]]
    return {
        "schema_version": "longworld.paper-fetch-request.v1",
        "user_agent": config["user_agent"],
        "authorization": {
            "record_id": "P105-ARXIV-" + work["work_id"].replace(".", "-"),
            "scope": "last two public arXiv source revisions for local research only",
            "basis": "project owner authorized bounded public-source LongWorld synthesis",
            "reviewed_at": _now(),
            "allowed_actions": ["fetch_arxiv_metadata", "fetch_arxiv_source"],
        },
        "arxiv_version_ids": version_ids,
        "openreview_forum_ids": [],
        "fetch_arxiv_source": True,
        "requests_per_second": 1.0 / config["request_interval_seconds"],
        "max_retries": 0,
    }


def _work_dir(output_dir: Path, work_id: str) -> Path:
    if not work_id.replace(".", "").isdigit():
        raise ValueError("P105 work ID invalid")
    return output_dir / "sources" / ("arxiv_" + work_id)


def _existing_inventory(directory: Path, work: dict) -> tuple[dict, dict] | None:
    inventory_path = directory / "inventory/paper_fetch_inventory.json"
    if not inventory_path.is_file():
        return None
    request_path = directory / "request.json"
    request = json.loads(request_path.read_text())
    expected_ids = [work["work_id"] + version for version in work["versions"]]
    if request["arxiv_version_ids"] != expected_ids:
        raise ValueError("P105 cached work request has different versions")
    pin = {
        "path": str(inventory_path.relative_to(ROOT)),
        "sha256": _sha(inventory_path),
    }
    entry = {
        "family_id": "p105-arxiv-" + work["work_id"],
        "split": work["split"],
        "inventory": pin,
    }
    source = _inventory(entry)
    if (
        source["work_id"] != work["work_id"]
        or [row["version"] for row in source["sources"]] != work["versions"]
    ):
        raise ValueError("P105 cached archive revisions differ")
    archive_bytes = sum(
        (ROOT / row["path"]).stat().st_size for row in source["sources"]
    )
    receipt = {
        "work_id": work["work_id"],
        "query": work["category_query"],
        "split": work["split"],
        "license_status": work["license_status"],
        "license_uri": work["license_uri"],
        "inventory": pin,
        "versions": work["versions"],
        "source_archives": source["sources"],
        "archive_bytes": archive_bytes,
        "status": "frozen_source",
    }
    return receipt, entry


def _save_manifest(output_dir: Path, value: dict) -> None:
    temporary = output_dir / ".manifest.tmp"
    temporary.write_text(_dump(value))
    os.replace(temporary, output_dir / "manifest.json")


@contextmanager
def _output_lock(output_dir: Path):
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir.parent / f".{output_dir.name}.p105.lock"
    with lock_path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"P105 output has another writer: {output_dir}") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _fetch_locked(
    config_path: Path,
    output_dir: Path,
    *,
    resume: bool = False,
    source_get=get_source,
    sleep=time.sleep,
) -> dict:
    config, catalog = _config(config_path)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    lock = {
        "schema": SCHEMA + ".lock",
        "config_sha256": _sha(config_path),
        "catalog_manifest_sha256": config["catalog_manifest"]["sha256"],
    }
    if output_dir.exists():
        if not resume or json.loads((output_dir / "lock.json").read_text()) != lock:
            raise ValueError("P105 source resume lock changed")
    else:
        if resume:
            raise ValueError("P105 source output missing on resume")
        output_dir.mkdir(parents=True)
        (output_dir / "lock.json").write_text(_dump(lock))
    prior_bytes = (
        (output_dir / "manifest.json").read_bytes()
        if resume and (output_dir / "manifest.json").is_file()
        else None
    )
    prior = json.loads(prior_bytes) if prior_bytes is not None else {}
    already_complete = (
        prior.get("schema") == SCHEMA + ".fetch-result"
        and prior.get("status") == "complete"
        and prior.get("frozen_works") == len(catalog["selected_works"])
    )
    receipts = []
    errors = []
    total_bytes = 0
    block_status = ""
    for work in catalog["selected_works"]:
        directory = _work_dir(output_dir, work["work_id"])
        directory.mkdir(parents=True, exist_ok=True)
        request_path = directory / "request.json"
        if not request_path.exists():
            if already_complete:
                raise ValueError("P105 completed source request disappeared")
            request_path.write_text(_dump(_request(work, config)))
        cached = _existing_inventory(directory, work)
        if cached is None:
            if already_complete:
                raise ValueError("P105 completed source inventory disappeared")
            if total_bytes >= config["max_archive_bytes_total"]:
                errors.append(
                    {
                        "work_id": work["work_id"],
                        "reason": "total_archive_budget_reached",
                        "status": "budget_stop",
                    }
                )
                block_status = "archive_budget_exceeded"
                break
            # A conservative gap covers process restarts and the last physical
            # request of the preceding work. The fetcher paces its four calls.
            sleep(config["request_interval_seconds"])
            try:
                fetch_paper_workflow(
                    request_path,
                    directory / "inventory",
                    http_get=source_get,
                    sleep=sleep,
                )
                cached = _existing_inventory(directory, work)
                if cached is None:
                    raise ValueError("paper fetcher omitted frozen inventory")
            except (
                OSError,
                ValueError,
                ProvenanceError,
                subprocess.TimeoutExpired,
            ) as error:
                reason = f"{type(error).__name__}:{error}"
                transport_failure = (
                    isinstance(error, subprocess.TimeoutExpired)
                    or "HTTP 406" in reason
                    or "HTTP 429" in reason
                    or "curl transport failed" in reason
                )
                errors.append(
                    {
                        "work_id": work["work_id"],
                        "reason": reason,
                        "status": "transport_failure"
                        if transport_failure
                        else "source_parse_rejected",
                    }
                )
                if transport_failure:
                    block_status = "transport_blocked"
                    break
                continue
        receipt, _entry = cached
        receipts.append(receipt)
        total_bytes += receipt["archive_bytes"]
        if total_bytes > config["max_archive_bytes_total"]:
            errors.append(
                {
                    "work_id": work["work_id"],
                    "reason": "newly_frozen_work_exceeds_total_archive_budget",
                    "status": "budget_stop",
                }
            )
            block_status = "archive_budget_exceeded"
        if not already_complete:
            _save_manifest(
                output_dir,
                {
                    "schema": SCHEMA + ".fetch-progress",
                    "config_sha256": _sha(config_path),
                    "selected_works": len(catalog["selected_works"]),
                    "frozen_works": len(receipts),
                    "frozen_source_archives": 2 * len(receipts),
                    "archive_bytes": total_bytes,
                    "errors": errors,
                    "train_ready": False,
                },
            )
        if block_status:
            break
    result = {
        "schema": SCHEMA + ".fetch-result",
        "config_sha256": _sha(config_path),
        "catalog_manifest_sha256": config["catalog_manifest"]["sha256"],
        "selected_works": len(catalog["selected_works"]),
        "frozen_works": len(receipts),
        "frozen_source_archives": 2 * len(receipts),
        "archive_bytes": total_bytes,
        "license_unknown_works": sum(
            row["license_status"] != "reported" for row in receipts
        ),
        "source_receipts": receipts,
        "errors": errors,
        "status": block_status
        or ("complete_with_source_rejects" if errors else "complete"),
        "single_network_connection": True,
        "minimum_request_interval_seconds": config["request_interval_seconds"],
        "content_use": config["content_use"],
        "train_ready": False,
    }
    if already_complete:
        if _dump(result).encode() != prior_bytes:
            raise ValueError("P105 completed source receipt drift")
    else:
        _save_manifest(output_dir, result)
    return result


def fetch(
    config_path: Path,
    output_dir: Path,
    *,
    resume: bool = False,
    source_get=get_source,
    sleep=time.sleep,
) -> dict:
    with _output_lock(output_dir):
        return _fetch_locked(
            config_path, output_dir, resume=resume, source_get=source_get, sleep=sleep
        )


def _verify_fetch_locked(config_path: Path, output_dir: Path) -> dict:
    config, catalog = _config(config_path)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    lock = json.loads((output_dir / "lock.json").read_text())
    if lock != {
        "schema": SCHEMA + ".lock",
        "config_sha256": _sha(config_path),
        "catalog_manifest_sha256": config["catalog_manifest"]["sha256"],
    }:
        raise ValueError("P105 source verify lock changed")
    result = json.loads((output_dir / "manifest.json").read_text())
    if (
        result.get("schema") != SCHEMA + ".fetch-result"
        or result.get("status") not in {"complete", "complete_with_source_rejects"}
        or result.get("config_sha256") != _sha(config_path)
        or result.get("catalog_manifest_sha256") != config["catalog_manifest"]["sha256"]
        or result.get("content_use") != config["content_use"]
    ):
        raise ValueError("P105 source fetch result is not a completed cohort")
    receipts = []
    missing = []
    for work in catalog["selected_works"]:
        cached = _existing_inventory(_work_dir(output_dir, work["work_id"]), work)
        if cached is None:
            missing.append(work["work_id"])
        else:
            receipts.append(cached[0])
    errors = result["errors"]
    if (
        result["selected_works"] != len(catalog["selected_works"])
        or result["source_receipts"] != receipts
        or result["frozen_works"] != len(receipts)
        or result["frozen_source_archives"] != 2 * len(receipts)
        or result["archive_bytes"] != sum(row["archive_bytes"] for row in receipts)
        or result["license_unknown_works"]
        != sum(row["license_status"] != "reported" for row in receipts)
        or sorted(row["work_id"] for row in errors) != sorted(missing)
        or len({row["work_id"] for row in errors}) != len(errors)
        or (bool(errors) != (result["status"] == "complete_with_source_rejects"))
        or any(row.get("status") != "source_parse_rejected" for row in errors)
    ):
        raise ValueError("P105 frozen source and rejection ledger differ")
    return result


def verify_fetch(config_path: Path, output_dir: Path) -> dict:
    with _output_lock(output_dir):
        return _verify_fetch_locked(config_path, output_dir)


def _probe_locked(
    config_path: Path, output_dir: Path, *, verify_only: bool = False
) -> dict:
    config, catalog = _config(config_path)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    fetched = _verify_fetch_locked(config_path, output_dir)
    entries = []
    for work in catalog["selected_works"]:
        cached = _existing_inventory(_work_dir(output_dir, work["work_id"]), work)
        if cached is not None:
            _receipt, entry = cached
            entries.append(entry)
    if len(entries) != fetched["frozen_works"]:
        raise ValueError("P105 frozen source inventory drift")
    with ProcessPoolExecutor(max_workers=4) as workers:
        examined = list(
            workers.map(
                _probe,
                [
                    {
                        "inventory": entry["inventory"]["path"],
                        "inventory_sha256": entry["inventory"]["sha256"],
                        "work_id": entry["family_id"].removeprefix("p105-arxiv-"),
                        "recorded_revisions": 2,
                    }
                    for entry in entries
                ],
            )
        )
    capacities = [row for row, _entry in examined]
    families = [entry for _row, entry in examined if entry is not None]
    for entry in families:
        entry["family_id"] = entry["family_id"].replace("p104-arxiv-", "p105-arxiv-", 1)
    families.sort(key=lambda row: row["family_id"])
    source_config = {
        "schema": "longworld.p86-frozen-paper-batch.v1",
        "families": families,
    }
    source_bytes = _dump(source_config).encode()
    source_path = output_dir / "source_config.json"
    qa_template_path = _pin(config["qa_template"])
    qa_config = json.loads(qa_template_path.read_text())
    qa_config.update(
        source_config={
            "path": str(source_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        prior_candidate_index=config["prior_candidate_index"],
        workers=4,
    )
    capacity_bytes = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in capacities
    ).encode()
    outputs = {
        "source_config.json": source_bytes,
        "qa_config.json": _dump(qa_config).encode(),
        "capacity_index.jsonl": capacity_bytes,
    }
    summary = {
        "schema": SCHEMA + ".probe-result",
        "fetch_manifest_sha256": _sha(output_dir / "manifest.json"),
        "frozen_works": fetched["frozen_works"],
        "source_shape_works": len(families),
        "raw_cross_file_links": sum(
            row.get("cross_file_links", 0) for row in capacities
        ),
        "rejection_reasons": dict(
            sorted(
                Counter(
                    row.get("status", "")
                    for row in capacities
                    if row.get("status") != "task_source_candidate"
                ).items()
            )
        ),
        "selected_splits": dict(
            sorted(Counter(row["split"] for row in families).items())
        ),
        "files_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()
        },
        "qa_config_sha256": hashlib.sha256(outputs["qa_config.json"]).hexdigest(),
        "train_ready": False,
    }
    outputs["probe_manifest.json"] = _dump(summary).encode()
    for name, data in outputs.items():
        path = output_dir / name
        if verify_only:
            if path.read_bytes() != data:
                raise ValueError(f"P105 source shape replay drift: {name}")
        else:
            if path.exists():
                raise ValueError(f"P105 source shape output already exists: {name}")
            path.write_bytes(data)
    return summary


def probe(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    with _output_lock(output_dir):
        return _probe_locked(config_path, output_dir, verify_only=verify_only)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("fetch", "probe"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "fetch":
        result = (
            verify_fetch(args.config, args.output_dir)
            if args.verify_only
            else fetch(args.config, args.output_dir, resume=args.resume)
        )
    else:
        result = probe(args.config, args.output_dir, verify_only=args.verify_only)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "source_receipts"},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
