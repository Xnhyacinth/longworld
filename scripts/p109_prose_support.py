"""Audit frozen RFC prose for executable numeric lower-bound rules."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = "longworld.p109-prose-rule-support.v1"
MINIMUM = re.compile(
    r"(?P<subject>(?:[A-Za-z][A-Za-z0-9_-]*[ \t]+){0,6}[A-Za-z][A-Za-z0-9_-]*)"
    r"[ \t\r\n]+MUST[ \t\r\n]+be[ \t\r\n]+at[ \t\r\n]+least"
    r"[ \t\r\n]+(?P<limit>[0-9]{1,5})(?![0-9^])",
    re.IGNORECASE,
)
RFC_FILE = re.compile(r"rfc([0-9]+)\.txt")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin(pin: dict) -> Path:
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("P109 source pin requires path and SHA-256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P109 source pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"P109 source pin drift: {relative}")
    return path


def _catalog(inventories: list[dict]) -> dict[str, dict]:
    sources = {}
    for inventory_pin in inventories:
        path = _pin(inventory_pin)
        inventory = json.loads(path.read_text())
        for record in inventory["fetch_receipt"]["retrievals"]:
            match = RFC_FILE.fullmatch(record["retrieval_file"])
            if not match or record["kind"] != "rfc":
                continue
            rfc_id = "rfc" + match.group(1)
            expected_url = f"https://www.rfc-editor.org/rfc/{rfc_id}.txt"
            source_path = path.parent / record["retrieval_file"]
            if (
                record["status"] != 200
                or record["final_url"] != expected_url
                or _sha(source_path) != record["sha256"]
            ):
                raise ValueError(f"P109 official RFC source differs: {rfc_id}")
            source = {
                "rfc_id": rfc_id,
                "source_path": str(source_path.relative_to(ROOT)),
                "source_sha256": record["sha256"],
                "source_url": expected_url,
                "inventory_path": str(path.relative_to(ROOT)),
                "inventory_sha256": inventory_pin["sha256"],
            }
            prior = sources.get(rfc_id)
            if prior is not None and prior["source_sha256"] != source["source_sha256"]:
                raise ValueError(
                    f"P109 same RFC has conflicting frozen bytes: {rfc_id}"
                )
            sources.setdefault(rfc_id, source)
    return dict(sorted(sources.items()))


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    return [
        (match.start(), match.end(), match.group())
        for match in re.finditer(r"[^\n]*(?:\n(?!\s*\n)[^\n]*)*", text)
        if match.group().strip()
    ]


def _field(subject: str) -> str | None:
    normalized = " ".join(subject.split())
    parameter = re.search(r"([A-Za-z][A-Za-z0-9_]*) parameter$", normalized)
    if parameter:
        return parameter.group(1)
    if re.fullmatch(r"[a-z][a-z0-9]*_[a-z0-9_]+", normalized):
        return normalized
    return None


def _scan_source(source: dict) -> list[dict]:
    text = (ROOT / source["source_path"]).read_text()
    paragraphs = _paragraphs(text)
    rows = []
    for match in MINIMUM.finditer(text):
        subject = " ".join(match.group("subject").split())
        limit = int(match.group("limit"))
        containing = [
            p for p in paragraphs if p[0] <= match.start() < match.end() <= p[1]
        ]
        field = _field(subject)
        if len(containing) != 1:
            status = "ambiguous_paragraph_boundary"
        elif field is None:
            status = "field_or_scope_not_typed"
        elif text.count(match.group()) != 1:
            status = "duplicate_exact_rule_clause"
        elif limit < 2 or limit > 4096:
            status = "limit_outside_supported_integer_range"
        else:
            status = "supported_numeric_minimum"
        support = []
        if status == "supported_numeric_minimum":
            stem = field.split("_")[0]
            for left, right, paragraph in paragraphs:
                normalized = " ".join(paragraph.split()).casefold()
                if (
                    stem.casefold() in normalized
                    and f"at least {limit}" in normalized
                    and "must" in normalized
                ):
                    support.append([left, right])
            if not support or not any(
                left <= match.start() < match.end() <= right for left, right in support
            ):
                status = "equivalent_support_not_bounded"
        rows.append(
            {
                **source,
                "status": status,
                "subject": subject,
                "field": field,
                "limit": limit,
                "rule_span": [match.start(), match.end()],
                "rule_text": match.group(),
                "support_paragraph_spans": support,
                "visible_excerpt": " ".join(containing[0][2].split())[:360]
                if containing
                else "",
            }
        )
    return rows


def build(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("P109 support config schema differs")
    prior = json.loads(_pin(config["annual_report_ledger"]).read_text())
    if prior["annual_filings"] != 32 or prior["tasks_admitted"] != 0:
        raise ValueError("P109 prior annual-report rejection ledger differs")
    sources = _catalog(config["rfc_inventories"])
    rows = [row for source in sources.values() for row in _scan_source(source)]
    return {
        "schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "annual_report_ledger": config["annual_report_ledger"],
        "annual_filings": prior["annual_filings"],
        "annual_tasks_admitted": prior["tasks_admitted"],
        "rfc_documents": len(sources),
        "gross_minimum_clauses": len(rows),
        "supported_rule_clauses": sum(
            row["status"] == "supported_numeric_minimum" for row in rows
        ),
        "supported_rfc_documents": len(
            {
                row["rfc_id"]
                for row in rows
                if row["status"] == "supported_numeric_minimum"
            }
        ),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "sources": list(sources.values()),
        "rows": rows,
        "reader_tasks_admitted": 0,
        "train_ready": False,
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
            raise ValueError("P109 support ledger replay differs")
    else:
        if args.output.exists():
            raise ValueError("P109 support ledger already exists")
        args.output.parent.mkdir(parents=True)
        args.output.write_text(content)
    print(
        json.dumps(
            {
                "rfc_documents": result["rfc_documents"],
                "gross_minimum_clauses": result["gross_minimum_clauses"],
                "supported_rule_clauses": result["supported_rule_clauses"],
                "supported_rfc_documents": result["supported_rfc_documents"],
                "status_counts": result["status_counts"],
            }
        )
    )


if __name__ == "__main__":
    main()
