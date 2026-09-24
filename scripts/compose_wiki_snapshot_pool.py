"""Compose pinned, disjoint Wiki snapshots and compile lookup reader candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import wiki_snapshot_concat
from scripts import export_p76_wiki_lookup

CONFIG_SCHEMA = "longworld.wiki-snapshot-concat-config.v1"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _read_pinned_json(spec: dict[str, str]) -> dict[str, Any]:
    path = _path(spec["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise ValueError(f"pinned catalog hash mismatch: {path}")
    return json.loads(raw)


def _validate_split_assignments(
    config: dict[str, Any],
    parts: list[tuple[dict[str, Any], str, str]],
) -> None:
    """Check declared splits against source pool and earlier page assignments."""
    catalog = _read_pinned_json(config["source_pool_catalog"])
    if catalog.get("schema") != "longworld.source-batch-pool.v2":
        raise ValueError("wrong source pool catalog schema")
    assignments: dict[str, set[str]] = {}
    for source in catalog["sources"]:
        assignments.setdefault(source["snapshot"]["sha256"], set()).add(source["split"])
    prior_spec = catalog["prior_source_manifest"]
    prior_path = _path(prior_spec["path"])
    prior_raw = prior_path.read_bytes()
    if hashlib.sha256(prior_raw).hexdigest() != prior_spec["sha256"]:
        raise ValueError("prior source manifest hash mismatch")
    prior = {"train": set(), "eval": set()}
    for line in prior_raw.splitlines():
        row = json.loads(line)
        if row.get("split") in prior:
            prior[row["split"]].update(row["revisions"])
    for snapshot, digest, split in parts:
        if assignments.get(digest) != {split}:
            raise ValueError("component split differs from pinned source catalog")
        opposite = "eval" if split == "train" else "train"
        overlap = set(snapshot["source"]["revisions"]) & prior[opposite]
        if overlap:
            raise ValueError(f"component page crosses prior split: {min(overlap)}")


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Fail closed on source identity, then materialize one research candidate."""
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    raw = config_path.read_bytes()
    config = json.loads(raw)
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError("wrong composition config schema")
    split, domain, topic = (config.get(key) for key in ("split", "domain", "topic"))
    if split not in {"train", "eval"} or not all(
        isinstance(value, str) and value.strip() for value in (domain, topic)
    ):
        raise ValueError("split/domain/topic required")
    max_tasks = config.get("max_tasks", 8)
    if type(max_tasks) is not int or not 1 <= max_tasks <= 32:
        raise ValueError("max_tasks must be in [1, 32]")
    specs = config.get("components")
    if not isinstance(specs, list) or len(specs) < 2:
        raise ValueError("at least two component specs required")
    parts: list[tuple[dict[str, Any], str, str]] = []
    for spec in specs:
        if not isinstance(spec, dict) or set(spec) != {"path", "sha256", "split"}:
            raise ValueError("component spec requires path, sha256, split")
        snapshot, digest = wiki_snapshot_concat.load_pinned(
            _path(spec["path"]), spec["sha256"]
        )
        parts.append((snapshot, digest, spec["split"]))
    _validate_split_assignments(config, parts)
    snapshot = wiki_snapshot_concat.compose_snapshots(parts, split=split)
    output_dir.mkdir(parents=True)
    snapshot_path = output_dir / "composite_snapshot.json"
    snapshot_bytes = (_dump(snapshot) + "\n").encode("utf-8")
    snapshot_path.write_bytes(snapshot_bytes)
    candidate_dir = output_dir / "candidates"
    native = export_p76_wiki_lookup.export(
        snapshot_path,
        candidate_dir,
        domain=domain,
        topic=topic,
        split=split,
        max_tasks=max_tasks,
    )
    index_path = candidate_dir / "sample_index.jsonl"
    indexes = [json.loads(line) for line in index_path.read_text().splitlines() if line]
    receipt = {
        "schema": wiki_snapshot_concat.SCHEMA,
        "config_sha256": hashlib.sha256(raw).hexdigest(),
        "source_pool_catalog_sha256": config["source_pool_catalog"]["sha256"],
        "composite_snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "source_group": snapshot["snapshot_id"],
        "component_snapshot_ids": [part[0]["snapshot_id"] for part in parts],
        "component_sha256": [part[1] for part in parts],
        "split": split,
        "domain": domain,
        "topic": topic,
        "documents": len(snapshot["documents"]),
        "source_characters": sum(len(doc["text"]) for doc in snapshot["documents"]),
        "facts": len(snapshot["facts"]),
        "independent_tasks": native["independent_tasks"],
        "candidate_rows": native["candidate_rows"],
        "rejected_rows": native["rejected_rows"],
        "final_chat_tokens": [row["full_chat_tokens"] for row in indexes],
        "quality_status": "research_candidate",
        "train_ready": False,
        "capability_claim": "source-preserving position/length scaling only; no new cross-source dependency",
    }
    (output_dir / "COMPOSITION_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(_dump(run(args.config, args.output_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
