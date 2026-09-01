from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.attestation import attach_attestation
from longworld.core.filingworkflow import (
    build_sec_filing_manifest,
    load_sec_filing_manifest,
    parse_issuer_gcs_merged_components,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.secvisible import normalize_sec_visible_text
from longworld.core.secxbrl import parse_sec_financial_program
from longworld.domains.company.simulate import _section_fact_spans
from scripts.fetch_sec_workflow import _validate_row, fetch_sec_workflow

MICROSOFT_ACCESSION = "0000950170-25-100235"
MICROSOFT_CIK = "0000789019"
MICROSOFT_PRIMARY_DOCUMENT = "msft-20250630.htm"
MICROSOFT_SOURCE_SHA256 = (
    "05dbf0c0107110572938280e244af32f7fa3d23be8736dff3235fa08a04e5b35"
)
MICROSOFT_SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/source_inventory/p12_sec_microsoft_gcs_v1/msft-20250630.html"
)


@pytest.mark.skipif(
    not MICROSOFT_SOURCE_PATH.is_file(),
    reason="requires the local Microsoft issuer source inventory",
)
def test_microsoft_real_ex31_certification_spans_are_visible_and_source_bound() -> None:
    source = MICROSOFT_SOURCE_PATH.read_text(encoding="utf-8")
    assert hashlib.sha256(source.encode()).hexdigest() == MICROSOFT_SOURCE_SHA256
    program = parse_sec_financial_program(
        source,
        MICROSOFT_SOURCE_SHA256,
        report_date="2025-06-30",
        parser_revision="issuer_gcs_merged_html@1",
    )
    sections = {section.section_id: section for section in program.sections}

    for section_id, expected_name, expected_kind in (
        ("ex_31_1", "Satya Nadella", "Exhibit 31.1"),
        ("ex_31_2", "Amy E. Hood", "Exhibit 31.2"),
    ):
        section = sections[section_id]
        visible = normalize_sec_visible_text(
            source,
            char_start=section.char_start,
            char_end=section.char_end,
            context_char_start=section.char_start,
            context_char_end=section.char_end,
        )
        spans = _section_fact_spans(
            source,
            program,
            visible=visible,
            char_start=section.char_start,
            char_end=section.char_end,
        )
        [fact] = program.certifications[section_id]
        [span] = [item for item in spans if item["kind"] == "certification"]
        kind_span = span["evidence_spans"]["certification_kind"]

        assert fact.component_sha256 == section.component_sha256
        assert source[fact.char_start : fact.char_end] == expected_name
        assert source[
            kind_span["source_char_start"] : kind_span["source_char_end"]
        ] == (expected_kind)
        assert visible.text[kind_span["char_start"] : kind_span["char_end"]] == (
            expected_kind
        )


def _gcs_merged(*, cik: str = MICROSOFT_CIK, duplicate_31_1: bool = False) -> str:
    def document(title: str, filename: str, body: str = "body") -> str:
        return (
            f'<div><a name="{filename}"></a></div><html>\n'
            f" <head>\n  <title>{title}</title>\n </head>\n"
            f" <body>{body}</body>\n</html>\n"
        )

    primary = (
        '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:dei="http://xbrl.sec.gov/dei/2024">\n'
        " <head>\n  <title>10-K</title>\n </head>\n"
        "<body><ix:header><ix:references><link:schemaRef "
        'xlink:href="#msft-20250630.xsd"/></ix:references></ix:header>'
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey">{cik}</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>'
        "<xbrli:endDate>2025-06-30</xbrli:endDate>"
        "</body>\n</html>\n"
    )
    exhibits = [
        document("EX-31.1", "msft-ex31_1.htm"),
        document("EX-31.2", "msft-ex31_2.htm"),
        document("EX-32.1", "msft-ex32_1.htm"),
        document("EX-32.2", "msft-ex32_2.htm"),
    ]
    if duplicate_31_1:
        exhibits.append(document("EX-31.1", "msft-ex31_1-copy.htm"))
    return (
        "<!DOCTYPE html>\n<html><body>\n"
        f'<a title="{MICROSOFT_ACCESSION}.pdf">PDF</a>\n'
        f'<a href="/sec-filings/sec-filing/10-k/{MICROSOFT_ACCESSION}">XBRL</a>\n'
        "<!-- Creation Date :2025-07-30T12:31:37+00:00 -->\n"
        "<XBRL>\n" + primary + "</XBRL>\n" + "".join(exhibits) + "</body></html>\n"
    )


def _gcs_detail() -> str:
    url = (
        "https://microsoft.gcs-web.com/sec-filings/sec-filing/10-k/"
        f"{MICROSOFT_ACCESSION}"
    )
    return (
        "<!DOCTYPE html><html><head>"
        f'<link rel="canonical" href="{url}" />'
        f"<title>{MICROSOFT_ACCESSION} | 10-K | Microsoft Corporation</title>"
        "</head><body>"
        '<div class="field__label">Form</div>'
        '<div class="field__item"><a href="/node/33951/html">10-K</a></div>'
        '<div class="field__label">Filing Date</div>'
        '<div class="field__item">Jul 30, 2025</div>'
        '<div class="field__label">Document Date</div>'
        '<div class="field__item">Jun 30, 2025</div>'
        "</body></html>"
    )


def _fact(text: str, fact_id: str, field: str, value: str, quote: str) -> dict:
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": quote,
        "evidence_char_start": text.index(quote),
    }


def _gcs_input(merged: str, detail: str) -> dict:
    return {
        "schema_version": "longworld.sec-filing-input.v1",
        "source_status": "issuer_owned_public_export",
        "authorization": {
            "record_id": "P12-MSFT-GCS-TEST",
            "scope": "Microsoft issuer-owned public 2025 10-K export",
            "basis": "public issuer source workflow test",
            "reviewed_at": "2026-08-29T00:00:00Z",
        },
        "filings": [
            {
                "accession": MICROSOFT_ACCESSION,
                "cik": MICROSOFT_CIK,
                "form": "10-K",
                "filing_date": "2025-07-30",
                "report_date": "2025-06-30",
                "primary_document": MICROSOFT_PRIMARY_DOCUMENT,
                "source_url": "https://microsoft.gcs-web.com/node/33951/html",
                "source_file": "merged.html",
                "source_sha256": hashlib.sha256(merged.encode()).hexdigest(),
                "detail_url": (
                    "https://microsoft.gcs-web.com/sec-filings/sec-filing/10-k/"
                    f"{MICROSOFT_ACCESSION}"
                ),
                "detail_file": "detail.html",
                "detail_sha256": hashlib.sha256(detail.encode()).hexdigest(),
                "retrieved_at": "2026-08-29T00:01:00Z",
                "access_policy": "public issuer-owned investor-relations export",
                "parser": {"name": "issuer_gcs_merged_html", "version": "1"},
                "derived_facts": [
                    _fact(
                        merged,
                        "accession",
                        "accession",
                        MICROSOFT_ACCESSION,
                        f"{MICROSOFT_ACCESSION}.pdf",
                    ),
                    _fact(
                        merged,
                        "cik",
                        "cik",
                        MICROSOFT_CIK,
                        f">{MICROSOFT_CIK}</ix:nonNumeric>",
                    ),
                    _fact(merged, "form", "form", "10-K", "<title>10-K</title>"),
                    _fact(
                        merged,
                        "filing_date",
                        "filing_date",
                        "2025-07-30",
                        "2025-07-30T12:31:37+00:00",
                    ),
                    _fact(
                        merged,
                        "report_date",
                        "report_date",
                        "2025-06-30",
                        "<xbrli:endDate>2025-06-30</xbrli:endDate>",
                    ),
                    _fact(
                        merged,
                        "primary_document_stem",
                        "primary_document_stem",
                        "msft-20250630",
                        'xlink:href="#msft-20250630.xsd"',
                    ),
                ],
            }
        ],
    }


def _request() -> dict:
    return {
        "schema_version": "longworld.sec-fetch-request.v1",
        "user_agent": "LongWorld Research research@longworld.dev",
        "authorization": {
            "record_id": "P12-SEC-MULTIISSUER-TEST",
            "scope": "Microsoft public EDGAR 10-K",
            "basis": "test fixture for the public SEC binding contract",
            "reviewed_at": "2026-08-29T00:00:00Z",
        },
        "allowed_ciks": [MICROSOFT_CIK],
        "forms": ["10-K"],
        "max_filings_per_cik": 1,
        "requests_per_second": 5,
        "max_retries": 0,
    }


def _submission() -> bytes:
    return json.dumps(
        {
            "cik": "789019",
            "filings": {
                "recent": {
                    "accessionNumber": [MICROSOFT_ACCESSION],
                    "filingDate": ["2025-07-30"],
                    "reportDate": ["2025-06-30"],
                    "form": ["10-K"],
                    "primaryDocument": [MICROSOFT_PRIMARY_DOCUMENT],
                }
            },
        }
    ).encode()


def _filing(*, cik: str = MICROSOFT_CIK) -> bytes:
    return (
        f"<SEC-DOCUMENT>{MICROSOFT_ACCESSION}.txt\n"
        f"ACCESSION NUMBER:\t\t{MICROSOFT_ACCESSION}\n"
        "CONFORMED SUBMISSION TYPE:\t10-K\n"
        "CONFORMED PERIOD OF REPORT:\t20250630\n"
        "FILED AS OF DATE:\t\t20250730\n"
        f"CENTRAL INDEX KEY:\t\t\t{cik}\n"
        "<DOCUMENT>\n"
        "<TYPE>10-K\n"
        "<SEQUENCE>1\n"
        f"<FILENAME>{MICROSOFT_PRIMARY_DOCUMENT}\n"
        "<TEXT>authentic filing body fixture</TEXT>\n"
        "</DOCUMENT>\n"
    ).encode()


def test_sec_fetch_accepts_accession_owned_by_filing_agent() -> None:
    """Microsoft's authentic 2025 10-K accession prefix is not its issuer CIK."""
    _validate_row(
        {
            "accessionNumber": MICROSOFT_ACCESSION,
            "form": "10-K",
            "filingDate": "2025-07-30",
            "reportDate": "2025-06-30",
            "primaryDocument": MICROSOFT_PRIMARY_DOCUMENT,
        },
        MICROSOFT_CIK,
    )


def test_sec_fetch_binds_accession_cik_form_and_primary_document(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    submission_url = f"https://data.sec.gov/submissions/CIK{MICROSOFT_CIK}.json"
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/789019/"
        "000095017025100235/0000950170-25-100235.txt"
    )

    def get(url: str, _headers: dict[str, str], _timeout: float):
        return ({submission_url: _submission(), filing_url: _filing()}[url], {})

    input_path = fetch_sec_workflow(
        request_path,
        tmp_path / "download",
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-29T01:00:00Z",
    )
    [filing] = json.loads(input_path.read_text(encoding="utf-8"))["filings"]

    assert filing["accession"] == MICROSOFT_ACCESSION
    assert filing["cik"] == MICROSOFT_CIK
    assert filing["form"] == "10-K"
    assert filing["primary_document"] == MICROSOFT_PRIMARY_DOCUMENT
    assert {fact["field"] for fact in filing["derived_facts"]} >= {
        "accession",
        "cik",
        "form",
        "primary_document",
    }

    manifest = build_sec_filing_manifest(
        json.loads(input_path.read_text(encoding="utf-8")),
        input_path.parent,
        generated_at="2026-08-29T01:01:00Z",
    )
    [exported] = manifest["filings"]
    assert exported["cik"] == MICROSOFT_CIK
    assert exported["primary_document"] == MICROSOFT_PRIMARY_DOCUMENT


def test_sec_fetch_rejects_body_cik_mismatch(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    submission_url = f"https://data.sec.gov/submissions/CIK{MICROSOFT_CIK}.json"
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/789019/"
        "000095017025100235/0000950170-25-100235.txt"
    )

    def get(url: str, _headers: dict[str, str], _timeout: float):
        bodies = {
            submission_url: _submission(),
            filing_url: _filing(cik="0001045810"),
        }
        return bodies[url], {}

    with pytest.raises(ProvenanceError, match="bind cik"):
        fetch_sec_workflow(
            request_path,
            tmp_path / "download",
            http_get=get,
            sleep=lambda _: None,
            generated_at="2026-08-29T01:00:00Z",
        )


def test_gcs_merged_parser_binds_exact_components_and_identity() -> None:
    source = _gcs_merged()
    components = parse_issuer_gcs_merged_components(
        source,
        expected_accession=MICROSOFT_ACCESSION,
        expected_cik=MICROSOFT_CIK,
        expected_form="10-K",
        expected_primary_document=MICROSOFT_PRIMARY_DOCUMENT,
    )

    assert [component.component_type for component in components] == [
        "10-K",
        "EX-31.1",
        "EX-31.2",
        "EX-32.1",
        "EX-32.2",
    ]
    assert components[0].filename == MICROSOFT_PRIMARY_DOCUMENT
    assert all(
        source[component.char_start : component.char_end].startswith("<html")
        for component in components
    )


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (_gcs_merged(cik="0001045810"), "CIK"),
        (_gcs_merged(duplicate_31_1=True), "duplicated"),
        (
            _gcs_merged().replace("<title>EX-32.1</title>", "<title>EX-99</title>"),
            "missing",
        ),
        (
            _gcs_merged().replace("<title>EX-31.2</title>", "<title>EX-31.1</title>"),
            "duplicated",
        ),
    ],
)
def test_gcs_merged_parser_fails_closed_on_identity_or_component_confusion(
    source: str, error: str
) -> None:
    with pytest.raises(ProvenanceError, match=error):
        parse_issuer_gcs_merged_components(
            source,
            expected_accession=MICROSOFT_ACCESSION,
            expected_cik=MICROSOFT_CIK,
            expected_form="10-K",
            expected_primary_document=MICROSOFT_PRIMARY_DOCUMENT,
        )


def test_gcs_manifest_replays_detail_and_merged_hashes(tmp_path: Path) -> None:
    merged = _gcs_merged()
    detail = _gcs_detail()
    (tmp_path / "merged.html").write_text(merged, encoding="utf-8")
    (tmp_path / "detail.html").write_text(detail, encoding="utf-8")
    manifest = build_sec_filing_manifest(
        _gcs_input(merged, detail),
        tmp_path,
        generated_at="2026-08-29T00:02:00Z",
    )

    [filing] = manifest["filings"]
    assert manifest["source_status"] == "issuer_owned_public_export"
    assert filing["parser"] == "issuer_gcs_merged_html@1"
    assert filing["acquisition_receipt"]["source_class"] == (
        "issuer_owned_rendered_filing_not_sec_archives_original"
    )

    key = b"gcs-manifest-test-key-material-32b"
    signed = attach_attestation(manifest, key, purpose="source_manifest")
    manifest_path = tmp_path / "signed.json"
    manifest_path.write_text(json.dumps(signed), encoding="utf-8")
    load_sec_filing_manifest(manifest_path, attestation_key=key)

    signed["filings"][0]["acquisition_receipt"]["detail_text"] += "tampered"
    manifest_path.write_text(json.dumps(signed), encoding="utf-8")
    with pytest.raises(ProvenanceError, match="attestation"):
        load_sec_filing_manifest(manifest_path, attestation_key=key)
