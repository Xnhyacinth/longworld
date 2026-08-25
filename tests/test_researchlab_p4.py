import pytest

from longworld.core.engine import answer_from_artifacts
from longworld.core.render import render_world
from longworld.core.sampler import materialize
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.researchlab.queries import build_lab_queries
from longworld.domains.researchlab.schema import sample_lab_spec
from longworld.domains.researchlab.simulate import simulate_lab


def _p4_materialized():
    return materialize(
        421,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        include_program_joins=False,
    )


def _workstream_world(n_workstreams: int):
    spec = sample_lab_spec(
        421,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=n_workstreams,
    )
    world = simulate_lab(spec)["focal"]
    artifacts = render_world(world)
    queries = build_lab_queries(world)
    return spec, world, artifacts, queries


def test_p4_research_workstream_limit_supports_native_64k_histories():
    spec = sample_lab_spec(421, n_parallel=0, n_pulses=0, n_workstreams=96)

    assert len(spec["focal"]["experiment_workstreams"]) == 96
    with pytest.raises(ValueError, match="between 0 and 128"):
        sample_lab_spec(421, n_parallel=0, n_pulses=0, n_workstreams=129)


def test_p4_research_64k_matrix_scales_necessary_and_supporting_evidence():
    _, _, _, queries = _workstream_world(88)
    matrix = next(
        query for query in queries if query.query_type == "experiment_matrix_resolution"
    )

    assert len(matrix.essential_event_ids) == 89
    assert len(matrix.sufficient_event_ids) == 441
    assert matrix.proof_depth == 76


def test_p4_research_workflow_spans_revision_review_and_reproduction_cycles():
    mat = _p4_materialized()
    world = mat.worlds["focal"]
    events = {event.id.rsplit(".", 1)[-1]: event for event in world.events}

    assert world.spec["truth_regime"] == "synthetic_executable"
    assert world.spec["project"]["truth_regime"] == "synthetic_executable"
    assert all(
        query.truth_regime
        in {
            "synthetic_executable",
            "grounded_public_private",
            "verified_real_content_hybrid",
        }
        for query in mat.queries
    )
    assert {
        "submit_revision_2",
        "review_round_1",
        "respond_round_1",
        "reproduction_failure",
        "benchmark_patch",
        "review_round_2",
        "respond_round_2",
        "reproduction_recovery",
        "submit_revision_3",
        "meta_decision",
    } <= set(events)
    assert events["submit_revision_2"].time < events["meta_decision"].time
    assert events["reproduction_failure"].time < events["reproduction_recovery"].time
    assert events["review_round_1"].id in events["respond_round_1"].causal_inputs
    assert events["benchmark_patch"].id in events["reproduction_recovery"].causal_inputs
    assert events["respond_round_2"].id in events["meta_decision"].causal_inputs
    assert events["reproduction_recovery"].id in events["meta_decision"].causal_inputs
    assert (
        world.state.values["accepted_revision"] == world.spec["project"]["revision_v3"]
    )
    assert (
        world.state.values["accepted_benchmark"]
        == world.spec["project"]["benchmark_v3"]
    )
    assert (
        world.state.values["accepted_reproduction"]
        == world.spec["project"]["reproduction_success"]
    )


def test_p4_complex_programs_have_strict_counterfactual_twins():
    mat = _p4_materialized()
    world = mat.worlds["focal"]
    artifacts = mat.artifacts["focal"]
    artifact_index = {artifact.artifact_id: artifact for artifact in artifacts}
    by_type = {query.query_type: query for query in mat.queries}

    assert {
        "revision_reproduction_resolution",
        "review_response_trace",
    } <= set(by_type)
    for query_type in (
        "revision_reproduction_resolution",
        "review_response_trace",
    ):
        query = by_type[query_type]
        assert query.truth_regime == "synthetic_executable"
        assert query.proof_depth >= 5
        assert len(query.essential_event_ids) >= 3
        assert query.cf_answer != query.answer
        essential = [artifact_index[aid] for aid in query.essential_artifact_ids]
        assert (
            answer_from_artifacts(
                world,
                query,
                essential,
            )
            == query.answer
        )
        strict = [
            artifact
            for artifact in artifacts
            if set(artifact.reveals_events).intersection(query.sufficient_event_ids)
        ]
        assert (
            answer_from_artifacts(
                world,
                query,
                strict,
                enforce_preconditions=True,
            )
            == query.answer
        )

        _, cf_artifacts = render_cf_view(world, query)
        cf_index = {artifact.artifact_id: artifact for artifact in cf_artifacts}
        cf_essential = [cf_index[aid] for aid in query.essential_artifact_ids]
        assert (
            answer_from_artifacts(
                world,
                query,
                cf_essential,
                extra_overrides={query.cf_event_id: query.cf_param_updates},
            )
            == query.cf_answer
        )
        cf_strict = [
            artifact
            for artifact in cf_artifacts
            if set(artifact.reveals_events).intersection(query.sufficient_event_ids)
        ]
        assert (
            answer_from_artifacts(
                world,
                query,
                cf_strict,
                extra_overrides={query.cf_event_id: query.cf_param_updates},
                enforce_preconditions=True,
            )
            == query.cf_answer
        )


def test_p4_complex_programs_pass_existing_executable_gates():
    mat = _p4_materialized()
    world = mat.worlds["focal"]
    artifacts = mat.artifacts["focal"]
    new_queries = [
        query
        for query in mat.queries
        if query.query_type
        in {"revision_reproduction_resolution", "review_response_trace"}
    ]

    assert mat.scan_ok, mat.scan_issues
    assert len(new_queries) == 2
    for query in new_queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
        )
        assert verification.all_green(), (query.query_type, notes, verification)


def test_p4_parameterized_workstreams_preserve_zero_and_grow_native_content():
    implicit = sample_lab_spec(421, n_parallel=0, n_pulses=0)
    explicit, _, _, _ = _workstream_world(0)
    assert explicit == implicit

    _, small_world, small_artifacts, small_queries = _workstream_world(6)
    _, large_world, large_artifacts, large_queries = _workstream_world(36)
    small_experiment = [
        artifact
        for artifact in small_artifacts
        if str(artifact.slots.get("event_type") or "").startswith("experiment_")
    ]
    large_experiment = [
        artifact
        for artifact in large_artifacts
        if str(artifact.slots.get("event_type") or "").startswith("experiment_")
    ]
    assert len(large_world.events) - len(small_world.events) == 30 * 5
    assert len(large_artifacts) - len(small_artifacts) == 30 * 5
    assert sum(len(artifact.text) for artifact in large_experiment) >= 70_000
    assert sum(len(artifact.text) for artifact in large_experiment) > 4 * sum(
        len(artifact.text) for artifact in small_experiment
    )

    small_matrix = next(
        query
        for query in small_queries
        if query.query_type == "experiment_matrix_resolution"
    )
    large_matrix = next(
        query
        for query in large_queries
        if query.query_type == "experiment_matrix_resolution"
    )
    assert len(large_matrix.essential_event_ids) == 37
    assert len(large_matrix.sufficient_event_ids) == 36 * 5 + 1
    assert len(large_matrix.sufficient_event_ids) > len(
        small_matrix.sufficient_event_ids
    )
    assert large_matrix.proof_depth > small_matrix.proof_depth


def test_p4_matrix_program_replays_cf_and_has_distinct_event_documents():
    _, world, artifacts, queries = _workstream_world(36)
    matrix = next(
        query for query in queries if query.query_type == "experiment_matrix_resolution"
    )
    experiment_artifacts = [
        artifact
        for artifact in artifacts
        if str(artifact.slots.get("event_type") or "").startswith("experiment_")
    ]

    assert len(experiment_artifacts) == 36 * 5 + 1
    assert len({artifact.text for artifact in experiment_artifacts}) == len(
        experiment_artifacts
    )
    assert sentence_near_dup_ratio(experiment_artifacts) == 0.0
    assert matrix.truth_regime == "synthetic_executable"
    assert len(matrix.answer.split(" | ")) == 36
    assert matrix.cf_answer != matrix.answer

    _, cf_artifacts = render_cf_view(world, matrix)
    verification, notes = verify_question(
        world,
        matrix,
        artifacts,
        cf_artifacts=cf_artifacts,
    )
    assert verification.all_green(), (notes, verification)
