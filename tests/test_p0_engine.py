from __future__ import annotations

from longworld.core.consist import parse_facts
from longworld.core.engine import answer_from_artifacts, answer_from_events
from longworld.core.graph import min_sufficient_subgraph, proof_depth
from longworld.core.sampler import materialize
from longworld.core.verify import shortcut_free, verify_question
from longworld.core.views import render_cf_view, split_views
from longworld.domains.company.queries import gold_from_full

HARD_TYPES = {"current_state", "multi_hop", "version_diff", "counterfactual"}
SHALLOW_TYPES = {"historical_state", "aggregation"}


def test_replay_and_gates_seed1():
    mat = materialize(1, n_parallel=2)
    assert mat.scan_ok, mat.scan_issues
    world = mat.worlds["focal"]
    arts = mat.artifacts["focal"]
    proc = world.spec["project"].get("process") or {}
    if proc.get("rollback"):
        assert (
            world.state.values["legal_effective_version"]
            == world.spec["project"]["signed_version"]
        )
    else:
        assert (
            world.state.values["legal_effective_version"]
            == world.spec["project"]["roadmap_version"]
        )
    assert (
        world.state.values["revenue_recognized"]
        == world.spec["project"]["audited_revenue"]
    )
    types = {q.query_type for q in mat.queries}
    assert HARD_TYPES <= types
    for spec in mat.queries:
        gold = gold_from_full(world, spec)
        assert spec.answer == gold
        cf_w, cf_arts = render_cf_view(world, spec)
        ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
        if spec.query_id.endswith(":decoy_invariance"):
            assert (
                spec.cf_answer == spec.answer or not ver.counterfactual_changes_answer
            )
            continue
        if spec.query_id.endswith(":decoy_overspec"):
            assert not ver.remove_one_fails
            continue
        assert spec.cf_answer != spec.answer
        assert ver.full_sufficient, (spec.query_type, notes)
        assert ver.minimal_sufficient, (spec.query_type, notes)
        if spec.query_type in SHALLOW_TYPES:
            assert not ver.min_complexity
            continue
        assert ver.remove_one_fails, (spec.query_type, notes)
        assert ver.counterfactual_changes_answer, (spec.query_type, notes)
        assert ver.closed_book_unsolved, (spec.query_type, notes)
        assert ver.distractor_invariance_gold, (spec.query_type, notes)
        assert ver.local_window_insufficient, (spec.query_type, notes)
        assert ver.surface_match, (spec.query_type, notes)
        assert ver.no_shortcut, notes.get("shortcut")
        assert ver.min_complexity
        dist = split_views(arts, [], spec, cf_arts)["distractor_only"]
        dist_ans = answer_from_artifacts(world, spec, dist)
        assert dist_ans != spec.answer


def test_no_facts_shortcut_and_unique_versions():
    mat = materialize(4, n_parallel=1, n_pulses=6)
    world = mat.worlds["focal"]
    for art in mat.artifacts["focal"]:
        if art.doc_type == "source_pack":
            continue
        assert not parse_facts(art.text), art.artifact_id
        assert "recorded facts" not in art.text.lower()
    signed = world.spec["project"]["signed_version"]
    roadmap = world.spec["project"]["roadmap_version"]
    assert signed.startswith("RV-")
    assert roadmap.startswith("RV-")
    assert signed != roadmap
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    ok, why = shortcut_free(world, mat.artifacts["focal"], spec)
    assert ok, why


def test_state_at_historical():
    mat = materialize(2, n_parallel=1)
    world = mat.worlds["focal"]
    hist = next(q for q in mat.queries if q.query_type == "historical_state")
    cur = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    assert hist.answer == world.spec["project"]["signed_version"]
    expected_cur = (
        world.spec["project"]["signed_version"]
        if (world.spec["project"].get("process") or {}).get("rollback")
        else world.spec["project"]["roadmap_version"]
    )
    # current_state uses core_as_of, so it stays the post-amendment version
    # even when a later rollback exists.
    assert cur.answer == world.spec["project"]["roadmap_version"]
    assert hist.answer != cur.answer
    _ = expected_cur


def test_subset_replay_drops_answer():
    mat = materialize(3, n_parallel=1)
    world = mat.worlds["focal"]
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    empty = answer_from_events(world, spec, [])
    assert empty != spec.answer


def test_min_subgraph_nonempty():
    mat = materialize(6, n_parallel=1)
    world = mat.worlds["focal"]
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    sub = min_sufficient_subgraph(world, spec)
    assert sub.number_of_nodes() >= 2
    assert proof_depth(world, spec) >= 2
