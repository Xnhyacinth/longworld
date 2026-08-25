"""ResearchLab world: executable synthetic scientific workflow.

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
from typing import TYPE_CHECKING, Any

from longworld.core.grounded import pick_anchors
from longworld.core.process import assign_register, attach_roles, sample_process
from longworld.domains.company.names import FIRST, LAST, STEMS

SCHEMA_VERSION = "p4.0"

if TYPE_CHECKING:
    from longworld.core.sourceworkflow import SourceWorkflow


def _person(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in used:
            used.add(name)
            return name


def sample_lab_spec(
    seed: int,
    n_parallel: int = 2,
    n_pulses: int = 0,
    n_workstreams: int = 0,
    source_workflows: list[SourceWorkflow] | None = None,
) -> dict[str, Any]:
    if not isinstance(n_workstreams, int) or isinstance(n_workstreams, bool):
        raise TypeError("n_workstreams must be an integer")
    if not 0 <= n_workstreams <= 128:
        raise ValueError("n_workstreams must be between 0 and 128")
    workflows = list(source_workflows or [])
    if any(
        workflow.target_domain != "researchlab"
        or workflow.source_kind != "paper_workflow"
        for workflow in workflows
    ):
        raise ValueError("researchlab requires paper source workflows")
    rng = random.Random(seed)
    used: set[str] = set()
    start = date(2026, 1, 8) + timedelta(days=rng.randrange(0, 10))

    def experiment_workstreams() -> list[dict[str, Any]]:
        work_rng = random.Random(seed ^ 0xE71A5EED)
        objectives = (
            "robustness under distribution shift",
            "calibration after label noise",
            "retrieval sensitivity at long range",
            "multilingual transfer stability",
            "tool-use recovery after timeout",
            "citation fidelity under revision",
        )
        dataset_slices = (
            "temporal holdout",
            "rare-category tail",
            "cross-lingual subset",
            "adversarial paraphrase set",
            "out-of-domain validation",
            "human-disagreement stratum",
        )
        metrics = (
            "macro-F1",
            "expected calibration error",
            "exact-match recall",
            "paired win rate",
            "recovery success rate",
            "citation entailment precision",
        )
        environments = (
            "isolated CPU replica",
            "single-GPU clean room",
            "air-gapped evaluation node",
            "independent cloud runner",
            "frozen container mirror",
            "external lab workstation",
        )
        failure_modes = (
            "label-map drift",
            "tokenizer checksum mismatch",
            "stale retrieval index",
            "non-deterministic sampler seed",
            "dataset shard omission",
            "metric aggregation skew",
        )
        recovery_actions = (
            "rebuild the label registry",
            "pin the tokenizer artifact",
            "recompute the retrieval index",
            "freeze the sampler ledger",
            "restore the omitted shard",
            "replace the metric reducer",
        )
        out: list[dict[str, Any]] = []
        lane_count = min(6, max(1, n_workstreams))
        for index in range(n_workstreams):
            lane = index % lane_count
            cycle = index // lane_count
            workstream_id = f"xp{index + 1:02d}"
            upstream_id = f"xp{index - lane_count + 1:02d}" if cycle > 0 else None
            out.append(
                {
                    "id": workstream_id,
                    "lane": lane + 1,
                    "cycle": cycle + 1,
                    "upstream_id": upstream_id,
                    "objective": objectives[lane % len(objectives)],
                    "dataset_slice": dataset_slices[
                        (lane + 2 * cycle) % len(dataset_slices)
                    ],
                    "metric": metrics[(lane + cycle) % len(metrics)],
                    "environment": environments[(2 * lane + cycle) % len(environments)],
                    "failure_mode": failure_modes[
                        (lane + 3 * cycle) % len(failure_modes)
                    ],
                    "recovery_action": recovery_actions[
                        (lane + 3 * cycle) % len(recovery_actions)
                    ],
                    "revision": f"XR-{index + 1:02d}-{work_rng.randint(1000, 9999)}",
                    "review": f"XV-{index + 1:02d}-{work_rng.randint(1000, 9999)}",
                    "benchmark": (f"XB-{index + 1:02d}-{work_rng.randint(1000, 9999)}"),
                    "failure": f"XF-{index + 1:02d}-{work_rng.randint(1000, 9999)}",
                    "recovery": f"XP-{index + 1:02d}-{work_rng.randint(1000, 9999)}",
                }
            )
        return out

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
        patch_commit = f"{rng.randint(0x100000, 0xEFFFFF):06x}"
        spdx = f"Apache-2.0-L{rng.randint(1000, 9999)}"
        people = {
            "pi": _person(rng, used),
            "student": _person(rng, used),
            "reviewer": _person(rng, used),
        }
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
            "benchmark_patch_commit": patch_commit,
            "spdx": spdx,
            "pi": people["pi"],
            "student": people["student"],
            "reviewer": people["reviewer"],
            "people": people,
            "org": attach_roles(people, {}),
            "process": sample_process(rng, "researchlab"),
            "dataset_v2": f"DSET-v2-{rng.randint(1000, 9999)}",
            "revision_v2": f"R2-{rng.randint(1000, 9999)}",
            "revision_v3": f"R3-{rng.randint(1000, 9999)}",
            "review_round1": f"REV1-{rng.randint(1000, 9999)}",
            "review_round2": f"REV2-{rng.randint(1000, 9999)}",
            "response_round1": f"RESP1-{rng.randint(1000, 9999)}",
            "response_round2": f"RESP2-{rng.randint(1000, 9999)}",
            "benchmark_v2": f"{bench}-cfg2-{rng.randint(100, 999)}",
            "benchmark_v3": f"{bench}-cfg3-{rng.randint(100, 999)}",
            "reproduction_failure": f"RFAIL-{rng.randint(1000, 9999)}",
            "reproduction_success": f"RPASS-{rng.randint(1000, 9999)}",
            "lab": f"{stem} Lab",
            "n_pulses": n_pulses,
            "start": start.isoformat(),
            "latent_token": f"LT-{stem[:3].upper()}{rng.randint(1000, 9999)}",
            "decoy_latent_token": f"LD-{stem[:3].upper()}{rng.randint(1000, 9999)}",
            **pick_anchors(rng),
            "docket_token": f"DK-{stem[:3].upper()}{rng.randint(1000, 9999)}",
            "truth_regime": "synthetic_executable",
            "experiment_workstreams": experiment_workstreams() if is_focal else [],
        }

    focal = one("focal", True)
    focal["source_workflows"] = workflows
    focal["source_workflow_ids"] = [workflow.workflow_id for workflow in workflows]
    parallels = [one("parallel", False) for _ in range(n_parallel)]
    assign_register(seed, focal, parallels)
    world_id = f"lab{seed:06d}-{focal['paper'].lower()}"
    return {
        "world_id": world_id,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "domain": "researchlab",
        "truth_regime": (
            "verified_real_content_hybrid" if workflows else "synthetic_executable"
        ),
        "n_pulses": n_pulses,
        "start": start.isoformat(),
        "focal": focal,
        "parallels": parallels,
        "n_parallel": n_parallel,
        "n_workstreams": n_workstreams,
        "project": focal,
    }
