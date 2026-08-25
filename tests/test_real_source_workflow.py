from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

import longworld.core.realworkflow as realworkflow_module
from longworld.core.attestation import ATTESTATION_ENV, attach_attestation
from longworld.core.grounded import apply_grounded, grounded_init
from longworld.core.provenance import (
    ProvenanceError,
    SourceLineage,
    load_verified_source_documents,
)
from longworld.core.realworkflow import (
    load_episode_replay_bundle,
    load_git_workflow_export,
    parse_rfc_workflow,
    query_workflow_fact,
)
from longworld.core.sourcepack import source_pack_artifacts
from longworld.core.state import WorldState
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    artifact_classification,
)
from longworld.core.world import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extend_episode_bundle import create_bundle
from fetch_source_pack import SourceSpec, fetch_source_pack

RFC_FIXTURE = """\
Internet Engineering Task Force (IETF)                  A. Example
Request for Comments: 9999                              Example Org
STD: 123                                                August 2026
Obsoletes: 1111, 2222
Updates: 3333
Category: Standards Track

                    Durable Workflow Semantics

Abstract

   This specification defines durable workflow semantics for tests.
   A durable export preserves issue, commit, continuous-integration, and
   release relationships so downstream tasks can recover real dependencies.
   The records remain in source order and retain their original identifiers.
"""
TEST_ATTESTATION_KEY = b"longworld-test-attestation-key-32-bytes"


def _public_governance() -> dict:
    return {
        "authorization": {
            "record_id": "PUBLIC-GITHUB-TERMS",
            "scope": "read-only workflow export",
            "basis": "public repository",
            "reviewed_at": "2026-08-20T07:00:00Z",
        },
        "privacy_review": {
            "emails": "redacted",
            "secrets": "fail_closed",
            "scanner": "longworld-public-secret-patterns",
            "scanner_revision": "v2",
        },
        "public_policy": {
            "record_id": "PUBLIC-GITHUB-TERMS",
            "sha256": "d" * 64,
        },
        "source_client": {"path": "/usr/bin/gh", "sha256": "e" * 64},
    }


def _release_ancestry(merge_sha: str = "b" * 40) -> dict:
    tag_sha = "a" * 40
    return {
        "tag_commit_sha": tag_sha,
        "merge_commit_sha": merge_sha,
        "ancestry_verified": True,
        "compare_status": "ahead",
        "compare_base_sha": merge_sha,
        "compare_head_sha": tag_sha,
        "compare_endpoint": (
            "https://api.github.com/repos/example/project/compare/"
            f"{merge_sha}...{tag_sha}"
        ),
        "compare_response_sha256": "c" * 64,
    }


@pytest.fixture(autouse=True)
def _attestation_key(monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, TEST_ATTESTATION_KEY.decode())


def _write_v2_pack(pack_dir: Path, *, text: str = RFC_FIXTURE) -> None:
    pack_dir.mkdir()
    source = pack_dir / "misleading-filename.txt"
    source.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "longworld.source-pack.v2",
        "generated_at": "2026-08-20T08:00:00Z",
        "n": 1,
        "docs": [
            {
                "file": source.name,
                "url": "https://www.rfc-editor.org/rfc/rfc9999.txt",
                "license": "IETF Trust Legal Provisions",
                "retrieved_at": "2026-08-20T07:59:00Z",
                "parser": {"name": "plain_text", "version": "1"},
                "chars": len(text),
                "bytes": len(text.encode()),
                "sha256": digest,
            }
        ],
    }
    manifest = attach_attestation(
        manifest, TEST_ATTESTATION_KEY, purpose="source_manifest"
    )
    (pack_dir / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_verified_manifest_is_self_contained_and_classified(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    _write_v2_pack(pack_dir)

    docs = load_verified_source_documents(pack_dir)
    assert len(docs) == 1
    assert docs[0].lineage.url.endswith("rfc9999.txt")
    assert docs[0].lineage.license == "IETF Trust Legal Provisions"
    assert docs[0].lineage.parser == "plain_text@1"
    assert len(docs[0].lineage.sha256) == 64

    [artifact] = source_pack_artifacts(
        "wtest", date(2026, 8, 20), n=1, pack_dir=pack_dir, allow_legacy=False
    )
    cls = artifact_classification(artifact)
    assert cls.source_origin == SourceOrigin.REAL_PUBLIC
    assert cls.workflow_kind == WorkflowKind.BACKGROUND_ONLY
    assert cls.evidence_role == EvidenceRole.NATURAL_BACKGROUND
    assert artifact.slots["provenance_verified"] is True
    assert "not be used as" not in artifact.text.lower()
    assert "not the gold" not in artifact.text.lower()
    assert "may be ignored" not in artifact.text.lower()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["docs"][0].pop("license"),
        lambda manifest: manifest["docs"][0].update(sha256="deadbeef"),
        lambda manifest: manifest["docs"][0].update(retrieved_at="yesterday"),
        lambda manifest: manifest["docs"][0].update(parser={"name": "plain_text"}),
    ],
)
def test_verified_manifest_fails_closed_without_stale_fallback(
    tmp_path: Path, mutation
):
    pack_dir = tmp_path / "pack"
    _write_v2_pack(pack_dir)
    manifest_path = pack_dir / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    mutation(manifest)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ProvenanceError):
        load_verified_source_documents(pack_dir)
    assert (
        source_pack_artifacts(
            "wtest", date(2026, 8, 20), pack_dir=pack_dir, allow_legacy=True
        )
        == []
    )


def test_verified_source_pack_rejects_symlinked_documents(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    _write_v2_pack(pack_dir)
    source = pack_dir / "misleading-filename.txt"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(ProvenanceError):
        load_verified_source_documents(pack_dir)


def test_rfc_workflow_answer_depends_on_body_not_filename(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    _write_v2_pack(pack_dir)
    [doc] = load_verified_source_documents(pack_dir)

    workflow = parse_rfc_workflow(doc.text, doc.lineage)
    assert workflow.workflow_id == "rfc:9999"
    assert query_workflow_fact(workflow, "document_id") == "RFC 9999"
    assert query_workflow_fact(workflow, "standards_track") == "STD 123"
    assert query_workflow_fact(workflow, "obsoletes") == "RFC 1111, RFC 2222"
    assert query_workflow_fact(workflow, "updates") == "RFC 3333"
    document_fact = workflow.facts["document_id"]
    assert RFC_FIXTURE[document_fact.char_start : document_fact.char_end] == "9999"
    assert "misleading-filename" not in query_workflow_fact(workflow, "document_id")


def test_content_grounded_state_uses_parsed_fact():
    state = WorldState(values=grounded_init())
    ingest = Event(
        id="focal.ingest_public",
        type="ingest_public",
        time=date(2026, 8, 1),
        params={
            "stem": "misleading-filename",
            "content_fact": {
                "key": "document_id",
                "value": "RFC 9999",
                "provenance_id": "sha256:abc",
            },
        },
        visibility=["focal.source.misleading-filename"],
    )
    adopt = Event(
        id="focal.adopt_public",
        type="adopt_public",
        time=date(2026, 8, 2),
        params={"adopt_pending": True},
        visibility=["focal.norm_adopt"],
        causal_inputs=[ingest.id],
    )

    assert apply_grounded(state, ingest)
    assert apply_grounded(state, adopt)
    assert state.values["public_norm"] == "misleading-filename"
    assert state.values["public_norm_content"] == "RFC 9999"
    assert state.values["public_norm_provenance_id"] == "sha256:abc"


def test_private_git_exports_are_disabled_until_independent_governance(
    tmp_path: Path,
) -> None:
    export = tmp_path / "workflow.json"
    payload = {
        "schema_version": "longworld.git-workflow.v1",
        "source_origin": "real_private_export",
        "repository_url": "https://github.com/example/project",
        "revision": "abc123",
        "license": "Apache-2.0",
        "exported_at": "2026-08-20T08:00:00Z",
        "private_export_governance": {
            "allowlisted_repository_url": "https://github.com/example/project",
            "authorization": {
                "record_id": "AUTH-2026-08-20-001",
                "scope": "read-only workflow export",
                "actor": "data-governance",
                "granted_at": "2026-08-20T07:00:00Z",
            },
            "privacy_scan": {
                "scanner": "gitleaks+pii-audit",
                "scanned_at": "2026-08-20T07:30:00Z",
                "secret_scan": "passed",
                "pii_scan": "passed",
                "redactions_audited": True,
            },
        },
        "records": [
            {
                "id": "issue:17",
                "kind": "issue",
                "occurred_at": "2026-08-18T09:00:00Z",
                "text": "Parser fails on folded headers.",
                "links": [],
            },
            {
                "id": "commit:abc123",
                "kind": "commit",
                "occurred_at": "2026-08-19T10:00:00Z",
                "text": "Fix folded headers (closes #17).",
                "links": ["issue:17"],
            },
            {
                "id": "ci:88",
                "kind": "ci_run",
                "occurred_at": "2026-08-19T10:05:00Z",
                "text": "linux tests: success",
                "links": ["commit:abc123"],
            },
            {
                "id": "release:v2.4.1",
                "kind": "release",
                "occurred_at": "2026-08-20T08:00:00Z",
                "text": "Release v2.4.1 includes the folded-header fix.",
                "links": ["ci:88"],
                "attributes": {"tag": "v2.4.1"},
            },
        ],
    }
    payload = attach_attestation(payload, TEST_ATTESTATION_KEY, purpose="git_workflow")
    export.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="private workflow exports are disabled"):
        load_git_workflow_export(export)


def test_git_export_rejects_dangling_or_future_links(tmp_path: Path):
    export = tmp_path / "workflow.json"
    export.write_text(
        json.dumps(
            attach_attestation(
                {
                    "schema_version": "longworld.git-workflow.v1",
                    "source_origin": "real_public",
                    "repository_url": "https://github.com/example/project",
                    "revision": "abc123",
                    "license": "Apache-2.0",
                    "exported_at": "2026-08-20T08:00:00Z",
                    **_public_governance(),
                    "records": [
                        {
                            "id": "release:v1",
                            "kind": "release",
                            "occurred_at": "2026-08-20T08:00:00Z",
                            "text": "release",
                            "links": ["ci:missing"],
                            "attributes": {"tag": "v1"},
                        }
                    ],
                },
                TEST_ATTESTATION_KEY,
                purpose="git_workflow",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProvenanceError, match="unknown workflow link"):
        load_git_workflow_export(export)


def test_episode_replay_bundle_loads_only_signed_hash_bound_exports(
    tmp_path: Path,
) -> None:
    export = tmp_path / "workflow.json"
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/project",
            "revision": "b" * 40,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            **_public_governance(),
            "records": [
                {
                    "id": "merge:1",
                    "kind": "merge",
                    "occurred_at": "2026-08-20T07:59:00Z",
                    "text": "Validated merge.",
                    "links": [],
                    "attributes": {"merge_commit_sha": "b" * 40},
                },
                {
                    "id": "release:v1.0.0",
                    "kind": "release",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "Release v1.0.0 follows the validated workflow.",
                    "links": ["merge:1"],
                    "attributes": {
                        "tag": "v1.0.0",
                        **_release_ancestry(),
                    },
                },
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    export.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(export.read_bytes()).hexdigest()
    bundle_path = tmp_path / "episodes.json"
    bundle = attach_attestation(
        {
            "schema_version": "longworld.episode-replay-bundle.v1",
            "composition": "chronological_causal_union",
            "episodes": [{"path": export.name, "sha256": digest}],
        },
        TEST_ATTESTATION_KEY,
        purpose="episode_replay_bundle",
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    workflows = load_episode_replay_bundle(bundle_path)

    assert len(workflows) == 1
    assert workflows[0].records[-1].text.startswith("Release v1.0.0")

    bundle["episodes"][0]["sha256"] = "0" * 64
    bundle = attach_attestation(
        bundle, TEST_ATTESTATION_KEY, purpose="episode_replay_bundle"
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ProvenanceError, match="digest"):
        load_episode_replay_bundle(bundle_path)


def test_episode_bundle_parses_the_same_export_bytes_that_were_hashed(
    tmp_path: Path, monkeypatch
) -> None:
    export = tmp_path / "workflow.json"

    def signed_export(revision: str, text: str) -> bytes:
        payload = attach_attestation(
            {
                "schema_version": "longworld.git-workflow.v1",
                "source_origin": "real_public",
                "repository_url": "https://github.com/example/project",
                "revision": revision,
                "license": "Apache-2.0",
                "exported_at": "2026-08-20T08:00:00Z",
                **_public_governance(),
                "records": [
                    {
                        "id": "merge:1",
                        "kind": "merge",
                        "occurred_at": "2026-08-20T08:00:00Z",
                        "text": text,
                        "links": [],
                    }
                ],
            },
            TEST_ATTESTATION_KEY,
            purpose="git_workflow",
        )
        return json.dumps(payload).encode()

    original = signed_export("a" * 40, "original validated bytes")
    replacement = signed_export("b" * 40, "replacement bytes")
    export.write_bytes(original)
    bundle_path = tmp_path / "episodes.json"
    bundle = attach_attestation(
        {
            "schema_version": "longworld.episode-replay-bundle.v1",
            "composition": "chronological_causal_union",
            "episodes": [
                {
                    "path": export.name,
                    "sha256": hashlib.sha256(original).hexdigest(),
                }
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="episode_replay_bundle",
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    original_reader = realworkflow_module._read_regular_file
    export_reads = 0

    def swapping_reader(path: Path, max_bytes: int) -> bytes:
        nonlocal export_reads
        if path == export:
            export_reads += 1
            return original if export_reads == 1 else replacement
        return original_reader(path, max_bytes)

    monkeypatch.setattr(realworkflow_module, "_read_regular_file", swapping_reader)

    [workflow] = load_episode_replay_bundle(bundle_path)
    assert workflow.lineage.revision == "a" * 40
    assert workflow.records[0].text == "original validated bytes"
    assert export_reads == 1


def test_episode_bundle_can_parse_preverified_manifest_bytes_without_rereading(
    tmp_path: Path, monkeypatch
) -> None:
    export = tmp_path / "workflow.json"
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/project",
            "revision": "a" * 40,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            **_public_governance(),
            "records": [
                {
                    "id": "merge:1",
                    "kind": "merge",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "validated merge",
                    "links": [],
                }
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    export.write_text(json.dumps(payload), encoding="utf-8")
    bundle_path = tmp_path / "episodes.json"
    bundle = attach_attestation(
        {
            "schema_version": "longworld.episode-replay-bundle.v1",
            "composition": "chronological_causal_union",
            "episodes": [
                {
                    "path": export.name,
                    "sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                }
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="episode_replay_bundle",
    )
    raw_bundle = json.dumps(bundle).encode()
    bundle_path.write_bytes(raw_bundle)
    original_reader = realworkflow_module._read_regular_file

    def rejecting_bundle_reread(path: Path, max_bytes: int) -> bytes:
        if path == bundle_path:
            raise AssertionError("bundle path was read after its bytes were verified")
        return original_reader(path, max_bytes)

    monkeypatch.setattr(
        realworkflow_module, "_read_regular_file", rejecting_bundle_reread
    )

    workflows = load_episode_replay_bundle(bundle_path, _verified_raw=raw_bundle)

    assert len(workflows) == 1


def test_git_export_rejects_implicit_private_origin(tmp_path: Path) -> None:
    path = tmp_path / "implicit-private.json"
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "repository_url": "https://github.com/example/project",
            "revision": "abc123",
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            "records": [
                {
                    "id": "issue:1",
                    "kind": "issue",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "Parser issue.",
                    "links": [],
                }
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="explicit source origin"):
        load_git_workflow_export(path)


def test_public_git_export_requires_structured_authorization(tmp_path: Path) -> None:
    path = tmp_path / "public.json"
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/project",
            "revision": "a" * 40,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            "authorization": "public repository",
            "privacy_review": {"emails": "redacted", "secrets": "fail_closed"},
            "records": [
                {
                    "id": "merge:1",
                    "kind": "merge",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "Validated merge.",
                    "links": [],
                }
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="public export authorization"):
        load_git_workflow_export(path)


def test_public_release_requires_executable_ancestry_receipt(tmp_path: Path) -> None:
    path = tmp_path / "public-release.json"
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/project",
            "revision": "a" * 40,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            **_public_governance(),
            "records": [
                {
                    "id": "merge:1",
                    "kind": "merge",
                    "occurred_at": "2026-08-20T07:59:00Z",
                    "text": "Validated merge.",
                    "links": [],
                },
                {
                    "id": "release:v1",
                    "kind": "release",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "Release v1.",
                    "links": ["merge:1"],
                    "attributes": {"tag": "v1"},
                },
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="release ancestry"):
        load_git_workflow_export(path)


def test_public_release_rejects_a_merge_sha_that_disagrees_with_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contradictory-release.json"
    revision = "d" * 40
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/project",
            "revision": revision,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            **_public_governance(),
            "records": [
                {
                    "id": "merge:1",
                    "kind": "merge",
                    "occurred_at": "2026-08-20T07:59:00Z",
                    "text": "Validated merge.",
                    "links": [],
                    "attributes": {"merge_commit_sha": revision},
                },
                {
                    "id": "release:v1",
                    "kind": "release",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "Release v1.",
                    "links": ["merge:1"],
                    "attributes": {"tag": "v1", **_release_ancestry()},
                },
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="release ancestry"):
        load_git_workflow_export(path)


def test_create_episode_bundle_validates_and_binds_exact_exports(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    config_dir = repository_root / "configs"
    workflow_dir = repository_root / "data" / "workflows"
    config_dir.mkdir(parents=True)
    workflow_dir.mkdir(parents=True)
    episode = workflow_dir / "public.json"
    payload = attach_attestation(
        {
            "schema_version": "longworld.git-workflow.v1",
            "source_origin": "real_public",
            "repository_url": "https://github.com/example/project",
            "revision": "a" * 40,
            "license": "Apache-2.0",
            "exported_at": "2026-08-20T08:00:00Z",
            **_public_governance(),
            "records": [
                {
                    "id": "merge:1",
                    "kind": "merge",
                    "occurred_at": "2026-08-20T08:00:00Z",
                    "text": "Validated merge.",
                    "links": [],
                }
            ],
        },
        TEST_ATTESTATION_KEY,
        purpose="git_workflow",
    )
    episode.write_text(json.dumps(payload), encoding="utf-8")
    bundle = config_dir / "episodes.json"

    assert create_bundle(bundle, [episode]) == 1
    assert len(load_episode_replay_bundle(bundle)) == 1

    linked_episode = workflow_dir / "linked-public.json"
    linked_episode.symlink_to(episode)
    with pytest.raises(ProvenanceError, match="regular file|symbolic link"):
        create_bundle(config_dir / "linked-episodes.json", [linked_episode])


def test_source_lineage_rejects_truncated_hash():
    with pytest.raises(ProvenanceError):
        SourceLineage(
            provenance_id="sha256:deadbeef",
            url="https://example.test/source",
            license="test-license",
            retrieved_at="2026-08-20T08:00:00Z",
            parser="plain_text@1",
            sha256="deadbeef",
        )


def test_fetch_source_pack_failure_keeps_previous_pack_untouched(tmp_path: Path):
    out = tmp_path / "pack"
    out.mkdir()
    (out / "old.txt").write_text("old verified bytes")
    (out / "MANIFEST.json").write_text('{"old": true}')
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    sources = {
        "one.txt": SourceSpec("https://example.test/one", "test-license"),
        "two.txt": SourceSpec("https://example.test/two", "test-license"),
    }

    def fail_second(url: str) -> bytes:
        if url.endswith("two"):
            raise OSError("offline")
        return ("first source sentence. " * 30).encode()

    with pytest.raises(OSError, match="offline"):
        fetch_source_pack(
            out,
            sources=sources,
            fetcher=fail_second,
            retrieved_at="2026-08-20T08:00:00Z",
        )
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before


def test_fetch_source_pack_writes_verified_atomic_manifest(tmp_path: Path):
    out = tmp_path / "pack"
    sources = {"one.txt": SourceSpec("https://example.test/one", "test-license")}

    fetch_source_pack(
        out,
        sources=sources,
        fetcher=lambda _url: ("real workflow source sentence. " * 30).encode(),
        retrieved_at="2026-08-20T08:00:00Z",
    )

    [doc] = load_verified_source_documents(out)
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert doc.provenance_verified
    assert len(manifest["docs"][0]["sha256"]) == 64
    assert manifest["docs"][0]["url"] == "https://example.test/one"
    assert manifest["docs"][0]["license"] == "test-license"
    assert manifest["docs"][0]["retrieved_at"] == "2026-08-20T08:00:00Z"
    assert manifest["docs"][0]["parser"] == {
        "name": "plain_text",
        "version": "1",
    }
