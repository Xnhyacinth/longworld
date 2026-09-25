"""Recover already-frozen P102 pairs after a path-serialization bug, offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_adapter, wiki_row_binding, wiki_world_bridge
from scripts.p102_connected_recovery import _workspace_relative
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p102-frozen-recovery.v1"
ACQUISITION_SCHEMA = "longworld.p102-connected-recovery.v1.result"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    path = Path(pin["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("frozen recovery pin must be workspace-relative")
    resolved = ROOT / path
    if not resolved.is_file() or _sha(resolved) != pin["sha256"]:
        raise ValueError(f"frozen recovery pin changed: {path}")
    return resolved


def build(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or output_dir.exists() != verify_only:
        raise ValueError("invalid frozen recovery config or output state")
    base = json.loads(_pin(config["base_pool"]).read_text())
    failed = json.loads(_pin(config["failed_acquisition"]).read_text())
    if (
        base.get("schema") != "longworld.source-batch-pool.v2"
        or failed.get("schema") != ACQUISITION_SCHEMA
        or failed.get("base_pool_sha256") != config["base_pool"]["sha256"]
        or failed.get("frozen_groups") != 0
    ):
        raise ValueError("failed acquisition is not the expected P102 run")
    by_source = {row["name"]: row for row in base["sources"]}
    reports = []
    sources = []
    for item in config["frozen_pairs"]:
        path = _pin(item["snapshot"])
        log_path = _pin(item["freeze_log"])
        snapshot = json.loads(path.read_text())
        log = json.loads(log_path.read_text())
        wiki_adapter.validate_snapshot(snapshot)
        if (
            log["snapshot_sha256"] != _sha(path)
            or log["snapshot_id"] != snapshot["snapshot_id"]
            or log["revisions"] != snapshot["source"]["revisions"]
        ):
            raise ValueError("frozen snapshot and HTTP freeze log differ")
        target = item["target_title"]
        if target not in {doc["title"] for doc in snapshot["documents"]}:
            raise ValueError("frozen target missing from source snapshot")
        failed_seed = next(
            (
                row
                for row in failed["seeds"]
                if row["anchor_source"] == item["anchor_source"]
            ),
            None,
        )
        anchor = by_source.get(item["anchor_source"])
        if (
            failed_seed is None
            or anchor is None
            or failed_seed["split"] != anchor["split"]
        ):
            raise ValueError("frozen pair source anchor differs")
        previews = [row for row in failed_seed["previewed"] if row["title"] == target]
        rejection = [
            row
            for row in failed_seed["rejections"]
            if row["title"] == target
            and row["reason"].startswith("preview_or_freeze:ValueError:")
            and "is not in the subpath" in row["reason"]
        ]
        if (
            len(previews) != 1
            or len(rejection) != 1
            or previews[0]["matching_rows"] < 1
        ):
            raise ValueError("pair was not previewed then lost to the known path bug")
        anchor_pages = {
            doc["title"].casefold(): doc
            for doc in _snapshot(ROOT, anchor["snapshot"])["documents"]
        }
        anchor_doc = next(
            doc for doc in snapshot["documents"] if doc["title"] != target
        )
        prior = anchor_pages.get(anchor_doc["title"].casefold())
        if prior is None or prior["page_url"] != anchor_doc["page_url"]:
            raise ValueError("frozen anchor is not the pinned P97 source page")
        tasks = wiki_row_binding.build_join_tasks(
            wiki_world_bridge.snapshot_to_world(snapshot), max_tasks=32
        )
        if not tasks:
            raise ValueError("already-frozen pair has no strict JOIN task")
        frozen = {
            "title": target,
            "path": _workspace_relative(Path(item["snapshot"]["path"])),
            "sha256": item["snapshot"]["sha256"],
            "snapshot_id": snapshot["snapshot_id"],
            "revisions": snapshot["source"]["revisions"],
            "license": log["license"],
            "strict_join_tasks": len(tasks),
            "task_ids": [task.task_id for task in tasks],
            "source_characters": sum(len(doc["text"]) for doc in snapshot["documents"]),
            "freeze_log_sha256": item["freeze_log"]["sha256"],
        }
        reports.append(
            {
                "seed": failed_seed["seed"],
                "anchor_source": anchor["name"],
                "split": anchor["split"],
                "searched_entities": failed_seed["searched_entities"],
                "previewed": previews,
                "frozen": [frozen],
                "rejections": [],
            }
        )
        sources.append(
            {
                "name": snapshot["snapshot_id"],
                "domain": anchor["domain"],
                "topic": anchor["topic"],
                "split": anchor["split"],
                "snapshot": item["snapshot"],
            }
        )
    if len({source["name"] for source in sources}) != len(sources):
        raise ValueError("recovered source snapshot IDs repeat")
    pool = {**base, "sources": sources}
    if not verify_only:
        output_dir.mkdir(parents=True)
    pool_bytes = json.dumps(pool, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    pool_path = output_dir / "source_pool.json"
    if verify_only:
        if pool_path.read_text() != pool_bytes:
            raise ValueError("frozen recovered pool byte replay differs")
    else:
        pool_path.write_text(pool_bytes)
    acquisition = {
        "schema": ACQUISITION_SCHEMA,
        "config_sha256": _sha(config_path),
        "base_pool_sha256": config["base_pool"]["sha256"],
        "salvaged_from_failed_acquisition_sha256": config["failed_acquisition"][
            "sha256"
        ],
        "search_queries_reused": failed["search_queries_reused"],
        "original_additional_page_previews": failed["additional_page_previews"],
        "additional_page_previews": 0,
        "additional_http_requests": 0,
        "seeds": reports,
        "frozen_groups": len(sources),
        "productive_groups": len(sources),
        "strict_join_tasks": sum(
            row["strict_join_tasks"] for report in reports for row in report["frozen"]
        ),
        "source_pool_sha256": _sha(pool_path),
        "train_ready": False,
    }
    acquisition_path = output_dir / "acquisition_manifest.json"
    acquisition_bytes = (
        json.dumps(acquisition, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    if verify_only:
        if acquisition_path.read_text() != acquisition_bytes:
            raise ValueError("frozen acquisition byte replay differs")
    else:
        acquisition_path.write_text(acquisition_bytes)
    receipt = {
        "schema": "longworld.connected-wiki-intake.v1.source-pool-receipt",
        "acquisition_manifest_sha256": _sha(acquisition_path),
        "source_pool_sha256": _sha(pool_path),
        "source_groups": len(sources),
        "train_groups": sum(row["split"] == "train" for row in sources),
        "eval_groups": sum(row["split"] == "eval" for row in sources),
    }
    receipt_path = output_dir / "source_pool_receipt.json"
    receipt_bytes = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    if verify_only:
        if receipt_path.read_text() != receipt_bytes:
            raise ValueError("frozen recovery receipt byte replay differs")
    else:
        receipt_path.write_text(receipt_bytes)
    return acquisition


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = build(args.config, args.output_dir, verify_only=args.verify_only)
    print(
        json.dumps(
            {
                "frozen_groups": result["frozen_groups"],
                "strict_join_tasks": result["strict_join_tasks"],
                "additional_http_requests": result["additional_http_requests"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
