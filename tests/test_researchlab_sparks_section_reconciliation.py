from __future__ import annotations

import random
import sys
from pathlib import Path

from longworld.core.engine import (
    answer_from_artifacts,
    semantic_answer_from_artifacts,
)
from longworld.core.pack import (
    compute_view_metrics,
    join_artifacts,
    pack_view,
    wrap_prompt,
)
from longworld.core.sampler import materialize
from longworld.core.sourcebundle import load_source_workflow_bundle
from longworld.core.views import render_cf_view, split_views

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
    / "data/source_inventory/p14_paper_sparks_revision_v1"
    / "paper_source_workflow_bundle.p14.sparks-section-reconciliation.signed.json"
)
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"


def _materialized():
    loaded = load_source_workflow_bundle(BUNDLE)
    return materialize(
        142303127,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=list(loaded.workflows),
        include_program_joins=False,
    )


def test_sparks_bundle_materializes_nested_section_reconciliation_queries() -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "paper_revision_section_reconciliation"
    ]

    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    events = {event.id: event for event in world.events}
    claims = [
        {
            str(events[event_id].params["section_claim_id"])
            for event_id in query.essential_event_ids
            if events[event_id].params.get("section_claim_id")
        }
        for query in queries
    ]
    assert [len(tier_claims) for tier_claims in claims] == [3, 5, 10]
    assert claims[0] < claims[1] < claims[2]
    for query in queries:
        selected = [events[event_id] for event_id in query.essential_event_ids]
        [relation] = [
            event for event in selected if event.type == "arxiv_revision_relation"
        ]
        assert relation.params["source_record_id"] == "arxiv:2303.12712v5"
        assert relation.params["target_record_id"] == "arxiv:2303.12712v4"
        assert any(
            event.type == "arxiv_section_reconciliation_control"
            for event in selected
        )
        claim_events = [
            event for event in selected if event.params.get("section_claim_id")
        ]
        assert all(event.params.get("source_compile_receipts") for event in claim_events)
        assert all(
            str(event.params["text"]).count(event.params["section_claim_quote"]) == 1
            for event in claim_events
        )
        assert selected[-1].type == "arxiv_section_reconciliation_decision"


def test_sparks_reconciliation_requires_every_signed_input_and_changes_under_cf(
) -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "paper_revision_section_reconciliation"
    ]

    for query in queries:
        essential = [
            artifact
            for artifact in materialized.artifacts["focal"]
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert semantic_answer_from_artifacts(world, query, essential) == query.answer
        assert query.answer.startswith("v5 revision_of v4 || ")
        assert query.answer.count(" || ") == len(query.program_ops) - 3
        assert query.cf_answer != query.answer
        for removed in essential:
            remaining = [artifact for artifact in essential if artifact is not removed]
            assert (
                answer_from_artifacts(
                    world, query, remaining, enforce_preconditions=True
                )
                == "unknown"
            )
            assert semantic_answer_from_artifacts(world, query, remaining) == "unknown"


def test_sparks_reconciliation_packs_exact_distinct_long_views() -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    tokenizer = _load_exact_tokenizer(
        "Qwen/Qwen3.5-4B", TOKENIZER_REVISION, True
    )

    def count(text: str) -> int:
        return tokenizer_token_count(text, tokenizer)

    expected = {
        "16k": (16_000, 16_384, 8_000),
        "32k": (32_000, 32_768, 16_000),
        "64k": (64_000, 65_536, 22_000),
    }
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "paper_revision_section_reconciliation"
    ]
    for query in queries:
        bucket = query.preferred_length_buckets[0]
        factual = source_workflow_artifacts_for_query(
            materialized.artifacts["focal"], query
        )
        _, cf_artifacts = render_cf_view(world, query)
        counterfactual = source_workflow_artifacts_for_query(cf_artifacts, query)
        views = split_views(factual, [], query, counterfactual)
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
            rng=random.Random(stable_seed(142303127, query.query_id, "pack")),
            min_distance_tokens=expected[bucket][2],
            walk_ids=[],
            min_semantic_tokens=800,
            token_counter=count,
        )
        assert packed.ok, packed.reject_reason
        cf_index = {artifact.artifact_id: artifact for artifact in views["cf"]}
        cf_view = [
            cf_index.get(artifact.artifact_id, artifact)
            for artifact in packed.artifacts
        ]
        ordered = sorted(
            packed.artifacts, key=lambda artifact: (artifact.time, artifact.artifact_id)
        )
        full_prompt = wrap_prompt(query.question, packed.text, "first")
        cf_prompt = wrap_prompt(query.question, join_artifacts(cf_view), "first")
        ordered_prompt = wrap_prompt(query.question, join_artifacts(ordered), "first")
        lower, upper, min_span = expected[bucket]
        assert lower <= count(full_prompt) <= upper
        assert count(cf_prompt) == count(full_prompt)
        assert count(ordered_prompt) == count(full_prompt)
        assert [artifact.artifact_id for artifact in ordered] != [
            artifact.artifact_id for artifact in packed.artifacts
        ]
        metrics = compute_view_metrics(
            ordered,
            set(query.essential_artifact_ids),
            query_timing="first",
            token_counter=count,
        )
        assert metrics.evidence_span_tokens >= min_span
