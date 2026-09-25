"""Audit P95 width targets against exact raw grids and final-reader cells."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import _clean_wikitext_inline, render_wikitext
from scripts.p100_wiki_categorical_scan import (
    options,
)
from scripts.p100_wiki_categorical_scan import (
    parse_tables as parse_reader_tables,
)
from scripts.p104_match_width_targets import _H2, _target_status
from scripts.p104_wiki_table_grid import parse_tables as parse_raw_tables
from scripts.p105_wiki_reader_cells import render_table, replace_in_document
from scripts.p106_freeze_width_revisions import _pin, _sha

SCHEMA = "longworld.p106-wiki-width-target-support.v1"


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != "longworld.p106-wiki-width-support-plan.v1"
        or not 1 <= config.get("max_tasks_per_table", 0) <= 8
    ):
        raise ValueError("P106 support config invalid")
    raw_manifest = json.loads(_pin(config["raw_manifest"]).read_text())
    audit = [
        json.loads(line) for line in _pin(config["page_audit"]).read_text().splitlines()
    ]
    pool = json.loads(_pin(config["source_pool"]).read_text())
    if (
        raw_manifest["frozen_pages"] != raw_manifest["planned_pages"]
        or raw_manifest["failed_pages"]
        or raw_manifest["source_pins"]["source_pool"]["sha256"]
        != config["source_pool"]["sha256"]
        or raw_manifest["source_pins"]["page_audit"]["sha256"]
        != config["page_audit"]["sha256"]
    ):
        raise ValueError("P106 raw/pool/audit lineage differs")
    source_by_name = {source["name"]: source for source in pool["sources"]}
    targets: dict[str, set[tuple[str, str]]] = defaultdict(set)
    gross = 0
    for page in audit:
        for rejected in page["rejected_tables"]:
            if rejected["reason"] == "row_width_mismatch":
                gross += 1
                targets[page["doc_id"]].add((rejected["heading"], rejected["header"]))
    ledger = []
    raw_base = (ROOT / config["raw_manifest"]["path"]).parent
    for record in raw_manifest["records"]:
        raw_path = raw_base / record["response_path"]
        if _sha(raw_path) != record["response_sha256"]:
            raise ValueError(f"P106 raw response changed: {raw_path}")
        page = json.loads(raw_path.read_text())["query"]["pages"][0]
        revision = page["revisions"][0]
        if page["title"] != record["title"] or revision["revid"] != record["revid"]:
            raise ValueError("P106 raw revision identity differs")
        wikitext = revision["slots"]["main"]["content"]
        raw_lines = wikitext.splitlines()
        tables = parse_raw_tables(wikitext)
        source = source_by_name[record["source_group"]]
        snapshot = json.loads(_pin(source["snapshot"]).read_text())
        document = next(
            doc for doc in snapshot["documents"] if doc["doc_id"] == record["doc_id"]
        )
        if (
            source["split"] != record["split"]
            or source["snapshot"]["sha256"] != record["source_snapshot_sha256"]
            or document["title"] != record["title"]
            or document["page_url"] != record["page_url"]
            or document["revision_url"] != record["revision_url"]
        ):
            raise ValueError("P106 P95 source title/URL/split differs")
        matches: dict[tuple[str, str], list[dict]] = defaultdict(list)
        heading = ""
        prior_cursor = 0
        for table in tables:
            for line in raw_lines[prior_cursor : table.source_start_line - 1]:
                found = _H2.fullmatch(line)
                if found:
                    heading = _clean_wikitext_inline(found.group(1))
            prior_cursor = table.source_end_line
            snippet = "\n".join(
                raw_lines[table.source_start_line - 1 : table.source_end_line]
            )
            visible = set(render_wikitext(record["title"], snippet).text.splitlines())
            for key in targets[record["doc_id"]]:
                if key[0] == heading and key[1] in visible:
                    matches[key].append(
                        {
                            "source_start_line": table.source_start_line,
                            "source_end_line": table.source_end_line,
                            "grid": table.grid,
                            "grid_reject_reason": table.reason,
                        }
                    )
        for heading, header in sorted(targets[record["doc_id"]]):
            matched = matches[heading, header]
            structural = _target_status(matched)
            reader_status = "not_structurally_supported"
            raw_start_lines = [item["source_start_line"] for item in matched]
            option_count = 0
            if structural == "grid_candidate_needs_visible_alignment":
                table = matched[0]
                try:
                    reader_table = render_table(
                        wikitext, table["source_start_line"], table["source_end_line"]
                    )
                    replaced, _start, _end = replace_in_document(
                        title=record["title"],
                        wikitext=wikitext,
                        frozen_reader_text=document["text"],
                        table=reader_table,
                    )
                    parsed, rejected = parse_reader_tables(
                        "## " + heading + "\n" + reader_table.text + "\n\n"
                    )
                    if len(parsed) != 1 or rejected:
                        raise ValueError("final reader table failed strict replay")
                    if not replaced:
                        raise ValueError("final reader replacement empty")
                    option_count = len(
                        options(parsed[0], max_tasks=config["max_tasks_per_table"])
                    )
                    reader_status = (
                        "reader_cell_aligned_with_options"
                        if option_count
                        else "reader_cell_aligned_no_category"
                    )
                except ValueError as error:
                    reader_status = str(error)
            ledger.append(
                {
                    "doc_id": record["doc_id"],
                    "source_group": record["source_group"],
                    "split": record["split"],
                    "domain": record["domain"],
                    "topic": record["topic"],
                    "title": record["title"],
                    "page_url": record["page_url"],
                    "revid": record["revid"],
                    "heading": heading,
                    "visible_header": header,
                    "raw_table_matches": len(matched),
                    "valid_grid_matches": sum(
                        item["grid"] is not None for item in matched
                    ),
                    "raw_start_lines": raw_start_lines,
                    "structural_status": structural,
                    "reader_status": reader_status,
                    "category_option_count": option_count,
                }
            )
    return {
        "schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "raw_manifest_sha256": config["raw_manifest"]["sha256"],
        "page_audit_sha256": config["page_audit"]["sha256"],
        "source_pool_sha256": config["source_pool"]["sha256"],
        "gross_width_rejections": gross,
        "unique_doc_heading_header": len(ledger),
        "structural_status_counts": dict(
            sorted(Counter(row["structural_status"] for row in ledger).items())
        ),
        "reader_status_counts": dict(
            sorted(Counter(row["reader_status"] for row in ledger).items())
        ),
        "reader_supported_tables": sum(
            row["reader_status"] == "reader_cell_aligned_with_options" for row in ledger
        ),
        "candidate_category_options": sum(
            row["category_option_count"] for row in ledger
        ),
        "reader_tasks_admitted": 0,
        "ledger": ledger,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = build(args.config)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.verify_only:
        if not args.output.is_file() or args.output.read_text() != content:
            raise ValueError("P106 support ledger replay differs")
    else:
        if args.output.exists():
            raise ValueError("P106 support ledger already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "gross_width_rejections",
                    "unique_doc_heading_header",
                    "reader_supported_tables",
                    "candidate_category_options",
                    "reader_status_counts",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
