from __future__ import annotations

import random
import sys
from pathlib import Path

import yaml

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
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.sourcebundle import load_source_workflow_bundle
from longworld.core.views import render_cf_view, split_views

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate import (
    _load_exact_tokenizer,
    context_source_relation_count,
    exact_context_pack_target,
    real_source_relation_edges,
    source_workflow_artifacts_for_query,
    stable_seed,
    tokenizer_token_count,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/p15_researchlab_sparks_128k_extension_v1.yaml"
BUNDLE = (
    ROOT
    / "data/source_inventory/p14_paper_sparks_revision_v1"
    / "paper_source_workflow_bundle.p14.sparks-section-reconciliation.signed.json"
)
SEED = 142303127
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
EXPECTED_PATHS = (
    "contents/1_intro.tex",
    "contents/2_see.tex",
    "contents/3_code.tex",
    "contents/4.2_datasets.tex",
    "contents/4.3_domains.tex",
    "contents/4.4_higher.tex",
    "contents/4_math.tex",
    "contents/5.1_affordances.tex",
    "contents/5.2_interact_environment.tex",
    "contents/roleplaying.tex",
    "contents/interpretability.tex",
    "contents/7_discrimination.tex",
    "contents/7.1_pii.tex",
    "contents/7.2_misconceptions.tex",
    "contents/reasoninglimitations.tex",
    "contents/societal.tex",
    "contents/conclusion.tex",
    "contents/intro_appendix.tex",
    "contents/code_appendix.tex",
    "contents/math_appendix.tex",
)


def _materialized():
    loaded = load_source_workflow_bundle(BUNDLE)
    return materialize(
        SEED,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=list(loaded.workflows),
        include_program_joins=False,
    )


def _query(materialized):
    return next(
        query
        for query in materialized.queries
        if query.query_type == "paper_revision_section_reconciliation"
        and query.preferred_length_buckets == ["128k"]
    )


def test_sparks_128k_config_is_exact_and_source_only() -> None:
    config = yaml.safe_load(CONFIG.read_text())

    assert config["required_seed_start"] == SEED
    assert config["source_workflow_bundle"] == str(BUNDLE.relative_to(ROOT))
    assert config["real_workflow_query_length_buckets"] == {
        "paper_revision_section_reconciliation": ["128k"]
    }
    assert config["real_workflow_views"] == [
        "full",
        "cf",
        "ordered_artifact_view",
    ]
    assert config["length_buckets"] == {"128k": 131072}
    assert config["exact_pack_admission"] is True
    assert config["n_anchors"] == config["n_source_pack"] == 0


def test_sparks_128k_extends_sections_and_revision_chain() -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    events = {event.id: event for event in world.events}
    query = _query(materialized)
    selected = [events[event_id] for event_id in query.essential_event_ids]
    claim_events = [event for event in selected if event.params.get("section_claim_id")]

    assert tuple(event.params["section_source_path"] for event in claim_events) == (
        EXPECTED_PATHS
    )
    assert len({event.params["section_claim_id"] for event in claim_events}) == len(
        EXPECTED_PATHS
    )
    assert all(event.params["source_compile_receipts"] for event in claim_events)
    relations = [event for event in selected if event.type == "arxiv_revision_relation"]
    assert {
        (event.params["source_record_id"], event.params["target_record_id"])
        for event in relations
    } == {
        ("arxiv:2303.12712v5", "arxiv:2303.12712v4"),
        ("arxiv:2303.12712v4", "arxiv:2303.12712v3"),
        ("arxiv:2303.12712v3", "arxiv:2303.12712v2"),
    }
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    assert semantic_answer_from_artifacts(world, query, essential) == query.answer
    assert query.cf_answer != query.answer
    for removed in essential:
        remaining = [artifact for artifact in essential if artifact is not removed]
        assert (
            answer_from_artifacts(world, query, remaining, enforce_preconditions=True)
            == "unknown"
        )
        assert semantic_answer_from_artifacts(world, query, remaining) == "unknown"
    assert context_source_relation_count(world, essential, spec=query) == 3
    assert len(real_source_relation_edges(world, query, essential)) == 3


def test_sparks_128k_exact_full_cf_ordered_views() -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    query = _query(materialized)
    tokenizer = _load_exact_tokenizer("Qwen/Qwen3.5-4B", TOKENIZER_REVISION, True)

    def count(text: str) -> int:
        return tokenizer_token_count(text, tokenizer)

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
        length_bucket="128k",
        target_tokens=exact_context_pack_target(
            query.question, "first", "128k", tokenizer
        ),
        rng=random.Random(stable_seed(SEED, query.query_id, "pack")),
        min_distance_tokens=45000,
        walk_ids=[],
        min_semantic_tokens=800,
        token_counter=count,
    )
    assert packed.ok, packed.reject_reason
    cf_index = {artifact.artifact_id: artifact for artifact in views["cf"]}
    cf_view = [
        cf_index.get(artifact.artifact_id, artifact) for artifact in packed.artifacts
    ]
    ordered = sorted(
        packed.artifacts, key=lambda artifact: (artifact.time, artifact.artifact_id)
    )
    prompts = {
        "full": wrap_prompt(query.question, packed.text, "first"),
        "cf": wrap_prompt(query.question, join_artifacts(cf_view), "first"),
        "ordered": wrap_prompt(query.question, join_artifacts(ordered), "first"),
    }
    prompt_tokens = {name: count(prompt) for name, prompt in prompts.items()}
    assert all(128000 <= tokens <= 131072 for tokens in prompt_tokens.values()), (
        prompt_tokens
    )
    assert prompts["full"] != prompts["cf"]
    assert prompts["full"] != prompts["ordered"]
    assert all(
        sentence_near_dup_ratio(view) <= 0.25
        for view in (packed.artifacts, cf_view, ordered)
    )
    metrics = compute_view_metrics(
        ordered,
        set(query.essential_artifact_ids),
        query_timing="first",
        token_counter=count,
    )
    assert metrics.evidence_span_tokens >= 45000
