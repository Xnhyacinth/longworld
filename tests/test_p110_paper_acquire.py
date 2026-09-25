"""Transport failures must stop paper acquisition before source rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import p110_paper_acquire as acquire
from scripts.fetch_paper_workflow import HttpResponse


def test_empty_429_and_empty_200_are_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acquire,
        "get_source",
        lambda *_args: HttpResponse(
            b"", 429, "https://export.arxiv.org/api/query", "text/plain"
        ),
    )
    with pytest.raises(ValueError, match="HTTP 429; body_bytes=0"):
        acquire.classified_get_source("https://export.arxiv.org/api/query", {}, 90)
    monkeypatch.setattr(
        acquire,
        "get_source",
        lambda *_args: HttpResponse(
            b"", 200, "https://export.arxiv.org/api/query", "text/plain"
        ),
    )
    with pytest.raises(ValueError, match="empty HTTP 200 body"):
        acquire.classified_get_source("https://export.arxiv.org/api/query", {}, 90)


def test_backoff_resume_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fetch(_config: Path, _output: Path, *, resume: bool, source_get):
        calls.append((resume, source_get))
        return {"status": "transport_blocked" if len(calls) == 1 else "complete"}

    monkeypatch.setattr(acquire.p105, "fetch", fetch)
    monkeypatch.setattr(acquire.time, "sleep", lambda seconds: calls.append(seconds))
    result = acquire.run(Path("config"), Path("output"), backoff_seconds=60)
    assert result["status"] == "complete"
    assert calls == [
        (False, acquire.classified_get_source),
        60,
        (True, acquire.classified_get_source),
    ]
