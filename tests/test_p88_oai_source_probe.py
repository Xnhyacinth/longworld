"""Offline OAI metadata and source-capacity checks."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from scripts import probe_p88_oai_source as probe


def _config(tmp_path: Path) -> Path:
    value = {
        "schema": probe.SCHEMA,
        "oai_url": "https://oaipmh.arxiv.org/oai",
        "oai_set": "cs:cs:CL",
        "metadata_prefix": "arXivRaw",
        "user_agent": "LongWorld/0.1 tests@example.org",
        "minimum_request_interval_seconds": 3.2,
        "maximum_network_requests": 2,
        "source_archive_limit": 1,
        "exclude_work_ids": ["2401.00001"],
        "content_use": "local_research_only_no_redistribution",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    return path


def _feed() -> bytes:
    return b"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
    <ListRecords>
      <record><header><datestamp>2026-01-01</datestamp></header><metadata>
        <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
          <id>2401.00001</id><version version="v1"/><version version="v2"/>
          <title>Excluded work</title><license>https://creativecommons.org/licenses/by/4.0/</license>
        </arXivRaw></metadata></record>
      <record><header><datestamp>2026-01-02</datestamp></header><metadata>
        <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
          <id>2401.00002</id><version version="v1"/><version version="v2"/>
          <title>Eligible work</title><categories>cs.CL</categories>
          <license>https://creativecommons.org/licenses/by/4.0/</license>
        </arXivRaw></metadata></record>
    </ListRecords></OAI-PMH>"""


def _archive() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        body = (
            "This paper describes a reproducible scientific method with multiple "
            "measurements and carefully compared experimental observations. " * 12
        ).encode()
        member = tarfile.TarInfo("section.tex")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    return raw.getvalue()


def test_two_request_limit_and_source_provenance(tmp_path: Path) -> None:
    clock = [1.0]
    calls = []

    def metadata(url: str, _agent: str) -> bytes:
        calls.append(("metadata", clock[0], url))
        return _feed()

    def source(url: str, _agent: str) -> bytes:
        calls.append(("source", clock[0], url))
        return _archive()

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    receipt = probe.probe(
        _config(tmp_path),
        tmp_path / "output",
        metadata_get=metadata,
        source_get=source,
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    assert [row[0] for row in calls] == ["metadata", "source"]
    assert calls[1][1] - calls[0][1] >= 3.2
    assert receipt["metadata_records"] == 2
    assert receipt["selected_work"]["work_id"] == "2401.00002"
    assert receipt["selected_work"]["versions"] == ["v1", "v2"]
    assert receipt["source_capacity"]["readable_source_files"] == 1
    assert receipt["source_archive"]["sha256"] == probe._sha(_archive())
    assert receipt["train_ready"] is False


def test_metadata_failure_never_issues_source_request(tmp_path: Path) -> None:
    def unavailable(_url: str, _agent: str) -> bytes:
        raise OSError("fixture unavailable")

    receipt = probe.probe(
        _config(tmp_path),
        tmp_path / "failure",
        metadata_get=unavailable,
        source_get=lambda _url, _agent: (_ for _ in ()).throw(
            AssertionError("unexpected")
        ),
    )
    assert receipt["status"] == "metadata_failed"
    assert receipt["network_requests_sent"] == 1
    assert receipt["source_archive"] is None
    assert receipt["errors"][0]["error_type"] == "OSError"
