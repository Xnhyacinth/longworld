from __future__ import annotations

from dataclasses import dataclass

from longworld.core.consist import consistency_scan
from longworld.core.render import Artifact, render_world
from longworld.core.world import SimulatedWorld
from longworld.domains.company.queries import QuerySpec, build_queries
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company


@dataclass
class MaterializedWorld:
    spec: dict
    worlds: dict[str, SimulatedWorld]
    artifacts: dict[str, list[Artifact]]
    queries: list[QuerySpec]
    scan_ok: bool
    scan_issues: list


def materialize(
    seed: int,
    n_parallel: int = 2,
    n_pulses: int = 14,
    domain: str = "company",
) -> MaterializedWorld:
    if domain == "researchlab":
        from longworld.domains.researchlab.queries import build_lab_queries
        from longworld.domains.researchlab.schema import sample_lab_spec
        from longworld.domains.researchlab.simulate import simulate_lab

        spec = sample_lab_spec(seed, n_parallel=n_parallel, n_pulses=n_pulses)
        worlds = simulate_lab(spec)
        query_fn = build_lab_queries
    elif domain == "codeforge":
        from longworld.domains.codeforge.queries import build_code_queries
        from longworld.domains.codeforge.schema import sample_code_spec
        from longworld.domains.codeforge.simulate import simulate_code

        spec = sample_code_spec(seed, n_parallel=n_parallel, n_pulses=n_pulses)
        worlds = simulate_code(spec)
        query_fn = build_code_queries
    else:
        spec = sample_world_spec(seed, n_parallel=n_parallel, n_pulses=n_pulses)
        worlds = simulate_company(spec)
        query_fn = build_queries
    artifacts = {k: render_world(w) for k, w in worlds.items()}
    issues = []
    ok = True
    for k, w in worlds.items():
        scan = consistency_scan(w, artifacts[k])
        if not scan.ok:
            ok = False
            issues.extend(scan.issues)
    queries = query_fn(worlds["focal"])
    return MaterializedWorld(
        spec=spec,
        worlds=worlds,
        artifacts=artifacts,
        queries=queries,
        scan_ok=ok,
        scan_issues=issues,
    )
