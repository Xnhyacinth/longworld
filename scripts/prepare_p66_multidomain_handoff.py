#!/usr/bin/env python3
"""Build and validate the signed P66 multi-domain local-candidate handoff."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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

RECEIPT_SCHEMA = "longworld.p66-multidomain-handoff-receipt.v1"
PROFILE = "p66-multisource-filtered-local-candidate-sft-v1"
MANIFEST = "BUILD_RECEIPT.json"
SPLITS = ("train", "eval")
DOMAINS = (
    "codeforge",
    "finance_disclosure",
    "ietf",
    "cyber_osv",
    "macro_vintage",
    "researchlab",
)
NET_NEW_DOMAINS = DOMAINS[2:]
EXPECTED_COUNTS = {
    "codeforge": {"train": 96, "eval": 34},
    "finance_disclosure": {"train": 18, "eval": 16},
    "ietf": {"train": 0, "eval": 0},
    "cyber_osv": {"train": 18, "eval": 18},
    "macro_vintage": {"train": 0, "eval": 0},
    "researchlab": {"train": 0, "eval": 0},
}
LIMIT = 262144
EXACT_RANGES = (
    ("64k", 64000, 65536),
    ("128k", 128000, 131072),
    ("256k", 256000, 262144),
)


@dataclass(frozen=True)
class SourceSpec:
    name: str
    root: str
    receipt_sha256: str
    receipt_schema: str
    config: str | None = None
    code: tuple[str, ...] = ()
    code_pins: tuple[tuple[str, str], ...] = ()


# Receipt hashes are deliberately pinned here: changing a native taskbank requires
# an explicit new handoff revision, rather than silently changing a training view.
SOURCE_SPECS = (
    SourceSpec(
        "p65",
        "data/candidates/p65_dependency_handoff_v1",
        "56c28c73ce40963375fd60e53e9f47f2351170f55b18b276b35b1b688ad32f15",
        "longworld.p65-dependency-handoff-receipt.v1",
    ),
    SourceSpec(
        "cyber_osv",
        "data/candidates/p66_cyber_osv_taskbank_v1",
        "82b496438abfeecf7ed9f9a928503d29bd86839964bccadc128b9b172ef885ba",
        "longworld.p66-cyber-osv-taskbank-receipt.v1",
        "configs/p66_cyber_osv_taskbank_v1.json",
        (
            "longworld/core/p66_cyber_taskbank.py",
            "scripts/materialize_p66_cyber_taskbank.py",
        ),
        (
            (
                "longworld/core/p66_cyber_taskbank.py",
                "1019edda1944c0bff941fe546ed44952962c214b0c0e5d8187243be15469f9aa",
            ),
            (
                "scripts/materialize_p66_cyber_taskbank.py",
                "25f3fd234c0e3df7fd71d0a6ba43205f2332b721b42806f63c5da6a14de60e10",
            ),
        ),
    ),
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
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("unsafe project path")
    if relative.parts[0] == "data":
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


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def checked_sample(sample: dict, sample_id: str) -> dict:
    if sample.get("sample_id") != sample_id or set(sample) not in (
        {"sample_id", "messages"},
        {"sample_id", "split", "messages"},
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
    return sample


def _file_inventory(receipt: dict) -> dict[str, dict]:
    raw = receipt.get("files")
    if not isinstance(raw, dict):
        raise TypeError("native receipt lacks file inventory")
    result = {}
    for name, value in raw.items():
        if isinstance(value, str):
            result[name] = {"sha256": value}
        elif isinstance(value, dict) and isinstance(value.get("sha256"), str):
            result[name] = value
        else:
            raise TypeError("invalid native file inventory entry")
    return result


def checked_native_tree(spec: SourceSpec) -> tuple[dict, list[dict]]:
    root = project_root(spec.root)
    receipt_path = _member(root, MANIFEST)
    if _digest(receipt_path) != spec.receipt_sha256:
        raise ValueError(f"{spec.name} native receipt hash changed")
    receipt = _json(receipt_path)
    if receipt.get("schema_version") != spec.receipt_schema:
        raise ValueError(f"{spec.name} native receipt schema mismatch")
    files = _file_inventory(receipt)
    actual = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if any(path.is_symlink() for path in root.rglob("*")) or actual != set(files) | {
        MANIFEST
    }:
        raise ValueError(f"{spec.name} native output inventory mismatch")
    bindings = [_binding(receipt_path)]
    for name, expected in sorted(files.items()):
        path = _member(root, name)
        if _digest(path) != expected["sha256"] or (
            "bytes" in expected and path.stat().st_size != expected["bytes"]
        ):
            raise ValueError(f"{spec.name} native output hash changed")
        bindings.append(_binding(path))
    if spec.config:
        config_path = project_path(spec.config)
        if _digest(config_path) != receipt.get("config_sha256"):
            raise ValueError(f"{spec.name} native config binding changed")
        bindings.append(_binding(config_path))
    receipt_code = receipt.get("code_sha256", {})
    explicit_code = dict(spec.code_pins)
    for name in spec.code:
        path = project_path(name)
        digest = _digest(path)
        expected = receipt_code.get(name, explicit_code.get(name))
        if expected is None or expected != digest:
            raise ValueError(f"{spec.name} native code binding changed")
        bindings.append(_binding(path))
    for entry in receipt.get("input_bindings", []):
        path = project_path(entry["path"])
        if _digest(path) != entry["sha256"]:
            raise ValueError(f"{spec.name} native source binding changed")
        normalized = _binding(path)
        if "source_role_key_id" in entry:
            normalized["source_role_key_id"] = entry["source_role_key_id"]
        bindings.append(normalized)
    return receipt, bindings


def checked_p65_tree(spec: SourceSpec) -> tuple[dict, list[dict]]:
    from scripts.prepare_p65_dependency_handoff import validate as validate_p65

    root = project_root(spec.root)
    receipt_path = _member(root, MANIFEST)
    if _digest(receipt_path) != spec.receipt_sha256:
        raise ValueError("P65 signed receipt hash changed")
    validate_p65(receipt_path)
    receipt = _json(receipt_path)
    if receipt.get("schema_version") != spec.receipt_schema:
        raise ValueError("P65 signed receipt schema mismatch")
    bindings = [_binding(receipt_path)]
    expected = {entry["path"]: entry for entry in receipt["outputs"]}
    actual = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if any(path.is_symlink() for path in root.rglob("*")) or actual != set(expected) | {
        MANIFEST
    }:
        raise ValueError("P65 output inventory mismatch")
    for name, entry in sorted(expected.items()):
        path = _member(root, name)
        if _digest(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("P65 output binding changed")
        bindings.append(_binding(path))
    bindings.extend(receipt["input_bindings"])
    bindings.extend(receipt["training_code_bindings"])
    return receipt, bindings


def _raw_split_files(root: Path) -> dict[str, bytes]:
    return {split: (root / f"{split}.jsonl").read_bytes() for split in SPLITS}


def _p65_rows(
    spec: SourceSpec,
) -> tuple[dict[str, bytes], list[tuple[dict, dict]], dict, list[dict]]:
    receipt, bindings = checked_p65_tree(spec)
    root = project_root(spec.root)
    raw = {}
    samples = {}
    for domain in DOMAINS[:2]:
        for split in SPLITS:
            name = f"{domain}_{split}.jsonl"
            raw[name] = (root / name).read_bytes()
            samples[domain, split] = read_jsonl(root / name)
    rows = []
    for meta in read_jsonl(root / "sample_index.jsonl"):
        domain, split, index = meta["domain"], meta["split"], meta["row_index"]
        if domain not in DOMAINS[:2] or split not in SPLITS:
            raise ValueError("P65 index domain/split mismatch")
        sample = checked_sample(samples[domain, split][index], meta["sample_id"])
        rows.append((sample, {**meta, "evidence_class": meta["admission_state"]}))
    return raw, rows, receipt, bindings


def _indexed_rows(
    spec: SourceSpec,
    *,
    metadata_file: str,
    id_key: str,
    domain: str,
    evidence_class,
) -> tuple[dict[str, bytes], list[tuple[dict, dict]], dict, list[dict]]:
    receipt, bindings = checked_native_tree(spec)
    root = project_root(spec.root)
    split_raw = _raw_split_files(root)
    samples = {split: read_jsonl(root / f"{split}.jsonl") for split in SPLITS}
    metadata = {row[id_key]: row for row in read_jsonl(root / metadata_file)}
    rows = []
    for split in SPLITS:
        for sample in samples[split]:
            sample_id = sample["sample_id"]
            item = metadata.get(sample_id)
            if item is None or item.get("split") != split:
                raise ValueError(f"{domain} SFT row lacks exact native metadata")
            checked_sample(sample, sample_id)
            semantic_id = item.get("semantic_task_id", item.get("task_id"))
            group_id = item.get("source_group_id", item.get("world_id"))
            world_id = item.get("world_instance_id", item.get("world_id"))
            if not all(
                isinstance(value, str) and value
                for value in (semantic_id, group_id, world_id)
            ):
                raise ValueError(f"{domain} native identity is incomplete")
            if (
                item.get("strict_long_dependency_verified") is not False
                or item.get("production_eligible") is not False
            ):
                raise ValueError(f"{domain} native admission boundary changed")
            rows.append(
                (
                    sample,
                    {
                        "domain": domain,
                        "split": split,
                        "group_id": group_id,
                        "world_instance_id": world_id,
                        "context_id": item["context_sha256"],
                        "semantic_task_id": semantic_id,
                        "sample_id": sample_id,
                        "stored_full_message_tokens": item.get("full_hf_chat_tokens"),
                        "evidence_class": evidence_class(item),
                    },
                )
            )
    if len(metadata) != len(rows):
        raise ValueError(f"{domain} metadata/SFT inventory mismatch")
    return (
        {f"{domain}_{split}.jsonl": split_raw[split] for split in SPLITS},
        rows,
        receipt,
        bindings,
    )


def collect() -> tuple[
    dict[str, bytes], list[tuple[dict, dict]], dict[str, dict], list[dict]
]:
    specs = {spec.name: spec for spec in SOURCE_SPECS}
    parts = [
        _p65_rows(specs["p65"]),
        _indexed_rows(
            specs["cyber_osv"],
            metadata_file="tasks.jsonl",
            id_key="task_id",
            domain="cyber_osv",
            evidence_class=lambda row: (
                "short_window_resistant_compact_target_sufficient"
            ),
        ),
    ]
    raw, rows, receipts, bindings = {}, [], {}, []
    for part_raw, part_rows, receipt, part_bindings in parts:
        overlap = set(raw) & set(part_raw)
        if overlap:
            raise ValueError("duplicate handoff output names")
        raw.update(part_raw)
        rows.extend(part_rows)
        receipts[receipt["schema_version"]] = receipt
        bindings.extend(part_bindings)
    unique = {binding["path"]: binding for binding in bindings}
    return raw, rows, receipts, sorted(unique.values(), key=lambda item: item["path"])


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
    percentile = lambda q: ordered[round((len(ordered) - 1) * q)]
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": percentile(0.1),
        "median": percentile(0.5),
        "p90": percentile(0.9),
        "max": ordered[-1],
        "sum": sum(ordered),
    }


def audit_identities(rows: list[tuple[dict, dict]]) -> dict:
    tasks, views, samples, groups = set(), set(), set(), {}
    for _, meta in rows:
        task = (meta["domain"], meta["semantic_task_id"])
        sample = meta["sample_id"]
        group = (meta["domain"], meta["group_id"])
        view = (meta["domain"], meta["semantic_task_id"], meta["context_id"])
        if view in views or sample in samples:
            raise ValueError("duplicate training view or sample")
        if group in groups and groups[group] != meta["split"]:
            raise ValueError("source group crosses train/eval")
        tasks.add(task)
        views.add(view)
        samples.add(sample)
        groups[group] = meta["split"]
    return {
        "canonical_tasks": len(tasks),
        "training_views": len(views),
        "samples": len(samples),
        "groups": len(groups),
    }


def tokenizer_digest() -> str:
    from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

    with sanitized_attestation_environment():
        return resolved_tokenizer_asset_manifest_sha256(
            "Qwen/Qwen3.5-4B", TOKENIZER_REVISION
        )


def code_bindings() -> list[dict]:
    return sorted(
        (
            _binding(path)
            for path in (
                Path(__file__).absolute(),
                ROOT / "longworld/core/taskbank_training.py",
                ROOT / "longworld/core/tokenizer_assets.py",
                ROOT / "scripts/train_sft.py",
                ROOT / "scripts/prepare_p65_dependency_handoff.py",
            )
        ),
        key=lambda item: item["path"],
    )


def dataset_info() -> dict:
    return {
        f"p66_{domain}_{split}": {
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
        if EXPECTED_COUNTS[domain][split]
    }


def prepare(output: Path, workers: int = 4) -> dict:
    output = data_path(output)
    raw, rows, receipts, inputs = collect()
    identities = audit_identities(rows)
    stage, assets = code_bindings(), tokenizer_digest()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    with sanitized_attestation_environment():
        tokenizer = _load_tokenizer("Qwen/Qwen3.5-4B", TOKENIZER_REVISION)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            measured = list(
                pool.map(
                    lambda row: _count_messages(row[0]["messages"], tokenizer, LIMIT),
                    rows,
                )
            )
    counts = {domain: {split: 0 for split in SPLITS} for domain in DOMAINS}
    tokens_by: dict[str, list[int]] = defaultdict(list)
    bins, exact = Counter(), Counter()
    index_rows = []
    positions = Counter()
    for (_, meta), tokens in zip(rows, measured, strict=True):
        stored = meta.get("stored_full_message_tokens")
        if (
            type(tokens) is not int
            or not 32768 < tokens <= LIMIT
            or (stored is not None and tokens != stored)
        ):
            raise ValueError("stored and recomputed full-chat token counts differ")
        domain, split = meta["domain"], meta["split"]
        row_index = positions[domain, split]
        positions[domain, split] += 1
        counts[domain][split] += 1
        index_rows.append(
            {
                **{
                    key: value
                    for key, value in meta.items()
                    if key != "stored_full_message_tokens"
                },
                "full_message_tokens": tokens,
                "capacity_bin": capacity(tokens),
                "exact_token_range": exact_range(tokens),
                "output_file": f"{domain}_{split}.jsonl",
                "row_index": row_index,
            }
        )
        for key in ("overall", domain, f"{domain}:{split}"):
            tokens_by[key].append(tokens)
        bins[capacity(tokens)] += 1
        exact[exact_range(tokens)] += 1
    if counts != EXPECTED_COUNTS:
        raise ValueError("exact per-domain source count matrix changed")
    expected_names = {
        f"{domain}_{split}.jsonl"
        for domain in DOMAINS
        for split in SPLITS
        if EXPECTED_COUNTS[domain][split]
    }
    if set(raw) != expected_names:
        raise ValueError("handoff domain/split output inventory mismatch")
    for name in sorted(raw):
        (output / name).write_bytes(raw[name])
    (output / "sample_index.jsonl").write_text(
        "".join(_canonical(row) + "\n" for row in index_rows)
    )
    (output / "dataset_info.json").write_text(_canonical(dataset_info()) + "\n")
    if (
        collect()[3] != inputs
        or code_bindings() != stage
        or tokenizer_digest() != assets
    ):
        raise ValueError(
            "source, tokenizer, or handoff code changed during preparation"
        )
    net_new = sum(
        counts[domain][split] for domain in NET_NEW_DOMAINS for split in SPLITS
    )
    manifest = {
        "schema_version": RECEIPT_SCHEMA,
        "profile_id": PROFILE,
        "manifest_path": MANIFEST,
        "trust_scope": "local_probe",
        "local_sft_handoff_eligible": True,
        "strict_long_dependency_verified": False,
        "training_release_eligible": False,
        "production_eligible": False,
        "framework_preprocessing_verified": False,
        "qualification_boundary": "All rows are validated local candidates; evidence classes remain domain-specific and compact controls prevent a universal strict-long claim.",
        "source_receipt_bindings": [
            {
                "name": spec.name,
                "path": str(project_root(spec.root) / MANIFEST),
                "sha256": spec.receipt_sha256,
                "schema_version": spec.receipt_schema,
            }
            for spec in SOURCE_SPECS
        ],
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
        "p65_carried_rows": sum(
            counts[domain][split] for domain in DOMAINS[:2] for split in SPLITS
        ),
        "p66_net_new_rows": net_new,
        "p66_included_canonical_tasks": identities["canonical_tasks"] - 164,
        "p66_included_training_views": identities["training_views"] - 164,
        "p66_evaluated_candidate_inventory": {
            "canonical_tasks_before_scientific_quarantine": 195,
            "training_views_before_scientific_quarantine": 411,
        },
        **identities,
        "source_domains_evaluated": len(DOMAINS),
        "training_domains": sum(
            any(counts[domain][split] for split in SPLITS) for domain in DOMAINS
        ),
        "included_world_instances": len(
            {(meta["domain"], meta["world_instance_id"]) for _, meta in rows}
        ),
        "unique_contexts": len(
            {(meta["domain"], meta["context_id"]) for _, meta in rows}
        ),
        "source_worlds_reported": {
            "p65": receipts["longworld.p65-dependency-handoff-receipt.v1"][
                "source_worlds"
            ]["total"],
            "cyber_osv": receipts["longworld.p66-cyber-osv-taskbank-receipt.v1"][
                "world_count"
            ],
        },
        "candidate_bearing_worlds": {
            "p65": 9,
            "ietf": 0,
            "cyber_osv": 6,
            "macro_vintage": 0,
            "researchlab": 0,
            "p66_included": 6,
            "consolidated_included": 15,
        },
        "scientific_quarantine": {
            "ietf": {
                "excluded_training_views": 3,
                "reason": "counterfactual/question conflict, output key-order mismatch, and compact/latest controls unmeasured",
            },
            "macro_vintage": {
                "excluded_training_views": 324,
                "reason": "answer programs ignore relation blocks that create the named length bands",
            },
            "researchlab": {
                "excluded_training_views": 48,
                "reason": "substantive prose deduplication and executable answer-bearing shortcut controls are not present in the bound receipt",
            },
        },
        "evidence_class_counts": dict(
            sorted(Counter(meta["evidence_class"] for _, meta in rows).items())
        ),
        "full_chat_token_distribution": {
            name: distribution(values) for name, values in sorted(tokens_by.items())
        },
        "full_chat_capacity_bins": dict(sorted(bins.items())),
        "full_chat_exact_token_ranges": dict(sorted(exact.items())),
        "token_count_basis": "pinned Qwen3.5 chat template, thinking disabled, complete assistant target, truncation=false",
    }
    output_names = sorted(raw) + ["sample_index.jsonl", "dataset_info.json"]
    manifest["outputs"] = [
        {
            "path": name,
            "sha256": _digest(output / name),
            "bytes": (output / name).stat().st_size,
        }
        for name in output_names
    ]
    signed = attach_attestation(
        manifest, _report_key(), purpose="training_export_manifest"
    )
    if (
        signed.get("attestation", {}).get("role") != "report"
        or signed["attestation"].get("environment") != "probe"
    ):
        raise ValueError("probe report identity required")
    (output / MANIFEST).write_text(_canonical(signed) + "\n")
    return signed


def validate(manifest_path: Path, workers: int = 4) -> dict:
    manifest_path = data_path(manifest_path)
    manifest = _json(manifest_path)
    if (
        manifest.get("schema_version") != RECEIPT_SCHEMA
        or manifest.get("profile_id") != PROFILE
        or manifest.get("local_sft_handoff_eligible") is not True
        or manifest.get("strict_long_dependency_verified") is not False
        or manifest.get("training_release_eligible") is not False
        or manifest.get("production_eligible") is not False
        or not verify_attestation(
            manifest, _report_key(), purpose="training_export_manifest"
        )
    ):
        raise ValueError("invalid signed P66 local handoff receipt")
    raw, rows, _, inputs = collect()
    identities = audit_identities(rows)
    expected_receipts = [
        {
            "name": spec.name,
            "path": str(project_root(spec.root) / MANIFEST),
            "sha256": spec.receipt_sha256,
            "schema_version": spec.receipt_schema,
        }
        for spec in SOURCE_SPECS
    ]
    expected_combined = {
        split: sum(EXPECTED_COUNTS[domain][split] for domain in DOMAINS)
        for split in SPLITS
    }
    expected_net_new = sum(
        EXPECTED_COUNTS[domain][split] for domain in NET_NEW_DOMAINS for split in SPLITS
    )
    if (
        inputs != manifest["input_bindings"]
        or code_bindings() != manifest["training_code_bindings"]
        or tokenizer_digest() != manifest["tokenizer"]["asset_manifest_sha256"]
        or manifest.get("source_receipt_bindings") != expected_receipts
        or manifest.get("counts") != EXPECTED_COUNTS
        or manifest.get("combined_counts") != expected_combined
        or manifest.get("p65_carried_rows") != 164
        or manifest.get("p66_net_new_rows") != expected_net_new
        or manifest.get("p66_included_canonical_tasks")
        != identities["canonical_tasks"] - 164
        or manifest.get("p66_included_training_views")
        != identities["training_views"] - 164
        or manifest.get("source_domains_evaluated") != 6
        or manifest.get("training_domains")
        != sum(
            any(EXPECTED_COUNTS[domain][split] for split in SPLITS)
            for domain in DOMAINS
        )
        or any(manifest.get(key) != value for key, value in identities.items())
    ):
        raise ValueError("P66 upstream or code binding changed")
    root = manifest_path.parent
    expected_outputs = sorted(raw) + ["sample_index.jsonl", "dataset_info.json"]
    if manifest.get("manifest_path") != manifest_path.name or {
        entry["path"] for entry in manifest["outputs"]
    } != set(expected_outputs):
        raise ValueError("P66 handoff output inventory mismatch")
    actual = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if any(path.is_symlink() for path in root.rglob("*")) or actual != set(
        expected_outputs
    ) | {MANIFEST}:
        raise ValueError("P66 handoff tree inventory mismatch")
    for entry in manifest["outputs"]:
        path = _member(root, entry["path"])
        if _digest(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("P66 handoff output hash changed")
    for name, source_bytes in raw.items():
        if (root / name).read_bytes() != source_bytes:
            raise ValueError("P66 handoff is not an exact source projection")
    index = read_jsonl(root / "sample_index.jsonl")
    if len(index) != len(rows):
        raise ValueError("P66 handoff index count mismatch")
    with sanitized_attestation_environment():
        tokenizer = _load_tokenizer("Qwen/Qwen3.5-4B", TOKENIZER_REVISION)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            measured = list(
                pool.map(
                    lambda row: _count_messages(row[0]["messages"], tokenizer, LIMIT),
                    rows,
                )
            )
    positions = Counter()
    tokens_by: dict[str, list[int]] = defaultdict(list)
    bins, exact = Counter(), Counter()
    for (_, meta), entry, tokens in zip(rows, index, measured, strict=True):
        row_index = positions[meta["domain"], meta["split"]]
        positions[meta["domain"], meta["split"]] += 1
        expected = {
            **{
                key: value
                for key, value in meta.items()
                if key != "stored_full_message_tokens"
            },
            "full_message_tokens": tokens,
            "capacity_bin": capacity(tokens),
            "exact_token_range": exact_range(tokens),
            "output_file": f"{meta['domain']}_{meta['split']}.jsonl",
            "row_index": row_index,
        }
        if entry != expected:
            raise ValueError("P66 handoff index/token projection mismatch")
        for key in ("overall", meta["domain"], f"{meta['domain']}:{meta['split']}"):
            tokens_by[key].append(tokens)
        bins[capacity(tokens)] += 1
        exact[exact_range(tokens)] += 1
    if (
        _json(root / "dataset_info.json") != dataset_info()
        or manifest.get("full_chat_token_distribution")
        != {name: distribution(values) for name, values in sorted(tokens_by.items())}
        or manifest.get("full_chat_capacity_bins") != dict(sorted(bins.items()))
        or manifest.get("full_chat_exact_token_ranges") != dict(sorted(exact.items()))
        or manifest.get("evidence_class_counts")
        != dict(sorted(Counter(meta["evidence_class"] for _, meta in rows).items()))
    ):
        raise ValueError("P66 dataset info mismatch")
    return {
        "ok": True,
        "counts": manifest["counts"],
        "p66_net_new_rows": manifest["p66_net_new_rows"],
        "strict_long_dependency_verified": False,
        "production_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("prepare")
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    check = subparsers.add_parser("validate")
    check.add_argument("--manifest", required=True, type=Path)
    check.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    result = (
        prepare(args.output, args.workers)
        if args.command == "prepare"
        else validate(args.manifest, args.workers)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
