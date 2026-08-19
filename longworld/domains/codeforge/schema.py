"""CodeForge world: repository workflow with independent proof motifs.

Motifs (not clones of company v2→v3 or lab leaderboard supersession):
  supersession     — release tag adopts HEAD; changelog is not controlling
  fork_join        — CI flake token AND issue fail token jointly name the fault
  delayed_effect   — broken commit hash only matters after CI ran
  contradiction    — changelog quotes the broken hash; tag adopts hotfix HEAD
  hidden_bridge    — early SPDX becomes the shipping license only at tag time
  counterfactual   — if the issue is never filed, HEAD stays the broken commit
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from longworld.domains.company.names import FIRST, LAST, STEMS

SCHEMA_VERSION = "p1.1"


def _person(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in used:
            used.add(name)
            return name


def _hex(rng: random.Random, n: int = 7) -> str:
    return f"{rng.randint(0x1000000, 0xEFFFFFF):07x}"[:n]


def sample_code_spec(
    seed: int, n_parallel: int = 2, n_pulses: int = 10
) -> dict[str, Any]:
    rng = random.Random(seed)
    used: set[str] = set()
    start = date(2026, 1, 8) + timedelta(days=rng.randrange(0, 10))

    def one(kind: str, is_focal: bool) -> dict[str, Any]:
        stem = rng.choice(STEMS)
        tag = rng.randint(10, 99)
        repo = f"{stem}kit-{tag}"
        broken = _hex(rng)
        hotfix = _hex(rng)
        while hotfix == broken:
            hotfix = _hex(rng)
        return {
            "kind": kind,
            "is_focal": is_focal,
            "repo": repo,
            "package": f"{stem[:3].lower()}lib",
            "test_name": f"test_{stem.lower()}_q{rng.randint(3, 19)}",
            "broken_hash": broken,
            "hotfix_hash": hotfix,
            "fail_token": f"FAIL{rng.randint(10, 99)}",
            "flake_token": f"FLAKE{rng.randint(10, 99)}",
            "spdx": f"Apache-2.0-X{rng.randint(1000, 9999)}",
            "owner": _person(rng, used),
            "reviewer": _person(rng, used),
            "ci": f"{stem}-ci",
            "n_pulses": n_pulses,
            "start": start.isoformat(),
        }

    focal = one("focal", True)
    parallels = [one("parallel", False) for _ in range(n_parallel)]
    world_id = f"code{seed:06d}-{focal['repo'].lower()}"
    return {
        "world_id": world_id,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "domain": "codeforge",
        "truth_regime": "real_schema_synthetic_instance",
        "n_pulses": n_pulses,
        "start": start.isoformat(),
        "focal": focal,
        "parallels": parallels,
        "n_parallel": n_parallel,
        "project": focal,
    }
