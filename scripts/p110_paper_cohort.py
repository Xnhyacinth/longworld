"""Deduplicate frozen P105 metadata shards into one bounded paper cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p104_paper_source_discovery import _split
from scripts.p105_paper_catalog import _read_page
from scripts.p110_paper_autocatalog import _pin

SCHEMA = "longworld.p110-paper-cohort.v1"
MAX_SOURCE_BYTES = 250_000_000
P105_RESPONSE_BOUND = 64_000_001


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def build(config_path: Path, output_dir: Path) -> dict[str, bytes]:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or type(config.get("work_limit")) is not int
        or not 1 <= config["work_limit"] <= 20
        or config.get("max_source_bytes") != MAX_SOURCE_BYTES
        or config.get("p105_soft_stop_bytes")
        != MAX_SOURCE_BYTES - 2 * P105_RESPONSE_BOUND
        or type(config.get("permit_metadata_failure")) is not bool
    ):
        raise ValueError("P110 cohort or archive budget is unsafe")
    plan_path = _pin(config["plan_manifest"])
    plan = json.loads(plan_path.read_text())
    if plan.get("schema") != "longworld.p110-paper-autocatalog.v1.result":
        raise ValueError("P110 taxonomy plan schema changed")
    plan_dir = plan_path.parent
    prior_path = _pin(config["prior_catalog_manifest"])
    prior = json.loads(prior_path.read_text())
    prior_ids = {row["work_id"] for row in prior["selected_works"]}
    if len(prior_ids) != prior["selected_count"]:
        raise ValueError("prior paper catalog has duplicate work")
    _pin(config["qa_template"])
    _pin(config["prior_candidate_index"])
    if len(config["metadata_manifests"]) != len(plan["shards"]):
        raise ValueError("P110 metadata shard inventory incomplete")
    selected = []
    ledger = []
    seen = set(prior_ids)
    metadata_pages = 0
    metadata_entries = 0
    failed_shards = []
    for name, pin in zip(plan["shards"], config["metadata_manifests"], strict=True):
        shard_config_path = plan_dir / name
        if _sha(shard_config_path.read_bytes()) != plan["files_sha256"][name]:
            raise ValueError("P110 metadata config drift")
        shard_config = json.loads(shard_config_path.read_text())
        source = _pin(pin)
        shard = json.loads(source.read_text())
        complete = shard.get("status") == "complete"
        failed = shard.get("status") == "metadata_failed"
        if (
            shard.get("schema") != "longworld.p105-paper-catalog.v1.result"
            or not (complete or failed)
            or shard.get("config_sha256") != _sha(shard_config_path.read_bytes())
            or len(shard["query_pages"]) > len(shard_config["queries"])
            or (complete and len(shard["query_pages"]) != len(shard_config["queries"]))
            or (
                failed
                and (
                    not config["permit_metadata_failure"]
                    or len(shard.get("errors", [])) != 1
                    or shard.get("selected_works") != []
                    or shard.get("selected_count") != 0
                    or len(shard["query_pages"]) >= len(shard_config["queries"])
                )
            )
        ):
            raise ValueError("P110 metadata source is incomplete or mispinned")
        entries_by_query = {}
        for index, receipt in enumerate(shard["query_pages"]):
            query = shard_config["queries"][index]
            from scripts.p105_paper_catalog import _page_url

            frozen = _read_page(
                source.parent, index, query, _page_url(shard_config, query)
            )
            if frozen is None or frozen[0] != receipt:
                raise ValueError("P110 frozen Atom page differs from metadata receipt")
            entries_by_query[query] = frozen[1]
        metadata_pages += len(shard["query_pages"])
        metadata_entries += shard["metadata_entries"]
        if failed:
            expected_query = shard_config["queries"][len(shard["query_pages"])]
            if shard["errors"][0].get("query") != expected_query:
                raise ValueError("P110 metadata failure is not the next query")
            failed_shards.append(
                {
                    "config": name,
                    "failed_query": expected_query,
                    "uncompleted_queries": len(shard_config["queries"])
                    - len(shard["query_pages"]),
                    "reason": shard["errors"][0].get("reason", ""),
                }
            )
            continue
        for work in shard["selected_works"]:
            if work["category_query"] not in shard_config["queries"]:
                raise ValueError("P110 selected work has no matching query")
            matching = [
                row
                for row in entries_by_query[work["category_query"]]
                if row["work_id"] == work["work_id"]
            ]
            if not any(
                work
                == {
                    **row,
                    "split": _split(row["work_id"]),
                    "versions": [
                        f"v{row['latest_version'] - 1}",
                        f"v{row['latest_version']}",
                    ],
                }
                for row in matching
            ):
                raise ValueError("P110 selected work differs from frozen Atom entry")
            reason = (
                "prior_p105_work"
                if work["work_id"] in prior_ids
                else "cross_shard_duplicate_work"
                if work["work_id"] in seen
                else "global_work_cap"
                if len(selected) >= config["work_limit"]
                else "selected_for_source_fetch"
            )
            ledger.append(
                {
                    "work_id": work["work_id"],
                    "category_query": work["category_query"],
                    "split": work["split"],
                    "license_status": work["license_status"],
                    "license_uri": work["license_uri"],
                    "versions": work["versions"],
                    "status": reason,
                }
            )
            if reason == "selected_for_source_fetch":
                selected.append(work)
            seen.add(work["work_id"])
    if not selected:
        raise ValueError("P110 metadata gave no novel two-revision work")
    # P105's cap is checked after each two-archive work. Reserving twice the
    # per-response 64 MB bound makes this campaign's 250 MB ceiling hard.
    catalog = {
        "schema": "longworld.p105-paper-catalog.v1.result",
        "status": "complete",
        "selected_works": selected,
        "selected_count": len(selected),
        "content_use": "local_research_only_no_redistribution",
        "p110_plan_manifest_sha256": config["plan_manifest"]["sha256"],
        "p110_metadata_manifest_sha256": [
            pin["sha256"] for pin in config["metadata_manifests"]
        ],
    }
    catalog_bytes = _dump(catalog)
    catalog_path = output_dir / "admitted_catalog.json"
    acquire = {
        "schema": "longworld.p105-paper-acquisition.v1.config",
        "catalog_manifest": {
            "path": _relative(catalog_path),
            "sha256": _sha(catalog_bytes),
        },
        "content_use": "local_research_only_no_redistribution",
        "user_agent": config["user_agent"],
        "request_interval_seconds": config["request_interval_seconds"],
        "workers": 4,
        "work_limit": config["work_limit"],
        "max_archive_bytes_total": config["p105_soft_stop_bytes"],
        "qa_template": config["qa_template"],
        "prior_candidate_index": config["prior_candidate_index"],
    }
    outputs = {
        "admitted_catalog.json": catalog_bytes,
        "acquire_config.json": _dump(acquire),
        "work_ledger.jsonl": b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
            for row in ledger
        ),
    }
    summary = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path.read_bytes()),
        "plan_manifest_sha256": config["plan_manifest"]["sha256"],
        "metadata_pages": metadata_pages,
        "metadata_entries": metadata_entries,
        "metadata_failed_shards": failed_shards,
        "proposed_two_revision_works": len(ledger),
        "selected_novel_works": len(selected),
        "selected_non_cs_works": sum(
            not x["category_query"].startswith("cat:cs.") for x in selected
        ),
        "selected_splits": dict(sorted(Counter(x["split"] for x in selected).items())),
        "selected_license_status": dict(
            sorted(Counter(x["license_status"] for x in selected).items())
        ),
        "admission_status": dict(sorted(Counter(x["status"] for x in ledger).items())),
        "hard_source_byte_bound": MAX_SOURCE_BYTES,
        "p105_soft_stop_bytes": config["p105_soft_stop_bytes"],
        "files_sha256": {name: _sha(data) for name, data in outputs.items()},
        "train_ready": False,
    }
    outputs["manifest.json"] = _dump(summary)
    return outputs


def run(config_path: Path, output_dir: Path, verify_only: bool) -> dict:
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    outputs = build(config_path, output_dir)
    if verify_only:
        if {path.name for path in output_dir.iterdir()} != set(outputs):
            raise ValueError("P110 cohort file inventory drift")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P110 cohort replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P110 cohort output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return json.loads(outputs["manifest.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(run(args.config, args.output_dir, args.verify_only), sort_keys=True)
    )


if __name__ == "__main__":
    main()
