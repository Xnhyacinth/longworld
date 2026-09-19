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
import re
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.synthesis import capability_families as families
from longworld.synthesis import capability_records as records
from longworld.synthesis import capability_unanswerable_adapter as unanswerable
from scripts.recommend_training_budget import derive_budget_report

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
SCHEMA = "longworld.record-world.v1"
BUDGET_GBS = 16
_TOKENIZER = None

# Family dispatch: every capability family owns its generator, visible contract
# and validator behind the same (L, K, H) surface, so the runner resolves the
# owning module once per family instead of branching in every step. The record
# worlds are the reference implementation; the P70 families ride the same spine.
FAMILY_MODULES = {
    **{name: records for name in records.FAMILIES},
    **{name: families for name in families.FAMILIES},
    **{name: unanswerable for name in unanswerable.FAMILIES},
}


def module_for(family: str):
    if family not in FAMILY_MODULES:
        raise ValueError(f"no module registered for family {family!r}")
    return FAMILY_MODULES[family]


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


def write_replace_json(path: Path, value):
    # Derived ledgers (rejects/infeasible) are recomputed on resume; atomic
    # rename rather than link so a resumed wave can rewrite its own ledger.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def split_for_seed(seed: int) -> str:
    """One world seed lives in exactly one split, with all its (L,K,H) variants."""
    return "eval" if seed % 5 == 0 else "train"


def rule_family_for_plan(family: str, seed: int, cell_index: int) -> str | None:
    """The rule structure a plan slot will generate, decided at plan time.

    The v2 bank let rule_family be seed % 2, which perfectly confounded
    structure with the (world, token_target) interleave: every depth-2
    threshold_class slot happened to land infeasible. Assigning the structure
    by cell_index parity instead decouples it from the seed stream, so both
    structures appear on both sides of the split and in every target bucket.
    The rule world derives its structure from `seed % 2` internally, so the
    plan re-derives the seed the world needs: keep the seed stream untouched
    for every other family and pass a structure-carrying seed for rule_holdout.
    """
    if family != "rule_holdout":
        return None
    return families.RULE_FAMILIES[cell_index % len(families.RULE_FAMILIES)]


def seed_for_rule_family(seed: int, rule_family: str | None) -> int:
    """A seed whose modulo draw yields the planned rule structure.

    _rule_world reads RULE_FAMILIES[seed % 2]; nudging an odd offset onto the
    seed when the planned structure disagrees keeps every other consumer of
    the seed (world_id hashing, split assignment) on the same stream.
    """
    if rule_family is None:
        return seed
    parity = families.RULE_FAMILIES.index(rule_family)
    if seed % len(families.RULE_FAMILIES) == parity:
        return seed
    return seed + 1


def split_for_job(config: dict, job: dict) -> str:
    """The bank-level split rule: rule structure for rule_holdout, seed otherwise.

    config's trained_rule_family names the ONE structure whose worlds train;
    the other structure's worlds are all eval (structure isolation), while
    every other family keeps the seed-modulo split. A world's local
    rule.holdout block is self-relative and stays untouched — the export
    split is decided here, once, at the bank level.
    """
    if job["family"] == "rule_holdout":
        trained = config["trained_rule_family"]
        return families.split_for_rule_family(job["rule_family"], trained)
    return split_for_seed(job["seed"])


def token_targets_for_depth(config: dict, depth: int) -> list[int]:
    """Token targets a depth is scheduled at; a K=200 cell cannot fit 8K."""
    targets = config["token_targets_by_depth"].get(str(depth))
    if not targets:
        raise ValueError(f"no token targets scheduled for depth {depth}")
    return targets


def worlds_per_cell_for(config: dict, family: str) -> int:
    """Per-family world density: a family's split rule and attrition differ.

    The v2 grid used one global density, but the families are not equal:
    rule_holdout's structure split sends a whole structure to eval, and the
    F-families' feasible surface is narrower (no depth 3). A family-level
    override absorbs exactly that; families without one keep the global value.
    """
    overrides = config.get("worlds_per_cell_by_family") or {}
    if family in overrides:
        return overrides[family]
    return config["worlds_per_cell"]


def depths_for(config: dict, family: str) -> list[int]:
    """Per-family depth surface: scheduling infeasible depths is noise.

    The F-families cap at depth 2 and reject depth 3 loudly at generation —
    v2 recorded 120 such rejects as pure scheduling waste. A family-level
    override plans only the depths the family can host; families without one
    keep the global list.
    """
    overrides = config.get("depths_by_family") or {}
    if family in overrides:
        return overrides[family]
    return config["depths"]


def make_plan(config: dict) -> list[dict]:
    """Stratify the grid deterministically: family x depth x worlds, L fitted."""
    if "rule_holdout" in config["families"] and "trained_rule_family" not in config:
        raise ValueError(
            "rule_holdout banks must pin trained_rule_family (structure split)"
        )
    plan = []
    for family in config["families"]:
        for depth in depths_for(config, family):
            consumed = config["consumed_by_depth"][str(depth)]
            for index in range(worlds_per_cell_for(config, family)):
                for target in token_targets_for_depth(config, depth):
                    floor = min_hostable_length(consumed, config["variants_per_world"])
                    seed = config["world_seed_base"] + len(plan)
                    rule_family = rule_family_for_plan(family, seed, index)
                    job = {
                        "seed": seed_for_rule_family(seed, rule_family),
                        "family": family,
                        "depth": depth,
                        "length_records": max(config["lengths"][0], floor),
                        "consumed_records": consumed,
                        "n_variants": config["variants_per_world"],
                        "rule_family": rule_family,
                        "cell_index": index,
                        "token_target": target,
                    }
                    job["split"] = split_for_job(config, job)
                    plan.append(job)
    return plan


def build_messages(context: str, task: dict) -> list[dict[str, str]]:
    """One question per row: the row's instruction, then the single answer.

    The instruction is re-rendered from (program, phrasing index) and must match
    byte for byte, so a reworded prompt cannot reach a training row.
    """
    instruction = task["instruction"]
    if instruction != module_for(task["question"]["family"]).render_instruction(
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
    # The floor is the family's own: a family that spends more primary rows per
    # consumed row needs a longer L than the record worlds do.
    module, variants = module_for(job["family"]), job["n_variants"]
    floor = job["length_records"]
    while module.plan_variants(floor, job["consumed_records"], variants) != variants:
        floor += 1
    low, high = floor, max(floor, 30000)
    best = None
    seen = set()
    length = max(floor, job["length_records"])
    for _ in range(24):
        if length in seen:
            raise ValueError("token fitting stalled; no truncated fallback")
        seen.add(length)
        bundle = module.generate_world(
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
    check = module_for(job["family"]).validate_bundle(bundle)
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
            "split": job["split"],
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


def manifest_rule_families(plan: list[dict]) -> dict[int, str | None]:
    """Seed -> planned rule structure, so the index reads the plan, not the seed.

    v3 decouples the structure from seed % 2 (rule_family_for_plan), so the
    seed alone can no longer re-derive it. The plan's own mapping is the
    authority; jobs carry it and the sample index copies it through.
    """
    return {job["seed"]: job.get("rule_family") for job in plan}


def write_sample_index(
    destination: Path, receipts: list[dict], rule_families: dict[int, str | None]
) -> str:
    """The one index every downstream tool reads (gate, arms, budget, holdouts).

    One line per exported task, covering the WHOLE bank (train+eval); the
    collapse gate counts only split == train rows. group_id is the world_id —
    the simulated world is the document analog, so exposure measures world
    atomicity, not source breadth. Field names are literal: the gate reads
    d.get("group_id") and d.get("full_message_tokens").
    """
    path = destination / "sample_index.jsonl"
    rows = 0
    with path.open("x") as stream:
        for receipt in receipts:
            shard = destination / "shards" / receipt["shard_id"]
            source = shard / "rows.jsonl"
            if sha(source) != receipt["files"]["rows.jsonl"]:
                raise RuntimeError("shard content changed before export")
            for line in source.read_text().splitlines():
                row = json.loads(line)
                task = _task_program_signature(row)
                index_row = {
                    "example_id": row["example_id"],
                    "semantic_task_id": row["example_id"],
                    "world_id": row["world_id"],
                    "group_id": row["world_id"],
                    "family": row["family"],
                    "rule_structure_id": rule_families.get(row["world_seed"]),
                    "program_signature": task,
                    "language": "en",
                    "renderer": "jsonl",
                    "length_records": row["length_records"],
                    "depth": row["depth"],
                    "consumed_records": row["consumed_records"],
                    "token_target": row["target"]["token_target"],
                    "input_tokens": row["input_tokens"],
                    "supervised_tokens": row["supervised_tokens"],
                    "full_message_tokens": row["full_chat_tokens"],
                    "output_file": f"{row['split']}.jsonl",
                    "row_index": rows,
                    "split": row["split"],
                    "admission_status": "completed",
                }
                stream.write(canonical(index_row) + "\n")
                rows += 1
    return sha(path)


def _task_program_signature(row: dict) -> str:
    """A masked signature of the typed program: op chain plus arity, no values.

    The renderer/CF variants of one semantic task share this signature, so
    arm extraction can tell 'same task, new expression' from a new task.
    """
    assistant = row["messages"][1]["content"]
    signature = re.sub(r'"[^"]*"', '"S"', assistant)
    signature = re.sub(r"\b\d+(?:\.\d+)?\b", "N", signature)
    return re.sub(r"\s+", " ", signature).strip()[:120]


def run_shard(
    job: dict, token_target: int, output: str, fingerprint: str, resume: bool = False
) -> dict:
    shard_id = (
        f"{job['family']}-h{job['depth']}-L{job['length_records']}"
        f"-seed{job['seed']}-{token_target}"
    )
    shard = Path(output) / "shards" / shard_id
    if shard.exists():
        # Resume adopts a completed shard instead of discarding it: before
        # this, a --resume wave re-filed every existing shard as a reject and
        # a finalized bank could not resume at all (train.jsonl opens with
        # "x"). Adoption is strict: the receipt must exist, match this
        # fingerprint, and re-hash both files; anything else stays an error.
        if not resume:
            raise ValueError(
                "shard directory already exists; resume needs a fresh output"
            )
        try:
            receipt = json.loads((shard / "receipt.json").read_text())
        except FileNotFoundError as error:
            raise ValueError(
                f"incomplete shard {shard_id} exists without a receipt; "
                "it cannot be adopted and the destination must be recreated"
            ) from error
        if receipt.get("fingerprint") != fingerprint:
            raise ValueError(f"shard {shard_id} belongs to a different state")
        for name in ("world.json", "rows.jsonl"):
            if sha(shard / name) != receipt["files"][name]:
                raise ValueError(f"shard {shard_id} content changed under the receipt")
        return receipt
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
        "rule_family": job.get("rule_family"),
        "token_target": token_target,
        "rows": len(rows),
        "length_records": bundle["length_records"],
        "consumed_rows_per_task": bundle["length_accounting"]["consumed_rows_per_task"],
        "padding_rows": bundle["length_accounting"]["padding_rows"],
        "decoy_padding_rows": bundle["length_accounting"]["decoy_padding_rows"],
        "reference_rows": bundle["length_accounting"]["reference_rows"],
        "instruction_variants": len({row["messages"][0]["content"] for row in rows}),
        "input_tokens": [row["input_tokens"] for row in rows],
        "full_chat_tokens": [row["full_chat_tokens"] for row in rows],
        "supervised_tokens": sum(row["supervised_tokens"] for row in rows),
        "validation": module_for(job["family"]).validate_bundle(bundle),
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
    structure_rows: dict[str, dict[str, int]] = {}
    for receipt in receipts:
        if receipt.get("rule_family") is None:
            continue
        bucket = structure_rows.setdefault(
            receipt["rule_family"], {"train": 0, "eval": 0}
        )
        bucket[receipt["split"]] += receipt["rows"]
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
        "split_basis": {
            "default": "seed_mod_5",
            "rule_holdout": (
                "rule_structure: trained_rule_family worlds all train, the "
                "other structure's worlds are all eval. Each world's local "
                "rule.holdout block is self-relative and does not decide the "
                "export split."
            ),
            "rule_structure_rows": structure_rows,
        },
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
        "longworld/synthesis/capability_families.py",
        "longworld/synthesis/capability_unanswerable_adapter.py",
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
                run_shard,
                job,
                job["token_target"],
                str(destination),
                fingerprint,
                resume,
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
    if any(sha(ROOT / name) != digest for name, digest in state["code_sha256"].items()):
        raise RuntimeError("code changed during run; completion forbidden")
    # rejects/infeasible are derived ledgers recomputed from the same state, so
    # a resumed run rewrites them (atomically) rather than hard-failing on the
    # link — unlike receipts and exports, they carry no first-write authority.
    write_replace_json(destination / "rejects.json", rejects)
    write_replace_json(destination / "infeasible.json", skips)
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
    files["sample_index.jsonl"] = write_sample_index(
        destination, receipts, manifest_rule_families(plan)
    )
    manifest = completion_manifest(receipts, plan, files, skips)
    manifest["budget_recommendation"] = {
        **derive_budget_report([destination / "train.jsonl"], BUDGET_GBS)[
            "budget_recommendation"
        ],
        "gbs": BUDGET_GBS,
        "rule": "steps = floor(epochs x train_rows / GBS); budget is an upper-bound recommendation, not a training license",
    }
    write_new_json(
        destination / "verification.json",
        write_solver_verification(destination, receipts, state["code_sha256"]),
    )
    manifest["verification"] = "verification.json"
    write_new_json(destination / "manifest.json", manifest)
    return manifest


def write_solver_verification(
    destination: Path, receipts: list[dict], code_sha256: dict
) -> dict:
    """The independent solver re-check as an in-bank receipt, not a commit note.

    Every exported task's assistant answer is re-derived from the stored
    world's visible context through the owning module's solve path; the
    3,476/3,476 claim of the v2 bank lived only in the commit message.
    A join_unanswerable task's expected outcome is the executor's REFUSAL
    (completion A: the join is undetermined over the visible world), so the
    solver raising ValueError there is the pass condition — the same signal
    the two-world certificate records.
    """
    checked = mismatches = 0
    for receipt in receipts:
        shard = destination / "shards" / receipt["shard_id"]
        bundle = json.loads((shard / "world.json").read_text())
        rows = (shard / "rows.jsonl").read_text().splitlines()
        for line in rows:
            row = json.loads(line)
            task_id = row["example_id"].split(":")[-1]
            task = next(t for t in bundle["tasks"] if t["task_id"] == task_id)
            module = module_for(row["family"])
            try:
                solved = module.solve_visible(bundle["context"], task["question"])
            except ValueError:
                # The undetermined-answer refusal: the stored answer must be
                # the UNKNOWN marker for this to count as verified.
                if row["messages"][1]["content"] != canonical("UNKNOWN"):
                    mismatches += 1
                checked += 1
                continue
            checked += 1
            if canonical(solved) != row["messages"][1]["content"]:
                mismatches += 1
    return {
        "solver_recheck": {
            "checked": checked,
            "mismatches": mismatches,
            "passed": mismatches == 0,
            "method": "solve_visible re-derivation over stored worlds, per task",
        },
        "code_sha256": code_sha256,
    }


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
