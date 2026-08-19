"""ResearchLab world: real scientific workflow schema, synthetic instance.

Motifs (independent topologies, not clones of company v2→v3):
  supersession     — release note adopts rerun JSON, not camera-ready prose
  fork_join        — stale cache AND wrong split jointly explain inflation
  delayed_effect   — February tokenizer commit only matters after May rerun
  contradiction    — camera-ready body vs table vs release authority
  counterfactual   — if the Issue is never filed, authoritative stays v1
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


def sample_lab_spec(
    seed: int, n_parallel: int = 2, n_pulses: int = 10
) -> dict[str, Any]:
    rng = random.Random(seed)
    used: set[str] = set()
    start = date(2026, 1, 8) + timedelta(days=rng.randrange(0, 10))

    def one(kind: str, is_focal: bool) -> dict[str, Any]:
        stem = rng.choice(STEMS)
        tag = rng.randint(10, 99)
        paper = f"{stem}Bench-{tag}"
        model = f"{stem[:3].upper()}-Net-{rng.randint(11, 29)}"
        bench = f"{stem}X-{rng.randint(2, 9)}"
        v1 = round(
            80.0 + rng.randint(11, 39) / 10.0 + rng.choice([0.03, 0.07, 0.09]), 2
        )
        final = round(
            v1 - rng.randint(18, 41) / 10.0 - rng.choice([0.02, 0.04, 0.08]), 2
        )
        if final >= v1:
            final = round(v1 - 2.17, 2)
        cause = f"CACHE{rng.randint(10, 99)}+SPLIT{rng.randint(10, 99)}"
        commit = f"{rng.randint(0x100000, 0xEFFFFF):06x}"
        spdx = f"Apache-2.0-L{rng.randint(1000, 9999)}"
        return {
            "kind": kind,
            "is_focal": is_focal,
            "paper": paper,
            "model": model,
            "benchmark": bench,
            "v1_score": v1,
            "final_score": final,
            "cause_token": cause,
            "commit_hash": commit,
            "spdx": spdx,
            "pi": _person(rng, used),
            "student": _person(rng, used),
            "reviewer": _person(rng, used),
            "lab": f"{stem} Lab",
            "n_pulses": n_pulses,
            "start": start.isoformat(),
        }

    focal = one("focal", True)
    parallels = [one("parallel", False) for _ in range(n_parallel)]
    world_id = f"lab{seed:06d}-{focal['paper'].lower()}"
    return {
        "world_id": world_id,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "domain": "researchlab",
        "truth_regime": "real_schema_synthetic_instance",
        "n_pulses": n_pulses,
        "start": start.isoformat(),
        "focal": focal,
        "parallels": parallels,
        "n_parallel": n_parallel,
        "project": focal,
    }
