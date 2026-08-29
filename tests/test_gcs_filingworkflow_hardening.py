from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    attach_attestation,
)
from longworld.core.filingworkflow import (
    build_sec_filing_manifest,
    load_sec_filing_manifest,
    parse_issuer_gcs_merged_components,
)
from longworld.core.provenance import ProvenanceError
from longworld.core.sourcebundle import (
    SOURCE_WORKFLOW_ADAPTER_REVISION_LATEST,
    SOURCE_WORKFLOW_BUNDLE_PURPOSE,
    SOURCE_WORKFLOW_BUNDLE_SCHEMA,
    load_source_workflow_bundle,
)
from longworld.core.sourceworkflow import SEC_SOURCE_KIND

ACCESSION = "0000950170-25-100235"
CIK = "0000789019"
PRIMARY_DOCUMENT = "msft-20250630.htm"
KEY = b"gcs-hardening-test-key-material-32b"


@pytest.fixture(autouse=True)
def _source_producer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], KEY.decode())
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-gcs-hardening-v1")


def _gcs_document(title: str, filename: str, body: str = "body") -> str:
    return (
        f'<div><a name="{filename}"></a></div><html>\n'
        f" <head>\n  <title>{title}</title>\n </head>\n"
        f" <body>{body}</body>\n</html>\n"
    )


def _gcs_merged() -> str:
    primary = (
        '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:dei="http://xbrl.sec.gov/dei/2024">\n'
        " <head>\n  <title>10-K</title>\n </head>\n"
        "<body><ix:header><ix:references><link:schemaRef "
        'xlink:href="#msft-20250630.xsd"/></ix:references></ix:header>'
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey">{CIK}</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>'
        "<xbrli:endDate>2025-06-30</xbrli:endDate>"
        "</body>\n</html>\n"
    )
    exhibits = "".join(
        _gcs_document(title, filename)
        for title, filename in (
            ("EX-31.1", "msft-ex31_1.htm"),
            ("EX-31.2", "msft-ex31_2.htm"),
            ("EX-32.1", "msft-ex32_1.htm"),
        )
    )
    return (
        "<!DOCTYPE html>\n<html><body>\n"
        f'<a title="{ACCESSION}.pdf">PDF</a>\n'
        f'<a href="/sec-filings/sec-filing/10-k/{ACCESSION}">XBRL</a>\n'
        "<!-- Creation Date :2025-07-30T12:31:37+00:00 -->\n"
        f"<XBRL>\n{primary}</XBRL>\n{exhibits}</body></html>\n"
    )


def _gcs_detail() -> str:
    detail_url = (
        f"https://microsoft.gcs-web.com/sec-filings/sec-filing/10-k/{ACCESSION}"
    )
    return (
        "<!DOCTYPE html><html><head>"
        f'<link rel="canonical" href="{detail_url}" />'
        f"<title>{ACCESSION} | 10-K | Microsoft Corporation</title>"
        "</head><body>"
        '<div class="field__label">Form</div>'
        '<div class="field__item"><a href="/node/33951/html">10-K</a></div>'
        '<div class="field__label">Filing Date</div>'
        '<div class="field__item">Jul 30, 2025</div>'
        '<div class="field__label">Document Date</div>'
        '<div class="field__item">Jun 30, 2025</div>'
        "</body></html>"
    )


def _gcs_fact(text: str, fact_id: str, field: str, value: str, quote: str) -> dict:
    return {
        "fact_id": fact_id,
        "field": field,
        "value": value,
        "evidence_quote": quote,
        "evidence_char_start": text.index(quote),
    }


def _gcs_input(merged: str, detail: str) -> dict:
    detail_url = (
        f"https://microsoft.gcs-web.com/sec-filings/sec-filing/10-k/{ACCESSION}"
    )
    return {
        "schema_version": "longworld.sec-filing-input.v1",
        "source_status": "issuer_owned_public_export",
        "authorization": {
            "record_id": "TEST-GCS-HARDENING-002",
            "scope": "issuer-owned public filing fixture",
            "basis": "bundle raw replay regression test",
            "reviewed_at": "2026-08-29T00:00:00Z",
        },
        "filings": [
            {
                "accession": ACCESSION,
                "cik": CIK,
                "form": "10-K",
                "filing_date": "2025-07-30",
                "report_date": "2025-06-30",
                "primary_document": PRIMARY_DOCUMENT,
                "source_url": "https://microsoft.gcs-web.com/node/33951/html",
                "source_file": "merged.html",
                "source_sha256": hashlib.sha256(merged.encode()).hexdigest(),
                "detail_url": detail_url,
                "detail_file": "detail.html",
                "detail_sha256": hashlib.sha256(detail.encode()).hexdigest(),
                "retrieved_at": "2026-08-29T00:01:00Z",
                "access_policy": "public issuer-owned investor-relations export",
                "parser": {"name": "issuer_gcs_merged_html", "version": "1"},
                "derived_facts": [
                    _gcs_fact(
                        merged,
                        "accession",
                        "accession",
                        ACCESSION,
                        f"{ACCESSION}.pdf",
                    ),
                    _gcs_fact(
                        merged,
                        "cik",
                        "cik",
                        CIK,
                        f">{CIK}</ix:nonNumeric>",
                    ),
                    _gcs_fact(merged, "form", "form", "10-K", "<title>10-K</title>"),
                    _gcs_fact(
                        merged,
                        "filing_date",
                        "filing_date",
                        "2025-07-30",
                        "2025-07-30T12:31:37+00:00",
                    ),
                    _gcs_fact(
                        merged,
                        "report_date",
                        "report_date",
                        "2025-06-30",
                        "<xbrli:endDate>2025-06-30</xbrli:endDate>",
                    ),
                    _gcs_fact(
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


def _fixture_input(source_text: str) -> dict:
    quote = "Revenue was USD 42 million"
    return {
        "schema_version": "longworld.sec-filing-input.v1",
        "source_status": "test_fixture",
        "authorization": {
            "record_id": "TEST-GCS-HARDENING-001",
            "scope": "read-only filing fixture",
            "basis": "loader replay regression test",
            "reviewed_at": "2026-08-29T00:00:00Z",
        },
        "filings": [
            {
                "accession": "0000000001-26-000001",
                "cik": "0000000001",
                "form": "10-K",
                "filing_date": "2026-02-20",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1/fixture.txt",
                "source_file": "filing.txt",
                "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "retrieved_at": "2026-08-29T00:01:00Z",
                "access_policy": "public test fixture",
                "parser": {"name": "sec_fixture_text", "version": "1"},
                "derived_facts": [
                    {
                        "fact_id": "revenue",
                        "field": "revenue",
                        "value": "USD 42 million",
                        "evidence_quote": quote,
                        "evidence_char_start": source_text.index(quote),
                    }
                ],
            }
        ],
    }


def _write_signed_manifest(tmp_path: Path, source_text: str) -> Path:
    (tmp_path / "filing.txt").write_text(source_text, encoding="utf-8")
    manifest = build_sec_filing_manifest(
        _fixture_input(source_text),
        tmp_path,
        generated_at="2026-08-29T00:02:00Z",
    )
    signed = attach_attestation(manifest, KEY, purpose="source_manifest")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(signed), encoding="utf-8")
    return path


def _write_gcs_signed_manifest(tmp_path: Path) -> Path:
    merged = _gcs_merged()
    detail = _gcs_detail()
    (tmp_path / "merged.html").write_text(merged, encoding="utf-8")
    (tmp_path / "detail.html").write_text(detail, encoding="utf-8")
    manifest = build_sec_filing_manifest(
        _gcs_input(merged, detail),
        tmp_path,
        generated_at="2026-08-29T00:02:00Z",
    )
    signed = attach_attestation(manifest, KEY, purpose="source_manifest")
    path = tmp_path / "gcs-manifest.json"
    path.write_text(json.dumps(signed), encoding="utf-8")
    return path


def _write_bundle(tmp_path: Path, manifest_path: Path) -> Path:
    manifest_raw = manifest_path.read_bytes()
    unsigned = {
        "schema_version": SOURCE_WORKFLOW_BUNDLE_SCHEMA,
        "n": 1,
        "entries": [
            {
                "kind": SEC_SOURCE_KIND,
                "target_domain": "company",
                "path": manifest_path.name,
                "sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "schema_version": "longworld.sec-filing-manifest.v1",
                "adapter_revision": SOURCE_WORKFLOW_ADAPTER_REVISION_LATEST,
            }
        ],
    }
    signed = attach_attestation(unsigned, KEY, purpose=SOURCE_WORKFLOW_BUNDLE_PURPOSE)
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(signed), encoding="utf-8")
    return path


def _relabel_gcs_manifest_as_authorized(manifest_path: Path) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("attestation")
    payload["source_status"] = "authorized_download"
    payload["filings"][0].pop("acquisition_receipt")
    signed = attach_attestation(payload, KEY, purpose="source_manifest")
    manifest_path.write_text(json.dumps(signed), encoding="utf-8")


def test_gcs_required_exhibit_must_be_inside_outer_html_envelope() -> None:
    source = _gcs_merged()
    exhibit = _gcs_document("EX-32.1", "msft-ex32_1.htm")
    outer_close = "</body></html>\n"
    corrupted = source.replace(exhibit, "").removesuffix(outer_close)
    corrupted += outer_close + exhibit

    with pytest.raises(ProvenanceError, match="outer HTML envelope"):
        parse_issuer_gcs_merged_components(
            corrupted,
            expected_accession=ACCESSION,
            expected_cik=CIK,
            expected_form="10-K",
            expected_primary_document=PRIMARY_DOCUMENT,
        )


def test_gcs_exhibit_anchor_and_html_must_be_contiguous() -> None:
    source = _gcs_merged()
    corrupted = source.replace(
        '<div><a name="msft-ex32_1.htm"></a></div><html>',
        '<div><a name="msft-ex32_1.htm"></a></div><p>noise</p><html>',
    )

    with pytest.raises(ProvenanceError, match="filename is missing"):
        parse_issuer_gcs_merged_components(
            corrupted,
            expected_accession=ACCESSION,
            expected_cik=CIK,
            expected_form="10-K",
            expected_primary_document=PRIMARY_DOCUMENT,
        )


def test_manifest_loader_replays_raw_source_and_sanitization(tmp_path: Path) -> None:
    source = "Contact filings@example.com. Revenue was USD 42 million.\n"
    manifest_path = _write_signed_manifest(tmp_path, source)

    loaded = load_sec_filing_manifest(manifest_path, attestation_key=KEY)
    assert loaded["filings"][0]["text"] == (
        "Contact [redacted-email]. Revenue was USD 42 million.\n"
    )

    (tmp_path / "filing.txt").write_text(source + "tampered\n", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="source hash mismatch"):
        load_sec_filing_manifest(manifest_path, attestation_key=KEY)


def test_manifest_loader_rejects_resigned_coordinated_source_digest_tamper(
    tmp_path: Path,
) -> None:
    source = "Contact filings@example.com. Revenue was USD 42 million.\n"
    manifest_path = _write_signed_manifest(tmp_path, source)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("attestation")
    filing = payload["filings"][0]
    forged_sha256 = hashlib.sha256(b"coordinated forged GCS source").hexdigest()
    filing["source_sha256"] = forged_sha256
    filing["provenance_id"] = f"sha256:{forged_sha256}"
    filing["derived_facts"][0]["source_sha256"] = forged_sha256
    resigned = attach_attestation(payload, KEY, purpose="source_manifest")
    manifest_path.write_text(json.dumps(resigned), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="source hash mismatch"):
        load_sec_filing_manifest(manifest_path, attestation_key=KEY)


def test_bundle_loader_requires_gcs_raw_source_sidecar(tmp_path: Path) -> None:
    manifest_path = _write_gcs_signed_manifest(tmp_path)
    bundle_path = _write_bundle(tmp_path, manifest_path)

    loaded = load_source_workflow_bundle(bundle_path, attestation_key=KEY)
    assert len(loaded.workflows) == 1

    (tmp_path / "merged.html").unlink()
    with pytest.raises(ProvenanceError, match="cannot replay SEC filing source"):
        load_source_workflow_bundle(bundle_path, attestation_key=KEY)


def test_bundle_loader_rejects_gcs_raw_source_tamper(tmp_path: Path) -> None:
    manifest_path = _write_gcs_signed_manifest(tmp_path)
    bundle_path = _write_bundle(tmp_path, manifest_path)
    source_path = tmp_path / "merged.html"
    source_path.write_text(source_path.read_text(encoding="utf-8") + "tampered\n")

    with pytest.raises(ProvenanceError, match="source hash mismatch"):
        load_source_workflow_bundle(bundle_path, attestation_key=KEY)


def test_bundle_loader_rejects_resigned_coordinated_gcs_digest_tamper(
    tmp_path: Path,
) -> None:
    manifest_path = _write_gcs_signed_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("attestation")
    filing = payload["filings"][0]
    forged_sha256 = hashlib.sha256(b"coordinated forged GCS source").hexdigest()
    filing["source_sha256"] = forged_sha256
    filing["provenance_id"] = f"sha256:{forged_sha256}"
    for fact in filing["derived_facts"]:
        fact["source_sha256"] = forged_sha256
    resigned = attach_attestation(payload, KEY, purpose="source_manifest")
    manifest_path.write_text(json.dumps(resigned), encoding="utf-8")
    bundle_path = _write_bundle(tmp_path, manifest_path)

    with pytest.raises(ProvenanceError, match="source hash mismatch"):
        load_source_workflow_bundle(bundle_path, attestation_key=KEY)


def test_build_rejects_gcs_parser_relabelled_as_authorized_download(
    tmp_path: Path,
) -> None:
    merged = _gcs_merged()
    detail = _gcs_detail()
    (tmp_path / "merged.html").write_text(merged, encoding="utf-8")
    payload = _gcs_input(merged, detail)
    payload["source_status"] = "authorized_download"
    filing = payload["filings"][0]
    for field in ("detail_url", "detail_file", "detail_sha256"):
        filing.pop(field)

    with pytest.raises(ProvenanceError, match="source status.*parser"):
        build_sec_filing_manifest(
            payload,
            tmp_path,
            generated_at="2026-08-29T00:02:00Z",
        )


def test_manifest_loader_rejects_gcs_parser_relabelled_as_authorized_download(
    tmp_path: Path,
) -> None:
    manifest_path = _write_gcs_signed_manifest(tmp_path)
    _relabel_gcs_manifest_as_authorized(manifest_path)

    with pytest.raises(ProvenanceError, match="source status.*parser"):
        load_sec_filing_manifest(manifest_path, attestation_key=KEY)


def test_bundle_loader_rejects_gcs_parser_relabelled_as_authorized_download(
    tmp_path: Path,
) -> None:
    manifest_path = _write_gcs_signed_manifest(tmp_path)
    _relabel_gcs_manifest_as_authorized(manifest_path)
    bundle_path = _write_bundle(tmp_path, manifest_path)

    with pytest.raises(ProvenanceError, match="source status.*parser"):
        load_source_workflow_bundle(bundle_path, attestation_key=KEY)
