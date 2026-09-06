"""Freeze at most three mixed R/M GovInfo tasks before any packing or raw check."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from copy import deepcopy
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reports import p52_govinfo_bill_disposition_pipeline as p52
from reports import p52_govinfo_raw_growth_20260906 as raw_growth
from reports import p52_govinfo_semantic_growth_20260906 as growth
from scripts.project_task_candidate_views import project


def select_keys(pools, specification):
    """Seeded lexical-pool sampling; no artifact IDs, hashes or positions used."""
    counts = specification["request_counts"]
    seed = specification["seed"]
    retained = specification["retained_per_block"]
    if (
        not isinstance(seed, int)
        or isinstance(seed, bool)
        or not isinstance(counts, list)
        or len(counts) != 3
        or any(not isinstance(v, int) or isinstance(v, bool) for v in counts)
        or not 2 <= counts[0] < counts[1] < counts[2]
        or any(v % counts[0] for v in counts)
        or not isinstance(retained, int)
        or isinstance(retained, bool)
        or not 0 < retained < counts[0] - 1
    ):
        raise p52.P52Blocker("invalid frozen mixed-disposition schedule")
    rng = random.Random(seed)
    queues = {label: sorted(pools[label]) for label in (p52.RETAINED, p52.MODIFIED)}
    if set(queues[p52.RETAINED]) & set(queues[p52.MODIFIED]) or any(
        len(set(v)) != len(v) for v in queues.values()
    ):
        raise p52.P52Blocker("mixed source key pools overlap or duplicate")
    blocks = counts[-1] // counts[0]
    modified = counts[0] - retained
    if (
        len(queues[p52.RETAINED]) < blocks * retained
        or len(queues[p52.MODIFIED]) < blocks * modified
    ):
        raise p52.P52Blocker("insufficient authentic mixed source-key capacity")
    for values in queues.values():
        rng.shuffle(values)
    selected = []
    labels = []
    for block in range(blocks):
        members = [
            (key, p52.RETAINED)
            for key in queues[p52.RETAINED][block * retained : (block + 1) * retained]
        ]
        members += [
            (key, p52.MODIFIED)
            for key in queues[p52.MODIFIED][block * modified : (block + 1) * modified]
        ]
        rng.shuffle(members)
        selected.extend(key for key, _ in members)
        labels.extend(label for _, label in members)
    anchor = rng.choice(
        [i for i, label in enumerate(labels[: counts[0]]) if label == p52.MODIFIED]
    )
    return selected, labels, f"D{anchor + 1:02d}"


def baseline_scores(answer):
    truth = json.loads(answer)
    if not truth or not set(truth.values()) <= {p52.RETAINED, p52.MODIFIED}:
        raise p52.P52Blocker("mixed answer is empty or not R/M")
    scores = {}
    for name, predicted in (
        ("always_M", {key: p52.MODIFIED for key in truth}),
        ("always_R", {key: p52.RETAINED for key in truth}),
        (
            "fixed_D02_R_else_M",
            {key: p52.RETAINED if key == "D02" else p52.MODIFIED for key in truth},
        ),
    ):
        correct = sum(predicted[key] == value for key, value in truth.items())
        scores[name] = {
            "exact_match": predicted == truth,
            "correct_labels": correct,
            "total_labels": len(truth),
            "label_accuracy": correct / len(truth),
        }
    counts = Counter(truth.values())
    scores["label_counts"] = dict(sorted(counts.items()))
    scores["best_constant_label_accuracy"] = max(counts.values()) / len(truth)
    return scores


def pair_receipts(config, state, tokenizer):
    receipts = []
    texts = []
    canonical = []
    for key in config["requested_keys"]:
        left = state["records"][(config["from_stage"], key)]
        right = state["records"][(config["to_stage"], key)]
        text_pair = [left["text"], right["text"]]
        canonical_pair = [p52.p49._canonical(value) for value in text_pair]
        oracle_pair = [left["oracle_canonical"], right["oracle_canonical"]]
        texts.extend(text_pair)
        canonical.extend(canonical_pair)
        receipts.append(
            {
                "base_key": key,
                "whole_section_text_sha256": [p52._sha256_text(v) for v in text_pair],
                "whole_section_exact_equal": text_pair[0] == text_pair[1],
                "normalized_whole_section_exact_equal": canonical_pair[0]
                == canonical_pair[1],
                "oracle_normalized_exact_equal": oracle_pair[0] == oracle_pair[1],
                "whole_section_jaccard": p52.p49._jaccard(
                    *(p52.p49._shingles(v, 5) for v in canonical_pair)
                ),
                "oracle_jaccard": p52.p49._jaccard(
                    *(p52.p49._shingles(v, 5) for v in oracle_pair)
                ),
            }
        )
    return {
        "pairs": receipts,
        "endpoint_count": len(texts),
        "exact_unique_whole_section_text_count": len(set(texts)),
        "normalized_unique_whole_section_text_count": len(set(canonical)),
        "whole_section_token_sum_with_repeated_endpoints": sum(
            p52._token_count(tokenizer, v) for v in texts
        ),
        "exact_unique_whole_section_token_sum": sum(
            p52._token_count(tokenizer, v) for v in sorted(set(texts))
        ),
        "scope": "whole normalized source-section derivatives, not raw XML or candidate capacity; duplicate endpoints are not new unique text",
        "duplicate_gate_exceptions": False,
    }


def run(plan_path, output):
    if output.exists():
        raise p52.P52Blocker("mixed-disposition output already exists")
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    if plan.get("schema_version") != "longworld.p56-govinfo-mixed-disposition-plan.v1":
        raise p52.P52Blocker("mixed-disposition plan schema is invalid")
    tasks = plan["tasks"]
    if (
        not 1 <= len(tasks) <= 3
        or len({v["trial_id"] for v in tasks}) != len(tasks)
        or any(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", v["trial_id"]) is None
            for v in tasks
        )
    ):
        raise p52.P52Blocker(
            "mixed batch requires one to three unique frozen trial IDs"
        )
    base, _, preflight = p52._load_config(Path(plan["base_generation_config"]))
    if (base["bill_id"], base["from_stage"], base["to_stage"]) != (
        "118-HR-4366",
        "eas",
        "eah",
    ):
        raise p52.P52Blocker("mixed-disposition batch is outside the frozen transition")
    base["requested_disposition_policy"] = "mixed_retained_modified"
    discovery = deepcopy(base)
    discovery["requested_keys"] = []
    state = p52._verified_source_state(discovery, preflight)
    left = {
        key: value
        for (stage, key), value in state["records"].items()
        if stage == base["from_stage"]
    }
    right = {
        key: value
        for (stage, key), value in state["records"].items()
        if stage == base["to_stage"]
    }
    dispositions = p52.cross_schema_dispositions(
        left, right, shingle_size=5, threshold=0.9
    )
    pools = {
        label: sorted(key for key, value in dispositions.items() if value == label)
        for label in (p52.RETAINED, p52.MODIFIED)
    }
    trials = []
    for specification in tasks:
        keys, labels, anchor = select_keys(pools, specification)
        slug = specification["trial_id"]
        config = deepcopy(base)
        config["data_product"] = plan["data_product"]
        shared_world_id = str(plan.get("shared_world_id") or "")
        if shared_world_id:
            if (
                re.fullmatch(r"govinfo-[a-z0-9-]{8,120}", shared_world_id) is None
            ):
                raise p52.P52Blocker("shared mixed-disposition world id is invalid")
            config["world_id"] = shared_world_id
            config["task_instance_id"] = slug
        else:
            config["world_id"] = f"govinfo-118-hr4366-eas-eah-{slug}-20260906"
        config["requested_keys"] = keys
        config["requested_key_count_by_bucket"] = dict(
            zip(("32k", "64k", "128k"), specification["request_counts"], strict=True)
        )
        config["counterfactual_code"] = anchor
        config["generation_buckets"] = ["32k", "64k", "128k"]
        config["authorization"]["record_id"] = (
            f"p56-hr4366-eas-eah-{slug}-registered-parent-20260906"
        )
        config["authorization"]["scope"] = (
            "User-authorized mixed retained/modified whole-section EAS-to-EAH tasks; no duplicate/gate exceptions; local-probe only"
        )
        config["output_dir"] = str(output / slug / "registered")
        trials.append(
            {
                "specification": specification,
                "expected_factual_labels": labels,
                "config": config,
            }
        )
    output.mkdir(parents=True)
    freeze_path = output / "FROZEN_MIXED_TRIALS.json"
    p52._write_atomic(
        freeze_path,
        p52._canonical_bytes(
            {
                "schema_version": "longworld.p56-mixed-disposition-freeze.v1",
                "plan_sha256": p52._sha256_bytes(plan_raw),
                "source_bundle_sha256": state["source_bundle_sha256"],
                "eligible_pools": pools,
                "trials": trials,
                "selection_rule": plan["selection_rule"],
                "train_ready": False,
            }
        ),
    )
    print(
        json.dumps(
            {
                "stage": "frozen",
                "pool_sizes": {k: len(v) for k, v in pools.items()},
                "trials": len(trials),
                "cf_codes": [t["config"]["counterfactual_code"] for t in trials],
            }
        ),
        flush=True,
    )
    tokenizer = p52._load_tokenizer(base)
    results = []
    for trial in trials:
        config = trial["config"]
        slug = trial["specification"]["trial_id"]
        directory = output / slug
        result = {
            "trial_id": slug,
            "source_duplicate_diagnostics": pair_receipts(config, state, tokenizer),
            "prefix_parent_count": 0,
            "full_parent_count": 0,
            "raw_checks": [],
            "stage": "prefix_generation",
        }
        try:
            prefix = deepcopy(config)
            prefix["generation_buckets"] = ["32k"]
            prefix["output_dir"] = str(directory / "prefix_registered")
            config_path = directory / "prefix_config.json"
            p52._write_atomic(config_path, p52._canonical_bytes(prefix))
            parents, sidecar, source, receipt = p52.build_registered_parents(
                config_path
            )
            p52._write_registered_generation_output(
                Path(prefix["output_dir"]), parents, sidecar, source, receipt
            )
            result["prefix_parent_count"] = len(parents)
            result["stage"] = "prefix_projection"
            views = directory / "prefix_shared_views"
            manifest = project(
                Path(prefix["output_dir"]) / "parents.jsonl",
                Path(prefix["output_dir"]) / "TASK_REPLAY_SIDECAR.json",
                views,
            )
            result["prefix_projection_count"] = manifest["projection_candidate_count"]
            candidates = p52._read_jsonl(views / "candidates.jsonl")
            result["constant_baselines"] = [
                {"view": row["view"], "scores": baseline_scores(row["answer"])}
                for row in candidates
            ]
            result["stage"] = "prefix_raw_window"
            for row in candidates:
                check = raw_growth.raw_precheck(row, tokenizer)
                result["raw_checks"].append(check)
                p52._write_atomic(
                    views / "RAW_WINDOW_PRECHECK.json",
                    p52._canonical_bytes(
                        {
                            "checks": result["raw_checks"],
                            "train_ready": False,
                            "inventory_delta": 0,
                        }
                    ),
                )
                print(
                    json.dumps(
                        {
                            "trial": slug,
                            "view": check["view"],
                            "status": check["status"],
                            "error": check.get("error"),
                        }
                    ),
                    flush=True,
                )
            if len(result["raw_checks"]) != 3 or not all(
                check["status"] == "RAW_PRECHECK_PASS_FULL_PROOF_PENDING"
                for check in result["raw_checks"]
            ):
                result["status"] = "REJECT_RAW_WINDOW"
            else:
                result["stage"] = "full_growth_generation"
                full_config = directory / "generation_config.json"
                p52._write_atomic(full_config, p52._canonical_bytes(config))
                full, sidecar, source, receipt = p52.build_registered_parents(
                    full_config
                )
                if full[0]["context"] != parents[0]["context"]:
                    raise p52.P52Blocker(
                        "mixed final generation changed the raw-checked prefix"
                    )
                measures = [growth.measure_growth_row(row, tokenizer) for row in full]
                checks = growth.assess_growth(measures)
                result["measurements"] = measures
                result["growth_checks"] = checks
                if (
                    len(full) == 3
                    and len(checks) == 6
                    and all(
                        c["status"] == "PRECHECK_PASS_SHARED_GATES_PENDING"
                        for c in checks
                    )
                ):
                    p52._write_registered_generation_output(
                        Path(config["output_dir"]), full, sidecar, source, receipt
                    )
                    result["full_parent_count"] = len(full)
                    result["status"] = "SIGNED_PARENTS_SHARED_FULL_AUDIT_PENDING"
                else:
                    result["status"] = "REJECT_PROOF_GROWTH"
        except ValueError as error:
            result["status"] = "REJECT"
            result["error_type"] = type(error).__name__
            result["error"] = str(error)
        results.append(result)
        p52._write_atomic(
            output / "MIXED_REPORT.json",
            p52._canonical_bytes(
                {
                    "schema_version": "longworld.p56-mixed-disposition-report.v1",
                    "frozen_trials_sha256": p52._sha256_bytes(freeze_path.read_bytes()),
                    "results": results,
                    "train_ready": False,
                    "inventory_delta": 0,
                    "production_eligible": False,
                    "scope": "bounded source/oracle/raw diagnostics; trials are not distinct worlds or admitted training rows",
                }
            ),
        )
        print(
            json.dumps(
                {
                    "trial": slug,
                    "status": result["status"],
                    "stage": result["stage"],
                    "parents": result["full_parent_count"],
                    "error": result.get("error"),
                }
            ),
            flush=True,
        )


def summarize_existing(plan_path, output):
    """Measure the declared baselines without rerunning source/raw audits."""
    freeze = json.loads((output / "FROZEN_MIXED_TRIALS.json").read_text())
    if freeze["plan_sha256"] != p52._sha256_bytes(plan_path.read_bytes()):
        raise p52.P52Blocker("mixed baseline plan differs from frozen plan")
    observations = json.loads((output / "MIXED_REPORT.json").read_text())
    counts = {
        row["trial_id"]: row["full_parent_count"] for row in observations["results"]
    }
    rows = []
    answers = []
    aggregate = {
        name: {"exact_matches": 0, "correct_labels": 0, "total_labels": 0, "rows": 0}
        for name in ("always_M", "always_R", "fixed_D02_R_else_M")
    }
    for trial in freeze["trials"]:
        slug = trial["specification"]["trial_id"]
        if not counts.get(slug):
            continue
        directory = output / slug / "shared_views"
        raw = (directory / "candidates.jsonl").read_bytes()
        manifest = json.loads((directory / "MANIFEST.json").read_text())
        digest = p52._sha256_bytes(raw)
        if digest != manifest["projection_candidates_sha256"]:
            raise p52.P52Blocker("mixed baseline projection bytes changed")
        for row in p52._read_jsonl(directory / "candidates.jsonl"):
            answers.append(json.loads(row["answer"]))
            scores = baseline_scores(row["answer"])
            selected = {
                name: {
                    key: scores[name][key]
                    for key in ("exact_match", "correct_labels", "total_labels")
                }
                for name in aggregate
            }
            rows.append(
                {
                    "trial_id": slug,
                    "query_id": row["query_id"],
                    "view": row["view"],
                    "length_bucket": row["length_bucket"],
                    "actual_context_tokens": row["actual_context_tokens"],
                    "candidate_file_sha256": digest,
                    "scores": selected,
                }
            )
            for name, score in selected.items():
                aggregate[name]["exact_matches"] += int(score["exact_match"])
                aggregate[name]["correct_labels"] += score["correct_labels"]
                aggregate[name]["total_labels"] += score["total_labels"]
                aggregate[name]["rows"] += 1
    for value in aggregate.values():
        if not value["rows"] or not value["total_labels"]:
            raise p52.P52Blocker("no complete mixed projections to measure")
        value["label_accuracy"] = value["correct_labels"] / value["total_labels"]
        value["exact_match_rate"] = value["exact_matches"] / value["rows"]
    templates = []
    for period in range(1, 7):
        for symbols in product(("M", "R"), repeat=period):
            predictions = [
                {code: symbols[(int(code[1:]) - 1) % period] for code in answer}
                for answer in answers
            ]
            templates.append(
                {
                    "pattern": "".join(symbols),
                    "period": period,
                    "exact_matches": sum(
                        predicted == answer
                        for predicted, answer in zip(predictions, answers, strict=True)
                    ),
                    "correct_labels": sum(
                        predicted[code] == value
                        for predicted, answer in zip(predictions, answers, strict=True)
                        for code, value in answer.items()
                    ),
                    "total_labels": sum(len(answer) for answer in answers),
                    "rows": len(answers),
                }
            )
    best = max(
        templates,
        key=lambda item: (
            item["exact_matches"],
            item["correct_labels"],
            -item["period"],
        ),
    )
    return {
        "schema_version": "longworld.p56-mixed-constant-baselines.v1",
        "scope": "measured on signed projected candidate answer labels, not model evaluation; views and schedules are not independent worlds",
        "rows": rows,
        "aggregate": aggregate,
        "posthoc_periodic_baseline": {
            **best,
            "templates_tested": len(templates),
            "predeclared": False,
            "scope": "all binary M/R positional templates of periods 1..6, selected on these same rows; not a held-out model evaluation",
        },
        "train_ready": False,
        "inventory_delta": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summarize-existing", action="store_true")
    args = parser.parse_args()
    if args.summarize_existing:
        report = summarize_existing(args.plan, args.output_dir)
        Path(
            "reports/p56_govinfo_mixed_dispositions_constant_baselines_20260906.json"
        ).write_text(json.dumps(report, indent=2) + "\n")
    else:
        run(args.plan, args.output_dir)
