"""P105 keeps source acquisition bounded and provenance checks replayable."""

from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import p105_paper_acquire as acquire
from scripts import p105_paper_http as http
from scripts import run_p86_frozen_paper_batch as paper_batch
from scripts.fetch_paper_workflow import HttpResponse


def _write(path: Path, value: dict) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_http_rejects_other_hosts_before_running_curl(monkeypatch):
    monkeypatch.setattr(
        http.subprocess, "run", lambda *_a, **_k: pytest.fail("curl ran")
    )
    with pytest.raises(ValueError, match="destination"):
        http._once("https://example.org/api/query", {}, 10, 100)


def test_http_checks_response_size_and_does_not_use_shell(monkeypatch):
    def fake_run(command, **kwargs):
        assert (
            "--proto" in command and command[command.index("--proto") + 1] == "=https"
        )
        assert "--max-redirs" in command
        assert "shell" not in kwargs
        Path(command[command.index("--output") + 1]).write_bytes(b"abcd")
        Path(command[command.index("--dump-header") + 1]).write_text("HTTP/2 200\n")
        return SimpleNamespace(
            returncode=0,
            stdout=f"200\n{command[-1]}\napplication/atom+xml",
            stderr="",
        )

    monkeypatch.setattr(http.subprocess, "run", fake_run)
    url = "https://export.arxiv.org/api/query?id_list=2608.31046v2"
    with pytest.raises(ValueError, match="size"):
        http._once(url, {}, 10, 3)
    response, redirect = http._once(url, {}, 10, 4)
    assert response.body == b"abcd" and redirect is None


def test_source_redirect_is_pinned_and_paced(monkeypatch):
    requested = "https://export.arxiv.org/e-print/2608.31046v2"
    expected = "https://export.arxiv.org/src/2608.31046v2"
    calls = []

    def fake_once(url, _headers, _timeout, _max_bytes):
        calls.append(url)
        if len(calls) == 1:
            return HttpResponse(b"redirect", 301, requested, "text/html"), expected
        return HttpResponse(b"archive", 200, expected, "application/x-tar"), None

    monkeypatch.setattr(http, "_once", fake_once)
    sleeps = []
    response = http.get_source(requested, {}, 10, sleep=sleeps.append)
    assert calls == [requested, expected]
    assert sleeps == [http.MIN_INTERVAL_SECONDS] * 2
    assert response.redirect_chain[0].to_url == expected


def test_source_redirect_rejects_other_destination(monkeypatch):
    monkeypatch.setattr(
        http,
        "_once",
        lambda url, *_a: (
            HttpResponse(b"redirect", 301, url, "text/html"),
            "https://other.example/src/2608.31046v2",
        ),
    )
    with pytest.raises(ValueError, match="differs"):
        http.get_source("https://export.arxiv.org/e-print/2608.31046v2", {}, 10)


def test_bare_p86_work_id_replays_frozen_versions_without_refetch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(acquire, "ROOT", tmp_path)
    monkeypatch.setattr(paper_batch, "ROOT", tmp_path)
    work = {
        "work_id": "2608.31046",
        "versions": ["v1", "v2"],
        "split": "train",
        "category_query": "cat:cs.CL",
        "license_status": "not_reported_by_atom",
        "license_uri": [],
    }
    output = tmp_path / "sources"
    directory = acquire._work_dir(output, work["work_id"])
    inventory_path = directory / "inventory/paper_fetch_inventory.json"
    inventory_path.parent.mkdir(parents=True)
    records = []
    for version in work["versions"]:
        name = f"arxiv-{work['work_id']}{version}.source.tar"
        archive = inventory_path.parent / name
        archive.write_bytes(version.encode())
        records.append(
            {
                "work_id": "arxiv:" + work["work_id"],
                "revision_id": version,
                "source_archive_file": name,
                "source_archive_sha256": acquire._sha(archive),
            }
        )
    inventory_path.write_text(
        json.dumps(
            {
                "source_status": "public_api_export",
                "authorization": {"basis": "local research"},
                "records": records,
            }
        )
    )
    (directory / "request.json").write_text(
        json.dumps(
            {"arxiv_version_ids": [work["work_id"] + v for v in work["versions"]]}
        )
    )
    receipt, _entry = acquire._existing_inventory(directory, work)
    assert receipt["work_id"] == work["work_id"]
    assert receipt["versions"] == ["v1", "v2"]
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    config = {
        "catalog_manifest": {"sha256": "frozen-catalog"},
        "max_archive_bytes_total": 100,
        "request_interval_seconds": 3.2,
        "content_use": "local_research_only_no_redistribution",
    }
    monkeypatch.setattr(
        acquire,
        "_config",
        lambda _path: (config, {"selected_works": [work]}),
    )
    (output / "lock.json").write_text(
        acquire._dump(
            {
                "schema": acquire.SCHEMA + ".lock",
                "config_sha256": acquire._sha(config_path),
                "catalog_manifest_sha256": "frozen-catalog",
            }
        )
    )

    def no_fetch(*_args, **_kwargs):
        pytest.fail("frozen work was fetched again")

    monkeypatch.setattr(acquire, "fetch_paper_workflow", no_fetch)
    result = acquire.fetch(config_path, output, resume=True, sleep=no_fetch)
    assert result["frozen_works"] == 1
    assert result["frozen_source_archives"] == 2
    assert result["status"] == "complete"
    final_bytes = (output / "manifest.json").read_bytes()
    acquire.fetch(config_path, output, resume=True, sleep=no_fetch)
    assert (output / "manifest.json").read_bytes() == final_bytes
    assert acquire.verify_fetch(config_path, output)["frozen_works"] == 1
    records[1]["source_archive_sha256"] = "0" * 64
    inventory_path.write_text(
        json.dumps(
            {
                "source_status": "public_api_export",
                "authorization": {"basis": "local research"},
                "records": records,
            }
        )
    )
    with pytest.raises(ValueError, match="archive provenance changed"):
        acquire._existing_inventory(directory, work)
    with pytest.raises(ValueError, match="archive provenance changed"):
        acquire.verify_fetch(config_path, output)


def test_output_lock_rejects_a_second_writer(tmp_path):
    output = tmp_path / "cohort"
    with (
        acquire._output_lock(output),
        pytest.raises(ValueError, match="another writer"),
        acquire._output_lock(output),
    ):
        pytest.fail("second writer entered")


def test_archive_budget_stops_after_newly_frozen_work(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "ROOT", tmp_path)
    work = {
        "work_id": "2608.31046",
        "versions": ["v1", "v2"],
        "split": "train",
        "category_query": "cat:cs.CL",
        "license_status": "not_reported_by_atom",
        "license_uri": [],
    }
    other = {**work, "work_id": "2609.18366"}
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    config = {
        "catalog_manifest": {"sha256": "catalog"},
        "max_archive_bytes_total": 5,
        "request_interval_seconds": 3.2,
        "content_use": "local_research_only_no_redistribution",
        "user_agent": "test@example.org",
    }
    monkeypatch.setattr(
        acquire,
        "_config",
        lambda _path: (config, {"selected_works": [work, other]}),
    )
    output = tmp_path / "cohort"
    visits = []

    def fake_cached(directory, current):
        visits.append(current["work_id"])
        if (directory / "inventory").is_dir():
            return (
                {"archive_bytes": 6, "license_status": work["license_status"]},
                {},
            )
        return None

    def fake_fetch(_request, inventory, **_kwargs):
        inventory.mkdir()

    monkeypatch.setattr(acquire, "_existing_inventory", fake_cached)
    monkeypatch.setattr(acquire, "fetch_paper_workflow", fake_fetch)
    result = acquire.fetch(config_path, output, sleep=lambda _seconds: None)
    assert result["frozen_works"] == 1
    assert result["archive_bytes"] == 6
    assert result["status"] == "archive_budget_exceeded"
    assert result["errors"][0]["status"] == "budget_stop"
    assert visits == [work["work_id"], work["work_id"]]


def test_transport_timeout_is_recorded_without_source_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "ROOT", tmp_path)
    work = {
        "work_id": "2608.31046",
        "versions": ["v1", "v2"],
        "split": "train",
        "category_query": "cat:cs.CL",
        "license_status": "not_reported_by_atom",
        "license_uri": [],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    config = {
        "catalog_manifest": {"sha256": "catalog"},
        "max_archive_bytes_total": 100,
        "request_interval_seconds": 3.2,
        "content_use": "local_research_only_no_redistribution",
        "user_agent": "test@example.org",
    }
    monkeypatch.setattr(
        acquire, "_config", lambda _path: (config, {"selected_works": [work]})
    )
    monkeypatch.setattr(acquire, "_existing_inventory", lambda *_a: None)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["curl"], 90)

    monkeypatch.setattr(acquire, "fetch_paper_workflow", timeout)
    result = acquire.fetch(config_path, tmp_path / "cohort", sleep=lambda _s: None)
    assert result["status"] == "transport_blocked"
    assert result["frozen_works"] == 0
    assert result["errors"][0]["status"] == "transport_failure"


def test_probe_pins_source_and_replays_four_worker_result(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "ROOT", tmp_path)
    monkeypatch.setattr(acquire, "ProcessPoolExecutor", ThreadPoolExecutor)
    work = {
        "work_id": "2608.31046",
        "versions": ["v1", "v2"],
        "split": "train",
        "category_query": "cat:cs.CL",
        "license_status": "not_reported_by_atom",
        "license_uri": [],
    }
    config = {
        "schema": acquire.SCHEMA + ".config",
        "content_use": "local_research_only_no_redistribution",
        "request_interval_seconds": 3.2,
        "workers": 4,
        "work_limit": 20,
        "max_archive_bytes_total": 1000,
        "user_agent": "test@example.org",
        "catalog_manifest": _write(
            tmp_path / "catalog.json",
            {
                "schema": "longworld.p105-paper-catalog.v1.result",
                "status": "complete",
                "content_use": "local_research_only_no_redistribution",
                "selected_count": 1,
                "selected_works": [work],
            },
        ),
        "qa_template": _write(
            tmp_path / "template.json",
            {"schema": "longworld.p96-paper-reference-qa.v1"},
        ),
        "prior_candidate_index": _write(tmp_path / "prior.json", {}),
    }
    for pin in (
        config["catalog_manifest"],
        config["qa_template"],
        config["prior_candidate_index"],
    ):
        pin["path"] = str(Path(pin["path"]).relative_to(tmp_path))
    config_path = tmp_path / "config.json"
    _write(config_path, config)
    output = tmp_path / "sources"
    output.mkdir()
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema": acquire.SCHEMA + ".fetch-result",
                "catalog_manifest_sha256": config["catalog_manifest"]["sha256"],
                "frozen_works": 1,
            }
        )
    )
    entry = {
        "family_id": "p105-arxiv-2608.31046",
        "split": "train",
        "inventory": {"path": "inventory.json", "sha256": "abc"},
    }
    monkeypatch.setattr(acquire, "_existing_inventory", lambda *_a: ({}, entry))
    monkeypatch.setattr(
        acquire,
        "_verify_fetch_locked",
        lambda *_a: {"frozen_works": 1},
    )
    monkeypatch.setattr(
        acquire,
        "_probe",
        lambda _row: (
            {"status": "task_source_candidate", "cross_file_links": 2},
            {**entry, "family_id": "p104-arxiv-2608.31046"},
        ),
    )
    result = acquire.probe(config_path, output)
    assert (result["source_shape_works"], result["raw_cross_file_links"]) == (1, 2)
    assert result["qa_config_sha256"] == acquire._sha(output / "qa_config.json")
    assert (
        json.loads((output / "source_config.json").read_text())["families"][0][
            "family_id"
        ]
        == "p105-arxiv-2608.31046"
    )
    acquire.probe(config_path, output, verify_only=True)
    (output / "qa_config.json").write_text("{}")
    with pytest.raises(ValueError, match="replay drift"):
        acquire.probe(config_path, output, verify_only=True)
