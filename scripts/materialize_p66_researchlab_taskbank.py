#!/usr/bin/env python3
"""Build and replay a bounded P66 ResearchLab revision taskbank."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p66_researchlab_taskbank import (
    REVISION,
    bundle_path_sets,
    canonical,
    delta_signature,
    elife_records,
    exact_numeric_range,
    load_text_tar,
    render_records,
    sha256_text,
)
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256
from scripts.train_sft import tokenize_assistant_only

SCHEMA = "longworld.p66-researchlab-taskbank-config.v1"
RECEIPT = "longworld.p66-researchlab-taskbank-receipt.v1"
CODE = (
    "longworld/core/p66_researchlab_taskbank.py",
    "scripts/materialize_p66_researchlab_taskbank.py",
)
_TOKENIZER = None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _init_tokenizer(config: dict) -> None:
    global _TOKENIZER
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        _TOKENIZER = AutoTokenizer.from_pretrained(
            config["model_id"],
            revision=config["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )


def token_count(text: str) -> int:
    if _TOKENIZER is None:
        raise RuntimeError("tokenizer is not initialized")
    return len(_TOKENIZER(text, add_special_tokens=False)["input_ids"])


def checked_config(path: Path) -> dict:
    config = json.loads(path.read_text())
    if config.get("schema_version") != SCHEMA:
        raise ValueError("unsupported P66 ResearchLab config")
    if not config.get("local_derivative_synthesis_authorized"):
        raise ValueError("local derivative synthesis is not authorized")
    families = config.get("families")
    if not families or len({item["family_id"] for item in families}) != len(families):
        raise ValueError("source families are missing or duplicated")
    for family in families:
        for source in family["sources"]:
            source_path = ROOT / source["path"]
            if (
                not source_path.is_file()
                or source_path.is_symlink()
                or digest(source_path) != source["sha256"]
            ):
                raise ValueError(f"source binding mismatch: {source['path']}")
        bundle = family.get("source_workflow_bundle")
        if bundle:
            bundle_path = ROOT / bundle["path"]
            payload = json.loads(bundle_path.read_text())
            attestation = payload.get("attestation", {})
            if (
                digest(bundle_path) != bundle["sha256"]
                or attestation.get("role") != "source"
                or attestation.get("scheme") != "hmac-sha256-v2"
                or attestation.get("key_id") != bundle["key_id"]
            ):
                raise ValueError("source workflow bundle identity mismatch")
            for entry in payload.get("entries", []):
                entry_path = bundle_path.parent / entry["path"]
                if digest(entry_path) != entry["sha256"]:
                    raise ValueError("source workflow manifest binding mismatch")
    if {item["split"] for item in families} - {"train", "eval"}:
        raise ValueError("invalid family split")
    return config


def _answer(paths: list[str], signatures: dict[str, dict[str, str]]) -> list[dict]:
    return [{"path": path, **signatures[path]} for path in sorted(paths)]


def _question(old_version: str, new_version: str, paths: list[str]) -> str:
    return (
        f"Compare complete source records from {old_version} and {new_version}. "
        "For every requested path, return the exact JSON array sorted by path with "
        "keys path, status, old_excerpt, and new_excerpt. Use added, removed, or "
        "replaced for status. Return UNKNOWN if either complete version record for "
        "any requested path is absent. Requested paths: " + canonical(sorted(paths))
    )


def _tar_specs(family: dict) -> tuple[list[dict], list[dict]]:
    versions = []
    for source in family["sources"]:
        path = ROOT / source["path"]
        versions.append((source["version"], load_text_tar(path)))
    accepted_specs: list[dict] = []
    rejected_specs: list[dict] = []
    for (old_version, old_files), (new_version, new_files) in pairwise(versions):
        changed = sorted(
            path
            for path in old_files.keys() & new_files.keys()
            if old_files[path] != new_files[path]
        )
        signatures = {
            path: signature
            for path in changed
            if (signature := delta_signature(old_files[path], new_files[path]))
        }
        weighted = [
            (path, token_count(old_files[path]) + token_count(new_files[path]))
            for path in signatures
        ]
        seen: set[tuple[str, ...]] = set()
        for capacity, minimum, maximum in (
            (65536, 32769, 65536),
            (131072, 65537, 131072),
            (262144, 131073, 262144),
        ):
            for paths in bundle_path_sets(weighted, minimum=minimum, maximum=maximum):
                if paths in seen:
                    continue
                seen.add(paths)
                selected = [(p, old_files[p], new_files[p]) for p in paths]
                context, spans = render_records(old_version, new_version, selected)
                accepted_specs.append(
                    {
                        "family_id": family["family_id"],
                        "split": family["split"],
                        "old_version": old_version,
                        "new_version": new_version,
                        "paths": list(paths),
                        "context": context,
                        "record_spans": spans,
                        "answer": _answer(list(paths), signatures),
                        "expected_capacity": capacity,
                        "compact_complete_child_tokens": sum(
                            weight for path, weight in weighted if path in paths
                        ),
                    }
                )
        if not seen:
            rejected_specs.append(
                {
                    "family_id": family["family_id"],
                    "split": family["split"],
                    "old_version": old_version,
                    "new_version": new_version,
                    "changed_scientific_paths": sorted(signatures),
                    "complete_changed_pair_tokens": sum(
                        weight for _, weight in weighted
                    ),
                    "reason": (
                        "no_substantive_scientific_delta"
                        if not signatures
                        else "compact_complete_children_fit_16k_or_context_below_32k"
                    ),
                }
            )
    return accepted_specs, rejected_specs


def _elife_specs(family: dict) -> tuple[list[dict], list[dict]]:
    inventory = json.loads((ROOT / family["sources"][0]["path"]).read_text())
    records = sorted(inventory["records"], key=lambda item: item["revision_id"])
    (old_sections, old_full), (new_sections, new_full) = [
        elife_records(item["text"]) for item in records
    ]
    paths = sorted(
        path
        for path in old_sections.keys() & new_sections.keys()
        if old_sections[path] != new_sections[path]
    )
    signatures = {
        path: signature
        for path in paths
        if (signature := delta_signature(old_sections[path], new_sections[path]))
    }
    compact = sum(
        token_count(old_sections[path]) + token_count(new_sections[path])
        for path in signatures
    )
    context, spans = render_records(
        records[0]["revision_id"],
        records[1]["revision_id"],
        [("complete-article-visible-text", old_full, new_full)],
    )
    if compact <= 16384:
        return [], [
            {
                "family_id": family["family_id"],
                "split": family["split"],
                "old_version": records[0]["revision_id"],
                "new_version": records[1]["revision_id"],
                "changed_scientific_paths": sorted(signatures),
                "complete_changed_pair_tokens": compact,
                "reason": "compact_complete_children_fit_16k",
            }
        ]
    return [
        {
            "family_id": family["family_id"],
            "split": family["split"],
            "old_version": records[0]["revision_id"],
            "new_version": records[1]["revision_id"],
            "paths": sorted(signatures),
            "context": context,
            "record_spans": spans,
            "answer": _answer(sorted(signatures), signatures),
            "expected_capacity": 262144,
            "compact_complete_child_tokens": compact,
        }
    ], []


def _evaluate(spec: dict) -> dict:
    question = _question(spec["old_version"], spec["new_version"], spec["paths"])
    answer_text = canonical(spec["answer"])
    context_tokens = token_count(spec["context"])
    required_spans = list(spec["record_spans"].values())
    required_records = set(spec["record_spans"])
    cover_start = min(start for start, _ in required_spans)
    cover_end = max(end for _, end in required_spans)
    cover_tokens = token_count(spec["context"][cover_start:cover_end])
    messages = [
        {"role": "user", "content": spec["context"] + "\n\nQuestion:\n" + question},
        {"role": "assistant", "content": answer_text},
    ]
    encoded = tokenize_assistant_only(_TOKENIZER, messages, 1 << 30)
    capacity = next(
        (limit for limit in (65536, 131072, 262144) if context_tokens <= limit), None
    )
    reason = "complete_record_contract_local_candidate"
    if context_tokens <= 32768:
        reason = "context_below_32k"
    elif capacity is None:
        reason = "context_above_256k"
    elif spec["compact_complete_child_tokens"] <= 16384:
        reason = "compact_complete_children_fit_16k"
    elif cover_tokens <= 16384:
        reason = "positive_evidence_cover_fits_16k"
    return {
        **spec,
        "question": question,
        "messages": messages,
        "context_tokens": context_tokens,
        "full_hf_chat_tokens": len(encoded["input_ids"]),
        "assistant_tokens": sum(label != -100 for label in encoded["labels"]),
        "capacity_bin": capacity,
        "exact_numeric_range": exact_numeric_range(context_tokens),
        "complete_record_span_tokens": cover_tokens,
        "required_complete_records": len(required_records),
        "shortcut_evidence": {
            "question_only": "unmeasured",
            "latest_revision_only": "unmeasured",
            "cropped_source_4k": "unmeasured",
            "cropped_source_8k": "unmeasured",
            "cropped_source_16k": "unmeasured",
            "remove_one_record": "unmeasured",
            "neural": "unmeasured",
        },
        "admission_reason": reason,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(canonical(row) + "\n" for row in rows))


def build(config_path: Path, output: Path, workers: int) -> dict:
    config = checked_config(config_path)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "contexts").mkdir()
    _init_tokenizer(config["tokenizer"])
    raw_specs: list[dict] = []
    source_rejects: list[dict] = []
    for family in config["families"]:
        specs, rejects = (
            _elife_specs(family)
            if family["kind"] == "elife_inventory"
            else _tar_specs(family)
        )
        raw_specs.extend(specs)
        source_rejects.extend(rejects)
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_tokenizer,
        initargs=(config["tokenizer"],),
    ) as pool:
        evaluated = list(pool.map(_evaluate, raw_specs))
    evaluated.sort(
        key=lambda item: (
            item["family_id"],
            item["old_version"],
            item["new_version"],
            item["paths"],
        )
    )
    candidates: list[dict] = []
    rejected = list(source_rejects)
    train: list[dict] = []
    eval_rows: list[dict] = []
    for item in evaluated:
        task_id = sha256_text(
            canonical(
                [
                    REVISION,
                    item["family_id"],
                    item["old_version"],
                    item["new_version"],
                    item["paths"],
                ]
            )
        )
        if item["admission_reason"] != "complete_record_contract_local_candidate":
            rejected.append({**item, "task_id": task_id})
            continue
        context_sha = sha256_text(item["context"])
        context_path = output / "contexts" / f"{context_sha}.txt"
        if not context_path.exists():
            context_path.write_text(item["context"])
        candidate = {
            key: value
            for key, value in item.items()
            if key not in {"context", "messages", "record_spans"}
        }
        candidate.update(
            {
                "schema_version": "longworld.p66-researchlab-taskbank-sample.v1",
                "task_id": task_id,
                "semantic_task_id": task_id,
                "source_group_id": "researchlab:" + item["family_id"],
                "world_instance_id": (
                    f"{item['family_id']}:{item['old_version']}:{item['new_version']}"
                ),
                "context_path": f"contexts/{context_sha}.txt",
                "context_sha256": context_sha,
                "admission_state": "complete_record_contract_local_candidate",
                "local_candidate": True,
                "local_training_candidate": False,
                "strict_long_dependency_verified": False,
                "training_release_eligible": False,
                "production_eligible": False,
            }
        )
        candidates.append(candidate)
        sft = {"sample_id": task_id, "messages": item["messages"]}
        (train if item["split"] == "train" else eval_rows).append(sft)
    _write_jsonl(output / "candidates.jsonl", candidates)
    _write_jsonl(output / "rejects.jsonl", rejected)
    _write_jsonl(output / "train.jsonl", train)
    _write_jsonl(output / "eval.jsonl", eval_rows)
    files = {
        path.relative_to(output).as_posix(): digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    receipt = {
        "schema_version": RECEIPT,
        "revision": REVISION,
        "source_domain": "researchlab",
        "source_families": len(config["families"]),
        "source_worlds": len({row["world_instance_id"] for row in candidates}),
        "semantic_tasks": len(candidates),
        "unique_contexts": len({row["context_sha256"] for row in candidates}),
        "train_rows": len(train),
        "eval_rows": len(eval_rows),
        "capacity_bins": dict(Counter(str(row["capacity_bin"]) for row in candidates)),
        "exact_numeric_ranges": dict(
            Counter(row["exact_numeric_range"] or "none" for row in candidates)
        ),
        "rejection_reasons": dict(Counter(row["reason"] for row in rejected)),
        "admission_states": dict(Counter(row["admission_state"] for row in candidates)),
        "controls": {
            "complete_record_contract": "declared and source-record inventory checked",
            "question_only": "unmeasured",
            "latest_revision_only": "unmeasured",
            "cropped_source_4k_8k_16k": "unmeasured",
            "remove_one": "unmeasured",
            "neural": "unmeasured",
        },
        "family_splits": {
            family["family_id"]: family["split"] for family in config["families"]
        },
        "source_attestation": {
            "historical_signed_bundle_identity_bound": True,
            "fresh_hmac_verification_in_current_environment": False,
            "local_derivative_authorization": True,
        },
        "strict_long_dependency_verified": False,
        "training_release_eligible": False,
        "production_eligible": False,
        "config_sha256": digest(config_path),
        "code_sha256": {name: digest(ROOT / name) for name in CODE},
        "input_bindings": [
            *[
                {"path": source["path"], "sha256": source["sha256"]}
                for family in config["families"]
                for source in family["sources"]
            ],
            *[
                {
                    "path": family["source_workflow_bundle"]["path"],
                    "sha256": family["source_workflow_bundle"]["sha256"],
                    "source_role_key_id": family["source_workflow_bundle"]["key_id"],
                }
                for family in config["families"]
                if family.get("source_workflow_bundle")
            ],
        ],
        "tokenizer_asset_manifest_sha256": resolved_tokenizer_asset_manifest_sha256(
            config["tokenizer"]["model_id"], config["tokenizer"]["revision"]
        ),
        "files": files,
    }
    (output / "BUILD_RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    return receipt


def validate(config_path: Path, output: Path, workers: int) -> dict:
    receipt_path = output / "BUILD_RECEIPT.json"
    if receipt_path.is_symlink():
        raise ValueError("receipt cannot be a symlink")
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("schema_version") != RECEIPT:
        raise ValueError("invalid P66 ResearchLab receipt schema")
    if any(path.is_symlink() for path in output.rglob("*")):
        raise ValueError("output contains symlinks")
    with tempfile.TemporaryDirectory(prefix="p66-researchlab-replay-") as temp:
        rebuilt = build(config_path, Path(temp) / "out", workers)
    for key in (
        "files",
        "config_sha256",
        "code_sha256",
        "input_bindings",
        "semantic_tasks",
        "capacity_bins",
        "rejection_reasons",
    ):
        if rebuilt[key] != receipt[key]:
            raise ValueError(f"P66 ResearchLab replay mismatch: {key}")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    receipt = (
        validate(args.config, args.output, args.workers)
        if args.validate
        else build(args.config, args.output, args.workers)
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
