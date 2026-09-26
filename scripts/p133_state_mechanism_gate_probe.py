"""Replay P133 per-world candidate gates without promoting partial worlds."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import p133_state_mechanism_batch as batch


def probe(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = batch._config(config_path)
    with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
        worlds = list(pool.map(batch._build_job, batch._jobs(config)))
    with tempfile.TemporaryDirectory(
        prefix="p133-gate-probe-", dir=output.parent
    ) as raw:
        temporary = Path(raw)
        paths = []
        for world in worlds:
            shard = temporary / "worlds" / world["world_id"]
            shard.mkdir(parents=True)
            path = shard / "world.json"
            path.write_text(batch.dump(world) + "\n")
            (shard / "receipt.json").write_text(
                batch.dump(
                    {
                        "world_id": world["world_id"],
                        "world_sha256": batch.file_sha(path),
                    }
                )
                + "\n"
            )
            paths.append(path)
        with ProcessPoolExecutor(max_workers=config["workers"]) as pool:
            results = list(
                pool.map(batch._compile_world, ((str(path), config) for path in paths))
            )
        attempts = sorted(
            (row for _, ledger in results for row in ledger),
            key=lambda row: (
                row["world_id"],
                row["task_id"],
                row["lane"],
                row.get("sign", 0),
            ),
        )
        rejected_by_world = defaultdict(list)
        for row in attempts:
            if row["status"] == "rejected":
                rejected_by_world[row["world_id"]].append(row)
        report = {
            "compiler_sha256": batch.file_sha(Path(batch.__file__)),
            "config_sha256": batch.file_sha(config_path),
            "gross_worlds": len(worlds),
            "gross_task_attempts": len(attempts),
            "worlds_with_rejections": len(rejected_by_world),
            "complete_worlds": len(worlds) - len(rejected_by_world),
            "rejected_task_attempts": sum(
                len(rows) for rows in rejected_by_world.values()
            ),
            "rejection_reasons": dict(
                sorted(
                    Counter(
                        row["reason"]
                        for rows in rejected_by_world.values()
                        for row in rows
                    ).items()
                )
            ),
            "rejected_worlds": [
                {
                    "world_id": world["world_id"],
                    "seed": world["seed"],
                    "mechanism": world["mechanism"],
                    "length_records": world["length_records"],
                    "rejections": rejected_by_world[world["world_id"]],
                }
                for world in worlds
                if world["world_id"] in rejected_by_world
            ],
            "claim_limit": "diagnostic only; no partial source world admitted",
        }
        data = {
            "report.json": batch.dump(report) + "\n",
            "attempt_ledger.jsonl": "".join(batch.dump(row) + "\n" for row in attempts),
        }
        if verify_only:
            for name, content in data.items():
                if (output / name).read_text() != content:
                    raise ValueError(f"frozen P133 gate probe differs: {name}")
        else:
            if output.exists():
                raise ValueError("P133 gate probe output exists")
            output.mkdir(parents=True)
            for name, content in data.items():
                (output / name).write_text(content)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            probe(args.config, args.output, verify_only=args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
