from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from longworld.core.attestation import (
    ATTESTATION_ENVIRONMENT_ENV,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
    verify_attestation,
)
from longworld.core.issuerpdfworkflow import ISSUER_OFFICIAL_PDF_SOURCE_KIND
from longworld.core.provenance import ProvenanceError
from longworld.core.sourcebundle import load_source_workflow_bundle

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_source_workflow_bundle import build_source_workflow_bundle
from export_jpmorgan_annual_reports import (
    HttpResponse,
    export_jpmorgan_annual_reports,
    load_verified_jpmorgan_annual_report_inventory,
)


@pytest.fixture(autouse=True)
def _source_producer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], "j" * 32)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-jpmorgan-source-v1")


def _text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    )
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode())
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(body)


def _request() -> dict:
    host = "www.jpmorganchase.com"
    base = (
        f"https://{host}/content/dam/jpmc/jpmorgan-chase-and-co/"
        "investor-relations/documents"
    )
    return {
        "schema_version": "longworld.jpmorgan-annual-report-request.v1",
        "user_agent": "LongWorld Research research@example-research.org",
        "authorization": {
            "record_id": "JPMORGAN-OFFICIAL-ANNUALS-TEST-001",
            "scope": "Three issuer-owned annual reports",
            "basis": "public issuer annual-report research export",
            "reviewed_at": "2026-09-02T19:00:00Z",
        },
        "reports": [
            {"year": year, "url": f"{base}/annualreport-{year}.pdf"}
            for year in (2022, 2023, 2024)
        ],
        "requests_per_second": 2,
        "timeout_seconds": 90,
    }


def _report_text(year: int) -> str:
    cyber = (
        "The Global Cybersecurity and Technology Controls governance structure "
        "is designed to identify, escalate, and mitigate information security risks."
        if year == 2022
        else (
            "The Cybersecurity and Technology Controls Operating Committee "
            '("CTOC") is the principal management committee that oversees the '
            "Firm's assessment and management of cybersecurity risk."
        )
    )
    return (
        "INTRODUCTION \nJPMorgan Chase & Co. (NYSE: JPM) annual overview.\n"
        "FIRMWIDE RISK MANAGEMENT\nFirmwide governance narrative.\n"
        "Operational Risk Management Framework\n"
        "The Firm's Compliance, Conduct, and Operational Risk framework.\n"
        f"{cyber}\n"
        "Management's report on internal control over financial reporting\n"
        f"Management control conclusion for December 31, {year}.\n"
        "Report of Independent Registered Public Accounting Firm"
    )


def test_export_writes_verified_jpmorgan_official_pdf_inventory(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }

    output = export_jpmorgan_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"j" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["issuer"] == {
        "name": "JPMorgan Chase & Co.",
        "cik": "0000019617",
        "official_host": "www.jpmorganchase.com",
    }
    assert [record["year"] for record in manifest["records"]] == [2022, 2023, 2024]
    assert [
        section["section_id"] for section in manifest["records"][2]["sections"]
    ] == ["mda_introduction", "firmwide_risk_management", "internal_control"]
    assert {fact["field"] for fact in manifest["records"][2]["derived_facts"]} == {
        "mda_introduction",
        "firmwide_risk_management",
        "operational_risk_framework",
        "cyber_ctoc_governance",
        "internal_control_report",
    }
    assert verify_attestation(manifest, b"j" * 32, purpose="source_manifest")


def _export_fixture(tmp_path: Path) -> Path:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    return export_jpmorgan_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"j" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )


def test_inventory_verifier_rejects_changed_jpmorgan_text(tmp_path: Path) -> None:
    output = _export_fixture(tmp_path)
    changed = output.parent / "jpmorgan-2024-annual.txt"
    changed.write_text("changed source text", encoding="utf-8")

    with pytest.raises(ProvenanceError, match="text receipt"):
        load_verified_jpmorgan_annual_report_inventory(
            output, attestation_key=b"j" * 32
        )


def test_export_rejects_a_different_jpmorgan_official_path(tmp_path: Path) -> None:
    request = _request()
    request["reports"][2]["url"] = (
        "https://www.jpmorganchase.com/content/dam/jpmc/"
        "jpmorgan-chase-and-co/investor-relations/documents/2024-10-k.pdf"
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="URL is invalid"):
        export_jpmorgan_annual_reports(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: None,
            attestation_key=b"j" * 32,
            generated_at="2026-09-02T20:00:00Z",
        )

    assert not (tmp_path / "inventory").exists()


def test_signed_bundle_loads_three_jpmorgan_reports_and_relations(
    tmp_path: Path,
) -> None:
    manifest_path = _export_fixture(tmp_path)
    bundle_path = manifest_path.parent / "source_workflow_bundle.signed.json"

    loaded = build_source_workflow_bundle(
        [(ISSUER_OFFICIAL_PDF_SOURCE_KIND, manifest_path)],
        bundle_path,
        attestation_key=b"j" * 32,
    )

    assert loaded.bindings[0].kind == ISSUER_OFFICIAL_PDF_SOURCE_KIND
    workflow = loaded.workflows[0]
    assert [record.attribute("year") for record in workflow.records] == [
        "2022",
        "2023",
        "2024",
    ]
    assert [relation.kind for relation in workflow.relations] == [
        "prior_official_annual_report",
        "prior_official_annual_report",
    ]


def test_bundle_replay_rejects_tampered_jpmorgan_text(tmp_path: Path) -> None:
    manifest_path = _export_fixture(tmp_path)
    bundle_path = manifest_path.parent / "source_workflow_bundle.signed.json"
    build_source_workflow_bundle(
        [(ISSUER_OFFICIAL_PDF_SOURCE_KIND, manifest_path)],
        bundle_path,
        attestation_key=b"j" * 32,
    )
    (manifest_path.parent / "jpmorgan-2024-annual.txt").write_text(
        "tampered", encoding="utf-8"
    )

    with pytest.raises(ProvenanceError, match="byte receipt"):
        load_source_workflow_bundle(bundle_path, attestation_key=b"j" * 32)


def test_export_accepts_official_pdf_between_16_and_32_mb(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    first_url = request["reports"][0]["url"]
    bodies[first_url] += b"\0" * 16_050_000

    output = export_jpmorgan_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"j" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )

    assert output.exists()


def test_export_rejects_official_pdf_above_32_mb(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    too_large = _text_pdf(_report_text(2022)) + b"\0" * 32_000_000

    with pytest.raises(ProvenanceError, match="bounded PDF"):
        export_jpmorgan_annual_reports(
            request_path,
            tmp_path / "inventory",
            http_get=lambda url, *_: HttpResponse(
                body=too_large,
                final_url=url,
                status=200,
                headers={"Content-Type": "application/pdf"},
            ),
            sleep=lambda _: None,
            attestation_key=b"j" * 32,
            generated_at="2026-09-02T20:00:00Z",
        )
