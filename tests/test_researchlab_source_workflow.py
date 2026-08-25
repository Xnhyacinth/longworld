from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from longworld.core.engine import answer_from_artifacts
from longworld.core.pack import (
    compute_view_metrics,
    dependency_evidence_ids,
    join_artifacts,
    wrap_prompt,
)
from longworld.core.promotion import _replayed_quality_metrics
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


def _record(
    *,
    record_id: str,
    revision: str,
    occurred_at: str,
    body: str,
    added_fact: str = "",
) -> SourceRecord:
    text = (
        json.dumps(
            {
                "entry_id": f"https://arxiv.org/abs/2203.01928{revision}",
                "kind": "arxiv_api_entry",
                "latex_sources": [{"path": "parts/acknowledgements.tex", "text": body}],
                "revision": revision,
                **({"previous_revision_id": "v1"} if revision == "v2" else {}),
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
        source_url=f"https://arxiv.org/abs/2203.01928{revision}",
        retrieval_url=(
            "https://export.arxiv.org/api/query?id_list=2203.01928" + revision
        ),
        source_family="arxiv_record",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=facts,
        attributes=(
            ("revision_id", revision),
            ("work_id", "arxiv:2203.01928"),
        ),
    )


def _workflow() -> tuple[SourceWorkflow, str]:
    shared_revision_text = (
        "This manuscript reports the same core experimental protocol across "
        "both publicly archived revisions."
    )
    disclosure = (
        "Jonathan Crabbé is funded by Aviva and Mihaela van der Schaar by the "
        "Office of Naval Research (ONR), NSF 172251."
    )
    v1 = _record(
        record_id="arxiv:2203.01928v1",
        revision="v1",
        occurred_at="2022-03-03T18:59:03Z",
        body=(
            f"Revision one introduction is specific to this archive. "
            f"{shared_revision_text} The authors thank the anonymous reviewers."
        ),
    )
    v2 = _record(
        record_id="arxiv:2203.01928v2",
        revision="v2",
        occurred_at="2022-06-07T11:25:15Z",
        body=(
            f"Revision two introduction is specific to this archive. "
            f"{shared_revision_text} {disclosure}"
        ),
        added_fact=disclosure,
    )
    relation_quote = '"previous_revision_id": "v1"'
    relation_start = v2.text.index(relation_quote)
    relation = SourceRelation(
        relation_id="revision-v2-v1",
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
        workflow_id="source:paper_workflow:0123456789abcdef01234567",
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

    verification, _ = verify_question(
        world,
        spec,
        corrupted,
        verification_mode="candidate",
    )

    assert not verification.essential_text_grounded
    assert not verification.semantic_sufficient


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
    assert sentence_near_dup_ratio(counterfactual_essential) == 0.0
