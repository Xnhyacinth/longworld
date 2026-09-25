"""Compile source-bound financial-report tasks with final reader/mask audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.taskbank_context import (
    assemble_analyst_context,
    assemble_statement_context,
    bind_visible_evidence,
    render_document,
)
from longworld.synthesis.length_controller import get_tokenizer
from longworld.synthesis.unified_candidate_contract import physical_length_bin
from scripts.materialize_finance_taskbank_v2 import load_world
from scripts.p95_report_finance_shared import canonical, compile_tasks, digest
from scripts.train_sft import _render_chat, tokenize_assistant_only

SCHEMA = "longworld.p95-report-finance-shared.v1"
CODE_FILES = (
    "scripts/p95_report_finance_shared.py",
    "scripts/run_p95_report_finance_shared.py",
    "longworld/core/finance_taskbank.py",
    "longworld/core/finance_taskbank_v2.py",
    "longworld/core/taskbank_context.py",
    "scripts/train_sft.py",
)
SEPARATOR = "\n\nQuestion: "


def sha(path: Path) -> str:
    digest_value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest_value.update(block)
    return digest_value.hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def _lines(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(canonical(value) + "\n" for value in values), encoding="utf-8"
    )


def _token_span(offsets: list[tuple[int, int]], start: int, end: int) -> list[int]:
    hits = [
        index
        for index, (left, right) in enumerate(offsets)
        if right > left and left < end and right > start
    ]
    if not hits or offsets[hits[0]][0] > start or offsets[hits[-1]][1] < end:
        raise ValueError("visible evidence span not covered by tokenizer")
    return [hits[0], hits[-1] + 1]


def _project(bound: dict, mappings: dict[str, list[dict]], context: str) -> dict:
    for block in mappings[bound["record_id"]]:
        if (
            block["visible_start"]
            <= bound["visible_start"]
            < bound["visible_end"]
            <= block["visible_end"]
        ):
            start = (
                block["context_start"] + bound["visible_start"] - block["visible_start"]
            )
            end = start + len(bound["quote"])
            if context[start:end] != bound["quote"]:
                raise ValueError("source evidence did not survive reader composition")
            return {
                "fact_id": bound["fact_id"],
                "record_id": bound["record_id"],
                "role": bound["role"],
                "quote": bound["quote"],
                "context_span": [start, end],
                "visible_span": [bound["visible_start"], bound["visible_end"]],
                "source_span": [bound["start"], bound["end"]],
            }
    raise ValueError("source evidence excluded by reader context")


def _render(
    task: dict, variant: str, context: str, mappings: dict, bound: dict, tokenizer
) -> tuple[dict, dict, dict]:
    evidence = [
        _project(bound[identity], mappings, context) for identity in task["fact_ids"]
    ]
    answer = canonical(task["answer"])
    user = context + SEPARATOR + task["question"]
    messages = [
        {"role": "user", "content": user},
        {"role": "assistant", "content": answer},
    ]
    encoded = tokenize_assistant_only(tokenizer, messages, 262144)
    labels = encoded["labels"]
    input_tokens = sum(label == -100 for label in labels)
    supervised = len(labels) - input_tokens
    prompt = _render_chat(tokenizer, messages[:1], generation_prompt=True)
    user_start = prompt.find(user)
    if user_start < 0 or prompt.count(user) != 1:
        raise ValueError("user text is not unique in the chat template")
    offsets = tokenizer(prompt, truncation=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    for item in evidence:
        start, end = item["context_span"]
        item["prompt_token_span"] = _token_span(
            offsets, user_start + start, user_start + end
        )
        if item["prompt_token_span"][1] > input_tokens:
            raise ValueError("source evidence overlaps supervised answer")
    query = _token_span(
        offsets, user_start + len(context) + len(SEPARATOR), user_start + len(user)
    )[0]
    if supervised <= 0 or query >= input_tokens:
        raise ValueError("assistant-only mask or query boundary invalid")
    context_sha = hashlib.sha256(context.encode()).hexdigest()
    sample_id = "p95:" + digest([task["semantic_task_id"], variant, context_sha])
    reader = {"sample_id": sample_id, "messages": messages}
    index = {
        "sample_id": sample_id,
        "semantic_task_id": task["semantic_task_id"],
        "operation": task["operation"],
        "variant": variant,
        "context_sha256": context_sha,
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "full_chat_tokens": len(labels),
        "input_tokens": input_tokens,
        "supervised_tokens": supervised,
        "length_bin": physical_length_bin(len(labels)),
        "source_document_count": len(task["required_record_ids"]),
        "dependency_status": "formal_program_lineage_only; visible_alternatives_unsearched",
    }
    audit = {
        "sample_id": sample_id,
        "operation": task["operation"],
        "evidence": evidence,
        "query_token_start": query,
        "evidence_extent_tokens": max(x["prompt_token_span"][1] for x in evidence)
        - min(x["prompt_token_span"][0] for x in evidence),
        "last_evidence_to_query_tokens": query
        - max(x["prompt_token_span"][1] for x in evidence),
        "trace": task["trace"],
        "mask_checked": True,
    }
    return reader, index, audit


def compile_issuer(
    base_config_path: Path,
    expected_config_sha: str,
    expected_source_sha: str,
    output: Path,
) -> dict:
    if output.exists():
        raise ValueError("issuer output already exists")
    if sha(base_config_path) != expected_config_sha:
        raise ValueError("base issuer config pin mismatch")
    base = json.loads(base_config_path.read_text())
    source_path = ROOT / base["source_manifest"]
    if sha(source_path) != expected_source_sha:
        raise ValueError("source manifest pin mismatch")
    world = load_world(base)
    tasks, rejects = compile_tasks(world)
    documents = {doc["record_id"]: doc for doc in world["documents"]}
    rendered = {identity: render_document(doc) for identity, doc in documents.items()}
    facts = {fact["fact_id"]: fact for fact in world["facts"]}
    bound = {}
    for task in tasks:
        for identity in task["fact_ids"]:
            if identity in bound:
                continue
            fact = facts[identity]
            evidence = bind_visible_evidence(
                documents[fact["record_id"]],
                rendered[fact["record_id"]],
                {**fact["span"], "value": fact["value"]},
            )
            bound[identity] = {
                **evidence,
                "fact_id": identity,
                "record_id": fact["record_id"],
                "role": fact["role"],
            }
    tokenizer = get_tokenizer()
    readers, indices, audits = [], [], []
    context_cache = {}
    for variant, builder in (
        ("complete_statements", assemble_statement_context),
        ("analyst_packet", assemble_analyst_context),
    ):
        try:
            context_cache[variant] = builder(
                world["documents"],
                rendered,
                [doc["record_id"] for doc in world["documents"]],
            )
        except ValueError as error:
            rejects.append({"variant": variant, "reason": str(error)})
    for task in tasks:
        for variant, (context, mappings) in context_cache.items():
            try:
                reader, index, audit = _render(
                    task, variant, context, mappings, bound, tokenizer
                )
            except ValueError as error:
                rejects.append(
                    {
                        "operation": task["operation"],
                        "variant": variant,
                        "reason": str(error),
                    }
                )
                continue
            index.update(
                {
                    "source_kind": "real_finance",
                    "source_group": world["split_group_id"],
                    "domain": "finance",
                    "topic": world["issuer"]["name"],
                    "split": base["split"],
                }
            )
            readers.append(reader)
            indices.append(index)
            audits.append(audit)
    output.mkdir(parents=True)
    _lines(output / "reader.jsonl", readers)
    _lines(output / "sample_index.jsonl", indices)
    _lines(output / "audit.jsonl", audits)
    _write(output / "rejects.json", rejects)
    receipt = {
        "schema": SCHEMA,
        "source_manifest": {
            "path": base["source_manifest"],
            "sha256": expected_source_sha,
        },
        "base_config": {
            "path": str(base_config_path.resolve().relative_to(ROOT)),
            "sha256": expected_config_sha,
        },
        "source_collection_id": world["source_collection_id"],
        "world_instance_id": world["world_instance_id"],
        "source_group": world["split_group_id"],
        "split": base["split"],
        "issuer": world["issuer"]["name"],
        "documents": len(world["documents"]),
        "source_facts": len(world["facts"]),
        "semantic_tasks": len({item["semantic_task_id"] for item in indices}),
        "views": len(indices),
        "operations": dict(
            sorted(Counter(item["operation"] for item in indices).items())
        ),
        "length_bins": dict(
            sorted(Counter(item["length_bin"] for item in indices).items())
        ),
        "mask_checked": len(audits),
        "rejects": rejects,
        "code_sha256": {name: sha(ROOT / name) for name in CODE_FILES},
        "file_sha256": {
            name: sha(output / name)
            for name in (
                "reader.jsonl",
                "sample_index.jsonl",
                "audit.jsonl",
                "rejects.json",
            )
        },
        "train_ready": False,
    }
    _write(output / "manifest.json", receipt)
    return receipt


def run_batch(catalog_path: Path, output: Path, *, workers: int) -> dict:
    if not 1 <= workers <= 8 or output.exists():
        raise ValueError("invalid worker count or existing output")
    catalog = json.loads(catalog_path.read_text())
    if catalog.get("schema_version") != "longworld.finance-taskbank-long-catalog.v1":
        raise ValueError("wrong finance source catalog")
    jobs = catalog["jobs"]
    if len({job["issuer"] for job in jobs}) != len(jobs):
        raise ValueError("duplicate issuer")
    for job in jobs:
        if (
            sha(ROOT / job["config"]) != job["config_sha256"]
            or sha(ROOT / job["source_manifest"]) != job["source_manifest_sha256"]
        ):
            raise ValueError("catalog input pin mismatch")
    output.mkdir(parents=True)

    def run(job: dict) -> dict:
        trust = Path(os.path.expandvars(job["trust_file"]))
        if not trust.is_file() or "${" in str(trust):
            raise ValueError("source trust file unavailable")
        argv = [
            sys.executable,
            str(ROOT / "scripts/run_with_local_probe_trust.py"),
            "--trust-file",
            str(trust),
            "--role",
            "source",
        ]
        for key in (
            "HF_HOME",
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "TOKENIZERS_PARALLELISM",
        ):
            if key in os.environ:
                argv.extend(["--pass-env", key])
        argv.extend(
            [
                "--",
                sys.executable,
                str(Path(__file__)),
                "--worker",
                "--base-config",
                str(ROOT / job["config"]),
                "--config-sha",
                job["config_sha256"],
                "--source-sha",
                job["source_manifest_sha256"],
                "--output",
                str(output / job["issuer"]),
            ]
        )
        completed = subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False
        )
        (output / f"{job['issuer']}.stdout.log").write_text(completed.stdout)
        (output / f"{job['issuer']}.stderr.log").write_text(completed.stderr)
        if completed.returncode:
            return {
                "issuer": job["issuer"],
                "status": "failed",
                "returncode": completed.returncode,
            }
        manifest = output / job["issuer"] / "manifest.json"
        value = json.loads(manifest.read_text())
        return {
            "issuer": job["issuer"],
            "status": "verified_candidate",
            "manifest_sha256": sha(manifest),
            "views": value["views"],
            "semantic_tasks": value["semantic_tasks"],
            "operations": value["operations"],
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))
    receipt = {
        "schema": SCHEMA + ".batch",
        "catalog_sha256": sha(catalog_path),
        "jobs": results,
        "views": sum(row.get("views", 0) for row in results),
        "semantic_tasks": sum(row.get("semantic_tasks", 0) for row in results),
        "verified_sources": sum(
            row["status"] == "verified_candidate" for row in results
        ),
        "train_ready": False,
    }
    _write(output / "batch_manifest.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--base-config", type=Path)
    parser.add_argument("--config-sha")
    parser.add_argument("--source-sha")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        value = compile_issuer(
            args.base_config, args.config_sha, args.source_sha, args.output
        )
        print(
            canonical(
                {"views": value["views"], "semantic_tasks": value["semantic_tasks"]}
            )
        )
    else:
        value = run_batch(args.catalog, args.output, workers=args.workers)
        print(canonical(value))
        if value["verified_sources"] != len(value["jobs"]):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
