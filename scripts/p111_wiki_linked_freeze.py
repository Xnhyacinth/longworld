"""Freeze exact P108 Wiki list revisions needed to inspect source wikilinks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p104_freeze_width_revisions import RateLimiter, _fetch, _validate_response

SCHEMA = "longworld.p111-wiki-linked-exact-list-freeze.v1"
OLDID = re.compile(r"https://en\.wikipedia\.org/w/index\.php\?oldid=([0-9]+)\Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P111 pin requires workspace path and SHA-256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P111 pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P111 source pin drift: {relative}")
    return path


def plan(config: dict) -> tuple[list[dict], dict[tuple[str, int], dict]]:
    pool = json.loads(_pin(config["source_pool"]).read_text())
    gate = json.loads(_pin(config["gate_result"]).read_text())
    prior_path = _pin(config["prior_raw_manifest"])
    prior = json.loads(prior_path.read_text())
    if (
        pool.get("schema") != "longworld.source-batch-pool.v2"
        or gate["net_novel"]["groups"] != len(pool["sources"])
        or gate["net_novel"]["pages"] != 171
        or prior.get("schema") != "longworld.p106-wiki-exact-revision-freeze.v1"
        or prior["source_pins"]["source_pool"]["sha256"]
        != config["source_pool"]["sha256"]
    ):
        raise ValueError(
            "P111 P108 source pool, global gate or prior raw receipt differs"
        )
    prior_by_revision = {
        (record["doc_id"], record["revid"]): {
            **record,
            "response_file": str(
                Path(config["prior_raw_manifest"]["path"]).parent
                / record["response_path"]
            ),
        }
        for record in prior["records"]
    }
    jobs = []
    seen_titles, seen_urls = set(), set()
    for source in pool["sources"]:
        snapshot = json.loads(_pin(source["snapshot"]).read_text())
        for doc in snapshot["documents"]:
            match = OLDID.fullmatch(doc["revision_url"])
            if match is None:
                raise ValueError("P111 source page lacks exact oldid revision")
            if doc["title"].casefold() in seen_titles or doc["page_url"] in seen_urls:
                raise ValueError("P111 source pool repeats a title or URL")
            seen_titles.add(doc["title"].casefold())
            seen_urls.add(doc["page_url"])
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
    if len(jobs) != 171:
        raise ValueError("P111 frozen source inventory is not 171 pages")
    return sorted(
        jobs, key=lambda row: (row["source_group"], row["doc_id"])
    ), prior_by_revision


def _response(record: dict, job: dict) -> None:
    path = ROOT / record["response_file"]
    if _sha(path) != record["response_sha256"]:
        raise ValueError(f"P111 exact revision response SHA differs: {path}")
    parsed = _validate_response(path.read_bytes(), job)
    if (
        parsed["revid"] != job["revid"]
        or parsed["wikitext_sha256"] != record["wikitext_sha256"]
    ):
        raise ValueError("P111 raw response revision or Wikitext differs")


def _acquire(
    job: dict, prior: dict | None, destination: Path, limiter: RateLimiter
) -> dict:
    if prior is not None:
        _response(prior, job)
        return {
            **job,
            "response_file": prior["response_file"],
            "response_sha256": prior["response_sha256"],
            "wikitext_sha256": prior["wikitext_sha256"],
            "revision_timestamp": prior["revision_timestamp"],
            "source_lane": "p108_raw_reuse",
        }
    fetched = _fetch(job, destination, limiter)
    record = {
        **job,
        "response_file": str(destination.relative_to(ROOT) / fetched["response_path"]),
        "response_sha256": fetched["response_sha256"],
        "wikitext_sha256": fetched["wikitext_sha256"],
        "revision_timestamp": fetched["revision_timestamp"],
        "source_lane": "p111_exact_revision",
    }
    _response(record, job)
    return record


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config_path = (ROOT / config_path).absolute()
    output_dir = (ROOT / output_dir).absolute()
    if not output_dir.is_relative_to(ROOT):
        raise ValueError("P111 output must be under workspace root")
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or config.get("workers") != 4
        or config.get("requests_per_second") != 2.0
    ):
        raise ValueError("P111 freeze config invalid")
    jobs, prior = plan(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "single_writer.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if (
                manifest.get("schema") != SCHEMA
                or manifest.get("config_sha256") != _sha(config_path)
                or manifest.get("source_pool_sha256") != config["source_pool"]["sha256"]
                or len(manifest.get("records", [])) != len(jobs)
            ):
                raise ValueError("P111 completed raw freeze manifest differs")
            for job, record in zip(jobs, manifest["records"], strict=True):
                if any(record[key] != job[key] for key in job):
                    raise ValueError(
                        "P111 completed raw freeze source identity differs"
                    )
                _response(record, job)
            return manifest
        if verify_only:
            raise ValueError("P111 raw freeze manifest is missing")
        limiter = RateLimiter(config["requests_per_second"])
        with ThreadPoolExecutor(max_workers=config["workers"]) as executor:
            records = list(
                executor.map(
                    lambda job: _acquire(
                        job,
                        prior.get((job["doc_id"], job["revid"])),
                        output_dir,
                        limiter,
                    ),
                    jobs,
                )
            )
        manifest = {
            "schema": SCHEMA,
            "config_sha256": _sha(config_path),
            "source_pool_sha256": config["source_pool"]["sha256"],
            "gate_result_sha256": config["gate_result"]["sha256"],
            "prior_raw_manifest_sha256": config["prior_raw_manifest"]["sha256"],
            "frozen_pages": len(records),
            "reused_prior_pages": sum(
                row["source_lane"] == "p108_raw_reuse" for row in records
            ),
            "new_exact_revision_pages": sum(
                row["source_lane"] == "p111_exact_revision" for row in records
            ),
            "records": records,
            "train_ready": False,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output_dir, verify_only=args.verify_only)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "frozen_pages",
                    "reused_prior_pages",
                    "new_exact_revision_pages",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
