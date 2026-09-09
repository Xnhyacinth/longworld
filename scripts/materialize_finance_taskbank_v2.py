#!/usr/bin/env python3
"""Versioned long-capacity taskbank export; leaves P63 snapshots unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.finance_taskbank import load_finance_world
from longworld.core.finance_taskbank_v2 import compile_taskbank, validate_task
from longworld.core.provenance import _read_regular_file
from longworld.core.taskbank_context import (
    bind_visible_evidence,
    render_document,
    text_sha256,
)
from longworld.core.taskbank_long_context import (
    CONTEXT_LIMIT,
    choose_primary,
    context_options,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.materialize_finance_histories import _load_tokenizer
from scripts.materialize_finance_taskbank import CODE_FILES as P63_CODE_FILES
from scripts.materialize_finance_taskbank import (
    canonical,
    hold_reasons,
    project_evidence,
)

SCHEMA = "longworld.finance-taskbank-long-build.v1"
ROW_SCHEMA = "longworld.finance-taskbank-long-sample.v1"
CODE_FILES = (
    *P63_CODE_FILES,
    "longworld/core/finance_taskbank_v2.py",
    "longworld/core/taskbank_long_context.py",
    "longworld/core/issuer_generic_inline.py",
    "scripts/materialize_finance_taskbank_v2.py",
)


def _json(path):
    value = json.loads(_read_regular_file(Path(path), 256_000_000))
    if not isinstance(value, dict):
        raise TypeError("JSON object required")
    return value


def load_world(config):
    path = ROOT / config["source_manifest"]
    if config.get("source_kind") == "generic_inline":
        from longworld.core.issuer_generic_inline import load_generic_inline_world

        world = load_generic_inline_world(path)
    elif config.get("source_kind") == "issuer_ir":
        world = load_finance_world(path)
    else:
        raise ValueError("unsupported source loader")
    if (
        config["split"] not in {"train", "eval"}
        or config["split_group_id"] != world["split_group_id"]
    ):
        raise ValueError("source split mismatch")
    if world["split_group_id"] in config["excluded_split_group_ids"]:
        raise ValueError("opposite split source conflict")
    return world


def _environment(config):
    if config.get("schema_version") != SCHEMA:
        raise ValueError("invalid long taskbank config")
    world = load_world(config)
    bank = compile_taskbank(world)
    documents = {d["record_id"]: d for d in world["documents"]}
    rendered = {identity: render_document(doc) for identity, doc in documents.items()}
    tc = config["tokenizer"]
    tokenizer = _load_tokenizer(tc["model_id"], tc["revision"])
    assets = resolved_tokenizer_asset_manifest_sha256(tc["model_id"], tc["revision"])
    return world, bank, documents, rendered, tokenizer, assets


def _samples(config, world, bank, documents, rendered, tokenizer, rejects):
    with sanitized_attestation_environment():
        yield from _sample_rows(
            config, world, bank, documents, rendered, tokenizer, rejects
        )


def _sample_rows(config, world, bank, documents, rendered, tokenizer, rejects):
    cache = {}
    for task in bank["tasks"]:
        identity = task["semantic_task_id"]
        if task["family"] in config.get("excluded_families", []):
            rejects.append({"semantic_task_id": identity, "stage": "structure_holdout"})
            continue
        if hold_reasons(world, task, config.get("holds", {})):
            rejects.append({"semantic_task_id": identity, "stage": "hold"})
            continue
        try:
            validate_task(world, task)
            evidence = [
                bind_visible_evidence(
                    documents[s["record_id"]], rendered[s["record_id"]], s
                )
                for s in task["evidence_spans"]
            ]
            required = tuple(sorted({s["record_id"] for s in evidence}))
            if required not in cache:
                cache[required] = choose_primary(
                    context_options(world["documents"], rendered, required, tokenizer),
                    len(required),
                )
            context = cache[required]
            evidence = [project_evidence(e, context) for e in evidence]
        except ValueError as error:
            rejects.append(
                {
                    "semantic_task_id": identity,
                    "stage": "surface_or_capacity",
                    "reason": str(error),
                }
            )
            continue
        sample_id = "sample:" + text_sha256(
            canonical([identity, context["sha256"], task["question"], task["answer"]])
        )
        row = {
            "schema_version": ROW_SCHEMA,
            "source_collection_id": world["source_collection_id"],
            "world_instance_id": world["world_instance_id"],
            "semantic_task_id": identity,
            "variant_family_id": "variants:" + identity,
            "sample_id": sample_id,
            "variant": "full",
            "split": config["split"],
            "split_group_id": world["split_group_id"],
            "context_path": f"contexts/{context['sha256']}.txt",
            "context_sha256": context["sha256"],
            "context_tokens": context["tokens"],
            "capacity_bin": context["capacity_bin"],
            "matches_exact_token_range": context["matches_exact_token_range"],
            "exact_band_certificate": False,
            "context_plan": context["plan"],
            "source_document_count": len(required),
            "task_profile": "long_input_retrieval_candidate"
            if len(required) == 1
            else "multi_filing_integration_candidate",
            "strict_long_dependency_verified": False,
            "alternative_proof_search_complete": False,
            "production_eligible": False,
            "training_release_eligible": False,
            "task_spec": task,
            "visible_evidence": evidence,
        }
        sft = {
            "sample_id": sample_id,
            "messages": [
                {
                    "role": "user",
                    "content": context["text"] + "\n\nQuestion:\n" + task["question"],
                },
                {"role": "assistant", "content": canonical(task["answer"])},
            ],
        }
        yield row, sft, context["text"]


def materialize(config_path, output):
    started = time.monotonic()
    config = _json(config_path)
    world, bank, docs, rendered, tokenizer, assets = _environment(config)
    output.mkdir(parents=True, exist_ok=False)
    (output / "contexts").mkdir()
    rejects = list(bank["rejections"]) + bank.get("applicability_rejections", [])
    counters = {
        key: Counter()
        for key in (
            "capacity_bins",
            "exact_token_ranges",
            "families",
            "compiler_revisions",
        )
    }
    count, tokens = 0, 0
    with (
        (output / "tasks.jsonl").open("x") as rows,
        (output / "sft_candidates.jsonl").open("x") as sft,
    ):
        for row, sample, text in _samples(
            config, world, bank, docs, rendered, tokenizer, rejects
        ):
            path = output / row["context_path"]
            if not path.exists():
                path.write_text(text)
            rows.write(canonical(row) + "\n")
            sft.write(canonical(sample) + "\n")
            count += 1
            tokens += row["context_tokens"]
            counters["capacity_bins"][str(row["capacity_bin"])] += 1
            if row["matches_exact_token_range"]:
                counters["exact_token_ranges"][str(row["capacity_bin"])] += 1
            counters["families"][row["task_spec"]["family"]] += 1
            counters["compiler_revisions"][row["task_spec"]["compiler_revision"]] += 1
    (output / "rejections.json").write_text(canonical(rejects) + "\n")
    receipt = {
        "schema_version": "longworld.finance-taskbank-long-receipt.v1",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "source_manifest": world["source_manifest"],
        "world_instance_id": world["world_instance_id"],
        "split_group_id": world["split_group_id"],
        "split": config["split"],
        "accepted_semantic_tasks": count,
        "training_views": count,
        "context_tokens": tokens,
        "compiler_metrics": bank["metrics"],
        **{k: dict(v) for k, v in counters.items()},
        "context_limit_with_headroom": CONTEXT_LIMIT,
        "tokenizer": {**config["tokenizer"], "asset_manifest_sha256": assets},
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "production_eligible": False,
        "training_release_eligible": False,
        "code_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in CODE_FILES
        },
        "files": {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    (output / "BUILD_RECEIPT.json").write_text(canonical(receipt) + "\n")
    return {
        k: v
        for k, v in receipt.items()
        if k not in {"files", "code_sha256", "source_manifest", "compiler_metrics"}
    }


def validate(config_path, output):
    receipt = _json(output / "BUILD_RECEIPT.json")
    if (
        receipt.get("schema_version") != "longworld.finance-taskbank-long-receipt.v1"
        or hashlib.sha256(config_path.read_bytes()).hexdigest()
        != receipt["config_sha256"]
    ):
        raise ValueError("receipt/config binding mismatch")
    if set(receipt["code_sha256"]) != set(CODE_FILES):
        raise ValueError("code coverage mismatch")
    for name, digest in receipt["code_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("bound code changed")
    members = list(output.rglob("*"))
    if any(p.is_symlink() for p in members) or {
        str(p.relative_to(output)) for p in members if p.is_file()
    } != set(receipt["files"]) | {"BUILD_RECEIPT.json"}:
        raise ValueError("output member inventory mismatch")
    for name, digest in receipt["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or hashlib.sha256(
                _read_regular_file(output / name, 2_000_000_000)
            ).hexdigest()
            != digest
        ):
            raise ValueError("output hash/path mismatch")
    config = _json(config_path)
    world, bank, docs, rendered, tokenizer, assets = _environment(config)
    expected_fields = {
        "split": config["split"],
        "split_group_id": world["split_group_id"],
        "world_instance_id": world["world_instance_id"],
        "tokenizer": {**config["tokenizer"], "asset_manifest_sha256": assets},
        "context_limit_with_headroom": CONTEXT_LIMIT,
    }
    if any(receipt.get(key) != value for key, value in expected_fields.items()):
        raise ValueError("receipt source/split/tokenizer/limit mismatch")
    if (
        receipt["source_manifest"] != world["source_manifest"]
        or assets != receipt["tokenizer"]["asset_manifest_sha256"]
    ):
        raise ValueError("source/tokenizer binding mismatch")
    rejects = list(bank["rejections"]) + bank.get("applicability_rejections", [])
    count = tokens = 0
    expected = _samples(config, world, bank, docs, rendered, tokenizer, rejects)
    counters = {
        key: Counter()
        for key in (
            "capacity_bins",
            "exact_token_ranges",
            "families",
            "compiler_revisions",
        )
    }
    with (
        (output / "tasks.jsonl").open() as rows,
        (output / "sft_candidates.jsonl").open() as sft,
    ):
        for generated, row_line, sample_line in zip_longest(expected, rows, sft):
            if generated is None or row_line is None or sample_line is None:
                raise ValueError("missing or extra generated task")
            row, sample, context = generated
            if (
                row_line != canonical(row) + "\n"
                or sample_line != canonical(sample) + "\n"
                or (output / row["context_path"]).read_text() != context
            ):
                raise ValueError("source task/context/SFT reexecution mismatch")
            count += 1
            tokens += row["context_tokens"]
            counters["capacity_bins"][str(row["capacity_bin"])] += 1
            if row["matches_exact_token_range"]:
                counters["exact_token_ranges"][str(row["capacity_bin"])] += 1
            counters["families"][row["task_spec"]["family"]] += 1
            counters["compiler_revisions"][row["task_spec"]["compiler_revision"]] += 1
    if (
        rejects != json.loads((output / "rejections.json").read_text())
        or count != receipt["accepted_semantic_tasks"]
        or count != receipt["training_views"]
        or tokens != receipt["context_tokens"]
    ):
        raise ValueError("receipt counts/rejection funnel mismatch")
    if (
        any(receipt[k] != dict(v) for k, v in counters.items())
        or receipt["compiler_metrics"] != bank["metrics"]
    ):
        raise ValueError("receipt metrics mismatch")
    if (
        receipt["production_eligible"] is not False
        or receipt["training_release_eligible"] is not False
    ):
        raise ValueError("local candidate cannot claim release eligibility")
    return {
        "status": "PASS",
        "semantic_tasks": count,
        "capacity_bins": receipt["capacity_bins"],
        "exact_token_ranges": receipt["exact_token_ranges"],
        "production_eligible": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            (validate if args.validate else materialize)(args.config, args.output),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
