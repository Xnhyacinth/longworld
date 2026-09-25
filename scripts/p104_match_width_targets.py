"""Map P100 visible width rejections to exact-revision Wikitext grids.

This is a support ledger. It does not certify rendered cell alignment or emit
reader tasks; that boundary needs a separate versioned reader renderer.
"""

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

from p104_wiki_table_grid import parse_tables

from longworld.synthesis.wiki_adapter import (
    _clean_wikitext_inline,
    render_wikitext,
)

RAW_MANIFEST = ROOT / "data/candidates/p104_wiki_width_raw_v1/manifest.json"
RAW_MANIFEST_SHA256 = "0f61ad495d1acc492ae8ad843eda31b0236a6cd2fbe00d93da4846e2e75d735d"
PAGE_AUDIT = ROOT / "data/candidates/p100_wiki_categorical_scan_v3/page_audit.jsonl"
PAGE_AUDIT_SHA256 = "8e3e008858a122d4a722c96d132f24d21bf9eeef5897417e4053bf09de379846"
SCHEMA = "longworld.p104-wiki-width-target-support.v1"
_H2 = re.compile(r"^==(?!=)\s*(.*?)\s*(?<!\=)==$")


def _pinned(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"pinned input changed: {path}")
    return raw


def _target_status(matches: list[dict]) -> str:
    if not matches:
        return "raw_section_header_unmatched"
    valid = [match for match in matches if match["grid"] is not None]
    if not valid:
        return "raw_grid_rejected"
    if len(valid) != 1 or len(matches) != 1:
        return "multiple_raw_tables_same_visible_key"
    grid = valid[0]["grid"]
    if len(grid.rows) < 9 or grid.width < 3:
        return "insufficient_grid_rows_or_width"
    if not all(cell.header for cell in grid.rows[0]):
        return "first_row_not_explicit_header"
    if any(not cell.raw.strip() for cell in grid.rows[0]):
        return "unlabeled_header_column"
    if any(cell.header for cell in grid.rows[1]):
        return "multi_level_header_needs_adapter"
    return "grid_candidate_needs_visible_alignment"


def build_support() -> dict:
    manifest = json.loads(_pinned(RAW_MANIFEST, RAW_MANIFEST_SHA256))
    if (
        manifest["planned_pages"] != manifest["frozen_pages"]
        or manifest["failed_pages"]
    ):
        raise ValueError("raw freeze is incomplete")
    page_audit = [
        json.loads(row) for row in _pinned(PAGE_AUDIT, PAGE_AUDIT_SHA256).splitlines()
    ]
    targets: dict[str, set[tuple[str, str]]] = defaultdict(set)
    gross = 0
    for page in page_audit:
        for reject in page["rejected_tables"]:
            if reject["reason"] == "row_width_mismatch":
                gross += 1
                targets[page["doc_id"]].add((reject["heading"], reject["header"]))
    ledger = []
    for source in manifest["records"]:
        raw_path = RAW_MANIFEST.parent / source["response_path"]
        raw = _pinned(raw_path, source["response_sha256"])
        page = json.loads(raw)["query"]["pages"][0]
        revision = page["revisions"][0]
        if page["title"] != source["title"] or revision["revid"] != source["revid"]:
            raise ValueError(f"revision identity changed: {raw_path}")
        wikitext = revision["slots"]["main"]["content"]
        lines = wikitext.splitlines()
        tables = parse_tables(wikitext)
        matches: dict[tuple[str, str], list[dict]] = defaultdict(list)
        heading = ""
        prior_cursor = 0
        for table in tables:
            for prior in lines[prior_cursor : table.source_start_line - 1]:
                found = _H2.fullmatch(prior)
                if found:
                    heading = _clean_wikitext_inline(found.group(1))
            prior_cursor = table.source_end_line
            snippet = "\n".join(
                lines[table.source_start_line - 1 : table.source_end_line]
            )
            visible_lines = set(
                render_wikitext(source["title"], snippet).text.splitlines()
            )
            for key in targets[source["doc_id"]]:
                if key[0] == heading and key[1] in visible_lines:
                    matches[key].append(
                        {
                            "source_start_line": table.source_start_line,
                            "source_end_line": table.source_end_line,
                            "grid": table.grid,
                            "grid_reject_reason": table.reason,
                        }
                    )
        for heading, header in sorted(targets[source["doc_id"]]):
            matched = matches[heading, header]
            status = _target_status(matched)
            ledger.append(
                {
                    "doc_id": source["doc_id"],
                    "source_group": source["source_group"],
                    "domain": source["domain"],
                    "topic": source["topic"],
                    "title": source["title"],
                    "revid": source["revid"],
                    "heading": heading,
                    "visible_header": header,
                    "raw_table_matches": len(matched),
                    "valid_grid_matches": sum(
                        item["grid"] is not None for item in matched
                    ),
                    "raw_start_lines": [item["source_start_line"] for item in matched],
                    "status": status,
                }
            )
    counts = Counter(row["status"] for row in ledger)
    return {
        "schema": SCHEMA,
        "raw_manifest_sha256": RAW_MANIFEST_SHA256,
        "page_audit_sha256": PAGE_AUDIT_SHA256,
        "gross_p100_width_rejections": gross,
        "unique_doc_heading_header": len(ledger),
        "status_counts": dict(sorted(counts.items())),
        "reader_tasks_admitted": 0,
        "ledger": ledger,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/candidates/p104_wiki_width_target_support_v1/ledger.json",
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = build_support()
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.verify_only:
        if not args.output.is_file() or args.output.read_text() != payload:
            raise ValueError("target support output differs from exact replay")
    else:
        if args.output.exists():
            raise ValueError("target support output already exists; use --verify-only")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "gross_p100_width_rejections",
                    "unique_doc_heading_header",
                    "status_counts",
                    "reader_tasks_admitted",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
