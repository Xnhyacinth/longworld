from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from longworld.core.engine import (
    answer_from_artifacts,
    semantic_answer_from_artifacts,
)
from longworld.core.graph import graph_stats
from longworld.core.promotion import _replayed_source_metadata
from longworld.core.render import semantic_attestation_valid
from longworld.core.sampler import materialize
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.sourceworkflow import (
    SourceEvidence,
    SourceRecord,
    SourceRelation,
    SourceWorkflow,
)
from longworld.core.taxonomy import EvidenceRole, SourceOrigin, artifact_classification
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.core.world import WorldSimulator
from longworld.domains.researchlab.events import apply_event, check_preconditions
from longworld.domains.researchlab.simulate import (
    _paper_benchmark_trace_records,
    canonical_researchlab_source_visible_text,
)
from scripts.generate import real_source_relation_edges


def _record(
    revision: str,
    occurred_at: str,
    *,
    abstract_score: str,
    abstract_days: str,
    detailed_score: str,
    benchmark: str = "WMT 2014 English-to-French translation task",
) -> SourceRecord:
    abstract = (
        f"On the {benchmark}, our model "
        "establishes a new single-model state-of-the-art BLEU score of "
        f"{abstract_score} after training for {abstract_days} days on eight GPUs."
    )
    detailed = (
        f"On the {benchmark}, our big model "
        f"achieves a BLEU score of ${detailed_score}$, outperforming earlier "
        "single models."
    )
    payload = {
        "entry_id": f"https://arxiv.org/abs/1706.03762{revision}",
        "kind": "arxiv_api_entry",
        "latex_sources": [
            {
                "path": "background.tex",
                "text": f"Authentic background retained for {revision} source view.",
            },
            {"path": "ms.tex", "text": abstract},
            {"path": "results.tex", "text": detailed},
            {
                "path": "training.tex",
                "text": (
                    "We trained our models on one machine with 8 NVIDIA P100 GPUs. "
                    f"Training protocol retained for {revision} source view."
                ),
            },
        ],
        "revision": revision,
        **(
            {"previous_revision_id": f"v{int(revision[1:]) - 1}"}
            if revision != "v1"
            else {}
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    return SourceRecord(
        record_id=f"arxiv:1706.03762{revision}",
        kind="manuscript_revision",
        occurred_at=occurred_at,
        text=text,
        source_url=f"https://arxiv.org/abs/1706.03762{revision}",
        retrieval_url=(
            "https://export.arxiv.org/api/query?id_list=1706.03762" + revision
        ),
        source_family="arxiv_record",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=(),
        attributes=(("revision_id", revision), ("work_id", "arxiv:1706.03762")),
    )


def _relation(source: SourceRecord, target: SourceRecord) -> SourceRelation:
    quote = f'"previous_revision_id": "{target.attribute("revision_id")}"'
    start = source.text.index(quote)
    return SourceRelation(
        relation_id=f"{source.record_id}:revision-of",
        kind="revision_of",
        source_record_id=source.record_id,
        target_record_id=target.record_id,
        evidence=(
            SourceEvidence(
                record_id=source.record_id,
                evidence_quote=quote,
                char_start=start,
                char_end=start + len(quote),
                source_sha256=source.source_sha256,
            ),
        ),
    )


def _workflow() -> SourceWorkflow:
    v1 = _record(
        "v1",
        "2017-06-12T17:57:34Z",
        abstract_score="41.0",
        abstract_days="3.5",
        detailed_score="41.0",
    )
    v2 = _record(
        "v2",
        "2017-06-19T16:49:45Z",
        abstract_score="41.2",
        abstract_days="4.5",
        detailed_score="41.17",
    )
    v3 = _record(
        "v3",
        "2017-06-20T05:20:02Z",
        abstract_score="41.0",
        abstract_days="3.5",
        detailed_score="41.17",
    )
    return SourceWorkflow(
        workflow_id="source:paper_workflow:benchmark-trace:1706.03762",
        component_digest="2" * 64,
        source_kind="paper_workflow",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("arxiv_record",),
        provenance_ids=(v1.provenance_id, v2.provenance_id, v3.provenance_id),
        records=(v1, v2, v3),
        relations=(_relation(v2, v1), _relation(v3, v2)),
    )


def test_attention_config_uses_a_stable_noncolliding_world_identity() -> None:
    config = yaml.safe_load(
        Path("configs/p12_paper_attention_benchmark_trace.yaml").read_text()
    )
    [seed] = config["source_workflow_seeds"]

    assert seed == config["required_seed_start"] == 170603762
    materialized = materialize(
        seed,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    world_id = materialized.worlds["focal"].spec["world_id"]

    assert world_id.startswith("lab170603762-")
    assert world_id != "lab000001-fenlightbench-18:focal"


def test_attention_benchmark_trace_has_growing_real_revision_proof() -> None:
    workflow = _workflow()
    assert _paper_benchmark_trace_records(workflow) is not None

    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    specs = [
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
    ]

    assert [spec.preferred_length_buckets for spec in specs] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert len({spec.base_task_group for spec in specs}) == 1
    assert len({spec.semantic_growth_group for spec in specs}) == 1
    assert [graph_stats(world, spec)["proof_depth"] for spec in specs] == [2, 3, 4]
    assert [spec.proof_depth for spec in specs] == [2, 3, 4]
    events = {event.id: event for event in world.events}
    assert [
        sum(
            events[event_id].type == "arxiv_revision_relation"
            for event_id in spec.sufficient_event_ids
        )
        for spec in specs
    ] == [0, 1, 2]
    context_events = [
        event for event in world.events if event.type == "arxiv_revision_context"
    ]

    assert context_events
    assert all(
        event.params["source_binding_provenance"] == "verified_derived"
        for event in context_events
    )
    assert all(
        event.params["source_origin"] == "real_derived" for event in context_events
    )
    artifacts = {
        event_id: artifact
        for artifact in materialized.artifacts["focal"]
        for event_id in artifact.reveals_events
    }
    assert all(
        artifact_classification(artifacts[event.id]).evidence_role
        is EvidenceRole.NATURAL_BACKGROUND
        for event in context_events
    )
    for event in context_events:
        evidence = [
            candidate
            for candidate in world.events
            if candidate.type == "arxiv_revision"
            and candidate.params.get("record_id") == event.params["record_id"]
        ]
        assert set(event.params["source_view_basenames"]).isdisjoint(
            basename
            for candidate in evidence
            for basename in candidate.params["source_view_basenames"]
        )
        assert (
            canonical_researchlab_source_visible_text(event) == artifacts[event.id].text
        )
    assert [len(spec.sufficient_event_ids) for spec in specs] == [4, 7, 10]
    assert [
        sum(
            events[event_id].type == "arxiv_revision"
            for event_id in spec.sufficient_event_ids
        )
        for spec in specs
    ] == [3, 5, 7]
    assert specs[0].answer == (
        "WMT 2014 English-to-French translation task | training v1 8 NVIDIA P100 "
        "GPUs | v1 41.0@3.5d | detail v1 41.0"
    )
    assert specs[1].answer.endswith("| detail v1 41.0 -> v2 41.17")
    assert specs[2].answer.endswith(
        "v3 41.0@3.5d | detail v1 41.0 -> v2 41.17 -> v3 41.17"
    )

    artifacts = materialized.artifacts["focal"]
    first_tier_sources = [
        events[event_id]
        for event_id in specs[0].sufficient_event_ids
        if events[event_id].type == "arxiv_revision"
    ]
    assert len(first_tier_sources) == 3
    for spec in specs:
        sources_by_revision: dict[str, list[set[str]]] = {}
        for event_id in spec.sufficient_event_ids:
            event = events[event_id]
            if event.type != "arxiv_revision":
                continue
            sources_by_revision.setdefault(str(event.params["revision_id"]), []).append(
                set(event.params["source_view_basenames"])
            )
        assert len(sources_by_revision["v1"]) == 3
        assert all(
            len(channels) == (3 if revision == "v1" else 2)
            for revision, channels in sources_by_revision.items()
        )
        assert all(
            all(
                left.isdisjoint(right)
                for index, left in enumerate(channels)
                for right in channels[index + 1 :]
            )
            for channels in sources_by_revision.values()
        )
    source_chars: list[int] = []
    for spec in specs:
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in spec.essential_artifact_ids
        ]
        assert semantic_answer_from_artifacts(world, spec, essential) == spec.answer
        assert all(
            answer_from_artifacts(
                world,
                spec,
                [artifact for artifact in essential if artifact is not removed],
                enforce_preconditions=True,
            )
            != spec.answer
            for removed in essential
        )
        assert all(
            semantic_answer_from_artifacts(
                world,
                spec,
                [artifact for artifact in essential if artifact is not removed],
            )
            != spec.answer
            for removed in essential
        )
        source_chars.append(
            sum(
                len(artifact.text)
                for artifact in essential
                if (artifact.slots or {}).get("event_type") == "arxiv_revision"
            )
        )
        assert spec.cf_answer != spec.answer
    assert source_chars[0] < source_chars[1] < source_chars[2]


def test_combined_probe_marker_preserves_artifact_semantic_attestation(
    monkeypatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], "s" * 43)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-source-paper-diagnostic")
    monkeypatch.setenv(
        LOCAL_PROBE_COMBINED_ROLES_ENV,
        LOCAL_PROBE_TRUST_ISOLATION_VALUE,
    )
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
        and query.preferred_length_buckets == ["16k"]
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]

    assert essential
    assert all(
        (artifact.slots or {}).get("local_probe_trust_isolation")
        == LOCAL_PROBE_TRUST_ISOLATION_VALUE
        for artifact in essential
    )
    assert all(semantic_attestation_valid(artifact) for artifact in essential)
    target = essential[0]
    assert not semantic_attestation_valid(
        replace(
            target,
            slots={
                **(target.slots or {}),
                "local_probe_trust_isolation": "tampered",
            },
        )
    )
    assert not semantic_attestation_valid(
        replace(
            target,
            slots={
                key: value
                for key, value in (target.slots or {}).items()
                if key != "local_probe_trust_isolation"
            },
        )
    )


def test_single_role_artifact_semantic_payload_has_no_isolation_marker(
    monkeypatch,
) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], "s" * 43)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-source-paper-diagnostic")
    monkeypatch.delenv(LOCAL_PROBE_COMBINED_ROLES_ENV, raising=False)
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    artifact = next(
        artifact
        for artifact in materialized.artifacts["focal"]
        if (artifact.slots or {}).get("event_type") == "arxiv_revision"
    )

    assert "local_probe_trust_isolation" not in (artifact.slots or {})
    assert semantic_attestation_valid(artifact)


def test_revision_overlap_requires_visible_complete_directional_path() -> None:
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
        and query.preferred_length_buckets == ["64k"]
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    relations = [
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "arxiv_revision_relation"
    ]
    sources = [
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "arxiv_revision"
    ]

    # Tier and chain-cardinality metadata intentionally repeat across the two
    # authentic relation records; the proof remains below the release gate.
    assert sentence_near_dup_ratio(essential) < 0.05
    assert (
        sentence_near_dup_ratio(
            [artifact for artifact in essential if artifact is not relations[1]]
        )
        > 0.0
    )
    assert sentence_near_dup_ratio(sources) > 0.0
    middle_revision_channels = [
        artifact
        for artifact in sources
        if (artifact.slots or {}).get("source_record_id", "").endswith("v2")
    ]
    assert len(middle_revision_channels) == 2
    assert (
        sentence_near_dup_ratio(
            [
                artifact
                for artifact in essential
                if artifact not in middle_revision_channels
            ]
        )
        == 0.0
    )

    original = relations[0]
    slots = dict(original.slots or {})
    params = dict(slots["params"])
    params.update(
        {
            "source_record_id": params["target_record_id"],
            "target_record_id": params["source_record_id"],
            "source_revision_id": params["target_revision_id"],
            "target_revision_id": params["source_revision_id"],
        }
    )
    reversed_text = canonical_researchlab_source_visible_text(
        {"type": "arxiv_revision_relation", "params": params}
    )
    assert reversed_text is not None
    digest = hashlib.sha256(reversed_text.encode()).hexdigest()
    classification = dict(slots["classification"])
    classification["provenance_id"] = f"derived-sha256:{digest}"
    reversed_relation = replace(
        original,
        text=reversed_text,
        slots={
            **slots,
            "params": params,
            "classification": classification,
            "semantic_text_sha256": digest,
        },
    )
    assert (
        sentence_near_dup_ratio(
            [
                reversed_relation if artifact is original else artifact
                for artifact in essential
            ]
        )
        < 0.05
    )


def test_terminal_revision_relation_requires_prior_relation_replay() -> None:
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
        and query.preferred_length_buckets == ["64k"]
    )
    relations = [
        event
        for event in world.events
        if event.type == "arxiv_revision_relation"
        and event.id in spec.sufficient_event_ids
    ]
    assert len(relations) == 2
    first, terminal = relations
    simulator = WorldSimulator(
        spec=world.spec,
        init_values=world.init_values,
        check_preconditions=check_preconditions,
        apply_event=apply_event,
    )
    state = simulator.replay_events(
        world.events,
        up_to=terminal.time,
        skip_ids={first.id},
        enforce_preconditions=True,
    )

    assert first.id in terminal.required_inputs
    assert terminal.params["relation_id"] not in set(
        state.values.get("verified_revision_relations") or []
    )


def _benchmark_trace_source_metadata(
    length_bucket: str,
    *,
    mutate_terminal=None,
) -> tuple[list[dict[str, str]], dict]:
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
        and query.preferred_length_buckets == [length_bucket]
    )
    if mutate_terminal is not None:
        relations = [
            event
            for event in world.events
            if event.type == "arxiv_revision_relation"
            and event.id in spec.sufficient_event_ids
        ]
        assert relations
        terminal = relations[-1]
        replacement = mutate_terminal(terminal, relations)
        world = replace(
            world,
            events=[
                replacement if event is terminal else event for event in world.events
            ],
        )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    return (
        real_source_relation_edges(world, spec, essential),
        _replayed_source_metadata(world, spec, essential),
    )


def test_revision_relation_metadata_accepts_two_endpoints_and_valid_chain() -> None:
    candidate_counts = []
    replay_counts = []
    for bucket in ("16k", "32k", "64k"):
        candidate, replay = _benchmark_trace_source_metadata(bucket)
        candidate_counts.append(
            sum(edge["relation_provenance"] == "authentic_source" for edge in candidate)
        )
        replay_counts.append(len(replay["authentic_source_relation_edges"]))

    assert candidate_counts == replay_counts == [0, 1, 2]


@pytest.mark.parametrize(
    "mutation",
    ["arbitrary_third", "missing_param", "wrong_type", "broken_chain"],
)
def test_revision_relation_metadata_rejects_invalid_chain_input(mutation: str) -> None:
    def mutate(terminal, relations):
        params = dict(terminal.params)
        required_inputs = list(terminal.required_inputs)
        prior = relations[-2]
        if mutation == "arbitrary_third":
            required_inputs[-1] = terminal.params["source_record_event_id"]
        elif mutation == "missing_param":
            params.pop("required_prior_relation_event_id")
        elif mutation == "wrong_type":
            wrong_id = terminal.params["source_record_event_id"]
            params["required_prior_relation_event_id"] = wrong_id
            required_inputs[-1] = wrong_id
        elif mutation == "broken_chain":
            params["required_prior_relation_event_id"] = prior.id
            params["target_record_id"] = "arxiv:unrelated:v0"
        return replace(terminal, params=params, required_inputs=required_inputs)

    candidate, replay = _benchmark_trace_source_metadata("64k", mutate_terminal=mutate)

    candidate_authentic = [
        edge for edge in candidate if edge["relation_provenance"] == "authentic_source"
    ]
    assert candidate_authentic == replay["authentic_source_relation_edges"]
    assert len(candidate_authentic) < 2


@pytest.mark.parametrize("length_bucket", ["16k", "32k", "64k"])
def test_attention_benchmark_trace_corruption_fails_semantic_replay(
    length_bucket: str,
) -> None:
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    [spec] = [
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
        and query.preferred_length_buckets == [length_bucket]
    ]
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    original_score = "41.0" if length_bucket == "16k" else "41.17"
    corrupted = [
        replace(
            artifact,
            text=artifact.text.replace(original_score, "99.17", 1),
        )
        if original_score in artifact.text
        else artifact
        for artifact in essential
    ]

    assert semantic_answer_from_artifacts(world, spec, corrupted) == "unknown"


@pytest.mark.parametrize(
    ("length_bucket", "training_revision"),
    [("16k", "v1"), ("32k", "v2"), ("64k", "v3")],
)
def test_attention_benchmark_trace_consumes_revision_training_hardware(
    length_bucket: str,
    training_revision: str,
) -> None:
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
        and query.preferred_length_buckets == [length_bucket]
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    corrupted = [
        replace(
            artifact,
            text=artifact.text.replace("8 NVIDIA P100 GPUs", "4 NVIDIA P100 GPUs", 1),
        )
        if (artifact.slots or {})
        .get("source_record_id", "")
        .endswith(training_revision)
        and "8 NVIDIA P100 GPUs" in artifact.text
        else artifact
        for artifact in essential
    ]

    assert corrupted != essential
    assert semantic_answer_from_artifacts(world, spec, corrupted) == "unknown"


def test_benchmark_trace_rejects_non_reverting_revision_sequence() -> None:
    workflow = _workflow()
    v3 = _record(
        "v3",
        "2017-06-20T05:20:02Z",
        abstract_score="41.3",
        abstract_days="3.5",
        detailed_score="41.17",
    )
    altered = replace(
        workflow,
        records=(*workflow.records[:2], v3),
        relations=(workflow.relations[0], _relation(v3, workflow.records[1])),
    )

    assert _paper_benchmark_trace_records(altered) is None


def test_benchmark_trace_rejects_cross_benchmark_revision_sequence() -> None:
    workflow = _workflow()
    v2 = _record(
        "v2",
        "2017-06-19T16:49:45Z",
        abstract_score="41.2",
        abstract_days="4.5",
        detailed_score="41.17",
        benchmark="Unrelated evaluation task",
    )
    altered = replace(
        workflow,
        records=(workflow.records[0], v2, workflow.records[2]),
        relations=(
            _relation(v2, workflow.records[0]),
            _relation(workflow.records[2], v2),
        ),
    )

    assert _paper_benchmark_trace_records(altered) is None


def test_benchmark_trace_passes_semantic_cf_and_remove_one_gates(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "LONGWORLD_ATTESTATION_KEY",
        "paper-benchmark-trace-test-key-32-bytes!",
    )
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[_workflow()],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    artifacts = materialized.artifacts["focal"]
    specs = [
        query
        for query in materialized.queries
        if query.query_type == "real_benchmark_revision_trace"
    ]

    for spec in specs:
        _cf_world, cf_artifacts = render_cf_view(world, spec)
        verification, notes = verify_question(
            world,
            spec,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert verification.semantic_sufficient, notes
        assert verification.strict_executable_sufficient, notes
        assert verification.remove_one_fails, notes
        assert verification.counterfactual_changes_answer, notes
        assert verification.counterfactual_replay_sufficient, notes
        assert verification.essential_text_grounded, notes
