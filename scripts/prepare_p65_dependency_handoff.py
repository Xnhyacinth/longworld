#!/usr/bin/env python3
"""Build and validate a signed local P65 evidence-filtered SFT handoff."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.attestation import (
    attach_attestation,
    sanitized_attestation_environment,
    verify_attestation,
)
from longworld.core.taskbank_training import (
    TOKENIZER_REVISION,
    _binding,
    _canonical,
    _count_messages,
    _digest,
    _json,
    _load_tokenizer,
    _member,
    _report_key,
)

CONFIG_SCHEMA = "longworld.p65-dependency-handoff-config.v1"
RECEIPT_SCHEMA = "longworld.p65-dependency-handoff-receipt.v1"
PROFILE = "p65-evidence-filtered-local-sft-v1"
MANIFEST = "BUILD_RECEIPT.json"
DOMAINS = ("codeforge", "finance_disclosure")
SPLITS = ("train", "eval")
LIMIT = 262144
EXACT_RANGES = (
    ("64k", 64000, 65536),
    ("128k", 128000, 131072),
    ("256k", 256000, 262144),
)
OUTPUTS = tuple(f"{domain}_{split}.jsonl" for domain in DOMAINS for split in SPLITS) + (
    "sample_index.jsonl",
    "dataset_info.json",
)


def data_path(path: Path) -> Path:
    path = path.absolute()
    mount = ROOT / "data"
    if path.is_relative_to(mount):
        path = mount.resolve(strict=True) / path.relative_to(mount)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("handoff path contains a descendant symlink")
    return path


def project_path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe project path")
    if relative.parts and relative.parts[0] == "data":
        return _member(
            (ROOT / "data").resolve(strict=True), Path(*relative.parts[1:]).as_posix()
        )
    return _member(ROOT, relative.as_posix())


def project_root(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("unsafe project directory")
    base = (ROOT / "data").resolve(strict=True) if relative.parts[0] == "data" else ROOT
    parts = relative.parts[1:] if relative.parts[0] == "data" else relative.parts
    path = base
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink source directory")
    if not path.is_dir():
        raise ValueError("missing source directory")
    return path


def checked_config(path: Path) -> dict:
    config = _json(path)
    if (
        config.get("schema_version") != CONFIG_SCHEMA
        or config.get("tokenizer")
        != {"model_id": "Qwen/Qwen3.5-4B", "revision": TOKENIZER_REVISION}
        or set(config.get("expected_counts", {})) != set(DOMAINS)
        or any(type(config["expected_counts"][domain]) is not int for domain in DOMAINS)
    ):
        raise ValueError("unsupported P65 handoff config")
    sources = config.get("sources", {})
    if (
        set(sources) != {"codeforge", "finance", "quarantined_govinfo"}
        or len(sources["finance"]) != 2
    ):
        raise ValueError("P65 source inventory mismatch")
    return config


def checked_tree(
    root: Path, expected_receipt_hash: str, schema: str, *, validate_code: bool = True
) -> tuple[dict, list[dict]]:
    receipt_path = _member(root, MANIFEST)
    if _digest(receipt_path) != expected_receipt_hash:
        raise ValueError("source receipt hash changed")
    receipt = _json(receipt_path)
    if receipt.get("schema_version") != schema:
        raise ValueError("source receipt schema mismatch")
    files = receipt.get("files")
    if not isinstance(files, dict):
        raise TypeError("source receipt lacks file inventory")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if any(p.is_symlink() for p in root.rglob("*")) or actual != set(files) | {
        MANIFEST
    }:
        raise ValueError("source output inventory mismatch")
    bindings = [_binding(receipt_path)]
    for name, expected in sorted(files.items()):
        path = _member(root, name)
        if _digest(path) != expected:
            raise ValueError("source output hash changed")
        bindings.append(_binding(path))
    if validate_code:
        for name, expected in sorted(receipt.get("code_sha256", {}).items()):
            path = project_path(name)
            if _digest(path) != expected:
                raise ValueError("source code binding changed")
            bindings.append(_binding(path))
    for entry in receipt.get("input_bindings", []) + receipt.get("source_bindings", []):
        path = Path(entry["path"])
        if _binding(path) != entry:
            raise ValueError("source input binding changed")
        bindings.append(entry)
    return receipt, bindings


def checked_sample(sample: dict, sample_id: str) -> dict:
    if (
        set(sample) not in ({"messages"}, {"sample_id", "messages"})
        or sample.get("sample_id", sample_id) != sample_id
    ):
        raise ValueError("unexpected SFT envelope or sample identity")
    messages = sample.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 2
        or [message.get("role") for message in messages] != ["user", "assistant"]
        or any(
            set(message) != {"role", "content"}
            or not isinstance(message["content"], str)
            or not message["content"]
            for message in messages
        )
    ):
        raise ValueError("invalid assistant-only SFT messages")
    return {"sample_id": sample_id, "messages": messages}


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in _member(path.parent, path.name).read_text().splitlines()
    ]


def collect(config_path: Path) -> tuple[list[tuple[dict, dict]], list[dict]]:
    config = checked_config(config_path)
    bindings = [_binding(config_path)]
    rows: list[tuple[dict, dict]] = []
    sources = config["sources"]

    code_root = project_root(sources["codeforge"]["path"])
    receipt, source_bindings = checked_tree(
        code_root,
        sources["codeforge"]["receipt_sha256"],
        "longworld.codeforge-reading-proof-build.v2",
    )
    bindings += source_bindings
    metadata = read_jsonl(code_root / "metadata.jsonl")
    if (
        receipt.get("qualified_existing_semantic_tasks") != 130
        or receipt.get("new_semantic_tasks") != 0
        or receipt.get("strict_long_dependency_verified") is not False
        or len(metadata) != 130
    ):
        raise ValueError("CodeForge qualification receipt mismatch")
    samples = {split: read_jsonl(code_root / f"{split}.jsonl") for split in SPLITS}
    for meta in metadata:
        split, index = meta.get("split"), meta.get("row_index")
        if (
            split not in SPLITS
            or meta.get("output_file") != f"{split}.jsonl"
            or type(index) is not int
            or not 0 <= index < len(samples[split])
            or meta.get("content_backed_scoped_certificate") is not True
            or meta.get("scoped_long_input_certificate") is not True
            or meta.get("canonical_strict_production_eligible") is not False
        ):
            raise ValueError("CodeForge row is outside the content-backed certificate")
        sample = checked_sample(samples[split][index], meta["sample_id"])
        rows.append(
            (
                sample,
                {
                    "domain": "codeforge",
                    "split": split,
                    "group_id": meta["source_group_id"],
                    "world_instance_id": meta["world_instance_id"],
                    "context_id": meta["context_id"],
                    "semantic_task_id": meta["semantic_task_id"],
                    "sample_id": meta["sample_id"],
                    "stored_full_message_tokens": meta["full_message_tokens"],
                    "admission_state": "content_backed_scoped_certificate",
                },
            )
        )

    finance_sources = config["sources"]["finance"]
    if {entry.get("name") for entry in finance_sources} != {"intel", "siriusxm"}:
        raise ValueError("finance issuer inventory mismatch")
    for entry in finance_sources:
        root = project_root(entry["path"])
        receipt, source_bindings = checked_tree(
            root, entry["receipt_sha256"], "longworld.finance-disclosure-receipt.v1"
        )
        bindings += source_bindings
        tasks = read_jsonl(root / "tasks.jsonl")
        sft = read_jsonl(root / "long_sft_candidates.jsonl")
        if (
            not (len(tasks) == len(sft) == receipt.get("long_local_candidates"))
            or receipt.get("strict_long_dependency_verified") is not False
            or receipt.get("training_release_eligible") is not False
        ):
            raise ValueError("finance long-candidate receipt mismatch")
        for task, sample in zip(tasks, sft, strict=True):
            if (
                task.get("schema_version")
                != "longworld.finance-disclosure-version-task.v1"
                or task.get("split") not in SPLITS
                or task.get("admission_state")
                != "long_local_candidate_unverified_dependency"
                or task.get("strict_long_dependency_verified") is not False
                or task.get("training_release_eligible") is not False
            ):
                raise ValueError("finance row admission mismatch")
            sample = checked_sample(sample, task["sample_id"])
            rows.append(
                (
                    sample,
                    {
                        "domain": "finance_disclosure",
                        "split": task["split"],
                        "group_id": task["split_group_id"],
                        "world_instance_id": task["source_collection_id"],
                        "context_id": task["context_sha256"],
                        "semantic_task_id": task["semantic_task_id"],
                        "sample_id": task["sample_id"],
                        "stored_full_message_tokens": task["full_message_tokens"],
                        "admission_state": task["admission_state"],
                    },
                )
            )

    gov_root = project_root(sources["quarantined_govinfo"]["path"])
    receipt, source_bindings = checked_tree(
        gov_root,
        sources["quarantined_govinfo"]["receipt_sha256"],
        "longworld.p65-govinfo-taskbank-receipt.v1",
        validate_code=False,
    )
    bindings += source_bindings
    if (
        receipt.get("long_sft_rows") != 7
        or receipt.get("short_sft_rows") != 21
        or receipt.get("strict_verified") != 0
        or receipt.get("training_release_eligible") is not False
    ):
        raise ValueError("GovInfo quarantine receipt mismatch")

    actual = Counter(meta["domain"] for _, meta in rows)
    if dict(actual) != config["expected_counts"]:
        raise ValueError("P65 selected row count mismatch")
    unique = {binding["path"]: binding for binding in bindings}
    return rows, sorted(unique.values(), key=lambda binding: binding["path"])


def capacity(tokens: int) -> str:
    return next(
        label
        for label, cap in (("64k", 65536), ("128k", 131072), ("256k", 262144))
        if tokens <= cap
    )


def exact_range(tokens: int) -> str:
    return next(
        (label for label, low, high in EXACT_RANGES if low <= tokens <= high),
        "other_long",
    )


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)

    def percentile(q: float) -> int:
        return ordered[round((len(ordered) - 1) * q)]

    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": percentile(0.1),
        "median": percentile(0.5),
        "p90": percentile(0.9),
        "max": ordered[-1],
        "sum": sum(ordered),
    }


def dataset_info() -> dict:
    return {
        f"p65_{domain}_{split}": {
            "file_name": f"{domain}_{split}.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
            },
        }
        for domain in DOMAINS
        for split in SPLITS
    }


def tokenizer_digest() -> str:
    from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

    with sanitized_attestation_environment():
        return resolved_tokenizer_asset_manifest_sha256(
            "Qwen/Qwen3.5-4B", TOKENIZER_REVISION
        )


def code_bindings(config_path: Path) -> list[dict]:
    return sorted(
        (
            _binding(path)
            for path in (
                Path(__file__).absolute(),
                config_path.absolute(),
                ROOT / "longworld/core/taskbank_training.py",
                ROOT / "longworld/core/tokenizer_assets.py",
                ROOT / "scripts/train_sft.py",
            )
        ),
        key=lambda binding: binding["path"],
    )


def prepare(config_path: Path, output: Path, workers: int = 4) -> dict:
    config_path = config_path.absolute()
    output = data_path(output)
    rows, inputs = collect(config_path)
    key, assets, stage = _report_key(), tokenizer_digest(), code_bindings(config_path)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    with sanitized_attestation_environment():
        tokenizer = _load_tokenizer("Qwen/Qwen3.5-4B", TOKENIZER_REVISION)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            measured = list(
                pool.map(
                    lambda item: _count_messages(item[0]["messages"], tokenizer, LIMIT),
                    rows,
                )
            )
    counts = {domain: {split: 0 for split in SPLITS} for domain in DOMAINS}
    token_values: dict[str, list[int]] = defaultdict(list)
    capacity_bins: Counter[str] = Counter()
    exact_ranges: Counter[str] = Counter()
    task_ids, sample_ids, groups = set(), set(), {}
    handles = {
        (domain, split): (output / f"{domain}_{split}.jsonl").open("x")
        for domain in DOMAINS
        for split in SPLITS
    }
    index = (output / "sample_index.jsonl").open("x")
    try:
        for (sample, meta), tokens in zip(rows, measured, strict=True):
            stored_tokens = meta["stored_full_message_tokens"]
            if (
                type(tokens) is not int
                or tokens != stored_tokens
                or not 32768 < tokens <= LIMIT
            ):
                raise ValueError("stored and recomputed full-chat token counts differ")
            task_key = (meta["domain"], meta["semantic_task_id"])
            group_key = (meta["domain"], meta["group_id"])
            if task_key in task_ids or meta["sample_id"] in sample_ids:
                raise ValueError("duplicate canonical task or sample")
            if group_key in groups and groups[group_key] != meta["split"]:
                raise ValueError("source group crosses train/eval")
            task_ids.add(task_key)
            sample_ids.add(meta["sample_id"])
            groups[group_key] = meta["split"]
            domain, split = meta["domain"], meta["split"]
            row_index = counts[domain][split]
            handles[domain, split].write(_canonical(sample) + "\n")
            index_meta = {
                key: value
                for key, value in meta.items()
                if key != "stored_full_message_tokens"
            }
            index.write(
                _canonical(
                    {
                        **index_meta,
                        "full_message_tokens": tokens,
                        "capacity_bin": capacity(tokens),
                        "exact_token_range": exact_range(tokens),
                        "output_file": f"{domain}_{split}.jsonl",
                        "row_index": row_index,
                    }
                )
                + "\n"
            )
            counts[domain][split] += 1
            token_values["overall"].append(tokens)
            token_values[domain].append(tokens)
            token_values[f"{domain}:{split}"].append(tokens)
            capacity_bins[capacity(tokens)] += 1
            exact_ranges[exact_range(tokens)] += 1
    finally:
        index.close()
        for handle in handles.values():
            handle.close()
    (output / "dataset_info.json").write_text(_canonical(dataset_info()) + "\n")
    if (
        collect(config_path)[1] != inputs
        or tokenizer_digest() != assets
        or code_bindings(config_path) != stage
    ):
        raise ValueError("input, tokenizer, or handoff code changed during preparation")
    manifest = {
        "schema_version": RECEIPT_SCHEMA,
        "profile_id": PROFILE,
        "manifest_path": MANIFEST,
        "trust_scope": "local_probe",
        "local_sft_handoff_eligible": True,
        "training_release_eligible": False,
        "production_eligible": False,
        "strict_long_dependency_verified": False,
        "framework_preprocessing_verified": False,
        "qualification_boundary": "CodeForge rows have a finite scoped content-backed certificate; Finance rows remain natural-long local candidates with unverified strict dependency.",
        "config_binding": _binding(config_path),
        "input_bindings": inputs,
        "training_code_bindings": stage,
        "tokenizer": {
            "model_id": "Qwen/Qwen3.5-4B",
            "revision": TOKENIZER_REVISION,
            "asset_manifest_sha256": assets,
            "cutoff": LIMIT,
            "truncation": False,
        },
        "counts": counts,
        "combined_counts": {
            split: sum(counts[domain][split] for domain in DOMAINS) for split in SPLITS
        },
        "canonical_tasks": len(task_ids),
        "samples": len(sample_ids),
        "source_worlds": {
            "codeforge": 7,
            "finance_disclosure": 2,
            "total": 9,
        },
        "quarantined_diagnostics": {
            "govinfo": {
                "long_rows": 7,
                "short_rows": 21,
                "included_in_sft": 0,
                "reason": "durable source authorization does not permit generate_candidates or promote_or_count_inventory",
            }
        },
        "unique_contexts": len({meta["context_id"] for _, meta in rows}),
        "full_chat_token_distribution": {
            name: distribution(values)
            for name, values in sorted(token_values.items())
            if values
        },
        "full_chat_capacity_bins": dict(sorted(capacity_bins.items())),
        "full_chat_exact_token_ranges": dict(sorted(exact_ranges.items())),
        "token_count_basis": "pinned Qwen3.5 chat template, thinking disabled, complete assistant target, truncation=false",
        "outputs": [
            {
                "path": name,
                "sha256": _digest(output / name),
                "bytes": (output / name).stat().st_size,
            }
            for name in OUTPUTS
        ],
    }
    signed = attach_attestation(manifest, key, purpose="training_export_manifest")
    if (
        signed.get("attestation", {}).get("role") != "report"
        or signed["attestation"].get("environment") != "probe"
    ):
        raise ValueError("probe report identity required")
    (output / MANIFEST).write_text(_canonical(signed) + "\n")
    return signed


def validate(manifest_path: Path) -> dict:
    manifest_path = data_path(manifest_path)
    manifest = _json(manifest_path)
    key = _report_key()
    if (
        manifest.get("schema_version") != RECEIPT_SCHEMA
        or manifest.get("profile_id") != PROFILE
        or manifest.get("strict_long_dependency_verified") is not False
        or manifest.get("training_release_eligible") is not False
        or manifest.get("production_eligible") is not False
        or manifest.get("local_sft_handoff_eligible") is not True
        or manifest.get("attestation", {}).get("role") != "report"
        or manifest["attestation"].get("environment") != "probe"
        or not verify_attestation(manifest, key, purpose="training_export_manifest")
    ):
        raise ValueError("invalid signed P65 local handoff receipt")
    config_path = Path(manifest["config_binding"]["path"])
    if (
        _binding(config_path) != manifest["config_binding"]
        or code_bindings(config_path) != manifest["training_code_bindings"]
    ):
        raise ValueError("handoff config or code binding changed")
    rows, inputs = collect(config_path)
    if (
        inputs != manifest["input_bindings"]
        or tokenizer_digest() != manifest["tokenizer"]["asset_manifest_sha256"]
    ):
        raise ValueError("upstream input or tokenizer binding changed")
    root = manifest_path.parent
    if manifest.get("manifest_path") != manifest_path.name or {
        entry["path"] for entry in manifest["outputs"]
    } != set(OUTPUTS):
        raise ValueError("handoff output inventory mismatch")
    for entry in manifest["outputs"]:
        path = _member(root, entry["path"])
        if _digest(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("handoff output hash changed")
    source_by_id = {
        (meta["domain"], meta["sample_id"]): (sample, meta) for sample, meta in rows
    }
    output_rows = {
        (domain, split): read_jsonl(root / f"{domain}_{split}.jsonl")
        for domain in DOMAINS
        for split in SPLITS
    }
    seen = set()
    seen_positions = set()
    for line in _member(root, "sample_index.jsonl").read_text().splitlines():
        entry = json.loads(line)
        key_id = (entry["domain"], entry["sample_id"])
        position = (entry["domain"], entry["split"], entry["row_index"])
        if (
            key_id in seen
            or key_id not in source_by_id
            or position in seen_positions
            or entry["output_file"] != f"{entry['domain']}_{entry['split']}.jsonl"
            or entry["domain"] not in DOMAINS
            or entry["split"] not in SPLITS
            or type(entry["row_index"]) is not int
            or not 0
            <= entry["row_index"]
            < len(output_rows[entry["domain"], entry["split"]])
        ):
            raise ValueError("duplicate or unknown handoff index identity")
        sample, meta = source_by_id[key_id]
        if (
            output_rows[entry["domain"], entry["split"]][entry["row_index"]] != sample
            or entry["full_message_tokens"] != meta["stored_full_message_tokens"]
            or any(
                entry[k] != meta[k] for k in meta if k != "stored_full_message_tokens"
            )
        ):
            raise ValueError("handoff is not an exact source projection")
        seen.add(key_id)
        seen_positions.add(position)
    if (
        len(seen) != len(rows)
        or len(seen_positions) != sum(len(value) for value in output_rows.values())
        or _json(root / "dataset_info.json") != dataset_info()
    ):
        raise ValueError("handoff row inventory mismatch")
    return {
        "ok": True,
        "counts": manifest["counts"],
        "combined_counts": manifest["combined_counts"],
        "strict_long_dependency_verified": False,
        "production_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("prepare")
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    check = subparsers.add_parser("validate")
    check.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = (
        prepare(args.config, args.output, args.workers)
        if args.command == "prepare"
        else validate(args.manifest)
    )
    print(
        json.dumps(
            result
            if args.command == "validate"
            else {
                "manifest": str(data_path(args.output) / MANIFEST),
                "counts": result["counts"],
                "combined_counts": result["combined_counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
