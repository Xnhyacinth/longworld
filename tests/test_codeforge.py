import random

from longworld.core.attestation import ATTESTATION_ENV
from longworld.core.causal import build_causal_graph
from longworld.core.engine import answer_from_artifacts
from longworld.core.graph import min_sufficient_subgraph, typed_walk_event_ids
from longworld.core.sampler import materialize
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.topology import canonical_topology
from longworld.core.verify import verify_question
from longworld.core.views import memory_card, render_cf_view


def test_codeforge_motifs_and_gates():
    mat = materialize(31, n_parallel=1, n_pulses=6, domain="codeforge")
    assert mat.scan_ok, mat.scan_issues
    world = mat.worlds["focal"]
    arts = mat.artifacts["focal"]
    assert world.spec["domain"] == "codeforge"
    motifs = {q.motif for q in mat.queries}
    assert {
        "supersession",
        "fork_join",
        "delayed_effect",
        "contradiction",
        "hidden_bridge",
        "counterfactual_supersession",
    } <= motifs
    topos = {q.topology_id for q in mat.queries}
    assert len(topos) == len(mat.queries)
    for spec in mat.queries:
        _, cf_arts = render_cf_view(world, spec)
        ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
        assert spec.cf_answer != spec.answer, spec.query_type
        assert ver.all_green(), (spec.query_type, spec.motif, notes, ver)
        sub = min_sufficient_subgraph(world, spec)
        assert sub.number_of_nodes() >= 2
        g = build_causal_graph(world)
        typed_walk_event_ids(
            g,
            starts=list(spec.essential_event_ids),
            allowed_kinds={"enables", "derived_from", "supersedes", "contradicts"},
            n_walks=4,
            walk_len=4,
            forbid=set(spec.essential_event_ids),
            rng=random.Random(0),
        )
        dist = [a for a in arts if a.artifact_id not in spec.essential_artifact_ids]
        assert answer_from_artifacts(world, spec, dist) != spec.answer
        card = memory_card(world, spec)
        assert "Calendar:" in card
        assert spec.answer not in card


def test_code_head_not_in_tag_notes():
    mat = materialize(32, n_parallel=1, n_pulses=4, domain="codeforge")
    world = mat.worlds["focal"]
    tag = next(
        a for a in mat.artifacts["focal"] if a.artifact_id.endswith("tag_release")
    )
    final = str(world.spec["project"]["hotfix_hash"])
    assert final not in tag.text
    cur = next(q for q in mat.queries if q.query_type == "current_state")
    assert cur.answer == str(world.spec["project"]["hotfix_hash"])
    lic = next(
        a for a in mat.artifacts["focal"] if a.artifact_id.endswith("license_note")
    )
    hid = next(q for q in mat.queries if q.query_type == "hidden_bridge")
    assert hid.answer in lic.text
    assert hid.answer not in tag.text


def test_code_renderer_does_not_append_prose_bank():
    from longworld.core.prose import SENTENCE_BANK

    mat = materialize(32, n_parallel=1, n_pulses=0, domain="codeforge")
    blob = "\n".join(a.text for a in mat.artifacts["focal"])
    assert not any(s in blob for s in SENTENCE_BANK)
    assert not any(e.type == "status_pulse" for e in mat.worlds["focal"].events)


def test_release_workstreams_form_a_strict_cross_stream_proof(monkeypatch):
    monkeypatch.setenv(ATTESTATION_ENV, "codeforge-test-attestation-key-32-bytes")
    mat = materialize(
        41,
        n_parallel=0,
        n_pulses=0,
        domain="codeforge",
        n_workstreams=4,
    )
    assert mat.scan_ok, mat.scan_issues
    world = mat.worlds["focal"]
    spec = next(q for q in mat.queries if q.query_type == "release_eligibility")
    assert "|release_eligibility|" in canonical_topology(spec)
    assert "|program_join|" not in canonical_topology(spec)
    artifacts = [
        artifact
        for artifact in mat.artifacts["focal"]
        if artifact.doc_type != "source_pack"
    ]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in set(spec.essential_artifact_ids)
    ]
    release_event = next(
        event for event in world.events if event.type == "release_decision"
    )

    assert len(essential) == 21
    assert release_event.required_inputs == release_event.causal_inputs
    assert sentence_near_dup_ratio(essential) <= 0.25
    assert len(world.state.history) >= 20
    assert all(
        spec.answer.lower() not in artifact.text.lower() for artifact in essential
    )
    assert (
        answer_from_artifacts(
            world,
            spec,
            essential,
            enforce_preconditions=True,
        )
        == spec.answer
    )
    for artifact in essential:
        assert (
            answer_from_artifacts(
                world,
                spec,
                [item for item in essential if item is not artifact],
                enforce_preconditions=True,
            )
            != spec.answer
        )

    _, rendered_cf_artifacts = render_cf_view(world, spec)
    cf_world, _ = render_cf_view(world, spec)
    cf_artifacts = [
        artifact
        for artifact in rendered_cf_artifacts
        if artifact.doc_type != "source_pack"
    ]
    cf_essential = [
        artifact
        for artifact in cf_artifacts
        if artifact.artifact_id in set(spec.essential_artifact_ids)
    ]
    assert (
        answer_from_artifacts(
            world,
            spec,
            cf_essential,
            extra_overrides={spec.cf_event_id: spec.cf_param_updates},
            enforce_preconditions=True,
        )
        == spec.cf_answer
    )
    assert spec.cf_answer != spec.answer
    assert spec.cf_event_id.endswith("_ci")
    assert spec.cf_param_updates == {"passed": False}
    assert cf_world.state.values["workflow_release_policy"] == "blocked"
    assert not cf_world.state.values[
        f"ws:{world.spec['project']['workstreams'][0]['id']}:ci_pass"
    ]
    assert "result: failed" in next(
        artifact.text
        for artifact in cf_artifacts
        if artifact.artifact_id.endswith("workflow_0_ci")
    )

    verification, notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert not verification.contiguous_windows_insufficient
    assert any(
        result["answer"] == spec.answer
        for result in notes["contiguous_windows"].values()
    )
    assert not verification.all_green()


def test_release_workstreams_expose_distinct_ci_and_license_matrix_programs():
    mat = materialize(
        41,
        n_parallel=0,
        n_pulses=0,
        domain="codeforge",
        n_workstreams=4,
    )
    world = mat.worlds["focal"]
    artifacts = [
        artifact
        for artifact in mat.artifacts["focal"]
        if artifact.doc_type != "source_pack"
    ]
    by_type = {query.query_type: query for query in mat.queries}

    assert {"release_ci_matrix", "release_license_matrix"} <= set(by_type)
    for query_type, blocked_answer in (
        ("release_ci_matrix", "BLOCKED-CI"),
        ("release_license_matrix", "BLOCKED-LICENSE"),
    ):
        query = by_type[query_type]
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in set(query.essential_artifact_ids)
        ]
        assert len(essential) == 21
        assert (
            answer_from_artifacts(world, query, essential, enforce_preconditions=True)
            == query.answer
        )
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item is not artifact],
                enforce_preconditions=True,
            )
            != query.answer
            for artifact in essential
        )
        assert query.cf_answer == blocked_answer
        assert query.answer != query.cf_answer


def test_license_matrix_counterfactual_is_visible_in_rendered_text() -> None:
    mat = materialize(
        41,
        n_parallel=0,
        n_pulses=0,
        domain="codeforge",
        n_workstreams=4,
    )
    query = next(
        item for item in mat.queries if item.query_type == "release_license_matrix"
    )
    _, cf_artifacts = render_cf_view(mat.worlds["focal"], query)
    factual = {artifact.artifact_id: artifact for artifact in mat.artifacts["focal"]}
    counterfactual = {artifact.artifact_id: artifact for artifact in cf_artifacts}
    changed_id = next(
        artifact_id
        for artifact_id in query.essential_artifact_ids
        if (factual[artifact_id].slots or {}).get("event_type") == "license_clearance"
        and factual[artifact_id].text != counterfactual[artifact_id].text
    )

    assert "compatible" in factual[changed_id].text
    assert "incompatible" in counterfactual[changed_id].text
    assert factual[changed_id].text != counterfactual[changed_id].text
