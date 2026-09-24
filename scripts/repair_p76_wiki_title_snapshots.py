"""Version a metadata correction for frozen explicit-title Wiki snapshots.

The original source text, page revisions and extracted facts remain byte-for-byte
the same. A new snapshot identity is computed after correcting the source kind;
the parent snapshot and its digest stay recorded for audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from longworld.synthesis.wiki_adapter import (
    TITLE_BUNDLE_KIND,
    _snapshot_id,
    canonical_json,
    snapshot_from_dict,
)


def repair(path: Path, output: Path) -> dict:
    raw = path.read_bytes()
    snapshot = json.loads(raw)
    source = snapshot["source"]
    if source.get("collection_kind") != "title_bundle":
        raise ValueError(f"not an explicit-title bundle: {path}")
    if source.get("kind") != "mediawiki_category":
        raise ValueError(f"source kind is not the legacy mislabel: {path}")
    parent_id = snapshot["snapshot_id"]
    parent_sha = hashlib.sha256(raw).hexdigest()
    source["kind"] = TITLE_BUNDLE_KIND
    source["metadata_repair"] = {
        "parent_snapshot_id": parent_id,
        "parent_sha256": parent_sha,
        "repair": "correct explicit-title source kind without changing documents or facts",
    }
    snapshot["snapshot_id"] = _snapshot_id(
        source["collection_label"],
        source,
        snapshot["documents"],
        snapshot["facts"],
    )
    snapshot_from_dict(snapshot)
    data = canonical_json(snapshot)
    output.write_bytes(data)
    return {
        "source": str(path),
        "source_sha256": parent_sha,
        "output": str(output),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "parent_snapshot_id": parent_id,
        "snapshot_id": snapshot["snapshot_id"],
        "documents": len(snapshot["documents"]),
        "facts": len(snapshot["facts"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--universities-v3", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output directory must not already exist")
    args.output_dir.mkdir(parents=True)
    paths = sorted(args.source_dir.glob("*_snapshot.json"))
    if not paths:
        parser.error("no source snapshots found")
    receipts = []
    for path in paths:
        source = args.universities_v3 if path.stem == "universities_snapshot" else path
        receipts.append(repair(source, args.output_dir / path.name))
    (args.output_dir / "repair_manifest.json").write_text(
        json.dumps(receipts, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
