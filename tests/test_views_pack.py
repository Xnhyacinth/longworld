import random

from longworld.core.causal import build_causal_graph
from longworld.core.graph import random_walk_event_ids
from longworld.core.pack import estimate_tokens, pack_view
from longworld.core.sampler import materialize
from longworld.core.views import render_cf_view, split_views, view_answer


def test_four_views_and_unique_pack():
    mat = materialize(5, n_parallel=2, n_pulses=10)
    world = mat.worlds["focal"]
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    _, cf_arts = render_cf_view(world, spec)
    par = []
    for k, arts in mat.artifacts.items():
        if k != "focal":
            par.extend(arts)
    views = split_views(mat.artifacts["focal"], par, spec, cf_arts)
    assert set(views) == {
        "full",
        "minimal",
        "cf",
        "distractor_only",
        "trajectory",
    }
    assert view_answer(spec, "distractor_only") == "unanswerable"
    assert view_answer(spec, "cf") == spec.cf_answer
    extra = []
    for seed in range(20, 28):
        m2 = materialize(seed, n_parallel=1, n_pulses=8)
        for arts in m2.artifacts.values():
            extra.extend(arts)
    g = build_causal_graph(world)
    walk = random_walk_event_ids(
        g,
        starts=list(spec.essential_event_ids),
        n_walks=8,
        walk_len=5,
        forbid=set(spec.essential_event_ids),
        rng=random.Random(0),
    )
    packed = pack_view(
        views["full"],
        spec,
        par + extra,
        query_timing="late",
        position_bucket="middle",
        length_bucket="8k",
        target_tokens=8000,
        rng=random.Random(0),
        min_distance_frac=0.20,
        walk_ids=walk,
    )
    assert packed.ok, packed.reject_reason
    assert packed.n_clones == 0
    assert packed.tokens >= 8000
    assert packed.window_ids
    assert estimate_tokens(packed.text) >= 8000
    assert packed.max_evidence_distance >= int(0.20 * 8000)
    assert not any("#pad" in a.artifact_id for a in packed.artifacts)
