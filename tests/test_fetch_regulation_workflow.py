from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from scripts.fetch_regulation_workflow import (
    FEDERAL_REGISTER_DOCUMENT_URL,
    REGULATIONS_DOCKET_URL,
    HttpResponse,
    fetch_regulation_workflow,
)

DOCUMENT_NUMBERS = ["2023-00414", "2023-07036", "2024-09171"]


def _request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.regulation-fetch-request.v1",
                "user_agent": "LongWorld/0.2 regulation@example.org",
                "authorization": {
                    "record_id": "regulation-public-test-1",
                    "scope": "bounded public rulemaking metadata export",
                    "basis": "public-source research authorization",
                    "reviewed_at": "2026-08-29T00:00:00Z",
                    "allowed_actions": [
                        "fetch_federal_register_document",
                        "fetch_regulations_docket_metadata",
                    ],
                },
                "docket_id": "FTC-2023-0007",
                "federal_register_document_numbers": DOCUMENT_NUMBERS,
                "regulations_api_key_env": "REGULATIONS_GOV_API_KEY",
                "requests_per_second": 2.0,
                "max_retries": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _fr_body(number: str) -> bytes:
    fields = {
        "2023-00414": (
            "Non-Compete Clause Rule",
            "Proposed Rule",
            "The Commission proposes a rule and seeks comment.",
            "2023-01-19",
            "88 FR 3482",
        ),
        "2023-07036": (
            "Non-Compete Clause Rule; Extension of Comment Period",
            "Proposed Rule",
            "The Commission extends the public comment period.",
            "2023-04-06",
            "88 FR 20441",
        ),
        "2024-09171": (
            "Non-Compete Clause Rule",
            "Rule",
            "The Commission issues a final rule regarding non-compete clauses.",
            "2024-05-07",
            "89 FR 38342",
        ),
    }
    title, document_type, abstract, publication_date, citation = fields[number]
    return json.dumps(
        {
            "document_number": number,
            "title": title,
            "type": document_type,
            "abstract": abstract,
            "publication_date": publication_date,
            "html_url": f"https://www.federalregister.gov/d/{number}",
            "pdf_url": f"https://www.govinfo.gov/content/pkg/FR-{number}.pdf",
            "raw_text_url": f"https://www.govinfo.gov/content/pkg/FR-{number}.txt",
            "agencies": [
                {
                    "name": "Federal Trade Commission",
                    "slug": "federal-trade-commission",
                }
            ],
            "docket_ids": [],
            "regulation_id_numbers": ["3084-AB74"],
            "action": document_type,
            "dates": abstract,
            "citation": citation,
        }
    ).encode()


def _docket_body(docket_id: str = "FTC-2023-0007") -> bytes:
    return json.dumps(
        {
            "data": {
                "id": docket_id,
                "type": "dockets",
                "attributes": {
                    "agencyId": "FTC",
                    "docketType": "Rulemaking",
                    "title": "Non-Compete Clause Rule",
                    "rin": "3084-AB74",
                    "modifyDate": "2024-08-26T13:13:42Z",
                },
            }
        }
    ).encode()


def test_fetches_only_pinned_metadata_endpoints_without_comment_content(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def get(url: str, headers: dict[str, str], _timeout: float) -> HttpResponse:
        seen.append((url, headers))
        if url.startswith(FEDERAL_REGISTER_DOCUMENT_URL):
            body = _fr_body(url.rsplit("/", 1)[-1].removesuffix(".json"))
        else:
            body = _docket_body()
        return HttpResponse(
            body,
            200,
            url,
            "application/vnd.api+json"
            if url.startswith(REGULATIONS_DOCKET_URL)
            else "application/json",
        )

    observed = iter(
        [
            "2026-08-29T01:00:01Z",
            "2026-08-29T01:00:02Z",
            "2026-08-29T01:00:03Z",
            "2026-08-29T01:00:04Z",
            "2026-08-29T01:00:05Z",
        ]
    )
    output = fetch_regulation_workflow(
        _request(tmp_path),
        tmp_path / "out",
        api_key="test-key",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed.__next__,
    )
    payload = json.loads(output.read_text())
    assert [url for url, _headers in seen] == [
        *(
            f"{FEDERAL_REGISTER_DOCUMENT_URL}/{number}.json"
            for number in DOCUMENT_NUMBERS
        ),
        f"{REGULATIONS_DOCKET_URL}/FTC-2023-0007",
    ]
    assert all("comments" not in url for url, _headers in seen)
    assert seen[-1][1]["X-Api-Key"] == "test-key"
    assert all(
        "test-key" not in item["requested_url"]
        for item in payload["fetch_receipt"]["retrievals"]
    )
    assert payload["data_stage"] == "source_inventory"
    assert payload["generation_integration"] == "disabled"
    assert payload["semantic_facts_train_ready"] is False
    assert payload["production_eligible"] is False
    assert payload["historical_snapshot_claims"] is False
    assert payload["n_retrievals"] == 4
    assert payload["fetch_receipt"]["retrievals"][-1]["content_type"] == (
        "application/vnd.api+json"
    )


def test_rejects_redirect_wrong_type_and_missing_api_key(tmp_path: Path) -> None:
    def redirect(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return HttpResponse(
            b"{}", 200, "https://evil.example/redirect", "application/json"
        )

    with pytest.raises(ProvenanceError, match="metadata"):
        fetch_regulation_workflow(
            _request(tmp_path),
            tmp_path / "redirect",
            api_key="test-key",
            http_get=redirect,
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )

    with pytest.raises(ProvenanceError, match="API key"):
        fetch_regulation_workflow(
            _request(tmp_path),
            tmp_path / "missing-key",
            api_key="",
            http_get=redirect,
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )


def test_rejects_unpinned_identity_and_comment_fetch_actions(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    payload = json.loads(request_path.read_text())
    payload["docket_id"] = "../escape"
    request_path.write_text(json.dumps(payload))
    with pytest.raises(ProvenanceError, match="docket"):
        fetch_regulation_workflow(
            request_path,
            tmp_path / "bad-id",
            api_key="test-key",
            http_get=lambda *_args: HttpResponse(b"{}", 200, "", "application/json"),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )

    second = tmp_path / "second"
    second.mkdir()
    request_path = _request(second)
    payload = json.loads(request_path.read_text())
    payload["authorization"]["allowed_actions"].append("fetch_regulations_comments")
    request_path.write_text(json.dumps(payload))
    with pytest.raises(ProvenanceError, match="actions"):
        fetch_regulation_workflow(
            request_path,
            second / "bad-action",
            api_key="test-key",
            http_get=lambda *_args: HttpResponse(b"{}", 200, "", "application/json"),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )
