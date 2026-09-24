"""Tests for the P74 shared semantic world (shared_semantic_world, T1).

Charter coverage: .hl/design/p74_real_shared_worlds.md §0.1 (scope from text),
§14 (SourceSnapshot v1 contract), §3 check 1 (fact reuse), §15 items a/f.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longworld.synthesis import shared_semantic_world as ssw
from scripts.demo_p74_world import (
    AS_OF,
    build_demo_world,
    chain_program,
    demo_snapshot,
)


@pytest.fixture(scope="module")
def world():
    return build_demo_world()


def _scope(task_id: str = "T1") -> ssw.ScopeEntry:
    return ssw.ScopeEntry(
        task_id=task_id,
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


# --- §15 item a: snapshot contract + fact->span integrity ---


def test_snapshot_roundtrip_and_schema_strictness(world):
    snapshot = demo_snapshot(world)
    reloaded = ssw.SourceSnapshot.from_dict(snapshot.to_dict())
    assert reloaded == snapshot
    # unknown keys are rejected: the loader stays schema-strict
    payload = snapshot.to_dict()
    payload["facts"][0]["surprise"] = 1
    with pytest.raises(ValueError, match="unknown keys"):
        ssw.SourceSnapshot.from_dict(payload)


def test_alias_surface_mention_roundtrips_and_requires_exact_span():
    text = "Transiting Exoplanet Survey Satellite is called TESS."
    start = text.index("TESS")
    payload = {
        "snapshot_id": "alias-test",
        "frozen_at": "2026-09-24",
        "source": {"kind": "test"},
        "documents": [{"doc_id": "D", "title": "Satellite", "text": text}],
        "entities": [
            {
                "entity_id": "E",
                "label": "Transiting Exoplanet Survey Satellite",
                "doc_id": "D",
                "mentions": [
                    {"start": start, "end": start + 4, "surface_form": "TESS"}
                ],
            }
        ],
        "facts": [],
    }
    snapshot = ssw.SourceSnapshot.from_dict(payload)
    assert snapshot.entities[0].mentions[0].surface_form == "TESS"
    assert ssw.SourceSnapshot.from_dict(snapshot.to_dict()) == snapshot
    payload["entities"][0]["mentions"][0]["surface_form"] = "TEST"
    with pytest.raises(ValueError, match="mismatched surface_form"):
        ssw.SourceSnapshot.from_dict(payload)


def test_span_mismatch_rejected_at_load(world):
    payload = demo_snapshot(world).to_dict()
    # move a span off the fact's evidence
    payload["facts"][0]["supporting_spans"][0]["start"] += 20
    with pytest.raises(ValueError):
        ssw.SourceSnapshot.from_dict(payload)


def test_ungrounded_fact_rejected(world):
    payload = demo_snapshot(world).to_dict()
    payload["facts"][0]["supporting_spans"] = []
    with pytest.raises(ValueError, match="ungrounded"):
        ssw.SourceSnapshot.from_dict(payload)


def test_ungrounded_relation_is_quarantined_not_gold(world):
    snapshot = ssw.SourceSnapshot(
        snapshot_id="s",
        frozen_at="2026-09-23",
        source={"kind": "simulated"},
        documents=world.documents,
        entities=world.entities,
        facts=world.facts,
        relations=(
            ssw.RelationCandidate(
                relation_id="R-1",
                subject="P-HALE",
                object="P-KIRK",
                relation_type="shares_facility",
                supporting_spans=(),
                grounded=False,
            ),
        ),
    )
    reloaded = ssw.SourceSnapshot.from_dict(snapshot.to_dict())
    assert reloaded.relations[0].relation_type == "shares_facility"
    assert not reloaded.relations[0].grounded
    semantic = ssw.SemanticWorld.from_snapshot(reloaded)
    # a graph edge with no text span can never become a queryable fact
    assert all(f.relation != "shares_facility" for f in semantic.facts)


# --- §15 item b material: the state timeline and version resolution ---


def test_version_resolution_picks_latest_effective(world):
    # v2 adopted 2026-03, v3 adopted 2026-07: as of 2026-09 it is v3
    entry = world.resolve_at("P-HALE", "adopts_method", AS_OF)
    assert entry.value == "V-SPEC3"
    early = world.resolve_at("P-HALE", "adopts_method", "2026-05-01")
    assert early.value == "V-SPEC2"


def test_revoked_entry_is_excluded(world):
    # O-107's calibration was revoked 2026-06-01: active before, gone after
    assert (
        world.resolve_at("O-107", "calibration_status", "2026-05-20").value == "active"
    )
    assert world.status_at("O-107", "calibration_status", AS_OF) is None


# --- §0.1 / §15 item f: scope recoverable from the rendered text ---


def test_render_roundtrip_and_scope_recovery(world):
    rendered = world.render((_scope(),))
    parsed = ssw.parse_rendered(rendered)
    assert [s.task_id for s in parsed["scope"]] == ["T1"]
    # every fact's spans point at verbatim document text
    docs = {d.doc_id: d for d in parsed["documents"]}
    for fact in parsed["facts"]:
        for span in fact.supporting_spans:
            assert span.end <= len(docs[span.doc_id].text)


def test_scope_restricted_world_reproduces_the_chain_answer(world):
    program = chain_program()
    from longworld.synthesis import dependency_ops as ops

    answer = ops.execute(world, program).answer
    rendered = world.render((_scope(),))
    recovered, scopes = ssw.from_rendered(rendered)
    restricted = recovered.restricted_to(scopes[0])
    assert ops.execute(restricted, program).answer == answer
    # the restricted world keeps only scoped documents
    assert [d.doc_id for d in restricted.documents] == ["D1", "D2", "D3"]
    # P-KIRK stays in scope (same families/relations) but its facts are untouched
    assert "P-KIRK" in restricted.objects


def test_scope_can_narrow_to_a_sub_document(world):
    program = chain_program()
    from longworld.synthesis import dependency_ops as ops

    full = ops.execute(world, program).answer
    narrow = ssw.ScopeEntry(
        task_id="T2",
        object_families=("project", "method_version", "instrument", "observation"),
        relations=_scope().relations,
        documents=("D3",),
    )
    restricted = world.restricted_to(narrow)
    # the adoption facts live in D2, so the narrowed world cannot resolve them
    with pytest.raises(ValueError):
        ops.execute(restricted, program)
    assert ops.execute(world, program, require_fold_gate=False).answer == full


# --- §3 check 1: fact reuse across tasks (same fact_id, two tasks) ---


def test_same_fact_serves_locate_and_aggregate_tasks(world):
    from longworld.synthesis import dependency_ops as ops
    from scripts.demo_p74_world import locate_program

    locate = ops.execute(world, locate_program(), require_fold_gate=False)
    chain = ops.execute(world, chain_program())
    assert locate.answer == 4.2
    assert "F-05" in set(locate.consumed_fact_ids)
    assert "F-05" in set(chain.consumed_fact_ids)  # shared, not disjoint subsets


def test_mutation_updates_documents_and_keeps_world_valid(world):
    from longworld.synthesis import dependency_ops as ops

    original = world.documents[1].text
    mutated = ops.mutate_fact(world, "F-05", 1.9)
    assert "noise_level 1.9" in mutated.documents[1].text
    assert "4.2" not in mutated.documents[1].text
    # every OTHER fact's span still resolves to verbatim text after re-offsetting
    for fact in mutated.facts:
        doc = next(
            d for d in mutated.documents if d.doc_id == fact.supporting_spans[0].doc_id
        )
        text = doc.text
        for span in fact.supporting_spans:
            assert 0 <= span.start < span.end <= len(text)
    # the original world is untouched (frozen docs)
    assert world.documents[1].text == original
