from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import date

import pytest

from longworld.core.attestation import ATTESTATION_ENV
from longworld.core.sampler import materialize
from longworld.core.verify import verify_packed_question, verify_question
from longworld.core.views import render_cf_view
from longworld.core.world import Event, WorldSimulator

TEST_ATTESTATION_KEY = "longworld-test-attestation-key-32-bytes"


@pytest.fixture(autouse=True)
def _attestation_key(monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, TEST_ATTESTATION_KEY)


def _company_case():
    mat = materialize(1, n_parallel=1, n_pulses=0, domain="company")
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    return mat.worlds["focal"], spec, mat.artifacts["focal"]


def test_surface_ratio_is_stable_across_python_hash_seeds() -> None:
    script = textwrap.dedent(
        """
        from types import SimpleNamespace
        import longworld.core.verify as verify

        factual = []
        counterfactual = []
        ratios = {}
        for index in range(300):
            left = f"L{index}"
            right = f"R{index}"
            ratios[(left, right)] = ((index * 7919) % 10007) / 10007
            factual.append(SimpleNamespace(artifact_id=f"artifact-{index}", text=left))
            counterfactual.append(
                SimpleNamespace(artifact_id=f"artifact-{index}", text=right)
            )
        verify.token_overlap = lambda left, right: ratios[(left, right)]
        print(repr(verify.artifact_surface_ratio(factual, counterfactual)))
        """
    )

    outputs = {
        subprocess.check_output(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
        ).strip()
        for seed in ("1", "2", "3", "4")
    }

    assert len(outputs) == 1


def test_strict_replay_requires_declared_causal_inputs() -> None:
    def check(_state, _event):
        return True, None

    def apply(state, event):
        state.set(event.type, True, event.id, event.time)

    parent = Event(
        id="parent",
        type="parent_seen",
        time=date(2026, 1, 1),
        params={},
        visibility=[],
    )
    child = Event(
        id="child",
        type="child_seen",
        time=date(2026, 1, 2),
        params={},
        visibility=[],
        causal_inputs=[parent.id],
        required_inputs=[parent.id],
    )
    simulator = WorldSimulator(
        spec={"world_id": "strict-causal", "seed": 0},
        init_values={"parent_seen": False, "child_seen": False},
        check_preconditions=check,
        apply_event=apply,
    )

    constructed = simulator.run([child])
    incomplete = simulator.replay_events([child], enforce_preconditions=True)
    complete = simulator.replay_events([parent, child], enforce_preconditions=True)

    assert not constructed.state.values["child_seen"]
    assert constructed.events[0].skip_reason == "required_input_missing"
    assert not incomplete.values["child_seen"]
    assert complete.values["child_seen"]


def test_counterfactual_gate_replays_artifacts_and_rejects_wrong_label():
    world, spec, artifacts = _company_case()
    _, cf_artifacts = render_cf_view(world, spec)
    bad_spec = replace(spec, cf_answer="definitely-the-wrong-counterfactual")

    verification, notes = verify_question(
        world,
        bad_spec,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="production",
    )

    assert notes["cf_replay_ans"] == spec.cf_answer
    assert not verification.counterfactual_replay_sufficient
    assert not verification.counterfactual_changes_answer
    assert not verification.all_green()


def test_counterfactual_gate_rejects_corrupted_cf_evidence_text():
    world, spec, artifacts = _company_case()
    _, cf_artifacts = render_cf_view(world, spec)
    essential = set(spec.essential_artifact_ids)
    corrupted_cf = [
        replace(a, text="Counterfactual evidence was corrupted.")
        if a.artifact_id in essential
        else a
        for a in cf_artifacts
    ]

    verification, notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=corrupted_cf,
        verification_mode="production",
    )

    assert notes["cf_replay_ans"] == spec.cf_answer
    assert notes["cf_text_issues"]
    assert not verification.counterfactual_replay_sufficient


def test_counterfactual_task_gate_enforces_hard_preconditions(monkeypatch):
    world, spec, artifacts = _company_case()

    def replay(_world, _spec, _artifacts, **kwargs):
        if kwargs.get("extra_overrides"):
            return (
                spec.cf_answer if not kwargs.get("enforce_preconditions") else "unknown"
            )
        return spec.answer

    monkeypatch.setattr("longworld.core.verify.answer_from_artifacts", replay)
    verification, notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=artifacts,
        verification_mode="candidate",
    )

    assert notes["cf_replay_ans"] == "unknown"
    assert not verification.counterfactual_replay_sufficient


def test_semantic_and_strict_executable_proofs_are_reported_separately():
    world, spec, artifacts = _company_case()
    semantic_only = replace(
        spec,
        sufficient_event_ids=[spec.sufficient_event_ids[-1]],
    )

    verification, notes = verify_question(
        world, semantic_only, artifacts, verification_mode="production"
    )

    assert verification.minimal_sufficient
    assert verification.semantic_sufficient
    assert notes["semantic_min_ans"] == semantic_only.answer
    assert not verification.strict_executable_sufficient
    assert notes["strict_min_ans"] != semantic_only.answer
    assert not verification.all_green()


def test_strict_executable_proof_uses_declared_sufficient_event_closure():
    world, spec, artifacts = _company_case()
    executable = replace(
        spec,
        sufficient_event_ids=[event.id for event in world.events if not event.skipped],
    )

    verification, notes = verify_question(
        world, executable, artifacts, verification_mode="production"
    )

    assert verification.semantic_sufficient
    assert verification.strict_executable_sufficient
    assert notes["strict_proof_scope"] == "declared_sufficient_event_set"


def test_corrupted_essential_text_fails_even_when_hidden_event_ids_remain():
    world, spec, artifacts = _company_case()
    essential = set(spec.essential_artifact_ids)
    corrupted = [
        replace(a, text="This text was corrupted and contains no source evidence.")
        if a.artifact_id in essential
        else a
        for a in artifacts
    ]

    verification, notes = verify_question(
        world, spec, corrupted, verification_mode="production"
    )

    assert verification.minimal_sufficient  # legacy metadata replay is unchanged
    assert not verification.essential_text_grounded
    assert notes["essential_text_issues"]
    assert not verification.all_green()


def test_semantic_gate_rejects_random_text_that_preserves_values_and_event_words():
    world, spec, artifacts = _company_case()
    essential = set(spec.essential_artifact_ids)
    corrupted = []
    for artifact in artifacts:
        if artifact.artifact_id not in essential:
            corrupted.append(artifact)
            continue
        values = " ".join(str(v) for v in artifact.slots.get("ground_values") or [])
        event_words = " ".join(
            event_id.replace("_", " ") for event_id in artifact.reveals_events
        )
        corrupted.append(
            replace(
                artifact,
                text=(
                    "Random unrelated prose preserves copied tokens without asserting "
                    f"the workflow fact. {values} {event_words} filler words only."
                ),
            )
        )

    verification, notes = verify_question(
        world, spec, corrupted, verification_mode="production"
    )

    assert verification.minimal_sufficient
    assert not verification.semantic_sufficient
    assert any(
        issue["kind"] == "text_integrity" for issue in notes["essential_text_issues"]
    )
    assert not verification.all_green()


def test_semantic_gate_rejects_negated_claim_even_with_a_matching_checksum():
    world, spec, artifacts = _company_case()
    essential = set(spec.essential_artifact_ids)
    corrupted = []
    for artifact in artifacts:
        if artifact.artifact_id not in essential:
            corrupted.append(artifact)
            continue
        values = " ".join(str(v) for v in artifact.slots.get("ground_values") or [])
        event_type = str(artifact.slots.get("event_type") or "").replace("_", " ")
        text = (
            f"The {event_type} claim says {values} is not effective or authoritative."
        )
        slots = {
            **artifact.slots,
            "semantic_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        corrupted.append(replace(artifact, text=text, slots=slots))

    verification, notes = verify_question(
        world, spec, corrupted, verification_mode="production"
    )

    assert not verification.semantic_sufficient
    assert any(
        issue["kind"] == "negated_ground_value"
        for issue in notes["essential_text_issues"]
    )


def test_essential_single_document_surface_and_replay_are_auditable():
    world, spec, artifacts = _company_case()

    verification, notes = verify_question(
        world, spec, artifacts, verification_mode="production"
    )

    checks = notes["essential_single_docs"]
    assert {c["artifact_id"] for c in checks} == set(spec.essential_artifact_ids)
    assert any(c["surface_gold_present"] for c in checks)
    assert all("replay_answer" in c for c in checks)
    assert verification.essential_single_doc_insufficient
    assert not verification.essential_surface_gold_free
    assert notes["remove_one_scope"] == "semantic_essential_proof_set"
    assert all(
        {"semantic_ans", "strict_ans"} <= check.keys() for check in notes["remove_one"]
    )


def test_surface_ratio_cache_preserves_gate_without_external_override(monkeypatch):
    from longworld.core.verify import token_overlap

    token_overlap.cache_clear()
    mat = materialize(1, n_parallel=1, n_pulses=0)
    world = mat.worlds["focal"]
    artifacts = mat.artifacts["focal"]
    spec = next(
        query
        for query in mat.queries
        if query.query_type == "current_state" and "decoy" not in query.query_id
    )
    _, cf_artifacts = render_cf_view(world, spec)
    expected, expected_notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=cf_artifacts,
    )

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("surface ratio should be reused")

    monkeypatch.setattr("longworld.core.verify.SequenceMatcher", fail_if_recomputed)
    actual, actual_notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=cf_artifacts,
    )

    assert actual.surface_match == expected.surface_match
    assert actual_notes["surface_ratio"] == expected_notes["surface_ratio"]
    token_overlap.cache_clear()


def test_exhaustive_contiguous_windows_find_an_adjacent_local_proof():
    world, spec, artifacts = _company_case()
    essential = set(spec.essential_artifact_ids)
    local = [a for a in artifacts if a.artifact_id in essential]
    remote = [a for a in artifacts if a.artifact_id not in essential]

    verification, notes = verify_question(
        world, spec, local + remote, verification_mode="production"
    )

    assert not verification.contiguous_windows_insufficient
    assert notes["contiguous_windows"]["4000"]["answer"] == spec.answer
    assert notes["contiguous_windows"]["4000"]["proof_mode"] == "exhaustive_replay"
    assert not verification.all_green()


def test_contiguous_windows_use_context_token_units_not_regex_words() -> None:
    from longworld.core.retrieve import contiguous_windows_insufficient

    world, spec, artifacts = _company_case()
    essential = set(spec.essential_artifact_ids)
    proof = [artifact for artifact in artifacts if artifact.artifact_id in essential]
    assert len(proof) >= 2
    spacer = replace(
        next(
            artifact for artifact in artifacts if artifact.artifact_id not in essential
        ),
        artifact_id="token-unit-spacer",
        text="x" * 20000,
        reveals_events=[],
    )

    insufficient, notes = contiguous_windows_insufficient(
        world,
        spec,
        [proof[0], spacer, *proof[1:]],
        window_sizes=(4000,),
    )

    assert insufficient
    assert notes["4000"]["answer"] != spec.answer


def test_contiguous_windows_prune_replay_only_after_necessity_is_proven(
    monkeypatch,
) -> None:
    from longworld.core.retrieve import contiguous_windows_insufficient

    world, spec, artifacts = _company_case()
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in set(spec.essential_artifact_ids)
    ]
    filler_template = next(
        artifact
        for artifact in artifacts
        if artifact.artifact_id not in set(spec.essential_artifact_ids)
    )
    filler = [
        replace(
            filler_template,
            artifact_id=f"filler-{index}",
            text=(f"unrelated-{index} " * 1000),
            reveals_events=[],
        )
        for index in range(20)
    ]
    calls = 0

    def counted(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return "unknown"

    monkeypatch.setattr("longworld.core.retrieve._replay_answer", counted)
    insufficient, notes = contiguous_windows_insufficient(
        world,
        spec,
        [essential[0], *filler, *essential[1:]],
        window_sizes=(4000,),
        necessary_artifact_ids=set(spec.essential_artifact_ids),
        necessary_set_proven=True,
    )

    assert insufficient
    assert notes["4000"]["checked"] > 0
    assert calls == 0


def test_bm25_and_lexical_tfidf_topk_retrieval_are_not_called_embeddings():
    world, spec, artifacts = _company_case()
    essential = set(spec.essential_artifact_ids)
    query_terms = " ".join(spec.question.split())
    boosted = [
        replace(a, text=f"{query_terms}\n{a.text}")
        if a.artifact_id in essential
        else replace(a, text="unrelated background")
        for a in artifacts
    ]

    verification, notes = verify_question(
        world,
        spec,
        boosted,
        retrieval_top_k=3,
        verification_mode="production",
    )

    assert not verification.bm25_topk_insufficient
    assert not verification.lexical_tfidf_topk_insufficient
    assert notes["bm25_topk"]["answer"] == spec.answer
    assert notes["lexical_tfidf_topk"]["answer"] == spec.answer
    assert "embedding" not in " ".join(notes["lexical_tfidf_topk"]).lower()
    assert not verification.all_green()


def test_dense_embedding_audit_is_required_and_replays_named_rankings():
    world, spec, artifacts = _company_case()

    missing, missing_notes = verify_question(
        world, spec, artifacts, verification_mode="production"
    )
    assert not missing.embedding_topk_insufficient
    assert not missing_notes["embedding_topk"]["audit_available"]

    essential = set(spec.essential_artifact_ids)
    distractors = [
        artifact.artifact_id
        for artifact in artifacts
        if artifact.artifact_id not in essential
    ][:3]
    audited, audited_notes = verify_question(
        world,
        spec,
        artifacts,
        verification_mode="production",
        embedding_ranked_ids=distractors,
        embedding_model_id="example/dense-retriever@revision",
    )
    assert audited.embedding_topk_insufficient
    assert audited_notes["embedding_topk"]["audit_available"]
    assert audited_notes["embedding_topk"]["answer"] != spec.answer


def test_packed_verification_independently_replays_all_view_gates(
    monkeypatch,
) -> None:
    world, spec, artifacts = _company_case()
    _, cf_artifacts = render_cf_view(world, spec)
    base, notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    calls = 0
    from longworld.core import verify as verify_module

    original = verify_module.answer_from_artifacts

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(verify_module, "answer_from_artifacts", counted)
    packed, packed_notes = verify_packed_question(
        world,
        spec,
        artifacts,
        base_verification=base,
        base_notes=notes,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )

    assert calls >= len(spec.essential_artifact_ids) + 8
    assert packed.remove_one_fails == base.remove_one_fails
    assert (
        packed.counterfactual_replay_sufficient == base.counterfactual_replay_sufficient
    )
    assert packed_notes["remove_one"] == notes["remove_one"]


def test_packed_verification_replays_the_exact_counterfactual_dossier() -> None:
    world, spec, artifacts = _company_case()
    _, cf_artifacts = render_cf_view(world, spec)
    base, notes = verify_question(
        world,
        spec,
        artifacts,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    corrupted_cf = [
        replace(artifact, text="Counterfactual dossier text was corrupted.")
        if artifact.artifact_id in spec.essential_artifact_ids
        else artifact
        for artifact in cf_artifacts
    ]

    packed, packed_notes = verify_packed_question(
        world,
        spec,
        artifacts,
        base_verification=base,
        base_notes=notes,
        cf_artifacts=corrupted_cf,
        verification_mode="candidate",
    )

    assert not packed.counterfactual_replay_sufficient
    assert packed_notes["cf_text_issues"]


def test_packed_verification_still_rejects_a_missing_essential() -> None:
    world, spec, artifacts = _company_case()
    base, notes = verify_question(world, spec, artifacts, verification_mode="candidate")
    missing = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id != spec.essential_artifact_ids[0]
    ]

    packed, packed_notes = verify_packed_question(
        world,
        spec,
        missing,
        base_verification=base,
        base_notes=notes,
        verification_mode="candidate",
    )

    assert packed_notes["missing_essential"]
    assert not packed.schema_ok
    assert not packed.full_sufficient
    assert not packed.all_green()
