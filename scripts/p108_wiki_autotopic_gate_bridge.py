"""Bind a P97 name-list gate to P106's count-based gate interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longworld.synthesis.p93_wiki_structural_intake import pinned_json, sha

SCHEMA = "longworld.p108-wiki-autotopic-gate-bridge.v1"


def build(config_path: Path, output: Path, *, verify_only: bool = False) -> dict:
    config = json.loads(config_path.read_text())
    if config.get("schema") != SCHEMA or set(config) != {
        "schema",
        "upstream_gate",
        "source_pool",
    }:
        raise ValueError("P108 gate bridge config differs")
    gate = pinned_json(ROOT, config["upstream_gate"])
    pool = pinned_json(ROOT, config["source_pool"])
    names = [source["name"] for source in pool["sources"]]
    if (
        gate.get("schema") != "longworld.p97-wiki-delta-gate.v1.result"
        or pool.get("schema") != "longworld.source-batch-pool.v2"
        or gate.get("accepted_groups") != names
        or gate.get("gated_source_pool_sha256") != config["source_pool"]["sha256"]
        or gate.get("net_novel", {}).get("groups") != len(names)
        or not gate.get("prior_router", {}).get("sha256")
        or len(names) != len(set(names))
    ):
        raise ValueError("P108 upstream gate/pool identity differs")
    receipt = {
        "schema": SCHEMA + ".result",
        "config_sha256": sha(config_path),
        "upstream_gate": config["upstream_gate"],
        "source_pool": config["source_pool"],
        "accepted_groups": len(names),
        "accepted_source_names_sha256": hashlib.sha256(
            ("\n".join(names) + "\n").encode()
        ).hexdigest(),
        "prior_router": gate["prior_router"],
        "train_ready": False,
    }
    content = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if verify_only:
        if output.read_text() != content:
            raise ValueError("P108 gate bridge replay differs")
    else:
        if output.exists():
            raise ValueError("P108 gate bridge output must be new")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.config, args.output, verify_only=args.verify_only),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
