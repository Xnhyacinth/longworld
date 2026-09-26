"""Materialize and retokenize a frozen mixed-source candidate selection.

The output is a reviewable local research set, not a training authorization.
Selected shard readers are copied byte-for-byte after pointer, answer and
assistant-only loss-mask checks. Unselected reader bodies are not copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import (
    TOKENIZER_MODEL,
    TOKENIZER_REVISION,
    get_tokenizer,
)
from longworld.synthesis.sharded_candidate_bank import _shard_path, verify_index
from scripts.audit_unified_reader_mask import audit_reader
from scripts.select_p90_balanced_candidates import verify_selection

SCHEMA = "longworld.p95-balanced-materialized-candidates.v1"
SELECTION_SCHEMA = "longworld.balanced-candidate-selection.v1"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _selected(
    selection_dir: Path, index_dir: Path, source: dict, selection_config: Path
) -> list[dict]:
    config = json.loads(selection_config.read_text())
    if (
        ROOT / config.get("input_index", "")
    ).resolve() != index_dir.resolve() or not config.get("codeforge_proofs"):
        raise ValueError(
            "selection config must bind this index and CodeForge proof gate"
        )
    verify_selection(
        index_dir,
        selection_dir,
        seed=config["seed"],
        max_per_group=config["max_per_source_group"],
        max_per_cell=config["max_per_cell"],
        max_per_kind_by_split=config["max_per_source_kind_by_split"],
        max_supervised_tokens_by_kind=config.get("max_supervised_tokens_by_kind"),
        codeforge_proofs=config["codeforge_proofs"],
        code_content_proof=config.get("code_content_proof"),
        code_content_proofs=config.get("code_content_proofs"),
        shared_world_rebalance=config.get("shared_world_rebalance"),
        require_dependency_status=config.get("require_dependency_status", False),
    )
    selection = json.loads((selection_dir / "manifest.json").read_text())
    if (
        selection.get("schema_version") != SELECTION_SCHEMA
        or selection.get("train_ready") is not False
        or selection.get("input_manifest_sha256") != sha(index_dir / "manifest.json")
        or selection.get("input_refs_sha256") != source["refs_sha256"]
        or selection.get("selected_refs_sha256")
        != sha(selection_dir / "selected_refs.jsonl")
    ):
        raise ValueError("selection is not bound to the frozen sharded index")
    selected = [
        json.loads(line)
        for line in (selection_dir / "selected_refs.jsonl").read_text().splitlines()
        if line
    ]
    if len(selected) != selection["after"]["views"]:
        raise ValueError("selected view count differs from selection receipt")
    by_pointer = {}
    for line in (index_dir / "candidate_refs.jsonl").read_text().splitlines():
        entry = json.loads(line)
        row = entry["candidate"]
        pointer = entry["shard"], row["split"], row["row_index"]
        if pointer in by_pointer:
            raise ValueError("sharded candidate pointer repeats")
        by_pointer[pointer] = entry
    ids: set[str] = set()
    tasks: set[tuple[str, str]] = set()
    for rank, entry in enumerate(selected):
        if entry.get("selection_rank") != rank:
            raise ValueError("selection ranks are not contiguous")
        source_entry = {
            key: value for key, value in entry.items() if key != "selection_rank"
        }
        row = entry["candidate"]
        pointer = entry["shard"], row["split"], row["row_index"]
        if by_pointer.get(pointer) != source_entry:
            raise ValueError("selected pointer or task metadata differs from index")
        sample_id = row["sample_id"]
        task = row["source_kind"], row["semantic_task_id"]
        if sample_id in ids or task in tasks:
            raise ValueError("selection repeats a reader or semantic task")
        ids.add(sample_id)
        tasks.add(task)
    if len(tasks) != selection["after"]["independent_semantic_tasks"]:
        raise ValueError("selected semantic-task count differs")
    return selected


def _build(
    index_dir: Path,
    selection_dir: Path,
    selection_config: Path,
    output: Path,
    max_seq_len: int,
) -> dict:
    source = verify_index(index_dir)
    selected = _selected(selection_dir, index_dir, source, selection_config)
    shards = {item["name"]: item for item in source["shards"]}
    targets: dict[tuple[str, str], dict[int, dict]] = defaultdict(dict)
    for entry in selected:
        row = entry["candidate"]
        targets[(entry["shard"], row["split"])][row["row_index"]] = entry
    tokenizer = get_tokenizer()
    output.mkdir(parents=True)
    spool_positions: dict[int, tuple[int, int]] = {}
    checked: dict[int, dict] = {}
    with (output / "reader_spool.bin").open("xb+") as spool:
        for (shard_name, split), positions in sorted(targets.items()):
            shard = shards.get(shard_name)
            if shard is None:
                raise ValueError("selection names an unknown shard")
            filename = f"candidate_{split}.jsonl"
            source_path = _shard_path(index_dir, shard) / filename
            digest = hashlib.sha256()
            found: set[int] = set()
            with source_path.open("rb") as stream:
                for position, line in enumerate(stream):
                    digest.update(line)
                    entry = positions.get(position)
                    if entry is None:
                        continue
                    candidate = entry["candidate"]
                    reader = json.loads(line)
                    audit = audit_reader(reader, candidate, tokenizer, max_seq_len)
                    offset = spool.tell()
                    spool.write(line)
                    rank = entry["selection_rank"]
                    spool_positions[rank] = (offset, len(line))
                    checked[rank] = audit
                    found.add(position)
                    if len(checked) % 50 == 0:
                        print(
                            f"[materialize] checked {len(checked)}/{len(selected)} readers",
                            file=sys.stderr,
                            flush=True,
                        )
            if digest.hexdigest() != shard["files_sha256"][filename]:
                raise ValueError(
                    f"selected shard reader file hash differs: {shard_name}"
                )
            if found != set(positions):
                raise ValueError(
                    f"selected shard reader pointers are missing: {shard_name}"
                )
        if len(checked) != len(selected):
            raise ValueError("selected reader count differs from completed audit")
        output_positions: Counter[str] = Counter()
        counts: Counter[str] = Counter()
        by_kind: Counter[str] = Counter()
        by_length: Counter[str] = Counter()
        full_tokens = supervised_tokens = 0
        with (
            (output / "train.jsonl").open("xb") as train,
            (output / "eval.jsonl").open("xb") as eval_file,
            (output / "sample_index.jsonl").open("xb") as index_stream,
            (output / "mask_audit.jsonl").open("xb") as mask_stream,
        ):
            readers = {"train": train, "eval": eval_file}
            for entry in selected:
                rank = entry["selection_rank"]
                candidate = entry["candidate"]
                split = candidate["split"]
                offset, length = spool_positions[rank]
                spool.seek(offset)
                raw = spool.read(length)
                if len(raw) != length:
                    raise ValueError("temporary selected reader spool is incomplete")
                readers[split].write(raw)
                index_stream.write(
                    _dump(
                        {
                            "selection_rank": rank,
                            "output_file": f"{split}.jsonl",
                            "materialized_row_index": output_positions[split],
                            "shard": entry["shard"],
                            "source_row_index": candidate["row_index"],
                            "candidate": candidate,
                            "reader_sha256": checked[rank]["reader_sha256"],
                        }
                    )
                )
                mask_stream.write(_dump({"selection_rank": rank, **checked[rank]}))
                output_positions[split] += 1
                counts[split] += 1
                by_kind[candidate["source_kind"]] += 1
                by_length[candidate["length_bin"]] += 1
                full_tokens += checked[rank]["full_chat_tokens"]
                supervised_tokens += checked[rank]["supervised_tokens"]
    (output / "reader_spool.bin").unlink()
    files = {
        name: sha(output / name)
        for name in (
            "train.jsonl",
            "eval.jsonl",
            "sample_index.jsonl",
            "mask_audit.jsonl",
        )
    }
    mask = {
        "schema_version": SCHEMA + ".mask",
        "checked_rows": len(selected),
        "splits": dict(sorted(counts.items())),
        "full_chat_tokens": full_tokens,
        "supervised_tokens": supervised_tokens,
        "tokenizer": {"model_id": TOKENIZER_MODEL, "revision": TOKENIZER_REVISION},
        "source_index_manifest_sha256": sha(index_dir / "manifest.json"),
        "selection_manifest_sha256": sha(selection_dir / "manifest.json"),
        "selection_config_sha256": sha(selection_config),
        "audit_index_sha256": files["mask_audit.jsonl"],
        "train_ready": False,
    }
    (output / "mask_audit.json").write_bytes(_dump(mask))
    files["mask_audit.json"] = sha(output / "mask_audit.json")
    manifest = {
        "schema_version": SCHEMA,
        "source_index_manifest_sha256": sha(index_dir / "manifest.json"),
        "source_index_refs_sha256": source["refs_sha256"],
        "selection_manifest_sha256": sha(selection_dir / "manifest.json"),
        "selection_config_sha256": sha(selection_config),
        "selected_refs_sha256": sha(selection_dir / "selected_refs.jsonl"),
        "selected_views": len(selected),
        "independent_semantic_tasks": len(selected),
        "splits": dict(sorted(counts.items())),
        "by_source_kind": dict(sorted(by_kind.items())),
        "by_length": dict(sorted(by_length.items())),
        "full_chat_tokens": full_tokens,
        "supervised_tokens": supervised_tokens,
        "files_sha256": files,
        "scope": "candidate_only_exact_reader_bytes_and_assistant_mask",
        "train_ready": False,
    }
    (output / "manifest.json").write_bytes(_dump(manifest))
    return manifest


def run(
    index_dir: Path,
    selection_dir: Path,
    selection_config: Path,
    output_dir: Path,
    *,
    max_seq_len: int = 262144,
    verify_only: bool = False,
) -> dict:
    if max_seq_len < 2 or output_dir.exists() != verify_only:
        raise ValueError("output must be new, or present for --verify-only")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="p95-selected-materialize-", dir=output_dir.parent
    ) as raw:
        staged = Path(raw) / "data"
        manifest = _build(
            index_dir, selection_dir, selection_config, staged, max_seq_len
        )
        if verify_only:
            original = json.loads((output_dir / "manifest.json").read_text())
            if manifest != original or any(
                sha(staged / name) != sha(output_dir / name)
                for name in (*manifest["files_sha256"], "manifest.json")
            ):
                raise ValueError("materialized selection differs from replay")
        else:
            os.rename(staged, output_dir)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seq-len", type=int, default=262144)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.index,
                args.selection,
                args.selection_config,
                args.output,
                max_seq_len=args.max_seq_len,
                verify_only=args.verify_only,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
