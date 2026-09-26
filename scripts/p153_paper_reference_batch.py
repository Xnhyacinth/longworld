"""Compile P146's verified TeX reference recipe over a new source cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    CandidateLedger,
    NativeCandidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p146_real_paper_reference import work_job

SCHEMA = "longworld.p153-paper-reference-batch.v1"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def pin(value: dict) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("P153 pin requires path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P153 pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or sha(path.read_bytes()) != value["sha256"]:
        raise ValueError(f"P153 pin drift: {relative}")
    return path


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def build(config_path: Path, output: Path) -> dict[str, bytes]:
    cfg = json.loads(config_path.read_text())
    if (
        cfg.get("schema") != SCHEMA + ".config"
        or ("fetch_manifest" in cfg) == ("prior_paper_manifest" in cfg)
        or type(cfg.get("workers")) is not int
        or not 1 <= cfg["workers"] <= 8
        or type(cfg.get("max_tasks_per_work")) is not int
        or not 1 <= cfg["max_tasks_per_work"] <= 8
        or type(cfg.get("min_unexposed_answer_words")) is not int
        or not 1 <= cfg["min_unexposed_answer_words"] <= 6
        or not 8192 <= cfg.get("min_support_gap_tokens", 0) <= 32768
        or not 16384
        <= cfg.get("min_full_chat_tokens", 0)
        < cfg.get("max_full_chat_tokens", 0)
        <= 262144
    ):
        raise ValueError("P153 reference config outside P146 gate")
    prior_path = pin(cfg["prior_refs"])
    previous = [row["candidate"] for row in jsonl(prior_path)]
    prior_tasks = {row["semantic_task_id"] for row in previous}
    prior_answers = {
        (row["source_group"], row["answer_sha256"])
        for row in previous
        if row["source_kind"].startswith("real_paper")
    }
    works = []
    if "fetch_manifest" in cfg:
        source_path = pin(cfg["fetch_manifest"])
        fetch = json.loads(source_path.read_text())
        if (
            fetch.get("schema") != "longworld.p105-paper-acquisition.v1.fetch-result"
            or fetch.get("status")
            not in {"complete", "complete_with_source_rejects", "transport_blocked", "archive_budget_exceeded"}
            or fetch.get("content_use") != "local_research_only_no_redistribution"
            or not fetch.get("source_receipts")
        ):
            raise ValueError("P153 requires a verified nonempty local-only source packet")
        for receipt in fetch["source_receipts"]:
            archive = receipt["source_archives"][-1]
            source = pin({key: archive[key] for key in ("path", "sha256")})
            if archive["version"] != receipt["versions"][-1] or source.stat().st_size == 0:
                raise ValueError("P153 latest source archive mismatch")
            works.append(
                {
                    "work_id": receipt["work_id"],
                    "source_group": "researchlab:arxiv:" + receipt["work_id"],
                    "split": receipt["split"],
                    "license_status": receipt["license_status"],
                    "source_archive": archive,
                    "category_query": receipt["query"],
                }
            )
        source_mode = "newly_frozen_p105_archives"
    else:
        manifest_path = pin(cfg["prior_paper_manifest"])
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("source_works") != 34:
            raise ValueError("P153 frozen paper inventory changed")
        source_path = manifest_path.parent / "support_matrix.jsonl"
        if sha(source_path.read_bytes()) != manifest["files_sha256"]["support_matrix.jsonl"]:
            raise ValueError("P153 frozen paper support matrix drift")
        works = [row for row in jsonl(source_path) if row["status"] == "source_parsed"]
        for work in works:
            pin({key: work["source_archive"][key] for key in ("path", "sha256")})
            work["category_query"] = "frozen_prior_inventory"
        source_mode = "reused_p127_archives"
    if len({work["work_id"] for work in works}) != len(works):
        raise ValueError("P153 source packet repeats a work")
    old_groups = {row["source_group"] for row in previous}
    if source_mode == "newly_frozen_p105_archives" and any(
        work["source_group"] in old_groups for work in works
    ):
        raise ValueError("P153 source work overlaps prior reader bank")
    args = [(work, cfg, str(source_path.relative_to(ROOT)), prior_answers) for work in works]
    with ProcessPoolExecutor(max_workers=cfg["workers"]) as pool:
        processed = list(pool.map(work_job, args))
    relative_output = output.relative_to(ROOT)
    support, decisions, proofs, masks, indices = [], [], [], [], []
    readers: dict[str, list[dict]] = {"train": [], "eval": []}
    ledger = CandidateLedger()
    categories = {work["work_id"]: work["category_query"] for work in works}
    for work_row, local_decisions, accepted in processed:
        work_row["category_query"] = categories[work_row["work_id"]]
        support.append(work_row)
        decisions.extend(local_decisions)
        for reader, index, proof, mask in accepted:
            if (
                index["semantic_task_id"] in prior_tasks
                or (index["source_group"], index["answer_sha256"]) in prior_answers
            ):
                raise ValueError("P153 candidate overlaps prior bank")
            ledger.add(
                NativeCandidate(
                    **{name: index[name] for name in NativeCandidate.__dataclass_fields__}
                )
            )
            split = index["split"]
            index["native_row_ref"] = str(relative_output / "audit.jsonl") + ":" + str(len(proofs))
            index.update(
                output_file=f"candidate_{split}.jsonl",
                row_index=len(readers[split]),
                source_name="p153_real_paper_source_reference",
            )
            proof["category_query"] = categories[work_row["work_id"]]
            readers[split].append(reader)
            indices.append(index)
            proofs.append(proof)
            masks.append(mask)
    files = {
        "candidate_train.jsonl": b"".join(dump(row) for row in readers["train"]),
        "candidate_eval.jsonl": b"".join(dump(row) for row in readers["eval"]),
        "sample_index.jsonl": b"".join(dump(row) for row in indices),
        "audit.jsonl": b"".join(dump(row) for row in proofs),
        "mask_rows.jsonl": b"".join(dump(row) for row in masks),
        "support_matrix.jsonl": b"".join(dump(row) for row in support),
        "decision_ledger.jsonl": b"".join(dump(row) for row in decisions),
    }
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "p153_schema": SCHEMA + ".result",
        "compiler_sha256": sha(Path(__file__).read_bytes()),
        "p146_compiler_sha256": sha((ROOT / "scripts/p146_real_paper_reference.py").read_bytes()),
        "config_sha256": sha(config_path.read_bytes()),
        "source_input_sha256": sha(source_path.read_bytes()),
        "source_mode": source_mode,
        "prior_candidate_refs_sha256": cfg["prior_refs"]["sha256"],
        "source_works": len(works),
        "source_parsed_works": sum(row["status"] == "source_parsed" for row in support),
        "official_source_categories": (
            len({row["category_query"] for row in support})
            if source_mode == "newly_frozen_p105_archives"
            else 0
        ),
        "accepted_worlds": len({row["source_group"] for row in indices}),
        "novel_source_groups": (
            len({row["source_group"] for row in indices} - old_groups)
            if source_mode == "newly_frozen_p105_archives"
            else 0
        ),
        "candidate_views": ledger.rows,
        "source_scoped_semantic_tasks": ledger.independent_tasks,
        "independent_semantic_tasks": ledger.independent_semantic_tasks,
        "splits": {split: len(rows) for split, rows in readers.items()},
        "views_by_lane": {"p153_real_paper_source_reference": ledger.rows},
        "decision_counts": dict(sorted(Counter(row["status"] for row in decisions).items())),
        "full_chat_tokens": sum(row["full_chat_tokens"] for row in indices),
        "supervised_tokens": sum(row["supervised_tokens"] for row in indices),
        "length_bins": dict(sorted(Counter(row["length_bin"] for row in indices).items())),
        "files_sha256": {name: sha(data) for name, data in files.items()},
        "redistribution_status": "local_research_only_no_redistribution",
        "claim_limit": "P146 unique TeX reference/target and dual reader-text deletion; bounded exact alternatives only",
        "train_ready": False,
    }
    files["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def run(config: Path, output: Path, verify_only: bool = False) -> dict:
    config = config if config.is_absolute() else ROOT / config
    output = output if output.is_absolute() else ROOT / output
    if verify_only:
        frozen = json.loads((output / "manifest.json").read_text())
        if frozen["compiler_sha256"] != sha(Path(__file__).read_bytes()):
            raise ValueError("P153 compiler changed since freeze")
        if frozen["p146_compiler_sha256"] != sha(
            (ROOT / "scripts/p146_real_paper_reference.py").read_bytes()
        ):
            raise ValueError("P146 source compiler changed since freeze")
    files = build(config, output)
    if verify_only:
        if {path.name for path in output.iterdir()} != set(files):
            raise ValueError("P153 reference inventory drift")
        for name, data in files.items():
            if (output / name).read_bytes() != data:
                raise ValueError(f"P153 reference replay drift: {name}")
    else:
        if output.exists():
            raise ValueError("P153 reference output must be new")
        output.mkdir(parents=True)
        for name, data in files.items():
            (output / name).write_bytes(data)
    return verify_merge(output) if json.loads(files["manifest.json"])["candidate_views"] else json.loads(files["manifest.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, args.verify_only), sort_keys=True))


if __name__ == "__main__":
    main()
