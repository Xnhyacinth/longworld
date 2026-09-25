"""Pin the genuinely new source groups added by the P94 train intake."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p94-wiki-structural-delta.v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned(pin: dict) -> dict:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("pool pin must be workspace-relative")
    path = ROOT / relative
    if sha(path) != pin["sha256"]:
        raise ValueError("pool pin drift")
    pool = json.loads(path.read_text())
    if pool.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("source pool schema drift")
    return pool


def build(config_path: Path) -> tuple[dict, dict]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA:
        raise ValueError("delta config schema drift")
    old, new = pinned(config["old_pool"]), pinned(config["new_pool"])
    if {k: v for k, v in old.items() if k != "sources"} != {
        k: v for k, v in new.items() if k != "sources"
    }:
        raise ValueError("source-pool contract changed")
    previous = {source["name"]: source for source in old["sources"]}
    latest = {source["name"]: source for source in new["sources"]}
    if len(previous) != len(old["sources"]) or len(latest) != len(new["sources"]):
        raise ValueError("source group names repeat")
    if any(latest.get(name) != source for name, source in previous.items()):
        raise ValueError("old source group changed or disappeared")
    delta = [source for source in new["sources"] if source["name"] not in previous]
    if not delta:
        raise ValueError("no new source groups")
    old_titles = {
        doc["title"].casefold()
        for source in old["sources"]
        for doc in _snapshot(ROOT, source["snapshot"])["documents"]
    }
    new_titles: set[str] = set()
    pages = facts = 0
    for source in delta:
        snapshot = _snapshot(ROOT, source["snapshot"])
        pages += len(snapshot["documents"])
        facts += len(snapshot["facts"])
        for doc in snapshot["documents"]:
            title = doc["title"].casefold()
            if title in old_titles or title in new_titles:
                raise ValueError("new source duplicates a frozen page title")
            new_titles.add(title)
    return (
        {**new, "sources": delta},
        {
            "schema": SCHEMA + ".result",
            "config_sha256": sha(config_path),
            "old_pool": config["old_pool"],
            "new_pool": config["new_pool"],
            "new_source_groups": len(delta),
            "new_pages": pages,
            "new_facts": facts,
            "new_group_names": [source["name"] for source in delta],
            "train_ready": False,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    pool, result = build(args.config)
    output = args.output_dir
    if args.verify_only:
        if (
            json.loads((output / "source_pool.json").read_text()) != pool
            or json.loads((output / "manifest.json").read_text()) != result
        ):
            raise ValueError("structural delta replay drift")
    else:
        output.mkdir(parents=True, exist_ok=False)
        for name, value in (("source_pool.json", pool), ("manifest.json", result)):
            (output / name).write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
