#!/usr/bin/env python3
"""Prepare a signed local P64 Finance/CodeForge input snapshot; never run training."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from itertools import zip_longest
from pathlib import Path

import yaml

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
    _regular,
    _report_key,
)
from scripts.validate_training_export import materialize_verified_snapshot

SCHEMA = "longworld.p64-local-training-manifest.v1"
PROFILE = "p64-finance-codeforge-primary-local-v1"
MANIFEST = "P64_TRAINING_MANIFEST.json"
DOMAINS = ("finance", "codeforge")
SPLITS = ("train", "eval")
LIMIT = 262144
VALIDATORS = {
    "finance": "scripts/materialize_finance_taskbank_v2.py",
    "codeforge": "scripts/export_codeforge_taskbank_sft.py",
}
RECIPE = {
    "model_name_or_path": "Qwen/Qwen3.5-4B",
    "model_revision": TOKENIZER_REVISION,
    "trust_remote_code": False,
    "stage": "sft",
    "dataset": "p64_finance_train",
    "template": "qwen3_5_nothink",
    "cutoff_len": LIMIT,
    "packing": False,
    "neat_packing": False,
    "train_on_prompt": False,
    "val_size": 0.0,
}
OUTPUTS = tuple(f"{d}_{s}.jsonl" for d in DOMAINS for s in SPLITS) + (
    "sample_index.jsonl",
    "dataset_info.json",
    "TASKBANK_P64.yaml",
)


def data_mount_path(path):
    """Resolve the repository's data mount, while rejecting links below it."""
    path = path.absolute()
    mount = ROOT / "data"
    if path.is_relative_to(mount):
        path = mount.resolve(strict=True) / path.relative_to(mount)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("handoff path contains a descendant symlink")
    return path


def project_file(value):
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe project input path")
    if relative.parts and relative.parts[0] == "data":
        return _member((ROOT / "data").resolve(), Path(*relative.parts[1:]).as_posix())
    return _member(ROOT, value)


def recipe(path):
    parsed = yaml.safe_load(_regular(path).read_text())
    if parsed != RECIPE:
        raise ValueError("P64 protected recipe changed or unsupported option")
    return parsed


def tree_bindings(root, receipt):
    files = receipt["files"]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if any(p.is_symlink() for p in root.rglob("*")) or actual != set(files) | {
        "BUILD_RECEIPT.json"
    }:
        raise ValueError("source output inventory mismatch")
    result = [_binding(_member(root, "BUILD_RECEIPT.json"))]
    for name, sha in sorted(files.items()):
        path = _member(root, name)
        if _digest(path) != sha:
            raise ValueError("source file hash changed")
        result.append(_binding(path))
    for name, sha in receipt.get("code_sha256", {}).items():
        path = project_file(name)
        if _digest(path) != sha:
            raise ValueError("source code binding changed")
        result.append(_binding(path))
    for entry in receipt.get("input_bindings", []) + receipt.get("source_bindings", []):
        path = Path(entry["path"])
        if path.is_absolute() and path.is_relative_to(ROOT):
            path = project_file(path.relative_to(ROOT).as_posix())
        else:
            path = _regular(path)
        if _digest(path) != entry["sha256"]:
            raise ValueError("native source/input binding changed")
        result.append(_binding(path))
    return result


def collect(finance_catalog, finance_root, code_catalog, code_root):
    fc, cc = _json(finance_catalog), _json(code_catalog)
    if (
        fc.get("schema_version") != "longworld.finance-taskbank-long-catalog.v1"
        or fc.get("legacy_union_allowed") is not False
    ):
        raise ValueError("unsupported finance catalog")
    if cc.get("schema_version") != "longworld.codeforge-taskbank-catalog.v1":
        raise ValueError("unsupported codeforge catalog")
    bindings = [_binding(finance_catalog), _binding(code_catalog)]
    worlds, names, groups = [], set(), {}
    for job in sorted(fc["jobs"], key=lambda j: j["issuer"]):
        name = job["issuer"]
        if not re.fullmatch(r"[a-z0-9_-]+", name) or name in names:
            raise ValueError("duplicate or invalid finance issuer")
        names.add(name)
        cp, sp = project_file(job["config"]), project_file(job["source_manifest"])
        config = _json(cp)
        if (
            _digest(cp) != job["config_sha256"]
            or _digest(sp) != job["source_manifest_sha256"]
            or config["source_manifest"] != job["source_manifest"]
        ):
            raise ValueError("finance config/source binding changed")
        group, split = config["split_group_id"], config["split"]
        if not re.fullmatch(r"\d{10}", group) or split not in SPLITS or group in groups:
            raise ValueError("duplicate CIK or invalid finance split")
        groups[group] = split
        root = finance_root / name
        receipt = _json(_member(root, "BUILD_RECEIPT.json"))
        if (
            receipt["schema_version"] != "longworld.finance-taskbank-long-receipt.v1"
            or receipt["config_sha256"] != _digest(cp)
            or receipt["source_manifest"]["sha256"] != _digest(sp)
            or receipt["split_group_id"] != group
            or receipt["split"] != split
            or receipt["production_eligible"] is not False
        ):
            raise ValueError("finance receipt mismatch")
        if config["tokenizer"] != {
            "model_id": RECIPE["model_name_or_path"],
            "revision": TOKENIZER_REVISION,
        }:
            raise ValueError("finance tokenizer mismatch")
        bindings += [_binding(cp), _binding(sp), *tree_bindings(root, receipt)]
        worlds.append(
            {
                "domain": "finance",
                "name": name,
                "job": job,
                "config": config,
                "root": root,
                "receipt": receipt,
            }
        )
    for w in worlds:
        opposite = {g for g, s in groups.items() if s != w["config"]["split"]}
        if not opposite <= set(w["config"]["excluded_split_group_ids"]):
            raise ValueError("finance opposite CIK split not protected")
    code_groups = {}
    for job in cc["jobs"]:
        cp = project_file(job["config"])
        config = _json(cp)
        group, split = config["source_group_id"], config["split"]
        if (
            split not in SPLITS
            or group in code_groups
            or not group.startswith("https://github.com/")
        ):
            raise ValueError("duplicate repository or invalid codeforge split")
        code_groups[group] = split
        bindings.append(_binding(cp))
        sp = project_file(config["source_bundle"])
        if _digest(sp) != config["source_bundle_sha256"]:
            raise ValueError("codeforge bundle changed")
        bindings.append(_binding(sp))
        bank_root = (ROOT / job["output"]).resolve(strict=True)
        bank_receipt = _json(_member(bank_root, "BUILD_RECEIPT.json"))
        if (
            bank_receipt.get("schema_version")
            != "longworld.codeforge-taskbank-build.v1"
            or bank_receipt.get("source_verified") is not True
            or bank_receipt.get("source_group_id") != group
            or bank_receipt.get("split") != split
        ):
            raise ValueError("codeforge source-bank identity mismatch")
        bindings += tree_bindings(bank_root, bank_receipt)
    code_receipt = _json(_member(code_root, "BUILD_RECEIPT.json"))
    if (
        code_receipt.get("schema_version")
        != "longworld.codeforge-taskbank-sft-build.v1"
        or code_receipt.get("production_eligible") is not False
        or code_receipt.get("tokenizer")
        != {
            "model_id": RECIPE["model_name_or_path"],
            "revision": TOKENIZER_REVISION,
            "template": "qwen3_5_nothink",
            "cutoff": LIMIT,
            "truncation": False,
        }
    ):
        raise ValueError("codeforge primary tokenizer/source contract mismatch")
    bindings += tree_bindings(code_root, code_receipt)
    worlds.append(
        {
            "domain": "codeforge",
            "name": "codeforge",
            "job": {
                "trust_file": cc["trust_file"],
                "catalog": str(code_catalog),
                "approved_policy_sha256": cc["approved_policy_sha256"],
                "source_client_sha256": cc["source_client_sha256"],
            },
            "config": {"groups": code_groups},
            "root": code_root,
            "receipt": code_receipt,
        }
    )
    unique = {b["path"]: b for b in bindings}
    return worlds, sorted(unique.values(), key=lambda b: b["path"])


def validate_source(world, logs):
    domain = world["domain"]
    if domain not in VALIDATORS:
        raise ValueError("unsupported domain validator")
    argv = [
        sys.executable,
        str(ROOT / "scripts/run_with_local_probe_trust.py"),
        "--trust-file",
        world["job"]["trust_file"],
        "--role",
        "source",
    ]
    env = dict(os.environ)
    env.update(CUDA_VISIBLE_DEVICES="", TOKENIZERS_PARALLELISM="false")
    if domain == "codeforge":
        pins = world["job"]["approved_policy_sha256"]
        client = world["job"]["source_client_sha256"]
        if not pins or any(
            re.fullmatch(r"[0-9a-f]{64}", p) is None for p in [*pins, client]
        ):
            raise ValueError("invalid codeforge policy/client pins")
        env["LONGWORLD_PUBLIC_POLICY_SHA256"] = ",".join(pins)
        env["LONGWORLD_GH_BINARY_SHA256"] = client
    for name in (
        "CUDA_VISIBLE_DEVICES",
        "TOKENIZERS_PARALLELISM",
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "LONGWORLD_PUBLIC_POLICY_SHA256",
        "LONGWORLD_GH_BINARY_SHA256",
    ):
        if name in env:
            argv += ["--pass-env", name]
    argv += ["--", sys.executable, str(ROOT / VALIDATORS[domain])]
    argv += (
        ["--config", str(project_file(world["job"]["config"]))]
        if domain == "finance"
        else ["--catalog", world["job"]["catalog"]]
    )
    argv += ["--output", str(world["root"]), "--validate"]
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=14400,
        check=False,
    )
    (logs / (world["name"] + ".stdout.txt")).write_text(result.stdout)
    (logs / (world["name"] + ".stderr.txt")).write_text(result.stderr)
    if result.returncode:
        raise ValueError(
            f"{domain} source validation failed: {world['name']} exit {result.returncode}"
        )
    proof = json.loads(result.stdout)
    if proof.get("status") != "PASS":
        raise ValueError("domain validator did not return PASS")
    return {"domain": domain, "name": world["name"], **proof}


def checked_sample(sample, sample_id):
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("missing canonical sample identity")
    if (
        set(sample) not in ({"messages"}, {"sample_id", "messages"})
        or sample.get("sample_id", sample_id) != sample_id
    ):
        raise ValueError("unexpected SFT envelope or sample identity")
    messages = sample["messages"]
    if (
        not isinstance(messages, list)
        or len(messages) != 2
        or [m.get("role") for m in messages] != ["user", "assistant"]
        or any(
            set(m) != {"role", "content"}
            or not isinstance(m["content"], str)
            or not m["content"]
            for m in messages
        )
    ):
        raise ValueError("invalid assistant-only message projection")
    return {"sample_id": sample_id, "messages": messages}


def rows(world):
    root = world["root"]
    if world["domain"] == "finance":
        count = 0
        with (
            _member(root, "tasks.jsonl").open() as tasks,
            _member(root, "sft_candidates.jsonl").open() as samples,
        ):
            for task_line, sample_line in zip_longest(tasks, samples):
                if task_line is None or sample_line is None:
                    raise ValueError("finance row alignment failed")
                task, sample = json.loads(task_line), json.loads(sample_line)
                if (
                    task["schema_version"]
                    != "longworld.finance-taskbank-long-sample.v1"
                    or task["variant"] != "full"
                    or task["variant_family_id"]
                    != "variants:" + task["semantic_task_id"]
                    or task["split_group_id"] != world["config"]["split_group_id"]
                    or task["split"] != world["config"]["split"]
                ):
                    raise ValueError("finance canonical primary identity mismatch")
                sample = checked_sample(sample, task["sample_id"])
                count += 1
                yield (
                    sample,
                    {
                        "domain": "finance",
                        "split": task["split"],
                        "group_id": task["split_group_id"],
                        "semantic_task_id": task["semantic_task_id"],
                        "sample_id": task["sample_id"],
                    },
                )
        if count != world["receipt"]["accepted_semantic_tasks"]:
            raise ValueError("finance task count mismatch")
    else:
        metadata = [
            json.loads(line)
            for line in _member(root, "metadata.jsonl").read_text().splitlines()
        ]
        indexed = {}
        for m in metadata:
            if m["output_file"] not in ("train.jsonl", "eval.jsonl"):
                continue
            if m.get("classification") != "long":
                raise ValueError("codeforge primary output contains non-long view")
            key = (m["output_file"], m["row_index"])
            if key in indexed:
                raise ValueError("duplicate codeforge row metadata")
            indexed[key] = m
        seen = set()
        for split in SPLITS:
            filename = split + ".jsonl"
            with _member(root, filename).open() as handle:
                for index, line in enumerate(handle):
                    key = (filename, index)
                    m = indexed.get(key)
                    if m is None:
                        raise ValueError("missing codeforge primary metadata")
                    seen.add(key)
                    group = m["source_group_id"]
                    if (
                        world["config"]["groups"].get(group) != split
                        or m["split"] != split
                    ):
                        raise ValueError("codeforge repository split mismatch")
                    yield (
                        checked_sample(json.loads(line), m["sample_id"]),
                        {
                            "domain": "codeforge",
                            "split": split,
                            "group_id": group,
                            "semantic_task_id": m["semantic_task_id"],
                            "sample_id": m["sample_id"],
                        },
                    )
        if seen != {key for key in indexed if key[0] in ("train.jsonl", "eval.jsonl")}:
            raise ValueError("extra codeforge primary metadata")


def dataset_info():
    return {
        f"p64_{d}_{s}": {
            "file_name": f"{d}_{s}.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
            },
        }
        for d in DOMAINS
        for s in SPLITS
    }


def tokenizer_digest():
    from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256

    with sanitized_attestation_environment():
        return resolved_tokenizer_asset_manifest_sha256(
            RECIPE["model_name_or_path"], TOKENIZER_REVISION
        )


def code_bindings(recipe_path):
    paths = [Path(__file__).absolute(), recipe_path.absolute()] + [
        ROOT / p
        for p in (
            "longworld/core/taskbank_training.py",
            "scripts/validate_training_export.py",
            "scripts/train_sft.py",
            "scripts/materialize_finance_histories.py",
            "longworld/core/tokenizer_assets.py",
        )
    ]
    return sorted((_binding(p) for p in paths), key=lambda b: b["path"])


def prepare(
    finance_catalog,
    finance_root,
    code_catalog,
    code_root,
    output,
    recipe_path,
    workers=4,
    audit_paths=(),
):
    output = data_mount_path(output)
    key = _report_key()
    recipe(recipe_path)
    assets = tokenizer_digest()
    audit_links = [_binding(p.absolute()) for p in audit_paths]
    stage_bindings = code_bindings(recipe_path)
    worlds, inputs = collect(finance_catalog, finance_root, code_catalog, code_root)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    logs = output / "source_validation"
    logs.mkdir(mode=0o700)
    with (
        sanitized_attestation_environment(),
        ThreadPoolExecutor(max_workers=min(workers, len(worlds))) as pool,
    ):
        proofs = list(pool.map(lambda w: validate_source(w, logs), worlds))
    counts = {d: {s: 0 for s in SPLITS} for d in DOMAINS}
    maximum = 0
    seen_tasks = set()
    seen_samples = set()
    groups = {}
    local = threading.local()
    lock = threading.Lock()

    def measure(sample, meta):
        if not hasattr(local, "tokenizer"):
            with lock:
                local.tokenizer = _load_tokenizer(
                    RECIPE["model_name_or_path"], TOKENIZER_REVISION
                )
        size = _count_messages(sample["messages"], local.tokenizer, LIMIT)
        if not 0 < size <= LIMIT:
            raise ValueError("full chat exceeds cutoff; truncation forbidden")
        return sample, {**meta, "full_message_tokens": size}

    previous_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        with (
            sanitized_attestation_environment(),
            ThreadPoolExecutor(max_workers=workers) as pool,
            ExitStack() as stack,
        ):
            handles = {
                (d, s): stack.enter_context((output / f"{d}_{s}.jsonl").open("x"))
                for d in DOMAINS
                for s in SPLITS
            }
            index = stack.enter_context((output / "sample_index.jsonl").open("x"))
            pending = deque()

            def drain():
                nonlocal maximum
                sample, meta = pending.popleft().result()
                d, s = meta["domain"], meta["split"]
                maximum = max(maximum, meta["full_message_tokens"])
                index.write(
                    _canonical(
                        {
                            **meta,
                            "output_file": f"{d}_{s}.jsonl",
                            "row_index": counts[d][s],
                        }
                    )
                    + "\n"
                )
                handles[d, s].write(_canonical(sample) + "\n")
                counts[d][s] += 1

            for w in worlds:
                for sample, meta in rows(w):
                    tid = (meta["domain"], meta["semantic_task_id"])
                    sid = meta["sample_id"]
                    group = (meta["domain"], meta["group_id"])
                    if tid in seen_tasks or sid in seen_samples:
                        raise ValueError(
                            "duplicate canonical task/sample; views cannot inflate rows"
                        )
                    if group in groups and groups[group] != meta["split"]:
                        raise ValueError("train/eval source group overlap")
                    groups[group] = meta["split"]
                    seen_tasks.add(tid)
                    seen_samples.add(sid)
                    pending.append(pool.submit(measure, sample, meta))
                    if len(pending) >= 2 * workers:
                        drain()
            while pending:
                drain()
    finally:
        if previous_cuda is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = previous_cuda
    if any(not counts[d][s] for d in DOMAINS for s in SPLITS):
        raise ValueError("each domain requires nonempty train and eval")
    (output / "dataset_info.json").write_text(_canonical(dataset_info()) + "\n")
    (output / "TASKBANK_P64.yaml").write_text(recipe_path.read_text())
    for b in inputs:
        if _binding(Path(b["path"])) != b:
            raise ValueError("upstream inputs changed during preparation")
    if code_bindings(recipe_path) != stage_bindings or tokenizer_digest() != assets:
        raise ValueError("training stage code or tokenizer changed during preparation")
    if any(_binding(Path(b["path"])) != b for b in audit_links):
        raise ValueError("dependency audit changed during preparation")
    manifest = {
        "schema_version": SCHEMA,
        "profile_id": PROFILE,
        "manifest_path": MANIFEST,
        "trust_scope": "local_probe",
        "local_training_eligible": True,
        "production_eligible": False,
        "strict_long_dependency_verified": False,
        "framework_preprocessing_verified": False,
        "arguments": {
            "finance_catalog": str(finance_catalog.absolute()),
            "finance_root": str(finance_root.absolute()),
            "code_catalog": str(code_catalog.absolute()),
            "code_root": str(code_root.absolute()),
            "recipe_path": str(recipe_path.absolute()),
        },
        "input_bindings": inputs,
        "advisory_dependency_audit_bindings": audit_links,
        "tokenizer_asset_manifest_sha256": assets,
        "source_validation": proofs,
        "training_code_bindings": stage_bindings,
        "counts": counts,
        "combined_counts": {s: sum(counts[d][s] for d in DOMAINS) for s in SPLITS},
        "canonical_tasks": len(seen_tasks),
        "samples": len(seen_samples),
        "max_full_message_tokens": maximum,
        "token_count_basis": "pinned chat template, thinking disabled, complete assistant target, truncation=false",
        "default_mode": "separate_domains",
        "dataset_modes": {
            "finance": "p64_finance_train",
            "codeforge": "p64_codeforge_train",
            "combined": "p64_finance_train,p64_codeforge_train",
        },
        "excluded_accounting": "CodeForge short.jsonl and overlength rejects are excluded from this primary handoff",
        "outputs": [
            {
                "path": p,
                "sha256": _digest(output / p),
                "bytes": (output / p).stat().st_size,
            }
            for p in OUTPUTS
        ],
    }
    manifest = attach_attestation(manifest, key, purpose="training_export_manifest")
    if (
        manifest.get("attestation", {}).get("role") != "report"
        or manifest["attestation"].get("environment") != "probe"
    ):
        raise ValueError("local report role required")
    (output / MANIFEST).write_text(_canonical(manifest) + "\n")
    return manifest


def validate(manifest_path, snapshot_root=None):
    manifest_path = data_mount_path(manifest_path)
    key = _report_key()
    m = _json(manifest_path)
    if (
        m.get("schema_version") != SCHEMA
        or m.get("profile_id") != PROFILE
        or m.get("production_eligible") is not False
        or m.get("local_training_eligible") is not True
        or m.get("strict_long_dependency_verified") is not False
        or m.get("framework_preprocessing_verified") is not False
        or m.get("trust_scope") != "local_probe"
        or m.get("default_mode") != "separate_domains"
        or m.get("attestation", {}).get("role") != "report"
        or m["attestation"].get("environment") != "probe"
        or not verify_attestation(m, key, purpose="training_export_manifest")
    ):
        raise ValueError("invalid signed local P64 training manifest")
    a = {k: Path(v) for k, v in m["arguments"].items()}
    recipe(a["recipe_path"])
    if code_bindings(a["recipe_path"]) != m["training_code_bindings"]:
        raise ValueError("training code/recipe binding changed")
    worlds, bindings = collect(
        a["finance_catalog"], a["finance_root"], a["code_catalog"], a["code_root"]
    )
    if bindings != m["input_bindings"]:
        raise ValueError("upstream input bindings changed")
    if tokenizer_digest() != m["tokenizer_asset_manifest_sha256"]:
        raise ValueError("tokenizer assets changed")
    for audit in m["advisory_dependency_audit_bindings"]:
        if _binding(Path(audit["path"])) != audit:
            raise ValueError("advisory dependency audit changed")
    root = manifest_path.absolute().parent
    if {e["path"] for e in m["outputs"]} != set(OUTPUTS) or m[
        "manifest_path"
    ] != manifest_path.name:
        raise ValueError("output inventory mismatch")
    for e in m["outputs"]:
        p = _member(root, e["path"])
        if _digest(p) != e["sha256"] or p.stat().st_size != e["bytes"]:
            raise ValueError("training output hash changed")
    counts = {d: {s: 0 for s in SPLITS} for d in DOMAINS}
    seen, tasks, groups = set(), set(), {}
    maximum = 0
    with ExitStack() as stack:
        outputs = {
            (d, s): stack.enter_context(_member(root, f"{d}_{s}.jsonl").open())
            for d in DOMAINS
            for s in SPLITS
        }
        index = stack.enter_context(_member(root, "sample_index.jsonl").open())
        for world in worlds:
            for sample, meta in rows(world):
                d, s = meta["domain"], meta["split"]
                if outputs[d, s].readline() != _canonical(sample) + "\n":
                    raise ValueError("training output not exact source projection")
                tid = (d, meta["semantic_task_id"])
                group = (d, meta["group_id"])
                if (
                    sample["sample_id"] in seen
                    or tid in tasks
                    or (group in groups and groups[group] != s)
                ):
                    raise ValueError("duplicate primary task/sample or split overlap")
                seen.add(sample["sample_id"])
                tasks.add(tid)
                groups[group] = s
                entry = json.loads(index.readline())
                tokens = entry.pop("full_message_tokens")
                expected = {
                    **meta,
                    "output_file": f"{d}_{s}.jsonl",
                    "row_index": counts[d][s],
                }
                if (
                    entry != expected
                    or type(tokens) is not int
                    or not 0 < tokens <= LIMIT
                ):
                    raise ValueError(
                        "sample index differs from canonical source identities"
                    )
                maximum = max(maximum, tokens)
                counts[d][s] += 1
        if index.readline() or any(handle.readline() for handle in outputs.values()):
            raise ValueError("extra training or metadata row")
    if (
        counts != m["counts"]
        or {s: sum(counts[d][s] for d in DOMAINS) for s in SPLITS}
        != m["combined_counts"]
        or len(tasks) != m["canonical_tasks"]
        or len(seen) != m["samples"]
        or maximum != m["max_full_message_tokens"]
        or _json(root / "dataset_info.json") != dataset_info()
        or recipe(root / "TASKBANK_P64.yaml") != RECIPE
    ):
        raise ValueError("training counts/dataset mapping mismatch")
    snap = (
        materialize_verified_snapshot(manifest_path, m, snapshot_root)
        if snapshot_root
        else None
    )
    return {
        "ok": True,
        "counts": counts,
        "production_eligible": False,
        "local_training_eligible": True,
        "snapshot_dir": str(snap) if snap else None,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--finance-catalog", type=Path, required=True)
    prep.add_argument("--finance-root", type=Path, required=True)
    prep.add_argument("--code-catalog", type=Path, required=True)
    prep.add_argument("--code-root", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument(
        "--recipe", type=Path, default=ROOT / "configs/llamafactory/TASKBANK_P64.yaml"
    )
    prep.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    prep.add_argument("--dependency-audit", type=Path, action="append", default=[])
    check = sub.add_parser("validate")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--snapshot-root", type=Path)
    args = p.parse_args()
    if args.command == "prepare":
        m = prepare(
            args.finance_catalog,
            args.finance_root.resolve(),
            args.code_catalog,
            args.code_root.resolve(),
            args.output,
            args.recipe,
            args.workers,
            args.dependency_audit,
        )
        print(
            json.dumps(
                {
                    "manifest": str(data_mount_path(args.output) / MANIFEST),
                    "counts": m["counts"],
                    "combined_counts": m["combined_counts"],
                },
                indent=2,
            )
        )
    else:
        print(json.dumps(validate(args.manifest, args.snapshot_root), indent=2))


if __name__ == "__main__":
    main()
