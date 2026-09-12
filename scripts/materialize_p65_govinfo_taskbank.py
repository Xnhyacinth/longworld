#!/usr/bin/env python3
"""Build/replay the P65 GovInfo printed-version taskbank, without raw XML files."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p65_govinfo_taskbank import (
    PROGRAMS,
    REVISION,
    canonical,
    execute,
    maps_from_blocks,
    question,
    render,
    sha,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from reports.p65_govinfo_amount_exception_preflight import work as refetch_source

CODE = (
    "longworld/core/p65_govinfo_taskbank.py",
    "scripts/materialize_p65_govinfo_taskbank.py",
    "reports/p65_govinfo_amount_exception_preflight.py",
    "reports/p49_govinfo_bill_text_disposition_preflight.py",
    "scripts/train_sft.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source(config):
    path = ROOT / config["source_world"]["path"]
    if path.is_symlink() or digest(path) != config["source_world"]["sha256"]:
        raise ValueError("derived source binding mismatch")
    world = json.loads(path.read_text())
    preflight_path = ROOT / config["original_source_config"]["path"]
    if digest(preflight_path) != config["original_source_config"]["sha256"]:
        raise ValueError("original official source pins changed")
    preflight = json.loads(preflight_path.read_text())
    chains = [c for c in preflight["chains"] if c["bill_id"] == world["bill_id"]]
    if len(chains) != 1:
        raise ValueError("source chain missing")
    observed, _ = refetch_source(chains[0], preflight)
    if canonical(observed) != canonical(world):
        raise ValueError("derived source does not reproduce official XML")
    return world


def window_probe(blocks, offsets, programs):
    starts, ends = [p[0] for p in offsets], [p[1] for p in offsets]
    intervals = [
        (bisect.bisect_right(ends, b["start"]), bisect.bisect_left(starts, b["end"]))
        for b in blocks
    ]
    reports = {program: {} for program in programs}
    gold = {p: execute(maps_from_blocks(blocks), p) for p in programs}
    for width in (4096, 8192, 16384):
        maximum_start = max(0, len(offsets) - width)
        events = {0}
        for left, right in intervals:
            lo, hi = max(0, right - width), min(left, maximum_start)
            if lo <= hi:
                events.add(lo)
                if hi + 1 <= maximum_start:
                    events.add(hi + 1)
        witnesses = {}
        for start in sorted(events):
            selected = [
                b
                for b, (a, z) in zip(blocks, intervals, strict=True)
                if start <= a and z <= start + width
            ]
            maps = maps_from_blocks(selected)
            for program in programs:
                if program not in witnesses and execute(maps, program) == gold[program]:
                    witnesses[program] = {
                        "token_start": start,
                        "token_end": min(len(offsets), start + width),
                        "complete_records": len(selected),
                        "full_population_records": len(blocks),
                    }
        for program in programs:
            reports[program][str(width)] = {
                "answer_em": program in witnesses,
                "witness": witnesses.get(program),
                "scope_completeness_proven": False,
            }
    return reports


def build(config_path, output):
    config = json.loads(config_path.read_text())
    if (
        config["schema_version"] != "longworld.p65-govinfo-taskbank-build.v1"
        or config["split"] != "train"
    ):
        raise ValueError("unsupported config/split")
    world = load_source(config)
    tokenizer_config = config["tokenizer"]
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        from scripts.train_sft import tokenize_assistant_only

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_config["model_id"],
            revision=tokenizer_config["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
        assets = resolved_tokenizer_asset_manifest_sha256(
            tokenizer_config["model_id"], tokenizer_config["revision"]
        )
        output.mkdir(parents=True, exist_ok=False)
        (output / "contexts").mkdir()
        rows, sft, short, rejects = [], [], [], []
        source_collection = sha(canonical(world["sources"]))
        world_id = sha(canonical([world["bill_id"], source_collection]))
        for prefix in config["scopes"]:
            context, blocks, maps = render(world, prefix)
            if not blocks:
                rejects.append({"scope": prefix, "reason": "empty_source_scope"})
                continue
            encoded = tokenizer(
                context, add_special_tokens=False, return_offsets_mapping=True
            )
            tokens = len(encoded["input_ids"])
            answers = {p: execute(maps, p) for p in PROGRAMS}
            accepted = [p for p, a in answers.items() if a["locations"]]
            for program in PROGRAMS:
                if program not in accepted:
                    rejects.append(
                        {
                            "scope": prefix,
                            "program": program,
                            "reason": "no_nonempty_answer",
                        }
                    )
            if not accepted:
                continue
            probes = window_probe(blocks, encoded["offset_mapping"], accepted)
            context_sha = sha(context)
            context_path = f"contexts/{context_sha}.txt"
            (output / context_path).write_text(context)
            for program in accepted:
                semantic = sha(
                    canonical([REVISION, source_collection, prefix, program])
                )
                prompt = question(world, prefix, program)
                messages = [
                    {"role": "user", "content": context + "\n\nQuestion:\n" + prompt},
                    {"role": "assistant", "content": canonical(answers[program])},
                ]
                encoded_chat = tokenize_assistant_only(tokenizer, messages, 1 << 30)
                full_tokens = len(encoded_chat["input_ids"])
                assistant_tokens = sum(
                    token != -100 for token in encoded_chat["labels"]
                )
                latest = (
                    execute({"eas": {}, "eah": maps["eah"]}, program)
                    == answers[program]
                )
                earliest = (
                    execute({"eas": maps["eas"], "eah": {}}, program)
                    == answers[program]
                )
                local = (
                    latest
                    or earliest
                    or any(p["answer_em"] for p in probes[program].values())
                )
                sample_id = sha(
                    canonical([semantic, context_sha, prompt, answers[program]])
                )
                stage = (
                    "long"
                    if tokens > 32768 and full_tokens <= 262144
                    else "short"
                    if full_tokens <= 262144
                    else "overflow"
                )
                row = {
                    "schema_version": "longworld.p65-govinfo-taskbank-sample.v1",
                    "sample_id": sample_id,
                    "semantic_task_id": semantic,
                    "variant_family_id": "variants:" + semantic,
                    "variant": "full",
                    "source_collection_id": source_collection,
                    "world_instance_id": world_id,
                    "source_group_id": "govinfo:" + world["bill_id"],
                    "split": "train",
                    "program_id": program,
                    "parameters": {
                        "scope": prefix,
                        "from_stage": "eas",
                        "to_stage": "eah",
                    },
                    "question": prompt,
                    "answer": answers[program],
                    "context_path": context_path,
                    "context_sha256": context_sha,
                    "context_tokens": tokens,
                    "full_hf_chat_tokens": full_tokens,
                    "assistant_tokens": assistant_tokens,
                    "capacity_bin": next(
                        (c for c in (65536, 131072, 262144) if tokens <= c), None
                    ),
                    "exact_numeric_range": next(
                        (
                            str(hi)
                            for lo, hi in (
                                (64000, 65536),
                                (128000, 131072),
                                (256000, 262144),
                            )
                            if lo <= tokens <= hi
                        ),
                        None,
                    ),
                    "classification": stage,
                    "profile": "retrieval_candidate"
                    if local
                    else "integration_candidate",
                    "window_probe": probes[program],
                    "latest_print_only_answer_em": latest,
                    "earliest_print_only_answer_em": earliest,
                    "empty_context_constant_answer_em": execute(
                        {"eas": {}, "eah": {}}, program
                    )
                    == answers[program],
                    "neural_baseline": "unmeasured",
                    "complete_record_window_probe_is_full_semantic_proof": False,
                    "strict_long_dependency_verified": False,
                    "production_eligible": False,
                    "training_release_eligible": False,
                }
                rows.append(row)
                if stage == "long":
                    sft.append({"sample_id": sample_id, "messages": messages})
                elif stage == "short":
                    short.append({"sample_id": sample_id, "messages": messages})
                else:
                    rejects.append(
                        {"sample_id": sample_id, "reason": "complete_hf_chat_overflow"}
                    )
        for name, values in [
            ("tasks.jsonl", rows),
            ("sft_candidates.jsonl", sft),
            ("short_sft_candidates.jsonl", short),
        ]:
            (output / name).write_text("".join(canonical(v) + "\n" for v in values))
        (output / "rejections.json").write_text(canonical(rejects) + "\n")
    receipt = {
        "schema_version": "longworld.p65-govinfo-taskbank-receipt.v1",
        "status": "verified_official_hash_pinned_local_candidates",
        "config_sha256": digest(config_path),
        "source_world": config["source_world"],
        "source_collection_id": source_collection,
        "world_instance_id": world_id,
        "source_group_id": "govinfo:" + world["bill_id"],
        "source_verification": "live_official_URL_hash_size_rights_stage_date_action_and_derivative_replay",
        "source_hmac_attestation": False,
        "sources": world["sources"],
        "tokenizer": dict(tokenizer_config, asset_manifest_sha256=assets),
        "semantic_tasks": len(rows),
        "training_views": len(rows),
        "long_sft_rows": len(sft),
        "short_sft_rows": len(short),
        "families": dict(Counter(r["program_id"] for r in rows)),
        "profiles": dict(Counter(r["profile"] for r in rows)),
        "long_capacity_bins": dict(
            Counter(
                str(r["capacity_bin"]) for r in rows if r["classification"] == "long"
            )
        ),
        "long_exact_numeric_ranges": dict(
            Counter(
                r["exact_numeric_range"]
                for r in rows
                if r["classification"] == "long" and r["exact_numeric_range"]
            )
        ),
        "unique_contexts": len({r["context_sha256"] for r in rows}),
        "strict_verified": 0,
        "net_new_source_entities": 0,
        "production_eligible": False,
        "training_release_eligible": False,
        "code_sha256": {name: digest(ROOT / name) for name in CODE},
        "files": {
            str(p.relative_to(output)): digest(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    (output / "BUILD_RECEIPT.json").write_text(canonical(receipt) + "\n")
    return receipt


def validate(config_path, output):
    with tempfile.TemporaryDirectory(prefix="p65-govinfo-replay-") as temporary:
        replay = Path(temporary) / "bank"
        receipt = build(config_path, replay)
        expected = {
            str(p.relative_to(replay)) for p in replay.rglob("*") if p.is_file()
        }
        observed = {
            str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()
        }
        if expected != observed or any(p.is_symlink() for p in output.rglob("*")):
            raise ValueError("output member inventory mismatch")
        for name in expected:
            if (output / name).read_bytes() != (replay / name).read_bytes():
                raise ValueError(
                    "source/task/SFT/window/receipt replay mismatch: " + name
                )
    return {
        "status": "PASS",
        "semantic_tasks": receipt["semantic_tasks"],
        "long_sft_rows": receipt["long_sft_rows"],
        "short_sft_rows": receipt["short_sft_rows"],
        "strict_verified": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    result = (validate if args.validate else build)(args.config, args.output)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("files", "code_sha256", "sources")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
