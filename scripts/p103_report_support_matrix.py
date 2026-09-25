"""Probe frozen annual filings for reader-visible cross-section task support.

This is a feasibility ledger, not a QA generator. A Note link is usable only
when the source line omits the answer, the target heading and body are visible,
and a table link retains row/column structure. Item links are recorded but
cannot become rule tasks without typed conditions and an executor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.taskbank_context import render_document

SCHEMA = "longworld.p103-report-support-matrix.v1"
NOTE_HEADING = re.compile(r"^Note\s*(\d{1,2})\s*[:.\-–—]\s*(.{4,100})$", re.IGNORECASE)
NOTE_REFERENCE = re.compile(
    r"\b(?:see|refers? to|discussed in)\s+(?:our\s+)?[\"“]?Note\s*(\d{1,2})\b",
    re.IGNORECASE,
)
ANY_NOTE = re.compile(r"\bNote\s*(\d{1,2})\b", re.IGNORECASE)
LOCAL_TITLE = re.compile(r"^\s*[:.\-–—]\s*[A-Z][A-Za-z, &()/-]{4,90}")
ITEM_HEADING = re.compile(
    r"^Item\s+(\d{1,2}[A-Z]?)\s*[.:\-–—]\s*(.{4,100})$", re.IGNORECASE
)
ITEM_REFERENCE = re.compile(
    r"\b(?:see|refers? to)\b[^.\n]{0,60}?\bItem\s+(\d{1,2}[A-Z]?)\b",
    re.IGNORECASE,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict[str, str]) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("source pin requires path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or sha(path) != pin["sha256"]:
        raise ValueError(f"source pin drift: {relative}")
    return path


def _lines(text: str) -> list[tuple[int, str, str]]:
    rows = []
    position = 0
    for line in text.splitlines(keepends=True):
        rows.append((position, line.rstrip("\r\n"), " ".join(line.split())))
        position += len(line)
    return rows


def _body_after(lines: list[tuple[int, str, str]], index: int) -> bool:
    """Exclude a contents-list heading that points only to another heading."""
    considered = 0
    for _, raw, normalized in lines[index + 1 : index + 10]:
        if not normalized or normalized.isdecimal():
            continue
        considered += 1
        if NOTE_HEADING.fullmatch(normalized) or ITEM_HEADING.fullmatch(normalized):
            return False
        if len(normalized) >= 50 and "\t" not in raw:
            return True
        if considered >= 5:
            break
    return False


def _headings(
    lines: list[tuple[int, str, str]], pattern: re.Pattern
) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for index, (position, _, normalized) in enumerate(lines):
        match = pattern.fullmatch(normalized)
        if match:
            grouped[match.group(1).upper()].append(
                {
                    "title": match.group(2).strip(),
                    "position": position,
                    "has_visible_body": _body_after(lines, index),
                }
            )
    return {
        number: {
            "titles": sorted({row["title"] for row in rows}, key=str.casefold),
            "body_positions": [
                row["position"] for row in rows if row["has_visible_body"]
            ],
            "occurrences": len(rows),
        }
        for number, rows in grouped.items()
    }


def _target(number: str, headings: dict[str, dict]) -> tuple[str, str | None]:
    heading = headings.get(number.upper())
    if heading is None:
        return "target_heading_absent", None
    if len({title.casefold() for title in heading["titles"]}) != 1:
        return "target_heading_ambiguous", None
    if not heading["body_positions"]:
        return "target_body_absent", None
    return "bound", heading["titles"][0]


def scan_visible(text: str) -> dict[str, Any]:
    lines = _lines(text)
    note_headings = _headings(lines, NOTE_HEADING)
    item_headings = _headings(lines, ITEM_HEADING)
    note_status = Counter()
    table_status = Counter()
    item_status = Counter()
    examples: dict[str, list[dict]] = defaultdict(list)
    bound_note = 0
    bound_table = 0
    for position, raw, normalized in lines:
        references = list(NOTE_REFERENCE.finditer(normalized))
        if len(references) == 1 and 40 <= len(normalized) <= 300:
            match = references[0]
            status, title = _target(match.group(1), note_headings)
            if LOCAL_TITLE.match(normalized[match.end() :]) or (
                title is not None and title.casefold() in normalized.casefold()
            ):
                status = "answer_on_reference_line"
            elif "\t" in raw:
                status = "tabular_or_layout_line_without_row_binding"
            elif status == "bound":
                bound_note += 1
            note_status[status] += 1
            if len(examples[status]) < 2:
                examples[status].append(
                    {
                        "note": match.group(1),
                        "visible_offset": position,
                        "line": normalized[:220],
                    }
                )
        if "\t" in raw:
            note_numbers = {match.group(1) for match in ANY_NOTE.finditer(normalized)}
            if note_numbers:
                cells = [cell.strip() for cell in raw.split("\t") if cell.strip()]
                if len(cells) < 3 or not any(
                    re.search(r"\d", cell) for cell in cells[1:]
                ):
                    status = "no_complete_row_with_numeric_cell"
                elif len(note_numbers) != 1:
                    status = "ambiguous_note_number"
                else:
                    number = next(iter(note_numbers))
                    status, title = _target(number, note_headings)
                    if title is not None and title.casefold() in normalized.casefold():
                        status = "answer_on_table_row"
                    elif status == "bound":
                        bound_table += 1
                table_status[status] += 1
                if len(examples["table." + status]) < 2:
                    examples["table." + status].append(
                        {"visible_offset": position, "line": normalized[:220]}
                    )
        references = list(ITEM_REFERENCE.finditer(normalized))
        if len(references) == 1 and 40 <= len(normalized) <= 300:
            status, title = _target(references[0].group(1), item_headings)
            if title is not None and title.casefold() in normalized.casefold():
                status = "answer_on_reference_line"
            elif status == "bound":
                status = "visible_navigation_only_no_typed_rule"
            item_status[status] += 1
    # A title lookup can be proposed only after a unique cue is proven in the
    # final reader. The structural probe deliberately admits no QA by itself.
    return {
        "visible_note_numbers": len(note_headings),
        "visible_item_numbers": len(item_headings),
        "notes_with_unique_title_and_body": sum(
            _target(n, note_headings)[0] == "bound" for n in note_headings
        ),
        "note_reference_status": dict(sorted(note_status.items())),
        "table_note_status": dict(sorted(table_status.items())),
        "item_reference_status": dict(sorted(item_status.items())),
        "structurally_bound_bare_note_lines": bound_note,
        "structurally_bound_table_note_rows": bound_table,
        "examples": dict(sorted(examples.items())),
    }


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("wrong P103 config schema")
    catalog_path = _pin(config["source_catalog"])
    p98_path = _pin(config["p98_ledger"])
    catalog = json.loads(catalog_path.read_text())
    prior = json.loads(p98_path.read_text())
    if prior.get("issuer_worlds") != len(catalog["jobs"]):
        raise ValueError("P98 comparison ledger has different issuer count")
    rows = []
    for job in catalog["jobs"]:
        manifest_path = _pin(
            {"path": job["source_manifest"], "sha256": job["source_manifest_sha256"]}
        )
        manifest = json.loads(manifest_path.read_text())
        if len(manifest["records"]) != 4:
            raise ValueError("expected four frozen filings per issuer")
        for record in manifest["records"]:
            source = record["text"]
            source_sha = hashlib.sha256(source.encode()).hexdigest()
            if record.get("text_sha256", source_sha) != source_sha:
                raise ValueError("frozen source text hash mismatch")
            rendered = render_document(
                {
                    "record_id": f"{job['issuer']}:{record['report_date']}",
                    "text": source,
                    "source_sha256": source_sha,
                }
            )
            support = scan_visible(rendered["text"])
            rows.append(
                {
                    "issuer": job["issuer"],
                    "report_year": record["report_date"][:4],
                    "source_manifest_sha256": job["source_manifest_sha256"],
                    "source_text_sha256": source_sha,
                    "visible_text_sha256": rendered["text_sha256"],
                    "visible_characters": len(rendered["text"]),
                    "support": support,
                    "admission": "unsupported_for_verified_cross_section_task",
                }
            )
    totals = Counter()
    for row in rows:
        support = row["support"]
        totals["visible_note_numbers"] += support["visible_note_numbers"]
        totals["visible_item_numbers"] += support["visible_item_numbers"]
        totals["notes_with_unique_title_and_body"] += support[
            "notes_with_unique_title_and_body"
        ]
        totals["structurally_bound_bare_note_lines"] += support[
            "structurally_bound_bare_note_lines"
        ]
        totals["structurally_bound_table_note_rows"] += support[
            "structurally_bound_table_note_rows"
        ]
        for profile in (
            "note_reference_status",
            "table_note_status",
            "item_reference_status",
        ):
            totals.update(
                {
                    f"{profile}.{reason}": count
                    for reason, count in support[profile].items()
                }
            )
    return {
        "schema": SCHEMA,
        "config_sha256": sha(config_path),
        "source_catalog": config["source_catalog"],
        "p98_ledger": config["p98_ledger"],
        "issuer_worlds": len(catalog["jobs"]),
        "annual_filings": len(rows),
        "profile_support": {
            "note_reference_to_visible_note_body": totals[
                "structurally_bound_bare_note_lines"
            ],
            "complete_table_row_to_visible_note_body": totals[
                "structurally_bound_table_note_rows"
            ],
            "item_reference_rule_application": 0,
        },
        "tasks_admitted": 0,
        "reason": "structural links alone do not provide a unique task cue, typed rule program, and answer-changing final-reader intervention",
        "totals": dict(sorted(totals.items())),
        "rows": rows,
        "train_ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = build(args.config)
    serialized = (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if args.verify_only:
        if args.output.read_bytes() != serialized:
            raise ValueError("P103 support matrix replay drift")
    else:
        if args.output.exists():
            raise ValueError("P103 output must be new")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(serialized)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "rows"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
