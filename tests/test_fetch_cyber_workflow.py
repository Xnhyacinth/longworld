from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from scripts.fetch_cyber_workflow import (
    CISA_KEV_URL,
    MAX_RESPONSE_BYTES,
    NVD_CVE_API_URL,
    HttpResponse,
    fetch_cyber_workflow,
)


def _request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.cyber-fetch-request.v1",
                "user_agent": "LongWorld/0.2 cyber@example.org",
                "authorization": {
                    "record_id": "cyber-public-test-1",
                    "scope": "bounded public NVD and CISA KEV source export",
                    "basis": "public-source research authorization",
                    "reviewed_at": "2026-08-29T00:00:00Z",
                    "allowed_actions": ["fetch_cisa_kev", "fetch_nvd_cve"],
                },
                "cve_ids": ["CVE-2021-44228"],
                "requests_per_second": 1.0,
                "max_retries": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _nvd_body(cve_id: str = "CVE-2021-44228") -> bytes:
    return json.dumps(
        {
            "format": "NVD_CVE",
            "version": "2.0",
            "totalResults": 1,
            "vulnerabilities": [
                {
                    "cve": {
                        "id": cve_id,
                        "sourceIdentifier": "security@apache.org",
                        "published": "2021-12-10T10:15:09.143",
                        "lastModified": "2025-10-27T17:29:54.680",
                        "vulnStatus": "Analyzed",
                        "descriptions": [
                            {"lang": "en", "value": "Apache Log4j2 JNDI vulnerability."}
                        ],
                        "metrics": {
                            "ssvc": [{"ssvcData": {"id": cve_id, "options": []}}]
                        },
                        "references": [{"url": "https://logging.apache.org/"}],
                    }
                }
            ],
        }
    ).encode()


def _kev_body(cve_id: str = "CVE-2021-44228") -> bytes:
    return json.dumps(
        {
            "title": "CISA Known Exploited Vulnerabilities Catalog",
            "catalogVersion": "2026.08.29",
            "dateReleased": "2026-08-29T10:00:00.0000Z",
            "count": 1,
            "vulnerabilities": [
                {
                    "cveID": cve_id,
                    "vendorProject": "Apache",
                    "product": "Log4j2",
                    "vulnerabilityName": "Apache Log4j2 Remote Code Execution Vulnerability",
                    "dateAdded": "2021-12-10",
                    "shortDescription": "Log4j2 contains a remote code execution vulnerability.",
                    "requiredAction": "Apply mitigations per vendor instructions.",
                    "dueDate": "2021-12-24",
                    "knownRansomwareCampaignUse": "Known",
                    "notes": "https://nvd.nist.gov/vuln/detail/CVE-2021-44228",
                    "cwes": ["CWE-20", "CWE-400", "CWE-502"],
                }
            ],
        }
    ).encode()


def test_fetches_pinned_official_endpoints_with_bounded_receipts(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        seen.append(url)
        body = _nvd_body() if url.startswith(NVD_CVE_API_URL) else _kev_body()
        return HttpResponse(body, 200, url, "application/json")

    observed = iter(
        [
            "2026-08-29T01:00:01Z",
            "2026-08-29T01:00:02Z",
            "2026-08-29T01:00:03Z",
        ]
    )
    output = fetch_cyber_workflow(
        _request(tmp_path),
        tmp_path / "out",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed.__next__,
    )
    payload = json.loads(output.read_text())
    assert seen == [
        f"{NVD_CVE_API_URL}?cveId=CVE-2021-44228",
        CISA_KEV_URL,
    ]
    assert payload["data_stage"] == "source_inventory"
    assert payload["generation_integration"] == "disabled"
    assert payload["semantic_facts_train_ready"] is False
    assert payload["production_eligible"] is False
    assert payload["snapshot_semantics"] == "current_state_observed_at_fetch"
    assert payload["historical_event_claims"] is False
    assert payload["n_retrievals"] == 2
    assert all(
        item["redirect_chain"] == []
        and item["final_url"] == item["requested_url"]
        and len(item["sha256"]) == 64
        and item["status"] == 200
        and item["content_type"] == "application/json"
        for item in payload["fetch_receipt"]["retrievals"]
    )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            HttpResponse(
                _nvd_body(), 200, "https://evil.example/redirect", "application/json"
            ),
            "metadata",
        ),
        (HttpResponse(_nvd_body(), 200, "", "text/html"), "metadata"),
        (
            HttpResponse(b"x" * (MAX_RESPONSE_BYTES + 1), 200, "", "application/json"),
            "metadata",
        ),
    ],
)
def test_rejects_redirect_wrong_type_or_oversized_response(
    tmp_path: Path, response: HttpResponse, message: str
) -> None:
    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return HttpResponse(
            response.body,
            response.status,
            response.final_url or url,
            response.content_type,
        )

    with pytest.raises(ProvenanceError, match=message):
        fetch_cyber_workflow(
            _request(tmp_path),
            tmp_path / "out",
            http_get=get,
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )


def test_rejects_credentials_and_invalid_requested_identity(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text())
    request["api_key"] = "not-allowed"
    request_path.write_text(json.dumps(request))
    with pytest.raises(ProvenanceError, match="schema"):
        fetch_cyber_workflow(
            request_path,
            tmp_path / "out",
            http_get=lambda *_args: HttpResponse(
                _nvd_body(), 200, NVD_CVE_API_URL, "application/json"
            ),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )

    second = tmp_path / "second"
    second.mkdir()
    request_path = _request(second)
    request = json.loads(request_path.read_text())
    request["cve_ids"] = ["../CVE-2021-44228"]
    request_path.write_text(json.dumps(request))
    with pytest.raises(ProvenanceError, match="CVE"):
        fetch_cyber_workflow(
            request_path,
            second / "out",
            http_get=lambda *_args: HttpResponse(
                _nvd_body(), 200, NVD_CVE_API_URL, "application/json"
            ),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )
