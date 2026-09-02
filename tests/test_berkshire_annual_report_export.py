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
from export_berkshire_annual_reports import (
    HttpResponse,
    export_berkshire_annual_reports,
    load_verified_berkshire_annual_report_inventory,
)


@pytest.fixture(autouse=True)
def _source_producer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], "b" * 32)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-berkshire-source-v1")


def _text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    )
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode("ascii"))
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(body)


def _request() -> dict:
    return {
        "schema_version": "longworld.berkshire-annual-report-request.v1",
        "user_agent": "LongWorld Research research@example-research.org",
        "authorization": {
            "record_id": "BERKSHIRE-OFFICIAL-ANNUALS-TEST-001",
            "scope": "Four issuer-owned annual reports",
            "basis": "public issuer annual-report research export",
            "reviewed_at": "2026-09-02T17:00:00Z",
        },
        "reports": [
            {
                "year": year,
                "url": (
                    f"https://www.berkshirehathaway.com/{year}ar/{year}ar.pdf"
                ),
            }
            for year in range(2021, 2025)
        ],
        "requests_per_second": 2,
        "timeout_seconds": 90,
    }


def _report_text(year: int) -> str:
    pilot = (
        "a 38.6% interest in Pilot Travel Centers LLC"
        if year == 2021
        else "Pilot Travel Centers\nDedicated operating-business disclosure"
    )
    risk = "Cyber security risks" if year < 2023 else "Cybersecurity risks."
    item_1c = (
        "\nItem 1C. Cybersecurity\n"
        "The Audit Committee of Berkshire's Board of Directors has responsibility "
        "for oversight of Berkshire's cybersecurity risk management program."
        if year >= 2023
        else "\nItem 1B. Unresolved Staff Comments"
    )
    return (
        f"Item 1. Business Description\nAnnual report {year}\n{pilot}\n"
        f"Item 1A. Risk Factors\n{risk}\nRisk narrative.{item_1c}\n"
        "Item 2. Description of Properties"
    )


def test_export_writes_verified_official_pdf_inventory(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }

    output = export_berkshire_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"b" * 32,
        generated_at="2026-09-02T18:00:00Z",
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["issuer"] == {
        "name": "Berkshire Hathaway Inc.",
        "cik": "0001067983",
        "official_host": "www.berkshirehathaway.com",
    }
    assert [record["year"] for record in manifest["records"]] == [
        2021,
        2022,
        2023,
        2024,
    ]
    assert all(record["page_count"] == 1 for record in manifest["records"])
    assert all(record["extracted_chars"] > 40 for record in manifest["records"])
    assert [section["section_id"] for section in manifest["records"][3]["sections"]] == [
        "item1_business",
        "item1a_risk_factors",
        "item1c_cybersecurity",
    ]
    assert {fact["field"] for fact in manifest["records"][3]["derived_facts"]} == {
        "pilot_dedicated_operating_section",
        "cyber_dedicated_item1c",
        "cyber_audit_committee_oversight",
    }
    assert manifest["parser"] == {"name": "pypdf", "version": "6.0.0"}
    assert verify_attestation(manifest, b"b" * 32, purpose="source_manifest")
    assert (tmp_path / "inventory" / "berkshire-2024-annual.pdf").read_bytes().startswith(
        b"%PDF-"
    )
    assert "Item 1A. Risk" in (
        tmp_path / "inventory" / "berkshire-2024-annual.txt"
    ).read_text(encoding="utf-8")


def test_inventory_verifier_rejects_changed_extracted_text(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    output = export_berkshire_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"b" * 32,
        generated_at="2026-09-02T18:00:00Z",
    )
    changed = tmp_path / "inventory" / "berkshire-2024-annual.txt"
    changed.write_text("changed source text", encoding="utf-8")

    import pytest

    with pytest.raises(ProvenanceError, match="text receipt"):
        load_verified_berkshire_annual_report_inventory(
            output, attestation_key=b"b" * 32
        )


def test_export_rejects_a_different_official_host_path(tmp_path: Path) -> None:
    request = _request()
    request["reports"][3]["url"] = (
        "https://www.berkshirehathaway.com/2024ar/202410-k.pdf"
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    import pytest

    with pytest.raises(ProvenanceError, match="URL is invalid"):
        export_berkshire_annual_reports(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: None,
            attestation_key=b"b" * 32,
            generated_at="2026-09-02T18:00:00Z",
        )

    assert not (tmp_path / "inventory").exists()


def test_signed_bundle_loads_berkshire_records_and_prior_year_relations(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    manifest_path = export_berkshire_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"b" * 32,
        generated_at="2026-09-02T18:00:00Z",
    )
    bundle_path = manifest_path.parent / "source_workflow_bundle.signed.json"

    loaded = build_source_workflow_bundle(
        [(ISSUER_OFFICIAL_PDF_SOURCE_KIND, manifest_path)],
        bundle_path,
        attestation_key=b"b" * 32,
    )

    assert loaded.bindings[0].kind == ISSUER_OFFICIAL_PDF_SOURCE_KIND
    assert len(loaded.workflows) == 1
    workflow = loaded.workflows[0]
    assert [record.attribute("year") for record in workflow.records] == [
        "2021",
        "2022",
        "2023",
        "2024",
    ]
    assert [relation.kind for relation in workflow.relations] == [
        "prior_official_annual_report",
    ] * 3


def test_bundle_replay_rejects_tampered_berkshire_extracted_text(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    manifest_path = export_berkshire_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        sleep=lambda _: None,
        attestation_key=b"b" * 32,
        generated_at="2026-09-02T18:00:00Z",
    )
    bundle_path = manifest_path.parent / "source_workflow_bundle.signed.json"
    build_source_workflow_bundle(
        [(ISSUER_OFFICIAL_PDF_SOURCE_KIND, manifest_path)],
        bundle_path,
        attestation_key=b"b" * 32,
    )
    (manifest_path.parent / "berkshire-2024-annual.txt").write_text(
        "tampered", encoding="utf-8"
    )

    with pytest.raises(ProvenanceError, match="byte receipt"):
        load_source_workflow_bundle(bundle_path, attestation_key=b"b" * 32)
