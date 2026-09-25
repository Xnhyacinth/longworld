"""Keep only new P99-proven PR pairs from a pinned expanded CodeForge bank."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.unified_candidate_contract import (
    CandidateLedger,
    NativeCandidate,
)
from longworld.synthesis.unified_candidate_merge import verify_merge
from scripts.p99_code_content_tasks import canonical

SCHEMA = "longworld.p107-code-curation.v1"
OUTPUTS = (
    "candidate_train.jsonl",
    "candidate_eval.jsonl",
    "sample_index.jsonl",
    "quality_ledger.jsonl",
    "manifest.json",
)


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pin(value: dict) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("P107 source pin requires path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("P107 source pin must be workspace-relative")
    path = ROOT / relative
    if not path.is_file() or _sha(path) != value["sha256"]:
        raise ValueError(f"P107 source pin drift: {relative}")
    return path


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"P107 row file missing: {path}")
    return [json.loads(line) for line in path.read_text().splitlines()]


def _verify_files(directory: Path, hashes: dict) -> None:
    for name, expected in hashes.items():
        relative = Path(name)
        if relative.name != name or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("P107 receipt file name is unsafe")
        if _sha(directory / name) != expected:
            raise ValueError(f"P107 receipt file changed: {name}")


def _pair(audit: dict) -> tuple[str, tuple[int, int]]:
    prs = audit["source_prs"]
    if len(prs) != 2 or len(set(prs)) != 2 or any(type(pr) is not int for pr in prs):
        raise ValueError("P107 code witness must name two distinct PRs")
    return audit["source_group"], tuple(sorted(prs))


def _evidence(audit: dict, index: dict) -> None:
    evidence = audit["evidence"]
    if (
        len(evidence) != 2
        or {row["pull_request"] for row in evidence} != set(audit["source_prs"])
        or not audit["filename_preserving_added_code_removal_changes_answer"]
        or not audit["all_selected_identifiers_absent_after_added_code_removal"]
        or audit["evidence_token_span"] <= 16384
        or audit["evidence_token_span"] != index["observed_witness_span_tokens"]
        or len(audit["answer"]) != 2
    ):
        raise ValueError("P107 added-line content certificate differs")


def _prior_status(
    semantic_task_id: str,
    pair: tuple[str, tuple[int, int]],
    old_ids: set[str],
    old_pairs: set[tuple[str, tuple[int, int]]],
) -> str:
    old_id = semantic_task_id in old_ids
    old_pair = pair in old_pairs
    if old_id != old_pair:
        raise ValueError("P107 prior semantic ID and PR pair exposure disagree")
    return "rejected_prior_pr_pair" if old_id else "accepted_new_pr_pair"


def build(config_path: Path) -> dict[str, str]:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config":
        raise ValueError("wrong P107 code curation schema")
    native_config_path = _pin(config["native_config"])
    prior_path = _pin(config["prior_native_manifest"])
    native_path = _pin(config["raw_native_manifest"])
    unified_path = _pin(config["raw_unified_manifest"])
    raw_mask_path = _pin(config["raw_all_mask_manifest"])
    native_config = json.loads(native_config_path.read_text())
    prior = json.loads(prior_path.read_text())
    native = json.loads(native_path.read_text())
    unified = verify_merge(unified_path.parent)
    raw_mask = json.loads(raw_mask_path.read_text())
    if (
        native_config.get("schema_version") != "longworld.p99-code-content.v1"
        or native.get("schema_version") != "longworld.p99-code-content.v1"
        or native["config_sha256"] != _sha(native_config_path)
        or prior.get("schema_version") != "longworld.p99-code-content.v1"
        or unified["native_manifest_sha256"] != _sha(native_path)
        or raw_mask["source_manifest_sha256"] != _sha(unified_path)
        or raw_mask["audited_views"] != unified["candidate_views"]
        or native["views"] != unified["candidate_views"]
    ):
        raise ValueError("P107 native, unified and all-mask chain differs")
    _verify_files(prior_path.parent, prior["files_sha256"])
    _verify_files(native_path.parent, native["files_sha256"])
    prior_index = _rows(prior_path.parent / "sample_index.jsonl")
    prior_audit = _rows(prior_path.parent / "audit.jsonl")
    old_ids = {row["semantic_task_id"] for row in prior_index}
    old_pairs = {_pair(row) for row in prior_audit}
    if len(old_ids) != len(prior_index) or len(old_pairs) != len(prior_audit):
        raise ValueError("P107 prior semantic tasks or PR pairs repeat")
    native_index = _rows(native_path.parent / "sample_index.jsonl")
    native_audit = _rows(native_path.parent / "audit.jsonl")
    by_id = {row["sample_id"]: row for row in native_index}
    audits = {row["sample_id"]: row for row in native_audit}
    index = _rows(unified_path.parent / "sample_index.jsonl")
    readers = {
        split: _rows(unified_path.parent / f"candidate_{split}.jsonl")
        for split in ("train", "eval")
    }
    if len(by_id) != len(index) or len(audits) != len(index):
        raise ValueError("P107 raw sample/index/audit inventory differs")
    ledger = []
    output_rows = {"train": [], "eval": []}
    output_index = []
    candidate_ledger = CandidateLedger()
    seen_pairs = set()
    for item in index:
        sample_id, split = item["sample_id"], item["split"]
        source = by_id[sample_id]
        audit = audits[sample_id]
        reader = readers[split][item["row_index"]]
        pair = _pair(audit)
        _evidence(audit, item)
        if (
            reader["sample_id"] != sample_id
            or source["semantic_task_id"] != item["semantic_task_id"]
            or source["source_group"] != item["source_group"]
            or item["source_group"] != pair[0]
            or source["split"] != split
            or source["full_chat_tokens"] != item["full_chat_tokens"]
            or source["assistant_tokens"] != item["supervised_tokens"]
            or reader["messages"][1]["content"] != canonical(audit["answer"])
        ):
            raise ValueError("P107 native and unified reader disagree")
        if pair in seen_pairs:
            raise ValueError("P107 raw PR pair repeats")
        seen_pairs.add(pair)
        status = _prior_status(item["semantic_task_id"], pair, old_ids, old_pairs)
        ledger.append(
            {
                "sample_id": sample_id,
                "semantic_task_id": item["semantic_task_id"],
                "source_group": pair[0],
                "source_prs": list(pair[1]),
                "split": split,
                "evidence_token_span": audit["evidence_token_span"],
                "status": status,
            }
        )
        if status == "rejected_prior_pr_pair":
            continue
        candidate = NativeCandidate(
            **{name: item[name] for name in NativeCandidate.__dataclass_fields__}
        )
        candidate_ledger.add(candidate)
        output_index.append(
            {
                **item,
                "row_index": len(output_rows[split]),
                "output_file": f"candidate_{split}.jsonl",
            }
        )
        output_rows[split].append(reader)
    if not output_index:
        raise ValueError("P107 no new content-proven PR pairs")
    outputs = {
        f"candidate_{split}.jsonl": "".join(canonical(row) + "\n" for row in rows)
        for split, rows in output_rows.items()
    }
    outputs["sample_index.jsonl"] = "".join(
        canonical(row) + "\n" for row in output_index
    )
    outputs["quality_ledger.jsonl"] = "".join(canonical(row) + "\n" for row in ledger)
    manifest = {
        "schema_version": "longworld.unified-candidates.v1",
        "curation_schema": SCHEMA,
        "config_sha256": _sha(config_path),
        "prior_native_manifest_sha256": _sha(prior_path),
        "raw_native_manifest_sha256": _sha(native_path),
        "raw_unified_manifest_sha256": _sha(unified_path),
        "raw_all_mask_manifest_sha256": _sha(raw_mask_path),
        "gross_reader_views": len(index),
        "rejected_prior_pr_pairs": sum(
            row["status"] != "accepted_new_pr_pair" for row in ledger
        ),
        "candidate_views": candidate_ledger.rows,
        "source_scoped_semantic_tasks": candidate_ledger.independent_tasks,
        "independent_semantic_tasks": candidate_ledger.independent_semantic_tasks,
        "views_by_lane": {"p107_code_content": candidate_ledger.rows},
        "splits": dict(sorted(Counter(row["split"] for row in output_index).items())),
        "length_bins": dict(
            sorted(Counter(row["length_bin"] for row in output_index).items())
        ),
        "min_evidence_span_tokens": min(
            row["evidence_token_span"]
            for row in ledger
            if row["status"] == "accepted_new_pr_pair"
        ),
        "max_evidence_span_tokens": max(
            row["evidence_token_span"]
            for row in ledger
            if row["status"] == "accepted_new_pr_pair"
        ),
        "quality_ledger_sha256": hashlib.sha256(
            outputs["quality_ledger.jsonl"].encode()
        ).hexdigest(),
        "files_sha256": {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in outputs.items()
            if name != "quality_ledger.jsonl"
        },
        "train_ready": False,
        "strict_long_dependency_verified": False,
    }
    outputs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return outputs


def run(config_path: Path, output_dir: Path, *, verify_only: bool = False) -> dict:
    outputs = build(config_path)
    if verify_only:
        if not output_dir.is_dir() or {p.name for p in output_dir.iterdir()} != set(
            OUTPUTS
        ):
            raise ValueError("P107 curated output inventory drift")
        for name, content in outputs.items():
            if (output_dir / name).read_text() != content:
                raise ValueError(f"P107 curated replay drift: {name}")
    else:
        if output_dir.exists():
            raise ValueError("P107 curated output must be new")
        output_dir.mkdir(parents=True)
        for name, content in outputs.items():
            (output_dir / name).write_text(content)
    return verify_merge(output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(canonical(run(args.config, args.output_dir, verify_only=args.verify_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
