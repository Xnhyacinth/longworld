#!/usr/bin/env python3
"""Compile and source-attest the DNSSEC family workflow from the fetched inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.export_ietf_workflow import export_ietf_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export_ietf_workflow(args.fetch_inventory, args.out)


if __name__ == "__main__":
    main()
