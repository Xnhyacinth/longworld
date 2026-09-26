"""Scale isolated P114 one-step policy over distinct executed P86 states.

This is a simulated policy candidate, not reader QA or a multi-turn trajectory.
Only new task states enter P128; the P114 q0/base decisions remain excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis import p86_state_shared_world as state
from longworld.synthesis.unified_candidate_contract import (
    AdapterBinding,
    CandidateLedger,
    NativeCandidate,
    normalize_native_candidate,
)
from scripts import p114_controlled_action_feedback as prior
from scripts.audit_unified_reader_mask import audit_reader
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p128-action-state-scale.v1"
OUTPUT_SCHEMA = "longworld.p128-action-state-scale-output.v3"


def _dump(value: Any) -> str:
    return prior.dump(value)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _state_task(base: dict[str, Any], variant: str) -> dict[str, Any]:
    task = {**base, "question": {**base["question"]}}
    query = task["question"]
    if variant == "effective_plus_one":
        query["asof"] = (
            date.fromisoformat(query["asof"]) + timedelta(days=1)
        ).isoformat()
    elif variant == "disclosure_plus_one":
        query["known_at"] = (
            date.fromisoformat(query["known_at"]) + timedelta(days=1)
        ).isoformat()
    elif variant != "base":
        raise ValueError("unsupported state variant")
    return task


def _execute_state(
    context: str,
    parsed: tuple[list[Any], list[dict[str, Any]]],
    task: dict[str, Any],
) -> tuple[int, list[str], str]:
    answer, consumed = state._state_result(*parsed, task["question"])
    task["consumed"] = consumed
    active_query = {**task["question"], "operation": "asof_complete_set"}
    active, _ = state._state_result(*parsed, active_query)
    if state.solve_visible(context, task) != answer:
        raise ValueError("state differs from visible-text executor")
    # Exact active IDs, not merely a coincidental equal sum or a different date label.
    signature = _sha_text(_dump(active["record_ids"]))
    return answer["sum"], active["record_ids"], signature


def _compile_action(
    world: dict[str, Any],
    world_path: Path,
    receipt_path: Path,
    context: str,
    task: dict[str, Any],
    variant: str,
    balance: int,
    active_ids: list[str],
    signature: str,
    sign: int,
    config: dict[str, Any],
    tokenizer: Any,
) -> dict[str, Any]:
    delta = config["action_delta"]
    witness_options: list[tuple[str, int]] = []
    margin = 0
    for candidate_margin in config["target_margins"]:
        target = balance + sign * candidate_margin
        witness_options = prior._flipping_facts(
            context, task, sign, balance, target, delta
        )
        if witness_options:
            margin = candidate_margin
            break
    if not witness_options:
        raise ValueError("no_visible_policy_flipping_intervention")
    target = balance + sign * margin
    choice, outcomes = prior.decision(balance, target, delta)
    if choice != ("ADD_500" if sign > 0 else "REMOVE_500"):
        raise ValueError("action_target_direction_mismatch")
    menu = prior.ACTIONS if world["seed"] % 4 in {0, 1} else prior.ACTIONS[::-1]
    question = prior._question(task, target, delta, menu)
    sample_id = (
        f"p128:{world['world_id']}:{task['task_id']}:{variant}:"
        f"{'above' if sign > 0 else 'below'}"
    )
    reader = {
        "sample_id": sample_id,
        "messages": [
            {"role": "user", "content": context + prior.MARKER + question},
            {"role": "assistant", "content": _dump({"action": choice})},
        ],
    }
    encoded = tokenize_assistant_only(
        tokenizer, reader["messages"], config["max_full_chat_tokens"]
    )
    ids, labels = encoded["input_ids"], encoded["labels"]
    supervised = sum(label != -100 for label in labels)
    input_tokens = len(ids) - supervised
    if not supervised or labels != [-100] * input_tokens + ids[input_tokens:]:
        raise ValueError("assistant_only_mask_mismatch")
    positions, query_start = prior._positions(
        context, reader["messages"], tokenizer, ids
    )
    candidates = sorted(
        (
            (query_start - positions[fact_id][1], fact_id, changed)
            for fact_id, changed in witness_options
        ),
        reverse=True,
    )
    gap, witness_id, changed_balance = candidates[0]
    if gap < config["minimum_decisive_to_query_tokens"]:
        raise ValueError("insufficient_bounded_witness_gap")
    changed_choice, changed_outcomes = prior.decision(changed_balance, target, delta)
    if changed_choice == choice:
        raise ValueError("visible_intervention_does_not_flip_action")
    intervention_kind, removed = prior._intervention_scope(context, witness_id)
    split = "eval" if world["seed"] % 5 == 0 else "train"
    native = {
        "sample_id": sample_id,
        "semantic_task_id": sample_id,
        "source_group": world["world_id"],
        "source_kind": "controlled_agentic_simulation",
        "domain": "simulated_state",
        "topic": "one_step_action_feedback",
        "operation": "choose_action_after_asof_state",
        "split": split,
        "full_chat_tokens": len(ids),
        "input_tokens": input_tokens,
        "supervised_tokens": supervised,
        "context_sha256": _sha_text(context),
        "dependency_status": (
            f"visible_{intervention_kind}_deletion_flips_policy_action;"
            "bounded_decisive_gap"
        ),
        "evidence_status": "source_state_solver_and_two_action_outcomes_replayed",
    }
    binding = AdapterBinding(
        source_kind=native["source_kind"],
        source_group=world["world_id"],
        domain=native["domain"],
        topic=native["topic"],
        operation=native["operation"],
        evidence_profile="controlled_action_conditioned_transition",
        tokenizer_profile="pinned-chat-template",
        receipt_path=receipt_path,
        receipt_sha256=prior.sha(receipt_path),
    )
    candidate = normalize_native_candidate(
        native, reader, binding, context_text=context
    )
    mask = audit_reader(
        reader, candidate.to_dict(), tokenizer, config["max_full_chat_tokens"]
    )
    proof = {
        "sample_id": sample_id,
        "world_id": world["world_id"],
        "source_world_sha256": prior.sha(world_path),
        "source_task_id": task["task_id"],
        "state_variant": variant,
        "active_state_signature": signature,
        "active_record_ids": active_ids,
        "observed_state": {
            "balance": balance,
            "asof": task["question"]["asof"],
            "known_at": task["question"]["known_at"],
        },
        "target": target,
        "available_actions": list(menu),
        "chosen_action": choice,
        "action_outcomes": outcomes,
        "decisive_fact_id": witness_id,
        "intervention_kind": intervention_kind,
        "intervention_removed_fact_ids": removed,
        "decisive_fact_token_span": list(positions[witness_id]),
        "query_start_token": query_start,
        "decisive_to_query_tokens": gap,
        "reader_minus_state_balance": changed_balance,
        "reader_minus_best_action": changed_choice,
        "reader_minus_action_outcomes": changed_outcomes,
        "bounded_scope": "selected visible support deletion; alternative supports and global shortest proof unsearched",
    }
    return {
        "reader": reader,
        "candidate": candidate.to_dict(),
        "proof": proof,
        "mask": mask,
    }


def _compile_world(
    args: tuple[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    world_path_text, config = args
    world_path = Path(world_path_text)
    receipt_path = world_path.parent / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    if prior.sha(world_path) != receipt["world_sha256"]:
        raise ValueError("source world differs from pinned receipt")
    world = json.loads(world_path.read_text())
    if (
        world["world_id"] != receipt["world_id"]
        or world["seed"] != receipt["job"]["seed"]
    ):
        raise ValueError("source world identity differs from pinned receipt")
    context = world["reader_context"]
    parsed = state._parse(context)
    tokenizer = prior._worker_tokenizer()
    results: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    # Reserve the actual P114 active state, not just its q0/base task label.
    prior_task = next(
        task for task in world["tasks"] if task["task_id"] == "q0:asof_sum"
    )
    _, _, prior_signature = _execute_state(
        context, parsed, _state_task(prior_task, "base")
    )
    seen: set[str] = {prior_signature}
    for source_task_id in config["source_task_ids"]:
        source = next(
            task for task in world["tasks"] if task["task_id"] == source_task_id
        )
        if state.solve_visible(context, source) != source["answer"]:
            raise ValueError("source as-of task does not replay")
        for variant in config["state_variants"]:
            row = {
                "world_id": world["world_id"],
                "source_task_id": source_task_id,
                "state_variant": variant,
            }
            if source_task_id == "q0:asof_sum" and variant == "base":
                ledger.append(
                    {**row, "status": "rejected", "reason": "existing_p114_state"}
                )
                continue
            task = _state_task(source, variant)
            try:
                balance, active_ids, signature = _execute_state(context, parsed, task)
                if signature in seen:
                    ledger.append(
                        {
                            **row,
                            "status": "rejected",
                            "reason": "duplicate_active_state",
                            "active_state_signature": signature,
                        }
                    )
                    continue
                for sign in (1, -1):
                    results.append(
                        _compile_action(
                            world,
                            world_path,
                            receipt_path,
                            context,
                            task,
                            variant,
                            balance,
                            active_ids,
                            signature,
                            sign,
                            config,
                            tokenizer,
                        )
                    )
            except ValueError as exc:
                # Do not admit one label of a paired state when its counterpart fails.
                results = [
                    item
                    for item in results
                    if not item["proof"]["sample_id"].startswith(
                        f"p128:{world['world_id']}:{source_task_id}:{variant}:"
                    )
                ]
                ledger.append({**row, "status": "rejected", "reason": str(exc)})
                continue
            seen.add(signature)
            ledger.append(
                {
                    **row,
                    "status": "accepted",
                    "active_state_signature": signature,
                    "balance": balance,
                }
            )
    return results, ledger


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if (
        config.get("schema_version") != SCHEMA
        or config.get("source_task_ids") != ["q0:asof_sum", "q1:asof_sum"]
        or config.get("state_variants")
        != ["base", "effective_plus_one", "disclosure_plus_one"]
        or config.get("exclude_prior_q0_base") is not True
        or config.get("action_delta") != 500
        or config.get("target_margins") != [250, 150]
        or not 1 <= config.get("workers", 0) <= 4
        or config.get("minimum_decisive_to_query_tokens", 0) < 16384
        or config.get("max_full_chat_tokens", 0) > 262144
    ):
        raise ValueError("invalid P128 policy scaling config")
    return config


def compile_batch(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = _config(config_path)
    compiler_sha256 = prior.sha(Path(__file__))
    if verify_only:
        frozen = json.loads((output / "manifest.json").read_text())
        if frozen.get("compiler_sha256") != compiler_sha256:
            raise ValueError("frozen P128 compiler SHA-256 differs")
    base = ROOT / config["source_base"]
    if prior.sha(base / "manifest.json") != config["source_manifest_sha256"]:
        raise ValueError("source base manifest changed")
    prior_path = ROOT / config["prior_policy_manifest"]
    if prior.sha(prior_path) != config["prior_policy_manifest_sha256"]:
        raise ValueError("P114 prior policy manifest changed")
    prior_manifest = json.loads(prior_path.read_text())
    prior_index = prior_path.parent / "sample_index.jsonl"
    if prior.sha(prior_index) != prior_manifest["files_sha256"]["sample_index.jsonl"]:
        raise ValueError("P114 source-world index differs from pinned manifest")
    prior_world_ids = {
        json.loads(line)["source_group"]
        for line in prior_index.read_text().splitlines()
    }
    source = json.loads((base / "manifest.json").read_text())
    paths = sorted((base / "shards").glob("*/world.json"))
    if len(paths) != source["accepted_jobs"]:
        raise ValueError("source world count differs")
    source_world_ids = {json.loads(path.read_text())["world_id"] for path in paths}
    if source_world_ids != prior_world_ids:
        raise ValueError("P128 worlds differ from prior controlled policy worlds")
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        batches = list(
            pool.map(_compile_world, ((str(path), config) for path in paths))
        )
    compiled = sorted(
        (item for items, _ in batches for item in items),
        key=lambda item: item["candidate"]["sample_id"],
    )
    rejected = sorted(
        (row for _, rows in batches for row in rows),
        key=lambda row: (row["world_id"], row["source_task_id"], row["state_variant"]),
    )
    ledger = CandidateLedger()
    streams = {
        name: []
        for name in (
            "policy_train.jsonl",
            "policy_eval.jsonl",
            "sample_index.jsonl",
            "proofs.jsonl",
            "mask_audit.jsonl",
            "state_ledger.jsonl",
        )
    }
    splits = Counter()
    actions = Counter()
    lengths = Counter()
    for item in compiled:
        row = item["candidate"]
        ledger.add(NativeCandidate(**row))
        split = row["split"]
        file_name = f"policy_{split}.jsonl"
        streams[file_name].append(_dump(item["reader"]) + "\n")
        streams["sample_index.jsonl"].append(
            _dump(
                {
                    **row,
                    "source_name": "p128_action_state_scale",
                    "native_row_ref": item["proof"]["world_id"],
                    "output_file": file_name,
                    "row_index": splits[split],
                }
            )
            + "\n"
        )
        streams["proofs.jsonl"].append(_dump(item["proof"]) + "\n")
        streams["mask_audit.jsonl"].append(_dump(item["mask"]) + "\n")
        splits[split] += 1
        actions[item["proof"]["chosen_action"]] += 1
        lengths[row["length_bin"]] += 1
    streams["state_ledger.jsonl"] = [_dump(row) + "\n" for row in rejected]
    groups: dict[str, set[str]] = defaultdict(set)
    pairs: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in compiled:
        candidate, proof = item["candidate"], item["proof"]
        groups[candidate["source_group"]].add(candidate["split"])
        pairs[
            (proof["world_id"], proof["source_task_id"], proof["state_variant"])
        ].append(proof)
    if any(len(values) != 1 for values in groups.values()):
        raise ValueError("source world crosses split")
    if any(
        len(values) != 2
        or {proof["chosen_action"] for proof in values} != set(prior.ACTIONS)
        or len({proof["active_state_signature"] for proof in values}) != 1
        or len({tuple(proof["available_actions"]) for proof in values}) != 1
        for values in pairs.values()
    ):
        raise ValueError("one fixed state must have both opposite actions")
    if len(
        {
            (proof["world_id"], proof["active_state_signature"])
            for item in compiled
            for proof in [item["proof"]]
        }
    ) != len(pairs):
        raise ValueError("duplicate state signature across task pairs")
    if actions["ADD_500"] != actions["REMOVE_500"]:
        raise ValueError("unbalanced opposite action labels")
    first_correct = sum(
        item["proof"]["available_actions"][0] == item["proof"]["chosen_action"]
        for item in compiled
    )
    if first_correct * 2 != len(compiled):
        raise ValueError("first-menu prior exceeds paired-state 50 percent")
    accepted_states = sum(row["status"] == "accepted" for row in rejected)
    if accepted_states != len(pairs) or len(rejected) != 6 * len(paths):
        raise ValueError("state attempt ledger incomplete")
    manifest = {
        "schema_version": OUTPUT_SCHEMA,
        "compiler_sha256": compiler_sha256,
        "config_sha256": prior.sha(config_path),
        "source_manifest_sha256": config["source_manifest_sha256"],
        "prior_policy_manifest_sha256": config["prior_policy_manifest_sha256"],
        "training_contract": "isolated_simulated_one_step_policy_action_candidate",
        "base_worlds": len(paths),
        "source_world_ids": sorted(source_world_ids),
        "source_qa_world_base": config["source_base"],
        "shared_source_worlds_with_prior_p114_policy": len(
            source_world_ids & prior_world_ids
        ),
        "gross_state_attempts": len(rejected),
        "accepted_distinct_states": accepted_states,
        "rejected_state_attempts": len(rejected) - accepted_states,
        "rejection_reasons": dict(
            sorted(
                Counter(
                    row["reason"] for row in rejected if row["status"] == "rejected"
                ).items()
            )
        ),
        "independent_policy_tasks": ledger.independent_semantic_tasks,
        "policy_views": len(compiled),
        "split_views": dict(sorted(splits.items())),
        "source_world_split_overlap": 0,
        "actions": dict(sorted(actions.items())),
        "choose_first_without_history_correct": first_correct,
        "state_only_prior_correct": len(pairs),
        "target_only_threshold_probe": prior._no_history_probe(compiled),
        "physical_length_bins": dict(sorted(lengths.items())),
        "full_chat_tokens": sum(
            item["candidate"]["full_chat_tokens"] for item in compiled
        ),
        "supervised_tokens": sum(
            item["candidate"]["supervised_tokens"] for item in compiled
        ),
        "minimum_decisive_to_query_tokens": min(
            item["proof"]["decisive_to_query_tokens"] for item in compiled
        ),
        "maximum_decisive_to_query_tokens": max(
            item["proof"]["decisive_to_query_tokens"] for item in compiled
        ),
        "files_sha256": {
            name: _sha_text("".join(lines)) for name, lines in streams.items()
        },
        "claim_limit": "one simulated ledger/action rule over 24 worlds; state pairs share observations; bounded visible deletion, no global shortest proof, no multi-turn or model gain",
        "train_ready": False,
    }
    streams["manifest.json"] = [_dump(manifest) + "\n"]
    if verify_only:
        for name, lines in streams.items():
            if (output / name).read_text() != "".join(lines):
                raise ValueError(f"frozen P128 artifact differs: {name}")
    else:
        if output.exists():
            raise ValueError("P128 output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="p128-policy-", dir=output.parent
        ) as raw:
            temp = Path(raw)
            for name, lines in streams.items():
                (temp / name).write_text("".join(lines))
            os.rename(temp, output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(compile_batch(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
