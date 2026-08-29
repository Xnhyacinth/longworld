from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from scripts.fetch_clinical_workflow import (
    CLINICAL_TRIAL_API_ROOT,
    FDA_APPLICATION_API_URL,
    FDA_LABEL_API_URL,
    MAX_RESPONSE_BYTES,
    HttpResponse,
    fetch_clinical_workflow,
)

NCT_ID = "NCT04280705"
APPLICATION_NUMBER = "NDA214787"


def _request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "longworld.clinical-fetch-request.v1",
                "user_agent": "LongWorld/0.2 clinical@example.org",
                "authorization": {
                    "record_id": "clinical-public-test-1",
                    "scope": "bounded public ClinicalTrials.gov and openFDA export",
                    "basis": "public-source research authorization",
                    "reviewed_at": "2026-08-29T00:00:00Z",
                    "allowed_actions": [
                        "fetch_clinical_trial",
                        "fetch_fda_application",
                        "fetch_fda_label",
                    ],
                },
                "nct_id": NCT_ID,
                "fda_application_number": APPLICATION_NUMBER,
                "max_retries": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _trial_body(nct_id: str = NCT_ID) -> bytes:
    return json.dumps(
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "organization": {"fullName": "NIAID", "class": "NIH"},
                    "briefTitle": "Adaptive COVID-19 Treatment Trial",
                    "officialTitle": "A randomized controlled trial",
                },
                "statusModule": {
                    "statusVerifiedDate": "2020-04",
                    "overallStatus": "COMPLETED",
                    "completionDateStruct": {"date": "2020-05-21", "type": "ACTUAL"},
                    "lastUpdatePostDateStruct": {
                        "date": "2022-03-14",
                        "type": "ACTUAL",
                    },
                },
                "outcomesModule": {
                    "primaryOutcomes": [
                        {
                            "measure": "Time to recovery",
                            "description": "Day of discharge or continued hospitalization.",
                            "timeFrame": "Day 1 through Day 29",
                        }
                    ],
                    "secondaryOutcomes": [],
                },
                "contactsLocationsModule": {
                    "centralContacts": [
                        {"name": "A person", "email": "person@example.org"}
                    ]
                },
            },
            "hasResults": True,
        }
    ).encode()


def _application_body(application_number: str = APPLICATION_NUMBER) -> bytes:
    return json.dumps(
        {
            "meta": {"results": {"skip": 0, "limit": 1, "total": 1}},
            "results": [
                {
                    "application_number": application_number,
                    "sponsor_name": "GILEAD SCIENCES INC",
                    "products": [{"brand_name": "VEKLURY"}],
                    "submissions": [
                        {
                            "submission_type": "ORIG",
                            "submission_number": "1",
                            "submission_status": "AP",
                            "submission_status_date": "20201022",
                            "review_priority": "PRIORITY",
                        }
                    ],
                }
            ],
        }
    ).encode()


def _label_body(
    application_number: str = APPLICATION_NUMBER,
    *,
    clinical_studies: str = (
        "Trial NCT04280705 evaluated VEKLURY in hospitalized adult subjects."
    ),
) -> bytes:
    return json.dumps(
        {
            "meta": {"results": {"skip": 0, "limit": 1, "total": 1}},
            "results": [
                {
                    "id": "label-version-id",
                    "set_id": "label-set-id",
                    "effective_time": "20250825",
                    "version": "33",
                    "indications_and_usage": ["VEKLURY is indicated for COVID-19."],
                    "clinical_studies": [clinical_studies],
                    "openfda": {"application_number": [application_number]},
                }
            ],
        }
    ).encode()


def _response_for(url: str) -> bytes:
    if url.startswith(CLINICAL_TRIAL_API_ROOT):
        return _trial_body()
    if url.startswith(FDA_APPLICATION_API_URL):
        return _application_body()
    return _label_body()


def test_fetches_exact_official_queries_and_writes_privacy_minimized_receipts(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        seen.append(url)
        return HttpResponse(_response_for(url), 200, url, "application/json")

    observed = iter(
        [
            "2026-08-29T01:00:01Z",
            "2026-08-29T01:00:02Z",
            "2026-08-29T01:00:03Z",
            "2026-08-29T01:00:04Z",
        ]
    )
    output = fetch_clinical_workflow(
        _request(tmp_path),
        tmp_path / "out",
        http_get=get,
        sleep=lambda _seconds: None,
        generated_at="2026-08-29T01:00:00Z",
        clock=observed.__next__,
    )
    payload = json.loads(output.read_text())
    assert seen == [
        f"{CLINICAL_TRIAL_API_ROOT}/{NCT_ID}",
        f"{FDA_APPLICATION_API_URL}?search=application_number%3A{APPLICATION_NUMBER}&limit=1",
        f"{FDA_LABEL_API_URL}?search=openfda.application_number%3A%22{APPLICATION_NUMBER}%22&limit=1",
    ]
    assert payload["data_stage"] == "source_inventory"
    assert payload["generation_integration"] == "disabled"
    assert payload["semantic_facts_train_ready"] is False
    assert payload["production_eligible"] is False
    assert payload["snapshot_semantics"] == "current_state_observed_at_fetch"
    assert payload["historical_snapshot_claims"] is False
    assert payload["n_retrievals"] == 3
    retrievals = payload["fetch_receipt"]["retrievals"]
    assert all(
        item["redirect_chain"] == []
        and item["final_url"] == item["requested_url"]
        and len(item["response_sha256"]) == 64
        and len(item["projection_sha256"]) == 64
        and item["status"] == 200
        and item["content_type"] == "application/json"
        and item["source_materialization"] == "privacy_minimized_projection"
        for item in retrievals
    )
    trial_receipt = next(
        item for item in retrievals if item["kind"] == "clinical_trial"
    )
    trial_projection = json.loads(
        (output.parent / trial_receipt["retrieval_file"]).read_text()
    )
    assert "contactsLocationsModule" not in json.dumps(trial_projection)
    assert "person@example.org" not in json.dumps(trial_projection)
    assert trial_receipt["response_sha256"] == hashlib.sha256(_trial_body()).hexdigest()


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(
            _trial_body(), 200, "https://evil.example/redirect", "application/json"
        ),
        HttpResponse(_trial_body(), 200, "", "text/html"),
        HttpResponse(b"x" * (MAX_RESPONSE_BYTES + 1), 200, "", "application/json"),
    ],
)
def test_rejects_redirect_wrong_type_or_oversized_response(
    tmp_path: Path, response: HttpResponse
) -> None:
    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return HttpResponse(
            response.body,
            response.status,
            response.final_url or url,
            response.content_type,
        )

    with pytest.raises(ProvenanceError, match="metadata"):
        fetch_clinical_workflow(
            _request(tmp_path),
            tmp_path / "out",
            http_get=get,
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )


def test_rejects_credentials_and_wrong_requested_identity(tmp_path: Path) -> None:
    request_path = _request(tmp_path)
    request = json.loads(request_path.read_text())
    request["api_key"] = "not-allowed"
    request_path.write_text(json.dumps(request))
    with pytest.raises(ProvenanceError, match="schema"):
        fetch_clinical_workflow(
            request_path,
            tmp_path / "out",
            http_get=lambda *_args: HttpResponse(
                _trial_body(), 200, "", "application/json"
            ),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )

    second = tmp_path / "second"
    second.mkdir()
    request_path = _request(second)
    request = json.loads(request_path.read_text())
    request["nct_id"] = "../NCT04280705"
    request_path.write_text(json.dumps(request))
    with pytest.raises(ProvenanceError, match="NCT"):
        fetch_clinical_workflow(
            request_path,
            second / "out",
            http_get=lambda *_args: HttpResponse(
                _trial_body(), 200, "", "application/json"
            ),
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )


def test_rejects_response_identity_mismatch_before_writing(tmp_path: Path) -> None:
    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        body = (
            _trial_body("NCT00000001")
            if url.startswith(CLINICAL_TRIAL_API_ROOT)
            else _response_for(url)
        )
        return HttpResponse(body, 200, url, "application/json")

    with pytest.raises(ProvenanceError, match="identity"):
        fetch_clinical_workflow(
            _request(tmp_path),
            tmp_path / "out",
            http_get=get,
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )


@pytest.mark.parametrize(
    "unsafe_value",
    ["Contact person@example.org", "ghp_abcdefghijklmnopqrstuvwxyz"],
)
def test_rejects_pii_or_secret_in_selected_source_fields(
    tmp_path: Path, unsafe_value: str
) -> None:
    trial = json.loads(_trial_body())
    trial["protocolSection"]["identificationModule"]["officialTitle"] = unsafe_value

    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        body = (
            json.dumps(trial).encode()
            if url.startswith(CLINICAL_TRIAL_API_ROOT)
            else _response_for(url)
        )
        return HttpResponse(body, 200, url, "application/json")

    with pytest.raises(ProvenanceError, match="public scanner"):
        fetch_clinical_workflow(
            _request(tmp_path),
            tmp_path / "out",
            http_get=get,
            sleep=lambda _seconds: None,
            generated_at="2026-08-29T01:00:00Z",
        )
