from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from longworld.core.consist import consistency_scan
from longworld.core.render import Artifact, render_world, stamp_text_integrity
from longworld.core.world import SimulatedWorld
from longworld.domains.company.queries import QuerySpec, build_queries
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company

if TYPE_CHECKING:
    from longworld.core.realworkflow import RealWorkflow
    from longworld.core.sourceworkflow import SourceWorkflow


def balanced_domain_schedule(
    domains: list[str],
    *,
    n_worlds: int,
    quotas: dict[str, int] | None = None,
) -> list[str]:
    """Allocate worlds deterministically while preserving declared domain order."""
    if not domains or len(set(domains)) != len(domains):
        raise ValueError("domains must be a non-empty list of unique names")
    if n_worlds < 1:
        raise ValueError("n_worlds must be positive")
    explicit_quotas = quotas is not None
    if quotas is None:
        quotas = {
            domain: n_worlds // len(domains) + (index < n_worlds % len(domains))
            for index, domain in enumerate(domains)
        }
    if set(quotas) != set(domains):
        raise ValueError("domain quota keys must exactly match configured domains")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < (1 if explicit_quotas else 0)
        for value in quotas.values()
    ):
        raise ValueError("domain quotas must be positive integers")
    if sum(quotas.values()) != n_worlds:
        raise ValueError("domain quotas must sum to n_worlds")

    remaining = dict(quotas)
    schedule: list[str] = []
    while len(schedule) < n_worlds:
        for domain in domains:
            if remaining[domain] > 0:
                schedule.append(domain)
                remaining[domain] -= 1
    return schedule


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
    n_pulses: int = 0,
    domain: str = "company",
    n_workstreams: int = 0,
    real_workflows: list[RealWorkflow] | None = None,
    source_workflows: list[SourceWorkflow] | None = None,
    include_program_joins: bool = True,
) -> MaterializedWorld:
    if real_workflows and domain != "codeforge":
        raise ValueError("real repository workflows require the codeforge domain")
    if source_workflows and any(
        workflow.target_domain != domain for workflow in source_workflows
    ):
        raise ValueError("source workflow target domain does not match materialization")
    if domain == "researchlab":
        from longworld.domains.researchlab.queries import build_lab_queries
        from longworld.domains.researchlab.schema import sample_lab_spec
        from longworld.domains.researchlab.simulate import simulate_lab

        spec = sample_lab_spec(
            seed,
            n_parallel=n_parallel,
            n_pulses=n_pulses,
            n_workstreams=n_workstreams,
            source_workflows=source_workflows,
        )
        worlds = simulate_lab(spec)
        query_fn = build_lab_queries
    elif domain == "codeforge":
        from longworld.domains.codeforge.queries import build_code_queries
        from longworld.domains.codeforge.schema import sample_code_spec
        from longworld.domains.codeforge.simulate import simulate_code

        spec = sample_code_spec(
            seed,
            n_parallel=n_parallel,
            n_pulses=n_pulses,
            n_workstreams=n_workstreams,
            real_workflows=real_workflows,
        )
        worlds = simulate_code(spec)
        query_fn = build_code_queries
    elif domain == "company":
        spec = sample_world_spec(
            seed,
            n_parallel=n_parallel,
            n_pulses=n_pulses,
            n_workstreams=n_workstreams,
            source_workflows=source_workflows,
        )
        worlds = simulate_company(spec)
        query_fn = build_queries
    else:
        raise ValueError(f"unsupported domain: {domain}")
    artifacts = {k: render_world(w) for k, w in worlds.items()}
    queries = list(query_fn(worlds["focal"]))

    by_id = {event.id: event for event in worlds["focal"].events}

    def causal_closure(event_ids: list[str]) -> list[str]:
        explicit_roots = set(event_ids)
        selected: set[str] = set()

        def visit(event_id: str) -> None:
            if event_id in selected or event_id not in by_id:
                return
            event = by_id[event_id]
            for parent_id in event.required_inputs:
                visit(parent_id)
            for parent_id in event.causal_inputs:
                relation = event.relation_kinds.get(parent_id)
                synthetic_relations = event.params.get("synthetic_relation_inputs", [])
                if (
                    relation == "supersedes"
                    and parent_id in synthetic_relations
                    and parent_id not in explicit_roots
                ):
                    continue
                if relation != "source_context":
                    visit(parent_id)
            selected.add(event_id)

        for event_id in event_ids:
            visit(event_id)
        return [event.id for event in worlds["focal"].events if event.id in selected]

    for query in queries:
        query.sufficient_event_ids = causal_closure(query.sufficient_event_ids)
    from longworld.core.artifact_dag import attach_dag_slots
    from longworld.core.compose_queries import compose_queries
    from longworld.core.contentplan import attach_content_plans
    from longworld.core.discourse import expand_discourse

    if include_program_joins:
        queries += compose_queries(worlds["focal"], queries)
    artifacts = {
        k: stamp_text_integrity(
            attach_dag_slots(worlds[k], expand_discourse(attach_content_plans(v)))
        )
        for k, v in artifacts.items()
    }
    issues = []
    ok = True
    for k, w in worlds.items():
        scan = consistency_scan(w, artifacts[k])
        if not scan.ok:
            ok = False
            issues.extend(scan.issues)
    return MaterializedWorld(
        spec=spec,
        worlds=worlds,
        artifacts=artifacts,
        queries=queries,
        scan_ok=ok,
        scan_issues=issues,
    )
