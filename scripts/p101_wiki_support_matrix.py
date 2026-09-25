"""Explain P97 row-JOIN support using pinned snapshots and native receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_row_binding import _rows
from scripts.probe_wiki_row_binding import SCHEMA as NATIVE_SCHEMA

SCHEMA = "longworld.p101-wiki-row-join-support.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_source(source: dict) -> dict:
    pin = source["snapshot"]
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("snapshot path must stay inside workspace")
    path = ROOT / relative
    if path.is_file() and _sha(path) == pin["sha256"]:
        snapshot = json.loads(path.read_text())
    else:
        raise ValueError(f"source snapshot pin differs: {pin['path']}")
    postings: dict[str, set[str]] = defaultdict(set)
    row_counts = {}
    for doc in snapshot["documents"]:
        rows = _rows(doc["text"])
        row_counts[doc["title"]] = len(rows)
        for name in {row.name.value for row in rows}:
            postings[name].add(doc["title"])
    shared = Counter()
    for titles in postings.values():
        ordered = sorted(titles)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                shared[left, right] += 1
    return {
        "source": source["name"],
        "split": source["split"],
        "domain": source["domain"],
        "topic": source["topic"],
        "snapshot_sha256": pin["sha256"],
        "documents": len(snapshot["documents"]),
        "documents_with_typed_rows": sum(bool(count) for count in row_counts.values()),
        "typed_rows": sum(row_counts.values()),
        "shared_name_page_pairs": [
            {"first_title": left, "second_title": right, "shared_names": count}
            for (left, right), count in sorted(shared.items())
        ],
    }


def build(
    pool_path: Path,
    native_dir: Path,
    output_dir: Path,
    *,
    workers: int = 4,
    verify_only: bool = False,
) -> dict:
    if not 1 <= workers <= 16 or output_dir.exists() != verify_only:
        raise ValueError("support output must be new or present for --verify-only")
    pool = json.loads(pool_path.read_text())
    native_path = native_dir / "manifest.json"
    native = json.loads(native_path.read_text())
    if (
        pool.get("schema") != "longworld.source-batch-pool.v2"
        or native.get("schema") != NATIVE_SCHEMA
        or native.get("config_sha256") != _sha(pool_path)
        or native.get("source_groups") != len(pool["sources"])
        or native.get("train_ready") is not False
    ):
        raise ValueError("P97 pool and native JOIN receipt differ")
    native_groups = {row["source"]: row for row in native["groups"]}
    if len(native_groups) != len(pool["sources"]):
        raise ValueError("native group names repeat")
    with ProcessPoolExecutor(max_workers=workers) as executor:
        reports = list(executor.map(_load_source, pool["sources"]))
    statuses: Counter[str] = Counter()
    for row in reports:
        native_row = native_groups[row["source"]]
        if (
            native_row["snapshot_sha256"] != row["snapshot_sha256"]
            or native_row["split"] != row["split"]
            or native_row["domain"] != row["domain"]
            or native_row["topic"] != row["topic"]
            or native_row["documents"] != row["documents"]
        ):
            raise ValueError("source metadata differs from native receipt")
        tasks = native_row["join_tasks"]
        if native_row["split_conflicts"]:
            status = "cross_split_source_rejected"
        elif tasks:
            status = "accepted_row_join"
        elif row["documents"] < 2:
            status = "single_page_group"
        elif row["documents_with_typed_rows"] < 2:
            status = "typed_rows_in_fewer_than_two_pages"
        elif not row["shared_name_page_pairs"]:
            status = "no_exact_shared_row_name"
        else:
            status = "selector_target_or_shortcut_gate_failed"
        if bool(tasks) != (status == "accepted_row_join"):
            raise ValueError("native task status differs from support matrix")
        row["native_join_tasks"] = tasks
        row["status"] = status
        statuses[status] += 1
    reports.sort(key=lambda row: row["source"])
    ledger = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in reports
    )
    if not verify_only:
        output_dir.mkdir(parents=True)
    path = output_dir / "group_support.jsonl"
    if verify_only:
        if path.read_text() != ledger:
            raise ValueError("P101 support ledger byte replay differs")
    else:
        path.write_text(ledger)
    manifest = {
        "schema": SCHEMA,
        "source_pool_sha256": _sha(pool_path),
        "native_manifest_sha256": _sha(native_path),
        "screened_groups": len(reports),
        "screened_documents": sum(row["documents"] for row in reports),
        "documents_with_typed_rows": sum(
            row["documents_with_typed_rows"] for row in reports
        ),
        "groups_with_exact_shared_name_pair": sum(
            bool(row["shared_name_page_pairs"]) for row in reports
        ),
        "productive_groups": sum(bool(row["native_join_tasks"]) for row in reports),
        "independent_tasks": sum(row["native_join_tasks"] for row in reports),
        "statuses": dict(sorted(statuses.items())),
        "group_support_sha256": _sha(path),
        "train_ready": False,
    }
    manifest_path = output_dir / "manifest.json"
    if verify_only:
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("P101 support manifest replay differs")
    else:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pool", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.source_pool,
                args.native_dir,
                args.output_dir,
                workers=args.workers,
                verify_only=args.verify_only,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
