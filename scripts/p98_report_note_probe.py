"""Audit whether frozen annual reports support nonnumeric Note cross-reference tasks."""

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

from longworld.core.taskbank_context import render_document

SCHEMA = "longworld.p98-report-note-probe.v1"
REFERENCE = re.compile(
    r"\b(?:see|refer to|discussed in)\s+(?:our\s+)?[\"“]?Note\s*(\d{1,2})\b",
    re.IGNORECASE,
)
NUMBERED_HEADING = re.compile(
    r"^\s*Note\s*(\d{1,2})\s*[.:—-]\s*[A-Za-z][^\t]{3,100}$",
    re.IGNORECASE,
)
SELF_LABEL = re.compile(r"^\s*[.:—-]\s*[A-Z][A-Za-z, &()/-]{4,90}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_visible(text: str) -> dict:
    """Count only bounded prose lines; preserve rejection evidence without writing tasks."""
    by_note: dict[str, set[str]] = defaultdict(set)
    bare: dict[str, set[str]] = defaultdict(set)
    self_labeled = 0
    usable_lines = 0
    headings: set[str] = set()
    for line in text.splitlines():
        normalized = " ".join(line.split())
        heading = NUMBERED_HEADING.fullmatch(normalized)
        if heading:
            headings.add(heading.group(1))
        matches = list(REFERENCE.finditer(normalized))
        if len(matches) != 1 or not 40 <= len(normalized) <= 300:
            continue
        match = matches[0]
        note = match.group(1)
        usable_lines += 1
        by_note[note].add(normalized)
        if SELF_LABEL.match(normalized[match.end() :]):
            self_labeled += 1
        else:
            bare[note].add(normalized)
    return {
        "usable_reference_lines": usable_lines,
        "distinct_reference_lines": sum(map(len, by_note.values())),
        "self_labeled_reference_lines": self_labeled,
        "bare_reference_lines": sum(map(len, bare.values())),
        "distinct_note_numbers": len(by_note),
        "visible_numbered_heading_notes": sorted(headings),
        "bare_notes_without_numbered_heading": sorted(set(bare) - headings),
        "exactly_two_distinct_reference_notes": sorted(
            note for note, lines in by_note.items() if len(lines) == 2
        ),
        "examples": [
            {"note": note, "line": line[:220]}
            for note, lines in sorted(by_note.items())
            for line in sorted(lines)[:1]
        ][:3],
    }


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("wrong P98 config schema")
    catalog_path = ROOT / config["source_catalog"]
    if sha(catalog_path) != config["source_catalog_sha256"]:
        raise ValueError("catalog pin mismatch")
    catalog = json.loads(catalog_path.read_text())
    rows = []
    for job in catalog["jobs"]:
        path = ROOT / job["source_manifest"]
        if sha(path) != job["source_manifest_sha256"]:
            raise ValueError(f"source manifest pin mismatch: {job['issuer']}")
        manifest = json.loads(path.read_text())
        if len(manifest["records"]) != 4:
            raise ValueError("expected four frozen annual filings")
        for record in manifest["records"]:
            source = record["text"]
            source_sha = hashlib.sha256(source.encode()).hexdigest()
            if record.get("text_sha256", source_sha) != source_sha:
                raise ValueError("record source text hash mismatch")
            rendered = render_document(
                {
                    "record_id": f"{job['issuer']}:{record['report_date']}",
                    "text": source,
                    "source_sha256": source_sha,
                }
            )
            evidence = scan_visible(rendered["text"])
            rows.append(
                {
                    "issuer": job["issuer"],
                    "report_year": record["report_date"][:4],
                    "source_manifest_sha256": job["source_manifest_sha256"],
                    "source_text_sha256": source_sha,
                    "visible_text_sha256": rendered["text_sha256"],
                    "status": "unsupported_for_shared_world_prose_dependency",
                    "reasons": [
                        "no_typed_prose_claim_or_note_target_relation_in_p64_world",
                        "self_labeled_reference_shortcut"
                        if evidence["self_labeled_reference_lines"]
                        else "no_self_labeled_reference",
                        "bare_note_target_unresolved"
                        if evidence["bare_notes_without_numbered_heading"]
                        else "no_unresolved_bare_note_target",
                    ],
                    **evidence,
                }
            )
    totals = Counter()
    for row in rows:
        for key in (
            "usable_reference_lines",
            "distinct_reference_lines",
            "self_labeled_reference_lines",
            "bare_reference_lines",
        ):
            totals[key] += row[key]
    return {
        "schema": SCHEMA,
        "config_sha256": sha(config_path),
        "catalog_sha256": sha(catalog_path),
        "issuer_worlds": len(catalog["jobs"]),
        "annual_filings": len(rows),
        "tasks_admitted": 0,
        "candidate_note_pairs_with_two_distinct_lines": sum(
            len(row["exactly_two_distinct_reference_notes"]) for row in rows
        ),
        "totals": dict(totals),
        "rows": rows,
        "train_ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    payload = build(args.config)
    serialized = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if args.verify_only:
        if args.output.read_bytes() != serialized:
            raise ValueError("P98 unsupported ledger replay mismatch")
    else:
        if args.output.exists():
            raise ValueError("P98 unsupported ledger already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(serialized)
    print(json.dumps({key: value for key, value in payload.items() if key != "rows"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
