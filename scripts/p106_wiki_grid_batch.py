"""Compile P95 repaired Wiki readers through pinned P105 task logic.

Each process uses a P106 source-loader adapter for the frozen P95 revisions.
The P105 compiler and reader logic are reused unchanged and SHA-pinned.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import p105_wiki_grid_batch as base
from scripts.p106_freeze_width_revisions import _pin, _sha

SCHEMA = "longworld.p106-wiki-grid-category.v1"
RAW_ROOT: Path | None = None


def _source_for_p106(job: dict, raw: dict, source: dict):
    if RAW_ROOT is None:
        raise RuntimeError("P106 source adapter was not initialized")
    snapshot = json.loads(_pin(source["snapshot"]).read_text())
    document = next(
        doc for doc in snapshot["documents"] if doc["doc_id"] == job["doc_id"]
    )
    if (
        document["title"] != job["title"]
        or document["revision_url"] != raw["revision_url"]
        or document["page_url"] != raw["page_url"]
        or source["name"] != raw["source_group"]
        or source["split"] != raw["split"]
        or source["snapshot"]["sha256"] != raw["source_snapshot_sha256"]
    ):
        raise ValueError("P106 exact source title/URL/split/revision differs")
    response_path = RAW_ROOT / raw["response_path"]
    if _sha(response_path) != raw["response_sha256"]:
        raise ValueError("P106 raw response changed")
    payload = json.loads(response_path.read_text())
    page = payload["query"]["pages"][0]
    revision = page["revisions"][0]
    if page["title"] != job["title"] or revision["revid"] != job["revid"]:
        raise ValueError("P106 raw revision identity differs")
    return snapshot, document, revision["slots"]["main"]["content"], page


def _initialize_worker(raw_root: str) -> None:
    global RAW_ROOT
    RAW_ROOT = Path(raw_root)
    # Process-local adapter injection. No P105 module/file or frozen asset is
    # changed; each worker executes the pinned P105 compiler serially.
    base._source_for = _source_for_p106


def _compile_job(job: tuple) -> tuple[list, list]:
    accepted, rejected = base._compile_one(job)
    renamed = []
    for reader, index, proof in accepted:
        original = index["sample_id"]
        if not original.startswith("p105-wiki-grid-"):
            raise ValueError("P105 delegated sample ID format changed")
        new_id = "p106-wiki-grid-" + original.removeprefix("p105-wiki-grid-")
        reader = dict(reader, sample_id=new_id, example_id=new_id)
        index = dict(index, sample_id=new_id, example_id=new_id)
        proof = dict(proof, sample_id=new_id)
        renamed.append((reader, index, proof))
    return renamed, rejected


def _validate(config: dict, raw: dict, support: dict, gate: dict) -> None:
    if (
        raw["source_pins"]["source_pool"]["sha256"] != config["source_pool"]["sha256"]
        or raw["source_pins"]["prior_p97_pool"]["sha256"]
        != config["prior_p97_pool"]["sha256"]
        or raw["source_pins"]["source_gate"]["sha256"]
        != config["source_gate"]["sha256"]
        or support["raw_manifest_sha256"] != config["raw_manifest"]["sha256"]
        or support["source_pool_sha256"] != config["source_pool"]["sha256"]
        or support["reader_tasks_admitted"] != 0
        or raw["failed_pages"]
        or not gate.get("prior_router", {}).get("sha256")
    ):
        raise ValueError("P106 cross-pool/source-gate lineage differs")


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if (
        config.get("schema") != SCHEMA
        or not 1 <= config.get("workers", 0) <= 4
        or not 1 <= config.get("max_tasks_per_table", 0) <= 8
        or not 1 <= config.get("max_full_tokens", 0) <= 131072
        or _sha(ROOT / "scripts/p105_wiki_grid_batch.py")
        != config["p105_compiler_sha256"]
        or _sha(ROOT / "scripts/p105_wiki_reader_cells.py")
        != config["p105_reader_sha256"]
    ):
        raise ValueError("P106 config or delegated P105 code pin differs")
    if output_dir.exists() != verify_only:
        raise ValueError("P106 output must be new, or exist for verify-only")
    raw = json.loads(_pin(config["raw_manifest"]).read_text())
    support = json.loads(_pin(config["target_ledger"]).read_text())
    pool = json.loads(_pin(config["source_pool"]).read_text())
    gate = json.loads(_pin(config["source_gate"]).read_text())
    _pin(config["prior_p97_pool"])
    _validate(config, raw, support, gate)
    if gate.get("accepted_groups") != len(pool["sources"]):
        raise ValueError("P106 source pool count differs from global gate")
    source_by_name = {source["name"]: source for source in pool["sources"]}
    raw_by_doc = {record["doc_id"]: record for record in raw["records"]}
    jobs = [
        (
            row,
            raw_by_doc[row["doc_id"]],
            source_by_name[row["source_group"]],
            config["max_full_tokens"],
            config["max_tasks_per_table"],
        )
        for row in support["ledger"]
        if row["reader_status"] == "reader_cell_aligned_with_options"
    ]
    if len(jobs) != support["reader_supported_tables"] or any(
        job[0]["split"] != job[2]["split"] or job[0]["title"] != job[1]["title"]
        for job in jobs
    ):
        raise ValueError("P106 support/job source identity differs")
    raw_root = (ROOT / config["raw_manifest"]["path"]).parent
    with ProcessPoolExecutor(
        max_workers=config["workers"],
        initializer=_initialize_worker,
        initargs=(str(raw_root),),
    ) as executor:
        results = list(executor.map(_compile_job, jobs))
    readers = {"train": [], "eval": []}
    indices, proofs, rejected = [], [], []
    for accepted, failures in results:
        rejected.extend(failures)
        for reader, index, proof in accepted:
            readers[index["split"]].append(reader)
            indices.append(index)
            proofs.append(proof)
    if len({row["task_id"] for row in indices}) != len(indices):
        raise ValueError("P106 duplicate semantic task ID")
    payloads = {
        "train.jsonl": readers["train"],
        "eval.jsonl": readers["eval"],
        "sample_index.jsonl": indices,
        "audit.jsonl": proofs,
        "rejected.jsonl": rejected,
    }
    if not verify_only:
        output_dir.mkdir(parents=True)
    for name, rows in payloads.items():
        content = "".join(base._dump(row) + "\n" for row in rows)
        path = output_dir / name
        if verify_only:
            if path.read_text() != content:
                raise ValueError(f"P106 native replay drift: {name}")
        else:
            path.write_text(content)
    manifest = {
        "schema": SCHEMA + ".result",
        "config_sha256": _sha(config_path),
        "code_sha256": _sha(ROOT / "scripts/p106_wiki_grid_batch.py"),
        "delegated_p105_compiler_sha256": config["p105_compiler_sha256"],
        "delegated_p105_reader_sha256": config["p105_reader_sha256"],
        "source_pins": {
            name: config[name]
            for name in (
                "raw_manifest",
                "target_ledger",
                "source_pool",
                "source_gate",
                "prior_p97_pool",
            )
        },
        "source_supported_tables": len(jobs),
        "gross_category_options": support["candidate_category_options"],
        "candidate_views": len(indices),
        "independent_tasks": len(indices),
        "split_views": dict(sorted(Counter(row["split"] for row in indices).items())),
        "worlds": dict(sorted(Counter(row["world_id"] for row in indices).items())),
        "domains": dict(sorted(Counter(row["domain"] for row in indices).items())),
        "topics": dict(sorted(Counter(row["topic"] for row in indices).items())),
        "operations": dict(
            sorted(Counter(row["operation"] for row in indices).items())
        ),
        "length_bins": dict(
            sorted(
                Counter(
                    "lt32k"
                    if row["full_chat_tokens"] < 32768
                    else "32k"
                    if row["full_chat_tokens"] < 65536
                    else "64k"
                    if row["full_chat_tokens"] < 131072
                    else "128k"
                    for row in indices
                ).items()
            )
        ),
        "rejection_reasons": dict(
            sorted(Counter(row["reason"] for row in rejected).items())
        ),
        "files_sha256": {name: _sha(output_dir / name) for name in payloads},
        "train_ready": False,
    }
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    output = output_dir / "manifest.json"
    if verify_only:
        if output.read_text() != content:
            raise ValueError("P106 native manifest replay drift")
    else:
        output.write_text(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(base._dump(run(args.config, args.output_dir, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
