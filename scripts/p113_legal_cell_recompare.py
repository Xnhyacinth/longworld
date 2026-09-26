"""Recompare a frozen native dispatch against a newer pinned candidate index."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p113_legal_cell_dispatch import _inputs, _lineage
from scripts.p113_legal_cell_plan import _dump, _pinned, _sha

SCHEMA = "longworld.p113-legal-cell-recompare.v1"


def compare(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("invalid P113 recomparison config")
    _, dispatch = _pinned(ROOT, config["dispatch_config"])
    release_path, release = _pinned(ROOT, config["release_manifest"])
    index_path, index = _pinned(ROOT, config["new_index_manifest"])
    if (
        index.get("refs_sha256") != config["new_index_refs"]["sha256"]
        or index_path.parent
        != (ROOT / config["new_index_refs"]["path"]).resolve().parent
        or release.get("config_sha256") != config["dispatch_config"]["sha256"]
        or release.get("train_ready") is not False
    ):
        raise ValueError("frozen index or dispatch provenance mismatch")
    release_dir = release_path.parent
    for field, relative in (
        ("wiki_batch_sha256", "wiki_native/batch_manifest.json"),
        ("finance_batch_sha256", "finance_native/batch_manifest.json"),
        ("finance_audit_sha256", "finance_final_audit.json"),
        ("finance_unified_sha256", "finance_unified/manifest.json"),
    ):
        if _sha(release_dir / relative) != release[field]:
            raise ValueError(f"frozen native release drift: {relative}")
    wiki, _, _, _, finance = _inputs(dispatch)
    old_rows, old_counts = _lineage(
        release_dir, wiki, finance, dispatch["prior_candidate_index"]
    )
    serialized = "".join(_dump(row) for row in old_rows)
    if (
        hashlib.sha256(serialized.encode()).hexdigest() != release["lineage_sha256"]
        or (release_dir / "lineage.jsonl").read_text(encoding="utf-8") != serialized
        or any(old_counts[key] != release[key] for key in old_counts)
    ):
        raise ValueError("frozen native lineage changed")
    new_rows, counts = _lineage(release_dir, wiki, finance, config["new_index_refs"])
    novel = [
        row
        for row in new_rows
        if row["quality_status"] == "native_audited_candidate"
        and not row["prior_task_overlap"]
    ]
    tasks = sorted(
        {
            (row["source_kind"], row["native_semantic_task_id"], row["answer_sha256"])
            for row in novel
        }
    )
    if (
        len(tasks) != counts["net_distinct_tasks_vs_prior_index"]
        or len(novel) != counts["net_views_vs_prior_index"]
    ):
        raise ValueError("net task/view accounting drift")
    result = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "frozen_release_manifest_sha256": config["release_manifest"]["sha256"],
        "new_index_manifest_sha256": config["new_index_manifest"]["sha256"],
        "new_index_refs_sha256": config["new_index_refs"]["sha256"],
        "old_index_refs_sha256": dispatch["prior_candidate_index"]["sha256"],
        "quality_scope": "index novelty only; previous native reader and mask audit retained; alternate reader-text supports unexhausted",
        "net_tasks": [
            {
                "source_kind": kind,
                "semantic_task_id": task_id,
                "answer_sha256": answer_hash,
                "sample_ids": sorted(
                    row["sample_id"]
                    for row in novel
                    if (
                        row["source_kind"],
                        row["native_semantic_task_id"],
                        row["answer_sha256"],
                    )
                    == (kind, task_id, answer_hash)
                ),
            }
            for kind, task_id, answer_hash in tasks
        ],
        **counts,
    }
    if verify_only:
        if json.loads(output.read_text(encoding="utf-8")) != result:
            raise ValueError("P113 novelty recomparison replay differs")
    else:
        output.parent.mkdir(parents=True, exist_ok=False)
        output.write_text(_dump(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        _dump(compare(args.config, args.output, verify_only=args.verify_only)), end=""
    )


if __name__ == "__main__":
    main()
