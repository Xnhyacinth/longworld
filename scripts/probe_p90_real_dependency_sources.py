"""Bounded offline yield probe for frozen Wiki tables and paper revisions.

This reruns the existing conservative Wiki JOIN compiler and reports why the
new P89 pages do or do not support a closed-table set task. It never turns a
schema mismatch into a training label.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_row_binding, wiki_table_scan
from scripts import index_frozen_wiki_joins
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p90-real-dependency-probe.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pinned(pin: dict[str, str]) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("probe input must use a workspace-relative path")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"probe input pin changed: {relative}")
    return path


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _wiki_pages(pool_path: Path) -> list[dict[str, Any]]:
    pool = json.loads(pool_path.read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong frozen Wiki pool schema")
    pages = []
    for source in pool["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        for doc in snapshot["documents"]:
            rows = wiki_row_binding._rows(doc["text"])
            try:
                table = wiki_table_scan.parse_closed_table(doc["text"], doc["title"])
                closed = {
                    "eligible_rows": len(table.eligible),
                    "excluded_rows": len(table.format_excluded),
                    "malformed_rows": table.malformed_row_count,
                }
            except ValueError as exc:
                closed = {"blocked": str(exc)}
            pages.append(
                {
                    "title": doc["title"],
                    "source_group": source["name"],
                    "split": source["split"],
                    "domain": source["domain"],
                    "topic": source["topic"],
                    "snapshot_sha256": source["snapshot"]["sha256"],
                    "typed_rows": len(rows),
                    "closed_established_table": closed,
                }
            )
    return sorted(pages, key=lambda row: (row["split"], row["title"]))


def _paper_summary(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "longworld.p86-frozen-paper-batch.v1.result":
        raise ValueError("wrong paper batch schema")
    for name, digest in manifest["files_sha256"].items():
        if _sha(manifest_path.parent / name) != digest:
            raise ValueError(f"paper batch file changed: {name}")
    capacity = _lines(manifest_path.parent / "capacity_index.jsonl")
    rejected = _lines(manifest_path.parent / "rejected.jsonl")
    if len(capacity) != manifest["source_revision_pairs"]:
        raise ValueError("paper revision pair count changed")
    if len(rejected) != manifest["rejected_pairs"]:
        raise ValueError("paper rejection count changed")
    return {
        "source_works": manifest["source_works"],
        "revision_pairs": len(capacity),
        "existing_admitted_tasks": manifest["quality_admitted_tasks"],
        "new_tasks_from_this_probe": 0,
        "rejected_pairs": len(rejected),
        "reject_reasons": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "quality_prose_pairs": sum(row["quality_prose_files"] > 0 for row in capacity),
        "claim_limit": "pinned prior compiler receipts; no new paper QA was generated",
    }


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong real dependency probe schema")
    index_config = _pinned(config["wiki_index_config"])
    pool_path = _pinned(config["new_wiki_pool"])
    paper_path = _pinned(config["paper_batch_manifest"])
    pages = _wiki_pages(pool_path)
    index = index_frozen_wiki_joins.run(index_config, output_dir / "wiki_join")
    pairs = _lines(output_dir / "wiki_join" / "pair_audit.jsonl")
    p89_titles = {page["title"] for page in pages}
    involving_new = [
        pair
        for pair in pairs
        if pair["first_title"] in p89_titles or pair["second_title"] in p89_titles
    ]
    novel = sum(len(pair.get("task_ids", [])) for pair in involving_new)
    if novel != sum(index["accepted_tasks_by_split"].values()):
        raise ValueError("new Wiki task accounting changed; inspect non-P89 pairs")
    paper = _paper_summary(paper_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "wiki_pages.json", pages)
    _write(output_dir / "new_page_pairs.json", involving_new)
    _write(output_dir / "paper_support.json", paper)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "new_wiki_pages": len(pages),
        "new_wiki_typed_pages": sum(page["typed_rows"] > 0 for page in pages),
        "new_wiki_closed_established_tables": sum(
            "eligible_rows" in page["closed_established_table"] for page in pages
        ),
        "new_page_join_pairs": len(involving_new),
        "new_page_pair_statuses": dict(
            sorted(Counter(pair["status"] for pair in involving_new).items())
        ),
        "novel_wiki_join_tasks": novel,
        "novel_closed_table_tasks": 0,
        "paper": paper,
        "new_real_l2_l3_tasks": novel,
        "files_sha256": {
            name: _sha(output_dir / name)
            for name in ("wiki_pages.json", "new_page_pairs.json", "paper_support.json")
        },
        "join_index_manifest_sha256": _sha(
            output_dir / "wiki_join" / "index_manifest.json"
        ),
        "train_ready": False,
        "claim_limit": "bounded exact Wiki table join and closed Established-table shape; paper results are pinned prior receipts",
    }
    _write(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
