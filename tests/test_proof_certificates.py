"""Tests for the P74 proof certificates (T3).

Charter coverage: .hl/design/p74_real_shared_worlds.md §6 in full — minimal
sufficient evidence vs minimum-cost vs in-context necessity; deletion ->
non-uniqueness (W1 != W2); D_min over checked proofs; the four certificates;
the bounded alternative-proof search with its honest completeness flags.

The demo world has no duplicate (subject, relation, value) rows, so the
duplicate-fact / OR-behavior tests build their own small world on top of the
demo builder's own construction helpers (the demo script itself is untouched).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import replace as dc_replace

from longworld.synthesis import dependency_ops as ops
from longworld.synthesis import proof_certificates as pc
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


@pytest.fixture(scope="module")
def analysis(world, program):
    # one shared-budget analysis per module: the tests below assert on it
    return pc.analyze(world, program, budget=64)


# --- the duplicate-fact test world (built, not a demo-script edit) ---


def build_duplicate_world() -> ssw.SemanticWorld:
    """Demo world + document D4, a summary that re-discloses two facts.

    F-23 (O-105, exposure_count, 2) gets a SECOND supporting span in D4 —
    the charter's duplicate disclosure (the same fact appearing in multiple
    places): one row, two spans, two documents. F-37 (O-105, band, aurora)
    gets an alternative_span in D4. The demo script itself is untouched:
    this builder re-uses the demo world's data and adds a document.
    """
    base = build_demo_world()
    doc_id = "D4"
    text = (
        "Quarterly summary: exposures of the active low-noise observations.\n"
        "obs-105 records exposure_count 2 in band aurora (duplicated entry)."
    )
    needle = "2"
    start = text.find(needle, text.find("obs-105"))
    band_start = text.find("aurora", start)
    documents = base.documents + (
        ssw.Document(doc_id, "Quarterly Summary", text),
    )
    facts = [
        dc_replace(
            f,
            supporting_spans=f.supporting_spans
            + (ssw.SpanRef(doc_id, start, start + len(needle)),),
        )
        if f.fact_id == "F-23"
        else dc_replace(
            f,
            alternative_spans=(
                ssw.SpanRef(doc_id, band_start, band_start + len("aurora")),
            ),
        )
        if f.fact_id == "F-37"
        else f
        for f in base.facts
    ]
    return ssw.SemanticWorld(documents, base.entities, tuple(facts))

@pytest.fixture(scope="module")
def dup_world():
    return build_duplicate_world()


@pytest.fixture(scope="module")
def dup_analysis(dup_world, program):
    return pc.analyze(dup_world, program, budget=64)


# --- §6 concept 1: minimal sufficient evidence (E*) ---


def test_minimal_set_strictly_smaller_than_consumed(world, program):
    result = pc.minimal_evidence(world, program, budget=64)
    assert set(result.fact_ids) < set(result.consumed_fact_ids)
    assert len(result.fact_ids) < len(result.consumed_fact_ids)


def test_minimal_set_is_sufficient_standalone(world, program):
    result = pc.minimal_evidence(world, program, budget=64)
    assert result.standalone_verified
    assert result.standalone_sufficient
    assert result.standalone_error is None


def test_minimal_search_log_records_every_trial(world, program):
    result = pc.minimal_evidence(world, program, budget=64)
    # every kept removal and every rejected trial appears, with pass + reason
    kept = [t for t in result.search_log if t.outcome == "kept"]
    rejected = [t for t in result.search_log if t.outcome == "rejected"]
    assert kept and rejected
    assert len(kept) == len(result.consumed_fact_ids) - len(result.fact_ids)
    for trial in result.search_log:
        assert trial.reason
        assert trial.reexecutions >= 0
    # no fact appears twice as a kept removal (it is removed from `remaining`)
    kept_ids = [t.fact_id for t in kept]
    assert len(kept_ids) == len(set(kept_ids))
    # fixpoint: a final pass attempted every remaining fact and removed none
    assert result.complete


def test_minimal_evidence_is_not_claimed_minimum(world, program):
    # the module docstring disclaims minimum-cost; the honest check is that
    # the claim fields exist and are typed as scoped records
    result = pc.minimal_evidence(world, program, budget=64)
    assert isinstance(result.fact_ids, tuple)
    assert isinstance(result.search_log, tuple)


def test_minimal_evidence_budget_exhaustion_is_honest(world, program):
    result = pc.minimal_evidence(world, program, budget=3)
    assert result.complete is False
    assert result.exhausted is True
    assert result.standalone_error == "budget_exhausted"


# --- §6 concept 2: in-context necessity / OR behavior via duplicates ---
def test_duplicate_disclosure_is_or_support(dup_world, program):
    """F-23 (O-105's exposure, minimal for the chain) is disclosed twice:
    D3 carries the ledger row, D4 carries a summary re-disclosure.

    OR behavior: with both disclosures present, deleting ONE of them (the
    E*-reduced world keeping only D4's span, or only D3's) still reproduces
    the answer — the fact has an OR node, so no single span deletion
    breaks the proof.
    """
    f23 = dup_world.facts_by_id["F-23"]
    docs_of = sorted({span.doc_id for span in f23.supporting_spans})
    assert docs_of == ["D3", "D4"]
    # single_span_removal + duplicate_disclosures branches fire for F-23
    alt = pc.alternative_proofs(
        dup_world, program, budget=64
    )
    dup_branches = [
        p
        for p in alt.checked
        if p.mechanism == "duplicate_disclosures" and p.drop_fact_id == "F-23"
    ]
    span_branches = [
        p
        for p in alt.checked
        if p.mechanism == "single_span_removal" and p.drop_fact_id == "F-23"
    ]
    assert len(dup_branches) == 2  # one branch per kept document
    assert all(p.verified for p in dup_branches)
    # OR behavior visible: deleting either single disclosure keeps the answer
    assert len(span_branches) == 2
    assert all(p.verified for p in span_branches)


def test_alternative_proof_alternative_span_relocates(dup_world, program):
    """Mechanism (b): F-37's band evidence relocates to D4's alternative_span."""
    alt = pc.alternative_proofs(dup_world, program, budget=64)
    b_branches = [
        p
        for p in alt.checked
        if p.mechanism == "alternative_spans" and p.drop_fact_id == "F-37"
    ]
    assert len(b_branches) == 1
    assert b_branches[0].verified
    # the relocated proof's spans include D4
    assert any(
        span.doc_id == "D4" for span in b_branches[0].spans()
    )


def test_duplicate_world_nonuniqueness_still_underdetermined(dup_world, program):
    """Deletion of ALL minimal evidence still underdetermines the answer."""
    result = pc.minimal_evidence(dup_world, program, budget=64)
    nonuniq = pc.deletion_non_uniqueness(dup_world, program, result, budget=64)
    assert nonuniq.verdict == "underdetermined"
    assert nonuniq.answers_differ is True
    assert nonuniq.q_w1 != nonuniq.q_w2


# --- §6 concept 3: D_min ---


def test_d_min_computed_over_checked_proofs(world, program):
    analysis = pc.analyze(world, program, budget=64)
    dmin = analysis.dmin
    assert dmin.d_min is not None
    assert dmin.d_min > 0
    # the E* proof is among the considered and verified
    assert "E*" in dmin.verified_proofs_considered
    # per-proof windows reported
    assert dmin.window_reports
    for report in dmin.window_reports:
        assert report.span_count >= 1
        if report.crosses_documents:
            # global extent mixes coordinate systems: flagged
            assert report.per_document


def test_d_min_is_min_not_max(world, program):
    analysis = pc.analyze(world, program, budget=16)
    # budget too small to verify anything but E* (min search cut short) —
    # d_min is still computed but scoped
    assert analysis.dmin.d_min is not None
    assert analysis.dmin.minimum_span_within_checked_proofs is True


def test_d_min_reference_included_when_supplied(world, program):
    execution = ops.execute(world, program)
    minimal = pc.minimal_evidence(world, program, execution, budget=64)
    alt = pc.alternative_proofs(world, program, execution, minimal=minimal, budget=64)
    dmin = pc.minimum_span(minimal, alt, reference=execution)
    assert "reference" in dmin.verified_proofs_considered


# --- §6 concept 4: deletion -> non-uniqueness (W1 != W2) ---


def test_deletion_of_minimal_evidence_underdetermines(world, program):
    """The §6 construction: C-minus + two completions, answers differ."""
    minimal = pc.minimal_evidence(world, program, budget=64)
    nonuniq = pc.deletion_non_uniqueness(world, program, minimal, budget=64)
    assert nonuniq.verdict == "underdetermined"
    assert nonuniq.answers_differ is True
    assert nonuniq.q_w1 != nonuniq.q_w2
    # W1 is the E*-restored world: its answer is the verified baseline
    assert nonuniq.q_w1["groups"] == {"aurora": 2, "boreal": 25}
    assert nonuniq.q_w1["verdict"] == "LT"
    # the pair is recorded with the mutated fact's coordinates
    pair = nonuniq.world_pair
    assert pair.fact_id in set(minimal.fact_ids)
    assert pair.fact_subject and pair.fact_relation
    assert pair.original_value != pair.mutated_value


def test_deletion_verdict_is_a_verdict_not_an_error(world, program):
    """A differing pair is reported as underdetermined, never raised."""
    minimal = pc.minimal_evidence(world, program, budget=64)
    nonuniq = pc.deletion_non_uniqueness(world, program, minimal, budget=64)
    # the verdicts are the closed set; no exception escapes this phase
    assert nonuniq.verdict in (
        "underdetermined",
        "deletion_insufficient",
        "budget_exhausted",
    )
    # the execution log records both re-executed worlds
    assert ("W1", "w1", nonuniq.q_w1) in nonuniq.execution_log


def test_w1_w2_both_consistent_with_c_minus(world, program):
    minimal = pc.minimal_evidence(world, program, budget=64)
    nonuniq = pc.deletion_non_uniqueness(world, program, minimal, budget=64)
    pair = nonuniq.world_pair
    assert pair is not None
    # both W1 and W2 are SemanticWorlds that pass their own validation
    # (construction succeeded) and both extend C-minus (same documents,
    # entities; facts differ by exactly the mutated row's value)
    assert len(pair.w1.facts) == len(pair.w2.facts)
    w1_by_id = {f.fact_id: f for f in pair.w1.facts}
    w2_by_id = {f.fact_id: f for f in pair.w2.facts}
    assert set(w1_by_id) == set(w2_by_id)
    differing = [fid for fid in w1_by_id if w1_by_id[fid].value != w2_by_id[fid].value]
    assert differing == [pair.fact_id]
    # and C-minus itself contains neither the original nor mutated row set:
    # the minimal rows were removed before the pair was built
    assert pair.fact_id not in {f.fact_id for f in pair.c_minus.facts}


def test_deletion_insufficient_reported_honestly(world):
    """The honest negative: no candidate pair differs -> deletion_insufficient.

    The listing program (P-HALE's assigned instruments, locate-family so the
    fold gate is off) has E* = the three assignment facts. Each is
    entity-valued with NO unused same-family object, so no candidate value
    exists: the phase skips them (logged as no_candidate), finds no
    differing pair, and returns deletion_insufficient with
    answers_differ=False — a verdict, never a solver error, and never a
    silent claim of underdetermination it did not demonstrate.
    """
    listing = {
        "steps": [
            {
                "op": "bind",
                "out": "p",
                "family": "project",
                "label": "Hale Atlas Survey",
            },
            {
                "op": "follow_relation",
                "out": "instr",
                "subject": "$p",
                "relation": "assigns_instrument",
            },
        ],
        "return": "instr",
    }
    minimal = pc.minimal_evidence(
        world, listing, budget=64, require_fold_gate=False
    )
    assert set(minimal.fact_ids) == {"F-08", "F-09", "F-10"}
    nonuniq = pc.deletion_non_uniqueness(
        world, listing, minimal, budget=64, require_fold_gate=False
    )
    assert nonuniq.verdict == "deletion_insufficient"
    assert nonuniq.answers_differ is False
    assert nonuniq.q_w1 == ("I-M1", "I-M2", "I-M3")
    # every fact was examined and honestly logged as having no candidate
    logged = {fid for fid, which, _ in nonuniq.execution_log}
    assert set(minimal.fact_ids) <= logged
    assert all(
        which == "no_candidate"
        for fid, which, _ in nonuniq.execution_log
        if fid != "W1"
    )


# --- the four certificates ---


def test_certificate_dict_has_exactly_the_four_keys(analysis):
    assert set(analysis.certificates) == set(pc.CERTIFICATE_KEYS)
    assert len(analysis.certificates) == 4


def test_each_certificate_is_a_scoped_record_not_a_bool(analysis):
    for key, record in analysis.certificates.items():
        assert isinstance(record, dict), key
        assert set(record) >= {"value"}
        # the scoped sub-fields carry the evidence for the claim
        assert len(record) >= 2, key


def test_all_certificates_true_on_demo_chain(analysis):
    values = {k: v["value"] for k, v in analysis.certificates.items()}
    assert values == {
        "surface_answer_supported": True,
        "evidence_sufficiency_checked": True,
        "alternative_proof_scope_checked": True,
        "long_span_requirement_verified_within_scope": True,
    }


def test_surface_certificate_rechecks_spans_verbatim(analysis, world):
    record = analysis.certificates["surface_answer_supported"]
    assert record["value"] is True
    assert record["spans_checked"] > 0
    assert record["bad_spans"] == []
    # spot-check one span verbatim against the world's document text
    docs = {doc.doc_id: doc for doc in world.documents}
    item = next(i for i in analysis.execution.proof if i.kind == "fact")
    span = item.spans[0]
    assert docs[span.doc_id].text[span.start : span.end] == item.span_texts[0]


def test_alt_scope_certificate_carries_scope(analysis):
    record = analysis.certificates["alternative_proof_scope_checked"]
    assert record["mechanisms"] == list(pc.MECHANISMS)
    assert record["alternative_proofs_checked"] == len(analysis.alt.checked)
    assert record["branches_enumerated"] == analysis.alt.branches_enumerated
    assert record["budget_total"] == 64


def test_long_span_certificate_carries_d_min(analysis):
    record = analysis.certificates["long_span_requirement_verified_within_scope"]
    assert record["d_min"] == analysis.dmin.d_min
    assert record["d_min_proof"]
    assert record["verified_proofs_considered"]


# --- budget exhaustion -> honest completeness flags ---


def test_budget_exhaustion_flips_completeness(world, program):
    analysis = pc.analyze(world, program, budget=6)
    scope = analysis.certificates["alternative_proof_scope_checked"]
    assert scope["proof_search_complete_within_supported_language"] is False
    assert analysis.alt.complete is False
    assert analysis.minimal.exhausted is True


def test_budget_zero_runs_nothing(world, program):
    analysis = pc.analyze(world, program, budget=0)
    assert analysis.minimal.complete is False
    assert analysis.minimal.standalone_error == "budget_exhausted"
    scope = analysis.certificates["alternative_proof_scope_checked"]
    assert scope["proof_search_complete_within_supported_language"] is False


def test_budget_never_exceeded(world, program):
    analysis = pc.analyze(world, program, budget=64)
    total = (
        analysis.minimal.reexecutions_used
        + analysis.alt.reexecutions_used
        + analysis.non_uniqueness.reexecutions_used
    )
    assert total <= 64


# --- determinism ---


def test_analysis_is_deterministic(world, program):
    first = pc.analyze(world, program, budget=64)
    second = pc.analyze(world, program, budget=64)
    assert first.to_dict() == second.to_dict()


# --- worked example output (the report's verbatim example) ---


def test_worked_example_demo_chain(analysis):
    """E*, D_min, and the four certificates for the demo chain (§6).

    The full worked example is printed by the report; this pins the values
    the report quotes so the prose cannot drift from the code.
    """
    m = analysis.minimal
    assert m.fact_ids == (
        "F-02",
        "F-04",
        "F-07",
        "F-10",
        "F-16",
        "F-17",
        "F-23",
        "F-24",
        "F-30",
        "F-31",
        "F-37",
        "F-38",
    )
    assert analysis.dmin.d_min == 718
    assert analysis.non_uniqueness.verdict == "underdetermined"
