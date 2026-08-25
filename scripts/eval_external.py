#!/usr/bin/env python3
"""Preflight or run pinned external long-context benchmark commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.core.external_eval import execute_external_eval_suite


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}."
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=86_400)
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    receipt = execute_external_eval_suite(
        suite,
        model_id=args.model_id,
        model_revision=args.model_revision,
        output_dir=args.output_dir,
        execute=args.execute,
        require_complete=not args.allow_partial,
        timeout_seconds=args.timeout_seconds,
    )
    _write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
