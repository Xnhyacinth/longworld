"""Freeze exact-oldid Wikipedia HTML and audit source table grids.

A complete grid is a source-shape candidate, not a question or gold label.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import WikiHttpFetcher
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p122-wiki-html-grid.v1"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def local(path: Path) -> Path:
    path = (ROOT / path).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal forbidden")
    target = path.resolve()
    if not target.is_relative_to(ROOT) and not target.is_relative_to(
        (ROOT / "data").resolve()
    ):
        raise ValueError("path escapes workspace storage")
    return path


def pinned(value: dict[str, str]) -> Path:
    if set(value) != {"path", "sha256"}:
        raise ValueError("pin needs path and sha256")
    path = local(Path(value["path"]))
    if not path.is_file() or sha(path.read_bytes()) != value["sha256"]:
        raise ValueError(f"pin changed: {value['path']}")
    return path


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class Tables(HTMLParser):
    """Read DOM cell boundaries while retaining source offsets and citations."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.html = html
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", html)]
        self.tables: list[dict] = []
        self.notes: dict[str, str] = {}
        self.table: dict | None = None
        self.depth = 0
        self.row: list[dict] | None = None
        self.cell: dict | None = None
        self.note: dict | None = None
        self.heading: dict | None = None
        self.section_path: list[str] = []
        self.caption = False
        self.reference = False

    def _char_offset(self) -> int:
        row, column = self.getpos()
        return self.line_starts[row - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.table = {
                    "class": a.get("class", ""),
                    "caption": "",
                    "section_path": list(self.section_path),
                    "rows": [],
                    "nested": 0,
                    "hidden": False,
                }
            elif self.table is not None:
                self.table["nested"] += 1
            return
        if self.depth > 1:
            return
        if self.depth == 0 and tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.heading = {"level": int(tag[1]), "text": ""}
        if tag == "li" and a.get("id", "").startswith("cite_note-"):
            self.note = {"id": a["id"], "text": ""}
        if self.table is None:
            return
        if "display:none" in a.get("style", "").replace(" ", "").lower():
            self.table["hidden"] = True
        if tag == "caption":
            self.caption = True
        elif tag == "tr":
            self.row = []
            self.table["rows"].append(self.row)
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = {
                "kind": tag,
                "text": "",
                "rowspan": a.get("rowspan", "1"),
                "colspan": a.get("colspan", "1"),
                "scope": a.get("scope", ""),
                "headers": a.get("headers", ""),
                "links": [],
                "refs": [],
                "start": self._char_offset(),
                "end": None,
            }
            self.row.append(self.cell)
        elif (
            tag == "sup"
            and self.cell is not None
            and "reference" in a.get("class", "").split()
        ):
            self.reference = True
        elif tag == "a" and self.cell is not None:
            href = a.get("href", "")
            if href:
                self.cell["links"].append(href)
                if self.reference and href.startswith("#cite_note-"):
                    self.cell["refs"].append(href[1:])
        elif tag == "img" and self.cell is not None and a.get("alt"):
            self.cell["image_alt"] = a["alt"]

    def handle_data(self, data: str) -> None:
        if self.note is not None:
            self.note["text"] += data
        if self.heading is not None:
            self.heading["text"] += data
        if self.depth == 1 and self.cell is not None:
            self.cell["text"] += data
        elif self.depth == 1 and self.caption and self.table is not None:
            self.table["caption"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3", "h4", "h5", "h6"} and self.heading is not None:
            level, value = self.heading["level"], clean(self.heading["text"])
            self.section_path = self.section_path[: max(0, level - 2)]
            if value:
                self.section_path.append(value)
            self.heading = None
        if tag == "li" and self.note is not None:
            self.notes[self.note["id"]] = clean(self.note["text"])
            self.note = None
        if tag == "table":
            if self.depth == 1 and self.table is not None:
                self.tables.append(self.table)
                self.table = None
                self.row = None
                self.cell = None
            self.depth = max(0, self.depth - 1)
        elif self.depth == 1 and tag in {"th", "td"} and self.cell is not None:
            end = self.html.find(">", self._char_offset())
            self.cell["end"] = end + 1 if end >= 0 else None
            self.cell = None
        elif self.depth == 1 and tag == "tr":
            self.row = None
        elif self.depth == 1 and tag == "caption":
            self.caption = False
        elif self.depth == 1 and tag == "sup":
            self.reference = False


def grid(table: dict, notes: dict[str, str], html: str) -> tuple[dict, list[str]]:
    """Expand row/col spans with stable origin IDs; reject incomplete geometry."""
    reasons = []
    if table["nested"]:
        reasons.append("nested_table")
    if table["hidden"]:
        reasons.append("hidden_markup")
    carry: dict[int, tuple[str, int]] = {}
    rows: list[list[str | None]] = []
    origins: dict[str, dict] = {}
    for row_index, source_row in enumerate(table["rows"]):
        if not source_row:
            continue
        current = {col: item[0] for col, item in carry.items()}
        following = {
            col: (item[0], item[1] - 1) for col, item in carry.items() if item[1] > 1
        }
        position = 0
        for source_cell in source_row:
            try:
                rowspan, colspan = (
                    int(source_cell["rowspan"]),
                    int(source_cell["colspan"]),
                )
            except ValueError:
                reasons.append("invalid_span")
                rowspan = colspan = 1
            if not (1 <= rowspan <= 1000 and 1 <= colspan <= 100):
                reasons.append("invalid_span")
                rowspan = colspan = 1
            while any(position + col in current for col in range(colspan)):
                position += 1
            cell_id = f"{row_index}:{len(origins)}"
            start, end = source_cell["start"], source_cell["end"]
            if end is None or not html[start:end].lower().startswith(("<th", "<td")):
                reasons.append("source_cell_span_missing")
            refs = list(dict.fromkeys(source_cell["refs"]))
            if any(ref not in notes for ref in refs):
                reasons.append("unresolved_citation")
            origins[cell_id] = {
                "kind": source_cell["kind"],
                "text": clean(source_cell["text"]),
                "rowspan": rowspan,
                "colspan": colspan,
                "scope": source_cell["scope"],
                "headers": source_cell["headers"],
                "links": list(dict.fromkeys(source_cell["links"])),
                "footnote_refs": refs,
                "footnotes": {ref: notes[ref] for ref in refs if ref in notes},
                "html_span": [start, end],
                "html_sha256": sha(html[start:end].encode())
                if end is not None
                else None,
                **(
                    {"image_alt": source_cell["image_alt"]}
                    if "image_alt" in source_cell
                    else {}
                ),
            }
            for col in range(position, position + colspan):
                current[col] = cell_id
                if rowspan > 1:
                    following[col] = (cell_id, rowspan - 1)
            position += colspan
        width = max(current, default=-1) + 1
        if set(current) != set(range(width)):
            reasons.append("grid_hole")
        rows.append([current.get(col) for col in range(width)])
        carry = following
    if carry:
        reasons.append("rowspan_outlives_table")
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        reasons.append("nonuniform_expanded_width")
    width = max(widths, default=0)
    if not 3 <= width <= 50:
        reasons.append("unsupported_width")
    header_rows = 0
    for row in rows:
        if all(cell is not None and origins[cell]["kind"] == "th" for cell in row):
            header_rows += 1
        else:
            break
    if not header_rows or len(rows) - header_rows < 8:
        reasons.append("no_header_or_too_few_body_rows")
    if any(
        all(cell is not None and origins[cell]["kind"] == "th" for cell in row)
        for row in rows[header_rows:]
    ):
        reasons.append("all_header_row_inside_body")
    paths = []
    for col in range(width):
        values = []
        for row in rows[:header_rows]:
            if col < len(row) and row[col] is not None:
                text = origins[row[col]]["text"]
                if text and (not values or values[-1] != text):
                    values.append(text)
        paths.append(values)
    if any(not path for path in paths):
        reasons.append("empty_header_path")
    if len({tuple(path) for path in paths}) != len(paths):
        reasons.append("duplicate_header_path")
    result = {
        "class": table["class"],
        "caption": clean(table["caption"]),
        "section_path": table["section_path"],
        "width": width,
        "header_rows": header_rows,
        "body_rows": max(0, len(rows) - header_rows),
        "header_paths": paths,
        "origins": origins,
        "rows": rows,
    }
    return result, sorted(set(reasons))


def sources(config: dict) -> list[dict]:
    manifest = json.loads(pinned(config["source_manifest"]).read_text())
    pool = json.loads(pinned(config["source_pool"]).read_text())
    ledger = {
        row["canonical_title"]: row
        for raw in pinned(config["source_ledger"]).read_text().splitlines()
        if (row := json.loads(raw)).get("status") == "admitted_source"
    }
    if (
        manifest["source_pool_sha256"] != config["source_pool"]["sha256"]
        or manifest["source_ledger_sha256"] != config["source_ledger"]["sha256"]
    ):
        raise ValueError("P119 source pins disagree")
    by_title = {}
    for source in pool["sources"]:
        snap = _snapshot(ROOT, source["snapshot"])
        doc = snap["documents"][0]
        by_title[doc["title"]] = (source, snap, doc)
    selected = []
    for title in config["titles"]:
        if title not in by_title or title not in ledger:
            raise ValueError(f"selected source missing: {title}")
        source, snap, doc = by_title[title]
        if (
            doc["revision_url"] != ledger[title]["revision_url"]
            or sha(doc["text"].encode()) != ledger[title]["body_sha256"]
        ):
            raise ValueError("P119 oldid/body changed")
        selected.append(
            {
                "title": title,
                "domain": source["domain"],
                "oldid": snap["source"]["revisions"][title],
                "page_url": doc["page_url"],
                "revision_url": doc["revision_url"],
                "rendered_body_sha256": ledger[title]["body_sha256"],
                "snapshot": source["snapshot"],
            }
        )
    if len(selected) != len(set(config["titles"])) or not 1 <= len(selected) <= 24:
        raise ValueError("sample titles repeat or exceed cap")
    return selected


def freeze(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config_path, output = local(config_path), local(output)
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 0 < config.get("requests_per_second", 0) <= 0.5
        or not 1 <= config.get("max_html_bytes_per_page", 0) <= 5_000_000
    ):
        raise ValueError("invalid bounded P122 config")
    selected = sources(config)
    path = output / "source_manifest.json"
    if path.exists() != verify_only:
        raise ValueError("freeze needs new output or existing replay")
    output.mkdir(parents=True, exist_ok=True)
    fetcher = WikiHttpFetcher(config.get("api", "https://en.wikipedia.org/w/api.php"))
    records = []
    for index, source in enumerate(selected):
        html_path = output / "html" / (sha(source["title"].encode())[:16] + ".html")
        if not html_path.exists():
            if verify_only:
                raise ValueError("frozen HTML missing")
            if index:
                time.sleep(1 / config["requests_per_second"])
            payload = fetcher.get_json(
                {
                    "action": "parse",
                    "format": "json",
                    "formatversion": 2,
                    "oldid": source["oldid"],
                    "prop": "text",
                }
            )
            parsed = payload.get("parse", {})
            if (
                parsed.get("revid") != source["oldid"]
                or parsed.get("title") != source["title"]
                or not isinstance(parsed.get("text"), str)
            ):
                raise ValueError("MediaWiki oldid/title/content changed")
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_bytes(parsed["text"].encode())
        html = html_path.read_bytes()
        if not html or len(html) > config["max_html_bytes_per_page"]:
            raise ValueError("HTML exceeds cap or is empty")
        records.append(
            {
                **source,
                "html_path": str(html_path.relative_to(ROOT)),
                "html_bytes": len(html),
                "html_sha256": sha(html),
            }
        )
    result = {
        "schema": SCHEMA + ".source",
        "config_sha256": sha(config_path.read_bytes()),
        "code_sha256": sha(Path(__file__).read_bytes()),
        "pages": len(records),
        "records": records,
        "train_ready": False,
    }
    data = encoded(result)
    if verify_only:
        if path.read_bytes() != data:
            raise ValueError("frozen source manifest differs")
    else:
        path.write_bytes(data)
    return result


def compile_grids(source_dir: Path, output: Path, *, verify_only: bool = False) -> dict:
    source_dir, output = local(source_dir), local(output)
    source_path = source_dir / "source_manifest.json"
    source = json.loads(source_path.read_text())
    if source.get("schema") != SCHEMA + ".source" or source["code_sha256"] != sha(
        Path(__file__).read_bytes()
    ):
        raise ValueError("frozen source/code changed")
    if output.exists() != verify_only:
        raise ValueError("grid needs new output or existing replay")
    ledger, accepted = [], []
    for page in source["records"]:
        html_path = pinned({"path": page["html_path"], "sha256": page["html_sha256"]})
        html = html_path.read_text()
        parser = Tables(html)
        parser.feed(html)
        for index, table in enumerate(parser.tables):
            if "wikitable" not in table["class"].split():
                continue
            value, reasons = grid(table, parser.notes, html)
            table_id = sha(f"{page['oldid']}:{index}:{page['html_sha256']}".encode())[
                :20
            ]
            status = "grid_valid" if not reasons else "grid_rejected"
            ledger.append(
                {
                    "table_id": table_id,
                    "title": page["title"],
                    "domain": page["domain"],
                    "status": status,
                    "reasons": reasons,
                    "width": value["width"],
                    "header_rows": value["header_rows"],
                    "body_rows": value["body_rows"],
                    "empty_cells": sum(
                        not cell["text"] for cell in value["origins"].values()
                    ),
                    "spanned_cells": sum(
                        cell["rowspan"] > 1 or cell["colspan"] > 1
                        for cell in value["origins"].values()
                    ),
                    "citation_refs": sum(
                        len(cell["footnote_refs"]) for cell in value["origins"].values()
                    ),
                }
            )
            if not reasons:
                accepted.append(
                    {
                        "table_id": table_id,
                        "title": page["title"],
                        "domain": page["domain"],
                        "oldid": page["oldid"],
                        "revision_url": page["revision_url"],
                        "html_sha256": page["html_sha256"],
                        "grid": value,
                        "claim_limit": "HTML grid only; no row/entity/unit/footnote semantic or QA admission",
                    }
                )
    files = {
        "table_ledger.jsonl": b"".join(line(row) for row in ledger),
        "valid_grids.jsonl": b"".join(line(row) for row in accepted),
    }
    result = {
        "schema": SCHEMA + ".grid",
        "source_manifest_sha256": sha(source_path.read_bytes()),
        "code_sha256": sha(Path(__file__).read_bytes()),
        "pages": source["pages"],
        "gross_wikitables": len(ledger),
        "grid_valid_tables": len(accepted),
        "valid_pages": len(
            {row["title"] for row in ledger if row["status"] == "grid_valid"}
        ),
        "valid_body_rows": sum(
            row["body_rows"] for row in ledger if row["status"] == "grid_valid"
        ),
        "reject_reasons": dict(
            Counter(reason for row in ledger for reason in row["reasons"])
        ),
        "domain_valid_tables": dict(
            Counter(row["domain"] for row in ledger if row["status"] == "grid_valid")
        ),
        "files_sha256": {name: sha(data) for name, data in files.items()},
        "train_ready": False,
    }
    files["manifest.json"] = encoded(result)
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("grid file inventory differs")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"grid replay differs: {name}")
    else:
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/p122_wiki_html_grid_v1.json")
    )
    parser.add_argument("--phase", choices=("freeze", "grid"), required=True)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/candidates/p122_wiki_html_source_v1"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        freeze(args.config, args.output, verify_only=args.verify_only)
        if args.phase == "freeze"
        else compile_grids(args.source_dir, args.output, verify_only=args.verify_only)
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "pages",
                    "gross_wikitables",
                    "grid_valid_tables",
                    "valid_pages",
                    "valid_body_rows",
                )
                if key in result
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
