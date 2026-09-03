from __future__ import annotations

import random
import sys
from pathlib import Path

from longworld.core.causal import build_causal_graph
from longworld.core.engine import semantic_answer_from_artifacts
from longworld.core.graph import (
    proof_depth as replayed_proof_depth,
)
from longworld.core.graph import (
    random_walk_event_ids,
    typed_walk_event_ids,
)
from longworld.core.issuerpdfworkflow import (
    selected_issuer_official_pdf_relation_edges,
)
from longworld.core.pack import join_artifacts, pack_view, wrap_prompt
from longworld.core.render import render_world
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.sourcebundle import load_source_workflow_bundle
from longworld.core.views import render_cf_view, split_views
from longworld.domains.company.queries import (
    build_queries,
    eval_answer,
    state_from_artifacts,
)
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate import (
    _load_exact_tokenizer,
    exact_context_pack_target,
    source_workflow_artifacts_for_query,
    stable_seed,
    tokenizer_token_count,
)

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = (
    ROOT
    / "data/source_inventory/p14_company_walmart_official_annuals_v1"
    / "walmart_source_workflow_bundle.signed.json"
)
REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"


def _materialized():
    loaded = load_source_workflow_bundle(BUNDLE)
    spec = sample_world_spec(
        14202,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=list(loaded.workflows),
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    queries = [
        query
        for query in build_queries(world)
        if query.query_type.startswith("walmart_reconciliation_")
    ]
    return world, artifacts, queries


def test_walmart_reconciliation_has_cumulative_essentials_and_remove_one() -> None:
    world, artifacts, queries = _materialized()

    assert [len(query.essential_event_ids) for query in queries] == [6, 13, 26]
    assert [query.proof_depth for query in queries] == [3, 4, 5]
    assert [replayed_proof_depth(world, query) for query in queries] == [3, 4, 5]
    assert set(queries[0].essential_event_ids) < set(queries[1].essential_event_ids)
    assert set(queries[1].essential_event_ids) < set(queries[2].essential_event_ids)
    for query in queries:
        state = state_from_artifacts(world, query.essential_event_ids, query.as_of)
        assert eval_answer(world, query, state.values) == query.answer
        essential_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert (
            semantic_answer_from_artifacts(world, query, essential_artifacts)
            == query.answer
        )
        for removed in query.essential_event_ids:
            reduced = [
                event_id
                for event_id in query.essential_event_ids
                if event_id != removed
            ]
            reduced_state = state_from_artifacts(world, reduced, query.as_of)
            assert eval_answer(world, query, reduced_state.values) == "unknown"


def test_walmart_reconciliation_counterfactual_changes_icfr_conclusion() -> None:
    world, _, queries = _materialized()

    for query in queries:
        counterfactual = state_from_artifacts(
            world,
            query.essential_event_ids,
            query.as_of,
            param_overrides={query.cf_event_id: query.cf_param_updates},
        )
        assert eval_answer(world, query, counterfactual.values) == query.cf_answer
        assert query.cf_answer != query.answer


def test_walmart_reconciliation_packs_exact_distinct_signed_three_views() -> None:
    world, artifacts, queries = _materialized()
    tokenizer = _load_exact_tokenizer("Qwen/Qwen3.5-4B", REVISION, True)

    def count(text: str) -> int:
        return tokenizer_token_count(text, tokenizer)

    graph = build_causal_graph(world)
    expected = {
        "16k": (16_000, 16_384, 8_000, 0),
        "32k": (32_000, 32_768, 16_000, 1),
        "64k": (64_000, 65_536, 22_000, 3),
    }
    for query in queries:
        bucket = query.preferred_length_buckets[0]
        factual = source_workflow_artifacts_for_query(artifacts, query)
        _, cf_artifacts = render_cf_view(world, query)
        counterfactual = source_workflow_artifacts_for_query(cf_artifacts, query)
        views = split_views(factual, [], query, counterfactual)
        walk_ids = random_walk_event_ids(
            graph,
            starts=list(query.essential_event_ids),
            n_walks=12,
            walk_len=8,
            forbid=set(query.essential_event_ids),
            rng=random.Random(stable_seed(14202, query.query_id, "random_walk")),
        )
        walk_ids += typed_walk_event_ids(
            graph,
            starts=list(query.essential_event_ids),
            allowed_kinds={
                "causes",
                "enables",
                "derived_from",
                "supersedes",
                "contradicts",
            },
            n_walks=8,
            walk_len=6,
            forbid=set(query.essential_event_ids),
            rng=random.Random(stable_seed(14202, query.query_id, "typed_walk")),
        )
        packed = pack_view(
            views["full"],
            query,
            [],
            query_timing="first",
            position_bucket="middle",
            length_bucket=bucket,
            target_tokens=exact_context_pack_target(
                query.question, "first", bucket, tokenizer
            ),
            rng=random.Random(stable_seed(14202, query.query_id, "pack")),
            min_distance_tokens=expected[bucket][2],
            walk_ids=list(dict.fromkeys(walk_ids)),
            min_semantic_tokens=800,
            token_counter=count,
        )
        assert packed.ok, packed.reject_reason
        assert sentence_near_dup_ratio(packed.artifacts) <= 0.25
        packed_cf = {artifact.artifact_id: artifact for artifact in views["cf"]}
        cf_view = [
            packed_cf.get(artifact.artifact_id, artifact)
            for artifact in packed.artifacts
        ]
        ordered = sorted(
            packed.artifacts,
            key=lambda artifact: (artifact.time, artifact.artifact_id),
        )
        full_prompt = wrap_prompt(query.question, packed.text, "first")
        cf_prompt = wrap_prompt(query.question, join_artifacts(cf_view), "first")
        ordered_prompt = wrap_prompt(query.question, join_artifacts(ordered), "first")
        lower, upper, _, relation_count = expected[bucket]
        assert lower <= count(full_prompt) <= upper
        assert count(cf_prompt) == count(full_prompt)
        assert count(ordered_prompt) == count(full_prompt)
        assert [artifact.artifact_id for artifact in ordered] != [
            artifact.artifact_id for artifact in packed.artifacts
        ]
        assert (
            len(
                selected_issuer_official_pdf_relation_edges(
                    world, query, packed.artifacts
                )
            )
            == relation_count
        )

    full_chain = queries[-1]
    essential_body = [
        str(event.params["text"])
        for event in world.events
        if event.id in full_chain.essential_event_ids
        and isinstance(event.params.get("text"), str)
    ]
    raw_body = "\n\n===== DOCUMENT =====\n\n".join(essential_body)
    assert count(raw_body) <= 65_536 - 2_000
