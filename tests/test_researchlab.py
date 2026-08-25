import random

from longworld.core.causal import build_causal_graph
from longworld.core.engine import answer_from_artifacts
from longworld.core.graph import min_sufficient_subgraph, typed_walk_event_ids
from longworld.core.prose import SENTENCE_BANK
from longworld.core.sampler import materialize
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view


def test_researchlab_motifs_and_gates():
    mat = materialize(21, n_parallel=1, n_pulses=6, domain="researchlab")
    assert mat.scan_ok, mat.scan_issues
    world = mat.worlds["focal"]
    arts = mat.artifacts["focal"]
    assert world.spec["domain"] == "researchlab"
    motifs = {q.motif for q in mat.queries}
    assert {
        "supersession",
        "fork_join",
        "delayed_effect",
        "contradiction",
        "counterfactual_supersession",
        "hidden_bridge",
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


def test_lab_final_score_not_in_release_note():
    mat = materialize(22, n_parallel=1, n_pulses=4, domain="researchlab")
    world = mat.worlds["focal"]
    rel = next(
        a for a in mat.artifacts["focal"] if a.artifact_id.endswith("release_note")
    )
    final = str(world.spec["project"]["final_score"])
    assert final not in rel.text
    cur = next(q for q in mat.queries if q.query_type == "current_state")
    assert cur.answer == str(world.spec["project"]["final_score"])


def test_lab_renderer_does_not_append_prose_bank():
    mat = materialize(22, n_parallel=1, n_pulses=0, domain="researchlab")
    blob = "\n".join(a.text for a in mat.artifacts["focal"])
    assert not any(s in blob for s in SENTENCE_BANK)
    assert not any(e.type == "status_pulse" for e in mat.worlds["focal"].events)
