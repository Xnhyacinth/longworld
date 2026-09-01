from __future__ import annotations

import json
from pathlib import Path

import pytest

from longworld.core.filingworkflow import (
    MAX_ISSUER_GCS_DETAIL_BYTES,
    MAX_SEC_MANIFEST_BYTES,
    SEC_LONGITUDINAL_GCS_FILINGS,
    SEC_MANIFEST_JSON_FIXED_OVERHEAD_BYTES,
    _audit_exported_manifest,
    build_sec_filing_manifest,
)
from longworld.core.provenance import MAX_SOURCE_BYTES, ProvenanceError
from longworld.core.sourceworkflow import adapt_sec_manifest
from scripts.fetch_microsoft_gcs_annual_history import (
    HttpResponse,
    fetch_microsoft_gcs_annual_history,
)

CIK = "0000789019"


def test_sec_manifest_limit_covers_four_merged_and_detail_sources() -> None:
    assert MAX_SEC_MANIFEST_BYTES == (
        2
        * SEC_LONGITUDINAL_GCS_FILINGS
        * (MAX_SOURCE_BYTES + MAX_ISSUER_GCS_DETAIL_BYTES)
        + SEC_MANIFEST_JSON_FIXED_OVERHEAD_BYTES
    )
    assert MAX_SEC_MANIFEST_BYTES == 140_000_000


def _request() -> dict:
    filings = []
    for year, accession, node_id in (
        (2022, "0001564590-22-026876", 30786),
        (2023, "0000950170-23-035122", 31736),
        (2024, "0000950170-24-087843", 32871),
        (2025, "0000950170-25-100235", 33951),
    ):
        filings.append(
            {
                "fiscal_year": year,
                "accession": accession,
                "cik": CIK,
                "form": "10-K",
                "filing_date": f"{year}-07-{28 if year == 2022 else 27 if year == 2023 else 30:02d}",
                "report_date": f"{year}-06-30",
                "primary_document": (
                    "msft-10k_20220630.htm" if year == 2022 else f"msft-{year}0630.htm"
                ),
                "node_id": node_id,
                "detail_url": (
                    "https://microsoft.gcs-web.com/sec-filings/sec-filing/10-k/"
                    f"{accession}"
                ),
                "merged_url": (f"https://microsoft.gcs-web.com/node/{node_id}/html"),
            }
        )
    return {
        "schema_version": "longworld.microsoft-gcs-annual-fetch-request.v1",
        "user_agent": "LongWorld/0.1 tests@users.noreply.github.com",
        "authorization": {
            "record_id": "TEST-MICROSOFT-GCS-FOUR-ANNUAL",
            "scope": "bounded public issuer-owned annual filing acquisition",
            "basis": "source adapter regression test",
            "reviewed_at": "2026-09-01T00:00:00Z",
        },
        "filings": filings,
        "requests_per_second": 5,
        "timeout_seconds": 30,
    }


def _merged(filing: dict) -> str:
    primary_stem = f"msft-{filing['fiscal_year']}0630"
    primary_title = (
        filing["primary_document"] if filing["fiscal_year"] == 2022 else "10-K"
    )
    primary = (
        '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:dei="http://xbrl.sec.gov/dei/2024">\n'
        f" <head>\n  <title>\n{primary_title}\n</title>\n </head>\n"
        "<body><ix:header><ix:references><link:schemaRef "
        f'xlink:href="#{primary_stem}.xsd"/></ix:references></ix:header>'
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey">{CIK}</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>'
        f"<xbrli:endDate>{filing['report_date']}</xbrli:endDate>"
        "</body>\n</html>\n"
    )
    legacy = filing["fiscal_year"] == 2022
    exhibit_files = (
        ("EX-31.1", "msft-ex311_11.htm" if legacy else "msft-ex31_1.htm"),
        ("EX-31.2", "msft-ex312_10.htm" if legacy else "msft-ex31_2.htm"),
        ("EX-32.1", "msft-ex321_9.htm" if legacy else "msft-ex32_1.htm"),
    )
    exhibits = "".join(
        f'<div><a name="{filename}"></a></div>'
        + (
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" '
            '"http://www.w3.org/TR/html4/loose.dtd">\n'
            if legacy
            else ""
        )
        + "<html>\n <head>\n"
        + f"  <title>\n{filename if legacy else title}\n</title>\n </head>\n"
        + f" <body><p>Exhibit {title.removeprefix('EX-')}</p> certification</body>\n"
        + "</html>\n"
        for title, filename in exhibit_files
    )
    if legacy:
        exhibits = (
            '<div><a name="msft-ex21_8.htm"></a></div>'
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" '
            '"http://www.w3.org/TR/html4/loose.dtd">\n'
            "<html><head><title>\nmsft-ex21_8.htm\n</title></head>"
            "<body><p>Exhibit 21</p></body></html>\n" + exhibits
        )
    return (
        "<!DOCTYPE html>\n<html><body>\n"
        f'<a title="{filing["accession"]}.pdf">PDF</a>\n'
        f'<a href="/sec-filings/sec-filing/10-k/{filing["accession"]}">XBRL</a>\n'
        f"<!-- Creation Date      : {filing['filing_date']}T12:00:00+00:00 -->\n"
        f"<XBRL>\n{primary}</XBRL>\n{exhibits}</body></html>\n"
    )


def _detail(filing: dict, *, node_id: int | None = None) -> str:
    linked_node = filing["node_id"] if node_id is None else node_id
    month_day = {
        2022: "Jul 28, 2022",
        2023: "Jul 27, 2023",
        2024: "Jul 30, 2024",
        2025: "Jul 30, 2025",
    }[filing["fiscal_year"]]
    return (
        "<!DOCTYPE html><html><head>"
        f'<link rel="canonical" href="{filing["detail_url"]}" />'
        f"<title>{filing['accession']} | 10-K | Microsoft Corporation</title>"
        "</head><body>"
        '<div class="field__label">Form</div>'
        f'<div class="field__item"><a href="/node/{linked_node}/html">10-K</a></div>'
        '<div class="field__label">Filing Date</div>'
        f'<div class="field__item">{month_day}</div>'
        '<div class="field__label">Document Date</div>'
        f'<div class="field__item">Jun 30, {filing["fiscal_year"]}</div>'
        "</body></html>"
    )


def _write_request(tmp_path: Path, request: dict) -> Path:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def _responses(request: dict) -> dict[str, bytes]:
    responses = {}
    for filing in request["filings"]:
        responses[filing["merged_url"]] = _merged(filing).encode()
        responses[f"{filing['detail_url']}?output=1"] = _detail(filing).encode()
    return responses


def _fake_get(responses: dict[str, bytes]):
    def get(url: str, _headers: dict[str, str], _timeout: float) -> HttpResponse:
        return HttpResponse(
            body=responses[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "text/html; charset=UTF-8"},
        )

    return get


def test_fetch_emits_four_replayable_annual_filings(tmp_path: Path) -> None:
    request = _request()
    output = tmp_path / "inventory"

    input_path = fetch_microsoft_gcs_annual_history(
        _write_request(tmp_path, request),
        output,
        http_get=_fake_get(_responses(request)),
        sleep=lambda _seconds: None,
        generated_at="2026-09-01T01:00:00Z",
    )

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    manifest = build_sec_filing_manifest(
        payload, output, generated_at="2026-09-01T01:01:00Z"
    )
    assert payload["source_status"] == "issuer_owned_public_export"
    assert {filing["parser"]["version"] for filing in payload["filings"]} == {"2"}
    for filing in payload["filings"]:
        facts = {fact["field"]: fact for fact in filing["derived_facts"]}
        assert 'name="dei:DocumentType"' in facts["form"]["evidence_quote"]
        assert "Creation Date" in facts["filing_date"]["evidence_quote"]
        assert "<xbrli:endDate>" in facts["report_date"]["evidence_quote"]
    assert [filing["report_date"] for filing in payload["filings"]] == [
        "2022-06-30",
        "2023-06-30",
        "2024-06-30",
        "2025-06-30",
    ]
    assert manifest["n"] == 4
    assert len(manifest["filing_relations"]) == 3
    assert all(
        relation["relation_type"] == "prior_annual_filing"
        for relation in manifest["filing_relations"]
    )
    assert len(list(output.glob("*.html"))) == 8
    [workflow] = adapt_sec_manifest(
        manifest,
        signed_bundle_authorized=True,
        adapter_revision="sourceworkflow@2",
    )
    assert workflow.source_families == ("issuer_gcs_merged_filing_v2",)
    assert {record.attribute("parser") for record in workflow.records} == {
        "issuer_gcs_merged_html@2"
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    (
        ("redirect", "redirect"),
        ("challenge", "challenge"),
        ("wrong_identity", "identity|accession"),
        ("wrong_exact_form", "form identity"),
        ("wrong_exact_filing_date", "filing_date identity"),
        ("wrong_node", "identity binding"),
        ("wrong_title", "title.*filename|title, heading"),
        ("missing_heading", "title, heading"),
        ("filename_ambiguity", "title, heading|duplicated"),
        ("cross_type", "title, heading"),
        ("oversize_detail", "response size"),
    ),
)
def test_fetch_fails_closed_without_partial_output(
    tmp_path: Path, mutation: str, match: str
) -> None:
    request = _request()
    responses = _responses(request)
    first = request["filings"][0]
    if mutation == "challenge":
        responses[first["merged_url"]] = b"<html><title>Just a moment...</title></html>"
    elif mutation == "wrong_identity":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            first["accession"].encode(), b"0000000000-00-000000"
        )
    elif mutation == "wrong_exact_form":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            b'name="dei:DocumentType">10-K</ix:nonNumeric>',
            b'name="dei:DocumentType">8-K</ix:nonNumeric>',
        )
    elif mutation == "wrong_exact_filing_date":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            b"Creation Date      : 2022-07-28T",
            b"Creation Date      : 2022-07-27T",
        )
    elif mutation == "wrong_node":
        responses[f"{first['detail_url']}?output=1"] = _detail(
            first, node_id=99999
        ).encode()
    elif mutation == "wrong_title":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            b"\nmsft-ex311_11.htm\n</title>", b"\nwrong-title.htm\n</title>"
        )
    elif mutation == "missing_heading":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            b"<p>Exhibit 31.1</p>", b"<p>Certification</p>"
        )
    elif mutation == "filename_ambiguity":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            b"msft-ex312_10.htm", b"msft-ex311_12.htm"
        )
    elif mutation == "cross_type":
        responses[first["merged_url"]] = responses[first["merged_url"]].replace(
            b"<p>Exhibit 31.1</p>", b"<p>Exhibit 32.1</p>"
        )
    elif mutation == "oversize_detail":
        responses[f"{first['detail_url']}?output=1"] = (
            b"<html>" + b"x" * MAX_ISSUER_GCS_DETAIL_BYTES + b"</html>"
        )

    fake_get = _fake_get(responses)
    if mutation == "redirect":
        original = fake_get

        def fake_get(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
            response = original(url, headers, timeout)
            return HttpResponse(
                body=response.body,
                final_url="https://microsoft.gcs-web.com/redirected",
                status=200,
                headers=response.headers,
            )

    output = tmp_path / "inventory"
    with pytest.raises(ProvenanceError, match=match):
        fetch_microsoft_gcs_annual_history(
            _write_request(tmp_path, request),
            output,
            http_get=fake_get,
            sleep=lambda _seconds: None,
            generated_at="2026-09-01T01:00:00Z",
        )
    assert not output.exists()


def test_request_rejects_non_gcs_host_before_network_access(tmp_path: Path) -> None:
    request = _request()
    request["filings"][0]["merged_url"] = "https://example.com/node/30786/html"
    called = False

    def unexpected_get(
        _url: str, _headers: dict[str, str], _timeout: float
    ) -> HttpResponse:
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ProvenanceError, match="GCS filing URL"):
        fetch_microsoft_gcs_annual_history(
            _write_request(tmp_path, request),
            tmp_path / "inventory",
            http_get=unexpected_get,
            sleep=lambda _seconds: None,
            generated_at="2026-09-01T01:00:00Z",
        )
    assert called is False


def test_fetch_refuses_existing_output_before_network_access(tmp_path: Path) -> None:
    request = _request()
    output = tmp_path / "inventory"
    output.mkdir()
    called = False

    def unexpected_get(
        _url: str, _headers: dict[str, str], _timeout: float
    ) -> HttpResponse:
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ProvenanceError, match="already exists"):
        fetch_microsoft_gcs_annual_history(
            _write_request(tmp_path, request),
            output,
            http_get=unexpected_get,
            sleep=lambda _seconds: None,
            generated_at="2026-09-01T01:00:00Z",
        )
    assert called is False


def test_v2_manifest_audit_replays_detail_sidecars(tmp_path: Path) -> None:
    request = _request()
    output = tmp_path / "inventory"
    input_path = fetch_microsoft_gcs_annual_history(
        _write_request(tmp_path, request),
        output,
        http_get=_fake_get(_responses(request)),
        sleep=lambda _seconds: None,
        generated_at="2026-09-01T01:00:00Z",
    )
    manifest = build_sec_filing_manifest(
        json.loads(input_path.read_text(encoding="utf-8")),
        output,
        generated_at="2026-09-01T01:01:00Z",
    )
    (output / "detail-2022.html").unlink()

    with pytest.raises(ProvenanceError, match="replay issuer GCS detail source"):
        _audit_exported_manifest(manifest, base_directory=output)


def test_issuer_export_rejects_unrecognized_gcs_parser_revision(
    tmp_path: Path,
) -> None:
    request = _request()
    output = tmp_path / "inventory"
    input_path = fetch_microsoft_gcs_annual_history(
        _write_request(tmp_path, request),
        output,
        http_get=_fake_get(_responses(request)),
        sleep=lambda _seconds: None,
        generated_at="2026-09-01T01:00:00Z",
    )
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    payload["filings"][0]["parser"]["version"] = "3"

    with pytest.raises(ProvenanceError, match="source status.*parser"):
        build_sec_filing_manifest(payload, output, generated_at="2026-09-01T01:01:00Z")
