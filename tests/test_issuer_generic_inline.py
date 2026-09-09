"""Small offline source contracts; fixtures never enter real source inventory."""

import json

import pytest

from longworld.core import finance_taskbank as bank
from longworld.core import issuer_generic_inline as generic
from longworld.core.attestation import attach_attestation
from longworld.core.issuerinlineworkflow import normalize_inline_source
from longworld.core.provenance import ProvenanceError

KEY = b"generic-inline-unit-test-source-key-32-bytes"


def source(year):
    namespaces = {
        "xbrli": "http://www.xbrl.org/2003/instance",
        "iso4217": "http://www.xbrl.org/2003/iso4217",
        "ix": "http://www.xbrl.org/2013/inlineXBRL",
        "ixt": "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
        "us-gaap": f"http://fasb.org/us-gaap/{year}",
        "dei": f"http://xbrl.sec.gov/dei/{year}",
    }
    text = (
        "<html "
        + " ".join(f'xmlns:{k}="{v}"' for k, v in namespaces.items())
        + "><body>"
    )
    for ref, period in [
        (
            "annual",
            f"<xbrli:startDate>{year}-01-01</xbrli:startDate><xbrli:endDate>{year}-12-31</xbrli:endDate>",
        ),
        ("instant", f"<xbrli:instant>{year}-12-31</xbrli:instant>"),
    ]:
        text += f'<xbrli:context id="{ref}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000050863</xbrli:identifier></xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>'
    text += (
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    )
    for name, value in {
        "EntityCentralIndexKey": "0000050863",
        "EntityRegistrantName": "INTEL CORPORATION",
        "DocumentType": "10-K",
        "DocumentFiscalPeriodFocus": "FY",
        "DocumentPeriodEndDate": f"December 31, {year}",
    }.items():
        text += f'<ix:nonNumeric id="{name}" name="dei:{name}" contextRef="annual">{value}</ix:nonNumeric>'
    values = dict(
        zip(generic.INLINE_ROLES, [100 + year - 2023, -20, 500, 500, 30, -10, 5, 25])
    )
    for title, roles in generic.GROUPS.items():
        text += f"<h2>{title}</h2><table><tr><th>Year ended December 31, {year} (In Millions)</th></tr>"
        for role in roles:
            value = values[role]
            ref = "instant" if bank.METRICS[role][1] == "instant" else "annual"
            sign = ' sign="-"' if value < 0 else ""
            fact = f'<ix:nonFraction id="{role}" name="{generic.INLINE_ROLES[role]}" contextRef="{ref}" unitRef="usd" scale="6" decimals="-6"{sign}>{abs(value)}</ix:nonFraction>'
            text += (
                f"<tr><td>{role}</td><td>"
                + ("(" + fact + ")" if value < 0 else fact)
                + "</td></tr>"
            )
        text += "</table>"
    return text + "</body></html>"


def test_visible_signed_annual_operands():
    parsed = generic.parse_generic_inline(
        source(2024), issuer=generic.REGISTRY["intel"], report_date="2024-12-31"
    )
    assert {f["role"]: f["value"] for f in parsed["facts"]}["operating_income"] == -20
    assert len(parsed["facts"]) == 8


@pytest.mark.parametrize(
    "old,new",
    [
        ('scale="6"', 'scale="3"'),
        ("iso4217:USD</", "iso4217:EUR</"),
        ('sign="-"', 'sign=""'),
        ("2024-01-01", "2024-10-01"),
        ("0000050863", "0000002488"),
        ("<xbrli:entity>", "<xbrli:entity><xbrli:segment/>"),
        (
            'DocumentType" contextRef="annual">10-K',
            'DocumentType" contextRef="annual">10-Q',
        ),
    ],
)
def test_native_contract_rejects_invalid_source(old, new):
    with pytest.raises(ProvenanceError):
        generic.parse_generic_inline(
            source(2024).replace(old, new),
            issuer=generic.REGISTRY["intel"],
            report_date="2024-12-31",
        )


def manifest():
    issuer = generic.REGISTRY["intel"]
    listing = "https://www.intc.com" + issuer["listing_path"] + "?form_type=10-K"
    filings = [
        {
            "form": "10-K",
            "report_date": f"{y}-12-31",
            "filing_date": f"{y + 1}-01-31",
            "source_url": "https://www.intc.com"
            + issuer["listing_path"]
            + f"/content/0000050863-{str(y + 1)[2:]}-000009/intc-{y}1231.htm",
        }
        for y in (2023, 2024)
    ]
    request = {
        "schema_version": generic.REQUEST_SCHEMA,
        "issuer_key": "intel",
        "issuer": issuer,
        "listing_url": listing,
        "authorization": {
            "record_id": "fixture",
            "scope": "test",
            "basis": "test",
            "reviewed_at": "2026-09-09T00:00:00Z",
        },
        "filings": filings,
    }

    def bound(raw, url):
        normalized, receipt = normalize_inline_source(raw.encode())
        return {
            "raw_text": raw,
            "text": normalized.decode(),
            "normalization": receipt,
            "retrieval_bytes": len(raw.encode()),
            "bytes": len(normalized),
            "source_url": url,
        }

    listing_text = (
        "<table>"
        + "".join(
            f'<tr><td>01/31/{str(int(f["filing_date"][:4]))[2:]}</td><td>10-K</td><td><a href="{f["source_url"]}">10-K</a></td></tr>'
            for f in filings
        )
        + "</table>"
    )
    records = [
        {
            **f,
            **bound(source(int(f["report_date"][:4])), f["source_url"]),
            "derived": generic.parse_generic_inline(
                source(int(f["report_date"][:4])),
                issuer=issuer,
                report_date=f["report_date"],
            ),
        }
        for f in filings
    ]
    return {
        "schema_version": generic.SCHEMA,
        "source_family": generic.SOURCE_FAMILY,
        "production_eligible": False,
        "request": request,
        "listing": bound(listing_text, listing),
        "records": records,
    }


def test_signed_world_compiles_and_rejects_unsigned_or_derived_tamper(tmp_path):
    payload = manifest()
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(attach_attestation(payload, KEY, purpose="source_manifest"))
    )
    world = generic.load_generic_inline_world(path, attestation_key=KEY)
    result = bank.compile_finance_taskbank(world)
    assert result["tasks"] and any(t["family"] == "ratio" for t in result["tasks"])
    for t in result["tasks"]:
        assert bank.execute_finance_task(world, t) == t["answer"]
    world["facts"][0]["value"] += 1
    with pytest.raises(bank.FinanceTaskBankError):
        bank.compile_finance_taskbank(world)
    signed = json.loads(path.read_text())
    signed["records"][0]["text"] += "x"
    path.write_text(json.dumps(signed))
    with pytest.raises(ProvenanceError, match="attestation"):
        generic.load_generic_inline_world(path, attestation_key=KEY)
    payload["records"][0]["derived"]["facts"][0]["value"] += 1
    path.write_text(
        json.dumps(attach_attestation(payload, KEY, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError, match="derived"):
        generic.load_generic_inline_world(path, attestation_key=KEY)


def test_visible_heading_variant_and_hidden_statement():
    text = source(2024).replace(
        "Consolidated Statements of Operations", "Consolidated Statements of Income"
    )
    assert (
        len(
            generic.parse_generic_inline(
                text, issuer=generic.REGISTRY["intel"], report_date="2024-12-31"
            )["facts"]
        )
        == 8
    )
    with pytest.raises(ProvenanceError, match="hidden"):
        generic.parse_generic_inline(
            text.replace("<table>", '<table style="display:none">', 1),
            issuer=generic.REGISTRY["intel"],
            report_date="2024-12-31",
        )


@pytest.mark.parametrize(
    "change", ["raw", "listing_date", "official_host", "byte_count"]
)
def test_resigned_manifest_still_requires_native_evidence(tmp_path, change):
    payload = manifest()
    if change == "raw":
        payload["records"][0]["raw_text"] += "x"
    elif change == "listing_date":
        payload["request"]["filings"][0]["filing_date"] = "2024-02-01"
    elif change == "official_host":
        payload["request"]["filings"][0]["source_url"] = "https://example.com/fake.htm"
    else:
        payload["records"][0]["bytes"] += 1
    path = tmp_path / "signed.json"
    path.write_text(
        json.dumps(attach_attestation(payload, KEY, purpose="source_manifest"))
    )
    with pytest.raises(ProvenanceError):
        generic.load_generic_inline_world(path, attestation_key=KEY)


@pytest.mark.parametrize(
    "empty", ['<td style="display:none"/>', '<td style="display:none"></td>']
)
def test_empty_hidden_layout_cells_are_not_numeric_evidence(empty):
    text = source(2024).replace("<tr><td>", "<tr>" + empty + "<td>")
    parsed = generic.parse_generic_inline(
        text, issuer=generic.REGISTRY["intel"], report_date="2024-12-31"
    )
    assert len(parsed["facts"]) == 8
    with pytest.raises(ProvenanceError, match="hidden"):
        generic.parse_generic_inline(
            text.replace(
                "<td><ix:nonFraction", '<td style="display:none"><ix:nonFraction'
            ),
            issuer=generic.REGISTRY["intel"],
            report_date="2024-12-31",
        )
