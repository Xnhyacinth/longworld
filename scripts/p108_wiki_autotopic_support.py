"""Probe P100 categorical and P106 raw-grid potential on new Wiki worlds."""

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

from longworld.synthesis.p93_wiki_structural_intake import pinned_json, sha
from scripts.p100_wiki_categorical_scan import _scan_source, options

SCHEMA = "longworld.p108-wiki-autotopic-table-support.v1"


def _bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _line(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def build(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or set(config)
        != {
            "schema",
            "source_pool",
            "native_result",
            "workers",
            "max_options_per_table",
        }
        or type(config["workers"]) is not int
        or not 1 <= config["workers"] <= 4
        or type(config["max_options_per_table"]) is not int
        or not 1 <= config["max_options_per_table"] <= 8
    ):
        raise ValueError("P108 support config differs")
    pool = pinned_json(ROOT, config["source_pool"])
    native = pinned_json(ROOT, config["native_result"])
    if (
        pool.get("schema") != "longworld.source-batch-pool.v2"
        or native["source_pool_sha256"] != config["source_pool"]["sha256"]
    ):
        raise ValueError("P108 support source/native pin differs")
    sources = sorted(pool["sources"], key=lambda row: (row["split"], row["name"]))
    with ProcessPoolExecutor(max_workers=config["workers"]) as executor:
        scans = list(executor.map(_scan_source, sources))
    page_rows = []
    option_groups = defaultdict(set)
    option_counts, rejects, domain_options = Counter(), Counter(), Counter()
    eligible_tables = option_tables = 0
    for source, _snapshot, tables, pages in scans:
        page_rows.extend(pages)
        eligible_tables += len(tables)
        for page in pages:
            rejects.update(item["reason"] for item in page["rejected_tables"])
        for doc, table in tables:
            found = options(table, max_tasks=config["max_options_per_table"])
            if not found:
                continue
            option_tables += 1
            option_groups[source["name"]].add(doc["doc_id"])
            option_counts[source["name"]] += len(found)
            domain_options[source["domain"]] += len(found)
    page_bytes = b"".join(_line(row) for row in page_rows)
    width_pages = {
        row["doc_id"]
        for row in page_rows
        if any(
            item["reason"] == "row_width_mismatch" for item in row["rejected_tables"]
        )
    }
    width_groups = {
        row["source_group"] for row in page_rows if row["doc_id"] in width_pages
    }
    report = {
        "schema": SCHEMA + ".result",
        "config_sha256": sha(config_path),
        "source_pool": config["source_pool"],
        "native_result": config["native_result"],
        "p100_parser_sha256": sha(ROOT / "scripts/p100_wiki_categorical_scan.py"),
        "page_audit_sha256": hashlib.sha256(page_bytes).hexdigest(),
        "source_groups_screened": len(sources),
        "pages_screened": len(page_rows),
        "p100_eligible_tables": eligible_tables,
        "p100_tables_with_options": option_tables,
        "p100_candidate_options_before_reader_audit": sum(option_counts.values()),
        "p100_groups_with_options": len(option_groups),
        "p100_groups_with_options_in_multiple_pages": sum(
            len(pages) > 1 for pages in option_groups.values()
        ),
        "p100_options_by_domain": dict(sorted(domain_options.items())),
        "p100_rejection_reasons": dict(sorted(rejects.items())),
        "p106_visible_row_width_rejections": rejects["row_width_mismatch"],
        "p106_pages_needing_exact_raw_revision": len(width_pages),
        "p106_groups_needing_exact_raw_revision": len(width_groups),
        "native_cross_document_pair_jobs": native["task_operations"].get(
            "wiki_table_pair", 0
        ),
        "p105_p106_reader_tasks_admitted": 0,
        "scope": "P100 visible-table parser/options only; raw Wikitext grid, reader-cell alignment and final intervention not yet checked",
        "train_ready": False,
    }
    outputs = {"page_audit.jsonl": page_bytes, "manifest.json": _bytes(report)}
    if verify_only:
        if not output_dir.is_dir() or {p.name for p in output_dir.iterdir()} != set(
            outputs
        ):
            raise ValueError("P108 support output inventory differs")
        for name, content in outputs.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"P108 support replay differs: {name}")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.config, args.output_dir, verify_only=args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
