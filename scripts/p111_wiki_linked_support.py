"""Measure exact raw-Wiki row links that bind to frozen visible list rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_adapter, wiki_row_binding
from scripts.p104_wiki_table_grid import parse_tables
from scripts.p111_wiki_linked_freeze import SCHEMA as FREEZE_SCHEMA
from scripts.p111_wiki_linked_freeze import _sha

SCHEMA = "longworld.p111-wiki-linked-row-support.v2"
SINGLE_LINK = re.compile(r"\s*\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]\s*")


def _raw_wikitext(record: dict) -> str:
    path = ROOT / record["response_file"]
    if _sha(path) != record["response_sha256"]:
        raise ValueError("P111 raw response SHA drift")
    payload = json.loads(path.read_text())
    page = payload["query"]["pages"][0]
    revision = page["revisions"][0]
    if page["title"] != record["title"] or revision["revid"] != record["revid"]:
        raise ValueError("P111 raw response identity drift")
    content = revision["slots"]["main"]["content"]
    if hashlib.sha256(content.encode()).hexdigest() != record["wikitext_sha256"]:
        raise ValueError("P111 Wikitext SHA drift")
    return content


def _snapshot(record: dict) -> dict:
    path = ROOT / record["source_snapshot_path"]
    if _sha(path) != record["source_snapshot_sha256"]:
        raise ValueError("P111 source snapshot SHA drift")
    snapshot = json.loads(path.read_text())
    docs = [doc for doc in snapshot["documents"] if doc["doc_id"] == record["doc_id"]]
    if len(docs) != 1 or docs[0]["title"] != record["title"]:
        raise ValueError("P111 rendered source identity drift")
    return docs[0]


def _page_support(record: dict) -> dict:
    raw = _raw_wikitext(record)
    visible = _snapshot(record)
    rows = wiki_row_binding._rows(visible["text"])
    by_name = defaultdict(list)
    for row in rows:
        by_name[row.name.value.casefold()].append(row)
    counts = Counter()
    examples = []
    candidates = []
    target_titles = set()
    for table in parse_tables(raw):
        counts["raw_tables"] += 1
        if table.grid is None:
            counts[f"grid_rejected:{table.reason}"] += 1
            continue
        counts["parsed_grids"] += 1
        grid = table.grid
        if not grid.rows or not all(cell.header for cell in grid.rows[0]):
            counts["missing_simple_header"] += 1
            continue
        header = tuple(
            wiki_adapter._clean_wikitext_inline(cell.raw) for cell in grid.rows[0]
        )
        name_index = wiki_adapter._name_column_index(header)
        if name_index is None or header.count(header[name_index]) != 1:
            counts["missing_name_column"] += 1
            continue
        counts["named_grids"] += 1
        for row in grid.rows[1:]:
            counts["named_grid_rows"] += 1
            if any(cell.rowspan != 1 or cell.colspan != 1 for cell in row):
                counts["span_row"] += 1
                continue
            match = SINGLE_LINK.fullmatch(row[name_index].raw)
            if match is None:
                counts["name_not_single_wikilink"] += 1
                continue
            target = match.group(1).replace("_", " ").strip()
            display = (match.group(2) or match.group(1)).strip()
            if not target or ":" in target or "{{" in display or "[[" in display:
                counts["unsafe_target_or_label"] += 1
                continue
            name = wiki_adapter._clean_wikitext_inline(row[name_index].raw)
            matches = by_name[name.casefold()]
            if len(matches) != 1 or matches[0].name.value != name:
                counts["visible_name_not_unique"] += 1
                continue
            if name != display:
                counts["display_render_differs"] += 1
                continue
            counts["visible_unique_linked_rows"] += 1
            target_titles.add(target)
            candidates.append(
                {
                    "name": name,
                    "target_title": target,
                    "raw_table_line": table.source_start_line,
                    "visible_name_span": [matches[0].name.start, matches[0].name.end],
                    "visible_columns": {
                        header: cell.value for header, cell in matches[0].columns
                    },
                }
            )
            if len(examples) < 4:
                examples.append(
                    {
                        "name": name,
                        "target_title": target,
                        "raw_table_line": table.source_start_line,
                    }
                )
    return {
        "doc_id": record["doc_id"],
        "source_group": record["source_group"],
        "domain": record["domain"],
        "topic": record["topic"],
        "split": record["split"],
        "title": record["title"],
        "page_url": record["page_url"],
        "revid": record["revid"],
        "counts": dict(sorted(counts.items())),
        "unique_target_titles": len(target_titles),
        "examples": examples,
        "candidates": candidates,
    }


def run(raw_manifest: Path, output_path: Path, *, verify_only: bool = False) -> dict:
    manifest = json.loads(raw_manifest.read_text())
    if manifest.get("schema") != FREEZE_SCHEMA or manifest["frozen_pages"] != len(
        manifest["records"]
    ):
        raise ValueError("P111 raw freeze incomplete")
    pages = [_page_support(record) for record in manifest["records"]]
    total = Counter()
    productive = Counter()
    by_domain = Counter()
    by_split = Counter()
    for page in pages:
        total.update(page["counts"])
        if page["counts"].get("visible_unique_linked_rows"):
            productive[page["source_group"]] += 1
            by_domain[page["domain"]] += 1
            by_split[page["split"]] += 1
    result = {
        "schema": SCHEMA,
        "raw_manifest_sha256": _sha(raw_manifest),
        "pages": pages,
        "summary": {
            "pages": len(pages),
            "groups": len({page["source_group"] for page in pages}),
            "groups_with_visible_unique_linked_rows": len(productive),
            "pages_with_visible_unique_linked_rows": sum(productive.values()),
            "counts": dict(sorted(total.items())),
            "productive_pages_by_domain": dict(sorted(by_domain.items())),
            "productive_pages_by_split": dict(sorted(by_split.items())),
        },
        "reader_tasks": 0,
        "train_ready": False,
    }
    encoded = (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode()
        + b"\n"
    )
    if verify_only:
        if not output_path.exists() or output_path.read_bytes() != encoded:
            raise ValueError("P111 support receipt replay differs")
    else:
        if output_path.exists() and output_path.read_bytes() != encoded:
            raise ValueError("P111 support receipt exists with different bytes")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(encoded)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(args.raw_manifest, args.output, verify_only=args.verify_only)
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
