"""Freeze several searched Wiki title groups and append them to one task pool.

The titles are explicit, not inferred category members. Failed source groups
remain visible in the acquisition manifest and never enter task synthesis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.wiki_adapter import HttpError, SnapshotError, WikiHttpFetcher
from scripts.freeze_wiki_title_bundle import freeze_titles

SCHEMA = "longworld.p76-wiki-expansion.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def run(catalog_path: Path, output_dir: Path, *, workers: int = 2) -> dict[str, Any]:
    if not 1 <= workers <= 4 or output_dir.exists():
        raise ValueError("workers must be 1..4 and output directory must be new")
    catalog = json.loads(catalog_path.read_text())
    if catalog.get("schema") != SCHEMA:
        raise ValueError("wrong expansion catalog schema")
    base_path = ROOT / catalog["base_pool"]
    base = json.loads(base_path.read_text())
    if base.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong base pool schema")
    groups = catalog.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("expansion needs groups")
    names: set[str] = set()
    titles: set[str] = set()
    for group in groups:
        name = group.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", name):
            raise ValueError("invalid expansion name")
        if name in names or name in {row["name"] for row in base["sources"]}:
            raise ValueError("duplicate expansion source name")
        names.add(name)
        if group.get("split") not in {"train", "eval"} or not all(
            isinstance(group.get(key), str) and group[key]
            for key in ("domain", "topic")
        ):
            raise ValueError("expansion domain/topic/split invalid")
        pages = group.get("titles")
        if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
            raise ValueError("expansion title count invalid")
        if any(not isinstance(title, str) or title in titles for title in pages):
            raise ValueError("invalid or repeated title across expansion groups")
        titles.update(pages)
    output_dir.mkdir(parents=True)
    (output_dir / "snapshots").mkdir()

    def freeze(group: dict[str, Any]) -> dict[str, Any]:
        path = output_dir / "snapshots" / f"{group['name']}_snapshot.json"
        receipt = freeze_titles(
            WikiHttpFetcher("https://en.wikipedia.org/w/api.php"),
            group["titles"],
            group["name"],
            path,
        )
        return {
            "name": group["name"],
            "domain": group["domain"],
            "topic": group["topic"],
            "split": group["split"],
            "snapshot": {"path": str(path), "sha256": _sha(path)},
            "pages": receipt["pages"],
            "facts": receipt["facts"],
            "snapshot_id": receipt["snapshot_id"],
        }

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(freeze, group): group["name"] for group in groups}
        for future in as_completed(futures):
            name = futures[future]
            try:
                successes.append(future.result())
            except (OSError, ValueError, KeyError, HttpError, SnapshotError) as error:
                failures.append(
                    {"name": name, "reason": f"{type(error).__name__}:{error}"}
                )
    successes.sort(key=lambda item: item["name"])
    failures.sort(key=lambda item: item["name"])
    pool_config = {
        **base,
        "sources": [
            *base["sources"],
            *(
                {
                    key: value
                    for key, value in item.items()
                    if key in {"name", "domain", "topic", "split", "snapshot"}
                }
                for item in successes
            ),
        ],
    }
    (output_dir / "source_pool.json").write_text(_dump(pool_config))
    result = {
        "schema": SCHEMA + ".result",
        "catalog_sha256": _sha(catalog_path),
        "base_pool_sha256": _sha(base_path),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "attempted_groups": len(groups),
        "frozen_groups": len(successes),
        "failed_groups": failures,
        "frozen": successes,
        "train_ready": False,
    }
    (output_dir / "acquisition_manifest.json").write_text(_dump(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    print(_dump(run(args.catalog, args.output_dir, workers=args.workers)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
