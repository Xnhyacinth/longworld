"""Project only novel verified Wiki views from a larger source-pool run.

The source-pool and unified candidate banks remain immutable. This adapter
checks their pinned bytes, streams final reader rows through the native Wiki
normalizer, and records source provenance for every newly emitted view.
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
from longworld.synthesis.unified_candidate_merge import _wiki, verify_merge


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError(f"expected JSONL object: {path}")
                yield value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _verified_native(native_pool: Path) -> tuple[dict[str, Any], Path]:
    result = _json(native_pool / "result.json")
    if result.get("schema") != "longworld.source-pool-batch.v2":
        raise ValueError("wrong native Wiki source-pool schema")
    for field, name in (
        ("plan_sha256", "plan.json"),
        ("batch_manifest_sha256", "batch/batch_manifest.json"),
        ("merged_manifest_sha256", "merged/manifest.json"),
    ):
        if result.get(field) != _sha(native_pool / name):
            raise ValueError(f"native Wiki {name} changed")
    plan = _json(native_pool / "plan.json")
    if plan.get("source_pool_sha256") != result.get("source_pool_sha256"):
        raise ValueError("native Wiki source-pool pin changed")
    merged = native_pool / "merged"
    manifest = _json(merged / "manifest.json")
    if manifest.get("schema") != "longworld.source-batch-merged.v1":
        raise ValueError("wrong native Wiki merged schema")
    if manifest.get("candidate_views") != result.get("candidate_views"):
        raise ValueError("native Wiki result/merged count differs")
    required = {"train.jsonl", "eval.jsonl", "sample_index.jsonl", "audit.jsonl"}
    hashes = manifest.get("files_sha256")
    if not isinstance(hashes, dict) or set(hashes) != required:
        raise ValueError("native Wiki file hashes incomplete")
    for name, digest in hashes.items():
        if _sha(merged / name) != digest:
            raise ValueError(f"native Wiki {name} changed")
    return manifest, merged


def _message_hash(reader: dict[str, Any]) -> str:
    messages = reader.get("messages")
    if not isinstance(messages, list):
        raise TypeError("candidate reader messages are missing")
    return hashlib.sha256(_dump(messages).encode("utf-8")).hexdigest()


def _fingerprint(
    candidate: NativeCandidate, message_hash: str
) -> tuple[str, str, str, str, str]:
    return (
        candidate.task_key,
        candidate.context_sha256,
        candidate.answer_sha256,
        candidate.split,
        message_hash,
    )


def _base_index(
    base_dir: Path,
) -> tuple[
    CandidateLedger,
    dict[str, tuple[str, str, str, str, str]],
    set[tuple[str, str, str, str, str]],
]:
    manifest = verify_merge(base_dir)
    ledger = CandidateLedger()
    fingerprints: dict[str, tuple[str, str, str, str, str]] = {}
    views: set[tuple[str, str, str, str, str]] = set()
    fields = NativeCandidate.__dataclass_fields__
    positions = Counter()
    ordered: dict[str, list[NativeCandidate]] = {"train": [], "eval": []}
    for row in _rows(base_dir / "sample_index.jsonl"):
        candidate = NativeCandidate(**{name: row[name] for name in fields})
        if (
            row.get("output_file") != f"candidate_{candidate.split}.jsonl"
            or row.get("row_index") != positions[candidate.split]
        ):
            raise ValueError("base candidate index position differs from reader files")
        positions[candidate.split] += 1
        ledger.add(candidate)
        ordered[candidate.split].append(candidate)
    if (
        ledger.rows != manifest["candidate_views"]
        or dict(positions) != manifest["splits"]
    ):
        raise ValueError("base candidate index count differs from manifest")
    for split, candidates in ordered.items():
        count = 0
        for candidate, reader in zip(
            candidates, _rows(base_dir / f"candidate_{split}.jsonl"), strict=True
        ):
            if reader.get("sample_id") != candidate.sample_id:
                raise ValueError("base reader/index sample ID mismatch")
            fingerprint = _fingerprint(candidate, _message_hash(reader))
            fingerprints[candidate.sample_id] = fingerprint
            views.add(fingerprint)
            count += 1
        if count != positions[split]:
            raise ValueError("base reader/index count mismatch")
    return ledger, fingerprints, views


def verify_delta(output_dir: Path, native_pool: Path, base_dir: Path) -> dict[str, Any]:
    """Verify a published delta and return paths suitable for the unified merger."""
    output_dir = Path(output_dir)
    native_pool = Path(native_pool)
    base_dir = Path(base_dir)
    manifest = _json(output_dir / "manifest.json")
    receipt = _json(output_dir / "ADAPTER_RECEIPT.json")
    if (
        manifest.get("schema_version") != "longworld.wiki-delta-candidates.v1"
        or receipt.get("schema_version") != "longworld.wiki-delta-adapter-receipt.v1"
    ):
        raise ValueError("wrong Wiki delta schema")
    if receipt.get("manifest_sha256") != _sha(output_dir / "manifest.json"):
        raise ValueError("Wiki delta adapter receipt changed")
    native_manifest, native_merged = _verified_native(native_pool)
    verify_merge(base_dir)
    for field, path in (
        ("base_manifest_sha256", base_dir / "manifest.json"),
        ("native_result_sha256", native_pool / "result.json"),
        ("native_merged_manifest_sha256", native_merged / "manifest.json"),
    ):
        if manifest.get(field) != _sha(path):
            raise ValueError(f"Wiki delta {field} pin changed")
    if receipt.get("base_dir") != str(base_dir.resolve()) or receipt.get(
        "native_pool_dir"
    ) != str(native_pool.resolve()):
        raise ValueError("Wiki delta receipt source path changed")
    expected = {"candidate_train.jsonl", "candidate_eval.jsonl", "sample_index.jsonl"}
    hashes = manifest.get("files_sha256")
    if not isinstance(hashes, dict) or set(hashes) != expected:
        raise ValueError("Wiki delta candidate file hashes incomplete")
    for name, digest in hashes.items():
        if _sha(output_dir / name) != digest:
            raise ValueError(f"Wiki delta candidate file changed: {name}")
    native_lane = {
        "paths": {
            "manifest": str(native_merged / "manifest.json"),
            "sample_index": str(native_merged / "sample_index.jsonl"),
            "train": str(native_merged / "train.jsonl"),
            "eval": str(native_merged / "eval.jsonl"),
        }
    }
    native_rows: dict[str, tuple[NativeCandidate, str]] = {}
    for candidate, reader, _ in _wiki(native_lane):
        if candidate.sample_id in native_rows:
            raise ValueError("native Wiki output repeats a sample ID")
        native_rows[candidate.sample_id] = (candidate, _message_hash(reader))
    if len(native_rows) != native_manifest["candidate_views"]:
        raise ValueError("native Wiki reader/index count changed")
    positions = Counter()
    sample_ids: set[str] = set()
    global_tasks: set[tuple[str, str]] = set()
    ordered: dict[str, list[tuple[str, str]]] = {"train": [], "eval": []}
    fields = NativeCandidate.__dataclass_fields__
    for row in _rows(output_dir / "sample_index.jsonl"):
        split = row.get("split")
        if (
            split not in {"train", "eval"}
            or row.get("output_file") != f"candidate_{split}.jsonl"
            or row.get("row_index") != positions[split]
        ):
            raise ValueError("Wiki delta index position changed")
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in sample_ids:
            raise ValueError("Wiki delta sample IDs changed")
        if sample_id not in native_rows:
            raise ValueError("Wiki delta sample absent from native reader")
        candidate = NativeCandidate(**{name: row[name] for name in fields})
        if candidate != native_rows[sample_id][0]:
            raise ValueError("Wiki delta candidate differs from native reader")
        sample_ids.add(sample_id)
        global_tasks.add((row["source_kind"], row["semantic_task_id"]))
        ordered[split].append((sample_id, native_rows[sample_id][1]))
        positions[split] += 1
    if (
        dict(positions) != manifest.get("splits")
        or len(sample_ids) != manifest.get("new_views")
        or receipt.get("new_views") != manifest.get("new_views")
        or receipt.get("new_independent_semantic_tasks")
        != manifest.get("new_independent_semantic_tasks")
        or manifest.get("native_views") != native_manifest["candidate_views"]
    ):
        raise ValueError("Wiki delta row/task counts changed")
    # This count can exceed the number of globally new tasks: additional views
    # may target tasks already present in the base bank.
    base_global_tasks: set[tuple[str, str]] = set()
    base_scoped_tasks: set[str] = set()
    for row in _rows(base_dir / "sample_index.jsonl"):
        base_global_tasks.add((row["source_kind"], row["semantic_task_id"]))
        base_scoped_tasks.add(row["task_key"])
    new_global = global_tasks - base_global_tasks
    new_scoped = {
        row["task_key"]
        for row in _rows(output_dir / "sample_index.jsonl")
        if row["task_key"] not in base_scoped_tasks
    }
    if (
        len(new_global) != manifest["new_independent_semantic_tasks"]
        or len(new_scoped) != manifest["new_source_scoped_semantic_tasks"]
    ):
        raise ValueError("Wiki delta semantic task counts differ from base")
    for split in ("train", "eval"):
        for (sample_id, message_hash), reader in zip(
            ordered[split],
            _rows(output_dir / f"candidate_{split}.jsonl"),
            strict=True,
        ):
            if (
                reader.get("sample_id") != sample_id
                or _message_hash(reader) != message_hash
            ):
                raise ValueError("Wiki delta reader differs from native reader/index")
    return {
        "rows": manifest["new_views"],
        "new_independent_semantic_tasks": manifest["new_independent_semantic_tasks"],
        "paths": {
            "manifest": str(output_dir / "manifest.json"),
            "train": str(output_dir / "candidate_train.jsonl"),
            "eval": str(output_dir / "candidate_eval.jsonl"),
            "sample_index": str(output_dir / "sample_index.jsonl"),
        },
    }


def project_delta(
    native_pool: Path, base_dir: Path, output_dir: Path
) -> dict[str, Any]:
    """Emit only new Wiki candidate views; never re-label reused tasks as new."""
    native_pool = Path(native_pool)
    base_dir = Path(base_dir)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("Wiki delta output already exists")
    native_manifest, native_merged = _verified_native(native_pool)
    ledger, base_samples, seen_views = _base_index(base_dir)
    base_global_tasks = set(ledger.global_task_answers)
    base_scoped_tasks = set(ledger.task_answers)
    native_samples: set[str] = set()
    counts = Counter()
    splits = Counter()
    operations = Counter()
    length_bins = Counter()
    new_global_tasks: set[tuple[str, str]] = set()
    new_scoped_tasks: set[str] = set()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="wiki-delta-", dir=output_dir.parent
    ) as raw:
        temp = Path(raw)
        with (
            (temp / "candidate_train.jsonl").open("x", encoding="utf-8") as train,
            (temp / "candidate_eval.jsonl").open("x", encoding="utf-8") as eval_stream,
            (temp / "sample_index.jsonl").open("x", encoding="utf-8") as index_stream,
        ):
            destinations = {"train": train, "eval": eval_stream}
            lane = {
                "paths": {
                    "manifest": str(native_merged / "manifest.json"),
                    "sample_index": str(native_merged / "sample_index.jsonl"),
                    "train": str(native_merged / "train.jsonl"),
                    "eval": str(native_merged / "eval.jsonl"),
                }
            }
            for candidate, reader, ref in _wiki(lane):
                counts["native_views"] += 1
                if candidate.sample_id in native_samples:
                    raise ValueError("native Wiki output repeats a sample ID")
                native_samples.add(candidate.sample_id)
                fingerprint = _fingerprint(candidate, _message_hash(reader))
                prior = base_samples.get(candidate.sample_id)
                if prior is not None:
                    if prior != fingerprint:
                        raise ValueError(
                            "sample ID conflicts with base reader task/context/answer/split"
                        )
                    counts["exact_base_duplicates"] += 1
                    continue
                # A regenerated ID with the same reader view is still reused data.
                if fingerprint in seen_views:
                    counts["reused_view_other_id"] += 1
                    continue
                ledger.add(candidate)
                seen_views.add(fingerprint)
                global_key = (candidate.source_kind, candidate.semantic_task_id)
                if global_key not in base_global_tasks:
                    new_global_tasks.add(global_key)
                if candidate.task_key not in base_scoped_tasks:
                    new_scoped_tasks.add(candidate.task_key)
                split = candidate.split
                destinations[split].write(
                    _dump(
                        {
                            "sample_id": candidate.sample_id,
                            "messages": reader["messages"],
                        }
                    )
                    + "\n"
                )
                record = candidate.to_dict()
                record.update(
                    source_name="wiki_task_scale_delta",
                    native_row_ref=ref,
                    native_result_sha256=_sha(native_pool / "result.json"),
                    output_file=f"candidate_{split}.jsonl",
                    row_index=splits[split],
                )
                index_stream.write(_dump(record) + "\n")
                splits[split] += 1
                operations[candidate.operation] += 1
                length_bins[candidate.length_bin] += 1
                counts["new_views"] += 1
        if counts["native_views"] != native_manifest["candidate_views"]:
            raise ValueError("native Wiki index/reader count differs from manifest")
        files = {
            name: _sha(temp / name)
            for name in (
                "candidate_train.jsonl",
                "candidate_eval.jsonl",
                "sample_index.jsonl",
            )
        }
        summary = {
            "schema_version": "longworld.wiki-delta-candidates.v1",
            "base_manifest_sha256": _sha(base_dir / "manifest.json"),
            "native_result_sha256": _sha(native_pool / "result.json"),
            "native_merged_manifest_sha256": _sha(native_merged / "manifest.json"),
            "native_views": counts["native_views"],
            "exact_base_duplicates": counts["exact_base_duplicates"],
            "reused_view_other_id": counts["reused_view_other_id"],
            "new_views": counts["new_views"],
            "new_source_scoped_semantic_tasks": len(new_scoped_tasks),
            "new_independent_semantic_tasks": len(new_global_tasks),
            "splits": dict(splits),
            "operations": dict(sorted(operations.items())),
            "length_bins": dict(sorted(length_bins.items())),
            "files_sha256": files,
            "train_ready": False,
        }
        (temp / "manifest.json").write_text(_dump(summary) + "\n", encoding="utf-8")
        receipt = {
            "schema_version": "longworld.wiki-delta-adapter-receipt.v1",
            "manifest_sha256": _sha(temp / "manifest.json"),
            "base_dir": str(base_dir.resolve()),
            "native_pool_dir": str(native_pool.resolve()),
            "new_views": counts["new_views"],
            "new_independent_semantic_tasks": len(new_global_tasks),
        }
        (temp / "ADAPTER_RECEIPT.json").write_text(
            _dump(receipt) + "\n", encoding="utf-8"
        )
        os.rename(temp, output_dir)
    verify_delta(output_dir, native_pool, base_dir)
    return summary
