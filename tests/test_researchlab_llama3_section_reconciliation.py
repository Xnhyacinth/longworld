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
    covering_span_tokens,
    join_artifacts,
    pack_view,
    wrap_prompt,
)
from longworld.core.sampler import materialize
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.sourcebundle import load_source_workflow_bundle
from longworld.core.views import render_cf_view, split_views
from longworld.domains.researchlab.simulate import (
    valid_arxiv_revision_relation_event,
)

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
CONFIG = ROOT / "configs/p14_researchlab_llama3_section_reconciliation_v1.yaml"
BUNDLE = (
    ROOT
    / "data/source_inventory/p14_paper_llama3_revision_preflight_v1"
    / "paper_source_workflow_bundle.p14.llama3-source-foundation.signed.json"
)
SEED = 142407218
WORK_ID = "arxiv:2407.21783"
TOKENIZER_REVISION = "a7b0d22b993d71000cf2eadfb37222a67cee521e"
V2_FILE_SHA256 = "7b4a874fb44e3c34e8c0d6fb1cee694684052f9f4c450ed2346b44a268db1a5b"
V3_FILE_SHA256 = "fa2165a779a5b42fd237a066bf7b9fd57af55304fb8153c703d4fd22c8f680a7"
V2_SCALING_ROW = (
    "16,384   & 8 & 16 & 16 & 4   & 131,072 &   16 & 16M   & 380     "
    "& 38\\%        \\\\"
)
V3_SCALING_ROW = V2_SCALING_ROW.replace("& 4   & 131,072", "& 8   & 131,072")
CF_SCALING_ROW = V3_SCALING_ROW.replace("& 8   & 131,072", "& 7   & 131,072")

CLAIMS = {
    "overview.tex": (
        "pretraining_scale",
        (
            "To do this effectively, pre-training is performed at massive scale: we "
            "pre-train a model with 405B parameters on 15.6T tokens using a context "
            "window of 8K tokens."
        ),
    ),
    "pretraining/model_scaling.tex": (
        "parallelism_delta",
        V3_SCALING_ROW,
    ),
    "results/video_recognition.tex": (
        "perception_test_size",
        (
            "It consists of $11.6K$ test QA pairs, each with an on-average $23s$ long "
            "video, filmed by $100$ participants worldwide to show perceptually "
            "interesting tasks."
        ),
    ),
    "results/tables/speech_ast_results.tex": (
        "fleurs_ast_scores",
        (
            "FLEURS \\scriptsize{(33 lang. $\\rightarrow$ English)} & 29.5 & "
            "\\textbf{33.7}  & 21.9  & 28.6  \\\\"
        ),
    ),
    "posttraining.tex": (
        "sft_schedule",
        (
            "Our largest models are finetuned with a learning rate of $10^{-5}$ over "
            "the course of 8.5K to 9K steps."
        ),
    ),
    "results/tables/benchmarks.tex": (
        "long_context_benchmarks",
        (
            "\\makecell{\\textbf{Long context}} & "
            "\\makecell[l]{QuALITY~\\citep{pang-etal-2022-quality}, many-shot "
            "GSM8K~\\citep{an2023eval}"
        ),
    ),
    "inference/fp8.tex": (
        "ffn_fp8_compute_share",
        (
            "In particular, we quantize most parameters and activations in the "
            "feedforward network layers in the model, which account for roughly "
            "50\\% of the inference compute time."
        ),
    ),
    "vision/data.tex": (
        "vision_data_pipeline",
        (
            "We construct this dataset via a complex data processing pipeline that "
            "consists of four main stages: \\textbf{(1)} quality filtering, "
            "\\textbf{(2)} perceptual de-duplication, \\textbf{(3)} resampling, and "
            "\\textbf{(4)} optical character recognition."
        ),
    ),
    "introduction.tex": (
        "flagship_context",
        (
            "Our largest model is dense Transformer with 405B parameters, processing "
            "information in a context window of up to 128K tokens."
        ),
    ),
    "pretraining/data.tex": (
        "pretraining_knowledge_cutoff",
        (
            "We create our dataset for language model pre-training from a variety of "
            "data sources containing knowledge until the end of 2023."
        ),
    ),
    "pretraining/model_architecture.tex": (
        "gqa_key_value_heads",
        "Key/Value Heads       & 8            & 8             & 8           \\\\",
    ),
    "results/finetuned.tex": (
        "ifeval_scope",
        (
            "IFEval comprises approximately 500 ``verifiable instructions'' such as "
            "``write in more than 400 words'', which can be verified by heuristics."
        ),
    ),
    "results/safety.tex": (
        "cyber_uplift_cohort",
        "A two-stage study was conducted with 62 internal volunteers.",
    ),
    "results/speech.tex": (
        "speech_task_scope",
        (
            "We evaluate the speech understanding capabilities of our speech "
            "interface for Llama 3 on three tasks: \\textbf{(1)} automatic speech "
            "recognition, \\textbf{(2)} speech translation, and \\textbf{(3)} spoken "
            "question answering."
        ),
    ),
}

STAGE_PATHS = {
    "16k": (
        "overview.tex",
        "pretraining/model_scaling.tex",
        "results/video_recognition.tex",
        "results/tables/speech_ast_results.tex",
        "results/tables/benchmarks.tex",
        "inference/fp8.tex",
        "vision/data.tex",
    ),
    "32k": (
        "overview.tex",
        "pretraining/model_scaling.tex",
        "results/video_recognition.tex",
        "results/tables/speech_ast_results.tex",
        "posttraining.tex",
        "results/tables/benchmarks.tex",
        "inference/fp8.tex",
        "vision/data.tex",
    ),
    "64k": (
        "overview.tex",
        "pretraining/model_scaling.tex",
        "results/video_recognition.tex",
        "results/tables/speech_ast_results.tex",
        "posttraining.tex",
        "results/tables/benchmarks.tex",
        "inference/fp8.tex",
        "vision/data.tex",
        "introduction.tex",
        "pretraining/data.tex",
        "pretraining/model_architecture.tex",
        "results/finetuned.tex",
        "results/safety.tex",
        "results/speech.tex",
    ),
}

PACK_EXPECTATIONS = {
    "16k": (16_000, 16_384, 8_000),
    "32k": (32_000, 32_768, 16_000),
    "64k": (64_000, 65_536, 22_000),
}


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


def _queries(materialized):
    return sorted(
        (
            query
            for query in materialized.queries
            if query.query_type == "paper_revision_section_reconciliation"
        ),
        key=lambda query: tuple(PACK_EXPECTATIONS).index(
            query.preferred_length_buckets[0]
        ),
    )


def test_llama3_candidate_declares_strict_three_view_exact_contract() -> None:
    config = yaml.safe_load(CONFIG.read_text())

    assert config["required_seed_start"] == SEED
    assert config["source_workflow_seeds"] == [SEED]
    assert config["source_workflow_bundle"] == str(BUNDLE.relative_to(ROOT))
    assert config["source_workflow_query_types"] == [
        "paper_revision_section_reconciliation"
    ]
    assert config["real_workflow_query_length_buckets"] == {
        "paper_revision_section_reconciliation": ["16k", "32k", "64k"]
    }
    assert config["real_workflow_views"] == [
        "full",
        "cf",
        "ordered_artifact_view",
    ]
    assert config["exact_pack_admission"] is True
    assert config["strict_semantic_verification"] is True
    assert config["strict_cf_replay"] is True
    assert config["release_min_evidence_tokens"] == {
        "16k": 8000,
        "32k": 16000,
        "64k": 22000,
    }


def test_llama3_materializes_changed_endpoint_and_remove_one_program() -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    events = {event.id: event for event in world.events}
    queries = _queries(materialized)

    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    claim_sets: list[set[str]] = []
    for query in queries:
        tier = query.preferred_length_buckets[0]
        selected = [events[event_id] for event_id in query.essential_event_ids]
        claim_events = [
            event for event in selected if event.params.get("section_claim_id")
        ]
        claim_by_path = {
            str(event.params["section_source_path"]): event for event in claim_events
        }
        assert tuple(claim_by_path) == STAGE_PATHS[tier]
        assert len(claim_by_path) == len(set(claim_by_path))
        claim_sets.append(
            {str(event.params["section_claim_id"]) for event in claim_events}
        )
        for source_path, event in claim_by_path.items():
            claim_id, claim_quote = CLAIMS[source_path]
            assert event.params["record_id"] == f"{WORK_ID}v3"
            assert event.params["revision_id"] == "v3"
            assert event.params["section_claim_id"] == claim_id
            assert event.params["section_claim_quote"] == claim_quote
            assert str(event.params["text"]).count(claim_quote) == 1
            [receipt] = event.params["source_compile_receipts"]
            assert receipt["path"] == source_path

        targets = [
            event
            for event in selected
            if event.type == "arxiv_revision"
            and event.params.get("record_id") in {f"{WORK_ID}v1", f"{WORK_ID}v2"}
        ]
        assert len(targets) == {"16k": 0, "32k": 1, "64k": 2}[tier]
        for target in targets:
            [target_span] = target.params["source_file_spans"]
            [target_receipt] = target.params["source_compile_receipts"]
            assert target_span["path"] == "pretraining/model_scaling.tex"
            assert str(target.params["text"]).count(V2_SCALING_ROW) == 1
        if tier == "32k":
            [target] = targets
            [target_receipt] = target.params["source_compile_receipts"]
            assert target_receipt["source_text_sha256"] == V2_FILE_SHA256

        source = claim_by_path["pretraining/model_scaling.tex"]
        [source_receipt] = source.params["source_compile_receipts"]
        assert source_receipt["source_text_sha256"] == V3_FILE_SHA256
        assert str(source.params["text"]).count(V3_SCALING_ROW) == 1
        assert V3_FILE_SHA256 != V2_FILE_SHA256

        relations = [
            event for event in selected if event.type == "arxiv_revision_relation"
        ]
        assert len(relations) == {"16k": 0, "32k": 1, "64k": 2}[tier]
        assert all(
            valid_arxiv_revision_relation_event(relation, events)
            for relation in relations
        )
        if tier != "16k":
            latest = next(
                relation
                for relation in relations
                if relation.params["source_record_id"] == f"{WORK_ID}v3"
            )
            target = next(
                event
                for event in targets
                if event.params["record_id"] == f"{WORK_ID}v2"
            )
            assert latest.params["target_record_id"] == f"{WORK_ID}v2"
            assert latest.params["source_record_event_id"] == source.id
            assert latest.params["target_record_event_id"] == target.id
        assert any(
            event.type == "arxiv_section_reconciliation_control" for event in selected
        )
        assert selected[-1].type == "arxiv_section_reconciliation_decision"

        essential = [
            artifact
            for artifact in materialized.artifacts["focal"]
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert semantic_answer_from_artifacts(world, query, essential) == query.answer
        assert query.answer.startswith(
            {
                "16k": CLAIMS[STAGE_PATHS[tier][0]][1],
                "32k": "v3 revision_of v2 || ",
                "64k": "v3 revision_of v2 || v2 revision_of v1 || ",
            }[tier]
        )
        assert all(CLAIMS[path][1] in query.answer for path in STAGE_PATHS[tier])
        assert all(
            CLAIMS[path][1] not in query.answer
            for path in set(CLAIMS) - set(STAGE_PATHS[tier])
        )
        assert V3_SCALING_ROW in query.answer
        assert query.cf_answer != query.answer
        assert query.cf_event_id == source.id
        assert query.cf_param_updates["section_claim_quote"] == CF_SCALING_ROW
        assert str(query.cf_param_updates["text"]).count(CF_SCALING_ROW) == 1
        assert V3_SCALING_ROW not in str(query.cf_param_updates["text"])

        _, cf_artifacts = render_cf_view(world, query)
        cf_essential = [
            artifact
            for artifact in cf_artifacts
            if artifact.artifact_id in query.essential_artifact_ids
        ]
        assert (
            semantic_answer_from_artifacts(
                world,
                query,
                cf_essential,
                extra_overrides={query.cf_event_id: query.cf_param_updates},
            )
            == query.cf_answer
        )
        changed = [
            artifact.artifact_id
            for artifact in essential
            if next(
                candidate
                for candidate in cf_essential
                if candidate.artifact_id == artifact.artifact_id
            ).text
            != artifact.text
        ]
        assert len(changed) == 1
        changed_artifact = next(
            artifact for artifact in cf_essential if artifact.artifact_id == changed[0]
        )
        assert query.cf_event_id in changed_artifact.reveals_events
        for removed in essential:
            remaining = [artifact for artifact in essential if artifact is not removed]
            assert (
                answer_from_artifacts(
                    world, query, remaining, enforce_preconditions=True
                )
                == "unknown"
            )
            assert semantic_answer_from_artifacts(world, query, remaining) == "unknown"

        relation_edges = real_source_relation_edges(world, query, essential)
        assert {
            (edge["child_record_id"], edge["parent_record_id"])
            for edge in relation_edges
        } == {
            "16k": set(),
            "32k": {(f"{WORK_ID}v3", f"{WORK_ID}v2")},
            "64k": {
                (f"{WORK_ID}v3", f"{WORK_ID}v2"),
                (f"{WORK_ID}v2", f"{WORK_ID}v1"),
            },
        }[tier]
        assert context_source_relation_count(world, essential, spec=query) == {
            "16k": 0,
            "32k": 1,
            "64k": 2,
        }[tier]

    assert claim_sets[0] < claim_sets[1] < claim_sets[2]


def test_llama3_exact_full_cf_ordered_views_span_required_windows() -> None:
    materialized = _materialized()
    world = materialized.worlds["focal"]
    tokenizer = _load_exact_tokenizer("Qwen/Qwen3.5-4B", TOKENIZER_REVISION, True)

    def count(text: str) -> int:
        return tokenizer_token_count(text, tokenizer)

    for query in _queries(materialized):
        tier = query.preferred_length_buckets[0]
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
            length_bucket=tier,
            target_tokens=exact_context_pack_target(
                query.question, "first", tier, tokenizer
            ),
            rng=random.Random(stable_seed(SEED, query.query_id, "pack")),
            min_distance_tokens=PACK_EXPECTATIONS[tier][2],
            walk_ids=[],
            min_semantic_tokens=800,
            token_counter=count,
        )
        assert packed.ok, packed.reject_reason

        packed_cf = {artifact.artifact_id: artifact for artifact in views["cf"]}
        cf_view = [
            packed_cf.get(artifact.artifact_id, artifact)
            for artifact in packed.artifacts
        ]
        ordered = sorted(
            packed.artifacts,
            key=lambda artifact: (artifact.time, artifact.artifact_id),
        )
        assert [artifact.artifact_id for artifact in ordered] != [
            artifact.artifact_id for artifact in packed.artifacts
        ]

        prompts = {
            "full": wrap_prompt(query.question, packed.text, "first"),
            "cf": wrap_prompt(query.question, join_artifacts(cf_view), "first"),
            "ordered_artifact_view": wrap_prompt(
                query.question, join_artifacts(ordered), "first"
            ),
        }
        lower, upper, min_span = PACK_EXPECTATIONS[tier]
        prompt_tokens = {view: count(prompt) for view, prompt in prompts.items()}
        assert all(lower <= tokens <= upper for tokens in prompt_tokens.values()), (
            prompt_tokens
        )
        assert len(set(prompt_tokens.values())) == 1
        assert prompts["full"] != prompts["cf"]
        assert prompts["full"] != prompts["ordered_artifact_view"]
        assert all(
            sentence_near_dup_ratio(view) <= 0.25
            for view in (packed.artifacts, cf_view, ordered)
        )
        essential_ids = set(query.essential_artifact_ids)
        assert covering_span_tokens(packed.artifacts, essential_ids, count) > min_span
        assert covering_span_tokens(ordered, essential_ids, count) > min_span
