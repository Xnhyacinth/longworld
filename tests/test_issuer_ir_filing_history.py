from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_issuer_ir_filing_history import (
    _RejectRedirects,
    _sanitize_detail_page,
    fetch_issuer_ir_filing_history,
)

CIK = "0001018724"
ISSUER_NAME = "Amazon.com, Inc."
DETAIL_HOST = "ir.aboutamazon.com"
DETAIL_PATH = "/sec-filings/sec-filings-details/default.aspx"
ARTIFACT_HOST = "d18rn0p25nwr6d.cloudfront.net"
NVIDIA_CIK = "0001045810"
NVIDIA_ISSUER_NAME = "NVIDIA CORP"
NVIDIA_DETAIL_HOST = "investor.nvidia.com"
NVIDIA_DETAIL_PATH = "/financial-info/sec-filings/sec-filings-details/default.aspx"


def _detail_page(*, form: str, filing_date: str, prefix: str, cik: str = CIK) -> bytes:
    return f"""
    <html><body>
      <input value="eyJpublicstate12345.volatilepayload12345.signaturepart12345">
      <span id="_ctrl0_ctl54_lblForm">{form}</span>
      <span id="_ctrl0_ctl54_lblDate">{filing_date}</span>
      <a id="_ctrl0_ctl54_hrefItemPdfDownload"
         href="https://{ARTIFACT_HOST}/CIK-{cik}/{prefix}.pdf">PDF</a>
      <a id="_ctrl0_ctl54_hrefItemXBRLDownload"
         href="https://{ARTIFACT_HOST}/CIK-{cik}/{prefix}.zip">ZIP</a>
      <a id="_ctrl0_ctl54_hrefItemXBRLHTMLDownload"
         href="https://{ARTIFACT_HOST}/CIK-{cik}/{prefix}.html">HTML</a>
    </body></html>
    """.encode()


def _xbrl_zip(
    tmp_path: Path,
    *,
    report_date: str,
    cik: str = CIK,
    issuer_name: str = ISSUER_NAME,
) -> bytes:
    import zipfile

    path = tmp_path / "facts.zip"
    instance = f"""<?xml version="1.0" encoding="utf-8"?>
    <xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:dei="http://xbrl.sec.gov/dei/2024">
      <dei:EntityRegistrantName>{issuer_name}</dei:EntityRegistrantName>
      <dei:EntityCentralIndexKey>{cik}</dei:EntityCentralIndexKey>
      <dei:DocumentType>10-K</dei:DocumentType>
      <dei:DocumentPeriodEndDate>{report_date}</dei:DocumentPeriodEndDate>
    </xbrl>
    """.encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"issuer-{report_date.replace('-', '')}_htm.xml", instance)
    return path.read_bytes()


def _request() -> dict:
    return {
        "schema_version": "longworld.issuer-ir-fetch-request.v1",
        "user_agent": "Longworld Research research@example-research.org",
        "authorization": {
            "record_id": "ISSUER-IR-TEST-001",
            "scope": "Two annual issuer-owned filing artifacts",
            "basis": "public issuer investor-relations research export",
            "reviewed_at": "2026-08-28T09:00:00Z",
        },
        "issuer": {
            "name": ISSUER_NAME,
            "cik": CIK,
            "detail_host": DETAIL_HOST,
            "artifact_host": ARTIFACT_HOST,
        },
        "filings": [
            {
                "filing_id": "2024-annual",
                "detail_url": f"https://{DETAIL_HOST}{DETAIL_PATH}?FilingId=2024",
                "form": "10-K",
                "filing_date": "2024-02-02",
                "report_date": "2023-12-31",
            },
            {
                "filing_id": "2023-annual",
                "detail_url": f"https://{DETAIL_HOST}{DETAIL_PATH}?FilingId=2023",
                "form": "10-K",
                "filing_date": "2023-02-03",
                "report_date": "2022-12-31",
            },
        ],
        "requests_per_second": 2,
        "max_retries": 1,
    }


def _nvidia_request() -> dict:
    request = _request()
    request["issuer"] = {
        "name": NVIDIA_ISSUER_NAME,
        "cik": NVIDIA_CIK,
        "detail_host": NVIDIA_DETAIL_HOST,
        "artifact_host": ARTIFACT_HOST,
    }
    request["filings"] = [
        {
            "filing_id": "nvidia-fy2025-annual",
            "detail_url": (
                f"https://{NVIDIA_DETAIL_HOST}{NVIDIA_DETAIL_PATH}?FilingId=18226262"
            ),
            "form": "10-K",
            "filing_date": "2025-02-26",
            "report_date": "2025-01-26",
        },
        {
            "filing_id": "nvidia-fy2024-annual",
            "detail_url": (
                f"https://{NVIDIA_DETAIL_HOST}{NVIDIA_DETAIL_PATH}?FilingId=17293267"
            ),
            "form": "10-K",
            "filing_date": "2024-02-21",
            "report_date": "2024-01-28",
        },
    ]
    return request


def test_fetch_accepts_pinned_nvidia_source_policy(tmp_path: Path) -> None:
    request = _nvidia_request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    bodies: dict[str, tuple[bytes, dict[str, str]]] = {}
    for filing_id, displayed_date, report_date in (
        ("18226262", "Feb 26, 2025", "2025-01-26"),
        ("17293267", "Feb 21, 2024", "2024-01-28"),
    ):
        prefix = f"nvidia-{filing_id}"
        detail_url = (
            f"https://{NVIDIA_DETAIL_HOST}{NVIDIA_DETAIL_PATH}?FilingId={filing_id}"
        )
        bodies[detail_url] = (
            _detail_page(
                form="10-K",
                filing_date=displayed_date,
                prefix=prefix,
                cik=NVIDIA_CIK,
            ),
            {"Content-Type": "text/html"},
        )
        artifact_prefix = f"https://{ARTIFACT_HOST}/CIK-{NVIDIA_CIK}/{prefix}"
        bodies[f"{artifact_prefix}.pdf"] = (
            b"%PDF-1.7\nannual report\n%%EOF\n",
            {"Content-Type": "application/pdf"},
        )
        bodies[f"{artifact_prefix}.zip"] = (
            _xbrl_zip(
                tmp_path,
                report_date=report_date,
                cik=NVIDIA_CIK,
                issuer_name=NVIDIA_ISSUER_NAME,
            ),
            {"Content-Type": "application/zip"},
        )
        bodies[f"{artifact_prefix}.html"] = (
            b"<!doctype html><html><body>XBRL rendering</body></html>",
            {"Content-Type": "text/html"},
        )

    output = fetch_issuer_ir_filing_history(
        request_path,
        tmp_path / "inventory",
        http_get=lambda url, *_: bodies[url],
        sleep=lambda _: None,
        generated_at="2026-08-29T04:00:00Z",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["issuer"] == request["issuer"]
    assert payload["n"] == 2
    assert payload["filing_relations"][0]["from_filing_id"] == ("nvidia-fy2025-annual")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        (
            "detail_url",
            "https://investor.nvidia.com/internal/admin?FilingId=1",
            "fixed path boundary",
        ),
        ("artifact_host", "cdn.example-research.org", "fixed source allowlist"),
    ),
)
def test_fetch_rejects_nvidia_source_policy_boundary_changes(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    request = _nvidia_request()
    if field == "detail_url":
        for filing in request["filings"]:
            filing[field] = value
    else:
        request["issuer"][field] = value
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match=match):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: pytest.fail(
                "untrusted NVIDIA boundary reached network"
            ),
            sleep=lambda _: None,
            generated_at="2026-08-29T04:00:00Z",
        )


def test_fetch_rejects_nvidia_artifact_from_wrong_cik_boundary(
    tmp_path: Path,
) -> None:
    request = _nvidia_request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    page = _detail_page(
        form="10-K",
        filing_date="Feb 26, 2025",
        prefix="nvidia-2025",
        cik=CIK,
    )

    with pytest.raises(ProvenanceError, match="artifact URL"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: (page, {"Content-Type": "text/html"}),
            sleep=lambda _: None,
            generated_at="2026-08-29T04:00:00Z",
        )


def test_fetch_binds_issuer_pages_artifacts_and_xbrl_identity(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    bodies: dict[str, tuple[bytes, dict[str, str]]] = {}
    for year, displayed_date, report_date in (
        ("2024", "Feb 02, 2024", "2023-12-31"),
        ("2023", "Feb 03, 2023", "2022-12-31"),
    ):
        prefix = f"issuer-{year}"
        bodies[f"https://{DETAIL_HOST}{DETAIL_PATH}?FilingId={year}"] = (
            _detail_page(form="10-K", filing_date=displayed_date, prefix=prefix),
            {"Content-Type": "text/html"},
        )
        bodies[f"https://{ARTIFACT_HOST}/CIK-{CIK}/{prefix}.pdf"] = (
            b"%PDF-1.7\nannual report\n%%EOF\n",
            {"Content-Type": "application/pdf"},
        )
        bodies[f"https://{ARTIFACT_HOST}/CIK-{CIK}/{prefix}.zip"] = (
            _xbrl_zip(tmp_path, report_date=report_date),
            {"Content-Type": "application/zip"},
        )
        bodies[f"https://{ARTIFACT_HOST}/CIK-{CIK}/{prefix}.html"] = (
            b"<!doctype html><html><body>XBRL rendering</body></html>",
            {"Content-Type": "text/html"},
        )

    calls: list[str] = []

    def get(url: str, headers: dict[str, str], timeout: float):
        del timeout
        assert headers["User-Agent"] == _request()["user_agent"]
        calls.append(url)
        return bodies[url]

    output = fetch_issuer_ir_filing_history(
        request_path,
        tmp_path / "inventory",
        http_get=get,
        sleep=lambda _: None,
        generated_at="2026-08-28T10:00:00Z",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["source_status"] == "issuer_owned_ir_download"
    assert payload["generation_integration"] == "disabled"
    assert payload["production_eligible"] is False
    assert payload["n"] == 2
    assert len(calls) == 8
    assert payload["filing_relations"] == [
        {
            "relation_id": "issuer-ir:2024-annual:prior:2023-annual",
            "relation_type": "prior_available_annual_filing",
            "from_filing_id": "2024-annual",
            "to_filing_id": "2023-annual",
            "relation_provenance": "derived_temporal_same_issuer",
            "derivation_rule": (
                "adjacent_report_dates_after_same_issuer_xbrl_identity_validation"
            ),
            "evidence": [
                {
                    "filing_id": "2024-annual",
                    "fields": ["registrant_name", "cik", "form", "report_date"],
                },
                {
                    "filing_id": "2023-annual",
                    "fields": ["registrant_name", "cik", "form", "report_date"],
                },
            ],
        }
    ]
    assert {filing["report_date"] for filing in payload["filings"]} == {
        "2022-12-31",
        "2023-12-31",
    }
    for filing in payload["filings"]:
        assert filing["xbrl_identity"] == {
            "registrant_name": ISSUER_NAME,
            "cik": CIK,
            "form": "10-K",
            "report_date": filing["report_date"],
        }
        detail = tmp_path / "inventory" / filing["detail_page"]["source_file"]
        assert (
            filing["detail_page"]["sha256"]
            == hashlib.sha256(detail.read_bytes()).hexdigest()
        )
        assert (
            filing["detail_page"]["retrieval_sha256"] != filing["detail_page"]["sha256"]
        )
        assert filing["detail_page"]["volatile_auth_state_redactions"] == 1
        assert b"eyJpublicstate" not in detail.read_bytes()
        for artifact in filing["artifacts"]:
            source = tmp_path / "inventory" / artifact["source_file"]
            assert artifact["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_fetch_rejects_artifact_not_bound_by_issuer_page(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    page = _detail_page(
        form="10-K", filing_date="Feb 02, 2024", prefix="issuer-2024"
    ).replace(f"CIK-{CIK}".encode(), b"CIK-0000000002")

    with pytest.raises(ProvenanceError, match="artifact URL"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: (page, {"Content-Type": "text/html"}),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_fetch_rejects_challenge_page(tmp_path: Path) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="challenge"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: (
                b"<html>Attention Required! Cloudflare Ray ID</html>",
                {"Content-Type": "text/html"},
            ),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_fetch_rejects_artifact_url_with_userinfo_or_nonstandard_port(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    page = _detail_page(
        form="10-K", filing_date="Feb 02, 2024", prefix="issuer-2024"
    ).replace(
        f"https://{ARTIFACT_HOST}/CIK-{CIK}/issuer-2024.pdf".encode(),
        f"https://user@{ARTIFACT_HOST}:444/CIK-{CIK}/issuer-2024.pdf".encode(),
    )

    with pytest.raises(ProvenanceError, match="artifact URL"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: (page, {"Content-Type": "text/html"}),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_fetch_rejects_artifact_path_traversal_outside_cik_boundary(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    page = _detail_page(
        form="10-K", filing_date="Feb 02, 2024", prefix="issuer-2024"
    ).replace(
        f"/CIK-{CIK}/issuer-2024.pdf".encode(),
        f"/CIK-{CIK}/../CIK-0000000002/issuer-2024.pdf".encode(),
    )

    with pytest.raises(ProvenanceError, match="artifact URL"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: (page, {"Content-Type": "text/html"}),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_detail_sanitizer_removes_adjacent_jwt_shaped_state() -> None:
    token = b"eyJpublicstate12345.volatilepayload12345.signaturepart12345"
    sanitized, redactions = _sanitize_detail_page(b"prefixabc" + token + b"suffix")

    assert redactions == 1
    assert token not in sanitized


def test_fetch_rejects_request_declared_hosts_outside_fixed_issuer_allowlist(
    tmp_path: Path,
) -> None:
    request = _request()
    request["issuer"]["detail_host"] = "169.254.169.254"
    request["issuer"]["artifact_host"] = "127.0.0.1"
    for index, filing in enumerate(request["filings"], start=1):
        filing["detail_url"] = f"https://169.254.169.254{DETAIL_PATH}?FilingId={index}"
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="fixed source allowlist"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: pytest.fail("untrusted host reached the network"),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_fetch_rejects_unknown_cik_even_on_pinned_hosts(tmp_path: Path) -> None:
    request = _request()
    request["issuer"]["cik"] = "0000000001"
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="fixed source allowlist"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: pytest.fail("unknown issuer reached the network"),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_fetch_rejects_detail_url_outside_fixed_path_boundary(
    tmp_path: Path,
) -> None:
    request = _request()
    for index, filing in enumerate(request["filings"], start=1):
        filing["detail_url"] = f"https://{DETAIL_HOST}/internal/admin?FilingId={index}"
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="fixed path boundary"):
        fetch_issuer_ir_filing_history(
            request_path,
            tmp_path / "inventory",
            http_get=lambda *_: pytest.fail("untrusted path reached the network"),
            sleep=lambda _: None,
            generated_at="2026-08-28T10:00:00Z",
        )


def test_default_transport_rejects_redirects() -> None:
    assert (
        _RejectRedirects().redirect_request(
            object(),
            object(),
            302,
            "Found",
            {},
            "https://169.254.169.254/latest/meta-data/",
        )
        is None
    )
