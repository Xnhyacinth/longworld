"""Bounded Wiki title discovery for pinned, structure-probed source pools.

Vocabulary expands search coverage only. A frozen page enters the source pool
even when no native task is supported; probes never assert reader admission.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from longworld.synthesis.source_batch_plan import expand
from longworld.synthesis.wiki_adapter import HttpError, SnapshotError, WikiHttpFetcher

SCHEMA = "longworld.p93-wiki-structural-intake.v1"
POOL_SCHEMA = "longworld.source-batch-pool.v2"
NAME = re.compile(r"[a-z][a-z0-9_]{2,40}\Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def within_storage(root: Path, path: Path) -> bool:
    resolved = path.resolve()
    return resolved.is_relative_to(root) or resolved.is_relative_to(
        (root / "data").resolve()
    )


def pinned_json(root: Path, pin: dict[str, str]) -> dict[str, Any]:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin must be workspace-relative")
    path = root / relative
    if not within_storage(root, path):
        raise ValueError(f"source pin escapes workspace data storage: {relative}")
    if not path.is_file() or sha(path) != pin["sha256"]:
        raise ValueError(f"source pin mismatch: {relative}")
    return json.loads(path.read_text())


def validate(config: dict[str, Any]) -> None:
    if config.get("schema") != SCHEMA:
        raise ValueError("wrong intake schema")
    families = config.get("families")
    if not isinstance(families, list) or not 1 <= len(families) <= 24:
        raise ValueError("families must contain 1..24 entries")
    if not isinstance(config.get("base_pool"), dict):
        raise TypeError("base_pool pin required")
    eval_families = config.get("eval_families")
    if (
        not isinstance(eval_families, list)
        or not eval_families
        or any(not isinstance(name, str) for name in eval_families)
    ):
        raise ValueError("eval_families must name source clusters")
    names: set[str] = set()
    total_queries = 0
    for family in families:
        if not isinstance(family, dict) or not all(
            isinstance(family.get(key), str) and family[key]
            for key in ("name", "domain", "query_template")
        ):
            raise ValueError("family needs name, domain and query_template")
        if (
            not NAME.fullmatch(family["name"])
            or len(family["name"]) > 24
            or family["name"] in names
        ):
            raise ValueError("family name invalid or repeated")
        names.add(family["name"])
        if family.get("mode") not in {"search", "category"}:
            raise ValueError("mode must be search or category")
        if family["query_template"].count("{term}") != 1:
            raise ValueError("query_template needs one {term}")
        terms = family.get("terms")
        if (
            not isinstance(terms, list)
            or not 1 <= len(terms) <= 20
            or any(not isinstance(term, str) or not term.strip() for term in terms)
            or len({term.casefold() for term in terms}) != len(terms)
        ):
            raise ValueError("terms must be 1..20 unique nonempty strings")
        total_queries += len(terms)
        for key, maximum in (
            ("max_results_per_term", 50),
            ("bundle_size", 10),
            ("max_bundles_per_term", 5),
        ):
            value = family.get(key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be 1..{maximum}")
        if family["bundle_size"] > family["max_results_per_term"]:
            raise ValueError("bundle_size exceeds max_results_per_term")
    if total_queries > 40:
        raise ValueError("intake exceeds 40 discovery queries")
    if len(eval_families) != len(set(eval_families)) or not set(eval_families) <= names:
        raise ValueError("eval_families must be unique known families")


def planned_split(config: dict[str, Any], family_name: str) -> str:
    return "eval" if family_name in config["eval_families"] else "train"


def discover(
    fetcher: WikiHttpFetcher, family: dict[str, Any], term: str
) -> tuple[list[str], list[dict[str, Any]]]:
    """Page through a bounded API result and return matching titles + raw pages."""
    maximum = family["max_results_per_term"]
    query = family["query_template"].replace("{term}", term)
    if family["mode"] == "category" and not query.startswith("Category:"):
        raise ValueError("category template must yield Category: title")
    titles: list[str] = []
    seen: set[str] = set()
    raw: list[dict[str, Any]] = []
    continuation: Any = None
    while len(titles) < maximum:
        limit = min(50, maximum - len(titles))
        if family["mode"] == "search":
            params: dict[str, Any] = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "search",
                "srsearch": query,
                "srnamespace": 0,
                "srlimit": limit,
            }
            if continuation is not None:
                params["sroffset"] = continuation
            key, next_key = "search", "sroffset"
        else:
            params = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "categorymembers",
                "cmtitle": query,
                "cmtype": "page",
                "cmlimit": limit,
            }
            if continuation is not None:
                params["cmcontinue"] = continuation
            key, next_key = "categorymembers", "cmcontinue"
        payload = fetcher.get_json(params)
        rows = payload.get("query", {}).get(key)
        if not isinstance(rows, list):
            raise SnapshotError(f"discovery response lacks {key}")
        raw.append({"request": params, "response": payload})
        for row in rows:
            title = row.get("title") if isinstance(row, dict) else None
            if not isinstance(title, str) or not title.startswith("List of "):
                continue
            if term.casefold() not in title.casefold() or "|" in title:
                continue
            folded = title.casefold()
            if folded not in seen:
                seen.add(folded)
                titles.append(title)
                if len(titles) == maximum:
                    break
        next_continuation = payload.get("continue", {}).get(next_key)
        if next_continuation is None or next_continuation == continuation or not rows:
            break
        continuation = next_continuation
        # Avoid unbounded scanning through off-topic pages.
        if len(raw) >= 4:
            break
    return titles, raw


def prior_titles(root: Path, pool: dict[str, Any]) -> set[str]:
    titles: set[str] = set()
    for source in pool["sources"]:
        snapshot = pinned_json(root, source["snapshot"])
        titles.update(doc["title"].casefold() for doc in snapshot["documents"])
    return titles


def probe(root: Path, source: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    """Run native structure builders against frozen text, before any reader."""
    from scripts.run_source_pool_batch import _snapshot

    one = {**pool, "sources": [source]}
    try:
        jobs, skipped = expand(one, root, _snapshot)
    except ValueError as error:
        jobs, skipped = [], [{"source": source["name"], "reason": str(error)}]
    return {
        "source": source["name"],
        "supported_recipes": sorted({job["recipe"] for job in jobs}),
        "potential_jobs": len(jobs),
        "unsupported_cells": skipped,
    }


def run(
    config_path: Path,
    output_dir: Path,
    root: Path,
    *,
    fetcher: WikiHttpFetcher | None = None,
) -> dict[str, Any]:
    from scripts.freeze_wiki_title_bundle import freeze_titles

    config = json.loads(config_path.read_text())
    validate(config)
    root = root.resolve()
    if ".." in output_dir.parts:
        raise ValueError("output directory cannot traverse parents")
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    if not output_dir.is_relative_to(root):
        raise ValueError("output directory must be workspace-relative")
    if not within_storage(root, output_dir):
        raise ValueError("output directory escapes workspace data storage")
    if output_dir.exists():
        raise ValueError("output directory must be new")
    base = pinned_json(root, config["base_pool"])
    if base.get("schema") != POOL_SCHEMA:
        raise ValueError("base source pool schema mismatch")
    seen = prior_titles(root, base)
    fetcher = fetcher or WikiHttpFetcher(
        config.get("api", "https://en.wikipedia.org/w/api.php")
    )
    output_dir.mkdir(parents=True)
    sources: list[dict[str, Any]] = []
    discovered: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    probes: list[dict[str, Any]] = []
    frozen: list[dict[str, Any]] = []
    for family in config["families"]:
        split = planned_split(config, family["name"])
        for term_index, term in enumerate(family["terms"], 1):
            key = f"{family['name']}_{term_index:02d}"
            try:
                titles, pages = discover(fetcher, family, term)
                discovery_path = output_dir / "discovery" / f"{key}.json"
                write_json(discovery_path, pages)
                novel = [title for title in titles if title.casefold() not in seen]
                discovered.append(
                    {
                        "key": key,
                        "query": family["query_template"].replace("{term}", term),
                        "raw_response": {
                            "path": str(discovery_path.relative_to(root)),
                            "sha256": sha(discovery_path),
                        },
                        "api_pages": len(pages),
                        "matching_titles": len(titles),
                        "novel_titles": len(novel),
                        "existing_titles": len(titles) - len(novel),
                    }
                )
                for bundle_index, offset in enumerate(
                    range(
                        0,
                        min(
                            len(novel),
                            family["bundle_size"] * family["max_bundles_per_term"],
                        ),
                        family["bundle_size"],
                    ),
                    1,
                ):
                    bundle = novel[offset : offset + family["bundle_size"]]
                    name = f"p93_{key}_{bundle_index:02d}"
                    path = output_dir / "snapshots" / f"{name}_snapshot.json"
                    try:
                        receipt = freeze_titles(fetcher, bundle, name, path)
                    except (HttpError, SnapshotError, OSError, ValueError) as error:
                        failures.append(
                            {"key": name, "reason": f"{type(error).__name__}:{error}"}
                        )
                        continue
                    seen.update(title.casefold() for title in bundle)
                    source = {
                        "name": name,
                        "domain": family["domain"],
                        "topic": re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_"),
                        "split": split,
                        "snapshot": {
                            "path": str(path.relative_to(root)),
                            "sha256": sha(path),
                        },
                    }
                    sources.append(source)
                    frozen.append(
                        {
                            "source": name,
                            "titles": bundle,
                            "snapshot_id": receipt["snapshot_id"],
                            "revisions": receipt["revisions"],
                            "license": receipt["license"],
                            "pages": receipt["pages"],
                            "facts": receipt["facts"],
                        }
                    )
                    probes.append(probe(root, source, base))
            except (HttpError, SnapshotError, OSError, ValueError) as error:
                failures.append(
                    {"key": key, "reason": f"{type(error).__name__}:{error}"}
                )
    new_pool = {**base, "sources": sources}
    write_json(output_dir / "source_pool.json", new_pool)
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": sha(config_path),
        "base_pool": config["base_pool"],
        "source_pool_sha256": sha(output_dir / "source_pool.json"),
        "discovery": discovered,
        "frozen": frozen,
        "probes": probes,
        "failures": failures,
        "source_groups": len(sources),
        "frozen_pages": sum(row["pages"] for row in frozen),
        "frozen_facts": sum(row["facts"] for row in frozen),
        "probe_supported_groups": sum(bool(row["supported_recipes"]) for row in probes),
        "family_splits": {
            family["name"]: planned_split(config, family["name"])
            for family in config["families"]
        },
        "http_requests": fetcher.requests,
        "train_ready": False,
    }
    write_json(output_dir / "manifest.json", result)
    return result


def verify(output_dir: Path, root: Path) -> dict[str, Any]:
    result = json.loads((output_dir / "manifest.json").read_text())
    if result.get("schema") != SCHEMA + ".result":
        raise ValueError("intake manifest schema mismatch")
    if sha(output_dir / "source_pool.json") != result["source_pool_sha256"]:
        raise ValueError("source pool hash drift")
    pool = json.loads((output_dir / "source_pool.json").read_text())
    for source in pool["sources"]:
        pinned_json(root, source["snapshot"])
    for row in result["discovery"]:
        pin = row["raw_response"]
        discovery_path = root / pin["path"]
        if sha(discovery_path) != pin["sha256"]:
            raise ValueError("discovery response hash drift")
    return result


def repartition(
    config_path: Path, previous_dir: Path, output_dir: Path, root: Path
) -> dict[str, Any]:
    """Make a new split-pinned pool from frozen source revisions, without HTTP."""
    config = json.loads(config_path.read_text())
    validate(config)
    root = root.resolve()
    previous_dir = previous_dir if previous_dir.is_absolute() else root / previous_dir
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    for path in (previous_dir, output_dir):
        if (
            ".." in path.parts
            or not path.is_relative_to(root)
            or not within_storage(root, path)
        ):
            raise ValueError("intake directory escapes workspace data storage")
    previous = verify(previous_dir, root)
    previous_pool = json.loads((previous_dir / "source_pool.json").read_text())
    if previous["base_pool"] != config["base_pool"]:
        raise ValueError("repartition base pool differs from frozen acquisition")
    expected_queries = {
        f"{family['name']}_{index:02d}": family["query_template"].replace(
            "{term}", term
        )
        for family in config["families"]
        for index, term in enumerate(family["terms"], 1)
    }
    actual_queries = {row["key"]: row["query"] for row in previous["discovery"]}
    if actual_queries != expected_queries:
        raise ValueError("repartition discovery queries differ from frozen acquisition")
    if output_dir.exists():
        raise ValueError("output directory must be new")
    family_names = [family["name"] for family in config["families"]]
    sources = []
    for source in previous_pool["sources"]:
        matching = [
            name for name in family_names if source["name"].startswith(f"p93_{name}_")
        ]
        if len(matching) != 1:
            raise ValueError(f"source has no unique family: {source['name']}")
        sources.append({**source, "split": planned_split(config, matching[0])})
    output_dir.mkdir(parents=True)
    write_json(output_dir / "source_pool.json", {**previous_pool, "sources": sources})
    result = {
        **previous,
        "config_sha256": sha(config_path),
        "source_pool_sha256": sha(output_dir / "source_pool.json"),
        "family_splits": {name: planned_split(config, name) for name in family_names},
        "offline_repartition_from": {
            "path": str((previous_dir / "manifest.json").relative_to(root)),
            "sha256": sha(previous_dir / "manifest.json"),
        },
    }
    write_json(output_dir / "manifest.json", result)
    return result
