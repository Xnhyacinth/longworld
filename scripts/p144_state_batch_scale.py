"""Run bounded P133 world batches as immutable, parallel candidate shards.

Each batch keeps the existing P133 semantic gates and four-process compiler.
The wrapper only schedules disjoint seed windows and checks cross-batch source
identity; it does not promote the output to training data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "longworld.p144-state-batch-scale.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _pinned_path(pin: dict[str, str]) -> Path:
    relative = Path(pin["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source pin must be workspace-relative")
    path = ROOT / relative
    if _sha(path) != pin["sha256"]:
        raise ValueError(f"source pin differs: {relative}")
    return path


def _plan(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if (
        config.get("schema_version") != SCHEMA
        or type(config.get("batch_count")) is not int
        or not 1 <= config["batch_count"] <= 64
        or type(config.get("seeds_per_cell")) is not int
        or not 1 <= config["seeds_per_cell"] <= 32
        or type(config.get("seed_base")) is not int
        or config["seed_base"] < 1_000_000
        or type(config.get("batch_workers")) is not int
        or not 1 <= config["batch_workers"] <= 4
    ):
        raise ValueError("invalid P144 schedule")
    template_path = _pinned_path(config["template"])
    prior_path = _pinned_path(config["prior_campaign"])
    template = json.loads(template_path.read_text())
    prior = json.loads(prior_path.read_text())
    if (
        template.get("schema_version")
        != "longworld.p133-state-mechanism-batch.v1"
        or prior.get("schema_version")
        != "longworld.p133-state-mechanism-output.v1"
    ):
        raise ValueError("P144 template or prior campaign schema differs")
    stride = (
        len(template["mechanisms"])
        * len(template["length_records"])
        * config["seeds_per_cell"]
    )
    prior_pin = config["prior_campaign"]
    if prior_pin in template["prior_world_sets"]:
        raise ValueError("P144 prior campaign repeats in template")
    batches = []
    for index in range(config["batch_count"]):
        generated = {
            **template,
            "seeds_per_cell": config["seeds_per_cell"],
            "seed_base": config["seed_base"] + index * stride,
            "prior_world_sets": [*template["prior_world_sets"], prior_pin],
        }
        batches.append(generated)
    return batches, {
        "template_sha256": _sha(template_path),
        "prior_campaign_sha256": _sha(prior_path),
        "seed_stride": stride,
    }


def _run_command(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"P144 subprocess failed (exit {result.returncode}): {' '.join(args)}\n"
            + result.stderr[-5000:]
        )


def _run_batch(
    index: int, batch_config: dict[str, Any], output: Path, verify_only: bool
) -> dict[str, Any]:
    batch_dir = output / f"batch_{index:03d}"
    if not verify_only:
        batch_dir.mkdir(parents=True, exist_ok=True)
    config_path = batch_dir / "campaign_config.json"
    expected_config = _dump(batch_config)
    if config_path.exists():
        if config_path.read_text() != expected_config:
            raise ValueError(f"P144 batch {index} config changed")
    elif verify_only:
        raise ValueError(f"P144 batch {index} config missing")
    else:
        config_path.write_text(expected_config)
    campaign_dir = batch_dir / "campaign"
    if verify_only and not campaign_dir.is_dir():
        raise ValueError(f"P144 batch {index} campaign missing")
    campaign_args = (
        "scripts/p133_state_mechanism_batch.py",
        "--config", str(config_path), "--output", str(campaign_dir),
    )
    _run_command(*campaign_args, *(["--verify-only"] if campaign_dir.exists() else []))
    campaign_manifest_path = campaign_dir / "manifest.json"
    campaign_manifest = json.loads(campaign_manifest_path.read_text())
    adapter_config = {
        "schema_version": "longworld.p133-reader-unified-adapter.v1",
        "campaign_manifest": str(campaign_manifest_path.relative_to(ROOT)),
        "campaign_manifest_sha256": _sha(campaign_manifest_path),
    }
    adapter_config_path = batch_dir / "reader_config.json"
    expected_adapter = _dump(adapter_config)
    if adapter_config_path.exists():
        if adapter_config_path.read_text() != expected_adapter:
            raise ValueError(f"P144 batch {index} adapter config changed")
    elif verify_only:
        raise ValueError(f"P144 batch {index} adapter config missing")
    else:
        adapter_config_path.write_text(expected_adapter)
    reader_dir = batch_dir / "reader"
    if verify_only and not reader_dir.is_dir():
        raise ValueError(f"P144 batch {index} reader missing")
    reader_args = (
        "scripts/p133_reader_unified_adapter.py",
        "--config", str(adapter_config_path), "--output", str(reader_dir),
    )
    _run_command(*reader_args, *(["--verify-only"] if reader_dir.exists() else []))
    reader_manifest = json.loads((reader_dir / "manifest.json").read_text())
    if reader_manifest["candidate_views"] != campaign_manifest["qa_views"]:
        raise ValueError("P144 campaign/reader QA counts differ")
    return {
        "batch": index,
        "seed_base": batch_config["seed_base"],
        "config_sha256": _sha(config_path),
        "campaign_manifest_sha256": _sha(campaign_manifest_path),
        "reader_manifest_sha256": _sha(reader_dir / "manifest.json"),
        "gross_source_worlds": campaign_manifest["gross_source_worlds"],
        "admitted_source_worlds": campaign_manifest["source_worlds"],
        "qa_views": campaign_manifest["qa_views"],
        "policy_views": campaign_manifest["policy_views"],
        "full_chat_tokens": campaign_manifest["full_chat_tokens"],
        "supervised_tokens": campaign_manifest["supervised_tokens"],
        "reader_dir": str(reader_dir.relative_to(ROOT)),
    }


def _audit_worlds(output: Path, batches: list[dict[str, Any]]) -> dict[str, int]:
    seeds: set[int] = set()
    world_ids: set[str] = set()
    base_hashes: set[str] = set()
    sample_ids: set[str] = set()
    for batch in batches:
        batch_dir = output / f"batch_{batch['batch']:03d}"
        campaign_dir = batch_dir / "campaign"
        campaign = json.loads((campaign_dir / "manifest.json").read_text())
        world_paths = sorted(campaign_dir.glob("worlds/*/world.json"))
        if len(world_paths) != batch["gross_source_worlds"]:
            raise ValueError("P144 gross world inventory differs")
        for world_path in world_paths:
            relative = str(world_path.relative_to(campaign_dir))
            if _sha(world_path) != campaign["world_files_sha256"][relative]:
                raise ValueError("P144 world source bytes differ")
            world = json.loads(world_path.read_text())
            seed = world["seed"]
            world_id = world["world_id"]
            base_hash = world["base_record_context_sha256"]
            if seed in seeds or world_id in world_ids or base_hash in base_hashes:
                raise ValueError("P144 cross-batch source identity repeats")
            seeds.add(seed)
            world_ids.add(world_id)
            base_hashes.add(base_hash)
        reader_dir = batch_dir / "reader"
        rows = [
            json.loads(line)
            for line in (reader_dir / "sample_index.jsonl").read_text().splitlines()
        ]
        if len(rows) != batch["qa_views"]:
            raise ValueError("P144 reader inventory differs")
        for row in rows:
            if row["sample_id"] in sample_ids:
                raise ValueError("P144 reader sample ID repeats")
            sample_ids.add(row["sample_id"])
    return {
        "gross_worlds": len(world_ids),
        "unique_seeds": len(seeds),
        "unique_base_record_contexts": len(base_hashes),
        "unique_reader_tasks": len(sample_ids),
    }


def run(config_path: Path, output: Path, *, verify_only: bool = False) -> dict[str, Any]:
    if not output.is_absolute():
        output = ROOT / output
    if not output.is_relative_to(ROOT) or ".." in output.parts:
        raise ValueError("P144 output must be inside workspace")
    config = json.loads(config_path.read_text())
    batch_configs, pins = _plan(config)
    if not verify_only:
        output.mkdir(parents=True, exist_ok=True)
    elif not output.is_dir():
        raise ValueError("P144 frozen output missing")
    with ThreadPoolExecutor(max_workers=config["batch_workers"]) as pool:
        futures = [
            pool.submit(_run_batch, index, item, output, verify_only)
            for index, item in enumerate(batch_configs)
        ]
        batches = [future.result() for future in futures]
    identity = _audit_worlds(output, batches)
    manifest = {
        "schema_version": SCHEMA + ".output",
        "config_sha256": _sha(config_path),
        "compiler_sha256": _sha(Path(__file__)),
        **pins,
        "batches": batches,
        "source_identity": identity,
        "candidate_views": sum(batch["qa_views"] for batch in batches),
        "policy_views_separate": sum(batch["policy_views"] for batch in batches),
        "full_chat_tokens_both_contracts": sum(
            batch["full_chat_tokens"] for batch in batches
        ),
        "supervised_tokens_both_contracts": sum(
            batch["supervised_tokens"] for batch in batches
        ),
        "claim_limit": "two existing controlled mechanisms; per-world event dependency gates; no new real domain, multi-turn trajectory, global shortest proof or model-gain claim",
        "train_ready": False,
    }
    expected = _dump(manifest)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if manifest_path.read_text() != expected:
            raise ValueError("P144 aggregate manifest differs")
    elif verify_only:
        raise ValueError("P144 aggregate manifest missing")
    else:
        manifest_path.write_text(expected)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = run(args.config, args.output, verify_only=args.verify_only)
    print(_dump({
        "batches": len(result["batches"]),
        "candidate_views": result["candidate_views"],
        "gross_worlds": result["source_identity"]["gross_worlds"],
        "policy_views_separate": result["policy_views_separate"],
        "train_ready": result["train_ready"],
    }))


if __name__ == "__main__":
    main()
