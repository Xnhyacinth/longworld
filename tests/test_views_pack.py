import random

from longworld.core.causal import build_causal_graph
from longworld.core.graph import random_walk_event_ids
from longworld.core.pack import (
    compute_view_metrics,
    pack_view,
    prompt_document_prefix,
    prompt_query_boundary,
    wrap_prompt,
)
from longworld.core.promotion import STRICT_REPLAY_REVISION
from longworld.core.sampler import materialize
from longworld.core.semantic import boilerplate_char_fraction, is_boilerplate
from longworld.core.topology import canonical_topology
from longworld.core.views import render_cf_view, split_views, view_answer


def test_four_views_and_unique_pack():
    mat = materialize(5, n_parallel=2, n_pulses=0)
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
    assert "ordered_artifact_view" in views
    assert views["ordered_artifact_view"] == views["trajectory"]
    assert view_answer(spec, "distractor_only") == "unanswerable"
    assert view_answer(spec, "memory") == "unanswerable"
    assert view_answer(spec, "cf") == spec.cf_answer
    extra = []
    for seed in range(20, 28):
        m2 = materialize(seed, n_parallel=1, n_pulses=0)
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
        min_distance_frac=0.0,
        walk_ids=walk,
    )
    assert packed.ok, packed.reject_reason
    assert packed.n_clones == 0
    assert 4000 <= packed.tokens <= 8000
    assert packed.window_ids
    assert packed.requested_max_tokens == 8000
    assert not any("#pad" in a.artifact_id for a in packed.artifacts)
    assert packed.boilerplate_token_ratio < 0.05
    assert packed.pulse_doc_ratio < 0.05
    assert not any(is_boilerplate(a) for a in packed.artifacts)


def test_pack_keeps_natural_length_instead_of_padding():
    mat = materialize(7, n_parallel=1, n_pulses=0)
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    packed = pack_view(
        mat.artifacts["focal"],
        spec,
        [],
        query_timing="first",
        position_bucket="middle",
        length_bucket="64k",
        target_tokens=64000,
        rng=random.Random(0),
        min_distance_frac=0.0,
        min_semantic_tokens=200,
    )
    assert packed.ok, packed.reject_reason
    assert packed.reject_reason is None
    assert packed.n_clones == 0
    assert not any("#pad" in a.artifact_id for a in packed.artifacts)
    assert boilerplate_char_fraction(packed.text) < 0.05
    has_pack = any(a.doc_type == "source_pack" for a in mat.artifacts["focal"])
    if has_pack:
        assert packed.tokens <= 64000
    else:
        assert packed.tokens < 20000


def test_pack_uses_exact_counter_for_optional_artifact_admission() -> None:
    from datetime import date

    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    essential = Artifact(
        "w.essential",
        "wiki",
        date(2026, 1, 1),
        "wiki",
        "focal",
        ["essential"],
        "x" * 1_000,
        [],
        slots={"content_plan": {"new_propositions": ["essential"]}},
    )
    optional = Artifact(
        "w.optional",
        "wiki",
        date(2026, 1, 2),
        "wiki",
        "focal",
        ["optional"],
        "y" * 1_000,
        [],
        slots={"content_plan": {"new_propositions": ["optional"]}},
    )
    spec = QuerySpec(
        query_id="w:exact-pack",
        query_type="source_choice",
        question="What is selected?",
        answer="selected",
        as_of=None,
        answer_key="selected",
        essential_event_ids=["essential"],
        essential_artifact_ids=[essential.artifact_id],
        sufficient_event_ids=["essential"],
        cf_event_id="essential",
        cf_param_updates={"value": "changed"},
        cf_answer="changed",
        invariance_event_id=None,
        proof_depth=1,
    )

    packed = pack_view(
        [essential, optional],
        spec,
        [],
        query_timing="late",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=10,
        rng=random.Random(0),
        min_semantic_tokens=1,
        token_counter=lambda text: len(text.split()),
    )

    assert packed.ok
    assert {artifact.artifact_id for artifact in packed.artifacts} == {
        essential.artifact_id,
        optional.artifact_id,
    }
    assert packed.tokens == len(packed.text.split())


def test_pack_preserves_real_source_corridor_without_promoting_intermediate_records():
    from datetime import date

    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    def record(artifact_id: str, order: int, *, event: str | None = None) -> Artifact:
        return Artifact(
            artifact_id=artifact_id,
            doc_type="json",
            time=date(2026, 1, order),
            project="repo",
            prefix="focal",
            reveals_events=[event] if event else [],
            text=(f"authentic source record {order} " * 120),
            facts=[],
            slots={
                "params": {"source_order": order},
                "content_plan": {"new_propositions": [artifact_id]},
            },
            is_focal=True,
        )

    source = [
        record("w.failure", 1, event="failure"),
        record("w.history-a", 2),
        record("w.history-b", 3),
        record("w.recovery", 4, event="recovery"),
        record("w.outside", 5),
    ]
    spec = QuerySpec(
        query_id="w:ci",
        query_type="ci_regression_origin",
        question="Which commit caused the recovered check?",
        answer="a" * 40 + " :: check",
        as_of=None,
        answer_key="regression",
        essential_event_ids=["failure", "recovery"],
        essential_artifact_ids=["w.failure", "w.recovery"],
        sufficient_event_ids=["failure", "recovery"],
        cf_event_id="failure",
        cf_param_updates={"commit": "b" * 40},
        cf_answer="b" * 40 + " :: check",
        invariance_event_id=None,
        proof_depth=2,
    )
    corridor = {"w.failure", "w.history-a", "w.history-b", "w.recovery"}

    packed = pack_view(
        source,
        spec,
        [],
        query_timing="late",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=16000,
        rng=random.Random(0),
        min_semantic_tokens=1,
        ordered_corridor_ids=corridor,
    )

    assert packed.ok, packed.reject_reason
    packed_corridor = [
        artifact for artifact in packed.artifacts if artifact.artifact_id in corridor
    ]
    assert [artifact.artifact_id for artifact in packed_corridor] == [
        "w.failure",
        "w.history-a",
        "w.history-b",
        "w.recovery",
    ]
    assert all(
        packed.roles[artifact_id] not in {"causal_gold", "causal_supporting"}
        for artifact_id in ("w.history-a", "w.history-b")
    )


def test_late_query_distance_excludes_question_and_answer_instruction_tokens() -> None:
    from datetime import date

    from longworld.core.render import Artifact

    evidence = Artifact(
        "w.evidence",
        "record",
        date(2026, 1, 1),
        "p",
        "focal",
        ["e0"],
        "grounded evidence",
        [],
    )

    distances = []
    for question in ("Q", "Q" * 1_000):
        context = evidence.text
        metrics = compute_view_metrics(
            [evidence],
            {evidence.artifact_id},
            query_timing="late",
            context=wrap_prompt(question, context, "late"),
            token_counter=len,
            token_prefix=prompt_document_prefix(question, "late"),
            query_boundary_tokens=prompt_query_boundary(question, context, "late", len),
        )
        distances.append(metrics.query_evidence_distance)

    assert distances[0] == distances[1]


def test_pack_compacts_oversized_real_corridor_in_source_order():
    from datetime import date

    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    source = [
        Artifact(
            artifact_id=f"w.record-{index:03d}",
            doc_type="json",
            time=date(2026, 1, 1),
            project="repo",
            prefix="focal",
            reveals_events=(
                ["failure"] if index == 0 else ["recovery"] if index == 99 else []
            ),
            text=(f"authentic workflow event {index} " * 80),
            facts=[],
            slots={
                "params": {"source_order": index},
                "content_plan": {"new_propositions": [f"record-{index}"]},
            },
            is_focal=True,
        )
        for index in range(100)
    ]
    spec = QuerySpec(
        query_id="w:ci-long",
        query_type="ci_regression_origin",
        question="Which commit caused the recovered check?",
        answer="a" * 40 + " :: check",
        as_of=None,
        answer_key="regression",
        essential_event_ids=["failure", "recovery"],
        essential_artifact_ids=["w.record-000", "w.record-099"],
        sufficient_event_ids=["failure", "recovery"],
        cf_event_id="failure",
        cf_param_updates={"commit": "b" * 40},
        cf_answer="b" * 40 + " :: check",
        invariance_event_id=None,
        proof_depth=2,
    )

    packed = pack_view(
        source,
        spec,
        [],
        query_timing="late",
        position_bucket="middle",
        length_bucket="8k",
        target_tokens=8000,
        rng=random.Random(0),
        min_semantic_tokens=1,
        ordered_corridor_ids={artifact.artifact_id for artifact in source},
    )

    assert packed.ok, packed.reject_reason
    assert packed.tokens <= 8000
    assert len(packed.artifacts) < len(source)
    assert packed.max_evidence_distance > 6000
    assert {"w.record-000", "w.record-099"}.issubset(
        {artifact.artifact_id for artifact in packed.artifacts}
    )
    source_orders = [
        artifact.slots["params"]["source_order"] for artifact in packed.artifacts
    ]
    assert source_orders == sorted(source_orders)


def test_packer_rejects_distance_shortfall():
    from datetime import date

    from longworld.core.pack import covering_span_tokens, pack_view
    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    def art(aid: str, n: int, day: int, prop: str) -> Artifact:
        return Artifact(
            artifact_id=aid,
            doc_type="email",
            time=date(2026, 1, day),
            project="p",
            prefix="focal",
            reveals_events=[],
            text=("body " + prop + " ") * n,
            facts=[],
            slots={"content_plan": {"new_propositions": [prop]}},
            is_focal=True,
        )

    spec = QuerySpec(
        query_id="t:revisitation",
        query_type="revisitation",
        question="q",
        answer="LT-X1001",
        as_of=None,
        answer_key="active_latent",
        essential_event_ids=["e1", "e2"],
        essential_artifact_ids=["w.focal.a", "w.focal.b"],
        sufficient_event_ids=["e1", "e2"],
        cf_event_id="e1",
        cf_param_updates={},
        cf_answer="LT-X1008",
        invariance_event_id=None,
        proof_depth=3,
    )
    a = art("w.focal.a", 20, 1, "seed")
    b = art("w.focal.b", 20, 8, "reopen")
    packed = pack_view(
        [a, b],
        spec,
        [],
        query_timing="first",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=16000,
        rng=random.Random(0),
        min_distance_tokens=5000,
        min_semantic_tokens=10,
        walk_ids=[],
    )
    assert not packed.ok
    assert packed.reject_reason and packed.reject_reason.startswith(
        "distance_shortfall"
    )

    fillers = [art(f"w.focal.f{i}", 400, 2 + (i % 5), f"prop{i}") for i in range(24)]
    spread = pack_view(
        [a, b],
        spec,
        fillers,
        query_timing="first",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=16000,
        rng=random.Random(0),
        min_distance_tokens=2000,
        min_semantic_tokens=10,
        walk_ids=[],
    )
    assert spread.ok, spread.reject_reason
    assert spread.max_evidence_distance >= 2000
    span = covering_span_tokens(spread.artifacts, set(spec.essential_artifact_ids))
    assert span >= 2000


def test_packer_skips_oversized_filler_instead_of_stopping():
    from datetime import date

    from longworld.core.pack import pack_view
    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    def art(aid: str, n: int, day: int, prop: str, *, source: bool = False) -> Artifact:
        return Artifact(
            artifact_id=aid,
            doc_type="source_pack" if source else "email",
            time=date(2026, 1, day),
            project="p",
            prefix="focal",
            reveals_events=[],
            text=("body " + prop + " ") * n,
            facts=[],
            slots={"content_plan": {"new_propositions": [prop]}},
            is_focal=True,
        )

    spec = QuerySpec(
        query_id="t:current",
        query_type="current_state",
        question="q",
        answer="RV-1",
        as_of=None,
        answer_key="k",
        essential_event_ids=["e1", "e2"],
        essential_artifact_ids=["w.focal.a", "w.focal.b"],
        sufficient_event_ids=["e1", "e2"],
        cf_event_id="e1",
        cf_param_updates={},
        cf_answer="RV-2",
        invariance_event_id=None,
        proof_depth=2,
    )
    a = art("w.focal.a", 20, 1, "seed")
    b = art("w.focal.b", 20, 8, "reopen")
    huge = art("w.focal.huge", 20000, 3, "rfc", source=True)
    fillers = [huge] + [art(f"w.focal.f{i}", 400, 2, f"prop{i}") for i in range(20)]
    packed = pack_view(
        [a, b],
        spec,
        fillers,
        query_timing="first",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=16000,
        rng=random.Random(0),
        min_distance_tokens=2000,
        min_semantic_tokens=10,
        walk_ids=[],
    )
    assert packed.ok, packed.reject_reason
    assert packed.max_evidence_distance >= 2000
    assert huge.artifact_id not in {x.artifact_id for x in packed.artifacts}


def test_packer_keeps_same_world_decoy_when_source_packs_compete():
    from datetime import date

    from longworld.core.pack import pack_view
    from longworld.core.render import Artifact
    from longworld.core.sampler import materialize

    mat = materialize(1, n_parallel=1, n_pulses=0, domain="company")
    specs = [q for q in mat.queries if q.query_type == "revisitation"]
    if not specs:
        return
    spec = specs[0]
    decoy = next(
        a for a in mat.artifacts["focal"] if a.artifact_id.endswith("latent_decoy")
    )
    fillers = []
    for i in range(12):
        fillers.append(
            Artifact(
                artifact_id=f"w.pack.{i}",
                doc_type="source_pack",
                time=date(2026, 1, 2),
                project="p",
                prefix="x",
                reveals_events=[],
                text=("public source " + str(i) + " ") * 800,
                facts=[],
                slots={"content_plan": {"new_propositions": [f"src{i}"]}},
                is_focal=False,
            )
        )
    packed = pack_view(
        mat.artifacts["focal"],
        spec,
        fillers,
        query_timing="first",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=16000,
        rng=random.Random(0),
        min_distance_tokens=8000,
        min_semantic_tokens=10,
        walk_ids=[],
        prefer_ids=set(spec.essential_artifact_ids) | {decoy.artifact_id},
    )
    assert packed.ok, packed.reject_reason
    assert decoy.artifact_id in {a.artifact_id for a in packed.artifacts}


def test_packer_prefers_native_over_unbound_rfc():
    from datetime import date

    from longworld.core.pack import pack_view
    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    def art(aid: str, prop: str, *, source: bool) -> Artifact:
        return Artifact(
            artifact_id=aid,
            doc_type="source_pack" if source else "email",
            time=date(2026, 1, 3),
            project="p",
            prefix="x",
            reveals_events=[],
            text=("body " + prop + " ") * 200,
            facts=[],
            slots={"content_plan": {"new_propositions": [prop]}},
            is_focal=False,
        )

    spec = QuerySpec(
        query_id="t:current",
        query_type="current_state",
        question="q",
        answer="RV-1",
        as_of=None,
        answer_key="k",
        essential_event_ids=["e1", "e2"],
        essential_artifact_ids=["w.focal.a", "w.focal.b"],
        sufficient_event_ids=["e1", "e2"],
        cf_event_id="e1",
        cf_param_updates={},
        cf_answer="RV-2",
        invariance_event_id=None,
        proof_depth=2,
    )
    a = art("w.focal.a", "seed", source=False)
    a.is_focal = True
    b = art("w.focal.b", "reopen", source=False)
    b.is_focal = True
    natives = [art(f"w.other.n{i}", f"nat{i}", source=False) for i in range(8)]
    rfcs = [art(f"w.pack.r{i}", f"rfc{i}", source=True) for i in range(8)]
    packed = pack_view(
        [a, b],
        spec,
        natives + rfcs,
        query_timing="first",
        position_bucket="middle",
        length_bucket="8k",
        target_tokens=8000,
        rng=random.Random(0),
        min_distance_tokens=0,
        min_semantic_tokens=10,
        walk_ids=[],
    )
    assert packed.ok, packed.reject_reason
    extra = [
        x
        for x in packed.artifacts
        if x.artifact_id not in set(spec.essential_artifact_ids)
    ]
    n_native = sum(1 for x in extra if x.doc_type == "email")
    n_rfc = sum(1 for x in extra if x.doc_type == "source_pack")
    assert n_native > n_rfc, (n_native, n_rfc, [x.doc_type for x in extra])


def test_packer_uses_unbound_rfc_when_native_cannot_meet_distance():
    from datetime import date

    from longworld.core.pack import pack_view
    from longworld.core.render import Artifact
    from longworld.domains.company.queries import QuerySpec

    spec = QuerySpec(
        query_id="t:current",
        query_type="current_state",
        question="q",
        answer="RV-1",
        as_of=None,
        answer_key="k",
        essential_event_ids=["e1", "e2"],
        essential_artifact_ids=["w.focal.a", "w.focal.b"],
        sufficient_event_ids=["e1", "e2"],
        cf_event_id="e1",
        cf_param_updates={},
        cf_answer="RV-2",
        invariance_event_id=None,
        proof_depth=2,
    )

    def tiny(aid: str, day: int) -> Artifact:
        return Artifact(
            artifact_id=aid,
            doc_type="email",
            time=date(2026, 1, day),
            project="p",
            prefix="focal",
            reveals_events=[],
            text="short native " * 20,
            facts=[],
            slots={"content_plan": {"new_propositions": [aid]}},
            is_focal=True,
        )

    a = tiny("w.focal.a", 1)
    b = tiny("w.focal.b", 8)
    rfc = Artifact(
        artifact_id="w.focal.source.rfc9110",
        doc_type="source_pack",
        time=date(2026, 1, 3),
        project="public",
        prefix="source",
        reveals_events=[],
        text=("public source unique span " * 400),
        facts=[],
        slots={"content_plan": {"new_propositions": []}},
        is_focal=False,
    )
    dummies = [tiny(f"w.other.n{i}", 2) for i in range(3)]
    packed = pack_view(
        [a, b],
        spec,
        dummies + [rfc],
        query_timing="first",
        position_bucket="middle",
        length_bucket="16k",
        target_tokens=16000,
        rng=random.Random(0),
        min_distance_tokens=2000,
        min_semantic_tokens=10,
        walk_ids=[],
    )
    assert packed.ok, packed.reject_reason
    assert rfc.artifact_id in {x.artifact_id for x in packed.artifacts}
    assert packed.max_evidence_distance >= 2000


def test_unbound_source_pack_role_is_background_not_hn():
    from datetime import date

    from longworld.core.render import Artifact
    from longworld.core.semantic import artifact_role

    rfc = Artifact(
        artifact_id="w000001-acme.source.rfc9110.abcd1234",
        doc_type="source_pack",
        time=date(2026, 1, 2),
        project="public",
        prefix="source",
        reveals_events=[],
        text="public source " * 40,
        facts=[],
        slots={"source_role": "natural_background"},
        is_focal=False,
    )
    other = Artifact(
        artifact_id="w000002-beta.focal.legal_email",
        doc_type="email",
        time=date(2026, 1, 2),
        project="beta",
        prefix="focal",
        reveals_events=["focal.sign_contract"],
        text="internal email " * 40,
        facts=[],
        slots={},
        is_focal=False,
    )
    gold = "w000001-acme"
    assert artifact_role(rfc, set(), set(), gold) == "natural_background"
    assert artifact_role(other, set(), set(), gold) == "structural_hard_negative"


def test_export_drops_local_or_mixed_on_long_buckets(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from export_llamafactory import COND_VIEWS, filter_rows

    from longworld.core.attestation import ATTESTATION_ENV, attach_attestation

    key = b"longworld-test-attestation-key-32-bytes"
    monkeypatch.setenv(ATTESTATION_ENV, key.decode())

    rows = [
        {
            "view": "full",
            "length_bucket": "32k",
            "query_id": "w:current_state:first:middle:32k",
            "query_timing": "first",
            "dependency_class": "local_or_mixed",
        },
        {
            "view": "full",
            "length_bucket": "32k",
            "query_id": "w:revisitation:first:middle:32k",
            "query_timing": "first",
            "dependency_class": "deep_dependency",
        },
        {
            "view": "full",
            "length_bucket": "16k",
            "query_id": "w:current_state:first:middle:16k",
            "query_timing": "first",
            "dependency_class": "local_or_mixed",
        },
        {
            "view": "memory",
            "length_bucket": "64k",
            "query_id": "w:current_state:first:middle:64k:memory",
            "query_timing": "first",
            "dependency_class": "deep_dependency",
        },
        {
            "view": "ordered_artifact_view",
            "length_bucket": "16k",
            "query_id": "w:revisitation:first:middle:16k:ord",
            "query_timing": "late",
            "dependency_class": "deep_dependency",
        },
    ]
    contract = {
        "context": "workflow documents",
        "answer": "RV-1",
        "data_stage": "train_ready",
        "training_objective": "sft",
        "workflow_ids": ["w"],
        "view_verification": {
            "production_eligible": True,
            "essential_present": True,
            "semantic_text_grounded": True,
            "classification_ok": True,
            "global_proof_green": True,
            "expected_answer": "RV-1",
            "strict_replay_answer": "RV-1",
        },
        "verification": {
            "production_mode": True,
            "semantic_sufficient": True,
            "strict_executable_sufficient": True,
            "embedding_topk_insufficient": True,
        },
        "promotion": {
            "schema_version": "train-ready-promotion-v1",
            "candidate_sha256": "0" * 64,
            "dense_audit_sha256": "1" * 64,
            "dense_ranking_sha256": "2" * 64,
            "dense_model_provider": "huggingface",
            "dense_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dense_model_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            "dense_model_backend": "sentence-transformers-6.0.0",
            "dense_score_metric": "dot_product",
            "dense_chunking": {
                "strategy": "tokenizer_token_windows",
                "max_tokens": 192,
                "overlap_tokens": 32,
                "aggregation": "max_similarity",
            },
            "dense_top_k": 3,
            "strict_replay_revision": STRICT_REPLAY_REVISION,
            "strict_replay_answer": "RV-1",
        },
        "artifact_classification": [
            {
                "artifact_id": "w.a",
                "workflow_id": "w",
                "workflow_kind": "synthetic_executable",
                "evidence_role": "causal_gold",
                "source_origin": "synthetic_world",
                "provenance_id": "synthetic-sha256:abc",
            }
        ],
    }
    for row in rows:
        row.update(contract)
        row["composition_method"] = (
            "causal_timeline"
            if row["view"] == "ordered_artifact_view"
            else "same_case_dossier"
        )
    rows = [attach_attestation(row, key, purpose="sft_row") for row in rows]
    kept = filter_rows(rows, "B1", {"16k", "32k"})
    ids = {r["query_id"] for r in kept}
    assert "w:revisitation:first:middle:32k" in ids
    assert "w:current_state:first:middle:16k" in ids
    assert "w:current_state:first:middle:32k" not in ids
    b5 = filter_rows(rows, "B5", {"8k", "16k", "32k", "64k", "128k", "256k"})
    b5_ids = {r["query_id"] for r in b5}
    assert "w:current_state:first:middle:16k" in b5_ids
    assert "w:revisitation:first:middle:16k:ord" in b5_ids
    assert "w:current_state:first:middle:64k:memory" not in b5_ids
    assert "memory" not in COND_VIEWS["B5"]
    assert "memory" not in COND_VIEWS["B5w"]
    long_ok = {
        "view": "full",
        "length_bucket": "128k",
        "query_id": "w:revisitation:late:middle:128k",
        "query_timing": "late",
        "dependency_class": "deep_dependency",
        "composition_method": "same_case_dossier",
        **contract,
    }
    long_ok = attach_attestation(long_ok, key, purpose="sft_row")
    b5_long = filter_rows(rows + [long_ok], "B5", {"16k", "32k", "64k", "128k", "256k"})
    assert "w:revisitation:late:middle:128k" in {r["query_id"] for r in b5_long}


def test_canonical_topology_anonymizes_entities():
    a = materialize(1, n_parallel=1, n_pulses=0)
    b = materialize(2, n_parallel=1, n_pulses=0)
    qa = next(
        q
        for q in a.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    qb = next(
        q
        for q in b.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    assert canonical_topology(qa) == canonical_topology(qb)
    assert qa.topology_id != qb.topology_id
    assert qa.answer not in canonical_topology(qa)
    assert qa.answer != qb.answer


def test_extra_world_filler_does_not_break_remove_one():
    from longworld.core.verify import verify_question

    mat = materialize(5, n_parallel=1, n_pulses=0)
    spec = next(
        q
        for q in mat.queries
        if q.query_type == "current_state" and "decoy" not in q.query_id
    )
    extra = []
    for seed in range(40, 44):
        m2 = materialize(seed, n_parallel=1, n_pulses=0)
        for arts in m2.artifacts.values():
            extra.extend(arts)
    packed = pack_view(
        mat.artifacts["focal"],
        spec,
        extra,
        query_timing="first",
        position_bucket="middle",
        length_bucket="8k",
        target_tokens=8000,
        rng=random.Random(1),
        min_distance_frac=0.0,
        min_semantic_tokens=200,
    )
    assert packed.ok, packed.reject_reason
    ver, notes = verify_question(
        mat.worlds["focal"],
        spec,
        packed.artifacts,
        surface_artifacts=mat.artifacts["focal"],
    )
    assert ver.remove_one_fails, notes
    assert ver.no_shortcut, notes.get("shortcut")
    assert ver.bm25_top1_insufficient, notes
