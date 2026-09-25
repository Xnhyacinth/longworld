"""Materialize all newly signed P108 repository worlds in parallel."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p108_code_catalog import _dump, _lock, _pin, _sha

SCHEMA = "longworld.p108-code-bank.v1"


def _job(job: tuple) -> dict:
    row, output, trust_file, config = job
    repo = row["repository"]
    safe = repo.replace("/", "__")
    bank_config = output / "configs" / f"{safe}.json"
    bank_output = output / "banks" / safe
    bundle = ROOT / row["bundle_path"]
    if row["bundle_status"] != "signed_source_bundle" or not bundle.is_file():
        raise ValueError("P108 bank requires a signed source bundle")
    payload = {
        "schema_version": "longworld.codeforge-taskbank-config.v1",
        "source_bundle": row["bundle_path"],
        "source_bundle_sha256": _sha(bundle),
        "source_group_id": "https://github.com/" + repo,
        "split": row["split"],
        "tokenizer": config["tokenizer"],
    }
    content = _dump(payload)
    bank_config.parent.mkdir(parents=True, exist_ok=True)
    if bank_config.exists():
        if bank_config.read_text() != content:
            raise ValueError("P108 bank config changed after source freeze")
    elif config["verify_only"]:
        raise ValueError("P108 bank config missing on verify-only")
    else:
        bank_config.write_text(content)
    env = os.environ.copy()
    env.update(
        LONGWORLD_PUBLIC_POLICY_SHA256=config["public_policy_sha256"],
        LONGWORLD_GH_BINARY_SHA256=config["source_client_sha256"],
        HF_HOME=config["hf_home"],
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    command = [
        sys.executable,
        str(ROOT / config["trust_wrapper"]["path"]),
        "--trust-file",
        str(trust_file),
        "--role",
        "source",
    ]
    for name in (
        "LONGWORLD_PUBLIC_POLICY_SHA256",
        "LONGWORLD_GH_BINARY_SHA256",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TOKENIZERS_PARALLELISM",
        "HF_HOME",
    ):
        command.extend(("--pass-env", name))
    command.extend(
        (
            "--",
            sys.executable,
            str(ROOT / config["materializer"]["path"]),
            "--config",
            str(bank_config),
            "--output",
            str(bank_output),
        )
    )
    if bank_output.exists():
        command.append("--validate")
    elif config["verify_only"]:
        raise ValueError("P108 bank missing on verify-only")
    result = subprocess.run(
        command, text=True, capture_output=True, check=False, env=env, timeout=1200
    )
    if result.returncode:
        message = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        return {
            "repository": repo,
            "split": row["split"],
            "status": "bank_rejected",
            "reason": message[0][:240],
        }
    receipt = bank_output / "BUILD_RECEIPT.json"
    if not receipt.is_file():
        raise ValueError("P108 bank has no build receipt")
    parsed = json.loads(receipt.read_text())
    if (
        parsed["source_group_id"] != payload["source_group_id"]
        or parsed["split"] != payload["split"]
        or parsed["source_episode_count"] != row["usable_episodes"]
    ):
        raise ValueError("P108 bank source/split/episode count differs")
    return {
        "repository": repo,
        "split": row["split"],
        "status": "bank_verified",
        "bank_root": str(bank_output.relative_to(ROOT)),
        "config_sha256": _sha(bank_config),
        "receipt_sha256": _sha(receipt),
        "source_episodes": parsed["source_episode_count"],
        "eligible_episodes": parsed["eligible_episode_count"],
        "semantic_tasks": parsed["semantic_task_count"],
        "max_exact_context_tokens": parsed["max_exact_context_tokens"],
    }


def run(
    config_path: Path,
    output: Path,
    trust_file: Path,
    *,
    verify_only=False,
    only_repository: str | None = None,
):
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA + ".config" or config.get("workers") != 4:
        raise ValueError("P108 bank config invalid")
    hf_home = Path(config.get("hf_home", ""))
    if not hf_home.is_absolute() or not hf_home.is_dir() or hf_home.is_symlink():
        raise ValueError("P108 pinned HF_HOME cache path unavailable")
    for field in ("trust_wrapper", "materializer", "source_manifest"):
        _pin(config[field])
    source = json.loads((ROOT / config["source_manifest"]["path"]).read_text())
    if (
        source.get("schema") != "longworld.p108-code-export.v1.result"
        or source.get("local_probe_only") is not True
        or source.get("train_ready") is not False
    ):
        raise ValueError("P108 signed source manifest invalid")
    if (
        source["allowlist_sha256"] != config["public_policy_sha256"]
        or source["source_client_sha256"] != config["source_client_sha256"]
    ):
        raise ValueError("P108 source policy/client binding differs")
    output = output if output.is_absolute() else ROOT / output
    if not trust_file.is_absolute() or not trust_file.is_file():
        raise ValueError("P108 source trust file missing")
    with _lock(output):
        jobs = [
            (row, output, trust_file, {**config, "verify_only": verify_only})
            for row in source["by_repository"]
            if row["bundle_status"] == "signed_source_bundle"
        ]
        if len(jobs) != source["signed_new_worlds"] or len(
            {job[0]["repository"] for job in jobs}
        ) != len(jobs):
            raise ValueError("P108 signed world count or repository identity differs")
        if only_repository is not None:
            jobs = [job for job in jobs if job[0]["repository"] == only_repository]
            if len(jobs) != 1:
                raise ValueError("P108 smoke repository is not a signed world")
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_job, jobs))
        manifest = {
            "schema": SCHEMA + ".result",
            "config_sha256": _sha(config_path),
            "source_manifest_sha256": config["source_manifest"]["sha256"],
            "runner_sha256": _sha(Path(__file__).resolve()),
            "only_repository": only_repository,
            "source_worlds_in_manifest": source["signed_new_worlds"],
            "signed_source_worlds": len(jobs),
            "verified_banks": sum(r["status"] == "bank_verified" for r in results),
            "semantic_tasks": sum(r.get("semantic_tasks", 0) for r in results),
            "by_repository": results,
            "local_probe_only": True,
            "train_ready": False,
        }
        receipt = output / "manifest.json"
        content = _dump(manifest)
        if verify_only:
            if receipt.read_text() != content:
                raise ValueError("P108 bank verify-only differs")
        else:
            receipt.write_text(content)
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trust-file", type=Path, required=True)
    parser.add_argument("--only-repository")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.config,
        args.output,
        args.trust_file,
        verify_only=args.verify_only,
        only_repository=args.only_repository,
    )
    print(
        _dump({key: value for key, value in result.items() if key != "by_repository"})
    )
    if result["verified_banks"] != result["signed_source_worlds"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
