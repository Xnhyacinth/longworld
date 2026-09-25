"""Probe all locally frozen source pools and write a source-capability matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p92_source_router import consolidated_wiki_pool, route, sha


def run(
    config_path: Path, output_dir: Path, *, workers: int, verify_only: bool = False
) -> dict:
    config = json.loads(config_path.read_text())
    result = route(config, ROOT, workers=workers)
    pool = consolidated_wiki_pool(result, config["prior_source_manifest"])
    pool_text = json.dumps(pool, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    result["consolidated_wiki_pool_sha256"] = hashlib.sha256(
        pool_text.encode()
    ).hexdigest()
    result["config_sha256"] = sha(config_path)
    result["router_sha256"] = sha(ROOT / "longworld/synthesis/p92_source_router.py")
    manifest = output_dir / "manifest.json"
    pool_path = output_dir / "consolidated_wiki_pool.json"
    if verify_only:
        if (
            not manifest.is_file()
            or json.loads(manifest.read_text()) != result
            or not pool_path.is_file()
            or pool_path.read_text() != pool_text
        ):
            raise ValueError("source routing receipt drift")
    else:
        if output_dir.exists():
            raise ValueError("output directory already exists")
        output_dir.mkdir(parents=True)
        pool_path.write_text(pool_text)
        manifest.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return {
        key: value
        for key, value in result.items()
        if key != "sources" and key != "input_sha256"
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/p92_source_router_v1.json"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    print(
        json.dumps(
            run(
                args.config,
                args.output_dir,
                workers=args.workers,
                verify_only=args.verify_only,
            ),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
