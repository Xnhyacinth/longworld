"""Verify a P108 episode replay bundle under the existing source-role trust."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from longworld.core.realworkflow import load_episode_replay_bundle


def verify(path: Path) -> dict:
    workflows = load_episode_replay_bundle(path)
    groups = {workflow.lineage.url for workflow in workflows}
    if len(groups) != 1:
        raise ValueError("P108 bundle crosses repository source groups")
    raw = path.read_bytes()
    payload = json.loads(raw)
    return {
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "repository_url": next(iter(groups)),
        "episode_count": len(workflows),
        "source_role_key_id": payload["attestation"].get("key_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
