"""Expand a pinned source pool into supported native reader-task jobs.

The planner never treats a domain label as evidence. Each requested operation
is probed against the pinned source with its native task builder; unsupported
cells are retained in the plan report instead of becoming empty exports.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from longworld.synthesis import (
    wiki_table_lookup,
    wiki_table_scan,
    wiki_table_tasks,
    wiki_world_bridge,
)

SCHEMA = "longworld.source-batch-pool.v2"
RECIPES = {
    "wiki_table_pair": "paired_32k_64k_or_native",
    "wiki_table_scan": "native_whole_pages",
    "wiki_table_lookup": "native_whole_pages",
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:34] or "source"


def expand(
    config: dict[str, Any], root: Path, read_snapshot: Any
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return (jobs, unsupported cells); read_snapshot verifies source pins."""
    sources = config.get("sources")
    requested = config.get("requested_recipes")
    if not isinstance(sources, list) or not sources:
        raise ValueError("source pool needs sources")
    if (
        not isinstance(requested, list)
        or not requested
        or len(requested) != len(set(requested))
        or any(recipe not in RECIPES for recipe in requested)
    ):
        raise ValueError("source pool requested_recipes invalid")
    quotas = config.get("max_tasks_by_recipe")
    if not isinstance(quotas, dict) or any(
        type(quotas.get(recipe)) is not int or not 1 <= quotas[recipe] <= 96
        for recipe in requested
    ):
        raise ValueError("source pool needs bounded recipe quotas")
    jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    names: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or not all(
            isinstance(source.get(key), str) and source[key].strip()
            for key in ("name", "domain", "topic", "split")
        ):
            raise ValueError("source entry lacks name/domain/topic/split")
        if source["split"] not in {"train", "eval"}:
            raise ValueError("source pool split invalid")
        name = source["name"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", name) or name in names:
            raise ValueError("source pool name invalid or repeated")
        names.add(name)
        snapshot_pin = source.get("snapshot")
        if not isinstance(snapshot_pin, dict):
            raise TypeError("source entry lacks pinned snapshot")
        snapshot = read_snapshot(root, snapshot_pin)
        pinned = {
            **snapshot_pin,
            "snapshot_id": snapshot["snapshot_id"],
            "revisions": snapshot["source"]["revisions"],
        }
        world = wiki_world_bridge.snapshot_to_world(snapshot)
        for recipe in requested:
            base = {
                "recipe": recipe,
                "snapshot": pinned,
                "domain": source["domain"],
                "topic": source["topic"],
                "split": source["split"],
                "max_tasks": quotas[recipe],
                "length_policy": RECIPES[recipe],
                "allow_task_rejects": True,
            }
            if recipe == "wiki_table_pair":
                try:
                    tasks = wiki_table_tasks.build_table_pair_tasks(world, max_tasks=1)
                except ValueError as error:
                    tasks = ()
                    reason = str(error)
                else:
                    reason = "no supported cross-document table-year pair"
                if tasks:
                    jobs.append({"name": f"{name}_pair", **base})
                else:
                    skipped.append({"source": name, "recipe": recipe, "reason": reason})
                continue
            if recipe == "wiki_table_lookup":
                typed, _ = wiki_world_bridge.structurally_typed_world(world)
                tasks = wiki_table_lookup.build_lookup_tasks(typed, max_tasks=1)
                if tasks:
                    jobs.append({"name": f"{name}_lookup", **base})
                else:
                    skipped.append(
                        {
                            "source": name,
                            "recipe": recipe,
                            "reason": "no unambiguous structurally supported table cell",
                        }
                    )
                continue
            for doc in world.documents:
                if not doc.text or not doc.title.startswith("List of "):
                    continue
                cell = {"source": name, "recipe": recipe, "table_title": doc.title}
                try:
                    tasks = wiki_table_scan.build_scan_tasks(
                        world, doc.doc_id, max_tasks=1
                    )
                except ValueError as error:
                    tasks = ()
                    reason = str(error)
                else:
                    reason = "no nontrivial closed-table interval"
                if tasks:
                    digest = hashlib.sha256(doc.title.encode()).hexdigest()[:8]
                    jobs.append(
                        {
                            "name": f"{name[:28]}_scan_{digest}",
                            "table_title": doc.title,
                            **base,
                        }
                    )
                else:
                    skipped.append({**cell, "reason": reason})
    if not jobs:
        raise ValueError("source pool has no supported task cells")
    return jobs, skipped
