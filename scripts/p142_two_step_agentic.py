"""Compile bounded two-action trajectories from frozen P133 state worlds.

The first action retains or cancels one visible event. Its observation changes
the balance used by the second ADD/REMOVE action. This is a separate policy
candidate contract, not a reader QA export or a training release.
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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import p114_controlled_action_feedback as policy
from scripts import p133_state_mechanism_batch as state
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p142-two-step-agentic.v1"
OUTPUT_SCHEMA = "longworld.p142-two-step-agentic-output.v1"
FIRST_ACTIONS = ("KEEP_EVENT", "CANCEL_EVENT")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mask(
    tokenizer: Any, messages: list[dict[str, str]], maximum: int
) -> dict[str, int]:
    encoded = tokenize_assistant_only(tokenizer, messages, maximum)
    ids, labels = encoded["input_ids"], encoded["labels"]
    supervised = sum(label != -100 for label in labels)
    prompt = len(ids) - supervised
    if not supervised or labels != [-100] * prompt + ids[prompt:]:
        raise ValueError("final assistant-only mask differs")
    return {
        "full_chat_tokens": len(ids),
        "input_tokens": prompt,
        "supervised_tokens": supervised,
        "loss_mask_start": prompt,
    }


def _branch(context: str, question: dict[str, Any], event_id: str, action: str) -> int:
    if action == "KEEP_EVENT":
        visible = context
    elif action == "CANCEL_EVENT":
        visible, _ = state._drop(context, event_id)
    else:
        raise ValueError("unknown first action")
    return state.solve_visible(visible, question)["sum"]


def _plan(balance_by_action: dict[str, int], target: int) -> dict[str, Any]:
    outcomes = {}
    for action in FIRST_ACTIONS:
        balance = balance_by_action[action]
        second, feedback = policy.decision(balance, target, 500)
        outcomes[action] = {
            "observed_balance": balance,
            "second_action": second,
            "second_action_outcomes": feedback,
            "terminal_feedback": feedback[second]["feedback"],
        }
    if (
        outcomes["KEEP_EVENT"]["second_action"]
        == outcomes["CANCEL_EVENT"]["second_action"]
    ):
        raise ValueError("first action does not change second decision")
    scores = {key: value["terminal_feedback"] for key, value in outcomes.items()}
    if scores["KEEP_EVENT"] == scores["CANCEL_EVENT"]:
        raise ValueError("first action has tied terminal feedback")
    chosen = max(FIRST_ACTIONS, key=lambda action: scores[action])
    return {"first_action": chosen, "branches": outcomes}


def _event(context: str, question: dict[str, Any]) -> tuple[str, dict[str, int]]:
    base = state.solve_visible(context, question)["sum"]
    _, rows, events = state._parse(context)
    scoped = set(state._scope(rows, question["filters"]))
    eligible = {
        item["id"]
        for item in events
        if item["type"] in {"partial_reversal", "hold"} and item["record_id"] in scoped
    }
    # Prefer an early state event so distance is measured in final chat tokens.
    for line in context.splitlines()[1:]:
        item = json.loads(line)
        event_id = item["id"]
        if event_id not in eligible:
            continue
        changed = _branch(context, question, event_id, "CANCEL_EVENT")
        difference = changed - base
        if difference > 500 and difference != 1000:
            return event_id, {"KEEP_EVENT": base, "CANCEL_EVENT": changed}
    raise ValueError("no state event yields distinct two-step branches")


def _compile_world(
    args: tuple[str, str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    world_text, expected_sha, config = args
    path = Path(world_text)
    if _sha(path) != expected_sha:
        raise ValueError("P133 source world bytes differ from manifest")
    world = json.loads(path.read_text())
    receipt = json.loads((path.parent / "receipt.json").read_text())
    if (
        receipt["world_id"] != world["world_id"]
        or receipt["world_sha256"] != expected_sha
    ):
        raise ValueError("P133 world receipt differs")
    context = world["reader_context"]
    if state.digest(context) != world["context_sha256"]:
        raise ValueError("P133 visible context differs")
    tokenizer = policy._worker_tokenizer()
    split = "eval" if world["seed"] % 5 == 0 else "train"
    accepted = []
    ledger = []
    for task in world["tasks"]:
        if task["question"]["operation"] != "net_sum":
            continue
        if state.solve_visible(context, task["question"]) != task["answer"]:
            raise ValueError("source P133 state does not replay")
        source = {"world_id": world["world_id"], "task_id": task["task_id"]}
        try:
            event_id, balances = _event(context, task["question"])
        except ValueError as exc:
            ledger.append({**source, "status": "rejected", "reason": str(exc)})
            continue
        low, high = balances["KEEP_EVENT"], balances["CANCEL_EVENT"]
        for preferred, target in (
            ("KEEP_EVENT", low + 500),
            ("CANCEL_EVENT", high - 500),
        ):
            identity = f"p142:{world['world_id']}:{task['task_id']}:{preferred}"
            try:
                plan = _plan(balances, target)
                if plan["first_action"] != preferred:
                    raise ValueError("first action preference differs")
                query = (
                    "This is a two-step simulated control episode. Apply the visible filters "
                    f"and state rules: {state._filter_text(task['question'])}. "
                    f"Use effective cutoff {task['question']['asof']} and disclosure cutoff "
                    f"{task['question']['known_at']}. First choose KEEP_EVENT or CANCEL_EVENT "
                    f"for event {event_id}; cancellation removes that event and its linked "
                    "grant rows. The environment then reports the resulting scoped balance. "
                    "Second choose ADD_500 or REMOVE_500; these change that balance by 500. "
                    f"Terminal feedback is minus the distance to target {target}. Maximize "
                    "terminal feedback, choosing the best second action for each first branch. "
                    'Return only {"action":"FIRST_ACTION"} now.'
                )
                first = {
                    "sample_id": identity + ":first",
                    "messages": [
                        {"role": "user", "content": context + state.MARKER + query},
                        {"role": "assistant", "content": _dump({"action": preferred})},
                    ],
                }
                first_mask = _mask(
                    tokenizer, first["messages"], config["max_full_chat_tokens"]
                )
                encoded = tokenize_assistant_only(
                    tokenizer, first["messages"], config["max_full_chat_tokens"]
                )
                positions, query_token = policy._positions(
                    context, first["messages"], tokenizer, encoded["input_ids"]
                )
                gap = query_token - positions[event_id][1]
                if gap < config["minimum_event_to_first_query_tokens"]:
                    raise ValueError(
                        "insufficient final-chat event-to-first-query distance"
                    )
                observed = _branch(context, task["question"], event_id, preferred)
                if observed != plan["branches"][preferred]["observed_balance"]:
                    raise ValueError(
                        "first action observation differs from world replay"
                    )
                observation = _dump(
                    {"observation": {"balance": observed, "event_action": preferred}}
                )
                second_action = plan["branches"][preferred]["second_action"]
                second = {
                    "sample_id": identity + ":second",
                    "messages": [
                        *first["messages"],
                        {
                            "role": "user",
                            "content": observation
                            + "\nChoose the best second action. Return only the action JSON.",
                        },
                        {
                            "role": "assistant",
                            "content": _dump({"action": second_action}),
                        },
                    ],
                }
                second_mask = _mask(
                    tokenizer, second["messages"], config["max_full_chat_tokens"]
                )
                replayed_second, replayed_outcomes = policy.decision(
                    observed, target, 500
                )
                if replayed_second != second_action:
                    raise ValueError("second action does not replay after observation")
                alternate = (
                    "CANCEL_EVENT" if preferred == "KEEP_EVENT" else "KEEP_EVENT"
                )
                alternate_balance = _branch(
                    context, task["question"], event_id, alternate
                )
                alternate_second = policy.decision(alternate_balance, target, 500)[0]
                if alternate_second == second_action:
                    raise ValueError(
                        "alternate first action does not flip second action"
                    )
            except ValueError as exc:
                ledger.append(
                    {
                        **source,
                        "preferred_first_action": preferred,
                        "status": "rejected",
                        "reason": str(exc),
                    }
                )
                continue
            proof = {
                "trace_id": identity,
                "world_id": world["world_id"],
                "source_world_sha256": expected_sha,
                "source_receipt_sha256": _sha(path.parent / "receipt.json"),
                "source_task_id": task["task_id"],
                "mechanism": world["mechanism"],
                "event_id": event_id,
                "event_token_span": list(positions[event_id]),
                "first_query_token": query_token,
                "event_to_first_query_tokens": gap,
                "target": target,
                "first_action": preferred,
                "observed_balance": observed,
                "second_action": second_action,
                "terminal_feedback": replayed_outcomes[second_action]["feedback"],
                "alternate_first_action": alternate,
                "alternate_observed_balance": alternate_balance,
                "alternate_second_action": alternate_second,
                "branch_replay": plan["branches"],
                "evidence_scope": "selected visible event and both first-action branches; global alternative proof unsearched",
            }
            accepted.append(
                {
                    "trace_id": identity,
                    "world_id": world["world_id"],
                    "split": split,
                    "mechanism": world["mechanism"],
                    "length_records": world["length_records"],
                    "first": first,
                    "first_mask": first_mask,
                    "second": second,
                    "second_mask": second_mask,
                    "proof": proof,
                }
            )
            ledger.append(
                {
                    **source,
                    "preferred_first_action": preferred,
                    "status": "accepted",
                    "trace_id": identity,
                }
            )
    return accepted, ledger


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if (
        config.get("schema_version") != SCHEMA
        or config.get("mechanisms") != list(state.MECHANISMS)
        or config.get("length_records") != [400, 800]
        or not 1 <= config.get("train_worlds_per_cell", 0) <= 8
        or not 1 <= config.get("eval_worlds_per_cell", 0) <= 4
        or config.get("minimum_event_to_first_query_tokens", 0) < 16384
        or config.get("max_full_chat_tokens", 0) > 262144
        or not 1 <= config.get("workers", 0) <= 4
    ):
        raise ValueError("invalid P142 config")
    source = ROOT / config["source_manifest"]
    if (
        source.is_relative_to(ROOT) is False
        or _sha(source) != config["source_manifest_sha256"]
    ):
        raise ValueError("P133 source manifest pin differs")
    return config


def _source_jobs(config: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    manifest_path = ROOT / config["source_manifest"]
    manifest = json.loads(manifest_path.read_text())
    if manifest["schema_version"] != state.OUTPUT_SCHEMA:
        raise ValueError("wrong P133 source schema")
    cells: dict[tuple[str, int, str], list[tuple[str, str, dict[str, Any]]]] = (
        defaultdict(list)
    )
    for world_id in manifest["source_world_ids"]:
        relative = f"worlds/{world_id}/world.json"
        world_path = manifest_path.parent / relative
        pin = manifest["world_files_sha256"][relative]
        if _sha(world_path) != pin:
            raise ValueError("source world SHA differs")
        world = json.loads(world_path.read_text())
        if world["world_id"] != world_id:
            raise ValueError("source world ID differs")
        split = "eval" if world["seed"] % 5 == 0 else "train"
        cells[(world["mechanism"], world["length_records"], split)].append(
            (str(world_path), pin, config)
        )
    jobs = []
    for mechanism in config["mechanisms"]:
        for length in config["length_records"]:
            for split, limit in (
                ("train", config["train_worlds_per_cell"]),
                ("eval", config["eval_worlds_per_cell"]),
            ):
                available = cells[(mechanism, length, split)]
                if len(available) < limit:
                    raise ValueError("insufficient frozen P133 source worlds for cell")
                jobs.extend(available[:limit])
    return jobs


def compile_pilot(
    config_path: Path, output: Path, *, verify_only: bool = False
) -> dict[str, Any]:
    config = _config(config_path)
    jobs = _source_jobs(config)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not verify_only:
        raise ValueError("P142 output already exists")
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        batches = list(pool.map(_compile_world, jobs))
    accepted = sorted(
        (item for items, _ in batches for item in items),
        key=lambda row: row["trace_id"],
    )
    attempts = sorted(
        (item for _, rows in batches for item in rows),
        key=lambda row: (
            row["world_id"],
            row["task_id"],
            row.get("preferred_first_action", ""),
        ),
    )
    if not accepted or len({row["trace_id"] for row in accepted}) != len(accepted):
        raise ValueError("empty or duplicate P142 traces")
    if not {row["split"] for row in accepted} == {"train", "eval"}:
        raise ValueError("P142 pilot lacks train/eval traces")
    streams = {
        name: []
        for name in (
            "stage1_train.jsonl",
            "stage1_eval.jsonl",
            "stage2_train.jsonl",
            "stage2_eval.jsonl",
            "sample_index.jsonl",
            "trajectory_proofs.jsonl",
            "mask_audit.jsonl",
            "attempt_ledger.jsonl",
        )
    }
    positions = Counter()
    for row in accepted:
        for stage, reader_key in ((1, "first"), (2, "second")):
            name = f"stage{stage}_{row['split']}.jsonl"
            reader = row[reader_key]
            mask = row[f"{reader_key}_mask"]
            streams[name].append(_dump(reader) + "\n")
            streams["sample_index.jsonl"].append(
                _dump(
                    {
                        "sample_id": reader["sample_id"],
                        "trace_id": row["trace_id"],
                        "source_group": row["world_id"],
                        "split": row["split"],
                        "mechanism": row["mechanism"],
                        "length_records": row["length_records"],
                        "stage": stage,
                        "output_file": name,
                        "row_index": positions[name],
                        **mask,
                    }
                )
                + "\n"
            )
            streams["mask_audit.jsonl"].append(
                _dump({"sample_id": reader["sample_id"], **mask}) + "\n"
            )
            positions[name] += 1
        streams["trajectory_proofs.jsonl"].append(_dump(row["proof"]) + "\n")
    streams["attempt_ledger.jsonl"] = [_dump(row) + "\n" for row in attempts]
    manifest = {
        "schema_version": OUTPUT_SCHEMA,
        "train_ready": False,
        "contract": "two_step_simulated_policy_candidate_separate_from_reader_QA",
        "source_manifest": config["source_manifest"],
        "source_manifest_sha256": config["source_manifest_sha256"],
        "config_sha256": _sha(config_path),
        "compiler_sha256": _sha(Path(__file__)),
        "source_worlds_attempted": len(jobs),
        "source_worlds_accepted": len({row["world_id"] for row in accepted}),
        "traces": len(accepted),
        "stage_rows": len(accepted) * 2,
        "attempts": len(attempts),
        "rejections": dict(
            sorted(
                Counter(
                    row["reason"] for row in attempts if row["status"] == "rejected"
                ).items()
            )
        ),
        "by_mechanism": dict(
            sorted(Counter(row["mechanism"] for row in accepted).items())
        ),
        "by_split": dict(sorted(Counter(row["split"] for row in accepted).items())),
        "first_actions": dict(
            sorted(Counter(row["proof"]["first_action"] for row in accepted).items())
        ),
        "length_bins": dict(
            sorted(
                Counter(
                    "<32K"
                    if row["first_mask"]["full_chat_tokens"] < 32768
                    else "32-64K"
                    if row["first_mask"]["full_chat_tokens"] < 65536
                    else "64-128K"
                    if row["first_mask"]["full_chat_tokens"] < 131072
                    else "128-256K"
                    for row in accepted
                ).items()
            )
        ),
        "full_chat_tokens": sum(
            row[f"{key}_mask"]["full_chat_tokens"]
            for row in accepted
            for key in ("first", "second")
        ),
        "supervised_tokens": sum(
            row[f"{key}_mask"]["supervised_tokens"]
            for row in accepted
            for key in ("first", "second")
        ),
        "minimum_event_to_first_query_tokens": min(
            row["proof"]["event_to_first_query_tokens"] for row in accepted
        ),
        "files_sha256": {
            name: hashlib.sha256("".join(lines).encode()).hexdigest()
            for name, lines in streams.items()
        },
    }
    with tempfile.TemporaryDirectory(prefix="p142-", dir=output.parent) as raw:
        temporary = Path(raw)
        for name, lines in streams.items():
            (temporary / name).write_text("".join(lines))
        (temporary / "manifest.json").write_text(_dump(manifest) + "\n")
        if verify_only:
            expected = {
                "manifest.json": _sha(temporary / "manifest.json"),
                **manifest["files_sha256"],
            }
            if {name: _sha(output / name) for name in expected} != expected:
                raise ValueError("P142 byte replay differs")
        else:
            os.rename(temporary, output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/p142_two_step_agentic_pilot_v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/candidates/p142_two_step_agentic_pilot_v1",
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(_dump(compile_pilot(args.config, args.output, verify_only=args.verify_only)))


if __name__ == "__main__":
    main()
