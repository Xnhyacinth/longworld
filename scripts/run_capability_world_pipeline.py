#!/usr/bin/env python3
"""Local simulated capability pilot; never a historical strict/release exporter."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from longworld.synthesis.capability_world import generate_bundle, validate_bundle
from scripts.train_sft import _render_chat, tokenize_assistant_only

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
SCHEMA = "longworld.capability-pilot.v1"
_TOKENIZER = None


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


def split_for_seed(seed):
    # Every length and presentation of a seed belongs to the same split.
    return "eval" if seed % 5 == 0 else "train"


def build_messages(context, task):
    return [
        {"role": "user", "content": context + "\n\nQUESTION\n" + task["prompt"]},
        {"role": "assistant", "content": canonical(task["answer"])},
    ]


def _variants(bundle):
    for view, variant in (
        ("factual", bundle),
        ("counterfactual", bundle["counterfactual"]),
    ):
        for task in variant["tasks"]:
            yield view, variant, task


def evidence_geometry(context, task, tokenizer):
    """Observed coverage only; these are not proofs of a minimal necessary set."""
    lines = context.splitlines()
    events = [json.loads(line) for line in lines[1:]]
    alias = task["question"]["alias"]
    binding = next(
        event
        for event in events
        if event["type"] == "bind" and event["params"]["alias"] == alias
    )
    entity = binding["params"]["entity"]
    relevant = [
        (index + 1, event)
        for index, event in enumerate(events)
        if event["params"]["entity"] == entity
    ]
    records = [(index, event) for index, event in relevant if event["type"] == "record"]
    if task["capability"] == "recall_binding":
        end = records[task["question"]["ordinal"] - 1][0]
        covered = [
            (index, event)
            for index, event in relevant
            if index <= end and event["type"] in ("bind", "record")
        ]
    elif task["capability"] == "dense_aggregation":
        covered = [
            (index, event)
            for index, event in relevant
            if event["type"] in ("bind", "record")
        ]
    else:
        covered = relevant
    start, end = covered[0][0], covered[-1][0]
    return {
        "interpretation": "observed coverage, not minimal necessity",
        "covered_events": len(covered),
        "context_events": len(events),
        "covered_records": sum(event["type"] == "record" for _, event in covered),
        "first_line": start,
        "last_line": end,
        "span_tokens": len(
            tokenizer("\n".join(lines[start : end + 1]), add_special_tokens=False)[
                "input_ids"
            ]
        ),
    }


def fit_bundle(seed, domain, capacity, tokenizer):
    """Grow complete event histories, accepting only [95%,100%] full-chat lengths."""
    if capacity < 4096:
        raise ValueError("capacity too small for the capability pilot")
    n_records = 24
    seen = set()
    for _ in range(12):
        if n_records in seen:
            raise ValueError("capacity fitting stalled; no truncated fallback")
        seen.add(n_records)
        bundle = generate_bundle(seed, domain=domain, n_records=n_records)
        if len(seen) == 1:
            preflight = validate_bundle(bundle)
            if not preflight["passed"]:
                raise ValueError(f"semantic preflight failed: {preflight['errors']}")
        chats = [
            (view, task, build_messages(variant["context"], task))
            for view, variant, task in _variants(bundle)
        ]
        lengths = [
            len(
                tokenizer(
                    _render_chat(tokenizer, messages, generation_prompt=False),
                    truncation=False,
                )["input_ids"]
            )
            for _, _, messages in chats
        ]
        if min(lengths) >= math.ceil(capacity * 0.95) and max(lengths) <= capacity:
            validation = validate_bundle(bundle)
            if not validation["passed"]:
                raise ValueError(f"semantic preflight failed: {validation['errors']}")
            rows = []
            for view, task, messages in chats:
                encoded = tokenize_assistant_only(tokenizer, messages, capacity)
                supervised = sum(label != -100 for label in encoded["labels"])
                context = (
                    bundle["context"]
                    if view == "factual"
                    else bundle["counterfactual"]["context"]
                )
                rows.append(
                    {
                        "schema_version": SCHEMA,
                        "example_id": f"{bundle['world_id']}:{view}:{task['task_id']}",
                        "world_id": bundle["world_id"],
                        "seed": seed,
                        "task_id": task["task_id"],
                        "capability": task["capability"],
                        "view": view,
                        "split": split_for_seed(seed),
                        "capacity_tokens": capacity,
                        "record_count_parameter": n_records,
                        "full_chat_tokens": len(encoded["input_ids"]),
                        "supervised_tokens": supervised,
                        "prompt_tokens": len(encoded["input_ids"]) - supervised,
                        "messages": messages,
                        "evidence_geometry": evidence_geometry(
                            context, task, tokenizer
                        ),
                        "lineage": bundle["lineage"],
                        "local_semantic_validation_passed": True,
                        "assistant_only_preprocessing_verified": True,
                        "strict_long_dependency_verified": False,
                        "production_eligible": False,
                    }
                )
            return bundle, rows
        next_count = max(6, round(n_records * (capacity * 0.975) / max(lengths)))
        if next_count == n_records:
            next_count += 1 if max(lengths) < capacity else -1
        n_records = next_count
    raise ValueError("capacity fitting exhausted; no truncated fallback")


def _init_worker():
    global _TOKENIZER
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        _TOKENIZER = AutoTokenizer.from_pretrained(
            MODEL, revision=REVISION, local_files_only=True, trust_remote_code=False
        )


def _run_shard(job):
    seed, domain, capacity, destination = job
    shard_id = f"seed-{seed}-{domain}-{capacity}"
    shard = Path(destination) / "shards" / shard_id
    shard.mkdir()
    bundle, rows = fit_bundle(seed, domain, capacity, _TOKENIZER)
    write_new_json(shard / "world.json", bundle)
    with (shard / "rows.jsonl").open("x") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")
    receipt = {
        "shard_id": shard_id,
        "worker_pid": os.getpid(),
        "seed": seed,
        "domain": domain,
        "capacity_tokens": capacity,
        "world_id": bundle["world_id"],
        "split": split_for_seed(seed),
        "rows": len(rows),
        "context_variants": 2,
        "full_chat_tokens": [row["full_chat_tokens"] for row in rows],
        "supervised_tokens": sum(row["supervised_tokens"] for row in rows),
        "record_count_parameter": rows[0]["record_count_parameter"],
        "capabilities": dict(Counter(row["capability"] for row in rows)),
        "topology_family": bundle["lineage"]["topology_family"],
        "semantic_validation": validate_bundle(bundle),
        "files": {name: sha(shard / name) for name in ("world.json", "rows.jsonl")},
    }
    # Completion receipt is written last; incomplete shards are never admitted.
    write_new_json(shard / "receipt.json", receipt)
    return receipt


def run(destination, seeds, capacities, workers):
    if workers < 1 or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("workers and unique seeds required")
    if any(seed < 0 for seed in seeds):
        raise ValueError("seeds must be nonnegative")
    if (
        not capacities
        or len(set(capacities)) != len(capacities)
        or any(capacity not in (65536, 131072, 262144) for capacity in capacities)
    ):
        raise ValueError("unique capacities must be 65536, 131072, or 262144")
    tokenizer_digest = resolved_tokenizer_asset_manifest_sha256(MODEL, REVISION)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "shards").mkdir()
    code_paths = (
        "scripts/run_capability_world_pipeline.py",
        "longworld/synthesis/capability_world.py",
        "longworld/core/world.py",
        "longworld/core/state.py",
        "scripts/train_sft.py",
    )
    config = {
        "schema_version": SCHEMA,
        "seeds": seeds,
        "capacities": capacities,
        "workers": workers,
        "domain": "project",
        "length_interval": "[0.95 * capacity, capacity]",
        "tokenizer": {
            "model_id": MODEL,
            "revision": REVISION,
            "assets_sha256": tokenizer_digest,
        },
        "code_sha256": {path: sha(ROOT / path) for path in code_paths},
    }
    write_new_json(destination / "config.json", config)
    jobs = [
        (seed, "project", capacity, str(destination))
        for seed in seeds
        for capacity in capacities
    ]
    receipts, rejects = [], []
    with ProcessPoolExecutor(
        max_workers=min(workers, len(jobs)),
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_init_worker,
    ) as pool:
        futures = {pool.submit(_run_shard, job): job[:3] for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                receipt = future.result()
                receipts.append(receipt)
                print(
                    canonical(
                        {
                            "complete": receipt["shard_id"],
                            "rows": receipt["rows"],
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
                AssertionError,
                KeyError,
            ) as error:
                rejects.append(
                    {"job": job, "error": f"{type(error).__name__}: {error}"}
                )
    write_new_json(destination / "rejects.json", rejects)
    if rejects:
        raise RuntimeError(
            f"{len(rejects)} shards failed; inspect rejects.json; no completed manifest"
        )
    if any(
        sha(ROOT / path) != digest for path, digest in config["code_sha256"].items()
    ):
        raise RuntimeError("code changed during execution; no completed manifest")
    receipts.sort(key=lambda item: item["shard_id"])
    # No promotion or concatenation of eval into train; these remain local messages.
    files = {}
    for split in ("train", "eval"):
        output = destination / f"{split}.jsonl"
        with output.open("x") as stream:
            for receipt in receipts:
                if receipt["split"] == split:
                    source = destination / "shards" / receipt["shard_id"] / "rows.jsonl"
                    if sha(source) != receipt["files"]["rows.jsonl"]:
                        raise RuntimeError("shard content changed before export")
                    with source.open() as incoming:
                        for line in incoming:
                            stream.write(line)
        files[output.name] = sha(output)
    manifest = {
        **config,
        "status": "local_pilot_complete",
        "shards": receipts,
        "seed_groups": len(seeds),
        "semantic_topology_families": len({r["topology_family"] for r in receipts}),
        "length_workload_variants": len(receipts),
        "rows": sum(r["rows"] for r in receipts),
        "split_rows": {
            split: sum(r["rows"] for r in receipts if r["split"] == split)
            for split in ("train", "eval")
        },
        "worker_pids": sorted({r["worker_pid"] for r in receipts}),
        "files": files,
        "strict_long_dependency_verified": False,
        "framework_release_ready": False,
        "production_eligible": False,
        "measured_training_utility": None,
        "limitations": [
            "one rule/topology family; seed split only, not held-out rule transfer",
            "structured simulated observations; no semantic paraphrase or historical-source validation",
            "no model shortcut probes, L4/L5 coverage, or training experiment",
        ],
    }
    write_new_json(destination / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=list(range(6)))
    parser.add_argument(
        "--capacities", nargs="+", type=int, default=[65536, 131072, 262144]
    )
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    manifest = run(args.output, args.seeds, args.capacities, args.workers)
    print(
        canonical(
            {
                key: manifest[key]
                for key in ("status", "rows", "split_rows", "worker_pids")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
