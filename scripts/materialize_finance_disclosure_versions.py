#!/usr/bin/env python3
"""Replay version-sensitive disclosures, then gate natural context capacity."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.finance_disclosure_versions import (
    canonical,
    compile_asof_tasks,
    digest,
    execute,
    load_disclosure_world,
    shortcut_probes,
    slots,
)
from longworld.core.taskbank_context import (
    assemble_statement_context,
    bind_visible_evidence,
    render_document,
    text_sha256,
)
from longworld.core.taskbank_long_context import choose_primary, context_options
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.materialize_finance_histories import _load_tokenizer
from scripts.materialize_finance_taskbank import project_evidence
from scripts.materialize_finance_taskbank_v2 import CODE_FILES as P64_CODE_FILES
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.finance-disclosure-build.v1"
RECEIPT_SCHEMA = "longworld.finance-disclosure-receipt.v1"
CODE_FILES = (
    *P64_CODE_FILES,
    "longworld/core/finance_disclosure_versions.py",
    "scripts/materialize_finance_disclosure_versions.py",
    "scripts/train_sft.py",
)


def file_digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def raw_window_coverage(offsets, evidence):
    starts = [p[0] for p in offsets]
    ends = [p[1] for p in offsets]
    intervals = [
        (bisect_right(ends, e["context_start"]), bisect_left(starts, e["context_end"]))
        for e in evidence
    ]
    if not intervals or any(lo >= hi for lo, hi in intervals):
        raise ValueError("numeric evidence is not covered by token offsets")
    width = max(hi for _, hi in intervals) - min(lo for lo, _ in intervals)
    return {
        "canonical_numeric_operand_envelope_tokens": width,
        "any_contiguous_window_covers_canonical_numeric_operands": {
            str(n): width <= n for n in (4096, 8192, 16384)
        },
        "gold_answer_used": False,
        "solver_correctness_tested": False,
        "alternative_proof_search_complete": False,
    }


def build(config_path, output, *, validate=False):
    config_path = config_path.absolute()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported disclosure build config")
    native_config_path = ROOT / config["source_config"]
    if file_digest(native_config_path) != config["source_config_sha256"]:
        raise ValueError("native config changed")
    native_config = json.loads(native_config_path.read_text())
    world = load_disclosure_world(native_config)
    if world.native["source_manifest"]["sha256"] != config["source_manifest_sha256"]:
        raise ValueError("native source changed")
    tasks = compile_asof_tasks(world)
    documents = world.native["documents"]
    by_doc = {d["record_id"]: d for d in documents}
    by_claim = {c["claim_id"]: c for c in world.claims}
    rendered = {d["record_id"]: render_document(d) for d in documents}
    # This compact control is query/gold-blind: every primary statement from
    # every available filing is retained, not just rows needed for an answer.
    compact, mappings = assemble_statement_context(
        documents, rendered, [d["record_id"] for d in documents]
    )
    compact_context = {
        "text": compact,
        "sha256": text_sha256(compact),
        "mappings": mappings,
        "offsets": None,
    }
    payloads = {}
    rows = []
    short = []
    long = []
    holds = []
    controls = []
    context_cache = {}
    offset_cache = {}
    with sanitized_attestation_environment():
        tc = native_config["tokenizer"]
        tokenizer = _load_tokenizer(tc["model_id"], tc["revision"])
        assets = resolved_tokenizer_asset_manifest_sha256(
            tc["model_id"], tc["revision"]
        )
        compact_tokens = len(tokenizer.encode(compact, add_special_tokens=False))
        compact_context.update(
            tokens=compact_tokens, capacity_bin=None, matches_exact_token_range=False
        )
        payloads["contexts/" + compact_context["sha256"] + ".txt"] = compact
        for task in tasks:
            result = execute(world, task["parameters"])
            if (
                result["answer"] != task["answer"]
                or result["consumed_claim_ids"] != task["consumed_claim_ids"]
            ):
                raise ValueError("task replay changed")
            targets = {
                (t["metric"], canonical(t["valid_time"]))
                for t in task["parameters"]["targets"]
            }
            relevant = tuple(
                sorted(
                    {
                        c["record_id"]
                        for c in world.claims
                        if (c["metric"], canonical(c["valid_time"])) in targets
                    }
                )
            )
            if relevant not in context_cache:
                options = context_options(documents, rendered, relevant, tokenizer)
                context_cache[relevant] = (
                    choose_primary(options, len(relevant))
                    if options
                    else compact_context
                )
            context = context_cache[relevant]
            context_tokens = context["tokens"]
            payloads["contexts/" + context["sha256"] + ".txt"] = context["text"]
            evidence = []
            compact_evidence = []
            for cid in task["consumed_claim_ids"]:
                c = by_claim[cid]
                span = {
                    **c["span"],
                    "record_id": c["record_id"],
                    "value": c["value"],
                    "claim_id": cid,
                }
                bound = bind_visible_evidence(
                    by_doc[c["record_id"]], rendered[c["record_id"]], span
                )
                evidence.append(project_evidence(bound, context))
                compact_evidence.append(project_evidence(bound, compact_context))
            probes = shortcut_probes(world, task)
            if context["sha256"] not in offset_cache:
                offset_cache[context["sha256"]] = tokenizer(
                    context["text"],
                    add_special_tokens=False,
                    return_offsets_mapping=True,
                    truncation=False,
                )["offset_mapping"]
            probes["original_raw_windows"] = raw_window_coverage(
                offset_cache[context["sha256"]], evidence
            )
            probes["all_statement_compact_control"] = {
                "context_tokens": compact_tokens,
                "context_sha256": compact_context["sha256"],
                "all_canonical_evidence_present": True,
                "all_supplied_filings_retained": True,
                "gold_answer_used_for_selection": False,
                "universal_invalidity_claim": False,
            }
            controls.append(
                {
                    "semantic_task_id": task["semantic_task_id"],
                    "control_context_path": "contexts/"
                    + compact_context["sha256"]
                    + ".txt",
                    "visible_evidence": compact_evidence,
                    "primary_sample_count_contribution": 0,
                }
            )
            messages = [
                {
                    "role": "user",
                    "content": context["text"] + "\n\nQuestion:\n" + task["question"],
                },
                {"role": "assistant", "content": canonical(task["answer"])},
            ]
            full_tokens = len(
                tokenize_assistant_only(tokenizer, messages, 1 << 30)["input_ids"]
            )
            if probes["latest_values_ignoring_availability_correct"]:
                state = "hold_latest_values_shortcut"
            elif probes["single_primary_statement_filing_sufficient"]:
                state = "hold_single_primary_filing_proof"
            elif full_tokens > 262144:
                state = "hold_full_message_overflow"
            elif context_tokens <= 32768:
                state = "short_local_only"
            else:
                state = "long_local_candidate_unverified_dependency"
            row = {
                **task,
                "sample_id": "sample:"
                + digest([task["semantic_task_id"], context["sha256"]]),
                "split": native_config["split"],
                "context_path": "contexts/" + context["sha256"] + ".txt",
                "context_sha256": context["sha256"],
                "context_tokens": context_tokens,
                "full_message_tokens": full_tokens,
                "context_policy": "natural_relevant_disclosure_history",
                "context_plan": context.get("plan", []),
                "capacity_bin": context["capacity_bin"],
                "matches_exact_token_range": context["matches_exact_token_range"],
                "visible_evidence": evidence,
                "probes": probes,
                "admission_state": state,
                "long_dependency_eligible": False,
                "training_release_eligible": False,
                "production_eligible": False,
            }
            rows.append(row)
            sample = {"sample_id": row["sample_id"], "messages": messages}
            if state == "short_local_only":
                short.append(sample)
            elif state == "long_local_candidate_unverified_dependency":
                long.append(sample)
            else:
                holds.append(
                    {"semantic_task_id": task["semantic_task_id"], "reason": state}
                )
    payloads["claims.jsonl"] = "".join(canonical(c) + "\n" for c in world.claims)
    payloads["tasks.jsonl"] = "".join(canonical(r) + "\n" for r in rows)
    payloads["short_sft_candidates.jsonl"] = "".join(canonical(r) + "\n" for r in short)
    payloads["long_sft_candidates.jsonl"] = "".join(canonical(r) + "\n" for r in long)
    payloads["rejections.json"] = (
        canonical({"source_claim_rejections": world.rejections, "task_holds": holds})
        + "\n"
    )
    payloads["compact_controls.jsonl"] = "".join(canonical(c) + "\n" for c in controls)
    changed_slots = [
        k for k, v in slots(world).items() if len({c["value"] for c in v}) > 1
    ]
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "config_sha256": file_digest(config_path),
        "source_manifest": world.native["source_manifest"],
        "source_config": str(native_config_path),
        "source_config_sha256": file_digest(native_config_path),
        "source_collection_id": world.native["source_collection_id"],
        "split_group_id": world.native["split_group_id"],
        "split": native_config["split"],
        "source_documents": len(documents),
        "admitted_comparative_claims": len(world.claims),
        "source_claim_rejections": len(world.rejections),
        "conflicting_metric_periods": len(changed_slots),
        "semantic_tasks": len(tasks),
        "by_family": dict(Counter(t["family"] for t in tasks)),
        "by_admission_state": dict(Counter(r["admission_state"] for r in rows)),
        "short_local_candidates": len(short),
        "long_local_candidates": len(long),
        "long_dependency_eligible": 0,
        "compact_all_statement_context_tokens": compact_tokens,
        "compact_control_views": len(controls),
        "capacity_bins": dict(
            Counter(
                str(r["capacity_bin"])
                for r in rows
                if r["admission_state"] == "long_local_candidate_unverified_dependency"
            )
        ),
        "exact_token_ranges": dict(
            Counter(
                str(r["capacity_bin"]) for r in rows if r["matches_exact_token_range"]
            )
        ),
        "raw_numeric_operand_window_coverage_counts": {
            str(n): sum(
                r["probes"]["original_raw_windows"][
                    "any_contiguous_window_covers_canonical_numeric_operands"
                ][str(n)]
                for r in rows
            )
            for n in (4096, 8192, 16384)
        },
        "max_full_message_tokens": max(
            (r["full_message_tokens"] for r in rows), default=0
        ),
        "latest_values_shortcut_failures": sum(
            not r["probes"]["latest_values_ignoring_availability_correct"] for r in rows
        ),
        "single_primary_filing_sufficiency_failures": sum(
            not r["probes"]["single_primary_statement_filing_sufficient"] for r in rows
        ),
        "alternative_proof_search_complete": False,
        "strict_long_dependency_verified": False,
        "production_eligible": False,
        "training_release_eligible": False,
        "tokenizer": {**tc, "asset_manifest_sha256": assets},
        "code_sha256": {p: file_digest(ROOT / p) for p in sorted(set(CODE_FILES))},
        "files": {
            p: hashlib.sha256(text.encode()).hexdigest() for p, text in payloads.items()
        },
        "yield_funnel": {
            "native_source_documents": len(documents),
            "comparative_claims": len(world.claims),
            "actual_conflicting_metric_periods": len(changed_slots),
            "version_sensitive_tasks": len(tasks),
            "compact_controls_below_32k": len(tasks) if compact_tokens <= 32768 else 0,
            "primary_natural_long_candidates": len(long),
            "primary_short_candidates": len(short),
            "long_dependency_qualified": 0,
        },
    }
    output = output.absolute()
    if validate:
        if output.is_symlink() or any(p.is_symlink() for p in output.rglob("*")):
            raise ValueError("symlink output member")
        actual = {
            p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()
        }
        if actual != set(payloads) | {"BUILD_RECEIPT.json"}:
            raise ValueError("unexpected disclosure output member")
        for name, text in payloads.items():
            if (output / name).read_text() != text:
                raise ValueError(
                    "disclosure source/context/task replay mismatch: " + name
                )
        if json.loads((output / "BUILD_RECEIPT.json").read_text()) != receipt:
            raise ValueError("disclosure receipt changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        for name, text in payloads.items():
            path = output / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
        (output / "BUILD_RECEIPT.json").write_text(canonical(receipt) + "\n")
    return {
        "status": "PASS",
        "receipt_path": str(output / "BUILD_RECEIPT.json"),
        **{
            k: receipt[k]
            for k in (
                "semantic_tasks",
                "short_local_candidates",
                "long_local_candidates",
                "long_dependency_eligible",
                "compact_all_statement_context_tokens",
                "yield_funnel",
                "by_family",
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.config, args.output, validate=args.validate), indent=2))


if __name__ == "__main__":
    main()
