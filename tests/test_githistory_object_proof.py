from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

import longworld.core.githistory as githistory_module
from longworld.core.githistory import (
    build_git_object_path_absence_proof,
    build_git_object_path_proof,
    git_history_scan_coverage,
    verify_git_object_path_absence_proof,
    verify_git_object_path_proof,
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "LongWorld test")
    _git(repo, "config", "user.email", "longworld@example.test")
    revisions: list[str] = []
    for index, body in enumerate(("license-v1\n", "license-v2\n"), start=1):
        (repo / "legal").mkdir(exist_ok=True)
        (repo / "legal/LICENSE.txt").write_text(body, encoding="utf-8")
        (repo / "history.txt").write_text(f"release {index}\n", encoding="utf-8")
        _git(repo, "add", "legal/LICENSE.txt", "history.txt")
        _git(repo, "commit", "-m", f"release {index}")
        revisions.append(_git(repo, "rev-parse", "HEAD"))
    return repo, tuple(revisions)


def _write_raw_commit(
    repo: Path,
    *,
    tree: str,
    parent: str,
    extra_header: bytes = b"",
    message: bytes,
    literally: bool = False,
) -> str:
    raw = (
        f"tree {tree}\nparent {parent}\n".encode()
        + b"author Legacy User <legacy@example.test> 0 +0000\n"
        + b"committer Legacy User <legacy@example.test> 0 +0000\n"
        + extra_header
        + b"\n"
        + message
    )
    command = ["/usr/bin/git", "-C", str(repo), "hash-object"]
    if literally:
        command.append("--literally")
    command.extend(["-t", "commit", "-w", "--stdin"])
    completed = subprocess.run(
        command,
        input=raw,
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("ascii").strip()


def _refresh_proof(proof: dict[str, object]) -> None:
    unsigned = {key: value for key, value in proof.items() if key != "proof_sha256"}
    proof["proof_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_git_object_path_proof_replays_commit_tree_and_blob_objects(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    bindings = [
        {
            "revision": revision,
            "git_blob_sha": _git(repo, "rev-parse", f"{revision}:legal/LICENSE.txt"),
        }
        for revision in revisions
    ]

    proof = build_git_object_path_proof(
        repo,
        commit_revisions=revisions,
        path="legal/LICENSE.txt",
    )

    assert verify_git_object_path_proof(
        proof,
        expected_path="legal/LICENSE.txt",
        expected_commit_bindings=bindings,
    ) == {
        "commit_count": 2,
        "object_count": proof["object_count"],
        "total_raw_bytes": proof["total_raw_bytes"],
        "unique_blob_count": 2,
    }


@pytest.mark.parametrize("tamper", ("commit", "tree", "blob"))
def test_git_object_path_proof_rejects_tampered_raw_objects(
    tmp_path: Path, tamper: str
) -> None:
    repo, revisions = _repository(tmp_path)
    bindings = [
        {
            "revision": revision,
            "git_blob_sha": _git(repo, "rev-parse", f"{revision}:legal/LICENSE.txt"),
        }
        for revision in revisions
    ]
    proof = build_git_object_path_proof(
        repo,
        commit_revisions=revisions,
        path="legal/LICENSE.txt",
    )
    corrupted = deepcopy(proof)
    target = next(item for item in corrupted["objects"] if item["type"] == tamper)
    raw = bytearray(base64.b64decode(target["content_base64"], validate=True))
    raw[-1] ^= 1
    target["content_base64"] = base64.b64encode(raw).decode("ascii")
    target["sha256"] = hashlib.sha256(raw).hexdigest()
    corrupted_without_digest = {
        key: value for key, value in corrupted.items() if key != "proof_sha256"
    }
    corrupted["proof_sha256"] = hashlib.sha256(
        json.dumps(
            corrupted_without_digest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    with pytest.raises(ValueError, match="Git object identity"):
        verify_git_object_path_proof(
            corrupted,
            expected_path="legal/LICENSE.txt",
            expected_commit_bindings=bindings,
        )


def test_git_object_path_proof_rejects_forged_tree_path_binding(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    proof = build_git_object_path_proof(
        repo,
        commit_revisions=revisions,
        path="legal/LICENSE.txt",
    )
    bindings = deepcopy(proof["commit_bindings"])
    bindings[-1]["git_blob_sha"] = bindings[0]["git_blob_sha"]
    proof["commit_bindings"] = bindings
    proof["proof_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in proof.items() if key != "proof_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    with pytest.raises(ValueError, match="tree path does not bind"):
        verify_git_object_path_proof(
            proof,
            expected_path="legal/LICENSE.txt",
            expected_commit_bindings=bindings,
        )


def test_git_object_path_proof_rejects_non_first_parent_order(tmp_path: Path) -> None:
    repo, revisions = _repository(tmp_path)
    with pytest.raises(ValueError, match="first-parent"):
        build_git_object_path_proof(
            repo,
            commit_revisions=tuple(reversed(revisions)),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_unreachable_extra_object(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    proof = build_git_object_path_proof(
        repo,
        commit_revisions=revisions,
        path="legal/LICENSE.txt",
    )
    raw = b"unrelated private payload"
    object_id = hashlib.sha1(
        f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False
    ).hexdigest()
    proof["objects"].append(
        {
            "oid": object_id,
            "type": "blob",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode("ascii"),
        }
    )
    proof["object_count"] += 1
    proof["total_raw_bytes"] += len(raw)
    proof["public_metadata_review"]["blob_count"] += 1
    _refresh_proof(proof)

    with pytest.raises(ValueError, match="exact reachable closure"):
        verify_git_object_path_proof(
            proof,
            expected_path="legal/LICENSE.txt",
            expected_commit_bindings=proof["commit_bindings"],
        )


def test_git_object_path_proof_rejects_legacy_non_utf8_commit_message(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    revision = _write_raw_commit(
        repo,
        tree=_git(repo, "rev-parse", f"{revisions[-1]}^{{tree}}"),
        parent=revisions[-1],
        extra_header=b"encoding ISO-8859-1\n",
        message=b"legacy release \xff\n",
    )

    with pytest.raises(ValueError, match="not auditable UTF-8 text"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(revision,),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_cp037_commit_message(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    revision = _write_raw_commit(
        repo,
        tree=_git(repo, "rev-parse", f"{revisions[-1]}^{{tree}}"),
        parent=revisions[-1],
        extra_header=b"encoding IBM037\n",
        message="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n".encode("cp037"),
    )

    with pytest.raises(ValueError, match="not auditable UTF-8 text"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(revision,),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_secret_in_commit_header(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    revision = _write_raw_commit(
        repo,
        tree=_git(repo, "rev-parse", f"{revisions[-1]}^{{tree}}"),
        parent=revisions[-1],
        extra_header=b"mergetag ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n",
        message=b"release\n",
    )

    with pytest.raises(ValueError, match="credential-shaped secret"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(revision,),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_utf16_commit_message(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    revision = _write_raw_commit(
        repo,
        tree=_git(repo, "rev-parse", f"{revisions[-1]}^{{tree}}"),
        parent=revisions[-1],
        message="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n".encode("utf-16le"),
        literally=True,
    )

    with pytest.raises(ValueError, match="not auditable text"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(revision,),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_secret_in_bound_blob(tmp_path: Path) -> None:
    repo, _revisions = _repository(tmp_path)
    (repo / "legal/LICENSE.txt").write_text(
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n", encoding="utf-8"
    )
    _git(repo, "add", "legal/LICENSE.txt")
    _git(repo, "commit", "-m", "update license")

    with pytest.raises(ValueError, match="credential-shaped secret"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(_git(repo, "rev-parse", "HEAD"),),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_utf16_bound_blob(tmp_path: Path) -> None:
    repo, _revisions = _repository(tmp_path)
    (repo / "legal/LICENSE.txt").write_bytes(
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n".encode("utf-16le")
    )
    _git(repo, "add", "legal/LICENSE.txt")
    _git(repo, "commit", "-m", "update encoded license")

    with pytest.raises(ValueError, match="not auditable text"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(_git(repo, "rev-parse", "HEAD"),),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_rejects_secret_in_sibling_tree_name(
    tmp_path: Path,
) -> None:
    repo, _revisions = _repository(tmp_path)
    secret_name = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    (repo / "legal" / secret_name).write_text("public sibling\n", encoding="utf-8")
    _git(repo, "add", f"legal/{secret_name}")
    _git(repo, "commit", "-m", "add sibling")

    with pytest.raises(ValueError, match="credential-shaped secret"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(_git(repo, "rev-parse", "HEAD"),),
            path="legal/LICENSE.txt",
        )


def test_git_object_builders_preflight_raw_byte_limit(
    tmp_path: Path, monkeypatch
) -> None:
    repo, revisions = _repository(tmp_path)
    monkeypatch.setattr(githistory_module, "_MAX_GIT_PROOF_RAW_BYTES", 32)

    with pytest.raises(ValueError, match="portable proof limit"):
        build_git_object_path_proof(
            repo,
            commit_revisions=(revisions[-1],),
            path="legal/LICENSE.txt",
        )
    (repo / "legal/LICENSE.txt").unlink()
    _git(repo, "add", "legal/LICENSE.txt")
    _git(repo, "commit", "-m", "remove license")
    with pytest.raises(ValueError, match="portable proof limit"):
        build_git_object_path_absence_proof(
            repo,
            commit_revision=_git(repo, "rev-parse", "HEAD"),
            path="legal/LICENSE.txt",
        )


def test_git_object_path_proof_binds_blob_bytes_to_approved_metadata(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    proof = build_git_object_path_proof(
        repo,
        commit_revisions=revisions,
        path="legal/LICENSE.txt",
    )
    expected_blobs = [
        {
            "git_blob_sha": binding["git_blob_sha"],
            "sha256": "0" * 64,
            "size": next(
                item["size"]
                for item in proof["objects"]
                if item["oid"] == binding["git_blob_sha"]
            ),
        }
        for binding in proof["commit_bindings"]
    ]

    with pytest.raises(ValueError, match="approved metadata"):
        verify_git_object_path_proof(
            proof,
            expected_path="legal/LICENSE.txt",
            expected_commit_bindings=proof["commit_bindings"],
            expected_license_blobs=expected_blobs,
        )


def test_git_history_scan_coverage_distinguishes_complete_and_gapped_scan() -> None:
    coverage = {
        "schema_version": "longworld.git-history-coverage-boundary.v1",
        "scope": "complete_first_parent_history",
        "root_revision": "a" * 40,
        "total_first_parent_commits": 4,
        "included_commit_count": 4,
        "excluded_commit_count": 0,
    }
    summaries = [
        {
            "repository": "example/repo",
            "history_coverage": coverage,
            "skip_commits": 0,
            "commit_count": 2,
            "history_slice_anchor": {
                "oldest_revision": "b" * 40,
                "newest_revision": "a" * 40,
                "oldest_first_parent_revision": "c" * 40,
            },
        },
        {
            "repository": "example/repo",
            "history_coverage": coverage,
            "skip_commits": 2,
            "commit_count": 2,
            "history_slice_anchor": {
                "oldest_revision": "d" * 40,
                "newest_revision": "c" * 40,
                "oldest_first_parent_revision": None,
            },
        },
    ]

    result = git_history_scan_coverage(summaries)
    assert result[0]["source_scan_complete"] is True
    assert result[0]["processed_commit_count"] == 4

    summaries[1]["skip_commits"] = 3
    summaries[1]["commit_count"] = 1
    result = git_history_scan_coverage(summaries)
    assert result[0]["source_scan_complete"] is False
    assert result[0]["processed_intervals"] == [[0, 2], [3, 4]]


def test_git_history_scan_coverage_rejects_overlap() -> None:
    coverage = {
        "included_commit_count": 3,
    }
    summaries = [
        {
            "repository": "example/repo",
            "history_coverage": coverage,
            "skip_commits": 0,
            "commit_count": 2,
            "history_slice_anchor": {
                "oldest_revision": "b" * 40,
                "newest_revision": "a" * 40,
                "oldest_first_parent_revision": "c" * 40,
            },
        },
        {
            "repository": "example/repo",
            "history_coverage": coverage,
            "skip_commits": 1,
            "commit_count": 2,
            "history_slice_anchor": {
                "oldest_revision": "c" * 40,
                "newest_revision": "b" * 40,
                "oldest_first_parent_revision": None,
            },
        },
    ]

    with pytest.raises(ValueError, match="overlap"):
        git_history_scan_coverage(summaries)


def test_git_object_path_absence_proof_replays_missing_tree_entry(
    tmp_path: Path,
) -> None:
    repo, revisions = _repository(tmp_path)
    (repo / "legal/LICENSE.txt").unlink()
    _git(repo, "add", "legal/LICENSE.txt")
    _git(repo, "commit", "-m", "remove license path")
    revision = _git(repo, "rev-parse", "HEAD")

    proof = build_git_object_path_absence_proof(
        repo, commit_revision=revision, path="legal/LICENSE.txt"
    )

    assert (
        verify_git_object_path_absence_proof(
            proof,
            expected_commit_revision=revision,
            expected_path="legal/LICENSE.txt",
        )["object_count"]
        == proof["object_count"]
    )
    with pytest.raises(ValueError, match="identity"):
        verify_git_object_path_absence_proof(
            proof,
            expected_commit_revision=revisions[-1],
            expected_path="legal/LICENSE.txt",
        )
