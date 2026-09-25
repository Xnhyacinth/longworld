"""Verify a native CodeForge expansion against the frozen bank and final mask."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.finance_code_native_adapter import verify_codeforge
from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.sharded_candidate_bank import verify_index
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise ValueError(f"blank row: {path}")
            yield json.loads(line)


def _check_novelty(base_rows: list[dict], new_rows: list[dict]) -> dict:
    old_groups = {row["candidate"]["source_group"] for row in base_rows}
    old_ids = {
        (row["candidate"]["source_kind"], row["candidate"]["semantic_task_id"])
        for row in base_rows
    }
    old_keys = {row["candidate"]["task_key"] for row in base_rows}
    groups: dict[str, str] = {}
    ids: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    keys: dict[str, tuple[str, str]] = {}
    for row in new_rows:
        group, split = row["source_group"], row["split"]
        identifier = (row["source_kind"], row["semantic_task_id"])
        key = row["task_key"]
        if group in old_groups or (group in groups and groups[group] != split):
            raise ValueError(
                "CodeForge repository group overlaps bank or crosses split"
            )
        if identifier in old_ids or key in old_keys:
            raise ValueError("CodeForge semantic task overlaps frozen bank or batch")
        identity = (group, split, row["answer_sha256"], key)
        if identifier in ids and ids[identifier] != identity:
            raise ValueError("CodeForge semantic task has inconsistent views")
        if key in keys and keys[key] != identifier:
            raise ValueError("CodeForge task key is reused for another task")
        groups[group] = split
        ids[identifier] = identity
        keys[key] = identifier
    return {
        "new_groups": len(groups),
        "group_splits": dict(sorted(groups.items())),
        "new_semantic_tasks": len(ids),
    }


def audit(
    batch: Path, base_index: Path, output: Path, *, verify_only: bool = False
) -> dict:
    batch, base_index = batch.resolve(strict=True), base_index.resolve(strict=True)
    merged = batch / "merged"
    verify_index(base_index)
    source = verify_merge(merged)
    plan = json.loads((batch / "plan.json").read_text())
    for entry in plan["sources"]:
        if entry["kind"] != "codeforge_taskbank":
            raise ValueError("expansion batch contains a non-CodeForge lane")
        lane = json.loads((batch / f"{entry['name']}.json").read_text())
        if lane["config_sha256"] != entry["config_sha256"]:
            raise ValueError("CodeForge lane config changed")
        verified = verify_codeforge(Path(lane["paths"]["root"]), Path(entry["config"]))
        if verified["rows"] != lane["rows"]:
            raise ValueError("CodeForge native reader count changed")
    base_rows = list(_rows(base_index / "candidate_refs.jsonl"))
    new_rows = list(_rows(merged / "sample_index.jsonl"))
    novelty = _check_novelty(base_rows, new_rows)
    if len(new_rows) != source["candidate_views"]:
        raise ValueError("CodeForge merged reader count changed")
    tokenizer = get_tokenizer()
    masks = []
    readers = {
        split: _rows(merged / f"candidate_{split}.jsonl") for split in ("train", "eval")
    }
    operations, lengths, splits = Counter(), Counter(), Counter()
    for index in new_rows:
        split = index["split"]
        if split not in readers:
            raise ValueError("invalid CodeForge split")
        reader = next(readers[split], None)
        if reader is None:
            raise ValueError("CodeForge reader missing")
        masks.append(audit_reader(reader, index, tokenizer, 262144))
        operations[index["operation"]] += 1
        lengths[index["length_bin"]] += 1
        splits[split] += 1
    if any(next(stream, None) is not None for stream in readers.values()):
        raise ValueError("CodeForge reader count exceeds index")
    manifest = {
        "schema_version": "longworld.p94-codeforge-expansion-audit.v1",
        "base_manifest_sha256": _sha(base_index / "manifest.json"),
        "base_index_sha256": _sha(base_index / "candidate_refs.jsonl"),
        "batch_manifest_sha256": _sha(batch / "manifest.json"),
        "merged_manifest_sha256": _sha(merged / "manifest.json"),
        "candidate_views": len(new_rows),
        **novelty,
        "operations": dict(sorted(operations.items())),
        "length_bins": dict(sorted(lengths.items())),
        "splits": dict(sorted(splits.items())),
        "exact_final_mask_rows": len(masks),
        "full_chat_tokens": sum(m["full_chat_tokens"] for m in masks),
        "supervised_tokens": sum(m["supervised_tokens"] for m in masks),
        "dependency_status": "native_program_replay_only; no reader-text deletion proof",
        "train_ready": False,
    }
    payload = "".join(json.dumps(m, sort_keys=True) + "\n" for m in masks)
    manifest["audit_jsonl_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    if verify_only:
        if (output / "mask_audit.jsonl").read_text() != payload or json.loads(
            (output / "manifest.json").read_text()
        ) != manifest:
            raise ValueError("CodeForge expansion audit replay differs")
    else:
        if output.exists():
            raise ValueError("CodeForge expansion audit output exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="p94-codeforge-audit-", dir=output.parent
        ) as raw:
            staging = Path(raw) / "audit"
            staging.mkdir()
            (staging / "mask_audit.jsonl").write_text(payload)
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            )
            staging.rename(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--base-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            audit(
                args.batch, args.base_index, args.output, verify_only=args.verify_only
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
