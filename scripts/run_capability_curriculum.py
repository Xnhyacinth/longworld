"""Stratified multi-QA generation; resume reuses complete verified shards only."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import multiprocessing
import os
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_capability_world_pipeline as base
from scripts.fetch_capability_topics import validate_topics

FAMILIES = {
    "ledger": "capability_curriculum",
    "reservation": "capability_curriculum",
    "rule_learning": "capability_rules_workflow",
    "workflow": "capability_rules_workflow",
}
SCHEMA = "longworld.multiqa-curriculum.v2"


def compiler(family):
    return importlib.import_module("longworld.synthesis." + FAMILIES[family])


def make_plan(topics, config):
    validate_topics(topics)
    grouped = defaultdict(list)
    for topic in sorted(topics, key=lambda x: x["id"]):
        grouped[topic["domain"]["id"]].append(topic)
    rng = random.Random(config["sampler_seed"])
    plan = []
    count = config["topics_per_domain"]
    if (
        count < 2
        or not config["families"]
        or len(set(config["families"])) != len(config["families"])
    ):
        raise ValueError("need distinct families and >=2 topics per domain")
    for domain in sorted(grouped):
        fields = defaultdict(list)
        for topic in grouped[domain]:
            fields[topic["field"]["id"]].append(topic)
        field_ids = sorted(fields)
        rng.shuffle(field_ids)
        for values in fields.values():
            rng.shuffle(values)
        candidates = []
        while len(candidates) < count and any(fields.values()):
            for field in field_ids:
                if fields[field]:
                    candidates.append(fields[field].pop())
        if len(candidates) < count:
            raise ValueError("insufficient topics in domain")
        for index, topic in enumerate(candidates[:count]):
            split = "eval" if index % 4 == 0 else "train"
            for family in config["families"]:
                if family not in FAMILIES:
                    raise ValueError("unknown family")
                plan.append(
                    {
                        "seed": config["world_seed_base"] + len(plan),
                        "family": family,
                        "topic": topic,
                        "split": split,
                    }
                )
    return plan


def build_messages(context, tasks, topic):
    ids = [task["task_id"] for task in tasks]
    if not tasks or len(set(ids)) != len(ids):
        raise ValueError("missing or duplicate question IDs")
    if any(context in task["prompt"] for task in tasks):
        raise ValueError("question prompt duplicates context")
    queries = [
        {"id": t["task_id"], "question": t["question"], "instruction": t["prompt"]}
        for t in tasks
    ]
    metadata = (
        "TOPIC METADATA (sampling label; all events are simulated):\n"
        + base.canonical(topic)
        + "\n"
        if topic
        else ""
    )
    return [
        {
            "role": "user",
            "content": metadata
            + context
            + "\n\nQUESTIONS\n"
            + base.canonical(queries)
            + "\nReturn one JSON object mapping every question id to its answer.",
        },
        {
            "role": "assistant",
            "content": base.canonical({t["task_id"]: t["answer"] for t in tasks}),
        },
    ]


def fit_world(job, capacity, tokenizer):
    module = compiler(job["family"])
    count = 120
    seen = set()
    for _ in range(12):
        if count in seen:
            raise ValueError("length fit stalled; no truncation fallback")
        seen.add(count)
        bundle = module.generate_bundle(
            job["seed"], job["family"], count, 16, job["topic"]
        )
        if len(seen) == 1:
            check = module.validate_bundle(bundle)
            if not check["passed"]:
                raise ValueError(f"small-world validation failed: {check['errors']}")
        rows = []
        for view, variant in (
            ("factual", bundle),
            ("counterfactual", bundle["counterfactual"]),
        ):
            tasks = variant["tasks"]
            if (
                len(tasks) != 16
                or len({base.canonical(t["question"]) for t in tasks}) != 16
            ):
                raise ValueError("expected 16 distinct questions")
            messages = build_messages(variant["context"], tasks, job["topic"])
            full = base._render_chat(tokenizer, messages, generation_prompt=False)
            prompt = base._render_chat(tokenizer, messages[:-1], generation_prompt=True)
            full_tokens = len(tokenizer(full, truncation=False)["input_ids"])
            input_tokens = len(tokenizer(prompt, truncation=False)["input_ids"])
            rows.append(
                {
                    "schema_version": SCHEMA,
                    "example_id": f"{bundle['world_id']}:{view}:multiqa16",
                    "world_id": bundle["world_id"],
                    "world_seed": job["seed"],
                    "family": job["family"],
                    "topic": job["topic"],
                    "split": job["split"],
                    "view": view,
                    "messages": messages,
                    "qa_count": len(tasks),
                    "capabilities": dict(Counter(t["capability"] for t in tasks)),
                    "capacity_tokens": capacity,
                    "full_chat_tokens": full_tokens,
                    "input_tokens": input_tokens,
                    "n_records": count,
                    "strict_long_dependency_verified": False,
                    "production_eligible": False,
                }
            )
        if all(
            math.ceil(capacity * 0.90) <= row["input_tokens"]
            and row["full_chat_tokens"] <= capacity
            for row in rows
        ):
            check = module.validate_bundle(bundle)
            if not check["passed"]:
                raise ValueError(f"full-world validation failed: {check['errors']}")
            for row in rows:
                encoded = base.tokenize_assistant_only(
                    tokenizer, row["messages"], capacity
                )
                row["supervised_tokens"] = sum(x != -100 for x in encoded["labels"])
                row["assistant_only_preprocessing_verified"] = True
                row["local_symbolic_validation_passed"] = True
            return bundle, rows, check
        maximum = max(row["full_chat_tokens"] for row in rows)
        if (
            count > 200
            and min(row["input_tokens"] / row["full_chat_tokens"] for row in rows)
            < 0.90
        ):
            raise ValueError("output dominates length; input-long contract cannot fit")
        proposed = max(32, round(count * (capacity * 0.975) / maximum))
        count = (
            proposed if proposed != count else count + (1 if maximum < capacity else -1)
        )
    raise ValueError("length fitting exhausted")


def load_cached(shard, fingerprint, *, semantic=False):
    receipt_file = shard / "receipt.json"
    if not receipt_file.is_file():
        raise ValueError(
            "incomplete shard: preserve it and choose a new output directory"
        )
    receipt = json.loads(receipt_file.read_text())
    if receipt["fingerprint"] != fingerprint:
        raise ValueError("cached shard fingerprint mismatch")
    for name, digest in receipt["files"].items():
        if Path(name).name != name or base.sha(shard / name) != digest:
            raise ValueError("cached shard content mismatch")
    if set(receipt["files"]) != {"world.json", "rows.jsonl"}:
        raise ValueError("cached shard required payload missing")
    if semantic:
        bundle = json.loads((shard / "world.json").read_text())
        module = compiler(receipt["family"])
        if not module.validate_bundle(bundle)["passed"]:
            raise ValueError("cached world semantic validation failed")
        rows = [
            json.loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        if len(rows) != 2 or {row["view"] for row in rows} != {
            "factual",
            "counterfactual",
        }:
            raise ValueError("cached rows are incomplete")
        for row in rows:
            variant = bundle if row["view"] == "factual" else bundle["counterfactual"]
            if (
                row["messages"]
                != build_messages(variant["context"], variant["tasks"], row["topic"])
                or row["world_id"] != bundle["world_id"]
                or row["world_seed"] != bundle["seed"]
                or row["family"] != bundle["family"]
                or row["qa_count"] != 16
                or row["split"] != receipt["split"]
                or row["topic"]["id"] != receipt["topic_id"]
            ):
                raise ValueError("cached row and world mismatch")
            encoded = base.tokenize_assistant_only(
                base._TOKENIZER, row["messages"], receipt["capacity_tokens"]
            )
            if (
                len(encoded["input_ids"]) != row["full_chat_tokens"]
                or sum(x != -100 for x in encoded["labels"]) != row["supervised_tokens"]
            ):
                raise ValueError("cached token/mask count mismatch")
    return receipt


def run_shard(job, capacity, output, fingerprint):
    shard_id = f"{job['family']}-seed{job['seed']}-{capacity}"
    shard = Path(output) / "shards" / shard_id
    if shard.exists():
        receipt = load_cached(shard, fingerprint, semantic=True)
        if (
            receipt["family"] != job["family"]
            or receipt["world_seed"] != job["seed"]
            or receipt["topic_id"] != job["topic"]["id"]
            or receipt["split"] != job["split"]
            or receipt["capacity_tokens"] != capacity
        ):
            raise ValueError("cached receipt differs from scheduled job")
        return receipt
    shard.mkdir()
    bundle, rows, check = fit_world(job, capacity, base._TOKENIZER)
    base.write_new_json(shard / "world.json", bundle)
    with (shard / "rows.jsonl").open("x") as stream:
        for row in rows:
            stream.write(base.canonical(row) + "\n")
    receipt = {
        "fingerprint": fingerprint,
        "shard_id": shard_id,
        "worker_pid": os.getpid(),
        "world_id": bundle["world_id"],
        "world_seed": job["seed"],
        "family": job["family"],
        "topic_id": job["topic"]["id"],
        "domain_id": job["topic"]["domain"]["id"],
        "split": job["split"],
        "capacity_tokens": capacity,
        "n_records": rows[0]["n_records"],
        "rows": len(rows),
        "qa_pairs": sum(row["qa_count"] for row in rows),
        "input_tokens": [row["input_tokens"] for row in rows],
        "full_chat_tokens": [row["full_chat_tokens"] for row in rows],
        "supervised_tokens": sum(row["supervised_tokens"] for row in rows),
        "validation": check,
        "rule_structure_signature": bundle["lineage"].get("rule_structure_signature"),
        "files": {
            name: base.sha(shard / name) for name in ("world.json", "rows.jsonl")
        },
    }
    base.write_new_json(shard / "receipt.json", receipt)
    return receipt


def run(config_path, destination, resume=False):
    config = json.loads(config_path.read_text())
    taxonomy_path = ROOT / config["taxonomy_path"]
    if base.sha(taxonomy_path) != config["taxonomy_sha256"]:
        raise ValueError("taxonomy snapshot hash mismatch")
    capacities = config["capacities"]
    if (
        not capacities
        or len(set(capacities)) != len(capacities)
        or set(capacities) - {65536, 131072, 262144}
    ):
        raise ValueError("invalid capacities")
    if config["workers"] < 1:
        raise ValueError("workers must be positive")
    plan = make_plan(json.loads(taxonomy_path.read_text())["topics"], config)
    paths = [
        "scripts/run_capability_curriculum.py",
        "scripts/fetch_capability_topics.py",
        "scripts/run_capability_world_pipeline.py",
        "scripts/train_sft.py",
        "longworld/core/world.py",
        "longworld/core/state.py",
        "longworld/synthesis/capability_curriculum.py",
        "longworld/synthesis/capability_rules_workflow.py",
    ]
    state = {
        "config": config,
        "plan": plan,
        "code_sha256": {name: base.sha(ROOT / name) for name in paths},
        "tokenizer_assets_sha256": base.resolved_tokenizer_asset_manifest_sha256(
            base.MODEL, base.REVISION
        ),
    }
    fingerprint = hashlib.sha256(base.canonical(state).encode()).hexdigest()
    destination = destination.resolve()
    if destination.exists():
        if not resume or json.loads((destination / "plan.json").read_text()) != state:
            raise ValueError(
                "existing output requires --resume and identical config/code"
            )
    else:
        destination.mkdir(parents=True)
        (destination / "shards").mkdir()
        base.write_new_json(destination / "plan.json", state)
    receipts, rejects = [], []
    with ProcessPoolExecutor(
        max_workers=config["workers"],
        mp_context=multiprocessing.get_context("spawn"),
        initializer=base._init_worker,
    ) as pool:
        futures = {
            pool.submit(run_shard, job, capacity, str(destination), fingerprint): (
                job,
                capacity,
            )
            for job in plan
            for capacity in capacities
        }
        for future in as_completed(futures):
            job, capacity = futures[future]
            try:
                receipt = future.result()
                receipts.append(receipt)
                print(
                    base.canonical(
                        {
                            "complete": receipt["shard_id"],
                            "qa_pairs": receipt["qa_pairs"],
                            "pid": receipt["worker_pid"],
                        }
                    ),
                    flush=True,
                )
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
                        "capacity": capacity,
                        "error": str(error),
                    }
                )
                print(base.canonical({"rejected": rejects[-1]}), flush=True)
    if any(
        base.sha(ROOT / name) != digest for name, digest in state["code_sha256"].items()
    ):
        raise RuntimeError("code changed during run; completion forbidden")
    if (destination / "manifest.json").exists():
        # Resume must recheck every shard before trusting an existing completion.
        manifest = json.loads((destination / "manifest.json").read_text())
        if rejects or len(receipts) != manifest["completed_shards"]:
            raise ValueError("resume audit failed")
        for name, digest in manifest["files"].items():
            if base.sha(destination / name) != digest:
                raise ValueError("completed export hash mismatch")
        return manifest
    receipts.sort(key=lambda row: row["shard_id"])
    base.write_new_json(destination / "rejects.json", rejects)
    if rejects:
        raise RuntimeError(
            f"{len(rejects)} rejected shards; no complete training manifest"
        )
    files = {}
    seen_ids, split_worlds = set(), defaultdict(set)
    for split in ("train", "eval"):
        path = destination / f"{split}.jsonl"
        with path.open("x") as stream:
            for receipt in receipts:
                if receipt["split"] != split:
                    continue
                split_worlds[split].add(receipt["world_seed"])
                shard = destination / "shards" / receipt["shard_id"]
                load_cached(shard, fingerprint)
                for line in (shard / "rows.jsonl").read_text().splitlines():
                    row = json.loads(line)
                    if row["example_id"] in seen_ids:
                        raise ValueError("duplicate exported example")
                    seen_ids.add(row["example_id"])
                    stream.write(line + "\n")
        files[path.name] = base.sha(path)
    if split_worlds["train"] & split_worlds["eval"]:
        raise ValueError("world split collision")
    topic_sets = {
        split: {r["topic_id"] for r in receipts if r["split"] == split}
        for split in ("train", "eval")
    }
    if topic_sets["train"] & topic_sets["eval"]:
        raise ValueError("topic split collision")
    rule_sets = {
        split: {
            r["rule_structure_signature"]
            for r in receipts
            if r["split"] == split and r["rule_structure_signature"]
        }
        for split in ("train", "eval")
    }
    manifest = {
        "schema_version": SCHEMA,
        "status": "local_symbolic_curriculum_complete",
        "fingerprint": fingerprint,
        "completed_shards": len(receipts),
        "world_seed_groups": len(plan),
        "semantic_families": sorted({r["family"] for r in receipts}),
        "sampled_topics": len({r["topic_id"] for r in receipts}),
        "sampled_domains": len({r["domain_id"] for r in receipts}),
        "sampled_fields": len({j["topic"]["field"]["id"] for j in plan}),
        "sampled_subfields": len({j["topic"]["subfield"]["id"] for j in plan}),
        "topic_scope": "real taxonomy metadata; domain-specific physics not established",
        "rows": sum(r["rows"] for r in receipts),
        "qa_pairs": sum(r["qa_pairs"] for r in receipts),
        "full_chat_tokens": sum(sum(r["full_chat_tokens"]) for r in receipts),
        "supervised_tokens": sum(r["supervised_tokens"] for r in receipts),
        "split_rows": {
            s: sum(r["rows"] for r in receipts if r["split"] == s)
            for s in ("train", "eval")
        },
        "rule_structure_overlap": sorted(rule_sets["train"] & rule_sets["eval"]),
        "rule_split_scope": "coefficient-instance overlap only; structural families shared between splits",
        "worker_pids": sorted({r["worker_pid"] for r in receipts}),
        "strict_long_dependency_verified": False,
        "framework_release_ready": False,
        "production_eligible": False,
        "model_utility_measured": False,
        "files": files,
        "shards": receipts,
    }
    base.write_new_json(destination / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output, args.resume)
    print(
        base.canonical(
            {
                k: result[k]
                for k in (
                    "status",
                    "rows",
                    "qa_pairs",
                    "supervised_tokens",
                    "worker_pids",
                )
            }
        )
    )
