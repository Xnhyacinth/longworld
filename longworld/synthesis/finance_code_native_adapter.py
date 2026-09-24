"""Run pinned Finance and CodeForge compilers under one batch control plane.

This adapter does not reinterpret either native oracle. It resolves local trust
paths, materializes native outputs, and checks the persisted receipts and rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import run_finance_taskbank_v2_batch as finance_native

ROOT = Path(__file__).resolve().parents[2]
_TRUST_PREFIX = "${QJIU_ROOT}/"


def _digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or non-regular pinned file: {path}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _pinned(path: str, expected: str) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe pinned source path: {path}")
    # Frozen source inventories may be symlinks to the project object store.
    # The digest, rather than the resolved location, is the content authority.
    target = (ROOT / relative).resolve(strict=True)
    if _digest(target) != expected:
        raise ValueError(f"pinned source changed: {path}")
    return target


def _trust_path(value: str) -> Path:
    if value.startswith(_TRUST_PREFIX):
        root = os.environ.get("QJIU_ROOT")
        if not root:
            raise ValueError("QJIU_ROOT is required for local probe trust")
        value = str(Path(root) / value[len(_TRUST_PREFIX) :])
    elif "${" in value:
        raise ValueError("unsupported trust path variable")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("local probe trust file is missing or unsafe")
    return path


def _write_effective(path: Path, payload: dict[str, Any]) -> Path:
    raw = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"effective catalog changed: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    return path


def probe_finance(catalog_path: Path) -> dict[str, Any]:
    catalog_path = Path(catalog_path).resolve(strict=True)
    catalog = json.loads(catalog_path.read_text())
    jobs = catalog.get("jobs")
    if (
        catalog.get("schema_version") != "longworld.finance-taskbank-long-catalog.v1"
        or not isinstance(jobs, list)
        or not jobs
    ):
        raise ValueError("invalid Finance catalog")
    names, groups, splits = set(), set(), Counter()
    for job in jobs:
        name = job["issuer"]
        if (
            name in names
            or not name
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name)
        ):
            raise ValueError("duplicate or invalid Finance issuer")
        names.add(name)
        config_path = _pinned(job["config"], job["config_sha256"])
        _pinned(job["source_manifest"], job["source_manifest_sha256"])
        config = json.loads(config_path.read_text())
        if config.get("source_manifest") != job["source_manifest"]:
            raise ValueError("Finance source/config binding mismatch")
        group, split = config["split_group_id"], config["split"]
        if group in groups or split not in ("train", "eval"):
            raise ValueError("Finance source group is repeated or split is invalid")
        groups.add(group)
        splits[split] += 1
        _trust_path(job["trust_file"])
    return {
        "source_kind": "real_finance",
        "catalog_sha256": _digest(catalog_path),
        "jobs": len(jobs),
        "source_groups": len(groups),
        "split_groups": dict(splits),
    }


def verify_finance(output: Path, catalog_path: Path | None = None) -> dict[str, Any]:
    output = Path(output).resolve(strict=True)
    batch = json.loads((output / "BATCH_RECEIPT.json").read_text())
    if batch.get(
        "schema_version"
    ) != "longworld.finance-taskbank-long-batch-receipt.v1" or batch.get(
        "statuses", {}
    ).get("blocked"):
        raise ValueError("Finance native batch is incomplete")
    effective = output.with_name(output.name + ".effective_catalog.json")
    if batch.get("catalog_sha256") != _digest(effective):
        raise ValueError("Finance effective catalog binding changed")
    raw = Path(catalog_path).resolve(strict=True) if catalog_path is not None else None
    if raw is not None and raw != effective:
        probe_finance(raw)
        expected = json.loads(raw.read_text())
        for job in expected["jobs"]:
            job["trust_file"] = str(_trust_path(job["trust_file"]))
        if json.loads(effective.read_text()) != expected:
            raise ValueError("Finance input catalog differs from effective catalog")
    pinned_jobs = {
        job["issuer"]: job for job in json.loads(effective.read_text())["jobs"]
    }
    if len(pinned_jobs) != len(batch["jobs"]):
        raise ValueError("Finance issuer inventory changed")
    semantic_ids, sample_ids, groups = set(), set(), {}
    views, families = 0, Counter()
    for job in batch["jobs"]:
        if job.get("status") != "verified_local_candidates":
            raise ValueError("Finance issuer was not natively verified")
        pin = pinned_jobs.get(job["name"])
        if pin is None:
            raise ValueError("Finance issuer is absent from effective catalog")
        directory = output / job["name"]
        receipt_path = directory / "BUILD_RECEIPT.json"
        if _digest(receipt_path) != job["receipt_sha256"]:
            raise ValueError("Finance issuer receipt changed")
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt["config_sha256"] != pin["config_sha256"]
            or receipt["source_manifest"]["sha256"] != pin["source_manifest_sha256"]
        ):
            raise ValueError("Finance issuer source/config pin changed")
        for relative, digest in receipt["files"].items():
            if (
                Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or _digest(directory / relative) != digest
            ):
                raise ValueError("Finance native output binding changed")
        source_path = Path(receipt["source_manifest"]["path"])
        if (
            _digest(source_path.resolve(strict=True))
            != receipt["source_manifest"]["sha256"]
        ):
            raise ValueError("Finance source manifest changed")
        split, group = receipt["split"], receipt["split_group_id"]
        if split not in ("train", "eval") or group in groups:
            raise ValueError("Finance source group crosses issuer or split")
        groups[group] = split
        task_sample_ids: set[str] = set()
        task_count = 0
        for task in _rows(directory / "tasks.jsonl"):
            task_count += 1
            identifier = task["semantic_task_id"]
            if (
                identifier in semantic_ids
                or task["split"] != split
                or task["split_group_id"] != group
                or task["task_spec"]["semantic_task_id"] != identifier
            ):
                raise ValueError("Finance semantic task duplicated")
            semantic_ids.add(identifier)
            if task["sample_id"] in task_sample_ids:
                raise ValueError("Finance task sample duplicated")
            task_sample_ids.add(task["sample_id"])
            families[task["task_spec"]["family"]] += 1
        view_count = 0
        view_sample_ids: set[str] = set()
        for view in _rows(directory / "sft_candidates.jsonl"):
            view_count += 1
            identifier = view["sample_id"]
            messages = view.get("messages")
            if (
                identifier in sample_ids
                or identifier not in task_sample_ids
                or not isinstance(messages, list)
                or [m.get("role") for m in messages] != ["user", "assistant"]
                or not all(
                    isinstance(m.get("content"), str) and m["content"] for m in messages
                )
            ):
                raise ValueError("Finance sample duplicate or empty")
            sample_ids.add(identifier)
            view_sample_ids.add(identifier)
        if (
            task_count != receipt["accepted_semantic_tasks"]
            or view_count != receipt["training_views"]
            or task_sample_ids != view_sample_ids
        ):
            raise ValueError("Finance task and reader view coverage differs")
        views += view_count
    if len(semantic_ids) != batch["accepted_semantic_tasks"]:
        raise ValueError("Finance batch semantic count changed")
    result = {
        "source_kind": "real_finance",
        "status": "verified_native_candidate",
        "semantic_tasks": len(semantic_ids),
        "rows": views,
        "source_groups": len(groups),
        "families": dict(families),
        "train_ready": False,
        "paths": {
            "batch_receipt": str(output / "BATCH_RECEIPT.json"),
            "root": str(output),
            "effective_catalog": str(effective),
            "issuers": {
                job["name"]: {
                    "tasks": str(output / job["name"] / "tasks.jsonl"),
                    "rows": str(output / job["name"] / "sft_candidates.jsonl"),
                    "receipt": str(output / job["name"] / "BUILD_RECEIPT.json"),
                }
                for job in batch["jobs"]
            },
        },
    }
    if raw is not None and raw != effective:
        result["input_catalog_sha256"] = _digest(raw)
    result["effective_catalog_sha256"] = _digest(effective)
    return result


def run_finance(
    catalog_path: Path, output: Path, *, workers: int = 2
) -> dict[str, Any]:
    catalog_path = Path(catalog_path).resolve(strict=True)
    probe = probe_finance(catalog_path)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("Finance output already exists")
    catalog = json.loads(catalog_path.read_text())
    for job in catalog["jobs"]:
        job["trust_file"] = str(_trust_path(job["trust_file"]))
    effective = _write_effective(
        output.with_name(output.name + ".effective_catalog.json"), catalog
    )
    batch = finance_native.run_batch(effective, output, workers)
    if batch.get("statuses", {}).get("blocked"):
        raise ValueError(
            f"Finance native batch blocked; inspect {output / 'BATCH_RECEIPT.json'}"
        )
    result = verify_finance(output, catalog_path)
    if result["input_catalog_sha256"] != probe["catalog_sha256"]:
        raise ValueError("Finance input catalog changed during build")
    return result


def probe_codeforge(catalog_path: Path) -> dict[str, Any]:
    catalog_path = Path(catalog_path).resolve(strict=True)
    catalog = json.loads(catalog_path.read_text())
    jobs = catalog.get("jobs")
    if (
        catalog.get("schema_version") != "longworld.codeforge-taskbank-catalog.v1"
        or not isinstance(jobs, list)
        or not jobs
    ):
        raise ValueError("invalid CodeForge catalog")
    _trust_path(catalog["trust_file"])
    names, groups, splits = set(), set(), Counter()
    for job in jobs:
        name = job["repository"]
        if (
            name in names
            or not name
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name)
        ):
            raise ValueError("duplicate or invalid CodeForge repository")
        names.add(name)
        config_path = ROOT / job["config"]
        if not config_path.is_file():
            raise ValueError("CodeForge config is missing")
        config = json.loads(config_path.read_text())
        if config.get("schema_version") != "longworld.codeforge-taskbank-config.v1":
            raise ValueError("CodeForge config schema mismatch")
        _pinned(config["source_bundle"], config["source_bundle_sha256"])
        group, split = config["source_group_id"], config["split"]
        if group in groups or split not in ("train", "eval"):
            raise ValueError("CodeForge source group is repeated or split is invalid")
        groups.add(group)
        splits[split] += 1
    return {
        "source_kind": "real_code_workflow",
        "catalog_sha256": _digest(catalog_path),
        "jobs": len(jobs),
        "source_groups": len(groups),
        "split_groups": dict(splits),
    }


def _source_command(
    trust: Path, program: Path, *args: str, pin_environment: dict[str, str]
) -> None:
    environment = {**os.environ, **pin_environment}
    command = [
        sys.executable,
        str(ROOT / "scripts/run_with_local_probe_trust.py"),
        "--trust-file",
        str(trust),
        "--role",
        "source",
    ]
    for variable in (
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TOKENIZERS_PARALLELISM",
        "LONGWORLD_PUBLIC_POLICY_SHA256",
        "LONGWORLD_GH_BINARY_SHA256",
    ):
        if variable in environment:
            command.extend(("--pass-env", variable))
    command.extend(("--", sys.executable, str(program), *args))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=7200,
        env=environment,
    )
    if completed.returncode:
        raise ValueError(
            f"native {program.name} failed ({completed.returncode}): "
            + completed.stderr[-2000:]
        )
    if '"status": "PASS"' not in completed.stdout:
        raise ValueError(f"native {program.name} did not report PASS")


def verify_codeforge(output: Path, catalog_path: Path | None = None) -> dict[str, Any]:
    output = Path(output).resolve(strict=True)
    receipt = json.loads((output / "BUILD_RECEIPT.json").read_text())
    if receipt.get("schema_version") != "longworld.codeforge-taskbank-sft-build.v1":
        raise ValueError("CodeForge SFT receipt schema mismatch")
    effective = output.with_name(output.name + ".effective_catalog.json")
    if Path(receipt["catalog_path"]).resolve() != effective:
        raise ValueError("CodeForge effective catalog binding changed")
    raw = Path(catalog_path).resolve(strict=True) if catalog_path is not None else None
    if raw is not None and raw != effective:
        probe_codeforge(raw)
        original = json.loads(raw.read_text())
        transformed = json.loads(effective.read_text())
        if {k: v for k, v in transformed.items() if k != "jobs"} != {
            k: v for k, v in original.items() if k != "jobs"
        } or len(transformed["jobs"]) != len(original["jobs"]):
            raise ValueError("CodeForge effective catalog differs from input")
        bank_root = output.with_name(output.name + ".native_banks")
        for before, after in zip(original["jobs"], transformed["jobs"], strict=True):
            if {k: v for k, v in after.items() if k != "output"} != {
                k: v for k, v in before.items() if k != "output"
            } or (ROOT / after["output"]).resolve() != bank_root / before["repository"]:
                raise ValueError("CodeForge native bank routing changed")
    for binding in receipt["output_bindings"]:
        if _digest(output / binding["path"]) != binding["sha256"]:
            raise ValueError("CodeForge output binding changed")
    for binding in receipt["input_bindings"]:
        if _digest(Path(binding["path"]).resolve(strict=True)) != binding["sha256"]:
            raise ValueError("CodeForge input binding changed")
    if any(proof.get("status") != "PASS" for proof in receipt["source_validation"]):
        raise ValueError("CodeForge native source validation failed")
    ids, groups, counts = set(), {}, Counter()
    for row in _rows(output / "metadata.jsonl"):
        sample = row["sample_id"]
        group, split = row["source_group_id"], row["split"]
        if sample in ids or split not in ("train", "eval"):
            raise ValueError("CodeForge duplicate sample or invalid split")
        ids.add(sample)
        if groups.setdefault(group, split) != split:
            raise ValueError("CodeForge source group crosses split")
        if row["classification"] != "rejected_overflow":
            counts[row["output_file"]] += 1
    for name in ("train.jsonl", "eval.jsonl", "short.jsonl"):
        if counts[name] != sum(1 for _ in _rows(output / name)):
            raise ValueError("CodeForge metadata row count mismatch")
    if len(ids) != receipt["canonical_source_tasks"]:
        raise ValueError("CodeForge semantic task count mismatch")
    result = {
        "source_kind": "real_code_workflow",
        "status": "verified_native_candidate",
        "semantic_tasks": len(ids),
        "rows": counts["train.jsonl"] + counts["eval.jsonl"] + counts["short.jsonl"],
        "long_rows": counts["train.jsonl"] + counts["eval.jsonl"],
        "source_groups": len(groups),
        "families": dict(
            Counter(row["program_id"] for row in _rows(output / "metadata.jsonl"))
        ),
        "train_ready": False,
        "paths": {
            "root": str(output),
            "receipt": str(output / "BUILD_RECEIPT.json"),
            "metadata": str(output / "metadata.jsonl"),
            "train": str(output / "train.jsonl"),
            "eval": str(output / "eval.jsonl"),
            "short": str(output / "short.jsonl"),
            "effective_catalog": str(effective),
            "native_banks": str(output.with_name(output.name + ".native_banks")),
        },
    }
    if raw is not None and raw != effective:
        result["input_catalog_sha256"] = _digest(raw)
    result["effective_catalog_sha256"] = _digest(effective)
    return result


def run_codeforge(catalog_path: Path, output: Path) -> dict[str, Any]:
    catalog_path = Path(catalog_path).resolve(strict=True)
    probe = probe_codeforge(catalog_path)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("CodeForge output already exists")
    catalog = json.loads(catalog_path.read_text())
    trust = _trust_path(catalog["trust_file"])
    pins = {
        "LONGWORLD_PUBLIC_POLICY_SHA256": ",".join(catalog["approved_policy_sha256"]),
        "LONGWORLD_GH_BINARY_SHA256": catalog["source_client_sha256"],
    }
    bank_root = output.with_name(output.name + ".native_banks")
    if bank_root.exists() and any(bank_root.iterdir()):
        raise ValueError("CodeForge native bank directory already contains data")
    # Native bank paths are stored relative to ROOT in the projection catalog.
    if not bank_root.is_relative_to(ROOT):
        raise ValueError("CodeForge native banks must be inside repository")
    bank_root.mkdir(parents=True, exist_ok=True)
    effective = dict(catalog)
    effective["jobs"] = []
    for job in catalog["jobs"]:
        bank = bank_root / job["repository"]
        argv = (
            "--config",
            str(ROOT / job["config"]),
            "--output",
            str(bank),
        )
        program = ROOT / "scripts/materialize_codeforge_taskbank.py"
        _source_command(trust, program, *argv, pin_environment=pins)
        _source_command(trust, program, *argv, "--validate", pin_environment=pins)
        effective["jobs"].append({**job, "output": str(bank.relative_to(ROOT))})
    effective_path = _write_effective(
        output.with_name(output.name + ".effective_catalog.json"), effective
    )
    _source_command(
        trust,
        ROOT / "scripts/export_codeforge_taskbank_sft.py",
        "--catalog",
        str(effective_path),
        "--output",
        str(output),
        pin_environment=pins,
    )
    result = verify_codeforge(output, catalog_path)
    if result["input_catalog_sha256"] != probe["catalog_sha256"]:
        raise ValueError("CodeForge input catalog changed during build")
    return result
