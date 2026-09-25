"""Measure P106 physical lengths, source worlds and table evidence spans."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p105_length_evidence_report import report as reader_report

SCHEMA = "longworld.p106-length-evidence-report.v1"


def report(native_dir: Path) -> dict:
    result = reader_report(native_dir)
    rows = [
        json.loads(line)
        for line in (native_dir / "sample_index.jsonl").read_text().splitlines()
    ]
    by_id = {row["sample_id"]: row for row in rows}
    if len(by_id) != len(result["rows"]):
        raise ValueError("P106 length report task identity differs")
    for row in result["rows"]:
        index = by_id[row["sample_id"]]
        row["split"] = index["split"]
        row["topic"] = index["topic"]
    result["schema"] = SCHEMA
    result["summary"]["splits"] = dict(
        sorted(Counter(row["split"] for row in result["rows"]).items())
    )
    result["summary"]["domains"] = dict(
        sorted(Counter(row["domain"] for row in result["rows"]).items())
    )
    result["summary"]["topics"] = dict(
        sorted(Counter(row["topic"] for row in result["rows"]).items())
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = report(args.native_dir)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.verify_only:
        if not args.output.is_file() or args.output.read_text() != content:
            raise ValueError("P106 length/evidence report replay differs")
    else:
        if args.output.exists():
            raise ValueError("P106 length/evidence output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
