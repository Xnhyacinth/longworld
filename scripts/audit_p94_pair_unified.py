"""Bind unified P94 pair readers to native oracle and mask-checked bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.p94-pair-unified-native-match.v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reader_hash(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def audit(native_dir: Path, unified_dir: Path) -> dict:
    verified = verify_merge(unified_dir)
    native_mask_path = native_dir / "mask_audit.json"
    native = json.loads(native_mask_path.read_text())
    if native.get(
        "schema_version"
    ) != "longworld.p94-wiki-native-mask-audit.v1" or native.get(
        "source_manifest_sha256"
    ) != sha(native_dir / "manifest.json"):
        raise ValueError("native mask audit is not bound to native manifest")
    expected = native["reader_sha256"]
    readers = {}
    for split in ("train", "eval"):
        with (unified_dir / f"candidate_{split}.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                sample_id = row["sample_id"]
                if sample_id in readers:
                    raise ValueError("unified sample ID repeats")
                readers[sample_id] = reader_hash(row)
    if readers != expected or len(readers) != verified["candidate_views"]:
        raise ValueError("unified reader bytes differ from native mask audit")
    result = {
        "schema_version": SCHEMA,
        "native_mask_audit_sha256": sha(native_mask_path),
        "native_manifest_sha256": native["source_manifest_sha256"],
        "unified_manifest_sha256": sha(unified_dir / "manifest.json"),
        "matched_readers": len(readers),
        "sample_ids": sorted(readers),
        "train_ready": False,
    }
    output = unified_dir / "native_reader_match.json"
    if output.exists():
        if json.loads(output.read_text()) != result:
            raise ValueError("existing unified match receipt differs")
    else:
        output.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--unified-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.native_dir, args.unified_dir), sort_keys=True))


if __name__ == "__main__":
    main()
