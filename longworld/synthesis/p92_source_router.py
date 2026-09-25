"""Route frozen sources by evidence-bearing structure, not domain labels.

The matrix describes source capacity. A successful native probe is only a
possible task cell; it is not an accepted reader sample or a training claim.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from longworld.synthesis import (
    wiki_table_lookup,
    wiki_table_scan,
    wiki_table_tasks,
    wiki_world_bridge,
)

SCHEMA = "longworld.p92-source-router.v1"
WIKI_OPS = {
    "wiki_table_lookup": "L1_location",
    "wiki_table_pair": "L2_cross_document_comparison",
    "wiki_table_scan": "L2_complete_set",
}
PRIOR_WIKI_OPS = {
    "wiki_table_lookup": "table_cell_lookup",
    "wiki_table_pair": "table_pair_earlier_year",
    "wiki_table_scan": "dense_table_interval_scan",
}
UNCONNECTED_GENRES = ("book", "general_report", "agentic_trajectory")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"source path must be workspace-relative: {relative}")
    resolved = root / path
    if not resolved.is_file():
        raise ValueError(f"source file missing: {relative}")
    return resolved


def _json(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads(_path(root, relative).read_text())
    if not isinstance(value, dict):
        raise TypeError(f"source JSON must be an object: {relative}")
    return value


def _cell(
    operation: str, status: str, *, reason: str = "", count: int = 0
) -> dict[str, Any]:
    return {
        "operation": operation,
        "capability": WIKI_OPS.get(operation, "source_specific"),
        "status": status,
        "reason": reason,
        "observed_tasks": count,
    }


def _block_page_split_conflicts(sources: list[dict[str, Any]]) -> list[str]:
    """Block snapshots sharing a Wiki title across train and eval."""
    page_splits: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        if source["source_kind"] != "real_wiki" or source["split"] == "conflict":
            continue
        for doc in source["document_identities"]:
            page_splits[doc["title"].casefold()].add(source["split"])
    conflicted = sorted(
        title for title, splits in page_splits.items() if len(splits) > 1
    )
    if not conflicted:
        return conflicted
    conflict_set = set(conflicted)
    for source in sources:
        if source["source_kind"] != "real_wiki" or not any(
            doc["title"].casefold() in conflict_set
            for doc in source["document_identities"]
        ):
            continue
        source["split"] = "conflict"
        source["cells"] = [
            _cell(
                cell["operation"],
                "blocked_split_conflict",
                reason="same Wiki page appears in train and eval snapshots",
            )
            for cell in source["cells"]
        ]
    return conflicted


def _source_shape(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Record parsed dimensions without promoting them to semantic truth."""
    return {
        "table_columns": sorted(
            {
                column
                for fact in facts
                if isinstance(
                    column := fact.get("qualifiers", {}).get("table_column"), str
                )
            }
        ),
        "relations": sorted({fact["relation"] for fact in facts}),
        "facts_with_single_reader_span": sum(
            len(fact.get("supporting_spans", [])) == 1 for fact in facts
        ),
        "facts_with_unit": sum(fact.get("unit") is not None for fact in facts),
        "facts_with_time_or_version": sum(
            fact.get("time") is not None or fact.get("version") is not None
            for fact in facts
        ),
        "facts_with_scope_qualifier": sum(
            any("scope" in key for key in fact.get("qualifiers", {})) for fact in facts
        ),
    }


def _probe_wiki(job: tuple[str, str, str, int]) -> tuple[str, dict[str, Any]]:
    root_name, relative, expected_sha, max_probe_bytes = job
    root = Path(root_name)
    path = _path(root, relative)
    if sha(path) != expected_sha:
        raise ValueError(f"snapshot pin mismatch: {relative}")
    snapshot = _json(root, relative)
    if not snapshot.get("snapshot_id") or not isinstance(
        snapshot.get("documents"), list
    ):
        raise ValueError(f"invalid Wiki snapshot: {relative}")
    if path.stat().st_size > max_probe_bytes:
        return expected_sha, {
            "source_kind": "real_wiki",
            "genre": "encyclopedia_unprobed",
            "source_identity": snapshot["snapshot_id"],
            "world_group_id": snapshot["snapshot_id"],
            "document_count": len(snapshot["documents"]),
            "document_identities": [
                {"title": doc["title"], "revision_url": doc.get("revision_url")}
                for doc in snapshot["documents"]
            ],
            "fact_count": len(snapshot.get("facts", [])),
            "source_shape": _source_shape(snapshot.get("facts", [])),
            "native_structure": {
                "lookup_probe_tasks": 0,
                "cross_document_pair_probe_tasks": 0,
                "closed_table_documents": 0,
            },
            "license": snapshot.get("source", {}).get(
                "license", {"status": "unrecorded"}
            ),
            "source_pins": [{"path": relative, "sha256": expected_sha}],
            "cells": [
                _cell(
                    op,
                    "unprobed_oversize",
                    reason=f"snapshot exceeds {max_probe_bytes} bytes native probe limit",
                )
                for op in WIKI_OPS
            ],
        }
    world = wiki_world_bridge.snapshot_to_world(snapshot)
    cells = []
    typed, _ = wiki_world_bridge.structurally_typed_world(world)
    lookup = wiki_table_lookup.build_lookup_tasks(typed, max_tasks=1)
    cells.append(
        _cell(
            "wiki_table_lookup",
            "probe_supported" if lookup else "unsupported",
            reason="" if lookup else "no unambiguous structurally supported table cell",
        )
    )
    try:
        pair = wiki_table_tasks.build_table_pair_tasks(world, max_tasks=1)
        pair_reason = "no supported cross-document table-year pair"
    except ValueError as error:
        pair, pair_reason = (), str(error)
    cells.append(
        _cell(
            "wiki_table_pair",
            "probe_supported" if pair else "unsupported",
            reason="" if pair else pair_reason,
        )
    )
    scan_reasons = []
    scans = 0
    for doc in world.documents:
        if not doc.text or not doc.title.startswith("List of "):
            continue
        try:
            tasks = wiki_table_scan.build_scan_tasks(world, doc.doc_id, max_tasks=1)
        except ValueError as error:
            scan_reasons.append(f"{doc.title}: {error}")
        else:
            scans += bool(tasks)
    cells.append(
        _cell(
            "wiki_table_scan",
            "probe_supported" if scans else "unsupported",
            reason=""
            if scans
            else "; ".join(scan_reasons[:3]) or "no supported closed list table",
        )
    )
    license_data = snapshot.get("source", {}).get("license")
    return expected_sha, {
        "source_kind": "real_wiki",
        "genre": "encyclopedia_list"
        if any(doc.title.startswith("List of ") for doc in world.documents)
        else "encyclopedia_article",
        "source_identity": snapshot["snapshot_id"],
        "world_group_id": snapshot["snapshot_id"],
        "document_count": len(world.documents),
        "document_identities": [
            {"title": doc["title"], "revision_url": doc.get("revision_url")}
            for doc in snapshot["documents"]
        ],
        "fact_count": len(world.facts),
        "source_shape": _source_shape(snapshot.get("facts", [])),
        "native_structure": {
            "lookup_probe_tasks": len(lookup),
            "cross_document_pair_probe_tasks": len(pair),
            "closed_table_documents": scans,
        },
        "license": license_data
        if isinstance(license_data, dict)
        else {"status": "unrecorded"},
        "source_pins": [{"path": relative, "sha256": expected_sha}],
        "cells": cells,
    }


def _wiki_entries(
    root: Path, catalog: str | list[dict[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    jobs = []
    bindings = {}
    if isinstance(catalog, str):
        paths = sorted(root.glob(catalog))
    elif isinstance(catalog, list) and catalog:
        paths = []
        seen: set[str] = set()
        for pin in catalog:
            if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
                raise ValueError("Wiki source-pool pin is incomplete")
            relative = pin["path"]
            if relative in seen:
                raise ValueError("duplicate Wiki source-pool pin")
            seen.add(relative)
            path = _path(root, relative)
            if sha(path) != pin["sha256"]:
                raise ValueError(f"Wiki source-pool pin changed: {relative}")
            paths.append(path)
        paths.sort()
    else:
        raise ValueError("Wiki source-pool catalog must be a glob or pinned list")
    for pool_path in paths:
        relative = str(pool_path.relative_to(root))
        pool = _json(root, relative)
        if pool.get("schema") != "longworld.source-batch-pool.v2":
            raise ValueError(f"unsupported Wiki pool schema: {relative}")
        bindings[relative] = sha(pool_path)
        for source in pool.get("sources", []):
            pin = source.get("snapshot", {})
            path = pin.get("path")
            digest = pin.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise TypeError(f"missing snapshot pin in {relative}")
            if sha(_path(root, path)) != digest:
                raise ValueError(f"snapshot pin mismatch: {path}")
            if source.get("split") not in {"train", "eval"}:
                raise ValueError(f"invalid Wiki split in {relative}")
            jobs.append(
                {"snapshot": path, "sha256": digest, "source": source, "pool": relative}
            )
            bindings[path] = digest
    return jobs, bindings


def _prior_wiki_index(
    root: Path, pin: dict[str, str]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    path = _path(root, pin["path"])
    if sha(path) != pin["sha256"]:
        raise ValueError("prior candidate index pin mismatch")
    manifest_path = path.parent / "manifest.json"
    manifest = _json(root, str(manifest_path.relative_to(root)))
    if manifest.get("refs_sha256") != pin["sha256"]:
        raise ValueError("prior candidate manifest does not bind refs")
    groups: dict[str, set[str]] = defaultdict(set)
    for line in path.open():
        if not line.strip():
            continue
        candidate = json.loads(line)["candidate"]
        if candidate["source_kind"] == "real_wiki":
            groups[candidate["source_group"]].add(candidate["operation"])
    return groups, {
        pin["path"]: pin["sha256"],
        str(manifest_path.relative_to(root)): sha(manifest_path),
    }


def route(config: dict[str, Any], root: Path, *, workers: int = 1) -> dict[str, Any]:
    """Return a deterministic, source-deduplicated support matrix."""
    if config.get("schema") != SCHEMA or workers < 1:
        raise ValueError("invalid source router config or workers")
    catalog = config.get("wiki_source_pools", config.get("wiki_source_pool_glob"))
    jobs, bindings = _wiki_entries(root, catalog)
    prior = config["prior_source_manifest"]
    if sha(_path(root, prior["path"])) != prior["sha256"]:
        raise ValueError("prior source manifest pin mismatch")
    bindings[prior["path"]] = prior["sha256"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        grouped[job["sha256"]].append(job)
    max_probe_bytes = config.get("max_native_probe_bytes", 2_000_000)
    if type(max_probe_bytes) is not int or max_probe_bytes < 100_000:
        raise ValueError("invalid max_native_probe_bytes")
    unique = [
        (str(root), entries[0]["snapshot"], digest, max_probe_bytes)
        for digest, entries in sorted(grouped.items())
    ]
    if workers == 1:
        probes = dict(map(_probe_wiki, unique))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            probes = dict(pool.map(_probe_wiki, unique))
    sources = []
    prior_groups: dict[str, set[str]] = {}
    if "prior_candidate_index" in config:
        prior_groups, prior_bindings = _prior_wiki_index(
            root, config["prior_candidate_index"]
        )
        bindings.update(prior_bindings)
    for digest, entries in sorted(grouped.items()):
        item = probes[digest]
        labels = sorted(
            {(entry["source"]["domain"], entry["source"]["topic"]) for entry in entries}
        )
        splits = sorted({entry["source"]["split"] for entry in entries})
        item["domain_topics"] = [
            {"domain": domain, "topic": topic} for domain, topic in labels
        ]
        item["split"] = splits[0] if len(splits) == 1 else "conflict"
        item["source_pool_refs"] = sorted({entry["pool"] for entry in entries})
        item["prior_indexed_operations"] = sorted(
            prior_groups.get(item["world_group_id"], set())
        )
        if len(splits) > 1:
            item["cells"] = [
                _cell(
                    cell["operation"],
                    "blocked_split_conflict",
                    reason="same frozen snapshot assigned train and eval",
                )
                for cell in item["cells"]
            ]
        sources.append(item)

    # Other lanes are routed from frozen, already compiled receipts. They are
    # intentionally reported as observed capacity, never as native probe yield.
    other = config["observed_receipts"]
    for kind in ("paper", "finance", "code", "rfc_hybrid"):
        relative = other[kind]
        receipt = _json(root, relative)
        bindings[relative] = sha(_path(root, relative))
        if kind == "paper":
            for family in receipt.get("source_families", []):
                path = family["inventory"]["path"]
                if sha(_path(root, path)) != family["inventory"]["sha256"]:
                    raise ValueError(f"paper inventory pin mismatch: {path}")
                bindings[path] = family["inventory"]["sha256"]
                for source_pin in family["sources"]:
                    if sha(_path(root, source_pin["path"])) != source_pin["sha256"]:
                        raise ValueError(
                            f"paper archive pin mismatch: {source_pin['path']}"
                        )
                    bindings[source_pin["path"]] = source_pin["sha256"]
                count = (
                    receipt.get("per_family", {})
                    .get(family["family_id"], {})
                    .get("admitted_tasks", 0)
                )
                sources.append(
                    {
                        "source_kind": "real_paper_revision",
                        "genre": "research_paper",
                        "source_identity": family["work_id"],
                        "world_group_id": "paper:" + family["work_id"],
                        "split": family["split"],
                        "domain_topics": [],
                        "document_count": len(family["sources"]),
                        "fact_count": None,
                        "license": {
                            "status": "source_inventory_authorization_only; redistribution_not_inferred"
                        },
                        "source_pins": [family["inventory"], *family["sources"]],
                        "cells": [
                            _cell(
                                "paper_revision_alignment",
                                "observed_candidate" if count else "unsupported",
                                reason=""
                                if count
                                else "prior batch admitted zero revision tasks",
                                count=count,
                            )
                        ],
                    }
                )
        elif kind == "finance":
            for job in receipt.get("jobs", []):
                if job.get("status") != "verified_local_candidates":
                    continue
                sources.append(
                    {
                        "source_kind": "real_finance",
                        "genre": "annual_report_xbrl",
                        "source_identity": job["split_group_id"],
                        "world_group_id": "issuer:" + job["split_group_id"],
                        "split": job["split"],
                        "domain_topics": [{"domain": "finance", "topic": job["name"]}],
                        "document_count": job.get("compiler_metrics", {}).get(
                            "source_documents"
                        ),
                        "fact_count": job.get("compiler_metrics", {}).get(
                            "source_facts"
                        ),
                        "license": {
                            "status": "issuer_source_manifest; reuse_rights_require_separate_review"
                        },
                        "source_pins": [
                            {"path": relative, "sha256": bindings[relative]}
                        ],
                        "cells": [
                            _cell(op, "observed_candidate", count=count)
                            for op, count in sorted(
                                job.get("task_families", {}).items()
                            )
                            if count
                        ],
                    }
                )
        elif kind == "code":
            for row in receipt.get("rows", []):
                sources.append(
                    {
                        "source_kind": "real_code_workflow",
                        "genre": "repository_history",
                        "source_identity": row["repository"],
                        "world_group_id": "repo:" + row["repository"],
                        "split": row["split"],
                        "domain_topics": [
                            {
                                "domain": "software",
                                "topic": row["repository"].rsplit("/", 1)[-1],
                            }
                        ],
                        "document_count": row.get("source_episode_count"),
                        "fact_count": None,
                        "license": {
                            "status": "repository_specific; not asserted by aggregate inventory"
                        },
                        "source_pins": [
                            {"path": relative, "sha256": bindings[relative]}
                        ],
                        "cells": [
                            _cell(op, "observed_candidate", count=count)
                            for op, count in sorted(
                                row.get("program_counts", {}).items()
                            )
                            if count
                        ],
                    }
                )
        else:
            if receipt.get("generator_revision") != "p87-hybrid-v6":
                raise ValueError("RFC hybrid receipt must be corrected v6")
            sources.append(
                {
                    "source_kind": "grounded_simulation",
                    "genre": "technical_standard_plus_simulated_events",
                    "source_identity": "ietf:rfc9114",
                    "world_group_id": "ietf:rfc9114:hybrid",
                    "split": "train",
                    "domain_topics": [
                        {"domain": "network_protocols", "topic": "http3"}
                    ],
                    "document_count": 1,
                    "fact_count": None,
                    "license": {
                        "status": "ietf_source_inventory; reuse_rights_require_separate_review"
                    },
                    "source_pins": [{"path": relative, "sha256": bindings[relative]}],
                    "cells": [
                        _cell(op, "observed_candidate", count=count)
                        for op, count in sorted(receipt.get("operations", {}).items())
                    ],
                }
            )

    document_split_conflicts = _block_page_split_conflicts(sources)
    ids = [source["world_group_id"] for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("world group collision across frozen sources")
    sources.sort(key=lambda row: row["world_group_id"])
    statuses = Counter(cell["status"] for source in sources for cell in source["cells"])
    kinds = Counter(source["source_kind"] for source in sources)
    wiki_sources = [
        source
        for source in sources
        if source["source_kind"] == "real_wiki" and source["split"] != "conflict"
    ]
    split_conflicts = [
        source["world_group_id"] for source in sources if source["split"] == "conflict"
    ]
    novel_supported = Counter(
        cell["operation"]
        for source in wiki_sources
        for cell in source["cells"]
        if cell["status"] == "probe_supported"
        and PRIOR_WIKI_OPS[cell["operation"]] not in source["prior_indexed_operations"]
    )
    new_supported_groups = sorted(
        source["world_group_id"]
        for source in wiki_sources
        if not source["prior_indexed_operations"]
        and any(cell["status"] == "probe_supported" for cell in source["cells"])
    )
    return {
        "schema": SCHEMA + ".result",
        "input_sha256": dict(sorted(bindings.items())),
        "source_groups": len(sources),
        "source_groups_by_kind": dict(sorted(kinds.items())),
        "wiki_pool_references": len(jobs),
        "wiki_unique_snapshots": len(grouped),
        "split_conflict_worlds": split_conflicts,
        "document_split_conflict_titles": document_split_conflicts,
        "prior_index_novelty": {
            "wiki_worlds_already_indexed": sum(
                bool(source["prior_indexed_operations"]) for source in wiki_sources
            ),
            "wiki_worlds_absent_from_index": sum(
                not source["prior_indexed_operations"] for source in wiki_sources
            ),
            "new_supported_world_group_ids": new_supported_groups,
            "new_supported_worlds": len(new_supported_groups),
            "supported_operation_cells_absent_from_index": dict(
                sorted(novel_supported.items())
            ),
        }
        if "prior_candidate_index" in config
        else None,
        "wiki_native_capacity": {
            "lookup_worlds": sum(
                bool(source["native_structure"]["lookup_probe_tasks"])
                for source in wiki_sources
            ),
            "cross_document_pair_worlds": sum(
                bool(source["native_structure"]["cross_document_pair_probe_tasks"])
                for source in wiki_sources
            ),
            "closed_table_worlds": sum(
                bool(source["native_structure"]["closed_table_documents"])
                for source in wiki_sources
            ),
            "closed_table_documents": sum(
                source["native_structure"]["closed_table_documents"]
                for source in wiki_sources
            ),
        },
        "source_operation_cells_by_status": dict(sorted(statuses.items())),
        "unsupported_genres": {
            genre: "no frozen source plus native reader compiler in this router"
            for genre in UNCONNECTED_GENRES
        },
        "sources": sources,
        "train_ready": False,
    }


def consolidated_wiki_pool(
    result: dict[str, Any], prior: dict[str, str]
) -> dict[str, Any]:
    """Produce one native source pool without relabeling unsupported cells."""
    sources = []
    for item in result["sources"]:
        if (
            item["source_kind"] != "real_wiki"
            or item["split"] == "conflict"
            or not any(cell["status"] == "probe_supported" for cell in item["cells"])
        ):
            continue
        label = item["domain_topics"][0]
        snapshot = item["source_pins"][0]
        sources.append(
            {
                "name": "wiki_" + snapshot["sha256"][:20],
                "domain": label["domain"],
                "topic": label["topic"],
                "split": item["split"],
                "snapshot": snapshot,
            }
        )
    return {
        "schema": "longworld.source-batch-pool.v2",
        "prior_source_manifest": prior,
        "requested_recipes": list(WIKI_OPS),
        "max_tasks_by_recipe": {
            "wiki_table_lookup": 32,
            "wiki_table_pair": 24,
            "wiki_table_scan": 16,
        },
        "sources": sorted(sources, key=lambda row: row["name"]),
    }
