"""Freeze the inventory of already acquired P93 Wiki discovery responses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import pinned_json, sha, verify

SCHEMA = "longworld.p108-wiki-autotopic-inventory.v1"


def _bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def freeze(search_root: Path, output: Path, *, verify_only: bool = False) -> dict:
    if verify_only:
        stored = json.loads(output.read_text())
        pins = stored["intakes"]
    else:
        if output.exists():
            raise ValueError("P108 inventory output must be new")
        pins = []
        for path in sorted(search_root.rglob("manifest.json")):
            value = json.loads(path.read_text())
            if value.get("schema") == "longworld.p93-wiki-structural-intake.v1.result":
                pins.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path)})
    if not pins or len({pin["path"] for pin in pins}) != len(pins):
        raise ValueError("P108 needs distinct frozen P93 intakes")
    discoveries = groups = 0
    for pin in pins:
        relative = Path(pin["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("P108 seed inventory path escapes workspace")
        path = ROOT / pin["path"]
        pinned_json(ROOT, pin)
        receipt = verify(path.parent, ROOT)
        discoveries += len(receipt["discovery"])
        groups += receipt["source_groups"]
    result = {
        "schema": SCHEMA,
        "intakes": pins,
        "frozen_intakes": len(pins),
        "discovery_queries": discoveries,
        "frozen_source_groups": groups,
        "train_ready": False,
    }
    content = _bytes(result)
    if verify_only:
        if output.read_bytes() != content or stored != result:
            raise ValueError("P108 seed inventory replay differs")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--search-root", type=Path, default=ROOT / "data/capability_records"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            freeze(args.search_root, args.output, verify_only=args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
