from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from longworld.core.engine import (
    answer_from_artifacts,
    answer_from_events,
    semantic_answer_from_artifacts,
)
from longworld.core.pack import (
    compute_view_metrics,
    dependency_evidence_ids,
    join_artifacts,
    wrap_prompt,
)
from longworld.core.promotion import (
    _replayed_quality_metrics,
    _replayed_source_metadata,
)
from longworld.core.sampler import materialize
from longworld.core.semantic import sentence_near_dup_ratio
from longworld.core.sourceworkflow import (
    SourceEvidence,
    SourceFact,
    SourceRecord,
    SourceRelation,
    SourceWorkflow,
)
from longworld.core.taxonomy import SourceOrigin, artifact_classification
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.researchlab.queries import _counterfactual_grounded_source
from longworld.domains.researchlab.simulate import (
    _paper_multiband_records,
    canonical_researchlab_source_event_envelope,
    canonical_researchlab_source_visible_text,
)


def _record(
    *,
    record_id: str,
    revision: str,
    occurred_at: str,
    body: str,
    added_fact: str = "",
    extra_sources: tuple[tuple[str, str], ...] = (),
    previous_revision: str = "",
    work_id: str = "2203.01928",
) -> SourceRecord:
    text = (
        json.dumps(
            {
                "entry_id": f"https://arxiv.org/abs/{work_id}{revision}",
                "kind": "arxiv_api_entry",
                "latex_sources": [
                    {"path": "parts/acknowledgements.tex", "text": body},
                    *({"path": path, "text": text} for path, text in extra_sources),
                ],
                "revision": revision,
                **(
                    {"previous_revision_id": previous_revision}
                    if previous_revision
                    else {"previous_revision_id": "v1"}
                    if revision == "v2"
                    else {}
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    facts: tuple[SourceFact, ...] = ()
    if added_fact:
        start = text.index(added_fact)
        facts = (
            SourceFact(
                fact_id="revision_added_text:funding_disclosure",
                field="revision_added_text",
                value=added_fact,
                record_id=record_id,
                evidence_quote=added_fact,
                char_start=start,
                char_end=start + len(added_fact),
                value_offset=0,
                source_sha256=digest,
            ),
        )
    return SourceRecord(
        record_id=record_id,
        kind="manuscript_revision",
        occurred_at=occurred_at,
        text=text,
        source_url=f"https://arxiv.org/abs/{work_id}{revision}",
        retrieval_url=(
            f"https://export.arxiv.org/api/query?id_list={work_id}" + revision
        ),
        source_family="arxiv_record",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=facts,
        attributes=(
            ("revision_id", revision),
            ("work_id", f"arxiv:{work_id}"),
        ),
    )


def _workflow(
    *,
    work_id: str = "2203.01928",
    disclosure: str = (
        "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
        "Office of Naval Research (ONR), NSF 172251."
    ),
) -> tuple[SourceWorkflow, str]:
    shared_revision_text = (
        "This manuscript reports the same core experimental protocol across "
        "both publicly archived revisions."
    )
    v1 = _record(
        record_id=f"arxiv:{work_id}v1",
        revision="v1",
        occurred_at="2022-03-03T18:59:03Z",
        body=(
            f"Revision one introduction is specific to this archive. "
            f"{shared_revision_text} The authors thank the anonymous reviewers."
        ),
        work_id=work_id,
    )
    v2 = _record(
        record_id=f"arxiv:{work_id}v2",
        revision="v2",
        occurred_at="2022-06-07T11:25:15Z",
        body=(
            f"Revision two introduction is specific to this archive. "
            f"{shared_revision_text} {disclosure}"
        ),
        added_fact=disclosure,
        work_id=work_id,
    )
    relation_quote = '"previous_revision_id": "v1"'
    relation_start = v2.text.index(relation_quote)
    relation = SourceRelation(
        relation_id=f"revision-{work_id}-v2-v1",
        kind="revision_of",
        source_record_id=v2.record_id,
        target_record_id=v1.record_id,
        evidence=(
            SourceEvidence(
                record_id=v2.record_id,
                evidence_quote=relation_quote,
                char_start=relation_start,
                char_end=relation_start + len(relation_quote),
                source_sha256=v2.source_sha256,
            ),
        ),
    )
    workflow = SourceWorkflow(
        workflow_id=f"source:paper_workflow:legacy:{work_id}",
        component_digest="0" * 64,
        source_kind="paper_workflow",
        target_domain="researchlab",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("arxiv_record",),
        provenance_ids=(v1.provenance_id, v2.provenance_id),
        records=(v1, v2),
        relations=(relation,),
    )
    return workflow, disclosure


def test_two_legacy_paper_workflows_isolate_answers_and_counterfactuals() -> None:
    first, first_disclosure = _workflow()
    second, _ = _workflow(
        work_id="2401.01234",
        disclosure=first_disclosure.replace("172251", "999999"),
    )
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[first, second],
        include_program_joins=False,
    )
    specs = [
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    ]

    assert len(specs) == 2
    assert len({spec.query_id for spec in specs}) == 2
    assert len({spec.answer_key for spec in specs}) == 2
    assert {spec.answer for spec in specs} == {
        "2022-03-03 | Aviva | ONR | NSF 172251",
        "2022-03-03 | Aviva | ONR | NSF 999999",
    }
    assert all(spec.cf_answer != spec.answer for spec in specs)


def test_one_legacy_paper_workflow_isolates_each_revision_relation() -> None:
    workflow, _ = _workflow(
        disclosure=(
            "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
            "Office of Naval Research (ONR), NSF 111111."
        )
    )
    later_disclosure = (
        "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
        "Office of Naval Research (ONR), NSF 222222."
    )
    v3 = _record(
        record_id="arxiv:2203.01928v3",
        revision="v3",
        occurred_at="2022-06-09T11:32:36Z",
        body=later_disclosure,
        added_fact=later_disclosure,
        previous_revision="v2",
    )
    relation_quote = '"previous_revision_id": "v2"'
    relation_start = v3.text.index(relation_quote)
    relation = SourceRelation(
        relation_id="revision-2203.01928-v3-v2",
        kind="revision_of",
        source_record_id=v3.record_id,
        target_record_id="arxiv:2203.01928v2",
        evidence=(
            SourceEvidence(
                record_id=v3.record_id,
                evidence_quote=relation_quote,
                char_start=relation_start,
                char_end=relation_start + len(relation_quote),
                source_sha256=v3.source_sha256,
            ),
        ),
    )
    workflow = replace(
        workflow,
        provenance_ids=(*workflow.provenance_ids, v3.provenance_id),
        records=(*workflow.records, v3),
        relations=(*workflow.relations, relation),
    )

    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[workflow],
        include_program_joins=False,
    )
    specs = [
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    ]

    assert len(specs) == 2
    assert len({spec.query_id for spec in specs}) == 2
    assert len({spec.answer_key for spec in specs}) == 2
    assert {spec.answer for spec in specs} == {
        "2022-03-03 | Aviva | ONR | NSF 111111",
        "2022-06-07 | Aviva | ONR | NSF 222222",
    }
    assert all(spec.cf_answer != spec.answer for spec in specs)


def _multiband_workflow(
    *,
    work_id: str = "2203.01928",
    disclosure: str = (
        "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
        "Office of Naval Research (ONR), NSF 172251."
    ),
) -> tuple[SourceWorkflow, str]:

    def sections(revision: str) -> tuple[tuple[str, str], ...]:
        return (
            ("parts/abstract.tex", f"Abstract evidence unique to {revision}. " * 40),
            (
                "main.tex",
                f"Main routing unique to {revision}. " * 30
                + (
                    "\\input{parts/acknowledgements}"
                    if revision in {"v2", "v3"}
                    else ""
                ),
            ),
            (
                "parts/illustrations_sup.tex",
                f"Illustration note unique to {revision}. " * 20,
            ),
            (
                "parts/introduction.tex",
                f"Introduction history unique to {revision}. " * 120,
            ),
            ("parts/example.tex", f"Worked example unique to {revision}. " * 180),
            (
                "parts/expressions.tex",
                f"Expression analysis unique to {revision}. " * 120,
            ),
            ("parts/feature.tex", f"Feature analysis unique to {revision}. " * 100),
            (
                "parts/experiments.tex",
                f"Experiment result unique to {revision}. " * 80,
            ),
            (
                "parts/experiments_sup.tex",
                f"Supplemental result unique to {revision}. " * 80,
            ),
            (
                "parts/experiments_details.tex",
                f"Experiment detail unique to {revision}. " * 240,
            ),
            ("parts/discussion.tex", f"Discussion unique to {revision}. " * 80),
        )

    v1 = _record(
        record_id=f"arxiv:{work_id}v1",
        revision="v1",
        occurred_at="2022-03-03T18:59:03Z",
        body="The first public revision contains no funding disclosure.",
        extra_sources=sections("v1"),
        work_id=work_id,
    )
    v2 = _record(
        record_id=f"arxiv:{work_id}v2",
        revision="v2",
        occurred_at="2022-06-07T11:25:15Z",
        body=disclosure,
        added_fact=disclosure,
        extra_sources=sections("v2"),
        work_id=work_id,
    )
    v3 = _record(
        record_id=f"arxiv:{work_id}v3",
        revision="v3",
        occurred_at="2022-06-09T11:32:36Z",
        body="The terminal public revision preserves the accepted disclosure.",
        extra_sources=sections("v3"),
        previous_revision="v2",
        work_id=work_id,
    )

    def relation(source: SourceRecord, target: SourceRecord) -> SourceRelation:
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

    return (
        SourceWorkflow(
            workflow_id=f"source:paper_workflow:multiband:{work_id}",
            component_digest="1" * 64,
            source_kind="paper_workflow",
            target_domain="researchlab",
            source_origin=SourceOrigin.REAL_PUBLIC,
            source_families=("arxiv_record",),
            provenance_ids=(v1.provenance_id, v2.provenance_id, v3.provenance_id),
            records=(v1, v2, v3),
            relations=(relation(v2, v1), relation(v3, v2)),
        ),
        disclosure,
    )


def _replace_record_sources(
    workflow: SourceWorkflow,
    revision: str,
    mutate,
) -> SourceWorkflow:
    record = next(
        item for item in workflow.records if item.attribute("revision_id") == revision
    )
    payload = json.loads(record.text)
    mutate(payload["latex_sources"])
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    updated = replace(
        record,
        text=text,
        source_sha256=digest,
        text_sha256=digest,
        provenance_id=f"sha256:{digest}",
        facts=tuple(replace(fact, source_sha256=digest) for fact in record.facts),
    )
    return replace(
        workflow,
        records=tuple(updated if item is record else item for item in workflow.records),
    )


def test_multiband_rejects_include_moved_out_of_shared_main_source() -> None:
    workflow, _ = _multiband_workflow()

    def move_marker(sources) -> None:
        by_path = {source["path"]: source for source in sources}
        marker = "\\input{parts/acknowledgements}"
        by_path["main.tex"]["text"] = by_path["main.tex"]["text"].replace(marker, "")
        by_path["parts/discussion.tex"]["text"] += marker

    altered = _replace_record_sources(workflow, "v2", move_marker)

    assert _paper_multiband_records(altered) is None


def test_multiband_rejects_missing_shared_main_source() -> None:
    workflow, _ = _multiband_workflow()

    def remove_main(sources) -> None:
        sources[:] = [source for source in sources if source["path"] != "main.tex"]

    altered = _replace_record_sources(workflow, "v1", remove_main)

    assert _paper_multiband_records(altered) is None


def test_two_multiband_paper_workflows_have_isolated_answers_and_query_ids() -> None:
    first, first_disclosure = _multiband_workflow()
    second_disclosure = first_disclosure.replace("172251", "999999")
    second, _ = _multiband_workflow(work_id="2401.01234", disclosure=second_disclosure)

    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        n_workstreams=0,
        source_workflows=[first, second],
        include_program_joins=False,
    )
    specs = [
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    ]

    assert len(specs) == 6
    world = materialized.worlds["focal"]
    assert len({event.id for event in world.events}) == len(world.events)
    assert len({spec.query_id for spec in specs}) == 6
    assert len({spec.answer_key for spec in specs}) == 6
    answers_by_group: dict[str, set[str]] = {}
    for spec in specs:
        answers_by_group.setdefault(spec.base_task_group, set()).add(spec.answer)
    assert len(answers_by_group) == 2
    assert all(len(answers) == 1 for answers in answers_by_group.values())
    assert {next(iter(answers)) for answers in answers_by_group.values()} == {
        "2022-03-03 | Aviva | ONR | NSF 172251 | v3 2022-06-09",
        "2022-03-03 | Aviva | ONR | NSF 999999 | v3 2022-06-09",
    }


def test_real_arxiv_multiband_queries_grow_source_history_and_proof() -> None:
    workflow, _ = _multiband_workflow()
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
        if query.query_type == "real_revision_added_text"
    ]

    assert [spec.preferred_length_buckets for spec in specs] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert len({spec.base_task_group for spec in specs}) == 1
    assert len({spec.semantic_growth_group for spec in specs}) == 1
    assert [spec.proof_depth for spec in specs] == [2, 3, 4]
    events_by_id = {event.id: event for event in world.events}
    revision_events = {
        (
            str(event.params.get("source_view_tier")),
            str(event.params.get("revision_id")),
        ): event
        for event in world.events
        if event.type == "arxiv_revision" and event.params.get("source_view_tier")
    }
    v1_16k_files = set(revision_events[("16k", "v1")].params["source_view_basenames"])
    v2_16k_files = set(revision_events[("16k", "v2")].params["source_view_basenames"])
    assert "main.tex" in v1_16k_files & v2_16k_files
    assert "illustrations_sup.tex" not in v1_16k_files
    assert "acknowledgements.tex" not in v1_16k_files
    assert "acknowledgements.tex" in v2_16k_files
    assert "experiments_details.tex" in v2_16k_files
    assert "discussion.tex" not in v2_16k_files
    context_by_revision = {
        event.params["revision_id"]: event
        for event in world.events
        if event.type == "arxiv_revision_context"
        and event.params.get("source_view_tier") == "16k"
    }
    assert context_by_revision["v1"].params["source_view_basenames"] == [
        "illustrations_sup.tex"
    ]
    assert context_by_revision["v2"].params["source_view_basenames"] == [
        "discussion.tex"
    ]
    assert all(event.required_inputs == [] for event in context_by_revision.values())
    decision_16k = events_by_id[specs[0].essential_event_ids[-1]]
    assert decision_16k.params["required_new_include"] == "parts/acknowledgements"
    assert [
        sum(
            events_by_id[event_id].type == "arxiv_revision_relation"
            for event_id in spec.sufficient_event_ids
        )
        for spec in specs
    ] == [0, 1, 2]
    assert [len(spec.sufficient_event_ids) for spec in specs] == [4, 5, 6]

    artifacts = materialized.artifacts["focal"]
    source_chars = []
    for spec in specs:
        essential = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in spec.essential_artifact_ids
        ]
        assert sentence_near_dup_ratio(essential) < 0.05
        source_chars.append(
            sum(
                len(artifact.text)
                for artifact in essential
                if (artifact.slots or {}).get("event_type") == "arxiv_revision"
            )
        )
        assert (
            answer_from_artifacts(world, spec, essential, enforce_preconditions=True)
            == spec.answer
        )
        assert all(
            answer_from_artifacts(
                world,
                spec,
                [artifact for artifact in essential if artifact is not removed],
                enforce_preconditions=False,
            )
            != spec.answer
            for removed in essential
        )
        assert spec.cf_answer != spec.answer
    assert source_chars[0] < source_chars[1] < source_chars[2]
    assert {spec.answer for spec in specs} == {
        "2022-03-03 | Aviva | ONR | NSF 172251 | v3 2022-06-09"
    }
    essential_16k = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in specs[0].essential_artifact_ids
    ]
    corrupted_include = [
        replace(
            artifact,
            text=artifact.text.replace(
                "parts/acknowledgements", "parts/unrelated_appendix"
            ),
        )
        for artifact in essential_16k
    ]
    assert (
        semantic_answer_from_artifacts(world, specs[0], corrupted_include) == "unknown"
    )


def test_real_arxiv_revision_body_drives_state_answer_and_counterfactual() -> None:
    workflow, disclosure = _workflow()
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
    [spec] = [
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    ]
    artifacts = materialized.artifacts["focal"]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in spec.essential_artifact_ids
    ]

    assert spec.answer == "2022-03-03 | Aviva | ONR | NSF 172251"
    assert spec.cf_answer == "2022-03-03 | Aviva | ONR | NSF 172259"
    assert (
        answer_from_artifacts(world, spec, essential, enforce_preconditions=True)
        == "2022-03-03 | Aviva | ONR | NSF 172251"
    )
    assert all(
        answer_from_artifacts(
            world,
            spec,
            [item for item in essential if item.artifact_id != removed.artifact_id],
            enforce_preconditions=True,
        )
        == "unknown"
        for removed in essential
    )
    assert all(spec.answer not in artifact.text for artifact in essential)
    verification, notes = verify_question(
        world,
        spec,
        artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_single_doc_insufficient
    assert verification.essential_surface_gold_free
    assert all(
        check["replay_answer"] != spec.answer
        for check in notes["essential_single_docs"]
    )

    source_artifacts = [
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("source_record_id")
    ]
    assert {artifact.text for artifact in source_artifacts} == {
        (
            "% arXiv manuscript revision v1\n"
            "% arXiv submitted_at 2022-03-03T18:59:03Z\n"
            "Revision one introduction is specific to this archive. "
            "This manuscript reports the same core experimental protocol across "
            "both publicly archived revisions. The authors thank the anonymous "
            "reviewers."
        ),
        (
            "% arXiv manuscript revision v2\n"
            "% arXiv submitted_at 2022-06-07T11:25:15Z\n"
            "Revision two introduction is specific to this archive. "
            "This manuscript reports the same core experimental protocol across "
            f"both publicly archived revisions. {disclosure}"
        ),
    }
    assert all(
        artifact_classification(artifact).source_origin is SourceOrigin.REAL_DERIVED
        for artifact in source_artifacts
    )
    assert {
        artifact_classification(artifact).workflow_id for artifact in source_artifacts
    } == {world.world_id}
    assert {
        str((artifact.slots or {}).get("source_workflow_id") or "")
        for artifact in source_artifacts
    } == {workflow.workflow_id}

    replay_context = wrap_prompt(spec.question, join_artifacts(essential), "first")
    replay_metrics = compute_view_metrics(
        essential,
        dependency_evidence_ids(essential, spec),
        query_timing="first",
        context=replay_context,
    )
    replayed = _replayed_quality_metrics(
        {
            "length_bucket": replay_metrics.length_bucket,
            "position_bucket": replay_metrics.position_bucket,
            "query_timing": "first",
            "view": "full",
        },
        world,
        spec,
        essential,
    )
    source_tokens = sum(len(artifact.text.split()) for artifact in source_artifacts)
    assert replayed["semantic_tokens"]["event_bearing"] >= source_tokens
    assert (
        replayed["semantic_tokens"]["generic_background"]
        < replayed["semantic_tokens"]["event_bearing"]
    )
    assert all(
        str((artifact.slots or {}).get("parent_provenance_id") or "").startswith(
            "sha256:"
        )
        for artifact in source_artifacts
    )


def test_real_arxiv_revision_corrupted_body_fails_semantic_gate() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    corrupted = [
        replace(artifact, text="Corrupted scholarly body with no source evidence.")
        if (artifact.slots or {}).get("source_record_id") == "arxiv:2203.01928v2"
        else artifact
        for artifact in essential
    ]

    verification, _ = verify_question(
        world,
        spec,
        corrupted,
        verification_mode="candidate",
    )

    assert not verification.essential_text_grounded
    assert not verification.semantic_sufficient


def test_real_arxiv_relation_requires_both_source_artifacts_in_exact_proof() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    source = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("source_record_id") == "arxiv:2203.01928v2"
    )

    complete = _replayed_source_metadata(world, spec, essential)
    missing_endpoint = _replayed_source_metadata(
        world,
        spec,
        [artifact for artifact in essential if artifact is not source],
    )

    assert len(complete["authentic_source_relation_edges"]) == 1
    assert missing_endpoint["authentic_source_relation_edges"] == []


def test_real_arxiv_revision_corrupted_submission_date_fails_semantic_gate() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    assert any("2022-03-03" in artifact.text for artifact in essential)
    corrupted = [
        replace(artifact, text=artifact.text.replace("2022-03-03", "2022-03-04"))
        for artifact in essential
    ]

    assert semantic_answer_from_artifacts(world, spec, corrupted) == "unknown"

    verification, _ = verify_question(
        world,
        spec,
        corrupted,
        verification_mode="candidate",
    )

    assert not verification.essential_text_grounded
    assert not verification.semantic_sufficient


def test_real_arxiv_grounded_bindings_reject_source_and_relation_tampering() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    source = next(
        event
        for event in world.events
        if event.type == "arxiv_revision"
        and event.params["record_id"] == "arxiv:2203.01928v2"
    )
    relation = next(
        event for event in world.events if event.type == "arxiv_revision_relation"
    )
    source_binding = dict(source.params["grounded_source"])
    relation_binding = dict(relation.params["grounded_relation"])

    assert source.params["source_binding_provenance"] == "verified_derived"
    assert relation_binding["claimed_provenance_class"] == "authentic_source_api"
    for event in (source, relation):
        canonical = canonical_researchlab_source_event_envelope(
            world.spec,
            event_id=event.id,
            event_type=event.type,
            record_id=str(event.params.get("record_id") or ""),
            relation_id=str(event.params.get("relation_id") or ""),
        )
        assert canonical == {
            "id": event.id,
            "type": event.type,
            "time": event.time,
            "params": event.params,
            "visibility": list(event.visibility),
            "preconditions": list(event.preconditions),
            "causal_inputs": list(event.causal_inputs),
            "required_inputs": list(event.required_inputs),
            "relation_kinds": dict(event.relation_kinds),
            "skipped": event.skipped,
            "skip_reason": event.skip_reason,
        }
        artifact = next(
            item
            for item in materialized.artifacts["focal"]
            if event.id in item.reveals_events
        )
        assert canonical_researchlab_source_visible_text(canonical) == artifact.text
    assert spec.cf_param_updates["source_binding_provenance"] == (
        "synthetic_executable"
    )
    assert spec.cf_param_updates["source_origin"] == "synthetic_world"
    assert spec.cf_param_updates["parent_source_origin"] == "real_derived"
    assert any(
        str(fact["fact_id"]).endswith(":submitted_at")
        for fact in source_binding["facts"]
    )

    corrupt_hash = {**source_binding, "text_sha256": "0" * 64}
    corrupt_fact = dict(source_binding["facts"][-1])
    corrupt_fact["char_end"] -= 1
    corrupt_span = {
        **source_binding,
        "facts": [*source_binding["facts"][:-1], corrupt_fact],
    }
    corrupt_parent = {
        **relation_binding,
        "target_id": relation_binding["source_id"],
    }
    for event_id, updates in (
        (source.id, {"grounded_source": corrupt_hash}),
        (source.id, {"grounded_source": corrupt_span}),
        (
            relation.id,
            {"fact_char_start": int(relation.params["fact_char_start"]) + 1},
        ),
        (relation.id, {"target_revision_id": "v9"}),
        (relation.id, {"grounded_relation": corrupt_parent}),
    ):
        assert (
            answer_from_events(
                world,
                spec,
                set(spec.sufficient_event_ids),
                extra_overrides={event_id: updates},
                enforce_preconditions=True,
            )
            == "unknown"
        )


def test_real_arxiv_visible_headers_reject_hidden_identity_tampering() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    source = next(
        event
        for event in world.events
        if event.type == "arxiv_revision"
        and event.params["record_id"] == "arxiv:2203.01928v1"
    )

    for updates in (
        {"occurred_at": "2022-03-04T18:59:03Z"},
        {"revision_id": "v9"},
    ):
        assert (
            answer_from_events(
                world,
                spec,
                set(spec.sufficient_event_ids),
                extra_overrides={source.id: updates},
                enforce_preconditions=True,
            )
            == "unknown"
        )


def test_real_arxiv_canonical_envelope_rejects_coordinated_body_rewrite() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    world = materialized.worlds["focal"]
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    source = next(
        event
        for event in world.events
        if event.type == "arxiv_revision"
        and event.params["record_id"] == "arxiv:2203.01928v2"
    )
    original = str(source.params["text"])
    changed = original.replace("NSF 172251", "NSF 172299")
    grounded = _counterfactual_grounded_source(
        source.params["grounded_source"],
        original_quote=(
            "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
            "Office of Naval Research (ONR), NSF 172251."
        ),
        changed_quote=(
            "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
            "Office of Naval Research (ONR), NSF 172299."
        ),
        changed_text=changed,
    )
    text_sha256 = hashlib.sha256(changed.encode()).hexdigest()
    provenance = hashlib.sha256(
        json.dumps(
            {
                "operation": "arxiv_semantic_latex_body_v1",
                "parent_provenance_id": source.params["parent_provenance_id"],
                "excluded_paths": source.params["excluded_paths"],
                "text_sha256": text_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    overrides = {
        "text": changed,
        "text_sha256": text_sha256,
        "grounded_source": grounded,
        "provenance_id": f"derived-sha256:{provenance}",
    }

    assert source.params["canonical_source_envelope_sha256"]
    assert (
        answer_from_events(
            world,
            spec,
            set(spec.sufficient_event_ids),
            extra_overrides={source.id: overrides},
            enforce_preconditions=True,
        )
        == "unknown"
    )


def test_counterfactual_grounded_source_shifts_later_facts() -> None:
    text = "revision v2\nnew fact\nsubmitted 2026-08-27"
    digest = hashlib.sha256(text.encode()).hexdigest()
    payload = {
        "source_id": "source:revision",
        "visible_text": text,
        "text_sha256": digest,
        "facts": [
            {
                "fact_id": "fact:new",
                "source_id": "source:revision",
                "text_sha256": digest,
                "quote": "new fact",
                "char_start": text.index("new fact"),
                "char_end": text.index("new fact") + len("new fact"),
                "normalized_quote": "new fact",
            },
            {
                "fact_id": "fact:date",
                "source_id": "source:revision",
                "text_sha256": digest,
                "quote": "2026-08-27",
                "char_start": text.index("2026-08-27"),
                "char_end": text.index("2026-08-27") + len("2026-08-27"),
                "normalized_quote": "2026-08-27",
            },
        ],
        "relations": [],
    }
    changed_text = text.replace("new fact", "a materially longer new fact")

    changed = _counterfactual_grounded_source(
        payload,
        original_quote="new fact",
        changed_quote="a materially longer new fact",
        changed_text=changed_text,
    )

    [first, later] = changed["facts"]
    assert changed_text[first["char_start"] : first["char_end"]] == first["quote"]
    assert changed_text[later["char_start"] : later["char_end"]] == later["quote"]


def test_revision_overlap_is_not_treated_as_unattributed_copying() -> None:
    workflow, _ = _workflow()
    materialized = materialize(
        7,
        n_parallel=0,
        n_pulses=0,
        domain="researchlab",
        source_workflows=[workflow],
        include_program_joins=False,
    )
    spec = next(
        query
        for query in materialized.queries
        if query.query_type == "real_revision_added_text"
    )
    essential = [
        artifact
        for artifact in materialized.artifacts["focal"]
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    source_artifacts = [
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("source_record_id")
    ]

    assert sentence_near_dup_ratio(essential) == 0.0
    assert sentence_near_dup_ratio(source_artifacts) > 0.0
    tampered = [
        replace(artifact, text=artifact.text.replace("NSF 172251", "NSF 172252"))
        if "NSF 172251" in artifact.text
        else artifact
        for artifact in essential
    ]
    assert sentence_near_dup_ratio(tampered) > 0.0

    _, counterfactual_artifacts = render_cf_view(materialized.worlds["focal"], spec)
    counterfactual_essential = [
        artifact
        for artifact in counterfactual_artifacts
        if artifact.artifact_id in spec.essential_artifact_ids
    ]
    assert sentence_near_dup_ratio(counterfactual_essential) > 0.0
    cf_source = next(
        artifact
        for artifact in counterfactual_essential
        if (artifact.slots or {}).get("event_type") == "arxiv_revision"
        and "NSF 172259" in artifact.text
    )
    assert (
        artifact_classification(cf_source).source_origin is SourceOrigin.SYNTHETIC_WORLD
    )
    assert (cf_source.slots or {})["parent_source_origin"] == "real_derived"
    assert (cf_source.slots or {})["parent_source_envelope_sha256"]
