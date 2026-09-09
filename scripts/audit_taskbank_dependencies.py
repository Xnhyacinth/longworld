#!/usr/bin/env python3
"""Audit unique taskbank contexts on bounded CPU processes; no model inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import attestation_environment_names
from longworld.core.taskbank_dependency_audit import (
    REVISION,
    audit_taskbank_sample,
    exact_offsets,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

TOKENIZER = None


def initialize(model, revision):
    global TOKENIZER
    for name in attestation_environment_names():
        os.environ.pop(name, None)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from transformers import AutoTokenizer

    TOKENIZER = AutoTokenizer.from_pretrained(
        model, revision=revision, local_files_only=True, trust_remote_code=False
    )


def audit_group(job):
    path, rows = job
    context = Path(path).read_text()
    offsets = exact_offsets(context, TOKENIZER)
    return [audit_taskbank_sample(row, context, token_offsets=offsets) for row in rows]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--model", default="Qwen/Qwen3.5-4B")
    parser.add_argument(
        "--revision", default="a7b0d22b993d71000cf2eadfb37222a67cee521e"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must be new; preserve prior audit bytes")
    groups = defaultdict(list)
    inputs = []
    for path in sorted(args.batch.glob("*/tasks.jsonl")):
        inputs.append({"path": str(path.resolve()), "sha256": digest(path)})
        for line in path.read_text().splitlines():
            row = json.loads(line)
            context_path = (path.parent / row["context_path"]).resolve()
            if not context_path.is_relative_to(path.parent.resolve()):
                raise ValueError("context path escapes issuer directory")
            groups[str(context_path)].append(row)
    if not groups:
        raise ValueError("no tasks")
    asset_sha = resolved_tokenizer_asset_manifest_sha256(args.model, args.revision)
    reports = []
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=initialize,
        initargs=(args.model, args.revision),
    ) as pool:
        for index, result in enumerate(pool.map(audit_group, sorted(groups.items()))):
            reports.extend(result)
            print(
                f"audited {index + 1}/{len(groups)} contexts, {len(reports)} rows",
                flush=True,
            )
    args.output.mkdir(parents=True)
    rows_path = args.output / "samples.jsonl"
    rows_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in reports))
    summary = {
        "schema_version": REVISION,
        "rows": len(reports),
        "unique_contexts": len(groups),
        "semantic_tasks": len({r["semantic_task_id"] for r in reports}),
        "profiles": dict(Counter(r["profile"] for r in reports)),
        "families": dict(Counter(r["family"] for r in reports)),
        "bound_numeric_spans_fit_window": {
            str(w): sum(
                r["raw_token_window_support"][str(w)]["all_bound_spans_fit"]
                for r in reports
            )
            for w in (4096, 8192, 16384)
        },
        "whole_visible_lines_fit_window": {
            str(w): sum(
                r["whole_visible_line_window_support"][str(w)]["all_bound_spans_fit"]
                for r in reports
            )
            for w in (4096, 8192, 16384)
        },
        "latest_filing_all_numeric_surfaces_present": sum(
            r["latest_filing_all_numeric_surfaces_present"] for r in reports
        ),
        "latest_filing_all_numeric_surfaces_present_multi_filing": sum(
            r["latest_filing_all_numeric_surfaces_present"]
            and len(r["source_documents"]) > 1
            for r in reports
        ),
        "oracle_located_lookup_replays": sum(
            r["oracle_located_lookup_answer_replay"].startswith("passed")
            for r in reports
        ),
        "strict_verified": 0,
        "neural_baseline": "unmeasured",
        "alternative_proof_search_complete": False,
        "interpretation": "Exact token support geometry and gold-assisted numeric repeat opportunities; no complete alternative-proof or model-failure claim.",
        "tokenizer": {
            "model": args.model,
            "revision": args.revision,
            "asset_manifest_sha256": asset_sha,
        },
        "inputs": inputs,
        "samples_sha256": digest(rows_path),
        "audit_code": {
            str(p.relative_to(ROOT)): digest(p)
            for p in (
                ROOT / "longworld/core/taskbank_dependency_audit.py",
                Path(__file__).resolve(),
            )
        },
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
