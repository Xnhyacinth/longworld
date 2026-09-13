#!/usr/bin/env python3
"""Audit and export a scoped filename-copy-qualified subset; never mutate P64."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import attestation_environment_names
from longworld.core.codeforge_reading_proof import (
    CONTENT_CONTROL,
    CONTENT_PROFILE,
    FILE_PROGRAMS,
    GRAMMAR,
    PROFILE,
    alias_copy_diagnostic,
    audit_file_task,
    occurrence_table,
    source_text_control,
)
from longworld.core.codeforge_taskbank import canonical, load_world, render_context
from longworld.core.taskbank_dependency_audit import exact_offsets
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.export_codeforge_taskbank_sft import TRANSFORM as PRIMARY_TRANSFORM
from scripts.export_codeforge_taskbank_sft import _load_banks, project_messages

TOKENIZER = None
OUTPUTS = (
    "proofs.jsonl",
    "occurrences.jsonl",
    "train.jsonl",
    "eval.jsonl",
    "metadata.jsonl",
    "unqualified_index.jsonl",
    "raw_qualified_index.jsonl",
)


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular bound file: " + str(path))
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def binding(path):
    path = path.resolve(strict=True)
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}


def initialize(model, revision):
    global TOKENIZER
    for name in attestation_environment_names():
        os.environ.pop(name, None)
    os.environ.update(
        CUDA_VISIBLE_DEVICES="",
        TOKENIZERS_PARALLELISM="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    from transformers import AutoTokenizer

    TOKENIZER = AutoTokenizer.from_pretrained(
        model, revision=revision, local_files_only=True, trust_remote_code=False
    )


def audit_bank(work):
    bank, primary, windows = work
    world = bank["world"]
    selected = [t for t in bank["tasks"] if t["semantic_task_id"] in primary]
    groups = defaultdict(list)
    for task in selected:
        groups[task["context_id"]].append(task)
    rows, occurrence_rows = [], []
    for context_id, tasks in sorted(groups.items()):
        file_tasks = [t for t in tasks if t["program_id"] in FILE_PROGRAMS]
        meta0 = primary[tasks[0]["semantic_task_id"]]
        text = render_context(world, tasks[0]["parameters"]["episode_ids"])
        if hashlib.sha256(text.encode()).hexdigest() != meta0["context_sha256"]:
            raise ValueError("primary context bytes changed")
        table = control_table = None
        control_tokens = None
        if file_tasks:
            offsets = exact_offsets(text, TOKENIZER)
            if len(offsets) != meta0["exact_context_tokens"]:
                raise ValueError("primary tokenizer count changed")
            filenames = sorted(
                {name for task in file_tasks for name in task["oracle_answer"]}
            )
            table = occurrence_table(text, filenames, offsets)
            control_text, control_info = source_text_control(
                json.loads(text), filenames
            )
            control_offsets = exact_offsets(control_text, TOKENIZER)
            control_tokens = len(control_offsets)
            control_table = occurrence_table(control_text, filenames, control_offsets)
            if control_table["patterns"] != table["patterns"]:
                raise ValueError("content-control alias grammar changed")
            occurrence_rows.append(
                {
                    "context_id": context_id,
                    "context_sha256": meta0["context_sha256"],
                    "context_tokens": len(offsets),
                    "grammar_id": GRAMMAR,
                    **table,
                    "content_control": {
                        **control_info,
                        "text_sha256": hashlib.sha256(
                            control_text.encode()
                        ).hexdigest(),
                        "tokens": control_tokens,
                        "occurrences": control_table["occurrences"],
                    },
                }
            )
        for task in tasks:
            meta = primary[task["semantic_task_id"]]
            base = {
                k: meta[k]
                for k in (
                    "sample_id",
                    "source_sample_id",
                    "semantic_task_id",
                    "variant_family_id",
                    "source_collection_id",
                    "world_instance_id",
                    "source_group_id",
                    "split",
                    "context_id",
                    "context_sha256",
                    "exact_context_tokens",
                    "full_message_tokens",
                    "program_id",
                )
            }
            base.update(
                source_output_file=meta["output_file"],
                source_row_index=meta["row_index"],
            )
            if task["program_id"] not in FILE_PROGRAMS:
                base.update(
                    classification="outside_filename_copy_grammar",
                    scoped_long_input_certificate=False,
                    content_backed_scoped_certificate=False,
                    strict_long_dependency_verified=False,
                )
            else:
                base.update(
                    audit_file_task(
                        text, task, table, meta["exact_context_tokens"], windows=windows
                    )
                )
                control = alias_copy_diagnostic(
                    task["oracle_answer"],
                    task["query"],
                    control_table,
                    control_tokens,
                    windows=windows,
                )
                base["content_span_control"] = {
                    "revision": CONTENT_CONTROL,
                    "standalone_task_solver": False,
                    "source_text_tokens": control_tokens,
                    "minimum_alias_cover": control["minimum_alias_cover"],
                    "control_window_tests": control["window_tests"],
                }
                base["content_backed_scoped_certificate"] = base[
                    "scoped_long_input_certificate"
                ] and control["minimum_alias_cover"]["minimum_tokens"] > max(windows)
            rows.append(base)
        print(
            bank["job"]["repository"],
            context_id[:10],
            len(file_tasks),
            "file tasks audited",
            file=sys.stderr,
            flush=True,
        )
    return rows, occurrence_rows


def run(config_path: Path, output: Path, workers: int, validate: bool):
    config_path = config_path.resolve(strict=True)
    config = json.loads(config_path.read_text())
    if (
        config.get("schema_version") != "longworld.codeforge-reading-proof-config.v1"
        or config.get("profile_id") != CONTENT_PROFILE
        or config.get("raw_profile_id") != PROFILE
        or config.get("content_control") != CONTENT_CONTROL
        or config.get("grammar_id") != GRAMMAR
        or config.get("windows") != [4096, 8192, 16384]
        or config.get("counterfactual_views") is not False
    ):
        raise ValueError("unsupported proof profile/configuration")
    catalog_path = ROOT / config["source_catalog"]
    primary_root = (ROOT / config["primary_root"]).resolve(strict=True)
    banks, inputs = _load_banks(catalog_path.resolve(strict=True))
    # Revalidate source attestations under the existing source role, before
    # workers strip producer credentials and import the tokenizer.
    for bank in banks:
        cfg = json.loads((ROOT / bank["job"]["config"]).read_text())
        verified = load_world(ROOT / cfg["source_bundle"], cfg["source_bundle_sha256"])
        verified["split"] = cfg["split"]
        if canonical(verified) != canonical(bank["world"]):
            raise ValueError("bank differs from freshly verified source world")
    primary_receipt = json.loads((primary_root / "BUILD_RECEIPT.json").read_text())
    if primary_receipt["transform_revision"] != PRIMARY_TRANSFORM:
        raise ValueError("primary projection revision differs")
    for entry in primary_receipt["input_bindings"]:
        if binding(Path(entry["path"])) != entry:
            raise ValueError("P64 primary input binding changed")
    for name, expected in primary_receipt["files"].items():
        if digest(primary_root / name) != expected:
            raise ValueError("P64 primary output binding changed")
    primary = {
        r["semantic_task_id"]: r
        for r in map(
            json.loads, (primary_root / "metadata.jsonl").read_text().splitlines()
        )
        if r["classification"] == "long"
    }
    if len(primary) != primary_receipt["primary_long_samples"]:
        raise ValueError("primary identity/count mismatch")
    task_lookup = {
        t["semantic_task_id"]: (bank, t) for bank in banks for t in bank["tasks"]
    }
    for semantic_id, meta in primary.items():
        bank, task = task_lookup[semantic_id]
        if (
            task["sample_id"] != meta["source_sample_id"]
            or task["split"] != meta["split"]
            or task["context_id"] != meta["context_id"]
        ):
            raise ValueError("primary source scope mismatch")
    extra_paths = [
        config_path,
        Path(__file__).resolve(),
        ROOT / "longworld/core/codeforge_reading_proof.py",
        ROOT / "longworld/core/taskbank_dependency_audit.py",
        ROOT / "longworld/core/tokenizer_assets.py",
        primary_root / "BUILD_RECEIPT.json",
    ]
    extra_paths += [primary_root / name for name in primary_receipt["files"]]
    inputs = sorted(
        {b["path"]: b for b in [*inputs, *(binding(p) for p in extra_paths)]}.values(),
        key=lambda b: b["path"],
    )
    assets = resolved_tokenizer_asset_manifest_sha256(
        config["tokenizer"]["model_id"], config["tokenizer"]["revision"]
    )
    rows, occurrence_rows = [], []
    work = [
        (
            bank,
            {
                k: v
                for k, v in primary.items()
                if v["source_group_id"] == bank["world"]["source_group_id"]
            },
            tuple(config["windows"]),
        )
        for bank in banks
    ]
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=initialize,
        initargs=(config["tokenizer"]["model_id"], config["tokenizer"]["revision"]),
    ) as pool:
        for part, occurrences in pool.map(audit_bank, work):
            rows.extend(part)
            occurrence_rows.extend(occurrences)
    rows.sort(key=lambda r: r["semantic_task_id"])
    occurrence_rows.sort(key=lambda r: r["context_id"])
    if len(rows) != len(primary) or len({r["sample_id"] for r in rows}) != len(rows):
        raise ValueError("proof row coverage mismatch")
    qualified = {
        r["semantic_task_id"]: r for r in rows if r["content_backed_scoped_certificate"]
    }
    raw_qualified = [r for r in rows if r["scoped_long_input_certificate"]]
    output = output.absolute()
    if validate:
        if {p.name for p in output.iterdir()} != {*OUTPUTS, "BUILD_RECEIPT.json"}:
            raise ValueError("unexpected proof output inventory")
    else:
        output.mkdir(parents=True, exist_ok=False)
    counts = Counter(train=0, eval=0)
    with ExitStack() as stack:
        handles = {
            name: stack.enter_context((output / name).open("r" if validate else "x"))
            for name in OUTPUTS
        }

        def write(name, value):
            line = canonical(value) + "\n"
            if validate:
                if handles[name].readline() != line:
                    raise ValueError("proof replay mismatch: " + name)
            else:
                handles[name].write(line)

        for row in rows:
            write("proofs.jsonl", row)
            if row["scoped_long_input_certificate"]:
                write(
                    "raw_qualified_index.jsonl",
                    {
                        k: row[k]
                        for k in (
                            "sample_id",
                            "semantic_task_id",
                            "program_id",
                            "source_output_file",
                            "source_row_index",
                            "content_backed_scoped_certificate",
                        )
                    },
                )
            if not row["content_backed_scoped_certificate"]:
                write(
                    "unqualified_index.jsonl",
                    {
                        k: row[k]
                        for k in (
                            "sample_id",
                            "semantic_task_id",
                            "program_id",
                            "classification",
                            "scoped_long_input_certificate",
                            "source_output_file",
                            "source_row_index",
                        )
                    },
                )
        for record in occurrence_rows:
            write("occurrences.jsonl", record)
        # Retain the exact P64 messages; no new source/context/task IDs.
        for split in ("train", "eval"):
            indexed = {
                v["source_row_index"]: v
                for v in qualified.values()
                if v["split"] == split
            }
            with (primary_root / (split + ".jsonl")).open() as source:
                for index, line in enumerate(source):
                    if index not in indexed:
                        continue
                    proof = indexed[index]
                    bank, task = task_lookup[proof["semantic_task_id"]]
                    expected = {"messages": project_messages(bank["world"], task)}
                    if line != canonical(expected) + "\n":
                        raise ValueError(
                            "qualified primary row differs from fresh reader/oracle"
                        )
                    write(split + ".jsonl", expected)
                    meta = dict(
                        primary[proof["semantic_task_id"]],
                        source_output_file=split + ".jsonl",
                        source_row_index=index,
                        output_file=split + ".jsonl",
                        row_index=counts[split],
                        qualification_profile_id=CONTENT_PROFILE,
                        raw_qualification_profile_id=PROFILE,
                        proof_sha256=hashlib.sha256(
                            canonical(proof).encode()
                        ).hexdigest(),
                        scoped_long_input_certificate=True,
                        content_backed_scoped_certificate=True,
                        canonical_strict_production_eligible=False,
                    )
                    write("metadata.jsonl", meta)
                    counts[split] += 1
        if sum(counts.values()) != len(qualified):
            raise ValueError("qualified export coverage mismatch")
        if validate and any(h.read(1) for h in handles.values()):
            raise ValueError("extra proof output rows")
    if any(binding(Path(entry["path"])) != entry for entry in inputs):
        raise ValueError("source/code changed during proof audit")
    files = {name: digest(output / name) for name in OUTPUTS}
    file_rows = [r for r in rows if r["program_id"] in FILE_PROGRAMS]
    receipt = {
        "schema_version": "longworld.codeforge-reading-proof-build.v2",
        "profile_id": CONTENT_PROFILE,
        "raw_profile_id": PROFILE,
        "content_control": CONTENT_CONTROL,
        "grammar_id": GRAMMAR,
        "input_bindings": inputs,
        "tokenizer": {**config["tokenizer"], "asset_manifest_sha256": assets},
        "primary_rows": len(rows),
        "filename_grammar_rows": len(file_rows),
        "qualified_existing_semantic_tasks": len(qualified),
        "raw_scoped_qualified_existing_tasks": len(raw_qualified),
        "raw_only_qualified_existing_tasks": len(raw_qualified) - len(qualified),
        "new_source_worlds": 0,
        "new_semantic_tasks": 0,
        "new_counterfactual_views": 0,
        "counts": dict(counts),
        "classifications": dict(Counter(r["classification"] for r in rows)),
        "qualified_by_repository": dict(
            Counter(r["source_group_id"] for r in qualified.values())
        ),
        "qualified_context_capacity_bins": dict(
            Counter(
                next(
                    label
                    for label, cap in (
                        ("cap64k", 65536),
                        ("cap128k", 131072),
                        ("cap256k", 262144),
                    )
                    if r["exact_context_tokens"] <= cap
                )
                for r in qualified.values()
            )
        ),
        "qualified_exact_context_token_ranges": dict(
            Counter(
                next(
                    (
                        label
                        for label, lo, hi in (
                            ("64k", 64000, 65536),
                            ("128k", 128000, 131072),
                            ("256k", 256000, 262144),
                        )
                        if lo <= r["exact_context_tokens"] <= hi
                    ),
                    "other_long",
                )
                for r in qualified.values()
            )
        ),
        "window_all_aliases_fit": {
            str(w): sum(
                r["raw_token_window_tests"][str(w)]["all_labels_fit"] for r in file_rows
            )
            for w in config["windows"]
        },
        "files": files,
        "scope": "single contiguous raw-token window; finite full-path/basename/JSON/URL aliases; ASCII case-insensitive substring matching; oracle disambiguation and selection; question aliases free; no filename synthesis or multiwindow/prior-model claims",
        "full_reader_scope": "visible PR/merge/head/review fields and diff-header filename set aggregation; removal is symbolic input contribution, not JSON parsing failure",
        "strict_long_dependency_verified": False,
        "canonical_strict_production_eligible": False,
        "production_eligible": False,
    }
    receipt_path = output / "BUILD_RECEIPT.json"
    if validate:
        if json.loads(receipt_path.read_text()) != receipt:
            raise ValueError("proof receipt replay mismatch")
    else:
        receipt_path.write_text(canonical(receipt) + "\n")
    return {
        k: receipt[k]
        for k in (
            "primary_rows",
            "filename_grammar_rows",
            "qualified_existing_semantic_tasks",
            "raw_scoped_qualified_existing_tasks",
            "raw_only_qualified_existing_tasks",
            "counts",
            "classifications",
            "qualified_context_capacity_bins",
            "qualified_exact_context_token_ranges",
            "window_all_aliases_fit",
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        raise ValueError("workers must be between1 and8")
    print(
        json.dumps(
            {
                "status": "PASS",
                **run(args.config, args.output, args.workers, args.validate),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
