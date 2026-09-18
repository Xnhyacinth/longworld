#!/usr/bin/env python3
"""P69 record-world runner: (L, K, H) grid to messages rows plus receipts.

Mirrors scripts/run_capability_world_pipeline.py: ProcessPool workers, one
exclusive directory per shard, hash-bound completion receipts, a fail-closed
manifest that never promotes a rejected shard, and a fresh output directory
required for resume. Each emitted row carries exactly one question (the
P0-3-independent one-question contract); the `messages` key is the same
sharegpt-style shape scripts/measure_sft_collapse_predictors.py --gate reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.synthesis import capability_records as records

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
SCHEMA = "longworld.record-world.v1"
_TOKENIZER = None


class InfeasibleTarget(ValueError):
    """The cell cannot host this token target at any L; recorded, not a defect.

    K consumed records plus the minimum distractor budget set a hard floor on L,
    so some (K, token target) pairs are arithmetically impossible. That is a
    property of the grid, not a generation failure, and it is recorded as a
    measured skip so the manifest never silently implies the cell was tried.
    """


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new_json(path: Path, value):
    # Publish complete JSON atomically and never replace an existing receipt.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink()


def split_for_seed(seed: int) -> str:
    """One world seed lives in exactly one split, with all its (L,K,H) variants."""
    return "eval" if seed % 5 == 0 else "train"


def token_targets_for_depth(config: dict, depth: int) -> list[int]:
    """Token targets a depth is scheduled at; a K=200 cell cannot fit 8K."""
    targets = config["token_targets_by_depth"].get(str(depth))
    if not targets:
        raise ValueError(f"no token targets scheduled for depth {depth}")
    return targets


def make_plan(config: dict) -> list[dict]:
    """Stratify the grid deterministically: family x depth x worlds, L fitted."""
    plan = []
    for family in config["families"]:
        for depth in config["depths"]:
            consumed = config["consumed_by_depth"][str(depth)]
            for index in range(config["worlds_per_cell"]):
                for target in token_targets_for_depth(config, depth):
                    floor = min_hostable_length(consumed, config["variants_per_world"])
                    plan.append(
                        {
                            "seed": config["world_seed_base"] + len(plan),
                            "family": family,
                            "depth": depth,
                            "length_records": max(config["lengths"][0], floor),
                            "consumed_records": consumed,
                            "n_variants": config["variants_per_world"],
                            "split": split_for_seed(
                                config["world_seed_base"] + len(plan)
                            ),
                            "cell_index": index,
                            "token_target": target,
                        }
                    )
    return plan


def build_messages(context: str, task: dict) -> list[dict[str, str]]:
    """One question per row: the row's instruction, then the single answer.

    The instruction is re-rendered from (program, phrasing index) and must match
    byte for byte, so a reworded prompt cannot reach a training row.
    """
    instruction = task["instruction"]
    if instruction != records.render_instruction(
        task["question"]["family"], task["question"], task["phrasing_index"]
    ):
        raise ValueError("instruction does not match the registered contract")
    if context in instruction:
        raise ValueError("question instruction duplicates the context")
    return [
        {"role": "user", "content": context + "\n\nQUESTION\n" + instruction},
        {"role": "assistant", "content": canonical(task["answer"])},
    ]


def measure_tokens(context: str, instruction: str, answer: str) -> dict:
    """Tokens per record and the rendered chat lengths, in the repo's tokenizer."""
    from scripts.train_sft import _render_chat, tokenize_assistant_only

    prompt = [{"role": "user", "content": context + "\n\nQUESTION\n" + instruction}]
    full = prompt + [{"role": "assistant", "content": answer}]
    full_tokens = _chat_tokens(full)
    input_tokens = len(
        _TOKENIZER(
            _render_chat(_TOKENIZER, prompt, generation_prompt=True), truncation=False
        )["input_ids"]
    )
    encoded = tokenize_assistant_only(_TOKENIZER, full, 1 << 30)
    return {
        "full_chat_tokens": full_tokens,
        "input_tokens": input_tokens,
        "supervised_tokens": sum(label != -100 for label in encoded["labels"]),
    }


def min_hostable_length(consumed_records: int, n_variants: int) -> int:
    """Smallest L whose distractor budget still hosts n_variants K-sized tasks."""
    length = 32
    while length < 40000:
        if records.plan_variants(length, consumed_records, n_variants) == n_variants:
            return length
        length += 1
    raise ValueError("no hostable L found for this K and variant count")


def fit_cell(job: dict, token_target: int, tokenizer) -> tuple[dict, list[dict]]:
    """Scale L to a token target, never truncating: only whole rows are added.

    L is the only length knob here (the record is the atom of this world), so
    the search is proportional rather than a binary scan: one measured world
    gives tokens-per-record, which predicts the next L directly. The accepted
    world is then re-measured row by row, not extrapolated. L is floored at the
    smallest length that can host the requested variants, so the search never
    proposes a world the generator would reject.
    """
    floor = min_hostable_length(job["consumed_records"], job["n_variants"])
    low, high = floor, max(floor, 30000)
    best = None
    seen = set()
    length = max(floor, job["length_records"])
    for _ in range(24):
        if length in seen:
            raise ValueError("token fitting stalled; no truncated fallback")
        seen.add(length)
        bundle = records.generate_world(
            job["seed"],
            job["family"],
            length,
            job["consumed_records"],
            job["depth"],
            job["n_variants"],
        )
        task = bundle["tasks"][0]
        messages = build_messages(bundle["context"], task)
        tokens = _chat_tokens(messages)
        if tokens <= token_target:
            best = (bundle, tokens, length)
            low = length + 1
        else:
            high = length - 1
        if low > high:
            break
        # Tokens are strictly increasing in L, so the first probe is the
        # tokens-per-record extrapolation and the rest is bisection.
        per_record = max(tokens / length, 1e-6)
        proposed = min(high, max(low, round(token_target * 0.97 / per_record)))
        length = proposed if proposed != length else (low + high) // 2
    if best is None:
        raise InfeasibleTarget(
            f"no L fits {token_target} tokens; the minimum hostable world is "
            f"L={floor} for K={job['consumed_records']}"
        )
    bundle, tokens, length = best
    check = records.validate_bundle(bundle)
    if not check["passed"]:
        # One validation per fitted cell: full intervention replay at final L.
        raise ValueError(f"fitted world failed validation: {check['errors']}")
    if not 0.80 * token_target <= tokens <= token_target:
        raise ValueError(
            f"fitted world is {tokens} tokens, outside [0.80, 1.00] x {token_target} "
            f"(record granularity)"
        )
    return bundle, _rows(bundle, job, token_target, tokens)


def _chat_tokens(messages: list[dict]) -> int:
    from scripts.train_sft import _render_chat

    return len(
        _TOKENIZER(
            _render_chat(_TOKENIZER, messages, generation_prompt=False),
            truncation=False,
        )["input_ids"]
    )


def _rows(bundle: dict, job: dict, token_target: int, tokens: int) -> list[dict]:
    rows = []
    for task in bundle["tasks"]:
        messages = build_messages(bundle["context"], task)
        measurements = measure_tokens(
            bundle["context"], task["instruction"], messages[1]["content"]
        )
        row = {
            "schema_version": SCHEMA,
            "example_id": f"{bundle['world_id']}:{task['task_id']}",
            "world_id": bundle["world_id"],
            "world_seed": job["seed"],
            "split": job["split"] if job.get("split") else split_for_seed(job["seed"]),
            "family": job["family"],
            "depth": job["depth"],
            "length_records": bundle["length_records"],
            "consumed_records": task["consumed_count"],
            "target": {
                "length_records": job["length_records"],
                "consumed_records": job["consumed_records"],
                "depth": job["depth"],
                "token_target": token_target,
                "tokens_per_record": round(tokens / bundle["length_records"], 3),
            },
            "messages": messages,
            "provenance": {
                "consumed_row_ids": task["consumed"],
                "distractor_rows": bundle["length_accounting"]["padding_rows"],
                "padding_is_exposure_not_semantic_scale": True,
            },
            "lineage": {
                **bundle["honesty"],
                "schema": records.VERSION,
                "world_family": bundle["family"],
                "n_variants": bundle["n_variants"],
            },
            "local_symbolic_validation_passed": True,
        }
        row.update(measurements)
        rows.append(row)
    return rows


def run_shard(job: dict, token_target: int, output: str, fingerprint: str) -> dict:
    shard_id = (
        f"{job['family']}-h{job['depth']}-L{job['length_records']}"
        f"-seed{job['seed']}-{token_target}"
    )
    shard = Path(output) / "shards" / shard_id
    if shard.exists():
        raise ValueError("shard directory already exists; resume needs a fresh output")
    shard.mkdir()
    bundle, rows = fit_cell(job, token_target, _TOKENIZER)
    write_new_json(shard / "world.json", bundle)
    with (shard / "rows.jsonl").open("x") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")
    receipt = {
        "fingerprint": fingerprint,
        "shard_id": shard_id,
        "worker_pid": os.getpid(),
        "world_id": bundle["world_id"],
        "world_seed": job["seed"],
        "family": job["family"],
        "depth": job["depth"],
        "target_length_records": job["length_records"],
        "target_consumed_records": job["consumed_records"],
        "split": job["split"],
        "token_target": token_target,
        "rows": len(rows),
        "length_records": bundle["length_records"],
        "consumed_rows_per_task": bundle["length_accounting"][
            "consumed_rows_per_task"
        ],
        "padding_rows": bundle["length_accounting"]["padding_rows"],
        "decoy_padding_rows": bundle["length_accounting"]["decoy_padding_rows"],
        "reference_rows": bundle["length_accounting"]["reference_rows"],
        "instruction_variants": len({row["messages"][0]["content"] for row in rows}),
        "input_tokens": [row["input_tokens"] for row in rows],
        "full_chat_tokens": [row["full_chat_tokens"] for row in rows],
        "supervised_tokens": sum(row["supervised_tokens"] for row in rows),
        "validation": records.validate_bundle(bundle),
        "files": {name: sha(shard / name) for name in ("world.json", "rows.jsonl")},
    }
    # Completion receipt is written last; incomplete shards are never admitted.
    write_new_json(shard / "receipt.json", receipt)
    return receipt


def completion_manifest(
    receipts: list[dict], plan: list[dict], files: dict, skips: list[dict]
) -> dict:
    split_worlds = {
        split: {r["world_seed"] for r in receipts if r["split"] == split}
        for split in ("train", "eval")
    }
    if split_worlds["train"] & split_worlds["eval"]:
        raise ValueError("world split collision")
    return {
        "schema_version": SCHEMA,
        "status": "local_symbolic_record_worlds_complete",
        "tokenizer": {"model_id": MODEL, "revision": REVISION},
        "planned_shards": len(plan),
        "completed_shards": len(receipts),
        "infeasible_shards": len(skips),
        "infeasibility_scope": (
            "targets rejected at generation time because K consumed records plus "
            "a minimum distractor budget exceed the token budget; recorded, not "
            "silently dropped, and not counted as completed"
        ),
        "world_seeds": len({r["world_seed"] for r in receipts}),
        "families": sorted({r["family"] for r in receipts}),
        "depths": sorted({r["depth"] for r in receipts}),
        "rows": sum(r["rows"] for r in receipts),
        "split_rows": {
            split: sum(r["rows"] for r in receipts if r["split"] == split)
            for split in ("train", "eval")
        },
        "length_records": sorted({r["length_records"] for r in receipts}),
        "consumed_rows": sorted(
            {value for r in receipts for value in r["consumed_rows_per_task"]}
        ),
        "padding_rows": sum(r["padding_rows"] for r in receipts),
        "semantic_tasks": sum(len(r["consumed_rows_per_task"]) for r in receipts),
        "full_chat_tokens": sum(sum(r["full_chat_tokens"]) for r in receipts),
        "supervised_tokens": sum(r["supervised_tokens"] for r in receipts),
        "worker_pids": sorted({r["worker_pid"] for r in receipts}),
        "one_question_per_row": True,
        "padding_is_exposure_not_semantic_scale": True,
        "one_semantic_task_one_primary_length": (
            "each world fixes one L; the (L,K,H) grid varies across worlds"
        ),
        "source_kind": "simulated",
        "strict_long_dependency_verified": False,
        "model_utility_measured": False,
        "production_eligible": False,
        "files": files,
        "shards": receipts,
    }


def run(config_path: Path, destination: Path, resume: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config["workers"] < 1:
        raise ValueError("workers must be positive")
    for targets in config["token_targets_by_depth"].values():
        if not targets or any(t < 4096 for t in targets):
            raise ValueError("token targets must be >= 4096")
    plan = make_plan(config)
    if not plan:
        raise ValueError("empty plan")
    code_paths = (
        "scripts/run_capability_records.py",
        "longworld/synthesis/capability_records.py",
        "scripts/train_sft.py",
    )
    state = {
        "config": config,
        "plan": plan,
        "code_sha256": {name: sha(ROOT / name) for name in code_paths},
    }
    fingerprint = hashlib.sha256(canonical(state).encode()).hexdigest()
    destination = destination.resolve()
    if destination.exists():
        if not resume or json.loads((destination / "state.json").read_text()) != state:
            raise ValueError(
                "existing output requires --resume and identical config/code"
            )
    else:
        destination.mkdir(parents=True)
        (destination / "shards").mkdir()
        write_new_json(destination / "state.json", state)
    receipts, rejects, skips = [], [], []
    with ProcessPoolExecutor(
        max_workers=config["workers"],
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_init_worker,
    ) as pool:
        futures = {
            pool.submit(
                run_shard, job, job["token_target"], str(destination), fingerprint
            ): (job, job["token_target"])
            for job in plan
        }
        for future in as_completed(futures):
            job, target = futures[future]
            try:
                receipt = future.result()
                receipts.append(receipt)
                print(
                    canonical(
                        {
                            "complete": receipt["shard_id"],
                            "rows": receipt["rows"],
                            "L": receipt["length_records"],
                            "pid": receipt["worker_pid"],
                        }
                    ),
                    flush=True,
                )
            except InfeasibleTarget as error:
                skips.append(
                    {
                        "family": job["family"],
                        "seed": job["seed"],
                        "depth": job["depth"],
                        "token_target": target,
                        "reason": str(error),
                    }
                )
                print(canonical({"skipped_infeasible": skips[-1]}), flush=True)
            except (
                ValueError,
                RuntimeError,
                OSError,
                TypeError,
                KeyError,
                AssertionError,
            ) as error:
                rejects.append(
                    {
                        "family": job["family"],
                        "seed": job["seed"],
                        "depth": job["depth"],
                        "token_target": target,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                print(canonical({"rejected": rejects[-1]}), flush=True)
    if any(
        sha(ROOT / name) != digest for name, digest in state["code_sha256"].items()
    ):
        raise RuntimeError("code changed during run; completion forbidden")
    write_new_json(destination / "rejects.json", rejects)
    write_new_json(destination / "infeasible.json", skips)
    receipts.sort(key=lambda row: row["shard_id"])
    files = {}
    for split in ("train", "eval"):
        path = destination / f"{split}.jsonl"
        seen = set()
        with path.open("x") as stream:
            for receipt in receipts:
                if receipt["split"] != split:
                    continue
                shard = destination / "shards" / receipt["shard_id"]
                source = shard / "rows.jsonl"
                if sha(source) != receipt["files"]["rows.jsonl"]:
                    raise RuntimeError("shard content changed before export")
                for line in source.read_text().splitlines():
                    row = json.loads(line)
                    if row["example_id"] in seen:
                        raise ValueError("duplicate exported example")
                    seen.add(row["example_id"])
                    stream.write(line + "\n")
        files[path.name] = sha(path)
    manifest = completion_manifest(receipts, plan, files, skips)
    write_new_json(destination / "manifest.json", manifest)
    return manifest


def _init_worker():
    global _TOKENIZER
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        _TOKENIZER = AutoTokenizer.from_pretrained(
            MODEL, revision=REVISION, local_files_only=True, trust_remote_code=False
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    manifest = run(args.config, args.output, args.resume)
    print(
        canonical(
            {
                key: manifest[key]
                for key in (
                    "status",
                    "rows",
                    "split_rows",
                    "semantic_tasks",
                    "padding_rows",
                    "worker_pids",
                )
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
