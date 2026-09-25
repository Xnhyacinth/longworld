"""Merge frozen structural Wiki intakes with global page-title split hygiene."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p92_generic_table_scan import (
    boundary_replay,
    intervals,
    parse_tables,
)
from longworld.synthesis.p93_wiki_structural_intake import verify as verify_intake
from longworld.synthesis.source_batch_plan import expand
from scripts.run_source_pool_batch import _snapshot

SCHEMA = "longworld.p94-wiki-structural-merge.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pinned(pin: dict[str, str]) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("input pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != pin["sha256"]:
        raise ValueError(f"input pin mismatch: {relative}")
    return path


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _prior_titles(router: dict[str, Any]) -> dict[str, set[str]]:
    prior: dict[str, set[str]] = {}
    for source in router["sources"]:
        if source["source_kind"] != "real_wiki":
            continue
        for pin in source["source_pins"]:
            snapshot = _snapshot(ROOT, pin)
            for doc in snapshot["documents"]:
                prior.setdefault(doc["title"].casefold(), set()).add(source["split"])
    return prior


def select(
    sources: list[dict[str, Any]], prior: dict[str, set[str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Accept only fully novel bundles; never change a frozen snapshot."""
    chosen: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    names: set[str] = set()
    for source in sources:
        if source["name"] in names:
            raise ValueError(f"duplicate source name: {source['name']}")
        names.add(source["name"])
        snapshot = _snapshot(ROOT, source["snapshot"])
        titles = [doc["title"].casefold() for doc in snapshot["documents"]]
        if len(titles) != len(set(titles)):
            raise ValueError("snapshot contains repeated document titles")
        overlaps = [
            {
                "title": doc["title"],
                "previous_splits": sorted(prior[doc["title"].casefold()]),
                "cross_split": source["split"] not in prior[doc["title"].casefold()],
            }
            for doc in snapshot["documents"]
            if doc["title"].casefold() in prior
        ]
        if overlaps:
            rejected.append(
                {
                    "source": source["name"],
                    "reason": "prior_document_title",
                    "overlaps": overlaps,
                }
            )
            continue
        chosen.append(source)
        for title in titles:
            prior[title] = {source["split"]}
    return chosen, rejected


def build(config_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or not config.get("intakes"):
        raise ValueError("invalid structural merge config")
    router = json.loads(_pinned(config["prior_router"]).read_text())
    if router.get("schema") != "longworld.p92-source-router.v1.result":
        raise ValueError("prior router schema mismatch")
    prior = _prior_titles(router)
    sources: list[dict[str, Any]] = []
    base: dict[str, Any] | None = None
    gross = Counter()
    for pin in config["intakes"]:
        path = _pinned(pin)
        receipt = verify_intake(path.parent, ROOT)
        pool = json.loads((path.parent / "source_pool.json").read_text())
        skeleton = {key: value for key, value in pool.items() if key != "sources"}
        if base is None:
            base = skeleton
        elif base != skeleton:
            raise ValueError("intakes use different source-pool contracts")
        if receipt["source_groups"] != len(pool["sources"]):
            raise ValueError("intake source count drift")
        sources.extend(pool["sources"])
        gross.update(
            groups=receipt["source_groups"],
            pages=receipt["frozen_pages"],
            facts=receipt["frozen_facts"],
            http_requests=receipt["http_requests"],
        )
    assert base is not None
    chosen, rejected = select(sources, prior)
    pool = {**base, "sources": chosen}
    support = Counter()
    generic_tables = []
    pages = facts = 0
    for source in chosen:
        snapshot = _snapshot(ROOT, source["snapshot"])
        pages += len(snapshot["documents"])
        facts += len(snapshot["facts"])
        try:
            jobs, _ = expand({**pool, "sources": [source]}, ROOT, _snapshot)
        except ValueError:
            jobs = []
        support.update({recipe: 1 for recipe in {job["recipe"] for job in jobs}})
        for doc in snapshot["documents"]:
            for table in parse_tables(doc["text"])[0]:
                passing = 0
                for low, high in intervals(table, 8):
                    try:
                        boundary_replay(doc["text"], table, low, high)
                    except ValueError:
                        continue
                    passing += 1
                generic_tables.append(
                    {
                        "source": source["name"],
                        "title": doc["title"],
                        "heading": table.heading,
                        "year_column": table.year_column,
                        "rows": len(table.rows),
                        "intervention_ready_intervals": passing,
                    }
                )
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "prior_router": config["prior_router"],
        "intakes": config["intakes"],
        "gross": dict(sorted(gross.items())),
        "accepted_groups": len(chosen),
        "accepted_pages": pages,
        "accepted_facts": facts,
        "rejected_groups": rejected,
        "native_supported_groups_by_recipe": dict(sorted(support.items())),
        "generic_closed_year_tables": generic_tables,
        "train_ready": False,
    }
    return pool, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    output = (
        args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    )
    if ".." in output.parts or not output.is_relative_to(ROOT):
        raise ValueError("output must be workspace-relative")
    pool, result = build(args.config)
    if args.verify_only:
        if json.loads((output / "source_pool.json").read_text()) != pool:
            raise ValueError("source pool replay drift")
        if json.loads((output / "manifest.json").read_text()) != result:
            raise ValueError("manifest replay drift")
    else:
        output.mkdir(parents=True, exist_ok=False)
        _write(output / "source_pool.json", pool)
        _write(output / "manifest.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
