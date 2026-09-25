"""Discover and freeze bounded Wiki source groups from a query vocabulary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import repartition, run, verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--repartition-from", type=Path)
    args = parser.parse_args(argv)
    if not args.verify_only and args.config is None:
        parser.error("--config is required for acquisition")
    if args.verify_only:
        result = verify(args.output_dir, ROOT)
    elif args.repartition_from:
        result = repartition(args.config, args.repartition_from, args.output_dir, ROOT)
    else:
        result = run(args.config, args.output_dir, ROOT)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
