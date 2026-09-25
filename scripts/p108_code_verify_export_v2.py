"""Return public source facts and pin comparisons without echoing passed hashes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.p108_code_verify_export import verify


def verify_pins(path: Path, repository: str, pull_number: int) -> dict:
    summary = verify(path, repository, pull_number)
    expected_policy = os.environ.get("LONGWORLD_PUBLIC_POLICY_SHA256", "")
    expected_client = os.environ.get("LONGWORLD_GH_BINARY_SHA256", "")
    if not expected_policy or not expected_client:
        raise ValueError("P108 source policy/client pins are missing")
    return {
        key: value
        for key, value in summary.items()
        if key not in {"public_policy_sha256", "source_client_sha256"}
    } | {
        "public_policy_pin_matches": summary["public_policy_sha256"] == expected_policy,
        "source_client_pin_matches": summary["source_client_sha256"] == expected_client,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull", type=int, required=True)
    args = parser.parse_args()
    print(
        json.dumps(verify_pins(args.path, args.repository, args.pull), sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
