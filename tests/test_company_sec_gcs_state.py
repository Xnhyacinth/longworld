from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    LOCAL_PROBE_COMBINED_ROLES_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.sampler import materialize
from longworld.core.secxbrl import parse_sec_financial_program
from longworld.core.sourcebundle import load_source_workflow_bundle
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view
from longworld.domains.company.queries import sec_financial_answer_conforms
from scripts.generate import validate_declared_source_workflow_query_buckets

SOURCE_BUNDLE = (
    Path(__file__).resolve().parents[1]
    / "data/source_inventory/p12_sec_microsoft_gcs_v1/"
    "sec_source_workflow_bundle_p12.v1.signed.json"
)
SOURCE_DIRECTORY = SOURCE_BUNDLE.parent
TEST_SOURCE_KEY = b"microsoft-gcs-state-test-source-key-32-bytes"
FY2024_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "data/source_inventory/p12_wave1_company_microsoft_gcs_history_v1/"
    "msft-20240630.html"
)


def _microsoft_materialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], TEST_SOURCE_KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "test-msft-gcs-state-source-v1")
    monkeypatch.delenv(LOCAL_PROBE_COMBINED_ROLES_ENV, raising=False)

    bundle = json.loads(SOURCE_BUNDLE.read_text(encoding="utf-8"))
    [entry] = bundle["entries"]
    manifest_path = SOURCE_DIRECTORY / entry["path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("attestation")
    signed_manifest = attach_attestation(
        manifest, TEST_SOURCE_KEY, purpose="source_manifest"
    )
    local_manifest = tmp_path / manifest_path.name
    local_manifest.write_text(json.dumps(signed_manifest), encoding="utf-8")
    shutil.copyfile(
        SOURCE_DIRECTORY / manifest["filings"][0]["source_file"],
        tmp_path / manifest["filings"][0]["source_file"],
    )

    bundle.pop("attestation")
    bundle["entries"][0]["sha256"] = hashlib.sha256(
        local_manifest.read_bytes()
    ).hexdigest()
    signed_bundle = attach_attestation(
        bundle, TEST_SOURCE_KEY, purpose="source_workflow_bundle"
    )
    local_bundle = tmp_path / SOURCE_BUNDLE.name
    local_bundle.write_text(json.dumps(signed_bundle), encoding="utf-8")

    loaded = load_source_workflow_bundle(local_bundle, attestation_key=TEST_SOURCE_KEY)
    return materialize(
        1231,
        n_parallel=0,
        n_pulses=0,
        domain="company",
        source_workflows=list(loaded.workflows),
        include_program_joins=False,
    )


def test_declared_source_workflow_buckets_fail_closed_when_query_is_missing() -> None:
    queries = [
        SimpleNamespace(
            query_type="sec_financial_reconstruction",
            preferred_length_buckets=[bucket],
        )
        for bucket in ("16k", "32k")
    ]

    with pytest.raises(
        ValueError,
        match=r"sec_financial_reconstruction.*64k",
    ):
        validate_declared_source_workflow_query_buckets(
            queries,
            source_query_types=["sec_financial_reconstruction"],
            routing={
                "sec_financial_reconstruction": ["16k", "32k", "64k"],
            },
        )


@pytest.mark.skipif(
    not FY2024_SOURCE.is_file(), reason="requires the local Microsoft FY2024 source"
)
def test_microsoft_fy2024_gcs_financial_program_parses_exact_source() -> None:
    source = FY2024_SOURCE.read_text(encoding="utf-8")
    source_sha256 = hashlib.sha256(source.encode()).hexdigest()

    program = parse_sec_financial_program(
        source, source_sha256, report_date="2024-06-30"
    )

    assert len(program.sections) == 12
    assert len(program.roles) == 27
    assert program.n_category == 10
    assert program.n_geo == 2
    assert set(program.certifications) == {
        "ex_31_1",
        "ex_31_2",
        "ex_32_1",
        "ex_32_2",
    }


@pytest.mark.skipif(
    not FY2024_SOURCE.is_file(), reason="requires the local Microsoft FY2024 source"
)
def test_microsoft_fy2024_parser_still_fails_closed_on_real_tag_corruption() -> None:
    source = FY2024_SOURCE.read_text(encoding="utf-8")
    unmatched = source.replace("</ix:nonFraction>", "", 1)
    assert unmatched != source

    with pytest.raises(ProvenanceError, match="unmatched open tag"):
        parse_sec_financial_program(
            unmatched,
            hashlib.sha256(unmatched.encode()).hexdigest(),
            report_date="2024-06-30",
        )

    non_nil = source.replace('xsi:nil="true"/>', 'xsi:nil="false"/>', 1)
    assert non_nil != source
    with pytest.raises(ProvenanceError, match="not explicitly nil"):
        parse_sec_financial_program(
            non_nil,
            hashlib.sha256(non_nil.encode()).hexdigest(),
            report_date="2024-06-30",
        )


def test_declared_source_workflow_buckets_accept_aggregate_query_subsets() -> None:
    queries = [
        SimpleNamespace(query_type="revision", preferred_length_buckets=["16k"]),
        SimpleNamespace(query_type="revision", preferred_length_buckets=["32k"]),
    ]

    validate_declared_source_workflow_query_buckets(
        queries,
        source_query_types=["revision"],
        routing={"revision": ["16k", "32k"]},
        active_buckets=["16k", "32k", "64k"],
    )
    validate_declared_source_workflow_query_buckets(
        queries,
        source_query_types=["revision"],
        routing={},
    )


@pytest.mark.parametrize("active_buckets", [["16k"], []])
def test_declared_source_workflow_buckets_reject_inactive_trainable_bands(
    active_buckets: list[str],
) -> None:
    queries = [
        SimpleNamespace(
            query_type="sec_financial_reconstruction",
            preferred_length_buckets=[bucket],
        )
        for bucket in ("16k", "64k")
    ]

    with pytest.raises(
        ValueError,
        match=r"sec_financial_reconstruction.*inactive.*64k",
    ):
        validate_declared_source_workflow_query_buckets(
            queries,
            source_query_types=["sec_financial_reconstruction"],
            routing={"sec_financial_reconstruction": ["16k", "64k"]},
            active_buckets=active_buckets,
        )


@pytest.mark.skipif(
    not SOURCE_BUNDLE.is_file(),
    reason="requires the local Microsoft issuer source inventory",
)
def test_microsoft_real_source_reaches_state_answer_and_all_declared_buckets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialized = _microsoft_materialization(tmp_path, monkeypatch)
    world = materialized.worlds["focal"]
    artifacts = materialized.artifacts["focal"]
    queries = [
        query
        for query in materialized.queries
        if query.query_type == "sec_financial_reconstruction"
    ]

    assert materialized.scan_ok, materialized.scan_issues
    assert [query.preferred_length_buckets for query in queries] == [
        ["16k"],
        ["32k"],
        ["64k"],
    ]
    assert all(
        query.answer != "unknown"
        and sec_financial_answer_conforms(query.question, query.answer)
        for query in queries
    )

    filing_day = date(2025, 7, 30)
    source_sections = [
        event for event in world.events if event.type == "sec_source_section"
    ]
    assert source_sections
    assert {event.time for event in source_sections} == {filing_day}
    computation_events = [
        event for event in world.events if event.type == "sec_financial_answer"
    ]
    assert computation_events
    assert all(event.time > filing_day for event in computation_events)
    assert {
        event.params.get("relation_provenance") for event in computation_events
    } == {"synthetic_executable"}

    geography = {
        str(event.params.get("section_alias")): event
        for event in source_sections
        if event.params.get("section_alias")
        in {"note13_segments", "note13_prior_segments"}
    }
    assert set(geography) == {"note13_segments", "note13_prior_segments"}
    assert {
        span["role"] for span in geography["note13_segments"].params["fact_spans"]
    } == {"geo_0", "geo_1"}
    assert {
        span["role"] for span in geography["note13_prior_segments"].params["fact_spans"]
    } == {"prior_geo_0", "prior_geo_1"}
    current_ranges = {
        (item["char_start"], item["char_end"])
        for item in geography["note13_segments"].params["source_ranges"]
    }
    prior_ranges = {
        (item["char_start"], item["char_end"])
        for item in geography["note13_prior_segments"].params["source_ranges"]
    }
    assert current_ranges
    assert prior_ranges
    assert all(
        current_end <= prior_start or prior_end <= current_start
        for current_start, current_end in current_ranges
        for prior_start, prior_end in prior_ranges
    )

    for query in queries:
        _, cf_artifacts = render_cf_view(world, query)
        verification, _notes = verify_question(
            world,
            query,
            artifacts,
            cf_artifacts=cf_artifacts,
            verification_mode="candidate",
        )
        assert query.cf_answer != query.answer
        assert verification.counterfactual_replay_sufficient
        assert verification.remove_one_fails
        assert verification.essential_single_doc_insufficient
        assert verification.essential_text_grounded
