"""Bounded, frozen GovInfo transition/key screening without moving background."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.govinfodisposition import govinfo_chronology
from longworld.core.pack import SEP
from reports import p52_govinfo_bill_disposition_pipeline as p52


def modified_keys(config, state):
    source = {
        key: value
        for (stage, key), value in state["records"].items()
        if stage == config["from_stage"]
    }
    target = {
        key: value
        for (stage, key), value in state["records"].items()
        if stage == config["to_stage"]
    }
    dispositions = p52.cross_schema_dispositions(
        source, target, shingle_size=5, threshold=0.9
    )
    return sorted(key for key, value in dispositions.items() if value == p52.MODIFIED)


def state_for_keys(config, inventory):
    state = deepcopy(inventory)
    essential = {
        (stage, key)
        for key in config["requested_keys"]
        for stage in (config["from_stage"], config["to_stage"])
    }
    shingles = [
        p52.p49._shingles(state["records"][key]["canonical"], 5)
        for key in sorted(essential)
    ]
    state["fillers"] = [
        record
        for record in state["fillers"]
        if (record["stage"], record["base_key"]) not in essential
        and not any(
            p52.p49._jaccard(p52.p49._shingles(record["canonical"], 5), value) >= 0.9
            for value in shingles
        )
    ]
    return state


def frozen_combinations(config, state, tokenizer):
    """Choose by CF length and structural keys, never by artifact hashes/windows."""
    keys = modified_keys(config, state)
    if len(keys) < 2:
        return [], []
    deltas = []
    for key in keys:
        source = p52._section_entry(
            state["records"][(config["from_stage"], key)],
            workflow_id=config["workflow_id"],
            essential=True,
        )
        target = p52._section_entry(
            state["records"][(config["to_stage"], key)],
            workflow_id=config["workflow_id"],
            essential=True,
        )
        values = [source, target]
        cf = p52._registered_cf_entries(
            values,
            cf_target_id=target["artifact_id"],
            cf_source_id=source["artifact_id"],
        )
        full = p52._ordered(values, "full")
        delta = p52._token_count(
            tokenizer, SEP.join(item["document"] for item in cf)
        ) - p52._token_count(tokenizer, SEP.join(item["document"] for item in full))
        deltas.append({"key": key, "required_only_shared_cf_delta": delta})
    anchors = sorted(
        deltas,
        key=lambda item: (abs(item["required_only_shared_cf_delta"]), item["key"]),
    )[:3]
    partners = list(
        dict.fromkeys(
            keys[index]
            for index in [
                0,
                (len(keys) - 1) // 3,
                2 * (len(keys) - 1) // 3,
                len(keys) - 1,
            ]
        )
    )
    combinations = []
    for anchor in anchors:
        for partner in partners:
            if partner == anchor["key"]:
                continue
            requested = [partner, anchor["key"]]
            requested.extend(key for key in keys if key not in requested)
            combinations.append(requested[:6])
    return combinations[:12], deltas


def screen_pack(config, state, tokenizer, bucket, prior_ids):
    entries, question, requests, essential, cf_source, cf_target = p52._pack_bucket(
        config,
        state,
        tokenizer,
        bucket=bucket,
        prior_ids=prior_ids,
        registered_counterfactual=True,
    )
    views = {
        "full": p52._ordered(entries.values(), "full"),
        "cf": p52._registered_cf_entries(
            entries.values(), cf_target_id=cf_target, cf_source_id=cf_source
        ),
    }
    chronology = govinfo_chronology(
        [(item["classification"], item["document"]) for item in views["full"]]
    )
    views["ordered_artifact_view"] = [
        {"artifact_id": cls["artifact_id"], "classification": cls, "document": doc}
        for _, cls, doc in chronology
    ]
    result = {
        "bucket": bucket,
        "artifact_count": len(entries),
        "essential_artifact_ids": essential,
        "views": {},
        "status": "SCREEN_PASS_SHARED_AUDIT_PENDING",
    }
    for name, values in views.items():
        docs, prompt = p52._context(question, values)
        row = {
            "question": question,
            "query_timing": "first",
            "requested_dispositions": requests,
            "oracle_shingle_size": 5,
            "oracle_threshold": 0.9,
            "artifact_classification": [item["classification"] for item in values],
            "document_context": docs,
            "context": prompt,
        }
        ids = [item["artifact_id"] for item in values]
        gold = p52.replay_candidate(row, ids)
        indices = [ids.index(item) for item in essential]
        start, stop = min(indices), max(indices) + 1
        span_text = SEP.join(item["document"] for item in values[start:stop])
        span_tokens = p52._token_count(tokenizer, span_text)
        count = p52._token_count(tokenizer, prompt)
        lower, upper = config["length_buckets"][bucket]
        if not lower <= count <= upper:
            raise p52.P52Blocker(
                f"shared {name} serialized exact-band failure: {count}"
            )
        missing_proofs = [
            p52.replay_candidate(
                row, [artifact_id for artifact_id in ids if artifact_id != missing]
            )
            != gold
            for missing in essential
        ]
        if not all(missing_proofs):
            raise p52.P52Blocker("remove-one essential artifact did not change replay")
        proof = p52.replay_candidate(row, ids[start:stop])
        rejecting_windows = [
            limit
            for limit in config["shortcut_windows"]
            if span_tokens <= limit < count and proof == gold
        ]
        positions = []
        for index in indices:
            prefix = SEP.join(item["document"] for item in values[:index])
            positions.append(
                {
                    "artifact_index": index,
                    "prefix_tokens": p52._token_count(tokenizer, prefix)
                    if prefix
                    else 0,
                    "artifact_tokens": p52._token_count(
                        tokenizer, values[index]["document"]
                    ),
                }
            )
        result["views"][name] = {
            "exact_tokens": count,
            "minimal_complete_proof_artifact_span": [start, stop],
            "proof_span_tokens": span_tokens,
            "essential_positions": positions,
            "gold": gold,
            "remove_one_pass": True,
            "rejecting_artifact_window_limits": rejecting_windows,
            "raw_token_window_gate": "NOT_RUN",
        }
        if rejecting_windows:
            result["status"] = "REJECT_ARTIFACT_WINDOW"
    if result["views"]["full"]["gold"] == result["views"]["cf"]["gold"]:
        raise p52.P52Blocker("shared counterfactual does not change answer")
    return result, set(entries)


def add_growth_diagnostics(result):
    """Describe necessary proof growth separately from background/context growth."""
    previous = None
    for pack in result["packs"]:
        views = pack.get("views", {})
        full = views.get("full")
        if full is None:
            continue
        current = {
            "requested_dispositions": len(full["gold"]),
            "essential_artifact_count": len(pack["essential_artifact_ids"]),
            "essential_document_exact_token_sum": sum(
                item["artifact_tokens"] for item in full["essential_positions"]
            ),
            "token_scope": "sum of individually tokenized whole essential documents; excludes separators and question",
            "shared_proof_growth_gate": "NOT_RUN",
        }
        if previous is not None:
            current["necessary_event_delta"] = (
                current["essential_artifact_count"]
                - previous["essential_artifact_count"]
            )
            current["essential_document_token_delta"] = (
                current["essential_document_exact_token_sum"]
                - previous["essential_document_exact_token_sum"]
            )
            current["diagnostic"] = (
                "NO_NEW_NECESSARY_EVENTS"
                if current["necessary_event_delta"] <= 0
                else "NECESSARY_EVENTS_INCREASED_SHARED_GATE_PENDING"
            )
        pack["proof_growth_diagnostic"] = current
        previous = current
    return result


def run(plan_path, output):
    if output.exists():
        raise p52.P52Blocker("screen output already exists")
    plan = json.loads(plan_path.read_text())
    base, _, preflight = p52._load_config(Path(plan["base_generation_config"]))
    tokenizer = p52._load_tokenizer(base)
    output.mkdir(parents=True)
    all_trials = []
    states = {}
    discovery = []
    for transition in plan["transitions"]:
        config = deepcopy(base)
        config.update(transition)
        config["requested_keys"] = []
        state = p52._verified_source_state(config, preflight)
        states[config["bill_id"]] = state
        combinations, deltas = frozen_combinations(config, state, tokenizer)
        discovery.append(
            {
                "bill_id": config["bill_id"],
                "modified_pair_count": len(deltas),
                "source_bundle_sha256": state["source_bundle_sha256"],
                "length_deltas": deltas,
            }
        )
        for index, requested in enumerate(combinations, 1):
            trial = deepcopy(config)
            slug = f"hr{config['bill_id'].split('-')[-1]}-eas-eah-{index:02d}"
            trial["world_id"] = f"govinfo-118-{slug}-20260906"
            trial["data_product"] = "p52_govinfo_geometry_screen_20260906"
            trial["requested_keys"] = requested
            trial["requested_key_count_by_bucket"] = {
                "32k": 2,
                "64k": min(4, len(requested)),
                "128k": len(requested),
            }
            trial["authorization"]["record_id"] = (
                f"p52-{slug}-registered-parent-20260906"
            )
            trial["authorization"]["scope"] = (
                f"frozen official GovInfo Bill Status and EAS/EAH/ENR/Public Law XML for {config['bill_id']}; EAS-to-EAH source-text tasks"
            )
            trial["authorization"]["reviewed_at"] = "2026-09-06T00:00:00Z"
            trial["output_dir"] = str(output / slug / "registered")
            all_trials.append({"trial_id": slug, "config": trial})
    freeze = {
        "schema_version": "longworld.p52-govinfo-geometry-freeze.v1",
        "plan_sha256": p52._sha256_bytes(plan_path.read_bytes()),
        "selection_rule": plan["selection_rule"],
        "discovery": discovery,
        "trials": all_trials,
        "train_ready": False,
    }
    p52._write_atomic(output / "FROZEN_TRIALS.json", p52._canonical_bytes(freeze))
    print(json.dumps({"stage": "frozen", "trials": len(all_trials)}), flush=True)
    results = []
    generated = []
    for trial in all_trials:
        config = trial["config"]
        state = state_for_keys(config, states[config["bill_id"]])
        result = {
            "trial_id": trial["trial_id"],
            "packs": [],
            "generated_parent_count": 0,
        }
        prior = set()
        passed = []
        for bucket in ("32k", "64k", "128k"):
            try:
                pack, prior = screen_pack(config, state, tokenizer, bucket, prior)
                result["packs"].append(pack)
                if pack["status"] != "SCREEN_PASS_SHARED_AUDIT_PENDING":
                    break
                passed.append(bucket)
            except p52.P52Blocker as error:
                result["packs"].append(
                    {"bucket": bucket, "status": "REJECT", "reason": str(error)}
                )
                break
        if passed:
            config = deepcopy(config)
            config["generation_buckets"] = passed
            path = output / trial["trial_id"] / "generation_config.json"
            p52._write_atomic(path, p52._canonical_bytes(config))
            parents, sidecar, source, receipt = p52.build_registered_parents(path)
            p52._write_registered_generation_output(
                Path(config["output_dir"]), parents, sidecar, source, receipt
            )
            result["generated_parent_count"] = len(parents)
            generated.append(str(path))
        results.append(add_growth_diagnostics(result))
        p52._write_atomic(
            output / "SCREEN_REPORT.json",
            p52._canonical_bytes(
                {
                    "schema_version": "longworld.p52-govinfo-geometry-screen.v1",
                    "frozen_trials_sha256": p52._sha256_bytes(
                        (output / "FROZEN_TRIALS.json").read_bytes()
                    ),
                    "results": results,
                    "generated_configs": generated,
                    "train_ready": False,
                    "inventory_delta": 0,
                    "shared_dense_raw_window_audit": "NOT_RUN",
                }
            ),
        )
        print(
            json.dumps(
                {
                    "trial": result["trial_id"],
                    "packs": [
                        {
                            "bucket": item["bucket"],
                            "status": item["status"],
                            "reason": item.get("reason"),
                        }
                        for item in result["packs"]
                    ],
                    "parents": result["generated_parent_count"],
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
