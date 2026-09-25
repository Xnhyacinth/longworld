"""Audit every P105 unified reader with the exact assistant loss mask."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.run_p92_generic_table_scan import _dump, _sha

SCHEMA = "longworld.p105-unified-all-reader-mask.v1"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def audit(merged_dir: Path) -> tuple[dict, list[dict]]:
    merged = verify_merge(merged_dir)
    readers = {
        split: _rows(merged_dir / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    indices = _rows(merged_dir / "sample_index.jsonl")
    if len(indices) != merged["candidate_views"]:
        raise ValueError("unified reader/index count differs")
    tokenizer = get_tokenizer()
    results = []
    seen = set()
    for index in indices:
        split = index["split"]
        position = index["row_index"]
        if (
            split not in readers
            or not isinstance(position, int)
            or not 0 <= position < len(readers[split])
            or (split, position) in seen
            or index["source_kind"] != "real_wiki"
            or index["evidence_profile"]
            != "raw_wikitext_cell_to_final_reader_complete_set_replayed"
        ):
            raise ValueError("P105 unified index route differs")
        seen.add((split, position))
        reader = readers[split][position]
        results.append(audit_reader(reader, index, tokenizer, 131072))
    if len(seen) != sum(map(len, readers.values())):
        raise ValueError("unified reader inventory incomplete")
    manifest = {
        "schema": SCHEMA,
        "merged_manifest_sha256": _sha(merged_dir / "manifest.json"),
        "audited_readers": len(results),
        "splits": dict(sorted(Counter(row["split"] for row in results).items())),
        "source_kinds": dict(
            sorted(Counter(row["source_kind"] for row in results).items())
        ),
        "operations": dict(
            sorted(Counter(row["operation"] for row in results).items())
        ),
        "train_ready": False,
    }
    return manifest, results


def run(merged_dir: Path, output_dir: Path, *, verify_only: bool) -> dict:
    manifest, rows = audit(merged_dir)
    if not verify_only and output_dir.exists():
        raise ValueError("P105 unified mask output already exists")
    payload = "".join(_dump(row) + "\n" for row in rows)
    if verify_only:
        if (output_dir / "mask_rows.jsonl").read_text() != payload:
            raise ValueError("P105 all-reader mask rows replay drift")
    else:
        output_dir.mkdir(parents=True)
        (output_dir / "mask_rows.jsonl").write_text(payload)
    manifest["mask_rows_sha256"] = _sha(output_dir / "mask_rows.jsonl")
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if verify_only:
        if (output_dir / "manifest.json").read_text() != content:
            raise ValueError("P105 all-reader mask manifest replay drift")
    else:
        (output_dir / "manifest.json").write_text(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(run(args.merged_dir, args.output_dir, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
