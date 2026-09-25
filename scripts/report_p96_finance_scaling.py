"""Compare bounded and wide P96 task sampling with explicit novelty limits."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p95_report_finance_shared import canonical
from scripts.run_p96_finance_factorial import sha


def _ids(batch_dir: Path) -> set[str]:
    return {
        json.loads(line)["semantic_task_id"]
        for path in batch_dir.glob("*/sample_index.jsonl")
        for line in path.open()
    }


def report(
    narrow_dir: Path,
    wide_dir: Path,
    wide_config: Path,
    output: Path,
    *,
    verify_only: bool = False,
) -> dict:
    narrow = json.loads((narrow_dir / "batch_manifest.json").read_text())
    wide = json.loads((wide_dir / "batch_manifest.json").read_text())
    config = json.loads(wide_config.read_text())
    if (
        narrow.get("schema") != "longworld.p96-finance-factorial.v1.batch"
        or wide.get("schema") != "longworld.p96-finance-factorial.v1.batch"
        or wide["config_sha256"] != sha(wide_config)
        or narrow["catalog_sha256"] != wide["catalog_sha256"]
        or narrow["prior_candidate_index"] != wide["prior_candidate_index"]
    ):
        raise ValueError(
            "P96 bounded and wide batches use different sources or prior index"
        )
    narrow_ids, wide_ids = _ids(narrow_dir), _ids(wide_dir)
    if not narrow_ids <= wide_ids or len(wide_ids) != wide["semantic_tasks"]:
        raise ValueError("P96 narrow task set is not contained in wide batch")
    statuses, family, target, split = Counter(), Counter(), Counter(), Counter()
    buckets = Counter()
    per_source = []
    for job in wide["jobs"]:
        native = wide_dir / job["issuer"]
        manifest_path = native / "manifest.json"
        if job["manifest_sha256"] != sha(manifest_path):
            raise ValueError("P96 wide issuer receipt changed")
        manifest = json.loads(manifest_path.read_text())
        source_status = Counter()
        for line in (native / "support_matrix.jsonl").open():
            cell = json.loads(line)
            statuses[cell["status"]] += 1
            source_status[cell["status"]] += 1
            if cell["status"] == "selected":
                kind = cell["selector"]["kind"]
                family[kind] += 1
                target[cell["target_metric"]] += 1
                buckets[
                    (job["issuer"], cell["target_metric"], cell["selected_year"])
                ] += 1
        if source_status["selected"] != manifest["semantic_tasks"]:
            raise ValueError("P96 source matrix and reader tasks differ")
        split[manifest["split"]] += manifest["semantic_tasks"]
        per_source.append(
            {
                "issuer": job["issuer"],
                "split": manifest["split"],
                "source_group": manifest["source_group"],
                "semantic_tasks": manifest["semantic_tasks"],
                "views": manifest["views"],
                "matrix_statuses": dict(sorted(source_status.items())),
                "selected_families": manifest["planning"]["selected_families"],
                "selected_targets": manifest["planning"]["selected_targets"],
            }
        )
    cap = config["answer_bucket_cap"]
    if any(count > cap for count in buckets.values()):
        raise ValueError("P96 answer concentration cap violated")
    result = {
        "schema": "longworld.p96-finance-scaling-report.v1",
        "narrow_batch_manifest_sha256": sha(narrow_dir / "batch_manifest.json"),
        "wide_batch_manifest_sha256": sha(wide_dir / "batch_manifest.json"),
        "wide_config_sha256": sha(wide_config),
        "source_groups": len(per_source),
        "world_task_cap_requested": config["max_tasks_per_world"],
        "world_task_bound_from_answer_buckets": 5 * 4 * cap,
        "narrow_semantic_tasks": len(narrow_ids),
        "wide_semantic_tasks": len(wide_ids),
        "wide_marginal_over_narrow": len(wide_ids - narrow_ids),
        "wide_exact_prior_semantic_id_overlap": wide["exact_prior_semantic_id_overlap"],
        "known_prior_program_equivalent_cells_blocked": statuses[
            "blocked_prior_equivalent"
        ],
        "matrix_statuses": dict(sorted(statuses.items())),
        "answer_bucket_occupancy": dict(sorted(Counter(buckets.values()).items())),
        "occupied_answer_buckets": len(buckets),
        "selected_selector_families": dict(sorted(family.items())),
        "selected_target_metrics": dict(sorted(target.items())),
        "split_semantic_tasks": dict(sorted(split.items())),
        "per_source": per_source,
        "novelty_scope": "exact prior semantic IDs plus five declared P64/P95 equivalent programs; other semantic equivalences not exhausted",
        "train_ready": False,
    }
    payload = canonical(result) + "\n"
    if verify_only:
        if not output.is_file() or output.read_text() != payload:
            raise ValueError("P96 scaling report drift")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    return {key: value for key, value in result.items() if key != "per_source"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--narrow", type=Path, required=True)
    parser.add_argument("--wide", type=Path, required=True)
    parser.add_argument("--wide-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        canonical(
            report(
                args.narrow,
                args.wide,
                args.wide_config,
                args.output,
                verify_only=args.verify_only,
            )
        )
    )


if __name__ == "__main__":
    main()
