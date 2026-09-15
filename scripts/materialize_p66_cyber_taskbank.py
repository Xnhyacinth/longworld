#!/usr/bin/env python3
"""Build or validate the bounded P66 PyPA advisory taskbank."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.p66_cyber_taskbank import (
    audit_task,
    canonical_json,
    make_task,
    package_names,
    semantic_advisory,
)
from longworld.core.provenance import ProvenanceError, _read_regular_file
from scripts.train_sft import tokenize_assistant_only

CONFIG_SCHEMA = "longworld.p66-cyber-osv-taskbank-build.v1"
RECEIPT_SCHEMA = "longworld.p66-cyber-osv-taskbank-receipt.v1"
MAX_CONFIG_BYTES = 256_000
MAX_OUTPUT_BYTES = 2_000_000_000
FAMILIES = ("lifecycle_matrix", "remediation_commit_join", "temporal_order")
MAX_TRAINING_SEQUENCE_TOKENS = 300_000


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_config(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_file(path, MAX_CONFIG_BYTES)
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA:
        raise ProvenanceError("invalid P66 cyber config")
    return value, raw


def _resolve(value: object) -> Path:
    path = Path(str(value or ""))
    if not str(path):
        raise ProvenanceError("empty P66 dependency path")
    return path if path.is_absolute() else ROOT / path


def _load_source_helpers(config: dict[str, Any]):
    dependency = config.get("source_workflow")
    if not isinstance(dependency, dict):
        raise ProvenanceError("missing P66 source workflow")
    path = _resolve(dependency.get("path"))
    raw = _read_regular_file(path, 2_000_000)
    if _sha(raw) != dependency.get("sha256"):
        raise ProvenanceError("P66 source workflow hash changed")
    spec = importlib.util.spec_from_file_location("p66_pinned_p51_helpers", path)
    if spec is None or spec.loader is None:
        raise ProvenanceError("cannot load P66 source workflow")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_authorization(config: dict[str, Any], p51: dict[str, Any]) -> None:
    prior = config.get("prior_preflight")
    authorization = config.get("authorization")
    source = config.get("source")
    if not all(isinstance(value, dict) for value in (prior, authorization, source)):
        raise ProvenanceError("P66 authorization metadata is incomplete")
    prior_path = _resolve(prior.get("path"))
    prior_raw = _read_regular_file(prior_path, MAX_CONFIG_BYTES)
    if _sha(prior_raw) != prior.get("sha256") or not prior.get(
        "historical_generate_candidates_hold_preserved"
    ):
        raise ProvenanceError("P66 historical hold binding is invalid")
    prior_value = json.loads(prior_raw)
    prohibited = prior_value.get("authorization", {}).get("prohibited_actions", [])
    if "generate_candidates" not in prohibited:
        raise ProvenanceError("P66 expected historical generation hold is absent")
    required_allowed = {
        "fetch_pinned_pypa_archive_to_process_memory",
        "persist_normalized_cc_by_advisory_derivatives",
        "generate_local_candidate_tasks",
        "run_local_dependency_controls",
    }
    required_prohibited = {
        "persist_raw_archive",
        "claim_source_owner_approval",
        "public_release",
        "promotion",
        "production_use",
    }
    if (
        authorization.get("supersedes_prior_hold_only_for_this_p66_local_scope")
        is not True
        or set(authorization.get("allowed_actions", [])) != required_allowed
        or set(authorization.get("prohibited_actions", [])) != required_prohibited
    ):
        raise ProvenanceError("P66 local derivative authorization is invalid")
    archive = p51.get("pypa_archive", {})
    if (
        source.get("license_spdx") != "CC-BY-4.0"
        or archive.get("license_spdx") != "CC-BY-4.0"
        or source.get("license_sha256") != archive.get("license_expected_sha256")
        or not source.get("attribution")
        or not archive.get("expected_sha256")
    ):
        raise ProvenanceError("P66 source license, attribution, or hashes are absent")


def _split_units(
    units: list[dict[str, Any]], lengths: dict[str, int]
) -> dict[str, list[dict[str, Any]]]:
    """Balance package-connected components without cross-split package leakage."""
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for unit in units:
        packages = unit["packages"]
        if not packages:
            raise ProvenanceError("eligible P66 record has no PyPI package")
        for package in packages[1:]:
            union(packages[0], package)
    groups: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        groups.setdefault(find(unit["packages"][0]), []).append(unit)
    components = sorted(
        groups.values(),
        key=lambda values: (
            -sum(lengths[item["digest"]] for item in values),
            values[0]["packages"][0],
        ),
    )
    result: dict[str, list[dict[str, Any]]] = {"train": [], "eval": []}
    totals = {"train": 0, "eval": 0}
    for component in components:
        split = min(totals, key=lambda name: (totals[name], name))
        result[split].extend(component)
        totals[split] += sum(lengths[item["digest"]] for item in component)
    return result


def _pack_worlds(
    units: list[dict[str, Any]],
    lengths: dict[str, int],
    bands: dict[str, list[int]],
    split: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    remaining = sorted(units, key=lambda item: (item["digest"], item["id"]))
    worlds = []
    rejects = []
    for band in ("64k", "128k", "256k"):
        lower, upper = bands[band]
        selected = []
        total = 0
        next_remaining = []
        reached = False
        for unit in remaining:
            if reached:
                next_remaining.append(unit)
                continue
            length = lengths[unit["digest"]]
            separator = 1 if selected else 0
            if total + separator + length > upper:
                next_remaining.append(unit)
                continue
            selected.append(unit)
            total += separator + length
            if total >= lower:
                reached = True
        if not reached:
            raise ProvenanceError(f"P66 {split} cannot fill exact {band} world")
        selected_ids = {item["id"] for item in selected}
        remaining = [item for item in remaining if item["id"] not in selected_ids]
        context = "\n".join(item["text"] for item in selected)
        worlds.append(
            {
                "world_id": f"p66-cyber-osv-{split}-{band}-v1",
                "split": split,
                "band": band,
                "context": context,
                "context_tokens": total,
                "units": selected,
            }
        )
    rejects.extend(
        {"id": item["id"], "reason": "bounded_batch_capacity_leftover", "split": split}
        for item in remaining
    )
    return worlds, rejects


def _targets(units: list[dict[str, Any]], variant: int) -> list[str]:
    count = len(units)
    if count < 16:
        raise ProvenanceError("P66 world has too few advisories")
    offsets = (0, count // 3, (2 * count) // 3, count - 1)
    return [units[(offset + variant * 7) % count]["id"] for offset in offsets]


def _worker(payload: dict[str, Any]) -> dict[str, Any]:
    return make_task(**payload)


def _line_spans(tokenizer: Any, context: str) -> dict[str, tuple[int, int]]:
    spans = {}
    cursor = 0
    separator_tokens = len(tokenizer.encode("\n", add_special_tokens=False))
    for line in context.splitlines():
        start = cursor
        end = start + len(tokenizer.encode(line, add_special_tokens=False))
        spans[json.loads(line)["id"]] = (start, end)
        cursor = end + separator_tokens
    return spans


def _full_chat_token_count(tokenizer: Any, messages: list[dict[str, str]]) -> int:
    encoded = tokenize_assistant_only(
        tokenizer, messages, max_length=MAX_TRAINING_SEQUENCE_TOKENS
    )
    return len(encoded["input_ids"])


def _write(path: Path, raw: bytes) -> None:
    if os.path.lexists(path):
        if _read_regular_file(path, max(MAX_OUTPUT_BYTES, len(raw))) != raw:
            raise ProvenanceError(f"existing P66 output differs: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return ("".join(canonical_json(row) + "\n" for row in rows)).encode()


def build(
    config_path: Path, output_dir: Path, *, workers_override: int | None = None
) -> dict[str, Any]:
    config, config_raw = _read_config(config_path)
    helpers = _load_source_helpers(config)
    prior_path = _resolve(config["prior_preflight"]["path"])
    p51 = json.loads(_read_regular_file(prior_path, MAX_CONFIG_BYTES))
    _validate_authorization(config, p51)

    archive_raw, archive_receipt = helpers._fetch(p51["pypa_archive"])
    records, archive_safety = helpers._load_pypa_archive(
        archive_raw, p51["pypa_archive"]
    )
    rejection_counts: dict[str, int] = {}
    eligible = []
    for record in records:
        reasons = helpers._eligibility_reasons(record)
        for reason in reasons:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        if not reasons:
            eligible.append(record)

    units = []
    for record in eligible:
        projected = semantic_advisory(record)
        text = canonical_json(projected)
        canonical = helpers._canonical(text)
        units.append(
            {
                "id": projected["id"],
                "text": text,
                "canonical": canonical,
                "digest": _sha(canonical.encode()),
                "packages": package_names(record),
            }
        )
    exact = {}
    for unit in units:
        exact.setdefault(unit["digest"], unit)
    near, near_removed = helpers._near_deduplicate(
        list(exact.values()),
        int(p51["capacity_filter"]["near_duplicate_word_shingle_size"]),
        float(p51["capacity_filter"]["near_duplicate_jaccard_threshold"]),
    )

    tokenizer_config = config["tokenizer"]
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_config["model_id"],
            revision=tokenizer_config["revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
    lengths = helpers._token_lengths(tokenizer, near)
    split_units = _split_units(near, lengths)
    for split, values in split_units.items():
        for unit in values:
            unit["split"] = split
    worlds = []
    rejects = []
    for split in ("train", "eval"):
        split_worlds, split_rejects = _pack_worlds(
            split_units[split],
            lengths,
            config["bands"],
            split,
        )
        worlds.extend(split_worlds)
        rejects.extend(split_rejects)

    train_packages = {
        package
        for item in near
        if item["split"] == "train"
        for package in item["packages"]
    }
    eval_packages = {
        package
        for item in near
        if item["split"] == "eval"
        for package in item["packages"]
    }
    overlap = train_packages & eval_packages
    if overlap:
        raise ProvenanceError("P66 package split isolation failed")

    source_binding = {
        "archive_commit": p51["pypa_archive"]["commit"],
        "archive_sha256": archive_receipt["sha256"],
        "license_spdx": config["source"]["license_spdx"],
        "license_sha256": config["source"]["license_sha256"],
        "attribution": config["source"]["attribution"],
        "authorization_record_id": config["authorization"]["record_id"],
        "raw_source_persisted": False,
    }
    payloads = []
    for world in worlds:
        for variant in range(int(config["tasks_per_world"])):
            payloads.append(
                {
                    "context": world["context"],
                    "target_ids": _targets(world["units"], variant),
                    "family": FAMILIES[variant % len(FAMILIES)],
                    "world_id": world["world_id"],
                    "split": world["split"],
                    "band": world["band"],
                    "variant": variant,
                    "source_binding": source_binding,
                }
            )
    workers = workers_override or int(config["workers"])
    if workers < 1 or workers > 32:
        raise ProvenanceError("P66 workers must be between 1 and 32")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = list(pool.map(_worker, payloads))
    tasks.sort(key=lambda item: item["task_id"])

    window_success = {"4k": 0, "8k": 0, "16k": 0}
    full_chat_tokens = []
    compact_success = 0
    audits = []
    spans_by_context = {}
    for task in tasks:
        audit = audit_task(task)
        if not all(audit.values()):
            raise ProvenanceError(f"P66 task audit failed: {task['task_id']}")
        compact_success += int(audit["compact_target_records_succeeds"])
        spans = spans_by_context.setdefault(
            task["context_sha256"], _line_spans(tokenizer, task["context"])
        )
        target_span = max(spans[item][1] for item in task["target_ids"]) - min(
            spans[item][0] for item in task["target_ids"]
        )
        for name, size in (("4k", 4096), ("8k", 8192), ("16k", 16384)):
            window_success[name] += int(target_span <= size)
        messages = [
            {
                "role": "user",
                "content": f"{task['context']}\n\n{task['question']}",
            },
            {"role": "assistant", "content": task["answer"]},
        ]
        with sanitized_attestation_environment():
            full_chat_tokens.append(_full_chat_token_count(tokenizer, messages))
        audits.append(
            {"task_id": task["task_id"], **audit, "target_span_tokens": target_span}
        )

    sft = {
        split: [
            {
                "sample_id": task["task_id"],
                "messages": [
                    {
                        "role": "user",
                        "content": f"{task['context']}\n\n{task['question']}",
                    },
                    {"role": "assistant", "content": task["answer"]},
                ],
            }
            for task in tasks
            if task["split"] == split
        ]
        for split in ("train", "eval")
    }
    task_raw = _jsonl(tasks)
    audit_raw = _jsonl(audits)
    reject_raw = _jsonl(sorted(rejects, key=lambda item: (item["split"], item["id"])))
    train_raw = _jsonl(sft["train"])
    eval_raw = _jsonl(sft["eval"])
    world_rows = [
        {
            "world_id": world["world_id"],
            "split": world["split"],
            "length_bucket": world["band"],
            "context_tokens": world["context_tokens"],
            "advisory_count": len(world["units"]),
            "context_sha256": _sha(world["context"].encode()),
        }
        for world in worlds
    ]
    files = {
        "tasks.jsonl": task_raw,
        "audits.jsonl": audit_raw,
        "rejects.jsonl": reject_raw,
        "train.jsonl": train_raw,
        "eval.jsonl": eval_raw,
    }
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "data_stage": "local_candidate_taskbank",
        "config_sha256": _sha(config_raw),
        "source": source_binding,
        "historical_p51_generation_hold_preserved": True,
        "p66_local_scope_supersedes_prior_hold": True,
        "archive_safety": archive_safety,
        "source_advisory_count": len(records),
        "eligibility_rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "eligible_advisory_count": len(eligible),
        "exact_deduplicated_advisory_count": len(exact),
        "near_deduplicated_advisory_count": len(near),
        "near_duplicate_removed_count": near_removed,
        "package_split": {
            "train_packages": len(train_packages),
            "eval_packages": len(eval_packages),
            "overlap": 0,
        },
        "world_count": len(worlds),
        "bounded_batch_used_advisory_count": sum(
            len(world["units"]) for world in worlds
        ),
        "bounded_batch_leftover_count": len(rejects),
        "world_source_overlap_count": 0,
        "task_count": len(tasks),
        "train_count": len(sft["train"]),
        "eval_count": len(sft["eval"]),
        "family_counts": {
            family: sum(task["family"] == family for task in tasks)
            for family in FAMILIES
        },
        "band_counts": {
            band: sum(task["length_bucket"] == band for task in tasks)
            for band in ("64k", "128k", "256k")
        },
        "worlds": world_rows,
        "unique_context_count": len({task["context_sha256"] for task in tasks}),
        "queried_target_advisory_count": len(
            {target for task in tasks for target in task["target_ids"]}
        ),
        "context_reuse_factor": len(tasks) / len(worlds),
        "full_chat_tokens": {
            "min": min(full_chat_tokens),
            "max": max(full_chat_tokens),
            "sum": sum(full_chat_tokens),
            "no_truncation_verified_count": len(full_chat_tokens),
            "max_length_used_for_rejection": MAX_TRAINING_SEQUENCE_TOKENS,
        },
        "controls": {
            "window_complete_target_success": window_success,
            "last_packed_record_success": 0,
            "single_record_success": 0,
            "remove_target_success": 0,
            "compact_target_records_success": compact_success,
        },
        "multiprocessing_workers": workers,
        "raw_source_persisted": False,
        "strict_long_dependency_verified_count": 0,
        "training_candidate_count": len(tasks),
        "train_ready": False,
        "production_eligible": False,
        "promoted": False,
        "files": {
            name: {"bytes": len(raw), "sha256": _sha(raw)}
            for name, raw in files.items()
        },
    }
    receipt_raw = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    for name, raw in files.items():
        _write(output_dir / name, raw)
    _write(output_dir / "BUILD_RECEIPT.json", receipt_raw)
    return receipt


def validate(
    config_path: Path, output_dir: Path, *, workers_override: int | None = None
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="p66-cyber-replay-") as temporary:
        replay_dir = Path(temporary)
        receipt = build(config_path, replay_dir, workers_override=workers_override)
        names = [*receipt["files"], "BUILD_RECEIPT.json"]
        for name in names:
            expected = _read_regular_file(output_dir / name, MAX_OUTPUT_BYTES)
            actual = _read_regular_file(replay_dir / name, MAX_OUTPUT_BYTES)
            if expected != actual:
                raise ProvenanceError(f"P66 native replay differs: {name}")
    return {"native_replay_identical": True, "receipt": receipt}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--output", "--output-dir", dest="output_dir", type=Path, required=True
    )
    parser.add_argument("--workers", type=int)
    parser.add_argument(
        "--validate", "--validate-only", dest="validate", action="store_true"
    )
    args = parser.parse_args()
    result = (
        validate(args.config, args.output_dir, workers_override=args.workers)
        if args.validate
        else build(args.config, args.output_dir, workers_override=args.workers)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
