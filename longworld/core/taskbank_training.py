"""Explicit local training-input contract for verified finance task banks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path
from typing import Any

import yaml

from longworld.core.attestation import (
    attach_attestation,
    attestation_key_from_env,
    sanitized_attestation_environment,
    verify_attestation,
)
from scripts.validate_training_export import materialize_verified_snapshot

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "longworld.local-taskbank-training-manifest.v1"
PROFILE = "p63-local-finance-taskbank-training-v1"
TRANSFORM = "longworld.local-taskbank-messages.v1"
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
RECIPE_REQUIRED = {
    "model_name_or_path": "Qwen/Qwen3.5-4B",
    "model_revision": TOKENIZER_REVISION,
    "trust_remote_code": False,
    "dataset": "p63_taskbank_train",
    "template": "qwen3_5_nothink",
    "cutoff_len": 262144,
    "packing": False,
    "neat_packing": False,
    "train_on_prompt": False,
    "val_size": 0.0,
}
RECIPE_TEXT = yaml.safe_dump(RECIPE_REQUIRED, sort_keys=False)
RECIPE_RUNTIME_KEYS = frozenset(
    [
        "stage",
        "do_train",
        "finetuning_type",
        "deepspeed",
        "dataset_dir",
        "overwrite_cache",
        "preprocessing_num_workers",
        "output_dir",
        "logging_steps",
        "save_steps",
        "per_device_train_batch_size",
        "gradient_accumulation_steps",
        "learning_rate",
        "num_train_epochs",
        "lr_scheduler_type",
        "warmup_ratio",
        "bf16",
        "flash_attn",
        "gradient_checkpointing",
        "ddp_timeout",
    ]
)
OUTPUT_NAMES = ("train.jsonl", "eval.jsonl", "dataset_info.json")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _regular(path: Path) -> Path:
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(f"not a regular non-symlink file: {path}")
    return path


def _digest(path: Path) -> str:
    with _regular(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _member(root: Path, value: str) -> Path:
    relative = Path(value)
    if (
        not value
        or relative.is_absolute()
        or "\\" in value
        or any(p in {".", ".."} for p in relative.parts)
    ):
        raise ValueError("unsafe member path")
    path = root
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe member root")
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink member: {path}")
    return _regular(path)


def _project_member(value: str) -> Path:
    relative = Path(value)
    if relative.parts and relative.parts[0] == "data":
        return _member((ROOT / "data").resolve(), Path(*relative.parts[1:]).as_posix())
    return _member(ROOT, value)


def _json(path: Path) -> dict:
    value = json.loads(_regular(path).read_text())
    if not isinstance(value, dict):
        raise TypeError("expected JSON object")
    return value


def _binding(path: Path) -> dict:
    return {
        "path": str(path.absolute()),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _recipe(path: Path) -> dict:
    value = yaml.safe_load(_regular(path).read_text())
    if not isinstance(value, dict) or any(
        value.get(k) != v for k, v in RECIPE_REQUIRED.items()
    ):
        raise ValueError("taskbank protected training recipe mismatch")
    if value.get("eval_dataset") or value.get("tokenized_path"):
        raise ValueError("taskbank recipe cannot replace the protected source/split")
    if set(value) - RECIPE_REQUIRED.keys() - RECIPE_RUNTIME_KEYS:
        raise ValueError("unsupported taskbank recipe options")
    return dict(RECIPE_REQUIRED)


def _report_key() -> bytes:
    key = attestation_key_from_env("training_export_manifest")
    if os.environ.get("LONGWORLD_ATTESTATION_ENVIRONMENT") != "probe" or key is None:
        raise ValueError("local taskbank training requires a probe report identity")
    return key


def _training_code_bindings(recipe_path: Path) -> list[dict]:
    code_root = Path(__file__).resolve().parents[2]
    paths = [Path(__file__).resolve(), recipe_path.absolute()]
    paths += [
        code_root / relative
        for relative in (
            "scripts/prepare_taskbank_training.py",
            "scripts/train_llamafactory.sh",
            "scripts/train_sft.py",
            "scripts/validate_training_export.py",
        )
    ]
    return sorted((_binding(p) for p in paths), key=lambda e: e["path"])


def _validate_source_world(job: dict, world_root: Path, logs: Path) -> dict:
    env = dict(os.environ)
    env.update(CUDA_VISIBLE_DEVICES="", TOKENIZERS_PARALLELISM="false")
    argv = [
        sys.executable,
        str(ROOT / "scripts/run_with_local_probe_trust.py"),
        "--trust-file",
        job["trust_file"],
        "--role",
        "source",
    ]
    for name in (
        "CUDA_VISIBLE_DEVICES",
        "TOKENIZERS_PARALLELISM",
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
    ):
        if name in env:
            argv += ["--pass-env", name]
    argv += [
        "--",
        sys.executable,
        str(ROOT / "scripts/materialize_finance_taskbank.py"),
        "--config",
        str(ROOT / job["config"]),
        "--output",
        str(world_root),
        "--validate",
    ]
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=7200,
        check=False,
    )
    (logs / f"{job['issuer']}.stdout.txt").write_text(result.stdout)
    (logs / f"{job['issuer']}.stderr.txt").write_text(result.stderr)
    if result.returncode:
        raise ValueError(
            f"source-world validation failed: {job['issuer']} exit {result.returncode}"
        )
    proof = json.loads(result.stdout)
    if proof.get("status") != "PASS":
        raise ValueError("source-world validation did not return PASS")
    return proof


def _load_tokenizer(model_id: str, revision: str):
    from scripts.materialize_finance_histories import _load_tokenizer as load

    return load(model_id, revision)


def _count_messages(messages: list[dict], tokenizer, cutoff: int) -> int:
    from scripts.train_sft import tokenize_assistant_only

    return len(tokenize_assistant_only(tokenizer, messages, cutoff)["input_ids"])


def _collect(catalog_path: Path, batch_root: Path) -> tuple[list[dict], list[dict]]:
    catalog = _json(catalog_path)
    batch = _json(_member(batch_root, "BATCH_RECEIPT.json"))
    if (
        catalog.get("schema_version")
        != "longworld.p63-finance-taskbank-source-catalog.v1"
        or catalog.get("legacy_union_allowed") is not False
        or batch.get("schema_version") != "longworld.finance-taskbank-batch-receipt.v1"
        or batch.get("catalog_sha256") != _digest(catalog_path)
    ):
        raise ValueError(
            "taskbank-only catalog/batch binding mismatch; legacy union forbidden"
        )
    jobs = catalog.get("jobs", [])
    reported = {j["name"]: j for j in batch.get("jobs", [])}
    if not jobs or len(reported) != len(jobs):
        raise ValueError("batch job coverage mismatch")
    inputs = [_binding(catalog_path), _binding(batch_root / "BATCH_RECEIPT.json")]
    groups: dict[str, str] = {}
    normalized = []
    names = set()
    for job in sorted(jobs, key=lambda j: j["issuer"]):
        name = job["issuer"]
        if not re.fullmatch(r"[a-z0-9_-]+", name) or name in names:
            raise ValueError("invalid or duplicate issuer name")
        names.add(name)
        report = reported.get(name, {})
        if report.get("status") != "verified_local_candidates":
            raise ValueError("batch contains an unverified source world")
        config_path = _project_member(job["config"])
        config = _json(config_path)
        source_path = _project_member(job["source_manifest"])
        if (
            _digest(config_path) != job["config_sha256"]
            or _digest(source_path) != job["source_manifest_sha256"]
            or config["source_manifest"] != job["source_manifest"]
        ):
            raise ValueError("source/config binding changed")
        group, split = config["split_group_id"], config["split"]
        if (
            not isinstance(group, str)
            or not re.fullmatch(r"\d{10}", group)
            or split not in {"train", "eval"}
        ):
            raise ValueError("invalid taskbank source split")
        if group in groups:
            raise ValueError(
                "duplicate source group; mixed train/eval or aliased issuer"
            )
        groups[group] = split
        world_root = batch_root / name
        if world_root.is_symlink() or not world_root.is_dir():
            raise ValueError("unsafe source-world directory")
        receipt_path = _member(world_root, "BUILD_RECEIPT.json")
        receipt = _json(receipt_path)
        if (
            _digest(receipt_path) != report["receipt_sha256"]
            or receipt["config_sha256"] != job["config_sha256"]
            or receipt["source_manifest"]["sha256"] != job["source_manifest_sha256"]
            or receipt["split_group_id"] != group
            or receipt["split"] != split
            or report["split_group_id"] != group
            or report["split"] != split
            or report["semantic_tasks"] != receipt["accepted_semantic_tasks"]
        ):
            raise ValueError("source-world receipt binding mismatch")
        expected = set(receipt["files"]) | {"BUILD_RECEIPT.json"}
        actual = set()
        for path in world_root.rglob("*"):
            if path.is_symlink():
                raise ValueError("source-world member is a symlink")
            if path.is_file():
                actual.add(path.relative_to(world_root).as_posix())
        if actual != expected:
            raise ValueError(
                "source-world inventory mismatch; legacy/extra files forbidden"
            )
        for relative, digest in receipt["files"].items():
            path = _member(world_root, relative)
            if _digest(path) != digest:
                raise ValueError("source-world file hash changed")
            inputs.append(_binding(path))
        for relative, digest in receipt["code_sha256"].items():
            path = _project_member(relative)
            if _digest(path) != digest:
                raise ValueError("source compiler code changed")
            inputs.append(_binding(path))
        inputs += [_binding(config_path), _binding(source_path), _binding(receipt_path)]
        normalized.append(
            {"job": job, "config": config, "root": world_root, "receipt": receipt}
        )
    if names != set(reported):
        raise ValueError("batch reports unexpected issuer")
    allowed = {
        "BATCH_RECEIPT.json",
        *names,
        *(f"{n}.{stage}.log" for n in names for stage in ("build", "validate")),
    }
    if any(p.name not in allowed or p.is_symlink() for p in batch_root.iterdir()):
        raise ValueError("unexpected batch member; mixed legacy input forbidden")
    for item in normalized:
        config = item["config"]
        excluded = set(config.get("excluded_split_group_ids", []))
        opposite = {g for g, s in groups.items() if s != config["split"]}
        if config["split_group_id"] in excluded or not opposite <= excluded:
            raise ValueError("opposite split groups are not protected")
        if config["tokenizer"] != {
            "model_id": RECIPE_REQUIRED["model_name_or_path"],
            "revision": TOKENIZER_REVISION,
        }:
            raise ValueError("unsupported tokenizer contract")
    unique_inputs = {entry["path"]: entry for entry in inputs}
    return normalized, sorted(unique_inputs.values(), key=lambda e: e["path"])


def _rows(item: dict):
    root, config = item["root"], item["config"]
    with (
        _member(root, "tasks.jsonl").open() as tasks,
        _member(root, "sft_candidates.jsonl").open() as samples,
    ):
        count = 0
        for task_line, sample_line in zip_longest(tasks, samples):
            if task_line is None or sample_line is None:
                raise ValueError("task/sample count mismatch")
            task, sample = json.loads(task_line), json.loads(sample_line)
            if (
                task.get("schema_version") != "longworld.finance-taskbank-sample.v1"
                or set(sample) != {"sample_id", "messages"}
                or task["sample_id"] != sample["sample_id"]
                or task["split_group_id"] != config["split_group_id"]
                or task["split"] != config["split"]
                or task["variant"] != "full"
                or task["variant_family_id"] != "variants:" + task["semantic_task_id"]
            ):
                raise ValueError(
                    "taskbank sample schema/split mismatch; legacy rows forbidden"
                )
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
                raise ValueError("invalid taskbank messages")
            count += 1
            yield sample
        if count != item["receipt"]["accepted_semantic_tasks"]:
            raise ValueError("source task count mismatch")


def _dataset_info() -> dict:
    return {
        f"p63_taskbank_{split}": {
            "file_name": f"{split}.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }
        for split in ("train", "eval")
    }


def prepare_training_inputs(
    catalog_path: Path, batch_root: Path, output: Path, recipe_path: Path
) -> dict:
    key = _report_key()
    catalog_path = catalog_path.absolute()
    batch_root = batch_root.resolve(strict=True)
    protected = _recipe(recipe_path)
    worlds, inputs = _collect(catalog_path, batch_root)
    output = output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    logs = output / "source_validation"
    logs.mkdir(mode=0o700)
    proofs = []
    with ThreadPoolExecutor(max_workers=min(6, len(worlds))) as pool:
        futures = [
            pool.submit(_validate_source_world, w["job"], w["root"], logs)
            for w in worlds
        ]
        verified = [f.result() for f in futures]
    for world, proof in zip(worlds, verified, strict=True):
        if proof.get("semantic_tasks") != world["receipt"]["accepted_semantic_tasks"]:
            raise ValueError("fresh source validation task count mismatch")
        proofs.append({"issuer": world["job"]["issuer"], **proof})
    counts, maximum, seen = {"train": 0, "eval": 0}, 0, set()
    tokenizer_local = threading.local()
    tokenizer_init_lock = threading.Lock()

    def measure(split, sample):
        if not hasattr(tokenizer_local, "tokenizer"):
            # Transformers lazy imports are not safe to initialize concurrently.
            with tokenizer_init_lock:
                tokenizer_local.tokenizer = _load_tokenizer(
                    protected["model_name_or_path"], TOKENIZER_REVISION
                )
        return (
            split,
            sample,
            _count_messages(
                sample["messages"], tokenizer_local.tokenizer, protected["cutoff_len"]
            ),
        )

    previous_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    try:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        with (
            sanitized_attestation_environment(),
            ThreadPoolExecutor(max_workers=8) as pool,
            (output / "train.jsonl").open("x") as train,
            (output / "eval.jsonl").open("x") as evaluation,
        ):
            pending = deque()

            def drain():
                nonlocal maximum
                split, sample, size = pending.popleft().result()
                maximum = max(maximum, size)
                (train if split == "train" else evaluation).write(
                    _canonical(sample) + "\n"
                )
                counts[split] += 1

            for world in worlds:
                for sample in _rows(world):
                    if sample["sample_id"] in seen:
                        raise ValueError(
                            "duplicate sample identity across source worlds"
                        )
                    seen.add(sample["sample_id"])
                    pending.append(
                        pool.submit(measure, world["config"]["split"], sample)
                    )
                    if len(pending) >= 16:
                        drain()
            while pending:
                drain()
    finally:
        if previous_cuda is None:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = previous_cuda
    if not counts["train"] or not counts["eval"]:
        raise ValueError(
            "local taskbank requires explicit nonempty train and held-out eval"
        )
    (output / "dataset_info.json").write_text(_canonical(_dataset_info()) + "\n")
    for entry in inputs:
        if _digest(Path(entry["path"])) != entry["sha256"]:
            raise ValueError("upstream input changed during preparation")
    manifest = {
        "schema_version": SCHEMA,
        "profile_id": PROFILE,
        "transform_revision": TRANSFORM,
        "manifest_path": "TASKBANK_TRAINING_MANIFEST.json",
        "trust_scope": "local_probe",
        "local_training_eligible": True,
        "production_eligible": False,
        "legacy_release_eligible": False,
        "catalog_path": str(catalog_path),
        "batch_root": str(batch_root),
        "input_bindings": inputs,
        "source_validation": proofs,
        "counts": counts,
        "protected_recipe": protected,
        "recipe_path": str(recipe_path.absolute()),
        "max_full_message_tokens": maximum,
        "training_code_bindings": _training_code_bindings(recipe_path),
        "token_count_basis": "pinned HuggingFace chat template, thinking disabled; no model forward",
        "framework_preprocessing_verified": False,
        "split_scope": "isolated taskbank only; do not union legacy training with this AMD eval",
        "outputs": [
            {
                "path": name,
                "bytes": (output / name).stat().st_size,
                "sha256": _digest(output / name),
            }
            for name in OUTPUT_NAMES
        ],
    }
    manifest = attach_attestation(manifest, key, purpose="training_export_manifest")
    attestation = manifest.get("attestation", {})
    if attestation.get("environment") != "probe" or attestation.get("role") != "report":
        raise ValueError("role-bound local report attestation required")
    (output / manifest["manifest_path"]).write_text(_canonical(manifest) + "\n")
    return manifest


def validate_training_inputs(
    manifest_path: Path, *, snapshot_root: Path | None = None
) -> dict:
    key = _report_key()
    manifest = _json(manifest_path)
    if (
        manifest.get("schema_version") != SCHEMA
        or manifest.get("profile_id") != PROFILE
        or manifest.get("transform_revision") != TRANSFORM
        or manifest.get("production_eligible") is not False
        or manifest.get("local_training_eligible") is not True
        or manifest.get("legacy_release_eligible") is not False
        or manifest.get("trust_scope") != "local_probe"
        or manifest.get("attestation", {}).get("scheme") != "hmac-sha256-v2"
        or manifest.get("attestation", {}).get("role") != "report"
        or manifest.get("attestation", {}).get("environment") != "probe"
        or not verify_attestation(manifest, key, purpose="training_export_manifest")
    ):
        raise ValueError("invalid signed local taskbank training manifest")
    if _recipe(Path(manifest["recipe_path"])) != manifest["protected_recipe"]:
        raise ValueError("protected recipe changed")
    if _training_code_bindings(Path(manifest["recipe_path"])) != manifest.get(
        "training_code_bindings"
    ):
        raise ValueError("training stage code or recipe changed")
    for entry in manifest["input_bindings"]:
        path = Path(entry["path"])
        if _digest(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("upstream input binding changed")
    worlds, bindings = _collect(
        Path(manifest["catalog_path"]), Path(manifest["batch_root"])
    )
    if bindings != manifest["input_bindings"]:
        raise ValueError("upstream binding coverage changed")
    root = manifest_path.absolute().parent
    if manifest["manifest_path"] != manifest_path.name or {
        e["path"] for e in manifest["outputs"]
    } != set(OUTPUT_NAMES):
        raise ValueError("invalid output inventory")
    for entry in manifest["outputs"]:
        path = _member(root, entry["path"])
        if _digest(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("training output hash changed")
    counts = {"train": 0, "eval": 0}
    for split in counts:
        expected = (
            sample
            for w in worlds
            if w["config"]["split"] == split
            for sample in _rows(w)
        )
        with _member(root, split + ".jsonl").open() as handle:
            for source, line in zip_longest(expected, handle):
                if source is None or line is None or line != _canonical(source) + "\n":
                    raise ValueError(
                        "training output is not the exact validated taskbank projection"
                    )
                counts[split] += 1
    if (
        counts != manifest["counts"]
        or _json(root / "dataset_info.json") != _dataset_info()
    ):
        raise ValueError("training count/dataset mapping mismatch")
    snapshot = (
        materialize_verified_snapshot(manifest_path, manifest, snapshot_root)
        if snapshot_root is not None
        else None
    )
    return {
        "ok": True,
        "profile_id": PROFILE,
        "counts": counts,
        "local_training_eligible": True,
        "production_eligible": False,
        "framework_preprocessing_verified": False,
        "snapshot_dir": str(snapshot) if snapshot is not None else None,
    }
