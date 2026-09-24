"""Adopt a native shared-record batch without weakening its world-level checks."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from scripts import run_shared_record_taskbank as native

SCHEMA = "longworld.shared-record-plan.v1"
FIELDS = {
    "schema_version",
    "seed_base",
    "worlds",
    "length_records",
    "consumed_records",
    "depth",
    "variants",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    if set(config) != FIELDS or config["schema_version"] != SCHEMA:
        raise ValueError("invalid shared-record plan")
    for key in FIELDS - {"schema_version"}:
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"invalid shared-record {key}")
    return config


def verify_native(config_path: Path, output: Path) -> dict:
    """Bind frozen world seeds, native replay and shard receipts to one lane."""
    config = _plan(config_path)
    if not output.is_dir() or not (output / "manifest.json").is_file():
        raise ValueError("shared-record batch is incomplete")
    verified = native.verify(output, native._tokenizer(), require_manifest=True)
    if verified["worlds"] != config["worlds"] or verified["reader_rows"] != (
        2 * config["worlds"] * config["variants"]
    ):
        raise ValueError("shared-record batch cardinality differs from plan")
    seeds = set()
    receipt_paths = []
    for receipt in sorted((output / "shards").glob("*/receipt.json")):
        world = json.loads((receipt.parent / "world.json").read_text())
        if (
            any(
                world[key] != config[key]
                for key in ("length_records", "consumed_records", "depth")
            )
            or world["n_variants"] != config["variants"]
        ):
            raise ValueError("shared-record world parameters differ from plan")
        seeds.add(world["seed"])
        receipt_paths.append(receipt)
    if seeds != set(range(config["seed_base"], config["seed_base"] + config["worlds"])):
        raise ValueError("shared-record world seeds differ from plan")
    return {
        "source_kind": "controlled_simulation",
        "status": "verified_native_candidate",
        "rows": verified["reader_rows"],
        "semantic_tasks": verified["reader_rows"],
        "worlds": verified["worlds"],
        "operations": verified["operations"],
        "domains": ["simulation"],
        "train_ready": False,
        "paths": {"root": str(output), "manifest": str(output / "manifest.json")},
        "receipt_paths": [str(path) for path in receipt_paths],
        "native_receipt_sha256": _sha(output / "manifest.json"),
    }


def run_native(config_path: Path, output: Path, *, workers: int) -> dict:
    """Build once through the native process pool, then adopt by exact replay."""
    config = _plan(config_path)
    if workers < 1:
        raise ValueError("shared-record workers must be positive")
    if not output.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="shared-record-stage-", dir=output.parent
        ) as raw:
            staged = Path(raw) / "batch"
            native.build(
                staged,
                worlds=config["worlds"],
                seed_base=config["seed_base"],
                length_records=config["length_records"],
                consumed_records=config["consumed_records"],
                depth=config["depth"],
                variants=config["variants"],
                workers=workers,
            )
            verify_native(config_path, staged)
            if output.exists():
                raise ValueError("shared-record output appeared during staging")
            os.rename(staged, output)
    return verify_native(config_path, output)
