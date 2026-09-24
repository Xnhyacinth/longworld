"""Tests for the P74 dual-path renderer (artifact_renderer, T6).

Charter coverage: .hl/design/p74_real_shared_worlds.md §8 (renderer dual path),
§16 T6 row (A path span offsets unchanged; B path span re-validation; same
answer across >=3 genres; no label/context loss).

All tests run on the demo world (scripts.demo_p74_world.build_demo_world) or
an explicit stub — simulated data, no RNG, no model calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import artifact_renderer as ar
from longworld.synthesis import dependency_ops as ops
from longworld.synthesis import shared_semantic_world as ssw
from scripts.demo_p74_world import (
    build_demo_world,
    chain_program,
    locate_program,
)

ROLE = "the duty engineer"
PURPOSE = "keep the shared record auditable"


@pytest.fixture(scope="module")
def world():
    return build_demo_world()


@pytest.fixture(scope="module")
def program():
    return chain_program()


@pytest.fixture(scope="module")
def scope() -> ssw.ScopeEntry:
    return ssw.ScopeEntry(
        task_id="T1",
        object_families=("project", "method_version", "instrument", "observation"),
        relations=(
            "adopts_method",
            "validity_rule",
            "assigns_instrument",
            "noise_level",
            "produces",
            "calibration_status",
            "band",
            "exposure_count",
        ),
        documents=("D2", "D3"),
    )


@pytest.fixture(scope="module")
def plan():
    return ar.ContentPlan("log_entry", ROLE, PURPOSE)


# --- genre set and plan validation ---


def test_genre_set_covers_the_charter_genres():
    # §8: log entry / review comment / change notice / meeting conclusion /
    # technical note — five genres, more than the required three.
    assert set(ar.GENRES) == {
        "log_entry",
        "review_comment",
        "change_notice",
        "meeting_conclusion",
        "technical_note",
    }
    assert len(ar.GENRES) >= 3


def test_templates_cover_every_genre_and_value_type():
    for genre in ar.GENRES:
        assert set(ar.GENRE_TEMPLATES[genre]) == set(ssw.VALUE_TYPES)
        for value_type, template in ar.GENRE_TEMPLATES[genre].items():
            assert template.count("{value}") == 1, (genre, value_type)


def test_content_plan_is_validated():
    with pytest.raises(ValueError, match="genre"):
        ar.ContentPlan("novel", ROLE, PURPOSE)
    with pytest.raises(ValueError, match="role"):
        ar.ContentPlan("log_entry", "  ", PURPOSE)
    with pytest.raises(ValueError, match="purpose"):
        ar.ContentPlan("log_entry", ROLE, "")


# --- path A: frozen-original passthrough, span offsets unchanged ---


def test_path_a_is_verbatim_passthrough(world, scope):
    rendering = ar.render_original(world, (scope,))
    for doc in world.documents:
        original = doc.text
        assert rendering.document_text(doc.doc_id) == original
    # every fact's span resolves to the SAME characters as in the source
    for fact in world.facts:
        for span in fact.supporting_spans:
            source_text = next(
                d for d in world.documents if d.doc_id == span.doc_id
            ).text
            expected = source_text[span.start : span.end]
            assert rendering.span_text(span.doc_id, span.start, span.end) == expected
    # no text mutation anywhere: the source documents are untouched
    for doc in world.documents:
        assert (
            doc.text
            == next(
                d for d in build_demo_world().documents if d.doc_id == doc.doc_id
            ).text
        )


def test_path_a_offset_map_is_exact(world):
    rendering = ar.render_original(world)
    for doc in world.documents:
        layout = next(l for l in rendering.doc_layouts if l.doc_id == doc.doc_id)
        assert rendering.text[layout.text_start : layout.text_end] == doc.text
        assert rendering.text[layout.block_start :].startswith(f"[{doc.doc_id}] ")
    # offsets are EXACT: position p maps to p, not p plus headers or joiners
    layout = rendering._layout("D2")
    for probe in (0, 3, 27, len(world.documents[1].text) - 1):
        start, end = rendering.span_in_joined("D2", probe, probe + 1)
        assert rendering.text[start:end] == world.documents[1].text[probe : probe + 1]
    # the joiner and header layout is as documented
    assert ar.DOC_JOINER == "\n\n"
    blocks = rendering.text.split("=== DOCUMENTS ===\n", 1)[1].split("\n\n")
    assert len(blocks) == len(world.documents)
    assert blocks[1].startswith("[D2] ")
    # out-of-range and unknown-doc are honest errors
    with pytest.raises(ValueError, match="out of range"):
        rendering.span_in_joined("D2", 0, 10**6)
    with pytest.raises(KeyError):
        rendering.span_in_joined("NOPE", 0, 1)


def test_path_a_world_rebuilds_and_answers_identically(world, program, scope):
    rendering = ar.render_original(world, (scope,))
    rebuilt = ar.world_from_original(rendering, world)
    for original, again in zip(world.documents, rebuilt.documents):
        assert original.text == again.text
    assert ops.execute(rebuilt, program).answer == ops.execute(world, program).answer


# --- path B: constrained artifacts with re-validation ---


@pytest.mark.parametrize("genre", ar.GENRES)
def test_path_b_revalidates_every_demo_fact(world, scope, genre):
    plan = ar.ContentPlan(genre, ROLE, PURPOSE)
    rendering = ar.render_artifact(world, plan, (scope,))
    assert rendering.stats["facts_total"] == 39
    assert rendering.stats["facts_revalidated"] == 39
    assert rendering.failures == ()
    # every re-validated span slices to exactly the fact's value surface
    for record in rendering.rendered_facts:
        doc = next(d for d in rendering.documents if d.doc_id == record.doc_id)
        assert doc.text[record.span.start : record.span.end] == _value_surface(
            world, record.fact_id
        )
    # the value surface is the FIRST occurrence in its sentence (unambiguous)
    assert rendering.stats["value_surface_first_occurrence_mismatches"] == 0


def _value_surface(world, fact_id):
    fact = world.facts_by_id[fact_id]
    if fact.value_type == "entity":
        return world.label_of(fact.value)
    return str(fact.value)


def test_path_b_sentences_carry_facts_naturally(world, scope):
    log = ar.render_artifact(
        world, ar.ContentPlan("log_entry", ROLE, PURPOSE), (scope,)
    )
    d2 = next(d for d in log.documents if d.doc_id == "D2").text
    assert "Mapper One noise_level 4.2; logged 2026-05-01." in d2
    assert "spec v3 sets validity_rule noise_level <= 2.5; logged 2026-06-01." in d2
    review = ar.render_artifact(
        world, ar.ContentPlan("review_comment", ROLE, PURPOSE), (scope,)
    )
    d2 = next(d for d in review.documents if d.doc_id == "D2").text
    assert (
        "Reviewer comment: the measured noise level of Mapper One reads 4.2 as of 2026-05-01."
        in d2
    )
    # path B is a different expression of the same facts, not the frozen text
    assert next(d for d in log.documents if d.doc_id == "D3").text != (
        world.documents[2].text
    )


def test_path_b_world_roundtrip_and_scope_recovery(world, program, scope):
    plan = ar.ContentPlan("meeting_conclusion", ROLE, PURPOSE)
    rendering = ar.render_artifact(world, plan, (scope,))
    rebuilt, scopes = ar.world_from_artifact(rendering)
    assert [s.task_id for s in scopes] == ["T1"]
    assert {f.fact_id for f in rebuilt.facts} == {f.fact_id for f in world.facts}
    assert ops.execute(rebuilt, program).answer == ops.execute(world, program).answer
    # restricted_to-style scope recovery works on the artifact rendering
    restricted = rebuilt.restricted_to(scopes[0])
    assert ops.execute(restricted, program).answer == ops.execute(world, program).answer


# --- cross-expression invariance (the T6 acceptance) ---


@pytest.mark.parametrize("genre", ar.GENRES)
def test_cross_expression_invariance(world, program, scope, genre):
    base = ops.execute(world, program).answer
    # (a) path-A rendering world
    a_rendering = ar.render_original(world, (scope,))
    a_world = ar.world_from_original(a_rendering, world)
    assert ops.execute(a_world, program).answer == base
    # (b) path-B rendered world (facts re-pointed, values unchanged)
    b_rendering = ar.render_artifact(
        world, ar.ContentPlan(genre, ROLE, PURPOSE), (scope,)
    )
    b_world, scopes = ar.world_from_artifact(b_rendering)
    assert ops.execute(b_world, program).answer == base
    assert ops.execute(b_world.restricted_to(scopes[0]), program).answer == base
    # a second program (locate) is invariant too
    assert (
        ops.execute(b_world, locate_program(), require_fold_gate=False).answer
        == ops.execute(world, locate_program(), require_fold_gate=False).answer
    )
    # answers equal, spans re-validated, no failures, for every genre
    assert b_rendering.stats["facts_revalidated"] == 39
    assert b_rendering.stats["facts_failed"] == 0


# --- no label/context loss: scope + question identical across paths ---


def test_scope_and_question_identical_across_paths(world, scope, program):
    a = ar.render_original(world, (scope,))
    b = ar.render_artifact(
        world, ar.ContentPlan("technical_note", ROLE, PURPOSE), (scope,)
    )
    # scope sections are byte-equal
    assert a.scope_block == b.scope_block
    assert a.scope_block in a.text
    assert b.scope_block in b.text
    assert ar.parse_scope_section(a.text) == ar.parse_scope_section(b.text) == (scope,)
    # the question renders identically (byte-equal)
    question_a = ops.render_instruction(
        "T1", program, scope, "Compare exposure totals by band."
    )
    question_b = ops.render_instruction(
        "T1", program, scope, "Compare exposure totals by band."
    )
    assert question_a == question_b
    assert "Scope:" in question_a
    # labels survive: every entity label appears in the path-B rendering text
    for entity in world.entities:
        assert entity.label in b.text


def test_rendering_is_deterministic(world, scope):
    a1 = ar.render_original(world, (scope,))
    a2 = ar.render_original(world, (scope,))
    assert a1.text == a2.text
    for genre in ar.GENRES:
        b1 = ar.render_artifact(world, ar.ContentPlan(genre, ROLE, PURPOSE), (scope,))
        b2 = ar.render_artifact(world, ar.ContentPlan(genre, ROLE, PURPOSE), (scope,))
        assert b1.text == b2.text
        assert b1.stats["model_calls"] == 0
        assert b1.stats["rng"] is False


# --- rendering failures are honest, not dropped silently ---


def _stub_world() -> ssw.SemanticWorld:
    entity = ssw.Entity("S-1", "Sensor Unit A", entity_type="sensor", doc_id="DX")
    text = (
        "Sensor Unit A reads pressure 12 and note ok.\n"
        "Sensor Unit A logs a two-line\nvalue spanning lines."
    )
    doc = ssw.Document("DX", "Stub Readings", text)

    def _span(needle: str) -> tuple[ssw.SpanRef, ...]:
        start = text.find(needle)
        assert start >= 0, needle
        return (ssw.SpanRef("DX", start, start + len(needle)),)

    facts = (
        ssw.Fact(
            fact_id="G-1",
            subject="S-1",
            relation="pressure",
            value=12,
            value_type="number",
            time="2026-01-01",
            supporting_spans=_span("12"),
        ),
        ssw.Fact(
            fact_id="G-2",
            subject="S-1",
            relation="note",
            value="ok",
            value_type="string",
            time="2026-01-01",
            supporting_spans=_span("ok"),
        ),
        ssw.Fact(
            fact_id="G-3",
            subject="S-1",
            relation="multiline",
            value="two-line\nvalue",
            value_type="string",
            time="2026-01-01",
            supporting_spans=_span("two-line\nvalue"),
        ),
        ssw.Fact(
            fact_id="G-4",
            subject="S-1",
            relation="empty",
            value="",
            value_type="string",
            time="2026-01-01",
            supporting_spans=_span("and"),
        ),
    )
    return ssw.SemanticWorld((doc,), (entity,), facts)


def test_render_failures_are_listed_not_dropped(scope):
    stub = _stub_world()
    plan = ar.ContentPlan("log_entry", ROLE, PURPOSE)
    rendering = ar.render_artifact(stub, plan, (scope,))
    failed_ids = {record.fact_id for record in rendering.failures}
    assert failed_ids == {"G-3", "G-4"}
    reasons = {record.fact_id: record.reason for record in rendering.failures}
    assert "multi-line" in reasons["G-3"]
    assert "empty" in reasons["G-4"]
    assert rendering.stats["facts_failed"] == 2
    assert rendering.stats["facts_revalidated"] == 2
    # the failed facts are NOT in the rebuilt world, and it still validates
    world_b, _ = ar.world_from_artifact(rendering)
    assert {f.fact_id for f in world_b.facts} == {"G-1", "G-2"}
    # the surviving values are unchanged and re-grounded
    assert world_b.facts_by_id["G-1"].value == 12
    assert world_b.facts_by_id["G-2"].value == "ok"
    doc = world_b.documents[0]
    for fact in world_b.facts:
        span = fact.supporting_spans[0]
        assert span.doc_id == "DX"
        assert doc.text[span.start : span.end] == str(fact.value)
