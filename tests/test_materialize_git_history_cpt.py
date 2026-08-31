from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from longworld.core.attestation import ATTESTATION_ENV, verify_attestation
from longworld.core.githistory import GitHistoryExtraction
from longworld.core.realworkflow import WorkflowRecord

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from materialize_git_history_cpt import (
    _configured_minimum_source_elapsed_seconds,
    _configured_truncation_ratio,
    _deduplicate_extraction,
    _requests,
    _source_manifest,
    _source_slices,
    validate_remote_identity,
)

from longworld.core.cptwindow import CPTBand


def test_source_text_dedup_breaks_the_chain_instead_of_bridging_it() -> None:
    records = (
        WorkflowRecord("r0", "commit", "2026-01-01T00:00:00Z", "unique", ()),
        WorkflowRecord("r1", "commit", "2026-01-02T00:00:00Z", "duplicate", ("r0",)),
        WorkflowRecord("r2", "commit", "2026-01-03T00:00:00Z", "recovery", ("r1",)),
        WorkflowRecord("r3", "commit", "2026-01-04T00:00:00Z", "unique", ("r2",)),
    )
    extraction = GitHistoryExtraction(records, 4, "a" * 40, {})
    seen = {hashlib.sha256(b"duplicate").hexdigest()}

    filtered = _deduplicate_extraction(extraction, seen)

    assert [record.record_id for record in filtered.records] == ["r0", "r2"]
    assert filtered.records[0].links == ()
    assert filtered.records[1].links == ()
    assert filtered.reject_reasons == {"duplicate_source_record_text": 2}
    assert len(seen) == 3


def test_source_slices_expand_repositories_in_round_robin_order() -> None:
    sources = [
        {"repository": "example/a", "checkout": "a"},
        {"repository": "example/b", "checkout": "b"},
    ]

    expanded = _source_slices(
        sources,
        slice_commits=1000,
        commit_count=lambda source: (
            2500 if source["repository"] == "example/a" else 1500
        ),
    )

    assert [
        (source["repository"], source["skip_commits"], source["max_commits"])
        for source in expanded
    ] == [
        ("example/a", 0, 1000),
        ("example/b", 0, 1000),
        ("example/a", 1000, 1000),
        ("example/b", 1000, 500),
        ("example/a", 2000, 500),
    ]


def test_window_requests_support_configured_multiband_curriculum() -> None:
    bands = {
        "16k": CPTBand("16k", 16_000, 16_384),
        "32k": CPTBand("32k", 32_000, 32_768),
        "256k": CPTBand("256k", 256_000, 262_144),
    }

    requests = _requests(
        bands,
        {"16k": 2, "32k": 1, "256k": 1},
        multiplier=1,
        minimum_source_events={"16k": 4, "32k": 8, "256k": 32},
    )

    assert [request.band.name for request in requests] == [
        "256k",
        "32k",
        "16k",
        "16k",
    ]
    assert [request.min_source_events for request in requests] == [32, 8, 4, 4]


def test_quality_gate_config_requires_real_longitudinal_metadata() -> None:
    bands = {
        "16k": CPTBand("16k", 16_000, 16_384),
        "32k": CPTBand("32k", 32_000, 32_768),
    }

    assert _configured_minimum_source_elapsed_seconds(
        {"16k": 86_400, "32k": 172_800}, bands, longitudinal=True
    ) == {"16k": 86_400, "32k": 172_800}
    with pytest.raises(ValueError, match="requires longitudinal"):
        _configured_minimum_source_elapsed_seconds(
            {"16k": 86_400, "32k": 172_800}, bands, longitudinal=False
        )
    with pytest.raises(ValueError, match="truncation ratio"):
        _configured_truncation_ratio(True)
    assert _configured_truncation_ratio(125_000) == 125_000


def test_git_history_remote_identity_requires_public_head_and_license() -> None:
    head = "a" * 40
    responses = {
        "repos/example/repo": {
            "full_name": "example/repo",
            "private": False,
            "visibility": "public",
            "html_url": "https://github.com/example/repo",
        },
        f"repos/example/repo/commits/{head}": {
            "sha": head,
            "html_url": f"https://github.com/example/repo/commit/{head}",
        },
        f"repos/example/repo/license?ref={head}": {"license": {"spdx_id": "MIT"}},
    }

    receipt = validate_remote_identity(
        "example/repo", head, "MIT", responses.__getitem__
    )

    assert receipt["head_revision"] == head
    assert receipt["license"] == "MIT"
    assert len(receipt["commit_response_sha256"]) == 64

    responses["repos/example/repo"]["private"] = True
    with pytest.raises(ValueError, match="expected public"):
        validate_remote_identity("example/repo", head, "MIT", responses.__getitem__)


def test_git_history_source_manifest_normalizes_yaml_timestamp(
    monkeypatch,
) -> None:
    key = b"git-history-source-manifest-test-key"
    monkeypatch.setenv(ATTESTATION_ENV, key.decode())
    extraction = SimpleNamespace(
        head_revision="a" * 40,
        commit_count=1,
        reject_reasons={},
        truncated_commits=(),
        records=(
            WorkflowRecord(
                record_id="r1",
                kind="commit",
                occurred_at="2026-01-01T00:00:00Z",
                text="real patch",
                links=(),
                attributes={
                    "sha": "a" * 40,
                    "chunk_index": 0,
                    "chunk_count": 1,
                    "commit_chunk_count_total": 1,
                    "commit_chunk_count_emitted": 1,
                    "commit_was_truncated": False,
                },
                source_pointer="https://github.com/example/repo/commit/" + "a" * 40,
            ),
        ),
    )

    manifest = _source_manifest(
        repository="example/repo",
        policy={
            "license": "MIT",
            "authorization": {
                "record_id": "PUBLIC",
                "scope": "read-only",
                "basis": "public repository",
                "reviewed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
        },
        policy_sha256=hashlib.sha256(b"policy").hexdigest(),
        extraction=extraction,
        exported_at="2026-01-02T00:00:00Z",
        source_client={"path": "/usr/bin/gh", "sha256": "b" * 64},
        history_client={"path": "/usr/bin/git", "sha256": "c" * 64},
        remote_identity={
            "repository_response_sha256": "d" * 64,
            "commit_response_sha256": "e" * 64,
            "license_response_sha256": "f" * 64,
            "head_revision": "a" * 40,
            "repository_url": "https://github.com/example/repo",
            "license": "MIT",
        },
        max_commits=1,
        skip_commits=0,
        max_record_tokens=768,
        max_chunks_per_commit=0,
        maximum_truncated_commit_ratio_ppm=0,
    )

    assert manifest["authorization"]["reviewed_at"] == "2026-01-01T00:00:00Z"
    assert manifest["truncation_quality"]["tier"] == "complete"
    assert manifest["record_index"][0]["commit_chunk_count_total"] == 1
    assert verify_attestation(manifest, key, purpose="source_manifest")
