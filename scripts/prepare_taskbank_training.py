#!/usr/bin/env python3
"""Prepare or validate signed local taskbank training inputs; never launch a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.taskbank_training import (
    prepare_training_inputs,
    validate_training_inputs,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--batch-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument(
        "--recipe", type=Path, default=ROOT / "configs/llamafactory/TASKBANK.yaml"
    )
    prepare.add_argument("--snapshot-root", type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--snapshot-root", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_training_inputs(args.catalog, args.batch_root, args.output, args.recipe)
        manifest = args.output / "TASKBANK_TRAINING_MANIFEST.json"
    else:
        manifest = args.manifest
    result = validate_training_inputs(manifest, snapshot_root=args.snapshot_root)
    print(json.dumps({"manifest": str(manifest.absolute()), **result}, indent=2))


if __name__ == "__main__":
    main()
