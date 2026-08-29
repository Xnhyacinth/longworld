from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from scripts.fetch_ietf_workflow import HttpResponse, fetch_ietf_workflow


def _request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.ietf-fetch-request.v1",
                "user_agent": "LongWorld/0.2 standards@example.org",
                "authorization": {
                    "record_id": "ietf-test-1",
                    "scope": "public IETF standards",
                    "basis": "public standards research",
                    "reviewed_at": "2026-08-29T00:00:00Z",
                    "allowed_actions": [
                        "fetch_datatracker_document",
                        "fetch_datatracker_relation",
                        "fetch_draft_revision",
                        "fetch_rfc",
                    ],
                },
                "drafts": [{"name": "draft-ietf-demo", "revisions": ["00", "01"]}],
                "rfc_numbers": [9999],
                "approved_public_test_vector_sha256": [],
                "requests_per_second": 2.0,
                "max_retries": 1,
            }
        )
    )
    return path


def test_fetches_only_pinned_official_endpoints_and_writes_disabled_inventory(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        seen.append(url)
        body = (
            json.dumps(
                {
                    "name": "draft-ietf-demo",
                    "rev": "01",
                    "rfc": "RFC 9999",
                    "time": "2024-02-03T12:00:00Z",
                }
            ).encode()
            if "/document/" in url
            else json.dumps({"meta": {"total_count": 1}, "objects": []}).encode()
            if "datatracker" in url
            else f"official body {url.rsplit('/', 1)[-1]}".encode()
        )
        return HttpResponse(
            body=body,
            status=200,
            final_url=url,
            content_type="application/json" if "datatracker" in url else "text/plain",
        )

    sleeps: list[float] = []
    observed_times = iter([f"2026-08-29T01:00:0{index}Z" for index in range(1, 7)])
    output = fetch_ietf_workflow(
        _request(tmp_path),
        tmp_path / "out",
        http_get=get,
        sleep=sleeps.append,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed_times.__next__,
    )
    payload = json.loads(output.read_text())
    assert payload["generation_integration"] == "disabled"
    assert payload["production_eligible"] is False
    assert payload["n_retrievals"] == 5
    assert seen == [
        "https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-demo/",
        "https://datatracker.ietf.org/api/v1/doc/relateddocument/?source__name=draft-ietf-demo&limit=100",
        "https://www.ietf.org/archive/id/draft-ietf-demo-00.txt",
        "https://www.ietf.org/archive/id/draft-ietf-demo-01.txt",
        "https://www.rfc-editor.org/rfc/rfc9999.txt",
    ]
    assert all(
        item["final_url"] == item["requested_url"]
        for item in payload["fetch_receipt"]["retrievals"]
    )
    assert [item["observed_at"] for item in payload["fetch_receipt"]["retrievals"]] == [
        f"2026-08-29T01:00:0{index}Z" for index in range(1, 6)
    ]
    assert payload["fetch_receipt"]["started_at"] == "2026-08-29T01:00:00Z"
    assert payload["fetch_receipt"]["completed_at"] == "2026-08-29T01:00:06Z"


def test_retries_bounded_transient_failure(tmp_path: Path) -> None:
    calls = 0

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return HttpResponse(b"retry", 503, url, "text/plain")
        body = b"{}" if "datatracker" in url else b"source"
        return HttpResponse(
            body, 200, url, "application/json" if "datatracker" in url else "text/plain"
        )

    output = fetch_ietf_workflow(
        _request(tmp_path),
        tmp_path / "out",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
    )
    assert json.loads(output.read_text())["n_retrievals"] == 5
    assert calls == 6


def test_rejects_redirect_or_non_official_request_identity(tmp_path: Path) -> None:
    request = json.loads(_request(tmp_path).read_text())
    request["drafts"][0]["name"] = "../escape"
    _request(tmp_path).write_text(json.dumps(request))
    with pytest.raises(ProvenanceError, match="draft name"):
        fetch_ietf_workflow(
            tmp_path / "request.json",
            tmp_path / "out",
            http_get=lambda *_args: HttpResponse(
                b"x", 200, "https://evil.example/x", "text/plain"
            ),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )


def test_rejects_nonconsecutive_draft_revisions(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text())
    request["drafts"][0]["revisions"] = ["00", "02"]
    request_path.write_text(json.dumps(request))
    with pytest.raises(ProvenanceError, match="revisions"):
        fetch_ietf_workflow(
            request_path,
            tmp_path / "out",
            http_get=lambda *_args: HttpResponse(
                b"x", 200, "https://www.ietf.org/x", "text/plain"
            ),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )
