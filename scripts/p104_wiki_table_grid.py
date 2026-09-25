"""Conservative grid parser for frozen MediaWiki Wikitext tables.

It preserves empty cells and expands explicit row/column spans. Ambiguous
structure is rejected rather than guessed. The resulting grid is an audit
artifact; it is not yet a reader-visible task or a semantic fact source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p104-wiki-wikitext-grid-audit.v1"
MAX_SPAN = 100
_SPAN = re.compile(r"(?:^|\s)(rowspan|colspan)\s*=\s*['\"]?(\d+)", re.IGNORECASE)
_ATTRIBUTE = re.compile(
    r"(?:^|\s)(?:rowspan|colspan|style|class|align|valign|width|height|scope|bgcolor|data-sort-value)\s*=",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Cell:
    raw: str
    header: bool
    rowspan: int = 1
    colspan: int = 1


@dataclass(frozen=True)
class Grid:
    rows: tuple[tuple[Cell, ...], ...]
    width: int
    source_start_line: int
    source_end_line: int


@dataclass(frozen=True)
class TableResult:
    grid: Grid | None
    reason: str | None
    source_start_line: int
    source_end_line: int


def _top_level_delimiters(text: str, marker: str) -> list[int]:
    """Find `||` or `!!` outside templates and Wiki links."""
    positions = []
    template_depth = 0
    link_depth = 0
    index = 0
    while index < len(text):
        two = text[index : index + 2]
        if two == "{{":
            template_depth += 1
            index += 2
        elif two == "}}" and template_depth:
            template_depth -= 1
            index += 2
        elif two == "[[":
            link_depth += 1
            index += 2
        elif two == "]]" and link_depth:
            link_depth -= 1
            index += 2
        elif two == marker and not template_depth and not link_depth:
            positions.append(index)
            index += 2
        else:
            index += 1
    return positions


def _split_cells(text: str, header: bool) -> list[str]:
    marker = "!!" if header else "||"
    positions = _top_level_delimiters(text, marker)
    starts = [0] + [position + 2 for position in positions]
    ends = positions + [len(text)]
    return [text[start:end] for start, end in zip(starts, ends)]


def _top_level_attr_pipe(text: str) -> int | None:
    """Locate the attribute/content separator, never a template/link pipe."""
    template_depth = 0
    link_depth = 0
    for index, char in enumerate(text):
        two = text[index : index + 2]
        if two == "{{":
            template_depth += 1
        elif two == "}}" and template_depth:
            template_depth -= 1
        elif two == "[[":
            link_depth += 1
        elif two == "]]" and link_depth:
            link_depth -= 1
        if char == "|" and not template_depth and not link_depth:
            prefix = text[:index]
            if _ATTRIBUTE.search(prefix):
                return index
            return None
    return None


def _parse_cell(part: str, header: bool) -> Cell:
    part = part.strip()
    pipe = _top_level_attr_pipe(part)
    attrs = part[:pipe] if pipe is not None else ""
    content = part[pipe + 1 :] if pipe is not None else part
    spans = {name.lower(): int(value) for name, value in _SPAN.findall(attrs)}
    rowspan = spans.get("rowspan", 1)
    colspan = spans.get("colspan", 1)
    if rowspan < 1 or colspan < 1 or rowspan > MAX_SPAN or colspan > MAX_SPAN:
        raise ValueError("invalid_span")
    return Cell(content.strip(), header, rowspan, colspan)


def _expand(rows: list[list[Cell]], start: int, end: int) -> Grid:
    if not rows:
        raise ValueError("no_rows")
    pending: dict[int, tuple[Cell, int]] = {}
    expanded = []
    width = None
    for row in rows:
        cells = {col: cell for col, (cell, _remaining) in pending.items()}
        next_pending = {
            col: (cell, remaining - 1)
            for col, (cell, remaining) in pending.items()
            if remaining > 1
        }
        col = 0
        for cell in row:
            while col in cells:
                col += 1
            for offset in range(cell.colspan):
                target = col + offset
                if target in cells or target >= 200:
                    raise ValueError("span_overlap_or_too_wide")
                cells[target] = cell
                if cell.rowspan > 1:
                    next_pending[target] = (cell, cell.rowspan - 1)
            col += cell.colspan
        if not cells or set(cells) != set(range(max(cells) + 1)):
            raise ValueError("grid_gap")
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise ValueError("row_width_mismatch_after_spans")
        expanded.append(tuple(cells[col] for col in range(width)))
        pending = next_pending
    if pending:
        raise ValueError("rowspan_past_table_end")
    return Grid(tuple(expanded), width or 0, start, end)


def parse_tables(wikitext: str) -> tuple[TableResult, ...]:
    results = []
    in_table = False
    table_depth = 0
    start = 0
    rows: list[list[Cell]] = []
    current: list[Cell] = []
    failure = None
    for line_number, raw in enumerate(wikitext.splitlines(), 1):
        line = raw.lstrip()
        if not in_table:
            if line.startswith("{|"):
                in_table = True
                table_depth = 1
                start = line_number
                rows = []
                current = []
                failure = None
            continue
        if "{|" in line:
            failure = "nested_table"
            table_depth += line.count("{|")
            continue
        if line.startswith("|}"):
            table_depth -= 1
            if table_depth:
                continue
            if current:
                rows.append(current)
            if failure is None:
                try:
                    grid = _expand(rows, start, line_number)
                except ValueError as error:
                    results.append(TableResult(None, str(error), start, line_number))
                else:
                    results.append(TableResult(grid, None, start, line_number))
            else:
                results.append(TableResult(None, failure, start, line_number))
            in_table = False
            continue
        if failure is not None:
            continue
        if line.startswith("|-"):
            if current:
                rows.append(current)
                current = []
            continue
        if line.startswith("|+"):
            continue
        if line.startswith(("!", "|")):
            header = line.startswith("!")
            try:
                current.extend(
                    _parse_cell(part, header) for part in _split_cells(line[1:], header)
                )
            except ValueError as error:
                failure = str(error)
            continue
        if line and current:
            prior = current[-1]
            current[-1] = Cell(
                prior.raw + "\n" + raw, prior.header, prior.rowspan, prior.colspan
            )
        elif line and not current:
            failure = "unattached_continuation"
    if in_table:
        results.append(
            TableResult(None, "unclosed_table", start, len(wikitext.splitlines()))
        )
    return tuple(results)


def audit(raw_manifest: Path) -> dict:
    manifest = json.loads(raw_manifest.read_text())
    if (
        manifest["frozen_pages"] != manifest["planned_pages"]
        or manifest["failed_pages"]
    ):
        raise ValueError("raw revision freeze incomplete")
    records = []
    gross = Counter()
    for source in manifest["records"]:
        raw_path = raw_manifest.parent / source["response_path"]
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["response_sha256"]:
            raise ValueError(f"raw revision changed: {raw_path}")
        page = json.loads(raw)["query"]["pages"][0]
        revision = page["revisions"][0]
        if page["title"] != source["title"] or revision["revid"] != source["revid"]:
            raise ValueError(f"revision identity changed: {raw_path}")
        wikitext = revision["slots"]["main"]["content"]
        tables = parse_tables(wikitext)
        reasons = Counter(item.reason or "grid_valid" for item in tables)
        gross.update(reasons)
        records.append(
            {
                "source_group": source["source_group"],
                "doc_id": source["doc_id"],
                "domain": source["domain"],
                "topic": source["topic"],
                "title": source["title"],
                "revid": source["revid"],
                "table_count": len(tables),
                "reasons": dict(sorted(reasons.items())),
                "grid_valid_tables": [
                    {
                        "source_start_line": item.source_start_line,
                        "source_end_line": item.source_end_line,
                        "rows": len(item.grid.rows),
                        "width": item.grid.width,
                        "first_row": [cell.raw[:80] for cell in item.grid.rows[0]],
                    }
                    for item in tables
                    if item.grid is not None
                ],
            }
        )
    return {
        "schema": SCHEMA,
        "raw_manifest_sha256": hashlib.sha256(raw_manifest.read_bytes()).hexdigest(),
        "pages": len(records),
        "gross_tables": sum(gross.values()),
        "grid_valid_tables": gross["grid_valid"],
        "rejection_reasons": dict(
            sorted((k, v) for k, v in gross.items() if k != "grid_valid")
        ),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-manifest",
        type=Path,
        default=ROOT / "data/candidates/p104_wiki_width_raw_v1/manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/candidates/p104_wiki_width_grid_v1/audit.json",
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.raw_manifest)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.verify_only:
        if not args.output.is_file() or args.output.read_text() != payload:
            raise ValueError("grid audit output differs from exact replay")
    else:
        if args.output.exists():
            raise ValueError("grid audit output already exists; use --verify-only")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "pages",
                    "gross_tables",
                    "grid_valid_tables",
                    "rejection_reasons",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
