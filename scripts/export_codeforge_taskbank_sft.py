#!/usr/bin/env python3
"""Project validated CodeForge banks to one complete primary SFT row per task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.codeforge_taskbank import (
    canonical,
    evaluate_program,
    identity,
    render_context,
)

SCHEMA = "longworld.codeforge-taskbank-sft-build.v1"
TRANSFORM = "codeforge-primary-query-first-chat-v1"
CUTOFF = 262144
MODEL = "Qwen/Qwen3.5-4B"
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
JSONL_NAMES = (
    "train.jsonl",
    "eval.jsonl",
    "short.jsonl",
    "metadata.jsonl",
    "rejects.jsonl",
)
OUTPUT_NAMES = (*JSONL_NAMES, "dataset_info.json")


def classify_sample(context_tokens: int, full_tokens: int) -> str:
    if full_tokens > CUTOFF:
        return "rejected_overflow"
    return "short" if context_tokens <= 32768 else "long"


def project_messages(world: dict, task: dict, context: str | None = None) -> list[dict]:
    answer = evaluate_program(
        world, task["parameters"]["episode_ids"], task["program_id"]
    )["answer"]
    if answer != task["oracle_answer"]:
        raise ValueError("task oracle does not match fresh source replay")
    if context is None:
        context = render_context(world, task["parameters"]["episode_ids"])
    return [
        {"role": "user", "content": task["query"] + "\n\nSource records:\n" + context},
        {"role": "assistant", "content": canonical(answer)},
    ]


def _digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular input/output file: " + str(path))
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _binding(path: Path) -> dict:
    return {"path": str(path), "sha256": _digest(path), "bytes": path.stat().st_size}


def _load_banks(catalog_path: Path):
    catalog = json.loads(catalog_path.read_text())
    if catalog.get("schema_version") != "longworld.codeforge-taskbank-catalog.v1":
        raise ValueError("unsupported CodeForge catalog")
    banks, inputs = [], {catalog_path.resolve()}
    groups = {"train": set(), "eval": set()}
    semantic_ids = set()
    for job in catalog["jobs"]:
        base = (ROOT / job["output"]).resolve(strict=True)
        world = json.loads((base / "world.json").read_text())
        tasks = [
            json.loads(line) for line in (base / "tasks.jsonl").read_text().splitlines()
        ]
        contexts = {
            c["context_id"]: c
            for c in (
                json.loads(line)
                for line in (base / "contexts.jsonl").read_text().splitlines()
            )
        }
        receipt = json.loads((base / "BUILD_RECEIPT.json").read_text())
        config_path = (ROOT / job["config"]).resolve(strict=True)
        config = json.loads(config_path.read_text())
        if (
            config["tokenizer"]["model_id"] != MODEL
            or config["tokenizer"]["revision"] != REVISION
        ):
            raise ValueError("tokenizer pin differs across source banks")
        if world["source_group_id"] in groups["train"] | groups["eval"]:
            raise ValueError("duplicated repository source group")
        groups[world["split"]].add(world["source_group_id"])
        for task in tasks:
            if (
                task["semantic_task_id"] in semantic_ids
                or task["split"] != world["split"]
            ):
                raise ValueError("duplicate canonical task or mixed split")
            semantic_ids.add(task["semantic_task_id"])
            if task["context_id"] not in contexts:
                raise ValueError("task context is missing")
        banks.append(
            {
                "job": job,
                "base": base,
                "world": world,
                "tasks": tasks,
                "contexts": contexts,
            }
        )
        inputs.update(
            base / name
            for name in (
                "world.json",
                "tasks.jsonl",
                "contexts.jsonl",
                "BUILD_RECEIPT.json",
            )
        )
        inputs.add(config_path)
        inputs.update(
            Path(b["path"]).resolve(strict=True) for b in receipt["source_bindings"]
        )
    inputs.update(
        ROOT / relative
        for relative in (
            "scripts/export_codeforge_taskbank_sft.py",
            "scripts/materialize_codeforge_taskbank.py",
            "longworld/core/codeforge_taskbank.py",
            "longworld/core/realworkflow.py",
            "scripts/train_sft.py",
            "scripts/materialize_finance_histories.py",
        )
    )
    return banks, sorted((_binding(path) for path in inputs), key=lambda b: b["path"])


def _validate_source_bank(bank: dict) -> dict:
    argv = [
        sys.executable,
        str(ROOT / "scripts/materialize_codeforge_taskbank.py"),
        "--config",
        str(ROOT / bank["job"]["config"]),
        "--output",
        str(bank["base"]),
        "--validate",
    ]
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", TOKENIZERS_PARALLELISM="false")
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=7200,
    )
    if result.returncode:
        raise ValueError(
            "fresh source bank validation failed: "
            + bank["job"]["repository"]
            + "\n"
            + result.stderr[-3000:]
        )
    proof = json.loads(result.stdout)
    if proof.get("status") != "PASS":
        raise ValueError("source bank did not return PASS")
    return proof


def _load_tokenizer():
    from scripts.materialize_finance_histories import _load_tokenizer as load

    return load(MODEL, REVISION)


def _measure(messages, tokenizer):
    from scripts.train_sft import tokenize_assistant_only

    # Measure the complete untruncated sequence first; classification below
    # rejects anything above the actual training cutoff, including its answer.
    encoded = tokenize_assistant_only(tokenizer, messages, 1 << 30)
    return len(encoded["input_ids"]), sum(v != -100 for v in encoded["labels"])


def materialize(catalog_path: Path, output: Path, *, validate: bool = False):
    catalog_path = catalog_path.resolve(strict=True)
    output = output.absolute()
    banks, bindings = _load_banks(catalog_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        proofs = list(pool.map(_validate_source_bank, banks))
    if validate:
        if {p.name for p in output.iterdir()} != {*OUTPUT_NAMES, "BUILD_RECEIPT.json"}:
            raise ValueError("unexpected SFT output inventory")
    else:
        output.mkdir(parents=True, exist_ok=False)
    counts = Counter(train=0, eval=0, short=0, rejected_overflow=0)
    full_sums = Counter()
    capacities, exact_ranges = Counter(), Counter()
    full_max = observed_max = 0
    positions = Counter()
    local = threading.local()
    lock = threading.Lock()

    def measure(item):
        if not hasattr(local, "tokenizer"):
            with lock:
                local.tokenizer = _load_tokenizer()
        messages, metadata = item
        full, assistant = _measure(messages, local.tokenizer)
        return messages, dict(
            metadata, full_message_tokens=full, assistant_tokens=assistant
        )

    previous_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        with (
            sanitized_attestation_environment(),
            ThreadPoolExecutor(max_workers=8) as pool,
            ExitStack() as stack,
        ):
            handles = {
                name: stack.enter_context(
                    (output / name).open("r" if validate else "x")
                )
                for name in JSONL_NAMES
            }
            pending = deque()

            def write(name, row):
                line = canonical(row) + "\n"
                if validate:
                    if handles[name].readline() != line:
                        raise ValueError("SFT replay mismatch: " + name)
                else:
                    handles[name].write(line)

            def drain():
                nonlocal full_max, observed_max
                messages, meta = pending.popleft().result()
                observed_max = max(observed_max, meta["full_message_tokens"])
                state = classify_sample(
                    meta["exact_context_tokens"], meta["full_message_tokens"]
                )
                meta["classification"] = state
                if state == "rejected_overflow":
                    meta.update(
                        output_file=None,
                        row_index=None,
                        reason="complete messages including answer exceed 262144 tokens",
                    )
                    counts[state] += 1
                    write("rejects.jsonl", meta)
                else:
                    group = "short" if state == "short" else meta["split"]
                    name = group + ".jsonl"
                    meta.update(output_file=name, row_index=positions[name])
                    positions[name] += 1
                    counts[group] += 1
                    write(name, {"messages": messages})
                    if state == "long":
                        full_max = max(full_max, meta["full_message_tokens"])
                        full_sums[group] += meta["full_message_tokens"]
                        cap = next(
                            label
                            for label, hi in (
                                ("cap64k", 65536),
                                ("cap128k", 131072),
                                ("cap256k", 262144),
                            )
                            if meta["full_message_tokens"] <= hi
                        )
                        capacities[cap] += 1
                        band = next(
                            (
                                label
                                for label, lo, hi in (
                                    ("64k", 64000, 65536),
                                    ("128k", 128000, 131072),
                                    ("256k", 256000, 262144),
                                )
                                if lo <= meta["exact_context_tokens"] <= hi
                            ),
                            "other_long",
                        )
                        exact_ranges[band] += 1
                write("metadata.jsonl", meta)

            for bank in banks:
                world = bank["world"]
                previous_context_id, context = None, None
                for task in bank["tasks"]:
                    c = bank["contexts"][task["context_id"]]
                    if previous_context_id != task["context_id"]:
                        context = render_context(
                            world, task["parameters"]["episode_ids"]
                        )
                        if (
                            hashlib.sha256(context.encode()).hexdigest()
                            != c["context_sha256"]
                        ):
                            raise ValueError("fresh context binding mismatch")
                        previous_context_id = task["context_id"]
                    messages = project_messages(world, task, context)
                    meta = {
                        key: task[key]
                        for key in (
                            "source_collection_id",
                            "world_instance_id",
                            "semantic_task_id",
                            "variant_family_id",
                            "source_group_id",
                            "split",
                            "program_id",
                            "context_id",
                        )
                    }
                    meta.update(
                        domain="codeforge",
                        source_sample_id=task["sample_id"],
                        sample_id=identity(
                            {
                                "source_sample_id": task["sample_id"],
                                "transform_revision": TRANSFORM,
                            }
                        ),
                        bank_directory=str(bank["base"]),
                        context_sha256=c["context_sha256"],
                        exact_context_tokens=c["exact_context_tokens"],
                        episode_ids=task["parameters"]["episode_ids"],
                    )
                    pending.append(pool.submit(measure, (messages, meta)))
                    if len(pending) >= 16:
                        drain()
            while pending:
                drain()
            if validate and any(handle.read(1) for handle in handles.values()):
                raise ValueError("extra SFT output rows")
    finally:
        if previous_cuda is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = previous_cuda
    dataset_info = {
        f"p64_codeforge_{split}": {
            "file_name": split + ".jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }
        for split in ("train", "eval")
    }
    data_path = output / "dataset_info.json"
    if validate:
        if data_path.read_text() != canonical(dataset_info) + "\n":
            raise ValueError("dataset mapping changed")
    else:
        data_path.write_text(canonical(dataset_info) + "\n")
    if any(_binding(Path(entry["path"])) != entry for entry in bindings):
        raise ValueError("source or code changed during SFT preparation")
    outputs = [
        {
            "path": name,
            "sha256": _digest(output / name),
            "bytes": (output / name).stat().st_size,
        }
        for name in OUTPUT_NAMES
    ]
    receipt = {
        "schema_version": SCHEMA,
        "transform_revision": TRANSFORM,
        "domain": "codeforge",
        "catalog_path": str(catalog_path),
        "input_bindings": bindings,
        "source_validation": proofs,
        "counts": dict(counts),
        "canonical_source_tasks": sum(len(b["tasks"]) for b in banks),
        "primary_long_samples": counts["train"] + counts["eval"],
        "full_message_token_sums": dict(full_sums),
        "full_message_capacity_counts": dict(capacities),
        "exact_context_token_range_sample_counts": dict(exact_ranges),
        "max_full_message_tokens": full_max,
        "max_observed_full_message_tokens": observed_max,
        "tokenizer": {
            "model_id": MODEL,
            "revision": REVISION,
            "template": "qwen3_5_nothink",
            "cutoff": CUTOFF,
            "truncation": False,
        },
        "files": {o["path"]: o["sha256"] for o in outputs},
        "output_bindings": outputs,
        "short_context_cutoff": 32768,
        "production_eligible": False,
        "local_training_eligible": False,
        "framework_preprocessing_verified": False,
    }
    receipt_path = output / "BUILD_RECEIPT.json"
    if validate:
        if json.loads(receipt_path.read_text()) != receipt:
            raise ValueError("SFT build receipt changed")
    else:
        receipt_path.write_text(canonical(receipt) + "\n")
    return {
        k: receipt[k]
        for k in (
            "counts",
            "canonical_source_tasks",
            "primary_long_samples",
            "full_message_capacity_counts",
            "exact_context_token_range_sample_counts",
            "max_full_message_tokens",
            "max_observed_full_message_tokens",
            "production_eligible",
            "local_training_eligible",
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "status": "PASS",
                **materialize(args.catalog, args.output, validate=args.validate),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
