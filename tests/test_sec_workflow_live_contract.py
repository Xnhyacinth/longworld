from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation
from longworld.core.filingworkflow import (
    build_sec_filing_manifest,
    load_sec_filing_manifest,
)
from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_sec_workflow import fetch_sec_workflow


def _request() -> dict:
    return {
        "schema_version": "longworld.sec-fetch-request.v1",
        "user_agent": "Longworld Research research@example-research.org",
        "authorization": {
            "record_id": "SEC-PUBLIC-TEST-001",
            "scope": "CIK 0000000001 public EDGAR filings",
            "basis": "public SEC filing research export",
            "reviewed_at": "2026-08-25T09:00:00Z",
        },
        "allowed_ciks": ["0000000001"],
        "forms": ["10-K", "10-K/A"],
        "max_filings_per_cik": 2,
        "requests_per_second": 5,
        "max_retries": 2,
    }


def _filing(
    accession: str,
    form: str,
    filed: str,
    report_date: str,
    *,
    cik: str = "0000000001",
    primary_document: str | None = None,
) -> bytes:
    primary_document = primary_document or (
        "filing-amendment.htm" if form.endswith("/A") else "filing.htm"
    )
    return (
        f"<SEC-DOCUMENT>{accession}.txt\n"
        f"ACCESSION NUMBER:\t\t{accession}\n"
        f"CONFORMED SUBMISSION TYPE:\t{form}\n"
        f"CONFORMED PERIOD OF REPORT:\t{report_date.replace('-', '')}\n"
        f"FILED AS OF DATE:\t\t{filed.replace('-', '')}\n"
        f"CENTRAL INDEX KEY:\t\t\t{cik}\n"
        f"<DOCUMENT>\n<TYPE>{form}\n<SEQUENCE>1\n"
        f"<FILENAME>{primary_document}\n"
        "Revenue for the reported period was USD 42 million.\n"
        "</DOCUMENT>\n"
    ).encode()


def test_fetch_builds_hash_bound_input_and_real_amendment_relation(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    base = "https://www.sec.gov/Archives/edgar/data/1"
    bodies = {
        "https://data.sec.gov/submissions/CIK0000000001.json": json.dumps(
            {
                "cik": "1",
                "filings": {
                    "recent": {
                        "accessionNumber": [
                            "0000000001-26-000002",
                            "0000000001-26-000001",
                        ],
                        "filingDate": ["2026-03-01", "2026-02-20"],
                        "reportDate": ["2025-12-31", "2025-12-31"],
                        "form": ["10-K/A", "10-K"],
                        "primaryDocument": [
                            "filing-amendment.htm",
                            "filing.htm",
                        ],
                    }
                },
            }
        ).encode(),
        f"{base}/000000000126000002/0000000001-26-000002.txt": _filing(
            "0000000001-26-000002", "10-K/A", "2026-03-01", "2025-12-31"
        ),
        f"{base}/000000000126000001/0000000001-26-000001.txt": _filing(
            "0000000001-26-000001", "10-K", "2026-02-20", "2025-12-31"
        ),
    }
    calls: list[tuple[str, str]] = []

    def get(url: str, headers: dict[str, str], timeout: float):
        del timeout
        calls.append((url, headers["User-Agent"]))
        return bodies[url], {}

    input_path = fetch_sec_workflow(
        request_path,
        tmp_path / "download",
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-25T10:00:00Z",
    )
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    assert payload["source_status"] == "public_sec_download"
    assert len(payload["filings"]) == 2
    assert all(agent == _request()["user_agent"] for _, agent in calls)
    for filing in payload["filings"]:
        source = input_path.parent / filing["source_file"]
        assert (
            filing["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        )
        assert {fact["field"] for fact in filing["derived_facts"]} >= {
            "accession",
            "form",
            "filing_date",
            "report_date",
        }

    manifest = build_sec_filing_manifest(
        payload,
        input_path.parent,
        generated_at="2026-08-25T10:01:00Z",
    )
    assert manifest["production_eligible"] is False
    assert manifest["generation_integration"] == "disabled"
    assert manifest["fetch_receipt"]["requests_per_second"] == 5.0
    assert manifest["filing_relations"] == [
        {
            "relation_id": ("sec:0000000001-26-000002:amends:sec:0000000001-26-000001"),
            "relation_type": "amends_report",
            "from_record_id": "sec:0000000001-26-000002",
            "to_record_id": "sec:0000000001-26-000001",
            "shared_report_date": "2025-12-31",
            "derivation_rule": ("unique_prior_same_cik_base_form_and_report_date"),
            "evidence": [
                {
                    "record_id": "sec:0000000001-26-000002",
                    "fact_ids": ["form", "report_date"],
                },
                {
                    "record_id": "sec:0000000001-26-000001",
                    "fact_ids": ["form", "report_date"],
                },
            ],
        }
    ]


@pytest.mark.parametrize(
    "user_agent",
    ("python-urllib", "Longworld Research research@example.com"),
)
def test_fetch_requires_declared_contact_user_agent(
    tmp_path: Path, user_agent: str
) -> None:
    request = _request()
    request["user_agent"] = user_agent
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="User-Agent"):
        fetch_sec_workflow(request_path, tmp_path / "download")


def test_fetch_retries_transient_http_status(tmp_path: Path) -> None:
    request = _request()
    request["forms"] = ["10-K"]
    request["max_filings_per_cik"] = 1
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    submission_url = "https://data.sec.gov/submissions/CIK0000000001.json"
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/1/"
        "000000000126000001/0000000001-26-000001.txt"
    )
    bodies = {
        submission_url: json.dumps(
            {
                "cik": "1",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000000001-26-000001"],
                        "filingDate": ["2026-02-20"],
                        "reportDate": ["2025-12-31"],
                        "form": ["10-K"],
                        "primaryDocument": ["filing.htm"],
                    }
                },
            }
        ).encode(),
        filing_url: _filing("0000000001-26-000001", "10-K", "2026-02-20", "2025-12-31"),
    }
    calls = 0
    sleeps: list[float] = []

    def get(url: str, headers: dict[str, str], timeout: float):
        nonlocal calls
        del headers, timeout
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                url, 429, "limited", {"Retry-After": "0"}, None
            )
        return bodies[url], {}

    fetch_sec_workflow(
        request_path,
        tmp_path / "download",
        http_get=get,
        sleep=sleeps.append,
        generated_at="2026-08-25T10:00:00Z",
    )

    assert calls == 3
    assert 0.0 in sleeps


def test_fetch_revalidates_online_instead_of_trusting_writable_cache(
    tmp_path: Path,
) -> None:
    request = _request()
    request["forms"] = ["10-K"]
    request["max_filings_per_cik"] = 1
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    submission_url = "https://data.sec.gov/submissions/CIK0000000001.json"
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/1/"
        "000000000126000001/0000000001-26-000001.txt"
    )
    bodies = {
        submission_url: json.dumps(
            {
                "cik": "1",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000000001-26-000001"],
                        "filingDate": ["2026-02-20"],
                        "reportDate": ["2025-12-31"],
                        "form": ["10-K"],
                        "primaryDocument": ["filing.htm"],
                    }
                },
            }
        ).encode(),
        filing_url: _filing("0000000001-26-000001", "10-K", "2026-02-20", "2025-12-31"),
    }
    calls = 0

    def get(url: str, headers: dict[str, str], timeout: float):
        nonlocal calls
        del headers, timeout
        calls += 1
        return bodies[url], {}

    out = tmp_path / "download"
    fetch_sec_workflow(
        request_path,
        out,
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-25T10:00:00Z",
    )
    assert calls == 2
    fetch_sec_workflow(
        request_path,
        out,
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-25T10:05:00Z",
    )
    assert calls == 4

    source = out / "0000000001-26-000001.txt"
    source.write_bytes(source.read_bytes() + b"tampered")
    fetch_sec_workflow(
        request_path,
        out,
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-25T10:10:00Z",
    )
    assert calls == 6


def test_fetch_rejects_complete_submission_cik_mismatch(tmp_path: Path) -> None:
    request = _request()
    request["forms"] = ["10-K"]
    request["max_filings_per_cik"] = 1
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    submissions = json.dumps(
        {
            "cik": "1",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000002-26-000001"],
                    "filingDate": ["2026-02-20"],
                    "reportDate": ["2025-12-31"],
                    "form": ["10-K"],
                    "primaryDocument": ["filing.htm"],
                }
            },
        }
    ).encode()

    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/1/"
        "000000000226000001/0000000002-26-000001.txt"
    )
    responses = {
        "https://data.sec.gov/submissions/CIK0000000001.json": submissions,
        filing_url: _filing(
            "0000000002-26-000001",
            "10-K",
            "2026-02-20",
            "2025-12-31",
            cik="0000000002",
        ),
    }

    with pytest.raises(ProvenanceError, match="bind cik"):
        fetch_sec_workflow(
            request_path,
            tmp_path / "download",
            http_get=lambda url, *_: (responses[url], {}),
            sleep=lambda _: None,
            generated_at="2026-08-25T10:00:00Z",
        )


def test_fetch_rejects_unsafe_accession_before_source_download(tmp_path: Path) -> None:
    request = _request()
    request["forms"] = ["10-K"]
    request["max_filings_per_cik"] = 1
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    submissions = json.dumps(
        {
            "cik": "1",
            "filings": {
                "recent": {
                    "accessionNumber": ["../../outside"],
                    "filingDate": ["2026-02-20"],
                    "reportDate": ["2025-12-31"],
                    "form": ["10-K"],
                    "primaryDocument": ["filing.htm"],
                }
            },
        }
    ).encode()
    calls = 0

    def get(url: str, headers: dict[str, str], timeout: float):
        nonlocal calls
        del url, headers, timeout
        calls += 1
        return submissions, {}

    with pytest.raises(ProvenanceError, match="accession"):
        fetch_sec_workflow(
            request_path,
            tmp_path / "download",
            http_get=get,
            sleep=lambda _: None,
            generated_at="2026-08-25T10:00:00Z",
        )

    assert calls == 1
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("rate", (0, 10.1))
def test_fetch_enforces_sec_fair_access_ceiling(tmp_path: Path, rate: float) -> None:
    request = _request()
    request["requests_per_second"] = rate
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="requests_per_second"):
        fetch_sec_workflow(request_path, tmp_path / "download")


def test_manifest_audit_recomputes_filing_relations(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    base = "https://www.sec.gov/Archives/edgar/data/1"
    bodies = {
        "https://data.sec.gov/submissions/CIK0000000001.json": json.dumps(
            {
                "cik": "1",
                "filings": {
                    "recent": {
                        "accessionNumber": [
                            "0000000001-26-000002",
                            "0000000001-26-000001",
                        ],
                        "filingDate": ["2026-03-01", "2026-02-20"],
                        "reportDate": ["2025-12-31", "2025-12-31"],
                        "form": ["10-K/A", "10-K"],
                        "primaryDocument": [
                            "filing-amendment.htm",
                            "filing.htm",
                        ],
                    }
                },
            }
        ).encode(),
        f"{base}/000000000126000002/0000000001-26-000002.txt": _filing(
            "0000000001-26-000002", "10-K/A", "2026-03-01", "2025-12-31"
        ),
        f"{base}/000000000126000001/0000000001-26-000001.txt": _filing(
            "0000000001-26-000001", "10-K", "2026-02-20", "2025-12-31"
        ),
    }

    def get(url: str, headers: dict[str, str], timeout: float):
        del headers, timeout
        return bodies[url], {}

    input_path = fetch_sec_workflow(
        request_path,
        tmp_path / "download",
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-25T10:00:00Z",
    )
    manifest = build_sec_filing_manifest(
        json.loads(input_path.read_text(encoding="utf-8")),
        input_path.parent,
        generated_at="2026-08-25T10:01:00Z",
    )
    manifest["filing_relations"] = []
    signed = attach_attestation(
        manifest, b"relation-audit-test-key-32-bytes", purpose="source_manifest"
    )
    path = tmp_path / "invalid-relations.json"
    path.write_text(json.dumps(signed), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="filing relations"):
        load_sec_filing_manifest(
            path, attestation_key=b"relation-audit-test-key-32-bytes"
        )


def test_explicit_filings_import_local_archives_without_http(tmp_path: Path) -> None:
    request = _request()
    request["forms"] = ["10-K"]
    request["max_filings_per_cik"] = 1
    request["explicit_filings"] = [
        {
            "cik": "0000000001",
            "accession": "0000000001-26-000001",
            "form": "10-K",
            "filing_date": "2026-02-20",
            "report_date": "2025-12-31",
            "primary_document": "filing.htm",
            "source_file": "0000000001-26-000001.txt",
        }
    ]
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "download"
    out.mkdir()
    (out / "0000000001-26-000001.txt").write_bytes(
        _filing("0000000001-26-000001", "10-K", "2026-02-20", "2025-12-31")
    )

    def forbidden(url: str, headers: dict[str, str], timeout: float):
        raise AssertionError(f"local archive import must not HTTP {url}")

    input_path = fetch_sec_workflow(
        request_path,
        out,
        http_get=forbidden,
        sleep=lambda _: None,
        generated_at="2026-08-26T12:00:00Z",
    )
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    assert payload["source_status"] == "authorized_download"
    assert payload["filings"][0]["accession"] == "0000000001-26-000001"
    assert payload["filings"][0]["cik"] == "0000000001"


def test_explicit_filings_fail_closed_when_local_archive_is_missing(
    tmp_path: Path,
) -> None:
    request = _request()
    request["forms"] = ["10-K"]
    request["max_filings_per_cik"] = 1
    request["explicit_filings"] = [
        {
            "cik": "0000000001",
            "accession": "0000000001-26-000001",
            "form": "10-K",
            "filing_date": "2026-02-20",
            "report_date": "2025-12-31",
            "primary_document": "filing.htm",
            "source_file": "0000000001-26-000001.txt",
        }
    ]
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ProvenanceError, match="local archive is missing"):
        fetch_sec_workflow(
            request_path,
            tmp_path / "download",
            http_get=lambda *_: (_ for _ in ()).throw(AssertionError("no HTTP")),
            sleep=lambda _: None,
        )
