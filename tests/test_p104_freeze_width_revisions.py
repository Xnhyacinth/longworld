"""Regression checks for exact-revision cache validation and path receipts."""

import fcntl
import hashlib
import json
import runpy
from pathlib import Path

import pytest

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/p104_freeze_width_revisions.py")
)


def _payload(revid: int = 42, title: str = "List of examples") -> bytes:
    return json.dumps(
        {
            "query": {
                "pages": [
                    {
                        "pageid": 9,
                        "title": title,
                        "revisions": [
                            {
                                "revid": revid,
                                "timestamp": "2026-01-01T00:00:00Z",
                                "slots": {
                                    "main": {
                                        "contentmodel": "wikitext",
                                        "content": "{|\n! Name !! Type\n|}",
                                    }
                                },
                            }
                        ],
                    }
                ],
            },
        }
    ).encode()


def test_cached_revision_resumes_outside_repository(tmp_path: Path) -> None:
    job = {
        "doc_id": "doc-9",
        "revid": 42,
        "title": "List of examples",
        "user_agent": "test",
    }
    path = tmp_path / "responses/doc-9-r42.json"
    path.parent.mkdir()
    path.write_bytes(_payload())
    result = MODULE["_fetch"](job, tmp_path, MODULE["RateLimiter"](2))
    assert result["fetched_via"] == "verified-cache"
    assert result["response_path"] == "responses/doc-9-r42.json"
    assert result["wikitext_chars"] > 0


def test_cached_wrong_revision_fails_closed(tmp_path: Path) -> None:
    job = {
        "doc_id": "doc-9",
        "revid": 42,
        "title": "List of examples",
        "user_agent": "test",
    }
    path = tmp_path / "responses/doc-9-r42.json"
    path.parent.mkdir()
    path.write_bytes(_payload(revid=43))
    with pytest.raises(MODULE["SourceResponseError"], match="identity"):
        MODULE["_fetch"](job, tmp_path, MODULE["RateLimiter"](2))


def test_completed_resume_verifies_without_rewriting_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = {
        "doc_id": "doc-9",
        "revid": 42,
        "title": "List of examples",
        "user_agent": "test",
    }
    path = tmp_path / "responses/doc-9-r42.json"
    path.parent.mkdir()
    path.write_bytes(_payload())
    record = MODULE["_fetch"](job, tmp_path, MODULE["RateLimiter"](2))
    manifest = {
        "schema": MODULE["SCHEMA"],
        "source_pool_sha256": MODULE["POOL_SHA256"],
        "page_audit_sha256": MODULE["AUDIT_SHA256"],
        "planned_pages": 1,
        "frozen_pages": 1,
        "failed_pages": 0,
        "records": [record],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    monkeypatch.setitem(MODULE["run"].__globals__, "plan", lambda: [job])
    assert MODULE["run"](tmp_path, 4, 2) == manifest
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == before
    path.write_bytes(_payload(revid=43))
    with pytest.raises(ValueError, match="identity"):
        MODULE["run"](tmp_path, 4, 2)


def test_concurrent_writer_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(MODULE["run"].__globals__, "plan", list)
    lock_path = tmp_path / ".freeze.lock"
    with lock_path.open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another freeze owns"):
            MODULE["run"](tmp_path, 4, 2)
