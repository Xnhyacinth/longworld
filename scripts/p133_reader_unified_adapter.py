"""Expose only P133 state-QA rows as a pinned unified candidate shard.

Policy rows remain in their separate source campaign. This adapter does not
certify P133 source components or insert them into any training selection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    CandidateLedger,
    NativeCandidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts import p133_state_mechanism_batch as campaign

SCHEMA = "longworld.p133-reader-unified-adapter.v1"


def _load_pin(config: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    relative = Path(config["campaign_manifest"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("campaign manifest pin must be workspace-relative")
    path = ROOT / relative
    if campaign.file_sha(path) != config["campaign_manifest_sha256"]:
        raise ValueError("P133 campaign manifest SHA differs")
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != campaign.OUTPUT_SCHEMA:
        raise ValueError("P133 campaign schema differs")
    return path, manifest


def compile_reader(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != SCHEMA:
        raise ValueError("invalid P133 reader adapter config")
    compiler_sha = campaign.file_sha(Path(__file__))
    if verify_only:
        old = json.loads((output / "manifest.json").read_text())
        if old.get("compiler_sha256") != compiler_sha:
            raise ValueError("frozen P133 reader adapter compiler SHA differs")
    source_manifest_path, source = _load_pin(config)
    source_dir = source_manifest_path.parent
    for name, expected in source["files_sha256"].items():
        if campaign.file_sha(source_dir / name) != expected:
            raise ValueError(f"P133 campaign file SHA differs: {name}")
    for name, expected in source["world_files_sha256"].items():
        if campaign.file_sha(source_dir / name) != expected:
            raise ValueError(f"P133 world/receipt SHA differs: {name}")
    source_rows = [
        json.loads(line)
        for line in (source_dir / "sample_index.jsonl").read_text().splitlines()
    ]
    proofs = {
        item["sample_id"]: item
        for item in (
            json.loads(line)
            for line in (source_dir / "proofs.jsonl").read_text().splitlines()
        )
    }
    masks = {
        item["sample_id"]: item
        for item in (
            json.loads(line)
            for line in (source_dir / "mask_audit.jsonl").read_text().splitlines()
        )
    }
    if len(source_rows) != source["accepted_task_views"] or not (
        len(proofs) == len(masks) == len(source_rows)
    ):
        raise ValueError("P133 source reader/proof/mask inventory differs")
    readers = {
        name: [
            json.loads(line) for line in (source_dir / name).read_text().splitlines()
        ]
        for name in ("qa_train.jsonl", "qa_eval.jsonl")
    }
    streams = {
        name: []
        for name in (
            "candidate_train.jsonl",
            "candidate_eval.jsonl",
            "sample_index.jsonl",
        )
    }
    ledger = CandidateLedger()
    splits = Counter()
    bins = Counter()
    worlds: dict[str, set[str]] = defaultdict(set)
    mechanisms = Counter()
    for row in source_rows:
        if row["source_kind"] != "controlled_simulation":
            continue
        if row["source_name"] != "p133_executed_state_qa":
            raise ValueError("P133 QA source lane differs")
        if row["output_file"] != f"qa_{row['split']}.jsonl":
            raise ValueError("P133 QA reader path differs")
        reader = readers[row["output_file"]][row["row_index"]]
        proof = proofs[row["sample_id"]]
        mask = masks[row["sample_id"]]
        if (
            reader["sample_id"] != row["sample_id"]
            or proof["sample_id"] != row["sample_id"]
            or mask["sample_id"] != row["sample_id"]
            or mask["status"] != "exact_assistant_mask_checked"
            or proof["mechanism"] != row["topic"]
        ):
            raise ValueError("P133 QA reader/proof/mask binding differs")
        world_id = row["source_group"]
        world_relative = Path("worlds") / world_id / "world.json"
        receipt_relative = Path("worlds") / world_id / "receipt.json"
        world_path = source_dir / world_relative
        receipt_path = source_dir / receipt_relative
        world = json.loads(world_path.read_text())
        receipt = json.loads(receipt_path.read_text())
        context, separator, _ = reader["messages"][0]["content"].partition(
            campaign.MARKER
        )
        if (
            not separator
            or world["world_id"] != world_id
            or row["native_row_ref"] != world_id
            or receipt["world_sha256"] != campaign.file_sha(world_path)
            or row["receipt_sha256"] != campaign.file_sha(receipt_path)
            or proof["source_world_sha256"] != receipt["world_sha256"]
            or context != world["reader_context"]
            or row["context_sha256"] != world["context_sha256"]
            or proof["question"]
            != next(
                task["question"]
                for task in world["tasks"]
                if task["task_id"] == proof["task_id"]
            )
            or proof["answer"] != json.loads(reader["messages"][1]["content"])
        ):
            raise ValueError("P133 QA source-world or reader bytes differ")
        native_fields = {
            name: row[name] for name in NativeCandidate.__dataclass_fields__
        }
        ledger.add(NativeCandidate(**native_fields))
        split = row["split"]
        output_file = f"candidate_{split}.jsonl"
        streams[output_file].append(campaign.dump(reader) + "\n")
        streams["sample_index.jsonl"].append(
            campaign.dump(
                {
                    **row,
                    "source_name": "p133_state_reader_QA",
                    "native_row_ref": str(source_manifest_path.parent / world_relative)
                    + ":"
                    + proof["task_id"],
                    "source_task_id": proof["task_id"],
                    "source_sample_id": row["sample_id"],
                    "source_world_path": str(world_path),
                    "source_world_sha256": receipt["world_sha256"],
                    "source_receipt_path": str(receipt_path),
                    "source_receipt_sha256": row["receipt_sha256"],
                    "source_campaign_manifest_sha256": config[
                        "campaign_manifest_sha256"
                    ],
                    "output_file": output_file,
                    "row_index": splits[split],
                }
            )
            + "\n"
        )
        splits[split] += 1
        bins[row["length_bin"]] += 1
        worlds[world_id].add(split)
        mechanisms[row["topic"]] += 1
    if (
        ledger.rows != source["qa_views"]
        or len(worlds) != source["source_worlds"]
        or any(len(value) != 1 for value in worlds.values())
        or set(worlds) != set(source["source_world_ids"])
    ):
        raise ValueError("P133 QA count or atomic source split differs")
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "p133_reader_adapter_schema": SCHEMA,
        "compiler_sha256": compiler_sha,
        "config_sha256": campaign.file_sha(config_path),
        "source_campaign_manifest_sha256": config["campaign_manifest_sha256"],
        "candidate_views": ledger.rows,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "splits": dict(sorted(splits.items())),
        "views_by_lane": {"p133_state_reader_QA": ledger.rows},
        "length_bins": dict(sorted(bins.items())),
        "source_worlds": len(worlds),
        "source_world_ids": sorted(worlds),
        "worlds_by_mechanism": source["worlds_by_mechanism"],
        "views_by_mechanism": dict(sorted(mechanisms.items())),
        "source_component_audit_status": "UNMAPPED_P133_NATIVE_ADAPTER_REQUIRED",
        "files_sha256": {
            name: campaign.digest("".join(lines)) for name, lines in streams.items()
        },
        "claim_limit": "reader QA from two controlled transition mechanisms; source components need P133 world/receipt/base-record mapping, no training/model-gain claim",
        "train_ready": False,
    }
    streams["manifest.json"] = [campaign.dump(manifest) + "\n"]
    if verify_only:
        for name, lines in streams.items():
            if (output / name).read_text() != "".join(lines):
                raise ValueError(f"frozen P133 reader shard differs: {name}")
    else:
        if output.exists():
            raise ValueError("P133 reader output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="p133-reader-", dir=output.parent
        ) as raw:
            temp = Path(raw)
            for name, lines in streams.items():
                (temp / name).write_text("".join(lines))
            os.rename(temp, output)
    verify_merge(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        campaign.dump(
            compile_reader(args.config, args.output, verify_only=args.verify_only)
        )
    )


if __name__ == "__main__":
    main()
