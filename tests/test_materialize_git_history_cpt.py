from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from longworld.core.attestation import ATTESTATION_ENV, verify_attestation
from longworld.core.githistory import (
    GitCommitTruncation,
    GitHistoryExtraction,
    bind_git_license_history,
    extract_first_parent_history,
)
from longworld.core.realworkflow import WorkflowRecord

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from materialize_git_history_cpt import (
    CANONICAL_ALLOWLIST,
    _configured_minimum_source_elapsed_seconds,
    _configured_truncation_ratio,
    _deduplicate_extraction,
    _load_allowlist,
    _load_slice_checkpoint,
    _requests,
    _slice_checkpoint_identity,
    _source_manifest,
    _source_slices,
    _validated_history_coverage,
    _write_slice_checkpoint,
    validate_remote_identity,
)

from longworld.core.cptwindow import CPTBand


class _CharacterTokenizer:
    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, list[tuple[int, int]]]:
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        return {"offset_mapping": [(index, index + 1) for index in range(len(text))]}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _license_history_repo(
    tmp_path: Path,
) -> tuple[Path, tuple[str, ...], tuple[bytes, ...]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LongWorld Test")
    _git(repo, "config", "user.email", "test@example.com")
    revisions: list[str] = []
    bodies = (
        b"Copyright (c) 2025 Example\nPermission is hereby granted\n",
        b"Copyright (c) 2026 Example\nPermission is hereby granted\n",
    )
    for index, body in enumerate(bodies):
        (repo / "LICENSE.txt").write_bytes(body)
        (repo / "history.txt").write_text(f"event {index}\n")
        _git(repo, "add", "LICENSE.txt", "history.txt")
        _git(repo, "commit", "-q", "-m", f"event {index}")
        revisions.append(_git(repo, "rev-parse", "HEAD"))
    (repo / "LICENSE.txt").unlink()
    _git(repo, "add", "LICENSE.txt")
    _git(repo, "commit", "-q", "-m", "remove license")
    revisions.append(_git(repo, "rev-parse", "HEAD"))
    return repo, tuple(revisions), bodies


def test_history_coverage_boundary_requires_first_missing_license_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "coverage-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LongWorld Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "history.txt").write_text("pre-license\n", encoding="utf-8")
    _git(repo, "add", "history.txt")
    _git(repo, "commit", "-q", "-m", "pre-license")
    first_excluded = _git(repo, "rev-parse", "HEAD")
    (repo / "LICENSE.txt").write_text("approved license\n", encoding="utf-8")
    _git(repo, "add", "LICENSE.txt")
    _git(repo, "commit", "-q", "-m", "add license")
    root_revision = _git(repo, "rev-parse", "HEAD")
    source = {
        "history_commit_limit": 1,
        "history_coverage": {
            "schema_version": "longworld.git-history-coverage-boundary.v1",
            "scope": "approved_license_path_contiguous_suffix",
            "root_revision": root_revision,
            "total_first_parent_commits": 2,
            "included_commit_count": 1,
            "excluded_commit_count": 1,
            "oldest_included_revision": root_revision,
            "first_excluded_revision": first_excluded,
            "approved_path": "LICENSE.txt",
            "exclusion_reason": "approved_license_path_absent",
        },
    }

    assert (
        _validated_history_coverage(source, repo, root_revision, 2)
        == source["history_coverage"]
    )
    source["history_coverage"]["first_excluded_revision"] = "f" * 40
    with pytest.raises(ValueError, match="boundary revisions disagree"):
        _validated_history_coverage(source, repo, root_revision, 2)


def _license_policy(repo: Path, revisions: tuple[str, ...]) -> dict[str, object]:
    approved_blobs = []
    for revision in revisions:
        blob = _git(repo, "rev-parse", f"{revision}:LICENSE.txt")
        body = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), "cat-file", "blob", blob],
            check=True,
            capture_output=True,
        ).stdout
        approved_blobs.append(
            {
                "git_blob_sha": blob,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        )
    return {
        "schema_version": "longworld.repo-license-binding-policy.v1",
        "approved_path": "LICENSE.txt",
        "approved_blobs": approved_blobs,
    }


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


def test_slice_checkpoint_is_deterministic_and_identity_bound(
    tmp_path: Path,
) -> None:
    config = {
        "schema_version": "longworld.git-history-cpt-materialization.v1",
        "source_slice_commits": 1000,
        "target_rows": {"16k": 2},
    }
    identity = _slice_checkpoint_identity(
        config=config,
        repository="example/repo",
        checkout=tmp_path / "checkout",
        root_revision="a" * 40,
        max_commits=1000,
        skip_commits=2000,
        max_record_tokens=4096,
        max_chunks_per_commit=3,
        tokenizer_model_id="example/tokenizer",
        tokenizer_revision="b" * 40,
        tokenizer_asset_manifest_sha256="c" * 64,
        public_policy_sha256="d" * 64,
        history_client={"sha256": "e" * 64, "size": "123"},
    )
    extraction = GitHistoryExtraction(
        records=(
            WorkflowRecord(
                record_id="git:example/repo:commit:0",
                kind="commit",
                occurred_at="2026-01-01T00:00:00Z",
                text="real patch body",
                links=(),
                attributes={"sha": "e" * 40, "chunk_index": 0},
                source_pointer="https://github.com/example/repo/commit/" + "e" * 40,
            ),
        ),
        commit_count=1,
        head_revision="e" * 40,
        reject_reasons={"binary_patch": 1},
        truncated_commits=(GitCommitTruncation("e" * 40, 3, 1),),
        commit_revisions=("e" * 40,),
    )
    key = b"slice-checkpoint-source-role-key-0001"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    _write_slice_checkpoint(first, identity, extraction, key)
    _write_slice_checkpoint(second, identity, extraction, key)

    assert first.read_bytes() == second.read_bytes()
    assert _load_slice_checkpoint(first, identity, key) == extraction

    changed_identities = (
        {**identity, "config_sha256": "0" * 64},
        {**identity, "tokenizer_asset_manifest_sha256": "1" * 64},
        {**identity, "root_revision": "f" * 40},
        {**identity, "skip_commits": 3000},
        {**identity, "history_client": {"sha256": "f" * 64, "size": "123"}},
    )
    for changed_identity in changed_identities:
        with pytest.raises(ValueError, match="identity does not match"):
            _load_slice_checkpoint(first, changed_identity, key)
    with pytest.raises(ValueError, match="authentication failed"):
        _load_slice_checkpoint(first, identity, b"different-source-role-key-000000001")
    symlink = tmp_path / "checkpoint-link.json"
    symlink.symlink_to(first)
    with pytest.raises(ValueError, match="unreadable"):
        _load_slice_checkpoint(symlink, identity, key)


def test_slice_checkpoint_corruption_fails_closed_and_raw_dedup_is_replayed(
    tmp_path: Path,
) -> None:
    identity = _slice_checkpoint_identity(
        config={"schema_version": "longworld.git-history-cpt-materialization.v1"},
        repository="example/repo",
        checkout=tmp_path / "checkout",
        root_revision="a" * 40,
        max_commits=2,
        skip_commits=0,
        max_record_tokens=4096,
        max_chunks_per_commit=2,
        tokenizer_model_id="example/tokenizer",
        tokenizer_revision="b" * 40,
        tokenizer_asset_manifest_sha256="c" * 64,
        public_policy_sha256="d" * 64,
        history_client={"sha256": "e" * 64, "size": "123"},
    )
    extraction = GitHistoryExtraction(
        records=(
            WorkflowRecord("r0", "commit", "2026-01-01T00:00:00Z", "duplicate", ()),
            WorkflowRecord("r1", "commit", "2026-01-02T00:00:00Z", "fresh", ("r0",)),
        ),
        commit_count=2,
        head_revision="f" * 40,
        reject_reasons={},
        commit_revisions=("e" * 40, "f" * 40),
    )
    key = b"slice-checkpoint-source-role-key-0001"
    checkpoint = tmp_path / "checkpoint.json"
    _write_slice_checkpoint(checkpoint, identity, extraction, key)

    restored = _load_slice_checkpoint(checkpoint, identity, key)
    seen = {hashlib.sha256(b"duplicate").hexdigest()}
    deduplicated = _deduplicate_extraction(restored, seen)
    assert [record.record_id for record in deduplicated.records] == ["r1"]
    assert deduplicated.records[0].links == ()

    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["extraction"]["records"][0]["text"] = "corrupted"
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="authentication failed"):
        _load_slice_checkpoint(checkpoint, identity, key)


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


def test_source_slices_honor_verified_history_commit_limit() -> None:
    source = {
        "repository": "example/a",
        "checkout": "a",
        "history_commit_limit": 1800,
    }

    expanded = _source_slices(
        [source],
        slice_commits=1000,
        commit_count=lambda _: 2500,
    )

    assert [(item["skip_commits"], item["max_commits"]) for item in expanded] == [
        (0, 1000),
        (1000, 800),
    ]


@pytest.mark.parametrize("limit", [None, False, "1000", 0, -1, 2501])
def test_source_slices_reject_invalid_or_unavailable_history_limit(
    limit: object,
) -> None:
    source = {
        "repository": "example/a",
        "checkout": "a",
        "history_commit_limit": limit,
    }

    with pytest.raises(ValueError, match="history commit limit"):
        _source_slices(
            [source],
            slice_commits=1000,
            commit_count=lambda _: 2500,
        )


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


def test_release_mode_requires_independently_approved_allowlist(
    monkeypatch,
) -> None:
    digest = hashlib.sha256(CANONICAL_ALLOWLIST.read_bytes()).hexdigest()
    monkeypatch.setenv("LONGWORLD_ATTESTATION_ENVIRONMENT", "probe")
    monkeypatch.delenv("LONGWORLD_PUBLIC_POLICY_SHA256", raising=False)
    with pytest.raises(ValueError, match="required in release mode"):
        _load_allowlist()

    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="not independently pinned"):
        _load_allowlist()

    monkeypatch.setenv("LONGWORLD_PUBLIC_POLICY_SHA256", digest)
    repositories, observed = _load_allowlist()
    assert "godotengine/godot" in repositories
    assert observed == digest


def test_slice_revision_is_newest_commit_actually_selected(tmp_path: Path) -> None:
    repo, revisions, _ = _license_history_repo(tmp_path)

    extraction = extract_first_parent_history(
        repo,
        repository="example/repo",
        tokenizer=_CharacterTokenizer(),
        max_commits=1,
        skip_commits=1,
        max_record_tokens=10_000,
    )

    assert extraction.head_revision == revisions[1]
    assert extraction.commit_revisions == (revisions[1],)
    assert extraction.head_revision != revisions[2]


def test_history_slice_uses_immutable_configured_root(tmp_path: Path) -> None:
    repo, revisions, _ = _license_history_repo(tmp_path)

    extraction = extract_first_parent_history(
        repo,
        repository="example/repo",
        tokenizer=_CharacterTokenizer(),
        max_commits=1,
        root_revision=revisions[1],
        max_record_tokens=10_000,
    )

    assert extraction.head_revision == revisions[1]
    assert extraction.commit_revisions == (revisions[1],)


def test_license_history_binding_replays_transitions_and_fails_closed(
    tmp_path: Path,
) -> None:
    repo, revisions, _ = _license_history_repo(tmp_path)
    policy = _license_policy(repo, revisions[:2])

    receipt = bind_git_license_history(repo, revisions[:2], policy)

    assert receipt["selected_commit_count"] == 2
    assert receipt["selected_first_revision"] == revisions[0]
    assert receipt["selected_last_revision"] == revisions[1]
    assert receipt["transition_count"] == 1
    assert len(receipt["observed_license_blobs"]) == 2
    assert len(receipt["commit_bindings_sha256"]) == 64

    unapproved = {**policy, "approved_blobs": policy["approved_blobs"][:1]}
    with pytest.raises(ValueError, match="not approved"):
        bind_git_license_history(repo, revisions[:2], unapproved)
    with pytest.raises(ValueError, match="missing"):
        bind_git_license_history(repo, revisions, policy)
    wrong_path = {**policy, "approved_path": "LICENSE.md"}
    with pytest.raises(ValueError, match="missing"):
        bind_git_license_history(repo, revisions[:2], wrong_path)


def test_git_history_remote_identity_requires_public_head_and_license() -> None:
    head = "a" * 40
    responses = {
        "repos/example/repo": {
            "full_name": "example/repo",
            "private": False,
            "visibility": "public",
            "html_url": "https://github.com/example/repo",
            "license": {"spdx_id": "MIT"},
        },
        f"repos/example/repo/commits/{head}": {
            "sha": head,
            "html_url": f"https://github.com/example/repo/commit/{head}",
        },
        f"repos/example/repo/license?ref={head}": {
            "path": "LICENSE",
            "sha": "b" * 40,
            "size": 12,
            "encoding": "base64",
            "content": base64.b64encode(b"MIT license\n").decode(),
            "html_url": f"https://github.com/example/repo/blob/{head}/LICENSE",
            "download_url": (
                f"https://raw.githubusercontent.com/example/repo/{head}/LICENSE"
            ),
            "license": {"spdx_id": "MIT"},
        },
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
    responses["repos/example/repo"]["private"] = False
    responses["repos/example/repo"]["license"] = {"spdx_id": "Apache-2.0"}
    with pytest.raises(ValueError, match="license does not match allowlist"):
        validate_remote_identity("example/repo", head, "MIT", responses.__getitem__)


def test_git_history_remote_identity_binds_revision_license_bytes_when_classifier_is_noassertion() -> (
    None
):
    head = "a" * 40
    license_bytes = b"Copyright Example\n\nPermission is hereby granted...\n"
    responses = {
        "repos/example/repo": {
            "full_name": "example/repo",
            "private": False,
            "visibility": "public",
            "html_url": "https://github.com/example/repo",
            "license": {"spdx_id": "MIT"},
        },
        f"repos/example/repo/commits/{head}": {
            "sha": head,
            "html_url": f"https://github.com/example/repo/commit/{head}",
        },
        f"repos/example/repo/license?ref={head}": {
            "path": "LICENSE.txt",
            "sha": "b" * 40,
            "size": len(license_bytes),
            "encoding": "base64",
            "content": base64.b64encode(license_bytes).decode(),
            "html_url": f"https://github.com/example/repo/blob/{head}/LICENSE.txt",
            "download_url": (
                f"https://raw.githubusercontent.com/example/repo/{head}/LICENSE.txt"
            ),
            "license": {"spdx_id": "NOASSERTION"},
        },
    }

    with pytest.raises(ValueError, match="independently pinned"):
        validate_remote_identity("example/repo", head, "MIT", responses.__getitem__)

    policy = {
        "schema_version": "longworld.repo-license-binding-policy.v1",
        "approved_path": "LICENSE.txt",
        "approved_blobs": [
            {
                "git_blob_sha": "b" * 40,
                "sha256": hashlib.sha256(license_bytes).hexdigest(),
                "size": len(license_bytes),
            }
        ],
    }
    receipt = validate_remote_identity(
        "example/repo",
        head,
        "MIT",
        responses.__getitem__,
        license_binding_policy=policy,
    )

    assert receipt["repository_license_spdx_id"] == "MIT"
    assert receipt["license_file_classifier_spdx_id"] == "NOASSERTION"
    assert receipt["license_file_path"] == "LICENSE.txt"
    assert receipt["license_file_git_blob_sha"] == "b" * 40
    assert receipt["license_file_size"] == len(license_bytes)
    assert receipt["license_file_sha256"] == hashlib.sha256(license_bytes).hexdigest()
    assert receipt["license_file_revision"] == head

    responses[f"repos/example/repo/license?ref={head}"]["size"] += 1
    with pytest.raises(ValueError, match="size does not match content"):
        validate_remote_identity(
            "example/repo",
            head,
            "MIT",
            responses.__getitem__,
            license_binding_policy=policy,
        )

    responses[f"repos/example/repo/license?ref={head}"]["size"] -= 1
    responses[f"repos/example/repo/license?ref={head}"]["content"] = base64.b64encode(
        b"arbitrary bytes with the same lengthxxxxxxxxxxxx"
    ).decode()
    responses[f"repos/example/repo/license?ref={head}"]["size"] = len(
        b"arbitrary bytes with the same lengthxxxxxxxxxxxx"
    )
    with pytest.raises(ValueError, match="not independently pinned"):
        validate_remote_identity(
            "example/repo",
            head,
            "MIT",
            responses.__getitem__,
            license_binding_policy=policy,
        )


def test_git_history_source_manifest_normalizes_yaml_timestamp(
    monkeypatch,
) -> None:
    key = b"git-history-source-manifest-test-key"
    monkeypatch.setenv(ATTESTATION_ENV, key.decode())
    monkeypatch.setattr(
        "materialize_git_history_cpt.validate_git_object_proof_public_export_governance",
        lambda _payload: None,
    )
    extraction = SimpleNamespace(
        head_revision="a" * 40,
        commit_revisions=("a" * 40,),
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
            "repository_license_spdx_id": "MIT",
            "license_file_classifier_spdx_id": "NOASSERTION",
            "license_file_revision": "a" * 40,
            "license_file_path": "LICENSE.txt",
            "license_file_git_blob_sha": "1" * 40,
            "license_file_size": 12,
            "license_file_sha256": "2" * 64,
            "license_file_html_url": (
                "https://github.com/example/repo/blob/" + "a" * 40 + "/LICENSE.txt"
            ),
            "license_file_download_url": (
                "https://raw.githubusercontent.com/example/repo/"
                + "a" * 40
                + "/LICENSE.txt"
            ),
        },
        license_binding={
            "schema_version": "longworld.git-license-binding-receipt.v3",
            "policy": {
                "schema_version": "longworld.repo-license-binding-policy.v1",
                "approved_path": "LICENSE.txt",
                "approved_blobs": [
                    {
                        "git_blob_sha": "1" * 40,
                        "sha256": "2" * 64,
                        "size": 12,
                    }
                ],
            },
            "policy_sha256": "3" * 64,
            "selected_commit_count": 1,
            "selected_first_revision": "a" * 40,
            "selected_last_revision": "a" * 40,
            "commit_bindings": [{"revision": "a" * 40, "git_blob_sha": "1" * 40}],
            "commit_bindings_sha256": "4" * 64,
            "observed_license_blobs": [
                {
                    "git_blob_sha": "1" * 40,
                    "sha256": "2" * 64,
                    "size": 12,
                }
            ],
            "transition_count": 0,
            "object_path_proof": {"public_metadata_review": {}},
        },
        root_revision="a" * 40,
        max_commits=1,
        skip_commits=0,
        max_record_tokens=768,
        max_chunks_per_commit=0,
        maximum_truncated_commit_ratio_ppm=0,
        history_coverage={
            "schema_version": "longworld.git-history-coverage-boundary.v1",
            "scope": "complete_first_parent_history",
            "root_revision": "a" * 40,
            "total_first_parent_commits": 1,
            "included_commit_count": 1,
            "excluded_commit_count": 0,
        },
        history_slice_anchor={
            "oldest_revision": "a" * 40,
            "newest_revision": "a" * 40,
            "oldest_first_parent_revision": None,
        },
        history_coverage_absence_proof=None,
    )

    assert manifest["authorization"]["reviewed_at"] == "2026-01-01T00:00:00Z"
    assert manifest["truncation_quality"]["tier"] == "complete"
    assert manifest["record_index"][0]["commit_chunk_count_total"] == 1
    assert manifest["remote_identity"]["repository_license_spdx_id"] == "MIT"
    assert manifest["remote_identity"]["license_file_sha256"] == "2" * 64
    assert manifest["license_binding"]["schema_version"].endswith(".v3")
    assert manifest["parser"]["revision"] == "v6"
    assert manifest["parser"]["root_revision"] == "a" * 40
    assert manifest["history_coverage"]["excluded_commit_count"] == 0
    assert verify_attestation(manifest, key, purpose="source_manifest")


def test_git_history_source_manifest_rejects_oversized_signed_payload(
    monkeypatch,
) -> None:
    monkeypatch.setattr("materialize_git_history_cpt.MAX_SOURCE_MANIFEST_BYTES", 8)
    monkeypatch.setattr(
        "materialize_git_history_cpt.validate_git_object_proof_public_export_governance",
        lambda _payload: None,
    )
    monkeypatch.setenv(ATTESTATION_ENV, "git-history-source-manifest-test-key")
    with pytest.raises(ValueError, match="audit size limit"):
        _source_manifest(
            repository="example/repo",
            policy={
                "license": "MIT",
                "authorization": {
                    "record_id": "PUBLIC",
                    "scope": "read-only",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
            },
            policy_sha256="a" * 64,
            extraction=SimpleNamespace(
                head_revision="b" * 40,
                commit_revisions=("b" * 40,),
                commit_count=0,
                reject_reasons={},
                truncated_commits=(),
                records=(),
            ),
            exported_at="2026-01-02T00:00:00Z",
            source_client={"path": "/usr/bin/gh", "sha256": "c" * 64},
            history_client={"path": "/usr/bin/git", "sha256": "d" * 64},
            remote_identity={},
            license_binding={"object_path_proof": {"public_metadata_review": {}}},
            root_revision="b" * 40,
            max_commits=1,
            skip_commits=0,
            max_record_tokens=768,
            max_chunks_per_commit=1,
            maximum_truncated_commit_ratio_ppm=None,
            history_coverage={},
            history_slice_anchor={},
            history_coverage_absence_proof=None,
        )
