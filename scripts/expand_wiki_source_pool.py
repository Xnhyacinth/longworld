"""Discover Wiki pages from topic seeds, freeze revisions, and probe task support.

This is source intake, not task admission. A seed changes the source/topic
without adding a per-topic parser. Frozen but unsupported groups stay in the
pool and in the report; the native compiler decides whether tasks exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.source_batch_plan import expand
from longworld.synthesis.wiki_adapter import HttpError, SnapshotError, WikiHttpFetcher
from scripts.freeze_wiki_title_bundle import freeze_titles
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.wiki-source-expansion.v1"
API = "https://en.wikipedia.org/w/api.php"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _validate(catalog: dict[str, Any], base: dict[str, Any]) -> None:
    if (
        catalog.get("schema") != SCHEMA
        or base.get("schema") != "longworld.source-batch-pool.v2"
    ):
        raise ValueError("wrong expansion or source pool schema")
    seeds = catalog.get("seeds")
    if not isinstance(seeds, list) or not seeds or len(seeds) > 100:
        raise ValueError("seeds must contain 1..100 entries")
    names = {source["name"] for source in base["sources"]}
    for seed in seeds:
        if not isinstance(seed, dict):
            raise TypeError("seed must be an object")
        name = seed.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,34}", name):
            raise ValueError("invalid seed name")
        if name in names or any(existing.startswith(name + "_") for existing in names):
            raise ValueError("duplicate seed name")
        names.add(name)
        if seed.get("split") not in {"train", "eval"} or not all(
            isinstance(seed.get(key), str) and seed[key].strip()
            for key in ("domain", "topic", "query")
        ):
            raise ValueError("seed needs domain, topic, split and query")
        if seed.get("mode") not in {"search", "category"}:
            raise ValueError("seed mode must be search or category")
        if seed["mode"] == "category" and not seed["query"].startswith("Category:"):
            raise ValueError("category query must start with Category:")
        for key, maximum in (
            ("max_pages", 30),
            ("bundle_size", 10),
            ("max_bundles", 10),
        ):
            value = seed.get(key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be within 1..{maximum}")
        if seed["bundle_size"] > seed["max_pages"]:
            raise ValueError("bundle_size exceeds max_pages")
        prefix = seed.get("title_prefix", "List of ")
        if not isinstance(prefix, str):
            raise TypeError("title_prefix must be a string")
        if not isinstance(seed.get("title_contains", ""), str):
            raise TypeError("title_contains must be a string")


def discover(
    fetcher: WikiHttpFetcher, seed: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Return an ordered, bounded title set and its original API response."""
    if seed["mode"] == "search":
        payload = fetcher.get_json(
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "search",
                "srsearch": seed["query"],
                "srnamespace": 0,
                "srlimit": seed["max_pages"],
            }
        )
        rows = payload.get("query", {}).get("search")
        if not isinstance(rows, list):
            raise SnapshotError("search response lacks results")
        titles = [row.get("title") for row in rows if isinstance(row, dict)]
    else:
        # Use the same API response as the title selection record, including
        # page IDs. Categories with more than max_pages are intentionally cut.
        payload = fetcher.get_json(
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "categorymembers",
                "cmtitle": seed["query"],
                "cmtype": "page",
                "cmlimit": seed["max_pages"],
            }
        )
        rows = payload.get("query", {}).get("categorymembers")
        if not isinstance(rows, list):
            raise SnapshotError("category response lacks members")
        titles = [row.get("title") for row in rows if isinstance(row, dict)]
    prefix = seed.get("title_prefix", "List of ")
    contains = seed.get("title_contains", "").casefold()
    clean = []
    seen = set()
    for title in titles:
        if (
            isinstance(title, str)
            and title.startswith(prefix)
            and contains in title.casefold()
            and "|" not in title
            and title.casefold() not in seen
        ):
            clean.append(title)
            seen.add(title.casefold())
    return clean, payload


def _existing_titles(base: dict[str, Any]) -> set[str]:
    titles: set[str] = set()
    for source in base["sources"]:
        snapshot = _snapshot(ROOT, source["snapshot"])
        titles.update(doc["title"].casefold() for doc in snapshot["documents"])
    return titles


def run(
    catalog_path: Path, output_dir: Path, *, fetcher: WikiHttpFetcher | None = None
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("output directory must be new")
    catalog = json.loads(catalog_path.read_text())
    base_path = Path(catalog["base_pool"])
    if not base_path.is_absolute():
        base_path = ROOT / base_path
    base = json.loads(base_path.read_text())
    _validate(catalog, base)
    existing = _existing_titles(base)
    fetcher = fetcher or WikiHttpFetcher(API)
    output_dir.mkdir(parents=True)
    (output_dir / "snapshots").mkdir()
    (output_dir / "discovery").mkdir()
    frozen: list[dict[str, Any]] = []
    discovery: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for seed in catalog["seeds"]:
        try:
            titles, payload = discover(fetcher, seed)
            discovery_path = output_dir / "discovery" / f"{seed['name']}.json"
            _write(discovery_path, payload)
            novel = [title for title in titles if title.casefold() not in existing]
            discovery.append(
                {
                    "seed": seed["name"],
                    "mode": seed["mode"],
                    "query": seed["query"],
                    "raw_response": {
                        "path": str(discovery_path),
                        "sha256": _sha(discovery_path),
                    },
                    "matched_titles": len(titles),
                    "novel_titles": len(novel),
                    "excluded_existing_titles": len(titles) - len(novel),
                }
            )
            for number, start in enumerate(
                range(0, len(novel), seed["bundle_size"]), 1
            ):
                if number > seed["max_bundles"]:
                    break
                bundle = novel[start : start + seed["bundle_size"]]
                if not bundle:
                    continue
                name = f"{seed['name']}_{number:02d}"
                path = output_dir / "snapshots" / f"{name}_snapshot.json"
                try:
                    receipt = freeze_titles(fetcher, bundle, name, path)
                except (HttpError, SnapshotError, OSError, ValueError) as error:
                    failures.append(
                        {"name": name, "reason": f"{type(error).__name__}:{error}"}
                    )
                    continue
                existing.update(title.casefold() for title in bundle)
                frozen.append(
                    {
                        "name": name,
                        "domain": seed["domain"],
                        "topic": seed["topic"],
                        "split": seed["split"],
                        "titles": bundle,
                        "snapshot": {"path": str(path), "sha256": _sha(path)},
                        "snapshot_id": receipt["snapshot_id"],
                        "pages": receipt["pages"],
                        "facts": receipt["facts"],
                        "revisions": receipt["revisions"],
                        "license": receipt["license"],
                    }
                )
        except (HttpError, SnapshotError, OSError, ValueError, KeyError) as error:
            failures.append(
                {"name": seed["name"], "reason": f"{type(error).__name__}:{error}"}
            )
    new_sources = [
        {key: item[key] for key in ("name", "domain", "topic", "split", "snapshot")}
        for item in frozen
    ]
    pool = {
        **base,
        "sources": [*base["sources"], *new_sources],
    }
    _write(output_dir / "source_pool.json", pool)
    if new_sources:
        _write(output_dir / "new_source_pool.json", {**base, "sources": new_sources})
    supported = []
    unsupported = []
    for item in frozen:
        probe_pool = {
            **base,
            "sources": [
                {
                    key: item[key]
                    for key in ("name", "domain", "topic", "split", "snapshot")
                }
            ],
        }
        try:
            jobs, skipped = expand(probe_pool, ROOT, _snapshot)
        except ValueError as error:
            jobs, skipped = [], [{"source": item["name"], "reason": str(error)}]
        supported.append(
            {"source": item["name"], "jobs": [job["recipe"] for job in jobs]}
        )
        unsupported.extend(skipped)
    result = {
        "schema": SCHEMA + ".result",
        "catalog_sha256": _sha(catalog_path),
        "base_pool_sha256": _sha(base_path),
        "source_pool_sha256": _sha(output_dir / "source_pool.json"),
        "new_source_pool_sha256": _sha(output_dir / "new_source_pool.json")
        if new_sources
        else None,
        "discovery": discovery,
        "frozen": frozen,
        "failed": failures,
        "native_probe": {"supported": supported, "unsupported": unsupported},
        "attempted_seeds": len(catalog["seeds"]),
        "frozen_groups": len(frozen),
        "productive_groups_at_probe": sum(bool(row["jobs"]) for row in supported),
        "train_ready": False,
    }
    _write(output_dir / "acquisition_manifest.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.catalog, args.output_dir), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
