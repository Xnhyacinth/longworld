from __future__ import annotations

import json
import sys
from itertools import pairwise
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
from export_walmart_annual_reports import (
    HttpResponse,
    export_walmart_annual_reports,
    load_verified_walmart_annual_report_inventory,
)


@pytest.fixture(autouse=True)
def _source_producer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENVIRONMENT_ENV, "probe")
    monkeypatch.setenv(ROLE_KEY_ENVS["source"], "w" * 32)
    monkeypatch.setenv(ROLE_KEY_ID_ENVS["source"], "probe-walmart-source-v1")


def _request() -> dict:
    urls = {
        2022: (
            "https://stock.walmart.com/_assets/"
            "_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9649/"
            "annual_report/WMT-FY2022-Annual-Report.pdf"
        ),
        2023: (
            "https://stock.walmart.com/_assets/"
            "_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9650/"
            "annual_report/Walmart+2023+Annual+Report.pdf"
        ),
        2024: (
            "https://stock.walmart.com/_assets/"
            "_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9651/"
            "annual_report/Walmart+2024+Annual+Report.pdf"
        ),
        2025: (
            "https://stock.walmart.com/_assets/"
            "_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9949/"
            "annual_report/Walmart+2025+Annual+Report.pdf"
        ),
    }
    return {
        "schema_version": "longworld.walmart-annual-report-request.v1",
        "user_agent": "LongWorld Research research@example-research.org",
        "authorization": {
            "record_id": "WALMART-OFFICIAL-ANNUALS-TEST-001",
            "scope": "Four issuer-owned annual reports",
            "basis": "public issuer annual-report research export",
            "reviewed_at": "2026-09-02T20:00:00Z",
        },
        "reports": [{"year": year, "url": urls[year]} for year in urls],
        "requests_per_second": 2,
        "timeout_seconds": 90,
    }


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
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
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


def _report_text(year: int) -> str:
    segments = (
        "Walmart U.S., Walmart International and Sam's Club U.S."
        if year == 2025
        else "Walmart U.S., Walmart International and Sam's Club"
    )
    totals = {2022: "13,106", 2023: "16,857", 2024: "20,606", 2025: "23,783"}
    largest = {2022: "7,197", 2023: "9,209", 2024: "11,828", 2025: "14,603"}
    allocation = (
        "Supply chain, omni-channel, technology and other"
        if year == 2022
        else "Supply chain, customer-facing initiatives, technology and other"
    )
    risk_lead = "W ith" if year == 2023 else "With"
    return (
        "ITEM 1. BUSINESS\n"
        f"Our operations comprise three reportable segments: {segments}.\n"
        "ITEM 1A. RISK FACTORS\n"
        f"{risk_lead} the interconnected components of this enterprise strategy and an "
        "increasing allocation of capital expenditures focused on these initiatives, "
        "our failure to successfully execute on individual components of this strategy "
        "may adversely affect our market position, net sales and financial performance.\n"
        "Capital Allocation\nAllocation of Capital Expenditures\n"
        f"{allocation} $ {largest[year]}\n"
        f"Total capital expenditures $ {totals[year]}\nReturns\n"
        "Opinion on Internal Control Over Financial Reporting\n"
        "In our opinion, Walmart Inc. (the Company) maintained, in all material "
        "respects, effective internal control over financial reporting as of "
        f"January 31, {year}.\nBasis for Opinion\n"
        "NOTE 26. SEGMENTS\n"
        f"The Company's operations are conducted in three reportable segments: {segments}.\n"
        "Segment results follow."
    )


def test_export_writes_replayable_walmart_source_receipts(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }

    output = export_walmart_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        attestation_key=b"w" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["issuer"] == {
        "name": "Walmart Inc.",
        "cik": "0000104169",
        "official_host": "stock.walmart.com",
    }
    assert [record["year"] for record in manifest["records"]] == [
        2022,
        2023,
        2024,
        2025,
    ]
    assert all(record["page_count"] == 1 for record in manifest["records"])
    assert [section["section_id"] for section in manifest["records"][3]["sections"]] == [
        "business_segments",
        "strategy_execution_risk",
        "capital_expenditures",
        "icfr_opinion",
        "segment_note",
    ]
    assert {fact["field"] for fact in manifest["records"][3]["derived_facts"]} == {
        "reportable_segments",
        "strategy_execution_risk",
        "total_capital_expenditures",
        "largest_capex_allocation",
        "icfr_effective",
        "segment_note_identity",
    }
    for record in manifest["records"]:
        text = (output.parent / record["text_file"]).read_text(encoding="utf-8")
        sections = record["sections"]
        assert all(
            left["char_end"] <= right["char_start"]
            for left, right in pairwise(sections)
        )
        capex = next(
            section
            for section in sections
            if section["section_id"] == "capital_expenditures"
        )
        assert text[capex["char_start"] :].startswith(
            "Allocation of Capital Expenditures"
        )
    assert verify_attestation(manifest, b"w" * 32, purpose="source_manifest")


def test_export_rejects_a_different_walmart_official_path(tmp_path: Path) -> None:
    request = _request()
    request["reports"][3]["url"] = (
        "https://stock.walmart.com/financial-information/annual-reports"
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="URL is invalid"):
        export_walmart_annual_reports(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: None,
            attestation_key=b"w" * 32,
            generated_at="2026-09-02T20:00:00Z",
        )

    assert not (tmp_path / "inventory").exists()


def test_export_explicitly_rejects_the_unextractable_2021_report(
    tmp_path: Path,
) -> None:
    request = _request()
    request["reports"] = [
        {
            "year": 2021,
            "url": (
                "https://stock.walmart.com/_assets/"
                "_5626a6ac33b6bc771a4a9e521fea7c96/walmart/db/950/9648/"
                "annual_report/WMT_2021_AnnualReport.pdf"
            ),
        },
        *request["reports"][:3],
    ]
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="FY2021 is unsupported"):
        export_walmart_annual_reports(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: None,
            attestation_key=b"w" * 32,
            generated_at="2026-09-02T20:00:00Z",
        )


def test_inventory_verifier_rejects_changed_walmart_text(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    output = export_walmart_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        attestation_key=b"w" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )
    changed = output.parent / "walmart-2025-annual.txt"
    changed.write_text("changed source text", encoding="utf-8")

    with pytest.raises(ProvenanceError, match="text receipt"):
        load_verified_walmart_annual_report_inventory(
            output, attestation_key=b"w" * 32
        )


def test_signed_bundle_loads_four_walmart_reports_and_relations(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    manifest_path = export_walmart_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        attestation_key=b"w" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )
    bundle_path = manifest_path.parent / "source_workflow_bundle.signed.json"

    loaded = build_source_workflow_bundle(
        [(ISSUER_OFFICIAL_PDF_SOURCE_KIND, manifest_path)],
        bundle_path,
        attestation_key=b"w" * 32,
    )

    assert loaded.bindings[0].kind == ISSUER_OFFICIAL_PDF_SOURCE_KIND
    workflow = loaded.workflows[0]
    assert [record.attribute("year") for record in workflow.records] == [
        "2022",
        "2023",
        "2024",
        "2025",
    ]
    assert [relation.kind for relation in workflow.relations] == [
        "prior_official_annual_report",
    ] * 3


def test_bundle_replay_rejects_tampered_walmart_text(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies = {
        report["url"]: _text_pdf(_report_text(report["year"]))
        for report in request["reports"]
    }
    manifest_path = export_walmart_annual_reports(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: HttpResponse(
            body=bodies[url],
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
        ),
        attestation_key=b"w" * 32,
        generated_at="2026-09-02T20:00:00Z",
    )
    bundle_path = manifest_path.parent / "source_workflow_bundle.signed.json"
    build_source_workflow_bundle(
        [(ISSUER_OFFICIAL_PDF_SOURCE_KIND, manifest_path)],
        bundle_path,
        attestation_key=b"w" * 32,
    )
    (manifest_path.parent / "walmart-2025-annual.txt").write_text(
        "tampered", encoding="utf-8"
    )

    with pytest.raises(ProvenanceError, match="byte receipt"):
        load_source_workflow_bundle(bundle_path, attestation_key=b"w" * 32)
