"""Build, extend, validate or materialize an immutable candidate shard index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longworld.synthesis.sharded_candidate_bank import (
    build_index,
    materialize,
    verify_index,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "extend"):
        sub = subcommands.add_parser(command)
        sub.add_argument("--output", type=Path, required=True)
        sub.add_argument(
            "--shard", action="append", required=True, metavar="NAME=MERGED_DIR"
        )
        if command == "extend":
            sub.add_argument("--base", type=Path, required=True)
    verify = subcommands.add_parser("verify")
    verify.add_argument("--index", type=Path, required=True)
    verify.add_argument("--full-readers", action="store_true")
    export = subcommands.add_parser("materialize")
    export.add_argument("--index", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"create", "extend"}:
        shards = []
        for value in args.shard:
            name, separator, raw_path = value.partition("=")
            if not separator or not name or not raw_path:
                parser.error("--shard must be NAME=MERGED_DIR")
            shards.append((name, Path(raw_path)))
        result = build_index(
            args.output,
            shards,
            base_index=args.base if args.command == "extend" else None,
        )
    elif args.command == "verify":
        result = verify_index(args.index, full_readers=args.full_readers)
    else:
        result = materialize(args.index, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
