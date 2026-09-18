"""Stratified multi-QA generation; resume reuses complete verified shards only.

Two answer contracts are packaged here, chosen per family:

* `joint` — every question of the world in one user turn and one assistant
  JSON object. Only families whose answers are independent per entity/alias
  (ledger, reservation) and the affine rule family may use it.
* `split` — one supervised question per row, each with its own context prefix.
  The workflow family must use it: its answers form one reversible value chain
  (value = ((prior + payload) * multiplier) % 100003 with depends_on = the
  previous record), so a joint row lets a decoder read answer k off answer
  k-1 plus the partition slice without the long read.

Because the packaging is part of the world identity and of the row schema,
an output directory written by the previous (v2) schema is not resumable:
choose a fresh output directory.
"""

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
# Packaging is a property of the family, not of a run: the workflow family is
# one-question-per-row and every other family stays joint.
PACKAGING = {
    "ledger": "joint",
    "reservation": "joint",
    "rule_learning": "joint",
    "workflow": "split",
}
QUESTIONS_PER_ROW = {"joint": 16, "split": 1}
SCHEMA = "longworld.multiqa-curriculum.v3"


def compiler(family):
    return importlib.import_module("longworld.synthesis." + FAMILIES[family])


# Families whose module does not own the accessor still have to state how their
# answers stand to the visible context. ledger/reservation answers are one
# per-entity reduction over that entity's own records, so they are independent
# by construction -- the same claim capability_curriculum's own lineage makes
# with "qa_layout": "independent_branches".
JOINT_INDEPENDENT_DEPENDENCY = {
    "answer_dependency_mode": "independent",
    "basis": "one question per entity/alias partition; no answer shares evidence with another",
    "visible_prefix_self_contained": True,
}


def dependency_metadata(family, packaging):
    module = compiler(family)
    accessor = getattr(module, "dependency_metadata", None)
    if accessor is None:
        if packaging != "joint":
            raise ValueError("split packaging requires a module dependency accessor")
        return dict(JOINT_INDEPENDENT_DEPENDENCY)
    return accessor(family, packaging)


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


def build_messages(context, tasks, topic, packaging="joint", supervised=None):
    """One visible context plus the assistant answer(s) this row supervises.

    `joint` supervises every question in one assistant JSON object. `split`
    supervises exactly one question (`supervised` names it) and fails closed if
    any sibling gold string would still appear in that assistant message.
    """
    ids = [task["task_id"] for task in tasks]
    if not tasks or len(set(ids)) != len(ids):
        raise ValueError("missing or duplicate question IDs")
    if any(context in task["prompt"] for task in tasks):
        raise ValueError("question prompt duplicates context")
    if packaging == "joint":
        supervised_rows = list(tasks)
    elif packaging == "split":
        if supervised not in ids:
            raise ValueError("split row requires one supervised question ID")
        supervised_rows = [task for task in tasks if task["task_id"] == supervised]
    else:
        raise ValueError("unknown packaging mode")
    queries = [
        {"id": t["task_id"], "question": t["question"], "instruction": t["prompt"]}
        for t in supervised_rows
    ]
    metadata = (
        "TOPIC METADATA (sampling label; all events are simulated):\n"
        + base.canonical(topic)
        + "\n"
        if topic
        else ""
    )
    assistant = base.canonical(
        {t["task_id"]: t["answer"] for t in supervised_rows}
    )
    # Fail closed on the P0-3 leak: a split row must not hand the model any
    # other question's gold string, whatever the answer shapes turn out to be.
    if packaging == "split":
        for task in tasks:
            if task["task_id"] == supervised:
                continue
            if base.canonical(task["answer"]) in assistant:
                raise ValueError("split row exposes another question's gold answer")
    # The closing line is the family module's constant: the auditor parses the
    # user turn by stripping exactly this suffix. The per-question wording lives
    # in the question payload above, where the auditor reads it.
    return [
        {
            "role": "user",
            "content": metadata
            + context
            + "\n\nQUESTIONS\n"
            + base.canonical(queries)
            + compiler("workflow").ANSWER_SUFFIX,
        },
        {"role": "assistant", "content": assistant},
    ]


def row_context(module, variant, task, packaging):
    """Visible context of one row: the whole world, or that question's prefix."""
    if packaging == "joint":
        return variant["context"]
    return module.workflow_context(
        json.loads(variant["context"]), task["question"]["cutoff"]
    )


def fit_world(job, capacity, tokenizer):
    """Grow the record count until every row is input-long and complete.

    Under `split` each row is length-fitted at its own prefix length: the
    shortest prefix is the hardest one to keep above 90% of capacity, so the
    record count is driven by the minimum over rows, not by the full world.
    """
    module = compiler(job["family"])
    packaging = PACKAGING[job["family"]]
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
            for task in tasks if packaging == "split" else [None]:
                supervised = None if task is None else task["task_id"]
                context = row_context(module, variant, task, packaging) if task else variant["context"]
                rows.append(
                    _row(
                        job,
                        capacity,
                        bundle["world_id"],
                        variant,
                        tasks,
                        context,
                        packaging,
                        view,
                        supervised,
                        count,
                    )
                )
        for row in rows:
            full = base._render_chat(
                tokenizer, row["messages"], generation_prompt=False
            )
            prompt = base._render_chat(
                tokenizer, row["messages"][:-1], generation_prompt=True
            )
            row["full_chat_tokens"] = len(tokenizer(full, truncation=False)["input_ids"])
            row["input_tokens"] = len(tokenizer(prompt, truncation=False)["input_ids"])
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


def _row(job, capacity, world_id, variant, tasks, context, packaging, view, supervised, count):
    """One exported row; the row's own packaging decides its supervision."""
    messages = build_messages(context, tasks, job["topic"], packaging, supervised)
    supervised_tasks = [
        t for t in tasks if supervised is None or t["task_id"] == supervised
    ]
    per_row = len(supervised_tasks)
    tag = "multiqa16" if supervised is None else f"single-{supervised}"
    metadata = dependency_metadata(job["family"], packaging)
    return {
        "schema_version": SCHEMA,
        "example_id": f"{world_id}:{view}:{tag}",
        "world_id": world_id,
        "world_seed": job["seed"],
        "family": job["family"],
        "topic": job["topic"],
        "split": job["split"],
        "view": view,
        "packaging": packaging,
        "questions_per_row": per_row,
        "supervised_question_id": supervised,
        "messages": messages,
        "qa_count": per_row,
        "capabilities": dict(Counter(t["capability"] for t in supervised_tasks)),
        "dependency_metadata": metadata,
        "answer_dependency_mode": metadata["answer_dependency_mode"],
        "capacity_tokens": capacity,
        "context_chars": len(context),
        "n_records": count,
        "strict_long_dependency_verified": False,
        "production_eligible": False,
    }


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
        packaging = PACKAGING[receipt["family"]]
        if receipt["packaging"] != packaging:
            raise ValueError("cached shard packaging does not match the family")
        rows = [
            json.loads(line) for line in (shard / "rows.jsonl").read_text().splitlines()
        ]
        if len(rows) != 2 * receipt["rows_per_view"] or {
            row["view"] for row in rows
        } != {"factual", "counterfactual"}:
            raise ValueError("cached rows are incomplete")
        for row in rows:
            variant = bundle if row["view"] == "factual" else bundle["counterfactual"]
            task = next(
                (
                    t
                    for t in variant["tasks"]
                    if t["task_id"] == row["supervised_question_id"]
                ),
                None,
            )
            context = (
                variant["context"]
                if task is None
                else row_context(module, variant, task, packaging)
            )
            if (
                row["schema_version"] != SCHEMA
                or row["packaging"] != packaging
                or row["questions_per_row"] != QUESTIONS_PER_ROW[packaging]
                or row["messages"]
                != build_messages(
                    context,
                    variant["tasks"],
                    row["topic"],
                    packaging,
                    row["supervised_question_id"],
                )
                or row["world_id"] != bundle["world_id"]
                or row["world_seed"] != bundle["seed"]
                or row["family"] != bundle["family"]
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
        "packaging": PACKAGING[job["family"]],
        "questions_per_row": QUESTIONS_PER_ROW[PACKAGING[job["family"]]],
        "n_records": rows[0]["n_records"],
        "rows": len(rows),
        "rows_per_view": len(rows) // 2,
        "dependency_metadata": rows[0]["dependency_metadata"],
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


def completion_manifest(receipts, plan, fingerprint, files):
    split_worlds = {
        split: {r["world_seed"] for r in receipts if r["split"] == split}
        for split in ("train", "eval")
    }
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
    return {
        "schema_version": SCHEMA,
        "status": "local_symbolic_curriculum_complete",
        "fingerprint": fingerprint,
        "completed_shards": len(receipts),
        "world_seed_groups": len(plan),
        "semantic_families": sorted({r["family"] for r in receipts}),
        # Packaging is a per-family contract, recorded here so a consumer can
        # see how many questions each row supervises and how the answer stands
        # to the visible context (independent vs dependency_given).
        "packaging": {
            family: {
                "packaging": receipt["packaging"],
                "questions_per_row": receipt["questions_per_row"],
                **receipt["dependency_metadata"],
            }
            for family, receipt in sorted(
                {r["family"]: r for r in receipts}.items()
            )
        },
        "rows_per_family": {
            family: sum(r["rows"] for r in receipts if r["family"] == family)
            for family in sorted({r["family"] for r in receipts})
        },
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
            split: sum(r["rows"] for r in receipts if r["split"] == split)
            for split in ("train", "eval")
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


def validate_completed_manifest(destination, manifest, expected):
    required = {
        "plan.json",
        "shards",
        "rejects.json",
        "train.jsonl",
        "eval.jsonl",
        "manifest.json",
    }
    if (
        {path.name for path in destination.iterdir()} != required
        or any(path.is_symlink() for path in destination.iterdir())
        or json.loads((destination / "rejects.json").read_text()) != []
        or manifest != expected
    ):
        raise ValueError("completed manifest or export inventory mismatch")
    for name, digest in expected["files"].items():
        if base.sha(destination / name) != digest:
            raise ValueError("completed export hash mismatch")


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
    receipts.sort(key=lambda row: row["shard_id"])
    if (destination / "manifest.json").exists():
        # Resume must recheck every shard before trusting an existing completion.
        manifest = json.loads((destination / "manifest.json").read_text())
        if rejects:
            raise ValueError("resume audit failed")
        files = {
            name: base.sha(destination / name) for name in ("train.jsonl", "eval.jsonl")
        }
        expected = completion_manifest(receipts, plan, fingerprint, files)
        validate_completed_manifest(destination, manifest, expected)
        return manifest
    base.write_new_json(destination / "rejects.json", rejects)
    if rejects:
        raise RuntimeError(
            f"{len(rejects)} rejected shards; no complete training manifest"
        )
    files = {}
    seen_ids = set()
    for split in ("train", "eval"):
        path = destination / f"{split}.jsonl"
        with path.open("x") as stream:
            for receipt in receipts:
                if receipt["split"] != split:
                    continue
                shard = destination / "shards" / receipt["shard_id"]
                load_cached(shard, fingerprint)
                for line in (shard / "rows.jsonl").read_text().splitlines():
                    row = json.loads(line)
                    if row["example_id"] in seen_ids:
                        raise ValueError("duplicate exported example")
                    seen_ids.add(row["example_id"])
                    stream.write(line + "\n")
        files[path.name] = base.sha(path)
    manifest = completion_manifest(receipts, plan, fingerprint, files)
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
