"""Immutable references to verified unified candidate shards.

An index extension reads candidate metadata, not giant reader bodies. Reader
bytes are copied only when a downstream consumer requests materialization.
The index is candidate-only and never promotes train_ready.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from longworld.synthesis.unified_candidate_contract import (
    CandidateLedger,
    NativeCandidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge

SCHEMA = "longworld.sharded-candidates.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _shard_path(index_dir: Path, entry: dict[str, Any]) -> Path:
    # Resolve the index root before applying `..`: data/candidates is a project
    # symlink, and resolving after the join changes the meaning of that path.
    return (index_dir.resolve() / entry["path"]).resolve()


def _entries(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError("blank sharded index row")
            yield json.loads(line)


def _add_row(
    ledger: CandidateLedger,
    row: dict[str, Any],
    positions: Counter[str],
    lanes: Counter[str],
    bins: Counter[str],
) -> None:
    candidate = NativeCandidate(
        **{name: row[name] for name in NativeCandidate.__dataclass_fields__}
    )
    if row["output_file"] != f"candidate_{candidate.split}.jsonl":
        raise ValueError("candidate output file/split mismatch")
    if (
        type(row["row_index"]) is not int
        or row["row_index"] != positions[candidate.split]
    ):
        raise ValueError("nonsequential shard reader row index")
    if row.get("source_name") is None or row.get("native_row_ref") is None:
        raise ValueError("candidate source binding missing")
    ledger.add(candidate)
    positions[candidate.split] += 1
    lanes[row["source_name"]] += 1
    bins[candidate.length_bin] += 1


def verify_index(index_dir: Path, *, full_readers: bool = False) -> dict[str, Any]:
    """Check index, source manifests and metadata; optionally hash reader bodies."""
    manifest = json.loads((index_dir / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != SCHEMA
        or manifest.get("train_ready") is not False
    ):
        raise ValueError("wrong sharded candidate manifest")
    if _sha(index_dir / "candidate_refs.jsonl") != manifest["refs_sha256"]:
        raise ValueError("sharded candidate references changed")
    shards = manifest["shards"]
    if not isinstance(shards, list) or len({s["name"] for s in shards}) != len(shards):
        raise ValueError("duplicate shard name")
    for shard in shards:
        path = _shard_path(index_dir, shard)
        if _sha(path / "manifest.json") != shard["manifest_sha256"]:
            raise ValueError(f"shard manifest changed: {shard['name']}")
        native = json.loads((path / "manifest.json").read_text())
        if (
            native.get("train_ready") is not False
            or native["files_sha256"] != shard["files_sha256"]
        ):
            raise ValueError(f"shard candidate contract changed: {shard['name']}")
        # The small source index anchors the row references on every extension.
        if (
            _sha(path / "sample_index.jsonl")
            != shard["files_sha256"]["sample_index.jsonl"]
        ):
            raise ValueError(f"shard sample index changed: {shard['name']}")
        if full_readers:
            for split in ("train", "eval"):
                file = f"candidate_{split}.jsonl"
                if _sha(path / file) != shard["files_sha256"][file]:
                    raise ValueError(f"shard reader changed: {shard['name']}/{file}")
    ledger = CandidateLedger()
    positions = Counter()
    lanes = Counter()
    bins = Counter()
    local_positions: dict[str, Counter[str]] = {
        shard["name"]: Counter() for shard in shards
    }
    references = iter(_entries(index_dir / "candidate_refs.jsonl"))
    for shard in shards:
        name = shard["name"]
        source = _shard_path(index_dir, shard) / "sample_index.jsonl"
        for source_row in _entries(source):
            entry = next(references, None)
            if entry != {"shard": name, "candidate": source_row}:
                raise ValueError(f"sharded candidate/source index differs: {name}")
            # Validate local ordering independently from global deduplication.
            _add_row(ledger, source_row, local_positions[name], lanes, bins)
            positions[source_row["split"]] += 1
    if next(references, None) is not None:
        raise ValueError("extra sharded candidate references")
    expected = {
        "candidate_views": ledger.rows,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "splits": dict(positions),
        "views_by_lane": dict(lanes),
        "length_bins": dict(sorted(bins.items())),
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("sharded candidate counts or deduplication changed")
    for shard in shards:
        native = json.loads(
            (_shard_path(index_dir, shard) / "manifest.json").read_text()
        )
        if local_positions[shard["name"]] != Counter(native["splits"]):
            raise ValueError(f"shard split rows changed: {shard['name']}")
    return manifest


def build_index(
    output: Path,
    new_shards: list[tuple[str, Path]],
    *,
    base_index: Path | None = None,
) -> dict[str, Any]:
    """Extend a frozen index without copying prior reader JSONL files."""
    if output.exists() or not new_shards:
        raise ValueError("index output must be new and contain a shard")
    base = verify_index(base_index) if base_index is not None else None
    prior = base["shards"] if base else []
    used_names = {entry["name"] for entry in prior}
    if len({name for name, _ in new_shards}) != len(new_shards) or any(
        not name or name in used_names for name, _ in new_shards
    ):
        raise ValueError("new shard names must be unique")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sharded-bank-", dir=output.parent) as temp:
        tmp = Path(temp)
        shards = []
        for old in prior:
            path = _shard_path(base_index, old)  # type: ignore[arg-type]
            shards.append({**old, "path": os.path.relpath(path, output.resolve())})
        ledger = CandidateLedger()
        positions = Counter()
        lanes = Counter()
        bins = Counter()
        local_positions: dict[str, Counter[str]] = {
            entry["name"]: Counter() for entry in shards
        }
        with (tmp / "candidate_refs.jsonl").open("x", encoding="utf-8") as dest:
            if base_index is not None:
                for entry in _entries(base_index / "candidate_refs.jsonl"):
                    row = entry["candidate"]
                    _add_row(ledger, row, local_positions[entry["shard"]], lanes, bins)
                    positions[row["split"]] += 1
                    dest.write(json.dumps(entry, ensure_ascii=False) + "\n")
            for name, raw_path in new_shards:
                path = raw_path.resolve()
                native = verify_merge(path)
                if native.get("train_ready") is not False:
                    raise ValueError("only candidate shards are allowed")
                shard = {
                    "name": name,
                    "path": os.path.relpath(path, output.resolve()),
                    "manifest_sha256": _sha(path / "manifest.json"),
                    "files_sha256": native["files_sha256"],
                }
                shards.append(shard)
                local_positions[name] = Counter()
                with (path / "sample_index.jsonl").open(encoding="utf-8") as stream:
                    for line in stream:
                        row = json.loads(line)
                        _add_row(ledger, row, local_positions[name], lanes, bins)
                        positions[row["split"]] += 1
                        dest.write(
                            json.dumps(
                                {"shard": name, "candidate": row}, ensure_ascii=False
                            )
                            + "\n"
                        )
                if local_positions[name] != Counter(native["splits"]):
                    raise ValueError(f"shard split rows changed: {name}")
        manifest = {
            "schema_version": SCHEMA,
            "shards": shards,
            "candidate_views": ledger.rows,
            "independent_semantic_tasks": ledger.independent_semantic_tasks,
            "source_scoped_semantic_tasks": ledger.independent_tasks,
            "splits": dict(positions),
            "views_by_lane": dict(lanes),
            "length_bins": dict(sorted(bins.items())),
            "refs_sha256": _sha(tmp / "candidate_refs.jsonl"),
            "train_ready": False,
        }
        _write_json(tmp / "manifest.json", manifest)
        os.rename(tmp, output)
    return manifest


def materialize(index_dir: Path, output: Path) -> dict[str, Any]:
    """Copy each source reader exactly once, checking its hash while copying."""
    if output.exists():
        raise ValueError("materialized output already exists")
    indexed = verify_index(index_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="sharded-materialize-", dir=output.parent
    ) as temp:
        tmp = Path(temp)
        output_hashes = {}
        for split in ("train", "eval"):
            file = f"candidate_{split}.jsonl"
            combined = hashlib.sha256()
            with (tmp / file).open("xb") as dest:
                for shard in indexed["shards"]:
                    digest = hashlib.sha256()
                    source = _shard_path(index_dir, shard) / file
                    with source.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            combined.update(chunk)
                            dest.write(chunk)
                    if digest.hexdigest() != shard["files_sha256"][file]:
                        raise ValueError(
                            f"shard reader changed during materialization: {shard['name']}"
                        )
            output_hashes[file] = combined.hexdigest()
        offsets = Counter()
        local = Counter()
        with (tmp / "sample_index.jsonl").open("x", encoding="utf-8") as dest:
            for entry in _entries(index_dir / "candidate_refs.jsonl"):
                row = dict(entry["candidate"])
                split = row["split"]
                if row["row_index"] != local[(entry["shard"], split)]:
                    raise ValueError("shard index order changed during materialization")
                local[(entry["shard"], split)] += 1
                shard_offset = offsets[split]
                # Global offset is the count from all earlier shards, not
                # interleaved train/eval metadata ordering.
                row["row_index"] = shard_offset
                offsets[split] += 1
                dest.write(json.dumps(row, ensure_ascii=False) + "\n")
        files = {
            **output_hashes,
            "sample_index.jsonl": _sha(tmp / "sample_index.jsonl"),
        }
        manifest = {
            "schema_version": "longworld.unified-candidates.v1",
            "candidate_views": indexed["candidate_views"],
            "independent_semantic_tasks": indexed["independent_semantic_tasks"],
            "source_scoped_semantic_tasks": indexed["source_scoped_semantic_tasks"],
            "splits": indexed["splits"],
            "views_by_lane": indexed["views_by_lane"],
            "length_bins": indexed["length_bins"],
            "sharded_index_manifest_sha256": _sha(index_dir / "manifest.json"),
            "files_sha256": files,
            "train_ready": False,
        }
        _write_json(tmp / "manifest.json", manifest)
        os.rename(tmp, output)
    return verify_merge(output)
