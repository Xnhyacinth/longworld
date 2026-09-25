#!/usr/bin/env python3
"""Pinned, globally paced GitHub CLI client for P108 public-source exports."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GH = Path("/usr/bin/gh")
GH_SHA256 = "2b61ea0d3a5654bbefaf6f75def59d13d37a791a95196764db19414ac50d0524"
RATE_LOCK = ROOT / "data/capability_records/p108_code_sources_v1/.gh_rate_lock"
MIN_INTERVAL_SECONDS = 1.0
ENDPOINT = re.compile(
    r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./?&=%:+-]*)?\Z"
)


def _request(argv: list[str], *, lock_path: Path = RATE_LOCK) -> int:
    if (
        len(argv) != 4
        or argv[:3] != ["api", "--hostname", "github.com"]
        or ENDPOINT.fullmatch(argv[3]) is None
        or GH.resolve() != GH
        or not GH.is_file()
        or hashlib.sha256(GH.read_bytes()).hexdigest() != GH_SHA256
    ):
        raise ValueError("P108 GitHub client request or executable pin invalid")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    child_env = {
        name: os.environ[name]
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR")
        if os.environ.get(name)
    }
    child_env.update(
        PATH="/usr/bin:/bin",
        GH_PROMPT_DISABLED="1",
        GH_NO_UPDATE_NOTIFIER="1",
    )
    with lock_path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.seek(0)
        previous = stream.read().strip()
        last_start = float(json.loads(previous)["last_start"]) if previous else 0.0
        elapsed = time.monotonic() - last_start
        remaining = MIN_INTERVAL_SECONDS - elapsed if elapsed >= 0 else 0.0
        if remaining > 0:
            time.sleep(remaining)
        started = time.monotonic()
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"last_start": started}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        result = subprocess.run(
            [str(GH), *argv],
            env=child_env,
            check=False,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(_request(sys.argv[1:]))
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from error
