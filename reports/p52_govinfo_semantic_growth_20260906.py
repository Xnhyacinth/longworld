"""Freeze and test actual GovInfo answer/proof growth before shared auditing."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.govinfodisposition import (
    govinfo_chronology,
    materialize_govinfo_counterfactual,
)
from longworld.core.pack import SEP, wrap_prompt
from reports import p52_govinfo_bill_disposition_pipeline as p52
from reports import p52_govinfo_geometry_screen_20260906 as screen


def measure_growth_row(parent, tokenizer):
    artifacts = list(
        zip(
            parent["artifact_classification"],
            parent["document_context"].split(SEP),
            strict=True,
        )
    )
    views = {
        "full": artifacts,
        "cf": materialize_govinfo_counterfactual(parent, artifacts),
        "ordered_artifact_view": [
            (cls, doc) for _, cls, doc in govinfo_chronology(artifacts)
        ],
    }
    output = {}
    for view, values in views.items():
        by_id = {cls["artifact_id"]: doc for cls, doc in values}
        proof = SEP.join(
            by_id[artifact_id] for artifact_id in parent["essential_artifact_ids"]
        )
        context = wrap_prompt(
            parent["question"],
            SEP.join(doc for _, doc in values),
            parent["query_timing"],
        )
        output[view] = {
            "context_tokens": p52._token_count(tokenizer, context),
            "proof_tokens": p52._token_count(tokenizer, proof),
            "strict_support_event_count": len(parent["requested_dispositions"]),
            "essential_artifact_count": len(parent["essential_artifact_ids"]),
        }
    return {"bucket": parent["length_bucket"], "views": output}


def assess_growth(measurements):
    checks = []
    for before, after in pairwise(measurements):
        for view in before["views"]:
            left, right = before["views"][view], after["views"][view]
            context_growth = max(1, right["context_tokens"] - left["context_tokens"])
            proof_growth = right["proof_tokens"] - left["proof_tokens"]
            support_growth = (
                right["strict_support_event_count"] - left["strict_support_event_count"]
            )
            minimum = max(256, (context_growth + 19) // 20)
            checks.append(
                {
                    "from": before["bucket"],
                    "to": after["bucket"],
                    "view": view,
                    "context_growth": context_growth,
                    "proof_growth": proof_growth,
                    "minimum_proof_growth": minimum,
                    "strict_support_growth": support_growth,
                    "status": "PRECHECK_PASS_SHARED_GATES_PENDING"
                    if proof_growth >= minimum and support_growth > 0
                    else "REJECT_SUBSTANTIAL_PROOF_GROWTH",
                }
            )
    return checks


def precheck_previous(path, tokenizer):
    results = []
    for parent_path in sorted(path.glob("*/registered/parents.jsonl")):
        parents = p52._read_jsonl(parent_path)
        measures = [measure_growth_row(row, tokenizer) for row in parents]
        results.append(
            {
                "trial_id": parent_path.parent.parent.name,
                "parent_file_sha256": p52._sha256_bytes(parent_path.read_bytes()),
                "measurements": measures,
                "checks": assess_growth(measures),
            }
        )
    return {
        "schema_version": "longworld.p52-govinfo-substantial-growth-precheck.v1",
        "proof_formula": "token_counter(SEP.join(essential_documents_in_declared_essential_id_order))",
        "threshold_formula": "max(256, (max(1, next_exact_context - previous_exact_context) + 19) // 20)",
        "shared_gate_executed": False,
        "results": results,
        "inventory_delta": 0,
    }


def run(plan_path, output):
    if output.exists():
        raise p52.P52Blocker("growth output already exists")
    plan = json.loads(plan_path.read_text())
    config, _, preflight = p52._load_config(Path(plan["base_generation_config"]))
    tokenizer = p52._load_tokenizer(config)
    output.mkdir(parents=True)
    previous = precheck_previous(Path(plan["previous_batch"]), tokenizer)
    p52._write_atomic(
        output / "PREVIOUS_BATCH_GROWTH_PRECHECK.json", p52._canonical_bytes(previous)
    )
    print(
        json.dumps(
            {
                "previous_checked": len(previous["results"]),
                "previous_rejected": sum(
                    any(
                        check["status"].startswith("REJECT")
                        for check in result["checks"]
                    )
                    for result in previous["results"]
                ),
            }
        ),
        flush=True,
    )
    discovery = deepcopy(config)
    discovery["requested_keys"] = []
    inventory = p52._verified_source_state(discovery, preflight)
    keys = screen.modified_keys(discovery, inventory)
    base_keys = config["requested_keys"][:2]
    if any(key not in keys for key in base_keys):
        raise p52.P52Blocker("frozen 32K task keys are no longer modified")
    keys = base_keys + [key for key in keys if key not in base_keys]
    trials = []
    for index, counts in enumerate(plan["request_counts"], 1):
        if counts[0] != 2 or not 2 < counts[1] < counts[2] <= len(keys):
            raise p52.P52Blocker("growth request counts are not a nested expansion")
        trial = deepcopy(config)
        trial["requested_keys"] = keys[: counts[-1]]
        trial["requested_key_count_by_bucket"] = dict(
            zip(("32k", "64k", "128k"), counts, strict=True)
        )
        trial["generation_buckets"] = ["32k", "64k", "128k"]
        slug = f"hr4366-eas-eah-growth-{index:02d}"
        trial["world_id"] = f"govinfo-118-{slug}-20260906"
        trial["data_product"] = "p52_govinfo_semantic_growth_20260906"
        trial["authorization"]["record_id"] = f"p52-{slug}-registered-parent-20260906"
        trial["output_dir"] = str(output / slug / "registered")
        trials.append({"trial_id": slug, "config": trial})
    if len(trials) > 3:
        raise p52.P52Blocker("growth batch exceeds three frozen trials")
    freeze = {
        "schema_version": "longworld.p52-govinfo-semantic-growth-freeze.v1",
        "plan_sha256": p52._sha256_bytes(plan_path.read_bytes()),
        "source_bundle_sha256": inventory["source_bundle_sha256"],
        "base_keys": base_keys,
        "selection_rule": plan["selection_rule"],
        "trials": trials,
    }
    p52._write_atomic(
        output / "FROZEN_GROWTH_TRIALS.json", p52._canonical_bytes(freeze)
    )
    results = []
    for trial in trials:
        trial_config = trial["config"]
        state = screen.state_for_keys(trial_config, inventory)
        prior = set()
        result = {
            "trial_id": trial["trial_id"],
            "packs": [],
            "measurements": [],
            "generated_parent_count": 0,
        }
        for bucket in ("32k", "64k", "128k"):
            try:
                geometry, next_prior = screen.screen_pack(
                    trial_config, state, tokenizer, bucket, prior
                )
                result["packs"].append(geometry)
                if geometry["status"] != "SCREEN_PASS_SHARED_AUDIT_PENDING":
                    break
                entries, question, requests, essential, cf_source, cf_target = (
                    p52._pack_bucket(
                        trial_config,
                        state,
                        tokenizer,
                        bucket=bucket,
                        prior_ids=prior,
                        registered_counterfactual=True,
                    )
                )
                prior = next_prior
                raw = p52._build_candidate(
                    trial_config,
                    tokenizer,
                    "0" * 64,
                    bucket=bucket,
                    entries=entries,
                    question=question,
                    requests=requests,
                    essential_ids=essential,
                    cf_source_id=cf_source,
                    cf_target_id=cf_target,
                    view="full",
                )
                source = json.loads(entries[cf_source]["document"])
                target = json.loads(entries[cf_target]["document"])
                raw["counterfactual_twin"] = {
                    "provenance_operation": "replace_target_with_authenticated_source_body",
                    "source_artifact_id": cf_source,
                    "target_artifact_id": cf_target,
                    "parent_value": {
                        field: target[field] for field in ("source_text", "oracle_text")
                    },
                    "value": {
                        field: source[field] for field in ("source_text", "oracle_text")
                    },
                }
                raw["task_replay_sidecar"] = {"sha256": "0" * 64}
                result["measurements"].append(measure_growth_row(raw, tokenizer))
            except p52.P52Blocker as error:
                result["packs"].append(
                    {"bucket": bucket, "status": "REJECT", "reason": str(error)}
                )
                break
        result["growth_checks"] = assess_growth(result["measurements"])
        if len(result["measurements"]) == 3 and all(
            check["status"] == "PRECHECK_PASS_SHARED_GATES_PENDING"
            for check in result["growth_checks"]
        ):
            config_path = output / trial["trial_id"] / "generation_config.json"
            p52._write_atomic(config_path, p52._canonical_bytes(trial_config))
            parents, sidecar, source_receipt, receipt = p52.build_registered_parents(
                config_path
            )
            actual = [measure_growth_row(parent, tokenizer) for parent in parents]
            if actual != result["measurements"]:
                raise p52.P52Blocker(
                    "fresh signed-parent growth measurements differ from screening"
                )
            p52._write_registered_generation_output(
                Path(trial_config["output_dir"]),
                parents,
                sidecar,
                source_receipt,
                receipt,
            )
            result["generated_parent_count"] = len(parents)
        results.append(result)
        report = {
            "schema_version": "longworld.p52-govinfo-semantic-growth-screen.v1",
            "frozen_trials_sha256": p52._sha256_bytes(
                (output / "FROZEN_GROWTH_TRIALS.json").read_bytes()
            ),
            "results": results,
            "shared_dense_raw_window_selection_promotion": "NOT_RUN",
            "inventory_delta": 0,
            "train_ready": False,
        }
        p52._write_atomic(output / "GROWTH_REPORT.json", p52._canonical_bytes(report))
        print(
            json.dumps(
                {
                    "trial": trial["trial_id"],
                    "parents": result["generated_parent_count"],
                    "checks": result["growth_checks"],
                    "pack_status": [pack["status"] for pack in result["packs"]],
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
