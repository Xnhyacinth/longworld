import pytest

from longworld.core.engine import answer_from_artifacts
from longworld.core.graph import min_sufficient_subgraph, proof_depth
from longworld.core.pack import estimate_tokens, join_artifacts
from longworld.core.render import render_world
from longworld.core.sampler import materialize
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.company.queries import build_queries
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company


def _renewal_case(seed: int = 1):
    materialized = materialize(
        seed,
        n_parallel=2,
        n_pulses=0,
        domain="company",
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    query = next(
        item
        for item in materialized.queries
        if item.query_type == "renewal_control_trace"
    )
    return materialized, world, query


def test_company_renewal_is_a_second_event_bearing_release_cycle() -> None:
    materialized, world, query = _renewal_case()
    events = {event.id: event for event in world.events}

    assert not materialized.spec["n_pulses"]
    assert query.truth_regime == "real_schema_synthetic_instance"
    assert query.preferred_length_buckets == ["64k", "128k", "256k"]
    assert len(query.essential_event_ids) == 5
    assert len(query.essential_artifact_ids) == 5

    first_audit = events["focal.audit_correction"]
    renewal_roadmap = events["focal.renewal_roadmap"]
    renewal_amendment = events["focal.renewal_amendment"]
    renewal_release = events["focal.renewal_release"]
    renewal_audit = events["focal.renewal_audit"]

    assert (renewal_audit.time - first_audit.time).days >= 120
    assert first_audit.id in renewal_roadmap.causal_inputs
    assert renewal_roadmap.id in renewal_amendment.causal_inputs
    assert renewal_amendment.id in renewal_release.causal_inputs
    assert renewal_release.id in renewal_audit.causal_inputs


def test_company_renewal_answer_requires_every_cross_stream_document() -> None:
    materialized, world, query = _renewal_case()
    artifacts = {
        artifact.artifact_id: artifact for artifact in materialized.artifacts["focal"]
    }
    essentials = [artifacts[item] for item in query.essential_artifact_ids]

    assert answer_from_artifacts(world, query, essentials) == query.answer
    for dropped in essentials:
        remaining = [item for item in essentials if item is not dropped]
        assert answer_from_artifacts(world, query, remaining) != query.answer


def test_company_renewal_counterfactual_replays_to_declared_twin(monkeypatch) -> None:
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-p4-candidate-test-key-32-bytes"
    )
    materialized, world, query = _renewal_case()
    _, counterfactual = render_cf_view(world, query)

    verification, notes = verify_question(
        world,
        query,
        materialized.artifacts["focal"],
        cf_artifacts=counterfactual,
        verification_mode="candidate",
    )

    assert query.answer != query.cf_answer
    assert notes["cf_replay_ans"] == query.cf_answer
    assert verification.counterfactual_replay_sufficient
    assert verification.strict_executable_sufficient
    assert verification.remove_one_fails
    assert verification.all_green(), notes


def test_company_renewal_keeps_primary_ledger_consistent_across_seeds() -> None:
    for seed in (1, 3, 7):
        materialized = materialize(
            seed,
            n_parallel=1,
            n_pulses=0,
            domain="company",
            include_program_joins=False,
        )
        world = materialized.worlds["focal"]
        project = world.spec["project"]

        assert materialized.scan_ok, materialized.scan_issues
        assert world.state.values["revenue_recognized"] == project["audited_revenue"]
        assert (
            world.state.values["renewal_revenue_recognized"]
            == project["renewal_audited_revenue"]
        )
        assert (
            world.state.values["renewal_legal_effective_version"]
            == project["renewal_roadmap_version"]
        )


def _long_workstream_case(n_workstreams: int = 36):
    spec = sample_world_spec(
        11,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=n_workstreams,
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    queries = build_queries(world)
    trace = next(
        query for query in queries if query.query_type == "portfolio_recovery_trace"
    )
    return spec, world, artifacts, trace


def test_company_workstream_extension_is_bounded_and_default_off() -> None:
    implicit = sample_world_spec(11, n_parallel=0, n_pulses=0)
    explicit = sample_world_spec(
        11,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
    )

    assert implicit == explicit
    assert explicit["focal"]["workstreams"] == []
    with pytest.raises(ValueError, match="between 0 and 64"):
        sample_world_spec(11, n_workstreams=65)


def test_company_workstreams_grow_unique_event_bearing_context() -> None:
    spec, world, artifacts, _ = _long_workstream_case()
    cycle_artifacts = [
        artifact for artifact in artifacts if ".cycle_" in artifact.artifact_id
    ]

    assert len(spec["focal"]["workstreams"]) == 36
    assert (
        len([event for event in world.events if event.type.startswith("cycle_")]) == 180
    )
    assert len(cycle_artifacts) == 180
    assert estimate_tokens(join_artifacts(cycle_artifacts)) >= 16_000
    assert len({artifact.text for artifact in cycle_artifacts}) == len(cycle_artifacts)
    assert all(artifact.reveals_events for artifact in cycle_artifacts)


def test_company_workstream_trace_has_cross_cycle_dependency_growth() -> None:
    spec, world, _, trace = _long_workstream_case()
    events = {event.id: event for event in world.events}

    for index in range(1, 36):
        previous_audit = f"focal.cycle_{index - 1:03d}_audit"
        plan = events[f"focal.cycle_{index:03d}_plan"]
        assert previous_audit in plan.required_inputs
        assert plan.relation_kinds[previous_audit] == "delayed_cause"

    assert len(trace.essential_artifact_ids) == 4 * 36
    assert len(trace.sufficient_event_ids) >= 180
    assert proof_depth(world, trace) >= 150
    assert trace.proof_depth == proof_depth(world, trace)
    assert min_sufficient_subgraph(world, trace).number_of_edges() >= 180
    assert trace.answer.count(" | ") == 35
    for workstream in spec["focal"]["workstreams"]:
        assert workstream["incident_token"] in trace.answer
        assert workstream["resolution_token"] in trace.answer
        assert workstream["release_token"] in trace.answer
        assert str(workstream["audit_amount"]) in trace.answer


def test_company_workstream_trace_replays_counterfactual_and_remove_one(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-p4-workstream-test-key-32-bytes"
    )
    _, world, artifacts, trace = _long_workstream_case()
    _, counterfactual = render_cf_view(world, trace)

    verification, notes = verify_question(
        world,
        trace,
        artifacts,
        cf_artifacts=counterfactual,
        verification_mode="candidate",
    )

    assert trace.answer != trace.cf_answer
    assert notes["cf_replay_ans"] == trace.cf_answer
    assert verification.counterfactual_replay_sufficient
    assert verification.remove_one_fails
    assert verification.strict_executable_sufficient
    assert verification.all_green(), notes
