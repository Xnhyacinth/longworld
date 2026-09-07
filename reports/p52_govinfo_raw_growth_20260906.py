"""Gate two previously frozen GovInfo bases on shared raw windows before growth."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.govinfodisposition import (
    replay_govinfo_disposition,
    replay_govinfo_disposition_raw_slice,
)
from longworld.core.pack import SEP
from longworld.core.taskproof import TaskProofError, _raw_token_window_proof
from reports import p52_govinfo_bill_disposition_pipeline as p52
from reports import p52_govinfo_geometry_screen_20260906 as screen
from reports import p52_govinfo_semantic_growth_20260906 as growth
from scripts.project_task_candidate_views import project


def raw_precheck(candidate, tokenizer):
    artifact_ids = [
        item["artifact_id"] for item in candidate["artifact_classification"]
    ]
    documents = candidate["document_context"].split(SEP)
    result = {
        "view": candidate["view"],
        "query_id": candidate["query_id"],
        "candidate_sha256": p52.candidate_sha256(candidate),
        "scope": "direct shared raw-window function; unsigned diagnostic, not complete task proof or dense audit",
    }
    try:
        windows, evidence, spans = _raw_token_window_proof(
            artifact_ids=artifact_ids,
            documents=documents,
            offset_tokenizer=tokenizer,
            replay_raw_answer=lambda raw, left, right: (
                replay_govinfo_disposition_raw_slice(
                    candidate, raw, left_framed=left, right_framed=right
                )["answer"]
            ),
            replay_artifact_answer=lambda ids: replay_govinfo_disposition(
                candidate, ids
            )["answer"],
            expected_answer=candidate["answer"],
            expected_total_tokens=p52._token_count(
                tokenizer, candidate["document_context"]
            ),
            records_are_artifacts=True,
        )
    except TaskProofError as error:
        return {**result, "status": "REJECT", "error": str(error)}
    return {
        **result,
        "status": "RAW_PRECHECK_PASS_FULL_PROOF_PENDING"
        if evidence
        else "REJECT_NO_STRICT_RAW_EVIDENCE",
        "windows": windows,
        "spans": spans,
    }


def request_schedules(plan):
    bases = plan["base_configs"]
    schedules = (
        plan["request_schedules"]
        if "request_schedules" in plan
        else [plan["request_counts"] for _ in bases]
    )
    if (
        len(bases) != 2
        or len(schedules) != 2
        or any(
            not isinstance(counts, list)
            or len(counts) != 3
            or any(
                not isinstance(count, int) or isinstance(count, bool)
                for count in counts
            )
            or not 2 <= counts[0] < counts[1] < counts[2]
            for counts in schedules
        )
    ):
        raise p52.P52Blocker("raw-growth requires two predeclared increasing schedules")
    return schedules


def run(plan_path, output):
    if output.exists():
        raise p52.P52Blocker("raw-growth output already exists")
    plan = json.loads(plan_path.read_text())
    schedules = request_schedules(plan)
    base, _, preflight = p52._load_config(Path(plan["base_configs"][0]))
    tokenizer = p52._load_tokenizer(base)
    discovery = deepcopy(base)
    discovery["requested_keys"] = []
    inventory = p52._verified_source_state(discovery, preflight)
    available = screen.modified_keys(discovery, inventory)
    trials = []
    for base_path, counts in zip(plan["base_configs"], schedules, strict=True):
        config, raw, _ = p52._load_config(Path(base_path))
        if (config["bill_id"], config["from_stage"], config["to_stage"]) != (
            base["bill_id"],
            base["from_stage"],
            base["to_stage"],
        ):
            raise p52.P52Blocker("raw-growth base transition differs")
        base_keys = config["requested_keys"][:2]
        if any(key not in available for key in base_keys):
            raise p52.P52Blocker("raw-growth base endpoints are not modified")
        suffix = Path(base_path).parent.name.rsplit("-", 1)[-1]
        slug = (
            "hr4366-eas-eah-necessary-" + "-".join(map(str, counts))
            if "request_schedules" in plan
            else f"hr4366-eas-eah-rawgrowth-{suffix}"
        )
        if counts[-1] > len(available):
            raise p52.P52Blocker(
                "request schedule exceeds real modified-section capacity"
            )
        config["requested_keys"] = (
            base_keys + [key for key in available if key not in base_keys]
        )[: counts[-1]]
        config["requested_key_count_by_bucket"] = dict(
            zip(("32k", "64k", "128k"), counts, strict=True)
        )
        config["generation_buckets"] = ["32k", "64k", "128k"]
        config["data_product"] = plan.get(
            "data_product", "p52_govinfo_raw_growth_20260906"
        )
        config["world_id"] = f"govinfo-118-{slug}-20260906"
        config["authorization"]["record_id"] = f"p52-{slug}-registered-parent-20260906"
        config["output_dir"] = str(output / slug / "registered")
        trials.append(
            {
                "trial_id": slug,
                "base_config_sha256": p52._sha256_bytes(raw),
                "config": config,
            }
        )
    output.mkdir(parents=True)
    p52._write_atomic(
        output / "FROZEN_RAW_GROWTH_TRIALS.json",
        p52._canonical_bytes(
            {
                "schema_version": "longworld.p52-govinfo-raw-growth-freeze.v1",
                "plan_sha256": p52._sha256_bytes(plan_path.read_bytes()),
                "source_bundle_sha256": inventory["source_bundle_sha256"],
                "selection_rule": plan["selection_rule"],
                "trials": trials,
                **(
                    {"eligible_modified_keys": available}
                    if "request_schedules" in plan
                    else {}
                ),
            }
        ),
    )
    results = []
    for trial in trials:
        config = trial["config"]
        prefix = deepcopy(config)
        prefix["generation_buckets"] = ["32k"]
        trial_dir = output / trial["trial_id"]
        prefix["output_dir"] = str(trial_dir / "prefix_registered")
        prefix_config = trial_dir / "prefix_config.json"
        p52._write_atomic(prefix_config, p52._canonical_bytes(prefix))
        parents, sidecar, source, receipt = p52.build_registered_parents(prefix_config)
        p52._write_registered_generation_output(
            Path(prefix["output_dir"]), parents, sidecar, source, receipt
        )
        views_dir = trial_dir / "prefix_shared_views"
        manifest = project(
            Path(prefix["output_dir"]) / "parents.jsonl",
            Path(prefix["output_dir"]) / "TASK_REPLAY_SIDECAR.json",
            views_dir,
        )
        result = {
            "trial_id": trial["trial_id"],
            "prefix_parent_count": 1,
            "prefix_projection_count": manifest["projection_candidate_count"],
            "raw_checks": [],
            "full_parent_count": 0,
        }
        candidates = p52._read_jsonl(views_dir / "candidates.jsonl")
        for candidate in candidates:
            check = raw_precheck(candidate, tokenizer)
            result["raw_checks"].append(check)
            p52._write_atomic(
                views_dir / "RAW_WINDOW_PRECHECK.json",
                p52._canonical_bytes(
                    {
                        "schema_version": "longworld.p52-shared-raw-window-precheck.v1",
                        "candidate_file_sha256": p52._sha256_bytes(
                            (views_dir / "candidates.jsonl").read_bytes()
                        ),
                        "checks": result["raw_checks"],
                        "train_ready": False,
                        "inventory_delta": 0,
                    }
                ),
            )
            print(
                json.dumps(
                    {
                        "trial": trial["trial_id"],
                        "view": check["view"],
                        "status": check["status"],
                        "error": check.get("error"),
                    }
                ),
                flush=True,
            )
        if len(result["raw_checks"]) == 3 and all(
            check["status"] == "RAW_PRECHECK_PASS_FULL_PROOF_PENDING"
            for check in result["raw_checks"]
        ):
            config_path = trial_dir / "generation_config.json"
            p52._write_atomic(config_path, p52._canonical_bytes(config))
            full, sidecar, source, receipt = p52.build_registered_parents(config_path)
            if full[0]["context"] != parents[0]["context"]:
                raise p52.P52Blocker(
                    "three-band rebuild changed the raw-checked 32K context"
                )
            measures = [growth.measure_growth_row(parent, tokenizer) for parent in full]
            result["measurements"] = measures
            result["growth_checks"] = growth.assess_growth(measures)
            if all(
                check["status"] == "PRECHECK_PASS_SHARED_GATES_PENDING"
                for check in result["growth_checks"]
            ):
                p52._write_registered_generation_output(
                    Path(config["output_dir"]), full, sidecar, source, receipt
                )
                result["full_parent_count"] = len(full)
        results.append(result)
        p52._write_atomic(
            output / "RAW_GROWTH_REPORT.json",
            p52._canonical_bytes(
                {
                    "schema_version": "longworld.p52-govinfo-raw-growth-report.v1",
                    "frozen_trials_sha256": p52._sha256_bytes(
                        (output / "FROZEN_RAW_GROWTH_TRIALS.json").read_bytes()
                    ),
                    "results": results,
                    "inventory_delta": 0,
                    "train_ready": False,
                    "shared_complete_task_proof_dense_selection_promotion": "NOT_RUN",
                }
            ),
        )
        print(
            json.dumps(
                {
                    "trial": trial["trial_id"],
                    "full_parents": result["full_parent_count"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan, args.output_dir)
