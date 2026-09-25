"""Compile every legal frozen Wiki year-table interval across pinned pools.

The existing strict table parser defines eligibility. This driver removes
previous semantic tasks before tokenizing and compiles independent tables in
separate worker processes. It never invents missing rows or changes sources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.p92_generic_table_scan import (
    SUBJECT_COLUMNS,
    YEAR_COLUMNS,
    intervals,
)
from longworld.synthesis.sharded_candidate_bank import verify_index
from scripts.audit_p94_wiki_native import audit as audit_mask
from scripts.run_p92_generic_table_scan import (
    _compile,
    _dump,
    _lines,
    _pinned_path,
    _prior_keys,
    _scan,
    _sha,
    _write,
)

SCHEMA = "longworld.p95-wiki-table-sweep.v1"


def _visible_table_keys(text: str) -> Counter[tuple[str, str]]:
    """Count reader-visible table identifiers, including malformed siblings."""
    heading = ""
    keys: Counter[tuple[str, str]] = Counter()
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:]
            continue
        columns = line.split(" | ")
        if (
            heading
            and len(columns) >= 2
            and columns[0] in SUBJECT_COLUMNS
            and sum(column in YEAR_COLUMNS for column in columns) == 1
        ):
            keys[heading, line] += 1
    return keys


def _task_id(doc: dict, table: Any, low: int, high: int) -> str:
    return hashlib.sha256(
        f"{doc['doc_id']}|{table.header_start}|{low}|{high}".encode()
    ).hexdigest()[:20]


def _load_sources(config: dict) -> tuple[list[dict], Counter[str]]:
    sources_by_snapshot: dict[str, dict] = {}
    gross: Counter[str] = Counter()
    for pin in config["source_pools"]:
        pool = json.loads(_pinned_path(pin).read_text())
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError("wrong source-pool schema")
        gross["pool_records"] += len(pool["sources"])
        for source in pool["sources"]:
            digest = source["snapshot"]["sha256"]
            previous = sources_by_snapshot.get(digest)
            if previous is not None:
                if previous["split"] != source["split"]:
                    raise ValueError("same snapshot appears in both splits")
                gross["duplicate_snapshots"] += 1
                continue
            sources_by_snapshot[digest] = source
    return sorted(
        sources_by_snapshot.values(),
        key=lambda source: (source["split"], source["name"]),
    ), gross


def _bank_task_ids(pin: dict) -> set[str]:
    path = _pinned_path(pin)
    index_dir = path.parent
    verify_index(index_dir)
    manifest = json.loads(path.read_text())
    refs = index_dir / "candidate_refs.jsonl"
    if _sha(refs) != manifest["refs_sha256"]:
        raise ValueError("frozen bank references changed")
    return {
        row["candidate"]["semantic_task_id"]
        for line in refs.read_text().splitlines()
        if line
        for row in (json.loads(line),)
        if row["candidate"]["source_kind"] == "real_wiki"
    }


def _plan(
    scans: list[tuple[dict, dict, list[tuple[dict, Any]], list[dict]]],
    prior_ids: set[str],
    prior_keys: dict[tuple[str, str, str, int, int], str],
    max_tasks: int,
) -> tuple[
    list[tuple[dict, dict, dict, Any, list[tuple[int, int]]]], list[dict], Counter[str]
]:
    work = []
    rejected = []
    counts: Counter[str] = Counter()
    titles: dict[str, str] = {}
    for source, snapshot, _candidates, pages in scans:
        counts["pages"] += len(pages)
        for page in pages:
            title = page["title"].casefold()
            previous = titles.setdefault(title, source["split"])
            if previous != source["split"]:
                raise ValueError(
                    f"frozen page title crosses train/eval: {page['title']}"
                )
            counts["parser_rejected_tables"] += len(page["rejected_tables"])
    seen_tables: set[tuple[str, str, int]] = set()
    seen_ids: set[str] = set(prior_ids)
    for source, snapshot, candidates, _pages in scans:
        for doc, table in candidates:
            counts["eligible_tables"] += 1
            if _visible_table_keys(doc["text"])[table.heading, table.header] != 1:
                rejected.append(
                    {
                        "source_group": source["name"],
                        "title": doc["title"],
                        "reason": "ambiguous_visible_table_key",
                    }
                )
                continue
            table_key = (doc["title"].casefold(), table.header, table.header_start)
            if table_key in seen_tables:
                rejected.append(
                    {
                        "source_group": source["name"],
                        "title": doc["title"],
                        "reason": "duplicate_source_table",
                    }
                )
                continue
            seen_tables.add(table_key)
            chosen = []
            for low, high in intervals(table, max_tasks):
                counts["intervals_planned"] += 1
                task_id = _task_id(doc, table, low, high)
                key = (
                    snapshot["snapshot_id"],
                    doc["doc_id"],
                    table.year_column,
                    low,
                    high,
                )
                if task_id in seen_ids or key in prior_keys:
                    rejected.append(
                        {
                            "source_group": source["name"],
                            "title": doc["title"],
                            "heading": table.heading,
                            "interval": [low, high],
                            "reason": "duplicate_existing",
                        }
                    )
                    continue
                seen_ids.add(task_id)
                chosen.append((low, high))
            if chosen:
                work.append((source, snapshot, doc, table, chosen))
    return work, rejected, counts


def _compile_table(
    job: tuple[dict, dict, dict, Any, list[tuple[int, int]]], config: dict
):
    source, snapshot, doc, table, chosen = job
    tokenizer = get_tokenizer()
    accepted = []
    rejected = []
    for interval in chosen:
        try:
            rows = _compile(
                {**config, "interval_override": interval},
                source,
                snapshot,
                doc,
                table,
                tokenizer,
            )
        except (ValueError, OverflowError) as exc:
            rejected.append(
                {
                    "source_group": source["name"],
                    "title": doc["title"],
                    "heading": table.heading,
                    "interval": list(interval),
                    "reason": str(exc),
                }
            )
            continue
        for reader, index, audit in rows:
            audit["header_start"] = table.header_start
            accepted.append((reader, index, audit))
    return accepted, rejected


def _compile_job(args):
    return _compile_table(*args)


def run(
    config_path: Path, output_dir: Path, *, workers: int = 2, verify_only: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not isinstance(config.get("source_pools"), list)
        or not config["source_pools"]
        or not 1 <= workers <= 16
        or not 1 <= config.get("max_tasks_per_table", 0) <= 64
        or not 1 <= config.get("max_full_tokens", 0)
    ):
        raise ValueError("invalid table sweep configuration")
    if output_dir.exists() != verify_only:
        raise ValueError("output must be new, or present for --verify-only")
    sources, gross = _load_sources(config)
    prior_ids = _bank_task_ids(config["prior_candidate_index"])
    prior_keys = _prior_keys(config)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        scans = list(executor.map(_scan, sources))
    work, rejected, counts = _plan(
        scans, prior_ids, prior_keys, config["max_tasks_per_table"]
    )
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(_compile_job, ((job, config) for job in work)))
    rows = []
    for accepted, failed in results:
        rows.extend(accepted)
        rejected.extend(failed)
    readers = {split: [] for split in ("train", "eval")}
    indices, audits = [], []
    for reader, index, item in rows:
        readers[index["split"]].append(reader)
        indices.append(index)
        audits.append(item)
    if len({row["task_id"] for row in indices}) != len(indices):
        raise ValueError("generated semantic task IDs repeat")
    pages = [
        page for _s, _snapshot, _candidates, page_audit in scans for page in page_audit
    ]
    payloads = {
        "train.jsonl": readers["train"],
        "eval.jsonl": readers["eval"],
        "sample_index.jsonl": indices,
        "audit.jsonl": audits,
        "page_audit.jsonl": pages,
        "rejected.jsonl": rejected,
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for name, value in payloads.items():
        path = output_dir / name
        if verify_only:
            if path.read_text() != "".join(_dump(row) + "\n" for row in value):
                raise ValueError(f"table sweep replay drift: {name}")
        else:
            _lines(path, value)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "source_pools": config["source_pools"],
        "prior_candidate_index": config["prior_candidate_index"],
        "prior_task_audits": config["prior_task_audits"],
        "code_sha256": {
            name: _sha(ROOT / name)
            for name in (
                "scripts/run_p95_wiki_table_sweep.py",
                "scripts/run_p92_generic_table_scan.py",
                "longworld/synthesis/p92_generic_table_scan.py",
            )
        },
        "source_records": gross["pool_records"],
        "duplicate_source_snapshots": gross["duplicate_snapshots"],
        "unique_source_groups": len(sources),
        "unique_pages": len({row["doc_id"] for row in pages}),
        "schema_eligible_tables": counts["eligible_tables"],
        "parser_rejected_tables": counts["parser_rejected_tables"],
        "intervals_planned": counts["intervals_planned"],
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "split_views": dict(sorted(Counter(row["split"] for row in indices).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in indices).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indices).items())),
        "source_groups": dict(
            sorted(Counter(row["source_group"] for row in indices).items())
        ),
        "candidate_rejections": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "full_chat_tokens": [row["full_chat_tokens"] for row in indices],
        "evidence_token_extents": [row["evidence_token_extent"] for row in indices],
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    if verify_only:
        if json.loads((output_dir / "manifest.json").read_text()) != manifest:
            raise ValueError("table sweep manifest replay drift")
    else:
        _write(output_dir / "manifest.json", manifest)
    audit_mask(output_dir, "generic_year")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(
            run(
                args.config,
                args.output_dir,
                workers=args.workers,
                verify_only=args.verify_only,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
