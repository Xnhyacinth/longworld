#!/usr/bin/env python3
"""Compile a frozen issuer into a source-bound local task bank and SFT candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.provenance import _read_regular_file
from longworld.core.taskbank_context import (
    assemble_analyst_context,
    assemble_context,
    assemble_statement_context,
    bind_visible_evidence,
    render_document,
    text_sha256,
    validate_visible_sign,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.materialize_finance_histories import _load_tokenizer

CODE_FILES = (
    "longworld/core/finance_taskbank.py",
    "longworld/core/taskbank_context.py",
    "longworld/core/issuerfilingworkflow.py",
    "longworld/core/issuerinlineworkflow.py",
    "longworld/core/attestation.py",
    "longworld/core/provenance.py",
    "longworld/core/tokenizer_assets.py",
    "scripts/materialize_finance_histories.py",
    "scripts/materialize_finance_taskbank.py",
    "pyproject.toml",
    "uv.lock",
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hold_reasons(world: dict, task: dict, holds: dict) -> list[str]:
    """A task hold excludes all views before serialization."""
    reasons = []
    for key, value in (
        ("source_collection_ids", world["source_collection_id"]),
        ("world_instance_ids", world["world_instance_id"]),
        ("split_group_ids", world["split_group_id"]),
        ("semantic_task_ids", task["semantic_task_id"]),
    ):
        if value in holds.get(key, []):
            reasons.append(key)
    if set(task["consumed_fact_ids"]) & set(holds.get("fact_ids", [])):
        reasons.append("fact_ids")
    return reasons


def project_evidence(binding: dict, cached: dict) -> dict:
    if cached["mappings"] is None:
        base = cached["offsets"][binding["record_id"]]
    else:
        matching = [
            m
            for m in cached["mappings"][binding["record_id"]]
            if m["visible_start"]
            <= binding["visible_start"]
            < binding["visible_end"]
            <= m["visible_end"]
        ]
        if len(matching) != 1:
            raise ValueError("statement context does not contain evidence")
        base = matching[0]["context_start"] - matching[0]["visible_start"]
    result = {
        **binding,
        "context_start": base + binding["visible_start"],
        "context_end": base + binding["visible_end"],
    }
    if (
        cached["text"][result["context_start"] : result["context_end"]]
        != result["quote"]
    ):
        raise ValueError("serialized context evidence mismatch")
    validate_visible_sign(
        cached["text"], result["context_start"], result["context_end"], result["value"]
    )
    return result


def choose_context(documents, rendered, required, tokenizer, maximum) -> dict:
    """Select a deterministic, complete-source view without clipping or filling."""
    context, offsets = assemble_context(documents, rendered, list(required))
    count = len(tokenizer.encode(context, add_special_tokens=False))
    selection, mappings = "complete_filings", None
    if count > maximum and len(required) > 1:
        context, mappings = assemble_analyst_context(
            documents, rendered, list(required)
        )
        count = len(tokenizer.encode(context, add_special_tokens=False))
        selection = "latest_complete_filing_with_historical_statements"
    if count > maximum:
        context, mappings = assemble_statement_context(
            documents, rendered, list(required)
        )
        count = len(tokenizer.encode(context, add_special_tokens=False))
        selection = "complete_audited_statement_packet"
    return {
        "text": context,
        "offsets": offsets,
        "mappings": mappings,
        "tokens": count,
        "selection": selection,
        "sha256": text_sha256(context),
    }


def materialize(config_path: Path, output: Path) -> dict:
    from longworld.core.finance_taskbank import (
        compile_finance_taskbank,
        load_finance_world,
        validate_finance_task,
    )

    started = time.monotonic()
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    if config.get("schema_version") != "longworld.finance-taskbank-build.v1":
        raise ValueError("unsupported taskbank build config")
    if config["split"] not in {"train", "eval"}:
        raise ValueError("invalid split")
    minimum, maximum = config["min_context_tokens"], config["max_context_tokens"]
    if not (0 < minimum < maximum <= 131072):
        raise ValueError("invalid natural context limits")
    world = load_finance_world(ROOT / config["source_manifest"])
    if world["split_group_id"] != config["split_group_id"]:
        raise ValueError("configured source split group does not match manifest")
    if world["split_group_id"] in config.get("excluded_split_group_ids", []):
        raise ValueError("source group conflicts with protected opposite split")
    holds = config.get("holds", {})
    bank = compile_finance_taskbank(world)
    tokenizer_config = config["tokenizer"]
    tokenizer = _load_tokenizer(
        tokenizer_config["model_id"], tokenizer_config["revision"]
    )
    assets = resolved_tokenizer_asset_manifest_sha256(
        tokenizer_config["model_id"], tokenizer_config["revision"]
    )
    documents = {doc["record_id"]: doc for doc in world["documents"]}
    rendered = {key: render_document(doc) for key, doc in documents.items()}
    # Exclusive directory creation prevents stale receipts and accidental resume
    # across compiler, source, tokenizer or hold changes.
    output.mkdir(parents=True, exist_ok=False)
    contexts_dir = output / "contexts"
    contexts_dir.mkdir()
    context_cache: dict[tuple[str, ...], dict] = {}
    rejects = list(bank["rejections"])
    accepted = []
    family_counts: Counter = Counter()
    length_counts: Counter = Counter()
    exposure: Counter = Counter()
    with (
        (output / "tasks.jsonl").open("x") as task_file,
        (output / "sft_candidates.jsonl").open("x") as sft_file,
    ):
        for task in bank["tasks"]:
            identity = task["semantic_task_id"]
            reasons = hold_reasons(world, task, holds)
            if reasons:
                rejects.append(
                    {"semantic_task_id": identity, "stage": "hold", "reasons": reasons}
                )
                continue
            try:
                validate_finance_task(world, task)
                bindings = [
                    bind_visible_evidence(
                        documents[s["record_id"]], rendered[s["record_id"]], s
                    )
                    for s in task["evidence_spans"]
                ]
                required = tuple(sorted({s["record_id"] for s in bindings}))
                if required not in context_cache:
                    context_cache[required] = choose_context(
                        world["documents"], rendered, required, tokenizer, maximum
                    )
                cached = context_cache[required]
                if not minimum <= cached["tokens"] <= maximum:
                    rejects.append(
                        {
                            "semantic_task_id": identity,
                            "stage": "natural_length",
                            "context_tokens": cached["tokens"],
                        }
                    )
                    continue
                bindings = [project_evidence(binding, cached) for binding in bindings]
            except ValueError as error:
                rejects.append(
                    {
                        "semantic_task_id": identity,
                        "stage": "surface",
                        "reason": str(error),
                    }
                )
                continue
            context_path = contexts_dir / (cached["sha256"] + ".txt")
            if not context_path.exists():
                context_path.write_text(cached["text"])
            sample_id = "sample:" + text_sha256(
                canonical(
                    [identity, cached["sha256"], task["question"], task["answer"]]
                )
            )
            bucket = next(
                (
                    str(limit)
                    for limit in (16384, 32768, 65536, 131072)
                    if cached["tokens"] <= limit
                ),
                "overflow",
            )
            row = {
                "schema_version": "longworld.finance-taskbank-sample.v1",
                "source_collection_id": world["source_collection_id"],
                "world_instance_id": world["world_instance_id"],
                "semantic_task_id": identity,
                "variant_family_id": "variants:" + identity,
                "sample_id": sample_id,
                "variant": "full",
                "split": config["split"],
                "split_group_id": world["split_group_id"],
                "context_path": str(context_path.relative_to(output)),
                "context_sha256": cached["sha256"],
                "context_tokens": cached["tokens"],
                "context_selection": cached["selection"],
                "natural_length_cap": bucket,
                "exact_band_certificate": False,
                "source_document_count": len(required),
                "task_profile": "long_input_retrieval"
                if len(required) == 1
                else "multi_filing_integration_candidate",
                "strict_long_dependency_verified": False,
                "alternative_proof_search_complete": False,
                "production_eligible": False,
                "training_release_eligible": False,
                "task_spec": task,
                "visible_evidence": bindings,
            }
            task_file.write(canonical(row) + "\n")
            messages = [
                {
                    "role": "user",
                    "content": cached["text"] + "\n\nQuestion:\n" + task["question"],
                },
                {"role": "assistant", "content": canonical(task["answer"])},
            ]
            sft_file.write(
                canonical({"sample_id": sample_id, "messages": messages}) + "\n"
            )
            accepted.append(sample_id)
            family_counts[task.get("task_family", task.get("family", "unknown"))] += 1
            length_counts[bucket] += 1
            exposure.update(required)
    (output / "rejections.json").write_text(canonical(rejects) + "\n")
    manifest = {
        "schema_version": "longworld.finance-taskbank-build-receipt.v1",
        "config_sha256": hashlib.sha256(config_raw).hexdigest(),
        "source_manifest": world["source_manifest"],
        "source_collection_id": world["source_collection_id"],
        "world_instance_id": world["world_instance_id"],
        "split_group_id": world["split_group_id"],
        "split": config["split"],
        "compiler_metrics": bank["metrics"],
        "accepted_semantic_tasks": len(accepted),
        "training_views": len(accepted),
        "unique_contexts": len(list(contexts_dir.iterdir())),
        "task_families": dict(family_counts),
        "natural_length_caps": dict(length_counts),
        "source_document_exposure": dict(exposure),
        "rejection_stages": dict(Counter(r.get("stage", "compiler") for r in rejects)),
        "tokenizer": {**tokenizer_config, "asset_manifest_sha256": assets},
        "code_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in CODE_FILES
        },
        "files": {
            str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.rglob("*"))
            if path.is_file()
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "production_eligible": False,
        "training_release_eligible": False,
        "readiness": "source_verified_scope_checked_visible_evidence_local_candidates",
        "unresolved": [
            "independent semantic surface review",
            "alternative evidence proofs and difficulty measurement",
            "production source rights and independent trust",
            "registered batch release and training export contract",
            "matched training evaluation",
        ],
    }
    (output / "BUILD_RECEIPT.json").write_text(canonical(manifest) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    result = (
        validate_export(args.config, args.output)
        if args.validate
        else materialize(args.config, args.output)
    )
    print(json.dumps(result, indent=2))


def validate_export(config_path: Path, output: Path) -> dict:
    """Replay persisted tasks and reassemble source text, not just row hashes."""
    from longworld.core.finance_taskbank import (
        load_finance_world,
        validate_finance_task,
    )

    try:
        receipt = json.loads(
            _read_regular_file(output / "BUILD_RECEIPT.json", 32_000_000)
        )
    except OSError as error:
        raise ValueError("build receipt must be a readable regular file") from error
    if receipt.get("schema_version") != "longworld.finance-taskbank-build-receipt.v1":
        raise ValueError("invalid build receipt schema")
    config_raw = config_path.read_bytes()
    if hashlib.sha256(config_raw).hexdigest() != receipt["config_sha256"]:
        raise ValueError("build config binding mismatch")
    config = json.loads(config_raw)
    members = list(output.rglob("*"))
    if any(p.is_symlink() for p in members):
        raise ValueError("unsafe symlink in export")
    actual_paths = {str(p.relative_to(output)) for p in members if p.is_file()}
    if actual_paths != set(receipt["files"]) | {"BUILD_RECEIPT.json"}:
        raise ValueError("export member inventory mismatch")
    for relative, digest in receipt["files"].items():
        path = output / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or path.is_symlink()
        ):
            raise ValueError("unsafe export member")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("export file hash mismatch")
    if set(receipt["code_sha256"]) != set(CODE_FILES):
        raise ValueError("compiler code inventory mismatch")
    for relative, digest in receipt["code_sha256"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest:
            raise ValueError("compiler code changed after build")
    world = load_finance_world(ROOT / config["source_manifest"])
    if world["source_manifest"] != receipt["source_manifest"]:
        raise ValueError("source manifest binding mismatch")
    if world["split_group_id"] != config["split_group_id"] or world[
        "split_group_id"
    ] in config.get("excluded_split_group_ids", []):
        raise ValueError("source split binding mismatch")
    tok_config = config["tokenizer"]
    tokenizer = _load_tokenizer(tok_config["model_id"], tok_config["revision"])
    assets = resolved_tokenizer_asset_manifest_sha256(
        tok_config["model_id"], tok_config["revision"]
    )
    if assets != receipt["tokenizer"]["asset_manifest_sha256"]:
        raise ValueError("tokenizer asset binding mismatch")
    docs = {d["record_id"]: d for d in world["documents"]}
    rendered = {key: render_document(doc) for key, doc in docs.items()}
    seen: set[str] = set()
    contexts: dict[tuple, dict] = {}
    context_tokens: dict[str, int] = {}
    families, lengths, exposure = Counter(), Counter(), Counter()
    from itertools import zip_longest

    with (
        (output / "tasks.jsonl").open() as tasks,
        (output / "sft_candidates.jsonl").open() as sft,
    ):
        for task_line, sft_line in zip_longest(tasks, sft):
            if task_line is None or sft_line is None:
                raise ValueError("task/SFT row count mismatch")
            row, training = json.loads(task_line), json.loads(sft_line)
            task = row["task_spec"]
            validate_finance_task(world, task)
            if hold_reasons(world, task, config.get("holds", {})):
                raise ValueError("held task reached export")
            identity = task["semantic_task_id"]
            if identity in seen:
                raise ValueError("duplicate semantic task")
            seen.add(identity)
            for field in (
                "source_collection_id",
                "world_instance_id",
                "split_group_id",
            ):
                if row[field] != world[field]:
                    raise ValueError("row world identity mismatch")
            if row["split"] != config["split"] or row["semantic_task_id"] != identity:
                raise ValueError("row task/split identity mismatch")
            if (
                row["variant"] != "full"
                or row["variant_family_id"] != "variants:" + identity
            ):
                raise ValueError("row variant identity mismatch")
            if any(
                row[field] is not False
                for field in (
                    "production_eligible",
                    "training_release_eligible",
                    "strict_long_dependency_verified",
                    "alternative_proof_search_complete",
                    "exact_band_certificate",
                )
            ):
                raise ValueError("local export cannot claim release eligibility")
            required = tuple(
                sorted({span["record_id"] for span in task["evidence_spans"]})
            )
            expected_profile = (
                "long_input_retrieval"
                if len(required) == 1
                else "multi_filing_integration_candidate"
            )
            if (
                row["source_document_count"] != len(required)
                or row["task_profile"] != expected_profile
            ):
                raise ValueError("source count/profile binding mismatch")
            key = required
            if key not in contexts:
                contexts[key] = choose_context(
                    world["documents"],
                    rendered,
                    required,
                    tokenizer,
                    config["max_context_tokens"],
                )
            if row["context_selection"] != contexts[key]["selection"]:
                raise ValueError(
                    "context selection does not follow fixed source policy"
                )
            context = contexts[key]["text"]
            digest = text_sha256(context)
            if (
                row["context_path"] != f"contexts/{digest}.txt"
                or row["context_sha256"] != digest
            ):
                raise ValueError("context source reassembly mismatch")
            if (output / row["context_path"]).read_text() != context:
                raise ValueError("context file differs from complete source selection")
            if digest not in context_tokens:
                context_tokens[digest] = len(
                    tokenizer.encode(context, add_special_tokens=False)
                )
            if (
                row["context_tokens"] != context_tokens[digest]
                or not config["min_context_tokens"]
                <= context_tokens[digest]
                <= config["max_context_tokens"]
            ):
                raise ValueError("exact context token count mismatch")
            bucket = next(
                str(limit)
                for limit in (16384, 32768, 65536, 131072)
                if context_tokens[digest] <= limit
            )
            if row["natural_length_cap"] != bucket:
                raise ValueError("natural length cap mismatch")
            if len(row["visible_evidence"]) != len(task["evidence_spans"]):
                raise ValueError("visible evidence count mismatch")
            for span, bound in zip(task["evidence_spans"], row["visible_evidence"]):
                fresh = bind_visible_evidence(
                    docs[span["record_id"]], rendered[span["record_id"]], span
                )
                fresh = project_evidence(fresh, contexts[key])
                if bound != fresh:
                    raise ValueError("visible evidence source binding mismatch")
                if (
                    context[bound["context_start"] : bound["context_end"]]
                    != span["quote"]
                ):
                    raise ValueError("persisted context evidence mismatch")
                validate_visible_sign(
                    context, bound["context_start"], bound["context_end"], span["value"]
                )
            sample_id = "sample:" + text_sha256(
                canonical([identity, digest, task["question"], task["answer"]])
            )
            expected = {
                "sample_id": sample_id,
                "messages": [
                    {
                        "role": "user",
                        "content": context + "\n\nQuestion:\n" + task["question"],
                    },
                    {"role": "assistant", "content": canonical(task["answer"])},
                ],
            }
            if row["sample_id"] != sample_id or training != expected:
                raise ValueError("serialized SFT binding mismatch")
            families[task["family"]] += 1
            lengths[bucket] += 1
            exposure.update(required)
    if (
        len(seen) != receipt["accepted_semantic_tasks"]
        or len(seen) != receipt["training_views"]
    ):
        raise ValueError("receipt task count mismatch")
    if (
        receipt["unique_contexts"] != len(context_tokens)
        or receipt["task_families"] != dict(families)
        or receipt["natural_length_caps"] != dict(lengths)
        or receipt["source_document_exposure"] != dict(exposure)
    ):
        raise ValueError("receipt summary metrics mismatch")
    return {
        "status": "PASS",
        "semantic_tasks": len(seen),
        "unique_contexts": len(context_tokens),
        "production_eligible": False,
        "training_release_eligible": False,
    }


if __name__ == "__main__":
    main()
