"""Tests for the P74 dependency operators (dependency_ops, T2).

Charter coverage: .hl/design/p74_real_shared_worlds.md §5 (bound-variable
chain, non-foldability gate, intervention check), §6 first-version direction
(span lineage), §15 items b/c/d/e.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import dependency_ops as ops
from longworld.synthesis import shared_semantic_world as ssw
from scripts.demo_p74_world import (
    AS_OF,
    build_demo_world,
    chain_program,
    locate_program,
)


@pytest.fixture(scope="module")
def world():
    return build_demo_world()


@pytest.fixture(scope="module")
def program():
    return chain_program()


# --- §15 item b: a REAL dependency chain, answer asserted by hand ---


def test_chain_answer_matches_hand_computation(world, program):
    """
    Hand derivation over the demo world, as of 2026-09-01:
    - proj = Hale Atlas Survey
    - spec = adopts_method resolved as-of: spec v3 (v2 superseded 2026-07)
    - rule = spec v3's validity_rule: noise_level <= 2.5
    - instr = assigned instruments: M1 (4.2), M2 (6.5), M3 (2.1)
    - kept = rule applied: only M3
    - obs = M3's observations with active calibration: O-105, O-106
    - aurora = O-105 = 2; boreal = O-106 = 25
    - verdict: 2 < 25 -> LT
    """
    result = ops.execute(world, program)
    assert result.answer["groups"] == {"aurora": 2, "boreal": 25}
    assert result.answer["verdict"] == "LT"
    assert result.answer["items"] == {"aurora": ["O-105"], "boreal": ["O-106"]}


def test_chain_metrics_report_the_four_charter_quantities(world, program):
    metrics = ops.compile_program(program)
    assert metrics["program_length"] == 8
    assert metrics["dependency_depth"] == 7
    # state transitions: two resolve_version + one status-checking join
    assert metrics["state_transition_depth"] == 3
    assert metrics["non_foldable"] is True
    assert metrics["key_bindings"]  # at least one computed key binding


def test_revoked_observation_is_excluded_even_under_the_loose_rule(world):
    # v2 rule (<= 4.5) admits M1; then the join must drop O-107 (revoked),
    # leaving O-101 (30 aurora) and O-102 (4 boreal).
    loose = {
        "steps": [
            {
                "op": "bind",
                "out": "proj",
                "family": "project",
                "label": "Hale Atlas Survey",
            },
            {
                "op": "resolve_version",
                "out": "spec",
                "subject": "$proj",
                "relation": "adopts_method",
                "at": "2026-05-01",
            },
            {
                "op": "resolve_version",
                "out": "rule",
                "subject": "$spec",
                "relation": "validity_rule",
                "at": "2026-05-01",
            },
            {
                "op": "follow_relation",
                "out": "instr",
                "subject": "$proj",
                "relation": "assigns_instrument",
            },
            {"op": "apply_rule", "out": "kept", "rule": "$rule", "over": "$instr"},
            {
                "op": "join_on_bound_result",
                "out": "obs",
                "left": "$kept",
                "relation": "produces",
                "status_relation": "calibration_status",
                "at": AS_OF,
            },
            {"op": "group", "out": "byband", "items": "$obs", "key": "band"},
            {
                "op": "aggregate",
                "out": "res",
                "groups": "$byband",
                "how": "sum",
                "field": "exposure_count",
                "compare": True,
            },
        ],
        "return": "res",
    }
    result = ops.execute(world, loose)
    # v2 rule (<= 4.5): M1 (4.2) AND M3 (2.1) pass, so obs-105/obs-106 stay;
    # only the revoked obs-107 drops out of M1's three observations.
    assert result.answer["items"] == {
        "aurora": ["O-101", "O-105"],
        "boreal": ["O-102", "O-106"],
    }
    assert result.answer["groups"] == {"aurora": 32, "boreal": 29}


# --- §6 first version: span lineage, not generator internals ---


def test_proof_is_ordered_and_span_bearing(world, program):
    result = ops.execute(world, program)
    steps = [item.step for item in result.proof]
    assert steps == sorted(steps)
    fact_items = [item for item in result.proof if item.kind == "fact"]
    assert fact_items
    docs = {d.doc_id: d for d in world.documents}
    for item in fact_items:
        assert item.spans
        for span, text in zip(item.spans, item.span_texts):
            assert docs[span.doc_id].text[span.start : span.end] == text


def test_consumed_facts_cover_the_whole_chain(world, program):
    result = ops.execute(world, program)
    consumed = set(result.consumed_fact_ids)
    # the adoption pair (resolution input), the winning rule, the noise facts,
    # the assignments, produces, calibration statuses and the aggregates
    for fact_id in (
        "F-01",
        "F-02",
        "F-04",
        "F-05",
        "F-06",
        "F-07",
        "F-08",
        "F-09",
        "F-10",
        "F-23",
        "F-24",
        "F-30",
        "F-31",
        "F-37",
        "F-38",
    ):
        assert fact_id in consumed, fact_id
    # the loser instruments' observations are never consumed
    assert "F-19" not in consumed
    assert "F-25" not in consumed
    assert "F-32" not in consumed  # revoked: excluded by the join
    # F-03 (the superseded v2 rule) is NOT consumed: resolution read only the
    # adoption PAIR (F-01, F-02); the winner's own rule is looked up next
    assert "F-03" not in consumed


def test_superseded_fact_is_consumed_for_resolution(world, program):
    result = ops.execute(world, program)
    # resolve_version reads the whole (subject, relation) timeline, so both
    # adoption facts appear in the lineage even though only v3 is effective
    assert {"F-01", "F-02"} <= set(result.consumed_fact_ids)


# --- §15 item c: interventions, both directions ---


def test_upstream_mutation_changes_the_answer(world, program):
    # Repoint the v3 adoption at spec v2: the chain now applies the loose rule,
    # M1 passes, and its observations (minus the revoked O-107) flip the verdict
    report = ops.check_intervention(world, program, "F-02", "V-SPEC2", relation=True)
    assert report["fact_in_proof"] is True
    assert report["answer_changed"] is True
    assert report["base_answer"]["verdict"] == "LT"
    assert report["mutated_answer"]["verdict"] == "GT"
    assert report["mutated_answer"]["groups"] == {"aurora": 32, "boreal": 29}


def test_unrelated_mutation_does_not_change_the_answer(world, program):
    # obs-103 belongs to M2, which the v3 rule filters out before the join
    report = ops.check_intervention(world, program, "F-21", 99)
    assert report["fact_in_proof"] is False
    assert report["answer_changed"] is False


def test_unrelated_relation_mutation_does_not_change_the_answer(world, program):
    # P-KIRK's instrument assignment is a relation edge on the other project:
    # same relation kind, same document, entirely outside the chain's lineage
    report = ops.check_intervention(world, program, "F-11", "I-M1", relation=True)
    assert report["fact_in_proof"] is False
    assert report["answer_changed"] is False
    # and the chain still solves on the mutated world
    assert report["mutated_answer"]["groups"] == {"aurora": 2, "boreal": 25}


def test_downstream_rule_mutation_changes_the_answer(world, program):
    # Loosen the effective rule's threshold: M1 (4.2) joins M3
    report = ops.check_intervention(world, program, "F-04", "noise_level <= 4.5")
    assert report["answer_changed"] is True
    assert report["mutated_answer"]["items"] == {
        "aurora": ["O-101", "O-105"],
        "boreal": ["O-102", "O-106"],
    }


def test_mutation_edits_the_document_text_in_place(world):
    mutated = ops.mutate_fact(world, "F-04", "noise_level <= 4.5")
    assert "noise_level <= 4.5" in mutated.documents[1].text
    # spans after the edit still point at verbatim text (re-offsetting held)
    docs = {d.doc_id: d for d in mutated.documents}
    for fact in mutated.facts:
        for span in fact.supporting_spans:
            text = docs[span.doc_id].text
            assert 0 <= span.start < span.end <= len(text)


# --- §15 item d: the non-foldability gate ---


def test_flat_constant_program_is_rejected(world):
    flat = {
        "steps": [
            {"op": "bind", "out": "a", "subject": "I-M1", "relation": "noise_level"},
            {"op": "bind", "out": "b", "subject": "I-M2", "relation": "noise_level"},
        ],
        "return": "b",
    }
    # it passes the def-use invariant only if the second step consumes a binding
    flat_consumes = {
        "steps": [
            {"op": "bind", "out": "i", "family": "instrument", "label": "Mapper Two"},
            {"op": "bind", "out": "b", "subject": "$i", "relation": "noise_level"},
        ],
        "return": "b",
    }
    with pytest.raises(ValueError, match="folds to a flat AND"):
        ops.compile_program(flat_consumes)
    with pytest.raises(ValueError, match="consumes no upstream"):
        ops.compile_program(flat)
    # the chain program passes both gates
    assert ops.compile_program(chain_program())["non_foldable"] is True


def test_rule_from_a_literal_is_not_a_dependency(world):
    # the rule string is a literal, not a bound variable: renamed constant
    literal_rule = {
        "steps": [
            {"op": "bind", "out": "i", "family": "instrument", "label": "Mapper One"},
            {
                "op": "follow_relation",
                "out": "f",
                "subject": "$i",
                "relation": "produces",
            },
        ],
        "return": "f",
    }
    # with the gate on, a literal-follow program is rejected (it must not slip
    # through as an H-chain); with the gate off its metrics honestly report
    # non-foldable: False — a data-position binding is a renamed constant.
    with pytest.raises(ValueError, match="folds"):
        ops.compile_program(literal_rule)
    metrics = ops.compile_program(literal_rule, require_fold_gate=False)
    assert metrics["non_foldable"] is False


def test_gate_is_opt_in_for_locate_tasks(world):
    # a locate task is a legal constant program; the chain gate is for H-family
    result = ops.execute(world, locate_program(), require_fold_gate=False)
    assert result.answer == 4.2
    with pytest.raises(ValueError, match="folds"):
        ops.execute(world, locate_program())


# --- §15 item e: fact reuse across tasks ---


def test_shared_fact_id_across_two_proofs(world):
    locate = ops.execute(world, locate_program(), require_fold_gate=False)
    chain = ops.execute(world, chain_program())
    shared = set(locate.consumed_fact_ids) & set(chain.consumed_fact_ids)
    assert "F-05" in shared  # M1's noise: locate answers it, the chain filters on it


def test_make_task_carries_proof_and_scope(world):
    scope = ssw.ScopeEntry(
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
        documents=("D1", "D2", "D3"),
    )
    task = ops.make_task(
        world,
        "T1",
        "dependency_chain",
        chain_program(),
        scope,
        "Compare exposure totals by band.",
    )
    assert task["answer"]["verdict"] == "LT"
    assert task["consumed_fact_ids"]
    assert task["proof"]
    assert task["honesty"]["source_kind"] == "simulated"
    assert "Scope:" in task["instruction"]
    assert "documents" in task["instruction"]


def test_describe_program_and_instruction_mention_scope(world):
    text = ops.render_instruction(
        "T1",
        chain_program(),
        ssw.ScopeEntry("T1", ("instrument",), ("noise_level",), ("D2",)),
        "goal",
    )
    assert "Scope: object families ['instrument']" in text
    assert "documents ['D2']" in text
    assert "LET proj" in text


def test_describe_program_renders_entity_id_only_bind():
    """entity_id-only bind steps (bridge chain constructors emit them) must
    render, not KeyError — execute() accepts the shape (INT seam 2)."""
    program = {
        "steps": [
            {"op": "bind", "out": "v0", "entity_id": "O-obs-101"},
            {
                "op": "resolve_version",
                "out": "v1",
                "subject": "$v0",
                "relation": "band",
                "at": "2026-09-01",
            },
        ],
        "return": "v1",
    }
    text = ops.describe_program(program)
    assert "O-obs-101" in text
    assert "band" in text


def test_mutate_fact_drops_clobbered_mentions_not_the_world():
    """A mutation whose text edit clobbers a label mention must still build a
    valid world (drop the broken mention) instead of failing validation —
    real snapshots carry thousands of mentions (INT seam 3)."""
    from longworld.synthesis.shared_semantic_world import (
        Document,
        Entity,
        EntityMention,
        Fact,
        SemanticWorld,
        SpanRef,
    )

    doc = Document(
        doc_id="D1", title="t", text="Kea is flightless and notable.", sections=()
    )
    entity = Entity(
        entity_id="E-1",
        label="Kea",
        aliases=(),
        external_qid=None,
        doc_id="D1",
        mentions=(EntityMention(0, 3),),
        entity_type="bird",
    )
    fact = Fact(
        fact_id="F-1",
        subject="E-1",
        relation="status",
        value="flightless",
        value_type="string",
        unit=None,
        time=None,
        version=None,
        qualifiers={},
        supporting_spans=(SpanRef("D1", 7, 17),),
        alternative_spans=(),
        source_hash="h",
    )
    world = SemanticWorld((doc,), (entity,), (fact,))
    # edit the fact's span: the text inside the mention's window changes so
    # the mention no longer carries the label verbatim
    mutated = ops.mutate_fact(world, "F-1", "notable")
    assert mutated.lookup_fact("E-1", "status") is not None
    mentions = [m for e in mutated.entities for m in e.mentions]
    # the clobbered mention is dropped, the entity survives
    assert mentions == [] or all(
        "Kea"
        in next(d.text for d in mutated.documents if d.doc_id == "D1")[m.start : m.end]
        for m in mentions
    )


def test_mutate_fact_repositions_alias_surface_mention():
    from longworld.synthesis.shared_semantic_world import (
        Document,
        Entity,
        EntityMention,
        Fact,
        SemanticWorld,
        SpanRef,
    )

    text = "TESS status active. Later, TESS remained visible."
    fact_start = text.index("active")
    alias_start = text.index("TESS", fact_start)
    world = SemanticWorld(
        (Document("D", "Satellite", text),),
        (
            Entity(
                "E",
                "Transiting Exoplanet Survey Satellite",
                doc_id="D",
                mentions=(EntityMention(alias_start, alias_start + 4, "TESS"),),
            ),
        ),
        (
            Fact(
                "F",
                "E",
                "status",
                "active",
                "string",
                supporting_spans=(SpanRef("D", fact_start, fact_start + 6),),
            ),
        ),
    )
    mutated = ops.mutate_fact(world, "F", "inactive")
    mention = mutated.entities[0].mentions[0]
    assert mention.surface_form == "TESS"
    assert mention.start == alias_start + 2
    assert mutated.documents[0].text[mention.start : mention.end] == "TESS"
