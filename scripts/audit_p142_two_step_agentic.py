"""Independently replay final P142 transcripts against frozen P133 worlds."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import p114_controlled_action_feedback as policy
from scripts import p133_state_mechanism_batch as state
from scripts.train_sft import tokenize_assistant_only


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit(input_dir: Path) -> dict[str, Any]:
    manifest = json.loads((input_dir / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != "longworld.p142-two-step-agentic-output.v1"
        or manifest.get("train_ready") is not False
    ):
        raise ValueError("wrong P142 candidate contract")
    for relative, expected in manifest["files_sha256"].items():
        if _sha(input_dir / relative) != expected:
            raise ValueError(f"P142 artifact bytes differ: {relative}")
    source_manifest_path = ROOT / manifest["source_manifest"]
    if _sha(source_manifest_path) != manifest["source_manifest_sha256"]:
        raise ValueError("P133 source manifest differs")
    source_manifest = json.loads(source_manifest_path.read_text())
    proof_rows = _rows(input_dir / "trajectory_proofs.jsonl")
    index_rows = _rows(input_dir / "sample_index.jsonl")
    by_sample = {row["sample_id"]: row for row in index_rows}
    if len(by_sample) != len(index_rows) or len(index_rows) != 2 * len(proof_rows):
        raise ValueError("P142 final stage identities differ")
    readers = {}
    for split in ("train", "eval"):
        for stage in (1, 2):
            name = f"stage{stage}_{split}.jsonl"
            for position, reader in enumerate(_rows(input_dir / name)):
                sample_id = reader["sample_id"]
                index = by_sample[sample_id]
                if (
                    index["split"] != split
                    or index["stage"] != stage
                    or index["output_file"] != name
                    or index["row_index"] != position
                ):
                    raise ValueError("P142 sample pointer differs")
                readers[sample_id] = reader
    if set(readers) != set(by_sample):
        raise ValueError("P142 stage reader/index mismatch")
    tokenizer = policy._worker_tokenizer()
    stage_tokens = Counter()
    source_splits: dict[str, set[str]] = defaultdict(set)
    actions = Counter()
    for proof in proof_rows:
        trace_id = proof["trace_id"]
        first = readers[trace_id + ":first"]
        second = readers[trace_id + ":second"]
        first_index = by_sample[first["sample_id"]]
        second_index = by_sample[second["sample_id"]]
        if first_index["trace_id"] != trace_id or second_index["trace_id"] != trace_id:
            raise ValueError("trace stage association differs")
        if (
            first_index["source_group"] != proof["world_id"]
            or second_index["source_group"] != proof["world_id"]
        ):
            raise ValueError("source world association differs")
        source_splits[proof["world_id"]].add(first_index["split"])
        source_splits[proof["world_id"]].add(second_index["split"])
        relative = f"worlds/{proof['world_id']}/world.json"
        source_path = source_manifest_path.parent / relative
        expected_source_sha = source_manifest["world_files_sha256"][relative]
        if (
            _sha(source_path) != expected_source_sha
            or expected_source_sha != proof["source_world_sha256"]
        ):
            raise ValueError("P133 source world pin differs")
        world = json.loads(source_path.read_text())
        receipt = source_path.parent / "receipt.json"
        receipt_relative = f"worlds/{proof['world_id']}/receipt.json"
        if (
            _sha(receipt) != proof["source_receipt_sha256"]
            or _sha(receipt) != source_manifest["world_files_sha256"][receipt_relative]
        ):
            raise ValueError("P133 receipt pin differs")
        expected_split = "eval" if world["seed"] % 5 == 0 else "train"
        if first_index["split"] != expected_split:
            raise ValueError("source seed split differs")
        task = next(
            item
            for item in world["tasks"]
            if item["task_id"] == proof["source_task_id"]
        )
        context = world["reader_context"]
        if first["messages"][0]["content"].split(state.MARKER, 1)[0] != context:
            raise ValueError("first turn source body differs")
        if second["messages"][:2] != first["messages"] or len(second["messages"]) != 4:
            raise ValueError("second turn does not continue first action")
        if [item["role"] for item in second["messages"]] != [
            "user",
            "assistant",
            "user",
            "assistant",
        ]:
            raise ValueError("invalid action/observation/action transcript")
        observed = json.loads(second["messages"][2]["content"].splitlines()[0])[
            "observation"
        ]
        chosen_first = json.loads(first["messages"][1]["content"])["action"]
        chosen_second = json.loads(second["messages"][3]["content"])["action"]
        if (
            chosen_first != proof["first_action"]
            or chosen_second != proof["second_action"]
            or observed["event_action"] != chosen_first
        ):
            raise ValueError("transcript actions differ from proof")
        original = state.solve_visible(context, task["question"])["sum"]
        reduced, removed = state._drop(context, proof["event_id"])
        if proof["event_id"] not in removed:
            raise ValueError("first action did not remove selected event")
        cancelled = state.solve_visible(reduced, task["question"])["sum"]
        balances = {"KEEP_EVENT": original, "CANCEL_EVENT": cancelled}
        if (
            balances[chosen_first] != observed["balance"]
            or observed["balance"] != proof["observed_balance"]
        ):
            raise ValueError("action observation differs")
        terminal = {}
        seconds = {}
        for first_action, balance in balances.items():
            scores = {
                action: -abs(
                    balance + (500 if action == "ADD_500" else -500) - proof["target"]
                )
                for action in ("ADD_500", "REMOVE_500")
            }
            if scores["ADD_500"] == scores["REMOVE_500"]:
                raise ValueError("second action ties")
            best = max(scores, key=scores.get)
            terminal[first_action] = scores[best]
            seconds[first_action] = best
        if terminal["KEEP_EVENT"] == terminal["CANCEL_EVENT"]:
            raise ValueError("first action ties")
        best_first = max(terminal, key=terminal.get)
        alternate = "CANCEL_EVENT" if best_first == "KEEP_EVENT" else "KEEP_EVENT"
        if (
            chosen_first != best_first
            or chosen_second != seconds[best_first]
            or seconds[alternate] == chosen_second
            or proof["alternate_first_action"] != alternate
            or proof["alternate_observed_balance"] != balances[alternate]
            or proof["alternate_second_action"] != seconds[alternate]
            or proof["terminal_feedback"] != terminal[best_first]
        ):
            raise ValueError("causal branch/action/feedback replay differs")
        for stage, reader, index in (
            (1, first, first_index),
            (2, second, second_index),
        ):
            encoded = tokenize_assistant_only(tokenizer, reader["messages"], 262144)
            ids, labels = encoded["input_ids"], encoded["labels"]
            supervised = sum(value != -100 for value in labels)
            prefix = len(ids) - supervised
            if (
                len(ids) != index["full_chat_tokens"]
                or prefix != index["input_tokens"]
                or supervised != index["supervised_tokens"]
                or index["loss_mask_start"] != prefix
                or labels != [-100] * prefix + ids[prefix:]
            ):
                raise ValueError("final assistant-only mask differs")
            if stage == 1:
                positions, query_token = policy._positions(
                    context, reader["messages"], tokenizer, ids
                )
                span = positions[proof["event_id"]]
                if (
                    list(span) != proof["event_token_span"]
                    or query_token != proof["first_query_token"]
                    or query_token - span[1] != proof["event_to_first_query_tokens"]
                    or query_token - span[1] < 16384
                ):
                    raise ValueError("final-chat event distance differs")
            stage_tokens[stage] += len(ids)
        actions[chosen_first] += 1
    if any(len(splits) != 1 for splits in source_splits.values()):
        raise ValueError("source world crosses train/eval")
    if (
        len(proof_rows) != manifest["traces"]
        or sum(stage_tokens.values()) != manifest["full_chat_tokens"]
    ):
        raise ValueError("P142 manifest count/token totals differ")
    return {
        "status": "independent_final_transcript_replay_passed",
        "traces": len(proof_rows),
        "stage_rows": len(index_rows),
        "source_worlds": len(source_splits),
        "first_actions": dict(sorted(actions.items())),
        "stage_full_chat_tokens": dict(sorted(stage_tokens.items())),
        "source_split_overlap": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data/candidates/p142_two_step_agentic_pilot_v1",
    )
    args = parser.parse_args()
    print(json.dumps(audit(args.input), sort_keys=True))


if __name__ == "__main__":
    main()
