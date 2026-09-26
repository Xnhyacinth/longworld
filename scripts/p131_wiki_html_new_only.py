"""Export only P131 HTML-table tasks absent from the prior candidate bank."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.audit_unified_reader_mask import audit_reader
from scripts.p131_wiki_html_campaign import digest, encoded, line, pinned

SCHEMA = "longworld.p131-wiki-html-new-only.v1"


def rows(path: Path) -> list[dict]:
    return [json.loads(raw) for raw in path.read_text().splitlines()]


def compile(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA + ".config"
        or not 1 <= config.get("max_seq_len", 0) <= 262144
    ):
        raise ValueError("invalid P131 new-only config")
    campaign_path = pinned(config["campaign_manifest"])
    prior_path = pinned(config["prior_p126_manifest"])
    refs_manifest_path = pinned(config["prior_p130_manifest"])
    refs_path = pinned(config["prior_p130_refs"])
    campaign = json.loads(campaign_path.read_text())
    prior = json.loads(prior_path.read_text())
    refs_manifest = json.loads(refs_manifest_path.read_text())
    if (
        campaign.get("schema") != "longworld.p131-wiki-html-campaign.v1.manifest"
        or prior.get("p126_schema") != "longworld.p126-wiki-html-table-tasks.v1"
        or refs_manifest.get("refs_sha256") != config["prior_p130_refs"]["sha256"]
    ):
        raise ValueError("prior candidate receipts disagree")
    combined_dir = campaign_path.parent / "combined"
    combined = verify_merge(combined_dir)
    if combined["candidate_views"] != campaign["candidate_views"]:
        raise ValueError("P131 combined candidate count differs")
    prior_dir = prior_path.parent
    old = rows(prior_dir / "sample_index.jsonl")
    old_ids = {row["sample_id"] for row in old}
    old_keys = {row["task_key"] for row in old}
    old_groups = {row["source_group"] for row in old}
    reference = [row["candidate"] for row in rows(refs_path)]
    ref_ids = {row["sample_id"] for row in reference}
    ref_keys = {row["task_key"] for row in reference}
    ref_groups = {row["source_group"] for row in reference}
    source_index = rows(combined_dir / "sample_index.jsonl")
    source_readers = {
        split: rows(combined_dir / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    source_audits = {
        audit["sample_id"]: audit
        for audit_path in sorted(
            (campaign_path.parent / "chunks").glob("chunk*/tasks/audit.jsonl")
        )
        for audit in rows(audit_path)
    }
    readers = {"train": [], "eval": []}
    indexes, audits, masks, decisions = [], [], [], []
    tokenizer = get_tokenizer()
    selected_groups: dict[str, str] = {}
    for candidate in source_index:
        sample_id = candidate["sample_id"]
        split = candidate["split"]
        reader = source_readers[split][candidate["row_index"]]
        if reader["sample_id"] != sample_id or sample_id not in source_audits:
            raise ValueError("P131 reader/index/audit identity differs")
        if sample_id in old_ids:
            if candidate["task_key"] not in old_keys:
                raise ValueError("old sample ID changes semantic task key")
            decision = "prior_p126_reuse"
        else:
            if (
                candidate["task_key"] in old_keys
                or candidate["source_group"] in old_groups
                or sample_id in ref_ids
                or candidate["task_key"] in ref_keys
                or candidate["source_group"] in ref_groups
            ):
                raise ValueError("new-only source or semantic task overlaps prior bank")
            group = candidate["source_group"]
            if group in selected_groups and selected_groups[group] != split:
                raise ValueError("new-only source crosses split")
            selected_groups[group] = split
            entry = {
                **candidate,
                "output_file": f"candidate_{split}.jsonl",
                "row_index": len(readers[split]),
            }
            masks.append(audit_reader(reader, entry, tokenizer, config["max_seq_len"]))
            readers[split].append(reader)
            indexes.append(entry)
            audits.append(source_audits[sample_id])
            decision = "new_source_and_task"
        decisions.append(
            {
                "sample_id": sample_id,
                "source_group": candidate["source_group"],
                "status": decision,
            }
        )
    if (
        len(indexes) + len(old_ids) != len(source_index)
        or {row["sample_id"] for row in source_index if row["sample_id"] in old_ids}
        != old_ids
    ):
        raise ValueError("prior P126 task accounting differs")
    files = {
        "candidate_train.jsonl": b"".join(line(row) for row in readers["train"]),
        "candidate_eval.jsonl": b"".join(line(row) for row in readers["eval"]),
        "sample_index.jsonl": b"".join(line(row) for row in indexes),
        "audit.jsonl": b"".join(line(row) for row in audits),
        "mask_audit.jsonl": b"".join(line(row) for row in masks),
        "novelty_ledger.jsonl": b"".join(line(row) for row in decisions),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "p131_schema": SCHEMA,
        "code_sha256": digest(Path(__file__).read_bytes()),
        "config_sha256": digest(config_path.read_bytes()),
        "campaign_manifest_sha256": config["campaign_manifest"]["sha256"],
        "prior_p126_manifest_sha256": config["prior_p126_manifest"]["sha256"],
        "prior_p130_manifest_sha256": config["prior_p130_manifest"]["sha256"],
        "gross_campaign_tasks": len(source_index),
        "prior_p126_reused_tasks": len(old_ids),
        "candidate_views": len(indexes),
        "source_scoped_semantic_tasks": len(indexes),
        "independent_semantic_tasks": len(indexes),
        "views_by_lane": {"p131_new_wiki_html_table_scan": len(indexes)},
        "splits": {split: len(readers[split]) for split in readers},
        "new_source_groups": len(selected_groups),
        "domains": dict(sorted(Counter(row["domain"] for row in indexes).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indexes).items())),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in indexes).items())
        ),
        "min_full_chat_tokens": min(row["full_chat_tokens"] for row in indexes),
        "max_full_chat_tokens": max(row["full_chat_tokens"] for row in indexes),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indexes),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indexes),
        "exact_mask_checked": len(masks),
        "answer_sizes": dict(
            sorted(Counter(row["answer"]["count"] for row in audits).items())
        ),
        "files_sha256": {name: digest(data) for name, data in files.items()},
        "train_ready": False,
        "claim_limit": "new source+task structured table scans; short dense L2 only, no remote dependency/model gain",
    }
    files["manifest.json"] = encoded(manifest)
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P131 new-only inventory differs")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"P131 new-only byte replay differs: {name}")
    else:
        if output.exists():
            raise ValueError("new-only output already exists")
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
    verify_merge(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = compile(
        (ROOT / args.config).absolute(),
        (ROOT / args.output).absolute(),
        verify_only=args.verify_only,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "gross_campaign_tasks",
                    "prior_p126_reused_tasks",
                    "candidate_views",
                    "new_source_groups",
                    "exact_mask_checked",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
