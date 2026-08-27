from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

from longworld.core.attestation import attach_attestation, attestation_key_from_env
from longworld.core.consist import artifact_text_issues
from longworld.core.engine import answer_from_artifacts, semantic_answer_from_artifacts
from longworld.core.graph import graph_stats
from longworld.core.pack import estimate_tokens
from longworld.core.render import (
    artifact_semantic_payload,
    render_world,
    semantic_attestation_valid,
    stamp_text_integrity,
)
from longworld.core.retrieve import raw_token_fact_windows_insufficient
from longworld.core.secvisible import (
    SEC_VISIBLE_TEXT_REVISION,
    sec_visible_provenance_id,
)
from longworld.core.sourceworkflow import SourceFact, SourceRecord, SourceWorkflow
from longworld.core.taxonomy import SourceOrigin, artifact_classification
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.company.queries import build_queries
from longworld.domains.company.schema import sample_world_spec
from longworld.domains.company.simulate import simulate_company

SOURCE_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "sec_p5_apple_smoke"
)
RECORD_ID = "sec:0000320193-25-000079"


def _identity_facts(text: str, digest: str) -> tuple[SourceFact, ...]:
    fields = (
        ("accession", "ACCESSION NUMBER:", "0000320193-25-000079"),
        ("form", "CONFORMED SUBMISSION TYPE:", "10-K"),
        ("filing_date", "FILED AS OF DATE:", "20251031"),
        ("report_date", "CONFORMED PERIOD OF REPORT:", "20250927"),
    )
    facts = []
    for field, prefix, value in fields:
        start = text.index(value, text.index(prefix))
        facts.append(
            SourceFact(
                fact_id=field,
                field=field,
                value=value,
                record_id=RECORD_ID,
                evidence_quote=value,
                char_start=start,
                char_end=start + len(value),
                value_offset=0,
                source_sha256=digest,
            )
        )
    return tuple(facts)


def _workflow(text: str) -> SourceWorkflow:
    digest = hashlib.sha256(text.encode()).hexdigest()
    record = SourceRecord(
        record_id=RECORD_ID,
        kind="sec_filing",
        occurred_at="2025-10-31",
        text=text,
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/0000320193-25-000079.txt"
        ),
        retrieval_url="",
        source_family="sec_edgar_submission",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=_identity_facts(text, digest),
        attributes=(
            ("accession", "0000320193-25-000079"),
            ("cik", "0000320193"),
            ("filing_date", "2025-10-31"),
            ("form", "10-K"),
            ("report_date", "2025-09-27"),
        ),
    )
    return SourceWorkflow(
        workflow_id="source:sec_filing:0123456789abcdef01234567",
        component_digest="0" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=(record.provenance_id,),
        records=(record,),
        relations=(),
    )


@pytest.fixture(scope="module")
def apple_world(monkeypatch_module: pytest.MonkeyPatch):
    manifest = json.loads(
        (SOURCE_DIRECTORY / "sec_filing_manifest.signed.json").read_text()
    )
    filing = manifest["filings"][0]
    text = (SOURCE_DIRECTORY / filing["source_file"]).read_text()
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-source-test-key-material-32-bytes"
    )
    spec = sample_world_spec(
        7,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[_workflow(text)],
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    queries = [
        query
        for query in build_queries(world)
        if query.query_type == "sec_financial_reconstruction"
    ]
    return world, artifacts, queries


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _resign_sec_source_params(world, essential, target, params):
    slots = {**target.slots, "params": params}
    artifacts = stamp_text_integrity(
        [
            replace(target, slots=slots)
            if artifact.artifact_id == target.artifact_id
            else artifact
            for artifact in essential
        ]
    )
    source_event_id = target.reveals_events[0]
    changed_world = replace(
        world,
        events=[
            replace(event, params=params) if event.id == source_event_id else event
            for event in world.events
        ],
    )
    return changed_world, artifacts


def test_financial_programs_replay_but_do_not_meet_long_bands(apple_world) -> None:
    world, artifacts, queries = apple_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
        ["128k"],
    ]
    assert [query.proof_depth for query in queries] == [2, 3, 4, 5]
    assert [len(query.essential_event_ids) for query in queries] == [4, 7, 13, 18]
    assert [graph_stats(world, query)["proof_depth"] for query in queries] == [
        2,
        3,
        4,
        5,
    ]
    assert all(
        graph_stats(world, query)["proof_depth"] == query.proof_depth
        for query in queries
    )
    copy_ids = {
        event.id
        for event in world.events
        if event.type == "sec_financial_answer"
        and event.params.get("compose") == "copy"
    }
    assert not copy_ids
    assert [len(query.essential_event_ids) for query in queries] == sorted(
        len(query.essential_event_ids) for query in queries
    )
    assert len({query.base_task_group for query in queries}) == 1
    answers = [query.answer for query in queries]
    assert answers[1].startswith(answers[0] + "||")
    assert answers[2].startswith(answers[1] + "||")
    assert answers[3].startswith(answers[2] + "||")
    assert answers[0].startswith("MIX:")
    assert "CAT:" in answers[1] and "GEO:" in answers[1]
    assert "GEO_PRIOR:" in answers[2] and "GEO_YOY:" in answers[2]
    assert "MAX_INDEX:0:+11308000000" in answers[2]
    assert "BS:" in answers[2] and "CERT:CEO:" in answers[2]
    assert "CERT_SCOPE:Form 10-K@2025-09-27#2025-10-31" in answers[2]
    assert answers[3].endswith(
        "||CF:111482000000+15195000000+-120686000000=5991000000"
        "||TAX:9683000000+1541000000+9495000000=20719000000"
        "||LEASE:1579000000+10911000000=12490000000"
        "||DEBT:12350000000+78328000000=90678000000"
    )
    mix = answers[0].removeprefix("MIX:")
    equation, gap, status = mix.split("|")
    left, total = equation.split("=")
    product, service = (int(part) for part in left.split("+"))
    assert product + service == int(total) == 416_161_000_000
    assert gap == "GAP:0"
    assert status == "STATUS:PASS"
    assert "307,003" not in answers[0]
    assert all("Workflow:" not in query.question for query in queries)
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    for query, expected_tokens in zip(
        queries, (12_000, 18_000, 40_000, 100_000), strict=True
    ):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "sec_source_section"
        )
        assert 0 < section_tokens < expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(
            answer_from_artifacts(
                world,
                query,
                [item for item in essential if item.artifact_id != removed.artifact_id],
            )
            == "unknown"
            for removed in essential
        )
        assert all(query.answer not in item.text for item in essential)
        assert any(
            artifact_classification(item).source_origin is SourceOrigin.REAL_DERIVED
            for item in essential
        )
    first_essential = [by_id[item] for item in queries[0].essential_artifact_ids]
    required_displays = ("307,003", "109,158", "416,161")
    assert all(
        sum(display in item.text for display in required_displays) < 3
        for item in first_essential
    )


def test_sec_financial_artifacts_use_readable_text_with_raw_fact_lineage(
    apple_world,
) -> None:
    _world, artifacts, _queries = apple_world
    manifest = json.loads(
        (SOURCE_DIRECTORY / "sec_filing_manifest.signed.json").read_text()
    )
    raw_source = (SOURCE_DIRECTORY / manifest["filings"][0]["source_file"]).read_text()
    sections = [
        artifact
        for artifact in artifacts
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    ]

    assert sections
    for artifact in sections:
        params = artifact.slots["params"]
        assert params["visible_text_revision"] == SEC_VISIBLE_TEXT_REVISION
        assert params["provenance_operation"] == SEC_VISIBLE_TEXT_REVISION
        assert params["provenance_id"].startswith("derived-sha256:")
        assert params["parent_provenance_id"] != params["provenance_id"]
        assert "<ix:" not in params["text"].lower()
        for fact in params["fact_spans"]:
            start = fact["source_char_start"]
            end = fact["source_char_end"]
            assert raw_source[start:end] == fact["source_evidence_quote"]
            for evidence in (fact.get("evidence_spans") or {}).values():
                field_start = evidence["source_char_start"]
                field_end = evidence["source_char_end"]
                assert (
                    raw_source[field_start:field_end]
                    == evidence["source_evidence_quote"]
                )


def test_resigned_raw_fact_coordinate_tamper_fails_semantic_replay(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    params = dict(target.slots["params"])
    spans = [dict(span) for span in params["fact_spans"]]
    spans[0]["source_char_start"] += 1
    params["fact_spans"] = spans
    slots = {**target.slots, "params": params}
    corrupted = stamp_text_integrity(
        [
            replace(target, slots=slots)
            if artifact.artifact_id == target.artifact_id
            else artifact
            for artifact in essential
        ]
    )
    source_event_id = target.reveals_events[0]
    corrupted_world = replace(
        world,
        events=[
            replace(event, params=params) if event.id == source_event_id else event
            for event in world.events
        ],
    )

    assert (
        semantic_answer_from_artifacts(corrupted_world, query, corrupted) == "unknown"
    )


def test_resigned_raw_fact_retarget_fails_semantic_replay(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    params = dict(target.slots["params"])
    spans = [dict(span) for span in params["fact_spans"]]
    raw_source = next(
        record.text
        for workflow in world.spec["project"]["source_workflows"]
        for record in workflow.records
        if record.record_id == params["record_id"]
    )
    replacement_start = params["parent_char_start"]
    replacement_end = replacement_start + 8
    spans[0]["source_char_start"] = replacement_start
    spans[0]["source_char_end"] = replacement_end
    spans[0]["source_evidence_quote"] = raw_source[replacement_start:replacement_end]
    params["fact_spans"] = spans
    corrupted_world, corrupted = _resign_sec_source_params(
        world, essential, target, params
    )

    assert (
        semantic_answer_from_artifacts(corrupted_world, query, corrupted) == "unknown"
    )


def test_resigned_parent_provenance_tamper_fails_semantic_replay(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    params = dict(target.slots["params"])
    params["parent_provenance_id"] = "derived-sha256:" + "f" * 64
    params["provenance_id"] = sec_visible_provenance_id(
        parent_provenance_id=params["parent_provenance_id"],
        raw_section_sha256=params["raw_section_sha256"],
        visible_text_sha256=params["section_sha256"],
        revision=params["visible_text_revision"],
    )
    corrupted_world, corrupted = _resign_sec_source_params(
        world, essential, target, params
    )

    assert (
        semantic_answer_from_artifacts(corrupted_world, query, corrupted) == "unknown"
    )


def test_resigned_xbrl_fact_identity_tamper_fails_semantic_replay(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    params = dict(target.slots["params"])
    spans = [dict(span) for span in params["fact_spans"]]
    spans[0]["fact_id"] = "tampered-canonical-fact-id"
    params["fact_spans"] = spans
    corrupted_world, corrupted = _resign_sec_source_params(
        world, essential, target, params
    )

    assert (
        semantic_answer_from_artifacts(corrupted_world, query, corrupted) == "unknown"
    )


def test_resigned_world_and_artifact_section_swap_fails_semantic_replay(
    apple_world,
) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    left = next(
        item
        for item in essential
        if item.slots["params"].get("financial_role") == "product_revenue"
    )
    right = next(
        item
        for item in essential
        if item.slots["params"].get("financial_role") == "service_revenue"
    )
    swapped_artifacts = stamp_text_integrity(
        [
            replace(left, text=right.text, slots=deepcopy(right.slots))
            if item.artifact_id == left.artifact_id
            else replace(right, text=left.text, slots=deepcopy(left.slots))
            if item.artifact_id == right.artifact_id
            else item
            for item in essential
        ]
    )
    left_event_id = left.reveals_events[0]
    right_event_id = right.reveals_events[0]
    left_params = deepcopy(left.slots["params"])
    right_params = deepcopy(right.slots["params"])
    swapped_world = replace(
        world,
        events=[
            replace(event, params=right_params)
            if event.id == left_event_id
            else replace(event, params=left_params)
            if event.id == right_event_id
            else event
            for event in world.events
        ],
    )

    assert (
        semantic_answer_from_artifacts(swapped_world, query, swapped_artifacts)
        == "unknown"
    )


def test_resigned_artifact_only_section_swap_fails_semantic_replay(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    left = next(
        item
        for item in essential
        if item.slots["params"].get("financial_role") == "product_revenue"
    )
    right = next(
        item
        for item in essential
        if item.slots["params"].get("financial_role") == "service_revenue"
    )
    swapped_artifacts = stamp_text_integrity(
        [
            replace(
                left,
                reveals_events=list(right.reveals_events),
                text=right.text,
                slots=deepcopy(right.slots),
            )
            if item.artifact_id == left.artifact_id
            else replace(
                right,
                reveals_events=list(left.reveals_events),
                text=left.text,
                slots=deepcopy(left.slots),
            )
            if item.artifact_id == right.artifact_id
            else item
            for item in essential
        ]
    )

    assert semantic_answer_from_artifacts(world, query, swapped_artifacts) == "unknown"


def test_resigned_sec_time_and_topology_tamper_fails_semantic_replay(
    apple_world,
) -> None:
    world, artifacts, queries = apple_world
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    fabricated_parent = "focal.fabricated_cross_project_dependency"
    changed_time = date(2000, 1, 1)
    assert not semantic_attestation_valid(replace(target, time=changed_time))
    corrupted_artifacts = stamp_text_integrity(
        [
            replace(target, time=changed_time)
            if artifact.artifact_id == target.artifact_id
            else artifact
            for artifact in essential
        ]
    )
    source_event_id = target.reveals_events[0]
    corrupted_world = replace(
        world,
        events=[
            replace(
                event,
                time=changed_time,
                causal_inputs=[fabricated_parent],
                required_inputs=[fabricated_parent],
                relation_kinds={fabricated_parent: "fabricated_dependency"},
            )
            if event.id == source_event_id
            else event
            for event in world.events
        ],
    )

    assert semantic_attestation_valid(
        next(
            item
            for item in corrupted_artifacts
            if item.artifact_id == target.artifact_id
        )
    )
    assert (
        semantic_answer_from_artifacts(corrupted_world, query, corrupted_artifacts)
        == "unknown"
    )


def test_legacy_artifact_semantic_attestation_remains_verifiable(apple_world) -> None:
    _world, artifacts, _queries = apple_world
    target = next(
        artifact
        for artifact in artifacts
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    slots = deepcopy(target.slots)
    slots.pop("semantic_attestation_revision")
    slots.pop("semantic_attestation")
    legacy = replace(target, slots=slots)
    key = attestation_key_from_env("source_manifest")
    assert key is not None
    signed = attach_attestation(
        artifact_semantic_payload(legacy), key, purpose="artifact_semantics"
    )
    legacy.slots["semantic_attestation"] = signed["attestation"]

    assert not semantic_attestation_valid(legacy)
    assert semantic_attestation_valid(legacy, allow_legacy=True)


@pytest.mark.parametrize("revision", [{"revision": "bad"}, ["bad"]])
def test_artifact_semantic_attestation_unknown_revision_fails_closed(
    apple_world, revision
) -> None:
    _world, artifacts, _queries = apple_world
    target = next(
        artifact
        for artifact in artifacts
        if (artifact.slots or {}).get("event_type") == "sec_source_section"
    )
    malformed = replace(
        target,
        slots={**target.slots, "semantic_attestation_revision": revision},
    )

    assert not semantic_attestation_valid(malformed)


def test_financial_cf_remove_one_and_surface_gates(apple_world) -> None:
    world, artifacts, queries = apple_world
    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert query.cf_answer.startswith("MIX:207003000000+")
        assert "|GAP:-100000000000|STATUS:FAIL" in query.cf_answer
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_surface_gold_free
        assert verification.essential_text_grounded
        assert all(
            check["replay_answer"] != query.answer
            for check in notes["essential_single_docs"]
        )
    query = queries[0]
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    _, cf_artifacts = render_cf_view(world, query)
    corrupted = replace(
        essential[0], text=essential[0].text.replace("307,003", "000,000", 1)
    )
    assert corrupted.text != essential[0].text
    remaining = [
        corrupted if item.artifact_id == corrupted.artifact_id else item
        for item in artifacts
    ]
    verification, _ = verify_question(
        world,
        query,
        remaining,
        cf_artifacts=cf_artifacts,
        verification_mode="candidate",
    )
    assert verification.essential_text_grounded is False

    factual_source = essential[0]
    cf_source = next(
        item for item in cf_artifacts if item.artifact_id == factual_source.artifact_id
    )
    factual_class = artifact_classification(factual_source)
    cf_class = artifact_classification(cf_source)
    assert factual_class.source_origin is SourceOrigin.REAL_DERIVED
    assert cf_class.source_origin is SourceOrigin.SYNTHETIC_WORLD
    assert cf_class.provenance_id != factual_class.provenance_id
    assert (cf_source.slots or {})["parent_provenance_id"] == (
        factual_class.provenance_id
    )


@pytest.mark.parametrize(
    ("tier", "artifact_fragment"),
    [
        ("16k", "item8_operations_program_corridor"),
        ("32k", "note2_revenue_current_mix"),
        ("32k", "note13_current_geography"),
        ("64k", "item8_balance_sheet_core"),
        ("64k", "ex_31_1"),
    ],
)
def test_resigned_semantic_corruption_cannot_replay_hidden_sec_events(
    apple_world, tier: str, artifact_fragment: str
) -> None:
    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == [tier])
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact for artifact in essential if artifact_fragment in artifact.artifact_id
    )
    values = " ".join(str(value) for value in target.slots["ground_values"])
    corrupt_text = (
        "SEC source section unrelated narrative with preserved surface anchors. "
        f"{values} sec source section financial reconstruction evidence only."
    )
    corrupted_target = replace(target, text=corrupt_text, slots=dict(target.slots))
    corrupted = stamp_text_integrity(
        [
            corrupted_target if item.artifact_id == target.artifact_id else item
            for item in essential
        ]
    )

    assert semantic_attestation_valid(corrupted_target)
    assert not artifact_text_issues(world, corrupted, require_attestation=True)
    assert answer_from_artifacts(world, query, corrupted) == query.answer
    assert semantic_answer_from_artifacts(world, query, corrupted) == "unknown"


@pytest.mark.parametrize(
    ("tier", "artifact_fragment", "old_label", "new_label"),
    [
        ("16k", "item8_operations_program_corridor", "Products", "Services"),
        ("32k", "note13_current_geography", "Americas", "Services"),
    ],
)
def test_resigned_sec_role_corruption_cannot_reuse_original_lineage(
    apple_world,
    tier: str,
    artifact_fragment: str,
    old_label: str,
    new_label: str,
) -> None:
    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == [tier])
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact for artifact in essential if artifact_fragment in artifact.artifact_id
    )
    params = dict(target.slots["params"])
    declared_text = str(params["text"])
    assert old_label in declared_text
    assert len(old_label) == len(new_label)
    mutated_text = declared_text.replace(old_label, new_label, 1)
    params["text"] = mutated_text
    params["text_sha256"] = hashlib.sha256(mutated_text.encode()).hexdigest()
    mutated_artifact_text = target.text.replace(declared_text, mutated_text, 1)
    corrupted_target = replace(
        target,
        text=mutated_artifact_text,
        slots={**target.slots, "event_type": "email", "params": params},
    )
    corrupted = stamp_text_integrity(
        [
            corrupted_target if item.artifact_id == target.artifact_id else item
            for item in essential
        ]
    )

    assert semantic_attestation_valid(corrupted_target)
    assert not artifact_text_issues(world, corrupted, require_attestation=True)
    assert answer_from_artifacts(world, query, corrupted) == query.answer
    assert semantic_answer_from_artifacts(world, query, corrupted) == "unknown"


def test_malformed_sec_semantic_slots_fail_closed(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if "item8_operations_program_corridor" in artifact.artifact_id
    )
    corrupted_target = replace(
        target,
        slots={**target.slots, "params": "malformed"},
    )
    corrupted = [
        corrupted_target if item.artifact_id == target.artifact_id else item
        for item in essential
    ]

    assert answer_from_artifacts(world, query, corrupted) == query.answer
    assert semantic_answer_from_artifacts(world, query, corrupted) == "unknown"


def test_sec_source_artifact_with_extra_revealed_event_fails_closed(
    apple_world,
) -> None:
    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    target = next(
        artifact
        for artifact in essential
        if "item8_operations_program_corridor" in artifact.artifact_id
    )
    extra_event_id = next(
        event_id
        for event_id in query.essential_event_ids
        if event_id not in target.reveals_events
    )
    corrupted_target = replace(
        target,
        reveals_events=[*target.reveals_events, extra_event_id],
    )
    corrupted = stamp_text_integrity(
        [
            corrupted_target if item.artifact_id == target.artifact_id else item
            for item in essential
        ]
    )

    assert semantic_attestation_valid(corrupted_target)
    assert answer_from_artifacts(world, query, corrupted) == query.answer
    assert semantic_answer_from_artifacts(world, query, corrupted) == "unknown"


def test_certification_metadata_requires_complete_body_evidence(apple_world) -> None:
    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["64k"])
    essential = [
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
    ]
    overrides: dict[str, dict] = {}
    corrupted = []
    for artifact in essential:
        params = json.loads(json.dumps((artifact.slots or {}).get("params") or {}))
        certification_spans = [
            span
            for span in params.get("fact_spans") or []
            if span.get("kind") == "certification"
        ]
        if not certification_spans:
            corrupted.append(artifact)
            continue
        for span in certification_spans:
            span["evidence_spans"] = {}
            span["certification_date"] = "2099-01-01"
            if span.get("certification_kind") == "section_906":
                span["covered_period_end"] = "2098-12-31"
        event_id = artifact.reveals_events[0]
        overrides[event_id] = params
        corrupted.append(replace(artifact, slots={**artifact.slots, "params": params}))
    corrupted = stamp_text_integrity(corrupted)

    assert (
        semantic_answer_from_artifacts(
            world,
            query,
            corrupted,
            extra_overrides=overrides,
            enforce_preconditions=True,
        )
        == "unknown"
    )


def test_raw_token_window_detects_local_sec_fact_shortcut(apple_world) -> None:
    class FourCharsPerToken:
        name_or_path = "test/four-chars"
        init_kwargs: ClassVar = {"_commit_hash": "0" * 40}

        @staticmethod
        def __call__(
            text: str,
            *,
            add_special_tokens: bool,
            return_offsets_mapping: bool,
        ) -> dict[str, list]:
            assert not add_special_tokens
            assert return_offsets_mapping
            offsets = [
                (start, min(len(text), start + 4)) for start in range(0, len(text), 4)
            ]
            return {"input_ids": list(range(len(offsets))), "offset_mapping": offsets}

    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])
    insufficient, notes = raw_token_fact_windows_insufficient(
        world,
        query,
        artifacts,
        FourCharsPerToken(),
        window_sizes=(4000,),
    )

    assert not insufficient
    assert notes["proof_mode"] == "pinned_tokenizer_source_span_replay"
    assert notes["tokenizer_evidence_span_tokens"] > 0
    assert "evidence_span_tokens" not in notes
    assert notes["windows"]["4000"]["answer"] == query.answer


def test_raw_token_window_rejects_duplicate_source_event_artifacts(apple_world) -> None:
    class FourCharsPerToken:
        name_or_path = "test/four-chars"
        init_kwargs: ClassVar = {"_commit_hash": "0" * 40}

        @staticmethod
        def __call__(
            text: str,
            *,
            add_special_tokens: bool,
            return_offsets_mapping: bool,
        ) -> dict[str, list]:
            assert not add_special_tokens
            assert return_offsets_mapping
            offsets = [
                (start, min(len(text), start + 4)) for start in range(0, len(text), 4)
            ]
            return {"input_ids": list(range(len(offsets))), "offset_mapping": offsets}

    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])
    target = next(
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
        and "item8_operations_program_corridor" in artifact.artifact_id
    )
    duplicate = replace(target, artifact_id=f"{target.artifact_id}.duplicate")

    insufficient, notes = raw_token_fact_windows_insufficient(
        world,
        query,
        [*artifacts, duplicate],
        FourCharsPerToken(),
        window_sizes=(4000,),
    )

    assert not insufficient
    assert notes["error"] == "duplicate_source_event_artifact"


def test_raw_token_window_rejects_non_monotonic_offsets(apple_world) -> None:
    class NonMonotonicTokenizer:
        name_or_path = "test/non-monotonic"
        init_kwargs: ClassVar = {"_commit_hash": "0" * 40}

        @staticmethod
        def __call__(
            text: str,
            *,
            add_special_tokens: bool,
            return_offsets_mapping: bool,
        ) -> dict[str, list]:
            assert text
            assert not add_special_tokens
            assert return_offsets_mapping
            return {"input_ids": [0, 1], "offset_mapping": [(4, 8), (0, 4)]}

    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])

    insufficient, notes = raw_token_fact_windows_insufficient(
        world,
        query,
        artifacts,
        NonMonotonicTokenizer(),
        window_sizes=(4000,),
    )

    assert not insufficient
    assert notes["error"] == "invalid_token_offsets"


def test_raw_token_window_rejects_malformed_source_artifact(apple_world) -> None:
    class FourCharsPerToken:
        name_or_path = "test/four-chars"
        init_kwargs: ClassVar = {"_commit_hash": "0" * 40}

        @staticmethod
        def __call__(
            text: str,
            *,
            add_special_tokens: bool,
            return_offsets_mapping: bool,
        ) -> dict[str, list]:
            assert not add_special_tokens
            assert return_offsets_mapping
            offsets = [
                (start, min(len(text), start + 4)) for start in range(0, len(text), 4)
            ]
            return {"input_ids": list(range(len(offsets))), "offset_mapping": offsets}

    world, artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])
    target = next(
        artifact
        for artifact in artifacts
        if artifact.artifact_id in query.essential_artifact_ids
        and "item8_operations_program_corridor" in artifact.artifact_id
    )
    malformed = replace(
        target,
        slots={**target.slots, "params": {**target.slots["params"], "record_id": "x"}},
    )

    insufficient, notes = raw_token_fact_windows_insufficient(
        world,
        query,
        [malformed if artifact is target else artifact for artifact in artifacts],
        FourCharsPerToken(),
        window_sizes=(4000,),
    )

    assert not insufficient
    assert notes["error"] == "invalid_source_event_artifact"


def test_resigned_cf_source_misclassification_cannot_replay_hidden_event(
    apple_world,
) -> None:
    world, _artifacts, queries = apple_world
    query = next(item for item in queries if item.preferred_length_buckets == ["16k"])
    _cf_world, cf_artifacts = render_cf_view(world, query)
    target = next(
        artifact
        for artifact in cf_artifacts
        if artifact.artifact_id in query.essential_artifact_ids
        and "item8_operations_program_corridor" in artifact.artifact_id
    )
    params = dict(target.slots["params"])
    declared_text = str(params["text"])
    mutated_text = declared_text.replace("Products", "Services", 1)
    params["text"] = mutated_text
    params["text_sha256"] = hashlib.sha256(mutated_text.encode()).hexdigest()
    corrupted_target = replace(
        target,
        text=target.text.replace(declared_text, mutated_text, 1),
        slots={**target.slots, "event_type": "email", "params": params},
    )
    corrupted = stamp_text_integrity(
        [
            corrupted_target if item.artifact_id == target.artifact_id else item
            for item in cf_artifacts
        ]
    )
    overrides = {query.cf_event_id: query.cf_param_updates}

    assert semantic_attestation_valid(corrupted_target)
    assert (
        answer_from_artifacts(
            world,
            query,
            corrupted,
            extra_overrides=overrides,
            enforce_preconditions=True,
        )
        == query.cf_answer
    )
    assert (
        semantic_answer_from_artifacts(
            world,
            query,
            corrupted,
            extra_overrides=overrides,
            enforce_preconditions=True,
        )
        == "unknown"
    )


AMAZON_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "sec_p7_amazon_v1"
)
AMAZON_RECORD_ID = "sec:0001018724-25-000004"
AMAZON_SOURCE = AMAZON_DIRECTORY / "0001018724-25-000004.txt"


def _amazon_identity_facts(text: str, digest: str) -> tuple[SourceFact, ...]:
    fields = (
        ("accession", "ACCESSION NUMBER:", "0001018724-25-000004"),
        ("form", "CONFORMED SUBMISSION TYPE:", "10-K"),
        ("filing_date", "FILED AS OF DATE:", "20250207"),
        ("report_date", "CONFORMED PERIOD OF REPORT:", "20241231"),
    )
    facts = []
    for field, prefix, value in fields:
        start = text.index(value, text.index(prefix))
        facts.append(
            SourceFact(
                fact_id=field,
                field=field,
                value=value,
                record_id=AMAZON_RECORD_ID,
                evidence_quote=value,
                char_start=start,
                char_end=start + len(value),
                value_offset=0,
                source_sha256=digest,
            )
        )
    return tuple(facts)


def _amazon_workflow(text: str) -> SourceWorkflow:
    digest = hashlib.sha256(text.encode()).hexdigest()
    record = SourceRecord(
        record_id=AMAZON_RECORD_ID,
        kind="sec_filing",
        occurred_at="2025-02-07",
        text=text,
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000101872425000004/0001018724-25-000004.txt"
        ),
        retrieval_url="",
        source_family="sec_edgar_submission",
        source_origin=SourceOrigin.REAL_PUBLIC,
        provenance_id=f"sha256:{digest}",
        source_sha256=digest,
        text_sha256=digest,
        facts=_amazon_identity_facts(text, digest),
        attributes=(
            ("accession", "0001018724-25-000004"),
            ("cik", "0001018724"),
            ("filing_date", "2025-02-07"),
            ("form", "10-K"),
            ("report_date", "2024-12-31"),
        ),
    )
    return SourceWorkflow(
        workflow_id="source:sec_filing:amazon0001018724abcdef0123",
        component_digest="0" * 64,
        source_kind="sec_filing",
        target_domain="company",
        source_origin=SourceOrigin.REAL_PUBLIC,
        source_families=("sec_edgar_submission",),
        provenance_ids=(record.provenance_id,),
        records=(record,),
        relations=(),
    )


@pytest.fixture(scope="module")
def amazon_world(monkeypatch_module: pytest.MonkeyPatch):
    if not AMAZON_SOURCE.is_file():
        pytest.skip("Amazon 10-K inventory is not on this host")
    text = AMAZON_SOURCE.read_text()
    monkeypatch_module.setenv(
        "LONGWORLD_ATTESTATION_KEY", "company-sec-source-test-key-material-32-bytes"
    )
    spec = sample_world_spec(
        7,
        n_parallel=0,
        n_pulses=0,
        n_workstreams=0,
        source_workflows=[_amazon_workflow(text)],
    )
    world = simulate_company(spec)["focal"]
    artifacts = render_world(world)
    queries = [
        query
        for query in build_queries(world)
        if query.query_type == "sec_financial_reconstruction"
    ]
    return world, artifacts, queries


def test_amazon_programs_replay_but_do_not_meet_long_bands(amazon_world) -> None:
    world, artifacts, queries = amazon_world
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
    ]
    assert [len(query.essential_event_ids) for query in queries] == [4, 7]
    assert [graph_stats(world, query)["proof_depth"] for query in queries] == [
        2,
        3,
    ]
    answers = [query.answer for query in queries]
    assert answers[0] == (
        "MIX:272311000000+365648000000=637959000000|GAP:0|STATUS:PASS"
    )
    assert answers[1].startswith(answers[0] + "||CAT:")
    assert "GEO:" in answers[1]
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    for query, expected_tokens in zip(queries, (12_000, 20_000), strict=True):
        essential = [by_id[item] for item in query.essential_artifact_ids]
        section_tokens = sum(
            estimate_tokens(item.text)
            for item in essential
            if (item.slots or {}).get("event_type") == "sec_source_section"
        )
        assert 0 < section_tokens < expected_tokens
        assert answer_from_artifacts(world, query, essential) == query.answer
        assert all(query.answer not in item.text for item in essential)

    current_geo = next(
        event
        for event in world.events
        if event.type == "sec_source_section"
        and event.params.get("section_alias") == "note13_segments"
    )
    roles = {str(span.get("role") or "") for span in current_geo.params["fact_spans"]}
    assert any(role.startswith("geo_") for role in roles)
    assert not any(role.startswith("prior_geo_") for role in roles)
    assert all(
        event.params.get("control_tier") not in {"64k", "128k"}
        for event in world.events
        if event.type == "sec_financial_answer"
    )


def test_amazon_financial_cf_mutates_product_display(amazon_world) -> None:
    _world, _artifacts, queries = amazon_world
    assert queries[0].cf_answer.startswith("MIX:172311000000+")
    assert "|GAP:-100000000000|STATUS:FAIL" in queries[0].cf_answer
    assert queries[0].cf_answer != queries[0].answer
