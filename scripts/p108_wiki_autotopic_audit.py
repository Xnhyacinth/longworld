"""Summarize gated Wiki task diversity, scoped evidence and final masks."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = "longworld.p108-wiki-autotopic-final-audit.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _quantiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"min": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p90": ordered[(9 * (len(ordered) - 1)) // 10],
        "max": ordered[-1],
    }


def summarize(campaign: Path, unified: Path, mask_dir: Path) -> dict:
    gate = json.loads((campaign / "gate_result.json").read_text())
    native = json.loads((campaign / "batch/result.json").read_text())
    native_mask = json.loads((campaign / "batch/merged/mask_audit.json").read_text())
    unified_manifest = json.loads((unified / "merged/manifest.json").read_text())
    mask = json.loads((mask_dir / "manifest.json").read_text())
    pool = json.loads((campaign / "gate/source_pool.json").read_text())
    rows = _rows(campaign / "batch/merged/sample_index.jsonl")
    if (
        gate["net_novel"]["groups"] != len(pool["sources"])
        or native["source_pool_entries"] != len(pool["sources"])
        or native["candidate_views"] != len(rows)
        or native["independent_tasks"] != len(rows)
        or native_mask["checked_rows"] != len(rows)
        or unified_manifest["candidate_views"] != len(rows)
        or mask["audited_views"] != len(rows)
        or native_mask["full_chat_tokens"] != mask["full_chat_tokens"]
        or native_mask["supervised_tokens"] != mask["supervised_tokens"]
    ):
        raise ValueError("P108 source/native/unified/mask inventories differ")
    split_by_group = {}
    ops_by_group = defaultdict(set)
    lengths, operations, statuses, mappings = Counter(), Counter(), Counter(), Counter()
    evidence_width, evidence_to_input_end = [], []
    long_examples = []
    for row in rows:
        group, split = row["source_group"], row["split"]
        if row["source_kind"] != "real_wiki" or (
            group in split_by_group and split_by_group[group] != split
        ):
            raise ValueError("P108 source kind or source-group split differs")
        split_by_group[group] = split
        if row["input_tokens"] + row["supervised_tokens"] != row["full_chat_tokens"]:
            raise ValueError("P108 final-chat token accounting differs")
        operation = row["task_type"]
        ops_by_group[group].add(operation)
        operations[operation] += 1
        lengths[row["length_bin"]] += 1
        statuses[str(row.get("dependency_status"))] += 1
        mappings[str(row.get("evidence_token_mapping"))] += 1
        span = row.get("observed_lineage_token_envelope")
        if isinstance(span, dict):
            start, end = span["start"], span["end"]
            if not (0 <= start <= end <= row["input_tokens"]):
                raise ValueError("P108 evidence span escapes final input")
            width, distance = end - start, row["input_tokens"] - end
            evidence_width.append(width)
            evidence_to_input_end.append(distance)
            if width >= 16384:
                long_examples.append(
                    {
                        "sample_id": row["example_id"],
                        "source_group": group,
                        "operation": operation,
                        "lineage_width_tokens": width,
                        "evidence_to_input_end_tokens": distance,
                    }
                )
    return {
        "schema": SCHEMA,
        "pins": {
            "gate_result_sha256": _sha(campaign / "gate_result.json"),
            "gated_pool_sha256": _sha(campaign / "gate/source_pool.json"),
            "native_result_sha256": _sha(campaign / "batch/result.json"),
            "native_index_sha256": _sha(campaign / "batch/merged/sample_index.jsonl"),
            "native_mask_sha256": _sha(campaign / "batch/merged/mask_audit.json"),
            "unified_manifest_sha256": _sha(unified / "merged/manifest.json"),
            "final_mask_sha256": _sha(mask_dir / "manifest.json"),
        },
        "gross": gate["gross"],
        "net_novel": gate["net_novel"],
        "overlap_groups": len(gate["rejected_groups"]),
        "native_supported_jobs": native["planned_jobs"],
        "native_jobs_with_candidates": native["jobs_with_candidates"],
        "native_unsupported_cells": native["unsupported_cells"],
        "native_rejected_rows": native["rejected_rows"],
        "independent_tasks": len(rows),
        "source_groups_with_tasks": len(split_by_group),
        "multi_operation_groups": sum(len(ops) > 1 for ops in ops_by_group.values()),
        "source_groups_by_operation_count": dict(
            sorted(Counter(len(ops) for ops in ops_by_group.values()).items())
        ),
        "operations": dict(sorted(operations.items())),
        "length_bins": dict(sorted(lengths.items())),
        "dependency_status": dict(sorted(statuses.items())),
        "evidence_token_mapping": dict(sorted(mappings.items())),
        "scoped_lineage_width_tokens": _quantiles(evidence_width),
        "evidence_to_input_end_tokens": _quantiles(evidence_to_input_end),
        "lineage_width_at_least_16k": sum(value >= 16384 for value in evidence_width),
        "lineage_width_at_least_32k": sum(value >= 32768 for value in evidence_width),
        "long_lineage_examples": sorted(
            long_examples, key=lambda row: -row["lineage_width_tokens"]
        )[:12],
        "final_chat_tokens": mask["full_chat_tokens"],
        "assistant_supervised_tokens": mask["supervised_tokens"],
        "train_ready": False,
        "scope": "observed selected lineage and tokenizer positions; not global shortest-proof or model learning certificate",
    }


def run(
    campaign: Path,
    unified: Path,
    mask_dir: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    result = summarize(campaign, unified, mask_dir)
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if verify_only:
        if output.read_text() != content:
            raise ValueError("P108 final audit replay differs")
    else:
        if output.exists():
            raise ValueError("P108 final audit output must be new")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--unified", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.campaign,
                args.unified,
                args.mask,
                args.output,
                verify_only=args.verify_only,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
