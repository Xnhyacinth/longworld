"""Compile an isolated simulated one-step policy pilot from P86 state worlds.

The reader sees the complete controlled ledger, action menu, transition and
reward rule. Executed outcomes and evidence interventions stay in sidecars.
This policy candidate is never inserted into reader-QA SFT automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from bisect import bisect_right
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from itertools import pairwise
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
from scripts.audit_unified_reader_mask import audit_reader
from scripts.run_shared_record_taskbank import _tokenizer
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p114-controlled-action-feedback.v1"
MARKER = "\n\nQUESTION\n"
ACTIONS = ("ADD_500", "REMOVE_500")


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decision(
    balance: int, target: int, delta: int
) -> tuple[str, dict[str, dict[str, int]]]:
    """Execute both permitted actions under one fixed observation."""
    if (
        type(balance) is not int
        or type(target) is not int
        or type(delta) is not int
        or delta < 1
    ):
        raise ValueError("invalid policy state or action delta")
    outcomes = {
        "ADD_500": {
            "next_balance": balance + delta,
            "feedback": -abs(balance + delta - target),
        },
        "REMOVE_500": {
            "next_balance": balance - delta,
            "feedback": -abs(balance - delta - target),
        },
    }
    if outcomes["ADD_500"]["feedback"] == outcomes["REMOVE_500"]["feedback"]:
        raise ValueError("policy action ties")
    chosen = max(ACTIONS, key=lambda action: outcomes[action]["feedback"])
    return chosen, outcomes


def _question(
    task: dict[str, Any], target: int, delta: int, menu: tuple[str, str]
) -> str:
    query = task["question"]
    filters = "; then ".join(
        "keep rows where "
        + " and ".join(
            f"{c['field']} {c['op']} {c['value']}" for c in step["conditions"]
        )
        for step in query["filters"]
    )
    return (
        "This is a simulated one-step decision. Apply the visible state rules to the record rows: "
        f"{filters}. Use effective cutoff {query['asof']} and disclosure cutoff "
        f"{query['known_at']}. The sum of active scoped record amounts is the current "
        f"balance. Available actions, in this order: {menu[0]}, {menu[1]}. "
        f"ADD_500 increases the balance by {delta}; REMOVE_500 decreases it by {delta}. "
        f"After the action, feedback is minus the absolute difference between the next balance "
        f"and target {target}. Choose the action with higher feedback. "
        'Return only one JSON object of the form {"action":"ACTION_ID"}.'
    )


def _drop(context: str, fact_id: str) -> str:
    return (
        state._drop_fact(context, fact_id)
        if fact_id.startswith("e")
        else state._drop_record_support(context, fact_id)
    )


def _flipping_facts(
    context: str, task: dict[str, Any], sign: int, balance: int, target: int, delta: int
) -> list[tuple[str, int]]:
    expected, _ = decision(balance, target, delta)
    fact_type = "e" if sign > 0 else "r"
    flipped = []
    for fact_id in task["consumed"]:
        if not fact_id.startswith(fact_type):
            continue
        try:
            changed = state.solve_visible(_drop(context, fact_id), task)["sum"]
        except ValueError:
            continue  # An invalid reduced world is not a dependency witness.
        alternate, _ = decision(changed, target, delta)
        if alternate != expected:
            flipped.append((fact_id, changed))
    return flipped


def _positions(
    context: str,
    messages: list[dict[str, str]],
    tokenizer: Any,
    expected_ids: list[int],
) -> tuple[dict[str, tuple[int, int]], int]:
    """Map exact final-chat offsets for facts and the question start."""
    rendered = _render_chat(tokenizer, messages, generation_prompt=False)
    start = rendered.find(context)
    if start < 0 or rendered.find(context, start + 1) >= 0:
        raise ValueError("context does not occur exactly once in rendered chat")
    encoded = tokenizer(rendered, truncation=False, return_offsets_mapping=True)
    if list(encoded["input_ids"]) != expected_ids:
        raise ValueError("offset tokenizer differs from exact assistant mask")
    question_char = start + len(context) + len(MARKER)
    offsets = encoded["offset_mapping"]
    question_token = next(
        (i for i, (left, right) in enumerate(offsets) if left <= question_char < right),
        None,
    )
    if question_token is None:
        question_token = next(
            (
                i
                for i, (left, right) in enumerate(offsets)
                if right > left >= question_char
            ),
            None,
        )
    if question_token is None:
        raise ValueError("question has no final-chat token position")
    lines = context.splitlines(keepends=True)
    line_starts = []
    cursor = 0
    for line in lines:
        line_starts.append(cursor)
        cursor += len(line)
    fact_ids = [None] + [json.loads(line)["id"] for line in lines[1:]]
    positions: dict[str, tuple[int, int]] = {}
    for index, (left, right) in enumerate(offsets):
        if right <= left or not start <= left < start + len(context):
            continue
        line_index = bisect_right(line_starts, left - start) - 1
        if line_index < 1:
            continue
        fact_id = fact_ids[line_index]
        prior = positions.get(fact_id)
        positions[fact_id] = (prior[0] if prior else index, index)
    if len(positions) != len(fact_ids) - 1:
        raise ValueError("one or more visible facts lack final-chat token position")
    return positions, question_token


@lru_cache(maxsize=1)
def _worker_tokenizer() -> Any:
    return _tokenizer()


def _compile_world(args: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    world_path_text, config = args
    world_path = Path(world_path_text)
    receipt_path = world_path.parent / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    if sha(world_path) != receipt["world_sha256"]:
        raise ValueError("source world differs from P86 job receipt")
    world = json.loads(world_path.read_text())
    if (
        world["world_id"] != receipt["world_id"]
        or world["seed"] != receipt["job"]["seed"]
    ):
        raise ValueError("source world identity differs from P86 receipt")
    context = world["reader_context"]
    task = next(task for task in world["tasks"] if task["task_id"] == "q0:asof_sum")
    balance = state.solve_visible(context, task)["sum"]
    if balance != task["answer"]["sum"]:
        raise ValueError("source as-of state does not replay from reader text")
    if config["action_delta"] != 500:
        raise ValueError("action ID and delta differ")
    menu = ACTIONS if world["seed"] % 4 in {0, 1} else ACTIONS[::-1]
    tokenizer = _worker_tokenizer()
    results = []
    for sign in (1, -1):
        margin = None
        flipping = []
        for candidate_margin in config["target_margins"]:
            target = balance + sign * candidate_margin
            flipping = _flipping_facts(
                context, task, sign, balance, target, config["action_delta"]
            )
            if flipping:
                margin = candidate_margin
                break
        if margin is None:
            raise ValueError("world has no action-flipping visible fact intervention")
        target = balance + sign * margin
        chosen, outcomes = decision(balance, target, config["action_delta"])
        if chosen != ("ADD_500" if sign > 0 else "REMOVE_500"):
            raise ValueError("policy action and target sign differ")
        question = _question(task, target, config["action_delta"], menu)
        messages = [
            {"role": "user", "content": context + MARKER + question},
            {"role": "assistant", "content": dump({"action": chosen})},
        ]
        encoded = tokenize_assistant_only(
            tokenizer, messages, config["max_full_chat_tokens"]
        )
        ids, labels = encoded["input_ids"], encoded["labels"]
        supervised = sum(label != -100 for label in labels)
        input_tokens = len(ids) - supervised
        if not supervised or labels != [-100] * input_tokens + ids[input_tokens:]:
            raise ValueError("assistant-only policy mask differs")
        positions, query_start = _positions(context, messages, tokenizer, ids)
        decisive = sorted(
            (
                (query_start - positions[fact_id][1], fact_id, changed)
                for fact_id, changed in flipping
            ),
            reverse=True,
        )
        gap, witness_id, changed_balance = decisive[0]
        if gap < config["minimum_decisive_to_query_tokens"]:
            raise ValueError("no long-distance action-flipping fact")
        after_action, changed_outcomes = decision(
            changed_balance, target, config["action_delta"]
        )
        if after_action == chosen:
            raise ValueError("selected visible fact does not flip policy")
        split = "eval" if world["seed"] % 5 == 0 else "train"
        sample_id = f"p114:{world['world_id']}:{'above' if sign > 0 else 'below'}"
        reader = {"sample_id": sample_id, "messages": messages}
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
            "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
            "dependency_status": "visible_fact_deletion_flips_policy_action;bounded_decisive_gap",
            "evidence_status": "source_state_solver_and_action_outcomes_replayed",
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
            receipt_sha256=sha(receipt_path),
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
            "source_world_sha256": sha(world_path),
            "source_task_id": task["task_id"],
            "observed_state": {
                "balance": balance,
                "asof": task["question"]["asof"],
                "known_at": task["question"]["known_at"],
            },
            "target": target,
            "available_actions": list(menu),
            "chosen_action": chosen,
            "action_outcomes": outcomes,
            "decisive_fact_id": witness_id,
            "decisive_fact_token_span": list(positions[witness_id]),
            "query_start_token": query_start,
            "decisive_to_query_tokens": gap,
            "reader_minus_state_balance": changed_balance,
            "reader_minus_best_action": after_action,
            "reader_minus_action_outcomes": changed_outcomes,
            "bounded_scope": "one chosen fact deletion flips action; alternative supports and global minimum proof unsearched",
        }
        results.append(
            {
                "reader": reader,
                "candidate": candidate.to_dict(),
                "proof": proof,
                "mask": mask,
            }
        )
    return results


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if (
        config.get("schema_version") != SCHEMA
        or config.get("split_rule") != "seed_mod5_zero_eval"
        or config.get("action_delta") != 500
        or config.get("target_margins") != [250, 150]
        or type(config.get("minimum_decisive_to_query_tokens")) is not int
        or config["minimum_decisive_to_query_tokens"] < 16384
        or type(config.get("max_full_chat_tokens")) is not int
        or config["max_full_chat_tokens"] > 262144
        or not 1 <= config.get("workers", 0) <= 4
    ):
        raise ValueError("invalid P114 pilot config")
    return config


def _no_history_probe(items: list[dict[str, Any]]) -> dict[str, Any]:
    """A bounded target-only threshold diagnostic, not a model shortcut proof."""

    def predict(target: int, threshold: float, direction: int) -> str:
        return "ADD_500" if (target >= threshold) == (direction > 0) else "REMOVE_500"

    train = [item for item in items if item["candidate"]["split"] == "train"]
    eval_rows = [item for item in items if item["candidate"]["split"] == "eval"]
    values = sorted({item["proof"]["target"] for item in train})
    if not values or not eval_rows:
        raise ValueError("target-only diagnostic needs train and eval rows")
    thresholds = [
        values[0] - 1,
        *((a + b) / 2 for a, b in pairwise(values)),
        values[-1] + 1,
    ]
    scored = []
    for threshold in thresholds:
        for direction in (1, -1):
            correct = sum(
                predict(item["proof"]["target"], threshold, direction)
                == item["proof"]["chosen_action"]
                for item in train
            )
            scored.append((correct, threshold, direction))
    best = min(scored, key=lambda result: (-result[0], result[1], -result[2]))
    eval_correct = sum(
        predict(item["proof"]["target"], best[1], best[2])
        == item["proof"]["chosen_action"]
        for item in eval_rows
    )
    return {
        "scope": "best_single_target_threshold_fit_on_train_then_eval;not_exhaustive_no_history_test",
        "train_correct": best[0],
        "train_rows": len(train),
        "eval_correct": eval_correct,
        "eval_rows": len(eval_rows),
        "threshold": best[1],
        "direction": "high_target_add" if best[2] > 0 else "low_target_add",
    }


def compile_pilot(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = _config(config_path)
    base = ROOT / config["source_base"]
    if sha(base / "manifest.json") != config["source_manifest_sha256"]:
        raise ValueError("source P86 batch manifest changed")
    source = json.loads((base / "manifest.json").read_text())
    world_paths = sorted((base / "shards").glob("*/world.json"))
    if len(world_paths) != source["accepted_jobs"]:
        raise ValueError("P86 source world count differs")
    jobs = [(str(path), config) for path in world_paths]
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        batches = list(pool.map(_compile_world, jobs))
    compiled = [item for batch in batches for item in batch]
    ledger = CandidateLedger()
    streams: dict[str, list[str]] = {
        name: []
        for name in (
            "policy_train.jsonl",
            "policy_eval.jsonl",
            "sample_index.jsonl",
            "proofs.jsonl",
            "mask_audit.jsonl",
        )
    }
    split_positions = Counter()
    lengths = Counter()
    actions = Counter()
    ordered = sorted(compiled, key=lambda item: item["candidate"]["sample_id"])
    for item in ordered:
        row = item["candidate"]
        ledger.add(NativeCandidate(**row))
        split = row["split"]
        output_file = f"policy_{split}.jsonl"
        row = {
            **row,
            "source_name": "p114_controlled_action_feedback",
            "native_row_ref": item["proof"]["world_id"],
            "output_file": output_file,
            "row_index": split_positions[split],
        }
        streams[output_file].append(dump(item["reader"]) + "\n")
        streams["sample_index.jsonl"].append(dump(row) + "\n")
        streams["proofs.jsonl"].append(dump(item["proof"]) + "\n")
        streams["mask_audit.jsonl"].append(dump(item["mask"]) + "\n")
        split_positions[split] += 1
        lengths[row["length_bin"]] += 1
        actions[item["proof"]["chosen_action"]] += 1
    if len(ordered) != 2 * len(world_paths) or ledger.independent_semantic_tasks != len(
        ordered
    ):
        raise ValueError("P114 world/task multiplicity differs")
    group_splits = {
        row["candidate"]["source_group"]: row["candidate"]["split"] for row in ordered
    }
    if len(group_splits) != len(world_paths):
        raise ValueError("P114 source worlds are not split atomically")
    if actions != {"ADD_500": len(world_paths), "REMOVE_500": len(world_paths)}:
        raise ValueError("P114 action labels are not balanced")
    menu_first = Counter(item["proof"]["available_actions"][0] for item in ordered)
    if menu_first != actions:
        raise ValueError("P114 action-menu order is not balanced")
    first_action_correct = sum(
        item["proof"]["available_actions"][0] == item["proof"]["chosen_action"]
        for item in ordered
    )
    if first_action_correct * 2 != len(ordered):
        raise ValueError("P114 first-menu shortcut is not balanced")
    manifest = {
        "schema_version": SCHEMA,
        "source_manifest_sha256": config["source_manifest_sha256"],
        "config_sha256": sha(config_path),
        "training_contract": "isolated_simulated_agent_policy_action_candidate",
        "base_worlds": len(world_paths),
        "independent_policy_tasks": ledger.independent_semantic_tasks,
        "policy_views": len(ordered),
        "split_views": dict(sorted(split_positions.items())),
        "source_group_split_overlap": 0,
        "actions": dict(sorted(actions.items())),
        "menu_first_actions": dict(sorted(menu_first.items())),
        "choose_first_without_history_correct": first_action_correct,
        "target_only_threshold_probe": _no_history_probe(ordered),
        "physical_length_bins": dict(sorted(lengths.items())),
        "full_chat_tokens": sum(
            item["candidate"]["full_chat_tokens"] for item in ordered
        ),
        "supervised_tokens": sum(
            item["candidate"]["supervised_tokens"] for item in ordered
        ),
        "minimum_decisive_to_query_tokens": min(
            item["proof"]["decisive_to_query_tokens"] for item in ordered
        ),
        "maximum_decisive_to_query_tokens": max(
            item["proof"]["decisive_to_query_tokens"] for item in ordered
        ),
        "files_sha256": {
            name: hashlib.sha256("".join(lines).encode()).hexdigest()
            for name, lines in streams.items()
        },
        "claim_limit": "simulated one-step action feedback; one deletion flips policy, no global shortest proof, no model gain",
        "train_ready": False,
    }
    streams["manifest.json"] = [dump(manifest) + "\n"]
    if verify_only:
        for name, lines in streams.items():
            if (output / name).read_text() != "".join(lines):
                raise ValueError(f"frozen P114 artifact differs: {name}")
    else:
        if output.exists():
            raise ValueError("P114 output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="p114-policy-", dir=output.parent
        ) as raw:
            temporary = Path(raw)
            for name, lines in streams.items():
                (temporary / name).write_text("".join(lines))
            os.rename(temporary, output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(dump(compile_pilot(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
