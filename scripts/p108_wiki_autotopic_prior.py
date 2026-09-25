"""Freeze a title/URL-clean union of prior Wiki source pools."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import pinned_json, sha
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p108-wiki-autotopic-prior.v1"


def _bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def build(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or set(config) != {"schema", "pools"}:
        raise ValueError("P108 prior config differs")
    if not isinstance(config["pools"], list) or len(config["pools"]) < 2:
        raise ValueError("P108 prior needs multiple pinned pools")
    pools = [pinned_json(ROOT, pin) for pin in config["pools"]]
    if any(pool.get("schema") != "longworld.source-batch-pool.v2" for pool in pools):
        raise ValueError("P108 prior source schema differs")
    contract = {key: value for key, value in pools[0].items() if key != "sources"}
    if any(
        {key: value for key, value in pool.items() if key != "sources"} != contract
        for pool in pools
    ):
        raise ValueError("P108 prior pools use different source contracts")
    names, titles, urls = set(), set(), set()
    sources = []
    pages = Counter()
    for pool in pools:
        for source in pool["sources"]:
            if source["name"] in names:
                raise ValueError("P108 prior source name repeats")
            names.add(source["name"])
            snapshot = _snapshot(ROOT, source["snapshot"])
            for doc in snapshot["documents"]:
                title, url = doc["title"].casefold(), doc["page_url"]
                if title in titles or url in urls:
                    raise ValueError("P108 prior title/URL repeats across pools")
                titles.add(title)
                urls.add(url)
                pages[source["split"]] += 1
            sources.append(source)
    union = {**contract, "sources": sources}
    pool_bytes = _bytes(union)
    manifest = {
        "schema": SCHEMA + ".result",
        "inputs": config["pools"],
        "config_sha256": sha(config_path),
        "source_pool_sha256": hashlib.sha256(pool_bytes).hexdigest(),
        "source_groups": len(sources),
        "pages_by_split": dict(sorted(pages.items())),
        "unique_titles": len(titles),
        "unique_urls": len(urls),
        "train_ready": False,
    }
    outputs = {"source_pool.json": pool_bytes, "manifest.json": _bytes(manifest)}
    if verify_only:
        if not output_dir.is_dir() or {p.name for p in output_dir.iterdir()} != set(
            outputs
        ):
            raise ValueError("P108 prior output inventory differs")
        for name, expected in outputs.items():
            if (output_dir / name).read_bytes() != expected:
                raise ValueError(f"P108 prior replay differs: {name}")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, content in outputs.items():
            (output_dir / name).write_bytes(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.config, args.output_dir, verify_only=args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
