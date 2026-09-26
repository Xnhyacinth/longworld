"""Plan pinned source-shape/recipe cells; native compilers still decide admission."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p113-legal-cell-plan.v1"


def _dump(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pinned(root: Path, pin: dict[str, str]) -> tuple[Path, dict[str, Any]]:
    if set(pin) != {"path", "sha256"} or not isinstance(pin["path"], str):
        raise ValueError("pin needs path and sha256")
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("pin must be project-relative")
    path = (root / relative).resolve(strict=True)
    data_root = (root / "data").resolve()
    if not (
        path.is_relative_to(root.resolve())
        or (relative.parts[0] == "data" and path.is_relative_to(data_root))
    ):
        raise ValueError("pin escapes project")
    if _sha(path) != pin["sha256"]:
        raise ValueError(f"pin hash mismatch: {relative}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def _sources(config: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    sources = []
    seen: set[tuple[str, str]] = set()
    for inventory in config["inventories"]:
        _, listing = _pinned(root, inventory["pin"])
        kind = inventory["kind"]
        if kind == "wiki_pool":
            if listing.get("schema") != "longworld.source-batch-pool.v2":
                raise ValueError("wrong Wiki inventory schema")
            for entry in listing["sources"]:
                _, snapshot = _pinned(root, entry["snapshot"])
                revisions = snapshot.get("source", {}).get("revisions")
                documents = snapshot.get("documents", [])
                if (
                    not snapshot.get("snapshot_id")
                    or not isinstance(revisions, dict)
                    or {d["title"] for d in documents} != set(revisions)
                ):
                    raise ValueError("Wiki snapshot/revisions mismatch")
                source = {
                    "source_id": snapshot["snapshot_id"],
                    "source_kind": "wiki_snapshot",
                    "source_pin": entry["snapshot"],
                    "domain": entry["domain"],
                    "topic": entry["topic"],
                    "split": entry["split"],
                    "bindings": [
                        d["doc_id"]
                        for d in documents
                        if d.get("title", "").startswith("List of ") and d.get("text")
                    ],
                    "document_titles": [d["title"].casefold() for d in documents],
                }
                sources.append(source)
        elif kind == "finance_catalog":
            if (
                listing.get("schema_version")
                != "longworld.finance-taskbank-long-catalog.v1"
            ):
                raise ValueError("wrong finance inventory schema")
            _, split_manifest = _pinned(root, inventory["split_pin"])
            split_map = {job["issuer"]: job for job in split_manifest["jobs"]}
            if len(split_map) != len(split_manifest["jobs"]):
                raise ValueError("finance split manifest duplicates issuer")
            for entry in listing["jobs"]:
                pin = {
                    "path": entry["source_manifest"],
                    "sha256": entry["source_manifest_sha256"],
                }
                _, source_manifest = _pinned(root, pin)
                issuer = entry["issuer"]
                if issuer not in split_map:
                    raise ValueError(f"finance split missing: {issuer}")
                request = source_manifest.get("request", {})
                if request.get("issuer_key", issuer) != issuer:
                    raise ValueError(f"finance issuer identity mismatch: {issuer}")
                cik = (
                    source_manifest.get("issuer") or request.get("issuer") or {}
                ).get("cik")
                if cik and cik != split_map[issuer]["source_group"]:
                    raise ValueError(f"finance issuer CIK mismatch: {issuer}")
                dates = [
                    r.get("report_date") for r in source_manifest.get("records", [])
                ]
                annual = all(
                    record.get("form") == "10-K"
                    for record in source_manifest.get("records", [])
                )
                source = {
                    "source_id": issuer,
                    "source_kind": "annual_reports",
                    "source_pin": pin,
                    "domain": "finance",
                    "topic": issuer,
                    "split": split_map[issuer]["split"],
                    "bindings": [issuer]
                    if annual
                    and len(dates) >= 4
                    and len(dates) == len(set(dates))
                    and all(dates)
                    else [],
                }
                sources.append(source)
        else:
            raise ValueError(f"unsupported inventory kind: {kind}")
    for source in sources:
        if source["split"] not in {"train", "eval"}:
            raise ValueError("invalid frozen split")
        key = (source["source_kind"], source["source_id"])
        if key in seen:
            raise ValueError(f"duplicate source identity: {key}")
        seen.add(key)
    page_splits: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        for title in source.get("document_titles", []):
            page_splits[title].add(source["split"])
    conflicted = {title for title, splits in page_splits.items() if len(splits) > 1}
    for source in sources:
        source["split_conflict"] = bool(
            conflicted.intersection(source.get("document_titles", []))
        )
    return sorted(sources, key=lambda s: (s["source_kind"], s["source_id"]))


def _params(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid or any(
        not isinstance(v, list) or not v or len({_dump(x) for x in v}) != len(v)
        for v in grid.values()
    ):
        raise ValueError("semantic parameter grid invalid")
    keys = sorted(grid)
    values = [grid[key] for key in keys]
    if math.prod(map(len, values)) > 1024:
        raise ValueError("semantic parameter grid too large")
    return [dict(zip(keys, combination)) for combination in itertools.product(*values)]


def plan(
    config: dict[str, Any], root: Path = ROOT
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if (
        config.get("schema") != SCHEMA
        or not config.get("inventories")
        or not config.get("recipes")
    ):
        raise ValueError("invalid legal-cell config")
    sources = _sources(config, root)
    renderers = config.get("renderers")
    lengths = config.get("target_lengths")
    if (
        not renderers
        or not lengths
        or any(not isinstance(r, str) or not r for r in renderers)
        or len(set(renderers)) != len(renderers)
        or len(set(lengths)) != len(lengths)
    ):
        raise ValueError("invalid presentation axes")
    if any(type(length) is not int or length <= 0 for length in lengths):
        raise ValueError("invalid target length")
    jobs: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    semantic_ids: set[str] = set()
    for recipe in config["recipes"]:
        if (recipe["source_kind"], recipe["required_shape"]) not in {
            ("wiki_snapshot", "wiki_list_document"),
            ("annual_reports", "four_annual_reports"),
        }:
            raise ValueError("unknown source-shape pairing")
        combinations = _params(recipe["semantic_parameters"])
        for source in sources:
            supported = (
                source["source_kind"] == recipe["source_kind"]
                and bool(source["bindings"])
                and not source["split_conflict"]
            )
            bindings = source["bindings"] if supported else [None]
            for binding in bindings:
                for params in combinations:
                    identity = {
                        "source_sha256": source["source_pin"]["sha256"],
                        "recipe": recipe["name"],
                        "binding": binding,
                        "semantic_parameters": params,
                    }
                    semantic_id = hashlib.sha256(_dump(identity).encode()).hexdigest()[
                        :24
                    ]
                    common = {
                        "semantic_id": semantic_id,
                        "source_id": source["source_id"],
                        "source_kind": source["source_kind"],
                        "source_pin": source["source_pin"],
                        "split": source["split"],
                        "domain": source["domain"],
                        "topic": source["topic"],
                        "recipe": recipe["name"],
                        "binding": binding,
                        "semantic_parameters": params,
                    }
                    if not supported:
                        reason = (
                            "source_kind_mismatch"
                            if source["source_kind"] != recipe["source_kind"]
                            else "cross_split_page_conflict"
                            if source["split_conflict"]
                            else "required_shape_missing"
                        )
                        unsupported.append({**common, "reason": reason})
                        continue
                    if semantic_id in semantic_ids:
                        raise ValueError("repeated semantic identity")
                    semantic_ids.add(semantic_id)
                    for renderer, target_length in itertools.product(
                        renderers, lengths
                    ):
                        presentation = {
                            "renderer": renderer,
                            "target_length": target_length,
                        }
                        job_id = hashlib.sha256(
                            _dump({"semantic_id": semantic_id, **presentation}).encode()
                        ).hexdigest()[:24]
                        jobs.append(
                            {
                                **common,
                                "job_id": job_id,
                                "presentation": presentation,
                                "status": "planned_only",
                            }
                        )
    jobs.sort(key=lambda row: row["job_id"])
    unsupported.sort(
        key=lambda row: (
            row["source_kind"],
            row["source_id"],
            row["recipe"],
            row["semantic_id"],
        )
    )
    if len({row["job_id"] for row in jobs}) != len(jobs):
        raise ValueError("repeated job identity")
    result = {
        "schema": SCHEMA + ".result",
        "source_groups": len(sources),
        "semantic_cells": len(semantic_ids),
        "planned_jobs": len(jobs),
        "unsupported_cells": len(unsupported),
        "source_kinds": dict(
            sorted(Counter(s["source_kind"] for s in sources).items())
        ),
        "splits": dict(sorted(Counter(j["split"] for j in jobs).items())),
        "unsupported_reasons": dict(
            sorted(Counter(u["reason"] for u in unsupported).items())
        ),
        "native_tasks_executed": 0,
        "train_ready": False,
    }
    return jobs, unsupported, result


def run(
    config_path: Path, output_dir: Path, *, root: Path = ROOT, verify_only: bool = False
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    jobs, unsupported, result = plan(config, root)
    rows = {
        "supported_jobs.jsonl": "".join(_dump(row) for row in jobs),
        "unsupported_cells.jsonl": "".join(_dump(row) for row in unsupported),
    }
    result.update(
        {
            "config_sha256": _sha(config_path),
            "files_sha256": {
                name: hashlib.sha256(value.encode()).hexdigest()
                for name, value in rows.items()
            },
        }
    )
    rows["manifest.json"] = _dump(result)
    if verify_only:
        if not output_dir.is_dir() or any(
            (output_dir / name).read_text(encoding="utf-8") != content
            for name, content in rows.items()
        ):
            raise ValueError("legal-cell plan replay differs from frozen output")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        for name, content in rows.items():
            (output_dir / name).write_text(content, encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(run(args.config, args.output_dir, verify_only=args.verify_only)), end=""
    )


if __name__ == "__main__":
    main()
