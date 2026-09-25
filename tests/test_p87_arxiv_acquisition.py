"""Offline discovery tests; no network or archive download is needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import acquire_p87_arxiv_sources as acquire


def _atom(*identifiers: str) -> bytes:
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/{identifier}</id>"
        f"<title>Paper {identifier}</title><updated>2026-01-01T00:00:00Z</updated>"
        "</entry>"
        for identifier in identifiers
    )
    return (f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>').encode()


def _config(tmp_path: Path) -> Path:
    config = {
        "schema": acquire.SCHEMA,
        "api_url": "https://export.arxiv.org/api/query",
        "user_agent": "LongWorld/0.1 tests@example.org",
        "categories": ["cs.CL", "q-bio.MN"],
        "sort_by": "lastUpdatedDate",
        "sort_order": "descending",
        "page_size": 10,
        "minimum_request_interval_seconds": 3.2,
        "metadata_request_limit": 2,
        "source_work_limit": 2,
        "source_archive_limit": 4,
        "minimum_latest_version": 2,
        "exclude_work_ids": ["2401.00001"],
        "content_use": "local_research_only_no_redistribution",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_offline_discovery_dedup_split_hashes_and_request_pacing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    responses = [
        _atom("2401.00001v3", "2401.00002v4", "2401.00003v1"),
        _atom("2401.00002v4", "2401.00004v2"),
    ]
    clock = [10.0]
    request_times = []

    def get(url: str, user_agent: str) -> bytes:
        assert user_agent.endswith("tests@example.org")
        request_times.append(clock[0])
        return responses[len(request_times) - 1]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    output = tmp_path / "output"
    receipt = acquire.acquire(
        config,
        output,
        fetch_sources=False,
        http_get=get,
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    assert request_times == [10.0, 13.2]
    assert receipt["metadata_entries"] == 5
    assert receipt["distinct_works"] == 4
    assert [item["work_id"] for item in receipt["selected_works"]] == [
        "2401.00002",
        "2401.00004",
    ]
    assert all(
        item["split"] == acquire._split(item["work_id"])
        for item in receipt["selected_works"]
    )
    assert receipt["source_archives_planned"] == 0
    assert receipt["source_archives_acquired"] == 0
    assert len(receipt["metadata_pages"]) == 2
    assert all((output / page["file"]).is_file() for page in receipt["metadata_pages"])
    assert receipt["train_ready"] is False


def test_metadata_failure_keeps_error_without_selecting_sources(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def unavailable(_url: str, _agent: str) -> bytes:
        raise OSError("fixture network unavailable")

    receipt = acquire.acquire(
        config,
        tmp_path / "failed",
        fetch_sources=True,
        http_get=unavailable,
    )
    assert receipt["status"] == "metadata_failed"
    assert receipt["selected_works"] == []
    assert receipt["source_archives_planned"] == 0
    assert receipt["source_archives_acquired"] == 0
    assert receipt["errors"][0]["error_type"] == "OSError"
    assert not (tmp_path / "failed/source_inventory").exists()


def test_rejects_rate_or_archive_caps_beyond_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    value = json.loads(config.read_text())
    value["minimum_request_interval_seconds"] = 2.9
    config.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unsafe acquisition"):
        acquire.acquire(config, tmp_path / "unsafe", fetch_sources=False)
