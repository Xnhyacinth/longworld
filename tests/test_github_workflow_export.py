from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.realworkflow import load_git_workflow_export

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from export_github_workflow import (
    GH_BINARY_SHA256_ENV,
    _gh_fetch,
    build_public_release_episode,
    release_mentions_pull,
    render_commit_files,
    sanitize_public_text,
)

TEST_KEY = b"github-export-test-attestation-key-32-bytes"


@pytest.fixture(autouse=True)
def _key(monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, TEST_KEY.decode())


def _responses() -> dict[str, object]:
    return {
        "repos/acme/tool": {
            "full_name": "acme/tool",
            "private": False,
            "visibility": "public",
            "html_url": "https://github.com/acme/tool",
        },
        "repos/acme/tool/license": {
            "sha": "license-sha",
            "path": "LICENSE",
            "license": {"spdx_id": "Apache-2.0"},
            "content": base64.b64encode(
                b"Apache License 2.0 contact legal@example.com"
            ).decode(),
        },
        "repos/acme/tool/commits?path=LICENSE&sha=base7&per_page=1": [
            {"commit": {"committer": {"date": "2025-01-01T00:00:00Z"}}}
        ],
        "repos/acme/tool/contents/LICENSE?ref=base7": {
            "content": base64.b64encode(
                b"Apache License 2.0 contact legal@example.com"
            ).decode()
        },
        "repos/acme/tool/pulls/7": {
            "created_at": "2026-01-01T00:10:00Z",
            "merged_at": "2026-01-01T01:00:00Z",
            "merge_commit_sha": "7" * 40,
            "title": "Resolve parser regression",
            "body": "Tracks the parser failure reported by dev@example.com",
            "head": {"sha": "c2"},
            "base": {"sha": "base7"},
        },
        "repos/acme/tool/releases/tags/v2": {
            "published_at": "2026-01-02T00:00:00Z",
            "body": "Release v2 includes #7 after its successful CI run.",
            "target_commitish": "main",
            "url": "https://api.github.com/repos/acme/tool/releases/2",
        },
        "repos/acme/tool/commits/v2": {"sha": "2" * 40},
        f"repos/acme/tool/compare/{'7' * 40}...{'2' * 40}": {
            "status": "ahead",
            "base_commit": {"sha": "7" * 40},
        },
        "repos/acme/tool/pulls/7/commits?per_page=100&page=1": [
            {
                "sha": "c1",
                "commit": {
                    "committer": {"date": "2026-01-01T00:00:00Z"},
                    "message": "Add failing parser regression test",
                },
            },
            {
                "sha": "c2",
                "commit": {
                    "committer": {"date": "2026-01-01T00:20:00Z"},
                    "message": "Fix parser state transition",
                },
            },
        ],
        "repos/acme/tool/commits/c1": {
            "files": [{"filename": "test_parser.py", "patch": "+ failing case"}]
        },
        "repos/acme/tool/commits/c2": {
            "files": [{"filename": "parser.py", "patch": "+ preserve state"}]
        },
        "repos/acme/tool/pulls/7/reviews?per_page=100&page=1": [
            {
                "id": 9,
                "submitted_at": "2026-01-01T00:30:00Z",
                "commit_id": "c2",
                "state": "APPROVED",
                "body": "The regression test and fix agree.",
            }
        ],
        "repos/acme/tool/pulls/7/comments?per_page=100&page=1": [],
        "repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1": {
            "total_count": 1,
            "check_runs": [
                {
                    "id": 11,
                    "head_sha": "c2",
                    "name": "parser-tests",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-01-01T00:35:00Z",
                    "completed_at": "2026-01-01T00:40:00Z",
                    "app": {"id": 1, "slug": "github-actions"},
                    "url": "https://api.github.com/repos/acme/tool/check-runs/11",
                }
            ],
        },
        "repos/acme/tool/commits/c1/check-runs?filter=all&per_page=100&page=1": {
            "total_count": 1,
            "check_runs": [
                {
                    "id": 10,
                    "head_sha": "c1",
                    "name": "parser-tests",
                    "status": "completed",
                    "conclusion": "failure",
                    "started_at": "2026-01-01T00:02:00Z",
                    "completed_at": "2026-01-01T00:05:00Z",
                    "app": {"id": 1, "slug": "github-actions"},
                    "url": "https://api.github.com/repos/acme/tool/check-runs/10",
                }
            ],
        },
    }


def test_public_episode_preserves_body_relations_and_loads_as_real_workflow(
    tmp_path: Path,
) -> None:
    responses = _responses()
    payload = build_public_release_episode(
        "acme/tool",
        7,
        "v2",
        {
            "visibility": "public",
            "license": "Apache-2.0",
            "authorization": {
                "record_id": "PUBLIC-GITHUB-TERMS",
                "scope": "read-only workflow export",
                "basis": "public repository",
                "reviewed_at": "2026-01-01T00:00:00Z",
            },
            "allowed_record_kinds": [
                "pull_request",
                "review",
                "commit",
                "ci_run",
                "merge",
                "release",
                "license",
            ],
        },
        responses.__getitem__,
        exported_at="2026-01-03T00:00:00Z",
    )
    assert [record["occurred_at"] for record in payload["records"]] == sorted(
        record["occurred_at"] for record in payload["records"]
    )
    assert "dev@example.com" not in json.dumps(payload)
    assert "legal@example.com" not in json.dumps(payload)
    assert any(
        record["kind"] == "release" and "pull_request:7" in record["links"]
        for record in payload["records"]
    )
    assert any(
        record["kind"] == "ci_run" and record["attributes"]["head_sha"] == "c2"
        for record in payload["records"]
    )
    assert any(
        record["id"] == "ci:11" and "run_id=11" in record["text"]
        for record in payload["records"]
    )
    assert any(
        record["id"] == "review:9" and "review_id=9" in record["text"]
        for record in payload["records"]
    )
    assert any(
        record["kind"] == "ci_run"
        and record["attributes"]["head_sha"] == "c1"
        and record["attributes"]["conclusion"] == "failure"
        for record in payload["records"]
    )
    assert any(
        record["kind"] == "merge"
        and {"pull_request:7", "commit:c2", "license:license-sha"}
        <= set(record["links"])
        for record in payload["records"]
    )

    path = tmp_path / "episode.json"
    path.write_text(
        json.dumps(attach_attestation(payload, TEST_KEY, purpose="git_workflow")),
        encoding="utf-8",
    )
    workflow = load_git_workflow_export(path)
    assert workflow.source_origin.value == "real_public"
    assert workflow.facts["release_tag"].value == "v2"
    assert any(
        "Resolve parser regression" in record.text for record in workflow.records
    )


def test_public_episode_rejects_secret_shaped_source_text() -> None:
    responses = _responses()
    responses["repos/acme/tool/pulls/7"]["body"] = (
        "token ghp_abcdefghijklmnopqrstuvwxyz123456"
    )
    with pytest.raises(ValueError, match="credential-shaped"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


@pytest.mark.parametrize(
    "text",
    (
        "OpenAI token sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "npm token npm_abcdefghijklmnopqrstuvwxyz123456",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz.1234567890",
    ),
)
def test_public_scanner_rejects_additional_credential_families(text: str) -> None:
    with pytest.raises(ValueError, match="credential-shaped"):
        sanitize_public_text(text)


def test_release_relation_requires_an_explicit_pull_reference() -> None:
    assert release_mentions_pull("Release includes PR #7.", 7)
    assert release_mentions_pull("See https://github.com/acme/tool/pull/7", 7)
    assert release_mentions_pull("Parser fix (#7)", 7)
    assert not release_mentions_pull("Supports Python 3.7", 7)
    assert not release_mentions_pull("Resolved issue #7", 7)
    assert not release_mentions_pull("Resolved issue 7", 7)


def test_public_pull_episode_does_not_invent_a_release() -> None:
    responses = _responses()
    payload = build_public_release_episode(
        "acme/tool",
        7,
        None,
        {
            "visibility": "public",
            "license": "Apache-2.0",
            "authorization": {
                "record_id": "PUBLIC-GITHUB-TERMS",
                "scope": "read-only workflow export",
                "basis": "public repository",
                "reviewed_at": "2026-01-01T00:00:00Z",
            },
            "allowed_record_kinds": [
                "pull_request",
                "review",
                "commit",
                "ci_run",
                "merge",
                "license",
            ],
        },
        responses.__getitem__,
        exported_at="2026-01-03T00:00:00Z",
    )

    assert not any(record["kind"] == "release" for record in payload["records"])
    assert any(record["kind"] == "merge" for record in payload["records"])


def test_release_episode_requires_merge_ancestry() -> None:
    responses = _responses()
    responses[f"repos/acme/tool/compare/{'7' * 40}...{'2' * 40}"]["status"] = "diverged"

    with pytest.raises(ValueError, match="does not contain the pull merge"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


def test_check_run_pagination_must_match_total_count() -> None:
    responses = _responses()
    responses["repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1"][
        "total_count"
    ] = 2

    with pytest.raises(ValueError, match="pagination coverage"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


def test_release_links_latest_pre_merge_attempt_per_app_and_check_name() -> None:
    responses = _responses()
    responses[
        "repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1"
    ] = {
        "total_count": 4,
        "check_runs": [
            {
                "id": 12,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-01-01T00:35:00Z",
                "completed_at": "2026-01-01T00:50:00Z",
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/12",
            },
            {
                "id": 11,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-01-01T00:39:00Z",
                "completed_at": "2026-01-01T00:40:00Z",
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/11",
            },
            {
                "id": 13,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-01-01T01:05:00Z",
                "completed_at": "2026-01-01T01:10:00Z",
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/13",
            },
            {
                "id": 14,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-01-01T00:38:00Z",
                "completed_at": "2026-01-01T00:42:00Z",
                "app": {"slug": "other-ci"},
                "url": "https://api.github.com/repos/acme/tool/check-runs/14",
            },
        ],
    }

    payload = build_public_release_episode(
        "acme/tool",
        7,
        "v2",
        {
            "visibility": "public",
            "license": "Apache-2.0",
            "authorization": {
                "record_id": "PUBLIC-GITHUB-TERMS",
                "scope": "read-only workflow export",
                "basis": "public repository",
                "reviewed_at": "2026-01-01T00:00:00Z",
            },
            "allowed_record_kinds": [
                "pull_request",
                "review",
                "commit",
                "ci_run",
                "merge",
                "release",
                "license",
            ],
        },
        responses.__getitem__,
        exported_at="2026-01-03T00:00:00Z",
    )

    release = next(
        record for record in payload["records"] if record["kind"] == "release"
    )
    assert "ci:11" in release["links"]
    assert "ci:14" in release["links"]
    assert "ci:12" not in release["links"]
    assert "ci:13" not in release["links"]
    linked_checks = {
        record["id"]: record
        for record in payload["records"]
        if record["id"] in release["links"] and record["kind"] == "ci_run"
    }
    assert linked_checks["ci:11"]["attributes"]["app_id"] == 1
    assert linked_checks["ci:14"]["attributes"]["app_slug"] == "other-ci"
    assert (
        release["attributes"]["ci_evidence_scope"]
        == "observed_selected_final_pre_merge_check_runs"
    )
    assert release["attributes"]["required_check_policy_verified"] is False


@pytest.mark.parametrize(
    "conclusion",
    (
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "stale",
        "startup_failure",
    ),
)
def test_release_rejects_each_blocking_head_check_conclusion(
    conclusion: str,
) -> None:
    responses = _responses()
    check = responses[
        "repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1"
    ]["check_runs"][0]
    check["conclusion"] = conclusion

    with pytest.raises(ValueError, match="observed selected pre-merge CI checks"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


def test_release_rejects_missing_selected_head_checks() -> None:
    responses = _responses()
    responses[
        "repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1"
    ] = {
        "total_count": 0,
        "check_runs": [],
    }

    with pytest.raises(ValueError, match="observed selected pre-merge CI checks"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


def test_later_pending_pre_merge_attempt_cannot_reuse_older_success() -> None:
    responses = _responses()
    responses[
        "repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1"
    ] = {
        "total_count": 2,
        "check_runs": [
            {
                "id": 11,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-01-01T00:35:00Z",
                "completed_at": "2026-01-01T00:40:00Z",
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/11",
            },
            {
                "id": 12,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "in_progress",
                "conclusion": None,
                "started_at": "2026-01-01T00:45:00Z",
                "completed_at": None,
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/12",
            },
        ],
    }

    with pytest.raises(ValueError, match="observed selected pre-merge CI checks"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


def test_older_slow_success_cannot_hide_a_newer_failed_head_check() -> None:
    responses = _responses()
    responses[
        "repos/acme/tool/commits/c2/check-runs?filter=all&per_page=100&page=1"
    ] = {
        "total_count": 2,
        "check_runs": [
            {
                "id": 11,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-01-01T00:35:00Z",
                "completed_at": "2026-01-01T00:50:00Z",
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/11",
            },
            {
                "id": 12,
                "head_sha": "c2",
                "name": "parser-tests",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-01-01T00:39:00Z",
                "completed_at": "2026-01-01T00:41:00Z",
                "app": {"id": 1},
                "url": "https://api.github.com/repos/acme/tool/check-runs/12",
            },
        ],
    }

    with pytest.raises(ValueError, match="observed selected pre-merge CI checks"):
        build_public_release_episode(
            "acme/tool",
            7,
            "v2",
            {
                "visibility": "public",
                "license": "Apache-2.0",
                "authorization": {
                    "record_id": "PUBLIC-GITHUB-TERMS",
                    "scope": "read-only workflow export",
                    "basis": "public repository",
                    "reviewed_at": "2026-01-01T00:00:00Z",
                },
                "allowed_record_kinds": [
                    "pull_request",
                    "review",
                    "commit",
                    "ci_run",
                    "merge",
                    "release",
                    "license",
                ],
            },
            responses.__getitem__,
            exported_at="2026-01-03T00:00:00Z",
        )


def test_large_mechanical_commit_uses_real_file_stats_not_repeated_patches() -> None:
    files = [
        {
            "filename": f"src/file_{index}.php",
            "status": "modified",
            "additions": 1,
            "deletions": 2,
            "changes": 3,
            "sha": f"sha-{index}",
            "patch": "- repeated header\n+ repeated header",
        }
        for index in range(51)
    ]

    rendered = render_commit_files(files)

    assert "51 files changed" in rendered
    assert "src/file_50.php" in rendered
    assert "additions=1 deletions=2 changes=3" in rendered
    assert "repeated header" not in rendered


def test_github_client_child_process_cannot_read_attestation_secrets(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace(stdout="{}")

    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "production")
    monkeypatch.setenv(
        GH_BINARY_SHA256_ENV,
        hashlib.sha256(Path("/usr/bin/gh").read_bytes()).hexdigest(),
    )
    monkeypatch.setenv("GH_TOKEN", "github-token-needed-by-client")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_CONFIG_DIR", "/tmp/test-gh-config")
    monkeypatch.setenv("HF_TOKEN", "unrelated-hf-token")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "unrelated-hf-hub-token")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-aws-secret")
    for name in (*ROLE_KEY_ENVS.values(), *ROLE_KEY_ID_ENVS.values()):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setattr("export_github_workflow.subprocess.run", fake_run)

    assert _gh_fetch("repos/acme/tool") == {}
    assert ATTESTATION_ENV not in captured
    assert ATTESTATION_ENVIRONMENT_ENV not in captured
    assert all(name not in captured for name in ROLE_KEY_ENVS.values())
    assert all(name not in captured for name in ROLE_KEY_ID_ENVS.values())
    assert captured == {
        "PATH": "/usr/bin:/bin",
        "GH_TOKEN": "github-token-needed-by-client",
        "GH_CONFIG_DIR": "/tmp/test-gh-config",
        "GH_PROMPT_DISABLED": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
    }
